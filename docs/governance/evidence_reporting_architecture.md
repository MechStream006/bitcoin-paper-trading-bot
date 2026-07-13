# Evidence Lifecycle & Governance Reporting — Architectural Specification

**Status:** Design. **Implemented and merged 2026-07-13 (Governance Architecture
PR-5)** — see `evidence_matrix_schema_reference.md` for the implemented schema.
The §9 migration (run_kind backfill) has been run against the real ledger.db
(70 rows classified, backup + audit note retained) and the deleted
`registry.yaml: provenance` field has been removed from all 35 entries. The
real registry passes all nine invariants (§8) and the real ledger reproduces
the documented known-correct L0/L1 split exactly, both verified by test.
§10's PR-5 ("Operationalise": systemd timer, daily commit, heartbeat/alert
wiring) is explicitly NOT part of this implementation — see the schema
reference's "Remaining work" section.
**Author:** Principal Research Architect / Governance Architect
**Date:** 2026-07-13
**Governance impact:** Introduces one new ledger column, one new ledger view, one
one-time classification backfill, and a rewritten reporting layer. **Changes no
threshold, no gate, no state machine arc, and no research logic** except the
one explicitly-flagged X→X re-pin arc (§9 PR-2 item 4).

---

## 0. Executive summary

The reporting layer currently answers the question *"what is the most recent
measurement of this family?"* and then presents that answer in a table whose
headings promise something entirely different: *"what evidence justifies this
family's governance state?"*

These are not the same question, and conflating them is not a cosmetic bug. It
means the governance board is **not reproducible** (the same registry renders
differently tomorrow with no governance act having occurred), **not
point-in-time correct** (there is no way to ask what the board said on any past
date), and **lossy in the one direction that matters** (a nightly cron can erase
a human adjudication from the display).

The fix is not a better `ORDER BY`. It is to recognise that the system has **two
orthogonal axes** — the *provenance/purpose of a run* and the *lifecycle state of
a family* — and that the current schema and report collapse them into one.

The chosen architecture is the **Evidence Matrix with a Pinned-Decision Anchor**:

- Every run is classified, at write time, into exactly one immutable **evidence
  lane**.
- The governance board renders one column per lane — evidence phases sit **beside**
  each other, never on top of each other.
- A separate, visually distinct **Decision** column shows the run the family's
  state actually rests on (`pinned_run`), which no automated process may ever
  change.
- Lifecycle state is **derived from the append-only transition ledger**, not read
  from mutable `registry.yaml`; the registry is demoted to a cache and *validated*
  against the ledger.
- Every report accepts `--as-of`, reconstructing the exact board that a reviewer
  would have seen on any past date.

---

## 1. Diagnosis: what is actually broken

### 1.1 The defect as observed

`report.py:381` calls `record.get_all_latest_runs()`, which is
`MAX(run_ts) GROUP BY family_id` (`record.py:239–257`; the same logic is frozen
into the `latest_run_per_family` VIEW at `record.py:59–65`). Every report —
`state_board.md`, `library.md`, `ml_readiness.md` — renders the metric columns
from that single run.

Until the forward-evidence ledger bridge (`ledger_sync.py`, branch
`fix/forward-evidence-ledger-bridge`), every run in the ledger had
`provenance='offline-corpus'`, so "latest run" happened to coincide with
"the offline evidence" and the defect was invisible. The bridge is correct and
must ship. It is what makes the latent defect manifest.

### 1.2 The defect is worse than "overwrite"

`ledger_sync.compute_forward_metrics()` deliberately writes `oos_sharpe=None`,
`evidence_score=None`, `verdict=None`, and leaves `dsr_prob` / `bh_p` NULL —
each for a principled reason (forward evidence *is* out-of-sample, so there is no
split; the Phase-B evidence score is a discovery construct with no forward
meaning; a verdict is adjudicated by a human; DSR/BH must not be computable by a
cron).

Consequence: on the day the bridge first runs, a family that has accumulated
three closed forward trades does not merely show *different* numbers on the
board. `_fmt_f(None, ...)` returns `"-"`, so it renders:

| Family | State | Provenance | Sharpe | OOS Sh | DSR | n | Evidence | Verdict |
|---|---|---|---|---|---|---|---|---|
| h5_vol_regime | **candidate** | live-forward | +0.41 | - | - | 3 | - | - |

The discovery evidence — the 34-trial battery result that is the *sole reason
this family is a Candidate at all* — has vanished from the governance board, and
been replaced by a three-trade sample. A reviewer reading this board would
conclude the family has almost no evidence behind its state. **The board now
misrepresents the basis of a governance decision.** That is the unacceptable
outcome, and it is unacceptable regardless of which numbers happen to be larger.

### 1.3 The three concepts that have been collapsed into one

