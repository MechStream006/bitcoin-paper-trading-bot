# FB-1d — Code Attribution for Five `live-forward` Ledger Rows with `git_sha = NULL`

**Written: 2026-08-31 (UTC) at Gate FB-4 · Status: DOCUMENTARY RECORD**

Five rows in the canonical governance ledger carry `git_sha = NULL`. This note
supplies the attribution that column would otherwise have held.

**It changes nothing.** No ledger row is rewritten, repaired or back-filled. The
`runs` table is append-only and stays that way. This note is **documentary
only**: it is not a promotion, an assessment, a verdict, or a governance state
change of any kind, and nothing about the five families' standing follows from
it.

---

## 1. The affected rows

All five were appended at Gate FB-1d, `as_of = 2026-08-29`, `run_ts ≈
2026-08-30T15:14:59Z`, lane **L2** (`provenance='live-forward'`,
`run_kind='forward'`):

| `run_id` | `family_id` | `n_trades` |
|---|---|---|
| `91f3cd75-9225-44fb-b5fa-b18c3f06812c` | `h5_vol_regime` | 7 |
| `4422c299-9c0d-41d3-9ece-9c74964a69ac` | `g1_market_regime_sma200` | 7 |
| `aed1a8d7-9bb7-4ab8-8a12-b35e2218d204` | `g3_momentum_tsmom` | 8 |
| `8198005a-2b33-4dd9-b4e9-3dcb14800c3c` | `ivrv_vrp_spread` | 6 |
| `05cf564d-2972-4a41-8bad-50b4870ac44f` | `g3_atr_expansion` | 3 |

Each carries `engine_version = 'forward-collect-v1'` and `n_trials = 34`.
Verified directly against the canonical ledger at this gate: exactly five
`live-forward` rows have `git_sha IS NULL`, and they are these five.

## 2. What produced them

`research/forward_evidence/ledger_sync.py`, from commit
**`4cf0923ccdb1ca53d80459e02c88cad3ee17482d`**.

| Property | Value |
|---|---|
| Size | **19,440 bytes** |
| SHA-256 of file bytes | **`63ee4d2f909ea59db0320a8f07ffd6ffd3ce794fb0063ebd78865d5f301af72e`** |
| Git blob SHA-1 | **`0ae442c7383be67cdce040cc44460dd1b87a48ed`** |

Both digests were computed at FB-1d **on the developer workstation and again on
the production host** after staging, and the git blob SHA-1 was independently
reconstructed (`sha1("blob <len>\0" + bytes)`) rather than taken from `git` on
trust. All three agreed. Re-verified at this gate against
`git cat-file blob 4cf0923:research/forward_evidence/ledger_sync.py`.

The same bytes are now the deployed module: the served
`/opt/btcbot/research/forward_evidence/ledger_sync.py` hashes to `63ee4d2f…`.

## 3. How it was executed — the authorized M4 mechanism

At FB-1d, `4cf0923` was **not deployed** (production was at `a72a064`, and that
gate explicitly forbade deployment). The append was performed under the
mechanism authorized as **M4**:

1. The exact committed blob was extracted with `git cat-file blob` (no
   checkout filters) and staged at `/tmp/fb1d/forward_evidence/ledger_sync.py`
   — **outside** `/opt/btcbot`.
2. Its SHA-256 and git blob SHA-1 were verified on the host before use.
3. A driver imported it with `sys.path` pointed at the real
   `/opt/btcbot/research/evidence_library`, pre-importing `record` so the module
   resolved the genuine production dependency, and called
   `ledger_sync.sync_all()` with explicit production database paths.
4. The rows were appended **in place** to the canonical ledger. The ledger file
   was never copied, swapped or replaced.
5. `/tmp/fb1d` was removed afterwards and confirmed absent — it remains absent.

Throughout, the served tree was untouched: production stayed at `a72a064` with a
clean working tree, and no unit file, service or timer was changed.

