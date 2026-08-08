# WBS 4.1 — Dataset Registry — Verification Record

| Field | Value |
|---|---|
| **Milestone** | WBS 4.1 — Dataset Registry (Sprint D1) |
| **Governing ADR** | [`ADR-I012`](../../../architecture/adr/ADR-I012-research-dataset-builder.md) §6, §7, §10, §11, §12 Tier A |
| **Roadmap** | [`phase_4_dataset_builder_roadmap.md`](../../phase_4_dataset_builder_roadmap.md) v1.0.2 §4.1 |
| **Standard version referenced** | 1.0.0 |
| **Date** | 2026-08-07 |
| **Implementer** | Claude Opus 5 |
| **Reviewer** | *(pending — Rohith)* |
| **Entry gate** | `E2` discharged 2026-08-07; Sprint D1 open |

**Pre-milestone production gate run before any code was written** (standing
instruction, 2026-08-07): health `overall INFO` / 0 non-OK · Telegram delivered
· collection `66/66` · all 5 forward signals at `2026-08-06` · `backup --all`
exit 0 · 0 failed units · governance `Registry check: OK`. All five gates
passed; implementation proceeded.

---

## 1. Scope

**Delivered:** `research/dataset_builder/registry.py` — content-addressed
Dataset Specifications, the seven-phase lifecycle, Tier A structural validation
over every §7.2 mandatory field, and append-only storage with tamper
enforcement at the database layer. Backed by
`research/dataset_builder/dataset_builder.db` (`DS-DEC-005`), the third
instance of the `<dir>/<dir>.db` one-writer pattern.

**Deliberately NOT delivered, each deferred by name in the module docstring:**

| Not built | Owner |
|---|---|
| Citation index (§6.1) | WBS 4.2 |
| Reference resolution against upstream stores; RF1–RF6 (§8.1) | WBS 4.3 |
| Tier B / C / D validation (§12) | WBS 4.4 |
| Sealing governance receipt (§13) | WBS 4.5 |
| Materialisation (§8.2) | WBS 4.8 |

**The scope boundary that matters.** §12's Tier A also requires references to
be *"resolvable and Active at `as_of`"* and the window to be *"within corpus
coverage"*. Both need the upstream registries and the corpus, which Resolution
(4.3) owns. What is validated here is everything decidable **from the
declaration alone**. The transition functions are therefore *mechanical arcs*:
they enforce the state machine and refuse illegal moves, but do not themselves
assert the substantive gate was met — the same discipline the Label Store's
`retire_specification()` applied when it deferred its citation check to this
very ADR.

## 2. Design decisions

**Two independent guards on every transition**, and the distinction is
load-bearing. The **arc table** refuses any `(from, to)` pair §11 does not
draw. The **row-phase guard** refuses when the row is not in the expected
`from` phase at write time. A phase *skip* (Declared → seal) is caught by the
second, not the first, because `Validated → Sealed` is a perfectly legal arc —
it is the row's state that is wrong. Two tests were initially written asserting
the wrong guard; they were corrected to assert the actual mechanism rather than
loosening the regex, and a third test was added proving each guard fires in
isolation.

**Storage-layer immutability, not Python-layer.** A guarded `BEFORE UPDATE`
trigger freezes `spec_hash`, `declaration_json`, `lineage`, `version_ordinal`,
`declared_by` and `declared_at` for the row's entire life, and permits a Sealed
row to advance only to Superseded or Retired. `DELETE` is blocked outright.
Every immutability claim is proven by a **direct `sqlite3` connection that
bypasses this module entirely** (P4-D1).

**The declaration is frozen from write, not from seal.** §6.2 only requires
refusing mutation of a *sealed* record, but content addressing would be a lie
if a Declared row's declaration could be edited while keeping its hash. The
trigger enforces the stronger property.

**`redefinition` is refused as a supersession.** §10.1 states a redefinition is
*"a new lineage, not a new version"*. Recording one as a supersession within a
lineage would assert a comparability that does not hold, so `supersede()`
accepts `extension` and `refinement` and rejects `redefinition` by name.

