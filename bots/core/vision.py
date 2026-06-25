"""
Binance Vision historical aggTrade loader (for backtesting the footprint stack).

data.binance.vision publishes free daily aggTrade dumps for USDT-M futures:
    https://data.binance.vision/data/futures/um/daily/aggTrades/{SYM}/{SYM}-aggTrades-{YYYY-MM-DD}.zip

Each zip holds one CSV:
    agg_trade_id, price, quantity, first_trade_id, last_trade_id,
    transact_time(ms), is_buyer_maker

`is_buyer_maker == true` means the taker SOLD (hit the bid); we expose the
aggressor side as `buy = not is_buyer_maker`, matching `footprint.Tick`.

Files are cached under `.cache/binance_vision/` so repeated backtests don't
re-download. No API key required.
"""
from __future__ import annotations

import io
import os
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator

import pandas as pd
import requests

from .logger import get_logger

log = get_logger("vision")

_BASE = "https://data.binance.vision/data/futures/{market}/daily/aggTrades/{sym}/{sym}-aggTrades-{day}.zip"
_COLS = ["agg_trade_id", "price", "quantity", "first_trade_id",
         "last_trade_id", "transact_time", "is_buyer_maker"]


def _cache_dir() -> Path:
    d = Path(__file__).resolve().parents[2] / ".cache" / "binance_vision"
    d.mkdir(parents=True, exist_ok=True)
    return d


def download_day(symbol: str, day: str, market: str = "um",
                 timeout: int = 120) -> Path:
    """Download one day's aggTrade zip (cached). `day` = 'YYYY-MM-DD'. Returns path."""
    sym = symbol.upper()
    dst = _cache_dir() / f"{sym}-aggTrades-{day}-{market}.zip"
    if dst.exists() and dst.stat().st_size > 0:
        return dst
    url = _BASE.format(market=market, sym=sym, day=day)
    log.info("vision download %s %s ...", sym, day)
    r = requests.get(url, timeout=timeout)
    if r.status_code == 404:
        raise FileNotFoundError(f"No Vision data for {sym} {day} ({url})")
    r.raise_for_status()
    tmp = dst.with_suffix(".part")
    tmp.write_bytes(r.content)
    tmp.replace(dst)
    return dst


def read_day(symbol: str, day: str, market: str = "um") -> pd.DataFrame:
    """Return one day of ticks as a DataFrame with columns [ts, price, qty, buy]."""
    path = download_day(symbol, day, market)
    with zipfile.ZipFile(path) as z:
        name = z.namelist()[0]
        with z.open(name) as fh:
            first = fh.readline().decode("utf-8", "ignore")
        has_header = first.lower().startswith("agg_trade_id")
        with z.open(name) as fh:
            df = pd.read_csv(
                fh,
                header=0 if has_header else None,
                names=None if has_header else _COLS,
                usecols=["price", "quantity", "transact_time", "is_buyer_maker"],
            )
    m = df["is_buyer_maker"]
    if m.dtype != bool:
        m = m.astype(str).str.strip().str.lower().map({"true": True, "false": False})
    out = pd.DataFrame({
        "ts": df["transact_time"].astype("int64"),
        "price": df["price"].astype(float),
        "qty": df["quantity"].astype(float),
        "buy": ~m.astype(bool),          # taker bought when NOT buyer-maker
    })
    return out


def _daterange(start: str, end: str) -> Iterator[str]:
    d0 = datetime.strptime(start, "%Y-%m-%d").date()
    d1 = datetime.strptime(end, "%Y-%m-%d").date()
    d = d0
    while d <= d1:
        yield d.isoformat()
        d += timedelta(days=1)


def read_range(symbol: str, start: str, end: str, market: str = "um",
               skip_missing: bool = True) -> pd.DataFrame:
    """Concatenated ticks for an inclusive date range [start, end] ('YYYY-MM-DD')."""
    frames = []
    for day in _daterange(start, end):
        try:
            frames.append(read_day(symbol, day, market))
        except FileNotFoundError:
            if not skip_missing:
                raise
            log.warning("vision: missing %s %s — skipping", symbol, day)
    if not frames:
        return pd.DataFrame(columns=["ts", "price", "qty", "buy"])
    return pd.concat(frames, ignore_index=True)
