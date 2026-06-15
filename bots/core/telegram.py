"""
Telegram notifier.

Every trade event (entry, SL/TP/break-even moves, exits, errors) is pushed to
your Telegram chat. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env.

To get them:
  1. Message @BotFather on Telegram -> /newbot -> copy the token.
  2. Send any message to your new bot, then open
     https://api.telegram.org/bot<TOKEN>/getUpdates and read "chat":{"id":...}.
     That id is TELEGRAM_CHAT_ID.

If the token/chat id are missing, the notifier is silently disabled so the bot
keeps running (trades still get logged to file/console).
"""
from __future__ import annotations

import requests

from .logger import get_logger

log = get_logger("telegram")
_API = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, bot_name: str = "bot"):
        self.token = token
        self.chat_id = chat_id
        self.bot_name = bot_name
        self.enabled = bool(token and chat_id)
        if not self.enabled:
            log.warning("Telegram disabled (token/chat id not set) — trades will only be logged.")

    def send(self, text: str) -> None:
        if not self.enabled:
            return
        try:
            resp = requests.post(
                _API.format(token=self.token),
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )
            if resp.status_code != 200:
                log.error("Telegram send failed %s: %s", resp.status_code, resp.text[:200])
        except Exception as exc:  # never let notifications crash trading
            log.error("Telegram send exception: %s", exc)

    # ---- convenience formatters -------------------------------------------- #
    def trade_open(self, info: dict) -> None:
        """`info` keys: symbol, strategy, side, qty, notional, entry, sl, tp1,
        tp2, tp3, grade, score, reason, leverage, balance, rr, dry."""
        side = info["side"]
        emoji = "🟢 LONG" if side == "BUY" else "🔴 SHORT"
        tag = " <i>(dry-run)</i>" if info.get("dry") else ""
        entry = info["entry"]

        def pct(level):
            if not entry:
                return ""
            p = (level - entry) / entry * 100.0
            return f" ({p:+.2f}%)"

        risk = abs(entry - info["sl"]) or 1e-9
        r = lambda lv: abs(lv - entry) / risk  # noqa: E731
        self.send(
            f"{emoji}  <b>{info['symbol']}</b>{tag}\n"
            f"Bot: <b>{self.bot_name}</b> ({info.get('strategy','')})\n"
            f"━━━━━━━━━━━━━━\n"
            f"Entry: <code>{entry}</code>\n"
            f"Qty: <code>{info['qty']}</code>  (~{info.get('notional',0):.2f} USDT, {info.get('leverage',1)}x)\n"
            f"🛑 SL: <code>{info['sl']}</code>{pct(info['sl'])}\n"
            f"🎯 TP1: <code>{info['tp1']}</code>{pct(info['tp1'])}  [{r(info['tp1']):.1f}R]\n"
            f"🎯 TP2: <code>{info['tp2']}</code>{pct(info['tp2'])}  [{r(info['tp2']):.1f}R]\n"
            f"🎯 TP3: <code>{info['tp3']}</code>{pct(info['tp3'])}  [{r(info['tp3']):.1f}R]\n"
            f"━━━━━━━━━━━━━━\n"
            f"R:R(TP1): <b>1:{info.get('rr',0):.2f}</b>  |  Grade: <b>{info.get('grade','—')}</b>\n"
            f"Reason: {info.get('reason','')}\n"
            f"Balance: {info.get('balance',0):.2f} USDT"
        )

    def trade_event(self, symbol, text, pnl=None):
        extra = ""
        if pnl is not None:
            extra = f"  |  uPnL: <b>{pnl:+.2f} USDT</b>"
        self.send(f"ℹ️ <b>{self.bot_name}</b> {symbol}: {text}{extra}")

    def trade_close(self, symbol, reason, pnl=None):
        if pnl is not None:
            tag = "✅ WIN" if pnl > 0 else "❌ LOSS" if pnl < 0 else "➖ flat"
            body = f"{tag}  PnL: <b>{pnl:+.2f} USDT</b>"
        else:
            body = ""
        self.send(f"🏁 <b>{self.bot_name}</b> {symbol} closed — {reason}\n{body}")

    def error(self, text):
        self.send(f"⚠️ <b>{self.bot_name}</b> error: {text}")

    def startup(self, symbols, tf, env_label, dry):
        mode = env_label + (" / dry-run" if dry else "")
        self.send(
            f"🤖 <b>{self.bot_name}</b> started\n"
            f"Mode: <b>{mode}</b>\nSymbols: {', '.join(symbols)}\nTF: {tf}"
        )
