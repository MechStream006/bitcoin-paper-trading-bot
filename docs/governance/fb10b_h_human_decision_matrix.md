# FB-10b-H — DSR/BH Human Decision Matrix

**Date: 2026-08-31 (UTC) · Gate FB-10b-H · Read-only · Decision matrix for human
approval. No option is chosen or recommended.**

Three governance decisions remain. This record presents the defensible options
for each, with frozen evidence, consequences, and what each would permanently
freeze. **It does not select among them.**

No code, statistics, ledger, production, threshold, pre-registration or registry
change.

---

## 0. Already settled — not reopened here

| item | status |
|---|---|
| **U1 — `sr_trials_std` population** | **RESOLVED**: the 34 Phase B library Sharpes from `evidence_library.json` (verified: 34 entries, all 34 carrying a Sharpe). Frozen in `compute_dsr_for_registration.py`'s "Governance constraints (FROZEN)" block |
| **`n_trials = 34`** | **Frozen.** Moving it into `thresholds.yaml` is optional housekeeping, not a prerequisite |
| **Observed series** | **Forward trade returns** remain authoritative (7-day, non-overlapping; `hold_days_actual = 7` on all 31 production trades) |
| **Current sample size** | An **execution constraint only**. It is not evidence for any governance definition and is excluded from every argument below |

### A structural constraint that bounds U2

```python
def psr(returns: np.ndarray, sr_benchmark: float = 0.0):
    sr, n, g3, g4 = _sr_stats(returns)      # SR derived FROM the returns array
    z = (sr - sr_benchmark) * np.sqrt(n - 1) / denom
```

`psr` computes the observed SR **from the returns array itself**, so
`sr_benchmark` (= `sr_star`, = `sr_trials_std × constant`) must already be in the
**returns' own units**. With per-trade returns, the dispersion must be per-trade.
There is no way to express an "annualise both sides" variant through this
signature. **U2 therefore reduces to exactly one question: how is a per-trade
dispersion obtained?** Three answers are evidence-supported.

---

## 1. U2 — forward `sr_trials_std` / return-unit basis

### Why the existing `× √7` cannot simply be adopted

`run_harmonization.py:134` — `sr_trials_std_trade = sr_trials_std * np.sqrt(7.0)
  # rough per-7d scaling`. Five independent reasons it is not available as-is:

1. **Its author labelled it "rough."** It is a working approximation, never a
   governance-authorized constant.
2. **Its assumption is not met.** `√7` scaling presumes 7 i.i.d. daily returns
   summing to a block. Forward trades are *held* positions with hold mechanics,
   turnover cost charged at entry/exit, and serial dependence.
3. **It papers over exactly what four pre-registrations flag.** *"IS and forward
   Sharpe/DSR are NOT directly comparable"* — the stated cause is the
   `hold_days=1` vs `hold_days=7` convention difference, which is precisely what
   `√7` would silently bridge.
4. **It lives in a discovery script**, which
   `n_trials_denominator_reconciliation.md` classes as non-governance (Group B).
5. **It has never entered the ledger** or any pre-registration.

Adopting it is still *available as an option* (U2-A) — but only by explicit
authorization, not by inheritance.

### Options

| | **U2-A — Declared scaling** | **U2-B — Native recomputation at hold_days=7** | **U2-C — Forward-native dispersion** |
|---|---|---|---|
| **Return series** | forward trade returns | forward trade returns | forward trade returns |
| **Units** | per-trade (7-day) | per-trade (7-day) | per-trade (7-day) |
| **How `sr_trials_std` is obtained** | frozen 34-library **daily** dispersion × a declared factor (e.g. `√7`) | re-evaluate all **34 library signals at `hold_days=7`** on the IS corpus; SD of their per-7-day Sharpes | SD of the **collecting families' own forward** per-trade Sharpes |
| **Conversion required?** | **Yes** — the whole option is the conversion | **No** — natively per-trade | **No** — natively per-trade |
| **Satisfies** | U1's frozen population verbatim; the literal `evidence_library.json` clause | U1's frozen *population* (same 34 signals); `deflated_sr`'s same-period requirement with no approximation | `deflated_sr`'s same-period requirement |
| **Conflicts with** | the four "NOT directly comparable" statements; the author's "rough" label | the literal clause *"from `evidence_library.json`"* — that file stores `hold_days=1` Sharpes, so re-evaluation produces numbers not in it. **Also touches the IS corpus, which spans the sealed lockbox** | **`n_trials = 34`**: dispersion would span ~7 families while the denominator counts 34 trials, violating `deflated_sr`'s same-population requirement |
| **`n_trials = 34` still semantically valid?** | **Yes** — same 34 trials, re-expressed | **Yes** — same 34 trials, re-measured | **No** — requires reopening the freeze |
| **New freeze / amendment required** | authorize the specific factor **and** its i.i.d. assumption; annotate the four non-comparability statements as "not comparable *without* this declared conversion" | amend the `sr_denominator` clause to "recomputed at the forward hold convention", superseding *"from `evidence_library.json`"*; state the lockbox handling | put `n_trials = 34` on the table and re-freeze a new denominator; define self-inclusion (a family appearing in its own benchmark) |

