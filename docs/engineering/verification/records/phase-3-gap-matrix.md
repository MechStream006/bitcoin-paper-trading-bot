# Phase 3 (Label Store) — `ADR-I011` Gap Matrix

| Field | Value |
|---|---|
| **Title** | `ADR-I011` Realisation Gap Matrix — Phase 3, Label Store |
| **Status** | **Complete — audit artifact** |
| **Version** | 1.0.0 |
| **Document Type** | Engineering audit artifact (not an ADR, not a roadmap) |
| **Governing ADR** | [`ADR-I011`](../../../architecture/adr/ADR-I011-label-store-architecture.md) Label Store Architecture v0.1.0 (**Draft**) |
| **Roadmap** | [`phase_3_label_store_roadmap.md`](../../phase_3_label_store_roadmap.md) v1.1.0 |
| **Companion** | [Phase 3 Closure Review](phase-3-closure-review.md) · [Phase 3 Closeout](phase-3-closeout.md) · [Label Store TD Register](../../label_store_technical_debt_register.md) |
| **Authors** | Rohith Suresh — Owner / Architect · Audited with Claude Opus 5 |
| **Date** | 2026-08-06 |
| **Code written** | **None.** Read-only audit |

> **What this document is.** A clause-by-clause verification of `ADR-I011` against the repository as it actually exists on 2026-08-06 — not against the milestone records' own claims. Every ✅ below was confirmed by reading source, querying a real database, or executing a command in this session. Where a record's claim and the repository disagreed, the repository won and the disagreement is stated.
>
> **What this document is not.** It designs nothing, decides nothing, and edits no roadmap or ADR. It satisfies roadmap §8.2 `P3-12`'s requirement for *"an `ADR-I011` Gap Matrix with an explicit **Unowned** bucket"* — §5 is that bucket.

---

## 0. Verdict Vocabulary

| Verdict | Meaning |
|---|---|
| **REALISED** | The requirement is implemented, tested, and confirmed against the repository this session |
| **REALISED (MECHANISM ONLY)** | The mechanism exists and is tested, but no real data exercises it yet — correct, and named as such |
| **PARTIAL** | Some obligations of the clause are met and others are demonstrably not |
| **DEFERRED (OWNED)** | Not built, deliberately, with a named future owner already recorded |
| **OPEN (UNOWNED)** | Not built, and **no** milestone, phase, or register entry owns it — see §5 |
| **N/A** | The clause imposes no Phase 3 obligation |

---

## 1. Core Architecture — §§1–7

| `ADR-I011` clause | Requirement | Owner | Verdict | Evidence verified this session |
|---|---|---|---|---|
| §1.1 | Two-kind split: research labels compute, forward labels are records | 3.2 | **REALISED** | `value_store.py` has `put/get/evict/resolve_research` (compute-on-read) and a separate materialised `label_value_forward` table |
| §6 | Registry holds Definitions + Specifications; **never** values | 3.1 | **REALISED** | `label_store.db` tables: `label_definitions`, `label_specifications` hold no outcome column; outcomes live only in `label_value_*` |
| §6 | Content addressing, append-only, phase-tracked, storage-level immutability | 3.1 | **REALISED** | `trg_label_definitions_no_update`, `trg_label_definitions_no_delete` present in `sqlite_master` |
| §6.1(7.1) | Registry refuses a Specification missing horizon / fill / cost / direction / maturity rule | 3.1 | **REALISED** | `_validate_tier_a()`; per-field negative tests in `tests/test_label_store_registry.py` |
| §6.1(7.1) | Refuses a Specification whose Definition version does not exist | 3.1 | **REALISED** | Confirmed empirically: `definition_version=99` → `SpecificationRejected`, `Rejected` row persisted |
| §6.1(7.1) | Refuses a Specification without a conditioning-propagation rule | 3.1 | **REALISED** | `CONDITIONING_RULES = {preserving, narrowing, cross-sectional}`; non-member refused |
| §6.1(7.1) | Refuses mutation of any sealed record | 3.1 | **REALISED** | Storage-layer triggers, not application guards |
| §7 | Two tiers split by recomputability, not convenience | 3.2 | **REALISED** | Distinct tables, distinct APIs, distinct retention semantics |
| §7 research tier | Evictable cache, no authority | 3.2 | **REALISED (MECHANISM ONLY)** | `evict_research_value()` exists; **0 rows** in `label_value_research` today |
| §7 forward tier | Indefinite retention; irreplaceable | 3.2, 3.10 | **REALISED** | 148 rows; backup wiring live (§4 below) |
| §7.1(9.1) | Research address = (spec · corpus version · `as_of`) | 3.2 | **REALISED** | `resolve_research()` signature |
| §7.1(9.1) | Forward address = (spec · subject · declaration instant) | 3.2 | **REALISED** | `label_value_forward` PK columns `spec_hash`/`subject_id`/`declared_at` |
| §7.2(9.2) | **Fill-once: a matured forward label is never rewritten** | 3.2, 3.4 | **REALISED** | `trg_label_value_forward_guard` raises `LABEL_TAMPER` on any change to `outcome_json`/`input_revision_set_json`/`matured_at` once state ∈ {Matured, Flagged, Adjudicated} — **storage layer, not application** |
| §7.3(9.3) | Research: stored-vs-recomputed divergence is reported | 3.2 | **REALISED (MECHANISM ONLY)** | `LabelDivergenceEvent` class present; no real research value exists to diverge |
| §7.3(9.3) | Forward: integrity = content hash + storage immutability + append-only maturation | 3.2 | **REALISED** | `_content_hash()` + the guard trigger + write-once instant columns |

