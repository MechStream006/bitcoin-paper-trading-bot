# FB-10b-W — Corpus Provisioning & Deployment Decision Audit

**Date: 2026-09-01 (UTC) · Gate FB-10b-W · READ-ONLY DECISION AUDIT.**

Nothing deployed, restarted, provisioned, computed or written. No lockbox
accessed. No code modified. Production operations were read-only listings and
`systemctl is-enabled/is-active` only. This document is the sole artifact.

Resolves the four defects recorded by FB-10b-V, and records **one new finding
(D-5)**.

---

## A. D-1 — CORPUS AUTHORITY

### A.1 What the producer actually requires — narrower than FB-10b-V implied

| requirement | status on production |
|---|---|
| `research/evidence/{engine,signals,governance}.py` | **present** |
| `research/data/raw/spot/BTCUSDT` monthly parquet | 2025-01 … 2026-08 |
| `research/data/raw/perp/BTCUSDT` | 2025-01 … 2026-08 |
| `research/data/raw/funding/BTCUSDT` | 2025-01 … 2026-08 |
| `research/data/raw/dvol/BTC` | **NOT REQUIRED** — see below |
| `research/reports/evidence_library/evidence_library.json` | **NOT REQUIRED** — see below |

**Two corrections to FB-10b-V's requirement list**, both established this gate:

- **`dvol` is not required.** `engine.load_master()` loads it inside a
  `try/except`, and its only consumers (`dvol_pctile` / `dvol_ivrank` →
  `sig_implied_vol_regime`) belong to `DVOL_FAMILY`, which FDB-1 **excludes**
  from the 34. Production's full 2021–2026 dvol coverage is irrelevant here.
- **`evidence_library.json` is not a runtime dependency.** `grep -c
  evidence_library research/evidence_library/forward_dsr_bh.py` → **0**. FDB-2
  excludes its stored Sharpes explicitly (annualised, `hold_days=1`,
  full-corpus). It is consumed only by a *test*
  (`test_matches_the_evidence_library_exactly`). Its absence on production
  blocks nothing at runtime.

### A.2 Exact date range and exact missing set

FDB-2a's window is **`2021-01-01 … 2025-05-31`**. Production already holds
2025-01 … 2025-05 — the tail of it. The gap is **everything before 2025-01**:

| measure | value |
|---|---|
| Missing partitions (spot + perp + funding) | **156** |
| Range | 2021-01 … 2024-12 (funding also carries 2020) |
| Bytes | **139.1 MB** |
| Carrying a `_manifest.json` with SHA-256 | **156 / 156** |
| **Of the 156, inside the lockbox `2025-06 … 2026-05`** | **ZERO** |

Production disk is 25–80 GB (ADR-0001 §L2); the corpus is currently 94 MB.
Provisioning takes it to ~233 MB.

### A.3 Provenance / hash mechanism

`research/storage/manifest.py` writes a `_manifest.json` sidecar beside every
partition carrying a SHA-256 of the parquet bytes (`_sha256`, raw chunked — not
the LF-normalising `pre_reg_guard` variant, correct for binary). Independently,
`run_inputs.parquet_sha256` pins the exact bytes each forward observation was
collected from. A conformance layer (`tests/test_backfill_conformance.py`,
`is_conformed` requiring `admissibility` + `conditioning` + `raw_content_hash`)
certifies partitions.

**Every one of the 156 partitions is hash-manifested**, so provisioning is
verifiable end-to-end without trusting the transport.

### A.4 Where the corpus already exists

| location | holds the 156? |
|---|---|
| Developer workstation `research/data/raw/` | **YES** — 2021-01 … 2026-07, manifested |
| Git repository | **No** — `.gitignore:17-19` *"Research data lake — raw + derived are **regenerable**"* |
| Backups | **No** — `scripts/backup.py` covers the four DBs; ADR-0001 §12 lists *"Parquet corpus \| rsync \| weekly \| **recommended** \| 3 — re-ingestable"*, and §12's own status note confirms *"neither the Parquet corpus nor `.env` is covered"* |
| Re-ingestable from source | **YES** — `ingest/binance_ohlcv.py` etc.; BK1: *"the corpus is re-ingestable"* |

