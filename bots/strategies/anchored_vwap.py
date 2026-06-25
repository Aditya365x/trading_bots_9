"""
Anchored VWAP + Market Structure Break — port of the "Nvidia Anchored VWAP
Strategy" playbook, adapted to single-symbol crypto futures.

Original idea (equities):
  * Anchor VWAPs at meaningful points (earnings, major swing H/L, breakouts).
    An AVWAP anchored at a swing LOW behaves as dynamic SUPPORT; one anchored at
    a swing HIGH behaves as RESISTANCE.
  * Entry = price RETESTS an AVWAP, then a Market Structure Break (MSB = close
    past the recent leg's wick H/L → reversal) on the execution timeframe.
    Enter market; SL at the retest H/L (conservative) or most-recent H/L
    (aggressive); TP 1.5R or trail to HOD/LOD / opposing AVWAP.
  * A+ filters: skip when AVWAPs are bunched together; no entry if a fresh
    HOD/LOD just printed (don't chase an extended move).

Crypto adaptation (no earnings, 24/7):
  * Anchor the SUPPORT AVWAP at the most recent confirmed MAJOR swing low and
    the RESISTANCE AVWAP at the most recent confirmed MAJOR swing high.
  * "30m to anchor, 2m to execute" collapses to one timeframe: MAJOR pivots
    (large left/right) set the anchors; MINOR pivots (small left/right) define
    the MSB. Run the bot on a fast TF (2m–5m) for the original feel.

Long setup : price retests the support AVWAP from above, holds, then a BULLISH
             MSB (close breaks the last minor swing high) fires.  Mirror short.
SL is structural (retest low / minor swing low); TP1=1.5R by default.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from ..core import indicators as ta
from ..core.strategy_base import Signal, Strategy


class AnchoredVWAP(Strategy):
    name = "anchored_vwap"

    def evaluate(self, df: pd.DataFrame) -> Optional[Signal]:
        major_lb = int(self.p("major_pivot", 20))     # "major" swing H/L anchors
        minor_lb = int(self.p("minor_pivot", 3))      # MSB leg pivots (fast TF)
        retest_win = int(self.p("retest_window", 12)) # bars the retest may sit in
        retest_tol = float(self.p("retest_tol_atr", 0.25))   # touch tolerance ×ATR
        max_age = int(self.p("max_anchor_age", 300))  # ignore stale anchors
        min_sep = float(self.p("min_avwap_sep_atr", 0.5))    # avoid bunched AVWAPs
        extend_lb = int(self.p("extend_lookback", 3)) # "no chasing a fresh HOD/LOD"
        sl_mode = str(self.p("sl_mode", "conservative"))     # conservative|aggressive
        atr_len = int(self.p("atr_len", 14))
        tp1m = float(self.p("tp1", 1.5)); tp2m = float(self.p("tp2", 2.5)); tp3m = float(self.p("tp3", 4.0))
        ny_only = bool(self.p("ny_session_only", False))

        n = len(df)
        if n < major_lb * 2 + retest_win + 5:
            return None

        high = df["high"].to_numpy(float); low = df["low"].to_numpy(float)
        close = df["close"].to_numpy(float)
        atr = ta.atr(df, atr_len).to_numpy(float)
        atr_v = atr[-1]
        if not np.isfinite(atr_v) or atr_v <= 0:
            return None

        # --- anchored VWAP via cumulative sums (efficient) ---
        tp = (df["high"] + df["low"] + df["close"]) / 3.0
        vol = df["volume"].fillna(0.0)
        cpv = (tp * vol).cumsum().to_numpy(float)
        cv = vol.cumsum().to_numpy(float)

        def avwap_at(anchor: int, t: int) -> float:
            base_pv = cpv[anchor - 1] if anchor > 0 else 0.0
            base_v = cv[anchor - 1] if anchor > 0 else 0.0
            dv = cv[t] - base_v
            return (cpv[t] - base_pv) / dv if dv > 0 else np.nan

        # --- anchors: most recent confirmed MAJOR swing low / high ---
        piv_lo = ta.pivot_low(df["low"], major_lb, major_lb).to_numpy(float)
        piv_hi = ta.pivot_high(df["high"], major_lb, major_lb).to_numpy(float)
        a_lo = _last_idx(piv_lo, n - 1 - max_age)
        a_hi = _last_idx(piv_hi, n - 1 - max_age)

        # --- minor pivots drive the MSB ---
        minor_hi = ta.pivot_high(df["high"], minor_lb, minor_lb).to_numpy(float)
        minor_lo = ta.pivot_low(df["low"], minor_lb, minor_lb).to_numpy(float)

        # --- session filter (optional; NY 13:30–20:00 UTC) ---
        if ny_only and isinstance(df.index, pd.DatetimeIndex):
            t = df.index[-1]
            mins = t.hour * 60 + t.minute
            if not (13 * 60 + 30 <= mins <= 20 * 60):
                return None

        # --- intraday HOD/LOD (don't chase) ---
        if isinstance(df.index, pd.DatetimeIndex):
            day = df.index.normalize()
            hod = df["high"].groupby(day).cummax().to_numpy(float)
            lod = df["low"].groupby(day).cummin().to_numpy(float)
        else:
            hod = ta.highest(df["high"], 96).to_numpy(float)
            lod = ta.lowest(df["low"], 96).to_numpy(float)
        new_hod_recent = any(high[-k] >= hod[-k] - 1e-9 for k in range(1, extend_lb + 1))
        new_lod_recent = any(low[-k] <= lod[-k] + 1e-9 for k in range(1, extend_lb + 1))

        # AVWAP separation guard (skip bunched-up anchors)
        sep_ok = True
        if a_lo is not None and a_hi is not None:
            sep = abs(avwap_at(a_lo, n - 1) - avwap_at(a_hi, n - 1))
            sep_ok = sep >= min_sep * atr_v

        i = n - 1

        # ============================ LONG ============================
        if a_lo is not None and sep_ok and not new_hod_recent:
            av_now = avwap_at(a_lo, i)
            if np.isfinite(av_now) and close[i] > av_now:
                # retest: a recent low came within tol·ATR of the support AVWAP
                retested = False
                for t in range(max(a_lo + 1, i - retest_win), i + 1):
                    av_t = avwap_at(a_lo, t)
                    if np.isfinite(av_t) and abs(low[t] - av_t) <= retest_tol * atr[t]:
                        retested = True
                        break
                # bullish MSB: close crosses above the last minor swing high
                lvl = _last_val(minor_hi, i - minor_lb)
                msb = lvl is not None and close[i] > lvl and close[i - 1] <= lvl
                if retested and msb:
                    retest_low = float(np.min(low[max(a_lo, i - retest_win): i + 1]))
                    mlo = _last_val(minor_lo, i - minor_lb)
                    sl = retest_low if sl_mode == "conservative" else (mlo if mlo is not None else retest_low)
                    sl = min(sl, close[i] - 0.2 * atr_v)         # ensure below entry
                    risk = close[i] - sl
                    if risk > 0:
                        return self._sig(1, close[i], sl, risk, tp1m, tp2m, tp3m,
                                         av_now, low, high, i, retest_win)

        # ============================ SHORT ===========================
        if a_hi is not None and sep_ok and not new_lod_recent:
            av_now = avwap_at(a_hi, i)
            if np.isfinite(av_now) and close[i] < av_now:
                retested = False
                for t in range(max(a_hi + 1, i - retest_win), i + 1):
                    av_t = avwap_at(a_hi, t)
                    if np.isfinite(av_t) and abs(high[t] - av_t) <= retest_tol * atr[t]:
                        retested = True
                        break
                lvl = _last_val(minor_lo, i - minor_lb)
                msb = lvl is not None and close[i] < lvl and close[i - 1] >= lvl
                if retested and msb:
                    retest_high = float(np.max(high[max(a_hi, i - retest_win): i + 1]))
                    mhi = _last_val(minor_hi, i - minor_lb)
                    sl = retest_high if sl_mode == "conservative" else (mhi if mhi is not None else retest_high)
                    sl = max(sl, close[i] + 0.2 * atr_v)
                    risk = sl - close[i]
                    if risk > 0:
                        return self._sig(-1, close[i], sl, risk, tp1m, tp2m, tp3m,
                                         av_now, low, high, i, retest_win)
        return None

    def _sig(self, direction, entry, sl, risk, tp1m, tp2m, tp3m, av, low, high, i, win):
        tps = (entry + direction * risk * tp1m,
               entry + direction * risk * tp2m,
               entry + direction * risk * tp3m)
        # A+ grade: tight retest (entry close to AVWAP relative to risk) scores higher
        reclaim = abs(entry - av) / risk if risk > 0 else 1.0
        grade = "A+" if reclaim <= 0.5 else "A" if reclaim <= 1.0 else "B"
        side = "long" if direction == 1 else "short"
        return Signal(direction, float(entry), float(sl), float(tps[0]), float(tps[1]),
                      float(tps[2]), grade, round(1.0 / (reclaim + 1e-6), 2),
                      f"AVWAP retest + MSB {side} (reclaim={reclaim:.2f}R)")


def _last_idx(piv: np.ndarray, min_idx: int) -> Optional[int]:
    """Index of the most recent confirmed pivot at or after `min_idx`."""
    valid = np.where(~np.isnan(piv))[0]
    valid = valid[valid >= max(0, min_idx)]
    return int(valid[-1]) if len(valid) else None


def _last_val(piv: np.ndarray, upto: int) -> Optional[float]:
    """Value of the most recent confirmed pivot at or before index `upto`."""
    if upto < 0:
        return None
    seg = piv[: upto + 1]
    valid = np.where(~np.isnan(seg))[0]
    return float(seg[valid[-1]]) if len(valid) else None
