# Governance Reconciliation — DSR n_trials Denominator (34 vs 38)

**Session date:** 2026-07-22. **Question:** is the canonical DSR/BH-FDR
multiple-testing denominator `34` or `38`? **Resolution: 34 is canonical.**
This memo closes the open item recorded in CLAUDE.md §16 ("Open governance
item — n_trials 34 vs 38 … deferred to the next governance session").

Conducted under the constraint: *use existing governance only; do not invent
new methodology; do not implement statistical changes beyond resolving the
documented inconsistency.*

---

## 1. Finding

There is **no genuine governance inconsistency**. `34` is the frozen
governance denominator and is used in every governance and gating path. `38`
appears only in an **exploratory discovery screen** for the DVOL family and in
one **display-only** proxy — neither of which is governance evidence, and both
of which the governance documents already flag as non-comparable.

The apparent conflict is a category difference (frozen battery vs. discovery
screen), not a contradiction requiring a value change.

---

## 2. Every document that references the denominator

### Group A — `34`, the frozen governance value (authoritative)

| Location | Statement |
|---|---|
| `CLAUDE.md:513` (Governance-frozen constraints) | "`n_trials = 34` for the full Phase B battery (DSR denominator is fixed at this value)" |
| `docs/adr/0001-infrastructure-architecture.md:658` (§17 Preserved Invariants) | "Frozen governance thresholds are untouched: `n_trials = 34` …" |
| `docs/governance/evidence_reporting_architecture.md:271, :508` | Validated bar stated with "(n_trials = 34, frozen)" |
| `docs/governance/pairwise_independence_schema_reference.md:12` | Lists `n_trials = 34` among untouched frozen values |
| `research/forward_evidence/compute_dsr_for_registration.py:9, :11, :47` | `N_TRIALS = 34` "FROZEN … do NOT change"; the registration DSR path |
| `research/forward_evidence/decision_function_ivrv.py:57–59` | "the FROZEN n_trials=34"; explicitly warns against reusing the 38 value |
| `research/forward_evidence/pre_registration_ivrv.json:72, :74` | `"n_trials": 34`; note calls the 38 value non-comparable |
| `research/forward_evidence/pre_registration_h5_vol_regime.json:141` | `n_trials=34 (frozen)` |
| `research/evidence_library/migrate_ivrv_admission.py` (×5) | `n_trials = 34` throughout the admission record |
| `research/evidence_library/migrate_ivrv_repair_local.py:97` | `n_trials=34` |
| `research/evidence_library/seed_from_scoreboard.py:154` | `n_trials = len(library)` — the library **is** the 34-signal battery |
| **`ledger.db` — all 70 runs** | `n_trials = 34` uniformly, including the only two runs with a computed DSR: `g3_atr_expansion` (0.2704) and `ivrv_vrp_spread` (0.1646) |

### Group B — `38`, the discovery-screen artifact (non-governance)

| Location | What it is |
|---|---|
| `research/evidence/run_dvol_family.py:109` | `n_trials = len(lib) + len(specs)` = 34 + 4 = 38, paired with `DSR_CONF = 0.95` (a discovery threshold, **not** the governance `min_dsr_prob = 0.50`). An exploratory screen of 4 DVOL specs beyond the battery. |
| `dvol_family_report.json` (`n_trials: 38`) | The output of that screen. A discovery artifact; never written to the ledger. |
| `research/forward_evidence/report.py:65` | `_dsr_proxy(returns, n_trials: int = 38)` — a **display-only** "quick DSR" in the H6 human-readable summary. Prints a "DSR proxy" line; never persisted; not imported by any gate path; also uses the discovery-screen `0.95` cutoff and a `sr_std*0.5` dispersion heuristic. |

### Group C — documents that reference 38 only to reject it

- `CLAUDE.md:526` — "These are **not** the `dvol_family_report.json` values (0.161 used n_trials=38 / DSR_CONF=0.95, a discovery screen)."
- `pre_registration_ivrv.json:74` — "NOT comparable to dvol_family_report.json's 0.161 (n_trials=38, DSR_CONF=0.95 discovery screen)."
- `decision_function_ivrv.py:59` — "Do NOT reuse dvol_family_report.json's dsr_prob=0.161 — that used n_trials=38 …"

---

## 3. Why 34 is canonical (existing-governance reasoning)

1. **It is explicitly frozen** in three independent authoritative places
   (CLAUDE.md §16 frozen constraints, ADR §17 invariant 8, evidence-reporting
   architecture). A freeze is not reopened without a formal governance
   session, and reopening is not the task here.
2. **The freeze is scoped to "the full Phase B battery" = 34 signals.** The
   DVOL family is, verbatim, "the **first registry entry outside** the
   original Phase B 34-signal seeding." It is a separate exploration, not an
   enlargement of the battery — so it does not, by itself, move the battery's
   denominator.
3. **Every governance surface already uses 34**: the registration DSR path,
   all pre-registrations, the IVRV admission record, and — decisively — all
   70 ledger runs, uniformly. There is nothing to reconcile in the evidence
   record; it is already consistent.
4. **The 34-vs-38 choice has had zero gating consequence.** IS DSR "does NOT
   gate registration" (`compute_dsr_for_registration.py` docstring); the
   Validated gate is evaluated on **forward** returns. For H-IVRV the IS value
   fails the `≥ 0.50` bar under **either** denominator (0.1646 at 34; 0.161 at
   38), so no admission, rejection, or promotion decision on record depends on
   which value is used.

---

## 4. On the deferred sub-question ("should the denominator grow to 38?")

CLAUDE.md §16 deferred a genuine methodological question: as more strategies
are explored, should the multiple-testing denominator grow to reflect the
larger selection set? **This memo does not adopt that change, for reasons
internal to existing governance:**

- Adopting it would **reopen a frozen constraint** — outside this task's remit
  and requiring a deliberate governance session with the freeze explicitly on
  the table.
- Growing the denominator is a **methodology change** ("do not invent new
  methodology"). The existing DSR formula (Bailey & López de Prado, in
  `governance.py:deflated_sr`) is unchanged; only the *policy* of what counts
  as a trial would change, and that policy is currently frozen at the
  34-signal battery.
- There is **no forcing event**: no Validated promotion is pending, and the
  gate that would consume the denominator recomputes on forward returns, not
  on the IS battery count.

**Recommendation:** ratify **34** as canonical and treat the "grow to 38"
question as *decided-not-to-adopt under current governance*, re-openable only
by a future session that explicitly puts the freeze on the table (e.g. if a
DVOL-family signal reaches a real Validated promotion window and the operator
judges the enlarged selection set material to its forward DSR). This is a
conservative no-change outcome, consistent with the freeze.

---

## 5. Documentation changes made (justified, non-statistical)

1. **`CLAUDE.md:527`** — the "Open governance item" bullet is updated to record
   this resolution and point here. (The frozen constraint at `:513` already
   says 34 and is unchanged.)
2. **`research/forward_evidence/report.py:65`** — a comment added to
   `_dsr_proxy` stating it is a **non-governance display proxy** whose `38` and
   `0.95` are discovery-screen parameters, and that the canonical governance
   denominator is `34` (`compute_dsr_for_registration.py`). **No number is
   changed** — the printed value is unchanged; the fix removes the ambiguity
   that a code reader might mistake the proxy's `38` for the canonical value.
   Hard-setting it to 34 was considered and rejected: the same function also
   uses the discovery-screen `0.95` cutoff and a `sr_std*0.5` heuristic, so
   changing only `n_trials` would falsely imply governance alignment while
   leaving two other non-governance parameters — labelling the whole helper as
   a proxy is the coherent resolution.

No other document is changed: Group A is already correct, and Group C already
states the distinction accurately.

---

## 6. Migration and replay implications

**None.**

- **Ledger:** every one of the 70 runs already stores `n_trials = 34`; no run
  stores 38. No migration, no backfill, no recompute. The two computed DSRs
  (`g3_atr_expansion`, `ivrv_vrp_spread`) already used 34 and stand.
- **Replay:** no governance receipt, state transition, or gate result
  references 38; replaying any recorded decision reproduces it unchanged.
- **`dvol_family_report.json` (n_trials=38) is deliberately left as-is.** It is
  a historical discovery-screen output, not governance evidence; rewriting it
  would falsify what that screen actually computed at the time and violate the
  repo's replay/provenance discipline. It never entered the ledger, so it
  cannot contaminate any governed decision.

---

## 7. One-line answer

**Canonical DSR/BH-FDR denominator = 34 (frozen).** `38` was only ever a
non-governance discovery screen; it has never been, and is not now, the
canonical value. No migration or replay impact.
