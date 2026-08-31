# FB-10c-D — Pairwise Measurement Contract: Resolution Record

**Date: 2026-08-31 (UTC) · Gate FB-10c-D · Read-only · Outcome: B-1 RESOLVED
(with a new sub-item opened), B-2 remains OPEN, two further questions
RESOLVED. FB-10c implementation stays BLOCKED.**

No code, no schema change, no measurement, no ledger write, no production
change. `pairwise_r` remains at 0 rows. Only observation *counts* were
computed against production; **no correlation was calculated or stored**.

---

## 1. B-1 — Alignment: RESOLVED, and zero-fill is affirmatively excluded

### 1.1 The repository contains two distinct, both-named alignment conventions

| convention | rule | applies to | named in |
|---|---|---|---|
| `analyze_mtf_independence._arr` | reindex to a common daily index, **`fillna(0.0)`** | **positions** | pre-registrations, `/independence/methodology` |
| `governance.continuous_correlation` | **complete cases** (`dropna()`), Pearson + Spearman | **continuous columns** | the same pre-registration field, immediately after |

Both are named in the same frozen sentence: *"analyze_mtf_independence.py
Test 1 (`_arr` alignment, Pearson on positions, both-std>0 guard) **plus
governance.vif_table / continuous_correlation / high_pairs**"*. They are not
alternatives to choose between by taste — they are separated by **data type**.
Positions are a step function where "absent" genuinely means flat, so `0.0` is
the true value. Returns are continuous, and `continuous_correlation` is the
frozen convention for continuous columns: it drops incomplete rows rather than
inventing values.

**Forward trade returns are continuous. The frozen convention is therefore
complete-case Pearson, not zero-fill.**

### 1.2 Zero-fill is excluded by the repository's own documented defect

`run_final_audit.py` — the source of the **only concrete pairwise number any
pre-registration cites** (`g3_momentum_tsmom` x `g5_trend_4h` at r = 0.712) —
correlates **held positions**, not returns:

```python
positions[sp.name] = _apply_hold(raw, sp.hold_days)   # _arr -> fillna(0.0)
P = pd.DataFrame(positions, index=m.index)
corr = P.corr()
```

and carries this comment, with a hand-maintained exception list:

```python
# correlation (correlation is understated when the signal is sparse).
DISGUISED_DUPLICATE = {
    "Inside Bar": "disguised trend-follower: ... correlation understated by sparsity",
}
```

The authors of the frozen method **already knew** zero-fill understates
correlation for sparse signals, and had to annotate by hand a case it missed.
Asynchronous 7-day trade returns projected onto a daily calendar are the
maximally sparse case — roughly 1 nonzero day in 7 per family, with the
nonzero days deliberately non-coincident. Applying zero-fill there would
maximise a defect the repository documents rather than accept.

This also confirms that the cited 0.712 is a **position** correlation,
consistent with `g1_trend_regime`'s risk R4 and with FB-10c-R's finding that a
position correlation does not establish the forward-return requirement.

### 1.3 The direction of the error is what settles it

| rule | bias under weekday misalignment | effect on a 0.70 ceiling |
|---|---|---|
| zero-fill | understates correlation (documented) | biases toward **PASS** — unsafe |
| complete-case | conditions on joint activity, if anything **overstating** co-movement | biases toward **FAIL** — conservative |

The gate's stated requirement — *the method must not manufacture low
correlation merely because two families trade on different weekdays* — is met
by complete-case and violated by zero-fill. Complete-case never injects a
synthetic observation; it reduces n instead of fabricating agreement.

### 1.4 A sub-ambiguity closes itself

`hold_days_actual` is **7 for all 31 closed trades** (verified on production).
Entry-date and exit-date indexing therefore select identical pairs, and the
choice of index is immaterial. Point-in-time filtering remains on `exit_date`
per FB-10c-impl R-2.

### 1.5 No frozen method defines overlap-based pairing

