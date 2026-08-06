# EC2 Deployment Runbook — Phase 4 D0 Baseline + Option A

**Target commit:** `cea23f0880400af7e1e820156b6de9ae75f44571`
**Prepared:** 2026-08-06 · **Status:** ⏸ awaiting Rohith's authorisation
**Companion documents:** `deployment-plan-phase4-d0-baseline.md` (the audit and
the reasoning), `deploy/install.md` (fresh-host install), `docs/runbook.md`
(steady-state operations).

This runbook is the **executable** half. Every command is copy-paste ready and
every expected result is stated, so a step that does not match is a stop, not a
judgement call.

---

## 1. What this deploys

Four production files, from WBS 3.10 plus the Option A fix:

| File | Change |
|---|---|
| `scripts/backup.py` | `REQUIRED_DB_LABELS` / `is_required()`; missing optional DB → skip, rc 0 |
| `scripts/healthcheck.py` | `check_backup_freshness(..., required=)`; never-configured optional → INFO |
| `src/api/config.py` | `ledger_backup_dir` (P1 Increment 1) |
| `src/api/services.py` | Same classification wiring as the CLI |

**It fixes two defects that would fire on day one of the current `main`:**

- **PROD-1** — `backup.py --all` returned 1 because `label_store.db` does not
  exist on EC2, failing `btcbot-backup.service` daily while all four real
  backups succeeded.
- **PROD-2** — `data/label-store-backups/` could never exist → WARNING → and
  `btcbot-healthcheck` runs **every 10 minutes** with `--alert-on-warning` and
  **no rate limiting**: 144 false Telegram alerts and 144 failed units per day.

### What this deployment does NOT do

| | |
|---|---|
| Schema migration | **None.** `migrate.py TARGET_VERSION` is still **5** |
| Dependency change | **None.** `requirements*.txt` unchanged — no venv rebuild |
| `deploy/` unit changes | **None.** No unit file is copied or edited |
| Bot core restart | **Not required** — `src/main.py` and its import graph are unchanged |
| Research corpus work | **None on EC2.** D0a/D0b were dev-machine only |

---

## 2. GO / NO-GO gate — all must be TRUE before Step 0

- [ ] Rohith has explicitly authorised this deployment
- [ ] `origin/main` is at `cea23f0` (verify in Step 0d — do not assume)
- [ ] Deployment window respects §2.1
- [ ] You can reach the host over SSH and have `sudo`
- [ ] You are prepared to complete Steps 0–5 in one sitting (~20 min)

### 2.1 Timing constraints

Avoid these UTC windows so no oneshot fires against a half-updated tree, and so
the settlement rollover is never disturbed:

| Window (UTC) | Why |
|---|---|
| `00:05`, `01:00`, `01:30`, `02:00`, `12:30` | Timer fire times |
| `11:58 – 12:06` | Daily options settlement dead zone (CLAUDE.md §14) |

**Recommended window: `14:00 – 23:00 UTC`** — after `mapsnap` (12:30), well
clear of settlement, and long before `evidence` (00:05).

---

## 3. Session setup — paste once per SSH session

```bash
# ON THE SERVER
export DEPLOY_SHA=cea23f0880400af7e1e820156b6de9ae75f44571
export BOT=/opt/btcbot
export PY=$BOT/venv/bin/python
export TIMERS="btcbot-backup.timer btcbot-healthcheck.timer btcbot-certify.timer btcbot-mapsnap.timer btcbot-evidence.timer btcbot-fwd-hg.timer"
cd $BOT
```

---

## STEP 0 — Pre-flight (no risk, nothing changes)

