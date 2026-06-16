#!/usr/bin/env bash
# Uploads your local .env (Binance keys + Telegram) to the EC2 server.
# Run in Git Bash from the project root:   bash scripts/upload_env.sh
#
# Edit KEY / ENV / HOST below if your paths or server change.

KEY="/d/pine scripts/trading_bots_9_key.pem"
ENV="/d/trading_bots/.env"
HOST="ubuntu@ec2-3-27-242-188.ap-southeast-2.compute.amazonaws.com"
DEST="~/trading_bots_9/.env"

echo "Uploading .env to $HOST ..."
scp -i "$KEY" "$ENV" "$HOST:$DEST" && echo ".env uploaded OK" || echo "upload FAILED"
