# Data Completeness Framework — Schema & Developer Reference

**Status:** Implemented (Governance Architecture PR-7, 2026-07-14).
**Package:** `research/dq/`
**Implements:** `docs/governance/data_completeness_gate.md` (the engine, its PR-1)
and the engine/check-versioning half of `docs/governance/contract_versioning.md`
(its PR-B), on top of the governance receipt ledger (PR-1) and the contract
registry (PR-2/3/4) that already existed.
**Tests:** `tests/test_dq_engine.py`, `test_dq_contract.py`, `test_dq_receipts.py`,
`test_dq_probes.py` — 187 tests.

This is the **generic framework**, not H6. H6 is the motivating example and the
correctness oracle; it is named in this document only where a design decision
exists *because* of a specific failure it exhibited. No pipeline has adopted the
framework yet — adoption is PR-8.

---

## 1. What this PR does and does not do

**Does:** the contract model, immutable hash-addressed loading, a generic
evaluation engine, the readiness verdict model, evaluation receipts through the
existing ledger, point-in-time replay under the historical contract, the rule
provider, four kinds of validation, and this documentation.

**Does not:** H6 repair or derivation, feature engineering, dataset generation,
Evidence Library changes, state-transition governance, ML readiness, operational
timers, Telegram, Docker, deployment, rollout. No pipeline is gated yet; nothing
in `research/` calls this package.

---

## 2. Module map

| Module | Responsibility | Depends on |
|---|---|---|
| `states.py` | 8 states, severity order, worst-wins; 6 actions, action precedence | — (pure) |
| `policy.py` | the `(state, criticality) -> action` matrix | `states` |
| `contract.py` | the contract MODEL — parse, validate, canonicalise, hash | `receipts`, `policy`, `states` |
| `observation.py` | `InputObservation` — metadata only; the replay substrate | — |
| `checks.py` | the 6 check families; pure; frozen by versioned `check_id` | `contract`, `observation`, `states` |
| `engine.py` | `evaluate(contract, observations, period, as_of) -> Verdict` | `checks`, `policy`, `contract` |
| `rules.py` | `ReadinessRule` + the generic `RuleRegistry` | `engine`, `contract` |
| `probes.py` | source-type probe registry (metadata only) | `contract`, `observation` |
| `contract_loader.py` | the THREE resolution operations (§5), never conflated | `contract_registry`, `contract` |
| `readiness.py` | receipts, the `data_readiness` projection, replay | all of the above + `receipt_ledger` |
| `validation.py` | framework / contract / determinism / replay validation | all of the above |

`states.py` and `policy.py` are, in the spec's own words, "the entire safety
argument"; every branch of both is covered.

---

## 3. The contract

Contracts are **data**. `contract.py` contains no completeness logic — every
`if` that decides whether data is complete lives in `checks.py` and is driven by
what a contract declares. A new pipeline adds a contract file, not a code path.

```yaml
contract_id: corpus_daily
grain: utc_day                 # drives the TEMPORAL check; a registry entry, not an if
owner: research
produces: [spot_close, funding_bps]

inputs:
  - id: funding
    source: {type: sqlite, path: research/data/x.db, table: funding,
             ts_column: ts, ts_unit: ms, arrival_column: ingested_at}
    criticality: required
    schema:
      columns: {ts: INTEGER, rate: REAL}
      bands: {ts: [1.0e12, 1.5e15]}      # catches the ms->us class
    coverage: {min_count: 3}             # 00:00/08:00/16:00 UTC — ALL THREE
    freshness: {max_age_hours: 26}
    late_arrival_hours: 12               # after this: INCOMPLETE_DATA -> BACKFILL_REQUIRED
    remediation: python -m ingest.binance_funding --start 2026-06

  - id: dvol
    source: {type: sqlite, ...}
    criticality: optional
    freshness: {max_age_hours: 30}
    late_arrival_hours: 24
    degradation:                          # the IVRV precedent, DECLARED
      on: [STALE_DATA, INCOMPLETE_DATA, BACKFILL_REQUIRED]
      outputs_unavailable: [ivrv_vrp_spread]

history_window: {min_rows: 31, continuity: no_interior_gaps}
policy: default                          # or a per-state override table
```

### Identity

```
contract_id     stable, human      "corpus_daily"   — the thing being governed
version         monotonic int      3                — a human label, assigned by the tool
contract_hash   sha256_lf(bytes)   9f3c1a…          — THE identity; what machines use
```

