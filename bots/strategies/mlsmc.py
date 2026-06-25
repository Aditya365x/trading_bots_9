"""
ML Smart Money Concepts (mlsmc) — port of GainzAlgo's
"Machine Learning Smart Money Concepts" Pine indicator (strategies2/mlsmc.pine).

All chart visuals (boxes / neon wick traces / tables / ribbons) are dropped; the
signal engine is reproduced faithfully:

  1. CHoCH detection — a trend state machine. A *bullish* Change-of-Character
     fires when price CLOSES above the last confirmed swing high while the trend
     was not already up; bearish mirrors it below the last swing low.
  2. Three features per CHoCH:
       • volDelta   — mean candle-position delta (2c-h-l)/range over the move
       • displace   — |close - open[duration]| / ATR
       • velocity   — |close - open[duration]| / duration
  3. KNN with DELAYED LABELS. Every past CHoCH is labelled `lookahead` bars later
     by whether its max-favourable run beat its max-adverse run (outcome ±1) and
     by how far it ran (favourableRun). A new CHoCH is classified against its K
     nearest historical analogues (same direction, within `windowLen`):
       score = (# favourable neighbours / K) × 100
     and the neighbours' favourable runs set TP1 (mean×scalar), TP2 (median),
     TP3 (75th pct). A trade fires when score ≥ minScore.

Causality: the database is rebuilt inside `evaluate()` from CHoCHs whose full
`lookahead` outcome window lies in the PAST (origin + lookahead ≤ current bar),
so there is no look-ahead leakage — the same property Pine's `[lookahead]`
training had. Needs a large window (config `lookback_bars` ≈ windowLen + slack).

Pine is an indicator (no stop); for a tradeable Signal the SL is placed at the
structural origin (the opposing swing) ± an ATR buffer, with an ATR fallback.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from ..core import indicators as ta
from ..core.strategy_base import Signal, Strategy


@dataclass
class _Choch:
    idx: int
    is_bull: bool
    vol_delta: float
    displace: float
    velocity: float
    outcome: float = 0.0      # +1 favourable, -1 adverse (filled by labelling)
    fav_run: float = 0.0      # max favourable excursion in price units


class MLSMC(Strategy):
    name = "mlsmc"

    def evaluate(self, df: pd.DataFrame) -> Optional[Signal]:
        p = self.p
        swing_len = int(p("swing_len", 5))
        atr_len = int(p("atr_len", 14))
        lookahead = int(p("lookahead", 20))
        window_len = int(p("window_len", 1500))
        k_neighbors = int(p("k_neighbors", 5))
        min_score = float(p("min_score", 60))
        target_scalar = float(p("target_scalar", 0.5))
        sl_buf_atr = float(p("sl_buffer_atr", 0.5))
        sl_fallback_atr = float(p("sl_fallback_atr", 1.5))
        min_target_atr = float(p("min_target_atr", 0.5))

        n = len(df)
        warmup = max(window_len // 3, swing_len * 4 + lookahead + 50)
        if n < warmup:
            return None

        o = df["open"].to_numpy(float); h = df["high"].to_numpy(float)
        lo = df["low"].to_numpy(float); c = df["close"].to_numpy(float)
        vol = df["volume"].to_numpy(float)
        rng = np.maximum(h - lo, 1e-6)
        vd = np.where(vol > 0, (2.0 * c - h - lo) / rng, 0.0)     # candle-position delta proxy
        atr = ta.atr(df, atr_len).to_numpy(float)
        ph = ta.pivot_high(df["high"], swing_len, swing_len).to_numpy(float)
        pl = ta.pivot_low(df["low"], swing_len, swing_len).to_numpy(float)

        # --- forward scan: confirm swings, detect CHoCH, compute features ---
        events: list[_Choch] = []
        last_at_end: Optional[_Choch] = None
        last_high = last_low = np.nan
        last_hi_idx = last_lo_idx = -1
        trend = 0
        for i in range(n):
            pb = i - swing_len                       # pivot confirmed `swing_len` bars later
            if pb >= 0:
                if not np.isnan(ph[pb]):
                    last_high, last_hi_idx = ph[pb], pb
                if not np.isnan(pl[pb]):
                    last_low, last_lo_idx = pl[pb], pb

            is_bull = (not np.isnan(last_high)) and trend <= 0 and c[i] > last_high
            is_bear = (not np.isnan(last_low)) and trend >= 0 and c[i] < last_low
            if is_bull:
                trend = 1
            elif is_bear:
                trend = -1
            else:
                continue

            start = last_hi_idx if is_bull else last_lo_idx
            duration = min(max(1, i - start), 4990)
            safe_lb = min(duration, 50)
            vol_delta = float(np.mean(vd[i - safe_lb + 1: i + 1]))
            total_move = abs(c[i] - o[i - duration]) if i - duration >= 0 else abs(c[i] - o[0])
            a = atr[i]
            displace = (total_move / a) if (np.isfinite(a) and a > 0) else 1.0
            velocity = total_move / duration
            ev = _Choch(i, is_bull, vol_delta, displace, velocity)
            events.append(ev)
            if i == n - 1:
                last_at_end = ev

        if last_at_end is None:                      # last bar isn't a CHoCH → no setup
            return None

        # --- label every PAST CHoCH whose lookahead window is fully resolved ---
        db: list[_Choch] = []
        last_idx = n - 1
        for ev in events:
            if ev is last_at_end or ev.idx + lookahead > last_idx:
                continue
            if last_idx - ev.idx > window_len:
                continue
            ref = c[ev.idx]
            mf = ma = 0.0
            for kk in range(1, lookahead + 1):
                hh = h[ev.idx + kk]; ll = lo[ev.idx + kk]
                if ev.is_bull:
                    mf = max(mf, hh - ref); ma = max(ma, ref - ll)
                else:
                    mf = max(mf, ref - ll); ma = max(ma, hh - ref)
            ev.outcome = 1.0 if mf > ma else -1.0
            ev.fav_run = mf
            db.append(ev)

        # --- KNN classify the current CHoCH ---
        q = last_at_end
        matches = [(np.sqrt((q.vol_delta - r.vol_delta) ** 2 +
                            (q.displace - r.displace) ** 2 +
                            (q.velocity - r.velocity) ** 2), r)
                   for r in db if r.is_bull == q.is_bull]
        if not matches:
            return None
        matches.sort(key=lambda t: t[0])
        kk = matches[:k_neighbors]
        successful = sum(1 for _, r in kk if r.outcome > 0.0)
        score = successful / len(kk) * 100.0
        if score < min_score:
            return None

        runs = sorted(r.fav_run for _, r in kk)
        nr = len(runs)
        mean_run = float(np.mean(runs))
        med_run = runs[nr // 2]
        p75 = min(int(round(nr * 0.75)) - 1, nr - 1) if nr > 1 else 0
        aggr_run = runs[max(0, p75)]

        direction = 1 if q.is_bull else -1
        entry = float(c[-1])
        a = float(atr[-1]) if np.isfinite(atr[-1]) and atr[-1] > 0 else entry * 0.005

        # targets from neighbour runs (fall back to ATR multiples if degenerate)
        floor = min_target_atr * a
        dists = sorted([max(mean_run * target_scalar, floor),
                        max(med_run, floor * 2),
                        max(aggr_run, floor * 3)])
        tp1 = entry + direction * dists[0]
        tp2 = entry + direction * dists[1]
        tp3 = entry + direction * dists[2]

        # structural stop: beyond the opposing swing, else ATR fallback
        if direction == 1:
            sl = (last_low - sl_buf_atr * a) if not np.isnan(last_low) else entry - sl_fallback_atr * a
            sl = min(sl, entry - 0.25 * a)
        else:
            sl = (last_high + sl_buf_atr * a) if not np.isnan(last_high) else entry + sl_fallback_atr * a
            sl = max(sl, entry + 0.25 * a)

        if abs(entry - sl) <= 0:
            return None

        grade = "A+" if score >= 85 else "A" if score >= 75 else "B" if score >= 60 else "C"
        prob = score
        reason = (f"ML-SMC {'bull' if direction == 1 else 'bear'} CHoCH "
                  f"score={score:.0f}% k={len(kk)}/{len(matches)} db={len(db)}")
        meta = {"setup": "MLSMC_CHOCH", "prob": round(prob, 1), "n_matches": len(matches),
                "db_size": len(db), "trend": int(direction),
                "vol_delta": round(q.vol_delta, 3), "displace": round(q.displace, 2),
                "velocity": round(q.velocity, 6)}
        return Signal(direction, entry, float(sl), float(tp1), float(tp2), float(tp3),
                      grade, round(float(score), 1), reason, meta)
