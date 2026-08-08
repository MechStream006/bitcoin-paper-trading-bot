# Phase 4 — Governance Completion Package

| Field | Value |
|---|---|
| **Phase** | Phase 4 — Research Dataset Builder |
| **Contents** | §1 Owner Sign-off Package · §2 `DS-DEC-007` · §3 Durability Proposal · §4 Deployment Readiness · §5 Phase 4 Exit Checklist |
| **Inputs** | [Governance Closure Report](phase-4-governance-closure-report.md) · [Engineering Audit](phase-4-engineering-audit.md) · [Closure Review](phase-4-closure-review.md) · 14 WBS records |
| **Date** | 2026-08-08 |
| **Author** | Claude Opus 5 |
| **Approver** | *(pending — Rohith)* |
| **Nature** | **Documentation only.** No code, no deployment, no dataset, no production change |

---

## 0. Corrections to the Governance Closure Report

Three figures in that report were wrong. Corrected here; the report is not
rewritten.

| # | Reported | Actual |
|---|---|---|
| **D-1** | "22 RC items are decision-bearing" | **55** of 146. My pattern only matched items whose text began with bold, missing every plainly-worded one (all of 4.1's and 4.0's) |
| **D-2** | Production is "9 commits behind" | **14 commits** (`59e4136..HEAD`) |
| **D-3** | `TD-4.12-4` — "`feature_store.db` has no dedicated backup **flag**" | Worse: it has **no backup path at all**. `feature_store` appears in `backup.py` exactly once, in a comment. There is no `run_backup()` call for it, and `--all` does not cover it. Severity raised 🟡 → 🔴 |

**D-3 is the most serious.** 17 Active Feature Specifications and 17
`feature.register` receipts exist in exactly one file, on one machine, with no
backup of any kind.

---

# §1 — Owner Sign-off Package

## 1.1 What is being signed

**Fourteen** verification records, **146** checklist items. The task
instruction, the Closure Review and the Engineering Audit all said *twelve* —
WBS 4.0 produced three records (D0a, D0b, 4.0.1), so signing twelve leaves two
unsigned.

| Record | RC items | Decision-bearing | Sign-off line? |
|---|---|---|---|
| `wbs-4.0-d0a-dvol-conformance-pilot` | 8 | 3 | ❌ **none** |
| `wbs-4.0-d0b-full-corpus-conformance` | 7 | 2 | ❌ **none** |
| `wbs-4.0.1-builder-manifest-upgrade-repair` | 8 | 4 | ❌ **none** |
| `wbs-4.1-dataset-registry` | 9 | 2 | ✅ |
| `wbs-4.2-citation-index` | 10 | 3 | ✅ |
| `wbs-4.3-declaration-resolution` | 11 | 4 | ✅ |
| `wbs-4.4-validation-tiers` | 11 | 4 | ✅ |
| `wbs-4.5-dataset-sealing` | 12 | 5 | ✅ |
| `wbs-4.6-rf2-sealed-region-relocation` | 11 | 4 | ✅ |
| `wbs-4.7-rf3-lane-enforcement` | 11 | 4 | ✅ |
| `wbs-4.8-materialiser` | 11 | 4 | ✅ |
| `wbs-4.9-experiment-datasets` | 12 | 5 | ✅ |
| `wbs-4.10-training-dataset-structure` | 11 | 4 | ✅ |
| `wbs-4.11-retirement-supersession-as-of` | 14 | 7 | ✅ |
| **Total** | **146** | **55** | 11 of 14 |

**Blocker:** the three 4.0 records end with a Summary and have no
`**Reviewer:** ___ **Date:** ___` block. There is nowhere to sign them.
Fixing this is a two-line documentation edit per file (`TD-4.12-5`), and it is
**not done here** because this package writes no changes to existing records.

## 1.2 The 91 verifiable items

Ninety-one items ask the reviewer to confirm a **fact**, not accept a
judgement. They recur in the same shape across records:

| Item | Recurs | How to verify |
|---|---|---|
| Milestone matches its WBS row; no forward scope | 14× | Read the record's §1 and Files Changed |
| No file under `docs/architecture/` touched | 13× | `git diff --name-only <sha> -- docs/architecture/` → empty |
| "Files Changed" matches `git status` exactly | 13× | `git show --stat <sha>` |
| At least two tests read in full | 13× | Reviewer's own reading |
| Full-suite counts reproduced in both environments | 13× | Re-run both suites |
| Rollback lever confirmed | 13× | Each record names it (mostly "two new files, additive") |

These can be discharged in bulk. **They are not where the governance weight
sits.**

## 1.3 The 55 decision-bearing items — grouped by what they actually decide

Signing these accepts a specific engineering judgement as governance. Grouping
them by theme makes the real decision count much smaller than 55.

### Group A — "The builder guards only the builder path" (4 items)

`4.6/RC9` (`HZ-4.6-1`), `4.7/RC10` (`HZ-4.7-1`), plus the RF2/RF3 scope items.

RF2 and RF3 protect declarations that go *through* the Dataset Builder. A
script reading the corpus directly is still outside them. **Accepting this
accepts that the builder is a governed doorway, not a wall.**

### Group B — "Recorded, but nothing alerts" (5 items)

`4.8/RC10`, `4.9/RC11`, `4.10` (holdout), `4.11/RC13`, and the divergence item.

Four hazards, one pattern, now confirmed in practice: the first `dataset.seal`
receipt was written with no alert. **Accepting these accepts that governance
events are queryable but unnoticed** — which is why §5 lists governance
observability as a Phase 5 entry gate rather than a debt item.

### Group C — Admissibility is caller-supplied (3 items)

`4.4/RC9`, `4.10/RC10` (`TD-4.10-1`), and `TD-4.4-3` by reference.

The meet is supplied by the caller, so a caller that omits it skips the RF4
gate. `TD-4.4-3` must be fixed before `TD-4.10-1` can be. **This is the single
most consequential open item in the builder**, because it is the one place a
governance gate can be bypassed without any refusal firing.

### Group D — Interpretations of an ADR rule (12 items)

`4.2/RC7` (sealed-only citation), `4.3/RC7,8` (RF5/RF6 rules), `4.4/RC7,8`
(Tier D caps-not-blocks; TB4 correction), `4.5/RC8` (`dataset.seal`
discretionary), `4.6/RC8` (full-coverage exclusion), `4.7/RC7,8`
(`not_evaluable`; one-rule table), `4.8/RC7,8` (float normalisation; L2
divergence), `4.9/RC9` (DI8 visible-not-refused), `4.11/RC9` (DG-5 scope).

Each is a place the ADR was ambiguous and a reading was chosen and documented
rather than guessed silently. **These are the items where a different reading
would produce a different platform.**

### Group E — Architecture accepted under constraint (7 items)

`4.1/RC7,8`, `4.2/RC8` (injected authority), `4.5/RC7,10` (taxonomy extension;
cross-DB atomicity), `4.11/RC7,8` (registry not append-only; triggers over
hooks).

Notably `4.11/RC7`: **the registry is not append-only**, contrary to what
§16 assumes, and L3 was provided by a side ledger rather than by rebuilding
4.1.

### Group F — Resolutions confirmed (4 items)

`4.4/RC9` → `4.5/RC9` (`HZ-4.4-1` resolved), `4.5/RC11` → `4.11/RC10`
(`HZ-4.5-2` resolved). These are paired: one record raises the requirement, a
later record discharges it. **Signing the later item confirms the earlier one
was actually met.**

### Group G — Debt acknowledged, not fixed (14 items)

`TD-4.7-1`, `TD-4.8-1`, `TD-4.9-2`, `TD-4.10-1`, `TD-4.11-1/2/3`, `HZ-4.9-2`,
and the 4.0 provenance/defect items. **Acknowledgement is not approval to
leave them open** — each remains in the register.

