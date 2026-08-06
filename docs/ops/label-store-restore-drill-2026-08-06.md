# Restore Drill — Label Store — 2026-08-06

Satisfies WBS 3.10's own exit criterion ("a restore drill is performed and
its transcript committed") and applies the same BK2 discipline ADR-0001 §12
already established for `ledger.db`/`btc_trading.db`
(`docs/ops/restore-drill-2026-07-19.md`) to the Label Store's own database
for the first time. Run on the local dev machine (no production host access
this session) using real local artifacts and the unmodified production
tooling (`scripts/backup.py`, `scripts/restore.py`, `scripts/healthcheck.py`,
`research/label_store/register_conventions.py`,
`research/label_store/value_store.py`). **No production data was modified**
— every restore target was a path under an isolated scratch directory
outside the repo and outside `data/`.

## Why this drill needed synthetic forward-tier data, and why that is not WBS 3.4

Unlike the July 19 drill (which backed up real, already-accumulated
`ledger.db`/`btc_trading.db` history), no real forward-tier data exists
anywhere yet — WBS 3.4 (wrapping the 148 real H6 forward labels) has not
been implemented. An empty `label_value_forward` table would make this
drill trivially pass without proving anything about the property it exists
to prove: that the table ADR-I011 §7 calls irreplaceable actually survives
a real backup → restore round trip unchanged.

Three rows were declared via `value_store.py`'s own, already-tested,
unmodified API (`declare_forward_label` / `observe_forward_label` /
`mature_forward_label` / `void_forward_label`) against the real local
`research/label_store/label_store.db`, exercising three of the six
maturity states (`Matured`, `Void`, `Declared`). Every row is clearly
labeled synthetic in its own content (`subject_id` prefixed
`drill-synthetic-`, `declared_by="restore-drill-2026-08-06"`, an
`outcome_json` note reading `"SYNTHETIC DRILL DATA, not real evidence"`).
This is **not** WBS 3.4: the schema carries no `attestation` or `standing`
column (those are WBS 3.4/3.5 concepts, not yet built), no H6 data was
read, and the rows were deleted after the drill — see "Cleanup" below. This
is the same category of act as WBS 3.1's hand-written test Specifications:
exercising a real, already-shipped mechanism with representative data to
prove it works, not beginning the next milestone.

## Files changed

| File | Change |
|---|---|
| `docs/ops/label-store-restore-drill-2026-08-06.md` | **New** — this report |
| `research/label_store/label_store.db` | Real, local, gitignored artifact — first real registration via `register_conventions.py --commit` at its actual default path (previously only exercised against throwaway scratch paths at WBS 3.3). Left in place, holding only the legitimate WBS 3.3 registry content (2 Definitions, 4 Specifications) — the synthetic drill rows were created, backed up, verified, and then removed; see "Cleanup." |
| `data/label-store-backups/label_store_20260805_190944.db` + `.manifest.json` | **New** — first-ever `label_store.db` backup artifact, produced by the new `--label-store` flag, of the clean post-cleanup state (2/4/0/0 row counts). Left in place, a normal product of the existing tooling, not drill scaffolding. |

No source code changed to produce this drill — it exercises the tooling
shipped earlier in this milestone (`tests/test_backup.py`,
`tests/test_healthcheck.py` already cover `--label-store`/
`check_backup_freshness(label="label_store")` directly); the deliverable
here is the operational proof and its writeup.

## Drill procedure

**Isolated restore location:** a scratch directory entirely outside the
repo and outside `data/` (session temp dir,
`restore_drill_label_store/`), so the restore step is physically incapable
of touching production or repo paths — mirroring the July 19 drill exactly.
The backup step itself operated on the real local
`research/label_store/label_store.db`, which is safe (VACUUM INTO is
read-only against its source).

