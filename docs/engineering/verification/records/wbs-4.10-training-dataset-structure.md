# WBS 4.10 — Training Dataset Structure — Verification Record

| Field | Value |
|---|---|
| **Milestone** | WBS 4.10 — Training Dataset structure + partition roles (Sprint D6) |
| **Governing ADR** | [`ADR-I012`](../../../architecture/adr/ADR-I012-research-dataset-builder.md) §7.1 · §15 · §15.1 · §15.2 · **DG-7** · resolves **PS5** structurally |
| **Roadmap** | [`phase_4_dataset_builder_roadmap.md`](../../phase_4_dataset_builder_roadmap.md) v1.0.2 §4.1 row 4.10; exit criterion **`P4-6`** |
| **Date** | 2026-08-08 |
| **Implementer** | Claude Opus 5 |
| **Reviewer** | *(pending — Rohith)* |
| **Depends on** | 4.5 (sealing), 4.8 (pin), and 4.1–4.9 |

**Pre-milestone production gate:** health `overall INFO` / 0 non-OK · Telegram
delivered · collection `66/66` · 5 forward signals at `2026-08-07` ·
`backup --all --dry-run` exit 0 · 0 failed units · `report.py --check` OK ·
chain valid (26). All five gates passed.

---

## 1. Governance check performed before starting

`T-4` ("Training Dataset scope reduces to structure only") is recorded as
**pending** in roadmap §3, so I checked whether it gates this milestone the way
`T-3` gated 4.6. It does not:

| | 4.6 | 4.10 |
|---|---|---|
| Dependency column | "4.3 merged; **`T-3` accepted**" | "4.5, 4.8 merged" |
| Named `DS-DEC` | `DS-DEC-004`, due "Before 4.6" | **none in the register** |
| Effect of the transfer | authorises change **outside** the builder | **reduces** scope |

`T-4` is referenced in 4.10's *Spec/Owns* column as context, not as an entry
condition. It also narrows scope, so structure-only is the conservative
reading under either resolution — and `A-3` makes a real instance impossible
regardless. **Proceeded, with the reasoning recorded here rather than assumed.**

## 2. `P4-6` — RF4 refuses every declaration today, proven both ways

The roadmap's own warning governs how this had to be tested:

> *"A test that passes because the feature is missing is worthless; it must
> fail if RF4 stops refusing."*

So the proof runs in **both directions**:

| Direction | Result |
|---|---|
| Today's evidence (`OFFLINE_CORPUS` meet) | **RF4 refuses**, naming `PROVENANCE` |
| Same declaration, meet raised to `LIVE_FORWARD` | **RF4 passes** |
| Meet at `MAINNET` | passes — the floor is *at or above* the forward lane |
| Meet at `UNKNOWN` | refuses |
| `LIVE_FORWARD` but weak certification/readiness | **passes** — see §4 |

The refusal today is not a limitation of this module. It is §15.2 working:
all 148 labels are `Context`, so every computed meet caps at `OFFLINE_CORPUS`,
strictly below the forward-lane floor.

TV3 confirms the refusal is load-bearing: lowering the floor to
`OFFLINE_CORPUS` **failed 4 tests**.

## 3. RF4 remains the single authority on admissibility

This module **implements no admissibility check.** It supplies the floor and
routes it into WBS 4.4's existing `validation.check_tier_c()`, where RF4
already lives. Re-deriving the comparison would create a second authority free
to drift — invisibly, since both would appear to be "checking admissibility".

Guarded three ways:

- `verify_rf4_is_sole_authority()` parses this module's own AST at runtime and
  fails on any direct `satisfies()` call or bare dimension comparison;
- an AST test asserts neither `satisfies` nor `meet` is imported;
- `check_training_admissibility()` returns **only** `RF4` findings, so a caller
  cannot mistake an unrelated Tier C finding for an admissibility refusal —
  tested by constructing a declaration that produces both.

## 4. The floor constrains the lane, and only the lane

§15.2 requires *"an admissibility floor at or above the forward lane"* and says
nothing about the other four dimensions. `training_floor()` therefore sets
`Provenance.LIVE_FORWARD` and leaves certification, readiness, maturity and
integrity at `UNKNOWN` — the lattice's bottom rung, which constrains nothing.