`contract_hash` is `sha256_lf` of the canonical JSON — byte-identical convention
to `pre_reg_guard._sha256_lf()` and `receipts.sha256_lf`. One hashing rule on
this platform, not two.

**Numeric canonicalisation.** Every numeric in the canonical body is an `int` or
a **decimal string** with trailing zeros stripped (`fmt_dec`). `1.0`, `1.00` and
`Decimal("1.000")` therefore converge on one hash — a contract whose identity
changed because someone typed an extra zero would make version history
meaningless. No IEEE float ever enters a hash (`governance_receipts.md` §1.3
rule 3), and exponent notation is never emitted (`normalize()` alone renders 100
as `1E+2`; `fmt_dec` corrects for that).

**Check identity is materialised.** Every check a contract invokes appears in the
canonical body **by id**, even when the author wrote nothing. A contract pins not
only its thresholds but the semantics those thresholds are interpreted under.

### Two contract-schema decisions worth knowing

**`history_window` is authoring sugar.** The spec declares it at contract level,
but a `Check` is typed `(observation, input_spec, as_of)` and an observation is
per-input. It is therefore applied at parse time to every REQUIRED input that
does not declare its own `continuity`, and the canonical body carries it
per-input — so what the receipt records is exactly what ran.

**`no_interior_gaps` defaults to `True`.** A window that tolerates gaps must say
so. "The author forgot to mention gaps" must never be read as "gaps are fine".

---

## 4. The eight states

Severity-ordered; the aggregate is the **worst** across all inputs.

| # | State | Meaning | Self-heals? | Fix belongs to |
|---|---|---|---|---|
| 0 | `READY` | complete, fresh, correctly shaped, period closed | — | — |
| 1 | `PARTIAL_DAY` | `period_end > as_of` — not wrong, **not finished** | yes, by time | nobody |
| 2 | `STALE_DATA` | newest record older than the freshness SLA | if the collector recovers | the collector |
| 3 | `INCOMPLETE_DATA` | coverage short, late budget **not** expired | maybe | the source |
| 4 | `BACKFILL_REQUIRED` | coverage short, late budget **expired** | **no** | an operator |
| 5 | `SCHEMA_MISMATCH` | columns/dtypes/bands contradict the declaration | **no** | **code** |
| 6 | `CONFIGURATION_ERROR` | the contract itself is unusable | **no** | the contract author |
| 7 | `UNKNOWN` | the gate could not determine readiness. **Never a pass.** | — | unknown |

**The state is never `None` and never `NULL`.** Incompleteness is a **verdict**,
not a value — a NULL reaching a derivation is indistinguishable from a legitimate
missing value. Asserted directly by
`test_dq_engine.py::TestStates::test_state_is_never_none`.

### The three-way split

`PARTIAL_DAY` / `INCOMPLETE_DATA` / `BACKFILL_REQUIRED` present as the **identical
physical symptom** — "fewer rows than expected" — and are separated by two clocks:

```
period_end > as_of ................................... PARTIAL_DAY       -> DEFER
period closed, coverage < floor, within late budget ... INCOMPLETE_DATA   -> RETRY
period closed, coverage < floor, late budget expired .. BACKFILL_REQUIRED -> BLOCK
```

A coverage shortfall on an **open** period is reported `PARTIAL_DAY`, never
`INCOMPLETE_DATA`. At 00:05 UTC, 1 of 3 funding events having fired is not a data
problem; two more are coming.

---

## 5. The policy matrix

| State | Required | Optional |
|---|---|---|
| `READY` | `COMPUTE` | `COMPUTE` |
| `PARTIAL_DAY` | `DEFER` | `DEFER` |
| `STALE_DATA` | `RETRY` | `DEGRADE`\* |
| `INCOMPLETE_DATA` | `RETRY` | `DEGRADE`\* |
| `BACKFILL_REQUIRED` | `BLOCK` | `DEGRADE`\* |
| `SCHEMA_MISMATCH` | **`HALT`** | **`HALT`** |
| `CONFIGURATION_ERROR` | `HALT` | `HALT` |
| `UNKNOWN` | `BLOCK` | `BLOCK` |

\* **only if the contract declared a degradation path for that state.** An
undeclared degradation collapses to `BLOCK` — never silently taken. Ad-hoc
"compute with what we have" is exactly what wrote a 00:05 tick into `spot_close`.

