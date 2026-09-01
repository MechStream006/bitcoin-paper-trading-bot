# FB-10b-T1 — Read-Only Gate Audit: `bh_p` Presentation

**Date: 2026-09-01 10:00 IST (04:30 UTC) · Gate FB-10b-T1 · READ-ONLY AUDIT.**

No file modified. No FDB-13. No `report.py` / `governance.py` /
`forward_dsr_bh.py` change. No DSR/BH computed. No lockbox accessed. No ledger
write. No deployment. No production change.

---

## 0. Two corrections to earlier records in this chain

**C-1 — FB-10b-T1's decision matrix (§2.1) claimed:** *"No frozen clause anywhere
in FDB-1 … FDB-12 mentions rendering, reporting, display, or an API."*
**That is wrong.** **FDB-7** reads:

> *"No DSR or BH-p is computed, stored **or reported**."*

The adopted contract **already imposes a render-layer obligation**. This
materially narrows question C below and is corrected here rather than left
standing.

**C-2 — FB-10b-T claimed "three sites in `report.py`."** Corrected at the
decision matrix to **three independent renderers**, one a live service.
Reaffirmed by this audit's exhaustive sweep (§B).

---

## A. Legacy `bh_p`

### A.1 What the value means for existing historical rows — established, not inferred

Exactly **three** rows in the ledger carry a non-NULL `bh_p`. All are
`offline-corpus`. Their `metrics_json` was read in full this gate:

| run | family | lane | engine | `bh_p` | FDB-9 keys present |
|---|---|---|---|---|---|
| `efbe6acb` | `ivrv_vrp_spread` | offline/discovery | `ivrv-1.0.0` | 0.2893 | **NONE (0 of 12)** |
| `b98c9685` | `ivrv_vrp_spread` | offline/confirmatory | `ivrv-1.0.0` | 0.2893 | **NONE (0 of 12)** |
| `0c7e1bf9` | `g3_atr_expansion` | offline/confirmatory | `phase_b_v2` | 0.2073 | **NONE (0 of 12)** |

Their metrics keys are the Phase-B discovery/confirmatory set — `sharpe`,
`oos_sharpe`, `evidence_score`, `wf_pass_frac`, `sens_pass_frac`,
`loo_sign_consistency`, `struct_sign_ok`, `breakeven_cost_bps`, `verdict` —
structurally disjoint from FDB-9's twelve.

**Meaning:** each is the in-sample raw `1 − psr(returns, 0.0)` produced by
`compute_dsr_for_registration.py` at `hold_days = 1` on the full corpus. Its own
source comments it *"IS, 1 - PSR(0)"* (`migrate_ivrv_admission.py:46`). It is
**not** a BH statistic of any kind — it is the *input* BH consumes.

Local ledger: **71 runs, all `offline-corpus`, 0 live-forward.** The 11 L2 rows
exist only on production, where FB-10b-P verified all of them carry **NULL**
`dsr_prob` and `bh_p`. *(A re-verification query this gate was refused by SQLite —
`attempt to write a readonly database`, the WAL-sidecar condition. I did not
escalate: forcing it would create `-shm`/`-wal` files on production, which this
gate forbids. The earlier reading stands and is not contradicted by anything
here.)*

### A.2 Is lane/engine/provenance available at each rendering point?

**Yes, at every one — no new data is needed anywhere.**

| renderer | what it holds | source |
|---|---|---|
| `report.py::_fmt_pinned_stat(pinned_run, key)` | the **entire** run dict from `record.get_run` — `provenance`, `run_kind`, `engine_version`, and `metrics` already parsed from `metrics_json` | `record.py:512-522` |
| `lifecycle.py:355` | `pinned_run`, the same dict | `lifecycle.py:347` |
| `src/api/services.py:256` | `pinned`, the same dict — and it **already returns `provenance`** on the same object | `services.py:245-261`, `schemas.py:102-107` |

### A.3 Soundness of F1 / F2 / F4