```bash
# 0a. Record the rollback target — WRITE THIS DOWN before anything else
git -C $BOT rev-parse HEAD | tee /tmp/rollback-sha.txt

# 0b. Host tree must be clean — local drift means STOP
git -C $BOT status --porcelain
# EXPECT: no output

# 0c. Pre-deploy backup. This is ALSO the PROD-1 "before" measurement.
sudo -u btcbot $PY scripts/backup.py --all; echo "exit=$?"
# EXPECT: exit=1  ← this is PROD-1, and it is exactly what we are fixing.
#         Note WHICH label failed; all four real DBs should still have
#         succeeded above the failure line.

# 0d. Prove the backups actually landed (BK4 — never infer from exit code)
ls -lh $BOT/data/backups/ | tail -3
ls -lh $BOT/data/ledger-backups/ | tail -3

# 0e. Confirm the target commit exists on the remote
sudo -u btcbot git -C $BOT fetch origin
git -C $BOT log --oneline -1 origin/main
# EXPECT: cea23f0 fix(ops): classify databases required vs optional ...

# 0f. Disk headroom
df -h $BOT | tail -1
# EXPECT: ≥20% free

# 0g. Current unit state, for the after-comparison
systemctl is-active btcbot.service btcbot-api.service
systemctl --failed --no-legend | grep btcbot || echo "no failed btcbot units"
```

⛔ **STOP** if 0b shows drift, or 0e does not show `cea23f0`.

> 0c returning `exit=1` is the **expected pre-state**, not a problem. If it
> returns 0, the host is not where this runbook assumes — stop and re-audit.

---

## STEP 1 — Stop timers, then pull

```bash
# 1a. Stop the six timers. btcbot.service and btcbot-api.service keep running.
sudo systemctl stop $TIMERS
systemctl is-active $TIMERS
# EXPECT: six lines, all "inactive"

# 1b. Pull to the exact target commit
sudo -u btcbot git -C $BOT checkout main
sudo -u btcbot git -C $BOT pull --ff-only origin main
git -C $BOT rev-parse HEAD
# EXPECT: cea23f0880400af7e1e820156b6de9ae75f44571

# 1c. Confirm no dependency change (belt and braces — §1 says none)
git -C $BOT diff --name-only $(cat /tmp/rollback-sha.txt)..HEAD -- requirements.txt requirements-api.txt deploy/
# EXPECT: no output → no pip install, no daemon-reload needed
```

⛔ **STOP** if 1b's SHA differs, or 1c prints any filename.

---

## STEP 2 — Smoke tests (the PROD-1 / PROD-2 gates)

These are the two measurements the whole deployment exists to change. Both are
non-destructive.

```bash
# 2a. PROD-1 GATE — the failing command must now succeed
sudo -u btcbot $PY scripts/backup.py --all --dry-run; echo "exit=$?"
# EXPECT: exit=0, and a "[skip] optional DB not present" line for label_store

# 2b. PROD-1 GATE, for real — a genuine backup run
sudo -u btcbot $PY scripts/backup.py --all; echo "exit=$?"
# EXPECT: exit=0  ← PROD-1 RESOLVED

# 2c. PROD-2 GATE — no false WARNING, and no Telegram traffic
sudo -u btcbot $PY scripts/healthcheck.py --no-telegram; echo "exit=$?"
# EXPECT: exit=0
#         label_store_backup severity = INFO   ← PROD-2 RESOLVED
#         overall not escalated by label_store

# 2d. Migration is a no-op confirmation, not a requirement
sudo -u btcbot $PY scripts/migrate.py --dry-run
# EXPECT: already at version 5, nothing to apply
```

⛔ **STOP and roll back (§7)** if 2b is non-zero, or 2c shows
`label_store_backup` at WARNING or above.

---

## STEP 3 — Service restart order

Order is deliberate: the API is the only long-running process whose code
changed, and timers come back last so a failed smoke test leaves them stopped.

| # | Action | Why |
|---|---|---|
| 1 | **Do NOT restart `btcbot.service`** | Bot core unchanged; restarting risks a collection gap on a 24/7 market for zero benefit |
| 2 | Restart `btcbot-api.service` | `src/api/services.py` + `config.py` changed — this is their **first runtime exercise** |
| 3 | Start the six timers | Only after the API is confirmed healthy |

```bash
# 3a. API — the only required restart
sudo systemctl restart btcbot-api.service
sleep 3
systemctl is-active btcbot-api.service
# EXPECT: active

# 3b. API liveness + readiness (unauthenticated probes, localhost only)
curl -fsS http://127.0.0.1:9109/health  | python3 -m json.tool
# EXPECT: {"status": "ok", ...}
curl -fsS http://127.0.0.1:9109/readyz  | python3 -m json.tool
# EXPECT: {"ready": true, ...}

# 3c. The authenticated roll-up — this is the line that exercises the changed
#     classification code inside the API. Token is read from .env, never echoed.
API_TOKEN=$(sudo grep -E '^API_TOKEN=' $BOT/.env | cut -d= -f2-) \
  curl -fsS -H "Authorization: Bearer $API_TOKEN" \
  http://127.0.0.1:9109/api/v1/runtime/health | python3 -m json.tool
# EXPECT: label_store_backup present with severity INFO — matching 2c exactly.
#         CLI/API disagreement here means STOP.

# 3d. Timers last
sudo systemctl start $TIMERS
systemctl list-timers 'btcbot-*' --no-pager
# EXPECT: six timers, all with a sane NEXT
```

