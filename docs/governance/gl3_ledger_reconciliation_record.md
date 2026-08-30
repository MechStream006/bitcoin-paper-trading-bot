# GL-3 — Ledger Reconciliation Record, and the Fate of the Superseded Local Archive

**Date: 2026-08-30 (UTC) · Companion to `gl2_ledger_canonicity_decision.md`**

GL-2 decided *which* ledger is canonical. This record states *what was done* to
give effect to that decision, and settles the one artifact GL-2 deliberately
left open: the superseded local chain that holds the only original of the
`dataset.seal` receipt.

No ledger, registry, threshold, sealed artifact, collection config, timer or
production code was touched by GL-3e. This file and two surgical edits are the
whole of it.

---

## 1. What GL-3 did

| Sub-gate | Action | Outcome |
|---|---|---|
| **GL-3a** | Rebuilt the local ledger from production (adoption-and-rebuild, never merge) | Local became byte-identical to production `0f6c2942…`; the superseded local chain was archived, not deleted |
| **GL-3b (attempt 1)** | Tried to append the `dataset.seal` re-attestation | **Refused by the database** — production's `governance_receipts` lacked `attestation_basis`. Rolled back cleanly; nothing written |
| **GL-3c** | Ran `migrate_receipt_attestation_basis.py --commit` on the canonical ledger | Two nullable columns added; all 26 receipts proven content-identical; `0f6c2942…` → `7fccf31a…` |
| **GL-3b (resume)** | Appended the re-attestation | seq 27, `7dae862f6afcedbe821d538463073124e7821bfe817bdb1208554174bac6eecd`; `7fccf31a…` → `dd568de2…` |
| **GL-3d** | Re-synced local from production | Both copies at `dd568de2…`, 27 receipts, `verify_chain ok=True` |
| **GL-3e** | This record + two documentation corrections | No state change |

**Current state of both copies:** SHA-256
`dd568de2f50a23a4c7cb314cc506c7a10f5b3e374670054da1a462875c51b2db`, counts
`[71 runs, 27 receipts, 13 transitions, 145 gate_results]`, `verify_chain
ok=True, n_receipts=27`, 23-column `governance_receipts`.

**A finding worth carrying forward:** GL-3b's first attempt exposed that the
canonical ledger could not accept *any* receipt — not `dataset.seal`, not
`family.transition`, not `family.promote` — because the schema migration had
never been run there. The guard that stopped it was deliberate: `receipt_ledger.
init_db()` refuses to add columns precisely so a read-only verification sweep
cannot silently mutate the ledger's schema before a migration is authorised. It
worked, including against an agent. That failure was the useful part of GL-3b.

## 2. The superseded local archive — custody decision

### 2.1 What it is

```
research/evidence_library/ledger.superseded-local.20260828T063031Z.db
research/evidence_library/ledger.superseded-local.20260828T063031Z.db.sha256

SHA-256 : 682ff9af087f3ac14c03bb63ac83b6a354588b85a87aacab031074fc59ca7c27
size    : 180,224 bytes
chain   : verify_chain ok=True, n_receipts=27
holds   : the ORIGINAL dataset.seal receipt
          871a4e54b5ab144f99637dc31bf11d11168a068b757e03b31a35d666900c6378
          seq 27, decided_at 2026-08-08T11:57:35.797294+00:00
```

It is the local governance chain GL-2 retired. It is **immutable** — nothing
will ever be appended to it again — and it is the **only** place the original
sealing receipt exists. The canonical ledger carries a *re-attestation* of that
act (seq 27, `7dae862f…`), which points back at the original by id and timestamp
but is not the original.

**Ruling: preserved permanently. It must not be deleted, pruned, or rotated
out.** Its evidentiary value is precisely that it is the untouched original.

### 2.2 The gap this record exists to name

Two facts, both verified 2026-08-30:

1. **It is not covered by any backup.** `scripts/backup.py --ledger` backs up
   `research/evidence_library/ledger.db` only. Sibling files in that directory
   are not in scope, so neither the local nor the off-host (BK3) path touches
   the archive.
2. **It is not gitignored either.** `.gitignore:39` covers
   `research/evidence_library/ledger.pre-*.db`, which does **not** match
   `ledger.superseded-local.*.db`. The archive is currently untracked but
   *committable* — a stray `git add -A` would sweep a database into the
   repository against the standing convention.

