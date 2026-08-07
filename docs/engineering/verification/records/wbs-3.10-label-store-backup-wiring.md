# Milestone Verification Record

| Field | Value |
|---|---|
| **Milestone** | Forward-tier backup and durability wiring — `scripts/backup.py`, `scripts/healthcheck.py`, `src/api/services.py` |
| **WBS item(s)** | 3.10 |
| **Roadmap phase** | Phase 3 — Label Store (`phase_3_label_store_roadmap.md` v1.1.0) |
| **Standard version referenced** | 1.0.0 |
| **Date** | 2026-08-06 |
| **Implementer** | Claude Sonnet 5 |
| **Reviewer** | Rohith Suresh — signed 2026-08-07 (`LS-DEC-003`) |

**LS-DEC-001 applied, as approved.** Rohith's ruling (2026-08-06): the Label Store forward tier stays co-located with the registry and research tier in a single `label_store.db`, with the explicit instruction *"recovery requirements differ by table, not by database."* This milestone implements that literally: **one backup unit** (one `--label-store` flag, one `VACUUM INTO`, one manifest, one freshness check — mirroring `--ledger`'s exact shape), but the **restore drill's verification is deliberately table-scoped** — proving the forward tier's exact-content survival independently from the registry's independent regenerability, rather than treating "the file restored OK" as sufficient on its own. See the drill transcript, §4/§5.

**Scope discipline.** Corresponds to WBS 3.10 only, per its own exit criterion: local backup wiring (`VACUUM INTO` + SHA-256 manifest + rotation), freshness check (CLI + `runtime_health()`), and a restore drill with a committed transcript. Off-host replication is explicitly out of scope — matches `feature_store_backup_strategy.md` §4's own "not recommended at the same time as local backup" carve-out and the roadmap's exit criterion, which names only local backup/freshness/drill. **WBS 3.4 is explicitly not begun** — no H6 data, no `attestation`/`standing` columns (not part of the current schema), no `migrate_h6_forward_labels.py`.

---

## 1. Build Verification (BV)

- [x] **BV1** — `python -m py_compile scripts/backup.py scripts/healthcheck.py src/api/config.py src/api/services.py tests/test_api.py tests/test_backup.py tests/test_healthcheck.py` → `COMPILE_OK` in both the base env and `btc_bot`.
- [x] **BV2** — Every addition mirrors an existing, already-reviewed pattern exactly: the `--label-store` CLI flag and dispatch block mirror `--ledger`'s shape verbatim (`scripts/backup.py`); the freshness check mirrors `check_backup_freshness(label="ledger", ...)`'s existing generalized call (P1 Increment 1); the `ApiSettings` field addition mirrors `ledger_backup_dir`'s exact precedent, including its own documented consequence (two `tests/test_api.py` fixtures require the new field — Increment 1's own note anticipated this recurring).
- [x] **BV3** — No new dependency.
- [x] **BV4** — Verified under both the base env and `btc_bot` for every file that can run there (`scripts/backup.py`, `scripts/healthcheck.py`, `src/api/config.py`, `src/api/services.py` are all base-env-importable — no pandas dependency). `tests/test_api.py` cannot be executed locally in either environment (pre-existing, documented `fastapi` gap, `CLAUDE.md` §18 Increment 1/6) — edited correctly per BV1's compile check, matching Increment 1's own precedent for the same file.

## 2. Test Verification (TV)

