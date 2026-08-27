# Mutanabby_AI Ultimate Algo Remastered+ — M-6 Decision Record

**Date:** 2026-08-27 · **Decision: DECLINE** · **Status: track closed, port retained**

Gates M-1 → M-6. This record exists so that what was proven, and what was
deliberately *not* proven, cannot be confused later.

---

## Decision

**DECLINE.** The Mutanabby Pine→Python reference port is retained and committed
as a verified artifact. The BTC research platform is **not** expanded to a 4H
evidence basis for this strategy. Mutanabby is classified
**verified-but-unevaluable on the current daily basis**.

---

## What IS proven

**Pine→Python equivalence, on the frozen 4H fixture.** Gate M-4, 5,813 bars of
`BINANCE:BTCUSDT` 4H, 2024-01-01T00:00Z → 2026-08-26T16:00Z:

| | |
|---|---|
| Real-valued intermediates | worst deviation **5.093e-10** (tolerance 1e-9) across `tr`, `atr`, `upperBasic/lowerBasic`, `upperBand/lowerBand`, `supertrend`, all seven SMA series |
| Discrete outputs | `direction`, `bull`, `bear`, `strength` — **bit-exact, 0 mismatches** |
| Signal bars | `bull` 51 = 51, `bear` 50 = 50, **index sets identical**; strength histogram identical (`{-2:8, -1:42, 0:5712, +1:42, +2:9}`) |
| Warm-up | rows 0–33 asserted explicitly; Wilder RMA seeding verified (`mean(tr[0:11]) == atr[10]`) |
| Leakage | prefix-invariance at 7 cut points; future-mutation isolation; past-mutation propagation |
| Adversarial | **22/22 mutations caught**, port restored byte-exact |

Artifact hashes:

| Artifact | SHA-256 |
|---|---|
| Pine source (authoritative, unmodified) | `75f1914a2f3e982cfc36eeb98edc142b494dcce2745e0707865389e77acc379d` |
| M-3 fixture (Pine Logs CSV, immutable) | `33825fbe7a10e5a84289fdd1da64c207d6d610c905d295dac7c2738d8cefe0cc` |
| `research/reference/mutanabby/pine_port.py` | `de0b69afffc0e2ff32f0913b00b2d13df36f6333be10ab9c652ed87a0395402b` |

Both source files live outside the repository (`D:\`) and were never modified.

---

## What is NOT proven

**No performance or edge claim is made, and none is supported.**

- **Performance / edge: NOT PROVEN.** No backtest result is asserted. No Sharpe,
  DSR, BH-p, drawdown or return figure from any M-gate may be cited as evidence.
- **Independence: NOT PROVEN.** Gate M-5 measured max |r| = 0.6369 (vs
  `trend_regime(20/100)`) and 0.5890 (vs `g3_momentum_tsmom`) under the
  source-derived reverse-on-opposite convention, with VIF R² = 0.586. Those
  numbers rest on **n = 27** and carry no inferential weight.
- **Incremental information: NOT PROVEN, in either direction.** M-5's three
  pre-declared execution conventions **contradicted each other** — gating added
  information on 4/7 comparators under one convention and 0/7 under another,
  with standalone Sharpe spanning 0.76 across cells. A single arbitrary
  convention would have produced a confident and unfounded answer.

---

## Why: the daily evidence basis is insufficient

```
DAILY:  32 signals / 5.58 yrs =  5.7 per year  ->  5.2 yrs to the 30-trade floor
4H   : 101 signals / 2.65 yrs = 38.1 per year  ->  0.8 yrs
       4H fires 6.6x more often than daily

TRAIN-SLICE DAILY SIGNALS: 27      (project evidence floor: 30 trades)
```

**27 < 30.** This is *insufficient evidence*, **not** a rejection — the same
discipline the h6 pre-registration states in its own words: record as
"insufficient evidence", never as "disproven".

The limitation is **structural**: the strategy's own documented timeframes are
15m–4H, and the entire BTC research library is daily. That mismatch, not the
strategy's merit, is what blocks evaluation.

---

## Why DECLINE rather than ACCEPT or NARROW

**ACCEPT (build a 4H lane)** was rejected as a platform-scale change justified by
a single strategy:

- `86400000` day-bucketing appears 4× in `engine.load_master`; `ANN = 365.0` and
  `ret_fwd1` are daily; **38** `sig_*` comparators are written against the daily master.
- **Two comparators cannot be reproduced at 4H at all.** DVOL is daily-only
  (`ingest/dvol.py RESOLUTION = "1D"`; Deribit offers 1/60/3600/43200/1D — no 4H),
  so `ivrv_vrp_spread` is unreproducible. Funding is 8-hourly, so
  `g2_funding_level` would update every *other* 4H bar — a different signal.
- The forward pipeline is daily by construction: `_derive_run_date()` returns
  yesterday UTC, `run_signal` prices on daily closes, the timer is 01:00 UTC.
- It would reopen **two frozen constraints**: `thresholds.yaml` (frozen
  2026-06-13; `min_active_days: 60` and `min_calendar_years: 2` are
  day-denominated and undefined on 4H) and **`n_trials = 34`**, since a 4H
  battery is a new multiple-testing surface.

**NARROW (standalone 4H, no comparison)** was rejected as *inadmissible under the
project's own rules*, not merely expensive: the candidate gate requires
`max_clone_r` and Validated requires `max_pairwise_r = 0.70`. Both are
**comparative**; on a basis with no comparators they are undefined, so any
resulting figure could never enter the registry. Gate 6A.2 already demonstrated
the failure mode this cannot detect — a distinct-looking trend signal that
degraded 4 of 4 comparators in combination.

---

## Reopening trigger

Reconsider **only** if a 4H BTC research lane is established for an
**independent** reason — never to rescue this strategy alone. If that happens:

1. Re-run **Gate M-5**'s overlap/incremental screen on a like-for-like 4H
   comparator basis.
2. No new port work is required — the port is verified and committed.
3. Treat reopening `thresholds.yaml` and `n_trials = 34` as explicit governance
   decisions, not incidental consequences.

One fact lowers the future cost materially: **the 4H data is already derivable.**
1m spot spans 2021-01 → 2026-07 (67 partitions); a 4H master needs only
`// 14400000` in place of `// 86400000`. The data is not the blocker — the
evidence framework is.

---

## Governance status

| | |
|---|---|
| Registry entry | **None.** Mutanabby is not a registered family. Creating one would assert evidence that does not exist |
| `registry.yaml` / `thresholds.yaml` / `ledger.db` | **Unmodified** |
| `n_trials = 34` | **Unspent, unchanged** |
| Lockbox (2025-06-01 .. 2026-05-31) | **Untouched.** No M-gate accessed it |
| Forward collection | **Not added.** Absent from `collection_config.yaml` |
| Execution / production | **No path.** Not wired to any runtime |

`research/reference/mutanabby/` is a research reference implementation only.

---

## Trail

| Gate | Outcome |
|---|---|
| M-1 | Pine source not retrievable via fetch — reconstruction refused |
| M-2 | Source supplied; frozen specification; D1–D5 resolved; three description errors caught (SMA naming, `close` vs `hl2`, inverted strength polarity) |
| M-3 | Frozen fixture captured via Pine Logs (chart-export unavailable on the plan); zero plot-count exporter |
| M-4 | **Equivalence PASS** — 5,813 bars, 22/22 mutations caught |
| M-5 | Entry semantics frozen; **no exit rule exists in the source**; 3-cell convention matrix; overlap screen inconclusive at n=27 |
| M-6 | **DECLINE** |
| M-7 | Port committed; this record written |