## 4. Why `git_sha` was NULL

`ledger_sync._git_sha()` (lines 390-400) runs:

```python
out = subprocess.run(
    ["git", "rev-parse", "--short", "HEAD"],
    cwd=str(_RESEARCH_ROOT),
    capture_output=True, text=True, timeout=5,
)
return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None
```

`_RESEARCH_ROOT` is derived from the module's **own file location**
(`_HERE = Path(__file__).resolve().parent; _RESEARCH_ROOT = _HERE.parent`).
Under M4 the module lived at `/tmp/fb1d/forward_evidence/`, so `_RESEARCH_ROOT`
resolved to `/tmp/fb1d` — not a git repository. `git rev-parse` therefore
returned non-zero, and the function returned `None`, exactly as its docstring
specifies ("or None outside a repo").

**This is not a defect in the bridge.** It is the documented behaviour of
`_git_sha()` when the module is executed from outside a repository, which is
what M4 required. The function failed safe: it recorded nothing rather than
recording something wrong.

## 5. Contrast: the FB-2 rows

The next five L2 rows (`as_of = 2026-08-30`), appended automatically by the
**deployed** module during the scheduled 2026-08-31 01:00 UTC run, carry
`git_sha = 4cf0923`. Same code, same bytes; the only difference is that it ran
from inside `/opt/btcbot`, where `git rev-parse` succeeds.

The two slices therefore differ in exactly one column, for a reason that is
fully explained by the execution mechanism and by nothing else.

## 6. Scope — what this does and does not affect

**All 71 `offline-corpus` runs also carry `git_sha = NULL`.** No row in the
ledger carried a `git_sha` before the FB-2 slice. The FB-1d rows are consistent
with the entire pre-existing corpus; they are not an anomaly within it, and the
FB-2 slice is the first time the column has ever been populated.

The affected rows remain **fully attributed on every other axis**, directly in
the ledger: `fn_hash` (tying each row to the exact decision-function bytes that
produced its observations), `engine_version`, `n_trials`, `window_start` /
`window_end`, and a `metrics_json` carrying `closed_trades_only: true` and
`source: "evidence_forward.db:trade_returns"`.

Gate FB-1e independently re-derived every metric on these five rows from
`evidence_forward.db`'s `trade_returns` view — without importing `ledger_sync` —
and matched the stored values to < 1e-9 on Sharpe and < 1e-12 on the aggregates.
Their content is verified; only the code-identity column is absent, and this
note supplies it.

## 7. Status

- **Permanent and accepted.** The rows are append-only; there is no UPDATE path
  on `runs`, and none should be created for this.
- **Not a repair.** Nothing is rewritten, re-synced or re-appended. Re-running
  `ledger_sync` for `as_of = 2026-08-29` would be a no-op by design (the
  idempotency key is `(family_id, provenance, window_end)`), so the NULL cannot
  be "corrected" by re-execution either — which is the correct behaviour for an
  append-only ledger.
- **Not evidence of anything about the signals.** These five rows record
  forward observations with `dsr_prob`, `bh_p`, `oos_sharpe`, `evidence_score`
  and `verdict` all NULL. No gate opens on them, and no statement about any
  family's merit is made or implied here.

## 8. Trail

| Gate | Outcome |
|---|---|
| FB-1b | Bridge implemented and committed as `4cf0923`; dry-run on copies only |
| FB-1c | Preflight: the five rows computed and verified against production copies |
| **FB-1d** | **First canonical append, via M4. `git_sha` NULL on all five rows** |
| FB-1e | Independent audit: content verified from source; NULL not yet characterised |
| Deployment | `4cf0923` deployed to production (HEAD `a72a064` → `4cf0923`) |
| FB-2 | First automated append; those rows carry `git_sha = 4cf0923` |
| FB-3 | Continuity audit surfaced the NULL and identified its cause |
| **FB-4** | **This note. Attribution recorded; no row modified** |
