# FB-11 (pre-gate) — Unevaluable-Window Governance Mechanism Audit

**Date: 2026-08-31 (UTC) · Read-only · Verdict: EXISTING mechanism is
sufficient. No new governance convention is required. The h6 interval is
`BACKFILL_REQUIRED` in PR-7's frozen vocabulary, and its downstream reach is
**provenance only**. FB-11 should be documentation-only.**

This is the audit the gate asked for. **The proposed FB-11 provenance record is
deliberately not written here.** Nothing was changed: PR-7, contracts, schemas,
h6, funding ingestion, the collector, registry, pre-registration, thresholds,
scheduler, ledger and production are all untouched. No backfill, interpolation,
synthetic evidence, promotion, or pairwise computation.

---

## 1. One correction to FB-10c-H6's framing

FB-10c-H6 characterised the 70 days as a **silent** failure. That overstates it.
The cause **was recorded** in production data: `regime_states.funding_regime =
'unknown'` on all 70 days, which is exactly how this audit reconstructed the
window.

The accurate characterisation is **recorded but not propagated**:

| layer | what it holds |
|---|---|
| `regime_states.funding_regime` | **`unknown`** — the truth, recorded daily |
| `signal_log.h6_canonical` | `0` — indistinguishable from a genuine flat |
| `run_log.status` / `anomalies_json` | `ok` / `[]` |

The information exists and no consumer reads it. That distinction matters
practically: **FB-11 does not need to create evidence about the past — the
evidence is already in production**, which is precisely why a documentation-only
record is sufficient.

---

## 2. Does an existing frozen mechanism cover this? Yes.

### 2.1 The state already exists: `BACKFILL_REQUIRED`

PR-7's eight severity-ordered states (`research/dq/states.py`) include, at
severity 4:

> **BACKFILL_REQUIRED** — "Identical symptom to INCOMPLETE_DATA, but the
> **late-arrival budget has expired**. This day will **never** self-heal. An
> explicit ingest action is required." Self-heals: **No.** Root cause lives
> with: **an operator / ingest job.**

That is a literal description of what happened. The funding coverage was below
the derivation's floor, it would never have healed by waiting, and it was
resolved by exactly one explicit operator ingest action — the backfill of 19
partitions on 2026-08-26 08:25–08:26.

### 2.2 The floor is expressible too: `ContinuitySpec.min_rows`

`ContinuitySpec(check, min_rows, no_interior_gaps)` already expresses a
window-depth requirement as **data**. h6's frozen `_funding_regime` requires
252 valid funding days within a 273-day window; `min_rows = 252` on the funding
input states that declaratively.

**Conclusion: no new state, no new schema, no new governance semantics.** The
vocabulary is adequate as frozen.

### 2.3 But it is a *fifth* failure class, not one of the documented four

`data_completeness_gate.md` §0 — "The lesson H6 actually taught" — enumerates
four failure classes from an **earlier, different** h6 incident:

| class | that incident |
|---|---|
| F1 temporal incompleteness | `daily_job` read `corpus.iloc[-1]` at 00:05 UTC — today, five minutes old |
| F2 coverage incompleteness | 1 of 3 funding events fired; `HAVING COUNT(*) >= 2` dropped the day; **the derivation consumed the NULL as a value** |
| F3 latched corruption | `INSERT OR IGNORE` + day-exists idempotency froze a partial day forever |
| F4 silent schema drift | `rv_21d`, `atr14`, `atr_ratio` declared but never written |

**All four concern a single day's own data. The 2026 window concerns the depth
of history behind each day.** Every individual funding day that existed was
complete and correct; there were simply not yet 252 of them. That is a
**warmup/window-depth insufficiency**, and it is not in the table.

This is worth recording because it is a genuinely new manifestation — but it
needs **no new state**, since `BACKFILL_REQUIRED` already describes both the
symptom and the remedy. It is a gap in the *documented examples*, not in the
*vocabulary*.

### 2.4 The mechanism is unadopted, so reuse is prospective only

