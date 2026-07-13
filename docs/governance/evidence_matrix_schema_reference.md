# Evidence Matrix & Governance Reporting — Implemented Schema Reference (PR-5)

**Status:** Implemented. Bundles the design's own internal PR-1 through PR-4
(schema/lanes, point-in-time substrate, reports, invariants) into one
reviewable unit, per the outer task's scope. §10 PR-5 ("Operationalise" —
systemd timer, daily commit, heartbeat/alert wiring) is explicitly NOT
implemented here.
**Design authority:** `docs/governance/evidence_reporting_architecture.md`.
This document describes the Python/SQLite implementation and the concrete
scoping decisions made while implementing it exactly; it is a reference,
not a second design decision.

Modules: `research/evidence_library/record.py`, `lifecycle.py`, `report.py`,
`migrate_run_kind.py`. This subsystem is **fully independent of PR-1–4's
governance receipts / contract registry / contract lifecycle** — it operates
entirely on the pre-existing `ledger.db` (runs / gate_results /
state_transitions) and `registry.yaml`. No contract or receipt table is
read or written by anything in this PR.

---

## What changed on the real data (not just code)

This PR's own acceptance test ("the real registry passes all nine
invariants") required two real, one-time data actions, both taken with
backups:

1. **`migrate_run_kind.py --commit`** run against the real `ledger.db`.
   Classified all 70 pre-existing rows: earliest offline-corpus run per
   family → `discovery`; every later offline-corpus run → `confirmatory`;
   the IVRV/ATR admission runs → `confirmatory`, matching the design doc's
   §9 worked example exactly. Backup:
   `ledger.pre-run_kind-migration.<timestamp>.db` (git-ignored — same
   category as `ledger.db` itself); audit note (`.audit.txt`, git-tracked)
   records the rule, git SHA, and row count.
2. **`registry.yaml: provenance` removed from all 35 entries** — a
   surgical, line-level deletion (verified via `git diff` to touch nothing
   else; a first attempt using `yaml.safe_dump` was reverted because it
   silently destroyed every comment and reformatted the whole file — not
   an acceptable diff for a hand-maintained governance file).

No evidence value (`sharpe`, `dsr_prob`, `metrics_json`, `run_ts`, `verdict`,
...) was touched by either action — verified by the test suite and by
direct inspection.

---

## Axis A: evidence lanes (`record.py`)

`LANES: dict[str, tuple[str, str]]` — the frozen `(provenance, run_kind)`
table:

| Code | provenance | run_kind |
|---|---|---|
| L0 | offline-corpus | discovery |
| L1 | offline-corpus | confirmatory |
| L2 | live-forward | forward |
| L3 | mainnet | production |

`append_run(..., run_kind)` — **now a required argument**. Validates
`run_kind ∈ _VALID_RUN_KIND` and `(provenance, run_kind) ∈ _VALID_LANE_PAIRS`,
mirroring the existing `provenance` validation exactly. Written once, never
updated (Law 2) — there is no `UPDATE runs SET run_kind` anywhere in this
codebase.

`current_run_per_lane` (VIEW): `ROW_NUMBER() OVER (PARTITION BY family_id,
provenance, run_kind ORDER BY run_ts DESC, run_id DESC)`, `WHERE rn = 1`.
The explicit `run_id` tiebreak removes the non-determinism from the old
`MAX(run_ts) GROUP BY` join — tested directly with two runs sharing an
identical `run_ts`.

**Deleted, not deprecated**: `get_all_latest_runs()`, `latest_run_per_family`
VIEW. `get_latest_run()` now requires a `run_kind` argument (raises
`TypeError` if omitted). There is no function anywhere in this module that
selects "the latest run" across lanes.

**Redefined**: `has_live_evidence` now requires `n_trades > 0` — a
bridge-written row with zero closed trades no longer counts as "has live
evidence."

**New**: `get_current_run(s)_by_lane(db, as_of=None)` — the only sanctioned
way to populate the Evidence Matrix. `as_of`, if given, must already be a
fully-qualified ISO datetime (see `report.normalize_as_of()` below); this
function applies no normalisation itself, precisely so that responsibility
lives in exactly one place (the CLI-facing call site) rather than being
duplicated and risking divergent behavior between call sites.

---

## Point-in-time substrate (`record.py`, `lifecycle.py`)

`derive_state_at(db, family_id, as_of=None) -> (state, pinned_run_id, ts)` /
`derive_all_states_at(db, as_of=None)` — pure derivation from
`state_transitions`, **never** reads `registry.yaml`. `registry.yaml` is a
cache of this, validated against it (C4/C5 below).

