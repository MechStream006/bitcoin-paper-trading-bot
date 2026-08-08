# WBS 4.11 — Retirement, Supersession, and `--as-of` Reconstruction — Verification Record

| Field | Value |
|---|---|
| **Milestone** | WBS 4.11 — retirement, supersession, `as_of` reconstruction (final Phase 4 milestone) |
| **Governing ADR** | [`ADR-I012`](../../../architecture/adr/ADR-I012-research-dataset-builder.md) §10.2 · §11 · §16 **L3** · **DG-5** |
| **Roadmap** | [`phase_4_dataset_builder_roadmap.md`](../../phase_4_dataset_builder_roadmap.md) v1.0.2 §4.1 row 4.11; exit criterion **`P4-7`** |
| **Resolves** | **`HZ-4.5-2`** (sealed ceiling can go stale) · discharges the §6.2 citation deferral `registry.retire()` recorded at WBS 4.1 |
| **Date** | 2026-08-08 |
| **Implementer** | Claude Opus 5 |
| **Reviewer** | *(pending — Rohith)* |
| **Depends on** | 4.2 (citation index), 4.5 (sealing), and 4.1–4.10 |
| **Governance** | None required |

**Pre-milestone production gate:** health `overall INFO` / 0 non-OK · Telegram
delivered · collection `quotes=66/66` · 5 forward signals at `2026-08-07` ·
`backup --all --dry-run` exit 0 · 0 failed units · `report.py --check` OK ·
chain valid (26). All five gates passed.

---

## 1. The finding that shaped this milestone

§16 grounds L3 in *"append-only registry + `as_of` derivation"*. **The registry
built at WBS 4.1 is not append-only in that sense.** `phase` is a mutable column
advanced in place under the `DATASET_TAMPER` guard, so
`dataset_specifications` records only where a dataset stands *now*.

The consequence is not cosmetic. The question §16 says L3 exists to answer —
*"what did we believe this dataset contained on the day we promoted?"* — had **no
source to read**. Every phase a dataset passed through before its current one
was overwritten at the moment it moved.

Three options were considered:

| Option | Verdict |
|---|---|
| Rebuild the registry as literally append-only | Rejected — rewrites 4.1 and every module built on it, to obtain a property a side ledger provides exactly |
| Derive history from existing timestamps (`declared_at`, `sealed_at`) | Rejected — `Rejected`, `Superseded` and `Retired` carry no timestamp, so the reconstruction would be silently partial |
| **Append-only transition ledger + `as_of` derivation** | **Adopted** — the shape `evidence_library.state_transitions` already uses, read back the way `record.derive_state_at()` reads it |

This is not a new pattern invented for Phase 4. It is the platform's existing
one, applied where the ADR assumed a property the implementation did not have.

## 2. History is written by triggers, not by Python

Two phase-write paths already exist:

```
registry._transition()            -> Declared/Resolved/Validated/Rejected/Superseded/Retired
sealing.seal_dataset()            -> Validated -> Sealed, via its own direct UPDATE
```

A Python-side hook would have to be added to **both**, and to every future third
path. A forgotten hook is worse than no history at all: it yields a *partial*
record that still reads as complete, answering an audit question with a
confident wrong answer.

So the ledger is populated by two database triggers. The strongest form of the
claim is tested directly —
`test_a_direct_sqlite_write_is_still_captured` bypasses this package entirely
with a raw `sqlite3` `UPDATE`, and the transition is still recorded.
`test_sealing_is_recorded_though_it_bypasses_registry_transition` covers the
path a Python hook would have missed.

Ordering uses `(ts, id)`. The autoincrement tiebreak is not decorative: two
transitions can land in the same millisecond, and `ts` alone would leave a fast
declare-then-resolve sequence undefined — the same flake class already
documented against `current_run_per_lane`.

## 3. DG-5 — scoped to what it says

> **DG-5:** *"A dataset cited by any run, model, or decision can never be
> retired or made unresolvable."*

