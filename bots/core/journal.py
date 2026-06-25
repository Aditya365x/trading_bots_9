"""
Trade journal — append-only CSV record of everything the bots do.

Every event (entry, TP1/TP2/TP3, break-even, SL, close) is written as one row to
logs/trades.csv with prices, quantities, PnL and reason. This is your permanent
record for reviewing / backtesting bot behaviour later.

CSV is append-only and safe to open in Excel / pandas:
    import pandas as pd; pd.read_csv("logs/trades.csv")
"""
from __future__ import annotations

import csv
import os
from datetime import datetime, timezone

import pandas as pd

_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "logs")
_PATH = os.path.join(_DIR, "trades.csv")

_FIELDS = [
    "time_utc", "bot", "strategy", "symbol", "event", "side",
    "qty", "notional_usdt", "entry", "sl", "tp1", "tp2", "tp3",
    "price", "pnl_usdt", "r_multiple", "grade", "score", "reason",
]


class TradeJournal:
    def __init__(self, bot: str, strategy: str):
        self.bot = bot
        self.strategy = strategy
        os.makedirs(_DIR, exist_ok=True)
        if not os.path.exists(_PATH):
            with open(_PATH, "w", newline="", encoding="utf-8") as fh:
                csv.DictWriter(fh, fieldnames=_FIELDS).writeheader()

    def write(self, event: str, symbol: str, **kw) -> None:
        row = {k: "" for k in _FIELDS}
        row.update({
            "time_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "bot": self.bot, "strategy": self.strategy,
            "symbol": symbol, "event": event,
        })
        for k, v in kw.items():
            if k in row:
                row[k] = v
        try:
            with open(_PATH, "a", newline="", encoding="utf-8") as fh:
                csv.DictWriter(fh, fieldnames=_FIELDS).writerow(row)
        except Exception:  # journaling must never break trading
            pass

    # ------------------------------------------------------------------
    # Read & summary
    # ------------------------------------------------------------------
    def read(self) -> pd.DataFrame:
        """Return the journal as a pandas DataFrame."""
        try:
            df = pd.read_csv(_PATH, parse_dates=["time_utc"])
            if "time_utc" in df.columns:
                df.set_index("time_utc", inplace=True)
            return df
        except Exception:
            return pd.DataFrame()

    def summary(self) -> str:
        """Return a short summary string of recent activity."""
        df = self.read()
        if df.empty:
            return "No trades recorded yet."
        close_df = df[df["event"] == "CLOSE"]
        n_closes = len(close_df)
        if "pnl_usdt" in df.columns:
            pnl_s = pd.to_numeric(df["pnl_usdt"], errors="coerce").fillna(0.0)
            total_pnl = float(pnl_s.sum())
            n_wins = int((pnl_s > 0).sum())
            n_losses = int((pnl_s < 0).sum())
        else:
            total_pnl = 0.0
            n_wins = 0
            n_losses = 0
        return (f"📊 {n_closes} closed trades | "
                f"PnL: {total_pnl:+.2f} USDT | "
                f"W/L: {n_wins}/{n_losses} | "
                f"Total events: {len(df)}")
