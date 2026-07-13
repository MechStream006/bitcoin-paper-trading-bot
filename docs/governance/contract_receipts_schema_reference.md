# Contract Governance Receipt Emission — Implemented Schema Reference (PR-4)

**Status:** Implemented (contract.activate / contract.retire only — no other
decision type emitted, no signing, no anchoring, no reporting integration).
**Design authority:** `governance_receipts.md` §1–2 (receipt anatomy,
taxonomy), `contract_versioning.md` §11 (PR-A), `contract_lifecycle_schema_reference.md`
(the receipt-lifecycle-status carry-over this PR resolves). This document
describes the Python/SQLite implementation; it is a reference, not a second
design decision.

Module: `research/evidence_library/contract_receipts.py`, built on PR-1's
`receipt_ledger.py`, PR-2's `contract_registry.py`, and PR-3's
`contract_lifecycle.py`.

---

## Dependency check (per the task's explicit stop condition)

The task instructed: *"If you discover that another receipt type is
required, stop implementation and explain the dependency instead of
expanding scope."*

No other receipt type is required. Both `contract.activate` and
`contract.retire` were already present in `receipts.DECISION_TAXONOMY`
(added in PR-1, unused until now) and are both `discretionary` — no
`derived` verdict needs recomputing, and neither construction needs a prior
receipt as an input. The only optional input either receipt references is
the *previous contract version's hash* (for activation, when a predecessor
exists) — a contract, not a receipt. Implementation proceeded without
expanding scope.

---

## The atomic transaction, exactly as specified

```
BEGIN
  -> activate contract        contract_lifecycle._activate_on_connection(),
                               written with activation_receipt_status=
                               'PENDING_RECEIPT', activated_receipt_id=NULL
  -> generate receipt         _build_activate_receipt_body()
  -> validate receipt         receipts.validate_body_or_raise() -- inside
                               receipt_ledger.append_receipt_on_connection()
  -> persist receipt          receipt_ledger.append_receipt_on_connection(),
                               SAME connection/transaction
  -> store receipt reference  _store_activation_receipt_reference():
                               UPDATE activated_receipt_id=<real receipt_id>,
                               activation_receipt_status='RECORDED'
COMMIT
```

Retirement follows the identical shape with `_retire_on_connection()` /
`_build_retire_receipt_body()` / `_store_retirement_receipt_reference()`.

**Both the contract write and the receipt write share one `sqlite3`
connection and one `BEGIN IMMEDIATE` lock.** This is only possible because
`receipt_ledger.py` (PR-1) and `contract_lifecycle.py` (PR-3) were each
given a `*_on_connection()` primitive that assumes the caller owns the
transaction — see those modules' "PR-4 update" sections. `contract_receipts.init_db()`
initializes both schemas (`contract_registry` + `governance_receipts`) in
one SQLite file, which is required for this to be possible at all: SQLite
cannot commit atomically across two separate database files without a
heavier mechanism this PR does not introduce.

### If any step fails: rollback everything

The `try/except Exception: con.rollback(); raise` wrapper around the whole
transaction body means a failure at *any* step — contract validation
(pre-lock, so nothing is even attempted), the contract insert (e.g. a
duplicate `contract_hash`), receipt construction, receipt validation,
receipt persistence, or the final reference-storage `UPDATE` — undoes
everything already issued in that transaction, including steps that
appeared to succeed moments earlier. Verified directly by three tests that
force failure at three different points (`TestRollbackAndAtomicity`):
mid-generation (receipt persistence itself fails), on a second activation
where the supersede `UPDATE` had already been issued, and specifically at
the final reference-storage step (contract row and receipt row both already
written, uncommitted) — all three leave zero trace in either table.

---

## Receipt construction

`_build_activate_receipt_body()` / `_build_retire_receipt_body()` produce
the kwargs for `receipts.ReceiptBody`:

