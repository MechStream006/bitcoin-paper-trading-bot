# FB-10b-P — Forward DSR/BH Producer Specification

**Date: 2026-08-31 (UTC) · Gate FB-10b-P · READ-ONLY · SPECIFICATION ONLY.**

Nothing was computed, written, deployed, activated or modified. No lockbox was
accessed. This record specifies a producer; it does not authorize one.

Governing contract: `docs/governance/fb10b_forward_dsr_bh_contract.md`
(FDB-1 … FDB-12, ADOPTED at FB-10b-O). Upstream chain: FB-10a-R (FR-1 … FR-7,
U1/U2/U3), FB-10b-G/J/K/L/M/N.

**Six items are NOT determined by the adopted contract or by existing
authoritative code. Four of them block implementation. They are listed in §5 and
are not resolved here.**

---

## 1. FROZEN INPUTS

| # | Input | Value | Authoritative source |
|---|---|---|---|
| 1 | Dispersion population | the **34** Phase B library signals | `signals.REGISTRY` groups `seeds, group1…group6`; **verified this gate: 34 specs, name-set identical to the 34 keys of `evidence_library.json`, zero difference either way** |
| 2 | Excluded from population | the **4** DVOL specs (`HV1`, `HV2_SPREAD`, `HV2_LOGRATIO`, `HV3`) | `signals.DVOL_FAMILY`; the `34 + 4 = 38` denominator is the discovery screen, retired by `n_trials_denominator_reconciliation.md` |
| 3 | `n_trials` | **34** | `compute_dsr_for_registration.py` "Governance constraints (FROZEN)"; CLAUDE.md §16 |
| 4 | Dispersion basis | re-measure those 34 at `hold_days = 7`, `cost_bps = 15`; **no scaling conversion**; `sr_trials_std * sqrt(7.0)` rejected for all governance use | FDB-2 |
| 5 | Recomputation window | **`2021-01-01 … 2025-05-31`** (frozen train / non-lockbox slice) | FDB-2a |
| 6 | Observed series | `trade_returns.net_return`, `exit_date <= as_of` | FDB-3 |
| 7 | Units invariant | `sr_trials_std`, `sr_star`, observed returns all **per-trade** | FDB-4 |
| 8 | `q` | **0.10** | `thresholds.yaml validated.{edge,feature}.max_bh_p`; FR-2 |
| 9 | `min_dsr_prob` | **0.50** both roles | `thresholds.yaml` |
| 10 | Family DSR minima | h5 **15** · g3_tsmom **15** · g1_market_sma200 **10** · ivrv **30** · g1_trend **30** · g3_atr **26** · g2_funding **30** | FDB-8; **recorded only as prose** — see OD-3 |
| 11 | Computability floor | `n >= 5`, mathematical only, never sufficiency | FDB-8; `governance._sr_stats` makes `psr` return `NaN` below it |
| 12 | Battery membership | frozen minimum **and** met at `as_of` **and** `n >= 5` | FDB-5 (CONFIRMED) |
| 13 | Invocation | battery-wide, operator-invoked, service actors refused, no governance act | FDB-11 |
| 14 | Statistical authority | `research/evidence/governance.py`, **sole**, unmodified | FDB-11 |
| 15 | Declared gaps | INC-0005 `2026-07-18 … 2026-08-05` | `run_date_policy.yaml immutable_gaps` |

### 1.1 Two consequences of the frozen inputs, stated rather than assumed

**Frozen input 4 overrides each spec's own `hold_days`.** Measured this gate, the
34 specs carry **18 at `hold_days = 1`, 8 at 7, and 8 at 5**. FDB-2 names 7
explicitly, so this is determined — but it means 26 of the 34 are re-measured at
a hold they were never specified with, including 18 continuous daily-position
signals that have no native trade structure at all. This is what makes OD-1 a
blocker rather than a detail.

**Frozen input 5 is satisfiable with existing machinery and without touching the
lockbox.** `run_harmonization.py:103` already slices
`m_full[m_full["day_ts"] < LOCKBOX_START_MS]` with
`LOCKBOX_START_MS = 2025-06-01T00:00Z` — exactly FDB-2a's boundary. Loading the
master and discarding lockbox rows before any statistic is *existence*, not
*spending* (FB-10b-J). The slice must be applied **before** any return array is
formed, so the discipline is structural rather than conventional.

