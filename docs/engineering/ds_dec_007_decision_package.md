# `DS-DEC-007` — Decision Package

| Field | Value |
|---|---|
| **Decision** | Amendment of Phase 4 exit criterion `P4-9` |
| **Raised by** | Phase 4 Governance Closure Report §12 (2026-08-08) |
| **Decision owner** | Rohith |
| **Status** | ✅ **RATIFIED 2026-08-08.** Applied to `phase_4_dataset_builder_roadmap.md` §8.2 (v1.0.3) and `phase-4-governance-closure-report.md` §12 |
| **Prepared** | 2026-08-08 |
| **Ratified** | 2026-08-08, by Rohith — exact wording per §8, unmodified |
| **Blocking** | Phase 4 formal closure; Phase 5 entry gate `E2` |

---

## 1. The criterion as written

> **`P4-9`** — *"`report.py --check` clean; `verify_chain()` clean on **all four
> ledgers**; both full suites at zero new failures."*

## 2. Verified state at commit `82ce7c0`

| Component | Status |
|---|---|
| `report.py --check` clean | ✅ 35 families (9 candidate / 22 discovery / 4 disqualified) |
| Both full suites, zero new failures | ✅ `btc_bot` 3290/19 · `base` 2841/62 |
| `verify_chain()` on "all four ledgers" | ⚠️ **three chains exist; all three verify** |

| Chain | Receipts | `verify_chain()` |
|---|---|---|
| `research/evidence_library/ledger.db` | 27 | ✅ `ok=True` |
| `research/feature_store/feature_store.db` | 17 | ✅ `ok=True` |
| `research/label_store/label_store.db` | 8 | ✅ `ok=True` |
| `research/dataset_builder/dataset_builder.db` | — | **no chain, by design** |

## 3. Rationale

The roadmap was written on 2026-08-06, anticipating that the Dataset Builder
would carry its own receipt ledger — a fourth chain beside evidence, feature and
label.

**`TD-4.5-1` (WBS 4.5) then decided the opposite**, on stated architectural
grounds: *"the ledger is the platform-wide chain and must not fork."*
`dataset.seal` receipts are written to `evidence_library/ledger.db`, which is
why that chain moved 26 → 27 at operational closure.

The criterion therefore counts a ledger the architecture **deliberately declined
to create**. Every chain that exists verifies clean, and the receipts the fourth
would have held are present and verified in the first.

**This is not an editorial fix.** `P4-9` is a ratified exit criterion; changing
it changes what "Phase 4 is complete" means. Hence a decision.

## 4. Consequences of ratifying

- `P4-9` becomes **MET**. Phase 4 closes on nine of ten criteria fully met, with
  `P4-7` partial (L1/L2 demonstrated on fixtures, not on the real dataset —
  `TD-4.12-1`).
- The criterion tracks the architecture rather than a superseded assumption, and
  remains correct if a fourth chain is ever added.
- `TD-4.5-1`'s ruling is recorded at roadmap level, where a future reader meets
  it before wondering why no builder ledger exists.
- **Cost:** amends a ratified criterion. The precedent must stay narrow — the
  amendment is warranted because the *architecture* changed under the criterion,
  not because the criterion proved inconvenient.

## 5. Consequences of rejecting

- `P4-9` is **permanently unmeetable** unless a Dataset Builder receipt ledger is
  created.
- Creating one **forks the platform chain**, directly contradicting `TD-4.5-1`,
  which was accepted on the reasoning that a single chain is the point.
- Phase 4 cannot be declared closed against its own criteria, so Phase 5 entry
  gate `E2` never clears.

## 6. The option that must not be taken

Declaring `P4-9` *"met in spirit"* without amending it would be the worst
outcome — worse than either ratifying or rejecting. It sets the precedent that
exit criteria may be waived informally, which is the discipline this platform
exists to protect.

## 7. Recommendation

**RATIFY.** The implementation is complete and correct; the wording describes a
platform that was deliberately not built.

## 8. Exact wording for ratification

*Reproduced for the owner's use. Applying it is a separate act — this package
does not apply it.*

> **`DS-DEC-007` — Amendment of exit criterion `P4-9`. RATIFIED.**
>
> `P4-9` is amended to read:
>
> *"`report.py --check` clean; `verify_chain()` clean on **every governance
> chain the platform maintains** — as of Phase 4 close these are
> `research/evidence_library/ledger.db`,
> `research/feature_store/feature_store.db` and
> `research/label_store/label_store.db`; both full suites at zero new
> failures."*
>
> **Note appended to the criterion:** *"The Dataset Builder maintains no
> separate chain. Per `TD-4.5-1` (WBS 4.5), `dataset.seal` receipts are written
> to the platform-wide evidence ledger, because the platform chain must not
> fork. Verifying that ledger verifies them."*
>
> **Scope of this amendment:** wording only. No implementation change, no
> receipt re-issued, no chain re-verified differently, no other exit criterion
> affected. The original wording remains in the roadmap changelog.

## 9. On ratification, update — DONE 2026-08-08

| File | Change | Status |
|---|---|---|
| `phase_4_dataset_builder_roadmap.md` | `P4-9` row amended (§8.2, v1.0.3); original wording preserved inline, not deleted | ✅ |
| `phase-4-governance-closure-report.md` | §12 status `AMENDMENT REQUIRED` → ratified; §8 table, §14, §15 line items updated | ✅ |
| `phase-4-governance-completion-package.md` | §2 status; §5.6 gate `E2` | ✅ |
| This package | Status → **RATIFIED**, with date | ✅ |

**`DS-DEC-007` is ratified and applied exactly as written in §8** — wording
only, no implementation change, no receipt re-issued, no chain re-verified
differently, no other exit criterion affected.
