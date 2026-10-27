"""
Data feeds for XAU/USD.

Two implementations:

  WebSocketFeed  — streams real-time ticks from Twelve Data and builds
                   1-minute OHLC candles locally. Uses 1 WebSocket credit.
                   Recommended for the free tier.

  RESTFeed       — polls the time_series endpoint every N seconds and
                   emits the last *closed* candle. Uses 1 REST credit per
                   poll (watch the 800/day limit).

Both feed implementations accept an `on_candle_close` callback that
receives a dict:
    {"timestamp": int, "open": float, "high": float, "low": float, "close": float}
or, for REST mode, the same dict plus a "datetime" string.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Callable, Optional

import requests
import websocket

log = logging.getLogger(__name__)

WS_URL = "wss://ws.twelvedata.com/v1/quotes/price"
REST_URL = "https://api.twelvedata.com/time_series"

CandleCallback = Callable[[dict], None]

# ─────────────────────────────────────────────────────────────
# Candle builder (WebSocket mode)
# ─────────────────────────────────────────────────────────────
class CandleBuilder:
    """Aggregates ticks into fixed-interval OHLC candles."""

    def __init__(self, interval_seconds: int, on_close: CandleCallback):
        self.interval = interval_seconds
        self.on_close = on_close
        self.current: Optional[dict] = None

    def add_tick(self, price: float, timestamp: int) -> None:
        bucket = (timestamp // self.interval) * self.interval

        if self.current is None:
            self.current = self._new(bucket, price)
            return

        # Guard against stale/out-of-order ticks from network jitter
        if bucket < self.current["timestamp"]:
            log.debug("Discarding stale tick timestamp %d (current %d)", timestamp, self.current["timestamp"])
            return

        if bucket > self.current["timestamp"]:
            closed = self.current
            self.current = self._new(bucket, price)
            time_gap = bucket - closed["timestamp"]
            if time_gap > self.interval * 3:
                log.warning("Data gap of %ds detected before tick — stream resumed", time_gap)
            self.on_close(closed)
        else:
            c = self.current
            c["high"] = max(c["high"], price)
            c["low"] = min(c["low"], price)
            c["close"] = price

    def flush(self) -> None:
        """Emit the in-progress candle (used on shutdown)."""
        if self.current is not None:
            self.on_close(self.current)
            self.current = None

    @staticmethod
    def _new(ts: int, price: float) -> dict:
        return {
            "timestamp": ts,
            "open": price,
            "high": price,
            "low": price,
            "close": price,
        }

# ─────────────────────────────────────────────────────────────
# WebSocket feed
# ─────────────────────────────────────────────────────────────
class WebSocketFeed:
    """Twelve Data WebSocket feed with automatic reconnection."""

    def __init__(
        self,
        api_key: str,
        symbol: str,
        interval_seconds: int,
        on_candle_close: CandleCallback,
    ):
        self.api_key = api_key
        self.symbol = symbol
        self.builder = CandleBuilder(interval_seconds, on_candle_close)
        self._stop = False
        self.ws: Optional[websocket.WebSocketApp] = None

    # ── WebSocketApp callbacks ───────────────────────────────
    def _on_open(self, ws) -> None:
        log.info("WebSocket connected — subscribing to %s", self.symbol)
        ws.send(
            json.dumps(
                {
                    "action": "subscribe",
                    "params": {"symbols": self.symbol},
                }
            )
        )

    def _on_message(self, ws, message: str) -> None:
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return

        event = data.get("event")

        if event == "price":
            try:
                self.builder.add_tick(
                    float(data["price"]),
                    int(data["timestamp"]),
                )
            except (KeyError, ValueError, TypeError) as exc:
                log.warning("Malformed price event: %s (%s)", data, exc)

        elif event == "subscribe-status":
            log.info("Subscription status: %s", data.get("status"))

        elif event == "heartbeat":
            log.debug("Heartbeat received")

        else:
            log.debug("Unhandled event: %s", event)

    def _on_error(self, ws, error) -> None:
        log.error("WebSocket error: %s", error)

    def _on_close(self, ws, code, msg) -> None:
        log.warning("WebSocket closed (code=%s, msg=%s)", code, msg)

    # ── Run loop ─────────────────────────────────────────────
    def run(self) -> None:
        """Blocking run loop with reconnect. Call from the main thread."""
        url = f"{WS_URL}?apikey={self.api_key}"

        while not self._stop:
            self.ws = websocket.WebSocketApp(
                url,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )
            # ping_interval keeps the connection alive through idle periods
            self.ws.run_forever(ping_interval=30, ping_timeout=10)

            if not self._stop:
                log.info("Reconnecting in 5 seconds…")
                time.sleep(5)

    def stop(self) -> None:
        self._stop = True
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:
                pass

# ─────────────────────────────────────────────────────────────
# REST feed (fallback)
# ─────────────────────────────────────────────────────────────
def _in_hour_window(hours_spec: str, now_utc: datetime) -> bool:
    """
    Parse a spec like "8-21" or "0-23" and return True if now_utc is inside.
    """
    hours_spec = hours_spec.strip()
    if not hours_spec or "-" not in hours_spec:
        return True
    try:
        start_s, end_s = hours_spec.split("-", 1)
        start, end = int(start_s), int(end_s)
    except ValueError:
        return True
    return start <= now_utc.hour <= end

class RESTFeed:
    """Polls Twelve Data time_series and emits the last closed candle."""

    def __init__(
        self,
        api_key: str,
        symbol: str,
        interval: str,
        poll_seconds: int,
        poll_hours_utc: str,
        on_candle_close: CandleCallback,
    ):
        self.api_key = api_key
        self.symbol = symbol
        self.interval = interval
        self.poll_seconds = poll_seconds
        self.poll_hours_utc = poll_hours_utc
        self.on_candle_close = on_candle_close
        self._stop = False
        self._last_closed_ts: Optional[str] = None

    # ── HTTP ─────────────────────────────────────────────────
    def _fetch(self, outputsize: int = 2) -> list[dict]:
        params = {
            "symbol": self.symbol,
            "interval": self.interval,
            "outputsize": outputsize,
            "apikey": self.api_key,
            "order": "ASC",
        }
        resp = requests.get(REST_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") == "error":
            raise RuntimeError(f"Twelve Data: {data.get('message')}")

        return data.get("values", [])

    # ── Run loop ─────────────────────────────────────────────
    def run(self) -> None:
        log.info(
            "REST feed started — polling every %ss (UTC hours: %s)",
            self.poll_seconds,
            self.poll_hours_utc,
        )

        while not self._stop:
            now = datetime.now(timezone.utc)
            if not _in_hour_window(self.poll_hours_utc, now):
                log.debug("Outside polling window — sleeping")
                time.sleep(self.poll_seconds)
                continue

            try:
                values = self._fetch(outputsize=2)

                # values[-1] is the currently-forming candle.
                # values[-2] is the last fully-closed candle.
                if len(values) >= 2:
                    closed = values[-2]
                    ts = closed["datetime"]

                    if ts != self._last_closed_ts:
                        self._last_closed_ts = ts
                        candle = {
                            "datetime": ts,
                            "open": float(closed["open"]),
                            "high": float(closed["high"]),
                            "low": float(closed["low"]),
                            "close": float(closed["close"]),
                        }
                        self.on_candle_close(candle)
                elif len(values) == 1:
                    # Only one candle returned — can't confirm closure yet.
                    pass

            except requests.RequestException as exc:
                log.error("REST request failed: %s", exc)
            except RuntimeError as exc:
                log.error("Twelve Data error: %s", exc)

            time.sleep(self.poll_seconds)

    def stop(self) -> None:
        self._stop = True