So today the archive exists in exactly one place: one developer workstation,
with no backup and no policy. That is the weakest custody of any governance
artifact on this platform, and it holds an original that cannot be reproduced.

### 2.3 Treatment

**Not chosen: commit it to git.** The repository has a firm convention that no
`.db` lives in version control (`.gitignore` §"Governance ledger — append-only
runtime data, backed up separately"), and BK3 exists specifically to give
irreplaceable ledger bytes encrypted, versioned, Object-Locked off-host custody
that has been drill-proven (BK2, 2026-08-12). Committing a database to solve a
backup problem would trade a proven mechanism for a convention violation.

**Chosen treatment — two actions, neither performed here:**

- **A1 — ignore it like its siblings.** Add
  `research/evidence_library/ledger.superseded-local.*.db*` to `.gitignore`
  alongside the existing `ledger.pre-*.db` line, so the archive cannot be
  committed by accident. *(Not a documentation change; deliberately left for a
  gate that is authorised to edit `.gitignore`.)*
- **A2 — one-time off-host custody under BK3.** Encrypt the archive and its
  `.sha256` manifest to the existing BK3 `age` recipient and store them in
  `s3://btcbot-offhost-backup-a71f9c2e` under a path that marks them
  **permanent**, explicitly **excluded from the 7-daily/4-weekly/12-monthly
  rotation** that `scripts/backup.py::_apply_retention()` applies. Retention
  pruning is correct for rolling snapshots and wrong for a one-off archive; the
  distinction must be explicit or a future retention pass will delete it.

**Until A2 is performed, the archive is at risk and this record says so.** It is
not protected by anything today. No claim to the contrary should be made on the
basis of this document existing.

### 2.4 Retention semantics, stated once

| Artifact class | Retention |
|---|---|
| Live canonical ledger | daily snapshot, rotated, off-host required (ADR-0001 §12) |
| `ledger.pre-*.db` migration backups | operational; may be pruned once the migration is proven and recorded |
| **`ledger.superseded-local.*.db`** | **permanent, never pruned, never rotated** |

## 3. Documentation corrections made at GL-3e

**`docs/ops/bk3-activation-plan.md` §5.4** — its pass condition read *"ledger 27
canonical / 26 production"*, naming the **local** copy as canonical. GL-2
reversed that. The line now names the production path as canonical, cites GL-2,
and carries a visible correction note. The quoted counts were also stale twice
over — both copies now hold 27 — so the corrected text tells the operator to
take a live `verify_chain()` reading rather than trust a number in a document.

**`docs/adr/0001-infrastructure-architecture.md` §12 BK1** — BK1 called the
ledger irreplaceable without saying *which file* that meant, which is how two
copies drifted apart unnoticed for weeks. It now names the canonical path,
states that the workstation copy is a replica rather than a second authority,
records that a merge is structurally impossible, and links both this record and
GL-2.

**Deliberately not changed:** the many `n_receipts=26` references in
`docs/engineering/verification/records/*`, `INC-0005`, the BK2 drill record and
ADR-0001 §12 BK2's own status line. Those are **historical readings** that were
correct when taken. Rewriting them would falsify the record — the same principle
that made the `dataset.seal` a re-attestation rather than a transplant.

## 4. State at this record

| | |
|---|---|
| Canonical ledger | `dd568de2…`, 27 receipts, chain ok, production |
| Local replica | `dd568de2…` — byte-identical |
| Archive | `682ff9af…`, chain ok, 27 receipts, **unbacked-up** (§2.2) |
| Production HEAD | `a72a064` |
| Forward collection | 274 observations, 7 signals; `g1_trend_regime_ma_cross` collecting since `run_date` 2026-08-28 |
| Registry / thresholds / lockbox / sealed artifacts / config / timer | unchanged |

## 5. Open items after GL-3

- **A1 / A2 above** — the archive's `.gitignore` entry and its off-host custody.
- **The three blockers on a first Validated family**, unchanged by any of GL-3:
  no `live-forward` (L2) run exists in the ledger and no bridge writes one;
  `pairwise_r` is empty on the canonical ledger; and observation floors are
  years from being met on current counts. See the daily-lane readiness audit.