`SCHEMA_MISMATCH` halts even for an **optional** input — the one asymmetry in the
table. A unit change or a silently-unwritten column corrupts everything
downstream, retroactively, with no symptom; there is no safe degraded computation
against data whose *meaning* has changed.

### Action precedence (`DEFER` > `RETRY` > `DEGRADE`)

Not the same ordering as state severity, deliberately. A required input's
`PARTIAL_DAY -> DEFER` must outrank an optional input's declared `DEGRADE`:
writing anything for a period that has not finished is the H6 bug, and no
degradation path can license it.

---

## 6. Check ordering — normative, and a safety property

```
1. CONFIGURATION  -> CONFIGURATION_ERROR   (is the contract even usable?)
2. PROBE          -> UNKNOWN               (could we look at all?)
3. SCHEMA         -> SCHEMA_MISMATCH       (is the data shaped as declared?)
4. TEMPORAL       -> PARTIAL_DAY           (is the period closed?)
5. FRESHNESS      -> STALE_DATA
6. COVERAGE       -> INCOMPLETE_DATA / BACKFILL_REQUIRED
7. CONTINUITY     -> BACKFILL_REQUIRED
```

**Schema runs before coverage, and that is the whole point.** The Binance ms→µs
switch silently produces a **0-row partition** (CLAUDE.md §11). If coverage ran
first, that would present as `INCOMPLETE_DATA`, and an operator would "backfill"
the same broken month forever, chasing a data problem that is actually a code
problem. Asserted by
`test_dq_engine.py::TestCheckOrdering::test_unit_switch_zero_row_partition_is_schema_mismatch_not_incomplete_data`.

### What short-circuits, and what deliberately does not

* `CONFIGURATION_ERROR` short-circuits **everything** — nothing the gate might
  say about the data can be trusted.
* A severity-≥5 state short-circuits **that input's** remaining checks, **not the
  other inputs'**.

Short-circuiting *globally* on the first ≥5 state would be a **defect, not an
optimisation**: input A's `UNKNOWN` (severity 7 → `BLOCK`) would mask input B's
`SCHEMA_MISMATCH` (severity 5 → **`HALT`**), and the pipeline would `BLOCK` where
it was required to `HALT`. **Worst-wins on state does not imply worst-wins on
action, because the policy matrix is not monotonic in severity.** Every input is
therefore always evaluated. Asserted by
`test_one_inputs_unknown_does_not_mask_anothers_schema_mismatch`.

---

## 7. Receipts and the `data_readiness` projection

Every verdict is emitted as a `readiness.verdict` row in the **existing**
`governance_receipts` ledger — hash-chained, append-only, verified by the
existing `verify_chain()`, replayed through the existing `receipts.replay()`. No
new receipt architecture. `readiness.verdict` has been reserved as a `derived`
decision in `DECISION_TAXONOMY` since PR-1 and is finally used here.

`data_completeness_gate.md` §9 specifies a `data_readiness` table;
`governance_receipts.md` deferred "unifying `data_readiness` as a receipt
projection". **Both are satisfied, and the deferral is resolved:** the table
exists as specified but is keyed by the receipt's own `receipt_id` and holds no
governance authority. It carries the **replay substrate** — the
`[InputObservation]` set — which is too large and structured to live in a receipt
body, and whose **content hash is committed inside the receipt** (`inputs[]`,
kind `observations`). A mutated projection row is therefore detected at replay
rather than silently changing what a historical verdict "was computed from".

*The receipt is the governance record. The projection is the substrate and the
board. Neither can be tampered with without the other noticing.*

```sql
CREATE TABLE data_readiness (
    receipt_id        TEXT PRIMARY KEY,  -- the governance_receipts receipt_id
    contract_id       TEXT NOT NULL,
    contract_version  INTEGER,
    contract_hash     TEXT NOT NULL,
    engine_version    TEXT NOT NULL,     -- §3: freezing the contract is NOT sufficient
    rule_id           TEXT NOT NULL,
    feature_id        TEXT NOT NULL,
    period            TEXT NOT NULL,
    as_of             TEXT NOT NULL,     -- the INJECTED clock (L6)
    state             TEXT NOT NULL,     -- one of the 8; NEVER NULL
    action            TEXT NOT NULL,
    checks_json       TEXT NOT NULL,     -- [CheckResult] — observed vs expected
    observations_json TEXT NOT NULL,     -- [InputObservation] — the REPLAY SUBSTRATE
    observations_hash TEXT NOT NULL,     -- committed inside the receipt body
    remediation       TEXT,              -- a LITERAL RUNNABLE COMMAND, or NULL
    created_at        TEXT NOT NULL
);
```
Append-only, enforced by DB triggers, exactly as `governance_receipts` is.