| Concept | Where it lives | Mutability | Question it answers |
|---|---|---|---|
| **Lifecycle state** | `registry.yaml: state` | mutable head | *Where is this family in the governance process?* |
| **Justifying evidence** | `registry.yaml: pinned_run` | changed only by a human governance act | *What did we decide that on?* |
| **Latest observation** | `ledger.db MAX(run_ts)` | changes nightly, unattended | *What are we measuring right now?* |

`report.py` reads column 1 from the registry, and then fills the *slot belonging
to column 2* with **column 3**. `pinned_run` is displayed in `library.md` as a
bare UUID string (`report.py:151`) — but the metrics printed next to it are the
**latest run's**, not the pinned run's. The two can already disagree today (five
families have ≥2 offline runs; `g3_atr_expansion` has three), so the library has
been capable of printing a run ID next to metrics that did not come from that run
since 2026-06-14. The forward bridge only makes the divergence loud.

### 1.4 Secondary defects found in the same code path

These must be fixed by the same work, or the new design inherits them:

- **Non-deterministic tie-break.** `JOIN ... ON r.run_ts = l.mx` emits *N* rows if
  *N* runs share a `run_ts`. `get_all_latest_runs()` then silently keeps whichever
  the cursor yielded last (`rows[d["family_id"]] = d`). Seeding scripts insert in a
  tight loop; microsecond collisions are improbable but not impossible, and
  `ivrv_vrp_spread`'s run carries a hand-written second-resolution timestamp
  (`2026-06-21T14:08:41+00:00`), which is exactly the shape that collides. There is
  no explicit tiebreak anywhere. **Violates determinism.**
- **No point-in-time reconstruction.** No report can be regenerated as of a past
  date. The `--check` output, the state board, and the ML-readiness count are all
  "now" and only "now". **Violates point-in-time correctness and reproducibility.**
- **`registry.yaml: state` is never checked against `state_transitions`.**
  `validate_registry()` checks that a state has a *pinned run*, but never that the
  state is the terminal state of the family's transition history. A hand-edit to
  the YAML that skips the ledger is currently undetectable. **Violates lineage
  integrity.**
- **`registry.yaml: provenance`** is a denormalised per-family copy of a
  *per-run* property. It is stale by construction the moment a family has runs of
  two provenances. It must become derived, or be deleted.
- **`has_live_evidence` VIEW** (`provenance != 'offline-corpus'`) will return true
  the instant the bridge writes its first row — including for a family with **zero
  closed forward trades** (`ledger_sync` writes a row with `n_trades=0`,
  `sharpe=NULL`). Any consumer treating this view as "has meaningful live
  evidence" is wrong. It answers "has a live *row*", not "has live *evidence*".

---

## 2. The two orthogonal axes

The brief lists five phases: Discovery, Offline corpus, Forward, Validated,
Production. **These are not five points on one line.** Three of them
(Discovery, Offline corpus, Forward) are properties of *evidence*. Two of them
(Validated, Production) are properties of a *family's governance standing*. The
request's framing mirrors precisely the conflation that exists in the code, and
the design must break it apart explicitly, or the same bug returns in a new form.

```
                      AXIS B — LIFECYCLE STATE (property of a FAMILY, mutable, human-driven)
                      discovery → insufficient_sample → candidate → validated
                                → promotion_ready → production_ready
                                (⊥ disqualified / rejected / retired)
                                     ▲
                                     │  a state is JUSTIFIED BY exactly one pinned run
                                     │
  AXIS A — EVIDENCE LANE ────────────┴──────────────────────────────────────────
  (property of a RUN, immutable, assigned at write, never reassigned)

    L0 discovery      L1 confirmatory     L2 forward        L3 production
    offline-corpus    offline-corpus      live-forward      mainnet
```

**Law 1 (Orthogonality).** A run never has a lifecycle state. A family never has
a provenance. Any code or report that assigns one to the other is wrong.

**Law 2 (Immutability of classification).** A run's lane is written once, at
`append_run()`, and is never recomputed, inferred at read time, or updated.
Read-time inference is what produced the current defect: `provenance` was being
used at read time as a proxy for "which phase is this", and it cannot carry that
load (L0 and L1 share a provenance).

**Law 3 (Selection is within-lane, never across-lane).**
`current run for (family, lane) = latest run in that lane`.
There is **no** operation anywhere in the system that selects a single "latest
run" for a family across lanes. That operation is the bug, and it is deleted, not
repaired.

**Law 4 (The decision anchor is sovereign).** `pinned_run` is the only run that
justifies a state. It is set exclusively by a human governance act. No scheduled
job may write it, and no report may substitute another run for it.

---

## 3. Evidence phases — full specification

### Axis A: evidence lanes (runs)

A run's lane is the pair `(provenance, run_kind)`. `provenance` keeps its
**governance-frozen** vocabulary — `{offline-corpus, live-forward, mainnet}`
(`record._VALID_PROVENANCE`; `thresholds.yaml validated.*.requires_non_offline_provenance`
depends on it). `run_kind` is **new** and orthogonal; it carries the epistemic
purpose that `provenance` cannot express.