### Group H — WBS 4.0 corpus-specific (6 items)

`4.0-D0a/RC3` (re-derived canonical, not as-received), `RC4` (stronger `C5`
proof), `RC5` (pre-existing defects out of scope), `4.0-D0b` (2),
`4.0.1` (4, incl. the corrected blast-radius analysis).

`D0a/RC3` matters most: the corpus was **re-derived locally**, not re-fetched.
Its provenance is "canonical as this platform computes it", not "as the
exchange served it".

## 1.4 Recommended sign-off sequence

1. **Fix the three 4.0 sign-off blocks first** (`TD-4.12-5`) — otherwise those
   records cannot be signed at all.
2. **Discharge the 91 verifiable items in bulk**, per §1.2.
3. **Review the 55 decision-bearing items by group** (A–H above), not
   record-by-record. Eight themes, not fifty-five judgements.
4. **Give Group C its own attention.** `TD-4.4-3` / `TD-4.10-1` is the only
   accepted item that leaves a governance gate bypassable.
5. Sign each record's final RC item last.

**Nothing in this package marks any item as signed.**

---

# §2 — `DS-DEC-007` Decision Package

## 2.1 Subject

Exit criterion **`P4-9`** counts a governance chain the architecture
deliberately declined to create.

> **`P4-9` (current):** *"`report.py --check` clean; `verify_chain()` clean on
> all four ledgers; both full suites at zero new failures."*

## 2.2 Rationale

The roadmap was written 2026-08-06, anticipating that the Dataset Builder would
carry its own receipt ledger — a fourth chain beside evidence, feature and
label.

**`TD-4.5-1` (WBS 4.5) then decided the opposite**, on stated architectural
grounds: *"the ledger is the platform-wide chain and must not fork."*
`dataset.seal` receipts go to `evidence_library/ledger.db`, which is why that
chain moved 26 → 27 at operational closure.

Verified state:

| Chain | Receipts | `verify_chain()` |
|---|---|---|
| `evidence_library/ledger.db` | 27 | ✅ `ok=True` |
| `feature_store.db` | 17 | ✅ `ok=True` |
| `label_store.db` | 8 | ✅ `ok=True` |
| `dataset_builder.db` | — | **no chain, by design** |

Every chain that exists verifies clean. The receipts the fourth would have held
are present and verified in the first. The criterion is stale, not unmet.

**This is not an editorial fix.** `P4-9` is a ratified exit criterion; changing
it changes what "Phase 4 is complete" means. Hence a decision, not an edit.

## 2.3 Consequences of ratifying

- `P4-9` becomes **MET**; Phase 4 closes on 9 of 10 criteria fully met and one
  partial (`P4-7`, L1/L2 unproven on real data).
- The criterion tracks the architecture rather than a superseded assumption,
  and stays correct if a fourth chain is ever added.
- Records the `TD-4.5-1` ruling at roadmap level, where a future reader meets
  it before wondering why no builder ledger exists.
- **Cost:** amends a ratified criterion. Precedent must be narrow — the
  amendment is warranted because the *architecture* changed under it, not
  because the criterion proved inconvenient.

## 2.4 Consequences of rejecting

- `P4-9` is **permanently unmeetable** unless a Dataset Builder receipt ledger
  is created.
- Creating one **forks the platform chain** and directly contradicts
  `TD-4.5-1`, which was accepted on the reasoning that a single chain is the
  point.
- Phase 4 cannot be declared closed against its own criteria, so `E2` in the
  Phase 5 entry checklist never clears.
- The alternative — declaring `P4-9` "met in spirit" without amending it —
  would be the worst outcome: it sets the precedent that criteria may be
  waived informally, which is exactly the discipline this platform exists to
  avoid.

## 2.5 Recommendation

