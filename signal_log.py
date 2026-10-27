"""
Append-only signal log for the XAU/USD bot.

main.py calls record_signal() each time the strategy fires. The outcome
tracker reads signals.csv and resolves each signal into outcomes.csv.

Layout of signals.csv (one row per fired signal):

    signal_id         unique ID — UTC time of the signal in ISO compact form
    signal_time_utc   full ISO timestamp with UTC offset
    signal_time_local local time (for your own reference)
    direction         "BUY" or "SELL"
    price_mid         close of the trigger bar (mid price)
    rsi               setup-bar RSI
    adx               setup-bar ADX
    ema               setup-bar EMA
    body_ratio        trigger body / setup body
    rsi_threshold     the RSI level required by the strategy (20 or 80)
    rsi_distance      soft-RSI confidence = how far past the threshold
                      (positive number; higher = more extreme)
"""
from __future__ import annotations

import csv
import os
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
SIGNALS_FILE = os.path.join(_HERE, "signals.csv")

FIELDS = [
    "signal_id",
    "signal_time_utc",
    "signal_time_local",
    "direction",
    "price_mid",
    "rsi",
    "adx",
    "ema",
    "body_ratio",
    "rsi_threshold",
    "rsi_distance",
]

def record_signal(
    direction: str,
    price_mid: float,
    rsi: float,
    adx: float,
    ema: float,
    body_ratio: float,
    rsi_threshold: float,
) -> str:
    """
    Append a signal to signals.csv and return its signal_id.

    signal_id is the UTC time in compact ISO format (YYYYMMDDTHHMMSSZ).
    Because the strategy only fires once per trigger bar, this is unique.
    """
    now = datetime.now(timezone.utc)
    signal_id = now.strftime("%Y%m%dT%H%M%SZ")

    # Soft-RSI confidence. Positive value = how far past the threshold.
    # BUY  (rsi < threshold) -> distance = threshold - rsi
    # SELL (rsi > threshold) -> distance = rsi - threshold
    if direction == "BUY":
        rsi_distance = rsi_threshold - float(rsi)
    else:
        rsi_distance = float(rsi) - rsi_threshold

    exists = os.path.exists(SIGNALS_FILE)
    with open(SIGNALS_FILE, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if not exists:
            w.writeheader()
        w.writerow({
            "signal_id": signal_id,
            "signal_time_utc": now.isoformat(),
            "signal_time_local": now.astimezone().isoformat(),
            "direction": direction,
            "price_mid": round(float(price_mid), 5),
            "rsi": round(float(rsi), 3),
            "adx": round(float(adx), 3),
            "ema": round(float(ema), 3),
            "body_ratio": round(float(body_ratio), 3),
            "rsi_threshold": float(rsi_threshold),
            "rsi_distance": round(rsi_distance, 3),
        })

    return signal_id
