# FB-10b-V — Production Deployment Readiness Audit

**Date: 2026-09-01 (UTC) · Gate FB-10b-V · READ-ONLY AUDIT.**
**Baseline: local `ee6434d` · production `af80fbd`.**

Nothing was deployed, executed against production, computed, or written. No
DSR/BH value exists. No lockbox was accessed. Production was not modified: the
only production operations were read-only `systemctl is-enabled/is-active`,
`git rev-parse`, directory listings, and two `scp` file copies taken to the
scratchpad. No production SQLite/WAL state was altered.

---

## A. VERDICT

> ### DEPLOY — SAFE, but it delivers no DSR/BH capability.

Two separate statements, both evidenced below:

1. **Deploying `af80fbd → ee6434d` is safe.** No collector, scheduler, systemd
   unit, config, schema or migration is involved. The producer is inert:
   nothing imports it, no timer invokes it, and it refuses on today's data. The
   one behavioural change to a running service is an **additive optional** API
   field, requiring a single `btcbot-api.service` restart.
2. **It does not enable a forward DSR/BH result.** Four prerequisites remain
   (§K), the largest being that **production does not carry the corpus the
   dispersion step requires**.

The deployment's real, immediate value is **FDB-13 convention labelling
reaching production** — the live API and the governance reports currently
present legacy raw p-values under a `BH-p` label with no discriminator.

**No STOP condition from §12 was triggered.** Four defects were found; none is
on that list, and all are recorded in §K.

---

## B. DEPLOYMENT DELTA

**14 commits · 35 files · +6,559 / −19.**

| category | files | note |
|---|---|---|
| Governance documents | 19 | records only, no runtime effect |
| Runtime code | 6 | see below |
| API | 2 | `src/api/schemas.py`, `src/api/services.py` |
| Governed artifacts | 5 | 4 pre-registrations + `registry.yaml` |
| Tests | 3 | 2 new, 1 updated |

**Runtime code, in full:**

| file | change | production effect |
|---|---|---|
| `research/evidence/governance.py` | **+30, additive** (`bh_adjusted_p`) | none until called |
| `research/evidence_library/bh_convention.py` | **new** | consumed by report/lifecycle/API |
| `research/evidence_library/forward_dsr_bh.py` | **new** | **inert** — imported by nothing |
| `research/evidence_library/lifecycle.py` | +8/−1 | gate message names the convention |
| `research/evidence_library/report.py` | +15/−3 | `bh_p` cells carry a marker |
| `research/forward_evidence/forward_snapshot.py` | +65/−7 | engine-scoped probe; optional payload |

**`registry.yaml`: 4 changed lines, every one a `hash:` value** — verified by
`grep -vc "hash:"` returning **0**.

### B.1 Must-be-unchanged — all verified across the full delta

| artifact | status |
|---|---|
| `ledger_sync.py` | **UNCHANGED** |
| `run_daily.py`, `runner_base.py` | **UNCHANGED** |
| all `runner_*.py`, all `decision_function*.py` | **UNCHANGED** |
| `daily_ingest.py`, `daily_job.py` | **UNCHANGED** |
| `collection_config.yaml` | **UNCHANGED** |
| `run_date_policy.yaml`, `run_date_policy.py` | **UNCHANGED** |
| `thresholds.yaml` | **UNCHANGED** |
| `record.py`, `schema.sql`, `schema_forward.sql` | **UNCHANGED** |
| `deploy/`, `scripts/` | **no files changed** |

### B.2 Restart / migration / dependencies

| question | answer |
|---|---|
| Restart required? | **Yes — `btcbot-api.service` only.** It is the sole running service whose code changed. Timers (`fwd-hg`, `evidence`, `backup`, `healthcheck`) load their code per-invocation and touch none of the changed files |
| Schema migration? | **None.** No DDL, no new column, no new table. `bh_p_convention` is derived at serialisation |
| Dependency change? | **None.** Verified in the production `btc_bot` env: numpy 2.2.6, pandas 2.3.3, scipy 1.15.3, duckdb 1.5.3, pyarrow 24.0.0, yaml 6.0.3 — every import the new code needs is already present |
| Collector/scheduler behaviour | **Unaffected** — nothing they import changed |

---

## C. FROZEN-CONTRACT VERIFICATION

### C.1 Pre-registration pins — 8 of 8 verify at `ee6434d`

Via `observation_floors.resolve_pinned_prereg` (A2a):

