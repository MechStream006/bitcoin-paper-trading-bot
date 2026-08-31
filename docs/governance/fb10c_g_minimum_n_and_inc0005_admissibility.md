# FB-10c-G — Pairwise Minimum-N and INC-0005 Admissibility: Decision Record

**Date: 2026-08-31 (UTC) · Gate FB-10c-G · Read-only · Outcome: B-2 requires a
NEW FROZEN CONSTANT (value not chosen here). B-3 RESOLVED against pooling by a
frozen binding constraint. FB-10c implementation is NOT authorized.**

No code, no schema change, no correlation of any family computed or stored, no
pre-registration / threshold / registry / ledger / production change. The only
numbers computed from the corpus are **observation counts**; the small-sample
figures below come from **synthetic random draws**, not from any family.

---

## 1. B-2 — Minimum n

### 1.1 Exhaustive search result: no frozen pairwise minimum exists

Searched every correlation call site, all eight pre-registrations field-by-
field, `thresholds.yaml`, `lifecycle.py`, `record.py`, `governance.py`,
`engine.py`, the methodology scripts, the governance documents, and the test
suite.

**Every correlation site in the repository guards on `std > 0` and nothing
else.** There is no n guard in `analyze_mtf_independence.py`,
`analyze_orderflow_independence.py`, `run_final_audit.py`, or
`governance.continuous_correlation`.

Two near-misses, both examined and both **excluded** under this gate's
instruction not to borrow without an established relationship:

| candidate | what it actually is | why excluded |
|---|---|---|
| `run_s1_validation.py:599` `len(common_yrs) >= 3` | a display guard before a diagnostic Pearson on **annual aggregates**, in a strategy-validation script outside the evidence library | not governance; a print-formatting threshold, never cited as a standard |
| `engine.py:72` `MIN_TRADES_TRUST = 15` | caps a **signal's own verdict** at "Weak" below 15 trades (`engine.py:519`) | about single-signal evidence strength, never connected to correlation |

`MIN_TRADES_TRUST` is worth recording for a different reason: it establishes
the platform's own **idiom** for small samples — a small sample does not
invalidate the computation, it **caps the strength of the claim**. That idiom
is relevant precedent for how a pairwise floor might be shaped. The number 15
is not transferable and is not used here.

### 1.2 The three minima, distinguished as required

| kind | frozen value for pairwise? | what exists |
|---|---|---|
| **computational** | **No** | Pearson is *defined* for n >= 2, but see §1.3 — n = 2 is degenerate. The repository's only guard is `std > 0`, which does **not** catch it |
| **statistical / research** | **No** | none for correlation. `MIN_TRADES_TRUST = 15` is for verdicts; DSR's n >= 5 is a PSR mathematical guard (FR-5); 10/15 are DSR sufficiency; 26-30 are FB-7 observation floors. FR-5 explicitly forbids conflating these |
| **promotion** | **No** | `max_pairwise_r = 0.70` constrains r and is **silent on n**. `check_validated_independence` never reads n |

### 1.3 The evidence that a floor is mandatory, not discretionary

Pearson on **n = 2 is identically ±1**: two points determine a line. Verified
on synthetic draws — six of six returned exactly `±1.000000000000000`. The
`std > 0` guard passes in every one of those cases.

Independent-noise behaviour at the 0.70 ceiling (20,000 synthetic trials per
n, no corpus data):

| n | mean \|r\| | P(\|r\| > 0.70) for **independent** series |
|---|---|---|
| 2 | 1.000 | **100.0%** |
| 3 | 0.634 | **50.1%** |
| 4 | 0.498 | 29.7% |
| 5 | 0.423 | 18.5% |
| 7 | 0.340 | 8.0% |
| 10 | 0.273 | 2.3% |
| 30 | 0.149 | 0.0% |

At n = 2 the ceiling is **guaranteed** to be breached by two perfectly
independent families. At n = 3 it is a coin flip. This is not "a noisy
estimate" — below roughly n = 5 the gate reports dependence that does not
exist, deterministically at n = 2.

Note the direction, and contrast it with FB-10c-D: zero-fill biased toward
**passing** (unsafe); small-n complete-case biases toward **failing**
(conservative, but still false evidence, and it would block a family for a
reason that is not dependence).

### 1.4 B-2 DECISION

**No frozen minimum exists, and none is invented here.** A minimum n **must
become a new frozen constant** before any pairwise measurement is recorded.
The evidence above establishes that it is *required* — without it the gate
produces guaranteed-spurious failures at n = 2 — but the **value is a
governance choice and is not made in this record.**

Two decisions are required, not one:

1. **The constant** — a `min_pairwise_n` in `thresholds.yaml`, alongside
   `max_pairwise_r`, stated as a governance criterion distinct from the three
   existing minima. The table in §1.3 is the decision input.
