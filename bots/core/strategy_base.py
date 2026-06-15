"""
Strategy contract.

A strategy is a pure function of the candle DataFrame: given closed candles it
returns a Signal for the most-recently-closed bar, or None. It performs NO I/O
and places NO orders — the runner + trade manager handle execution. This keeps
every strategy testable and backtestable in isolation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd


@dataclass
class Signal:
    direction: int                 # 1 = long, -1 = short
    entry: float                   # reference entry (last close)
    sl: float
    tp1: float
    tp2: float
    tp3: float
    grade: str = "—"
    score: float = 0.0
    reason: str = ""
    meta: dict[str, Any] | None = None

    @property
    def side(self) -> str:
        return "BUY" if self.direction == 1 else "SELL"


# Shared risk presets used by several strategies: SL ×ATR, TP1/TP2/TP3 ×Risk.
RISK_PRESETS = {
    "Conservative": (2.5, 1.0, 2.0, 4.0),
    "Balanced":     (1.5, 1.0, 2.0, 3.0),
    "Aggressive":   (1.0, 1.5, 2.5, 4.0),
    "Scalping":     (0.8, 0.8, 1.5, 2.0),
}


def resolve_preset(preset: str, custom: tuple | None = None) -> tuple[float, float, float, float]:
    if preset == "Custom" and custom:
        return custom
    return RISK_PRESETS.get(preset, RISK_PRESETS["Balanced"])


def atr_sl_tp(direction: int, entry: float, atr_val: float,
              sl_mult: float, tp1: float, tp2: float, tp3: float):
    """SL = entry -/+ atr*sl_mult; TPs at R-multiples of that risk distance."""
    risk = atr_val * sl_mult
    sign = 1.0 if direction == 1 else -1.0
    sl = entry - sign * risk
    return sl, entry + sign * risk * tp1, entry + sign * risk * tp2, entry + sign * risk * tp3


class Strategy:
    """Base class. Subclasses implement evaluate()."""

    name = "base"

    def __init__(self, params: dict | None = None):
        self.params = params or {}

    def p(self, key: str, default: Any) -> Any:
        return self.params.get(key, default)

    def evaluate(self, df: pd.DataFrame) -> Optional[Signal]:
        raise NotImplementedError
