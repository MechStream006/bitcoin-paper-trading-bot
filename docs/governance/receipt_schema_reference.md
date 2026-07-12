# Governance Receipt — Implemented Schema Reference (PR-1)

**Status:** Implemented (foundation only — nothing emits receipts yet).
**Design authority:** `governance_receipts.md`. This document describes the
Python/SQLite implementation of that design; it is a reference, not a second
design decision.

Modules:
- `research/evidence_library/receipts.py` — data model, canonicalisation,
  hashing, validation, replay-foundation interfaces. Pure, no I/O.
- `research/evidence_library/receipt_ledger.py` — SQLite persistence
  (append, query, chain verification).

---

## Body fields (`ReceiptBody`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `schema_version` | int | always `1` | bump only if the envelope shape changes |
| `decision_type` | str | yes | must be a key in `DECISION_TAXONOMY` |
| `decision_class` | `"derived"` \| `"discretionary"` | yes | must match `DECISION_TAXONOMY[decision_type]` |
| `action` | str | yes | one of `ACTIONS` |
| `subject` | `Subject{kind, id, version_or_hash}` | yes | `kind` ∈ `SUBJECT_KINDS` |
| `from_state` / `to_state` | str \| None | for transitions | |
| `as_of` | ISO-8601 str | yes | point-in-time coordinate — an input to the decision |
| `decided_at` | ISO-8601 str | yes | wall clock — hashed, but not a decision input |
| `actor` | `Actor{type, id, key_id}` | yes | `type` ∈ `{human, service}` |
| `authority` | `Authority{policy_id, policy_hash, role}` | yes | |
| `inputs` | tuple of `InputRef{kind, id, hash}` | no | content-addressed antecedents |
| `rule` | `RuleRef{rule_id, rule_hash, engine_version, thresholds_hash}` | no | required in practice for `derived` |
| `evaluation` | tuple of `EvaluationItem{check_id, observed, expected, passed}` | no | |
| `verdict` | `Verdict{result, reason_code}` | yes | |
| `reason_text` | str \| None | **required, non-blank, for `discretionary`** | |
| `prev_receipt_hash` | str \| None | assigned by the ledger at append time | `None` only for `seq=1` |
| `attestation` | `"bootstrap"` \| None | | if set, `attestation_note` is required |
| `attestation_note` | str \| None | required iff `attestation="bootstrap"` | |

All numeric values embedded anywhere in a body (e.g. inside `observed`,
`expected`, thresholds) **must** already be decimal strings — produced via
`decimal_str(value, places=None)` — before construction. `canonical_json()`
recursively rejects any raw Python `float` with `NonDecimalNumberError`.

## Canonicalisation

`canonical_json(obj) -> bytes`: `json.dumps(obj, sort_keys=True,
separators=(",", ":"))`, UTF-8, no trailing newline, floats forbidden.

`sha256_lf(data: bytes) -> str`: SHA-256 hex digest after CRLF→LF
normalisation — byte-identical convention to
`forward_evidence/pre_reg_guard.py::_sha256_lf`.

`compute_receipt_id(body: dict) -> str`: `sha256_lf(canonical_json(body))`.
Bare hex, 64 chars, no algorithm prefix — matches `contract_hash` / `fn_hash`
elsewhere in the codebase.

## Validation

`validate_body(body: ReceiptBody) -> list[str]` — returns errors, never
raises. `validate_body_or_raise(body)` raises `BodyValidationError` on any
error. Checks: taxonomy membership and class match, enum membership for
`action`/`subject.kind`/`actor.type`, required fields, ISO-8601 well-formed
timestamps, non-blank `reason_text` for discretionary receipts, bootstrap
attestation/note pairing, and that the body actually hashes (float check).

## Persistence — `governance_receipts` table

Matches `governance_receipts.md` §1.2 exactly (see `receipt_ledger.py`'s
`_SCHEMA_SQL`). `body_json` is the archive; every other column is a
projection for indexing. Append-only is enforced two ways:
1. No `update_receipt()` / `delete_receipt()` function exists.
2. `BEFORE UPDATE` / `BEFORE DELETE` triggers `RAISE(ABORT, ...)` — a raw SQL
   `UPDATE`/`DELETE` against the table raises `sqlite3.IntegrityError`.

`append_receipt(db_path, body)`:
1. Reads the current chain head (`MAX(seq)`) inside `BEGIN IMMEDIATE`.
2. Sets `body.prev_receipt_hash` = head's `receipt_id` (or `None` for the
   genesis receipt) and `seq` = head's `seq + 1` (or `1`).
3. Validates the resulting body (`validate_body_or_raise`).
4. Computes `receipt_id` over the now-chained body.
5. Inserts. A `receipt_id` collision (only possible for a byte-identical
   body) raises `DuplicateReceiptError`, not a bare `IntegrityError`.

Query functions: `get_receipt`, `get_by_seq`, `get_chain_head`,
`get_all_receipts`, `get_receipts_by_subject`, `get_receipts_by_type` — all
read-only, all return plain dicts with `body` and `signatures` pre-parsed
from their JSON columns.

`verify_chain(db_path) -> ChainVerificationResult{ok, n_receipts, errors}`:
walks the chain in `seq` order and checks (a) `seq` is gapless from 1, (b)
`receipt_id` recomputes from the stored body (tamper detection), (c)
`prev_receipt_hash` links to the prior row's `receipt_id`. This is the
`--verify-chain` primitive from `governance_receipts.md` §7 — not yet wired
to a CLI flag or a daily cron (later PR).

## Replay foundation (not yet wired to any engine)

- `verify_receipt(body, receipt_id) -> bool` — recomputes and compares.
  Needs nothing but the receipt itself; works offline, forever.
- `replay(body, rule_fn=None) -> ReplayResult` — dispatches on
  `attestation`/`decision_class`:
  - `attestation="bootstrap"` → `mode="refused"`, always.
  - `decision_class="discretionary"` → verifies the envelope only
    (`mode="envelope-verified"`); never recomputes the choice.
  - `decision_class="derived"` → requires an injected `rule_fn`; raises
    `NotImplementedError` if none is supplied (no such `rule_fn` exists on
    the platform yet — that is per-subsystem integration work).
- `fold_reconstruct(receipts, as_of) -> dict[(subject_kind, subject_id), body]`
  — generic last-write-wins fold, the reusable core that a later PR's
  per-cache `reconstruct()` (registry.yaml, contract phase, ...) will
  specialise.

## What this PR deliberately does not do

- No call site anywhere in the platform constructs or appends a receipt.
- No signing. `signature_json` exists as a column (default `'[]'`) but no
  key registry, no Ed25519 signing/verification code. Deferred — not in the
  PR-1 "Implement" list.
- No CLI (`--verify-chain` flag), no cron wiring, no anchoring (git/Telegram/
  backup-manifest head publication).
- No `state_transitions` / `contract_registry` / `data_readiness` migration
  or dual-write. Those tables are untouched.
