# FB-10b-S — DSR/BH Implementation-Readiness Record

**Date: 2026-08-31 (UTC) · Gate FB-10b-S · READ-ONLY PREFLIGHT.**

No DSR or BH was computed. No lockbox was read or spent. No pre-registration,
`registry.yaml`, `thresholds.yaml`, `governance.py`, `ledger_sync.py`, ledger,
production host or scheduler was modified. No producer was implemented. No
deployment.

Records the three human decisions taken at this gate, the read-only preflight
against them, and the exact change surface.

---

## 1. The decisions, as given

| # | decision |
|---|---|
| **OD-4** | **D2** — `bh_p` is the Benjamini–Hochberg **adjusted q-value**, `adj_p(i) = min over k ≥ i of min(1, (m/k)·p_(k))`. Validated criterion `bh_p <= q`. Preserve `bh_fdr()`; do not silently reinterpret its fields. Any statistical-authority extension must be **additive and explicitly documented** |
| **OD-2** | **T1** — battery-level refusal. If **any** of the frozen 34 cannot produce a valid Sharpe on the frozen train slice, DSR/BH for the **entire battery** fails closed. No dropping, no substitution, no injected zeros, no change to `n_trials = 34` |
| **OD-5** | **C2** — DSR/BH goes in the **existing `forward_snapshot` row**. No competing ledger row. `ledger_sync.py` not modified. An engine/version-aware existence check **inside `forward_snapshot`** so a prior collector row is not mistaken for the DSR/BH snapshot. Existing ledger idempotency semantics preserved unless a mechanically necessary conflict is proven |

---

## 2. Preflight — contract compatibility

### 2.1 OD-4 / D2

**Compatible with the adopted contract as written. No frozen text is
contradicted.**

| check | finding |
|---|---|
| Does the contract define `bh_p`? | **No.** FDB-9 requires recording `q = 0.10` and `psr0`; FB-10a §4 fixes the surrounding metadata. Neither states the number. D2 **fills a documented silence**; it does not overwrite a frozen definition |
| FDB-9 compatibility | FDB-9 is a **minimum** list (*"must record"*), not an exclusive one. Adding fields is permitted; nothing on the list is removed or changed |
| FDB-11 compatibility | FDB-11 makes `research/evidence/governance.py` the **sole statistical authority**. Placing the new function **inside it** *satisfies* FDB-11 — putting it anywhere else would violate it. So the additive placement is not merely allowed, it is required |
| `bh_fdr()` preserved? | **Yes, byte-identical.** No caller changes; `run_dvol_family`, `run_harmonization` and `compute_dsr_for_registration` are untouched |
| Fields reinterpreted? | **No.** `p`, `rank`, `bh_threshold`, `reject` keep their existing meanings and are **cross-checked**, not redefined — see the equivalence in §2.2 |
| `thresholds.yaml` | **No change.** `validated.{edge,feature}.max_bh_p = 0.10` unchanged; `governance.fdr_q = 0.10` unchanged, and its comment *"Must match governance.bh_fdr default q parameter"* stays true because `bh_fdr`'s default is untouched |
| Pre-registrations | **No amendment mechanically required.** Their frozen text states a **threshold** (*"dsr_prob >= 0.50 AND bh_p <= 0.10 on forward trade returns"*), never the statistic's construction. D2 does not contradict any of them |
| `registry.yaml` | **No change.** No governed value moves |

### 2.2 Proof that D2 does not reinterpret `bh_fdr()`

`bh_fdr` sets `kmax` = the largest `i` with `p_(i) <= (i/m)·q`, and rejects
`i <= kmax`. For `q < 1` (here `q = 0.10`):

- **If `i <= kmax`:** take `k = kmax >= i`. Then `p_(kmax) <= (kmax/m)·q`, so
  `(m/kmax)·p_(kmax) <= q`, hence `adj_p(i) <= q`.
- **If `adj_p(i) <= q`:** some `k >= i` has `min(1, (m/k)·p_(k)) <= q`; since
  `q < 1` this means `p_(k) <= (k/m)·q`, so `k` satisfies the BH condition and
  `kmax >= k >= i`.

**Therefore `adj_p(i) <= q` ⟺ `bh_fdr(...)[name]["reject"]`, exactly.** The new
function is a *refinement* of the existing one — it reports how far inside or
outside the rejection region a family sits, without changing where that region
is. This equivalence becomes a mandatory test (§5, T-2), which is what makes
"preserve `bh_fdr()`" verifiable rather than asserted.

Ties are safe: if `p_(i) = p_(i+1)` then `adj_p(i) = adj_p(i+1)`, and `kmax`
cannot split a tie (if `p <= (i/m)q` then `p <= ((i+1)/m)q`), so no
tie-breaking convention is introduced.

### 2.3 OD-2 / T1

**Compatible. No amendment required** — FB-10b-R §2.1 established that T1's only
prerequisite was a governance statement, which §1 of this record now is.

