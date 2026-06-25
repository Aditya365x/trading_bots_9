"""Event-driven backtest engine with realistic fills, fees, and position mgmt."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

TP_SPLIT = (0.34, 0.33, 0.33)


@dataclass
class TradeRecord:
    symbol: str
    direction: int
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    size: float
    pnl: float
    pnl_pct: float
    fee: float
    setup: str
    grade: str
    score: float
    exit_reason: str
    bars_held: int
    r_multiple: float


def simulate_trade(
    direction: int,
    entry: float,
    sl: float,
    tp1: float,
    tp2: float,
    tp3: float,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    cost_bps: float = 5.0,
) -> Optional[tuple[float, int]]:
    """Simulate a trade against forward price bars. Returns (R_multiple, exit_offset)."""
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    long = direction == 1

    def R(price):
        return (price - entry) / risk * direction

    p1, p2, p3 = TP_SPLIT
    realized = 0.0
    remaining = 1.0
    cur_sl = sl
    t1 = t2 = t3 = False
    exit_i = len(highs) - 1

    for i in range(len(highs)):
        hi, lo = highs[i], lows[i]
        sl_hit = lo <= cur_sl if long else hi >= cur_sl
        if sl_hit:
            realized += remaining * R(cur_sl)
            remaining = 0.0
            exit_i = i
            break
        if not t1 and (hi >= tp1 if long else lo <= tp1):
            realized += p1 * R(tp1)
            remaining -= p1
            t1 = True
            cur_sl = entry
        if not t2 and (hi >= tp2 if long else lo <= tp2):
            realized += p2 * R(tp2)
            remaining -= p2
            t2 = True
        if not t3 and (hi >= tp3 if long else lo <= tp3):
            realized += p3 * R(tp3)
            remaining -= p3
            t3 = True
            remaining = 0.0
            exit_i = i
            break

    if remaining > 0:
        realized += remaining * R(closes[exit_i])

    fee_R = (2.0 * cost_bps / 1e4) * entry / risk
    return realized - fee_R, exit_i


class BacktestEngine:
    def __init__(self, config: dict):
        self.cfg = config["backtest"]
        self.trading_cfg = self.cfg["trading"]
        self.initial_capital = self.cfg["initial_capital"]
        self.commission = self.trading_cfg["commission"]
        self.slippage = self.trading_cfg["slippage"]
        self.risk_pct = self.trading_cfg["risk_per_trade_pct"] / 100.0

        self.equity = self.initial_capital
        self.peak_equity = self.initial_capital
        self.trades: list[TradeRecord] = []
        self.equity_curve: list[dict] = []
        self.window = 400

    def run(self, strategy, df: pd.DataFrame, symbol: str, cost_bps: float = 5.0) -> list[TradeRecord]:
        n = len(df)
        highs = df["high"].to_numpy(float)
        lows = df["low"].to_numpy(float)
        closes = df["close"].to_numpy(float)
        trades: list[TradeRecord] = []
        t = self.window

        while t < n - 1:
            sig = strategy.evaluate(df.iloc[max(0, t - self.window + 1): t + 1])
            if sig is None:
                t += 1
                continue

            res = simulate_trade(
                sig.direction, float(sig.entry), sig.sl, sig.tp1, sig.tp2, sig.tp3,
                highs[t + 1:], lows[t + 1:], closes[t + 1:], cost_bps=cost_bps,
            )
            if res is None:
                t += 1
                continue

            rR, exit_off = res
            exit_idx = t + 1 + exit_off
            if exit_idx >= n:
                exit_idx = n - 1

            risk_amt = self.equity * self.risk_pct
            size = risk_amt / abs(float(sig.entry) - sig.sl) if abs(float(sig.entry) - sig.sl) > 1e-12 else 0.0
            pnl = size * float(sig.entry) * rR * abs(float(sig.entry) - sig.sl) / float(sig.entry) if float(sig.entry) > 0 else 0.0
            # simplified: pnl ≈ risk_amt * rR (R multiples are fractions of risk)
            pnl = risk_amt * rR if rR != 0 else 0.0
            fee = risk_amt * (2.0 * cost_bps / 1e4)

            trade = TradeRecord(
                symbol=symbol,
                direction=sig.direction,
                entry_time=df.index[t],
                exit_time=df.index[min(exit_idx, n - 1)],
                entry_price=float(sig.entry),
                exit_price=closes[exit_idx] if exit_idx < n else closes[-1],
                size=size,
                pnl=pnl,
                pnl_pct=pnl / self.equity if self.equity > 0 else 0.0,
                fee=fee,
                setup=sig.reason,
                grade=sig.grade,
                score=sig.score,
                exit_reason="SL hit" if rR < 0 else "TP hit",
                bars_held=exit_off + 1,
                r_multiple=rR,
            )
            trades.append(trade)

            self.equity += pnl
            self.peak_equity = max(self.peak_equity, self.equity)
            self.equity_curve.append({"time": trade.exit_time, "equity": self.equity, "symbol": symbol})

            t = t + 1 + exit_off + 1

        self.trades.extend(trades)
        return trades
