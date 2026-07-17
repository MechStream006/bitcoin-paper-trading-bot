# Data Completeness Gate — Platform Framework Specification

**Status:** §12 **PR-1 (Core engine) implemented and merged 2026-07-14** as
Governance Architecture PR-7 — package `research/dq/`, 187 tests. See
`data_completeness_framework_schema_reference.md` for the implemented
schema, public interfaces, three documented deviations from a literal
reading of this document (all resolved toward its stated intent), and the
per-criterion status of §13. The `readiness.verdict` engine now exists:
contracts, checks, verdicts, receipts, and point-in-time replay under the
historical contract.

**No adopters yet.** §12's PR-2 (H6 / `corpus_daily` adopts it — including
the readiness-aware `_already_processed()`, "the most important line in the
entire body of work"), PR-3 (forward-evidence collectors), PR-4 (governance
integration — §7's G1/G2/G3), PR-5 (observability / `readiness_board.md`)
and PR-6 (platform rollout) are all still outstanding.

**Consequently §7's governance integration is NOT yet in force**, and the
note it superseded still holds in the interim: Governance Architecture PR-6
wired `evidence.admit`/`.reject` receipts for the Evidence Library using
`lifecycle.check_candidate_gate()` as the admissibility rule specifically
*because* this engine did not exist. That rule is unchanged. Readiness is
not yet an admissibility precondition; making it one is a governance change
(G1/G2) and belongs to the adoption PR, not to the framework. The
substitution point PR-6 built for exactly this purpose is proven to work —
see the schema reference's §13 — but is deliberately left unused here. Until
it is wired, `evidence.admit` continues to attest *gate-check pass/fail*,
not *input-data completeness*.
**Author:** Principal Data Platform Architect
**Date:** 2026-07-13
**Scope:** Every derived pipeline on the platform — current (H6, H5, G1, G3, IVRV,
`corpus_daily`, SnapshotWriter) and future (Greeks, IV surface, liquidity, Feature
Store, Dataset Engineering, ML/RL feature generation).
**Governance impact:** Introduces a readiness verdict that becomes an
**admissibility precondition** for Evidence Library runs. Changes no threshold, no
gate, no decision function, no state machine.

---

## 0. The lesson H6 actually taught

H6 is usually described as "the job read a partial day." That is the *symptom*.
The pipeline contained **four independent failure classes**, and not one of them
was detected, because the pipeline had no concept of *asking whether its inputs
were fit to derive from*. It assumed. Every one of these will recur, in a new
pipeline, with a new name, unless the assumption is replaced by a structure.

| # | Failure class | H6 manifestation | Generalises to |
|---|---|---|---|
| **F1** | **Temporal incompleteness** — the period is still open | `daily_job` processed `corpus.iloc[-1]`, which at its 00:05 UTC run time is *today, five minutes old*. `spot_close` was a 00:05 intraday tick, not a daily close. | Any bar/window/session-grained derivation. IV surface snapshots. Any "EOD" concept in a 24/7 market. |
| **F2** | **Coverage incompleteness** — the period is closed but under-populated | Only 1 of the day's 3 funding events (00:00/08:00/16:00 UTC) had fired. The corpus query's `HAVING COUNT(*) >= 2` **correctly** dropped the day → `funding_bps` NULL. The query did its job. **The derivation then consumed the NULL as if it were a value.** | Missing minutes in OHLCV. Partial option chains. A Greek computed from a half-populated surface. Any `COUNT(*) >= k` guard whose failure is expressed as a NULL. |
| **F3** | **Latched corruption** — a *transient* incompleteness became *permanent* | `corpus_daily` uses `INSERT OR IGNORE`, and `_already_processed()` keys on *day exists*. So the partial day was written once and then **never revisited**, forever. The nightly fix protects only future days; `repair_h6_partial_days.py` exists solely to undo this. | **Every** idempotent-by-primary-key writer on the platform. This is the most dangerous class and the least visible. |
| **F4** | **Silent schema drift** — declared ≠ written | `rv_21d`, `atr14`, `atr_ratio` were declared in the schema but **absent from the INSERT's column list**. They had never been written, for any row, ever. Nothing noticed. | The Binance µs/ms switch (CLAUDE.md §11). Any column added to a schema but not to a writer. Any unit change upstream. |

**And the contract already existed — it just wasn't a *thing*.** The sibling
collector had it: `run_daily._derive_run_date()` returns *yesterday* UTC, "the last
complete BTC calendar day," and `runner_base.load_daily_closes()` raises
`DataUnavailableError` rather than deriving from short history. `daily_job.py`
never got that contract, because the contract lived as **copy-pasted intent in two
places instead of as one enforced object**. Two divergent, informally-implemented
completeness contracts already coexist in this codebase today. The framework's job
is to make there be exactly one, declared as data, enforced by shared code.

**The framework's core thesis:** completeness is not a property a pipeline
*checks*. It is a **contract an input must satisfy before a derivation is permitted
to exist**, and the verdict of that check is itself **evidence** that must be
persisted, reported, and consumed by governance.

---

## 1. Architecture

