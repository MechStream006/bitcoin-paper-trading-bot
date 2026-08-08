# WBS 4.2 — Bidirectional Citation Index — Verification Record

| Field | Value |
|---|---|
| **Milestone** | WBS 4.2 — Bidirectional Citation Index (Sprint D1) |
| **Governing ADR** | [`ADR-I012`](../../../architecture/adr/ADR-I012-research-dataset-builder.md) §6.1, §6.2; DG-5 |
| **Scope transfer** | `T-6` — `ADR-I010` §11 / `ADR-I011` LG5 retirement checks land here |
| **Roadmap** | [`phase_4_dataset_builder_roadmap.md`](../../phase_4_dataset_builder_roadmap.md) v1.0.2 §4.1 row 4.2 |
| **Date** | 2026-08-07 |
| **Implementer** | Claude Opus 5 |
| **Reviewer** | *(pending — Rohith)* |
| **Depends on** | WBS 4.1 (merged, `d6b020d`) |

**Pre-milestone production gate, run before any code:** health `overall INFO` /
0 non-OK · Telegram delivered · collection `66/66` · 5 forward signals at
`2026-08-06` · `backup --all --dry-run` exit 0 · 0 failed units ·
`report.py --check` OK · chain valid (26). All five gates passed.

---

## 1. What was built

`research/dataset_builder/citation_index.py` — the bidirectional index §6.1
calls *"the Registry's most consequential function"*:

| Direction | Question | Used for |
|---|---|---|
| **Forward** | what does this dataset cite? | rematerialisation, provenance closure |
| **Reverse** | what cites this input? | blast radius, retirement refusal, superseded-input propagation |

Stored as **one table, not two**. Both directions are projections of the same
edge set, so they cannot disagree — the alternative (two tables kept in sync)
makes inconsistency possible in order to make it detectable, which is backwards.
`verify_bidirectional_consistency()` verifies the property rather than assuming it.

## 2. The design decision that carries the milestone

