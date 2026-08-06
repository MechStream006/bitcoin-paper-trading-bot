# Engineering Verification Standard

| Field | Value |
|---|---|
| **Title** | Engineering Verification Standard — Research Platform |
| **Status** | **Active** |
| **Version** | 1.0.0 |
| **Document Type** | Engineering Process Standard (not an ADR, not an architecture document) |
| **Applies to** | Every milestone executed under [`docs/engineering/implementation_roadmap_v1.md`](../implementation_roadmap_v1.md), and any successor engineering roadmap in this repository |
| **Companion** | [`templates/milestone_verification_template.md`](templates/milestone_verification_template.md) — the fill-in artifact each milestone produces against this standard |
| **Authors** | Rohith Suresh — Owner / Architect · Drafted with Claude Sonnet 5 |
| **Last Updated** | 2026-08-04 |

---

## 0. Purpose and Boundaries

**What this is.** A single, reusable verification procedure run once per engineering milestone, before that milestone is reported complete. It exists so that "is this milestone actually done" is answered the same way every time, rather than re-derived per phase.

**What this is not.**

- Not an ADR. It creates no IQROS Article, no new object, no new field on any architectural artifact.
- Not an architecture document. It does not redesign, extend, or reinterpret anything in `docs/architecture/`.
- Not a gate on `G-CONST` or `G-STAB` (roadmap §0). Those remain Rohith's calls, unaffected by this standard's existence.

**What it is, precisely.** An operationalisation of work already committed to in the roadmap's own §11 (Testing Strategy), §12 (Verification Gates), §13 (CI/CD Integration), §14 (Rollback Strategy), §16 (Risks), and §17 (Definition of Done) — collapsed into one checklist so those five sections don't need to be re-derived, re-worded, or drift apart from each other on every future milestone.

---

## 1. When This Standard Applies

Every WBS item, or a clearly bounded group of WBS items, from the Implementation Roadmap (or a successor roadmap), before it is reported to Rohith as complete. It applies uniformly regardless of phase — Phase 0's pure-computation lattice module and a future Phase 4 milestone touching a SQLite registry are verified against the **same** ten sections; sections that don't apply are marked not applicable **explicitly** (§2), never omitted.

It also applies **retroactively as a self-check**. §15 below re-runs this standard against the already-shipped Phase 0 Admissibility Lattice milestone as a worked example, confirming the standard describes practice already in use rather than inventing new obligations.

---

## 2. How to Use This Standard

