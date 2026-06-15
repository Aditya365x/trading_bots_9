# One image, runs any bot. Choose the bot via CONFIG env or command args.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bots/ ./bots/
COPY configs/ ./configs/
COPY scripts/ ./scripts/
COPY run_bot.py .

# CONFIG is the yaml to run, e.g. configs/precision_sniper.yaml
ENV CONFIG=configs/precision_sniper.yaml
CMD ["sh", "-c", "python run_bot.py --config $CONFIG"]