2. **Whether the check enforces it.** `check_validated_independence` currently
   reads only `r`. Recording n without enforcing it leaves the defect fully in
   place while creating the appearance of governance.

---

## 2. B-3 — INC-0005 admissibility

### 2.1 A frozen binding constraint applies directly

`INC-0005`'s section titled **"Evidence provenance — binding constraint on
future analysis"**, and CLAUDE.md §20, both state:

> "**Any weekday-sensitive analysis** of the G3 entry series **must treat the
> series as two segments, not one.**"

FB-10c-D resolved alignment to **complete-case pairing on coincident trade
dates**. That method is *definitionally* weekday-sensitive: the entire
coincident set is determined by whether two families share an entry weekday.
The demonstration is in the corpus itself — `h5_vol_regime` (Tue) had **zero**
coincident dates with `g1`/`g3` (Mon) before the gap, and 2-3 after it, purely
because all families re-anchored to Thursday.

**The frozen constraint therefore applies to the resolved method. Pooling
pre-gap and post-gap observations into one correlation is inadmissible.**

**Scope, stated honestly:** the frozen text names *the G3 entry series*. For
pairs involving `g3_momentum_tsmom` the constraint applies **literally**.
FB-10c-D newly measured that `g1_market_regime_sma200`, `h5_vol_regime`,
`ivrv_vrp_spread` and `g3_atr_expansion` **all** re-anchored across the same
gap, so the identical factual condition holds for them. Extending the rule to
those pairs is the consistent reading of its own rationale, but formally
widening §20's scope is a small governance decision that this record does not
take unilaterally.

### 2.2 Quantified consequences for h5 / g1 / g3, as required

Segmented at the gap (counts only, from FB-10c-D):

| pair | pre-gap n | post-gap n | pooled n | consequence under §1.3 |
|---|---|---|---|---|
| `g1_market_regime_sma200` x `g3_momentum_tsmom` | 5 | 2 | 7 | pre: 18.5% false-fail; post: **\|r\| = 1.0 guaranteed** |
| `g3_momentum_tsmom` x `h5_vol_regime` | **0** | 3 | 3 | no pre-segment exists; post: **50.1% false-fail** |
| `g1_market_regime_sma200` x `h5_vol_regime` | **0** | 2 | 2 | no pre-segment exists; post: **\|r\| = 1.0 guaranteed** |

**Two of the three pairs this gate names would return \|r\| = 1.0 by arithmetic
necessity**, failing the 0.70 independence ceiling for a reason that has
nothing to do with the families being related. The third would be a coin flip.

Pooling would "rescue" n only by violating the frozen constraint — and even
pooled, the best pair reaches n = 7 (8.0% false-fail).

### 2.3 B-3 DECISION

1. **A pooled pre+post correlation is INADMISSIBLE.** Frozen constraint,
   applied to a method that is weekday-sensitive by construction.
2. **Segmented measurements are honest but currently unusable.** Every
   available segment is at or below the degeneracy boundary (n in {0, 2, 3,
   5}); two land exactly on n = 2.
3. **Post-gap-only pairs are not silently accepted.** `h5`'s pairs have no
   pre-segment at all; any r for them would describe only the ~3-week window in
   which an outage forcibly synchronised every family. That is recordable as a
   labelled segment, never as "the" pairwise r for the family.
4. **Pre-gap data is not silently excluded.** It is preserved as its own
   segment wherever it exists (n = 5 and n = 2 for two pairs).
5. **Open, and required before any measurement:** there is **no frozen rule for
   which segment governs a promotion, or how two segment correlations
   combine.** `max_pairwise_r` presumes one r per pair.

---

## 3. The four measurement states — `pairwise_r` cannot represent them honestly

| state | has a real `r`? | representable today? |
|---|---|---|
| `NO_EVIDENCE` | No | **Correctly represented by absence.** `append_pairwise_r` refuses a non-real r precisely so "an unmeasurable pair cannot masquerade as a measured one", and §4.1 already makes a missing pair a hard failure |
| `INSUFFICIENT_OVERLAP` | No (or a degenerate one) | same — absence, already fail-closed |
| `INCIDENT_SELECTED_WINDOW` | **Yes** | **No.** A row would carry a real r that `check_validated_independence` accepts at face value |
| valid measurement | Yes | Yes |

The first two need no new machinery: absence already fails closed, and adding
placeholder rows could only create the appearance of coverage (FB-10c-D §4,
unchanged).

The last two are the problem. **They are distinguishable only if the
enforcement path is status-aware.** `check_validated_independence` reads
`r` alone. Adding a `measurement_status` column without changing that function
would record a caveat the gate ignores — governance theatre, strictly worse
than not recording it.

**Conclusion: this is not a schema problem, it is an enforcement-semantics
problem.** The existing one-scalar-per-pair model cannot honestly represent a
segmented, sample-size-qualified contract.

