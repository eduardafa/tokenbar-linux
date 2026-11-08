"""User configuration stored in ~/.config/tokenbar/config.json."""
from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "tokenbar"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "tokenbar"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULTS = {
    "refresh_seconds": 180,
    "providers": {"claude": True, "codex": True},
    "tray_metric": "session",      # session | weekly | highest
    "show_cost": True,
    "check_status": True,
    "notify_at_percent": [80, 95],  # desktop notifications when a window crosses these
}


def load() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        user = json.loads(CONFIG_FILE.read_text())
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    except (OSError, ValueError):
        pass
    return cfg


def save(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2))
    tmp.replace(CONFIG_FILE)
