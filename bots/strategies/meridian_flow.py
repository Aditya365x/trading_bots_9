"""
Meridian Flow — Smart-Money structure: BOS / CHoCH entries.

Port of MeridianFlow.pine entry engine:
  * Confirmed swing highs/lows (pivot swingLen).
  * A close beyond the last unbroken swing high/low = a structure break.
    BOS continues the trend; CHoCH reverses it.
  * Signal modes: BOS+CHoCH (default), CHoCH only, BOS only.
  * Optional HTF EMA bias filter (longs only when price >= HTF EMA, etc.).
  * Risk preset (default Balanced) -> SL ×ATR, TP1/TP2/TP3 ×Risk.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from ..core import indicators as ta
from ..core.strategy_base import Signal, Strategy, atr_sl_tp, resolve_preset

_TF_PANDAS = {"1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min", "30m": "30min",
              "1h": "1h", "2h": "2h", "4h": "4h", "6h": "6h", "12h": "12h", "1d": "1D"}


class MeridianFlow(Strategy):
    name = "meridian_flow"

    def evaluate(self, df: pd.DataFrame) -> Optional[Signal]:
        swing_len = int(self.p("swing_len", 13))
        break_src = self.p("break_src", "Close")        # Close | Wick
        mode = self.p("signal_mode", "BOS + CHoCH")
        use_htf = bool(self.p("use_htf", False))
        htf_tf = self.p("htf_tf", "1h")
        htf_ema_len = int(self.p("htf_ema_len", 50))
        atr_len = int(self.p("atr_len_risk", 13))
        preset = self.p("risk_preset", "Balanced")
        custom = (float(self.p("sl_mult", 1.5)), float(self.p("tp1", 1.0)),
                  float(self.p("tp2", 2.0)), float(self.p("tp3", 3.0)))
        sl_m, tp1, tp2, tp3 = resolve_preset(preset, custom)

        warmup = max(swing_len * 2, 50)
        if len(df) < warmup + 5:
            return None

        ph = ta.pivot_high(df["high"], swing_len, swing_len)
        pl = ta.pivot_low(df["low"], swing_len, swing_len)
        close = df["close"].to_numpy()
        high = df["high"].to_numpy()
        low = df["low"].to_numpy()
        n = len(df)

        struct_trend = 0
        last_hi = np.nan; hi_broken = True
        last_lo = np.nan; lo_broken = True
        ev = (False, False, False, False)  # bullBOS, bullCHoCH, bearBOS, bearCHoCH at last bar
        for k in range(n):
            cb = k - swing_len
            if cb >= 0 and not np.isnan(ph.iloc[cb]):
                last_hi = float(ph.iloc[cb]); hi_broken = False
            if cb >= 0 and not np.isnan(pl.iloc[cb]):
                last_lo = float(pl.iloc[cb]); lo_broken = False
            bh = close[k] if break_src == "Close" else high[k]
            bl = close[k] if break_src == "Close" else low[k]
            bull = (not np.isnan(last_hi)) and (not hi_broken) and bh > last_hi
            bear = (not np.isnan(last_lo)) and (not lo_broken) and bl < last_lo
            if bull and bear:
                bull = bear = False
            warmed = k >= warmup
            bbos = bcho = sbos = scho = False
            if bull and warmed:
                bbos = struct_trend >= 0
                bcho = struct_trend < 0
                struct_trend = 1; hi_broken = True
            if bear and warmed:
                sbos = struct_trend <= 0
                scho = struct_trend > 0
                struct_trend = -1; lo_broken = True
            if k == n - 1:
                ev = (bbos, bcho, sbos, scho)

        bull_bos, bull_choch, bear_bos, bear_choch = ev
        if mode == "CHoCH only":
            bull_ev, bear_ev = bull_choch, bear_choch
        elif mode == "BOS only":
            bull_ev, bear_ev = bull_bos, bear_bos
        else:
            bull_ev, bear_ev = (bull_bos or bull_choch), (bear_bos or bear_choch)
        if not (bull_ev or bear_ev):
            return None

        # HTF bias
        htf_bull = htf_bear = True
        if use_htf:
            rule = _TF_PANDAS.get(htf_tf, "1h")
            agg = df["close"].resample(rule).last().dropna()
            if len(agg) > htf_ema_len:
                htf_ema = ta.ema(agg, htf_ema_len).reindex(df.index, method="ffill")
                ema_v = float(htf_ema.iloc[-1])
                htf_bull = close[-1] >= ema_v
                htf_bear = close[-1] <= ema_v

        atr_v = float(ta.atr(df, atr_len).iloc[-1])
        if atr_v <= 0 or np.isnan(atr_v):
            return None
        c = float(close[-1])

        if bull_ev and htf_bull:
            sl, t1, t2, t3 = atr_sl_tp(1, c, atr_v, sl_m, tp1, tp2, tp3)
            tag = "CHoCH" if bull_choch else "BOS"
            return Signal(1, c, sl, t1, t2, t3, "A", 0.0, f"Bullish {tag}")
        if bear_ev and htf_bear:
            sl, t1, t2, t3 = atr_sl_tp(-1, c, atr_v, sl_m, tp1, tp2, tp3)
            tag = "CHoCH" if bear_choch else "BOS"
            return Signal(-1, c, sl, t1, t2, t3, "A", 0.0, f"Bearish {tag}")
        return None
