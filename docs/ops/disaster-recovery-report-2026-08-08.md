# Disaster Recovery Report — BK2 Drill, 2026-08-08

| Field | Value |
|---|---|
| **Obligation** | ADR-0001 §12 **BK2** — quarterly restore drill. Never performed in full before this date |
| **Scope** | All six backed-up databases, end to end: backup → manifest → SHA verify → restore → integrity → chain verify |
| **Executed** | 2026-08-08, canonical host, commit `82ce7c0` (engineering frozen) |
| **Restore target** | Scratch directory only. No live database was replaced |
| **Result** | ✅ **PASS — 6 / 6 stores recovered**, with two findings (§4) |

---

## 1. Method

Every store was backed up with the real `scripts/backup.py`, verified against its
own manifest, restored with the real `scripts/restore.py` into a scratch path,
then checked for row equality, `PRAGMA integrity_check`, and — for the three
governance chains — `receipt_ledger.verify_chain()` **on the restored file**.

Chain verification at the destination is the check that matters for a
governance store: `integrity_check` proves SQLite consistency and says nothing
about whether the hash chain still links.

## 2. Results

| Store | Rows | Backup | Restore | Manifest SHA | Integrity | Rows match | Chain (restored) |
|---|---|---|---|---|---|---|---|
| `ledger` | 252 | 0.07s | 0.01s | ✅ | ok | ✅ | ✅ `ok=True, n=27` |
| `feature_store` | 51 | 0.05s | 0.01s | ✅ | ok | ✅ | ✅ `ok=True, n=17` |
| `label_store` | 166 | 0.06s | 0.01s | ✅ | ok | ✅ | ✅ `ok=True, n=8` |
| `dataset_builder` | 21 | 0.05s | 0.01s | ✅ | ok | ✅ | n/a — no chain by design |
| `btc_trading` | 40,320 | 0.16s | 0.22s | ✅ | ok | ✅ | n/a |
| `fwd_hg` | 0 | 0.04s | 0.01s | ✅ | ok | ✅ | n/a |

**Recovery timing:** the entire six-store cycle completed in **under one
second** of compute. Wall-clock recovery is dominated by operator decisions and
service stop/start, not by the data path. The largest store (40,320 rows)
restored in 0.22s.

**Rollback:** `restore.py` preserves the displaced file as `<name>.pre-restore`
before swapping. Observed on every run. A failed restore is reversible by moving
that file back.

## 3. WAL behaviour — `TD-P1-002` tested directly

The 2026-07-19 drill was inconclusive because the database happened to have no
`-wal` file. **Every store in this drill was also WAL-clean**, so the scenario
was constructed explicitly rather than waited for:

1. Backup taken at 10 rows.
2. Destination advanced to 25 rows, left **uncheckpointed in `-wal`** (4,152 bytes, `-shm` present).
3. A second connection held an **open uncommitted transaction** across the swap.
4. `restore.py` executed.

**Result: CLEAN.** Post-restore `rows=10`, **zero** stale rows, `integrity=ok`,
and both sidecars gone.

**What protected us was SQLite, not `restore.py`.** A WAL file carries a salt
matching its database header; after the swap the stale WAL no longer matched
and was rejected rather than replayed.

**Therefore `TD-P1-002` is downgraded, not closed.** `restore.py` still performs
no explicit checkpoint and does not remove sidecars itself. The feared
contamination did not reproduce in the scenario most likely to cause it, but the
protection is incidental. **Keep the interim runbook mitigation** (checkpoint +
remove `-wal`/`-shm` before any manual `cp`).

## 4. Findings

### F1 — `backup.py` mutates the source database 🟡

`scripts/backup.py:277` executes `PRAGMA journal_mode=WAL` on the **live**
database before `VACUUM INTO`. Five of six stores were already WAL, so this was
a no-op. **`label_store.db` was in `delete` mode and was converted**, rewriting
its header — its SHA-256 changed as a direct result of taking a backup.

- **Content impact: none.** Row counts identical, `integrity_check ok`, chain
  still `ok=True, n=8`, all verified after the fact.
- **Correction to this drill's own framing:** the run was described as
  read-only against live stores. For `label_store` that was **false**. Reported
  rather than quietly restated.
- **Not reverted.** WAL is the mode every other governed store uses, and the
  next `--all` run would apply it again. Reverting would fight the tool.
- **Recommendation:** document the behaviour (done, here and in the runbook).
  Suppressing the pragma would remove the concurrency property that makes
  `VACUUM INTO` safe on a running bot, which is a worse trade.

### F2 — an empty database produced a healthy backup signal (`AF-4`) 🟠

Proven and **fixed** during this work — see the Operations Verification Report.
A fresh backup of a zero-table database reported `OK`. It now reports `INFO`
with a `0 rows` message.

## 5. What this drill does **not** establish

- **Off-host recovery.** No ciphertext was decrypted because **BK3 is not
  active**: `age` is not installed on either host, and neither
  `BACKUP_AGE_RECIPIENT` nor `BACKUP_OFFHOST_CMD` is set. A drill of local
  backups is not a disaster-recovery drill for the loss of the host.
- **Production recovery.** Executed on the canonical host only. Production runs
  18 commits behind and holds a 26-receipt ledger with empty
  Feature/Label stores.
- **Corpus recovery.** The 548 MB corpus is not backed up by any path.

## 6. Verdict

**BK2 is satisfied for on-host recovery of all six backed-up databases**, with
recovery proven by row equality, integrity, and — for the three chains —
verification of the hash chain on the restored artifact.

**BK2 is not satisfied for off-host recovery**, because no off-host copy exists.
That is BK3, and it remains blocked on owner action.
