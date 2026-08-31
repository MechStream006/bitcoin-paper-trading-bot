# FB-12b — h6 Completeness Producer: Activation Audit

**Date: 2026-08-31 (UTC) · Gate FB-12b · Read-only · Verdict: DO NOT ACTIVATE.
The producer emits a correct receipt that the consumer cannot find, and even if
it could, no existing or future L2 run can satisfy the readiness-receipt path.
Two defects found, both in work committed at FB-12a.**

Nothing activated, no receipt emitted on any real ledger, no scheduler wiring.
`readiness_gate`, `transition_governance`, collectors, h6, registry, thresholds,
ledger and pre-registrations untouched. Production was read, never executed
against.

---

## 1. Exact receipt / data flow

```
h6_completeness.evaluate_current(db, period, as_of, actor)
  |
  +-- refuses period older than {as_of_date, as_of_date - 1}   -> writes NOTHING
  |
  +-- dq.readiness.init_db(db)          creates data_readiness + contract_registry
  +-- dq.readiness.evaluate_and_record(...)
        |
        +-- contract_loader.resolve_for_derivation(db, CONTRACT_ID)   READ-ONLY
        +-- probes.probe_input(...)  -> parquet_probe -> InputObservation
        +-- checks.check_continuity  -> READY | BACKFILL_REQUIRED | UNKNOWN ...
        +-- receipt: decision_type = "readiness.verdict"  (derived, PR-1 taxonomy)
        |     appended to governance_receipts, hash-chained on prev_receipt_hash
        +-- projection row in data_readiness, keyed by receipt_id
```

Verified empirically on a throwaway database:

| observed | value |
|---|---|
| `decision_type` | **`readiness.verdict`** |
| chain field | `prev_receipt_hash` populated (genesis on a fresh DB) |
| tables created by evaluation | `governance_receipts`, `data_readiness`, **`contract_registry`** |
| `contract_registry` rows written by evaluation | **0** |
| projection row on a READY verdict | 1 (`state=READY`, `action=COMPUTE`) |
| projection row on CONFIGURATION_ERROR | **0** — receipt only |

**Receipt type**: PR-7's `readiness.verdict`, reserved as `derived` in
`receipts.DECISION_TAXONOMY` since PR-1. No new receipt type, no new table, no
second ledger — `data_readiness` is a projection keyed by the receipt's own id.

---

## 2. Correction to an FB-12a claim

FB-12a's commit message and module comment state that evaluation *"never
activates or mutates the contract registry."* That is true of **rows** and false
of **schema**: `init_db` creates an empty `contract_registry` table as a
side-effect of evaluation. Zero rows, no activation, no lifecycle transition —
but the table appears.

It matters because the **production ledger currently has no such table**
(verified: `gate_results`, `governance_receipts`, `pairwise_r`, `runs`,
`sqlite_sequence`, `state_transitions`). A single evaluation against production
would add two tables to the governed store.

---

## 3. Activation state and its absence

| condition | behaviour | verified |
|---|---|---|
| Contract **active** in `contract_registry` | verdict rendered from probe + checks | yes — `READY`, `may_derive=True` on a complete 273-day lake |
| Contract **not activated** | **`CONFIGURATION_ERROR`**, `may_derive=False`, receipt still written, **no projection row** | yes |
| Contract activation | separate operator act via PR-3 `contract_lifecycle.activate_contract`; `h6_completeness.activate()` is the only path and is never called by `evaluate_current()` | yes |

Fail-closed is correct: *"a gate that dies with a traceback writes no receipt,
and a period with no receipt must never be indistinguishable from a period that
passed."* Note the consequence for §5, though — a CONFIGURATION_ERROR writes no
projection row, so to `get_readiness()` it is indistinguishable from never
having been evaluated.

---

## 4. Period boundary

| period | outcome |
|---|---|
| current UTC day (`as_of` date) | accepted |
| last complete UTC day (`as_of` date − 1) | accepted — the framing `run_daily._derive_run_date()` uses |
| anything older | **`HistoricalPeriodRefused`**, raised **before** any import, DB open or file read |