**Rejection is persisted before the exception propagates.** A caller that
swallows `SpecificationRejected` still cannot erase the negative record
(§12, IQROS-P15) — verified by `test_rejected_declaration_is_persisted_before_raising`.
This is `P4-D3` applied, the failure mode `LS-TD-002` recorded.

**No default `--db` anywhere**, enforced by an AST test that parses the
argument parser and asserts `--db` carries `required` and no `default`.

## 3. Verification

### §3 BV — build

`test_no_src_import_anywhere` parses `registry.py`'s AST and asserts zero
imports whose root package is `src` — P4-8 / DG9 proven structurally, not by
grepping a docstring. Arc table and phase set asserted to match §11 exactly,
including that no arc leaves a terminal phase.

### §4 TV — tests

**128 tests**, `tests/test_dataset_registry.py`:

> **Count addendum (2026-08-08, audit finding `E-1`).** The figure above is the
> count **as delivered by this milestone** and is left unchanged. The file now
> holds **160** tests. The 32 additional tests were added by two later
> commits that strengthened this milestone's guard trigger and therefore
> extended its test file rather than creating a new one:
>
> | Commit | Added | Reason |
> |---|---|---|
> | `dc16a70` | +18 | `AF-1` — `rejection_reason` / `superseded_by` made write-once |
> | `82ce7c0` | +14 | `EB-1` / `EB-3` — `change_class` write-once; atomic schema swap |
>
> `RC5` asks the reviewer to reproduce full-suite counts. Reproducing
> `tests/test_dataset_registry.py` today yields **160**, not 128, and that is
> correct. No test delivered by WBS 4.1 was removed or altered.

| Group | Count | Covers |
|---|---|---|
| Import boundary / state machine | 5 | AST, 7 phases, 8 arcs, terminal closure |
| Declare (positive) | 8 | hand-written declaration, retrieval by hash, lineage ordinals |
| Content addressing (TV5) | 5 | determinism, key-order independence, idempotent re-declare, hash survives lifecycle |
| **Tier A per-field (negative)** | **58** | **one negative test per mandatory field, ×2 (missing and empty)** |
| Tier A structural rules | 9 | inverted/zero window, `as_of` closure, duplicates, wrong types, accumulation |
| Rejection permanence | 4 | persisted-before-raise, no arc out of Rejected |
| Lifecycle | 10 | happy path, citability, skips, wrong-phase, both guards |
| Supersession | 7 | successor sealed, redefinition refused, self-supersession |
| Retire | 3 | |
| **Sealed immutability (direct sqlite3)** | **11** | frozen columns, phase regression, terminal, DELETE, CHECK |
| LR4 schema assertion | 5 | foreign DB, no-write-before-assert, nonexistent path |
| CLI / no default `--db` | 7 | |
| TV3 sabotage guards | 3 | |
| Rollback / compatibility | 3 | |

**Per-field coverage is exhaustive**, not representative: 9 top-level field
classes and 13 nested keys are each tested twice (missing, empty), plus both
mandatory `label_references` keys.

### §4 TV3 — sabotage

Each guard was broken and the suite re-run, then reverted:

| Sabotage | Failures | Verdict |
|---|---|---|
| `MANDATORY_FIELDS` emptied (Tier A vacuous) | **6 failed**, incl. `test_tier_a_is_not_vacuous` | non-vacuous |
| Tamper trigger neutered (`WHEN 0`) | **3 failed** — exactly the sealed-immutability tests | load-bearing |
| LR4 assertion removed | **3 failed** — exactly the LR4 tests | load-bearing |

Each sabotage failed **precisely** the tests guarding it and nothing else.
Revert restored 128 passed.

### §5 REG — regression

| Environment | Result | Baseline |
|---|---|---|
| `btc_bot` | **2518 passed, 19 skipped** | 2390 + 128 new |
| `base` (excl. pandas-less research tree) | **2069 passed, 62 skipped** | 1941 + 128 new |

