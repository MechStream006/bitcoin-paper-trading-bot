# Phase 2 (Feature Store) — Closure Review

| Field | Value |
|---|---|
| **Review type** | Formal phase closure review — read-only audit |
| **Scope** | Engineering Implementation Roadmap v1.0.1 §6, Phase 2 (WBS 2.1–2.7) |
| **Audited against** | `ADR-I010` Feature Store Architecture v0.1.0 (Draft); `docs/engineering/implementation_roadmap_v1.md` v1.0.1; `docs/engineering/verification/engineering_verification_standard.md` v1.0.0 |
| **Date** | 2026-08-05 |
| **Reviewer** | Claude Opus 5 (audit pass); **Rohith Suresh — sign-off pending, see §10** |
| **Code changed by this review** | **None.** Read-only. No file under `research/`, `src/`, `tests/`, or `docs/architecture/` was modified. |

---

## 1. Executive Summary

Phase 2's Work Breakdown Structure is **complete**. All seven WBS items (2.1–2.7) have shipped with a verification record each, and every headline claim made by those records was independently re-derived during this audit rather than accepted from the implementer's own report.

Independently reproduced this session:

| Check | Reported by milestones | Reproduced now | Match |
|---|---|---|---|
| Full suite, `btc_bot` env, repo-wide (`pytest --ignore=tests/test_api.py`) | 1881 passed / 19 skipped / 0 failed | **1881 passed / 19 skipped / 0 failed** (76.31s) | ✅ |
| Full suite, base env, `tests/` scope | 1525 passed / 37 skipped / 0 failed | **1525 passed / 37 skipped / 0 failed** (53.89s) | ✅ |
| `feature_store.db` | 17 Definitions, 17 Specifications, all Active | **17 / 17 / all `Active`**, `PRAGMA integrity_check` = `ok` | ✅ |
| Conditioning rules registered | all 17 `preserving` | **17 × `preserving`** | ✅ |
| Real `ledger.db` untouched | 70 runs / 26 receipts / 13 transitions / 142 gate_results | **70 / 26 / 13 / 142**, `verify_chain()` `ok=True` | ✅ |
| `report.py --check` | clean, 35 families (9/22/4) | **OK — 35 families, 9 candidate / 22 discovery / 4 disqualified** | ✅ |
| Research/operational boundary | zero coupling | **zero imports either direction** (`research/feature_store/` ↔ `src/`) | ✅ |

**The engineering is sound and the verification discipline is genuine.** Four incidents (`INC-0001`–`INC-0004`) were found and fully closed within the phase, three of them by the implementer's own falsifiability exercises rather than by a later reviewer — including root-causing a flake that Program P1 had carried as "unexplained/intermittent" for weeks.

**However, this review does not recommend unconditional closure.** Three findings separate "Phase 2 WBS-complete" from "Phase 2 closed":

1. **`F-1` (blocking, procedural).** All five verification records have an **unsigned Reviewer Checklist** — RC1–RC8 unchecked, "Reviewer / Date" blank, marked *"Pending — Rohith"*. The Verification Standard's own pass criterion for §12 requires all eight rows completed in a separate pass, and `D11` requires all ten sections complete. **By the phase's own governing standard, no Phase 2 milestone has yet passed its Definition of Done.** This is Rohith's act; no engineering work can close it.
2. **`F-2` (material, carried forward).** Three named `ADR-I010` requirements — §9.2 Admissibility Inheritance, §13 Tier D Output Hygiene, and §8.3's *registration*-precondition framing of binding equivalence — have **no implementation and no owning WBS item** anywhere in the roadmap. Phase 2 did not fail to deliver them; the roadmap never assigned them. **Phase 2 WBS-complete ≠ ADR-I010 realised** — the same distinction the roadmap itself was forced to draw for Phase 1 (§6 reconciliation note).
3. **`F-3` (durability).** The entire Feature Store — code, tests, all eight architecture ADRs, and every engineering document including this one — exists **only as uncommitted working-tree state on one machine**. This is the identical risk Program P1 named as R6 and then explicitly closed by committing before declaring the program closed (`CLAUDE.md` §18).

**Recommendation: close Phase 2 conditional on `F-1` and `F-3`; record `F-2` as a carried-forward architectural gap.** Detail in §10.

---

## 2. WBS Completion Matrix

