"""Trade lifecycle state machine — manages TP1/trailing/CVD-based exits."""
from __future__ import annotations

from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Callable
from datetime import datetime

from .kill_switch import TradeRecord


class TradePhase(Enum):
    IDLE = "idle"
    ENTRY_PENDING = "entry_pending"
    MANAGING = "managing"
    TP1_PARTIAL = "tp1_partial"
    TRAILING = "trailing"
    EXIT_PENDING = "exit_pending"
    CLOSED = "closed"


@dataclass
class ActiveTrade:
    signal_id: str
    direction: int
    entry_price: float
    entry_time: datetime
    initial_sl: float
    initial_tp1: float
    initial_tp2: float
    initial_tp3: float
    size: float

    phase: TradePhase = TradePhase.ENTRY_PENDING
    current_sl: Optional[float] = None
    current_tp: Optional[float] = None
    quantity_closed: float = 0.0
    tp1_hit: bool = False
    tp2_hit: bool = False
    sl_moved_to_be: bool = False

    trail_high: Optional[float] = None
    trail_low: Optional[float] = None

    def __post_init__(self):
        self.current_sl = self.initial_sl
        self.current_tp = self.initial_tp1


class TradeLifecycle:
    def __init__(
        self,
        tp1_close_pct: float = 0.5,
        trail_activation_r: float = 1.0,
        trail_distance_atr: float = 0.5,
        max_trade_bars: int = 20,
        cvd_reversal_threshold: float = 0.30,
    ):
        self.tp1_pct = tp1_close_pct
        self.trail_activation = trail_activation_r
        self.trail_atr = trail_distance_atr
        self.max_bars = max_trade_bars
        self.cvd_reversal_thresh = cvd_reversal_threshold

        self.active_trade: Optional[ActiveTrade] = None
        self.bars_in_trade: int = 0
        self.on_exit_signal: Optional[Callable] = None

    def enter_trade(self, signal, entry_price: float, size: float, timestamp: datetime):
        self.active_trade = ActiveTrade(
            signal_id=f"{timestamp.timestamp()}_{signal.direction}",
            direction=signal.direction,
            entry_price=entry_price,
            entry_time=timestamp,
            initial_sl=signal.sl,
            initial_tp1=signal.tp1,
            initial_tp2=signal.tp2,
            initial_tp3=signal.tp3,
            size=size,
            phase=TradePhase.MANAGING,
        )
        self.bars_in_trade = 0

        if signal.direction == 1:
            self.active_trade.trail_high = entry_price
        else:
            self.active_trade.trail_low = entry_price

    def update(
        self,
        current_price: float,
        current_atr: float,
        current_cvd: float,
        cvd_at_entry: float,
        bar_closed: bool = False
    ) -> Optional[dict]:
        trade = self.active_trade
        if trade is None or trade.phase in [TradePhase.CLOSED]:
            return None

        if bar_closed:
            self.bars_in_trade += 1

        # CHECK 1: Stop Loss
        if trade.direction == 1:
            if current_price <= trade.current_sl:
                return {'type': 'close_full', 'reason': 'SL hit',
                        'price': trade.current_sl}
        else:
            if current_price >= trade.current_sl:
                return {'type': 'close_full', 'reason': 'SL hit',
                        'price': trade.current_sl}

        # CHECK 2: Time Stop
        if self.bars_in_trade >= self.max_bars:
            return {'type': 'close_full', 'reason': 'time stop',
                    'price': current_price}

        # CHECK 3: TP1 (partial close + SL to BE)
        if not trade.tp1_hit:
            if (trade.direction == 1 and current_price >= trade.initial_tp1) or \
               (trade.direction == -1 and current_price <= trade.initial_tp1):
                trade.tp1_hit = True
                trade.current_sl = trade.entry_price
                trade.sl_moved_to_be = True
                return {'type': 'close_partial', 'pct': self.tp1_pct,
                        'reason': 'TP1 hit', 'price': trade.initial_tp1}

        # CHECK 4: TP2
        if not trade.tp2_hit:
            if (trade.direction == 1 and current_price >= trade.initial_tp2) or \
               (trade.direction == -1 and current_price <= trade.initial_tp2):
                trade.tp2_hit = True
                return {'type': 'close_full', 'reason': 'TP2 hit',
                        'price': trade.initial_tp2}

        # CHECK 5: Trailing Stop (after TP1)
        if trade.sl_moved_to_be and trade.tp1_hit:
            if trade.direction == 1:
                trade.trail_high = max(trade.trail_high or current_price, current_price)
                trail_sl = trade.trail_high - self.trail_atr * current_atr
                if trail_sl > trade.current_sl:
                    trade.current_sl = trail_sl
                    return {'type': 'move_sl', 'new_sl': trail_sl,
                            'reason': 'trailing'}
            else:
                trade.trail_low = min(trade.trail_low or current_price, current_price)
                trail_sl = trade.trail_low + self.trail_atr * current_atr
                if trail_sl < trade.current_sl:
                    trade.current_sl = trail_sl
                    return {'type': 'move_sl', 'new_sl': trail_sl,
                            'reason': 'trailing'}

        # CHECK 6: CVD Reversal
        if trade.sl_moved_to_be:
            cvd_change = current_cvd - cvd_at_entry
            if trade.direction == 1 and cvd_change < -abs(cvd_at_entry) * self.cvd_reversal_thresh:
                return {'type': 'close_full', 'reason': 'CVD reversal',
                        'price': current_price}
            if trade.direction == -1 and cvd_change > abs(cvd_at_entry) * self.cvd_reversal_thresh:
                return {'type': 'close_full', 'reason': 'CVD reversal',
                        'price': current_price}

        return None

    def close_trade(self, exit_price: float, reason: str, timestamp: datetime) -> Optional[TradeRecord]:
        if self.active_trade is None:
            return None

        trade = self.active_trade

        if trade.direction == 1:
            pnl_per_unit = exit_price - trade.entry_price
        else:
            pnl_per_unit = trade.entry_price - exit_price

        total_pnl = pnl_per_unit * trade.size
        pnl_pct = pnl_per_unit / trade.entry_price

        record = TradeRecord(
            entry_time=trade.entry_time,
            exit_time=timestamp,
            direction=trade.direction,
            entry_price=trade.entry_price,
            exit_price=exit_price,
            size=trade.size,
            pnl=total_pnl,
            pnl_pct=pnl_pct,
            setup="order_flow",
            grade="A",
            was_stopped=('SL' in reason)
        )

        trade.phase = TradePhase.CLOSED
        self.active_trade = None
        self.bars_in_trade = 0
        return record

    @property
    def in_trade(self) -> bool:
        return self.active_trade is not None and self.active_trade.phase != TradePhase.CLOSED
