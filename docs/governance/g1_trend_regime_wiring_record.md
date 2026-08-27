# g1_trend_regime_ma_cross — Gate 6A.18 Wiring Record

**Date: 2026-08-27 (UTC) · Wired into forward collection · NOT deployed**

The repository is now configured to collect this family. **Production is not.**
Nothing was deployed, no collection run was triggered, and the scheduled job was
not touched. No performance statistic was computed and the sealed lockbox
(2025-06-01..2026-05-31) was never read.

---

## 1. U-3 frozen: `minimum_calendar_months_before_assessment = 12`

Fixed here, **before any forward observation exists** — after that it could not
be set honestly.

**This is determined by precedent, not selected.** All six collecting families
follow one rule: *months = max(time to reach `minimum_trades` at the family's
structural cadence, a regime-coverage floor).*

| Family | Role | Cadence | `minimum_trades` | Months |
|---|---|---|---|---|
| `h5_vol_regime` | feature | ~6 /yr | 30 | 60 |
| `g1_market_regime_sma200` | feature | ~5.8 /yr | 30 | 60 |
| `g3_momentum_tsmom` | edge | ~33 /yr | 30 | 12 |
| `g2_funding_level` | feature | 17.7 /yr | 30 | 12 |
| **`ivrv_vrp_spread`** | **feature** | **~52 /yr max** | **30** | **12** |
| `g3_atr_expansion` | edge | ~52 /yr max | 26 | 6 |

Where cadence is slow, sample size binds and the answer is 60. Where cadence is
fast, **regime coverage binds**, and both fast-cadence *feature* families
resolved it to 12. `ivrv_vrp_spread` states the case verbatim:

> *"n=30 forward trades at hold_days=7 (~52/yr max) is ~7-12 months. 12 months
> minimum before assessment to span vol regimes."*

This family matches that precedent exactly: **feature role, `hold_days = 7`,
near the ~52/yr structural ceiling** (train activity 93.9% of days, recorded at
6A.15-PRE), **`minimum_trades = 30`**. The only lower value on the platform — 6
months for `g3_atr_expansion` — is *edge* role with `minimum_trades = 26`, so it
is not the matching precedent.

**Inputs used:** role, `hold_days`, the structural hold ceiling, and an
already-recorded train activity fraction. **No performance quantity and no
lockbox data enters it.**

Carried alongside from U-1 (resolved 6A.17): `minimum_trades = 30` (platform
precedent, identical in `g1_market_regime_sma200` and `g2_funding_level`) and
`minimum_both_long_and_short_trades: true` (the frozen `long_short` direction;
train split 804 long / 709 short).

## 2. Two real defects the wiring exposed — both fixed

Neither was a test artifact. Both were found because the existing suite fired.

### 2.1 A wired-but-unconfigured signal took down the entire run

`run_all()` built its runner table by indexing `sigs['<id>']` directly. A signal
present in code but absent from `collection_config.yaml` raised `KeyError`
**before** the per-signal isolation loop — so one missing config entry aborted
**every** signal, the exact opposite of the DQ3 isolation contract the module
documents, and the same failure shape as INC-0005.

This mattered immediately: production at `6eb12bd` does **not** have this
signal in its config, so a code-before-config deployment would have crashed the
whole daily collection.

**Fixed** by building the runner table from what the config actually declares,
skipping an absent entry with a warning — the `sigs.get(...)` pattern the
isolated ivrv/g2/atr branches already use.

### 2.2 The new signal was missing from the governance-failure audit trail

`_blocked_signal_ids()` — which records an error `run_log` row per signal when
the governance pre-check fails — re-listed the close-only signals in a second
place. Wiring `run_all()` alone left this family out, so a governance failure
would have written an error row for **every signal except this one**: a silent
hole in an audit trail the charter requires to be complete.

