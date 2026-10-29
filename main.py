#!/usr/bin/env python3
"""
XAU/USD Signal Bot — entry point.

Usage:
    python main.py            # run the bot (WebSocket or REST per .env)
    python main.py --test     # verify API keys + send a test Telegram message
    python main.py --backtest # run a quick historical backtest (1 API credit)
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
from datetime import datetime, timezone

import pandas as pd

import config
import signal_log                       # NEW — appends fired signals to signals.csv
from data_feed import RESTFeed, WebSocketFeed
from indicators import compute_indicators
from notifier import format_signal_message, send_desktop, send_telegram
from strategy import check_signal

# ── Logging ─────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("xauusd-bot")

# ── In-memory candle history ────────────────────────────────
MAX_HISTORY = 500
_candles: list[dict] = []
_last_alert_bar_ts: int | None = None

def _append_candle(candle: dict) -> None:
    """Append a closed candle to the rolling history."""
    global _candles
    _candles.append(candle)
    if len(_candles) > MAX_HISTORY:
        _candles = _candles[-MAX_HISTORY:]

def _to_dataframe(candles: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(candles)
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)

def prime_history(api_key: str, symbol: str, interval: str, outputsize: int = 250) -> int:
    """
    Pre-fill the rolling candle history using 1 REST call on startup.
    This eliminates the ~105-minute cold-start warm-up delay and costs
    only 1 API credit (out of 800 free credits/day).
    """
    import requests
    log.info("Priming candle history from Twelve Data (%d bars, 1 credit)...", outputsize)
    try:
        resp = requests.get(
            "https://api.twelvedata.com/time_series",
            params={
                "symbol": symbol,
                "interval": interval,
                "outputsize": outputsize,
                "apikey": api_key,
                "order": "ASC",
            },
            timeout=20,
        )
        data = resp.json()
        if data.get("status") == "error":
            log.warning("Could not prime history: %s. Starting cold.", data.get("message"))
            return 0
        values = data.get("values", [])
        for v in values:
            try:
                ts = int(pd.to_datetime(v["datetime"]).timestamp())
            except Exception:
                ts = None
            _append_candle({
                "timestamp": ts,
                "datetime": v["datetime"],
                "open": float(v["open"]),
                "high": float(v["high"]),
                "low": float(v["low"]),
                "close": float(v["close"]),
            })
        log.info("✓ Primed %d candles. Indicators (EMA, RSI, ADX) ready immediately.", len(_candles))
        return len(values)
    except Exception as exc:
        log.warning("History priming request failed: %s. Starting cold.", exc)
        return 0

def on_candle_close(candle: dict) -> None:
    """Called whenever a candle closes. Evaluates the strategy."""
    global _last_alert_bar_ts

    _append_candle(candle)

    # Need enough history for EMA 100 + ADX warm-up
    if len(_candles) < config.MIN_BARS:
        log.debug(
            "Warming up — %d/%d candles collected",
            len(_candles),
            config.MIN_BARS,
        )
        return

    df = compute_indicators(_to_dataframe(_candles))
    result = check_signal(df)

    bar_ts = candle.get("timestamp") or candle.get("datetime")

    if result is None:
        log.info(
            "Bar closed @ %.2f | EMA %.2f | RSI %.2f | ADX %.2f | no signal",
            candle["close"],
            df.iloc[-1]["ema"],
            df.iloc[-1]["rsi"],
            df.iloc[-1]["adx"],
        )
        return

    # Dedup: only alert once per bar
    if bar_ts == _last_alert_bar_ts:
        log.debug("Duplicate bar — skipping alert")
        return
    _last_alert_bar_ts = bar_ts

    # 0. SAFETY KILL SWITCH (Instant freeze for real trading)
    if config.KILL_SWITCH:
        log.warning("🛑 KILL SWITCH ACTIVE: %s signal @ %.2f suppressed.", result.signal, result.price)
        return

    message = format_signal_message(
        signal=result.signal,
        symbol=config.SYMBOL,
        price=result.price,
        reason=result.reason,
        timeframe=config.INTERVAL,
        rsi=result.rsi,
        adx=result.adx,
        ema=result.ema,
        body_ratio=result.body_ratio,
        setup_time=result.setup_time,
    )

    # 1. SEND TELEGRAM IMMEDIATELY (highest priority, no disk lag)
    send_telegram(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID, message)

    log.info("🚨 %s SIGNAL @ %.2f — %s", result.signal, result.price, result.reason)

    # 2. Log signal to signals.csv with exact candle close time
    try:
        rsi_threshold = (
            config.RSI_OVERSOLD if result.signal == "BUY" else config.RSI_OVERBOUGHT
        )
        signal_log.record_signal(
            direction=result.signal,
            price_mid=result.price,
            rsi=result.rsi,
            adx=result.adx,
            ema=result.ema,
            body_ratio=result.body_ratio,
            rsi_threshold=rsi_threshold,
            candle_time=bar_ts,
        )
        log.info("Logged signal to signals.csv")
    except Exception:
        log.exception("Failed to write signal to signals.csv")

    # 3. Desktop notification (non-blocking)
    send_desktop(
        f"{result.signal} {config.SYMBOL}",
        f"{result.price:.2f} — {result.reason}",
    )

# ── Modes ───────────────────────────────────────────────────
def run_bot() -> int:
    problems = config.validate()
    if problems:
        for p in problems:
            log.error("Config error: %s", p)
        log.error("Fix .env and try again.")
        return 1

    log.info("Starting XAU/USD signal bot — mode=%s", config.DATA_MODE)
    log.info(
        "Strategy: EMA%d | RSI%d (%.0f/%.0f) | ADX%d > %.0f | body %.1fx–%.1fx",
        config.EMA_PERIOD,
        config.RSI_PERIOD,
        config.RSI_OVERSOLD,
        config.RSI_OVERBOUGHT,
        config.ADX_PERIOD,
        config.ADX_THRESHOLD,
        config.BODY_RATIO_MIN,
        config.BODY_RATIO_MAX,
    )

    # Prime historical candles once on boot (1 REST credit)
    prime_history(config.TWELVE_DATA_API_KEY, config.SYMBOL, config.INTERVAL, outputsize=250)

    if config.DATA_MODE == "websocket":
        feed = WebSocketFeed(
            api_key=config.TWELVE_DATA_API_KEY,
            symbol=config.SYMBOL,
            interval_seconds=config.CANDLE_INTERVAL_SECONDS,
            on_candle_close=on_candle_close,
        )
    else:
        feed = RESTFeed(
            api_key=config.TWELVE_DATA_API_KEY,
            symbol=config.SYMBOL,
            interval=config.INTERVAL,
            poll_seconds=config.REST_POLL_SECONDS,
            poll_hours_utc=config.REST_POLL_HOURS_UTC,
            on_candle_close=on_candle_close,
        )

    def _shutdown(signum, frame):  # noqa: ARG001
        log.info("Shutting down (signal %s)…", signum)
        feed.stop()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        feed.run()
    except SystemExit:
        pass
    except Exception:
        log.exception("Fatal error in feed loop")
        return 1

    return 0

def run_test() -> int:
    """Verify Twelve Data + Telegram and print current indicators."""
    import requests

    problems = config.validate()
    if problems:
        for p in problems:
            log.error("Config error: %s", p)
        return 1

    log.info("Testing Twelve Data API key…")
    try:
        resp = requests.get(
            "https://api.twelvedata.com/quote",
            params={
                "symbol": config.SYMBOL,
                "apikey": config.TWELVE_DATA_API_KEY,
            },
            timeout=15,
        )
        data = resp.json()
        if data.get("status") == "error":
            log.error("Twelve Data error: %s", data.get("message"))
            return 1
        log.info(
            "✓ Twelve Data OK — %s = %s",
            data.get("symbol"),
            data.get("close"),
        )
    except requests.RequestException as exc:
        log.error("Twelve Data request failed: %s", exc)
        return 1

    log.info("Sending test Telegram message (previewing new enhanced format)…")
    current_price = float(data.get("close") or 4025.00)
    sample_card = format_signal_message(
        signal="SELL",
        symbol=config.SYMBOL,
        price=current_price,
        timeframe=config.INTERVAL,
        rsi=88.5,
        adx=36.0,
        ema=current_price + 3.50,
        body_ratio=1.0,
        setup_time=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    )
    ok = send_telegram(
        config.TELEGRAM_BOT_TOKEN,
        config.TELEGRAM_CHAT_ID,
        "🧪 <b>TEST ALERT (PREVIEW OF NEW CARD FORMAT)</b>\n\n" + sample_card,
    )
    if not ok:
        return 1
    log.info("✓ Telegram OK")

    log.info("Fetching recent candles for indicator check…")
    try:
        resp = requests.get(
            "https://api.twelvedata.com/time_series",
            params={
                "symbol": config.SYMBOL,
                "interval": config.INTERVAL,
                "outputsize": max(config.MIN_BARS + 10, 120),
                "apikey": config.TWELVE_DATA_API_KEY,
                "order": "ASC",
            },
            timeout=30,
        )
        data = resp.json()
        if data.get("status") == "error":
            log.error("Twelve Data error: %s", data.get("message"))
            return 1

        values = data.get("values", [])
        if len(values) < config.MIN_BARS:
            log.warning(
                "Only %d candles returned — indicators may be unreliable",
                len(values),
            )

        df = pd.DataFrame(values)
        for col in ("open", "high", "low", "close"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
        df = compute_indicators(df)

        last = df.iloc[-1]
        log.info(
            "Latest bar @ %s | close %.2f | EMA%d %.2f | RSI%d %.2f | ADX%d %.2f",
            last.get("datetime"),
            last["close"],
            config.EMA_PERIOD,
            last["ema"],
            config.RSI_PERIOD,
            last["rsi"],
            config.ADX_PERIOD,
            last["adx"],
        )

        result = check_signal(df)
        if result:
            log.info("Signal on latest bar: %s — %s", result.signal, result.reason)
        else:
            log.info("No signal on latest bar (conditions not met)")

    except requests.RequestException as exc:
        log.error("Indicator fetch failed: %s", exc)
        return 1

    log.info("All checks passed.")
    return 0

def run_backtest(bars: int = 5000) -> int:
    """Fetch historical bars and run the strategy over them."""
    import requests

    problems = config.validate()
    if problems:
        for p in problems:
            log.error("Config error: %s", p)
        return 1

    log.info("Fetching %d bars of %s %s…", bars, config.SYMBOL, config.INTERVAL)
    try:
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
            return 1
    except requests.RequestException as exc:
        log.error("Fetch failed: %s", exc)
        return 1

    values = data.get("values", [])
    if not values:
        log.error("No data returned")
        return 1

    df = pd.DataFrame(values)
    df["datetime"] = pd.to_datetime(df["datetime"])
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    df = compute_indicators(df)

    signals: list[dict] = []
    for i in range(config.MIN_BARS, len(df)):
        window = df.iloc[: i + 1]
        result = check_signal(window)
        if result is not None:
            signals.append(
                {
                    "datetime": df.iloc[i]["datetime"],
                    "signal": result.signal,
                    "price": round(result.price, 2),
                    "rsi": round(result.rsi, 2),
                    "adx": round(result.adx, 2),
                    "body_ratio": round(result.body_ratio, 2),
                }
            )

    log.info("Backtest complete: %d bars scanned, %d signals found", len(df), len(signals))
    if signals:
        out = pd.DataFrame(signals)
        print()
        print(out.to_string(index=False))
    else:
        print("\nNo signals found in the selected window.")
    return 0

# ── CLI ─────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(description="XAU/USD Signal Bot")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--test", action="store_true", help="Verify API keys and send a test message")
    group.add_argument("--backtest", action="store_true", help="Run a historical backtest")
    parser.add_argument("--bars", type=int, default=5000, help="Bars for --backtest (default 5000)")
    args = parser.parse_args()

    if args.test:
        return run_test()
    if args.backtest:
        return run_backtest(bars=args.bars)
    return run_bot()

if __name__ == "__main__":
    raise SystemExit(main())
