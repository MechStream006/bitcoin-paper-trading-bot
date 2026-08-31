# FB-12h — Forward Readiness Deployment & First-Run Audit

**Date: 2026-08-31 (UTC) · Gate FB-12h · Read-only · Verdict: deployment is
runtime-inert and safe; the first snapshot would be the first run on this
platform to SATISFY TG-RDY. One non-obvious first-run interaction found: an
operator snapshot can pre-empt the nightly collector's ledger row for the same
date. Governance approval is required before execution.**

Nothing deployed, activated, executed or written. No code, config or schema
changed. Production read-only throughout.

---

## A. Deployment delta

**Production `4cf0923` → `origin/main` `83d6756`: 22 commits.** Non-doc files:

| file | status | reachable from a scheduled path? |
|---|---|---|
| `research/forward_evidence/observation_floors.py` | new (FB-7) | **No** |
| `research/forward_evidence/forward_admissibility.py` | new (FB-8) | **No** |
| `research/forward_evidence/forward_snapshot.py` | new (FB-9, modified FB-12g) | **No** |
| `research/forward_evidence/h6_completeness.py` | new (FB-12a) | **No** |
| `research/forward_evidence/forward_readiness.py` | new (FB-12e/g) | **No** |
| `research/dq/probes.py` | +191 additive (FB-12a) | **No** |
| `research/evidence_library/transition_governance.py` | docstring only (FB-9d) | imported, but **AST-identical with docstrings stripped** |
| `CLAUDE.md`, `docs/**`, `tests/**` | documentation and tests | n/a |

**`run_daily.py`, `runner_base.py`, `daily_job.py`, `ledger_sync.py`,
`run_date_policy.py/.yaml`, `decision_function*.py`, `registry.yaml`,
`thresholds.yaml` are all UNCHANGED.** The nightly collector would execute
byte-identical code after deployment.

Verified: no collector imports any of the five new modules, and `probes.py` is
unreachable from the nightly path. **No `src/` file changes**, so the two
long-running services (`btcbot.service`, `btcbot-api.service`) are untouched.

---

## B. Deployment safety

| question | answer |
|---|---|
| Migrations / schema changes? | **None.** No module in the delta creates a table on import; `forward_readiness` and `h6_completeness` touch schema only when *called* |
| Protected artifacts that must stay unchanged | `ledger.db`, `evidence_forward.db`, `forward_evidence.db`, `registry.yaml`, `thresholds.yaml`, `run_date_policy.yaml`, all pre-registrations |
| Service restart required? | **No.** Every affected unit is `Type=oneshot` on a timer and picks up new files on its next invocation. The two running services read no changed file |
| Rollback | **Clean.** `git checkout 4cf0923` in `/opt/btcbot` restores the prior tree exactly; nothing persistent is created by deployment alone |
| Runtime impact on scheduled collectors | **None** — the executed code is identical |

**Deployment alone has no unexpected runtime consequence.** The interaction
worth knowing about belongs to the first *run*, not the deploy (§C.1).

---

## C. First-run prerequisites

**Schema preconditions verified against production and both PASS:**

```
assert_ledger_schema : runs=True   missing_cols=[]
assert_forward_schema: missing=[]
```

| prerequisite | status |
|---|---|
| Config files | `run_date_policy.yaml` (present, unchanged), `registry.yaml`, the hash-pinned pre-registrations — all already on the host |
| Contract activation | **Not required.** The run-block path needs no contract, no receipt, no `contract_registry` |
| Family IDs | the five with closed trades: `h5_vol_regime`, `g1_market_regime_sma200`, `g3_momentum_tsmom`, `ivrv_vrp_spread`, `g3_atr_expansion`. `g1_trend_regime_ma_cross` and `g2_funding_level` have **0 closed trades** → `no-evidence`, no row |
| Paths | forward `/opt/btcbot/research/data/evidence_forward.db`; ledger `/opt/btcbot/research/evidence_library/ledger.db` |
| Actor | required, non-empty; `actor_type` must **not** be `service`/`cron`/`system` — a snapshot is an operator act |
| Contemporaneous evidence | **Sufficient** — see the payload table below |

### `--as-of` semantics, and the interaction (C.1)

`as_of` is both the trade cut-off (`exit_date <= as_of`) and the row's
`window_end`, and the idempotency key is
**`(family_id, provenance='live-forward', window_end)`**.

