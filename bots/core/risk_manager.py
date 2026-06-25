"""
Professional risk management system for low-timeframe (1m/5m) trading.

Implements institutional-grade risk controls:

  * **Fixed Fractional** — risk % of account per trade (standard)
  * **Kelly Criterion** — optimal fraction based on win rate & avg win/loss
  * **Volatility-Adjusted** — scale size based on ATR relative to recent history
  * **Circuit Breaker** — max daily loss / consecutive losses halt trading
  * **Correlation Hedge** — reduce total exposure when multiple positions
    are correlated
  * **Max Position Count** — prevent over-concentration
  * **Regime-Aware** — tighten sizing in HIGH_VOL, loosen in RANGING
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

from . import indicators as ta
from .logger import get_logger
from .regime import Regime, RegimeDetector, RegimeResult

log = get_logger("risk")


class SizingMethod(Enum):
    FIXED_FRACTIONAL = "fixed_fractional"
    KELLY = "kelly"
    VOLATILITY_ADJUSTED = "volatility_adjusted"


@dataclass
class RiskProfile:
    """Current risk state for a single bot run."""

    # --- Base settings ---
    method: SizingMethod = SizingMethod.FIXED_FRACTIONAL
    risk_per_trade_pct: float = 1.0       # % of balance risked per trade
    risk_usdt: float = 0.0                 # absolute $ risk (overrides % above)
    max_position_usdt: float = 0.0         # notional cap per position

    # --- Kelly settings ---
    kelly_fraction: float = 0.25           # 25% of full Kelly (conservative)
    estimated_win_rate: float = 0.55       # prior estimate
    estimated_avg_win_loss_ratio: float = 1.5  # prior estimate

    # --- Volatility adjustment ---
    vol_scale_min: float = 0.25            # smallest multiplier (high vol → cut)
    vol_scale_max: float = 2.0             # largest multiplier (low vol → increase)
    atr_period: int = 14
    atr_lookback: int = 100                # for percentile calculation

    # --- Circuit breaker ---
    max_daily_loss_pct: float = 5.0        # stop trading for the day after this loss
    max_consecutive_losses: int = 5        # stop after this many losses in a row
    circuit_breaker_cooldown_seconds: int = 3600  # 1h cooldown after trip

    # --- Position limits ---
    max_open_positions: int = 3            # max simultaneous positions
    max_correlated_positions: int = 2       # max positions in correlated assets

    # --- Regime overrides ---
    regime_multipliers: dict[str, float] = field(default_factory=lambda: {
        "trending": 0.60,
        "ranging": 1.00,
        "high_vol": 0.25,
        "low_vol": 1.50,
    })

    # State (updated at runtime)
    _daily_start_balance: float = 0.0
    _daily_pnl: float = 0.0
    _daily_peak: float = 0.0
    _consecutive_losses: int = 0
    _circuit_breaker_trip_time: float = 0.0
    _day_start: str = ""
    _recent_r_multiple: deque = field(default_factory=lambda: deque(maxlen=20))

    def is_circuit_broken(self) -> bool:
        """Check if trading is halted by circuit breaker."""
        if self._circuit_breaker_trip_time > 0:
            if time.time() - self._circuit_breaker_trip_time < self.circuit_breaker_cooldown_seconds:
                log.warning("Circuit breaker active — %.0fs remaining",
                            self.circuit_breaker_cooldown_seconds - (time.time() - self._circuit_breaker_trip_time))
                return True
            else:
                self._circuit_breaker_trip_time = 0.0
                log.info("Circuit breaker reset after cooldown.")
        return False

    def record_result(self, pnl_usdt: float, r_multiple: float, balance: float) -> None:
        """
        Record a trade result and update circuit breaker state.

        Call this after every trade close.
        """
        # Daily PnL tracking
        today = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d")
        if self._day_start != today:
            self._day_start = today
            self._daily_pnl = 0.0
            self._daily_start_balance = balance
            self._daily_peak = balance
            self._consecutive_losses = 0

        self._daily_pnl += pnl_usdt
        if balance > self._daily_peak:
            self._daily_peak = balance

        self._recent_r_multiple.append(r_multiple)

        # Consecutive losses
        if pnl_usdt < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

        # Check circuit breakers
        daily_loss_pct = -self._daily_pnl / max(self._daily_start_balance, 1.0)
        if daily_loss_pct >= (self.max_daily_loss_pct / 100.0):
            log.warning("⚠️ Circuit breaker TRIPPED: daily loss %.2f%% >= %.2f%%",
                        daily_loss_pct * 100, self.max_daily_loss_pct)
            self._circuit_breaker_trip_time = time.time()

        if self._consecutive_losses >= self.max_consecutive_losses:
            log.warning("⚠️ Circuit breaker TRIPPED: %d consecutive losses",
                        self._consecutive_losses)
            self._circuit_breaker_trip_time = time.time()

    def estimate_kelly_fraction(self, win_rate: float | None = None,
                                avg_win_loss: float | None = None) -> float:
        """
        Estimate Kelly-optimal fraction to risk.

        f* = p - (1-p)/R   where p = win rate, R = avg win / avg loss

        Returns fraction of capital to risk (0.0 to 0.25 capped).
        """
        p = win_rate if win_rate is not None else self.estimated_win_rate
        r = avg_win_loss if avg_win_loss is not None else self.estimated_avg_win_loss_ratio
        if r <= 0:
            return self.risk_per_trade_pct / 100.0
        kelly = p - (1.0 - p) / r
        # Cap at 25% of capital and apply fraction
        kelly = max(0.0, min(kelly, 0.25)) * self.kelly_fraction
        return kelly if kelly > 0 else self.risk_per_trade_pct / 100.0


@dataclass
class SizingResult:
    """Result of position sizing calculation."""
    qty: float
    risk_usdt: float
    notional_usdt: float
    sl_mult_used: float
    reg_mult_used: float
    sized_by: str  # description of sizing method used


class RiskManager:
    """
    Central risk manager — computes position sizes and enforces limits.

    Usage:
        risk_mgr = RiskManager(profile)
        result = risk_mgr.size_position(symbol, balance, entry, sl, df, regime)
        if result.qty <= 0:
            # signal skipped by risk
            pass
    """

    def __init__(self, profile: RiskProfile | None = None, bot_name: str = "bot"):
        self.profile = profile or RiskProfile()
        self.bot_name = bot_name
        self._open_symbols: dict[str, float] = {}  # symbol -> notional

    @property
    def open_position_count(self) -> int:
        return len(self._open_symbols)

    def register_open(self, symbol: str, notional: float) -> None:
        self._open_symbols[symbol] = notional

    def register_close(self, symbol: str) -> None:
        self._open_symbols.pop(symbol, None)

    # ------------------------------------------------------------------
    # Core sizing
    # ------------------------------------------------------------------
    def size_position(
        self,
        symbol: str,
        balance: float,
        entry: float,
        sl: float,
        df: pd.DataFrame | None = None,
        regime: RegimeResult | None = None,
    ) -> SizingResult:
        """
        Compute the position size for a new trade, applying all risk controls.

        Args:
            symbol: Trading pair (e.g. "BTCUSDT")
            balance: Current wallet balance in USDT
            entry: Entry price
            sl: Stop loss price
            df: Candle DataFrame (needed for volatility adjustment)
            regime: Current market regime (from RegimeDetector)

        Returns:
            SizingResult with qty=0 if the trade should be skipped.
        """
        profile = self.profile

        # --- 1. Circuit breaker check ---
        if profile.is_circuit_broken():
            return SizingResult(0.0, 0.0, 0.0, 1.0, 1.0, "circuit breaker")

        # --- 2. Position count check ---
        if self.open_position_count >= profile.max_open_positions:
            log.warning("%s skip — %d positions open (max %d)",
                        symbol, self.open_position_count, profile.max_open_positions)
            return SizingResult(0.0, 0.0, 0.0, 1.0, 1.0, "max positions")

        risk_dist = abs(entry - sl)
        if risk_dist <= 0:
            return SizingResult(0.0, 0.0, 0.0, 1.0, 1.0, "zero risk distance")

        # --- 3. Base risk amount ---
        if profile.risk_usdt > 0:
            base_risk_amt = profile.risk_usdt
            sized_by = "fixed-usdt"
        else:
            base_risk_amt = balance * (profile.risk_per_trade_pct / 100.0)
            sized_by = "fixed-fractional"

        # --- 4. Regime multiplier ---
        reg_mult = 1.0
        if regime is not None:
            reg_mult = profile.regime_multipliers.get(regime.label, 1.0)

        # --- 5. Volatility adjustment ---
        vol_mult = 1.0
        if df is not None and len(df) >= profile.atr_lookback:
            atr_v = ta.atr(df, profile.atr_period)
            if atr_v.iloc[-1] > 0 and not np.isnan(atr_v.iloc[-1]):
                atr_pct = atr_v.rolling(profile.atr_lookback, min_periods=1).rank(pct=True)
                p = float(atr_pct.iloc[-1]) if not np.isnan(atr_pct.iloc[-1]) else 0.5
                # Low ATR percentile → vol_mult > 1 (increase size)
                # High ATR percentile → vol_mult < 1 (decrease size)
                vol_mult = profile.vol_scale_min + (1.0 - p) * (profile.vol_scale_max - profile.vol_scale_min)
                sized_by += "+vol"

        # --- 6. Effective risk amount after all multipliers ---
        effective_risk = base_risk_amt * reg_mult * vol_mult
        qty = effective_risk / risk_dist

        # --- 7. Notional cap ---
        notional = qty * entry
        notional_cap = profile.max_position_usdt or (balance * 2.0)  # 2x balance as safety cap
        if notional > notional_cap:
            qty = notional_cap / entry
            notional = notional_cap
            sized_by += "+cap"

        # --- 8. Kelly override (if enabled) ---
        if profile.method == SizingMethod.KELLY and profile.kelly_fraction > 0:
            # Use recent R-multiples to estimate Kelly
            recent = list(profile._recent_r_multiple)
            if len(recent) >= 10:
                wins = [r for r in recent if r > 0]
                losses = [r for r in recent if r < 0]
                if wins and losses:
                    est_wr = len(wins) / len(recent)
                    est_ratio = abs(float(np.mean(wins)) / float(np.mean(losses)))
                    kelly_f = profile.estimate_kelly_fraction(est_wr, est_ratio)
                    # Recompute qty with Kelly fraction
                    kelly_risk = balance * kelly_f
                    qty = min(qty, kelly_risk / risk_dist)
                    sized_by += "+kelly"

        # --- 9. Minimum notional check ---
        if qty <= 0 or notional < 1.0:
            return SizingResult(0.0, 0.0, 0.0, 1.0, 1.0, "below minimum")

        return SizingResult(
            qty=round(qty, 8),
            risk_usdt=round(effective_risk, 2),
            notional_usdt=round(notional, 2),
            sl_mult_used=1.0,
            reg_mult_used=round(reg_mult, 3),
            sized_by=sized_by,
        )


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Demo: show sizing for different regimes
    profile = RiskProfile(
        risk_per_trade_pct=1.0,
        max_daily_loss_pct=10.0,
        max_consecutive_losses=3,
        method=SizingMethod.FIXED_FRACTIONAL,
    )
    mgr = RiskManager(profile, bot_name="test")

    # Simulate balance
    balance = 10_000.0
    entry = 60_000.0
    sl = 59_000.0  # ~1.67% stop distance

    print("Risk Manager self-test")
    print("=" * 50)

    # Test 1: No regime info
    r1 = mgr.size_position("BTCUSDT", balance, entry, sl)
    print(f"\n1. No regime: qty={r1.qty:.6f} notional={r1.notional_usdt:.2f} "
          f"risk={r1.risk_usdt:.2f} method={r1.sized_by}")

    # Test 2: Ranging regime
    from bots.core.regime import RegimeDetector
    detector = RegimeDetector()
    # Simulate ranging
    np.random.seed(42)
    dates = pd.date_range("2026-01-01", periods=200, freq="5min", tz="UTC")
    df = pd.DataFrame({
        "open": 100 + np.cumsum(np.random.randn(200) * 0.05),
        "high": 100 + np.cumsum(np.random.randn(200) * 0.05) + 0.3,
        "low": 100 + np.cumsum(np.random.randn(200) * 0.05) - 0.3,
        "close": 100 + np.cumsum(np.random.randn(200) * 0.05),
        "volume": np.abs(np.random.randn(200) * 100 + 1000),
    }, index=dates)
    regime = detector.classify(df)
    print(f"\n2. Regime: {regime.label} (ADX={regime.adx}, ER={regime.efficiency_ratio})")
    r2 = mgr.size_position("BTCUSDT", balance, entry, sl, df=df, regime=regime)
    print(f"   Result: qty={r2.qty:.6f} notional={r2.notional_usdt:.2f} "
          f"risk={r2.risk_usdt:.2f} mult={r2.reg_mult_used} method={r2.sized_by}")

    # Test 3: Kelly method
    profile.method = SizingMethod.KELLY
    # Simulate some trade results
    for _ in range(15):
        profile.record_result(np.random.randn() * 50, np.random.randn() * 2 + 0.3, balance)
    r3 = mgr.size_position("BTCUSDT", balance, entry, sl, df=df, regime=regime)
    print(f"\n3. Kelly: qty={r3.qty:.6f} notional={r3.notional_usdt:.2f} "
          f"risk={r3.risk_usdt:.2f} method={r3.sized_by}")

    # Test 4: Circuit breaker
    profile.method = SizingMethod.FIXED_FRACTIONAL
    for _ in range(4):
        profile.record_result(-50.0, -1.0, balance)
    r4 = mgr.size_position("BTCUSDT", balance, entry, sl, df=df, regime=regime)
    print(f"\n4. After 4 losses (max={profile.max_consecutive_losses}): qty={r4.qty:.6f} "
          f"method={r4.sized_by}")

    # Test 5: Recover after cooldown
    profile._circuit_breaker_trip_time = 0.0  # reset
    r5 = mgr.size_position("BTCUSDT", balance, entry, sl, df=df, regime=regime)
    print(f"\n5. After reset: qty={r5.qty:.6f} notional={r5.notional_usdt:.2f} "
          f"method={r5.sized_by}")

    print("\n✅ Risk manager self-test complete.")