---

## 2. Specifications, Metadata, Versioning — §§8–11

| Clause | Requirement | Owner | Verdict | Evidence |
|---|---|---|---|---|
| §8.1 | Six object classes distinguished | 3.1–3.5 | **PARTIAL** | Five realised. **Promotion Labels have no realisation** — no use-counting, no sealed-decision reference (see `LS-GAP-005`) |
| §8.2 | Nine mandatory fields, **no field defaultable** | 3.1 | **REALISED** | All nine present as columns; each has a negative test |
| §8.2 + §17 | Tenth field: horizon governance (calendar trap) | 3.1 (`LR7`) | **REALISED** | `horizon_governance` column; non-member string refused (verified empirically) |
| §8.3 | Conventions become identity — PS1 resolved | 3.3 | **REALISED** | Three `trade_forward_return` specs, three distinct hashes: `0db88b4fc0dd` (C2, turnover cost, signed), `89869c534200` (C1, flat 15bps, long-only), `57ce016e51a0` (C3, flat per-trade, ±1) — two share `horizon_days=7` and still differ, proving convention alone drives identity |
| §9 Definition metadata | Semantic statement, rationale, interpretation, authorship, version history | 3.1 | **REALISED** | `register_definition()` requires every field; blank refused |
| §9 Specification metadata | Everything in §8.2 + lifecycle phase | 3.1 | **REALISED** | `phase` column |
| §9 Specification metadata | **+ registration receipt** | 3.8a | **PARTIAL** | Mechanism realised (`label_receipts.py`); **0 of 8 real Specifications carry one** — `label_store.db` has no `governance_receipts` table at all. See `LS-GAP-001` |
| §9 Value-set metadata | Maturity state and fraction | 3.2, 3.9 | **REALISED** | `maturity_state` column; `set_hygiene.maturity_fraction()` |
| §9 Value-set metadata | Admissibility ceiling (inherited) | 3.7, 3.9 | **REALISED** | `propagation.label_value_ceiling()`, `set_hygiene.set_admissibility_ceiling()` |
| §9 Value-set metadata | Conditioning statement (propagated) | 3.7 | **REALISED** | `propagation.conditioning_for_label_specification()` |
| §9 Value-set metadata | Evidentiary standing | 3.5 | **REALISED** | `standing` column; 148/148 = `Context` |
| §9 Value-set metadata | Input revision set | 3.2, 3.6 | **PARTIAL** | Column exists and is enforced write-once; the 148 real rows hold a **descriptive placeholder string**, not a `raw_lake.py` reference. See `LS-TD-004` |
| §9 Value-set metadata | **Corpus version** | — | **DEFERRED (OWNED)** | `FS-GAP-005`; roadmap §4.3 binding instruction: *do not invent one* |
| §10.1 | Standing recorded, never re-derived — PS6 resolved | 3.5 | **REALISED** | `standing.py` resolves per-signal via `pre_reg_guard.py`; trigger makes it immutable after declaration |
| §10.2 | Conditioning propagates; admissibility is the meet | 3.7 | **REALISED** | `propagation.py` imports `feature_store/conditioning.py` unchanged |
| §10 versioning | Three levels compose upward | 3.1 | **REALISED** | Definition ordinal + Specification content hash + value-set identity |
| §11.1 | Reconvention / Rehorizon / Redefinition / Input correction | 3.3, 3.4, 3.6 | **REALISED** | Rehorizon exercised for real: 5 `spot_one_day_forward_return` specs (h = 1/5/7/14/30) share one Definition |
| §11.2 | **Maturation is not a version change** | 3.1 | **REALISED** | Invariant test: maturation does not alter a Specification hash |
| §11.3 | Superseded-input flagging — PS4 made decidable | 3.6 | **REALISED (MECHANISM ONLY)** | `superseded.find_affected_labels()` / `flag_affected_labels()`, tested against a genuine `raw_lake.py` partition; **no real correction has occurred**, so 0 real rows are `Flagged` |