| | verdict | reason |
|---|---|---|
| **F1** — lane-derived | **Sound**, but indirect | Correct for every row that exists and every row the contract can produce (FDB-3/FDB-11 confine D2 results to the forward lane). It is sound *by consequence* of other clauses, and tests a **correlate** of the convention rather than the convention |
| **F2** — new stored marker | **Sound but redundant, and not implementable now** | Duplicates metadata FDB-9 **already mandates**. Its marker is written by the producer, so it cannot label anything until the producer exists |
| **F4** — suppress on offline lane | **UNSOUND** | Renders `not-computed` for a value that **was** computed, is stored, and is cited by name in `registry.yaml:277/403/413`, `CLAUDE.md §16` and both pre-registrations — the report would assert a falsehood. And it **cannot cover `lifecycle.py:355`**, which is the gate's own failure explanation and must print the number the gate compared |

### A.4 The fourth solution — **F5**, already supplied by frozen provenance

**FDB-9 is the discriminator.** It is adopted, frozen text:

> *"Every stored DSR/BH result must record: `battery_members[]` … `calc_version`.
> **A result missing any of these is uninterpretable and invalid.**"*

So under the adopted contract, a valid FB-10b result **is definitionally one that
carries the FDB-9 block.** A non-NULL `bh_p` without it was not produced under
this contract. That is not a proxy, a heuristic, or an inference — it is the
contract's own validity condition, applied as written.

**Verified this gate: all three legacy rows carry zero of the twelve FDB-9 keys.**
The partition is clean, with no overlap to adjudicate.

### A.5 Three-way distinction, without touching history

| observed | classification | basis |
|---|---|---|
| `bh_p IS NULL` | **uncomputed** | the existing `not-computed` sentinel; unchanged |
| `bh_p` non-NULL **and** FDB-9 block present | **D2 BH-adjusted q-value** | FDB-9 satisfied |
| `bh_p` non-NULL **and** FDB-9 block absent | **legacy raw p (pre-contract)** | FDB-9 unsatisfied ⇒ not an FB-10b result |

**F5 works today, before the producer exists** — the 11 current L2 rows have NULL
`bh_p` and classify correctly as *uncomputed*; the 3 legacy rows classify
correctly as *legacy*. Unlike F2, there is no interval in which the labelling is
incomplete.

**Smallest reversible presentation-only change:** one predicate plus one
formatter branch in `report.py`, called from the three existing sites. No new
file, no new stored field, no schema change, no migration; reversible by
`git revert`.

---

## B. Every committed surface that reads, renders, labels, exports or documents `bh_p`

### B.1 Renderers — can mislabel

| # | surface | code | can mislabel legacy raw p? |
|---|---|---|---|
| **R-A1** | `state_board.md` "Decision anchors" — column header **`BH-p`** | `report.py:361`, `:373` | **YES — actively does today.** Lines 12 / 17 print `0.2073` and `0.2893` under `BH-p` |
| **R-A2** | `library.md` — **`BH-p 0.2073`** | `report.py:444` | **YES — actively does today** (lines 117, 281) |
| **R-A3** | `state_board.md` validated-summary table | `report.py:612`, `:625` | **Latently** — dormant, no validated families |
| **R-B** | `ml_readiness.md` + `report.py --check` stdout + transition errors — *"BH-FDR p 0.2073 > q=0.1"* | `lifecycle.py:355` | **YES — actively does today** (`ml_readiness.md:22`, `:27`). Calls a raw p a "BH-FDR p" |
| **R-C** | read-only HTTP API, `PinnedRunModel.bh_p` | `src/api/services.py:256-257`, `schemas.py:102-103` | **YES.** Unlabelled float. `btcbot-api.service` verified **`enabled` + `active`** on production |

### B.2 Documentation — quotes a value

| surface | mislabel? |
|---|---|
| `docs/api/read-only-api.md:216` — *"BH-p 0.2893"* as an example response | **Yes, latently** — an unqualified example |

### B.3 Self-labelling — **not** defects, no change needed

All say **"IS"** explicitly: `registry.yaml:277/403/413` · `CLAUDE.md:529` ·
`library.md:130/293` · `ml_readiness.md` narrative · `migrate_ivrv_admission.py:46`
(*"IS, 1 - PSR(0)"*) · `migrate_g3_atr_admission.py:26` ·
`docs/research/*` specifications · `decision_function_ivrv.py:58`.

### B.4 Producers / storage — not presentation

`record.py:51/244/256` (column + writer) · `compute_dsr_for_registration.py`
(the D1 source) · `migrate_*.py` (historical, already run) ·
`thresholds.yaml:53/61` (`max_bh_p`, a threshold not a value).

### B.5 Tests asserting rendered output

