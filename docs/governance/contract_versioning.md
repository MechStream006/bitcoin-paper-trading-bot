# Data Completeness Contracts — Versioning & Lifecycle Specification

**Status:** Design. **PR-2 (Contract Registry, storage layer only) implemented
and merged 2026-07-13** — see `contract_registry_schema_reference.md` for the
implemented schema and this PR's deliberate scope narrowing (no
activate()/supersede()/retire(), no receipt emission). PR-3+ (activation
orchestration, engine/check versioning, impact report, reprocessing,
governance wiring) not yet implemented.
**Author:** Principal Data Platform Architect
**Date:** 2026-07-13
**Depends on:** `docs/governance/data_completeness_gate.md`,
`docs/governance/evidence_reporting_architecture.md`
**Precedent inherited:** `pre_reg_guard.verify_fn_hash()` — SHA-256 of LF-normalised
bytes, pinned in a registration record, verified on every run, mismatch is a hard
stop. **This document applies that exact discipline to completeness contracts.** A
contract governs what evidence is *admissible*; it deserves no weaker a lock than
the decision function whose output it admits.

---

## 0. Direct answers

| Question | Answer |
|---|---|
| **Should contracts be immutable?** | **Yes — once *activated*.** A contract has a `draft` phase in which it is freely editable and binds nothing. At activation its bytes are frozen forever and copied into an append-only registry. Editing an activated contract is `CONTRACT_TAMPER` → `HALT`, exactly as `FnHashViolation` is today. |
| **How are versions assigned?** | Two-layer identity. `version` = monotonic integer, **assigned by the activation tool, never hand-written** (two branches cannot both claim v4). `contract_hash` = SHA-256 of LF-normalised bytes — **this is the true identity**; the integer is a human label. |
| **How are historical runs tied to versions?** | By **hash, in the receipt** — never by version number, never by file path, never by date. `data_readiness.contract_hash` → `contract_registry.body`. Derived rows carry `receipt_id`. The chain is total. |
| **How are contract changes audited?** | `contract_registry` is append-only and stores the **full frozen text** of every activated version, plus actor, reason, change class, and a **mandatory replay-diff impact report**. Activation is a governance event with the same weight as a `state_transition`. |
| **When should a new version be created?** | On **any byte change**, without exception — there is no in-place edit. But *approval weight* is graded by empirically-measured change class (§4). And a change to the contract's **identity** (grain, or the set of things it `produces`) is a **new contract**, not a new version — the same rule as "a changed decision function is a new family, not a new run." |
| **How should replay choose the correct contract?** | **It doesn't choose. It dereferences.** The receipt names a hash; the registry returns those exact bytes. There is no lookup, no date-resolution, no ambiguity, no tie-break. Resolution-by-date is the trap; resolution-by-hash is exact. |

---

## 1. Why hash-addressing, not date-resolution

The intuitive design — *"look up which contract version was active on 2026-06-14"* —
is wrong, and it is worth being explicit about why, because it will be proposed.

Date resolution requires the system to answer *"what was active then?"* That question
has no stable answer:

- **Backfills invert the clocks.** A day derived on 2026-07-20 for period 2026-06-14
  has two dates. Which one selects the contract? Either choice is defensible, which
  means the choice is a bug waiting to be re-litigated by whoever next reads the code.
- **Activation intervals are mutable metadata.** If `active_from` is a column, then
  `active_from` can be edited, and history silently re-judges itself.
- **A git checkout changes history.** If the contract is read from `contracts/*.yaml`
  at replay time, then checking out an older commit changes what a 2026-06-14 receipt
  "means." The filesystem is not an archive.

Hash-addressing dissolves all three. The receipt written on 2026-06-14 recorded
`contract_hash=9f3c1a…`. Replay fetches exactly those bytes from an append-only
registry. **There is no question to get wrong.** The filesystem `contracts/` directory
is demoted to a *working copy*; the registry is the archive, and **replay never reads
the filesystem.**

