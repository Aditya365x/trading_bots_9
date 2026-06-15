"""
Synapse Trail Pro — ratcheting EMA±ATR SuperTrend-style trail; signal on flip.

Port of Synapse_Trail_pro.pine core:
  * trailCenter = EMA(close, trailLen); bands = center ± ATR × effMult.
  * Optional adaptive multiplier (ATR percentile) and ratchet (bands only
    tighten toward the position until a flip).
  * dir flips long when close clears the previous upper band, short below lower.
  * Signal on dir flip, optionally filtered by a 0-100 Quality Score
    (HTF/volume/RSI/regime/breakout) and choppy-regime skip.
  * Risk preset (default Balanced) -> SL ×ATR, TP1/TP2/TP3 ×Risk.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from ..core import indicators as ta
from ..core.strategy_base import Signal, Strategy, atr_sl_tp, resolve_preset


class SynapseTrailPro(Strategy):
    name = "synapse_trail_pro"

    def evaluate(self, df: pd.DataFrame) -> Optional[Signal]:
        atr_len = int(self.p("atr_len", 13))
        base_mult = float(self.p("base_mult", 1.618))
        trail_len = int(self.p("trail_len", 21))
        use_adaptive = bool(self.p("adaptive_mult", False))
        ratchet = bool(self.p("ratchet", True))
        min_quality = float(self.p("min_quality", 0))
        skip_choppy = bool(self.p("skip_choppy", False))
        preset = self.p("risk_preset", "Balanced")
        custom = (float(self.p("sl_mult", 1.5)), float(self.p("tp1", 1.0)),
                  float(self.p("tp2", 2.0)), float(self.p("tp3", 3.0)))
        sl_m, tp1, tp2, tp3 = resolve_preset(preset, custom)

        warmup = max(atr_len, trail_len, 50) + 5
        if len(df) < warmup:
            return None

        close = df["close"]
        atr = ta.atr(df, atr_len).to_numpy()
        center = ta.ema(close, trail_len).to_numpy()
        c = close.to_numpy()
        n = len(df)

        if use_adaptive:
            pr = ta.percent_rank(pd.Series(atr, index=df.index), 100).to_numpy()
            adj = np.where(pr < 30, 0.8, np.where(pr > 70, 1.25, 1.0))
        else:
            adj = np.ones(n)
        eff = base_mult * adj

        upper = np.full(n, np.nan)
        lower = np.full(n, np.nan)
        dir_ = np.zeros(n, dtype=int)
        for k in range(n):
            raw_u = center[k] + atr[k] * eff[k]
            raw_l = center[k] - atr[k] * eff[k]
            prev_d = dir_[k - 1] if k > 0 else 0
            d = prev_d
            pu = upper[k - 1] if k > 0 and not np.isnan(upper[k - 1]) else c[k]
            pl = lower[k - 1] if k > 0 and not np.isnan(lower[k - 1]) else c[k]
            if c[k] > pu:
                d = 1
            elif c[k] < pl:
                d = -1
            flipped = d != prev_d
            if ratchet:
                if d == 1:
                    lower[k] = raw_l if flipped else max(raw_l, pl)
                    upper[k] = raw_u
                elif d == -1:
                    upper[k] = raw_u if flipped else min(raw_u, pu)
                    lower[k] = raw_l
                else:
                    upper[k], lower[k] = raw_u, raw_l
            else:
                upper[k], lower[k] = raw_u, raw_l
            dir_[k] = d

        i = n - 1
        raw_buy = dir_[i] == 1 and dir_[i - 1] == -1
        raw_sell = dir_[i] == -1 and dir_[i - 1] == 1
        if not (raw_buy or raw_sell):
            return None

        # --- regime + quality score ---
        _, _, adx = ta.dmi(df, 14, 14)
        adx_score = min(float(adx.iloc[i]) / 50.0 * 100.0, 100.0)
        chop_len = 14
        hi = ta.highest(df["high"], chop_len).iloc[i]
        lo = ta.lowest(df["low"], chop_len).iloc[i]
        tr_sum = ta.true_range(df).rolling(chop_len).sum().iloc[i]
        rng = hi - lo
        if rng <= 0:
            chop_raw = 100.0
        elif tr_sum > 0:
            chop_raw = 100.0 * np.log10(tr_sum / rng) / np.log10(max(chop_len, 2))
        else:
            chop_raw = 50.0
        chop_score = max(0.0, min(100.0, 100.0 - chop_raw))
        bar_idx = pd.Series(np.arange(n), index=df.index, dtype=float)
        corr = ta.correlation(close, bar_idx, 50).iloc[i]
        r2_score = (0.0 if pd.isna(corr) else corr ** 2) * 100.0
        regime_score = adx_score * 0.40 + chop_score * 0.35 + r2_score * 0.25
        is_choppy = regime_score < 35

        rsi = ta.rsi(close, 14).iloc[i]
        atr_v = float(atr[i])
        break_dist = (c[i] - (upper[i - 1] if not np.isnan(upper[i - 1]) else c[i])) if raw_buy \
            else ((lower[i - 1] if not np.isnan(lower[i - 1]) else c[i]) - c[i])
        break_strength = min(abs(break_dist) / atr_v, 3.0) / 3.0 * 100.0 if atr_v else 0.0

        is_buy = raw_buy
        rsi_part = 20.0 if ((is_buy and rsi > 50) or (not is_buy and rsi < 50)) else 0.0
        quality = 15.0 + 20.0 + rsi_part + regime_score * 0.20 + break_strength * 0.10
        grade = "A" if quality >= 75 else "B" if quality >= 55 else "C"

        if quality < min_quality:
            return None
        if skip_choppy and is_choppy:
            return None
        if atr_v <= 0 or np.isnan(atr_v):
            return None

        d = 1 if raw_buy else -1
        sl, t1, t2, t3 = atr_sl_tp(d, float(c[i]), atr_v, sl_m, tp1, tp2, tp3)
        reason = f"Trail flip ({'choppy ' if is_choppy else ''}q={quality:.0f})"
        return Signal(d, float(c[i]), float(sl), float(t1), float(t2), float(t3), grade, quality, reason)
