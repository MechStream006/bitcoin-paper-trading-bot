# Phase 3 (Label Store) — Readiness Review

| Field | Value |
|---|---|
| **Review type** | Pre-implementation readiness review — read-only audit |
| **Subject** | [`docs/engineering/phase_3_label_store_roadmap.md`](../../phase_3_label_store_roadmap.md) v1.0.0 |
| **Audited against** | [`ADR-I011`](../../../architecture/adr/ADR-I011-label-store-architecture.md) v0.1.0 (Draft) · [`implementation_roadmap_v1.md`](../../implementation_roadmap_v1.md) v1.0.1 §6 · [Engineering Verification Standard](../engineering_verification_standard.md) v1.0.0 |
| **Date** | 2026-08-05 |
| **Code changed** | **None.** Documentation only |
| **Authorisation authority** | **Rohith Suresh — pending, see §5** |

> **Independence note.** This review re-derives its conclusions from `ADR-I011` and the repository, not from the roadmap's own claims about itself. **It finds six defects in the roadmap under review**, four of them material. That is the review working, not the roadmap failing — but they must be closed before implementation begins.

---

## 1. Executive Recommendation

> **AUTHORISE PHASE 3 — CONDITIONALLY.** The roadmap's audit is sound, its sequencing is correct, and its three headline findings (`A-1`, `A-2`, §4.1) are load-bearing and independently confirmed. **But the roadmap is not yet implementable as written.** Six defects — chiefly two entirely unmapped `ADR-I011` validation tiers — must be closed in a **v1.1 revision** before Sprint L1 begins.

### 1.1 What the roadmap gets right

Confirmed by independent re-derivation:

- **`A-1` is correct and is the most important finding in either phase so far.** `forward_evidence.db` has zero triggers; fill-once is a `WHERE` clause. `ADR-I011` §12 Tier C requires storage-layer enforcement. The parent roadmap's Phase 3 DoD, read literally, would have shipped a guard the ADR rejects. Catching this before implementation is worth more than everything else in the document.
- **`A-2` is correct.** No next-open convention exists in this repository. Building WBS 3.3 to the ADR's literal list would have registered a fiction.
- **`A-3` is correct.** Four distinct pre-registration instants across six artifacts.
- **§4.1's `bootstrap` + `Context` classification is the right answer** to the hardest question in Phase 3, and it follows a precedent this repository has already used twice rather than inventing one.
- **Sequencing is sound.** L1–L3 touching no real data mirrors how WBS 2.1–2.3 preceded 2.4, and gives three independently revertible sprints with zero data implication.

### 1.2 What must be fixed first

| # | Defect | Severity |
|---|---|---|
| **D-1** | **`ADR-I011` §12 Tier B (Declaration integrity) is never mentioned in the roadmap.** Not in any WBS item, exit criterion, or verification section | **Material** |
| **D-2** | **§12 Tier D (Set hygiene) is 25% covered.** WBS 3.9 covers maturity fraction; **horizon coverage, outcome-distribution/class-imbalance disclosure, and standing composition are unassigned** | **Material** |
| **D-3** | **The roadmap's own gap-recurrence table (§1.3) omits `FS-GAP-002` and `FS-GAP-005`.** It analyses five Phase 2 items and silently skips two — and `FS-GAP-002` is precisely `D-2` | **Material** |
| **D-4** | **§13.4 names backup wiring as "in scope for L4", but no WBS item owns it.** It is real work touching `scripts/backup.py`, `healthcheck.py`, and `src/api/services.py` | **Material** |
| **D-5** | **No WBS item has an explicit owner or per-item entry criterion**, and four items share sprint-level exit criteria, violating the parent roadmap's *"independently mergeable and independently testable"* rule | Moderate |
| **D-6** | **The forward-tier storage-location decision has no identifier or owner** — deferred to "record at L2" with no tracking | Minor |