**Older dates are structurally impossible, not merely discouraged.** The guard
precedes every side-effecting call; a refused period leaves no database file at
all (asserted by test). The 70 historical h6 days cannot be re-verdicted through
this interface.

---

## 5. THE BLOCKING DEFECT — the verdict is emitted where the consumer never looks

`readiness_gate._from_readiness_receipts()` resolves:

```python
feature_id = run["family_id"]          # "h6_funding_vol_regime"
row = dq_readiness.get_readiness(db_path, feature_id, day, as_of=run["run_ts"])
```

but the projection is written with `feature_id = "h6_funding_vol_regime.completeness"`.

Root cause, `engine.py:186`:

```python
feature = feature_id or contract.contract_id
```

The engine accepts an optional `feature_id` and **falls back to `contract_id`**.
`readiness.evaluate_and_record()` exposes no `feature_id` parameter, so the
fallback always wins. My contract's `produces: ["h6_funding_vol_regime"]` field
is never consulted.

Demonstrated end-to-end: a genuine `READY` verdict was written, and
`get_readiness(db, "h6_funding_vol_regime", "2026-08-30", as_of=...)` returned
**`None`**.

**So today the emitted result is provenance ONLY. `DefaultReadinessProvider`
will not consume it, and TG-RDY will read `NO_RECEIPT`.**

The fix needs **no framework change**: renaming `CONTRACT_ID` to
`"h6_funding_vol_regime"` makes the fallback produce the right key. Passing an
explicit `feature_id` would be cleaner but requires editing
`dq/readiness.py` — a PR-7 core module this gate forbids touching.

---

## 6. THE STRUCTURAL FINDING — no L2 run can ever satisfy the receipt path

Worse than the defect above, and unaffected by fixing it.

`_from_readiness_receipts` requires a `READY` receipt for **every UTC day** in a
run's `window_start..window_end`, each read **as of that run's own `run_ts`**.
Production's ten L2 runs:

| family | window | days |
|---|---|---|
| `g1_market_regime_sma200` | 2026-06-15..2026-08-30 | 77 |
| `g3_momentum_tsmom` | 2026-06-15..2026-08-30 | 77 |
| `h5_vol_regime` | 2026-06-15..2026-08-30 | 77 |
| `ivrv_vrp_spread` | 2026-06-19..2026-08-30 | 73 |
| `g3_atr_expansion` | 2026-06-28..2026-08-30 | 64 |
| *(and their 2026-08-30 counterparts)* | | |
| **total day-receipts required** | | **731** |

Every one of those days is in the past, and every `run_ts` has elapsed. A
receipt written now carries a later `as_of` and is therefore invisible to those
runs — deliberately, since that is exactly the look-ahead the design prevents.
Retroactive receipts are separately forbidden (FB-11; PR-7 L4).

**Future runs do not escape it.** `ledger_sync._first_run_date()` sets
`window_start = MIN(run_date)` — *"the true start of the forward observation
window, which begins when collection begins, not when the first trade happens to
open."* So every future L2 run for these families still spans from 2026-06-15,
across days no readiness receipt can ever cover.

**Consequence: for all five collecting families, the readiness-receipt path is
permanently unsatisfiable.** The only remaining route is
`_from_run_block` — a `readiness` block inside `runs.metrics_json`, which
`DefaultReadinessProvider` checks *first* — and populating that honestly would
still have to declare the pre-activation days non-READY unless governance
decides that readiness begins when readiness collection begins.

**That decision is not mine to make and is not implied by anything frozen.** It
is the real blocker between the platform and a first Validated family.

---

## 7. Isolation — what the producer cannot touch

