"""
Entrypoint — run a single bot.

Examples (use the project venv on D:):
  ./venvbots/Scripts/python.exe run_bot.py --config configs/precision_sniper.yaml
  ./venvbots/Scripts/python.exe run_bot.py --strategy pulse_trend_radar --symbols BTCUSDT --tf 5m --dry-run
  ./venvbots/Scripts/python.exe run_bot.py --list

Each bot trades the configured symbols on one timeframe with one strategy and
reports every trade to Telegram. Run one process per bot (9 processes for 9 bots).
"""
from __future__ import annotations

import argparse
import sys

from bots.core.config import BotConfig
from bots.core.runner import Runner
from bots.strategies import available


def main() -> int:
    ap = argparse.ArgumentParser(description="Run a Pine-converted trading bot on Binance Futures.")
    ap.add_argument("--config", help="Path to a YAML config in configs/")
    ap.add_argument("--strategy", help="Strategy name (overrides config)")
    ap.add_argument("--symbols", help="Comma-separated symbols, e.g. BTCUSDT,ETHUSDT")
    ap.add_argument("--tf", dest="timeframe", help="Timeframe, e.g. 5m")
    ap.add_argument("--name", help="Bot name (log + Telegram label)")
    ap.add_argument("--leverage", type=int)
    ap.add_argument("--risk", dest="risk_per_trade_pct", type=float, help="Risk %% per trade")
    ap.add_argument("--dry-run", action="store_true", help="Compute + notify, do NOT send orders")
    ap.add_argument("--list", action="store_true", help="List available strategies and exit")
    args = ap.parse_args()

    if args.list:
        print("Available strategies:")
        for s in available():
            print(f"  - {s}")
        return 0

    overrides = {}
    if args.strategy:
        overrides["strategy"] = args.strategy
    if args.symbols:
        overrides["symbols"] = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if args.timeframe:
        overrides["timeframe"] = args.timeframe
    if args.name:
        overrides["name"] = args.name
    if args.leverage is not None:
        overrides["leverage"] = args.leverage
    if args.risk_per_trade_pct is not None:
        overrides["risk_per_trade_pct"] = args.risk_per_trade_pct
    if args.dry_run:
        overrides["dry_run"] = True

    cfg = BotConfig.load(args.config, overrides)
    if not args.config and not args.strategy:
        ap.error("Provide --config or --strategy")

    if not cfg.dry_run and (not cfg.api_key or not cfg.api_secret):
        print("ERROR: API keys missing. Set them in .env (see .env.example) or use --dry-run.",
              file=sys.stderr)
        return 2

    Runner(cfg).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
