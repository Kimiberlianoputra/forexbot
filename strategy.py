"""
Signal logic for XAU/USD — two-bar setup / trigger model.

BUY
---
  Setup bar (N-1):   close > EMA100  AND  RSI(3) < 20  AND  ADX(5) > 30
  Trigger bar (N):   close > EMA100  AND  green candle
                     AND body(N) / body(N-1) in [BODY_RATIO_MIN, BODY_RATIO_MAX]

SELL
----
  Setup bar (N-1):   close < EMA100  AND  RSI(3) > 80  AND  ADX(5) > 30
  Trigger bar (N):   close < EMA100  AND  red candle
                     AND body(N) / body(N-1) in [BODY_RATIO_MIN, BODY_RATIO_MAX]

RSI and ADX are only required on the *setup* bar. The trigger bar only
needs to satisfy: side of EMA, colour, and a "normal" body relative to
the setup bar.

`check_signal(df)` evaluates the last two rows of df — the last row is
the just-closed trigger bar (N), the second-to-last is the setup bar (N-1).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import pandas as pd

from config import (
    ADX_THRESHOLD,
    BODY_RATIO_MAX,
    BODY_RATIO_MIN,
    EMA_PERIOD,
    RSI_OVERBOUGHT,
    RSI_OVERSOLD,
    RSI_PERIOD,
    ADX_PERIOD,
)

Signal = Literal["BUY", "SELL"]

@dataclass
class SignalResult:
    signal: Signal
    price: float
    reason: str
    ema: float
    rsi: float          # setup-bar RSI
    adx: float          # setup-bar ADX
    body_ratio: float   # trigger body / setup body
    setup_time: str
    trigger_time: str

def _body(row: pd.Series) -> float:
    return abs(float(row["close"]) - float(row["open"]))

def _normal_body(trigger_body: float, setup_body: float) -> tuple[bool, float]:
    """
    Return (is_normal, ratio).

    Guard: if the setup bar is a doji (zero body), the ratio is undefined.
    We treat that as "not normal" — the setup bar should have had *some*
    real body for the comparison to be meaningful.
    """
    if setup_body <= 0:
        return False, float("nan")
    ratio = trigger_body / setup_body
    return BODY_RATIO_MIN <= ratio <= BODY_RATIO_MAX, ratio

def _fmt_time(row: pd.Series) -> str:
    if "datetime" in row.index and pd.notna(row["datetime"]):
        return str(row["datetime"])
    if "timestamp" in row.index and pd.notna(row["timestamp"]):
        return str(row["timestamp"])
    return "?"

def check_signal(df: pd.DataFrame) -> Optional[SignalResult]:
    """
    Evaluate the last two closed candles in df.

    df must contain columns: open, high, low, close, ema, rsi, adx
    (indicators computed by indicators.compute_indicators).

    Returns a SignalResult or None.
    """
    if len(df) < max(EMA_PERIOD, 2, RSI_PERIOD * 2, ADX_PERIOD * 2):
        return None

    setup = df.iloc[-2]     # bar N-1
    trigger = df.iloc[-1]   # bar N

    # Guard against NaN indicators on the setup bar (warm-up)
    if (
        pd.isna(setup.get("ema"))
        or pd.isna(setup.get("rsi"))
        or pd.isna(setup.get("adx"))
    ):
        return None

    setup_close = float(setup["close"])
    trigger_close = float(trigger["close"])
    trigger_open = float(trigger["open"])

    setup_ema = float(setup["ema"])
    trigger_ema = float(trigger["ema"])
    setup_rsi = float(setup["rsi"])
    setup_adx = float(setup["adx"])

    setup_body = _body(setup)
    trigger_body = _body(trigger)
    normal, ratio = _normal_body(trigger_body, setup_body)

    setup_time = _fmt_time(setup)
    trigger_time = _fmt_time(trigger)

    # ── BUY ──────────────────────────────────────────────────
    if (
        setup_close > setup_ema
        and trigger_close > trigger_ema
        and setup_rsi < RSI_OVERSOLD
        and setup_adx > ADX_THRESHOLD
        and trigger_close > trigger_open
        and normal
    ):
        return SignalResult(
            signal="BUY",
            price=trigger_close,
            reason=(
                f"setup {setup_time}: close {setup_close:.2f} > EMA{EMA_PERIOD} "
                f"{setup_ema:.2f}, RSI{RSI_PERIOD} {setup_rsi:.2f} < {RSI_OVERSOLD}, "
                f"ADX {setup_adx:.2f} > {ADX_THRESHOLD} | "
                f"trigger {trigger_time}: green, body {trigger_body:.2f} "
                f"(ratio {ratio:.2f} vs setup)"
            ),
            ema=setup_ema,
            rsi=setup_rsi,
            adx=setup_adx,
            body_ratio=ratio,
            setup_time=setup_time,
            trigger_time=trigger_time,
        )

    # ── SELL ─────────────────────────────────────────────────
    if (
        setup_close < setup_ema
        and trigger_close < trigger_ema
        and setup_rsi > RSI_OVERBOUGHT
        and setup_adx > ADX_THRESHOLD
        and trigger_close < trigger_open
        and normal
    ):
        return SignalResult(
            signal="SELL",
            price=trigger_close,
            reason=(
                f"setup {setup_time}: close {setup_close:.2f} < EMA{EMA_PERIOD} "
                f"{setup_ema:.2f}, RSI{RSI_PERIOD} {setup_rsi:.2f} > {RSI_OVERBOUGHT}, "
                f"ADX {setup_adx:.2f} > {ADX_THRESHOLD} | "
                f"trigger {trigger_time}: red, body {trigger_body:.2f} "
                f"(ratio {ratio:.2f} vs setup)"
            ),
            ema=setup_ema,
            rsi=setup_rsi,
            adx=setup_adx,
            body_ratio=ratio,
            setup_time=setup_time,
            trigger_time=trigger_time,
        )

    return None
