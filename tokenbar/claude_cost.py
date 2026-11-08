"""Estimate Claude Code spend from local session logs (~/.claude/projects/**/*.jsonl).

Costs are API-equivalent estimates (what the tokens would cost at list price),
not what a subscription actually bills.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import CACHE_DIR
from .models import CostSummary

# $ per million tokens: (input, output, cache_read). Cache writes are derived:
# 5-minute write = 1.25x input, 1-hour write = 2x input.
# Longest prefix wins. Source: Anthropic pricing, cached 2026-10.
PRICES = {
    "claude-fable-5": (10.00, 50.00, 0.25),
    "claude-mythos-5": (10.00, 50.00, 0.25),
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-opus-5": (5.00, 25.00, 0.50),
    "claude-opus-4-8": (5.00, 25.00, 0.50),
    "claude-opus-4-7": (5.00, 25.00, 0.50),
    "claude-opus-4-6": (5.00, 25.00, 0.50),
    "claude-opus-4-5": (5.00, 25.00, 0.50),
    "claude-opus-4": (15.00, 75.00, 1.50),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-sonnet-5": (2.00, 10.00, 0.20),
    "claude-sonnet-4": (3.00, 15.00, 0.30),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-haiku-4-5": (1.00, 5.00, 0.10),
    "claude-3-5-haiku": (0.80, 4.00, 0.08),
}

CACHE_FILE = CACHE_DIR / "claude-cost-v2.json"


def _price(model: str):
    best = None
    for prefix, p in PRICES.items():
        if model.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, p)
    return best[1] if best else None


def pretty_model(model: str) -> str:
    """claude-opus-5-5 -> Opus 5.5, claude-haiku-4-5-20251001 -> Haiku 4.5."""
    parts = model.removeprefix("claude-").split("-")
    if len(parts) >= 2 and parts[0].isalpha():
        nums = [p for p in parts[1:] if p.isdigit() and len(p) <= 2]
        return " ".join([parts[0].capitalize(), ".".join(nums)]).strip()
    return model


def _entry_cost(model: str, u: dict) -> tuple[float, int]:
    inp = u.get("input_tokens") or 0
    out = u.get("output_tokens") or 0
    cread = u.get("cache_read_input_tokens") or 0
    cwrite = u.get("cache_creation_input_tokens") or 0
    cc = u.get("cache_creation") or {}
    w1h = cc.get("ephemeral_1h_input_tokens") or 0
    w5m = cc.get("ephemeral_5m_input_tokens") if cc else cwrite
    w5m = w5m or 0
    tokens = inp + out + cread + cwrite
    p = _price(model)
    if not p:
        return 0.0, tokens
    pin, pout, pread = p
    cost = (inp * pin + out * pout + cread * pread + w5m * pin * 1.25 + w1h * pin * 2.0) / 1e6
    return cost, tokens


def _scan_file(path: Path) -> dict:
    """Return {dedupe_key: [local_day, model, cost, tokens, ts, in, out, cache_read, cache_write]}."""
    out = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"usage"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") != "assistant":
                    continue
                msg = d.get("message") or {}
                usage = msg.get("usage")
                model = msg.get("model") or ""
                if not usage or model.startswith("<"):
                    continue
                key = f"{msg.get('id')}:{d.get('requestId')}"
                ts = d.get("timestamp")
                try:
                    local = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
                except (AttributeError, ValueError):
                    continue
                cost, tokens = _entry_cost(model, usage)
                out[key] = [local.date().isoformat(), model, cost, tokens, local.timestamp(),
                            usage.get("input_tokens") or 0, usage.get("output_tokens") or 0,
                            usage.get("cache_read_input_tokens") or 0,
                            usage.get("cache_creation_input_tokens") or 0]
    except OSError:
        pass
    return out


def _roots() -> list[Path]:
    roots = []
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    for base in ([Path(p) for p in env.split(",")] if env else []) + [
        Path.home() / ".claude", Path.home() / ".config" / "claude"
    ]:
        p = base / "projects"
        if p.is_dir() and p not in roots:
            roots.append(p)
    return roots


def scan(days: int = 30) -> CostSummary | None:
    roots = _roots()
    if not roots:
        return None
    try:
        cache = json.loads(CACHE_FILE.read_text())
    except (OSError, ValueError):
        cache = {}
    cutoff = (date.today() - timedelta(days=days - 1))
    cutoff_ts = datetime.combine(cutoff, datetime.min.time()).timestamp()

    new_cache, entries = {}, {}
    for root in roots:
        for path in root.rglob("*.jsonl"):
            try:
                st = path.stat()
            except OSError:
                continue
            if st.st_mtime < cutoff_ts:
                continue  # untouched since before the window
            sig = [st.st_mtime, st.st_size]
            c = cache.get(str(path))
            if c and c.get("sig") == sig:
                data = c["entries"]
            else:
                data = _scan_file(path)
            new_cache[str(path)] = {"sig": sig, "entries": data}
            entries.update(data)

    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(new_cache))
        tmp.replace(CACHE_FILE)
    except OSError:
        pass

    today = date.today().isoformat()
    cutoff_s = cutoff.isoformat()
    # day -> model -> [cost, tokens, in, out, cache_read, cache_write]
    daily = defaultdict(lambda: defaultdict(lambda: [0.0, 0, 0, 0, 0, 0]))
    totals = defaultdict(lambda: [0.0, 0, 0, 0, 0, 0])
    latest = None
    s = CostSummary()
    for day, model, cost, tokens, ts, *parts in entries.values():
        if day < cutoff_s:
            continue
        for agg in (daily[day][model], totals[model]):
            agg[0] += cost
            agg[1] += tokens
            for i, v in enumerate(parts[:4]):
                agg[2 + i] += v
        s.month_cost += cost
        s.month_tokens += tokens
        if day == today:
            s.today_cost += cost
            s.today_tokens += tokens
        if latest is None or ts > latest[0]:
            latest = (ts, tokens)

    def rows(by_model: dict) -> list[dict]:
        out = [{"model": m, "name": pretty_model(m), "cost": v[0], "tokens": v[1], "input": v[2],
                "output": v[3], "cache_read": v[4], "cache_write": v[5]} for m, v in by_model.items()]
        return sorted(out, key=lambda r: (-r["cost"], -r["tokens"]))

    s.models = rows(totals)
    s.top_model = s.models[0]["model"] if s.models else None
    s.latest_tokens = latest[1] if latest else None
    s.daily = []
    for i in range(days):
        day = (cutoff + timedelta(days=i)).isoformat()
        models = rows(daily.get(day, {}))
        s.daily.append({"date": day, "cost": sum(r["cost"] for r in models),
                        "tokens": sum(r["tokens"] for r in models), "models": models})
    return s