---

## 4. The smallest complete contract before implementation

| field | purpose | schema change? |
|---|---|---|
| `as_of` | point-in-time identity; `ts` must not be overloaded | **Yes** — additive |
| `window_start`, `window_end` | the window the correlation covers | **Yes** — additive |
| `n` | coincident observations; meaningless without it (§1.3) | **Yes** — additive |
| `return_basis` | forward trade returns (FB-10c-impl R-1) | **Yes** — additive |
| `alignment_method` | complete-case vs zero-fill; two legitimate conventions coexist (FB-10c-D §1) | **Yes** — additive |
| `measurement_status` | valid vs incident-selected | **Yes** — additive, **and insufficient alone** (§3) |
| **`segment`** | pre-gap / post-gap identity, required by §2.1 | **Yes** — additive; **not in the gate's list, and mandatory** |

Schema changes are required, are permitted (FB-10c-D §5: append-only triggers
are `BEFORE UPDATE`/`DELETE` and do not fire on `ADD COLUMN`;
`get_pairwise_r` selects explicit named columns; 0 rows), and are **not
sufficient**. Completing the contract additionally requires:

- a frozen `min_pairwise_n` (§1.4);
- an n-aware and status-aware `check_validated_independence`;
- a rule for which segment governs promotion (§2.3.5).

The latter two modify **frozen lifecycle semantics** and are outside anything
authorized to date.

---

## 5. Is FB-10c implementation authorized?

**No.** Four independent blockers, any one sufficient:

1. **B-2 unresolved** — the minimum n is a required new frozen constant whose
   value is a governance choice.
2. **B-3 partially open** — pooling is settled (inadmissible), but the
   segment-governance rule is not, and `max_pairwise_r` presumes one r per pair.
3. **Enforcement change required** — n-awareness and status-awareness in
   `check_validated_independence`, plus a `segment` concept the model lacks.
   Frozen lifecycle semantics; not authorized.
4. **Nothing admissible exists to measure.** Every segment of every pair sits
   at or below the degeneracy boundary, and every *pre-registered* pair still
   lacks a counterparty entirely (`h6`: 0 closed trades; `g5_trend_4h`: not
   collected).

Blocker 4 is the one that matters most: even if 1-3 were closed today,
implementation would produce **zero admissible measurements**.

---

## 6. Recommended next gate

**Do not implement, and do not open a further pairwise specification gate.**
The contract is now specified as far as evidence permits; what remains is a
value judgment plus a wait for sample size.

**Recommended: defer pairwise entirely, on an explicit, checkable trigger.**

Post-gap the families are Thursday-synchronised, accruing roughly **one
coincident observation per pair per week** (2-3 accrued between 2026-08-06 and
2026-08-30). Projected, contingent on collection continuing and cadences
staying aligned:

| target post-gap segment n | approximate date |
|---|---|
| 10 | early November 2026 |
| 15 | mid-December 2026 |

**Recommended trigger:** revisit when the leading pair's post-gap segment
reaches the n that FB-10c-G's successor freezes. Until then pairwise
enforcement remains vacuous anyway — there are still **0 Validated families**,
so no promotion is being blocked by this deferral.

**When it is revisited, one decision gate should close all of it at once:**
`min_pairwise_n`, the segment-governance rule, and authorization to make
`check_validated_independence` n- and status-aware — then implementation.
Splitting these further would produce more specification against an unchanged
corpus.

**Explicitly not recommended:** pooling across INC-0005 to reach n = 7, or
reverting to positions/zero-fill for a comfortable n. Both are foreclosed by
frozen evidence (FB-10c-R, FB-10c-D §1, §2.1 here).

---

## 7. Summary

| item | status |
|---|---|
| B-2 minimum n | **OPEN — new frozen constant required**, value not chosen; check must also become n-aware |
| B-3 pooling across INC-0005 | **RESOLVED — inadmissible** (frozen binding constraint) |
| B-3 segment governance | **OPEN** — no rule for which segment governs |
| §20 scope beyond G3 | **OPEN** — small; same condition now measured for all families |
| Four measurement states | **RESOLVED** — absence covers two; the other two need enforcement change, not just schema |
| Schema extension | **Required, permitted, insufficient alone**; must also carry `segment` |
| FB-10c implementation | **NOT AUTHORIZED** |

---

## 8. Trail

| Gate | Outcome |
|---|---|
| FB-10c-R | Basis resolved to forward returns |
| FB-10c-impl | R-1..R-4 resolved; stopped on B-1, B-2 |
| FB-10c-D | B-1 resolved (complete-case); B-3 opened |
| **FB-10c-G** | **This record. B-3 pooling resolved; B-2 needs a frozen constant; implementation NOT authorized** |
| (deferred) | Single decision gate + implementation, on the §6 trigger |