**RATIFY.** The implementation is complete and correct; the wording describes a
platform that was deliberately not built.

## 2.6 Exact wording for ratification

> **`DS-DEC-007` — Amendment of exit criterion `P4-9`. RATIFIED.**
>
> `P4-9` is amended to read:
>
> *"`report.py --check` clean; `verify_chain()` clean on **every governance
> chain the platform maintains** — as of Phase 4 close these are
> `research/evidence_library/ledger.db`, `research/feature_store/feature_store.db`
> and `research/label_store/label_store.db`; both full suites at zero new
> failures."*
>
> **Note appended to the criterion:** *"The Dataset Builder maintains no
> separate chain. Per `TD-4.5-1` (WBS 4.5), `dataset.seal` receipts are written
> to the platform-wide evidence ledger, because the platform chain must not
> fork. Verifying that ledger verifies them."*
>
> **Scope of this amendment:** wording only. No implementation changes, no
> receipt is re-issued, no chain is re-verified differently, and no other exit
> criterion is affected. The original wording remains in the roadmap changelog.

---

# §3 — Phase 4 Durability Proposal

**Design only. Nothing below is implemented.**

## 3.1 The problem, stated plainly

The entire Phase 4 governance layer exists on **one machine**, and one of its
assets has no backup at all.

| Asset | Size | Copies | Backed up | Off-host | On production |
|---|---|---|---|---|---|
| Corpus `research/data/raw` (274 conformed partitions) | **548 MB** | 1 | ❌ **no** | ❌ | present but unconformed-status unknown |
| `feature_store.db` (17 Active specs, 17 receipts) | 96 KB | **1** | ❌ **none at all** (D-3) | ❌ | empty file, 0 tables |
| `label_store.db` (4 Active specs, 8 receipts) | 184 KB | 2 | ✅ `--label-store` | ❌ | empty file, 0 tables |
| `ledger.db` (27 receipts) | 176 KB | 2 | ✅ `--ledger` | ❌ | 26 receipts |
| `dataset_builder.db` (2 specs, 1 sealed) | 92 KB | 2 | ✅ `--dataset-builder` | ❌ | absent |

Program P1 recorded **BK2 (restore drill) never performed** and **BK3
(off-host) NOT ESTABLISHED** when the irreplaceable asset was one ledger. Phase
4 raised that to three chains plus a 548 MB corpus.

**The governance chains are small.** All three total under 500 KB. Replicating
them is cheap; the corpus is the only sizeable item, and it is re-derivable in
principle (WBS 4.0 proved local re-derivation) though at real cost.

## 3.2 BK2 — restore drill

| | |
|---|---|
| **Status** | Never performed |
| **Aggravating factor** | `TD-P1-002` — `restore.py` is **not WAL-aware**. `ledger.db` is WAL-mode in production. The one drill ever run (2026-07-19) happened to hit a clean checkpoint, so the gap was missed by circumstance, not covered |
| **Proposal** | Resolve `TD-P1-002` **before** the drill, otherwise the drill validates a path that differs from the real one |
| **Scope** | Restore each of the four backed-up databases into a scratch location; verify `PRAGMA integrity_check`, row counts, and `verify_chain()` on every restored chain |
| **Success criterion** | A restored `ledger.db` verifies its chain **and** `report.py --check` runs clean against it |
| **Cadence** | Quarterly, per ADR-0001 BK2 |
| **Owner** | Rohith (execution) · engineering (fix `TD-P1-002` first) |

## 3.3 BK3 — off-host replication

| | |
|---|---|
| **Status** | Designed, never activated. `scripts/offhost_backup.py` and `age` encryption exist; the `--encrypt` unit change is gated in `docs/ops/offhost-backup.md` §8 |
| **Blocking** | Target provisioning, key generation and custody — all operator work, none performable from a session |
| **Proposal** | Activate for the **governance chains first** (under 500 KB total, daily, negligible cost), and treat the corpus as a separate tier |
| **Key custody** | The single decision that must not be rushed: an `age` identity lost after encryption begins makes every off-host copy unrecoverable. Custody must be off-machine **and** off-account |

