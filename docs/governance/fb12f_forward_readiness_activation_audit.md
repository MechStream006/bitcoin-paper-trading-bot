# FB-12f — Forward Readiness Activation Audit

**Date: 2026-08-31 (UTC) · Gate FB-12f · Read-only · Verdict: DO NOT ACTIVATE —
and the decisive reason is that activation is NOT REQUIRED. The run-block path
satisfies TG-RDY with no contract, no receipt and no ledger change at all.
Activation would permanently extend the production hash chain for no consumable
benefit.**

No code modified, no contract activated, no production receipt, no scheduler
wiring, no production write. Production state verified before and after.

---

## 1. Participating families and contract identities

`forward_readiness.FAMILIES`, seven, in `run_daily`'s own order, with
**`contract_id == family_id == feature_id`** (the FB-12d frozen identity, which
is what makes a verdict discoverable by `readiness_gate`'s
`feature_id = run["family_id"]` lookup):

`h5_vol_regime`, `g1_market_regime_sma200`, `g3_momentum_tsmom`,
`g1_trend_regime_ma_cross`, `ivrv_vrp_spread`, `g2_funding_level`,
`g3_atr_expansion`.

A test drift-guards this roster against `run_daily._CLOSE_ONLY_SIGNAL_IDS` plus
the three conditionals.

---

## 2. Activation mechanism, and whether it is required

**Mechanism**: PR-3's `contract_lifecycle.activate_contract(db_path,
contract_id, body, actor, reason)` — one `BEGIN IMMEDIATE` transaction that
validates the draft, assigns a version under lock, supersedes any incumbent and
inserts `phase='active'`.

**Required for the receipt path only.** `contract_loader.resolve_for_derivation`
resolves by `contract_id` to the unique ACTIVE version; with no active version
`evaluate_and_record` renders **`CONFIGURATION_ERROR`** and still writes a
receipt (fail-closed, verified at FB-12b).

**Not required for the run-block path.** `DefaultReadinessProvider` checks
`run["metrics"]["readiness"]` **first** and short-circuits; `_from_run_block`
touches no database. Verified: `assess()` against a **non-existent** database
returns `ok=True, source="run_block"`.

---

## 3. Exact operator command — and it does not exist yet

`forward_readiness.py` exposes **no `activate()`**. Its public surface is
`declared_gap_days`, `collected_days`, `build_contract_doc`, `evaluate_day`,
`build_readiness_block`, `assert_exclusions_declared`, `main`.

An operator would have to drive PR-3 directly, per family:

```python
import contract_registry, contract_lifecycle
from forward_evidence import forward_readiness as fr
contract_registry.init_db(LEDGER)                  # creates contract_registry
for fam in fr.FAMILIES:
    contract_lifecycle.activate_contract(
        LEDGER, contract_id=fam, body=fr.build_contract_doc(fam),
        actor="<person>", reason="FB-12f activation")
