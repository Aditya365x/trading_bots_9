"""
Backtest OrderFlowSniper on BTC/ETH/XRP/SOL at 1m and 5m for June 2026.

Usage:
    python scripts/backtest_order_flow_sniper.py
"""
from __future__ import annotations

import os
import sys
import time

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bots.strategies.order_flow_sniper import OrderFlowSniper  # noqa: E402

_FAPI = "https://fapi.binance.com/fapi/v1/klines"
TP_SPLIT = (0.34, 0.33, 0.33)
COST_BPS = 5.0
RISK_PCT = 0.005  # 0.5% risk per trade
WINDOW = 400

START_MS = int(pd.Timestamp("2026-06-01", tz="UTC").timestamp() * 1000)
SYMBOLS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT"]

# Tighter params for 1m (noisy), standard for 5m
PARAMS_1M = {
    "profile_window": 240, "profile_bins": 80, "value_area_pct": 0.70,
    "ctx_tol_atr": 0.4, "vol_ma_len": 30, "vol_spike_mult": 2.0,
    "delta_strong": 0.50, "absorb_close_pct": 0.15,
    "div_swing": 4, "div_lookback": 80, "div_price_tol_atr": 0.15,
    "stacked_n": 5, "void_min_levels": 6, "void_frac": 0.08,
    "retr_window": 15, "vwap_band_sigma": 2.5, "cvd_mom_len": 20,
    "init_body_atr": 0.5, "init_delta_strong": 0.70,
    "hvn_vol_mult": 3.0, "atr_len": 14, "sl_buffer_atr": 0.2,
    "min_rr": 2.0, "rth_only": False,
}

PARAMS_5M = {
    "profile_window": 150, "profile_bins": 60, "value_area_pct": 0.70,
    "ctx_tol_atr": 0.5, "vol_ma_len": 20, "vol_spike_mult": 2.5,
    "delta_strong": 0.45, "absorb_close_pct": 0.18,
    "div_swing": 3, "div_lookback": 50, "div_price_tol_atr": 0.12,
    "stacked_n": 3, "void_min_levels": 4, "void_frac": 0.10,
    "retr_window": 8, "vwap_band_sigma": 2.5, "cvd_mom_len": 12,
    "init_body_atr": 0.40, "init_delta_strong": 0.70,
    "hvn_vol_mult": 2.0, "atr_len": 14, "sl_buffer_atr": 0.2,
    "min_rr": 1.5, "rth_only": False,
}


def fetch_from(symbol: str, interval: str, start_ms: int, window_bars: int) -> pd.DataFrame:
    tf_min = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}[interval]
    out: list = []
    end = int(time.time() * 1000)
    step_ms = tf_min * 60_000
    total_bars = max(1000, int((end - start_ms) / step_ms) + window_bars + 50)
    sess = requests.Session()
    while True:
        limit = min(1500, total_bars + 1)
        r = sess.get(_FAPI, params={"symbol": symbol, "interval": interval,
                                     "limit": limit, "endTime": end}, timeout=30)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            break
        out = rows + out
        total_bars -= len(rows)
        end = rows[0][0] - 1
        if rows[0][0] <= start_ms:
            break
        if len(rows) < limit:
            break
        time.sleep(0.1)
    df = pd.DataFrame(out, columns=["open_time", "open", "high", "low", "close",
                                     "volume", "ct", "qav", "t", "tb", "tq", "i"])
    df = df.drop_duplicates(subset="open_time")
    for c in ["open", "high", "low", "close", "volume", "tb"]:
        df[c] = df[c].astype(float)
    df["taker_buy_volume"] = df["tb"]
    df.index = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df[["open", "high", "low", "close", "volume", "taker_buy_volume"]].iloc[:-1]
    june_start = pd.Timestamp("2026-06-01", tz="UTC")
    return df[df.index >= june_start]


def simulate_trade(direction, entry, sl, tp1, tp2, tp3, highs, lows, closes,
                   use_be=True, cost_bps=COST_BPS):
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    long = direction == 1

    def R(price):
        return (price - entry) / risk * direction

    p1, p2, p3 = TP_SPLIT
    realized = 0.0
    remaining = 1.0
    cur_sl = sl
    t1 = t2 = t3 = False
    exit_i = len(highs) - 1
    for i in range(len(highs)):
        hi, lo = highs[i], lows[i]
        sl_hit = lo <= cur_sl if long else hi >= cur_sl
        if sl_hit:
            realized += remaining * R(cur_sl)
            remaining = 0.0; exit_i = i; break
        if not t1 and (hi >= tp1 if long else lo <= tp1):
            realized += p1 * R(tp1); remaining -= p1; t1 = True
            if use_be:
                cur_sl = entry
        if not t2 and (hi >= tp2 if long else lo <= tp2):
            realized += p2 * R(tp2); remaining -= p2; t2 = True
        if not t3 and (hi >= tp3 if long else lo <= tp3):
            realized += p3 * R(tp3); remaining -= p3; t3 = True
            remaining = 0.0; exit_i = i; break
    if remaining > 0:
        realized += remaining * R(closes[exit_i])
    fee_R = (2.0 * cost_bps / 1e4) * entry / risk
    return realized - fee_R, exit_i


