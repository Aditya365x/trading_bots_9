"""
ICT Session Zones — liquidity sweeps of killzone session highs/lows.

Port of ICT_session_Zone.pine sweep signals:
  * Track each killzone's session high/low (Asia / London / NY AM / Lunch / PM).
  * After a session ends, its high/low rest as liquidity. A sweep = price wicks
    beyond that level and closes back inside, with a minimum wick depth (ATR),
    body confirmation, and a per-session cooldown.
  * Long  = session-low swept + close back above; Short = session-high swept +
    close back below.
  * The original is a pure indicator (no SL/TP); we add ATR-based SL & TP ladder.

Session times are interpreted in the configured timezone offset (default UTC).
DST is not modelled — set `tz_offset` to match your preference.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from ..core import indicators as ta
from ..core.strategy_base import Signal, Strategy, atr_sl_tp

_NAMED_TZ = {  # approximate standard-time offsets (no DST)
    "America/New_York": -5, "America/Chicago": -6, "America/Los_Angeles": -8,
    "Europe/London": 0, "Europe/Berlin": 1, "Europe/Moscow": 3,
    "Asia/Tokyo": 9, "Asia/Shanghai": 8, "Asia/Kolkata": 5.5, "Australia/Sydney": 10,
}

_DEFAULT_SESSIONS = [
    ("Asia", "2000-0000"), ("London", "0200-0500"), ("NY AM", "0930-1100"),
    ("NY Lunch", "1200-1300"), ("NY PM", "1330-1600"),
]


def _mins(hhmm: str, is_end: bool) -> int:
    v = int(hhmm)
    m = (v // 100) * 60 + (v % 100)
    if is_end and m == 0:
        return 1440
    return m


class IctSessionZones(Strategy):
    name = "ict_session_zones"

    def evaluate(self, df: pd.DataFrame) -> Optional[Signal]:
        tz = self.p("timezone", "GMT+0")
        offset = self._tz_offset(tz)
        min_sweep_atr = float(self.p("min_sweep_atr", 0.1))
        cooldown = int(self.p("sweep_cooldown", 3))
        body_filter = bool(self.p("body_filter", True))
        sessions_in = self.p("sessions", _DEFAULT_SESSIONS)
        sl_mult = float(self.p("sl_mult", 1.5))
        tp1m = float(self.p("tp1", 1.0)); tp2m = float(self.p("tp2", 2.0)); tp3m = float(self.p("tp3", 3.0))

        if not isinstance(df.index, pd.DatetimeIndex) or len(df) < 60:
            return None

        atr = ta.atr(df, 14).to_numpy()
        o = df["open"].to_numpy(); h = df["high"].to_numpy()
        lo = df["low"].to_numpy(); c = df["close"].to_numpy()
        n = len(df)
        i = n - 1
        warmup = 50

        # local minute-of-day per bar
        idx = df.index
        utc_min = idx.hour * 60 + idx.minute
        local_min = (np.asarray(utc_min) + int(round(offset * 60))) % 1440

        sessions = [(name, _mins(rng.split("-")[0], False), _mins(rng.split("-")[1], True))
                    for name, rng in sessions_in]

        result_dir = 0
        result_reason = ""

        for name, start, end in sessions:
            in_prev = False
            run_hi = run_lo = np.nan
            level_hi = level_lo = np.nan
            hi_valid = lo_valid = False
            last_sweep = -10_000
            for k in range(n):
                m = local_min[k]
                if start < end:
                    in_sess = start <= m < end
                else:
                    in_sess = m >= start or m < end
                if in_sess and not in_prev:
                    run_hi = h[k]; run_lo = lo[k]
                elif in_sess:
                    run_hi = max(run_hi, h[k]); run_lo = min(run_lo, lo[k])
                if (not in_sess) and in_prev:
                    level_hi = run_hi; level_lo = run_lo
                    hi_valid = lo_valid = True

                if (not in_sess) and k >= warmup:
                    atr_v = atr[k] if not np.isnan(atr[k]) else 0.0
                    body_mid = (o[k] + c[k]) / 2.0
                    cool = (k - last_sweep) >= cooldown
                    if hi_valid and not np.isnan(level_hi) and h[k] > level_hi:
                        depth_ok = (h[k] - level_hi) >= atr_v * min_sweep_atr if atr_v > 0 else True
                        body_ok = (c[k] < body_mid) if body_filter else True
                        if depth_ok and c[k] < level_hi and body_ok and cool:
                            last_sweep = k
                            if k == i:
                                result_dir = -1; result_reason = f"{name} high sweep"
                        if c[k] > level_hi:
                            hi_valid = False
                    if lo_valid and not np.isnan(level_lo) and lo[k] < level_lo:
                        depth_ok = (level_lo - lo[k]) >= atr_v * min_sweep_atr if atr_v > 0 else True
                        body_ok = (c[k] > body_mid) if body_filter else True
                        if depth_ok and c[k] > level_lo and body_ok and cool:
                            last_sweep = k
                            if k == i:
                                result_dir = 1; result_reason = f"{name} low sweep"
                        if c[k] < level_lo:
                            lo_valid = False
                in_prev = in_sess

        if result_dir == 0:
            return None
        atr_v = float(atr[i])
        if atr_v <= 0 or np.isnan(atr_v):
            return None
        c0 = float(c[i])
        sl, t1, t2, t3 = atr_sl_tp(result_dir, c0, atr_v, sl_mult, tp1m, tp2m, tp3m)
        return Signal(result_dir, c0, sl, t1, t2, t3, "A", 0.0, result_reason)

    @staticmethod
    def _tz_offset(tz: str) -> float:
        if tz in _NAMED_TZ:
            return _NAMED_TZ[tz]
        if tz.startswith("GMT"):
            try:
                return float(tz[3:] or 0)
            except ValueError:
                return 0.0
        return 0.0
