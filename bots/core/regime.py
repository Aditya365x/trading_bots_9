"""
Market regime classifier — determines the current market state from candle data.

Regimes detected:
  * TRENDING (strong directional move — avoid mean reversion)
  * RANGING  (choppy / sideways — favour mean reversion)
  * HIGH_VOL (extreme volatility — tighten stops or skip)
  * LOW_VOL  (compressed volatility — expect breakout)

Each strategy receives the regime context so it can adapt parameters or
filter signals accordingly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

from . import indicators as ta


class Regime(Enum):
    TRENDING = "trending"
    RANGING = "ranging"
    HIGH_VOL = "high_vol"
    LOW_VOL = "low_vol"


@dataclass
class RegimeConfig:
    """Per-regime parameter overrides that strategies can apply."""

    sl_mult: float | None = None          # override stop-loss multiplier
    tp1_mult: float | None = None         # override TP1 multiplier
    min_score_offset: float = 0.0         # adjustment to score threshold
    skip_signals: bool = False            # completely skip in this regime

    # Default presets
    @classmethod
    def for_regime(cls, regime: Regime) -> "RegimeConfig":
        return _REGIME_PRESETS.get(regime, cls())


# fmt: off
_REGIME_PRESETS = {
    Regime.TRENDING: RegimeConfig(
        sl_mult=1.8, tp1_mult=1.5, min_score_offset=1.0, skip_signals=False,
    ),
    Regime.RANGING: RegimeConfig(
        sl_mult=1.2, tp1_mult=0.8, min_score_offset=0.0, skip_signals=False,
    ),
    Regime.HIGH_VOL: RegimeConfig(
        sl_mult=2.0, tp1_mult=2.0, min_score_offset=2.0, skip_signals=True,
    ),
    Regime.LOW_VOL: RegimeConfig(
        sl_mult=1.0, tp1_mult=1.0, min_score_offset=-0.5, skip_signals=False,
    ),
}
# fmt: on


@dataclass
class RegimeResult:
    regime: Regime
    adx: float = 0.0
    efficiency_ratio: float = 0.0
    atr_percentile: float = 0.0
    rsi: float = 50.0
    config: RegimeConfig = field(default_factory=RegimeConfig)

    @property
    def label(self) -> str:
        return self.regime.value


class RegimeDetector:
    """
    Evaluates market regime from a DataFrame of closed candles.

    Call ``classify(df)`` after new candle data arrives. The detector looks at
    the **last N bars** (default 60) for its calculations.
    """

    def __init__(
        self,
        adx_len: int = 14,
        adx_trend_threshold: float = 25.0,
        er_len: int = 20,
        er_trend_threshold: float = 0.4,
        atr_percentile_len: int = 100,
        vol_high_percentile: float = 0.75,
        vol_low_percentile: float = 0.20,
        rsi_len: int = 14,
    ):
        self.adx_len = adx_len
        self.adx_trend_threshold = adx_trend_threshold
        self.er_len = er_len
        self.er_trend_threshold = er_trend_threshold
        self.atr_percentile_len = atr_percentile_len
        self.vol_high_percentile = vol_high_percentile
        self.vol_low_percentile = vol_low_percentile
        self.rsi_len = rsi_len

    def classify(self, df: pd.DataFrame) -> RegimeResult:
        """
        Determine the current regime from the last closed bar.

        Returns a RegimeResult with the classification and supporting metrics.
        """
        if len(df) < max(self.adx_len * 2, self.atr_percentile_len, 60):
            return RegimeResult(Regime.RANGING)

        close = df["close"]
        atr_val = ta.atr(df, self.adx_len)
        _, _, adx_val = ta.dmi(df, self.adx_len, self.adx_len)
        rsi_val = ta.rsi(close, self.rsi_len)

        # Efficiency Ratio (KAMA-style: net change / total volatility)
        change_ = close.diff(self.er_len).abs()
        volatility_ = close.diff().abs().rolling(self.er_len, min_periods=1).sum()
        er = (change_ / volatility_.replace(0.0, np.nan)).fillna(0.0)

        # ATR percentile (current ATR vs its own history)
        atr_pct = atr_val.rolling(self.atr_percentile_len, min_periods=1).rank(pct=True)

        i = len(df) - 1
        adx = float(adx_val.iloc[i]) if not pd.isna(adx_val.iloc[i]) else 0.0
        er_v = float(er.iloc[i]) if not pd.isna(er.iloc[i]) else 0.0
        atr_p = float(atr_pct.iloc[i]) if not pd.isna(atr_pct.iloc[i]) else 0.5
        rsi = float(rsi_val.iloc[i]) if not pd.isna(rsi_val.iloc[i]) else 50.0

        # Rule-based classification
        is_trending = adx >= self.adx_trend_threshold and er_v >= self.er_trend_threshold
        is_high_vol = atr_p >= self.vol_high_percentile
        is_low_vol = atr_p <= self.vol_low_percentile

        # Priority: volatility extremes override trend/ranging
        if is_high_vol:
            regime = Regime.HIGH_VOL
        elif is_low_vol and not is_trending:
            regime = Regime.LOW_VOL
        elif is_trending:
            regime = Regime.TRENDING
        else:
            regime = Regime.RANGING

        return RegimeResult(
            regime=regime,
            adx=round(adx, 1),
            efficiency_ratio=round(er_v, 3),
            atr_percentile=round(atr_p, 3),
            rsi=round(rsi, 1),
            config=RegimeConfig.for_regime(regime),
        )


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    # Fetch a slice of real data if a client is available, else use synthetic
    try:
        from bots.core.binance_client import BinanceFutures

        client = BinanceFutures("", "", testnet=True)
        df = client.get_klines("BTCUSDT", "1h", 200)
    except Exception:
        # Synthetic data
        np.random.seed(42)
        dates = pd.date_range("2026-01-01", periods=500, freq="h", tz="UTC")
        df = pd.DataFrame(
            {
                "open": 100 + np.cumsum(np.random.randn(500) * 0.1),
                "high": 100 + np.cumsum(np.random.randn(500) * 0.1) + 0.5,
                "low": 100 + np.cumsum(np.random.randn(500) * 0.1) - 0.5,
                "close": 100 + np.cumsum(np.random.randn(500) * 0.1),
                "volume": np.abs(np.random.randn(500) * 100 + 1000),
            },
            index=dates,
        )

    detector = RegimeDetector()
    result = detector.classify(df)
    print(f"Regime: {result.label.upper()}")
    print(f"  ADX:  {result.adx}")
    print(f"  ER:   {result.efficiency_ratio}")
    print(f"  ATR%: {result.atr_percentile}")
    print(f"  RSI:  {result.rsi}")
    print(f"  Config: sl_mult={result.config.sl_mult}, "
          f"tp1_mult={result.config.tp1_mult}, "
          f"skip={result.config.skip_signals}")

    # Bar-by-bar demo
    print("\nBar-by-bar regime history (last 30 bars):")
    for idx in range(len(df) - 30, len(df)):
        sub = df.iloc[: idx + 1]
        r = detector.classify(sub)
        print(f"  {sub.index[-1]} | {r.label:>9} | ADX={r.adx:5.1f} ER={r.efficiency_ratio:.2f}")