**Which citations block retirement.** `ADR-I010` §11 says *"cited by any
**sealed** artifact"*. A **Declared** dataset naming a feature does **not** pin
it — that dataset is not citable (§11: *"nothing may reference a dataset before
seal"*) and may still be Rejected. Retiring a feature because an abandoned draft
mentioned it would be wrong.

So `cited_by()` and `is_cited()` default to `CITABLE_PHASES` = {Sealed,
Superseded} — Superseded included because §10.2 keeps a superseded dataset
citable by its existing citations. `include_phases` widens this for blast-radius
work, and `blast_radius()` deliberately spans **all** phases: when a corpus
revision is corrected, a Declared dataset depending on it still needs
re-resolution before it can seal.

Both sides of that gate are tested, and TV3 proves the gate is load-bearing
rather than incidental.

## 3. Dependency direction — the constraint that shaped the wiring

Roadmap `J7` authorises editing both upstream stores. The naive implementation
has `feature_store/registry.py` import `dataset_builder.citation_index` — which
**inverts the dependency arrow**: the builder is downstream of both stores, and
a store importing its own consumer creates a cycle in the layering.

**Resolution: inject the authority, never import it.** Each store's
`retire_specification()` gains one optional parameter:

```python
retire_specification(db_path, spec_hash, from_phase, citation_check=None)
```

`citation_check(spec_hash) -> bool` returns True when cited; the store refuses
with `SpecificationCited`. `citation_index.make_citation_check(db, kind)` builds
that callable. **Neither store references the Dataset Builder anywhere** —
asserted by an AST test over both files, not by inspection.

`citation_check=None` preserves the pre-4.2 signature exactly. That is a
backward-compatibility choice, not an escape hatch: before 4.2 there was
literally no index to consult, which is what both stores' original docstrings
said.

## 4. Obligations closed

| Obligation | Source | Closed by |
|---|---|---|
| *"Retirement requires zero citations"* | `ADR-I010` §11 | `feature_store.retire_specification(citation_check=…)` |
| *"A Specification cited by a sealed artifact can never be retired"* | `ADR-I011` LG5 / DI7 | `label_store.retire_specification(citation_check=…)` |
| *"A dataset cited by any run, model or decision can never be retired"* | `ADR-I012` DG-5 | `assert_retirable()`, `is_cited()` |
| Partition-revision citation constraint | `ADR-I009` §15.1 | `cited_kind='evidence'` edges |

Both stores shipped with docstrings deferring this check to *"whichever future
milestone builds the index"*. Those docstrings are now replaced by the check.

## 5. Orphan prevention, both directions

| Direction | Orphan shape | Prevention |
|---|---|---|
| **Forward** | edge whose citing dataset is absent | FK to `dataset_specifications` **plus** an explicit Python check so the error names the dataset rather than surfacing a bare constraint |
| **Reverse** | edge whose `cited_id` is blank — nothing could ever match it | refused at `record_citation()` and at every `index_declaration()` entry point |
| **Audit** | either, in a restored or hand-edited database | `find_orphans()` and `verify_bidirectional_consistency()` — the invariant is *auditable*, not merely asserted by the code that wrote it |

## 6. Verification

### §4 TV — tests

**80 tests**, `tests/test_citation_index.py`:

| Group | n | Covers |
|---|---|---|
| Dependency direction | 4 | AST: no `src` import; **neither store imports the builder** |
| Recording | 7 | idempotent duplicates, unknown kind, blank id/hash, detail |
| Forward-orphan prevention | 4 | unknown dataset refused, nothing written, FK, audit |
| `index_declaration` | 9 | all three reference classes, maturity/revision detail, idempotency, blank/malformed refusals |
| Forward | 6 | what a dataset cites, filters, determinism |
| **Reverse** | 12 | **unsealed does not pin**, sealing binds, superseded still cites, retired stops, widening, blast radius |
| **Retirement refusal** | 9 | **both stores refuse while cited**, refusal precedes any write, uncited passes through, backward-compatible default |
| Append-only | 4 | UPDATE/DELETE blocked, citations survive rejection |
| Bidirectional consistency | 5 | forward↔reverse answerable, injected-orphan detection |
| CLI | 9 | no default `--db`, all four query modes |
| TV3 guards | 3 | non-vacuity of reverse edge, phase gate, orphan detector |
| Regression | 4 | 4.1 behaviour unchanged, schema additive |

### §4 TV3 — sabotage

| Sabotage | Failures | Verdict |
|---|---|---|
| Phase gate dropped in `cited_by` | **8** incl. `test_phase_gate_is_not_vacuous` | load-bearing |
| Retirement refusal neutered (`if False`) | **2** — exactly the feature-store refusal tests | load-bearing |
| Forward-orphan guard neutered | **2** — exactly the orphan tests | load-bearing |

Each failed precisely its own guards. Revert restored 208 passed (80 + 4.1's 128).

### §5 REG — regression

| Environment | Result | Prior baseline |
|---|---|---|
| `btc_bot` | **2598 passed, 19 skipped** | 2518 + 80 |
| `base` | **2149 passed, 62 skipped** | 2069 + 80 |

Zero pre-existing tests modified. The two upstream edits are purely additive —
a new exception class and one optional parameter each.

### §7 DBV

`citations` is additive to `dataset_builder.db`; no migration, no existing
database touched. `test_citation_schema_is_additive` asserts the registry's
tables are a strict subset before/after. Idempotent `init_db`.

### CLI/API parity

**Not applicable, and the reason is structural.** The citation index is research
tooling under `research/`; the operational API (`src/api/`) exposes runtime and
governance health, not the Dataset Builder. IQROS-P1's boundary means the API
must not read it. The `--dataset-builder` backup target added at 4.1 already
covers `dataset_builder.db`, so the new table is backed up with no wiring change.

## 7. Production verification (after implementation)

`overall INFO` · 0 non-OK · `btcbot` + `btcbot-api` active · 6 timers · **0
failed units** · collection `66/66` · disk 7.1 GB (75%) · `report.py --check`
OK (35 families: 9/22/4) · chain `ok=True, n_receipts=26` · trading/ledger/forward
integrity **ok/ok/ok**.

**No production change was made.** Research tooling only.

## 8. Technical debt and hazards

| # | Item | Severity | Note |
|---|---|---|---|
| **TD-4.2-1** | `citation_check=None` default means a caller can still retire without consulting the index | 🟡 Medium | Required for backward compatibility. WBS 4.11 owns retirement end-to-end and should make the check mandatory at that layer |
| **TD-4.2-2** | Citations are recorded by `index_declaration()`, which nothing calls automatically | 🟡 Medium | By design — WBS 4.3 (Resolution) is where declarations get bound and indexed as one step. Until then indexing is explicit |
| **TD-4.2-3** | `cited_id` is not validated against the upstream stores | 🟢 Low | Deliberate: resolvability is WBS 4.3's RF1. The index records what was cited, not whether it resolves |
| **HZ-4.2-1** | A caller passing the wrong `cited_kind` to `make_citation_check()` would check the wrong namespace | 🟢 Low | Kind is validated at construction, and `feature`/`label` hashes do not collide in practice |

## 9. Files changed

| File | Change |
|---|---|
| `research/dataset_builder/citation_index.py` | **New** — the index |
| `tests/test_citation_index.py` | **New** — 80 tests |
| `research/feature_store/registry.py` | `+SpecificationCited`, `retire_specification(citation_check=…)` |
| `research/label_store/registry.py` | `+SpecificationCited`, `retire_specification(citation_check=…)` |

## 10. Reviewer Checklist (RC)

*Signed 2026-08-08 — see below.*

- [x] **RC1** — Milestone corresponds to WBS 4.2 exactly; no 4.3+ scope
- [x] **RC2** — No file under `docs/architecture/` touched
- [x] **RC3** — "Files Changed" matches `git status` exactly
- [x] **RC4** — At least two tests read in full
- [x] **RC5** — Full-suite counts reproduced in both environments
- [x] **RC6** — Rollback lever confirmed (purely additive)
- [x] **RC7** — **The sealed-only citation reading (§2) is accepted** *(basis: `OD-7`, Group D)*
- [x] **RC8** — **The injected-authority wiring (§3) is accepted over an import** *(basis: `OD-7`, Group E)*
- [x] **RC9** — `TD-4.2-1`/`TD-4.2-2` accepted as deferred to 4.3/4.11 *(basis: `OD-3`, mechanical debt-deferral — confirmed 2026-08-08 as legitimately covered by content)*
- [x] **RC10** — Sign-off

  **Reviewer:** Rohith Suresh  **Date:** 2026-08-08