| WBS | Item (roadmap §6) | Status | Verification record | Real-data demonstration | Notes |
|---|---|---|---|---|---|
| **2.1** | `registry.py` — Definition + Specification tables, SQLite, single-writer, content-addressed | **Complete** | `wbs-2.1-2.3-feature-store-registry.md` | Yes — 17 real Specifications registered | Append-only triggers on `feature_definitions`; phase guard in Python (documented TD) |
| **2.2** | `value_store.py` — cache tier (evictable) + materialisation tier (write-once, obligation-bound) | **Complete (code); never exercised against real data** | `wbs-2.1-2.3-feature-store-registry.md` | **No** — see `F-4` below | `feature_value_cache` / `feature_value_materialized` tables **do not exist** in the real `feature_store.db`; proven by test only |
| **2.3** | Registration path: Tier A (completeness) + Tier B (binding equivalence) per §13 | **Complete** | `wbs-2.1-2.3-feature-store-registry.md` | Yes — Tier B ran for real on all 17 during `--commit` | Tier A refuses on all 8 mandatory fields; Tier B fail-closed |
| **2.4** | Migrate the ~20 shared derived features to Specifications — wrapped, not rewritten | **Complete** | `wbs-2.4-feature-migration.md` | Yes — 17 Active against 1987-row corpus | 17 migrated + 1 (`ret_fwd1`) correctly excluded as a label = 18/18 accounted. `engine.py` never edited. `INC-0002`, `INC-0003` closed |
| **2.5** | Equivalence demonstration for the 5 hash-pinned decision functions (PS1, §8.3) | **Complete as demonstration; not as a registration precondition** | `wbs-2.5-feature-equivalence-demonstration.md` | Yes — all 5 exact-match over real corpus | See `F-2c`: the 5 frozen bindings are **not registered** in `feature_store.db` (0 rows); equivalence is test-enforced, not registry-enforced |
| **2.6** | Warm-up as a contract term (§8.4) — refuse, not silently truncate | **Complete** | `wbs-2.6-warmup-contract.md` | Yes — refuse-mode run against real corpus, 0 refusals | Shipped `mode: warn` per roadmap R5. Live-script TV3 performed against the real DB and reverted hash-identically |
| **2.7** | Conditioning propagation rule (§9.1) declared per migrated Specification | **Complete** | `wbs-2.7-conditioning-propagation.md` | Yes — all 17 sources resolve against real ingest-writer text | `INC-0004` found and fixed pre-ship. `narrowing`/`cross-sectional` implemented, honestly unexercised |

**Phase-level DoD (roadmap §17):** *"All 5 frozen decision functions pass binding-equivalence demonstration, or are explicitly registered as two distinct Specifications with the divergence named (R3)."* — **Satisfied.** All 5 passed; no divergence found, so the second branch never applied. R3 (roadmap §16, "equivalence demonstrations surface real historical drift") **did not materialise** — a genuinely favourable outcome, not a skipped check.

**Scope discipline:** every milestone corresponds to exactly one WBS item (or the explicitly-bounded 2.1–2.3 group). No milestone silently absorbed a later item's scope. `docs/architecture/` was never opened for writing by any Phase 2 milestone (RC2 satisfied by inspection: the directory is wholly untracked, i.e. authored earlier and never modified since).

---

## 3. ADR-I010 Conformance Matrix

Audited against the ADR's own numbered sections. **`Conformant`** = implemented and demonstrated. **`Partial`** = implemented for the realised surface, with a stated boundary. **`Not implemented`** = no code exists. **`Deferred (owned)`** = explicitly assigned to a later phase. **`Gap (unowned)`** = required by the ADR, assigned to no WBS item anywhere.