```
                                 ┌──────────────────────────────────┐
  SOURCES                        │      CONTRACT REGISTRY           │
  ┌────────────────┐             │  contracts/*.yaml (declarative,  │
  │ Parquet lake   │             │  versioned, content-hashed)      │
  │ evidence_*.db  │             └───────────────┬──────────────────┘
  │ btc_trading.db │                             │ resolve(feature_id, as_of)
  │ REST / WS      │                             ▼
  └───────┬────────┘             ┌──────────────────────────────────┐
          │  probe (metadata     │      COMPLETENESS GATE           │
          │  ONLY — never a      │                                  │
          │  full data load)     │  1 resolve   → Contract          │
          └─────────────────────►│  2 probe     → [InputObservation]│
                                 │  3 check     → [CheckResult]     │
                                 │  4 aggregate → Verdict           │
                                 │  5 policy    → Action            │
                                 └───────────────┬──────────────────┘
                                                 │
                    ┌────────────────────────────┼────────────────────────────┐
                    ▼                            ▼                            ▼
         ┌────────────────────┐      ┌────────────────────┐      ┌────────────────────┐
         │  DERIVATION        │      │  RECEIPT STORE     │      │  OBSERVABILITY     │
         │  (runs ONLY if the │      │  data_readiness    │      │  logs / heartbeat  │
         │  action permits)   │      │  (append-only)     │      │  readiness_board   │
         └─────────┬──────────┘      └─────────┬──────────┘      └────────────────────┘
                   │                           │
                   │  every derived row carries│
                   │  readiness + receipt_id   │
                   ▼                           ▼
         ┌──────────────────────────────────────────────────────┐
         │            EVIDENCE LIBRARY (governance)             │
         │  admissibility = f(readiness of every input day)     │
         └──────────────────────────────────────────────────────┘
```

### Architectural laws

**L1 — Declare, don't code.** Completeness requirements are **data** (a versioned
YAML contract), not `if` statements inside a derivation. The gate engine is
generic and shared; only contracts differ per feature. A new pipeline adds a
contract file, not a code path. *(This is what makes it reusable rather than
another H6-shaped `if`.)*

**L2 — Fail closed.** Absence of evidence of completeness is **not** evidence of
completeness. Any check the gate could not perform yields `UNKNOWN`, and `UNKNOWN`
blocks. A gate that cannot tell you whether the data is good must never say yes.

**L3 — Probe metadata, not payload.** The gate reads counts, bounds, hashes,
schemas, and timestamps — never the full dataset. Gating must be orders of
magnitude cheaper than deriving, or it will be skipped under load, and a gate that
gets skipped is not a gate.

**L4 — A non-READY verdict must never produce a durable row that a later
completeness check will treat as done.** *This is F3, and it is the single
highest-value law in this document.* Mechanically:
- Preferred: **do not write.** An unwritten day is automatically revisited.
- If a degraded row *must* be written, it carries its `readiness`, and every
  `_already_processed()`-style predicate becomes **readiness-aware**:
  `WHERE day = ? AND readiness = 'READY'` — never `WHERE day = ?`.

That one predicate change converts H6's permanent corruption into a self-healing
retry, for **every** writer on the platform, forever.

**L5 — The verdict is evidence.** It is persisted, immutable, provenance-carrying,
and consumed by governance. A derivation's readiness receipt is as much a part of
its lineage as its `fn_hash`.

**L6 — `now` is an argument, never a syscall.** No component of the gate reads the
wall clock. `as_of` is injected. This is what makes the gate deterministic,
point-in-time correct, and replayable — the same properties the evidence corpus
already demands.

---

## 2. Readiness states

Eight states. **Severity-ordered** (later = worse); when a contract has multiple
inputs, the aggregate verdict is the **worst** state across all of them
(worst-wins).

| # | State | Produced when | Self-heals? | Root cause lives with |
|---|---|---|---|---|
| 0 | **READY** | Every check passed. The inputs are complete, fresh, correctly-shaped, and the period is closed. | — | — |
| 1 | **PARTIAL_DAY** | The observation period **has not fully elapsed** as of `as_of`. `period_end > as_of`. The data is not wrong — it is *not finished*. | **Yes — by time alone.** | Nobody. Wait. |
| 2 | **STALE_DATA** | Period closed; newest input record is **older than the input's freshness SLA**. The upstream collector has stalled. Distinct from INCOMPLETE_DATA: the data that exists is fine, there just isn't *recent* data. | Yes, if the collector recovers. | The **collector**. |
| 3 | **INCOMPLETE_DATA** | Period closed and elapsed, but **coverage is below the contract's floor** — and the input's **late-arrival budget has not yet expired**. (H6: 1 of 3 funding events; 900 of 1440 minutes.) Upstream may still deliver. | **Maybe** — late-arriving data may complete it. | The **source**, possibly. |
| 4 | **BACKFILL_REQUIRED** | Identical symptom to INCOMPLETE_DATA, but the **late-arrival budget has expired**. This day will **never** self-heal. An explicit ingest action is required. | **No.** | An **operator / ingest job**. |
| 5 | **SCHEMA_MISMATCH** | Observed columns, dtypes, units, or value-magnitude bands **contradict the declared schema**. (Binance ms→µs; `rv_21d` declared-but-never-written; an option ticker field becoming a string.) | **No.** | **Code**, on one side or the other. |
| 6 | **CONFIGURATION_ERROR** | The **contract itself** is unusable: unresolvable path, unknown source type, unknown probe, a coverage floor that exceeds the period's maximum possible count, a contract version that does not exist. The gate never got as far as looking at data. | **No.** | The **contract author**. |
| 7 | **UNKNOWN** | The gate **could not determine readiness**: a probe raised, the source was unreachable, required metadata was absent, or a check errored. **Never a pass.** | — | Unknown — that is the point. |

