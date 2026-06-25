"""
Performance analytics — compute trading metrics from the trade journal CSV.

Provides:
  * Sharpe / Sortino ratios, win rate, profit factor, max drawdown
  * Equity curve builder (from trade PnL series)
  * Per-symbol breakdowns and rolling metrics
  * Formatted text report (for console + Telegram)
"""
from __future__ import annotations

import csv as csv_mod
import os
from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np
import pandas as pd

# bars per period for annualisation (crypto trades 24/7)
BARS_PER_YEAR = {
    "1m": 525_600, "5m": 105_120, "15m": 35_040, "30m": 17_520,
    "1h": 8_760, "2h": 4_380, "4h": 2_190, "6h": 1_460, "12h": 730, "1d": 365,
}
_DEFAULT_BARS_YEAR = 8_760  # 1h


@dataclass
class PerfMetrics:
    """Comprehensive set of trading performance metrics."""

    # Returns
    total_return_pct: float = 0.0       # total return (%)
    cagr_pct: float = 0.0                # compound annual growth rate (%)
    avg_return_pct: float = 0.0          # per-trade average return (%)

    # Ratios
    sharpe: float = 0.0
    sortino: float = 0.0
    profit_factor: float = 0.0
    calmar: float = 0.0                  # CAGR / maxDD

    # Risk
    max_drawdown_pct: float = 0.0
    volatility_pct: float = 0.0          # annualised

    # Trade stats
    win_rate: float = 0.0
    n_trades: int = 0
    n_wins: int = 0
    n_losses: int = 0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    avg_r_multiple: float = 0.0          # average R (risk multiple)
    expectancy: float = 0.0              # avg PnL per trade (USDT)

    # Breakdown
    by_symbol: dict[str, dict] | None = None
    best_trade_pnl: float = 0.0
    worst_trade_pnl: float = 0.0

    # Dates
    first_trade: str = ""
    last_trade: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    def short_str(self) -> str:
        """One-line summary for Telegram."""
        return (
            f"Sharpe={self.sharpe:.2f}  RF={self.profit_factor:.2f}  "
            f"Win={self.win_rate:.0%}  {self.n_trades}t  "
            f"DD={self.max_drawdown_pct:.1%}  "
            f"PnL={self.total_return_pct:+.1%}"
        )

    def report(self) -> str:
        """Multi-line formatted report."""
        lines = [
            "📊  PERFORMANCE REPORT",
            "━" * 40,
            f"Trades:        {self.n_trades}  ({self.n_wins}W / {self.n_losses}L)",
            f"Win Rate:      {self.win_rate:.1%}",
            f"Total Return:  {self.total_return_pct:+.2%}",
            f"CAGR:          {self.cagr_pct:+.2%}",
            f"Max DD:        {self.max_drawdown_pct:.2%}",
            f"Sharpe:        {self.sharpe:.2f}",
            f"Sortino:       {self.sortino:.2f}",
            f"Profit Factor: {self.profit_factor:.2f}",
            f"Calmar:        {self.calmar:.2f}",
            f"Avg R:         {self.avg_r_multiple:.2f}",
            f"Expectancy:    {self.expectancy:+.2f} USDT",
            f"Best/Worst:    {self.best_trade_pnl:+.2f} / {self.worst_trade_pnl:+.2f} USDT",
            f"Period:        {self.first_trade}  →  {self.last_trade}",
        ]
        if self.by_symbol:
            lines.append("")
            lines.append("Per Symbol:")
            for sym, m in sorted(self.by_symbol.items()):
                lines.append(
                    f"  {sym:<10}  {m.get('n_trades', 0)}t  "
                    f"Win={m.get('win_rate', 0):.0%}  "
                    f"PnL={m.get('total_return_pct', 0):+.1%}  "
                    f"Sharpe={m.get('sharpe', 0):.2f}"
                )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers for safe numpy extraction
# ---------------------------------------------------------------------------
def _np(v) -> np.ndarray:
    """Convert a pandas Series/array to a plain float64 numpy array."""
    if isinstance(v, np.ndarray):
        return v.astype(float)
    return np.asarray(v, dtype=float)


