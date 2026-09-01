# FB-10b-X — Local Defect Remediation (D-2, D-3, D-4, D-5)

**Date: 2026-09-01 (UTC) · Gate FB-10b-X · IMPLEMENTATION.**

Not deployed. No historical data provisioned. No DSR/BH computed. No forward
snapshot written. No lockbox accessed. No production change.

**W-1 (architecture A/B), W-2 (copy vs re-ingest) and W-3 (deployment timing)
are untouched and were not decided implicitly.** Nothing here presumes where the
producer runs: the corpus validation (D-5) applies identically to a loaded and
an injected frame, and the CLI (D-2) is a file hand-off that works on one host
or across two.

---

## D-2 — Operator CLI plumbing

The function API was already sufficient (FB-10b-W §D). Only argument plumbing
was missing, so only argument plumbing was added.

| side | addition |
|---|---|
| `forward_dsr_bh.py` | `--out PATH` — writes the battery result, `payloads` included, to a **file**. Never the ledger |
| `forward_snapshot.py` | `--dsr-payload PATH` — loads that file, selects this family's block, and passes it to **both** branches |

`_load_dsr_payload()` refuses rather than guesses:

- an `as_of` mismatch is rejected — FDB-5 membership is fixed per `as_of`, so
  attaching one battery's statistics to another evidence window is barred;
- a family absent from `payloads` is rejected — it was not a member, and
  inventing a block for it is what FDB-7 forbids.

Parity is structural: `main()` calls `_load_dsr_payload` **once**, and both the
dry-run and the write branch receive the same `dsr_block`. A dry run can no
longer display something a write would not persist — the FB-12i defect is not
reintroduced. The write still returns after the dry-run branch, so it remains an
explicit second operator action. No scheduler wiring; `ledger_sync.py`
untouched.

**Verified end to end on a read-only copy of the real production corpus:** the
producer refused (`empty_battery`, zero payloads), and the snapshot then refused
`h5_vol_regime` — *"It was not a battery member at this as_of, and no block may
be invented for it (FDB-7)"*. Both databases byte-identical afterwards.

## D-3 — Evidence-engine import collision

`_load_evidence_module(name)` loads `research/evidence/<name>.py` under a
process-unique `sys.modules` key, following
`research/feature_store/evidence_engine_bindings.py`, which documents this
collision by name.

One extension the precedent did not need: `signals.py:16`'s bare
`from engine import SignalSpec` still resolves through `sys.modules["engine"]`,
so the correct engine is bound to the bare name **only while the module
executes**, and the previous binding — **including its absence** — is restored
in a `finally`. A failed load also drops its own cache entry, so no
half-initialised module is left behind.

Both call sites use it (`library_specs`, `_train_slice_master`). No global
import hook. `research/dq/engine.py`, `research/evidence/engine.py` and
`signals.py` are all **unmodified**.

Proven in fresh subprocesses: clean interpreter → 34; poisoned interpreter → 34;
binding restored after success; binding restored after an exception; an absent
binding restored to absent.

**One test-side consequence, recorded rather than hidden.**
`test_dvol_family_is_excluded` did a bare `import signals`, which had been
riding on a side effect of the old bare import inside `library_specs()`. The
boundary correctly removed that side effect, so the test now goes through the
boundary too.

## D-4 — Stale docstrings

Exactly the two statements FB-10b-V identified, replaced with the text drafted
at FB-10b-W §F: DSR/BH may now be present but is never computed here, and the
snapshot identity is `(family_id, provenance, window_end, engine_version)`.

**No behaviour was changed under cover of the documentation fix** — asserted by
a test that AST-compares `build_snapshot_metrics`, `_existing_snapshot_run` and
`_load_registry` against `HEAD` and requires them structurally identical.

## D-5 — Explicit corpus-slice validation

`validate_train_slice()` runs in `compute_battery` **before any statistic**, on
every frame — loaded or injected, so an off-host caller clears the same bar.

