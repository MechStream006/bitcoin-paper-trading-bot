# Production Deployment Plan — Phase 4 D0 Baseline

| Field | Value |
|---|---|
| **Title** | Production Deployment Audit and Plan — Phase 4 D0 baseline |
| **Status** | ✅ **READY — both blockers resolved 2026-08-06 via Option A (§3.4). Awaiting deployment authorisation** |
| **Baseline audited** | `c398e802ef1d0000303fad30533dec099cf690d7` (`feat(phase-4): complete full corpus conformance (D0b)`) |
| **Last deployed reference** | `program-p1-closed` |
| **Target** | EC2 `ap-south-1`, `/opt/btcbot`, user `btcbot` |
| **Document Type** | Operational deployment plan (not an ADR, not a roadmap) |
| **Author** | Claude Opus 5 |
| **Date** | 2026-08-06 |
| **Code written** | Audit itself was read-only. The Option A fix (§3.4) was implemented separately on 2026-08-06 after Rohith's ruling. |

> **Method.** Every claim below was verified against the repository at `c398e80`. The two blockers in §3 were **empirically reproduced** against a simulated EC2 tree, not inferred from reading. They were **not** fixed during the audit — both admitted more than one valid fix, and choosing between them was an owner decision (§3.3). Rohith ruled **Option A** on 2026-08-06; §3.4 records the implementation and its verification.

---

## 1. Deployment Delta Since the Last Deployment

**The surprising result: the delta is tiny.** Four phases of research work produced **four changed production files**, all from WBS 3.10.

```
git diff --stat program-p1-closed..HEAD -- src/ scripts/ deploy/ config/ requirements.txt

 scripts/backup.py      | 48 ++++++++++++++++++++++++++++++++++++++----------
 scripts/healthcheck.py |  8 ++++++++
 src/api/config.py      |  4 ++++
 src/api/services.py    |  3 +++
 4 files changed, 53 insertions(+), 10 deletions(-)
```

| Area | Delta | Verified |
|---|---|---|
| `src/` trading bot core | **None** beyond 2 API lines | ✅ |
| `deploy/` systemd units | **None** | ✅ |
| `requirements.txt` / `requirements-api.txt` | **None** | ✅ |
| Trading DB schema | **None** — `migrate.py` `TARGET_VERSION` still **5** | ✅ |
| Deployed research collectors (`forward_evidence/`) | **One test file only** | ✅ |
| `forward_evidence` imports of Phase 0–4 trees | **Zero** — no `label_store`/`feature_store`/`admissibility`/`raw_lake` reference | ✅ |

**Why so small:** IQROS-P1's operational/research boundary held. Phases 0–3 built `research/` trees that no deployed unit invokes; Phase 4's conformance work targeted the **dev-machine corpus** only.

**The one genuinely valuable production change is WBS 4.0.1** — it repairs a defect that has been live since Phase 1: `build_corpus.py`/`build_perp_corpus.py` stripped conformance metadata from any manifest they re-upgraded. EC2 runs the ingest writers that *produce* that metadata, so deploying 4.0.1 protects the production corpus going forward.

---

## 2. Service, Backup, Health-Check and Monitoring Inventory

### 2.1 Systemd units (8 verified in `deploy/`)

| Unit | Type | Schedule | Entry point | Runtime |
|---|---|---|---|---|
| `btcbot.service` | long-running | — | `python -m src.main` | `venv` |
| `btcbot-api.service` | long-running | — | `python -m src.api.main` | `venv-api` |
| `btcbot-backup.timer` | oneshot | `02:00 UTC` | `scripts/backup.py --all` | `venv` |
| `btcbot-healthcheck.timer` | oneshot | **every 10 min** | `scripts/healthcheck.py --alert-on-warning` | `venv` |
| `btcbot-certify.timer` | oneshot | `01:30 UTC` | `scripts/certify_catchup.py` | `venv` |
| `btcbot-mapsnap.timer` | oneshot | `12:30 UTC` | `scripts/snapshot_instrument_map.py` | `venv` |
| `btcbot-evidence.timer` | oneshot | `00:05 UTC` | `forward_evidence/daily_ingest.py` | **conda `btc_bot`** |
| `btcbot-fwd-hg.timer` | oneshot | `01:00 UTC` | `forward_evidence.run_daily` | **conda `btc_bot`** |

Two runtimes are in play — `venv` (Python 3.11) for the bot, and conda `btc_bot` (Python 3.10.20) for the two research collectors. `install.md` §6 is explicit that this split is deliberate.

### 2.2 Health-check coverage (11 checks verified in `main()`)

