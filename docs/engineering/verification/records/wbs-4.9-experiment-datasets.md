# WBS 4.9 — Experiment Datasets and Use-Counting — Verification Record

| Field | Value |
|---|---|
| **Milestone** | WBS 4.9 — Experiment Datasets, use-counting (Sprint D6) |
| **Governing ADR** | [`ADR-I012`](../../../architecture/adr/ADR-I012-research-dataset-builder.md) §7.1 · §9 · §15.3 · DG-6 · **DG-8** · resolves **PS6** |
| **Also discharges** | `ADR-I011` **LG7 / DI8** (scope transfer `T-5`) |
| **Roadmap** | [`phase_4_dataset_builder_roadmap.md`](../../phase_4_dataset_builder_roadmap.md) v1.0.2 §4.1 row 4.9 |
| **Date** | 2026-08-08 |
| **Implementer** | Claude Opus 5 |
| **Reviewer** | *(pending — Rohith)* |
| **Depends on** | 4.5 (sealing), 4.8 (materialisation), and 4.1–4.7 |
| **Governance** | None required. `DS-DEC-006` (retro-binding the 70 historical runs) remains **Owed** and untouched |

**Pre-milestone production gate:** health `overall INFO` / 0 non-OK · Telegram
delivered · collection `66/66` · 5 forward signals at `2026-08-07` ·
`backup --all --dry-run` exit 0 · 0 failed units · `report.py --check` OK ·
chain valid (26). All five gates passed.

---

## 1. What PS6 actually costs, and what DG-8 buys

PS6, in the ADR's words:

> *"Trial context is not carried. The trial denominator is frozen and correct,
> but a dataset reused across trials records nothing about that reuse."*
> **Consequence:** *"Multiple-comparisons burden is tracked at the family level
> and invisible at the substrate level."*

And why it matters:

> *"**DG-8 is the multiple-comparisons defence at the substrate layer.** The
> platform already freezes its trial denominator by governance session. DG-8
> makes substrate reuse legible so the denominator can be **checked** rather
> than merely declared."*

The operative word is **checked**. That single distinction drove every design
decision below.

## 2. The use-count is derived, never stored

**A stored counter can be decremented, reset, or "corrected". Rows in a
write-once table cannot.** If reuse is to be *checkable* rather than *declared*,
the count must be a consequence of evidence, not an assertion about it.

So `use_count()` is a `SELECT COUNT(*)` over an append-only table, and there is
deliberately **no** `set_use_count()`, `reset_uses()`, or `decrement()` anywhere
in the module. Three tests enforce the absence:

- no `use_count` column exists on `experiment_datasets` (asserted via `PRAGMA table_info`)
- no function name containing `set_use`, `reset` or `decrement` exists (AST)
- `UPDATE`/`DELETE` on `dataset_uses` are blocked at the **database** layer

TV3 makes the point sharply: adding a stored `use_count` column **failed 22
tests**. The absence is the design, not an omission.

## 3. The denominator is declared, not invented

The Phase B battery freezes `n_trials = 34` — but that number belongs to *that
battery*, not to this module. Hard-coding it would be inventing governance and
would silently break the first registration with a different denominator.

A trial registration declares its own size; the module enforces **internal
consistency**:

| Rule | Refusal |
|---|---|
| position within `1..size` | `DenominatorError` naming the range |
| one declared size per registration | refuses a mid-registration change |
| no two experiments at one position | names the occupying dataset |

All three are enforced in Python **and** at the database layer (`CHECK`
constraints plus `UNIQUE (trial_registration, denominator_position)`), so a
direct `sqlite3` insert cannot bypass them. An AST test asserts `34` is not
hard-coded anywhere.

Registrations may legitimately differ in size from one another — tested.

## 4. Immutability protects the position, not just the row

An Experiment Dataset is immutable (§7.1). The reason is specific:
**rebinding a trial context after the fact would let a denominator position be
moved once the result was known** — precisely the abuse DG-8 exists to prevent.
`test_position_cannot_be_moved_after_a_result` proves the database refuses it
even after a use has been recorded.

Registration is **idempotent on identical context** (the experiment id is the
content hash of dataset + registration + position), because a second row for
the same context would inflate the very reuse count DG-8 exists to make
trustworthy.

## 5. DI8 — visible, not refused

`ADR-I011` DI8: *"A dataset citing Promotion Labels records that consumption,
so the same forward evidence cannot silently support repeated promotion
attempts."*

`promotion_uses()` surfaces every promotion consumption of a substrate, across
all trials. **Repeated promotion is not refused** — §13.1 is explicit that
*"Promotion — Human — Builder role: supply use-counted evidence; never
decide."* What this makes impossible is doing it **silently**. That distinction
is tested by name.

## 6. Scope — R9 held

`ADR-I012` R9: *"Nothing in the audited platform cites a dataset today ...
**Out of scope here.** This ADR specifies the object; wiring it into evaluation
is gated and separate."*

This milestone builds the **object**. No evaluation pipeline, forward-evidence
script, or historical governance artifact was touched, and `DS-DEC-006` remains
Owed and untouched.

## 7. Verification

### §4 TV — 72 tests, `tests/test_experiments.py`