All ten existing L2 rows are `forward-collect-v1` (the nightly `ledger_sync`)
with `window_end` **2026-08-29** and **2026-08-30**. Therefore:

- `--as-of 2026-08-29` or `2026-08-30` → **`skipped`**, nothing written.
- `--as-of 2026-08-31` (or later) → **writes**.

**The non-obvious consequence:** the nightly job at 01:00 also writes
`window_end = <the run_date it processed>`. An operator snapshot for a date the
nightly has not yet reached **claims that key first**, and the nightly's
`_run_ledger_sync` then silently skips — so that date's row would carry
`forward-snapshot-v1` **with** a readiness and admissibility payload instead of
`forward-collect-v1`. That is a strictly richer row, not a loss, but it changes
which producer owns the date and the nightly's skip is silent. **An operator
must choose the `as_of` deliberately, not incidentally.**

### Expected readiness payload (production, `as_of = 2026-08-30`)

| family | total | ready | excluded | non-ready | TG-RDY |
|---|---|---|---|---|---|
| `h5_vol_regime` | 77 | 58 | **19** | **0** | **PASS** |
| `g1_market_regime_sma200` | 77 | 58 | **19** | **0** | **PASS** |
| `g3_momentum_tsmom` | 77 | 58 | **19** | **0** | **PASS** |
| `ivrv_vrp_spread` | 73 | 54 | **19** | **0** | **PASS** |
| `g3_atr_expansion` | 64 | 45 | **19** | **0** | **PASS** |

Every excluded day is INC-0005; **no family has a single undeclared gap**.

---

## D. INC-0005 — three proofs

1. **The gap comes from the frozen policy.** `forward_readiness.declared_gap_days()`
   calls `run_date_policy.load_policy()` and expands `immutable_gaps`. That file
   is unchanged in the delta and already on the host; `load_policy` raises rather
   than returning a permissive default if it is missing or malformed.
2. **The 19 days cannot become READY.** They have no `ok` `run_log` row and never
   will (§20 permits no backfill), so `collected_days()` cannot contain them. They
   are placed in `days_excluded`, never counted in `days_ready`
   (`days_ready + len(days_excluded) == days_total`, test-asserted), and
   `evaluate_day()` refuses them outright.
3. **An undeclared missing day fails closed.** It is absent from `collected_days`
   and from `declared_gap_days`, so it lands in `days_non_ready`, and
   `_from_run_block`'s `ok = not days_non_ready` returns `False`. A payload that
   *claimed* such an exclusion is rejected by `assert_exclusions_declared`
   **before** `append_run` — the ledger stays empty.

---

## E. L2 interaction and expected production mutations

A first snapshot at a fresh `as_of` would, **per family, append exactly one row**:

| surface | change |
|---|---|
| `runs` | **+1 row per family** (up to 5), `provenance='live-forward'`, `run_kind='forward'`, `engine_version='forward-snapshot-v1'` |
| existing 10 L2 rows | **untouched** — append-only; a new `(family, provenance, window_end)` |
| `metrics_json` | `forward_admissibility` payload **intact and unchanged**, plus the new `readiness` block |
| `dsr_prob` / `bh_p` | **NULL** (`governance=None`) |
| `state_transitions`, `gate_results`, `governance_receipts`, `pairwise_r` | **no change** |
| `contract_registry`, `data_readiness` | **not created** — the run-block path never touches them |
| `registry.yaml` | **no change** — no admission, pinning, promotion or transition |
| `verify_chain` | **`ok=True, n_receipts=27`** — unchanged, since no receipt is written |

---

## F. TG-RDY outcome — it would PASS, and what that does and does not mean

Trace: `validate_transition` → (only when `to_state == "validated"`) →
`DefaultReadinessProvider.assess(db, run)` → `run["metrics"]["readiness"]` is
present → `_from_run_block` → `ok = not days_non_ready` → `days_non_ready == []`
→ **`ok=True, source="run_block"`**.

**This would be the first time any run on this platform satisfies TG-RDY.**

**It does not make any family promotable.** Entering `validated` also requires,
independently: non-offline provenance (satisfied), `dsr_prob >= 0.50` (**NULL —
hard failure under R1**), `bh_p <= 0.10` (**NULL — hard failure**), and pairwise
independence (vacuous at 0 Validated peers, but FB-10c-G left `min_pairwise_n`
unresolved). Readiness ceases to be the blocker; DSR/BH become the blocker.

