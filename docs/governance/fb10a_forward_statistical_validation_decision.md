# FB-10a — Forward Statistical Validation (DSR / BH-FDR): Governance Decision

**Date: 2026-08-31 (UTC) · Gate FB-10a-R · Decision: DEFER implementation; freeze
what is already frozen; keep three inputs explicitly OPEN**

Records the governance contract for computing Deflated Sharpe Ratio and
Benjamini-Hochberg FDR on **forward** (lane L2) evidence. It implements nothing,
chooses none of the open inputs, and changes no threshold, pre-registration or
ledger row.

Every claim below was verified against the repository at this gate. Where the
evidence is ambiguous or absent, that is stated rather than resolved.

---

## 1. Decision

**Forward DSR/BH computation is DEFERRED.** No forward statistical values are
generated, and none should be, at current sample sizes.

The reason is not caution in the abstract — it is that the families' own
pre-registrations forbid computing yet. Measured at `as_of = 2026-08-30`:

| family | closed trades | pre-registered statistical minimum | status |
|---|---|---|---|
| `h5_vol_regime` | 7 | **15** (non-overlapping 7-day blocks) | below |
| `g3_momentum_tsmom` | 8 | **15** (completed-period returns) | below |
| `g1_market_regime_sma200` | 7 | **10** (7-day blocks across all trades) | below |
| `ivrv_vrp_spread` | 6 | not stated | — |
| `g3_atr_expansion` | 3 | not stated | **below the n ≥ 5 computational guard** |

Computing now would either produce `NaN` (`g3_atr_expansion`) or produce numbers
that the families themselves pre-registered as not-yet-computable. Deferral is
the pre-registrations being honoured, not a delay.

---

## 2. Frozen — not reopened by this record