**`append_transition()` gained two new requirements:**
1. A transition into `STATES_REQUIRING_RUN` (`candidate`, `validated`,
   `promotion_ready`, `production_ready`) requires a non-`NULL`
   `justifying_run_id`, **unless** `ts` is explicitly supplied and predates
   `2026-07-01` (the grandfathered pre-ledger bootstrap history — and only
   that history; every one of the real ledger's 13 transitions already had
   a non-`NULL` `justifying_run_id`, so this grandfather clause is
   currently unexercised by real data but preserved for the historical
   record it documents).
2. A self-transition (`from_state == to_state`) — a **re-pin** — requires a
   non-`NULL` `justifying_run_id`.

**`lifecycle.can_transition()` — the one state-machine touch in this
design**, flagged explicitly in code, here, and in the PR deliverables:
`from_state == to_state` is now structurally legal for every state,
including terminal ones. This adds **no new arc between distinct states** —
the pre-existing `ALLOWED_TRANSITIONS` graph is byte-for-byte unchanged;
verified by a test asserting every pre-existing arc (and non-arc) still
holds. The self-transition's *evidentiary requirement* (a justifying run)
is enforced in `append_transition()`, not in `can_transition()`, because
`can_transition()` has no visibility into `justifying_run_id` — it answers
"is this arc shape legal," not "is this specific write legal."

`STATES_REQUIRING_RUN` is defined once, in `lifecycle.py`, and imported by
`record.py` — a single source of truth shared by the write-time check
(`append_transition`) and the check-time invariant (`validate_registry`'s
pre-existing loop), so the two can never diverge.

---

## The Evidence Matrix (`report.py`)

`build_context(registry_path, thresholds_path, ledger_path, as_of=None) ->
ReportContext` is the **single point** where `(ledger.db, registry.yaml,
thresholds.yaml, as_of)` become a pure, fully-derived snapshot
(`ReportContext`, containing one `FamilyRow` per family: derived state,
pinned run, per-lane current runs, `is_pin_stale`, computed flags). Every
report generator consumes only this object — none of them touch the ledger
or `registry.yaml` directly. This is what makes report generation a pure
function of its inputs (acceptance criterion 4).

`normalize_as_of(as_of)`: a bare `YYYY-MM-DD` is end-of-day normalised
(`T23:59:59.999999+00:00`) before any comparison. **This is load-bearing,
not cosmetic**: lexicographic comparison of a bare date string against a
full timestamp (`"2026-06-14T09:37:56" <= "2026-06-14"`) is `False` for
every timestamp on that date, which would silently exclude the entire day
(e.g. the 2026-06-14 re-seed) from a `--as-of 2026-06-14` reconstruction —
directly contradicting the acceptance criterion that this exact command
reproduces that exact day's board. Verified by a dedicated test.

### `state_board.md` — two tables, one page

**Decision anchors** (only families with a pinned run): Family, Role,
State, Since, Decided-on (pinned run, short hash), Lane, Sharpe, DSR, BH-p,
Flags. **Evidence by lane** (every family): L0/L1/L2/L3 cells, side by
side. The top table only ever changes on a governance act (a new
transition); the bottom table changes nightly. Verified directly: adding a
simulated L2 run to a candidate leaves its L0 decision-anchor row's
evidence columns (state/lane/Sharpe/DSR/BH-p) byte-identical — only the
Flags column may react to new evidence (this is `GATE-BLOCKED`'s entire
purpose), and the new L2 evidence appears additionally in the bottom table.
This is the acceptance test the design doc calls "the acceptance test for
the entire piece of work," and it passes on the real ledger with a
simulated forward run injected.

### Rendering rules (§5.3) — implemented exactly

| Situation | Renders as | Never renders as |
|---|---|---|
| No run exists in the lane | `—` (`NO_RUN`) | anything else |
| `sharpe` NULL on an L2/L3 run (n < 2) | `insufficient` | `-` |
| `dsr_prob`/`bh_p` NULL on any run (bridge-written) | `not-computed` | `-` |
| L1 cell | `Δ vs L0` with the run's date, e.g. `+0.02 (2026-06-14)` | the absolute L1 Sharpe |
| L2/L3 cell | `Sharpe / n_trades / days` always together | a bare Sharpe |
| Row sort | `(state_rank, family_id)` only | anything a nightly job can change |

The old `_sort_sharpe` secondary sort key was removed (confirmed to be a
permanent no-op — the field does not exist anywhere in `registry.yaml` —
and a metric-shaped key sitting in the sort tuple is exactly the kind of
thing that quietly becomes load-bearing later).

### `evidence_timeline.md` — new report

Runs and transitions interleaved chronologically, per family — the audit
substrate underneath the board (adopted alternative #4 from the design's
§4 evaluation). Filtered by `as_of` like every other report.

### `ml_readiness.md` — L2/L3 lane only, plus a worklist