---

## 2. AUTHORITATIVE EXISTING MACHINERY

### A. Exact authoritative input tables / files / functions

| Role | Artifact |
|---|---|
| Observed returns | `research/data/evidence_forward.db`, VIEW `trade_returns` (`schema_forward.sql`) |
| Observed reader | `ledger_sync.load_closed_trades(forward_db, signal_id, as_of)` — read-only URI, `exit_date <= as_of`, `ORDER BY entry_date, entry_id` |
| Dispersion corpus | `research/data/raw/{spot,funding,perp,dvol}/…` via `engine.load_master()` — verified present locally, contiguous `2021-01 … 2026-07` |
| Population roster | `research/evidence/signals.py` `REGISTRY["seeds"…"group6"]` (34 `SignalSpec`) |
| Return mechanics | `engine._apply_hold`, `engine._net_daily`, `engine._runs`, `engine.REALISTIC_COST = 15`, `engine.ANN = 365.0` |
| Statistics | `research/evidence/governance.py`: `psr`, `deflated_sr`, `bh_fdr` |
| Pre-registration pin | `observation_floors.resolve_pinned_prereg()` (A2a hash verification) |
| Floors payload | `observation_floors.evaluate()` then `forward_admissibility.build_payload()` |
| Readiness | `forward_readiness.build_readiness_block()` + `assert_exclusions_declared()` |
| Ledger writer | `record.append_run(..., governance={...})` |
| Idempotency probe | `ledger_sync._existing_live_forward_run()` |
| Gate consumer | `lifecycle.check_validated_preconditions()` (reads `runs.dsr_prob` / `runs.bh_p`) |

### B. Exact return series and transformations

**Observed side — fully determined.**
`trade_returns.net_return = (exit_close/entry_close - 1) * direction - cost_bps/10000`.
`direction` is in `{-1, +1}`; `cost_bps = 15.0` per `collection_config.yaml`.
Verified on production this gate: **every** closed forward trade across all
families has `hold_days_actual = 7` — 24 trades, no exceptions. So the observed
side already satisfies FDB-3/FDB-4 with no transformation whatsoever. `n` is the
row count; the array is `[float(t["net_return"]) for t in trades]`.

**Dispersion side — NOT determined.** See OD-1.

### C. Exact dispersion source and population

`sr_trials_std = numpy.std(per_trade_sharpes_of_the_34, ddof=1)`, where each
element is that signal's per-trade Sharpe on the train slice at `hold_days = 7`,
`cost_bps = 15`.

The per-signal Sharpe form **is** determined: `governance._sr_stats` computes
`sr = mean(r) / std(r, ddof=1)` on the return array with no annualisation, and
`psr` / `deflated_sr` consume exactly that. Applying the same form to a per-trade
array is the only reading consistent with FDB-4.

What is **not** determined is how the per-trade array is built (OD-1) and what
happens when a signal has too few trades to have a Sharpe at all (OD-2).

The stored `evidence_library.json` Sharpes are **not usable**: they are
annualised, `hold_days = 1`, per-day, and full-corpus (lockbox-spanning). FDB-2
excludes them explicitly. `harmonized_library.json`'s stored
`sr_trials_std_daily = 0.017989…` is likewise unusable — it is daily-unit, built
at `n_trials = 38`, and is the input to the `sqrt(7)` scaling FDB-2 rejects.

### D. Exact DSR call / signature

```python
dsr_prob, sr_star, sr_obs = governance.deflated_sr(net_returns, sr_trials_std, 34)
psr0,     _              = governance.psr(net_returns, 0.0)
```

`deflated_sr` computes
`sr_star = sr_trials_std * ((1-g)*Phi^-1(1 - 1/34) + g*Phi^-1(1 - 1/(34e)))`,
`g` = Euler-Mascheroni, then returns `psr(returns, sr_star)`. It falls back to
`psr(returns, 0.0)` with `sr_star = 0.0` when `n_trials < 2` or
`sr_trials_std <= 0` — a silent degradation the producer must detect and refuse
rather than record, since an `sr_star = 0.0` result is an undeflated Sharpe
wearing a DSR label.

