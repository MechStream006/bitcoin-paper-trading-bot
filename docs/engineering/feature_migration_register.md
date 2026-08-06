# Feature Migration Register

| Field | Value |
|---|---|
| **Milestones covered** | WBS 2.4 (Feature Specification migration) — §§1–4 below; WBS 2.5 (Feature Equivalence Demonstration) — §5 below; WBS 2.6 (Warm-up Contract) — §6 below; WBS 2.7 (Conditioning Propagation Rule) — §7 below |
| **Roadmap phase** | Phase 2 — Feature Store |
| **Source of migrated computations** | `research/evidence/engine.py`'s `load_master()` (unmodified by any of the four milestones) |
| **Destination** | `research/feature_store/registry.py`'s Feature Definitions + Feature Specifications, real local `research/feature_store/feature_store.db` |
| **Date** | 2026-08-05 (WBS 2.4); 2026-08-05 (WBS 2.5); 2026-08-05 (WBS 2.6); 2026-08-05 (WBS 2.7) |

This register is extended, not rewritten, as each Feature Store milestone completes. §§1–4 record WBS 2.4 exactly as originally written. §5 was added for WBS 2.5. §6 was added for WBS 2.6. §7 is new, added for WBS 2.7. **Phase 2's full WBS list (2.1–2.7) is now complete** — see §7's closing note.

## 1. Method

**Stated once rather than per row:** every migrated feature is a **thin wrap**, never a rewrite. `research/feature_store/evidence_engine_bindings.py` defines one extraction function per column — `df = engine.load_master(); return df[<name>].tolist()` — calling the existing, unmodified `load_master()` computation verbatim. `research/feature_store/migrate_evidence_engine_features.py` registers a Feature Definition + Feature Specification for each, wires its binding, and activates it only after a real Tier B equivalence check (binding output vs. an independent direct `load_master()` call, compared via NaN-safe JSON-string equality) passes against the real local research corpus. `engine.py` itself was read, never edited, for the entire milestone.

---

## 2. Migrated (17 of 18 `load_master()` derived columns)

| Column | Original implementation (`engine.py`) | Registered Specification (`binding_id`) | Migration status | Equivalence verified |
|---|---|---|---|---|
| `basis_bps` | `(perp_close/close - 1) * 10_000` | `evidence_engine.basis_bps` | Active | Yes — real data |
| `trend_1h` | `_tf_trend_daily(df_1h, 50, 200)` | `evidence_engine.trend_1h` | Active | Yes — real data |
| `trend_4h` | `_tf_trend_daily(df_4h, 50, 200)` | `evidence_engine.trend_4h` | Active | Yes — real data |
| `trend_1d` | daily 50/200 MA crossover | `evidence_engine.trend_1d` | Active | Yes — real data |
| `log_ret` | `np.log(close / close.shift(1))` | `evidence_engine.log_ret` | Active | Yes — real data |
| `rv_21d` | 21-day log-return std, annualised (`sqrt(365)`) | `evidence_engine.rv_21d` | Active | Yes — real data |
| `vol_regime` | rolling-tercile label over `rv_21d`, 252-day window | `evidence_engine.vol_regime` | Active | Yes — real data |
| `funding_regime` | rolling-tercile label over funding rate, 252-day window | `evidence_engine.funding_regime` | Active | Yes — real data |
| `market_regime` | `hypotheses.shared.market_regimes.build_market_regimes()` (SMA200 + 20-day slope) | `evidence_engine.market_regime` | Active | Yes — real data |
| `dvol_frac` | `dvol_close / 100` | `evidence_engine.dvol_frac` | Active | Yes — real data |
| `rv30` | 30-day realised vol, annualised | `evidence_engine.rv30` | Active | Yes — real data |
| `atr_ratio` | `atr14 / atr14.shift(10)` | `evidence_engine.atr_ratio` | Active | Yes — real data |
| `dvol_pctile` | DVOL trailing percentile rank, 252-day window, `min_periods=126` | `evidence_engine.dvol_pctile` | Active | Yes — real data |
| `dvol_ivrank` | DVOL min-max IV-rank, 252-day window, `min_periods=126` | `evidence_engine.dvol_ivrank` | Active | Yes — real data |
| `vol_of_vol` | 21-day std of DVOL daily log-changes | `evidence_engine.vol_of_vol` | Active | Yes — real data |
| `vrp_spread` | `dvol_frac - rv30` | `evidence_engine.vrp_spread` | Active | Yes — real data |
| `vrp_logratio` | `log(dvol_frac / rv30)` | `evidence_engine.vrp_logratio` | Active | Yes — real data |

