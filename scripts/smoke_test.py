"""
Offline smoke test — no network, no keys. Generates synthetic 5m candles and
runs every indicator + all 9 strategies + the dry-run trade manager to verify
nothing throws. Run:  ./venvbots/Scripts/python.exe scripts/smoke_test.py
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bots.core import indicators as ta          # noqa: E402
from bots.core.config import BotConfig          # noqa: E402
from bots.core.strategy_base import Signal       # noqa: E402
from bots.core.telegram import TelegramNotifier  # noqa: E402
from bots.core.trade_manager import TradeManager  # noqa: E402
from bots.strategies import available, get_strategy  # noqa: E402


def make_df(n=800, seed=7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    # trend + noise + a couple of regime shifts so signals can fire
    drift = np.concatenate([np.full(n // 2, 0.0008), np.full(n - n // 2, -0.0008)])
    rets = drift + rng.normal(0, 0.004, n)
    close = 30000 * np.exp(np.cumsum(rets))
    high = close * (1 + np.abs(rng.normal(0, 0.0025, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.0025, n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    vol = rng.uniform(50, 500, n)
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    return pd.DataFrame({"open": open_, "high": np.maximum.reduce([open_, high, close]),
                         "low": np.minimum.reduce([open_, low, close]),
                         "close": close, "volume": vol}, index=idx)


def test_indicators(df):
    assert ta.ema(df["close"], 21).notna().any()
    assert ta.atr(df, 14).notna().any()
    assert ta.rsi(df["close"], 14).between(0, 100).any()
    macd_l, sig, hist = ta.macd(df["close"])
    assert hist.notna().any()
    p, m, a = ta.dmi(df, 14, 14)
    assert a.notna().any()
    assert ta.kama(df["close"], 13, 2, 30).notna().any()
    assert ta.vwap_session(df).notna().any()
    assert ta.pivot_high(df["high"], 5, 5).notna().any()
    print("  indicators: OK")


def test_strategies(df):
    fired = []
    for name in available():
        strat = get_strategy(name)({})
        sig = strat.evaluate(df)
        assert sig is None or isinstance(sig, Signal)
        # also force a few sub-windows to exercise more bars
        hits = 0
        for end in range(300, len(df), 23):
            s = strat.evaluate(df.iloc[:end])
            if isinstance(s, Signal):
                hits += 1
                assert s.direction in (1, -1)
                assert s.sl != s.entry
        fired.append((name, hits))
        print(f"  {name:24s}: OK ({hits} signals across windows)")
    return fired


def test_trade_manager(df):
    cfg = BotConfig()
    cfg.dry_run = True
    cfg.symbols = ["BTCUSDT"]
    notifier = TelegramNotifier("", "", "smoke")     # disabled
    tm = TradeManager(client=None, cfg=cfg, notifier=notifier)
    sig = Signal(1, float(df["close"].iloc[-1]),
                 float(df["close"].iloc[-1]) * 0.99,
                 float(df["close"].iloc[-1]) * 1.01,
                 float(df["close"].iloc[-1]) * 1.02,
                 float(df["close"].iloc[-1]) * 1.03, "A", 8.0, "test")
    tm.open_trade("BTCUSDT", sig)
    assert "BTCUSDT" in tm.state
    tm.manage("BTCUSDT", df)             # simulate against last candle
    print("  trade_manager (dry-run): OK")


if __name__ == "__main__":
    df = make_df()
    print(f"Synthetic data: {len(df)} candles "
          f"({df['close'].min():.0f}-{df['close'].max():.0f})")
    test_indicators(df)
    test_strategies(df)
    test_trade_manager(df)
    print("\nALL SMOKE TESTS PASSED")
