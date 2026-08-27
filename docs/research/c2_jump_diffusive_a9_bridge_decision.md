# C2 — A9 Direction-Bridge Decision

**Date: 2026-08-27 · Decision: OPTION 3(c) — research feature, NO candidate signal · C2 does NOT proceed to implementation**

No performance statistic of any kind was computed at A9: no Sharpe, return, DSR,
BH-p, hit rate or clone correlation. The lockbox was never read. A8's U1–U5 and
§3 mathematics are preserved exactly and are not touched by this gate.

---

## 0. Provenance verified, and an error found

A8 required an independent re-verification before anything downstream. Performed,
and **it caught a real error in A8's own record**:

- A8 §1 recorded SHA-256 `460c77d5…`. **Wrong.** That is the BNKS 2008
  semivariance paper — C3's source, 251,638 bytes — copied into C2's provenance
  table by mistake.
- The BNS 2006 jumps paper is **`cbdaa3609b5150dcb2bd984d15e7678d581d4af4d699065231885543532af5fe`, 737,510 bytes**.
- An independent re-fetch of the same URL at A9 returned **byte-identical**
  content, and the body text contains "Econometrics of Testing for Jumps",
  "Bipower Variation" and "Barndorff-Nielsen".

**The mathematics is unaffected** — A8 read the correct document; only the
recorded hash was wrong. Corroborated further by the constants recovered from the
paper matching its own printed values (θ ≈ 2.6090, μ₁² ≈ 0.6366). A8 §1 and §9
are corrected, with §9 recording what was wrong and how it surfaced.

## 1. The decision framework

The question is not "which bridge performs best" — that cannot be asked without
performance, and asking it would invert the whole pre-registration discipline.
It is: **which bridge, if any, is a scientifically defensible use of Ĵ?**

Five criteria, applied before any recommendation:

| # | Criterion | Why it is disqualifying if failed |
|---|---|---|
| **F1** | **Source fidelity** — does the bridge use Ĵ as BNS defines it? | A bridge that re-purposes the statistic is not this source's candidate |
| **F2** | **Hypothesis count** — how many *undeclared* claims are added? | Each added claim is a degree of freedom pre-registration exists to close |
| **F3** | **Information origin** — does the directional content come from Ĵ, or from something else? | If from something else, Ĵ is decoration and the candidate is that something else |
| **F4** | **Independence** — from registered families and from C3 | A reformulation is not a new candidate (gate 6A.2's demonstrated failure mode) |
| **F5** | **Contract compatibility** — does it yield `{−1,0,+1}` *honestly*? | Satisfying the interface by invention is explicitly forbidden by this gate |

**F3 is the decisive criterion**, and it is the one that A8's central observation
forces. Ĵ is unsigned. Any directional signal built from it must import its
direction from elsewhere. F3 asks where.

## 2. Option-by-option

### Option 1 — `Ĵ × sign(daily return)`

| | |
|---|---|
| F1 source fidelity | **Partial.** Ĵ is source-exact; `sign(return)` is wholly platform-added and appears nowhere in BNS |
| F2 hypotheses added | **Two, both undeclared**: (i) jump-dominated moves continue *or* reverse — the source takes no position; (ii) the *daily* return sign is the right directional carrier, rather than the sign of the jump itself |
| F3 information origin | **The direction comes entirely from `sign(daily return)`.** Ĵ modulates *when* to act, never *which way*. Strip Ĵ and a directional signal remains; strip the sign and nothing directional is left |
| F4 independence | **Weak.** `sign(daily return)` held 7 days is a short-horizon momentum signal. `sig_momentum`, `sig_trend_continuation` and `sig_trend_1d` already occupy that space. Gate 6A.2 demonstrated exactly this failure — a distinct-looking trend signal that degraded 4 of 4 comparators |
| F5 contract | Yes, natively |
| Extra data / params | None beyond the daily return sign |
| Freezable pre-performance | Yes, but only by *guessing* (i) |

**Assessment: fails F3 decisively, and F4 seriously.** This is a momentum signal
with a jump-activity filter, presented as a jump candidate. Naming matters:
whatever it measures, its directional content is momentum.

### Option 2 — signed jump variation

| | |
|---|---|
| F1 source fidelity | **Fails.** BNS define no signed decomposition. Signing `RV − BV` requires the semivariance apparatus, i.e. a *different paper* (Patton & Sheppard territory). The candidate would no longer rest on the A8 source at all, and U1–U5 would not govern it |
| F2 hypotheses added | One directional claim, plus an entire second source's machinery |
| F3 information origin | Direction comes from the sign decomposition — that is, from C3's construct, not from Ĵ |
| F4 independence | **Fails, twice.** Structurally it is C3's quantity restricted to the jump component. And procedurally, C3's train slice was evaluated at A7, so adopting a signed-variance direction now would be choosing it with knowledge of that result — the contamination A7 recorded as permanent |
| F5 contract | Yes |
| Freezable pre-performance | **No — not honestly.** The relevant evidence already exists and cannot be un-seen |

**Assessment: REJECT.** It changes the source, duplicates C3's construct, and is
procedurally contaminated. Any one of those alone would be sufficient.

### Option 3 — Ĵ as a regime / conditioning gate

| | |
|---|---|
| F1 source fidelity | **Highest of the three.** Ĵ used exactly as BNS built it — a per-day test of whether the path was continuous. No directional claim is bolted on |
| F2 hypotheses added | **Minimal**: "jump-dominated days are a distinct regime", which is the source's own framing rather than an import. Plus one declared operating α |
| F3 information origin | The information *is* Ĵ. Nothing else is smuggled in |
| F4 independence | **Strong.** Nothing registered decomposes variance into continuous and discontinuous parts; the vol families measure *level*, Ĵ measures *composition* |
| F5 contract | **INCOMPATIBLE.** A gate is a classification, not a position. This is the real cost |
| Extra data / params | Operating α — `[PLATFORM-NEW]`, declarable in advance |
| Freezable pre-performance | Yes, cleanly |

**Assessment: the only scientifically defensible use — and the platform cannot
currently express it.**

## 3. Recommendation

**OPTION 3, sub-option (c): C2 is a research feature with no candidate signal
yet. It does NOT proceed to implementation.**

Sub-options (a) and (b) were considered and rejected on specific grounds:

**(a) standalone feature family — rejected.** On this platform `role: feature`
does *not* mean "non-directional". G2, H5, G1 and IVRV are all `feature` and all
emit `{−1, 0, +1}`; the role governs what may be *claimed*, not what is *emitted*.
An unsigned gate does not satisfy that contract, and the `validated.feature`
gate (DSR, BH-p, pairwise r) is defined over forward *returns* on positions. A
gate has none.

**(b) gate attached to an already-defined signal — rejected, for now.** This is
the natural use and has precedent: gate 6A.8-PRE measured G2's value exactly this
way. But as a *candidate* it fails twice. Choosing which signal to gate is a new
free parameter with no principled answer. And it makes C2 non-independently
admissible — its evaluation would be inseparable from the host's, so what is
being tested is the pair, not C2. There is no registry construct for that.

**(c) research feature — recommended.** Honest about what has been established
and what has not. The measure is source-complete: U1–U5 closed against the
primary text, the exact statistic pinned, the aggregation contract defined,
fail-closed behaviour specified. What does not exist is a defensible way to turn
an unsigned day-classifier into a position — and this gate's own instruction is
explicit: *"Do not invent a trading direction merely to satisfy the {−1,0,+1}
interface."*

**This is not REJECT.** Option 1 and Option 2 are rejected; C2 is not. The
measure stands, fully specified, awaiting a platform capability that does not
exist rather than awaiting a better idea.

## 4. Frozen interpretation

Frozen, and it is deliberately **not** a trading direction:

> **Ĵ classifies a completed UTC day as jump-dominated or continuous.** A
> significantly negative Ĵ is evidence the day's path was discontinuous. That is
> the entire claim. C2 asserts nothing about the direction of subsequent
> returns, and no such assertion may be added without a new pre-registration
> stating its economic mechanism in advance.

Everything in A8 §3 (mathematics) and §4 (aggregation, fail-closed) is carried
forward unchanged and remains frozen.

## 5. What remains unresolved

1. **The platform has no contract for an unsigned conditioning variable.**
   This is a *governance architecture* question — should the registry admit a
   family type evaluated other than by forward returns on positions? — not a
   research question. It is out of scope for any A-track gate.
2. **Operating α is undeclared.** BNS give a one-sided test but no significance
   level for a trading context. It must be declared before evaluation, never
   tuned. Not needed until (1) is answered.
3. **Microstructure at 1-minute** (carried from A8 §8.2): if a future gate wants
   the 1m grid, the staggered estimator must be reconsidered *together with it*.
   They were designed as a pair; changing one alone is not source-faithful.

## 6. Governance

| | |
|---|---|
| Registry / ledger | **untouched** — C2 not registered, no entry created |
| `thresholds.yaml`, `collection_config.yaml`, sealed artifacts | **unmodified** |
| Production | **unmodified**; nothing implemented |
| Lockbox 2025-06-01..2026-05-31 | **SEALED — never read by any C2 gate** |
| Performance statistics | **none computed at A8 or A9** |
| C3 | closed at A7; used here only to *exclude* Option 2 |
| A8 U1–U5 and §3 mathematics | **preserved exactly** |
| `n_trials = 34` | unspent, unchanged |

## 7. Trail

| Gate | Outcome |
|---|---|
| A1 | C2 shortlisted |
| A2 | Source located; formulas unreadable by then-available tooling; U1–U5 opened |
| A8 | U1–U5 closed against the primary source; direction found BLOCKED |
| **A9** | **Bridges 1 and 2 rejected; Option 3(c) adopted. C2 does not proceed to implementation.** |