Raising them would invent governance the ADR did not write, and would make a
Training Dataset refusable for reasons §15.2 never stated.
`test_the_refusal_is_about_provenance_only` proves a forward-lane dataset with
`Certification.UNKNOWN` and `Readiness.NOT_READY` still clears the floor.

## 5. Three datasets, not three slices (§15.1)

> *"Train, validation, and holdout are **three separate sealed datasets**, not
> three slices of one."*

So the module registers *partitions of a sibling group*, each an independently
sealed and pinned dataset. Non-overlap is verified **across siblings at
registration**, which is what makes leakage *"structurally impossible, not a
review finding"*.

Overlap is **inclusive** — two partitions sharing a single day overlap,
because one leaked observation is still leakage. Adjacent windows
(`…12-31` / `01-01`) are permitted; different sibling groups may overlap freely.

## 6. The governed lifecycle is the only path

`register_partition()` refuses, by name, at every step that was skipped:

```
declare → resolve → validate → seal → materialise → pin → register
           ↑unsealed        ↑unsealed      ↑NotPinnedError
```

`test_governed_lifecycle_is_the_only_path` walks the whole chain, asserting a
refusal at each incomplete stage and success only at the end.

The **pin** requirement is §15's, not an invention: a Training Dataset's
materialisation is *"required and pinned"* and its defining property is
exact-bytes recoverability — which an unpinned dataset cannot offer, because
there is nothing to recover *to*.

## 7. DG-7 — holdout is single-use

> *"Holdout partitions are single-use. Consumption is recorded against the
> consuming decision."*

The second attempt raises `HoldoutSpentError` **naming the decision that spent
it** — the point is not to hide the first use but to make the second
impossible. Consumptions are append-only for the same reason WBS 4.9's
use-count is derived: a consumption that could be deleted would make
"single-use" unenforceable.

Partitions are immutable, and the reason is specific:
`test_role_cannot_be_relabelled_after_consumption` proves a spent holdout
cannot be relabelled `train` once its result is known.

## 8. Verification

### §4 TV — 65 tests, `tests/test_training.py`

| Group | n | Covers |
|---|---|---|
| **`P4-6` RF4 refusal** | 7 | refused today, **passes at forward lane**, mainnet, unknown, **provenance-only** |
| Floor | 3 | forward lane, constrains nothing else, immutable |
| **RF4 sole authority** | 4 | runtime AST guard, no `satisfies`/`meet` import, delegation, only-RF4 findings |
| Partition structure | 10 | three roles, siblings by hash, ordering, duplicate role, idempotent, completeness |
| **Non-overlap** | 6 | overlap refused, **single shared day**, adjacent allowed, cross-group allowed |
| Pin required | 6 | unpinned refused, materialised-but-unpinned refused, **full lifecycle chain** |
| **DG-7 single-use** | 8 | first recorded, second refused, **names the spender**, non-holdout roles, append-only |
| Immutability | 4 | no update/delete, **role immovable after consumption**, DB `CHECK` |
| TV3 guards | 5 | non-vacuity of RF4, overlap, pin, single-use |
| Regression | 11 | RF1–RF6, materialisation, L1, **Experiment Datasets orthogonal (§15.3)**, citations, sealing, chain, result invariance |

### §4 TV3 — sabotage

| Sabotage | Failures | Verdict |
|---|---|---|
| Floor lowered to `OFFLINE_CORPUS` | **4** | RF4 refusal is load-bearing |
| Non-overlap check neutered | **3** | load-bearing |
| Pin requirement neutered | **4** | load-bearing |
| Holdout single-use neutered | **3** | load-bearing |

**One sabotage of mine was ineffective and was redone.** My first attempt at
the pin sabotage inserted a stub *before* the real check, so the check still
ran and the suite passed — which would have been a false clean. Corrected to
genuinely disable it; the suite then failed 4. Reported because a TV3 pass that
proves nothing is worse than no TV3 at all.

### §5 REG

| Environment | Result | Prior |
|---|---|---|
| `btc_bot` | **3142 passed, 19 skipped** | 3077 + 65 |
| `base` | **2693 passed, 62 skipped** | 2628 + 65 |

