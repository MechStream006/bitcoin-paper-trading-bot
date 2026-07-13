# Governance Receipt Architecture

**Status:** Design. **PR-1 (Governance Receipt Foundation) implemented and merged
2026-07-13** — see `receipt_schema_reference.md` for the implemented schema.
**PR-4 (2026-07-13, merged) wires the first two decision types into
production**: `contract.activate` and `contract.retire`, emitted atomically
alongside Contract Registry activation/retirement — see
`contract_receipts_schema_reference.md`.

**Governance Architecture PR-6 (2026-07-13, merged, including a
post-review architectural correction) wires two more**: `evidence.admit`
and `evidence.reject`, emitted by the Evidence Library
(`evidence_governance.py`) for a run's pass/fail against a replaceable
`AdmissibilityRule` (default provider: `CandidateGateRule`, wrapping
`lifecycle.check_candidate_gate()` with zero behavior change) — see
`evidence_library_governance_schema_reference.md`, including the explicit
scoping note on what these two receipt types do and do not attest (the
Data Completeness Gate's readiness-based admissibility rule does not exist
yet; these receipts attest gate-check outcome, not input-data
completeness). Replay resolves the exact historical rule implementation
via `get_rule(receipt.rule.rule_id, receipt.rule.engine_version)` and
fails loudly (`UnknownRuleError`) rather than silently substituting the
current default — the first real exercise of the `derived` replay path
(`rule_fn` injection) in this codebase, and the first receipt-emitting
module to honor `contract_versioning.md` §3's engine-pinning discipline.
`register_rule()` is the substitution point a future Data Completeness
Gate rule will use — PR-4's contract receipts are all `discretionary` and
never exercised any of this.

Still unemitted: `family.transition`, `family.promote`, `readiness.verdict`,
`feature.*`, `dataset.*`, `model.*`, `reversal`. No signing, no anchoring,
no `--verify-chain` CLI/cron.

**PR-5 must confirm/extend chain verification for contract receipts and
prepare for anchoring**: every `contract.activate`/`.retire` receipt PR-4
emits already goes through `receipt_ledger.append_receipt_on_connection()`
and therefore already carries `seq` and `prev_receipt_hash` in the single,
platform-wide chain — `receipt_ledger.verify_chain()` (PR-1) requires no
new code to walk them. What PR-5 must add is the **daily operational
surface** on top of that already-correct substrate: the `--verify-chain`
CLI/cron entry point, and the three zero-infrastructure anchors
(`governance_receipts.md` §3.3: git, Telegram, backup manifest) publishing
the chain head — without changing any receipt's contents, per the
established law that receipt bodies are frozen once written.
**Author:** Principal Data Platform / Governance Architect
**Date:** 2026-07-13
**Depends on:** `evidence_reporting_architecture.md`, `data_completeness_gate.md`,
`contract_versioning.md`
**Objective:** Every irreversible governance decision the platform makes can be
**reconstructed exactly, years later**, by someone who does not trust us and was not
there.

---

## 0. What a receipt is, and what it is not

An **audit log** records *that* a decision happened.
A **receipt** lets you **recompute the decision** — or, where the decision was not
computable, prove exactly what was known, what was permitted, and who chose.

That distinction is the entire design. A log entry saying
`2026-06-19  ivrv_vrp_spread  discovery → candidate  actor=operator` is worthless in
2031: it does not say what the numbers were, which thresholds were in force, which
code produced them, which data they were derived from, or whether that data was
complete. Every one of those is mutable, and every one of them has *already* changed
at least once on this platform.

A receipt closes over **all of it**, by hash.

### The one insight the design turns on

**Not all governance decisions are the same kind of object**, and conflating them
produces either fake rigour or despair.

