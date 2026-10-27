"""
Configuration loader.

Reads .env (via python-dotenv) and exposes typed constants for the rest of
the app. Strategy parameters can be overridden from .env without editing code.
"""
import os

from dotenv import load_dotenv

load_dotenv()

def _get_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default

def _get_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default

# ── Credentials ──────────────────────────────────────────────
TWELVE_DATA_API_KEY: str = os.getenv("TWELVE_DATA_API_KEY", "").strip()
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# ── Data feed ────────────────────────────────────────────────
SYMBOL: str = "XAU/USD"
INTERVAL: str = "1min"
DATA_MODE: str = os.getenv("DATA_MODE", "websocket").strip().lower()
REST_POLL_SECONDS: int = _get_int("REST_POLL_SECONDS", 60)
REST_POLL_HOURS_UTC: str = os.getenv("REST_POLL_HOURS_UTC", "0-23").strip()

CANDLE_INTERVAL_SECONDS: int = _get_int("CANDLE_INTERVAL_SECONDS", 60)

# ── Strategy ─────────────────────────────────────────────────
EMA_PERIOD: int = _get_int("EMA_PERIOD", 100)
RSI_PERIOD: int = _get_int("RSI_PERIOD", 3)
ADX_PERIOD: int = _get_int("ADX_PERIOD", 5)
RSI_OVERSOLD: float = _get_float("RSI_OVERSOLD", 20.0)
RSI_OVERBOUGHT: float = _get_float("RSI_OVERBOUGHT", 80.0)
ADX_THRESHOLD: float = _get_float("ADX_THRESHOLD", 30.0)
BODY_RATIO_MIN: float = _get_float("BODY_RATIO_MIN", 0.5)
BODY_RATIO_MAX: float = _get_float("BODY_RATIO_MAX", 1.5)

# Minimum bars needed before indicators are valid
MIN_BARS: int = max(EMA_PERIOD, ADX_PERIOD * 2, RSI_PERIOD * 2) + 5

def validate() -> list[str]:
    """Return a list of configuration problems (empty = OK)."""
    problems: list[str] = []
    if not TWELVE_DATA_API_KEY:
        problems.append("TWELVE_DATA_API_KEY is not set")
    if not TELEGRAM_BOT_TOKEN:
        problems.append("TELEGRAM_BOT_TOKEN is not set")
    if not TELEGRAM_CHAT_ID:
        problems.append("TELEGRAM_CHAT_ID is not set")
    if DATA_MODE not in ("websocket", "rest"):
        problems.append(f"DATA_MODE must be 'websocket' or 'rest' (got {DATA_MODE!r})")
    return problems
