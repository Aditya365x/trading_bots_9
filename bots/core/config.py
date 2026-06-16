"""
Configuration loading.

Secrets (API keys, Telegram token) come from environment variables / .env so
they never live in the repo. Per-bot trading parameters come from a YAML file
in configs/.  CLI flags can override a few common fields.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv()  # read .env from CWD if present


@dataclass
class BotConfig:
    # --- identity ---
    name: str = "bot"
    strategy: str = "precision_sniper"      # module name in bots/strategies

    # --- market ---
    symbols: list[str] = field(default_factory=lambda: ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    timeframe: str = "5m"                    # 1m,3m,5m,15m,30m,1h,...
    market: str = "futures"                  # futures (USDT-M). spot reserved.

    # --- risk / sizing ---
    leverage: int = 1
    risk_per_trade_pct: float = 1.0          # % of wallet balance risked per trade
    risk_usdt: float = 0.0                    # absolute $ risk per trade; >0 overrides the % above
    max_position_usdt: float = 0.0           # notional cap (margin = notional / leverage)
    use_break_even: bool = True              # move SL to entry after TP1
    tp_split: list[float] = field(default_factory=lambda: [0.34, 0.33, 0.33])  # TP1/2/3 portions

    # --- strategy params (free-form, passed to the strategy) ---
    params: dict[str, Any] = field(default_factory=dict)

    # --- engine ---
    lookback_bars: int = 600                 # candles fetched each cycle (warmup)
    poll_seconds: int = 5                    # how often to check for a new closed candle
    dry_run: bool = False                    # compute + notify but do NOT send orders

    # --- environment-derived (filled in load()) ---
    testnet: bool = True
    futures_base_url: str = ""        # set to use Binance Demo Futures, e.g.
                                      # https://demo-fapi.binance.com/fapi
    api_key: str = ""
    api_secret: str = ""
    telegram_token: str = ""
    telegram_chat_id: str = ""

    @property
    def env_label(self) -> str:
        if self.futures_base_url:
            return "DEMO"
        return "TESTNET" if self.testnet else "LIVE"

    @classmethod
    def load(cls, yaml_path: str | None = None, overrides: dict | None = None) -> "BotConfig":
        data: dict[str, Any] = {}
        if yaml_path and os.path.exists(yaml_path):
            with open(yaml_path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}

        valid = {f.name for f in fields(cls)}
        clean = {k: v for k, v in data.items() if k in valid}
        cfg = cls(**clean)

        # secrets / environment
        cfg.testnet = _env_bool("BINANCE_TESTNET", True)
        cfg.futures_base_url = os.getenv("BINANCE_FUTURES_BASE_URL", "")
        # 1) per-bot key (preferred): BINANCE_KEY_<NAME> / BINANCE_SECRET_<NAME>
        nm = (clean.get("name") or cfg.name).upper()
        per_key = os.getenv(f"BINANCE_KEY_{nm}")
        per_sec = os.getenv(f"BINANCE_SECRET_{nm}")
        if per_key and per_sec:
            cfg.api_key, cfg.api_secret = per_key, per_sec
        # 2) fallback to a shared key
        elif cfg.testnet:
            cfg.api_key = os.getenv("BINANCE_TESTNET_API_KEY", "")
            cfg.api_secret = os.getenv("BINANCE_TESTNET_API_SECRET", "")
        else:
            cfg.api_key = os.getenv("BINANCE_API_KEY", "")
            cfg.api_secret = os.getenv("BINANCE_API_SECRET", "")
        cfg.telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        cfg.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

        if overrides:
            for k, v in overrides.items():
                if v is not None and hasattr(cfg, k):
                    setattr(cfg, k, v)
        return cfg


def _env_bool(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")