Searched: no artifact anywhere in the repository pairs observations by
overlapping holding windows. Overlap pairing would have to be invented, and is
therefore excluded — not rejected on merit, simply absent from frozen
evidence.

**B-1 verdict: RESOLVED.** Complete-case (pairwise-complete) Pearson on
coincident trade dates, `both-std > 0` guard, comparison on `|r|`.

---

## 2. NEW OPEN ITEM B-3 — INC-0005 did not shift one family, it converged all of them

The gate required testing consequences against the real corpus including the
INC-0005 cadence shift. Doing so surfaced something CLAUDE.md §20 does not
record. §20 documents a **G3** Monday-to-Thursday shift. In fact **every**
family re-anchored across the outage:

| family | entry weekday before gap | after gap |
|---|---|---|
| `g1_market_regime_sma200` | Mon | **Thu** |
| `g3_momentum_tsmom` | Mon | **Thu** |
| `h5_vol_regime` | Tue | **Thu** |
| `ivrv_vrp_spread` | Fri / Mon / Tue | **Thu** |
| `g3_atr_expansion` | Mon | Wed |

The 7-day cycle re-anchored to the resumption weekday, so the families were
mutually **asynchronous before** the outage and are mutually **synchronous
after** it.

Coincident-entry-date observation counts, split at the gap:

| pair | all | pre-gap | post-gap |
|---|---|---|---|
| `g1_market_regime_sma200` x `g3_momentum_tsmom` | 7 | 5 | 2 |
| `g3_momentum_tsmom` x `ivrv_vrp_spread` | 4 | 1 | 3 |
| `h5_vol_regime` x `ivrv_vrp_spread` | 4 | 1 | 3 |
| `g1_market_regime_sma200` x `ivrv_vrp_spread` | 3 | 1 | 2 |
| `g3_momentum_tsmom` x `h5_vol_regime` | 3 | **0** | 3 |
| `g1_market_regime_sma200` x `h5_vol_regime` | 2 | **0** | 2 |
| `g1_market_regime_sma200` x `g3_atr_expansion` | 2 | 2 | 0 |
| `g3_atr_expansion` x `g3_momentum_tsmom` | 2 | 2 | 0 |
| `g3_atr_expansion` x `ivrv_vrp_spread` | 1 | 1 | 0 |
| `g3_atr_expansion` x `h5_vol_regime` | **0** | 0 | 0 |

**Every `h5_vol_regime` pair has zero pre-gap coincident observations.** Under
the resolved complete-case rule, a pairwise r for h5 against `g1` or `g3`
would rest **entirely on post-outage data** (n = 2-3), silently discarding
h5's entire pre-outage history.

This is not the manufactured-independence hazard of §1 — it is a **sample
selected by an operational incident** rather than by research design.
CLAUDE.md §20 requires the gap be carried explicitly in any analysis but does
not say how for a pairwise measurement, and no other frozen artifact does
either.

**Decision required:** whether a pairwise correlation may rest on a window
whose composition was determined by the INC-0005 outage, and if so how the
pre/post segmentation must be recorded.

---

## 3. B-2 — Minimum n: OPEN

Searched exhaustively — all eight pre-registrations (every string field, not
just the independence blocks), `thresholds.yaml`, `lifecycle.py`,
`record.py`, `governance.py`, `analyze_mtf_independence.py`, the governance
documents, and the test suite. **No frozen pairwise minimum sample size
exists anywhere.** The only guard in the named methodology is `both-std > 0`.

The three minima that *are* frozen are each explicitly something else, and
FR-5 (FB-10a-R) forbids conflating them:

| minimum | value | kind | scope |
|---|---|---|---|
| computational guard | n >= 5 | mathematical | DSR / PSR only |
| statistical sufficiency | n >= 10 / 15 | governance | DSR / BH only |
| observation floor | 26-30 trades | governance | FB-7 admissibility |

