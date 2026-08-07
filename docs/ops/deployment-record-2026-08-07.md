# Production Deployment Record — 2026-08-07

**Host:** `3.110.103.217` (`ip-172-31-7-223`) · **Repo:** `bitcoin-paper-trading-bot` · **Branch:** `main`
**Deployed:** `7fd201e` → `59e4136` (**23 commits**) · **Window:** 02:47–03:15 UTC
**Executed by:** Claude Opus 5 under standing operating authority
**Preceded by:** [Incident investigation and recovery](incident-2026-08-06-production-investigation.md)

---

## 1. Why this deployment differed from the approved plan

`deployment-plan-phase4-d0-baseline.md` computed its delta as
`program-p1-closed..HEAD` = *"four production files, 53 insertions, no
migration, no dependency change."* **Every one of those conclusions was wrong
for this host**, because the host was never at `program-p1-closed` — it was
5 commits *before* it, at `7fd201e` (2026-07-18).

| Plan said | Reality |
|---|---|
| 4 files, +53 | **36 files, +3533 / −34** |
| No dependency change | **`prometheus_client==0.25.0` added, absent from the venv** |
| No migration required | **Trading DB was at `user_version=4`; target 5** |
| `btcbot-api.service` deployed | **Never installed; no `venv-api`** |

The plan's §2.1 verified units in `deploy/` and assumed `deploy/` ≡ host. It
does not. **Lesson: verify the host's actual HEAD before computing any delta.**

## 2. Credential unblock

The host could not fetch: HTTPS remote on a private repo, no credential helper,
no `.git-credentials`, no `.netrc`, no SSH key for `btcbot`. Every historical
pull was an operator typing a PAT. Resolved with a **read-only GitHub deploy
key** — keypair generated on-host (private key never left it), public key added
by Rohith, remote switched to SSH.

## 3. Sequence executed

| # | Step | Result |
|---|---|---|
| 1 | Verify deploy key | `git ls-remote` → `59e4136` ✅ |
| 2 | Remote → SSH | ✅ |
| 3 | Baseline + rollback SHA recorded | `7fd201e` → `.rollback-sha` |
| 4 | **Gate A** — ATR drift diff-gate | Incoming **also `enabled: true`**; only comments differ ✅ |
| 5 | **Gate B** — dependency delta | `prometheus_client` new, **not installed** |
| 6 | Pre-deploy backup, all DBs | exit 0 |
| 7 | Stop 5 timers | ✅ |
| 8 | Discard ATR local edit (gate A passed) | ✅ |
| 9 | `git pull --ff-only` | → `59e4136` ✅ |
| 10 | **Post-pull ATR re-check** | **`enabled: true` preserved** ✅ |
| 11 | `pip install -r requirements.txt` | `prometheus_client` installed |
| 12 | Import smoke `src.main` | OK — *before* touching the live writer |
| 13 | **Maintenance window** — stop writer, WAL checkpoint | ✅ |
| 14 | `migrate.py` v4 → v5 | **15.7s on 4,780,477 rows**; `idx_option_quotes_ts` created |
| 15 | Restart writer | Collection resumed `66/66` |
| 16 | Install `btcbot-certify`, refresh `btcbot-healthcheck` | ✅ |
| 17 | Restart all timers | 6 active |
| 18 | Build `venv-api`, generate `API_TOKEN`, start API | ✅ |

**Gate A was the one step that could have caused silent harm** — a bare
`git checkout --` on `collection_config.yaml` would disable ATR collection if
the incoming version ever stopped carrying `enabled: true`. It was diff-gated
before and re-verified after the pull.

## 4. Post-deployment verification

| # | Check | Result |
|---|---|---|
| 1 | `backup.py --all` | **exit 0** — PROD-1 resolved |
| 2 | `healthcheck.py` CLI | `overall INFO`; `label_store_backup **INFO**` — PROD-2 resolved |
| 3 | **API `/api/v1/runtime/health`** | **Identical classification to the CLI** |
| 4 | Services | `btcbot` active · `btcbot-api` active · 6 timers · **0 failed** |
| 5 | DB integrity | trading / ledger / forward — **ok / ok / ok**; `user_version=5` |
| 6 | Governance | `report.py --check` OK (35: 9/22/4) · chain **ok, 26 receipts** |
| 7 | Evidence collection | All 5 signals at `2026-08-06` |
| 8 | Live collection | `quotes=66/66`, `coverage=1.0`, `stale=0` |
| 9 | Metrics exporter | `:9108` — `coverage_ratio 1.0`, `stale_symbols 0` |
| 10 | API probes | `/health` ok · `/readyz` `ready:true, chain_valid:true` |
| 11 | Telegram | delivered ✅ |
| 12 | Heartbeat | `sent (total=1)` at startup; 03:27 job ran |
| 13 | Rollback | SHA reachable · 2 `.env` backups · 4 ledger backups · `restore.py` present |

**Item 3 is the significant one.** `tests/test_api.py` cannot be collected in
any local conda environment (pre-existing `fastapi` gap), so `services.py`'s
Option A change had **never been executed anywhere** — only checked
structurally by an AST parity guard. This was its first runtime execution, and
CLI and API agree exactly.

## 5. Certifier verdicts — independent confirmation of the incident

Catch-up certified 7 days. The verdicts corroborate the outage without being
told about it:

| Day | Verdict |
|---|---|
| 2026-07-31 | AMBER *(testnet policy)* |
| 2026-08-01 | AMBER |
| 2026-08-02 | AMBER |
| **2026-08-03** | 🔴 **RED** |
| **2026-08-04** | 🔴 **RED** |
| **2026-08-05** | 🔴 **RED** |
| 2026-08-06 | AMBER |

The RED block is exactly the collection blackout (WS died 08-03 12:33).

## 6. Rollback

```bash
sudo systemctl stop btcbot-backup.timer btcbot-healthcheck.timer btcbot-mapsnap.timer \
                    btcbot-evidence.timer btcbot-fwd-hg.timer btcbot-certify.timer
sudo -u btcbot git -C /opt/btcbot checkout $(sudo cat /opt/btcbot/.rollback-sha)
sudo systemctl restart btcbot.service btcbot-api.service
sudo systemctl start btcbot-backup.timer btcbot-healthcheck.timer btcbot-mapsnap.timer \
                     btcbot-evidence.timer btcbot-fwd-hg.timer
```

⚠️ **The schema migration does NOT roll back with the code.** `user_version=5`
and `idx_option_quotes_ts` persist. This is forward-compatible — the index is
additive and older code ignores it — so a code rollback is safe without a DB
restore. A DB restore would only be needed for corruption, and then
**`TD-P1-002` applies**: checkpoint and clear `-wal`/`-shm` before any
`restore.py` invocation.

## 7. Still open

| Item | Status |
|---|---|
| **80,860 stale `option_quotes` rows** (single frozen `exch_ts`) | ⏸ **Owner decision** — irreversible deletion |
| **Supervisor health ignores `last_msg` age** | Open — the defect that made an 86h outage look HEALTHY |
| `healthcheck.py` has no alert rate limiting | Open (`R6`) |
| BK3 off-host replication | **NOT ESTABLISHED** — all backups on this one instance |
| BK2 production restore drill | Never performed |

---

| Role | Name | Date |
|---|---|---|
| Deployment executed | Claude Opus 5 | 2026-08-07 |
| Deploy key added | **Rohith Suresh** | 2026-08-07 |
