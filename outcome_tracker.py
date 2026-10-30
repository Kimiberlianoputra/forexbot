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
import fcntl
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
# TP/SL defaults now live in config.py (single source of truth shared with
# the notifier). signals.csv rows written by the new bot carry their own
# tp_dollars / sl_dollars; older rows fall back to config defaults.
HALF_SPREAD = 0.025                  # $0.05 total spread / 2
TWO_HS = 2 * HALF_SPREAD             # 0.05 round-trip spread cost
TIMEOUT_BARS = 60                     # 60 bars (1 hour trade timeout)

# ── Fetch settings ────────────────────────────────────────────────────
BAR_INTERVAL = "1min"
FORWARD_BARS = 75                     # fetch +75 min of data to cover full 60 bars
RESOLVE_AFTER_MIN = 80                # wait 80 min before resolving to ensure bars are closed
STALE_AFTER_HOURS = 168                # older than this -> unresolvable

# Free plan is capped at 8 REST credits/minute. One resolve = 1 credit, so
# sleeping 8s between fetches keeps a catch-up pass at ~7.5 credits/min —
# under the limit even when resolving a large backlog after downtime.
RATE_LIMIT_SLEEP = 8                  # seconds between Twelve Data fetches
MAX_FETCH_ATTEMPTS = 5                # give up on a signal after this many failed fetches

class FetchRetry(Exception):
    """Transient fetch failure (network blip / 429 / 5xx) — retry next pass.

    A momentary outage must not permanently mark a signal 'unresolvable':
    that would silently erase real outcomes from the stats.
    """

# signal_id -> consecutive failed fetch attempts (persists across passes)
_fetch_failures: dict[str, int] = {}

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
    """
    Fetch the bars that follow a signal.

    IMPORTANT: end_date must be given explicitly. With only start_date +
    outputsize, Twelve Data returns the most RECENT bars in the window —
    which silently resolved old signals against bars from days later and
    fabricated win/loss outcomes.
    """
    start = signal_time - timedelta(minutes=2)
    end = signal_time + timedelta(minutes=FORWARD_BARS + 5)
    now = datetime.now(timezone.utc)
    if end > now:
        end = now
    params = {
        "symbol": config.SYMBOL,
        "interval": BAR_INTERVAL,
        "start_date": start.strftime("%Y-%m-%d %H:%M:%S"),
        "end_date": end.strftime("%Y-%m-%d %H:%M:%S"),
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

    # Per-signal TP/SL. New signal rows carry them; older rows have blanks,
    # in which case we fall back to the config defaults.
    def _csv_float(v, fallback):
        try:
            f = float(v)
            return f if f > 0 else fallback
        except (TypeError, ValueError):
            return fallback

    tp_d = _csv_float(sig.get("tp_dollars"), config.TP_DOLLARS)
    sl_d = _csv_float(sig.get("sl_dollars"), config.SL_DOLLARS)

    if direction == "BUY":
        entry_fill = entry_mid + HALF_SPREAD
        tp_trigger = entry_mid + tp_d + TWO_HS
        sl_trigger = entry_mid - sl_d + TWO_HS
    else:
        entry_fill = entry_mid - HALF_SPREAD
        tp_trigger = entry_mid - tp_d - TWO_HS
        sl_trigger = entry_mid + sl_d - TWO_HS

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
        # Transient failure (timeout, 429 rate limit, 5xx). Raise so run_once
        # can retry on a later pass instead of burning the outcome forever.
        raise FetchRetry(f"{sig_id}: {exc}") from exc

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

    # Sanity check: the first forward bar must sit right after the signal.
    # If it doesn't, the fetch returned a misaligned window and any outcome
    # built from it would be fiction (see the 2026-10-20/21 corrupted rows).
    if not bars or (bars[0]["time"] - t0) > timedelta(minutes=5):
        first = bars[0]["time"].isoformat() if bars else "none"
        base["outcome"] = "unresolvable"
        base["note"] = f"fetch misaligned: first bar {first} vs signal {t0.isoformat()}"
        return base

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
        pnl = tp_d
    elif outcome == "loss":
        pnl = -sl_d
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

        try:
            row = _resolve_one(sig)
        except FetchRetry as exc:
            tries = _fetch_failures.get(sig_id, 0) + 1
            _fetch_failures[sig_id] = tries
            if tries >= MAX_FETCH_ATTEMPTS:
                # API is down for real (or signal is cursed) — stop retrying,
                # cap the credit waste at MAX_FETCH_ATTEMPTS per signal.
                _append_outcome({
                    "signal_id": sig_id,
                    "signal_time_utc": sig_time.isoformat(),
                    "direction": sig["direction"],
                    "outcome": "unresolvable",
                    "note": f"fetch failed {tries}x: {exc}",
                    "rsi_distance": sig.get("rsi_distance", ""),
                })
                resolved.add(sig_id)
                _fetch_failures.pop(sig_id, None)
                processed += 1
                log.error("Gave up on %s after %d failed fetches", sig_id, tries)
            else:
                log.warning(
                    "Fetch failed for %s (attempt %d/%d) — retrying next pass",
                    sig_id, tries, MAX_FETCH_ATTEMPTS,
                )
            time.sleep(RATE_LIMIT_SLEEP)
            continue

        _fetch_failures.pop(sig_id, None)
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

        time.sleep(RATE_LIMIT_SLEEP)

    if processed:
        log.info("Processed %d signal(s) this pass", processed)
    return processed

def main() -> int:
    # Single-instance guard: the systemd service plus a manual run used to
    # append duplicate outcome rows (both passes resolved the same signals).
    lock_fh = open(os.path.join(_HERE, ".tracker.lock"), "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.error("Another outcome_tracker instance is already running — exiting.")
        return 1

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