| ADR-I010 § | Requirement | Status | Evidence / boundary |
|---|---|---|---|
| **§6.1** | Two records: Definition (versioned, immutable per version) + Specification (immutable, content-addressed) | **Conformant** | `feature_definitions` (PK `name,version`) + `feature_specifications` (PK `spec_hash`) |
| **§6.2** | Content addressing; append-only; phase-tracked; storage-level immutability; hash-only resolution; single writer | **Partial** | Content addressing ✅ (SHA-256 over canonical JSON); append-only ✅ (DB triggers on definitions); phase-tracked ✅. **Storage-level immutability is Python-guarded for phase transitions, not trigger-guarded** — a documented WBS 2.1 TD, weaker than `contract_lifecycle.py`'s audited precedent |
| **§6.3** | Registry refuses: missing Definition version; undeclared inputs/warm-up/conditioning; >1 binding; mutation of a sealed record | **Conformant** | `_validate_tier_a()` + FK + `UnknownDefinitionError`; single `(binding_id, engine_version)` pair per Specification by schema |
| **§7.1** | Two tiers distinguished by obligation, not content | **Conformant (code)** | `feature_value_cache` (freely overwritten, `evict_cache()`) vs `feature_value_materialized` (write-once). See `F-4` — never populated |
| **§7.2** | Addressing triple (spec hash · corpus version · `as_of`) | **Partial** | Triple is the PK of both tiers ✅. **No producer of a real `corpus_version` exists** — it is an opaque caller-supplied string; ADR-I009 §16 partition-revision composition is Phase 1/4 work (`F-2d`) |
| **§7.3** | Stored value disagreeing with recomputation is wrong by definition; divergence reported, never reconciled | **Conformant** | `DivergenceEvent` on both re-materialisation-with-different-content and stored-hash-mismatch-on-read. Explicitly contrasted with `raw_lake.py`'s legitimate-revision model in the module docstring |
| **§8.2** | Eight mandatory field classes, none defaultable | **Partial** | All eight present and Tier-A-enforced ✅. **Input requirements are shape-validated (`source`/`fields`) but not deep-validated for the ADR's named "required admissibility floor, required lane"** — documented WBS 2.1 TD, unowned beyond "WBS 2.4+" |
| **§8.3** | **One authoritative binding**; a second binding must demonstrate equivalence *as a condition of registration* | **Partial — `F-2c`** | Equivalence **demonstrated** for all 5 (WBS 2.5, exact match, real corpus) ✅. But the 5 frozen bindings are **not registered** (`feature_store.db` holds 0 `decision_function.*` bindings — verified by query). The ADR's stated intent — *"converts the audited dual-implementation risk from a testing question into a registration precondition"* — is **not yet structurally achieved**: it remains a testing question, enforced by `tests/test_decision_function_research_equivalence.py` |
| **§8.4** | Warm-up declared, checkable before compute; refusal not truncation | **Conformant** | `warmup.check_warmup()`, three governed modes, refuse-mode proven against real corpus. Enforcement point is the one real resolve()-shaped consumer, honestly stated |
| **§9.1** | Conditioning propagation: preserving / narrowing / cross-sectional; never dropped | **Conformant** | `conditioning.py`. Dual-source features carry **both** statements forward (never collapsed). Source text read live from ingest writers, never copied |
| **§9.2** | **Admissibility inheritance — value set ceiling is the meet over inputs** | **Not implemented — `F-2a`** | **Zero references to `research/admissibility/` anywhere in `research/feature_store/`** (grep-confirmed). Phase 0's lattice exists and is unused by Phase 2. No WBS item in Phase 2 claimed it |
| **§10** | Three versioning levels; §10.1 change classification; §10.2 supersession never invalidates | **Partial** | Definition version + Specification hash ✅. Value-set identity exists in the value-store schema but has no real instance. `Superseded`/`Deprecated` arcs implemented and tested; **never exercised** (no reparameterisation has occurred) |
| **§11** | Lifecycle phases and allowed arcs | **Conformant** | `_ALLOWED_TRANSITIONS` transcribes the ADR's state diagram verbatim. `Proposed` deliberately not persisted (documented). `Rejected` is a permanent record ✅ (FG3/IQROS-P15) |
| **§11** | *"Retirement requires zero citations"* | **Deferred (owned)** | `retire_specification()` performs the mechanical transition only; citation index is `ADR-I012` / Phase 4. Correctly deferred and documented |
| **§12.1–12.3** | Compute-on-read default; materialisation only for C1/C2; write-once with obligations | **Conformant (by construction)** | Materialisation exists but is unused — which *is* the ADR's default posture, not a shortfall. `citing_artifact` + `retention_obligation` are `NOT NULL` |
| **§12.4** | `resolve()` flowchart: admissibility floor → warm-up → materialised → cache → compute | **Partial** | Materialised/cache/compute path ✅ in `value_store.resolve()`. Warm-up ✅ but at the caller, per the module's own stated boundary. **Admissibility-floor step absent** (same as §9.2) |
| **§13 Tier A** | Specification completeness | **Conformant** | `_validate_tier_a()`, negative-tested per field |
| **§13 Tier B** | Binding equivalence; failure is Rejected, permanently recorded | **Conformant** | `activate_specification()`, fail-closed on exception, Rejected row persisted before raising |
| **§13 Tier C** | Resolution-time integrity: inputs at declared floor + lane, warm-up satisfied, corpus pinned, `as_of` closure | **Partial** | **Warm-up only.** Floor, lane, corpus pinning, and `as_of` closure are all unimplemented. Honestly stated in `warmup_contract.md` §3 |
| **§13 Tier D** | **Output hygiene: validity fraction, degenerate-output detection, warm-up boundary correctness** | **Not implemented — `F-2b`** | Zero implementation (grep-confirmed: no `validity_fraction`, no degenerate-output check). Notably, *"the first valid index must match the declared warm-up exactly, neither early nor late"* is a check that **could** run today against all 17 features and does not |
| **§14.2 FG1** | Registration is a human act, emits a receipt | **Partial** | Registration is human-invoked (`--commit`-gated CLI) ✅. **No governance receipt is emitted** — `feature_store.db` has no link to `governance_receipts`. Unowned |
| **§14.2 FG3** | Rejections and failed equivalence are permanent records | **Conformant** | Rejected rows written before the exception is raised — verified by test |
| **§14.2 FG4** | A Specification is never edited | **Conformant** | No UPDATE path except the phase transition; content change ⇒ new hash ⇒ new row |
| **§14.2 FG6** | Registry never computes values; Value Store never issues judgments | **Conformant** | Clean separation; `resolve()` takes `compute_fn` from the caller |
| **§14.2 FG7** | Sensitivity variants declared at registration | **Partial** | Declaration is mandatory and non-omittable ✅ (empty list valid, omission rejected). **All 17 declare `[]`** — so §13 Tier B's *"reproduces across the declared sensitivity variants"* is vacuously satisfied for every real Specification |
| **§15** | Provenance closure (8 elements) | **Partial** | Spec hash, Definition version, binding + engine version ✅. Corpus version, `as_of`, admissibility inputs, conditioning statement, computation record — **not persisted on any value set** (none exists) |
| **§17** | Asset is a resolution parameter, never a Specification property | **Conformant** | No asset field on any Specification; all 17 are asset-agnostic computations |
| **§18 R2/R8** | Equivalence may be hard; warm-up refusal will surface silent degradation | **Both retired favourably** | R2: all 5 equivalent. R8: zero features currently warm-up-deficient |
| **§18 R9** | Greeks/IV/OI still have no feature surface | **Unchanged — correctly out of scope** | Confirmed: all 17 migrated features derive from spot/perp/funding/DVOL only |