### E. Exact BH universe and ranking procedure

Universe = the FDB-5 battery members at this `as_of`, and nothing else. FDB-6
forbids any p-value for a library signal not collected forward, and forbids the
`psr_approx = Phi(sr * sqrt(n-1))` shortcut that
`compute_dsr_for_registration.py` uses for its other 31 signals.

`governance.bh_fdr(pvals: dict[str, float], q=0.10)` sorts ascending by `p`, sets
`m = len(pvals)`, finds the largest `i` with `p_(i) <= (i/m)*q`, and returns per
name `{p, rank, bh_threshold = (i/m)*q, reject}`.

**What it does not return is a BH-adjusted p-value.** See OD-4 — the value that
belongs in `runs.bh_p` is undetermined, and this is the sharpest blocker in the
specification.

### F. How `n` and minimum eligibility are enforced

`n = len(load_closed_trades(...))`. A family is a member iff it has a frozen
minimum (OD-3), `n >= that minimum`, and `n >= 5`. Non-members are recorded in
`battery_excluded[]` with `below_minimum(n=<n>, min=<m>)` and receive **no** DSR
and **no** BH-p (FDB-7). Absence is never recorded as failure.

**Measured this gate on production — the battery is empty:**

| family | closed trades | DSR minimum | member? |
|---|---|---|---|
| `h5_vol_regime` | 7 | 15 | no |
| `g1_market_regime_sma200` | 7 | 10 | no |
| `g3_momentum_tsmom` | 8 | 15 | no |
| `ivrv_vrp_spread` | 6 | 30 | no |
| `g3_atr_expansion` | 3 | 26 | no (also below `n >= 5`) |
| `g1_trend_regime_ma_cross` | 0 | 30 | no |
| `g2_funding_level` | 0 | 30 | no |

This confirms the contract's §5 statement empirically. A producer built today
would correctly compute nothing.

### G. How `as_of` point-in-time semantics are enforced

Three independent mechanisms, all pre-existing:

1. Open trades are **structurally absent** from `trade_returns` (no exit row, so
   no join), so unrealised P&L cannot leak.
2. `exit_date <= as_of` in SQL excludes exits that had not happened yet.
3. The result is frozen into an append-only `runs` row; nothing is recomputed on
   read (FDB-10).

The dispersion side is point-in-time by construction: the train slice ends
2025-05-31, before forward collection began (2026-06-15).

### H. How readiness is checked

`forward_readiness.build_readiness_block(family_id, window_start, as_of,
forward_db=…, signal_id=…)` produces the block stored under key `"readiness"`,
then `assert_exclusions_declared(block)` runs before any write. `window_start`
must be the run's own — `ledger_sync._first_run_date(...)` — and carried *inside*
the block so the row and the block cannot diverge (the FB-12i-R discipline).

`readiness_gate.DefaultReadinessProvider` reads `run["metrics"]["readiness"]`
first, as recorded, and never recomputes it; `ok = not days_non_ready`. INC-0005
days land in `days_excluded` with `exclusion_basis =
"run_date_policy.yaml#immutable_gaps"`. **INC-0005 policy is not reopened.**

### I. Required output payload / schema

No ledger schema change is required. `record.append_run` merges
`{**metrics, **governance}` into `metrics_json` while promoting `dsr_prob` and
`bh_p` into their own columns, so every FDB-9 field fits in existing storage.

