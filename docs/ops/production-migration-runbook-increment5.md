# Production Migration Runbook — Program P1 Increment 5

**Single-session, copy-paste runbook for the governance ledger schema
backfill.** Derived from the Increment 4 local harness
(`tests/test_migration_harness.py`,
`docs/ops/migration-harness-dry-run-2026-07-26.md`) and the Final Program
Readiness Review (2026-07-26). Style mirrors
`docs/ops/deployment-execution-guide.md` deliberately — same host, same
operator, same discipline.

**This is a one-time production operation against the canonical ledger.**
Do not improvise ordering. Do not skip a STOP condition. If anything here
conflicts with what you observe on the host, **stop and re-plan** — do not
guess.

**Status of this document: EXECUTED 2026-07-26. Retained as the historical
record of the procedure that was followed, and as the reference procedure
for any future ledger migration. The execution transcript was not captured
into this repository — see §7 Sign-off and
`docs/governance/p1_assurance_declaration.md` claim 4.**

---

## 0. What this migration does

Backfills `runs.run_kind` (a lane classifier: discovery / confirmatory /
forward / production) and bootstraps the receipt-chain rows
(`governance_receipts`, via bootstrap admission + transition receipts) that
already exist on this repository's local ledger but have never been applied
to the production ledger.

**What is touched:** the `run_kind` column (only where currently `NULL`),
plus new append-only rows in `governance_receipts`.
**What is never touched:** any evidence value — `sharpe`, `oos_sharpe`,
`dsr_prob`, `bh_p`, `metrics_json`, `provenance`, `run_ts`, `verdict`, or any
other existing column. This is a **classification** of rows that already
exist, not a revision of them (`migrate_run_kind.py`'s own docstring).

**Three scripts, in this exact order (the "D-0 sequence"):**

```
1. migrate_run_kind.py
2. migrate_bootstrap_evidence_admission.py
3. migrate_bootstrap_transition_receipts.py
```

Each is independently idempotent, dry-run by default, `--commit`-gated,
and self-backing-up before any write. This order is load-bearing — script 3
reads state that script 2 writes, which reads state that script 1 writes.

---

## 1. GO / NO-GO gate — all must be TRUE before Step 0

| # | Precondition | Check | Source |
|---|---|---|---|
| G1 | Increments 1–7 committed to `main` (local + pushed) | `git status --porcelain` empty; `git log` shows the commit | Readiness Review C-1 |
| G2 | Local full suite passes at the committed state | 1529 passed / 19 skipped / 1 pre-existing unrelated failure only | §2 of this doc |
| G3 | Local `report.py --check` clean | exit 0, 35 families | Readiness Review |
| G4 | Local `receipt_ledger.verify_chain()` valid | `ok=True, n_receipts=26` | Readiness Review |
| G5 | Production schema reconnaissance performed (read-only) | Step 0 below — performed during the 2026-07-26 execution; output not retained in this repository | Readiness Review C-2 — was **blocking**, satisfied at execution |
| G6 | Reconnaissance result classified as Scenario A, B, or C | §3 below | — |
| G7 | If Scenario C: **stop, do not proceed** — re-plan against the real shape | §3 | ADR-P1-4 |
| G8 | A fresh on-host ledger backup exists, independent of this migration's self-backups | Step 0b | Readiness Review C-3 |
| G9 | Rohith's explicit final approval to execute (this document, signed off) | — | This runbook's own gate |
| G10 | Deploy window clear of settlement/evidence/certifier/backup windows | Timing box below | Mirrors deployment-execution-guide.md |
| G11 | **Both processes that touch `ledger.db` in production — `btcbot-api.service` and `btcbot-backup.timer`/`.service` — are stopped and confirmed inactive** | Step 0a | Corrects an incomplete claim found during audit follow-up (below) — **blocking** |
| G12 | `ledger.db` has no live `-wal` write-ahead frames before any backup/migration/restore touches it (`PRAGMA wal_checkpoint(TRUNCATE)` run and confirmed) | Step 0a / Step 0b / §5 | Same audit follow-up — **blocking** |

**If any is FALSE — stop. Do not begin.**

### Why G11/G12 exist — corrected finding (post-audit follow-up)

The Final Program Readiness Review's WAL-rollback concern was investigated
by checking, from source (not assumption), every systemd unit's `ExecStart`
and the Python it runs — not just grepping unit files for the literal
string `ledger`. That grep-based method is what produced the earlier,
**incomplete** claim that "no scheduled systemd unit writes to `ledger.db`."
It missed one:

**`btcbot-backup.timer` → `btcbot-backup.service` runs `scripts/backup.py
--all` daily at 02:00 UTC, which — for the `--ledger` leg — opens
`research/evidence_library/ledger.db` directly and executes
`conn.execute("PRAGMA journal_mode=WAL")` on it** (`scripts/backup.py:235`,
verified by reading the source), before a read-consistent `VACUUM INTO`
snapshot. This is the actual, confirmed reason `ledger.db` is in WAL mode in
production at all (independently corroborated by `btcbot-api.service`'s own
comment, which already calls it a "WAL database"). The backup process never
writes ledger *data*, but it is a real, scheduled process that opens the
file and can leave `-wal`/`-shm` sidecars behind.

Combined with `btcbot-api.service` (continuous, read-only, but its
individual request-scoped connections could momentarily coincide with a
migration write), these are the **only two** processes — of every systemd
unit on the host — confirmed by source inspection to ever open `ledger.db`.
(`btcbot.service`, `btcbot-certify.service`, `btcbot-evidence.service`,
`btcbot-fwd-hg.service`, `btcbot-healthcheck.service`, and
`btcbot-mapsnap.service` were each checked directly against their
`ExecStart` target's source and confirmed to touch only `btc_trading.db` /
`research/data/*.db` — never `research/evidence_library`.)

The previous version of this runbook only stopped `btcbot-api.service`
inside the **rollback** section (§5a/§5b), and relied on **schedule
avoidance** (the "AVOID 02:00 UTC" line below) rather than an enforced stop
for the backup timer — and only during the *forward* migration path, never
established as a precondition covering the *entire* window including
rollback. Schedule avoidance reduces the probability of collision; it does
not eliminate it (the timer's own `RandomizedDelaySec=300` alone makes the
"±10m" estimate approximate, not exact). **G11 replaces avoidance with an
explicit stop-and-verify gate, and G12 adds an explicit WAL-clean check
rather than relying on either unit's connections happening to close
cleanly.**

### Timing constraints (defense in depth — G11 is now the actual guarantee)

```
NEVER   11:58–12:06 UTC   settlement dead zone
AVOID   00:05–01:00 UTC   evidence -> fwd-hg ordering precondition
AVOID   01:30 UTC ±10m    certifier
AVOID   12:30 UTC         mapsnap
PREFERRED: 03:00–08:00 UTC
```

This migration does not touch `btc_trading.db` or the runtime bot process,
so the window is looser than a full deployment. The backup-timer
constraint is **no longer a scheduling avoidance note** — `btcbot-api` and
`btcbot-backup.timer` are stopped outright at Step 0a for the full duration
of Steps 0–4, so their schedule no longer matters for this migration. The
remaining AVOID lines are for the *other* jobs, which are confirmed to
never touch `ledger.db` but still contend for host I/O and operator
attention.

---

## 2. Session setup (paste once per SSH session)

```bash
KEY=D:/My_paper_trading_bot/bitcoin-key.pem
ssh -i "$KEY" -o StrictHostKeyChecking=no ubuntu@3.110.103.217

# ON THE SERVER — paste this block once:
BOT=/opt/btcbot
EVLIB=$BOT/research/evidence_library
PY=/opt/miniconda3/envs/btc_bot/bin/python      # [VERIFY ON HOST] conda path
VENV=$BOT/venv/bin/python                        # runtime venv (backup.py lives here)
STAMP=$(date -u +%Y%m%d_%H%M%S)
echo "BOT=$BOT  EVLIB=$EVLIB  STAMP=$STAMP"; $PY --version; $VENV --version
```

---

## STEP 0 — Pre-flight (read-only where marked; no risk, no deploy window)

### 0a. Record the rollback target, then stop everything that can touch `ledger.db` (G11/G12)

```bash
sudo -u btcbot git -C $BOT rev-parse HEAD | tee /tmp/rollback_commit.txt
```

**Stop both confirmed touchers of `ledger.db` before anything else opens
it** — this is what makes the migration and its rollback path a genuine
single-process operation, not a claim resting on schedule avoidance:

```bash
sudo systemctl stop btcbot-backup.timer
sudo systemctl stop btcbot-backup.service   # defensive — in case a oneshot run is mid-flight
sudo systemctl stop btcbot-api

# Verify — all three must report inactive
for u in btcbot-backup.timer btcbot-backup.service btcbot-api; do
  printf "%-24s " "$u"; systemctl is-active "$u"
done
```

**Expected:** all three print `inactive` (or `dead`/`inactive` for the
oneshot service, which is normal if it wasn't running).

> **STOP if:** any unit reports `active`/`activating` after the stop
> command. Do not proceed with anything else touching `ledger.db` while a
> known writer/reader might still hold it open.

**Checkpoint and confirm the ledger has no live WAL residue** before the
first read of the session:

```bash
cd $EVLIB
sudo -u btcbot sqlite3 ledger.db "PRAGMA wal_checkpoint(TRUNCATE);"
ls -la ledger.db ledger.db-wal ledger.db-shm 2>/dev/null
```

**Expected:** the checkpoint returns a row like `0|N|N` (the leading `0`
means no other connection blocked it — if it isn't `0`, something above
did not actually stop). `ledger.db-wal`, if listed at all, should be 0
bytes; more commonly it will be absent entirely after a clean `TRUNCATE`
checkpoint.

> **STOP if:** the checkpoint's busy flag is non-zero, or `ledger.db-wal`
> exists with nonzero size after this command. That means an open
> connection still exists somewhere — go back and find it before
> proceeding to 0b.

### 0b. Full backup of all four databases — before anything else (G8)

```bash
sudo -u btcbot $VENV $BOT/scripts/backup.py --all; echo "exit=$?"
ls -lh $BOT/data/backups/ $BOT/data/ledger-backups/ \
       $BOT/data/evidence-backups/ $BOT/data/evidence-fwd-backups/ | tail -20
```

**Expected:** `exit=0`; four fresh `*_<STAMP-ish>.db` + `.manifest.json`
pairs, most recent timestamp today.

> **STOP if:** `exit≠0`, or any backup directory missing today's file. Do
> not proceed without a verified, independent backup of the ledger.

**This step itself opens `ledger.db` and sets its journal mode (`backup.py`
runs `PRAGMA journal_mode=WAL` on the source as part of taking the ledger
backup — see the note under G11/G12 above). Re-confirm clean WAL state
immediately after, before Step 0c reads anything:**

```bash
cd $EVLIB
sudo -u btcbot sqlite3 ledger.db "PRAGMA wal_checkpoint(TRUNCATE);"
ls -la ledger.db ledger.db-wal ledger.db-shm 2>/dev/null
```

**Expected:** same as above — busy flag `0`, no nonzero-size `-wal` file.

### 0c. Read-only production reconnaissance (G5 — the decisive check)

This is the step the Final Program Readiness Review flagged as the
program's one unverified assumption: **whether production's `runs` table
actually has the schema shape Increment 4's harness validated.** Nothing
below writes anything.

```bash
cd $EVLIB

# Schema shape
sudo -u btcbot $PY -c "
import sqlite3
con = sqlite3.connect('ledger.db')
tables = [r[0] for r in con.execute(\"SELECT name FROM sqlite_master WHERE type='table' ORDER BY name\")]
views  = [r[0] for r in con.execute(\"SELECT name FROM sqlite_master WHERE type='view' ORDER BY name\")]
cols   = [r[1] for r in con.execute('PRAGMA table_info(runs)')]
print('tables:', tables)
print('views :', views)
print('runs.run_kind present:', 'run_kind' in cols)
print('runs total:', con.execute('SELECT COUNT(*) FROM runs').fetchone()[0])
if 'run_kind' in cols:
    print('runs with NULL run_kind:', con.execute('SELECT COUNT(*) FROM runs WHERE run_kind IS NULL').fetchone()[0])
print('state_transitions:', con.execute('SELECT COUNT(*) FROM state_transitions').fetchone()[0])
try:
    print('governance_receipts:', con.execute('SELECT COUNT(*) FROM governance_receipts').fetchone()[0])
except Exception as e:
    print('governance_receipts: TABLE ABSENT --', e)
con.close()
"

# GV-SCHEMA fingerprint (fail-closed, read-only by construction — ADR-P1-1)
sudo -u btcbot $PY -c "
import governance_verification as gv
findings = gv.check_schema_fingerprint('ledger.db')
if not findings:
    print('GV-SCHEMA: zero findings')
for f in findings:
    print(f'[{f.severity}] {f.message}')
"

# Governance baseline (mirrors deployment-execution-guide.md Step 0d)
sudo -u btcbot $PY report.py --check; echo "check_exit=$?"
sudo -u btcbot $PY -c "import receipt_ledger; print(receipt_ledger.verify_chain('ledger.db'))"
```

**Record every line of output verbatim** — this is the evidence that
resolves G6/§3 below, and becomes part of the permanent record regardless
of which scenario it turns out to be.

### 0d. Final concurrency sanity check (defense in depth — the actual guarantee is 0a)

**Corrected claim:** two systemd units are confirmed (by reading their
`ExecStart` target's source, not by grepping unit files for the string
`ledger`) to open `ledger.db` — `btcbot-api.service` (continuous, read-only
by construction, asserted by `tests/test_api.py`) and `btcbot-backup.timer`
→ `btcbot-backup.service` (daily 02:00 UTC, opens the ledger to set WAL
mode and take a `VACUUM INTO` snapshot — see G11/G12 above). Both were
stopped and verified inactive at Step 0a; this step is a **second,
independent check**, not the primary control — it exists to catch a manual
`systemctl start` by a second operator, or an ad hoc script run by hand,
that 0a's stop couldn't have anticipated.

All other systemd units (`btcbot.service`, `btcbot-certify.service`,
`btcbot-evidence.service`, `btcbot-fwd-hg.service`,
`btcbot-healthcheck.service`, `btcbot-mapsnap.service`) were checked
directly against their `ExecStart` target's source and confirmed to never
touch `research/evidence_library` — they remain running throughout this
migration with no reasoned effect on `ledger.db`.

```bash
# Re-confirm both known touchers are still stopped
for u in btcbot-backup.timer btcbot-backup.service btcbot-api; do
  printf "%-24s " "$u"; systemctl is-active "$u"
done

# Confirm no other operator session is active
who
# Confirm no lingering process holds the ledger open
sudo lsof $EVLIB/ledger.db $EVLIB/ledger.db-wal $EVLIB/ledger.db-shm 2>/dev/null \
  || echo "lsof not available, or none of these files are held open — proceed with the 'who' check only"
```

**Expected:** all three units still `inactive`. One session (yours). No
`python`/`sqlite3` process holds any of `ledger.db`/`-wal`/`-shm`.

> **STOP if:** any unit is no longer `inactive`, another session is
> active, or a stray process holds any of the three files. Resolve before
> proceeding — do not run two writers against an append-only ledger
> concurrently, and do not let a WAL-mode file accumulate residue from an
> untracked connection.

---

## 3. Scenario classification (decision point — do not skip)

Based on Step 0c's output, classify before proceeding to Step 1:

| Scenario | Signature | Meaning | Action |
|---|---|---|---|
| **A** | `run_kind` column present, some rows `NULL`; `governance_receipts` present but fewer than 26 rows (or table exists with partial data); GV-SCHEMA reports no hard errors | Matches the documented drift Increment 4 validated | **Proceed to Step 1.** Increment 4's evidence directly applies. |
| **B** | `run_kind` present, **zero** `NULL` rows; `governance_receipts` present; GV-SCHEMA zero findings; `report.py --check` already exit 0 | Already migrated (or migrated by someone else previously) | **Do not run `--commit`.** Skip to Step 3 (post-migration verification) to confirm, then close Increment 5 as a no-op. Document in the assurance declaration. |
| **C** | `run_kind` **absent** in a way that doesn't match the documented shape, GV-SCHEMA reports an **unexpected** error, or `runs`/`state_transitions`/`gate_results` have a structure the harness never modeled | Production drift differs from what Increment 4 proved | **STOP. Do not run any migration script.** Increment 4's evidence does not apply to this shape. Return to planning — the harness (`tests/test_migration_harness.py::_build_synthetic_pre_migration_ledger`) must be re-run against a *copy of the real production shape* before any script touches production. This is ADR-P1-4, applied literally. |

**Write the classification down, with the Step 0c output that justifies
it, before touching Step 1.** This is the single most important judgment
call in the entire runbook.

---

## STEP 1 — Dry run (writes nothing; safe to run any number of times)

```bash
cd $EVLIB
sudo -u btcbot $PY migrate_run_kind.py
sudo -u btcbot $PY migrate_bootstrap_evidence_admission.py
sudo -u btcbot $PY migrate_bootstrap_transition_receipts.py
```

**Expected output — script 1** (`migrate_run_kind.py`, no `--commit`):

```
<run_ts>  <family_id>  <provenance>  <run_id[:8]>  NULL -> <run_kind>
... (one line per row to be classified)

N row(s) would be classified.

Dry run only. Re-run with --commit to write.
```

If instead you see `0 rows to classify (already fully backfilled, or
ledger is empty).` — that is Scenario B for this script specifically.
Re-confirm against the Step 0c row count before treating the whole
migration as a no-op; it is possible only script 1 was already applied.

**Expected output — script 2** (`migrate_bootstrap_evidence_admission.py`):

```
family_id                    run_id      state             decision_type     verdict
... (one line per pinned run needing a receipt)

N receipt(s) would be written.

Dry run only. Re-run with --commit to write.
```

**Expected output — script 3** (`migrate_bootstrap_transition_receipts.py`):

```
#     family_id                    arc                                decision_type        ts
... (one line per transition needing a receipt)

N receipt(s) would be written.

Dry run only. Re-run with --commit to write.
```

**Cross-check against reconnaissance:** the row/receipt counts printed here
must be consistent with the `NULL run_kind` count and missing-receipt gap
observed in Step 0c. If the numbers don't reconcile, stop and understand
why before committing — a mismatch means either Step 0c or this dry run is
reading a different ledger than you think.

> **STOP if:** any script errors (traceback, not a clean "0 to classify"
> message), or the counts don't reconcile with Step 0c.

---

## STEP 2 — Execute (writes; one script at a time, verify between each)

**Run these one at a time. Do not pipe them together. Verify after each
before starting the next** — script 2 depends on script 1's committed
state, and script 3 depends on script 2's.

### 2a. `migrate_run_kind.py --commit`

```bash
cd $EVLIB
sudo -u btcbot $PY migrate_run_kind.py --commit
```

**Expected:**
```
<same before/after table as the dry run>

N row(s) would be classified.

Backed up ledger.db -> ledger.pre-run_kind-migration.<UTC-timestamp>.db
Audit note -> <path>
Committed N row(s).
```

**Verify immediately:**
```bash
sudo -u btcbot $PY -c "
import sqlite3
con = sqlite3.connect('ledger.db')
print('NULL run_kind remaining:', con.execute('SELECT COUNT(*) FROM runs WHERE run_kind IS NULL').fetchone()[0])
con.close()
"
```
**Expected:** `0`.

> **STOP if:** the script errors, the backup file is not created, or
> `NULL run_kind remaining` is not `0` after a reported success. Do not
> proceed to 2b. Go to §5 (rollback).

### 2b. `migrate_bootstrap_evidence_admission.py --commit`

```bash
sudo -u btcbot $PY migrate_bootstrap_evidence_admission.py --commit
```

**Expected:**
```
<same table as the dry run>

N receipt(s) would be written.

Backed up ledger.db -> ledger.pre-evidence-admission-migration.<UTC-timestamp>.db
Committed N bootstrap receipt(s).
```

**Verify immediately:**
```bash
sudo -u btcbot $PY -c "
import sqlite3
con = sqlite3.connect('ledger.db')
print('governance_receipts:', con.execute('SELECT COUNT(*) FROM governance_receipts').fetchone()[0])
print('  admit/reject:', con.execute(\"SELECT COUNT(*) FROM governance_receipts WHERE decision_type IN ('evidence.admit','evidence.reject')\").fetchone()[0])
con.close()
"
```

> **STOP if:** the script errors or the receipt count does not increase by
> the number reported. Go to §5.

### 2c. `migrate_bootstrap_transition_receipts.py --commit`

```bash
sudo -u btcbot $PY migrate_bootstrap_transition_receipts.py --commit
```

**Expected:**
```
<same table as the dry run>

N receipt(s) would be written.

Backed up ledger.db -> ledger.pre-transition-receipts-migration.<UTC-timestamp>.db
Committed N bootstrap receipt(s).
```

> **STOP if:** the script errors. Go to §5.

---

## STEP 3 — Post-migration verification (mandatory, all four must pass)

```bash
cd $EVLIB

# 3a. Schema fingerprint — must be clean
sudo -u btcbot $PY -c "
import governance_verification as gv
findings = gv.check_schema_fingerprint('ledger.db')
print('GV-SCHEMA findings:', findings if findings else 'NONE (clean)')
"

# 3b. Registry check
sudo -u btcbot $PY report.py --check; echo "check_exit=$?"

# 3c. Receipt chain
sudo -u btcbot $PY -c "import receipt_ledger; print(receipt_ledger.verify_chain('ledger.db'))"

# 3d. Idempotency — re-run all three; MUST all report zero pending
sudo -u btcbot $PY migrate_run_kind.py
sudo -u btcbot $PY migrate_bootstrap_evidence_admission.py
sudo -u btcbot $PY migrate_bootstrap_transition_receipts.py
```

**Expected:**
- 3a: `NONE (clean)`
- 3b: `Registry check: OK`, `check_exit=0`, family counts printed
- 3c: `ChainVerificationResult(ok=True, n_receipts=<N>, errors=[])` where
  `N` = pre-migration receipt count + the sum committed in 2b/2c
- 3d: all three print their "0 … to classify/write" no-op message

> **STOP and escalate if any of 3a–3c fails.** Do not consider Increment 5
> closed. Do not run any further governance operations against this
> ledger until resolved. This is exactly the condition ADR-P1-1 exists to
> surface loudly rather than let slide.

> **If 3d shows any script finding new work** — something wrote to the
> ledger between Step 2 and Step 3 (the §0a stop + §0d re-check should have
> prevented this). Investigate before treating the migration as complete.

---

## STEP 4 — Close-out

```bash
# Confirm the three self-backups + audit notes exist, independent of Step 0b's backup
ls -la $EVLIB/ledger.pre-*.db
ls -la $EVLIB/*.audit.txt 2>/dev/null || true

# Record the final state for the permanent record
sudo -u btcbot $PY -c "
import sqlite3
con = sqlite3.connect('ledger.db')
print('runs:', con.execute('SELECT COUNT(*) FROM runs').fetchone()[0])
print('state_transitions:', con.execute('SELECT COUNT(*) FROM state_transitions').fetchone()[0])
print('governance_receipts:', con.execute('SELECT COUNT(*) FROM governance_receipts').fetchone()[0])
con.close()
"

# Final WAL checkpoint before handing the ledger back to its normal readers/writer
sudo -u btcbot sqlite3 $EVLIB/ledger.db "PRAGMA wal_checkpoint(TRUNCATE);"
ls -la $EVLIB/ledger.db $EVLIB/ledger.db-wal $EVLIB/ledger.db-shm 2>/dev/null

# Only now — restart the two units stopped at Step 0a
sudo systemctl start btcbot-api
sudo systemctl start btcbot-backup.timer

# Verify both are back to normal
systemctl is-active btcbot-api
systemctl list-timers btcbot-backup.timer --no-pager
```

**Expected:** checkpoint busy flag `0`, no nonzero `-wal`; `btcbot-api`
reports `active`; `btcbot-backup.timer` shows a next-trigger time at the
following 02:00 UTC.

> **STOP and do not restart either unit if Step 3's checks (3a–3c) have not
> all passed.** Restarting `btcbot-api` against a ledger that hasn't passed
> governance verification would put a failing ledger back in front of a
> read path immediately; restarting the backup timer would risk its next
> run backing up a ledger nobody has confirmed is sound.

Then, **as a separate, later documentation step (not part of this
execution)** — all three completed 2026-07-26 during the post-closure
documentation reconciliation:
- ~~Update `docs/governance/p1_assurance_declaration.md` claim 4.~~ **Done**
  — claim 4 now reads **EXECUTED (ATTESTED)**.
- ~~Update `research/evidence_library/MIGRATIONS.md`'s retirement criteria
  for the three D-0 scripts.~~ **Done** — the three D-0 scripts are now
  eligible for the deferred archival decision, per that document's §3.
- ~~Update `CLAUDE.md` §18 with the Increment 5 completion entry.~~ **Done.**

---

## 5. Rollback procedure

Three independent recovery layers exist. Use the narrowest one that fixes
the problem.

**Every layer below requires the same precondition, checked first:**

```bash
# Confirm the two known touchers are still stopped (they should be, from
# Step 0a — this only matters if Step 4 already restarted them and a
# problem was discovered afterward).
for u in btcbot-backup.timer btcbot-backup.service btcbot-api; do
  printf "%-24s " "$u"; systemctl is-active "$u"
done
# If any report "active", stop them again before touching ledger.db:
sudo systemctl stop btcbot-backup.timer btcbot-backup.service btcbot-api
```

**Then, before any `cp` or `restore.py` invocation below, checkpoint and
clear WAL residue on the file about to be overwritten** — this is the
concrete fix for the audited WAL concern. `ledger.db` is WAL-mode in
production (§0a/G11-G12); a `cp` that overwrites the main `.db` file while
a stale `-wal` sits next to it can have SQLite replay mismatched
write-ahead frames against the freshly-restored file on next open —
exactly the failure mode most likely right after a failed `--commit`,
which is precisely when rollback is invoked:

```bash
cd $EVLIB
sudo -u btcbot sqlite3 ledger.db "PRAGMA wal_checkpoint(TRUNCATE);" 2>/dev/null || true
rm -f ledger.db-wal ledger.db-shm
ls -la ledger.db ledger.db-wal ledger.db-shm 2>/dev/null
```

**Expected:** `ledger.db-wal`/`ledger.db-shm` absent after this. Only
proceed to the `cp` below once confirmed.

### 5a. Mid-script failure (Step 2a/2b/2c stopped partway)

Each script's own self-backup is the fastest lever, and is scoped to
exactly that script's change:

```bash
cd $EVLIB
cp ledger.db ledger.db.failed-$(date -u +%Y%m%d_%H%M%S)   # preserve the failure state for diagnosis
cp ledger.pre-run_kind-migration.<ts>.db ledger.db        # restore to the point before THAT script ran
ls -la ledger.db ledger.db-wal ledger.db-shm 2>/dev/null  # confirm no -wal reappeared from the cp itself
sudo systemctl start btcbot-api
```

Substitute the correct `ledger.pre-*-migration.<ts>.db` for whichever step
failed. This restores to immediately before that script's write — earlier
successful steps in the same session are **not** undone by this alone; if
you need to undo the whole session, use 5b. **Do not restart
`btcbot-backup.timer` here** — leave it stopped until Step 3's
verification (re-run after rollback) passes; see Step 4's own gating logic.

### 5b. Full session rollback (undo all of Increment 5's writes)

Use Step 0b's independent backup — the one taken before Step 0c even ran:

```bash
cd $EVLIB
cp $BOT/data/ledger-backups/ledger_<STAMP-from-0b>.db ledger.db
ls -la ledger.db ledger.db-wal ledger.db-shm 2>/dev/null
sudo systemctl start btcbot-api

# Re-verify you are back to the pre-migration baseline
sudo -u btcbot $PY report.py --check
sudo -u btcbot $PY -c "import receipt_ledger; print(receipt_ledger.verify_chain('ledger.db'))"
```

This is the authoritative rollback — independent of any of the three
migration scripts' own backup behavior, satisfying G8's purpose. As in 5a,
leave `btcbot-backup.timer` stopped until re-verification passes.

### 5c. Using `scripts/restore.py` (if you prefer the tooled path)

```bash
sudo -u btcbot $VENV $BOT/scripts/restore.py \
    $BOT/data/ledger-backups/ledger_<STAMP-from-0b>.db \
    --db-path $EVLIB/ledger.db --skip-service
```

**Known limitation, confirmed by reading `scripts/restore.py`'s source
during this investigation:** the script's swap step (`shutil.copy2`) only
ever copies the single `.db` file — it does **not** remove or checkpoint
any `-wal`/`-shm` sidecar at the destination. This is a real gap in the
tool itself, not just in this runbook, and it is why the manual WAL
checkpoint/cleanup above is performed **before** invoking `restore.py`
here, not left to the tool. `--skip-service` is used because no systemd
service directly owns `ledger.db` as its primary database (unlike
`btc_trading.db` and `btcbot.service`) — `restore.py`'s SHA-256 + `PRAGMA
integrity_check` verification still applies in full and remains valid
protection for the parts of the swap it does cover.

### 5d. After any rollback

Re-run Step 0c's reconnaissance in full to confirm you are back to a known
state before deciding whether to retry, escalate, or close the increment
as failed. **Do not retry `--commit` on the same script without
understanding why it failed first.** Do not restart `btcbot-backup.timer`
until this re-verification passes (see Step 4).

---

## 6. Contingency plan — specific failure modes

| Failure | Response |
|---|---|
| Script 1 succeeds, script 2 fails | Roll back to `ledger.pre-evidence-admission-migration.<ts>.db` (undoes only script 2's attempt — script 1's classification is unaffected since it's a separate table/column). Re-run Step 1 dry-run for script 2 to see the actual error before retrying. |
| Script 2 succeeds, script 3 fails | Same pattern, using `ledger.pre-transition-receipts-migration.<ts>.db`. |
| `report.py --check` fails at Step 3b after all three scripts report success | **Do not roll back yet.** Read the specific named error (ADR-P1-1: it will be named, not silent). Determine if it's a genuine new problem or a pre-existing one unmasked by the migration (compare against Step 0c's baseline). Escalate before deciding rollback vs. forward-fix. |
| `verify_chain()` returns `ok=False` at Step 3c | **Escalate immediately. Do not attempt to repair the chain manually.** A broken hash chain after this migration is a new incident, not a rollback situation to self-serve — the chain's entire value is that it is not locally repairable. |
| Host connection drops mid-Step-2 | SSH back in, run Step 0c's reconnaissance fresh before assuming anything about state. The script that was running may have completed, partially completed (unlikely — SQLite transactions), or not started its write. Idempotency (re-running the same script) is safe in all three cases. |
| Scenario C discovered at Step 0c | Do not proceed past §3. This is not a rollback situation — nothing was written. Return to Increment 4 planning with the real production schema shape. |
| A second operator session is discovered mid-run | Stop at the current step. Coordinate — do not let two sessions write concurrently. Re-run Step 0c after the other session ends before resuming. |
| `btcbot-backup.timer` or `btcbot-api` shows `active` at a 0d re-check, after being stopped at 0a | Someone (or something) restarted it. Stop it again immediately, re-run the WAL checkpoint check, and confirm `ledger.db-wal` didn't grow in the interim before proceeding. Do not assume the interval was harmless without checking. |
| `PRAGMA wal_checkpoint(TRUNCATE)` reports a non-zero busy value, or `ledger.db-wal` still has nonzero size after the checkpoint | An open connection still exists somewhere despite both known units reporting `inactive`. Do not proceed to any `cp`/restore. Re-run the 0d process/session check; if nothing is found, escalate rather than force the checkpoint — forcing past a busy checkpoint is exactly the scenario that produces the corruption risk this gate exists to prevent. |

---

## 7. Sign-off

| Field | Value |
|---|---|
| Runbook version | 1.0 (2026-07-26) |
| Approved to execute | **YES** — approved by Rohith, 2026-07-26 |
| Executed by | Rohith, on the production host, 2026-07-26 |
| Outcome | **Completed.** Reported successful at Program P1 closure. |
| Scenario observed (§3) | *Not recorded in this repository* |
| Step 2a/2b/2c results | *Not recorded in this repository* |
| Step 3 verification | *Not recorded in this repository* |

> **Evidence note.** This execution was reported complete by the operator,
> but the per-step transcript was not captured back into the repository.
> The assurance declaration therefore records claim 4 at **EXECUTED
> (ATTESTED)** rather than DEMONSTRATED. If the host-side output is still
> recoverable (shell scrollback, `journalctl`, or the migration scripts'
> own backup files under `research/evidence_library/`), appending it to
> this section would close the gap and raise claim 4 to DEMONSTRATED. This
> is an archival improvement, not a defect — nothing indicates the
> migration was unsuccessful.
