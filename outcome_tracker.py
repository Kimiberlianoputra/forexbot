#!/usr/bin/env python3
"""
Outcome tracker for the XAU/USD signal bot.

Reads signals.csv (written by signal_log.record_signal when the bot fires),
fetches the following 1-min bars from Twelve Data via REST, and resolves each
signal into a trade outcome.
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

import config

# ── Paths ─────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
SIGNALS_FILE = os.path.join(_HERE, "signals.csv")
OUTCOMES_FILE = os.path.join(_HERE, "outcomes.csv")

# ── Trade parameters (locked spec) ────────────────────────────────────
TP_DOLLARS = 7.50
SL_DOLLARS = 5.00
HALF_SPREAD = 0.025                  # $0.05 total spread / 2
TWO_HS = 2 * HALF_SPREAD             # 0.05 round-trip spread cost
TIMEOUT_BARS = 30

# ── Fetch settings ────────────────────────────────────────────────────
BAR_INTERVAL = "1min"
FORWARD_BARS = 60                     # need +60 min of data
RESOLVE_AFTER_MIN = 70                # wait this long before resolving
STALE_AFTER_HOURS = 168                # older than this -> unresolvable

# ── Output columns ────────────────────────────────────────────────────
FIELDS = [
    "signal_id",
    "signal_time_utc",
    "direction",
    "entry_mid",
    "entry_fill",
    "tp_trigger_mid",
    "sl_trigger_mid",
    "outcome",
    "exit_mid",
    "exit_time_utc",
    "bars_held",
    "pnl_dollars",
    "pnl_pips",
    "fwd_5m_pips",
    "fwd_15m_pips",
    "fwd_30m_pips",
    "fwd_60m_pips",
    "rsi_distance",
    "note",
]

# ── Logging ───────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  tracker  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("tracker")

def _truncate_minute(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)

def _parse_signal_utc(s: str) -> datetime:
    s = s.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def _parse_bar_time(s: str) -> datetime:
    dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    return dt.replace(tzinfo=timezone.utc)

def _load_signals() -> list[dict]:
    if not os.path.exists(SIGNALS_FILE):
        return []
    with open(SIGNALS_FILE, newline="") as f:
        return list(csv.DictReader(f))

def _load_resolved_ids() -> set[str]:
    if not os.path.exists(OUTCOMES_FILE):
        return set()
    with open(OUTCOMES_FILE, newline="") as f:
        reader = csv.DictReader(f)
        return {row["signal_id"] for row in reader}

def _append_outcome(row: dict) -> None:
    exists = os.path.exists(OUTCOMES_FILE)
    with open(OUTCOMES_FILE, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if not exists:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in FIELDS})

def _fetch_bars(signal_time: datetime) -> list[dict]:
    start = signal_time - timedelta(minutes=2)
    params = {
        "symbol": config.SYMBOL,
        "interval": BAR_INTERVAL,
        "start_date": start.strftime("%Y-%m-%d %H:%M:%S"),
        "outputsize": FORWARD_BARS + 30,
        "apikey": config.TWELVE_DATA_API_KEY,
        "order": "ASC",
        "timezone": "UTC",
    }
    resp = requests.get(
        "https://api.twelvedata.com/time_series",
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") == "error":
        raise RuntimeError(data.get("message", "unknown error"))
    return data.get("values", [])

def _resolve_one(sig: dict) -> dict:
    sig_id = sig["signal_id"]
    direction = sig["direction"]
    signal_time = _parse_signal_utc(sig["signal_time_utc"])
    entry_mid = float(sig["price_mid"])
    rsi_distance = sig.get("rsi_distance", "")

    if direction == "BUY":
        entry_fill = entry_mid + HALF_SPREAD
        tp_trigger = entry_mid + TP_DOLLARS + TWO_HS
        sl_trigger = entry_mid - SL_DOLLARS + TWO_HS
    else:
        entry_fill = entry_mid - HALF_SPREAD
        tp_trigger = entry_mid - TP_DOLLARS - TWO_HS
        sl_trigger = entry_mid + SL_DOLLARS - TWO_HS

    base = {
        "signal_id": sig_id,
        "signal_time_utc": signal_time.isoformat(),
        "direction": direction,
        "entry_mid": round(entry_mid, 5),
        "entry_fill": round(entry_fill, 5),
        "tp_trigger_mid": round(tp_trigger, 5),
        "sl_trigger_mid": round(sl_trigger, 5),
        "rsi_distance": rsi_distance,
    }

    try:
        raw = _fetch_bars(signal_time)
    except Exception as exc:
        log.warning("Fetch failed for %s: %s", sig_id, exc)
        base["outcome"] = "unresolvable"
        base["note"] = f"fetch failed: {exc}"
        return base

    t0 = _truncate_minute(signal_time)
    bars: list[dict] = []
    for b in raw:
        try:
            bt = _parse_bar_time(b["datetime"])
        except Exception:
            continue
        if bt > t0:
            bars.append({
                "time": bt,
                "open": float(b["open"]),
                "high": float(b["high"]),
                "low": float(b["low"]),
                "close": float(b["close"]),
            })

    if len(bars) < TIMEOUT_BARS:
        base["outcome"] = "unresolvable"
        base["note"] = f"only {len(bars)} bars returned (need {TIMEOUT_BARS})"
        return base

    for N in (5, 15, 30, 60):
        if len(bars) >= N:
            close_N = bars[N - 1]["close"]
            move = (close_N - entry_mid) if direction == "BUY" else (entry_mid - close_N)
            base[f"fwd_{N}m_pips"] = round(move * 10, 2)
        else:
            base[f"fwd_{N}m_pips"] = ""

    outcome = "timeout"
    exit_bar = bars[TIMEOUT_BARS - 1]
    bars_held = TIMEOUT_BARS

    for i, bar in enumerate(bars[:TIMEOUT_BARS]):
        if direction == "BUY":
            hit_tp = bar["high"] >= tp_trigger
            hit_sl = bar["low"] <= sl_trigger
        else:
            hit_tp = bar["low"] <= tp_trigger
            hit_sl = bar["high"] >= sl_trigger

        if hit_tp and hit_sl:
            outcome = "ambiguous"
            exit_bar = bar
            bars_held = i + 1
            break
        elif hit_tp:
            outcome = "win"
            exit_bar = bar
            bars_held = i + 1
            break
        elif hit_sl:
            outcome = "loss"
            exit_bar = bar
            bars_held = i + 1
            break

    if outcome == "win":
        pnl = TP_DOLLARS
    elif outcome == "loss":
        pnl = -SL_DOLLARS
    elif outcome == "ambiguous":
        pnl = 0.0
    else:
        if direction == "BUY":
            pnl = (exit_bar["close"] - entry_mid) - TWO_HS
        else:
            pnl = (entry_mid - exit_bar["close"]) - TWO_HS

    base.update({
        "outcome": outcome,
        "exit_mid": round(exit_bar["close"], 5),
        "exit_time_utc": exit_bar["time"].isoformat(),
        "bars_held": bars_held,
        "pnl_dollars": round(pnl, 4),
        "pnl_pips": round(pnl * 10, 2),
        "note": "",
    })
    return base

def run_once() -> int:
    signals = _load_signals()
    if not signals:
        log.info("signals.csv empty or missing — nothing to do")
        return 0

    resolved = _load_resolved_ids()
    now = datetime.now(timezone.utc)
    processed = 0

    for sig in signals:
        sig_id = sig["signal_id"]
        if sig_id in resolved:
            continue

        sig_time = _parse_signal_utc(sig["signal_time_utc"])
        age_min = (now - sig_time).total_seconds() / 60

        if age_min < RESOLVE_AFTER_MIN:
            continue

        if age_min > STALE_AFTER_HOURS * 60:
            _append_outcome({
                "signal_id": sig_id,
                "signal_time_utc": sig_time.isoformat(),
                "direction": sig["direction"],
                "outcome": "unresolvable",
                "note": f"older than {STALE_AFTER_HOURS}h — skipped",
                "rsi_distance": sig.get("rsi_distance", ""),
            })
            resolved.add(sig_id)
            processed += 1
            continue

        row = _resolve_one(sig)
        _append_outcome(row)
        resolved.add(sig_id)
        processed += 1

        log.info(
            "Resolved %s  %-4s  %-13s  P&L %s pips",
            sig_id,
            sig["direction"],
            row["outcome"],
            row.get("pnl_pips", "?"),
        )

        time.sleep(1)

    if processed:
        log.info("Processed %d signal(s) this pass", processed)
    return processed

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run one pass then exit")
    parser.add_argument("--interval", type=int, default=300, help="Seconds between passes (default 300)")
    args = parser.parse_args()

    if args.once:
        run_once()
        return 0

    log.info("Tracker started — checking every %ds", args.interval)
    while True:
        try:
            run_once()
        except Exception:
            log.exception("Tracker pass failed")
        time.sleep(args.interval)

if __name__ == "__main__":
    raise SystemExit(main())