```
governance = {
  "dsr_prob":  float,
  "bh_p":      float,          # value UNDETERMINED — OD-4
}

metrics["forward_dsr_bh"] = {
  "schema":              "forward-dsr-bh/1",
  "as_of":               "YYYY-MM-DD",
  "calc_version":        "<engine identity>",
  "return_basis":        "trade_returns.net_return; non-overlapping 7-day; cost_bps=15",
  "n":                   int,
  "n_trials":            34,
  "q":                   0.10,
  "psr0":                float,
  "sr_star":             float,
  "sr_observed":         float,
  "sr_trials_std":       float,
  "sr_trials_std_basis": {
      "population":          "34 Phase B library signals",
      "population_members":  [ ...34 names, ordered... ],
      "units":               "per-trade",
      "hold_days":           7,
      "cost_bps":            15,
      "window":              ["2021-01-01", "2025-05-31"],
      "conversion_applied":  "none",
      "construction_rule":   "<UNDETERMINED — OD-1>"
  },
  "battery_members":  [ {family_id, n, minimum, p, rank, bh_threshold, reject}, ... ],
  "battery_excluded": [ {family_id, reason: "below_minimum(n=…, min=…)"}, ... ]
}
metrics["readiness"]              = <forward_readiness block>
metrics["forward_admissibility"]  = <floors payload>          # see OD-6
```

Every FDB-9 field is present. `battery_members[]` carries **order**, as FB-10a §4
requires, because BH ranks within its universe and an unordered membership list
cannot reproduce a stored `bh_p`.

### J. Required provenance metadata

Carried on the run row by existing writers: `provenance='live-forward'`,
`run_kind='forward'` (lane L2), `window_start` / `window_end`, `git_sha`
(`ledger_sync._git_sha()`), `fn_hash` (`ledger_sync._fn_hash()` — the locked
decision-function bytes), `engine_version`, `n_trials=34`, `run_ts`, `run_id`.
Inside the payload: `calc_version`, `sr_trials_std_basis`, the ordered battery,
`as_of`.

### K. Idempotency key

Existing precedent is `(family_id, provenance='live-forward', window_end=as_of)`
via `ledger_sync._existing_live_forward_run()`. **That key is already occupied
for every recent date by the nightly collector** — see OD-5.

### L. Ledger / write boundary

The producer may append **one `runs` row per family**, and nothing else. It must
not: write `gate_results`, write `state_transitions`, emit an
admission/transition receipt, edit `registry.yaml`, pin or re-pin, promote, run
DDL, or touch `evidence_forward.db` other than read-only, as every existing
reader does. It must refuse `actor_type` in `{service, cron, system}` before
reading anything, mirroring `forward_snapshot._REFUSED_ACTOR_TYPES`. Nothing in
the scheduled path may import it.

### M. Failure / UNEVALUABLE behaviour

Fail closed, and never write a partial or substituted value:

| condition | behaviour |
|---|---|
| battery empty | write **nothing**; report; exit 0. Not a failure (FDB-7) |
| family below its minimum | `battery_excluded[]`; no DSR, no BH-p, no row for it |
| family has no frozen minimum | **refuse the whole run** — FDB-8 forbids borrowing and defaulting |
| `n < 5` | excluded; `psr` returns `NaN` and must never be stored |
| `sr_trials_std <= 0` or non-finite | **abort** — `deflated_sr` would silently return an undeflated `sr_star = 0.0` |
| a population member yields no Sharpe | **abort** — OD-2 |
| pre-registration pin fails A2a | abort (`PreRegistrationError`) |
| readiness block excludes an undeclared day | abort (`UndeclaredGapError`) before any write |
| ledger schema missing a column | abort (`LedgerSchemaError`) — assert, never repair (ADR-P1-1) |
| corpus partition missing for the train slice | abort; never impute, never shorten the window silently |

### N. How existing offline/IS DSR code must NOT leak into forward gating

Three existing call sites compute something DSR-shaped. **None may feed the
forward gate**, and each must be excluded by an explicit test:

| source | why it must not leak |
|---|---|
| `compute_dsr_for_registration.py` | `hold_days = 1`, per-day, full corpus (lockbox-spanning), and its BH table approximates 31 of 34 p-values with `Phi(sr * sqrt(n-1))` — **banned by name in FDB-6** |
| `run_harmonization.py` | `n_trials = 38`, `DSR_CONF = 0.95`, and `sr_trials_std * sqrt(7.0)` — **rejected for all governance use by FDB-2** |
| `report.py::_dsr_proxy` | `n_trials = 38` default and an `sr_std * 0.5` dispersion heuristic; self-labelled "NON-GOVERNANCE … display proxy" |

