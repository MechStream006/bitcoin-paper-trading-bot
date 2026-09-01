# FB-10b-Q — DSR/BH Producer Open-Decision Resolution

**Date: 2026-08-31 (UTC) · Gate FB-10b-Q · READ-ONLY.**

No DSR or BH was computed. No lockbox was read or spent. No pre-registration,
`registry.yaml`, `thresholds.yaml`, `governance.py`, ledger, production host or
scheduler was modified. No producer was implemented.

Resolves the five open decisions raised by
`docs/governance/fb10b_p_forward_dsr_bh_producer_specification.md` against the
adopted contract `fb10b_forward_dsr_bh_contract.md` (FDB-1 … FDB-12).

---

## 0. Correction to FB-10b-P — OD-1 was not a blocker

FB-10b-P presented three candidate trade constructions and said the contract
"names none of them". **That was wrong, and it inflated the blocker count.**

FDB-2 names the basis explicitly: *"re-measured at **the forward trade-return
basis**"*. The forward trade-return basis is not one of three equal candidates —
it is a single, frozen, already-implemented algorithm, and the other two are the
*offline* constructions that FDB-2's next sentence excludes. The ambiguity was
mine, not the contract's.

**Blockers therefore go from four to two: OD-4 and OD-2.**

---

## OD-1 — Forward 7-day trial construction

### 1. Frozen evidence

FDB-2: *"the 34 library Sharpes are re-measured at the **forward trade-return
basis** — sequential, non-overlapping 7-day evaluation windows, `hold_days = 7`,
`cost_bps = 15` — so the dispersion is natively per-trade."*

FDB-3: *"A trade **is** a non-overlapping 7-day block; no conversion between the
two descriptions exists or is permitted."*

FDB-4: dispersion, `sr_star` and observed returns must all be per-trade.

### 2. Existing authoritative implementation

The forward trade-return basis has **exactly one** implementation, split across
two frozen artifacts: `runner_base.run_signal()` steps 5a/5b (trade construction)
and the `trade_returns` VIEW in `schema_forward.sql` (return computation).
`runner_base` is content-hashed into `run_provenance.framework_hash`; the view is
schema.

Every descriptor in FDB-2's dash-clause maps onto it one-to-one:

| FDB-2 wording | mechanism | site |
|---|---|---|
| *sequential* | 5a closes overdue trades **before** 5b opens a new one, in the same transaction, so a trade may exit and the next open on the same `run_date` — back to back, no gap | `runner_base.py:820-852` |
| *non-overlapping* | `if not _has_open_trade(conn, signal_id) and position != 0.0` — at most one open trade per signal, ever | `runner_base.py:626-630, 841` |
| *7-day evaluation windows* / `hold_days = 7` | `planned_exit = date.fromisoformat(run_date) + timedelta(days=7)`; exit is **unconditional** and does **not** depend on signal reversal | `runner_base.py:844-846`, 5a |
| signed long/short | `direction = int(position)`; `CHECK (direction IN (-1, 1))` | `runner_base.py:842`; `schema_forward.sql` |
| `cost_bps = 15` | charged **once per trade** as a flat deduction, not as per-side turnover | `trade_returns` view; `collection_config.yaml` |
| per-trade return | `(exit_close/entry_close - 1) * direction - cost_bps/10000` — close-to-close, **not** compounded daily nets | `trade_returns` view |

**The construction, stated once:** slice the master to `2021-01-01 … 2025-05-31`
(`m_full[m_full["day_ts"] < LOCKBOX_START_MS]`, the `run_harmonization.py:103`
convention) **before** computing positions; compute the spec's daily position
series on the sliced frame; walk the days applying 5a-then-5b; price each trade
close-to-close; net return per the view.

**Applicability verified.** All 38 `sig_*` functions emit values only in
`{-1.0, 0.0, +1.0}` — established by inspecting every assignment site in
`signals.py` (literals are only `0.0`/`±1.0`, plus `np.sign(...)`), and by
tracing the three column pass-throughs `sig_trend_1h/4h/1d` back to
`engine._tf_trend_daily` and `td`, which assign the same three values. So
`position != 0.0 → direction = int(position) ∈ {-1, +1}` holds for all 34 and
satisfies the schema CHECK. **Verified by source inspection, not by execution** —
executing them is a computation this gate forbids.

