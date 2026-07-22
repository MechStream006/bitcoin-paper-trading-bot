# Proposal 1 — Validated Family Independence Enforcement

Implements the design frozen in **CLAUDE.md §16** ("Deferred — Proposal 1:
Pairwise independence enforcement"), whose stated implementation trigger was:
*"As soon as a first Candidate enters the realistic Validated promotion
window… implement at least one full `report.py --check` cycle before the
promotion attempt."*

**Introduces no new governance rule.** `max_pairwise_r = 0.70` has been
declared in `thresholds.yaml` under `validated.edge` and `validated.feature`
since 2026-06-13 and was, until now, **enforced nowhere**. This work makes the
existing rule structural. The threshold value, `n_trials = 34`,
`min_dsr_prob = 0.50`, and `max_bh_p = 0.10` are untouched.

---

## 1. Architecture

Three layers, matching the frozen spec's six steps:

```
  STORAGE            record.pairwise_r table          (spec 1)
                     record.append_pairwise_r()       (spec 2)
                     record.get_pairwise_r()

  GATE LOGIC         lifecycle.check_validated_independence()   (spec 3)
                             │
                             ▼
                     lifecycle.check_validated_preconditions()  (spec 4)
                             │
             ┌───────────────┴────────────────┐
             ▼                                ▼
  REGISTRY GATE                    PROMOTION GATE
  lifecycle.validate_registry()    transition_governance.validate_transition()
  (spec 5)                         TG-PRE
  → report.py --check              → promote_with_receipt()
  detects an inconsistent          REFUSES the promotion
  registry after the fact          before it happens
```

### 1.1 One deviation from the literal spec, and why

The frozen spec (written 2026-06-13) names two wiring points: step 4
(`check_validated_preconditions`) and step 5 (`validate_registry`). It does
**not** mention `transition_governance.validate_transition` — because
`transition_governance.py` **did not exist yet**. It was created by PR-8 on
2026-07-17, four weeks after the spec was frozen.

`validate_registry` alone would only catch a violation **after** a family had
already been promoted, when `report.py --check` next ran. The requirement is
to *"enforce `max_pairwise_r` before any family can be promoted to
Validated."* Wiring TG-PRE is therefore the faithful application of the
spec's own intent to the architecture as it now exists, not an expansion of
it. Both wiring points are implemented; the spec's two are unchanged.

### 1.2 Where the peer set comes from — deliberately not unified

| Caller | Peer source | Why |
|---|---|---|
| `validate_registry()` | **registry.yaml** | It validates registry claims: if the registry asserts two families are simultaneously Validated, they must be independent of each other |
| `validate_transition()` | **ledger-derived** (`derive_all_states_at`) | PR-8 made ledger-derived state authoritative for transitions, and `family_meta` is optional — a caller passing no registry must not thereby promote into an unchecked peer set |

These answer different questions and are intentionally kept separate.

---

## 2. Files changed

| File | Change |
|---|---|
| `research/evidence_library/record.py` | `pairwise_r` table + indexes + append-only triggers; `append_pairwise_r()`; `get_pairwise_r()`; `import math` |
| `research/evidence_library/lifecycle.py` | `check_validated_independence()`; three optional params on `check_validated_preconditions()`; wiring in `validate_registry()` |
| `research/evidence_library/transition_governance.py` | TG-PRE now passes the pairwise substrate and ledger-derived peer set |
| `tests/test_pairwise_independence.py` | **New** — 41 tests |
| `docs/governance/pairwise_independence_schema_reference.md` | **New** — this document |

---

## 3. Schema

```sql
CREATE TABLE IF NOT EXISTS pairwise_r (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    family_id_a TEXT NOT NULL,
    family_id_b TEXT NOT NULL,
    r           REAL NOT NULL,
    method      TEXT NOT NULL,
    actor       TEXT NOT NULL DEFAULT 'operator',
    CHECK (family_id_a < family_id_b)   -- canonical ordering
);
```

Verbatim from the frozen spec, plus two additions consistent with existing
precedent: an index on `(family_id_a, family_id_b)`, and `RAISE(ABORT)`
triggers blocking `UPDATE`/`DELETE` — the spec says "append-only (no UPDATEs,
no DELETEs)", and DB-level triggers are how this codebase enforces that
(`governance_receipts`, `contract_registry`), rather than trusting every
caller to route through Python.

**Canonical ordering** means a pair has exactly one representation, so a
lookup can never miss a measurement because it was recorded "the other way
round". `append_pairwise_r()` **refuses** unsorted input rather than silently
sorting it: swapping the arguments also swaps the meaning the caller believes
it is recording, and refusing surfaces a caller bug that reordering would hide.

---

## 4. Three fail-closed behaviours

| Condition | Result |
|---|---|
| Validated peer with **no recorded pair** | **FAIL** |
| `abs(r) > max_pairwise_r` | **FAIL** |
| Recorded `r` is NaN | **FAIL** |
| `r == max_pairwise_r` exactly | PASS (it is a ceiling) |
| No Validated peers | PASS (vacuous) |
| No `max_pairwise_r` declared for the role | PASS (nothing to enforce) |

### 4.1 Missing coverage is a hard failure — the one judgment call