The forward producer must not import any of the three, must not read
`harmonized_library.json`'s `sr_trials_std_daily`, and must not read
`evidence_library.json`'s stored Sharpes as a dispersion input.

### O. Tests required — see §6.

---

## 3. PROPOSED PRODUCER FLOW

New module `research/forward_evidence/forward_dsr_bh.py`, operator-invoked.

```
 0. refuse service/cron/system actor                      [determined]
 1. assert_ledger_schema / assert_forward_schema          [determined]
 2. for each of the 7 families:
       resolve_pinned_prereg -> verify A2a hash           [determined]
       read its frozen DSR minimum                        [OD-3 BLOCKER]
       n = len(load_closed_trades(fwd_db, signal_id, as_of))
       member iff minimum exists AND n >= minimum AND n >= 5   [determined]
 3. if battery empty -> report, write nothing, exit       [determined]
 4. build the dispersion, ONCE for the whole battery:
       m = load_master(); m = m[m.day_ts < 2025-06-01Z]   [determined]
       for each of the 34 specs:
           build its per-trade array at hold=7, cost=15   [OD-1 BLOCKER]
           sr_i = mean/std(ddof=1)                        [determined, given OD-1]
           if undefined                                   [OD-2 BLOCKER]
       sr_trials_std = std(sr_i, ddof=1)                  [determined]
 5. per member: deflated_sr(net, sr_trials_std, 34); psr(net, 0.0)   [determined]
 6. bh_fdr({member: p}, q=0.10)                           [determined]
       what "p" is, and what is stored as bh_p            [OD-4 BLOCKER]
 7. per member: build readiness block + assert_exclusions  [determined]
       build forward_admissibility payload                [OD-6]
 8. append ONE runs row per member                        [OD-5]
 9. print the frozen battery; write nothing else          [determined]
```

Steps 0–3, 5, 7 and 9 are fully determined by the contract and existing code.
Steps 4, 6 and 8 are not.

---

## 4. OUTPUT CONTRACT

As §2.I. Two invariants any implementation must hold, both of which follow from
FDB-4 and FDB-9 rather than from preference:

1. **`sr_trials_std_basis.units == "per-trade"` and `return_basis` per-trade.** A
   result whose two bases disagree is invalid (FDB-4) and must be refused at
   construction, not merely documented.
2. **`battery_members[]` is ordered and complete, and `battery_excluded[]` carries
   a reason per entry.** A stored `bh_p` without them is uninterpretable
   (FDB-9), because BH ranks within its universe.

---

## 5. OPEN DECISIONS / BLOCKERS

**Each of these is a governance question. None is filled in here with statistical
convention or engineering preference.**

### OD-1 — BLOCKER. The per-trade construction rule for the 34-signal re-measurement.

FDB-2 requires "sequential, non-overlapping 7-day evaluation windows,
`hold_days = 7`, `cost_bps = 15`". **Three mutually incompatible constructions
exist in authoritative code, and the contract names none of them:**

| construction | shape | why it does not settle the question |
|---|---|---|
| `engine._apply_hold(pos,7)` then `_net_daily` then `_runs` | compounds daily nets over maximal runs of constant position; cost on turnover | a fresh signal during a hold **resets the clock** (`_apply_hold` docstring), so runs routinely exceed 7 days — these are not non-overlapping 7-day blocks |
| `run_harmonization._h6_native_trades` / `run_lockbox_validation._h6_trades` | strictly non-overlapping: `close[t+7]/close[t] - 1 - cost` | **long-only** — both skip `pos[t] != 1.0`, so they cannot express the short leg most of the 34 signals take. Generalising to signed positions is a new construction |
| forward corpus `trade_returns` | `(exit/entry - 1) * direction - cost/1e4`, exactly 7 calendar days | this is the *observed* side's definition; nothing applies it to a `SignalSpec` |

The contract's own wording ("A trade *is* a non-overlapping 7-day block") matches
the third semantically, and choosing it would make dispersion and observation
genuinely commensurable — but **no function implements it for the library specs**,
and writing one is a methodological choice with a governance consequence: it sets
`sr_star` for every future forward DSR.

