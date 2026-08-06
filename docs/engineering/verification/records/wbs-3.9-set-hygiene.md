# Milestone Verification Record

| Field | Value |
|---|---|
| **Milestone** | Set Hygiene — `research/label_store/set_hygiene.py` (Phase 3 completion) |
| **WBS item(s)** | 3.9 |
| **Roadmap phase** | Phase 3 — Label Store (`phase_3_label_store_roadmap.md` v1.1.0) |
| **Standard version referenced** | 1.0.0 |
| **Date** | 2026-08-06 |
| **Implementer** | Claude Sonnet 5 |
| **Reviewer** | *(pending — Rohith)* |

**WBS 3.4–3.8b semantics are unchanged, as instructed.** Every Label Store file (`registry.py`, `value_store.py`, `standing.py`, `superseded.py`, `propagation.py`, `label_receipts.py`), both stores' admissibility substrate (`lattice.py`, `composition.py`), the Feature Store, and the governance-receipt infrastructure (`receipts.py`, `receipt_ledger.py`, `migrate_bootstrap_feature_receipts.py`) show **zero diff** (`git diff --stat` confirms; the only diffs present predate this session entirely, unchanged in size from WBS 3.8b's own record). This milestone adds exactly one new module and two new test files.

**No architectural inconsistency with ADR-I011 was found.**

**This is Phase 3's completion milestone, per instruction, and end-to-end dataset integration across the completed Label Store was validated (§0, §2 TV5).** Phase 4 is explicitly not begun.

---

## 0. Design Summary (read before the checklist)

`set_hygiene.py` implements ADR-I011 §12 Tier D's four statistics and §16.3's partial-maturity refusal, all **compute-on-demand with zero schema change** — mirroring WBS 3.7's own precedent exactly.