The spec says the check "will fail if any pair exceeds `max_pairwise_r`". It
does not state what a *missing* pair means. Left silent, missing coverage
would pass — and a rule that applies only when the operator volunteers data
can always be satisfied by volunteering nothing. That is not enforcement, and
it would not satisfy the requirement.

Treating missing coverage as failure follows **R1's precedent exactly**
(CLAUDE.md §16): *"NULL or NaN for either governance stat is a hard failure.
There is no silent bypass."* The spec's own operator workflow (step 6) already
requires computing correlation "against **all** existing Validated-family
returns", so a missing pair means the workflow was not followed.

### 4.2 Comparison is on `|r|`

`max_pairwise_r`'s stated purpose is that a *"signal must be independent of
other validated signals"*. A family at `r = -0.95` to an existing Validated
family is a near-perfect inverse duplicate carrying the same risk factor with
the sign flipped — not independent in any sense the threshold protects.
Comparing raw `r` would admit exactly that case. This is the standard reading
of a correlation ceiling, not a change of statistical methodology.

---

## 5. Determinism and replay safety

- **Latest-per-pair** wins, with an explicit `(ts, id)` tiebreak — the same
  pattern PR-5 introduced for `current_run_per_lane`, so two rows sharing a
  timestamp still resolve to one deterministic winner.
- **`as_of` is honoured** in `validate_registry`'s path: a historical
  `--check --as-of` evaluates the independence evidence that existed at that
  instant, not evidence recorded later. Re-running a past check returns the
  past answer.
- **Append-only** means a measurement is never edited, so a replayed decision
  can always resolve the exact row it saw.

---

## 6. Operator workflow (spec step 6, unchanged)

Before promoting a Candidate to Validated:

```python
import record

# 1. Compute correlation of this family's returns against EVERY currently
#    Validated family. Pass the lexicographically lesser id first.
a, b = sorted([candidate_id, validated_peer_id])
record.append_pairwise_r(
    "research/evidence_library/ledger.db",
    a, b, r=0.31,
    method="pearson_daily_returns",   # required: state how it was computed
    actor="rohith",
)

# 2. Repeat for every Validated peer. Any peer left unmeasured blocks
#    the promotion (§4.1).
```

Then promote as usual. `promote_with_receipt()` refuses if any pair exceeds
the ceiling or is missing; `report.py --check` reports the same violations for
an already-Validated family.

---

## 7. Audit evidence

Every rejection names the specific pair, the observed value, when it was
measured, by what method, and the ledger row id:

```
fam_b: pairwise |r|=0.9100 against Validated family 'fam_a' exceeds
       max_pairwise_r=0.7 (measured 2026-07-19T00:00:00Z via
       pearson_daily_returns, row id=3)
```

Acceptance is recorded too: TG-PRE's `observed` field reads
`"DSR/BH-FDR/provenance satisfied; independent of N Validated peer(s)"`, and
that check block is carried in the `family.promote` governance receipt — so a
promotion's independence basis is reconstructable from the hash-chained record
years later, per `governance_receipts.md`'s objective.

---

## 8. Migration impact

**None requiring action.** `pairwise_r` is created by the existing idempotent
schema-ensure (`record.init_db()` / `evidence_governance.init_db()`, which
`report.py run_check()` already calls — PR-6 precedent, *"CREATE TABLE IF NOT
EXISTS only, no ALTER"*). No migration script, no backfill, no `--commit` step.

There is nothing to backfill: **zero families are Validated today**, so there
are no historical pairs that should have been measured. The gate is currently
vacuous, which is precisely why CLAUDE.md §16 says to land it *before* the
first promotion rather than on promotion day.

Verified against the real ledger, using the Item 3/5 backup as the
pre-change reference:

| Table | Before | After |
|---|---|---|
| `runs` | 70 | 70 |
| `gate_results` | 142 | 142 |
| `state_transitions` | 13 | 13 |
| `governance_receipts` | 26 | 26 |
| `pairwise_r` | *(absent)* | 0 |

Full row contents of all four pre-existing tables compared **identical**;
`verify_chain()` → `ok=True, n_receipts=26`; `report.py --check` → exit 0.

---

## 9. Rollback

The gate is inert while no family is Validated, so rollback is low-urgency.

| Scope | Action |
|---|---|
| Disable the gate only | Remove `max_pairwise_r` from `thresholds.yaml validated.<role>` — the check becomes a no-op (§4). No code change. **This is the fastest lever.** |
| Revert the code | `git revert` the commit. `pairwise_r` remains as an unused empty table; nothing reads it. |
| Remove the table | Not recommended and not required. It is append-only by trigger and holds no rows. |

No data loss is possible in any path: the table is additive and, today, empty.

---

## 10. Operational impact

- **No runtime impact.** `btcbot.service` does not touch the Evidence Library.
- **`report.py --check`**: one extra `SELECT` over a table that currently has
  zero rows. Unmeasurable.
- **New operator obligation**: before any Validated promotion, compute and
  record correlations against every Validated family (§6). With N Validated
  families, promoting the next one requires N measurements. At the governance
  target band of 10–20 families this stays manageable by hand; if it ever
  doesn't, that is the trigger to automate the computation — not to relax the
  threshold.
- **First real exercise expected at H-IVRV's promotion.** CLAUDE.md §16 notes
  it as "the first pairwise-independence test case… at IS max corr 0.37 it
  should clear `max_pairwise_r = 0.70`."
