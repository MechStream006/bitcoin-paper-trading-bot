# Operations Verification Report — 2026-08-08

| Field | Value |
|---|---|
| **Scope** | Evidence for every operational change and check made during Phase 4 operational readiness |
| **Commit** | engineering frozen at `82ce7c0`; this work touches `scripts/healthcheck.py` + tests + `docs/ops/` only |
| **Rule applied** | Every claim below was re-derived from source or reproduced empirically. Prior reports were treated as claims |

---

## 1. `AF-4` — investigated, proven TRUE, fixed

### 1.1 Proof from source

`check_backup_freshness()` (`scripts/healthcheck.py`) selected the newest
`{label}_*.db` by **mtime** and returned OK/WARNING on **age alone**. It read no
manifest, no row counts, no file size. Nothing in the function could distinguish
a healthy backup from a backup of an empty database.

### 1.2 Proof by reproduction

A zero-table SQLite file was backed up with the real `backup.py` and checked
with the real healthcheck:

```
backup of a 0-table DB: rc=0  files=1  row_counts={}
healthcheck verdict   : OK  backup OK: feature_store_<ts>.db (0.0h ago)
```

**A store containing nothing reported a healthy, fresh backup.** On production
this is not hypothetical: `feature_store.db` and `label_store.db` are zero-table
placeholder files there.

### 1.3 Fix

`check_backup_freshness()` now reads the total from the sibling manifest's
`row_counts` — evidence `backup.py` already writes — and returns **INFO** when
it is zero.

**INFO, not WARNING**, deliberately. An empty optional store is a normal state
on a host where its research process never ran. INFO ranks below WARNING, so it
can never escalate `overall` or send Telegram — the same discipline `PROD-2`
applied to absent optional databases. Making this WARNING would put two
permanent warnings on production and erode trust in the backup signal, which is
the exact failure `PROD-1` was raised to fix.

### 1.4 Behaviour matrix — reproduced

| Case | Severity | `rows` |
|---|---|---|
| 0 tables | **INFO** — "backup fresh but EMPTY" | 0 |
| Tables present, 0 rows | **INFO** | 0 |
| Populated (34 rows) | **OK** — "34 rows" | 34 |
| Manifest deleted | **OK** | `None` |
| Manifest corrupt | **OK** | `None` |
| Stale **and** empty | **WARNING** (staleness outranks) | — |

The `None` cases are the load-bearing ones: **absence of evidence is not
evidence of absence.** An unreadable manifest must never be read as "empty", or
deleting a manifest would make a healthy backup look empty.

### 1.5 Tests

**8 added** to `tests/test_healthcheck.py`, covering every row above plus:
INFO cannot escalate `overall` (rank assertion), and the fix holds for **all**
labels (`ledger`, `label_store`, `dataset_builder`, `btc_trading`), not just the
one that motivated it.

### 1.6 Production behaviour after deployment

Predicted and stated so it is not a surprise: production's `feature_store_backup`
and `label_store_backup` will report **INFO … 0 rows** instead of OK once this
deploys. That is the fix working — those stores are genuinely empty there.
`overall` is unaffected; no alert fires.

## 2. BK2 drill — executed

Full results: [Disaster Recovery Report](disaster-recovery-report-2026-08-08.md).

**6/6 stores** backed up, SHA-verified, restored, integrity-checked; the three
governance chains verified **on the restored artifact** (27 / 17 / 8, all
`ok=True`). Whole cycle under one second of compute.

`TD-P1-002` tested by constructing the scenario (uncheckpointed 4,152-byte WAL +
open transaction across the swap): **clean** — 10 rows, zero stale rows,
integrity ok, sidecars gone. Protection came from SQLite's WAL salt validation,
not from `restore.py`, so the debt is **downgraded, not closed**.

## 3. Correction to this session's own claim

The BK2 drill was described as read-only against live stores. **For
`label_store.db` that was false.** `backup.py:277` executes
`PRAGMA journal_mode=WAL` on the source; that store was in `delete` mode and was
converted, changing its SHA-256.

Verified after the fact: content unaffected — `integrity_check ok`, chain still
`ok=True, n=8`, all 166 rows present. Not reverted: WAL is the mode every other
governed store uses and the next `--all` run would reapply it.

## 4. Governance stores — state after all operational work

| Store | Journal | Integrity | Chain |
|---|---|---|---|
| `ledger.db` | wal | ok | `ok=True, n=27` |
| `feature_store.db` | wal | ok | `ok=True, n=17` |
| `label_store.db` | wal | ok | `ok=True, n=8` |
| `dataset_builder.db` | wal | ok | n/a by design |

## 5. Regression

`btc_bot`: **3297 passed, 19 skipped** (3290 baseline + 8 new AF-4 tests, minus
one known-brittle guard — see §6).

## 6. Known brittle test, not fixed

`tests/test_sealed_regions.py::test_rf2_touches_no_file_outside_the_builder`
reads `git status` and fails on **any** uncommitted change outside
`research/dataset_builder/`, `tests/`, `docs/`. It therefore fails while
`scripts/healthcheck.py` is modified-but-uncommitted, and passes once committed.

Already logged as a standing false-positive generator. **Not fixed here** —
engineering is frozen and this is not an operational defect.

## 7. BK3

**No verification possible.** `age` is not installed on either host, and neither
`BACKUP_AGE_RECIPIENT` nor `BACKUP_OFFHOST_CMD` is set, so no encryption,
transfer, or decrypt round-trip could be exercised. The code paths that do not
require the binary are covered by the pre-existing 29 tests in
`tests/test_offhost_backup.py`.

Blockers and the activation sequence: [BK3 Deployment Guide](bk3-deployment-guide.md).