---

## 3. Lifecycle, Validation, Governance — §§12–14

| Clause | Requirement | Owner | Verdict | Evidence |
|---|---|---|---|---|
| §12.1 | Specification lifecycle Proposed → Registered → Active → {Deprecated, Superseded} → Retired | 3.1 | **REALISED (MECHANISM ONLY)** | `activate_specification()` / `retire_specification()` exist. **All 8 real Specifications sit at `Registered`; none is `Active`.** See `LS-GAP-002` |
| §12.1 | Rejected is a permanent negative record | 3.1 | **PARTIAL** | Verified empirically: vocabulary violations, blank strings, and unresolvable Definition versions **do** persist a `Rejected` row. **`None` on any `NOT NULL` column (TEXT *and* INTEGER) raises correctly but persists no row.** See `LS-TD-002` |
| §12.1 | Retirement requires zero citations | Phase 4 | **DEFERRED (OWNED)** | No citation index until `ADR-I012`; roadmap §4.3 assigns to Phase 4 |
| §12.2 | Six-state maturity lifecycle, storage-enforced | 3.2 | **REALISED** | Trigger encodes exactly the ADR's five legal arcs and refuses all others |
| §12.2 | `Void` recorded permanently | 3.2 | **REALISED** | `void_at`/`void_reason` write-once |
| **§12 Tier A** | Specification completeness | 3.1 | **REALISED** | Ten fields, one negative test each |
| **§12 Tier B** | Placeholder before horizon opens; declaration instant; subject resolvable; **standing at declaration** | 3.4, 3.5 | **REALISED** | Declaration instants are true historical H6 timestamps (AST guard proves no `datetime.now()`); standing immutable by trigger |
| **§12 Tier C** | Maturity rule satisfied; **fill-once at the storage layer**; revision set recorded; append-only | 3.2, 3.4 | **REALISED** | The single strongest result in Phase 3 — adversarial direct-`sqlite3` tests prove no SQL path can rewrite a matured label |
| **§12 Tier D** | Maturity fraction · horizon coverage · outcome distribution incl. class imbalance · standing composition | 3.9 | **REALISED** | All four in `set_hygiene.py`; `maturity_fraction` feeds `Maturity.PARTIAL` into the lattice; the other three are disclosures (`LS-GAP-007`) |
| §12 | **Assert, never repair** | all | **REALISED** | No imputation, horizon extension, back-dating, or standing reassignment exists in any module |
| §13.1 | Label Store governs measurement, never merit or promotion | all | **REALISED** | No promotion logic anywhere in `research/label_store/` |
| **LG1** | Registration is a human act emitting a receipt | 3.8a | **PARTIAL** | `register_specification_with_receipt()` refuses non-human actors; **but the 8 real Specifications were registered before it existed and have no receipts** (`LS-GAP-001`) |
| **LG2** | Matured forward label never rewritten | 3.2 | **REALISED** | Trigger |
| **LG3** | Standing assigned at declaration, immutable thereafter | 3.5 | **REALISED** | Trigger clause naming LG3 explicitly |
| **LG4** | Void labels and rejected Specifications are permanent records | 3.1, 3.2 | **PARTIAL** | Void: realised. Rejected: see §12.1 row above and `LS-TD-002` |
| **LG5** | A cited Specification can never be retired or made unresolvable | Phase 4 | **DEFERRED (OWNED)** | Roadmap §4.3 |
| **LG6** | Registry never holds values; Value Store never issues judgments | 3.1, 3.2 | **REALISED** | Table separation; no gate function in `value_store.py` |
| **LG7** | Promotion Labels are use-counted | Phase 4 | **DEFERRED (OWNED)** | Roadmap §4.3 |
| **LG8** | Horizons declared at registration, never selected at evaluation | 3.1 | **REALISED** | `horizon_days` is a hashed Specification field; no evaluation-time horizon parameter exists |
| §14 provenance | 10 elements | 3.4–3.7 | **PARTIAL** | 8 realised. Corpus revision set is a placeholder (`LS-TD-004`); corpus version deferred (`FS-GAP-005`) |
| §14 | **Declaration instant ≠ maturation instant, both required** | 3.4 | **REALISED** | Separate `declared_at` / `matured_at`, both write-once, both true historical values |

