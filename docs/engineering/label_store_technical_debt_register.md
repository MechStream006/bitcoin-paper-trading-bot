# Label Store — Technical Debt Register

| Field | Value |
|---|---|
| **Title** | Technical Debt Register — Label Store (`research/label_store/`) |
| **Status** | **Active — living register** |
| **Version** | 1.0.0 |
| **Document Type** | Engineering Process Register (not an ADR, not an architecture document) |
| **Opened** | 2026-08-06, at Phase 3 Closeout |
| **Governing ADR** | [`ADR-I011`](../architecture/adr/ADR-I011-label-store-architecture.md) v0.1.0 (**Draft**) |
| **Companion** | [Phase 3 Closeout](verification/records/phase-3-closeout.md) · [Phase 3 Closure Review](verification/records/phase-3-closure-review.md) · [Gap Matrix](verification/records/phase-3-gap-matrix.md) · [Feature Store TD Register](feature_store_technical_debt_register.md) · [Hazard Register](hazard_register.md) |
| **Authors** | Rohith Suresh — Owner / Architect · Drafted with Claude Opus 5 |

---

## 0. Purpose and Boundaries

This register records what Phase 3 **did not** build, so that absence stays visible and does not decay into a false assumption of completeness. It mirrors the [Feature Store TD Register](feature_store_technical_debt_register.md)'s structure and ID discipline exactly.

**Three categories, deliberately separated:**

| Category | ID prefix | Meaning |
|---|---|---|
| **Realisation gap** | `LS-GAP-nnn` | An `ADR-I011` requirement with no owning implementation. The architecture says it; the repository does not do it |
| **Technical debt** | `LS-TD-nnn` | An implementation is present but narrower, weaker, or more fragile than it should eventually be |
| **Decision owed** | `LS-DEC-nnn` | A choice only Rohith can make, blocking or shaping future work |

**Not in this register:** anything `ADR-I011` explicitly defers (§16.4 path labels), anything the roadmap assigns to Phase 4 with a named owner (LG5, LG7, DI7, DI8), and anything already tracked in the Feature Store register (`FS-GAP-003`, `FS-GAP-005`) or `CLAUDE.md` §19 (`TD-P1-002`). Those are cross-referenced, never duplicated.

**Every entry below was verified against the repository on 2026-08-06**, not inherited from a milestone record's claim. Where a record understated a gap, this register states the wider finding and says so.

---

## 1. Register — Summary

### 1.1 Realisation gaps