```
OK  g1_market_regime_sma200   693ef379…      OK  g3_momentum_tsmom    1cab7804…
OK  g1_trend_regime_ma_cross  a270d163…      OK  h5_vol_regime        0a27671b…
OK  g2_funding_level          6cba09d3…      OK  h6_funding_vol_regime 9201bd3a…
OK  g3_atr_expansion          846a965d…      OK  ivrv_vrp_spread      eb6f8e6f…
--> 8 verified, 0 mismatched
```

Pre-registrations and `registry.yaml` move in the **same commit**, so a partial
deploy that could desynchronise them is not possible via git.

### C.2 The seven minima

| family | minimum | source |
|---|---|---|
| `h5_vol_regime` | 15 | pre-existing prose |
| `g3_momentum_tsmom` | 15 | pre-existing prose |
| `g1_market_regime_sma200` | 10 | pre-existing prose |
| `ivrv_vrp_spread` | **30** | FB-10b-O, prose verified present |
| `g1_trend_regime_ma_cross` | **30** | FB-10b-O, prose verified present |
| `g3_atr_expansion` | **26** | FB-10b-O, prose verified present |
| `g2_funding_level` | **30** | FB-10b-O, prose verified present |

All four amended files carry both the `n >= N` clause and the non-conflation
statement. The producer **hardcodes none of them** — asserted by a test that
searches for each `family: value` literal and requires its absence.

### C.3 Statistical authority — static proof

| property | result |
|---|---|
| imports | `engine, governance, ledger_sync, signals, numpy, pandas` + stdlib |
| `compute_dsr_for_registration` imported | **NO** |
| `run_harmonization` imported | **NO** |
| `run_dvol_family` / `report` imported | **NO** |
| `sqrt(7`, `_dsr_proxy`, `psr_approx`, `n_trials=38` in executable code | **NONE** |
| `_sr_stats` anywhere in executable code | **NONE** |
| `G.deflated_sr(arr, sr_trials_std, N_TRIALS)` | present |
| `G.psr(arr, 0.0)` / `G.bh_adjusted_p(` | present |
| `N_TRIALS = 34`, `HOLD_DAYS = 7`, `TRAIN_END = "2025-05-31"` | present |
| member-Sharpe path free of `psr(` / `_sr_stats` / `deflated_sr` | **YES** |

Population verified **34**, set-identical to `evidence_library.json`, no
duplicates, the 4 DVOL specs excluded.

---

## D. LOCKBOX-SAFETY PROOF

| property | evidence |
|---|---|
| The slice predicate | `_train_slice_master` filters `day_ts < LOCKBOX_START_MS` before returning |
| The boundary | `LOCKBOX_START_MS = datetime(2025, 6, 1, tz=utc)`, asserted equal to `run_date_policy.yaml lockbox.start` by test |
| Single choke point | `load_master` appears **exactly once** in executable code — there is no second corpus reader |
| No alternate reader | `read_parquet` / `open_db` appear nowhere |
| `2026-05-31` in executable code | **absent** — its one occurrence is a comment stating the lockbox is *not* spanned |
| Slice-before-compute | the master is sliced inside the loader, so no caller ever holds a lockbox row and no rolling window can see one |
| Runtime confirmation | none needed: the producer refuses before `_train_slice_master` is reached, verified by a test that monkeypatches the loader to raise and shows it is never called |

**No production lockbox data was accessed to establish any of this.**

---

## E. CURRENT FAMILY SAMPLE COUNTS

Read from a read-only copy of the production forward corpus. **Counts only —
no Sharpe, DSR, PSR, BH or dispersion was computed.**

| family | n | minimum | short by |
|---|---|---|---|
| `h5_vol_regime` | 7 | 15 | 8 |
| `g1_market_regime_sma200` | **7** | **10** | **3** ← closest |
| `g3_momentum_tsmom` | 8 | 15 | 7 |
| `ivrv_vrp_spread` | 6 | 30 | 24 |
| `g3_atr_expansion` | 3 | 26 | 23 |
| `g1_trend_regime_ma_cross` | 0 | 30 | 30 |
| `g2_funding_level` | 0 | 30 | 30 |

**No family meets its frozen minimum. The battery is `REFUSED /
empty_battery`.** This is the recorded execution blocker.

---

## F. WRITE / IDEMPOTENCY / COLLISION ANALYSIS

**The path is not fully wired — see §G, defect D-2.** What exists:

```
operator CLI (forward_snapshot)      -- has NO --dsr flag; cannot supply a battery
    write_snapshot(..., dsr_block=, governance=)   <-- function API only, tested
        _existing_snapshot_run(...)  -- engine-scoped probe
        build_snapshot_metrics(...)  -- readiness + floors + optional DSR block
        record.append_run(..., governance=governance)
            INSERT INTO runs         -- one row, append-only
```

