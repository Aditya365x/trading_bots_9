"""
Technical indicators that mirror TradingView Pine Script v6 semantics.

All functions take pandas Series / DataFrame and return pandas Series aligned
to the same index. The goal is parity with `ta.*` so the Python signals match
what the original Pine indicators produced on the same candles.

Key parity notes:
  * ta.ema  -> EWM with alpha = 2/(n+1), recursive, seeded on first value.
  * ta.rma  -> Wilder smoothing, alpha = 1/n, seeded with SMA(n) (used by
               ATR / RSI / DMI internally).
  * ta.atr  -> rma(true_range, n).
  * ta.rsi  -> Wilder RSI.
  * ta.dmi  -> +DI, -DI, ADX (Wilder).
  * ta.pivothigh / pivotlow -> confirmed pivots, value placed on the pivot bar
               and only "known" `right` bars later (we expose both forms).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------- #
# Moving averages
# --------------------------------------------------------------------------- #
def ema(src: pd.Series, length: int) -> pd.Series:
    """Pine ta.ema — exponential MA, alpha = 2/(length+1)."""
    return src.ewm(span=length, adjust=False).mean()


def sma(src: pd.Series, length: int) -> pd.Series:
    """Pine ta.sma — simple MA."""
    return src.rolling(length, min_periods=length).mean()


def rma(src: pd.Series, length: int) -> pd.Series:
    """
    Pine ta.rma — Wilder's smoothing (alpha = 1/length), seeded with SMA(length).

    This matches Pine's internal RMA used by ATR/RSI/ADX. We seed the first
    valid value with the simple average of the first `length` samples, then
    apply the recursive Wilder formula.
    """
    src = src.astype(float)
    n = len(src)
    out = np.full(n, np.nan)
    if n == 0:
        return pd.Series(out, index=src.index)
    alpha = 1.0 / length
    values = src.to_numpy()
    # find first index with `length` non-nan values for the SMA seed
    seed_idx = None
    run = 0
    for i in range(n):
        if not np.isnan(values[i]):
            run += 1
        else:
            run = 0
        if run >= length:
            seed_idx = i
            break
    if seed_idx is None:
        return pd.Series(out, index=src.index)
    out[seed_idx] = np.nanmean(values[seed_idx - length + 1: seed_idx + 1])
    for i in range(seed_idx + 1, n):
        prev = out[i - 1]
        cur = values[i]
        if np.isnan(cur):
            out[i] = prev
        else:
            out[i] = alpha * cur + (1 - alpha) * prev
    return pd.Series(out, index=src.index)


# --------------------------------------------------------------------------- #
# Volatility
# --------------------------------------------------------------------------- #
def true_range(df: pd.DataFrame) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    # first bar: high - low (Pine ta.tr behaviour with handle_na false-ish)
    tr.iloc[0] = (high.iloc[0] - low.iloc[0]) if len(df) else np.nan
    return tr


def atr(df: pd.DataFrame, length: int) -> pd.Series:
    """Pine ta.atr — rma(true_range, length)."""
    return rma(true_range(df), length)


# --------------------------------------------------------------------------- #
# Momentum
# --------------------------------------------------------------------------- #
def rsi(src: pd.Series, length: int) -> pd.Series:
    """Pine ta.rsi — Wilder RSI."""
    delta = src.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta.clip(upper=0.0))
    avg_gain = rma(gain, length)
    avg_loss = rma(loss, length)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    out = out.where(avg_loss != 0.0, 100.0)  # all-gain => 100
    return out


def macd(src: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """Pine ta.macd -> (macd_line, signal_line, hist)."""
    macd_line = ema(src, fast) - ema(src, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def dmi(df: pd.DataFrame, di_len: int, adx_len: int):
    """
    Pine ta.dmi(di_len, adx_len) -> (+DI, -DI, ADX), Wilder method.
    """
    high, low = df["high"], df["low"]
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr = true_range(df)
    atr_ = rma(tr, di_len)
    plus_di = 100.0 * rma(plus_dm, di_len) / atr_.replace(0.0, np.nan)
    minus_di = 100.0 * rma(minus_dm, di_len) / atr_.replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    adx = rma(dx, adx_len)
    return plus_di.fillna(0.0), minus_di.fillna(0.0), adx.fillna(0.0)


# --------------------------------------------------------------------------- #
# Pivots / swings
# --------------------------------------------------------------------------- #
def pivot_high(high: pd.Series, left: int, right: int) -> pd.Series:
    """
    Pine ta.pivothigh — value placed on the pivot bar position.

    Returns a Series where, at index i, the value is high[i] if bar i is a
    pivot high (strictly higher than `left` bars before and `right` after),
    else NaN. NOTE: a pivot at bar i is only *confirmable* once `right` more
    bars exist, so callers acting in real time must not use the last `right`
    bars as confirmed.
    """
    h = high.to_numpy()
    n = len(h)
    out = np.full(n, np.nan)
    for i in range(left, n - right):
        v = h[i]
        if v == max(h[i - left: i + right + 1]) and \
           all(v > h[j] for j in range(i - left, i)) and \
           all(v >= h[j] for j in range(i + 1, i + right + 1)):
            out[i] = v
    return pd.Series(out, index=high.index)


def pivot_low(low: pd.Series, left: int, right: int) -> pd.Series:
    """Pine ta.pivotlow — value placed on the pivot bar position."""
    lo = low.to_numpy()
    n = len(lo)
    out = np.full(n, np.nan)
    for i in range(left, n - right):
        v = lo[i]
        if v == min(lo[i - left: i + right + 1]) and \
           all(v < lo[j] for j in range(i - left, i)) and \
           all(v <= lo[j] for j in range(i + 1, i + right + 1)):
            out[i] = v
    return pd.Series(out, index=low.index)


def highest(src: pd.Series, length: int) -> pd.Series:
    return src.rolling(length, min_periods=1).max()


def lowest(src: pd.Series, length: int) -> pd.Series:
    return src.rolling(length, min_periods=1).min()


# --------------------------------------------------------------------------- #
# Specialty
# --------------------------------------------------------------------------- #
def kama(src: pd.Series, er_len: int, fast_len: int, slow_len: int) -> pd.Series:
    """
    Kaufman Adaptive Moving Average — matches `kamaCalc` in Pulse Trend Radar.
        dir = |src - src[er_len]|
        vol = sum(|src - src[1]|, er_len)
        er  = dir / vol
        sc  = (er*(fastSC - slowSC) + slowSC)^2
        kama = kama[1] + sc*(src - kama[1])
    """
    s = src.to_numpy(dtype=float)
    n = len(s)
    out = np.full(n, np.nan)
    fast_sc = 2.0 / (fast_len + 1.0)
    slow_sc = 2.0 / (slow_len + 1.0)
    change = np.abs(np.diff(s, prepend=s[0]))  # |src - src[1]|, first = 0
    change[0] = 0.0
    for i in range(n):
        direction = abs(s[i] - s[i - er_len]) if i >= er_len else 0.0
        vol = change[max(0, i - er_len + 1): i + 1].sum()
        er = direction / vol if vol != 0 else 0.0
        sc = (er * (fast_sc - slow_sc) + slow_sc) ** 2
        prev = out[i - 1] if i > 0 and not np.isnan(out[i - 1]) else s[i]
        out[i] = prev + sc * (s[i] - prev)
    return pd.Series(out, index=src.index)


def rolling_median(src: pd.Series, length: int) -> pd.Series:
    return src.rolling(length, min_periods=1).median()


def vwap_session(df: pd.DataFrame) -> pd.Series:
    """
    Session-anchored VWAP on hlc3, reset each UTC day — approximates Pine
    ta.vwap(hlc3). Requires a DatetimeIndex.
    """
    hlc3 = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"].fillna(0.0)
    if isinstance(df.index, pd.DatetimeIndex):
        day = df.index.normalize()
    else:
        day = pd.Series(0, index=df.index)
    pv = (hlc3 * vol).groupby(day).cumsum()
    cv = vol.groupby(day).cumsum().replace(0.0, np.nan)
    return (pv / cv).fillna(hlc3)


def percent_rank(src: pd.Series, length: int) -> pd.Series:
    """Pine ta.percentrank — % of previous `length` values <= current."""
    def _pr(window):
        cur = window[-1]
        prev = window[:-1]
        if len(prev) == 0:
            return 0.0
        return 100.0 * np.sum(prev <= cur) / len(prev)
    return src.rolling(length + 1, min_periods=1).apply(_pr, raw=True)


def correlation(a: pd.Series, b: pd.Series, length: int) -> pd.Series:
    """Pine ta.correlation."""
    return a.rolling(length, min_periods=length).corr(b)


def cum(src: pd.Series) -> pd.Series:
    """Pine ta.cum — cumulative sum."""
    return src.fillna(0.0).cumsum()