| ID | Gap | `ADR-I011` basis | Severity | Owner |
|---|---|---|---|---|
| [`LS-GAP-001`](#ls-gap-001--the-8-real-label-specifications-carry-no-registration-receipt) | The 8 real Label Specifications carry no registration receipt | LG1, §9 | **High** | **Unowned** — proposed WBS 3.8c |
| [`LS-GAP-002`](#ls-gap-002--no-label-specification-is-active) | No Label Specification is `Active`; DI4 unsatisfiable | §12.1, DI4 | **High** | **Unowned** — proposed Phase 4 item 4.0 |
| [`LS-GAP-003`](#ls-gap-003--label-bindings-are-declared-identity-only-and-do-not-resolve) | Label bindings are declared identity only; none resolves | §8.2 binding | Medium | Inherits `FS-GAP-003` (`S-4`) |
| [`LS-GAP-004`](#ls-gap-004--promotion-labels-have-no-realisation) | Promotion Labels have no realisation | §8.1, LG7, DI8 | Medium | Phase 4 (owned) |
| [`LS-GAP-005`](#ls-gap-005--tier-ds-three-non-maturity-statistics-have-no-lattice-consequence) | Three of four Tier D statistics have no lattice consequence | §12 Tier D | Low | Requires `ADR-I014` amendment |
| [`LS-GAP-006`](#ls-gap-006--three-phase-3-documentation-deliverables-were-never-written) | Three Phase 3 documentation deliverables were never written | Roadmap §13.1, §13.3 | Medium | **Unowned** — Rohith to schedule |

### 1.2 Technical debt

| ID | Debt | Severity | Closed by |
|---|---|---|---|
| [`LS-TD-001`](#ls-td-001--specification-phase-transitions-are-python-guarded-not-trigger-guarded) | Specification phase transitions are Python-guarded, not trigger-guarded | Low | Not scheduled (mirrors `FS-TD-001`) |
| [`LS-TD-002`](#ls-td-002--a-null-on-any-not-null-column-rejects-without-persisting-a-rejected-row) | A `None` on any `NOT NULL` column rejects without persisting a `Rejected` row | **Medium** | Not scheduled — needs its own fix |
| [`LS-TD-003`](#ls-td-003--the-value-store-never-resolves-spec_hash-against-the-registry) | Value Store never resolves `spec_hash` against the Registry | Low | Not scheduled (mirrors Feature Store) |
| [`LS-TD-004`](#ls-td-004--the-148-real-labels-input-revision-set-is-a-placeholder-string) | The 148 real labels' `input_revision_set` is a placeholder string | Medium | Whichever milestone first declares real `raw_lake`-backed labels |
| [`LS-TD-005`](#ls-td-005--five-pre-existing-test-files-lack-the-sysmodules-loader-guard) | Five pre-existing test files lack the `sys.modules` loader guard | Low | Opportunistic |
| [`LS-TD-006`](#ls-td-006--only-the-h6--subject-prefix-resolves-to-a-signal) | Only the `h6-` subject prefix resolves to a signal | Low | First non-H6 forward-tier wrap |
| [`LS-TD-007`](#ls-td-007--only-five-label-binding-sources-are-mapped) | Only five label binding sources are mapped | Low | First new real binding |
| [`LS-TD-008`](#ls-td-008--bootstrap-standing-backfill-is-scoped-by-null-not-by-bootstrap-marker) | Bootstrap standing backfill is scoped by `NULL`, not by bootstrap marker | Low | Not scheduled |
| [`LS-TD-009`](#ls-td-009--no-re-flagging-path-for-an-already-adjudicated-label) | No re-flagging path for an already-`Adjudicated` label | Low | Needs an ADR-level decision |
| [`LS-TD-010`](#ls-td-010--admissibility-ceilings-are-never-persisted) | Admissibility ceilings are never persisted | Low | Phase 4, plausibly |
| [`LS-TD-011`](#ls-td-011--no-gauntlet-certification-exists-for-three-of-four-real-sources) | No gauntlet certification for three of four real sources — every real ceiling caps at `UNKNOWN` | Medium | Own milestone |
| [`LS-TD-012`](#ls-td-012--class-imbalance-disclosure-is-sign-based-only) | Class-imbalance disclosure is sign-based only | Low | First categorical outcome |
| [`LS-TD-013`](#ls-td-013--label_storedb-has-no-off-host-replication) | `label_store.db` has no off-host replication | Medium | Re-evaluate post-Phase-4 |
| [`LS-TD-014`](#ls-td-014--td-p1-002-wal-aware-restore-also-applies-to-label_storedb) | `TD-P1-002` WAL-aware restore also applies to `label_store.db` | Medium | `CLAUDE.md` §19 (existing) |
| [`LS-TD-015`](#ls-td-015--the-quarterly-production-drill-does-not-cover-label_storedb) | Quarterly production drill does not cover `label_store.db` | Low | Host-side activation |
| [`LS-TD-016`](#ls-td-016--pr-8s-migration-script-retains-the-unconditional-init_db-pattern) | PR-8's migration script retains the unconditional `init_db()` pattern | Low | Not scheduled |
| [`LS-TD-017`](#ls-td-017--the-research-tier-holds-zero-values) | The research tier holds zero values — §7.3 divergence never exercised | Low | First real research-label consumer |

### 1.3 Decisions owed

| ID | Decision | Blocking? | Owner |
|---|---|---|---|
| `LS-DEC-001` | Forward-tier storage location | ✅ **RESOLVED** 2026-08-06 — co-located. *Brief header is stale; see `LS-GAP-006`* | Rohith |
| `LS-DEC-002` | Whether the multi-signal collector adopts the Label Store (PS5) | No — out of Phase 3 scope | Rohith + `G-PROD` |
| [`LS-DEC-003`](#ls-dec-003--reviewer-sign-off-on-ten-phase-3-verification-records) | Reviewer sign-off on ten Phase 3 verification records | ⚠️ **Blocks Phase 3 formal closure** | Rohith |
| [`LS-DEC-004`](#ls-dec-004--ratify-or-replace-the-roadmaps-label_policyyaml-rollback-strategy) | Ratify or replace the roadmap's `label_policy.yaml` rollback strategy | No | Rohith |
| [`LS-DEC-005`](#ls-dec-005--ratify-the-p3-1-shared-code-path-test-reading) | Ratify the `P3-1` "shared-code-path test" reading | ⚠️ **Blocks a clean DoD claim** | Rohith |
| [`LS-DEC-006`](#ls-dec-006--commit-phases-03-to-version-control) | Commit Phases 0–3 to version control | 🔴 **Blocks Phase 4** | Rohith |

---

## 2. Realisation Gaps — Detail

### `LS-GAP-001` — The 8 real Label Specifications carry no registration receipt

**Requirement.** `ADR-I011` LG1: *"Registration is a **human act** emitting a receipt (IQROS-P8, P10)."* §9 lists *"registration receipt"* as part of Specification metadata.

**State.** The mechanism is fully built and tested — `label_receipts.register_specification_with_receipt()` emits a chained, replayable `label.register` receipt and refuses non-human actors. But the real `label_store.db` **has no `governance_receipts` table at all**; all 8 Specifications (WBS 3.3's four, WBS 3.4's four horizon variants) were registered before 3.8a existed. `validate_label_governance()` correctly reports `LREC1` for all 8 when run against a copy.

**Why it is unowned.** Roadmap WBS 3.8b scopes the mandatory backfill *exclusively* to Phase 2's 17 `feature.register` receipts (`FS-GAP-004`). No WBS item names a label-side backfill. WBS 3.8a's record identified this precisely and declined to invent one, correctly, under its own "do not begin 3.8b" instruction.

**Why it matters more than it looks.** Scope transfer `S-3`'s stated justification was: *"If labels emit receipts and features do not, a reader encountering a receipt-less Specification cannot distinguish 'not required' from 'missing — defect'."* The delivered state is that ambiguity **with the stores reversed** — features have all 17, labels have none. The gap `S-3` was written to prevent exists, pointing the other way.

**Blast radius.** No incorrect data. Incomplete §14 provenance for every Label Specification Phase 4 will cite.

**Recommended closure.** A new **WBS 3.8c**, mechanically identical to 3.8b: `migrate_bootstrap_label_receipts.py`, `attestation="bootstrap"`, dry-run/`--commit`-gated, idempotent. ~1 session. The precedent is exact and already proven four times.

---

### `LS-GAP-002` — No Label Specification is `Active`

**Requirement.** `ADR-I011` §12.1 and **DI4**: *"Only **Active** Specifications may be referenced by a **new** sealed dataset."*

**State.** Verified by query: all 8 Label Specifications sit at phase `Registered`. Zero are `Active`. By contrast all 17 Feature Specifications are `Active`.

**Why it is unowned.** WBS 3.3's own TD entry says: *"Not scheduled by name — a natural fit for whichever milestone first needs an `Active` Label Specification (possibly WBS 3.4, not decided here)."* WBS 3.4 did not take it, and nothing since has.

**Why activation was not trivial.** `activate_specification()` requires a caller-supplied `equivalence_check` callable, and `LS-GAP-003` means no label binding actually resolves — so activation cannot currently be demonstrated the way the Feature Store demonstrated it.

**Blast radius.** **Direct and immediate for Phase 4.** The parent roadmap's Phase 4 exit criterion is *"at least one real Research Dataset and one real Training Dataset seal successfully against real Phase 1–3 data."* With zero `Active` Label Specifications, a conforming Dataset Builder must refuse every label reference. Phase 4 cannot meet its own DoD until this is closed.

**Recommended closure.** A **Phase 4 item 4.0**, ordered before 4.3's resolution stage: decide and implement what equivalence means for a label binding, then activate the Specifications that pass. This is genuine design work, not a mechanical backfill — which is precisely why it should be scoped explicitly rather than absorbed silently into 4.3.

---

### `LS-GAP-003` — Label bindings are declared identity only and do not resolve

**Requirement.** `ADR-I011` §8.2: a Specification carries *"exactly one binding"* — implementation identity + engine version.

**State.** `binding_id` strings (e.g. `forward_evidence.daily_job.trade_labels`) are stored and hashed into Specification identity, but `get_binding()` resolves none of them. Equivalence is test-enforced, never registration-enforced.

**Disposition.** **Deliberately inherited**, per roadmap scope transfer `S-4`, which upheld `FS-GAP-003` with two concrete re-open triggers (`LR8`): a sixth frozen implementation appearing in `research/forward_evidence/`, or any equivalence test being skipped/quarantined/`xfail`ed. Neither has fired.

**Cross-reference.** [`FS-GAP-003`](feature_store_technical_debt_register.md#fs-gap-003--binding-equivalence-is-test-enforced-not-registration-enforced). **Note the coupling:** closing `LS-GAP-002` will likely force a decision on this one.

---

### `LS-GAP-004` — Promotion Labels have no realisation

**Requirement.** `ADR-I011` §8.1 names Promotion Labels as one of six object classes; LG7 and DI8 require use-counting against a sealed decision.

**State.** `STANDING_VALUES` includes `Promotion evidence`, and the trigger will enforce its immutability — but no code path can ever write it, no use-count exists, and no sealed decision exists to count against. All 148 real labels are `Context`.

**Disposition.** **Owned by Phase 4**, per roadmap §4.3, and correctly so: use-counting is *against a sealed decision*, which does not exist until the Dataset Builder ships. Recorded here only so the object class's absence stays visible.

---

### `LS-GAP-005` — Tier D's three non-maturity statistics have no lattice consequence

**Requirement.** `ADR-I011` §12 Tier D: the four set-hygiene checks *"**cap admissibility** rather than blocking."*

**State.** Only `maturity_fraction` maps to a lattice dimension (`set_admissibility_ceiling()` derives `Maturity.PARTIAL`/`FULL`/`UNKNOWN`). `horizon_coverage`, `outcome_distribution`, and `standing_composition` are computed and reported but cap nothing.

**Assessment — this is defensible, not a defect.** `ADR-I014`'s five-dimension lattice (Provenance, Certification, Readiness, Maturity, Integrity) has **no** dimension corresponding to horizon coverage, outcome shape, or standing mix. Inventing one would be an architecture change outside any milestone's authority, and the ADR's own §15 diagram annotates only *"maturity fraction → caps admissibility."* Reading the other three as disclosures is the reading the ADR's own diagram supports.

**Closure.** Would require an `ADR-I014` amendment — a governance decision, not engineering work. Phase 4 can consult the three disclosures directly.

---

### `LS-GAP-006` — Three Phase 3 documentation deliverables were never written

**Requirement.** Roadmap §13.1 and §13.3 name each with an owning WBS item.

| Deliverable | Roadmap ref | Owner | State |
|---|---|---|---|
| `docs/engineering/label_migration_register.md` | §13.3 | WBS 3.4 | **Never created.** No reference exists anywhere in the repository except the roadmap line itself |
| Tier D extension of `label_maturity_contract.md` | §13.3 | WBS 3.9 | **Never written.** The document's own header states *"3.9 **will extend this document** with Tier D set-hygiene content… do not treat this document as covering Tier D yet"* — still literally true after 3.9 shipped |
| `research/label_store/label_policy.yaml` | §13.1 | WBS 3.5, 3.7, 3.9 | **Never created.** Consequently the four rollback config keys named in roadmap §10 (`standing_enforcement`, `superseded_input_flagging`, `admissibility_enforcement`, `maturity_refusal`) do not exist |

**Assessment.** The first two are straightforward omissions. The third was a *reasoned* omission — WBS 3.9's record documents it explicitly and shows 3.5/3.6/3.7 set the same precedent by satisfying "non-blocking by default" structurally rather than by configuration. But the roadmap's §10 rollback strategy is stated in terms of those keys, so as written it is unrealised. That makes it a decision (`LS-DEC-004`), not merely a missing file.

**Blast radius.** No runtime effect. A future reader following roadmap §10 to disable a Phase 3 enforcement will find no lever where one is documented.

---

## 3. Technical Debt — Detail

### `LS-TD-001` — Specification phase transitions are Python-guarded, not trigger-guarded

`_transition_phase()` enforces the allowed-arc table in Python with a `WHERE`-clause concurrency check. Value maturity transitions, by contrast, *are* trigger-guarded. Mirrors the Feature Store's identical, already-reviewed decision ([`FS-TD-001`](feature_store_technical_debt_register.md#fs-td-001--phase-transitions-are-python-guarded-not-trigger-guarded)); single writer, no concurrent-writer scenario exists. **Low** — the Python guard refuses concurrent and stale-phase writes correctly, and is tested.

### `LS-TD-002` — A `None` on any `NOT NULL` column rejects without persisting a `Rejected` row

**Verified empirically this session** across five rejection modes against isolated databases:

| Rejection mode | Raises | `Rejected` row persisted |
|---|---|---|
| `horizon_governance='nonsense'` (vocabulary) | `SpecificationRejected` | ✅ 1 |
| `fill_convention='  '` (blank string) | `SpecificationRejected` | ✅ 1 |
| `definition_version=99` (unresolvable) | `SpecificationRejected` | ✅ 1 |
| `horizon_days=None` (`NOT NULL INTEGER`) | `SpecificationRejected` | ❌ **0** |
| `fill_convention=None` (`NOT NULL TEXT`) | `SpecificationRejected` | ❌ **0** |

**Cause.** A `None` reaches the `INSERT` and trips `sqlite3.IntegrityError`, which `register_specification()`'s exception handler mis-attributes to its "duplicate content hash — idempotent no-op" branch. It rolls back silently and re-raises `SpecificationRejected`, so the caller is correctly refused but **no permanent negative record survives**.

**Why this is a governance gap, not a cosmetic one.** `ADR-I011` LG4 and IQROS-P15 both require rejected Specifications to be permanent records. For this class of rejection they are not.

**Wider than previously recorded.** WBS 3.8a's TD entry identified this for `horizon_days` (INTEGER) only. This audit confirms it affects **any** `NOT NULL` column, TEXT included.

**Blast radius. Medium.** No invalid Specification is ever admitted — the refusal itself is correct and fail-loud. The loss is audit-trail completeness, plus one narrow consequence: a receipt replay for such a rejection would fail with a clear `LabelReceiptError`. No such receipt exists today.

### `LS-TD-003` — The Value Store never resolves `spec_hash` against the Registry

No import, no foreign key, no existence check. Mirrors `feature_store/value_store.py`'s reviewed decision (*"this module holds no binding-resolution logic of its own"*). Callers are expected to resolve first. **Low** — WBS 3.4's migration does resolve correctly.

### `LS-TD-004` — The 148 real labels' `input_revision_set` is a placeholder string

H6's `corpus_daily` predates Phase 1's revisioning, so the wrapped rows carry a descriptive string rather than a real `raw_lake.py` revision reference. WBS 3.6's structured-reference format and detection logic are real and tested against a genuine `raw_lake.py` partition — **but no real label uses the format**, so superseded-input flagging cannot fire on any existing row. **Medium** — this is the one place where a mechanism proven correct in test is inert on all real data.

### `LS-TD-005` — Five pre-existing test files lack the `sys.modules` loader guard

`test_label_store_registry.py`, `test_label_store_propagation.py` and three others load sibling modules via `importlib` without checking `sys.modules` first, so whichever file is collected last silently replaces the cached copy. Exposed by WBS 3.8a's cross-module exception-identity assertions and worked around entirely within the new test files. **Low** — latent test-isolation fragility only; the full suite passes deterministically in both environments today.

### `LS-TD-006` — Only the `h6-` subject prefix resolves to a signal

`standing._SUBJECT_PREFIX_TO_SIGNAL` maps only `h6-`. An unregistered prefix raises `UnknownSubjectError` rather than mis-resolving. **Low** — fail-loud by construction; registering `g1-`/`g3-`/`h5-`/`ivrv-` now would be speculative.

### `LS-TD-007` — Only five label binding sources are mapped

`propagation._LABEL_BINDING_SOURCES` covers the five real registered bindings; anything else raises `UnknownBindingSourceError`. **Low** — refuses rather than guesses (Ph6).

### `LS-TD-008` — Bootstrap standing backfill is scoped by `NULL`, not by bootstrap marker

`backfill_bootstrap_standing()` selects `standing IS NULL`. A future non-bootstrap row left `NULL` by a caller bug would be swept in and resolved via the live-boundary path rather than flagged as anomalous. **Low** — `resolve_standing()` branches on `declared_by`, so the outcome is still correct per §10.1.

### `LS-TD-009` — No re-flagging path for an already-`Adjudicated` label

A second corpus correction affecting a label already adjudicated has no arc. **Correct as built** — `ADR-I011` §12.2's state diagram draws no such arc, and inventing one would be architecture. **Low** — no real data has ever reached `Adjudicated`. Closure needs an ADR decision, not engineering.

### `LS-TD-010` — Admissibility ceilings are never persisted

Every `propagation.py` / `set_hygiene.py` ceiling call recomputes from scratch; neither store has an admissibility column. Deliberate — adding one would be architectural redesign. **Low** — negligible cost; Phase 4 is the plausible first consumer with a real place to put a persisted value.

### `LS-TD-011` — No gauntlet certification exists for three of four real sources

Only `dvol.py` is wired to `research/quality/admissibility.py`. `spot_1m`, `perp_1m`, and `funding_raw` have none, so **every real admissibility ceiling this platform can currently compute caps at `Certification.UNKNOWN` / `Readiness.UNKNOWN`**. **Medium** — the low ceiling is the *honest* answer per §10.2 (*"a label computed on an uncertified span inherits that cap"*), and a future gauntlet-wiring milestone would only ever raise ceilings. But it means Phase 4's DI5 meet will be dominated by `UNKNOWN` on the evidence leg regardless of label quality.

### `LS-TD-012` — Class-imbalance disclosure is sign-based only

`outcome_distribution()` partitions positive/negative/zero rather than N-class. The real label shape is a continuous forward-return float; sign is the natural non-invented binary partition. `outcome_field` is already caller-overridable. **Low**.

### `LS-TD-013` — `label_store.db` has no off-host replication

Local backup, manifest, rotation, and freshness monitoring are all live (WBS 3.10), and a restore drill transcript is committed. Off-host replication is not wired, matching `feature_store_backup_strategy.md` §4's carve-out. **Medium** — and rising: unlike the Feature Store, the forward tier is *irreplaceable from the first row*, and 148 such rows now exist. Re-evaluate as soon as forward-tier volume grows.

### `LS-TD-014` — `TD-P1-002` WAL-aware restore also applies to `label_store.db`

`scripts/restore.py`'s swap copies only the `.db` file, never checkpointing or clearing `-wal`/`-shm`. Already tracked in `CLAUDE.md` §19 for `ledger.db` and `btc_trading.db`; WBS 3.10 confirmed a third affected database. **Medium**, unchanged in nature — this entry adds scope to an existing item, not a new one.

### `LS-TD-015` — The quarterly production drill does not cover `label_store.db`

`docs/runbook.md`'s drill script has no third `restore.py` invocation. Deliberate: `label_store.db` has no production deployment yet. A pointer to the standalone drill transcript was added instead. **Low**.

### `LS-TD-016` — PR-8's migration script retains the unconditional `init_db()` pattern

`migrate_bootstrap_transition_receipts.py` calls `evidence_governance.init_db()` before checking `--commit` — the exact pattern WBS 3.8b's first draft copied and had to correct after it created an empty table on a real database during a dry run. Harmless for `ledger.db`, which always has the table. **Low** — would only manifest if that script were pointed via `--db` at a genuinely fresh database.

### `LS-TD-017` — The research tier holds zero values

`label_value_research` has 0 rows. `put/get/evict/resolve_research` and `LabelDivergenceEvent` are implemented and unit-tested, but §7.3's verification rule (stored-vs-recomputed divergence reporting) has never run against real data. **Low** — mechanism-only status is correct and honest; `ret_fwd1`'s Specification exists and awaits a consumer.

---

## 4. Decisions Owed — Detail

### `LS-DEC-003` — Reviewer sign-off on ten Phase 3 verification records

**All ten** Phase 3 milestone records (3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8a, 3.8b, 3.9, 3.10) have their entire Reviewer Checklist unticked. Every other checkbox in every record — BV, TV, REG, PERF, DBV, BC, RBV, TD, DOD — is complete; only RC is outstanding.

**Why this is more than bookkeeping.** Roadmap §8.1 states explicitly: *"🔄 **Reviewer sign-off (Verification Standard §12, RC1–RC8) is obtained per milestone, not deferred.** Phase 2 accumulated five unsigned records (`FS-DEC-002`); **Phase 3 does not repeat that**."* Phase 3 accumulated ten. This is a named criterion the phase set for itself and did not meet.

**Discharge.** Rohith reviews and ticks. No engineering work.

### `LS-DEC-004` — Ratify or replace the roadmap's `label_policy.yaml` rollback strategy

Roadmap §10 names four config keys as the rollback levers for WBS 3.5/3.6/3.7/3.9. None exists (`LS-GAP-006`). Four milestones independently satisfied "non-blocking by default" structurally instead — a coherent and arguably better answer, since a config switch that disables falsification protection is *exactly* what §10 itself refuses to build for WBS 3.2.

**The decision:** either (a) ratify the structural approach and record that §10's four keys are superseded, or (b) require `label_policy.yaml` as originally planned. **Recommendation: (a)** — it matches what was built, is already documented in WBS 3.9's record, and avoids adding configuration surface for a store with one writer and no production deployment.

### `LS-DEC-005` — Ratify the `P3-1` "shared-code-path test" reading

Roadmap `P3-1` requires *"the declare-then-fill **discipline** is proven reused via a shared-code-path test, **and** enforcement is proven strengthened to the storage layer. Both halves required."*

The second half is unambiguously met (the trigger). The first half is **not satisfiable as literally worded**, because the roadmap's own `LR1` mitigation — *"the single most important design decision in Phase 3"* — mandates that H6 keep running unchanged and the Label Store merely *observe* it, in new tables, with no shared code path. A test proving `daily_job.py` and the Label Store share a fill path cannot exist without undoing `LR1`.

**The defensible reading, and the one the delivered work satisfies:** the declare-then-fill *discipline* is reused via `value_store.py`'s `declare_forward_label()` → `mature_forward_label()` pair, which **is** a shared code path — shared between WBS 3.4's migration and every future live declaration — and is tested as such.

**This is a genuine internal inconsistency in an approved roadmap, between `P3-1` and `LR1`.** It is reported rather than edited, per the closeout's documentation-only scope. **Recommendation:** ratify the reading above and annotate `P3-1`; do not weaken `LR1`.

### `LS-DEC-006` — Commit Phases 0–3 to version control

**The single highest-severity finding of this closeout.** `git log` shows the most recent commit is `c14ba38` (Program P1). Everything produced by Phases 0, 1, 2, and 3 — `research/admissibility/`, `research/feature_store/`, `research/label_store/`, `research/storage/raw_lake.py`, `research/quality/admissibility.py`, `docs/architecture/`, `docs/engineering/`, and 20+ test files — exists **only as uncommitted working-tree state on one machine**.

**This was foreseen and gated.** Roadmap `E11` was *raised from "advised" to blocking for L4 onward* (`LR13`, Moderate/**Severe**), and readiness condition `C-3` says *"Blocks **L4 onward**."* L4 through L7 (WBS 3.4 → 3.10) executed with `C-3` undischarged. Phase 2's own closeout resolved this to *"a plan, not an act,"* correctly deferring the act to Rohith.

**Why it now blocks Phase 4.** The parent roadmap calls Phase 4 *"a hard join point — it consumes all three"* prior phases. Building it atop four uncommitted phases means no revert point, no bisect, no diff-based review, and total loss on a single disk failure. `CLAUDE.md` §18 records this exact lesson from Program P1, whose closure explicitly celebrated *"its own output no longer exists only on one machine."*

**Discharge.** Execute the ordered commit plan already written at [Phase 2 Closeout §4](verification/records/phase-2-closeout.md), extended to cover Phase 3. Rohith's act; this session will not commit without an explicit instruction.

---

## 5. Adding to This Register

New entries take the next free ID in the appropriate series and must state: what is missing or weak, why it was deferred, who closes it, and the blast radius if it is never closed. An entry is removed only when the work is done and verified — never because it became inconvenient.

## 6. Governance of This Register

This register is **descriptive, not authoritative**. It records what is true about the repository; it grants no permission and imposes no requirement. Closing an entry requires the normal milestone discipline (Engineering Verification Standard, one record per milestone). `ADR-I011` remains the authority on what the Label Store *should* be.

---

| Role | Name | Date |
|---|---|---|
| Register opened | Claude Opus 5 | 2026-08-06 |
| **Owner acceptance** | **Rohith Suresh** | **_______________** |