| Group | n | Covers |
|---|---|---|
| Build / scope | 5 | no `src` import, **no setter exists (AST)**, **34 not hard-coded**, bindable phases == citable phases |
| Registration | 11 | binds sealed, content-addressed id, **idempotent**, unsealed/resolved refused, superseded permitted, LR4, schema guard |
| **Denominator integrity** | 11 | bounds, boundaries, duplicate position, mid-registration size change, cross-registration independence, **DB-layer CHECK** |
| **Use-count integrity** | 12 | starts at zero, increments, **derived not stored**, DB-layer append-only, `consuming_ref` required both layers, purpose filter, repeat by same consumer counted |
| **Substrate reuse (DG-8)** | 5 | single use is not reuse, **cross-trial reuse visible**, same-trial reuse, aggregate totals |
| **DI8 promotion** | 4 | recorded, **repeated promotion visible not refused**, evaluation excluded, cross-trial aggregate |
| Immutability | 4 | no update/delete, **position immovable after a result**, no orphans |
| Lineage / context | 5 | §9's four required fields, substrate citations, ordering |
| TV3 guards | 5 | non-vacuity of count, reuse, denominator, phase, promotion filter |
| Regression | 10 | RF1–RF6, phase untouched, citations, sealing, **materialisation still deterministic**, chain, result invariance |

### §4 TV3 — sabotage

| Sabotage | Failures | Verdict |
|---|---|---|
| Use-count made a stored column | **22** | the derived-count design is load-bearing |
| Duplicate-position guard neutered (both layers) | **2** | load-bearing |
| Citable-phase guard neutered | **3** | load-bearing |

Revert restored 72 passed.

### §5 REG

| Environment | Result | Prior |
|---|---|---|
| `btc_bot` | **3077 passed, 19 skipped** | 3005 + 72 |
| `base` | **2628 passed, 62 skipped** | 2556 + 72 |

**Zero pre-existing files modified.** 4.9 is two new files.

### Invariants re-verified

RF1–RF6 unchanged · registry phase untouched by registration · citation graph
bidirectionally consistent · sealed immutability still enforced by direct
`sqlite3` · **materialisation still byte-deterministic with the new tables
present** · declaration hash stable · receipt chain valid · **result invariance
10/10 unchanged**.

## 8. Production verification

`overall INFO` · 0 non-OK · `btcbot` + `btcbot-api` active · 6 timers · **0
failed units** · collection `quotes=66/66` · 5 forward signals at `2026-08-07` ·
`report.py --check` OK (35: 9/22/4) · chain `ok=True, 26 receipts` · three DBs
integrity **ok/ok/ok** · disk 6.8 GB free (76%).

**No production change was made.**

## 9. Technical debt and hazards

| # | Item | Severity | Note |
|---|---|---|---|
| **TD-4.9-1** | `consuming_ref` is an opaque string; nothing validates it resolves to a real run | 🟡 Medium | Deliberate — R9 keeps evaluation wiring separate, and validating a run id would require the builder to know the evaluation layer. A dangling ref is currently possible and undetectable |
| **TD-4.9-2** | `trial_registration` is a free-text label with no registry behind it | 🟠 High | Two registrations could differ only by typo and be treated as independent, quietly inflating the effective denominator — the exact failure DG-8 defends against, arrived at from the other side. A trial registry is a governance object this milestone had no mandate to create |
| **TD-4.9-3** | Gaps in a denominator are reported as information, not error | 🟢 Low | Correct while trials are still being bound, but nothing later asserts the denominator was fully populated before results were drawn |
| **TD-4.9-4** | `USE_PURPOSES` is a module constant, not governed data | 🟢 Low | Same shape as `TD-4.6-2`/`TD-4.7-3` |
| **HZ-4.9-1** | Nothing **alerts** on cross-trial substrate reuse | 🟠 High | `substrate_reuse().spans_multiple_trials` makes it queryable, but no report, healthcheck or governance check surfaces it. DG-8 makes reuse *legible*; it does not yet make it *noticed*. Mirrors `HZ-4.8-1` |
| **HZ-4.9-2** | A dataset may be bound to a trial and then never used | 🟡 Medium | A registered-but-unused position still occupies the denominator. Whether that inflates or deflates the multiple-comparisons count is a **statistical governance question**, not a builder one — flagged rather than decided |

## 10. Files changed

| File | Change |
|---|---|
| `research/dataset_builder/experiments.py` | **New** — Experiment Datasets, use-counting, reuse visibility |
| `tests/test_experiments.py` | **New** — 72 tests |

**Zero files changed under `research/forward_evidence/`, `research/evidence/`
or `research/reports/`.**

## 11. Reviewer Checklist (RC)

*Signed 2026-08-08 — see below.*

- [x] **RC1** — Milestone corresponds to WBS 4.9 exactly; no 4.10+ scope
- [x] **RC2** — No file under `docs/architecture/` touched
- [x] **RC3** — "Files Changed" matches `git status` exactly
- [x] **RC4** — At least two tests read in full
- [x] **RC5** — Full-suite counts reproduced in both environments
- [x] **RC6** — Rollback lever confirmed (two new files, additive)
- [x] **RC7** — **The derived-not-stored use-count (§2) is accepted** *(basis: `OD-7`, Group D/E — confirmed 2026-08-08 as legitimately covered by content, defended by TV3)*
- [x] **RC8** — **The declared-not-invented denominator (§3) is accepted** — `34` is not hard-coded *(basis: `OD-7`, Group D/E — confirmed 2026-08-08: protects the frozen `n_trials=34` constant from re-invention)*
- [x] **RC9** — **DI8's visible-not-refused reading (§5) is accepted** *(basis: `OD-7`, Group D)*
- [x] **RC10** — **`TD-4.9-2` acknowledged** — `trial_registration` is free text; a typo creates a phantom independent trial. A trial registry would need its own decision *(basis: `OD-3`, Group G)*
- [x] **RC11** — **`HZ-4.9-2` acknowledged** — whether an unused bound position counts toward the denominator is a statistical governance question, not a builder one *(basis: `OD-3`, Group G — same item also referenced in Group B; low-severity either way)*
- [x] **RC12** — Sign-off

  **Reviewer:** Rohith Suresh  **Date:** 2026-08-08
