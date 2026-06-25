"""
Trap Master standalone backtest engine.

A self-contained backtester for the Trap Master price-action system (modelled on
trap_master_backtestengine.py), but it REUSES the live components so the result
reflects what would actually trade:

  * signals      -> bots.strategies.trap_master.TrapMaster.evaluate()
  * management   -> bots.core.trap_master_manager.TrapMasterManager  (Part 9)
  * structure    -> build_context()  (same causal swing/EMA arrays as live)

It models currency P&L, commission per fill, direction-aware slippage (market
exits/entries get an adverse fill; TP1/TP2 limit fills do not), an equity curve,
drawdown, Sharpe, and a per-setup breakdown. Exits fill at the manager's exact
trigger price (intrabar) — the thing backtrader can't do cleanly.

Step 1 downloads the data locally to parquet; step 2 runs on the local machine.

    venvbots/Scripts/python.exe scripts/trap_master_backtest_engine.py \
        --symbols BTCUSDT ETHUSDT --tf 15m --days 30
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bots.strategies.trap_master import TrapMaster                       # noqa: E402
from bots.core.trap_master_manager import (                              # noqa: E402
    ActiveTrade, TrapMasterManager, build_context)
from orderflow_backtest.data_fetcher import fetch_klines                 # noqa: E402

_BARS_PER_YEAR = {"5m": 105_120, "15m": 35_040, "1h": 8_760}
# exits that cross the book (market) get adverse slippage; TP1/TP2 are limits
_MARKET_REASONS = {"entry", "stop_loss", "breakeven", "trailing_stop", "invalidation",
                   "momentum_loss", "time_stop", "time_stop_tp1", "trend_exhaustion",
                   "end_of_data"}


def _slip(price: float, direction: int, slip_pct: float, is_entry: bool, reason: str) -> float:
    """Direction-aware slippage. Limit TP fills (tp1/tp2) are exact."""
    if not is_entry and reason not in _MARKET_REASONS:
        return price
    s = slip_pct / 100.0
    if is_entry:                       # pay up to get in
        return price * (1 + s) if direction == 1 else price * (1 - s)
    return price * (1 - s) if direction == 1 else price * (1 + s)   # give up on the way out


def run_symbol(df, sparams, capital, commission, slippage, lookback, warmup, right):
    strat = TrapMaster(sparams)
    mgr = TrapMasterManager()
    comm = commission / 100.0
    C = df["close"].to_numpy(float)
    idx = df.index
    n = len(df)
    equity, trades = [], []
    ot = None                          # open-trade bookkeeping

    for i in range(n):
        if ot is not None:
            t = ot["trade"]
            mtm = (C[i] - ot["entry_fill"]) * t.direction * (ot["size"] * t.remaining)
            equity.append((idx[i], capital + mtm))
        else:
            equity.append((idx[i], capital))

        if i + 1 < warmup:
            continue
        window = df.iloc[max(0, i - lookback + 1): i + 1]
        ic = len(window) - 1

        if ot is None:
            sig = strat.evaluate(window)
            if sig is None:
                continue
            risk = abs(sig.entry - sig.sl)
            if risk <= 0:
                continue
            mult = float((sig.meta or {}).get("risk_mult", 1.0))
            size = (capital * 0.01 * mult) / risk          # 1% of equity × quality
            if size <= 0:
                continue
            entry_fill = _slip(sig.entry, sig.direction, slippage, True, "entry")
            capital -= comm * entry_fill * size            # entry fee
            ot = {
                "trade": ActiveTrade(sig.direction, sig.entry, sig.sl, sig.tp1, sig.tp2, sig.tp3),
                "entry_fill": entry_fill, "size": size, "risk": risk, "dir": sig.direction,
                "entry_time": idx[i], "setup": (sig.meta or {}).get("setup", "?"),
                "grade": sig.grade, "score": sig.score, "realized": 0.0, "bars": 0, "exits": [],
            }
        else:
            t = ot["trade"]
            ctx = build_context(window, right)
            for ev in mgr.update(t, ctx, ic):
                fill = _slip(ev["price"], t.direction, slippage, False, ev["reason"])
                part = ot["size"] * ev["pct"]
                pnl = (fill - ot["entry_fill"]) * t.direction * part - comm * fill * part
                capital += pnl
                ot["realized"] += pnl
                ot["exits"].append(f"{ev['reason']}:{ev['pct']:.0%}")
            ot["bars"] = t.bars
            if t.state == "CLOSED":
                trades.append(_close(ot, idx[i], "state_machine"))
                ot = None

    if ot is not None:                                     # liquidate at last close
        t = ot["trade"]; part = ot["size"] * t.remaining
        fill = _slip(C[-1], t.direction, slippage, False, "end_of_data")
        pnl = (fill - ot["entry_fill"]) * t.direction * part - comm * fill * part
        capital += pnl; ot["realized"] += pnl
        trades.append(_close(ot, idx[-1], "end_of_data"))

    return trades, pd.DataFrame(equity, columns=["time", "equity"]).set_index("time"), capital


def _close(ot, exit_time, reason):
    risk_cur = ot["risk"] * ot["size"]
    return {
        "entry_time": ot["entry_time"], "exit_time": exit_time,
        "dir": "long" if ot["dir"] == 1 else "short", "setup": ot["setup"],
        "grade": ot["grade"], "score": ot["score"],
        "pnl": ot["realized"], "pnl_r": (ot["realized"] / risk_cur if risk_cur else 0.0),
        "bars": ot["bars"], "exit_reason": reason, "exits": "|".join(ot["exits"]),
    }


def metrics(trades, equity, init_cap, final_cap, tf):
    if not trades:
        return dict(n=0, win=0.0, avgR=0.0, pf=0.0, ret=0.0, maxdd=0.0, sharpe=0.0,
                    avg_win_r=0.0, avg_loss_r=0.0)
    pnl = np.array([t["pnl"] for t in trades])
    R = np.array([t["pnl_r"] for t in trades])
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    pf = (wins.sum() / abs(losses.sum())) if losses.sum() < 0 else float("inf")
    dd = 0.0
    if not equity.empty:
        eq = equity["equity"].to_numpy(float)
        dd = float((eq / np.maximum.accumulate(eq) - 1.0).min())
    ret_series = equity["equity"].pct_change().dropna() if not equity.empty else pd.Series(dtype=float)
    sharpe = (ret_series.mean() / ret_series.std() * np.sqrt(_BARS_PER_YEAR.get(tf, 35_040))
              if len(ret_series) and ret_series.std() > 0 else 0.0)
    return dict(
        n=len(trades), win=float((pnl > 0).mean()), avgR=float(R.mean()), pf=float(pf),
        ret=(final_cap - init_cap) / init_cap, maxdd=dd, sharpe=float(sharpe),
        avg_win_r=float(R[pnl > 0].mean()) if (pnl > 0).any() else 0.0,
        avg_loss_r=float(R[pnl <= 0].mean()) if (pnl <= 0).any() else 0.0,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="*", default=["BTCUSDT", "ETHUSDT"])
    ap.add_argument("--tf", default="15m")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--capital", type=float, default=10_000)
    ap.add_argument("--commission", type=float, default=0.04)   # % per fill (Binance taker)
    ap.add_argument("--slippage", type=float, default=0.02)     # % per market fill
    ap.add_argument("--lookback", type=int, default=500,        # per-bar window (speed vs history)
                    help="bars of history passed to evaluate() each step")
    args = ap.parse_args()

    sparams = {}                                                # TrapMaster defaults
    lookback = args.lookback
    warmup = 210
    right = 2
    cache = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data_cache")

    print(f"Trap Master standalone engine — {args.tf}, last {args.days}d, "
          f"commission={args.commission}%/fill, slippage={args.slippage}%\n")

    # ---- Step 1: download data locally ----
    print("--- Step 1: downloading data locally ---", flush=True)
    data = {}
    for sym in args.symbols:
        df = fetch_klines(sym, args.tf, days=args.days, cache_dir=cache)
        data[sym] = df
        f = os.path.join(cache, f"{sym}_{args.tf}_{args.days}d.parquet")
        print(f"  {sym} {args.tf}: {len(df)} bars  {str(df.index[0])[:16]} -> {str(df.index[-1])[:16]}"
              f"   cached: {f}", flush=True)

    # ---- Step 2: run locally ----
    print("\n--- Step 2: running backtest ---", flush=True)
    print(f"{'symbol':<9} {'trades':>6} {'win%':>6} {'avgR':>6} {'PF':>6} {'return':>8} {'maxDD':>8} {'Sharpe':>7}")
    all_trades, all_eq = [], []
    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
    os.makedirs(out_dir, exist_ok=True)
    for sym in args.symbols:
        trades, eq, final = run_symbol(data[sym], sparams, args.capital, args.commission,
                                       args.slippage, lookback, warmup, right)
        m = metrics(trades, eq, args.capital, final, args.tf)
        all_trades += [{**t, "symbol": sym} for t in trades]
        all_eq.append(eq.rename(columns={"equity": sym}))
        pf = "inf" if m["pf"] == float("inf") else f"{m['pf']:.2f}"
        print(f"{sym:<9} {m['n']:>6} {m['win']:>5.0%} {m['avgR']:>6.2f} {pf:>6} "
              f"{m['ret']:>+8.1%} {m['maxdd']:>+8.1%} {m['sharpe']:>7.2f}", flush=True)

    # pooled (equal-capital) summary
    if all_trades:
        pnl = np.array([t["pnl"] for t in all_trades])
        R = np.array([t["pnl_r"] for t in all_trades])
        pf = (pnl[pnl > 0].sum() / abs(pnl[pnl <= 0].sum())) if pnl[pnl <= 0].sum() < 0 else float("inf")
        print("-" * 64)
        print(f"{'POOLED':<9} {len(all_trades):>6} {float((pnl>0).mean()):>5.0%} "
              f"{float(R.mean()):>6.2f} {('inf' if pf==float('inf') else f'{pf:.2f}'):>6}")
        print("\nby setup:")
        for s in sorted({t["setup"] for t in all_trades}):
            sub = [t for t in all_trades if t["setup"] == s]
            sp = np.array([t["pnl"] for t in sub]); sr = np.array([t["pnl_r"] for t in sub])
            spf = (sp[sp > 0].sum() / abs(sp[sp <= 0].sum())) if sp[sp <= 0].sum() < 0 else float("inf")
            print(f"  {s:<20} {len(sub):>4} trades  win {float((sp>0).mean()):>4.0%}  "
                  f"avgR {float(sr.mean()):+.2f}  PF {('inf' if spf==float('inf') else f'{spf:.2f}')}")
        tcsv = os.path.join(out_dir, "trap_master_engine_trades.csv")
        pd.DataFrame(all_trades).to_csv(tcsv, index=False)
        print(f"\nTrade log: {tcsv}")
    else:
        print("\n(no trades generated)")


if __name__ == "__main__":
    main()
