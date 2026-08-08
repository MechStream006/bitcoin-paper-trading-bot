# Recovery Checklist

Operator tick-list for an active incident. Procedures are in the
[Restore Runbook](restore-runbook.md); this page is the order of operations.

**Rule: do not restore until you know what you lost.** A restore over a healthy
store is itself a data-loss event.

---

## Phase 0 — Stop and assess (do not restore yet)

- [ ] Identify the affected store(s) and the symptom
- [ ] `sudo systemctl status btcbot btcbot-api`
- [ ] `sudo -u btcbot python scripts/healthcheck.py --no-telegram | head -40`
- [ ] `sqlite3 <store> "PRAGMA integrity_check;"`
- [ ] For a governance store, `verify_chain()` — **a store can pass `integrity_check` and still have a broken chain**
- [ ] Decide: corruption, deletion, bad write, or host loss?

## Phase 1 — Preserve evidence

- [ ] **Copy the damaged file aside before any repair.** `cp <store> <store>.incident-<ts>`
- [ ] Capture `journalctl -u btcbot --since '-2h' > /tmp/incident.log`
- [ ] Record the last known-good backup timestamp and its manifest `row_counts`

## Phase 2 — Choose the recovery point

- [ ] `ls -la <backup_dir>/`
- [ ] `cat <backup_dir>/<name>.manifest.json`
- [ ] **Check `row_counts` is non-empty** — an empty backup will destroy live data
- [ ] Confirm the SHA in the manifest matches the file on disk
- [ ] Accept the data loss window: backups run daily at 02:00 UTC

## Phase 3 — Restore

- [ ] Stop the holding service if any
- [ ] `PRAGMA wal_checkpoint(TRUNCATE);` then remove `-wal` / `-shm`
- [ ] Run `scripts/restore.py` (see runbook §3.3)
- [ ] Confirm `.pre-restore` was created — this is the rollback

## Phase 4 — Verify before declaring recovery

- [ ] `PRAGMA integrity_check` → `ok`
- [ ] Row counts match the manifest
- [ ] **Governance store: `verify_chain()` → `ok=True`, expected receipt count**
- [ ] Dataset Builder: citation consistency, 0 orphans
- [ ] Restart services; confirm `active`
- [ ] Collection resumes — `quotes=N/N` within 3 minutes
- [ ] `healthcheck.py` → `overall INFO` or `OK`, 0 non-OK
- [ ] Telegram alert delivered

## Phase 5 — Rollback (if Phase 4 fails)

- [ ] `mv <store>.pre-restore <store>`
- [ ] Restart services, re-run Phase 4 checks
- [ ] If rollback also fails, restore from the **previous** backup generation

## Phase 6 — Close out

- [ ] Take a fresh backup of the recovered state
- [ ] Record: what failed, when, recovery point used, data lost, time to recover
- [ ] File the incident under `docs/ops/incident-<date>-<slug>.md`
- [ ] If a control failed, raise it as debt — do not fix silently

---

## Total-host-loss path

**Not currently available.** BK3 is not active: no off-host copy of any store
exists. Loss of the canonical host today loses the Feature Store, Label Store,
Dataset Builder store, and the 548 MB corpus outright.

Once BK3 is active, the path is: provision host → install `age` → retrieve
ciphertext → `offhost_backup.py verify` → `decrypt --identity` → runbook §3.3 →
Phase 4 verification.

**Prerequisite the operator must confirm now, not during an incident:** the
`age` identity is stored somewhere that survives the loss of the bot host.