**The receipt is written before the action and regardless of it** — even on
`DEFER`, even on `READY`. A deferred day leaves a receipt saying "deferred,
PARTIAL_DAY, here is the evidence". That is how a board distinguishes *"this day
was correctly skipped"* from *"nothing ran"* — today those two are
indistinguishable, which is why the H6 outage was silent.

**One transaction.** Receipt + projection commit together, reusing
`receipt_ledger.append_receipt_on_connection()` (the extraction PR-4 already made
for exactly this pattern). A crash leaves neither; the period is simply
un-attempted, which the system already handles.

---

## 8. Replay

`replay_readiness(db, receipt_id)` dereferences **every** input by hash. It never
looks anything up by date and never reads the working tree:

| input | resolved by |
|---|---|
| contract | `resolve_by_hash(receipt.subject.version_or_hash)` — the exact frozen bytes, even if superseded three times and retired years ago |
| engine/rule | `rules.get_rule(receipt.rule.rule_id, receipt.rule.engine_version)` — `UnknownRuleError` if unavailable |
| checks | every `check_id` the contract names must exist — `UnknownCheckError` if not |
| observations | the projection, whose hash the receipt committed to — `ObservationsTamperError` on mismatch |
| `as_of` | **the receipt's own** — not `now()` |

**Why hash-addressing, not date resolution.** Backfills invert the clocks (a day
derived on 07-20 for period 06-14 has two dates; either choice is defensible,
which means it is a bug waiting to be re-litigated). Activation intervals are
mutable metadata. And a git checkout would change history: if replay read
`contracts/*.yaml`, checking out an older commit would change what a 2026-06-14
receipt *means*. **The filesystem is not an archive.**

Enforced **structurally**, not by convention: `contract_registry.py` has no
file-reading code path at all, and `contract_loader.py`'s one filesystem function
(`load_working_copy`, authoring-only) is unreachable from the replay path — both
asserted by AST inspection in `test_dq_contract.py::TestNoFilesystemInReplay`.

**Replay never substitutes.** If the rule or a check named in a receipt is
unavailable, replay raises. Falling back to "whatever is current" would replay a
historical receipt under semantics it was never rendered under — while every hash
still verified and replay still reported green. That is the one failure mode an
audit trail must not have.

**Reproducibility guarantee:**
`(contract_hash, engine_version, check_ids, observations, as_of) -> verdict` is a
pure, total, byte-stable function, replayable with no source access, forever.

---

## 9. The three resolution operations (§5)

Three operations need a contract and resolve it by **three different mechanisms**.
Conflating them is the bug, so they are three separately-named functions and
there is deliberately **no `get_contract()`** (asserted by
`test_there_is_no_ambiguous_get_contract_function`):

| Operation | Function | Mechanism |
|---|---|---|
| fresh derivation | `resolve_for_derivation(db, contract_id)` | the unique **`active`** version. Not by date. A partial unique index enforces "exactly one active" at the DB layer, so there is nothing to choose between. |
| replay / audit | `resolve_for_replay(db, contract_hash)` | **dereference.** Total, no lookup, no clock. |
| reprocessing | `resolve_for_reprocessing(db, id, pinned_hash, contract_policy)` | an **explicit human choice**, never defaulted. |

```
--contract-policy pinned    the historical hash from the receipt
                            => reproduces the original verdict exactly
                            => the contract was right; something ELSE was wrong

--contract-policy current   today's active contract
                            => DELIBERATELY re-judges history
                            => the CONTRACT ITSELF was wrong
```

**There is no default; a missing policy refuses to run.** The person running a
repair at 2am is exactly the person who must state which one they mean.

**Law V1 — relaxation never retroactively re-admits.** Activating a new version
never alters an existing receipt or derived row. The relaxation attack (lower
`min_count` from 3 to 2, blocked days turn READY, blocked evidence becomes
admissible, no alarm fires — p-hacking performed against the data-quality layer)
is structurally impossible: history is pinned to the hash it was rendered under.
Re-judging is a separate, signed act. Asserted by
`test_replay_uses_the_historical_contract_not_the_current_one`.

---

## 10. Public interfaces