| # | Check | Covers |
|---|---|---|
| 1 | `check_service` | systemd unit alive |
| 2 | `check_disk` | free space |
| 3 | `check_db_liveness` | trading DB writable/recent |
| 4 | `check_wal` | WAL growth |
| 5 | `check_backup_freshness` | trading backups |
| 6 | `check_backup_freshness` | ledger backups |
| 7 | `check_backup_freshness` | **label_store backups** ← **PROD-2 source** |
| 8 | `check_offhost_freshness` | trading off-host leg |
| 9 | `check_offhost_freshness` | ledger off-host leg |
| 10 | `check_forward_cycle` | forward-evidence cadence (conditional) |
| 11 | `check_integrity` | `PRAGMA integrity_check` (conditional) |

**No rate limiting or de-duplication exists in `healthcheck.py`** (verified by grep) — every run that reaches WARNING sends a Telegram message.

### 2.3 Backup coverage — 5 databases

`scripts/backup.py --all` covers: `btc_trading.db`, `forward_evidence.db`, `evidence_forward.db`, `ledger.db`, **`label_store.db`**. Each uses `VACUUM INTO` + SHA-256 manifest + rotation. The fifth is the problem (§3.1).

### 2.4 Telegram alert coverage

Alerts originate from **9 modules**: `alerts/heartbeat.py`, `alerts/rate_limiter.py`, `api/services.py`, `ingestion/chain_refresher.py`, `observability/metrics_exporter.py`, `runtime/lifecycle.py`, `scripts/certify_catchup.py`, `scripts/certify_day.py`, `scripts/healthcheck.py`.

`src/alerts/rate_limiter.py` exists and is used by the **bot's** alert paths. **`scripts/healthcheck.py` does not use it** — that asymmetry is what makes PROD-2 severe.

---

## 3. Production-Critical Blockers — ✅ BOTH RESOLVED

Both were **empirically reproduced** against a simulated EC2 tree (trading/evidence/fwd_hg/ledger DBs present, `label_store.db` absent — its real state on EC2, since it is gitignored and no deployed unit creates it; Phase 3 never ran there because `G-PROD` was never opened).

### 3.1 `PROD-1` — `btcbot-backup.service` will fail every day

**Reproduced:**

```
simulated EC2 tree; label_store.db present? False
[DRY RUN] would create .../evidence_20260806_120532.db
[DRY RUN] would create .../fwd_hg_20260806_120532.db
[DRY RUN] would create .../ledger_20260806_120532.db
[DRY RUN] would create .../btc_trading_20260806_120532.db

>>> backup.py --all exit code = 1   (FAILS -> systemd unit failed)
```

**Mechanism.** `run_backup()` returns `1` when `db_path` does not exist. `--all` returns `max(rc_trading, rc_evidence, rc_fwd_hg, rc_ledger, rc_label_store)`. All four real backups **succeed**, and the unit still reports failure because of the fifth.

**Impact.** `btcbot-backup.timer` fires daily at 02:00 UTC into a `Type=oneshot` unit that enters `failed`. Any operator or monitor keying on unit state sees a **daily false backup failure** — the single worst signal to make untrustworthy, because a real backup failure then looks identical to the noise.

**Severity: Critical.** Introduced by WBS 3.10.

### 3.2 `PROD-2` — 144 false Telegram alerts and unit failures per day

**Reproduced:**

```
>>> healthcheck label_store_backup: severity='WARNING'
    message='backup directory does not exist'
    --alert-on-warning would send Telegram: True
```

**Mechanism.** `data/label-store-backups/` is never created on EC2 — `run_backup()` returns before its `mkdir` (§3.1), so the directory cannot come into existence. `check_backup_freshness` returns WARNING. `main()` then:
- sends a Telegram alert (`overall == WARNING and args.alert_on_warning`), and
- returns **1**, so the oneshot unit also enters `failed`.

**Impact, quantified.** `btcbot-healthcheck.timer` runs **every 10 minutes** with **no rate limiting**: **144 Telegram alerts/day and 144 unit failures/day**, permanently, for a condition that is correct and expected on EC2. This is alert-channel destruction — the channel that must carry genuine trading and collection alarms.

**Severity: Critical.** Introduced by WBS 3.10.

### 3.4 ✅ Resolution — Option A implemented 2026-08-06

Ruled by Rohith: `btc_trading.db` **REQUIRED**; `forward_evidence.db`, `evidence_forward.db`, `ledger.db`, `label_store.db` **OPTIONAL**.