**Fixed** by hoisting the roster to a single module constant
(`_CLOSE_ONLY_RUNNERS` / `_CLOSE_ONLY_SIGNAL_IDS`) consumed by both call sites,
so the two can no longer drift. Regression-tested (`test_P04b`).

## 3. Files changed

| File | Change |
|---|---|
| `research/forward_evidence/pre_registration_g1_trend_regime.json` | **new** — active pre-registration, built from the frozen draft |
| `research/forward_evidence/runner_g1_trend_regime.py` | **new** — thin runner, mirrors `runner_g1.py` |
| `research/forward_evidence/collection_config.yaml` | +16 — one signal entry with the declared independence note |
| `research/forward_evidence/run_daily.py` | wiring + the two §2 fixes |
| `research/forward_evidence/tests/test_g1_trend_regime.py` | P04/P05 inverted, P07 hash, P08 allowlist, +7 tests |
| `research/forward_evidence/tests/test_funding_loader.py` | census +1 |
| `research/forward_evidence/tests/test_run_date_policy.py` | census 6 → 7 |
| `docs/governance/g1_trend_regime_wiring_record.md` | **new** — this file |

**The frozen 6A.15 draft and the 6A.16 decision function are byte-unchanged.**
The draft stays in `drafts/` with `status: SPECIFICATION_FROZEN_NOT_ADMITTED` as
the freeze record, exactly as `drafts/g1_market_regime_sma200_prereg_draft.json`
coexists with its active pre-registration. Every block the active file carries
across is asserted byte-identical (`test_P05e`).

`thresholds.yaml`, `registry.yaml`, `runner_base.py`, `run_date_policy.py`,
`signals.py`, `engine.py` and the ledger are all untouched.

## 4. Input contract, as wired

Daily BTC spot closes via `load_daily_closes`; `MIN_HISTORY = 100`;
`HOLD_DAYS = 7`; `cost_bps = 15.0`; positions raw `{−1, 0, +1}` from the pinned
`compute_signal`. Close-only and single-input, so it runs in the shared
close-only loop — no isolated branch. Input identity is resolved through the
existing `_inputs_for(... 'price')` cache, so it shares the price-partition
identity entry with the other close-only signals.

**Fail-closed is unchanged and still upstream:** `run_signal` refuses a missing
or NaN close at `run_date` (6B.1 defect B-02) and refuses history shorter than
`MIN_HISTORY` at both the loader and the runner boundary; a refusal writes no
`run_log` row and the next scheduled run retries. `runner_base.py` was not
modified. **No `engine.py` or DuckDB dependency enters the forward path** —
asserted by parsing the runner's import graph (`test_P05b`).

`max_history` gained `_g1tr.MIN_HISTORY`. At 100 it is below the existing
maximum (`h5` = 273), so this is a no-op today; it is added so the invariant
"closes cover every signal's `MIN_HISTORY`" holds by construction rather than by
coincidence.

## 5. Residual item

**U-4 (open, cosmetic): `registry.yaml preregistration` still points at nothing
for this family.** The pointer is set on 4 of 6 collecting families and absent
on 2 — `g2_funding_level` collects with it `null` — so it is a convenience
pointer, not a gate, and collection does not depend on it. It was deferred
rather than set silently: setting it would also require editing a 6A.16 test
(`test_P06`), which this gate had no mandate to touch. Worth closing as a
platform-consistency cleanup across all six families at once, not for this
family alone.

## 6. Readiness

**g1_trend_regime_ma_cross is fully ready for collection in the repository, and
not yet collecting anywhere.** The remaining step is deployment, which is a
separate gate.

**Deployment ordering matters and is now safe either way.** Before §2.1's fix,
shipping code ahead of config would have crashed the entire daily job; it now
degrades to a warning and skips only the unconfigured signal. Both files ship
together regardless.

**Deployment should not land while 6B.7 is pending** — its first
post-provenance collection audit has a fixed baseline (run_log 254, max
`run_date` 2026-08-26, six signals), and adding a seventh signal mid-audit would
confound it.
