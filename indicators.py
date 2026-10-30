"""
Technical indicators implemented with pure pandas.

No external TA library required — avoids version drift and keeps the
dependency surface small.

All functions expect a DataFrame with columns: open, high, low, close.

NOTE: This version uses np.nan (not pd.NA) for divide-by-zero guards.
pd.NA is the pandas "nullable" NAType and breaks .astype(float) under
pandas 3.x / Python 3.14+. np.nan is a real float and propagates safely.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()

def rsi(close: pd.Series, period: int) -> pd.Series:
    """
    Relative Strength Index using Wilder's smoothing.

    RSI = 100 - (100 / (1 + RS))
    RS  = avg_gain / avg_loss
    """
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    # Wilder's smoothing == EMA with alpha = 1/period
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()

    # Guard against divide-by-zero with np.nan (NOT pd.NA).
    avg_loss_safe = avg_loss.mask(avg_loss == 0.0, np.nan)
    rs = avg_gain / avg_loss_safe
    out = 100.0 - (100.0 / (1.0 + rs))

    # Edge case: no losses in the window but some gains → RSI = 100
    out = out.mask((avg_loss == 0.0) & (avg_gain > 0.0), 100.0)
    # Edge case: perfectly flat window → RSI = 50 (convention)
    out = out.mask((avg_loss == 0.0) & (avg_gain == 0.0), 50.0)

    return out.astype(float)

def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """
    Average Directional Index using Wilder's smoothing.

    Steps:
      1. True Range
      2. +DM / -DM
      3. Wilder-smoothed TR, +DM, -DM
      4. +DI, -DI
      5. DX
      6. ADX = Wilder-smoothed DX
    """
    prev_close = close.shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    up_move = high - high.shift(1)
    down_move = low.shift(1) - low

    plus_dm = pd.Series(0.0, index=high.index)
    minus_dm = pd.Series(0.0, index=high.index)

    plus_dm[(up_move > down_move) & (up_move > 0)] = up_move
    minus_dm[(down_move > up_move) & (down_move > 0)] = down_move

    alpha = 1.0 / period
    atr = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_dm_s = plus_dm.ewm(alpha=alpha, adjust=False).mean()
    minus_dm_s = minus_dm.ewm(alpha=alpha, adjust=False).mean()

    # Guard against divide-by-zero with np.nan (NOT pd.NA).
    atr_safe = atr.mask(atr == 0.0, np.nan)
    plus_di = 100.0 * (plus_dm_s / atr_safe)
    minus_di = 100.0 * (minus_dm_s / atr_safe)

    di_sum = plus_di + minus_di
    denom = di_sum.mask(di_sum == 0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / denom
    return dx.ewm(alpha=alpha, adjust=False).mean().astype(float)

def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """
    Average True Range using Wilder's smoothing.

    Used for volatility-scaled TP/SL (config TP_SL_MODE="atr").
    """
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean().astype(float)

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Attach ema, rsi, adx columns to a copy of df.

    df must have: open, high, low, close (numeric).
    Returns a new DataFrame with indicator columns appended.
    """
    out = df.copy()
    out["ema"] = ema(out["close"], period=_ema_period())
    out["rsi"] = rsi(out["close"], period=_rsi_period())
    out["adx"] = adx(out["high"], out["low"], out["close"], period=_adx_period())
    out["atr"] = atr(out["high"], out["low"], out["close"], period=_atr_period())
    return out

# Late imports to avoid circular dependency with config at module import time.
def _ema_period() -> int:
    from config import EMA_PERIOD
    return EMA_PERIOD

def _rsi_period() -> int:
    from config import RSI_PERIOD
    return RSI_PERIOD

def _adx_period() -> int:
    from config import ADX_PERIOD
    return ADX_PERIOD

def _atr_period() -> int:
    from config import ATR_PERIOD
    return ATR_PERIOD