`tests/test_evidence_report.py:297` asserts the decision-anchor row's evidence
columns including **BH-p**. Any change to `_fmt_pinned_stat`'s output must be
reconciled with it. No other test asserts the rendered label.

---

## C. Does the contract already imply convention identification at a surface?

**Partly — and the honest answer changed once FDB-7 was read correctly.**

**What IS already frozen:**

1. **FDB-7 binds the render layer**: *"computed, stored **or reported**."* The
   contract unambiguously reaches reporting. Render-layer obligations are **not**
   alien to it — there is precedent inside it.
2. **FDB-9** declares a metadata-less result *"uninterpretable and invalid."*
3. **FDB-4** declares a basis-mismatched result *"invalid."*

**What is NOT frozen:** FDB-7's reporting prohibition is scoped to *one*
situation — a family below its minimum, whose BH-p must not be reported at all.
**No clause requires a value that legitimately exists to be labelled with its
convention.** Combining FDB-9's "uninterpretable" with FDB-7's reporting reach to
produce a general labelling duty requires an inferential step the contract does
not take.

**Determination:** binding the render layer generally is **a genuine new
governance decision** — but a **narrower extension** than the decision matrix
claimed, because the contract already regulates reporting. It is not silently
added here.

---

## D. Historical safety of F5 — proof

| claim | proof |
|---|---|
| **Does not rewrite historical ledger rows** | F5 is a read of `metrics_json` inside a string formatter. The three legacy rows are append-only and untouched; verified unchanged this gate: `bh_p` = `[0.2073, 0.2893, 0.2893]` |
| **Does not recompute historical p-values** | F5 tests **key presence** in a dict. It never calls `psr`, `bh_fdr`, `deflated_sr`, or any statistical function on historical data |
| **Does not convert raw p into q** | The stored float is rendered **verbatim**, `.4f`, exactly as today. Only an adjacent marker is added. No arithmetic is applied to any legacy value |
| **Does not alter existing governance results** | `check_validated_preconditions` reads `pinned_run.get("bh_p")` — the **numeric column** (`lifecycle.py:347-356`), never the formatter. `_fmt_pinned_stat` has no caller in any gate path |
| **Does not affect Validated gating** | Two independent reasons: (a) the formatter is not on the gate path at all; (b) both legacy-pinned families are `candidate`, and Validated additionally requires non-offline provenance (`thresholds.yaml validated.*.requires_non_offline_provenance: true`), so an `offline-corpus` pinned run can never satisfy the gate regardless of its `bh_p` |

The same five properties hold for **F1**. They do **not** hold for **F4**, which
changes what the report asserts about a computed value.

---

## E. DECISION STATUS

### 1. MECHANICALLY RESOLVED

