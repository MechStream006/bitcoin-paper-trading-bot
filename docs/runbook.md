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

### Per-store flags

`--all` covers every store below. Each flag may also be used alone, in which
case only that store is backed up.

| Flag | Database | Backup directory |
|---|---|---|
| *(no flag)* | `data/btc_trading.db` | `data/backups/` |
| `--evidence` | `research/data/forward_evidence.db` | `data/evidence-backups/` |
| `--fwd-hg` | `research/data/evidence_forward.db` | `data/evidence-fwd-backups/` |
| `--ledger` | `research/evidence_library/ledger.db` | `data/ledger-backups/` |
| `--label-store` | `research/label_store/label_store.db` | `data/label-store-backups/` |
| `--dataset-builder` | `research/dataset_builder/dataset_builder.db` | `data/dataset-builder-backups/` |
| `--feature-store` | `research/feature_store/feature_store.db` | `data/feature-store-backups/` |

Only `btc_trading.db` is REQUIRED; its absence fails the run. The six research
databases are OPTIONAL — a missing file is logged as SKIPPED and does not
affect the exit code (PROD-1, 2026-08-06), because each is created by a
research process that may legitimately never have run on a given host.

Every store has a matching `check_backup_freshness` entry in
`scripts/healthcheck.py` and in the read-only API's `runtime_health()`, and all
are restorable through the same store-agnostic `scripts/restore.py`:

```bash
python scripts/restore.py data/feature-store-backups/feature_store_<ts>.db \
    --db-path research/feature_store/feature_store.db --skip-service
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

**After any restore, check whether the schema needs to catch up** — a backup
taken before the latest `migrate.py` shipped will restore at its old
`user_version`:

```bash
python scripts/migrate.py --db-path /opt/btcbot/data/btc_trading.db --dry-run
# if it shows pending steps:
python scripts/migrate.py --db-path /opt/btcbot/data/btc_trading.db
```

---

## Encrypted off-host backup (ADR-0001 §12 BK3)

Full design, key management, and DR procedure:
`docs/ops/offhost-backup.md`. **Not enabled in production yet** — see that
document §8 for the gated enable sequence.

```bash
# Encrypt one artifact manually (host side)
python scripts/offhost_backup.py encrypt data/ledger-backups/ledger_<ts>.db

# Audit an off-host copy WITHOUT the private key — safe to run anywhere
python scripts/offhost_backup.py verify /srv/backups/ledger_<ts>.db.age

# Backup + encrypt in one step (what the unit will eventually run)
python scripts/backup.py --all --encrypt
```

**Key facts, in one place:**
- The host holds only the **public** recipient (`age1...`). The private
  identity (`AGE-SECRET-KEY-1...`) must **never** be on the production host.
- **Losing the private key makes every off-host backup permanently
  unrecoverable.** Keep two independent copies (password manager + paper).
- `verify` proves a ciphertext is *intact*, **not** that it is *decryptable*.
  Only a real decrypt drill proves the key still works — do one quarterly.
- Encryption is **fail-closed**: if it fails, the off-host transfer is
  skipped rather than shipping plaintext.

**Monitoring the transfer leg (P1 Increment 6):** `scripts/healthcheck.py`
now reports `offhost_backup` / `ledger_offhost_backup` checks, read from a
`.offhost-status.json` sidecar `backup.py` writes after every off-host
attempt. These are **silent (INFO)** until off-host backup is actually
configured — no alert noise pre-activation. Once configured:

| `healthcheck.py` output | Meaning |
|---|---|
| `OK` | Last successful transfer < 48h ago |
| `WARNING` | Last success ≥ 48h ago, **or** the latest attempt failed |
| `CRITICAL` | Last success ≥ 7 days ago, or off-host is configured but has **never** succeeded even once |

This proves the transfer *command* ran and exited 0 — it does **not** prove
the bytes at the target are intact. That is what `offhost_backup.py verify`
(no key needed) and the quarterly decrypt drill are for; run them, don't
rely on a green healthcheck alone.

**Governance store durability (Program P1).** For a plain-language,
per-claim statement of what has and has not been established about ledger
schema integrity, backup, and off-host durability, see
`docs/governance/p1_assurance_declaration.md`. Decision record:
`docs/adr/0002-p1-governance-store-consolidation.md`. Full engineering
history: `CLAUDE.md` §18. Migration script inventory:
`research/evidence_library/MIGRATIONS.md`.

---

## Restore drill (quarterly — ADR-0001 §12 BK2)

BK2: *"Backups are worthless until a restore drill proves them."* Full
write-up of the first drill: `docs/ops/restore-drill-2026-07-19.md`.

**Label Store** (Phase 3 WBS 3.10, repository-side only — not yet on any
production host): `docs/ops/label-store-restore-drill-2026-08-06.md`. Not
folded into the quarterly production script below yet, since
`label_store.db` has no production deployment to drill against until WBS
3.4/host-side activation; add a third `restore.py` invocation there
(mirroring the `btc_trading`/`ledger` pair) once it does.

Repeat quarterly, **on the production host**, against a real backup
artifact, restoring into an isolated scratch path (never the live DB path):

```bash
DRILL=/tmp/restore-drill-$(date +%Y%m%d)
mkdir -p "$DRILL"