### Consequences

- **Every DSR value scales with this choice.** `sr_star` is linear in
  `sr_trials_std`; a `√7` factor is ≈ 2.65×, which moves `dsr_prob` across the
  0.50 bar in either direction depending on sign.
- **U2-A** is the smallest change to frozen text and the largest assumption.
- **U2-B** is the largest computational change and the smallest assumption; it
  requires deciding how the sealed lockbox is treated during re-evaluation.
- **U2-C** changes the *scientific claim* DSR makes — from "deflated against the
  Phase B search" to "deflated against the forward cohort" — and with ~5–7
  members the dispersion is itself estimated from very few points.

### What becomes permanently frozen after approval

The dispersion's **units, derivation and any conversion factor**, recorded as
`sr_trials_std_basis` in every DSR result thereafter. Every stored `dsr_prob`
becomes interpretable only against the chosen basis, and a later change would
invalidate comparison across results.

---

## 2. U3 — forward BH battery membership

### Can the 34-family registration precedent be transplanted?

The frozen precedent exists: `compute_dsr_for_registration.py` §4 runs
`bh_fdr(all_pvals, q=0.10)` across **all 34 library signals**, reporting
`rank/N_TRIALS`.

**It cannot be transplanted as-is.** Those 34 signals have no forward returns.
The IS path achieved full coverage only by *approximating* the 31 non-registered
signals — `psr_approx = Φ(sr_daily × √(n−1))`, commented *"ignores skew/
kurtosis"* — an approximation the file itself frames as *"sufficient to establish
that no signal rejects BH-FDR"* for an informational screen. Whether that is
acceptable for a **gating** forward statistic is a governance judgment, not a
technical one; it is offered below as U3-A.

### Options

| option | membership | size today | BH interpretation |
|---|---|---|---|
| **U3-A** | all 34 library signals, forward p-values where available, **approximated** elsewhere | 34 | preserves the IS precedent and `rank/34` symmetry; imports an approximation into a gating statistic |
| **U3-B** | all collecting forward families | 7 | "FDR across everything being collected forward"; stable membership; includes families with zero trades, whose p-values are undefined or uninformative |
| **U3-C** | families meeting their own DSR minimum | 0 today | "FDR across everything actually testable"; **membership is dynamic** — a family qualifying later changes every other member's rank and threshold |
| **U3-D** | families with ≥ 1 closed trade | 5 | computable for all members; the threshold is arbitrary relative to any frozen criterion |
| **U3-E** | the cohort tested at a given promotion attempt | varies | matches BH's textbook purpose (simultaneous inference); **membership differs per attempt**, so the same family can hold different `bh_p` values at different times |

### Consequences (common to all)

- **`bh_p` is a property of a set, not of a family.** Adding or removing one
  member changes every other member's rank and threshold.
- **Dynamic-membership options (U3-C, U3-E) require freezing membership into each
  stored result**, or a recorded `bh_p` becomes uninterpretable later.
- **`n_trials = 34` is not a BH universe.** It is the DSR multiple-testing count.
  U3-A is the only option under which they coincide, and that coincidence is
  inherited from the IS path rather than independently justified.

### What becomes permanently frozen after approval

The **membership rule** (not a count), plus the requirement that
`battery_members[]` and their order be recorded with every stored `bh_p`.

---

## 3. U4 — families with no pre-registered DSR minimum

### Exactly which families

| family | `minimum_trades` (FB-7 observation floor) | DSR compute-minimum |
|---|---|---|
| `h5_vol_regime` | 30 | **15** |
| `g3_momentum_tsmom` | 30 | **15** |
| `g1_market_regime_sma200` | 30 | **10** |
| **`ivrv_vrp_spread`** | 30 | **none stated** |
| **`g2_funding_level`** | 30 | **none stated** |
| **`g1_trend_regime_ma_cross`** | 30 | **none stated** |
| **`g3_atr_expansion`** | **26** | **none stated** |

