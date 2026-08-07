# WBS 4.6 — RF2 Sealed-Region Relocation — Verification Record

| Field | Value |
|---|---|
| **Milestone** | WBS 4.6 — RF2, sealed-region relocation (Sprint D4) |
| **Governing ADR** | [`ADR-I012`](../../../architecture/adr/ADR-I012-research-dataset-builder.md) §8.1 RF2 · IQROS-P7 · **resolves PS3** |
| **Governance** | `DS-DEC-004` **ratified 2026-08-07**, **amended same day** (Option B) |
| **Roadmap** | [`phase_4_dataset_builder_roadmap.md`](../../phase_4_dataset_builder_roadmap.md) v1.0.2 §4.1 row 4.6 |
| **Date** | 2026-08-07 |
| **Implementer** | Claude Opus 5 |
| **Reviewer** | *(pending — Rohith)* |
| **Depends on** | 4.3 `8fff41d`, and 4.4/4.5 for the deferred-map updates |

**Pre-milestone production gate:** health `overall INFO` / 0 non-OK · Telegram
delivered · collection `66/66` · 5 forward signals at `2026-08-06` ·
`backup --all --dry-run` exit 0 · 0 failed units · `report.py --check` OK ·
chain valid (26). All five gates passed.

---

## 1. Result invariance — the primary acceptance criterion

**10 artifacts checked, 0 changed.** Baseline captured **before** any edit
(`BASELINE_46.json`), compared byte-for-byte after:

```
IDENTICAL  research/evidence/run_lockbox_validation.py
IDENTICAL  research/forward_evidence/decision_function.py
IDENTICAL  research/forward_evidence/decision_function_g1_market_regime.py
IDENTICAL  research/forward_evidence/decision_function_g3_atr_expansion.py
IDENTICAL  research/forward_evidence/decision_function_g3_tsmom.py
IDENTICAL  research/forward_evidence/decision_function_h5.py
IDENTICAL  research/forward_evidence/decision_function_ivrv.py
IDENTICAL  research/forward_evidence/m0_pre_register.py
IDENTICAL  research/forward_evidence/pre_registration.json
IDENTICAL  research/reports/evidence_library/lockbox_validation.json
```

**The proof is structural, not merely empirical.** Under the amended scope no
file that produces published research output is touched, so no published value
*can* change. Three independent tests enforce this:

- `test_every_invariant_file_is_bit_identical` — SHA-256 against the baseline
- `test_no_analysis_script_was_modified` — `git diff` over the three research
  trees returns empty
- `test_rf2_touches_no_file_outside_the_builder` — `git status` shows nothing
  outside `research/dataset_builder/`, `tests/`, `docs/`

**Criterion 4 verified in production**, not just locally:
`prod fn_hash f78f0a52… | registered f78f0a52… | MATCH: True`.

**Criterion 4 (lockbox untouched):** the one-time script was never invoked.
Enforced by an AST guard over every Dataset Builder module — naming the script
in prose is permitted, importing or calling it is not.

## 2. Why the scope narrowed, and what that cost

The roadmap's `A-4` recorded *"the lockbox constant is triplicated across three
analysis scripts"* and planned to make all three read one governed source.
Investigation **before** implementation found two of the three cannot be edited:

| Script | Why it is frozen |
|---|---|
| `decision_function.py` | **Cryptographically hash-locked.** `pre_registration.json` pins `decision_function_hash = f78f0a52…`; `daily_job.py:508` compares the file's SHA-256 on **every** forward-evidence run and sets `status="error"` on mismatch. Editing even a descriptive string breaks live collection. The file states: *"Do not modify decision_function.py after this registration."* |
| `m0_pre_register.py` | Writes the frozen pre-registration record; editing it rewrites a historical governance artifact |

Rohith's amendment resolved this: **RF2 lands only in the Dataset Builder path;
no analysis script is modified at all.** Both files are recorded as
`FROZEN_EXCEPTIONS` **in code**, with their reasons, so the exception is
tracked rather than remembered.

