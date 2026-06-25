"""
Binance WebSocket stream manager — replaces REST polling with real-time
kline data via the Binance WebSocket API.

Design:
  * Uses ``python-binance``'s ``BinanceSocketManager`` under the hood
  * Connects to **combined kline streams** for all symbols at once
  * Emits closed candle events via an ``asyncio.Queue``
  * Automatic reconnection with exponential backoff
  * Fallback to REST for initial historical data + order operations

Usage:
    async def main():
        mgr = BinanceStreamManager(api_key, api_secret)
        await mgr.connect(["BTCUSDT", "ETHUSDT"], "5m")
        async for event in mgr.stream():
            print(event.symbol, event.close)
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from .binance_client import BinanceFutures
from .logger import get_logger

log = get_logger("websocket")


@dataclass
class CandleEvent:
    """A single closed candle received via WebSocket."""
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int            # ms timestamp
    is_closed: bool             # True for closed candles, False for updates
    taker_buy_volume: float = float("nan")   # aggressive-buy base volume (field "V")

    @classmethod
    def from_kline(cls, symbol: str, k: dict) -> "CandleEvent":
        """Parse a Binance WebSocket kline payload."""
        return cls(
            symbol=symbol,
            open=float(k["o"]),
            high=float(k["h"]),
            low=float(k["l"]),
            close=float(k["c"]),
            volume=float(k["v"]),
            close_time=int(k["T"]),
            is_closed=k.get("x", False),
            taker_buy_volume=float(k.get("V", "nan")),
        )


class BinanceStreamManager:
    """
    Manages WebSocket connections to Binance Futures kline streams.

    This manager scales to many symbols with a single connection by using
    Binance's combined stream URLs:
        wss://fstream.binance.com/stream?streams=btcusdt@kline_5m/ethusdt@kline_5m
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool = True,
        futures_base_url: str = "",
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.futures_base_url = futures_base_url
        self._client: Optional[BinanceFutures] = None
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._running = False
        self._ws = None  # holds the WebSocket connection
        self._reconnect_delay = 1.0  # seconds, doubles on failure

    @property
    def _rest_client(self) -> BinanceFutures:
        """Lazy-init REST client for initial data fetch."""
        if self._client is None:
            self._client = BinanceFutures(
                self.api_key, self.api_secret,
                testnet=self.testnet, futures_base_url=self.futures_base_url,
            )
        return self._client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def connect(self, symbols: list[str], interval: str = "5m") -> None:
        """
        Open the WebSocket stream for the given symbols + interval.

        This is a no-op if already connected with the same parameters.
        """
        if self._running:
            log.info("WebSocket already connected — skipping duplicate connect")
            return

        self._symbols = symbols
        self._interval = interval
        self._running = True
        self._reconnect_delay = 1.0

        log.info("Connecting WebSocket: %s @ %s (%s)", symbols, interval,
                 "testnet" if self.testnet else "live")

        # Start the background listener task
        asyncio.create_task(self._run_loop())

    async def stream(self) -> asyncio.Queue:
        """
        Get the async queue of CandleEvent objects.

        Usage:
            async for event in mgr.stream():
                # process event
        """
        return self._queue

    async def get_candle(self, timeout: float = 10.0) -> Optional[CandleEvent]:
        """Wait for the next candle event (or None on timeout)."""
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def close(self) -> None:
        """Shut down the WebSocket connection."""
        self._running = False
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        log.info("WebSocket connection closed")

    async def fetch_initial_klines(
        self, symbol: str, limit: int = 600
    ) -> pd.DataFrame:
        """Fetch initial historical candles via REST (for warmup)."""
        return self._rest_client.get_klines(symbol, self._interval, limit)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        """Background loop: connect → listen → reconnect on error."""
        while self._running:
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("WebSocket error: %s — reconnecting in %.0fs",
                          exc, self._reconnect_delay)
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, 60.0)

    async def _connect_and_listen(self) -> None:
        """Open a raw WebSocket to the Binance combined stream endpoint."""
        import websockets as ws_lib  # lazy import

        # Build the combined stream URL
        base = "wss://fstream.binance.com" if not self.testnet else \
               "wss://stream.binancefuture.com"
        streams = "/".join(
            f"{sym.lower()}@kline_{self._interval}"
            for sym in self._symbols
        )
        url = f"{base}/stream?streams={streams}"

        log.info("WebSocket connecting to %s ...", url[:80])
        async with ws_lib.connect(url, ping_interval=20, ping_timeout=10) as ws:
            self._ws = ws
            self._reconnect_delay = 1.0  # reset on successful connect
            log.info("WebSocket connected ✅")

            async for raw in ws:
                if not self._running:
                    break
                try:
                    data = json.loads(raw)
                    if data.get("stream") and data.get("data"):
                        k = data["data"].get("k")
                        if k:
                            sym = data["data"]["s"]
                            event = CandleEvent.from_kline(sym, k)
                            if event.is_closed:
                                # Put closed candles in the queue (non-blocking)
                                try:
                                    self._queue.put_nowait(event)
                                except asyncio.QueueFull:
                                    # Drop oldest if queue is full
                                    try:
                                        self._queue.get_nowait()
                                        self._queue.put_nowait(event)
                                    except asyncio.QueueEmpty:
                                        pass
                except json.JSONDecodeError:
                    log.warning("WebSocket: invalid JSON received")
                except Exception as exc:
                    log.error("WebSocket: error processing message: %s", exc)

    async def subscribe_symbols(self, symbols: list[str]) -> None:
        """
        Dynamically subscribe additional symbols (Binance requires
        reconnection, so this flag is for the next reconnect).
        """
        current = set(self._symbols)
        current.update(symbols)
        self._symbols = list(current)
        # Force reconnect to pick up new symbols
        if self._ws is not None:
            await self.close()
            asyncio.create_task(self._run_loop())


# ---------------------------------------------------------------------------
# Legacy sync helper (for backward-compatible tests)
# ---------------------------------------------------------------------------
class SyncCandlePoller:
    """
    Fallback poller that returns CandleEvent-compatible objects via REST.

    Used when WebSocket is unavailable or for testing.
    """

    def __init__(self, client: BinanceFutures, interval: str = "5m"):
        self.client = client
        self.interval = interval
        self._last_ts: dict[str, int] = {}

    def poll(self, symbol: str) -> Optional[CandleEvent]:
        """Poll REST for the latest closed candle. Returns None if no new candle."""
        df = self.client.get_klines(symbol, self.interval, limit=2)
        if len(df) < 2:
            return None
        ts = int(df.index[-1].timestamp())
        if self._last_ts.get(symbol) == ts:
            return None
        self._last_ts[symbol] = ts
        row = df.iloc[-1]
        return CandleEvent(
            symbol=symbol,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
            close_time=ts * 1000,
            is_closed=True,
        )