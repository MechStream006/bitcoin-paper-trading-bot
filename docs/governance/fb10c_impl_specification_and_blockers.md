# FB-10c-impl — Pairwise Independence Producer: Specification and Blockers

**Date: 2026-08-31 (UTC) · Gate FB-10c-impl · Outcome: STOPPED at the
specification boundary. Three of five open items resolved from frozen
evidence; two remain genuinely unresolved and are material.**

No producer was implemented. No schema was changed. No measurement was
computed or recorded. `pairwise_r` remains at 0 rows. Production untouched.

All state below was verified read-only against the **production** host
(`/opt/btcbot`), never the local scaffold, at `as_of = 2026-08-30`.

---

## 1. Production state as verified

| Fact | Value |
|---|---|
| `pairwise_r` | **0 rows**; columns `(id, ts, family_id_a, family_id_b, r, method, actor)`; `CHECK (a < b)`; `RAISE(ABORT)` triggers on UPDATE/DELETE |
| Ledger runs | 81 total: 10 `live-forward/forward`, 36 `offline-corpus/confirmatory`, 35 `offline-corpus/discovery` |
| State transitions | 9 to `candidate`, 4 to `disqualified`, **0 to `validated`** |
| Registry | 35 families |
| Collecting forward signals | **7** |
| Forward closed trades (`exit_date <= as_of`) | `g3_momentum_tsmom` 8, `g1_market_regime_sma200` 7, `h5_vol_regime` 7, `ivrv_vrp_spread` 6, `g3_atr_expansion` 3, `g1_trend_regime_ma_cross` **0**, `g2_funding_level` **0** |

The forward corpus stores prices only at trade boundaries
(`entry_close_price`, `exit_close_price`). It holds **no daily price or daily
return series**. Daily spot reaches the collector only as hash-pinned
`spot/BTCUSDT` parquet partitions recorded in `run_inputs`.

---

## 2. RESOLVED from frozen evidence

### R-1 — Return basis: FORWARD TRADE RETURNS (resolves FB-10c-R O-1, in part)

Every pre-registration that declares a forward return basis declares the same
one, in the same words:

| family | declared basis |
|---|---|
| `h5_vol_regime`, `g1_market_regime_sma200`, `g3_tsmom`, `g3_atr_expansion` | "dsr_prob ... on **forward trade returns**" |
| `g1_trend_regime`, `g2_funding_level` | "mean **forward trade return** > 0 net of 15 bps" |
| `h6` | "DSR on **trade returns** (non-overlapping 7d)" |

and the unit is defined identically across families: *"each 7-day evaluation
window is one trade; trades are sequential and non-overlapping."*

The measurement surface is therefore `trade_returns.net_return`, filtered
`exit_date <= as_of` — the same surface `ledger_sync`, FB-7 observation floors
and the FB-10a DSR path already use.

**`pearson_daily_returns` is rejected as the basis.** It appears in
`pairwise_independence_schema_reference.md` §6/§7 and in test fixtures, but it
is an **illustrative example string** written with the schema reference, not
part of the frozen spec text (CLAUDE.md §16 step 6 says only "compute
correlation of its returns"). A daily-return basis would additionally require
the engine's `_net_daily(position, ret_fwd1, cost_bps)` stream, which is
position-derived — the substitution FB-10c-R rejected — and is not computable
from the forward corpus at all.

### R-2 — Point-in-time rule (resolves O-3)

`exit_date <= as_of`, structurally. Frozen practice:
`ledger_sync.load_closed_trades()`, FB-9's snapshot producer, and FB-10a-R §4
("every return must satisfy `exit_date <= as_of`"). Open trades are absent
from the `trade_returns` view by construction — no exit row, no join — so the
filter is enforced by the schema rather than by a caller remembering it.

### R-3 — Production timing (resolves O-4)

**Pre-emptive, operator-invoked, not gated on a Validated peer existing.**
Three pre-registrations independently instruct measurement *"before any
Validated promotion attempt"* (`h5_vol_regime`, `g1_market_regime_sma200`,
`g3_momentum_tsmom`), at the quarterly snapshot or 12-month mark; schema
reference §6 additionally requires coverage of **every** Validated peer at
promotion. Both are satisfied by a producer that measures operator-named pairs
on demand and never self-triggers.