> **Naming clarification.** The ruling listed the optional set as *"evidence.db, forward_evidence.db, ledger.db, label_store.db"*. In code the two transposition-prone research databases are `--evidence` → `forward_evidence.db` and `--fwd-hg` → `evidence_forward.db` (roadmap risk `LR4`). Intent was unambiguous — **all four research databases optional, only the trading database required** — and that is what was implemented. Stated here rather than silently resolved.

| Change | File |
|---|---|
| `REQUIRED_DB_LABELS` + `is_required()`; missing optional → `[skip]`, rc 0; missing required → `[error]`, rc 1 | `scripts/backup.py` |
| `check_backup_freshness(..., required=)`; optional + never configured → **INFO**; `required=False` wired for ledger and label_store | `scripts/healthcheck.py` |
| Same `required=False` wiring, preserving CLI/API parity | `src/api/services.py` |

**Verified after the fix, against the same simulated EC2 tree:**

```
>>> PROD-1: backup.py --all exit code = 0   (ok)
>>> PROD-2: label_store_backup severity : INFO
            would send Telegram         : False
```

**Two properties deliberately preserved:**
- **A missing REQUIRED database still fails**, and `--all`'s max-of-return-codes still propagates it (`test_all_flag_still_propagates_a_required_failure`).
- **Freshness is still enforced for optional databases once backups exist** — only the never-configured case is downgraded. A *stale* optional backup remains a WARNING (`test_optional_freshness_is_still_enforced_once_backups_exist`).

**An unknown label defaults to OPTIONAL** (`test_an_unknown_label_is_treated_as_optional`) — fail-soft, so adding a future database can never reintroduce PROD-1 by omission.

### 3.3 Why these were not fixed at audit time

Both admit **more than one valid fix**, and the choice changes backup semantics:

| Option | Description | Trade-off |
|---|---|---|
| **A** | Treat a missing **optional** DB as skip (`rc=0`) with a notice; keep a missing **required** DB fatal | Smallest change; requires classifying each of the 5 targets as required/optional — a real semantic decision |
| **B** | Remove `--label-store` from `--all`; keep it opt-in | Trivial; label-store backup then never runs anywhere it is not explicitly invoked |
| **C** | Gate both the backup and the health check on the DB existing | Symmetric; risks silently skipping a database that *should* exist |
| **D** | Create `label_store.db` on EC2 | ❌ Wrong — it is dev-machine research data; would put unowned data in production |

**Recommendation: A**, with `btc_trading.db` required and the four research DBs optional — it keeps a genuinely missing trading DB fatal, which is the case that matters. But this is a decision, and the standing rule against silently widening scope applies.

**A fix must also cover the health check**, not only the backup — otherwise PROD-2 survives PROD-1's repair.

---

## 4. Database Migration Requirements

| Item | Verified |
|---|---|
| `migrate.py` `TARGET_VERSION` | **5** — unchanged since `program-p1-closed` |
| Trading-schema delta since last deploy | **None** (`scripts/migrate.py`, `src/persistence/` both unchanged) |
| **Migration required for this deployment** | ✅ **NO** |

`scripts/migrate.py` remains idempotent and safe to run; running it is a no-op confirmation, not a requirement. The one expensive step (`4 → 5`, `CREATE INDEX`, ~55 s at 36 M rows) was already applied at the P1 deployment.

**Research databases** (`ledger.db`, `feature_store.db`, `label_store.db`) have their own schema-ensure paths and are **not** part of the production migration surface. `label_store.db` and `feature_store.db` do not exist on EC2 at all.

---

## 5. Backup and Restore Verification

| Item | State |
|---|---|
| `backup.py` mechanism | `VACUUM INTO` + SHA-256 manifest + rotation — unchanged and sound |
| Coverage | 5 DBs; **the 5th breaks the run on EC2** (§3.1) |
| `restore.py` | Present; takes `backup_file`, verifies SHA-256 manifest, `PRAGMA integrity_check`, `.pre-restore` safety copy, systemd stop/start |
| Restore test coverage | `tests/test_restore.py` — 21 tests (P1 Increment 2) |
| Documented drills | `restore-drill-2026-07-19.md` (trading), `label-store-restore-drill-2026-08-06.md` (label store, **dev machine only**) |
| **BK2 production restore drill** | ❌ **Never performed** — open since P1 closeout |
| Off-host replication (**BK3**) | ❌ **Not activated** — monitoring wired (P1 Increment 6), transfer never enabled |
| `TD-P1-002` WAL-aware restore | ❌ **Open** — `restore.py` copies only the `.db`, never checkpoints or clears `-wal`/`-shm` |

**`TD-P1-002` is the most consequential of these for an actual recovery.** The trading DB is WAL-mode in production; a restore that ignores `-wal` can silently resurrect stale state. The Increment 5 runbook works around it manually, but `restore.py` itself is still unsafe by default.

