# Contract Registry — Implemented Schema Reference (PR-2)

**Status:** Implemented (storage layer only — no activation, no retirement,
no receipts emitted yet).
**Design authority:** `contract_versioning.md` §2, §11 (PR-A). This document
describes the Python/SQLite implementation and where it *narrows* the PR-A
spec for this PR's explicit scope; it is a reference, not a second design
decision.

Module: `research/evidence_library/contract_registry.py`. Pure SQLite +
`receipts.py` reuse, no filesystem access to contract bodies at all.

---

## Scope note: how this differs from `contract_versioning.md` §11 PR-A

The approved spec's PR-A bundles the archive table together with
`activate()` / `supersede()` / `retire()`. This PR-2 task explicitly carves
those out ("Do NOT implement contract activation yet" / "Do NOT implement
contract retirement"), so this module implements **only the archive**:

- `append_contract()` is a raw, low-level insert — the caller supplies
  `phase` directly. There is no orchestration that computes a hash from a
  draft file, stamps a monotonic version, atomically supersedes an
  incumbent `active` row, or runs tamper detection. That is `activate()`'s
  job, deferred to a later PR.
- `phase` includes `draft` (not in the original PR-A DDL comment, which
  only lists `active | superseded | retired`) so this pure-archive registry
  can hold a contract at any declared lifecycle point without requiring
  the activation tool. Nothing in this module transitions a row between
  phases.
- The append-only triggers block **all** `UPDATE`/`DELETE`, with no
  phase-mutation exception. `contract_versioning.md` §11 documents `phase`
  as "the sole exception to append-only" for the eventual
  `activate()`/`supersede()`/`retire()` operations — that exception does
  not exist yet because those operations don't exist yet. **A later PR
  implementing activation must introduce that exception explicitly
  (CHECK-guarded, phase only advances) rather than silently loosening the
  trigger added here.**
- `activated_at` and `retired_at` are placeholder columns — always `NULL`
  in this PR. A later PR populates them.
- `version` is caller-supplied, not auto-assigned. Monotonic version
  stamping "by the activation tool, never hand-written" (§2.2) is
  activation's job.

Everything else — hashing, canonicalization, the metadata model, querying,
validation, and the "replay never reads the filesystem" guarantee — is
implemented per spec.

---

## Metadata model (`ContractRecord`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `contract_id` | str | yes | stable human identifier ("contract type"), e.g. `corpus_daily` |
| `version` | int | yes | human label; **not** auto-assigned in this PR |
| `contract_schema_version` | int | yes | version of the contract *format* itself |
| `body` | str \| dict | yes | the contract's content; canonicalized before hashing |
| `phase` | `draft`\|`active`\|`superseded`\|`retired` | yes | |
| `change_class` | `editorial`\|`relaxing`\|`tightening`\|`corrective`\|None | no | `None` pre-PR-C (no impact-report classifier yet) |
| `impact_report_hash` | str \| None | no | `None` until PR-C |
| `superseded_by` | str \| None | no | contract_hash of the successor, if any |
| `actor` | str | yes | |
| `reason` | str | yes, non-blank | |
| `created_at` | str | no | defaults to now (UTC ISO-8601) — when this row was appended |
| `activated_at` | str \| None | no | **placeholder**, always `None` in this PR |
| `retired_at` | str \| None | no | **placeholder**, always `None` in this PR |

`contract_hash` is **not** a `ContractRecord` field — it is computed by
`append_contract()` from the canonicalized body and returned in the stored
row. Callers never assert a hash.

## Canonicalization & hashing (items 4–5)

`canonicalize_contract_body(body: str | dict) -> str`:
- `str` input (e.g. literal working-copy YAML text) passes through as-is —
  it is already source-of-truth bytes.
- `dict` input is canonicalized via `receipts.canonical_json()` (sorted
  keys, no whitespace, raw floats forbidden) — reused verbatim from PR-1,
  not reimplemented.

`compute_contract_hash(body) -> str` = `receipts.sha256_lf(canonical_bytes)`
— the same LF-normalised SHA-256 convention as `pre_reg_guard._sha256_lf`
and PR-1's receipt hashing. One hashing rule on this platform, not two.
Bare 64-char hex, no algorithm prefix.

`verify_contract_hash(record) -> bool` recomputes and compares — the
hash-verification counterpart to PR-1's `verify_receipt()`.

## Validation (item 8)

`validate_record(record) -> list[str]`: required-field presence, `phase` ∈
allowed set, `change_class` ∈ allowed set (if set), non-blank
`actor`/`reason`, and — defense in depth — that the body actually
canonicalizes and hashes (catches e.g. a `dict` body containing a raw
float). `validate_record_or_raise()` raises `ContractValidationError` on any
error. `append_contract()` calls this before ever touching the database.

## Persistence — `contract_registry` table

Matches `contract_versioning.md` §11 PR-A's DDL, extended with `draft` in
the phase vocabulary and the `created_at`/`retired_at` columns discussed
above. `contract_hash` is the primary key (the archive's true identity);
`UNIQUE(contract_id, version)` prevents two rows claiming the same human
version; the partial unique index `ux_one_active_per_contract` enforces
"exactly one active version per contract_id" **at the database**, not in
Python, exactly as specified.

`append_contract(db_path, record)`:
1. Validates the record.
2. Canonicalizes the body and computes `contract_hash`.
3. Pre-checks for an existing row with that exact hash and raises
   `DuplicateContractError` if found — this is checked explicitly *before*
   the `INSERT`, because a byte-identical resubmission with the same
   `(contract_id, version)` would otherwise collide on two constraints at
   once, and SQLite only reports one `IntegrityError` message — message
   sniffing alone cannot reliably distinguish "duplicate hash" from "same
   `(contract_id, version)`, different body."
4. Inserts. A remaining `IntegrityError` (different body, same
   `(contract_id, version)`, or a second `active` row for the
   `contract_id`) is classified from the SQLite message into a clear
   `ContractRegistryError`.

Query functions (item 7): `get_by_hash`, `get_by_version`,
`get_by_contract_id` (all versions — "lookup by contract type"),
`get_active` (the current `active` row), `get_all_activated` (every
version that has ever been `active`/`superseded`/`retired` — "immutable
activated versions," plural, the historical set), `get_latest_draft`
(highest-version `draft` row), `get_all_contracts`.

## Replay support (item 9)

`resolve_by_hash(db_path, contract_hash) -> dict` is the **only** function
replay may use. It raises `ContractNotFoundError` — a hard failure, never a
fallback — if the hash does not resolve. There is no file-reading code
anywhere in this module (no `Path.read_text`, no `open()`, no YAML
parsing of a working-copy file); "replay never reads the filesystem" is
therefore a structural property of what this file imports and does, not a
convention that a stray call could quietly violate. Enforced by a test that
scans the module's own source for exactly those calls.

## What this PR deliberately does not do

- No `activate()` / `supersede()` / `retire()`. No monotonic version
  auto-assignment. No tamper detection (`dq/tamper.py`).
- No governance receipt emission (`contract.activate` / `contract.retire`).
- No impact report / change-class classifier (PR-C).
- No engine/check versioning (PR-B).
- No reprocessing policy (PR-D).
- No Evidence Ledger / Feature Store / Dataset wiring (PR-E).
- No CLI, no cron wiring.

---

## PR-2 review outcome (2026-07-13)

**Merge approved and completed.** Full suite green post-merge, no
regressions, no conflicts. One enhancement was explicitly carried forward
into PR-3 rather than implemented here:

> **PR-3 must add `activated_receipt_id` to every activated contract row** —
> a permanent reference to the governance receipt (PR-1's
> `governance_receipts`, `decision_type='contract.activate'`) that activated
> it. This strengthens lineage (a contract row points to *the decision that
> froze it*, not just to the fact that it is frozen) and gives `replay()` a
> direct join from contract to receipt instead of requiring a
> `subject_kind='contract', subject_id=contract_hash` scan.

This is additive to the schema (`contract_registry.activated_receipt_id`,
nullable, populated only by `activate()`) and requires no change to
anything built in PR-2 — `append_contract()` already accepts arbitrary
placeholder columns (`activated_at`, `retired_at`) that stay `NULL` until a
later PR populates them; `activated_receipt_id` joins that pattern.

---

## PR-3 update to this module (2026-07-13)

PR-3 (`docs/governance/contract_lifecycle_schema_reference.md`) modified
`contract_registry.py` in exactly the two ways this document said a later
PR would have to:

1. **`activated_receipt_id TEXT` column added** to `_SCHEMA_SQL`, plus a
   migration branch in `init_db()` (`PRAGMA table_info` check +
   `ALTER TABLE ... ADD COLUMN` if missing) so a pre-existing PR-2 database
   upgrades cleanly. Still a placeholder — PR-3's `activate_contract()`
   writes it as `NULL`; only PR-4 populates it.
2. **The blanket `trg_contracts_no_update` trigger was dropped** (by
   `init_db()`, for any database created under the PR-2 schema) **and
   replaced** with `trg_contracts_guard_update` — the documented "phase
   only advances" exception from `contract_versioning.md` §11, implemented
   as a `CONTRACT_TAMPER`-raising guard rather than a bare `CHECK` (SQLite
   trigger `WHEN`/`CASE` logic, since the legality of a phase transition
   depends on both `OLD` and `NEW` values, which a column-level `CHECK`
   cannot see). Full detail in `contract_lifecycle_schema_reference.md`.

`append_contract()`'s `INSERT` gained the `activated_receipt_id` column;
`ContractRecord` gained the corresponding field (default `None`). No other
PR-2 public interface changed. One PR-2 test
(`TestImmutabilityOfActivatedContracts::test_active_contract_cannot_be_updated`)
was updated to assert against a still-illegal operation (mutating `body`)
rather than `phase = 'retired'`, which the guarded trigger now legitimately
permits — this is a deliberate, documented behavior change, not a
regression; see that test's inline comment.

---

## PR-4 update to this module (2026-07-13)

PR-4 (`docs/governance/contract_receipts_schema_reference.md`) resolved the
receipt-lifecycle-status enhancement carried forward from the PR-2 review
and modified `contract_registry.py` further:

1. **`retired_receipt_id TEXT`** column added (mirrors `activated_receipt_id`
   for the retirement event).
2. **`activation_receipt_status TEXT` / `retirement_receipt_status TEXT`**
   added — an explicit enum (`RECEIPT_STATUSES = {PENDING_RECEIPT, RECORDED,
   LEGACY_IMPORT, BOOTSTRAP}`) replacing bare-`NULL` ambiguity on the
   `*_receipt_id` columns. `_SCHEMA_SQL` (fresh databases) enforces the full
   cross-column invariants via `CHECK` (non-draft rows require
   `activation_receipt_status`; retired rows require
   `retirement_receipt_status`; `RECORDED` requires a non-`NULL` receipt id;
   `LEGACY_IMPORT` requires a `NULL` one). `validate_record()` mirrors every
   one of these checks in Python with a clear message, so a malformed
   record fails before ever reaching the database.
3. **Migration asymmetry, discovered empirically and worth flagging**:
   SQLite validates a `CHECK` added via `ALTER TABLE ADD COLUMN` against
   *every existing row* immediately — confirmed by direct test — so the
   cross-column `CHECK`s above **cannot** be added this way to a database
   that already has non-draft rows (they would fail at migration time,
   before the backfill that would satisfy them ever runs). The migration
   path therefore adds these two columns with **only** their single-column
   enum-membership `CHECK` (satisfied trivially by `NULL`), then backfills
   `LEGACY_IMPORT` on every pre-existing non-draft/retired row. **A database
   that went through this migration path does not get the cross-column
   invariants enforced by SQLite** — only a freshly-created database does.
   `validate_record()` is the invariant's real enforcement point for a
   migrated database; this is a deliberate, documented trade-off (a full
   table-rebuild migration was considered and rejected as disproportionate
   risk for a platform with no live production data yet), not an oversight.
4. **`append_contract()` defaults `activation_receipt_status`/
   `retirement_receipt_status` to `PENDING_RECEIPT`** (never `LEGACY_IMPORT`
   — that value is reserved exclusively for the migration backfill) when a
   caller omits them for a non-draft/retired phase, so every existing PR-2
   test caller keeps working unchanged.
5. **The guard trigger's write-once rule for the two status columns was
   loosened to a two-state machine**: `NULL → anything` (unchanged), plus
   specifically `PENDING_RECEIPT → RECORDED` (the "store receipt reference"
   step of PR-4's atomic transaction). Every other transition, including
   `RECORDED`/`LEGACY_IMPORT`/`BOOTSTRAP` changing to anything else, is
   still `CONTRACT_TAMPER`.

See `contract_receipts_schema_reference.md` for the full receipt-status
semantics and the atomic transaction that writes these columns.
