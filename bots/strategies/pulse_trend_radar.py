"""
Pulse Trend Radar — KAMA core with median-ATR bands; signal on trend flip.

Port of Pulse_Trend_Radar.pine core:
  * KAMA(close) with median-ATR volatility bands (kama ± medATR×bandMult).
  * Trend state flips long when prior close pierces the upper band, short when
    it pierces the lower band.
  * Long  = trend flips to bullish; Short = flips to bearish.
  * Risk: SL = medATR × slMult; TP1/TP2/TP3 = R-multiples.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from ..core import indicators as ta
from ..core.strategy_base import Signal, Strategy


class PulseTrendRadar(Strategy):
    name = "pulse_trend_radar"

    def evaluate(self, df: pd.DataFrame) -> Optional[Signal]:
        er_len = int(self.p("er_length", 13))
        fast = int(self.p("fast_alpha", 2))
        slow = int(self.p("slow_alpha", 30))
        band_mult = float(self.p("band_mult", 1.8))
        atr_len = int(self.p("atr_length", 50))
        sl_mult = float(self.p("sl_mult", 3.0))
        tp1m = float(self.p("tp1", 1.0)); tp2m = float(self.p("tp2", 2.0)); tp3m = float(self.p("tp3", 3.0))

        warmup = max(er_len + slow, atr_len, 50)
        if len(df) < warmup + 5:
            return None

        src = df["close"]
        kama = ta.kama(src, er_len, fast, slow).to_numpy()
        tr = ta.true_range(df)
        m_atr = ta.rolling_median(tr, atr_len).to_numpy()
        s = src.to_numpy()
        upper = kama + m_atr * band_mult
        lower = kama - m_atr * band_mult

        n = len(df)
        ts = np.zeros(n, dtype=int)
        cur = 0
        for k in range(n):
            if cur == 0:
                cur = 1 if s[k] > kama[k] else -1
            elif cur == -1 and k >= 1 and s[k - 1] > upper[k - 1]:
                cur = 1
            elif cur == 1 and k >= 1 and s[k - 1] < lower[k - 1]:
                cur = -1
            ts[k] = cur

        i = n - 1
        if ts[i] == ts[i - 1]:
            return None  # no flip on the last closed bar

        c = float(s[i])
        risk = float(m_atr[i]) * sl_mult
        if risk <= 0 or np.isnan(risk):
            return None

        if ts[i] == 1:
            return Signal(1, c, c - risk, c + risk * tp1m, c + risk * tp2m, c + risk * tp3m,
                          "A", 0.0, "Trend flip -> bullish")
        return Signal(-1, c, c + risk, c - risk * tp1m, c - risk * tp2m, c - risk * tp3m,
                      "A", 0.0, "Trend flip -> bearish")
