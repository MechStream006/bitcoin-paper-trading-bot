# WBS 4.8 — Dataset Materialiser (L1/L2 Reproducibility) — Verification Record

| Field | Value |
|---|---|
| **Milestone** | WBS 4.8 — Materialiser; L1/L2; divergence events (Sprint D5) |
| **Governing ADR** | [`ADR-I012`](../../../architecture/adr/ADR-I012-research-dataset-builder.md) §8.2 · §7.1 · §16 L1/L2 |
| **Roadmap** | [`phase_4_dataset_builder_roadmap.md`](../../phase_4_dataset_builder_roadmap.md) v1.0.2 §4.1 row 4.8 |
| **Date** | 2026-08-08 |
| **Implementer** | Claude Opus 5 |
| **Reviewer** | *(pending — Rohith)* |
| **Depends on** | 4.5 `0343621` (sealing), and 4.1–4.7 |
| **Governance** | None required — no `DS-DEC` owed before 4.8 |

**Pre-milestone production gate:** health `overall INFO` / 0 non-OK · Telegram
delivered · collection `66/66` · 5 forward signals current ·
`backup --all --dry-run` exit 0 · 0 failed units · `report.py --check` OK ·
chain valid (26). All five gates passed.

---

## 1. Determinism — the primary requirement

*"Repeated materialisation of the same sealed dataset must produce
byte-identical outputs."* Four rules make that true rather than hoped for:

1. **Rows sorted canonically** by the serialisation of the row itself, so
   ordering is a function of content alone — not of provider iteration order,
   dict insertion order, or which keys happen to exist.
2. **Keys sorted within every row.**
3. **Canonical JSON** (`sort_keys`, fixed separators, UTF-8, LF) — the same
   convention the receipt ledger and every store already use.
4. **No wall-clock, run id, or environment ever enters the snapshot.** Those
   live on the *manifest*, deliberately outside the content hash.

Proven across repeated runs, reversed provider order, permuted key order,
empty datasets, and **a fresh subprocess** — so hash randomisation and dict
ordering cannot affect the bytes.

TV3 confirms each rule is load-bearing: removing the row sort fails 2 tests;
putting the wall-clock into the content fails **11**.

## 2. The finding that shaped the module

`receipts.canonical_json` **refuses raw IEEE floats** —
`NonDecimalNumberError: raw IEEE float at $.v: 1.5. Convert with decimal_str()
first.` That is PR-1's decimal-string discipline, and it exists because a
float's `repr` is **not guaranteed byte-stable** across platforms or
interpreter versions.

Dataset content is mostly floats. Without handling this, L1 would be a
guarantee the platform could not actually keep.

`normalise_value()` converts at the assembly boundary — which is exactly what
`decimal_str`'s own docstring describes as its purpose (*"accepted here only as
a convenience for the caller converting at the boundary"*). Ints are left
alone: already exact, and converting them would change every existing integer
column for no gain.

**NaN and the infinities get explicit sentinels** (`__NaN__`, `__Infinity__`,
`__-Infinity__`). This is not hypothetical — the published lockbox record
carries `"sortino": NaN`. Dropping or zeroing such values would corrupt
content; JSON has no native representation, so they are encoded rather than
silently coerced.

## 3. L1 and L2 kept structurally apart

§16 warns that conflating the levels *"is how reproducibility claims become
unfalsifiable."*

| | Guarantee | Compares against | Applies to |
|---|---|---|---|
| **L1** | byte-identical | a **pinned** snapshot | Training Datasets |
| **L2** | semantically reproducible | the last recorded materialisation | all sealed datasets |

They are separate functions (`verify_l1`, `rematerialise_l2`), and every result
records `level_verified` so a caller can never report one having checked the
other.

**L1's mechanism is complete; its Training-Dataset binding is WBS 4.10.**
`pin_snapshot()` works today, but nothing declares a Training Dataset yet, so
nothing is pinned by default. Stated rather than implied.

## 4. Why an L2 divergence is a correct outcome

