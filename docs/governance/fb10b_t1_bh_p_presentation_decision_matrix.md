# FB-10b-T1 — `bh_p` Presentation / Legacy Labelling Decision Matrix

**Date: 2026-09-01 (UTC) · Gate FB-10b-T1 · READ-ONLY.**

No implementation. No FDB-13 amendment. No DSR/BH computed. No lockbox access.
No production write. `governance.py`, `forward_snapshot.py`, `forward_dsr_bh.py`,
`report.py`, `lifecycle.py`, `src/api/*`, pre-registrations, `registry.yaml`,
`thresholds.yaml` and the ledger are **unmodified**. No historical row was read
for reinterpretation, rewritten, or converted.

**No option is selected.**

---

## 0. Scope correction — the surface is wider than FB-10b-T reported

FB-10b-T named *"three sites in `report.py`"*. A full repo sweep finds **three
independent renderers**, one of which is a **live network service**.

| # | renderer | code | reaches | status |
|---|---|---|---|---|
| **R-A** | `report.py::_fmt_pinned_stat` | `:373`, `:444`, `:625` | `state_board.md` (column header **`BH-p`**), `library.md` (**`BH-p 0.2073`**), validated-summary table | committed artifacts; `:625` currently dormant (no validated families) |
| **R-B** | `lifecycle.py:355` — `f"BH-FDR p {float(bh_p):.4f} > q={max_bh} …"` | `:355` | `ml_readiness.md` (committed), `report.py --check` stdout, `transition_governance` error paths | live on every gate evaluation |
| **R-C** | `src/api/services.py:256-257` → `schemas.py:102-103` `PinnedRunModel.bh_p` | — | read-only HTTP JSON | **`btcbot-api.service` verified `enabled` + `active` on production** |

A fourth, documentary: `docs/api/read-only-api.md:216` quotes *"BH-p 0.2893"* as
an example response.

**Not defects** — these already self-label as in-sample and need no change:
`registry.yaml:277/403/413`, `CLAUDE.md:529`, `library.md:130/293`, and the
`docs/research/*` specifications, all of which say **"IS"** explicitly.

**Fact that matters for every option below:** `provenance` **already travels
beside `bh_p`** in both `report.py`'s `row.pinned_run` and the API's
`PinnedRunModel`. The discriminator needed by F1 exists today, everywhere the
value is surfaced. No new data is required for it.

---

## 1. Question 1 — legacy `bh_p` presentation (F1 / F2 / F4)

### F1 — identify legacy values from the pinned run's **lane**