Date resolution is still needed — but for exactly **one** operation, and it is not
replay. See §5.

---

## 2. Identity and immutability

### 2.1 Contract lifecycle

```
   draft ──activate──► active ──supersede──► superseded ──► retired
     │                   │                                      ▲
     │                   └──────────────retire──────────────────┘
     │
     └── freely editable, binds nothing, cannot appear in a receipt
```

| Phase | Editable? | May gate a new derivation? | May be dereferenced by replay? |
|---|---|---|---|
| `draft` | **Yes** | **No** — a draft contract in a live pipeline is `CONFIGURATION_ERROR` | No |
| `active` | **No — frozen** | Yes | Yes |
| `superseded` | No | **No** | **Yes — forever** |
| `retired` | No | **No** | **Yes — forever** |

**Exactly one `active` version per `contract_id` at any time.** Activation
atomically supersedes the incumbent. This is what makes fresh-derivation resolution
(§5) trivially deterministic: there is nothing to choose between.

**Nothing is ever pruned.** A superseded or retired contract's bytes remain in the
registry permanently, because a receipt from 2026-06-14 must remain dereferenceable
in 2031. **Registry storage is unbounded and that is correct** — a few kilobytes of
YAML per version, against the alternative of an unreplayable evidence corpus.

### 2.2 Three-part identity

```
contract_id     stable, human   e.g. "corpus_daily"        — the thing being governed
version         monotonic int   e.g. 3                     — a human label, for conversation
contract_hash   sha256(LF)      e.g. 9f3c1a…               — THE identity; what machines use
```

`contract_hash` is SHA-256 of the contract's bytes after CRLF→LF normalisation —
**byte-identical convention to `pre_reg_guard._sha256_lf()`**, deliberately, so the
platform has one hashing rule and not two. (This project is developed on Windows and
deployed on Linux; a line-ending-sensitive hash would fire spurious violations on
every deploy. That lesson is already paid for; inherit it.)

**Version numbers are assigned by the activation tool, not by the author.** An author
writing `version: 4` by hand in a YAML file on a branch, while a colleague does the
same on another branch, produces two v4s with different hashes — an ambiguity that
would silently corrupt every conversation about the system thereafter. The author
writes `version: draft`; the registry stamps the integer at activation.

### 2.3 Tamper detection

On every gate evaluation, the engine verifies that the working-copy file's hash
matches the registry's `active` hash for that `contract_id`. Mismatch ⇒
`CONTRACT_TAMPER` ⇒ **`HALT`**, recorded, alerting.

This is `FnHashViolation` for contracts, and it exists for the same reason: a change
to what "complete" *means*, made without going through activation, retroactively
alters the admissibility of evidence with **no trace**. That is precisely the class
of silent corruption the platform exists to prevent.

---

## 3. Immutability is necessary but **not sufficient**

Here is the trap that a naive contract-versioning design walks straight into.

A verdict is not a function of the contract alone:

```
Verdict = f( contract_hash , observations , as_of , ENGINE_SEMANTICS )
                                                    ^^^^^^^^^^^^^^^^^
```

Freeze the contract perfectly, and a maintainer can *still* change what
`coverage.min_count: 3` **means** by editing `checks.py` — say, changing whether the
count is inclusive of a boundary event. Every historical receipt then replays to a
**different verdict** while every hash still matches. Replay reports green. History
has silently changed underneath a perfect audit trail.

So contract immutability must be paired with **engine immutability**, by the same
mechanism:

1. **`engine_version`** — a semantic version of `research/dq/`, recorded in **every
   receipt** alongside `contract_hash`.
2. **Checks are frozen by `check_id`.** A check's semantics may **never** be
   redefined under an existing ID. A change in what a check *means* requires a **new
   `check_id`** (`coverage.events_per_day.v2`), which contracts then opt into
   explicitly by naming it. Old check IDs are retained and executable **forever**.
   Bug fixes to a check are the sole exception, and are themselves a governed act
   (§4, `corrective`) — because a bug fix *is* a semantics change to everything that
   previously ran through it.