That is the correct outcome and worth stating plainly so a passing readiness
assessment is never mistaken for promotion readiness.

---

## G. Rollback and recovery

**Code rollback and ledger rollback are different things and only one is
possible.**

| layer | reversible? | how |
|---|---|---|
| **Code** | **Yes, cleanly** | `git checkout 4cf0923` in `/opt/btcbot`; no schema or data to unwind |
| **Ledger rows** | **No** | `runs` is append-only. A snapshot row, once written, stays |
| **Receipts** | n/a | none written on this path |

**No deletion or rewriting of evidence is proposed, and none should be.** If a
snapshot is written with an unintended `as_of`, the correct remedy is to leave
it and record why — the same discipline FB-11 applied to h6's 70 days. The row
is honest evidence of what was computed; removing it would be the defect.

The practical control is therefore **before** execution: choose `as_of`
deliberately (§C.1), and dry-run first.

---

## H. Scheduler

**Not required for readiness.** The block is computed from `run_log` at snapshot
time; no daily producer run is needed for a family's readiness to be assessable.

A daily producer would only feed the **receipt** path, which §F shows is not the
path in use and which FB-12f proved cannot clear a gap-spanning window anyway.
It becomes useful only for windows that begin after INC-0005 — none exist, since
`window_start = MIN(run_date)`.

**Left as a separate future gate. `run_daily.py` and the scheduler are
untouched.**

---

## I. Exact command sequence for a future controlled run

Not to be executed under this gate.

```bash
# 1. Deploy (as btcbot, in /opt/btcbot)
git fetch origin && git checkout 83d6756

# 2. Verify inert: collectors unchanged, chain intact
git diff --stat 4cf0923..83d6756 -- research/forward_evidence/run_daily.py \
    research/forward_evidence/ledger_sync.py research/forward_evidence/runner_base.py
python -c "import sys;sys.path.insert(0,'research/evidence_library');\
import receipt_ledger;print(receipt_ledger.verify_chain('research/evidence_library/ledger.db'))"

# 3. DRY RUN first -- writes nothing, prints the payload
python research/forward_evidence/forward_snapshot.py \
    --family h5_vol_regime --as-of <FRESH_DATE> --actor "<person>" --dry-run

# 4. Single family, real write, only after inspecting the dry run
python research/forward_evidence/forward_snapshot.py \
    --family h5_vol_regime --as-of <FRESH_DATE> --actor "<person>"

# 5. Post-run proof the operation was limited to one append
sqlite3 "file:research/evidence_library/ledger.db?mode=ro" \
  "SELECT COUNT(*) FROM runs;                       -- expect 81 + 1
   SELECT COUNT(*) FROM governance_receipts;        -- expect 27, unchanged
   SELECT COUNT(*) FROM state_transitions;          -- expect 13, unchanged
   SELECT name FROM sqlite_master WHERE type='table';  -- expect the same 6"
python -c "...verify_chain..."   # expect ok=True, n_receipts=27
```

Backup before step 4 per the standing practice (`scripts/backup.py --ledger`).

---

## J. Is separate governance approval required? — **Yes**

FB-12d approved **Option B's readiness semantics**. It did not approve:

1. **Deploying 22 commits** carrying five modules that have never run on the
   host — inert by analysis, but never executed there.
2. **Writing the first `forward-snapshot-v1` row** into the production ledger.
   Append-only means it is permanent.
3. **Pre-empting the nightly collector's row for a date** (§C.1), which changes
   which producer owns that date.

Each is an irreversible production act. **Stopping at that boundary.**

---

## K. Recommended next gate

**FB-12i — controlled production deployment and single-family dry run.**
Deploy `83d6756`, verify the collectors are byte-identical and the chain intact,
and run `--dry-run` for one family. **No real write.** That converts every
claim here from analysis into observation at zero irreversible cost.

A real snapshot write should then be its own gate, with the `as_of` chosen
explicitly and a fresh ledger backup taken first.

**Stopped at the execution boundary.**

---

## L. Trail

| Gate | Outcome |
|---|---|
| FB-12g | Readiness block frozen into new L2 snapshots, CI green (`83d6756`) |
| **FB-12h** | **This audit. Deployment inert; first snapshot would PASS TG-RDY; as_of pre-emption found; approval required** |
| FB-12i | Proposed: deploy + dry run, no write |
