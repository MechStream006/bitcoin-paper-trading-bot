# WBS 4.12 — `TD-4.4-3` Admissibility Derivation — Verification Record

| Field | Value |
|---|---|
| **Milestone** | WBS 4.12 — derive `InputAdmissibility` from resolved references, closing `TD-4.4-3` |
| **Governing ADR** | `ADR-I012` §8.1 stage 3; `ADR-I014` (lattice); `ADR-I011` §11.1/§16.3 (label maturity) |
| **Binding condition** | Owner Decision Pack `OD-6`, 2026-08-08: `TD-4.4-3` must resolve before any Phase 5 code path invokes the Dataset Builder |
| **Date** | 2026-08-09 |
| **Implementer** | Claude Opus 5 |
| **Reviewer** | Rohith Suresh |
| **Status** | ✅ **CLOSED 2026-08-09 — `TD-4.4-3` and `TD-4.10-1` RESOLVED**, commit `c6d00b0`. Implemented under the ratified `OD-10A`/`OD-10B`/`OD-10C`, independently audited (12/12 gates GO), then closed. §§1–8 below are the *investigation* record, preserved as originally written — the record of what was found before implementation. §9 is the closure addendum: what was actually built, and its independent verification |
| **Governance** | `OD-10A`/`OD-10B`/`OD-10C` ratified 2026-08-09 — [`OD-10` Ratification](od-10-td-4-4-3-ratification.md). Implemented same date, commit `c6d00b0`. `TD-4.4-3` and `TD-4.10-1` marked resolved in the [TD register](../../dataset_builder_technical_debt_register.md) |

**Pre-milestone production gate:** not re-run this session — no production, database, key, credential, or configuration change was made or contemplated in this milestone. Confirmed via `git status` (only the files in §7 changed) and direct receipt-chain re-verification (§5).

---

## 1. What this record is

`TD-4.4-3`: `InputAdmissibility` was caller-supplied everywhere in the Dataset Builder; nothing derived it from resolved references. `OD-6` accepted this with a binding condition rather than deferring it indefinitely. This record is the scoped attempt to resolve it.

**It does not resolve `TD-4.4-3`.** Building the derivation machinery surfaced two real findings that block reproducing the one real sealed dataset's historical ceiling — not implementation defects, but properties of the *authoritative sources themselves* once actually exercised against real governed data. Per the task's own instruction — *"Stop if the label-window aggregation cannot be derived unambiguously from existing governed data. Do not guess"* — this record stops and reports rather than routing around either finding.

## 2. Baseline captured before any edit

The one real sealed dataset (`spec_hash f444c132b2a5d666…`, lineage `ivrv-vrp-substrate`, window `2021-05-01`..`2025-05-31`):

| Field | Value |
|---|---|
| `capped_admissibility_json` | `{certification: AMBER, readiness: READY, maturity: FULL, provenance: OFFLINE_CORPUS, integrity: CLEAN}` |
| `computed_meet_json` | identical (Tier D caps empty — nothing capped it) |
| `validation_fingerprint` | `81b080ac91a269ae68c4ba9c6289af56bf648cfb90cd570784c1ffbd4378255f` |
| Cited evidence | `dvol` rev-`62ee257e…`, `spot` rev-`a87ba269…` |
| Cited features | 3 hashes (`b1970c…`, `4885210c…`, `95c322f2…`) |
| Cited label | `ff680d93…` (`spot_one_day_forward_return`, horizon 5d, `maturity_requirement: matured`) |

Re-verified identical, byte-for-byte, at the end of this session (§5) — nothing in `dataset_builder.db` was written to at any point.

## 3. What was found, tracing the real code and real data

### 3.1 The confirmed original defect

`research/dataset_builder/bootstrap_first_dataset.py::build_inputs()` (the only real production caller of `validate()`) computed ceilings with its own hand-rolled `_CERT_ORDER`/`_CERT_ENUM`/`_READY_ORDER`/`_READY_ENUM` dicts and manual `min()` calls — a second, unreviewed re-implementation of what `research/admissibility/composition.py::meet()` already does via the lattice's own `IntEnum` ordering. `check_tier_c`/RF4 itself (`validation.py:459`) was untouched and remains the sole gate; the duplication was entirely upstream of it, in how `ceiling` values got computed before RF4 ever saw them.

