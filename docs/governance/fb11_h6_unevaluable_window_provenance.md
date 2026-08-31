# FB-11 — h6 Unevaluable-Window Provenance Record (2026-06-16 → 2026-08-26)

**Read this before counting h6 observation days, computing h6 continuity, or
assessing h6 sample size.**

**Status: permanent provenance record. Documentation-only — this record changes
no runtime semantics.**

---

## 1. The interval

`h6_funding_vol_regime` was **collected but not evaluable** on every day from
**2026-06-16 through 2026-08-26 inclusive** — **70 collected days**.

On each of those days the collector ran, reported success, and wrote a row.
None of those rows records a determination of h6's signal. The signal was never
computed on those days.

| | |
|---|---|
| Interval | **2026-06-16 … 2026-08-26 inclusive** |
| Collected days in interval | **70** |
| Trade entries / exits / closed trades | **0 / 0 / 0** |
| First evaluable day | **2026-08-27** |
| Days additionally absent in the span | 2 — 2026-07-01 and 2026-08-01, both month-rollover days |

---

## 2. Classification: `BACKFILL_REQUIRED` → `BLOCK`

In the frozen vocabulary of PR-7's Data Completeness Framework
(`research/dq/states.py`, `docs/governance/data_completeness_gate.md` §2/§3):

| field | value |
|---|---|
| State | **`BACKFILL_REQUIRED`** (severity 4) on the `funding` input |
| Policy action | **`BLOCK`** — a required input that will never self-heal |
| Self-heals? | **No** — an explicit operator ingest action was required, and occurred |

The state's frozen definition fits without adaptation: *"Identical symptom to
INCOMPLETE_DATA, but the late-arrival budget has expired. This day will never
self-heal. An explicit ingest action is required."*

**No new state, schema, or governance semantics were needed to classify this.**
The existing vocabulary was sufficient (FB-11 pre-gate audit,
`fb11_unevaluable_window_mechanism_audit.md`).

---

## 3. Cause: insufficient funding history, resolved by the 2026-08-26 backfill

h6's frozen decision function requires
`vol_regime == medium AND funding_regime == low AND market_regime not in
{bear, unknown}`.

`_funding_regime()` returns **`unknown`** whenever fewer than
`REGIME_WINDOW = 252` valid funding observations exist inside the decision
window (`MIN_HISTORY = 273` rows). `daily_ingest.py` writes only the **current**
month, so the research funding lake held roughly two months of history — far
below 252.

A backfill of **19 historical monthly partitions (2025-01 … 2026-05)** was
written on **2026-08-26 08:25–08:26 UTC**. The lake then held **608 distinct
days (2025-01-01 … 2026-08-31)**, 607 of them satisfying the corpus query's
`COUNT(*) >= 2` guard.

The effect was immediate and is visible as a single clean transition:

```
2026-06-16   funding_regime -> unknown
2026-08-27   funding_regime -> high
```

The first h6 run after the backfill was evaluable. Reproducing the corpus load
today yields **272 of the last 273 days valid** against a requirement of 252.

**h6 could not have fired on any day before 2026-08-27, regardless of market
conditions.** On four of those days `vol_regime` was `medium` — h6's volatility
condition satisfied — and the outcome still could not be determined, because
the funding input did not exist.

---

## 4. The unevaluable interval versus legitimate zero-evidence

These two periods look identical in `signal_log` and must never be conflated:

| period | days | meaning |
|---|---|---|
| **2026-06-16 … 2026-08-26** | **70** | **Unevaluable.** The signal was not computed. `h6_canonical = 0` is not a determination |
| **2026-08-27 onward** | ongoing | **Legitimate zero-evidence.** Inputs present and valid; `funding_regime = high` while h6 requires `low`; correctly flat |

From 2026-08-27 h6 is a fully functioning collector producing genuine
observations. Its continued flatness is a **real research outcome** and is
evidence — of the market, and of h6's selectivity. The 70 preceding days are
not.

---

## 5. Permanent exclusion from live-forward evidence

**The 70 days are permanently excluded from h6's live-forward (L2) evidence.**
They may never be counted as:

- observation days, active days, or calendar coverage for h6;
- input to any h6 sample-size, continuity, or floor assessment;
- days on which h6 "was flat";
- part of any window over which an h6 forward return series is constructed.

Any future h6 forward-return series must begin **no earlier than 2026-08-27**.

This is immutable provenance, in the same sense as CLAUDE.md §20's INC-0005
gap: it will appear in every future recount, and its *absence* from a recount
would mean something silently closed it.

---

## 6. Prohibition: no retroactive recomputation, no retroactive receipts

**Neither of the following is permitted.**

**6.1 No retroactive recomputation into forward evidence.** Since the
2026-08-26 backfill, every input needed to compute h6 over 2026-06-16 …
2026-08-26 now exists. **This makes a wrong action available that was not
available for INC-0005**, and it is explicitly foreclosed here.