§16: *"If an input revision was corrected, rematerialisation yields different
content — and that is the **correct** outcome, surfaced as a divergence rather
than hidden."*

`rematerialise_l2()` therefore **returns** a `DivergenceEvent` rather than
raising: under L2 a divergence is information, not an error, and the caller
decides what it means. Under L1 the same event sets `ok = False`, because
§16 says L1 *"exists precisely for the cases where different-but-correct is
unacceptable."*

Either way the divergence is written to the **append-only** `materialisations`
table, so it cannot be discovered and then quietly forgotten. Nothing is
reconciled, repaired, or hidden (IQROS-P3) — `test_nothing_is_reconciled`
proves the earlier record survives a later divergence untouched.

## 5. Materialisation is off-lifecycle

§7.1: a sealed dataset is complete whether or not any bytes exist. This module
**never** changes a phase, never writes to the registry's tables, never touches
the citation index or the sealed row. Four tests assert each. Deleting every
snapshot loses nothing.

Superseded datasets remain materialisable: §10.2 keeps them citable, and a
citation that cannot be materialised is not resolvable in any useful sense.

## 6. The manifest is interpretable without registry access

§7.1 requires it, so the manifest carries the full provenance closure, column
inventory, row count, the **capped** admissibility ceiling (HZ-4.4-1 carried
through — taken from the seal, never recomputed), and an explicit
`verification` block naming the canonicalisation and **what is excluded from
the content hash**.

When no sealed row exists the closure is derived from the registry and citation
index and **marked as such** (`"source": "derived-at-materialisation"`), so a
reader can tell it apart from WBS 4.5's richer sealed closure rather than
assuming equivalence.

## 7. Verification

### §4 TV — 69 tests, `tests/test_materialisation.py`

| Group | n | Covers |
|---|---|---|
| **Determinism** | 8 | repeated runs, row order, key order, **fresh subprocess**, no timestamp/engine in content, format version *inside* the hash |
| **Float normalisation** | 7 | decimal strings, ints untouched, **NaN/Inf sentinels**, nested, stability |
| Materialise | 9 | snapshot+manifest, row count, columns, phase guard, superseded, LR4, schema guard, window |
| Off-lifecycle | 4 | no phase change, no citation/seal/registry write |
| Manifest | 6 | self-describing, closure, **capped ceiling**, hash determinism and sensitivity |
| **L1** | 7 | pin→verify, detects change, requires pin, double pin, cross-dataset pin, immutability |
| **L2** | 8 | reproduces, **divergence returned not raised**, names both hashes, row delta, **recorded permanently**, nothing reconciled |
| History | 3 | append-only, ordering, cross-dataset divergences |
| TV3 guards | 5 | non-vacuity of determinism, divergence, row sort, float normalisation, phase guard |
| Regression | 9 | RF1–RF6 unchanged, hash stable, citations, sealing, chain, **result invariance** |

### §4 TV3 — sabotage

| Sabotage | Failures | Verdict |
|---|---|---|
| Row sorting removed | **2** | load-bearing |
| Wall-clock inserted into snapshot content | **11** | load-bearing |
| Divergence detection neutered | **8** | load-bearing |

Revert restored 69 passed.

### §5 REG

| Environment | Result | Prior |
|---|---|---|
| `btc_bot` | **3005 passed, 19 skipped** | 2936 + 69 |
| `base` | **2556 passed, 62 skipped** | 2487 + 69 |

**Zero pre-existing files modified.** 4.8 is two new files.

### Result invariance carried forward

**10 artifacts, 0 changed** — 4.6's baseline re-proved, as every milestone now
does automatically via `test_result_invariance_still_holds`.

### Invariants re-verified

RF1–RF6 unchanged (`DEFERRED_REFUSALS == {RF4}`, `DEFERRED_CHECKS == {}`) ·
declaration hash stable · citation graph bidirectionally consistent · sealed
immutability still enforced by direct `sqlite3` · receipt chain valid · full
pipeline declare → resolve → validate → seal → materialise → pin → verify L1.