---

## 6. EC2 Deployment Sequence

Follows `deploy/install.md` for a fresh host. For **this** deployment (an existing host at `program-p1-closed`), the sequence is far shorter because §1 showed the delta is four files.

| # | Step | Command / action | Notes |
|---|---|---|---|
| 0 | **Resolve PROD-1 + PROD-2** | §3.3 | ⛔ **Blocking** |
| 1 | Pre-deploy backup | `sudo -u btcbot /opt/btcbot/venv/bin/python scripts/backup.py --all` | Must exit **0** — this is also the PROD-1 regression gate |
| 2 | Capture rollback point | `git -C /opt/btcbot rev-parse HEAD` | Record the SHA |
| 3 | Stop timers | `sudo systemctl stop btcbot-backup.timer btcbot-healthcheck.timer btcbot-certify.timer btcbot-mapsnap.timer btcbot-evidence.timer btcbot-fwd-hg.timer` | Leave `btcbot.service` running — no bot-core change |
| 4 | Fetch code | `sudo -u btcbot git -C /opt/btcbot fetch origin && git checkout <sha>` | |
| 5 | Dependencies | **No change** — `requirements*.txt` unchanged; venv rebuild not required | Verified §1 |
| 6 | Migration | **Not required** — `TARGET_VERSION` still 5. Optionally run `scripts/migrate.py --dry-run` to confirm | Verified §4 |
| 7 | Smoke: backup | `scripts/backup.py --all --dry-run` → **exit 0** | |
| 8 | Smoke: health | `scripts/healthcheck.py --no-telegram` → **exit 0**, no `label_store_backup` WARNING | |
| 9 | Restart timers | `sudo systemctl start …` (all six) | |
| 10 | Reload API (only if API changed) | `sudo systemctl restart btcbot-api.service` | `config.py`/`services.py` changed → **required** |
| 11 | Verify | §9 post-deployment checklist | |

**`btcbot.service` (the bot core) does not need restarting** — `src/main.py` and everything it imports are unchanged. Restarting it anyway is safe but adds avoidable collection-gap risk on a 24/7 market.

---

## 7. Deployment Checklist

**Pre-deployment**

- [ ] PROD-1 resolved and regression-tested
- [ ] PROD-2 resolved and regression-tested
- [ ] Full suite green on the deployment commit (`2359/19/0` btc_bot, `1910/62/0` base)
- [ ] `scripts/backup.py --all` exits **0** on the host **before** the change
- [ ] Rollback SHA recorded
- [ ] `.env` untouched and present (never in git — verified)
- [ ] Disk free ≥ 20% on `/opt/btcbot`

**Deployment**

- [ ] Timers stopped (6)
- [ ] Code fetched to the target SHA
- [ ] `git status` on host is clean (no local drift)
- [ ] No dependency install performed (none required)
- [ ] No migration performed (none required)
- [ ] `backup.py --all --dry-run` → exit 0
- [ ] `healthcheck.py --no-telegram` → exit 0
- [ ] Timers restarted (6)
- [ ] `btcbot-api.service` restarted
- [ ] `btcbot.service` **not** restarted (unless independently justified)

**Immediate verification**

- [ ] `systemctl list-timers 'btcbot-*'` — all 6 active with sane next-fire
- [ ] `systemctl is-failed 'btcbot-*'` — none failed
- [ ] `journalctl -u btcbot-healthcheck --since '-15m'` — one clean run, no label_store WARNING
- [ ] No unexpected Telegram traffic in 30 minutes

---

## 8. Rollback Plan

**Rollback is cheap here** — four files, no schema change, no dependency change.

| Trigger | Action |
|---|---|
| Backup unit fails | `git checkout <rollback-sha>` → restart timers. No data implication |
| Health check floods Telegram | Immediate: `systemctl stop btcbot-healthcheck.timer`. Then roll back code |
| API fails to start | `git checkout <rollback-sha>` → `systemctl restart btcbot-api.service` |
| Bot core misbehaves | **Should be impossible** (unchanged), but: `git checkout <rollback-sha>` → `systemctl restart btcbot.service` |
| Data corruption | `scripts/restore.py <backup>` — ⚠️ **see `TD-P1-002`**: checkpoint and remove `-wal`/`-shm` manually first |

**Rollback verification:** `backup.py --all` exits 0 · `healthcheck.py --no-telegram` exits 0 · no failed units · bot heartbeat resumes.

**What rollback does *not* undo:** nothing. No migration, no schema change, no data mutation is part of this deployment.

---

## 9. Post-Deployment Monitoring Checklist

