"""
Bollinger Band Breakout — port of bollinger_band_breakout_strategy.pine.

Entry:
  * LONG  when close crosses ABOVE the upper Bollinger band,
  * SHORT when close crosses BELOW the lower Bollinger band,
  gated by a VOLATILITY-EXPANSION filter (stdev(close) > its own MA — only trade
  when volatility is expanding) and an optional TREND filter (close vs a long
  EMA) and optional ROC filter.

The original exits on the opposite band cross plus a fixed %% SL/TP and a
trailing stop. The live engine here manages a fixed bracket instead, so we map:
  * SL  = sl_pct %% from entry (default 2%),
  * TP1/TP2/TP3 = tp*_pct %% from entry (default 3/6/9%) — the 9% original TP
    becomes TP3, with break-even after TP1 standing in for the trailing stop.
Percent-based stops match the original (it is not ATR-based).
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from ..core import indicators as ta
from ..core.strategy_base import Signal, Strategy


class BollingerBreakout(Strategy):
    name = "bollinger_breakout"

    def evaluate(self, df: pd.DataFrame) -> Optional[Signal]:
        bb_len = int(self.p("bb_len", 15))
        bb_dev = float(self.p("bb_dev", 2.0))
        use_vol = bool(self.p("volatility_filter", True))
        vol_sd_len = int(self.p("vol_sd_len", 15))
        vol_ma_len = int(self.p("vol_ma_len", 15))
        use_trend = bool(self.p("trend_filter", False))
        trend_len = int(self.p("trend_period", 223))
        use_roc = bool(self.p("roc_filter", False))
        roc_period = int(self.p("roc_period", 96))     # ~1 day of 15m bars
        roc_thresh = float(self.p("roc_threshold", 1.0))
        direction_mode = str(self.p("direction", "Long&Short"))
        sl_pct = float(self.p("sl_pct", 2.0))
        tp1_pct = float(self.p("tp1_pct", 3.0))
        tp2_pct = float(self.p("tp2_pct", 6.0))
        tp3_pct = float(self.p("tp3_pct", 9.0))

        n = len(df)
        if n < max(trend_len if use_trend else 0, bb_len, roc_period) + 5:
            return None

        close = df["close"]
        mid = ta.sma(close, bb_len)
        sd = close.rolling(bb_len, min_periods=bb_len).std(ddof=0)
        upper = mid + bb_dev * sd
        lower = mid - bb_dev * sd

        c = close.to_numpy(float)
        up = upper.to_numpy(float); lo = lower.to_numpy(float)
        i = n - 1
        if np.isnan(up[i]) or np.isnan(up[i - 1]):
            return None

        cross_up = c[i] > up[i] and c[i - 1] <= up[i - 1]
        cross_dn = c[i] < lo[i] and c[i - 1] >= lo[i - 1]
        if not (cross_up or cross_dn):
            return None

        # volatility-expansion filter
        if use_vol:
            sd_close = close.rolling(vol_sd_len, min_periods=vol_sd_len).std(ddof=0)
            sd_ma = ta.sma(sd_close, vol_ma_len)
            if not (sd_close.iloc[i] > sd_ma.iloc[i]):
                return None

        # optional long-EMA trend filter
        trend_long = trend_short = True
        if use_trend:
            ema = ta.ema(close, trend_len)
            trend_long = c[i] > ema.iloc[i]
            trend_short = c[i] < ema.iloc[i]

        # optional rate-of-change filter
        roc_ok = True
        if use_roc and i >= roc_period:
            roc = (c[i] - c[i - roc_period]) / c[i - roc_period] * 100.0
            roc_ok = roc > roc_thresh

        allow_long = direction_mode in ("Long&Short", "Long Only", "Auto")
        allow_short = direction_mode in ("Long&Short", "Short Only", "Auto")

        entry = float(c[i])
        bandwidth = float((up[i] - lo[i]) / mid.iloc[i] * 100.0) if mid.iloc[i] else 0.0
        score = round(min(100.0, bandwidth * 10.0), 1)     # wider band on breakout = stronger
        grade = "A+" if score >= 75 else "A" if score >= 60 else "B" if score >= 40 else "C"

        if cross_up and allow_long and trend_long and roc_ok:
            sl = entry * (1 - sl_pct / 100.0)
            return Signal(1, entry, sl, entry * (1 + tp1_pct / 100.0),
                          entry * (1 + tp2_pct / 100.0), entry * (1 + tp3_pct / 100.0),
                          grade, score, f"BB breakout long (band {bandwidth:.1f}%)")
        if cross_dn and allow_short and trend_short and roc_ok:
            sl = entry * (1 + sl_pct / 100.0)
            return Signal(-1, entry, sl, entry * (1 - tp1_pct / 100.0),
                          entry * (1 - tp2_pct / 100.0), entry * (1 - tp3_pct / 100.0),
                          grade, score, f"BB breakdown short (band {bandwidth:.1f}%)")
        return None
