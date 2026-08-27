# Forward Collection — Scheduling Chain and UTC Reporting Convention

**Recorded 2026-08-27 (UTC) at Gate 6B.7. Verdict: NO BUG. Documentation only —
no runtime behaviour was changed and nothing was deployed.**

Written because a gate report stated "today is 2026-08-28" while production UTC
was still 2026-08-27, and an earlier gate wrote a wrong date into a provenance
record on the same mistake (corrected in `7989420`). The scheduling chain is
sound; the ambiguity was entirely in how the state was *reported*.

---

## 1. The scheduling chain, verified end to end

Every link was checked read-only on the production host on 2026-08-27.

| Link | Verified value | Verdict |
|---|---|---|
| Host time zone | `Etc/UTC (UTC, +0000)`, NTP active, clock synchronized | UTC |
| Host clock, 3 independent sources | `date -u`, `timedatectl`, systemd `Universal time` all agree | consistent |
| `btcbot-fwd-hg.timer` | `OnCalendar=*-*-* 01:00:00 UTC` | **explicit `UTC` suffix** |
| Timer jitter | `RandomizedDelaySec=30`, `AccuracyUSec=1min` | explains `:22` / `:40` |
| Timer catch-up | `Persistent=true` | a missed slot runs once on next boot/enable |
| Ingest ordering | `btcbot-evidence.timer` `OnCalendar=*-*-* 00:05:00 UTC` | 55 min before collection |
| `run_daily._derive_run_date()` | `(datetime.now(timezone.utc).date() - timedelta(days=1))` | UTC-aware |
| Policy anchor | `evaluate_run_date(..., today=datetime.now(timezone.utc).date())` | UTC-aware |
| Clock reads in the whole forward path | exactly **two**, both `datetime.now(timezone.utc)` | no naive `now()`, no `date.today()`, no `utcnow()` |

**The `UTC` suffix on `OnCalendar` is the load-bearing detail.** Without it
systemd interprets a calendar expression in *local* time, so the firing instant
would follow the host time zone. With it, the timer is time-zone-independent by
construction — the host being `Etc/UTC` is a second, redundant safeguard rather
than the thing being relied on.

## 2. Why `run_date` lags the firing date by one day — by design, not by defect

`run_date` is **the last complete UTC calendar day**, so a run firing on day *D*
collects day *D − 1*. That is the definition of a completed daily bar, and it is
what the 1440-bar completeness guard (6B.1) enforces.

| Firing instant (UTC) | Derived `run_date` | Policy decision |
|---|---|---|
| 2026-08-27 01:00:40 | **2026-08-26** | allowed, `mode=scheduled` |
| 2026-08-28 01:00:22 | **2026-08-27** | allowed, `mode=scheduled` |
| 2026-08-29 01:00:xx | 2026-08-28 | allowed, `mode=scheduled` |

The middle row was evaluated directly against the deployed
`evaluate_run_date()` with `today=2026-08-28` — a pure function call with no
side effects, no DB write, and no collection. It returns
`allowed=True, mode=scheduled, code=None`. **The next scheduled run will
correctly evaluate `run_date = 2026-08-27`.**

So the observation that the 2026-08-27 01:00:40 run "showed `run_date=2026-08-26`"
is the system behaving exactly as specified.

## 3. Root cause of the ambiguity

The operator workstation runs **IST (UTC+05:30)**. Between 18:30 and 24:00 UTC
the local *calendar date* is already tomorrow:

```
local naive  : 2026-08-28T02:23:06     <- what a bare datetime.now() shows
local UTC    : 2026-08-27T20:53:06Z    <- what production is actually living in
offset       : +0530
```

Every date in this repository — `run_date`, `pre_registration_date`,
provenance timestamps, the lockbox window `2025-06-01..2026-05-31`, the
immutable gap `2026-07-18..2026-08-05` — is **UTC-denominated**. A report that
quotes the workstation's local date is off by one for 5.5 hours of every day,
and during that window it can make a *pending* scheduled run look as though it
has already happened.

This is a reporting hazard, not a scheduling one. Nothing in the runtime reads
the workstation clock.

## 4. Reporting convention — mandatory for every future audit

1. **Quote UTC first, always, with the `Z` suffix.** IST may follow in
   parentheses when it helps a human, never alone:
   `2026-08-27T20:53:06Z (2026-08-28 02:23 IST)`.
2. **Never use the workstation's calendar date to decide whether a scheduled run
   has occurred.** Establish that from the host: `systemctl show
   btcbot-fwd-hg.service -p ExecMainStartTimestamp` plus the timer's `NEXT`.
3. **Any date written into a governance or provenance artifact is UTC.** If the
   workstation date and the host date disagree, the host wins.
4. **Distinguish the firing date from `run_date`** (§2). They differ by one day
   by design; conflating them will make a correct run look wrong.

## 5. Copy-paste verification block (read-only)

Run this before asserting anything about scheduling state. It writes nothing,
triggers nothing, and touches no database.

```bash
ssh -i <key> ubuntu@<host> "
  date -u '+HOST_UTC=%Y-%m-%dT%H:%M:%SZ'
  timedatectl | sed -n '1,5p'
  systemctl cat btcbot-fwd-hg.timer | grep -E 'OnCalendar|Persistent|RandomizedDelaySec'
  systemctl list-timers btcbot-evidence.timer btcbot-fwd-hg.timer --all --no-pager | head -4
  systemctl show btcbot-fwd-hg.service -p Result -p ExecMainStartTimestamp -p ExecMainStatus
"
```

The scheduled run has occurred **only if** `ExecMainStartTimestamp` is at or
after the expected firing instant. The timer's `LAST` column shows the same
fact. A `NEXT` in the future with an unchanged `LAST` means **PENDING** — wait,
do not trigger.

## 6. What was deliberately not changed

No source file, unit file, timer, configuration value or deployed artifact was
touched. There is no defect to fix: adding a "fix" to a correct scheduling chain
would introduce risk to buy nothing. The `Persistent=true` catch-up semantics,
the 55-minute ingest margin, and the `UTC`-suffixed `OnCalendar` are all left
exactly as they are.

## 7. Related records

- `docs/ops/unit-drift-and-failure-alerting-2026-08-26.md` — `OnFailure=` and the
  `forward_cycle` freshness detector, the two independent detectors that cover a
  *failed* run (this record covers a *pending* one).
- `CLAUDE.md` §20 — the 2026-07-18..2026-08-05 forward-evidence gap, whose
  `run_date`s are UTC and must never be re-derived from a local clock.
