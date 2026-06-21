# BTC Paper Trading Bot — VPS Installation Guide

Target: Ubuntu 22.04 LTS, single VPS, non-root service account.

---

## Prerequisites

- Python 3.11+ for the live bot venv (`python3 --version`)
- Python 3.10.x for the `btc_bot` research conda env — installed by §6; do NOT use 3.11 here
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

# Accept Conda ToS as the btcbot user before creating environments.
# The -b flag on the Miniconda installer skips the installer ToS only; conda create
# requires separate ToS acceptance. The btcbot nologin user cannot do it interactively.
sudo -u btcbot /opt/miniconda3/bin/conda tos accept --override-channels --channel defaults
# If the above command is not available on your Conda version, run instead:
#   sudo su -s /bin/bash btcbot -c "/opt/miniconda3/bin/conda init bash && conda tos accept"

# Create the btc_bot environment.
# IMPORTANT: use python=3.10 — the research pinset is validated on Python 3.10.20.
# python=3.11 is for the live bot venv (§3) only.
sudo -u btcbot /opt/miniconda3/bin/conda create -n btc_bot python=3.10 -y
sudo -u btcbot /opt/miniconda3/envs/btc_bot/bin/pip install \
    -r /opt/btcbot/research/requirements-research.txt

# Verify the Python path used by the evidence service units
/opt/miniconda3/envs/btc_bot/bin/python --version
# Expected: Python 3.10.x
# If your path differs, update ExecStart lines in deploy/btcbot-evidence.service
# and deploy/btcbot-fwd-hg.service.
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
sudo cp /opt/btcbot/deploy/btcbot-mapsnap.service      /etc/systemd/system/
sudo cp /opt/btcbot/deploy/btcbot-mapsnap.timer        /etc/systemd/system/
sudo cp /opt/btcbot/deploy/btcbot-fwd-hg.service       /etc/systemd/system/
sudo cp /opt/btcbot/deploy/btcbot-fwd-hg.timer         /etc/systemd/system/

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

# Instrument id->symbol map snapshot timer (runs daily at 12:30 UTC)
sudo systemctl enable btcbot-mapsnap.timer
sudo systemctl start  btcbot-mapsnap.timer

# H5/G1/G3 forward evidence timer (runs daily at 01:00 UTC)
# PRE-CONDITION: btcbot-evidence.timer must be running AND the Parquet corpus
# must be bootstrapped (§9 below) before this timer will succeed.
sudo systemctl enable btcbot-fwd-hg.timer
sudo systemctl start  btcbot-fwd-hg.timer
```

---

## 9. Bootstrap the Parquet research corpus

The H5/G1/G3 forward evidence pipeline (`btcbot-fwd-hg.timer`) reads BTC spot,
perp, and DVOL data from a local Parquet lake at `research/data/raw/`. This step
must complete before the timer can succeed.

Estimated storage: ~200 MB for 2025-01 → current month.
Estimated runtime: 15–45 minutes depending on network speed.

```bash
PY=/opt/miniconda3/envs/btc_bot/bin/python

# Run each script from the research/ directory.
# Scripts skip months that are already ingested (safe to re-run).
cd /opt/btcbot/research

# 1. Spot OHLCV (BTC/USDT 1-minute candles from Binance Vision)
sudo -u btcbot sh -c "cd /opt/btcbot/research && $PY build_corpus.py"

# 2. Perp OHLCV (BTC perpetual 1-minute candles)
sudo -u btcbot sh -c "cd /opt/btcbot/research && $PY build_perp_corpus.py"

# 3. DVOL (Deribit BTC implied volatility index — public API, no auth needed)
sudo -u btcbot sh -c "cd /opt/btcbot/research && $PY build_dvol_corpus.py"
```

Default start dates: spot/perp from 2021-01, DVOL from 2021-03. For a shorter
corpus (e.g. 2025-01 → present), edit `SPOT_START` / `FUND_START` in `build_corpus.py`
and `build_perp_corpus.py`, or run `ingest/binance_ohlcv.py` CLI directly
(`python -m ingest.binance_ohlcv --dataset spot --start 2025-01 --end YYYY-MM`).

Fix corpus file ownership if any file was created as root:

```bash
sudo chown -R btcbot:btcbot /opt/btcbot/research/data/
```

Verify corpus structure:

```bash
ls /opt/btcbot/research/data/raw/spot/BTCUSDT/
ls /opt/btcbot/research/data/raw/perp/BTCUSDT/
ls /opt/btcbot/research/data/raw/dvol/BTC/
```

---

## 10. Initialise and backfill the forward evidence database

Run once before the first timer fires:

```bash
PY=/opt/miniconda3/envs/btc_bot/bin/python

# Init DB schema
sudo -u btcbot sh -c "cd /opt/btcbot/research && $PY forward_evidence/init_db.py"

# Backfill all historical corpus days into evidence DB
sudo -u btcbot sh -c "cd /opt/btcbot/research && $PY forward_evidence/daily_job.py --backfill"

# Verify backfill
sudo -u btcbot sh -c "cd /opt/btcbot/research && $PY forward_evidence/report.py --summary"
```

---

## 11. Smoke-test the new deployment

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

# Force a manual evidence run (H6, don't wait for 00:05 timer)
sudo systemctl start btcbot-evidence.service
sudo journalctl -u btcbot-evidence -n 50 --no-pager

# Force a manual H5/G1/G3 run (don't wait for 01:00 timer)
sudo systemctl start btcbot-fwd-hg.service
sudo journalctl -u btcbot-fwd-hg -n 50 --no-pager

# All timers scheduled?
systemctl list-timers | grep btcbot
```

---

## 12. Optional: configure off-host backup

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

## 13. Update procedure

```bash
cd /opt/btcbot
sudo systemctl stop btcbot
sudo -u btcbot /opt/btcbot/venv/bin/python scripts/backup.py --all   # snapshot first
sudo -u btcbot git pull --ff-only
sudo -u btcbot /opt/btcbot/venv/bin/pip install -r requirements.txt

# Apply any pending schema migrations (idempotent — safe to run every update):
sudo -u btcbot /opt/btcbot/venv/bin/python scripts/migrate.py

# If deploy/*.service or deploy/*.timer files changed, re-copy and reload:
# sudo cp /opt/btcbot/deploy/btcbot*.service /etc/systemd/system/
# sudo cp /opt/btcbot/deploy/btcbot*.timer   /etc/systemd/system/
sudo systemctl daemon-reload

sudo systemctl start btcbot
sudo systemctl status btcbot
```
