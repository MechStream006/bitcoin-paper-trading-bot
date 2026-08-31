# FB-12d — INC-0005 Readiness Tolerance: Governance Decision Record

**Date: 2026-08-31 (UTC) · Gate FB-12d · Read-only · Finding: Option B is
ALREADY supported end-to-end by existing machinery. No new governance contract
or schema field is required — the frozen, machine-readable gap declaration B
needs already exists and is already enforced. Recommendation: adopt B under
four conditions. The decision itself requires Rohith's approval.**

No code, no contract activation, no receipt, no registry/threshold/
pre-registration change, no TG-RDY or INC-0005 change, no production change,
no FB-12 implementation.

---

## 1. Frozen facts

**F1 — Contemporaneous readiness evidence exists for every day of every L2
window except the 19 INC-0005 days.** `load_daily_closes()` raises
`DataUnavailableError` when a day is absent, has fewer than
`MIN_DAILY_BARS = 1440` bars, **or when `len < min_rows`**, and writes no
`run_log` row. Measured on production: `g1_market_regime_sma200` 57/76,
`g3_momentum_tsmom` 57/76, `h5_vol_regime` 57/76, `ivrv_vrp_spread` 53/72,
`g3_atr_expansion` 44/63 — **19 missing in each, all INC-0005, zero others**.

**F2 — The gap is already declared as machine-readable, enforced data.**
`research/forward_evidence/run_date_policy.yaml`:

```yaml
immutable_gaps:
  - start: "2026-07-18"
    end:   "2026-08-05"
    reason: >-
      INC-0005 forward-evidence outage; the gap is immutable provenance and
      backfilling it would destroy the record that it happened
```

The file states its own purpose: *"These windows are DATA, not code… so a policy
change is a reviewable config change"*, and *"It has no other machine-readable
declaration anywhere in the repository — CLAUDE.md §20 and
docs/engineering/incidents/INC-0005-*.md are prose. **This is the first.**"*
`run_date_policy.py` enforces it as an **unconditional refusal**, evaluated
before any authorization flag, with stable machine-readable refusal codes.

**F3 — `_from_run_block` reads exactly three keys** — `days_total`,
`days_ready`, `days_non_ready` — and returns `ok = not days_non_ready`. Unknown
keys are ignored. It is explicitly *"read as recorded — the gate does not
recompute it."*

**F4 — `window_start` is immutable and spans the gap.**
`ledger_sync._first_run_date()` sets it to `MIN(run_date)`, *"the true start of
the forward observation window, which begins when collection begins"*. Every
future run for these families therefore also spans 2026-07-18…2026-08-05.

**F5 — Late receipts are structurally invisible.** `get_readiness` selects the
latest verdict with `as_of <= T`; the gate passes `as_of = run_ts`.

**F6 — The 19 days contribute nothing to any run metric.** They produced no
observations, so they are absent from the numerator *and* denominator of every
statistic a run records. The evidence pipeline already treats them as absence,
not as zero.

---

## 2. A vs B — the evidence

### Option A — Strict completeness

| | |
|---|---|
| Rule | any missing day in the window ⇒ NOT READY |
| Consequence for the five families | **permanently non-READY, on every run, forever** (F4) |
| Protective purpose | prevents deriving from a window whose data quality is unknown |
| Does that purpose apply here? | **No.** INC-0005 is *evidence of absence*, not absence of evidence: cause, exact dates and fail-closed behaviour are documented, and the job wrote **nothing** rather than something wrong. No corrupt or partial row entered the corpus |
| Cost | a permanent bar unrelated to signal quality, unfixable by any future evidence |

### Option B — Declared-gap tolerance

