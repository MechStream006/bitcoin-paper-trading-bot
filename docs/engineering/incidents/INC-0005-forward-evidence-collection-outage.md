# INC-0005 — Forward-Evidence Collection Silently Dead for 19 Consecutive Days

| Field | Value |
|---|---|
| **Status** | Closed — root-caused, fixed, verified. **The data loss is permanent and is not closed by this record.** |
| **Severity** | High (19 calendar days of irreplaceable forward observations lost for all 5 signals; no corruption, no incorrect data written) |
| **Discovered during** | Phase 4 D0 deployment pre-flight on the production host, 2026-08-06 — not by any monitoring signal |
| **Discovered by** | Manual pre-flight inspection; the failure had been written to the journal every day for weeks with nothing listening |
| **Affected system** | `btcbot-fwd-hg.service` on the production host, and by consequence the forward-evidence corpus (`evidence_forward.db`) for H5, G1, G3, TSMOM and IVRV |
| **Author** | Claude Opus 5 (paired session), recorded per Gate 5C.29 |
| **Date filed** | 2026-08-26 |

---

## Summary

Between **run_date 2026-07-18 and run_date 2026-08-05 inclusive — 19 consecutive
days** — `btcbot-fwd-hg.service` exited non-zero on every invocation and wrote
**zero** forward-evidence observations. The timer fired normally throughout and
`systemctl list-timers` looked healthy, because a `oneshot` unit failing is not a
timer fault.

The service aborted on a governance pre-check, **before** any evidence write.
That is the designed fail-closed behaviour and it worked correctly: no evidence
was written against a ledger whose registry could not be validated. The defect
was not that the job refused — it was that **nothing was told that it had
refused**.

**The 19 missing days are permanent.** Forward observations must be captured on
the calendar day they describe; they cannot be reconstructed from any source at
any cost. No backfill was performed, and none is permitted (see
§ *Evidence provenance*).

---

## Timeline

| UTC | Event |
|---|---|
| 2026-07-17 | **Last successful collection.** Final `run_log` `run_date` for all 5 signals |
| 2026-07-18 07:12 | Host pulled to `7fd201e`, introducing `lifecycle.py`'s C1 `run_kind` invariant |
| 2026-07-18 → 2026-08-05 | `btcbot-fwd-hg.service` fails on every run. **19 consecutive missing `run_date`s.** Timer healthy throughout |
| 2026-07-26 | Program P1 Increment 5 recorded as "Production Migration complete" (see § *Relationship to CLAUDE.md §18 Increment 5*) |
| 2026-08-06 | Failure discovered during unrelated deployment pre-flight; investigation filed as [`incident-2026-08-06-production-investigation.md`](../../ops/incident-2026-08-06-production-investigation.md) §B2 |
| 2026-08-07 02:39 | Ledger migration executed; service run manually — **first successful resumed run**, producing `run_date` **2026-08-06** |
| 2026-08-26 | `OnFailure=` escalation deployed (Gate 5C.28), closing the detection gap |

---

## Root Cause

`research/evidence_library/lifecycle.py`'s C1 invariant reads `r["run_kind"]`
unconditionally off a `sqlite3.Row` (`lifecycle.py:497`). The production ledger
at `/opt/btcbot/research/evidence_library/ledger.db` **did not have a `run_kind`
column**, so the subscript raised `IndexError: No item with that key`.

`run_daily` invokes `evidence_library/report.py --check` as a hard precondition.
The `IndexError` propagated out of that pre-check, so `run_daily` aborted with
status `1/FAILURE` on every invocation, before reaching any collection or write
path.

Two conditions had to coincide, and both were true on this host from 2026-07-18:

1. Code requiring `runs.run_kind` was deployed (arrived in the 2026-07-18 pull).
2. The production ledger had never been migrated to provide that column.

---

## Detection

**Detection failed.** The failure was loud, daily, and durable in the journal,
and was found only because a human looked, three weeks later, for an unrelated
reason.

| Signal that should have caught it | Why it did not |
|---|---|
| `btcbot-fwd-hg.timer` | The timer fired correctly every day. A failing `oneshot` service does not make its timer unhealthy |
| `systemctl list-timers` | Shows timer schedule and next-elapse, not the triggered unit's exit status |
| `OnFailure=` escalation | **Did not exist.** No unit in the project carried `OnFailure=`, and no notifier unit existed |
| `btcbot-healthcheck` (`forward_cycle` freshness) | **Not installed on this host** at the time (see the 2026-08-06 investigation, §B5) |

---

## Impact