> **Design note — why not extend `provenance`?**
> Adding `"discovery"` to `_VALID_PROVENANCE` would be the smaller diff and the
> wrong move. `provenance` is load-bearing for the Validated gate
> (`requires_non_offline_provenance`), and its vocabulary is governance-frozen
> (CLAUDE.md §16). Overloading it with purpose semantics would silently change the
> meaning of a frozen gate. A new orthogonal column changes nothing that is frozen.

#### L0 — Discovery

- **`provenance`** `offline-corpus` · **`run_kind`** `discovery`
- **Purpose.** Record the output of the hypothesis-screening battery: the *selection
  event*. This is the run that carries the multiple-testing burden (`n_trials = 34`,
  frozen).
- **Admissibility.** Offline corpus only (Binance/Deribit Parquet lake). Must carry
  `n_trials`. Exactly **one** L0 run may exist per family — a second selection event
  on the same family is a *new family*, not a new discovery run.
- **Governance meaning.** *"This is why we ever looked at this family."* It is the
  only lane whose metrics may be cited as the justification for
  `discovery → candidate`.
- **Promotion rules.** L0 metrics feed `lifecycle.check_candidate_gate()`. Passing
  → `candidate`. Failing the E3 knife-edge → `disqualified`. Failing on sample
  size → `insufficient_sample`.
- **Visibility.** Permanent. An L0 run is **never** hidden, superseded, or aged out
  of any report, in any state, forever — including for `disqualified` and `retired`
  families (a reviewer must always be able to see *why* something was killed).
- **Reporting.** Own column on the board. Always populated for every family that has
  ever been screened.

#### L1 — Offline-corpus confirmatory

- **`provenance`** `offline-corpus` · **`run_kind`** `confirmatory`
- **Purpose.** Re-derivation of L0 metrics after a code fix, an engine change, a
  corpus rebuild, or an admission decision (e.g. the 2026-06-14 re-seed; the ATR
  and IVRV admission runs). It answers: *does the discovery result still reproduce?*
- **Admissibility.** Offline corpus only. Must be a re-run of the **same** locked
  decision function on the **same** hypothesis; a changed decision function is a new
  family, not a confirmatory run. `fn_hash` and `git_sha` are mandatory.
- **Governance meaning.** *"The discovery result does / does not still reproduce
  under current code."* It is a **reproducibility check, not new evidence.** It adds
  no statistical power (same data), and it **must never be treated as independent
  confirmation.** It cannot, by itself, promote anything.
- **Promotion rules.** None. An L1 run can *demote* (if it fails to reproduce L0 →
  the family is flagged, and a human decides between `disqualified` and a re-pin)
  but can never promote.
- **Visibility.** Latest L1 run shown on the board next to L0. All prior L1 runs
  retained in the timeline.
- **Reporting.** Own column, rendered as a **delta against L0**, not as an absolute
  — because the only interesting question about a confirmatory run is *whether it
  moved*. A material drift (see §5.4) raises a `REPRO-DRIFT` flag.

#### L2 — Forward (live-forward)

- **`provenance`** `live-forward` · **`run_kind`** `forward`
- **Purpose.** Out-of-sample evidence accumulated in real time by the locked
  decision function, from data that did not exist when the family was screened.
  This is the **only lane that accumulates genuinely new statistical power.**
- **Admissibility.** Written by `ledger_sync` from `evidence_forward.db`. Only
  **closed** trades (`exit_date <= as_of`), so no unrealised P&L can leak in.
  Requires a pre-registration (`pre_registration_*.json`) and a locked `fn_hash`
  predating the first observation. **Sourced from the offline Parquet corpus of
  live public data (Binance/Deribit) — not from Delta testnet.**
  *Testnet data is pipeline-validation only and is inadmissible as evidence in any
  lane* (CLAUDE.md §11, research-integrity boundaries).
- **Governance meaning.** *"Does it work on data nobody has seen?"* This lane, and
  only this lane, satisfies `requires_non_offline_provenance` at the Validated gate.
- **Promotion rules.** `candidate → validated` requires: pinned run in **L2 or L3**;
  `dsr_prob ≥ 0.50`; `bh_p ≤ 0.10` (n_trials = 34, frozen); pairwise `r ≤ 0.70` vs
  all Validated families (CLAUDE.md §16, Proposal 1, deferred-but-specified).
  **DSR and BH-p are NULL on every bridge-written L2 run by design** — a cron
  cannot open a gate. A human must compute them and re-pin. This is a feature.
- **Visibility.** Latest L2 run on the board; every L2 run in the timeline (the
  L2 series *is* the forward equity curve of the governance process).
- **Reporting.** Own column, always annotated with **n_trades and window length** —
  an L2 Sharpe from 3 trades and one from 300 must never render identically.
  `sharpe = NULL` (n < 2) renders as `insufficient`, **never** as `-`.