## 8. Production verification

`overall INFO` · 0 non-OK · `btcbot` + `btcbot-api` active · 6 timers · **0
failed units** · collection `quotes=66/66` · **all 5 forward signals advanced
to `2026-08-07`** — the `btcbot-fwd-hg` timer ran unattended overnight,
confirming the B2 recovery is holding without intervention ·
`report.py --check` OK (35: 9/22/4) · chain `ok=True, 26 receipts` · three DBs
integrity **ok/ok/ok** · disk 6.8 GB free (76%).

**No production change was made.**

## 9. Technical debt and hazards

| # | Item | Severity | Note |
|---|---|---|---|
| **TD-4.8-1** | Snapshot bytes are returned, never written to durable storage | 🟠 High | Deliberate: §7.1 calls a Research Dataset's snapshot *disposable*, and inventing a snapshot store is not in 4.8's scope. But an L1 pin currently records a hash whose bytes exist nowhere — the pin is verifiable only by re-materialising. A real Training Dataset (4.10) needs a byte store |
| **TD-4.8-2** | `ValueProvider` has no real implementation | 🟡 Medium | Same shape as 4.3's resolver: the Feature/Label stores own the real one. Nothing yet assembles real content, so determinism is proven on doubles, not on production data |
| **TD-4.8-3** | Row sort key serialises each row twice (once to sort, once to emit) | 🟢 Low | Correctness over speed at this stage. Matters only at scale |
| **TD-4.8-4** | `MATERIALISER_ENGINE_VERSION` is a hand-maintained constant | 🟡 Medium | Same shape as `TD-4.5-3`. An assembly change without a bump would make two incompatible materialisations indistinguishable |
| **HZ-4.8-1** | A divergence is recorded but **nothing alerts on it** | 🟠 High | `divergences()` makes them queryable, but no timer, healthcheck or Telegram path surfaces one. A silent divergence in a promoted dataset is exactly the failure §8.2 exists to prevent. **Wiring is out of 4.8's scope and needs its own decision** |
| **HZ-4.8-2** | L1 cannot distinguish "content changed" from "engine changed" | 🟡 Medium | `engine_version` is recorded per materialisation, so the evidence exists — but `verify_l1` does not compare it, so an engine upgrade reads as a content divergence. 4.10 should compare both |

## 10. Files changed

| File | Change |
|---|---|
| `research/dataset_builder/materialisation.py` | **New** — assembly, manifest, L1/L2, divergence |
| `tests/test_materialisation.py` | **New** — 69 tests |

**Zero files changed under `research/forward_evidence/`, `research/evidence/`
or `research/reports/`.**

## 11. Reviewer Checklist (RC)

*Signed 2026-08-08 — see below.*

- [x] **RC1** — Milestone corresponds to WBS 4.8 exactly; no 4.9+ scope
- [x] **RC2** — No file under `docs/architecture/` touched
- [x] **RC3** — "Files Changed" matches `git status` exactly
- [x] **RC4** — At least two tests read in full
- [x] **RC5** — Full-suite counts reproduced in both environments
- [x] **RC6** — Rollback lever confirmed (two new files, additive)
- [x] **RC7** — **The float-normalisation decision (§2) is accepted** — decimal strings at the boundary, explicit NaN/Inf sentinels *(basis: `OD-7`, Group D)*
- [x] **RC8** — **The L2 divergence-is-not-an-error semantics (§4) is accepted** *(basis: `OD-7`, Group D)*
- [x] **RC9** — **`TD-4.8-1` accepted** — snapshot bytes are not persisted; an L1 pin is verifiable only by re-materialising until 4.10 adds a byte store *(basis: `OD-3`, Group G)*
- [x] **RC10** — **`HZ-4.8-1` accepted** — divergences are recorded but nothing alerts; wiring needs its own decision *(basis: `OD-5`, Group B)*
- [x] **RC11** — Sign-off

  **Reviewer:** Rohith Suresh  **Date:** 2026-08-08
