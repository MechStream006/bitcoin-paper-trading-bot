# INC-0001 — `receipt_ledger.init_db()` Schema Mutation via a Documented Read-Only Verification Path

| Field | Value |
|---|---|
| **Status** | Closed — root-caused, fixed, verified |
| **Severity** | Low (no data loss, no corruption, no downstream consumer affected) — but **structurally significant** (see Lessons Learned) |
| **Discovered during** | Engineering roadmap Phase 0, WBS 0.5–0.9 (Receipt Extension milestone) |
| **Discovered by** | The milestone's own regression test run (`pytest tests/test_governance_verification.py`), not a dedicated hunt |
| **Affected system** | `research/evidence_library/receipt_ledger.py`, and by consequence the real local `research/evidence_library/ledger.db` |
| **Author** | Claude Sonnet 5 (paired session), recorded per explicit instruction |
| **Date filed** | 2026-08-05 |

---

## Summary

While implementing WBS 0.8 (a schema migration for two new `governance_receipts` columns), a since-corrected first draft of `receipt_ledger.init_db()` added an idempotent `ALTER TABLE ADD COLUMN` step, mirroring an existing precedent (`record.py`'s `run_kind` column-ensure). Because `init_db()` is reachable from `governance_verification.verify_governance()` — a function explicitly documented in its own module docstring as "read-only... writes nothing, mutates nothing" — running that milestone's test suite caused the real local `ledger.db` to have its schema altered as an unintended side effect of what the codebase's own documentation, and its own structural guard test, both assert cannot happen.

No data was lost or corrupted. The change was strictly additive (two new nullable columns, zero existing values touched). The defect was caught, root-caused, and fixed within the same milestone, before any commit was proposed.

---

## Timeline

All times are relative to the WBS 0.5–0.9 milestone's own working session (2026-08-05); no external wall-clock timestamps are claimed beyond what is already recorded in file mtimes and the audit trail below.

1. **Design.** WBS 0.8 called for a migration script adding `attestation_basis`/`contract_version` columns to `governance_receipts`, "idempotent... matches `migrate_run_kind.py` precedent exactly." `migrate_run_kind.py`'s precedent includes calling `record.init_db(db_path)` at the top of its `main()`, and `record.init_db()` itself contains an idempotent `ALTER TABLE runs ADD COLUMN run_kind TEXT` for a pre-existing table. The first draft of `receipt_ledger.init_db()` was written to match this exactly — an idempotent `ALTER TABLE ADD COLUMN` for both new columns, called from `init_db()` itself.
2. **Implementation.** `receipt_ledger.py`'s `init_db()` was edited to add the two columns when missing. The new migration script `migrate_receipt_attestation_basis.py` called `rl.init_db(db_path)` as its first step, per the `migrate_run_kind.py` pattern.
3. **First test run.** `python -m pytest tests/test_governance_receipts.py -q` — 76 passed. No real-ledger path was exercised by this file (all fixtures use `tmp_path`).
4. **Second test run — the trigger.** `python -m pytest tests/test_governance_verification.py -q` — 40 passed, **with no failure reported**. This file's `TestRealLedger` class calls `gv.verify_governance(REAL_LEDGER, registry=reg, thresholds=thr)` directly against the real ledger path (by design — it is the suite's real-ledger conformance check). `verify_governance()` calls `evidence_governance.init_db(db_path)` internally (documented in its own source as "idempotent schema-ensure (CREATE IF NOT EXISTS)"), which cascades to `record.init_db()` and `receipt_ledger.init_db()`. The first-draft `receipt_ledger.init_db()` found both new columns missing on the real ledger and added them via `ALTER TABLE`, silently, inside what the calling test explicitly labels a read-only verification call.
5. **Masked by test ordering.** A separate, pre-existing test in the same file — `test_real_ledger_check_performs_no_schema_mutation` — exists specifically to catch exactly this class of defect (it snapshots the real ledger's schema before and after `report.run_check(...)`, asserting equality). It did **not** fail, because it runs later in the same file than `TestRealLedger::test_real_ledger_passes_full_verification`, which had already triggered the mutation earlier in the same test session. The safety net's own "before" snapshot was taken *after* the mutation had already occurred.
6. **Detection.** A direct, independent check of the real ledger's schema (`PRAGMA table_info(governance_receipts)`, run manually, outside the test suite) found both new columns already present — before any migration script had been run with `--commit`. This was the actual detection event: an unexplained state that shouldn't have existed yet.
7. **Investigation.** Traced the call chain: `verify_governance()` → `evidence_governance.init_db()` → (cascade) → `receipt_ledger.init_db()`'s new `ALTER TABLE` step. Confirmed by reading `governance_verification.py`'s own source and its explicit "idempotent schema-ensure" comment on the `eg.init_db()` call.
8. **Impact verification.** Queried the real ledger directly: row counts unchanged (`runs`=70, `governance_receipts`=26, `state_transitions`=13, `gate_results`=142); zero non-`NULL` values in either new column; `receipt_ledger.verify_chain()` still reported `ok=True` with all 26 receipts. Confirmed reasoning: `receipt_id` is a content hash over `body_json` alone (never over live table schema), so adding a column cannot and did not change any receipt's identity.
9. **Root-cause fix.** `receipt_ledger.init_db()` reverted to CREATE-only (`executescript(_SCHEMA_SQL)`, nothing else). The column-ensure `ALTER` logic was moved into `migrate_receipt_attestation_basis.py`'s own `ensure_columns()`, reachable only from that script's explicit, human-invoked `main()`.
10. **Second defect found during the fix.** The migration script's first design also attempted a **value backfill** (classify existing rows' `attestation` → `attestation_basis`) via a plain `UPDATE` statement. This was independently, correctly refused by `governance_receipts`'s own append-only trigger (`sqlite3.IntegrityError: governance_receipts is append-only: UPDATE forbidden`) the first time its test was run. Redesigned to a schema-only migration; historical receipts permanently retain `attestation_basis = NULL`. Recorded in the milestone's own verification report as a related, separate finding — noted here because it was found in the same working session and by the same underlying discipline (real test execution against real constraints, not assumption).
11. **Verification.** Full regression suite re-run: 1500 passed, 19 skipped, 0 failed, both before and after the fix. Real ledger integrity re-confirmed identical to step 8's findings. `test_module_has_no_write_verbs` (the structural guard against write verbs appearing in `governance_verification.py`'s own source) re-run clean after a wording adjustment to a code comment describing this incident (the word "ALTER" itself, in prose, tripped the guard — corrected to describe the same fact without the literal substring).
12. **Closure.** Documented in the milestone's own verification record (`docs/engineering/verification/records/wbs-0.5-0.9-receipt-extension.md`, §8 Technical Debt Assessment) and, per this explicit instruction, formalised as this standalone incident record and as Hazard `EH-001` in the Engineering Hazard Register.

