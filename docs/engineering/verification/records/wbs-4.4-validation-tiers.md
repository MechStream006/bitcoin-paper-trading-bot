# WBS 4.4 — Admissibility Composition + Validation Tiers A–D + RF4 — Verification Record

| Field | Value |
|---|---|
| **Milestone** | WBS 4.4 — stages 3–4, RF4 (Sprint D3) |
| **Governing ADR** | [`ADR-I012`](../../../architecture/adr/ADR-I012-research-dataset-builder.md) §8 stages 3–4, §8.1 RF4, §9.1, §12 |
| **Roadmap** | [`phase_4_dataset_builder_roadmap.md`](../../phase_4_dataset_builder_roadmap.md) v1.0.2 §4.1 row 4.4 |
| **Date** | 2026-08-07 |
| **Implementer** | Claude Opus 5 |
| **Reviewer** | *(pending — Rohith)* |
| **Depends on** | 4.1 `d6b020d`, 4.2 `a203038`, 4.3 `8fff41d` |

**Pre-milestone production gate, before any code:** health `overall INFO` /
0 non-OK · Telegram delivered · collection `66/66` · 5 forward signals at
`2026-08-06` · `backup --all --dry-run` exit 0 · 0 failed units ·
`report.py --check` OK · chain valid (26). All five gates passed.

---

## 1. The distinction that shapes the milestone

§12 is explicit: *"Tier D — Statistical hygiene … These **cap** admissibility;
they do not block."*

Tiers A–C **refuse**. Tier D **does not**: it lowers the dataset's ceiling. A
class-imbalanced dataset is not invalid — it is *less admissible*, and a
consumer with a high floor is refused by **RF4**, not by Tier D. Conflating the
two would either reject usable research or admit unusable research; the lattice
exists so neither is necessary.

Every Tier D finding carries `blocking=False` and a `caps` dict naming the
dimension and rung it lowers to. `apply_tier_d_caps()` applies them as a `meet`
against a cap tuple, so the monotone-decreasing guarantee comes from the
lattice rather than from this function being careful — **a cap can only weaken**.

## 2. RF4 is `satisfies()`, not a reimplementation

§8.1 RF4 is *"declared admissibility floor exceeds the computed meet"*. Phase 0
already built the lattice, whose `satisfies(ceiling, floor)` returns **every**
unmet dimension (ADR-I014 AD4). RF4 calls it.

Re-deriving the comparison would create a second authority on admissibility —
exactly what IQROS-P4 forbids. A test asserts the delegation at source level,
so a future reimplementation fails CI rather than passing silently.

The meet is composed by `admissibility.composition.meet()`, which is
order-independent and monotone decreasing. Both properties are tested here as
well as in Phase 0, because 4.4 depends on them being true rather than merely
documented.

## 3. Deferred, never silent

| Check | §12 lists it under | Owner |
|---|---|---|
| **RF2** sealed-region intersection | Tier B | WBS 4.6 |
| **RF3** lane conflict | Tier C | WBS 4.7 |

Both are named in `DEFERRED_CHECKS` and carried on **every** `ValidationReport`.
Tier D checks run without statistics are recorded in `not_computed`, never as
passed — the dataset is not materialised at this stage (§8.2), so absent
statistics are the normal case at 4.4, and *"we did not look"* must never be
indistinguishable from *"we looked and it was fine"*.

TC2 verifies a lane was **recorded**, not that it is **compatible**. TB4 checks
an exclusion span is coherent, not whether one was **required**.

## 4. A design error found by testing, and corrected

The first TB4 implementation flagged an exclusion lying entirely outside the
window as *"excludes nothing"*, blocking.

**That rule was wrong.** The standard declaration fixture carries the sealed
lockbox `2025-06-01..2026-05-31` against a window ending `2025-05-31` — which is
precisely how H-IVRV's lockbox is handled on this platform. Declaring the
lockbox even when the window does not reach it is **defensive practice**;
flagging it would penalise the careful declaration and reward the silent one.

The check was removed, with the reasoning recorded in the source. TB4 retains
the genuinely malformed cases: non-mapping entry, unparseable dates, inverted
span. Whether an exclusion was *required* is RF2's question.

This surfaced only because the fixture encodes real house practice rather than
a convenient minimal case — 18 of 21 failures traced to this single rule.

## 5. Verification

### §4 TV — 87 tests, `tests/test_dataset_validation.py`

| Group | n | Covers |
|---|---|---|
| Build / scope | 6 | no `src` import, fixed tier order, deferred named with owner, **RF4 delegation asserted at source** |
| Stage 3 compose | 6 | weakest wins, no cross-dimension contamination, order independence, meet stated |
| Tier A | 4 | reuse of registry Tier A, field naming, ADR clause |
| Tier B | 13 | TB1–TB4, both sides of each boundary, unknown span does not fire |
| Tier C | 7 | verdict present/unrecognised/RED-is-present, lane, readiness |
| **RF4** | 7 | floor above/equal/below meet, **every unmet dimension named**, floor never relaxed, clause+tier+field carried |
| **Tier D** | 12 | each check fires, **all non-blocking**, caps lower but never raise, N/A dimensions skipped, boundaries |
| Stage 4 battery | 6 | phase guards, all blocking tiers at once, LR4 |
| Rejection permanence | 4 | written before raising, no arc out, reason carries code+tier |
| TV3 guards | 6 | non-vacuity of each tier and RF4 |
| Regression | 8 | 4.1/4.2/4.3 invariants |

