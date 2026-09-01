# FB-10b-R — FINAL DSR/BH HUMAN DECISION FORM

**Date: 2026-08-31 (UTC) · Gate FB-10b-R · READ-ONLY.**

No DSR or BH was computed. No lockbox was read or spent. No pre-registration,
`registry.yaml`, `thresholds.yaml`, `governance.py`, `ledger_sync.py`, ledger,
production host or scheduler was modified. No producer was implemented.

**No option is recommended, selected, or ranked anywhere in this document.**

Three items remain. Everything else is closed (§4).

---

## 1. OD-4 — BH statistic semantics

### 1.0 The situation, stated exactly

`runs.bh_p` is a `REAL` column. `lifecycle.check_validated_preconditions`
compares `pinned_run["bh_p"] <= max_bh_p`, where `thresholds.yaml` sets
`validated.{edge,feature}.max_bh_p = 0.10` and, separately, `governance.fdr_q =
0.10` (commented *"Must match governance.bh_fdr default q parameter"*). Two
distinct declarations that coincide numerically.

`governance.bh_fdr()` returns, per name, a **structure** —
`{p, rank, bh_threshold, reject}` — and **no adjusted p-value. No function in the
repository produces one.** The frozen contract requires a **scalar**.

Verified this gate: the word *"adjusted"* appears nowhere in the FB-10 chain in a
BH sense. FDB-9 requires recording `q = 0.10` and `psr0`; FB-10a §4 says a
recorded BH-p means *"the BH-p of this family, under this battery, at this
`as_of`"*. **None of them states the number.**

### 1.1 Definitions compatible with the adopted contract — there are two

**D1 — `bh_p` = the raw per-family p-value, `p = 1 − psr(returns, 0.0)`.**

| | |
|---|---|
| Exact meaning | one minus the Probabilistic Sharpe Ratio against a zero benchmark: the probability that this family's true Sharpe is **not** above zero, given its own observed skew and kurtosis |
| Code needed | **none.** `governance.psr()` already returns it; `bh_fdr()` still runs to freeze `rank` / `bh_threshold` / `reject` for the record |
| Existing precedent | the **only** storage precedent: `compute_dsr_for_registration.py`, and therefore the `evidence_basis.bh_p` values already inside the pre-registrations (e.g. `ivrv_vrp_spread` = 0.2893) |
| Changes frozen methodology? | **No.** `governance.py` untouched |
| Amendment required? | **Yes — a clarifying one.** The gate would compare a *raw* p to `q`, so the BH step-up never enters the decision: `rank`, `bh_threshold` and `reject` would be frozen for audit but **read by nothing**, and FDB-5's battery membership would have **no gating force**. FDB-5/FDB-9 must say so explicitly, or the contract reads as though membership gates something it does not |

**D2 — `bh_p` = the Benjamini–Hochberg adjusted p-value (q-value).**

| | |
|---|---|
| Exact meaning | for ordered `p_(1) ≤ … ≤ p_(m)`, `adj_(i) = min over k ≥ i of min(1, (m/k)·p_(k))` — the smallest FDR level at which this family would be rejected. `adj ≤ q` is **exactly equivalent** to BH's rejection rule |
| Code needed | one **additive** sibling function in `governance.py`. `bh_fdr()` stays **byte-identical**, so every existing caller and every already-stored historical value is unaffected |
| Existing precedent | **none** — this statistic has never been computed in this repository |
| Changes frozen methodology? | **`governance.py` gains a function.** `psr`, `deflated_sr` and `bh_fdr` are untouched. But it changes **what the Validated gate tests** — from a per-family p ceiling to genuine FDR control |
| Amendment required? | **Yes.** `governance.py` is frozen as the sole statistical authority (FDB-11); an addition needs explicit authorization, and FDB-9 must name the new quantity |

### 1.2 Definitions that are NOT compatible — recorded so they are not revisited

| candidate | why it is excluded |
|---|---|
| `bh_p` = `bh_threshold` | `bh_threshold = (i/m)·q ≤ q` **by construction, for every member**. Stored in `bh_p`, the gate `bh_p <= 0.10` would pass **unconditionally**. Fail-open |
| `bh_p` = `reject` | a boolean in a `REAL` column: `reject = False → 0.0`, and `0.0 <= 0.10` is **True**, so a family BH *rejected nothing for* would **pass**. Fail-open, and inverted |

Neither is a p-value, and **neither may be called an adjusted p-value.**

### 1.3 Whether the two live definitions actually differ