Three citation classes, three evidence tables:

| DG-5 class | Evidenced by |
|---|---|
| **run** | `dataset_uses` joined through `experiment_datasets` (WBS 4.9) |
| **decision** | `holdout_consumptions` (WBS 4.10, DG-7) |
| **model** | `training_partitions` (WBS 4.10 — §15.1: a model cites all three partitions by hash) |

**A bound-but-unused Experiment Dataset does not block.** A trial binding is not
yet a run, and widening a governance rule past its written text is inventing
governance rather than enforcing it. It is surfaced instead, in
`RetirementReport.bindings_without_uses`, and recorded as `TD-4.11-1` — see §7.

Refusal is unconditional and has no override parameter. DG-5 does not say
"should not".

This **discharges the deferral WBS 4.1 recorded in its own docstring**: *"§6.2
additionally refuses retirement of any specification with a live citation; the
citation index is WBS 4.2, so that check is deferred by name."*

## 4. Supersession — verified, not assumed

§10.2: *"A superseded dataset remains fully resolvable and citable."*

The transition itself is delegated to WBS 4.1, which already enforces the arc
and records `superseded_by`. What this milestone adds is the check that **a
transition which succeeded is not the same as a predecessor which survived**:
`verify_supersession_preserved()` re-reads the predecessor after the fact and
asserts its phase, its citability, and — against a fingerprint taken before —
that its sealed record, its citations, and its declaration are byte-identical.

`redefinition` remains refused by the registry (§10.1: a redefinition is a new
lineage, not a new version). 4.11 did not relax it.

## 5. `HZ-4.5-2` resolved — flag, never recompute

The hazard: a sealed dataset's admissibility ceiling was computed from its
inputs as they stood at seal time; if an input is later superseded or flagged,
the ceiling may no longer be justified.

The tempting fix — recompute and update the sealed row — is **forbidden**, and
not merely by DG-3's immutability rule. A ceiling that silently moved would
destroy the reproducibility of every evaluation that already cited the dataset:
*a dataset whose recorded ceiling changes after the fact is no longer the
dataset that was evaluated.*

So the doubt is recorded **beside** the seal, never inside it:

- `flag_stale_ceiling()` appends to `ceiling_staleness_flags` (append-only);
- `detect_stale_ceilings()` computes the blast radius **via the WBS 4.2 citation
  index** rather than re-traversing — that index exists precisely so this
  question is answerable, and a second traversal would be free to disagree;
- `test_flagging_does_not_mutate_the_sealed_record` reads the whole
  `sealed_datasets` row before and after and asserts equality;
- an AST test asserts no `recompute_ceiling` / `update_ceiling` function exists.

TV3 confirms it is load-bearing: making the flag mutate the seal **failed 8
tests**.

## 6. A real interaction found while testing: L1 after retirement

I wrote `test_l1_still_verifies_after_retirement` asserting that retirement ends
citability but not recoverability. **It failed, and the assertion was wrong, not
the code.** §8.2 gates materialisation on a `Sealed` phase, so `verify_l1()`
refuses a `Retired` dataset outright.

The test was corrected to assert the **actual** behaviour, and the interaction
recorded as `TD-4.11-2` rather than left as an assumption. It is survivable
only because DG-5 makes it unreachable for anything that matters: a dataset any
run, model or decision cited can *never* be retired, so no evaluation can be
stranded. The pin row itself survives — traceability is intact — and the test
asserts that too.

Had I quietly deleted the failing assertion, this would have gone unrecorded.

## 7. Verification

### §4 TV — 91 tests, `tests/test_history.py`

