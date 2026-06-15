"""
Breakout Pattern Setup — converging channel detection + boundary breakout.

Port of the core engine in Breakout_pattern.pine:
  * Collect recent confirmed pivot highs/lows.
  * Fit an upper boundary line (all highs below it) and a lower boundary line
    (all lows above it), each needing >= min touches.
  * Require the channel to be converging, within width bounds, and price inside
    on the previous bar.
  * Long  = last close breaks ABOVE upper boundary (with volume + momentum
            confirmation); Short = breaks below lower.
  * SL = opposite boundary (+ padding); TP1/TP2/TP3 = thirds of the projected
    channel-width target.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from ..core import indicators as ta
from ..core.strategy_base import Signal, Strategy


def _line_at(x1, y1, x2, y2, x):
    dx = x2 - x1
    return y1 + (y2 - y1) * (x - x1) / dx if abs(dx) > 1e-10 else y1


def _fit_boundary(points, upper, dev_tol, touch_tol, min_touches):
    """points: list[(x,y)]. Return (x1,y1,x2,y2,touches) of best fit or None."""
    n = len(points)
    best = None
    best_touches = 0
    for a in range(n - 1):
        ax, ay = points[a]
        for b in range(a + 1, n):
            bx, by = points[b]
            if abs(bx - ax) < 1.0:
                continue
            ok = True
            touches = 0
            for (kx, ky) in points:
                proj = _line_at(ax, ay, bx, by, kx)
                diff = ky - proj
                if upper and diff > dev_tol:
                    ok = False
                    break
                if (not upper) and diff < -dev_tol:
                    ok = False
                    break
                if abs(diff) <= touch_tol:
                    touches += 1
            if ok and touches >= min_touches and touches > best_touches:
                best_touches = touches
                best = (ax, ay, bx, by, touches)
    return best


class BreakoutPattern(Strategy):
    name = "breakout_pattern"

    def evaluate(self, df: pd.DataFrame) -> Optional[Signal]:
        pivot_len = int(self.p("pivot_len", 5))
        min_touches = int(self.p("min_touches", 2))
        max_bars = int(self.p("max_channel_bars", 120))
        conv_min = float(self.p("convergence_min", 0.02))
        touch_tol_m = float(self.p("touch_tolerance", 0.15))
        dev_max = float(self.p("deviation_max", 0.30))
        min_width_atr = float(self.p("min_channel_width_atr", 0.5))
        vol_confirm = float(self.p("vol_confirm", 1.2))
        momentum_filter = bool(self.p("momentum_filter", True))
        sl_pad = float(self.p("sl_padding_atr", 0.0))
        scan = int(self.p("max_pivot_scan", 15))

        if len(df) < max(max_bars, 60):
            return None

        atr20 = ta.atr(df, 20)
        rsi = ta.rsi(df["close"], 14)
        vol_sma = ta.sma(df["volume"], 20)
        ph = ta.pivot_high(df["high"], pivot_len, pivot_len)
        pl = ta.pivot_low(df["low"], pivot_len, pivot_len)

        i = len(df) - 1
        atr_v = float(atr20.iloc[i])
        if atr_v <= 0 or pd.isna(atr_v):
            return None

        # gather recent confirmed pivots (bar index position, price)
        hi_pts, lo_pts = [], []
        last_confirmable = i - pivot_len
        for j in range(last_confirmable, -1, -1):
            if i - j > max_bars:
                break
            if not np.isnan(ph.iloc[j]):
                hi_pts.append((float(j), float(ph.iloc[j])))
            if not np.isnan(pl.iloc[j]):
                lo_pts.append((float(j), float(pl.iloc[j])))
            if len(hi_pts) >= scan and len(lo_pts) >= scan:
                break
        if len(hi_pts) < min_touches or len(lo_pts) < min_touches:
            return None

        dev_tol = atr_v * dev_max
        touch_tol = atr_v * touch_tol_m
        up = _fit_boundary(hi_pts, True, dev_tol, touch_tol, min_touches)
        lo = _fit_boundary(lo_pts, False, dev_tol, touch_tol, min_touches)
        if up is None or lo is None:
            return None

        end_x = float(i)
        start_x = min(up[0], up[2], lo[0], lo[2])
        upper_now = _line_at(up[0], up[1], up[2], up[3], end_x)
        lower_now = _line_at(lo[0], lo[1], lo[2], lo[3], end_x)
        upper_start = _line_at(up[0], up[1], up[2], up[3], start_x)
        lower_start = _line_at(lo[0], lo[1], lo[2], lo[3], start_x)
        width_now = upper_now - lower_now
        width_start = upper_start - lower_start
        if width_now <= 0:
            return None
        conv_rate = 1.0 - (width_now / width_start) if width_start > 0 else 0.0
        if conv_rate < conv_min:
            return None
        if not (atr_v * min_width_atr <= width_now < atr_v * 10.0):
            return None

        # channel max width over overlap (for TP projection)
        max_w = width_now
        for s in range(0, 11):
            sx = start_x + (end_x - start_x) * s / 10.0
            w = _line_at(up[0], up[1], up[2], up[3], sx) - _line_at(lo[0], lo[1], lo[2], lo[3], sx)
            max_w = max(max_w, w)

        c = float(df["close"].iloc[i])
        prev_c = float(df["close"].iloc[i - 1])
        inside_prev = lower_now <= prev_c <= upper_now
        if not inside_prev:
            return None

        vol_ok = df["volume"].iloc[i] > float(vol_sma.iloc[i] or 0) * vol_confirm if (df["volume"] > 0).any() else True
        body = abs(c - df["open"].iloc[i])
        rng = df["high"].iloc[i] - df["low"].iloc[i]
        body_ratio = body / rng if rng > 0 else 0.0
        body_mid = (df["open"].iloc[i] + c) / 2.0

        def strength(pen, body_commit):
            vol_b = 1.0 if vol_ok else 0.4
            mom_b = 1.0  # filled below
            return (pen * 0.25 + body_ratio * 0.15 + body_commit * 0.15 + vol_b * 0.25 + mom_b * 0.20) * 100

        if c > upper_now:
            mom_ok = (rsi.iloc[i] > 50) if momentum_filter else True
            if not mom_ok:
                return None
            entry = c
            sl = lower_now - atr_v * sl_pad
            target = upper_now + max_w
            full = abs(target - entry)
            pen = min((c - upper_now) / atr_v, 2.0) / 2.0
            sc = strength(pen, 1.0 if body_mid > upper_now else 0.3)
            grade = "Strong" if sc >= 65 else "Medium" if sc >= 35 else "Weak"
            return Signal(1, entry, float(sl), float(entry + full / 3), float(entry + full * 2 / 3),
                          float(entry + full), grade, sc, f"Bull breakout [{grade}]")

        if c < lower_now:
            mom_ok = (rsi.iloc[i] < 50) if momentum_filter else True
            if not mom_ok:
                return None
            entry = c
            sl = upper_now + atr_v * sl_pad
            target = lower_now - max_w
            full = abs(entry - target)
            pen = min((lower_now - c) / atr_v, 2.0) / 2.0
            sc = strength(pen, 1.0 if body_mid < lower_now else 0.3)
            grade = "Strong" if sc >= 65 else "Medium" if sc >= 35 else "Weak"
            return Signal(-1, entry, float(sl), float(entry - full / 3), float(entry - full * 2 / 3),
                          float(entry - full), grade, sc, f"Bear breakout [{grade}]")
        return None