def load_trades(csv_path: str | None = None) -> pd.DataFrame:
    """
    Load the trade journal CSV into a DataFrame.

    If ``csv_path`` is None, looks for ``logs/trades.csv`` relative to the
    project root (two directories up from ``bots/core/``).
    """
    if csv_path is None:
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        csv_path = os.path.join(base, "logs", "trades.csv")
    if not os.path.exists(csv_path):
        return pd.DataFrame()

    df = pd.read_csv(csv_path, parse_dates=["time_utc"])
    if "time_utc" in df.columns:
        df.set_index("time_utc", inplace=True)
    # Ensure numeric columns
    for col in ["pnl_usdt", "r_multiple", "score", "notional_usdt"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def compute_metrics(
    df: pd.DataFrame,
    interval: str = "1h",
    initial_balance: float = 0.0,
) -> PerfMetrics:
    """
    Compute full performance metrics from a trades DataFrame.

    Args:
        df: Trades DataFrame from ``load_trades()``.
        interval: Bar interval for annualisation (default "1h" for crypto).
        initial_balance: Starting balance (used for % returns). If 0, estimates
                         from the journal.

    Returns:
        PerfMetrics dataclass.
    """
    if df.empty:
        return PerfMetrics()

    ann = BARS_PER_YEAR.get(interval, _DEFAULT_BARS_YEAR)
    meta = PerfMetrics()

    # Filter to CLOSE events for PnL (or use OPEN if no CLOSE exists)
    close_df = df[df["event"] == "CLOSE"].copy()
    if close_df.empty:
        close_df = df[df["pnl_usdt"].notna() & (df["pnl_usdt"] != 0.0)].copy()
    if close_df.empty:
        return meta

    # Extract PnL as plain float64 numpy array
    pnl = _np(close_df["pnl_usdt"])
    meta.n_trades = len(pnl)
    meta.first_trade = str(close_df.index[0]) if not close_df.empty else ""
    meta.last_trade = str(close_df.index[-1]) if not close_df.empty else ""

    # Win / loss stats
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    meta.n_wins = int(wins.shape[0])
    meta.n_losses = int(losses.shape[0])
    meta.win_rate = meta.n_wins / meta.n_trades if meta.n_trades else 0.0
    meta.avg_win_pct = float(wins.mean()) if wins.shape[0] else 0.0
    meta.avg_loss_pct = float(losses.mean()) if losses.shape[0] else 0.0
    meta.best_trade_pnl = float(pnl.max())
    meta.worst_trade_pnl = float(pnl.min())

    # Total return
    total_pnl = float(pnl.sum())
    if initial_balance > 0:
        meta.total_return_pct = total_pnl / initial_balance
    else:
        cum = np.cumsum(pnl)
        denom = max(float(abs(cum).min()), float(abs(total_pnl)), 100.0)
        meta.total_return_pct = total_pnl / denom

    # CAGR
    n_days = (close_df.index[-1] - close_df.index[0]).days if len(close_df) >= 2 else 1
    years = max(n_days / 365.25, 1.0 / 365.25)
    meta.cagr_pct = (1.0 + meta.total_return_pct) ** (1.0 / years) - 1.0

    # Per-trade returns for Sharpe / Sortino
    avg_pnl = float(np.abs(pnl).mean())
    denom = max(avg_pnl, 1e-9)
    trade_returns = pnl / denom
    mu = float(trade_returns.mean())
    sd = float(trade_returns.std()) if trade_returns.shape[0] > 1 else 0.0
    downside = trade_returns[trade_returns < 0]
    dd_sd = float(downside.std()) if downside.shape[0] > 1 else 1.0

    meta.avg_return_pct = mu
    meta.sharpe = (mu / sd * np.sqrt(ann)) if sd > 0 else 0.0
    meta.sortino = (mu / dd_sd * np.sqrt(ann)) if dd_sd > 0 else 0.0
    meta.volatility_pct = sd * np.sqrt(ann)

    # Profit factor
    gross_win = float(wins.sum()) if wins.shape[0] else 0.0
    gross_loss = float(np.abs(losses).sum()) if losses.shape[0] else 1e-9
    meta.profit_factor = gross_win / gross_loss if gross_loss > 0 else 0.0

    # Max drawdown from equity curve
    scale = denom  # reuse for equity scaling
    equity = 1.0 + np.cumsum(pnl) / scale
    peak = np.maximum.accumulate(equity)
    dd = (equity / peak - 1.0)
    meta.max_drawdown_pct = float(dd.min())
    meta.calmar = meta.cagr_pct / abs(meta.max_drawdown_pct) if meta.max_drawdown_pct < 0 else 0.0

    # Average R multiple
    if "r_multiple" in close_df.columns:
        r_vals = _np(close_df["r_multiple"].dropna())
        meta.avg_r_multiple = float(r_vals.mean()) if r_vals.shape[0] else 0.0

    # Expectancy
    meta.expectancy = float(pnl.mean())

    # Per-symbol breakdown
    if "symbol" in close_df.columns:
        sym_stats = {}
        for sym, grp in close_df.groupby("symbol"):
            spnl = _np(grp["pnl_usdt"])
            sw = spnl[spnl > 0]
            total_ret = float(spnl.sum())
            sd_sym = float(spnl.std()) if spnl.shape[0] > 1 else 0.0
            sym_stats[sym] = {
                "n_trades": spnl.shape[0],
                "win_rate": float(sw.shape[0]) / spnl.shape[0] if spnl.shape[0] else 0.0,
                "total_return": total_ret,
                "total_return_pct": total_ret / (abs(total_ret) + 1e-9),
                "sharpe": (float(spnl.mean()) / sd_sym * np.sqrt(ann)) if sd_sym > 0 else 0.0,
                "avg_pnl": float(spnl.mean()),
            }
        meta.by_symbol = sym_stats

    return meta


def equity_curve(df: pd.DataFrame, initial_balance: float = 10_000.0) -> pd.Series:
    """Build an equity curve from trade PnL. Returns Series indexed by trade close time."""
    close_df = df[df["event"] == "CLOSE"].copy()
    if close_df.empty:
        close_df = df[df["pnl_usdt"].notna() & (df["pnl_usdt"] != 0.0)].copy()
    if close_df.empty:
        return pd.Series(dtype=float)

    pnl = close_df["pnl_usdt"].fillna(0.0)
    equity = initial_balance + pnl.cumsum()
    equity.name = "equity"
    return equity


def summary_report(csv_path: str | None = None, interval: str = "1h") -> PerfMetrics:
    """Convenience: load trades + compute metrics + print report."""
    df = load_trades(csv_path)
    if df.empty:
        print("No trades found in journal.")
        return PerfMetrics()
    metrics = compute_metrics(df, interval=interval)
    print(metrics.report())
    return metrics


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    csv_path = os.path.join(base, "logs", "trades.csv")
    if not os.path.exists(csv_path):
        print("No trades.csv found. Creating synthetic demo data...")
        os.makedirs(os.path.dirname(csv_path), exist_ok=True)
        dates = pd.date_range("2026-05-01", periods=200, freq="2h", tz="UTC")
        rows = []
        for i, dt in enumerate(dates):
            rows.append({
                "time_utc": dt,
                "bot": "test_bot",
                "strategy": "precision_sniper",
                "symbol": "BTCUSDT",
                "event": "CLOSE",
                "side": "BUY" if i % 2 == 0 else "SELL",
                "qty": 0.01,
                "notional_usdt": 600.0,
                "entry": 60000.0,
                "sl": 59000.0,
                "tp1": 61000.0,
                "tp2": 62000.0,
                "tp3": 63000.0,
                "price": 60500.0 + (np.random.randn() * 500),
                "pnl_usdt": float(np.random.randn() * 20 + 2),
                "r_multiple": float(np.random.rand() * 3),
                "grade": "A",
                "score": 7.5,
                "reason": "demo",
            })

        fields = list(rows[0].keys())
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            w = csv_mod.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
        print(f"Wrote {len(rows)} synthetic trades to {csv_path}")

    metrics = summary_report(csv_path)
    print("\n" + "=" * 40)
    print("Short summary:")
    print(metrics.short_str())