| Group | n | Covers |
|---|---|---|
| Build / scope | 5 | no `src` import, idempotent init, non-builder store refused, **no `backfill`/`repair` function exists (AST)** |
| **Trigger-driven history** | 10 | genesis, every lifecycle step, **sealing's bypassing UPDATE**, **raw `sqlite3` write still captured**, no-op records nothing, append-only, `(ts,id)` ordering, chaining, independence |
| **`--as-of` derivation** | 7 | now, every past instant, before existence, **derived from ledger not the mutable column**, inclusive boundary, **retirement does not change the past** |
| **Registry reconstruction** | 7 | whole registry, **later dataset absent not defaulted**, citable set at instant, reconstructed phase, `was_sealed`, **declaration identical across all reconstructions** |
| History completeness | 3 | complete store OK, **pre-trigger spec reported not repaired**, gap not silently filled |
| **Retirement (DG-5)** | 15 | uncited retires, recorded, **refused by run / decision / model**, names every citer, refused leaves phase untouched, **binding does not block but is reported**, arc enforced, traceability preserved, **past citability still reconstructable** |
| **Supersession (§10.2)** | 12 | succeeds, predecessor citable + resolvable, **sealed record untouched**, citations untouched, **history appended not rewritten**, superseded still bindable, redefinition refused |
| **`HZ-4.5-2` staleness** | 11 | flag recorded, **seal not mutated**, no recompute fn, append-only, idempotent, blast radius via 4.2, non-citable skipped |
| **L3 composition** | 5 | pinned composes, unpinned reported, absent reported, **L1 after retirement (§6)**, L2 unaffected by supersession |
| TV3 guards | 5 | non-vacuity of DG-5, `as_of`, triggers, supersession preservation, staleness |
| Regression | 11 | RF1–RF6, phases, bidirectional consistency, no orphans, lineage ordering, sealed immutability, materialisation determinism, use-counting, training floor, chain, schema guards |

### §4 TV3 — sabotage

| Sabotage | Failures | Verdict |
|---|---|---|
| DG-5 citation check neutered | **8** | load-bearing |
| `as_of` filter ignored | **12** | load-bearing |
| Genesis trigger removed | **10** | load-bearing |
| Phase-change trigger removed | **16** | load-bearing |
| Append-only guards removed | **1** | load-bearing |
| Staleness flag mutates the seal | **8** | load-bearing |

Revert restored 91 passed.

**One sabotage was ineffective and was redone.** My first attempt at the trigger
sabotage *renamed* `trg_dataset_genesis` rather than removing it — the trigger
still fired and the suite reported **91 passed**, a false green. Corrected to
delete the `CREATE TRIGGER` block outright, with an assertion that the trigger
name is genuinely absent from the emitted DDL before the run is trusted. This is
the same failure mode caught at WBS 4.10; reported for the same reason.

### §5 REG

| Environment | Result | Prior |
|---|---|---|
| `btc_bot` | **3233 passed, 19 skipped** | 3142 + 91 |
| `base` | **2784 passed, 62 skipped** | 2693 + 91 |

`base` run excludes `research/forward_evidence/tests` (pre-existing `pandas`
gap in that interpreter) and `tests/test_api.py` (pre-existing `fastapi` gap),
per the documented convention.

**Zero pre-existing files modified.** 4.11 is two new files.

### Invariants re-verified

RF1–RF6 unchanged · `CITABLE_PHASES` unchanged · citation graph bidirectionally
consistent after retirement · zero orphans after supersession · lineage ordering
preserved · sealed immutability still enforced via direct `sqlite3` ·
materialisation still byte-deterministic · L1 verifies · experiment use-counting
unaffected · training floor and RF4 sole-authority guard intact · receipt chain
valid (26) · **result invariance 10 artifacts, 0 changed**.

## 8. Production verification

`overall INFO` · 0 non-OK · `btcbot` + `btcbot-api` active · **0 failed units** ·
collection `quotes=66/66` · `report.py --check` OK (35 families) · chain
`ok=True, 26 receipts` · three DBs integrity **ok / ok / ok** · disk 6.8 GB free
(76%) · `HEAD=59e4136`, 0 modified.

**No production change was made.** This milestone deploys nothing.

## 9. Technical debt and hazards