def backtest(strategy, df, window=WINDOW, cost_bps=COST_BPS):
    n = len(df)
    highs = df["high"].to_numpy(float)
    lows = df["low"].to_numpy(float)
    closes = df["close"].to_numpy(float)
    trades = []
    t = window
    while t < n - 1:
        sig = strategy.evaluate(df.iloc[max(0, t - window + 1): t + 1])
        if sig is None:
            t += 1
            continue
        res = simulate_trade(sig.direction, float(sig.entry), sig.sl, sig.tp1,
                             sig.tp2, sig.tp3, highs[t + 1:], lows[t + 1:],
                             closes[t + 1:], cost_bps=cost_bps)
        if res is None:
            t += 1
            continue
        rR, exit_off = res
        trades.append({
            "time": df.index[t], "R": rR, "direction": sig.direction,
            "reason": sig.reason, "grade": sig.grade, "score": sig.score,
            "entry": sig.entry, "sl": sig.sl,
        })
        t = t + 1 + exit_off + 1
    return trades


def metrics(trades, risk_frac=RISK_PCT):
    if not trades:
        return dict(n=0, win=0.0, avgR=0.0, ret=0.0, maxdd=0.0, pf=0.0,
                    longR=0.0, shortR=0.0, n_long=0, n_short=0)
    R = np.array([t["R"] for t in trades])
    eq = np.cumprod(1.0 + risk_frac * R)
    peak = np.maximum.accumulate(eq)
    maxdd = float((eq / peak - 1.0).min())
    gains = R[R > 0].sum()
    losses = -R[R < 0].sum()
    pf = float(gains / losses) if losses > 0 else float("inf")

    R_long = np.array([t["R"] for t in trades if t["direction"] == 1])
    R_short = np.array([t["R"] for t in trades if t["direction"] == -1])
    return dict(
        n=len(R), win=float((R > 0).mean()), avgR=float(R.mean()),
        ret=float(eq[-1] - 1.0), maxdd=maxdd, pf=pf,
        longR=float(R_long.mean()) if len(R_long) else 0.0,
        shortR=float(R_short.mean()) if len(R_short) else 0.0,
        n_long=len(R_long), n_short=len(R_short),
    )


def safe_str(s):
    return str(s).encode("ascii", errors="replace").decode("ascii")


