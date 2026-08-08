# WBS 4.12 — `TD-4.4-3` Admissibility Derivation — Verification Record

| Field | Value |
|---|---|
| **Milestone** | WBS 4.12 — derive `InputAdmissibility` from resolved references, closing `TD-4.4-3` |
| **Governing ADR** | `ADR-I012` §8.1 stage 3; `ADR-I014` (lattice); `ADR-I011` §11.1/§16.3 (label maturity) |
| **Binding condition** | Owner Decision Pack `OD-6`, 2026-08-08: `TD-4.4-3` must resolve before any Phase 5 code path invokes the Dataset Builder |
| **Date** | 2026-08-09 |
| **Implementer** | Claude Opus 5 |
| **Reviewer** | *(not requested — this record is not submitted for sign-off; see §6)* |
| **Status** | 🔶 **PARTIAL — NOT RESOLVED.** Evidence-side derivation proven correct and bit-identical-capable. Feature-side and label-side derivation implemented and correct, but **cannot reproduce the currently-sealed dataset's historical ceiling**, for two distinct, evidenced reasons (§3). `TD-4.4-3` is **not marked resolved** |
| **Governance** | ✅ **`OD-10A`/`OD-10B`/`OD-10C` RATIFIED 2026-08-09** — see [`OD-10` Ratification](od-10-td-4-4-3-ratification.md). The governance path forward is now decided; **implementation has not started**. `TD-4.4-3` remains open until the corrected completion criteria in that record are independently verified |

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

## 6. Governance — explicitly not actioned

- `TD-4.4-3` is **not** marked resolved in the TD register.
- This record is **not signed** — no Reviewer/Date filled in, per instruction.
- No governance receipt was created (a derivation-module addition is not in `receipts.DECISION_TAXONOMY`, and none was added to it).
- Phase 5 readiness is **unchanged**: `TD-4.4-3` remains the binding condition on any Phase 5 code path invoking the Dataset Builder.

## 7. Files changed

| File | Change |
|---|---|
| `research/dataset_builder/admissibility_derivation.py` | **New** |
| `research/label_store/set_hygiene.py` | `list_subject_ids()` added |
| `tests/test_admissibility_derivation.py` | **New**, 15 tests |
| `docs/engineering/verification/records/wbs-4.12-td-4-4-3-admissibility-derivation.md` | **New** — this record |

No other file touched. No schema change, no migration, no database write.

## 8. What has to happen next, and whose decision each is

**Updated 2026-08-09 — items 1 and 2 below are now decided.** See
[`OD-10` Ratification](od-10-td-4-4-3-ratification.md) for the exact,
ratified wording. The original framing is preserved below, struck through
where superseded, rather than deleted — this is what the questions looked
like before ratification.

1. ~~**Owner decision** — how should feature certification be handled when a feature's real underlying sources are certified but `propagation.py`'s `source_ceiling()` cannot see that?~~ **Decided: `OD-10A`.** A new, Dataset-Builder-owned function, reading real manifests directly, never calling `propagation.py`. `propagation.py` itself is not modified.
2. ~~**Owner decision** — is the label window-aggregation gap... something to backfill, or to accept as a boundary...?~~ **Decided: `OD-10B`.** Accepted as a boundary — future citations over windows with no governed data are refused, not backfilled. Bulk historical label materialization (`LS-TD-017`) remains separate, unauthorized, unscheduled.
3. **Engineering, gated on 1 and 2 — now unblocked, not yet started.** Wire the ratified design into `resolution.py`; retire `bootstrap_first_dataset.py`'s hand-rolled logic; make `training.py`'s `computed_meet` mandatory; resolve the RF7-vs-Tier-fold implementation question (`OD-10B`, not decided by the ratification itself); mark `TD-4.4-3`/`TD-4.10-1` resolved only once the corrected completion criteria (`OD-10` record) independently verify; sign this record.