| | **Derived decisions** | **Discretionary decisions** |
|---|---|---|
| Examples | gate verdict; evidence admissibility; candidate-gate pass/fail; contract change-class | family promotion; accepting a tightening; unpinning a run; retiring a model |
| Determined by | inputs + rule, totally | a human, within what the rule *permits* |
| Replay can | **recompute the verdict** and assert it matches | **verify the envelope** — preconditions held, actor was authorised, state was X — but **never** recompute the choice |
| A replay divergence means | **a defect** — the platform is not reproducible | nothing; there was nothing to recompute |

A promotion is **not** a computation. `check_validated_preconditions()` can only ever
say *"you are permitted to promote"* — it can never say *"you must."* Pretending
otherwise ("replay confirms the promotion was correct") is the most dangerous
possible failure of an audit system, because it manufactures false confidence in
precisely the decisions that most needed a human to be accountable for them.

So: a discretionary receipt has a **machine-verifiable envelope** and an
**irreducible human core**. Replay verifies the envelope with certainty and attests
the core immutably. The core — *"Rohith chose this, on this date, for this stated
reason, knowing exactly this"* — is not reproducible. It is **attestable**. That is
the honest ceiling, and the architecture should hit it exactly rather than overclaim.

---

## 1. The receipt

### 1.1 Anatomy

```
┌─ RECEIPT ────────────────────────────────────────────────────────────────┐
│                                                                          │
│  IDENTITY      receipt_id = H(canonical(BODY))     ← content-addressed   │
│                                                                          │
│ ┌─ BODY (immutable; every field is an input to receipt_id) ───────────┐  │
│ │                                                                     │  │
│ │  WHAT      decision_type, decision_class, action                    │  │
│ │  ON WHAT   subject      {kind, id, version_or_hash}                 │  │
│ │  FROM/TO   from_state, to_state                     (transitions)   │  │
│ │  WHEN      as_of        ← the point-in-time coordinate (an INPUT)   │  │
│ │            decided_at   ← wall clock (NOT an input to the decision) │  │
│ │  WHO       actor        {type, id, key_id}                          │  │
│ │  BY WHAT   authority    {policy_id, policy_hash, role}              │  │
│ │            RIGHT                                                    │  │
│ │  FROM      inputs[]     content-addressed refs — evidence runs,     │  │
│ │            WHAT         readiness receipts, contract hashes,        │  │
│ │                         dataset manifests, PRIOR RECEIPTS           │  │
│ │  UNDER     rule         {rule_id, rule_hash, engine_version,        │  │
│ │            WHAT RULE     thresholds_hash}                           │  │
│ │  SHOWING   evaluation[] {check_id, observed, expected, passed}      │  │
│ │            WHAT                                                     │  │
│ │  DECIDING  verdict      {result, reason_code}                       │  │
│ │            WHAT                                                     │  │
│ │  BECAUSE   reason_text  ← REQUIRED, non-empty, for discretionary    │  │
│ │  AFTER     prev_receipt_hash  ← the chain link                      │  │
│ └─────────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  SIGNATURE   {alg, key_id, sig(receipt_id)}   ← detached; see §3         │
└──────────────────────────────────────────────────────────────────────────┘
```

**The signature covers `receipt_id`, not the body.** This makes signatures
**detachable and additive**: a second party can countersign an existing receipt years
later without altering its identity. Sign-over-body would make every countersignature
a new receipt.

### 1.2 Storage

