#!/usr/bin/env bash
# One-command deploy: commit + push local changes, then pull + rebuild + restart
# the bots on EC2. Run from the project root in Git Bash:
#
#     bash scripts/deploy.sh "what I changed"
#
# Edit KEY / HOST below if your key path or server address changes
# (the public address changes if you STOP/START the instance).
set -e

KEY="/d/pine scripts/trading_bots_9_key.pem"
HOST="ubuntu@ec2-3-27-242-188.ap-southeast-2.compute.amazonaws.com"
DIR="~/trading_bots_9"
MSG="${1:-update}"

echo ">> Committing & pushing local changes..."
git add -A
git commit -m "$MSG" || echo "   (nothing to commit)"
git push

echo ">> Redeploying on EC2..."
ssh -i "$KEY" "$HOST" "cd $DIR && git pull && docker compose build && docker compose up -d && echo '--- status ---' && docker compose ps --format 'table {{.Name}}\t{{.State}}'"

echo ">> Done. Bots are running the latest code."