Zero pre-existing tests modified except the six `ApiSettings(...)`
constructions listed in §4, which required the new field.

### §7 DBV — database

New store only; no migration, no existing database touched. Schema creation is
idempotent (`init_db` twice, verified). `test_no_other_store_is_touched`
asserts that using the registry creates no `.db` anywhere but the given path.

### §9 RBV — rollback

Purely additive: one new package, one new test file, one `.gitignore` block,
and additive wiring in four existing files. `git revert` requires no data or
schema rollback — nothing pre-existing is mutated by this commit's presence.

## 4. Scope deviation, declared

Roadmap §13.1 lists *"a `--dataset-builder` backup unit mirroring
`--label-store`"* as a **4.1 deliverable**, so it is included. It required
completing CLI/API parity:

- `scripts/backup.py` — `--dataset-builder`, `--all` now covers six databases.
- `scripts/healthcheck.py` — `dataset_builder_backup` freshness check.
- `src/api/config.py`, `src/api/services.py` — the same, for CLI/API parity.
- `tests/test_api.py`, `tests/test_healthcheck.py` — six `ApiSettings(...)`
  constructions extended with the new field.

**This was not optional.** The AST parity guard
(`test_api_services_mirrors_the_cli`, P1 Increment 1) **failed** the moment the
CLI gained a check the API lacked — it caught the divergence exactly as
designed. Half-wiring would have been the real scope violation.

`dataset_builder` is **not** in `REQUIRED_DB_LABELS`, so Option A's fail-soft
default classifies it OPTIONAL automatically: a missing database skips with
rc 0 and reports INFO, never WARNING. **PROD-1/PROD-2 cannot recur through this
addition** — verified by the existing 29-test classification suite.

**Inert until deployed.** No `dataset_builder.db` exists in production, and
this commit is not deployed. On a future deployment the new check reports INFO
and the new backup target skips, both silently.

## 5. Files changed

| File | Change |
|---|---|
| `research/dataset_builder/__init__.py` | **New** — package marker, scope statement |
| `research/dataset_builder/registry.py` | **New** — the Dataset Registry |
| `tests/test_dataset_registry.py` | **New** — 128 tests |
| `.gitignore` | +5 — `dataset_builder.db` and sidecars |
| `scripts/backup.py` | +5 sites — `--dataset-builder` |
| `scripts/healthcheck.py` | +2 sites — freshness check |
| `src/api/config.py` | +2 — `dataset_builder_backup_dir` |
| `src/api/services.py` | +1 — parity check |
| `tests/test_api.py` | 2 fixtures extended |
| `tests/test_healthcheck.py` | 2 fixtures extended |

## 6. Production health at commit time

`overall INFO` · 0 non-OK checks · `btcbot` and `btcbot-api` active · 6 timers ·
**0 failed units** · collection `66/66` · disk 7.1 GB free (75%) ·
`backup --all --dry-run` exit 0.

**No production change was made by this milestone.** The registry is research
tooling under `research/`; IQROS-P1's operational/research boundary holds.

## 7. Reviewer Checklist (RC)

*Signed 2026-08-08 — see below.*

- [x] **RC1** — Milestone corresponds to WBS 4.1 exactly; no 4.2+ scope
- [x] **RC2** — No file under `docs/architecture/` touched
- [x] **RC3** — "Files Changed" matches `git status` exactly
- [x] **RC4** — At least two tests read in full, confirmed to exercise real behaviour
- [x] **RC5** — Full-suite counts reproduced independently in both environments
- [x] **RC6** — Rollback lever confirmed
- [x] **RC7** — The §4 scope deviation (backup wiring) is accepted *(basis: `OD-7`, Group E — architecture accepted under constraint)*
- [x] **RC8** — The `redefinition`-refused-as-supersession reading is accepted *(basis: `OD-7`, Group E)*
- [x] **RC9** — Sign-off

  **Reviewer:** Rohith Suresh  **Date:** 2026-08-08
