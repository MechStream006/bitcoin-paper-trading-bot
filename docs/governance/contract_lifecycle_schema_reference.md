# Contract Lifecycle — Implemented Schema Reference (PR-3)

**Status:** Implemented (activation + retirement orchestration; no receipt
emission, no impact-report classifier, no engine/check versioning).
**Design authority:** `contract_versioning.md` §2.1 (lifecycle), §2.2
(identity/versioning), §2.3 (tamper detection), §11 PR-A. This document
describes the Python/SQLite implementation; it is a reference, not a second
design decision.

Module: `research/evidence_library/contract_lifecycle.py`, built directly
on PR-2's `contract_registry.py` (which this PR also modifies — see
`contract_registry_schema_reference.md`'s "PR-3 update" section).

---

## Dependency check (per the PR-3 task's explicit stop condition)

The task instructed: *"If you discover that governance receipt emission
cannot be cleanly separated from lifecycle management, STOP implementation
and explain the dependency instead of expanding scope."*

It is cleanly separable. Activation and retirement are fully expressible as
atomic SQLite transactions against `contract_registry` alone:
`activate_contract()` writes `activated_receipt_id = NULL` (a placeholder
column PR-2 already added for this purpose); `retire_contract()` doesn't
touch it at all. Neither function constructs, validates, or references a
`governance_receipts` row, and neither imports `receipt_ledger.py`. PR-4
populates the placeholder later; nothing in this PR requires that to exist
first. Implementation proceeded without expanding scope.

---

## Lifecycle arcs

```
   draft ──activate──► active ──supersede──► superseded ──► retired
     │                   │                                      ▲
     │                   └──────────────retire──────────────────┘
```

```python
ALLOWED_TRANSITIONS = {
    "draft":      frozenset({"active"}),
    "active":     frozenset({"superseded", "retired"}),
    "superseded": frozenset({"retired"}),
    "retired":    frozenset(),
}
```

Mirrors the shape of `evidence_library/lifecycle.py`'s
`ALLOWED_TRANSITIONS`/`can_transition()` for family states — same pattern,
same discipline, applied to contracts. `can_transition(from_phase,
to_phase) -> bool` is a pure lookup, usable without a database connection
(tested directly).

**Two independent enforcement points**, deliberately not merged into one:
1. `contract_lifecycle.py` checks `can_transition()` in Python *before*
   issuing an `UPDATE`, so a caller gets a clear `InvalidTransitionError`
   with the attempted arc named.
2. `contract_registry.py`'s `trg_contracts_guard_update` trigger checks the
   same legality independently, at the database layer, for *any* caller —
   including raw SQL that bypasses this module entirely. This is what makes
   tamper detection (item 9) a structural guarantee rather than an
   API-usage convention.

---

## Activation (`activate_contract`)

```python
activate_contract(
    db_path, contract_id, body, actor, reason,
    contract_schema_version=1, change_class=None, impact_report_hash=None,
) -> dict
```

**Atomic transaction** (`BEGIN IMMEDIATE` — acquires a write lock
immediately, serializing concurrent activators for the same `contract_id`
rather than racing them):

1. **Validate draft** — structural validation via
   `contract_registry.validate_record()` (reused, not reimplemented) against
   a candidate record. Runs *before* `BEGIN IMMEDIATE`, so a doomed call
   never takes a lock or touches the database.
2. **Assign version** — `next_version(con, contract_id)` =
   `MAX(version WHERE contract_id=?) + 1` (or `1`), computed *inside* the
   transaction. Because SQLite serializes writers under `BEGIN IMMEDIATE`,
   two concurrent activators for the same `contract_id` can never compute
   the same version — the second's query only runs after the first commits
   or rolls back.
3. **Supersede previous active version, if any** — `UPDATE ... SET
   phase='superseded', superseded_by=<new hash> WHERE contract_hash=<old
   hash>`. Legality (`active -> superseded`) is checked before issuing this.
4. **Activate new version** — `INSERT` with `phase='active'`,
   `activated_at=<now>`.
5. **`activated_receipt_id` stays `NULL`** — the PR-4 placeholder.
6. **Commit.**

**If any step fails** — validation (pre-lock), an illegal supersede arc, or
a `sqlite3.IntegrityError` at `INSERT` (most commonly a duplicate
`contract_hash`, classified into `DuplicateContractError`) — the `except
Exception: con.rollback(); raise` wrapper around the whole transaction body
undoes **everything**, including an already-issued but not-yet-committed
supersede `UPDATE`. Verified directly: `TestAtomicRollback` plants a
`contract_hash` collision that only surfaces at the `INSERT` step (*after*
the supersede `UPDATE` has been issued in the same transaction) and asserts
the previously-active row is still `active`, unsuperseded, afterward.

`contract_hash` is computed here exactly as `contract_registry.append_contract()`
computes it — callers never supply one.

---

## Retirement (`retire_contract`)

```python
retire_contract(db_path, contract_hash, actor, reason) -> dict
```