Structurally, yes. BH's rank-1 threshold is `q/m`, so any family whose raw p falls
in `(q/m, q]` passes D1 and is rejected by D2 unless a higher rank qualifies. For
a six-member battery at `q = 0.10` that band is `(0.0167, 0.10]`.

Checked against the frozen `harmonized_library.json` battery (m = 6): the two
readings **happen to agree on all six** there — no member fell inside the band.
The divergence is therefore **structural, not exhibited by that artifact**, and
its realisation depends entirely on the battery that eventually forms.

### 1.4 The exact human decision required

> **Does `bh_p` hold D1 (the raw per-family p-value) or D2 (the BH-adjusted
> p-value)?**
>
> If **D1**: authorize the clarifying amendment stating that battery membership
> is recorded for reproducibility and is **not** gating.
> If **D2**: authorize one additive function in `governance.py`, leaving
> `bh_fdr()` byte-identical.

---

## 2. OD-2 — Incomplete 34-member battery

### 2.0 A determined clarification that narrows the exposure

FDB-1 requires the **per-period Sharpe ratios** of the 34 — not their PSR. A
Sharpe is `mean / std(ddof=1)`, which exists whenever **n ≥ 2 and std > 0**.

`n >= 5` is **not** the bar here: FDB-8 ties it explicitly to `psr` (*"`psr`
returns `NaN` below it"*), and `psr` is applied to a **family's observed
returns**, never to a dispersion member's Sharpe.

So *"cannot produce a valid Sharpe"* means precisely **n < 2, or zero
dispersion** — a materially weaker failure condition than FB-10b-P implied. Two
consequences: the exposure is smaller than feared, and the correct member-Sharpe
computation is a direct `mean/std`, which yields a NaN or a divide-by-zero rather
than `_sr_stats`' silent `0.0`. *(This refines, not contradicts, FB-10b-Q's
forbidden-inference #3: the silent zero is only reachable if a producer wrongly
routes a dispersion member through `psr`/`_sr_stats`.)*

Whether any of the 34 actually fails **cannot be established without running the
OD-1 construction**, which this gate forbids.

### 2.1 Legitimate treatments

**T1 — battery-level refusal (UNEVALUABLE).** The producer computes no
dispersion, no DSR for anyone, and writes nothing.

*Frozen support (three independent precedents, all pointing the same way):*
`observation_floors` — *"An UNEVALUABLE floor blocks exactly as an unmet one
does … absence must never be satisfiable by recording nothing"*; PR-7's DQ
framework — `UNKNOWN → BLOCK`, and L2 *"absence of evidence of completeness is
not evidence of completeness"*; R1's NULL discipline — a NULL governance stat is
a hard failure with no silent bypass.

| affects | consequence |
|---|---|
| `n_trials = 34` | **untouched** — never modified, never read in a degraded form |
| `sr_trials_std` | **never computed**; no partial or 33-member value is ever formed |
| DSR | **not computed for any family**, including families that are individually fine |
| BH membership | the battery **never forms**; no `bh_p` for anyone |
| Reproducibility | **perfect** — nothing is recorded, so nothing can later be found to rest on an invented value |

**T2 — any other treatment.** **None exists with frozen support.** Dropping the
member is barred by FDB-1 (*"Neither may be changed to accommodate the other"* —
a 33-member dispersion against `n_trials = 34` is exactly that divergence);
injecting `0.0` invents a value; substituting another statistic is barred by
FDB-2 (*"no scaling conversion is applied"*) and FDB-6 (no approximation). This is
reported as an absence of options, not manufactured into a second choice.

### 2.2 The consequence the human must weigh

**The refusal is permanent, not transient.** The train slice
`2021-01-01 … 2025-05-31` is frozen, so a member uncomputable on it is
uncomputable **forever**; retrying later changes nothing. Under T1, if **one** of
34 legacy signals fails, forward DSR is permanently unavailable to **every**
family, and under R1 no family can ever reach Validated — on account of a signal
unrelated to the family being judged.

The only alternative to accepting that is a **governance amendment** — to FDB-1
(define a treatment) or to FDB-2/FDB-2a (change the basis or window so all 34 are
computable). Both reopen frozen clauses. Neither is a "treatment"; both are acts.

### 2.3 The exact human decision required

> **Is T1 accepted as the standing rule — with its permanence — or is an
> amendment to FDB-1 / FDB-2 / FDB-2a opened instead?**
>
> Optionally decidable on fact rather than on risk: authorize a read-only
> diagnostic that applies the OD-1 construction to the 34 on the train slice and
> reports **trade counts only** — no Sharpe, no dispersion, no `psr`, no
> `deflated_sr`, no `bh_fdr`, no write.