- **`maturity_fraction()`** — the fraction of a Specification's declared population that has reached or passed maturity (Matured/Flagged/Adjudicated — all three are post-maturation states in `value_store.py`'s own five-arc machine).
- **`horizon_coverage()`** — across a Definition's registered horizon Specifications (§16.1: horizon *is* identity), which ones actually carry any declared population.
- **`outcome_distribution()`** — basic distributional stats plus a sign-based class-imbalance disclosure over the matured population's outcome values, with the numeric field auto-detected (deterministically) rather than assumed by name.
- **`standing_composition()`** — the Context / Forward evidence / Promotion evidence breakdown of a population, with an explicit unassigned bucket for NULL standing.
- **`request_fully_matured()`** — §16.3's refusal gate: refuses **by name** (spec_hash, observed fraction, and the specific not-yet-matured `subject_id`s) unless the caller explicitly passes `admit_partial=True`, in which case the matured-or-later subset is returned with the fraction recorded alongside it.
- **`set_admissibility_ceiling()`** — the one Tier D statistic with a direct lattice-dimension mapping (the ADR's own Mermaid note: `"FT -.->|maturity fraction caps admissibility| SEAL"`). Activates `lattice.py`'s `Maturity.PARTIAL` rung, defined at Phase 0 and never produced by any prior milestone (a single Label Value's `maturity_state` is a discrete state, never a fraction). A naive `composition.meet()` across a partially-matured set would collapse it to the same `DECLARED` floor as a 1%-matured set; this function meets the other four dimensions normally (weakest-link is correct there) and replaces only Maturity with the fraction-derived rung.
- **No `label_policy.yaml` was introduced.** Verified directly: no such file exists anywhere in `research/label_store/` despite the roadmap's own §10 table naming a `maturity_refusal: warn` flag for WBS 3.5/3.6/3.7 too — none of those milestones built a YAML-loaded toggle either; each satisfied "non-blocking by default" structurally (never wiring a blocking gate). This milestone follows the same real, established precedent: `admit_partial` (default `False`) is the concrete, call-site expression of §16.3's own text.

**End-to-end validation.** `TestEndToEndLabelStoreIntegration` (in `tests/test_label_store_set_hygiene.py`) exercises registration-with-receipt (3.8a), multi-subject declaration with standing (3.2/3.5), maturation, flagging (3.6's own mechanism, reused directly), adjudication-with-receipt (3.8a), per-value conditioning and admissibility (3.7), all four Tier D statistics and the set-level ceiling (3.9), §16.3's refusal-then-admission, and governance-receipt chain/replay integrity (3.8a) — one real, composed scenario spanning every completed Label Store milestone, plus a second test running the read side of the same composition against the real local `label_store.db`.

## 1. Build Verification (BV)

- [x] **BV1** — `python -m py_compile research/label_store/set_hygiene.py tests/test_label_store_set_hygiene.py` → clean in both environments.
- [x] **BV2** — Pure stdlib (`json`/`sqlite3`/`collections.Counter`/`dataclasses.replace`) plus `lattice.py`/`composition.py`/`propagation.py`/`registry.py` (none pandas-dependent for the code paths this module calls) — no environment gating anywhere in this file.
- [x] **BV3** — No new dependency.
- [x] **BV4** — Verified under both environments; identical pass counts.

## 2. Test Verification (TV)

- [x] **TV1** — `tests/test_label_store_set_hygiene.py` (new, 43 tests): Maturity Fraction (7), Horizon Coverage (6, 2 real-gated), Outcome Distribution (7, 1 real-gated), Standing Composition (4, 1 real-gated), Tier D Report (2), Request Fully Matured (6, 2 real-gated), Set Admissibility Ceiling (7, 1 real-gated), No Registry/Value Store Mutation (1, structural), No Datetime Now (1, structural), End-to-End Integration (2, one full synthetic pipeline + one real-data snapshot).
- [x] **TV2** — House style confirmed, **with one finding corrected before it caused a false failure**: the end-to-end real-data test's first draft called `label_receipts.validate_label_governance()` directly against the real local `label_store.db`, which raised `OperationalError: no such table: governance_receipts` — the real file has never had that table created on it (unlike `feature_store.db` post-WBS 3.8b's real backfill; WBS 3.8a's own real-data tests only ever exercised a disposable copy). Corrected: the governance-receipt half of that test now runs against a `tmp_path` copy, exactly mirroring WBS 3.8a's and 3.8b's own established discipline, with an added SHA-256 before/after check proving the real file itself was never touched by any part of the test.
- [x] **TV3** — Empirically verified: replaced `_set_maturity_rung()`'s core `return lattice.Maturity.PARTIAL` (for `0 < fraction < 1`) with `return lattice.Maturity.FULL` — 3 tests failed, including `test_partially_matured_set_yields_partial_maturity_not_declared`, `test_high_fraction_partial_set_is_still_partial_not_full`, and — critically — `TestEndToEndLabelStoreIntegration::test_full_pipeline`, confirming the full composed pipeline genuinely exercises this milestone's own central claim, not just its isolated unit tests. Restored; full 43-test file re-confirmed green in both environments.
- [x] **TV4** — Refusal/exclusion paths tested: `request_fully_matured()` refuses by name for a partial set (`test_refuses_by_name_when_partial_and_not_admitted` — asserts the spec_hash prefix, the specific unmatured `subject_id`, and the `n/total` fraction all appear in the message) and for an empty set even with `admit_partial=True` (`test_empty_set_always_refuses_even_with_admit_partial`); `tier_d_report()` raises `LookupError` for an unregistered `spec_hash`; `outcome_distribution()` honestly reports `disclosed=False` (never a fabricated zero/mean) when no common numeric field exists across differing outcome shapes.
- [x] **TV5** — Property/invariant tests: **Void never counts as matured** (`test_void_does_not_count_as_matured`); **Flagged/Adjudicated count as matured-or-later for the fraction, while Integrity still reflects the flag** (`test_flagged_and_adjudicated_count_as_matured_or_later`, `test_flagged_row_drags_integrity_down_even_at_full_maturity` — proving `set_admissibility_ceiling()`'s split treatment of Maturity vs. the other four dimensions is real, not incidental); **the PARTIAL rung is not merely reachable but is the CORRECT answer at both low and high fractions** (`test_high_fraction_partial_set_is_still_partial_not_full` — a 9-of-10 matured set is still `PARTIAL`, guarding specifically against a regression to the naive per-value-meet's `DECLARED`); **no write path exists** (`test_module_issues_no_write_statement`, a structural substring guard); **the roadmap's own exit criterion, exercised literally against real data** — `TestEndToEndLabelStoreIntegration::test_real_data_end_to_end_snapshot` composes every completed milestone's real output together (all four Tier D statistics, the set ceiling, and the already-known, already-disclosed `LREC1` gap from WBS 3.8a, unchanged) without ever mutating the real file (SHA-256-verified).

## 3. Regression Verification (REG)

- [x] **REG1** — `btc_bot` env, `python -m pytest tests/ -q --ignore=tests/test_api.py`:
  - Before (after WBS 3.8b): **1990 passed, 19 skipped, 0 failed**
  - After: **2033 passed, 19 skipped, 0 failed**
  - Delta: **+43 passed**, exactly matching the new test count.
  - Base env, full suite (`python -m pytest tests/ -q`): **1915 passed, 60 skipped, 0 failed** (before: 1872/60 — delta **+43**, zero skips from this file in either environment).
  - Maximal collision-risk combination (all six Label Store test files, both Feature Store test files, `test_conditioning.py`, `test_governance_receipts.py`, `test_migrate_bootstrap_feature_receipts.py`): **439/439 passed**.
- [x] **REG2** — Skip count unchanged in both environments.
- [x] **REG3** — No pre-existing flake recurred.
- [x] **REG4** — CI workflow unchanged.

## 4. Performance Verification (PERF)

**N/A** — real-scale row counts (37–148 rows per Specification) read once per statistic call; no corpus-scale loop.

## 5. Database Verification (DBV)

**Applicability: READ-ONLY.** `set_hygiene.py` issues no `INSERT`/`UPDATE`/`DELETE` anywhere (structurally guarded, `TestNoRegistryOrValueStoreMutation`) — every statistic is a live `SELECT` over `label_specifications`/`label_value_forward`, computed and returned, never persisted.

- [x] **DBV1** — N/A — no schema change of any kind.
- [x] **DBV2** — N/A — no write path exists to gate.
- [x] **DBV3** — Verified via the real test suite, run directly against the real local `label_store.db` (`REAL_SPOT_1D_HORIZON5`, the real 37-subject, fully-matured horizon-5 `spot_one_day_forward_return` Specification): `maturity_fraction`=1.0, `horizon_coverage`=4/5 populated (the original, never-populated `ret_fwd1` horizon-1 Specification correctly shows up as the one gap), `outcome_distribution` auto-detects `forward_return` over all 37 matured rows, `standing_composition`={"Context": 37}, and `set_admissibility_ceiling()` correctly reports `Maturity.FULL` / `Certification.UNKNOWN` (honest, uncertified, per WBS 3.7's own established finding) / `Integrity.CLEAN` (0 real flags, matching WBS 3.6/3.7's own independently-confirmed count). `request_fully_matured()` against the real, fully-matured horizon-5 Specification succeeds (37 rows, `admitted_partial=False`); against the real, entirely-unpopulated horizon-1 Specification, it correctly refuses (`PartialMaturityRefusalError`, empty-set branch). Every real-data assertion re-confirmed byte-unchanged (SHA-256 before/after) on the real file itself.
- [x] **DBV4** — N/A — no governance ledger written by this module; the one governance-receipt check in the real-data test runs against a disposable copy (§2 TV2).
- [x] **DBV5** — N/A — no real data written.
- [x] **DBV6** — N/A — unchanged from prior findings.

## 6. Backward Compatibility (BC)

- [x] **BC1–BC4** — `registry.py`, `value_store.py`, `standing.py`, `superseded.py`, `propagation.py`, `label_receipts.py`, `lattice.py`, `composition.py`, `receipts.py`, `receipt_ledger.py`, both Feature Store files, `migrate_bootstrap_feature_receipts.py` — **zero diff to any of them**. `git diff --stat` shows only the same pre-session-predating hunks already fully disclosed at WBS 3.8a's and 3.8b's own records, unchanged in size.
- [x] **BC5** — Confirmed by direct search: `grep -rl "set_hygiene\|maturity_fraction\|horizon_coverage\|outcome_distribution\|standing_composition\|request_fully_matured\|set_admissibility_ceiling\|PartialMaturityRefusalError" --include="*.py" .` returns only this milestone's own two new files.

## 7. Rollback Verification (RBV)

- [x] **RBV1** — Delete `research/label_store/set_hygiene.py` and `tests/test_label_store_set_hygiene.py`. Nothing else references any of its symbols (BC5); every other Label Store module remains fully functional and independently usable without it — this module only ever calls into them, never the reverse.
- [x] **RBV2** — No production data touched; every real-data test is read-only against the real `label_store.db` (SHA-256-verified unchanged) or writes only to a `tmp_path` copy.
- [x] **RBV3** — Exercised directly: TV3's sabotage/revert cycle.
- [x] **RBV4** — N/A — no governance ledger record in scope beyond what WBS 3.8a already covers.

## 8. Technical Debt Assessment (TD)

| What | Why deferred | Closed by (WBS ID) | Blast radius if never closed |
|---|---|---|---|
| Only `maturity_fraction` has a direct lattice-dimension mapping (`set_admissibility_ceiling()`); `horizon_coverage`, `outcome_distribution`, and `standing_composition` are pure disclosures with no lattice consequence of their own | `ADR-I014`'s five-dimension lattice has no dimension corresponding to horizon coverage, outcome shape, or standing mix — `§12`'s "cap admissibility rather than blocking" reads correctly as a report for these three, not a gate, and inventing a new lattice dimension for them would be exactly the "architectural redesign" this milestone is instructed to avoid | Not scheduled — would require an `ADR-I014` amendment, a governance decision outside this milestone's authority | None today — Phase 4's Dataset Builder (the roadmap's own named consumer of DI5's meet) can consult these three disclosures directly wherever it composes a dataset; nothing in this milestone forecloses that |
| `outcome_distribution()`'s class-imbalance disclosure is sign-based (positive/negative/zero), not a general N-class breakdown | The real, current label shape in this repository is a continuous forward-return float, not a discrete class label; sign is the natural, non-invented binary partition. A general N-class mechanism would be speculative machinery for a label shape that does not exist yet | Whichever future milestone first registers a genuinely categorical outcome | Low today — no real or planned Specification in this repository declares a categorical outcome; `outcome_field` is already a caller-overridable parameter, so a future categorical need does not require touching this function's own signature |

- [x] **TD3** — No placeholder work; every statistic, the refusal gate, and the set ceiling were exercised against real registered Specifications and, for the read paths, the real local Label Store's actual 37-subject fully-matured population and its known, real coverage gaps.
- [x] **TD4** — Both items above are named, ADR-scoped boundaries or genuinely speculative future work, not urgent or unscoped debt of this milestone's own making.

## 9. Definition of Done (DOD)

- [x] **D1** — Compiles/imports cleanly in both environments
- [x] **D2** — Full suite passes, zero new failures (2033/19 `btc_bot`; 1915/60 base env)
- [x] **D3** — 43 new tests, all passing in both environments
- [x] **D4** — N/A, explicitly stated (§5 — no writes exist to gate)
- [x] **D5** — Zero new imports from `research/` into any operational-plane module
- [x] **D6** — Rollback lever named (§7)
- [x] **D7** — No existing signature, table, or column touched at all
- [x] **D8** — No `.env`, credential, or secret touched, referenced, or required
- [x] **D9** — Independently deployable — merging this alone changes no other module's behaviour (zero files modified besides new ones)
- [x] **D10** — N/A for this milestone specifically (no new mandatory schema field; `PartialMaturityRefusalError` is a refusal on an explicit, named condition, not a Tier-A-style completeness gate)
- [x] **D11** — All sections completed, including explicit N/A statements

## 10. Reviewer Checklist (RC)

*Pending — Rohith.*

- [ ] **RC1** — Milestone corresponds to WBS 3.9 exactly, no scope creep into Phase 4
- [ ] **RC2** — No file under `docs/architecture/` touched
- [ ] **RC3** — "Files Changed" list matches `git status` exactly
- [ ] **RC4** — At least two tests read in full, confirmed to exercise real behaviour, including `TestEndToEndLabelStoreIntegration::test_full_pipeline`
- [ ] **RC5** — Full-suite counts reproduced independently in both environments
- [ ] **RC6** — Rollback lever confirmed
- [ ] **RC7** — Full diff read — confirm every WBS 3.1–3.8b file (both stores, plus the governance-receipt substrate) shows no diff
- [ ] **RC8** — Real local `label_store.db` confirmed untouched (no `governance_receipts` table exists on it — correctly so, per §2 TV2)
- [ ] **RC9** — Sign-off

  **Reviewer:** _______________  **Date:** _______________

---

## Files Changed

| File | Type |
|---|---|
| `research/label_store/set_hygiene.py` | New — all four §12 Tier D statistics, §16.3 partial-maturity refusal, set-level admissibility ceiling |
| `tests/test_label_store_set_hygiene.py` | New — 43 tests, including the end-to-end integration exercise |

## Summary

Shipped WBS 3.9, Phase 3's completion milestone: all four ADR-I011 §12 Tier D statistics (maturity fraction, horizon coverage, outcome-distribution disclosure including class imbalance, standing composition) and §16.3's partial-maturity refusal, computed on demand with zero schema change to either store — mirroring WBS 3.7's own "computed and recorded, not persisted" precedent exactly. `set_admissibility_ceiling()` activates `lattice.py`'s `Maturity.PARTIAL` rung, defined at Phase 0 and never produced by any prior milestone, resolving the one Tier D statistic the ADR itself diagrams as directly capping admissibility; the other three statistics are disclosures with no lattice dimension of their own, correctly left as reports for a human or Phase 4's Dataset Builder to consult. No `label_policy.yaml` was introduced — verified that no prior milestone (3.5, 3.6, 3.7) built one either, so this milestone follows the same real, established, function-parameter-based non-blocking discipline rather than inventing new machinery. A full, end-to-end integration test composes every completed Label Store milestone together in one realistic scenario — registration-with-receipt, multi-subject declaration with standing, maturation, flagging, adjudication-with-receipt, conditioning and per-value admissibility, all four Tier D statistics, the set-level ceiling, partial-maturity refusal-then-admission, and governance-receipt chain/replay integrity — directly satisfying this milestone's own "validate end-to-end dataset integration across the completed Label Store" instruction. A second real-data test runs the same composition's read side against the real local `label_store.db`, correcting one finding along the way (that file has never had `governance_receipts` created on it, unlike `feature_store.db`; the governance-receipt half of the real-data test correctly runs against a disposable copy instead, exactly mirroring WBS 3.8a's and 3.8b's own established discipline). TV3's sabotage/revert cycle confirmed the PARTIAL-rung claim is genuinely tested at both the unit and the full-pipeline level. No architectural inconsistency with ADR-I011 was found. Zero changes to any WBS 3.1–3.8b file in either store. **Phase 3 (WBS 3.1–3.9, plus 3.10) is now functionally complete**; the separate, not-yet-requested §13.3 closeout deliverables (Label Store Technical Debt Register, Phase 3 Closure Review, Phase 3 Closeout document) remain open, un-begun items distinct from WBS 3.9 itself. Next: Phase 4 — **not begun**, per instruction.
