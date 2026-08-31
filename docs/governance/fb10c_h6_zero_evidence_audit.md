# FB-10c-H6 — h6 Zero-Evidence / Dormant-Path Audit

**Date: 2026-08-31 (UTC) · Gate FB-10c-H6 · Read-only · Classification:
COMBINATION — B (upstream evidence dependency missing) for 70 of 75 collected
days, A (legitimate zero-evidence) for the 5 days since, plus a minor C
recording defect that does not affect decisions.**

No modification to h6, funding capture, the collector, registry,
pre-registration, thresholds, scheduler, ledger, or production. No trades,
labels or evidence manufactured. No pairwise work.

---

## 1. Two corrections to FB-10c-T

**Correction 1 — the dependency was misattributed.** FB-10c-T stated that h6's
`funding_regime = unknown` *"traces to the dormant funding capture path
documented and gated in CLAUDE.md §11."* **That is wrong.** §11's dormant path
is the **live Delta** `funding_snapshots` table in the trading database. h6
does not use it. h6's funding comes from the **Binance research parquet lake**
(`data/raw/funding/BTCUSDT`, ingested by `daily_ingest.py`), a different
dependency entirely. The §11 gate is not h6's blocker and never was.

**Correction 2 — h6 is no longer unable to fire.** FB-10c-T concluded h6
*"structurally cannot fire."* That was true when written of the *observed
history*, but not of the *current configuration*: since **2026-08-27** h6 has
been fully evaluable. It is flat because the funding regime is `high` and h6
requires `low` — a real market condition, not a computation failure.

Both corrections improve the pairwise outlook and are stated before the
evidence rather than buried in it.

---

## 2. The frozen definition and its inputs

`decision_function.py`, pre-registered 2026-06-10, `VERSION = "1.0.0"`:

```
long (1) when vol_regime == medium AND funding_regime == low
          AND market_regime not in {bear, unknown}
flat (0) otherwise
```

| input | source | frozen guard |
|---|---|---|
| `vol_regime` | 21d realised vol vs 252d terciles, from spot close | `unknown` if the 252d window is incomplete |
| `funding_regime` | Binance USDT-perp daily mean funding x 10,000, 252d terciles | **`unknown` if fewer than `REGIME_WINDOW = 252` valid values** |
| `market_regime` | 200d SMA + 20d slope | `unknown` if the SMA window is incomplete |

The decision window is `corpus.iloc[idx - MIN_HISTORY + 1 : idx + 1]`, at most
**273 rows** (`MIN_HISTORY = 252 + 21`). So `funding_regime` is `unknown`
whenever the trailing 273-day window holds fewer than 252 valid funding days.

---

## 3. Root cause: the funding lake was not backfilled until 2026-08-26

Partition write times on production (`find -printf`):

| partitions | written |
|---|---|
| `year=2026/month=06` | 2026-07-01 00:06 |
| `year=2026/month=07` | 2026-08-01 00:06 |
| **`year=2025/month=01` … `year=2026/month=05` (19 partitions)** | **2026-08-26 08:25–08:26** |
| `year=2026/month=08` | 2026-08-31 00:06 |

`daily_ingest.py` writes only the **current** month. Before the 2026-08-26
backfill the lake therefore held roughly two months of funding — far below the
252-day requirement. After it, the lake holds **608 distinct days
(2025-01-01 … 2026-08-31)**, 607 of them with the required `COUNT(*) >= 2`
(Binance funds 3x daily).

The observed regime transition matches exactly:

```
2026-06-16   funding_regime -> unknown
2026-08-27   funding_regime -> high
```

The first h6 run **after** the backfill flipped the regime. Reproducing
`_load_corpus()` today gives **272 of the last 273 days valid** (the single
gap is 2026-08-31, the in-progress day) — comfortably above 252.

**h6 could not have fired on any day before 2026-08-27, regardless of market
conditions.** Those days are not evidence that h6 was flat; they are days on
which h6 was not computable.

---

## 4. Quantified evidence chain

| measure | value |
|---|---|
| Days collected | **75** (2026-06-16 … 2026-08-31, a 77-day span) |
| Missing days in span | **2** — 2026-07-01 and 2026-08-01, both month-rollover days |
| Distinct `h6_canonical` values | **1** — `0`, on every day |
| `h6_shadow` | `NULL` on all 75 |
| `in_hold` | `0` on all 75 |
| Days with **unknown** funding input | **70** (2026-06-16 … 2026-08-26) |
| Days with **actionable** inputs | **5** (2026-08-27 … 2026-08-31) |
| Trade entries / exits / closed trades | **0 / 0 / 0** |
| `run_log.status` | **`ok` on all 75** |
| `run_log.anomalies_json` | **`[]` on all 75** |
| Rejected or blocked evaluations recorded | **none — the concept is not recorded** |
| First / last observation | 2026-06-16 / 2026-08-31 |

Joint regime distribution over the 75 days:

| vol_regime | funding_regime | days | h6 could fire? |
|---|---|---|---|
| low | unknown | 54 | No — input unavailable |
| high | unknown | 12 | No — input unavailable |
| medium | unknown | **4** | **No — input unavailable, though vol was right** |
| high | **high** | 5 | No — funding is `high`, h6 needs `low` (**legitimate**) |

The four `medium`-vol days are the sharpest illustration: on those days h6's
volatility condition was satisfied and the outcome still could not be
determined, because the funding input did not exist.

---

## 5. Independent finding — the h6 collector survived INC-0005

`btcbot-evidence.service` wrote **18 of the 19 days** inside the INC-0005
window (2026-07-18 … 2026-08-05); the single absence is 2026-08-01, a
month-rollover day, not the outage. INC-0005 was specific to
`btcbot-fwd-hg.service`. **The two forward collectors are independent**, which
CLAUDE.md §20 does not state and which matters for any future continuity
analysis.

