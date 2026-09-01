# FB-10b-T — STOP: `bh_p` label collision in `report.py`

**Date: 2026-09-01 (UTC) · Gate FB-10b-T · HALTED AT PREFLIGHT.**

**Nothing was implemented. FDB-13 was NOT written. `governance.py`,
`forward_dsr_bh.py` and `forward_snapshot.py` were NOT created or modified. No
tests were added. No DSR/BH was computed. No lockbox was accessed. No production
write, no deployment. The ledger is unchanged.**

Halted under this gate's own explicit instruction:

> *"If the current report can mislabel a legacy raw-p value as a BH-adjusted
> q-value, **STOP and report that as a required compatibility fix before
> implementation continues**."*

**The condition is satisfied.** Evidence below.

---

## 1. The finding

`report.py` renders `runs.bh_p` from the **pinned run** under a column literally
headed **`BH-p`**, at three sites, with **no discriminator** for which convention
produced the number:

| site | context |
|---|---|
| `report.py:361` / `:373` | *Decision anchors* table header and cell — covers **every** family with a pinned run, not just validated ones |
| `report.py:444` | per-family detail line, `BH-p {value}` |
| `report.py:612` / `:625` | validated-summary table header and cell |

`_fmt_pinned_stat(pinned_run, "bh_p")` (`report.py:331-337`) reads the column
verbatim and formats it `.4f`. There is no lane check, no engine check, and no
convention marker.

### 1.1 Two families are pinned to legacy raw-p rows, today

| family | state | pinned run | lane | `bh_p` | convention |
|---|---|---|---|---|---|
| `ivrv_vrp_spread` | candidate | `b98c9685` | offline-corpus/confirmatory | **0.2893** | **D1 — raw p** (`1 − psr0`, IS) |
| `g3_atr_expansion` | candidate | `0c7e1bf9` | offline-corpus/confirmatory | **0.2073** | **D1 — raw p** (`1 − psr0`, IS) |

### 1.2 It is already rendered — this is not hypothetical

From the committed, generated `research/evidence_library/state_board.md`:

```
 7 | Family | Role | State | Since | Decided on (pinned) | Lane | Sharpe | DSR | BH-p | Flags |
12 | g3_atr_expansion | edge    | **candidate** | 2026-06-25 | 0c7e1bf9 | L1 | +0.49 | 0.270 | 0.2073 |  |
17 | ivrv_vrp_spread  | feature | **candidate** | 2026-06-21 | b98c9685 | L0 | +0.26 | 0.165 | 0.2893 |  |
```

Both numbers are raw p-values sitting under a heading that reads **BH-p**.

---

## 2. Why this blocks, and why it is not cosmetic

Under the adopted **OD-4 = D2** decision, `bh_p` means the **BH-adjusted
q-value**. The moment the producer writes its first forward row, the same column
carries two different statistics:

- offline-pinned families → **raw p** (D1), never adjusted, never battery-ranked;
- forward-pinned families → **adjusted q-value** (D2), ranked within a frozen
  battery.

A reader comparing `g3_atr_expansion`'s `0.2073` against a forward family's
adjusted q would be comparing **different quantities under one label** — and
would read the legacy values *as* BH-adjusted q-values, which they are not. A raw
p is not a BH-p; it is the input BH consumes.

**This is precisely the interpretation FDB-13 is being asked to forbid.** The
gate's own required FDB-13 content includes:

> *"offline historical `bh_p` values are legacy raw-p values and must not be
> interpreted as forward D2 results"*

Writing that rule into the frozen contract while the platform's own governance
report continues to invite exactly that interpretation would create a documented,
shipping violation on the day the rule is adopted. That is why this is a
**compatibility fix**, not a cosmetic preference, and why it is sequenced
*before* FDB-13 rather than after.

**This is the acknowledgment item raised at FB-10b-S §3, now upgraded from
"acknowledge" to "blocking" by direct evidence** that two pinned runs already
render under the colliding label.

---

## 3. Why FDB-13 was not written first

The gate orders FDB-13 before implementation. I stopped short of it deliberately:

1. Appending to `fb10b_forward_dsr_bh_contract.md` is an **append-only act on a
   frozen, adopted contract** — irreversible by convention.