---

## 3. OD-5 — Snapshot / idempotency ownership

### 3.0 Closed at FB-10b-Q, not reopened here

Three independent readers each resolve **one** run — `lifecycle.py:439-459`,
`transition_governance.py:283/336/341` (the same `run` object feeds TG-PIN,
TG-RDY and TG-PRE), and `evidence_governance.admit_evidence`. So **a single row
must carry L2 lane + floors payload + readiness block + non-NULL
`dsr_prob`/`bh_p`**, and DSR/BH therefore belongs on the **`forward_snapshot`
row**. No second producer, no competing row, key `(family_id, 'live-forward',
window_end)` unchanged.

### 3.1 The remaining problem: the collector pre-empts the date

`ledger_sync._existing_live_forward_run()` matches on `(family_id, provenance,
window_end)` and **ignores `engine_version`**, and `run_daily.py:683` calls
`sync_all()` **every night** — writing for exactly the families that have closed
trades, i.e. the only families that could ever be battery members
(`sync_family` writes nothing otherwise).

**Therefore `as_of = yesterday` is always already taken for any eligible family,
and a snapshot could only ever be written for a date the collector skipped —
which is precisely a date with no evidence to compute on.** Verified on
production: ten `forward-collect-v1` rows at `window_end` 2026-08-29 / 2026-08-30;
FB-12k's single `forward-snapshot-v1` row sits at 2026-08-28, a date the
collector had not covered.

### 3.2 Smallest legitimate integrations

**C1 — operator selects an `as_of` the collector has not covered.**
No code change anywhere; `ledger_sync` untouched; key untouched. Works today
(most historical dates are free). Degrades continuously as nightly coverage
accumulates, and an older `as_of` carries **fewer** closed trades — working
directly against reaching a DSR minimum. Avoidance rather than resolution.

**C2 — `forward_snapshot` uses its own engine-scoped existence probe.**
A snapshot-local probe additionally matching `engine_version =
'forward-snapshot-v1'`, added **inside `forward_snapshot.py`**;
`ledger_sync._existing_live_forward_run()` is **not modified** and the collector's
own idempotency is unchanged. Consequence: two L2 rows may share one
`window_end`. Promotion is unaffected — pinning is by explicit `run_id`
(`registry.yaml pinned_run` → `record.get_run`), never by the view. But
`current_run_per_lane` orders by `run_ts DESC, run_id DESC`, so **the next
night's collector row — which carries NULL DSR — becomes the "current" L2 run for
reporting.** Presentational, not gating; it must be stated, not discovered later.

**C3 — the collector stands down for a family/date that already has a snapshot
row.** Would require modifying `ledger_sync.sync_family()`, which this gate
forbids, and changes unattended collector behaviour — the largest blast radius.
**Listed for completeness only; not available under this gate.**

### 3.3 The exact human decision required

> **C1, C2, or open C3 for separate scoping?** C2 additionally requires
> accepting that `current_run_per_lane` may surface the collector's NULL-DSR row
> as current while the pinned row remains correct.

---

## 4. FINAL HUMAN DECISION TABLE

| | **OD-4 — BH meaning** | **OD-2 — incomplete member** | **OD-5 — write ownership** |
|---|---|---|---|
| **Decision required** | what number `bh_p` holds | accept T1's permanence, or amend | how to avoid the collector pre-empting the date |
| **Available choices** | **D1** raw p · **D2** BH-adjusted q-value | **T1** battery-level refusal · **amend FDB-1** · **amend FDB-2/2a** | **C1** uncovered `as_of` · **C2** engine-scoped probe · **C3** (needs separate scoping) |
| **Frozen constraints** | FDB-9 (`q`, `psr0` recorded); FDB-11 (`governance.py` sole authority); `max_bh_p = 0.10`; `fdr_q = 0.10`; `bh_p` is a scalar `REAL` | FDB-1 (population fixed at 34, may not diverge from `n_trials`); FDB-2 (no conversion); FDB-6 (no approximation); R1 NULL discipline | FDB-10 (append-only); FDB-11 (appends, never rewrites); one row must carry all four payloads; `ledger_sync` not modifiable here |
| **Irreversible consequence** | D1 makes FDB-5 membership **non-gating**; D2 changes what the Validated gate tests. Either way, every stored `bh_p` is permanent and interpreted under the definition in force when written | T1 is **permanent** — the train slice is frozen, so one failing member blocks forward DSR for **all** families, forever | rows are append-only: a row written under C1 or C2 can never be withdrawn, only superseded |
| **Files / contracts to amend** | **D1:** this contract (FDB-5/FDB-9 clarification) only. **D2:** `research/evidence/governance.py` (additive) + FDB-9 | **T1:** a governance statement only. **Amend:** `fb10b_forward_dsr_bh_contract.md` FDB-1 and/or FDB-2/FDB-2a | **C1:** none. **C2:** `research/forward_evidence/forward_snapshot.py` only. **C3:** `ledger_sync.py` — out of scope |
| **Lockbox spending?** | **No** | **No** — the train slice is the non-lockbox window (FDB-2a); the optional diagnostic is also lockbox-free | **No** |
| **Implementation may begin after?** | **Not alone** — OD-2 and OD-5 must also be decided | **Not alone** | **Not alone** |