**The two rivals are excluded by the contract's own words, not by preference:**

- `engine._runs` on `_apply_hold(pos, 7)` — produces **variable-length** runs (a
  fresh signal during a hold *resets the clock*, per the `_apply_hold` docstring),
  which is not "non-overlapping 7-day". FDB-2 separately excludes the stored
  `hold_days = 1` measurements outright.
- `run_harmonization._h6_native_trades` / `run_lockbox_validation._h6_trades` —
  **long-only**: both skip `pos[t] != 1.0`. They cannot represent the short leg
  that most of the 34 signals take. Excluded by **insufficiency**, not taste.

Two details that follow and need no decision: the last trade of the slice, if its
`planned_exit_date` falls past the window, simply stays open and is
**structurally absent** from `trade_returns` (no exit row → no join) — the same
guard `_h6_trades` implements manually as `t + HOLD >= n`. And slicing before
computing positions is required so rolling windows never see lockbox rows; this
is FDB-2a's discipline made structural, and matches the existing
slice-then-`evaluate_signal` precedent.

### 3. Mechanically determined or human?

**Mechanically determined.** No new construction is invented; the named basis is
adopted verbatim.

### 4. Downstream consequence

`sr_trials_std` becomes natively per-trade, satisfying FDB-4 with **no scaling
conversion**, which is what makes FDB-2's rejection of `sqrt(7.0)` coherent.
Dispersion and observation become genuinely commensurable: both sides are the
same algorithm, one on the train slice and one on the live corpus.

Consequence worth naming: FDB-2's `hold_days = 7` overrides each spec's own hold
(**18 of the 34 are `hold_days = 1`**, 8 are 7, 8 are 5), so 26 signals are
re-measured at a hold they were never specified with. That is what the contract
says, and it is a methodological fact to carry, not a defect.

### 5. Smallest amendment

**None.**

---

## OD-2 — Member with no computable Sharpe

### 1. Frozen evidence

FDB-1: *"The population is fixed at 34 and is identical to the population
`n_trials = 34` counts. **Neither may be changed to accommodate the other.**"*

FDB-2: *"no scaling conversion is applied."* FDB-6: no approximation, no
imputation, no estimation.

### 2. Existing authoritative implementation

`governance._sr_stats` returns `0.0, n, 0.0, 3.0` when `n < 5` **or** when
`r.std(ddof=1) == 0` — no NaN, no exception. `psr` returns `NaN` only when
`n < 5`, and `deflated_sr` degrades to `psr(returns, 0.0)` with `sr_star = 0.0`
when `sr_trials_std <= 0`.

Existing precedent runs the other way and is **not** usable here:
`compute_dsr_for_registration._load_library()` skips `None` Sharpes then merely
*warns* if the count is not 34; `run_harmonization` filters NaN out of the
dispersion while keeping `n_trials = len(lib)`. Both operate on the stored
library FDB-2 excludes, and both do exactly what FDB-1 now bars.

### 3. Mechanically determined or human?

**Determined by elimination for the runtime behaviour; HUMAN DECISION REQUIRED
for its acceptance.**

All three available actions are forbidden:

| action | forbidden by |
|---|---|
| drop the member | FDB-1 — the dispersion population becomes 33 while `n_trials` stays 34: the exact divergence "neither may be changed to accommodate the other" bars |
| inject `0.0` | invents a value — and it is the **silent default**: `_sr_stats` supplies it with no NaN and no error |
| substitute another statistic | FDB-2 (no conversion), FDB-6 (no approximation), and this gate |

The only contract-consistent runtime behaviour left is: **refuse to produce any
DSR result for the entire battery.**

**What is not determined** — and what FDB-1 never contemplated — is whether that
consequence is acceptable. If any **one** of 34 legacy signals cannot produce a
valid per-trade Sharpe on the frozen train slice under the OD-1 construction,
forward DSR becomes **permanently unavailable for every family**, and under R1's
NULL discipline no family can ever reach Validated — on account of a signal that
may have nothing to do with the family being judged.