None is connected by any frozen statement to pairwise correlation, so none may
be reused for it.

Two facts make this binding rather than academic. Achievable n under the
resolved contract is **0-7**, and `check_validated_independence` **never reads
n** — it sees only `r`. A correlation on n = 2 would therefore satisfy the
0.70 ceiling indistinguishably from one on n = 200, and under §4.1's
fail-closed-on-missing rule it would additionally *discharge* the coverage
obligation it cannot honestly meet.

**Decision required:** the minimum n at which a pairwise measurement is
admissible as governance evidence, and whether `check_validated_independence`
must be made n-aware to enforce it.

---

## 4. Unmeasurable counterparties: RESOLVED — record nothing

Confirmed on production:

| pre-registered pair | counterparty status | usable forward-return series |
|---|---|---|
| `h5_vol_regime` x `h6` | h6 legacy corpus: 75 signal rows, **0 closed trades** | No |
| `g1_market_regime_sma200` x `h6` | same | No |
| `g3_momentum_tsmom` x `g5_trend_4h` | **not collected forward at all** | No |
| `g1_market_regime_sma200` x `g1_trend_regime_ma_cross` | **0 closed trades** | No |

**Every currently pre-registered structural pair lacks two usable
forward-return series.**

The question of whether to record an explicit `UNEVALUABLE` / `NO_EVIDENCE`
measurement is **settled by frozen evidence**, and the answer is no.
`append_pairwise_r` already refuses a non-real `r`, with its rationale stated
in the code:

> *"A NaN correlation is not a measurement -- it is a failed computation, and
> recording it would let an unmeasurable pair masquerade as a measured one."*

That is precisely the case at hand. Two further considerations agree:

- **Absence already fails closed.** Schema reference §4.1 makes a missing pair
  against a Validated peer a hard failure. There is no enforcement gap for a
  placeholder row to fill, and a row that *looked* like coverage could only
  weaken the gate.
- **FB-9 set the precedent for this exact situation**: `write_snapshot`
  returns `'no-evidence'` and writes **nothing** when a family has no closed
  trades, because a row asserting otherwise would make the evidence view
  report something untrue.

FB-7's `UNEVALUABLE` is not a counter-precedent: it is a **floor status inside
a payload** on a run that genuinely exists, not a measurement row asserting a
pair was measured.

**Verdict: pairwise implementation is blocked for these pairs until the
counterparties have forward evidence. No placeholder row is written.**
Adding `h6` and `g5_trend_4h` to forward collection is a capture-path
expansion — gated, and out of scope here.

---

## 5. Schema extension: nothing frozen prevents it

Verified against the live production schema:

- The append-only triggers are `BEFORE UPDATE` and `BEFORE DELETE`. **Neither
  fires on `ALTER TABLE ... ADD COLUMN`**, so the extension does not
  circumvent or weaken append-only enforcement.
- `CHECK (family_id_a < family_id_b)` is unaffected by added columns.
- `get_pairwise_r()` issues `SELECT id, ts, family_id_a, family_id_b, r,
  method, actor` — **explicit named columns**, so appended columns cannot
  break it or `check_validated_independence`.
- The table has **0 rows**, so the PR-4 `CHECK`-on-`ALTER` validation
  asymmetry (CLAUDE.md §17) is moot.

Proposed additive, nullable extension:

```sql
ALTER TABLE pairwise_r ADD COLUMN as_of            TEXT;
ALTER TABLE pairwise_r ADD COLUMN window_start     TEXT;
ALTER TABLE pairwise_r ADD COLUMN window_end       TEXT;
ALTER TABLE pairwise_r ADD COLUMN n                INTEGER;
ALTER TABLE pairwise_r ADD COLUMN return_basis     TEXT;
ALTER TABLE pairwise_r ADD COLUMN alignment_method TEXT;
```