`expected_train_days()` derives the window from FDB-2a's two dates and yields
**1612** days, which is exactly the row count the pre-registrations record
(*"train only, 1612 rows, 2021-01-01..2025-05-31; 365 lockbox rows excluded"*).
A test asserts that agreement, so drift in either direction is caught rather
than absorbed. Measured on the real corpus this gate: **1612 rows, 2021-01-01 →
2025-05-31, zero missing calendar days.**

Three refusals, all fail-closed:

| reason | condition |
|---|---|
| `corpus_incomplete` | empty frame, absent `dt`/`close`, or **any** missing calendar day |
| `corpus_beyond_train_slice` | a day after `TRAIN_END` reached the statistical path — the frame was not sliced |
| *(retained)* `empty_battery` | corpus fine; no family meets its frozen minimum |

It never truncates the window to what is available, never substitutes a later
end date, and never reaches past `TRAIN_END` — it is handed the already-sliced
frame, so the sealed window is not present to be read. `2026-05-31` remains
absent from executable code.

**The two blockers are now separately reported**, which was the whole point:
today's production state still refuses as `empty_battery` (membership resolves
first, so the corpus is never consulted); an under-provisioned host with a
qualifying member refuses as `corpus_incomplete` naming the first missing day —
never as `member_sharpe_uncomputable` blaming a signal.

---

## Statistical methodology — untouched

`research/evidence/governance.py` is **UNCHANGED** (`git diff` empty), so
`bh_fdr`, `bh_adjusted_p`, `deflated_sr`, `psr` and `_sr_stats` are byte-identical.
The 34-member population, `n_trials = 34`, `hold_days = 7`, the frozen train
slice, the DSR minima, the BH convention and FDB-13 are all unchanged. **No
DSR/BH was computed in this gate.**

## Protected surfaces — all verified UNCHANGED

`ledger_sync.py` · `run_daily.py` · `runner_base.py` · `daily_job.py` ·
`lifecycle.py` · `record.py` · `thresholds.yaml` · `registry.yaml` · every
pre-registration · `run_date_policy.yaml` · `schema_forward.sql` ·
`governance.py` · `signals.py` · `evidence/engine.py` · `dq/engine.py` ·
`src/api/*`.

## Tests

New `tests/test_forward_dsr_bh_remediation.py` — **34 tests**. Focused suites
across the producer, FDB-13, remediation and all three snapshot files:
**210 passed**.

| | failed | passed |
|---|---|---|
| Pristine baseline at `e9d1ccd` | **24** | 4,963 |
| With changes | **27** | 4,994 |

`4963 + 34 − 3 = 4994`. The three delta failures are the working-tree guards
(`test_P08`, `test_rf2`, `test_no_analysis_script_was_modified`), which pass
after commit. **Zero regressions.**

`report.py --check` OK (35 families, 9/22/4) · `verify_chain` ok/27 · INV-1
11 passed · local ledger `dd568de2f50a23a4` unchanged.

---

## Remaining human decisions — unchanged by this gate

| # | decision |
|---|---|
| **W-1** | Architecture **A** (provision the 156 manifested, lockbox-free partitions to production) or **B** (compute off-host, ship the payload). GL-2 fixes only the *write* location |
| **W-2** | If A: copy the manifested partitions, or re-ingest on the host |
| **W-3** | Deployment timing — `af80fbd` → current head |

Sample size remains an independent condition: no family meets its minimum;
`g1_market_regime_sma200` is closest at **7/10**.

**Next gate:** W-1/W-2/W-3 decision, then deployment, then a separate
write-authorization gate for the first snapshot carrying DSR/BH.

**STOP.**

---

## Trail

| Gate | Outcome |
|---|---|
| FB-10b-V | DEPLOY-SAFE; D-1…D-4 recorded |
| FB-10b-W | D-1 scoped; GL-2 fixes the write location; D-5 newly found |
| **FB-10b-X** | **This record. D-2/D-3/D-4/D-5 remediated locally; no architecture or deployment decision made** |
