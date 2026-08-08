# Dataset Builder — Technical Debt Register

| Field | Value |
|---|---|
| **Declared by** | [`phase_4_dataset_builder_roadmap.md`](phase_4_dataset_builder_roadmap.md) §13.3 — *"Dataset Builder TD Register"*, WBS closeout |
| **Scope** | Every `TD-` and `HZ-` item raised by WBS 4.0 – 4.11 and by Phase 4 closure work |
| **Created** | 2026-08-08 — closing audit finding `AF-3` (declared deliverable absent) |
| **Source of truth** | The per-milestone verification records. This register **indexes** them; it does not restate or revise their findings |
| **Status** | Open register. No item is closed by its appearance here |

---

## 1. How to read this

Each row is a pointer, not a replacement. The owning verification record holds
the full reasoning, and where the two ever disagree **the record wins** — it was
written with the implementation in front of it.

Two items have been **resolved** since being raised and are marked as such.
Everything else is open.

Severities are as recorded by the owning milestone: 🔴 High · 🟡 Medium · 🟢 Low.

## 2. Technical debt — 43 items

| # | Item | Sev | Owning record |
|---|---|---|---|
| `TD-4.2-1` | `citation_check=None` default lets a caller retire without consulting the index | 🟡 | `wbs-4.2` |
| `TD-4.2-2` | `index_declaration()` is not called automatically | 🟡 | `wbs-4.2` |
| `TD-4.2-3` | `cited_id` is not validated against the upstream stores | 🟢 | `wbs-4.2` |
| `TD-4.3-1` | `resolve()` binds a reference to itself rather than to a resolved-at-`as_of` successor | 🟡 | `wbs-4.3` |
| `TD-4.3-2` | RF6 treats warm-up periods as calendar days | 🟡 | `wbs-4.3` |
| `TD-4.3-3` | Corpus coverage is injected, not read from the WBS 4.0 manifests | 🟢 | `wbs-4.3` |
| `TD-4.3-4` | `_MATURITY_DEMANDING` is a fixed vocabulary | 🟢 | `wbs-4.3` |
| `TD-4.4-1` | TB1 unreachable through the normal path — Tier A refuses first | 🟡 | `wbs-4.4` |
| `TD-4.4-2` | Tier D thresholds are module constants | 🟡 | `wbs-4.4` |
| **`TD-4.4-3`** | ✅ **RESOLVED 2026-08-09**, commit `c6d00b0`. `InputAdmissibility` is derived from resolved references via `research/dataset_builder/admissibility_derivation.py`, under the ratified [`OD-10A`/`OD-10B`/`OD-10C`](verification/records/od-10-td-4-4-3-ratification.md). Feature ceilings composed from real governed manifests, independent of `propagation.py` (`OD-10A`); label ceilings aggregated through `set_hygiene.set_admissibility_ceiling()`, with missing/non-derivable governed label data producing a persisted `TC4` rejection before the refusal propagates (`OD-10B`). Independently verified — [WBS 4.12 record](verification/records/wbs-4.12-td-4-4-3-admissibility-derivation.md), §§9–10 | 🟡 | `wbs-4.4`, `wbs-4.12` |
| `TD-4.4-4` | `check_tier_a` re-runs registry Tier A, so findings all carry `TA1` | 🟢 | `wbs-4.4` |
| **`TD-4.5-1`** | **Seal receipt and sealed row are in different databases — not one transaction** | 🔴 | `wbs-4.5` |
| `TD-4.5-2` | Already-sealed check unreachable — the phase guard fires first | 🟢 | `wbs-4.5` |
| `TD-4.5-3` | `SEALING_ENGINE_VERSION` is a constant, not derived from the producing code | 🟡 | `wbs-4.5` |
| `TD-4.5-4` | `resolution_ts` records the declaration timestamp | 🟡 | `wbs-4.5` |
| `TD-4.6-1` | Lockbox constant still textually duplicated in the two frozen artifacts | 🟡 | `wbs-4.6` |
| `TD-4.6-2` | `SEALED_REGIONS` is a Python constant, not governed data | 🟡 | `wbs-4.6` |
| `TD-4.6-3` | `run_lockbox_validation.py` was not migrated | 🟢 | `wbs-4.6` |
| **`TD-4.7-1`** | **RF3 is `not_evaluable` for every existing declaration — inert until a lane code is carried** | 🔴 | `wbs-4.7` |
| `TD-4.7-2` | `input_lane` read via `getattr` rather than on the Protocol | 🟡 | `wbs-4.7` |
| `TD-4.7-3` | `FORBIDDEN_INPUT_LANES` is a Python constant | 🟡 | `wbs-4.7` |
| **`TD-4.8-1`** | **Snapshot bytes are returned, never written to durable storage** | 🔴 | `wbs-4.8` |
| `TD-4.8-2` | `ValueProvider` has no real implementation | 🟡 | `wbs-4.8` |
| `TD-4.8-3` | Row sort key serialises each row twice | 🟢 | `wbs-4.8` |
| `TD-4.8-4` | `MATERIALISER_ENGINE_VERSION` hand-maintained | 🟡 | `wbs-4.8` |
| `TD-4.9-1` | `consuming_ref` opaque; nothing validates it resolves to a real run | 🟡 | `wbs-4.9` |
| **`TD-4.9-2`** | **`trial_registration` is free text — a typo creates a phantom independent trial** | 🔴 | `wbs-4.9` |
| `TD-4.9-3` | Denominator gaps reported as information, not error | 🟢 | `wbs-4.9` |
| `TD-4.9-4` | `USE_PURPOSES` is a module constant | 🟢 | `wbs-4.9` |
| **`TD-4.10-1`** | ✅ **RESOLVED 2026-08-09**, commit `c6d00b0`. `computed_meet` is now a mandatory keyword-only argument on `register_partition()` (no default) — a caller omitting it raises `TypeError` at the language level rather than silently skipping the RF4 gate. Enabled by `TD-4.4-3`'s resolution (the meet is now derivable, per the ordering constraint below) | 🔴 | `wbs-4.10` |
| `TD-4.10-2` | Non-overlap verified between declared windows, not materialised content | 🟡 | `wbs-4.10` |
| `TD-4.10-3` | Sibling-group completeness never enforced before use | 🟡 | `wbs-4.10` |
| `TD-4.10-4` | `windows_overlap` treats an unparseable date as non-overlapping | 🟢 | `wbs-4.10` |
| `TD-4.11-1` | A bound-but-unused Experiment Dataset does not block retirement | 🟡 | `wbs-4.11` |
| `TD-4.11-2` | `verify_l1()` refuses a Retired dataset — L1 re-verification ends at retirement | 🟡 | `wbs-4.11` |
| **`TD-4.11-3`** | **History is complete only from `history.init_db()` forward — L3 is a forward guarantee** | 🔴 | `wbs-4.11` |
| `TD-4.11-4` | `ts` from SQLite's `now`, not an injected clock | 🟢 | `wbs-4.11` |
| `TD-4.11-5` | Staleness-flag `cited_kind`/`cited_id` are free text | 🟢 | `wbs-4.11` |
| **`TD-4.12-1`** | **The sealed dataset is never materialised — L1/L2 unproven on real data** | 🔴 | `phase-4-engineering-audit` |
| `TD-4.12-2` | `bootstrap_first_dataset.py` hard-codes window, features, label horizon | 🟢 | `phase-4-engineering-audit` |
| `TD-4.12-3` | Two tests pinned the real ledger at exactly 26 receipts | 🟡 | `phase-4-engineering-audit` |
| `TD-4.12-4` | *(Resolved 2026-08-08, `dd75aeb`)* `feature_store.db` had no backup path at all | 🔴 | `phase-4-governance-completion-package` |
| `TD-4.12-5` | *(Resolved 2026-08-08)* Three WBS 4.0 records had no reviewer signature block | 🟡 | `phase-4-governance-completion-package` |

