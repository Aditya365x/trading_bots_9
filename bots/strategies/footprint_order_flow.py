"""
Footprint Order Flow — the order_flow.md setups on TRUE footprint bars.

This is the tick-data successor to `order_flow_sniper` (which works on OHLCV
candles). Here every bar carries real per-price bid/ask volume, so:

  * delta / CVD are REAL (summed tick aggressor sides), not a close-location proxy
  * the volume profile (POC/VAH/VAL) is built from actual per-price volume
  * absorption, stacked imbalances and liquidity voids are read directly off the
    footprint instead of inferred from a candle

Contract: `evaluate(bars: list[FootprintBar]) -> Optional[Signal]`, acting on the
most-recently-closed bar (bars[-1]). Pure function — the runner/backtester
handles execution. Mirrors the two course setups:

  Setup A — Absorption Reversal: at VAH/VAL, a volume-spike bar shows strong
    one-sided REAL delta but price is capped (close at the extreme it was pushed
    to) with a real CVD effort-vs-result divergence → fade it.
  Setup B — Void Fill Continuation: an impulse left a true single-print void;
    price retraced into it and a stacked diagonal imbalance reloads in the trend
    direction (CVD not reversed, close on the trend side of VWAP).
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from ..core.strategy_base import Signal


class FootprintOrderFlow:
    name = "footprint_order_flow"

    def __init__(self, params: dict | None = None):
        self.params = params or {}

    def p(self, k, d):
        return self.params.get(k, d)

    # ------------------------------------------------------------------ #
    def evaluate(self, bars: list) -> Optional[Signal]:
        prof_bars = int(self.p("profile_bars", 120))
        va_pct = float(self.p("value_area_pct", 0.70))
        vol_spike = float(self.p("vol_spike_mult", 1.5))
        vol_ma_len = int(self.p("vol_ma_len", 20))
        min_dimb = float(self.p("min_delta_imb", 0.33))
        absorb_retreat = float(self.p("absorb_close_pct", 0.25))
        ctx_tol_rng = float(self.p("ctx_tol_rng", 0.5))     # ×median bar range
        div_swing = int(self.p("div_swing", 3))
        div_lookback = int(self.p("div_lookback", 40))
        div_price_tol_rng = float(self.p("div_price_tol_rng", 0.1))
        stacked_n = int(self.p("stacked_n", 3))
        stacked_ratio = float(self.p("stacked_ratio", 3.0))
        void_min_ticks = int(self.p("void_min_ticks", 3))
        retr_win = int(self.p("retr_window", 8))
        sl_buf_ticks = float(self.p("sl_buffer_ticks", 2.0))
        min_rr = float(self.p("min_rr", 1.5))

        n = len(bars)
        if n < prof_bars + vol_ma_len + 5:
            return None
        i = n - 1
        b = bars[i]
        tick = b.tick_size
        rng = b.range
        if rng <= 0 or b.volume <= 0:
            return None

        h = np.array([x.high for x in bars]); lo = np.array([x.low for x in bars])
        c = np.array([x.close for x in bars])
        qv = np.array([x.quote_volume for x in bars])
        delta = np.array([x.delta for x in bars])
        ts = np.array([x.ts for x in bars])

        med_range = float(np.median([x.range for x in bars[-prof_bars:] if x.range > 0]) or rng)
        tol = ctx_tol_rng * med_range
        ptol = div_price_tol_rng * med_range

        cvd = _daily_cumsum(ts, delta)
        vwap = _session_vwap(bars)
        vol_ma = float(np.mean(qv[i - vol_ma_len:i])) if i >= vol_ma_len else 0.0
        if vol_ma <= 0:
            return None

        prof = _profile(bars[i - prof_bars + 1: i + 1], va_pct)
        if prof is None:
            return None
        poc, vah, val = prof["poc"], prof["vah"], prof["val"]

        # ---- Setup A: Absorption Reversal ----
        if b.quote_volume >= vol_spike * vol_ma:
            dimb = b.delta_imbalance
            pos = (b.close - b.low) / rng        # 0=low, 1=high

            # SHORT: aggressive buying capped at VAH (close stays high), CVD div
            at_vah = abs(b.high - vah) <= tol or (b.low <= vah <= b.high)
            if (at_vah and dimb >= min_dimb and pos >= 1.0 - absorb_retreat
                    and _abs_div(h, cvd, i, div_swing, div_lookback, -1, ptol)):
                entry = b.close
                sl = b.high + sl_buf_ticks * tick
                risk = sl - entry
                if risk > 0:
                    tp1 = entry - 0.5 * rng
                    tp2 = min(vwap[i], poc) if np.isfinite(vwap[i]) else poc
                    tp2 = min(tp2, entry - risk * min_rr)
                    tp3 = min(val, tp2 - risk)
                    tp1, tp2, tp3 = _order(-1, entry, tp1, tp2, tp3)
                    if (entry - tp2) / risk >= min_rr:
                        grade = "A+" if poc < vwap[i] else "A"
                        return Signal(-1, float(entry), float(sl), float(tp1), float(tp2),
                                      float(tp3), grade, round((entry - tp2) / risk, 2),
                                      f"FP absorption @VAH (real Δ={dimb:+.0%}, CVD bear-div) "
                                      f"vol={b.quote_volume/vol_ma:.1f}x")

            # LONG: aggressive selling held at VAL (close stays low), CVD div
            at_val = abs(b.low - val) <= tol or (b.low <= val <= b.high)
            if (at_val and dimb <= -min_dimb and pos <= absorb_retreat
                    and _abs_div(lo, cvd, i, div_swing, div_lookback, +1, ptol)):
                entry = b.close
                sl = b.low - sl_buf_ticks * tick
                risk = entry - sl
                if risk > 0:
                    tp1 = entry + 0.5 * rng
                    tp2 = max(vwap[i], poc) if np.isfinite(vwap[i]) else poc
                    tp2 = max(tp2, entry + risk * min_rr)
                    tp3 = max(vah, tp2 + risk)
                    tp1, tp2, tp3 = _order(+1, entry, tp1, tp2, tp3)
                    if (tp2 - entry) / risk >= min_rr:
                        grade = "A+" if poc > vwap[i] else "A"
                        return Signal(+1, float(entry), float(sl), float(tp1), float(tp2),
                                      float(tp3), grade, round((tp2 - entry) / risk, 2),
                                      f"FP absorption @VAL (real Δ={dimb:+.0%}, CVD bull-div) "
                                      f"vol={b.quote_volume/vol_ma:.1f}x")

        # ---- Setup B: Void Fill Continuation ----
        sig = self._void_fill(bars, i, h, lo, c, ts, cvd, vwap, poc,
                              prof_bars, retr_win, stacked_n, stacked_ratio,
                              void_min_ticks, sl_buf_ticks, tick, min_rr)
        return sig

    # ------------------------------------------------------------------ #
    def _void_fill(self, bars, i, h, lo, c, ts, cvd, vwap, poc, prof_bars,
                   retr_win, stacked_n, stacked_ratio, void_min_ticks,
                   sl_buf_ticks, tick, min_rr):
        # voids from the impulse-era profile (exclude the recent pullback)
        impulse = bars[i - prof_bars + 1: i - retr_win + 1]
        if len(impulse) < 5:
            return None
        voids = _profile_voids(impulse, void_min_ticks, tick)
        if not voids:
            return None

        # stacked diagonal imbalance on the last bar (real footprint)
        si = bars[i].stacked_imbalances(ratio=stacked_ratio, min_run=stacked_n)
        retr_low = float(np.min(lo[i - retr_win:i + 1]))
        retr_high = float(np.max(h[i - retr_win:i + 1]))
        cvd_slope = cvd[i] - cvd[max(0, i - stacked_n)]
        day = ts[i] // 86_400_000
        same = ts // 86_400_000 == day
        sess_hi = float(np.max(h[same])); sess_lo = float(np.min(lo[same]))

        # LONG continuation
        if si["buy"] >= stacked_n and cvd_slope > 0 and c[i] > vwap[i]:
            z = _void_hit(voids, retr_low, below=True)
            if z is not None:
                entry = c[i]; sl = retr_low - sl_buf_ticks * tick
                risk = entry - sl
                if risk > 0:
                    tp1 = max(retr_high, entry + risk * min_rr)
                    tp2 = max(sess_hi, tp1 + risk); tp3 = tp2 + risk
                    tp1, tp2, tp3 = _order(+1, entry, tp1, tp2, tp3)
                    if (tp1 - entry) / risk >= min_rr:
                        return Signal(+1, float(entry), float(sl), float(tp1), float(tp2),
                                      float(tp3), "A", round((tp1 - entry) / risk, 2),
                                      "FP void-fill continuation (stacked bid imbalance, CVD up)")
        # SHORT continuation
        if si["sell"] >= stacked_n and cvd_slope < 0 and c[i] < vwap[i]:
            z = _void_hit(voids, retr_high, below=False)
            if z is not None:
                entry = c[i]; sl = retr_high + sl_buf_ticks * tick
                risk = sl - entry
                if risk > 0:
                    tp1 = min(retr_low, entry - risk * min_rr)
                    tp2 = min(sess_lo, tp1 - risk); tp3 = tp2 - risk
                    tp1, tp2, tp3 = _order(-1, entry, tp1, tp2, tp3)
                    if (entry - tp1) / risk >= min_rr:
                        return Signal(-1, float(entry), float(sl), float(tp1), float(tp2),
                                      float(tp3), "A", round((entry - tp1) / risk, 2),
                                      "FP void-fill continuation (stacked ask imbalance, CVD down)")
        return None


# --------------------------------------------------------------------------- #
#  Helpers (operate on FootprintBar lists / arrays)
# --------------------------------------------------------------------------- #
def _daily_cumsum(ts, delta) -> np.ndarray:
    day = ts // 86_400_000
    out = np.empty_like(delta, dtype=float)
    acc = 0.0; cur = None
    for k in range(len(delta)):
        if day[k] != cur:
            cur = day[k]; acc = 0.0
        acc += delta[k]; out[k] = acc
    return out


def _session_vwap(bars) -> np.ndarray:
    ts = np.array([b.ts for b in bars]); day = ts // 86_400_000
    out = np.empty(len(bars)); cq = cv = 0.0; cur = None
    for k, b in enumerate(bars):
        if day[k] != cur:
            cur = day[k]; cq = cv = 0.0
        cq += b.quote_volume; cv += b.volume
        out[k] = cq / cv if cv > 0 else b.close
    return out


def _profile(bars, va_pct):
    agg: dict[float, float] = {}
    for b in bars:
        for price, lvl in b.levels.items():
            agg[price] = agg.get(price, 0.0) + lvl.total
    if not agg:
        return None
    prices = sorted(agg)
    vols = np.array([agg[p] for p in prices])
    total = vols.sum()
    if total <= 0:
        return None
    poc_i = int(np.argmax(vols))
    lo_b = hi_b = poc_i; acc = vols[poc_i]; target = va_pct * total
    while acc < target and (lo_b > 0 or hi_b < len(prices) - 1):
        below = vols[lo_b - 1] if lo_b > 0 else -1.0
        above = vols[hi_b + 1] if hi_b < len(prices) - 1 else -1.0
        if above >= below:
            hi_b += 1; acc += max(above, 0.0)
        else:
            lo_b -= 1; acc += max(below, 0.0)
    return {"poc": prices[poc_i], "vah": prices[hi_b], "val": prices[lo_b]}


def _profile_voids(bars, min_ticks, tick) -> list[tuple[float, float]]:
    """True single-print voids: gaps of ≥min_ticks untraded ticks in the profile."""
    traded = set()
    for b in bars:
        traded.update(b.levels.keys())
    prices = sorted(traded)
    out = []
    for k in range(len(prices) - 1):
        gap = round((prices[k + 1] - prices[k]) / tick) - 1
        if gap >= min_ticks:
            out.append((prices[k] + tick, prices[k + 1] - tick))
    return out


def _void_hit(voids, retr_extreme, below):
    best = None
    for zlo, zhi in voids:
        if zlo <= retr_extreme <= zhi:
            if below:
                if best is None or zhi > best[1]:
                    best = (zlo, zhi)
            else:
                if best is None or zlo < best[0]:
                    best = (zlo, zhi)
    return best


def _abs_div(price_ext, cvd, i, swing, lookback, direction, tol) -> bool:
    """Effort-vs-result CVD divergence on real bar deltas (see order_flow_sniper)."""
    a = max(0, i - lookback); bnd = i - swing
    if bnd <= a:
        return False
    if direction < 0:
        return bool(cvd[i] >= float(np.max(cvd[a:bnd + 1]))
                    and price_ext[i] <= float(np.max(price_ext[a:bnd + 1])) + tol)
    return bool(cvd[i] <= float(np.min(cvd[a:bnd + 1]))
                and price_ext[i] >= float(np.min(price_ext[a:bnd + 1])) - tol)


def _order(direction, entry, t1, t2, t3):
    if direction == 1:
        t1 = max(t1, entry); t2 = max(t2, t1); t3 = max(t3, t2)
    else:
        t1 = min(t1, entry); t2 = min(t2, t1); t3 = min(t3, t2)
    return t1, t2, t3
