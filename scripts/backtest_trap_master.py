"""
Backtest the Trap Master system on 5-minute candles with real 15m/1h context.

Why a dedicated script (vs scripts/backtest_all.py): Trap Master is 5m-primary and
resamples its higher-timeframe context internally, so it must be replayed on 5m
with a LARGE trailing window (default 720 bars ≈ 60h ≈ 60 × 1h). It also scales
each trade's risk by the blueprint quality score (Signal.meta["risk_mult"]), which
this script feeds into the equity curve so the reported return matches live sizing.

Unlike the generic harness, this script implements the blueprint's full Part 9
trade-management STATE MACHINE faithfully (this is where the profit factor lives):

  ENTERED  -> SL | structural invalidation (new opposing swing before TP1)
            | time stop (>20 bars, not in profit) | TP1 (40%, SL->breakeven)
  TP1_HIT  -> breakeven SL (60%) | TP2 (30%, start trailing)
            | momentum loss (close vs EMA20, slope down) | time stop (>40 bars)
  TP2_HIT  -> swing-trailing SL on the 30% runner | trend exhaustion (EMA20<EMA50)

All forward-looking decisions use only confirmed data (swings via pivot+right
lag, causal EMAs), so there is no look-ahead.

Usage:
    ./venvbots/Scripts/python.exe scripts/backtest_trap_master.py
    ./venvbots/Scripts/python.exe scripts/backtest_trap_master.py --symbols BTCUSDT --days 14
    ./venvbots/Scripts/python.exe scripts/backtest_trap_master.py --simple   # repo-standard bracket
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.backtest_all import fetch, simulate_trade           # noqa: E402
from bots.strategies.trap_master import TrapMaster               # noqa: E402
from bots.core.trap_master_manager import (                      # noqa: E402
    ActiveTrade, TrapMasterManager, build_context)


def simulate_state_machine(t, direction, entry, sl, tp1, tp2, tp3, ctx, cost_bps):
    """Drive ONE trade through the live Part 9 manager from bar t+1 -> realised R.

    Uses the exact same TrapMasterManager that live trading would, so the
    backtest measures the real execution layer rather than a parallel copy."""
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    n = len(ctx["c"])
    trade = ActiveTrade(direction, entry, sl, tp1, tp2, tp3)
    mgr = TrapMasterManager()

    def R(price):
        return (price - entry) / risk * direction

    realized = 0.0
    exit_i = min(t + 1, n - 1)
    for j in range(t + 1, n):
        exit_i = j
        for ev in mgr.update(trade, ctx, j):
            realized += ev["pct"] * R(ev["price"])
        if trade.state == "CLOSED":
            break
    if trade.remaining > 0:                                  # mark-to-market at the last bar
        realized += trade.remaining * R(ctx["c"][exit_i])
    fee_R = (2.0 * cost_bps / 1e4) * entry / risk
    return realized - fee_R, exit_i


def replay(strat, df, window, cost_bps, simple=False):
    """Return trades: (entry_time, R, direction, risk_mult, setup)."""
    n = len(df)
    highs = df["high"].to_numpy(float); lows = df["low"].to_numpy(float)
    closes = df["close"].to_numpy(float)
    ctx = None if simple else build_context(df, int(strat.p("swing_right", 2)))
    trades = []
    t = window
    while t < n - 1:
        sig = strat.evaluate(df.iloc[max(0, t - window + 1): t + 1])
        if sig is None:
            t += 1
            continue
        if simple:
            res = simulate_trade(sig.direction, float(sig.entry), sig.sl, sig.tp1, sig.tp2,
                                 sig.tp3, highs[t + 1:], lows[t + 1:], closes[t + 1:],
                                 cost_bps=cost_bps)
            if res is None:
                t += 1; continue
            rR, exit_off = res
            exit_abs = t + 1 + exit_off
        else:
            res = simulate_state_machine(t, sig.direction, float(sig.entry), sig.sl,
                                         sig.tp1, sig.tp2, sig.tp3, ctx, cost_bps)
            if res is None:
                t += 1; continue
            rR, exit_abs = res
        mult = float((sig.meta or {}).get("risk_mult", 1.0))
        setup = (sig.meta or {}).get("setup", "?")
        trades.append((df.index[t], rR, sig.direction, mult, setup))
        t = exit_abs + 1
    return trades


def metrics(trades, risk_frac):
    if not trades:
        return dict(n=0, win=0.0, avgR=0.0, ret=0.0, maxdd=0.0, pf=0.0)
    R = np.array([x[1] for x in trades])
    mult = np.array([x[3] for x in trades])
    eq = np.cumprod(1.0 + risk_frac * mult * R)        # quality-scaled sizing
    peak = np.maximum.accumulate(eq)
    maxdd = float((eq / peak - 1.0).min())
    gains = R[R > 0].sum(); losses = -R[R < 0].sum()
    pf = float(gains / losses) if losses > 0 else float("inf")
    return dict(n=len(R), win=float((R > 0).mean()), avgR=float(R.mean()),
                ret=float(eq[-1] - 1.0), maxdd=maxdd, pf=pf)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="*", default=["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT"])
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--window", type=int, default=720)       # >= ~720 to build 1h context
    ap.add_argument("--cost-bps", type=float, default=5.0)
    ap.add_argument("--risk-pct", type=float, default=1.0)
    ap.add_argument("--simple", action="store_true",
                    help="use the repo-standard TP1/2/3 bracket instead of the Part 9 state machine")
    args = ap.parse_args()

    risk_frac = args.risk_pct / 100.0
    bars = int(args.days * 1440 / 5) + args.window + 50
    now = pd.Timestamp.now("UTC")
    cutoff = now - pd.Timedelta(days=args.days)
    mode = "repo bracket" if args.simple else "Part 9 state machine"

    all_tr = []
    print(f"Trap Master backtest — {args.days}d 5m, window={args.window}, cost={args.cost_bps}bps, "
          f"risk={args.risk_pct}%/trade (× quality), exits={mode}\n")
    print(f"{'symbol':<9} {'trades':>6} {'win%':>6} {'avgR':>6} {'return':>8} {'maxDD':>8} {'PF':>6}")
    for sym in args.symbols:
        df = fetch(sym, "5m", bars)
        tr = [x for x in replay(TrapMaster({}), df, args.window, args.cost_bps, args.simple)
              if x[0] >= cutoff]
        m = metrics(tr, risk_frac)
        all_tr += tr
        pf = "inf" if m["pf"] == float("inf") else f"{m['pf']:.2f}"
        print(f"{sym:<9} {m['n']:>6} {m['win']:>5.0%} {m['avgR']:>6.2f} "
              f"{m['ret']:>+8.1%} {m['maxdd']:>+8.1%} {pf:>6}")

    m = metrics(all_tr, risk_frac)
    pf = "inf" if m["pf"] == float("inf") else f"{m['pf']:.2f}"
    print("-" * 56)
    print(f"{'ALL':<9} {m['n']:>6} {m['win']:>5.0%} {m['avgR']:>6.2f} "
          f"{m['ret']:>+8.1%} {m['maxdd']:>+8.1%} {pf:>6}")

    if all_tr:
        print("\nby setup:")
        for s in sorted({x[4] for x in all_tr}):
            sub = [x for x in all_tr if x[4] == s]
            ms = metrics(sub, risk_frac)
            spf = "inf" if ms["pf"] == float("inf") else f"{ms['pf']:.2f}"
            print(f"  {s:<20} {ms['n']:>4} trades  win {ms['win']:>4.0%}  "
                  f"avgR {ms['avgR']:+.2f}  PF {spf}")


if __name__ == "__main__":
    main()
