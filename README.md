# Trading Bots — Pine Scripts → Python (Binance USDT-M Futures)

Nine TradingView Pine strategies converted into Python bots that trade on
**Binance USDT-M Futures** (testnet for the demo phase), with **Telegram alerts
on every trade**. One shared engine (data, indicators, risk/trade management)
drives nine thin strategy modules.

> ⚠️ Trading is risky. Start on the **testnet** (`BINANCE_TESTNET=true`) or with
> `--dry-run`. Only switch to live keys after you trust a bot's behaviour.

---

## The 9 bots

| Config / strategy | Engine | Entry trigger |
|---|---|---|
| `precision_sniper` | EMA ribbon + 10-pt confluence score | EMA fast×slow cross + score |
| `breakout_pattern` | Converging channel from pivots | Close breaks the boundary |
| `pulse_trend_radar` | KAMA ± median-ATR bands | Trend-state flip |
| `synapse_trail_pro` | Ratcheting EMA±ATR trail + regime | Trail direction flip |
| `adaptive_fib_trailing` | Structure swings → Fib | 0.5 cross (confirmed) |
| `meridian_flow` | SMC swings → BOS/CHoCH | Structure break event |
| `liquidity_pools` | Equal-high/low pools | Sweep + close-back |
| `fib_structure_engine` | Swings+EQH/EQL+fib+engulf | CHoCH / sweep / engulf |
| `ict_session_zones` | Session killzone high/low | Session-level sweep |

Every bot uses the same trade skeleton from the original scripts: **ATR-based
SL, TP1/TP2/TP3 as R-multiples, break-even after TP1, one position per symbol.**

Defaults: symbols **BTCUSDT / ETHUSDT / SOLUSDT**, timeframe **5m**, leverage **1×**,
risk **1% of balance per trade**. All editable in `configs/*.yaml`.

---

## Setup (Windows, using your venv on D:)

The project venv is `venvbots` (Python 3.11). Dependencies are already installed.
Nothing is installed on C:.

```powershell
# 1. configure secrets
copy .env.example .env
notepad .env        # paste testnet keys + Telegram token/chat id

# 2. dry-run one bot (no orders, just signals + Telegram)
.\venvbots\Scripts\python.exe run_bot.py --config configs\precision_sniper.yaml --dry-run

# 3. testnet live orders (after BINANCE_TESTNET=true and keys are set)
.\venvbots\Scripts\python.exe run_bot.py --config configs\precision_sniper.yaml

# run every bot, each in its own window
.\scripts\run_all_local.ps1            # add -DryRun to simulate
```

### Getting the keys
- **Binance Futures testnet:** https://testnet.binancefuture.com → log in with
  GitHub → *API Key* panel → copy key/secret into `BINANCE_TESTNET_*` in `.env`.
  Fund the testnet wallet from the faucet on that site.
- **Telegram:** message `@BotFather` → `/newbot` → copy the token. Then send your
  bot any message and open
  `https://api.telegram.org/bot<TOKEN>/getUpdates` to read your `chat.id`.
  Put both into `.env`. (If left blank, bots still run and log trades; they just
  don't push to Telegram.)

---

## How a bot runs

Each cycle (every `poll_seconds`) and for each symbol:
1. fetch the latest **closed** 5m candles,
2. if **flat** and a new candle just closed → ask the strategy for a signal; if
   one fires, open a market entry and place SL + TP1/TP2/TP3 bracket orders,
3. if **in a trade** → manage it: move SL to break-even after TP1, detect exit,
   cancel leftovers, and report the close to Telegram.

Run **one process per bot**. The 9 bots are independent.

---

## Deploy on AWS

A small EC2 instance (t3.small is plenty) running 24/7.

### Option A — Docker (simplest)
```bash
git clone <your repo> /opt/trading_bots && cd /opt/trading_bots
cp .env.example .env && nano .env        # testnet keys + Telegram
docker compose build
docker compose up -d                     # all 9 bots
docker compose logs -f precision_sniper
```

### Option B — systemd (one service per bot)
```bash
sudo mkdir -p /opt/trading_bots && cd /opt/trading_bots
# copy the repo here, then:
python3.11 -m venv venv && ./venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env
sudo cp deploy/tradingbot@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tradingbot@precision_sniper
sudo systemctl enable --now tradingbot@pulse_trend_radar
# ... repeat for each config name
journalctl -u tradingbot@precision_sniper -f
```

Keep the EC2 clock synced (`chrony`/`systemd-timesyncd`) — Binance rejects
requests with skewed timestamps.

---

## Going live (real funds)
1. Prove a bot on testnet for a meaningful period.
2. In `.env`: set `BINANCE_TESTNET=false` and fill `BINANCE_API_KEY` /
   `BINANCE_API_SECRET` (Futures-enabled, IP-restricted, **no withdrawal**).
3. Start with `leverage: 1` and a small `risk_per_trade_pct`.

---

## Project layout
```
bots/core/        indicators, binance client, trade manager, runner, telegram
bots/strategies/  the 9 strategy modules (+ registry)
configs/          one YAML per bot
run_bot.py        entrypoint  (--config / --strategy / --dry-run / --list)
deploy/           systemd unit ; Dockerfile + docker-compose.yml at root
scripts/          run_all_local.ps1
```

## Notes & fidelity
- All chart visuals/dashboards/labels from the Pine scripts are intentionally
  dropped — a bot only needs the signal + trade logic.
- A few engines (Breakout channel fit, Liquidity pools, Adaptive-Fib confidence,
  ICT sessions) are faithful *functional* ports; exact pivot/visual edge cases on
  TradingView won't match bar-for-bar. Validate on testnet before trusting them.
- TP ladders are standardised to TP1/TP2/TP3 R-multiples even where a script used
  a single target, so break-even and partial exits behave consistently.
