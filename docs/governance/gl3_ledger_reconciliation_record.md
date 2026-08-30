# GL-3 — Ledger Reconciliation Record, and the Fate of the Superseded Local Archive

**Date: 2026-08-30 (UTC) · Companion to `gl2_ledger_canonicity_decision.md`**

GL-2 decided *which* ledger is canonical. This record states *what was done* to
give effect to that decision, and settles the one artifact GL-2 deliberately
left open: the superseded local chain that holds the only original of the
`dataset.seal` receipt.

No ledger, registry, threshold, sealed artifact, collection config, timer or
production code was touched by GL-3e, GL-3f or GL-3g. GL-3f's only write was
one `.gitignore` line and one read-only encrypt-and-upload of an existing file;
GL-3e and GL-3g are documentation.

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
| **GL-3f** | Archive custody: `.gitignore` (A1) + encrypted off-host upload (A2) | A1 committed `50afb63`, CI green; A2 stored under `archive/`, ciphertext verified |
| **GL-3g** | Closed §2.3 with the GL-3f evidence | No state change |

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

### 2.2 The gap this record existed to name — CLOSED at GL-3f

> **Status: closed 2026-08-30 by GL-3f (§2.3).** Both gaps below were real
> when written and are retained as the record of why the custody work was
> needed. Neither is current: A1 added the ignore rule, A2 put an encrypted
> copy off-host.

Two facts, both verified 2026-08-30 *before* GL-3f:

1. **It is not covered by any backup.** `scripts/backup.py --ledger` backs up
   `research/evidence_library/ledger.db` only. Sibling files in that directory
   are not in scope, so neither the local nor the off-host (BK3) path touches
   the archive.
2. **It is not gitignored either.** `.gitignore:39` covers
   `research/evidence_library/ledger.pre-*.db`, which does **not** match
   `ledger.superseded-local.*.db`. The archive is currently untracked but
   *committable* — a stray `git add -A` would sweep a database into the
   repository against the standing convention.

At that moment the archive existed in exactly one place — one developer
workstation, with no backup and no policy. That was the weakest custody of any
governance artifact on this platform, holding an original that cannot be
reproduced. **That is what GL-3f fixed.**

### 2.3 Treatment

