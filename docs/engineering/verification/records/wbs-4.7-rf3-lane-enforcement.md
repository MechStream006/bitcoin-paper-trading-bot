# WBS 4.7 — RF3 Lane Enforcement — Verification Record

| Field | Value |
|---|---|
| **Milestone** | WBS 4.7 — RF3, lane enforcement (Sprint D4) |
| **Governing ADR** | [`ADR-I012`](../../../architecture/adr/ADR-I012-research-dataset-builder.md) §8.1 RF3 · IQROS-P6 · **resolves PS4** · DG4 |
| **Roadmap** | [`phase_4_dataset_builder_roadmap.md`](../../phase_4_dataset_builder_roadmap.md) v1.0.2 §4.1 row 4.7 |
| **Date** | 2026-08-07 |
| **Implementer** | Claude Opus 5 |
| **Reviewer** | *(pending — Rohith)* |
| **Depends on** | 4.0 (lane per partition), 4.3 `8fff41d`, and 4.4–4.6 for the deferred-map updates |
| **Governance** | None required. No `DS-DEC` was owed before 4.7 — verified against §4.2 |

**Pre-milestone production gate:** health `overall INFO` / 0 non-OK · Telegram
delivered · collection `66/66` · 5 forward signals at `2026-08-06` ·
`backup --all --dry-run` exit 0 · 0 failed units · `report.py --check` OK ·
chain valid (26). All five gates passed.

---

## 1. What RF3 defends against

PS4, in the ADR's own words:

> *"Lane disjointness is convention. Nothing structurally prevents a
> confirmatory evaluation from resolving discovery-lane inputs."*
> **Consequence:** *"The confirmatory lane can be contaminated by the thing it
> exists to confirm."*

DG4 asks to *"make lane disjointness structural"*. RF3 refuses at **resolution**,
so a contaminated declaration never resolves — it can therefore never be
validated, sealed, or cited, and no consumer can opt out by writing different
code. `test_refused_dataset_can_never_be_sealed_or_cited` proves the chain.

## 2. Single governing authority — the requirement that shaped the module

**The lane vocabulary is not defined here.** It is imported from
`evidence_library/record.py`'s frozen `LANES` table, which has governed the
evidence ledger since PR-5:

```
L0 (offline-corpus, discovery)     L1 (offline-corpus, confirmatory)
L2 (live-forward,   forward)       L3 (mainnet,        production)
```

Two tests enforce this: `test_lane_table_is_imported_not_redefined` asserts
identity (`ln.LANES is record.LANES`), and an **AST test** fails if the module
ever assigns its own `LANES`. Defining a second vocabulary is exactly the
duplication that lets two authorities drift.

**A lane is a pair, not a word.** That is the insight the module turns on, and
the reason for §3.

## 3. The design decision: when RF3 *cannot* be evaluated

`run_kind` determines a lane uniquely. **Provenance does not** — `offline-corpus`
is *both* L0 and L1. §7.2 names the declaration's field simply `lane`, and the
platform's existing declarations carry the provenance half.

So RF3 has three outcomes, not two:

| Outcome | When |
|---|---|
| **Conflict** | dataset run_kind is `confirmatory` and an input's run_kind is `discovery` |
| **Clean** | both halves determinable, no conflict |
| **`not_evaluable`** | a lane expression does not determine its `run_kind` half |

**`not_evaluable` is never a silent pass.** It is surfaced on
`ResolutionReport.not_evaluable` and reported by name. Guessing the missing
half would be exactly the contamination RF3 exists to prevent — and TV3 proves
the point: making `offline-corpus` resolve to a lane fails **10 tests**.

This is also why RF3 does not suddenly refuse every existing declaration.
Those declarations genuinely do not state which side of the
discovery/confirmatory divide they sit on.

## 4. One rule, not a lattice

`FORBIDDEN_INPUT_LANES` contains **exactly one entry**, because §8.1 names
exactly one conflict:

```python
{"confirmatory": frozenset({"discovery"})}
```

