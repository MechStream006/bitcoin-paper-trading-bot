# Phase 2 (Feature Store) — Closeout

| Field | Value |
|---|---|
| **Document type** | Phase Closeout — the terminal record for Phase 2 |
| **Scope** | Engineering Implementation Roadmap v1.0.1 §6, Phase 2 (WBS 2.1–2.7) |
| **Follows** | [Phase 2 Closure Review](phase-2-closure-review.md) (2026-08-05) — this document resolves its findings where resolvable |
| **Governing ADR** | [`ADR-I010`](../../../architecture/adr/ADR-I010-feature-store-architecture.md) Feature Store Architecture v0.1.0 (**Draft**) |
| **Date** | 2026-08-05 |
| **Code changed** | **None.** Documentation only, per instruction. No file under `research/`, `src/`, `tests/`, or `docs/architecture/` was modified |
| **Closure authority** | **Rohith Suresh — sign-off pending (`FS-DEC-002`)** |

---

## 1. Phase 2 Closeout Report

### 1.1 What Phase 2 set out to do, and what it did

Phase 2 built the Feature layer of the Research plane: a registry that gives every feature an addressable, versioned, content-addressed identity, and a value store that holds numbers without holding authority. Seven WBS items, seven verification records, four incidents found and closed inside the phase.

**All seven WBS items are complete.** Every headline claim was independently re-derived at the Closure Review rather than accepted from the implementer, and reproduced exactly:

| Evidence | Value | Independently reproduced |
|---|---|---|
| Full suite — `btc_bot`, repo-wide | 1881 passed / 19 skipped / 0 failed | ✅ |
| Full suite — base env, `tests/` scope | 1525 passed / 37 skipped / 0 failed | ✅ |
| `feature_store.db` | 17 Definitions, 17 Specifications, all `Active`, `integrity_check ok` | ✅ |
| Conditioning rules registered | 17 × `preserving` | ✅ |
| Real `ledger.db` | 70 / 26 / 13 / 142, `verify_chain() ok=True` | ✅ — untouched by Phase 2 |
| `report.py --check` | OK — 35 families (9 / 22 / 4) | ✅ |
| Research ↔ operational boundary | zero imports either direction | ✅ |

**Problem statements resolved.** `ADR-I010` §2 names seven. Phase 2 closes four outright and one partially:

| ID | Problem | Outcome |
|---|---|---|
| **PS1** | Dual implementation without an equivalence guarantee | **Resolved as demonstration.** All 5 hash-pinned decision functions proven equivalent to their research counterparts by exact array match over the real 1987-row corpus. **Not** resolved as a registration precondition — see `FS-GAP-003` |
| **PS2** | Feature definitions are prose inside code objects | **Resolved.** 17 governed, versioned Definition records |
| **PS3** | No addressable feature identity | **Resolved.** Content-addressed Specification hashes, stable across three `--commit` runs and two sabotage-and-revert cycles |
| **PS4** | Warm-up requirements are implicit | **Resolved.** Declared, mandatory, and enforced with three governed modes; refuse-mode proven against the real corpus |
| **PS5** | No path to exact-bytes recoverability | **Mechanism built, never exercised.** See `FS-TD-014` |
| **PS6** | Input requirements are undeclared | **Partially resolved.** Sources and fields are declared and Tier-A-enforced; the admissibility floor and lane are not — `FS-GAP-001` |
| **PS7** | Conditioning does not propagate | **Resolved.** Propagation computed from the real ingest writers' own population statements, never re-authored; dual-source features carry both statements forward |

### 1.2 Resolution of the Closure Review's findings