---

## 4. Engineering Verification Summary

Audited against `engineering_verification_standard.md` v1.0.0. Every record fills all ten sections including explicit N/A statements — the standard's §2 discipline was followed without exception.

| Section | Across all five Phase 2 records | Audit result |
|---|---|---|
| **§3 BV** (Build) | `py_compile` run per milestone; bare-sibling-import convention matched; zero new third-party dependencies except `yaml` (already in `requirements-research.txt`); Python 3.10 target respected | **Pass** |
| **§4 TV** (Test) | 171 new Phase 2 tests total. **TV3 performed on every milestone**, and from WBS 2.6 onward at *two* levels — unit-level sabotage **and** live-script sabotage against the real `feature_store.db`, each reverted with byte-identical / hash-identical confirmation | **Pass — exceeds the standard's own bar** (TV3 requires only one representative test per milestone) |
| **§5 REG** (Regression) | Both environments, both scopes, every milestone | **Pass — independently reproduced this session, exact match (§1)** |
| **§6 PERF** | N/A stated with reason on 2.1–2.3, 2.5, 2.6, 2.7; addressed where the corpus-scale `load_master()` call was introduced | **Pass** |
| **§7 DBV** (Database) | DBV1–DBV6 addressed on 2.4/2.6/2.7. Migration is dry-run/`--commit`-gated and verified idempotent across three consecutive real runs. DBV5 satisfied by the regenerability argument (see `F-4`) | **Pass, with one standing caveat** — `feature_store.db` still has **no backup wiring**; the documented rationale (100% regenerable, unlike `ledger.db`) is sound and the trigger condition for revisiting it is written down (`feature_store_backup_strategy.md`) |
| **§8 BC** (Backward compat) | BC5 repo-wide searches actually run and shown, not asserted. `engine.py`, `signals.py`, and all 5 `decision_function_*.py` confirmed untouched | **Pass** |
| **§9 RBV** (Rollback) | Levers named per milestone. WBS 2.6's lever (`warmup_policy.yaml`'s `mode`) was **actually exercised** (all three modes run against the real script) rather than asserted | **Pass** |
| **§10 TD** (Technical debt) | 13 debt items logged across five records, each with why-deferred, owning WBS, and blast radius. No item disguised as complete | **Pass** — see §5 |
| **§11 DOD** (D1–D11) | D1–D10 satisfied on every milestone | **`D11` NOT satisfied on any milestone** — see `F-1` |
| **§12 RC** (Reviewer) | *"Pending — Rohith"* on all five; RC1–RC8 unchecked; Reviewer/Date blank | **Fail — `F-1`, the one blocking finding** |

**On `F-1`, precisely.** This is not a paperwork quibble. §12's purpose is stated explicitly: *"re-deriving the answer rather than re-reading the implementer's own claims"* — generalising IQROS Article 11's separated-authorities principle, *"the entity that built a thing does not solely certify it."* Every Phase 2 completion claim currently rests on the implementer's own verification. This audit pass discharges RC4 and RC5 independently (two tests read in full; both suite counts reproduced from a fresh run) and RC1/RC2/RC6/RC7 by inspection — but **RC8 sign-off is owner authority and cannot be self-granted.**

---

## 5. Technical Debt Review

All 13 items logged across the five records, consolidated. **None is disguised as complete; none was found undeclared during this audit.**

