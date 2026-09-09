# G3 Momentum (TSMOM) — Forward Evaluation Scope: Decision Record

**Date: 2026-09-09 (UTC) · Family `g3_momentum_tsmom` · Read-only at time of
writing · Not hash-pinned**

This is a **decision record**, not a pre-registration. It creates no threshold,
no gate, no construction parameter and no observation floor. It records four
decisions and two deviations, and cites the frozen artifacts by path and field.
Where a frozen value is quoted it is for identification and context only; the
cited artifact governs, and this record never restates a value as authority.

Nothing frozen is modified. `thresholds.yaml` (frozen 2026-06-13),
`research/forward_evidence/pre_registration_g3_tsmom.json` (registered
2026-06-14, hash `1cab7804…`, pinned in `registry.yaml`), the K=30 / HOLD_DAYS=7
construction, the 15 bps cost assumption, the observation floors and the
promotion criteria all stand unchanged.

---

## 1. What is binding, and what this record is

| Class | Where it lives | Changed here? |
|---|---|---|
| **Binding frozen gates** | `thresholds.yaml` `validated.edge`; pre-registration `observation_floors`, `promotion_criteria` G1/G2/G4 | **No** |
| **Frozen provenance constraint** | INC-0005 §"Evidence provenance"; CLAUDE.md §20 | **No** — applied, and its scope extended once (D-1) |
| **Interpretation / clarification** | This record: D-1, D-2, D-4 | Yes — that is its purpose |
| **Corpus boundary** | This record: D-3 | Yes |
| **Monitoring-only diagnostics** | Pre-registration G3, G5 (both marked *informational*), R1–R6 | **No** |
| **Implementation commitments** | Pre-registration `collection_spec` steps 1–8 | Two recorded as deviations (§4) |

Nothing in this record can relax, delay or substitute for a binding gate.

---

## 2. Evidence state at the time of decision

Counts only, read from `research/data/evidence_forward.db`:

- G3 forward entries: **10** — 9 resolved, 1 open (entry 40, `planned_exit 2026-09-10`).
- Segments: **pre-gap 5 resolved** (Mon anchor, 2026-06-15 … 2026-07-13),
  **post-gap 4 resolved** (Thu anchor, from 2026-08-06).
- Determinism: **67/67** `run_log.fn_hash` rows equal the registered
  `decision_function_hash` `90865666…`. No drift across the whole collection
  period.
- `dsr_prob` / `bh_p`: **NULL** on every G3 run, so the Validated gate is
  blocked by the Evidence Library's DSR/BH-FDR NULL enforcement (CLAUDE.md §16
  "R1") independently of sample size. That governance rule is a **different R1**
  from the G3 pre-registration's risk-register `R1` (2026 degradation) cited in
  §4; the two namespaces are unrelated.

---

## 3. Decisions

### D-1 (A1) — Primary evaluation uses the post-INC-0005 segment only

The primary statistical evaluation of G3 forward evidence is computed on the
**post-gap segment only** (Thursday anchor, from 2026-08-06). Pre-gap
observations are retained in full as **secondary descriptive evidence** and are
never pooled into the primary evaluation.

*Status: extension of a frozen constraint.* INC-0005 and CLAUDE.md §20 freeze
that the G3 entry series must be treated as two segments **for any
weekday-sensitive analysis**. The primary forward evaluation is not
weekday-sensitive by construction, so the frozen text does not reach it
literally. This record therefore **extends the same treatment** to the primary
evaluation, on the frozen constraint's own rationale. FB-10c-G §2.1 established
that widening §20's scope is a governance decision and declined to take it
unilaterally; this record takes it, **scoped to G3's own forward evaluation
and to nothing else**.

**Scope limit.** D-1 governs the primary forward evaluation series only. It does
**not** resolve FB-10c-G §2.3(5) — which segment governs a pairwise-independence
measurement, or how two segment correlations combine — which remains **open** and
requires a separate decision before any `max_pairwise_r` assessment.

Cost of the extension, absent a further collection interruption: none. See D-2 —
on current cadence the frozen calendar floor is expected to land after the
post-gap segment alone reaches the frozen 30-trade floor.

### D-2 (A2) — The 12-month assessment clock is calendar time

`observation_floors.minimum_calendar_months_before_assessment: 12` runs as
**calendar time from the registration date 2026-06-14**, giving an assessment
date of **2027-06-14**. It is not reinterpreted as collected time.

*Status: interpretation of an internally inconsistent frozen field.* The field
name says calendar months; the accompanying rationale says *"at least one full
year of forward data"*. The 19-day INC-0005 absence makes the two readings
differ by ~19 days. The field name governs. The frozen field itself is
unchanged.

