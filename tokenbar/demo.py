"""Deterministic demo data (TOKENBAR_DEMO=1), used for README screenshots."""
from __future__ import annotations

import random
from datetime import date, timedelta

from .claude_cost import pretty_model
from .models import CostSummary, ProviderSnapshot, StatusInfo, UsageWindow, now

MODELS = {  # model -> ($/M input, $/M output, $/M cache read), as in claude_cost.PRICES
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}


def _cost(days: int = 30) -> CostSummary:
    rng = random.Random(7)
    start = date.today() - timedelta(days=days - 1)
    s = CostSummary()
    totals: dict[str, list] = {}
    for i in range(days):
        d = start + timedelta(days=i)
        weekend = d.weekday() >= 5
        activity = 0.0 if (weekend and rng.random() < 0.6) else rng.uniform(0.3, 1.0) * (1 + i / days)
        if i == days - 1:
            activity = 0.8  # "today" always has usage
        rows = []
        for model, (pin, pout, pread) in MODELS.items():
            share = {"claude-opus-5-5": 0.7, "claude-sonnet-5-5": 0.25, "claude-haiku-5-5": 0.05}[model]
            if activity == 0 or rng.random() < (0.15 if model != "claude-opus-5-5" else 0.02):
                continue
            scale = activity * share * rng.uniform(0.7, 1.3)
            inp = int(9_000 * scale)
            out = int(260_000 * scale)
            cread = int(28_000_000 * scale)
            cwrite = int(900_000 * scale)
            cost = (inp * pin + out * pout + cread * pread + cwrite * pin * 2.0) / 1e6
            tokens = inp + out + cread + cwrite
            rows.append({"model": model, "name": pretty_model(model), "cost": cost, "tokens": tokens,
                         "input": inp, "output": out, "cache_read": cread, "cache_write": cwrite})
            t = totals.setdefault(model, [0.0, 0, 0, 0, 0, 0])
            for j, v in enumerate((cost, tokens, inp, out, cread, cwrite)):
                t[j] += v
        rows.sort(key=lambda r: -r["cost"])
        day_cost = sum(r["cost"] for r in rows)
        day_tokens = sum(r["tokens"] for r in rows)
        s.daily.append({"date": d.isoformat(), "cost": day_cost, "tokens": day_tokens, "models": rows})
        s.month_cost += day_cost
        s.month_tokens += day_tokens
    s.today_cost, s.today_tokens = s.daily[-1]["cost"], s.daily[-1]["tokens"]
    s.models = sorted(
        ({"model": m, "name": pretty_model(m), "cost": v[0], "tokens": v[1], "input": v[2], "output": v[3],
          "cache_read": v[4], "cache_write": v[5]} for m, v in totals.items()),
        key=lambda r: -r["cost"])
    s.top_model = s.models[0]["model"]
    return s


def snapshots() -> list[ProviderSnapshot]:
    t = now()
    claude = ProviderSnapshot(
        id="claude", name="Claude", account="you@example.com", plan="Max 5x",
        dashboard_url="https://claude.ai/settings/usage",
        status=StatusInfo("none", "All Systems Operational", "https://status.claude.com"),
        windows=[
            UsageWindow("session", "Session", 38.0, t + timedelta(hours=2, minutes=53), 5 * 3600),
            UsageWindow("weekly", "Weekly", 22.0, t + timedelta(days=4, hours=20), 7 * 86400),
        ],
        cost=_cost(),
    )
    codex = ProviderSnapshot(
        id="codex", name="Codex", account="you@example.com", plan="Plus",
        dashboard_url="https://chatgpt.com/codex/settings/usage",
        status=StatusInfo("none", "All Systems Operational", "https://status.openai.com"),
        windows=[
            UsageWindow("session", "Session", 12.0, t + timedelta(hours=4, minutes=10), 5 * 3600),
            UsageWindow("weekly", "Weekly", 41.0, t + timedelta(days=2, hours=6), 7 * 86400),
        ],
    )
    return [claude, codex]
