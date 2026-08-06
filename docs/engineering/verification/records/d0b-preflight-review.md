# Sprint D0b — Pre-Flight Engineering Review

| Field | Value |
|---|---|
| **Title** | D0b Pre-Flight Review — read-only, conducted before any D0b code |
| **Status** | ⛔ **STOPPED — D0b NOT begun. Two governance decisions required** |
| **Scope reviewed** | `build_corpus.py` / `build_perp_corpus.py` interaction · `calendar.py` shadowing blast radius · 64 → 274 scaling risks · roadmap/ADR/governance consistency |
| **Roadmap** | [`phase_4_dataset_builder_roadmap.md`](../../phase_4_dataset_builder_roadmap.md) v1.0.2, Sprint D0b |
| **Predecessor** | [WBS 4.0 D0a](wbs-4.0-d0a-dvol-conformance-pilot.md) — committed `ea3a8c6`, pushed |
| **Author** | Claude Opus 5 |
| **Date** | 2026-08-06 |
| **Code written** | **None.** Read-only review; two factual corrections applied to the D0a record |

---

## 0. Verdict

**D0b must not begin.** Three findings, two of which require an owner decision. One of them **inverts an assessment recorded in the D0a milestone record**, which has been corrected in place.

| # | Finding | Severity | Blocks D0b? |
|---|---|---|---|
| **F1** | Conformance **increases** a partition's exposure to `build_corpus.py` / `build_perp_corpus.py`, which strip all four conformance fields from any manifest they re-upgrade | **High** | ⛔ **Yes — governance decision** |
| **F2** | 3 partitions carry legacy manifests with **no `parquet_sha256`**, so condition `C4` cannot be evaluated for them | **Medium** | ⛔ **Yes — governance decision** |
| **F3** | `calendar.py` shadowing blast radius is **confined**; no path-form invocation is documented anywhere | Low | No |
| **F4** | Scaling 64 → 274 partitions makes D0a's real-corpus `C4` test disproportionately expensive | Medium | No — design input |

**No ADR-I012 inconsistency and no roadmap contradiction were found.** `ADR-I012` remains architecturally correct, and roadmap v1.0.2's method, conditions and sequencing all hold. The blockers are repository defects and a data-population gap, not specification problems.

---

## F1 — Conformance makes partitions *more* exposed to the corpus builders

### What was recorded at D0a, and why it was wrong

D0a's record stated this defect was *"narrower than it first appears"* because the builders *"skip any manifest already at `schema_version 1` with a passing `verify_hash`"*, and *"a conformed manifest satisfies both, so it is skipped."*

**That reasoning was wrong, and it was wrong because of a second error in the same record.** D0a's §6 `BC3` claimed conformed manifests *"remain `schema_version: 1`"*. They do not: `storage.manifest.SCHEMA_VERSION` is **3**, so `write_manifest()` stamps every conformed sidecar `schema_version: 3` (15 keys → 19).

Both errors are now **corrected in place** in the D0a record, with the original text quoted rather than deleted.

### The verified mechanism

Both builders gate identically (`build_corpus.py:217`, `build_perp_corpus.py:163`):

```python
if old_m.get("schema_version") == 1 and "parquet_sha256" in old_m:
    if verify_hash(parquet_path):
        continue          # skip
# ...otherwise fall through to: read parquet, write WI-6 manifest
```

Evaluated against a **real conformed manifest**:

```
conformed manifest schema_version = 3
skip gate fires?                  = False
  -> FALLS THROUGH TO RE-UPGRADE (STRIPS)

for comparison, an UNCONFORMED manifest:
  schema_version = 1, skip gate fires = True   (safe)

fields lost: ['admissibility', 'conditioning', 'raw_content_hash',
              'normalization_contract_version']
```

The re-upgrade calls `write_manifest(... ingest_ts, config_version=1)` and nothing else; the four conformance parameters default to `None` and overwrite the sidecar.

**So the polarity is inverted from what D0a recorded:** an *unconformed* partition is protected by the gate; a *conformed* one is not. Conformance is what breaks the skip.