| | |
|---|---|
| **Files / code paths** | `report.py:331-337` (`_fmt_pinned_stat` gains the pinned run's provenance/run_kind); optionally `lifecycle.py:355`; optionally `src/api/services.py:256-257` (or left to the consumer, since `provenance` is already returned) |
| **Historical visibility** | **Fully preserved** — every legacy value still displays, now with its convention named |
| **Reproducibility** | **Improved.** The marker is *derived* from the row's own immutable lane, so re-rendering an old ledger at any later date yields the same label. Nothing is stored, so nothing can drift |
| **Historical rows untouched?** | **Yes** — read-only derivation |
| **Future D2 rows distinguishable?** | **Yes**, immediately and for every row, including the 11 existing L2 rows and any row written before the producer exists |
| **Smallest safe implementation** | one helper in `report.py` that appends a marker when the pinned run's provenance is `offline-corpus`; call it from the three existing sites. `lifecycle.py` and the API are separate, optional decisions |
| **Weakness** | lane is a **proxy** for convention, not the convention itself. It is correct for every row that exists today (all three legacy values are `offline-corpus`; every D2 row will be `live-forward`/`mainnet`), but it would mislabel a hypothetical future offline-lane row computed under D2 |

### F2 — identify legacy values from an explicit convention marker

| | |
|---|---|
| **Files / code paths** | the producer stamps `bh_p_definition` into `metrics_json`; `report.py:331-337` reads it; `lifecycle.py:355` and `src/api/*` each need the same read to stay consistent. **`record.py` and the ledger schema are NOT touched** — `metrics_json` is free-form and `append_run` already merges arbitrary keys |
| **Historical visibility** | **Fully preserved** |
| **Reproducibility** | **Strongest.** The convention is recorded *by the producer that applied it*, not inferred. Absence of the marker is itself the legacy signal — the same informative-absence pattern `collection_provenance` already uses (*"absence means collected before 6B.3"*) |
| **Historical rows untouched?** | **Yes.** The three legacy rows are never rewritten; they are legacy *because* they carry no marker |
| **Future D2 rows distinguishable?** | **Yes — but only from the first producer-written row onward.** Everything before it, including the 11 existing L2 rows, is covered solely by the absence rule |
| **Smallest safe implementation** | a payload field plus a read in `_fmt_pinned_stat`; the marker cannot be added in this gate (*"Do not add a compatibility field yet"*), so F2 is **not implementable until the producer exists** |
| **Weakness** | ordering: the marker arrives with the producer, so the fix and the thing it labels land together — there is no interval in which the labelling is complete but the producer is absent |

### F4 — suppress `bh_p` for offline/confirmatory pinned runs

| | |
|---|---|
| **Files / code paths** | `report.py:331-337` returns `not-computed` when the pinned run's lane is offline. **`lifecycle.py:355` cannot follow suit** — see below |
| **Historical visibility** | **Lost.** Two currently-visible values (`ivrv_vrp_spread` 0.2893, `g3_atr_expansion` 0.2073) disappear from `state_board.md` and `library.md` |
| **Reproducibility** | **Degraded, and misleadingly so.** The value would render as `not-computed`, which in this codebase means *"nobody computed it"* — but it **was** computed, is stored, and is cited by name in `registry.yaml`, `CLAUDE.md §16` and both pre-registrations. The report would assert something untrue |
| **Historical rows untouched?** | Yes |
| **Future D2 rows distinguishable?** | Yes, trivially — legacy rows show nothing |
| **Smallest safe implementation** | a lane check returning the existing `NO_RUN`/`not-computed` sentinel |
| **Structural incompleteness** | **`lifecycle.py:355` is a gate-failure explanation.** It exists to say *why* Validated was refused, and the number it prints is the number the gate actually compared. Suppressing it would leave the gate unable to explain itself; not suppressing it leaves an unlabelled legacy value on the R-B surface and in committed `ml_readiness.md`. **F4 therefore cannot cover all three renderers** — it is a report-only fix |

### 1.1 Comparison

| | **F1** lane-derived | **F2** stored marker | **F4** suppress |
|---|---|---|---|
| History visible | ✅ full | ✅ full | ❌ removed |
| Rows untouched | ✅ | ✅ | ✅ |
| D2 distinguishable | ✅ all rows | ⚠️ producer-written rows; earlier by absence | ✅ |
| Reproducible label | ✅ derived, stable | ✅ recorded at source | ⚠️ asserts "not-computed" for a computed value |
| Covers R-A | ✅ | ✅ | ✅ |
| Covers R-B (`lifecycle.py`) | ✅ optional | ✅ optional | ❌ structurally cannot |
| Covers R-C (live API) | ✅ optional | ✅ optional | ⚠️ would remove a field consumers receive |
| Implementable **now** | ✅ | ❌ needs the producer | ✅ |
| New stored data | none | one payload key | none |

*(F3 — a split column — was raised in FB-10b-T and is outside this gate's scope;
it is neither analysed nor excluded here.)*

---

## 2. Question 2 — should FDB-13 bind the render layer?

**Proposed clause:** *"Any rendered or surfaced `bh_p` value must identify its
statistical convention."*

### 2.1 What the existing contract supports — and where it stops

**Supported:** the interpretability *principle* is already frozen.
FDB-9 — *"A result missing any of these is **uninterpretable and invalid**"*;
FDB-4 — a result whose bases disagree *"is invalid"*; FB-10a §4 — *"Without
frozen membership and ordering a stored BH-p cannot be reproduced or **honestly
interpreted**."*

**Not supported:** every one of those clauses governs **storage**. No frozen
clause anywhere in FDB-1 … FDB-12 mentions rendering, reporting, display, or an
API. Extending the principle to surfaces is a **genuine widening of the
contract's scope**, not a mechanical consequence of it.

### 2.2 Therefore

**This is NOT mechanically determined. It is a human decision**, and the two
answers differ materially:

| | **YES — bind the render layer** | **NO — storage only** |
|---|---|---|
| Nature | **Creates a new implementation obligation**, not documentation | Documentation of an existing storage rule |
| Immediate effect | The current state becomes a **contract violation the moment FDB-13 is adopted** — three renderers, two committed artifacts and one live API surface all fail it. The fix must ship **in the same change**, not after | The `report.py` collision becomes engineering hygiene; it can be sequenced independently |
| Ongoing effect | Binds **every future surface** — a dashboard, a Telegram alert, a new API field — permanently | Nothing forbids the collision recurring on a new surface |
| Blast radius | `report.py`, `lifecycle.py`, `src/api/*` — the last of which is **live on production** and would need a deployment | `report.py` only, at whatever pace is chosen |
| Risk if chosen | The contract asserts something the platform must then continuously satisfy across surfaces nobody has built yet | A frozen rule about interpretability that the platform's own outputs do not honour |

### 2.3 Draft wording — **NOT adopted, for the YES case only**

> **FDB-13.7 — Convention identification at the surface.** Any artifact,
> report, API response or other surface that renders a `bh_p` value must, at
> that surface, identify which convention produced it: the **D2 adjusted
> q-value** defined above, or a **legacy raw p-value** written before this
> addendum. A `bh_p` presented without its convention is uninterpretable in the
> sense of FDB-9 and must not be compared with a value of the other convention.

If the answer is **NO**, FDB-13 stops at the storage/semantics clauses already
enumerated in the FB-10b-T gate instruction and no such clause is added.

---

## 3. FINAL DECISION TABLE

| | **D-1 · Legacy presentation** | **D-2 · FDB-13 render binding** |
|---|---|---|
| **Decision required** | which of F1 / F2 / F4 fixes the collision | does FDB-13 bind surfaces, yes or no |
| **Choices** | **F1** lane-derived marker · **F2** stored convention marker · **F4** suppress on offline lane | **YES** / **NO** |
| **Frozen constraints** | never rewrite historical rows; never convert raw p to adjusted q; not cosmetic; no compatibility field may be added in this gate (which defers F2 by construction) | FDB-9/FDB-4 support the *principle*; no frozen clause reaches presentation |
| **Irreversible consequence** | F4 removes two currently-published values and labels a computed statistic `not-computed`; F1/F2 remove nothing | YES makes the present state a violation on adoption and binds all future surfaces; NO leaves surfaces ungoverned |
| **Files affected** | `report.py` (all three options); `lifecycle.py:355` and `src/api/*` only if D-2 = YES or chosen separately | `docs/governance/fb10b_forward_dsr_bh_contract.md` (append-only), plus whatever D-2 = YES obliges |
| **Lockbox involved?** | **No** | **No** |
| **Interaction** | If D-2 = YES, D-1 must cover **R-A, R-B and R-C**, which rules F4 out structurally (§1, F4) and makes an API deployment part of the change | — |

### 3.1 Exactly what human decision is required

> **1. D-1 — choose F1, F2 or F4.**
> **2. D-2 — FDB-13 render-layer binding: YES or NO.**

Nothing else is outstanding on this question. Both answers together determine
whether FB-10b-T can resume as a single pass, and how wide that pass is.

**Two facts to weigh, stated without recommendation:** F2 cannot be implemented
before the producer exists, because its marker is written by the producer — so
choosing it means the labelling and the thing it labels arrive together. And
`btcbot-api.service` is **enabled and active on production**, so any option
extended to R-C implies a deployment, which no gate has authorized.

---

## 4. Verification

| check | result |
|---|---|
| Tracked files modified | **none** — `git diff` empty |
| FDB-13 in the contract | **0 occurrences** — not written |
| Ledger | **71 runs · 0 live-forward rows with non-NULL `dsr_prob` · 27 receipts** — unchanged |
| Production | **untouched**, `af80fbd`; only read-only `systemctl is-enabled` / `is-active` queries were issued |
| Lockbox | **not accessed, not spent** |
| Historical rows | **not rewritten, not reinterpreted, not converted** |

**STOP.**

---

## 5. Trail

| Gate | Outcome |
|---|---|
| FB-10b-S | D2 / T1 / C2 decided; `runs.bh_p` mixed semantics raised |
| FB-10b-T | HALTED — two pinned runs render raw p under a `BH-p` heading |
| **FB-10b-T1** | **This matrix. Surface map corrected to three renderers incl. a live API; F1/F2/F4 characterised; FDB-13 render binding shown to be a human decision, not mechanically determined** |
