# Corpus Conformance Backfill — Operations Record

| Field | Value |
|---|---|
| **Declared by** | [`phase_4_dataset_builder_roadmap.md`](../engineering/phase_4_dataset_builder_roadmap.md) §13.3 — *"Corpus backfill transcript"*, WBS 4.0 |
| **Sprints** | D0a (dvol pilot) · D0b (full corpus) · WBS 4.0.1 (builder repair) |
| **Governing decisions** | `DS-DEC-001`, `DS-DEC-002`, `G-CORPUS` conditions `C1`–`C6` — all ratified 2026-08-06 |
| **Milestone records** | [D0a](../engineering/verification/records/wbs-4.0-d0a-dvol-conformance-pilot.md) · [D0b](../engineering/verification/records/wbs-4.0-d0b-full-corpus-conformance.md) · [4.0.1](../engineering/verification/records/wbs-4.0.1-builder-manifest-upgrade-repair.md) |
| **Created** | 2026-08-08 — closing audit finding `AF-3` (declared deliverable absent) |
| **State verified at** | 2026-08-08, commit `82ce7c0` |

---

## 1. Scope and honesty note

The roadmap declares a *"transcript"* for this sprint. **No console transcript
was captured at run time**, and none is reconstructed here — fabricating one
after the fact would be worse than its absence.

What this record supplies instead is the **verified end state of the corpus**,
re-derived from the manifests on disk at the freeze commit, plus pointers to
the three milestone records that hold the run-time reasoning, the `C1`–`C6`
condition results, and the TV3 sabotage evidence.

**This is an outcome record, not a transcript.** Treat it as such.

## 2. What the backfill was for

`ADR-I012` §12 Tier C and RF3 both require corpus partitions to carry a
certification verdict, a conditioning statement, and a revision reference. The
Phase 4 gap analysis finding `A-2` established that **0 of 274 partitions
carried any of the three** — the ingest code was wired, the data predated the
wiring. Until that was closed, no Research Dataset could seal against real data.

## 3. Verified end state — 2026-08-08

All four sources, all partitions, re-read from `research/data/raw/*/**/_manifest.json`
(`_backup_*` snapshot directories excluded — they are pre-conformance copies,
not corpus members):

| Source | Partitions | Certification | Readiness | `schema_version` | `normalization_contract_version` | With `parquet_sha256` |
|---|---|---|---|---|---|---|
| `dvol` | 64 | 60 GREEN · 2 AMBER · 2 RED | 62 READY · 2 NOT_READY | 3 (all) | 1 (all) | **64 / 64** |
| `funding` | 78 | 56 GREEN · 21 AMBER · 1 RED | 77 READY · 1 NOT_READY | 3 (all) | 1 (all) | **78 / 78** |
| `perp` | 66 | 59 GREEN · 6 AMBER · 1 RED | 65 READY · 1 NOT_READY | 3 (all) | 1 (all) | **66 / 66** |
| `spot` | 66 | 53 GREEN · 12 AMBER · 1 RED | 64 READY · 1 PARTIAL · 1 NOT_READY | 3 (all) | 1 (all) | **66 / 66** |
| **Total** | **274** | — | — | uniform | uniform | **274 / 274** |

**Finding `A-2` is discharged.** Every one of the 274 partitions now carries a
certification verdict and a readiness state, at a uniform schema and
normalization-contract version, each with a recorded content hash.

### Certification is genuinely differentiated

The verdicts are not uniformly GREEN, and that is the point — a conformance run
that stamped everything GREEN would have proven nothing. The RED partitions are
explainable: DVOL's 8-row inception month (2021-03) and current partial months.
The AMBER distribution reflects real coverage variation, most visibly in
`funding` (21 AMBER).

This differentiation is what makes the first sealed dataset's **AMBER** ceiling
honest rather than aspirational: eight in-window `spot` partitions are AMBER, so
the meet cannot be GREEN.

## 4. Method — re-derivation, not re-download

`DS-DEC-002` ratified **local re-derivation**. The original 274 partitions were
never overwritten; conformance fields were computed from bytes already on disk
and written through the builders' own `stage_manifest_upgrade()`.

Condition `C4` — a per-partition hash-equality assertion — is what converts a
content change into a **failed precondition** rather than a regression found
afterwards. D0b records it holding **274/274 corpus-wide**.

## 5. Consumption evidence

The corpus is not merely conformed; it has been **used** under governance. The
first sealed Research Dataset (`f444c132…`) pins two sources by
content-addressed revision:

```
dvol  rev-62ee257eee22939b   49 in-window partitions, cert meet GREEN
spot  rev-a87ba2697ad6bf83   49 in-window partitions, cert meet AMBER
```

Each revision id is a SHA-256 over the sorted `(partition path, parquet_sha256)`
pairs in the declared window, so any byte change in any partition moves the
revision and breaks the pin. That is what makes the pin meaningful rather than
a label.

## 6. Known limitations

- **`RC3` of D0a** records that provenance is *"re-derived canonical, not
  as-received"*. The corpus is canonical as this platform computes it, not as
  the exchange served it. Accepted at ratification; restated here so a future
  reader does not over-claim.
- **`TD-4.3-3`**: resolution injects corpus coverage rather than reading these
  manifests, so the manifests are the authority but not yet the single reader.
- **Off-host replication of the corpus (548 MB) does not exist** — see
  `p1_assurance_declaration.md` BK3, still NOT ESTABLISHED.
