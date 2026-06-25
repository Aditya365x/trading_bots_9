"""
Live trade manager for Trap Master — drives the blueprint Part 9 state machine
on the exchange, candle by candle.

Design (single source of truth = TrapMasterManager, so LIVE == BACKTEST):
  * Entry: market order for the full quality-sized qty + ONE protective
    STOP_MARKET(closePosition) at the signal SL. No resting TP orders.
  * Each CLOSED candle: rebuild the causal context, step `TrapMasterManager`
    once, and translate its exit events into reduceOnly MARKET closes (40% at
    TP1, 30% at TP2, the remainder on any discretionary exit). When the manager
    moves the trailing/breakeven stop, cancel + replace the protective stop.
  * If the exchange shows the position already flat (the protective stop fired
    between candles, or a manual close), finalize and report.

Because the manager checks the stop FIRST each bar — exactly as
scripts/backtest_trap_master.py does — the live fills line up with the
backtested behaviour rather than a separate resting-order model.

⚠️ The live order path should be validated on testnet / Binance demo before real
funds. `dry_run=True` is fully simulated (no orders) and is the tested path.
"""
from __future__ import annotations

import pandas as pd

from .logger import get_logger
from .strategy_base import Signal
from .trade_manager import TradeManager
from .trap_master_manager import ActiveTrade, TrapMasterManager, build_context

log = get_logger("trade.trapmaster")