3. **Replay pins the engine.** `replay(receipt)` must execute the check
   implementations named in the receipt at the recorded `engine_version`. If the
   current engine cannot honour a historical `check_id`, replay **fails loudly** — it
   never silently substitutes the modern check.

**Reproducibility guarantee, stated precisely:**

> `(contract_hash, engine_version, check_ids, observations, as_of) → verdict` is a
> pure, total, byte-stable function, replayable with **no access to the original
> sources**, forever.

Anything less than this and "immutable historical replay" is a slogan.

---

## 4. Change classes — measured, not declared

A version bump's approval weight is graded by its **impact on history**. The critical
design decision: the class is **computed empirically**, not asserted by the author.

**Activation requires a mandatory replay-diff impact report**: re-evaluate the
candidate contract against **every historical period** using each period's **stored
observations** (from the receipts — no source access needed, §3), and diff the
verdicts against what was originally recorded.

The author's opinion of their own change is not evidence. The diff is.

| Class | Empirical signature (over the full receipt history) | Approval | Retroactive effect |
|---|---|---|---|
| **`editorial`** | **Zero** verdict deltas. Comments, `owner`, `description`, key reordering. | Auto-approve. Still versioned, still hashed. | None. |
| **`relaxing`** | Some `non-READY → READY`. **No** `READY → non-READY`. | Review. | **None applied automatically** (see below). |
| **`tightening`** | Any `READY → non-READY`. | **Governance sign-off, with a per-family evidence-impact list.** | Questions existing evidence. |
| **`corrective`** | Deltas in both directions, *or* an engine/check bug fix. | **Governance sign-off.** | Both. The most dangerous class. |
| **`structural`** | Change to `grain` or to the set of `produces`. | **Not a version — a new `contract_id`.** | N/A. |

### 4.1 The relaxation attack, and the rule that stops it

`relaxing` looks like the safe class. It is the one that needs the sharpest rule.

Consider: an operator has evidence blocked by `BACKFILL_REQUIRED` days. They lower
`min_count` from 3 to 2. The blocked days become `READY`. The blocked evidence
becomes admissible. **No alarm fires** — the change was, by the empirical test,
purely relaxing, and nothing turned red.

That is p-hacking, performed against the data-quality layer instead of the signal
layer, and it would be entirely invisible to the Evidence Library.

> **Law V1 — Relaxation never retroactively re-admits.** Activating a new contract
> version **never** alters any existing receipt or any existing derived row. Historical
> verdicts are pinned to the hash they were rendered under. A relaxed contract applies
> to **future derivations only**.
>
> Re-judging history under a new contract is a **separate, explicit, governed act**
> (§6, reprocessing with `--contract-policy current`), which produces new receipts,
> preserves the old ones, and is recorded in the ledger with an actor and a reason.

This converts a silent re-admission into a signed one. The operator may still do it —
sometimes they *should*, and H6 is exactly that case (§6.2) — but they must **say so,
on the record, and the board will show it.**

### 4.2 Tightening

A `tightening` bump means: *"days we previously called complete, we now know were
not."* The impact report must enumerate, and governance must explicitly rule on:

- every derived row whose period would now be non-READY;
- every Evidence Library run whose window contains such a period;
- **every `pinned_run` so affected** — each must be either re-pinned, re-derived, or
  explicitly accepted with a recorded justification.

**A tightening bump may not be activated while it would silently invalidate a
`pinned_run`.** The activation tool refuses, and names the runs.

---

## 5. Resolution — three operations, three rules

This is the heart of the design. **Three different operations need a contract, and
they must resolve it by three different mechanisms.** Conflating them is the bug.