---

## 4. Multi-Horizon, Multi-Asset, Dataset Integration — §§15–17

| Clause | Requirement | Owner | Verdict | Evidence |
|---|---|---|---|---|
| §16.1 | **Horizon is identity, not a parameter** | 3.1, 3.3, 3.4 | **REALISED** | 5 horizons of one Definition = 5 distinct hashes in the real database |
| §16.2 | Declaration fan-out: one placeholder per horizon | 3.4 | **REALISED** | 37 subjects × 4 horizons = 148 real rows |
| §16.3 | **Partial maturity expressible; full-maturity request refused** | 3.9 | **REALISED** | `request_fully_matured()` raises `PartialMaturityRefusalError` naming spec, fraction, and the not-yet-matured subject ids; `admit_partial=True` returns the set with the fraction recorded |
| §16.4 | Path labels are Specifications in their own right | — | **DEFERRED (OWNED)** | Roadmap §4.3, research-question-gated. `mae_mfe_path`'s 222 rows remain unwrapped |
| §17 | Horizon expressed in observation count **and** calendar span; Specification states which governs | 3.1 | **REALISED** | `horizon_governance` mandatory field (`LR7` promoted to Tier A) |
| §17 | Asset supplied at resolution | — | **DEFERRED (OWNED)** | Roadmap §14.1: no multi-asset resolution exists yet |
| §15 **DI1** | Dataset references Specification hashes, never values | Phase 4 | **N/A — Phase 4** | Label side ready: `get_specification(db, hash)` resolves by hash only |
| §15 **DI2** | Dataset declares a maturity requirement per label reference | Phase 4 | **N/A — Phase 4** | Label side ready: `request_fully_matured(admit_partial=…)` is exactly this contract |
| §15 **DI3** | Resolution closed at `as_of` | Phase 4 | **N/A — Phase 4** | Label side ready: `declared_at` / `matured_at` support point-in-time filtering |
| §15 **DI4** | **Only `Active` Specifications may be referenced by a new dataset** | Phase 4 | ⚠️ **BLOCKED** | Label side **not** ready: **zero** Label Specifications are `Active`. See `LS-GAP-002` |
| §15 **DI5** | Admissibility flows dataset-ward as the meet across evidence, features, labels | 3.7 + Phase 4 | **REALISED (LABEL SIDE)** | `propagation.py` supplies both legs — label ceilings *and* all 17 feature ceilings |
| §15 **DI6** | Training Dataset requires matured labels; Research Dataset may admit partial maturity if declared | Phase 4 | **N/A — Phase 4** | Label side ready (§16.3) |
| §15 **DI7** | Bidirectional citation index | Phase 4 | **DEFERRED (OWNED)** | Roadmap §4.3 |
| §15 **DI8** | Promotion Labels use-counted against the sealed decision | Phase 4 | **DEFERRED (OWNED)** | Roadmap §4.3 |

---

## 5. The Unowned Bucket — required by roadmap `P3-12`

> Roadmap §14.1 claimed: *"every `ADR-I011` requirement has a named owner… **Zero are unowned**."* That was true **as planned**. This audit finds it is **no longer true as built** — three items lost their owner during execution, and two more were never owned in the first place. Each is stated with the evidence for the claim.

