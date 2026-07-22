# ENG-017 — Operational SQLite Performance Review

Follow-up to the PostgreSQL Adoption Review (2026-07-18), which found a real
latency cliff in `option_quotes` and concluded it was an indexing gap rather
than an engine limitation. This ticket verifies that conclusion, fixes it, and
records the scaling characteristics.

**Outcome: one index added (`idx_option_quotes_ts`). No PostgreSQL migration.**

---

## 1. Root cause

`option_quotes` is declared `PRIMARY KEY (instrument_id, ts)`. `ts` is therefore
a **non-leading** index column, and SQLite cannot use the PK to restrict a
time range. Every time-ranged query degraded to a full scan, O(rows) — including
`healthcheck.py:190`'s `SELECT max(ts)` dead-man probe, which runs every 10
minutes via `btcbot-healthcheck.timer` and again on every
`/api/v1/runtime/health` call.

Before this ticket the entire schema contained exactly one explicit index
(`idx_instruments_underlying_type`). There was no index on any time column.

---

## 2. Query plan review

Every query below was taken **verbatim from source**, not invented for the
benchmark, and run against a v4-schema replica at production shape and cadence
(1,440 cycles/day × 66 instruments; 2,851,200 rows ≈ 30 days ≈ 553 MB).

| Source | Query shape | Before | After | Verdict |
|---|---|---|---|---|
| `healthcheck.py:190` | `SELECT max(ts)` | 512.8 ms `SCAN` | **0.01 ms** `SEARCH` | **fixed** |
| `dq/probes.py:197` | `ts >= ? AND ts < ?` | 624.0 ms `SCAN` | **24.2 ms** `SEARCH` | **fixed** (25.8×) |
| `dq/probes.py:278` | `DISTINCT DATE(ts)` + range | 741.9 ms `SCAN` | **91.9 ms** `SEARCH` | **fixed** (8.1×) |
| `certify_day.py:215` C2 | `WHERE DATE(ts) = ?` | 1,439 ms | 1,626 ms | unchanged — non-sargable |
| `certify_day.py:256` C3 | `WHERE DATE(ts) = ?` | 1,550 ms | 1,707 ms | unchanged — non-sargable |
| `certify_day.py:308` C4 | `WHERE DATE(ts) = ?` + GROUP BY | 2,029 ms | 1,857 ms | unchanged — non-sargable |
| `certify_day.py:350` C5 | `WHERE DATE(ts) = ?` | 1,568 ms | 1,686 ms | unchanged — non-sargable |
| `partition_reader.py:72` | `ts LIKE ? \|\| '%'` | 1,207 ms | 1,253 ms | unchanged — see §4 |
| `certify_day.py:161` C1 | `collection_metrics` `DATE(ts)` | 11.9 ms | 14.4 ms | fine (1,440 rows/day) |
| `snapshot_instrument_map.py:49` | `seen_date = ?` | 0.02 ms | 0.01 ms | already served by PK |

**Not measured as slow, so not touched:** `deployment_report.py` and
`report.py` query `forward_evidence.db` (584 KB) and `ledger.db` (168 KB). All
their queries measured **0.007–0.054 ms**. No index is justified, and the
governance ledger is not modified — INV-1 is untouched by this ticket.

---

## 3. Why exactly one index, and not two

`WHERE DATE(ts) = ?` wraps the column in a function, so no plain index on `ts`
can restrict it. Two options were measured against each other:

| Option | Certifier C2 | partition_reader | Storage | Write cycle |
|---|---|---|---|---|
| Expression index `option_quotes(DATE(ts))` | 50.8 ms (29.5×) | **1,282 ms (1.0×)** | +54 MB / 2.85M rows | +3.5 ms (+107%) |
| **Sargable rewrite** `ts >= day AND ts < next` | **43.3 ms (34.2×)** | **140.5 ms (9.0×)** | **+0 MB** | **+0 ms** |

The rewrite is strictly better on every axis: faster than the expression index,
free, and it also fixes `partition_reader.py`, which **no index can help**. A
second index was therefore rejected.

Result equivalence was verified, not assumed: both spellings returned identical
result sets. The equivalence rests on `ts` being lexicographically sortable
ISO-8601 UTC, which was confirmed at the source (`snapshot_writer.py:225` writes
`datetime.now(timezone.utc).isoformat()`, single writer) and in the data
(`SELECT DISTINCT substr(ts,-6)` over the live DB returns exactly `+00:00`).

**The rewrites are NOT implemented in this ticket.** `certify_day.py` produces
per-day evidence-admissibility verdicts written to `daily_certifier_log`;
changing its queries is a governance-adjacent behavioural change that deserves
its own ticket and its own equivalence testing, not a drive-by edit inside a
performance ticket. It is recorded as a follow-up in §7 with the measurements
already done.

`tests/test_migrate_v5.py::test_date_function_wrapper_does_not_use_index` pins
this: it asserts the `DATE(ts)` plan is a `SCAN` while the sargable form is a
`SEARCH`, so a future engineer who tries to fix the certifier with another
index finds the real answer in a failing test.

---

## 4. A subtlety worth knowing

After the index exists, the `DATE(ts)` plan reads
`SCAN option_quotes USING COVERING INDEX idx_option_quotes_ts`. **It names the
index but does not benefit from it.** SQLite scans the index because it is a
narrower covering structure than the table — but it is still a full `SCAN`, not
a range `SEARCH`, so the cost stays O(rows). The measured timings above confirm
this: those four queries did not improve.

