"""Cross-process cache + rate-limit backoff for provider API calls.

Every `tokenbar usage` run is a new process, and the COSMIC panel runs one applet
per monitor, so an in-memory cache isn't enough. Responses are kept in
~/.cache/tokenbar/api-cache.json under a file lock (one process fetches, the
others read). On HTTP 429 we back off (Retry-After, else 5 min doubling up to 1 h)
and keep serving the last good response.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .config import CACHE_DIR
from .http import HTTPError

FILE = CACHE_DIR / "api-cache.json"
LOCK = CACHE_DIR / "api-cache.lock"
MIN_BACKOFF, MAX_BACKOFF = 300, 3600


@dataclass
class Result:
    data: Optional[Any]          # response (possibly stale), None if never fetched
    fetched_at: Optional[float]  # unix time of that response
    error: Optional[str] = None  # set when the latest attempt failed or is backing off
    status: Optional[int] = None


@contextmanager
def store():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOCK, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # serialise fetches across processes
        try:
            data = json.loads(FILE.read_text())
        except (OSError, ValueError):
            data = {}
        yield data
        tmp = FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.chmod(0o600)
        tmp.replace(FILE)


def _who(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:16]  # never store the token itself


def get(db: dict, key: str, token: str, ttl: float, fetch: Callable[[], Any], mode: str = "normal") -> Result:
    """mode: "offline" = cache only, never call the API; "normal" = use the cache if
    younger than ttl; "force" = ignore ttl (a manual refresh). All modes honour backoff."""
    now = time.time()
    who = _who(token)
    entry = db.get(key) if (db.get(key) or {}).get("who") == who else None
    stale = Result(entry["data"], entry["at"]) if entry else Result(None, None)

    backoff = (db.get("_backoff") or {}).get(key)
    backing_off = bool(backoff and now < backoff["until"])
    if backing_off:
        stale.error = f"Rate limited, retrying in {_mins(backoff['until'] - now)}"
        stale.status = 429

    if mode == "offline" or backing_off:
        return stale
    if mode != "force" and entry and now - entry["at"] < ttl:
        return stale

    try:
        data = fetch()
    except HTTPError as e:
        stale.status = e.status
        if e.status == 429:
            wait = e.retry_after or min(MAX_BACKOFF, max(MIN_BACKOFF, 2 * (backoff or {}).get("wait", 0)))
            db.setdefault("_backoff", {})[key] = {"until": now + wait, "wait": wait}
            stale.error = f"Rate limited, retrying in {_mins(wait)}"
        else:
            stale.error = str(e)
        return stale
    except Exception as e:  # network down, timeout, ...
        stale.error = f"{type(e).__name__}: {e}"
        return stale

    db[key] = {"who": who, "at": now, "data": data}
    (db.get("_backoff") or {}).pop(key, None)
    return Result(data, now)


def _mins(secs: float) -> str:
    m = max(1, round(secs / 60))
    return f"{m} min" if m < 60 else f"{m // 60} h {m % 60} min"
