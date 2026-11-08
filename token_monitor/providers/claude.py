"""Claude (claude.ai subscription used by Claude Code).

Reads the OAuth token Claude Code stores in ~/.claude/.credentials.json and calls
the same usage endpoint Claude Code's /usage uses. The token is used read-only:
we never refresh it ourselves (refresh tokens rotate, which would log Claude Code
out). When it expires, running `claude` once refreshes it.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .. import claude_cost, status
from ..http import HTTPError, get_json
from ..models import ProviderSnapshot, UsageWindow, parse_time, now

ID = "claude"
NAME = "Claude"
API = "https://api.anthropic.com/api/oauth"
HEADERS = {"anthropic-beta": "oauth-2025-04-20"}

WINDOWS = [
    # (api key, our key, label, seconds)
    ("five_hour", "session", "Session", 5 * 3600),
    ("seven_day", "weekly", "Weekly", 7 * 86400),
    ("seven_day_opus", "weekly_opus", "Weekly · Opus", 7 * 86400),
    ("seven_day_sonnet", "weekly_sonnet", "Weekly · Sonnet", 7 * 86400),
]

_profile_cache: dict = {"at": 0.0, "data": None, "token": None}


def _credentials_path() -> Path | None:
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    for base in ([Path(env.split(",")[0])] if env else []) + [Path.home() / ".claude"]:
        p = base / ".credentials.json"
        if p.is_file():
            return p
    return None


def _plan_name(oauth: dict, profile: dict | None) -> str | None:
    tier = (oauth.get("rateLimitTier") or "").lower()
    sub = (oauth.get("subscriptionType") or "").lower()
    if profile:
        org = profile.get("organization") or {}
        tier = (org.get("rate_limit_tier") or tier).lower()
    if "max" in tier or sub == "max":
        mult = "20x" if "20x" in tier else ("5x" if "5x" in tier else "")
        return f"Max {mult}".strip()
    if sub:
        return sub.capitalize()
    return None


def fetch(cfg: dict) -> ProviderSnapshot:
    snap = ProviderSnapshot(id=ID, name=NAME, dashboard_url="https://claude.ai/settings/usage")
    path = _credentials_path()

    if cfg.get("show_cost", True):
        try:
            snap.cost = claude_cost.scan()
        except Exception:
            snap.cost = None
    if cfg.get("check_status", True):
        snap.status = status.statuspage("https://status.claude.com")

    if not path:
        if snap.cost is None:
            snap.available = False
        snap.error = "Not logged in. Run `claude` and log in with your Claude account."
        return snap

    oauth = json.loads(path.read_text()).get("claudeAiOauth") or {}
    token = oauth.get("accessToken")
    if not token:
        snap.error = "No OAuth token found (API-key logins have no usage limits to show)."
        return snap
    expires = oauth.get("expiresAt")
    if expires and expires / 1000 < time.time():
        snap.error = "Login expired. Run `claude` once to refresh it."
        snap.plan = _plan_name(oauth, None)
        return snap

    headers = dict(HEADERS, Authorization=f"Bearer {token}")
    try:
        usage = get_json(f"{API}/usage", headers)
    except HTTPError as e:
        snap.error = "Login rejected. Run `claude` to log in again." if e.status in (401, 403) else str(e)
        return snap

    for api_key, key, label, secs in WINDOWS:
        w = usage.get(api_key)
        if isinstance(w, dict) and w.get("utilization") is not None:
            snap.windows.append(UsageWindow(key, label, float(w["utilization"]),
                                            parse_time(w.get("resets_at")), secs))

    extra = usage.get("extra_usage") or {}
    if extra.get("is_enabled"):
        div = 10 ** (extra.get("decimal_places") or 2)
        used = (extra.get("used_credits") or 0) / div
        limit = extra.get("monthly_limit")
        cur = extra.get("currency") or "USD"
        snap.credits = f"Extra usage: {used:.2f} / {limit / div:.2f} {cur}" if limit else f"Extra usage: {used:.2f} {cur}"

    # Profile (email, plan) changes rarely: cache for an hour.
    pc = _profile_cache
    if pc["token"] != token or time.time() - pc["at"] > 3600:
        try:
            pc.update(data=get_json(f"{API}/profile", headers), at=time.time(), token=token)
        except Exception:
            pass
    profile = pc["data"]
    if profile:
        snap.account = (profile.get("account") or {}).get("email")
    snap.plan = _plan_name(oauth, profile)
    snap.updated_at = now()
    return snap
