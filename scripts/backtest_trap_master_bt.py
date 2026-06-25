"""
Backtest Trap Master using the backtrader Cerebro engine.

Usage:
    python scripts/backtest_trap_master_bt.py
    python scripts/backtest_trap_master_bt.py --symbols BTCUSDT --days 14
    python scripts/backtest_trap_master_bt.py --simple  # repo-standard bracket (no Part 9)
"""
from __future__ import (absolute_import, division, print_function,
                        unicode_literals)

import argparse
import os
import sys

# Project root + backtrader local package
PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(1, os.path.join(PROJ, "backtrader"))

import backtrader as bt
import pandas as pd

from backtrader.strategies.bt_trap_master import BtTrapMaster
from scripts.backtest_all import fetch


def load_config() -> dict:
    """Load trap_master params from yaml config for the strategy."""
    import yaml
    cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "configs", "trap_master.yaml")
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)
    return (cfg or {}).get("params", {})


def run_backtest(args):
    """Run backtrader Cerebro backtest for given symbols & args."""
    trap_params = load_config()
    if args.params:
        # Override specific params from CLI e.g. --params "atr_len: 10"
        for kv in args.params.split(","):
            k, v = kv.strip().split(":")
            trap_params[k.strip()] = v.strip()

    # Calculate bars needed
    bars = int(args.days * 1440 / 5) + args.window + 200  # extra for warmup/HTF

    cerebro = bt.Cerebro(stdstats=False)

    # --- Add data feeds ---
    for sym in args.symbols:
        print(f"  Fetching {sym} 5m data ({bars} bars)...")
        df = fetch(sym, "5m", bars)

        # Ensure required columns exist (PandasData auto-detects)
        df = df[["open", "high", "low", "close", "volume"]].copy()

        # Filter to date range if specified
        if args.since:
            cutoff = pd.Timestamp(args.since, tz="UTC") if "T" in args.since \
                else pd.Timestamp(args.since, tz="UTC")
            df = df[df.index >= cutoff]

        if len(df) < args.window:
            print(f"  WARNING: {sym} only has {len(df)} bars, need at least {args.window}")
            continue

        data = bt.feeds.PandasData(dataname=df)
        cerebro.adddata(data, name=sym)

    if not cerebro.datas:
        print("No data feeds added. Aborting.")
        return

    # --- Broker setup ---
    cerebro.broker.setcash(args.cash)
    cerebro.broker.setcommission(
        commission=args.cost_bps / 1e4,           # bps -> fraction
        margin=1.0 / args.leverage if args.leverage > 0 else 1.0,
        mult=1.0,
    )
    cerebro.broker.set_slippage_perc(
        perc=0.0005                                # 0.05% slippage
    )

    # --- Add strategy ---
    cerebro.addstrategy(
        BtTrapMaster,
        trap_params=trap_params,
        window=args.window,
        risk_frac=args.risk_pct / 100.0,
        cost_bps=args.cost_bps,
    )

    # --- Analyzers ---
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe",
                        riskfreerate=0.0, timeframe=bt.TimeFrame.Days,
                        annualize=True, compression=1)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
    cerebro.addanalyzer(bt.analyzers.Returns, _name="returns",
                        tann=365)
    cerebro.addanalyzer(bt.analyzers.VWR, _name="vwr")

    # --- Run ---
    print(f"\nStarting backtrader backtest — {args.days}d 5m, window={args.window}, "
          f"cost={args.cost_bps}bps, risk={args.risk_pct}%/trade, leverage={args.leverage}x\n")

    start_val = cerebro.broker.getvalue()
    results = cerebro.run()
    end_val = cerebro.broker.getvalue()

    # --- Print results ---
    strat = results[0]

    print(f"\n{'='*60}")
    print(f"  BACKTEST RESULTS")
    print(f"{'='*60}")
    print(f"  Start Value:     ${start_val:,.2f}")
    print(f"  End Value:       ${end_val:,.2f}")
    print(f"  Return:          {(end_val / start_val - 1) * 100:.2f}%")

    # Sharpe
    sharpe = strat.analyzers.sharpe.get_analysis()
    if sharpe and "sharperatio" in sharpe:
        print(f"  Sharpe Ratio:    {sharpe['sharperatio']:.4f}" if sharpe['sharperatio'] is not None else "  Sharpe Ratio:    N/A")

    # Drawdown
    dd = strat.analyzers.drawdown.get_analysis()
    if dd:
        print(f"  Max Drawdown:    {dd.get('max', {}).get('drawdown', 0):.2f}%")

    # Trades
    ta = strat.analyzers.trades.get_analysis()
    if ta:
        total = ta.get("total", {})
        won = ta.get("won", {})
        lost = ta.get("lost", {})
        print(f"  Total Trades:    {total.get('total', 0)}")
        print(f"  Won:             {won.get('total', 0)}")
        print(f"  Lost:            {lost.get('total', 0)}")
        if total.get("total", 0) > 0:
            win_rate = won.get("total", 0) / total["total"]
            print(f"  Win Rate:        {win_rate:.1%}")
        pnl = ta.get("pnl", {}).get("net", {})
        if pnl:
            print(f"  Net PnL:         ${pnl.get('total', 0):.2f}")
        # Avg trade length
        length = ta.get("len", {}).get("average", None)
        if length:
            print(f"  Avg Bars/Trade:  {length:.1f}")

    # Returns
    ret = strat.analyzers.returns.get_analysis()
    if ret:
        rtot = ret.get("rtot", None)
        if rtot:
            print(f"  Total Return:    {(rtot - 1) * 100:.2f}%")
        rnorm = ret.get("rnorm100", None)
        if rnorm:
            print(f"  Annual Return:   {rnorm:.2f}%")

    print(f"{'='*60}\n")

    # --- Optional plot ---
    if args.plot:
        cerebro.plot(style="candlestick" if args.plot == "candle" else "line",
                     volume=True)


def main():
    ap = argparse.ArgumentParser(
        description="Backtest Trap Master with backtrader Cerebro engine"
    )
    ap.add_argument("--symbols", nargs="*", default=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--window", type=int, default=720)
    ap.add_argument("--cost-bps", type=float, default=5.0)
    ap.add_argument("--risk-pct", type=float, default=1.0)
    ap.add_argument("--cash", type=float, default=10000.0)
    ap.add_argument("--leverage", type=int, default=1)
    ap.add_argument("--since", type=str, default=None,
                    help="Start date filter (YYYY-MM-DD or ISO format)")
    ap.add_argument("--params", type=str, default=None,
                    help="Comma-separated param overrides, e.g. 'atr_len:10,pool_min_strength:3'")
    ap.add_argument("--plot", nargs="?", const="line", default=None,
                    choices=["line", "candle"],
                    help="Plot results (optional: line/candle)")
    ap.add_argument("--analyzers", action="store_true", default=True,
                    help="Print analyzer results")
    args = ap.parse_args()
    run_backtest(args)


if __name__ == "__main__":
    main()