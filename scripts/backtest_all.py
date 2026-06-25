"""
Backtest every single-symbol strategy across BTC/ETH/SOL/XRP and several
look-back windows (7 / 14 / 30 / 60 days).

It replays each strategy bar-by-bar exactly like the live runner:
  * while flat, call strategy.evaluate() on a trailing window of candles;
  * on a Signal, enter at the signal price and arm the SAME bracket the live
    trade manager uses — SL + TP1/TP2/TP3 split by tp_split, with the stop moved
    to break-even after TP1;
  * size every trade to a fixed fractional risk so results are comparable, and
    charge taker fees (cost_bps per side) so tight-stop strategies pay for churn.

Each (strategy, symbol, timeframe) is replayed once; the 7/14/30/60-day numbers
are then computed by filtering trades by entry time (7d & 14d use 15m candles,
30d & 60d use 1h candles).

No API keys (public Binance data). Output prints to console and is written to
logs/backtest_all.txt.

    python scripts/backtest_all.py
    python scripts/backtest_all.py --symbols BTCUSDT ETHUSDT --periods 7 30
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bots.strategies import get_strategy, available   # noqa: E402

_FAPI = "https://fapi.binance.com/fapi/v1/klines"
_TF_MIN = {"5m": 5, "15m": 15, "30m": 30, "1h": 60}
TP_SPLIT = (0.34, 0.33, 0.33)

# strategies are replayed on the timeframe that suits each period
PERIOD_TF = {7: "15m", 14: "15m", 30: "1h", 50: "1h", 60: "1h"}

# all single-symbol directional strategies
STRATEGIES = [s for s in available()]


def fetch(symbol: str, interval: str, bars: int) -> pd.DataFrame:
    out: list = []
    end = int(time.time() * 1000)
    step = _TF_MIN[interval] * 60_000
    remaining = bars + 1
    sess = requests.Session()
    while remaining > 0:
        limit = min(1500, remaining)
        r = sess.get(_FAPI, params={"symbol": symbol, "interval": interval,
                                    "limit": limit, "endTime": end}, timeout=15)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            break
        out = rows + out
        remaining -= len(rows)
        end = rows[0][0] - 1
        if len(rows) < limit:
            break
        time.sleep(0.1)
    df = pd.DataFrame(out, columns=["open_time", "open", "high", "low", "close",
                                    "volume", "ct", "qav", "t", "tb", "tq", "i"])
    df = df.drop_duplicates(subset="open_time")
    for c in ["open", "high", "low", "close", "volume", "tb"]:
        df[c] = df[c].astype(float)
    df["taker_buy_volume"] = df["tb"]   # real aggressive-buy volume (for order-flow strategies)
    df.index = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df[["open", "high", "low", "close", "volume", "taker_buy_volume"]].iloc[:-1].tail(bars)


def simulate_trade(direction, entry, sl, tp1, tp2, tp3, highs, lows, closes,
                   use_be=True, cost_bps=5.0):
    """Replay the TP1/2/3 + break-even bracket over future bars -> realised R."""
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    long = direction == 1

    def R(price):
        return (price - entry) / risk * direction

    p1, p2, p3 = TP_SPLIT
    realized = 0.0
    remaining = 1.0
    be = False
    cur_sl = sl
    t1 = t2 = t3 = False
    exit_i = len(highs) - 1
    for i in range(len(highs)):
        hi, lo = highs[i], lows[i]
        sl_hit = lo <= cur_sl if long else hi >= cur_sl
        if sl_hit:                                   # conservative: stop first
            realized += remaining * R(cur_sl)
            remaining = 0.0; exit_i = i; break
        if not t1 and (hi >= tp1 if long else lo <= tp1):
            realized += p1 * R(tp1); remaining -= p1; t1 = True
            if use_be:
                be = True; cur_sl = entry
        if not t2 and (hi >= tp2 if long else lo <= tp2):
            realized += p2 * R(tp2); remaining -= p2; t2 = True
        if not t3 and (hi >= tp3 if long else lo <= tp3):
            realized += p3 * R(tp3); remaining -= p3; t3 = True
            remaining = 0.0; exit_i = i; break
    if remaining > 0:                                # mark-to-market at last close
        realized += remaining * R(closes[exit_i])
    fee_R = (2.0 * cost_bps / 1e4) * entry / risk    # entry+exit taker fees
    return realized - fee_R, exit_i


def backtest(strat, df, window=240, cost_bps=5.0):
    """Return a list of trades: (entry_time, realised_R, direction)."""
    n = len(df)
    highs = df["high"].to_numpy(float)
    lows = df["low"].to_numpy(float)
    closes = df["close"].to_numpy(float)
    trades = []
    t = window
    while t < n - 1:
        sig = strat.evaluate(df.iloc[max(0, t - window + 1): t + 1])
        if sig is None:
            t += 1
            continue
        entry = float(sig.entry)
        res = simulate_trade(sig.direction, entry, sig.sl, sig.tp1, sig.tp2, sig.tp3,
                             highs[t + 1:], lows[t + 1:], closes[t + 1:], cost_bps=cost_bps)
        if res is None:
            t += 1
            continue
        rR, exit_off = res
        trades.append((df.index[t], rR, sig.direction))
        t = t + 1 + exit_off + 1            # resume after the trade closes (flat)
    return trades


def metrics(trades, risk_frac=0.01):
    if not trades:
        return dict(n=0, win=0.0, avgR=0.0, ret=0.0, maxdd=0.0, pf=0.0)
    R = np.array([x[1] for x in trades])
    eq = np.cumprod(1.0 + risk_frac * R)
    peak = np.maximum.accumulate(eq)
    maxdd = float((eq / peak - 1.0).min())
    gains = R[R > 0].sum()
    losses = -R[R < 0].sum()
    pf = float(gains / losses) if losses > 0 else float("inf")
    return dict(n=len(R), win=float((R > 0).mean()), avgR=float(R.mean()),
                ret=float(eq[-1] - 1.0), maxdd=maxdd, pf=pf)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="*", default=["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"])
    ap.add_argument("--periods", nargs="*", type=int, default=[7, 14, 30, 60])
    ap.add_argument("--strategies", nargs="*", default=STRATEGIES)
    ap.add_argument("--window", type=int, default=240)
    ap.add_argument("--cost-bps", type=float, default=5.0)
    ap.add_argument("--risk-pct", type=float, default=1.0)
    ap.add_argument("--out", default="logs/backtest_all.txt")
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    lines: list[str] = []

    def emit(s=""):
        print(s, flush=True)
        lines.append(s)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))

    tfs = sorted({PERIOD_TF[p] for p in args.periods}, key=lambda x: _TF_MIN[x])
    now = pd.Timestamp.now("UTC")
    risk_frac = args.risk_pct / 100.0

    # fetch enough history per (symbol, tf)
    max_days_for_tf = {tf: max(p for p in args.periods if PERIOD_TF[p] == tf) for tf in tfs}
    data: dict[tuple, pd.DataFrame] = {}
    for sym in args.symbols:
        for tf in tfs:
            bars = int(max_days_for_tf[tf] * 1440 / _TF_MIN[tf]) + args.window + 50
            emit(f"fetch {sym} {tf} ~{bars} bars ...")
            data[(sym, tf)] = fetch(sym, tf, bars)

    # replay each (strategy, symbol, tf) once -> trades
    trades_cache: dict[tuple, list] = {}
    total = len(args.strategies) * len(args.symbols) * len(tfs)
    done = 0
    for name in args.strategies:
        cls = get_strategy(name)
        for sym in args.symbols:
            for tf in tfs:
                strat = cls({})
                t0 = time.time()
                tr = backtest(strat, data[(sym, tf)], window=args.window, cost_bps=args.cost_bps)
                trades_cache[(name, sym, tf)] = tr
                done += 1
                emit(f"  [{done}/{total}] {name:20} {sym:8} {tf:>3}  "
                     f"{len(tr):4d} trades  ({time.time() - t0:.1f}s)")

    # ---- report per period ----
    for period in args.periods:
        tf = PERIOD_TF[period]
        cutoff = now - pd.Timedelta(days=period)
        emit("\n" + "=" * 86)
        emit(f"=== PAST {period} DAYS  (timeframe {tf})  risk={args.risk_pct}%/trade  "
             f"cost={args.cost_bps}bps ===")
        emit("=" * 86)
        emit(f"{'strategy':<20} {'trades':>6} {'win%':>6} {'avgR':>6} "
             f"{'return':>8} {'maxDD':>8} {'PF':>6}   per-symbol return")
        agg = []
        for name in args.strategies:
            sym_rets = []
            all_trades = []
            for sym in args.symbols:
                tr = [t for t in trades_cache[(name, sym, tf)] if t[0] >= cutoff]
                m = metrics(tr, risk_frac)
                sym_rets.append((sym, m["ret"]))
                all_trades += tr
            m = metrics(all_trades, risk_frac)
            ret_str = " ".join(f"{s[:3]}:{r:+.1%}" for s, r in sym_rets)
            agg.append((name, m))
            pf = "inf" if m["pf"] == float("inf") else f"{m['pf']:.2f}"
            emit(f"{name:<20} {m['n']:>6} {m['win']:>5.0%} {m['avgR']:>6.2f} "
                 f"{m['ret']:>+8.1%} {m['maxdd']:>+8.1%} {pf:>6}   {ret_str}")
        # rank by avgR (risk-model independent)
        ranked = sorted([a for a in agg if a[1]["n"] >= 3], key=lambda a: a[1]["avgR"], reverse=True)
        if ranked:
            emit("  top by avgR: " + ", ".join(f"{n}({m['avgR']:+.2f}R,{m['n']}t)" for n, m in ranked[:3]))

    emit(f"\nWritten to {args.out}")


if __name__ == "__main__":
    main()