## 3.4 Replication tiers — recommended

Rather than one policy for everything, three tiers by irreplaceability:

| Tier | Assets | Cadence | Rationale |
|---|---|---|---|
| **T1 — Irreplaceable** | `ledger.db`, `feature_store.db`, `label_store.db`, `dataset_builder.db` | **Daily, off-host, encrypted** | ~550 KB total. Content-addressed and hash-chained: a governance receipt cannot be re-derived, only lost. **Cheapest and most valuable thing to fix** |
| **T2 — Expensive to reproduce** | `research/data/raw` (548 MB, 274 conformed partitions) | **Weekly, off-host** | Re-derivable in principle — WBS 4.0 proved it — but conformance carries `ingest_ts` values that were *preserved*, not recomputed. A re-derivation would lose original ingest instants |
| **T3 — Operational** | `btc_trading.db`, `evidence_forward.db` | Daily local (existing), off-host optional | Already covered locally; loss costs collection history, not governance |

## 3.5 Per-asset proposals

**Receipt replication (T1).** All three chains are append-only and verifiable
in isolation. Replication needs no coordination: copy, then run
`verify_chain()` at the destination. **Verification at the destination is the
whole point** — an unverified copy proves only that bytes moved.

**Corpus replication (T2).** 548 MB, 274 immutable partitions each carrying a
`parquet_sha256`. Ideal for incremental sync: only new months change. The
per-partition hash makes destination verification exact, not sampled.

**`feature_store` replication (T1) — the urgent one.** It has **no backup path
at all**. Proposal: add `--feature-store` to `backup.py` mirroring
`--label-store` exactly (same OPTIONAL classification, same manifest, same
retention), then include it in T1. This is a small, well-precedented change
and is the single highest-value durability fix available.

**`label_store` / `dataset_builder` replication (T1).** Both already have
backup flags and manifests. They need only the off-host leg.

## 3.6 Integrity verification

A backup that is never verified is a belief, not a control. Three tiers,
mirroring the existing off-host design:

| Tier | Check | Cadence | Exists? |
|---|---|---|---|
| 1 | SHA-256 manifest written at backup time | every run | ✅ |
| 2 | Remote-side `verify` of the transferred artifact | weekly | ❌ designed, not activated |
| 3 | Full decrypt-and-restore drill (BK2) | quarterly | ❌ never run |
| **4 (new)** | **`verify_chain()` on every restored chain** | with BK2 | ❌ — proposed here |

Tier 4 is the Phase 4 addition. `integrity_check` proves SQLite consistency;
it says nothing about whether the hash chain still links. For a governance
store those are different questions, and only the second one matters.

## 3.7 Operational ownership

| Responsibility | Owner | Status |
|---|---|---|
| Off-host target provisioning, key generation, custody | **Rohith** | Not started |
| `--encrypt` unit activation | **Rohith** | Gated |
| `--feature-store` backup path | Engineering | **Proposed, not implemented** |
| `TD-P1-002` WAL-aware restore | Engineering | Open |
| Tier-4 chain verification in the drill | Engineering | **Proposed** |
| Quarterly BK2 execution | **Rohith** | Never performed |
| Deciding T2 corpus cadence | **Rohith** | Open |

**Unowned today:** nobody is responsible for noticing that BK3 has still not
been activated. That is how a gap survives four programmes.

---

# §4 — Deployment Readiness Package

**Specification only. Nothing is deployed.**

## 4.1 What the deployment is

Move production from `59e4136` to `3fc81ea` — **14 commits** (correction D-2),
WBS 4.1 → 4.11 plus operational closure and governance documentation.

**What actually reaches production code paths is far smaller than 14 commits
suggests:**