**Sequence:**
1. Create the real registry via `python research/label_store/register_conventions.py --commit` (first real run against its default path — 2 Definitions, 4 Specifications, matching WBS 3.3's own dry-run/`--commit` idempotency proof).
2. Declare 3 synthetic forward-tier rows via `value_store.py`'s real API (Matured, Void, Declared states).
3. Snapshot pre-backup state: source SHA-256, and the exact JSON content of every `label_value_forward` row.
4. Run `python scripts/backup.py --label-store` — produces a real artifact + manifest under `data/label-store-backups/`.
5. Confirm row-level content identical before/after the backup step (source file's SHA-256 changed — see "The one honest surprise" below — but every row's content did not).
6. Run `python scripts/restore.py <artifact> --db-path <scratch>/restored_label_store.db --skip-service` — **unmodified script**.
7. Independently re-verify schema, integrity, foreign keys, and row-level content — not just trusting `restore.py`'s own internal checks.
8. Demonstrate the registry's independent regenerability by re-running `register_conventions.py --commit` against a second, fresh throwaway path and comparing hashes.
9. Verify `check_backup_freshness(label="label_store")` directly.
10. Verify `--label-store` did not write into any other backup directory.
11. Clean up the synthetic rows and the backup artifact that captured them; leave one final, legitimate backup of the clean registry-only state.

## Verification results

### 1. The one honest surprise: the source file's own hash changes on first backup

Unlike the July 19 drill (`ledger.db`'s SHA-256 was identical before and
after `--ledger`, because `ledger.db` was already in WAL mode), the very
first `scripts/backup.py --label-store` run against `label_store.db`
changed the **source file's own SHA-256** —
`b8f463046d4d0f427b164dc638c7579fc235339138de030742f0be4d03135116` before,
`5056678e99b9768234f36bd1fcf38d2d622c44f26874367b455679564a1ed8be` after.

Cause: `run_backup()` calls `conn.execute("PRAGMA journal_mode=WAL")` on
the **source** connection before `VACUUM INTO` (`scripts/backup.py:235`,
already documented as a cross-cutting effect in `CLAUDE.md` §19
`TD-P1-002`). `label_store.db` was created fresh via plain
`sqlite3.connect()` (no explicit journal mode), so it started in SQLite's
default rollback-journal mode — this backup run was the *first* time
anything set it to WAL, a one-time, expected, documented side effect, not
a defect. **Row-level content was verified byte-identical** before and
after (diffed the full JSON dump of every `label_value_forward` row —
zero differences); only the file's own journal-mode header changed.

### 2. Restore duration and `restore.py`'s own checks (measured)

```
[restore] verifying SHA-256 against manifest...
[restore] SHA-256 OK (a159dfc8233e7352...)
[restore] row_counts : {'label_definitions': 2, 'label_specifications': 4,
                         'label_value_forward': 3, 'label_value_research': 0}
[restore] integrity_check on backup file...   OK
[restore] post-swap integrity_check...        OK
```

Sub-second, dominated by the two `PRAGMA integrity_check` passes on a
48 KB file — consistent with the July 19 drill's scaling note.

### 3. Independent verification (beyond `restore.py`'s own checks)

```
integrity_check:    [('ok',)]
foreign_key_check:  0 violations
row counts:         label_definitions=2, label_specifications=4,
                     label_value_forward=3, label_value_research=0
```

Matches the manifest exactly.

### 4. Table-level recovery verification — the point of this drill

Every `label_value_forward` row's full content (all 15 columns) was
compared, field by field, between the pre-backup snapshot and the restored
copy:

- **`drill-synthetic-subject-1` (Matured)**: `outcome_json`,
  `input_revision_set_json`, and `matured_at` — the three fields
  `trg_label_value_forward_guard` (WBS 3.2) makes fill-once — are
  **byte-identical**.
- **`drill-synthetic-subject-2` (Void)**: `void_at`/`void_reason` intact,
  `maturity_state` still `Void` — the permanent-record property (LG4)
  survived the round trip.
- **`drill-synthetic-subject-3` (Declared)**: unchanged placeholder state.

**Zero differences found.** This is the concrete demonstration of what
Rohith's LS-DEC-001 approval named directly: *"Recovery requirements
differ by table, not by database."* The backup/restore **mechanism**
operated on the whole file, exactly once, exactly as it does for
`ledger.db` — but what it was asked to prove is table-specific: the
registry's recoverability claim was checked by demonstrating a **second,
independent** recovery path (below); the forward tier's was checked by
exact-content survival through the one path it actually has.

### 5. Registry's independent regenerability, demonstrated concretely

Re-ran `register_conventions.py --commit` against a **second**, fresh
throwaway database (not the one restored from backup):

```
real registered spec_hashes:        [0db88b..., 57ce01..., 89869c..., e785df...]
freshly regenerated spec_hashes:    [0db88b..., 57ce01..., 89869c..., e785df...]
```

**Identical.** This is the property `label_definitions`/
`label_specifications` have that `label_value_forward` structurally
cannot (ADR-I011 §1.1, §7: a forward label is a record of an observation,
not a computation — it cannot be recomputed into existence). The registry
has *two* independent recovery paths (backup, and re-running the
deterministic registration script); the forward tier has exactly one
(backup) — which is precisely why WBS 3.10 exists ahead of WBS 3.4, per
the roadmap's own `3.10 → 3.4` sprint ordering.

### 6. Freshness check, called directly

```python
check_backup_freshness(data/label-store-backups, label="label_store", check_name="label_store_backup")
-> CheckResult(name='label_store_backup', severity='OK',
               message='backup OK: label_store_20260805_190410.db (0.1h ago)')
```

### 7. Backup isolation confirmed

`data/backups/` (trading), `data/ledger-backups/`, and
`data/evidence-backups/` all show only their pre-existing files from
earlier sessions (Jun 9/10, Jul 19) — nothing new from this drill's
`--label-store` invocation. Only `data/label-store-backups/` gained a new
artifact. Matches the isolation `tests/test_backup.py`'s
`test_label_store_early_return_no_other_backups` already proves at the
unit level; this is the same property demonstrated against the real CLI.

### 8. Original data unaffected by the restore step

The restore step's target was the isolated scratch path throughout — the
real local `research/label_store/label_store.db` was never a restore
*destination* in this drill, only a backup *source*. Confirmed by hash: the
source file's SHA-256 immediately after the backup step
(`5056678e...4a1ed8be`) exactly matches its SHA-256 checked again after the
full restore-and-verify sequence completed — the restore step touched only
the scratch copy.

## Cleanup

The three synthetic forward-tier rows were **not** left in the real local
`label_store.db` — they exist nowhere except this transcript, which
records their exact content for reproducibility. Disposition:

```bash
# Removed: synthetic rows (by deleting and re-creating the DB from the
# real, legitimate WBS 3.3 registration — the only rows that belong there)
rm research/label_store/label_store.db{,-wal,-shm}
python research/label_store/register_conventions.py --commit   # 2 defs, 4 specs — identical hashes to before

# Removed: the backup artifact that captured the synthetic rows (drill
# scaffolding, not real evidence — unlike ledger's first backup at the
# July 19 drill, which captured real governance history and was kept)
rm data/label-store-backups/label_store_20260805_190410.db*
rm data/label-store-backups/.offhost-status.json

# Left in place: one final, legitimate backup of the clean, real registry
# state (mirrors the July 19 drill's choice to leave a real artifact behind)
python scripts/backup.py --label-store
# -> data/label-store-backups/label_store_20260805_190944.db (24 KB, 2/4/0/0)
```

Scratch restore directory removed entirely (`rm -rf`).

## Rollback

Nothing to roll back in production — no production path was touched at any
point. The one real, non-scratch artifact this drill leaves behind
(`research/label_store/label_store.db` and its backup) is exactly what WBS
3.3 was always going to produce the first time someone ran
`register_conventions.py --commit` for real — not drill-specific state.

## Operational recommendations

1. **Confirm `--all` (including `--label-store`) actually runs on the
   production host once WBS 3.4/host-side activation happens** — mirrors
   the July 19 drill's identical recommendation for `--ledger`. Repo side
   is correct: `deploy/btcbot-backup.service` runs `backup.py --all`,
   which now covers `label_store.db` automatically, no deploy-file change
   needed.
2. **Off-host replication remains explicitly out of this milestone's
   scope** (mirrors `feature_store_backup_strategy.md` §4's "not
   recommended at the same time as local backup" carve-out, and the
   roadmap's WBS 3.10 exit criterion, which names only local backup +
   freshness + drill). Re-evaluate once real WBS 3.4 forward-tier data
   exists and has held for long enough to matter — the same
   trigger-based reasoning `feature_store_backup_strategy.md` §3 already
   established for `feature_store.db`.
3. **`TD-P1-002` (WAL-aware restore support) is confirmed to also apply
   to `label_store.db`**, not just `ledger.db`/`btc_trading.db` — finding
   1 above is new, independent confirmation. Not fixed here (pre-existing,
   logged technical debt, out of this milestone's scope); worth citing
   `label_store.db` alongside the other two the next time that ticket is
   scoped.
4. **Repeat this drill after WBS 3.4** lands real H6-wrapped forward-tier
   data, against the *real* production-shaped content rather than
   synthetic drill rows — the mechanism is now proven; a second drill
   after 3.4 would prove the *real* data survives the same way, closing
   the gap this drill's synthetic substitute leaves open.
