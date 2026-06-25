"""
Binance USDT-M Futures client wrapper around python-binance.

Responsibilities:
  * Connect to testnet or live futures.
  * Fetch klines as a clean pandas DataFrame (closed candles only).
  * Cache symbol filters (tick size / step size / min notional) and round
    prices & quantities so orders are never rejected for precision.
  * Thin helpers for balance, leverage, market entry, bracket SL/TP, position
    state and order cancellation.
"""
from __future__ import annotations

import math
import time
from decimal import Decimal

import pandas as pd
from binance.client import Client
from binance.exceptions import BinanceAPIException

from .logger import get_logger

log = get_logger("binance")

_TF_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000,
    "6h": 21_600_000, "8h": 28_800_000, "12h": 43_200_000, "1d": 86_400_000,
}


class BinanceFutures:
    def __init__(self, api_key: str, api_secret: str, testnet: bool = True,
                 futures_base_url: str = ""):
        self.testnet = testnet
        self.futures_base_url = futures_base_url
        if futures_base_url:
            # Binance Demo Futures (or any custom futures endpoint). We init the
            # client as non-testnet and override only the futures base URL.
            self.client = Client(api_key, api_secret, testnet=False)
            self.client.FUTURES_URL = futures_base_url.rstrip("/")
            label = f"DEMO ({futures_base_url})"
        else:
            self.client = Client(api_key, api_secret, testnet=testnet)
            label = "TESTNET" if testnet else "LIVE"
        self._filters: dict[str, dict] = {}
        log.info("Connected to Binance Futures %s", label)

    # ------------------------------------------------------------------ data #
    def get_klines(self, symbol: str, interval: str, limit: int = 600) -> pd.DataFrame:
        """Return closed candles as a DataFrame indexed by close time (UTC)."""
        raw = self.client.futures_klines(symbol=symbol, interval=interval, limit=limit)
        cols = ["open_time", "open", "high", "low", "close", "volume",
                "close_time", "qav", "trades", "tbav", "tqav", "ignore"]
        df = pd.DataFrame(raw, columns=cols)
        for c in ["open", "high", "low", "close", "volume", "tbav"]:
            df[c] = df[c].astype(float)
        # Real aggressive-BUY volume (taker hit the ask). Order-flow strategies
        # use this for true per-bar delta / CVD; others simply ignore the column.
        df["taker_buy_volume"] = df["tbav"]
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df.set_index("open_time", inplace=True)
        # Drop the still-forming last candle so we only act on closed bars.
        df = df.iloc[:-1]
        return df[["open", "high", "low", "close", "volume", "taker_buy_volume"]]

    # ----------------------------------------------------------- precision #
    def _load_filters(self, symbol: str) -> dict:
        if symbol in self._filters:
            return self._filters[symbol]
        info = self.client.futures_exchange_info()
        for s in info["symbols"]:
            if s["symbol"] == symbol:
                f = {"tick_size": None, "step_size": None, "min_qty": 0.0, "min_notional": 0.0}
                for filt in s["filters"]:
                    if filt["filterType"] == "PRICE_FILTER":
                        f["tick_size"] = float(filt["tickSize"])
                    elif filt["filterType"] == "LOT_SIZE":
                        f["step_size"] = float(filt["stepSize"])
                        f["min_qty"] = float(filt["minQty"])
                    elif filt["filterType"] in ("MIN_NOTIONAL", "NOTIONAL"):
                        f["min_notional"] = float(filt.get("notional", filt.get("minNotional", 0.0)))
                f["price_prec"] = _decimals(f["tick_size"])
                f["qty_prec"] = _decimals(f["step_size"])
                self._filters[symbol] = f
                return f
        raise ValueError(f"Symbol {symbol} not found in exchange info")

    def round_price(self, symbol: str, price: float) -> float:
        f = self._load_filters(symbol)
        tick = f["tick_size"]
        return round(math.floor(price / tick) * tick, f["price_prec"]) if tick else price

    def round_qty(self, symbol: str, qty: float) -> float:
        f = self._load_filters(symbol)
        step = f["step_size"]
        return round(math.floor(qty / step) * step, f["qty_prec"]) if step else qty

    def min_qty(self, symbol: str) -> float:
        return self._load_filters(symbol)["min_qty"]

    def min_notional(self, symbol: str) -> float:
        return self._load_filters(symbol)["min_notional"]

    # -------------------------------------------------------------- account #
    def wallet_balance(self, asset: str = "USDT") -> float:
        for b in self.client.futures_account_balance():
            if b["asset"] == asset:
                return float(b["balance"])
        return 0.0

    def set_leverage(self, symbol: str, leverage: int) -> None:
        try:
            self.client.futures_change_leverage(symbol=symbol, leverage=leverage)
        except BinanceAPIException as exc:
            log.warning("set_leverage %s -> %s: %s", symbol, leverage, exc.message)

    def get_position(self, symbol: str) -> dict:
        """Return position info. amt>0 long, <0 short, 0 flat."""
        for p in self.client.futures_position_information(symbol=symbol):
            if p["symbol"] == symbol:
                return {
                    "amt": float(p["positionAmt"]),
                    "entry": float(p["entryPrice"]),
                    "upnl": float(p.get("unRealizedProfit", 0.0) or 0.0),
                    "mark": float(p.get("markPrice", 0.0) or 0.0),
                }
        return {"amt": 0.0, "entry": 0.0, "upnl": 0.0, "mark": 0.0}

    def mark_price(self, symbol: str) -> float:
        return float(self.client.futures_mark_price(symbol=symbol)["markPrice"])

    # --------------------------------------------------------------- orders #
    def market_order(self, symbol: str, side: str, qty: float) -> dict:
        return self.client.futures_create_order(
            symbol=symbol, side=side, type="MARKET", quantity=qty)

    def stop_market(self, symbol: str, side: str, stop_price: float, close_position: bool = True,
                    qty: float | None = None) -> dict:
        params = dict(symbol=symbol, side=side, type="STOP_MARKET",
                      stopPrice=self.round_price(symbol, stop_price), workingType="MARK_PRICE")
        if close_position:
            params["closePosition"] = "true"
        else:
            params["quantity"] = qty
            params["reduceOnly"] = "true"
        return self.client.futures_create_order(**params)

    def take_profit_market(self, symbol: str, side: str, stop_price: float, qty: float) -> dict:
        return self.client.futures_create_order(
            symbol=symbol, side=side, type="TAKE_PROFIT_MARKET",
            stopPrice=self.round_price(symbol, stop_price),
            quantity=qty, reduceOnly="true", workingType="MARK_PRICE")

    def open_orders(self, symbol: str) -> list:
        return self.client.futures_get_open_orders(symbol=symbol)

    def cancel_all(self, symbol: str) -> None:
        try:
            self.client.futures_cancel_all_open_orders(symbol=symbol)
        except BinanceAPIException as exc:
            log.warning("cancel_all %s: %s", symbol, exc.message)

    def server_time(self) -> int:
        return self.client.futures_time()["serverTime"]


def _decimals(step: float | None) -> int:
    if not step:
        return 8
    d = Decimal(str(step)).normalize()
    exp = d.as_tuple().exponent
    return -exp if exp < 0 else 0
