"""Tiny stdlib HTTP helper (no third-party deps)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

USER_AGENT = "tokenbar/0.1 (linux)"


class HTTPError(Exception):
    def __init__(self, status: int, message: str, retry_after: int | None = None):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.retry_after = retry_after  # seconds, from the Retry-After header


def get_json(url: str, headers: dict | None = None, timeout: float = 15):
    h = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        body = e.read()[:300].decode("utf-8", "replace")
        ra = e.headers.get("Retry-After") if e.headers else None
        raise HTTPError(e.code, body, int(ra) if ra and ra.isdigit() else None) from None