```

`h6_completeness.py` has an `activate()` helper; the generalized module does
not. **This asymmetry is a prerequisite gap, not a design decision** — recorded
in §10.

---

## 4. What a first production evaluation would write

| surface | production now | after activation + evaluation |
|---|---|---|
| ledger tables | **6** (`gate_results`, `governance_receipts`, `pairwise_r`, `runs`, `sqlite_sequence`, `state_transitions`) | **8** — adds `contract_registry` and `data_readiness` |
| `governance_receipts` | **27** | 27 + **7** `contract.activate` + **N** `readiness.verdict` |
| `contract_registry` rows | n/a | 7 (one active version per family) |
| `data_readiness` rows | n/a | one per evaluated (family, day) |
| affected dates | — | exactly the days the operator passes; gap days always skipped |
| affected families | — | exactly those activated |

For a single family over the current window, N = **57** (76 window days minus
19 declared-gap days). Across all seven, N is in the low hundreds.

**Yes — activation itself creates durable schema.** `contract_registry.init_db`
and `readiness.init_db` create tables in the governed ledger. This was
confirmed at FB-12b for evaluation and holds for activation.

---

## 5. Existing L2 rows and retroactive receipts

**L2 rows cannot be rewritten.** `forward_readiness` never opens `runs`; a test
asserts evaluation creates none of `runs`, `state_transitions`, `gate_results`,
`pairwise_r`. It only appends receipts and projection rows.

**On retroactivity, stated precisely rather than reassuringly.** Unlike
`h6_completeness`, `forward_readiness.evaluate_day()` has **no
current-period-only guard** — it accepts any past day except a declared gap.
That is deliberate and necessary: Option B is evidence-window-bounded, so
covering a window means evaluating its days. The protection is therefore not
refusal but two other properties:

1. **The verdict is sourced contemporaneously** — from `run_log` `status='ok'`,
   a record written on the day by a fail-closed collector — never from a
   present-day corpus probe (which FB-12c proved would invert FB-11).
2. **Late receipts are structurally invisible.** `get_readiness` selects the
   latest verdict with `as_of <= T`, and the gate passes `as_of = run_ts`, so a
   receipt written now cannot make an existing run READY. Test-proven.

**No existing L2 run can be retro-blessed.** But an operator *can* write
readiness receipts bearing past `period` values with a present `as_of`, and that
is intended behaviour, not a loophole.

---

## 6. INC-0005 handling — verified

| case | behaviour | verified |
|---|---|---|
| day inside `run_date_policy.yaml immutable_gaps` | excluded from the block with `exclusion_basis`; `evaluate_day` **raises** and writes no receipt; never counted ready | yes |
| day missing, **not** declared | lands in `days_non_ready`; `evaluate_day` yields `BACKFILL_REQUIRED`; provider returns `ok=False` | yes |
| forged exclusion | `assert_exclusions_declared` raises against the real policy | yes |

Exclusions are **read** from the frozen policy; the module cannot express one
that is not declared there.

---

## 7. Can the first evaluation satisfy TG-RDY? — Receipts: NO. Block: YES.

Simulated the **fullest possible activation** — contract active, every one of
the 57 evaluable days evaluated and `READY`:

```
RECEIPT path   -> ok=False  source=readiness_receipts  ready=57  non_ready=19
                  ['2026-07-18=NO_RECEIPT', '2026-07-19=NO_RECEIPT', ...]
