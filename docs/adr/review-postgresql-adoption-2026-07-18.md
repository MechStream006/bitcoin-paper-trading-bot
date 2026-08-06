# PostgreSQL Adoption Review — 2026-07-18

Conducted per ADR-0001 §18 (Deferred Decisions) and §19 (Phase 5 — gated).
**No code was changed and no PostgreSQL work was implemented.** This is a
decision record only.

**Verdict: PostgreSQL is not justified.**

---

## 1. The trigger being evaluated

ADR-0001 §18 states the deferral and its revisit conditions verbatim:

> | PostgreSQL | Deferred | Measured SQLite contention (`collection_metrics.failed`), or `btc_trading.db` > 10 GB. **Telemetry only — never governance.** |

Two conditions, either sufficient to *revisit*. Note what the trigger is not:
it is a trigger to **re-examine**, not a commitment to adopt. Meeting it opens
the question; it does not answer it.

The third clause is a hard constraint, not a preference: governance data is
excluded from any PostgreSQL discussion by INV-1 ("governance ledgers remain
SQLite, append-only, hash-chained, host-resident. No migration, no
containerization, no network write path, no ORM"). Nothing in this review can
or does reopen that.

---

## 2. Evidence reviewed

### 2.1 Measurement caveat — stated first because it bounds everything below

The measurements come from the **development host**, not production. The local
`btc_trading.db` is `PRAGMA user_version = 0` against a target of **4**
(`scripts/migrate.py: TARGET_VERSION = 4`), and its newest row is
`2026-05-21`, ~58 days stale. Consequently:

- **`collection_metrics` does not exist in this artifact.** The table arrived
  in schema v3. The ADR's *primary named trigger metric* — `collection_metrics.failed`
  — is therefore **not measurable from any artifact available to this review.**
- Observed collection cadence here (624 cycles over 23.4 days, mean gap ~54
  min) reflects intermittent dev runs, **not** the production cadence.

Growth projections below are therefore built from the **configured production
cadence in code**, not extrapolated from the dev sample. Where a number could
not be measured, it is labelled as such rather than estimated.

### 2.2 Configured production write pattern (from code, authoritative)

| Parameter | Value | Source |
|---|---|---|
| Snapshot cadence | **60 s** | `lifecycle.py:164` `snapshot_interval_sec: int = 60` |
| Chain refresh | 3600 s | `lifecycle.py:165` |
| Expiry observe | 300 s | `lifecycle.py:166` |
| Heartbeat | 1800 s | `lifecycle.py:167` |
| Universe | 11 strikes × 2 types × 3 expiries = **66** | `instruments.py:53-54` (`n_strikes=5`, `n_expiries=3`) |

`src/main.py:111` constructs `Runtime(...)` **without overriding any interval**,
so production runs these defaults: 1,440 write cycles/day over ~66 symbols.
Observed mean of 63.6 rows/cycle in the dev corpus corroborates the 66 figure,
as does CLAUDE.md's "64 symbols on a single `v2/ticker` subscription".

### 2.3 Measured artifacts

| Artifact | Size | Rows | Journal mode |
|---|---|---|---|
| `data/btc_trading.db` | 10.9 MB | 39,680 `option_quotes`, 636 `instruments` | WAL |
| `research/evidence_library/ledger.db` | **168 KB** | 26 receipts, 70 runs, 13 transitions, 142 gate_results | **delete** |
| `research/data/forward_evidence.db` | 584 KB | 1,987 `corpus_daily` / `signal_log` / `regime_states` | WAL |
| `research/data/evidence_forward.db` | 36 KB | **0 rows** (collector has never run on dev) | WAL |

`btc_trading.db` PRAGMAs: `page_size=4096`, `wal_autocheckpoint=1000` (≈4 MB),
`busy_timeout=5000`, `synchronous=2` (FULL).

Measured real storage cost: **274 bytes per `option_quotes` row** (file size ÷
row count, so inclusive of index and page overhead).

---

## 3. Trigger evaluation

### Condition A — `btc_trading.db` > 10 GB: **NOT MET** (10.9 MB, 0.1% of threshold)

But "not met" understates the finding. Projected from the configured cadence:

```
1,440 cycles/day × 66 symbols        =   95,040 rows/day
95,040 × 274 bytes                   ≈    26 MB/day
                                     ≈   9.5 GB/year
10 GB threshold reached in           ≈   ~13 months of continuous 24/7 collection
```

**This is the most important number in the review.** The 10 GB trigger is not a
distant hypothetical — it is roughly one year of uninterrupted operation away.
It has not been met, and the correct response when it is met is *not*
PostgreSQL (see §6), but it does mean a retention decision has a real deadline
rather than an indefinite one.

### Condition B — measured contention via `collection_metrics.failed`: **NOT MEASURABLE, and structurally near-impossible**

Not measurable for the reason in §2.1. What *can* be established from code is
that the architecture has close to the minimum contention surface a persistent
store can have:

**Writers to `btc_trading.db`:**

1. `btcbot.service` `SnapshotWriter` — the only sustained writer. **One
   process, one long-lived connection, one batched transaction per 60 s cycle**
   (`snapshot_writer.py:331` — all ~66 quote rows plus the `collection_metrics`
   row commit together, all-or-nothing, with `ROLLBACK` on failure).
2. `scripts/snapshot_instrument_map.py` — mapsnap timer, **once daily** at
   12:30 UTC.
3. `scripts/migrate.py` — manual, one-off.

**Readers:** heartbeat (in-process), `healthcheck.py` (every 10 min),
`backup.py` (`VACUUM INTO`, daily 02:00), `deployment_report.py` (manual), and
now the Phase 4 API (cached, `mode=ro`).

Timers are deliberately staggered — 00:05, 01:00, 02:00, 12:30, and `*:0/10` —
with `RandomizedDelaySec` on each. WAL permits concurrent readers with a single
writer without blocking. Measured commit cost for a full production-shaped
cycle stayed **3.35 ms → 7.03 ms across a 16× growth in table size** (132k →
2.1M rows), i.e. writes barely notice scale.

A single-writer, once-per-minute, batched-commit workload against WAL is not a
contention scenario. There is no plausible path to write contention that does
not first involve adding a second sustained writer — which nothing in the
roadmap proposes.

**Neither trigger condition is met. Under ADR-0001 §18, the deferral stands on
its own terms.**

---

## 4. What the evidence *did* surface: a latency cliff that is not an engine problem

The review's most actionable finding is unrelated to the triggers.

`option_quotes` has `PRIMARY KEY (instrument_id, ts)` and **no `ts`-leading
index**. Every time-ranged query is therefore an O(n) covering-index scan —
including `check_db_liveness()`'s `SELECT max(ts)`, which runs every 10 minutes
via the healthcheck dead-man and on every `/api/v1/runtime/health` call.

Measured scaling on a synthetic replica of the exact table shape and PK
(scratchpad only; no project data touched):

| Rows | Size | `MAX(ts)` | `ts`-range scan | Write cycle |
|---|---|---|---|---|
| 132,000 | 24.7 MB | 24.3 ms | 22.2 ms | 3.35 ms |
| 528,066 | 98.6 MB | 94.5 ms | 88.7 ms | 4.22 ms |
| 1,056,066 | 197.1 MB | 205.7 ms | 194.9 ms | 6.37 ms |
| 2,112,066 | 393.9 MB | 485.5 ms | 431.6 ms | 7.03 ms |

Cleanly linear. Extrapolated to the 10 GB trigger (~36.5M rows), the liveness
probe would take **~8 seconds** — an operational problem well before it is a
capacity problem.

**Adding one index resolves it completely.** Measured on the 2.1M-row table:

| Query | Before | After `CREATE INDEX idx_oq_ts ON option_quotes(ts)` |
|---|---|---|
| `SELECT MAX(ts)` | 392.4 ms | **0.01 ms** (~39,000×) |
| `ts`-range count | 404.6 ms | **5.85 ms** (~69×) |
| Write cycle (66 rows + commit) | 7.03 ms | **4.35 ms** (no penalty) |

Index build: 3.07 s on 2.1M rows. Plans confirm `SEARCH ... USING COVERING
INDEX idx_oq_ts` replacing full `SCAN`.

This is decisive for the adoption question: **the latency problem is a missing
index, not a missing engine.** PostgreSQL with the same missing index would
scan too. Adopting a new database to fix a one-line indexing gap would be
solving the wrong problem at maximum cost.

*(Recorded as a finding only. Per the task constraints, no index was created
and no schema was changed.)*

---

## 5. Assessment against each requested dimension

**SQLite write contention.** Structurally minimal: one sustained writer, one
batched commit per 60 s, staggered timers, WAL. Write cost is near-flat in
table size. The named trigger metric could not be read from available
artifacts and should be checked on the host, but no mechanism for contention is
present in the design.

**WAL behavior.** Correctly configured on the three live-data DBs
(`autocheckpoint=1000`, `busy_timeout=5000`, `synchronous=FULL`). No orphaned
`-wal`/`-shm` sidecars were found. WAL is also the reason ADR §7.3 V1 exists,
and V1 compliance is already established practice in the unit files. One
observation: `ledger.db` is `journal_mode=delete`, **not** WAL — worth knowing
because V1's rationale is WAL-specific, so read-only access to the ledger does
not strictly require the same directory-write allowance that the WAL databases
do. Not a defect; noted for a future least-privilege tightening.

**Database growth.** ~26 MB/day, ~9.5 GB/year at the configured cadence. Linear
and entirely predictable. Reaches the 10 GB trigger in ~13 months. This
warrants a retention policy on a real timeline — it does not warrant an engine
change.

**Query latency.** The one genuine degradation path, fully characterised in §4,
and fully addressable with an index inside SQLite.

**Concurrent access patterns.** One writer, several intermittent readers on
deliberately non-overlapping schedules. The Phase 4 API added readers, but
behind a 10 s single-flight cache using `mode=ro` connections, and was verified
to leave the ledger byte-identical. Concurrency is low by construction and the
architecture actively keeps it that way.

**Backup and restore.** Measured `VACUUM INTO` throughput of **144 MB/s** →
~1.2 min at 10 GB; `PRAGMA integrity_check` ~2.4 min at 10 GB. Both fit
comfortably in the 02:00 UTC window. The copy verified `integrity_check: ok`
with all rows preserved. SQLite's decisive operational advantage here is that a
database is **one file**: backup is a file copy with a SHA-256 manifest, and
restore is a file move. PostgreSQL would replace this with `pg_dump`/`pg_restore`
or PITR/WAL archiving, and would require rebuilding `backup.py`, the manifest
scheme, `restore.py`, and the BK2 restore drill.

**Operational complexity.** This is a single-operator, single-VPS deployment
with a 1–2 GB memory budget already allocated across Prometheus (512 MB),
Grafana (256 MB), Alertmanager (64 MB), node_exporter (64 MB) and the Phase 4
API (384 MB). PostgreSQL adds a persistent daemon, a second backup mechanism, a
second restore procedure, connection/role management, version-upgrade
migrations, and a new class of failure (server down, connections exhausted,
disk-full mid-transaction) against a store that currently cannot fail in any of
those ways. It also violates the spirit of §5.2's reasoning for not
containerizing the bot: *the proven thing is the asset; re-deriving it is the
dominant risk*.

**Governance implications.** Foreclosed, and correctly so. INV-1 and §18's
"never governance" clause both bar it. Volume confirms there was never a
technical case: `ledger.db` is **168 KB** with 26 receipts and 13 transitions
after the entire PR-1..PR-9 programme — growth measured in rows per week. More
importantly, the governance guarantees are *SQLite-shaped*: append-only
triggers, `BEGIN IMMEDIATE` serialisation (PR-3, PR-8), hash-chained receipts,
and a single-file artifact that can be hashed and archived. A network database
would add a write path to the one artifact ADR §12 BK1 calls "the only truly
irreplaceable" one. Not in scope, not reopened.

**Future scalability.** The plausible growth vectors are (a) more strikes /
expiries, (b) higher snapshot cadence, (c) mainnet + longer retention. All
three scale the *same single-writer append pattern* — the workload shape does
not change, only its rate. Each is better served by retention, partitioning,
and moving analytics to the columnar corpus than by a row-store server. A
genuine multi-writer or multi-node requirement would change this analysis, and
none exists or is proposed.

---

## 6. Risks, benefits, alternatives

### Risks of adopting PostgreSQL now

1. **Solves a problem that does not exist** (no contention) while leaving the
   real one (missing index) untouched.
2. **Destroys the one-file backup property** and forces a rewrite of
   `backup.py`, `restore.py`, the manifest scheme, and the BK2 drill — the
   drill that ADR §20 already flags as not yet performed.
3. **New failure class on a resource-constrained host**, competing for memory
   with the monitoring stack that exists to detect failures.
4. **Dual-engine permanence**: governance and forward evidence must stay
   SQLite, so PostgreSQL adds a second engine rather than replacing one.
5. **INV-1 boundary erosion**: introducing a network-reachable store adjacent
   to governance data creates a pressure gradient toward the exact migration
   INV-1 forbids.
6. **Violates the current mandate**: CLAUDE.md §15 is an active stabilization
   phase — "do not propose or implement new infrastructure ... propose
   observations, measurements, and documentation updates instead". This review
   is itself the sanctioned activity; adoption would not be.

### Genuine benefits of PostgreSQL (stated fairly)

Real, and none currently applicable: true concurrent multi-writer MVCC; native
declarative partitioning and `BRIN` indexes for time-series; a materially
better planner for complex analytical joins; richer types and extensions
(`TimescaleDB`); server-side roles making read-only enforcement structural
rather than test-enforced — which would, notably, close the one honest gap in
the Phase 4 API's read-only argument. These become compelling under multi-node,
multi-writer, or multi-consumer conditions. None of those hold.

### Alternatives, ranked by value-per-risk

1. **Add `idx_oq_ts` on `option_quotes(ts)`.** Measured 39,000× on the
   liveness probe, no write penalty, ~3 s build. Highest value, near-zero risk.
2. **Adopt a retention/rollup policy** before ~13 months elapse: keep raw
   quotes for N days, roll older data into per-minute/hourly aggregates or
   Parquet. This is the actual answer to the growth trigger.
3. **Partition by month** into separate SQLite files with `ATTACH` for
   cross-period queries — keeps the one-file-per-period backup property.
4. **Move analytical work to the Parquet corpus + DuckDB**, which is already
   the sanctioned research stack. Heavy scans do not belong in the operational
   store at all; `btc_trading.db` should stay a capture buffer.
5. **Instrument before deciding**: `collection_metrics.failed` and
   `stale_count` already exist in schema v3 and are exported by the Phase 2
   exporter. Alert on them and let the trigger prove itself with production
   data instead of inference.
6. **PostgreSQL** — reserved for a genuine multi-writer or multi-node
   requirement.

---

## 7. Recommendation

**PostgreSQL is not justified.**

Neither documented trigger condition is met. `btc_trading.db` is at 0.1% of the
10 GB threshold, and the contention trigger is not merely unmet but
structurally implausible for a single-writer, one-commit-per-minute, batched
WAL workload. Governance is excluded by INV-1 regardless of any measurement.

The review did find a real degradation path — O(n) time-range queries from a
missing `ts` index, reaching ~8 s at the 10 GB mark — and a real deadline: ~13
months to the growth trigger at the configured cadence. Both are addressed
inside SQLite, the first by one index (measured 392 ms → 0.01 ms) and the second
by a retention policy. Adopting a database server would address neither more
effectively while forfeiting single-file backup, adding a daemon to a
memory-constrained host, and creating a second engine permanently alongside the
SQLite that governance must keep using.

### Recommended follow-ups (not implemented here, each requiring its own gate)

1. Read `collection_metrics.failed` and `stale_count` **on the production
   host** to close the one evidence gap this review could not.
2. Consider `idx_oq_ts` on `option_quotes(ts)` as a Phase-appropriate change.
3. Open a retention-policy decision well before the ~13-month growth horizon.
4. Re-run this review if any of: a second sustained writer is introduced,
   `collection_metrics.failed` becomes persistently non-zero, or
   `btc_trading.db` exceeds 10 GB.

### Suggested ADR-0001 §18 amendment (proposal only, not applied)

The current trigger reads as though meeting it implies adoption. Recommend
clarifying that the triggers open a **review** (this document), and adding
"missing-index and retention remedies must be evaluated and rejected before
PostgreSQL is considered" — so the next reviewer inherits the reasoning rather
than re-deriving it.