**None of the six is a design error.** All are coverage and traceability gaps in a planning document, closable by revision without changing a single architectural choice. `D-2` is the one with real downstream consequence: uncorrected, Phase 3 would repeat `FS-GAP-002` exactly, and the resulting `Unowned` bucket in Phase 3's own closure review would be self-inflicted.

---

## 2. WBS Completeness Audit — Owner, Dependency, Entry, Exit

The review objective was to verify **every** WBS item has all four. It does not.

| WBS | Owner | Dependency | Entry criterion | Exit criterion |
|---|---|---|---|---|
| 3.1 | ❌ unassigned | ✅ 0.1–0.3 | ❌ phase-level only | ✅ L1, separable |
| 3.2 | ❌ | ✅ 3.1 | ❌ | ✅ L2, separable |
| 3.3 | ❌ | ✅ 3.1 | ❌ | ✅ L3, separable |
| 3.4 | ❌ | ✅ 3.2, 3.3 | ❌ | ✅ L4, separable |
| 3.5 | ❌ | ✅ 3.4 | ❌ | ⚠️ **shares L5 with 3.9** |
| 3.6 | ❌ | ✅ 3.4, 1.3 | ❌ | ⚠️ **shares L6 with 3.7** |
| 3.7 | ❌ | ✅ 3.1, 3.4 | ❌ | ⚠️ **shares L6 with 3.6** |
| 3.8 | ❌ | ✅ 3.1, 3.6 | ❌ | ✅ L7, separable |
| 3.9 | ❌ | ✅ 3.2, 3.4 | ❌ | ⚠️ **shares L5 with 3.5** |
| *(unnumbered)* | ❌ | — | ❌ | ❌ **backup wiring — `D-4`** |

**Score: 9/9 dependencies · 5/9 separable exit criteria · 0/9 entry criteria · 0/9 owners · 1 unassigned deliverable.**

### 2.1 On "owner" — the honest reading

This is a solo project. Every human owner is Rohith; assigning that per row would be ceremony, not control. **The meaningful ownership question is requirement→WBS mapping**, and that is where the real gaps are (`D-1`, `D-2`, `D-4`). The v1.1 revision should therefore add an **"Owns (ADR-I011 §)"** column rather than a "person" column — naming, for each item, exactly which ADR requirements it discharges. Any requirement not appearing in that column is unowned by construction, which is the property `RR-2` exists to guarantee.

### 2.2 On entry criteria

Phase-level `E1`–`E13` are well-formed. Per-item entry criteria are absent, which matters for exactly three items:

- **3.4** must not begin until production row counts are known (`LR3`) — the local DB is empty and misleading.
- **3.6** must not begin until a *disposable copy* of a revisioned partition exists to simulate a correction against.
- **3.8** must not begin until it is decided whether the Phase 2 backfill is in scope (see `S-3` below) — the answer changes the item's shape.

The other six are adequately gated by their dependencies alone.

---

## 3. Scope Transfer Decision Matrix (E10)

Each of the seven transfers, decided. **Four accepted as proposed, two accepted with amendment, one upheld with amendment.**

