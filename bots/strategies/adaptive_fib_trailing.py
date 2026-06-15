"""
Adaptive Fibonacci Trailing — structure swings -> Fib 0.5 cross / trail flip.

Port of Adaptive_Fibbonachi_trailing.pine core (default 0.5 Cross mode):
  * Track the most recent opposing confirmed swings -> Fib range (top/bottom).
  * fib500 = midpoint. Long when close crosses ABOVE fib500 with confirmation
    (body ratio + penetration + regime/ADX by strictness); Short on cross below.
  * Optional confidence score (structure/regime/ADX/volume/volatility/SMA50);
    signals below min confidence are skipped.
  * Risk preset (default Balanced) -> SL ×ATR, TP1/TP2/TP3 ×Risk.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from ..core import indicators as ta
from ..core.strategy_base import Signal, Strategy, atr_sl_tp, resolve_preset


class AdaptiveFibTrailing(Strategy):
    name = "adaptive_fib_trailing"

    def evaluate(self, df: pd.DataFrame) -> Optional[Signal]:
        pivot_len = int(self.p("pivot_len", 13))
        strictness = self.p("cross_strictness", "Strict")     # Strict | Relaxed | None
        confirm_atr = float(self.p("confirm_filter", 0.2))
        body_min = float(self.p("body_filter", 0.5))
        adx_trend = float(self.p("adx_trend", 22.0))
        vol_mult = float(self.p("volatility_mult", 1.6))
        use_conf = bool(self.p("use_confidence", True))
        min_conf = float(self.p("min_confidence", 50.0))
        preset = self.p("risk_preset", "Balanced")
        custom = (float(self.p("sl_mult", 1.5)), float(self.p("tp1", 1.0)),
                  float(self.p("tp2", 2.0)), float(self.p("tp3", 3.0)))
        sl_m, tp1, tp2, tp3 = resolve_preset(preset, custom)

        if len(df) < max(pivot_len * 3, 60):
            return None

        close = df["close"]
        c = close.to_numpy()
        n = len(df)
        atr14 = ta.atr(df, 14).to_numpy()
        atr_avg = ta.sma(pd.Series(atr14, index=df.index), 50).to_numpy()
        sma50 = ta.sma(close, 50).to_numpy()
        _, _, adx = ta.dmi(df, 14, 14)
        adx_a = adx.to_numpy()
        ph = ta.pivot_high(df["high"], pivot_len, pivot_len)
        pl = ta.pivot_low(df["low"], pivot_len, pivot_len)

        # build last-confirmed swing high/low per bar (confirmed pivot_len bars later)
        last_hi = np.full(n, np.nan)
        last_lo = np.full(n, np.nan)
        rh = rl = np.nan
        for k in range(n):
            cb = k - pivot_len
            if cb >= 0 and not np.isnan(ph.iloc[cb]):
                rh = float(ph.iloc[cb])
            if cb >= 0 and not np.isnan(pl.iloc[cb]):
                rl = float(pl.iloc[cb])
            last_hi[k] = rh
            last_lo[k] = rl

        fib500 = np.where((~np.isnan(last_hi)) & (~np.isnan(last_lo)) & (last_hi > last_lo),
                          last_hi - (last_hi - last_lo) * 0.5, np.nan)

        # side per bar relative to fib500
        side = np.zeros(n, dtype=int)
        prev = 0
        for k in range(n):
            if np.isnan(fib500[k]):
                side[k] = prev
            else:
                prev = 1 if c[k] > fib500[k] else (-1 if c[k] < fib500[k] else prev)
                side[k] = prev

        i = n - 1
        if np.isnan(fib500[i]):
            return None
        crossed_up = side[i - 1] == -1 and side[i] == 1
        crossed_down = side[i - 1] == 1 and side[i] == -1
        if not (crossed_up or crossed_down):
            return None

        # regime (simplified, no hysteresis): volatile > trend > range
        vol_ratio = atr14[i] / atr_avg[i] if atr_avg[i] else 1.0
        if vol_ratio >= vol_mult:
            regime = "VOLATILE"
        elif adx_a[i] >= adx_trend:
            regime = "TREND"
        else:
            regime = "RANGE"

        body = abs(c[i] - df["open"].iloc[i])
        rng = df["high"].iloc[i] - df["low"].iloc[i]
        body_ratio = body / rng if rng > 0 else 0.0
        is_long = crossed_up
        penetration = (c[i] - fib500[i]) if is_long else (fib500[i] - c[i])

        def confirm() -> bool:
            if strictness == "None":
                return True
            body_ok = body_ratio >= body_min
            pen_ok = penetration > confirm_atr * atr14[i]
            if strictness == "Relaxed":
                return body_ok and pen_ok
            regime_ok = True if regime == "TREND" else (body_ok if regime == "VOLATILE" else adx_a[i] >= 18.0)
            return body_ok and pen_ok and regime_ok

        if not confirm():
            return None

        # confidence score (approximate, 0-100)
        if use_conf:
            rng_struct = (last_hi[i] - last_lo[i]) if (not np.isnan(last_hi[i]) and not np.isnan(last_lo[i])) else atr14[i]
            struct_score = min(max(rng_struct / (atr14[i] * 4.0), 0.0), 1.0) if atr14[i] else 0.0
            cross_q = 0.7 if strictness == "Strict" else 0.6 if strictness == "Relaxed" else 0.5
            regime_score = cross_q if regime != "VOLATILE" else 0.5
            adx_score = min(max(adx_a[i] / 40.0, 0.0), 1.0)
            vol_favor = 0.0 if (vol_ratio < 0.3 or vol_ratio > 2.5) else max(0.0, min(1.0, 1.0 - abs(vol_ratio - 1.0) / 1.5))
            conf_ref = fib500[i]
            dist = abs(conf_ref - sma50[i]) / atr14[i] if atr14[i] else 2.0
            conf_score = max(0.0, min(1.0, 1.0 - dist / 2.0))
            confidence = (struct_score * 0.20 + regime_score * 0.20 + adx_score * 0.15 +
                          0.5 * 0.15 + vol_favor * 0.15 + conf_score * 0.15) * 100.0
            if confidence < min_conf:
                return None
        else:
            confidence = 0.0

        grade = "A+" if confidence >= 90 else "A" if confidence >= 75 else "B" if confidence >= 60 else "C" if confidence >= 40 else "D"
        atr_v = float(atr14[i])
        if atr_v <= 0 or np.isnan(atr_v):
            return None
        d = 1 if is_long else -1
        sl, t1, t2, t3 = atr_sl_tp(d, float(c[i]), atr_v, sl_m, tp1, tp2, tp3)
        return Signal(d, float(c[i]), float(sl), float(t1), float(t2), float(t3),
                      grade, confidence, f"Fib 0.5 cross ({regime}, conf {confidence:.0f})")
