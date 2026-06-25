"""Focused backtest of Mirage Liquidity Sweep across two param sets."""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.backtest_all import fetch, backtest, metrics    # noqa: E402
from bots.strategies.mirage_liquidity_sweep import MirageLiquiditySweep  # noqa: E402

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
PERIODS = [7, 14, 30, 50]
PERIOD_TF = {7: "15m", 14: "15m", 30: "1h", 50: "1h"}
VARIANTS = {
    "faithful (swing=21)": dict(swing_len=21, min_score=50),
    "active (swing=10)":   dict(swing_len=10, min_score=45),
}
RISK = 0.01

now = pd.Timestamp.now("UTC")
tfs = sorted({PERIOD_TF[p] for p in PERIODS})
max_days = {tf: max(p for p in PERIODS if PERIOD_TF[p] == tf) for tf in tfs}
tf_min = {"15m": 15, "1h": 60}

print("fetching data ...")
data = {}
for sym in SYMBOLS:
    for tf in tfs:
        bars = int(max_days[tf] * 1440 / tf_min[tf]) + 300
        data[(sym, tf)] = fetch(sym, tf, bars)

cache = {}
for vname, params in VARIANTS.items():
    for sym in SYMBOLS:
        for tf in tfs:
            strat = MirageLiquiditySweep(params)
            cache[(vname, sym, tf)] = backtest(strat, data[(sym, tf)], window=240, cost_bps=5.0)
    print(f"  backtested variant: {vname}")

for vname in VARIANTS:
    print("\n" + "=" * 78)
    print(f"=== MIRAGE LIQUIDITY SWEEP — {vname}   risk=1%/trade  cost=5bps ===")
    print("=" * 78)
    print(f"{'period':<8} {'trades':>6} {'win%':>6} {'avgR':>6} {'return':>8} {'maxDD':>8} {'PF':>6}   per-symbol return")
    for period in PERIODS:
        tf = PERIOD_TF[period]
        cutoff = now - pd.Timedelta(days=period)
        all_tr = []
        sym_rets = []
        for sym in SYMBOLS:
            tr = [t for t in cache[(vname, sym, tf)] if t[0] >= cutoff]
            sym_rets.append((sym, metrics(tr, RISK)["ret"]))
            all_tr += tr
        m = metrics(all_tr, RISK)
        pf = "inf" if m["pf"] == float("inf") else f"{m['pf']:.2f}"
        ret_str = " ".join(f"{s[:3]}:{r:+.1%}" for s, r in sym_rets)
        print(f"{period:>3}d    {m['n']:>6} {m['win']:>5.0%} {m['avgR']:>6.2f} "
              f"{m['ret']:>+8.1%} {m['maxdd']:>+8.1%} {pf:>6}   {ret_str}")
