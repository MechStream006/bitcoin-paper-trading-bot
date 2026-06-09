# BTC Paper Trading Bot — Operations Runbook

This runbook covers day-to-day operations: start/stop, logs, backup,
restore, health check, and troubleshooting. Architecture decisions and
exchange behavior are documented in `CLAUDE.md`.

---

## Service management

| Action | Command |
|--------|---------|
| Start  | `sudo systemctl start btcbot` |
| Stop   | `sudo systemctl stop btcbot` |
| Restart | `sudo systemctl restart btcbot` |
| Status | `sudo systemctl status btcbot` |
| Enable on boot | `sudo systemctl enable btcbot` |
| Disable on boot | `sudo systemctl disable btcbot` |

The service uses `KillSignal=SIGTERM` and `TimeoutStopSec=30`.
`main.py` catches SIGTERM and calls `runtime.drain()`, which shuts down
the WebSocket, flushes the DB write queue, and stops the scheduler
before exiting. The 30-second timeout is a hard kill backstop only.

---

## Viewing logs

```bash
# Live tail (most common)
sudo journalctl -u btcbot -f

# Last 500 lines
sudo journalctl -u btcbot -n 500 --no-pager

# By time range
sudo journalctl -u btcbot --since "2026-06-01 00:00:00" --until "2026-06-02 00:00:00"

# Backup job logs
sudo journalctl -u btcbot-backup --no-pager

# Health check logs
sudo journalctl -u btcbot-healthcheck -n 50 --no-pager
```

The rotating JSONL file is also written to `/opt/btcbot/data/logs/btc_bot.jsonl`
(10 MB per file, 5 backups = 50 MB cap).

---

## Manual backup

```bash
# Standard backup (creates data/backups/btc_trading_YYYYMMDD_HHMMSS.db)
sudo -u btcbot /opt/btcbot/venv/bin/python scripts/backup.py

# Dry run (shows what would be created/deleted, writes nothing)
sudo -u btcbot /opt/btcbot/venv/bin/python scripts/backup.py --dry-run

# Custom paths
sudo -u btcbot /opt/btcbot/venv/bin/python scripts/backup.py \
    --db-path /opt/btcbot/data/btc_trading.db \
    --backup-dir /opt/btcbot/data/backups
```

Backup files: `data/backups/btc_trading_YYYYMMDD_HHMMSS.db`
Manifests:    `data/backups/btc_trading_YYYYMMDD_HHMMSS.manifest.json`

Retention policy (applied automatically on each backup run):
- Daily:   all backups from the last 7 days
- Weekly:  Sunday backups from the last 28 days (4 weeks)
- Monthly: 1st-of-month backups from the last 365 days (12 months)

The scheduled backup timer runs at **02:00 UTC daily** — well clear of
the 11:58–12:06 UTC settlement window. Check timer status:

```bash
sudo systemctl list-timers btcbot-backup.timer
```

---

## Restore from backup

**Full restore procedure** (service stopped during swap):

```bash
# 1. Identify the backup to restore
ls -lh /opt/btcbot/data/backups/

# 2. Run restore (stops service, verifies, swaps, starts service)
sudo -u btcbot /opt/btcbot/venv/bin/python scripts/restore.py \
    /opt/btcbot/data/backups/btc_trading_20260609_020000.db

# 3. Verify
sudo systemctl status btcbot
sudo -u btcbot sqlite3 /opt/btcbot/data/btc_trading.db \
    "SELECT max(ts), COUNT(*) FROM option_quotes;"
```

**If the service is not installed** (local dev restore):

```bash
python scripts/restore.py data/backups/btc_trading_20260609_020000.db \
    --skip-service
```

The restore script:
1. Verifies SHA-256 against the `.manifest.json` sidecar
2. Runs `PRAGMA integrity_check` on the backup file
3. Stops the service
4. Saves the current DB as `btc_trading.db.pre-restore`
5. Copies the backup to the live path
6. Runs a post-swap `PRAGMA integrity_check`
7. Starts the service

If any check fails **before step 5**, the live DB is untouched.
If the post-swap check fails, the pre-restore file is preserved for manual recovery.

**If the restore script exits with an unhandled exception during the copy (step 5):**
The service will be stopped and the live DB may be partially written. Recover manually:

```bash
# 1. Confirm the pre-restore file exists
ls -lh /opt/btcbot/data/btc_trading.db.pre-restore

# 2. Verify it is intact
sudo -u btcbot sqlite3 /opt/btcbot/data/btc_trading.db.pre-restore \
    "PRAGMA integrity_check;"

# 3. If OK, restore it and restart
sudo -u btcbot cp /opt/btcbot/data/btc_trading.db.pre-restore \
    /opt/btcbot/data/btc_trading.db
sudo systemctl start btcbot
sudo systemctl status btcbot
```

---

## Health check

```bash
# Manual run (no Telegram, prints JSON summary to stdout)
sudo -u btcbot /opt/btcbot/venv/bin/python scripts/healthcheck.py --no-telegram

# With Telegram alerts on WARNING too
sudo -u btcbot /opt/btcbot/venv/bin/python scripts/healthcheck.py --alert-on-warning

# Full integrity check (slower, use for post-restore or daily spot check)
sudo -u btcbot /opt/btcbot/venv/bin/python scripts/healthcheck.py \
    --no-telegram --full-integrity
```