| Field | Activation | Retirement |
|---|---|---|
| `decision_type` | `contract.activate` | `contract.retire` |
| `decision_class` | `discretionary` (per `DECISION_TAXONOMY`) | `discretionary` |
| `action` | `activate` | `retire` |
| `subject` | `{kind: contract, id: <new contract_hash>, version_or_hash: <version>}` | `{kind: contract, id: <contract_hash>, version_or_hash: <version>}` |
| `from_state` / `to_state` | `draft` / `active` | `<prior phase: active\|superseded>` / `retired` |
| `actor` | `{type: human, id: <caller's actor>}` | same |
| `authority` | `{policy_id: contract_lifecycle.activate, policy_hash: <see below>}` | `{policy_id: contract_lifecycle.retire, ...}` |
| `inputs` | `[{kind: contract, id: contract_id, hash: <predecessor's hash>}]` if a predecessor was superseded, else `[]` | `[{kind: contract, id: contract_id, hash: contract_hash}]` |
| `rule` | `{rule_id: contract_lifecycle.ALLOWED_TRANSITIONS, rule_hash: <policy hash>, engine_version: contract_lifecycle.v1}` | same |
| `evaluation` | `[{check_id: lifecycle.transition_legal, observed: "draft->active", expected: "in ALLOWED_TRANSITIONS", passed: true}]` | `[{..., observed: "<from>->retired", ...}]` |
| `verdict` | `{result: activated}` | `{result: retired}` |
| `reason_text` | the caller's `reason` argument | the caller's `reason` argument |

**`authority.policy_hash` / `rule.rule_hash`** are the *same* real,
deterministic value: `sha256_lf(canonical_json({engine_version, allowed_transitions:
ALLOWED_TRANSITIONS}))`, computed once as a module constant from
`contract_lifecycle.ALLOWED_TRANSITIONS`. Not an invented placeholder — it
changes automatically, and correctly, if the lifecycle rules in code ever
change.

**Supersession is folded into the activation receipt, not a separate
event.** When activating a new version supersedes an incumbent, that fact
is recorded via `inputs`, referencing the superseded contract's hash. There
is no `contract.supersede` decision type — the taxonomy only defines
`contract.activate` / `contract.retire`, and the task's scope explicitly
excludes adding one.

---

## Receipt validation (item 6)

Performed entirely by `receipts.validate_body_or_raise()`, called inside
`receipt_ledger.append_receipt_on_connection()` — reused verbatim, not
reimplemented. This includes the taxonomy check (`decision_class` must
match `DECISION_TAXONOMY[decision_type]`), the non-blank `reason_text`
requirement for discretionary receipts, and the float-rejection check on
the canonicalized body. A validation failure raises inside the transaction
and is caught by `ReceiptEmissionError`, then rolled back by the outer
handler.

---

## Populate `activated_receipt_id` / `retired_receipt_id` (items 3–4)