### The three-way split that matters most

PARTIAL_DAY / INCOMPLETE_DATA / BACKFILL_REQUIRED can present as **the identical
physical symptom** — "there are fewer rows than I expected." Collapsing them into
one state is precisely how H6 happened: the pipeline saw a NULL and could not
distinguish *"not finished yet"* from *"broken forever."* They are separated by
**two clocks**, and each demands a **different remedy**:

```
period_end > as_of ................................... PARTIAL_DAY      → DEFER  (time fixes it)
period closed, coverage < floor, within late budget ... INCOMPLETE_DATA → RETRY  (source may fix it)
period closed, coverage < floor, late budget expired .. BACKFILL_REQUIRED → BLOCK (only an operator fixes it)
```

The late-arrival budget (`late_arrival_hours`) is declared **per input**, because
it is a property of the *source*, not of the feature: Binance monthly klines
settle within hours; a Deribit DVOL day is available minutes after close; a
funding series is complete at 16:00 UTC + ε.

### Ordering of checks is normative

Checks run in this order, and the **first** state produced at a severity ≥ 5 short-
circuits:

```
1. CONFIGURATION  → CONFIGURATION_ERROR   (is the contract even usable?)
2. SCHEMA         → SCHEMA_MISMATCH       (is the data shaped as declared?)
3. TEMPORAL       → PARTIAL_DAY           (is the period closed?)
4. FRESHNESS      → STALE_DATA            (is the newest data recent enough?)
5. COVERAGE       → INCOMPLETE_DATA / BACKFILL_REQUIRED
6. CONTINUITY     → BACKFILL_REQUIRED     (interior gaps in the history window)
```

**This order is not cosmetic.** If coverage were checked before schema, the
Binance ms→µs switch — which silently produces a **0-row partition** (CLAUDE.md
§11) — would present as INCOMPLETE_DATA, and the operator would "backfill" the
same broken month forever, chasing a data problem that is actually a code problem.
**Schema drift must never be allowed to masquerade as missing data.**

---

## 3. Downstream reaction: the policy matrix

The downstream pipeline **does not decide** what to do. Ad-hoc reaction is what
produced two divergent contracts in the first place. The reaction is a **declared
policy**, resolved from `(state, input criticality)`, and the pipeline simply
executes the returned `Action`.

### Actions

| Action | Semantics |
|---|---|
| `COMPUTE` | Derive normally. Write. Stamp `readiness=READY`. |
| `DEFER` | **Not an error.** Do nothing, write nothing, exit 0, log at INFO. The day will be reconsidered on the next cycle. The absence of a row *is* the retry mechanism (L4). |
| `RETRY` | Re-probe after a bounded backoff, within this run. On exhaustion, escalate to the state's terminal action. |
| `DEGRADE` | Compute the subset of outputs whose **required** inputs are all READY. Outputs depending on the missing **optional** input are written as explicitly `unavailable` — **never as NULL, never as an imputed value.** Stamp the row `readiness=DEGRADED_*`. |
| `BLOCK` | Do not derive. Do not write. Emit a **remediation instruction**. Exit non-zero. This is a *known* failure with a *known* fix. |
| `HALT` | Stop the pipeline **and every sibling pipeline sharing the contract**. Page a human. Used only where continuing risks **silent corruption**. |
| `INADMISSIBLE` | A modifier, not an alternative: the row may exist, but it is barred from being cited as evidence (§7). |

### The matrix

| State | Required input | Optional input | Rationale |
|---|---|---|---|
| READY | `COMPUTE` | `COMPUTE` | |
| PARTIAL_DAY | **`DEFER`** | `DEFER` | Time is the fix. Writing *anything* now is the H6 bug. **Never warn** — a routine 00:05 deferral that pages someone is a defect in the alerting, not a signal. |
| STALE_DATA | `RETRY` → `DEGRADE` if a degradation path is declared, else `BLOCK` | `DEGRADE` | The IVRV precedent, generalised: a stale/missing DVOL day yields `ivrv=unavailable` and **never blocks H5/G1/G3** (CLAUDE.md §16). Also raises an **operational** alarm — a stalled collector is invisible by construction, and absence of evidence is itself evidence of a broken pipeline. |
| INCOMPLETE_DATA | `RETRY` (bounded, within late budget) → then re-verdict | `DEGRADE` | The source may still deliver. Retrying is cheap; deriving from a short window is not recoverable. |
| BACKFILL_REQUIRED | **`BLOCK`** + remediation | `DEGRADE` | It will not self-heal. Silence here is how a hole becomes permanent. The emitted instruction must be the literal command to run. |
| SCHEMA_MISMATCH | **`HALT`** | **`HALT`** | The one state where an *optional* input still halts. A unit change (ms→µs) or a silently-unwritten column corrupts **everything downstream, retroactively, without any symptom**. There is no safe degraded computation against data whose meaning has changed. **Never auto-heal a schema mismatch.** |
| CONFIGURATION_ERROR | `HALT` | `HALT` | The gate is not trustworthy; nothing it says can be relied upon. |
| UNKNOWN | **`BLOCK`** | `BLOCK` | Fail closed (L2). A gate that doesn't know must not be a gate that says yes. |

