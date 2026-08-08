# WBS 4.3 — Dataset Declaration + Resolution — Verification Record

| Field | Value |
|---|---|
| **Milestone** | WBS 4.3 — Declaration + Resolution (stages 1–2), RF1/RF5/RF6 (Sprint D2) |
| **Governing ADR** | [`ADR-I012`](../../../architecture/adr/ADR-I012-research-dataset-builder.md) §8 stages 1–2, §8.1 RF1/RF5/RF6, §14 |
| **Roadmap** | [`phase_4_dataset_builder_roadmap.md`](../../phase_4_dataset_builder_roadmap.md) v1.0.2 §4.1 row 4.3 |
| **Date** | 2026-08-07 |
| **Implementer** | Claude Opus 5 |
| **Reviewer** | *(pending — Rohith)* |
| **Depends on** | 4.0 (complete), 4.1 (`d6b020d`), 4.2 (`a203038`) |

**Pre-milestone production gate, before any code:** health `overall INFO` /
0 non-OK · Telegram delivered · collection `66/66` · 5 forward signals at
`2026-08-06` · `backup --all --dry-run` exit 0 (dry-run only, per the disk
lesson from 4.1) · 0 failed units · `report.py --check` OK · chain valid (26).
All five gates passed.

---

## 1. Scope — three of six refusals

| # | Refusal | Owner |
|---|---|---|
| **RF1** | unresolvable or non-Active specification reference | **4.3 — here** |
| RF2 | sealed-region intersection without exclusion | 4.6 |
| RF3 | lane conflict | 4.7 |
| RF4 | declared floor exceeds computed meet | 4.4 |
| **RF5** | label maturity requirement unsatisfiable | **4.3 — here** |
| **RF6** | warm-up unavailable within the resolved window | **4.3 — here** |

§8's stage table also lists sealed-region intersection and lane conflict as
*Resolution* refusals. Those are RF2 and RF3, assigned elsewhere by the roadmap.
Rather than leave them silently absent, **every `ResolutionReport` carries a
`deferred` map naming RF2/RF3/RF4 and the milestone that owns each** — so a
reader of a resolution result can never mistake *"not checked yet"* for
*"checked and passed"*. Asserted by test.

Stages 3–5 (admissibility composition, validation battery, seal) are untouched:
a successful `resolve()` moves `Declared → Resolved` and stops.

## 2. Engineering rationale

### Automatic indexing closes `TD-4.2-2`

After 4.2, `index_declaration()` had to be called by hand, so a dataset could
exist with **no citation edges** and the reverse index would silently
under-report — meaning a retirement refusal could pass because the citation was
never recorded. `declare()` now performs declaration and indexing as one
operation. Tier A runs first inside `registry.declare_dataset()`, so a rejected
declaration never becomes a dataset and therefore correctly has no edges.

`find_unindexed_declarations()` makes the invariant auditable for rows written
before 4.3, or by calling `registry.declare_dataset()` directly.

### Duplicate lineage is refused, not silently absorbed

Content addressing makes a second declaration of identical content a no-op that
returns the **first** handle — with the *original* lineage. A caller who
believes they are opening a new research line would be wrong and would never
find out. `DuplicateLineageError` names both lineages and explains why.

Implemented in the 4.3 declaration wrapper, **not** by changing 4.1's registry:
idempotency within a lineage is correct registry behaviour, and cross-lineage
duplication is a declaration-stage policy. 4.1 is untouched.

### RF5 — the reasoning, stated because it is a judgement

A label observed at `t` matures at `t + horizon_days`. The latest observation a
dataset can hold is `window_end`, so every label is matured only if:

```
window_end + horizon_days <= as_of
```

This is unsatisfiable **in principle**, not merely unsatisfied today: `as_of` is
declared, not current time, so waiting does not help. Refusing is correct;
silently truncating the window to the satisfiable part would answer a question
nobody asked (IQROS-P3, assert never repair).

RF5 fires **only** when the declaration demands maturity. A declaration asking
for `partial` makes no claim RF5 can falsify. It also does not double-report
when RF1 already refused the same reference.