Sharpened by §1.1: 18 of the 34 are continuous daily-position signals with no
native trade structure, so this is not an edge case.

### OD-2 — BLOCKER. A population member with no computable Sharpe.

FDB-1 fixes the population at **34** and says it "may not be changed". If a
signal yields fewer than 2 trades on the train slice under the OD-1 rule, its
per-trade Sharpe is undefined. Dropping it changes the population (forbidden);
substituting `0.0` invents a value.

**This fails silently by default, and that is the danger:**
`governance._sr_stats` returns `0.0, n, 0.0, 3.0` for `n < 5`, so a naive
implementation would inject invented zeros into the dispersion with **no error
and no NaN**, shifting `sr_trials_std` and therefore every `sr_star`.

### OD-3 — BLOCKER. Where the producer reads the seven DSR minima.

FDB-8 was adopted in **Form 1 — prose** in
`collection_spec.evidence_snapshots.content[]`. **There is no structured key
anywhere in any pre-registration.** Verified this gate, the seven minima appear
in three different wordings at three different array indices:

```
h5_vol_regime            content[3]  "DSR on non-overlapping 7-day return blocks (compute when n >= 15)"
g1_market_regime_sma200  content[2]  "DSR on non-overlapping 7-day return blocks across all trades (when n >= 10)"
g3_momentum_tsmom        content[3]  "DSR on completed-period returns (when n >= 15)"
ivrv / g1_trend / g3_atr / g2_funding
                         content[0]  "DSR on non-overlapping 7-day forward trade returns (compute when n >= NN)"
```

FB-10b-N stated this consequence **in advance**, before the choice was made:
*"the value remains machine-unreadable, so battery membership (FDB-5) would be
enforced by the operator, not by code."* That is now the operative condition.

