"""Adaptive background refresh, same table as CodexBar's AdaptiveRefreshPolicyCore.

  popup/menu opened < 5 min ago  -> every 2 min
  opened < 1 h ago               -> every 5 min
  opened < 4 h ago               -> every 15 min
  otherwise (or never)           -> every 30 min
  coding activity < 5 min ago    -> at most every 5 min

"Coding activity" = Claude Code / Codex session logs changed recently (local files, no API).
The COSMIC applet implements the same table in Rust (applet/src/main.rs).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

MIN = 60


def delay(now: float, last_open: float | None, last_activity: float | None) -> float:
    since_open = now - last_open if last_open else None
    if since_open is not None and since_open < 5 * MIN:
        d = 2 * MIN
    elif since_open is not None and since_open < 60 * MIN:
        d = 5 * MIN
    elif since_open is not None and since_open < 4 * 60 * MIN:
        d = 15 * MIN
    else:
        d = 30 * MIN
    if last_activity and now - last_activity < 5 * MIN:
        d = min(d, 5 * MIN)
    return d


def last_coding_activity() -> float | None:
    """Newest mtime of Claude Code project logs and Codex session dirs (cheap: two levels)."""
    newest = None
    roots = [Path.home() / ".claude" / "projects",
             Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "sessions"]
    for root in roots:
        try:
            for d in root.iterdir():
                for p in [d, *(d.iterdir() if d.is_dir() else [])]:
                    try:
                        m = p.stat().st_mtime
                    except OSError:
                        continue
                    newest = m if newest is None or m > newest else newest
        except OSError:
            continue
    return newest


def due(last_refresh: float | None, last_open: float | None) -> bool:
    now = time.time()
    if last_refresh is None:
        return True
    return now - last_refresh >= delay(now, last_open, last_coding_activity())
