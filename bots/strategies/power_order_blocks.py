"""
Power Order Blocks — port of power_order_blocks.pine (ChartPrime).

The original is an indicator that draws order-block (OB) zones and flags
retests. We keep the *signal* logic and drop all box/label drawing:

  * A BULLISH OB forms when a bearish candle is immediately followed by a
    bullish displacement candle (close>open, close>high[1], and the body is
    bigger than the prior range × displacement multiplier). The zone is the
    prior candle's [low, high]. It stays valid until price closes below its
    bottom.  Mirror for BEARISH OBs (valid until price closes above the top).
  * A RETEST is when price taps back into the zone edge. In the Pine: bull
    retest = low[1] <= top and low >= top (price dips to the top of the demand
    zone and holds); bear retest = high[1] >= btm and high <= btm.

As a bot: a bullish-OB retest is a LONG (demand defended), a bearish-OB retest
is a SHORT. Stop sits just beyond the far edge of the zone; TPs are R-multiples.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from ..core import indicators as ta
from ..core.strategy_base import Signal, Strategy


@dataclass
class _OB:
    top: float
    btm: float
    birth: int
    touches: int = 0


class PowerOrderBlocks(Strategy):
    name = "power_order_blocks"

    def evaluate(self, df: pd.DataFrame) -> Optional[Signal]:
        disp_thresh = float(self.p("disp_thresh", 0.5))
        atr_len = int(self.p("atr_len", 14))
        str_lookback = int(self.p("strength_lookback", 100))
        max_blocks = int(self.p("max_blocks", 10))
        sl_buffer = float(self.p("sl_buffer_atr", 0.2))   # extra room beyond zone
        tp1m = float(self.p("tp1", 1.0)); tp2m = float(self.p("tp2", 2.0)); tp3m = float(self.p("tp3", 3.0))
        gap_bars = int(self.p("retest_gap_bars", 10))
        min_power = float(self.p("min_power_pct", 0.0))    # filter tiny zones

        n = len(df)
        if n < max(str_lookback, atr_len) + 5:
            return None

        o = df["open"].to_numpy(float); h = df["high"].to_numpy(float)
        lo = df["low"].to_numpy(float); c = df["close"].to_numpy(float)
        atr = ta.atr(df, atr_len)
        max_candle = ta.highest(df["high"] - df["low"], str_lookback)

        bulls: list[_OB] = []
        bears: list[_OB] = []
        last_bull_retest = -10**9
        last_bear_retest = -10**9
        sig_dir = 0
        sig_block: Optional[_OB] = None

        for i in range(1, n):
            # --- invalidate + detect retests on existing blocks ---
            bull_retest_raw = False
            for ob in list(bulls):
                if c[i] < ob.btm:
                    bulls.remove(ob)
                    continue
                if lo[i - 1] <= ob.top and lo[i] >= ob.top and i > ob.birth:
                    bull_retest_raw = True
                    ob.touches += 1
            bear_retest_raw = False
            for ob in list(bears):
                if c[i] > ob.top:
                    bears.remove(ob)
                    continue
                if h[i - 1] >= ob.btm and h[i] <= ob.btm and i > ob.birth:
                    bear_retest_raw = True
                    ob.touches += 1

            # 10-bar gap filter (mirror of the Pine cooldown)
            bull_retest = bull_retest_raw and (i - last_bull_retest >= gap_bars)
            bear_retest = bear_retest_raw and (i - last_bear_retest >= gap_bars)
            if bull_retest:
                last_bull_retest = i
            if bear_retest:
                last_bear_retest = i

            # --- new OB detection (uses prior candle as the zone) ---
            is_bear_prev = c[i - 1] < o[i - 1]
            bull_disp = c[i] > o[i] and c[i] > h[i - 1] and (c[i] - o[i]) > (h[i - 1] - lo[i - 1]) * disp_thresh
            if is_bear_prev and bull_disp:
                top, btm = h[i - 1], lo[i - 1]
                bulls = [b for b in bulls if not (top >= b.btm and btm <= b.top)]  # drop overlaps
                bulls.append(_OB(top, btm, i))
                if len(bulls) > max_blocks:
                    bulls.pop(0)

            is_bull_prev = c[i - 1] > o[i - 1]
            bear_disp = c[i] < o[i] and c[i] < lo[i - 1] and (o[i] - c[i]) > (h[i - 1] - lo[i - 1]) * disp_thresh
            if is_bull_prev and bear_disp:
                top, btm = h[i - 1], lo[i - 1]
                bears = [b for b in bears if not (top >= b.btm and btm <= b.top)]
                bears.append(_OB(top, btm, i))
                if len(bears) > max_blocks:
                    bears.pop(0)

            # remember a retest that lands on the final bar -> that's our signal
            if i == n - 1:
                if bull_retest:
                    sig_dir, sig_block = 1, _pick(bulls, "bull")
                elif bear_retest:
                    sig_dir, sig_block = -1, _pick(bears, "bear")

        if sig_dir == 0 or sig_block is None:
            return None

        mc = float(max_candle.iloc[-1]) or 0.0
        power = ((sig_block.top - sig_block.btm) / mc * 100.0) if mc > 0 else 0.0
        if power < min_power:
            return None

        atr_v = float(atr.iloc[-1])
        price = float(c[-1])
        if sig_dir == 1:
            sl = sig_block.btm - atr_v * sl_buffer
            risk = price - sl
            if risk <= 0:
                return None
            tps = (price + risk * tp1m, price + risk * tp2m, price + risk * tp3m)
        else:
            sl = sig_block.top + atr_v * sl_buffer
            risk = sl - price
            if risk <= 0:
                return None
            tps = (price - risk * tp1m, price - risk * tp2m, price - risk * tp3m)

        grade = "A+" if power >= 60 else "A" if power >= 40 else "B" if power >= 20 else "C"
        return Signal(sig_dir, price, float(sl), float(tps[0]), float(tps[1]), float(tps[2]),
                      grade, round(power, 1),
                      f"{'Bull' if sig_dir == 1 else 'Bear'} OB retest power={power:.0f}% touches={sig_block.touches}")


def _pick(blocks: list[_OB], side: str) -> Optional[_OB]:
    """The block being retested = the nearest valid zone (highest bull / lowest bear)."""
    if not blocks:
        return None
    return max(blocks, key=lambda b: b.top) if side == "bull" else min(blocks, key=lambda b: b.btm)
