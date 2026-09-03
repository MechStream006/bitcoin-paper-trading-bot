# ADR-0003: Live Entry Policy

**Status:** Accepted (2026-09-03). `ADR-LE-1` through `ADR-LE-5` are all
decided; `ADR-LE-4` (the trigger, the relationship to the forward corpus and
the cadence) and `ADR-LE-5` (the protective stop) were strategy-owner
decisions, taken after a specification audit found no recorded intent for
either. Implemented in the same change that records this ADR.

**Relationship to ADR-0001 / ADR-0002:** Extends, does not supersede. ADR-0001
covers infrastructure and ADR-0002 governance-store consolidation; neither
addresses strategy entry semantics. This is the first ADR to govern the live
trading path.

**Full engineering narrative:** `CLAUDE.md` §22. This ADR records *decisions*;
§22 records *history* — what was measured, what was found, and what was built.
This document cross-references §22 rather than repeating it. If you need "what
happened", read §22. If you need "what was decided and why it is binding",
read this.

---

## 1. Context

The forward research collector and the live execution path each generated
entries, and their conditions were opposite with respect to the forward book.
The collector opens a trade only when none is open (`runner_base` 5b,
`not _has_open_trade(...)`). The live path derived its direction from
`bridge.latest_direction()`, which returns the direction of a forward trade
that **is** open — so it could only ever act while the collector was holding.

The forward side is fully specified and governed.
`pre_registration_g3_tsmom.json` → `collection_spec.daily_job` states it
verbatim:

> 4. *"If no open trade: open trade_entries row (direction=signal,
>    planned_exit_date=today+7)"*
> 5. *"If open trade AND today == planned_exit_date: close trade_exits row …;
>    immediately open new trade_entries row"*

Registered 2026-06-14 and frozen; its own `notes` require a version bump and a
new registration to change. **It is not modified by this ADR.**

The live side had **no specification**. A repository-wide search found no file
under `docs/` naming `strategy_jobs`, `latest_direction`,
`DEMO_STRATEGY_ENABLED` or `DEMO_STRATEGY_DRY_RUN`; no pre-registration
contained any execution key; `CLAUDE.md` carried no entry-cadence statement.
What existed were two risk caps, an idempotency key, and a module docstring
that contradicted the function beneath it — `bridge.py`'s header said it *"reads
the latest `g3_momentum_tsmom` position"* while `latest_direction`'s own
docstring said *"Most recent open trade direction"*. The code did the latter.
Commit `e6bc330`, which wired the path, describes itself as *"the missing
caller, and nothing more"*.

The behaviour was therefore inherited from wiring, not chosen. Measurements
that motivated this ADR are recorded in `CLAUDE.md` §22; they are **not** the
basis for the decisions below.

---

## 2. Decisions

### ADR-LE-1 — Signal direction and consumed direction are distinct quantities

`signal_log.position` (the latest daily signal) and `trade_entries.direction`
(the direction of a held trade) are different observations of the same family
and may legitimately differ, because the collector holds without early exit.
Neither is "the direction"; each must be named where it is used.

**Binding form:** the feature layer carries both under distinct, hash-pinned
contracts — `signal_position` (`6a2dcdb053c8`) reads `signal_log.position`;
`consumed_direction` (`e0c0af82039e`) reads the direction actually held.
`trade_signal_context.direction` records the direction of the **submitted
intent**, pinned from `intent.side` and never re-read.

**Rationale:** conflating them allowed three real SELL orders to be recorded
beside a `+1` daily signal with nothing marking the difference.

**Implementing code:** `research/alignment/features.py`,
`research/alignment/forward_trades.py`, `src/strategies/bridge.py`
(`capture_signal_context`).

### ADR-LE-2 — The client_order_id guarantees idempotency ONLY

`client_order_id(...)` exists to make a pass repeatable: a short scheduler
interval, a mid-day restart or a retry must not produce a second order. It is
**not** an entry-frequency policy and must not be read as one. Entry frequency
is bounded only by the risk layer (`MAX_ORDERS_PER_DAY = 2`, enforced at
`manager.py:291`).

