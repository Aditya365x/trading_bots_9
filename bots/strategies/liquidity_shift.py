"""
Liquidity Shift Detection (liquidity_shift) — port of Zeiierman's
"Professional Liquidity Shift Detection [LSD]" (PineScripts/liquiditySweep.pine).

All chart drawing (lines / boxes / wick traces / dashboard / labels) is dropped;
the signal state machine is reproduced faithfully:

  * SWEEP (arms a pending setup):
      bull = low < last swing low  AND close > last swing low   (grab + reclaim)
      bear = high > last swing high AND close < last swing high
  * CONFIRMATION (within `max_bars_pending` bars) — the "liquidity shift":
      break       = close beyond the OPPOSITE swing, or
      displacement= strong candle in-direction (|close-open| ≥ ATR × disp_mult)
      confirm_mode selects break-only / displacement-only / either (default).
  * On confirmation the setup fires in the sweep-reversal direction; the pending
    setup expires if unconfirmed after `max_bars_pending` bars.

Pine is an indicator (no risk model). For a tradeable Signal the stop sits beyond
the sweep extreme (the natural invalidation) ± an ATR buffer, with a min/max-stop
guard, and TP1/2/3 are R-multiples of that risk.

The pending state machine is rebuilt inside the pure `evaluate(df)` window each
call, and a Signal is emitted only when a confirmation lands on the LAST bar.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from ..core import indicators as ta
from ..core.strategy_base import Signal, Strategy


class LiquidityShift(Strategy):
    name = "liquidity_shift"

    def evaluate(self, df: pd.DataFrame) -> Optional[Signal]:
        p = self.p
        swing_len = int(p("swing_len", 8))
        atr_len = int(p("atr_len", 14))
        disp_mult = float(p("disp_mult", 1.20))
        max_pending = int(p("max_bars_pending", 100))
        mode = str(p("confirm_mode", "both")).lower()       # both | break | disp
        sl_buf = float(p("sl_buffer_atr", 0.5))
        min_stop_atr = float(p("min_stop_atr", 0.3))
        max_stop_pct = float(p("max_stop_pct", 2.0)) / 100.0
        tp1_r = float(p("tp1_r", 1.0)); tp2_r = float(p("tp2_r", 2.0)); tp3_r = float(p("tp3_r", 3.0))

        n = len(df)
        if n < max(80, swing_len * 2 + 20):
            return None

        o = df["open"].to_numpy(float); h = df["high"].to_numpy(float)
        lo = df["low"].to_numpy(float); c = df["close"].to_numpy(float)
        atr = ta.atr(df, atr_len).to_numpy(float)
        body = np.abs(c - o)
        ph = ta.pivot_high(df["high"], swing_len, swing_len).to_numpy(float)
        pl = ta.pivot_low(df["low"], swing_len, swing_len).to_numpy(float)

        last_high = last_low = np.nan
        trend = 0
        # pending bull/bear formations
        bp = False; bb = 0; blvl = 0.0; bext = 0.0
        sp = False; sb = 0; slvl = 0.0; sext = 0.0
        fired_bull = fired_bear = None

        for i in range(n):
            pb = i - swing_len                       # pivot confirmed `swing_len` bars later
            if pb >= 0:
                if not np.isnan(ph[pb]):
                    last_high = ph[pb]
                if not np.isnan(pl[pb]):
                    last_low = pl[pb]

            # ---- sweeps arm a pending setup (a new sweep replaces the old) ----
            if (not np.isnan(last_low)) and lo[i] < last_low and c[i] > last_low:
                bp, bb, blvl, bext = True, i, last_low, lo[i]
            if (not np.isnan(last_high)) and h[i] > last_high and c[i] < last_high:
                sp, sb, slvl, sext = True, i, last_high, h[i]

            if bp:
                bext = min(bext, lo[i])
            if sp:
                sext = max(sext, h[i])
            if bp and i - bb > max_pending:
                bp = False
            if sp and i - sb > max_pending:
                sp = False

            # ---- confirmation readiness ----
            a = atr[i]
            ok = np.isfinite(a) and a > 0
            bull_disp = ok and c[i] > o[i] and body[i] >= a * disp_mult
            bear_disp = ok and c[i] < o[i] and body[i] >= a * disp_mult
            bull_break = (not np.isnan(last_high)) and c[i] > last_high
            bear_break = (not np.isnan(last_low)) and c[i] < last_low
            if mode == "break":
                bull_ready, bear_ready = bull_break, bear_break
            elif mode == "disp":
                bull_ready, bear_ready = bull_disp, bear_disp
            else:
                bull_ready, bear_ready = (bull_break or bull_disp), (bear_break or bear_disp)

            if bp and bull_ready:
                if i == n - 1:
                    fired_bull = (blvl, bext, bull_break, bull_disp)
                trend = 1; bp = False
            if sp and bear_ready:
                if i == n - 1:
                    fired_bear = (slvl, sext, bear_break, bear_disp)
                trend = -1; sp = False

        if fired_bull and fired_bear:                # contradictory close — skip
            return None
        if not fired_bull and not fired_bear:
            return None

        entry = float(c[-1])
        a = float(atr[-1]) if (np.isfinite(atr[-1]) and atr[-1] > 0) else entry * 0.005
        direction = 1 if fired_bull else -1
        level, extreme, brk, disp = fired_bull if fired_bull else fired_bear

        if direction == 1:
            sl = extreme - sl_buf * a
            if entry - sl < min_stop_atr * a:
                sl = entry - min_stop_atr * a
        else:
            sl = extreme + sl_buf * a
            if sl - entry < min_stop_atr * a:
                sl = entry + min_stop_atr * a
        risk = abs(entry - sl)
        if risk <= 0 or risk / entry > max_stop_pct:
            return None

        tp1 = entry + direction * risk * tp1_r
        tp2 = entry + direction * risk * tp2_r
        tp3 = entry + direction * risk * tp3_r

        score = min(100, 60 + (20 if disp else 0) + (20 if brk else 0))
        grade = "A+" if score >= 90 else "A" if score >= 75 else "B"
        conf = "+".join([t for t, f in (("break", brk), ("disp", disp)) if f]) or "none"
        reason = (f"Liquidity shift {'bull' if direction == 1 else 'bear'} "
                  f"(sweep+{conf}) score={score}")
        meta = {"setup": "LIQ_SHIFT", "confirm": conf, "level": round(level, 6),
                "sweep_extreme": round(extreme, 6), "trend": trend}
        return Signal(direction, entry, float(sl), float(tp1), float(tp2), float(tp3),
                      grade, float(score), reason, meta)