| Operation | Mechanism | Determinism |
|---|---|---|
| **Fresh derivation** (no receipt exists) | Resolve by `contract_id` → **the unique `active` version**. Not by date. Not by interval search. | Exactly one `active` version exists by construction (§2.1). Nothing to choose. |
| **Replay / audit** (receipt exists) | **Dereference** `receipt.contract_hash` → registry bytes. Plus `receipt.engine_version` → check implementations. | Total. No lookup, no ambiguity, no clock. |
| **Reprocessing / repair** (receipt exists, but we intend to re-derive) | **Explicit policy, mandatory, never defaulted.** | See below. |

### 5.1 Reprocessing policy — the one place a human must choose

```
--contract-policy pinned     use the historical contract_hash from the existing receipt
                             ⇒ reproduces the original verdict exactly
                             ⇒ USE WHEN: the contract was right and something ELSE was wrong
                                (a derivation bug, a lost row, an audit re-run)

--contract-policy current    use today's active contract
                             ⇒ deliberately re-judges history under new rules
                             ⇒ USE WHEN: the CONTRACT ITSELF was wrong
                             ⇒ requires: reason, actor, ledger record; emits new receipts;
                                the old receipts are retained, never deleted
```

**There is no default.** A reprocessing job invoked without `--contract-policy`
**refuses to run.** The distinction is too consequential to be inherited from a
config file, and the person running a repair at 2am is exactly the person who must be
made to state which one they mean.

### 5.2 H6 as the worked example

`repair_h6_partial_days.py` is a `--contract-policy current` operation, and naming it
that way clarifies what it actually did:

- The **old, implicit contract** was wrong. `HAVING COUNT(*) >= 2` admitted a day
  missing an entire funding event, and *nothing at all* enforced day-closure.
- The corrupt rows were, under that old contract, "correctly" derived. Replaying them
  with `--contract-policy pinned` would faithfully reproduce the corruption — which is
  precisely the *right* answer to the question *"what did we believe on 2026-07-12?"*
  and precisely the *wrong* basis for the corpus going forward.
- The repair re-judged history under the **corrected** contract (day-closure enforced,
  funding floor 3). That is a legitimate, deliberate, governed act — and under this
  framework it is **recorded as one**, with an actor, a reason, and a preserved trail
  of what the old verdicts had been.

Today that repair is a one-off script whose reasoning lives in a docstring. Under this
framework it is a first-class, audited platform operation — and the *next* one will
not need a bespoke script.

---

## 6. Migration policy

**M1 — Never mutate a derived row in place.** Reprocessing writes new receipts and new
rows; the atomic swap repoints `receipt_id`. The prior rows are retained (archived,
not deleted). Lineage is the one thing that must survive every operation.

**M2 — Never re-derive a period that recorded evidence depends on, without an explicit
unpin.** `repair_h6_partial_days.py` already refuses to run if any `trade_entries`
exist — *"silently rewriting the history behind a recorded trade is exactly the kind of
thing an evidence ledger must never do."* **Generalise that refusal to a platform law:**
a reprocess must refuse if the affected periods intersect the evidence window of any
`pinned_run` or any recorded trade, until a human explicitly unpins.

**M3 — Dry-run is mandatory, and it is the impact report.** Every reprocess emits the
same replay-diff artifact that activation does, before writing anything.

**M4 — Backup before write.** Inherited from the existing repair script's discipline.

**M5 — Migrations are themselves append-only events**, recorded with actor, reason,
contract policy, affected periods, and the impact-report hash.

---

## 7. Retirement

A contract is `retired` when its feature is no longer derived (superseded by a new
contract, or the feature is discontinued).

- **Bytes persist forever.** Retirement removes the contract from *fresh derivation*,
  never from *replay*. A retired contract that could not be dereferenced would render
  its entire historical corpus unreplayable — the precise opposite of the goal.
- **Retirement requires `superseded_by`** if a replacement exists — mirroring the
  Evidence Library's existing rule (*"a superseded family must be in 'retired'
  state"*, `lifecycle.validate_registry`). Consistency of governance vocabulary across
  the platform is worth more than local elegance.
