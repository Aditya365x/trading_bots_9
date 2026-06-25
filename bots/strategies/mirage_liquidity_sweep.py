"""
Mirage Liquidity Sweep Pro — port of MIrage_liquidity_sweer.pine
(WillyAlgoTrader). Visuals/dashboard/alerts dropped; the signal + risk engine
is reproduced faithfully.

Idea: liquidity rests above swing highs (BSL) and below swing lows (SSL). A
SWEEP grabs that liquidity then reverses:
  * bullish sweep = price trades BELOW an un-swept swing low but CLOSES back
    above it (low < lvl and close > lvl);
  * bearish sweep = price trades ABOVE an un-swept swing high but CLOSES back
    below it (high > lvl and close < lvl).
A sweep is scored 0–100 (wick depth, reclaim, close position, volume spike, HTF
bias). If `require_confirm` (default), the sweep is only a SETUP — the trade
fires when price then breaks the minor market structure (CHoCH): close above the
last minor swing high for longs / below the last minor swing low for shorts,
within a confirmation window. Otherwise it fires on the sweep bar.

Risk = the original's: SL beyond the sweep wick (±buffer·ATR, min 0.5·ATR),
TP1/TP2/TP3 as R-multiples by preset, break-even after TP1 (handled live by the
trade manager).
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from ..core import indicators as ta
from ..core.strategy_base import Signal, Strategy

_PRESETS = {                       # buffer×ATR, TP1, TP2, TP3 (in R)
    "Conservative": (0.50, 1.0, 2.0, 4.0),
    "Balanced":     (0.25, 1.0, 2.0, 3.0),
    "Aggressive":   (0.15, 1.5, 2.5, 4.0),
    "Scalping":     (0.10, 0.8, 1.5, 2.0),
}
_MAX_STORED = 25


class MirageLiquiditySweep(Strategy):
    name = "mirage_liquidity_sweep"

    def evaluate(self, df: pd.DataFrame) -> Optional[Signal]:
        swing_len = int(self.p("swing_len", 21))
        lookback = int(self.p("max_sweep_bars", 80))
        min_score = float(self.p("min_score", 50))
        require_confirm = bool(self.p("require_confirm", True))
        minor_len = int(self.p("minor_len", 8))
        confirm_window = int(self.p("confirm_window", 13))
        use_volume = bool(self.p("use_volume", True))
        vol_len = int(self.p("vol_len", 21))
        vol_mult = float(self.p("vol_mult", 1.5))
        use_htf = bool(self.p("use_htf", True))
        htf_ema_len = int(self.p("htf_ema_len", 200))     # HTF-bias proxy on this TF
        preset = str(self.p("risk_preset", "Balanced"))
        atr_len = int(self.p("atr_len", 14))
        eff_buf, eff_tp1, eff_tp2, eff_tp3 = _PRESETS.get(preset, _PRESETS["Balanced"])
        if preset == "Custom":
            eff_buf = float(self.p("sl_buffer_atr", 0.25))
            eff_tp1 = float(self.p("tp1", 1.0)); eff_tp2 = float(self.p("tp2", 2.0)); eff_tp3 = float(self.p("tp3", 3.0))

        n = len(df)
        warmup = max(swing_len * 2, 50)
        if n < warmup + 5:
            return None

        o = df["open"].to_numpy(float); h = df["high"].to_numpy(float)
        lo = df["low"].to_numpy(float); c = df["close"].to_numpy(float)
        vol = df["volume"].to_numpy(float)
        atr = ta.atr(df, atr_len).to_numpy(float)
        ema_htf = ta.ema(df["close"], htf_ema_len).to_numpy(float)
        vol_sma = ta.sma(df["volume"], vol_len).to_numpy(float)
        has_vol = bool((vol > 0).any())

        ph = ta.pivot_high(df["high"], swing_len, swing_len).to_numpy(float)
        pl = ta.pivot_low(df["low"], swing_len, swing_len).to_numpy(float)
        mph = ta.pivot_high(df["high"], minor_len, minor_len).to_numpy(float)
        mpl = ta.pivot_low(df["low"], minor_len, minor_len).to_numpy(float)

        hi_levels: list[list] = []   # [level, origin_bar, used]
        lo_levels: list[list] = []
        last_minor_high = np.nan
        last_minor_low = np.nan
        pending_dir = 0; pending_start = 0
        pending_lvl = np.nan; pending_wick = np.nan; pending_score = np.nan
        fire: Optional[tuple] = None

        def f_score(direction, lvl, t):
            rng = h[t] - lo[t]
            a = atr[t]
            if rng <= 0 or a <= 0 or np.isnan(lvl):
                return 0.0
            wick = (min(o[t], c[t]) - lo[t]) if direction == 1 else (h[t] - max(o[t], c[t]))
            reclaim = (c[t] - lvl) if direction == 1 else (lvl - c[t])
            close_pos = (c[t] - lo[t]) / rng
            cp = close_pos if direction == 1 else 1.0 - close_pos
            wick_c = min(max(wick, 0.0) / a, 1.0)
            rcl_c = min(max(reclaim, 0.0) / a, 1.0)
            if use_volume and has_vol and not np.isnan(vol_sma[t]) and vol_sma[t] > 0:
                vr = vol[t] / vol_sma[t]
                vol_c = min(max((vr - 1.0) / max(vol_mult - 1.0, 0.1), 0.0), 1.0)
            else:
                vol_c = 0.5
            if not use_htf:
                htf_c = 0.5
            elif np.isnan(ema_htf[t]):
                htf_c = 0.0
            else:
                bull = c[t] > ema_htf[t]
                htf_c = (1.0 if (bull if direction == 1 else not bull) else 0.0)
            return (wick_c * 0.30 + rcl_c * 0.25 + cp * 0.20 + vol_c * 0.15 + htf_c * 0.10) * 100.0

        for t in range(n):
            # major pivots confirm `swing_len` bars after they form
            pb = t - swing_len
            if pb >= 0:
                if not np.isnan(ph[pb]):
                    hi_levels.append([ph[pb], pb, False])
                    if len(hi_levels) > _MAX_STORED:
                        hi_levels.pop(0)
                if not np.isnan(pl[pb]):
                    lo_levels.append([pl[pb], pb, False])
                    if len(lo_levels) > _MAX_STORED:
                        lo_levels.pop(0)
            pbm = t - minor_len
            if pbm >= 0:
                if not np.isnan(mph[pbm]):
                    last_minor_high = mph[pbm]
                if not np.isnan(mpl[pbm]):
                    last_minor_low = mpl[pbm]

            bull_sweep = False; bull_lvl = np.nan
            bear_sweep = False; bear_lvl = np.nan
            for lv in reversed(lo_levels):
                if lv[2]:
                    continue
                if t - lv[1] <= lookback:
                    if c[t] < lv[0]:
                        lv[2] = True
                    elif lo[t] < lv[0] and c[t] > lv[0]:
                        lv[2] = True
                        if not bull_sweep:
                            bull_sweep, bull_lvl = True, lv[0]
            for lv in reversed(hi_levels):
                if lv[2]:
                    continue
                if t - lv[1] <= lookback:
                    if c[t] > lv[0]:
                        lv[2] = True
                    elif h[t] > lv[0] and c[t] < lv[0]:
                        lv[2] = True
                        if not bear_sweep:
                            bear_sweep, bear_lvl = True, lv[0]

            warmed = t >= warmup and atr[t] > 0
            bull_q = bull_sweep and warmed and f_score(1, bull_lvl, t) >= min_score
            bear_q = bear_sweep and warmed and f_score(-1, bear_lvl, t) >= min_score

            fire_bull = fire_bear = False
            if require_confirm:
                if pending_dir != 0 and (t - pending_start) > confirm_window:
                    pending_dir = 0
                if bull_q:
                    pending_dir, pending_start = 1, t
                    pending_lvl, pending_wick, pending_score = bull_lvl, lo[t], f_score(1, bull_lvl, t)
                if bear_q:
                    pending_dir, pending_start = -1, t
                    pending_lvl, pending_wick, pending_score = bear_lvl, h[t], f_score(-1, bear_lvl, t)
                if pending_dir == 1 and not np.isnan(last_minor_high) and c[t] > last_minor_high:
                    fire_bull = True
                    sig_score, wick = pending_score, pending_wick
                    pending_dir = 0
                elif pending_dir == -1 and not np.isnan(last_minor_low) and c[t] < last_minor_low:
                    fire_bear = True
                    sig_score, wick = pending_score, pending_wick
                    pending_dir = 0
            else:
                if bull_q:
                    fire_bull, sig_score, wick = True, f_score(1, bull_lvl, t), lo[t]
                if bear_q:
                    fire_bear, sig_score, wick = True, f_score(-1, bear_lvl, t), h[t]

            if t == n - 1 and (fire_bull != fire_bear):   # exclusive → no conflict
                fire = (1 if fire_bull else -1, sig_score, wick)

        if fire is None:
            return None
        direction, score, wick = fire
        entry = float(c[-1]); a = float(atr[-1])
        if direction == 1:
            raw_sl = wick - a * eff_buf
            dist = abs(entry - raw_sl)
            if dist < a * 0.5:
                raw_sl, dist = entry - a * 0.5, a * 0.5
        else:
            raw_sl = wick + a * eff_buf
            dist = abs(raw_sl - entry)
            if dist < a * 0.5:
                raw_sl, dist = entry + a * 0.5, a * 0.5
        tp1 = entry + direction * dist * eff_tp1
        tp2 = entry + direction * dist * eff_tp2
        tp3 = entry + direction * dist * eff_tp3
        grade = "A+" if score >= 75 else "A" if score >= 60 else "B" if score >= 50 else "C"
        return Signal(direction, entry, float(raw_sl), float(tp1), float(tp2), float(tp3),
                      grade, round(float(score), 1),
                      f"Liquidity sweep {'long' if direction == 1 else 'short'} "
                      f"score={score:.0f}{' +CHoCH' if require_confirm else ''}")
