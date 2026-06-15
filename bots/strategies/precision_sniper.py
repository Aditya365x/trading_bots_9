"""
Precision Sniper — EMA ribbon cross gated by a multi-factor confluence score.

Faithful port of the entry engine from Presision_sniper.pine:
  * EMAs (fast/slow/trend), ATR, RSI, MACD histogram, volume vs SMA, ADX/DMI,
    session VWAP.
  * bull/bear confluence score (max 10 with volume+VWAP available).
  * Long  = EMA fast crosses ABOVE slow + price above both + RSI<75
            + score >= effective min + grade filter + not high-vol skip.
    Short = mirror.
  * SL = structure-based (wider of ATR stop vs swing low/high, capped 1.5×ATR,
    floored 0.5×ATR). TP1/TP2/TP3 = R-multiples of SL distance.

Visual/dashboard/alert code from the original is intentionally dropped.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from ..core import indicators as ta
from ..core.strategy_base import Signal, Strategy

_GRADE_APLUS, _GRADE_A, _GRADE_B = 0.80, 0.65, 0.50


class PrecisionSniper(Strategy):
    name = "precision_sniper"

    def evaluate(self, df: pd.DataFrame) -> Optional[Signal]:
        ema_fast_len = int(self.p("ema_fast", 9))
        ema_slow_len = int(self.p("ema_slow", 21))
        ema_trend_len = int(self.p("ema_trend", 55))
        rsi_len = int(self.p("rsi_len", 13))
        atr_len = int(self.p("atr_len", 14))
        min_score = float(self.p("min_score", 5))
        sl_mult = float(self.p("sl_mult", 1.5))
        tp1m = float(self.p("tp1", 1.0)); tp2m = float(self.p("tp2", 2.0)); tp3m = float(self.p("tp3", 3.0))
        swing_lb = int(self.p("swing_lookback", 10))
        grade_filter = self.p("grade_filter", "All")     # All | A+ and A | A+ Only
        hide_c = bool(self.p("hide_c_grade", True))
        high_vol_thresh = float(self.p("high_vol_thresh", 1.3))
        skip_high_vol = bool(self.p("skip_high_vol", True))

        if len(df) < max(ema_trend_len, 50) + 5:
            return None

        src = df["close"]
        ema_fast = ta.ema(src, ema_fast_len)
        ema_slow = ta.ema(src, ema_slow_len)
        ema_trend = ta.ema(src, ema_trend_len)
        atr = ta.atr(df, atr_len)
        rsi = ta.rsi(src, rsi_len)
        _, _, hist = ta.macd(src)
        vol_sma = ta.sma(df["volume"], 20)
        plus_di, minus_di, adx = ta.dmi(df, 14, 14)
        vwap = ta.vwap_session(df)

        i = len(df) - 1
        c = src.iloc[i]
        atr_v = float(atr.iloc[i])
        if atr_v <= 0 or pd.isna(atr_v):
            return None

        has_vol = bool((df["volume"] > 0).any())
        vol_above = has_vol and df["volume"].iloc[i] > float(vol_sma.iloc[i] or 0) * 1.2
        vwap_valid = has_vol  # intraday assumed for crypto
        max_score = 8.0 + (1.0 if has_vol else 0.0) + (1.0 if vwap_valid else 0.0)
        eff_min = min_score * max_score / 10.0

        macd_hist = float(hist.iloc[i]); macd_prev = float(hist.iloc[i - 1])
        strong_trend = float(adx.iloc[i]) > 20
        htf_bias = 1 if ema_fast.iloc[i] > ema_slow.iloc[i] else (-1 if ema_fast.iloc[i] < ema_slow.iloc[i] else 0)

        def score(bull: bool) -> float:
            s = 0.0
            s += 1.0 if ((ema_fast.iloc[i] > ema_slow.iloc[i]) == bull) else 0.0
            s += 1.0 if ((c > ema_trend.iloc[i]) == bull) else 0.0
            if bull:
                s += 1.0 if (50 < rsi.iloc[i] < 75) else 0.0
                s += 1.0 if macd_hist > 0 else 0.0
                s += 1.0 if macd_hist > macd_prev else 0.0
                s += 1.0 if (vwap_valid and c > vwap.iloc[i]) else 0.0
                s += 1.0 if (strong_trend and plus_di.iloc[i] > minus_di.iloc[i]) else 0.0
                s += 1.5 if htf_bias == 1 else 0.0
                s += 0.5 if c > ema_fast.iloc[i] else 0.0
            else:
                s += 1.0 if (25 < rsi.iloc[i] < 50) else 0.0
                s += 1.0 if macd_hist < 0 else 0.0
                s += 1.0 if macd_hist < macd_prev else 0.0
                s += 1.0 if (vwap_valid and c < vwap.iloc[i]) else 0.0
                s += 1.0 if (strong_trend and minus_di.iloc[i] > plus_di.iloc[i]) else 0.0
                s += 1.5 if htf_bias == -1 else 0.0
                s += 0.5 if c < ema_fast.iloc[i] else 0.0
            s += 1.0 if vol_above else 0.0
            return s

        bull_score = score(True)
        bear_score = score(False)

        # high-vol regime filter
        atr_sma = ta.sma(atr, 42)
        vol_ratio = atr_v / float(atr_sma.iloc[i]) if atr_sma.iloc[i] else 1.0
        high_vol = vol_ratio > high_vol_thresh
        if skip_high_vol and high_vol:
            return None

        bull_cross = ema_fast.iloc[i] > ema_slow.iloc[i] and ema_fast.iloc[i - 1] <= ema_slow.iloc[i - 1]
        bear_cross = ema_fast.iloc[i] < ema_slow.iloc[i] and ema_fast.iloc[i - 1] >= ema_slow.iloc[i - 1]
        bull_mom = c > ema_fast.iloc[i] and c > ema_slow.iloc[i]
        bear_mom = c < ema_fast.iloc[i] and c < ema_slow.iloc[i]

        def passes_grade(s: float) -> bool:
            r = s / max_score if max_score else 0.0
            if grade_filter == "A+ Only":
                ok = r >= _GRADE_APLUS
            elif grade_filter == "A+ and A":
                ok = r >= _GRADE_A
            else:
                ok = True
            return ok and (r >= _GRADE_B if hide_c else True)

        def grade(s: float) -> str:
            r = s / max_score if max_score else 0.0
            return "A+" if r >= _GRADE_APLUS else "A" if r >= _GRADE_A else "B" if r >= _GRADE_B else "C"

        swing_low = df["low"].iloc[max(0, i - swing_lb): i + 1].min()
        swing_high = df["high"].iloc[max(0, i - swing_lb): i + 1].max()

        def calc_sl(is_long: bool) -> float:
            atr_sl = atr_v * sl_mult
            atr_stop = c - atr_sl if is_long else c + atr_sl
            struct = (swing_low - atr_v * 0.2) if is_long else (swing_high + atr_v * 0.2)
            stop = min(atr_stop, struct) if is_long else max(atr_stop, struct)
            max_dist = atr_sl * 1.5
            if abs(c - stop) > max_dist:
                stop = c - max_dist if is_long else c + max_dist
            min_dist = atr_v * 0.5
            if abs(c - stop) < min_dist:
                stop = c - min_dist if is_long else c + min_dist
            return stop

        if (bull_cross and bull_mom and rsi.iloc[i] < 75
                and bull_score >= eff_min and passes_grade(bull_score)):
            sl = calc_sl(True)
            risk = abs(c - sl)
            return Signal(1, float(c), float(sl), float(c + risk * tp1m), float(c + risk * tp2m),
                          float(c + risk * tp3m), grade(bull_score), bull_score,
                          f"EMA cross + score {bull_score:.1f}/{max_score:.0f}")

        if (bear_cross and bear_mom and rsi.iloc[i] > 25
                and bear_score >= eff_min and passes_grade(bear_score)):
            sl = calc_sl(False)
            risk = abs(c - sl)
            return Signal(-1, float(c), float(sl), float(c - risk * tp1m), float(c - risk * tp2m),
                          float(c - risk * tp3m), grade(bear_score), bear_score,
                          f"EMA cross + score {bear_score:.1f}/{max_score:.0f}")
        return None