**What this cost, stated plainly:** the constant remains textually duplicated
in those two files. What RF2 actually promised is nevertheless delivered — see
§3.

## 3. What RF2 delivers, and why relocation beats deduplication

A constant read from one place is still a **convention**: a future script can
forget to read it. §8.1 asks for something stronger:

> *"Here, exclusion is applied at resolution: a consumer **cannot** read a
> sealed region from a dataset that excludes it, regardless of what the
> consumer's code does. Enforcement stops depending on every future script
> remembering."*

RF2 is enforced at **resolution**. A dataset whose window intersects a governed
sealed region without an explicit covering exclusion **cannot be resolved** —
so it can never be validated, sealed, or cited, and the consumer never receives
the rows to ignore the rule with. `test_a_refused_dataset_can_never_be_sealed_or_cited`
proves the chain end to end.

**That property does not depend on the two frozen files at all**, which is why
the narrowed scope still satisfies the ADR.

## 4. Design decisions

**Full coverage required, not partial.** An exclusion overlapping only part of
a sealed region leaves the remainder readable — the exact leak RF2 prevents.
`covers()` requires the exclusion to span the whole region. TV3 confirms the
rule is load-bearing: relaxing it to intersection fails 3 tests.

**Governed values transcribed from the published record**, not from a script's
literal. `test_governed_source_matches_published_record` asserts the registry
equals `lockbox_validation.json`'s `"lockbox": ["2025-06-01","2026-05-31"]`, so
drift is caught rather than assumed.

**Inclusive intersection.** Two spans sharing a single day intersect — a sealed
region leaking one observation is still a leak.

**One authority per refusal.** RF2 was removed from *both* deferred maps
(`resolution.DEFERRED_REFUSALS`, `validation.DEFERRED_CHECKS`) and validation
does **not** also report it. TB4 still checks that a declared exclusion is
well-formed; **RF2 decides whether one was required**. Two checks, one
authority each.

## 5. Verification

### §4 TV — 45 tests, `tests/test_sealed_regions.py`

| Group | n | Covers |
|---|---|---|
| **Result invariance** | 7 | baseline comparison, git-level proof, fn_hash, published-record agreement, **lockbox never invoked (AST)** |
| Governed source | 6 | registry, immutability, spent state, **frozen exceptions named with real reasons** |
| Intersection | 7 | overlap, before/after, **single shared day**, containment both ways |
| Coverage | 5 | exact, wider, **partial is not coverage**, malformed |
| RF2 at resolution | 9 | refusal, full/partial exclusion, non-intersecting, **standard fixture unaffected**, permanence, **never sealable** |
| TV3 guards | 5 | non-vacuity of RF2, coverage rule, registry, exceptions |
| Regression | 6 | deferred maps, no duplication, TB4 preserved, import boundary |

### §4 TV3 — sabotage

| Sabotage | Failures | Verdict |
|---|---|---|
| RF2 neutered | **7** | load-bearing |
| Partial coverage accepted (reopens the leak) | **3** | load-bearing |
| Governed registry emptied | **19** | load-bearing |

Revert restored 45 passed.

### §5 REG

| Environment | Result | Prior |
|---|---|---|
| `btc_bot` | **2874 passed, 19 skipped** | 2828 + 45 + 1 |
| `base` | **2425 passed, 62 skipped** | 2379 + 45 + 1 |

Three pre-existing test assertions updated, each because 4.6 legitimately
changed what they assert — the deferred set in `resolution`, in `validation`,
and in **4.5's sealed provenance closure**. That last one is 4.6 propagating
correctly into the sealed record: a dataset sealed today carries one fewer
deferred check, because RF2 is no longer deferred.

### Invariants re-verified

Registry lifecycle · citation graph and bidirectional consistency · declaration
and resolution refusals · Tier B/C/D and RF4 · sealing, DG-1, DG-3 and
HZ-4.4-1 · `report.py --check` OK (35: 9/22/4) · chain `ok=True, 26 receipts`.

