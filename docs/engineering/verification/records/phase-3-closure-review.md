# Phase 3 (Label Store) — Closure Review

| Field | Value |
|---|---|
| **Title** | Phase 3 Closure Review — Label Store |
| **Status** | **Complete — recommendation is CONDITIONAL; Phase 4 is NOT authorised** |
| **Version** | 1.0.0 |
| **Document Type** | Engineering closure review (not an ADR, not a roadmap) |
| **Governing ADR** | [`ADR-I011`](../../../architecture/adr/ADR-I011-label-store-architecture.md) v0.1.0 (**Draft**) |
| **Roadmap** | [`phase_3_label_store_roadmap.md`](../../phase_3_label_store_roadmap.md) v1.1.0 |
| **Companion** | [Gap Matrix](phase-3-gap-matrix.md) · [Label Store TD Register](../../label_store_technical_debt_register.md) · [Phase 3 Closeout](phase-3-closeout.md) |
| **Predecessors** | [Phase 2 Closure Review](phase-2-closure-review.md) · [Phase 3 Readiness Review](phase-3-readiness-review.md) |
| **Authors** | Rohith Suresh — Owner / Architect · Reviewed with Claude Opus 5 |
| **Date** | 2026-08-06 |
| **Code written** | **None.** Read-only audit |

---

## 1. Executive Summary

**Phase 3 built what it set out to build.** All eleven WBS items (3.1–3.10, with 3.8 split) shipped with a complete verification record each, every non-reviewer checkbox ticked, and both full regression suites green. Ten of twelve phase-level Definition-of-Done criteria are met outright, verified against the repository in this session rather than accepted from the records.

The engineering quality is high, and one result deserves to be called out: **`ADR-I011` §12 Tier C — the fill-once rule enforced at the storage layer — is realised more strongly than the parent roadmap originally asked for.** The parent roadmap's DoD would have shipped a guard the ADR rejects; the Phase 3 roadmap's `A-1` audit caught that, and the delivered `trg_label_value_forward_guard` trigger now refuses falsification through *any* SQL path, including a direct `sqlite3` session. That is the difference between a documented intention and an enforced invariant.

**Phase 3 is nevertheless not formally closeable today, and Phase 4 is not authorised.** Six conditions stand in the way. None is an engineering defect in delivered code; five are Rohith's own acts, and one is a scoping omission. Specifically:

- **All ten milestone records are unsigned** — the precise failure roadmap §8.1 named and promised not to repeat.
- **Everything from Phases 0–3 exists only as uncommitted working-tree state.** Roadmap gate `C-3`/`E11` was raised to *blocking for L4 onward*; L4–L7 executed anyway.
- **`P3-9` is half-delivered.** `FS-GAP-004` is closed feature-side (17 receipts) and open label-side (0 of 8) — the mirror image of the ambiguity scope transfer `S-3` was written to prevent.
- **Zero Label Specifications are `Active`**, which makes `ADR-I011` DI4 unsatisfiable and blocks Phase 4's own exit criterion.
- **`P3-1` contains an internal inconsistency** with the roadmap's own `LR1` and cannot be claimed clean without a ruling.
- **Three named §13 deliverables were never written.**

Every one of these is small, concrete, and dischargeable. None requires redesign. The recommendation is **CONDITIONAL CLOSURE** (§10).

---

## 2. WBS Completion Matrix

Verified by reading each record and confirming the claimed artifact exists.