**Not chosen: commit it to git.** The repository has a firm convention that no
`.db` lives in version control (`.gitignore` §"Governance ledger — append-only
runtime data, backed up separately"), and BK3 exists specifically to give
irreplaceable ledger bytes encrypted, versioned, Object-Locked off-host custody
that has been drill-proven (BK2, 2026-08-12). Committing a database to solve a
backup problem would trade a proven mechanism for a convention violation.

**Chosen treatment — two actions. Both were executed at Gate GL-3f
(2026-08-30) and are recorded here as COMPLETE. The paragraph this replaced said
they had not been performed; that is no longer true, and the earlier wording is
superseded rather than silently dropped.**

### A1 — `.gitignore` protection: DONE (commit `50afb63`)

```gitignore
research/evidence_library/ledger.superseded-local.*.db*
```

Added alongside the existing `ledger.pre-*.db` line. Deliberately narrow —
`git check-ignore` confirms it matches the archive and its `.sha256` manifest and
**nothing else**: `registry.yaml`, `thresholds.yaml`, `report.py` and the
existing `ledger.pre-*.audit.txt` are all still tracked.

This also fixed a live repository defect, not just an untidiness: because
`.gitignore`'s `ledger.pre-*.db` does not match `ledger.superseded-local.*.db`,
the archive was untracked-but-committable, and
`tests/test_sealed_regions.py::TestResultInvariance::
test_rf2_touches_no_file_outside_the_builder` — which scans
`git status --untracked-files=all` — failed on any machine holding it. That
guard now passes; 509 tests green across the sealed-region and forward-evidence
suites, CI green on `50afb63`.

**Ignoring the archive does not delete it.** It only keeps it out of version
control. What actually protects it is A2.

### A2 — encrypted off-host custody: DONE (2026-08-30)

| | |
|---|---|
| S3 key | `s3://btcbot-offhost-backup-a71f9c2e/archive/ledger.superseded-local.20260828T063031Z.db.age` |
| Manifest | `…/archive/ledger.superseded-local.20260828T063031Z.db.age-manifest.json` |
| Region | `ap-southeast-1` |
| **Plaintext SHA-256** | **`682ff9af087f3ac14c03bb63ac83b6a354588b85a87aacab031074fc59ca7c27`** (180,224 bytes) |
| **Ciphertext SHA-256** | **`f9b4cf7bd2a41954f371a0928dd3764a950581034656a1db1043aa47dc3c38a5`** (180,456 bytes) |
| `age` recipient | `age10npeurzd3643wqjkwl0c4rem7w9zsrmwrvp5cwanyzg79ydpsyyqtfthsd` — the existing BK3 recipient, verified byte-identical to production `/opt/btcbot/.env` and to `docs/ops/offhost-backup.md` |
| Object Lock | **COMPLIANCE, retain until `2026-11-28T12:16:01Z`** |
| VersionId | `itHaV77EYwf_1b59V.V_d4WgY6.jOr49` |

**What was proven, and what was not.** The object exists remotely; it was
downloaded again with the read principal and its SHA-256 recomputed to
`f9b4cf7b…`, matching the manifest exactly; `offhost_backup.py verify` returned
`ciphertext OK` with the expected recipient. All of that was done **without the
private identity and without producing any plaintext** — the recorded
`plaintext_sha256` was read from the manifest, never recomputed.

**Decryptability was NOT tested.** No decrypt drill was run against this
artifact. Ciphertext integrity and recipient are proven; the ability to actually
recover the plaintext from it is an untested assumption, exactly as BK2 says of
any backup that has never been restored. A decrypt drill for this artifact
remains outstanding.

**The local archive is unchanged and still present.** Encryption and upload read
it only: `research/evidence_library/ledger.superseded-local.20260828T063031Z.db`
still hashes to `682ff9af…`, still passes `verify_chain ok=True, n_receipts=27`,
and still holds the original `dataset.seal` receipt. Nothing was moved or
deleted. Custody is now local **and** off-host, not off-host instead of local.

### The distinction this record exists to keep

Two different artifacts, and they must never be conflated:

| | Canonical production ledger | Superseded local archive |
|---|---|---|
| Path | `/opt/btcbot/research/evidence_library/ledger.db` | `research/evidence_library/ledger.superseded-local.20260828T063031Z.db` |
| SHA-256 | `dd568de2f50a23a4c7cb314cc506c7a10f5b3e374670054da1a462875c51b2db` | `682ff9af087f3ac14c03bb63ac83b6a354588b85a87aacab031074fc59ca7c27` |
| Status | **live, authoritative, still being appended to** | **retired, immutable, never appended to again** |
| `dataset.seal` | a **re-attestation** (seq 27, `7dae862f…`) | the **original** (seq 27, `871a4e54…`) |
| Backup | daily BK3 leg under `prod/`, rotated | one-off under `archive/`, never rotated |

The archive is **not** a backup of the canonical ledger. It is a different
chain, retired by GL-2, kept because it holds an original that the canonical
ledger can only point at.

### Residual: the 90-day Object Lock window

Object Lock COMPLIANCE runs to **2026-11-28**. After that date the object becomes
deletable again. For an artifact whose retention class is *permanent* (§2.4),
a 90-day lock is a floor, not a guarantee, and this gap is recorded rather than
left to be discovered when it expires.

**Future governance consideration, not decided here:** before 2026-11-28, either
apply a legal hold, re-assert a longer retention period, or adopt a standing
obligation to re-verify and re-lock the object. Whichever is chosen should be
written into §2.4's retention table so "permanent" has a mechanism behind it
rather than only a label.

### 2.4 Retention semantics, stated once

| Artifact class | Retention |
|---|---|
| Live canonical ledger | daily snapshot, rotated, off-host required (ADR-0001 §12) |
| `ledger.pre-*.db` migration backups | operational; may be pruned once the migration is proven and recorded |
| **`ledger.superseded-local.*.db`** | **permanent, never pruned, never rotated.** Mechanism as of GL-3f: gitignored locally, encrypted copy under `archive/` in S3, outside `_apply_retention()`'s `ledger_*.db` glob and outside any S3 expiry rule. Object Lock currently guarantees this only to 2026-11-28 — see §2.3 residual |

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

- ~~A1 / A2 — the archive's `.gitignore` entry and its off-host custody.~~
  **CLOSED at GL-3f (2026-08-30);** evidence in §2.3.
- **Object Lock expiry 2026-11-28** — the archive's S3 COMPLIANCE lock
  lapses then, after which a "permanent" artifact becomes deletable. Decide a
  legal hold, a longer retention, or a standing re-lock obligation before that
  date (§2.3 residual).
- **No decrypt drill for the archive ciphertext.** Integrity and recipient are
  proven; recoverability is not. BK2's own rule applies: a backup that has
  never been restored is an untested assumption.
- **The three blockers on a first Validated family**, unchanged by any of GL-3:
  no `live-forward` (L2) run exists in the ledger and no bridge writes one;
  `pairwise_r` is empty on the canonical ledger; and observation floors are
  years from being met on current counts. See the daily-lane readiness audit.
