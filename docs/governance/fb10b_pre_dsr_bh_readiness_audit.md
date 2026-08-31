# FB-10b-pre — DSR/BH Implementation Readiness Audit

**Date: 2026-08-31 (UTC) · Gate FB-10b-pre · Read-only · Verdict: STOP.**

**U1, U2 and U3 remain unfrozen, and frozen pre-registration text actively
argues against the only available bridge for U1/U2. Independently of all three,
a hard frozen blocker exists: NOT ONE family currently satisfies its own
pre-registered minimum n, so no DSR may legitimately be computed for any family
today.**

No implementation, no statistic computed, no ledger write, no change to
thresholds, pre-registrations, registry or production.

---

## A. Frozen and resolved

**A1 — `n_trials = 34` is sufficiently frozen. Moving it is desirable, not
necessary.** Seven of eight pre-registrations state it; `g1_trend_regime` and
`g2_funding_level` both carry a `G2_governance_stats` gate reading *"dsr_prob >=
0.50 AND bh_p <= 0.10, computed at the **FROZEN n_trials=34** on forward
returns"*; CLAUDE.md §16 lists it among the governance-frozen constraints. It is
binding through hash-pinned documents already. Relocating it into
`thresholds.yaml` would improve discoverability and nothing else — it is **not a
prerequisite** for FB-10b. Not modified.