| WBS | Item | Record | Non-RC boxes | Artifact verified |
|---|---|---|---|---|
| **3.1** | Label Registry | [`wbs-3.1`](wbs-3.1-label-registry.md) | 37/37 ✅ | `registry.py`; 2 Definitions, 8 Specifications in real DB |
| **3.2** | Label Value Store | [`wbs-3.2`](wbs-3.2-label-value-store.md) | 37/37 ✅ | `value_store.py`; 3 triggers confirmed in `sqlite_master` |
| **3.3** | Convention Specifications | [`wbs-3.3`](wbs-3.3-convention-specifications.md) | 38/38 ✅ | `register_conventions.py`; C1/C2/C3 hashes distinct |
| **3.4** | H6 forward-label migration | [`wbs-3.4`](wbs-3.4-h6-forward-label-migration.md) | 38/38 ✅ | 148 rows, `bootstrap`-attested; H6 source hash unchanged |
| **3.5** | Evidentiary standing | [`wbs-3.5`](wbs-3.5-evidentiary-standing.md) | 41/41 ✅ | `standing.py`; 148/148 = `Context` |
| **3.6** | Superseded-input flagging | [`wbs-3.6`](wbs-3.6-superseded-input-flagging.md) | 38/38 ✅ | `superseded.py`; real `raw_lake.py` integration test |
| **3.7** | Conditioning + admissibility | [`wbs-3.7`](wbs-3.7-conditioning-admissibility.md) | 38/38 ✅ | `propagation.py`; ceilings for all 17 Feature Specs |
| **3.8a** | Label governance receipts | [`wbs-3.8a`](wbs-3.8a-label-governance-receipts.md) | 38/38 ✅ | `label_receipts.py`; replay verified both directions |
| **3.8b** | Phase 2 receipt backfill | [`wbs-3.8b`](wbs-3.8b-feature-receipts-backfill.md) | 38/38 ✅ | 17 receipts in real `feature_store.db`, chain valid |
| **3.9** | Set hygiene (Tier D) | [`wbs-3.9`](wbs-3.9-set-hygiene.md) | 38/38 ✅ | `set_hygiene.py`; all four statistics |
| **3.10** | Backup & durability wiring | [`wbs-3.10`](wbs-3.10-label-store-backup-wiring.md) | 41/41 ✅ | `backup.py --label-store`; drill transcript committed |

**11 of 11 complete. 0 of 11 reviewer-signed** (`LS-DEC-003`).

---

## 3. Definition of Done — Roadmap §8.2

Each criterion independently re-verified in this session.

| # | Criterion | Verdict | Evidence |
|---|---|---|---|
| **P3-1** | Declare-then-fill discipline proven reused via a shared-code-path test **and** enforcement strengthened to the storage layer | ⚠️ **CONDITIONAL** | Second half **fully met** — trigger refuses direct-SQL rewrite. First half not satisfiable as literally worded; `LR1` mandates the architectural separation that makes an H6-shared code path impossible. Requires `LS-DEC-005` |
| **P3-2** | Three conventions → three distinct hashes | ✅ **MET** | `0db88b4fc0dd` / `89869c534200` / `57ce016e51a0`; two share `horizon_days=7` and still differ |
| **P3-3** | 148 labels wrapped, `Context`, `bootstrap`, zero mutation | ✅ **MET** | Queried: 148 rows, 148 `Context`, 148 `Matured`; H6 SHA-256 verified identical across three `--commit` runs |
| **P3-4** | Full-maturity request refused by name, observable via query | ✅ **MET** | `PartialMaturityRefusalError` names spec, fraction, and not-yet-matured subject ids |
| **P3-5** | Simulated correction flags correct labels, neither rewrites nor trusts | ✅ **MET** | WBS 3.6 against a genuine `raw_lake.py` partition |
| **P3-6** | Standing recorded, per-signal, two consumers cannot differ | ✅ **MET** | `standing.py` via `pre_reg_guard.py`; trigger enforces LG3 immutability |
| **P3-7** | Every label set carries conditioning + computed admissibility ceiling | ✅ **MET** | `propagation.py`; `conditioning.py` imported unmodified (diff-proven) |
| **P3-8** | All four Tier D statistics computed and recorded | ✅ **MET** | `set_hygiene.py`; `maturity_fraction` feeds `Maturity.PARTIAL` |
| **P3-9** | `FS-GAP-001` **and** `FS-GAP-004` closed in **BOTH** stores | ⚠️ **HALF-MET** | `FS-GAP-001` ✅ closed both stores. `FS-GAP-004` ✅ feature-side (17/17), ❌ **label-side 0 of 8** — `LS-GAP-001` |
| **P3-10** | Forward tier backed up, freshness-monitored, drill transcript committed | ✅ **MET** | `backup.py`, `healthcheck.py`, `services.py`, `config.py` all wired; `docs/ops/label-store-restore-drill-2026-08-06.md` exists |
| **P3-11** | `report.py --check` clean; `verify_chain()` clean; both suites zero new failures | ✅ **MET** | Run live: check OK (35 families); `verify_chain` ok on `feature_store.db` (17) and `ledger.db` (26); **btc_bot 2215/19/0**, **base 1836/60/0** |
| **P3-12** | Closure Review produced, including Gap Matrix with explicit **Unowned** bucket | ✅ **MET** | This document + [Gap Matrix](phase-3-gap-matrix.md) §5 |

**Score: 10 met, 2 conditional/half-met.**

### 3.1 Phase 3's three additional per-milestone criteria (§8.1)