def main():
    header = "=" * 90
    print(header)
    print("ORDER FLOW SNIPER -- June 2026 Backtest")
    print(f"Symbols: {SYMBOLS}")
    print(f"1m params: vol_spike=2.0x  delta=0.50  init_delta=0.70  min_rr=2.0R")
    print(f"5m params: vol_spike=2.5x  delta=0.45  init_delta=0.70  min_rr=1.5R")
    print(header)

    out_lines = []
    def emit(s=""):
        safe = safe_str(s)
        print(safe, flush=True)
        out_lines.append(safe)

    # Step 1: Fetch
    data: dict[tuple, pd.DataFrame] = {}
    for sym in SYMBOLS:
        for tf in ["1m", "5m"]:
            emit(f"\n--- Fetching {sym} {tf} from June 1, 2026 ---")
            df = fetch_from(sym, tf, START_MS, WINDOW)
            emit(f"    {len(df)} bars, {df.index[0]} to {df.index[-1]}")
            data[(sym, tf)] = df

    # Step 2: Backtest
    all_results: dict[tuple, list] = {}
    total = len(SYMBOLS) * 2
    done = 0
    for sym in SYMBOLS:
        for tf in ["1m", "5m"]:
            params = PARAMS_1M if tf == "1m" else PARAMS_5M
            strat = OrderFlowSniper(params)
            df = data[(sym, tf)]
            t0 = time.time()
            trades = backtest(strat, df)
            elapsed = time.time() - t0
            done += 1
            m = metrics(trades)
            all_results[(sym, tf)] = trades

            grades = {}
            for t in trades:
                g = t.get("grade", "?")
                grades[g] = grades.get(g, 0) + 1

            # Setup breakdown
            setups = {}
            for t in trades:
                reason = t.get("reason", "")
                if "Absorption" in reason:
                    setups["Absorption"] = setups.get("Absorption", 0) + 1
                elif "Exhaustion" in reason:
                    setups["Exhaustion"] = setups.get("Exhaustion", 0) + 1
                elif "VWAP band" in reason:
                    setups["VWAP"] = setups.get("VWAP", 0) + 1
                elif "Void-fill" in reason:
                    setups["VoidFill"] = setups.get("VoidFill", 0) + 1
                elif "Initiative breakout" in reason:
                    setups["InitBO"] = setups.get("InitBO", 0) + 1
                elif "HVN" in reason:
                    setups["HVN"] = setups.get("HVN", 0) + 1
                else:
                    setups["Other"] = setups.get("Other", 0) + 1
            setup_str = " ".join(f"{k}:{v}" for k, v in sorted(setups.items()))

            # R by setup
            R_by_setup = {}
            for t in trades:
                reason = t.get("reason", "")
                if "Absorption" in reason:
                    s = "Absorption"
                elif "Exhaustion" in reason:
                    s = "Exhaustion"
                elif "VWAP band" in reason:
                    s = "VWAP"
                elif "Void-fill" in reason:
                    s = "VoidFill"
                elif "Initiative breakout" in reason:
                    s = "InitBO"
                elif "HVN" in reason:
                    s = "HVN"
                else:
                    s = "Other"
                if s not in R_by_setup:
                    R_by_setup[s] = []
                R_by_setup[s].append(t["R"])
            r_setup_str = "  ".join(
                f"{k}:{np.mean(v):+.2f}R({len(v)})" for k, v in sorted(R_by_setup.items()))

            emit(f"\n[{done}/{total}] {sym} {tf:>3}  done in {elapsed:.1f}s")
            emit(f"  Trades: {m['n']}  Win: {m['win']:.0%}  AvgR: {m['avgR']:+.3f}R  "
                 f"PF: {m['pf']:.2f}  Return: {m['ret']:+.2%}  MaxDD: {m['maxdd']:+.2%}")
            emit(f"  Long: {m['n_long']} ({m['longR']:+.2f}R)  "
                 f"Short: {m['n_short']} ({m['shortR']:+.2f}R)")
            emit(f"  Setups: {setup_str}")
            emit(f"  R/setup: {r_setup_str}")

            for t in trades[-5:]:
                tstr = safe_str(str(t['time'])[:19])
                emit(f"    {tstr}  {t['direction']:+d}  {t['R']:+.2f}R  "
                     f"{t['grade']}  score={t['score']:.0f}  [{t['reason'][:80]}]")

    # Step 3: Summary
    emit("\n" + "=" * 90)
    emit("SUMMARY -- OrderFlowSniper June 2026")
    emit("=" * 90)
    emit(f"{'Symbol':<10} {'TF':>4} {'Trades':>7} {'Win%':>7} {'AvgR':>7} "
         f"{'Return':>9} {'MaxDD':>9} {'PF':>7}  {'L:avgR':>8} {'S:avgR':>8}")
    emit("-" * 90)
    agg_results = []
    for sym in SYMBOLS:
        for tf in ["1m", "5m"]:
            m = metrics(all_results[(sym, tf)])
            emit(f"{sym:<10} {tf:>4} {m['n']:>7} {m['win']:>6.0%} {m['avgR']:>+7.3f} "
                 f"{m['ret']:>+8.2%} {m['maxdd']:>+8.2%} {m['pf']:>7.2f}  "
                 f"{m['longR']:>+8.2f} {m['shortR']:>+8.2f}")
            agg_results.append((sym, tf, m))

    emit("\n--- RANKED BY AVG R ---")
    ranked = sorted([a for a in agg_results if a[2]["n"] >= 3],
                    key=lambda a: a[2]["avgR"], reverse=True)
    for sym, tf, m in ranked:
        emit(f"  {sym:<10} {tf:>4}:  {m['avgR']:+.3f}R  ({m['n']} trades)  "
             f"Win={m['win']:.0%}  PF={m['pf']:.2f}")

    emit("\n--- AGGREGATE BY TIMEFRAME ---")
    for tf in ["1m", "5m"]:
        all_t = []
        for sym in SYMBOLS:
            all_t += all_results[(sym, tf)]
        m = metrics(all_t)
        emit(f"  {tf:>4}:  {m['n']} trades  Win={m['win']:.0%}  AvgR={m['avgR']:+.3f}R  "
             f"Return={m['ret']:+.2%}  MaxDD={m['maxdd']:+.2%}  PF={m['pf']:.2f}")

    emit("\n--- AGGREGATE BY SYMBOL (both TFs combined) ---")
    for sym in SYMBOLS:
        all_t = all_results[(sym, "1m")] + all_results[(sym, "5m")]
        m = metrics(all_t)
        emit(f"  {sym:<10}:  {m['n']} trades  Win={m['win']:.0%}  AvgR={m['avgR']:+.3f}R  "
             f"PF={m['pf']:.2f}  Return={m['ret']:+.2%}")

    os.makedirs("logs", exist_ok=True)
    report_path = "logs/backtest_order_flow_sniper_june2026.txt"
    with open(report_path, "w", encoding="ascii", errors="replace") as f:
        f.write("\n".join(out_lines))
    print(f"\nReport written to {report_path}")


if __name__ == "__main__":
    main()