| # | Item | Severity | Note |
|---|---|---|---|
| **TD-4.11-1** | A bound-but-unused Experiment Dataset does not block retirement | 🟡 Medium | Faithful to DG-5's text (a binding is not a run), but retiring under one strands a trial position whose dataset is no longer citable. Widening DG-5 is a **governance decision**, not a builder one — flagged, not taken |
| **TD-4.11-2** | `verify_l1()` refuses a Retired dataset, so L1 re-verification ends at retirement | 🟡 Medium | §8.2 gates materialisation on `Sealed`. Safe only because DG-5 makes it unreachable for anything cited. The pin row survives; the bytes cannot be re-derived through the governed path. See §6 |
| **TD-4.11-3** | History is only complete from `history.init_db()` forward | 🟠 High | A store that ran before the triggers existed has unrecoverable early history. `history_completeness()` reports the gap by name and **refuses to fill it** (ADR-P1-1 assert-never-repair). Correct behaviour, but it means L3 is a *forward* guarantee, not a retroactive one |
| **TD-4.11-4** | `ts` comes from SQLite's `now`, not an injected clock | 🟢 Low | Untestable at fabricated instants; tests read back real recorded timestamps instead, which is arguably stronger. But history cannot be replayed against a simulated clock |
| **TD-4.11-5** | `cited_kind`/`cited_id` on a staleness flag are free text | 🟢 Low | Same shape as `TD-4.9-2`/`TD-4.6-2`. Nothing validates the flagged input exists |
| **HZ-4.11-1** | Nothing **alerts** on a recorded staleness flag | 🟠 High | `stale_ceilings()` makes it queryable; no report, healthcheck or governance check surfaces it. A sealed dataset can carry an unread staleness flag indefinitely. Mirrors `HZ-4.8-1`, `HZ-4.9-1`, `HZ-4.10-2` — **the fourth instance of the same pattern**, which is now a theme rather than an isolated gap |
| **HZ-4.11-2** | `detect_stale_ceilings()` must be invoked; nothing calls it automatically | 🟡 Medium | Superseding a Feature or Label Specification does not itself trigger detection. The blast radius is computable but not computed |

## 10. Files changed

| File | Change |
|---|---|
| `research/dataset_builder/history.py` | **New** — transition ledger, `--as-of` derivation, DG-5 retirement, supersession verification, staleness flags |
| `tests/test_history.py` | **New** — 91 tests |

**Zero files changed under `research/forward_evidence/`, `research/evidence/`,
`research/reports/`, or any pre-existing `dataset_builder` module.**

## 11. Reviewer Checklist (RC)

*Pending — Rohith.*

- [ ] **RC1** — Milestone corresponds to WBS 4.11 exactly; no Phase 5 scope
- [ ] **RC2** — No file under `docs/architecture/` touched
- [ ] **RC3** — "Files Changed" matches `git status` exactly
- [ ] **RC4** — At least two tests read in full
- [ ] **RC5** — Full-suite counts reproduced in both environments
- [ ] **RC6** — Rollback lever confirmed (two new files, additive)
- [ ] **RC7** — **The §1 finding is accepted** — the registry was not append-only, and L3 is provided by a side ledger rather than by rebuilding 4.1
- [ ] **RC8** — **The trigger-based mechanism (§2) is accepted** over Python-side hooks
- [ ] **RC9** — **The DG-5 scope reading (§3) is accepted** — a bound-but-unused experiment does not block retirement (`TD-4.11-1`)
- [ ] **RC10** — **The `HZ-4.5-2` resolution (§5) is accepted** — flag beside the seal, never recompute
- [ ] **RC11** — **`TD-4.11-2` accepted** — L1 re-verification ends at retirement (§6)
- [ ] **RC12** — **`TD-4.11-3` accepted** — L3 is a forward guarantee; pre-trigger history is reported, not reconstructed
- [ ] **RC13** — **`HZ-4.11-1` acknowledged** — fourth instance of "recorded but nothing alerts"
- [ ] **RC14** — Sign-off

  **Reviewer:** _______________  **Date:** _______________
