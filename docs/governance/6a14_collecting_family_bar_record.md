# 6A.14 — Collecting-Family Independence Bar: Retrospective Durable Record

**Written: 2026-08-30 (UTC) at Gate A1 · Subject gate: 6A.14 (already executed)
· Status: RECONSTRUCTION, not a decision**

This record exists because Gate 6A.17 — an executed governance decision that
admitted `g1_trend_regime_ma_cross` for forward collection — **applies and
depends on a rule established at Gate 6A.14, which left no durable record in
`docs/governance/`.** 6A.17 named the gap in its own §6 and declined to close
it, on the grounds that reconstructing another gate's reasoning from a citation
is precisely the fabrication this project forbids:

> This admission **applies** that rule and depends on it. Writing 6A.14's record
> retroactively is not this gate's authority — reconstructing another gate's
> reasoning from a citation is precisely the fabrication discipline this project
> forbids. Recorded here so the gap is visible and can be closed by a gate that
> owns it.
>
> — `docs/governance/g1_trend_regime_admission_decision.md` §6

**A1 owns it. This record closes the gap in the only way the evidence permits:
by recording what 6A.14 decided and what is provably recoverable about it, and
by marking — explicitly, item by item — what is NOT recoverable.** No rationale,
parameter, counterparty, method, window or figure is supplied here that is not
already present in a tracked repository artifact. Where the evidence stops, this
record stops and says so.

**Nothing is decided, ratified, re-opened or changed here.** No code, config,
database, ledger, registry, threshold, systemd unit or production artifact is
modified by this gate. `g5_trend_4h`'s governance state is exactly what it was
before this file existed.

---

## 1. What 6A.14 decided

Two decisions, both attested consistently across the tracked artifacts in
§2:

1. **`g5_trend_4h` was rejected for forward collection**, at an observed
   **|r| = 0.7380 against a family that was already collecting.**
2. **The trend branch fell back to `g1_trend_regime_ma_cross`**, the candidate
   subsequently taken through 6A.15 → 6A.19b and now collecting.

### What 6A.14 did NOT decide

This distinction is load-bearing and is established by the ledger, not by
inference:

- **It was not a lifecycle-state change.** `g5_trend_4h` is still `state:
  candidate`, `state_since: 2026-06-13`. Its **only** row in
  `state_transitions` is `id=5`, the 2026-06-13 `discovery -> candidate`
  bootstrap transition (`actor=bootstrap`). 6A.14 wrote no transition.
- **It was not a disqualification.** `dq_reasons: []`. The four disqualified
  families (`g1_breakout_donchian`, `g1_volatility_regime`, `g4_inside_bar`,
  `g6_obv_trend`) all carry explicit `discovery -> disqualified` transitions;
  `g5_trend_4h` does not.
- **It did not retire, supersede or downgrade the family.** `supersedes: None`,
  `superseded_by: None`.

`g5_trend_4h` remains a Candidate in good standing that is not collecting. That
is a coherent state: as the 6A.17 pre-registration records, *"there is no
`admitted` lifecycle state and no registry field marking a family as
collecting"* — collection is what happens between Candidate and Validated, and
admission to it is a governance judgement recorded in prose.

---

## 2. The evidence actually supporting this record

Every statement in §1 rests on these tracked artifacts. No other source was
used.

| # | Artifact | What it attests |
|---|---|---|
| E1 | `docs/governance/g1_trend_regime_admission_decision.md` §2.3, §5, §6, §8 | The rule as applied at 6A.17; the 0.7380 figure; the gap declaration; gate trail row *"6A.14 — `g5_trend_4h` rejected for collection; trend branch falls back here"* |
| E2 | `research/forward_evidence/pre_registration_g1_trend_regime.json` (ACTIVE, hash-pinned) | `/governance/collecting_family_bar`, `/admission/criteria_applied`, `/admission/governance_gap_surfaced`, `/independence/context_vs_6a14_rejection`, `/gate_trail[2]` |
| E3 | `research/forward_evidence/drafts/g1_trend_regime_ma_cross_prereg_draft.json` (frozen at 6A.15) | Same four fields, frozen **before** 6A.16/6A.17 — the citation predates the admission that relies on it |
| E4 | `docs/research/g1_trend_regime_ma_cross_6a15_specification.md` lines 241, 252-254, 319, 362 | The rule stated at specification-freeze time |
| E5 | `research/evidence_library/ledger.db` — `state_transitions`, `pairwise_r` | The negative evidence in §1: no transition, no recorded pair |
| E6 | Commit `93f9a94` body, line 49 | *"Also surfaced, not closed: the 6A.14 collecting-family determination has no..."* — the gap declared in the immutable commit record, not only in the document |