### §4 TV3 — sabotage

| Sabotage | Failures | Verdict |
|---|---|---|
| RF4 neutered | **11** | load-bearing |
| Tier D made blocking | **5** | the §12 conflation is caught |
| Tier C verdict check neutered | **1** | load-bearing *(see below)* |
| Tier D caps neutered | **1** | load-bearing |

**TV3 found a weakness in my own test.** Neutering the "missing verdict" branch
initially failed **nothing** — an empty verdict falls through to the
*unrecognised* branch, so `TC1` still fired, and the test asserted only the
code. It was strengthened to assert the specific condition and to prove the two
messages differ. The sabotage then failed correctly. The test was fixed, not
the sabotage.

### §5 REG

| Environment | Result | Prior |
|---|---|---|
| `btc_bot` | **2759 passed, 19 skipped** | 2672 + 87 |
| `base` | **2310 passed, 62 skipped** | 2223 + 87 |

**Zero pre-existing files modified.** 4.4 is two new files.

### Invariants re-verified

Registry lifecycle to Sealed · sealed immutability by direct `sqlite3` attempt ·
citations intact after validation · `verify_bidirectional_consistency()` clean ·
declaration hash unchanged by validation · a dataset **Rejected at validation
stops pinning inputs** (4.2's phase gate composes with 4.4's rejection path) ·
4.3's resolution refusals still fire.

## 6. Production verification

`overall INFO` · 0 non-OK · `btcbot` + `btcbot-api` active · 6 timers · **0
failed units** · collection `66/66` · disk 7.1 GB (75%) · `report.py --check`
OK (35: 9/22/4) · chain `ok=True, n_receipts=26` · trading/ledger/forward
integrity **ok/ok/ok**.

**No production change was made.** Research tooling only.

## 7. Technical debt and hazards

| # | Item | Severity | Note |
|---|---|---|---|
| **TD-4.4-1** | **TB1 is unreachable through the normal path** — registry Tier A already refuses `as_of < window_end` at declaration time | 🟡 Medium | Defence in depth, not dead code: §12 lists `as_of` closure under Tier B, and a dataset reaching stage 4 by any future path must still be checked. Recorded so nobody mistakes it for untested |
| **TD-4.4-2** | Tier D thresholds (`min_rows=30`, `min_calendar_years=1`, `max_class_imbalance=0.95`, `min_matured_fraction=0.5`) are function defaults, not governed constants | 🟡 Medium | They cap rather than block, so a wrong value cannot reject research — but they should move to `thresholds.yaml` with the rest of the governed numbers |
| **TD-4.4-3** | `InputAdmissibility` is caller-supplied; nothing yet derives it from the resolved references | 🟡 Medium | 4.5/4.8 will need a builder that reads ceilings from the upstream stores. Today the caller asserts them, which is the one place stage 3 is not yet "computed, never asserted" end-to-end |
| **TD-4.4-4** | `check_tier_a` re-runs registry Tier A, so its findings all carry code `TA1` | 🟢 Low | Registry Tier A returns strings, not codes. Per-rule codes would need a registry change, which 4.4 must not make |
| **HZ-4.4-1** | Tier D caps are computed but **not persisted** — `capped_admissibility` lives only on the report | 🟠 High | WBS 4.5 must carry it onto the sealed dataset (§9 "Sealed dataset: computed admissibility ceiling"). If 4.5 stores `computed_meet` instead of `capped_admissibility`, every Tier D cap is silently lost. **Named here so 4.5 cannot miss it** |
| **HZ-4.4-2** | An empty `inputs` sequence raises rather than validating | 🟢 Low | Correct — a dataset citing nothing has no ceiling — but the caller decides what "inputs" means until 4.5 derives them |

## 8. Files changed

| File | Change |
|---|---|
| `research/dataset_builder/validation.py` | **New** — stages 3–4, Tiers A–D, RF4 |
| `tests/test_dataset_validation.py` | **New** — 87 tests |

## 9. Reviewer Checklist (RC)

*Signed 2026-08-08 — see below.*

- [x] **RC1** — Milestone corresponds to WBS 4.4 exactly; no 4.5+ scope
- [x] **RC2** — No file under `docs/architecture/` touched
- [x] **RC3** — "Files Changed" matches `git status` exactly
- [x] **RC4** — At least two tests read in full
- [x] **RC5** — Full-suite counts reproduced in both environments
- [x] **RC6** — Rollback lever confirmed (purely additive)
- [x] **RC7** — **The Tier D caps-not-blocks model (§1) is accepted** *(basis: `OD-7`, Group D)*
- [x] **RC8** — **The TB4 correction (§4) is accepted** — a non-intersecting exclusion is not a finding *(basis: `OD-7`, Group D)*
- [x] **RC9** — **`HZ-4.4-1` is accepted as a hard requirement on WBS 4.5** — the sealed dataset must carry `capped_admissibility`, not `computed_meet` *(basis: `OD-3`, Group F — paired with `4.5/RC9`, resolution confirmed; distinct from `TD-4.4-3`, no binding condition)*
- [x] **RC10** — `TD-4.4-1` (TB1 unreachable) and `TD-4.4-2` (ungoverned thresholds) accepted *(basis: `OD-3`, mechanical debt acknowledgment — confirmed 2026-08-08 as legitimately covered by content)*
- [x] **RC11** — Sign-off

  **Reviewer:** Rohith Suresh  **Date:** 2026-08-08