PR-7 has **no adopters** (CLAUDE.md §17; the design doc's §12 PR-2 — "The H6
pipeline adopts it (the proof)" — is unimplemented). So the framework can
*prevent recurrence* once adopted, but it cannot *retrospectively record* this
window, and it should not be made to:

- **PR-7's own L4** — "a non-READY verdict must never produce a durable row that
  a later run treats as done." Emitting 70 retroactive receipts would
  manufacture governance rows for days nobody evaluated.
- The design doc reserves a **`NO_RECEIPT`** count for exactly "days nobody
  evaluated — the H6 blind spot." Retro-emitting receipts would destroy that
  signal.

**Therefore: recording the historical interval is a documentation act; adopting
PR-7 is a separate engineering act.**

---

## 3. Exact classification of the 70-day interval

| field | value |
|---|---|
| Interval | **2026-06-16 … 2026-08-26 inclusive** |
| Collected days in interval | **70** (of 75 total; 2 further days absent at month rollover — 2026-07-01, 2026-08-01) |
| State (PR-7 vocabulary) | **`BACKFILL_REQUIRED`** on the `funding` input |
| Action (PR-7 policy) | **`BLOCK`** — a required input that will not self-heal |
| Failure class | **Window-depth / warmup insufficiency** — a fifth class beyond F1–F4 |
| Nature | **Unevaluable**, not degraded and not merely incomplete |
| Resolved by | Operator ingest action, 2026-08-26 08:25–08:26 |
| First evaluable day | **2026-08-27** |

**Unevaluable, not degraded.** "Degraded" implies a declared-in-advance fallback
producing a reduced-but-honest output (`DegradationSpec`). h6 declared no
degradation path and produced an output **indistinguishable from a real one**.
The verdict was never computed — so `unevaluable` is exact.

---

## 4. Downstream effects — provenance only, today

| consumer | affected? | evidence |
|---|---|---|
| **Observation floors** (FB-7) | **No** | h6's pre-registration declares exactly one floor: `promotion_criteria.minimum_trades = 30`. It counts **trades**, not calendar days or active days. h6 has 0 trades in and out of the interval, so no floor count changes |
| **Readiness** (PR-9) | **No** | h6 has no readiness contract; PR-7 is unadopted and FB-9b deferred (Option C) |
| **Forward admissibility** (FB-8) | **No** | requires an L2 run. h6 has **2 `offline-corpus` runs and 0 `live-forward`** in the production ledger |
| **DSR / BH** | **No** | requires returns; h6 has none |
| **Pairwise** | **Only as a future boundary** | any future h6 forward-return series must begin no earlier than **2026-08-27**; under FB-10c-D's complete-case alignment the interval contributes nothing regardless |
| **Provenance** | **Yes — this is the whole effect** | 70 rows assert `h6_canonical = 0` for days on which the signal was never determined |

**The reach is materially narrower than FB-10c-H6 implied.** That gate warned
the days could "silently absorb" into observation floors; h6's only declared
floor is trade-count, so they cannot. The risk is real but confined: it is that
a **future** reader treats 70 rows of `0` as evidence that h6 was flat, or
counts them as observation time in an analysis h6 has not yet declared.

---

## 5. Can future accumulation ever legitimately absorb those dates?

**No — permanently excluded from forward evidence.** Two independent reasons:

1. **The signal was never evaluated on those days.** No later event changes what
   was or was not computed then.
2. **Recomputation is now technically possible but lane-violating.** Since the
   2026-08-26 backfill, the inputs to recompute h6 for 2026-06-16 … 2026-08-26
   all exist. Doing so would produce **retrospectively computed** values, which
   are not `live-forward` by definition. They could at best be
   `offline-corpus` evidence in a different lane — never L2 — and writing them
   into the forward corpus would be precisely the backfill CLAUDE.md §20
   forbids.

**That temptation is new and specific to this case**, and is the single most
important thing for FB-11 to foreclose: unlike INC-0005 (where no inputs exist
to backfill from), here the inputs *do* now exist, so "we could just recompute
it" is an available and wrong action.

---

## 6. Does the system distinguish "evaluated → zero" from "could not evaluate"?

**As a convention: yes, twice, and both are frozen. As implemented in this
collector: no.**

| precedent | how unavailability is expressed | status |
|---|---|---|
| **IVRV / DVOL** (CLAUDE.md §16; `DegradationSpec` docstring) | *"a stale/missing DVOL day yields ivrv `unavailable` (fail-safe skip)"*; `outputs_unavailable` marked **UNAVAILABLE** — *"Never NULL, never imputed, never ffilled, never zero-filled"* | **Frozen convention** |
| **FB-7 observation floors** | `MET` / `NOT_MET` / **`UNEVALUABLE`** — "declared but cannot be computed from the available evidence" | **Implemented**, at floor level |
| **PR-7 DQ** | 8 states; `UNKNOWN` never a pass; "empty is not READY" | **Built, 187 tests, no adopters** |
| **h6 collector** | writes `h6_canonical = 0` | **Neither** |

Empirically, **no signal in the forward corpus writes a distinguishable
unavailable marker** — `position IS NULL` count is **0 for all seven** collecting
families. IVRV's declared behaviour is to *skip the day* (absence), which is at
least distinguishable; h6 writes a value.

---

## 7. Is `status='ok'` with `h6_canonical=0` itself a defect?

**Yes — a data-quality defect, measured against the platform's own frozen
precedent, though not a trading defect.**

`DegradationSpec`'s frozen docstring requires unavailable outputs be marked
explicitly and **"never zero-filled."** Writing `0` for an output that could not
be computed is zero-filling an unavailable output. It is F2's exact descendant —
*"the derivation then consumed the NULL as if it were a value"* — one level up:
here the derivation consumed **absent history** and emitted a real-looking flat.

Severity is bounded and should be stated as such: no trading decision was
affected (paper trading; h6 has no trades, no positions, no L2 runs), and the
cause was recorded in `regime_states`. The defect is that **70 rows assert a
signal state that was never determined**, and nothing downstream can tell.

---

## 8. Comparison — h6 vs INC-0005 vs IVRV/DVOL

| | **INC-0005** | **IVRV / DVOL** | **h6 window** |
|---|---|---|---|
| What failed | the job itself | an optional input | a required input's **history depth** |
| Record left | **no rows** — clean absence | day skipped / marked unavailable | **75 rows that look like evidence** |
| Detectability | **loud** — 19 unit failures | explicit, by design | recorded in `regime_states`, **not propagated** |
| Declared in advance? | no | **yes** — `DegradationSpec` | **no** |
| Self-heals | no | yes, next fresh day | no — needed an operator backfill |
| Governance treatment | **CLAUDE.md §20**, immutable, no backfill | frozen degradation precedent | **none yet** |

**Analogous to INC-0005:** immutable; must be carried explicitly; no backfill
permitted; it will reappear in every future recount.

**NOT analogous to INC-0005 — and this is the crux:** INC-0005 produced a
**clean absence**, which is self-evident and impossible to misread. The h6
window produced **false presence** — rows that read as ordinary flat days.
Absence announces itself; a plausible-looking zero does not. By that measure the
h6 window is the *more* dangerous of the two, despite being far smaller and
having caused no operational impact.

**Analogous to IVRV:** an input dependency unavailable, non-fatal to other
signals.

**NOT analogous to IVRV:** IVRV's unavailability was **declared in advance** and
surfaces as an explicit non-value. h6's was undeclared and surfaced as `0`.

---

## 9. Answers to the gate's five closing questions

**1. Existing mechanism reusable, or new one required?**
**Reusable — no new governance convention is required.** `BACKFILL_REQUIRED`
plus `ContinuitySpec.min_rows` already express both the state and the floor.
The only genuine novelty is that this is a **window-depth** failure rather than
one of the four documented per-day classes, which is a gap in PR-7's *examples*,
not its *vocabulary*.

**2. Exact classification.** 2026-06-16 … 2026-08-26 inclusive, 70 collected
days, **`BACKFILL_REQUIRED`** on the funding input → action **`BLOCK`**;
**unevaluable**, not degraded, not merely incomplete; resolved by operator
backfill 2026-08-26; first evaluable day 2026-08-27.

**3. Downstream effects.** **Provenance only.** Observation floors, readiness,
forward admissibility and DSR/BH are all unaffected today — h6's sole floor is
`minimum_trades = 30`, and h6 has 0 trades, 0 L2 runs and no readiness contract.
Pairwise is affected only as a future window boundary (no h6 series may start
before 2026-08-27). The dates must be **permanently excluded** from forward
evidence, and recomputation — now technically possible — would be lane-violating.

**4. Should FB-11 be documentation-only?** **Yes.** The delimiting evidence
already exists in production (`regime_states.funding_regime`), the state
vocabulary already exists frozen, and PR-7's own L4 and `NO_RECEIPT` design
forbid retro-emitting receipts. A durable §20-style record citing existing
evidence is both sufficient and the maximum that is safe.

**5. Smallest next gate.** **FB-11 — h6 unevaluable-window provenance record,
documentation-only.** It should: name the interval and the 70 days; classify it
as `BACKFILL_REQUIRED` in PR-7's existing vocabulary; cite
`regime_states.funding_regime` and the 2026-08-26 partition timestamps as
evidence; state that the days are permanently excluded from forward evidence;
and explicitly forbid recomputation-as-backfill, which is the one new and
available wrong action. No code, no schema, no PR-7 change, no backfill.

---

## 10. Separately, not part of FB-11

- **Adopt PR-7 for the forward collectors** (its own §12 PR-2, "the proof"),
  which would make this class structural rather than reconstructable. The
  window-depth case should be added to its examples when that happens.
- **Month-rollover collection loss** — 2026-07-01 and 2026-08-01 absent; recurs
  2026-09-01 without a fix.
- **`corpus_daily.funding_bps` NULL on all 75 rows** — a recording defect that
  forced this audit to reconstruct causation from parquet file timestamps.