All 17: `engine_version="v1"`, `conditioning_rule="preserving"`, `sensitivity_variants=[]`, registered/activated by `migrate_evidence_engine_features.py` (actor `migrate_evidence_engine_features`), phase **Active** in the real local `feature_store.db` (verified: 17 `feature_definitions` rows, 17 `feature_specifications` rows, all `phase='Active'`, stable across three consecutive `--commit` re-runs).

**Equivalence verification method:** each Specification's Tier B activation check (`registry.activate_specification()`) ran the feature's binding function AND an independent direct `engine.load_master()[<name>].tolist()` call in the same process, comparing via `json.dumps(..., sort_keys=True)` string equality (NaN-safe). This ran for real against the real local research corpus (274 parquet files under `research/data/raw/`; `load_master()` returns 1987 rows) during the actual `--commit` run — not only against synthetic test fixtures. Additionally, `tests/test_evidence_engine_bindings.py::TestAgainstRealLocalData` and `tests/test_migrate_evidence_engine_features.py::TestAgainstRealLocalData` re-run this same real-data comparison independently under pytest, gated to skip only if the real corpus is absent.

**Empirical falsifiability (TV3):** a deliberate, temporary sabotage of `rv_21d`'s binding (made to extract `log_ret` instead) was applied and reverted. Both real-data equivalence tests correctly failed (`SpecificationRejected: Tier B binding equivalence check returned False`), confirming the equivalence check has real discriminating power, not merely "runs without error." The source was verified byte-identical to its pre-sabotage state after reverting.

---

## 3. Deliberately excluded (1 of 18)

| Column | Original implementation | Why excluded | Where it belongs instead |
|---|---|---|---|
| `ret_fwd1` | `m["close"].shift(-1) / m["close"] - 1` | **Structurally a label, not a feature.** ADR-I011 §1.1's Central Asymmetry: *"A feature is a deterministic function of the corpus up to t... A label depends on data after t."* `ret_fwd1` is computed via a **negative** shift — it looks one period into the future by construction. Registering it as a Feature Specification would violate the exact type distinction ADR-I010/ADR-I011 exist to enforce; this is not a hypothetical concern, it is the constitutional line those two ADRs draw. | A future Label Store migration (ADR-I011, Phase 3 of the roadmap) — not this milestone. |

Two module-level assertions in `migrate_evidence_engine_features.py` make this exclusion structurally enforced, not just documented: `_MIGRATED_NAMES == set(bindings.BINDINGS.keys())` (the declarative `FEATURES` table and the bindings module cannot drift apart silently) and `"ret_fwd1" not in _MIGRATED_NAMES` (an explicit, permanent guard — any future edit that accidentally added `ret_fwd1` to either table would fail at import time, not silently ship).

---

## 4. Remaining unmigrated features (as of WBS 2.4)