So there are two provisioning routes: **copy** the workstation's manifested
partitions, or **re-ingest** on the host. Both are legitimate; the copy is
hash-verifiable against manifests that already exist.

### A.5 Does provisioning read or spend a lockbox? **No — and the premise has already lapsed**

**Production already holds the entire sealed lockbox window on disk.** Measured
this gate: spot, perp and funding each carry `2025-01 … 2026-08`, which fully
contains `2025-06-01 … 2026-05-31`. This has been true since forward collection
began — `daily_ingest.py` refreshes the current month, so the corpus grew
forward straight through the sealed window.

Consequences, stated carefully:

- Provisioning the 156 pre-2025 partitions adds **zero** lockbox bytes.
- Lockbox **exposure** therefore does not distinguish Option A from Option B:
  the bytes are already there under both.
- Nothing here is *spending*. FB-10b-J's rule stands: existence ≠ spending;
  gating use = spending. The producer's slice (`day_ts < LOCKBOX_START_MS`,
  single choke point, proven at FB-10b-V §D) is what prevents spending, and it
  is unaffected by which bytes sit on disk.

### A.6 Is production architecturally expected to hold this corpus?

**Partially — the ADR contemplates a corpus on production, but nothing provides
history.**

- ADR-0001 §12 lists the Parquet corpus as a **production asset** with a
  recommended weekly rsync, and §L2 sizes the production disk around it.
- `.dockerignore` strips `research/data/` from `Dockerfile.research` (INV-1), so
  the corpus lives on the **host**, never in a container.
- But `daily_ingest.py` writes **only the current month**. Production's corpus
  is by design a **forward rolling store** for collection, with no mechanism to
  acquire history. That is exactly why it begins at 2025-01.

So production is expected to hold *a* corpus; it is not expected — and has no
means — to hold the *research* corpus. Provisioning would be a deliberate,
one-time act outside the collector's design.

---

## B. ARCHITECTURE A vs B

| dimension | **A — provision the train slice to production** | **B — compute off-host, ship the snapshot** |
|---|---|---|
| Governance | No frozen contract changes. FDB-11 says operator-invoked, never says *where* | No frozen contract changes either |
| Reproducibility | Full. FDB-9 freezes population hash, window, basis, battery into the row | **Equally full** — FDB-9's metadata travels in the row, not in the execution location |
| Integrity | 156 manifested SHA-256s verifiable on arrival | Payload must be transported and verified; **no such mechanism exists** |
| Security | +139 MB of public market data. **No new lockbox exposure** (§A.5) | No new data on production |
| Operational complexity | One-time copy or re-ingest; then everything runs where it already runs | Requires a payload transport + verification component, and an operator procedure spanning two hosts |
| Backup/rollback | Corpus is priority-3 re-ingestable and unbacked either way; rollback = delete 156 dirs | Nothing to roll back on production; the off-host copy becomes a second operational surface |
| Frozen contract changes | **None** | **None** |
| Lockbox status | **Unchanged** | **Unchanged** |
| Ledger carries complete provenance | **Yes** | **Yes** |

### B.1 GL-2 is the architectural determinant

> *"`/opt/btcbot/research/evidence_library/ledger.db` on the production host is
> the canonical governance ledger. The developer-workstation copy … is a
> byte-identical **replica**, not a second authority: **no governance act may be
> recorded solely on it**, and it is refreshed from production rather than
> merged with it (the two chains are hash-linked and append-only, so a merge is
> **structurally impossible**)."* — ADR-0001 §12, GL-2 (2026-08-28)

Therefore:

- **The `append_run` write must occur on production.** Computing *and writing*
  off-host, then syncing the ledger back, is **excluded by GL-2** — that is a
  restore, not an append, and a merge is structurally impossible.
- **B is still legitimate**, but only in one shape: compute off-host → transport
  the *payload* → invoke `forward_snapshot` **on production** to append it. The
  producer already suits this: it returns a payload and has no write path.
