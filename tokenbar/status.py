"""Provider status pages (Atlassian Statuspage format)."""
from __future__ import annotations

import time

from .http import get_json
from .models import StatusInfo

_cache: dict[str, tuple[float, StatusInfo]] = {}
TTL = 300


def statuspage(base_url: str) -> StatusInfo | None:
    hit = _cache.get(base_url)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    try:
        d = get_json(base_url.rstrip("/") + "/api/v2/status.json", timeout=8)
        st = d.get("status") or {}
        info = StatusInfo(st.get("indicator", "none"), st.get("description", ""), base_url)
    except Exception:
        return hit[1] if hit else None
    _cache[base_url] = (time.time(), info)
    return info