| # | Debt item | Origin | Owner | Blast radius today | Assessment |
|---|---|---|---|---|---|
| 1 | Phase transitions Python-guarded, not DB-trigger-guarded | 2.1–2.3 | Unscheduled | Low — single writer, no concurrent scenario | **Accept.** Weaker than `contract_lifecycle.py`'s precedent but correctly scoped |
| 2 | Plain `sort_keys` JSON hashing, not `receipts.py` decimal canonicalisation | 2.1–2.3 | Unscheduled | Low — but **now partially realised**: all 17 real Specifications have `parameters` containing ints only, so the float-instability concern flagged at 2.1 has not materialised | **Accept, and downgrade** — the trigger condition (real float parameters) is confirmed absent |
| 3 | `input_requirements` not deep-validated for floor/lane | 2.1–2.3 | "WBS 2.4+" — **never actually closed by 2.4–2.7** | None today | **Re-classify as `F-2a`** — this is the same gap as §9.2 admissibility inheritance, wearing a different name |
| 4 | No citation-index check in `retire_specification()` | 2.1–2.3, 2.4 | Phase 4 | None — nothing retires | **Accept.** Correctly owned |
| 5 | `EH-002` remaining instance (`compute_dsr_for_registration.py`) | 2.4, 2.5 | Hazard Register | None — dormant | **Accept.** See §6 |
| 6 | `feature_store.db` has no backup wiring | 2.4, 2.5 | Trigger condition documented | Low — regenerable in seconds | **Accept.** See `F-4` for the one nuance |
| 7 | 6 `research/forward_evidence/tests/*.py` lack `importorskip("pandas")`, so base env cannot run the true repo-wide suite | 2.5 | Unscheduled | Low — both scopes are valid signals, and the distinction is now documented rather than assumed | **Accept.** Genuinely out of scope; fixing it would be the unrelated refactoring the milestone was told to avoid |
| 8 | Warm-up enforced only at the migration script's resolve()-shaped point, not general `resolve()` with `as_of`-scoped history | 2.6 | Phase 4 | None — it *is* the only real consumer | **Accept.** Correctly owned |
| 9 | `mode: warn` shipped, not `refuse` | 2.6 | Future explicit decision | None — zero features deficient | **Accept.** Deliberate, per roadmap R5. See §9 `RR-1` |
| 10 | No structured capture of warn-mode `WARNING:` lines (stdout only) | 2.6 | Unscheduled | Low — zero warnings produced today | **Accept**, but note this weakens §5's *"review any WARNING lines actually logged"* flip-condition: there is currently no durable place to review them from |
| 11 | No rollout flag for conditioning refusal | 2.7 | Not scheduled — deliberate | None | **Accept.** The asymmetry with warm-up is reasoned, not accidental: an unresolvable source is malformed input, not a data-availability risk |
| 12 | Conditioning propagation not attached to `ResolvedValue` at resolve() time | 2.7 | Phase 4 | None | **Accept.** Deliberately avoided an unauthorised API change |
| 13 | `narrowing`/`cross-sectional` implemented but unexercised by any real Specification | 2.7 | When a real case exists | None | **Accept — and commend the honesty.** Fabricating an example would have been worse |

**Newly identified by this audit, not previously logged:**

| # | Item | Why it matters |
|---|---|---|
| **14** | **All 17 Specifications declare `sensitivity_variants=[]`**, making ADR-I010 §13 Tier B's *"reproduces across the declared sensitivity variants"* and FG7's anti-post-hoc-selection guarantee **vacuous for every real Specification** | The field is correctly mandatory and correctly non-omittable — but an empty list means the robustness surface FG7 exists to protect is currently empty. Not a defect (WBS 2.4 never claimed to author variants); worth naming before Phase 4 relies on it |
| **15** | **No governance receipt is emitted on Specification registration** (FG1) | `feature_store.db` and `ledger.db`'s `governance_receipts` are entirely disconnected. FG1 says registration *"is a human act and emits a receipt (IQROS-P8, P10)."* The receipt infrastructure exists (Phase 0 / PR-1) and is unused here |

---

## 6. Hazard & Incident Review

### Incidents — all four closed within the phase that found them

| ID | Title | Found in | Status | Audit note |
|---|---|---|---|---|
| `INC-0001` | `ALTER` inside `init_db()` reachable from a read-only-documented path | Phase 0 (WBS 0.5–0.9) | **Closed** | Fix verified structurally: `registry.py`/`value_store.py`'s `init_db()` are both CREATE-only and cite this incident by name in their docstrings — the lesson propagated forward into Phase 2's *new* code, which is the strongest form of incident closure |
| `INC-0002` | Migration not genuinely idempotent (re-run doubled the registry) | WBS 2.4 | **Closed** | Verified idempotent across three consecutive real `--commit` runs |
| `INC-0003` | `sys.modules['engine']` collision across subsystems | WBS 2.4 | **Closed** | Fix propagated to 3 of 4 known instances; escalated to `EH-002` |
| `INC-0004` | `sys.path.insert(0, …)` shadowed stdlib `calendar`, breaking pandas | WBS 2.7 | **Closed** | Caught during manual verification **before any test was written or anything shipped**. Regression test added. Correctly judged *not* to warrant a Hazard entry (novel, self-contained, no other instance) |

### Hazards — both open, both dormant, neither owned by Phase 2

| ID | Severity | Status | Audit assessment |
|---|---|---|---|
| `EH-001` | Low today, no severity ceiling on the pattern | **Open — 1 of 3 instances fixed** | Remaining instances (`record.py`, `contract_registry.py`) are dormant because their real migrations already ran. **Phase 2 did not add a new instance** — verified: both new `init_db()` functions are CREATE-only by explicit design. The hazard did not grow |
| `EH-002` | Low today; deterministic once triggered | **Open — 3 of 4 instances fixed** (2 in WBS 2.4, 1 in WBS 2.5) | The one remaining instance (`compute_dsr_for_registration.py`) is a standalone CLI never imported by any test. **Phase 2 net-reduced this hazard** and converted a long-standing "intermittent flake" into a closed root cause |