- **A feature whose contract is retired with no successor is `frozen`**: existing
  evidence remains admissible under its original receipts; **no new days may be
  derived**. The readiness board must show frozen features explicitly — a feature that
  quietly stops collecting is the failure mode that costs a month.
- **Retirement is blocked** while any `pinned_run` depends on the contract *for a
  period not yet derived*. You may not retire the ground someone is standing on.

---

## 8. Interaction with the Evidence Ledger

**E1 — Receipts, not versions, are the join key.** A run's readiness block records the
**set** of `contract_hash`es spanned by its evidence window — not "the" version.

**E2 — Version-spanning windows must be visible.** An evidence window that spans a
contract-version boundary is **not** an error (contracts will legitimately improve
mid-collection), but the meaning of "complete" changed partway through it. The
governance board must flag it: `CONTRACT-SPAN`, listing the hashes. A reviewer
approving a promotion is entitled to know that the first half of the evidence was
admitted under a weaker standard than the second.

**E3 — A `pinned_run` may not span a `tightening` boundary without an explicit
governance acknowledgement.** A relaxing or editorial span is informational; a
tightening span means the run's early days would not qualify under the standard its
later days met. That must be adjudicated, not absorbed.

**E4 — Activation is a ledger event.** It carries actor, reason, change class, impact
report hash, and the affected-run list. It sits in the same audit substrate as
`state_transitions`, and appears in the evidence timeline. **A change to what
"complete" means is a governance act, and it will be visible on the same page as a
promotion.**

---

## 9. Interaction with the Feature Store

**F1 — A feature version is a triple, not a number:**

```
feature_version := ( contract_hash , fn_hash , engine_version )
```

All three are required. `fn_hash` alone (today's state) pins *what was computed*;
`contract_hash` pins *what it was permitted to be computed from*; `engine_version`
pins *what the permission meant*. A feature store that keys on only the first is
storing values whose admissibility it cannot reconstruct.

**F2 — Materialisations are namespaced by the triple and never overwritten in place.**
A contract bump creates a **new materialisation**, side by side with the old. Two
consumers pinned to different triples continue to see stable, unchanged values. This
is what makes a contract change **non-breaking for in-flight research** — the property
that would otherwise make every version bump a platform-wide event that nobody wants
to be responsible for, and which therefore never happens, and the contracts rot.

**F3 — Consumers pin the triple explicitly.** "Latest" is not a valid pin for anything
that will be cited as evidence — the same discipline, and for the same reason, as
`pinned_run` versus `MAX(run_ts)` in the reporting architecture. The platform has now
made this mistake once; it should not be available to make twice.

**F4 — Point-in-time reads are by `as_of` over receipts**, not by materialisation
mtime. The feature store answers *"what was the value of feature F for period P, as
known at time T?"* — and it answers from receipts, which record arrival times, so a
backfilled value **cannot** leak into a point-in-time read that precedes its arrival.
This is the property that keeps look-ahead out of every downstream training set.

---

## 10. Interaction with Dataset Engineering & ML

**D1 — A dataset manifest is a closure.** It records, for every feature and every
period: `feature_id`, the full `feature_version` triple, and the `receipt_id`. The
manifest hash is the dataset's identity.

**D2 — A dataset is reproducible iff every referenced hash resolves.** Guaranteed,
because the registry is append-only and never prunes. **This is the load-bearing
justification for unbounded registry retention** — the alternative is a training set
that can never be rebuilt, which makes every model built on it unauditable.

**D3 — A model card cites the dataset manifest hash.** From a deployed model, an
auditor can walk: model → dataset manifest → feature versions → contract hashes +
engine version → receipts → input observations → raw partition fingerprints. **The
lineage is total and mechanical.** No step requires anyone's memory, notes, or good
faith.

**D4 — A dataset containing a non-READY period is inadmissible for training**, unless
the degradation was declared in the contract and is recorded in the manifest. A model
trained on a `PARTIAL_DAY` is a model trained on look-ahead — the 00:05 UTC snapshot
that H6 wrote into `spot_close` is *tomorrow's information* from the perspective of the
signal that consumed it. That is the exact failure the whole evidence apparatus exists
to prevent, arriving through the data layer instead of the strategy layer.

**D5 — Contract changes never mutate an existing dataset.** They produce a new
manifest. Model reproducibility is preserved by construction.

---

## 11. Implementation specification for Sonnet

**Do not modify:** decision functions; `pre_reg_guard`'s hashing convention (**reuse
it verbatim** — one hashing rule on this platform, not two); `thresholds.yaml`;
`lifecycle.ALLOWED_TRANSITIONS`; any research logic.

