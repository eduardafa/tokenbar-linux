"""GTK4/libadwaita app: tray icon + dashboard window + preferences."""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, APP_NAME, __version__, config  # noqa: E402
from .models import ProviderSnapshot, fmt_duration, now  # noqa: E402
from .providers import fetch_all  # noqa: E402
from .tray import TrayIcon  # noqa: E402
from .widgets import ProviderCard  # noqa: E402

AUTOSTART = Path(GLib.get_user_config_dir()) / "autostart" / "tokenbar.desktop"


def _short_reset(w) -> str:
    if not w.resets_at:
        return ""
    return " · resets in " + fmt_duration((w.resets_at - now()).total_seconds())


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: "TokenBarApp"):
        super().__init__(application=app, title=APP_NAME, default_width=420, default_height=640)
        self.app = app
        self.cards: dict[str, ProviderCard] = {}

        header = Adw.HeaderBar()
        self.refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh now")
        self.refresh_btn.connect("clicked", lambda *_: app.refresh("force"))
        self.spinner = Gtk.Spinner()
        header.pack_start(self.refresh_btn)
        header.pack_start(self.spinner)

        menu = Gio.Menu()
        menu.append("Preferences", "app.preferences")
        menu.append("About TokenBar", "app.about")
        menu.append("Quit", "app.quit")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu))

        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.box.set_margin_top(12)
        self.box.set_margin_bottom(18)
        self.box.set_margin_start(12)
        self.box.set_margin_end(12)
        self.empty = Adw.StatusPage(
            icon_name="dialog-information-symbolic", title="No providers found",
            description="Log in to Claude Code (`claude`) or Codex (`codex`) and refresh.")
        self.empty.set_visible(False)
        self.box.append(self.empty)

        clamp = Adw.Clamp(maximum_size=560, child=self.box)
        scroller = Gtk.ScrolledWindow(child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_vexpand(True)
        view = Adw.ToolbarView(content=scroller)
        view.add_top_bar(header)
        self.set_content(view)
        self.connect("close-request", self._on_close)

    def _on_close(self, *_):
        if self.app.tray is None:
            return False  # no tray: closing the window quits
        self.set_visible(False)  # keep running in the tray
        return True

    def set_loading(self, loading: bool):
        self.spinner.set_spinning(loading)
        self.spinner.set_visible(loading)
        self.refresh_btn.set_sensitive(not loading)

    def update(self, snaps: list[ProviderSnapshot]):
        shown = [s for s in snaps if s.available]
        for s in shown:
            card = self.cards.get(s.id)
            if card is None:
                card = self.cards[s.id] = ProviderCard()
                self.box.append(card)
            card.update(s)
        ids = {s.id for s in shown}
        for pid, card in self.cards.items():
            card.set_visible(pid in ids)
        self.empty.set_visible(not shown)


class TokenBarApp(Adw.Application):
    def __init__(self, start_hidden: bool, use_tray: bool = True, open_prefs: bool = False):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.cfg = config.load()
        self.start_hidden = start_hidden
        self.use_tray = use_tray
        self.open_prefs = open_prefs
        self.snaps: list[ProviderSnapshot] = []
        self.window: MainWindow | None = None
        self.tray: TrayIcon | None = None
        self._timer = 0
        self._loading = False
        self._notified: dict[str, int] = {}

    # ---------- lifecycle ----------
    def do_startup(self):
        Adw.Application.do_startup(self)
        if self.use_tray:
            self.hold()  # stay alive with no window
        for name, cb in (("preferences", self.show_preferences), ("about", self.show_about),
                         ("quit", self.quit_app), ("refresh", lambda *_: self.refresh("force"))):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda *_a, cb=cb: cb())
            self.add_action(act)
        self.set_accels_for_action("app.quit", ["<Ctrl>q"])
        self.set_accels_for_action("app.refresh", ["<Ctrl>r", "F5"])

        if self.use_tray:
            self.tray = TrayIcon(APP_ID, APP_NAME, self.toggle_window, on_menu_open=self.refresh)
            try:
                self.tray.start()
            except Exception as e:  # no session bus / tray host: dashboard still works
                print(f"tray unavailable: {e}", file=sys.stderr)
                self.tray = None
        self._update_tray()
        self._schedule()
        self.refresh("offline")  # cached data only; the API is called when you open the window

    def do_activate(self):
        if self.start_hidden and self.tray:
            self.start_hidden = False
            return
        self.show_window()
        if self.open_prefs:
            self.open_prefs = False
            self.show_preferences()

    def quit_app(self):
        if self.tray:
            self.tray.stop()
            self.release()
        self.quit()

    # ---------- window ----------
    def show_window(self):
        if self.window is None:
            self.window = MainWindow(self)
            self.window.update(self.snaps)
            self.window.set_loading(self._loading)
        self.window.present()
        self.refresh()  # opening the window fetches (the backend's 2-minute cache prevents spamming)

    def toggle_window(self):
        if self.window and self.window.get_visible() and self.window.is_active():
            self.window.set_visible(False)
        else:
            self.show_window()

    # ---------- refresh ----------
    def _schedule(self):
        if self._timer:
            GLib.source_remove(self._timer)
        secs = max(60, int(self.cfg.get("refresh_seconds", 180)))
        # Periodic updates only re-read the cache (local logs + last API response): no API calls.
        self._timer = GLib.timeout_add_seconds(secs, lambda: (self.refresh("offline"), True)[1])

    def refresh(self, mode: str = "normal"):
        """mode: "offline" (cache only), "normal" (2-minute cache), "force" (manual refresh)."""
        if self._loading:
            return
        self._loading = True
        if self.window:
            self.window.set_loading(True)
        cfg = dict(self.cfg)

        def work():
            snaps = fetch_all(cfg, mode)
            GLib.idle_add(self._apply, snaps)

        threading.Thread(target=work, daemon=True).start()

    def _apply(self, snaps):
        self._loading = False
        self.snaps = snaps
        if self.window:
            self.window.set_loading(False)
            self.window.update(snaps)
        self._update_tray()
        self._maybe_notify(snaps)
        return False

    # ---------- tray ----------
    def _update_tray(self):
        if not self.tray:
            return
        shown = [s for s in self.snaps if s.available]
        items, tip = [], []
        for s in shown:
            items.append({"label": s.name + (f" · {s.plan}" if s.plan else ""), "enabled": False})
            for w in s.windows:
                line = f"{w.label}: {w.used_percent:.0f}% used{_short_reset(w)}"
                items.append({"label": "    " + line, "enabled": False})
                tip.append(f"{s.name} {line}")
            if s.credits:
                items.append({"label": "    " + s.credits, "enabled": False})
            if s.cost and s.cost.month_tokens:
                items.append({"label": f"    Today ${s.cost.today_cost:,.2f} · 30 days ${s.cost.month_cost:,.2f}",
                              "enabled": False})
            if s.status and s.status.indicator not in ("none", ""):
                items.append({"label": f"    ⚠ {s.status.description}", "enabled": False})
            if s.error:
                items.append({"label": f"    ! {s.error[:70]}", "enabled": False})
            items.append({"separator": True})
        if not shown:
            items += [{"label": "Loading…" if self._loading or not self.snaps else "No providers found",
                       "enabled": False}, {"separator": True}]
        items += [
            {"label": "Open Dashboard", "callback": self.show_window},
            {"label": "Refresh Now", "callback": lambda: self.refresh("force")},
            {"label": "Preferences…", "callback": self.show_preferences},
            {"separator": True},
            {"label": "Quit", "callback": self.quit_app},
        ]
        self.tray.set_menu(items)
        self.tray.set_tooltip(APP_NAME, "\n".join(tip) or "No data yet")

    # ---------- notifications ----------
    def _maybe_notify(self, snaps):
        thresholds = sorted(self.cfg.get("notify_at_percent") or [])
        for s in snaps:
            for w in s.windows:
                key = f"{s.id}:{w.key}:{w.resets_at.isoformat() if w.resets_at else ''}"
                crossed = max((t for t in thresholds if w.used_percent >= t), default=0)
                if crossed and crossed > self._notified.get(key, 0):
                    self._notified[key] = crossed
                    n = Gio.Notification.new(f"{s.name} {w.label.lower()} at {w.used_percent:.0f}%")
                    n.set_body(f"{w.left_percent:.0f}% left{_short_reset(w)}")
                    self.send_notification(key, n)

    # ---------- dialogs ----------
    def _parent(self):
        if self.window is None or not self.window.get_visible():
            self.show_window()
        return self.window

    def show_about(self):
        Adw.AboutDialog(
            application_name=APP_NAME, version=__version__, application_icon="io.github.eduardafranca.TokenBar",
            developer_name="Eduarda França", license_type=Gtk.License.MIT_X11,
            comments="Usage limits, reset countdowns and spend for AI coding plans, in your Pop!_OS panel.",
        ).present(self._parent())

    def show_preferences(self):
        dlg = Adw.PreferencesDialog(title="Preferences")
        page = Adw.PreferencesPage()
        dlg.add(page)

        general = Adw.PreferencesGroup(title="General")
        page.add(general)
        interval = Adw.SpinRow.new_with_range(1, 60, 1)
        interval.set_title("Refresh every (minutes)")
        interval.set_value(max(60, self.cfg["refresh_seconds"]) // 60)
        interval.connect("notify::value", lambda r, _p: self._set("refresh_seconds", int(r.get_value()) * 60))
        general.add(interval)

        login = Adw.SwitchRow(title="Start at login", active=AUTOSTART.exists())
        login.connect("notify::active", lambda r, _p: self._set_autostart(r.get_active()))
        general.add(login)

        provs = Adw.PreferencesGroup(title="Providers")
        page.add(provs)
        for pid, name, sub in (("claude", "Claude", "Uses the Claude Code login"),
                               ("codex", "Codex", "Uses the Codex CLI login")):
            row = Adw.SwitchRow(title=name, subtitle=sub, active=self.cfg["providers"].get(pid, True))
            row.connect("notify::active", lambda r, _p, pid=pid: self._set_provider(pid, r.get_active()))
            provs.add(row)

        extras = Adw.PreferencesGroup(title="Extras")
        page.add(extras)
        for key, title, sub in (("show_cost", "Cost estimate", "Scan local Claude Code logs for token spend"),
                                ("check_status", "Service status", "Show incidents from provider status pages")):
            row = Adw.SwitchRow(title=title, subtitle=sub, active=self.cfg.get(key, True))
            row.connect("notify::active", lambda r, _p, key=key: self._set(key, r.get_active()))
            extras.add(row)
        notify = Adw.SwitchRow(title="Notify at 80% and 95%", active=bool(self.cfg.get("notify_at_percent")))
        notify.connect("notify::active", lambda r, _p: self._set("notify_at_percent", [80, 95] if r.get_active() else []))
        extras.add(notify)

        dlg.present(self._parent())

    def _set(self, key, value):
        self.cfg[key] = value
        config.save(self.cfg)
        if key == "refresh_seconds":
            self._schedule()
        else:
            self.refresh()

    def _set_provider(self, pid, on):
        self.cfg["providers"][pid] = on
        config.save(self.cfg)
        self.refresh()

    def _set_autostart(self, on: bool):
        if on:
            AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
            AUTOSTART.write_text(
                "[Desktop Entry]\nType=Application\nName=TokenBar\n"
                f"Exec={_launcher()} --hidden\nIcon=io.github.eduardafranca.TokenBar\n"
                "X-GNOME-Autostart-enabled=true\nNoDisplay=true\n")
        elif AUTOSTART.exists():
            AUTOSTART.unlink()


def _launcher() -> str:
    local = Path.home() / ".local" / "bin" / "tokenbar"
    return str(local) if local.exists() else f"{sys.executable} -m tokenbar"


def run() -> int:
    hidden = "--hidden" in sys.argv
    no_tray = "--no-tray" in sys.argv
    prefs = "--preferences" in sys.argv
    return TokenBarApp(start_hidden=hidden and not no_tray and not prefs, use_tray=not no_tray,
                           open_prefs=prefs).run(sys.argv[:1])