2. FDB-13's own required content is the rule this finding shows to be currently
   unenforceable in the report layer.
3. If you sequence differently on seeing this — fix `report.py` first, or adjust
   FDB-13's wording to cover the render layer explicitly — an already-appended
   FDB-13 would have to be superseded by a second addendum rather than written
   once, correctly.

Stopping fully is the reversible choice. Nothing is lost: the entire change
surface from FB-10b-S §4 remains valid and unexecuted.

---

## 4. The required compatibility fix — scope, not selection

The fix must live **entirely in `report.py`'s rendering**. Two constraints from
this gate bound it:

- *"Never rewrite historical rows"* — the three legacy rows are append-only
  history and stay exactly as they are.
- *"Do NOT change `report.py` merely for cosmetic reasons"* — the change must
  remove the collision, not restyle the table.

**No option is selected here.** Four that satisfy both constraints:

| # | approach | preserves history? | shows legacy values? | notes |
|---|---|---|---|---|
| **F1** | mark the value by the pinned run's **lane**: offline-lane `bh_p` renders with an explicit legacy marker (e.g. `0.2073ᴰ¹`), forward/production renders plain | yes | yes | purely derived from data already on the row; needs no new field and works for rows written before the producer exists |
| **F2** | mark by an explicit `bh_p_definition` marker in `metrics_json`, which the producer stamps on every row it writes; absent marker renders as legacy | yes | yes | the informative-absence pattern `collection_provenance` already uses; requires the producer to exist, so legacy rows are covered only by the absence rule |
| **F3** | split the column: `BH-p (D2)` populated only for rows carrying the D2 marker; legacy values move to a separate legacy column | yes | yes | most explicit, widest diff to `report.py` |
| **F4** | suppress `bh_p` for offline-lane pinned runs, rendering `not-computed` | yes | **no** | smallest diff, but hides real historical information that is currently visible — a regression in observability |

**A decision is also needed on whether FDB-13's wording should bind the render
layer** (i.e. state that any surface displaying `bh_p` must identify its
convention), or remain silent on presentation. FB-10b-S's draft content did not
address the render layer, because the blocking evidence had not yet been
established.

---

## 5. State of the preflight otherwise

Everything else from FB-10b-S re-verified clean and unchanged this gate:

| check | result |
|---|---|
| Local ledger | **71 runs · 0 live-forward rows with non-NULL `dsr_prob` · 27 receipts** — unchanged |
| Production | **untouched**, `af80fbd`, 82 runs. No execution, no write, no deploy |
| Lockbox | **not accessed, not spent** |
| Tracked files modified | **none** (`git diff` empty; only untracked governance records present) |
| `ledger_sync.py`, `thresholds.yaml`, `registry.yaml`, pre-registrations, `lifecycle.py`, `record.py`, schema | **unmodified** |
| Change surface from FB-10b-S §4 | **still valid, still unexecuted** |

---

## 6. What is needed to resume

1. **Authorize a compatibility fix** for `report.py` — F1, F2, F3, F4, or another.
2. **Confirm whether FDB-13 binds the render layer.**

On those two answers, FB-10b-T can be re-run end to end in a single pass:
FDB-13 addendum → `governance.py` additive `bh_adjusted_p` → `forward_dsr_bh.py`
→ `forward_snapshot.py` engine-scoped probe → the `report.py` fix → the full test
matrix and verification sweep. No production write, per the standing instruction.

**Note on today's battery, unchanged and unaffected by this stop:** it remains
**empty** — 7 / 7 / 8 / 6 / 3 / 0 / 0 closed trades against minima
15 / 10 / 15 / 30 / 26 / 30 / 30 — so a correctly-built producer would refuse to
compute today regardless. Nothing here creates schedule pressure.

**STOP.**

---

## 7. Trail

| Gate | Outcome |
|---|---|
| FB-10b-R | Decision form: OD-4, OD-2, OD-5-collision put to a human |
| FB-10b-S | D2 / T1 / C2 decided; preflight clean; `runs.bh_p` mixed-semantics raised as an acknowledgment item |
| **FB-10b-T** | **HALTED. The acknowledgment item is blocking: two pinned runs already render raw p under a `BH-p` heading. No FDB-13, no code, no tests** |
