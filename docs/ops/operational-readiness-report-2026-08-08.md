# Phase 4 — Operational Readiness Report

| Field | Value |
|---|---|
| **Scope** | BK3, BK2, AF-4, operational documentation, final audit |
| **Engineering** | Frozen at `82ce7c0`. No engineering implementation changed |
| **Ops commit** | `ed9b6bd` — `scripts/healthcheck.py` (AF-4) + tests + `docs/ops/` |
| **Date** | 2026-08-08 |
| **Verdict** | 🔴 **B — NOT OPERATIONALLY READY.** One blocker: BK3 (§1) |

---

## 1. BK3 — NOT ESTABLISHED, blocked on three owner actions

The mechanism is **complete and tested**; only activation is missing. Verified
from source: encryption, ciphertext manifests, verify, decrypt, fail-closed
transfer, status sidecars, freshness monitoring with a CRITICAL tier, retention
of `.age` artifacts — all built, 29 tests passing.

| # | Blocker | Evidence |
|---|---|---|
| **B1** | `age` not installed | not on `PATH` locally **or** on production |
| **B2** | No keypair; **custody undecided** | `BACKUP_AGE_RECIPIENT` absent from both `.env` files |
| **B3** | No off-host target | `BACKUP_OFFHOST_CMD` absent from both `.env` files |

**B2 is the one that must not be rushed.** The identity is the only thing that
can ever decrypt an off-host copy. Lose it after encryption begins and every
off-host artifact is unrecoverable — silently, because backups keep succeeding.
Custody must be off-machine and off-account relative to the bot host.

B3 requires provisioning a destination with cost and credential implications.
**Both are owner actions; neither can be performed from an engineering session.**

Sequence, commands and the mandatory decrypt drill:
[BK3 Deployment Guide](bk3-deployment-guide.md).

**Coverage gap even after activation:** the 548 MB corpus (274 partitions) has
**no backup path at all**. `backup.py` handles SQLite via `VACUUM INTO`; a
Parquet tree needs a file-sync mechanism that does not exist. Recorded as
tier T2 in the guide.

## 2. BK2 — SATISFIED for on-host recovery

First full drill executed. **6/6 stores** backed up, SHA-verified, restored,
integrity-checked, with the three governance chains verified **on the restored
artifact** (27 / 17 / 8, all `ok=True`). Entire cycle under one second of
compute; largest store (40,320 rows) restored in 0.22s.

`TD-P1-002` tested by constructing the scenario the 2026-07-19 drill missed —
uncheckpointed 4,152-byte WAL plus an open transaction across the swap.
**Clean.** But the protection is SQLite's WAL salt validation, not `restore.py`,
so the debt is **downgraded, not closed**.

**BK2 is not satisfied for off-host recovery**, because no off-host copy exists.

Full detail: [Disaster Recovery Report](disaster-recovery-report-2026-08-08.md).

## 3. AF-4 — proven TRUE, fixed, tested

`check_backup_freshness()` judged backups on mtime alone. A zero-table database
produced `backup OK`. Now returns **INFO … 0 rows**, read from the manifest
`backup.py` already writes. A missing or corrupt manifest yields `None`, never
0 — absence of evidence is not evidence of absence. **8 tests.**

Evidence: [Operations Verification Report](operations-verification-report-2026-08-08.md).

## 4. Correction — two 0-byte files I created on production

Earlier reports of mine stated that production's `feature_store.db` and
`label_store.db` were "empty placeholder files", and used that to describe
AF-4's production impact.

**That was wrong, and the cause was my own audit.** Both files are **0 bytes,
timestamped `2026-08-08 12:26`** — the moment an earlier read-only-intended probe
ran `sqlite3 <path> "SELECT ..."` against them. `sqlite3` creates a database
file on open. Before that, neither existed, and the healthcheck correctly
reported *"backup directory does not exist (optional database, not configured on
this host)"* — INFO, the right signal.