**E2 and E3 are one lineage, not two independent witnesses.** Their 6A.14
fields are **byte-identical** (verified field by field at this gate): E2 carries
forward E3's text unchanged. They are listed separately because E3 was frozen at
6A.15 — which establishes that the 6A.14 citation entered the record **before**
the gate that would come to depend on it, rather than being assembled afterwards
to justify it — but they should be counted as a single attestation.

Discounting for that, the independent attestations are: **E1** (the 6A.17
governance record), **E2/E3** (one pre-registration lineage), **E4** (the 6A.15
specification), **E6** (6A.17's commit body, `93f9a94`), and **E5** (the ledger's
negative evidence). The genuinely independent corroboration of the rule's *scope*
comes from §2.1, which involves a different family, a different gate, and the
opposite outcome.

### 2.1 Independent corroboration of the rule's scope

The word *"collecting"* in the rule is not an artifact of one gate's phrasing.
It is corroborated, in the opposite direction, by a source that predates 6A.17
and was written for an unrelated family — `decision_function_g2_funding_level.py`
lines 39-50:

> `|r| = 0.8187` against `g6_funding_basis_composite` on the train slice
> (2021-01-01..2025-05-31), measured with the `analyze_mtf_independence`
> convention. That exceeds `max_pairwise_r = 0.70`. `g6_funding_basis_composite`
> **is currently `discovery` and is not collecting, so the conflict is latent,
> not active** — but if it is ever promoted, the Validated independence gate
> will fire on this pair.

`g2_funding_level` was admitted for collection **while carrying a pair at
0.8187** — far above the bar — because that counterparty was not collecting; and
the same source separately records its max |r| against the five families that
*were* collecting as 0.3774. The bar therefore discriminates on **collecting
status**, exactly as the 6A.14 rule states, and it does so in a case where the
answer went the other way. Two independent gates, opposite outcomes, one
consistent rule.

### 2.2 The source of the 0.70 constant

`research/evidence_library/thresholds.yaml`:

```yaml
validated:
  edge:
    max_pairwise_r: 0.70      # line 55
  feature:
    max_pairwise_r: 0.70      # line 62
  independence_r: 0.70        # line 88
```

The constant appears three times, all three under `validated:` — there is no
collection-time counterpart anywhere in the file (see 4.3).

Frozen 2026-06-13 and named in CLAUDE.md §16 among the governance-frozen
constraints. **6A.14 introduced no new constant.** It applied an existing,
already-frozen one at a different point in the lifecycle.

---

## 3. The exact rule 6A.17 relied upon

Stated verbatim in the active, hash-pinned pre-registration
(`/governance/collecting_family_bar`):

> **6A.14 determination — a candidate whose |r| vs a CURRENTLY COLLECTING family
> reaches 0.70 is inadmissible for collection. This candidate clears it at
> 0.6030.**

And applied, verbatim, in `/admission/criteria_applied`:

> Clone 0.6030 < 0.90; **collecting-family bar 0.6030 < 0.70.** No new statistic
> was generated.

`g1_trend_regime_ma_cross` cleared it at **|r| = 0.6030** against
`g1_market_regime_sma200`, measured on the train slice only (1,612 rows,
2021-01-01..2025-05-31, the 365 lockbox rows excluded) under frozen methodology
`analyze_mtf_independence.py` Test 1, with VIF 1.882 and 53.1% unexplained
variance — all recorded at 6A.15-PRE and re-stated at 6A.17 §2.3.

This bar is **distinct from** the clone bar it sits beside.
`candidate.feature.max_clone_r = 0.90` and `hard_dq.clone_r` detect
near-duplication against *any* family. The 6A.14 bar is tighter (0.70) and
narrower in population (collecting families only). Both were evaluated
separately at 6A.17 and both passed.

---

## 4. Why that rule was admissible

Four reasons, each checkable against the repository. None of them is a
reconstruction of 6A.14's reasoning — they are properties of the rule as
applied, which is what 6A.17 depended on.

**4.1 It spends no new governance authority.** The 0.70 constant was frozen on
2026-06-13. Applying an already-frozen ceiling at an earlier lifecycle point
neither creates a threshold nor relaxes one, and CLAUDE.md §16's frozen list is
untouched by it.

