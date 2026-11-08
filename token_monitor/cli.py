"""Command line: `token-monitor usage [--json]`, `token-monitor` (tray app)."""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__, config
from .models import fmt_pace, fmt_reset, fmt_tokens
from .providers import fetch_all

BAR = 20


def _bar(pct: float, color: bool) -> str:
    filled = round(BAR * min(100, max(0, pct)) / 100)
    s = "█" * filled + "░" * (BAR - filled)
    if not color:
        return s
    c = "32" if pct < 70 else ("33" if pct < 90 else "31")
    return f"\033[{c}m{s}\033[0m"


def print_usage(snaps, color: bool) -> None:
    b = (lambda t: f"\033[1m{t}\033[0m") if color else (lambda t: t)
    dim = (lambda t: f"\033[2m{t}\033[0m") if color else (lambda t: t)
    for s in snaps:
        if not s.available:
            continue
        head = s.name + (f" · {s.plan}" if s.plan else "")
        print(b(head) + (dim(f"  {s.account}") if s.account else ""))
        if s.status and s.status.indicator not in ("none", ""):
            print(f"  ⚠ {s.status.description}")
        for w in s.windows:
            extra = "  ".join(x for x in (fmt_reset(w.resets_at), fmt_pace(w.pace())) if x)
            print(f"  {w.label:<16} {_bar(w.used_percent, color)} {w.used_percent:3.0f}% used  {dim(extra)}")
        if s.credits:
            print(f"  {s.credits}")
        if s.cost:
            c = s.cost
            print(f"  Today            ${c.today_cost:,.2f}  ({fmt_tokens(c.today_tokens)} tokens)")
            print(f"  Last 30 days     ${c.month_cost:,.2f}  ({fmt_tokens(c.month_tokens)} tokens)")
            if c.top_model:
                print(f"  Top model        {c.top_model}")
        if s.error:
            print(f"  ! {s.error}")
        print()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="token-monitor", description="AI coding plan usage monitor")
    ap.add_argument("-V", "--version", action="version", version=__version__)
    ap.add_argument("--hidden", action="store_true", help="start in the tray without opening the window")
    ap.add_argument("--no-tray", action="store_true", help="dashboard window only (used by the COSMIC applet)")
    ap.add_argument("--preferences", action="store_true", help="open the preferences dialog")
    sub = ap.add_subparsers(dest="cmd")
    u = sub.add_parser("usage", help="print usage and exit")
    u.add_argument("--json", action="store_true")
    u.add_argument("--no-color", action="store_true")
    u.add_argument("--provider", choices=["claude", "codex"])
    sub.add_parser("app", help="run the tray app (default)")
    args = ap.parse_args(argv)

    if args.cmd == "usage":
        cfg = config.load()
        if args.provider:
            cfg["providers"] = {k: k == args.provider for k in ("claude", "codex")}
        snaps = fetch_all(cfg)
        if args.json:
            print(json.dumps([s.to_dict() for s in snaps if s.available], indent=2))
        else:
            print_usage(snaps, color=sys.stdout.isatty() and not args.no_color)
        return 0

    from .app import run
    return run()


if __name__ == "__main__":
    sys.exit(main())