### What is explicitly forbidden

- **Partially computing from a REQUIRED input.** Degradation is only ever available
  along a path the contract *declared in advance*. Ad-hoc "compute with what we
  have" is exactly what wrote a 00:05 tick into `spot_close`.
- **Imputation.** Never fill, ffill, interpolate, or zero-fill a gated input. The
  corpus rule already in force — *"Jan–Mar 2021 has no DVOL → NaN → drop, never
  impute"* (CLAUDE.md §11) — is hereby promoted from a note about DVOL to a
  platform law.
- **Expressing incompleteness as NULL.** A NULL that reaches a derivation is
  indistinguishable from a legitimate missing value. Incompleteness must be a
  **verdict**, not a value. This is F2, and it is the difference between the
  funding query (which behaved correctly) and the derivation (which did not).
- **Warning and proceeding.** `WARN` is not an action in this framework. A warning
  that does not change behaviour is a log line that trains operators to ignore
  logs.

---

## 4. Lifecycle of a gated derivation

```
  ┌──── for each (feature_id, period, as_of) ────────────────────────────────┐
  │                                                                          │
  │  RESOLVE ── contract@version, content-hashed                             │
  │     │                                                                    │
  │  PROBE ──── per input: cheap metadata only (L3)                          │
  │     │       → InputObservation{rows, min_ts, max_ts, schema, coverage,   │
  │     │                          newest_arrival, content_hash}             │
  │  CHECK ──── ordered checks (§2) → [CheckResult]                          │
  │     │                                                                    │
  │  AGGREGATE ─ worst-wins → Verdict{state, per-check evidence, remediation}│
  │     │                                                                    │
  │  RECEIPT ─── persist to data_readiness (append-only) — ALWAYS, even on   │
  │     │        READY, even on DEFER. A day with no receipt is a day        │
  │     │        nobody looked at, and that must be visible.                 │
  │     │                                                                    │
  │  POLICY ──── (state, criticality) → Action                               │
  │     │                                                                    │
  │     ├─ COMPUTE  → derive → write rows stamped (readiness, receipt_id)    │
  │     ├─ DEGRADE  → derive subset → write, outputs marked `unavailable`    │
  │     ├─ DEFER    → write nothing, exit 0                                  │
  │     ├─ RETRY    → backoff, re-probe, re-verdict                          │
  │     ├─ BLOCK    → write nothing, emit remediation, exit non-zero         │
  │     └─ HALT     → stop pipeline + siblings, alert                        │
  └──────────────────────────────────────────────────────────────────────────┘
```

**The receipt is written before the action is taken and regardless of the action.**
A deferred day leaves a receipt saying "deferred, PARTIAL_DAY, here is the
evidence." That is how a readiness board can distinguish *"this day was correctly
skipped"* from *"nothing ran."* Today those two are indistinguishable, which is why
the H6 outage was silent.

---

## 5. Interfaces

Described as contracts, not code.

### 5.1 The Contract (declarative, versioned, hashed)

The contract is the entire configuration surface. One file per derived feature.

```yaml
# contracts/corpus_daily.yaml
contract_id: corpus_daily
version: 3                      # monotonic; bump on ANY semantic change
grain: utc_day                  # period definition — drives the TEMPORAL check
owner: research
produces: [corpus_daily.spot_close, corpus_daily.funding_bps,
           corpus_daily.rv_21d, corpus_daily.atr14, corpus_daily.atr_ratio]

inputs:
  - id: spot_ohlcv
    source: {type: parquet, path: research/data/raw/spot/BTCUSDT}
    criticality: required
    schema:
      columns: {open_time: int64, open: float64, high: float64,
                low: float64, close: float64, volume: float64}
      # magnitude band — catches the Binance ms→µs class (CLAUDE.md §11)
      bands: {open_time: [1.0e12, 1.5e15]}
    coverage:
      rule: minute_bars_per_day
      min_count: 1440           # a full UTC day
      min_fraction: 1.0
    freshness: {max_age_hours: 26}
    late_arrival_hours: 6       # after this, INCOMPLETE_DATA → BACKFILL_REQUIRED

  - id: funding
    source: {type: parquet, path: research/data/raw/funding/BTCUSDT}
    criticality: required
    coverage:
      rule: events_per_day
      min_count: 3              # 00:00 / 08:00 / 16:00 UTC — ALL THREE
      # H6 note: the old corpus query used `HAVING COUNT(*) >= 2`, which admits a
      # day missing an entire funding event. The floor is 3. If a 2-of-3 day is
      # ever to be admitted, that must be a DECLARED degradation, not a silent NULL.
    grid: {period_ms: 28800000, tolerance_ms: 1000}   # ±1s — CLAUDE.md §11
    freshness: {max_age_hours: 26}
    late_arrival_hours: 12

  - id: dvol
    source: {type: parquet, path: research/data/raw/dvol/BTC}
    criticality: optional               # ← the IVRV precedent, declared
    degradation:
      on: [STALE_DATA, INCOMPLETE_DATA, BACKFILL_REQUIRED]
      outputs_unavailable: [dvol_close, ivrv_vrp_spread]
      # NEVER: impute, ffill, or zero-fill. Absent means absent.
    freshness: {max_age_hours: 30}      # refreshed 00:05 UTC, non-critical
    late_arrival_hours: 24

history_window:
  # The derivation reads a trailing window; interior gaps invalidate it.
  # Sized by the longest lookback among `produces` (rv_21d=21, atr_ratio=14+10).
  min_rows: 31
  continuity: no_interior_gaps

policy: default                 # or an explicit per-state override table
```