| Path | Change | From |
|---|---|---|
| `research/dataset_builder/` | 13 new modules | 4.1–4.11 |
| `tests/` | 14 new test files | 4.1–4.11 |
| `docs/` | 16 records | all |
| **`scripts/backup.py`** | +28/−4 — `--dataset-builder` flag and call | **d6b020d only** |
| **`scripts/healthcheck.py`** | +10 — `dataset_builder_backup` check | **d6b020d only** |
| **`src/api/config.py`** | +5 — `dataset_builder_backup_dir` | **d6b020d only** |
| **`src/api/services.py`** | +4 — same check in `runtime_health()` | **d6b020d only** |
| `deploy/*.service`, `*.timer` | **no change** | — |

Net production-path delta: **43 insertions, 4 deletions, 4 files, one commit.**

## 4.2 Prerequisites

| # | Prerequisite | Status |
|---|---|---|
| P1 | Production healthy at deploy time (`INFO`, 0 non-OK, 0 failed units) | ✅ verified today |
| P2 | Clean working tree on the host, branch `main` | ✅ `modified=0` |
| P3 | `btcbot` deploy key can reach origin | ✅ established 2026-08-07 |
| P4 | Fresh backup of all databases immediately before | ⬜ run `backup.py --all` |
| P5 | Rollback SHA recorded | ✅ `59e4136` |
| P6 | **`DS-DEC-007` decided** | ⬜ not required to deploy, but required to call Phase 4 closed |
| P7 | Operator aware the healthcheck gains a new line | ⬜ see §4.5 |

**Note on P3:** production's `origin/main` ref is stale at `59e4136` because
`git fetch` as `ubuntu` fails — the deploy key belongs to `btcbot`. Fetch must
be run as `btcbot`, or the deployment will appear to be a no-op.

## 4.3 Migration plan

**There is no data migration.** No schema changes to any existing database, no
`ALTER TABLE`, no backfill, no `migrate_*.py` run.

The Dataset Builder's schema is created by idempotent `init_db()` calls, and
**nothing on production will call them** — the builder has no entry point, no
CLI in any unit, and no timer. Deployed code that never executes cannot
migrate anything.

Sequence (as `btcbot`, on `/opt/btcbot`):

1. `backup.py --all` — capture the pre-deploy state.
2. `git fetch origin && git rev-parse origin/main` — confirm `3fc81ea`.
3. `git reset --hard origin/main`.
4. `systemctl restart btcbot.service btcbot-api.service`.
5. Run §4.4 verification.

## 4.4 Verification after deployment

| # | Check | Expected |
|---|---|---|
| V1 | `git rev-parse HEAD` | `3fc81ea…` |
| V2 | `systemctl is-active btcbot btcbot-api` | `active` / `active` |
| V3 | `systemctl --failed` | 0 btcbot units |
| V4 | Collection resumes | `quotes=N/N` within 3 min |
| V5 | `healthcheck.py` | `overall INFO`, 0 non-OK |
| V6 | **`dataset_builder_backup`** | present and **INFO/absent** — not OK, not WARNING |
| V7 | `integrity_check` × 3 | `ok` |
| V8 | `report.py --check` | clean, 35 families |
| V9 | `verify_chain(ledger.db)` | **`ok=True, n=26`** — unchanged |
| V10 | `backup.py --all --dry-run` | exit 0 |
| V11 | Telegram alert | delivered |

**V9 is the one to watch.** The deployment must **not** change production's
receipt count. If it becomes 27, something executed that should not have.