It is deliberately **not** generalised into a lane-compatibility lattice —
inventing additional conflicts would be inventing governance. Adding an entry
is a governance act. A test asserts the table's exact contents so a future
addition cannot slip in unreviewed.

Consequences verified by test: a discovery dataset *may* consume discovery or
confirmatory inputs; a confirmatory dataset *may* consume forward-lane inputs
(stronger provenance is not contamination). Only the one named direction is
refused.

## 5. Integration without breaking any existing resolver

RF3 optionally reads `resolver.input_lane(kind, reference)` to learn the lane an
input actually resolved to — caught with `getattr`, **not** added to the
`ReferenceResolver` Protocol. Every pre-4.7 resolver, including 4.3's
`StoreResolver` and all test doubles, keeps working unchanged;
`test_resolver_without_input_lane_still_works` asserts it.

Two sources are checked: the declaration-internal case (a confirmatory dataset
*requiring* discovery evidence is self-evidently contaminated and refusable
before any resolver is consulted) and the resolved-input case (declared
requirement and actual lane disagree).

## 6. `DEFERRED_CHECKS` is now empty — and that is the honest state

| Map | Before 4.7 | After |
|---|---|---|
| `resolution.DEFERRED_REFUSALS` | RF3, RF4 | **RF4 only** |
| `validation.DEFERRED_CHECKS` | RF3 | **`{}`** |

RF4 remains listed from *resolution's* perspective because it genuinely is a
stage-4 check. Validation defers nothing: every check §12 assigns to that
module now runs there. The sealed provenance closure records the empty map, so
a dataset sealed today carries no unchecked-refusal caveat.

## 7. Verification

### §4 TV — 62 tests, `tests/test_lanes.py`