| ID | Transfer | Decision | Reasoning |
|---|---|---|---|
| **`S-1`** | Storage-layer immutability becomes a Phase 3 requirement | ✅ **ACCEPT as proposed** | Not discretionary. §12 Tier C states it; `A-1` proves the current guard does not meet it. The argument that `FS-TD-001`'s "single writer, no concurrency" justification does not transfer is correct — a forward label is irreplaceable, a feature Specification is regenerable |
| **`S-2`** | Phase 3 becomes the admissibility lattice's first consumer | ⚠️ **ACCEPT WITH AMENDMENT** | See §3.1 — build **generically and backfill Phase 2**, not label-only |
| **`S-3`** | Registration receipts built in Phase 3 | ⚠️ **ACCEPT WITH AMENDMENT** | See §3.2 — the Phase 2 backfill must be **mandatory**, not "optional" |
| **`S-4`** | Equivalence stays test-enforced (`FS-GAP-003` inherited) | ✅ **UPHOLD, with re-open triggers added** | See §3.3 |
| **`S-5`** | Import `warmup.py` and `conditioning.py` rather than re-implement | ⚠️ **SPLIT** — accept for `conditioning.py`, **drop the `warmup.py` half** | `conditioning.py` is directly required: `ADR-I011` §8.2 mandates a conditioning-propagation rule with the same three cases over the same ingest sources. The `warmup.py` clause is hedged — *"if a maturity-window analogue is wanted"* — and no `ADR-I011` requirement calls for one. **Maturity is elapsed calendar time, not insufficient history**; they are different concepts that happen to both gate a resolution. Carrying a speculative reuse into a roadmap invites a forced fit later |
| **`S-6`** | `ret_fwd1` migrates to the Label Store | ✅ **ACCEPT as proposed** | Constitutionally required (§1.1 Central Asymmetry). Already guarded by an import-time assertion. Trivially correct |
| **`S-7`** | Phase 3 captures host-side execution evidence into the repository | ✅ **ACCEPT, and already correctly hardened** | The roadmap promotes it to `L-D2`, a per-milestone DoD criterion, rather than leaving it as a "should." That is the right strength — P1 Increment 5 remains at `EXECUTED (ATTESTED)` rather than `DEMONSTRATED` for exactly this reason |

### 3.1 `S-2` amendment — build it generically, backfill Phase 2

**Should Phase 3 formally own admissibility inheritance? Yes — but not in the form proposed.**

The roadmap scopes WBS 3.7 to *"compute the §9.2 meet"* for labels. That closes the label half of `FS-GAP-001` and leaves the feature half open. The consequence is worse than it appears: **Phase 4's DI5 requires a meet across evidence, features, *and* labels.** With only the label leg built, Phase 4 must either build the feature leg itself — at a different time, in a different milestone, against a different integration point — or compose a meet with a missing input. That is precisely the registry-pattern drift the parent roadmap's `R4` warns about, arriving through a different door.

**Amendment.** WBS 3.7 builds a **store-agnostic** inheritance module — a function from a set of input admissibility ceilings to a composed ceiling, with no label-specific concept in its signature — and applies it to **both** stores. The Feature Store application is a small additive change to `migrate_evidence_engine_features.py`'s existing per-feature resolution point (the same place warm-up and conditioning already attach), not a redesign.

**Cost:** approximately one additional session on top of 3.7's M sizing. **Benefit:** `FS-GAP-001` — the single highest-severity unowned item in the entire programme, rated **High** and constitutional (IQROS-P4) — closes completely rather than half.

**This is the strongest recommendation in this review.**

### 3.2 `S-3` amendment — make the Phase 2 backfill mandatory

**Should Phase 3 formally own registration receipts? Yes — and more completely than proposed.**

The roadmap's L7 exit criterion says receipts *"**optionally** backfill Phase 2's 17 Specifications."* Optional is the wrong strength, for a reason this repository has already documented.

If `label.register` receipts exist and `feature.register` receipts do not, a future reader of the governance chain encountering a Specification with no receipt **cannot distinguish** "receipts were not required for this object class" from "the receipt is missing — defect." That is the exact ambiguity `CLAUDE.md` §17 records at PR-3→PR-4, where bare-`NULL` on `activated_receipt_id` had to be replaced by an explicit four-state status enum precisely because absence was uninterpretable.

**Amendment.** Split WBS 3.8 into two separately-mergeable items:

| ID | Item | Size |
|---|---|---|
| **3.8a** | `label.register` / `label.adjudicate` receipt emission, generic over subject kind | M |
| **3.8b** | Bootstrap backfill of Phase 2's 17 `feature.register` receipts, closing `FS-GAP-004` in both stores | S |

3.8b is a bootstrap migration of 17 rows using a pattern executed three times already (`migrate_bootstrap_transitions.py`, `migrate_bootstrap_evidence_admission.py`, `migrate_bootstrap_transition_receipts.py`). **Low cost, and it closes the gap rather than halving it.**

### 3.3 `S-4` — upheld, with triggers that were missing