## 4.5 Operational risks

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R1 | Collector downtime during restart | 🟢 Low | Seconds; WS reconnects; the same restart has been performed before |
| R2 | **New healthcheck line confuses the signal** | 🟡 Medium | `dataset_builder_backup` will report the store absent. Verify it lands as **INFO**, not WARNING — a new perpetual WARNING would erode trust in the backup signal, which is the exact failure `PROD-1` fixed |
| R3 | `--all` behaviour change | 🟢 Low | `dataset_builder` is OPTIONAL; a missing file is SKIPPED, exit code unaffected (`PROD-1` ruling) |
| R4 | API config gains a required field | 🟡 Medium | `ledger_backup_dir` at P1 Increment 1 forced test fixture edits. Confirm `btcbot-api` starts — V2 covers it |
| R5 | Stale `origin/main` makes the deploy a silent no-op | 🟡 Medium | Fetch as `btcbot`; V1 catches it |
| R6 | Disk pressure from the pre-deploy backup | 🟡 Medium | 6.8 GB free, 76% used; `--all` adds ~21 MB. Safe, but capacity remains an open owner decision |
| R7 | Deployment creates a false impression that the Dataset Builder is "live" | 🟠 **High** | **It is not.** No store, no timer, no entry point. Production gains the *capability*, not the component. §5 states this separately for exactly this reason |

## 4.6 Rollback

| | |
|---|---|
| **Trigger** | Any of V2, V3, V4, V5, V7, V8, V9 failing |
| **Command** | `git reset --hard 59e4136` then restart both services |
| **Data rollback** | **None required.** No database is created, migrated or written by this deployment |
| **Time** | Under 2 minutes |
| **Residual risk after rollback** | Zero. The pre-deploy backup (P4) is belt-and-braces, not the mechanism |

**This is the safest deployment in the project's history** — no migration, no
schema change, no data written, no unit file touched, and a rollback with
nothing to undo.

---

# §5 — Phase 4 Exit Checklist

Six distinct readiness questions. They are separated because **Phase 4 passes
some and fails others**, and a single verdict would hide that.

## 5.1 Engineering completion — ✅ **COMPLETE**

| # | Item | Status |
|---|---|---|
| EC1 | All 14 WBS records delivered with evidence | ✅ |
| EC2 | 842 dedicated tests; both suites green (3233/19, 2784/62) | ✅ |
| EC3 | All six refusal points implemented and tested | ✅ |
| EC4 | All eight Design Guarantees enforced | ✅ |
| EC5 | TV3 sabotage performed at every milestone | ✅ |
| EC6 | Result invariance preserved (10 artifacts, 0 changed) | ✅ |
| EC7 | Import-boundary proven (`P4-8`) | ✅ |
| EC8 | ADR §10.3 propagation | ❌ **Unowned** |
| EC9 | ADR §16 L1/L2 on real data | ❌ `TD-4.12-1` |

EC8 and EC9 are **unowned scope**, not incomplete milestones. Every WBS row
delivered what it specified.

## 5.2 Governance completion — ❌ **INCOMPLETE**

| # | Item | Status |
|---|---|---|
| GC1 | 146 RC items across 14 records signed | ❌ **0 signed** |
| GC2 | 55 decision-bearing items accepted | ❌ |
| GC3 | Three 4.0 records given a sign-off block (`TD-4.12-5`) | ❌ |
| GC4 | `DS-DEC-007` decided (§2) | ❌ |
| GC5 | `T-2`, `T-4`, `T-5`, `T-6` ratified or closed | ❌ |
| GC6 | `DS-DEC-006` decided | ❌ Owed |
| GC7 | All ratified decisions applied consistently | ✅ |
| GC8 | Every refusal and rejection permanently recorded | ✅ |

## 5.3 Operational readiness — ⚠️ **CANONICAL HOST ONLY**

| # | Item | Status |
|---|---|---|
| OR1 | Governed store exists, `integrity_check: ok` | ✅ canonical |
| OR2 | One real dataset sealed; receipt on the chain | ✅ |
| OR3 | Store backed up with SHA-256 manifest | ✅ |
| OR4 | Healthcheck covers the store | ✅ canonical |
| OR5 | Append-only guards verified on live data | ✅ |
| OR6 | All three chains verify clean | ✅ |
| OR7 | Governance events surfaced to an operator | ❌ **nothing alerts** |
| OR8 | Store exists on production | ❌ absent — and **not required** |

