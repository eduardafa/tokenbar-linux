"""Desktop notifications, modelled on CodexBar's notification settings.

Evaluated on every fresh observation (`tokenbar usage --notify`). State lives on disk
(~/.local/state/tokenbar/notify.json) so the applet, the tray app and the CLI share it
and never notify twice for the same event.

Kinds (each can be switched off in config["notifications"]):
  thresholds  session/weekly used crosses notify_at_percent (default 80, 95), once per window
  pace        at the current pace the window runs out before it resets, once per window
  depleted    session hits 100% used, and again when it's available ("restored")
  reset       a session/weekly limit reset
  login       the provider login expired or was rejected, once until it works again
"""
from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .models import ProviderSnapshot, UsageWindow, fmt_duration, now

STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "tokenbar" / "notify.json"
ICON = "io.github.eduardafranca.TokenBar"

DEFAULTS = {"thresholds": True, "pace": True, "depleted": True, "reset": False, "login": True}
PACE_MIN_USED = 10.0     # don't warn about pace on almost-unused windows
DEPLETED_AT = 99.5


def enabled(cfg: dict, kind: str) -> bool:
    return bool({**DEFAULTS, **(cfg.get("notifications") or {})}.get(kind))


def _window_id(w: UsageWindow) -> str:
    """Stable id for one reset period (resets_at jitters by sub-seconds between calls)."""
    if not w.resets_at:
        return "none"
    return str(round(w.resets_at.timestamp() / 600))  # 10-minute buckets


def _load() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def _save(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(STATE)


def send(title: str, body: str, key: str) -> None:
    """org.freedesktop.Notifications over D-Bus (no GTK needed); notify-send as fallback."""
    try:
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call_sync(
            "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
            "org.freedesktop.Notifications", "Notify",
            GLib.Variant("(susssasa{sv}i)", ("TokenBar", 0, ICON, title, body, [],
                                             {"desktop-entry": GLib.Variant("s", "io.github.eduardafranca.TokenBar")},
                                             -1)),
            None, Gio.DBusCallFlags.NONE, 5000, None)
    except Exception:
        try:
            import subprocess
            subprocess.run(["notify-send", "-a", "TokenBar", "-i", ICON, title, body], timeout=5, check=False)
        except Exception:
            pass


def evaluate(snaps: list[ProviderSnapshot], cfg: dict, deliver=send) -> list[tuple[str, str]]:
    """Compare with the previous observation, send what's new, return [(title, body)].

    Serialised with a file lock: with two monitors COSMIC runs two applets, and both can
    evaluate the same observation at once; without the lock both would notify."""
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _evaluate(snaps, cfg, deliver)


def _evaluate(snaps, cfg, deliver):
    state = _load()
    sent: list[tuple[str, str]] = []
    thresholds = sorted(cfg.get("notify_at_percent") or [])

    def fire(kind: str, key: str, title: str, body: str):
        if enabled(cfg, kind):
            deliver(title, body, key)
            sent.append((title, body))

    for s in snaps:
        if not s.available:
            continue
        pstate = state.setdefault(s.id, {})

        # login expired / rejected
        login_bad = bool(s.error and ("log in" in s.error.lower() or "login" in s.error.lower()))
        if login_bad and not pstate.get("login_notified"):
            fire("login", f"{s.id}:login", f"{s.name} needs you to sign in again", s.error)
            pstate["login_notified"] = True
        elif not login_bad and s.windows:
            pstate.pop("login_notified", None)

        if s.updated_at is None:  # no fresh data (rate limited before any fetch, etc.)
            continue
        for w in s.windows:
            wid = _window_id(w)
            prev = pstate.get(w.key) or {}
            same = prev.get("wid") == wid
            cur = {"wid": wid, "used": w.used_percent,
                   "thr": prev.get("thr", []) if same else [],      # thresholds already notified
                   "pace": prev.get("pace", False) if same else False}
            label = w.label.lower()

            # limit reset: the reset period changed and usage dropped
            if prev and prev.get("wid") != wid and w.used_percent < prev.get("used", 0):
                fire("reset", f"{s.id}:{w.key}:reset", f"{s.name} {label} limit reset",
                     f"{w.left_percent:.0f}% available" + (f" · next reset in {fmt_duration((w.resets_at - now()).total_seconds())}"
                                                           if w.resets_at else ""))

            # depleted / restored (session only, like CodexBar)
            if w.key == "session":
                was = prev.get("depleted", False)
                is_ = w.used_percent >= DEPLETED_AT
                if is_ and not was:
                    reset = f" Resets in {fmt_duration((w.resets_at - now()).total_seconds())}." if w.resets_at else ""
                    fire("depleted", f"{s.id}:session:depleted", f"{s.name} session depleted",
                         f"0% left.{reset} You'll be notified when it's available again.")
                elif was and not is_:
                    fire("depleted", f"{s.id}:session:restored", f"{s.name} session restored",
                         "Session quota is available again.")
                cur["depleted"] = is_

            # threshold warnings: highest newly crossed threshold, once per window
            crossed = [t for t in thresholds if w.used_percent >= t and t not in cur["thr"]]
            if crossed and w.used_percent < DEPLETED_AT:
                t = max(crossed)
                reset = f" · resets in {fmt_duration((w.resets_at - now()).total_seconds())}" if w.resets_at else ""
                fire("thresholds", f"{s.id}:{w.key}:t{t}", f"{s.name} {label} at {w.used_percent:.0f}%",
                     f"{w.left_percent:.0f}% left{reset}")
            cur["thr"] = sorted(set(cur["thr"]) | set(crossed))

            # pace warning: will run out before reset, once per window
            p = w.pace()
            if (p and not p.lasts and w.used_percent >= PACE_MIN_USED and w.used_percent < DEPLETED_AT
                    and not cur["pace"]):
                left = fmt_duration((p.runs_out_at - now()).total_seconds())
                fire("pace", f"{s.id}:{w.key}:pace", f"{s.name} {label} pace warning",
                     f"At the current pace, this quota may run out in {left}, before it resets.")
                cur["pace"] = True

            pstate[w.key] = cur

    state["_at"] = datetime.now(timezone.utc).isoformat()
    _save(state)
    return sent
