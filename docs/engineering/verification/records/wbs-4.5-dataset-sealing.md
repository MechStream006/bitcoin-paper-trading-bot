# WBS 4.5 — Dataset Sealing — Verification Record

| Field | Value |
|---|---|
| **Milestone** | WBS 4.5 — Seal (stage 5) + `dataset.seal` receipts (Sprint D3) |
| **Governing ADR** | [`ADR-I012`](../../../architecture/adr/ADR-I012-research-dataset-builder.md) §8 stage 5, §9, §11, §13.2 DG-1/DG-2/DG-3, §14 |
| **Roadmap** | [`phase_4_dataset_builder_roadmap.md`](../../phase_4_dataset_builder_roadmap.md) v1.0.2 §4.1 row 4.5 |
| **Date** | 2026-08-07 |
| **Implementer** | Claude Opus 5 |
| **Reviewer** | *(pending — Rohith)* |
| **Depends on** | 4.1 `d6b020d`, 4.2 `a203038`, 4.3 `8fff41d`, 4.4 `80b5382` |

**Pre-milestone production gate, before any code:** health `overall INFO` /
0 non-OK · Telegram delivered · collection `66/66` · 5 forward signals at
`2026-08-06` · `backup --all --dry-run` exit 0 · 0 failed units ·
`report.py --check` OK · chain valid (26). All five gates passed.

---

## 1. HZ-4.4-1 — the mandatory carry-over, resolved structurally

WBS 4.4 computed `capped_admissibility` (the meet **after** Tier D caps) but
carried it only on the report. §9 requires the sealed dataset to hold its
*"computed admissibility ceiling"*. Had sealing stored `computed_meet`, every
Tier D cap would be silently lost **at the exact moment the dataset becomes
citable** — a consumer would see a ceiling the validation never granted.

Resolved four ways, not one:

1. **`seal_dataset()` takes the `ValidationReport` object**, not loose values,
   so the ceiling cannot be supplied out of band.
2. **The persisted ceiling is `report.capped_admissibility`, verbatim.**
3. **Recomputation is impossible, not merely discouraged** — `sealing.py`
   imports neither `meet` nor `compose_admissibility`, asserted by an AST test
   that inspects the actual import nodes.
4. **`computed_meet` is stored alongside**, so the cap is auditable as a
   *difference* rather than inferred, and `verify_seal()` refuses a seal whose
   ceiling equals the raw meet while Tier D applied a cap.

`admissibility_ceiling()` is the single accessor a consumer should use, so
nobody has to remember which of two columns is authoritative.

## 2. DG-1 — sealing is a human act

§13.2: *"Sealing is a **human act** emitting a receipt (IQROS-P8, P10)."*
A service actor is refused — **the platform must not make its own research
citable**. Refusal happens before any write: no phase change, no sealed row,
**no receipt** (all three asserted).

Contrast **DG-2**: resolution and validation are *computations* and replay.
That is precisely why `dataset.seal` is registered **`discretionary`**, not
`derived`. A derived receipt is re-derivable by replaying its rule — so a
derived seal could be manufactured years later by re-running the battery.
Envelope verification is the correct semantics: *evidence admissibility is a
computation; sealing is a judgement.*

## 3. Taxonomy extension — additive, and authorised by the ratified roadmap

`dataset.seal` was **not** reserved in `DECISION_TAXONOMY`, and `seal` was not
in `ACTIONS`. Both were added.

- **Authorised:** roadmap v1.0.2 §4.1 row 4.5 names *"`dataset.seal` receipts"*
  in its own acceptance criteria, and the roadmap was ratified 2026-08-07.
  This executes ratified scope; it does not invent governance.
- **Precedent:** PR-7 added `"evaluate"`; WBS 3.8a added `"register"`,
  `"adjudicate"` and the `"label"` subject kind — each recorded as *"purely
  ADDITIVE: no existing receipt uses it, no existing validation is loosened."*
  The same reasoning and the same wording are used here.
- **`"promote"` was deliberately not reused.** §13.1 separates *Sealing* from
  *Promotion* as distinct authorities; overloading the verb would conflate
  making a dataset citable with advancing it through a research gate.
- **Verified non-destructive:** every pre-existing taxonomy entry is asserted
  still present, and the **real local ledger's 26-receipt chain verifies clean
  after the change** — no historical receipt is affected.