The three ways out are all governance acts, not engineering preferences: regex
the prose (makes a governed value depend on wording, and invents a parse
convention nothing declares); hardcode the seven in the producer (a second source
of truth for a per-family governed parameter); or add a structured key (Form 2 —
a pre-registration schema change plus seven re-hashes and seven `registry.yaml`
updates, i.e. reopening FDB-8's adopted *form*).

### OD-4 — BLOCKER. The numeric definition of `bh_p`.

`governance.bh_fdr()` returns `{p, rank, bh_threshold, reject}`. **It does not
produce a BH-adjusted p-value (q-value), and no function in the repository
does.** `lifecycle.check_validated_preconditions` compares
`pinned_run["bh_p"] <= max_bh_p` where `max_bh_p = 0.10 = q`.

The only precedent, `compute_dsr_for_registration.py`, stores the **raw**
`1 - psr(returns, 0.0)` under the name `bh_p`. Recorded as a fact, without
recommendation: under that reading, with `max_bh_p` and `q` both `0.10`, the
stored value is a raw p-value and the BH step-up **never enters the gate at all**
— `rank`, `bh_threshold` and `reject` are computed and frozen but read by
nothing, so battery membership would have no gating effect despite FDB-5 being
the contract's most carefully specified rule.

The alternatives — storing `bh_threshold`, storing a `reject` flag, or computing
a genuine adjusted q-value — each change what the Validated gate means, and the
third would require adding a statistic to `governance.py`, which this gate
forbids ("do not alter existing statistical methodology").

**FDB-9 lists `q = 0.10` among the required metadata but never states what number
`bh_p` holds. FB-10a §4 likewise specifies the surrounding metadata and not the
value.**

### OD-5 — OPEN. The write boundary against the nightly collector.

`run_daily.py:683` calls `ledger_sync.sync_all()` **every night**, appending an
L2 run at `window_end = run_date` for every family with closed trades. Verified
on production: 10 such rows exist at `window_end` 2026-08-29 and 2026-08-30,
plus one `forward-snapshot-v1` row at 2026-08-28.

`forward_snapshot` refuses to write when *any* live-forward run exists at the
same `window_end` — `ls._existing_live_forward_run()` filters on
`(family_id, provenance, window_end)` and **ignores `engine_version`**. A DSR
producer reusing that guard could never write for a date the nightly job has
already covered, which is every recent date.

FDB-10 and FDB-11 require append-only and "appends new runs rather than rewriting
existing ones", but do not say whether the DSR run (a) must target a fresh
`as_of` the collector has not reached, (b) may coexist with a same-`as_of`
collect run, or (c) is intended to supersede it via `current_run_per_lane`'s
`ORDER BY run_ts DESC, run_id DESC`.

### OD-6 — OPEN. Whether the DSR run must also carry `forward_admissibility`.

FDB-9's required-metadata list does not include it. But
`check_validated_preconditions` reads `dsr_prob` / `bh_p` from the **pinned**
run, and `ForwardAdmissibilityRule` reads the floors payload from **that same
run's** `metrics_json`. If the two payloads land on different rows, no single row
can ever satisfy both, and promotion stays structurally impossible for a reason
unrelated to evidence. Reusing `forward_snapshot.build_snapshot_metrics()` would
resolve it — but that is a design decision the contract does not make.

### Deployment prerequisite (not a decision — a fact to schedule)

Production is at **`af80fbd`**, which predates FB-10b-O. Verified on the host this
gate: all four amended pre-registrations still lack `evidence_snapshots`
entirely, and `registry.yaml` still carries the four pre-amendment hashes. The
two are mutually consistent, so nothing is broken today — but **four of the seven
DSR minima do not exist on production**, under any resolution of OD-3.

---

## 6. IMPLEMENTATION TEST PLAN

Assumes OD-1 … OD-4 resolved. Tests marked **AL** are anti-leakage.

**Population and dispersion**

1. The population is exactly 34, and its name-set equals `evidence_library.json`'s keys.
2. The 4 DVOL specs are absent from the population.
3. `n_trials == 34` is asserted, not passed as a default.
4. The train slice ends at 2025-05-31; a synthetic row dated 2025-06-01 is excluded.
5. **AL** — no lockbox-dated row reaches any return array (assert on the sliced frame, not on the call).
6. Per-signal Sharpe is `mean/std(ddof=1)` on a per-trade array (unit test against a hand-computed fixture).
7. A member with no computable Sharpe aborts (per OD-2's resolution) and **never** yields a silent `0.0` — regression test driving `_sr_stats`' `n < 5` branch directly.
8. `sr_trials_std <= 0` or non-finite aborts rather than reaching `deflated_sr`.
9. `sr_trials_std_basis.conversion_applied == "none"`.

**Battery and eligibility**

10. Membership requires all three FDB-5 conditions; one test per condition failing alone.
11. A family with no frozen minimum aborts the whole run (never borrows, never defaults).
12. `n = 4` is excluded even if its minimum were lower.
13. An empty battery writes nothing and exits 0.
14. `battery_excluded[]` reasons match `below_minimum(n=…, min=…)` exactly.
15. Membership is a pure function of `as_of`: replaying an earlier `as_of` reproduces the earlier battery.

**Statistics**

16. `deflated_sr` is called with `(returns, sr_trials_std, 34)` and nothing else — AST guard.
17. `bh_fdr` is called with the member set only, `q=0.10`.
18. **AL** — a non-collecting library family never appears in `pvals` (FDB-6).
19. **AL** — `Phi(sr * sqrt(n-1))` appears nowhere in the module (source scan).
20. Adding one member changes another member's `rank` / `bh_threshold` — proves the universe is real and frozen for a reason.

**Anti-leakage — the three forbidden sources**

21. The module imports none of `compute_dsr_for_registration`, `run_harmonization`, `report._dsr_proxy` — AST import scan.
22. `sqrt(7)` / `* 0.5` dispersion scaling appears nowhere — source scan with docstrings stripped.
23. `38` never reaches `deflated_sr`'s `n_trials`.
24. `harmonized_library.json`'s `sr_trials_std_daily` and `evidence_library.json`'s `sharpe` are never read as dispersion inputs.

**Point-in-time**

25. A trade exiting after `as_of` is excluded.
26. An open trade is excluded (structurally, via the view).
27. Adding a later trade does not change an already-written row (re-read and compare bytes).

**Readiness / payload**

28. `readiness` is present and `assert_exclusions_declared` passes.
29. A payload excluding an undeclared day raises before any write.
30. INC-0005 days land in `days_excluded` with the declared basis; `days_non_ready` stays empty for them.
31. Every FDB-9 field is present; a payload missing any one is refused.
32. `--dry-run` renders byte-identically to what a write persists (the FB-12i-R parity discipline, as its own test file).

**Boundary and actor**

33. `actor_type` in `{service, cron, system}` is refused before any read.
34. No `gate_results`, `state_transitions`, receipt, or registry write occurs — assert on table row counts before and after.
35. `evidence_forward.db` is opened read-only; a write attempt raises.
36. Nothing in the scheduled path imports the module — AST scan of `run_daily.py`.
37. A missing ledger column raises `LedgerSchemaError`; no DDL is issued.

**Real-artifact checks**

38. Against a **copy** of the real local ledger: `report.py --check` stays clean and `verify_chain()` stays valid after a simulated write.
39. The real ledger is byte-identical before and after the whole test module (module-scoped autouse hash fixture, the Increment-4 pattern).

---

## 7. MINIMAL FILE CHANGE SET

**New**

| file | purpose |
|---|---|
| `research/forward_evidence/forward_dsr_bh.py` | the producer; the only new executable file |
| `tests/test_forward_dsr_bh.py` | §6 tests 1–31, 33–39 |
| `tests/test_forward_dsr_bh_dryrun_parity.py` | §6 test 32 |
| `docs/governance/forward_dsr_bh_schema_reference.md` | payload reference, matching the per-PR precedent |

**Modified — none required.** Confirmed this gate:

- `record.append_run` already merges `{**metrics, **governance}` into
  `metrics_json` and promotes `dsr_prob` / `bh_p` into columns, so **no ledger
  schema change** and no `record.py` edit.
- `governance.py` is used unmodified — it stays the sole statistical authority.
- `thresholds.yaml`, `registry.yaml`, `collection_config.yaml`,
  `run_date_policy.yaml`, every pre-registration, `lifecycle.py`,
  `readiness_gate.py`, `forward_readiness.py`, `ledger_sync.py`,
  `forward_snapshot.py`, `run_daily.py` and every systemd unit are **untouched**.

**Conditionally modified — only under specific resolutions of the open decisions:**

| condition | consequence |
|---|---|
| OD-1 resolved to a shared trade-construction function | one additive function in `research/evidence/` — must be additive, since `_apply_hold` / `_runs` / `_net_daily` are consumed by the frozen IS battery |
| OD-3 resolved to Form 2 (structured key) | 7 pre-registration edits + 7 `registry.yaml` hash updates — reopens FDB-8's adopted form |
| OD-4 resolved to a genuine adjusted q-value | an addition to `governance.py`, which this gate forbids |
| OD-6 resolved to "carry the floors payload" | reuse `forward_snapshot.build_snapshot_metrics()`; no edit to it |

**Deployment**: production must reach a commit at or after `eee73a9` before any
producer can read four of the seven minima. Not authorized by this gate.

---

## 8. Status

The producer is **specified but NOT implementable**. Four blockers (OD-1, OD-2,
OD-3, OD-4) and two open questions (OD-5, OD-6) are governance decisions that
neither the adopted contract nor existing authoritative code determines.

Independently, the battery is **empty**: no family meets its DSR minimum, so even
a fully-resolved producer would compute nothing today.

Nothing was computed, written, deployed or modified. No lockbox was accessed.

**STOP.**

---

## 9. Trail

| Gate | Outcome |
|---|---|
| FB-10a-R | U1/U2/U3 opened; FR-1…FR-7 frozen; BH-p declared not a property of a family |
| FB-10b-G/J/K/L/M/N | U1, FDB-2, FDB-2a and the lockbox rule resolved; FDB-5/FDB-8 put to a human |
| FB-10b-O | FDB-8 Option A adopted (Form 1, prose); FDB-5 confirmed; FDB-1…FDB-12 approved |
| **FB-10b-P** | **This record. Producer specified; 4 blockers and 2 open questions identified; nothing implemented** |
