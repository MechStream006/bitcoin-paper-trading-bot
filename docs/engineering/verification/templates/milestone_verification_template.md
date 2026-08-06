# Milestone Verification Record

> Copy this file per milestone. Fill every section. Sections 4 and 5 are
> "when applicable" — if a section does not apply, write **`N/A — <one-line reason>`**
> directly under its heading and leave the checklist rows unfilled. Never
> leave a section blank with no statement at all.
>
> Full definitions, pass criteria, and rationale for every check below
> live in [`../engineering_verification_standard.md`](../engineering_verification_standard.md).
> This file only reproduces the checklist items so a milestone author can
> fill them in one place.

| Field | Value |
|---|---|
| **Milestone** | *(name, e.g. "Admissibility Lattice")* |
| **WBS item(s)** | *(e.g. "0.1–0.4")* |
| **Roadmap phase** | *(e.g. "Phase 0 — Admissibility + Receipt Substrate")* |
| **Standard version referenced** | *(e.g. "1.0.0")* |
| **Date** | *(ISO date)* |
| **Implementer** | *(name / session)* |
| **Reviewer** | *(name / session — filled at §10, not by the implementer)* |

---

## 1. Build Verification (BV)

- [ ] **BV1** — Every new/modified file compiles cleanly
  - Command:
  - Output:
- [ ] **BV2** — Imports follow this repo's established sibling-import convention (no new convention introduced)
  - Confirmation:
- [ ] **BV3** — No new dependency added without updating the owning requirements file and CI's install step (or: no new dependency)
  - Statement:
- [ ] **BV4** — No file targets a Python version outside its owning environment
  - Confirmation:

---

## 2. Test Verification (TV)

- [ ] **TV1** — A new test exists for every new public function/class/behaviour
  - Test file(s):
  - Behaviour → test mapping:
- [ ] **TV2** — New tests follow house style (`sys.path` insertion, `class TestXxx:`, plain `assert`)
  - Confirmation:
- [ ] **TV3** — At least one representative test confirmed to fail without the implementation (red → green)
  - Test(s) exercised this way, and what was observed:
- [ ] **TV4** — A negative/refusal-path test exists for every refusal the milestone's governing ADR specifies (or: no refusal specified)
  - Test(s):
- [ ] **TV5** — A property/invariant test exists for every algebraic or structural claim made (or: no such claim made)
  - Test(s):

---

## 3. Regression Verification (REG)

- [ ] **REG1** — Full suite run before and after, zero new failures
  - Before: pass=___ fail=___ skip=___ (timestamp: ___)
  - After: pass=___ fail=___ skip=___ (timestamp: ___)
  - Command:
- [ ] **REG2** — Skip count unchanged, or explained
  - Statement:
- [ ] **REG3** — Known pre-existing flakes checked against
  - Notes:
- [ ] **REG4** — CI workflow unchanged, or the change was exercised locally with an equivalent command
  - Statement:

---

## 4. Performance Verification (PERF) — *when applicable*

**Applicability:** *(state N/A with reason, or confirm this section applies and why)*

- [ ] **PERF1** — Complexity class recorded for every function over an unbounded/corpus-scale input
  - Statement:
- [ ] **PERF2** — No accidental repeated recomputation where compute-on-read is claimed
  - Evidence:
- [ ] **PERF3** — No unbounded in-memory accumulation without a stated retention/eviction policy
  - Statement:

---

## 5. Database Verification (DBV) — *when applicable*

**Applicability:** *(state N/A with reason, or confirm this section applies and why)*

- [ ] **DBV1** — Schema change is additive only; no deployment/migration mutates in place outside an explicit, human-authorised script
  - Statement:
- [ ] **DBV2** — Migration script is idempotent, dry-run/`--commit`-gated
  - Script path:
- [ ] **DBV3** — Migration re-run twice produces zero new writes on the second run
  - Command and output:
- [ ] **DBV4** — Governance verification run against a throwaway DB copy before/after, clean
  - Output:
- [ ] **DBV5** — Backup taken before touching the real database; integrity verified before migration proceeds *(no N/A path once this section applies)*
  - Backup path and integrity-check output:
- [ ] **DBV6** — Schema-fingerprint manifest updated at the correct staged severity
  - Entry added, current severity:

---

## 6. Backward Compatibility (BC)

- [ ] **BC1** — No existing public function's name/params/return type changed
  - Confirmation:
- [ ] **BC2** — No existing table/column/file renamed or removed
  - Confirmation:
- [ ] **BC3** — No existing CLI flag/config key's meaning changed
  - Confirmation:
- [ ] **BC4** — New fields are additive with safe defaults
  - Confirmation:
- [ ] **BC5** — Existing consumers of every touched symbol identified and confirmed unaffected
  - Search command and result:

---

## 7. Rollback Verification (RBV)

- [ ] **RBV1** — Rollback lever named
  - Lever:
- [ ] **RBV2** — Lever does not require touching production data
  - Confirmation:
- [ ] **RBV3** — Lever exercised, or explicitly reasoned why exercising it now is not yet meaningful
  - Statement:
- [ ] **RBV4** — Rollback does not discard already-recorded governance history
  - Confirmation:

---

## 8. Technical Debt Assessment (TD)

- [ ] **TD1** — Every deferral/simplification listed (or: none identified)
- [ ] **TD2** — Per item: what / why deferred / which future milestone closes it / blast radius if never closed

| What | Why deferred | Closed by (WBS ID) | Blast radius if never closed |
|---|---|---|---|
| | | | |

- [ ] **TD3** — No debt item disguised as complete; no placeholder implementation; no TODO-only commit
  - Confirmation:
- [ ] **TD4** — Long-lived debt carried into the running maintenance log (or: no long-lived debt from this milestone)
  - Log reference:

---

## 9. Definition of Done (DOD)

- [ ] **D1** — Compiles/imports cleanly under the owning environment
- [ ] **D2** — Full suite passes, zero new failures
- [ ] **D3** — New tests present and passing
- [ ] **D4** — Governance verification clean, or a named expected deviation is documented
- [ ] **D5** — Zero new imports from `research/` into any operational-plane module
- [ ] **D6** — Rollback lever named before merge
- [ ] **D7** — No existing table/column/function signature renamed, removed, or changed in return type
- [ ] **D8** — No `.env`, credential, or secret touched/referenced/required
- [ ] **D9** — Independently deployable — merging this alone, with nothing later, leaves the repo working and fully tested
- [ ] **D10** — Every new mandatory field is rejected when absent, verified by a dedicated negative test
- [ ] **D11** — All sections of this template completed, including explicit N/A statements where applicable

---

## 10. Reviewer Checklist (RC)

*Filled by a reviewer, in a separate pass from the implementer — re-deriving the answer, not re-reading the implementer's claim.*

- [ ] **RC1** — Milestone corresponds to exactly one WBS item / an explicitly bounded subset, no unstated scope creep
- [ ] **RC2** — No file under `docs/architecture/` was touched
- [ ] **RC3** — "Files Changed" list matches `git status`/`git diff` exactly
- [ ] **RC4** — At least two tests read in full and confirmed to exercise real behaviour, not tautology
- [ ] **RC5** — Full-suite pass/skip/fail count reproduced independently, matches the report
- [ ] **RC6** — Rollback lever named in §7 confirmed to actually exist in the diff
- [ ] **RC7** — Full diff of every changed file read, no unrelated refactor/rename/formatting sweep found
- [ ] **RC8** — Sign-off

  **Reviewer:** _______________  **Date:** _______________

---

## Summary

*(One paragraph: what shipped, what's next, any deviations from this template and why.)*
