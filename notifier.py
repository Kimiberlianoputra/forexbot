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
import shutil
import subprocess

import requests

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

def _esc(value) -> str:
    """HTML-escape a value for safe insertion into a Telegram HTML message."""
    return html.escape(str(value), quote=False)

def send_telegram(token: str, chat_id: str, message: str) -> bool:
    """
    Send a message via Telegram (HTML parse mode).

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
        resp = requests.post(url, json=payload, timeout=10)
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
            resp2 = requests.post(url, json=payload_plain, timeout=10)
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

    Silently does nothing if notify-send isn't available.
    """
    if shutil.which("notify-send") is None:
        return
    try:
        subprocess.run(
            ["notify-send", "--urgency=normal", title, message],
            check=False,
            timeout=5,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("Desktop notification failed: %s", exc)

def format_signal_message(
    signal: str,
    symbol: str,
    price: float,
    reason: str,
    timeframe: str = "1min",
) -> str:
    """Build a clean Telegram message for a signal. All dynamic content
    is HTML-escaped so characters like `<`, `>`, `&` cannot break the
    parse."""
    emoji = "🟢" if signal == "BUY" else "🔴"
    action = "BUY" if signal == "BUY" else "SELL"

    return (
        f"{emoji} <b>{_esc(action)} SIGNAL — {_esc(symbol)}</b>\n"
        f"<b>Timeframe:</b> {_esc(timeframe)}\n"
        f"<b>Price:</b> {_esc(f'{price:.2f}')}\n"
        f"<b>Reason:</b> {_esc(reason)}\n"
        f"\n<i>Not financial advice.</i>"
    )