## 5.4 Disaster-recovery readiness — 🔴 **NOT READY**

| # | Item | Status |
|---|---|---|
| DR1 | `feature_store.db` backed up **at all** | 🔴 **NO** (correction D-3) |
| DR2 | BK2 restore drill performed | ❌ never |
| DR3 | `TD-P1-002` WAL-aware restore | ❌ open |
| DR4 | BK3 off-host replication active | ❌ NOT ESTABLISHED |
| DR5 | Corpus (548 MB) replicated | ❌ no |
| DR6 | Chain verification at restore destination | ❌ not designed until §3.6 |
| DR7 | Governance layer exists in more than one place | 🔴 **NO** |

**This is Phase 4's weakest dimension by a wide margin.** DR1 and DR7 mean a
single disk failure destroys 17 Feature Specifications, 25 governance receipts,
274 conformed corpus partitions and the platform's first sealed dataset.

## 5.5 Production deployment readiness — ✅ **READY, NOT AUTHORISED**

| # | Item | Status |
|---|---|---|
| PD1 | Delta understood: 14 commits, 43 net production-path lines | ✅ |
| PD2 | No migration required | ✅ |
| PD3 | Rollback specified, nothing to undo | ✅ |
| PD4 | Verification specified (V1–V11) | ✅ |
| PD5 | Risks identified (R1–R7) | ✅ |
| PD6 | Pre-deploy backup step defined | ✅ |
| PD7 | **Owner authorisation** | ❌ **not given** |

The deployment is fully specified and low-risk. It has not been authorised and
was not performed.

## 5.6 Phase 5 entry readiness — ❌ **NOT READY**

| # | Gate | Blocking? | Status |
|---|---|---|---|
| E1 | 14 records signed (146 items) | ✅ **Blocking** | ❌ |
| E2 | `DS-DEC-007` decided | ✅ **Blocking** | ❌ |
| E3 | Sign-off names **fourteen** records | ✅ **Blocking** | ❌ |
| E4 | 4.0 sign-off blocks added | ✅ **Blocking** (E1 impossible without it) | ❌ |
| E5 | `feature_store` backup exists (DR1) | ✅ **Blocking** | ❌ |
| E6 | Durability decision taken (§3) | ✅ **Blocking** | ❌ |
| E7 | §10.3 and governance alerting given an owner | ✅ **Blocking** | ❌ |
| E8 | Phase 4 deployed, or the lag explicitly accepted | ⬜ Non-blocking | ❌ |
| E9 | `TD-4.12-1` materialisation decided | ⬜ Non-blocking | ❌ |
| E10 | `T-2`/`T-4`/`T-5`/`T-6`, `DS-DEC-006` closed | ⬜ Non-blocking | ❌ |
| E11 | BK2 drill performed | ⬜ Non-blocking, strongly advised | ❌ |
| E12 | Full-suite baseline re-established at entry | ⬜ Non-blocking | ❌ |

**Seven blocking gates. `E5` is new and non-negotiable:** starting Phase 5 on
a Feature Store with no backup would compound an already-single-homed
governance layer.

## 5.7 Verdict

| Dimension | Verdict |
|---|---|
| Engineering completion | ✅ **COMPLETE** |
| Governance completion | ❌ **INCOMPLETE** — 0 of 146 items signed |
| Operational readiness | ⚠️ **CANONICAL HOST ONLY** |
| Disaster-recovery readiness | 🔴 **NOT READY** |
| Production deployment readiness | ✅ **READY, NOT AUTHORISED** |
| Phase 5 entry readiness | ❌ **NOT READY** — 7 blocking gates |

**Phase 4 built what it promised and proved it on real data. What stands
between it and closure is signature and durability — not engineering.**

---

**No production change. No code written. No deployment. No dataset created.
Phase 5 not started.**
