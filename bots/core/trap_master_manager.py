"""
Trap Master trade-management state machine (blueprint Part 9 + 13.1).

This is the execution layer the pure strategy cannot be: it nurtures an open
position bar-by-bar through the full lifecycle —

    ENTERED  → SL | structural invalidation (new opposing swing before TP1)
             | breakeven-acceleration (13.1, at 0.8R on a reversal candle)
             | time stop (>20 bars, not in profit) | TP1 (40%, SL→breakeven)
    TP1_HIT  → breakeven SL (60%) | TP2 (30%, start trailing)
             | lower-high/higher-low → trail | momentum loss (EMA20 + slope)
             | time stop (>40 bars)
    TP2_HIT  → swing-trailing SL on the 30% runner | trend exhaustion (EMA20×EMA50)

It is deliberately I/O-free and index-based so the SAME code runs in the
backtester (scripts/backtest_trap_master.py) and, with a thin candle-loop in the
runner, live. `build_context()` produces the causal arrays it needs — every
forward decision uses only confirmed swings (pivot+right lag) and causal EMAs, so
there is no look-ahead.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import indicators as ta
from ..strategies.trap_master import _confirmed_pivots

# Exit-split (blueprint Setup 1) and lifecycle timings
P1, P2, P3 = 0.40, 0.30, 0.30
TIME_STOP_ENTERED = 20
TIME_STOP_TP1 = 40
TRAIL_BUFFER = 0.0005          # 0.05% beyond the trailed swing
BREAKEVEN_ACCEL_R = 0.8        # 13.1


@dataclass
class ActiveTrade:
    direction: int             # 1 long, -1 short
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp3: float
    state: str = "ENTERED"
    bars: int = 0
    remaining: float = 1.0
    cur_sl: float = field(default=float("nan"))
    be_moved: bool = False

    def __post_init__(self):
        if not np.isfinite(self.cur_sl):
            self.cur_sl = self.sl

    @property
    def risk(self) -> float:
        return abs(self.entry - self.sl)


def build_context(df: pd.DataFrame, swing_right: int = 2) -> dict:
    """Causal arrays for the state machine (no look-ahead)."""
    h = df["high"].to_numpy(float); lo = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float); o = df["open"].to_numpy(float)
    n = len(df)
    rng = np.maximum(h - lo, 1e-12)
    body_ratio = np.abs(c - o) / rng
    ema20 = ta.ema(df["close"], 20).to_numpy(float)
    ema50 = ta.ema(df["close"], 50).to_numpy(float)
    highs_idx, lows_idx = _confirmed_pivots(h, lo, swing_right, swing_right)

    new_low = np.full(n, np.nan); new_high = np.full(n, np.nan)
    lower_high = np.zeros(n, dtype=bool); higher_low = np.zeros(n, dtype=bool)
    prev_h = prev_l = None
    for p in sorted(highs_idx):
        j = p + swing_right
        if j < n:
            new_high[j] = h[p]
            lower_high[j] = prev_h is not None and h[p] < prev_h
        prev_h = h[p]
    for p in sorted(lows_idx):
        j = p + swing_right
        if j < n:
            new_low[j] = lo[p]
            higher_low[j] = prev_l is not None and lo[p] > prev_l
        prev_l = lo[p]

    rs_low = np.full(n, np.nan); rs_high = np.full(n, np.nan)
    last = np.nan
    for j in range(n):
        if not np.isnan(new_low[j]):
            last = new_low[j]
        rs_low[j] = last
    last = np.nan
    for j in range(n):
        if not np.isnan(new_high[j]):
            last = new_high[j]
        rs_high[j] = last
    return dict(o=o, h=h, lo=lo, c=c, body_ratio=body_ratio, ema20=ema20, ema50=ema50,
                rs_low=rs_low, rs_high=rs_high, new_low=new_low, new_high=new_high,
                lower_high=lower_high, higher_low=higher_low)


class TrapMasterManager:
    """Blueprint Part 9 state machine. `update()` returns a list of exit events
    ``{"pct", "price", "reason"}`` and mutates the trade in place; state becomes
    ``CLOSED`` when nothing remains."""

    def update(self, t: ActiveTrade, ctx: dict, i: int) -> list[dict]:
        if t.state == "CLOSED" or t.remaining <= 0:
            return []
        t.bars += 1
        long = t.direction == 1
        hi, lo, c, o = ctx["h"][i], ctx["lo"][i], ctx["c"][i], ctx["o"][i]
        ema20, ema50 = ctx["ema20"][i], ctx["ema50"][i]
        ema20p = ctx["ema20"][i - 1] if i > 0 else ema20
        ema50p = ctx["ema50"][i - 1] if i > 0 else ema50
        buf = TRAIL_BUFFER * t.entry
        out: list[dict] = []

        # 1) stop / trailing stop always checked first (conservative)
        if (lo <= t.cur_sl) if long else (hi >= t.cur_sl):
            return self._close(t, t.cur_sl, "trailing_stop" if t.state == "TP2_HIT"
                               else ("breakeven" if t.be_moved else "stop_loss"))

        if t.state == "ENTERED":
            nl = ctx["new_low"][i] if long else ctx["new_high"][i]
            if not np.isnan(nl) and ((nl < t.entry) if long else (nl > t.entry)):
                return self._close(t, c, "invalidation")
            if t.bars > TIME_STOP_ENTERED and ((c < t.entry) if long else (c > t.entry)):
                return self._close(t, c, "time_stop")
            # 13.1 breakeven acceleration: at 0.8R on a reversal candle, protect
            cur_r = (c - t.entry) / t.risk * t.direction if t.risk > 0 else 0.0
            reversal = (c < o if long else c > o) and ctx["body_ratio"][i] > 0.5
            if not t.be_moved and cur_r >= BREAKEVEN_ACCEL_R and reversal:
                t.cur_sl = t.entry; t.be_moved = True
            if (hi >= t.tp1) if long else (lo <= t.tp1):
                out.append({"pct": P1, "price": t.tp1, "reason": "tp1"})
                t.remaining -= P1; t.cur_sl = t.entry; t.be_moved = True
                t.state = "TP1_HIT"

        elif t.state == "TP1_HIT":
            if (hi >= t.tp2) if long else (lo <= t.tp2):
                out.append({"pct": P2, "price": t.tp2, "reason": "tp2"})
                t.remaining -= P2; t.state = "TP2_HIT"
                self._trail(t, ctx, i, buf)                 # arm the runner trail
            else:
                if ctx["lower_high"][i] if long else ctx["higher_low"][i]:
                    self._trail(t, ctx, i, buf)             # structure weakening → tighten
                mom = (c < ema20 and ema20 < ema20p) if long else (c > ema20 and ema20 > ema20p)
                if mom:
                    return self._close(t, c, "momentum_loss")
                if t.bars > TIME_STOP_TP1:
                    return self._close(t, c, "time_stop_tp1")

        elif t.state == "TP2_HIT":
            self._trail(t, ctx, i, buf)
            exhaust = (c < ema20 and ema20 < ema50 and ema20p >= ema50p) if long else \
                      (c > ema20 and ema20 > ema50 and ema20p <= ema50p)
            if exhaust:
                return self._close(t, c, "trend_exhaustion")
        return out

    def _trail(self, t: ActiveTrade, ctx: dict, i: int, buf: float) -> None:
        sw = ctx["rs_low"][i] if t.direction == 1 else ctx["rs_high"][i]
        if np.isnan(sw):
            return
        new = sw - buf if t.direction == 1 else sw + buf
        t.cur_sl = max(t.cur_sl, new) if t.direction == 1 else min(t.cur_sl, new)

    def _close(self, t: ActiveTrade, price: float, reason: str) -> list[dict]:
        pct = t.remaining
        t.remaining = 0.0
        t.state = "CLOSED"
        return [{"pct": pct, "price": price, "reason": reason}]
