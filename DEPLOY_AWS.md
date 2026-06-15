# Deploy the 9 bots on AWS EC2 (via GitHub + Docker)

End-to-end runbook. The bots pull code from **GitHub**, run as 9 Docker
containers, trade your **Binance Demo Futures** account, and stream to Telegram.
Trades are recorded to `logs/trades.csv` on the host (persists across restarts).

> 🔐 **Secrets never go to GitHub.** `.env` (your keys + Telegram token) is
> git-ignored. You create `.env` directly on the EC2 box.

---

## 0. One-time: push this project to GitHub
Done from your PC (see the "GitHub" section the assistant set up). Use a
**private** repo — it contains your strategies. Result: a repo URL like
`git@github.com:<you>/trading_bots.git` or `https://github.com/<you>/trading_bots.git`.

---

## 1. Launch the EC2 instance
- **AMI:** Ubuntu Server 24.04 LTS
- **Type:** `t3.small` (2 GB RAM — comfortably runs all 9). `t3.micro` only for 1–3 bots.
- **Storage:** 20 GB gp3.
- **Security group:** inbound **SSH (22)** from *your IP only*; outbound: allow all
  (bots only make outbound HTTPS to Binance + Telegram — no inbound needed).
- (Recommended) attach an **Elastic IP** so the public IP is stable.

## 2. Connect & bootstrap
```bash
ssh -i your-key.pem ubuntu@<EC2_PUBLIC_IP>

# get the code
sudo apt-get update -y && sudo apt-get install -y git
git clone https://github.com/<you>/trading_bots.git
cd trading_bots

# install docker + compose + time sync
chmod +x deploy/aws_setup.sh && ./deploy/aws_setup.sh
# log out & back in so the docker group applies:
exit
ssh -i your-key.pem ubuntu@<EC2_PUBLIC_IP>
cd trading_bots
```

## 3. Create the .env on the server
```bash
cp .env.example .env
nano .env
```
Fill in (same values you used locally):
```
BINANCE_TESTNET=true
BINANCE_FUTURES_BASE_URL=https://demo-fapi.binance.com/fapi
TELEGRAM_BOT_TOKEN=...    TELEGRAM_CHAT_ID=...
BINANCE_KEY_PRECISION_SNIPER=...   BINANCE_SECRET_PRECISION_SNIPER=...
... (all 9 per-bot key pairs)
```
> Tip: instead of retyping, securely copy your local `.env`:
> `scp -i your-key.pem .env ubuntu@<EC2_PUBLIC_IP>:~/trading_bots/.env`

## 4. (If your Binance keys are IP-restricted)
Add the EC2 **Elastic IP** to each key's allowlist in Binance, or the API will
reject orders with `-2015`. Demo keys are usually unrestricted — skip if so.

## 5. Build & run all 9 bots
```bash
docker compose build
docker compose up -d
docker compose ps                 # all 9 should be "running"
```
Within seconds you'll get **9 startup messages on Telegram**. Confirm the live
status broadcast too:
```bash
docker compose run --rm precision_sniper python scripts/telegram_check.py
```

## 6. Watch them
```bash
docker compose logs -f                         # all bots
docker compose logs -f precision_sniper        # one bot
tail -f logs/trades.csv                         # the trade record
```

---

## Operating
| Action | Command |
|---|---|
| Stop all | `docker compose down` |
| Restart all | `docker compose restart` |
| Start some | `docker compose up -d precision_sniper pulse_trend_radar` |
| Update after a GitHub push | `git pull && docker compose build && docker compose up -d` |
| Back up the record | `scp -i key.pem ubuntu@IP:~/trading_bots/logs/trades.csv .` |

`restart: unless-stopped` is set, so bots auto-restart on crash or instance reboot.

## Going live later
Edit `.env`: set `BINANCE_TESTNET=false`, **remove** `BINANCE_FUTURES_BASE_URL`
(so it uses real futures), and put **live** Futures keys in the per-bot vars.
Start tiny (leverage 1, low risk %). Then `docker compose up -d --force-recreate`.