```sql
CREATE TABLE governance_receipts (       -- append-only. No UPDATE. No DELETE. Ever.
    receipt_id        TEXT PRIMARY KEY,  -- H(canonical(body)) — the identity
    seq               INTEGER NOT NULL UNIQUE,   -- chain position, monotonic
    prev_receipt_hash TEXT,              -- NULL only for the genesis receipt
    schema_version    INTEGER NOT NULL,
    decision_type     TEXT NOT NULL,     -- §2 taxonomy
    decision_class    TEXT NOT NULL,     -- derived | discretionary
    action            TEXT NOT NULL,     -- activate|retire|admit|reject|promote|
                                         -- demote|transition|reverse
    subject_kind      TEXT NOT NULL,     -- contract|family|evidence_run|feature|
                                         -- dataset|model
    subject_id        TEXT NOT NULL,
    subject_version   TEXT,              -- version or hash of the subject
    from_state        TEXT,
    to_state          TEXT,
    as_of             TEXT NOT NULL,     -- point-in-time coordinate (an INPUT)
    decided_at        TEXT NOT NULL,     -- wall clock (NOT an input)
    actor_type        TEXT NOT NULL,     -- human | service
    actor_id          TEXT NOT NULL,
    authority_hash    TEXT NOT NULL,     -- hash of the policy that permitted this
    rule_hash         TEXT,              -- NULL iff decision_class='discretionary'
                                         --   AND no preconditions apply (rare)
    engine_version    TEXT,
    body_json         TEXT NOT NULL,     -- THE canonical body. The archive.
    signature_json    TEXT,              -- [{alg, key_id, sig}] — 0..n, additive
    CHECK (decision_class IN ('derived','discretionary'))
);
CREATE INDEX ix_receipt_subject ON governance_receipts(subject_kind, subject_id, seq);
CREATE INDEX ix_receipt_type    ON governance_receipts(decision_type, seq);
```

`body_json` is the archive — the receipt is **self-contained**. Every other column is
a **projection of it, for indexing**. If every other table on the platform were
dropped, the receipt log alone would rebuild the platform's entire governance state
(§6). That is the design target, and it is testable.

### 1.3 Canonicalisation — where reproducibility actually dies

A content-addressed system lives or dies on byte-stable serialisation. Three rules,
all of them learned the hard way somewhere:

1. **Canonical JSON**: keys lexicographically sorted, no insignificant whitespace,
   UTF-8, no trailing newline. (RFC 8785 or a frozen local equivalent — but *frozen*,
   and versioned by `schema_version`.)
2. **LF normalisation** on any embedded text — **byte-identical to
   `pre_reg_guard._sha256_lf()`**. One hashing convention on this platform, not two.
   Windows dev, Linux deploy: this is not hypothetical.
3. **No IEEE floats in a receipt. Ever.** All numerics are **decimal strings with
   declared precision**: `"0.1646"`, not `0.16460000000000002`. A float's `repr`
   varies across Python versions, platforms, and libraries. `dsr_prob=0.1646` hashed
   as a float today and rehashed on a different interpreter in 2031 can yield a
   different hash — and the chain breaks with **no tampering having occurred**. This
   is a real, mundane, catastrophic bug and it is invisible until the day you need the
   audit to work.

Rule 3 is the single most likely reason a naive implementation of this document fails
in five years. It is worth more than the signatures.

---

## 2. Decision taxonomy

| `decision_type` | Class | Subject | `rule_hash` covers | Replay asserts |
|---|---|---|---|---|
| `contract.activate` | **discretionary** | contract | impact-report engine | measured change-class matches declared; no `pinned_run` invalidated; exactly one active |
| `contract.retire` | discretionary | contract | retirement policy | `superseded_by` present or feature frozen; no live dependency |
| `readiness.verdict` | **derived** | feature-period | `dq` engine + `check_ids` | **verdict reproduces bit-for-bit** |
| `evidence.admit` | **derived** | evidence run | admissibility rule | every input day READY/declared-degraded ⇒ admissible |
| `evidence.reject` | **derived** | evidence run | admissibility rule | the specific failing day/check reproduces |
| `family.transition` | discretionary | family | `lifecycle.ALLOWED_TRANSITIONS` | arc legal; preconditions held at `as_of` |
| `family.promote` | **discretionary** | family | `check_validated_preconditions` + `thresholds.yaml` | **preconditions were satisfied** — never that the promotion was *right* |
| `family.demote` | discretionary | family | lifecycle | arc legal |
| `feature.promote` / `.retire` | discretionary | feature | feature policy | version triple resolvable; consumers notified |
| `dataset.promote` / `.retire` | discretionary | dataset | dataset policy | manifest closure fully resolves |
| `model.promote` / `.retire` | discretionary | model | model policy | dataset manifest admissible; baseline beaten OOS |
| `reversal` | discretionary | *(the erroneous receipt)* | — | the reversed receipt exists and is not already reversed |