#### L3 — Production (future)

- **`provenance`** `mainnet` · **`run_kind`** `production`
- **Purpose.** Evidence from real orders against a live venue, reconciled against
  actual fills.
- **Admissibility.** Requires `requires_live_reconciliation` — every ledger trade
  must reconcile to an exchange fill. Unreconciled ⇒ inadmissible, full stop.
- **Governance meaning.** *"Does it survive contact with the market?"* The only lane
  that captures slippage, partial fills, and adverse selection.
- **Promotion rules.** `promotion_ready → production_ready` requires ≥30 forward
  trades and mainnet provenance. Decay in L3 demotes to `validated` (already an
  allowed arc: `lifecycle._BASE_TRANSITIONS`).
- **Visibility.** Column reserved and rendered as `—` until the first mainnet run
  exists. **The column is built now, empty.** Retrofitting a column into a governance
  board later is how boards lose history.
- **Reporting.** Divergence between L2 and L3 (paper vs. real) is the single most
  important number the platform will ever produce, and gets a dedicated
  `execution_slippage` panel when the lane opens.

#### Lane extensibility (ML / DL / RL)

Future lanes — `model-backtest`, `rl-sim`, `ensemble` — are added as new
`run_kind` values. Because selection is within-lane (Law 3), **adding a lane adds
a column, never a code path, and can never disturb an existing lane's numbers.**
This is the property that makes the design scale to the ML phases. A model is
just another family whose evidence happens to arrive in different lanes.

### Axis B: lifecycle states (families)

Unchanged. `lifecycle.VALID_STATES` and `ALLOWED_TRANSITIONS` are governance-frozen
and this design **does not touch them.** What changes is only where the report
*reads state from*:

| | Today | Specified |
|---|---|---|
| Source of truth for state | `registry.yaml: state` (mutable file) | `state_transitions` (append-only ledger) |
| `registry.yaml: state` | truth | **cache / head pointer**, validated against the ledger |
| Divergence between them | undetectable | **hard `--check` failure** |

**Validated** and **Production-ready** are *states*, not evidence phases. They are
reported as positions on Axis B, and the evidence that justifies them is the
pinned run on Axis A. The board shows both, in different columns, and never lets
one impersonate the other.

---

## 4. Alternatives evaluated

| # | Design | Verdict |
|---|---|---|
| 1 | **Latest per provenance** | **Rejected — insufficient.** Strictly better than today, and it is a *subset* of the chosen design. But `provenance` cannot separate L0 from L1 (both `offline-corpus`), so a 2026-06-14 re-seed still overwrites the discovery run it was meant to confirm. It also still has no place to show the *decision* run, so the pinned/latest divergence (§1.3) survives untouched. It fixes the symptom the bridge exposed and leaves the disease. |
| 2 | **Latest per lifecycle stage** | **Rejected — category error.** Runs do not have lifecycle stages; families do (Law 1). Implementing this requires stamping each run with the family's state *at write time*, which (a) is a denormalised copy of mutable data, immediately stale, (b) has no defined value for runs written while a state change is in flight, and (c) makes the board change retroactively when a family is demoted. It would encode the exact conflation this design exists to eliminate. |
| 3 | **Separate report sections per phase** | **Rejected as primary; adopted as secondary.** Lossless and simple. But the single most common review question — *"the offline Sharpe was 0.90; what is it doing live?"* — becomes a manual join across pages, performed by a human, under time pressure, on the day a promotion is being argued. That is precisely when a reviewer must not be doing clerical work. Retained as the per-family drill-down (`library.md`), which is the right shape for depth. |
| 4 | **Evidence timeline** (append-only chronological log per family) | **Necessary but not sufficient — adopted as a component.** Perfect lineage, zero at-a-glance governance value. A reviewer cannot answer "which families are ready?" by reading a log. Adopted as `evidence_timeline.md`, the audit substrate underneath the board — not the board. |
| 5 | **Multi-column governance board** (lanes as columns) | **Chosen as the core.** Comparison across phases is the primary act of research governance, and a matrix makes it O(1) per family, visually, with no cross-referencing. Loses nothing (every run stays in the ledger and the timeline). Scales by adding columns. |
| 6 | **Evidence Matrix + Pinned-Decision Anchor + ledger-derived state + as-of reproducibility** | **CHOSEN.** = (5) as the board, (4) as the substrate, (3) as the drill-down, plus the two things none of 1–5 supply on their own: a **decision column** that no automation may touch, and **point-in-time reconstruction**. |

### Why #6 wins

Designs 1–5 each fix *display*. Only #6 fixes **epistemics**, and the brief's
requirements are epistemic requirements:

- *No loss of historical lineage* — the ledger is already append-only; the failure
  was that the **report** discarded lineage at read time. Within-lane selection
  (Law 3) means no lane can ever erase another. The timeline preserves everything
  the board summarises.