**First hour**
- [ ] Health check: 6 consecutive clean runs (10-min cadence)
- [ ] Zero failed units
- [ ] Telegram: only expected heartbeats
- [ ] API `/health` responds

**First 24 hours**
- [ ] `btcbot-evidence` (00:05) succeeded
- [ ] `btcbot-fwd-hg` (01:00) succeeded
- [ ] `btcbot-certify` (01:30) succeeded
- [ ] **`btcbot-backup` (02:00) succeeded and exited 0** ← the PROD-1 regression gate
- [ ] `btcbot-mapsnap` (12:30) succeeded
- [ ] All 5 backup sets present with fresh manifests
- [ ] Telegram alert volume matches the pre-deployment baseline

**First week**
- [ ] No new `failed` units
- [ ] Backup freshness never WARNING
- [ ] WAL size stable
- [ ] Disk trend flat
- [ ] Forward-evidence cadence unbroken (no lost calendar days — unrecoverable)

---

## 10. Remaining Operational Risks

| # | Risk | Severity | State |
|---|---|---|---|
| ~~**R1**~~ | `PROD-1` — daily false backup failure | ~~🔴 Critical~~ | ✅ **RESOLVED** (§3.4) |
| ~~**R2**~~ | `PROD-2` — 144 false alerts + 144 unit failures/day | ~~🔴 Critical~~ | ✅ **RESOLVED** (§3.4) |
| **R3** | **BK2 production restore drill never performed** | 🟠 High | Open since P1 |
| **R4** | **BK3 off-host replication never activated** — all backups are on the same EC2 instance; instance loss = total loss | 🟠 High | Open since P1 |
| **R5** | `TD-P1-002` — `restore.py` is not WAL-aware; a restore can resurrect stale state | 🟠 High | Open |
| **R6** | `healthcheck.py` has **no alert rate limiting**, unlike the bot's own alert paths | 🟡 Medium | Structural; PROD-2 is its first real consequence |
| **R7** | EC2's research corpus is **not conformed** — D0a/D0b were dev-machine only | 🟢 Low | Correct and expected; nothing on EC2 consumes conformance yet. WBS 4.0.1 protects it going forward |
| **R8** | Two Python runtimes (venv 3.11 + conda 3.10.20) | 🟢 Low | Deliberate; documented in `install.md` §6 |
| **R9** | Neither corpus builder has a test file | 🟢 Low | Noted at WBS 4.0.1; not deployment-blocking |

**R3 + R4 together are the standing durability exposure**: backups are taken, verified by manifest, and never tested by a real production restore, on a host whose loss would take the backups with it. Neither is new, neither is caused by this deployment, and both remain the largest operational risks on the platform.

---

## 11. Verdict

# ✅ READY TO DEPLOY — pending authorisation

Both production-critical blockers are **resolved and regression-tested** (§3.4). Every other pre-deployment checklist item in §7 is verified:

| Item | State |
|---|---|
| PROD-1 / PROD-2 resolved | ✅ exit 0; INFO, no alert |
| Full suite | ✅ btc_bot **2390/19/0**, base **1941/62/0** |
| `.env` not in git | ✅ |
| Dependency change | ✅ none |
| Schema migration | ✅ none required (`TARGET_VERSION` 5) |
| `deploy/` units | ✅ unchanged |
| Bot core (`src/main.py`, `runtime/`, `ingestion/`) | ✅ unchanged |
| Governance chains / DB integrity | ✅ 8/17/26 valid; four DBs `integrity_check=ok` |

**One verification gap, stated rather than glossed:** `tests/test_api.py` cannot be collected in any local conda environment (pre-existing `fastapi` gap, documented since P1 Increment 1), so `src/api/services.py`'s change is **not covered by a runtime test here**. It *is* covered structurally — `test_api_services_mirrors_the_cli` parses both modules with `ast` and asserts the API and CLI classify every database identically. First runtime exercise will be the post-deploy API restart (§6 step 10).

**Remaining risks are unchanged and none is deployment-blocking:** `R3` (BK2 restore drill never performed), `R4` (BK3 off-host never activated), `R5` (`TD-P1-002` WAL-aware restore), `R6` (healthcheck has no alert rate limiting — PROD-2 was its first consequence, and the structural gap remains).

**Deployment authorisation is Rohith's and has not been given.**

---

| Role | Name | Date |
|---|---|---|
| Deployment audit | Claude Opus 5 | 2026-08-06 |
| **PROD-1 / PROD-2 fix ruling** | **Rohith Suresh** | ✅ **Option A, 2026-08-06** |
| **Deployment authorisation** | **Rohith Suresh** | **_____ WITHHELD _____** |