**Contract versioning is load-bearing.** The receipt records
`(contract_id, version, content_hash)`. A contract change is therefore a
**visible, attributable event** in the lineage of every row derived after it —
exactly as `fn_hash` is for a decision function. Tightening the funding floor from
2 to 3 becomes a governed act, not a silent edit.

### 5.2 Component interfaces

| Component | Contract | Notes |
|---|---|---|
| **`Probe`** | `(source_spec, period, as_of) → InputObservation` | One implementation per source **type** (`parquet`, `sqlite`, `http`), not per feature. Registered by type. Metadata only (L3). Adding a source type is the only reason to write a new probe. |
| **`InputObservation`** | `{rows, min_ts, max_ts, newest_arrival_ts, schema, coverage_count, coverage_denominator, content_hash, probe_error}` | Everything a check may see. Serialisable, and stored in the receipt — so a verdict can be **recomputed from the receipt alone** without re-reading the source (reproducibility). |
| **`Check`** | `(observation, input_spec, as_of) → CheckResult` | **Pure.** No I/O, no clock (L6). One per check family (§2). Directly unit-testable — the whole point. |
| **`CheckResult`** | `{check_id, state, observed, expected, detail}` | Always carries *observed vs expected*. A gate that says "failed" without saying "1 funding event, expected 3" has not helped anyone. |
| **`Verdict`** | `{state, checks[], remediation, contract_id, contract_version, as_of}` | Worst-wins aggregate. `remediation` is a **literal, runnable command** for BACKFILL_REQUIRED. |
| **`Gate.evaluate`** | `(contract_id, period, as_of) → Verdict` | The single entry point. Deterministic and pure given the observations. |
| **`Policy.resolve`** | `(state, criticality) → Action` | §3 matrix. Overridable per contract; the default is the matrix. |
| **`Receipt`** | persisted `Verdict` + observations + `receipt_id` | Append-only. |
| **`@gated` / `with gate(...)`** | Pipeline-facing wrapper | Runs the lifecycle, writes the receipt, and either invokes the derivation or short-circuits. **A derivation body is never entered unless the action is COMPUTE or DEGRADE.** Adoption should be a decorator, so that "forgot to gate" is visible in review as a missing line. |

---

## 6. Failure semantics

| Property | Rule |
|---|---|
| **Determinism** | A verdict is a pure function of `(contract@version, observations, as_of)`. Re-evaluating a stored receipt's observations must reproduce the verdict **bit-for-bit**. Any check reading a clock, a random source, or unpinned config is a defect. |
| **Point-in-time correctness** | `as_of` is mandatory and injected. `Gate.evaluate(..., as_of=2026-06-15T00:05Z)` must return **exactly what it would have returned on that date** — using only data whose *arrival* timestamp precedes `as_of`. This is what makes a backfill honest: a replay must not benefit from data that had not yet arrived. Without arrival-time tracking, backfills silently acquire look-ahead. |
| **Reproducibility** | Receipts are self-contained: `(contract_hash, observations, as_of) → verdict` is replayable with no source access. An audit never depends on the sources still existing in the state they were in. |
| **Idempotency** | Re-running a gated derivation for the same `(feature, period)` where the prior verdict was READY is a **no-op**. Where it was **not** READY, it is a **full retry** — because `_already_processed()` is readiness-aware (L4). This is the structural cure for F3. |
| **No partial writes** | Derivation + row writes + receipt commit in **one transaction**. A crash mid-derivation leaves no row and no receipt — the day is simply un-attempted, which is a state the system already handles correctly (`schema_forward.sql` already documents this invariant for `run_log`; the framework adopts it universally). |
| **No silent success** | Every terminal action emits a receipt. A `(feature, period)` with **no receipt** is itself a reportable anomaly (`NO_RECEIPT`), distinct from any verdict. **A day nobody looked at must not look like a day that passed.** |
| **Escalation** | Repeated BACKFILL_REQUIRED / STALE_DATA on the same `(feature, input)` across N consecutive cycles escalates from log → heartbeat → alert. A single stale day is noise; five in a row is a dead collector. |

---

## 7. Governance integration

This is where the framework stops being a data-quality nicety and becomes part of
the research-integrity perimeter. It plugs directly into the Evidence Library
lane/matrix design (`docs/governance/evidence_reporting_architecture.md`).

**G1 — Readiness is an admissibility precondition.**
An Evidence Library run is **admissible** only if **every input day within its
evidence window** carries a `READY` (or explicitly-declared `DEGRADED`) receipt.
A run touching a `PARTIAL_DAY`, `INCOMPLETE_DATA`, or `BACKFILL_REQUIRED` day is
**INADMISSIBLE**: it may exist in the ledger (deleting it would destroy lineage),
but it **may not be a `pinned_run`**, and it must render on the governance board
with an `INADMISSIBLE` flag.

