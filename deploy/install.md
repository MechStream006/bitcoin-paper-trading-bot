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

## 6. Install conda environment for research code

The Phase E forward evidence pipeline runs under the `btc_bot` conda environment.
The Layer 2 bot uses the venv from Step 3; the evidence service needs conda.

```bash
# Install Miniconda system-wide (skip if already installed)
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O /tmp/mc.sh
sudo bash /tmp/mc.sh -b -p /opt/miniconda3
sudo chown -R btcbot:btcbot /opt/miniconda3

# Create the btc_bot environment and install research dependencies
sudo -u btcbot /opt/miniconda3/bin/conda create -n btc_bot python=3.11 -y
sudo -u btcbot /opt/miniconda3/bin/conda run -n btc_bot pip install \
    -r /opt/btcbot/research/requirements-research.txt

# Verify the Python path used by the evidence service unit
sudo -u btcbot /opt/miniconda3/bin/conda run -n btc_bot which python
# Expected: /opt/miniconda3/envs/btc_bot/bin/python
# If your path differs, update ExecStart lines in deploy/btcbot-evidence.service.
```

Create the evidence data directory:

```bash
sudo -u btcbot mkdir -p /opt/btcbot/research/data
```

---

## 7. Install systemd units

```bash
sudo cp /opt/btcbot/deploy/btcbot.service              /etc/systemd/system/
sudo cp /opt/btcbot/deploy/btcbot-backup.service       /etc/systemd/system/
sudo cp /opt/btcbot/deploy/btcbot-backup.timer         /etc/systemd/system/
sudo cp /opt/btcbot/deploy/btcbot-healthcheck.service  /etc/systemd/system/
sudo cp /opt/btcbot/deploy/btcbot-healthcheck.timer    /etc/systemd/system/
sudo cp /opt/btcbot/deploy/btcbot-evidence.service     /etc/systemd/system/
sudo cp /opt/btcbot/deploy/btcbot-evidence.timer       /etc/systemd/system/

sudo systemctl daemon-reload
```

---

## 8. Enable and start

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

# Forward evidence timer (runs daily at 00:05 UTC)
sudo systemctl enable btcbot-evidence.timer
sudo systemctl start  btcbot-evidence.timer
```

---

## 9. Initialise and backfill the forward evidence database

Run once before the first timer fires:

```bash
# Init DB schema
sudo -u btcbot /opt/miniconda3/bin/conda run -n btc_bot \
    bash -c "cd /opt/btcbot/research && python forward_evidence/init_db.py"

# Backfill all historical corpus days into evidence DB
sudo -u btcbot /opt/miniconda3/bin/conda run -n btc_bot \
    bash -c "cd /opt/btcbot/research && python forward_evidence/daily_job.py --backfill"

# Verify backfill
sudo -u btcbot /opt/miniconda3/bin/conda run -n btc_bot \
    bash -c "cd /opt/btcbot/research && python forward_evidence/report.py --summary"
```

---

## 10. Smoke-test the new deployment

```bash
# Bot is running and logging?
sudo journalctl -u btcbot -f --no-pager

# DB is being written to?
sudo -u btcbot sqlite3 /opt/btcbot/data/btc_trading.db \
    "SELECT max(ts), COUNT(*) FROM option_quotes;"

# Health check passes?
sudo -u btcbot /opt/btcbot/venv/bin/python scripts/healthcheck.py --no-telegram

# Manual trading DB backup works?
sudo -u btcbot /opt/btcbot/venv/bin/python scripts/backup.py
ls -lh /opt/btcbot/data/backups/

# Manual evidence DB backup works?
sudo -u btcbot /opt/btcbot/venv/bin/python scripts/backup.py --evidence
ls -lh /opt/btcbot/data/evidence-backups/

# Force a manual evidence run (don't wait for 00:05 timer)
sudo systemctl start btcbot-evidence.service
sudo journalctl -u btcbot-evidence -n 50 --no-pager

# All timers scheduled?
systemctl list-timers | grep btcbot
```

---

## 11. Optional: configure off-host backup

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

## 12. Update procedure

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