**None**, within WBS 2.4's declared scope. All 17 of `load_master()`'s 18 derived columns that qualify as features (per ADR-I011's feature/label distinction) are migrated and Active. The one remaining column (`ret_fwd1`) is deliberately out of scope for this milestone, belonging to a future Label Store migration instead — it is not "remaining Feature Store work," it is a different store entirely.

Beyond `load_master()`'s own derived columns, `research/evidence/signals.py`'s 38 `SignalSpec` objects were investigated during this milestone's scoping and ruled **out of scope**: they are strategy-level trading-signal constructs (composed from the same underlying features, generating buy/sell/hold decisions), not themselves features per ADR-I010's definition. They are not tracked here as "remaining" because they were never part of this milestone's target — the roadmap's "~20 shared derived features" language refers to `load_master()`'s own columns, which this migration now fully covers (17 Active + 1 correctly excluded = 18/18 accounted for).

**WBS 2.4 incidents:**

- **[`INC-0002`](incidents/INC-0002-feature-migration-idempotency.md)** — the migration script was not actually idempotent on first implementation (re-running unconditionally created new Definition versions, doubling the registry). Fixed via `registry.get_latest_definition()` + `migrate_evidence_engine_features._get_or_register_definition()`. Verified idempotent across three consecutive real `--commit` runs.
- **[`INC-0003`](incidents/INC-0003-engine-module-name-collision.md)** — under full-suite `pytest` collection, a `sys.modules['engine']` name collision with `research/dq/engine.py` (an unrelated subsystem) silently broke 37 tests + 16 collection errors in WBS 2.4's own new test files. Fixed via `importlib.util.spec_from_file_location()`-based import isolation in `evidence_engine_bindings.py`. A generalisable instance of the same risk pattern was found elsewhere in the repository and registered as [`EH-002`](hazard_register.md#eh-002--bare-import-engine-is-not-collision-safe-across-subsystems); see §5 below for its resolution.

---

## 5. WBS 2.5 — Feature Equivalence Demonstration (PS1 / ADR-I010 §8.3)

ADR-I010 §1 audits the platform's **5 SHA-256 hash-pinned decision functions**, each a *second*, frozen implementation of a computation that also exists in `research/evidence/signals.py`'s research form — the exact "dual implementation without an equivalence guarantee" ADR-I010 names as **PS1**. §8.3's resolution: the research implementation is authoritative; a frozen implementation "must demonstrate equivalence against the authoritative one." This is that demonstration, for all 5 — neither side rewritten.

| `signal_id` | Frozen implementation | Research (authoritative) implementation | Equivalence status | Verification evidence |
|---|---|---|---|---|
| `g1_market_regime_sma200` | `decision_function_g1_market_regime.compute_signal(close)` | `signals.sig_market_regime(m, win=200, lag=20)` | **Equivalent — demonstrated** | `tests/test_decision_function_research_equivalence.py::TestG1MarketRegimeEquivalence`, exact array match over the full real corpus (1987 rows) |
| `h5_vol_regime` | `decision_function_h5.compute_signal(close)` | `signals.sig_volatility_regime(m, rv_win=21, pctile=33, regime_win=252)` | **Equivalent — demonstrated** | `TestH5VolRegimeEquivalence`, exact array match over the real corpus. Compared against `sig_volatility_regime`, not `sig_h5_vol_regimes` — the frozen file's own docstring names this as the correct target (`sig_h5_vol_regimes` only reads a precomputed column; the RV21 computation itself is what's hashed) |
| `g3_momentum_tsmom` | `decision_function_g3_tsmom.compute_signal(close)` | `signals.sig_momentum(m, k=30)` | **Equivalent — demonstrated** | `TestG3MomentumTsmomEquivalence`, exact array match over the real corpus |
| `g3_atr_expansion` | `decision_function_g3_atr_expansion.compute_signal(ohlcv)` | `signals.sig_atr_expansion(m, n=14, lag=10)` | **Equivalent — demonstrated** (pre-existing + extended) | Pre-existing `research/forward_evidence/tests/test_atr_expansion.py::TestDecisionFnAtr::test_DFA07_cross_validation_vs_signals_py` (synthetic data, exact match) **plus** new `TestG3AtrExpansionEquivalence` (real corpus, exact match) |
| `ivrv_vrp_spread` | `decision_function_ivrv.compute_signal(closes, dvol)` | `signals.sig_vrp_spread(m, pctile=33, rw=252)` applied to `engine.py`'s own `vrp_spread` column | **Equivalent — demonstrated** | `TestIvrvVrpSpreadEquivalence`, exact array match over the real corpus. Compared via `m["vrp_spread"]` (the same column WBS 2.4 registered as the Active `evidence_engine.vrp_spread` Specification), not `sig_vrp_spread` in isolation — the frozen file's own docstring gives the same "not the thing being hashed" reasoning as h5 |