### Why this blocks D0b

dvol is unaffected — neither builder references it (0 occurrences), which is why D0a shipped safely. **The entire D0b population is affected**: `build_corpus.py` handles `spot` and `funding`; `build_perp_corpus.py` handles `perp`. Conforming them would produce work that any subsequent corpus rebuild silently destroys.

**This is a governance decision because the fix is out of scope.** WBS 4.0's only deliverable is `research/ingest/backfill_conformance.py`. Repairing F1 means modifying `build_corpus.py` and `build_perp_corpus.py` — shipped scripts outside this WBS item — which is exactly the class of change roadmap `T-3`/`DS-DEC-004` gates for RF2's modification of the analysis scripts. Widening D0b's scope silently would violate the standing rule against it.

### Options (not decided here)

| Option | Description | Trade-off |
|---|---|---|
| **A** | Fix the gate in both builders (`schema_version in (1, 3)`, or `>= 1`) before D0b | Smallest correct fix; needs authorisation to touch two shipped scripts |
| **B** | Make both builders *preserve* existing conformance fields when re-upgrading | More faithful; larger change to shipped code |
| **C** | Conform now, accept the exposure, document it | Leaves a silent-destruction path live. **Not recommended** |
| **D** | Defer D0b until the builders are retired or superseded by Phase 4 resolution | Blocks Tier C on real data indefinitely |

---

## F2 — Three partitions cannot satisfy condition `C4`

Manifest population, verified across all 274 partitions:

| Dataset | `(schema_version, has parquet_sha256)` |
|---|---|
| dvol / BTC | `(3, True): 64` — conformed at D0a |
| funding / BTCUSDT | `(1, True): 77`, **`(ABSENT, False): 1`** |
| perp / BTCUSDT | `(1, True): 65`, **`(ABSENT, False): 1`** |
| spot / BTCUSDT | `(1, True): 65`, **`(ABSENT, False): 1`** |

The three outliers are all `year=2026/month=06`, all carry **real data** (`skipped: False`; perp and spot hold 14,009 rows each), and all pre-date WI-6 — their keys are the older shape (`interval`, `ccxt_exchange`, `cadence_ms`), with **no `parquet_sha256` and no `schema_version`**.

`conform_partition()` reads `man["parquet_sha256"]` and would raise `KeyError` on all three.

### Why this needs a decision rather than a code fix

`C4`'s entire value is comparing a re-derived hash against an **independently recorded** one. For these three there is no recorded hash, so:

- **Computing the hash from disk and adopting it** would assert the content is correct with nothing to check it against — defeating precisely what `C4` exists to prevent. It must not be done silently.
- **Skipping them with a stated reason** preserves `C4`'s meaning but leaves `ADR-I012` §12 Tier C (*"certification verdict present for **every** partition"*) unsatisfied for 3 of 274 — a governance-relevant outcome, not an engineering detail.
- **Upgrading them to WI-6 first** via the builders' own path would give them a `parquet_sha256` — but `build_corpus.py` covers only spot/funding, not perp, and running it invokes F1's stripping path.

**Recommendation (for ratification, not adopted here):** skip the three, refuse them **by name** with the reason recorded per-partition, and treat Tier C as satisfied for 271/274 with an explicit, named exception — consistent with how WBS 3.11 deferred four Label Specifications by name rather than activating what it could not demonstrate.

---

## F3 — `calendar.py` shadowing: blast radius is confined

Five modules live in `research/ingest/`: `backfill_conformance.py`, `binance_ohlcv.py`, `calendar.py`, `dvol.py`, `funding.py`. Every one is unrunnable as a path-form script, because Python places the script's own directory at `sys.path[0]`, where `calendar.py` shadows the stdlib `calendar` that pandas imports transitively via `_strptime`.

**Blast radius assessment: Low, and bounded.**

