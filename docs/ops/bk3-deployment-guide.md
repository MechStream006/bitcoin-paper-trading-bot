# BK3 — Off-Host Replication Deployment Guide

| Field | Value |
|---|---|
| **Obligation** | ADR-0001 §12 **BK3** — off-host replication. Status: **NOT ESTABLISHED** |
| **Design** | Derived entirely from existing infrastructure. **No new architecture** |
| **Status** | 🔴 **BLOCKED — three owner prerequisites (§3).** Everything else is built and verified |
| **Date** | 2026-08-08 |

---

## 1. Everything BK3 needs already exists

Verified from source at `82ce7c0`. This is an **activation**, not a build:

| Capability | Where | State |
|---|---|---|
| Encrypt a backup | `offhost_backup.encrypt_backup()` (`age`, recipient from env) | ✅ built |
| Ciphertext manifest | `build_age_manifest()` → `.age-manifest.json` | ✅ built |
| Verify ciphertext | `offhost_backup.py verify` | ✅ built |
| Decrypt | `offhost_backup.py decrypt --identity` | ✅ built |
| Ship off-host | `BACKUP_OFFHOST_CMD` template, `{file}` + `{manifest}` | ✅ built |
| Fail-closed | encryption failure **aborts the transfer**; plaintext never ships | ✅ built |
| Status sidecar | `.offhost-status.json` per backup dir, carries `last_success_at` | ✅ built |
| Freshness monitoring | `healthcheck.check_offhost_freshness()` — INFO/OK/WARNING/CRITICAL | ✅ built |
| Local retention of `.age` | `_apply_retention()` prunes ciphertext with its plaintext | ✅ built |
| Tests | `tests/test_offhost_backup.py` — 29 | ✅ passing |

## 2. Coverage — what replicates, and what does not

`backup.py --all --encrypt` covers **all seven** backed-up databases, which
carry **every governance receipt** on the platform:

| Asset | Covered | Carrier |
|---|---|---|
| Evidence ledger (27 receipts) | ✅ | `ledger.db` |
| Feature Store (17 receipts + 17 Active specs) | ✅ | `feature_store.db` |
| Label Store (8 receipts + 4 Active specs) | ✅ | `label_store.db` |
| Dataset Builder store | ✅ | `dataset_builder.db` |
| Backup manifests | ✅ | shipped as `{manifest}` alongside each artifact |
| Trading + forward evidence | ✅ | `btc_trading.db`, `evidence_forward.db`, `forward_evidence.db` |
| **Corpus (548 MB, 274 partitions + their `_manifest.json`)** | ❌ **NOT COVERED** | no backup path exists at all |

**The corpus gap is real and out of BK3's current mechanism.** `backup.py` backs
up SQLite databases via `VACUUM INTO`; a Parquet tree is a different shape. See
§6.

## 3. The three blockers — all owner actions

**None of these can be performed or verified from an engineering session.**

### B1 — `age` is not installed

Verified: not on `PATH` locally, not on production.

```bash
sudo apt-get update && sudo apt-get install -y age
age --version
```

### B2 — no keypair exists, and custody is undecided 🔴

`BACKUP_AGE_RECIPIENT` is absent from both `.env` files.

```bash
age-keygen -o /root/btcbot-backup-identity.txt      # PRIVATE — never on the bot host long-term
grep "public key" /root/btcbot-backup-identity.txt  # this is the recipient
```

**This is the decision that must not be rushed.** The identity (private key) is
the only thing that can ever decrypt an off-host copy. If it is lost after
encryption begins, **every off-host artifact becomes unrecoverable**, and the
loss is silent — backups keep succeeding.

Custody must be **off-machine and off-account** relative to the bot host.
Storing the identity on the EC2 instance it protects defeats the purpose.

### B3 — no off-host target exists

`BACKUP_OFFHOST_CMD` is absent. It needs a destination and credentials —
neither exists today. Any of these satisfies the interface:

