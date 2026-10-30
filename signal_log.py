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
import logging
import os
from datetime import datetime, timezone

log = logging.getLogger(__name__)

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
    "atr",
    "tp_dollars",
    "sl_dollars",
]


def _ensure_schema() -> None:
    """Migrate signals.csv if written by an older version (fewer columns).

    Old rows get empty cells for the new fields; outcome_tracker falls back to
    config defaults for TP/SL when those cells are blank.
    """
    if not os.path.exists(SIGNALS_FILE):
        return
    with open(SIGNALS_FILE, newline="") as f:
        rows = list(csv.reader(f))
    if not rows or rows[0] == FIELDS:
        return
    migrated = [FIELDS]
    for r in rows[1:]:
        if not r:
            continue
        r = list(r) + [""] * (len(FIELDS) - len(r))
        migrated.append(r[: len(FIELDS)])
    tmp = SIGNALS_FILE + ".tmp"
    with open(tmp, "w", newline="") as f:
        csv.writer(f).writerows(migrated)
    os.replace(tmp, SIGNALS_FILE)
    log.info("signals.csv migrated to %d-column schema", len(FIELDS))

def record_signal(
    direction: str,
    price_mid: float,
    rsi: float,
    adx: float,
    ema: float,
    body_ratio: float,
    rsi_threshold: float,
    candle_time: datetime | str | int | float | None = None,
    atr: float | None = None,
    tp_dollars: float | None = None,
    sl_dollars: float | None = None,
) -> str:
    """
    Append a signal to signals.csv and return its signal_id.

    signal_id is the UTC time in compact ISO format (YYYYMMDDTHHMMSSZ).
    Uses the candle close time when provided for exact bar alignment.
    """
    if candle_time is None:
        now = datetime.now(timezone.utc)
    elif isinstance(candle_time, (int, float)):
        now = datetime.fromtimestamp(candle_time, tz=timezone.utc)
    elif isinstance(candle_time, datetime):
        now = candle_time if candle_time.tzinfo else candle_time.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)
    elif isinstance(candle_time, str):
        try:
            parsed = datetime.fromisoformat(candle_time.replace("Z", "+00:00"))
            now = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            now = now.astimezone(timezone.utc)
        except Exception:
            try:
                parsed = datetime.strptime(candle_time, "%Y-%m-%d %H:%M:%S")
                now = parsed.replace(tzinfo=timezone.utc)
            except Exception:
                now = datetime.now(timezone.utc)
    else:
        now = datetime.now(timezone.utc)

    signal_id = now.strftime("%Y%m%dT%H%M%SZ")

    # Soft-RSI confidence. Positive value = how far past the threshold.
    # BUY  (rsi < threshold) -> distance = threshold - rsi
    # SELL (rsi > threshold) -> distance = rsi - threshold
    if direction == "BUY":
        rsi_distance = rsi_threshold - float(rsi)
    else:
        rsi_distance = float(rsi) - rsi_threshold

    _ensure_schema()

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
            "atr": round(float(atr), 3) if atr is not None else "",
            "tp_dollars": round(float(tp_dollars), 2) if tp_dollars is not None else "",
            "sl_dollars": round(float(sl_dollars), 2) if sl_dollars is not None else "",
        })

    return signal_id
