# `feature_store.db` — Backup Strategy Recommendation

| Field | Value |
|---|---|
| **Status** | Recommendation only — **not implemented, not scheduled** |
| **Requested by** | WBS 2.5 completion instruction ("review `feature_store.db` and recommend an engineering backup strategy... documentation only unless already in roadmap") |
| **Roadmap coverage** | None — `docs/engineering/implementation_roadmap_v1.md` contains no mention of `feature_store.db` backup at any WBS item. This is new ground, not a gap in an existing plan. |
| **Date** | 2026-08-05 |

---

## 1. Current state

`research/feature_store/feature_store.db` (SQLite, gitignored, ~40 KB as of this review) holds two append-only tables (`feature_definitions`, `feature_specifications`; DB-level triggers block `UPDATE`/`DELETE` on both — see `research/feature_store/registry.py`'s `_SCHEMA_SQL`). It currently has:

- **No backup of any kind.** `scripts/backup.py` names exactly four databases it protects (`data/btc_trading.db`, `research/data/forward_evidence.db`, `research/data/evidence_forward.db`, `research/evidence_library/ledger.db`); `feature_store.db` is not among them.
- **No healthcheck signal.** `scripts/healthcheck.py`'s `check_backup_freshness()` is only ever called for the trading and ledger backup directories (Program P1 Increment 1, `CLAUDE.md` §18).
- **100% regenerable today, verified, not assumed.** Every row in the real local `feature_store.db` was written by `research/feature_store/migrate_evidence_engine_features.py --commit`, confirmed idempotent across three consecutive re-runs this session (identical `spec_hash`es, stable 17/17 row counts each time — see `docs/engineering/incidents/INC-0002-feature-migration-idempotency.md`). Deleting the file and re-running the migration script reproduces it byte-for-byte in content (not byte-for-byte at the SQLite file level, but identical at the row-content level, which is what matters for recoverability).

## 2. Why this is not "just wire it into `backup.py` now"

`scripts/backup.py`'s four existing databases share one property that `feature_store.db` does not yet have: **irreplaceable, hand-authored history.** `ledger.db`'s 70 runs / 26 receipts / 13 transitions are governance decisions that cannot be recomputed from anything else (ADR-0001 §12 calls it "the only truly irreplaceable asset"). `feature_store.db`, today, is the opposite — it is a **deterministic projection** of `migrate_evidence_engine_features.py`'s own declarative `FEATURES` table plus `research/evidence/engine.py`'s unmodified source. Losing the file loses zero information; `python migrate_evidence_engine_features.py --commit` reconstructs it.

Adding backup infrastructure for a fully-regenerable artifact would be protecting against a risk that does not exist yet — the same reasoning WBS 2.1–2.3's own verification record used to justify skipping a backup step for this exact database at its creation (`docs/engineering/verification/records/wbs-2.1-2.3-feature-store-registry.md` §5, DBV5), and the same reasoning this session's WBS 2.4 record flagged for reassessment once real (non-test) data started landing in it (§8 there).

## 3. The trigger condition for when this changes

`feature_store.db` stops being fully regenerable the first time **any** Feature Specification is registered by something other than an idempotent, re-runnable migration script — for example:

- WBS 2.6 (warm-up as a contract term) or WBS 2.7 (conditioning propagation) registering new Specification fields by hand rather than through an extended, still-idempotent script.
- A future operator directly calling `registry.register_definition()`/`register_specification()` outside any script, to register a genuinely new feature that has no corresponding regeneration path.
- Any Specification transitioning to `Deprecated`/`Superseded`/`Retired` by a one-off operator action (`registry.retire_specification()`) rather than a re-runnable process.

**None of these have happened yet.** `git grep -n "register_definition\|register_specification" research/` (as of this review) finds exactly one caller of each: `migrate_evidence_engine_features.py`. The day a second, non-script caller exists, `feature_store.db` acquires the same "hand-authored, not recomputable" property `ledger.db` already has, and the recommendation below should convert from "documented, deferred" to "implemented."

### 3.1 Second trigger — first materialisation (added 2026-08-05, Phase 2 Closeout)

The trigger above is stated entirely in terms of *who writes a Specification*. That framing was complete for WBS 2.5's registry-only scope, but it misses a second, independent way this database stops being regenerable — identified during the [Phase 2 Closure Review](verification/records/phase-2-closure-review.md) finding `F-4`:

> **The first time anything is written to the materialisation tier (`feature_value_materialized`), this document's regenerability argument expires — regardless of who wrote it or whether a script could re-run.**

The materialisation tier exists precisely to hold values that a citing artifact requires to be recoverable *as exact bytes* (`ADR-I010` §7.1, §12.2 case C1). A value set that can be safely recreated by re-running a script does not need materialising in the first place; the tier's entire purpose is the obligation that recomputation cannot discharge. So "re-run the migration script" is not a recovery path for a materialised row — it is the exact thing materialisation exists to make unnecessary.

**Current state: the trigger has not fired, and cannot fire silently.** Neither `feature_value_cache` nor `feature_value_materialized` exists as a table in the real `feature_store.db` — `value_store.init_db()` has never been called against it (verified by direct query, Phase 2 Closure Review §1). The materialisation tier is implemented and tested but has no real tenant, which is the correct steady state while compute-on-read remains the default (`ADR-I010` §12.1).

**What to check, and when.** This trigger is expected to fire in Phase 4, when the Dataset Builder seals its first Training Dataset (`ADR-I010` §16 item 5: *"A Training Dataset triggers materialisation case C1; a Research Dataset does not"*). The check is a single query:

```sql
SELECT COUNT(*) FROM feature_value_materialized;   -- > 0 ⇒ §4 applies, immediately
```

When it fires, §4's recommendation applies **without the "deferred" qualifier** and with one amendment: the "explicitly not recommended" carve-out on off-host replication in §4 should be **reconsidered rather than inherited**, since a lost byte guarantee has the same irrecoverable character that earned `ledger.db` its off-host leg in the first place.

## 4. Recommended strategy (for when the trigger condition above is met)

Mirror `ledger.db`'s existing pattern exactly — `feature_store.db` is architecturally identical to it (SQLite, single local writer, append-only via DB-level triggers, no concurrent-writer scenario), so no new backup mechanism needs to be designed:

1. **`scripts/backup.py`**: add a fifth named database, following the `--ledger` flag's exact shape (`VACUUM INTO`, SHA-256 manifest, 7-daily/4-weekly/12-monthly rotation — all of `run_backup()` is already generic over `db_path`/`backup_dir`/`db_label`; this is an additive call site, not new logic). Suggested flag: `--feature-store`, suggested label: `feature_store`, suggested default backup dir: `data/feature-store-backups/` (matching `data/ledger-backups/`'s naming convention).
2. **`scripts/healthcheck.py`**: add a third `check_backup_freshness()` call site with `label="feature_store"`, mirroring Increment 1's `ledger` wiring exactly (both `main()`'s CLI path and `src/api/services.py`'s `runtime_health()`, for CLI/API parity — Increment 1's own stated reason for touching both).
3. **No new mechanism, no new severity tier, no new retention policy** — every parameter this needs already exists as a generic argument on functions written for `ledger.db`. This is expected to be a small, mechanical PR (comparable in size to Increment 1 itself), not a design exercise.

**Explicitly not recommended:** wiring `feature_store.db` into off-host replication (`scripts/offhost_backup.py`) at the same time as the local backup. `ledger.db` earned off-host replication because it is irreplaceable governance history with real operational consequence if lost; `feature_store.db` should earn that same protection only after it has held hand-authored, non-regenerable state for long enough to matter, not automatically the day local backup is added.

## 5. What this document is not

This is not an implementation plan with a committed timeline, and it is not itself a roadmap item — WBS 2.5's own instruction was explicit that this review is documentation-only. No code in `scripts/backup.py` or `scripts/healthcheck.py` was touched to produce this recommendation. If a future session wants to act on it, §4 above is written to be directly actionable without further design work — the trigger condition in §3 is the thing to check first.
