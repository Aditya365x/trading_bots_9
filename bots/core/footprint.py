"""
True footprint bars from tick (aggTrade) data.

This is the order-flow data layer the candle proxy could never be: every price
level inside a bar carries the EXACT volume that hit the bid (taker sold) vs the
ask (taker bought). From that you can read genuine absorption, stacked
imbalances and single-print liquidity voids — not inferred from a candle close.

Design goals
------------
* Source-agnostic: the builder is fed `Tick` objects one at a time. Those ticks
  can come from the live `@aggTrade` WebSocket OR from historical Binance Vision
  aggTrade CSV dumps (data.binance.vision). Same builder → same bars → the
  strategy backtests and trades on identical logic.
* Pure & synchronous: no asyncio, no locks here. Streaming/IO wrappers live
  elsewhere; this module is trivially unit-testable.

Tick side convention (Binance aggTrade `m` flag):
    m == True  -> buyer is the MAKER  -> the taker SOLD  (hit the bid)  -> bid_vol
    m == False -> buyer is the TAKER  -> the taker BOUGHT (hit the ask)  -> ask_vol
So `Tick.buy = not m`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np


# --------------------------------------------------------------------------- #
#  Tick
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Tick:
    """A single aggregated trade."""
    ts: int            # trade time, ms
    price: float
    qty: float         # base-asset quantity
    buy: bool          # True = taker bought (hit ask); False = taker sold (hit bid)

    @property
    def quote(self) -> float:
        """Notional in quote currency (USDT)."""
        return self.price * self.qty

    @classmethod
    def from_agg(cls, m: dict) -> "Tick":
        """Build from a Binance @aggTrade payload (WS or REST aggTrades row)."""
        return cls(ts=int(m["T"]), price=float(m["p"]), qty=float(m["q"]),
                   buy=not bool(m["m"]))


# --------------------------------------------------------------------------- #
#  Footprint level + bar
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class FootprintLevel:
    """Executed volume at one price, split by aggressor side."""
    bid: float = 0.0       # taker SOLD here (hit the bid)
    ask: float = 0.0       # taker BOUGHT here (hit the ask)
    trades: int = 0

    @property
    def total(self) -> float:
        return self.bid + self.ask

    @property
    def delta(self) -> float:
        return self.ask - self.bid


@dataclass(slots=True)
class FootprintBar:
    """One footprint bar: OHLC + per-price bid/ask volume + summary stats."""
    ts: int                                   # bar open time, ms
    tick_size: float
    open: float = field(default=float("nan"))
    high: float = field(default=float("nan"))
    low: float = field(default=float("nan"))
    close: float = field(default=float("nan"))
    levels: dict[float, FootprintLevel] = field(default_factory=dict)
    volume: float = 0.0                       # base-asset volume
    quote_volume: float = 0.0                 # USDT volume
    delta: float = 0.0                        # bar net aggressive flow (ask-bid)
    trades: int = 0

    # ------------------------------------------------------------------ stats #
    @property
    def range(self) -> float:
        return (self.high - self.low) if np.isfinite(self.high) else 0.0

    @property
    def delta_imbalance(self) -> float:
        """Net aggressive imbalance ∈ [-1, 1] (delta / volume)."""
        return (self.delta / self.volume) if self.volume > 0 else 0.0

    @property
    def poc(self) -> Optional[float]:
        """Price level with the most total volume."""
        if not self.levels:
            return None
        return max(self.levels.items(), key=lambda kv: kv[1].total)[0]

    def _prices_desc(self) -> list[float]:
        return sorted(self.levels.keys(), reverse=True)

    def stacked_imbalances(self, ratio: float = 3.0, min_run: int = 3) -> dict:
        """
        Diagonal-imbalance stacks (footprint standard). At each price level the
        ask volume is compared to the bid volume of the level BELOW it (and vice
        versa) — a diagonal comparison, because aggressive buyers lift the offer
        while resting bids sit one tick lower.

        Returns {"buy": longest_ask_run, "sell": longest_bid_run, "buy_zone":
        (lo,hi)|None, "sell_zone": (lo,hi)|None}.
        """
        prices = self._prices_desc()
        best = {"buy": 0, "sell": 0, "buy_zone": None, "sell_zone": None}
        run_buy = run_sell = 0
        buy_start = sell_start = None
        for k in range(len(prices) - 1):
            up = self.levels[prices[k]]          # upper level
            dn = self.levels[prices[k + 1]]      # one tick lower
            # buy imbalance: ask at this level dominates bid one tick down
            if dn.bid > 0 and up.ask >= ratio * dn.bid:
                run_buy = run_buy + 1 if run_buy else 1
                buy_start = buy_start if run_buy > 1 else prices[k]
                if run_buy > best["buy"]:
                    best["buy"] = run_buy
                    best["buy_zone"] = (prices[k + 1], buy_start)
            else:
                run_buy = 0; buy_start = None
            # sell imbalance: bid at lower level dominates ask one tick up
            if up.ask > 0 and dn.bid >= ratio * up.ask:
                run_sell = run_sell + 1 if run_sell else 1
                sell_start = sell_start if run_sell > 1 else prices[k]
                if run_sell > best["sell"]:
                    best["sell"] = run_sell
                    best["sell_zone"] = (prices[k + 1], sell_start)
            else:
                run_sell = 0; sell_start = None
        best["buy"] = best["buy"] + 1 if best["buy"] else 0      # runs count levels, not gaps
        best["sell"] = best["sell"] + 1 if best["sell"] else 0
        return best

    def voids(self, min_empty: int = 3) -> list[tuple[float, float]]:
        """Single-print runs: ≥min_empty consecutive empty ticks between trades."""
        prices = sorted(self.levels.keys())
        out: list[tuple[float, float]] = []
        for k in range(len(prices) - 1):
            gap_ticks = round((prices[k + 1] - prices[k]) / self.tick_size) - 1
            if gap_ticks >= min_empty:
                out.append((prices[k] + self.tick_size, prices[k + 1] - self.tick_size))
        return out

    def absorption(self, close_retreat: float = 0.25,
                   min_delta_imb: float = 0.33) -> Optional[int]:
        """
        Effort-vs-result absorption on THIS bar (intra-bar, true footprint):
          * strong one-sided aggression (|delta_imbalance| ≥ min_delta_imb), yet
          * the bar's RESULT contradicts it — the close is rejected to the
            OPPOSITE extreme (within `close_retreat` of it).
        Returns -1 (aggressive buying rejected to the low → fade short),
        +1 (aggressive selling rejected to the high → fade long), or None.
        Uses REAL per-bar delta from tick sides, so it catches the case the
        candle proxy gets backwards (close-near-low but delta strongly positive).
        """
        if self.range <= 0 or self.volume <= 0:
            return None
        pos = (self.close - self.low) / self.range          # 0=at low, 1=at high
        if self.delta_imbalance >= min_delta_imb and pos <= close_retreat:
            return -1     # buyers aggressive but price rejected DOWN -> short
        if self.delta_imbalance <= -min_delta_imb and pos >= 1.0 - close_retreat:
            return +1     # sellers aggressive but price rejected UP -> long
        return None


# --------------------------------------------------------------------------- #
#  Builder
# --------------------------------------------------------------------------- #
class FootprintBuilder:
    """
    Aggregates ticks into time-based footprint bars (1m, 5m, ...).

    Usage:
        fb = FootprintBuilder(interval_s=300, tick_size=0.1)
        for tick in ticks:
            bar = fb.add(tick)        # returns a closed FootprintBar or None
            if bar: handle(bar)
        last = fb.flush()             # close the final partial bar (backtest end)
    """

    def __init__(self, interval_s: int, tick_size: float, max_bars: int = 1000):
        self.interval_ms = interval_s * 1000
        self.tick_size = tick_size
        self.max_bars = max_bars
        self.bars: list[FootprintBar] = []
        self._cur: Optional[FootprintBar] = None

    def _bucket(self, ts: int) -> int:
        return (ts // self.interval_ms) * self.interval_ms

    def _round(self, price: float) -> float:
        return round(price / self.tick_size) * self.tick_size

    def add(self, t: Tick) -> Optional[FootprintBar]:
        bucket = self._bucket(t.ts)
        closed: Optional[FootprintBar] = None
        if self._cur is None:
            self._cur = FootprintBar(ts=bucket, tick_size=self.tick_size)
        elif bucket > self._cur.ts:
            closed = self._close()
            self._cur = FootprintBar(ts=bucket, tick_size=self.tick_size)
        self._apply(t)
        return closed

    def _apply(self, t: Tick) -> None:
        bar = self._cur
        p = self._round(t.price)
        if not np.isfinite(bar.open):
            bar.open = bar.high = bar.low = p
        bar.high = max(bar.high, p)
        bar.low = min(bar.low, p)
        bar.close = p
        lvl = bar.levels.get(p)
        if lvl is None:
            lvl = bar.levels[p] = FootprintLevel()
        if t.buy:
            lvl.ask += t.qty
        else:
            lvl.bid += t.qty
        lvl.trades += 1
        bar.volume += t.qty
        bar.quote_volume += t.quote
        bar.delta += t.qty if t.buy else -t.qty
        bar.trades += 1

    def _close(self) -> FootprintBar:
        bar = self._cur
        self.bars.append(bar)
        if len(self.bars) > self.max_bars:
            self.bars.pop(0)
        return bar

    def flush(self) -> Optional[FootprintBar]:
        """Close and return the final in-progress bar (use at end of a backtest)."""
        if self._cur is None:
            return None
        bar = self._close()
        self._cur = None
        return bar

    def avg_quote_volume(self, lookback: int = 20) -> float:
        recent = self.bars[-lookback:]
        return float(np.mean([b.quote_volume for b in recent])) if recent else 0.0


# --------------------------------------------------------------------------- #
#  Vectorized builder (backtest path — millions of ticks)
# --------------------------------------------------------------------------- #
def build_bars(df, interval_s: int, tick_size: float) -> list[FootprintBar]:
    """
    Build time-based footprint bars from a tick DataFrame [ts, price, qty, buy]
    using vectorized pandas groupbys — fast enough for whole-day aggTrade dumps.

    Produces the same bars as feeding `FootprintBuilder.add()` tick-by-tick
    (price levels rounded to `tick_size`, OHLC from rounded levels).
    """
    import pandas as pd

    if df is None or len(df) == 0:
        return []
    df = df.sort_values("ts", kind="stable")
    ts = df["ts"].to_numpy()
    interval_ms = interval_s * 1000
    bucket = (ts // interval_ms) * interval_ms
    price = df["price"].to_numpy(float)
    level = np.round(np.round(price / tick_size) * tick_size, 10)
    qty = df["qty"].to_numpy(float)
    buy = df["buy"].to_numpy(bool)

    g = pd.DataFrame({
        "bucket": bucket, "level": level, "qty": qty,
        "ask": np.where(buy, qty, 0.0),
        "bid": np.where(~buy, qty, 0.0),
        "quote": price * qty,
    })
    ohlc = g.groupby("bucket")["level"].agg(o="first", h="max", l="min", c="last")
    agg = g.groupby("bucket").agg(vol=("qty", "sum"), ask=("ask", "sum"),
                                  bid=("bid", "sum"), quote=("quote", "sum"),
                                  trades=("qty", "size"))
    lvl = (g.groupby(["bucket", "level"])
             .agg(ask=("ask", "sum"), bid=("bid", "sum"), trades=("qty", "size"))
             .reset_index())

    levels_by_bucket: dict[int, dict] = {}
    for r in lvl.itertuples(index=False):
        levels_by_bucket.setdefault(int(r.bucket), {})[float(r.level)] = \
            FootprintLevel(bid=float(r.bid), ask=float(r.ask), trades=int(r.trades))

    bars: list[FootprintBar] = []
    for bkt in ohlc.index:
        o = ohlc.loc[bkt]; a = agg.loc[bkt]
        bars.append(FootprintBar(
            ts=int(bkt), tick_size=tick_size,
            open=float(o.o), high=float(o.h), low=float(o.l), close=float(o.c),
            levels=levels_by_bucket.get(int(bkt), {}),
            volume=float(a.vol), quote_volume=float(a.quote),
            delta=float(a.ask - a.bid), trades=int(a.trades),
        ))
    return bars