## 6. Production verification

`overall INFO` · 0 non-OK · `btcbot` + `btcbot-api` active · 6 timers · **0
failed units** · collection `66/66` · **all 5 forward signals at 2026-08-06** ·
`report.py --check` OK · chain valid (26) · three DBs integrity **ok/ok/ok** ·
**production `fn_hash` MATCH: True**.

**No production change was made** (criterion 3 satisfied).

## 7. Technical debt and hazards

| # | Item | Severity | Note |
|---|---|---|---|
| **TD-4.6-1** | The lockbox constant remains textually duplicated in `decision_function.py` and `m0_pre_register.py` | 🟡 Medium | Deliberate and governed (`DS-DEC-004` amended). Tracked in `FROZEN_EXCEPTIONS`. Deduplicating requires a VERSION bump and re-registration — a larger governance act |
| **TD-4.6-2** | `SEALED_REGIONS` is a Python constant, not a governed data file | 🟡 Medium | Adding a region is a code change requiring review, which is arguably correct for a seal. But it is not in `thresholds.yaml` with the platform's other governed numbers |
| **TD-4.6-3** | `run_lockbox_validation.py` was not migrated | 🟢 Low | Excluded by the amendment specifically so no published output could change. Migrating it later would be safe only if its output is regenerated, which would reopen a spent lockbox |
| **HZ-4.6-1** | RF2 only guards datasets declared **through the builder**. A script reading the corpus directly still bypasses it | 🟠 High | This is the residual half of PS3. The builder path is now structurally safe; the direct-read path is not, and cannot be until consumers are required to go through sealed datasets. **Not solvable inside Phase 4** |
| **HZ-4.6-2** | A sealed dataset sealed *before* 4.6 was never RF2-checked | 🟡 Medium | No such dataset exists today (no real dataset has been sealed), so the exposure is theoretical. Becomes real the moment one is sealed and RF2 later changes |

## 8. Files changed

| File | Change |
|---|---|
| `research/dataset_builder/sealed_regions.py` | **New** — governed registry, coverage rules, frozen exceptions |
| `research/dataset_builder/resolution.py` | RF2 enforcement; RF2 removed from deferred |
| `research/dataset_builder/validation.py` | RF2 removed from deferred (docstrings only otherwise) |
| `tests/test_sealed_regions.py` | **New** — 45 tests |
| `tests/test_dataset_resolution.py` | 2 assertions updated + 1 test added |
| `tests/test_dataset_validation.py` | 3 assertions updated |
| `tests/test_dataset_sealing.py` | 1 assertion updated |
| `docs/engineering/phase_4_dataset_builder_roadmap.md` | `DS-DEC-004` / `T-3` recorded as ratified |
| `BASELINE_46.json` | **New** — the invariance baseline, retained as evidence |

**Zero files changed under `research/forward_evidence/`, `research/evidence/`
or `research/reports/`.**

## 9. Reviewer Checklist (RC)

*Pending — Rohith.*

- [ ] **RC1** — Milestone corresponds to WBS 4.6 exactly; no 4.7+ scope
- [ ] **RC2** — No file under `docs/architecture/` touched
- [ ] **RC3** — "Files Changed" matches `git status` exactly
- [ ] **RC4** — At least two tests read in full
- [ ] **RC5** — Full-suite counts reproduced in both environments
- [ ] **RC6** — **Result invariance accepted** — 10 artifacts, 0 changed, production `fn_hash` MATCH
- [ ] **RC7** — **The frozen exceptions (§2) are accepted** as documented, tracked and permanent absent a new decision
- [ ] **RC8** — **The full-coverage rule (§4) is accepted** — partial exclusion refuses
- [ ] **RC9** — **`HZ-4.6-1` accepted** — RF2 guards the builder path only; direct corpus reads remain outside it
- [ ] **RC10** — `BASELINE_46.json` retained as committed evidence *(or instruct removal)*
- [ ] **RC11** — Sign-off

  **Reviewer:** _______________  **Date:** _______________
