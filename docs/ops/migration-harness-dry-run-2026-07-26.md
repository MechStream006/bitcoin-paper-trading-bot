# Migration Harness Dry Run — 2026-07-26

Program P1, Increment 4 (Local Pre-Validation Harness). Executed on the local
dev machine only, against a disposable copy of the real local `ledger.db`.
**No production host was touched or accessed. The real local `ledger.db` was
verified byte-for-byte unchanged before and after this run.**

Source: `tests/test_migration_harness.py` (`pytest tests/test_migration_harness.py -v -s`).

## 1. Pre-migration schema check (GV-SCHEMA)

Synthetic ledger built by copying the real local ledger and stripping it to
the historically-documented pre-migration shape: `run_kind` column removed
from `runs`; `governance_receipts` and `pairwise_r` tables dropped;
`current_run_per_lane`, `candidate_gate_status`, and `has_live_evidence`
views dropped (all three still actively defined by `_SCHEMA_SQL` today, so
their correct recreation by the migration sequence is verified below, not
assumed).

```
[ERROR]   GV-SCHEMA: required column 'runs.run_kind' is absent from the ledger
[ERROR]   GV-SCHEMA: required table 'governance_receipts' is absent from the ledger
[WARNING] GV-SCHEMA: required table 'pairwise_r' is absent from the ledger
```

`report.py --check` against the same synthetic ledger: **exit 1**, printing
the two named errors (`SCHEMA CHECK FAILED — 2 error(s)`), performing **zero
schema mutation** (verified by byte-identical `sqlite_master` before/after
this failing check itself).

## 2. Migration sequence (first run)

Executed in documented order, via each script's real CLI, against the
synthetic copy's `--db`/`--registry` only:

| Script | Result |
|---|---|
| `migrate_run_kind.py --commit` | exit 0 — 70 rows classified (discovery/confirmatory per family) |
| `migrate_bootstrap_evidence_admission.py --commit` | exit 0 — 13 admission/rejection receipts written |
| `migrate_bootstrap_transition_receipts.py --commit` | exit 0 — 13 transition receipts written |

Each script produced its own timestamped self-backup before writing, exactly
as it would in production — the harness performs no backup logic of its own.

## 3. Post-migration checks

- `GV-SCHEMA`: **zero findings.**
- `report.py --check`: **exit 0** — `Registry check: OK`, 35 total families
  (9 candidate / 22 discovery / 4 disqualified).
- `receipt_ledger.verify_chain()`: **ok=True, n_receipts=26.**
- `current_run_per_lane`, `candidate_gate_status`, `has_live_evidence` all
  recreated and queryable — confirmed, not assumed.

## 4. A real, previously-undocumented artifact discovered

A fourth view, `latest_run_per_family`, existed on the real ledger — a stale
leftover from before its deletion at PR-5 (`record.py` explicitly documents
this function/view as "DELETED, not deprecated"; no current code creates or
queries it). It blocked the `runs` table rebuild (SQLite requires dropping a
view before dropping/renaming a table it references) and was dropped as a
mechanical necessity. **Deliberately not recreated** — it is dead schema, not
part of the documented pre-migration drift, and out of scope to resurrect.
Confirmed absent after migration, as expected.

## 5. Idempotency (second run)

The full three-script sequence was re-run against the now-migrated copy:

```
migrate_run_kind.py:                        0 rows to classify
migrate_bootstrap_evidence_admission.py:    0 pinned runs need a bootstrap admission receipt
migrate_bootstrap_transition_receipts.py:   0 transitions need a bootstrap receipt
```

All three exit 0. **Zero new backup files created** (each script's own
`if not plan: return 0` guard fires before its backup step, exactly as
designed). Ledger content (full `sqlite_master` schema, row/receipt counts,
chain validity) is byte-identical before and after this second run.
`report.py --check` still passes cleanly afterward.

## 6. Real ledger protection

- Every migration-script invocation passed an explicit `--db`/`--registry`
  pointing at the tmp-path copy; an assertion before every invocation
  refused to proceed had any path resolved to the real ledger.
- The real local `ledger.db`'s SHA-256 was captured before this run and
  confirmed byte-for-byte identical after all 12 tests completed.

## Conclusion

The D-0-determined minimal migration set — `migrate_run_kind.py` →
`migrate_bootstrap_evidence_admission.py` → `migrate_bootstrap_transition_receipts.py`
— closes the historically-documented drift cleanly, verifiably, and
idempotently, using the scripts' real, unmodified CLI. This is local
evidence only; it does not itself authorize the production migration
(Increment 5, still gated on Rohith's explicit authorization).