Validated families are now rendered on their **live** lane (L2/L3, whichever
exists), never the offline lane — structurally impossible to misread an
offline Sharpe as ML-input evidence. Candidates get a **blocked-on**
column, reusing `lifecycle.check_validated_preconditions()` verbatim (not
reimplemented) to enumerate exactly which Validated precondition each is
missing.

---

## Invariants C1–C9 (`lifecycle.validate_registry()`)

All nine implemented in `_check_invariants_c1_to_c9()`, appended after the
pre-existing checks (candidate gate_results, Validated preconditions,
`superseded_by` consistency — all unchanged). One targeted failure test per
invariant; **the real registry, post-migration, passes all nine** —
matching the design doc's own stated acceptance gate.

**A scoping decision made during implementation, documented here because
the design text does not fully resolve it:** C4/C5/C8/C9 compare against
`registry.yaml`, which is **not versioned per-date** — there is only ever
one copy. These four invariants are therefore always evaluated against the
**current** registry regardless of `--check --as-of`. The remaining
invariants (C1/C2/C3/C6/C7) are pure ledger-internal checks and **do**
respect `as_of` — "was the ledger internally consistent as of that date"
is answerable; "does today's registry.yaml match a past ledger state" is
not a question this function pretends to answer.

**C5, refined during implementation**: the design's literal text ("registry
pinned_run == justifying_run_id of the latest transition") produces a
false positive for every pure-`discovery` family, because `registry.yaml`
sets `pinned_run` for **every** family (pointing at the run that evaluated
it), including the 22 families that were never promoted and therefore have
**zero transitions**. C5 is scoped to fire **only when the family has at
least one transition** — a family's evaluation-run pointer is not the
governance decision this invariant polices, and comparing it against a
nonexistent "latest transition" would be a false alarm, not a detected gap.
Verified: the real registry produced 34 false C5 positives before this
refinement, zero after.

**C9, similarly refined**: implemented as two directions — every
`state_transitions` family_id must exist in `registry.yaml` (orphan
detection, unconditional), and every **non-`discovery`** registry family
must appear in `state_transitions` (catches a hand-edited non-default state
with no ledger backing). A literal "every registry family, including
`discovery`, must appear in transitions" would require fabricating 22
synthetic transition rows for families that were correctly never touched —
exactly the kind of invented governance history the outer task's "must not
change governance decisions" constraint forbids.

---

## Public interfaces

**`record.py`**: `LANES`, `LANE_CODE_BY_RUN_KIND`, `append_run(...,
run_kind)`, `get_latest_run(db, family_id, run_kind)`,
`get_current_run(s)_by_lane(db, as_of=None)`, `derive_state_at`,
`derive_all_states_at`, `get_all_runs`. Deleted: `get_all_latest_runs`.

**`lifecycle.py`**: `STATES_REQUIRING_RUN`, `can_transition` (extended),
`validate_registry(..., as_of=None)`.

**`report.py`**: `build_context`, `ReportContext`, `FamilyRow`,
`compute_flags`, `normalize_as_of`, `generate_state_board`,
`generate_library`, `generate_evidence_timeline` (new),
`generate_ml_readiness`, `generate_transitions`, `run_check(...,
as_of=None)`. CLI gained `--as-of`.

**`migrate_run_kind.py`**: `classify`, `apply`, `backup`,
`write_audit_note`, `main` (`--commit`-gated, dry-run default).

---

## What this PR deliberately does not do

- No systemd timer, no daily-commit automation, no heartbeat/alert wiring
  for `FWD-STALE`/`GATE-BLOCKED` (§10 PR-5 — "Operationalise").
- No new governance decision, threshold, gate, or research-logic change,
  other than the one explicitly-flagged X→X re-pin arc.
- No wiring to PR-1–4's governance receipts or contract registry/lifecycle
  — this is a fully separate subsystem (families/runs/registry.yaml vs.
  contracts/receipts), and nothing here reads or writes those tables.
- No signing, no anchoring, no `--verify-chain` equivalent for this ledger.

## Remaining work for PR-6

- §10 PR-5 operationalisation: `btcbot-report.timer` (daily, after
  `btcbot-fwd-hg.timer`), committing/publishing reports daily, wiring
  `FWD-STALE`/`GATE-BLOCKED` into the heartbeat/alert path.
- Deferred Proposal 1 (pairwise-independence enforcement, CLAUDE.md §16) is
  unrelated but still open — not touched by this PR.
- If/when a live-forward bridge (`ledger_sync.py`, the abandoned
  `fix/forward-evidence-ledger-bridge` branch) is revived, its
  `append_run()` call site will need an explicit `run_kind="forward"`
  argument — this PR's schema is ready for it, but the bridge itself does
  not exist on `main` and was out of scope here.
