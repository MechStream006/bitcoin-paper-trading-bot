# FB-12j — Production Dry-Run Reverification

**Date: 2026-08-31 (UTC) · Gate FB-12j · Outcome: PASS. The production dry-run
now exercises and displays the complete payload, all thirteen checks hold, and
TG-RDY resolves to PASS from the actual embedded block. No snapshot written.**

Deployment of `af80fbd` and one dry-run were authorized and performed. No
snapshot write, no contract activation, no scheduler wiring, no DSR/BH, no
`pairwise_r` change, no admission/pinning/promotion, no threshold or
pre-registration change, no backfill.

---

## 1. Deployment

| | |
|---|---|
| Before | `f64fc12`, 0 modified tracked files |
| After | **`af80fbd`**, 0 modified tracked files |
| Method | clean fast-forward on `main`, no merge commit |
| Delta | **1 commit, 2 files**: `forward_snapshot.py` (+30/−16) and a new test file |
| Collectors, `run_date_policy.*`, `forward_readiness.py`, `evidence_library/`, `src/` | **unchanged** (empty diff) |
| Restart | **none performed or required** — `forward_snapshot.py` is imported by no collector and no long-running service |
| Services after | `btcbot.service` active, `btcbot-api.service` active; both timers scheduled |
| Ledger after deploy | byte-identical |

---

## 2. Dry-run

`h5_vol_regime`, `as_of = 2026-08-28`, `--dry-run`, exit 0, empty stderr.

Rationale unchanged from FB-12i: inside the verified corpus (7 closed trades),
window contains INC-0005, and no existing L2 row at that `window_end` — so the
full payload path executes rather than short-circuiting on idempotency.

**The FB-12i defect is fixed and confirmed in production.** The payload's
top-level keys now include `readiness` alongside `forward_admissibility`.
Everything below is read **from the CLI's own output**, not reconstructed.

### The readiness block, verbatim

```json
{
  "days_total": 75,
  "days_ready": 56,
  "days_non_ready": [],
  "days_excluded": ["2026-07-18", ... , "2026-08-05"],   // 19 days
  "exclusion_basis": "run_date_policy.yaml#immutable_gaps",
  "source": "run_log:status=ok",
  "window_start": "2026-06-15",
  "window_end": "2026-08-28"
}
```

### The thirteen checks

| # | check | result |
|---|---|---|
| 1 | readiness block present | **yes** |
| 2 | 56 READY / 19 excluded / 0 non-ready | **yes** |
| 3 | exclusions are exactly INC-0005 | **yes** — `2026-07-18 .. 2026-08-05`, set-equal to `declared_gap_days()` |
| 4 | `exclusion_basis` names `immutable_gaps` | **yes** |
| 5 | undeclared exclusions | **NONE** |
| 6 | `forward_admissibility` intact | **yes** — `forward-admissibility/1`, 5 floors |
| 7 | `prereg_pin_verified` | **true** |
| 8 | DSR/BH absent | **yes** — no `dsr`/`bh` key; `oos_sharpe=None`, `verdict=None` |
| 9 | ledger/receipt/registry/schema writes | **none** |
| 10 | existing L2 rows | unchanged — 81 runs, 10 live-forward |
| 11 | `verify_chain` | `ok=True, n_receipts=27` — unchanged |
| 12 | production hashes | unchanged except deployed application files |
| 13 | scheduler / services | unaffected |

### TG-RDY

`DefaultReadinessProvider.assess()` was given the **actual dry-run payload**
(the whole `metrics` dict from the CLI output, not a rebuilt block):

```
ok=True  source=run_block  ready=56/75
detail: run's readiness block reports 56/75 days READY
```

**PASS.** As FB-12h recorded, this does not make the family promotable —
`dsr_prob` and `bh_p` remain NULL and are independent hard failures at the
Validated gate. Readiness simply is no longer the blocker.

---

## 3. Production invariants

| | baseline | after |
|---|---|---|
| `runs` / `live_forward` | 81 / 10 | **81 / 10** |
| `governance_receipts` | 27 | **27** |
| `state_transitions` / `gate_results` / `pairwise_r` | 13 / 145 / 0 | **13 / 145 / 0** |
| tables | 6 | **6** (no `contract_registry`, no `data_readiness`) |
| rows at `window_end=2026-08-28` | 0 | **0** |
| `forward-snapshot-v1` rows | 0 | **0** |
| `ledger.db` sha256 | `b7085fbb…` | **`b7085fbb…`** |
| `evidence_forward.db`, `forward_evidence.db`, `registry.yaml`, `thresholds.yaml`, `run_date_policy.yaml` | — | **all unchanged** |
| `verify_chain` | ok, 27 | **ok, 27** |

---

## 4. One observation, reported rather than repaired

Two sidecar files exist on the ledger that were absent at baseline:
`ledger.db-shm` (32 KB) and `ledger.db-wal` (**0 bytes**).

Per this gate's instruction they were **not** auto-repaired. Attribution,
established filesystem-only without opening any database:

- `ledger.db` mtime is **01:00:41** — the nightly job's own run, hours before
  this session. **The database file was never touched.**
- The sidecars' mtime is **15:45:35**, which is *this audit's own read-only
  verification queries* (`sqlite3 file:…?mode=ro`), not the dry-run. The CLI
  dry-run path (`build_snapshot_metrics`) never opens the ledger at all.
- `ledger.db` sha256 is **identical** to baseline, and the `-wal` is **0 bytes**
  — no committed frames, nothing pending.

**No evidence was written or altered.** These are transient artifacts of
read-only access to a WAL-mode database, of the same class seen earlier in this
programme on local backup files, and SQLite manages them on its own. They are
recorded here so a future reader does not mistake them for a write, and left in
place because removing them was not authorized and is not necessary.

---

## 5. Conclusion

The dry-run **genuinely passes**. What FB-12i could only reconstruct, this gate
observed directly from the production CLI path.

**Prerequisites for a real write (FB-12k), unchanged from FB-12h §G:**

1. `as_of` chosen deliberately — an operator snapshot for a date the nightly has
   not yet reached claims that idempotency key first and the nightly then
   silently skips.
2. A fresh ledger backup taken immediately beforehand.
3. **Explicit governance approval for the write itself** — the first
   `forward-snapshot-v1` row is permanent and append-only.

**Stopped after the dry-run verification.**

---

## 6. Trail

| Gate | Outcome |
|---|---|
| FB-12h | Deployment analysed as inert; first snapshot predicted to PASS TG-RDY |
| FB-12i | Deployed `f64fc12`; dry-run clean but exposed the missing-readiness defect |
| FB-12i-R | Defect fixed; dry-run and write share one payload path (`af80fbd`) |
| **FB-12j** | **This record. Deployed `af80fbd`; dry-run verified end-to-end from the actual CLI path; TG-RDY PASS; nothing written** |
| FB-12k | Proposed: the first real snapshot write — requires its own approval |