| # | Criterion | Verdict |
|---|---|---|
| **L-D1** | Every immutability claim proven adversarially at the storage layer | ✅ **MET** — the strongest result in the phase |
| **L-D2** | Host-executed items capture their transcript in-session | ✅ **N/A, satisfied** — `G-PROD` never opened; no Phase 3 item executed against a production host. All real execution was local and transcript-captured |
| **L-D3** | No label assigned `Forward evidence` whose outcome predated its Specification | ✅ **MET** — proven by AST-based negative test, not merely asserted |

---

## 4. Gap Summary

Full detail in the [Gap Matrix](phase-3-gap-matrix.md) and [TD Register](../../label_store_technical_debt_register.md). Summarised by bucket:

| Bucket | Count | Items |
|---|---|---|
| **Realised** | Majority of `ADR-I011` | §§1–17 core: two-tier split, content addressing, Tier A–D validation, six-state lifecycle, horizon-as-identity, standing, conditioning, admissibility, partial-maturity refusal |
| **Realised (mechanism only)** | 5 | Research tier (0 rows); §7.3 divergence; superseded flagging (0 real flags); Specification lifecycle (0 `Active`); `Promotion evidence` standing (unwritable by design) |
| **Deferred (owned)** | 6 | LG5, LG7, DI7, DI8 → Phase 4; §16.4 path labels → research-question-gated; corpus version → `FS-GAP-005` |
| **Open (UNOWNED)** | **5** | `U1` label registration receipts · `U2` no `Active` Specification · `U3` `label_migration_register.md` · `U4` maturity contract Tier D extension · `U5` `label_policy.yaml` / §10 rollback levers |

> **Roadmap §14.1 claimed "Zero are unowned."** That was true as planned. It is **no longer true as built**. Three requirements lost their owner during execution and two were never owned. This is the single most important finding of the review, because an unowned requirement is one nobody will notice is missing.

---

## 5. Technical Debt Review

**6 realisation gaps, 17 technical-debt items, 6 decisions owed** — see the [register](../../label_store_technical_debt_register.md). Concentration by theme:

| Theme | Items | Assessment |
|---|---|---|
| **Receipts / provenance** | `LS-GAP-001`, `LS-TD-002` | The only theme with a *governance* consequence. `LS-TD-002` was verified empirically this session and is **wider than WBS 3.8a recorded** — a `None` on **any** `NOT NULL` column (TEXT and INTEGER) rejects correctly but persists no `Rejected` row, contrary to LG4 / IQROS-P15 |
| **Mechanism-without-data** | `LS-TD-004`, `LS-TD-011`, `LS-TD-017` | Expected at this stage and honestly recorded. `LS-TD-004` is the sharpest: superseded-input flagging is proven correct in test yet **inert on all 148 real rows**, which carry a placeholder revision string |
| **Narrow maps** | `LS-TD-006`, `LS-TD-007` | All fail loud rather than guess. Correct discipline |
| **Durability** | `LS-TD-013`, `LS-TD-014`, `LS-TD-015` | Local backup + drill are live. Off-host is not, and the forward tier is irreplaceable from row one |
| **Deliberate mirrors of Phase 2** | `LS-TD-001`, `LS-TD-003`, `LS-GAP-003` | Consistent with already-reviewed Feature Store decisions. Not new debt |

**No item in this register is an outright defect in delivered behaviour.** The closest is `LS-TD-002`, which loses an audit record without ever admitting bad data.

---

## 6. Process Adherence Review

Phase 3 set itself explicit process standards. Measured against them:

| Standard | Source | Outcome |
|---|---|---|
| One verification record per milestone | §9 | ✅ **11/11** |
| **Reviewer sign-off per milestone, not deferred** | §8.1 | ❌ **0/11.** Phase 2 accumulated five unsigned records and Phase 3 explicitly promised not to repeat it; it accumulated ten |
| TV3 sabotage-and-revert every milestone | §9 | ✅ Present in all eleven, including one honestly reported as non-mechanism before the real one was found (3.3) |
| Injected clock, AST-enforced | §9, `LR5` | ✅ Guard present; **no third timestamp flake was introduced** |
| Adversarial storage-layer immutability | §9 | ✅ Direct-`sqlite3` tests, not application-path tests |
| Both environments, every milestone | §9 REG | ✅ Confirmed live this session |
| Host evidence captured in-session | `S-7`, `L-D2` | ✅ N/A — no host execution occurred |
| **`E11`/`C-3` commit gate before L4** | §7, §15, `LR13` | ❌ **Crossed undischarged.** L4–L7 executed on an uncommitted tree |
| Warn-modes write to a queryable table | §10 | ✅ N/A — no warn-mode was built (see `LS-DEC-004`) |

