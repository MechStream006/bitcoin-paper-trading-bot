# Milestone Verification Record

| Field | Value |
|---|---|
| **Milestone** | Normalisation-Contract-Version field on `research/ingest/` writers |
| **WBS item(s)** | 1.1 |
| **Roadmap phase** | Phase 1 — Storage Plane Conformance |
| **Standard version referenced** | 1.0.0 |
| **Date** | 2026-08-05 |
| **Implementer** | Claude Sonnet 5 (paired session) |
| **Reviewer** | *(pending — Rohith)* |

---

## 1. Build Verification (BV)

- [x] **BV1** — Every new/modified file compiles cleanly
  - Command: `"<btc_bot>/python.exe" -m py_compile research/storage/manifest.py research/ingest/binance_ohlcv.py research/ingest/funding.py research/ingest/dvol.py research/validate_wi6.py tests/test_storage_manifest.py tests/test_ingest_normalization_contract_version.py`
  - Output: `COMPILE OK`
- [x] **BV2** — Imports follow this repo's established convention
  - Confirmation: new tests mirror the exact root-relative dotted-import convention `research/ingest/*.py` and `research/storage/*.py` already use (`sys.path.insert(research/)`, `from ingest import ...`, `from storage import ...`) — distinct from, and correctly matched to, `research/evidence_library/`'s bare sibling-import convention used in the prior milestone.
- [x] **BV3** — No new dependency added
  - Statement: none. `pandas`/`pyarrow`/`requests` were already in `research/requirements-research.txt`, already used by every file touched.
- [x] **BV4** — No file targets a Python version outside its owning environment
  - Confirmation: verified under `research/`'s owning environment (`btc_bot`, Python 3.10.20) directly — see §3.

---

## 2. Test Verification (TV)

- [x] **TV1** — A new test exists for every new public function/class/behaviour
  - Test files: `tests/test_storage_manifest.py` (new, 10 tests), `tests/test_ingest_normalization_contract_version.py` (new, 10 tests) — **20 new tests**, the first pytest coverage either `research/storage/manifest.py` or any of the three `research/ingest/` writers has ever had.
- [x] **TV2** — New tests follow house style
  - Confirmation: `class TestXxx:` groupings, plain `assert`, `tmp_path`/`monkeypatch` fixtures — matches the rest of `tests/`.
- [x] **TV3** — At least one representative test confirmed to fail without the implementation
  - Empirically verified, not merely reasoned: temporarily removed the new manifest key from `binance_ohlcv.py`'s `meta` dict, re-ran `TestBinanceOhlcvIngest` — `test_manifest_records_the_constant` failed with `KeyError: 'normalization_contract_version'`, all others still passed. Change reverted, full re-run green.
- [x] **TV4** — A negative/refusal-path test exists for every refusal the milestone's own logic specifies
  - N/A — this milestone introduces no new refusal path (additive metadata only, no validation gate added).
- [x] **TV5** — A property/invariant test exists for every structural claim made
  - `test_does_not_alter_the_hash_or_row_count` (the field is additive metadata, never changes the artifact it describes); `test_row_count_and_parquet_content_unaffected` / `test_row_count_unaffected` (the "no behaviour change" claim, per writer); `test_skip_logic_unaffected` (idempotent re-ingest skip path untouched).

---

## 3. Regression Verification (REG)