### 4. Downstream consequence

Whether this is live or hypothetical **cannot be established without running the
OD-1 construction across the 34**, which this gate forbids. It is therefore an
open risk of unknown magnitude, not a known problem.

### 5. Smallest amendment

None is possible without a human choice. The three options, presented without
recommendation and with no value selected:

- **(a)** Adopt the by-elimination reading explicitly: an uncomputable member
  makes the whole battery UNEVALUABLE; the producer writes nothing. Requires no
  amendment — only a governance statement that the consequence is accepted.
- **(b)** Amend FDB-1 to define a treatment for an uncomputable member. Reopens a
  frozen clause.
- **(c)** Amend FDB-2 / FDB-2a so all 34 are computable. Reopens two frozen
  clauses, including the lockbox-window decision.

**De-risking act, strictly smaller than any DSR computation:** an authorized
diagnostic that applies the OD-1 construction and reports **trade counts only**
per library signal on the train slice — no Sharpe, no dispersion, no `psr`, no
`deflated_sr`, no `bh_fdr`, no write. It would convert OD-2 from an unknown into
a fact before the human decides. Flagged, not performed.

---

## OD-3 — Machine-readable minimums

### 1. Frozen evidence

The contract's own §1: *"Amendment form | **Form 1** — existing prose convention
in `collection_spec.evidence_snapshots.content[]` … **no structured minimum-n
schema introduced**."*

And, decisively, **FB-10b-N §0 stated the consequence before the choice was
made**, and the human then chose Form 1 anyway:

> **Form 1 — prose**, matching the three existing families. Precedent-consistent;
> the value remains **machine-unreadable**, so battery membership (FDB-5) would
> be **enforced by the operator, not by code**.

Operator enforcement of FDB-5 is therefore **not a new decision requiring
authorization — it is the adopted decision's declared content.**

### 2. Existing authoritative implementation

`observation_floors.resolve_pinned_prereg()` already verifies the A2a pin (the
LF-normalised SHA-256 in `registry.yaml`) and raises `PreRegistrationError` on
mismatch. That gives the runtime a cryptographic bind to the exact bytes carrying
the prose, without reading the prose.

FDB-7 and FDB-9 already require `battery_members[]` and `battery_excluded[]` with
reason `below_minimum(n=<n>, min=<m>)` — the minimum is therefore *already*
required to be frozen into every stored result.

### 3. Mechanically determined or human?

**Mechanically determined, and already authorized.** Form 1 was adopted with the
operator-enforcement consequence on the record. Preserved; **no amendment.**

The authoritative implementation representation is: **there is none, by design,
and none may be manufactured.** Specifically the producer must **not** parse the
prose — that would construct the machine-readable representation the governance
decision declined, and would make a governed value depend on wording — and must
**not** hardcode the seven, which would create a second source of truth.

### 4. Downstream consequence — exactly where and how operator values are frozen

1. **Supplied at invocation, explicitly, no defaults.** One argument per family
   (e.g. `--minimum ivrv_vrp_spread=30`). A collecting family whose minimum is
   not supplied causes the producer to **refuse the run** — FDB-8: never
   borrowed, never defaulted.
2. **Frozen into the append-only run row.** `battery_members[].minimum` and
   `battery_excluded[].reason = below_minimum(n=…, min=…)`, already required by
   FDB-7/FDB-9. *This is the freezing point*: the value becomes permanent
   provenance of the result it gated, exactly as FB-10b-M anticipated — *"the
   value becomes part of that result's permanent provenance even if later
   changed."*
3. **Bound per family to the document it came from**: record `prereg_path` and
   the verified `prereg_sha256` beside each minimum, so the row names the exact
   bytes that carried the prose and an auditor can re-read them.
4. **Cross-checked by a test, never at runtime.** A test may extract the integer
   from the pinned prose and assert it equals the documented operator-facing
   value. A test is *allowed* to be wording-fragile: its failure is loud,
   offline, and can never silently alter a stored result. Runtime parsing has
   none of those three properties.

