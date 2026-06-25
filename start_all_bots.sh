#!/bin/bash
cd /d/trading_bots
VENV="/d/trading_bots/venvbots/Scripts/python.exe"
SCRIPT="/d/trading_bots/run_bot.py"
DELAY=3

for cfg in configs/*.yaml; do
    botname=$(basename "$cfg" .yaml)
    nohup "$VENV" "$SCRIPT" --config "$cfg" --dry-run > "logs/${botname}.log" 2>&1 &
    echo "Started $botname"
    sleep $DELAY
done

echo "All bots started. Check logs/ for output."
