"""OpenAI Codex (ChatGPT plan), using the login stored by the Codex CLI in ~/.codex/auth.json."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from datetime import datetime, timezone

from .. import apicache, status
from ..http import get_json
from ..models import ProviderSnapshot, StatusInfo, UsageWindow, parse_time, now

ID = "codex"
NAME = "Codex"
USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"


def _auth_path() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "auth.json"


def _jwt_claims(token: str) -> dict:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def _window(key: str, w: dict | None) -> UsageWindow | None:
    if not isinstance(w, dict) or w.get("used_percent") is None:
        return None
    secs = w.get("limit_window_seconds")
    label = "Session" if secs and secs <= 6 * 3600 else ("Weekly" if secs else key.capitalize())
    reset = parse_time(w.get("reset_at"))
    if reset is None and w.get("reset_after_seconds") is not None:
        reset = parse_time(now().timestamp() + w["reset_after_seconds"])
    return UsageWindow(label.lower(), label, float(w["used_percent"]), reset, secs)


def fetch(cfg: dict) -> ProviderSnapshot:
    snap = ProviderSnapshot(id=ID, name=NAME, dashboard_url="https://chatgpt.com/codex/settings/usage")
    path = _auth_path()
    if not path.is_file():
        snap.available = False
        snap.error = "Codex CLI not logged in (~/.codex/auth.json missing)."
        return snap

    auth = json.loads(path.read_text())
    tokens = auth.get("tokens") or {}
    access = tokens.get("access_token")
    if not access:
        snap.error = "Codex is using an API key; plan limits are only available with a ChatGPT login."
        return snap

    claims = _jwt_claims(tokens.get("id_token") or "")
    snap.account = claims.get("email")
    if cfg.get("check_status", True) and cfg.get("_mode") != "offline":
        snap.status = status.statuspage("https://status.openai.com")
    if snap.status is None:  # keep the Status Page link even without a status check
        snap.status = StatusInfo(url="https://status.openai.com")

    headers = {"Authorization": f"Bearer {access}"}
    if tokens.get("account_id"):
        headers["ChatGPT-Account-Id"] = tokens["account_id"]
    with apicache.store() as db:
        res = apicache.get(db, "codex.usage", access, 120, lambda: get_json(USAGE_URL, headers),
                           cfg.get("_mode", "normal"))
    if res.error:
        snap.error = "Login rejected. Run `codex` to log in again." if res.status in (401, 403) else res.error
    d = res.data
    if d is None:
        snap.updated_at = None
        return snap
    snap.updated_at = datetime.fromtimestamp(res.fetched_at, tz=timezone.utc)

    snap.plan = (d.get("plan_type") or "").capitalize() or None
    rl = d.get("rate_limit") or {}
    for key in ("primary_window", "secondary_window"):
        w = _window(key.split("_")[0], rl.get(key))
        if w:
            snap.windows.append(w)
    cr = d.get("credits") or {}
    if cr.get("unlimited"):
        snap.credits = "Credits: unlimited"
    elif cr.get("has_credits") or cr.get("balance"):
        snap.credits = f"Credits: {cr.get('balance')} left"
    return snap