```bash
BACKUP_OFFHOST_CMD="rsync -az {file} {manifest} user@backup-host:/srv/btcbot/"
BACKUP_OFFHOST_CMD="aws s3 cp {file} s3://bucket/btcbot/ && aws s3 cp {manifest} s3://bucket/btcbot/"
BACKUP_OFFHOST_CMD="rclone copy {file} remote:btcbot/ && rclone copy {manifest} remote:btcbot/"
```

Provisioning a target has cost and credential implications. **Owner decision.**

## 4. Activation sequence — once B1–B3 are done

1. Add `BACKUP_AGE_RECIPIENT=age1…` to `/opt/btcbot/.env`.
2. Add `BACKUP_OFFHOST_CMD=…` to `/opt/btcbot/.env`.
3. **Dry-run first**, one store, manually:
   ```bash
   sudo -u btcbot /opt/btcbot/venv/bin/python scripts/backup.py --ledger --encrypt
   ```
   Expect: `[backup] encrypted: ledger_<ts>.db.age`, then the off-host command,
   then `.offhost-status.json` showing `status: ok`.
4. **Prove decryptability before trusting it** (§5). Do not proceed until a
   round-trip has succeeded.
5. Only then change the unit:
   ```
   ExecStart=/opt/btcbot/venv/bin/python scripts/backup.py --all --encrypt
   ```
   `systemctl daemon-reload && systemctl restart btcbot-backup.timer`
6. Confirm `healthcheck.py` reports `offhost_backup` transitioning from INFO
   (not configured) to OK.

## 5. Mandatory decrypt drill — the step that must not be skipped

An encrypted backup nobody has decrypted is a **belief**, not a control.

```bash
python scripts/offhost_backup.py verify  /srv/backups/ledger_<ts>.db.age
python scripts/offhost_backup.py decrypt /srv/backups/ledger_<ts>.db.age \
       --identity /path/to/identity.txt --out /tmp/ledger_restored.db
sqlite3 /tmp/ledger_restored.db "PRAGMA integrity_check;"
python -c "import sys; sys.path.insert(0,'research/evidence_library'); \
import receipt_ledger as r; print(r.verify_chain('/tmp/ledger_restored.db'))"
```

**Pass condition:** `integrity_check: ok` **and** `verify_chain ok=True` with the
expected receipt count. The chain check is the one that matters — bytes can
survive while the hash chain does not.

## 6. Recommended replication tiers

| Tier | Assets | Cadence | Rationale |
|---|---|---|---|
| **T1** | `ledger.db`, `feature_store.db`, `label_store.db`, `dataset_builder.db` | **daily, encrypted** | ~550 KB total. Hash-chained and content-addressed: a receipt cannot be re-derived, only lost. **Cheapest and highest-value** |
| **T2** | corpus `research/data/raw` (548 MB) | weekly | Re-derivable in principle (WBS 4.0 proved it) but re-derivation **loses the preserved original `ingest_ts` values**. Needs a mechanism `backup.py` does not have — a file-tree sync, not `VACUUM INTO` |
| **T3** | `btc_trading.db`, `evidence_forward.db` | daily local (already), off-host optional | Loss costs collection history, not governance |

**Start with T1.** It is under half a megabyte per day and covers 52 of 52
governance receipts.

## 7. Post-activation monitoring

`check_offhost_freshness()` already implements the severity model — no work
needed:

| Condition | Severity |
|---|---|
| Not configured | INFO (never alerts) |
| Success < 48h | OK |
| ≥ 48h stale, or latest attempt failed | WARNING |
| ≥ 7 days stale, or configured but **never** succeeded | **CRITICAL** |

## 8. Rollback

Remove `--encrypt` from the unit, or unset `BACKUP_OFFHOST_CMD`. Local
plaintext backups are unaffected either way — the off-host leg is deliberately
non-fatal, so a broken target never breaks the daily backup.
