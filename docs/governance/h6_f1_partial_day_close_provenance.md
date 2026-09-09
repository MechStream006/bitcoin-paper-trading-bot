# H6 F1 — Partial-Day `spot_close` Provenance Record (2026-06-16 → 2026-09-09)

**Read this before treating any historical h6 `corpus_daily.spot_close` as a
completed-day close, or before computing continuity, returns or regime
statistics from the stored h6 series.**

**Status: permanent provenance record. Documentation-only — no stored
observation is rewritten, recomputed or backfilled.**

---

## 1. What the stored values actually are

For every h6 day from **2026-06-16 through 2026-09-09 inclusive**, the stored
`corpus_daily.spot_close` is the **00:05 UTC intraday tick of that day**, not
the day's close. The whole OHLCV row for the day is partial for the same
reason.

`daily_job` ran at 00:05 UTC and processed `corpus.iloc[-1]`, which at that
moment was *today, five minutes old*. `_load_corpus` derives `spot_close` as
`LAST(close ORDER BY open_time)` per UTC day, so with only ~5 one-minute bars
present that value is the 5th bar of the day. `INSERT OR IGNORE` then wrote the
row and the day entered `already`, so it was never revisited and the partial
value was frozen permanently.

The practical effect is a **systematic ~1-day lag, not random noise**:
`stored[D]` closely tracks the lake's `D-1` close.

| check (measured 2026-09-09, read-only) | result |
|---|---|
| Recent days whose stored close differs from the lake | **12 of 12** |
| Largest observed difference | **$3,892** (2026-09-03: stored 77,377.66 vs true 81,270.37) |
| Example of the lag | stored 2026-09-09 = 78,563.12 vs lake 2026-09-08 = 78,455.80 |

The interval is now closed. The F1 fix was deployed on **2026-09-09 at 11:15
UTC** (commit `002eee6`), after that day's 00:05 collection, so **2026-09-09 is
the last day stored under the old behaviour**. Every day stored from
2026-09-10 onward is a completed-day close.

## 2. Why this is a defect and not a definition

h6's frozen `collection_spec.daily_job.trigger` is *"once per day, **after daily
close is available** (00:05 UTC)"* and its first data input is *"Binance BTCUSDT
spot close (from 1m OHLCV, **last bar of day**)"*. The old behaviour satisfied
neither. `research/dq/checks.py::check_temporal` already classifies exactly this
condition as `PARTIAL_DAY` → wait; the framework has no adopters, so nothing
enforced it.

The fix therefore **restores the registered definition**. It is spec compliance,
not a change of signal semantics.

## 3. Quantified impact on the recorded evidence

Measured by recomputing each stored day against completed-day closes,
**in memory only — nothing was persisted, and no stored row was altered**:

| effect | days | detail |
|---|---|---|
| `vol_regime` differs | **3 / 83** | 2026-06-24 low→medium; 2026-08-19 low→medium; 2026-08-21 medium→high |
| `market_regime` differs | **1 / 83** | 2026-08-19 bear→sideways |
| **h6 conjunction satisfied** | **0 stored / 0 corrected** | no trade would have fired either way |

**No false signal and no suppressed signal has occurred.** One day shows the
defect is not cosmetic: on **2026-08-19** the corrected values are
`(medium, high, sideways)` — two of the three conjuncts satisfied, blocked only
by funding — against stored `(low, unknown, bear)`, which satisfies none.

Separately and **not** part of F1: `funding_regime` differs on 70 days before
2026-08-27, which is the FB-11 unevaluable window already recorded in
CLAUDE.md §21. Two further post-backfill days (2026-09-05, 2026-09-08) differ
for the funding analogue of the same 00:05 mechanism — at 00:05 a day has one
8-hourly funding event, and `_load_corpus` requires `COUNT(*) >= 2`.

## 4. What is NOT affected

- **`decision_function.py` is untouched.** `fn_hash` is `f78f0a52…` on all
  `run_log` rows, in `pre_registration.json`, and on disk.
- **Subsequent days' computations.** `compute_h6_decision` and `_compute_rv30`
  read the freshly loaded lake, not stored `corpus_daily`. Contamination is
  confined to the stored row itself and to the regime computed *on* that day,
  where the partial close is the last point of its own 273-row window.
- **The other evidence collector.** `evidence_forward.db`
  (g1/g2/g3/h5/ivrv, written by `run_daily.py`) stores true completed-day
  closes and is unaffected.

## 5. How this interval must be treated

- **Do not rewrite, recompute or backfill these observations.** They are
  recorded evidence. Correcting them would be evidence replacement, not an
  engineering fix — the same boundary CLAUDE.md §20 and §21 draw.
- **Do not interpret the stored series as completed-day closes.** Any analysis
  of h6 closes, returns or regime continuity spanning this interval must carry
  the ~1-day lag explicitly.
- Days collected **after** the fix are completed-day closes. The series
  changes convention exactly once, between 2026-09-09 and 2026-09-10, and that
  boundary is this record. Note the one-off consequence: because 2026-09-09 was
  already stored under the old behaviour, the 2026-09-10 run collects nothing,
  and from 2026-09-11 each day D is collected on D+1. No day is skipped.
- h6 has **0 trade entries and 0 exits** over the whole interval, so no h6
  outcome, label or promotion statistic depends on these values.
