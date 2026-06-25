"""
Order Flow Sniper — complete institutional order-flow strategy implementing
every concept from the 14-episode `order_flow.md` course:

  * Delta & Cumulative Volume Delta (CVD) — real taker-buy from Binance klines.
  * Volume Profile — POC, Value Area (VAH/VAL), HVN/LVN, liquidity voids.
  * VWAP with standard-deviation bands (institutional fair value).
  * Absorption & Exhaustion divergences (CVD Effort vs. Result, Ep 13).
  * POC migration & CVD momentum for directional bias (Ep 7, Ep 13).
  * Market-state classifier — Balance vs. Imbalance (Ep 14).
  * Initiative candle trigger — closing-body confirmation (Ep 13, Ep 14).

Signal hierarchy: CONTEXT → STRUCTURE → TRIGGER, enforced in every setup.

  Setup A — Absorption Reversal (mean reversion at a value edge):
    Price at VAH/VAL/VWAP with CVD divergence + vol spike absorbed → fade.

  Setup B — CVD Exhaustion Reversal (Type-2 divergence, Ep 13):
    Price makes a new swing extreme but CVD participation fades → reversal
    confirmed by an initiative candle.

  Setup C — VWAP Band Mean Reversion (Ep 11):
    Price stretches to ±2σ VWAP bands, order flow confirms exhaustion → fade
    back to VWAP.

  Setup D — Void Fill Continuation (trend through an LVN):
    Stacked imbalance + CVD slope + retracement into a liquidity void →
    continuation in trend direction.

  Setup E — Initiative Breakout (Ep 12, Ep 13):
    CVD builds pre-breakout pressure, then an initiative bar rips through a
    key level (VAH/VAL, prior-day POC, VWAP).

  Setup F — HVN Bounce (high-volume node as support/resistance):
    Price tests a known high-volume price level with absorption confirmation.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from ..core import indicators as ta
from ..core.strategy_base import Signal, Strategy


class OrderFlowSniper(Strategy):
    name = "order_flow_sniper"

    def evaluate(self, df: pd.DataFrame) -> Optional[Signal]:
        # --- profile / context ---
        prof_win = int(self.p("profile_window", 120))
        n_bins = int(self.p("profile_bins", 60))
        va_pct = float(self.p("value_area_pct", 0.70))
        ctx_tol_atr = float(self.p("ctx_tol_atr", 0.5))
        # --- effort / delta ---
        vol_ma_len = int(self.p("vol_ma_len", 20))
        vol_spike_mult = float(self.p("vol_spike_mult", 1.5))
        delta_strong = float(self.p("delta_strong", 0.33))
        absorb_close_pct = float(self.p("absorb_close_pct", 0.25))
        # --- structure ---
        div_swing = int(self.p("div_swing", 3))
        div_lookback = int(self.p("div_lookback", 40))
        div_price_tol_atr = float(self.p("div_price_tol_atr", 0.1))
        stacked_n = int(self.p("stacked_n", 3))
        void_min_levels = int(self.p("void_min_levels", 3))
        void_frac = float(self.p("void_frac", 0.10))
        retr_win = int(self.p("retr_window", 8))
        # --- vwap bands ---
        vwap_band_sigma = float(self.p("vwap_band_sigma", 2.0))
        # --- CVD momentum ---
        cvd_mom_len = int(self.p("cvd_mom_len", 10))
        # --- initiative candle ---
        init_body_atr = float(self.p("init_body_atr", 0.3))
        init_delta_strong = float(self.p("init_delta_strong", 0.50))
        # --- HVN ---
        hvn_vol_mult = float(self.p("hvn_vol_mult", 1.5))
        # --- risk ---
        atr_len = int(self.p("atr_len", 14))
        sl_buf_atr = float(self.p("sl_buffer_atr", 0.2))
        min_rr = float(self.p("min_rr", 1.5))
        # --- session filter ---
        rth_only = bool(self.p("rth_only", False))

        n = len(df)
        if n < prof_win + vol_ma_len + 5:
            return None

        o = df["open"].to_numpy(float); h_col = df["high"].to_numpy(float)
        lo = df["low"].to_numpy(float); c = df["close"].to_numpy(float)
        v = df["volume"].fillna(0.0).to_numpy(float)
        i = n - 1

        atr_arr = ta.atr(df, atr_len).to_numpy(float)
        atr_v = atr_arr[i]
        if not np.isfinite(atr_v) or atr_v <= 0:
            return None

        if rth_only and isinstance(df.index, pd.DatetimeIndex):
            mins = df.index[i].hour * 60 + df.index[i].minute
            if not (13 * 60 + 30 <= mins <= 20 * 60):
                return None

        # ----- pre-signal filters (Fix 4: avoid tight ranges & low volume) -----
        rng_20 = float(np.max(h_col[max(0, i - 20): i + 1]) - np.min(lo[max(0, i - 20): i + 1]))
        if rng_20 < atr_v * 2.0:
            return None
        vol_20_avg = float(np.mean(v[max(0, i - 20): i + 1]))
        if v[i] < vol_20_avg * 0.4:
            return None

        # ---------------- order flow core ----------------
        delta, dimb, real_flow = _flow(df, h_col, lo, c, v)
        cvd = _session_cvd(df.index, delta)
        vol_ma = ta.sma(df["volume"].fillna(0.0), vol_ma_len).to_numpy(float)
        vwap = ta.vwap_session(df).to_numpy(float)

        # ---------------- volume profile ----------------
        prof = _volume_profile(h_col, lo, v, i - prof_win + 1, i, n_bins, va_pct, void_frac)
        if prof is None:
            return None
        poc, vah, val = prof["poc"], prof["vah"], prof["val"]

        void_prof = _volume_profile(h_col, lo, v, i - prof_win + 1, i - retr_win,
                                    n_bins, va_pct, void_frac)
        voids = void_prof["voids"] if void_prof is not None else []

        # ---------------- VWAP bands ----------------
        vwap_lo1, vwap_hi1, vwap_lo2, vwap_hi2 = _vwap_bands(
            df, vwap, df.index, i, vwap_band_sigma)

        # ---------------- POC migration & market state ----------------
        poc_dir, poc_slope = _poc_migration(
            h_col, lo, v, i - prof_win + 1, i, n_bins, va_pct, void_frac, 6)
        cvd_mom = _cvd_momentum(cvd, i, cvd_mom_len)
        state = _market_context(c[i], vah, val)
        tag = "real" if real_flow else "proxy"

        # ---------------- signal priority ----------------
        # `enabled_setups` (config) gates which setups may fire — default all six
        # (A=Absorption, B=Exhaustion, C=VWAP-band, D=Void-fill, E=Initiative,
        #  F=HVN). Backtest showed C (VWAP reversion) is a heavy bleeder in crypto,
        # so configs can drop it without touching code.
        enabled = set(self.p("enabled_setups", ["A", "B", "C", "D", "E", "F"]))

        # Setup A: Absorption Reversal (at VAH/VAL with divergence)
        if "A" in enabled:
            sig = self._absorption_reversal(
                i, o, h_col, lo, c, v, dimb, delta, cvd, vol_ma, vwap, atr_v,
                poc, vah, val, ctx_tol_atr, vol_spike_mult, delta_strong,
                absorb_close_pct, div_swing, div_lookback, div_price_tol_atr,
                sl_buf_atr, min_rr, real_flow, poc_dir)
            if sig is not None and self._validate_signal(sig, min_rr):
                return sig

        # Setup B: Exhaustion Reversal (CVD fading at swing extreme)
        if "B" in enabled:
            sig = self._exhaustion_reversal(
                i, o, h_col, lo, c, v, dimb, delta, cvd, atr_v,
                div_swing, div_lookback, div_price_tol_atr, sl_buf_atr,
                min_rr, vwap, real_flow, init_body_atr, init_delta_strong)
            if sig is not None and self._validate_signal(sig, min_rr):
                return sig

        # Setup D: Void Fill Continuation (trend through LVN)
        if "D" in enabled:
            sig = self._void_fill_continuation(
                i, h_col, lo, c, v, dimb, delta, cvd, vol_ma, atr_arr, voids,
                stacked_n, void_min_levels, delta_strong, sl_buf_atr, min_rr,
                vwap, retr_win, df.index, real_flow, cvd_mom)
            if sig is not None and self._validate_signal(sig, min_rr):
                return sig

        # Setup E: Initiative Breakout (CVD pre-breakout pressure)
        if "E" in enabled:
            sig = self._initiative_breakout(
                i, o, h_col, lo, c, v, dimb, delta, cvd, vol_ma, atr_v,
                vwap, poc, vah, val, init_body_atr, init_delta_strong,
                ctx_tol_atr, sl_buf_atr, min_rr, real_flow, cvd_mom)
            if sig is not None and self._validate_signal(sig, min_rr):
                return sig

        # Setup C: VWAP Band Mean Reversion (±2σ bands)
        if "C" in enabled:
            sig = self._vwap_band_reversion(
                i, o, h_col, lo, c, v, dimb, delta, cvd, vol_ma, vwap,
                vwap_lo2, vwap_hi2, vwap_lo1, vwap_hi1, atr_v, vol_spike_mult,
                delta_strong, absorb_close_pct, sl_buf_atr, min_rr, real_flow)
            if sig is not None and self._validate_signal(sig, min_rr):
                return sig

        # Setup F: HVN Bounce (high-volume node support/resistance)
        if "F" in enabled:
            sig = self._hvn_bounce(
                i, o, h_col, lo, c, v, dimb, delta, cvd, vol_ma, atr_v,
                prof, hvn_vol_mult, delta_strong, absorb_close_pct,
                sl_buf_atr, min_rr, real_flow, ctx_tol_atr)
            if sig is not None and self._validate_signal(sig, min_rr):
                return sig

        return None

    # ------------------------------------------------------------------ #
    #  Setup A — Absorption Reversal
    # ------------------------------------------------------------------ #
    def _validate_signal(self, sig, min_rr):
        """Validate signal against fee-adjusted RR requirement."""
        FEE_RATE = 0.0008
        entry_cost = abs(sig.entry) * FEE_RATE
        if sig.direction == 1:
            net_rr = ((sig.tp1 - sig.entry) - entry_cost * 2) / ((sig.entry - sig.sl) + entry_cost)
        else:
            net_rr = ((sig.entry - sig.tp1) - entry_cost * 2) / ((sig.sl - sig.entry) + entry_cost)
        if net_rr < min_rr * 0.7:
            return False
        return True
    def _absorption_reversal(self, i, o, h, lo, c, v, dimb, delta, cvd, vol_ma,
                             vwap, atr_v, poc, vah, val, ctx_tol_atr,
                             vol_spike_mult, delta_strong, absorb_close_pct,
                             div_swing, div_lookback, div_price_tol_atr,
                             sl_buf_atr, min_rr, real_flow, poc_dir):
        rng = h[i] - lo[i]
        if rng <= 0 or vol_ma[i] <= 0:
            return None

        vol_spike = v[i] >= vol_spike_mult * vol_ma[i]
        if not vol_spike:
            return None

        tol = ctx_tol_atr * atr_v
        ptol = div_price_tol_atr * atr_v
        tag = "real" if real_flow else "proxy"

        # ----- SHORT: absorption at VAH -----
        at_vah = abs(h[i] - vah) <= tol or (lo[i] <= vah <= h[i])
        bull_effort = dimb[i] >= delta_strong and delta[i] > 0
        absorbed_top = (h[i] - c[i]) / rng >= (1.0 - absorb_close_pct)
        bear_div = _absorption_divergence(h, lo, cvd, i, div_swing, div_lookback, -1, ptol)
        # enhanced: avoid trading against POC migration direction
        poc_aligned = poc_dir <= 0  # POC flat/falling supports short
        if at_vah and bull_effort and absorbed_top and bear_div and poc_aligned:
            entry = c[i]
            sl = h[i] + sl_buf_atr * atr_v
            risk = sl - entry
            if risk <= 0:
                return None
            tp1 = entry - 0.5 * rng
            tp2 = min(vwap[i], poc) if np.isfinite(vwap[i]) else poc
            tp2 = min(tp2, entry - risk * min_rr)
            tp3 = min(val, tp2 - risk)
            tp1, tp2, tp3 = _order_targets(-1, entry, tp1, tp2, tp3)
            if (entry - tp2) / risk < min_rr:
                return None
            score = _context_score(poc_dir, cvd, i, cvd_mom_len=10,
                                   at_vah=True, bull_effort=True, div_confirmed=True,
                                   poc_aligned=poc_aligned)
            grade = _grade(score)
            return Signal(-1, float(entry), float(sl), float(tp1), float(tp2), float(tp3),
                          grade, round(score, 1),
                          f"Absorption reversal @VAH score={score:.0f} vol={v[i]/vol_ma[i]:.1f}x [{tag}]")

        # ----- LONG: absorption at VAL -----
        at_val = abs(lo[i] - val) <= tol or (lo[i] <= val <= h[i])
        bear_effort = dimb[i] <= -delta_strong and delta[i] < 0
        absorbed_bot = (c[i] - lo[i]) / rng >= (1.0 - absorb_close_pct)
        bull_div = _absorption_divergence(h, lo, cvd, i, div_swing, div_lookback, +1, ptol)
        poc_aligned = poc_dir >= 0  # POC flat/rising supports long
        if at_val and bear_effort and absorbed_bot and bull_div and poc_aligned:
            entry = c[i]
            sl = lo[i] - sl_buf_atr * atr_v
            risk = entry - sl
            if risk <= 0:
                return None
            tp1 = entry + 0.5 * rng
            tp2 = max(vwap[i], poc) if np.isfinite(vwap[i]) else poc
            tp2 = max(tp2, entry + risk * min_rr)
            tp3 = max(vah, tp2 + risk)
            tp1, tp2, tp3 = _order_targets(+1, entry, tp1, tp2, tp3)
            if (tp2 - entry) / risk < min_rr:
                return None
            score = _context_score(poc_dir, cvd, i, cvd_mom_len=10,
                                   at_vah=False, bull_effort=False, div_confirmed=True,
                                   poc_aligned=poc_aligned)
            grade = _grade(score)
            return Signal(+1, float(entry), float(sl), float(tp1), float(tp2), float(tp3),
                          grade, round(score, 1),
                          f"Absorption reversal @VAL score={score:.0f} vol={v[i]/vol_ma[i]:.1f}x [{tag}]")
        return None

    # ------------------------------------------------------------------ #
    #  Setup B — CVD Exhaustion Reversal (Type-2 divergence, Ep 13)
    # ------------------------------------------------------------------ #
    def _exhaustion_reversal(self, i, o, h, lo, c, v, dimb, delta, cvd, atr_v,
                             div_swing, div_lookback, div_price_tol_atr, sl_buf_atr,
                             min_rr, vwap, real_flow, init_body_atr, init_delta_strong):
        """
        Price makes a new swing extreme but CVD fails to confirm (exhaustion).
        REQUIRES an initiative candle closing in the reversal direction.
        """
        ptol = div_price_tol_atr * atr_v
        tag = "real" if real_flow else "proxy"

        # ----- BULLISH exhaustion: price makes lower low, CVD makes higher low -----
        bear_exhaustion = _exhaustion_divergence(h, lo, cvd, i, div_swing, div_lookback, -1, ptol)
        if bear_exhaustion:
            cvd_at_div = cvd[i]
            init = _initiative_bar_check(o, h, lo, c, v, dimb, i, +1, atr_v,
                                         init_body_atr, init_delta_strong)
            if init:
                entry = c[i]
                sl = max(lo[max(0, i - div_lookback): i].min() - sl_buf_atr * atr_v,
                         entry - 2.0 * atr_v)
                risk = entry - sl
                if risk <= 0:
                    return None
                tp1 = entry + risk * min_rr
                tp2 = max(entry + risk * 2.0, vwap[i]) if np.isfinite(vwap[i]) else tp1 + risk
                tp3 = tp2 + risk
                tp1, tp2, tp3 = _order_targets(+1, entry, tp1, tp2, tp3)
                if (tp1 - entry) / risk < min_rr:
                    return None
                score = _context_score(0, cvd, i, 10, False, False, True, True,
                                       cvd_snapshot=cvd_at_div)
                grade = _grade(score)
                return Signal(+1, float(entry), float(sl), float(tp1), float(tp2), float(tp3),
                              grade, round(score, 1),
                              f"Exhaustion reversal (price LL / CVD HL) + init candle [{tag}]")

        # ----- BEARISH exhaustion: price makes higher high, CVD makes lower high -----
        bull_exhaustion = _exhaustion_divergence(h, lo, cvd, i, div_swing, div_lookback, +1, ptol)
        if bull_exhaustion:
            cvd_at_div = cvd[i]
            init = _initiative_bar_check(o, h, lo, c, v, dimb, i, -1, atr_v,
                                         init_body_atr, init_delta_strong)
            if init:
                entry = c[i]
                sl = min(h[max(0, i - div_lookback): i].max() + sl_buf_atr * atr_v,
                         entry + 2.0 * atr_v)
                risk = sl - entry
                if risk <= 0:
                    return None
                tp1 = entry - risk * min_rr
                tp2 = min(entry - risk * 2.0, vwap[i]) if np.isfinite(vwap[i]) else tp1 - risk
                tp3 = tp2 - risk
                tp1, tp2, tp3 = _order_targets(-1, entry, tp1, tp2, tp3)
                if (entry - tp1) / risk < min_rr:
                    return None
                score = _context_score(0, cvd, i, 10, False, False, True, True,
                                       cvd_snapshot=cvd_at_div)
                grade = _grade(score)
                return Signal(-1, float(entry), float(sl), float(tp1), float(tp2), float(tp3),
                              grade, round(score, 1),
                              f"Exhaustion reversal (price HH / CVD LH) + init candle [{tag}]")
        return None

    # ------------------------------------------------------------------ #
    #  Setup C — VWAP Band Mean Reversion (Ep 11)
    # ------------------------------------------------------------------ #
    def _vwap_band_reversion(self, i, o, h, lo, c, v, dimb, delta, cvd, vol_ma, vwap,
                             lo2, hi2, lo1, hi1, atr_v, vol_spike_mult, delta_strong,
                             absorb_close_pct, sl_buf_atr, min_rr, real_flow):
        """
        Price at extreme VWAP band (±2σ), order flow confirms exhaustion → fade
        back to VWAP or ±1σ band.
        """
        if not (np.isfinite(lo2) and np.isfinite(hi2)):
            return None
        rng = h[i] - lo[i]
        if rng <= 0 or vol_ma[i] <= 0:
            return None
        tag = "real" if real_flow else "proxy"

        # ----- SHORT: price at upper 2sigma band, absorption/overbought -----
        near_hi2 = c[i] >= hi2 or (lo[i] <= hi2 <= h[i])
        if near_hi2:
            eff = dimb[i] >= delta_strong and delta[i] > 0
            absorbed = (h[i] - c[i]) / rng >= (1.0 - absorb_close_pct)
            vol_ok = v[i] >= vol_spike_mult * vol_ma[i]
            if vol_ok and eff and absorbed:
                entry = c[i]
                sl = h[i] + sl_buf_atr * atr_v
                risk = sl - entry
                if risk <= 0:
                    return None
                tp1 = max(hi1, vwap[i]) if np.isfinite(hi1) else vwap[i]
                tp1 = entry - max(entry - tp1, risk * min_rr)
                tp2 = max(vwap[i], entry - risk * 2.0) if np.isfinite(vwap[i]) else entry - risk * 2.0
                tp2 = min(tp2, tp1)
                tp3 = tp2 - risk
                tp1, tp2, tp3 = _order_targets(-1, entry, tp1, tp2, tp3)
                if (entry - tp1) / risk < min_rr:
                    return None
                return Signal(-1, float(entry), float(sl), float(tp1), float(tp2), float(tp3),
                              "A", round((entry - tp1) / risk, 2),
                              f"VWAP band reversion @+2std (to VWAP) [{tag}]")

        # ----- LONG: price at lower 2sigma band, oversold / absorption -----
        near_lo2 = c[i] <= lo2 or (lo[i] <= lo2 <= h[i])
        if near_lo2:
            eff = dimb[i] <= -delta_strong and delta[i] < 0
            absorbed = (c[i] - lo[i]) / rng >= (1.0 - absorb_close_pct)
            vol_ok = v[i] >= vol_spike_mult * vol_ma[i]
            if vol_ok and eff and absorbed:
                entry = c[i]
                sl = lo[i] - sl_buf_atr * atr_v
                risk = entry - sl
                if risk <= 0:
                    return None
                tp1 = min(lo1, vwap[i]) if np.isfinite(lo1) else vwap[i]
                tp1 = entry + max(tp1 - entry, risk * min_rr)
                tp2 = min(vwap[i], entry + risk * 2.0) if np.isfinite(vwap[i]) else entry + risk * 2.0
                tp2 = max(tp2, tp1)
                tp3 = tp2 + risk
                tp1, tp2, tp3 = _order_targets(+1, entry, tp1, tp2, tp3)
                if (tp1 - entry) / risk < min_rr:
                    return None
                return Signal(+1, float(entry), float(sl), float(tp1), float(tp2), float(tp3),
                              "A", round((tp1 - entry) / risk, 2),
                              f"VWAP band reversion @-2std (to VWAP) [{tag}]")
        return None

    # ------------------------------------------------------------------ #
    #  Setup D — Void Fill Continuation
    # ------------------------------------------------------------------ #
    def _void_fill_continuation(self, i, h, lo, c, v, dimb, delta, cvd, vol_ma,
                                atr_arr, voids, stacked_n, void_min_levels,
                                delta_strong, sl_buf_atr, min_rr,
                                vwap, retr_win, index, real_flow, cvd_mom):
        if not voids:
            return None
        tag = "real" if real_flow else "proxy"

        if i - stacked_n + 1 < 0:
            return None
        recent = dimb[i - stacked_n + 1: i + 1]
        stacked_up = bool(np.all(recent >= delta_strong))
        stacked_dn = bool(np.all(recent <= -delta_strong))
        if not (stacked_up or stacked_dn):
            return None

        cvd_slope = cvd[i] - cvd[max(0, i - stacked_n)]

        rwin = slice(max(0, i - retr_win), i + 1)
        retr_low = float(np.min(lo[rwin]))
        retr_high = float(np.max(h[rwin]))
        sl_atr = float(np.max(atr_arr[rwin])) if retr_win > 0 and np.all(np.isfinite(atr_arr[rwin])) else atr_arr[i]

        if isinstance(index, pd.DatetimeIndex):
            same_day = index.normalize() == index.normalize()[i]
            sess_hi = float(np.max(h[same_day])); sess_lo = float(np.min(lo[same_day]))
        else:
            sess_hi = float(np.max(h[max(0, i - 96):i + 1]))
            sess_lo = float(np.min(lo[max(0, i - 96):i + 1]))

        # ----- LONG continuation -----
        if stacked_up and cvd_slope > 0 and c[i] > vwap[i]:
            # enhanced: CVD momentum must be positive (CVD still building, not fading)
            if cvd_mom <= 0:
                return None
            zone = _void_touched(voids, retr_low, c[i], void_min_levels, below=True)
            if zone is not None:
                zlo, zhi = zone
                entry = c[i]
                sl = retr_low - sl_buf_atr * sl_atr
                risk = entry - sl
                if risk > 0:
                    tp1 = max(retr_high, entry + risk * min_rr)
                    tp2 = max(sess_hi, tp1 + risk)
                    tp3 = tp2 + risk
                    tp1, tp2, tp3 = _order_targets(+1, entry, tp1, tp2, tp3)
                    if (tp1 - entry) / risk >= min_rr:
                        return Signal(+1, float(entry), float(sl), float(tp1), float(tp2),
                                      float(tp3), "A", round((tp1 - entry) / risk, 2),
                                      f"Void-fill continuation (bid stack, CVD up) [{tag}]")

        # ----- SHORT continuation -----
        if stacked_dn and cvd_slope < 0 and c[i] < vwap[i]:
            if cvd_mom >= 0:
                return None
            zone = _void_touched(voids, retr_high, c[i], void_min_levels, below=False)
            if zone is not None:
                zlo, zhi = zone
                entry = c[i]
                sl = retr_high + sl_buf_atr * sl_atr
                risk = sl - entry
                if risk > 0:
                    tp1 = min(retr_low, entry - risk * min_rr)
                    tp2 = min(sess_lo, tp1 - risk)
                    tp3 = tp2 - risk
                    tp1, tp2, tp3 = _order_targets(-1, entry, tp1, tp2, tp3)
                    if (entry - tp1) / risk >= min_rr:
                        return Signal(-1, float(entry), float(sl), float(tp1), float(tp2),
                                      float(tp3), "A", round((entry - tp1) / risk, 2),
                                      f"Void-fill continuation (ask stack, CVD down) [{tag}]")
        return None

    # ------------------------------------------------------------------ #
    #  Setup E — Initiative Breakout (Ep 12, Ep 13)
    # ------------------------------------------------------------------ #
    def _initiative_breakout(self, i, o, h, lo, c, v, dimb, delta, cvd, vol_ma, atr_v,
                             vwap, poc, vah, val, init_body_atr, init_delta_strong,
                             ctx_tol_atr, sl_buf_atr, min_rr, real_flow, cvd_mom):
        """
        CVD builds pre-breakout pressure (CVD diverging while price consolidates),
        then an initiative bar rips through a key level.
        """
        tol = ctx_tol_atr * atr_v
        tag = "real" if real_flow else "proxy"

        init_up = _initiative_bar_check(o, h, lo, c, v, dimb, i, +1, atr_v,
                                        init_body_atr, init_delta_strong)
        init_dn = _initiative_bar_check(o, h, lo, c, v, dimb, i, -1, atr_v,
                                        init_body_atr, init_delta_strong)

        # ----- BULLISH breakout through VAH or POC -----
        if init_up and cvd_mom > 0:
            broke_level = (c[i] > vah + tol) or (c[i - 1] <= poc and c[i] > poc + tol)
            vol_ok = v[i] >= v[max(0, i - 20): i + 1].mean() * 1.2
            if broke_level and vol_ok:
                entry = c[i]
                sl = lo[i] - sl_buf_atr * atr_v
                risk = entry - sl
                if risk <= 0:
                    return None
                tp1 = entry + risk * min_rr
                tp2 = entry + risk * 2.0
                tp3 = tp2 + risk
                tp1, tp2, tp3 = _order_targets(+1, entry, tp1, tp2, tp3)
                if (tp1 - entry) / risk < min_rr:
                    return None
                return Signal(+1, float(entry), float(sl), float(tp1), float(tp2), float(tp3),
                              "B", round((tp1 - entry) / risk, 2),
                              f"Initiative breakout long (dimb={dimb[i]:.2f}, CVD up) [{tag}]")

        # ----- BEARISH breakout through VAL or POC -----
        if init_dn and cvd_mom < 0:
            broke_level = (c[i] < val - tol) or (c[i - 1] >= poc and c[i] < poc - tol)
            vol_ok = v[i] >= v[max(0, i - 20): i + 1].mean() * 1.2
            if broke_level and vol_ok:
                entry = c[i]
                sl = h[i] + sl_buf_atr * atr_v
                risk = sl - entry
                if risk <= 0:
                    return None
                tp1 = entry - risk * min_rr
                tp2 = entry - risk * 2.0
                tp3 = tp2 - risk
                tp1, tp2, tp3 = _order_targets(-1, entry, tp1, tp2, tp3)
                if (entry - tp1) / risk < min_rr:
                    return None
                return Signal(-1, float(entry), float(sl), float(tp1), float(tp2), float(tp3),
                              "B", round((entry - tp1) / risk, 2),
                              f"Initiative breakout short (dimb={dimb[i]:.2f}, CVD down) [{tag}]")
        return None

    # ------------------------------------------------------------------ #
    #  Setup F — HVN Bounce (high-volume node as support/resistance)
    # ------------------------------------------------------------------ #
    def _hvn_bounce(self, i, o, h, lo, c, v, dimb, delta, cvd, vol_ma, atr_v,
                    prof, hvn_vol_mult, delta_strong, absorb_close_pct,
                    sl_buf_atr, min_rr, real_flow, ctx_tol_atr):
        """
        Price tests a known high-volume node and shows absorption/response,
        implying the level is holding as institutional support/resistance.
        """
        tag = "real" if real_flow else "proxy"
        bins = prof.get("bins", None)
        if bins is None:
            # _volume_profile must have been enhanced to return bins
            return None
        hist = bins.get("hist", None)
        edges = bins.get("edges", None)
        if hist is None or edges is None:
            return None

        total = hist.sum()
        if total <= 0:
            return None
        mean_bin = total / len(hist)
        # find HVNs: bins with volume significantly above average
        hvn_mask = hist >= hvn_vol_mult * mean_bin
        if not hvn_mask.any():
            return None

        tol = ctx_tol_atr * atr_v
        rng = h[i] - lo[i]
        if rng <= 0 or vol_ma[i] <= 0:
            return None
        half_rng = rng / 2.0

        # Find nearest HVN below/above price
        bin_w = (edges[-1] - edges[0]) / len(hist)
        hvn_levels = []
        for k in range(len(hist)):
            if hvn_mask[k]:
                hvn_levels.append((edges[k] + edges[k + 1]) / 2.0)

        if not hvn_levels:
            return None

        # ----- LONG: price bounces off HVN support (below price) -----
        hvns_below = [lv for lv in hvn_levels if lv < c[i]]
        if hvns_below:
            nearest_hvn = max(hvns_below)
            at_hvn = abs(lo[i] - nearest_hvn) <= tol * 2 or (lo[i] <= nearest_hvn <= h[i])
            # absorption confirmation: sellers hit the level, price returns
            sell_effort = dimb[i] <= -delta_strong * 0.5
            bounce = (c[i] - lo[i]) / rng >= 0.5  # close in upper half
            vol_ok = v[i] >= vol_ma[i]
            delta_flip = dimb[i] >= 0 and delta[i] > 0
            if at_hvn and bounce and (sell_effort or delta_flip) and vol_ok:
                entry = c[i]
                sl = nearest_hvn - sl_buf_atr * atr_v
                risk = entry - sl
                if risk <= 0:
                    return None
                tp1 = entry + risk * min_rr
                tp2 = max(entry + risk * 2.0, nearest_hvn + risk * 3.0)
                tp3 = tp2 + risk
                tp1, tp2, tp3 = _order_targets(+1, entry, tp1, tp2, tp3)
                if (tp1 - entry) / risk < min_rr:
                    return None
                return Signal(+1, float(entry), float(sl), float(tp1), float(tp2), float(tp3),
                              "B", round((tp1 - entry) / risk, 2),
                              f"HVN bounce long @{nearest_hvn:.4f} [{tag}]")

        # ----- SHORT: price rejects off HVN resistance (above price) -----
        hvns_above = [lv for lv in hvn_levels if lv > c[i]]
        if hvns_above:
            nearest_hvn = min(hvns_above)
            at_hvn = abs(h[i] - nearest_hvn) <= tol * 2 or (lo[i] <= nearest_hvn <= h[i])
            buy_effort = dimb[i] >= delta_strong * 0.5
            reject = (h[i] - c[i]) / rng >= 0.5  # close in lower half
            vol_ok = v[i] >= vol_ma[i]
            delta_flip = dimb[i] <= 0 and delta[i] < 0
            if at_hvn and reject and (buy_effort or delta_flip) and vol_ok:
                entry = c[i]
                sl = nearest_hvn + sl_buf_atr * atr_v
                risk = sl - entry
                if risk <= 0:
                    return None
                tp1 = entry - risk * min_rr
                tp2 = min(entry - risk * 2.0, nearest_hvn - risk * 3.0)
                tp3 = tp2 - risk
                tp1, tp2, tp3 = _order_targets(-1, entry, tp1, tp2, tp3)
                if (entry - tp1) / risk < min_rr:
                    return None
                return Signal(-1, float(entry), float(sl), float(tp1), float(tp2), float(tp3),
                              "B", round((entry - tp1) / risk, 2),
                              f"HVN reject short @{nearest_hvn:.4f} [{tag}]")
        return None


# ============================================================================ #
#  Order-flow helpers (module-level, pure numpy)
# ============================================================================ #
def _clv(h, lo, c) -> np.ndarray:
    """Close-location-value ∈ [-1, 1]: +1 = closed at high, -1 = closed at low."""
    rng = h - lo
    with np.errstate(divide="ignore", invalid="ignore"):
        clv = ((c - lo) - (h - c)) / rng
    return np.where(rng > 0, clv, 0.0)


def _flow(df, h, lo, c, v):
    """Per-bar delta + delta-imbalance. Real taker-buy when available, else CLV proxy."""
    proxy = v * _clv(h, lo, c)
    delta = proxy.copy()
    real_flow = False
    if "taker_buy_volume" in df.columns:
        tb = df["taker_buy_volume"].to_numpy(float)
        real = 2.0 * tb - v
        mask = np.isfinite(tb)
        delta[mask] = real[mask]
        real_flow = bool(mask[-1])
    with np.errstate(divide="ignore", invalid="ignore"):
        dimb = np.where(v > 0, delta / v, 0.0)
    return delta, dimb, real_flow


def _session_cvd(index, delta) -> np.ndarray:
    """Cumulative volume delta, reset each UTC day."""
    s = pd.Series(delta, index=index)
    if isinstance(index, pd.DatetimeIndex):
        return s.groupby(index.normalize()).cumsum().to_numpy(float)
    return s.cumsum().to_numpy(float)


def _volume_profile(h, lo, v, a, b, n_bins, va_pct, void_frac):
    """
    Histogram of volume across price over bars [a, b].
    Returns POC, VAH, VAL, voids, and raw bin data (edges + hist).
    """
    a = max(0, a)
    pmin = float(np.min(lo[a:b + 1])); pmax = float(np.max(h[a:b + 1]))
    if pmax <= pmin:
        return None
    edges = np.linspace(pmin, pmax, n_bins + 1)
    bin_w = (pmax - pmin) / n_bins
    hist = np.zeros(n_bins)
    for k in range(a, b + 1):
        if v[k] <= 0:
            continue
        b_lo = min(n_bins - 1, max(0, int((lo[k] - pmin) / bin_w)))
        b_hi = min(n_bins - 1, max(0, int((h[k] - pmin) / bin_w)))
        span = max(1, b_hi - b_lo + 1)
        hist[b_lo:b_hi + 1] += v[k] / span

    total = hist.sum()
    if total <= 0:
        return None
    poc_bin = int(np.argmax(hist))
    poc = (edges[poc_bin] + edges[poc_bin + 1]) / 2.0

    lo_b = hi_b = poc_bin
    acc = hist[poc_bin]
    target = va_pct * total
    while acc < target and (lo_b > 0 or hi_b < n_bins - 1):
        below = hist[lo_b - 1] if lo_b > 0 else -1.0
        above = hist[hi_b + 1] if hi_b < n_bins - 1 else -1.0
        if above >= below:
            hi_b += 1; acc += max(above, 0.0)
        else:
            lo_b -= 1; acc += max(below, 0.0)
    val = edges[lo_b]
    vah = edges[hi_b + 1]

    mean_bin = total / n_bins
    empty = hist < void_frac * mean_bin
    voids = []
    run_start = None
    for k in range(n_bins):
        if empty[k]:
            if run_start is None:
                run_start = k
        else:
            if run_start is not None:
                voids.append((edges[run_start], edges[k], k - run_start))
                run_start = None
    if run_start is not None:
        voids.append((edges[run_start], edges[n_bins], n_bins - run_start))

    return {"poc": poc, "vah": vah, "val": val, "voids": voids,
            "bins": {"edges": edges, "hist": hist}}


def _absorption_divergence(h, lo, cvd, i, swing, lookback, direction, tol) -> bool:
    """
    Type-1 CVD divergence (Absorption): CVD pushes to new extreme but price fails.
    direction=-1 → bearish (CVD higher high, price capped).
    direction=+1 → bullish (CVD lower low, price held).
    """
    a = max(0, i - lookback)
    b = i - swing
    if b <= a:
        return False
    if direction < 0:
        prior_high = float(np.max(h[a:b + 1]))
        prior_cvd_high = float(np.max(cvd[a:b + 1]))
        return bool(cvd[i] >= prior_cvd_high and h[i] <= prior_high + tol)
    else:
        prior_low = float(np.min(lo[a:b + 1]))
        prior_cvd_low = float(np.min(cvd[a:b + 1]))
        return bool(cvd[i] <= prior_cvd_low and lo[i] >= prior_low - tol)


def _exhaustion_divergence(h, lo, cvd, i, swing, lookback, direction, tol) -> bool:
    """
    Type-2 CVD divergence (Exhaustion / Lack of Participation, Ep 13):
    Price continues to a new extreme but CVD participation fades.

    direction=-1 (bearish exhaustion → bullish reversal):
        Price makes LOWER LOW but CVD makes HIGHER LOW.
    direction=+1 (bullish exhaustion → bearish reversal):
        Price makes HIGHER HIGH but CVD makes LOWER HIGH.
    """
    a = max(0, i - lookback)
    b = i - swing
    if b <= a:
        return False
    if direction < 0:
        # Price: lower low vs prior swing
        prior_low = float(np.min(lo[a:b + 1]))
        price_ll = lo[i] < prior_low - tol
        # CVD: higher low (lack of selling participation)
        prior_cvd_low = float(np.min(cvd[a:b + 1]))
        cvd_hl = cvd[i] > prior_cvd_low + tol * 0.01
        return bool(price_ll and cvd_hl)
    else:
        # Price: higher high vs prior swing
        prior_high = float(np.max(h[a:b + 1]))
        price_hh = h[i] > prior_high + tol
        # CVD: lower high (lack of buying participation)
        prior_cvd_high = float(np.max(cvd[a:b + 1]))
        cvd_lh = cvd[i] < prior_cvd_high - tol * 0.01
        return bool(price_hh and cvd_lh)


def _initiative_bar_check(o, h, lo, c, v, dimb, i, direction, atr_v,
                          body_atr_thresh, delta_thresh):
    """
    Check if bar[i] is an initiative (trigger/confirmation) candle.

    direction=+1 (bullish initiative):
      - Strong positive delta exceeding threshold
      - Body (close-open) ≥ body_atr_thresh × ATR
      - Close in upper third of its range
      - Volume above zero

    direction=-1 (bearish initiative):
      - Strong negative delta exceeding threshold
      - Body ≥ body_atr_thresh × ATR
      - Close in lower third of its range
    """
    body = abs(c[i] - o[i])
    if body < body_atr_thresh * atr_v or v[i] <= 0:
        return False
    rng = h[i] - lo[i]
    if rng <= 0:
        return False
    if direction == 1:
        return bool(c[i] > o[i] and dimb[i] >= delta_thresh and
                    (c[i] - lo[i]) / rng >= 0.67)
    else:
        return bool(c[i] < o[i] and dimb[i] <= -delta_thresh and
                    (h[i] - c[i]) / rng >= 0.67)


def _vwap_bands(df, vwap_series, index, i, num_std):
    """
    VWAP with rolling standard-deviation bands (Ep 11).
    Returns (lo_1std, hi_1std, lo_2std, hi_2std) at bar i.
    Uses cumulative variance of (hlc3 - vwap) per session, volume-weighted.
    """
    hlc3 = (df["high"] + df["low"] + df["close"]) / 3.0
    vwap_arr = vwap_series
    dev = hlc3.to_numpy(float) - vwap_arr
    v = df["volume"].fillna(0.0).to_numpy(float)

    if isinstance(index, pd.DatetimeIndex):
        day = index.normalize()
        sq_dev = pd.Series(dev ** 2, index=index)
        vol_s = pd.Series(v, index=index)
        cum_sq = sq_dev.groupby(day).cumsum()
        cum_vol = vol_s.groupby(day).cumsum().replace(0.0, np.nan)
        cum_sq = (cum_sq / cum_vol).fillna(0.0).to_numpy(float)
        counts = vol_s.groupby(day).cumcount().to_numpy(float) + 1.0
    else:
        cum_v = np.cumsum(v)
        cum_sq = np.where(cum_v > 0, np.cumsum(dev ** 2) / cum_v, 0.0)
        counts = np.arange(1, len(dev) + 1, dtype=float)

    std = np.sqrt(np.maximum(cum_sq, 0.0))
    std = np.where(counts > 1, std, 0.0)

    vw = vwap_arr[i]
    s = std[i] if i < len(std) else 0.0
    s1 = s * 1.0
    s2 = s * num_std
    return vw - s1, vw + s1, vw - s2, vw + s2


def _poc_migration(h, lo, v, a, b, n_bins, va_pct, void_frac, segments):
    """
    Track POC direction over the profile window split into `segments`.
    Returns (direction, slope):
      direction: +1 POC rising, -1 POC falling, 0 flat/unclear
      slope: average POC change per segment as fraction of profile range
    """
    n = b - a + 1
    if n < segments * 4:
        return 0, 0.0
    pocs = []
    seg_size = max(1, n // segments)
    for s in range(segments):
        s_a = a + s * seg_size
        s_b = min(b, s_a + seg_size - 1)
        if s_b <= s_a:
            continue
        prof = _volume_profile(h, lo, v, s_a, s_b, max(8, n_bins // segments),
                               va_pct, void_frac)
        if prof is not None:
            pocs.append(prof["poc"])
    if len(pocs) < 3:
        return 0, 0.0
    pmin = float(min(pocs)); pmax = float(max(pocs))
    prange = pmax - pmin
    if prange <= 0:
        return 0, 0.0
    changes = np.diff(pocs)
    net = pocs[-1] - pocs[0]
    slope = net / (prange * (len(pocs) - 1))
    if len(changes) >= 2 and np.all(np.array(changes) >= 0):
        direction = 1
    elif len(changes) >= 2 and np.all(np.array(changes) <= 0):
        direction = -1
    else:
        direction = 1 if net > prange * 0.05 else (-1 if net < -prange * 0.05 else 0)
    return direction, slope


def _cvd_momentum(cvd, i, period):
    """Rate of change of CVD over `period` bars (fraction of total CVD range)."""
    if i < period:
        return 0.0
    cvd_range = np.max(cvd[max(0, i - 60): i + 1]) - np.min(cvd[max(0, i - 60): i + 1])
    if cvd_range <= 0:
        return 0.0
    return (cvd[i] - cvd[i - period]) / cvd_range


def _market_context(price, vah, val):
    """
    Classify market state (Ep 14, Step 1):
      'balance' — price inside the Value Area → reversion setups
      'trend'   — price outside the Value Area → trend/continuation setups
    Returns 'balance' or 'trend'.
    """
    if val <= price <= vah:
        return "balance"
    return "trend"


def _context_score(poc_dir, cvd, i, cvd_mom_len, at_vah, bull_effort,
                   div_confirmed, poc_aligned, cvd_snapshot=None):
    """Composite context score 0-100 based on confluence of order-flow factors."""
    score = 20.0  # baseline
    if poc_aligned:
        score += 15.0
    if div_confirmed:
        score += 20.0
    cvd_mom = _cvd_momentum(cvd, i, cvd_mom_len)
    if abs(cvd_mom) > 0.05:
        score += 10.0
    cvd_ma = np.mean(cvd[max(0, i - 30): i + 1])
    cvd_ref = cvd_snapshot if cvd_snapshot is not None else cvd[i]
    if not bull_effort and cvd_ref > cvd_ma:
        score += 10.0
    if bull_effort and cvd_ref < cvd_ma:
        score += 10.0
    cvd_short_trend = cvd[i] - cvd[max(0, i - 3)]
    if (not bull_effort and cvd_short_trend > 0) or (bull_effort and cvd_short_trend < 0):
        score += 15.0
    score += max(0, min(10, abs(cvd_mom) * 100))
    return min(100.0, score)


def _grade(score):
    if score >= 80:
        return "A+"
    if score >= 65:
        return "A"
    if score >= 50:
        return "B"
    return "C"


def _void_touched(voids, retr_extreme, price, min_levels, below):
    """Find nearest qualifying liquidity void touched by the retracement."""
    best = None
    for (zlo, zhi, width) in voids:
        if width < min_levels:
            continue
        if not (zlo <= retr_extreme <= zhi):
            continue
        if below:
            if best is None or zhi > best[1]:
                best = (zlo, zhi)
        else:
            if best is None or zlo < best[0]:
                best = (zlo, zhi)
    return best


def _order_targets(direction, entry, t1, t2, t3):
    """Force targets monotonically away from entry in the trade direction."""
    if direction == 1:
        t1 = max(t1, entry)
        t2 = max(t2, t1)
        t3 = max(t3, t2)
    else:
        t1 = min(t1, entry)
        t2 = min(t2, t1)
        t3 = min(t3, t2)
    return t1, t2, t3