**Residual, stated plainly:** the producer can verify the pin hash, `n >=
supplied minimum`, and `n >= 5`. It **cannot** verify that the supplied minimum
is the pre-registered one. That gap is the accepted cost of Form 1 and was
accepted when Form 1 was chosen.

### 5. Smallest amendment

**None.**

---

## OD-4 — Meaning of `bh_p`

### 1. Frozen evidence

**Verified this gate: the word "adjusted" never appears in a BH sense anywhere in
the frozen chain.** Its only occurrences are `n_trials … must never be adjusted`
(FDB-5), DSR's *"selection-bias-adjusted benchmark"* (`thresholds.yaml` comment),
and *"skew/kurtosis-adjusted PSR"* (FB-10a). **None supports reading `bh_p` as a
BH-adjusted p-value.**

What the contract *does* say: FDB-9 requires recording `q = 0.10` and `psr0`;
FDB-5 requires the frozen battery; FB-10a §4 says a recorded BH-p means *"the
BH-p of this family, under this battery, at this `as_of`"*. **None states the
number.**

### 2. Existing authoritative implementation

`governance.bh_fdr()` returns `{p, rank, bh_threshold, reject}` and **no adjusted
p-value; no function in the repository produces one.**
`lifecycle.check_validated_preconditions` compares
`pinned_run["bh_p"] <= max_bh_p` and its own error string names the threshold
`q`. The only storage precedent — `compute_dsr_for_registration.py`, and the
values it produced in the pre-registrations' `evidence_basis` (e.g. `ivrv
bh_p = 0.2893`) — stores the **raw** `1 - psr(returns, 0.0)`.

### 3. Mechanically determined or human?

**HUMAN DECISION REQUIRED. Stopping here, per this gate's instruction.**

The three readings and what each entails:

| reading | support | consequence |
|---|---|---|
| **raw p** `= 1 - psr0` | the only existing precedent | comparing a raw p to `q` makes BH **inert**: `rank`, `bh_threshold` and `reject` would be frozen but read by nothing, so FDB-5's carefully-specified membership would have **no gating effect** |
| **BH-adjusted q-value** | the only reading under which `bh_p <= max_bh_p (= q)` reproduces BH's actual rejection rule and FDB-5 has force | **no such function exists**; adding one modifies `governance.py`, the sole statistical authority (FDB-11), which this gate forbids |
| **`bh_threshold` or `reject`** | none | category error: `reject` is boolean; `bh_threshold = (i/m)·q <= q` is true for **every** member by construction, so the gate would pass unconditionally |

I have **not** equated raw p, the BH threshold, or rejection status with an
adjusted p-value, because the frozen contract does not explicitly support it.

### 4. Downstream consequence

Nothing downstream of `bh_p` can be implemented until this is decided — it
determines what number the Validated gate actually tests, and therefore whether
FDB-5 governs anything at all.

### 5. Smallest amendment, if adjusted-q is chosen

One **additive** function in `governance.py` — a standard BH step-up adjusted
p-value (the monotone cumulative-minimum form) — leaving `bh_fdr()`
**byte-identical**, so every existing caller and every already-stored historical
value is untouched. That is the minimum. It still requires explicit
authorization, because `governance.py` is frozen as the sole statistical
authority.

---

## OD-5 — Ledger / idempotency boundary

### 1. Frozen evidence

FDB-10: results *"are frozen into append-only rows, never recomputed on read."*
FDB-11: the producer *"appends new runs rather than rewriting existing ones"* and
performs **no** governance act.

### 2. Existing authoritative implementation — three readers, one row

| enforcement point | reads | site |
|---|---|---|
| `lifecycle.validate_registry` | `run = record.get_run(ledger, pinned_run_id)` → `check_validated_preconditions(fam, run, …)` reads `dsr_prob` / `bh_p` | `lifecycle.py:439-459` |
| `transition_governance.validate_transition` | `run = record.get_run(db_path, justifying_run_id)` at TG-PIN, then **that same `run`** feeds TG-RDY (readiness) and TG-PRE | `transition_governance.py:283, 336, 341` |
| `evidence_governance.admit_evidence` | `rule.evaluate(family_meta, run["metrics"], …)` reads `forward_admissibility` from **that same run's** metrics | `forward_admissibility` docstring |

⇒ **A single row must simultaneously carry: L2 lane + floors payload + readiness
block + non-NULL `dsr_prob`/`bh_p`.** This is mechanically determined by three
independent existing readers, none of which this gate may change.

### 3. Mechanically determined or human?

**Core: mechanically determined. One narrow sub-question: authorization
required.**

**Determined — DSR/BH belongs in the `forward_snapshot` row.** A separate DSR
producer writing its own row would have to duplicate floors and readiness to be
pinnable — which *is* the "second competing producer" this gate forbids — or
would emit a row that can never be pinned and therefore never matters.
`forward_snapshot` already has every required property: operator-invoked, service
actor refused, append-only, readiness embedded and asserted, floors embedded,
dry-run/write parity. It currently passes `governance=None` deliberately, so the
change is the narrowest possible: an **optional, explicitly-flagged** governance
payload on the existing writer.

**The idempotency key `(family_id, 'live-forward', window_end)` is UNCHANGED.**

### 4. Downstream consequence — a structural deadlock, not a preference

`ls._existing_live_forward_run()` filters on `(family_id, provenance,
window_end)` and **ignores `engine_version`**, so any date the nightly collector
has covered is closed to a snapshot. And `run_daily.py:683` calls
`ledger_sync.sync_all()` **every night**, writing for exactly the families that
have closed trades — i.e. the only families that could ever be battery members
(`sync_family` returns `no-evidence` and writes nothing otherwise).

**Therefore, for any eligible family, `as_of = yesterday` is always already
taken, and a snapshot could only ever be written for a date the collector
skipped — which is precisely a date with no evidence to compute on.** Verified on
production: 10 `forward-collect-v1` rows at `window_end` 2026-08-29 / 2026-08-30;
FB-12k's single `forward-snapshot-v1` row sits at 2026-08-28, a date the
collector had not covered.

### 5. Smallest amendment — requires authorization, and is deliberately not chosen here

| option | change | cost |
|---|---|---|
| **(a)** operator picks an `as_of` strictly older than collector coverage | none | works today; degrades to unusable as coverage grows — it is avoidance, not resolution |
| **(b)** narrow the key to `(family_id, provenance, window_end, engine_version)` | `ledger_sync._existing_live_forward_run()` | alters a governed idempotency boundary; two rows would coexist at one `as_of`, resolved by `current_run_per_lane`'s `run_ts DESC` |
| **(c)** suppress the nightly write for a family that already has a snapshot row | `ledger_sync.sync_family()` | changes unattended collector behaviour — the larger blast radius |

All three alter a governed boundary, which this gate forbids me to do silently.
**Authorization required, narrowly, for this sub-question only.**

---

## A. RESOLVED — mechanically determined

| # | resolution |
|---|---|
| **OD-1** | The construction is `runner_base.run_signal()` 5a/5b + the `trade_returns` view, applied to the train-sliced master. Signed, non-overlapping, unconditional 7-calendar-day exit, one flat cost per trade, close-to-close. Applicable to all 34 (positions verified `{-1,0,+1}` by source inspection). **No amendment.** |
| **OD-3** | No machine-readable representation exists **or may be manufactured**; Form 1 preserved. Minima are operator-supplied at invocation with no defaults, and frozen into the append-only row via `battery_members[].minimum` / `battery_excluded[].reason`, bound to `prereg_path` + verified `prereg_sha256`, cross-checked only by an offline test. Already authorized by the Form 1 decision, which declared operator enforcement. **No amendment.** |
| **OD-5 (core)** | DSR/BH belongs on the **`forward_snapshot` row**, not a second producer — forced by three existing readers that all read one run. Idempotency key unchanged. |

## B. HUMAN DECISION REQUIRED

| # | question | why it cannot be resolved here |
|---|---|---|
| **OD-4** | What number does `bh_p` contain? | The contract never states it, and "adjusted" appears nowhere in a BH sense. Raw p makes FDB-5 inert; adjusted-q requires adding a statistic to the frozen sole authority; threshold/reject are category errors. **A new definition is required either way.** |
| **OD-2** | Is "any uncomputable member ⇒ the whole battery is UNEVALUABLE" accepted? | Determined only by elimination. FDB-1 never contemplated it, and the consequence — forward DSR permanently unavailable for every family — is disproportionate to be adopted silently. Magnitude is unknown and knowable only via an authorized diagnostic. |
| **OD-5 (sub)** | May an operator-invoked governance row coexist with a same-`as_of` collector row, and if so by which of (a)/(b)/(c)? | Every option alters a governed idempotency boundary. |

## C. EXPLICITLY FORBIDDEN INFERENCE

Recorded so no later gate re-derives them by convenience:

1. **Do not** treat raw `p = 1 - psr0`, `bh_threshold`, or `reject` as a
   BH-adjusted p-value. Not supported by the frozen contract (OD-4).
2. **Do not** drop an uncomputable member from the dispersion — FDB-1 bars the
   population diverging from `n_trials` (OD-2).
3. **Do not** inject `0.0` for an uncomputable member. `governance._sr_stats`
   supplies it **silently**, with no NaN and no error; a producer that does not
   guard against this will invent values invisibly (OD-2).
4. **Do not** parse the pre-registration prose at runtime to obtain a minimum —
   that manufactures the machine-readable representation Form 1 declined (OD-3).
5. **Do not** hardcode the seven minima in the producer — a second source of
   truth for a governed per-family parameter (OD-3).
6. **Do not** use `engine._runs` / `_apply_hold` runs (variable length) or
   `_h6_native_trades` (long-only) as the trial construction (OD-1).
7. **Do not** apply `sr_trials_std * sqrt(7.0)`, `n_trials = 38`, `DSR_CONF =
   0.95`, `sr_std * 0.5`, or `psr_approx = Φ(sr·√(n−1))` — each rejected by name
   in FDB-2 / FDB-6 or self-labelled non-governance.
8. **Do not** compute positions on the unsliced master and slice afterwards —
   rolling windows would read lockbox rows (FDB-2a).
9. **Do not** create a second L2 producer or a second row carrying DSR/BH
   separately from floors and readiness (OD-5).
10. **Do not** widen or narrow the idempotency key without explicit
    authorization (OD-5).

## D. MINIMAL NEXT GATE

**No producer may be implemented: OD-4 is a hard stop, and it is a pure
governance question needing no code.**

The minimal next gate is a **human decision gate** carrying exactly three
questions — OD-4 (the definition of `bh_p`), OD-2 (acceptance of the
by-elimination treatment), and OD-5's coexistence sub-question — presented as a
decision form in the FB-10b-N style, with no value recommended.

**Optional and strictly smaller, to be taken first only if the human wants OD-2
decided on fact rather than on risk:** an authorized read-only diagnostic that
applies the now-resolved OD-1 construction to the 34 library signals on the train
slice and reports **trade counts only** — which signals fall below `n = 2` and
below `n = 5`. It must compute **no** Sharpe, **no** dispersion, **no** `psr`,
**no** `deflated_sr`, **no** `bh_fdr`, and must write nothing. That is a
materially smaller act than computing DSR, and it is the only way to learn
whether OD-2 is a live problem or a dead letter.

Sample size remains an independent execution condition: the battery is empty
today, so no ordering of these gates unblocks a computation by itself.

**STOP.**

---

## Trail

| Gate | Outcome |
|---|---|
| FB-10b-O | FDB-8 Option A adopted (Form 1, prose); FDB-5 confirmed; FDB-1…FDB-12 approved |
| FB-10b-P | Producer specified; 4 blockers + 2 open questions raised |
| **FB-10b-Q** | **This record. OD-1, OD-3 and OD-5's core RESOLVED mechanically; OD-1 was not a blocker (FB-10b-P corrected). OD-4 and OD-2 remain HUMAN DECISION REQUIRED, plus one narrow OD-5 sub-question** |