**Implementation may begin only when all three are decided.** OD-4 gates what is
computed, OD-2 gates whether it may be computed at all, OD-5 gates where it is
written. Sample size remains an **independent execution condition**: the battery
is empty today (7 / 7 / 8 / 6 / 3 / 0 / 0 closed trades against minima
15 / 10 / 15 / 30 / 26 / 30 / 30), so deciding all three unblocks implementation,
not computation.

---

## 5. Already CLOSED — do not reopen

| item | status | basis |
|---|---|---|
| **OD-1 — trial construction** | **CLOSED** | `runner_base.run_signal()` 5a/5b + the `trade_returns` view, on the train-sliced master: signed, non-overlapping (`_has_open_trade`), unconditional 7-calendar-day exit, one flat cost per trade, close-to-close. Applicable to all 34 — positions verified `{-1, 0, +1}` at every assignment site. Mechanically determined by FDB-2's phrase *"the forward trade-return basis"* (FB-10b-Q §OD-1) |
| **FDB-1 — population** | **CLOSED** | the 34 Phase B library signals, fixed, identical to what `n_trials` counts; name-set verified identical to `evidence_library.json`. *Only the treatment of an uncomputable member is open (OD-2) — the population itself is not* |
| **FDB-2 / FDB-2a — basis and window** | **CLOSED** | forward trade-return basis, `hold_days = 7`, `cost_bps = 15`, no conversion, `sqrt(7.0)` rejected; window `2021-01-01 … 2025-05-31`, lockbox untouched |
| **FDB-5 — membership rule** | **CLOSED as a rule** | explicitly CONFIRMED by human decision at FB-10b-O. *Its gating force* — whether membership changes any verdict — is a consequence of OD-4, not a reopening of the rule |
| **FDB-8 — the seven minima** | **CLOSED** | 15 / 15 / 10 pre-existing; 30 / 30 / 26 / 30 adopted at FB-10b-O in Form 1 (prose). Machine-unreadability was declared before the choice and is the decision's own content (OD-3, closed at FB-10b-Q) |
| **`n_trials = 34`** | **CLOSED** | frozen; `38` was the discovery screen, retired by `n_trials_denominator_reconciliation.md` |
| **Forward trade-return basis** | **CLOSED** | as OD-1 |
| **Train-slice window** | **CLOSED** | as FDB-2a; slice applied **before** positions are computed, so rolling windows never read lockbox rows |
| **OD-3 — minima representation** | **CLOSED** | no machine-readable form exists or may be manufactured; operator-supplied at invocation with no defaults, frozen into the append-only row via `battery_members[].minimum` / `battery_excluded[].reason`, bound to `prereg_path` + verified `prereg_sha256`, cross-checked only by an offline test |
| **OD-5 core — DSR/BH placement** | **CLOSED** | the `forward_snapshot` row; no second producer; key unchanged. *Only the date-collision question is open* |

---

## 6. Status

**FB-10b implementation remains UNAUTHORIZED until OD-4, OD-2 and OD-5 are
explicitly decided.**

Nothing was computed, written, deployed or modified. No lockbox was accessed.

**STOP.**

---

## 7. Trail

| Gate | Outcome |
|---|---|
| FB-10b-O | FDB-8 Option A adopted (Form 1); FDB-5 confirmed; FDB-1…FDB-12 approved |
| FB-10b-P | Producer specified; 4 blockers + 2 open questions raised |
| FB-10b-Q | OD-1, OD-3, OD-5-core resolved mechanically; OD-1 shown not to be a blocker |
| **FB-10b-R** | **This form. Three decisions remain: OD-4, OD-2, OD-5-collision. Nine items recorded CLOSED** |