**G2 — The Validated gate gains a readiness precondition.**
`check_validated_preconditions()` gains one more requirement alongside DSR and
BH-FDR: **the pinned run's evidence window must be 100% READY.** A signal cannot
be Validated on evidence derived from data we know was incomplete. Rationale is
identical to the existing NULL-DSR hard-fail: there is no silent bypass.

**G3 — Readiness receipts are part of run provenance.**
`runs.metrics_json` gains a `readiness` block: `{days_total, days_ready,
days_degraded, days_non_ready[], contract_versions{}}`. A reviewer can then see, at
the moment of promotion, exactly what the evidence was made of.

**G4 — A contract version bump is a governed act.**
Because a contract change alters what "complete" *means*, tightening or loosening a
floor **after** evidence has accumulated changes the admissibility of history. The
receipt records the contract version used at derivation time, so history is never
retroactively re-judged by a new contract — **but** a version bump must be recorded
and surfaced, exactly as a threshold change would be.

**G5 — Testnet remains inadmissible regardless of readiness.**
A `READY` verdict means *complete*, **not** *admissible as research evidence*. Delta
testnet data can be perfectly complete and still be economically synthetic
(CLAUDE.md §11). Readiness and admissibility are **independent gates, both
required**. The gate must never be mistaken for a promotion of testnet data.

---

## 8. Logging

Follows the precedent already established by `SnapshotWriter` — *one aggregated
warning per cycle, never per row* (CLAUDE.md §11), because a systematic failure
must be **loud without flooding**.

| Level | Emitted for |
|---|---|
| `INFO` | READY (one line per feature-period); **DEFER** (routine — a 00:05 partial-day deferral is the system working) |
| `WARNING` | DEGRADE; STALE_DATA; INCOMPLETE_DATA within budget |
| `ERROR` | BLOCK; BACKFILL_REQUIRED; UNKNOWN |
| `CRITICAL` | HALT; SCHEMA_MISMATCH; CONFIGURATION_ERROR |

Every line is structured and carries `{contract_id, contract_version, feature_id,
period, as_of, state, failing_check, observed, expected, receipt_id}`. **Never log
per input row** — aggregate to one line per `(feature, period)`.

`remediation` for `BACKFILL_REQUIRED` must be a **literal runnable command**:

```
ERROR [gate] corpus_daily/2026-06-14 BACKFILL_REQUIRED
  check=coverage input=funding observed=1 expected=3 late_budget_expired=6h
  remediation: python -m ingest.binance_funding --start 2026-06 --end 2026-06
  receipt=9f3c1a…
```

An error that tells an operator *what* is wrong but not *what to type* will be
triaged at 3am by someone who did not write it.

---

## 9. Provenance

Every derived row is traceable to the exact input state that produced it.

```sql
CREATE TABLE data_readiness (            -- append-only; never UPDATE, never DELETE
    receipt_id        TEXT PRIMARY KEY,  -- sha256(contract_hash|feature|period|as_of|observations)
    contract_id       TEXT NOT NULL,
    contract_version  INTEGER NOT NULL,
    contract_hash     TEXT NOT NULL,
    feature_id        TEXT NOT NULL,
    period            TEXT NOT NULL,     -- 'YYYY-MM-DD' for utc_day grain
    as_of             TEXT NOT NULL,     -- injected clock (L6)
    state             TEXT NOT NULL,     -- the 8 readiness states
    action            TEXT NOT NULL,     -- COMPUTE | DEGRADE | DEFER | BLOCK | HALT
    checks_json       TEXT NOT NULL,     -- [CheckResult] — observed vs expected
    observations_json TEXT NOT NULL,     -- [InputObservation] — replay substrate
    remediation       TEXT,
    created_at        TEXT NOT NULL
);
CREATE INDEX ix_readiness_feature_period ON data_readiness(feature_id, period);
```

`receipt_id` is **content-addressed** — a deterministic hash of everything the
verdict depended on. Two identical evaluations produce the same `receipt_id`,
which is the reproducibility guarantee made mechanical rather than aspirational.

Derived tables (`corpus_daily`, `signal_log`, feature tables, dataset manifests)
each gain **two columns**: `readiness TEXT NOT NULL` and `receipt_id TEXT NOT NULL`.
Given any derived row, an auditor can recover: the contract that governed it, the
version of that contract, every input observation, the verdict, and the exact
`as_of` at which the verdict was rendered.

**The `input_fingerprint`** (part of `InputObservation`) is the content hash of the
input partitions consumed. If a Parquet partition is ever silently rewritten
upstream, the fingerprint changes, and the mismatch is detectable against every
receipt that ever consumed it. **Silent upstream mutation becomes discoverable
rather than invisible.**

---

## 10. Reporting

**`readiness_board.md`** — a feature × day matrix, the operational twin of the
governance Evidence Matrix:

```
# Data Readiness Board — as-of 2026-07-13

## Coverage (last 14 days)
                    07-01 .. 07-12  |  READY  DEGRADED  NON-READY  NO-RECEIPT
corpus_daily        ████████████    |   12/12      0         0          0
h5_vol_regime       ████████████    |   12/12      0         0          0
ivrv_vrp_spread     ██████░░████    |   10/12      2         0          0   ← DVOL stale 07-07/08 (declared degradation)
option_quotes       ████████████    |   12/12      0         0          0

## Open remediations
| Feature      | Period     | State             | Failing check    | Remediation                    |
|--------------|------------|-------------------|------------------|--------------------------------|
| corpus_daily | 2026-06-14 | BACKFILL_REQUIRED | coverage/funding | python -m ingest.binance_fun…  |

## Contract versions in force
| Contract     | Version | Hash     | Since      |
|--------------|---------|----------|------------|
| corpus_daily | 3       | 9f3c1a…  | 2026-07-13 |
```

