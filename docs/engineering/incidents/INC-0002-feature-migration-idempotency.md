# INC-0002 — Feature Specification Migration Not Actually Idempotent

| Field | Value |
|---|---|
| **Status** | Closed — root-caused, fixed, verified |
| **Severity** | Low (no data loss; the only affected database is brand-new, self-contained, and had zero external consumers at the time of the defect) |
| **Discovered during** | Engineering roadmap Phase 2, WBS 2.4 (Feature Specification migration milestone) |
| **Discovered by** | Running the migration script a second time as a routine idempotency check (the exact check `DBV3` of the Engineering Verification Standard calls for), not a dedicated hunt |
| **Affected system** | `research/feature_store/migrate_evidence_engine_features.py`, and the real local `research/feature_store/feature_store.db` it wrote to |
| **Author** | Claude Sonnet 5 (paired session), recorded per standing instruction: incidents are recorded when verification reveals a real engineering failure |
| **Date filed** | 2026-08-05 |

---

## Summary

`migrate_evidence_engine_features.py`'s own docstring asserted it was idempotent: *"re-running after a successful `--commit` re-registers the same content-addressed Specifications."* This was false. Every `--commit` run unconditionally called `registry.register_definition()`, which — correctly, by its own documented contract — always creates the **next** Feature Definition version. Since `definition_version` is part of a Feature Specification's content hash, each re-run produced a **new** Specification hash for every one of the 17 migrated features, silently doubling the registry on every re-run rather than recognising "this has already been registered."

Caught immediately: the second `--commit` run (performed as a routine DBV3 check, not in response to any suspicion) produced 17 new specification hashes, all different from the first run's. A direct row-count check confirmed `feature_definitions` held 34 rows, not 17.

No data was lost. `feature_store.db` is a database created for the first time within this same milestone, with no other consumer yet — the defect was caught and corrected before the file was ever relied upon by anything.

---

## Timeline

1. **Implementation.** `migrate_evidence_engine_features.py` was written calling `reg.register_definition(...)` directly, once per feature, inside `run()`.
2. **First `--commit`.** Run against the real local `research/feature_store/feature_store.db` (newly created in this same milestone). All 17 features registered and activated successfully — 17 `feature_definitions` rows, 17 `feature_specifications` rows, all `Active`.
3. **Second `--commit` — the trigger.** Run again, as the routine "does re-running produce zero new writes" check this Engineering Verification Standard's DBV3 requires for every migration script. It did not: 17 **new** specification hashes were printed, all different from the first run's.
4. **Detection.** A direct query confirmed `feature_definitions` held 34 rows (17 features × 2 versions each) and `feature_specifications` held 34 rows.
5. **Root-cause investigation.** Traced to `register_definition()`'s own, already-documented, already-correct behaviour: *"Always creates the NEXT ordinal version for `name`... there is no update path."* This is the right behaviour for a human deliberately versioning a Definition. It is the wrong behaviour to call unconditionally from an idempotent script, and the migration script's `run()` did exactly that.
6. **Fix.** Added `registry.get_latest_definition(db_path, name)` — a new, purely additive, read-only function returning the highest-version row for a name, or `None`. Added `_get_or_register_definition()` to the migration script: reads the latest existing Definition for a name; if its `rationale`/`intended_role` are unchanged, reuses that version; only calls `register_definition()` (creating a genuinely new version) when the content has actually changed.
7. **Cleanup.** The real local `feature_store.db`, left in a doubled-but-harmless state by the pre-fix runs, was deleted and regenerated cleanly with the fixed script — a legitimate action here (unlike `ledger.db`'s protected governance history) because this database had existed for under an hour, in this same session, with zero external consumers.
8. **Verification.** Ran `--commit` twice more with the fix in place: identical specification hashes both times, row counts held at 17/17 on the second run. Confirmed genuinely idempotent.
9. **Closure.** Documented here, in `docs/engineering/feature_migration_register.md`, and in this milestone's own verification record.

---

## Root Cause

**A correct, already-documented function was called from a context whose correctness requirement it did not satisfy.** `register_definition()`'s "always create the next version" behaviour is exactly right when a human is deliberately choosing to version a Definition (ADR-I010 §6.1: Definitions are versioned, immutable per version, with no update path — by design). A migration script re-run, however, needs a **different** operation: "ensure this Definition exists with this content," which is idempotent by nature. Nothing in the Phase 2.1–2.3 milestone's own scope (proven only against hand-written, one-shot test Specifications, never against a re-run migration) had ever exercised the "call this twice with the same intent" case, so the gap was invisible until this milestone's real, repeated-invocation usage pattern hit it.

This is a narrower, single-function version of the general lesson `INC-0001` already recorded: **a component's local correctness does not guarantee correctness for every caller's usage pattern**, and the caller — not the component — is responsible for verifying that its own idempotency requirement actually holds, empirically, not by assumption.

---

## Detection

Not caught by:
- Any test written during Phase 2.1–2.3 (correctly — that milestone never claimed migration-script idempotency; it built the registry primitives only).
- A read of the migration script's own docstring, which asserted idempotency confidently and incorrectly.

Caught by:
- Literally running the documented "how to use this script" instructions twice, exactly as `DBV3` (Engineering Verification Standard §5) requires for every migration script before a milestone can be marked done. This is the second incident in this roadmap's execution where a required verification-standard check — not a special investigation — is what found the defect (`INC-0001`'s equivalent was the dedicated regression test's own before/after schema snapshot; here it was DBV3's own "run it twice" instruction, both of which found the actual bug in this session).