---

## 6. Classification

**COMBINATION — predominantly B, now A, with a minor C.**

| period | days | class | basis |
|---|---|---|---|
| 2026-06-16 … 2026-08-26 | **70** | **B — upstream evidence dependency missing** | funding history absent from the research lake; `funding_regime = unknown`; the decision was not computable |
| 2026-08-27 … 2026-08-31 | **5** | **A — legitimate zero-evidence** | inputs present and valid; `funding_regime = high`, h6 requires `low`; correctly flat |

**Not D.** The specification is unambiguous — the frozen conjunction and the
252-day guard are explicit and were applied correctly.

**Minor C, non-decision-affecting** — two real defects, neither of which
changed any h6 decision:

1. **`corpus_daily.funding_bps` is `NULL` on all 75 rows**, including the five
   days when funding was available. The job records the *current* day, whose
   funding is not yet ingested at 00:06 UTC. The regime computation is
   unaffected (it reads the trailing window), but the corpus's own funding
   column is useless for audit — which is why this root cause had to be
   reconstructed from parquet file timestamps rather than read off the record.
2. **Two observations lost at month rollover** (2026-07-01, 2026-08-01), a
   repeating pattern rather than random loss.

---

## 7. Is this a legitimate research result or an evidence-collection failure?

**Both, and the platform cannot currently tell them apart from its own
records.** For 70 days the collector reported `status = 'ok'` with
`anomalies = []` while producing observations that could never become
evidence. A day on which h6 was computed and said flat, and a day on which h6
could not be computed at all, are recorded **identically** — `h6_canonical = 0`.

### Does any existing governance mechanism distinguish them?

**Yes — and it is built, tested, and wired to nothing.**

PR-7's Data Completeness Framework (`research/dq/`, CLAUDE.md §17) exists
precisely for this distinction: eight states including `UNKNOWN` and
`INCOMPLETE_DATA`, a `(state, criticality) -> action` matrix, worst-wins
aggregation, and the explicit rule that **"empty is not READY -> UNKNOWN ->
BLOCK"**. 187 tests. Its own record states: *"No adopters yet; adoption
(`corpus_daily`, the readiness-aware `_already_processed()`) is deferred."*

**h6 is exactly the case PR-7 was designed for, and PR-7 was not connected to
it.** FB-7's `UNEVALUABLE` floor status embodies the same principle at the
floor level; neither reaches the daily collection record.

---

## 8. Should h6 remain in the forward collection universe?

**Yes — remain, and take a deliberate provenance decision about the past.**

Removing h6 would be a capture-path change (gated), and it is now working: the
last 5 days are genuine, evaluable observations. Collection should continue.

**But the 70 pre-backfill days must not be counted as h6 evidence.** They are
currently indistinguishable from real flat days, so any future count of "days
h6 was observed", any observation floor, and any continuity or sample-size
assessment would silently absorb 70 non-observations. That is the same failure
mode INC-0005 was documented to prevent, and it requires the same remedy: a
durable record of an immutable unevaluable window, carried explicitly and
never interpolated away.

Frozen governance does not currently require this — no rule covers it, which
is itself the gap. It should be a deliberate decision, not a default.

---

## 9. Finding · Evidence · Governance implication · Next gate

**Finding.** h6's zero evidence is **not** a strategy result for 70 of its 75
collected days. It was caused by a missing upstream dependency — the Binance
funding lake was not backfilled until 2026-08-26 — which forced
`funding_regime = unknown` and made the frozen conjunction uncomputable. Since
2026-08-27 h6 is fully evaluable and its flatness is a legitimate research
outcome. Neither state is distinguishable in the platform's own records.

**Evidence.** 19 funding partitions written 2026-08-26 08:25–08:26; regime
transition to `high` on the first subsequent run (2026-08-27); 272/273 valid
funding days today versus a 252 requirement; `h6_canonical` single-valued at
`0` across 75 days; `run_log` `ok` / `[]` throughout; 0 entries, 0 exits.

**Governance implication.** The platform recorded 70 days of clean success for
a signal that could not produce evidence. The mechanism to prevent that
(PR-7 DQ states) exists and has no adopters. Until it is wired, "the job ran
and wrote a row" cannot be read as "evidence was collected".

**Smallest next gate — FB-11: h6 unevaluable-window provenance record
(decision-only, read-only).** Durably record 2026-06-16 … 2026-08-26 as an
immutable unevaluable window for h6, in the form CLAUDE.md §20 already uses for
INC-0005, so no future count treats those 70 days as observations of a flat
signal. No code, no schema, no backfill — the days stay exactly as collected.
This is the smallest remediation that protects existing data, and it is
strictly smaller than the engineering fix below.

---

## 10. Independent workstream, available now

**Adopt PR-7's Data Completeness Framework for the forward collectors.** Built,
tested (187 tests), zero adopters, and this audit is its motivating case. Its
own deferred-adoption note names `corpus_daily` — the very table whose
`funding_bps` column is `NULL` on every row. Wiring it would make the A-versus-B
distinction structural rather than reconstructable-by-archaeology, for h6 and
for all seven signals in the other collector.

Two smaller, unrelated items also surfaced:

- **Month-rollover collection loss** — 2026-07-01 and 2026-08-01 are missing
  from a 77-day span; both are month boundaries, so this will recur on
  2026-09-01 without a fix.
- **`corpus_daily.funding_bps` always NULL** (§6 C.1) — a recording defect that
  cost this audit real effort and would cost it again.

Neither requires waiting for more observations.