Plus: a **readiness SLO** per feature (`% of days READY over trailing 30d`) — the
metric that makes a slowly-decaying collector visible **before** it costs a month
of evidence; a `NO_RECEIPT` count (days nobody evaluated — the H6 blind spot made
into a number); and an `INADMISSIBLE` count feeding the governance board.

The `FWD-STALE` / `GATE-BLOCKED` heartbeat flags specified in the Evidence
Reporting architecture are fed **from this board**, closing the loop between data
health and governance.

---

## 11. Future-proofing

| Consumer | How it uses the gate — with **no framework change** |
|---|---|
| **Volatility / RV / ATR** | Contracts declaring `history_window.min_rows` = the lookback. `rv_21d` needs 21 clean rows; the gate refuses a 20-row window instead of returning a quietly-wrong number. |
| **Funding** | The `events_per_day: 3` floor, with the ±1s grid tolerance (CLAUDE.md §11) as a declared `grid` rule rather than a magic number in a query. |
| **Greeks / IV surface** | Coverage rules over the **option universe**, not over time: "≥ 90% of the ATM±5 / 3-expiry universe has a non-NULL `mark_iv` at sample time." A surface fitted to a half-populated chain is exactly F2 in a new costume. |
| **Liquidity** | Freshness + coverage over top-of-book snapshots; the `exch_ts` band check (CLAUDE.md §11) becomes a declared `bands` schema rule rather than bespoke code. |
| **Feature Store** | A feature's completeness contract **is** its registry entry. Point-in-time correctness (`as_of`) is the property a feature store lives or dies by, and here it is a first-class argument rather than a convention. |
| **Dataset Engineering** | A dataset build is *just another gated derivation* whose inputs are features. Its receipt references its input features' receipts, producing a **complete recursive lineage** from dataset → feature → raw partition. |
| **ML / DL / RL** | A training set materialisation records the **full receipt set** of every feature-day it consumed. A model's training data becomes point-in-time reconstructible and audit-replayable — which is the entire precondition for the ML-readiness gate the Evidence Library is building toward. A model trained on a `PARTIAL_DAY` is a model trained on look-ahead. |
| **ResearchOps** | Readiness SLOs, open-remediation queue, contract-version drift — the operational surface of research data quality, in one board. |