**Both registers' own governance is being followed.** Neither hazard was silently closed as an incidental side effect of an unrelated milestone (`hazard_register.md` §3's explicit prohibition), and both "Known instances" tables carry an honest statement of how exhaustively they were compiled. No new hazard is recommended for registration by this review.

### Rollout flags

| Flag | Location | Shipped value | Exercised? | Assessment |
|---|---|---|---|---|
| Warm-up mode | `research/feature_store/warmup_policy.yaml` → `mode` | `warn` | **Yes** — all three modes run against the real script and real corpus | The only rollout flag in Phase 2. Correctly staged per roadmap R5. Rollback surface is the policy file alone — no code change needed, verified |
| Conditioning refusal | *(none — deliberate)* | n/a | n/a | Reasoned absence, documented. See TD #11 |

**Roadmap §14's requirement — "every new enforcement mechanism must be disableable by configuration, not by a redeploy or a code revert" — is satisfied for warm-up and knowingly waived for conditioning.** The waiver is defensible (a malformed declaration has no legitimate reason to be admitted) but it is a deviation from a stated blanket rule and should be visible at closure rather than buried in a TD table.

---

## 7. Architectural Readiness Assessment

### What is genuinely load-bearing today

- **The Registry is real and populated.** 17 Active, content-addressed, hash-stable across every milestone from 2.4 through 2.7 (spec hashes unchanged through two live-script sabotage-and-revert cycles — strong evidence of determinism).
- **PS2 and PS3 are resolved.** Feature definitions are governed records, not prose in code objects; every feature has an addressable identity a future dataset can cite.
- **PS4 and PS7 are resolved as mechanisms**, live and empirically falsifiable.
- **PS1 is resolved as a demonstration** — see the caveat below.

### `F-4` — The Value Store is architecture without a tenant

`feature_value_cache` and `feature_value_materialized` **do not exist as tables in the real `feature_store.db`** — `init_db()` for the value store has never been called against it. WBS 2.2 is proven entirely by test.

This is **not a defect**: ADR-I010 §12.1 makes compute-on-read the default requiring no justification, and materialisation exceptional. A Feature Store with an empty materialisation tier is the ADR's *intended* steady state before any Training Dataset exists. But three consequences should be stated plainly at closure:

1. **PS5 (exact-bytes recoverability) is unproven end-to-end.** The mechanism exists; nothing has ever exercised it against real values.
2. **`F-4` interacts with the backup argument.** The current "no backup needed, 100% regenerable" rationale is correct **today** precisely because the DB holds only registry rows. The moment the materialisation tier holds a byte guarantee for a citing artifact, that rationale expires — the tier's whole purpose is to hold something that *cannot* be recreated by re-running a script. `feature_store_backup_strategy.md` names a different trigger (a second non-script writer); **first materialisation is an equally valid, currently unnamed trigger.**
3. **§7.2's "reproducible by construction" claim is untested** because no real `corpus_version` has ever been composed (`F-2d`).

### Partial-implementation summary — direct answer to the review objective

The objective was: *confirm no architectural objectives remain partially implemented.* **That confirmation cannot be given.** Five ADR-I010 objectives remain partial or absent:

| Ref | Objective | State | Owner |
|---|---|---|---|
| `F-2a` | §9.2 Admissibility inheritance (DG6, IQROS-P4) — value sets inherit the meet over inputs | **Not implemented.** Phase 0's `research/admissibility/` is unused by Phase 2 | **Unowned.** Nearest natural home: WBS 4.4 (RF4) |
| `F-2b` | §13 Tier D output hygiene — validity fraction, degenerate detection, warm-up boundary exactness | **Not implemented** | **Unowned.** Could be built today against the 17 real features |
| `F-2c` | §8.3 equivalence as a **registration precondition**, not a testing question | **Partial** — demonstrated, not registered | **Unowned.** Would require registering the 5 frozen bindings as conformant Specifications |
| `F-2d` | §7.2 / §15 real `corpus_version` composition and value-set provenance closure | **Not implemented** | Phase 1 (WBS 1.3) + Phase 4 |
| `F-2e` | §14.2 FG1 — registration emits a governance receipt | **Not implemented** | **Unowned** |

**Framing this correctly.** Phase 2's WBS list (2.1–2.7) never contained any of `F-2a`, `F-2b`, `F-2c`, or `F-2e`. **Phase 2 delivered exactly what the roadmap asked of it.** The gap is between the roadmap's Phase 2 scope and ADR-I010's full requirement set — a *planning* gap, discovered at closure, not an *execution* gap. This is precisely the distinction the roadmap itself was forced to draw at §6 for Phase 1 (*"Phase 1 DoD-satisfied" vs "Phase 1 WBS-complete"*), and the same terminology discipline should be applied here:

> **Phase 2 is WBS-complete. ADR-I010 is not fully realised.** Any future report must name which sense it means.

---

## 8. Phase 3 (Label Store) Entry Checklist

Per roadmap §5: *"Phase 3 — Label Store: depends on Phase 0; reuses Phase 2's registry pattern but does not require Phase 2 complete."* **Phase 3 is not gated on Phase 2 closure.**

| # | Entry criterion | State | Blocking Phase 3? |
|---|---|---|---|
| 1 | Phase 0 complete (`research/admissibility/`, receipt extensions) | ✅ Complete — the hard dependency | No |
| 2 | `research/label_store/` does not yet exist | ✅ Confirmed absent — clean start | No |
| 3 | Registry pattern available to copy (WBS 3.1 reuses 2.1's shape) | ✅ `registry.py` is a proven, real-data-exercised template. ADR-I011 §6 states registry properties *"mirror ADR-I010 §6.2 exactly… No new governance mechanism is introduced"* | No |
| 4 | `ret_fwd1` correctly identified and reserved for the Label Store | ✅ Excluded from Phase 2 with a structural import-time guard, not just a comment | No — **this is a genuine Phase 3 hand-off asset** |
| 5 | H6 forward-label system available to wrap (WBS 3.4) | ✅ Exists, unmodified by Phase 2 | No |
| 6 | Three fill conventions identified for WBS 3.3 | ✅ Named in the roadmap's own gap ledger | No |
| 7 | Phase 3 Phase-level DoD achievable (*"H6 fill-once guard proven reused, not reimplemented, via a shared-code-path test"*) | ⚠️ Not assessed by this review — Phase 3 scoping work | No |
| 8 | **`F-2c` lesson available before Phase 3 repeats it** | ⚠️ ADR-I011 §216 uses the *same* binding-identity mechanism as ADR-I010 §8.3. If Phase 3 copies Phase 2's pattern faithfully, it will inherit `F-2c` — equivalence demonstrated by test rather than enforced by registration | **No, but decide deliberately** |
| 9 | Roadmap R4 (Phase 2 ∥ Phase 3 registry-pattern drift) | ✅ Mitigated by construction — Phase 2 ships first, sequentially, exactly as R4 recommends for solo pacing | No |
| 10 | `F-2a` (admissibility inheritance) will recur in Phase 3 | ⚠️ ADR-I011 §DI5 requires admissibility to flow dataset-ward as a meet across evidence, features, **and labels**. Phase 3 will hit the same unwired-lattice gap | **No, but scope it explicitly** |

**Verdict: Phase 3 is entry-ready and unblocked.** Two advisories (items 8 and 10) are pattern-inheritance risks worth a deliberate decision at Phase 3 scoping rather than a rediscovery at Phase 3 closure.

**Separately, for Phase 4 planning** (not a Phase 3 concern): Phase 4 requires Phases 1, 2, **and** 3 complete. Phase 1 is DoD-satisfied but **not WBS-complete** — `research/ingest/calendar.py` (the WI-5 macro event calendar) has no conditioning statement and no normalisation-contract-version, unlike the three conformant paths (`dvol`, `funding`, `binance_ohlcv`). WBS 1.6 has one path remaining.

---

## 9. Remaining Risks

| ID | Risk | Likelihood | Impact | Assessment |
|---|---|---|---|---|
| **RR-1** | Warm-up `mode: warn` is never flipped to `refuse`, and the staged rollout becomes a permanent no-op | **Moderate** — the flip requires a deliberate act with no scheduled owner, and TD #10 means the "review the warnings logged" precondition has no durable evidence source to review | Low today (zero features deficient), rising if a future feature declares a warm-up the corpus cannot satisfy | The mechanism would then silently proceed exactly as pre-WBS-2.6 — the precise silent-degradation ADR-I010 PS4 exists to eliminate. **Recommend an explicit flip decision be scheduled, not left ambient** |
| **RR-2** | `F-2a`/`F-2b`/`F-2c`/`F-2e` are never picked up because no WBS item owns them | **Moderate–high** — unowned work in a roadmap-driven programme tends to stay unowned | Medium — ADR-I010 would remain permanently partially realised while every report truthfully says "Phase 2 complete" | **This review's principal substantive finding.** Mitigated only by recording it somewhere durable at closure (§10 recommendation 3) |
| **RR-3** | The `F-3` commit gap persists and a machine loss destroys Phase 0–2 plus all eight architecture ADRs | Low per-day, cumulative | **Severe and unrecoverable** — the ADRs are authored artifacts, not regenerable like `feature_store.db` | Program P1 hit this exact risk (its own R6), named it, and closed it by committing before declaring closure. **The precedent is unambiguous** |
| **RR-4** | `EH-001`/`EH-002` remain open indefinitely; the Hazard Elimination Sprint is never scheduled | Moderate | Low today — every remaining instance is dormant, and both registers say so honestly | Acceptable. The registers exist precisely so this stays visible |
| **RR-5** | Empty `sensitivity_variants` on all 17 Specifications is inherited by Phase 4's robustness assessment as though it were a real declaration | Low–moderate | Medium — FG7's anti-post-hoc-selection guarantee is vacuous with an empty set | Newly identified (TD #14). Worth naming before Phase 4 consumes the field |
| **RR-6** | `feature_store.db` backup trigger is defined only in terms of "a second non-script writer", missing the first-materialisation trigger | Low | Medium if it fires — a materialised value set is by definition not regenerable-equivalent | See `F-4`. A one-line addition to the existing strategy document would close it |
| **RR-7** | Building on Draft ADRs (roadmap §1.2 / R1); `G-CONST` and `G-STAB` both remain closed | Known and accepted | Bounded | **Unchanged by this review.** Phase 2 is additive and independently revertible, so the reversal cost stays as R1 assessed it. Not a Phase 2 finding |

---

## 10. Formal Recommendation

### Verdict

> **Phase 2 (Feature Store) is WBS-complete and verification-sound. Recommend closure, conditional on two items — one of which is owner-only.**

The engineering work meets or exceeds the standard the phase set for itself. Every reproducible claim reproduced exactly. Four incidents were found and closed inside the phase, three by the implementer's own adversarial testing. No undeclared scope creep, no undeclared file changes, no architecture document touched, no placeholder implementation, and no debt item disguised as complete were found by this audit.

### Blocking items

| ID | Item | Owner | Why blocking |
|---|---|---|---|
| **B-1** | **Reviewer Checklist sign-off (RC1–RC8, RC8 date/name) on all five Phase 2 verification records** | **Rohith — cannot be delegated or self-granted** | The Verification Standard's §12 pass criterion and `D11` are both currently unsatisfied for every Phase 2 milestone. This audit discharges RC4/RC5 independently and RC1/RC2/RC6/RC7 by inspection, so the remaining act is the sign-off itself — but until it is recorded, **no Phase 2 milestone has formally passed its own Definition of Done** |
| **B-2** | **Commit Phase 2 (and Phases 0–1, and `docs/architecture/`) to version control** | Rohith | `F-3`/`RR-3`. Program P1 set the precedent explicitly: it did not consider its own durability programme closed until its output stopped existing only on one machine. Closing a *Feature Store* phase while the feature store exists only in an uncommitted working tree repeats the exact failure mode P1 closed |

### Non-blocking, but required to be recorded at closure

3. **Record `F-2a`, `F-2b`, `F-2c`, `F-2e` as carried-forward ADR-I010 realisation gaps with named owners** — ideally as a short section in the roadmap or a successor backlog, mirroring `CLAUDE.md` §19's Post-P1 Maintenance Backlog pattern. Without this, `RR-2` is very likely to materialise: the work is real, required by an adopted-track ADR, and currently owned by nobody.
4. **Adopt the "WBS-complete vs. ADR-realised" distinction in language**, exactly as the roadmap §6 note did for Phase 1. Future reports should say *"Phase 2 is WBS-complete"* and never *"the Feature Store is complete."*
5. **Schedule an explicit warm-up flip decision** (`RR-1`), rather than leaving `mode: warn` as an ambient default with no owner.
6. **Add "first materialisation" as a second trigger condition** to `feature_store_backup_strategy.md` (`RR-6`) — a one-line documentation change, not code.
7. **Carry TD #14/#15 (empty sensitivity variants; no FG1 registration receipt)** into the same carried-forward record as item 3.

### What this review explicitly does *not* recommend

- **No remediation of `EH-001`/`EH-002`.** Both are dormant, honestly registered, and governed by their own register's §3 rule against incidental closure.
- **No retirement of any implementation.** WBS 2.5's conclusion — that demonstrating equivalence is a *precondition* for retirement eligibility, not retirement itself — is correct and should stand.
- **No reopening of the `G-CONST`/`G-STAB` gates.** Unchanged and untouched by Phase 2.
- **No Phase 3 blocking.** Phase 3 depends on Phase 0, not Phase 2, and is entry-ready today (§8). It may begin before `B-1` is discharged if Rohith prefers.

### Sign-off

| Role | Name | Date |
|---|---|---|
| Audit pass (read-only, this document) | Claude Opus 5 | 2026-08-05 |
| **Phase 2 closure authority** | **Rohith Suresh** | **_______________** |

---

## Appendix — Audit Method

Read-only throughout. No file under `research/`, `src/`, `tests/`, or `docs/architecture/` was modified; the only file written was this report. Commands run were limited to `pytest` (both environments), `report.py --check`, read-only SQLite queries against `feature_store.db` and `ledger.db`, `git status`, and `grep`/`find` sweeps.

Claims were re-derived rather than accepted: suite counts came from fresh runs, not from the records; registry contents came from direct SQL, not from the Migration Register's tables; the `F-2a` and `F-2c` findings came from repository greps and a binding-ID query, not from any record's own self-assessment. Where this audit agrees with a milestone's own claim, that agreement is an independent reproduction, not a restatement.
