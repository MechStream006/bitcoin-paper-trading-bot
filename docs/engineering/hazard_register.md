# Engineering Hazard Register

| Field | Value |
|---|---|
| **Title** | Engineering Hazard Register — Research Platform |
| **Status** | **Active** |
| **Version** | 1.0.0 |
| **Document Type** | Engineering Process Register (not an ADR, not an architecture document) |
| **Companion** | [Engineering Verification Standard](verification/engineering_verification_standard.md); [INC-0001](incidents/INC-0001-init-db-side-effect.md), [INC-0003](incidents/INC-0003-engine-module-name-collision.md) (origin incidents) |
| **Authors** | Rohith Suresh — Owner / Architect · Drafted with Claude Sonnet 5 |
| **Last Updated** | 2026-08-05 |

---

## 0. Purpose

A **hazard** is a discovered, generalisable, *not-yet-remediated* risk pattern in the repository — distinct from a **technical debt item** (a milestone-scoped simplification, tracked in that milestone's own verification record, §8 of the [Verification Standard](verification/engineering_verification_standard.md)) and distinct from an **incident** (a specific event that already occurred, recorded under `docs/engineering/incidents/`).

A hazard is registered when:
1. An incident, or a milestone's own investigation, surfaces a pattern that recurs **elsewhere in the repository**, beyond the file(s) that milestone touched, and
2. Fixing it everywhere is **out of scope** for the milestone that found it (would violate that milestone's own blast-radius or file-scope discipline).

**This register does not remediate anything.** It exists so that hazards found once are not silently forgotten, and so a future, dedicated Engineering Hazard Elimination Sprint has a real, evidence-based backlog rather than starting from a fresh audit.

---

## 1. Register

| ID | Title | Severity | Status | Registered |
|---|---|---|---|---|
| [`EH-001`](#eh-001--alter-statements-inside-init_db-reachable-from-verification-paths) | ALTER statements inside `init_db()` reachable from verification paths | Low (no known exploit/incident beyond `INC-0001`) — structurally significant | **Open — not remediated** | 2026-08-05 |
| [`EH-002`](#eh-002--bare-import-engine-is-not-collision-safe-across-subsystems) | Bare `import engine` is not collision-safe across subsystems (`sys.modules` is process-global) | Low today (one known unfixed instance remains, not currently imported by any test; 3 other known instances fixed as of WBS 2.5) — structurally significant, deterministic once triggered (not intermittent) | **Open — 1 of 4 known instances not remediated** | 2026-08-05 |

---

## `EH-001` — ALTER statements inside `init_db()` reachable from verification paths

| Field | Value |
|---|---|
| **Severity** | Low today (every known instance is additive-only: nullable `TEXT` columns, no data loss possible) — but the *pattern* has no severity ceiling; a future column-ensure with a `NOT NULL`, a `DEFAULT`, or any data-shaping default could turn "low" into "real" the next time this pattern is copied |
| **Status** | Open — not remediated. Tracked for a future Engineering Hazard Elimination Sprint per explicit instruction |
| **Origin** | [`INC-0001`](incidents/INC-0001-init-db-side-effect.md) |
| **Discovered** | 2026-08-05, during engineering roadmap Phase 0 (WBS 0.5–0.9) |

### Description

A recurring pattern in this codebase: a module's `init_db(path)` function performs `CREATE TABLE/INDEX/TRIGGER IF NOT EXISTS` (safe — a genuine no-op against an existing, correctly-shaped object) **and also** an idempotent `ALTER TABLE ADD COLUMN` for columns that predate the module's current schema (safe **in isolation** — additive, non-destructive to existing rows).

The hazard is not either operation alone. It is that `init_db()` functions following this pattern are called, unconditionally and without a call-site-specific safety check, from **cascades that terminate in code explicitly documented as read-only** — `governance_verification.verify_governance()` (own docstring: *"This module only reads and reports. It writes nothing, mutates nothing"*) and, transitively, `report.py --check`. The first time such a cascade runs against a real database that genuinely predates a given column, the "safe in isolation" `ALTER` fires for real, silently, inside a call path a reader has every reason to trust is inert.

### Known instances

| Module | Function | Column(s) ensured | Confirmed exercised against real data yet? |
|---|---|---|---|
| `research/evidence_library/receipt_ledger.py` | `init_db()` | `attestation_basis`, `contract_version` | **Yes — this is `INC-0001`.** Fixed: the `ALTER` was removed from `init_db()` and moved to `migrate_receipt_attestation_basis.ensure_columns()`, reachable only from that script's own explicit `main()`. |
| `research/evidence_library/record.py` | `init_db()` | `runs.run_kind` | Not exercised destructively (the real migration, `migrate_run_kind.py --commit`, ran before this exposure was ever hit against a genuinely pre-migration real ledger) — but the code path is structurally identical to the one that fired in `INC-0001`. **Not fixed.** |
| `research/evidence_library/contract_registry.py` | (its own schema-ensure function) | `activated_receipt_id`, `retired_receipt_id`, `activation_receipt_status`, `retirement_receipt_status` | Not exercised destructively for the same reason as `record.py` above. **Not fixed.** |

This list was compiled from the exact two cross-references made during `INC-0001`'s investigation (`grep`-confirmed `ALTER TABLE.*ADD COLUMN` patterns adjacent to `init_db`-named functions), **not from an exhaustive repository-wide sweep.** A dedicated Hazard Elimination Sprint should re-run that sweep formally before treating this list as complete — see §2 below.

### Why not fixed now

Per explicit instruction accompanying this hazard's registration: *"Do not remediate repository-wide hazards yet. Track them for the future Engineering Hazard Elimination Sprint."* Independently, fixing `record.py`/`contract_registry.py` would mean editing files no WBS item in the currently-approved roadmap names, which would violate this repository's own standing scope discipline (minimise blast radius, no unrelated refactoring) for the milestone that found the hazard.

### What remediation would look like (recorded for the future sprint, not authorised now)

1. A repository-wide, tool-assisted sweep (AST- or regex-based, not manual) for every `init_db()`-or-equivalent function containing an `ALTER TABLE ADD COLUMN` statement.
2. For each: determine every call path that reaches it, and whether any such path is documented, or reasonably relied upon, as read-only.
3. Where a read-only path exists: move the `ALTER` out of `init_db()` into the one explicit, human-invoked migration script that owns that column — mirroring `INC-0001`'s resolution for `receipt_ledger.py` exactly, so this becomes a repeated, verified pattern rather than a one-off fix.
4. Strengthen (or add) a structural test analogous to `test_module_has_no_write_verbs`, but scoped to the **transitive call graph** reachable from `verify_governance()`/`report.py --check`, not just the literal text of `governance_verification.py` itself — closing the detection gap `INC-0001` §Lessons Learned item 2 names.
5. Strengthen `test_real_ledger_check_performs_no_schema_mutation` (or add a sibling test) to snapshot the real ledger's schema **before any test in the full suite runs**, not merely before the specific test's own call — closing `INC-0001` §Lessons Learned item 4.

### Rollback / mitigation available today, without a full remediation sprint

None of the three known instances currently poses a live risk **as long as their real migrations have already been run** (true today for `run_kind` and the `contract_registry.py` columns, per `INC-0001`'s own investigation). The hazard only manifests the *first* time a read-only-labelled verification path runs against a database that genuinely predates a given column — which, for the two un-fixed instances, has already safely passed. The hazard is dormant, not active, for those two — but remains structurally present for **any future column** added the same way, which is the reason for registering it now rather than waiting for a second incident.

---

## `EH-002` — Bare `import engine` is not collision-safe across subsystems

| Field | Value |
|---|---|
| **Severity** | Low today (exactly one known unfixed instance, and it is a standalone script never imported by any test — not currently triggerable) — but deterministic, not intermittent, once two colliding files ever share a process (unlike `EH-001`, which degrades gracefully; this one produces an immediate, loud `AttributeError` the first time it fires) |
| **Status** | Open — 3 of 4 known instances fixed (2 during WBS 2.4, 1 during WBS 2.5); 1 remaining instance (`compute_dsr_for_registration.py`) not remediated, dormant. Tracked for a future Engineering Hazard Elimination Sprint per the same standing instruction as `EH-001` |
| **Origin** | [`INC-0003`](incidents/INC-0003-engine-module-name-collision.md) |
| **Discovered** | 2026-08-05, during engineering roadmap Phase 2 (WBS 2.4); a third instance found and fixed during WBS 2.5 (2026-08-05) |

### Description

`sys.modules` is process-global. At least two unrelated subsystems in this repository each name a module `engine.py` and reach it via this repository's established bare-sibling-import convention (`sys.path.insert(0, <dir>)` then `import engine`): `research/dq/engine.py` (PR-7's Data Completeness Framework) and `research/evidence/engine.py` (the Phase B signals/derived-features engine). Whichever one is imported *first* in a given process silently wins the `sys.modules['engine']` slot; every later bare `import engine` anywhere else in that same process — regardless of which directory it inserted onto `sys.path` — transparently receives the wrong module, with no import error. The failure only surfaces later, as an `AttributeError` on whatever attribute the caller actually needed and the wrong module doesn't have.

This is a property of the **combination** of two files, not a defect in either file alone — the bare-sibling-import convention remains correct and is not being proposed for removal; it only needs a project-wide "no two `sys.path`-inserted modules should share a bare importable name" invariant that nothing currently checks for.

### Known instances

| Consumer | Target module | Collides with | Currently triggerable? |
|---|---|---|---|
| `tests/test_evidence_engine_bindings.py`, `tests/test_migrate_evidence_engine_features.py`, `research/feature_store/evidence_engine_bindings.py`, `research/feature_store/migrate_evidence_engine_features.py` | `research/evidence/engine.py` | `tests/test_dq_engine.py` (→ `research/dq/engine.py`) under full-suite pytest collection | **Fixed as of `INC-0003`** — these four files now resolve `research/evidence/engine.py` via `importlib.util.spec_from_file_location()` under a process-unique key, immune to the collision regardless of import order. |
| `tests/test_decision_function_research_equivalence.py` (WBS 2.5, new) | `research/evidence/engine.py`, transitively also `research/evidence/signals.py` (which itself does `from engine import SignalSpec`) | `tests/test_dq_engine.py` under full-suite pytest collection | **Fixed at creation** — reuses `evidence_engine_bindings.engine` plus a scoped `sys.modules['engine']` save/restore around the one `import signals` call, so `signals.py`'s own transitively-vulnerable import resolves correctly regardless of collection order, with no lingering global mutation afterward. |
| `research/forward_evidence/tests/test_atr_expansion.py::TestDecisionFnAtr::test_DFA07_cross_validation_vs_signals_py` | `research/evidence/signals.py` (→ `from engine import SignalSpec`) | `tests/test_dq_engine.py`, in any full-suite ordering where it collects/runs first | **Fixed during WBS 2.5** — this is the pre-existing, previously-documented-as-"intermittent" flake from Program P1 Increment 6 (`CLAUDE.md` §18), now root-caused as a third instance of this exact hazard rather than an unexplained flake. Reproduced deterministically (`git stash` the fix, run `pytest tests/test_dq_engine.py research/forward_evidence/tests/test_atr_expansion.py::...::test_DFA07...` → `ImportError: cannot import name 'SignalSpec' from 'engine'`), then fixed with the same scoped-`sys.modules`-override technique, verified to survive the deliberately-poisoned ordering. |
| `research/forward_evidence/compute_dsr_for_registration.py` | `research/evidence/engine.py` | `research/dq/engine.py`, in any process where both happen to be imported | **Not fixed. Not currently triggerable** — this file is a standalone CLI script (`python forward_evidence/compute_dsr_for_registration.py`), never imported by any test file (confirmed by repository-wide search) and never run in the same process as anything importing `research/dq/engine.py`. Dormant, not active — identical to `EH-001`'s framing for its two un-fixed instances. **The only remaining open instance of this hazard.** |

This list was compiled from a `grep -rl "^import engine"` / `from engine import` sweep across `tests/` and `research/`, refreshed during WBS 2.5's own investigation, **not from an exhaustive audit of every possible bare-import name collision in the repository** (only the `engine` name was checked; other common filenames — e.g. `rules.py`, `contract.py` — were not swept for the same risk). A dedicated Hazard Elimination Sprint should re-run a fuller sweep before treating this list as complete.

### Why not fixed now

The only remaining unfixed instance (`compute_dsr_for_registration.py`) is a file no WBS item in the currently-approved roadmap names, and it is not currently exercised by anything that would trigger the collision — fixing it now would be unrelated-file scope creep for WBS 2.4, which this milestone was explicitly told to avoid. It is safe to leave open because it is dormant, not active, exactly as `EH-001`'s two un-fixed instances are.

### What remediation would look like (recorded for the future sprint, not authorised now)

1. A repository-wide sweep for every bare `import <name>` reachable via a `sys.path.insert`-based sibling-import, grouped by `<name>`, to find every colliding pair (not just `engine`).
2. For each collision found: apply the same fix `INC-0003` applied to the four WBS 2.4 files — load the intended target via `importlib.util.spec_from_file_location()` under a process-unique key, or (a lower-effort alternative worth evaluating at sprint time) rename one of the colliding files so the bare names no longer collide at all, which would be a genuine architectural simplification but is out of scope for any single milestone to decide unilaterally.
3. Add a structural, repository-wide test that fails collection if two different files under `sys.path.insert`-eligible directories share a bare module name — turning this from a runtime `AttributeError` into a build-time signal, closing the detection gap that let this pattern exist undetected until `INC-0003`.

### Rollback / mitigation available today, without a full remediation sprint

None needed for the fixed instances (`INC-0003`'s four files are immune to import order now). The one remaining unfixed instance poses no live risk today because it is never imported by any test and is only ever run as an isolated `python <script>.py` invocation, which starts a fresh process each time — the collision requires two colliding imports to share one process, which does not currently happen for this file. This could change the day someone writes a test that imports `compute_dsr_for_registration.py`, which is exactly why this is registered now rather than left to a second incident.

---

## 2. Adding a New Hazard

1. A hazard is registered when an incident or milestone investigation finds a pattern that recurs beyond the current milestone's own file scope.
2. Allocate the next `EH-###` identifier (sequential, never reused, even if a hazard is later closed).
3. Add a row to §1 and a full entry below it, following `EH-001`'s section shape: Description, Known instances (with an explicit statement of how exhaustively that list was compiled), Why not fixed now, What remediation would look like, Rollback/mitigation available today.
4. Link the originating incident (if any) under `docs/engineering/incidents/`.
5. **Do not remediate the hazard as part of registering it**, unless a future instruction explicitly authorises immediate remediation for that specific hazard.

---

## 3. Governance of This Register

Same governance model as the [Engineering Verification Standard](verification/engineering_verification_standard.md) §13: an engineering process document, not part of the IQROS constitutional series, amendable by Rohith or explicit instruction, no constitutional session required. A hazard's `Status` field moves from `Open` to `Remediated` only when a dedicated, scoped piece of work (an Engineering Hazard Elimination Sprint, or an explicitly-authorised standalone fix) closes it — never silently, and never as an incidental side effect of an unrelated milestone.