```python
# engine.py
evaluate(contract, observations, period, as_of, *, feature_id=None,
         contract_version=None, contract_hash=None) -> ReadinessVerdict
ReadinessVerdict(.state, .action, .checks, .input_states, .remediation,
                 .outputs_unavailable, .may_derive, .is_ready, .to_dict())
normalise_as_of(as_of) -> datetime          # the ONLY as_of conversion; never invents one
observations_hash(observations) -> str
ENGINE_VERSION = "dq.v1"

# contract.py
parse_contract(doc) -> Contract;  parse_contract_text(text) -> Contract
validate_contract(contract) -> list[str];  validate_contract_or_raise(contract)
Contract(.contract_hash(), .canonical_text(), .check_ids(), .input(id), .to_canonical())
period_bounds(grain, period);  GRAIN_REGISTRY   # a new grain is an entry, not a change

# states.py / policy.py
READY PARTIAL_DAY STALE_DATA INCOMPLETE_DATA BACKFILL_REQUIRED
SCHEMA_MISMATCH CONFIGURATION_ERROR UNKNOWN
COMPUTE DEGRADE RETRY DEFER BLOCK HALT
worst(states); worst_action(actions); severity(state)
policy.resolve(state, criticality, degradation_declared=False, overrides=None) -> str

# checks.py
CHECK_REGISTRY; get_check(id); register_check(id, fn)   # additive; redefinition refused
CheckResult(.check_id, .input_id, .state, .observed, .expected, .detail, .passed)

# observation.py
InputObservation(input_id, rows, min_ts, max_ts, newest_arrival_ts, schema,
                 value_bounds, coverage_count, coverage_denominator,
                 history_rows, has_interior_gaps, content_hash, probe_error)

# probes.py
register_probe(source_type, probe); probe_all(contract, period, as_of)
# built in: "static", "sqlite"

# rules.py
ReadinessRule (Protocol); CompletenessGateRule; DEFAULT_RULE
RuleRegistry(name).register/get;  register_rule(rule);  get_rule(id, engine_version)
UnknownRuleError

# contract_loader.py
resolve_for_derivation / resolve_for_replay / resolve_for_reprocessing
load_working_copy(path)            # authoring only
check_tamper(db, working_copy)     # CONTRACT_TAMPER -> HALT
CONTRACT_POLICY_PINNED / CONTRACT_POLICY_CURRENT

# readiness.py
init_db(db)
evaluate_and_record(db, contract_id, period, as_of, observations=None, rule=None,
                    actor="dq", actor_type="service") -> GateResult
GateResult(.verdict, .receipt, .observations, .may_derive)
replay_readiness(db, receipt_id) -> ReadinessReplay
get_readiness(db, feature_id, period, as_of=None)      # point-in-time
get_readiness_history / get_readiness_board / get_readiness_by_receipt
bootstrap_readiness_receipt(db, verdict, attestation_note, actor="bootstrap")

# validation.py
validate_framework() -> [str]                     # DQ-F*
validate_contract_document(doc) -> [str]          # DQ-C*
assert_deterministic(contract, obs, period, as_of, iterations=3) -> [str]   # DQ-D*
validate_replay(db, receipt_id) -> [str]          # DQ-R*
validate_ledger(db) -> [str]                      # DQ-L*
```

Error codes are `DQ`-prefixed so they are visibly distinct from `lifecycle.py`'s
`C1`–`C9` and `evidence_governance`'s `EG1`/`EG2`.

---

## 11. `now` is an argument, never a syscall (L6)

`as_of` is mandatory and injected everywhere. There is **no `datetime.now()` in
the evaluation path** — asserted by AST inspection over `engine.py`, `checks.py`,
`policy.py`, `states.py` (`test_engine_module_never_calls_datetime_now`). A gate
that reads a clock cannot be replayed; a gate that cannot be replayed cannot be
audited.

The `sqlite` probe extends this to the source: with an `arrival_column`
configured, rows that had not **arrived** by `as_of` are invisible. Without
arrival-time tracking, backfills silently acquire look-ahead.

`as_of` and `decided_at` are separate fields in every receipt: the point-in-time
coordinate and the wall clock. They differ on every backfill, and conflating them
is how a replay quietly gains look-ahead.

---

## 12. Probes (L3)

