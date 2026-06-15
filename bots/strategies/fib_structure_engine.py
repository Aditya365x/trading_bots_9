"""
Fibonacci Structure Engine — multi-trigger SMC entries with cooldown.

Port of fibonachiStructureEngine.pine signal logic:
  * ATR-filtered swings; EQH/EQL detection; liquidity sweeps (wick through a
    level + close back inside); BOS/CHoCH; Fib direction + premium/discount;
    confluence weight; engulfing-in-zone patterns.
  * BUY  triggers (any): bullish CHoCH, OR bullish sweep in discount/strong
    confluence, OR bullish engulfing in discount with confluence + bull bias.
    SELL = mirror. A shared cooldown prevents signal clustering; same-bar
    conflicting buy+sell cancel.
  * Risk: SL = ATR×sl_mult, TP1/TP2/TP3 = R-multiples (standardised ladder;
    the original used a single 2.5×ATR TP).
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from ..core import indicators as ta
from ..core.strategy_base import Signal, Strategy, atr_sl_tp


class FibStructureEngine(Strategy):
    name = "fib_structure_engine"

    def evaluate(self, df: pd.DataFrame) -> Optional[Signal]:
        swing_len = int(self.p("swing_len", 10))
        atr_filter = bool(self.p("atr_filter", True))
        atr_mult = float(self.p("atr_mult", 0.5))
        cooldown = int(self.p("cooldown", 5))
        eq_tol_m = float(self.p("eq_tolerance", 0.1))
        conf_tol_m = float(self.p("confluence_tolerance", 0.3))
        strict_engulf = bool(self.p("strict_engulf", True))
        sl_mult = float(self.p("sl_mult", 1.5))
        tp1m = float(self.p("tp1", 1.0)); tp2m = float(self.p("tp2", 2.0)); tp3m = float(self.p("tp3", 3.0))

        warmup = max(swing_len * 3, 50)
        if len(df) < warmup + 5:
            return None

        atr = ta.atr(df, 14).to_numpy()
        ph = ta.pivot_high(df["high"], swing_len, swing_len)
        pl = ta.pivot_low(df["low"], swing_len, swing_len)
        o = df["open"].to_numpy(); h = df["high"].to_numpy()
        lo = df["low"].to_numpy(); c = df["close"].to_numpy()
        body = np.abs(c - o)
        body_ema = ta.ema(pd.Series(body, index=df.index), 14).to_numpy()
        n = len(df)
        i = n - 1

        sw_h1 = sw_h2 = np.nan
        sw_l1 = sw_l2 = np.nan
        struct_bias = 0
        last_choch_dir = 0
        last_broken_hi = last_broken_lo = -1
        last_swept_hi = last_swept_lo = -1
        eqh_active = eql_active = False
        eqh_price = eql_price = np.nan
        bars_since = 999
        result_dir = 0
        result_reason = ""

        for k in range(n):
            atr_v = atr[k] if not np.isnan(atr[k]) else 0.0
            atr_min = atr_v * atr_mult if atr_filter else 0.0
            eq_tol = atr_v * eq_tol_m
            conf_tol = atr_v * conf_tol_m if atr_v > 0 else 1e-9
            warmed = k >= warmup
            bars_since += 1

            new_h = new_l = False
            cb = k - swing_len
            if cb >= 0 and not np.isnan(ph.iloc[cb]):
                if np.isnan(sw_l1) or (ph.iloc[cb] - sw_l1) >= atr_min:
                    sw_h2 = sw_h1; sw_h1 = float(ph.iloc[cb]); new_h = True
            if cb >= 0 and not np.isnan(pl.iloc[cb]):
                if np.isnan(sw_h1) or (sw_h1 - pl.iloc[cb]) >= atr_min:
                    sw_l2 = sw_l1; sw_l1 = float(pl.iloc[cb]); new_l = True

            # EQH / EQL
            if new_h and not np.isnan(sw_h2) and abs(sw_h1 - sw_h2) <= eq_tol and warmed:
                eqh_active = True; eqh_price = (sw_h1 + sw_h2) / 2.0
            if new_l and not np.isnan(sw_l2) and abs(sw_l1 - sw_l2) <= eq_tol and warmed:
                eql_active = True; eql_price = (sw_l1 + sw_l2) / 2.0

            # sweeps
            sweep_high = sweep_low = False
            if warmed:
                ref_hi = eqh_price if eqh_active else sw_h1
                ref_lo = eql_price if eql_active else sw_l1
                cb_hi = (k - swing_len) if not eqh_active else -2
                cb_lo = (k - swing_len) if not eql_active else -3
                if not np.isnan(ref_hi) and cb_hi != last_swept_hi and h[k] > ref_hi and c[k] < ref_hi and o[k] < ref_hi:
                    sweep_high = True; last_swept_hi = cb_hi
                    if eqh_active:
                        eqh_active = False
                if not np.isnan(ref_lo) and cb_lo != last_swept_lo and lo[k] < ref_lo and c[k] > ref_lo and o[k] > ref_lo:
                    sweep_low = True; last_swept_lo = cb_lo
                    if eql_active:
                        eql_active = False

            # BOS / CHoCH
            is_choch = is_bull_break = is_bear_break = False
            bull_cond = (not np.isnan(sw_h1)) and warmed and c[k] > sw_h1 and (k - swing_len) != last_broken_hi
            bear_cond = (not np.isnan(sw_l1)) and warmed and c[k] < sw_l1 and (k - swing_len) != last_broken_lo
            if bull_cond and bear_cond:
                if struct_bias <= 0:
                    bear_cond = False
                else:
                    bull_cond = False
            if bull_cond:
                if struct_bias <= 0:
                    is_choch = True; last_choch_dir = 1
                    last_swept_hi = last_swept_lo = -1
                struct_bias = 1; is_bull_break = True; last_broken_hi = k - swing_len
            if bear_cond:
                if struct_bias >= 0:
                    is_choch = True; last_choch_dir = -1
                    last_swept_hi = last_swept_lo = -1
                struct_bias = -1; is_bear_break = True; last_broken_lo = k - swing_len

            # fib direction + premium/discount (approx from current swing range)
            fib500 = np.nan
            if not np.isnan(sw_h1) and not np.isnan(sw_l1) and sw_h1 > sw_l1 and last_choch_dir != 0:
                fib500 = sw_h1 - (sw_h1 - sw_l1) * 0.5
            in_premium = in_discount = False
            if not np.isnan(fib500):
                if last_choch_dir == 1:
                    in_premium = c[k] > fib500; in_discount = c[k] <= fib500
                else:
                    in_premium = c[k] < fib500; in_discount = c[k] >= fib500

            # confluence weight
            cw = 0.0
            for lvl, w in ((sw_h1, 1.0), (sw_l1, 1.0)):
                if not np.isnan(lvl) and (abs(c[k] - lvl) <= conf_tol or (lo[k] <= lvl + conf_tol and h[k] >= lvl - conf_tol)):
                    cw += w
            if not np.isnan(fib500) and abs(c[k] - fib500) <= conf_tol:
                cw += 2.0
            if sweep_high or sweep_low:
                cw += 2.0

            # engulfing
            is_long_body = body[k] > body_ema[k]
            small_prev = (body[k - 1] if k > 0 else 0.0) < (body_ema[k - 1] if k > 0 else body_ema[k])
            bigger = body[k] > (body[k - 1] if k > 0 else 0.0)
            bear_eng = bull_eng = False
            if k > 0:
                if strict_engulf:
                    bear_eng = c[k] < o[k] and is_long_body and bigger and c[k-1] > o[k-1] and small_prev and o[k] > c[k-1] and c[k] < o[k-1]
                    bull_eng = c[k] > o[k] and is_long_body and bigger and c[k-1] < o[k-1] and small_prev and o[k] < c[k-1] and c[k] > o[k-1]
                else:
                    bear_eng = c[k] < o[k] and is_long_body and bigger and c[k-1] > o[k-1] and small_prev and c[k] <= o[k-1] and o[k] >= c[k-1]
                    bull_eng = c[k] > o[k] and is_long_body and bigger and c[k-1] < o[k-1] and small_prev and c[k] >= o[k-1] and o[k] <= c[k-1]
            bear_eng_ctx = bear_eng and warmed and (in_premium or cw >= 1.5)
            bull_eng_ctx = bull_eng and warmed and (in_discount or cw >= 1.5)

            # triggers
            buy_eng = bull_eng_ctx and struct_bias == 1 and cw >= 1.5
            buy_choch = is_choch and is_bull_break
            buy_sweep = sweep_low and warmed and (in_discount or cw >= 2.0)
            sell_eng = bear_eng_ctx and struct_bias == -1 and cw >= 1.5
            sell_choch = is_choch and is_bear_break
            sell_sweep = sweep_high and warmed and (in_premium or cw >= 2.0)

            buy_raw = buy_eng or buy_choch or buy_sweep
            sell_raw = sell_eng or sell_choch or sell_sweep
            if buy_raw and sell_raw:
                buy_raw = sell_raw = False

            conf_buy = buy_raw and bars_since >= cooldown and warmed
            conf_sell = sell_raw and bars_since >= cooldown and warmed
            if conf_buy or conf_sell:
                bars_since = 0
                if k == i:
                    if conf_buy:
                        parts = [t for t, on in (("choch", buy_choch), ("sweep", buy_sweep), ("engulf", buy_eng)) if on]
                        result_dir = 1; result_reason = "+".join(parts) or "buy"
                    else:
                        parts = [t for t, on in (("choch", sell_choch), ("sweep", sell_sweep), ("engulf", sell_eng)) if on]
                        result_dir = -1; result_reason = "+".join(parts) or "sell"

        if result_dir == 0:
            return None
        atr_v = float(atr[i])
        if atr_v <= 0 or np.isnan(atr_v):
            return None
        c0 = float(c[i])
        sl, t1, t2, t3 = atr_sl_tp(result_dir, c0, atr_v, sl_mult, tp1m, tp2m, tp3m)
        return Signal(result_dir, c0, sl, t1, t2, t3, "A", 0.0, f"FibStruct [{result_reason}]")