RUN-BLOCK path -> ok=True   source=run_block
```

`_from_readiness_receipts` has no concept of exclusion: a day with no receipt is
`NO_RECEIPT`, and `_ADMISSIBLE_READINESS_STATES = {READY}`. **So the receipt
path can never satisfy a gap-spanning window, no matter how complete the
activation.**

**The run-block path satisfies it — and needs no activation, no receipts and no
database.** The block is computed from `run_log` at snapshot time.

**The only missing link is that nothing writes the block.**
`forward_snapshot.write_snapshot()` deliberately emits no readiness payload (its
docstring says so, written before a contract existed). Until that is wired, no
run carries a block, and TG-RDY blocks every family regardless of activation.

---

## 8. h6 — keep separate, unchanged

h6 is **not** in `FAMILIES` and should stay out:

- different collector (`daily_job`) and corpus (`forward_evidence.db`, keyed
  `h6_canonical`, not `signal_id`/`status`), so `collected_days()`'s `run_log`
  query does not apply;
- a different question — `h6_completeness` asks about **funding history depth**,
  `forward_readiness` asks about **collection-day presence**;
- 0 live-forward runs, so nothing would consume it;
- FB-11 already governs its historical window, and `h6_completeness` retains the
  current-period-only guard appropriate to it.

Keep both contracts defined; activate neither.

---

## 9. Cadence and run-date boundary

Readiness for day D requires D's `run_log` row, which `btcbot-fwd-hg` writes at
**01:00 UTC on D+1** for `run_date = yesterday UTC`
(`run_daily._derive_run_date()`).

So a continuous producer must run **daily, after 01:00 UTC**, evaluating
`D = yesterday UTC` — the same boundary the collector uses. A safe slot is
**01:30 UTC**, giving the collector 30 minutes and staying clear of the 02:00
backup and 02:20 ledger backup. It must never evaluate today (incomplete) nor
re-evaluate a declared gap (refused).

**But per §7 this cadence is optional**: it feeds only the receipt path, which
cannot satisfy a gap-spanning window anyway. It becomes valuable later, for
windows that begin after the gap.

---

## 10. Prerequisites FB-12e did not establish

1. **Production is 20 commits behind** (`4cf0923` vs `fdf91b9`).
   `forward_readiness.py` does not exist there. **Deployment must precede
   anything.** Mitigating evidence gathered here: none of the six new runtime
   modules (`observation_floors`, `forward_admissibility`, `forward_snapshot`,
   `h6_completeness`, `forward_readiness`, `probes.py`) is imported by any
   collector, and `transition_governance.py`'s change is **AST-identical with
   docstrings stripped** — so the deployment is behaviourally inert for the
   nightly job.
2. **No packaged activation command** (§3).
3. **No block wiring** into `forward_snapshot` — the actual missing link (§7).
4. **The governed ledger gains two tables** on first activation/evaluation (§4).
5. **No systemd unit or timer** for the producer.

---

## 11. Rollback and deactivation

| action | reversible? |
|---|---|
| contract activation | `contract_lifecycle.retire_contract()` advances phase to `retired`; **the row is never removed** and stays resolvable by hash |
| `contract.activate` receipts | **Permanent.** `governance_receipts` carries `RAISE(ABORT)` triggers forbidding UPDATE and DELETE |
| `readiness.verdict` receipts | **Permanent**, same triggers |
| new tables | remain (dropping them is not a supported operation) |

**Activation is a one-way door for the receipt chain.** Retirement stops future
evaluation; it cannot unwind what was written. Every receipt emitted becomes a
permanent part of the production governance chain — which is the point of the
chain, and precisely why it should not be extended speculatively.

---

## 12. Production state — read-only, verified

| | before | after |
|---|---|---|
| `ledger.db` sha256 | `b7085fbb…` | `b7085fbb…` |
| `evidence_forward.db` sha256 | `8b03bafe…` | `8b03bafe…` |
| ledger tables | 6 | 6 |
| `governance_receipts` | 27 | 27 |
| deployed commit | `4cf0923` | `4cf0923` |

---

## 13. Conclusions

**Activation prerequisites**: deploy `fdf91b9`; add a packaged activation
command; accept two new ledger tables; provision a timer — **and note that under
§7 none of this is on the critical path.**

**Expected production changes if activated**: +2 tables, +7 `contract.activate`
receipts, +N `readiness.verdict` receipts (57 per family per current window),
chain 27 → 27+7+N. All permanent.

**Risks**: permanent, unremovable chain growth; two new tables in the governed
store; a 20-commit deployment; and — the material one — **all of it buys nothing
for TG-RDY**, because the receipt path cannot clear a gap-spanning window.

**Is activation safe?** Technically low-risk and behaviourally inert for the
collectors. **But it is not warranted**, because it is not required: the
run-block path satisfies TG-RDY with no contract, no receipt and no ledger
change.

**Is scheduler wiring separately required?** For the receipt path, yes — daily
at ~01:30 UTC on `yesterday UTC`. For satisfying TG-RDY, **no**: the block is
computed from `run_log` whenever a snapshot is created.

**No governance approval is inferred here.** Wiring the block into
`forward_snapshot` changes what a *new* run records, and FB-12d's Option B
approval covers the readiness semantics but not the decision to embed the
payload into run creation. That remains yours.

---

## 14. Recommended next gate

**FB-12g — wire `build_readiness_block()` into `forward_snapshot.write_snapshot()`.**
Smallest change that makes readiness consumable: one call at run creation, the
`assert_exclusions_declared` invariant enforced before the row is written, tests
proving a new snapshot's block passes `DefaultReadinessProvider` and that
existing rows are untouched. **No contract activation, no receipts, no ledger
schema change, no scheduler.**

Deployment and activation should then be a **separate, later operational gate**,
justified on its own merits (forward-looking receipts for post-gap windows)
rather than bundled into the change that actually unblocks readiness.

**Stopped at the activation decision boundary.**

---

## 15. Trail

| Gate | Outcome |
|---|---|
| FB-12d | Option B approved; gap declaration already exists |
| FB-12e | Generalized producer, 49 tests, CI green (`fdf91b9`) |
| **FB-12f** | **This audit. Do not activate — activation is not required; the block path needs neither contract nor receipt** |
| FB-12g | Proposed: wire the block into `forward_snapshot` |
