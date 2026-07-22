# Restore Drill — 2026-07-19

Satisfies ADR-0001 §12 **BK2**: *"Backups are worthless until a restore drill
proves them... restore, then verify `receipt_ledger.verify_chain()` valid and
`report.py --check` clean."* This is the first such drill. It was run on the
local dev machine (no production host access this session) using real backup
artifacts and the unmodified production tooling (`scripts/backup.py`,
`scripts/restore.py`, `scripts/migrate.py`, `scripts/healthcheck.py`,
`receipt_ledger.py`, `report.py`). **No production data was modified** —
every restore target was a path under an isolated scratch directory outside
the repo and outside `data/`.

## Files changed

| File | Change |
|---|---|
| `docs/ops/restore-drill-2026-07-19.md` | **New** — this report |
| `docs/runbook.md` | New "Restore drill" section (procedure + cadence) |
| `data/ledger-backups/ledger_20260719_050802.db` + `.manifest.json` | **New** — first-ever ledger backup artifact, produced by the existing `--ledger` flag (not previously exercised locally). Left in place; it is a normal product of the existing tooling, not drill scaffolding. |

No source code changed. No test files added — this drill exercises existing,
already-tested tooling (`backup.py`/`restore.py` have their own suite in
`tests/test_backup.py`); the deliverable here is the operational proof and
its writeup, not new automation.

## Drill procedure

**Isolated location:** a scratch directory entirely outside the repo and
outside `data/` (session temp dir, `restore_drill/{trading,ledger}/`), so
every write the drill performs is physically incapable of touching
production paths.

**Artifacts used** (one per irreplaceability tier per BK1):

| DB | Artifact | Source |
|---|---|---|
| `btc_trading.db` (tier 3, re-collectable) | `btc_trading_20260610_173121.db` | **Pre-existing** real backup on disk (2026-06-10), untouched since creation |
| `ledger.db` (tier 1, irreplaceable) | `ledger_20260719_050802.db` | Generated fresh via `python scripts/backup.py --ledger` — no ledger backup artifact existed locally before this drill, so producing one was step 0 |

Step 0 confirmed the backup step itself is non-destructive: `ledger.db`'s
SHA-256 (`f4b745a8...`) was identical before and after running
`backup.py --ledger`, and the manifest's row counts (`governance_receipts=26,
runs=70, state_transitions=13, gate_results=142`) match the known real
ledger state exactly.

**Sequence, both DBs:**
1. Stage the backup artifact + manifest into the isolated directory.
2. Run `scripts/restore.py <artifact> --db-path <isolated>/restored_X.db --skip-service` — **unmodified script**, timed.
3. Independently re-verify schema, integrity, and (for the ledger) the governance chain — not just trusting restore.py's own internal checks.
4. Exercise real application code paths against the restored copies.

## Verification results

### 1. Restore duration (measured, wall-clock)

| DB | Artifact size | Restore time | Includes |
|---|---|---|---|
| `btc_trading.db` | 10,854,400 bytes (10.4 MB) | **0.428 s** | SHA-256 verify + integrity_check (backup) + copy + integrity_check (post-swap) |
| `ledger.db` | 172,032 bytes (168 KB) | **0.206 s** | same four steps |

At current scale, restore time is dominated by the two `PRAGMA
integrity_check` passes, not the copy — both are sub-second. Scaling note
below.

### 2. Schema verification (independent, beyond restore.py's own checks)

Restored `btc_trading.db`: `user_version = 0`, 9 tables (`candles,
funding_snapshots, instruments, oi_snapshots, option_quotes, orders,
positions, sqlite_sequence, trades`), pre-ENG-017 index set (no
`idx_option_quotes_ts`). **Finding:** this backup predates the v4/v5
migrations (item 2's `daily_certifier_log` and ENG-017's index are both
absent) — expected, since it was taken 2026-06-10, before either shipped.

To prove the upgrade path also works on a *restored* backup (not just a live
DB), `scripts/migrate.py --dry-run` then for real were run against the
restored copy: clean 0→5 migration, ending at `user_version = 5`,
`PRAGMA integrity_check` still `ok`, all expected tables present including
`collection_metrics`, `daily_certifier_log`, `instrument_map_log`.

Restored `ledger.db`: 5 tables (`gate_results, governance_receipts, runs,
sqlite_sequence, state_transitions`), row counts exactly matching the
manifest (`runs=70, gate_results=142, state_transitions=13,
governance_receipts=26`).

### 3. Integrity verification (independent)

Ran directly against both restored copies (not just relying on restore.py's
internal checks):

```
trading integrity_check: [('ok',)]  foreign_key_check violations: 0
ledger  integrity_check: [('ok',)]  foreign_key_check violations: 0
```

Row counts on both restored copies match their manifests exactly — no
truncation or corruption in the `VACUUM INTO` → restore round-trip.

### 4. Governance chain verification (BK2's explicit requirement)

Both checks BK2 names, run against the restored ledger copy:

```
receipt_ledger.verify_chain(restored_ledger.db)
  -> ChainVerificationResult(ok=True, n_receipts=26, errors=[])