## 4. What sealing persists (§9, §14)

`sealed_datasets` carries the capped ceiling, the raw meet, the declared floor,
the individual Tier D caps, the full provenance closure, a validation
fingerprint, the resolution timestamp, the resolving engine version, the human
who sealed, when, and the receipt id.

**The closure is built from the WBS 4.2 citation index**, not by re-reading the
declaration — so the closure and the reverse index can never disagree about
what a dataset cites. §14 calls provenance completeness *"the rematerialisation
guarantee"*.

**`validation_fingerprint`** is a deterministic hash over the capped ceiling,
the raw meet, every finding, the tiers run, and what was deliberately *not*
computed. It answers the requirement *"verify the sealed artifact faithfully
represents the validation result"*: a seal cannot silently describe a cleaner
validation than actually occurred, and `verify_seal()` detects a substituted
report.

Admissibility is serialised by **rung name, not integer** — a sealed artifact
must stay readable if the enum's numeric backing ever changes.

## 5. Ordering, and which failure mode is made impossible

The receipt is appended **before** the phase advances, and the sealed row is
written in the **same transaction** as the phase change.

A crash after the receipt leaves a receipt with no sealed dataset — visible,
harmless, and detected by `find_orphan_seal_receipts()`. The reverse — a
**citable dataset with no receipt** — would be a governance hole, so it is the
ordering that cannot happen.

## 6. Verification

### §4 TV — 69 tests, `tests/test_dataset_sealing.py`

| Group | n | Covers |
|---|---|---|
| Build / scope | 4 | no `src` import, **no recomputation primitive importable**, additive taxonomy, discretionary class |
| **DG-1 human act** | 8 | service actor refused, no phase change, **no receipt written**, `sealed_by` required |
| **HZ-4.4-1** | 8 | cap persisted not raw meet, raw meet also recorded, accessor, `cap_was_applied`, per-cap detail, **`verify_seal` detects a lost cap** |
| Prerequisites | 9 | only Validated seals, mismatched report, failed validation, double seal, LR4 |
| Seal + receipt | 7 | citable at seal, **chain valid across several seals**, transition recorded, fingerprint bound |
| Persisted state | 6 | closure from the index, deferred/not-computed recorded, **survives reload in a fresh connection** |
| Determinism | 6 | fingerprint stable, order-independent, changes with ceiling and findings, names not values |
| DG-3 immutability | 6 | UPDATE/DELETE forbidden by trigger, ceiling uneditable, spec still tamper-proof, orphan detector |
| TV3 guards | 5 | non-vacuity of DG-1, HZ-4.4-1, phase guard, fingerprint |
| Regression | 9 | 4.1–4.4 invariants, **end-to-end retirement refusal** |

### §4 TV3 — sabotage

| Sabotage | Failures | Verdict |
|---|---|---|
| DG-1 neutered (service actor allowed) | **8** | load-bearing |
| **HZ-4.4-1 broken** — persist `computed_meet` instead of capped | **4** | load-bearing |
| DG-3 trigger neutered | **2** | load-bearing |
| Validation-clean precondition neutered | **1** | load-bearing |

Revert restored 69 passed; full dataset-builder suite 438 passed.

### §5 REG

| Environment | Result | Prior |
|---|---|---|
| `btc_bot` | **2828 passed, 19 skipped** | 2759 + 69 |
| `base` | **2379 passed, 62 skipped** | 2310 + 69 |

One pre-existing file modified: `research/evidence_library/receipts.py`, two
additive insertions (§3). No existing member touched.

### Invariants re-verified