### RF6 — the reasoning

A feature with `warmup_periods = W` cannot produce a valid value at
`window_start` unless the corpus holds W periods before it. Checked against the
**latest-starting** cited evidence source, because a feature reads whichever
sources its inputs need and the binding constraint is the one that starts last.

**Periods are treated as calendar days**, matching the daily corpus this
platform collects. A feature on a different cadence needs its own
period-to-days mapping — recorded as `TD-4.3-2` rather than guessed.

When no coverage information is available, RF6 **does not fire**. Refusing on
absent information would be guessing, and the platform's rule is to assert what
is known and name what is not.

### Dependency direction

The builder is downstream of both stores, so importing them is the correct
direction — the inversion 4.2 avoided was the opposite one. Even so, the
refusals depend on a `ReferenceResolver` **Protocol**, with `StoreResolver` as
the only concrete adapter that imports the upstream registries, and it does so
**inside methods, not at module level**. A test asserts no module-level upstream
import, keeping §8.3 ("the builder never reads operational data") verifiable by
inspecting one small class rather than the whole module.

## 3. Verification

### §4 TV — 74 tests, `tests/test_dataset_resolution.py`

> **Count addendum (2026-08-08, audit finding `E-1`).** The figure above is the
> count **as delivered by this milestone** and is left unchanged. The file now
> holds **75** tests. The single additional test was added by WBS 4.6
> (`9828d6e`), which enforced `RF2` inside `resolution.py` and recorded the
> addition in its own Files-changed table (*"2 assertions updated + 1 test
> added"*). WBS 4.7 (`5ece707`) further updated deferred-refusal assertions in
> this file without changing the count.
>
> `RC5` reproduction today yields **75**, not 74, and that is correct. No test
> delivered by WBS 4.3 was removed or altered.

| Group | n | Covers |
|---|---|---|
| Build / scope | 4 | no `src` import; upstream only inside the adapter; deferred refusals named |
| Declaration | 10 | automatic indexing, Tier A precedence, idempotency, **duplicate lineage**, orphan detection |
| **RF1** | 14 | unresolvable + **every non-Active phase**, by name, with the phase in the message |
| **RF5** | 7 | horizon past `as_of`, exact boundary, one-day-short, non-demanding requirement, no double-report |
| **RF6** | 7 | insufficient warm-up, exact boundary, latest-source binding, absent coverage, zero warm-up |
| Resolution mechanics | 13 | binding, pinning precedence, Rejected permanence, phase guards, LR4 |
| StoreResolver | 6 | Protocol conformance, real Feature/Label store reads |
| TV3 guards | 6 | non-vacuity of each refusal and of indexing |
| Regression | 8 | 4.1/4.2 invariants preserved |

**Boundary cases are tested on both sides** for RF5 and RF6 — exactly-satisfied
passes, one day short refuses. A refusal that only fires far from the boundary
would be untrustworthy near it.

### §4 TV3 — sabotage

| Sabotage | Failures | Verdict |
|---|---|---|
| RF1 neutered | **14** | load-bearing |
| RF5 neutered | **4** incl. its own non-vacuity test | load-bearing |
| RF6 neutered | **4** incl. its own non-vacuity test | load-bearing |
| Automatic indexing neutered | **8** | load-bearing |

Revert restored 74 passed.

### §5 REG

| Environment | Result | Prior |
|---|---|---|
| `btc_bot` | **2672 passed, 19 skipped** | 2598 + 74 |
| `base` | **2223 passed, 62 skipped** | 2149 + 74 |

**Zero pre-existing files modified.** 4.3 is purely additive — two new files.

### Invariants re-verified after resolution

- Registry lifecycle (4.1) unchanged; sealed immutability still enforced by
  direct `sqlite3` attempt.
- Citations append-only still enforced.
- `verify_bidirectional_consistency()` clean after resolution.
- Declaration content hash unchanged by resolution — resolution binds
  references, it does not alter the declaration.
- A **Rejected** dataset keeps its citation rows but **stops pinning inputs**
  (`cited_by` excludes it) — the 4.2 phase gate and the 4.3 rejection path
  compose correctly.

## 4. Production verification (after implementation)

`overall INFO` · 0 non-OK · `btcbot` + `btcbot-api` active · 6 timers · **0
failed units** · collection `66/66` · disk 7.1 GB (75%) · `report.py --check`
OK (35 families: 9/22/4) · chain `ok=True, n_receipts=26` ·
trading/ledger/forward integrity **ok/ok/ok**.

**No production change was made.** Research tooling only; IQROS-P1's boundary
holds.

## 5. Technical debt and hazards

| # | Item | Severity | Note |
|---|---|---|---|
| **TD-4.3-1** | `resolve()` binds a reference to *itself* (the declared hash) rather than to a resolved-at-`as_of` successor | 🟡 Medium | Neither upstream store implements `as_of` supersession lookup yet. Today a hash resolves to itself or is refused; when the stores gain point-in-time resolution this becomes a real binding step |
| **TD-4.3-2** | RF6 treats warm-up periods as calendar days | 🟡 Medium | Correct for the daily corpus. A sub-daily or trading-day feature needs an explicit period→days mapping. Named, not guessed |
| **TD-4.3-3** | Corpus coverage is injected, not read from the WBS 4.0 manifests | 🟢 Low | Deliberate: the manifests are the authority and re-deriving coverage here would duplicate it. A manifest-backed resolver is a small future adapter |
| **TD-4.3-4** | `_MATURITY_DEMANDING` is a fixed vocabulary (`matured`/`mature`/`complete`) | 🟢 Low | An unrecognised requirement silently skips RF5. Tier A does not constrain the vocabulary; 4.4 should |
| **HZ-4.3-1** | Resolution is not transactional across registry + citation index | 🟡 Medium | A crash between `declare_dataset()` and `index_declaration()` leaves an unindexed declaration. `find_unindexed_declarations()` detects it; a shared-transaction fix mirrors PR-4's `append_receipt_on_connection` pattern |
| **HZ-4.3-2** | RF6 silently passes when coverage is unknown | 🟢 Low | Deliberate (assert, never guess), but a declaration citing an unknown source gets no warm-up check at all. 4.4's Tier C should refuse unknown sources outright |

## 6. Files changed

| File | Change |
|---|---|
| `research/dataset_builder/resolution.py` | **New** — declaration, resolution, RF1/RF5/RF6 |
| `tests/test_dataset_resolution.py` | **New** — 74 tests |

## 7. Reviewer Checklist (RC)

*Signed 2026-08-08 — see below.*

- [x] **RC1** — Milestone corresponds to WBS 4.3 exactly; no 4.4+ scope
- [x] **RC2** — No file under `docs/architecture/` touched
- [x] **RC3** — "Files Changed" matches `git status` exactly
- [x] **RC4** — At least two tests read in full
- [x] **RC5** — Full-suite counts reproduced in both environments
- [x] **RC6** — Rollback lever confirmed (purely additive, two new files)
- [x] **RC7** — **The RF5 rule (§2) is accepted** — `window_end + horizon <= as_of` *(basis: `OD-7`, Group D)*
- [x] **RC8** — **The RF6 rule (§2) is accepted** — calendar days, latest source binding, silent pass on unknown coverage *(basis: `OD-7`, Group D)*
- [x] **RC9** — **`DuplicateLineageError` is accepted** as a declaration-stage policy rather than a 4.1 registry change *(basis: `OD-7`, Group D — confirmed 2026-08-08 as legitimately covered by content)*
- [x] **RC10** — `HZ-4.3-1` (non-transactional declare+index) accepted as detected-not-prevented *(basis: `OD-3`, mechanical — confirmed 2026-08-08: this is a crash-recovery detection mechanism, not the "recorded-but-unalerted" Group B pattern)*
- [x] **RC11** — Sign-off

  **Reviewer:** Rohith Suresh  **Date:** 2026-08-08