1. Copy [`templates/milestone_verification_template.md`](templates/milestone_verification_template.md) to a new file per milestone (suggested path: `docs/engineering/verification/records/<wbs-id>-<slug>.md`; creating that `records/` directory is itself the first act of using the template, not part of this standard's own scope).
2. Fill every section. A section marked "when applicable" (Performance, Database) must still be addressed — state **N/A with a one-line reason**, never leave it blank.

   > This mirrors a pattern already adopted elsewhere in this repository: the Evidence Matrix reporting layer (`CLAUDE.md` §17 PR-5) distinguishes *no evidence* / *insufficient* / *not computed* / *n/a* and renders none of them as a bare `-`. The same discipline applies here — an absent verification result is a different fact from an inapplicable one, and both are different from a check that was simply skipped.
3. Attach the completed record to the milestone's report or PR description.
4. A reviewer works through §12 (Reviewer Checklist) independently — not by re-reading the implementer's own checklist, but by re-deriving the answer.

---

## 3. Section 1 — Build Verification (BV)

| ID | Check | Evidence to capture |
|---|---|---|
| **BV1** | Every new or modified file compiles cleanly | Exact command (`python -m py_compile <files>`) and output |
| **BV2** | Imports resolve using this repository's established convention — bare sibling-module imports via `sys.path` insertion, matching existing `research/*` packages — not a new import convention introduced for this milestone alone | File(s) and the specific pattern used, e.g. "matches `research/dq/`'s `sys.path.insert` + bare `import` style" |
| **BV3** | No new third-party dependency was added without updating the owning requirements file (`requirements.txt`, `research/requirements-research.txt`, or `requirements-api.txt`) **and** without confirming `.github/workflows/ci.yml`'s install step still covers it | State "no new dependency" or name the file(s) updated |
| **BV4** | No file targets a Python version outside what its own environment supports — 3.10.x for `research/*` (the `btc_bot` conda env, per `CLAUDE.md` §4), the runtime venv for `src/*` | Confirm the interpreter used to compile/test matches the owning environment |

**Pass criterion.** All four rows checked with evidence. A milestone touching zero files in a given row's scope may state "N/A" for that row alone (e.g., a documentation-only milestone has nothing to compile) — this is a row-level exception, distinct from the section-level "when applicable" marking reserved for §6 and §7.

---

## 4. Section 2 — Test Verification (TV)

| ID | Check | Evidence to capture |
|---|---|---|
| **TV1** | A new test exists for every new public function, class, or observable behaviour introduced | Test file path(s) and a mapping from behaviour to test name |
| **TV2** | New tests follow this repository's house style — `sys.path` insertion at the top of the file, `class TestXxx:` groupings, plain `assert` statements, no `unittest.TestCase` inheritance, matching `tests/test_dq_engine.py` and siblings | State conformance or name the deviation and why |
| **TV3** | Tests are not tautological. At least a sample is confirmed to **fail without the implementation** — temporarily revert or stub the code under test, re-run, confirm red, then restore | The specific test(s) exercised this way and the failure observed |
| **TV4** | A negative or refusal-path test exists wherever the governing ADR specifies a refusal, per assert-never-repair (`IQROS-P3`, `Ph5`) | Test name(s) and the refusal condition each proves |
| **TV5** | A property or invariant test exists wherever the milestone's own documentation claims an algebraic or structural property (e.g. "commutative", "idempotent", "monotone", "fill-once") | Test name(s) mapped to the claimed property |

**Pass criterion.** TV1, TV2 always required. TV3 required for at least one representative test per milestone, not necessarily every test. TV4/TV5 required only where the milestone actually makes a refusal or invariant claim — if it makes none, state so explicitly rather than leaving the row blank.

---

## 5. Section 3 — Regression Verification (REG)

| ID | Check | Evidence to capture |
|---|---|---|
| **REG1** | Full suite run before and after the change (`python -m pytest tests/ -q`, matching `.github/workflows/ci.yml`'s exact invocation), zero new failures | Before/after pass, fail, and skip counts, with timestamps |
| **REG2** | Skip count is unchanged, or the change is explained | State the delta and reason, or "unchanged" |
| **REG3** | Any pre-existing, previously-documented flake (tracked historically in `CLAUDE.md`, e.g. the `current_run_per_lane` tiebreak timing flake) is checked against — if one recurs, it is investigated using the established pattern (isolation re-run, diff of files actually touched by this milestone) before being dismissed as unrelated | Investigation notes, or "no known flake observed this run" |
| **REG4** | `.github/workflows/ci.yml` itself is unchanged, **or**, if the milestone explicitly modifies it, the modified workflow was exercised locally with an equivalent command before merge | State "workflow unchanged" or describe the local equivalent run |

**Pass criterion.** REG1 is the load-bearing check and is never skipped or marked N/A — every milestone runs the full suite before declaring completion, regardless of how small the change appears.

---

## 6. Section 4 — Performance Verification (PERF) — *when applicable*

**Applicability trigger.** This section applies when a milestone introduces: a loop or query over corpus-scale data; a new database access pattern; a computation invoked per-tick, per-row, or per-request; or a claim (in the milestone's own documentation) that a property holds "at scale" — e.g. that a lattice operation remains cheap when composing many inputs.

If none of these apply, state **"N/A — <one-line reason>"** and stop; do not fill the rows below.

| ID | Check | Evidence to capture |
|---|---|---|
| **PERF1** | The complexity class (Big-O, or a stated bound) of every new function operating over an unbounded or corpus-scale input is recorded | The complexity statement and the reasoning |
| **PERF2** | Where compute-on-read is claimed (`Ph2`), there is no accidental repeated recomputation — verified by a trace, a microbenchmark, or a direct read of the call path if the function is expected to run per-request | Benchmark numbers, trace output, or the call-path reasoning |
| **PERF3** | No unbounded in-memory accumulation exists without a stated retention or eviction policy | State the policy, or confirm accumulation is bounded by construction |

**Pass criterion.** Either the explicit N/A statement, or all three rows addressed with evidence.

---

## 7. Section 5 — Database Verification (DBV) — *when applicable*

**Applicability trigger.** This section applies when a milestone touches a `.db` file's schema, adds or modifies a table, writes a migration script, or touches `ledger.db` (or any future registry database) in any way.

If none of these apply, state **"N/A — <one-line reason>"** and stop; do not fill the rows below.

| ID | Check | Evidence to capture |
|---|---|---|
| **DBV1** | The schema change is additive only. Deployment or migration never mutates existing data in place outside an explicit, separately-invoked, human-authorised migration script (`IQROS CI3`/`D4`, `ADR-P1-1` assert-never-repair) | State the additive nature of the change, or name and justify any non-additive step |
| **DBV2** | Any new migration script is idempotent and gated behind a dry-run / `--commit` split, matching the `migrate_run_kind.py` precedent | Script path and confirmation of the dry-run/--commit pattern |
| **DBV3** | The migration, run twice in sequence, produces zero new writes on the second run | Command and output of both runs |
| **DBV4** | Governance verification (`report.py --check` or an equivalent) is run against a throwaway copy of the real database before and after, and stays clean | Before/after output |
| **DBV5** | A backup of the real database is taken before any migration is run against it for real, and its integrity is verified (`PRAGMA integrity_check` or equivalent) before the migration proceeds | Backup path and integrity-check output |
| **DBV6** | The schema-fingerprint manifest (`governance_verification.py`'s `SCHEMA_FINGERPRINT`, or the milestone's equivalent) is updated for any new table or column, at **warning** severity until the migration has run on the real database, promotable to **error** only in a dedicated follow-up change | Manifest entry added, and its current severity |

**Pass criterion.** Either the explicit N/A statement, or all six rows addressed with evidence. **DBV5 is never skipped when a real database is touched** — this is the one row in this entire standard with no N/A path once the section itself applies.

---

## 8. Section 6 — Backward Compatibility (BC)

| ID | Check | Evidence to capture |
|---|---|---|
| **BC1** | No existing public function's name, parameter list, or return type changed | Confirm, or name and justify any change |
| **BC2** | No existing table, column, or file renamed or removed | Confirm, or name and justify |
| **BC3** | No existing CLI flag or configuration key's meaning changed | Confirm, or name and justify |
| **BC4** | Any new field is additive with a safe default; nothing is retroactively required on existing records | Confirm the default, or state "no new field on an existing record" |
| **BC5** | Existing consumers of every touched symbol are identified (repo-wide search) and confirmed unaffected | Search command and result — "0 existing consumers" is a valid and common answer for a wholly new module |

**Pass criterion.** All five rows checked. BC5's search must actually be run and its command shown, not asserted from memory.

---

## 9. Section 7 — Rollback Verification (RBV)

| ID | Check | Evidence to capture |
|---|---|---|
| **RBV1** | A rollback lever is named — a configuration key, a feature flag, or, for a change with no runtime consumer yet, "revert the commit" — per `IQROS D5`/`G7` | The lever, stated plainly |
| **RBV2** | The lever does not require touching production data (`ADR-P1-1` assert-never-repair) | Confirm, or justify the one case where data must be touched (e.g. a migration's own documented rollback via a pre-migration backup copy) |
| **RBV3** | The lever has been exercised at least once — toggled off or reverted, behaviour confirmed to revert — **or** it is explicitly reasoned why exercising it now is not yet meaningful (e.g. the mechanism has no consumers yet, so "rollback" reduces to "do not adopt it," which is trivially true but must still be **stated**, not silently assumed) | The exercise performed, or the explicit reasoning |
| **RBV4** | Rollback does not discard already-recorded governance history — append-only tables and negative records persist (`IQROS-P15`) | Confirm, or state "no governance record exists yet for this mechanism" |

**Pass criterion.** All four rows addressed. RBV3's "not yet meaningful" path is legitimate but must be written out, not left as an implicit assumption.

---

## 10. Section 8 — Technical Debt Assessment (TD)

| ID | Check | Evidence to capture |
|---|---|---|
| **TD1** | Everything deferred, simplified, or explicitly left for a later milestone is listed — never silently absorbed into a "done" report | The list, or "none identified" |
| **TD2** | Each debt item states: what it is, why it was deferred, which future milestone (by WBS ID, if known) is expected to close it, and the blast radius if it is never closed | One row per debt item |
| **TD3** | No debt item is disguised as complete. No placeholder implementation, no TODO-only commit, no stub masquerading as a finished function | Confirm by direct statement |
| **TD4** | Any debt item expected to persist across more than one milestone is carried into a running log, mirroring the existing `CLAUDE.md` §19 Post-P1 Maintenance Backlog pattern | Log entry reference, or "no long-lived debt from this milestone" |

**Pass criterion.** TD1–TD3 always required. TD4 required only for debt expected to outlive the current milestone.

---

## 11. Section 9 — Definition of Done (DOD)

This section **restates, generalises, and becomes the single canonical source for** the per-milestone Definition of Done first stated in `docs/engineering/implementation_roadmap_v1.md` §17 (`D1`–`D10`). The roadmap document itself is not edited by this standard; the criteria below are unchanged in substance from the roadmap's own wording, collected here so future milestones cite one place rather than two.

| ID | Criterion |
|---|---|
| **D1** | Compiles / imports cleanly under the file's owning environment (§3 BV4) |
| **D2** | Full existing test suite passes with zero new failures (§5 REG1) |
| **D3** | New tests for the milestone's own behaviour are present and pass (§4 TV1) |
| **D4** | Governance verification (where applicable) remains clean, or a named, expected deviation is documented (§7 DBV4) |
| **D5** | Zero new imports from any `research/` module into any operational-plane module, verified by the AST boundary guard or an equivalent manual check (roadmap §13 CI/CD Integration Plan) |
| **D6** | A rollback lever is named before merge, not added retroactively (§9 RBV1) |
| **D7** | No existing table, column, or public function signature is renamed, removed, or has its return type changed (§8 BC1–BC2) |
| **D8** | No `.env`, credential, or secret is touched, referenced, or required by the new code |
| **D9** | The milestone is independently deployable — merging it alone, with no later milestone, leaves the repository in a working, fully-tested state |
| **D10** | Any new mandatory field follows `Ph6` — a missing declaration is a **rejected** registration, verified by a dedicated negative test, not merely asserted in a docstring (§4 TV4) |
| **D11** | *(new in this standard, not in the roadmap's original list)* All ten sections of this standard (§3–§12) have been completed using the milestone template, including explicit N/A statements where a section does not apply |

**Pass criterion.** All eleven rows true. A milestone that cannot satisfy `D9` (independent deployability) is not a milestone — it is a fragment of one and should be re-scoped, not shipped with an exception noted.

---

## 12. Section 10 — Reviewer Checklist (RC)

A **separate** pass, performed by whoever reviews the milestone (Rohith, or a second Claude session with no memory of the implementation), re-deriving the answer rather than re-reading the implementer's own claims.

| ID | Check |
|---|---|
| **RC1** | Does the milestone correspond to exactly one WBS item, or a clearly bounded, explicitly-listed subset — no unstated scope creep? |
| **RC2** | Were any files under `docs/architecture/` touched? For an engineering milestone, the answer must be **no**. |
| **RC3** | Does the "Files Changed" list in the milestone report match `git status`/`git diff` exactly — no undeclared files, no declared files that don't exist? |
| **RC4** | Pick at least two tests at random, read them fully, and confirm they exercise real behaviour rather than restating the implementation (a tautology check independent of the implementer's own TV3 claim) |
| **RC5** | Does the reported full-suite pass/skip/fail count match a fresh local run, not merely the implementer's report? |
| **RC6** | Does the rollback lever named in §9 actually exist in the diff — is it real, or aspirational? |
| **RC7** | Read the full diff of every changed file, not only the new ones — confirm no unrelated refactor, rename, or formatting sweep slipped in |
| **RC8** | Reviewer name and date recorded against the milestone record |

**Pass criterion.** All eight rows completed by someone other than — or, at minimum, in a separate pass from — the implementer.

---

## 13. Governance of This Standard Itself

This is an engineering process document, not part of the IQROS constitutional series. It requires no constitutional session to amend (`E1` does not apply — this document creates no Article, ADR, or governance rule under IQROS). It operationalises conventions that **already exist and predate the Draft IQROS documents** — `pytest`, `py_compile`, the dry-run/`--commit` migration pattern, staged `GV-SCHEMA` severity, and named rollback levers are all established practice in this repository independent of whether `ADR-I001` is ever adopted (roadmap §0 `G-CONST`). This standard is therefore **usable immediately**, unlike the eight source ADRs it indirectly serves.

| Rule | Statement |
|---|---|
| **Versioning** | Semantic versioning. A change adding a check is `MINOR`; a change removing or weakening a check is `MAJOR` and requires Rohith's explicit sign-off, mirroring the seriousness (without the constitutional machinery) of `IQROS AV3`'s major/minor distinction for the admissibility lattice. |
| **Amendment authority** | Rohith, or an explicit instruction in a future session. No milestone may unilaterally weaken this standard to make its own verification easier — that is the exact failure mode assert-never-repair guards against elsewhere in this repository. |
| **Retroactivity** | Milestones completed **before** this standard existed (Phase 0, §15 below) are not required to be redone. They are checked against it once, retroactively, as evidence the standard describes real practice — not as a compliance obligation applied after the fact. |

---

## 14. Relationship to the Implementation Roadmap and to IQROS

| This standard's section | Roadmap source | IQROS principle operationalised |
|---|---|---|
| §3 Build Verification | §13 CI/CD Integration Plan | — |
| §4 Test Verification | §11 Testing Strategy | `Ph5` assert, never repair (refusal-path tests) |
| §5 Regression Verification | §13 CI/CD Integration Plan | — |
| §6 Performance Verification | §11 Testing Strategy (implicit) | `Ph2` reproducibility by refusal to persist (compute-on-read cost claims) |
| §7 Database Verification | §12 Verification Gates | `IQROS-P3` assert, never repair; `CI3`/`D4` deployment never mutates schema |
| §8 Backward Compatibility | §1.3, §17 D7 | `IQROS-P2` single writer; `IQROS-P5` content addressing (identity never silently changes meaning) |
| §9 Rollback Verification | §14 Rollback Strategy | `D5`/`G7` kill switches are configuration, not redeploys |
| §10 Technical Debt Assessment | §16 Risks | `IQROS-P15` negative records persist (debt is logged, not hidden) |
| §11 Definition of Done | §17 Definition of Done | — |
| §12 Reviewer Checklist | *(new — not previously specified in the roadmap)* | Independent verification, generalising `IQROS` Article 11's separated-authorities principle (the entity that built a thing does not solely certify it) |

---

## 15. Worked Precedent — Retroactive Check Against Phase 0

The Admissibility Lattice milestone (`research/admissibility/`, WBS 0.1–0.4) shipped before this standard existed. Checking it retroactively confirms the standard describes practice already in use, per §13's retroactivity rule.

| Section | Result |
|---|---|
| **BV** | `python -m py_compile` run against all five new files, clean. Bare-sibling-import convention matched exactly (`from lattice import ...`, `sys.path.insert` in the test file). Zero new dependencies. |
| **TV** | 41 new tests across 10 `TestXxx` classes, covering construction, ordering, the two named ADR choices, `meet`/`join` algebraic laws, the not-applicable propagation rule, admission refusal naming every unmet dimension, and lattice-version-mismatch refusal on all four multi-value operations. |
| **REG** | Full suite: 1468 passed, 19 skipped, 0 failed, both before and after — the skip count was unchanged. |
| **PERF** | **N/A — pure in-memory tuple comparison over five fixed dimensions, O(1) per operation, no corpus-scale loop, no database access.** |
| **DBV** | **N/A — no schema, table, or database file touched by this milestone.** |
| **BC** | Wholly new module; grep confirmed 0 existing consumers of any new symbol. |
| **RBV** | Lever: "revert the commit" — the module has no consumer yet, so no runtime behaviour depends on it; this was stated explicitly rather than left implicit. |
| **TD** | One item logged: the not-applicable-propagation rule for `join()` (as opposed to `meet()`) is implemented symmetrically but is not exercised by a dedicated test, because `ADR-I014` specifies the not-applicable concept only for ceilings (artifact composition, §5.1), not floors — noted as an open question for whichever future milestone first constructs a floor carrying a not-applicable dimension, rather than silently assumed correct. |
| **DOD** | All eleven criteria satisfied, evidenced above. |

This confirms the standard is calibrated correctly: it did not require inventing new verification steps to pass Phase 0 — it required writing down steps that were already performed.

---

## 16. Self-Review

| Check | Result |
|---|---|
| Any architecture redesigned, amended, or newly decided? | **No.** This document creates no IQROS Article, ADR, or object. Every reference to `IQROS-P*`, `Ph*`, `ADR-I014`, or the roadmap's own sections is a citation, not a redefinition. |
| Any implementation modified? | **No.** No file under `research/`, `src/`, or `tests/` was touched in producing this document. |
| Does the standard cover all ten requested items? | **Yes** — §3 Build, §4 Test, §5 Regression, §6 Performance (when applicable), §7 Database (when applicable), §8 Backward Compatibility, §9 Rollback, §10 Technical Debt, §11 Definition of Done, §12 Reviewer Checklist. |
| Is the standard reusable rather than milestone-specific? | **Yes** — every check is phrased generically and paired with a fill-in template (§2, companion file) rather than being written against Phase 0's specifics; §15's Phase 0 walkthrough is presented as a worked example, not as the standard's subject. |
| Does "when applicable" ever become a silent skip? | **No** — §6 and §7 both require an explicit N/A statement with a one-line reason; §2 states this rule once, generally, rather than repeating it as a caveat on every row. |

No code was written or modified in producing this document.