| # | Unowned item | `ADR-I011` basis | Why it is unowned | Recommended owner |
|---|---|---|---|---|
| **U1** | **The 8 real Label Specifications carry no registration receipt** | LG1, §9 | Roadmap WBS 3.8b scopes the *mandatory* backfill exclusively to Phase 2's 17 `feature.register` receipts. No WBS item names a **label-side** backfill. WBS 3.8a's record names this and correctly declines to invent one | **New WBS 3.8c** — mechanical, ~1 session, mirrors 3.8b exactly |
| **U2** | **No Label Specification is `Active`; DI4 cannot be satisfied** | §12.1, DI4 | WBS 3.3's own TD says *"Not scheduled by name — a natural fit for whichever milestone first needs an `Active` Label Specification (possibly WBS 3.4, not decided here)."* WBS 3.4 did not take it. Nothing since has | **Phase 4, item 4.0** (before 4.3's resolution stage) |
| **U3** | **`label_migration_register.md` was never written** | Roadmap §13.3 (WBS 3.4) | Named as a WBS 3.4 deliverable; WBS 3.4's record does not mention it; zero references exist anywhere in the repository except the roadmap line itself | **Phase 3 closeout follow-up** — Rohith to schedule |
| **U4** | **The Maturity & Standing Contract's Tier D extension was never written** | Roadmap §13.3 (WBS 3.9) | `label_maturity_contract.md` states in its own header: *"3.9 **will extend this document** with Tier D set-hygiene content… do not treat this document as covering Tier D yet."* WBS 3.9 shipped without extending it | **Phase 3 closeout follow-up** — Rohith to schedule |
| **U5** | **`label_policy.yaml` does not exist; §10's named rollback levers therefore do not exist** | Roadmap §10, §13.1 | §10 names four config keys (`standing_enforcement`, `superseded_input_flagging`, `admissibility_enforcement`, `maturity_refusal`). None exists. WBS 3.9's record documents the reasoning honestly and shows 3.5/3.6/3.7 set the same precedent — but the roadmap's stated rollback strategy is consequently unrealised | **Decision, not work** — see `LS-DEC-004` |

**Nothing else is unowned.** Every other gap in §§1–4 carries an owner: a future phase, an existing register entry (`FS-GAP-003`, `FS-GAP-005`), or an explicit roadmap §4.3 deferral.

---

## 6. Requirements That Changed Owner During Execution

Recorded because a silent owner change is how a requirement becomes unowned.

| Requirement | Planned owner (roadmap §14.1) | Actual outcome |
|---|---|---|
| §12 Tier B | 3.4 **+** 3.5 | Honoured as planned — 3.4 declined to assign standing and recorded a binding instruction for 3.5, which executed it |
| The four `trade_labels` horizon Specifications | Implied by 3.3 | **Moved to 3.4.** WBS 3.3's audit (`A-2`) characterised only `trade_exits.net_return`; the actual 148-row table used a different, unregistered convention. 3.4 registered four new Specifications under `ret_fwd1`'s Definition (Rehorizon). Documented in 3.4 §0, correctly |
| `FS-GAP-004` closure "in **both** stores" (`P3-9`) | 3.8a + 3.8b | **Half-delivered.** Feature side closed (17 receipts, chain valid). Label side left open (`U1`) — the exact reverse of the asymmetry `S-3` was written to prevent |
| §10 rollback levers | 3.5, 3.7, 3.9 via `label_policy.yaml` | **Not delivered** (`U5`) |

---

## 7. Audit Method

Every verdict above rests on one of these, performed in this session on 2026-08-06:

1. **Direct source reading** — all seven `research/label_store/` modules, `registry.py`, `value_store.py`, `propagation.py`, `set_hygiene.py`, `label_receipts.py`, `standing.py`, `superseded.py`.
2. **Live database queries** against the real `label_store.db` (8 Specifications, 2 Definitions, 148 forward rows, 0 research rows, 3 triggers, 0 views), the real `feature_store.db` (17 Active Specifications, 17 receipts), and the real `ledger.db` (26 receipts).
3. **Trigger DDL extraction** from `sqlite_master` — the fill-once, LG3, and maturity-arc claims were read from the actual SQL, not from a record's summary of it.
4. **Empirical probes** — five Tier A rejection modes executed against isolated temporary databases to establish exactly which persist a `Rejected` row (§12.1 row, `LS-TD-002`).
5. **Command execution** — `report.py --check` (clean, 35 families), `receipt_ledger.verify_chain()` on both `feature_store.db` (17, ok) and `ledger.db` (26, ok).
6. **Full regression** — `btc_bot`: **2215 passed / 19 skipped / 0 failed**; base env: **1836 passed / 60 skipped / 0 failed**.
7. **Filesystem verification** — deliverable existence checked by path, not by claim; `.gitignore` coverage confirmed for `label_store.db` and both sidecars.

**No file was created, modified, or deleted by this audit** other than this document and its three companions. No database was written to.

---

| Role | Name | Date |
|---|---|---|
| Gap matrix audit | Claude Opus 5 | 2026-08-06 |
| **Owner acceptance** | **Rohith Suresh** | **_______________** |