**Extensibility:** a new decision type is a new taxonomy row plus a rule hash. The
receipt schema does not change. `schema_version` bumps only when the *envelope* changes
— which should be approximately never.

**Note the asymmetry, deliberately:** `readiness.verdict`, `evidence.admit` and
`evidence.reject` are the **only** fully-derived decisions. Everything a human touches
is discretionary. That is the correct proportion, and any future design that claims to
have made promotion "derived" has smuggled a human's judgement into a threshold and
lost the accountability for it.

---

## 3. Signatures, chaining, anchoring — and the honest threat model

### 3.1 What each primitive actually buys

| Primitive | Protects against | Does **not** protect against |
|---|---|---|
| **Content-addressing** (`receipt_id = H(body)`) | Silent amendment of a receipt's contents | Deleting the receipt entirely |
| **Hash chain** (`prev_receipt_hash`, `seq`) | Deletion, reordering, insertion into history | An attacker rewriting the *whole chain* forward from the tampered point |
| **Anchoring** (§3.3) | Rewriting the whole chain — the head was published externally and cannot be un-published | Tampering *before* the next anchor |
| **Signature** (Ed25519) | Forging a decision as another actor | An attacker who holds the key |

**No layer protects against a host compromised at write time.** If the machine is
owned when the receipt is written, it writes whatever the attacker wants, correctly
signed. **This is unfixable by any scheme, and any design claiming otherwise is
selling something.** What this architecture guarantees is narrower and achievable:

> **Whatever was written cannot later be changed, deleted, or reordered without
> detection.**

That is the property an audit needs. State it plainly and stop.

### 3.2 Keys — proportionate, not cargo-culted

- **Human decisions** (all discretionary types): **Ed25519**, private key **offline**
  (not on the server, not in the repo, not in `.env` on the VPS), public key committed
  to the repo. Rohith signs promotions on the dev machine. This is the only class where
  **non-repudiation** genuinely matters — these are the decisions someone must own.
- **Service decisions** (`readiness.verdict`, `evidence.admit`): a **per-service**
  Ed25519 key on the server. Weaker guarantee, stated openly: it proves *which process*
  wrote the receipt, **not** that a host compromise didn't. Adequate, because these
  decisions are `derived` — and a derived receipt is **independently checkable by
  replay**, which needs no key at all. **Replay is the real integrity check for derived
  receipts; the signature is only provenance.**
- **No PKI, no CA, no HSM, no rotation ceremony.** A key registry file
  (`keys/registry.yaml`: `key_id → pubkey, owner, valid_from, valid_to`) is committed,
  append-only, and itself covered by the chain. This is proportionate to a solo-operated
  research platform. Building more would mean building something nobody maintains, and
  an unmaintained security control is worse than none because it is *believed*.

### 3.3 Anchoring — the cheap primitive that makes append-only *provable*

A hash chain in a SQLite file you control is append-only **by convention**. Anchoring
makes it append-only **by evidence**, and this platform already has three anchors it
isn't using:

1. **Git.** The daily chain head (`seq`, `receipt_id`) is committed to the repo and
   pushed. Git history is distributed and hard to rewrite unnoticed. **Cheapest, best.**
2. **Telegram.** The daily summary already posts to the private channel. Append the
   chain head. Telegram's servers timestamp it — a **third party** now holds evidence
   of what the head was on a given day, which is precisely what an anchor is for.
3. **Off-host backup manifests.** `scripts/backup.py` already writes manifests and
   supports `BACKUP_OFFHOST_CMD`. Include the head.