---

## Resolution

1. Added `registry.get_latest_definition()` — additive, read-only, does not change `register_definition()`'s own documented contract or any existing test's expectations.
2. Added `migrate_evidence_engine_features._get_or_register_definition()` — the idempotency decision now lives in the migration script, where the "does this content already exist" question actually belongs, not inside the generic registry primitive.
3. Deleted and cleanly regenerated the real local `feature_store.db`.
4. Added a dedicated regression test (`test_migration_is_actually_idempotent_on_a_real_run`, see this milestone's test files) that runs the migration script's `run()` function twice against the same database and asserts the resulting specification hashes are identical the second time — closing the exact gap that let this ship in the first place.

---

## Prevention

- **Applied in this milestone:** the specific fix above.
- **Not applied, and not needed:** this does not generalise the way `EH-001` did. `register_specification()` (the other write path in `registry.py`) already handles duplicate content correctly by design — confirmed by an existing, passing test from the prior milestone (`test_registering_identical_content_twice_is_idempotent`). The defect was isolated to `register_definition()`'s interaction with exactly one caller (this migration script); no other file in the repository calls `register_definition()` in a loop or from a re-runnable context. **No Hazard Register entry is warranted** — `EH-001`'s bar is "a pattern that recurs beyond the file(s) this milestone touched"; this does not recur anywhere else.
- **Generalisable habit, not a hazard:** any *future* migration script that calls a "always creates a new X" registration primitive (as several already exist in this codebase — `register_definition()` here, and conceptually similar version-incrementing writers elsewhere) must independently verify, empirically, that its own re-run is idempotent — rather than assuming a primitive's local correctness transfers to the script's own usage pattern. This is restated from `INC-0001`'s own lessons, not a new principle.

---

## Lessons Learned

1. **DBV3 ("run the migration twice, confirm zero new writes") is not a formality — it found a real, ship-blocking defect on its very first application this session.** The instinct to treat a written-down verification step as a checkbox is exactly the instinct this incident argues against.
2. **A function's own docstring being correct does not make a caller's docstring correct.** `register_definition()`'s documentation was accurate throughout this incident; `migrate_evidence_engine_features.py`'s documentation was wrong, about a different function's composition, until it was actually tested.
3. **A brand-new, self-contained database with zero consumers is the right place to be permissive about deleting and regenerating state** — the same action would be entirely wrong for `ledger.db`. Distinguishing "governed, irreplaceable state" from "a development artifact that can be safely regenerated" is a judgment call worth making explicitly each time, not a rule to apply uniformly.
4. **The fix belongs at the layer that has the context to make the idempotency decision correctly** — the migration script, which knows "this call represents an idempotent re-run intent," not the generic registry primitive, which correctly has no way to know that and should not guess.