`alignment_method` is now justified rather than speculative: §1 establishes
that two legitimate conventions exist in this repository for different data
types, so a row that does not name which one produced it is not replayable.

**Still not applied.** `n`'s admissibility floor (B-2) and the INC-0005
window rule (B-3) are unresolved, and both would be encoded in these columns.

---

## 6. "Computable today" versus "valid under the frozen contract"

The distinction the gate asked to be strict about, stated plainly:

| basis | n available today | valid under frozen contract? |
|---|---|---|
| daily positions | 45-58 | **No** — FB-10c-R: positions do not establish G3 |
| daily zero-filled returns | ~58 | **No** — §1.2, documented sparsity understatement; biases toward passing |
| **complete-case trade returns** | **0-7** | **Yes** — but n is unresolved (B-2) and window is outage-selected (B-3) |

Every basis that yields a comfortable sample size is invalid; the only valid
basis yields between 0 and 7 observations. That inversion is the whole
finding. A measurement is available today only by adopting a rule the frozen
evidence excludes.

---

## 7. Resolved / unresolved summary

**Resolved (from frozen evidence):**

- **R-1** (FB-10c-impl) basis = forward trade returns.
- **R-2** point-in-time `exit_date <= as_of`.
- **R-3** pre-emptive, operator-invoked production.
- **R-4** cluster-head rule is not producer scope.
- **B-1** alignment = complete-case Pearson on coincident trade dates;
  zero-fill affirmatively excluded; entry/exit indexing immaterial
  (`hold_days_actual` uniformly 7); overlap pairing absent from frozen
  evidence.
- **§4** no `UNEVALUABLE` / `NO_EVIDENCE` row is ever written to `pairwise_r`.
- **§5** no frozen rule prevents the additive schema extension.

**Unresolved (governance decisions required):**

- **B-2** minimum n for admissibility, and whether the check must become
  n-aware.
- **B-3** whether a pairwise r may rest on an INC-0005-selected window, and
  how pre/post segmentation is recorded.

---

## 8. Is FB-10c implementation still blocked?

**Yes — on three independent grounds, any one of which is sufficient.**

1. **B-2 open.** Achievable n is 0-7 and the check is n-blind; without an
   admissibility floor a noise correlation would satisfy the ceiling.
2. **B-3 open.** The only samples that exist for several pairs are entirely
   post-outage.
3. **No pre-registered pair is measurable at all** (§4), so even a fully
   specified producer would today emit nothing that any pre-registration asks
   for.

---

## 9. Recommended next gate

**FB-10c-G — Pairwise Admissibility Decision (decision-only, no code).**
Close exactly B-2 and B-3, and nothing else:

1. Minimum n for an admissible pairwise measurement, stated as a governance
   criterion distinct from the three existing minima.
2. Whether `check_validated_independence` becomes n-aware, or n remains
   advisory and recorded only.
3. Whether an INC-0005-spanning window is admissible, and the required
   pre/post recording.

Only after both are closed should **FB-10c-impl** resume, in this order:
apply the §5 schema extension, implement the producer, then measure — noting
that measurement of any pre-registered pair additionally requires a separate,
gated decision to bring `h6` and `g5_trend_4h` into forward collection.

**Explicitly not recommended:** widening the basis to positions or zero-filled
returns to obtain a workable n. That is the one move this record exists to
foreclose.

---

## 10. Trail

| Gate | Outcome |
|---|---|
| FB-10c | Read-only audit; basis conflict and zero-peer vacuity found |
| FB-10c-R | Basis RESOLVED to forward returns; O-1..O-6 open |
| FB-10c-impl | R-1..R-4 resolved; STOPPED on B-1 and B-2 |
| **FB-10c-D** | **This record. B-1 resolved, B-3 opened, B-2 still open; UNEVALUABLE and schema questions resolved** |
| FB-10c-G | Recommended next — closes B-2 and B-3 |
| FB-10c-impl (resumed) | Blocked pending FB-10c-G |
