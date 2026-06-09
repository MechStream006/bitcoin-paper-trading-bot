# BTC Paper Trading Bot — VPS Installation Guide

Target: Ubuntu 22.04 LTS, single VPS, non-root service account.

---

## Prerequisites

- Python 3.11+ (`python3 --version`)
- git (`git --version`)
- systemd (standard on Ubuntu)
- 25–80 GB disk, 1–2 GB RAM minimum

---

## 1. Create a dedicated service account

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin btcbot
```

---

## 2. Deploy code

```bash
sudo mkdir -p /opt/btcbot
sudo git clone https://github.com/<your-org>/bitcoin-paper-trading-bot.git /opt/btcbot
sudo chown -R btcbot:btcbot /opt/btcbot
```

Or copy from local dev:

```bash
rsync -av --exclude '.env' --exclude 'data/' --exclude '__pycache__/' \
    /path/to/Bitcoin_paper_trading_bot/ user@host:/opt/btcbot/
```

---

## 3. Python virtual environment

```bash
sudo -u btcbot python3.11 -m venv /opt/btcbot/venv
sudo -u btcbot /opt/btcbot/venv/bin/pip install --upgrade pip
sudo -u btcbot /opt/btcbot/venv/bin/pip install -r /opt/btcbot/requirements.txt
```

Verify encoding is clean (should print 9 package lines, no garbage):

```bash
cat /opt/btcbot/requirements.txt
```

---

## 4. Configure .env

```bash
sudo -u btcbot cp /opt/btcbot/.env.example /opt/btcbot/.env
sudo -u btcbot nano /opt/btcbot/.env
# Fill in: DELTA_API_KEY, DELTA_API_SECRET, TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID
```

Permissions (owner-only read):

```bash
sudo chmod 600 /opt/btcbot/.env
sudo chown btcbot:btcbot /opt/btcbot/.env
```

---

## 5. Create data directories

```bash
sudo -u btcbot mkdir -p /opt/btcbot/data/logs
sudo -u btcbot mkdir -p /opt/btcbot/data/backups
```

---

## 6. Install systemd units

```bash
sudo cp /opt/btcbot/deploy/btcbot.service              /etc/systemd/system/
sudo cp /opt/btcbot/deploy/btcbot-backup.service       /etc/systemd/system/
sudo cp /opt/btcbot/deploy/btcbot-backup.timer         /etc/systemd/system/
sudo cp /opt/btcbot/deploy/btcbot-healthcheck.service  /etc/systemd/system/
sudo cp /opt/btcbot/deploy/btcbot-healthcheck.timer    /etc/systemd/system/

sudo systemctl daemon-reload
```

---

## 7. Enable and start

```bash
# Main bot service
sudo systemctl enable btcbot
sudo systemctl start  btcbot
sudo systemctl status btcbot

# Backup timer (runs daily at 02:00 UTC)
sudo systemctl enable btcbot-backup.timer
sudo systemctl start  btcbot-backup.timer

# Health check timer (runs every 10 minutes)
sudo systemctl enable btcbot-healthcheck.timer
sudo systemctl start  btcbot-healthcheck.timer
```

---

## 8. Smoke-test the new deployment

```bash
# Bot is running and logging?
sudo journalctl -u btcbot -f --no-pager

# DB is being written to?
sudo -u btcbot sqlite3 /opt/btcbot/data/btc_trading.db \
    "SELECT max(ts), COUNT(*) FROM option_quotes;"

# Health check passes?
sudo -u btcbot /opt/btcbot/venv/bin/python scripts/healthcheck.py --no-telegram

# Manual backup works?
sudo -u btcbot /opt/btcbot/venv/bin/python scripts/backup.py
ls -lh /opt/btcbot/data/backups/
```

---

## 9. Optional: configure off-host backup

Add to `/opt/btcbot/.env`:

```
BACKUP_OFFHOST_CMD=rsync -az {file} {manifest} user@backup-host:/backups/btcbot/
```

Or S3:

```
BACKUP_OFFHOST_CMD=aws s3 cp {file} s3://my-bucket/btcbot/ && aws s3 cp {manifest} s3://my-bucket/btcbot/
```

The `{file}` and `{manifest}` placeholders are substituted by `scripts/backup.py` at runtime.
Off-host failure is non-fatal — the local backup is always written first.

---

## 10. Update procedure

```bash
cd /opt/btcbot
sudo -u btcbot git pull --ff-only
sudo -u btcbot /opt/btcbot/venv/bin/pip install -r requirements.txt

# If deploy/*.service or deploy/*.timer files changed, re-copy and reload:
# sudo cp /opt/btcbot/deploy/btcbot*.service /etc/systemd/system/
# sudo cp /opt/btcbot/deploy/btcbot*.timer   /etc/systemd/system/
sudo systemctl daemon-reload

sudo systemctl restart btcbot
sudo systemctl status btcbot
```
