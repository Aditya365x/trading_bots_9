"""
Make every bot send a live status message to Telegram — your confirmation that
all 9 channels work. Each message shows the bot, strategy, coin, environment,
balance, current price, and whether it currently holds a position.

Run:  ./venvbots/Scripts/python.exe scripts/telegram_check.py
"""
from __future__ import annotations

import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bots.core.binance_client import BinanceFutures   # noqa: E402
from bots.core.config import BotConfig                 # noqa: E402
from bots.core.telegram import TelegramNotifier        # noqa: E402

sent = 0
for y in sorted(glob.glob(os.path.join(os.path.dirname(os.path.dirname(__file__)), "configs", "*.yaml"))):
    cfg = BotConfig.load(y)
    notifier = TelegramNotifier(cfg.telegram_token, cfg.telegram_chat_id, cfg.name)
    sym = cfg.symbols[0]
    try:
        cl = BinanceFutures(cfg.api_key, cfg.api_secret, testnet=cfg.testnet,
                            futures_base_url=cfg.futures_base_url)
        bal = cl.wallet_balance("USDT")
        price = float(cl.get_klines(sym, cfg.timeframe, 2)["close"].iloc[-1])
        pos = cl.get_position(sym)
        if abs(pos["amt"]) > 0:
            d = "LONG" if pos["amt"] > 0 else "SHORT"
            posline = f"{d} {abs(pos['amt'])} @ {pos['entry']} (uPnL {pos['upnl']:+.2f} USDT)"
        else:
            posline = "flat (no open position)"
        msg = (f"✅ <b>{cfg.name}</b> ONLINE\n"
               f"Strategy: {cfg.strategy}\n"
               f"Coin: <b>{sym}</b>  |  TF: {cfg.timeframe}  |  Mode: <b>{cfg.env_label}</b>\n"
               f"Balance: {bal:.2f} USDT\n"
               f"Price now: <code>{price}</code>\n"
               f"Position: {posline}\n"
               f"Risk/trade: {cfg.risk_per_trade_pct}%  |  Leverage: {cfg.leverage}x")
        notifier.send(msg)
        print(f"sent: {cfg.name} ({sym})")
        sent += 1
    except Exception as e:  # noqa: BLE001
        notifier.send(f"⚠️ <b>{cfg.name}</b> status check FAILED: {type(e).__name__}: {str(e)[:120]}")
        print(f"ERROR {cfg.name}: {e}")

print(f"\nDone. {sent}/9 bots reported in.")
