"""Provider registry. Each provider exposes `ID`, `NAME` and `fetch(cfg) -> ProviderSnapshot`."""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

from ..models import ProviderSnapshot
from . import claude, codex

ALL = [claude, codex]


def fetch_all(cfg: dict) -> list[ProviderSnapshot]:
    if os.environ.get("TOKENBAR_DEMO") == "1" or cfg.get("demo"):
        from ..demo import snapshots
        return snapshots()
    enabled = [p for p in ALL if cfg.get("providers", {}).get(p.ID, True)]

    def run(p):
        try:
            return p.fetch(cfg)
        except Exception as e:  # never let one provider kill the refresh
            return ProviderSnapshot(id=p.ID, name=p.NAME, error=f"{type(e).__name__}: {e}")

    with ThreadPoolExecutor(max_workers=max(1, len(enabled))) as ex:
        return list(ex.map(run, enabled))