- [x] **TV1** — 11 new tests: `tests/test_backup.py` (+6: `test_label_store_flag_backs_up_label_store_db`, `test_label_store_correct_label_in_filename`, `test_label_store_correct_backup_dir`, `test_label_store_early_return_no_other_backups`, `test_label_store_dry_run_no_files`, `test_label_store_row_counts_in_manifest`); `tests/test_healthcheck.py` (+5: `test_label_store_missing_dir_is_warning`, `test_label_store_no_matching_backups_is_warning`, `test_label_store_fresh_backup_is_ok`, `test_label_store_stale_backup_is_warning`, `test_runtime_health_includes_label_store_backup_check`). Two pre-existing `--all`-flag tests were renamed (`..._covers_all_four_dbs` → `..._covers_all_five_dbs`, `..._prints_four_lines` → `..._prints_five_lines`) and extended with a label_store assertion — a forced, direct consequence of `--all` now covering five databases, not a rename for its own sake; net test count unaffected by the rename itself (11 new node IDs, matching the observed +11 full-suite delta exactly).
- [x] **TV2** — House style confirmed; every new test mirrors its `ledger`-labeled sibling's exact structure.
- [x] **TV3** — Empirically verified **twice**, on the two genuinely new code paths this milestone adds: (a) removed the `--label-store` dispatch block from `scripts/backup.py`'s `main()` — 4 of 6 new backup tests failed (`AssertionError: assert None is not None`, `AttributeError: 'NoneType' object has no attribute 'parent'`), confirming they are not tautological; restored, re-confirmed 37/37 green. (b) removed the new `check_backup_freshness(label="label_store", ...)` call site from `src/api/services.py`'s `runtime_health()` — `test_runtime_health_includes_label_store_backup_check` failed (`'label_store_backup' not in [...]`); restored, re-confirmed 24/24 green.
- [x] **TV4** — No new refusal-path claim is made by this milestone (the underlying `run_backup()`/`check_backup_freshness()` refusal behaviors — missing DB, missing dir, no matching backups — are pre-existing and already tested; this milestone's tests confirm the *same* refusals fire correctly for the new `label_store` label, which TV1's list covers).
- [x] **TV5** — Property/invariant tests: **isolation** (`--label-store` alone creates no trading/ledger/fwd_hg backup — `test_label_store_early_return_no_other_backups`); **manifest completeness** (`test_label_store_row_counts_in_manifest` — the whole-file backup unit's manifest reflects both co-located tables, not just whichever happens to be non-empty, directly evidencing the LS-DEC-001 "one backup unit" property); **label isolation** (pre-existing `test_labels_do_not_cross_contaminate`, unmodified, still covers the general mechanism the new label reuses).

## 3. Regression Verification (REG)

- [x] **REG1** — `btc_bot` env, `python -m pytest tests/ -q --ignore=tests/test_api.py`:
  - Before (after WBS 3.3): **1805 passed, 19 skipped, 0 failed**
  - After: **1816 passed, 19 skipped, 0 failed**
  - Delta: **+11 passed**, exactly matching the new test count (TV1).
  - Base env (`tests/test_backup.py tests/test_healthcheck.py` together): **61/61 passed**.
  - `tests/test_restore.py` (untouched — no code change to `restore.py` this milestone): **21/21 passed**, confirming no incidental regression.
- [x] **REG2** — Skip count unchanged (19→19).
- [x] **REG3** — No known pre-existing flake recurred in any run performed for this milestone.
- [x] **REG4** — CI workflow unchanged.

## 4. Performance Verification (PERF)

**N/A** — `VACUUM INTO` on a small SQLite file (measured: 24–49 KB in this session's drill), same cost class as the existing `--ledger`/`--evidence`/`--fwd-hg` backups it mirrors.

## 5. Database Verification (DBV)

**Applicability: YES** — this milestone is the first time anything writes real, non-registry-script data into `label_store.db`'s forward tier (drill-only, cleaned up — see §7 below), and the first time `label_store.db` is backed up for real.

- [x] **DBV1** — Additive only. No schema change to `registry.py`/`value_store.py` (both untouched this milestone — verified: `git status` shows no diff to either file after the TV3 sabotage/revert cycles).
- [x] **DBV2** — `scripts/backup.py --label-store` mirrors the existing dry-run/`--commit`-equivalent gate exactly (`--dry-run` flag, pre-existing, generic over `db_label`).
- [x] **DBV3** — Verified directly in the real drill (not just reasoned about): ran the real backup step and the real restore step once each against real local data; separately confirmed `register_conventions.py --commit` (the registry's own idempotent script) run a third time in this session still produces the same 4 hashes as WBS 3.3's original run and WBS 3.4-adjacent re-runs.
- [x] **DBV4** — N/A — no governance ledger touched; `label_store.db` is independent of `ledger.db`.
- [x] **DBV5** — **Exercised, not N/A, for the first time this Phase 3 track**: the real local `label_store.db` was backed up before any further mutation, and `PRAGMA integrity_check`/`PRAGMA foreign_key_check` were run independently against the restored copy (both clean) — see the drill transcript §3.
- [x] **DBV6** — N/A — unchanged from the WBS 3.1/3.2/3.3 finding; `governance_verification.py`'s manifest covers `ledger.db` only.

## 6. Backward Compatibility (BC)

- [x] **BC1** — No existing function's name, parameter list, or return type changed. `run_backup()`, `check_backup_freshness()`, `runtime_health()` all keep identical signatures; new behavior is purely additive call sites and one new dataclass field.
- [x] **BC2** — No existing table/column/file renamed or removed.
- [x] **BC3** — No existing CLI flag's meaning changed. `--all` now covers one more database — an intentional, documented behavior extension (matches the precedent set when `--ledger`/`--fwd-hg` joined `--all`), not a meaning change to any existing flag.
- [x] **BC4** — `ApiSettings.label_store_backup_dir` is a new **required** field (mirrors `ledger_backup_dir`'s own precedent exactly — Increment 1 explicitly chose symmetric extension over a dataclass-only default). Not "additive with a safe default" in the strict sense, but the same, already-reviewed and already-accepted deviation.
- [x] **BC5** — `grep -rln "ApiSettings(" --include="*.py" .` finds exactly three files: `src/api/config.py` (the one production construction site) and the two test fixtures — all three updated. No other consumer exists anywhere in the repository.

## 7. Rollback Verification (RBV)

- [x] **RBV1** — Revert the four source-file diffs (`backup.py`, `healthcheck.py`, `config.py`, `services.py`) and the three test-file diffs; delete `data/label-store-backups/` (gitignored, not tracked). `label_store.db` itself (WBS 3.1–3.3's own artifact) is untouched by any rollback of this milestone.
- [x] **RBV2** — No production data touched — confirmed; every real write this milestone performed was to the local dev machine's own gitignored files, verified against `git status` at every stage.
- [x] **RBV3** — Exercised directly, not merely reasoned: TV3's two sabotage/revert cycles are themselves a live demonstration that reverting these specific additions cleanly returns prior behavior (tests pass identically before the addition existed and after it is restored).
- [x] **RBV4** — N/A — no governance record in scope; nothing here touches `governance_receipts`/`state_transitions`.
- **One named exception, per the roadmap's own §10 pattern**: the `trg_label_value_forward_guard` fill-once trigger (WBS 3.2) has no configuration-level disable — unaffected by this milestone, restated here only because this milestone's drill is the first time that guarantee was exercised against real backup/restore machinery, and it held (drill §4).

## 8. Technical Debt Assessment (TD)

| What | Why deferred | Closed by (WBS ID) | Blast radius if never closed |
|---|---|---|---|
| No off-host replication wiring for `label_store.db` | Matches `feature_store_backup_strategy.md` §4's explicit carve-out and this milestone's own exit criterion, which names only local backup | Not scheduled — re-evaluate once real WBS 3.4 data exists and has held long enough to matter (same trigger logic as `feature_store.db`'s own deferral) | Low today — no irreplaceable real data exists yet; local backup + this drill already prove the mechanism |
| `TD-P1-002` (WAL-aware restore support) confirmed to also apply to `label_store.db`, not just `ledger.db`/`btc_trading.db` | Pre-existing, already-logged gap in `restore.py` itself; out of this milestone's scope (no code change to `restore.py`) | Not scheduled (tracked in `CLAUDE.md` §19) | Unchanged from the existing entry — this milestone adds a third affected database to an already-known gap, does not create a new one |
| Quarterly production drill script (`docs/runbook.md`'s bash block) not yet extended with a third `restore.py` invocation for `label_store.db` | `label_store.db` has no production deployment to drill against yet — extending the production script ahead of host-side activation would be premature | WBS 3.4 / host-side activation | None today — a pointer to the standalone drill transcript was added instead (`docs/runbook.md`) |

- [x] **TD3** — No placeholder work. The restore drill actually ran against real local artifacts; nothing here is asserted without having been executed.
- [x] **TD4** — None of the above is long-lived urgent debt for *this* milestone; the WAL-restore item is already tracked in `CLAUDE.md` §19 and this entry only adds a cross-reference, not a new backlog item.

## 9. Definition of Done (DOD)

- [x] **D1** — Compiles/imports cleanly in both environments
- [x] **D2** — Full suite passes, zero new failures (1816/19, `btc_bot`; 61/61, base env for the two touched test files)
- [x] **D3** — 11 new tests, all passing
- [x] **D4** — N/A, explicitly stated (§5, except DBV5 which was genuinely exercised)
- [x] **D5** — Zero new imports from `research/` into any operational-plane module (`scripts/backup.py`/`healthcheck.py` already read SQLite files generically; no new cross-boundary import was added)
- [x] **D6** — Rollback lever named (§7)
- [x] **D7** — No existing signature/table/column renamed, removed, or changed in return type
- [x] **D8** — No `.env`, credential, or secret touched, referenced, or required
- [x] **D9** — Independently deployable — merging this alone changes no existing behaviour except the intentional, documented `--all` extension
- [x] **D10** — N/A for this milestone specifically (no new mandatory Specification/Value field introduced; that was WBS 3.1/3.2's concern, already tested there)
- [x] **D11** — All sections completed, including explicit N/A statements for §4 and the applicable rows of §5/§6

## 10. Reviewer Checklist (RC)

*Signed — Rohith Suresh, 2026-08-07 (`LS-DEC-003`, Approval Package `A1`).*

- [x] **RC1** — Milestone corresponds to WBS 3.10 exactly, no scope creep into 3.4
- [x] **RC2** — No file under `docs/architecture/` touched
- [x] **RC3** — "Files Changed" list matches `git status` exactly
- [x] **RC4** — At least two tests read in full, confirmed to exercise real behaviour
- [x] **RC5** — Full-suite counts reproduced independently in both environments
- [x] **RC6** — Rollback lever confirmed
- [x] **RC7** — Full diff read, including the restore-drill transcript
- [x] **RC8** — Sign-off

  **Reviewer:** Rohith Suresh  **Date:** 2026-08-07

---

## Files Changed

| File | Type |
|---|---|
| `scripts/backup.py` | Modified — `--label-store` flag, dispatch block, `--all` extension, docstring update |
| `scripts/healthcheck.py` | Modified — `--label-store-backup-dir` CLI arg, `check_backup_freshness()` call site in `main()` |
| `src/api/config.py` | Modified — `ApiSettings.label_store_backup_dir` field + `load_settings()` wiring |
| `src/api/services.py` | Modified — `check_backup_freshness()` call site in `runtime_health()` |
| `tests/test_backup.py` | Modified — 6 new `--label-store` tests, 2 pre-existing `--all` tests extended/renamed, `_LABEL_STORE_TABLES` constant, `_setup_root()` extended |
| `tests/test_healthcheck.py` | Modified — 5 new `label_store`-labeled tests, existing `ApiSettings(...)` fixture extended |
| `tests/test_api.py` | Modified — 2 `ApiSettings(...)` fixtures extended (forced by the new required field; file cannot be executed locally, edited correctly per compile check) |
| `docs/ops/label-store-restore-drill-2026-08-06.md` | New — restore drill transcript |
| `docs/runbook.md` | Modified — pointer to the new drill transcript |
| `research/label_store/label_store.db` | Real, local, gitignored artifact — first real registration at its default path (2 Definitions, 4 Specifications) |
| `data/label-store-backups/label_store_20260805_190944.db` + `.manifest.json` | Real, local, gitignored artifact — first-ever `label_store.db` backup |

## Summary

Shipped WBS 3.10 with the approved LS-DEC-001 decision applied literally: one co-located `label_store.db`, one backup unit, mirroring `--ledger`'s already-reviewed shape exactly across `scripts/backup.py`, `scripts/healthcheck.py`, and `src/api/services.py`. The restore drill (`docs/ops/label-store-restore-drill-2026-08-06.md`) is the deliverable that actually earns the exit criterion: it ran the real backup and restore tooling against real local data — including three hand-written, clearly-labeled synthetic forward-tier rows exercising Matured/Void/Declared states, since no real WBS 3.4 data exists yet — and verified, table by table, exactly what Rohith's approval named: the registry's recoverability was proven by demonstrating a *second, independent* path (re-running `register_conventions.py --commit` and getting identical hashes), while the forward tier's was proven by exact-content survival through the *only* path it has (backup/restore), since it cannot be recomputed into existence. One genuine, honest surprise was found and documented rather than glossed over: the first backup run changes the source file's own SHA-256 (a documented, expected `journal_mode=WAL` side effect, already logged as `TD-P1-002` for the other two databases) — row-level content was independently verified unaffected. Synthetic drill data was cleaned up afterward; the real, legitimate registry state and one clean backup artifact were left in place. Zero changes to `research/label_store/registry.py` or `value_store.py`. Next: WBS 3.4 (wrapping the 148 real H6 forward labels) — **not begun**, per instruction.