Metadata only: counts, bounds, arrival times, a schema, a fingerprint. **A probe
that loads a full dataframe is a failed review** — asserted by inspection
(`test_a_probe_never_materialises_a_row`: every SELECT in `probes.py` is an
aggregate or a PRAGMA). Gating must be orders of magnitude cheaper than deriving,
or it will be skipped under load, and a gate that gets skipped is not a gate.

Registered **by source type, not by feature**. Adding a source type is an
additive registration, never an engine change.

**A probe never raises into the engine.** Every failure becomes
`probe_error` → `UNKNOWN` → `BLOCK` (L2). A gate that dies with a traceback
writes no receipt, and a period with no receipt is a period nobody looked at.

`static` exists so a pipeline that **already has** the metadata can supply it:
`SnapshotWriter`'s `collection_metrics` already records `coverage_pct` and
`stale_count` per cycle with precisely-defined semantics (CLAUDE.md §11), and the
gate should **consume** those numbers rather than recompute them — a second,
divergent definition of "coverage" is the exact disease this framework treats.

No pandas/pyarrow anywhere in the package: it imports cleanly in both the venv
bot and the conda research env.

---

## 13. The rule provider, and PR-6's substitution point

PR-6 established a rule architecture whose contract is four properties: identity
is `(rule_id, engine_version)`; both are recorded in every receipt; replay
dispatches on the recorded pair; an unresolvable rule fails loudly.
`rules.RuleRegistry` implements exactly those four properties — the **mechanism
generalised**, written once, shape-agnostic. It is not a second copy.

PR-6's `AdmissibilityRule` is typed to the Evidence Library's gate signature
`(family_meta, run_metrics, thresholds) -> (bool, gate_results)`; a readiness
rule is `(contract, observations, period, as_of) -> ReadinessVerdict`. The two
rule *shapes* genuinely differ, so one Protocol cannot serve both — but the
registry/dispatch/fail-loud mechanism is identical and now exists once.