- *Provenance preserved* — provenance becomes a **structural coordinate** of the
  board (which column a number lives in), not a metadata string in a cell that a
  reader may or may not notice has changed.
- *Deterministic* — within-lane selection with an explicit total order
  `(run_ts DESC, run_id DESC)`; an invariant assertion of exactly one current run
  per `(family, lane)`; and a byte-identical report for identical inputs.
- *Point-in-time correct* — `--as-of` filters runs **and** transitions, and derives
  state from the transition ledger. The board of any past date is reconstructible
  from the ledger alone.
- *Reproducible* — the report becomes a pure function of
  `(ledger.db, registry.yaml, thresholds.yaml, as_of)`. Today it is a function of
  wall-clock time, which is why a cron can change the board.
- *Scalable for ML/DL/RL* — new evidence sources become new lanes/columns, with a
  hard structural guarantee (Law 3) that they cannot corrupt existing evidence.
  A model is a family; a backtest is a lane.
- *No ambiguity for reviewers* — the board never shows a number without showing
  which lane produced it, how many observations are behind it, and whether it is
  the number the state was decided on. **The pinned-decision anchor makes the
  question "what does this state rest on?" unaskable-and-unanswered.**

---

## 5. Reporting architecture

### 5.1 Artifacts

| Artifact | Grain | Audience | Answers |
|---|---|---|---|
| `state_board.md` | family × lane (**the matrix**) | governance review | *Where is everything, and does the evidence agree with itself?* |
| `library.md` | family (deep, all lanes, all flags) | analyst drill-down | *Everything known about one family.* |
| `evidence_timeline.md` | run + transition, chronological | audit | *What happened, in what order, and who did it?* |
| `ml_readiness.md` | Validated set | ML gate | *Do we have enough independent validated families to start modelling?* |
| `transitions.md` | transition | audit | (exists; unchanged) |
| `report.py --check` | invariants | CI / pre-promotion | *Is the registry consistent with the ledger?* |

### 5.2 `state_board.md` — the Evidence Matrix

```
# Evidence Library — Governance Board
_Generated 2026-07-13 06:00 UTC · as-of 2026-07-13 · ledger 71 runs / 13 transitions_

## Decision anchors                     <- what each state RESTS ON (never automated)

| Family          | Role    | State     | Since      | Decided on (pinned) | Lane | Sharpe | DSR  | BH-p | Flags |
|-----------------|---------|-----------|------------|---------------------|------|--------|------|------|-------|
| h5_vol_regime   | feature | candidate | 2026-06-13 | 3f2a91c4            | L0   | +0.13  | —    | —    |       |
| ivrv_vrp_spread | feature | candidate | 2026-06-19 | b98c9685            | L0   | +0.27  | —    | —    |       |
| g3_atr_expansion| edge    | candidate | 2026-06-25 | 7d10bb2e            | L1   | +0.48  | —    | —    | ⚠ PIN-STALE |

## Evidence by lane                     <- what we CURRENTLY MEASURE, per lane, side by side

| Family          | L0 Discovery        | L1 Confirmatory   | L2 Forward             | L3 Production | Flags        |
|                 | Sharpe / OOS / n    | Δ vs L0           | Sharpe / n / days      |               |              |
|-----------------|---------------------|-------------------|------------------------|---------------|--------------|
| h5_vol_regime   | +0.13 / +0.68 / 30  | +0.00 (2026-06-14)| +0.41 / 3 / 24d        | —             | ⚠ FWD-THIN   |
| ivrv_vrp_spread | +0.27 / +0.50 / 61  | —                 | insufficient / 1 / 24d | —             | ⚠ FWD-THIN   |
| g3_atr_expansion| +0.48 / +0.67 / 152 | +0.02 (2026-06-25)| -0.22 / 8 / 24d        | —             | ⚠ FWD-CONTRA |
```

Two tables, one page. The top table is *governance* — it never moves unless a
human moves it. The bottom table is *observation* — it moves nightly. **The
nightly job can only ever change the bottom table.** That single structural
property is the entire fix, made visible in the layout.

### 5.3 Rendering rules (normative)

1. A cell renders `—` **only** when no run exists in that lane. Never for a NULL
   metric on an existing run.
2. A NULL metric on an existing run renders with its **reason**:
   `insufficient` (n < 2), `not-computed` (DSR/BH on a bridge run), `n/a`
   (`oos_sharpe` in L2 — forward evidence *is* OOS). Never `-`. **A reader must
   never be unable to distinguish "no evidence" from "evidence, statistic not
   defined".** This is the single most important rendering rule in the document.
3. Every L2/L3 cell carries `n_trades` and window length inline. A Sharpe without
   a sample size is not a number, it is a rumour.
4. L1 renders as **Δ vs L0**, not absolute (§3, L1).
5. Sort: state rank (as today), then `family_id`. **Never sort by a metric that a
   nightly job can change** — a board whose row order changes overnight cannot be
   diffed by a human.
