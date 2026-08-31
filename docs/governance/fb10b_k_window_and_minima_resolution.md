# FB-10b-K — FDB-2a (window) and FDB-8 (four minima)

**Date: 2026-08-31 (UTC) · Gate FB-10b-K · Read-only · No computation, no
lockbox read/consumed/spent, metadata inspection only.**

**FDB-2a: RESOLVED — window A (train slice `2021-01-01 … 2025-05-31`).** Its
window is itself frozen and stated; window B would contradict a frozen
per-family commitment and is available only by an explicit override that spends
two lockboxes irreversibly.

**FDB-8: HUMAN DECISION REQUIRED for all four families.** No frozen evidence
supplies a usable DSR minimum for any of them, and no value is derivable.

---

## 0. Already settled — restated, not reopened

| item | value |
|---|---|
| **U1** | `sr_trials_std` population = the **34 Phase B library** Sharpes |
| **U2-B** | forward/trade-return **re-measurement of those same 34** |
| **`n_trials`** | **34** |
| **Forward basis** | 7-day **non-overlapping trade returns** (`trade_returns.net_return`) |
| **U3** | explicit battery membership, per the previously selected rule; realized membership frozen in every result |
| **`n >= 5`** | **computability floor only** — never sufficiency |

---

## 1. FDB-2a — recomputation window

### Window A — training / non-lockbox portion

| question | answer |
|---|---|
| **Frozen evidence supporting it** | The train slice is **itself frozen and repeatedly stated**: *"train slice only (1612 rows, **2021-01-01..2025-05-31**)"*; *"train slice 2021-01-01..2025-05-31 (lockbox 2025-06-01..2026-05-31 **SEALED**)"*; `g2_funding_level`: *"All gating numbers in evidence_basis are **train-slice** (2021-01-01..2025-05-31)"*; CLAUDE.md §16: *"on the **lockbox-disciplined train slice** (2021-01..2025-05)"*. **No new value is required — the window is already governance-frozen.** |
| **All 34 re-measurable at `hold_days=7`?** | **Yes.** `engine._apply_hold(raw_pos, hold_days)` is parameterised; every library signal is a daily-position signal. The train slice is ~4.4 years, ample for 7-day non-overlapping blocks |
| **Used for gating?** | Yes — `sr_trials_std` sets `sr_star`, which gates DSR at the Validated bar |
| **Spends any unspent lockbox?** | **No.** No lockbox-window data enters the computation |
| **Lockboxes affected** | **None** |
| **Authorization required?** | **None** |

### Window B — full Phase B corpus `2021-01-01 … 2026-05-31`

| question | answer |
|---|---|
| **Frozen evidence supporting it** | **None found.** The only support is that `evidence_library.json`'s stored Sharpes were computed over this window — but U2-B already supersedes that file's basis by re-measuring at `hold_days=7`, so it cannot also be cited to fix the window |
| **Frozen evidence rejecting it** | `g2_funding_level`, verbatim: *"The pinned battery run's full-sample Sharpe **spans the lockbox and is recorded for transparency only, never used for gating**."* Under B, *Funding Level*'s lockbox-spanning Sharpe becomes an **input to a gating benchmark** — precisely the use its own pre-registration forecloses. Reinforced by CLAUDE.md §16: *"**do not spend it for interim gating**"*, and `ivrv`: *"must remain unspent for interim gating"* |
| **All 34 re-measurable at `hold_days=7`?** | Yes — technically identical to A |
| **Used for gating?** | Yes — identically |
| **Spends any unspent lockbox?** | **Yes.** Per FB-10b-J's resolved rule (existence ≠ spending; **gating use = spending**) |
| **Lockboxes affected** | Exactly **two** of the 34: **"Trend Regime (MA cross)"** (= `g1_trend_regime_ma_cross`) and **"Funding Level"** (= `g2_funding_level`). Both currently `spent: false`. *(`ivrv` also holds an unspent lockbox but is **not** one of the 34, so it is unaffected.)* |
| **Authorization required?** | **Yes** — an explicit authorization to spend both, plus amendment of both `lockbox_status` records to `spent: true`. Additionally: both are themselves forward families awaiting validation, so their own sealed OOS data would enter the benchmark that later judges them |

### Determination