class TrapMasterTradeManager(TradeManager):
    """Part 9 active management. The runner fetches klines while in position
    (see `needs_klines`) so `manage()` can step the state machine each bar."""

    needs_klines = True

    def __init__(self, client, cfg, notifier):
        super().__init__(client, cfg, notifier)
        self.mgr = TrapMasterManager()
        self._right = int((cfg.params or {}).get("swing_right", 2))
        self.tmstate: dict[str, dict] = {}      # symbol -> live trade bookkeeping

    # ------------------------------------------------------------- helpers #
    def in_position(self, symbol: str) -> bool:
        if symbol in self.tmstate:
            return True
        if self.cfg.dry_run:
            return False
        return abs(self.client.get_position(symbol)["amt"]) > 0

    # -------------------------------------------------------------- open #
    def open_trade(self, symbol: str, sig: Signal) -> None:
        risk_mult = float((sig.meta or {}).get("risk_mult", 1.0))
        qty = self._size(symbol, sig.entry, sig.sl, risk_mult)
        if qty <= 0:
            log.warning("%s skipped — size resolved to 0", symbol)
            return

        side = "BUY" if sig.direction == 1 else "SELL"
        close_side = "SELL" if sig.direction == 1 else "BUY"
        stop_id = None
        if not self.cfg.dry_run:
            try:
                self.client.set_leverage(symbol, self.cfg.leverage)
                mo = self.client.market_order(symbol, side, qty)
                if not isinstance(mo, dict) or not mo.get("orderId"):
                    raise RuntimeError(f"market order not accepted: {mo}")
                sl_resp = self.client.stop_market(symbol, close_side, sig.sl, close_position=True)
                stop_id = sl_resp.get("orderId") if isinstance(sl_resp, dict) else None
            except Exception as exc:  # noqa: BLE001
                log.error("open_trade %s failed: %s", symbol, exc)
                self.notifier.error(f"{symbol} entry failed: {exc}")
                self.client.cancel_all(symbol)
                return

        trade = ActiveTrade(sig.direction, sig.entry, sig.sl, sig.tp1, sig.tp2, sig.tp3)
        self.tmstate[symbol] = {
            "trade": trade, "qty": qty, "entry": sig.entry, "dir": sig.direction,
            "close_side": close_side, "stop_id": stop_id, "last_ts": None, "realized": 0.0,
        }
        balance = 10_000.0 if self.cfg.dry_run else self.client.wallet_balance("USDT")
        notional = qty * sig.entry
        rr = abs(sig.tp1 - sig.entry) / (abs(sig.entry - sig.sl) or 1e-9)
        log.info("OPEN %s %s qty=%s entry=%s sl=%s tp=%s/%s/%s grade=%s mult=%s",
                 side, symbol, qty, sig.entry, sig.sl, sig.tp1, sig.tp2, sig.tp3, sig.grade, risk_mult)
        self.notifier.trade_open({
            "symbol": symbol, "strategy": self.cfg.strategy, "side": side, "qty": qty,
            "notional": notional, "entry": sig.entry, "sl": sig.sl, "tp1": sig.tp1,
            "tp2": sig.tp2, "tp3": sig.tp3, "grade": sig.grade, "score": sig.score,
            "reason": sig.reason, "leverage": self.cfg.leverage, "balance": balance,
            "rr": rr, "dry": self.cfg.dry_run,
        })
        self.journal.write("OPEN", symbol, side=side, qty=qty, notional_usdt=round(notional, 2),
                           entry=sig.entry, sl=sig.sl, tp1=sig.tp1, tp2=sig.tp2, tp3=sig.tp3,
                           price=sig.entry, grade=sig.grade, score=round(sig.score, 2),
                           reason=sig.reason)

    # ------------------------------------------------------------ manage #
    def manage(self, symbol: str, df: pd.DataFrame) -> None:
        st = self.tmstate.get(symbol)
        if st is None:
            return
        if self.cfg.dry_run:
            self._manage_dry(symbol, st, df)
        else:
            self._manage_live(symbol, st, df)

    def _new_candle(self, st: dict, df) -> bool:
        if df is None or len(df) == 0:
            return False
        ts = int(df.index[-1].timestamp())
        if st["last_ts"] == ts:
            return False
        st["last_ts"] = ts
        return True

    def _manage_dry(self, symbol: str, st: dict, df) -> None:
        if not self._new_candle(st, df):
            return
        ctx = build_context(df, self._right)
        i = len(df) - 1
        trade, entry, d, qty = st["trade"], st["entry"], st["dir"], st["qty"]
        for ev in self.mgr.update(trade, ctx, i):
            pnl = (ev["price"] - entry) * d * (qty * ev["pct"])
            st["realized"] += pnl
            self.journal.write(ev["reason"].upper(), symbol, price=round(ev["price"], 6),
                               pnl_usdt=round(pnl, 2))
            self.notifier.trade_event(symbol, f"{ev['reason']} ({ev['pct']:.0%}) sim", pnl)
        if trade.state == "CLOSED":
            self._finalize(symbol, st, "state machine (sim)", st["realized"])

    def _manage_live(self, symbol: str, st: dict, df) -> None:
        pos = self.client.get_position(symbol)
        if abs(pos["amt"]) < 1e-12:                  # protective stop fired / closed externally
            self.client.cancel_all(symbol)
            self._finalize(symbol, st, "position flat", None)
            return
        if not self._new_candle(st, df):
            return
        ctx = build_context(df, self._right)
        i = len(df) - 1
        trade, close_side, qty = st["trade"], st["close_side"], st["qty"]
        prev_sl = trade.cur_sl
        for ev in self.mgr.update(trade, ctx, i):
            self._market_reduce(symbol, close_side, qty * ev["pct"])
            self.notifier.trade_event(symbol, f"{ev['reason']} ({ev['pct']:.0%})")
            self.journal.write(ev["reason"].upper(), symbol, price=round(ev["price"], 6))
        if trade.state == "CLOSED":
            self.client.cancel_all(symbol)
            self._finalize(symbol, st, "state machine", None)
            return
        if trade.cur_sl != prev_sl:                  # trail / breakeven moved
            self._replace_stop(symbol, st, trade.cur_sl)

    # ------------------------------------------------------- order helpers #
    def _market_reduce(self, symbol: str, side: str, qty: float) -> None:
        q = self.client.round_qty(symbol, qty)
        if q <= 0:
            return
        try:
            self.client.client.futures_create_order(
                symbol=symbol, side=side, type="MARKET", quantity=q, reduceOnly="true")
        except Exception as exc:  # noqa: BLE001
            log.error("reduce %s %s qty=%s failed: %s", symbol, side, q, exc)

    def _replace_stop(self, symbol: str, st: dict, new_sl: float) -> None:
        close_side = st["close_side"]
        try:
            if st.get("stop_id"):
                self.client.client.futures_cancel_order(symbol=symbol, orderId=st["stop_id"])
            st["stop_id"] = self.client.stop_market(
                symbol, close_side, new_sl, close_position=True)["orderId"]
            self.journal.write("TRAIL_STOP", symbol, price=round(new_sl, 6))
        except Exception as exc:  # noqa: BLE001
            log.error("replace stop %s -> %s failed: %s", symbol, new_sl, exc)

    def _finalize(self, symbol: str, st: dict, reason: str, pnl) -> None:
        if pnl is None and not self.cfg.dry_run:
            try:
                inc = self.client.client.futures_income_history(
                    symbol=symbol, incomeType="REALIZED_PNL", limit=20)
                if inc:
                    pnl = sum(float(x["income"]) for x in inc[-6:])
            except Exception:  # noqa: BLE001
                pnl = None
        log.info("CLOSE %s — %s pnl=%s", symbol, reason, pnl)
        self.journal.write("CLOSE", symbol, side=("BUY" if st["dir"] == 1 else "SELL"),
                           qty=st["qty"], entry=st["entry"],
                           pnl_usdt=(round(pnl, 2) if pnl is not None else ""), reason=reason)
        self.notifier.trade_close(symbol, reason, pnl)
        self.tmstate.pop(symbol, None)