**PR-6 built its substitution point for exactly this framework** ("the
substitution point PR-7's Data Completeness Gate will use"). `test_dq_probes.py::
TestPr6SubstitutionPoint` proves the seam is real: a readiness-backed
`AdmissibilityRule` defined entirely outside the Evidence Library satisfies its
Protocol, registers, resolves, and **coexists with** (never displaces) the
existing `CandidateGateRule` — with zero edits to `evidence_governance.py`.

**This PR does not register such a rule**, and a test asserts it
(`test_this_pr_does_not_register_an_admissibility_rule`). Doing so would change
how evidence is admitted — a governance change (§7 G1/G2), not a framework
change. That is PR-8.

`test_dq_never_imports_the_evidence_library_governance_module` asserts the
dependency direction: the framework depends on shared infrastructure (the receipt
ledger, the contract registry), never on the Evidence Library, which is one
*consumer* of readiness.

---

## 14. Migration notes

**Nothing to migrate.** No table was altered, no existing row was touched, no
existing call path changed. `data_readiness` is created by `readiness.init_db()`
on databases that opt in; the real `ledger.db` does not have it and was not
touched by this PR (verified: `--check` still passes, chain still valid at 13
receipts, no `data_readiness` table present).

**The one change to an existing file** is additive:
`receipts.ACTIONS` gains `"evaluate"`. `readiness.verdict` has been in
`DECISION_TAXONOMY` since PR-1, but no *verb* described what a readiness
evaluation does — a gap that only surfaced when something finally emitted one. No
existing receipt uses it; no validation was loosened; every receipt written under
PR-1…PR-6 still verifies and replays byte-identically (full suite green, real
ledger re-verified).

`"admit"`/`"reject"` were deliberately **not** reused: a `READY` verdict means
*complete*, not *admissible as evidence*. §7 G5 is explicit that readiness and
admissibility are **independent gates, both required** — Delta testnet data can be
perfectly complete and still be economically synthetic. Overloading the admission
verbs would encode exactly the conflation the spec warns against.

**Adoption (PR-8) is opt-in per pipeline**: write a contract, activate it through
the registry, call `evaluate_and_record()`, and act on `.may_derive`. The gate
never changes *what* is computed — only *whether it is permitted to be computed
at all*.

---

## 15. Deviations from the spec, and why

Three places where this implementation deliberately differs from a literal
reading. All were resolved in the direction the spec's own stated intent points,
and none silently.

**1. `STALE_DATA` / required → `RETRY` (not "`RETRY` → `DEGRADE` if declared,
else `BLOCK`").** The §3 matrix's required-column cell for `STALE_DATA` describes
a *sequence across time* ("retry, and if that is exhausted, degrade or block"),
but `Policy.resolve` is typed `(state, criticality) -> Action` — a single action.
Encoding the escalation inside the matrix would make the pure function
time-dependent. The engine returns `RETRY`; the escalation-on-exhaustion belongs
to the retry driver, which is adoption-layer work (PR-8). The declared-degradation
path is honoured for optional inputs, where it is unambiguous.

**2. Global short-circuit narrowed to per-input.** See §6 above. A literal
"first ≥5 state short-circuits" would let an `UNKNOWN` mask a `SCHEMA_MISMATCH`
and downgrade a required `HALT` to a `BLOCK`.

**3. `history_window` applied per-input at parse time.** See §3 above.

---

## 16. Acceptance criteria (`data_completeness_gate.md` §13)

| # | Criterion | Status |
|---|---|---|
| 1 | a derivation cannot run against ungated inputs | **framework ready** — `.may_derive` is the only door; enforcement lands with the first adopter (PR-8) |
| 2 | a non-READY period cannot produce a durable row a later run treats as done — **F3 structurally dead** | **framework ready** — a non-READY verdict yields no derived row at all; `test_the_partial_day_is_revisited_the_f3_latch_is_dead`. Full platform-wide guarantee needs the readiness-aware `_already_processed()` (PR-8) |
| 3 | schema drift → `SCHEMA_MISMATCH` → `HALT`, never masquerading as missing data | **met** |
| 4 | every receipt replays bit-identically without touching the sources | **met** |
| 5 | `--as-of` reproduces any past verdict with no look-ahead | **met** |
| 6 | a new feature = one contract file + zero engine change | **met** (no pipeline-specific logic exists in the package; a new grain, source type or check is an additive registration) |
| 7 | governance cannot pin a run to evidence derived from incomplete data | **not this PR** — G1/G2 wiring is PR-8; the substitution point is proven |

Criterion 2 is the one the framework exists to guarantee.

---

## 17. Remaining work for PR-8

**In scope for the next PR — the first adopter (the spec's own PR-2, "the
proof"):** write `contracts/corpus_daily.yaml` (funding floor **3**, not 2),
activate it **through the registry** (never load it from a file — "or the
filesystem-as-archive habit sets in immediately and is very hard to remove
later"), gate `daily_job.process_day()`, and make `_already_processed()`
readiness-aware — `WHERE day = ? AND readiness = 'READY'`. That predicate is,
per the spec, "the most important line in the entire body of work": it converts
H6's permanent corruption into a self-healing retry for **every** writer on the
platform.

Its gate: *the framework independently rediscovers the H6 corruption from
production data, with no hand-coded knowledge of it.* The engine already produces
the right verdict for the H6 scenario from the contract alone — a corpus whose
last row is today at 00:05 with 1 of 3 funding events yields
`PARTIAL_DAY → DEFER → no row written` — but it has never been run against the
real `corpus_daily` history.

**Then, in order:** forward-evidence collectors (`run_log.status` gains
`deferred`/`blocked`/`degraded`, with `deferred` retryable **without operator
action** — that distinction is currently absent and is the F3 latch in a
different hat); governance integration (G1–G3: the `readiness` block in
`runs.metrics_json`, the 100%-READY precondition on `check_validated_preconditions`,
`INADMISSIBLE` rather than refusing to write — refusing loses the fact that the
day existed, and lineage loss is the one thing the Evidence Library exists to
prevent); observability (`readiness_board.md`, structured logging, heartbeat
escalation); then platform rollout by contract only.

**Deferred by `contract_versioning.md`'s own sequencing, not by this PR:** the
impact report / empirical change classifier (PR-C — `change_class` is currently
author-declared and unverified; the spec requires it be *measured* from a
replay-diff and activation *refused* on misdeclaration), reprocessing execution
and the M2 pinned-window guard (PR-D — `resolve_for_reprocessing` provides the
policy choice; nothing executes a reprocess yet), and receipt signing/anchoring.

**One known, deliberate, non-blocking divergence:** PR-6's private
`_RULE_REGISTRY` still hand-rolls the registry/dispatch mechanism that
`rules.RuleRegistry` now generalises. Collapsing it onto `RuleRegistry` is a
zero-behaviour-change refactor, but it edits the Evidence Library, which this PR
was forbidden to touch. **Proposed, not performed** — a natural, small first step
for PR-8.
