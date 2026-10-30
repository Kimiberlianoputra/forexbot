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
TELEGRAM_ADMIN_CHAT_ID: str = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "").strip()

# ── Data feed ────────────────────────────────────────────────
SYMBOL: str = "XAU/USD"
INTERVAL: str = "1min"
DATA_MODE: str = os.getenv("DATA_MODE", "websocket").strip().lower()
REST_POLL_SECONDS: int = _get_int("REST_POLL_SECONDS", 60)
REST_POLL_HOURS_UTC: str = os.getenv("REST_POLL_HOURS_UTC", "0-23").strip()

CANDLE_INTERVAL_SECONDS: int = _get_int("CANDLE_INTERVAL_SECONDS", 60)
# A closed candle is only trusted if it saw at least this many ticks.
# Sparse tick delivery (free WS tier) can fabricate candles that never existed.
MIN_TICKS_PER_CANDLE: int = _get_int("MIN_TICKS_PER_CANDLE", 2)

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

# ── Trade plan (single source of truth: notifier + tracker) ──
TP_DOLLARS: float = _get_float("TP_DOLLARS", 7.50)
SL_DOLLARS: float = _get_float("SL_DOLLARS", 5.00)
# "fixed" = constant dollar TP/SL; "atr" = volatility-scaled
# (TP = TP_ATR_MULT x ATR(ATR_PERIOD), SL = SL_ATR_MULT x ATR)
TP_SL_MODE: str = os.getenv("TP_SL_MODE", "fixed").strip().lower()
ATR_PERIOD: int = _get_int("ATR_PERIOD", 14)
TP_ATR_MULT: float = _get_float("TP_ATR_MULT", 1.5)
SL_ATR_MULT: float = _get_float("SL_ATR_MULT", 1.0)
MIN_SL_DOLLARS: float = _get_float("MIN_SL_DOLLARS", 1.0)

def compute_tp_sl(atr: float | None) -> tuple[float, float]:
    """Return (tp_dollars, sl_dollars) for a signal with the given ATR."""
    if TP_SL_MODE == "atr" and atr is not None and atr > 0:
        sl = max(MIN_SL_DOLLARS, SL_ATR_MULT * atr)
        tp = max(sl * 1.1, TP_ATR_MULT * atr)
        return round(tp, 2), round(sl, 2)
    return TP_DOLLARS, SL_DOLLARS

# ── Signal gating ────────────────────────────────────────────
ENABLE_BUY: bool = os.getenv("ENABLE_BUY", "true").strip().lower() in ("true", "1", "yes")
ENABLE_SELL: bool = os.getenv("ENABLE_SELL", "true").strip().lower() in ("true", "1", "yes")
# Suppress signals within N seconds of the previous one (0 = off).
SIGNAL_COOLDOWN_SECONDS: int = _get_int("SIGNAL_COOLDOWN_SECONDS", 300)
# Re-verify every signal against REST candles before alerting (cheap: 1
# credit per actual signal, catches phantom candles from sparse WS ticks).
CONFIRM_WITH_REST: bool = os.getenv("CONFIRM_WITH_REST", "true").strip().lower() in ("true", "1", "yes")

# ── Safety / Kill Switch ─────────────────────────────────────
_KILL_SWITCH_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".kill_switch_active")
KILL_SWITCH: bool = os.getenv("KILL_SWITCH", "false").strip().lower() in ("true", "1", "yes")
MAX_CONSECUTIVE_LOSSES: int = _get_int("MAX_CONSECUTIVE_LOSSES", 3)
MAX_DAILY_DRAWDOWN_USD: float = _get_float("MAX_DAILY_DRAWDOWN_USD", 50.0)

def is_kill_switch_active() -> bool:
    """Check if kill switch is active via persistent file or environment flag."""
    if os.path.exists(_KILL_SWITCH_FILE):
        return True
    return KILL_SWITCH

def set_kill_switch(active: bool) -> None:
    """Set kill switch state and persist to disk so it survives restarts."""
    global KILL_SWITCH
    KILL_SWITCH = active
    if active:
        try:
            with open(_KILL_SWITCH_FILE, "w") as f:
                f.write("active\n")
        except Exception:
            pass
    else:
        if os.path.exists(_KILL_SWITCH_FILE):
            try:
                os.remove(_KILL_SWITCH_FILE)
            except Exception:
                pass

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
    if TP_SL_MODE not in ("fixed", "atr"):
        problems.append(f"TP_SL_MODE must be 'fixed' or 'atr' (got {TP_SL_MODE!r})")
    return problems