| question | answer |
|---|---|
| Idempotency key | `(family_id, provenance='live-forward', window_end, engine_version='forward-snapshot-v1')` |
| Engine scoping | present, and asserted: `AND engine_version = ?` in the probe |
| Collision with `ledger_sync` | a `forward-collect-v1` row at the same `window_end` **no longer suppresses** a snapshot (tested); the collector's own idempotency is untouched, and `ledger_sync.py` is byte-identical |
| Can an existing row be overwritten? | **No.** The only DB verb reachable is `INSERT`; a duplicate is prevented by not writing, never by editing |
| Can historical rows be modified? | **No.** The producer contains no `append_run`, `INSERT`, `UPDATE`, `DELETE`, `init_db` or `commit()` — verified by source scan |
| Does refusal write anything? | **No.** `compute_battery` returns a result object; refusal never reaches a writer, and the producer has no writer |
| Does success create exactly one row? | **Yes** — one `record.append_run` per member family, no `gate_results`, no `state_transitions`, no receipt |

---

## G. DRY-RUN ASSESSMENT — one requirement NOT met

Executed against read-only copies (`--dry-run`, `h5_vol_regime`, `as-of
2026-08-25`):

| requirement | result |
|---|---|
| Executes the same **payload** construction as the real write | **YES** — both route through `build_snapshot_metrics`; parity is separately covered by `tests/test_forward_snapshot_dryrun_parity.py`, including a sabotage test |
| Displays readiness | **YES** — 53/72 ready, **19 excluded** (exactly INC-0005's window) |
| Displays admissibility | **YES** — blocking floors listed |
| Displays the DSR/BH decision/result status | **NO — `forward_dsr_bh` absent from the payload** |
| Never writes the ledger | **YES** — ledger SHA-256 identical, ledger never opened |
| Never creates governance tables | **YES** — the dry-run branch bypasses `write_snapshot` entirely |
| Never accesses lockbox data | **YES** |

**Defect D-2 (wiring gap, NOT a divergence defect).** `forward_snapshot.main()`
passes neither `dsr_block` nor `governance` on **either** branch, and offers no
flag to supply them; `forward_dsr_bh` references neither `forward_snapshot` nor
`append_run`. So there is **no operator CLI path** from producer to ledger.

This is materially different from the FB-12i defect, which was a genuine
divergence — a dry-run rendering a payload the write would never produce. Here
the two CLI branches are **consistent with each other** (both omit DSR), and the
function-level API preserves parity when a block is supplied. Nothing is
misleading; a capability is simply absent.

**Secondary gap:** the CLI dry-run also skips `assert_ledger_schema`,
`assert_forward_schema` and the engine-scoped probe, so it cannot warn an
operator that a write would be skipped or that the ledger schema is wrong.

---

## H. LEGACY `bh_p` SAFETY

Against the **production** ledger (read-only copy), using the deployment
candidate's classifier:

```
ivrv_vrp_spread   offline-corpus  0.2893 -> legacy_raw_p
ivrv_vrp_spread   offline-corpus  0.2893 -> legacy_raw_p
g3_atr_expansion  offline-corpus  0.2073 -> legacy_raw_p
```

Reinterpretation is impossible by construction: F5 keys on the presence of the
complete 12-field FDB-9 block, all three rows carry **zero** of those fields,
and the producer contains no `get("bh_p")` or `["bh_p"]` read of an existing row
(asserted by test). Nothing in the delta writes to `runs`, so the values cannot
change.

---

## I. TEST COMPARISON

| suite | result |
|---|---|
| Producer (`tests/test_forward_dsr_bh.py`) | **67 passed** |
| FDB-13 (`tests/test_bh_convention.py`) | **42 passed** |
| Snapshot (3 files) | **67 + 21 + 18 passed** |
| INV-1 | **11 passed** |
| `report.py --check` | **OK — 35 families (9/22/4)** |
| `verify_chain` (local) | **ok, 27 receipts** |
| `verify_chain` (production copy) | **ok, 27 receipts** |
| **Full suite at clean `ee6434d`** | **24 failed / 4,963 passed / 4 skipped** |
| **Pristine baseline at `d7b715e`** | **24 failed / 4,896 passed / 4 skipped** |
| **Failure-set delta** | **EMPTY — no new failures, none fixed** |

The +67 passed is exactly the new producer file. **Zero regressions**; the 24
failures are the documented pre-existing set (corpus-conformance and
`test_dq_probes` items untouched by this program).

**Defect D-3, found by this audit.** In one *ad hoc* suite combination, four
`TestPopulation` tests fail with
`ImportError: cannot import name 'SignalSpec' from 'engine'
(research/dq/engine.py)`. Reproduced deterministically: importing
`research/dq/engine` first makes `library_specs()` raise. This is the
**pre-existing `sys.modules['engine']` collision** recorded in CLAUDE.md §18
Increment 6 (`research/dq/engine.py` vs `research/evidence/engine.py`), to which
`forward_dsr_bh` is a **new consumer**. It **fails closed** (ImportError, never
a wrong result) and does not occur in the full-suite ordering — all 67 tests
pass there. It must be fixed before the producer is ever invoked in a process
that has already touched the `research/dq` stack.

---

## J. PRODUCTION-STATE BASELINE (read-only)

| item | value |
|---|---|
| git HEAD | **`af80fbd`** |
| Working tree | 3 untracked `.env.bak.*` operator artifacts (pre-existing) |
| Ledger SHA-256 | `b8eede685794c358` |
| Runs | **87** |
| Receipts | **27** |
| Transitions | **13** |
| Tables + views | **10** |
| L2 rows | **16**, of which **0** carry `dsr_prob`/`bh_p` |
| Chain verification | **ok, 27 receipts** |
| Services | `btcbot-fwd-hg.timer`, `btcbot-evidence.timer`, `btcbot-api.service`, `btcbot-backup.timer`, `btcbot-healthcheck.timer` — all **enabled + active** |
| Research corpus | `spot`, `perp`, `funding`: **year=2025, year=2026 only**; `dvol`: 2021–2026 |
| `reports/evidence_library/evidence_library.json` | **ABSENT** |

Local at `ee6434d`: ledger `dd568de2f50a23a4`, 71 runs, 27 receipts, chain ok,
working tree clean but for 2 pre-existing untracked docs.

---

## K. PREREQUISITES BEFORE THE FIRST REAL DSR/BH RESULT

Four defects and one data condition, in the order they block.

### D-1 — Production lacks the corpus the dispersion requires *(largest)*

`spot`, `perp` and `funding` carry **only 2025 and 2026**. FDB-2a's window is
`2021-01-01 … 2025-05-31`, so **2021–2024 are entirely absent**, and
`evidence_library.json` is absent too. On production, `_train_slice_master`
would yield roughly five months; most of the 34 signals need 200–252-day
warmups, so many would produce no trades and the battery would refuse with
`member_sharpe_uncomputable`.

That refusal is **fail-closed and safe**, but it would be the *wrong reason* —
an artefact of provisioning, not a statistical fact. **The producer cannot
compute on production as currently provisioned**, regardless of sample size.
Either the full 2021–2025 corpus is provisioned there, or the battery is
computed off-host. That is a governance question this audit does not answer.

### D-2 — No operator wiring producer → snapshot → ledger

§G. Needs a CLI path and, with it, an explicit write authorization gate.

### D-3 — `sys.modules['engine']` collision exposure

§I. Fix by resolving `engine`/`signals` by explicit file location rather than a
bare `import`, or by asserting the resolved `__file__` and failing loudly.

### D-4 — Two stale docstrings in `forward_snapshot.py`, from my own FB-10b-U commit

- Lines 35-37: *"**No DSR / BH-p.** `governance=None` always, so both stay
  NULL"* — contradicted by `governance=governance`.
- Line 41: the idempotency key is described as
  `(family_id, provenance, window_end)` — now engine-scoped.

Documentation-only, but both assert invariants the code no longer holds.

### E-1 — Sample size

§E. `g1_market_regime_sma200` is closest at **7/10**. At roughly one 7-day trade
per 8 days that is on the order of a month; the rest are far further out.

### Order implied by the above

Deployment is safe **now** and delivers FDB-13 labelling to production. It
delivers no DSR/BH capability until **D-1** is resolved — and fixing **D-2**,
**D-3** and **D-4** before deploying would let one deployment carry a working
path rather than two. That sequencing is Rohith's call; this audit does not make
it.

---

## Verification of this audit

| check | result |
|---|---|
| Production modified | **No** — read-only commands and two `scp` copies only |
| Production SQLite/WAL altered | **No** — the ledger was copied, not opened in place |
| DSR/BH computed | **No** |
| Snapshot written | **No** |
| Lockbox accessed | **No** |
| Local tracked files modified | **None** — this document only |

**STOP.**

---

## Trail

| Gate | Outcome |
|---|---|
| FB-10b-T2 | FDB-13 adopted; convention labelling across all three renderers |
| FB-10b-U | Producer + `bh_adjusted_p`; battery refuses on today's corpus |
| **FB-10b-V** | **This audit. DEPLOY-SAFE verdict; 4 defects and 1 data condition recorded as prerequisites** |