Stated precisely, because the distinction matters: CLAUDE.md §16 line 519 scopes
the frozen entry as *"`max_pairwise_r = 0.70` **at Validated**"*, and
`thresholds.yaml` places all three occurrences under `validated:`. 6A.14
therefore applied the constant at a checkpoint the frozen text does not name.
That is an **extension of scope, not a redefinition of the value** — the number,
its meaning (a ceiling on |r| between families) and its Validated-gate
enforcement are all unchanged. §4.2 is why the extension is safe in the only
direction it can act.

**4.2 It is strictly conservative — it can only ever refuse.** The bar gates
*entry to collection*. A family it blocks is not promoted, not disqualified, not
altered; it simply does not start accumulating forward evidence. Because the
same 0.70 ceiling is enforced again at the Validated gate on a stricter basis
(forward returns), applying it earlier cannot admit anything the later gate
would reject. The failure mode of being wrong is a delayed candidate, never an
unsound promotion.

**4.3 It fills a real void rather than duplicating an existing control.**
`thresholds.yaml` declares **no** collection-time independence bar of any kind.
The codified `max_pairwise_r` check (`lifecycle.check_validated_independence`)
runs only against families in the **Validated** state, of which there are
currently **zero** — so it is structurally incapable of firing at any point
before the first promotion. Without the 6A.14 bar, an arbitrary number of
mutually redundant families could enter forward collection and accumulate months
of correlated evidence before any independence control existed to notice.

**4.4 It matches the platform's own foreseeability discipline.** §2.1's g2 note
records a latent 0.8187 conflict at registration *"so that outcome is
foreseeable from the registration rather than a surprise at promotion."* The
6A.14 bar is the same principle made operative: measure independence when the
family enters collection, not when it is already carrying a year of evidence.

---

## 5. Scope and limitations

This section is the substance of this record, not a caveat appended to it. Seven
items are **not** recoverable from the repository and are **not** supplied here.

**L1 — The counterparty of the 0.7380 measurement is not stated in any
artifact.** Every citation says only *"against a collecting family."* Two
pre-existing artifacts converge on `g3_momentum_tsmom`: the registry weakness for
`g5_trend_4h` reads *"Correlated at r>=0.70 with Momentum TSMOM (cluster head in
FINAL_AUDIT); independence check fires at Validated gate"*, and
`research/reports/evidence_library/correlation_matrix.csv` records that pair at
**0.7120** — the only collecting family above 0.70 in that matrix.
**This is the corroborated reading, and it is recorded as such — it is NOT
6A.14's recorded finding, because 6A.14 recorded no counterparty.**

**L2 — The 0.7380 figure's method and window are unrecovered, and it is
demonstrably not the FINAL_AUDIT value.** 0.7380 does not appear anywhere in
`correlation_matrix.csv` (verified: zero cells within 5e-5 of it, across the full
34x34 matrix). The IS matrix value for the converged pair is 0.7120. A different
measurement convention, window or construction therefore produced 0.7380, and no
artifact attributes one to 6A.14. `analyze_mtf_independence.py` is the frozen
convention used by the immediately adjacent gates (6A.15-PRE for 0.6030, and
`g2_funding_level` for 0.8187) — recorded as adjacent context only. **No artifact
attributes it to 6A.14, so this record does not.**

**L3 — 6A.14 produced no primary artifact of any kind.** No commit on any
branch was authored for it: searching every commit subject across all refs for a
`(6A.14` gate tag returns nothing. (`git log --all --grep='6A.14'` *does* return
one hit — `93f9a94`, whose subject is `governance(6A.17)` and whose body cites
the gap at line 49. That is E6, a citation by a later gate, not a 6A.14 commit.)
There is likewise no `state_transitions` row, no `pairwise_r` row (that table is
**empty**, 0 rows), no report file, and no document. Its entire trace in this repository is second-hand citation from later
gates. This record does not change that; it makes it legible and permanent.

**L4 — The boundary semantics are unsettled, and are NOT settled here.** The
citations say a candidate whose |r| *"reaches 0.70"* is inadmissible, which reads
as `>= 0.70` blocking. The codified Validated check is `abs(float(r)) > max_r`
(`lifecycle.py:262`), and `tests/test_pairwise_independence.py:220` asserts
deliberately that *"max_pairwise_r is a ceiling: r == threshold must pass"* — so
at the Validated gate, exactly 0.70 **passes**. The two readings differ at
precisely one point and have **never been exercised there**: 0.7380 is
unambiguously above and 0.6030 unambiguously below under either. Resolving this
would be a new governance decision and is outside A1's authority. It is recorded
as an open question in §7.

