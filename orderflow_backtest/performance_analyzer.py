"""Comprehensive performance metrics, CSV export, and HTML report."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

from .backtest_engine import TradeRecord


def compute_metrics(trades: list[TradeRecord], initial_capital: float = 10000.0) -> dict:
    if not trades:
        return {"total_trades": 0}

    R = np.array([t.r_multiple for t in trades])

    eq = np.cumprod(1.0 + (0.005) * R)
    peak = np.maximum.accumulate(eq)
    dd = eq / peak - 1.0
    maxdd = float(dd.min())

    gains = R[R > 0].sum()
    losses = -R[R < 0].sum()
    pf = float(gains / losses) if losses > 0 else (float("inf") if gains > 0 else 0.0)

    wins = R[R > 0]
    losses_arr = R[R < 0]

    R_long = np.array([t.r_multiple for t in trades if t.direction == 1])
    R_short = np.array([t.r_multiple for t in trades if t.direction == -1])

    total_pnl = sum(t.pnl for t in trades)
    total_fees = sum(t.fee for t in trades)
    net_pnl = total_pnl - total_fees

    sharpe = float(R.mean() / R.std() * np.sqrt(365 * 24 * 60 / 5)) if R.std() > 0 and len(R) > 1 else 0.0
    sortino = float(R.mean() / losses_arr.std() * np.sqrt(365 * 24 * 60 / 5)) if len(losses_arr) > 1 and losses_arr.std() > 0 else 0.0

    return {
        "total_trades": len(R),
        "win_rate": float((R > 0).mean()),
        "avg_r": float(R.mean()),
        "med_r": float(np.median(R)),
        "std_r": float(R.std()),
        "max_r": float(R.max()),
        "min_r": float(R.min()),
        "profit_factor": pf,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": maxdd,
        "total_return": float(eq[-1] - 1.0) if len(eq) > 0 else 0.0,
        "avg_win_r": float(wins.mean()) if len(wins) > 0 else 0.0,
        "avg_loss_r": float(losses_arr.mean()) if len(losses_arr) > 0 else 0.0,
        "total_pnl": total_pnl,
        "total_fees": total_fees,
        "net_pnl": net_pnl,
        "avg_r_long": float(R_long.mean()) if len(R_long) > 0 else 0.0,
        "avg_r_short": float(R_short.mean()) if len(R_short) > 0 else 0.0,
        "n_long": len(R_long),
        "n_short": len(R_short),
        "consecutive_wins": _max_consecutive(R > 0),
        "consecutive_losses": _max_consecutive(R < 0),
    }


def _max_consecutive(condition: np.ndarray) -> int:
    if len(condition) == 0:
        return 0
    count = max_count = 0
    for val in condition:
        count = count + 1 if val else 0
        max_count = max(max_count, count)
    return max_count


def breakdown_by_setup(trades: list[TradeRecord]) -> dict:
    setups = {}
    for t in trades:
        key = "Absorption" if "Absorption" in t.setup else \
              "Exhaustion" if "Exhaustion" in t.setup else \
              "VWAP" if "VWAP" in t.setup or "vwap" in t.setup else \
              "VoidFill" if "Void" in t.setup or "void" in t.setup else \
              "InitBO" if "Initiative" in t.setup or "breakout" in t.setup else \
              "HVN" if "HVN" in t.setup or "hvn" in t.setup else "Other"
        if key not in setups:
            setups[key] = []
        setups[key].append(t)
    result = {}
    for key, ts in setups.items():
        R = np.array([t.r_multiple for t in ts])
        result[key] = {
            "count": len(ts),
            "win_rate": float((R > 0).mean()),
            "avg_r": float(R.mean()),
            "total_r": float(R.sum()),
            "profit_factor": float(R[R > 0].sum() / -R[R < 0].sum()) if R[R < 0].sum() != 0 else float("inf"),
        }
    return result


def trades_to_df(trades: list[TradeRecord]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "time": t.entry_time,
            "exit_time": t.exit_time,
            "symbol": t.symbol,
            "direction": "LONG" if t.direction == 1 else "SHORT",
            "entry": t.entry_price,
            "exit": t.exit_price,
            "size": t.size,
            "pnl": round(t.pnl, 2),
            "pnl_pct": round(t.pnl_pct * 100, 2),
            "fee": round(t.fee, 2),
            "r_multiple": round(t.r_multiple, 3),
            "grade": t.grade,
            "score": t.score,
            "reason": t.setup,
            "exit_reason": t.exit_reason,
            "bars_held": t.bars_held,
        }
        for t in trades
    ])


def save_csv(trades: list[TradeRecord], path: Path):
    df = trades_to_df(trades)
    df.to_csv(path, index=False)


def generate_report(
    all_results: dict[tuple[str, str], list[TradeRecord]],
    initial_capital: float,
    output_dir: Path,
):
    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True)

    all_trades = []
    for key, trades in all_results.items():
        all_trades.extend(trades)
    all_trades.sort(key=lambda t: t.entry_time)

    save_csv(all_trades, output_dir / "all_trades.csv")

    rows = []
    rows.append("=" * 120)
    rows.append("ORDER FLOW SNIPER — COMPREHENSIVE BACKTEST REPORT")
    rows.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    rows.append(f"Initial Capital: ${initial_capital:,.2f}")
    rows.append("=" * 120)
    rows.append("")

    # Aggregate across all
    m = compute_metrics(all_trades, initial_capital)
    rows.append("--- OVERALL PERFORMANCE ---")
    rows.append(f"  Total Trades:        {m['total_trades']}")
    rows.append(f"  Win Rate:            {m['win_rate']:.2%}")
    rows.append(f"  Avg R:               {m['avg_r']:+.4f}R")
    rows.append(f"  Median R:            {m['med_r']:+.4f}R")
    rows.append(f"  Std R:               {m['std_r']:.4f}R")
    rows.append(f"  Best Trade:          {m['max_r']:+.4f}R")
    rows.append(f"  Worst Trade:         {m['min_r']:+.4f}R")
    rows.append(f"  Avg Win:             {m['avg_win_r']:+.4f}R")
    rows.append(f"  Avg Loss:            {m['avg_loss_r']:+.4f}R")
    rows.append(f"  Profit Factor:       {m['profit_factor']:.4f}")
    rows.append(f"  Sharpe (5m):         {m['sharpe']:.3f}")
    rows.append(f"  Sortino (5m):        {m['sortino']:.3f}")
    rows.append(f"  Max Drawdown:        {m['max_drawdown']:.2%}")
    rows.append(f"  Total Return:        {m['total_return']:.2%}")
    rows.append(f"  Net P&L:             ${m['net_pnl']:,.2f}")
    rows.append(f"  Total Fees:          ${m['total_fees']:,.2f}")
    rows.append(f"  Max Consec Wins:     {m['consecutive_wins']}")
    rows.append(f"  Max Consec Losses:   {m['consecutive_losses']}")
    rows.append(f"  Long/Short Ratio:    {m['n_long']}/{m['n_short']} (avg R: {m['avg_r_long']:+.3f}/{m['avg_r_short']:+.3f})")
    rows.append("")

    # By symbol
    rows.append("--- BY SYMBOL (all timeframes combined) ---")
    symbols = sorted(set(k[0] for k in all_results))
    for sym in symbols:
        sym_trades = [t for t in all_trades if t.symbol == sym]
        sym_m = compute_metrics(sym_trades, initial_capital)
        rows.append(f"  {sym:<10}: {sym_m['total_trades']:>4} trades  "
                     f"Win={sym_m['win_rate']:.0%}  "
                     f"AvgR={sym_m['avg_r']:+.4f}R  "
                     f"PF={sym_m['profit_factor']:.2f}  "
                     f"Return={sym_m['total_return']:.2%}  "
                     f"MaxDD={sym_m['max_drawdown']:.2%}  "
                     f"Net=${sym_m['net_pnl']:+,.2f}")
    rows.append("")

    # By timeframe
    rows.append("--- BY TIMEFRAME (all symbols combined) ---")
    tfs = sorted(set(k[1] for k in all_results))
    for tf in tfs:
        tf_trades = [t for t in all_trades if any(k[1] == tf for k in all_results if t.symbol == k[0])]
        tf_trades_sym = []
        for k, ts in all_results.items():
            if k[1] == tf:
                tf_trades_sym.extend(ts)
        tf_m = compute_metrics(tf_trades_sym, initial_capital)
        rows.append(f"  {tf:>4}:  {tf_m['total_trades']:>4} trades  "
                     f"Win={tf_m['win_rate']:.0%}  "
                     f"AvgR={tf_m['avg_r']:+.4f}R  "
                     f"PF={tf_m['profit_factor']:.2f}  "
                     f"Return={tf_m['total_return']:.2%}  "
                     f"MaxDD={tf_m['max_drawdown']:.2%}")
    rows.append("")

    # By symbol + timeframe
    rows.append("--- DETAILED BY SYMBOL + TIMEFRAME ---")
    rows.append(f"{'Symbol':<10} {'TF':>4} {'Trades':>7} {'Win%':>7} {'AvgR':>9} "
                 f"{'Return':>9} {'MaxDD':>9} {'PF':>7}  {'L-R':>8} {'S-R':>8}")
    rows.append("-" * 90)
    for sym in symbols:
        for tf in sorted(tfs):
            key = (sym, tf)
            if key not in all_results:
                continue
            tm = compute_metrics(all_results[key], initial_capital)
            rows.append(f"{sym:<10} {tf:>4} {tm['total_trades']:>7} {tm['win_rate']:>6.0%} "
                         f"{tm['avg_r']:>+9.4f} {tm['total_return']:>+8.2%} "
                         f"{tm['max_drawdown']:>+8.2%} {tm['profit_factor']:>7.2f}  "
                         f"{tm['avg_r_long']:>+8.3f} {tm['avg_r_short']:>+8.3f}")
    rows.append("")

    # Setup breakdown
    rows.append("--- SETUP BREAKDOWN (all trades) ---")
    sb = breakdown_by_setup(all_trades)
    rows.append(f"{'Setup':<20} {'Trades':>7} {'Win%':>7} {'AvgR':>9} {'TotalR':>9} {'PF':>7}")
    rows.append("-" * 65)
    for key in sorted(sb.keys(), key=lambda k: sb[k]["count"], reverse=True):
        s = sb[key]
        rows.append(f"{key:<20} {s['count']:>7} {s['win_rate']:>6.0%} "
                     f"{s['avg_r']:>+9.4f} {s['total_r']:>+9.2f} {s['profit_factor']:>7.2f}")
    rows.append("")

    # Grade breakdown
    rows.append("--- GRADE BREAKDOWN ---")
    grades = {}
    for t in all_trades:
        g = t.grade
        if g not in grades:
            grades[g] = {"trades": [], "total": 0}
        grades[g]["trades"].append(t)
        grades[g]["total"] += 1
    rows.append(f"{'Grade':<8} {'Trades':>7} {'Win%':>7} {'AvgR':>9}")
    rows.append("-" * 35)
    for g in sorted(grades.keys(), reverse=True):
        ts = grades[g]["trades"]
        R_arr = np.array([t.r_multiple for t in ts])
        rows.append(f"{g:<8} {len(ts):>7} {(R_arr > 0).mean():>6.0%} {R_arr.mean():>+9.4f}")
    rows.append("")

    # Top/Bottom trades
    rows.append("--- TOP 5 BEST TRADES ---")
    sorted_by_r = sorted(all_trades, key=lambda t: t.r_multiple, reverse=True)[:5]
    for t in sorted_by_r:
        rows.append(f"  +{t.r_multiple:+.3f}R  {t.symbol}  {str(t.entry_time)[:19]}  {t.setup}")
    rows.append("")
    rows.append("--- TOP 5 WORST TRADES ---")
    sorted_by_r = sorted(all_trades, key=lambda t: t.r_multiple)[:5]
    for t in sorted_by_r:
        rows.append(f"  {t.r_multiple:+.3f}R  {t.symbol}  {str(t.entry_time)[:19]}  {t.setup}")
    rows.append("")

    # Trade list
    rows.append("--- ALL TRADES ---")
    for t in all_trades:
        rows.append(f"  {str(t.entry_time)[:19]}  {t.symbol:<8}  {'LONG' if t.direction==1 else 'SHORT':<5}  "
                     f"{t.r_multiple:+.3f}R  {t.grade}  [{t.setup[:60]}]")

    report_text = "\n".join(rows)
    (output_dir / "report.txt").write_text(report_text, encoding="utf-8")

    if HAS_MPL and len(all_trades) > 0:
        _plot_equity_curve(all_trades, initial_capital, output_dir)
        _plot_drawdown(all_trades, initial_capital, output_dir)
        _plot_r_distribution(all_trades, output_dir)
        _plot_monthly_bars(all_trades, initial_capital, output_dir)

    return report_text


def _plot_equity_curve(all_trades: list[TradeRecord], initial_capital: float, output_dir: Path):
    eq = [initial_capital]
    times = [all_trades[0].entry_time]
    for t in all_trades:
        eq.append(eq[-1] + t.pnl)
        times.append(t.exit_time)
    fig, ax = plt.subplots(figsize=(14, 5))
    ax.plot(times, eq, color="navy", linewidth=1.2)
    ax.fill_between(times, initial_capital, eq, alpha=0.1, color="navy")
    ax.axhline(initial_capital, color="gray", linestyle="--", linewidth=0.7)
    ax.set_title("Equity Curve")
    ax.set_ylabel("Portfolio Value (USDT)")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(output_dir / "equity_curve.png", dpi=150)
    plt.close(fig)


def _plot_drawdown(all_trades: list[TradeRecord], initial_capital: float, output_dir: Path):
    eq = [initial_capital]
    for t in all_trades:
        eq.append(eq[-1] + t.pnl)
    eq_arr = np.array(eq)
    peak = np.maximum.accumulate(eq_arr)
    dd = eq_arr / peak - 1.0
    fig, ax = plt.subplots(figsize=(14, 3))
    ax.fill_between(range(len(dd)), dd * 100, 0, color="crimson", alpha=0.4)
    ax.set_title("Drawdown")
    ax.set_ylabel("Drawdown %")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(output_dir / "drawdown.png", dpi=150)
    plt.close(fig)


def _plot_r_distribution(all_trades: list[TradeRecord], output_dir: Path):
    R = np.array([t.r_multiple for t in all_trades])
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.hist(R, bins=50, color="steelblue", edgecolor="white", alpha=0.8)
    ax.axvline(0, color="red", linestyle="--", linewidth=1)
    ax.set_title(f"R-Multiple Distribution (n={len(R)}, avg={R.mean():.3f})")
    ax.set_xlabel("R Multiple")
    ax.set_ylabel("Frequency")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(output_dir / "r_distribution.png", dpi=150)
    plt.close(fig)


def _plot_monthly_bars(all_trades: list[TradeRecord], initial_capital: float, output_dir: Path):
    try:
        df = pd.DataFrame([
            {"month": t.exit_time.strftime("%Y-%m"), "pnl": t.pnl} for t in all_trades
        ])
        monthly = df.groupby("month")["pnl"].sum()
        fig, ax = plt.subplots(figsize=(10, 4))
        colors = ["green" if v >= 0 else "red" for v in monthly.values]
        ax.bar(range(len(monthly)), monthly.values, color=colors, edgecolor="white")
        ax.set_xticks(range(len(monthly)))
        ax.set_xticklabels(monthly.index, rotation=45, ha="right")
        ax.set_title("Monthly P&L")
        ax.set_ylabel("P&L (USDT)")
        ax.grid(True, alpha=0.3, axis="y")
        fig.tight_layout()
        fig.savefig(output_dir / "monthly_pnl.png", dpi=150)
        plt.close(fig)
    except Exception:
        pass