Sequenced **after** `data_completeness_gate.md` **PR-1**, and **before its PR-2** —
the first real contract must be activated through the registry, never loaded from a
file, or the filesystem-as-archive habit sets in immediately and is very hard to
remove later.

---

### PR-A — Contract registry (append-only archive)

```sql
CREATE TABLE contract_registry (            -- append-only; never UPDATE, never DELETE
    contract_hash      TEXT PRIMARY KEY,    -- sha256(LF-normalised bytes) — THE identity
    contract_id        TEXT NOT NULL,
    version            INTEGER NOT NULL,    -- assigned by the tool, never hand-written
    contract_schema_v  INTEGER NOT NULL,    -- version of the CONTRACT FORMAT itself
    body               TEXT NOT NULL,       -- the FULL frozen text. This is the archive.
    phase              TEXT NOT NULL,       -- active | superseded | retired
    change_class       TEXT NOT NULL,       -- editorial|relaxing|tightening|corrective
    impact_report_hash TEXT,                -- NULL only for the first version of a contract
    superseded_by      TEXT REFERENCES contract_registry(contract_hash),
    actor              TEXT NOT NULL,
    reason             TEXT NOT NULL,
    activated_at       TEXT NOT NULL,
    UNIQUE (contract_id, version)
);
CREATE UNIQUE INDEX ux_one_active_per_contract
    ON contract_registry(contract_id) WHERE phase = 'active';   -- §2.1, enforced by the DB
```

`phase` is the **sole** exception to append-only (`active` → `superseded`/`retired`).
Every other column is write-once. **Justification must be recorded in the migration
notes** — a lifecycle pointer is not evidence, and the alternative (a separate
`contract_phase_transitions` table) is more faithful but adds a join to every
resolution; take the exception, document it, and enforce it with a `CHECK` that
`phase` only ever advances.

1. `dq/registry.py` — `activate()`, `get_by_hash()`, `get_active()`, `supersede()`,
   `retire()`. **`get_by_hash()` never touches the filesystem.**
2. `dq/hashing.py` — re-export `pre_reg_guard._sha256_lf`. Do not reimplement.
3. `dq/tamper.py` — working-copy hash vs registry `active` hash; mismatch ⇒
   `CONTRACT_TAMPER` ⇒ `HALT`. Called on **every** gate evaluation.
4. The partial unique index is the enforcement of "exactly one active version." Do
   not enforce it in Python.

**Tests:** activation assigns a monotonic version even under concurrent drafts; a
second `active` row for one `contract_id` is rejected **by the database**; tamper
detection fires on a one-byte edit; CRLF↔LF round-trip does **not** fire it (the
Windows-dev/Linux-deploy case — this test is not optional).

---

### PR-B — Engine & check versioning

1. `ENGINE_VERSION` in `dq/__init__.py`; recorded in **every** receipt.
2. `check_id` becomes explicit and versioned (`coverage.events_per_day.v1`).
   Contracts name check IDs. **A registry of check implementations, keyed by ID,
   retained forever.**
3. `replay(receipt)` executes the `check_id`s recorded **in that receipt**. If a
   recorded `check_id` is unavailable in the current engine, replay **fails loudly**
   — it never substitutes the modern check.
4. Receipts gain `engine_version` and `check_ids`.

**Tests:** the §3 attack — redefine a check's semantics under a new ID, prove old
receipts still replay to their **original** verdicts; prove a missing historical
`check_id` fails replay rather than silently substituting.