report.run_check(registry.yaml, restored_ledger.db, thresholds.yaml)
  -> Registry check: OK
     Total families: 35  (candidate: 9, discovery: 22, disqualified: 4)
     exit code: 0
```

The hash chain is intact (tamper-evidence holds) and every governance
invariant (C1–C9, EG1/EG2, TG1–TG6) validates clean against the restored
copy, using the real `registry.yaml`/`thresholds.yaml` read-only. This is
the strongest possible proof the backup is usable: not just "the bytes are
valid SQLite" but "the entire governance decision record it contains
reconstructs and re-verifies correctly."

### 5. Application startup verification

**Storage layer** — `src.data.storage.get_connection()` opened the restored
`btc_trading.db` and ran the same query shape the runtime uses:

```
option_quotes: 39,680 rows, ts range 2026-04-28 .. 2026-05-21
instruments:   636 rows
```

**Health check** — `scripts/healthcheck.py --db-path <restored> --full-integrity`
ran the real, unmodified production health-check code against the restored
copy:

```
service:      INFO      systemd checks skipped (Windows)       [expected — not Linux]
disk:         OK        487.8 GB free
db_liveness:  CRITICAL  DB stale: last tick 5065522s ago        [expected — see below]
wal:          OK        no WAL file (clean checkpoint)
backup:       WARNING   backup directory does not exist        [expected — see below]
integrity:    OK        integrity_check OK
overall: CRITICAL (exit 2)
```

The two non-OK results are **correct, not failures of the drill**:
`db_liveness` is CRITICAL because this backup's last tick is 2026-05-21 —
~59 days stale relative to today, which is exactly what a staleness check
restored from an old backup *should* report. `backup` is WARNING only
because the isolated scratch directory has no `backups/` sibling directory
(there is nothing to be fresh relative to in an isolated drill location).
Both demonstrate the checks are reading the restored file correctly and
reacting to its actual content, which is the point of this verification
step.

**Scope note:** full `src/main.py` boot (network WS/REST connections to
Delta, scheduler start) was deliberately **not** invoked. That code path
performs live outbound network I/O unrelated to database restoration, and
running it is a live-trading-adjacent action out of scope for a backup
drill. "Application startup against the restored copy" is satisfied by
exercising the actual DB-facing code the runtime depends on at boot
(`storage.get_connection()`, and the independent health-check dead-man that
`src/api/services.py`'s `runtime_health` also mirrors) — not by starting the
full autonomous process.

### 6. Original data unmodified

Confirmed by hash before/after the entire drill:

```
ledger.db      sha256 f4b745a8...  (unchanged throughout)
btc_trading.db sha256 483c34a2...  (only ever read, never targeted by a restore)
```

## Rollback

Nothing to roll back in production — no production path was touched. To
remove the drill's local artifacts:

```bash
rm -rf <scratch>/restore_drill
# optional — the ledger backup this drill produced is a legitimate first
# backup, not scaffolding; keep it unless you want to re-generate on schedule
rm data/ledger-backups/ledger_20260719_050802.db*
```

## Operational recommendations

1. **Confirm `--all` (including `--ledger`) is actually running on the
   production host, not just committed in the repo.** `deploy/btcbot-backup.service`
   correctly runs `backup.py --all`, which covers `ledger.db` — the repo side
   is right. But before this drill, **no ledger backup had ever been taken
   locally** (`data/ledger-backups/` didn't exist), which means either the
   unit has never fired here or the ledger backup path has simply never been
   exercised in this dev environment. [VERIFY ON HOST]:
   `ls -lh /opt/btcbot/data/ledger-backups/` — confirm files exist and are
   recent (daily). BK1 calls `ledger.db` the one truly irreplaceable asset;
   this is worth a direct on-host confirmation, not an assumption from the
   unit file alone.
2. **BK3 (off-host backup) is still the open gap** noted in the ADR itself
   (§ "not yet provisioned") — this drill re-confirms it's unaddressed;
   Item 5 in the current queue is scoped to close it.
3. **Repeat quarterly on the production host**, per BK2's own cadence,
   against a *real* production backup artifact and pointed at a scratch path
   on that host (e.g. `/tmp/restore-drill/`) — this local drill proves the
   procedure and tooling work, but only a host-side run proves the
   production artifacts themselves restore cleanly.
4. **A backup older than the current migration level is expected, not a
   defect** — this drill's `btc_trading.db` artifact needed `migrate.py` to
   reach current schema, which is exactly what a real restore-after-loss
   would also require. Fold `python scripts/migrate.py --dry-run` (check
   only) as a standard post-restore step in `docs/runbook.md`'s restore
   procedure, not just in this drill.