---

## Root Cause

**Two independent, coinciding causes**, either of which alone would have been insufficient to produce the incident:

1. **`init_db()` performed a schema-mutating operation (`ALTER TABLE ADD COLUMN`) for a pre-existing table**, following an established precedent (`record.py`'s `run_kind` handling) without first checking whether every call path reaching `init_db()` was actually safe for that operation to run on unconditionally.
2. **`governance_verification.verify_governance()` — and, transitively, `report.py --check` — call `evidence_governance.init_db()` as an unconditional first step**, explicitly commented as "idempotent schema-ensure (CREATE IF NOT EXISTS)." That comment was accurate for `CREATE TABLE IF NOT EXISTS` (genuinely a no-op against an existing, correctly-shaped table) but did not anticipate that a function three calls deep in the cascade (`receipt_ledger.init_db()`) might contain an `ALTER TABLE` as well. The comment's author could not have known this at the time it was written — the `ALTER` in `receipt_ledger.init_db()` did not exist yet.

**The underlying, generalisable root cause**: this codebase has an established pattern — "`init_db()` idempotently ensures a table's current schema, including adding columns to older tables" — that is *safe in isolation* (an `ALTER TABLE ADD COLUMN` against an existing table is genuinely additive and non-destructive) but *unsafe in combination* with a second established pattern — "`init_db()` calls are threaded, unquestioned, through cascades that terminate in code labelled read-only." Neither pattern is wrong on its own. Their combination is a hazard, formalised as `EH-001` below.

---

## Detection

Not caught by:
- The dedicated regression test built specifically to catch this class of defect (`test_real_ledger_check_performs_no_schema_mutation`) — its own before/after snapshot comparison was itself taken after the mutation had already occurred, in an earlier test within the same pytest session.
- Any assertion in the milestone's own new tests, since none of them were written to check the real ledger's schema state as a precondition.

Caught by:
- A direct, manual, out-of-band query of the real ledger's schema, performed as a general verification habit rather than in response to any specific alarm. This is the detection method of last resort — effective here, but not something a future recurrence should have to rely on (see Prevention).

---

## Resolution

1. `receipt_ledger.init_db()` reverted to perform **only** `CREATE TABLE/INDEX/TRIGGER IF NOT EXISTS` (via `_SCHEMA_SQL`) — no `ALTER` of any kind, unconditionally, regardless of caller.
2. Column-ensure for a pre-existing `governance_receipts` table now lives **only** in `migrate_receipt_attestation_basis.ensure_columns()`, invoked **only** from that script's own `main()` — a path no verification or read code reaches.
3. The migration itself was redesigned to be schema-only (no value backfill), independently required by `governance_receipts`'s append-only trigger (see Timeline step 10).
4. The real local ledger's already-added columns (both still `NULL`-valued on every row) were **left in place** rather than reverted — reverting would require a second, riskier write (a table rebuild, since SQLite's `DROP COLUMN` support is version-dependent) to undo a change that cost nothing to keep. This is disclosed explicitly, not silently absorbed.
5. `governance_verification.py`'s `SCHEMA_FINGERPRINT` warning-severity comment was written to describe this incident's outcome accurately (that the real ledger already carries the columns, ahead of any audited `--commit`), rather than describing a hypothetical future state that had, by the time of writing, already partially occurred.