6. Reports are byte-identical for identical `(ledger, registry, thresholds, as_of)`.
   The generation timestamp is the only permitted non-determinism and is confined to
   a single header line.

### 5.4 Flags (computed, informational, never gating)

| Flag | Condition | Meaning |
|---|---|---|
| `PIN-STALE` | pinned run is not the current run in its own lane | The state rests on a superseded run. Re-pin, or justify. |
| `REPRO-DRIFT` | \|L1.sharpe − L0.sharpe\| > 0.10 | The discovery result does not reproduce. **Investigate before any promotion.** |
| `FWD-CONTRA` | sign(L2.sharpe) ≠ sign(pinned.sharpe), n ≥ min_trades | Forward evidence contradicts the decision. |
| `FWD-THIN` | L2 n_trades < `MIN_TRADES_FOR_SHARPE` (2) or < a reporting floor | Forward Sharpe exists but is not yet interpretable. |
| `FWD-STALE` | latest L2 run_ts older than 3 days | **Collection has stalled.** Operational alarm — the absence of evidence is itself evidence of a broken pipeline. |
| `GATE-BLOCKED` | state=candidate, L2 exists, but `dsr_prob` IS NULL | Forward evidence is accruing but DSR/BH have never been computed — the family **cannot** advance. Makes the deliberate cron-cannot-promote design *visible* rather than merely true. |

Flags never change a state. They direct human attention. `GATE-BLOCKED` in
particular converts a silent structural fact ("a cron may not open a gate") into a
standing, visible reminder of the one human act that is required.

### 5.5 `ml_readiness.md`

Must consume the **Validated** set and, for each family, render the **L2/L3 lane**
explicitly — never the offline lane. It is structurally impossible to satisfy the
ML gate with offline metrics, and the dashboard must make that impossible to
*misread* as well. Add a "blocked on" column enumerating exactly which Validated
precondition each Candidate is missing (`dsr_prob` NULL, forward n too low,
pairwise-r not recorded, …). This turns the dashboard from a scoreboard into a
worklist.

---

## 6. Data model changes

### 6.1 `runs.run_kind` (new column)

```sql
ALTER TABLE runs ADD COLUMN run_kind TEXT;   -- one-time; see §9
```

- Vocabulary: `{discovery, confirmatory, forward, production}` — extensible.
- **NOT NULL for all new writes**, enforced in `record.append_run()` (which gains a
  required `run_kind` parameter, validated against a `_VALID_RUN_KIND` set exactly
  as `provenance` is today).
- `(provenance, run_kind)` pairs are validated against a frozen lane table. An
  `offline-corpus` + `forward` run is a contradiction and must raise.
- **Write-once.** No code path may ever `UPDATE runs SET run_kind`. After the
  one-time backfill (§9), the column is immutable, exactly like `provenance`.

### 6.2 New view

```sql
CREATE VIEW current_run_per_lane AS
    SELECT * FROM (
        SELECT r.*,
               ROW_NUMBER() OVER (
                   PARTITION BY family_id, provenance, run_kind
                   ORDER BY run_ts DESC, run_id DESC     -- explicit total order
               ) AS rn
        FROM runs r
    ) WHERE rn = 1;
```

The explicit `run_id` tiebreak removes the non-determinism of §1.4. This view is
the **only** sanctioned way to select a "current" run.

### 6.3 Deprecations

- `latest_run_per_family` VIEW and `record.get_all_latest_runs()` — **delete.**
  Not deprecate: *delete*. They are the bug. Leaving them in the codebase leaves a
  loaded gun on the table for the next author who wants "the latest run".
  `get_latest_run(family_id)` may remain **only** if it gains a required `lane`
  argument.
- `has_live_evidence` VIEW — redefine to require `n_trades > 0`, or delete. As
  written it will report "has live evidence" for a family with an empty forward
  row (§1.4).
- `registry.yaml: provenance` (per-family) — **delete.** It is a denormalised copy
  of a per-run property and is stale by construction. `--check` should reject it if
  present.

### 6.4 What does *not* change

`thresholds.yaml` (frozen), `lifecycle.VALID_STATES`, `ALLOWED_TRANSITIONS`,
`check_candidate_gate()`, `check_validated_preconditions()`, `n_trials = 34`,
`_VALID_PROVENANCE`, all of `research/` signal and engine logic, the append-only
invariant on `runs` / `gate_results` / `state_transitions` values.

---

## 7. Point-in-time correctness

`--as-of YYYY-MM-DD` (default: now) must:

1. Filter `runs` to `run_ts <= as_of` **and** `state_transitions` to `ts <= as_of`.
2. **Derive** each family's state as the `to_state` of its last transition at or
   before `as_of` — *not* read `registry.yaml: state`.