### R-4 — Cluster-head rule is not producer scope (resolves O-5)

`g3_momentum_tsmom`'s cluster-head priority over `g5_trend_4h` is a
**promotion-order judgment**, and the producer never promotes. It is
additionally moot today: `g5_trend_4h` is a registered `candidate`
(role=feature) that is **not collected forward at all**.

The related PE-4 finding stands unchanged — `g1_market_regime_sma200`
references an *"independence gate override"* that exists nowhere in code or
thresholds. Still a gap; still not invented here.

---

## 3. BLOCKER B-1 — Alignment of misaligned 7-day windows is not frozen

Every family trades a 7-day non-overlapping window, but **each family runs its
own weekday grid**, and nothing frozen says how to pair two such grids.

Observed entry weekdays: `g1_market_regime_sma200` and `g3_momentum_tsmom`
enter Mondays; `h5_vol_regime` enters Tuesdays; `ivrv_vrp_spread` is mixed;
`g3_atr_expansion` is sparse. Exact-date pairing therefore yields:

| pair | aligned observations |
|---|---|
| `g1_market_regime_sma200` x `g3_momentum_tsmom` | 7 |
| `g3_momentum_tsmom` x `ivrv_vrp_spread` | 4 |
| `h5_vol_regime` x `ivrv_vrp_spread` | 4 |
| `g1_market_regime_sma200` x `ivrv_vrp_spread` | 3 |
| `g3_momentum_tsmom` x `h5_vol_regime` | 3 |
| `g1_market_regime_sma200` x `h5_vol_regime` | 2 |
| `g3_atr_expansion` x `ivrv_vrp_spread` | 1 |

Two candidate rules exist and neither is frozen:

- **Exact-date pairing** keeps only coincident trades: n collapses to 1-7,
  discarding most of the evidence.
- **Common-grid reindex with `fillna(0.0)`** — the convention in the named
  methodology `analyze_mtf_independence.py` Test 1 (`_arr`). Applied to
  *positions* this is sound (absent = flat). Applied to **misaligned weekly
  realized returns it manufactures independence**: two families whose trades
  land on different weekdays each contribute zeros where the other is nonzero,
  driving Pearson r toward 0 regardless of true economic overlap.

**That second failure mode is why this is a stop and not a judgment call.** It
biases toward r ≈ 0, which *passes* a ceiling of 0.70. A gate satisfiable by
cadence misalignment is the mirror image of the "satisfiable by volunteering
nothing" failure that schema reference §4.1 was written to close.

Aggravating fact: cadence is not stable even within a family. Per CLAUDE.md
§20, the G3 entry anchor shifted **Monday to Thursday** across the INC-0005
outage, so any alignment rule must also state how it treats a cadence
discontinuity inside its own window.

**Decision required:** the alignment rule for non-coincident 7-day trade
windows, and its treatment of the INC-0005 cadence shift.

---

## 4. BLOCKER B-2 — Minimum n is not frozen, and the check is n-blind

No frozen artifact states a minimum sample size for a pairwise correlation.
The named methodology guards only `std > 0`. The three minima that *are*
frozen are all something else, and FR-5 (FB-10a-R) forbids conflating them:

| minimum | value | kind | applies to |
|---|---|---|---|
| computational guard | n >= 5 | mathematical | DSR/PSR only |
| statistical sufficiency | n >= 10 / 15 | governance | DSR/BH only |
| observation floor | 26-30 trades | governance | FB-7 admissibility |

Under the resolved basis, achievable n today is **1 to 7**. A Pearson r on
n = 2 is not a measurement, and `check_validated_independence` **never reads
n** — it sees only `r`. A low-n r would therefore silently satisfy the ceiling
and, under §4.1's fail-closed-on-missing rule, would also *discharge* the
coverage obligation it cannot honestly meet.

