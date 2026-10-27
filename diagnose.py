#!/usr/bin/env python3
"""
Strategy funnel diagnostic — two-bar (setup / trigger) model.

For XAU/USD 1-min bars:

  Setup bar (N-1):  side of EMA100, RSI(3), ADX(5)
  Trigger bar (N):  side of EMA100, candle colour, body ratio vs setup

Every bar N >= 1 is treated as a potential *trigger*, with bar N-1 as its
setup. The diagnostic reports:

  1. Indicator distributions (RSI, ADX, distance from EMA)
  2. Per-condition counts, aligned to the trigger index
  3. A progressive BUY funnel and SELL funnel
  4. Near-miss counts (how many of the 6 conditions a pair satisfies)

Usage:
    python diagnose.py --bars 5000
"""
from __future__ import annotations

import argparse
import logging
import sys

import numpy as np
import pandas as pd
import requests

import config
from indicators import compute_indicators

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("diagnose")

# ─────────────────────────────────────────────────────────────
# Data
# ─────────────────────────────────────────────────────────────
def fetch(bars: int) -> pd.DataFrame:
    log.info("Fetching %d bars of %s %s…", bars, config.SYMBOL, config.INTERVAL)
    resp = requests.get(
        "https://api.twelvedata.com/time_series",
        params={
            "symbol": config.SYMBOL,
            "interval": config.INTERVAL,
            "outputsize": bars,
            "apikey": config.TWELVE_DATA_API_KEY,
            "order": "ASC",
        },
        timeout=60,
    )
    data = resp.json()
    if data.get("status") == "error":
        log.error("Twelve Data error: %s", data.get("message"))
        sys.exit(1)

    values = data.get("values", [])
    if not values:
        log.error("No data returned")
        sys.exit(1)

    df = pd.DataFrame(values)
    df["datetime"] = pd.to_datetime(df["datetime"])
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)

# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────
def pct(n: int, total: int) -> str:
    if total == 0:
        return f"{n:>6d}  (  n/a )"
    return f"{n:>6d}  ({100.0 * n / total:5.2f}%)"

def step(label: str, count: int, total: int, indent: int = 2) -> None:
    pad = " " * indent
    print(f"{pad}{label:<34s} {pct(count, total)}")

# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bars", type=int, default=5000)
    args = parser.parse_args()

    raw = fetch(args.bars)
    raw = compute_indicators(raw)

    # Drop warm-up rows where indicators are not yet valid.
    # We keep the raw row order so that shifting still pairs correctly.
    valid = (
        raw["ema"].notna()
        & raw["rsi"].notna()
        & raw["adx"].notna()
    )
    df = raw.loc[valid].reset_index(drop=True)

    total_raw = len(df)
    # We lose one bar at the very start because the first bar has no setup.
    total = total_raw - 1

    print()
    print("=" * 70)
    print(f"  Bars analysed (after warm-up): {total_raw}")
    print(f"  Setup/trigger pairs evaluated: {total}")
    print(f"  Period: {df['datetime'].iloc[0]}  ->  {df['datetime'].iloc[-1]}")
    print("=" * 70)

    # ── Indicator distributions ─────────────────────────────
    print()
    print("Indicator distributions")
    print("-" * 70)
    for name, series in [("RSI3", df["rsi"]), ("ADX5", df["adx"])]:
        s = series.dropna()
        print(
            f"  {name:6s}  min {s.min():6.2f}   p10 {s.quantile(0.10):6.2f}   "
            f"median {s.median():6.2f}   p90 {s.quantile(0.90):6.2f}   "
            f"max {s.max():6.2f}"
        )

    dist = (df["close"] - df["ema"]).dropna()
    print(
        f"  close-EMA100   min {dist.min():8.2f}   median {dist.median():8.2f}   "
        f"max {dist.max():8.2f}"
    )

    # ── Build aligned setup/trigger condition vectors ───────
    #
    # Every condition is expressed as a boolean Series indexed by the
    # *trigger* bar. Setup conditions are shifted forward by one bar
    # so that row i = bar i (trigger) vs bar i-1 (setup).

    # Setup-side conditions (bar N-1 -> aligned to trigger index)
    setup_above_ema = (df["close"] > df["ema"]).shift(1).fillna(False)
    setup_below_ema = (df["close"] < df["ema"]).shift(1).fillna(False)
    setup_rsi_low = (df["rsi"] < config.RSI_OVERSOLD).shift(1).fillna(False)
    setup_rsi_high = (df["rsi"] > config.RSI_OVERBOUGHT).shift(1).fillna(False)
    setup_adx_strong = (df["adx"] > config.ADX_THRESHOLD).shift(1).fillna(False)

    # Trigger-side conditions (bar N)
    trig_above_ema = (df["close"] > df["ema"]).fillna(False)
    trig_below_ema = (df["close"] < df["ema"]).fillna(False)
    trig_green = (df["close"] > df["open"]).fillna(False)
    trig_red = (df["close"] < df["open"]).fillna(False)

    # Body ratio: trigger body / setup body
    body = (df["close"] - df["open"]).abs()
    setup_body = body.shift(1)
    ratio = body / setup_body.replace(0.0, np.nan)
    normal_body = (
        (ratio >= config.BODY_RATIO_MIN) & (ratio <= config.BODY_RATIO_MAX)
    ).fillna(False)

    # Drop the very first row — it has no setup bar.
    def align(series: pd.Series) -> pd.Series:
        return series.iloc[1:].reset_index(drop=True)

    setup_above_ema = align(setup_above_ema)
    setup_below_ema = align(setup_below_ema)
    setup_rsi_low = align(setup_rsi_low)
    setup_rsi_high = align(setup_rsi_high)
    setup_adx_strong = align(setup_adx_strong)

    trig_above_ema = align(trig_above_ema)
    trig_below_ema = align(trig_below_ema)
    trig_green = align(trig_green)
    trig_red = align(trig_red)
    normal_body = align(normal_body)
    ratio_aligned = align(ratio)

    # ── Individual condition counts ─────────────────────────
    print()
    print("Individual conditions (counted once per setup/trigger pair)")
    print("-" * 70)
    print("  — Setup bar (N-1) —")
    step("close > EMA100", int(setup_above_ema.sum()), total, indent=4)
    step("close < EMA100", int(setup_below_ema.sum()), total, indent=4)
    step(f"RSI3  <  {config.RSI_OVERSOLD:<4.0f}", int(setup_rsi_low.sum()), total, indent=4)
    step(f"RSI3  >  {config.RSI_OVERBOUGHT:<4.0f}", int(setup_rsi_high.sum()), total, indent=4)
    step(f"ADX5  >  {config.ADX_THRESHOLD:<4.0f}", int(setup_adx_strong.sum()), total, indent=4)

    print("  — Trigger bar (N) —")
    step("close > EMA100", int(trig_above_ema.sum()), total, indent=4)
    step("close < EMA100", int(trig_below_ema.sum()), total, indent=4)
    step("bar is green", int(trig_green.sum()), total, indent=4)
    step("bar is red", int(trig_red.sum()), total, indent=4)
    step(
        f"body ratio {config.BODY_RATIO_MIN}-{config.BODY_RATIO_MAX}",
        int(normal_body.sum()),
        total,
        indent=4,
    )

    # ── BUY funnel ──────────────────────────────────────────
    print()
    print("BUY funnel — setup(N-1) then trigger(N), applied in order")
    print("-" * 70)
    running = pd.Series(True, index=setup_above_ema.index)
    buy_steps = [
        ("setup: close > EMA100", setup_above_ema),
        ("setup: + RSI3 < 20", setup_rsi_low),
        ("setup: + ADX5 > 30", setup_adx_strong),
        ("trigger: + close > EMA100", trig_above_ema),
        ("trigger: + green candle", trig_green),
        ("trigger: + normal body", normal_body),
    ]
    for label, cond in buy_steps:
        running = running & cond
        step(label, int(running.sum()), total)

    buy_final = int(running.sum())

    # ── SELL funnel ─────────────────────────────────────────
    print()
    print("SELL funnel — setup(N-1) then trigger(N), applied in order")
    print("-" * 70)
    running = pd.Series(True, index=setup_below_ema.index)
    sell_steps = [
        ("setup: close < EMA100", setup_below_ema),
        ("setup: + RSI3 > 80", setup_rsi_high),
        ("setup: + ADX5 > 30", setup_adx_strong),
        ("trigger: + close < EMA100", trig_below_ema),
        ("trigger: + red candle", trig_red),
        ("trigger: + normal body", normal_body),
    ]
    for label, cond in sell_steps:
        running = running & cond
        step(label, int(running.sum()), total)

    sell_final = int(running.sum())

    # ── Near misses ─────────────────────────────────────────
    print()
    print("Near misses — pairs passing N of the 6 BUY / SELL conditions")
    print("-" * 70)

    buy_conditions = [
        setup_above_ema, setup_rsi_low, setup_adx_strong,
        trig_above_ema, trig_green, normal_body,
    ]
    sell_conditions = [
        setup_below_ema, setup_rsi_high, setup_adx_strong,
        trig_below_ema, trig_red, normal_body,
    ]

    buy_hits = sum(c.astype(int) for c in buy_conditions)
    sell_hits = sum(c.astype(int) for c in sell_conditions)

    for n in (6, 5, 4, 3):
        b = int((buy_hits == n).sum())
        s = int((sell_hits == n).sum())
        print(f"  {n} of 6    BUY: {pct(b, total)}    SELL: {pct(s, total)}")

    # ── Summary ─────────────────────────────────────────────
    print()
    print("=" * 70)
    print(f"  Final BUY signals (pair-level):   {buy_final}")
    print(f"  Final SELL signals (pair-level):  {sell_final}")
    print(f"  Combined:                         {buy_final + sell_final}")
    print(f"  Rate: {100.0 * (buy_final + sell_final) / total:5.3f}% of pairs"
          f"  (~{(buy_final + sell_final) * 1440 / total:.1f} signals/day)")
    print("=" * 70)
    print()

    # Body ratio sanity check
    valid_ratios = ratio_aligned.dropna()
    if len(valid_ratios) > 0:
        print("Body ratio distribution (trigger / setup)")
        print("-" * 70)
        for q in (0.10, 0.25, 0.50, 0.75, 0.90):
            print(f"  p{int(q*100):02d}: {valid_ratios.quantile(q):6.2f}")
        print()

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