⛔ **STOP and roll back** if 3a is not `active`, or 3c disagrees with 2c.

> 3c is important: `tests/test_api.py` cannot be collected in any local conda
> env (pre-existing `fastapi` gap), so this is the **first runtime test** of
> `services.py`'s change anywhere. It is covered structurally on the dev
> machine by an AST parity guard, but not executed until here.

---

## STEP 4 — Telegram verification

Both directions must be checked. A silent channel proves nothing on its own —
it could mean "no false alerts" or "alerting is broken".

```bash
# 4a. POSITIVE — prove the channel is alive with a deliberate test message.
sudo -u btcbot $BOT/venv/bin/python - <<'EOF'
import os, sys
sys.path.insert(0, "/opt/btcbot")
from dotenv import dotenv_values
from src.alerts.telegram import send_message
env = dotenv_values("/opt/btcbot/.env")
r = send_message(env["TELEGRAM_BOT_TOKEN"], env["TELEGRAM_CHANNEL_ID"],
                 "deploy cea23f0: channel check, ignore")
print("ok:", r.get("ok"))
EOF
# EXPECT: ok: True, and the message visible in the BTC Paper Alerts channel.

# 4b. NEGATIVE — the real PROD-2 proof. Watch for 30 minutes.
#     healthcheck fires every 10 min, so this covers ≥3 runs.
journalctl -u btcbot-healthcheck --since '-30m' --no-pager | grep -i "label_store"
# EXPECT: either no output, or lines showing INFO. Any WARNING = PROD-2 not fixed.

# 4c. Confirm no alert was actually SENT for label_store
journalctl -u btcbot-healthcheck --since '-30m' --no-pager | grep -iE "telegram|alert"
# EXPECT: no alert-send entries attributable to label_store_backup
```

**Pass criterion:** 4a delivers, **and** 30 minutes elapse with zero
`label_store` alerts. Before this deployment that window would have contained
**three** false alerts.

⛔ If 4a fails, alerting is broken independently of this deployment — that is
its own incident. Do not treat 4b's silence as reassuring until 4a passes.

---

## STEP 5 — Immediate verification checklist

- [ ] `git -C /opt/btcbot rev-parse HEAD` = `cea23f0…`
- [ ] `git -C /opt/btcbot status --porcelain` — empty
- [ ] `backup.py --all` → **exit 0** (PROD-1)
- [ ] `healthcheck.py --no-telegram` → exit 0, `label_store_backup` = **INFO** (PROD-2)
- [ ] API `/health` ok · `/readyz` ready · `/api/v1/runtime/health` agrees with the CLI
- [ ] `systemctl list-timers 'btcbot-*'` — 6 active, sane next-fire
- [ ] `systemctl --failed | grep btcbot` — none
- [ ] `btcbot.service` still `active` and **never restarted**
- [ ] Telegram test message delivered (4a)
- [ ] 30 min elapsed, zero false `label_store` alerts (4b)
- [ ] All four real backup sets have fresh manifests

---

## 6. Post-deployment monitoring

**First hour**
- [ ] 6 consecutive clean healthcheck runs
- [ ] Zero failed units
- [ ] Telegram carries only expected traffic
- [ ] API `/health` still responding

**First 24 hours** — each timer must complete once:
- [ ] `btcbot-evidence` 00:05
- [ ] `btcbot-fwd-hg` 01:00
- [ ] `btcbot-certify` 01:30
- [ ] **`btcbot-backup` 02:00 → exit 0** ← the definitive PROD-1 gate, unattended
- [ ] `btcbot-mapsnap` 12:30
- [ ] Four backup sets present with fresh manifests (label-store correctly absent)
- [ ] Telegram volume matches the pre-deployment baseline