3. Derive the pinned run as of that date. This requires the ledger to record
   re-pins. Today, `pinned_run` lives **only** in mutable YAML, so **a past board
   cannot be reconstructed at all.** Fix: `state_transitions.justifying_run_id`
   already exists and is exactly this datum — require it non-NULL for every
   transition into a run-requiring state, and record a re-pin as an explicit
   ledger event. Without this, `--as-of` is decorative.
4. Compute flags from the as-of snapshot only.

The current-day board is then just `--as-of now`, i.e. **there is one code path,
and the historical path is the one that is exercised daily.** A reconstruction
path that is only used during audits is a reconstruction path that does not work.

---

## 8. Invariants for `report.py --check`

Existing checks are retained. New, all hard failures:

| # | Invariant |
|---|---|
| C1 | Every run has a valid `run_kind`, and `(provenance, run_kind)` is a legal lane. |
| C2 | Exactly one current run per `(family, lane)` — asserts the tiebreak is total. |
| C3 | At most one `L0/discovery` run per family. |
| C4 | `registry.yaml: state` == state derived from `state_transitions`. **Detects hand-edits that bypass the ledger.** |
| C5 | `registry.yaml: pinned_run` == `justifying_run_id` of the family's latest transition. **Detects a re-pin that was never recorded.** |
| C6 | Every transition into `{candidate, validated, promotion_ready, production_ready}` has a non-NULL `justifying_run_id` that exists in `runs`. |
| C7 | `state=validated` ⇒ pinned run's lane ∈ {L2, L3}. (Today's provenance check, restated on the lane axis.) |
| C8 | No family has `registry.yaml: provenance` set (deleted field, §6.3). |
| C9 | Every family in `registry.yaml` appears in `state_transitions`, and vice versa. |

C4 and C5 are the load-bearing additions: they make `registry.yaml` a **verifiable
cache** rather than an unverifiable second source of truth.

---

## 9. Migration (requires Rohith's explicit sign-off)

`run_kind` must be backfilled for the 70 existing runs. This is the **one point in
this design that touches existing ledger rows**, and it deserves to be called out
rather than buried.

- **What is touched:** the new `run_kind` column only.
- **What is never touched:** every evidence value — `sharpe`, `oos_sharpe`,
  `dsr_prob`, `bh_p`, `metrics_json`, `provenance`, `run_ts`, `verdict`. The
  append-only invariant on *evidence* is preserved absolutely. What is being added
  is a **classification of rows that already exist**, not a revision of them.
- **Deterministic backfill rule:**
  - earliest `offline-corpus` run per family (by `run_ts`, `run_id` tiebreak) →
    `discovery`;
  - every later `offline-corpus` run → `confirmatory`;
  - any `live-forward` run → `forward`; any `mainnet` run → `production`.
  - This exactly reproduces the known history: the 2026-06-13 bootstrap seed is L0;
    the 2026-06-14 re-seed and the 2026-06-21 IVRV / 2026-06-25 ATR admission runs
    are L1.
- **Script properties:** idempotent; refuses to overwrite a non-NULL `run_kind`;
  prints a full before/after classification table and requires `--commit` to write;
  takes a `ledger.db` backup first.
- **Audit record:** the migration writes a note recording the rule, the script's
  `git_sha`, and the row count, so the classification's own provenance is on the
  record.

> If strict append-only on the physical table is judged inviolable, the alternative
> is an append-only `run_classification(run_id, run_kind, ts, actor)` side table
> and a view that joins it. It is strictly more machinery for the same semantics
> and one extra join on every read. **Recommendation: take the ALTER.** But this is
> Rohith's call, not the implementer's, and it must be made before work starts.

---

## 10. Implementation brief for Sonnet

**Do not** modify: `thresholds.yaml`; `lifecycle.VALID_STATES`;
`ALLOWED_TRANSITIONS`; `check_candidate_gate()`; the body of
`check_validated_preconditions()` (its *call sites* may change); `n_trials`;
`_VALID_PROVENANCE`; anything under `research/evidence/`,
`research/ingest/`, or any `decision_function_*.py` / `runner_*.py`.
**No research logic changes. No auto-promotion. No registry auto-edit.**

Land in **five sequential PRs**, each independently revertible. Do not proceed to
the next until the previous is green.

---

### PR-1 — Schema: lanes (no behaviour change)

1. `record.py`
   - `_VALID_RUN_KIND = {"discovery", "confirmatory", "forward", "production"}`.
   - `_VALID_LANES` — the frozen `(provenance, run_kind)` pairs from §3.
   - `append_run(..., run_kind: str)` — **required** arg; validate the kind and the
     lane pair; raise `ValueError` on violation (mirror the existing `provenance`
     validation exactly).
   - Add the `current_run_per_lane` view (§6.2) to `_SCHEMA_SQL`.
   - Add `get_current_run(db, family_id, lane)` and
     `get_current_runs_by_lane(db) -> {family_id: {lane: run}}`.
   - **Delete** `get_all_latest_runs()` and the `latest_run_per_family` view.
     Update every caller. Nothing may remain that selects across lanes.
   - Redefine `has_live_evidence` to require `n_trades > 0`.
