"""
Liquidity Pools Pro — sweep-and-reverse off equal-high/low liquidity pools.

Port of Liquidity_pool.pine core:
  * Cluster confirmed pivot highs/lows within an ATR tolerance into "pools",
    tracking touch count, age and (approx) volume for a 0-100 strength score.
  * A sweep = wick pierces a pool's edge; if the bar also closes back inside
    (mitigation) it's a liquidity grab -> reversal signal.
  * Long  = low-side pool swept + close back above (strength >= min).
    Short = high-side pool swept + close back below.
  * Risk preset (default Balanced) -> SL ×ATR, TP1/TP2/TP3 ×Risk.

Note: per-bar volume-at-level accumulation is approximated by pivot-bar volume;
this only nudges the strength score (the threshold is driven by touches/recency).
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

from ..core import indicators as ta
from ..core.strategy_base import Signal, Strategy, atr_sl_tp, resolve_preset


class LiquidityPools(Strategy):
    name = "liquidity_pools"

    def evaluate(self, df: pd.DataFrame) -> Optional[Signal]:
        left = int(self.p("pivot_len", 8))
        right = int(self.p("right_confirm", 2))
        atr_tol = float(self.p("atr_tolerance", 0.25))
        max_lb = int(self.p("max_lookback", 200))
        half_life = int(self.p("half_life_bars", 150))
        min_strength = float(self.p("min_strength_signal", 25))
        require_body = bool(self.p("require_body_reversal", True))
        atr_len = int(self.p("atr_len_risk", 14))
        preset = self.p("risk_preset", "Balanced")
        custom = (float(self.p("sl_mult", 1.5)), float(self.p("tp1", 1.0)),
                  float(self.p("tp2", 2.0)), float(self.p("tp3", 3.0)))
        sl_m, tp1, tp2, tp3 = resolve_preset(preset, custom)

        if len(df) < max(60, left + right + 10):
            return None

        atr14 = ta.atr(df, 14).to_numpy()
        ph = ta.pivot_high(df["high"], left, right)
        pl = ta.pivot_low(df["low"], left, right)
        high = df["high"].to_numpy(); low = df["low"].to_numpy(); close = df["close"].to_numpy()
        vol = df["volume"].to_numpy()
        n = len(df)
        i = n - 1

        pools: list[dict] = []        # active pools
        sig_dir = 0
        sig_strength = 0.0

        def strength(p, bar):
            age = bar - p["last"]
            decay = 0.5 ** (age / half_life) if half_life else 1.0
            touch_pts = min(35.0, (p["touches"] - 1) * 9.0)
            return max(0.0, min(100.0, touch_pts + 35.0 * decay))

        for k in range(n):
            tol = atr14[k] * atr_tol if not np.isnan(atr14[k]) else 0.0
            # register confirmed pivots at this bar (pivot bar = k-right)
            pb = k - right
            if pb >= 0 and tol > 0:
                if not np.isnan(ph.iloc[pb]):
                    _match_or_create(pools, float(ph.iloc[pb]), pb, vol[pb], True, tol, max_lb)
                if not np.isnan(pl.iloc[pb]):
                    _match_or_create(pools, float(pl.iloc[pb]), pb, vol[pb], False, tol, max_lb)

            # sweep detection on active pools
            survivors = []
            for p in pools:
                p["top"] = p["level"] + tol / 2.0
                p["bot"] = p["level"] - tol / 2.0
                eligible = k >= p["last"] + right + 1
                wick = (high[k] > p["top"]) if p["is_high"] else (low[k] < p["bot"])
                if eligible and wick:
                    close_back = (close[k] < p["level"]) if p["is_high"] else (close[k] > p["level"])
                    if require_body and not close_back:
                        # swept (broken) — pool consumed, drop
                        continue
                    s = strength(p, k)
                    if (not require_body or close_back) and s >= min_strength and k == i:
                        if p["is_high"]:
                            sig_dir = -1
                        else:
                            sig_dir = 1
                        sig_strength = max(sig_strength, s)
                    # pool consumed by the sweep -> drop
                    continue
                survivors.append(p)
            pools = survivors

        if sig_dir == 0:
            return None
        atr_v = float(ta.atr(df, atr_len).iloc[-1])
        if atr_v <= 0 or np.isnan(atr_v):
            return None
        c = float(close[i])
        sl, t1, t2, t3 = atr_sl_tp(sig_dir, c, atr_v, sl_m, tp1, tp2, tp3)
        side = "low-side" if sig_dir == 1 else "high-side"
        return Signal(sig_dir, c, sl, t1, t2, t3, "A", sig_strength,
                      f"{side} liquidity sweep (str {sig_strength:.0f})")


def _match_or_create(pools, price, bar, volume, is_high, tol, max_lb):
    for p in pools:
        if p["is_high"] == is_high and abs(price - p["level"]) <= tol and (bar - p["last"]) <= max_lb:
            p["level"] = (p["level"] * p["touches"] + price) / (p["touches"] + 1)
            p["last"] = bar
            p["touches"] += 1
            p["vol"] += (volume if not math.isnan(volume) else 0.0)
            return
    pools.append({"level": price, "last": bar, "touches": 1,
                  "vol": (volume if not math.isnan(volume) else 0.0),
                  "is_high": is_high, "top": price + tol / 2, "bot": price - tol / 2})