# 1. Restore the most recent real artifacts into isolation
python scripts/restore.py /opt/btcbot/data/backups/btc_trading_<ts>.db \
    --db-path "$DRILL/restored_btc_trading.db" --skip-service
python scripts/restore.py /opt/btcbot/data/ledger-backups/ledger_<ts>.db \
    --db-path "$DRILL/restored_ledger.db" --skip-service

# 2. Governance chain — the two checks BK2 names explicitly
cd research/evidence_library
python -c "import receipt_ledger; print(receipt_ledger.verify_chain(r'$DRILL/restored_ledger.db'))"
python -c "
from pathlib import Path
import report
print(report.run_check(Path('registry.yaml'), Path(r'$DRILL/restored_ledger.db'), Path('thresholds.yaml')))
"

# 3. Application-level check against the restored trading copy
python scripts/healthcheck.py --db-path "$DRILL/restored_btc_trading.db" \
    --no-forward-check --no-telegram --full-integrity

# 4. Clean up
rm -rf "$DRILL"
```

Pass criteria: both restores complete, `verify_chain().ok == True`,
`run_check(...) == 0`, `healthcheck.py`'s `integrity` check is `OK` (ignore
`db_liveness`/`backup` — they report on the restored *file's* age/location,
not the drill's success).

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

## Daily evidence certifier

Certifies each completed UTC collection day and writes a verdict to
`daily_certifier_log`. This is the gate CLAUDE.md refers to when it says
evidence accumulation requires "the daily certifier reports green".

Runs at **01:30 UTC** via `btcbot-certify.timer`.

```bash
# Timer status / next run
sudo systemctl list-timers btcbot-certify.timer

# Logs (verdict JSON is printed in full)
sudo journalctl -u btcbot-certify.service -n 100 --no-pager

# Manual run of the scheduled job (safe: skips days already certified)
sudo -u btcbot /opt/btcbot/venv/bin/python scripts/certify_catchup.py

# See what it WOULD do, without certifying anything
sudo -u btcbot /opt/btcbot/venv/bin/python scripts/certify_catchup.py --dry-run

# Re-certify one specific day (deliberate override — the wrapper never does this)
sudo -u btcbot /opt/btcbot/venv/bin/python scripts/certify_day.py --date 2026-07-18

# Backfill a longer outage (bounded lookback, oldest first)
sudo -u btcbot /opt/btcbot/venv/bin/python scripts/certify_catchup.py --lookback 30
```

### Reading the result — the unit state is NOT the verdict

| Verdict | `certify_day.py` exit | Unit state | Meaning |
|---|---|---|---|
| GREEN | 0 | success | Day is admissible evidence |
| AMBER | 1 | **success** | Requires adjudication before research use |
| RED | 2 | **success** | Day is non-admissible |
| error | 3 | **failed** | Certifier could not reach a verdict |

`SuccessExitStatus=0 1 2` is deliberate. The job's purpose is to *produce* a
verdict, not to produce a particular one. **While `PLATFORM=testnet`, C7 marks
every day AMBER by policy** — without this the unit would report `failed` every
single day and the operator would learn to ignore it.

**So: a green unit does not mean a green day.** Read the verdict from the DB:

```bash
sudo -u btcbot sqlite3 -header -column /opt/btcbot/data/btc_trading.db \
  "SELECT day, verdict, platform FROM daily_certifier_log ORDER BY day DESC LIMIT 14;"
```

AMBER and RED days also send a Telegram alert (most recent day only — a
backfill after an outage suppresses alerts for older days to avoid an alert
storm, but still records every verdict).

### Missed executions

`Persistent=true` replays only **one** missed timer activation, which would
leave a multi-day outage permanently uncertified. `certify_catchup.py` closes
that gap: it certifies every day in a 7-day lookback that has collection data
but no verdict yet. Days with no `collection_metrics` rows are skipped, not
marked RED — an outage is a gap in the collection record (already reported by
the heartbeat and healthcheck), not a data-quality verdict.

For an outage longer than 7 days, pass a larger `--lookback`.

### Requires schema v4+

The certifier needs `collection_metrics` (v2/v3) and `daily_certifier_log` (v4).
On an older database it exits 3 and the unit correctly reports `failed`. Fix by
running the migration, not by editing the unit:

```bash
python scripts/migrate.py --dry-run && python scripts/migrate.py
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