Frozen support, unchanged: FDB-1 (*"Neither may be changed to accommodate the
other"*), `observation_floors` (*"An UNEVALUABLE floor blocks exactly as an unmet
one does"*), PR-7 (`UNKNOWN → BLOCK`), R1's NULL discipline.

**Failure condition, determined (FB-10b-R §2.0):** a member "cannot produce a
valid Sharpe" iff **n < 2 or zero dispersion**. `n >= 5` is *not* the bar here —
FDB-8 ties it explicitly to `psr`, which applies to a family's observed returns,
never to a dispersion member's Sharpe.

**Implementation consequence:** member Sharpes must be computed as a direct
`mean / std(ddof=1)`, **never** through `psr` / `_sr_stats`, which return a
silent `0.0` for `n < 5` or zero variance — an invented value with no NaN and no
exception. The refusal must be triggered by an explicit check, not left to a
downstream NaN.

### 2.4 OD-5 / C2 — proven implementable without touching `ledger_sync.py`

| check | finding |
|---|---|
| `runs.engine_version` exists? | **Yes**, verified by `PRAGMA table_info(runs)` |
| Can the engine-scoped probe live outside `ledger_sync`? | **Yes.** `forward_snapshot.write_snapshot()` currently calls `ls._existing_live_forward_run(...)`. Replacing that call with a new private probe **inside `forward_snapshot.py`** that additionally matches `engine_version = 'forward-snapshot-v1'` requires no edit to `ledger_sync.py`, and leaves the collector's own idempotency untouched. **C2 therefore does not require modifying `ledger_sync.py`** |
| Does any invariant forbid two rows sharing `(family, lane, window_end)`? | **No.** C2 tests only that the tiebreak is *total* — `ORDER BY run_ts DESC, run_id DESC` with `run_id` a uuid4 is total. C3 restricts only `run_kind='discovery'`, not `forward`. `has_live_evidence` is `SELECT DISTINCT family_id`, unaffected |
| Duplicates today? | **None** — verified: zero `(family_id, window_end)` pairs with count > 1 in the live-forward lane |
| Gating impact | **None.** Pinning is by explicit `run_id` (`registry.yaml pinned_run` → `record.get_run`), never by `current_run_per_lane` |
| Reporting impact | **Real and accepted:** the next night's collector row, carrying NULL DSR, becomes the "current" L2 run in `current_run_per_lane` and therefore in `report.py`'s lane table. Presentational only |

### 2.5 Lockbox

**No lockbox access or spending is required by any of the three decisions.** The
recomputation window is the **non-lockbox train slice** `2021-01-01 …
2025-05-31` (FDB-2a), applied by the existing
`m_full[m_full["day_ts"] < LOCKBOX_START_MS]` convention **before** positions are
computed. `run_date_policy.yaml`'s unconditional lockbox refusal governs forward
collection dates and is not engaged here. All `lockbox_status` blocks remain
untouched.

### 2.6 Production

**Untouched.** Production remains at `af80fbd`. Ledger verified read-only this
gate: **71 runs, 0 live-forward rows with a non-NULL `dsr_prob`, 27 receipts** —
identical to the FB-10b-Q and FB-10b-R readings. No deployment was performed and
none is authorized by this gate.

---

## 3. Material finding — `runs.bh_p` becomes a mixed-semantics column

**This is reported, not resolved. It does not block implementation, but it should
be acknowledged before implementation begins.**

`runs.bh_p` is **not empty**. Three offline-corpus rows already carry values,
written under the D1 (raw-p) convention by the IS path:

| run | family | lane | engine | `dsr_prob` | `bh_p` |
|---|---|---|---|---|---|
| `efbe6acb` | `ivrv_vrp_spread` | offline-corpus/discovery | `ivrv-1.0.0` | 0.1646 | 0.2893 |
| `b98c9685` | `ivrv_vrp_spread` | offline-corpus/confirmatory | `ivrv-1.0.0` | 0.1646 | 0.2893 |
| `0c7e1bf9` | `g3_atr_expansion` | offline-corpus/confirmatory | `phase_b_v2` | 0.2704 | 0.2073 |

Under D2, future L2 rows will hold an **adjusted q-value** in the same column.

**Why it does not block:**

- `check_validated_preconditions` reads `bh_p` only from the **pinned** run and
  only when `state == "validated"`, where
  `requires_non_offline_provenance: true` fires in the same function. An
  offline-corpus row can therefore never supply a D1 value to the Validated gate.
- The producer never reads or rewrites these rows; the ledger is append-only.

**Why it must be acknowledged:** `report.py` renders `bh_p` from the pinned run at
three sites (`:373`, `:444`, `:625`). Post-implementation, that column would
display a D1 value for `ivrv_vrp_spread` / `g3_atr_expansion` and a D2 value for
forward rows, under one heading, with nothing on the row saying which.

**Handling that follows directly from the instruction "do not silently
reinterpret its existing fields", offered but NOT adopted here:** leave the three
rows exactly as they are — they keep D1 semantics permanently — and require every
row this producer writes to carry an explicit `bh_p_definition` marker in its
payload. Absence of that marker then *means* "written before the definition was
fixed", the same informative-absence pattern `collection_provenance` already
uses. No migration, no rewrite, no interpretation imposed on history.

**If a stronger treatment is wanted** — a separate column, a backfilled marker on
the three rows, or a `report.py` annotation — that is a further decision and is
not taken here.

---

## 4. Exact change surface

### 4.1 Files that MUST change

| file | change | why minimal |
|---|---|---|
| `research/evidence/governance.py` | **ADD** one function, `bh_adjusted_p(pvals, ...) -> dict[str, float]`. `bh_fdr`, `psr`, `deflated_sr`, `_sr_stats` left **byte-identical** | FDB-11 requires the statistic to live in the sole authority; additive placement is the only compliant one |
| `research/forward_evidence/forward_dsr_bh.py` | **NEW.** Battery-wide computation: train-slice dispersion over the 34 (OD-1 construction), T1 refusal, FDB-5 membership, `deflated_sr`, `psr`, `bh_fdr` + `bh_adjusted_p`, and the frozen payload | FDB-11 makes DSR/BH **battery-wide**, while `forward_snapshot` is per-family — the computation cannot live inside a per-family writer |
| `research/forward_evidence/forward_snapshot.py` | (a) new **private** engine-scoped existence probe replacing the `ls._existing_live_forward_run` call; (b) accept an optional governance payload; (c) carry `metrics["forward_dsr_bh"]` through `build_snapshot_metrics` so dry-run parity holds | C2 localises the probe here; the payload must flow through `build_snapshot_metrics` because that function is contractually the **complete** payload (FB-12i-R) |

**One-source-of-truth requirement:** `governance={"dsr_prob": …, "bh_p": …}` must
be **derived at write time from `metrics["forward_dsr_bh"]`**, never computed
separately — exactly the pattern FB-12i-R established for `window_start`. A
`--dry-run` must therefore render the same bytes a write persists.

### 4.2 Documentation that MUST be added before implementation

| artifact | change |
|---|---|
| `docs/governance/fb10b_forward_dsr_bh_contract.md` | **Append-only addendum — a new `FDB-13`, recording the D2 definition, the `adj_p ≤ q` ⟺ `reject` equivalence, and the T1 refusal rule. FDB-1 … FDB-12 are NOT edited or rewritten**, following the repository's supersede-by-append convention |
| `docs/governance/forward_dsr_bh_schema_reference.md` | **NEW** — payload reference, per the per-PR precedent |

### 4.3 Files that MUST NOT change — verified unnecessary

`ledger_sync.py` (**proven** in §2.4) · `thresholds.yaml` · `registry.yaml` · all
eight pre-registrations · `lifecycle.py` · `readiness_gate.py` ·
`forward_readiness.py` · `run_daily.py` · `record.py` · `schema_forward.sql` ·
the ledger schema · every systemd unit · `run_harmonization.py` ·
`run_dvol_family.py` · `compute_dsr_for_registration.py`.

---

## 5. Mandatory tests specific to these three decisions

Beyond FB-10b-P §6's plan:

- **T-1** `bh_fdr()` is byte-identical — AST/source comparison against `HEAD`.
- **T-2** the equivalence: for randomised `pvals` and `q ∈ (0,1)`,
  `bh_adjusted_p(...)[n] <= q` equals `bh_fdr(..., q)[n]["reject"]` for **every**
  name. This is what makes "do not reinterpret" checkable.
- **T-3** monotonicity: `adj_p` is non-decreasing in rank; every value is `<= 1`.
- **T-4** ties produce equal `adj_p`.
- **T-5** T1 refusal: a synthetic 34th member with `n < 2`, and one with zero
  dispersion, each abort the **whole** battery — no row written for any family.
- **T-6** no member Sharpe is ever obtained via `psr` / `_sr_stats` — AST guard,
  since that path silently returns `0.0`.
- **T-7** `n_trials` is `34` on every written row, and the dispersion population
  size is `34`, in the refusal case as well as the success case.
- **T-8** the engine-scoped probe: a pre-existing `forward-collect-v1` row at the
  same `window_end` does **not** suppress the snapshot; a pre-existing
  `forward-snapshot-v1` row at that `window_end` **does**.
- **T-9** `ledger_sync.py` is byte-identical — AST/source comparison.
- **T-10** dry-run/write parity including the DSR payload.
- **T-11** the three legacy `bh_p` rows are unchanged by any producer run.

---

## 6. Status

The three decisions are **mechanically consistent** with the adopted contract,
with existing authoritative code, and with each other. No frozen governed value
requires amendment; the only documentation change required before implementation
is the **append-only FDB-13 addendum** (§4.2).

**One acknowledgment item is outstanding: §3, the mixed-semantics `runs.bh_p`
column.** It blocks nothing mechanically, and no handling has been adopted.

Nothing was computed, written, deployed or modified. No lockbox was accessed.

**STOP.**

---

## 7. Trail

| Gate | Outcome |
|---|---|
| FB-10b-Q | OD-1, OD-3, OD-5-core resolved mechanically |
| FB-10b-R | Final decision form: OD-4, OD-2, OD-5-collision put to a human |
| **FB-10b-S** | **This record. D2 / T1 / C2 decided; preflight clean; change surface fixed at 3 code files + 2 docs; one acknowledgment item raised** |
