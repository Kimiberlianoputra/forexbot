#!/usr/bin/env python3
"""
Compare a signal's actual outcome vs. its inverted outcome.

Reads signals.csv, fetches the forward 30 minutes for each signal, and
calculates what would have happened if you took the opposite trade.

Usage:
    python invert_test.py
"""
import csv
import os
import sys
from datetime import datetime, timedelta, timezone

import requests
import config

_HERE = os.path.dirname(os.path.abspath(__file__))
SIGNALS_FILE = os.path.join(_HERE, "signals.csv")

# Trade parameters
TP = 7.50
SL = 5.00
HALF_SPREAD = 0.025  # $0.05 spread / 2
TWO_HS = HALF_SPREAD * 2
TIMEOUT_BARS = 30

def fetch_bars(signal_time):
    start = signal_time - timedelta(minutes=2)
    params = {
        "symbol": config.SYMBOL,
        "interval": "1min",
        "start_date": start.strftime("%Y-%m-%d %H:%M:%S"),
        "outputsize": 40,
        "apikey": config.TWELVE_DATA_API_KEY,
        "order": "ASC",
        "timezone": "UTC",
    }
    resp = requests.get("https://api.twelvedata.com/time_series", params=params, timeout=30)
    data = resp.json()
    if data.get("status") == "error":
        return []
    return data.get("values", [])

def simulate(direction, entry_mid, bars):
    if direction == "BUY":
        tp_trigger = entry_mid + TP + TWO_HS
        sl_trigger = entry_mid - SL + TWO_HS
    else:
        tp_trigger = entry_mid - TP - TWO_HS
        sl_trigger = entry_mid + SL - TWO_HS

    for i, b in enumerate(bars[:TIMEOUT_BARS]):
        high, low = float(b["high"]), float(b["low"])
        if direction == "BUY":
            hit_tp = high >= tp_trigger
            hit_sl = low <= sl_trigger
        else:
            hit_tp = low <= tp_trigger
            hit_sl = high >= sl_trigger

        if hit_tp and hit_sl:
            return "ambiguous", 0.0
        elif hit_tp:
            return "win", TP
        elif hit_sl:
            return "loss", -SL

    # Timeout
    close_price = float(bars[TIMEOUT_BARS - 1]["close"])
    if direction == "BUY":
        pnl = (close_price - entry_mid) - TWO_HS
    else:
        pnl = (entry_mid - close_price) - TWO_HS
    return "timeout", pnl

def main():
    if not os.path.exists(SIGNALS_FILE):
        print("No signals.csv found.")
        return

    signals = []
    with open(SIGNALS_FILE, newline="") as f:
        signals = list(csv.DictReader(f))

    print(f"\n{'Time':<20} | {'Orig':<5} | {'Orig P&L':>9} | {'Inv':<5} | {'Inv P&L':>9}")
    print("-" * 65)

    orig_total = 0.0
    inv_total = 0.0
    orig_wins = 0
    inv_wins = 0

    for sig in signals:
        sig_id = sig["signal_id"]
        direction = sig["direction"]
        entry_mid = float(sig["price_mid"])
        
        # Parse time
        t = datetime.fromisoformat(sig["signal_time_utc"].replace("Z", "+00:00"))
        
        raw_bars = fetch_bars(t)
        
        # Filter to only bars after signal
        t0 = t.replace(second=0, microsecond=0)
        bars = []
        for b in raw_bars:
            bt = datetime.strptime(b["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            if bt > t0:
                bars.append(b)

        if len(bars) < TIMEOUT_BARS:
            print(f"{t.strftime('%Y-%m-%d %H:%M'):<20} | SKIPPED (not enough data)")
            continue

        inv_direction = "SELL" if direction == "BUY" else "BUY"

        orig_outcome, orig_pnl = simulate(direction, entry_mid, bars)
        inv_outcome, inv_pnl = simulate(inv_direction, entry_mid, bars)

        orig_total += orig_pnl
        inv_total += inv_pnl
        if orig_outcome == "win": orig_wins += 1
        if inv_outcome == "win": inv_wins += 1

        print(f"{t.strftime('%Y-%m-%d %H:%M'):<20} | {orig_outcome[:4]:<5} | {orig_pnl:>+9.2f} | {inv_outcome[:4]:<5} | {inv_pnl:>+9.2f}")

    n = len(signals)
    print("-" * 65)
    print(f"{'TOTAL':<20} | {orig_wins}W/{n-len(signals)+len(signals)-orig_wins}L | {orig_total:>+9.2f} | {inv_wins}W/{n-inv_wins}L | {inv_total:>+9.2f}")
    print(f"\nOriginal Expectancy: {orig_total/n:+.2f} USD")
    print(f"Inverted Expectancy: {inv_total/n:+.2f} USD")

if __name__ == "__main__":
    main()