| requirement B states | already satisfied? |
|---|---|
| gap is immutable and explicitly declared | **Yes** — F2, and enforced unconditionally |
| never imputed or treated as READY evidence | **Yes** — no backfill exists or is permitted; the days stay absent |
| assessable days independently satisfy the completeness contract | **Yes** — F1: every non-gap day has a contemporaneous fail-closed `ok` record |
| gap remains visible and frozen in the readiness payload | representable additively (§4) |
| no retroactive receipt/probe may convert incident days to READY | **structurally guaranteed** — F5, plus FB-12c's demonstration that retroactive probing would invert FB-11 |

**Quantified exclusion under B**, stated plainly rather than minimised:

| family | window | excluded | **share excluded** |
|---|---|---|---|
| `g1_market_regime_sma200` | 76 | 19 | **25.0%** |
| `g3_momentum_tsmom` | 76 | 19 | **25.0%** |
| `h5_vol_regime` | 76 | 19 | **25.0%** |
| `ivrv_vrp_spread` | 72 | 19 | **26.4%** |
| `g3_atr_expansion` | 63 | 19 | **30.2%** |

**A quarter to nearly a third of each declared evidence window would be excluded
from readiness assessment.** That is not a rounding error, and it is the honest
cost of B. The mitigating fact is F6: those days are already absent from every
statistic the run reports, so excluding them from readiness aligns readiness
with how the rest of the pipeline already treats them, rather than creating a
new exception.

---

## 3. Can existing PR-7 machinery represent B? — Yes, entirely

This was the open question, and the answer is stronger than expected.

| B needs | mechanism | new artifact? |
|---|---|---|
| a frozen, non-discretionary exclusion set | **`run_date_policy.yaml immutable_gaps`** — exists, enforced, reviewable-as-config, versioned (`schema_version: 1`), holds a list so future gaps append | **None** |
| exclusion visible in the payload | additive keys (`days_excluded`, `exclusion_basis`) in the `readiness` block; `_from_run_block` ignores unknown keys (F3) | **None** |
| a passing verdict | INC-0005 days in `days_excluded`, so `days_non_ready` is empty and `ok = not non_ready` holds | **None — `readiness_gate` unmodified** |
| no new state | none added; the eight PR-7 states are untouched | **None** |

**The one residual hazard, and its fix.** `_from_run_block` trusts the block as
recorded — it cannot verify that `days_excluded` matches `immutable_gaps`. So B
must be implemented with the exclusion set **read from `run_date_policy.yaml`,
never producer-declared**, plus an invariant asserting no day outside
`immutable_gaps` is ever excluded. That is a test and a `report.py --check`
invariant, not a governance artifact.

---

## 4. `DegradationSpec` is the wrong mechanism — do not use it

Two independent disqualifications:

1. **It is state-scoped, not date-scoped.** `DegradationSpec.on: tuple[str, ...]`
   names *states*, and `spec.degrades_on(state)` tests membership. Declaring
   INC-0005 through it would mean *"whenever this input is missing, degrade"* —
   **permanently, for every future missing day**. That is exactly the
   over-broadening to avoid: it converts a closed, 19-day historical fact into
   an open-ended licence.
2. **It is for OPTIONAL inputs.** Its own docstring: *"`on` names the states for
   which this **OPTIONAL** input may be degraded rather than blocked."* The
   inputs INC-0005 affected are `required`.

The IVRV/DVOL precedent it encodes is a genuinely different situation: a
*recurring, expected* staleness in an optional input, declared in advance.
INC-0005 is a *singular, closed, historical* absence in a required one.
`immutable_gaps` is the right home; `DegradationSpec` is not.

---

## 5. Contract identity

**One readiness contract per family is the correct boundary.** A contract holds
`inputs: tuple[InputSpec, ...]` with worst-wins aggregation, so "one contract,
many inputs" is PR-7's intended shape; "many contracts, one feature" is not
modelled in the default path.

**`contract_id == family_id` should be frozen before generalization.** It makes
`engine.py:186`'s `feature = feature_id or contract.contract_id` fallback
resolve to what `readiness_gate` queries (`run["family_id"]`) with no framework
change. Two consequences must be accepted knowingly:

- **One contract per family, permanently.** Versioning still works —
  `contract_registry` versions by `(contract_id, version)` — but a family can
  never have two concurrent contracts.
- **Namespace sharing.** `contract_registry` is a single shared namespace, so
  family ids now reserve those contract ids against any future non-readiness
  contract of the same name.

Both are acceptable; neither should be discovered later.

---

## 6. The governance decision required

> **Does a day inside a declared, frozen, machine-readable `immutable_gap`
> count as non-READY (A), or as excluded-and-declared (B)?**

Nothing else in FB-12 is unresolved. This one answer determines whether a first
Validated family is reachable.

**Recommendation: B, on the evidence, subject to four conditions.** The
reasoning is not time-to-validation — it is that **A's protective purpose is
absent for these specific days while its cost is permanent**. Readiness exists
to prevent deriving from data of unknown quality; INC-0005's days are of
*known* quality (none was produced, nothing corrupt entered, and the failure is
documented to the day). A would impose an unreachable bar for a reason
unconnected to any family's signal quality, and no future evidence could ever
lift it.

**Conditions, all mandatory:**

1. The exclusion set is read from `run_date_policy.yaml immutable_gaps` and is
   **never producer-declared**.
2. Excluded days appear in the payload as `days_excluded` with an
   `exclusion_basis` naming the declaration; they are never silently dropped and
   never counted as ready.
3. An invariant (test + `report.py --check`) fails if any day outside
   `immutable_gaps` is excluded.
4. Adding a future entry to `immutable_gaps` is a governance act with its own
   incident record — tolerance is never claimed ad hoc.

**This is a recommendation, not a decision.** It changes what the Validated bar
means and requires Rohith's explicit approval.

---

## 7. Minimum implementation after approval

| # | change | blocked on §6? |
|---|---|---|
| 1 | rename `CONTRACT_ID` to the family id; test `get_readiness(db, family_id, day)` finds the verdict | No |
| 2 | parameterise `build_contract_doc()` per family, importing each family's `MIN_HISTORY` | No |
| 3 | readiness-block builder: read `run_log` presence for the window; emit `days_total` / `days_ready` / `days_non_ready` / `days_excluded` / `exclusion_basis`, with exclusions loaded from `run_date_policy` | **Yes** |
| 4 | wire the builder into `forward_snapshot.write_snapshot()` so the block is written when the run is created | **Yes** |
| 5 | invariant: no excluded day outside `immutable_gaps` | **Yes** |
| 6 | schedule the parquet probe daily, forward-only | No |

**No change to `readiness_gate`, `transition_governance`, PR-7, the eight
states, `thresholds.yaml`, the registry, any pre-registration, or INC-0005.**

---

## 8. Closing

**Frozen facts** — §1. **A vs B evidence** — §2. **Decision required** — §6.

**Can existing PR-7 machinery represent the decision?** **Yes, completely.** B
requires no new governance contract and no new schema field: the frozen gap
declaration already exists and is already enforced, and the readiness block
carries exclusions additively through keys `_from_run_block` ignores.

**Recommended next gate: FB-12e — implementation of items 1, 2 and 6 (safe
regardless of §6), and, if B is approved, items 3, 4 and 5.** If A is chosen
instead, items 3–5 are dropped, the five families are recorded as permanently
non-READY, and that consequence should itself be written into a durable record
so it is never rediscovered as a surprise.

**Stopped at the governance decision boundary.**

---

## 9. Trail

| Gate | Outcome |
|---|---|
| FB-12b | Do not activate; feature-id mismatch; receipt path unsatisfiable |
| FB-12c | Readiness is evidence-window-bounded; contemporaneous evidence exists; only INC-0005 blocks |
| **FB-12d** | **This record. B is fully supported by existing machinery; recommended under four conditions; decision pending** |
| FB-12e | Proposed implementation |