Three independent anchors, **zero new infrastructure**, all reusing what is already
built and running. Rewriting governance history would require simultaneously falsifying
a git remote, a Telegram channel, and an off-host backup — retroactively, consistently.
That is a real bar, cleared with a config change and a few lines in an existing job.

---

## 4. Provenance: the governance DAG

Receipts reference receipts. The result is a directed acyclic graph in which **every
governance decision names its antecedents by hash**.

```
   contract.activate ─────┐
   (corpus_daily v3)      │
                          ▼
                   readiness.verdict  ×N days ──┐
                   (feature-period)             │
                                                ▼
   contract.activate ──►  evidence.admit  ──────┼──► family.promote
   (dq engine v2)         (run 3f2a91c4)        │    (ivrv → validated)
                                                │           │
   readiness.verdict ×N ──────────────────────► │           │
                                                            ▼
                                                  dataset.promote ──► model.promote
```

**A `family.promote` receipt therefore transitively closes over** the evidence run, the
readiness verdict of every day in that run's window, the contract version under which
each of those days was judged, the engine version that judged them, the thresholds in
force, and the decision function's `fn_hash`. **Change any one of them and the promote
receipt's hash no longer resolves.**

That is what "reconstructed exactly, years later" means, mechanically. Not a promise —
a hash.

### Reversal, not deletion

Governance decisions are **irreversible in the ledger and reversible in effect**. A
mistake is never amended or removed. It is corrected by a **compensating receipt**
(`decision_type: reversal`) that names the erroneous receipt and states why. Both stand.
The DAG shows the error **and** the correction.

This is double-entry bookkeeping. It is not a limitation to work around; it is the
property that makes the ledger trustworthy — **a ledger you can quietly fix is a ledger
that tells you what someone wanted you to hear.**

---

## 5. Replay behaviour — three verbs, three guarantees

| Verb | Cost | Needs | Guarantee |
|---|---|---|---|
| **`verify(receipt)`** | µs | nothing but the receipt + chain | Recomputes `receipt_id` from body; checks signature against the key registry; checks `prev_receipt_hash` links to `seq-1`. **Detects any tampering. Works forever, offline, for every receipt, including bootstrap ones.** |
| **`replay(receipt)`** | ms–s | the referenced inputs (by hash) + the pinned `engine_version` | **`derived`:** re-executes `rule` at the recorded engine/check versions against the recorded inputs, and asserts `evaluation` + `verdict` reproduce **bit-for-bit**. Divergence ⇒ **defect, loud**. **`discretionary`:** re-evaluates only the **preconditions**; asserts they held. **Never claims the choice was correct.** |
| **`reconstruct(as_of)`** | s | the receipt log | Folds every receipt with `as_of ≤ T`, in `seq` order, into the **complete platform state at instant T**: which families in which states, which contracts active, which features/datasets/models promoted, which evidence admissible. |

### `reconstruct` is the whole point

**Platform state is a fold of the receipt log.** `registry.yaml`, `contract_registry.phase`,
the feature-store pointers, the model registry — **all of them are caches**. Every one is
a materialised view over the receipt stream.

This closes the loop with the reporting architecture, which already demoted
`registry.yaml` from *truth* to *validated cache*. This document supplies the thing it is
validated against.

`report.py --check` therefore gains its strongest invariant, the one that subsumes several
others:

> **`reconstruct(now)` == the current contents of every cache.**
> Any divergence is an unrecorded governance act — someone changed the platform without
> writing a receipt.

That single check detects: hand-edited YAML, a manual DB update, a script that promoted
without recording, a rolled-back deploy that left state behind. **It is the tripwire that
makes the whole apparatus real** rather than a set of files everyone agrees to respect.

---

## 6. Relationship to the existing ledgers

**Nothing is replaced. The receipt log becomes the spine; the existing tables become
indexed projections of it.**