**Decision required:** the minimum n at which a pairwise measurement is
admissible as governance evidence, and whether the check must enforce it.

---

## 5. Structural finding — every pre-registered concern is unmeasurable today

Independent of B-1 and B-2, the producer could not today produce a single
measurement any pre-registration actually asks for:

| pre-registered concern | counterparty status | measurable? |
|---|---|---|
| `h5_vol_regime` x `h6` | `h6` legacy corpus: 75 signal rows, **0 closed trades** | **No** |
| `g1_market_regime_sma200` x `h6` | same | **No** |
| `g3_momentum_tsmom` x `g5_trend_4h` | `g5_trend_4h` **not collected forward** | **No** |
| `g1_market_regime_sma200` x `g1_trend_regime_ma_cross` | `g1_trend_regime_ma_cross`: **0 closed trades** | **No** |

Closing this is a **capture-path expansion** — adding `h6` and `g5_trend_4h`
to forward collection — which is gated and explicitly out of scope here.

---

## 6. Schema: extension confirmed necessary, deliberately not applied

The contract in §2 **cannot** be honestly represented in the existing table.
`ts` is the recording time and must not be overloaded as `as_of`; nothing
records the window, the sample size, or which return basis was used.

Minimum additive extension, ready to apply once B-1 and B-2 are closed:

```sql
ALTER TABLE pairwise_r ADD COLUMN as_of        TEXT;
ALTER TABLE pairwise_r ADD COLUMN window_start TEXT;
ALTER TABLE pairwise_r ADD COLUMN window_end   TEXT;
ALTER TABLE pairwise_r ADD COLUMN n            INTEGER;
ALTER TABLE pairwise_r ADD COLUMN return_basis TEXT;
```

Additive and nullable, so existing readers (`get_pairwise_r`,
`check_validated_independence`) keep working unchanged; the table has 0 rows,
so there is no migration burden and no `CHECK`-on-ALTER asymmetry to manage
(the PR-4 hazard in CLAUDE.md §17 applies only to populated tables).

**Not applied at this gate.** `return_basis`'s permitted vocabulary and any
`n` floor are precisely what B-1 and B-2 leave undetermined; applying the
columns now would either bake in a guess or ship a column nothing can fill.

---

## 7. What is already fully specified, for whoever resumes

Frozen and not in question: estimator **Pearson**; guard **both std > 0**;
comparison on **|r|**; canonical ordering **a < b** with refusal, never silent
sorting; append-only; ceiling `max_pairwise_r = 0.70` with `r == 0.70`
passing; latest-per-pair with `(ts, id)` tiebreak; `as_of`-honouring replay.

Producer properties required by this gate and unchanged by the stop:
operator-invoked with a named actor and service actors refused (as
`forward_snapshot.py` already does); battery-independent; never imported by
`run_daily.py`; never admits, pins, promotes, or writes a transition or
receipt; computes only operator-named pairs; fails closed on insufficient or
invalid data.

---

## 8. Decision

1. **R-1 through R-4 are resolved** from frozen evidence and may be treated as
   settled: basis = forward trade returns; `exit_date <= as_of`; pre-emptive
   operator-invoked production; cluster-head is not producer scope.
2. **B-1 (alignment) and B-2 (minimum n) are unresolved**, material, and
   require a governance decision. Neither was guessed.
3. **No producer, no schema change, no measurement, no ledger write.**
4. Even with B-1 and B-2 closed, **no pre-registered pair is measurable**
   until `h6` and `g5_trend_4h` have forward evidence (§5).

---

## 9. Trail

| Gate | Outcome |
|---|---|
| FB-10c | Read-only audit; found the basis conflict and the zero-peer vacuity |
| FB-10c-R | Basis RESOLVED to forward returns; O-1..O-6 left open |
| **FB-10c-impl** | **This record. R-1..R-4 resolved; STOPPED on B-1 and B-2** |
| FB-10c-impl (resumed) | Blocked pending B-1 and B-2 |