The framework scales because of **L1 (declare, don't code)**: every one of these
adds a **contract**, not a code path. The only reason to touch the engine again is
a genuinely new *source type* (a new `Probe`) or a genuinely new *check family* —
and both are additive.

---

## 12. Implementation specification for Sonnet

**Do not modify:** any decision function; `fn_hash`-locked code; `thresholds.yaml`;
`lifecycle.ALLOWED_TRANSITIONS`; `check_candidate_gate()`; any signal or engine
logic. **No research logic changes.** The gate never changes *what* is computed —
only *whether it is permitted to be computed at all*.

Land in **six sequential PRs**, each independently revertible and independently
useful. Do not proceed until the previous is green.

---

### PR-1 — Core engine (no adopters)

New package `research/dq/` — deliberately standalone, importable by both the venv
bot and the conda research env, **no pandas dependency in the core**.

1. `states.py` — the 8 states, severity order, worst-wins aggregation.
2. `contract.py` — YAML load, schema-validate, **version + content-hash**, resolve.
   A malformed contract raises `CONFIGURATION_ERROR`, never a bare exception.
3. `observation.py` — `InputObservation`, fully serialisable both ways.
4. `checks.py` — the six check families (§2), **pure**, clock injected. Each returns
   `CheckResult{check_id, state, observed, expected, detail}`.
5. `probes/` — `parquet.py`, `sqlite.py`. Registered **by source type**. Metadata
   only: row counts, min/max ts, arrival ts, schema, content hash. **A probe that
   loads a full dataframe is a failed review.**
6. `policy.py` — the §3 matrix, with per-contract override.
7. `gate.py` — `evaluate(contract_id, period, as_of) → Verdict`; `Verdict.action`.
8. `receipt.py` — content-addressed `receipt_id`; `data_readiness` DDL (§9);
   append-only writer; **`replay(receipt) → Verdict`** (must reproduce bit-for-bit
   from stored observations, with **no source access**).

**Tests:** every state producible in isolation; the check-ordering invariant
(a 0-row partition caused by a µs/ms unit switch yields `SCHEMA_MISMATCH`,
**not** `INCOMPLETE_DATA` — the §2 masquerade); worst-wins across multiple inputs;
`UNKNOWN` on probe exception (**fail-closed**); replay determinism; content-address
stability across processes.

**Gate:** 100% branch coverage on `checks.py` and `policy.py`. These two files are
the entire safety argument; nothing else in the framework matters if they are wrong.

---

### PR-2 — The H6 pipeline adopts it (the proof)

The first adopter must be the pipeline that motivated the framework — if the gate
cannot express H6's contract, the design is wrong and we find out now, cheaply.

1. Write `contracts/corpus_daily.yaml` (§5.1). The funding floor is **3**, not 2.
2. `daily_job.py` — gate at the top of `process_day()`. Replace `last_complete_day_index()`
   with the TEMPORAL check. **Keep the function as a thin delegate initially** so
   the PR is a pure refactor at the behavioural level.
3. **Make `_already_processed()` readiness-aware** — `WHERE day = ? AND readiness = 'READY'`
   (L4). **This is the most important line in the entire body of work.**
4. Add `readiness` + `receipt_id` columns to `corpus_daily`, `regime_states`,
   `signal_log`.
5. Backfill existing rows' `readiness` by **re-evaluating the gate at each row's
   original `ingested_at` as `as_of`** — which is the point-in-time-correct value,
   and which will correctly mark the known-corrupt H6 rows as non-READY, *independently
   confirming* what `repair_h6_partial_days.py` found by hand.

**Tests:** the exact H6 scenario — a corpus whose last row is today at 00:05 with
1 of 3 funding events — yields `PARTIAL_DAY` → `DEFER` → **no row written**;
the next day's run writes it correctly; **a golden test that the day is revisited**
(the F3 latch is dead). Replay the real production `corpus_daily` history through
the gate and assert the corrupt days are flagged.

**Gate:** the framework independently rediscovers the H6 corruption from production
data, with no hand-coded knowledge of it. If it does not, the contract is wrong.

---

### PR-3 — Forward-evidence collectors adopt it

`runner_base.run_signal()` — replace the ad-hoc `DataUnavailableError` raises with
gate verdicts. `DataUnavailableError` becomes an *internal* signal of the probe
layer, not the pipeline's error vocabulary.

- Extend `run_log.status` CHECK to include the gate actions
  (`'deferred'`, `'blocked'`, `'degraded'`) alongside `ok | skipped | error |
  fn_hash_violation`. **Preserve the existing idempotency semantics exactly**:
  `deferred` must be **retryable without operator action** (unlike `error`, which
  requires deleting the row) — that distinction is currently absent and is the same
  latch as F3 wearing a different hat.
- The IVRV DVOL degradation becomes a **declared** `degradation` block rather than
  a special case in `run_signal`, and H5/G1/G3 remain structurally unblockable by a
  DVOL outage.

**Tests:** all four signals; DVOL-missing yields `DEGRADE` for IVRV and `READY` for
the other three; a deferred day is retried on the next cycle without intervention.

---

### PR-4 — Governance integration

1. `runs.metrics_json` gains the `readiness` block (G3).
2. `check_validated_preconditions()` gains the 100%-READY evidence-window
   requirement (G2) — same hard-fail, no-silent-bypass semantics as the DSR/BH-FDR
   NULL check.
3. `ledger_sync` computes the readiness summary for each run's window and **refuses
   to write a run whose window contains a non-READY, non-declared-degraded day** —
   or writes it flagged `INADMISSIBLE` (G1). Recommend the latter: refusing to write
   loses the fact that the day existed, and lineage loss is the one thing the
   Evidence Library exists to prevent.
4. `report.py --check` gains: every derived row has a receipt; every receipt's
   contract version exists; no `pinned_run` has a non-READY window.

---

### PR-5 — Observability

1. `readiness_board.md` generator (§10), with `--as-of`.
2. Structured logging (§8), including the **runnable remediation string**.
3. Heartbeat integration: `NO_RECEIPT`, `BACKFILL_REQUIRED`, and N-consecutive
   `STALE_DATA` escalation. Feeds the `FWD-STALE` / `GATE-BLOCKED` flags already
   specified in the Evidence Reporting architecture.

---

### PR-6 — Platform rollout

Contracts (no engine changes — this PR is the proof of L1):
`option_quotes` (coverage/staleness rules already exist informally in
`collection_metrics` — **the gate should *consume* `coverage_pct` and `stale_count`
rather than recompute them**; CLAUDE.md §11 already defines their semantics
precisely), `oi_snapshots`, `funding_snapshots` (**currently dormant/empty — the gate
must report it as such rather than silently reading zero rows**, which is exactly
the F2 blind spot), and each existing derived feature.

**Gate for the whole programme:** a new derived feature can be added to the platform
by writing **one contract file and one decorator line**, with **zero** changes to
`research/dq/`. If that is not true, L1 has been violated somewhere and the
framework has quietly become another H6-shaped `if`.

---

## 13. Acceptance criteria

1. A derivation **cannot** run against inputs that have not been gated — the
   decorator is the only entry point, and its absence is visible in review.
2. A non-READY period **cannot** produce a durable row that a later run treats as
   done. **F3 is structurally dead**, platform-wide.
3. Schema drift (ms→µs, declared-but-unwritten column) produces `SCHEMA_MISMATCH` →
   `HALT`, and **never** masquerades as missing data.
4. Every derived row names the receipt that authorised it; every receipt replays to
   a bit-identical verdict without touching the sources.
5. `--as-of` reproduces any past verdict exactly, with **no** look-ahead from
   later-arriving data.
6. A new feature = one contract file + one decorator. No engine change.
7. Governance cannot pin a run to evidence derived from data we knew was incomplete.

Criterion 2 is the one this framework exists to guarantee. Everything else is in
service of it.