Validates (item 5): `actor`/`reason` non-blank; `contract_hash` resolves;
the current phase has a legal arc to `retired` (`active` or `superseded` —
`draft` and already-`retired` are rejected with `InvalidTransitionError`).
Only `phase` and `retired_at` change; `superseded_by`, if already set from
an earlier supersede, is left untouched. A retired contract's bytes remain
permanently resolvable via `resolve_by_hash()` — retirement advances a
row's phase, it never removes or alters the row's content
(`contract_versioning.md` §7: "Bytes persist forever").

---

## Contract tamper detection (item 9)

`contract_registry.trg_contracts_guard_update` (`BEFORE UPDATE`) enforces,
at the database layer, independent of any Python code path:

1. **Frozen fields never change**: `contract_hash`, `contract_id`,
   `version`, `contract_schema_v`, `body`, `change_class`,
   `impact_report_hash`, `actor`, `reason`, `created_at`. Any attempted
   change raises `sqlite3.IntegrityError` with message
   `CONTRACT_TAMPER: immutable field changed`.
2. **Write-once lineage fields**: `superseded_by`, `activated_receipt_id`,
   `retired_at` may go from `NULL` to a value exactly once; a second change
   to an already-set value raises `CONTRACT_TAMPER: ... is write-once`.
3. **Only documented phase arcs**: any `phase` change not matching
   `draft→active`, `active→superseded`, `active→retired`, or
   `superseded→retired` raises
   `CONTRACT_TAMPER: illegal phase transition X -> Y`.

`DELETE` remains unconditionally blocked (`trg_contracts_no_delete`,
unchanged from PR-2).

This holds for **any** writer, not just `contract_lifecycle.py` — tested
directly with raw `sqlite3` `UPDATE` statements bypassing this module
entirely (`TestTamperDetection`), which is the point: tamper detection that
only fires through one blessed code path is not tamper detection.

---

## Replay compatibility (item 10)

Unchanged from PR-2, verified again here at each lifecycle stage:
`contract_registry.resolve_by_hash()` — the *only* function replay may
use — continues to resolve a contract's frozen bytes correctly whether the
row is currently `active`, `superseded`, or `retired`, and
`verify_contract_hash()` continues to hold at every stage. Nothing in
`contract_lifecycle.py` reads from the filesystem (checked by a
source-scanning test, matching PR-2's).

---

## What this PR deliberately does not do

- No governance receipt construction, validation, or emission.
  `activated_receipt_id` is written as `NULL` and never anything else in
  this PR — PR-4's job.
- No receipt signing, no anchoring.
- No impact report / change-class classifier (PR-C) — `change_class` and
  `impact_report_hash` are accepted as optional caller-supplied values and
  stored as-is; never computed or inferred here.
- No engine/check versioning (PR-B).
- No `pinned_run` / Evidence Ledger retirement-blocking (`contract_versioning.md`
  §7's "Retirement is blocked while any `pinned_run` depends on the
  contract"). `retire_contract()` does not query any evidence table.
- No Data Completeness Gate, Feature Store, or ML wiring.
- No CLI, no cron wiring.

---

## PR-3 review outcome (2026-07-13)

**Merge approved and completed.** Full suite green post-merge (517 passed,
same 14 pre-existing unrelated failures), no regressions, no conflicts. One
enhancement was explicitly carried forward into PR-4 rather than
implemented here:

> **PR-4 must introduce an explicit receipt-lifecycle status** (or
> equivalent invariant), because `activated_receipt_id IS NULL` on an
> `active` row is currently ambiguous between four distinct situations that
> demand different operator responses:
>
> - **legacy activation** — the row was activated under PR-3 (or earlier),
>   before PR-4 existed to populate this field. Expected and **permanent**;
>   never resolves to a value.
> - **pending receipt emission** — PR-4 exists and is wired, but the
>   receipt-write step for *this* activation has not yet run (or is
>   in-flight). Expected and **transient**; should resolve shortly.
> - **migration state** — a bulk backfill/bootstrap operation (mirroring
>   PR-1's `migrate_bootstrap_transitions.py` precedent) is populating
>   historical rows and this one hasn't been reached yet.
> - **implementation defect** — PR-4 is wired and should have written a
>   receipt for this activation, but didn't (a bug). This is the only case
>   that should page anyone.
>
> Today, all four look identical: `NULL`. A reviewer or an automated
> `--check` cannot tell "this is fine forever" from "this is broken right
> now" by looking at the column alone. PR-4 needs a companion field (e.g. a
> `receipt_status` enum, or a platform epoch/cutover marker distinguishing
> pre-PR-4 rows from post-PR-4 rows) so that `NULL` + status is
> unambiguous, and only the defect case is ever alertable.

This does not require any change to what PR-3 already shipped — every row
this PR ever writes falls cleanly into "legacy activation" once PR-4 lands,
and `activated_receipt_id`'s write-once-from-`NULL` guard (§ tamper
detection above) already accommodates a future single write with no
migration needed to the column itself.