Both are written in the **final** step of the atomic transaction
(`_store_activation_receipt_reference()` / `_store_retirement_receipt_reference()`),
after the receipt has already been persisted and its real `receipt_id` is
known. This requires the guard trigger to permit a
`PENDING_RECEIPT -> RECORDED` transition on `activation_receipt_status` /
`retirement_receipt_status` (a NULL→value write for the `*_receipt_id`
columns was already legal under PR-3's write-once-from-`NULL` rule; the
status columns needed a small, explicit extension — see
`contract_registry_schema_reference.md`'s PR-4 section).

Once written, both `*_receipt_id` columns are frozen (write-once) exactly
like every other lineage field — a second attempt to change either raises
`CONTRACT_TAMPER`, tested directly with raw SQL bypassing this module.

---

## Receipt lifecycle status (item 8)

`activation_receipt_status` / `retirement_receipt_status` ∈
`contract_registry.RECEIPT_STATUSES = {PENDING_RECEIPT, RECORDED,
LEGACY_IMPORT, BOOTSTRAP}`.

| Status | Written by | Meaning | Permanent? |
|---|---|---|---|
| `RECORDED` | `contract_receipts.py`'s governed functions, at the final transaction step | A real receipt was generated, validated, and persisted; `*_receipt_id` is non-`NULL` and resolves in `governance_receipts`. | Yes — write-once. |
| `PENDING_RECEIPT` | `contract_lifecycle.py`'s bare `activate_contract()` / `retire_contract()` (kept for backward compatibility), and transiently inside a governed transaction before the reference-storage step | No receipt was emitted for this event through this write path. Honest and non-committal — does **not** imply one will ever be attached. | Only observable at rest for bare/non-governed writes; never durably committed at rest for the governed path (atomicity means readers never see it mid-transaction). |
| `LEGACY_IMPORT` | **exclusively** `contract_registry.init_db()`'s one-time schema-migration backfill | The row predates this column's existence entirely — a historical fact about *when the schema changed*, not a governance decision. Never chosen by a live code path. | Yes — permanent, by construction. |
| `BOOTSTRAP` | *(reserved — not written by any code in this PR)* | For a future migration that manufactures a bootstrap-attested receipt (mirroring `receipts.ATTESTATION_BOOTSTRAP`) for a `LEGACY_IMPORT`/`PENDING_RECEIPT` row. | N/A yet. |

**No bare `NULL` is ever a resting state for a non-draft row** —
`activation_receipt_status IS NULL` only for `phase='draft'` rows (which
were never activated, so the question doesn't apply), enforced by a `CHECK`
on fresh databases and by `validate_record()` universally. The specific
ambiguity the PR-3 review flagged — *"is a `NULL` `*_receipt_id` legacy,
pending, mid-migration, or a bug?"* — is resolved: read the status column
first; `NULL` never has to be interpreted on its own again.

---

## Receipt replay verification (item 9)

`verify_activation_receipt(db_path, contract_hash)` /
`verify_retirement_receipt(db_path, contract_hash)` — both return a
`ContractReceiptVerification{contract_hash, event, receipt_id, hash_ok,
replay}`, built entirely from **PR-1's replay framework**, not
reimplemented:

- `hash_ok = receipts.verify_receipt(body, receipt_id)` — recomputes the
  receipt_id from the stored body and compares. Detects tampering.
- `replay = receipts.replay(body)` — since `contract.activate`/`.retire`
  are `discretionary`, this verifies the **envelope** only (`mode=
  "envelope-verified"`) and, correctly, never claims to recompute the
  choice itself — the same honest ceiling `governance_receipts.md` §0
  specifies for every discretionary decision on the platform.

Raises `ContractNotFoundError` for an unknown `contract_hash`, and
`ReceiptEmissionError` if the contract has no receipt to verify (status is
`PENDING_RECEIPT`/`LEGACY_IMPORT`/`BOOTSTRAP`) or if `*_receipt_id`
references a `receipt_id` that does not resolve in `governance_receipts` —
a **corrupted cross-table reference**, distinguished explicitly from a
merely-tampered receipt body (`hash_ok=False`) in the error message.

---

## Backward compatibility

`contract_lifecycle.activate_contract()` / `retire_contract()` (PR-3) are
**unchanged in behavior and signature** — refactored internally to share
logic with the new governed path, but every one of PR-3's 48 tests passes
unmodified. They now write `PENDING_RECEIPT` instead of leaving the status
columns `NULL` (those columns didn't exist before PR-4), which is additive,
not breaking. `append_contract()` (PR-2) similarly defaults the new status
columns for any caller that doesn't know about them.

---

## What this PR deliberately does not do

- No receipt type other than `contract.activate` / `contract.retire`.
- No signing (`signature_json` stays `'[]'`), no anchoring, no
  `--verify-chain` CLI/cron wiring.
- No `report.py --check` integration, no Evidence Ledger / Feature Store /
  reporting wiring.
- No `BOOTSTRAP`-status migration tooling (the value is reserved in the
  enum; nothing produces it in this PR).
- No `pinned_run` retirement-blocking (`contract_versioning.md` §7) — this
  module's `retire_contract_with_receipt()` does not query any evidence
  table, same scope boundary PR-3 held.