**Two process failures, both Rohith-dischargeable, both foreseen and named in advance by the roadmap itself.** That the roadmap predicted them and they happened anyway is the useful lesson for Phase 4: a gate that only a document enforces is not a gate.

---

## 7. Architectural Integrity Assessment

| Question | Finding |
|---|---|
| Was any architecture designed, amended, or invented during Phase 3? | **No.** Every object, field, rule, and refusal traces to a numbered `ADR-I011` section. Two milestones (3.4, 3.9) faced genuine reconciliations and both resolved them *within* the ADR's own rules — 3.4 by applying §11.1 Rehorizon, 3.9 by declining to invent a lattice dimension |
| Was any `docs/architecture/` file touched? | **No.** Verified by `git status`: no modification under `docs/architecture/` |
| Did the Label Store improperly couple to the Feature Store? | **No.** `I8` honoured — `research/label_store/` imports neither `feature_store/registry.py` nor `value_store.py`. It imports `conditioning.py` (`I2`, sanctioned) and reads `feature_specifications` for ceiling computation (`I9`, authorised) |
| Was `conditioning.py` modified? | **No** — diff-proven in WBS 3.7 |
| Was `research/evidence/engine.py` modified? | **No** (`I5` honoured) |
| Was H6 modified? | **No.** `forward_evidence.db` SHA-256 verified identical before and after all three `--commit` runs. `LR1`'s central mitigation held completely |
| Did receipt-taxonomy extension follow the sanctioned pattern? | **Yes** — three additive `DECISION_TAXONOMY` rows, mirroring PR-7's precedent; no existing row relaxed |
| Is the operational/research boundary intact? | **Yes.** No `src/` module imports anything from `research/`; the backup/healthcheck wiring reads SQLite files generically |

**Architectural verdict: clean.** Phase 3 is a conforming realisation of `ADR-I011`, not a reinterpretation of it.

---

## 8. Risk Review

Roadmap §11's thirteen risks, re-rated post-delivery:

| ID | Risk | Outcome |
|---|---|---|
| `LR1` | Triggers on a live collector DB break the daily job | ✅ **Avoided by design.** New tables only; H6 untouched and still running |
| `LR2` | Retroactive standing launders 148 outcomes into forward evidence | ✅ **Held.** 148/148 `Context`; AST test proves no code path can write `Forward evidence` |
| `LR3` | Production state not observable from dev | ⚪ **Did not bite** — no host execution occurred |
| `LR4` | `forward_evidence.db` / `evidence_forward.db` transposition | ✅ **Held.** No default `--db` anywhere; `LS-DEC-001` chose co-location, adding no third confusable name |
| `LR5` | A third timestamp flake | ✅ **Avoided.** AST guard; suites deterministic in both environments |
| `LR6` | Horizon multiplicity | ✅ **Accepted and visible** — 8 Specifications for 2 Definitions, exactly as intended |
| `LR7` | The calendar trap | ✅ **Closed** — `horizon_governance` is a mandatory Tier A field |
| `LR8` | Inherits `FS-GAP-003` | ⚪ **Inherited as decided.** Neither re-open trigger fired |
| `LR9` | Scope inflation | ✅ **Controlled.** No item exceeded its mapped requirement; §4.3's exclusions all held |
| `LR10` | ADR is Draft and factually wrong in two places | ⚪ **Materialised a third time** (WBS 3.4's `trade_labels` convention). Handled correctly — repository for facts, ADR for rules |
| `LR11` | Unmapped validation tiers | ✅ **Closed** — all four tiers realised |
| `LR12` | Forward-tier backup unassigned | ✅ **Closed** — WBS 3.10 shipped before the first irreplaceable row |
| `LR13` | **Phase 3 begins atop uncommitted phases** | 🔴 **MATERIALISED AND UNRESOLVED.** The gate was crossed; the exposure has now grown by a whole phase |

**Twelve of thirteen risks were controlled. The thirteenth is the open blocker.**

---

## 9. Remaining Risks Into Phase 4

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| **1** | Four phases of work exist on one machine with no version control | 🔴 **Critical** | `LS-DEC-006` — execute the commit plan |
| **2** | Phase 4 begins, reaches its resolution stage, and discovers no Label Specification is citable | **High** | `LS-GAP-002` — scope a Phase 4 item 4.0 before 4.3 |
| **3** | Ten unsigned records become permanent, repeating Phase 2's `FS-DEC-002` at twice the scale | **High** | `LS-DEC-003` |
| **4** | Phase 4 composes DI5's meet and finds every real ceiling capped at `UNKNOWN` | **Medium** | `LS-TD-011` — expected and honest, but should be a known input to Phase 4 planning, not a surprise |
| **5** | Superseded-input flagging never fires because no real label carries a real revision reference | **Medium** | `LS-TD-004` |
| **6** | Forward tier grows while off-host replication stays unwired | **Medium** | `LS-TD-013` |

---

## 10. Formal Recommendation

### 10.1 Phase 3 closure

**CONDITIONAL CLOSURE — RECOMMENDED.**

Phase 3's engineering is complete and of high quality. Ten of twelve DoD criteria are met outright and verified independently. The two that are not (`P3-1`, `P3-9`) are a wording inconsistency and a half-delivered backfill respectively — neither is a defect in shipped behaviour.

Phase 3 may be declared **closed** once these four conditions are discharged:

| # | Condition | Type | Effort |
|---|---|---|---|
| **C3-1** | Reviewer sign-off on all eleven records (`LS-DEC-003`) | Rohith's act | Review time |
| **C3-2** | Rule on `P3-1`'s reading (`LS-DEC-005`) | Rohith's act | One decision |
| **C3-3** | Rule on the `label_policy.yaml` rollback strategy (`LS-DEC-004`) | Rohith's act | One decision |
| **C3-4** | Schedule or explicitly waive the three missing §13 deliverables (`LS-GAP-006`) | Rohith's act | One decision |

### 10.2 Phase 4 authorisation

# ⛔ PHASE 4 IS **NOT** AUTHORISED

Two conditions block it, over and above Phase 3's own closure conditions:

| # | Blocker | Why it blocks Phase 4 specifically |
|---|---|---|
| **B-1** | **Phases 0–3 are uncommitted** (`LS-DEC-006`) | Phase 4 is *"a hard join point — it consumes all three"* prior phases. Building it on an unversioned base means no revert point, no bisect, no diff-based review, and total loss on one disk failure. The roadmap itself made this gate blocking for L4 and it was crossed once already; crossing it again at a larger join point compounds an already-materialised Severe risk |
| **B-2** | **Zero `Active` Label Specifications** (`LS-GAP-002`) | `ADR-I011` DI4 permits only `Active` Specifications in a new dataset. Phase 4's own exit criterion requires a real Research Dataset and a real Training Dataset to seal against real Phase 1–3 data. A conforming Dataset Builder must refuse every label reference today, so Phase 4 cannot meet its DoD. This needs a *scoped owner and a design decision on label-binding equivalence* — not a silent absorption into item 4.3 |

**Strongly recommended before Phase 4, though not strictly blocking:** close `LS-GAP-001` (label-side registration receipts) as a new **WBS 3.8c**. It is ~1 session, mechanically identical to the already-proven 3.8b, and closing it *before* Phase 4 begins citing Specifications avoids Phase 4 inheriting a provenance gap in its very first sealed artifact.

### 10.3 What is explicitly **not** recommended

- **Do not** weaken `LR1` to satisfy `P3-1`'s literal wording. The architectural separation is correct; the wording is what needs a ruling.
- **Do not** invent a `corpus_version` (roadmap §4.3's binding instruction stands).
- **Do not** add a new `ADR-I014` lattice dimension for the three disclosure-only Tier D statistics without a governance session.
- **Do not** retrofit `Forward evidence` standing onto the 148 bootstrap rows under any circumstances. `LR2` held; it must keep holding.

---

## 11. Audit Method

Read-only. Every finding rests on direct source reading, live database queries against the three real databases, trigger DDL extracted from `sqlite_master`, empirical probes against isolated temporary databases, live command execution (`report.py --check`, `verify_chain()`), full regression in both environments, and filesystem verification of deliverables by path rather than by claim. Full method: [Gap Matrix §7](phase-3-gap-matrix.md#7-audit-method).

**No source file, test, database, or configuration was created, modified, or deleted by this review** — only this document and its three companions. No commit was made.

---

| Role | Name | Date |
|---|---|---|
| Closure review | Claude Opus 5 | 2026-08-06 |
| **Phase 3 closure sign-off** | **Rohith Suresh** | **_______________** |
| **Phase 4 authorisation** | **Rohith Suresh** | **_____ WITHHELD pending B-1, B-2 _____** |
