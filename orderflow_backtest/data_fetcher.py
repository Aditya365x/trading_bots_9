"""Binance Futures data fetcher with parquet caching.

Two fetch modes:
  * trailing window  — the last N days ending now (the original behaviour), and
  * explicit range   — an exact [start, end) UTC window, used for a specific
    CALENDAR MONTH (e.g. the previous month). Past-month windows are immutable,
    so they are cached permanently (no 2h expiry) and keyed by their label.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

_FAPI = "https://fapi.binance.com/fapi/v1/klines"
_TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000}

COLUMNS = ["open", "high", "low", "close", "volume", "taker_buy_volume"]


# --------------------------------------------------------------------------- #
#  Period helpers
# --------------------------------------------------------------------------- #
def previous_month_range(now: Optional[pd.Timestamp] = None):
    """Return (start, end, label) for the previous calendar month in UTC.

    end is the first instant of the current month, so the window is [start, end)."""
    now = now or pd.Timestamp.now(tz="UTC")
    this_month_start = now.normalize().replace(day=1)
    end = this_month_start
    start = (this_month_start - pd.Timedelta(days=1)).replace(day=1)
    return start, end, start.strftime("%Y-%m")


def resolve_period(mode: str = "previous_month", days: int = 30,
                   start: Optional[str] = None, end: Optional[str] = None):
    """Turn config into (start_ts|None, end_ts|None, label). `None` start/end ⇒
    trailing `days` window."""
    if mode == "explicit" and start and end:
        s = pd.Timestamp(start, tz="UTC"); e = pd.Timestamp(end, tz="UTC")
        return s, e, f"{s.strftime('%Y%m%d')}-{e.strftime('%Y%m%d')}"
    if mode == "previous_month":
        return previous_month_range()
    return None, None, f"{days}d"          # trailing window


# --------------------------------------------------------------------------- #
#  Fetching
# --------------------------------------------------------------------------- #
def _paginate(symbol: str, interval: str, start_ms: int, end_ms: int) -> list:
    """Page Binance klines forward over [start_ms, end_ms)."""
    step = _TF_MS[interval]
    out: list = []
    sess = requests.Session()
    cur = start_ms
    while cur < end_ms:
        r = sess.get(_FAPI, params={"symbol": symbol, "interval": interval,
                                    "startTime": cur, "endTime": end_ms, "limit": 1500},
                     timeout=30)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            break
        out += rows
        cur = rows[-1][0] + step
        if len(rows) < 1500:
            break
        time.sleep(0.08)
    return out


def fetch_klines(
    symbol: str,
    interval: str,
    days: int = 30,
    start: Optional[pd.Timestamp] = None,
    end: Optional[pd.Timestamp] = None,
    label: Optional[str] = None,
    cache_dir: str = "data_cache",
) -> pd.DataFrame:
    """Fetch klines either by explicit [start, end) (when given) or by trailing
    `days`. Returns a UTC-indexed DataFrame of COLUMNS."""
    ranged = start is not None and end is not None
    label = label or (f"{days}d")
    cache_dir_p = Path(cache_dir)
    cache_dir_p.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir_p / f"{symbol}_{interval}_{label}.parquet"

    # Cache policy: immutable past windows live forever; live/trailing expire 2h.
    if cache_file.exists():
        immutable = ranged and end <= pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=1)
        fresh = datetime.now() - datetime.fromtimestamp(cache_file.stat().st_mtime) < timedelta(hours=2)
        if immutable or fresh:
            df = pd.read_parquet(cache_file)
            if len(df) > 100:
                return df

    if ranged:
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
    else:
        end_ms = int(time.time() * 1000)
        start_ms = int((datetime.now(timezone.utc) - timedelta(days=days + 2)).timestamp() * 1000)

    out = _paginate(symbol, interval, start_ms, end_ms)
    if not out:
        return pd.DataFrame()

    df = pd.DataFrame(out, columns=["open_time", "open", "high", "low", "close", "volume",
                                    "ct", "qav", "t", "tb", "tq", "i"])
    df = df.drop_duplicates(subset="open_time")
    for c in ["open", "high", "low", "close", "volume", "tb"]:
        df[c] = df[c].astype(float)
    df["taker_buy_volume"] = df["tb"]
    df.index = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.sort_index()
    df = df[COLUMNS]

    if ranged:
        df = df[(df.index >= start) & (df.index < end)]          # exact [start, end)
    else:
        df = df.iloc[:-1]                                         # drop the forming bar
        cutoff = pd.Timestamp.now(tz="UTC") - timedelta(days=days + 1)
        df = df[df.index >= cutoff]

    df.to_parquet(cache_file, index=True)
    return df


def fetch_multi_symbol(
    symbols: list[str],
    timeframes: list[str],
    days: int = 30,
    start: Optional[pd.Timestamp] = None,
    end: Optional[pd.Timestamp] = None,
    label: Optional[str] = None,
    cache_dir: str = "data_cache",
) -> dict[tuple[str, str], pd.DataFrame]:
    result = {}
    for sym in symbols:
        for tf in timeframes:
            df = fetch_klines(sym, tf, days=days, start=start, end=end,
                              label=label, cache_dir=cache_dir)
            if len(df) > 0:
                result[(sym, tf)] = df
    return result
