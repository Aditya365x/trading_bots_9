from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timedelta
import json
from pathlib import Path


@dataclass
class TradeRecord:
    entry_time: datetime
    exit_time: datetime
    direction: int
    entry_price: float
    exit_price: float
    size: float
    pnl: float
    pnl_pct: float
    setup: str
    grade: str
    was_stopped: bool = False


class KillSwitch:
    def __init__(
        self,
        daily_loss_pct: float = 0.03,
        daily_profit_pct: float = 0.03,
        max_consecutive_losses: int = 5,
        max_drawdown_pct: float = 0.15,
        max_trades_per_day: int = 20,
        min_seconds_between_trades: int = 60,
        journal_path: Optional[Path] = None
    ):
        self.daily_loss_limit = daily_loss_pct
        self.daily_profit_target = daily_profit_pct
        self.max_consec_losses = max_consecutive_losses
        self.max_dd_pct = max_drawdown_pct
        self.max_trades = max_trades_per_day
        self.min_trade_interval = timedelta(seconds=min_seconds_between_trades)

        self.session_start_equity: Optional[float] = None
        self.peak_equity: Optional[float] = None
        self.current_equity: Optional[float] = None
        self.session_pnl: float = 0.0
        self.session_pnl_pct: float = 0.0
        self.consecutive_losses: int = 0
        self.trades_today: int = 0
        self.last_trade_time: Optional[datetime] = None
        self.state: str = "ACTIVE"
        self.state_reason: str = ""

        self.trades: list[TradeRecord] = []
        self.journal_path = journal_path or Path("trade_journal.json")
        self.current_session_date: Optional[datetime] = None

    def initialize_session(self, equity: float, timestamp: datetime):
        today = timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
        if self.current_session_date != today:
            self.session_start_equity = equity
            self.peak_equity = equity
            self.current_equity = equity
            self.session_pnl = 0.0
            self.session_pnl_pct = 0.0
            self.consecutive_losses = 0
            self.trades_today = 0
            self.last_trade_time = None
            self.state = "ACTIVE"
            self.state_reason = ""
            self.current_session_date = today
        else:
            self.current_equity = equity
            self.peak_equity = max(self.peak_equity or equity, equity)
            self.session_pnl = equity - self.session_start_equity
            self.session_pnl_pct = self.session_pnl / self.session_start_equity

    def can_enter(self, timestamp: datetime):
        if self.state == "LOCKED":
            return False, f"Trading locked: {self.state_reason}"
        if self.state == "REVIEW":
            return False, f"Manual review required: {self.state_reason}"

        if self.session_pnl_pct >= self.daily_profit_target:
            self._lock(f"Daily profit target reached: {self.session_pnl_pct:.2%}")
            return False, self.state_reason

        if self.session_pnl_pct <= -self.daily_loss_limit:
            self._lock(f"Daily loss limit reached: {self.session_pnl_pct:.2%}")
            return False, self.state_reason

        if self.peak_equity and self.current_equity:
            dd_pct = (self.peak_equity - self.current_equity) / self.peak_equity
            if dd_pct >= self.max_dd_pct:
                self.state = "REVIEW"
                self.state_reason = f"Max drawdown: {dd_pct:.2%}"
                return False, self.state_reason

        if self.consecutive_losses >= self.max_consec_losses:
            self._lock(f"{self.max_consec_losses} consecutive losses")
            return False, self.state_reason

        if self.trades_today >= self.max_trades:
            self._lock(f"Max trades ({self.max_trades}) reached")
            return False, self.state_reason

        if self.last_trade_time:
            elapsed = timestamp - self.last_trade_time
            if elapsed < self.min_trade_interval:
                remaining = self.min_trade_interval - elapsed
                return False, f"Cooldown: {remaining.seconds}s remaining"

        return True, "OK"

    def record_trade(self, trade: TradeRecord):
        self.trades.append(trade)
        self.trades_today += 1
        self.last_trade_time = trade.exit_time

        if trade.pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

        self.current_equity = (self.current_equity or self.session_start_equity) + trade.pnl
        self.peak_equity = max(self.peak_equity or 0, self.current_equity)
        self.session_pnl = self.current_equity - self.session_start_equity
        self.session_pnl_pct = self.session_pnl / self.session_start_equity
        self._save_journal()

    def _lock(self, reason: str):
        self.state = "LOCKED"
        self.state_reason = reason

    def _save_journal(self):
        records = []
        for t in self.trades[-100:]:
            records.append({
                'entry_time': t.entry_time.isoformat(),
                'exit_time': t.exit_time.isoformat(),
                'direction': t.direction,
                'entry_price': t.entry_price,
                'exit_price': t.exit_price,
                'size': t.size,
                'pnl': t.pnl,
                'pnl_pct': t.pnl_pct,
                'setup': t.setup,
                'grade': t.grade,
                'was_stopped': t.was_stopped
            })
        self.journal_path.write_text(json.dumps(records, indent=2))

    def get_stats(self) -> dict:
        if not self.trades:
            return {'total_trades': 0}

        wins = [t for t in self.trades if t.pnl > 0]
        losses = [t for t in self.trades if t.pnl <= 0]

        return {
            'total_trades': len(self.trades),
            'wins': len(wins),
            'losses': len(losses),
            'win_rate': len(wins) / len(self.trades) if self.trades else 0,
            'avg_win': sum(t.pnl for t in wins) / len(wins) if wins else 0,
            'avg_loss': sum(t.pnl for t in losses) / len(losses) if losses else 0,
            'profit_factor': abs(sum(t.pnl for t in wins)) / abs(sum(t.pnl for t in losses)) if losses and sum(t.pnl for t in losses) != 0 else float('inf'),
            'largest_win': max((t.pnl for t in self.trades), default=0),
            'largest_loss': min((t.pnl for t in self.trades), default=0),
            'consecutive_losses': self.consecutive_losses,
            'session_pnl': self.session_pnl,
            'session_pnl_pct': self.session_pnl_pct,
            'state': self.state
        }