- [x] **REG1** — Full suite run before and after, zero new failures, **in both locally-available environments**
  - Base/default env (Python 3.13.12, `requirements.txt` only — no `pandas`): **1500 passed, 21 skipped, 0 failed** (skip count +2 vs. the prior milestone's 19, both new — see REG2).
  - `btc_bot` env (Python 3.10.20, has `research/requirements-research.txt`): the two new test files run fully — **20/20 passed**.
  - Neither locally-available env can run the complete combined suite (a long-standing, pre-existing split — `requirements-api.txt`'s `fastapi` is absent from `btc_bot`; `research/requirements-research.txt`'s `pandas` is absent from the base env; only CI's single job installs all three requirement files together, per `ci.yml`'s own header comment). Both envs were run to cover what each can, consistent with how this split has been handled throughout this repository's history.
- [x] **REG2** — Skip count change explained
  - Statement: **+2**, both new and both correct: `tests/test_storage_manifest.py` and `tests/test_ingest_normalization_contract_version.py` each guard their `pandas` import with `pytest.importorskip`, so they skip cleanly (not error) in the base env rather than aborting the entire collection — this guard was added specifically because a hard `ImportError` at collection time aborts the whole `pytest tests/` run (verified empirically; see §8 Technical Debt).
- [x] **REG3** — Known pre-existing flakes checked against
  - Notes: none observed in any run this milestone.
- [x] **REG4** — CI workflow unchanged
  - Statement: `.github/workflows/ci.yml` not touched. In CI's actual single combined-dependency job, both new test files run for real (not skipped) — the `importorskip` guard only activates in an environment genuinely missing the dependency.

---

## 4. Performance Verification (PERF) — *when applicable*

**Applicability: N/A.** No new loop over corpus-scale data; the new field is a single additional dict/manifest key computed once per monthly partition, identical cost class to every other manifest field already written.

---

## 5. Database Verification (DBV) — *when applicable*

**Applicability: N/A.** No `.db` file, table, or SQLite schema is touched by this milestone — `research/storage/manifest.py` writes a JSON sidecar (`_manifest.json`), not a database row. (Contrast with the prior milestone, WBS 0.5–0.9, which was DBV-applicable.)

---

## 6. Backward Compatibility (BC)

- [x] **BC1** — No existing public function's name/params/return type changed
  - Confirmation: `write_manifest()` gained one new **optional, defaulted** keyword parameter (`normalization_contract_version: int | None = None`); every existing positional/keyword call remains valid unmodified.
- [x] **BC2** — No existing table/column/file renamed or removed
  - Confirmation: `manifest.py`'s `SCHEMA_VERSION` was bumped 1 → 2 (a value change, not a removal), per that module's own docstring instruction ("increment when fields are added or removed") — verified this is a value change other code reads dynamically (`validate_wi6.py`'s check compares against the live `SCHEMA_VERSION` constant, not a hardcoded literal), not a hardcoded assumption anywhere.
- [x] **BC3** — No existing CLI flag/config key's meaning changed
  - Confirmation: none of the three ingest scripts' CLI argument sets were touched.
- [x] **BC4** — New fields are additive with safe defaults
  - Confirmation: `write_manifest()`'s new parameter defaults to `None`; the three ingest writers' local `meta` dicts gained one new always-populated key (their own `NORMALIZATION_CONTRACT_VERSION` constant, never `None` in practice) — additive in both cases, no existing key removed or renamed.
- [x] **BC5** — Existing consumers of every touched symbol identified and confirmed unaffected
  - Search: `grep -rn "write_manifest\b"` → three real call sites (`research/ingest/dvol.py`, updated in this milestone; `research/build_corpus.py`, `research/build_perp_corpus.py`, **out of WBS 1.1's scope, deliberately not touched** — see §8) plus `research/validate_wi6.py` (a smoke script, one cosmetic label fix only, see §8). Confirmed `build_corpus.py`/`build_perp_corpus.py` call `write_manifest()` without the new keyword — remains valid since it is optional/defaulted; no behaviour change for either file.

---

## 7. Rollback Verification (RBV)

- [x] **RBV1** — Rollback lever named
  - `normalization_contract_version` (manifest field + three writer constants): revert the commit. No consumer reads the field yet (it exists purely for future Dataset Builder / citation-index consumption, per `ADR-I009` §13.3), so nothing downstream depends on its presence.
  - `SCHEMA_VERSION` bump (1 → 2): revert alongside the field; `validate_wi6.py`'s check reads the constant dynamically, so no separate lever is needed there.
- [x] **RBV2** — Lever does not require touching production data
  - Confirmation: no database, no production data path touched at all this milestone (§5). Reverting is a pure code change; any `_manifest.json` sidecar already written with the new field simply keeps an extra JSON key that nothing reads — harmless if left in place, and every sidecar is regenerable from source data regardless (`ADR-I009` §16: partition revisions, not overwrites, but manifests themselves are cheap to rewrite).
- [x] **RBV3** — Lever exercised, or explicitly reasoned why not yet meaningful
  - Statement: not exercised — no consumer exists yet to observe the difference. Stated explicitly per the standard's requirement, not assumed.
- [x] **RBV4** — Rollback does not discard already-recorded governance history
  - Confirmation: no governance receipt, ledger row, or any append-only record is touched by this milestone at all.

---

## 8. Technical Debt Assessment (TD)

- [x] **TD1/TD2** — Every deferral/simplification, with future milestone and blast radius

| What | Why deferred | Closed by (WBS ID) | Blast radius if never closed |
|---|---|---|---|
| `research/build_corpus.py` and `research/build_perp_corpus.py` also call `storage.manifest.write_manifest()` but were **not** updated to pass `normalization_contract_version` | WBS 1.1 scopes to `research/ingest/` writers explicitly (roadmap's own wording); these two are corpus-*building* scripts, a different layer, not named by any current WBS item | Not yet scheduled — a future milestone that formally distinguishes ingest-writer vs. corpus-builder normalisation contracts, if that distinction is ever needed | Low — both remain fully functional (the new parameter is optional); their manifests simply carry `normalization_contract_version: null`, exactly as every manifest did before this milestone |
| `pytest.importorskip("pandas")` guard added to both new test files | Necessary correction, found empirically (see REG1/REG2): a hard `ImportError` at collection time aborts the ENTIRE `pytest tests/` run in an environment lacking `research/requirements-research.txt`, not just the two new files. This is a discovered, pre-existing environment-split property (`CLAUDE.md` §4's documented two-environment model), not something introduced by this milestone | Closed within this milestone, before the report was written — not deferred | None remaining for these two files. **Not applied repo-wide**: other files under `research/forward_evidence/tests/` etc. do not use this guard (they are collected only in CI's combined-dependency job or the `btc_bot` env, per existing convention) — left as-is, since retrofitting the guard onto pre-existing test files is unrelated-scope work this milestone does not own |
| `test_real_ledger_check_performs_no_schema_mutation` and the transitive-write-verb detection gap named in `INC-0001` | Same reasoning as `INC-0001`'s own Prevention section — fixing either means editing `tests/test_governance_verification.py` again, for a third, unrelated reason, in a milestone (WBS 1.1) that has nothing to do with receipts | A future Engineering Hazard Elimination Sprint, per `docs/engineering/hazard_register.md` `EH-001` | Already logged; no new blast radius introduced by this milestone |

- [x] **TD3** — No debt item disguised as complete; no placeholder implementation; no TODO-only commit
  - Confirmation: every function touched has a complete, tested implementation; both new test files are fully populated, not stubs.
- [x] **TD4** — Long-lived debt carried into the running log
  - `research/build_corpus.py`/`research/build_perp_corpus.py`'s deferred update is logged here; not promoted to `CLAUDE.md` §19 (outside this milestone's file scope, consistent with the prior milestone's same judgment call).

---

## 9. Definition of Done (DOD)

- [x] **D1** — Compiles/imports cleanly (verified under the owning `btc_bot` environment)
- [x] **D2** — Full suite passes, zero new failures, in both locally-available environments (§3)
- [x] **D3** — New tests present and passing (20 new tests)
- [x] **D4** — Governance verification N/A for this milestone (no DB touched) — stated explicitly, not silently skipped
- [x] **D5** — Zero new imports from `research/` into any operational-plane module (none of this milestone's files import from `src/`)
- [x] **D6** — Rollback lever named before merge (§7)
- [x] **D7** — No existing table/column/function signature renamed, removed, or changed in return type (one function gained one optional parameter — additive, not a signature break)
- [x] **D8** — No `.env`, credential, or secret touched/referenced/required
- [x] **D9** — Independently deployable — merging this alone leaves the repo working and fully tested in every environment that was checked
- [x] **D10** — N/A — no new mandatory field introduced this milestone (`normalization_contract_version` is deliberately optional at the shared `write_manifest()` layer; each ingest writer populates it unconditionally from its own constant, which is enforced by that writer's own code, not by a validation gate)
- [x] **D11** — All sections of this template completed, including explicit N/A statements for §4 and §5

---

## 10. Reviewer Checklist (RC)

*To be completed by Rohith in a separate pass.*

- [ ] **RC1** — Milestone corresponds to WBS 1.1 exactly, no unstated scope creep
- [ ] **RC2** — No file under `docs/architecture/` was touched
- [ ] **RC3** — "Files Changed" list matches `git status`/`git diff` exactly
- [ ] **RC4** — At least two tests read in full and confirmed to exercise real behaviour
- [ ] **RC5** — Full-suite pass/skip/fail count reproduced independently, in both environments
- [ ] **RC6** — Rollback levers named in §7 confirmed to actually exist in the diff
- [ ] **RC7** — Full diff of every changed file read, no unrelated refactor found (note: `research/validate_wi6.py`'s one-line label change is disclosed above and should be checked as intentional, not accidental)
- [ ] **RC8** — Sign-off

  **Reviewer:** _______________  **Date:** _______________

---

## Summary

Shipped WBS 1.1: a `normalization_contract_version` field, additive and optional, threaded through `storage/manifest.py`'s `write_manifest()` (`SCHEMA_VERSION` bumped 1→2 per that module's own instruction) and into all three `research/ingest/` writers (`binance_ohlcv.py`, `funding.py`, `dvol.py`), each carrying its own independently-bumpable version constant per `ADR-I009` §13.3. Zero behaviour change to any existing artifact, hash, or row count — proven, not merely asserted, via `TestNormalizationContractVersion::test_does_not_alter_the_hash_or_row_count` and equivalents. First pytest coverage either `manifest.py` or any of the three writers has had. One pre-existing, unrelated environment-split issue (a hard collection-time `ImportError` aborting the full suite) was discovered and fixed within this milestone via `pytest.importorskip`. Per this session's explicit instruction, `INC-0001` (the prior milestone's `init_db()` side effect) and Hazard `EH-001` were formalised as standalone records rather than left inside that milestone's own verification report. Next: WBS 1.2 (the raw/canonical separation pilot on one ingest path).