- That shape needs a payload transport and integrity check **that does not
  exist today**.

---

## C. DOES THE REPOSITORY ALREADY DETERMINE THE ARCHITECTURE?

**It determines the write location, not the compute location.**

- **Determined:** the row is appended on production (GL-2).
- **Determined:** the producer is separable from persistence — it returns a
  payload and contains no writer (verified at FB-10b-V §F). That separation was
  designed for OD-5 and happens to make B expressible.
- **Not determined:** where the *computation* runs. FDB-11 fixes *who*
  (operator, never a service) and never *where*. ADR-0001 §12 leans toward a
  corpus on production by listing it as a production asset, but `daily_ingest`
  gives production no way to obtain history.

**This is a genuine human decision.** The repository narrows it — B requires a
component that does not exist; A requires a one-time data act with no contract
change — but it does not settle it.

---

## D. D-2 — OPERATOR PATH

| question | finding |
|---|---|
| Does a CLI already exist, undocumented? | **`forward_snapshot.py` has a full CLI** (`--family --as-of --actor --forward-db --ledger-db --registry --dry-run`), and `forward_dsr_bh.py` has a diagnostic CLI. **Neither is documented anywhere** — `grep` across `docs/` and `docs/runbook.md` finds no occurrence outside governance records. FB-12i/j/k invoked it ad hoc |
| Is the API surface sufficient? | **Yes at the function level.** `write_snapshot(..., dsr_block=, governance=)` and `build_snapshot_metrics(..., dsr_block=)` already exist and are tested. **No new API is needed** |
| What is actually missing? | Only **argument plumbing in `main()`**: neither branch passes `dsr_block`/`governance`, and there is no flag to supply them |
| Would a CLI require scheduler changes? | **No.** Nothing in `run_daily.py`/`ledger_sync.py` imports `forward_snapshot` — asserted by an existing test. `ledger_sync.py` need not be touched |
| Can dry-run expose DSR/BH without writing? | **Yes, with no new mechanism.** `build_snapshot_metrics(dsr_block=…)` already embeds it, and the dry-run branch already calls that function |
| Can the real write stay a separate explicit operation? | **Yes** — it already is: `--dry-run` returns before `write_snapshot` is ever reached |

**Smallest safe operator interface** (specified, not implemented): one flag on
`forward_snapshot` naming a **file** containing a producer payload, passed to
both branches identically; the producer keeps its own diagnostic CLI to emit
that file. Two explicit operator steps, no scheduler change, no shared-module
change, dry-run parity preserved by construction because both branches already
route through `build_snapshot_metrics`.

---

## E. D-3 — ENGINE COLLISION

**Mechanism.** `research/evidence/signals.py:16` does a **bare**
`from engine import SignalSpec`. `sys.modules` is process-global, and
`research/dq/engine.py` (PR-7) registers under the same bare name `engine`.
Whichever imports first wins; `forward_dsr_bh.library_specs()` then binds the
wrong module and raises `ImportError: cannot import name 'SignalSpec'`.
Reproduced deterministically, and reproduced again this gate in the same suite
combination (4 failures, identical set — deterministic, not flaky).

**The repository already has the convention.**
`research/feature_store/evidence_engine_bindings.py:57` loads
`research/evidence/engine.py` under a process-unique `sys.modules` key via
`importlib.util.spec_from_file_location`, and its comment documents this exact
collision by name.

**One extension is needed**, because the precedent only had to load `engine`
while the producer also needs `signals`, whose own bare `from engine import`
still resolves through `sys.modules['engine']`. Probed this gate on a poisoned
interpreter (no code modified): loading evidence `engine` under a unique key,
binding it to the bare name **only while `signals` executes**, then restoring —
yields **34 specs**, with the poisoned binding restored intact afterwards.

| question | answer |
|---|---|
| Avoidable without changing shared modules? | **Yes** — entirely local to `forward_dsr_bh.py` |
| Does the existing convention provide it? | **Yes**, plus the scoped bind/restore above |
| Does fixing it require touching protected research modules? | **No.** `research/dq/engine.py`, `research/evidence/engine.py` and `signals.py` are all untouched |