**Six High-severity items open** *(updated 2026-08-09 — was "eight"; `TD-4.10-1`
resolved this date, `TD-4.12-4` already resolved 2026-08-08 but not previously
reflected in this count)*: `TD-4.5-1`, `TD-4.7-1`, `TD-4.8-1`, `TD-4.9-2`,
`TD-4.11-3`, `TD-4.12-1`.

**The `TD-4.4-3` → `TD-4.10-1` ordering constraint is discharged**: both
resolved together, commit `c6d00b0`, 2026-08-09 — `computed_meet` could only
be made mandatory once the meet was derivable, per `TD-4.4-3`'s own
resolution. `TD-4.4-3`'s binding Phase 5 entry pre-condition
([`OD-6`](verification/records/owner-decision-pack-2026-08-08.md): must
resolve before any Phase 5 code path invokes the Dataset Builder) is
likewise satisfied — the derivation authority is ratified (`OD-10A`/`OD-10B`)
and implemented (commit `c6d00b0`), independently verified before this
closure. See the [WBS 4.12 record](verification/records/wbs-4.12-td-4-4-3-admissibility-derivation.md)
for full evidence. Two items carried forward as future debt, not closure
blockers: `NoGovernedEvidenceForWindowError` is not yet converted to a
persisted rejection (evidence-side, out of `TD-4.4-3`'s scope); a
successfully-derived label input inherits `propagation.py`'s permanent
`Readiness.UNKNOWN`, so `TC3` will fire for it (a correct consequence of
reusing the existing, unmodified label authority — `OD-10B`).

## 3. Hazards — 19 items

| # | Item | Sev | Owning record |
|---|---|---|---|
| `HZ-4.2-1` | Wrong `cited_kind` to `make_citation_check()` checks the wrong namespace | 🟢 | `wbs-4.2` |
| `HZ-4.3-1` | Resolution is not transactional across registry + citation index | 🟡 | `wbs-4.3` |
| `HZ-4.3-2` | RF6 silently passes when coverage is unknown | 🟢 | `wbs-4.3` |
| ~~`HZ-4.4-1`~~ | Tier D caps computed but not persisted | ✅ **Resolved at 4.5** | `wbs-4.4` |
| `HZ-4.4-2` | An empty `inputs` sequence raises rather than validating | 🟢 | `wbs-4.4` |
| `HZ-4.5-1` | Nothing consumes `admissibility_ceiling()` to gate a reader | 🟡 | `wbs-4.5` |
| ~~`HZ-4.5-2`~~ | A sealed ceiling can go stale if a cited input is later flagged | ✅ **Resolved at 4.11** | `wbs-4.5` |
| **`HZ-4.6-1`** | **RF2 guards only builder-declared datasets; direct corpus reads bypass it** | 🔴 | `wbs-4.6` |
| `HZ-4.6-2` | A dataset sealed before 4.6 was never RF2-checked | 🟡 | `wbs-4.6` |
| **`HZ-4.7-1`** | **RF3 guards only builder-declared datasets** | 🔴 | `wbs-4.7` |
| `HZ-4.7-2` | A dataset sealed before 4.7 was never RF3-checked | 🟡 | `wbs-4.7` |
| **`HZ-4.8-1`** | **A divergence is recorded but nothing alerts on it** | 🔴 | `wbs-4.8` |
| `HZ-4.8-2` | L1 cannot distinguish "content changed" from "engine changed" | 🟡 | `wbs-4.8` |
| **`HZ-4.9-1`** | **Nothing alerts on cross-trial substrate reuse** | 🔴 | `wbs-4.9` |
| `HZ-4.9-2` | A dataset may be bound to a trial and never used | 🟡 | `wbs-4.9` |
| `HZ-4.10-1` | No Training Dataset can exist until forward-lane labels do — expected | 🟢 | `wbs-4.10` |
| `HZ-4.10-2` | Holdout consumption is recorded but nothing alerts on it | 🟡 | `wbs-4.10` |
| **`HZ-4.11-1`** | **Nothing alerts on a recorded staleness flag** | 🔴 | `wbs-4.11` |
| `HZ-4.11-2` | `detect_stale_ceilings()` must be invoked; nothing calls it | 🟡 | `wbs-4.11` |

### 3.1 The dominant pattern

Four hazards — `HZ-4.8-1`, `HZ-4.9-1`, `HZ-4.10-2`, `HZ-4.11-1` — are one gap
seen four times: **the platform records a high-consequence governance event
faithfully and nothing surfaces it.** This was confirmed in practice at
operational closure: the first `dataset.seal` receipt was written to the chain
with no alert, no report entry and no operator-visible signal.

Two further hazards — `HZ-4.6-1`, `HZ-4.7-1` — are a second shared shape: **the
builder is a governed doorway, not a wall.** RF2 and RF3 protect declarations
made through it; a script reading the corpus directly is outside both.

Treating each pattern as one work item is likely cheaper and more coherent than
patching six hazards separately.

## 4. Register maintenance

New items are added by the milestone that raises them, in its own verification
record first. This file is regenerated from those records; it is never the place
a debt item is *first* described.