**Consequence of ADR-LE-4:** the key's day component is now the forward
entry's `entry_date`, not the submission day. The UTC day no longer appears
anywhere in the entry path, so restart, retry and midnight are all idempotent
by construction rather than by timing.

**Recorded hazard:** the id is the dedupe key, so an id *format* change
defeats dedupe. Observed 2026-08-20, when a 35-char and a 32-char id for the
same (signal, day, side) both submitted.

**Implementing code:** `src/strategies/bridge.py` (`client_order_id`),
`src/strategies/strategy_jobs.py` (`evaluate_once`).

### ADR-LE-3 — MAX_OPEN_POSITIONS = 1 is a risk bound, not the strategy

Enforced at `manager.py:275`: a non-exit intent is rejected while any position
is open. It constrains the policy identically in all cases and is not reopened
here. No policy may be specified that requires holding more than one position.

**Implementing code:** `src/risk/limits.py`, `src/risk/manager.py` — both
unchanged by this ADR.

### ADR-LE-4 — Live entry mirrors the governed forward entry process (P-MIRROR)

The live `g3_momentum_tsmom` path executes the same governed 7-day strategy
defined by the frozen forward pre-registration.

- The **forward entry event** — a new `trade_entries` row — is the
  authoritative strategy entry event. The live path mirrors it; it does not
  originate entries.
- **One position per 7-day hold.**
- **Entry direction** is the signal current at the forward entry event, i.e.
  that row's own `direction` (which `runner_base` 5b sets as `int(position)`
  for `run_date == entry_date`, in the same transaction).
- The **live execution direction is pinned from the consumed entry intent**
  (`intent.side`), never re-read.
- **Live exits follow the governed 7-day planned-exit convention.** No
  alternative exit policy is introduced, and there is no live horizon timer:
  the exit fires on the appearance of the forward `trade_exits` row, which the
  collector writes in the same transaction that sets `trade_entries.open = 0`.
  A timer would drift from the governed convention during a collector outage.
- The **frozen pre-registration is unchanged.** The mirroring obligation lives
  here, not there.

**Mirror window — one hour.** A forward entry becomes mirrorable when its
committed `trade_entries` row is readable, taken as the `run_log.created_at`
of the run that wrote it (the collector commits at 01:00:06–01:00:42 UTC under
`RandomizedDelaySec=30`; the nominal `entry_date + 25h` is the fallback when
that stamp is unusable). The live path may submit for **up to one hour** after
that instant. After the window expires the entry is permanently
`NOT_MIRRORED`, with reason `cold_start` (readable before this process began
mirroring) or `missed` (readable while it was running). The window is **never**
extended by `planned_exit_date` or by the next collector run, and a late entry
is never made: a position opened mid-hold has a different entry price and a
truncated horizon, and is therefore not comparable at the entry level — the one
property ADR-LE-5 requires a stopped trade to retain.

**Operational consequence, accepted:** after a first deployment or any outage
the live path waits for the next forward entry event, up to seven days.

**Implementing code:** `src/strategies/bridge.py` (`MIRROR_WINDOW`,
`ForwardEntry`, `read_forward_entries`, `mirrorable_entry`, `expired_entries`,
`forward_exit`), `src/strategies/strategy_jobs.py` (`evaluate_once`,
`evaluate_exit_once`), `src/data/storage.py` (`mirrored_forward_entry`).

### ADR-LE-5 — The protective stop is an independent live risk overlay

`STOP_DISTANCE_PCT`, `MAX_TRADE_RISK_USD`, `HARD_RISK_CEILING_USD` and
`MAX_CONTRACTS_PER_ORDER` are **unchanged and not reopened**.

The governed forward strategy defines the **7-day research horizon and the
theoretical strategy outcome**. The live system mirrors the forward **entry
event and direction**, and remains subject to **mandatory risk controls,
including the protective stop**. Live execution is therefore a **"7-day
strategy + live risk overlay"**, not a byte-for-byte reproduction of the
forward outcome.