**Impact if left:** the next `--all` run (Sun 02:01 UTC) will back both up,
creating backup directories for stores containing nothing. Under the currently
deployed code (18 commits behind, no AF-4 fix) those would report **OK** — a
false-green. This is AF-4's exact failure mode, and I created the conditions for
it.

**Proposed remediation — not performed. Owner approval requested.**

```bash
sudo -u btcbot rm -f /opt/btcbot/research/feature_store/feature_store.db \
                     /opt/btcbot/research/label_store/label_store.db
```

- **Justified:** restores production to its state before my probe. Both files
  are gitignored, 0 bytes, contain nothing, and no deployed code reads them.
- **Reversible:** trivially — an empty file is recreated by any `sqlite3` open,
  and the real stores would be created by their own research processes.
- **Independently verifiable:** afterwards, `healthcheck.py` should report
  `label_store_backup` / `feature_store_backup` as INFO *"not configured on this
  host"*, and no `data/{feature,label}-store-backups/` directory should appear
  after the next backup run.

I did not execute it: it is a production change, and this task is an audit.

## 5. Independent audit — verified now, not assumed

| Area | Evidence |
|---|---|
| **Production** | `HEAD=59e4136`, 0 modified, `overall INFO`, 0 non-OK, `btcbot`+`btcbot-api` active, 0 failed units, 26 receipts, disk 6.8 GB free (76%) |
| **Backups** | Backup service ran 02:06 UTC, exit **0**; next 02:01 UTC. `--all` covers 7 stores locally |
| **Restores** | 6/6 verified in the drill, incl. chain verification on the restored file |
| **Monitoring** | 6 backup checks + 2 off-host checks registered; CLI/API parity preserved |
| **Replication** | **None.** All sidecars `not_configured`, `last_success: None` |
| **Healthchecks** | AF-4 fix verified across 6 cases incl. two negatives |
| **Governance chains** | 27 / 17 / 8, all `ok=True`; four stores `integrity_check ok` |
| **Regression** | **3298 passed / 19 skipped** |
| **Deployment readiness** | 19 commits, 4 production-path files, no migration, rollback with nothing to undo |

## 6. Remaining operational blockers

| # | Blocker | Severity | Evidence |
|---|---|---|---|
| **OB-1** | **BK3 not established** — no off-host copy of any governance asset exists | 🔴 **Blocking** | `age` absent both hosts; both env vars unset; all sidecars `not_configured` |
| **OB-2** | Governance layer single-homed — 3 chains + 548 MB corpus on one machine | 🔴 **Blocking** | consequence of OB-1 |
| **OB-3** | Corpus has no backup path at any tier | 🟠 | `backup.py` has no file-tree mechanism |
| **OB-4** | Two 0-byte files on production, created by this audit | 🟡 | §4 — remediation proposed, approval pending |
| **OB-5** | Phase 4 undeployed — AF-4 fix not live | 🟡 | production 19 commits behind |
| **OB-6** | `TD-P1-002` downgraded, not closed | 🟢 | `restore.py` still performs no checkpoint |

**OB-1 and OB-2 are the same fact stated twice, and they are the reason this
report concludes NOT READY.** Everything else is either fixed, documented, or
non-blocking.

## 7. Shortest path to OPERATIONALLY READY

1. **B1** — `apt-get install age` on production *(minutes)*
2. **B2** — generate the keypair, place the identity in off-host custody *(owner decision)*
3. **B3** — provision a target, set `BACKUP_OFFHOST_CMD` *(owner decision)*
4. Activate T1 for the four governance stores — **under 550 KB/day**
5. **Run the decrypt drill.** An encrypted backup nobody has decrypted is a belief
6. Deploy Phase 4 so the AF-4 fix is live
7. Approve §4 remediation

Steps 1–5 close OB-1 and OB-2. Nothing in that list requires engineering.