| Finding | Disposition at closeout |
|---|---|
| **`B-1`** — Reviewer Checklists unsigned on all five records | **Not resolvable by this closeout.** Escalated to [`FS-DEC-002`](../../feature_store_technical_debt_register.md#fs-dec-002--reviewer-sign-off-on-five-phase-2-verification-records) with a named owner. This remains the one item blocking formal closure |
| **`B-2`** — Phase 0–2 exist only as uncommitted working-tree state | **Resolved to a plan, not an act.** §4 below is a complete, ordered, dependency-verified commit plan. Executing it is Rohith's call, consistent with this repository's standing rule that commits are never made without an explicit in-the-moment instruction |
| **`F-2a`…`F-2e`** — five `ADR-I010` requirements with no owning WBS item | **Resolved.** All five now have stable identifiers, severities, blast-radius statements, and nearest-owner suggestions in the new [Feature Store Technical Debt Register](../../feature_store_technical_debt_register.md) §2. This directly closes `RR-2`, the risk that unowned work disappears because every milestone truthfully reports itself complete |
| **`F-4`** — Value Store has no tenant; backup rationale expires on first materialisation | **Resolved.** [`feature_store_backup_strategy.md`](../../feature_store_backup_strategy.md) gains §3.1, a second trigger condition keyed to the first row in `feature_value_materialized`, with the exact check query and an instruction to reconsider — not inherit — the off-host carve-out when it fires |
| **Recommendation 3** — record carried-forward gaps in a durable home | **Resolved.** The Technical Debt Register, structured on `CLAUDE.md` §19's pattern as recommended |
| **Recommendation 4** — adopt "WBS-complete vs ADR-realised" language | **Resolved.** Adopted throughout this document and stated as a standing rule in §1.3 |
| **Recommendation 5** — schedule an explicit warm-up flip decision | **Resolved to a tracked decision.** [`FS-DEC-001`](../../feature_store_technical_debt_register.md#fs-dec-001--flip-warm-up-mode-from-warn-to-refuse), with the discovery that its own second precondition is currently unsatisfiable as written (`FS-TD-009`) |
| **Recommendation 7** — carry TD #14/#15 forward | **Resolved.** `FS-TD-013` (empty sensitivity variants) and `FS-GAP-004` (no registration receipt) |

### 1.3 The standing distinction this phase forced

> **Phase 2 is WBS-complete. `ADR-I010` is not fully realised.**

Phase 2 delivered exactly what the roadmap's WBS 2.1–2.7 asked of it. Five `ADR-I010` requirements are unimplemented, and **the roadmap never assigned any of them to a Phase 2 item.** The gap is between the roadmap's Phase 2 scope and the ADR's requirement set — a planning gap surfaced at closure, not an execution gap.

This is the same distinction the roadmap itself was forced to draw at §6 for Phase 1 (*"Phase 1 DoD-satisfied"* vs *"Phase 1 WBS-complete"*), after an engineering report conflated the two. **Adopted here as a standing rule:** every future report naming Phase 2 must say which sense it means. *"The Feature Store is complete"* is not a sentence this programme is entitled to write.

### 1.4 What went well, recorded because it should be repeated

- **TV3 escalated beyond the standard's own bar.** From WBS 2.6 onward, empirical falsifiability was performed at two levels — unit sabotage *and* live-script sabotage against the real `feature_store.db`, each reverted with hash-identical confirmation of all 17 spec hashes. The Verification Standard requires one representative test per milestone.
- **A long-standing "flake" was root-caused, not re-dismissed.** `test_DFA07_cross_validation_vs_signals_py` had been carried since Program P1 Increment 6 as unexplained and intermittent. WBS 2.5 proved it deterministic (a third `EH-002` instance), fixed it, and verified the fix in both directions via `git stash`.
- **`INC-0004` was caught before it shipped**, during manual verification, before any test existed — and correctly judged *not* to warrant a hazard entry.
- **`engine.py` was never edited.** The entire migration is a thin wrap, as the WBS item required. Two structural import-time assertions make the `ret_fwd1` feature/label exclusion enforced rather than merely documented.
- **Incident lessons propagated forward into new code.** `registry.py` and `value_store.py`'s `init_db()` are both CREATE-only and cite `INC-0001` by name — the strongest form of incident closure, and the reason `EH-001` did not grow during Phase 2.

### 1.5 One observation, recorded without registering a hazard

`research/quality/admissibility.py` (Phase 1) uses `sys.path.insert(0, …)` to reach `research/admissibility/` — the same pattern class as `INC-0004`. **No collision exists**: the target directory contains `composition.py`, `lattice.py`, `lattice_version.py`, none of which shadows a stdlib module. This is recorded as an observation for the future Hazard Elimination Sprint's fuller sweep (both hazard entries already state their sweeps were non-exhaustive), **not** as a new hazard registration — nothing is currently at risk, and registering a hazard for a non-collision would dilute the register.

---

## 2. ADR-I010 Gap Matrix

Every requirement in `ADR-I010`, placed in exactly one bucket. **A fifth bucket — "Unowned" — was added to the four requested**, because forcing the five unowned gaps into "Deferred by Roadmap" would misrepresent unplanned work as planned, and that misrepresentation is precisely what the Closure Review's `RR-2` warned about.

### 2.1 Implemented — delivered and demonstrated in Phase 2

| `ADR-I010` ref | Requirement | Delivered by | Real-data demonstration |
|---|---|---|---|
| §6.1 | Two record types: Definition (versioned) + Specification (content-addressed) | WBS 2.1 | 17 + 17 |
| §6.2 | Content addressing, append-only, phase-tracked, hash-only resolution, single writer | WBS 2.1 | Hash stability proven across 3 runs + 2 sabotage cycles |
| §6.3 | Registry refusals (unknown Definition, undeclared inputs/warm-up/conditioning, >1 binding) | WBS 2.1, 2.3 | Negative-tested per field |
| §7.1 | Two tiers distinguished by obligation, not content | WBS 2.2 | ⚠️ code only — `FS-TD-014` |
| §7.3 | Stored value disagreeing with recomputation is wrong by definition; divergence reported | WBS 2.2 | ⚠️ never fired against real data |
| §8.2 | Eight mandatory field classes, none defaultable | WBS 2.1 | All 17; omission rejected, empty-but-declared accepted |
| §8.4 | Warm-up declared, checkable before compute, refused not truncated | WBS 2.6 | Refuse-mode run against real corpus, 0 refusals |
| §9.1 | Conditioning propagation; never dropped | WBS 2.7 | All 17 resolve; dual-source features carry both |
| §10 | Three composing versioning levels | WBS 2.1 | Definition version + spec hash live |
| §11 | Lifecycle phases and allowed arcs; Rejected is permanent | WBS 2.1 | Arc table transcribes the ADR diagram verbatim |
| §12.1–12.3 | Compute-on-read default; materialisation exceptional, write-once, obligation-bound | WBS 2.2 | Correct by construction |
| §13 Tier A | Specification completeness | WBS 2.3 | Negative-tested |
| §13 Tier B | Binding equivalence; failure Rejected and recorded | WBS 2.3, 2.4 | Ran for real on all 17 during `--commit` |
| §14.2 FG3 | Rejections are permanent records | WBS 2.1 | Rejected row written before raising |
| §14.2 FG4 | A Specification is never edited | WBS 2.1 | No UPDATE path except phase |
| §14.2 FG6 | Registry never computes; Value Store never judges | WBS 2.1, 2.2 | `resolve()` takes `compute_fn` from caller |
| §14.2 FG7 | Sensitivity variants declared at registration | WBS 2.1 | ⚠️ mandatory but empty on all 17 — `FS-TD-013` |
| §17 | Asset is a resolution parameter, never a Specification property | WBS 2.1, 2.4 | No asset field anywhere |
| §18 R2, R8 | Equivalence may be hard; warm-up refusal will surface degradation | WBS 2.5, 2.6 | **Both retired favourably** — all 5 equivalent, zero features deficient |

### 2.2 Deferred by Roadmap — a later owner is explicitly named

| `ADR-I010` ref | Requirement | Named owner | Recorded as |
|---|---|---|---|
| §11 | *"Retirement requires zero citations"* — citation-index check | `ADR-I012` / Phase 4 | `FS-TD-004` |
| §18 R9 | Greeks / IV / OI have no feature surface | **Out of scope by the ADR's own words** — *"whether they should exist is a research judgment"* | Not debt |
| §16 | Dataset integration (6 contract items) | Phase 4 — `ADR-I012` is the counterparty; nothing to build unilaterally | Not debt |

### 2.3 Requires Phase 3 (Label Store)

| Item | Why Phase 3 | Recorded as |
|---|---|---|
| `ret_fwd1` migration | Excluded from the Feature Store on constitutional grounds — `ADR-I011` §1.1's Central Asymmetry: it is computed by a **negative** shift, so it depends on data after `t`. Registering it as a feature would violate the exact type distinction `ADR-I010`/`ADR-I011` exist to enforce | Feature Migration Register §3; enforced by an import-time assertion |

**Nothing in `ADR-I010` itself requires Phase 3.** The Feature Store does not depend on labels. This bucket contains exactly one entry, and it is an *output* of Phase 2 (a correctly-refused migration) rather than an unmet Feature Store requirement. Stated explicitly rather than left as an empty table.

### 2.4 Requires Phase 4 (Dataset Builder)

| `ADR-I010` ref | Requirement | Why Phase 4 | Recorded as |
|---|---|---|---|
| §12.4 | The general `resolve()` path with `as_of`-scoped history | The Dataset Builder is the architecturally-intended general caller; no other real consumer exists | `FS-TD-008` |
| §9 | Conditioning attached to the served value set | Requires extending `ResolvedValue` — deliberately not done unilaterally | `FS-TD-011` |
| §7.2, §15 | Real `corpus_version` composition and provenance closure | Needs `ADR-I009` §16 partition revisions (Phase 1 WBS 1.3) **and** a dataset boundary to compose at | `FS-GAP-005` |
| §12.2 C1 | Materialisation exercised for real | Triggered by the first Training Dataset — *"a Training Dataset triggers materialisation case C1; a Research Dataset does not"* (§16.5) | `FS-TD-014` |
| §11 | Citation-index check on retirement | Same as §2.2 | `FS-TD-004` |

### 2.5 Unowned — required by the ADR, assigned to no WBS item *(bucket added at closeout)*

| `ADR-I010` ref | Requirement | Severity | Actionable today? | Recorded as |
|---|---|---|---|---|
| §9.2, §12.4, §13 Tier C | **Admissibility inheritance** — value-set ceiling is the meet over inputs (IQROS-P4) | **High** | Partly — Phase 0's lattice exists and is unused | [`FS-GAP-001`](../../feature_store_technical_debt_register.md#fs-gap-001--admissibility-inheritance-is-not-implemented) |
| §13 Tier D | **Output hygiene** — validity fraction, degenerate detection, warm-up boundary exactness | Medium | **Yes, fully** — all 17 features and the real corpus are available | [`FS-GAP-002`](../../feature_store_technical_debt_register.md#fs-gap-002--tier-d-output-hygiene-is-not-implemented) |
| §8.3 | **Equivalence as a registration precondition**, not a testing question | Medium | **Yes** | [`FS-GAP-003`](../../feature_store_technical_debt_register.md#fs-gap-003--binding-equivalence-is-test-enforced-not-registration-enforced) |
| §14.2 FG1, §9 | **Registration emits a governance receipt** | Medium | **Yes** — receipt infrastructure is complete and audited | [`FS-GAP-004`](../../feature_store_technical_debt_register.md#fs-gap-004--registration-emits-no-governance-receipt) |
| §7.2, §15 | Real `corpus_version` / provenance closure | Medium | No — depends on Phase 1 and Phase 4 | [`FS-GAP-005`](../../feature_store_technical_debt_register.md#fs-gap-005--no-real-corpus_version-and-no-value-set-provenance-closure) |

> **Three of these five are actionable today with no phase dependency.** That is the single most useful fact in this matrix. `FS-GAP-002` in particular would either confirm all 17 warm-up declarations are exactly right or find a real off-by-N — an outcome that is currently unknown either way.

---

## 3. Technical Debt Register

**Delivered as a standalone living document:** [`docs/engineering/feature_store_technical_debt_register.md`](../../feature_store_technical_debt_register.md).

It is separate from this closeout deliberately. A closeout is a point-in-time record that should never be rewritten; a debt register is a living document that must be. The register follows the shape of this repository's existing `hazard_register.md` and `feature_migration_register.md` rather than inventing a new one.

**Contents:** 21 entries with stable, never-reused identifiers across three deliberately separated classes.

| Class | Count | Meaning |
|---|---|---|
| `FS-GAP-001`…`005` | 5 | `ADR-I010` requirements with no implementation and no owning WBS item |
| `FS-TD-001`…`014` | 14 | Milestone-scoped simplifications (one reclassified into a gap, one downgraded on evidence) |
| `FS-DEC-001`…`002` | 2 | Deliberate staged choices requiring an explicit human act |

**Two entries changed status on evidence during consolidation**, rather than being copied forward unexamined:

- `FS-TD-002` (JSON hashing) **downgraded** — WBS 2.1 flagged a float-instability risk *"worth revisiting once real parameter values are known."* They are now known: all 17 Specifications carry integer-only parameters, and hashes proved stable across five independent regeneration events. The trigger condition is confirmed absent.
- `FS-TD-003` (`input_requirements` shape-only validation) **reclassified** into `FS-GAP-001` — it was logged as debt owned by *"WBS 2.4+"*, which never closed it and could not have. It is not a validation-depth shortcut; it is the same missing admissibility wiring. The ID is retained rather than deleted, because a milestone record cites it.

**One coupling was discovered during consolidation and is new:** `FS-TD-009` (warn-mode warnings have no durable capture) is not merely cosmetic — `warmup_contract.md` §5 makes *"a review of any `WARNING:` lines actually logged"* a **precondition** for the `FS-DEC-001` flip, and there is no durable place to review them from. The flip decision's own evidence requirement is unsatisfiable as written until either `FS-TD-009` closes or the precondition is restated.

---

## 4. Commit Plan

### 4.1 The problem this solves

Phases 0, 1, and 2 — plus all eight architecture documents, every engineering process document, and every verification record — exist **only as uncommitted working-tree state on one machine**. `git status` shows 18 modified/renamed and 63 untracked files.

This is the identical risk Program P1 named as its own `R6` and then explicitly closed by committing before declaring the program closed (`CLAUDE.md` §18: *"closing the last durability gap in the durability program itself — its own output no longer exists only on one machine"*). Closing a **Feature Store** phase while the feature store exists only in an uncommitted working tree would repeat the exact failure mode P1 closed.

**The ADRs are the highest-value item at risk** — `feature_store.db` is regenerable in seconds and `research/` code could in principle be rewritten, but ten hand-authored architecture documents cannot be reconstructed from anything.

### 4.2 Grouping principle, and one honest limitation

Commits are grouped **by subsystem and phase**, not strictly per-WBS-item.

**Per-WBS commits are not achievable from the current working tree.** Several files accumulated changes across multiple milestones before any commit was made — `research/ingest/dvol.py`, for example, carries both WBS 1.1 (normalisation contract version) and WBS 1.5 (conditioning metadata) in one unstaged diff. Splitting them would require interactive per-hunk staging of files whose milestones are already separately and thoroughly documented.

**This is acceptable, and the reason is structural:** the per-WBS granularity that the commit history cannot carry is fully preserved in the eleven verification records under `docs/engineering/verification/records/`. Reconstructing "what WBS 2.6 changed" is a documentation lookup, not a `git log` archaeology exercise. Attempting hunk-level reconstruction now would risk producing commits that do not individually build — a worse outcome than coarser grouping.

### 4.3 Dependency verification

Checked directly, not assumed:

- `research/quality/admissibility.py` (Phase 1) imports `composition` and `lattice` from `research/admissibility/` (Phase 0) → **Phase 0 lattice must precede Phase 1**.
- `research/feature_store/conditioning.py` (Phase 2) reads `CONDITIONING` / `_conditioning()` live from `research/ingest/{dvol,funding,binance_ohlcv}.py` (Phase 1) → **Phase 1 must precede Phase 2**.
- `research/feature_store/` imports nothing from `research/evidence_library/` → Phase 0's receipt substrate and Phase 2 are **independent**; their relative order is free.
- `research/feature_store/` ↔ `src/` → **zero imports either direction**, verified.
- `feature_store.db` is already gitignored (`.gitignore` lines 43–45, part of the Phase 2 diff) → **no database file enters version control**.

### 4.4 Commit order

Eight commits. Each is independently buildable; none requires a later commit to leave the tree in a working state.

| # | Commit | Files | Rationale |
|---|---|---|---|
| **1** | `docs(governance): finalise Program P1 closure record` | `CLAUDE.md`, `docs/adr/0002-*`, `docs/adr/…review-postgresql…` *(staged rename)*, `docs/governance/p1_assurance_declaration.md`, `docs/ops/migration-harness-dry-run-*`, `docs/ops/production-migration-runbook-increment5.md`, `research/evidence_library/MIGRATIONS.md` | Oldest residue, unrelated to the roadmap. Lands first so nothing later is entangled with it. Includes P1 Increment 5 + closure records that post-date `c14ba38` |
| **2** | `docs(architecture): IQROS master architecture and ADR-I009–I015 (Draft)` | `docs/architecture/**` (10 files) | **The highest-value, least-recoverable artifacts.** Every subsequent commit cites them. Should land first among new work regardless of anything else in this plan |
| **3** | `docs(engineering): implementation roadmap, verification standard, milestone template` | `implementation_roadmap_v1.md`, `verification/engineering_verification_standard.md`, `verification/templates/milestone_verification_template.md` | The process framework every following commit is verified against |
| **4** | `feat(admissibility): Phase 0 — admissibility lattice (WBS 0.1–0.4)` | `research/admissibility/**` (4 files), `tests/test_admissibility.py` | First engineering phase. **Blocks commit 6** |
| **5** | `feat(receipts): Phase 0 — receipt substrate extension (WBS 0.5–0.9)` | `research/evidence_library/{receipts,receipt_ledger,governance_verification}.py`, `research/evidence_library/migrate_receipt_attestation_basis.py`, `tests/test_{governance_receipts,governance_verification,migrate_receipt_attestation_basis}.py` | Independent of commit 4 (disjoint files, per roadmap §15). Includes `INC-0001`'s fix |
| **6** | `feat(storage): Phase 1 — storage plane conformance (WBS 1.1–1.6b)` | `research/ingest/{binance_ohlcv,dvol,funding}.py`, `research/storage/{manifest.py,raw_lake.py}`, `research/quality/admissibility.py`, `research/validate_wi6.py`, `tests/test_{dvol,funding,binance_ohlcv}_pilot_conformance.py`, `tests/test_{ingest_normalization_contract_version,storage_manifest,raw_lake,quality_admissibility}.py` | **Requires commit 4.** Phase 1 is DoD-satisfied, **not WBS-complete** — `research/ingest/calendar.py` remains unconformed. The commit message must say so |
| **7** | `feat(feature-store): Phase 2 — Feature Store, WBS 2.1–2.7` | `.gitignore`, `research/feature_store/**` (8 files), `tests/test_{feature_store_registry,feature_store_value_store,evidence_engine_bindings,migrate_evidence_engine_features,decision_function_research_equivalence,warmup,conditioning}.py`, `research/forward_evidence/tests/test_atr_expansion.py` | **Requires commit 6.** `.gitignore` belongs here — its only change is the `feature_store.db` ignore rules. `test_atr_expansion.py` belongs here — its only change is the `EH-002` third-instance fix from WBS 2.5 |
| **8** | `docs(engineering): Phase 0–2 verification records, registers, incidents, and Phase 2 closeout` | `docs/engineering/verification/records/**` (11 files), `docs/engineering/incidents/**` (4), `hazard_register.md`, `feature_migration_register.md`, `feature_store_technical_debt_register.md`, `feature_store_backup_strategy.md`, `warmup_contract.md` | Lands last so the tree at this commit has code and its full verification evidence together. **This is the tag target** (§5) |

### 4.5 Notes on specific placements

- **`hazard_register.md` cannot be split.** Its single file records `EH-001` (Phase 0 origin) and `EH-002` (Phase 2 origin, 3-of-4 fixed as of WBS 2.5). Placed in commit 8 with the other registers; splitting it across commits 5 and 7 would require hunk staging for no benefit.
- **`.gitignore` is genuinely Phase 2 only.** Verified: the diff is `+6` lines, all `feature_store.db` ignore rules, no deletions. P1's `CLAUDE.md` un-ignore already landed in `c14ba38`.
- **No database file is committed anywhere in this plan.** `feature_store.db` and `ledger.db` are both gitignored; the plan adds none.
- **Branch.** Current branch is `main`. Per this repository's standing policy, this work should land on a feature branch (suggested: `feat/research-platform-phases-0-2`) and merge, rather than being committed directly to `main` — matching how Program P1's own increments were executed.
- **Executing this plan requires an explicit instruction.** It is written to be directly actionable; nothing in it has been run.

---

## 5. Release Recommendation

### 5.1 Should Phase 2 be tagged?

> **Yes — but not yet.** Tag after `FS-DEC-002` (reviewer sign-off) is recorded and commits 1–8 have landed.

**Why tag at all.** Phase 2 is a genuine, self-contained capability boundary: a working Feature Registry holding 17 real, Active, content-addressed Specifications with demonstrated equivalence, enforced warm-up, and computed conditioning propagation. It is exactly the kind of state this repository has tagged before (`program-p1-closed`, `i1-v1.0.0`, `v0.4-governance-freeze`). It is also the last stable point before Phase 3 begins changing the surrounding structure.

**Why not yet.** A tag asserts *"this state was certified."* Every Phase 2 verification record currently reads `*Pending — Rohith*` with RC1–RC8 unchecked. Tagging before sign-off would mark an unreviewed state as a release point and quietly weaken the separated-authorities principle the Verification Standard §12 exists to protect. The gap is a single act, not a body of work.

**Tag target:** commit 8 — the first commit at which code and its full verification evidence exist together. Tagging commit 7 would point at code whose verification records are not yet in the tree.

### 5.2 Suggested version identifier

> **`feature-store-v1.0.0`** — annotated (`git tag -a`), matching every existing tag in this repository.

**Why this form.** Existing tags follow no single convention (`program-p1-closed`, `phase-a1-complete`, `i1-v1.0.0`, `v0.4-governance-freeze`, `governance-v2-*`). Of those, `i1-v1.0.0`'s subject-plus-semver shape is the clearest and is followed here.

**Why not `phase-2-complete`.** **`phase-a1-complete` already exists in this repository and refers to something entirely different** — the 2026 research Phase A.1 (WI-1…WI-8 historical evidence infrastructure), not the roadmap's Phase A1 (IQROS adoption). A `phase-2-complete` tag would sit next to it and invite exactly the wrong inference. The roadmap's own §7 had to retire its `P2-` increment prefix for this same collision reason; the same discipline applies to tags.

**Why `1.0.0` and not `0.x`.** The registry's public surface is real, exercised against real data, and consumed by a real migration. Semver `1.0.0` asserts a stable interface, which is true. It does **not** assert that `ADR-I010` is fully realised — §2.5's five gaps are Feature Store *capability*, not interface, and closing them is expected to be additive (`1.1.0`) rather than breaking.

**Suggested tag message:**

```
Feature Store v1.0.0 — Engineering Roadmap Phase 2 complete (WBS 2.1-2.7)

Feature Registry and Value Store per ADR-I010 (Draft). 17 shared derived
features migrated as Active, content-addressed Specifications; equivalence
demonstrated for all 5 hash-pinned decision functions; warm-up enforced as a
governed contract term (mode: warn); conditioning propagation computed from
real ingest-writer population statements.

WBS-complete, not ADR-realised: 5 ADR-I010 requirements remain unimplemented
and unowned. See docs/engineering/feature_store_technical_debt_register.md.
```

### 5.3 What this is not a release of

Not a release of the trading bot, and not on its version line (`v0.4-governance-freeze` is app-scoped). Nothing in `src/` is touched, no operational behaviour changes, and no scheduled job's behaviour changes. The Feature Store has **zero runtime consumers** — it is research-plane infrastructure whose only caller today is a human-invoked migration script.

**Optional, at Rohith's discretion:** a second annotated tag `research-platform-phase-0-1` on commit 6, marking the substrate Phase 2 sits on. Not recommended as necessary — Phase 1 is DoD-satisfied but not WBS-complete, so a tag there would mark a deliberately partial state and would need a message saying so.

---

## 6. Phase 3 (Label Store) Handoff Package

Phase 3 is governed by [`ADR-I011`](../../../architecture/adr/ADR-I011-label-store-architecture.md) and covers WBS 3.1–3.6. **Per roadmap §5, Phase 3 depends on Phase 0, not Phase 2** — it reuses Phase 2's registry *pattern* without a hard code dependency. This package is therefore a set of transferable assets and lessons, not a dependency contract.

### 6.1 Assumptions Phase 3 may safely make

| # | Assumption | Basis |
|---|---|---|
| **A1** | The registry pattern in `research/feature_store/registry.py` is proven against real data and safe to copy | 17 Specifications registered, activated, and hash-stable across 5 regeneration events |
| **A2** | `ADR-I011`'s registry properties genuinely need no new mechanism | The ADR says so itself (§6: *"mirror ADR-I010 §6.2 exactly… No new governance mechanism is introduced"*) |
| **A3** | `ret_fwd1` is unclaimed and reserved for the Label Store | Deliberately excluded from Phase 2, enforced by an import-time assertion, not merely documented |
| **A4** | The H6 forward-label system is unmodified and available to wrap | Phase 2 touched exactly one file under `research/forward_evidence/` — one test method, for the `EH-002` fix |
| **A5** | `research/evidence/engine.py` is unmodified | Never edited across the entire phase; verified by diff |
| **A6** | The three real ingest sources carry conditioning statements Phase 3 can consume the same way | `research/ingest/{dvol,funding,binance_ohlcv}.py`; read live by `conditioning.py`, never copied |
| **A7** | Phase 0's admissibility lattice exists, is tested, and is **unused** — Phase 3 may be its first consumer | `FS-GAP-001`: zero references from `research/feature_store/` |
| **A8** | Both full-suite baselines are stable and reproducible | 1881/19/0 (`btc_bot`, repo-wide); 1525/37/0 (base env, `tests/` scope) |

### 6.2 Guarantees Phase 2 provides

| # | Guarantee | Strength |
|---|---|---|
| **G1** | 17 Feature Specifications are `Active` and citable by hash | **Strong** — content-addressed, append-only, DB-trigger-protected on definitions |
| **G2** | Specification hashes are stable and deterministic | **Strong** — unchanged across three `--commit` runs and two full sabotage-and-revert cycles |
| **G3** | The 5 hash-pinned decision functions are equivalent to their research counterparts | **Moderate** — proven by exact array match over the real corpus, but **test-enforced, not registration-enforced** (`FS-GAP-003`) |
| **G4** | Every migrated feature's warm-up is declared and satisfied by the real corpus | **Moderate** — verified in hard-refuse mode against real data; but Tier D's boundary-exactness check has never run (`FS-GAP-002`), so *declared* ≠ *verified against actual first-valid-index* |
| **G5** | Every migrated feature's conditioning propagates from real source statements | **Strong** — computed live, all 17 resolve, never dropped |
| **G6** | `feature_store.db` is fully regenerable from source | **Strong today**, and **expires** on the first materialised row (backup strategy §3.1) |
| **G7** | Zero research → operational coupling | **Strong** — verified both directions |
| **G8** | No admissibility ceiling is computed or carried anywhere | **This is a guarantee of absence.** Phase 3 must not assume features arrive with a ceiling — they do not (`FS-GAP-001`) |

### 6.3 Interfaces available to Phase 3

Reusable **as a pattern**; `ADR-I011` §6 explicitly sanctions mirroring rather than importing.

```python
# research/feature_store/registry.py — the shape WBS 3.1 should mirror
init_db(path)                                              # CREATE-only, never ALTER (INC-0001)
register_definition(db_path, name, rationale,              # always creates the NEXT version
                    intended_role, actor, registered_at) -> FeatureDefinitionHandle
get_latest_definition(db_path, name) -> dict | None        # the idempotency read (INC-0002)
register_binding(binding_id, engine_version, fn)           # in-process binding registry
get_binding(binding_id, engine_version)                    # raises UnknownBindingError, never substitutes
register_specification(db_path, *, ...) -> spec_hash       # Tier A; writes a permanent Rejected row on failure
activate_specification(db_path, spec_hash, equivalence_check)   # Tier B; fail-closed
retire_specification(db_path, spec_hash, from_phase)       # no citation check yet (FS-TD-004)

# research/feature_store/value_store.py — two tiers, distinguished by obligation
put_cache / get_cache / evict_cache                        # evictable, no authority
materialize(...) -> content_hash                           # write-once; DivergenceEvent on conflict
get_materialized(...)                                      # verifies stored hash before returning
resolve(db_path, spec_hash, corpus_version, as_of, compute_fn, *, computed_at) -> ResolvedValue

# research/feature_store/warmup.py — directly reusable, not just a pattern
check_warmup(spec_name, warmup_periods, available_periods, *, mode=None) -> WarmupCheckResult
load_warmup_mode(policy_path=None)                         # 'disabled' | 'warn' | 'refuse'

# research/feature_store/conditioning.py — directly reusable, not just a pattern
propagate_conditioning(rule, input_conditionings, *, restriction=None, universe_statement=None)
conditioning_for_specification(conditioning_rule, input_requirements, *, source_map=None)
_source_conditioning_map()                                 # live-reads the real ingest writers
```

**Two modules are directly reusable rather than merely copyable.** `warmup.py` and `conditioning.py` are generic — neither has a feature-specific concept in its signature. `ADR-I011` §7.1 requires a conditioning-propagation rule on Label Specifications with the same three cases, and §8.2 requires the same non-defaultable discipline. **Recommendation: import them, do not re-implement them.** Re-implementing would create exactly the registry-pattern drift roadmap `R4` warns about, and would duplicate `INC-0004`'s `sys.path` fix into a second place where it could be got wrong.

**Four things Phase 3 must build itself** (`ADR-I011` §2's asymmetry — no Feature Store analogue exists):
1. The **value maturity lifecycle** (`Declared → Pending → Matured → {Flagged → Adjudicated}`, plus `Void`).
2. The **fill-once guard** — reused from H6, not reimplemented; the Phase-level DoD requires a shared-code-path test proving reuse.
3. **Evidentiary standing** recorded on the label (§10.1).
4. **Maturity requirements** on dataset references (§DI2) — the defining difference from features.

### 6.4 Entry criteria

| # | Criterion | State | Blocking? |
|---|---|---|---|
| 1 | Phase 0 complete — the only hard dependency | ✅ | No |
| 2 | `research/label_store/` does not exist | ✅ Confirmed absent — clean start | No |
| 3 | Registry pattern proven and available | ✅ | No |
| 4 | `ret_fwd1` reserved and structurally guarded | ✅ | No |
| 5 | H6 system unmodified and wrappable | ✅ | No |
| 6 | Three fill conventions identified for WBS 3.3 | ✅ Named in the roadmap's gap ledger | No |
| 7 | Full-suite baselines established | ✅ Both reproduced this session | No |
| 8 | **`FS-DEC-002`** — Phase 2 sign-off | ⏳ **Owed** | **No** — Phase 3 does not depend on Phase 2 |
| 9 | **Commit plan executed** | ⏳ Owed | **Strongly advised** — starting Phase 3 on top of three uncommitted phases compounds `RR-3` |
| 10 | **Decide whether Phase 3 repeats `FS-GAP-003`** | ⚠️ **Open decision** | **Advisory — decide at scoping** |
| 11 | **Decide whether Phase 3 is the first consumer of the admissibility lattice** | ⚠️ **Open decision** | **Advisory — decide at scoping** |

**On criteria 10 and 11 — the two decisions worth making before writing code, not after:**

- **10.** `ADR-I011` §8.3 uses the *same* identity mechanism as `ADR-I010` §8.3 (*"the same mechanism ADR-I010 §8.3 uses for feature bindings"*). Copying Phase 2's pattern faithfully means inheriting `FS-GAP-003` — equivalence demonstrated by test rather than enforced by registration. That may be the right call for a first pass, but it should be **chosen**, not inherited by default and rediscovered at Phase 3's closure review.
- **11.** `ADR-I011` §DI5 requires admissibility to flow dataset-ward as a meet across evidence, features, **and** labels. Phase 3 will meet `FS-GAP-001` from the other side. Either Phase 3 becomes the lattice's first consumer, or the gap widens to two layers instead of one. **The second outcome is worse and is the default if nothing is decided.**

### 6.5 One caution about Phase 4, carried here so it is not discovered late

Phase 4 requires Phases 1, 2, **and** 3 complete. **Phase 1 is DoD-satisfied but not WBS-complete** — `research/ingest/calendar.py` (the WI-5 macro event calendar) still carries no conditioning statement and no normalisation-contract version, unlike the three conformant paths. WBS 1.6 has one path remaining, and it is not Phase 3's work. Recorded here because Phase 3's completion would otherwise be mistaken for "Phase 4 is now unblocked."

---

## 7. Closeout Status

| Item | State |
|---|---|
| WBS 2.1–2.7 | **Complete** |
| Verification (Standard §3–§11, `D1`–`D10`) | **Complete on every milestone** |
| Verification (Standard §12, `D11` — reviewer sign-off) | ⏳ **Owed — `FS-DEC-002`** |
| Closure Review findings | **Resolved**, except `B-1` (owner act) and `B-2` (planned, not executed) |
| `ADR-I010` realisation | **Partial** — 5 requirements unimplemented, now tracked with owners |
| Technical Debt Register | **Delivered** — 21 entries |
| Commit plan | **Delivered** — 8 commits, dependency-verified, not executed |
| Release recommendation | **Delivered** — tag `feature-store-v1.0.0` after sign-off, on commit 8 |
| Phase 3 handoff | **Delivered** — Phase 3 is entry-ready and unblocked |
| Phase 3 | **Not started, per instruction** |

**Phase 2 is engineering-complete and closeout-documented. Formal closure awaits one act: `FS-DEC-002`.**

| Role | Name | Date |
|---|---|---|
| Closeout preparation | Claude Opus 5 | 2026-08-05 |
| **Phase 2 closure authority** | **Rohith Suresh** | **_______________** |