**A2 — The return basis is forward trade returns, and the per-family phrasings
describe one series, not several.** FB-10a-R listed the bases as apparently
divergent ("7-day non-overlapping blocks" for h5/g1/h6, "completed-period
returns" for g3_tsmom, "trade returns" elsewhere). They are the same thing:
every pre-registration defines *"each 7-day evaluation window is one trade;
trades are sequential and non-overlapping"*, and `hold_days_actual = 7` for all
31 production trades (verified FB-10c-D). **A forward trade return IS a
non-overlapping 7-day block return.** No conversion between those descriptions
is required or permitted.

**A3 — Point-in-time is `exit_date <= as_of`.** Frozen practice:
`ledger_sync.load_closed_trades()`, FB-9, FB-10a-R §4, FB-12g.

**A4 — Where a per-family minimum is stated, it governs eligibility.**
`h5_vol_regime` **n >= 15**, `g3_momentum_tsmom` **n >= 15**,
`g1_market_regime_sma200` **n >= 10**. FR-5 stands: `governance.py`'s `n >= 5`
is a *computability* guard and must never be cited as sufficiency.

**A5 — BH-p is a property of a set, not of a family** (FB-10a-R §4), so a stored
value is meaningless without frozen membership.

**A6 — `research/evidence/governance.py` is the sole statistical authority**
(FR-3). No second implementation.

**A7 — Readiness is no longer the promotion blocker; DSR/BH is.** Verified on
the persisted FB-12k row `2d1adf5d…`: readiness 56 ready / 19 excluded / 0
non-ready, TG-RDY satisfiable, `dsr_prob = None`, `bh_p = None`. Across the
whole production ledger, **0** live-forward runs carry either statistic.

---

## B. Still genuinely unresolved

### U1 — `sr_trials_std` population: NOT FROZEN, and the obvious bridge is
### argued against by frozen text

Exhaustive search (`sr_trials_std`, `sr_star`, `trials_std` across all `.py`,
`.json`, `.yaml`, `.md`): it appears in **exactly two discovery scripts, which
disagree**, and in one research report. **No pre-registration mentions it. No
governance document defines it. Nothing defines it for the forward lane.**

```
run_dvol_family.py:108   std(lib_daily_sr + dvol_daily_sr, ddof=1)   # library + candidates
run_harmonization.py:119 std(lib_daily_sr, ddof=1)                    # library only
```

`deflated_sr`'s own contract states the benchmark is *"expected maximum Sharpe
under n_trials independent trials given the **cross-trial SR dispersion**"*,
with results *"all per-period"*. So `sr_trials_std` and `n_trials` must describe
**the same trial population, in the same period units** as the observed returns.

**And that is precisely what frozen evidence forbids here.** The only existing
dispersion is the library's **daily** SRs from `hold_days = 1` evaluation. The
forward returns are **7-day** trade returns from `hold_days = 7`. Four
pre-registrations state, in hash-pinned text:

> *"IS and forward Sharpe/DSR are **NOT directly comparable**; the Validated gate
> evaluates forward returns only."* — `g3_atr_expansion`, `ivrv`
>
> *"IS and forward Sharpe distributions are not directly comparable across the
> two conventions."* — `g2_funding_level`, `g3_momentum_tsmom`

Using the IS library dispersion as the forward benchmark would implement exactly
the comparison those documents declare invalid. **U1 cannot be resolved from
frozen evidence, and the tempting resolution is affirmatively contradicted.**

### U2 — Scaling: NOT FROZEN, and follows U1

Since A2 removes any basis conversion, U2 reduces entirely to *"in what units is
`sr_trials_std` expressed, and how is it brought to per-trade units?"* The only
existing bridge is:

```python
run_harmonization.py:134   sr_trials_std_trade = sr_trials_std * np.sqrt(7.0)   # rough per-7d scaling
```

**"rough" is the author's own word.** Adopting it would import an approximation
its author flagged, to perform a comparison four pre-registrations call invalid.
Rejected as unauthorized, exactly as this gate requires.

### U3 — BH universe: NOT FROZEN

Searched every pre-registration for `bh_p` / `benjamini` / `fdr` / `universe`.
Every hit states only the **threshold** (`bh_p <= 0.10 ... on forward trade
returns`). **None names the participating set.** The discovery precedents
disagree (`run_dvol_family` = DVOL family; `run_harmonization` = KEEP-6).

Four populations exist and must not be conflated:

| population | size | what it is |
|---|---|---|
| registered families | **35** | the registry |
| collecting families | **7** | writing forward evidence |
| families meeting their own minimum n | **0** | see §B/U4 |
| `n_trials` denominator | **34** | the frozen Phase B battery — a *count*, not a membership list |

`n_trials = 34` is emphatically **not** a BH universe: it is the frozen
multiple-testing denominator for DSR, and the Phase B battery it counts is not
the set of families that would participate in a forward BH.

### U4 — Unstated per-family minima: NOT FROZEN (new)

Only three families declare a DSR minimum. **`ivrv_vrp_spread`,
`g2_funding_level`, `g3_atr_expansion` and `g1_trend_regime_ma_cross` state
none.** Whether silence means "any n above the mathematical guard" or "no
authorization to compute" is undecided, and FR-5 forbids importing another
family's number.

---

## C. The hard blocker that stands regardless of U1–U4

**No family is eligible today.** Counts at `as_of = 2026-08-30` against each
family's *own* frozen minimum:

| family | closed trades | own minimum | eligible? |
|---|---|---|---|
| `g3_momentum_tsmom` | 8 | 15 | **No** |
| `h5_vol_regime` | 7 | 15 | **No** |
| `g1_market_regime_sma200` | 7 | 10 | **No** |
| `ivrv_vrp_spread` | 6 | — | **Unauthorized** (U4) |
| `g3_atr_expansion` | 3 | — | **No** — below the `n >= 5` computability guard |
| `g1_trend_regime_ma_cross` | 0 | — | **No** |
| `g2_funding_level` | 0 | — | **No** |

Even with U1–U4 all closed tomorrow, **there is nothing a DSR/BH producer could
legitimately compute.** Deferral is the pre-registrations being honoured, not a
delay — exactly as FB-10a-R concluded, and still true.

---

## D. Human approvals still required

1. **U1** — the forward `sr_trials_std` population, *and* how (or whether) an
   `hold_days=1` daily-SR dispersion may benchmark `hold_days=7` trade returns,
   given four pre-registrations declaring them non-comparable. A genuinely
   forward-native dispersion (e.g. across the collecting families' own forward
   SRs) is a different construct and would need its own authorization.
2. **U2** — the units/scaling that follow from U1. The `× √7` bridge is not
   authorized.
3. **U3** — the BH participating set, stated as a membership rule, not a count.
4. **U4** — what an unstated per-family minimum means.
5. *(Optional, not blocking)* whether `n_trials = 34` moves into
   `thresholds.yaml`.

---

## E. Proposed FB-10b implementation contract

**WITHHELD.** This gate's own rule: *"If U1, U2, U3 or any other value that
changes DSR/BH is not frozen, STOP at specification."* U1, U2, U3 and U4 are all
unfrozen. No contract is proposed and no value is selected by engineering
judgment.

What *can* be recorded without choosing anything is the architectural boundary
(§F), and the mandatory reproducibility metadata, which is determined by
`deflated_sr`/`bh_fdr`'s own signatures rather than by any open decision.

**Mandatory** for a stored result to be interpretable and reproducible —
governance requirements, not conveniences:

```
battery_members[]      exact set AND order (BH ranks within its set)
battery_excluded[]     with a reason per exclusion
sr_trials_std          the value used
sr_trials_std_basis    population, period units, and any scaling   (U1 + U2)
n_trials               34
q                      0.10
psr0, sr_star          the intermediates deflated_sr returns
return_basis           per that family's pre-registration
n                      observations the statistic rests on
as_of                  point-in-time anchor
calc_version           the calculation identity
```

**Implementation conveniences** (useful, not governance-load-bearing): human-
readable summaries, per-family ordering for display, cached intermediates.

---

## F. Architectural boundary for a future producer

Recorded so it need not be re-derived; **not authorization to build it.**

- **Battery-wide and operator-invoked**, never in `run_daily.py`, never on a
  timer, refusing service actors — the boundary `forward_snapshot.py` already
  holds.
- **Reuses `research/evidence/governance.py` exclusively** (`_sr_stats`, `psr`,
  `deflated_sr`, `bh_fdr`). No second statistical implementation.
- **Point-in-time**: every return satisfies `exit_date <= as_of`.
- **Appends** new L2 runs carrying `governance={...}`; **never** rewrites an
  existing row. The 11 existing live-forward rows keep `dsr_prob`/`bh_p` NULL
  permanently (FB-10a-R §5).
- **Performs no governance act** — no admission, pinning, transition, promotion
  or registry change.
- **Freezes the §E metadata into `metrics_json`** alongside the two columns.

---

## G. VERDICT: **STOP**

Implementation is **not authorized**. Four governance decisions are open (U1–U4),
frozen text argues against the only available resolution of U1/U2, and no family
is eligible regardless.

**Recommended next gate: none in this track.** FB-10b should not be reopened
until either (a) U1–U4 are decided, or (b) a family approaches its own minimum n
— whichever is later. Since the leading family is 8 trades against a 15 minimum
and accrues roughly one per week, that is months away, and the governance
decisions can be taken at leisure rather than under schedule pressure.

Work available meanwhile, unaffected by this stop: the remaining four families'
readiness snapshots (FB-12k §7), and the `min_pairwise_n` / INC-0005 pairwise
decisions left open at FB-10c-G.

---

## H. Trail

| Gate | Outcome |
|---|---|
| FB-10a-R | Forward DSR/BH deferred; FR-1..FR-7 frozen; U1/U2/U3 opened |
| FB-12k | First snapshot written; readiness satisfied; DSR/BH identified as the blocker |
| **FB-10b-pre** | **This record. STOP — U1–U4 unfrozen, and zero families eligible** |
| FB-10b | **Not authorized** |
