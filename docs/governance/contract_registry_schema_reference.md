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