**Should Phase 3 own `FS-GAP-003` (equivalence as a registration precondition)? No.**

Resolving it means registering conformant bindings in **both** stores — real work in two places, for a risk that is already well-controlled in practice: the 5 frozen decision functions are SHA-256 hash-pinned by `pre_reg_guard.verify_fn_hash()` on every forward run, *and* covered by full-suite equivalence tests. What is missing is structural enforcement, not protection.

**But "defer indefinitely" is not a decision, it is an absence of one.** The roadmap's `LR8` says *"re-open when either store next revisits binding identity"* — too vague to ever fire. **Amendment: two concrete triggers, either of which re-opens `FS-GAP-003` as scoped work:**

1. A **sixth** frozen implementation is added to `research/forward_evidence/` (the test-based guarantee scales by author discipline, which is what fails first).
2. Any equivalence test is skipped, quarantined, or marked `xfail` for any reason (the guarantee's entire load-bearing surface).

### 3.4 The remaining Phase 2 unowned gaps

| Gap | Should Phase 3 own it? | Decision |
|---|---|---|
| **`FS-GAP-001`** admissibility inheritance | **Yes — fully, both stores** | §3.1 |
| **`FS-GAP-002`** Tier D output hygiene | **Label side: yes, mandatory — it is `ADR-I011` §12 Tier D and currently unassigned (`D-2`). Feature side: no** | See below |
| **`FS-GAP-003`** equivalence precondition | **No** | §3.3, with triggers |
| **`FS-GAP-004`** registration receipts | **Yes — including the Phase 2 backfill** | §3.2 |
| **`FS-GAP-005`** `corpus_version` / provenance closure | **No — genuinely blocked** | See below |

**On `FS-GAP-002`.** The label-side half is not a transfer at all — it is an `ADR-I011` requirement the roadmap failed to map (`D-2`). §12 Tier D names four checks; WBS 3.9 covers one. The v1.1 revision must expand 3.9 (or add 3.10) to cover **horizon coverage, outcome-distribution disclosure including class imbalance, and standing composition**, all of which *cap admissibility* rather than block — meaning they are cheap to add and directly feed 3.7's meet.

The **feature-side** half should **not** be absorbed. It is Phase 2 work, it is small, and the Phase 2 Closure Review already established it is *"actionable today"* and would either confirm all 17 warm-up declarations exact or find a real off-by-N. **Recommend scheduling it as a standalone item outside Phase 3** rather than letting Phase 3 quietly grow a Feature Store obligation.

**On `FS-GAP-005`.** Phase 3 cannot own it — a real `corpus_version` requires Phase 1 revisioning composed at a Phase 4 dataset boundary. **One binding instruction for Phase 3: do not invent one.** WBS 3.2's research-tier addressing must use the same opaque caller-supplied string Phase 2 uses, and record that the §9.1 addressing guarantee is structurally sound but untested. A locally-invented corpus version would be worse than none — it would look like the real thing and compose incorrectly at Phase 4.

---

## 4. ADR-I011 Conformance Matrix

Every `ADR-I011` requirement mapped to a WBS item under the roadmap **as written** (v1.0.0). Post-revision coverage is shown where this review's amendments change it.

| `ADR-I011` § | Requirement | v1.0.0 coverage | After v1.1 |
|---|---|---|---|
| §1.1 | Research/Forward type distinction | ✅ 3.2 | ✅ |
| §6 | Registry holds Definitions + Specifications, never values | ✅ 3.1 | ✅ |
| §6.1 *(numbered §7.1)* | Registry refusals — horizon, fill, cost, direction, maturity, conditioning | ✅ 3.1 | ✅ |
| §7 | Two tiers split by recomputability | ✅ 3.2 | ✅ |
| §7.1 *(numbered §9.1)* | Addressing — research triple; forward (spec · subject · declaration instant) | ⚠️ **implicit in 3.2, never stated** | ✅ explicit |
| §7.2 *(numbered §9.2)* | Fill-once rule | ✅ 3.2, 3.4 — the roadmap's strongest item | ✅ |
| §7.3 *(numbered §9.3)* | Verification rule; forward labels have nothing to verify against | ✅ 3.2 | ✅ |
| §8.1 | Six object classes | ⚠️ five covered; **Promotion Labels partial** — standing value exists (3.5), use-counting → Phase 4 | ⚠️ unchanged, correctly |
| §8.2 | Nine mandatory Specification fields | ✅ 3.1 | ✅ + calendar-governance field (`LR7`) |
| §8.3 | Conventions become identity | ✅ 3.3, with the `A-2` correction | ✅ |
| §9 *(metadata)* | Value-set metadata | ⚠️ partial — 3.5–3.7 cover standing, conditioning, admissibility; corpus version blocked | ⚠️ `FS-GAP-005` |
| §10.1 | Evidentiary standing recorded, not re-derived | ✅ 3.5, per-signal per `A-3` | ✅ |
| §10.2 | Conditioning + admissibility meet | ✅ 3.7 | ✅ **both stores** |
| §11.1 | Change classification — reconvention, rehorizon, redefinition, input correction | ⚠️ input correction → 3.6; other three **unstated** | ✅ 3.1 |
| §11.2 | Maturation is not a version change | ❌ **not mentioned** | ✅ 3.1 invariant test |
| §11.3 | Superseded-input flagging | ✅ 3.6, Phase 1 dependency verified | ✅ |
| §12.1 | Specification lifecycle | ✅ 3.1 | ✅ |
| §12.2 | Value maturity lifecycle, six states | ✅ 3.2 | ✅ |
| **§12 Tier A** | Specification completeness | ✅ 3.1 | ✅ |
| **§12 Tier B** | **Declaration integrity** — placeholder before horizon opens; declaration instant; subject resolvable; standing at declaration | ❌ **`D-1` — entirely unmapped** | ✅ 3.4 + 3.5 |
| **§12 Tier C** | Maturation integrity | ✅ 3.2, 3.4 | ✅ |
| **§12 Tier D** | **Set hygiene** — maturity fraction, horizon coverage, outcome distribution/class imbalance, standing composition | ⚠️ **`D-2` — 1 of 4 covered** | ✅ 3.9 expanded |
| §13.1 | Governance authority table | ✅ implicit across 3.5–3.8 | ✅ |
| LG1 | Registration is human, emits receipt | ✅ 3.8 | ✅ 3.8a + **3.8b** |
| LG2 | Matured label never rewritten | ✅ 3.2, 3.4 | ✅ |
| LG3 | Standing immutable after declaration | ✅ 3.5 | ✅ |
| LG4 | Void labels and rejections permanent | ✅ 3.2 | ✅ |
| LG5 | Cited Specification never retired | ⏭️ Phase 4 (citation index) | ⏭️ |
| LG6 | Registry never holds values | ✅ 3.1, 3.2 | ✅ |
| LG7 | Promotion Labels use-counted | ⏭️ Phase 4 | ⏭️ |
| LG8 | Horizons declared at registration | ✅ 3.1, §16.1 identity | ✅ |
| §14 | Provenance — ten elements | ⚠️ partial; declaration ≠ maturation instant is a TV5 test, not a WBS item | ⚠️ acceptable |
| §15 DI1–DI8 | Dataset integration | ⏭️ Phase 4 — correctly excluded | ⏭️ |
| §16.1 | Horizon is identity | ✅ 3.1, 3.3 | ✅ |
| §16.2 | Declaration fan-out | ✅ 3.4 | ✅ |
| §16.3 | Partial maturity expressible and refusable | ✅ 3.9 | ✅ |
| §16.4 | Path labels | ⏭️ **deliberately deferred**, with the ADR's own support | ⏭️ |
| §17 | Multi-asset; the calendar trap | ⚠️ risk `LR7` only, not a WBS item | ✅ Tier A field in 3.1 |
| §18 R1–R8 | Risks | ✅ carried into `LR6`, `LR7`, `LR8` | ✅ |

**Coverage: 26 of 38 fully covered as written; 8 partial; 2 uncovered (`D-1`, §11.2); 6 correctly deferred.** After the v1.1 amendments: **34 covered, 3 partial (all blocked on Phase 4 or `FS-GAP-005`), 0 uncovered.**

---

## 5. Phase 3 Risk Assessment

The roadmap's ten risks (`LR1`–`LR10`) are re-assessed independently, plus three this review adds.

| ID | Risk | Roadmap rating | Review rating | Note |
|---|---|---|---|---|
| `LR1` | Triggers on a live collector DB break the daily job | High / Severe | ✅ **Concur** | Mitigation is correct and structural: new tables, observe rather than modify. **The single most important design decision in Phase 3** |
| `LR2` | Retroactive standing launders known outcomes | Moderate / Severe | ⬆️ **Raise likelihood to High** | The roadmap rates it "Moderate — path of least resistance." It is higher: the 148 labels are 100% matured, visibly clean, and *look* like ideal forward evidence. The pressure to classify them as such will be real at L4. `L-D3` is correct but should be a **negative test**, not only a DoD line |
| `LR3` | Production state not observable from dev | Certain / Medium | ✅ **Concur** | Correctly rated certain |
| `LR4` | `forward_evidence.db` / `evidence_forward.db` transposition | Moderate / Severe | ✅ **Concur** | The no-default-`--db` + schema-assert mitigation is right, and correctly declines to rename |
| `LR5` | Third timestamp flake | Moderate / Low-cumulative | ⬆️ **Raise impact to Medium** | Phase 3 is materially more time-dependent than any prior phase — maturation *is* elapsed time. Two flakes already exist from this cause. Injected clock should be a **`BV`-level structural test** (AST guard for `datetime.now()` in maturation paths), not a convention |
| `LR6` | Horizon-as-identity multiplies Specifications | Certain / Low | ✅ **Concur** | Intended, per ADR R1 |
| `LR7` | Calendar trap | Low / Medium | ✅ **Concur; promote to a WBS field** | Making it a mandatory Tier A field while free is correct. It belongs in 3.1's item text, not only in a risk row |
| `LR8` | Inherits `FS-GAP-003` | Certain / Low | ✅ **Concur, with §3.3's triggers** | |
| `LR9` | Scope inflation | Moderate / Medium | ⚠️ **Partially materialised already** | This review's own amendments add 3.8b and expand 3.7 and 3.9. **That is not creep** — each closes a mapped `ADR-I011` requirement or an unowned gap. But it confirms `LR9` is live, and the v1.1 revision should re-state the out-of-scope table rather than let it drift |
| `LR10` | `ADR-I011` is Draft and factually wrong in two places | Confirmed / Medium | ✅ **Concur — and note it materialised twice** | `A-2` and `A-3`. The rule ("build to the verified repository for facts, to the ADR for rules") is the right one |
| **`LR11`** | **[NEW] Two `ADR-I011` validation tiers are unmapped** (`D-1`, `D-2`) | — | **High / Medium** | Uncorrected, Phase 3's closure review produces a self-inflicted `Unowned` bucket — the exact outcome Phase 2's `RR-2` was written to prevent. **Closed by the v1.1 revision** |
| **`LR12`** | **[NEW] Backup wiring for the forward tier is unassigned** (`D-4`) | — | **Medium / Severe** | The forward tier is *irreplaceable from the first row*. `feature_store_backup_strategy.md`'s regenerability argument explicitly does not apply. An unassigned deliverable in a §13 table is the most likely thing to be missed entirely |
| **`LR13`** | **[NEW] Phase 3 begins atop four uncommitted phases** | — | **Moderate / Severe** | `E11` marks the Phase 2 commit plan "strongly advised." This review **raises it to blocking** for any item from L4 onward — see §6 |

---

## 6. Final Implementation Authorisation Recommendation

> **AUTHORISE, subject to three conditions. Two are documentation revisions; one is an act already planned.**

### 6.1 Conditions

| # | Condition | Blocks | Effort |
|---|---|---|---|
| **C-1** | **Revise the roadmap to v1.1**, closing `D-1` through `D-6`: map §12 Tier B and Tier D to WBS items; correct the §1.3 recurrence table to include `FS-GAP-002` and `FS-GAP-005`; assign backup wiring to a numbered item; add an "Owns (`ADR-I011` §)" column; give 3.5/3.9 and 3.6/3.7 separable exit criteria; give the forward-tier storage decision an ID | **L1** | One session |
| **C-2** | **Record the E10 decisions** from §3 of this review — `S-2` and `S-3` amended, `S-5` split, `S-4` upheld with triggers | **L1** | Included in C-1 |
| **C-3** | **Execute the Phase 2 commit plan** before **L4** | **L4 onward** | Already planned; Rohith's act |

**C-1 and C-2 are documentation only and do not require re-authorisation once done.** C-3 is `LR13`: L1–L3 touch no real data and are safe to begin immediately, but L4 writes to a real database — starting that atop four uncommitted phases means a machine loss would take the Feature Store, the Label Store, all ten architecture ADRs, *and* whatever the migration had begun.

### 6.2 The three ownership questions, answered directly

| Question | Answer |
|---|---|
| **Should Phase 3 formally own admissibility inheritance?** | **Yes — and more than proposed.** Build it store-agnostically and apply it to **both** stores, closing `FS-GAP-001` completely. Cost: ~1 session over 3.7's existing M. This is the highest-value single change this review recommends |
| **Should Phase 3 formally own registration receipts?** | **Yes — with the Phase 2 backfill made mandatory**, split as 3.8a/3.8b. Half-closing this gap leaves a governance chain where a missing receipt is uninterpretable, the exact ambiguity PR-4 had to introduce a status enum to resolve |
| **Should Phase 3 own the remaining Phase 2 unowned gaps?** | **Partially, and deliberately.** `FS-GAP-002` **label side — yes, mandatory** (it is an unmapped `ADR-I011` requirement, `D-2`, not a transfer); **feature side — no**, schedule separately. `FS-GAP-003` — **no**, uphold deferral with two concrete re-open triggers. `FS-GAP-005` — **no**, genuinely blocked, with a binding instruction not to invent a local `corpus_version` |

### 6.3 What is authorised, and in what order

| Sprint | Status |
|---|---|
| **L1–L3** (3.1, 3.2, 3.3) | ✅ **Authorised on C-1 + C-2 completion.** Zero real data touched; fully revertible |
| **L4** (3.4) | ⏳ Authorised on C-1, C-2, **C-3**, plus production row counts recorded (`LR3`) |
| **L5–L7** (3.5–3.9) | ⏳ Authorised on L4 completion. `G-PROD` still gates any host-side execution |

### 6.4 What this review does not authorise

- **Phase 4** — unchanged, requires Phases 1, 2, 3 complete, and Phase 1's WBS 1.6 still has `research/ingest/calendar.py` outstanding.
- **Live-collector adoption of the Label Store** (PS5) — `ADR-I011` R8 makes it a separate gated decision, and this review does not open it.
- **Path labels** (§16.4) — correctly deferred; re-opening needs a research question, not an engineering decision.
- **Any change to `ADR-I011`.** Where the ADR is factually wrong about this repository (`A-2`, `A-3`), the roadmap builds to the verified repository and says so. That is the correct handling of a Draft ADR and requires no amendment to it.

---

## 7. Sign-off

| Role | Name | Date |
|---|---|---|
| Readiness review | Claude Opus 5 | 2026-08-05 |
| **Phase 3 authorisation** | **Rohith Suresh** | **_______________** |

**Recommendation: AUTHORISE Phase 3 conditional on C-1, C-2, and (from L4) C-3.** The roadmap's foundations are sound and its three headline audit findings are correct and valuable. The six defects are coverage gaps in a planning document, not design errors, and close in a single revision session.
