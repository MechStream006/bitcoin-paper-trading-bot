# FB-12k — First Real Forward Snapshot Write

**Date: 2026-08-31 (UTC) · Gate FB-12k · Outcome: SUCCESS. One L2 snapshot row
written to the production ledger. Every post-write invariant holds, and the
persisted payload is byte-identical to the pre-write dry-run.**

The first `forward-snapshot-v1` row on this platform, and the first run to carry
a readiness assessment. **The write is irreversible and permanent.**

---

## 1. Pre-write conditions — all five satisfied

| # | condition | result |
|---|---|---|
| 1 | fresh ledger backup | `ledger_20260831_155447.db`, 188 KB |
| 2 | backup integrity | `PRAGMA integrity_check` = **ok**; sha256 `b70fc1fc…` **matches its manifest exactly**; 81 runs / 27 receipts |
| 3 | no existing row for this family/window | `rows_h5_at_2026_08_28 = 0`; `snapshot_v1_rows_total = 0` |
| 4 | `verify_chain` | `ok=True, n_receipts=27` |
| 5 | production tree | `af80fbd`, 0 modified tracked files |

A fresh dry-run was captured immediately beforehand and its canonical payload
hash recorded — **`371cc8fa1dc4a2566ecb373d049a306e`** — as the comparison
anchor.

---

## 2. The write

```
forward_snapshot.py --family h5_vol_regime --as-of 2026-08-28 --actor rohith
→ written  2d1adf5d-37b8-4634-9f11-ecb521c0f802   (exit 0)
```

Log line: `n_trades=7  floors_blocking=['minimum_active_days',
'minimum_calendar_months_before_assessment', 'minimum_high_vol_periods_observed',
'minimum_regimes_observed', 'minimum_trades']` — the observation floors are
correctly reported as unmet, which is the expected and honest state.

---

## 3. The persisted row

| field | value |
|---|---|
| `run_id` | `2d1adf5d-37b8-4634-9f11-ecb521c0f802` |
| `family_id` | `h5_vol_regime` |
| `provenance` / `run_kind` | `live-forward` / `forward` (lane L2) |
| `window_start` … `window_end` | `2026-06-15` … `2026-08-28` |
| `engine_version` | `forward-snapshot-v1` |
| `n_trials` | 34 |
| `git_sha` | `af80fbd` |
| `dsr_prob` / `bh_p` | **NULL / NULL** |

### Dry-run parity — the decisive check

```
dry-run   canonical sha256: 371cc8fa1dc4a2566ecb373d049a306e
persisted canonical sha256: 371cc8fa1dc4a2566ecb373d049a306e
IDENTICAL: True
```

**The write persisted exactly what the dry-run displayed**, byte for byte. This
is the property FB-12i-R was created to establish, now demonstrated on
production rather than in tests.

### Readiness block

```
days_total = 75   days_ready = 56   days_excluded = 19   days_non_ready = 0
exclusions  = 2026-07-18 .. 2026-08-05, set-equal to declared_gap_days()
undeclared  = NONE
basis       = run_date_policy.yaml#immutable_gaps
source      = run_log:status=ok
```

### Admissibility

`forward-admissibility/1`, 5 floors, **`prereg_pin_verified = true`**.

---

## 4. TG-RDY on the persisted row

```
DefaultReadinessProvider.assess(ledger, run) → ok=True  source=run_block  56/75
```

**PASS.** This is the first run on this platform to satisfy the readiness gate.

**It does not make h5_vol_regime promotable.** Entering `validated` independently
requires `dsr_prob >= 0.50` and `bh_p <= 0.10`, both **NULL** and both hard
failures under R1's NULL discipline, plus pairwise independence whose
`min_pairwise_n` remains unresolved (FB-10c-G). Readiness has simply stopped
being the blocker; DSR/BH are now the blocker.

---

## 5. Post-write invariants

Verified by row-by-row comparison of the live ledger against the **pre-write
backup**, not merely by counts:

| check | result |
|---|---|
| `forward-snapshot-v1` rows | **1** |
| `runs` | 81 → **82** |
| live-forward runs | 10 → **11** |
| rows **added** | **1** — `2d1adf5d…` |
| rows **removed** | **0** |
| rows **CHANGED** | **0** |
| `governance_receipts` | 27 → **27** (unchanged) |
| `state_transitions` | 13 → **13** |
| `gate_results` | 145 → **145** |
| `pairwise_r` | 0 → **0** |
| tables | **6**, unchanged — no `contract_registry`, no `data_readiness` |
| `verify_chain` | **`ok=True, n_receipts=27`** |
| `report.py --check` | **OK**, 35 families (9 candidate / 22 discovery / 4 disqualified), exit 0 |
| `evidence_forward.db`, `forward_evidence.db`, `registry.yaml`, `thresholds.yaml`, `run_date_policy.yaml` | **all byte-identical** |
| services / timers | `btcbot.service` and `btcbot-api.service` active; both timers scheduled |
| tree | `af80fbd`, 0 modified tracked files |

**No contract activation, no receipt, no transition, no gate_result, no
`pairwise_r` change, no admission, no pinning, no promotion, no DSR/BH
computation, no scheduler change, no historical evidence rewritten.**

---

## 6. What changed in the world

Exactly one thing: the production governance ledger now contains one additional
append-only L2 run row, `2d1adf5d…`, carrying a frozen readiness assessment for
`h5_vol_regime` over `2026-06-15 … 2026-08-28`, in which the 19 INC-0005 days
are explicitly excluded on the authority of `run_date_policy.yaml`'s
`immutable_gaps` and the remaining 56 days are backed by contemporaneous
`run_log` evidence.

That row is permanent. It is not to be deleted, rewritten, or superseded by
erasure — only ever added to.

---

## 7. Next

Nothing follows automatically. Candidate directions, each needing its own gate:

- **The remaining four families** (`g1_market_regime_sma200`,
  `g3_momentum_tsmom`, `ivrv_vrp_spread`, `g3_atr_expansion`) could receive
  equivalent snapshots. Note the FB-12h §C.1 interaction: choose each `as_of`
  deliberately, since a date the nightly has not yet reached will be claimed by
  the snapshot and the nightly will then silently skip.
- **DSR/BH** are now the binding blocker at Validated, and remain blocked on
  FB-10a's U1/U2/U3.
- **Scheduler wiring** for the readiness producer remains unnecessary for the
  run-block path and is still a separate future gate.

---

## 8. Trail

| Gate | Outcome |
|---|---|
| FB-12h | Deployment analysed as inert; first snapshot predicted to PASS TG-RDY |
| FB-12i | Deployed `f64fc12`; dry-run exposed the missing-readiness defect |
| FB-12i-R | Defect fixed; dry-run and write share one payload path |
| FB-12j | Deployed `af80fbd`; dry-run reverified end-to-end; TG-RDY PASS |
| **FB-12k** | **This record. One L2 snapshot written; payload identical to the dry-run; all invariants hold** |