Note the two minima are **distinct and both real**: where a DSR minimum exists it
is *lower* than the observation floor (compute at 10–15, promote at 30). FR-5
requires they not be conflated.

### Options

| option | rule | consequence | amendment required |
|---|---|---|---|
| **U4-A** | exclude the four until each pre-registration states a minimum | preserves per-family authority absolutely; pre-registrations are **hash-pinned**, so amending re-hashes them and requires re-registration under the A2a convention | amend 4 pre-registrations |
| **U4-B** | freeze one new common DSR minimum applying **only** to families that state none | one new frozen constant; does not touch any pre-registration; the number would be new governance, not derived from evidence | new frozen constant in `thresholds.yaml` |
| **U4-C** | apply each family's existing `minimum_trades` floor (30, or 26 for `g3_atr_expansion`) as its DSR minimum | uses a value already frozen per family; strictly **stricter** than the 10/15 precedent, so DSR would become computable only at promotion-readiness; FR-5 warns these are distinct minima, so this is a deliberate *unification*, not a conflation | governance statement that the observation floor also governs DSR where no DSR minimum is stated |
| **U4-D** | use `n >= 5` | **Conflicts with FR-5**, which states `n >= 5` is *"a floor on computability, never on sufficiency, and must never be cited as one."* Listed for completeness | would require reopening FR-5 |

### What becomes permanently frozen after approval

Whether silence in a pre-registration means **"no authorization"** (U4-A),
**"a default applies"** (U4-B/U4-C), or something else — a precedent that will
govern every future family registered without a stated minimum.

---

## 4. HUMAN APPROVAL TABLE

| Decision | Option | Consequence | Required amendment | Approval required |
|---|---|---|---|---|
| **U2** | **A** — declared scaling of the frozen daily dispersion | smallest change to frozen text; largest assumption; every DSR scales by the factor | authorize the factor + its i.i.d. assumption; annotate the 4 non-comparability statements | **Yes** |
| | **B** — recompute 34-library dispersion at `hold_days=7` | no approximation; requires touching the IS corpus, which spans the sealed lockbox | supersede the *"from `evidence_library.json`"* clause; state lockbox handling | **Yes** |
| | **C** — forward-native dispersion | changes what DSR claims; dispersion from ~5–7 points | **reopen `n_trials = 34`**; define self-inclusion | **Yes** |
| **U3** | **A** — all 34 library signals | preserves IS precedent; imports a skew/kurtosis-ignoring approximation into a gating statistic | authorize the approximation for gating use | **Yes** |
| | **B** — all 7 collecting families | stable membership; includes zero-trade families | define handling of undefined p-values | **Yes** |
| | **C** — families meeting their own minimum | tests only what is testable; **dynamic membership** | require frozen `battery_members[]` per result | **Yes** |
| | **D** — families with ≥ 1 closed trade | all members computable; threshold not derived from any frozen criterion | justify the ≥ 1 threshold | **Yes** |
| | **E** — per-promotion-attempt cohort | matches BH's purpose; same family may hold different `bh_p` over time | require frozen membership + `as_of` per result | **Yes** |
| **U4** | **A** — exclude until stated | maximal per-family authority; 4 hash-pinned re-registrations | amend 4 pre-registrations | **Yes** |
| | **B** — new common minimum | one new constant; touches no pre-registration | add frozen constant | **Yes** |
| | **C** — use the existing `minimum_trades` floor (30 / 26) | reuses already-frozen per-family values; stricter than the 10/15 precedent | statement unifying the two minima where no DSR minimum exists | **Yes** |
| | **D** — use `n >= 5` | **conflicts with FR-5** | reopen FR-5 | **Yes** |

**All twelve rows require human approval. No option is selected, preferred, or
implied by this record.**

---

## 5. Status

FB-10b implementation remains **not authorized**. It becomes authorizable when
U2, U3 and U4 are each decided and recorded; the sample-size condition (each
family's own minimum, plus `n >= 5`) is separate and additional.

**STOP.**

---

## 6. Trail

| Gate | Outcome |
|---|---|
| FB-10a-R | U1/U2/U3 opened; FR-1..FR-7 frozen |
| FB-10b-pre | STOP; U4 opened; zero families eligible |
| FB-10b-G | U1 and U4-core RESOLVED; U2, U3, U4-residual open |
| **FB-10b-H** | **This record. Decision matrix prepared; twelve options across three decisions; none chosen** |