- **The discriminator.** **F5** — presence of the FDB-9-mandated metadata block —
  is determined by already-frozen text (FDB-9's own validity condition),
  requires **no new stored field**, and was verified to partition the real ledger
  cleanly (3 legacy rows: 0 of 12 FDB-9 keys). It distinguishes legacy raw p,
  future D2 q, and NULL/uncomputed, **without rewriting history** and **before
  the producer exists**.
- **F4 is eliminated on soundness**, not preference: it asserts `not-computed`
  for a computed, stored, publicly-cited value, and structurally cannot cover
  `lifecycle.py:355`.
- **F2 is eliminated as redundant** — it duplicates metadata FDB-9 already
  requires — and is not implementable in the current ordering.
- **F1 remains sound** but tests a correlate rather than the contract's own
  condition.
- **Historical safety is proven** (§D), not asserted.
- **The full surface inventory is established** (§B): three renderers, one live
  API, one documentation example, one test.

### 2. HUMAN DECISION REQUIRED

**Exactly one question remains — scope, not mechanism.**

Fixing **R-A** alone (`report.py`) is presentation-only, touches no gate, and is
reversible. But it leaves the collision live on **R-B** (`ml_readiness.md`, a
committed artifact, plus `--check` stdout) and **R-C** (a running production
API). Whether those must also be fixed is decided entirely by question C, which
§C shows is not settled by the contract.

> **DECISION FORM — one question**
>
> **Does FDB-13 bind the render layer?**
>
> ```
> [ ] YES — "Any rendered or surfaced bh_p must identify its statistical
>           convention."
>           ⇒ FDB-13 gains the clause; R-A, R-B and R-C must ALL be fixed in
>             the same change; R-C implies an API deployment (not yet
>             authorized by any gate); the present state becomes a contract
>             violation the moment FDB-13 is adopted.
>
> [ ] NO  — FDB-13 stays a storage/semantics rule.
>           ⇒ R-A is fixed as engineering hygiene under F5; R-B and R-C are
>             logged as known unlabelled surfaces; nothing forbids the
>             collision recurring on a future surface.
> ```

No second question. The F1/F2/F4/F5 choice is **not** on this form: F5 is
determined by §A.4, and F4 is eliminated by §A.3.

### 3. FORBIDDEN INFERENCES

1. **Do not** convert, rescale or re-derive any legacy raw p into an adjusted q.
2. **Do not** rewrite, backfill or annotate the three legacy ledger rows.
3. **Do not** render a computed legacy value as `not-computed` (F4) — it asserts
   a falsehood contradicted by `registry.yaml`, `CLAUDE.md §16` and both
   pre-registrations.
4. **Do not** use `engine_version` as the discriminator — it is free-form, its
   legacy values (`ivrv-1.0.0`, `phase_b_v2`) are not a closed set, and FDB-9
   supplies a contract-grounded test that needs no such enumeration.
5. **Do not** treat FDB-7's reporting clause as already requiring convention
   identification — it forbids reporting a value that must not exist; it does
   not label one that legitimately does.
6. **Do not** suppress or reword `lifecycle.py:355` in a way that stops the gate
   explaining which number it compared.
7. **Do not** add a `bh_p_definition` field (F2) while FDB-9's block already
   carries the same information.

### 4. MINIMUM CHANGE SURFACE

**If D-2 = NO** — one file:

| file | change |
|---|---|
| `research/evidence_library/report.py` | one predicate (FDB-9 block present?) + one branch in `_fmt_pinned_stat` (`:331-337`); the three call sites are unchanged in shape |
| `tests/test_evidence_report.py` | reconcile the `:297` assertion on the rendered evidence columns |

**If D-2 = YES** — additionally:

| file | change |
|---|---|
| `research/evidence_library/lifecycle.py` | `:355` message names the convention |
| `src/api/services.py` + `schemas.py` | carry the convention beside `bh_p` |
| `docs/api/read-only-api.md` | qualify the `:216` example |
| — | **an API deployment, which no gate has authorized** |

Not touched under either answer: `governance.py`, `forward_dsr_bh.py`,
`forward_snapshot.py`, `ledger_sync.py`, `record.py`, `thresholds.yaml`,
`registry.yaml`, pre-registrations, the schema, the ledger.

### 5. CAN FB-10b-T PROCEED?

**Not yet — but it is now blocked on one binary answer instead of two open
questions.**

On that answer, FB-10b-T runs end to end in a single pass: FDB-13 (with or
without the render clause) → additive `bh_adjusted_p` in `governance.py` →
`forward_dsr_bh.py` → `forward_snapshot.py` engine-scoped probe → the F5
presentation fix at the chosen scope → full test matrix and verification sweep.
**No production write**, per the standing instruction.

Unchanged and unaffected: today's battery is still **empty** — 7 / 7 / 8 / 6 / 3
/ 0 / 0 closed trades against minima 15 / 10 / 15 / 30 / 26 / 30 / 30 — so a
correct producer would refuse to compute regardless. There is no schedule
pressure on this decision.

---

## F. Verification

| check | result |
|---|---|
| Tracked files modified | **none** — `git diff` empty |
| FDB-13 | **0 occurrences** — not created |
| Ledger | **71 runs · 0 live-forward · 27 receipts**; legacy `bh_p` = `[0.2073, 0.2893, 0.2893]` unchanged |
| Production | **untouched**; one read-only query was refused by SQLite and **not** escalated |
| Lockbox | **not accessed, not spent** |
| Historical rows | **not rewritten, not recomputed, not converted** |

**STOP.**

---

## G. Trail

| Gate | Outcome |
|---|---|
| FB-10b-T | HALTED — two pinned runs render raw p under a `BH-p` heading |
| FB-10b-T1 (matrix) | F1/F2/F4 characterised; surface map corrected to three renderers |
| **FB-10b-T1 (audit)** | **F5 identified as contract-determined; F4 eliminated on soundness; FDB-7's reporting clause corrects the earlier "no clause touches rendering" claim; one binary human decision remains** |
