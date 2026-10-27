#!/usr/bin/env python3
"""
Print a summary of outcome_tracker.py results.

Reads outcomes.csv and reports:

  - Counts by outcome (win / loss / timeout / ambiguous / unresolvable)
  - Win rate — both (a) resolved-only and (b) timeout-counted-as-loss
  - Expectancy in USD per trade
  - Forward returns at +5 / +15 / +30 / +60 minutes (in pips)
  - Breakdown by direction
  - Breakdown by soft-RSI proximity
  - Data quality flags (ambiguity rate, timeout rate)

Usage:
    python report.py
    python report.py --csv outcomes.csv
"""
from __future__ import annotations

import argparse
import csv
import os
import statistics
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CSV = os.path.join(_HERE, "outcomes.csv")

def _parse_time(s: str):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None

def _num(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default

def _load(path: str) -> list[dict]:
    if not os.path.exists(path):
        print(f"No file at {path}.")
        print("Run outcome_tracker.py first to generate it.")
        raise SystemExit(1)
    with open(path, newline="") as f:
        return list(csv.DictReader(f))

def _bar(label: str, value, width: int = 38) -> None:
    print(f"  {label.ljust(width)}{value}")

def _pct(n: int, d: int) -> str:
    return "n/a" if d == 0 else f"{100.0 * n / d:.1f}%"

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=DEFAULT_CSV)
    args = ap.parse_args()

    rows = _load(args.csv)
    if not rows:
        print("outcomes.csv is empty.")
        return 0

    wins = [r for r in rows if r.get("outcome") == "win"]
    losses = [r for r in rows if r.get("outcome") == "loss"]
    timeouts = [r for r in rows if r.get("outcome") == "timeout"]
    ambiguous = [r for r in rows if r.get("outcome") == "ambiguous"]
    unresolvable = [r for r in rows if r.get("outcome") == "unresolvable"]

    resolved = wins + losses + timeouts + ambiguous
    clean = wins + losses
    with_pnl = wins + losses + timeouts

    print()
    print("=" * 72)
    print("  XAU/USD Signal Bot — Outcome Report")
    print(f"  Source: {os.path.basename(args.csv)}")
    times = [t for t in (_parse_time(r.get("signal_time_utc")) for r in rows) if t]
    if times:
        print(f"  Period: {min(times).strftime('%Y-%m-%d %H:%M')}  ->  "
              f"{max(times).strftime('%Y-%m-%d %H:%M')} UTC")
    print("=" * 72)
    print()

    _bar("Total signals logged:", len(rows))
    _bar("Resolved (win+loss+timeout+ambig):", len(resolved))
    _bar("  Wins:", f"{len(wins)}   ({_pct(len(wins), len(resolved))})")
    _bar("  Losses:", f"{len(losses)}   ({_pct(len(losses), len(resolved))})")
    _bar("  Timeouts:", f"{len(timeouts)}   ({_pct(len(timeouts), len(resolved))})")
    _bar("  Ambiguous:", f"{len(ambiguous)}   ({_pct(len(ambiguous), len(resolved))})")
    _bar("  Unresolvable:", len(unresolvable))
    print()

    _bar("Win rate (win / win+loss):",
         f"{_pct(len(wins), len(clean))}   (n={len(clean)})")
    _bar("Win rate (timeout=loss):",
         f"{_pct(len(wins), len(wins) + len(losses) + len(timeouts))}")
    print()

    # ── Expectancy ────────────────────────────────────────────────
    pnls = [_num(r.get("pnl_dollars")) for r in with_pnl]
    if pnls:
        exp = sum(pnls) / len(pnls)
        total = sum(pnls)
        _bar("Expectancy (excl. ambiguous):",
             f"{exp:+.3f} USD / trade   (n={len(pnls)})")
        _bar("Cumulative P&L (1 unit/trade):", f"{total:+.2f} USD")
    print()

    # ── Forward returns ───────────────────────────────────────────
    print("Forward returns (avg pips from signal mid):")
    for N in (5, 15, 30, 60):
        vals = []
        for r in rows:
            v = r.get(f"fwd_{N}m_pips", "")
            if v not in ("", None):
                try:
                    vals.append(float(v))
                except ValueError:
                    pass
        if vals:
            print(f"    +{N:>2d} min:   {statistics.mean(vals):+7.2f} pips   "
                  f"(n={len(vals)}, median {statistics.median(vals):+6.2f})")
    print()

    # ── By direction ──────────────────────────────────────────────
    print("Breakdown by direction:")
    for d in ("BUY", "SELL"):
        sub = [r for r in rows if r.get("direction") == d]
        w = [r for r in sub if r.get("outcome") == "win"]
        l = [r for r in sub if r.get("outcome") == "loss"]
        pnl_vals = [_num(r.get("pnl_dollars"))
                    for r in sub if r.get("outcome") in ("win", "loss", "timeout")]
        avg = sum(pnl_vals) / len(pnl_vals) if pnl_vals else 0.0
        print(f"    {d:<5s}  {len(sub):>3d} signals   "
              f"{len(w)}W / {len(l)}L   "
              f"win rate {_pct(len(w), len(w) + len(l))}   "
              f"avg {avg:+.3f} USD")
    print()

    # ── By RSI proximity ──────────────────────────────────────────
    buckets = [(0.0, 2.0, "0-2 pips past threshold"),
               (2.0, 5.0, "2-5 pips past threshold"),
               (5.0, 1e9, "5+ pips past threshold")]
    print("Breakdown by soft-RSI proximity:")
    for lo, hi, label in buckets:
        sub = []
        for r in rows:
            v = r.get("rsi_distance", "")
            if v in ("", None):
                continue
            try:
                d = float(v)
            except ValueError:
                continue
            if lo <= d < hi:
                sub.append(r)
        w = [r for r in sub if r.get("outcome") == "win"]
        l = [r for r in sub if r.get("outcome") == "loss"]
        print(f"    {label:<28s} {len(sub):>3d} signals   "
              f"{len(w)}W / {len(l)}L   "
              f"win rate {_pct(len(w), len(w) + len(l))}")
    print()

    # ── Data quality ──────────────────────────────────────────────
    print("Data quality flags:")
    _bar("Ambiguity rate:", f"{_pct(len(ambiguous), len(resolved))}   (target <10%)")
    _bar("Timeout rate:", f"{_pct(len(timeouts), len(resolved))}")
    _bar("Unresolvable:",
         f"{len(unresolvable)} of {len(rows)}  ({_pct(len(unresolvable), len(rows))})")
    print()

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
