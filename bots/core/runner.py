"""
Bot runner — the live loop.

Each cycle, for every symbol:
  * fetch the latest closed candles,
  * if a NEW candle has closed since last check:
        - if flat: ask the strategy for a Signal; if present, open a trade,
        - if in a trade: manage it (break-even / exit reconciliation).

One strategy instance drives all configured symbols. Designed to run forever
under systemd / Docker on AWS; Ctrl-C or SIGTERM exits cleanly.
"""
from __future__ import annotations

import math
import signal
import time

_TF_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200, "1d": 86400,
}

from .binance_client import BinanceFutures
from .config import BotConfig
from .logger import get_logger
from .telegram import TelegramNotifier
from .trade_manager import TradeManager
from ..strategies import get_strategy


class Runner:
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.log = get_logger(cfg.name)
        self.notifier = TelegramNotifier(cfg.telegram_token, cfg.telegram_chat_id, cfg.name)
        self.client = BinanceFutures(cfg.api_key, cfg.api_secret, testnet=cfg.testnet,
                                     futures_base_url=cfg.futures_base_url)
        self.strategy = get_strategy(cfg.strategy)(cfg.params)
        self.tm = TradeManager(self.client, cfg, self.notifier)
        self._last_ts: dict[str, int] = {}
        self._next_fetch: dict[str, float] = {}
        self._interval = _TF_SECONDS.get(cfg.timeframe, 300)
        self._stop = False

    def _handle_signal(self, *_):
        self._stop = True
        self.log.info("Shutdown requested — finishing current cycle...")

    def run(self) -> None:
        signal.signal(signal.SIGINT, self._handle_signal)
        try:
            signal.signal(signal.SIGTERM, self._handle_signal)
        except (ValueError, AttributeError):
            pass

        self.log.info("Starting %s | strategy=%s | symbols=%s | tf=%s | dry_run=%s | env=%s",
                      self.cfg.name, self.cfg.strategy, self.cfg.symbols,
                      self.cfg.timeframe, self.cfg.dry_run, self.cfg.env_label)
        self.notifier.startup(self.cfg.symbols, self.cfg.timeframe, self.cfg.env_label, self.cfg.dry_run)

        while not self._stop:
            for symbol in self.cfg.symbols:
                try:
                    self._process(symbol)
                except Exception as exc:  # noqa: BLE001 — one symbol must not kill the loop
                    self.log.exception("error processing %s: %s", symbol, exc)
                    self.notifier.error(f"{symbol}: {exc}")
            self._sleep(self.cfg.poll_seconds)
        self.log.info("Stopped.")

    def _process(self, symbol: str) -> None:
        # Manage an open trade on every poll (timely break-even / exit). Live
        # management uses position + order state — no klines needed, which keeps
        # the per-IP rate-limit usage low when running many bots.
        if self.tm.in_position(symbol):
            df = self.client.get_klines(symbol, self.cfg.timeframe, self.cfg.lookback_bars) \
                if self.cfg.dry_run else None
            self.tm.manage(symbol, df)
            return

        # Flat: only fetch candles once per closed bar (not every poll).
        now = time.time()
        if now < self._next_fetch.get(symbol, 0.0):
            return
        df = self.client.get_klines(symbol, self.cfg.timeframe, self.cfg.lookback_bars)
        # next candle boundary (+3s buffer so the candle is fully closed)
        self._next_fetch[symbol] = math.ceil(now / self._interval) * self._interval + 3
        if len(df) < 60:
            return
        ts = int(df.index[-1].timestamp())
        if self._last_ts.get(symbol) == ts:
            return
        self._last_ts[symbol] = ts

        sig = self.strategy.evaluate(df)
        if sig is not None:
            self.log.info("%s SIGNAL %s grade=%s reason=%s", symbol, sig.side, sig.grade, sig.reason)
            self.tm.open_trade(symbol, sig)

    def _sleep(self, seconds: int) -> None:
        end = time.time() + seconds
        while time.time() < end and not self._stop:
            time.sleep(0.5)