A fail-closed alternative — assert `engine.__file__` resolves under
`research/evidence/` and refuse otherwise — is also purely local and matches
"assert, never repair" (ADR-P1-1). **Not selected here.**

---

## F. D-4 — EXACT DOCUMENTATION CORRECTION

Current text, `research/forward_evidence/forward_snapshot.py` lines 36-41:

```
- **No DSR / BH-p.** `governance=None` always, so both stay NULL. Computing them
  is a different authority and a different gate.
- **No admission, re-pin, transition, gate_result, receipt or promotion.** This
  module appends a run row and nothing else.
- **No modification of any existing row.** The ledger is append-only; a snapshot
  is always a *new* `(family_id, provenance, window_end)`.
```

Two of the three statements are stale. Proposed replacement, describing only
the invariants the code actually holds — **no behavioural change is implied or
hidden**:

```
- **No DSR / BH-p COMPUTED HERE.** `governance` defaults to None, so both stay
  NULL unless an operator hands in a battery result from `forward_dsr_bh`
  (OD-5 = C2: three enforcement points each resolve ONE run, so the statistics,
  the readiness block and the floors payload must share it). This module never
  computes a statistic and never names one in executable code.
- **No admission, re-pin, transition, gate_result, receipt or promotion.** This
  module appends a run row and nothing else.
- **No modification of any existing row.** The ledger is append-only; a snapshot
  is always a *new* row, and the identity that makes it new is
  `(family_id, provenance, window_end, engine_version)` — engine-scoped, so the
  nightly collector's row at the same `window_end` is not mistaken for ours and
  is never overwritten.
```

Sentence 2 is unchanged and remains true.

---

## G. DEPLOYMENT SAFETY — reconfirmed

| question | answer |
|---|---|
| Is deployment still safe? | **Yes**, unchanged from FB-10b-V. No collector, scheduler, unit, config, schema or migration is involved |
| API restart required? | **Yes — `btcbot-api.service` only**, for an additive optional response field |
| Is corpus provisioning a separate operation? | **Yes, entirely.** It touches no tracked file, no service and no unit — it is a data act, independent of the git deployment |
| Does deployment itself write to production? | **No.** No migration, no DDL; the ledger is untouched by a checkout + one restart |
| Does the producer remain inert until invoked? | **Yes.** Imported by nothing, invoked by no timer, no write path, and it refuses on today's data |

---

## H. LOCKBOX IMPACT

**Neither architecture reads or spends the lockbox. No STOP is required.**

| | A | B |
|---|---|---|
| Adds lockbox bytes to production | **No** — 0 of the 156 partitions fall in the window | No |
| Lockbox bytes already on production | **Yes, both cases** — 2025-06 … 2026-05 present since collection began (§A.5) |
| Producer can read the window | **No** — single `load_master` call site behind `day_ts < LOCKBOX_START_MS`; `2026-05-31` absent from executable code |
| `lockbox_status` in any pre-registration changes | **No** | **No** |

Established from metadata and static evidence only; **no lockbox partition was
opened or read**.

---

## I. SAMPLE-SIZE STATUS — an independent blocker, and it must stay that way

| family | n | minimum |
|---|---|---|
| `h5_vol_regime` | 7 | 15 |
| `g1_market_regime_sma200` | **7** | **10** ← closest |
| `g3_momentum_tsmom` | 8 | 15 |
| `ivrv_vrp_spread` | 6 | 30 |
| `g3_atr_expansion` | 3 | 26 |
| `g1_trend_regime_ma_cross` | 0 | 30 |
| `g2_funding_level` | 0 | 30 |

**No DSR/BH was computed to establish this — counts only.**

Today the producer refuses with `empty_battery` **before** touching the corpus
(membership is resolved first, proven by a test that makes the loader raise and
shows it is never called). So the two blockers are correctly ordered, and
provisioning the corpus would **not** make anything computable.