**L5 — The bar is enforced nowhere in code.** A repository-wide search finds no
implementation of a collecting-family independence check. It is a prose
governance judgement applied by the operator at each admission gate. Unlike the
Validated gate, nothing structural prevents a future gate from omitting it.

**L6 — It measures a different quantity than the gate it borrows its constant
from.** The 6A.14 bar was applied to **train-slice position correlations**; the
Validated gate enforces the same 0.70 on **forward returns** against Validated
peers. Clearing the collection bar establishes nothing about the promotion bar —
a point already carried as risk R4 in the active pre-registration: *"The 0.6030
recorded here does NOT guarantee the forward-return pair clears 0.70 against
`g1_market_regime_sma200`. A real promotion risk, not a resolved matter."*

**L7 — No measurement was recomputed to fill any of the above.** Re-deriving
0.7380 was considered and deliberately rejected. A number generated in 2026-08 by
this gate would not be 6A.14's finding, and placing it in this record would
manufacture exactly the false provenance that A1 exists to prevent. The gaps in
L1-L4 are left open.

---

## 6. Status of this record

**This is a retrospective durable record of an already-executed gate. It is not
a new decision, and it does not ratify, re-open, revise or re-authorise 6A.14.**

- 6A.14's decision took effect when it was made. `g5_trend_4h` has not been
  collecting since; `g1_trend_regime_ma_cross` was taken forward instead and has
  been collecting since 2026-08-28 (audited at Gate 6A.20, PASS).
- **6A.17's admission is unaffected.** It stands on the metrics recorded in its
  own §2.3 and its pinned run, all of which were verified at that gate. This
  record supplies the missing durable statement of one rule 6A.17 cited; it adds
  no evidence to, and withdraws none from, that admission.
- Had the evidence contradicted 6A.17's citation, this record would say so and
  stop. It does not: five artifacts agree, two of them frozen before the
  admission, one corroborating the scope from an unrelated family with the
  opposite outcome.
- **No governance state changed.** `registry.yaml`, `thresholds.yaml`,
  `collection_config.yaml`, `ledger.db`, the sealed lockbox, every sealed
  artifact, and production are untouched. `n_trials = 34` unspent.

---

## 7. Open questions this record deliberately leaves open

Recorded so they are visible, not resolved. Each would be a new governance
decision requiring its own gate.

1. **Boundary semantics (L4).** Does the collection bar block at `>= 0.70` or at
   `> 0.70`? The Validated gate's answer is settled and tested; the collection
   bar's is not.
2. **Codification (L5).** Should the collecting-family bar be implemented, or
   deliberately left as an operator judgement? Implementing it would require
   deciding (1) first.
3. **Ledger home for collection-time measurements (L3).** `pairwise_r` exists and
   is empty. Whether collection-time pairs belong in it — or whether it is
   reserved for the forward-return measurements the Validated gate consumes — is
   undecided. Until that is settled, figures like 0.7380 have nowhere durable to
   live and will keep depending on prose.
4. **`g5_trend_4h`'s standing.** It remains a Candidate that is not collecting,
   with no recorded review date. Whether it should be re-screened against the
   current collecting set, left dormant, or formally retired is not addressed
   here.

---

## 8. Trail

| Gate | Outcome |
|---|---|
| 2026-06-13 | `g5_trend_4h` admitted `candidate`, role `feature`, Phase B seeding (ledger transition id=5) |
| 6A.14-PRE / **6A.14** | **`g5_trend_4h` rejected for collection at \|r\| = 0.7380 vs a collecting family; trend branch falls back to `g1_trend_regime_ma_cross`** — no artifact produced |
| 6A.15-PRE | Clone screen on the fallback candidate: max \|r\| 0.6030, VIF 1.882, no pair >= 0.70 |
| 6A.15 → 6A.19b | Specification frozen, implementation hash-pinned, admitted, wired, deployed |
| 6A.17 | Applied the 6A.14 bar (0.6030 < 0.70); **declared this gap in its §6** |
| 6A.20 | First-observation audit of the fallback candidate: PASS |
| **A1** | **This record. Gap closed to the limit of the evidence; L1-L4 left open.** |