**Frozen evidence uniquely selects A.** A's window is already frozen, requires
no new value, and violates nothing. B is supported by no frozen statement and
**contradicts one** (`g2_funding_level`'s own commitment). B therefore cannot be
adopted silently; it is reachable only as a deliberate human override that
spends two lockboxes permanently.

**FDB-2a status: RESOLVED — A.** *(A human may still elect B; that would be an
override of frozen evidence requiring the authorization above, not a selection
frozen evidence permits.)*

---

## 2. FDB-8 — the four families

### Per-family findings

| family | own pre-reg supplies a DSR minimum? | its own frozen `minimum_trades` | may 10 / 15 be borrowed? | does 26 / 30 have a frozen basis **as a DSR minimum**? |
|---|---|---|---|---|
| `ivrv_vrp_spread` | **No** — no DSR-timing statement anywhere | **30** | **No** — FR-5 | **No.** 30 is frozen for it as an **observation floor**, a different minimum FR-5 says not to conflate |
| `g2_funding_level` | **No** — its `G2_governance_stats` fixes the *thresholds* and denominator (*"computed at the FROZEN n_trials=34 on forward returns"*) but never a sample size; `dsr_prob_note`: *"NOT COMPUTED … none is fabricated here"* | **30** | **No** | **No** — same distinction |
| `g1_trend_regime_ma_cross` | **No** — same `G2_governance_stats` pattern, no sample size | **30** | **No** | **No** — same distinction |
| `g3_atr_expansion` | **No** — no DSR-timing statement anywhere | **26** | **No** | **No** — same distinction |

**Key nuance:** each family *does* carry a frozen per-family number (30/30/30/26)
— but frozen as an **observation floor**, not a DSR compute-minimum. Adopting it
would therefore **not require inventing a number**, yet would still be a
*decision to unify two minima that FR-5 explicitly distinguishes*. It is a
governance act, never a derivation.

### The available treatments

| | treatment | permissible? | amendment required |
|---|---|---|---|
| **8-i** | per-family DSR minimum set by **pre-registration amendment** | yes | 4 amendments, each re-hashing under the A2a convention and requiring re-registration |
| **8-ii** | one **new common constant** in a governance record, applying only to families stating none | yes | new frozen constant; creates a second source of truth for a per-family parameter |
| **8-iii** | **unify with each family's own `minimum_trades`** (30/30/30/26) | yes | governance statement that the observation floor also governs DSR where no DSR minimum is stated; deliberately conflates two minima FR-5 separates |
| **8-iv** | **permanent exclusion** — leave unamended | yes; **not required** | record the exclusion explicitly so it is a decision, not an omission |

**No value is derivable.** `n >= 5` remains solely the computability guard;
observation floors remain distinct from DSR minima; existing 10/15 stay
family-specific and non-transferable; and no value may be taken from current
sample sizes.

---

## 3. FINAL DECISION MATRIX

| Decision | Status | Frozen evidence | Remaining human choice | Irreversible consequence |
|---|---|---|---|---|
| **FDB-2a** | **RESOLVED — window A** (`2021-01-01 … 2025-05-31`) | train slice frozen and stated in ≥4 artifacts; `g2_funding_level` *"never used for gating"*; CLAUDE.md *"do not spend it for interim gating"* | accept A (no action), **or** override to B | **A: none.** **B: permanently spends two unspent lockboxes** (*Trend Regime (MA cross)*, *Funding Level*), and their sealed OOS data enters the benchmark that later judges them |
| **FDB-8 — `ivrv_vrp_spread`** | **HUMAN DECISION REQUIRED** | no DSR minimum; own floor 30 (observation floor only); FR-5 bars borrowing | 8-i / 8-ii / 8-iii / 8-iv | chosen value permanently gates every future DSR for this family; 8-iv permanently excludes it from Validated |
| **FDB-8 — `g2_funding_level`** | **HUMAN DECISION REQUIRED** | as above; `dsr_prob_note`: *"NOT COMPUTED … none is fabricated here"* | 8-i / 8-ii / 8-iii / 8-iv | as above |
| **FDB-8 — `g1_trend_regime_ma_cross`** | **HUMAN DECISION REQUIRED** | as above | 8-i / 8-ii / 8-iii / 8-iv | as above |
| **FDB-8 — `g3_atr_expansion`** | **HUMAN DECISION REQUIRED** | as above; own floor is **26**, not 30 | 8-i / 8-ii / 8-iii / 8-iv | as above |

### Restated as settled

U1 = the 34 Phase B library Sharpe population · U2-B = forward/trade-return
re-measurement of those same 34 · `n_trials = 34` · forward basis = 7-day
non-overlapping trade returns · U3 = explicit battery membership under the
previously selected rule · `n >= 5` = computability floor only.

---

## 4. Status

FB-10b implementation remains **not authorized**. One decision remains open
(FDB-8, across four families), plus an optional override on FDB-2a. Nothing was
computed, no lockbox was read or consumed, and no value was selected by
inference, preference or convenience.

**STOP.**

---

## 5. Trail

| Gate | Outcome |
|---|---|
| FB-10b-I | FDB-1…FDB-12 drafted; three values outstanding |
| FB-10b-J | FDB-2 interpretation and the lockbox rule RESOLVED |
| **FB-10b-K** | **This record. FDB-2a RESOLVED to the train slice; FDB-8 open for all four families** |
