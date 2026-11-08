"""Data model shared by providers, the CLI and the GUI."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Optional


def now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class UsageWindow:
    key: str                      # "session", "weekly", ...
    label: str                    # human label
    used_percent: float           # 0..100
    resets_at: Optional[datetime] = None
    window_seconds: Optional[int] = None  # total length of the window, for pace

    @property
    def left_percent(self) -> float:
        return max(0.0, 100.0 - self.used_percent)

    def pace(self, at: Optional[datetime] = None) -> Optional["Pace"]:
        """Project when the window will run out at the current burn rate."""
        if not (self.resets_at and self.window_seconds):
            return None
        at = at or now()
        remaining = (self.resets_at - at).total_seconds()
        elapsed = self.window_seconds - remaining
        if elapsed <= 60 or remaining <= 0:
            return None
        expected = 100.0 * elapsed / self.window_seconds  # ideal steady burn
        if self.used_percent <= 0:
            return Pace(expected, None, lasts=True)
        rate = self.used_percent / elapsed            # % per second
        secs_to_empty = (100.0 - self.used_percent) / rate
        if secs_to_empty >= remaining:
            return Pace(expected, None, lasts=True)
        return Pace(expected, at + timedelta(seconds=secs_to_empty), lasts=False)


@dataclass
class Pace:
    expected_percent: float           # where an even burn would be right now
    runs_out_at: Optional[datetime]   # None when it lasts until reset
    lasts: bool


@dataclass
class CostSummary:
    today_cost: float = 0.0
    today_tokens: int = 0
    month_cost: float = 0.0           # last 30 days
    month_tokens: int = 0
    top_model: Optional[str] = None
    # [{date, cost, tokens, models: [{model, name, cost, tokens, input, output, cache_read, cache_write}]}], oldest first
    daily: list = field(default_factory=list)
    models: list = field(default_factory=list)  # same row shape, totals over the window
    latest_tokens: Optional[int] = None


@dataclass
class StatusInfo:
    indicator: str = "none"           # none | minor | major | critical | maintenance
    description: str = ""
    url: str = ""


@dataclass
class ProviderSnapshot:
    id: str
    name: str
    available: bool = True            # False => provider not configured on this machine
    account: Optional[str] = None
    plan: Optional[str] = None
    windows: list[UsageWindow] = field(default_factory=list)
    credits: Optional[str] = None     # free-form credits / extra usage line
    cost: Optional[CostSummary] = None
    status: Optional[StatusInfo] = None
    error: Optional[str] = None
    updated_at: datetime = field(default_factory=now)
    dashboard_url: Optional[str] = None

    def primary(self) -> Optional[UsageWindow]:
        return self.windows[0] if self.windows else None

    def to_dict(self) -> dict:
        def conv(o):
            if isinstance(o, datetime):
                return o.isoformat()
            if isinstance(o, dict):
                return {k: conv(v) for k, v in o.items()}
            if isinstance(o, (list, tuple)):
                return [conv(v) for v in o]
            return o
        return conv(asdict(self))


def parse_time(value) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        if value > 1e12:
            value /= 1000.0
        return datetime.fromtimestamp(value, tz=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def fmt_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def fmt_reset(dt: Optional[datetime]) -> str:
    if not dt:
        return ""
    return "Resets in " + fmt_duration((dt - now()).total_seconds())


def fmt_pace(p: Optional[Pace]) -> str:
    if not p:
        return ""
    if p.lasts:
        return "Lasts until reset"
    return "Runs out in " + fmt_duration((p.runs_out_at - now()).total_seconds())


def fmt_tokens(n: int) -> str:
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if n >= div:
            return f"{n / div:.1f}{unit}"
    return str(n)