**FR-1 — `n_trials = 34` applies to forward validation.**
Seven of the eight pre-registrations state it explicitly *with a forward-returns
basis* (all except `h6`, whose own basis is "DSR on trade returns
(non-overlapping 7d)"). `g1_trend_regime` puts it beyond doubt: *"FROZEN. This
family was inside the original Phase B battery and its pinned run already
records n_trials = 34. This candidate does not reopen the denominator."*
`g2_funding_level`: *"Validated is unreachable until both are computed at
n_trials=34."*

The discovery scripts' `n_trials = len(lib) + 4 = 38` is **not** governance.
CLAUDE.md §16 already reconciled it as a discovery screen that never entered the
ledger and had no gating consequence. **38 must not be silently reused.**

**FR-2 — `q = 0.10`.** `thresholds.yaml` carries `validated.edge.max_bh_p: 0.10`,
`validated.feature.max_bh_p: 0.10` and `validated.fdr_q: 0.10`; both discovery
callers pass `q=0.10`. Consistent across all four sites.

**FR-3 — one statistical authority.** `research/evidence/governance.py` —
`_sr_stats`, `psr`, `deflated_sr`, `bh_fdr`. A second implementation must not be
created; a forward producer, when built, calls these.

**FR-4 — per-family pre-registered minima are authoritative.** 15 / 15 / 10 as
tabulated in §1. These are governance sufficiency criteria.

**FR-5 — `n >= 5` is a mathematical guard, not a governance criterion.**
`governance.py` lines 79 and 90 return `NaN` below five observations because the
skew/kurtosis-adjusted PSR is undefined there. It is a floor on *computability*,
never on *sufficiency*, and must never be cited as one. Three distinct minima
exist and must not be conflated:

| minimum | value | kind | source |
|---|---|---|---|
| computational guard | n ≥ 5 | mathematical | `governance.py` |
| statistical sufficiency | n ≥ 10 / 15 | governance | per-family pre-registration |
| observation floor | 26–30 trades | governance | per-family `observation_floors` (FB-7) |

**FR-6 — DSR/BH stay outside the automated collection path.** They must never be
computed in `run_daily.py`, `ledger_sync.py`, or anything a timer invokes. A
forward statistical producer is operator-invoked and refuses service actors, the
same boundary `forward_snapshot.py` (FB-9) already holds. *"DSR/BH must not be
computable by a cron"* is existing design, restated here because this path did
not previously exist.

**FR-7 — statistical validation performs no governance act.** It appends run
rows carrying `governance={...}` and stops. It must not admit, pin, transition,
promote, or modify `registry.yaml`. Those remain separate, human-invoked steps.

---

## 3. OPEN — deliberately not decided here

**U1 — the `sr_trials_std` population is undefined for the forward lane, and the
two discovery scripts disagree.**

```python
run_dvol_family.py:108   std(lib_daily_sr + dvol_daily_sr, ddof=1)   # library + candidates
run_harmonization.py:119 std(lib_daily_sr, ddof=1)                    # library only
```

Same function, different populations, different `sr_star`, different DSR for
identical returns. Neither is frozen governance. This input scales every DSR
value, so it cannot be chosen implicitly.

**U2 — the trade-level basis and scaling are undefined, and the only existing
trade-level scaling is self-described as approximate.**

```python
run_harmonization.py:134   sr_trials_std_trade = sr_trials_std * np.sqrt(7.0)   # rough per-7d scaling
```

The comment is the author's own. The forward lane is inherently trade-level
(`hold_days = 7`, `trade_returns`), so adopting this would import an
approximation its author flagged as rough. The alternatives — deriving a genuine
trade-level dispersion, or requiring daily-unit returns — are equally open.

Note also that the pre-registered bases differ per family: 7-day non-overlapping
blocks (`h5`, `g1_market_regime_sma200`, `h6`), completed-period returns
(`g3_tsmom`), plain forward trade returns (others). Any forward producer must
honour each family's own basis rather than imposing one.

**U3 — the BH battery universe is undefined.** `run_dvol_family` runs BH over the
DVOL family; `run_harmonization` over KEEP-6. Different universes give different
BH-p for identical returns, because BH ranks within its universe.

**These three remain OPEN. This record does not choose among them.** Whoever
closes them should note they interact: U1 and U2 jointly determine `sr_star`,
and U3 determines every BH-p.

---

## 4. BH-p is not a property of a family

`bh_fdr(pvals, q)` computes `m = len(pvals)` and ranks within that set. Adding or
removing one family changes every other family's rank and threshold.

A recorded BH-p therefore means *"the BH-p of this family, under this battery, at
this as_of"* — never *"this family's p-value"*. Any future producer **must**
freeze, in `metrics_json` alongside `dsr_prob` / `bh_p`:

```
battery_members[]        the exact family set, and its ordering
battery_excluded[]       with the reason each was excluded
sr_trials_std            the value used
sr_trials_std_basis      population, units, and any scaling applied  (U1 + U2)
n_trials                 34
q                        0.10
psr0, sr_star            the intermediates
return_basis             per that family's pre-registration
as_of, calc_version      the calculation identity
```

Without frozen membership and ordering a stored BH-p cannot be reproduced or
honestly interpreted. `record.append_run(governance={...})` is sufficient for the
two columns; this metadata is what makes them meaningful.

**Point-in-time discipline:** every return must satisfy `exit_date <= as_of`, the
structural filter FB-1c and FB-9 already use. **No later corpus growth may change
a recorded statistical result** — which is why results are frozen into an
append-only row rather than recomputed on read.

---

## 5. Existing evidence is untouched

All **10** live-forward rows carry `dsr_prob` and `bh_p` **NULL** — verified at
this gate. That is correct: no forward statistics have ever been computed, and
NULL is a hard failure at the Validated gate under R1's discipline.

**No migration, no rewrite, no backfill.** The rows remain historical evidence,
permanently blocked from Validated. Any future statistics arrive as **new** rows
at a **new** `as_of`.

---

## 6. Pairwise independence stays separate

`pairwise_r` has **0 rows**. `record.append_pairwise_r()` is its own append-only
surface, enforced by `lifecycle.check_validated_independence` against
`max_pairwise_r = 0.70`, and the pre-registrations specify it on forward returns
at promotion time.

It shares inputs with DSR/BH but is a distinct measurement against a distinct
threshold. **It remains FB-10c.** Folding it into the statistical producer would
give one operation two authorities.

---

## 7. The layer boundary

```
observation floors       FB-7    is there enough evidence?     per-family
forward admissibility    FB-8    is this run pinnable?         per-run
readiness                PR-9    is the window complete?       per-window (deferred, FB-9b)
statistical validation   FB-10   does it clear DSR / BH?       BATTERY-WIDE  <- only cross-family layer
pairwise independence    FB-10c  is it distinct from peers?    pairwise
human promotion          --      should we?                    judgment
```

Statistical validation is the only layer whose result depends on families other
than the one being assessed. That is inherent to FDR and to the deflation
benchmark, and it is why battery composition must be frozen with the result.

---

## 8. Human approval points, carried forward

1. **U1** — which `sr_trials_std` population?
2. **U2** — trade-level basis and scaling: accept `× √7`, derive a true
   trade-level dispersion, or require daily units?
3. **U3** — BH universe: all collecting families, or only floor-qualifying ones?
4. Should **`n_trials = 34`** move into `thresholds.yaml`? It is the one
   governance-frozen constant **absent** from that file — verified — living
   instead in `ledger_sync.N_TRIALS`, a comment in `dataset_builder/
   experiments.py`, CLAUDE.md §16, and the pre-registrations.

Questions 1 and 2 change every DSR value. Neither has a frozen answer today.

---

## 9. Trail

| Gate | Outcome |
|---|---|
| FB-5 | Identified the missing DSR/BH segment (gap M2) |
| FB-10 | Audited the path; found DSR/BH implemented but forward-unused, and battery-scoped |
| FB-10a | Specification: options and consequences |
| **FB-10a-R** | **This record. Deferred; FR-1..FR-7 frozen; U1/U2/U3 open** |
| FB-10b | The producer — **not started**, blocked on U1/U2/U3 |
| FB-10c | `pairwise_r` workflow — **not started** |