### D-5 — NEW FINDING: the two blockers are not separately reported

The producer has **no explicit corpus-availability check**.
`_train_slice_master` refuses only when the sliced frame is **empty**. A frame
covering 2025-01 … 2025-05 is not empty, so a corpus-short production run would
proceed and fail later as `member_sharpe_uncomputable`, **naming a signal**
rather than the missing corpus.

That is fail-closed and safe — it never produces a wrong number — but it
reports a *statistical* reason for a *provisioning* fault. Under Architecture B
this never surfaces; under A it would surface only if provisioning were
incomplete. Recorded as a defect to fix alongside D-2/D-3/D-4, not resolved
here.

---

## J. DECISIONS REQUIRING HUMAN APPROVAL

| # | decision | why it is not mechanical |
|---|---|---|
| **W-1** | **Architecture A or B.** | §C: the repo determines the *write* location (GL-2) but not the *compute* location. A needs a one-time data act; B needs a payload-transport component that does not exist |
| **W-2** | If **A**: **copy the 156 manifested partitions, or re-ingest on the host?** | Both are legitimate; copy is hash-verifiable against existing manifests, re-ingest re-derives from source |
| **W-3** | **Deploy `af80fbd → c337f5f` now, or after fixing D-2/D-3/D-4/D-5?** | Deploying now delivers FDB-13 labelling to the live API and reports; deploying after delivers a working operator path in one action instead of two |
| **W-4** | Authorize the **D-2/D-3/D-4/D-5 fix gate** (scope in §D/§E/§F/§I). | Implementation was explicitly excluded from this gate |

A **write-authorization gate** for the first real snapshot carrying DSR/BH
remains separate and downstream of all four.

---

## K. RECOMMENDED SEQUENCING

**This section is a RECOMMENDATION, not a governance decision. Nothing below is
adopted, and none of it is implied by any frozen contract.**

1. **W-4 first — fix D-2, D-3, D-4, D-5** in one gate. All four are local: no
   shared module, no protected research file, no scheduler, no `ledger_sync`,
   no contract change. D-3 already has a repository precedent, and its
   extension was probed working this gate.
2. **W-3 — deploy once, afterwards.** Deploying after the fixes means a single
   production change carries FDB-13 labelling *and* a working operator path,
   with one `btcbot-api.service` restart. Deploying now is also safe; it simply
   delivers labelling alone and implies a second deployment later.
3. **W-1/W-2 — decide the architecture, then provision if A.** This can proceed
   in parallel with 1–2 and is not urgent: **sample size, not corpus, is the
   binding constraint today**, and `g1_market_regime_sma200` at 7/10 is on the
   order of a month away at roughly one 7-day trade per 8 days.
4. **Write-authorization gate — last**, once a family actually meets its
   minimum.

**Why this order rather than provisioning first:** provisioning changes nothing
observable until sample size arrives, whereas D-5 means an incompletely
provisioned host would misreport a provisioning fault as a statistical one — so
fixing D-5 before provisioning makes the provisioning itself verifiable.

---

## Verification of this audit

| check | result |
|---|---|
| Production modified | **No** — read-only listings and `systemctl is-*` only |
| Data provisioned | **No** |
| Code modified | **No** — `git diff` clean; the D-3 probe ran in a throwaway interpreter |
| DSR/BH computed | **No** |
| Snapshot written / ledger touched | **No** |
| Lockbox accessed | **No** |
| Relevant tests | 183 passed; the same 4 deterministic D-3 failures, unchanged from FB-10b-V |

**STOP.**

---

## Trail

| Gate | Outcome |
|---|---|
| FB-10b-U | Producer + `bh_adjusted_p`; battery refuses on today's corpus |
| FB-10b-V | DEPLOY-SAFE verdict; D-1…D-4 recorded |
| **FB-10b-W** | **This audit. D-1 scoped to 156 lockbox-free manifested partitions; GL-2 shown to fix the write location; D-2/D-3/D-4 resolved to local fixes; D-5 newly found; four human decisions stated** |