| surface | can the producer affect it? | basis |
|---|---|---|
| `contract_registry` rows | **No** — only `activate()` writes, and evaluation never calls it | verified: 0 rows after evaluation |
| `contract_registry` table | creates it empty (§2) | verified |
| `signal_log`, `run_log`, `regime_states`, h6 observations | **No** — different databases entirely; the module never opens the forward corpora for writing | it takes only a `lake_root` (read) and a `db_path` (readiness) |
| `runs`, `state_transitions`, `gate_results`, `pairwise_r` | **No** — asserted by test that none of these tables is created | FB-12a test |
| `governance_receipts` | **appends** one `readiness.verdict` per evaluation, including on failure | verified |
| Scheduler / systemd | **No** — asserted by test that no unit or collector references the module | FB-12a test |
| h6 strategy output | **No** — `h6_decide` byte-identical across all 343 regime triples | FB-12a test |

---

## 8. Would activating h6 completeness affect promotion of an h6 L2 run?

**No, on three independent grounds:**

1. **h6 has no L2 run to promote.** Production shows `h6_funding_vol_regime`
   with **2 `offline-corpus` runs and 0 `live-forward`**. h6 has never traded, so
   `ledger_sync`/`forward_snapshot` skip it by design.
2. **The verdict would not be found** (§5).
3. **Even fixed and even with an L2 run**, one day's receipt cannot satisfy a
   multi-day window (§6).

---

## 9. Generality

| component | h6-specific? |
|---|---|
| `parquet_probe` | **No** — fully generic, dataset/symbol/ts declared in `source` |
| `_dq()` loader, historical-period refusal, activation separation, fail-closed behaviour | **No** — generic |
| `build_contract_doc()` and `CONTRACT_ID` / `FEATURE_ID` / `MIN_DAILY_FUND` | **Yes** — the only h6-specific surface |

The interface is already ~90% generic; generalisation means parameterising one
function and three constants.

**And the seven `run_daily` collectors are where the value is.** They own all ten
L2 runs; h6 owns none. Activating h6-only would add two tables and a growing
receipt chain to the production governance ledger **for the one family that
cannot benefit**.

---

## 10. Closing answers

**Exact receipt/data flow** — §1. `readiness.verdict`, `derived`, hash-chained
in `governance_receipts`, projected into `data_readiness` keyed by `receipt_id`.

**Is the producer currently operationally useful?** **No.** It emits a correct
verdict under a key the consumer never queries (§5), and the consumer's path is
unsatisfiable for every run that exists or will exist (§6). Its present value is
provenance and nothing more.

**Is activation safe?** Technically yes — additive, fail-closed, no strategy or
collector effect, structurally unable to reach historical days. But it is
**not warranted**: it would add `contract_registry` and `data_readiness` to the
production governance ledger and begin extending the hash chain daily, in
exchange for no consumable signal.

**Smallest activation gate — FB-12c, in this order:**

1. Fix the feature-id mapping so the verdict is discoverable (rename
   `CONTRACT_ID`; no framework change), with a test that asserts
   `get_readiness(db, family_id, day)` finds it.
2. Generalise `build_contract_doc()` to any forward family, since the seven
   `run_daily` collectors are the ones with L2 runs.
3. **Resolve the §6 governance question first** — whether readiness may begin
   when readiness collection begins, or whether `window_start` semantics change.
   Without it, daily receipts still satisfy no run, and activation buys nothing.
4. Only then activate and schedule daily; a once-off invocation is worthless,
   because the gate needs continuity, not a sample.

**Should it be generalised before activation?** **Yes.** Activating h6 alone
inverts the value: the family with no L2 runs gains a governance surface, while
the five families with L2 runs gain nothing.

---

## 11. Trail

| Gate | Outcome |
|---|---|
| FB-12 | PR-7 reusable; adoption at provenance; readiness producer identified as the prize |
| FB-12a | `parquet_probe` + `h6_completeness.py` + 35 tests, CI green (`34e6ce3`) |
| **FB-12b** | **This audit. DO NOT ACTIVATE — feature-id mismatch, and the receipt path is unsatisfiable for every run** |
| FB-12c | Proposed: fix mapping, generalise, resolve the readiness-window question, then activate |