**A protective-stop exit is a live RISK exit, not a strategy exit.** It
terminates the live position; it does not terminate, shorten or redefine the
forward trade, which continues to its governed 7-day exit and produces its own
label independently. No re-entry is ever made after a risk exit while the same
forward entry remains open.

**A stopped live trade remains comparable to its forward trade at the entry
level** — same entry event, same direction, same vintage — **and never at the
outcome level**. Its realised live outcome must never overwrite, replace or
redefine the forward 7-day label.

**Future research must distinguish two named quantities per mirrored entry:**

| quantity | owned by | measured from |
|---|---|---|
| `strategy_horizon_outcome` | the forward corpus | `entry_close_price` → `exit_close_price` over the governed 7 days, net of the pre-registered 15 bps |
| `live_risk_overlay_outcome` | the execution corpus | real fills, real fees, plus an `exit_cause` |

They are two measurements of **one entry event under two exit policies**. They
share `forward_entry_id` as the join key and **must never be merged into a
single number**.

**Quantified expectation, recorded so it is not rediscovered as a surprise.**
Over the 31 completed forward holds, using daily closes only (a lower bound —
intraday is deeper), **15/31 = 48%** breach the 140 bps stop implied by the
current budget; the median worst adverse excursion is **−131 bps**. Roughly
half of mirrored trades are expected to exit on the overlay rather than at the
horizon. **That is the accepted design, not a defect**, and it is the reason
the two outcomes must stay separate.

**Implementing code:** `src/strategies/strategy_jobs.py`
(`evaluate_exit_once`, `_risk_exit_cause`, `_entry_filled`),
`src/data/storage.py` (`mirrored_forward_entry.exit_cause`, the `live_*` and
`forward_exit_*` column groups). The reconciler and protection paths are
untouched.

---

## 3. Consequences

- The forward corpus becomes admissible as evidence for the live path
  **prospectively, from the first mirrored entry onward** — never
  retroactively.
- **All 8 historical execution decisions remain permanently non-comparable and
  must not be relabelled.** None corresponds to a forward entry event; all were
  taken 3.6–6.8 days into a hold; two were rejected on a client-side id-length
  defect; one was never produced by `evaluate_once`; four of five completed
  trades were closed by supervised manual exits. They remain valid
  **execution-quality** evidence (fills, fees, slippage, latency, MAE/MFE) and
  keep their stored labels, contracts and hashes unchanged.
- Existing label and feature contracts are unchanged and not re-versioned:
  outcome contract X1 v1 `7796fed7751a0606`, `signal_position`
  `6a2dcdb053c8`, `consumed_direction` `e0c0af82039e`. At a mirrored entry the
  latter two agree by construction, which becomes the conformance test that the
  mirror is working.
- Every existing safety gate is preserved: `DEMO_STRATEGY_ENABLED`,
  `DEMO_STRATEGY_DRY_RUN`, the reconciler stand-down, `MAX_OPEN_POSITIONS`,
  `MAX_ORDERS_PER_DAY`, the per-trade risk budget and the protective stop.
- `mae_mfe_path` (pre-registered `collection_spec` step 6) was never
  implemented and no such table exists. It does not block the mirror; intra-hold
  excursion is not measurable from the collector.

---

## 4. Where to look for more

| Question | Answer is in |
|---|---|
| What the collector is contractually required to do | `research/forward_evidence/pre_registration_g3_tsmom.json` → `collection_spec` |
| What was measured, and what was built | `CLAUDE.md` §22 |
| What the live path does now | `src/strategies/strategy_jobs.py`, `src/strategies/bridge.py` |
| The mirror lifecycle and its states | `src/data/storage.py` (`mirrored_forward_entry`) |
| What bounds an order regardless of policy | `src/risk/limits.py`, `src/risk/manager.py` |
| Why automated entry is permitted at all | `tests/test_demo_execution.py::test_automated_entry_cannot_submit_without_the_explicit_controls` |