**Verification method.** All 5 comparisons run `numpy.testing.assert_array_equal` between the frozen `compute_signal()` output and the corresponding research `sig_*()` output, both computed fresh in the same test over the real local research corpus (`research/evidence/engine.py`'s `load_master()`, 1987 rows, the same corpus WBS 2.4 verified 17 features against). All 10 tests (`5 signals × {equivalence, non-trivial-signal sanity}`) pass. See `tests/test_decision_function_research_equivalence.py`.

**Empirical falsifiability (TV3).** `decision_function_g1_market_regime.py`'s bull/bear position assignment was temporarily swapped (long↔short) and reverted. The equivalence test correctly failed — 1444 of 1987 positions (72.7%) mismatched — before the fix, and passed clean after reverting (confirmed byte-identical via `git diff`). This confirms the equivalence check has real discriminating power against an actual sign-error class of defect, not merely "runs without error."

**Retirement eligibility.** Per this milestone's own instruction ("verify equivalence before considering any legacy implementation retired"): **none of the 5 frozen decision functions are recommended for retirement.** Equivalence being demonstrated is a precondition for retirement eligibility, not retirement itself — ADR-I010 §8.3's model keeps the frozen implementation registered as a *conformant binding*, coexisting with the authoritative research implementation, specifically because "a frozen implementation with a different execution profile" exists for **legitimate operational reasons** (independent of `engine.py`'s preprocessing pipeline, hash-pinned so forward collection is provably unchanged run to run). Demonstrating equivalence closes PS1's *testing* gap; it does not remove the reason the second implementation exists. No Feature Specification retirement (`registry.retire_specification()`) was performed or is recommended by this milestone.

**Incident discovered during this milestone:**

- **A third instance of `EH-002`'s collision pattern was found and fixed, not merely logged.** `research/forward_evidence/tests/test_atr_expansion.py::TestDecisionFnAtr::test_DFA07_cross_validation_vs_signals_py` — the pre-existing cross-validation test this milestone's own new tests extend — imports `signals.py` via a bare `from signals import sig_atr_expansion`, which transitively hits `signals.py`'s own module-level `from engine import SignalSpec`. This is the exact `sys.modules['engine']` collision `INC-0003` root-caused: reproduced deterministically (`ImportError: cannot import name 'SignalSpec' from 'engine'`) by running `pytest tests/test_dq_engine.py research/forward_evidence/tests/test_atr_expansion.py::...::test_DFA07...` — confirming this was a real, previously-documented-as-"intermittent" (Program P1 Increment 6) full-suite flake, not a hypothetical risk. Fixed with the identical scoped-`sys.modules`-override technique already used in `evidence_engine_bindings.py`, applied directly inside the one test method (no change to `signals.py`, `research/dq/engine.py`, or the rest of the test file). Re-verified: fails without the fix (reproduced via `git stash`), passes with it, in both the deliberately-poisoned order and the true repo-wide full suite. `EH-002`'s "Known instances" table is updated accordingly (§ below); this is no longer an open instance.

---

## 6. WBS 2.6 — Warm-up Contract (ADR-I010 §8.4, §12.4 Tier C)

Full lifecycle, refusal-behaviour, activation-condition, and rollback-condition documentation lives in `docs/engineering/warmup_contract.md` (this section records status only, per the register's own per-milestone-status purpose).

Every one of the 17 migrated Feature Specifications now has its declared `warmup_periods` **enforced** (not merely declared) at the one real resolution-shaped point in this codebase (`migrate_evidence_engine_features.py`'s Tier B equivalence check), governed by `research/feature_store/warmup_policy.yaml`'s `mode` key.

| Column | Declared `warmup_periods` | Real corpus available (rows) | Warm-up check result (any mode) |
|---|---|---|---|
| `basis_bps` | 0 | 1987 | Sufficient |
| `trend_1h` | 200 | 1987 | Sufficient |
| `trend_4h` | 200 | 1987 | Sufficient |
| `trend_1d` | 200 | 1987 | Sufficient |
| `log_ret` | 1 | 1987 | Sufficient |
| `rv_21d` | 21 | 1987 | Sufficient |
| `vol_regime` | 273 | 1987 | Sufficient |
| `funding_regime` | 252 | 1987 | Sufficient |
| `market_regime` | 220 | 1987 | Sufficient |
| `dvol_frac` | 0 | 1987 | Sufficient |
| `rv30` | 30 | 1987 | Sufficient |
| `atr_ratio` | 24 | 1987 | Sufficient |
| `dvol_pctile` | 126 | 1987 | Sufficient |
| `dvol_ivrank` | 126 | 1987 | Sufficient |
| `vol_of_vol` | 22 | 1987 | Sufficient |
| `vrp_spread` | 30 | 1987 | Sufficient |
| `vrp_logratio` | 30 | 1987 | Sufficient |

**Every declared warm-up is satisfied by the real corpus today, even under hard-refuse mode** — confirmed by `tests/test_migrate_evidence_engine_features.py::TestAgainstRealLocalData::test_real_corpus_satisfies_every_declared_warmup_even_in_refuse_mode`, which runs `migrate.run(db_path, warmup_mode="refuse")` against the genuine, unmocked corpus and asserts zero refusals. **Shipped mode is `warn`** (the rollout default, roadmap §16 R5), so this table's "Sufficient" outcome is unaffected by the mode currently in force — no feature's registration status changed as a result of this milestone.

**Empirical falsifiability, both at the unit level and against the real, live script:**
- `tests/test_warmup.py` — 22 unit tests, TV3-verified (temporarily changed `>=` to `>` in the boundary comparison; 4 tests correctly failed; reverted, confirmed byte-identical).
- `tests/test_migrate_evidence_engine_features.py::TestWarmupIntegration` — 8 tests against a mocked, artificially-shrunk corpus (50 rows), proving all three modes (`disabled`/`warn`/`refuse`) behave distinctly and correctly when history genuinely is insufficient (8 of the 17 features have `warmup_periods > 50`).
- **Live-script TV3 (manual, real database):** `rv_21d`'s declared `warmup_periods` was temporarily inflated from 21 to 5000 in the real, unmodified `migrate_evidence_engine_features.py`, then run three ways against the real 1987-row corpus: `--warmup-mode refuse` → `rv_21d` genuinely skipped (`phase=Warmup-Refused`, 16 Active/1 not), all other 16 spec_hashes unchanged; `--warmup-mode warn` → `rv_21d` registered with a `WARNING:` line and a *new* spec_hash (content changed, as expected); reverted to `warmup_periods=21`, `feature_store.db` deleted and cleanly regenerated, confirmed identical to the pre-sabotage 17 spec_hashes (including `rv_21d`'s original `05b40acefc52a04f...`).

**Retirement eligibility.** N/A — this milestone changes no Specification's phase; it adds an enforcement mechanism, not a migration decision. All 17 remain Active.

---

## 7. WBS 2.7 — Conditioning Propagation Rule (ADR-I010 §9.1)

`registry.py`'s `conditioning_rule` field has existed since WBS 2.1 (Tier A already refused an unrecognised value); WBS 2.4's migration hardcoded `conditioning_rule="preserving"` for all 17 features in a single line inside `run()`. This milestone makes the declaration **explicit per Specification** (each `FEATURES` entry now names its own `conditioning_rule`, read by `run()` rather than hardcoded) and adds the **propagation computation** ADR-I010 §9.1 actually specifies: deriving each feature's real, propagated conditioning statement from its declared `input_requirements`, resolved against the real ingest writers' own recorded population statements (`research/ingest/dvol.py`, `funding.py`, `binance_ohlcv.py`) — not merely declaring a rule name.

| Feature | Conditioning rule | Input source(s) | Conditioning status |
|---|---|---|---|
| `basis_bps` | preserving | `spot_1m`, `perp_1m` | **Resolved** — 2 distinct input statements carried forward |
| `trend_1h` | preserving | `spot_1m` | **Resolved** — 1 input statement |
| `trend_4h` | preserving | `spot_1m` | **Resolved** — 1 input statement |
| `trend_1d` | preserving | `spot_1m` | **Resolved** — 1 input statement |
| `log_ret` | preserving | `spot_1m` | **Resolved** — 1 input statement |
| `rv_21d` | preserving | `spot_1m` | **Resolved** — 1 input statement |
| `vol_regime` | preserving | `spot_1m` | **Resolved** — 1 input statement |
| `funding_regime` | preserving | `funding_raw` | **Resolved** — 1 input statement |
| `market_regime` | preserving | `spot_1m` | **Resolved** — 1 input statement |
| `dvol_frac` | preserving | `dvol_daily` | **Resolved** — 1 input statement |
| `rv30` | preserving | `spot_1m` | **Resolved** — 1 input statement |
| `atr_ratio` | preserving | `spot_1m` | **Resolved** — 1 input statement |
| `dvol_pctile` | preserving | `dvol_daily` | **Resolved** — 1 input statement |
| `dvol_ivrank` | preserving | `dvol_daily` | **Resolved** — 1 input statement |
| `vol_of_vol` | preserving | `dvol_daily` | **Resolved** — 1 input statement |
| `vrp_spread` | preserving | `dvol_daily`, `spot_1m` | **Resolved** — 2 distinct input statements carried forward |
| `vrp_logratio` | preserving | `dvol_daily`, `spot_1m` | **Resolved** — 2 distinct input statements carried forward |

**All 17 declare `preserving`; none narrows or replaces conditioning.** Confirmed by `tests/test_migrate_evidence_engine_features.py::TestFeaturesTableConsistency::test_all_seventeen_declare_preserving`, run against the real, live `FEATURES` table (not a snapshot) so any future drift is caught immediately. `narrowing` and `cross-sectional` are implemented and unit-tested (`tests/test_conditioning.py`) but have no real Specification exercising them yet — honestly stated, not implied.

**Every declared source resolves today** — confirmed against real ingest-writer text (not mocks) by `tests/test_migrate_evidence_engine_features.py::TestAgainstRealLocalData::test_real_sources_resolve_conditioning_for_all_seventeen`, and by the actual `--commit` run against the real database (all 17 remain `Active`, spec_hashes unchanged from WBS 2.6). **Conditioning is never dropped** (ADR-I010 §9.1): the three dual-source features (`basis_bps`, `vrp_spread`, `vrp_logratio`) each carry **both** their distinct input conditioning statements forward, joined, not collapsed to one.

**Empirical falsifiability, both at the unit level and against the real, live script:**
- `tests/test_conditioning.py` — 24 tests (17 base-env-compatible pure-logic + mocked-source-map tests, 7 real-ingest-writer tests gated on pandas availability), TV3-verified (temporarily removed the dedup step from `propagate_conditioning()`; the duplicate-input test correctly failed; reverted, confirmed byte-identical).
- `tests/test_migrate_evidence_engine_features.py::TestConditioningIntegration` — 4 tests, including one that monkeypatches `rv_21d`'s declared source to an unknown value and confirms `run()` refuses only that one feature (`phase="Conditioning-Refused"`) while the other 16 remain unaffected.
- **Live-script TV3 (manual, real database):** `log_ret`'s declared `input_requirements` was temporarily changed to reference a nonexistent source (`spot_1m_bogus`) in the real, unmodified `migrate_evidence_engine_features.py`, then run with `--commit` against the real corpus: `log_ret` genuinely refused (`REFUSED (conditioning): log_ret: no known conditioning statement for source 'spot_1m_bogus'`, `phase=Conditioning-Refused`), all other 16 spec_hashes unchanged. Reverted; `feature_store.db` deleted and cleanly regenerated; confirmed identical to the pre-sabotage 17 spec_hashes (including `log_ret`'s original `12dbdacd1a30ad49...`).

**Incident discovered during this milestone:**

- **[`INC-0004`](incidents/INC-0004-conditioning-sys-path-stdlib-shadow.md)** — the first implementation of `conditioning.py`'s `_source_conditioning_map()` inserted `research/ingest/` at `sys.path[0]` to import the real ingest writers, following this codebase's usual sibling-import convention. `research/ingest/calendar.py` (a real, unrelated WI-5 module) shares its name with Python's own stdlib `calendar` module, and inserting at position 0 shadowed it — breaking `pandas`'s own import chain for the rest of the process (`AttributeError: module 'calendar' has no attribute 'day_abbr'`, deep inside `pandas._libs.tslibs`). Caught during initial manual verification, before any test was written. Fixed by appending rather than inserting at the front (`sys.path.append(...)`), with a regression test (`test_does_not_shadow_stdlib_calendar`) added to `tests/test_conditioning.py`. No Hazard Register entry — novel, fully self-contained, fixed before ever shipping, confirmed by search to have no other instance in the repository.

**Retirement eligibility.** N/A — this milestone changes no Specification's phase; all 17 remain Active with unchanged spec_hashes.

---

## Phase 2 (Feature Store) — WBS 2.1–2.7 complete

This is the closing entry for Phase 2's own Work Breakdown Structure (`docs/engineering/implementation_roadmap_v1.md` §6, "Phase 2 — Feature Store", items 2.1 through 2.7). All seven items have shipped:

| WBS | Milestone | Verification record |
|---|---|---|
| 2.1–2.3 | Feature Registry + Value Store | `docs/engineering/verification/records/wbs-2.1-2.3-feature-store-registry.md` |
| 2.4 | Feature Specification migration | `docs/engineering/verification/records/wbs-2.4-feature-migration.md` |
| 2.5 | Feature Equivalence Demonstration | `docs/engineering/verification/records/wbs-2.5-feature-equivalence-demonstration.md` |
| 2.6 | Warm-up Contract | `docs/engineering/verification/records/wbs-2.6-warmup-contract.md` |
| 2.7 | Conditioning Propagation Rule | `docs/engineering/verification/records/wbs-2.7-conditioning-propagation.md` |

Per the roadmap's own dependency table (§5), Phase 2's completion does not by itself gate anything further within Phase 2 — the next roadmap phase this unblocks is Phase 3 (Label Store), which reuses Phase 2's registry pattern but does not require it as a hard dependency, and Phase 4 (Dataset Builder), which requires Phases 1, 2, and 3 all complete. Neither is started by this milestone; both remain future, separately-authorised work.