2. `migrate_run_kind.py` — the §9 backfill. Idempotent, `--commit`-gated, backs up
   first, prints the classification table, refuses to overwrite non-NULL.
3. Update `ledger_sync.append_run(...)` and every `migrate_*_admission.py` /
   `seed_from_scoreboard.py` call site to pass an explicit `run_kind`.
4. Tests: lane validation; illegal lane pairs rejected; `current_run_per_lane`
   returns exactly one row per `(family, lane)`; **an explicit test that two runs
   with an identical `run_ts` produce a deterministic, stable winner** (the §1.4
   defect); backfill idempotency; backfill produces the known-correct L0/L1 split
   for the real 70-run history.

**Gate:** `report.py --check` still passes; `ledger.db` classification table matches
the expected split exactly.

---

### PR-2 — Point-in-time substrate

1. Require `justifying_run_id` on every transition into a run-requiring state
   (validate in `record.append_transition()`; back-compat: existing NULL bootstrap
   rows are grandfathered by `ts < 2026-07-01` and **only** by that).
2. `record.derive_state_at(db, family_id, as_of) -> (state, pinned_run_id, ts)` —
   pure ledger derivation, no YAML.
3. `record.derive_all_states_at(db, as_of)`.
4. A **re-pin** becomes a first-class recordable event. Simplest form that preserves
   the existing state machine: a self-transition `(X → X)` carrying the new
   `justifying_run_id` and a reason. `can_transition()` must be extended to permit
   `X → X` **only** when a `justifying_run_id` is supplied. **This is the one
   state-machine touch in the design; it adds no new arc between distinct states.**
   Flag it explicitly in the PR description for Rohith's review.
5. Tests: state-at-date reconstruction over the real 13-transition history;
   re-pin recorded and reconstructible; `X → X` without a `justifying_run_id`
   rejected; every other arc unchanged.

---

### PR-3 — Reports: the Evidence Matrix

1. `report.py`
   - `--as-of YYYY-MM-DD` (default now), threaded through every generator.
   - `generate_state_board()` → the two-table layout of §5.2, rendered from
     `current_run_per_lane` + ledger-derived state, obeying every rule in §5.3.
   - `generate_library()` → all lanes per family, all flags, decision anchor first.
   - `generate_evidence_timeline()` → **new** `evidence_timeline.md`: runs and
     transitions interleaved chronologically per family.
   - `generate_ml_readiness()` → Validated set rendered on the **L2/L3 lane**, plus
     the "blocked on" worklist column (§5.5).
   - `compute_flags()` — pure function, §5.4, fully unit-tested.
2. Tests: **golden-file** tests for every report (byte-identical output for a fixed
   fixture ledger — this is how determinism stops being an aspiration); every §5.3
   rendering rule, especially **rule 2** (`—` vs `insufficient` vs `not-computed`
   vs `n/a` — assert all four render distinctly); every flag's trigger and
   non-trigger; an `--as-of` test proving a past board reconstructs exactly.

**Gate:** generate the board against the real ledger *with* a simulated L2 run
present, and confirm by inspection that the discovery row is **still fully visible**
and that no offline number has moved. That is the acceptance test for the entire
piece of work.

---

### PR-4 — Invariants

1. Implement C1–C9 (§8) in `validate_registry()`.
2. Delete `registry.yaml: provenance` from all 35 entries; C8 enforces its absence.
3. `--check` gains `--as-of`.
4. Tests: one targeted failure case per invariant; the real registry passes all nine.

---

### PR-5 — Operationalise

1. `btcbot-report.timer` — daily, **after** `btcbot-fwd-hg.timer` (01:00 UTC), so
   the board reflects the night's collection. Follow the `SuccessExitStatus`
   pattern established by `btcbot-certify.service`.
2. Reports are regenerated and committed (or published) daily; `--check` failure is
   loud.
3. Wire `FWD-STALE` and `GATE-BLOCKED` into the existing heartbeat/alert path — a
   stalled collector and an un-computed DSR are both **silent** failures today, and
   both are exactly the kind of quiet stall that costs weeks.

---

## 11. Acceptance criteria

The work is done when **all** of these hold:

1. A forward run landing in the ledger **cannot** change any offline number on any
   report. Structurally cannot — not "does not currently".
2. The board shows, for every family, what it is, what its state rests on, and what
   every lane currently measures — with no cell whose meaning depends on a value in
   another cell.
3. `report.py --as-of 2026-06-14` reproduces the board of 2026-06-14 from the ledger
   alone.
4. Two invocations with identical inputs produce byte-identical reports.
5. `--check` fails if `registry.yaml` disagrees with the ledger in any respect.
6. A new evidence lane can be added by adding one `run_kind` value and one column,
   with no change to any selection logic.
7. No number that a human adjudicated has been changed by a machine.

Criterion 7 is the one this whole document exists to guarantee.
