"""
Trade manager — turns a Signal into live orders and manages the open trade.

Execution model (one position per symbol, mirrors the Pine logic):
  * Market entry for the full position.
  * SL as STOP_MARKET closePosition=true (closes whatever remains).
  * TP1/TP2/TP3 as TAKE_PROFIT_MARKET reduceOnly, quantity split by tp_split.
  * After TP1 fills, optionally move the stop to break-even (entry).
  * When the exchange position returns to flat, cancel leftovers, report, reset.

Position sizing is risk-based: risk_per_trade_pct % of wallet balance divided by
the stop distance gives the quantity, then clamped to exchange filters and an
optional max notional / leverage cap.

`dry_run=True` skips all order calls and simulates SL/TP fills against the latest
closed candle's high/low so you can validate behaviour without sending orders.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from .binance_client import BinanceFutures
from .journal import TradeJournal
from .logger import get_logger
from .strategy_base import Signal
from .telegram import TelegramNotifier

log = get_logger("trade")


@dataclass
class TradeState:
    direction: int
    entry: float
    qty: float
    sl: float
    tp1: float
    tp2: float
    tp3: float
    sl_order_id: Optional[int] = None
    tp_order_ids: list[int] = field(default_factory=list)
    be_active: bool = False
    tp1_done: bool = False
    tp2_done: bool = False
    tp3_done: bool = False


class TradeManager:
    def __init__(self, client: BinanceFutures, cfg, notifier: TelegramNotifier):
        self.client = client
        self.cfg = cfg
        self.notifier = notifier
        self.journal = TradeJournal(cfg.name, cfg.strategy)
        self.state: dict[str, TradeState] = {}

    # ------------------------------------------------------------- helpers #
    def in_position(self, symbol: str) -> bool:
        if symbol in self.state:
            return True
        if self.cfg.dry_run:
            return False
        return abs(self.client.get_position(symbol)["amt"]) > 0

    def _size(self, symbol: str, entry: float, sl: float) -> float:
        risk_dist = abs(entry - sl)
        if risk_dist <= 0:
            return 0.0
        balance = 10_000.0 if self.cfg.dry_run else self.client.wallet_balance("USDT")
        # absolute $ risk if set, else % of balance
        risk_amt = self.cfg.risk_usdt if self.cfg.risk_usdt > 0 else balance * (self.cfg.risk_per_trade_pct / 100.0)
        qty = risk_amt / risk_dist
        # cap by max notional (margin used = notional / leverage)
        notional_cap = self.cfg.max_position_usdt or (balance * self.cfg.leverage)
        qty = min(qty, notional_cap / entry)
        if self.cfg.dry_run:
            return round(qty, 6)
        qty = self.client.round_qty(symbol, qty)
        if qty < self.client.min_qty(symbol):
            log.warning("%s qty %.8f below min %.8f", symbol, qty, self.client.min_qty(symbol))
            return 0.0
        return qty

    # -------------------------------------------------------------- open #
    def open_trade(self, symbol: str, sig: Signal) -> None:
        entry = sig.entry
        qty = self._size(symbol, entry, sig.sl)
        if qty <= 0:
            log.warning("%s skipped — position size resolved to 0", symbol)
            return

        st = TradeState(direction=sig.direction, entry=entry, qty=qty,
                        sl=sig.sl, tp1=sig.tp1, tp2=sig.tp2, tp3=sig.tp3)
        side = "BUY" if sig.direction == 1 else "SELL"
        close_side = "SELL" if sig.direction == 1 else "BUY"

        if not self.cfg.dry_run:
            try:
                self.client.set_leverage(symbol, self.cfg.leverage)
                mo = self.client.market_order(symbol, side, qty)
                if not isinstance(mo, dict) or not mo.get("orderId"):
                    raise RuntimeError(f"market order not accepted: {mo}")
                sl_resp = self.client.stop_market(symbol, close_side, sig.sl, close_position=True)
                st.sl_order_id = sl_resp.get("orderId") if isinstance(sl_resp, dict) else None
                # split qty across TPs
                splits = self.cfg.tp_split
                q1 = self.client.round_qty(symbol, qty * splits[0])
                q2 = self.client.round_qty(symbol, qty * splits[1])
                q3 = self.client.round_qty(symbol, max(0.0, qty - q1 - q2))
                for tp, q in ((sig.tp1, q1), (sig.tp2, q2), (sig.tp3, q3)):
                    if q > 0:
                        resp = self.client.take_profit_market(symbol, close_side, tp, q)
                        oid = resp.get("orderId") if isinstance(resp, dict) else None
                        if oid:
                            st.tp_order_ids.append(oid)
            except Exception as exc:  # noqa: BLE001
                log.error("open_trade %s failed: %s", symbol, exc)
                self.notifier.error(f"{symbol} entry failed: {exc}")
                self.client.cancel_all(symbol)
                return

        self.state[symbol] = st
        balance = 10_000.0 if self.cfg.dry_run else self.client.wallet_balance("USDT")
        notional = qty * entry
        rr = abs(sig.tp1 - entry) / (abs(entry - sig.sl) or 1e-9)
        log.info("OPEN %s %s qty=%s entry=%s sl=%s tp=%s/%s/%s grade=%s",
                 side, symbol, qty, entry, sig.sl, sig.tp1, sig.tp2, sig.tp3, sig.grade)
        self.notifier.trade_open({
            "symbol": symbol, "strategy": self.cfg.strategy, "side": side, "qty": qty,
            "notional": notional, "entry": entry, "sl": sig.sl, "tp1": sig.tp1,
            "tp2": sig.tp2, "tp3": sig.tp3, "grade": sig.grade, "score": sig.score,
            "reason": sig.reason, "leverage": self.cfg.leverage, "balance": balance,
            "rr": rr, "dry": self.cfg.dry_run,
        })
        self.journal.write("OPEN", symbol, side=side, qty=qty, notional_usdt=round(notional, 2),
                           entry=entry, sl=sig.sl, tp1=sig.tp1, tp2=sig.tp2, tp3=sig.tp3,
                           price=entry, grade=sig.grade, score=round(sig.score, 2),
                           reason=sig.reason)

    # ------------------------------------------------------------ manage #
    def manage(self, symbol: str, df: pd.DataFrame) -> None:
        st = self.state.get(symbol)
        if st is None:
            return
        if self.cfg.dry_run:
            self._manage_dry(symbol, st, df)
        else:
            self._manage_live(symbol, st)

    def _manage_live(self, symbol: str, st: TradeState) -> None:
        pos = self.client.get_position(symbol)
        if abs(pos["amt"]) < 1e-12:                      # flat -> trade closed
            self.client.cancel_all(symbol)
            self._report_close(symbol, st, "position flat")
            del self.state[symbol]
            return
        try:
            open_ids = {o["orderId"] for o in self.client.open_orders(symbol)}
        except Exception as exc:  # noqa: BLE001
            log.warning("manage %s open_orders failed: %s", symbol, exc)
            return

        ids = st.tp_order_ids
        upnl = pos.get("upnl", 0.0)
        mark = pos.get("mark", 0.0)
        if len(ids) >= 1 and not st.tp1_done and ids[0] not in open_ids:
            st.tp1_done = True
            self.notifier.trade_event(symbol, "🎯 TP1 hit", upnl)
            self.journal.write("TP1", symbol, side=("BUY" if st.direction == 1 else "SELL"),
                               price=mark or st.tp1, pnl_usdt=round(upnl, 2))
            if self.cfg.use_break_even and not st.be_active:
                self._move_to_break_even(symbol, st)
        if len(ids) >= 2 and not st.tp2_done and ids[1] not in open_ids:
            st.tp2_done = True
            self.notifier.trade_event(symbol, "🎯 TP2 hit", upnl)
            self.journal.write("TP2", symbol, price=mark or st.tp2, pnl_usdt=round(upnl, 2))
        if len(ids) >= 3 and not st.tp3_done and ids[2] not in open_ids:
            st.tp3_done = True
            self.notifier.trade_event(symbol, "🏆 TP3 hit", upnl)
            self.journal.write("TP3", symbol, price=mark or st.tp3, pnl_usdt=round(upnl, 2))

    def _move_to_break_even(self, symbol: str, st: TradeState) -> None:
        close_side = "SELL" if st.direction == 1 else "BUY"
        try:
            if st.sl_order_id:
                self.client.client.futures_cancel_order(symbol=symbol, orderId=st.sl_order_id)
            st.sl_order_id = self.client.stop_market(
                symbol, close_side, st.entry, close_position=True)["orderId"]
            st.be_active = True
            st.sl = st.entry
            self.notifier.trade_event(symbol, "🛡️ SL moved to break-even")
            self.journal.write("BREAK_EVEN", symbol, price=st.entry)
        except Exception as exc:  # noqa: BLE001
            log.error("break-even %s failed: %s", symbol, exc)

    def _manage_dry(self, symbol: str, st: TradeState, df: pd.DataFrame) -> None:
        """Simulate fills against the latest closed candle's high/low."""
        if df is None or len(df) == 0:
            return
        hi = float(df["high"].iloc[-1])
        lo = float(df["low"].iloc[-1])
        long = st.direction == 1
        sl_hit = (lo <= st.sl) if long else (hi >= st.sl)
        tp1 = (hi >= st.tp1) if long else (lo <= st.tp1)
        tp2 = (hi >= st.tp2) if long else (lo <= st.tp2)
        tp3 = (hi >= st.tp3) if long else (lo <= st.tp3)

        def pnl_at(price):
            return (price - st.entry) * st.qty * st.direction

        if sl_hit:
            reason = "break-even stop" if st.be_active else "SL hit"
            self._report_close(symbol, st, reason, pnl=pnl_at(st.sl))
            del self.state[symbol]
            return
        if tp1 and not st.tp1_done:
            st.tp1_done = True
            self.notifier.trade_event(symbol, "🎯 TP1 hit (sim)", pnl_at(st.tp1))
            self.journal.write("TP1", symbol, price=st.tp1, pnl_usdt=round(pnl_at(st.tp1), 2))
            if self.cfg.use_break_even:
                st.be_active = True
                st.sl = st.entry
                self.notifier.trade_event(symbol, "🛡️ SL -> break-even (sim)")
                self.journal.write("BREAK_EVEN", symbol, price=st.entry)
        if tp2 and not st.tp2_done:
            st.tp2_done = True
            self.notifier.trade_event(symbol, "🎯 TP2 hit (sim)", pnl_at(st.tp2))
            self.journal.write("TP2", symbol, price=st.tp2, pnl_usdt=round(pnl_at(st.tp2), 2))
        if tp3 and not st.tp3_done:
            st.tp3_done = True
            self.notifier.trade_event(symbol, "🏆 TP3 hit (sim)", pnl_at(st.tp3))
            self._report_close(symbol, st, "TP3 full exit", pnl=pnl_at(st.tp3))
            del self.state[symbol]

    def _report_close(self, symbol: str, st: TradeState, reason: str, pnl: float | None = None) -> None:
        log.info("CLOSE %s — %s", symbol, reason)
        if pnl is None and not self.cfg.dry_run:
            try:
                inc = self.client.client.futures_income_history(
                    symbol=symbol, incomeType="REALIZED_PNL", limit=10)
                if inc:
                    pnl = sum(float(x["income"]) for x in inc[-3:])
            except Exception:  # noqa: BLE001
                pnl = None
        exit_price = st.sl if ("SL" in reason or "stop" in reason) else st.tp3
        self.journal.write("CLOSE", symbol, side=("BUY" if st.direction == 1 else "SELL"),
                           qty=st.qty, entry=st.entry, price=exit_price,
                           pnl_usdt=(round(pnl, 2) if pnl is not None else ""), reason=reason)
        self.notifier.trade_close(symbol, reason, pnl)