`SEARCH` vs `SCAN` is the signal. The index appearing in a plan is not.

---

## 5. Scaling characteristics (measured, four sizes)

`SELECT max(ts) FROM option_quotes`, production shape:

| Days | Rows | Table MB | Without index | With index | Index MB |
|---:|---:|---:|---:|---:|---:|
| 5 | 475,200 | 92 | 104.4 ms | 0.012 ms | 16 |
| 15 | 1,425,600 | 274 | 322.5 ms | 0.011 ms | 49 |
| 30 | 2,851,200 | 553 | 713.9 ms | 0.011 ms | 97 |
| 60 | 5,702,400 | 1,117 | 1,630.3 ms | 0.017 ms | 195 |

- **Unindexed: O(n), linear.** 12× the rows → ~15.6× the time.
- **Indexed: O(log n), effectively flat.** 12× the rows → no measurable change.
- **Index overhead: ~34 bytes/row (~17.5%)**, stable across all four sizes.

Extrapolating to the ADR §18 10 GB trigger (~36.5M rows): the probe would have
reached **~10 seconds** unindexed, and stays at ~0.02 ms indexed.

### Cost side

| Metric | Value |
|---|---|
| Write cycle (66 rows + metrics + commit), no index | 3.24 ms mean |
| Write cycle with `idx_option_quotes_ts` | 3.50 ms mean |
| **Write overhead** | **+0.26 ms/cycle (+8%)** |
| Duty cycle at 60 s cadence | 0.006% of wall clock |
| Storage growth, before | ~26.0 MB/day (~9.5 GB/yr) |
| Storage growth, after | ~29.3 MB/day (~10.7 GB/yr) |

**This accelerates the ADR §18 10 GB trigger from ~384 days to ~341 days** — a
~6-week trade, accepted deliberately to remove an O(n) probe from the 10-minute
dead-man path. It strengthens rather than weakens the review's recommendation to
open a retention decision on a real timeline (§7).

---

## 6. Changes made

| File | Change |
|---|---|
| `src/data/storage.py` | `CREATE INDEX IF NOT EXISTS idx_option_quotes_ts ON option_quotes(ts)` in `SCHEMA`, so **fresh** databases are never slower than migrated ones |
| `scripts/migrate.py` | `TARGET_VERSION` 4 → 5; new guarded 4→5 step |
| `tests/test_migrate_v5.py` | **New**, 19 tests |
| `tests/test_migrate_v4.py` | End-state assertions retargeted from literal `4` to `TARGET_VERSION`; `test_target_version_is_4` → `test_v4_step_is_still_in_the_chain` |

No runtime code path, query, schema table, or governance artifact was changed.
The index is additive and invisible to every existing caller.

### Verification performed

- Migration **dry-run** on a copy of the real DB: reported the full v0→v5 plan, wrote nothing (`user_version` still 0, index absent).
- Migration **applied** to that copy: `user_version = 5`, `integrity_check = ok`, 39,680 rows preserved, plan switched to `SEARCH ... USING COVERING INDEX idx_option_quotes_ts`.
- **Idempotent** re-run: all steps `=` (already present), version unchanged.
- **Fresh** DB via `init_schema()` carries the index — parity with migrated.
- Real `healthcheck.check_db_liveness()` at 2.85M rows: **511.3 ms → 1.61 ms (318×)** end-to-end (the residual is connection setup and timestamp parsing, not the query).
- Full suite: **1,281 passed**, 2 failures — the same pre-existing date-dependent expiry tests documented in CLAUDE.md, unrelated to this work.

### Deployment

```bash
sudo systemctl stop btcbot.service        # recommended on a large DB (see below)
cd /opt/btcbot && python scripts/migrate.py --dry-run
python scripts/migrate.py
sudo systemctl start btcbot.service
```

`CREATE INDEX` is the first migration step whose cost scales with table size:
it sorts every existing row and holds a write lock while doing so. Measured
**4.3 s at 2.85M rows**; extrapolate **~55 s at 36M rows**. Every earlier step
is metadata-only. Running it against a live writer is safe but will block
snapshot commits for that window, so stopping the bot is preferred.

**Rollback:** `DROP INDEX idx_option_quotes_ts; PRAGMA user_version = 4;` —
instant, no data loss, no schema change to undo. The index holds no data of its
own.

---

## 7. Follow-ups (measured here, not implemented)

1. **Sargable rewrite in `certify_day.py`** (C2/C3/C4/C5): replace
   `WHERE DATE(ts) = ?` with `WHERE ts >= ? AND ts < ?`. Measured 21–34× with
   the index already in place, zero additional storage. Needs its own ticket
   because it touches evidence-admissibility verdicts. Equivalence argument and
   the UTC-offset precondition are recorded in §3.
2. **Same rewrite in `partition_reader.py`**: `ts LIKE ? || '%'` →
   `ts >= ? AND ts < ?`. Measured **9.0×**. This is the one path an index alone
   can never fix, and it runs daily for archival.
3. **Retention policy.** Unchanged from the PostgreSQL review, now slightly more
   urgent: ~341 days to the 10 GB trigger with the index in place.
4. **Re-measure on the production host.** All figures here are from a synthetic
   replica on a dev machine. The shapes and ratios will hold; the absolute
   numbers depend on the VPS's disk.