**Zero pre-existing files modified.** 4.10 is two new files.

### Invariants re-verified

RF1–RF6 unchanged · registry phase untouched · **materialisation still
byte-deterministic** · **L1 still verifies** · **Experiment Datasets still work
on a Training partition** (§15.3: the roles are orthogonal) · citation graph
consistent · sealed immutability enforced · receipt chain valid · **result
invariance 10/10 unchanged**.

## 9. Production verification

`overall INFO` · 0 non-OK · `btcbot` + `btcbot-api` active · 6 timers · **0
failed units** · collection `quotes=66/66` · 5 forward signals at `2026-08-07` ·
`report.py --check` OK (35: 9/22/4) · chain `ok=True, 26 receipts` · three DBs
integrity **ok/ok/ok** · disk 6.8 GB free (76%).

**No production change was made.**

## 10. Technical debt and hazards

| # | Item | Severity | Note |
|---|---|---|---|
| **TD-4.10-1** | `computed_meet` is optional on `register_partition()`; omitting it skips the RF4 gate | 🟠 High | Deliberate — the meet is caller-supplied everywhere in this builder (`TD-4.4-3`) and forcing it would require this module to recompute admissibility, creating the second authority §3 forbids. But a caller that omits it registers a partition whose floor was never checked. **The honest fix is 4.4's `TD-4.4-3`**: derive `InputAdmissibility` from resolved references, then make the argument mandatory |
| **TD-4.10-2** | Non-overlap is verified between *declared windows*, not materialised content | 🟡 Medium | Two partitions could declare disjoint windows yet contain overlapping rows if a provider ignored the window. §15.1 says "verified at seal", which the declared window satisfies; content-level verification would need the materialiser and is a stronger claim than the ADR makes |
| **TD-4.10-3** | A sibling group's completeness is never enforced before use | 🟡 Medium | `verify_group_complete()` reports, but nothing requires all three roles to exist before a holdout is consumed. A model could cite an incomplete split |
| **TD-4.10-4** | `windows_overlap` treats an unparseable date as non-overlapping | 🟢 Low | Fails open on malformed input. Tier A already refuses malformed windows upstream, so unreachable through the governed path |
| **HZ-4.10-1** | No Training Dataset can exist until forward-lane labels do | 🟢 Low | **Expected and correct** (`A-3`, `T-4`). Recorded so the empty table is not later mistaken for a defect |
| **HZ-4.10-2** | Holdout consumption is recorded but nothing **alerts** on it | 🟡 Medium | Mirrors `HZ-4.8-1` and `HZ-4.9-1`. Spending a holdout is a one-way, high-consequence act; it is queryable but not noticed |

## 11. Files changed

| File | Change |
|---|---|
| `research/dataset_builder/training.py` | **New** — partition roles, forward-lane floor, non-overlap, DG-7 |
| `tests/test_training.py` | **New** — 65 tests |

**Zero files changed under `research/forward_evidence/`, `research/evidence/`
or `research/reports/`.**

## 12. Reviewer Checklist (RC)

*Pending — Rohith.*

- [ ] **RC1** — Milestone corresponds to WBS 4.10 exactly; no 4.11 scope
- [ ] **RC2** — No file under `docs/architecture/` touched
- [ ] **RC3** — "Files Changed" matches `git status` exactly
- [ ] **RC4** — At least two tests read in full
- [ ] **RC5** — Full-suite counts reproduced in both environments
- [ ] **RC6** — Rollback lever confirmed (two new files, additive)
- [ ] **RC7** — **The `T-4` reasoning (§1) is accepted** — proceeding without ratification, because it is not an entry condition and reduces scope
- [ ] **RC8** — **`P4-6` accepted as satisfied (§2)** — RF4 refuses today, proven in both directions
- [ ] **RC9** — **The provenance-only floor (§4) is accepted**
- [ ] **RC10** — **`TD-4.10-1` accepted** — `computed_meet` is optional, so a caller can skip the RF4 gate; the fix belongs with `TD-4.4-3`
- [ ] **RC11** — Sign-off

  **Reviewer:** _______________  **Date:** _______________
