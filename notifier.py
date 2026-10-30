"""
Notifiers — Telegram (primary) and desktop notification (fallback).

Telegram uses the raw Bot API via `requests`, so no heavy bot library is
required.

IMPORTANT: Telegram's HTML parse mode requires any dynamic text (signal
reason, symbol, etc.) to be HTML-escaped before insertion, otherwise
characters like `<` in "RSI < 20" break the message with a 400 error.
"""
from __future__ import annotations

import html
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone

import requests

import config

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
_SESSION = requests.Session()

def _esc(value) -> str:
    """HTML-escape a value for safe insertion into a Telegram HTML message."""
    return html.escape(str(value), quote=False)

def send_telegram(token: str, chat_id: str, message: str) -> bool:
    """
    Send a message via Telegram (HTML parse mode).

    Uses a persistent HTTP session to eliminate connection and TLS overhead.
    Returns True on success.
    """
    if not token or not chat_id:
        log.error("Telegram token or chat_id is missing")
        return False

    url = TELEGRAM_API.format(token=token)
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        resp = _SESSION.post(url, json=payload, timeout=8)
    except requests.RequestException as exc:
        log.error("Telegram request failed: %s", exc)
        return False

    if resp.status_code == 200 and resp.json().get("ok"):
        log.info("Telegram alert sent")
        return True

    # ── Fallback: retry as plain text if HTML parse failed ─────
    if resp.status_code == 400 and "can't parse entities" in resp.text:
        log.warning("HTML parse failed — retrying as plain text")
        # Strip the common tags so the fallback isn't cluttered
        plain = (message
                 .replace("<b>", "").replace("</b>", "")
                 .replace("<i>", "").replace("</i>", ""))
        payload_plain = {
            "chat_id": chat_id,
            "text": plain,
            "disable_web_page_preview": True,
        }
        try:
            resp2 = _SESSION.post(url, json=payload_plain, timeout=8)
            if resp2.status_code == 200 and resp2.json().get("ok"):
                log.info("Telegram alert sent (plain-text fallback)")
                return True
            log.error("Plain fallback also failed %s: %s",
                      resp2.status_code, resp2.text)
        except requests.RequestException as exc:
            log.error("Plain fallback request failed: %s", exc)

    log.error("Telegram error %s: %s", resp.status_code, resp.text)
    return False

def send_desktop(title: str, message: str) -> None:
    """
    Best-effort desktop notification via notify-send (Fedora/GNOME/KDE).

    Silently does nothing if running headless (no DISPLAY or WAYLAND_DISPLAY)
    or if notify-send isn't available. Non-blocking so it never delays alerts.
    """
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return
    if shutil.which("notify-send") is None:
        return
    try:
        subprocess.Popen(
            ["notify-send", "--urgency=normal", title, message],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("Desktop notification failed: %s", exc)

def _fmt_bar_time(t) -> str:
    """
    Human-readable UTC bar time from an epoch int/float (WebSocket mode),
    a numeric string, or an ISO / 'YYYY-MM-DD HH:MM:SS' string (REST mode).
    """
    if t is None:
        return "?"
    try:
        if isinstance(t, (int, float)):
            dt = datetime.fromtimestamp(t, tz=timezone.utc)
        else:
            s = str(t).strip()
            if s.isdigit():
                dt = datetime.fromtimestamp(int(s), tz=timezone.utc)
            else:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                dt = dt.astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return str(t)

def format_signal_message(
    signal: str,
    symbol: str,
    price: float,
    timeframe: str = "1min",
    trigger_time=None,
    live_price: float | None = None,
    tp_dollars: float = 7.50,
    sl_dollars: float = 5.00,
    rsi: float | None = None,
    adx: float | None = None,
    ema: float | None = None,
    body_ratio: float | None = None,
    reason: str = "",
) -> str:
    """
    Build a slim, honest Telegram alert.

    Design notes:
      - No "confidence meter": the old 0-99% score was a points checklist that
        maxed out for every strong setup and was calibrated on corrupted
        outcome data. Setup facts are shown as facts instead.
      - Entry is clearly labelled as the trigger-bar CLOSE, and the live price
        (at send time) is shown next to it, so staleness is never hidden.
    """
    emoji = "🟢" if signal == "BUY" else "🔴"
    action = "BUY" if signal == "BUY" else "SELL"
    sign_tp = "+" if signal == "BUY" else "-"
    sign_sl = "-" if signal == "BUY" else "+"

    if signal == "BUY":
        tp_price = price + tp_dollars
        sl_price = price - sl_dollars
    else:
        tp_price = price - tp_dollars
        sl_price = price + sl_dollars

    rr = (tp_dollars / sl_dollars) if sl_dollars else 0.0

    lines = [
        f"{emoji} <b>{_esc(action)} — {_esc(symbol)}</b> • {_esc(timeframe)}",
        f"⏱ Bar close: <b>{_esc(_fmt_bar_time(trigger_time))}</b>",
        "",
        f"💵 <b>Entry (bar close):</b> ${_esc(f'{price:.2f}')}",
        f"🎯 <b>TP:</b> ${_esc(f'{tp_price:.2f}')} ({sign_tp}${tp_dollars:.2f})",
        f"🛑 <b>SL:</b> ${_esc(f'{sl_price:.2f}')} ({sign_sl}${sl_dollars:.2f})",
        f"⚖️ <b>R:R</b> 1 : {rr:.1f}",
    ]

    if live_price is not None:
        try:
            delta = live_price - price
            lines.append(
                f"📡 <b>Live now:</b> ${_esc(f'{live_price:.2f}')} ({delta:+.2f})"
            )
        except (TypeError, ValueError):
            pass

    lines.append("")

    if rsi is not None and adx is not None and ema is not None:
        side = "above" if price > ema else "below"
        br = f"{body_ratio:.2f}x" if body_ratio else "n/a"
        lines.append(
            f"📈 RSI({config.RSI_PERIOD}) {_esc(f'{rsi:.1f}')} • "
            f"ADX({config.ADX_PERIOD}) {_esc(f'{adx:.1f}')} • "
            f"EMA({config.EMA_PERIOD}) {_esc(f'{ema:.2f}')} ({side}) • body {_esc(br)}"
        )
    elif reason:
        lines.append(_esc(reason))

    lines.extend([
        "",
        "<i>XAU/USD Bot • alert only, no orders • not financial advice.</i>",
    ])
    return "\n".join(lines)