Projection, recorded so the arithmetic is not re-derived later — **not a
guaranteed outcome**: if collection continues uninterrupted at the ~1/week
cadence, the post-gap segment alone would hold on the order of 45 observations
by 2027-06-14, so D-1 and the frozen 30-trade floor would be satisfied together
without pooling. A further collection interruption invalidates this projection
without altering D-1 or D-2; actual counts at the assessment date govern.

### D-3 (A5) — P-MIRROR is a separate corpus and never enters the forward population

P-MIRROR (ADR-0003) observations **never** increase the registered forward
statistical population or its `n`, and never enter any statistic used at the
Validated gate. They form a distinct execution/evidence corpus.

Grounds, each independently sufficient:

1. **Double counting.** The mirror does not generate `trade_entries`; it
   observes them. Admitting a mirror outcome makes one forward entry contribute
   twice, inflating `n` and understating variance — and DSR/BH are directly
   `n`-sensitive.
2. **It would silently rewrite the registered exit rule.** The frozen
   `function_spec` states *"no intra-hold exits"*. A mirrored position carries a
   protective stop, which is an intra-hold exit (ADR-LE-5, an independent
   overlay). Admitting stopped-out mirror trades changes the registered
   construction without a VERSION bump.
3. **Price basis.** Forward labels are Binance daily closes; mirror outcomes are
   Delta testnet fills, which CLAUDE.md §11 declares pipeline-validation only,
   never research evidence.
4. **Lane separation.** PR-5 built `current_run_per_lane` precisely to stop
   cross-lane selection; merging a live-execution measurement into the
   `forward/live-forward` lane defeats it.

**Permitted later, and not foreclosed:** because a mirror observation is keyed
to a specific `trade_entries.id`, forward and mirror give two measurements of
the same draw. A **paired** execution-vs-counterfactual analysis (tracking
difference on the same entry) is legitimate, isolates execution effects, and
costs the registered population nothing. That analysis is permitted; pooling is
not.

### D-4 (A6) — G2 is evaluated on the primary post-gap series

Promotion gate G2 (one-sided t-test, `p < 0.10`) is evaluated on the same
primary post-gap series as D-1. Pre-gap and post-gap are not pooled to increase
power.

*Status: follows D-1; the frozen G2 threshold is unchanged.* The frozen gate's
parenthetical (*"n=30 gives ~40% power"*) presumes one series of 30; D-2 shows
the post-gap segment supplies ≥30 by the assessment date, so the frozen power
assumption is preserved rather than weakened. The frozen text already
pre-commits that a G2 failure is recorded as **insufficient evidence**, not as
disproof.

---

## 4. Deviations recorded (no re-registration)

Both are **post-registration implementation commitments**, not gates. Neither
writes to `signal_log`, `trade_entries`, `trade_exits` or `trade_returns`, so
neither can contaminate collected evidence — corroborated by the clean 67/67
determinism result in §2.

### V-1 (A3) — `mae_mfe_path` never wired for the G3 collector

`collection_spec.daily_job.steps[6]` commits to updating `mae_mfe_path`.
`_update_mae_mfe` exists in `research/forward_evidence/daily_job.py`, but that
job writes `forward_evidence.db`; the G3 corpus is written by a different
collector into `evidence_forward.db`, which has no such table. Step 6 has never
run for G3.

**Non-contaminating implementation deviation. No re-registration.** No binding
gate depends on it: G4 drawdown is defined *"across all forward trades"*
(trade-level, from `trade_returns`), and no `validated.edge` key references it.

**Honest cost:** without intra-hold excursion, stop-vs-horizon compatibility and
the R2/R3 monitoring commitments can only be examined on daily closes.

### V-2 (A4) — R6 k=20 shadow logging not running

`signal_log` carries only the seven canonical signals; no k=20 variant series
exists.

**Non-binding monitoring deviation. No re-registration.** The frozen text
itself states *"Shadow logging only — k=20 does not drive decisions"*, and
`validated.edge` contains no sensitivity key at all (`min_sensitivity` is a
**candidate** threshold, already passed). R6's phrase *"100% sensitivity at the
Validated gate"* is a risk note not backed by any frozen threshold; that
overstatement is corrected here and nowhere else.

---

## 5. Status and open items

- **Not hash-pinned.** This record carries no governance force over
  `report.py --check` and is not referenced from `registry.yaml`. Pinning is a
  separate decision; it would make the record binding and immutable, and would
  require resolving whether `registry.yaml`'s single-object `preregistration`
  field can carry a second artefact.
- **Unchanged by this record:** every frozen threshold, the K=30/HOLD_DAYS=7
  construction, the 15 bps cost assumption, all observation floors, promotion
  criteria G1–G5, and the pre-registration hash pin.
- **Still blocking Validated promotion, independent of this record:**
  `dsr_prob` and `bh_p` are NULL on every G3 run.
