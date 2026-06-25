"""
ML RSI — port of MLRSI.pine ("Machine Learning RSI | AI Classification &
Ranking", Zeiierman).

It is a k-nearest-neighbours *analog matching* engine over RSI-derived features:

  1. Each bar is fingerprinted by 8 RSI features (level, slope, acceleration,
     midpoint distance, percentile, volatility/"churn", fast/slow spread,
     regime).
  2. Every bar is banked with a forward OUTCOME label: how price moved over the
     next `horizon` bars, bucketed by an ATR threshold (±1σ, ±2σ → ±1/±2/±3).
     The label is only attached once it is known, so there is no look-ahead.
  3. For the current bar we find the k closest historical analogs by a weighted
     Lorentzian distance  Σ w·log(1+|Δfeature|)  and let them vote
     (distance-weighted) on direction. Feature weights are learned online with a
     Fisher discriminant (bull vs bear separation).
  4. The vote → engine bias, agreement fraction and "gap tightness", which feed
     a Rank and a Confidence score. A signal fires on a stance flip that clears
     the Rank + Confidence gates and the Trend / Volatility / Chop filters.
  5. An ML-adaptive Supertrend acts as the trend gate and the trailing stop; we
     use its line as the protective SL and place TPs at R-multiples.

All chart drawing from the original is dropped. The decision is taken on the
last CLOSED bar (the runner only passes closed candles).
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from ..core import indicators as ta
from ..core.strategy_base import Signal, Strategy


def _scale01(s: pd.Series, length: int) -> pd.Series:
    lo = s.rolling(length, min_periods=1).min()
    hi = s.rolling(length, min_periods=1).max()
    out = (s - lo) / (hi - lo)
    return out.where(hi != lo, 0.5)


def _classify(move: np.ndarray, band: np.ndarray) -> np.ndarray:
    out = np.zeros(len(move))
    out = np.where(move > 2 * band, 3.0, out)
    out = np.where((move > band) & (move <= 2 * band), 2.0, out)
    out = np.where((move > 0) & (move <= band), 1.0, out)
    out = np.where(move < -2 * band, -3.0, out)
    out = np.where((move < -band) & (move >= -2 * band), -2.0, out)
    out = np.where((move < 0) & (move >= -band), -1.0, out)
    return out


class MLRSI(Strategy):
    name = "mlrsi"

    def evaluate(self, df: pd.DataFrame) -> Optional[Signal]:
        # --- params (mirror the Pine inputs) ---
        rsi_base = int(self.p("rsi_base", 14))
        memory_depth = int(self.p("memory_depth", 500))
        k_neighbors = int(self.p("k_neighbors", 8))
        win_len = int(self.p("win_len", 100))
        spacing = int(self.p("spacing_bars", 4))
        horizon = int(self.p("horizon_bars", 4))
        step = int(self.p("step_len", 3))
        atr_factor = float(self.p("atr_factor", 0.5))
        auto_weights = bool(self.p("auto_weights", True))
        gate_rank = float(self.p("gate_rank", 60))
        gate_conf = float(self.p("gate_conf", 50))
        use_trend_gate = bool(self.p("use_trend_gate", True))
        use_vol_band = bool(self.p("use_vol_band", True))
        vol_lo = float(self.p("vol_band_lo", 20)); vol_hi = 85.0
        use_chop = bool(self.p("use_chop", True))
        cool_bars = int(self.p("cooldown_bars", 5))
        st_mult_base = float(self.p("st_mult", 1.5))
        st_ml_resp = float(self.p("st_ml_resp", 1.0))
        st_atr_len = int(self.p("st_atr_len", 10))
        smooth_len = int(self.p("smooth_len", 10))
        sl_fallback = float(self.p("sl_mult", 1.5))
        tp1m = float(self.p("tp1", 1.0)); tp2m = float(self.p("tp2", 2.0)); tp3m = float(self.p("tp3", 3.0))

        n = len(df)
        if n < max(win_len, rsi_base * 2, 60) + horizon + 5:
            return None

        src = df["close"]
        hl2 = (df["high"] + df["low"]) / 2.0

        # --- 8 RSI features ---
        rsi = ta.rsi(src, rsi_base)
        rsi_f = ta.rsi(src, max(2, round(rsi_base / 2)))
        rsi_s = ta.rsi(src, rsi_base * 2)
        f_value = (rsi / 100.0)
        f_slope = _scale01(rsi - rsi.shift(step), win_len)
        f_accel = _scale01((rsi - rsi.shift(step)) - (rsi.shift(step) - rsi.shift(2 * step)), win_len)
        f_mid = (rsi - 50.0).abs() / 50.0
        f_pct = ta.percent_rank(rsi, win_len) / 100.0
        f_churn = _scale01(rsi.rolling(14, min_periods=1).std(ddof=0), win_len)
        f_spread = _scale01(rsi_f - rsi_s, win_len)
        f_regime = _scale01(ta.ema(rsi, 20) - 50.0, win_len)
        Feat = np.column_stack([f_value, f_slope, f_accel, f_mid, f_pct,
                                f_churn, f_spread, f_regime]).astype(float)

        # --- forward outcome label for each source bar j ---
        atr14 = ta.atr(df, 14).to_numpy(float)
        s = src.to_numpy(float)
        outcome = np.full(n, np.nan)
        jmax = n - 1 - horizon
        if jmax >= 1:
            j = np.arange(0, jmax + 1)
            move = s[j + horizon] - s[j]
            band = atr_factor * atr14[j]
            band = np.where(band <= 0, np.nan, band)
            outcome[j] = _classify(move, band)

        # --- feature weights (Fisher discriminant over the labeled bank) ---
        weights = self._weights(Feat, outcome, auto_weights, jmax)
        W = weights
        Wsum = float(W.sum())

        # --- per-bar engine (vectorised k-NN inner loop) ---
        analog = np.zeros(n); bias = np.zeros(n, int)
        agree = np.zeros(n); tight = np.zeros(n); kcnt = np.zeros(n, int)
        for t in range(n):
            jhi = t - horizon
            if jhi < 1 or np.isnan(Feat[t]).any():
                continue
            jlo = max(0, jhi - memory_depth + 1)
            idxs = np.arange(jhi, jlo - 1, -spacing)
            F = Feat[idxs]
            ok = ~np.isnan(F).any(axis=1) & ~np.isnan(outcome[idxs])
            idxs = idxs[ok]; F = F[ok]
            if len(idxs) == 0:
                continue
            d = (W * np.log1p(np.abs(F - Feat[t]))).sum(axis=1)
            k = min(k_neighbors, len(d))
            ksel = np.argpartition(d, k - 1)[:k]
            gaps = d[ksel]; cls = outcome[idxs[ksel]]
            w = 1.0 / (1.0 + gaps)
            tot = w.sum()
            if tot <= 0:
                continue
            score = float((cls * w).sum())
            bull = float(w[cls > 0].sum()); bear = float(w[cls < 0].sum())
            a = score / tot
            analog[t] = a
            bias[t] = 1 if a > 0.15 else -1 if a < -0.15 else 0
            agree[t] = (bull if bias[t] == 1 else bear if bias[t] == -1 else 0.0) / tot
            avg_gap = float(gaps.mean())
            tight[t] = float(np.clip(1.0 - avg_gap / (Wsum * 0.45 + 1e-9), 0.0, 1.0))
            kcnt[t] = k

        # --- chop / regime context ---
        ema_trend = ta.ema(src, 50).to_numpy(float)
        ema_quick = ta.ema(src, 5).to_numpy(float)
        trend_force = np.where(atr14 > 0, np.abs(ema_quick - ema_trend) / np.where(atr14 > 0, atr14, np.nan), 0.0)
        chop_raw = trend_force < 0.5
        chop_now = chop_raw if use_chop else np.zeros(n, bool)
        atr_pct = ta.percent_rank(pd.Series(atr14, index=df.index), 100).to_numpy(float)
        vol_healthy = (atr_pct >= vol_lo) & (atr_pct <= vol_hi)

        # --- ML-adaptive Supertrend (recursive) ---
        conv_inst = np.clip(analog / 1.5, -1.0, 1.0)
        conv_sm = pd.Series(conv_inst, index=df.index).ewm(span=smooth_len, adjust=False).mean().to_numpy()
        ml_drive = np.clip(np.abs(conv_sm) * 0.5 + tight * 0.3 + agree * 0.2, 0.0, 1.0)
        ml_drive = np.where(chop_now, ml_drive * 0.35, ml_drive)
        adapt_mult = st_mult_base * (1.0 + st_ml_resp * (1.0 - ml_drive))
        st_atr = ta.atr(df, st_atr_len).to_numpy(float)
        st_src = hl2.to_numpy(float)
        up_band = st_src - adapt_mult * st_atr
        dn_band = st_src + adapt_mult * st_atr
        cl = df["close"].to_numpy(float)
        st_long = np.full(n, np.nan); st_short = np.full(n, np.nan); st_dir = np.ones(n, int)
        for t in range(n):
            if t == 0 or np.isnan(up_band[t]):
                st_long[t] = up_band[t]; st_short[t] = dn_band[t]; st_dir[t] = 1
                continue
            st_long[t] = max(up_band[t], st_long[t - 1]) if cl[t - 1] > st_long[t - 1] else up_band[t]
            st_short[t] = min(dn_band[t], st_short[t - 1]) if cl[t - 1] < st_short[t - 1] else dn_band[t]
            if st_dir[t - 1] == -1 and cl[t] > st_short[t - 1]:
                st_dir[t] = 1
            elif st_dir[t - 1] == 1 and cl[t] < st_long[t - 1]:
                st_dir[t] = -1
            else:
                st_dir[t] = st_dir[t - 1]
        st_line = np.where(st_dir == 1, st_long, st_short)

        # --- stance / rank / confidence / triggers across the series ---
        rsi_np = rsi.to_numpy(float)
        slope_up = rsi_np > np.concatenate([[np.nan] * step, rsi_np[:-step]])
        osc_reg = ta.ema(rsi, 20).to_numpy(float)
        ema_r5 = ta.ema(rsi, 5).to_numpy(float)
        osc_smooth_up = np.concatenate([[False], ema_r5[1:] > ema_r5[:-1]])

        stance = np.zeros(n, int); age = np.zeros(n, int)
        last_entry = -10**9; trig_long = trig_short = False
        prev_stance = 0; changed_hist: list[bool] = []
        for t in range(n):
            aligned = (bias[t] == 1 and st_dir[t] == 1) or (bias[t] == -1 and st_dir[t] == -1)
            gates = ((not use_trend_gate or aligned) and (not use_vol_band or vol_healthy[t])
                     and not chop_now[t])
            st_now = 1 if (bias[t] == 1 and gates) else -1 if (bias[t] == -1 and gates) else prev_stance
            stance[t] = st_now
            chg = st_now != prev_stance
            changed_hist.append(chg)
            age[t] = 0 if chg else (age[t - 1] + 1 if t > 0 else 0)
            early_flip = chg and any(changed_hist[-4:-1])

            stretched = (bias[t] == 1 and rsi_np[t] > 70) or (bias[t] == -1 and rsi_np[t] < 30)
            slope_fit = (bias[t] == 1 and slope_up[t]) or (bias[t] == -1 and not slope_up[t])
            rank = self._rank(bias[t], agree[t], tight[t], aligned, vol_healthy[t], atr_pct[t],
                              vol_lo, osc_reg[t], slope_fit, stretched, osc_smooth_up[t],
                              age[t], early_flip, chop_raw[t], kcnt[t], k_neighbors)
            conf = self._conf(bias[t], agree[t], tight[t], age[t], slope_fit, early_flip,
                              kcnt[t], k_neighbors)

            flip_long = st_now == 1 and prev_stance != 1
            flip_short = st_now == -1 and prev_stance != -1
            qualifies = rank >= gate_rank and conf >= gate_conf
            cool_ok = (t - last_entry) >= cool_bars
            tl = flip_long and qualifies and cool_ok
            ts = flip_short and qualifies and cool_ok
            if tl or ts:
                last_entry = t
            if t == n - 1:
                trig_long, trig_short = tl, ts
                last_rank, last_conf = rank, conf
            prev_stance = st_now

        if not (trig_long or trig_short):
            return None

        direction = 1 if trig_long else -1
        price = float(cl[-1])
        sl = float(st_line[-1])
        # use the Supertrend line as the stop if it sits on the correct side,
        # otherwise fall back to an ATR stop.
        risk = (price - sl) if direction == 1 else (sl - price)
        if not np.isfinite(risk) or risk <= 0:
            risk = float(atr14[-1]) * sl_fallback
            sl = price - direction * risk
        tps = (price + direction * risk * tp1m,
               price + direction * risk * tp2m,
               price + direction * risk * tp3m)
        grade = "A+" if last_rank >= 80 else "A" if last_rank >= 65 else "B" if last_rank >= 50 else "C"
        return Signal(direction, price, float(sl), float(tps[0]), float(tps[1]), float(tps[2]),
                      grade, round(float(last_rank), 1),
                      f"ML-RSI {'long' if direction == 1 else 'short'} "
                      f"rank={last_rank:.0f} conf={last_conf:.0f}")

    # ------------------------------------------------------------------ #
    def _weights(self, Feat: np.ndarray, outcome: np.ndarray, auto: bool, jmax: int) -> np.ndarray:
        manual = np.array([
            float(self.p("w_value", 1.0)), float(self.p("w_slope", 1.0)),
            float(self.p("w_accel", 1.0)), float(self.p("w_mid", 1.0)),
            float(self.p("w_pct", 1.0)), float(self.p("w_churn", 1.0)),
            float(self.p("w_spread", 1.0)), float(self.p("w_regime", 1.0))])
        if not auto or jmax < 60:
            return manual
        F = Feat[:jmax + 1]; o = outcome[:jmax + 1]
        bull = F[o > 0]; bear = F[o < 0]
        if len(bull) < 3 or len(bear) < 3:
            return manual
        mB, mBe = np.nanmean(bull, axis=0), np.nanmean(bear, axis=0)
        vB, vBe = np.nanvar(bull, axis=0), np.nanvar(bear, axis=0)
        fish = (mB - mBe) ** 2 / (vB + vBe + 1e-6)
        mx = np.nanmax(fish)
        norm = fish / mx if mx > 0 else np.ones(8)
        return np.maximum(0.5, norm * 10.0)

    def _rank(self, bias, agree, tight, aligned, vol_healthy, atr_pct, vol_lo, osc_reg,
              slope_fit, stretched, smooth_up, age, early_flip, chop_raw, k, kmax) -> float:
        if bias == 0:
            return 0.0
        p_agree = 25.0 * agree
        p_gap = 15.0 * tight
        p_struct = (10.0 if slope_fit else 0.0) + (0.0 if stretched else 5.0)
        p_trend = 10.0 if aligned else 0.0
        p_vol = 10.0 if vol_healthy else (5.0 if atr_pct < vol_lo else 3.0)
        reg_fit = (bias == 1 and osc_reg > 55) or (bias == -1 and osc_reg < 45)
        p_reg = 10.0 if reg_fit else (4.0 if 45 <= osc_reg <= 55 else 6.0)
        p_smooth = 5.0 if ((bias == 1 and smooth_up) or (bias == -1 and not smooth_up)) else 0.0
        p_hold = min(5.0, age)
        p_pen = min(20.0, (8.0 if chop_raw else 0.0) + (6.0 if stretched else 0.0)
                   + (6.0 if early_flip else 0.0) + (5.0 * (kmax - k) / kmax if k < kmax else 0.0))
        raw = p_agree + p_gap + p_struct + p_trend + p_vol + p_reg + p_smooth + p_hold - p_pen
        return float(np.clip(raw, 0.0, 100.0))

    def _conf(self, bias, agree, tight, age, slope_fit, early_flip, k, kmax) -> float:
        if bias == 0:
            return 0.0
        raw = (40.0 * agree + 25.0 * tight + 15.0 * min(1.0, age / 5.0)
               + 10.0 * (1.0 if slope_fit else 0.0) - (15.0 if early_flip else 0.0)
               - (10.0 * (kmax - k) / kmax if k < kmax else 0.0))
        return float(np.clip(raw, 0.0, 100.0))
