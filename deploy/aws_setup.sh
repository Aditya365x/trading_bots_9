#!/usr/bin/env bash
# One-time EC2 bootstrap (Ubuntu 22.04/24.04). Installs Docker + Compose, syncs
# the clock (Binance rejects skewed timestamps), and prepares the app dir.
#
#   chmod +x deploy/aws_setup.sh && ./deploy/aws_setup.sh
#
set -euo pipefail

echo ">> Updating packages..."
sudo apt-get update -y

echo ">> Time sync (critical for Binance API)..."
sudo apt-get install -y chrony
sudo systemctl enable --now chrony

echo ">> Installing Docker + Compose plugin..."
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update -y
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

echo ">> Allow current user to run docker without sudo..."
sudo usermod -aG docker "$USER" || true

echo
echo "DONE. Log out and back in (so docker group applies), then:"
echo "  cd <repo>  &&  cp .env.example .env  &&  nano .env   # fill keys + demo URL + telegram"
echo "  docker compose build && docker compose up -d"