- **19 consecutive `run_date`s lost** (2026-07-18 → 2026-08-05 inclusive), for
  **all 5 signals** — H5, G1, G3, TSMOM and IVRV.
- Includes the **entire post-activation life of `g3_atr_expansion`** to that
  point: ATR was activated 2026-07-19 and had produced nothing.
- **G3 cadence discontinuity.** G3's weekly entry rhythm restarted on a
  different weekday anchor after the outage — recorded in Gates 5C.25/5C.26 as
  **Monday → Thursday**. Any weekday-sensitive analysis of the G3 entry series
  must treat the series as two segments, not one.
- **No incorrect data was written.** The fail-closed abort preceded every write
  path, so the corpus contains a clean absence, not corrupt or partial rows.

### Counting note

The authoritative measure of this outage is **missing `run_date`s: 19**
(2026-07-18 → 2026-08-05 inclusive, arithmetically verified). Other counts
appear in earlier documents and measure different things — the 2026-08-06
investigation reports "20/21 days" (calendar days of service failure, counted
from different endpoints) and its F12 reports **28 failed invocations** over the
journal's 14-day retention window (Jul 24 – Aug 06), i.e. more than one
invocation on some days. **No explanation for the per-day invocation count is
recorded here, because none was established.**

---

## Evidence provenance — binding constraint on future analysis

**The gap is immutable provenance. It must never be interpolated, imputed,
forward-filled, or otherwise silently closed.**

- **No backfill was performed**, and no backfill is permitted. Forward evidence
  derives its validity from having been recorded prospectively on the day; a
  reconstructed row is not the same object as an observed one, regardless of how
  accurately it could be recomputed.
- Any future research assessment that counts observation days, computes
  continuity, or measures sample size **must carry this gap explicitly** and
  report it, rather than treating the series as continuous.
- The gap will appear in **every** future recount. Its presence is correct and
  expected; its *absence* would indicate that something interpolated it.
- The already-recorded per-signal missing-day counts are fully explained by this
  single outage — see
  [`docs/research/evidence_accumulation_baseline_2026-08-11.md`](../../research/evidence_accumulation_baseline_2026-08-11.md)
  §4, which independently records the same window
  (2026-07-18 → 2026-08-05, 19 calendar days) as permanent and non-closing.

---

## Relationship to CLAUDE.md §18 Increment 5

Program P1 Increment 5 records a production ledger migration as executed and
attested on **2026-07-26**. Production behaviour observed on **2026-08-06** shows
the production ledger still lacked `runs.run_kind` at that date, and the column
was created by the migration run on **2026-08-07**.

Both records are preserved. **No explanation for the discrepancy is offered
here**, because none was established — the original execution transcript for the
2026-07-26 attestation is unavailable. See CLAUDE.md §18 Increment 5 and
[`p1_assurance_declaration.md`](../../governance/p1_assurance_declaration.md)
claim 4.

---

## Resolution

**Data loss: not resolvable.** The 19 days are gone.

**Failure condition: resolved 2026-08-07.** The three-script D-0 migration
sequence was executed against the production ledger, each step backed up and
verified: `report.py --check` OK (35 families, 9/22/4), `verify_chain()`
`ok=True, n_receipts=26`, `PRAGMA integrity_check` ok, re-run idempotent
(0 pending), and `btcbot-fwd-hg.service` returned `Result=success`. Full
procedure and results: the 2026-08-06 investigation, §10 B2.

**Detection gap: resolved 2026-08-26** (Gate 5C.28) — see § *Prevention*.

---

## Prevention

Two independent detectors now cover this failure class, and neither retries nor
backfills:

1. **`OnFailure=btcbot-alert@%n.service`** on `btcbot-fwd-hg.service` —
   escalates any non-zero exit to Telegram with the unit's status and the last
   15 journal lines, so the alert reports *why*, not merely *that*.
   Deliberately **not** `Restart=`: re-running a fail-closed governance refusal
   would either fail identically or write evidence against a registry already
   judged invalid.
2. **`btcbot-healthcheck.timer`** — `forward_cycle` freshness, an independent
   staleness check that fires even if the unit never runs at all (the case
   `OnFailure=` structurally cannot catch).

`GV-SCHEMA` (Program P1 Increment 3) additionally converts this specific crash
into a named diagnostic instead of an `IndexError`; that code was undeployed on
this host at the time of the outage.

Operational record of the deployment:
[`unit-drift-and-failure-alerting-2026-08-26.md`](../../ops/unit-drift-and-failure-alerting-2026-08-26.md).
