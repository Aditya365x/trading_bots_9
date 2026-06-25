#!/usr/bin/env python3
"""
OrderFlowSniper - Full Backtest Runner.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import yaml
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bots.strategies.order_flow_sniper import OrderFlowSniper  # noqa: E402
from orderflow_backtest.data_fetcher import fetch_multi_symbol, resolve_period  # noqa: E402
from orderflow_backtest.backtest_engine import BacktestEngine  # noqa: E402
from orderflow_backtest.performance_analyzer import compute_metrics, generate_report  # noqa: E402


COST_BPS = 5.0


def load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def main():
    cfg = load_config(Path(__file__).parent / "config.yaml")
    bc = cfg["backtest"]
    sc = cfg["strategy"]
    sc_1m = cfg["strategy_1m"]
    oc = cfg["output"]

    symbols = bc["symbols"]
    timeframes = bc["timeframes"]
    prim_tf = timeframes["primary"]  # 5m
    sec_tf = timeframes["secondary"]  # 1m
    # backtest across all timeframes in `list` (falls back to primary+secondary)
    tf_list = timeframes.get("list") or sorted({prim_tf, sec_tf})
    days = bc["days"]
    initial_capital = bc["initial_capital"]

    # Resolve the backtest period (default: previous calendar month).
    pcfg = bc.get("period", {}) or {}
    start, end, label = resolve_period(
        mode=pcfg.get("mode", "previous_month"), days=days,
        start=pcfg.get("start"), end=pcfg.get("end"))
    if start is not None:
        period_desc = f"{label}  ({str(start)[:10]} -> {str(end)[:10]} UTC)"
    else:
        period_desc = f"~{days} days ending {pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d')}"

    output_dir = Path(__file__).parent / "output"
    output_dir.mkdir(exist_ok=True)
    cache_dir = str(Path(__file__).parent / "data_cache")

    header = "=" * 100
    print(header, flush=True)
    print("ORDER FLOW SNIPER — COMPLETE BACKTEST", flush=True)
    print(f"Symbols: {symbols}", flush=True)
    print(f"Timeframes: {', '.join(tf_list)}", flush=True)
    print(f"Period: {period_desc}", flush=True)
    print(f"Initial Capital: ${initial_capital:,.2f}", flush=True)
    print(header, flush=True)

    # ---- Step 1: Fetch Data ----
    print("\n--- Fetching Data ---", flush=True)
    all_tfs = tf_list
    data = fetch_multi_symbol(symbols, all_tfs, days=days, start=start, end=end,
                              label=label, cache_dir=cache_dir)
    for (sym, tf), df in data.items():
        print(f"  {sym:<10} {tf:>4}: {len(df):>7} bars  {str(df.index[0])[:19]} -> {str(df.index[-1])[:19]}", flush=True)

    # ---- Step 2: Run Backtest ----
    print("\n--- Running Backtest ---", flush=True)
    all_results: dict[tuple[str, str], list] = {}

    total = len(symbols) * len(all_tfs)
    done = 0
    for sym in symbols:
        for tf in all_tfs:
            key = (sym, tf)
            if key not in data:
                continue
            df = data[key]
            params = sc_1m if tf == "1m" else sc
            strategy = OrderFlowSniper(params)

            engine = BacktestEngine(cfg)   # fresh account per (symbol, timeframe)
            t0 = time.time()
            trades = engine.run(strategy, df, symbol=sym, cost_bps=COST_BPS)
            elapsed = time.time() - t0
            done += 1
            all_results[key] = trades
            m = compute_metrics(trades, initial_capital)

            print(f"  [{done}/{total}] {sym:<10} {tf:>4}:  {m['total_trades']:>4} trades  "
                  f"Win={m['win_rate']:.0%}  AvgR={m['avg_r']:+.4f}R  "
                  f"PF={m['profit_factor']:.2f}  Return={m['total_return']:.2%}  "
                  f"MaxDD={m['max_drawdown']:.2%}  ({elapsed:.1f}s)", flush=True)

    # ---- Step 3: Generate Report ----
    print("\n--- Generating Report ---", flush=True)
    report_text = generate_report(all_results, initial_capital, output_dir)
    with open(output_dir / "report.txt", "w", encoding="utf-8") as f:
        f.write(report_text)

    print(f"\nReport saved to: {output_dir / 'report.txt'}", flush=True)
    print(f"Trades CSV:  {output_dir / 'all_trades.csv'}", flush=True)
    print(f"Charts:      {output_dir / 'equity_curve.png'}", flush=True)
    print(f"             {output_dir / 'drawdown.png'}", flush=True)
    print(f"             {output_dir / 'r_distribution.png'}", flush=True)
    print(header, flush=True)

    # Terminal summary
    all_trades = [t for ts in all_results.values() for t in ts]
    m = compute_metrics(all_trades, initial_capital)
    print("\n=== FINAL SUMMARY ===", flush=True)
    print(f"  Total Trades:     {m['total_trades']}", flush=True)
    print(f"  Win Rate:         {m['win_rate']:.2%}", flush=True)
    print(f"  Profit Factor:    {m['profit_factor']:.4f}", flush=True)
    print(f"  Avg R:            {m['avg_r']:+.4f}R", flush=True)
    print(f"  Total Return:     {m['total_return']:.2%}", flush=True)
    print(f"  Net P&L:          ${m['net_pnl']:+,.2f}", flush=True)
    print(f"  Max Drawdown:     {m['max_drawdown']:.2%}", flush=True)
    print(f"  Sharpe (5m):      {m['sharpe']:.3f}", flush=True)
    print(f"  Long avg R:       {m['avg_r_long']:+.3f}R  ({m['n_long']} trades)", flush=True)
    print(f"  Short avg R:      {m['avg_r_short']:+.3f}R  ({m['n_short']} trades)", flush=True)
    print(header, flush=True)


if __name__ == "__main__":
    main()