Exit codes: `0` = all OK, `1` = at least one WARNING, `2` = at least one CRITICAL.

Checks performed:

| Check | WARNING threshold | CRITICAL threshold |
|-------|------------------|-------------------|
| service | memory >= 512 MB | not active / memory >= 1 GB |
| disk | free < 5 GB | free < 2 GB |
| db_liveness | last tick > 60s ago | last tick > 300s ago |
| wal | WAL file > 200 MB | — |
| backup | newest backup > 48h old | — |
| integrity | — | integrity_check != ok |

The health check timer runs every **10 minutes**.
Check timer status:

```bash
sudo systemctl list-timers btcbot-healthcheck.timer
```

---

## Troubleshooting

### Bot not starting / exits immediately

```bash
sudo journalctl -u btcbot -n 100 --no-pager
```

Look for `[main] bootstrap failed` or `[supervisor]` FATAL lines.
Common causes: bad `.env` credentials, DB locked, port conflict.

### WebSocket disconnects / reconnect loops

Normal behavior — the supervisor restarts components automatically.
Check restart frequency:

```bash
sudo journalctl -u btcbot --since "1 hour ago" | grep -c "reconnect\|restart"
```

If restarts exceed 3 per 5 minutes the supervisor escalates to FATAL and
the service exits. Systemd will restart it after 10 seconds (`RestartSec=10`).

### Restart storm (supervisor FATAL loop)

Symptom: `systemctl status btcbot` shows repeated activating → failed cycles.
The supervisor escalated to FATAL, the process exited, and systemd keeps restarting it.

```bash
# 1. Check how many times systemd has restarted the service
sudo systemctl status btcbot | grep -E "Main PID|Restart"

# 2. Read the last crash reason
sudo journalctl -u btcbot -n 100 --no-pager | grep -E "FATAL|bootstrap|ERROR"
```

**Self-healing causes** (transient network / exchange outage): the bot will stabilize
on its own within a few restart cycles once connectivity is restored. No action needed.

**Requires intervention:**

- `[main] bootstrap failed` — bad `.env` credentials, DB locked, or empty universe.
  Fix the root cause, then: `sudo systemctl restart btcbot`
- `[supervisor] FATAL` with `warmup timeout` — WS could not warm up within the allowed
  window. Usually a transient exchange issue; wait 2–5 minutes and retry.
- Repeated FATAL within minutes of each restart — stop the service, read the full log,
  fix the root cause before re-enabling:

```bash
sudo systemctl stop btcbot
sudo journalctl -u btcbot -n 500 --no-pager
# fix root cause, then:
sudo systemctl start btcbot
```

### DB growing faster than expected

```bash
sudo -u btcbot sqlite3 /opt/btcbot/data/btc_trading.db \
    "SELECT COUNT(*) FROM option_quotes; SELECT max(ts) FROM option_quotes;"
```

Expected growth: ~3,800 rows/hour, ~90,000 rows/day, ~2.7 M rows/month.
DB size: ~1–1.5 GB/year uncompressed (see CLAUDE.md storage plan).

### WAL file very large

A large WAL means the checkpoint has not run recently.
Force a checkpoint while the bot is running:

```bash
sudo -u btcbot sqlite3 /opt/btcbot/data/btc_trading.db \
    "PRAGMA wal_checkpoint(TRUNCATE);"
```

### Backup failed

```bash
sudo journalctl -u btcbot-backup --no-pager
```

Backup failure is logged but does not affect the running bot.
Run `scripts/backup.py` manually to retry.

### Disk near full

1. Run `scripts/backup.py` to trigger retention rotation (deletes old files).
2. Check WAL: `ls -lh /opt/btcbot/data/btc_trading.db-wal`.
3. Check log rotation: `ls -lh /opt/btcbot/data/logs/`.
4. If still critical, stop the bot, run `VACUUM` on the DB, restart.

### Telegram alerts not arriving

```bash
# Test Telegram credentials manually
python -c "
import os; from dotenv import load_dotenv; load_dotenv()
from src.alerts.telegram import send_message
send_message(os.getenv('TELEGRAM_BOT_TOKEN'), os.getenv('TELEGRAM_CHANNEL_ID'), 'test')
"
```

---

## Timer status reference

```bash
sudo systemctl list-timers --all | grep btcbot
```

Expected output (all ACTIVE):
```
btcbot-backup.timer         active  waiting  ...
btcbot-healthcheck.timer    active  waiting  ...
```

---

## File locations

| Item | Path |
|------|------|
| Code | `/opt/btcbot/` |
| Database | `/opt/btcbot/data/btc_trading.db` |
| Log file | `/opt/btcbot/data/logs/btc_bot.jsonl` |
| Backups | `/opt/btcbot/data/backups/` |
| Config | `/opt/btcbot/.env` (600, owner btcbot) |
| systemd units | `/etc/systemd/system/btcbot*.service` |
| Journal logs | `journalctl -u btcbot` |