---

### PR-C — Impact report (the empirical change classifier)

`dq/impact.py`:

1. `compute_impact(candidate_contract) → ImpactReport` — replay the candidate against
   **every historical receipt's stored observations** (no source access, §3), diff the
   verdicts.
2. **Derive `change_class` from the diff**, not from the author. If the author's
   declared class disagrees with the measured one, **activation is refused** and both
   are shown. (Not a warning. A refusal. A misdeclared tightening is exactly the change
   that must not slip through.)
3. Enumerate affected: periods, derived rows, evidence runs, **`pinned_run`s**.
4. **Refuse activation of a `tightening`/`corrective` that would invalidate a
   `pinned_run`** — name them, require an explicit governance unpin first.
5. `ImpactReport` is content-hashed and stored; `contract_registry.impact_report_hash`
   references it.

**Tests:** relaxing / tightening / editorial / corrective each correctly *measured*
from a synthetic history; misdeclaration refused; `pinned_run` invalidation blocks
activation.

---

### PR-D — Resolution & reprocessing

1. `dq/resolve.py` — the three operations of §5, as three **distinct, separately-named
   functions**. There must be **no** function that "gets the contract" without the
   caller having chosen which of the three it means. Ambiguity at the call site is the
   defect this whole document exists to eliminate; do not reintroduce it in the API.
2. `--contract-policy {pinned,current}` on every reprocessing entry point. **No
   default; missing ⇒ refuse to run.**
3. Reprocessing: emits new receipts, retains old ones, records a ledger event with
   actor + reason + policy + impact hash.
4. Implement **M2**: refuse if affected periods intersect a `pinned_run` window or a
   recorded trade, absent an explicit unpin. Generalise the existing
   `repair_h6_partial_days.py` `trade_entries` guard — **do not** re-implement it
   locally a second time.

**Tests:** `pinned` replay of the real corrupt H6 rows reproduces the **original
(corrupt) verdicts** — this is correct and must be asserted, because it proves the
audit path is faithful rather than flattering; `current` re-judges them as non-READY;
missing policy refuses; M2 blocks a reprocess touching a pinned window.

---

### PR-E — Governance, feature store, dataset wiring

1. Receipts carry the full triple; `runs.metrics_json` readiness block records the
   `contract_hash` **set** spanned (E1).
2. `CONTRACT-SPAN` flag on the governance board; `tightening` spans block promotion
   pending acknowledgement (E2/E3).
3. Activation events appear in the evidence timeline (E4).
4. Feature-store materialisation namespacing by the triple (F2); `--as-of` reads over
   receipts (F4).
5. Dataset manifests record the closure; manifest hash is the dataset identity (D1–D3).
6. `report.py --check` gains:
   - every `contract_hash` referenced by any receipt **resolves** in the registry;
   - no `active` contract's working copy differs from its registry bytes;
   - exactly one `active` version per `contract_id`;
   - no `pinned_run` spans an unacknowledged `tightening` boundary;
   - every activation has an impact report, an actor, and a reason.

---

## 12. Acceptance criteria

1. Replay of any historical receipt reproduces its verdict **bit-for-bit**, with no
   filesystem access and no source access, **after** the contract has been superseded
   three times and the engine has been upgraded twice.
2. Editing an active contract file is detected on the next gate evaluation and halts.
3. A relaxing bump **cannot** silently re-admit previously-inadmissible evidence.
4. A tightening bump **cannot** silently invalidate a `pinned_run`.
5. Re-judging history requires a human to state, on the record, that that is what they
   are doing.
6. From a deployed model, an auditor reaches the raw input partitions in a finite
   number of mechanical dereferences, with no ambiguity at any step.
7. Two contract authors on two branches cannot produce two different `v4`s.

Criterion 5 is the one that matters. Everything else is machinery in service of it:
**the system must make it impossible to quietly change what "good data" means, and
then quietly benefit from having changed it.**