- A repo-wide grep across `*.md`, `*.yml`, `*.yaml`, `*.bat`, `*.sh` found **no documented path-form invocation** of any `research/ingest/` module — the only match is D0a's own record describing the defect.
- The test suite imports these modules (`from ingest import dvol`) rather than executing them, so it is unaffected — 2324 tests pass.
- The exposure is therefore limited to **ad-hoc developer invocation**, which fails loudly and immediately rather than producing wrong results.

**Not a D0b blocker.** Worth an `INC-0004` addendum recording that the incident's fix addressed modules that *insert* `research/ingest/` but not modules that *live inside* it.

---

## F4 — Scaling 64 → 274 partitions

| Dimension | dvol pilot | D0b remainder | Assessment |
|---|---|---|---|
| Partitions | 64 | 207 conformable (+3 blocked by F2) | Linear |
| Size | 788 KB | **182 MB** (spot 100 MB, perp 82 MB) | **231×** |
| Rows/partition | 8–31 | up to ~44,640 | Per-partition gauntlet cost rises materially |
| Symbols | `BTC` | `BTCUSDT` for all three | `_SOURCES` must carry per-source symbols — already parameterised |
| `ts_column` | `open_time` | `open_time` (spot/perp), `timestamp_ms` (funding) | Already a per-source field; must not be hardcoded |

**The concrete risk is test-suite cost, and it is measurable.** D0a's `test_c4_holds_for_every_real_dvol_partition` re-reads and re-hashes the whole dvol corpus, and raised full-suite runtime from 92 s to 125 s for **788 KB**. A naive D0b equivalent over 182 MB would be roughly two orders of magnitude heavier — unacceptable in a suite that runs every milestone.

**Design input for D0b (not a blocker):** the real-corpus `C4` assertion should be sampled or marked slow rather than exhaustive, with the exhaustive check performed once during the conformance run itself (where it is already a hard precondition) and evidenced in the milestone record.

---

## Roadmap / ADR / Governance Consistency

| Checked | Result |
|---|---|
| `ADR-I012` inconsistency | **None.** No clause was contradicted by anything D0a produced |
| Roadmap v1.0.2 method (`DS-DEC-002` re-derivation) | **Holds.** Validated end-to-end on 64 real partitions |
| Roadmap v1.0.2 conditions `C1`–`C6` | **Hold**, and `C4` proved its worth — F2 exists *because* `C4` is a real gate, not a formality |
| Roadmap D0a → D0b sequencing | **Vindicated.** The pilot's entire purpose was to surface exactly this class of problem at 788 KB instead of 182 MB. It did |
| `G-CORPUS` conditions still adequate | **Yes**, but F1 reveals a gap they do not cover: conditions govern *the conformance act*, not the *durability of its output* against other tooling |

**The one thing worth flagging to the roadmap owner:** `G-CORPUS`'s conditions verify that conformance is performed safely. Nothing in them requires that conformance **survives**. F1 is invisible to `C1`–`C6` and would have passed every one of them.

---

## Files Changed by This Review

| File | Change |
|---|---|
| `docs/engineering/verification/records/wbs-4.0-d0a-dvol-conformance-pilot.md` | **2 factual corrections** — §6 `BC3` (`schema_version` claim) and §8 defect 2 (severity, inverted). Original text quoted, not deleted |
| `docs/engineering/verification/records/d0b-preflight-review.md` | **New** — this record |

**No production code written. No data touched. No database written to. No ADR or roadmap modified.**

---

## Required Decisions Before D0b

| # | Decision | Owner |
|---|---|---|
| **1** | **F1** — authorise a fix to `build_corpus.py` / `build_perp_corpus.py` (option A or B), or accept the exposure (C), or defer D0b (D) | Rohith |
| **2** | **F2** — rule on the three legacy partitions: skip-by-name (recommended), or another route | Rohith |

---

| Role | Name | Date |
|---|---|---|
| Pre-flight review | Claude Opus 5 | 2026-08-06 |
| **F1 ruling** | **Rohith Suresh** | **_______________** |
| **F2 ruling** | **Rohith Suresh** | **_______________** |
