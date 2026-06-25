"""
Backtest the footprint order-flow strategy on Binance Vision aggTrade history.

Pipeline:  Vision aggTrades -> ticks -> footprint bars -> evaluate() bar-by-bar
           -> simulate the SL/TP1/2/3 + break-even bracket -> metrics.

Reuses simulate_trade()/metrics() from backtest_all.py so footprint results are
directly comparable to the candle (order_flow_sniper) numbers.

    python scripts/backtest_footprint.py --symbols XRPUSDT SOLUSDT --start 2026-06-14 --end 2026-06-20
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import time

import numpy as np
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bots.core import vision                                  # noqa: E402
from bots.core.footprint import build_bars                    # noqa: E402
from bots.strategies.footprint_order_flow import FootprintOrderFlow  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "bt", os.path.join(os.path.dirname(__file__), "backtest_all.py"))
bt = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(bt)


def tick_size(symbol: str) -> float:
    info = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=30).json()
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            for f in s["filters"]:
                if f["filterType"] == "PRICE_FILTER":
                    return float(f["tickSize"])
    raise ValueError(f"tick size not found for {symbol}")


def replay(bars, strat, window, cost_bps):
    highs = np.array([b.high for b in bars]); lows = np.array([b.low for b in bars])
    closes = np.array([b.close for b in bars])
    trades = []
    t = window
    while t < len(bars) - 1:
        sig = strat.evaluate(bars[:t + 1])
        if sig is None:
            t += 1; continue
        res = bt.simulate_trade(sig.direction, float(sig.entry), sig.sl, sig.tp1, sig.tp2,
                                sig.tp3, highs[t + 1:], lows[t + 1:], closes[t + 1:],
                                cost_bps=cost_bps)
        if res is None:
            t += 1; continue
        rR, exit_off = res
        trades.append((bars[t].ts, rR, sig.direction, sig.reason))
        t = t + 1 + exit_off + 1
    return trades


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="*", default=["XRPUSDT", "SOLUSDT"])
    ap.add_argument("--start", default="2026-06-14")
    ap.add_argument("--end", default="2026-06-20")
    ap.add_argument("--interval", type=int, default=300, help="bar seconds (300=5m)")
    ap.add_argument("--window", type=int, default=160)
    ap.add_argument("--risk-pct", type=float, default=0.5)
    args = ap.parse_args()

    strat = FootprintOrderFlow({})
    risk_frac = args.risk_pct / 100.0
    print(f"=== Footprint order flow  {args.start}..{args.end}  {args.interval//60}m  "
          f"risk={args.risk_pct}%/trade ===")
    print(f"{'symbol':<10}{'bars':>7}{'trades':>7}{'win%':>6}{'avgR(5bps)':>11}"
          f"{'avgR(0bps)':>11}{'ret':>8}{'maxDD':>8}{'PF':>6}")

    for sym in args.symbols:
        tk = tick_size(sym)
        t0 = time.time()
        ticks = vision.read_range(sym, args.start, args.end)
        bars = build_bars(ticks, args.interval, tk)
        tr5 = replay(bars, strat, args.window, cost_bps=5.0)
        tr0 = replay(bars, strat, args.window, cost_bps=0.0)
        m5 = bt.metrics([(a, b, c) for a, b, c, _ in tr5], risk_frac)
        m0 = bt.metrics([(a, b, c) for a, b, c, _ in tr0], risk_frac)
        pf = "inf" if m5["pf"] == float("inf") else f"{m5['pf']:.2f}"
        print(f"{sym:<10}{len(bars):>7}{m5['n']:>7}{m5['win']:>5.0%}"
              f"{m5['avgR']:>+11.2f}{m0['avgR']:>+11.2f}{m5['ret']:>+8.1%}"
              f"{m5['maxdd']:>+8.1%}{pf:>6}   ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