Recomputing those dates would produce **retrospectively computed** values.
Retrospective computation is not `live-forward` by definition — the lane is
constituted by *when and how* a value was produced, not by which inputs were
available. Such values could at most be **`offline-corpus`** evidence in a
different lane, and writing them into the forward corpus would be precisely the
backfill CLAUDE.md §20 forbids. It would also destroy the one thing that makes
forward evidence worth collecting: that it was produced without knowledge of
what came after.

**6.2 No retroactive readiness or evidence receipts.** No readiness verdict,
`evidence.admit`/`.reject`, or governance receipt may be emitted for those days.
Two frozen reasons:

- PR-7's **L4** — *"a non-READY verdict must never produce a durable row that a
  later run treats as done."* Emitting 70 receipts would manufacture governance
  rows for days nobody evaluated.
- The framework reserves a **`NO_RECEIPT`** count for exactly *"days nobody
  evaluated"*. Retro-emitting receipts would erase that signal.

---

## 7. Causal provenance is preserved in production data

This record does not rest on assertion. The cause remains independently
verifiable from production data that already exists and is not modified here:

| evidence | where |
|---|---|
| `funding_regime = 'unknown'` on all 70 days | `research/data/forward_evidence.db` → `regime_states` |
| Single transition to `high` on 2026-08-27 | same table, ordered by `day_ts` |
| Backfill timestamps, 19 partitions | `data/raw/funding/BTCUSDT/year=*/month=*/data.parquet` file mtimes, 2026-08-26 08:25–08:26 |
| 252-day requirement | `research/forward_evidence/decision_function.py`, `REGIME_WINDOW` |

**`regime_states.funding_regime` is the load-bearing artifact.** It preserves
the causal provenance of the interval, which is why this record could be written
from evidence rather than from memory, and why no new evidence had to be
created.

---

## 8. Bounded observability defect

`signal_log` and `run_log` represented the 70 unevaluable days as **ordinary
rows**: `h6_canonical = 0`, `status = 'ok'`, `anomalies_json = '[]'` — the same
representation a genuinely flat, fully-evaluated day receives. No explicit
unavailable marker was written.

This is a data-quality defect measured against the platform's own frozen
precedent: `DegradationSpec` requires unavailable outputs be marked explicitly
and *"never NULL, never imputed, never ffilled, never zero-filled."*

**Its scope is bounded, and overstating it would be its own error:**

- **No demonstrated trading-decision impact.** This is paper trading; h6 has
  no trades, no positions, and **0 `live-forward` runs** in the ledger (it holds
  2 `offline-corpus` runs).
- **No governance decision was affected.** h6's only declared floor is
  `promotion_criteria.minimum_trades = 30`, which counts trades — 0 in and out
  of the interval — so no floor, readiness verdict, admissibility payload, DSR,
  BH-p or pairwise measurement consumed these rows.
- **The cause was recorded**, in `regime_states` (§7). The defect is that it was
  never *propagated* to the signal value, the run status, or any consumer.

The residual risk this record closes is precise: that a **future** reader treats
70 rows of `0` as evidence that h6 was flat.

---

## 9. This record changes no runtime semantics

Explicitly:

- **No code, schema, contract, or PR-7 module is modified.**
- No h6 row is altered, deleted, reclassified, or annotated in any database.
- No registry, pre-registration, threshold, scheduler, collector, funding-ingest
  or ledger change.
- No receipt is emitted; the governance receipt chain is untouched.
- h6 **remains in forward collection**, unchanged, and continues to produce
  genuine observations from 2026-08-27 onward.

The 70 rows stay exactly as collected. This record governs **how they are
read**, not what they are.

---

## 10. Future engineering, recorded here only as a pointer

Not part of this gate, and deliberately not acted on:

- **Adopt PR-7 for the forward collectors** (`data_completeness_gate.md` §12
  PR-2). This would make the unevaluable-versus-flat distinction structural
  rather than reconstructable after the fact. Its example set should gain the
  **window-depth / warmup** failure class, which this interval demonstrates and
  which the framework's documented F1–F4 classes do not cover.
- **Month-rollover collection loss** — 2026-07-01 and 2026-08-01 absent; will
  recur without a fix.
- **`corpus_daily.funding_bps` is `NULL` on every row**, because the job records
  a day whose funding is not yet ingested — a recording defect that forced the
  root cause to be reconstructed from parquet file timestamps.

Each requires its own gate.

---

## 11. Trail

| Gate | Outcome |
|---|---|
| FB-10c-T | Surfaced h6's 75 days of zero evidence |
| FB-10c-H6 | Root cause: 70 days B (dependency missing), 5 days A (legitimate) |
| FB-11 pre-gate | Existing PR-7 vocabulary sufficient; effect is provenance only; documentation-only is correct |
| **FB-11** | **This record. Permanent provenance; recomputation and retroactive receipts prohibited** |