| Existing | Becomes | Migration |
|---|---|---|
| `state_transitions` | A **projection** of `family.*` receipts. Keeps its shape and its consumers. Gains a `receipt_id` FK. | Every future `append_transition()` **must** write a receipt first, and carry its ID. A transition without a receipt is rejected. |
| `runs` / `gate_results` | Unchanged — they are **evidence**, not decisions. But `evidence.admit` / `.reject` receipts now reference runs by `run_id`, and admissibility moves **out of** the run row and **into** a receipt. | Additive. |
| `contract_registry` | The **archive of contract bytes**; its `phase` column becomes a projection of `contract.activate` / `.retire` receipts. | The `phase` mutation exception noted in `contract_versioning.md` §11 **disappears** — phase is now derived from receipts, and the registry becomes fully append-only. **This design retroactively removes the one impurity in that spec.** |
| `data_readiness` | Readiness receipts **are** `readiness.verdict` governance receipts. **Do not build two receipt systems.** `data_readiness` becomes a typed index over `decision_type='readiness.verdict'`. | Unify at implementation time; do not ship two parallel append-only stores. |

### Bootstrap honesty

The platform has **13 existing `state_transitions`** and **70 existing runs** that predate
receipts. They get **bootstrap receipts**, following the precedent already set by
`migrate_bootstrap_transitions.py` — and they are **explicitly marked**:

```
attestation: bootstrap        # NOT replayable
attestation_note: "Reconstructed 2026-07-13 from state_transitions + registry.yaml.
                   The inputs, rule versions, and evaluations that produced this
                   decision were not captured at the time and CANNOT be recovered.
                   verify() holds. replay() is unavailable and will refuse."
```

**`replay()` on a bootstrap receipt refuses. It does not approximate, and it does not
pass.**

An audit system that silently claims full reproducibility for history it never actually
captured is worse than one that admits the gap — because the false claim is *itself* the
thing an auditor is looking for. The chain starts clean from the genesis receipt forward.
Everything before it is attested, dated, and honestly labelled as unreconstructible.

---

## 7. Audit trail & reporting

- **`governance_receipts.md`** — the chain, newest first: `seq`, type, subject, actor,
  verdict, `receipt_id`, anchor status.
- **Per-subject DAG** — for any family / contract / dataset / model, the full antecedent
  closure of every decision ever made about it, rendered as a tree. This is the artifact a
  reviewer opens before a promotion, and the one an auditor opens in 2031.
- **Evidence timeline** (from the reporting spec) gains receipt IDs on every row — the two
  documents' audit surfaces converge into one.
- **`--verify-chain`** — walk every link, check every signature, confirm the head matches
  the latest anchor. **Runs daily, in CI and on the server.** A broken chain is a
  `CRITICAL` page.
- **Anchor status** — days since last anchor, and whether the anchored head still matches
  the local head. A local head that has *diverged from a published anchor* is the loudest
  possible alarm this system can raise.

---

## 8. Acceptance criteria

1. Every mutation to family state, contract phase, feature/dataset/model status, or
   evidence admissibility is **impossible** without writing a receipt — enforced at the
   write path, not by convention.
2. `verify()` succeeds on every receipt in the chain, offline, with no source data, forever.
3. `replay()` on any `derived` receipt reproduces its verdict **bit-for-bit** after the
   contract has been superseded and the engine upgraded.
4. `replay()` on a `discretionary` receipt reproduces the **preconditions** and **never**
   claims to reproduce the choice.
5. `replay()` on a bootstrap receipt **refuses**.
6. `reconstruct(T)` for any past `T` yields the exact platform state at `T` — and
   `reconstruct(now)` equals the live caches, or `--check` fails.
7. A single altered byte anywhere in the history is detected by `--verify-chain`.
8. From a promoted model, an auditor reaches every raw input partition through a finite
   chain of hash dereferences, with **no step requiring anyone's memory, notes, or good
   faith**.
9. No IEEE float is ever hashed.

Criterion 8 is the objective. Criterion 4 is the integrity. Criterion 9 is the one that
will actually break it.