```bash
# The single most important post-deploy check, run the morning after:
systemctl status btcbot-backup.service --no-pager | head -5
journalctl -u btcbot-backup --since '-25h' --no-pager | tail -20
# EXPECT: "status=0/SUCCESS", a [skip] line for label_store, no failure.
```

**First week**
- [ ] No new failed units
- [ ] Backup freshness never WARNING
- [ ] WAL size stable · disk trend flat
- [ ] Forward-evidence cadence unbroken (a lost calendar day is unrecoverable)

---

## 7. Rollback procedure

Rollback is cheap: four files, **no schema change, no dependency change, no
data mutation**. Nothing needs undoing beyond the code.

```bash
# R1. Stop timers first
sudo systemctl stop $TIMERS

# R2. Return to the recorded pre-deploy commit
sudo -u btcbot git -C $BOT checkout $(cat /tmp/rollback-sha.txt)
git -C $BOT rev-parse HEAD    # confirm it matches /tmp/rollback-sha.txt

# R3. Restart the API (the only long-running process whose code moved)
sudo systemctl restart btcbot-api.service
systemctl is-active btcbot-api.service

# R4. Timers back
sudo systemctl start $TIMERS
```

### Trigger → action

| Trigger | Action |
|---|---|
| Backup unit fails | Full rollback (R1–R4). No data implication |
| Telegram flooding | **Immediately** `sudo systemctl stop btcbot-healthcheck.timer`, then R1–R4 |
| API will not start | R1–R4; if it still fails, the cause predates this deploy |
| Bot core misbehaves | Should be impossible (unchanged). R1–R4, then `sudo systemctl restart btcbot.service` |
| Data corruption | `scripts/restore.py <backup>` — ⚠️ **see the TD-P1-002 warning below** |

### ⚠️ TD-P1-002 — `restore.py` is not WAL-aware

The trading DB is WAL-mode in production. `restore.py` copies only the `.db`
file — it never checkpoints the source nor clears `-wal`/`-shm` at the
destination, so a restore **can silently resurrect stale state**. Before any
`restore.py` invocation:

```bash
sudo systemctl stop btcbot.service
sudo -u btcbot sqlite3 $BOT/data/btc_trading.db "PRAGMA wal_checkpoint(TRUNCATE);"
sudo -u btcbot rm -f $BOT/data/btc_trading.db-wal $BOT/data/btc_trading.db-shm
# only now run restore.py
```

**Rollback verification:** `backup.py --all` → exit 1 (the pre-fix state, expected) ·
`healthcheck.py --no-telegram` runs · no failed units besides the known PROD-1/2 ·
bot heartbeat resumes.

**Rollback restores PROD-1 and PROD-2.** Only roll back for a defect worse than
144 false alerts a day, and re-fix forward rather than sitting on the old state.

---

## 8. Remaining operational risks (unchanged by this deployment)

| # | Risk | Severity |
|---|---|---|
| **R3** | BK2 production restore drill **never performed** | 🟠 High |
| **R4** | BK3 off-host replication **never activated** — every backup is on this one instance; instance loss = total loss | 🟠 High |
| **R5** | `TD-P1-002` — `restore.py` not WAL-aware (§7) | 🟠 High |
| **R6** | `healthcheck.py` has **no alert rate limiting**, unlike the bot's own alert paths — PROD-2 was its first real consequence; the structural gap remains | 🟡 Medium |
| **R7** | EC2's research corpus is not conformed (dev-machine only, correct and expected) | 🟢 Low |
| **R8** | Two Python runtimes (venv 3.11 + conda 3.10.20) — deliberate | 🟢 Low |

**R3 + R4 together remain the platform's largest exposure**: backups are taken,
manifest-verified, and never tested by a real production restore, on a host
whose loss would take the backups with it. Neither is caused by this
deployment; neither is fixed by it.

---

## 9. Sign-off

| Role | Name | Date |
|---|---|---|
| Runbook prepared | Claude Opus 5 | 2026-08-06 |
| Deployment authorisation | **Rohith Suresh** | **_____ PENDING _____** |
| Deployment executed | | |
| PROD-1 gate (backup exit 0) | | |
| PROD-2 gate (INFO, 30 min quiet) | | |
| 24h unattended backup verified | | |