| Group | n | Covers |
|---|---|---|
| **Single authority** | 5 | table identity, **AST guard against redefinition**, derived sets, one-rule table |
| Lane resolution | 12 | all four codes, run_kind, unambiguous/**ambiguous** provenance, unknowns |
| Conflict detection | 12 | both sources, all non-conflicting directions, **not-evaluable paths**, determinism |
| **RF3 at resolution** | 10 | refused **by name**, clean paths, **standard fixture unaffected**, resolver-without-lanes, permanence, never sealable |
| **RF1–RF6 composition** | 6 | all resolution refusals together, one authority per refusal, **full pipeline to sealed** |
| TV3 guards | 5 | non-vacuity of RF3, ambiguity handling, forbidden table |
| Regression | 5 | RF2 still fires, hash stable, citation graph, **result invariance re-proof** |

### §4 TV3 — sabotage

| Sabotage | Failures | Verdict |
|---|---|---|
| RF3 neutered | **11** | load-bearing |
| Ambiguous provenance guessed into a lane | **10** | the not-evaluable discipline is load-bearing |
| Forbidden table emptied | **16** | load-bearing |

Revert restored 62 passed.

### §5 REG

| Environment | Result | Prior |
|---|---|---|
| `btc_bot` | **2936 passed, 19 skipped** | 2874 + 62 |
| `base` | **2487 passed, 62 skipped** | 2425 + 62 |

Four pre-existing test files updated, each because 4.7 legitimately changed
what they assert (the deferred maps in resolution, validation, sealing's
closure, and 4.6's cross-check).

### Result invariance carried forward

**10 artifacts, 0 changed** — 4.6's baseline re-verified after 4.7.
`test_result_invariance_still_holds` makes it a standing regression test, so
every future milestone re-proves it automatically rather than relying on
someone remembering.

### Invariants re-verified

Registry lifecycle · citation graph and bidirectional consistency · declaration
and resolution refusals · **RF2 still enforced** · Tier B/C/D and RF4 · sealing,
DG-1, DG-3, HZ-4.4-1 · full pipeline declare → resolve → validate → seal with
lanes, ending citable with a valid receipt chain.

## 8. Production verification

`overall INFO` · 0 non-OK · `btcbot` + `btcbot-api` active · 6 timers · **0
failed units** · collection `66/66` · all 5 forward signals at `2026-08-06` ·
`report.py --check` OK (35: 9/22/4) · chain `ok=True, 26 receipts` · three DBs
integrity **ok/ok/ok**.

**No production change was made.**

## 9. Technical debt and hazards

| # | Item | Severity | Note |
|---|---|---|---|
| **TD-4.7-1** | Every existing declaration carries `lane: "offline-corpus"`, so RF3 is `not_evaluable` for all of them | 🟠 High | **The most consequential item here.** RF3 is correct but currently inert on real declarations, because none states its `run_kind` half. Making it bite requires declarations to carry a lane *code* (`L0`/`L1`) or a run_kind. That is a declaration-content change, not a builder change — and a `DS-DEC` |
| **TD-4.7-2** | `input_lane` is read via `getattr` rather than being on the Protocol | 🟡 Medium | Chosen for backward compatibility. A resolver typo (`input_lanes`) silently disables the resolved-input half of RF3. Adding it to the Protocol with a default is the fix once all resolvers implement it |
| **TD-4.7-3** | `FORBIDDEN_INPUT_LANES` is a Python constant, not governed data | 🟡 Medium | Same shape as `TD-4.6-2`. Adding a conflict should require review, which a code change gives — but it is not in `thresholds.yaml` with the platform's other governed values |
| **HZ-4.7-1** | RF3 guards only datasets declared through the builder | 🟠 High | The residual half of PS4, exactly mirroring `HZ-4.6-1` for PS3. An evaluation reading the corpus directly still crosses lanes freely. **Not solvable inside Phase 4** |
| **HZ-4.7-2** | A dataset sealed before 4.7 was never RF3-checked | 🟡 Medium | No real dataset has been sealed, so the exposure is theoretical today. Becomes real the moment one is |

## 10. Files changed

| File | Change |
|---|---|
| `research/dataset_builder/lanes.py` | **New** — lane resolution, one-rule conflict table |
| `research/dataset_builder/resolution.py` | RF3 enforcement; `not_evaluable` on the report; RF3 removed from deferred |
| `research/dataset_builder/validation.py` | `DEFERRED_CHECKS` now empty |
| `tests/test_lanes.py` | **New** — 62 tests |
| `tests/test_dataset_resolution.py` | deferred assertions |
| `tests/test_dataset_validation.py` | deferred assertions |
| `tests/test_dataset_sealing.py` | closure assertion |
| `tests/test_sealed_regions.py` | 4.6/4.7 cross-check |

**Zero files changed under `research/forward_evidence/`, `research/evidence/`
or `research/reports/`.**

## 11. Reviewer Checklist (RC)

*Signed 2026-08-08 — see below.*

- [x] **RC1** — Milestone corresponds to WBS 4.7 exactly; no later scope
- [x] **RC2** — No file under `docs/architecture/` touched
- [x] **RC3** — "Files Changed" matches `git status` exactly
- [x] **RC4** — At least two tests read in full
- [x] **RC5** — Full-suite counts reproduced in both environments
- [x] **RC6** — Rollback lever confirmed (one new module, additive wiring)
- [x] **RC7** — **The `not_evaluable` third outcome (§3) is accepted** — RF3 reports rather than guesses when a lane is ambiguous *(basis: `OD-7`, Group D)*
- [x] **RC8** — **The one-rule table (§4) is accepted** — not generalised into a lattice *(basis: `OD-7`, Group D)*
- [x] **RC9** — **`TD-4.7-1` acknowledged** — RF3 is inert on today's declarations until they carry a lane code; making it bite needs a `DS-DEC` *(basis: `OD-3`, Group G)*
- [x] **RC10** — **`HZ-4.7-1` accepted** — RF3 guards the builder path only, mirroring `HZ-4.6-1` *(basis: `OD-4`, Group A)*
- [x] **RC11** — Sign-off

  **Reviewer:** Rohith Suresh  **Date:** 2026-08-08
