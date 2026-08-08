# Restore Runbook

| Field | Value |
|---|---|
| **Scope** | Restoring any of the seven governed/operational databases from a local backup |
| **Validated** | 2026-08-08 BK2 drill — 6/6 stores restored, incl. chain verification on the restored artifact |
| **Companion** | [Disaster Recovery Report](disaster-recovery-report-2026-08-08.md) · [Recovery Checklist](recovery-checklist.md) · [BK3 Guide](bk3-deployment-guide.md) |

---

## 1. `restore.py` is store-agnostic

One tool restores every store. It takes a backup file and a destination:

```bash
python scripts/restore.py <backup_file.db> --db-path <live_target.db> [--service-name NAME] [--skip-service]
```

It performs, in order: **SHA-256 verification against the manifest** → backup
`integrity_check` → service stop → preserve current file as `.pre-restore` →
swap → post-swap `integrity_check` → service start.

A SHA mismatch **aborts before the swap**. Verified by test.

## 2. Store map

| Store | Backup directory | Live path | Service |
|---|---|---|---|
| Trading | `data/backups/` | `data/btc_trading.db` | `btcbot` |
| Forward evidence (H6) | `data/evidence-backups/` | `research/data/forward_evidence.db` | — |
| Forward evidence (H5/G1/G3) | `data/evidence-fwd-backups/` | `research/data/evidence_forward.db` | — |
| **Governance ledger** | `data/ledger-backups/` | `research/evidence_library/ledger.db` | `btcbot-api` |
| **Label Store** | `data/label-store-backups/` | `research/label_store/label_store.db` | — |
| **Dataset Builder** | `data/dataset-builder-backups/` | `research/dataset_builder/dataset_builder.db` | — |
| **Feature Store** | `data/feature-store-backups/` | `research/feature_store/feature_store.db` | — |

## 3. Procedure

### 3.1 Before touching anything

```bash
ls -la <backup_dir>/                       # pick the artifact
cat <backup_dir>/<name>.manifest.json      # confirm ts, sha256, row_counts
```

**Read `row_counts` first.** If it is `{}` or all zeros, that backup is of an
empty database and restoring it will destroy live data. The healthcheck now
flags this as `INFO … 0 rows` (AF-4), but the manifest is the authority.

### 3.2 WAL preparation — required for governance stores

Every governed store runs in WAL mode. The BK2 drill found a stale WAL is
**rejected by SQLite** rather than replayed, so contamination did not reproduce
— but `restore.py` performs no checkpoint of its own (`TD-P1-002`). Do this
anyway; it costs nothing and removes the ambiguity:

```bash
sqlite3 <live_target.db> "PRAGMA wal_checkpoint(TRUNCATE);"
rm -f <live_target.db>-wal <live_target.db>-shm
```

### 3.3 Restore

```bash
sudo -u btcbot /opt/btcbot/venv/bin/python scripts/restore.py \
    data/ledger-backups/ledger_<ts>.db \
    --db-path research/evidence_library/ledger.db \
    --service-name btcbot-api
```

Use `--skip-service` for stores no service holds open (Feature, Label, Dataset
Builder, forward evidence).

### 3.4 Verify — governance stores need the chain check

```bash
sqlite3 <live_target.db> "PRAGMA integrity_check;"          # expect: ok

python - <<'EOF'
import sys; sys.path.insert(0, "research/evidence_library")
import receipt_ledger as rl
print(rl.verify_chain("research/evidence_library/ledger.db"))
EOF
```

**`integrity_check` is not sufficient for a governance store.** It proves SQLite
consistency and says nothing about whether the hash chain still links. Expect
`ok=True` with the receipt count you intended to recover:

| Store | Expected receipts (2026-08-08) |
|---|---|
| `ledger.db` | 27 |
| `feature_store.db` | 17 |
| `label_store.db` | 8 |

For `dataset_builder.db` there is no chain — by design (`TD-4.5-1`); its seal
receipts live in `ledger.db`. Verify it with:

```bash
python -c "import sys;sys.path.insert(0,'research');\
from dataset_builder import citation_index as ci;print(ci.verify_bidirectional_consistency('research/dataset_builder/dataset_builder.db'))"
```

### 3.5 Rollback

`restore.py` leaves the displaced file at `<name>.pre-restore`:

```bash
sudo -u btcbot mv research/evidence_library/ledger.db.pre-restore \
                research/evidence_library/ledger.db
sudo systemctl restart btcbot-api
```

## 4. Restoring from an off-host encrypted copy

**Not available until BK3 is activated** — see the BK3 guide. Once it is:

```bash
python scripts/offhost_backup.py verify  <artifact>.age
python scripts/offhost_backup.py decrypt <artifact>.age --identity <identity> --out /tmp/restored.db
# then §3.3 onward, using /tmp/restored.db as the backup file
```

## 5. Known behaviour

- **Taking a backup writes to the live database.** `backup.py:277` sets
  `PRAGMA journal_mode=WAL` on the source before `VACUUM INTO`. On a store
  already in WAL this is a no-op; on one in `delete` mode it rewrites the header
  and changes the file's SHA-256. Content is unaffected. Observed on
  `label_store.db` during the BK2 drill.
- **The off-host leg is non-fatal by design.** A broken target never fails the
  local backup; it is recorded in `.offhost-status.json` and surfaced by the
  healthcheck instead.
- **Recovery is fast.** All six stores backed up and restored in under one
  second of compute in the drill; the largest (40,320 rows) restored in 0.22s.
  Wall-clock time is dominated by decisions and service restarts.