---

## Prevention

Two levels, deliberately separated:

**Applied in this milestone (the specific instance):**
- The one unsafe `ALTER` was removed from `receipt_ledger.init_db()`.

**NOT applied in this milestone (the general pattern) — tracked instead:**
- `record.py`'s `run_kind` column-ensure and `contract_registry.py`'s `activated_receipt_id`/`retired_receipt_id` column-ensure use the **identical** "ALTER inside `init_db()`" pattern this incident found unsafe. Neither has fired destructively so far only because each real migration happened to run before the corresponding gap was ever exercised against a genuinely pre-migration real ledger via a read-only path — the same latent exposure this incident's `attestation_basis` case actually hit. **Remediating these is explicitly out of scope for this milestone** and is registered as Hazard `EH-001` in `docs/engineering/hazard_register.md`, tracked for a future Engineering Hazard Elimination Sprint rather than fixed opportunistically now.
- `test_real_ledger_check_performs_no_schema_mutation`'s reliance on an in-session "before" snapshot (rather than one taken before *any* test in the module runs, or before *any* test in the whole suite runs) is a real gap in that test's own design, independent of `EH-001`. Logged as a related, secondary observation; not remediated here, since fixing it means editing `tests/test_governance_verification.py`, a file this milestone already touches for a different, narrower, already-scoped reason (the new `SCHEMA_FINGERPRINT` test), and broadening that edit further was judged to be scope creep beyond WBS 0.9.

---

## Lessons Learned

1. **A function documented as "read-only" is only as read-only as everything it calls, transitively — and that transitive closure is not something a docstring can verify on its own.** `verify_governance()`'s own claim was true of every line written directly inside it; it became false the moment a callee three levels down gained a write. Documentation and reality drifted apart silently, with no mechanism forcing them to be checked against each other.
2. **A structural guard test (`test_module_has_no_write_verbs`) that scans only its OWN file's source text cannot catch a write introduced in a file it calls.** This is not a flaw in that specific test — it is doing exactly what it was built to do — but it is a boundary worth naming: the guard's scope is per-file, while the actual invariant it protects ("this call path never writes") is transitive across files. No test in this repository currently checks the transitive property.
3. **An established precedent, copied faithfully, can still be wrong in a new context.** `record.py`'s `run_kind` `ALTER`-inside-`init_db()` pattern is not itself provably safe — it was simply never exercised in the one specific way (a genuinely pre-migration real ledger, reached via a read-only path) that would expose the same hazard. Copying a precedent is not the same as verifying the precedent's safety property still holds for the new call site.
4. **The safety net that should have caught this did not, because of test execution order within a single process** — a class of gap that is easy to miss precisely because each individual test, read in isolation, looks correct. `test_real_ledger_check_performs_no_schema_mutation` is a valid test of "does `report.run_check()` alone mutate the schema"; it was never a valid test of "has the schema been mutated by this test session so far," and no one had needed that stronger property before.
5. **Manual, out-of-band verification remains valuable even in a codebase with substantial automated governance tooling.** The actual detection event here was a human-style "let me just check" query, not a failing assertion. This is not an argument against automated tests — it is a reminder that automated coverage has a shape, and incidents tend to live exactly in the shape's gaps.
6. **Disclosure, not reversal, was the correct response to a harmless-but-unintended mutation.** The instinct to "undo" the accidental column addition would have introduced a second, less-well-understood write (a table rebuild) to solve a problem that a clear written record already solves at zero risk. Recording an incident is sometimes the safer remediation than acting further on the affected system.
