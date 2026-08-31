# FB-10c — Pairwise Independence: Governance Decision Record

**Date: 2026-08-31 (UTC) · Gate FB-10c-R · Decision: measurement basis RESOLVED
from frozen evidence; implementation DEFERRED; six questions left OPEN**

Records the governance contract for forward pairwise-independence measurement.
Implements nothing, measures nothing, and changes no schema, threshold,
pre-registration or ledger row.

Every claim was verified at this gate: pre-registered text against the
hash-pinned JSON in the repository, and all counts against the **production**
corpus and canonical ledger, read-only (`as_of = 2026-08-30`; 81 runs, 10 L2,
`pairwise_r` 0 rows, 0 Validated).

---

## 1. Frozen facts

**PW-1 — `append_pairwise_r()` is complete and strict.** It refuses NaN
(*"a NaN correlation is not a measurement -- it is a failed computation, and
recording it would let an unmeasurable pair masquerade as a measured one"*),
`|r| > 1`, self-pairs, unsorted ids (*"refusing is honest; silently reordering
hides a caller bug"*), and an empty `method` (*"a correlation without a stated
method is not reproducible"*). Append-only; re-measuring a pair appends.

**PW-2 — the table stores exactly six fields.**

```sql
pairwise_r(id, ts, family_id_a, family_id_b, r, method, actor)
CHECK (family_id_a < family_id_b)
```

**PW-3 — `get_pairwise_r(as_of)` is replay-safe**: latest per canonical pair,
`(ts, id)` tiebreak mirroring PR-5's. Note precisely what `as_of` filters:
**`ts` is the RECORDING time**, so `as_of` selects "the evidence the operator
actually had then", not "the window the correlation covers".

**PW-4 — `check_validated_independence` is pair-scoped and fail-closed** on
three modes: no recorded pair against a Validated peer, NaN, or `abs(r) > max_r`.
Comparison is strict `>`, so `r == 0.70` **passes** — deliberate, and asserted by
`tests/test_pairwise_independence.py:220`.

**PW-5 — `max_pairwise_r = 0.70`**, frozen under CLAUDE.md §16, read from
`validated.<role>` with `min(ceilings)` across roles.

**PW-6 — production state, verified:** `pairwise_r` **0 rows**; **0 Validated
families**; **no production caller** of `append_pairwise_r` — every call site is
a test or a docstring.

---

## 2. RESOLVED from frozen evidence: the measurement basis is FORWARD RETURNS

This was the one question at risk of being answered by convenience. It is not
open, and the evidence settles it against the convenient answer.

`g1_trend_regime`'s `promotion_criteria.gates.G3_independence` — hash-pinned:

> "max_pairwise_r <= 0.70 against every Validated family at the time of
> promotion, **computed on FORWARD RETURNS**. The 0.6030 measured at 6A.15-PRE is
> a train-slice **POSITION** correlation and **does not establish this** (risk R4)."

Its risk R4 states the same in the negative: *"PAIRWISE INDEPENDENCE MEASURED ON
TRAIN POSITIONS, NOT FORWARD RETURNS ... A real promotion risk, not a resolved
matter."*

The pre-registration therefore does not merely *prefer* returns — it names the
position substitution as the specific inadequacy, and carries it as a live risk.

**Consequence, recorded plainly:** a position-series correlation is computable
today (58 overlapping days for the three oldest families, 54 with `ivrv`, 45 with
`g3_atr_expansion`, but only 3-5 for `g1_trend_regime_ma_cross` and
`g2_funding_level`), while a forward-return correlation is not — closed trades
number 0-8 and do not align across families. **Computability does not override a
frozen basis.** Measuring positions and recording them as satisfying G3 would
contradict a hash-pinned document that explicitly anticipated and rejected that
substitution.

Positions may still be measured and recorded as *supporting* evidence, provided
`method` says so unambiguously and no promotion relies on it. That is a
reporting choice, not a gate.

---

## 3. Pre-registered expectations (binding, not yet actionable)

**PE-1 — measurement is pre-registered as PRE-EMPTIVE, before any promotion
attempt.** Three families state it independently:

- `h5_vol_regime`: *"compute and record pairwise r before any Validated
  promotion attempt"* — with h6, at the 12-month forward mark.
- `g1_market_regime_sma200`: *"Compute and record pairwise r with h6 and with
  g1_trend_regime_ma_cross before any Validated promotion attempt"*, and
  *"at the quarterly snapshot"*.
- `g3_momentum_tsmom`: *"Compute and record pairwise r at the 12-month forward
  mark before any Validated promotion attempt."*

**PE-2 — structural containment is anticipated, with expected failure.**

| pair | pre-registered expectation |
|---|---|
| `h5_vol_regime` x `h6` | *"h5 is a direct component of h6 (h6 requires vol_regime==medium)"* -- r likely >= 0.70; *"Independence check will block simultaneous Validated promotion of both."* Recorded as **"expected and not a defect"** |
| `g1_market_regime_sma200` x `h6` | h6 *"embeds this signal as its bear-filter"* -- likely >= 0.70 in trend-consistent environments |
| `g3_momentum_tsmom` x `g5_trend_4h` | IS r = **0.712** (FINAL_AUDIT); *"only one can be promoted unless r < 0.70 in forward data"* |

**PE-3 — a cluster-head priority rule is pre-registered.** `g3_momentum_tsmom`:
*"If r >= 0.70, g3_momentum_tsmom has priority as cluster head (higher IS Sharpe,
edge role vs feature role for 4H Trend)."* Stated twice, in
`promotion_criteria.notes` and `known_risks[4].monitoring`.

**PE-4 — an "independence gate override" is referenced but does not exist.**
`g1_market_regime_sma200`: *"only one can be promoted without an independence
gate override."* Repo-wide search finds **no such mechanism** in code or
thresholds. Recorded as a gap, not invented here.

---

## 4. The zero-peer vacuity, and why it is recorded

`check_validated_independence` iterates the Validated peer set. With **zero
Validated families**, the loop never executes and the check passes vacuously.

So: **the first family promoted faces no pairwise requirement at all.** The
second faces one pair, the third two.

This interacts directly with PE-2. `h6` structurally contains `h5_vol_regime` and
embeds `g1_market_regime_sma200`; `g3_momentum_tsmom` and `g5_trend_4h` are
correlated at 0.712 in-sample. If promotion order is discretionary, **the order
itself can determine which of several related families is ever validatable** —
without any gate firing, because the first promotion is unconstrained.

This is not a defect in the check; a first family genuinely has nothing to be
independent of. It is recorded because the consequence is easy to realise too
late, and because PE-3's cluster-head rule exists precisely to constrain such a
choice for one pair while nothing constrains it generally.

---

## 5. Schema sufficiency

**The current schema is INSUFFICIENT for the resolved contract, and the shortfall
is specific:** `pairwise_r` records no `as_of`, no sample size `n`, and no window.
`ts` is the recording time (PW-3). Under §2's forward-returns basis, a
measurement is meaningless without the window it covers and the sample it rests
on -- a forward r over 6 trades and one over 60 are not comparable, and nothing
in the row distinguishes them.

Two ways to close it, **neither adopted here**:

- **S-A — convention only.** Require `method` to encode basis, window and n in a
  controlled vocabulary. No schema change; enforcement is by convention, and the
  field stays free text.
- **S-B — additive columns** (`as_of`, `n`, `window_start`, `window_end`).
  Stronger and machine-checkable, but `pairwise_r` is a governed ledger table:
  additive migration only, and `CHECK` constraints added by `ALTER TABLE` are
  validated against existing rows immediately (the PR-4 migration asymmetry
  documented in CLAUDE.md §17).

**Recommendation, for a later gate: S-B.** The table has 0 rows, so an additive
migration is free now and never cheaper later. But it is a governed-schema change
and is not made here.

---

## 6. The future chain

```
forward snapshot (FB-9)     per-family, frozen floors payload
        |
        +--> statistical validation (FB-10b)   BATTERY-WIDE, deferred (FB-10a)
        |        DSR/BH are properties OF THE RUN, frozen into metrics_json
        |
        +--> pairwise independence (FB-10c-impl)
                 a property OF THE PAIR, recorded in pairwise_r
        |
        v
   admission (FB-8) --> pin/re-pin --> Validated promotion (human)
```

**Pairwise is a sibling of statistical validation, not a stage of it.** Evidence:
`check_validated_preconditions` evaluates provenance -> DSR -> BH -> **pairwise
last**, and pairwise is the only check taking an external argument
(`validated_peers`) instead of reading the pinned run. DSR/BH are frozen into the
run; pairwise is consumed *at promotion*, against whoever is Validated then.
Computing them in one operation would give it two authorities.

---

## 7. Unresolved -- OPEN, not chosen here

| # | Question | Why it cannot be resolved from frozen evidence |
|---|---|---|
| **O-1** | What must `method` contain? Controlled vocabulary? | Nothing frozen specifies it; PW-1 only requires it be non-empty |
| **O-2** | Minimum n for a pairwise measurement | No minimum is frozen anywhere. `analyze_mtf_independence` has a both-std>0 guard only. Distinct from FB-10a's n>=5 DSR guard and from FB-7's observation floors |
| **O-3** | Is `exit_date <= as_of` required? | Consistent with every other forward computation, but **not stated** for pairwise in any pre-registration or threshold |
| **O-4** | Measure all pairs at snapshot time, or only against Validated peers at promotion? | PE-1 says "before any promotion attempt" and names quarterly / 12-month marks -- but the enforcement set is peers-at-promotion. Both readings are supported |
| **O-5** | How is PE-3's cluster-head rule operationalised, and does PE-4's "override" exist? | The rule is pre-registered prose with no mechanism; the override is referenced and absent |
| **O-6** | Does promotion ordering itself need governance (§4)? | Nothing frozen addresses ordering |

Note O-3 and O-4 interact: a pre-emptive measurement at a snapshot `as_of` and a
promotion-time measurement against current peers may be different numbers, and
nothing frozen says which binds.

---

## 8. Decision

1. **Measurement basis: FORWARD RETURNS.** Resolved from `G3_independence` and
   risk R4. Position-series correlation is **not** a substitute and must not be
   recorded as satisfying G3.
2. **Implementation DEFERRED.** With 0-8 closed trades, 0 Validated peers, and
   the forward-return basis not yet computable for any pair, no measurement can
   bind on anything today.
3. **Schema change DEFERRED but recommended (S-B)** for a later gate.
4. **Six questions O-1..O-6 remain OPEN** and are not chosen here.
5. **Nothing measured, nothing recorded.** `pairwise_r` stays at 0 rows.

---

## 9. Conditions that must be satisfied before FB-10c-impl

1. O-1 through O-4 decided (basis is settled; method, minimum n, point-in-time
   rule and timing are not).
2. Schema question settled: S-A or S-B.
3. Enough closed forward trades for a forward-return correlation to be
   meaningful for at least one pair -- which is downstream of FB-7's observation
   floors, none of which is met.
4. FB-10a's U1/U2/U3 need **not** be resolved first: pairwise is a sibling of
   DSR/BH, not a consumer of it.
5. The producer must be operator-invoked and refuse service actors, as
   `forward_snapshot.py` (FB-9) already does. It must not admit, pin, transition
   or promote.

---

## 10. Trail

| Gate | Outcome |
|---|---|
| §16 Proposal 1 | `max_pairwise_r = 0.70` frozen; enforcement implemented 2026-07-19 |
| FB-5 | Identified `pairwise_r` empty as gap M4 |
| FB-10 | Confirmed no production caller; pairwise separate from DSR/BH |
| FB-10c | Audited the path; found the basis conflict and the zero-peer vacuity |
| **FB-10c-R** | **This record. Basis RESOLVED to forward returns; deferred; O-1..O-6 open** |
| FB-10c-impl | The producer -- **not started** |