Citation graph intact and bidirectionally consistent after seal · **a sealed
dataset pins its inputs at exactly the moment it becomes citable** (4.2's phase
gate composes with 4.5's transition) · **end-to-end retirement refusal now
bites** — seal a dataset and the Feature Store refuses to retire a cited
specification · registry lineage/ordinal preserved · declaration content and
hash unchanged · supersession still works after seal · local ledger chain
`ok=True, n_receipts=26`, `report.py --check` OK (35: 9/22/4).

## 7. Production verification

`overall INFO` · 0 non-OK · `btcbot` + `btcbot-api` active · 6 timers · **0
failed units** · collection `66/66` · disk 7.1 GB (75%) · `report.py --check`
OK · chain `ok=True, n_receipts=26` · trading/ledger/forward integrity
**ok/ok/ok**.

**No production change was made.** The taxonomy addition ships in the
repository but production runs an older commit; when deployed it is inert until
something emits a `dataset.seal`, and the existing chain verifies clean either
way.

## 8. Technical debt and hazards

| # | Item | Severity | Note |
|---|---|---|---|
| **TD-4.5-1** | The seal receipt goes to `evidence_library/ledger.db`, a **different database** from `dataset_builder.db`, so receipt and sealed row are not one transaction | 🟠 High | Deliberate — the ledger is the platform-wide chain and must not fork. Mitigated by ordering (§5) plus `find_orphan_seal_receipts()`. A cross-database atomic write is not available in SQLite; WBS 4.11 should decide whether a two-phase marker is warranted |
| **TD-4.5-2** | The already-sealed check is unreachable through the normal path — the phase guard fires first | 🟢 Low | Defence in depth against a hand-advanced phase, same class as `TD-4.4-1`. Recorded so it is not mistaken for untested |
| **TD-4.5-3** | `SEALING_ENGINE_VERSION` is a module constant, not derived from the code that produced the fingerprint | 🟡 Medium | A change to `validation_fingerprint()` without bumping it would make two incompatible fingerprints indistinguishable. A content hash of the serialisation would be stronger |
| **TD-4.5-4** | `resolution_ts` records the declaration timestamp, not a distinct resolution timestamp | 🟡 Medium | 4.3 does not persist when resolution ran. §9 asks for a resolution timestamp; supplying the declaration time is honest but approximate. 4.3's `ResolutionReport` should persist its own |
| **HZ-4.5-1** | Nothing yet consumes `admissibility_ceiling()` to gate a reader | 🟡 Medium | The ceiling is persisted and correct, but no consumer enforces it. RF4 protects the *declaring* side; the *reading* side arrives with materialisation (4.8) and Experiment Datasets (4.9) |
| **HZ-4.5-2** | A sealed dataset's ceiling can go stale if a cited input is later flagged | 🟠 High | ADR-I011 §10.3's superseded-input propagation is not built. The citation index makes the blast radius computable (4.2), but nothing recomputes or flags a sealed ceiling. **Named for WBS 4.11** |

## 9. Files changed

| File | Change |
|---|---|
| `research/dataset_builder/sealing.py` | **New** — stage 5, `sealed_datasets`, receipts |
| `tests/test_dataset_sealing.py` | **New** — 69 tests |
| `research/evidence_library/receipts.py` | +2 additive: `"seal"` action, `dataset.seal` decision type |

## 10. Reviewer Checklist (RC)

*Signed 2026-08-08 — see below.*

- [x] **RC1** — Milestone corresponds to WBS 4.5 exactly; no 4.6+ scope
- [x] **RC2** — No file under `docs/architecture/` touched
- [x] **RC3** — "Files Changed" matches `git status` exactly
- [x] **RC4** — At least two tests read in full
- [x] **RC5** — Full-suite counts reproduced in both environments
- [x] **RC6** — Rollback lever confirmed (two new files + one additive edit)
- [x] **RC7** — **The taxonomy extension (§3) is accepted** as ratified-roadmap scope, following the PR-7 / WBS 3.8a precedent *(basis: `OD-7`, Group E)*
- [x] **RC8** — **`dataset.seal` as `discretionary` (§2) is accepted** — a seal must not be re-derivable *(basis: `OD-7`, Group D)*
- [x] **RC9** — **HZ-4.4-1 is confirmed resolved** (§1) — the cap survives the seal, proven by sabotage *(basis: `OD-3`, Group F — discharges `4.4/RC9`)*
- [x] **RC10** — **`TD-4.5-1` accepted** — cross-database receipt/row atomicity is mitigated by ordering, not eliminated *(basis: `OD-7`, Group E)*
- [x] **RC11** — **`HZ-4.5-2` accepted as a WBS 4.11 requirement** — a sealed ceiling can go stale if a cited input is later flagged *(basis: `OD-3`, Group F — discharged by `4.11/RC10`)*
- [x] **RC12** — Sign-off

  **Reviewer:** Rohith Suresh  **Date:** 2026-08-08