### 3.2 Evidence — derivable, bit-identical-capable (PROVEN)

`research/quality/admissibility.py::corpus_ceiling()` (`= composition.meet()`, "adds no new composition logic of its own") composes each partition's own, already-WBS-4.0-certified verdict — stored in every `_manifest.json`'s `admissibility` field. Reconstructing per-partition `Admissibility` from that stored verdict and composing across the window reproduces real, non-`UNKNOWN` certification: `dvol → GREEN/READY`, `spot → AMBER/READY`, matching the sealed declaration's own `required_certification_verdict: AMBER` (`test_real_dvol_and_spot_windows_are_derivable`).

**Deviation from the task's stated authority, made and justified, not silent:** the task named `research/label_store/propagation.py::source_ceiling()` as "the authoritative existing source ceiling." Tracing it: `source_ceiling()` **always** returns `certification=UNKNOWN, readiness=UNKNOWN` — its own docstring: *"no gauntlet report read by this module."* It is the Label Store's deliberately-conservative placeholder for when it has no direct access to per-partition certification, not the authority to use when that real data (WBS 4.0's own manifests) is directly reachable, as it is here. Using it as prescribed would silently downgrade every real AMBER/GREEN verdict to `UNKNOWN`, breaking the bit-identical requirement outright. `quality.admissibility.corpus_ceiling()` is the correct authority for this case — same composition primitive (`meet()`), fed real data instead of a conservative fallback.

### 3.3 Feature — derivable, but produces `UNKNOWN`, not the sealed AMBER (FINDING)

`propagation.feature_specification_ceiling(spec)` — the task's prescribed authority — internally composes a feature's own assessment (GREEN) with `meet(*(source_ceiling(s) for s in sources))`. Since `source_ceiling()` is *always* `UNKNOWN` (§3.2), the meet of GREEN and UNKNOWN is **UNKNOWN**, for every feature, structurally, regardless of what its real underlying sources are actually certified at.

Verified against all three real cited features (`test_real_features_derive_to_unknown_certification`): all three derive to `UNKNOWN` certification. This is `propagation.py` working exactly as written — not a bug in this derivation. But it is **not what the hand-rolled code produced** (which borrowed the corpus's real cert/ready values directly, bypassing `propagation.py` entirely), so faithfully using the prescribed authority **cannot** reproduce the sealed AMBER baseline. Composing this into the full 6-input meet would make the *whole* dataset's ceiling `UNKNOWN` on certification — a different, and by DA3/DG-2's own discipline arguably more honest, but **not bit-identical**, result.

**This was not routed around.** Making `feature_specification_ceiling()` read real per-partition data would mean changing `propagation.py` itself — cross-cutting Label Store code with other consumers (label ceilings too), well beyond "the smallest correct fix," and not something to decide unilaterally.

### 3.4 Label — derivable in general, but no governed data exists for this window (FINDING)

Enumerating every declared subject for the cited label specification (`ff680d93…`) via the new `set_hygiene.list_subject_ids()` (§4, a small additive enumeration — WBS 4.12's only change to `label_store/`) against the real `label_store.db`: **37 rows exist, all with `subject_id` values of the shape `"h6-trade-N"`** — a fixed set of historical H6 forward-evidence trade identifiers, not calendar dates. **Zero** fall within, or parse as, any date in `[2021-05-01, 2025-05-31]`.

Verified directly (`test_real_sealed_dataset_window_has_no_governed_label_data`): `derive_label_ceiling()` correctly raises `NoGovernedLabelDataForWindowError`. This is not a defect in the aggregation logic — the governed Label Store genuinely has no per-date value for this specification covering this window. The hand-rolled code never touched real label-value data for the label input at all; it borrowed `spot`'s evidence cert/ready as a stand-in. **No spec-level label ceiling was invented to route around this** (explicitly forbidden by the task) — the function was built to aggregate real per-subject data, found none in-window, and refused.

## 4. What was implemented

**New file:** `research/dataset_builder/admissibility_derivation.py` — `derive_evidence_ceiling()`, `derive_feature_ceiling()`, `derive_label_ceiling()`, plus `_partition_admissibility_from_manifest()` (a narrow adapter reconstructing `Admissibility` from a manifest's already-computed verdict, using `quality.admissibility.partition_ceiling()`'s own documented constants — not a new rule). Two named exceptions (`NoGovernedEvidenceForWindowError`, `NoGovernedLabelDataForWindowError`) instead of silent fallback.

**Modified:** `research/label_store/set_hygiene.py` — added `list_subject_ids(db_path, spec_hash)`, pure enumeration (subject_id/declared_at/maturity_state, every state, not just matured), needed because `request_fully_matured()` deliberately returns only the matured-or-later subset and gives no way to see what a window covers *before* checking maturity. No new judgement, no new admissibility logic.

**Not modified, deliberately:** `validation.py`, `compose_admissibility()`, `satisfies()`, `meet()`, `check_tier_c()` — untouched, as instructed. `resolution.py` (`StoreResolver`/`ResolvedReference`/`ResolutionReport`) — **not wired**, because wiring it would mean either (a) making the real production path produce a different, non-bit-identical result (via `feature_specification_ceiling`'s `UNKNOWN`), or (b) silently choosing not to use the prescribed authority for features, either of which is a decision, not an implementation detail. `bootstrap_first_dataset.py`'s hand-rolled `_CERT_ENUM`/`_CERT_ORDER`/`build_inputs()` — **not removed**, for the same reason: removing it now, before the feature-certification question is decided, would either break the one real seal's reproducibility or leave the script unable to explain its own historical output. `training.py`'s `register_partition(computed_meet)` — **not made mandatory**; audit confirmed zero production callers (only `tests/test_training.py`, 58 calls, 56 omitting it) so it is *safe* to change, but doing so before the upstream derivation question is settled would be premature.

## 5. Verification

### Chains, sealed dataset, production — unchanged

| Check | Before | After |
|---|---|---|
| `ledger.db` | 27, `ok=True` | 27, `ok=True` |
| `feature_store.db` | 17, `ok=True` | 17, `ok=True` |
| `label_store.db` | 8, `ok=True` | 8, `ok=True` |
| Sealed dataset `capped_admissibility_json`/`computed_meet_json`/`validation_fingerprint` | (§2) | **byte-identical** |
| Production (SSH) | — | **not touched this session** |
| Lockbox (`2025-06-01`..`2026-05-31`) | — | **not opened; not referenced by any new code or test** |

### `TestTV3ReintroducingHandRolledLogic` and TV3 sabotage results

| # | Sabotage | Result |
|---|---|---|
| a | Bypass propagation/quality-derived ceilings | N/A as a literal sabotage test — the finding *is* that the prescribed bypass path (`propagation.source_ceiling`) would itself be wrong (§3.2); this is proven by the deviation analysis, not a pass/fail sabotage |
| b | Omit one label subject from aggregation | `test_TV3_omitting_one_subject_changes_the_result` — PASS. Narrowing the window changes which subjects are aggregated, proving an accidental omission is detectable, not silent |
| c | Reintroduce the old `_CERT_ORDER` logic | `test_old_style_dict_lookup_crashes_on_a_real_lattice_value` — PASS. The old `_CERT_ENUM` dict has no `"UNKNOWN"` key; feeding it a real, legitimate lattice value raises `KeyError` where the correct path (`Certification["UNKNOWN"]`) succeeds |
| d | Omit `computed_meet` where mandatory | **Not applicable — `computed_meet` was not made mandatory this session** (§4). Deferred, not fabricated as a false pass |

### Full regression, both environments

| Environment | Result |
|---|---|
| `btc_bot` | 3312 passed, 19 skipped, **1 known-brittle failure**: `test_rf2_touches_no_file_outside_the_builder` — this test asserts `git status` shows no change outside `research/dataset_builder/`/`tests/`/`docs/`/`BASELINE_46.json`; `research/label_store/set_hygiene.py` (§4) is outside that allowlist by design. Previously logged as a known flake pattern (clears on commit); confirmed here to be exactly that, not a regression |
| `base` | 2909 passed, 62 skipped, same single known-brittle failure |

15/15 new WBS 4.12 tests pass (`tests/test_admissibility_derivation.py`).

## 6. Governance — as it stood at investigation time (superseded by §9)

- `TD-4.4-3` is **not** marked resolved in the TD register.
- This record is **not signed** — no Reviewer/Date filled in, per instruction.
- No governance receipt was created (a derivation-module addition is not in `receipts.DECISION_TAXONOMY`, and none was added to it).
- Phase 5 readiness is **unchanged**: `TD-4.4-3` remains the binding condition on any Phase 5 code path invoking the Dataset Builder.

**Superseded 2026-08-09 — see §9.** `TD-4.4-3` and `TD-4.10-1` are now resolved and this record is now signed. No governance receipt was created then or since — that determination stands unchanged.

## 7. Files changed

| File | Change |
|---|---|
| `research/dataset_builder/admissibility_derivation.py` | **New** |
| `research/label_store/set_hygiene.py` | `list_subject_ids()` added |
| `tests/test_admissibility_derivation.py` | **New**, 15 tests |
| `docs/engineering/verification/records/wbs-4.12-td-4-4-3-admissibility-derivation.md` | **New** — this record |

No other file touched. No schema change, no migration, no database write.

**Superseded 2026-08-09 — see §9** for the complete, final files-changed list across both the `OD-10` ratification and the implementation commit `c6d00b0`.

## 8. What has to happen next, and whose decision each is

**Updated 2026-08-09 — items 1 and 2 below are now decided.** See
[`OD-10` Ratification](od-10-td-4-4-3-ratification.md) for the exact,
ratified wording. The original framing is preserved below, struck through
where superseded, rather than deleted — this is what the questions looked
like before ratification.

1. ~~**Owner decision** — how should feature certification be handled when a feature's real underlying sources are certified but `propagation.py`'s `source_ceiling()` cannot see that?~~ **Decided: `OD-10A`.** A new, Dataset-Builder-owned function, reading real manifests directly, never calling `propagation.py`. `propagation.py` itself is not modified.
2. ~~**Owner decision** — is the label window-aggregation gap... something to backfill, or to accept as a boundary...?~~ **Decided: `OD-10B`.** Accepted as a boundary — future citations over windows with no governed data are refused, not backfilled. Bulk historical label materialization (`LS-TD-017`) remains separate, unauthorized, unscheduled.
3. ~~**Engineering, gated on 1 and 2 — now unblocked, not yet started.**~~ **Done — see §9.** Retired `bootstrap_first_dataset.py`'s hand-rolled logic; made `training.py`'s `computed_meet` mandatory; resolved RF7-vs-Tier-fold as `TC4` (Tier C, following `RF4`'s own precedent — a separate adjudication, not decided by the `OD-10` ratification itself); `TD-4.4-3`/`TD-4.10-1` marked resolved after the corrected completion criteria independently verified; this record signed.

---

## 9. Implementation and Closure — 2026-08-09, commit `c6d00b0`

This section is the closure addendum. §§1–8 above are preserved exactly as
written during the investigation phase (before implementation began); this
section documents what was actually built and its independent verification,
which together are what closed `TD-4.4-3` and `TD-4.10-1`.

### 9.1 `OD-10A` — feature derivation, independent of `propagation.py`

`research/dataset_builder/admissibility_derivation.py::derive_feature_ceiling()`
was rewritten to compose a feature's own static assessment (GREEN, Maturity
not-applicable — mirrored by value from `propagation.py`'s own constant, not
imported) with `derive_evidence_ceiling()`'s real per-partition manifest
reading, for each source the feature declares. It calls
`quality.admissibility.corpus_ceiling()`, never `propagation.py`.

Verified, independently, this closure:
- Zero `from label_store import propagation` anywhere in the file.
- AST-level: `derive_feature_ceiling()`'s call set contains neither
  `feature_specification_ceiling` nor `source_ceiling`.
- `git log -- research/label_store/propagation.py`: one commit, `b984036`
  (Phase 3 creation) — nothing since, including this closure.
- Real features now derive real, non-`UNKNOWN` certification (`dvol_frac`→GREEN,
  `rv30`→AMBER, `vrp_spread`→AMBER) — a direct, working consequence of moving
  off `propagation.py`'s conservative model.

### 9.2 `OD-10B` — `TC4`, persisted rejection, no evidence proxy, no bulk materialisation

`validation.py` gained one new function, `validate_with_derivation()`
(additive only — `validate()`, `compose_admissibility()`, `satisfies()`,
`check_tier_c()` are byte-unchanged, confirmed by `git diff` showing zero
removed lines in that file). It calls
`admissibility_derivation.derive_inputs()`; a label window with no governed
data, or windowed data that fails the declared maturity requirement, is
caught and converted to `Finding(code="TC4", tier="C", blocking=True)`.

**Persist-before-raise, verified by line number**: `reg.reject(db_path,
spec_hash, "Resolved", f"[TC4/C] {exc}")` at `validation.py:759`,
`raise ValidationRefused(...)` at line 760 — identical ordering to
`validate()`'s own existing `reg.reject()`/`raise` pair at lines 682–683.

**`TC4` itself was not invented as a number** — `Finding`'s own field
comment already read `# RF4, TB1-TB4, TC1-TC4, TD1-TD5` before this change;
`TC4` was reserved, unused, and unclaimed anywhere else in the codebase.

Proven live, twice, on disposable scratch stores (never the real
`dataset_builder.db`):
- A fresh `declare()`→`resolve()`→`validate_with_derivation()` on real data
  correctly raised `ValidationRefused([Finding(code="TC4", ...)])`, and the
  specification's phase was independently confirmed to have transitioned to
  `Rejected` with `rejection_reason` containing `"[TC4/C] …"`.
- Calling `validate_with_derivation()` against a *copy* of the real,
  already-`Sealed` store correctly refused with `TransitionError` (wrong
  `from_phase`) and left the copy's phase unchanged — proving the function
  cannot corrupt a sealed record even if misapplied.

`derive_label_ceiling()`'s body contains zero references to evidence data
(`corpus`/`spot`/`dvol`) — no proxy is possible. Zero references anywhere in
`research/dataset_builder/` to `label_value_research`, `put_research_value`,
or `resolve_research` — no bulk historical label materialisation exists;
`LS-TD-017` remains untouched, unscheduled.

### 9.3 Label aggregation through the `set_hygiene` authority, not duplicated

`derive_label_ceiling()` was rewritten to call
`set_hygiene.set_admissibility_ceiling(label_store_db, spec_hash,
subject_ids=window_subject_ids)` for aggregation, replacing a hand-rolled
per-subject `meet()` loop. `set_admissibility_ceiling()` (WBS 3.9, unmodified)
already composes `label_value_ceiling()` per row and correctly activates
`Maturity.PARTIAL` for a partially-matured set — logic this module no longer
duplicates. The refusal gate (`request_fully_matured()`, mirroring RF5) is
retained as a separate, upstream call — aggregation and refusal are distinct
concerns, both reused, neither reimplemented.

### 9.4 `readiness_resolved` derived, not defaulted

`InputAdmissibility.readiness_resolved` defaults to `True` on the dataclass;
`derive_inputs()` now explicitly passes
`readiness_resolved=_readiness_resolved(ceiling)` for all three input kinds.

**Authority traced, not invented**: `ADR-I012` §12 lists three parallel
presence-checks — *"certification verdict present... lane recorded...
readiness resolved"* — about whether something was recorded, not its value.
`quality/admissibility.py::readiness_from_gauntlet()` never emits
`Readiness.UNKNOWN` for a real certified partition (only `NOT_READY`/
`PARTIAL`/`READY`) — confirming `UNKNOWN` is the lattice's own "not
determined" state. Resolution: `readiness_resolved = (ceiling.readiness !=
Readiness.UNKNOWN)`. A determined-but-bad readiness (`NOT_READY`/`PARTIAL`)
is real data that correctly degrades the composed meet and is caught by
**RF4**, not `TC3`.

**Real, documented consequence, not routed around**: evidence and feature
ceilings are always resolved (verified against real data). Label ceilings,
built through `set_admissibility_ceiling()` → `propagation.label_value_ceiling()`
→ `propagation.source_ceiling()`, inherit `source_ceiling()`'s *permanent*
`UNKNOWN` readiness — verified directly against a real synthetic label store
using the real code path. **Every successfully-derived label input will,
today, report `readiness_resolved=False`, and `TC3` will fire for it.** This
is the direct, correct consequence of reusing `propagation.py`'s existing
model for labels, per `OD-10B` — carried forward as future debt (§9.9), not
a closure blocker (no ADR/WBS text requires labels pass `TC3` cleanly, and
for the one real declaration that exists, `TC4` fires first, before `TC3` is
ever reached).

TV3, proven not vacuous: constructed the same label ceiling both correctly
(`readiness_resolved=False`) and sabotaged (default `True`), and showed
`TC3` fires for the correct one and not the sabotaged one.

### 9.5 `computed_meet` mandatory at every caller

`training.py::register_partition()`'s `computed_meet` parameter lost its
`None` default; the guard that skipped the RF4 check when it was omitted was
removed — the check now always runs. `inspect.signature` confirms no
default remains.

Every real call site was audited and fixed, not just the ones anticipated at
design time: `tests/test_training.py`'s `_register()` helper (56 of 58
calls) gained a trivially-passing top-rung default; two *direct* call sites
in `tests/test_history.py` (missed by the original caller audit, found only
by running the full regression suite) were fixed to pass `computed_meet`
explicitly. One test, `test_omitting_computed_meet_raises_type_error`,
proves omission now raises `TypeError` at the language level.

### 9.6 `bootstrap_first_dataset.py` — hand-rolled logic retired

`_CERT_ENUM`, `_READY_ENUM`, `_sources_of()`, `build_inputs()`, and every
direct `InputAdmissibility(...)` construction were removed. The script now
calls `val.validate_with_derivation(STORE, spec_hash,
feature_store_db=FEATURE_STORE, label_store_db=LABEL_STORE, raw_dir=RAW)`.
`_CERT_ORDER`/`_READY_ORDER` (declaration-field bookkeeping, unrelated to
ceiling computation) were deliberately retained, with a comment explaining
why.

**Idempotent short-circuit preserved, proven live**: the original
`inputs = build_inputs(...)` call executed *before* the idempotency check
(`existing = reg.get_specification(...); if existing is not None: return
0`) — meaning derivation used to run unconditionally, even for the
already-sealed case. The fix moves derivation to occur only inside
`validate_with_derivation()`, called *after* the idempotency check. Running
`bootstrap_first_dataset.py --commit` live against the real store this
closure: `ALREADY PRESENT in phase 'Sealed' — idempotent, no second seal, no
second receipt` — confirming derivation is never even attempted for the
grandfathered dataset, exactly as required by `OD-10C`.

Independent confirmation: repository-wide grep for `InputAdmissibility(`
found exactly one production construction site —
`admissibility_derivation.py` itself. No unsanctioned construction remains
anywhere else in `research/`.

### 9.7 Evidence and feature derivation — deterministic, reproducible, not compared to the grandfathered value

Per the corrected `OD-10` completion criterion, verified exactly as
specified: `derive_evidence_ceiling()` and `derive_feature_ceiling()`, each
called twice independently against the same real governed data, produced
identical results both times (ceiling and detail dict). **No test, in this
file or `tests/test_admissibility_derivation.py`, compares a derived value
against the 2026-08-08 sealed dataset's historical `AMBER` ceiling** —
confirmed by review of every test added this closure.

### 9.8 `OD-10C` — grandfathered dataset, byte-identical throughout

`sealed_datasets`' `capped_admissibility_json`/`computed_meet_json`/
`validation_fingerprint`/`seal_receipt_id` for `spec_hash
f444c132b2a5d666…` were re-read at every major checkpoint across the full
implementation and this closure audit — **identical every time**:
`AMBER/CLEAN/FULL/OFFLINE_CORPUS/READY`, fingerprint `81b080ac91a269ae…`,
receipt `871a4e54b5ab144f…`. `sealed_datasets` carries `BEFORE UPDATE`/
`BEFORE DELETE` triggers (storage-layer, unconditional) — grandfathering
requires zero writes to any Dataset Builder table, and none occurred.

### 9.9 Final regression, chains, and files changed

| Check | Result |
|---|---|
| `tests/test_admissibility_derivation.py` | 30/30 pass |
| `tests/test_training.py` | 67/67 pass, including 2 new mandatory-`computed_meet` tests |
| `tests/test_history.py` | 91/91 pass, including 2 fixed direct call sites |
| Full regression, `btc_bot` | 3330 passed, 19 skipped, 0 failed |
| Full regression, `base` | 2927 passed, 62 skipped, 0 failed |
| RF2 git-status guard (`test_rf2_touches_no_file_outside_the_builder`) | **PASS**, confirmed post-commit |
| `ledger.db` | 27, `ok=True` |
| `feature_store.db` | 17, `ok=True` |
| `label_store.db` | 8, `ok=True` |
| `report.py --check` | clean, 35 families (9/22/4) |
| Production (SSH) | unaffected — `HEAD 0894f70`, this work never pushed until closure |
| Schema/migration/database write in the implementation commit | none — confirmed via `git show c6d00b0 \| grep -iE "CREATE TABLE\|ALTER TABLE\|DROP TABLE\|CREATE TRIGGER"`, zero matches |

**Files changed, implementation commit `c6d00b0`:**

| File | Change |
|---|---|
| `research/dataset_builder/admissibility_derivation.py` | Rewritten: `derive_feature_ceiling()` independent of `propagation.py`; `derive_label_ceiling()` reuses `set_hygiene.set_admissibility_ceiling()`; new `derive_inputs()`, `_corpus_source_for()`, `_FEATURE_OWN_CEILING`, `_readiness_resolved()` |
| `research/dataset_builder/validation.py` | Additive only — new `validate_with_derivation()` |
| `research/dataset_builder/training.py` | `computed_meet` mandatory |
| `research/dataset_builder/bootstrap_first_dataset.py` | Hand-rolled admissibility logic retired; routes through `validate_with_derivation()` |
| `research/label_store/set_hygiene.py` | `list_subject_ids()` (from the investigation phase, §7 — carried into this commit) |
| `tests/test_admissibility_derivation.py` | 30 tests (new file) |
| `tests/test_training.py` | `_register()` default + 2 new tests |
| `tests/test_history.py` | 2 call sites fixed |

**This record's closure files** (documentation only, separate from `c6d00b0`):
`docs/engineering/dataset_builder_technical_debt_register.md` (`TD-4.4-3`/
`TD-4.10-1` marked resolved) and this file.

### 9.10 Future debt, explicitly not closure blockers

- `NoGovernedEvidenceForWindowError` is not caught by
  `validate_with_derivation()` — no persisted-rejection path exists for a
  missing evidence input. No source authority found requiring it for this
  milestone's stated scope (`"TC4 = missing/non-derivable governed label
  data"`).
- Label-derived readiness remains `UNKNOWN` through the existing,
  unmodified `propagation.py` authority (§9.4) — `TC3` will fire for any
  future, successfully-derived label input. Not a defect; the direct,
  correct consequence of `OD-10B`'s ratified choice to reuse the existing
  label authority rather than build a second one.

### 9.11 What this closure does not do

Does not modify `research/label_store/propagation.py`. Does not modify
`ADR-I012` — nothing found this closure required it. Does not touch
production, any schema, or any database beyond the two disposable scratch
stores used for verification. Does not alter the grandfathered sealed
dataset. Does not create a governance receipt. Does not start Phase 5.
