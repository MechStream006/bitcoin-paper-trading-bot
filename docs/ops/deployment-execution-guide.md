# Production Deployment — Operator Execution Guide

**Single-session, copy-paste runbook for the manual VPS deployment.**
Derived from `docs/ops/deployment-plan-2026-07-19.md` (rationale lives there;
this document is execution only). Do not improvise ordering — each step gates
the next.

Host: `ubuntu@3.110.103.217`  ·  Root: `/opt/btcbot`

---

## What this deploys

| Component | Effect on deploy |
|---|---|
| **Phase 2** — metrics exporter | Runtime code change (bootstrap/drain order). Ships as an unconditional top-level import (`main.py` → `runtime/lifecycle.py` → `observability/metrics_exporter.py` → `prometheus_client`) and is **enabled by default** (`METRICS_EXPORTER_ENABLED` defaults to `1` when unset). It therefore activates at **Step 4e** — the first restart after the pull — not at Step 8. Runtime deps must be installed **before** Step 4e (see Step 2). |
| **Phase 3** — observability (Prometheus/Grafana/Alertmanager/node-exporter) | New containers. Cannot touch the runtime. |
| **Phase 4** — read-only API | New additive unit. |
| **ENG-017** — `idx_option_quotes_ts` | Schema migration to `user_version = 5`. Requires a stopped-bot window. |
| **ATR activation** | `git pull` flips `g3_atr_expansion` to enabled — see the ATR hazard in Step 2/3. |
| **Certifier scheduling** | New timer at 01:30 UTC. |

**Ships in the pull but INERT — not activated by this deployment:**
- **Proposal 1** (pairwise independence): additive empty `pairwise_r` table, created by the idempotent schema-ensure `--check` already runs. No action, no migration. Gate is vacuous (zero Validated families).
- **Off-host encrypted backup (BK3)**: `scripts/offhost_backup.py` ships, `backup.py --encrypt` defaults **off**, `btcbot-backup.service` is unchanged. Enabling BK3 is a **separate** gated sequence in `docs/ops/offhost-backup.md §8` — **out of scope here.**
- **n_trials reconciliation**: docs + one comment only.

---

## GO / NO-GO gate — all must be TRUE before Step 0

| # | Precondition | Check |
|---|---|---|
| G1 | All work committed to `main` and pushed | `git status --porcelain` empty locally |
| G2 | CI green on that commit (no override) | GitHub Actions ✓ |
| G3 | Local `report.py --check` clean | exit 0 |
| G4 | Local `receipt_ledger.verify_chain()` valid | `ok=True, n_receipts=26` |
| G5 | Full suite passes | 1273 passed, 2 known date-dependent expiry fails only |
| G6 | Rohith's Phase 4 production gate confirmed | ADR §19 |
| G7 | Deploy window is **03:00–08:00 UTC** | see timing box |

**If any is FALSE — stop. Do not begin.**

### Timing constraints (all hold simultaneously)

```
NEVER deploy 11:58–12:06 UTC   settlement dead zone
AVOID 00:05–01:00 UTC          evidence -> fwd-hg ordering precondition
AVOID 01:30 UTC ±5m            certifier
AVOID 02:00 UTC ±10m           backup
AVOID 12:30 UTC                mapsnap
NEVER deploy 23:00–01:00 UTC   compresses the ATR gate (Step 3) to <1h
PREFERRED: 03:00–08:00 UTC
```

---

## Session setup (paste once per SSH session)

```bash
KEY=D:/My_paper_trading_bot/bitcoin-key.pem
ssh -i "$KEY" -o StrictHostKeyChecking=no ubuntu@3.110.103.217

# ON THE SERVER — paste this block once:
BOT=/opt/btcbot
CFG=$BOT/research/forward_evidence/collection_config.yaml
PY=/opt/miniconda3/envs/btc_bot/bin/python      # [VERIFY ON HOST] conda path
VENV=$BOT/venv/bin/python                         # runtime venv
EVDB=$BOT/research/data/evidence_forward.db
STAMP=$(date -u +%Y%m%d)
echo "BOT=$BOT  STAMP=$STAMP"; $VENV --version; $PY --version
```

---

## STEP 0 — Pre-flight (no risk, no window)

```bash
# 0a. Record the rollback target — WRITE THIS DOWN
sudo -u btcbot git -C $BOT rev-parse HEAD | tee /tmp/rollback_commit.txt

# 0b. Full backup of all four DBs (ADR D6, before any schema change)
sudo -u btcbot $VENV $BOT/scripts/backup.py --all; echo "exit=$?"

# 0c. Prove the backups exist (BK4 — not assumed from exit 0)
ls -lh $BOT/data/backups/ $BOT/data/ledger-backups/ \
       $BOT/data/evidence-backups/ $BOT/data/evidence-fwd-backups/ | tail -20

# 0d. Governance baseline BEFORE
cd $BOT/research/evidence_library
sudo -u btcbot $PY report.py --check; echo "check_exit=$?"
sudo -u btcbot $PY -c "import receipt_ledger; print(receipt_ledger.verify_chain('ledger.db'))"

# 0e. Record runtime baseline for the §16 item-7 comparison — SAVE THIS OUTPUT
sudo -u btcbot sqlite3 -header -column $BOT/data/btc_trading.db \
 "SELECT ts, universe_size, coverage_pct, reconcile_count, stale_count
  FROM collection_metrics ORDER BY ts DESC LIMIT 10;"

# 0f. Host facts that size the window
sudo -u btcbot sqlite3 $BOT/data/btc_trading.db "PRAGMA user_version;"
sudo -u btcbot sqlite3 $BOT/data/btc_trading.db "SELECT COUNT(*) FROM option_quotes;"
df -h $BOT
```

**Expected:**
- 0b: `exit=0`; ledger/evidence/fwd backups all listed with today's date.
- 0d: `Registry check: OK`, `check_exit=0`; `ChainVerificationResult(ok=True, n_receipts=26, errors=[])`.
- 0f: note `user_version` (drives Step 4) and the `option_quotes` count (drives window length: `rows / 3,000,000 × 4s` index build).

> **STOP if:** any backup missing/`exit≠0`; `check_exit≠0`; chain not `ok=True`.
> Never deploy onto a governance-failing ledger.

---

## STEP 1 — ATR Phase A + B (PRE-PULL, hazard fix)

The repo config is `enabled: true`, so **the pull in Step 2 activates ATR.**
Phases A/B must run against the still-disabled on-host config first.

```bash
# 1a. Backup the genuine pre-activation config (no-clobber)
test -f "$CFG.bak.$STAMP" && echo "BACKUP EXISTS — not overwriting" || cp "$CFG" "$CFG.bak.$STAMP"
md5sum "$CFG" "$CFG.bak.$STAMP"          # expect two identical hashes

# 1b. Confirm pre-pull state is genuinely disabled
grep -n "enabled:" "$CFG"                 # expect a single: enabled: false

# 1c. Verify fn_hash ON HOST (do not trust any off-host run)
sudo -u btcbot bash -c "cd $BOT/research && $PY -c \"from forward_evidence.pre_reg_guard import verify_fn_hash; import pathlib as P; b='forward_evidence/'; print('FNHASH OK:', verify_fn_hash(P.Path(b+'pre_registration_g3_atr_expansion.json'), P.Path(b+'decision_function_g3_atr_expansion.py'), 'g3_atr_expansion')[:8])\""
```

**Expected:** 1a two identical MD5s · 1b one `enabled: false` · 1c `FNHASH OK: acaf70e9`.

> **STOP if:** `FNHASH` ≠ `acaf70e9` (pre-registration integrity failure — do
> not deploy, escalate) · more than one `enabled:` line.

---

## STEP 2 — Pull (nothing restarts)

```bash
sudo -u btcbot git -C $BOT fetch --all
sudo -u btcbot git -C $BOT pull --ff-only
sudo -u btcbot git -C $BOT rev-parse HEAD               # confirm the intended commit

# requirements.txt gained one line (prometheus_client) in this pull. The
# runtime process is NOT restarted here, but Step 4e (the ENG-017 restart)
# is the first restart after this pull — main.py's import chain reaches
# prometheus_client unconditionally at process start, regardless of the
# METRICS_EXPORTER_ENABLED flag (that flag only gates instantiation, not
# import). Installing here, before any restart, is load-bearing.
sudo -u btcbot $BOT/venv/bin/pip install -r $BOT/requirements.txt; echo "pip_exit=$?"

grep -n "enabled:" "$CFG"                 # expect: enabled: true   (ATR now active in config)
diff "$CFG.bak.$STAMP" "$CFG"             # expect exactly one hunk (false -> true)
systemctl is-active btcbot.service        # still running OLD code — expect: active
```

**Expected:** fast-forward succeeds; `pip_exit=0`; `enabled: true`; one-hunk diff;
bot still `active` (this step still does not restart the runtime).

> **STOP if:** `pull --ff-only` fails (diverged — reconcile before continuing) ·
> `pip_exit≠0` (do not proceed to Step 4 with runtime deps unresolved — the
> Step 4e restart will crash on import) · the diff shows more than the single
> enabled-flag change.

---

## STEP 3 — ATR Phase D (immediately after pull — the real gate)

Must complete **before the next 01:00 UTC fwd-hg firing.**

```bash
sudo -u btcbot bash -c "cd $BOT/research && $PY -m forward_evidence.run_daily --dry-run"; echo "exit=$?"
```

**Expected:** `[dry-run] g3_atr_expansion ... position=<-1|0|1>` (no DB write), `exit=0`,
and all five signals compute.

> **DECISION POINT:**
> - `position=-1|0|1`, exit 0 → proceed to Step 4.
> - `unavailable` or `error` for `g3_atr_expansion` → **STOP.** On the host
>   `daily_ingest.py` keeps OHLCV current, so this is a real failure, not dev
>   staleness. Roll back the config (Rollback R1) and abort the deployment.

---

## STEP 4 — ENG-017 migration ⟨MAINTENANCE WINDOW — bot stopped⟩

This is the only downtime. ~4 min at current scale.

```bash
# 4a. Stop the writer (SIGTERM drain, D4 — never kill -9)
sudo systemctl stop btcbot.service
systemctl is-active btcbot.service            # expect: inactive
ls -lh $BOT/data/btc_trading.db*              # -wal absent or 0 bytes (clean checkpoint)

# 4b. Dry-run, then apply
sudo -u btcbot $VENV $BOT/scripts/migrate.py --dry-run
time sudo -u btcbot $VENV $BOT/scripts/migrate.py

# 4c. Verify schema
sudo -u btcbot sqlite3 $BOT/data/btc_trading.db "PRAGMA user_version;"        # expect 5
sudo -u btcbot sqlite3 $BOT/data/btc_trading.db "PRAGMA integrity_check;"     # expect ok
sudo -u btcbot sqlite3 $BOT/data/btc_trading.db \
 "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_option_quotes_ts';"

# 4d. Prove the index is USED (SEARCH, not SCAN — the ENG-017 signal)
sudo -u btcbot sqlite3 $BOT/data/btc_trading.db \
 "EXPLAIN QUERY PLAN SELECT max(ts) FROM option_quotes;"

# 4e. Restart the writer — this IS the first execution of the new code.
# Runtime deps were installed in Step 2, so the prometheus_client import
# resolves; METRICS_EXPORTER_ENABLED defaults to 1 (unset in .env => on), so
# the exporter starts here too, not at Step 8.
sudo systemctl start btcbot.service
sleep 90
sudo -u btcbot $VENV $BOT/scripts/healthcheck.py --no-telegram; echo "hc_exit=$?"
journalctl -u btcbot.service -n 80 --no-pager | grep -iE "exporter|error|traceback"
curl -sf http://127.0.0.1:9108/metrics | head -5   # exporter should already be live
```

**Expected:** 4c `user_version=5`, `ok`, index present · 4d plan contains
`SEARCH`/covering index, **not** `SCAN option_quotes` · 4e healthcheck `db_liveness` OK
after warmup, no `error`/`traceback` in the journal, and `/metrics` already renders
(the exporter is live from this step onward — Step 8 only adds observability/API
around it, it does not activate it).

> **STOP / ROLLBACK if:** 4c `integrity_check` ≠ `ok` or `user_version` ≠ 5 →
> **Rollback R-DB** (restore from the 0b backup). This is the only step whose
> rollback touches data.
> **STOP / ROLLBACK if:** 4e the service will not stay `active`, or the journal
> shows a `ModuleNotFoundError`/traceback on startup → **Rollback R-CODE**
> (this is now the real risk point for the runtime code change, not Step 8).

**Window ends here.** Everything below is additive or one further restart.

---

## STEP 5 — Certifier units

Depends on `user_version >= 4` (now 5). ✓

```bash
sudo cp $BOT/deploy/btcbot-certify.service /etc/systemd/system/
sudo cp $BOT/deploy/btcbot-certify.timer   /etc/systemd/system/
sudo systemctl daemon-reload

# Dry-run BEFORE enabling — proves schema + plan
sudo -u btcbot $VENV $BOT/scripts/certify_catchup.py --dry-run; echo "exit=$?"

sudo systemctl enable btcbot-certify.timer
sudo systemctl start  btcbot-certify.timer
systemctl list-timers btcbot-certify.timer --no-pager        # next run 01:30 UTC

# Optional: one manual run to prove the path
sudo systemctl start btcbot-certify.service
systemctl show btcbot-certify.service -p Result -p ExecMainStatus
```

**Expected:** dry-run `exit=0` printing `to_certify/already/no_data`; timer lists
next run 01:30 UTC; manual `Result=success` **even for an AMBER/RED verdict**
(`SuccessExitStatus=0 1 2`).

> **STOP if:** dry-run `exit=3` → schema/DB problem; do **not** enable the timer.
> **Reminder:** a green unit ≠ a green day. Read the verdict from `daily_certifier_log`;
> testnet is AMBER by policy (C7).

---

## STEP 6 — Observability plane (Phase 3)

```bash
cd $BOT/deploy/compose
test -f .env || sudo -u btcbot cp .env.example .env
sudo -u btcbot nano .env          # set a real GRAFANA_ADMIN_PASSWORD

sudo -u btcbot docker compose config >/dev/null && echo "COMPOSE CONFIG OK"
sudo -u btcbot docker compose up -d
sudo -u btcbot docker compose ps

curl -sf http://127.0.0.1:9090/-/healthy  && echo " prometheus OK"
curl -sf http://127.0.0.1:9093/-/healthy  && echo " alertmanager OK"
curl -sf http://127.0.0.1:3000/api/health && echo " grafana OK"
curl -sf http://127.0.0.1:9100/metrics >/dev/null && echo " node-exporter OK"
curl -s http://127.0.0.1:9090/api/v1/targets | grep -o '"health":"[a-z]*"' | sort | uniq -c
```

**Expected:** all four health checks OK; the **btcbot exporter target is DOWN** —
**this is correct** (Phase 2 not deployed until Step 8).

> **STOP if:** any container unhealthy, or any of these binds `0.0.0.0` (see Step 9 item 6).

---

## STEP 7 — API service (Phase 4)

```bash
# Separate venv — a FastAPI/pydantic bump must never alter the runtime's deps
sudo -u btcbot python3 -m venv $BOT/venv-api
sudo -u btcbot $BOT/venv-api/bin/pip install --upgrade pip
sudo -u btcbot $BOT/venv-api/bin/pip install -r $BOT/requirements-api.txt

# Token — the service refuses to start without a sufficiently long one
python3 -c "import secrets; print('API_TOKEN=' + secrets.token_urlsafe(32))"
sudo -u btcbot nano $BOT/.env            # append that API_TOKEN line

sudo cp $BOT/deploy/btcbot-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable btcbot-api.service
sudo systemctl start  btcbot-api.service
systemctl is-active btcbot-api.service

TOKEN=$(sudo grep '^API_TOKEN=' $BOT/.env | cut -d= -f2-)
curl -sf http://127.0.0.1:9109/health && echo " api health OK"
curl -s -o /dev/null -w "unauth=%{http_code}\n" http://127.0.0.1:9109/governance/verify  # expect 401/403
curl -sf -H "Authorization: Bearer $TOKEN" http://127.0.0.1:9109/governance/verify | head
ss -tlnp | grep 9109        # expect 127.0.0.1:9109 — never 0.0.0.0
```

**Expected:** unit `active`; `/health` 200; unauthenticated `401/403`; authenticated
returns JSON; bind `127.0.0.1` only.

> **STOP if:** unit fails to start (usually `API_TOKEN` missing/short) · binds `0.0.0.0`.
> API failure does **not** affect `btcbot.service` — no runtime rollback needed here.

---

## STEP 8 — Exporter verification (already live since Step 4e)

The exporter was already installed (Step 2) and activated (Step 4e restart) —
this step verifies it, and connects it to the now-running observability
plane (Step 6). It does **not** restart the runtime.

```bash
systemctl is-active btcbot.service     # expect: active (unchanged since Step 4e)

# Exporter live + loopback-only
curl -sf http://127.0.0.1:9108/metrics | head -20
ss -tlnp | grep 9108        # expect 127.0.0.1:9108

# Prometheus target should now flip to up
sleep 60
curl -s http://127.0.0.1:9090/api/v1/targets | grep -o '"health":"[a-z]*"' | sort | uniq -c
```

**Expected:** `active`; `/metrics` renders; bind `127.0.0.1:9108`; exporter target now `up`.

> **KILL SWITCH (no redeploy):** if the exporter misbehaves, set
> `METRICS_EXPORTER_ENABLED=0` in `$BOT/.env` and `systemctl restart btcbot`.
> Bind address is hardcoded to loopback; no env can widen it.
> **If the exporter never came up:** the fault was introduced at Step 4e, not
> here — see Step 4e's own stop/rollback condition. A `systemctl restart` at
> this point re-runs the same already-installed code and will not fix an
> import-time failure.

---

## STEP 9 — Post-deploy verification block (ADR §16 — all seven mandatory)

```bash
# 1
systemctl is-active btcbot.service
# 3
cd $BOT/research/evidence_library && sudo -u btcbot $PY report.py --check; echo "exit=$?"
# 4
sudo -u btcbot $PY -c "import receipt_ledger; print(receipt_ledger.verify_chain('ledger.db'))"
# 5
sudo -u btcbot $VENV $BOT/scripts/healthcheck.py --no-telegram; echo "hc_exit=$?"
# 6
ss -tlnp
# extras
sudo -u btcbot sqlite3 $BOT/data/btc_trading.db "PRAGMA user_version;"
sudo -u btcbot $PY -c "import sqlite3;from collections import Counter;c=sqlite3.connect('ledger.db');print('n_trials',dict(Counter(r[0] for r in c.execute('SELECT n_trials FROM runs'))))"
```

| # | Check | PASS |
|---|---|---|
| 1 | `is-active btcbot.service` | `active` |
| 2 | **Telegram heartbeat** | `fwd-evid : YYYY-MM-DD  ok=5/5  fn_hash=ok  gov=ok` |
| 3 | `report.py --check` | `Registry check: OK`, exit 0 |
| 4 | `verify_chain()` | `ok=True, n_receipts=26` |
| 5 | `healthcheck.py` | overall OK |
| 6 | `ss -tlnp` | no `0.0.0.0` except `:22`; 9108/9109/9090/9093/3000/9100 all `127.0.0.1` |
| 7 | coverage/reconcile vs 0e baseline | unchanged (24h — Step 10) |
| + | `user_version` | `5` |
| + | ledger `n_trials` | `{34: N}` uniformly (Proposal 1 changed nothing) |

**Item 2 is the highest-value single check** — `ok=5/5` (not 4/5) confirms ATR
activation, `fn_hash`, governance, and the sandbox/filesystem/WAL layers in one line.

> **DECISION POINT:** all seven trending green → deployment is provisionally
> complete; proceed to the 24-hour watch. Any hard failure (items 1,3,4,6) →
> roll back the narrowest failing component.

---

## Rollback quick reference

| ID | Trigger | Command | Data risk |
|---|---|---|---|
| **R1** | ATR dry-run fails (Step 3) | `cp "$CFG.bak.$STAMP" "$CFG"` — no restart (one-shot re-reads YAML) | none |
| **R-DB** | Migration integrity fail (Step 4) | `sudo systemctl stop btcbot; sudo -u btcbot $VENV $BOT/scripts/restore.py $BOT/data/backups/btc_trading_<0b-stamp>.db --skip-service; sudo -u btcbot sqlite3 $BOT/data/btc_trading.db "PRAGMA integrity_check;"; sudo systemctl start btcbot` | **real** (proven in restore drill) |
| **R5** | Certifier bad (Step 5) | `sudo systemctl disable --now btcbot-certify.timer; sudo rm /etc/systemd/system/btcbot-certify.{service,timer}; sudo systemctl daemon-reload` | none |
| **R6** | Observability bad (Step 6) | `cd $BOT/deploy/compose && sudo -u btcbot docker compose down` | none |
| **R7** | API bad (Step 7) | `sudo systemctl disable --now btcbot-api` | none |
| **R-CODE** | Exporter/runtime bad (first appears at Step 4e; Step 8 only re-verifies) | `METRICS_EXPORTER_ENABLED=0` in `.env` + `restart btcbot` (preferred); or `sudo -u btcbot git -C $BOT checkout $(cat /tmp/rollback_commit.txt)` + `restart` | none if pre-Step-4 |
| **R-ALL** | Abort before Step 4 ran | `sudo -u btcbot git -C $BOT checkout $(cat /tmp/rollback_commit.txt)` + `restart btcbot` | none |

> **Governance is NEVER rolled back by mutation (ADR R2/R5).** Nothing here
> touches `ledger.db`. If any rollback appears to require editing the ledger —
> **STOP and escalate to Rohith.** Restore-from-backup only, never UPDATE/DELETE.
> **ATR rows:** never purge `status='ok'` rows — that is evidence destruction.

---

## STEP 10 — 24-hour observation checklist

### T+90 min
- [ ] §16 items 1–6 all pass.
- [ ] `journalctl -u btcbot.service --since "90 min ago"` — no `ERROR`/`Traceback`.
- [ ] ≥1 new `collection_metrics` cycle; `coverage_pct` matches the 0e baseline.

### T+24 h — the real gates

**ATR Phase E** (requires one *unattended* 01:00 UTC cycle — a manual start is not sufficient):
```bash
journalctl -u btcbot-fwd-hg.service -n 30 --no-pager | tail -20
sudo -u btcbot sqlite3 -header -column "$EVDB" \
 "SELECT signal_id, run_date, status, substr(fn_hash,1,8) fn8
  FROM run_log WHERE signal_id='g3_atr_expansion' ORDER BY run_date DESC LIMIT 3;"
sudo -u btcbot sqlite3 -header -column "$EVDB" \
 "SELECT signal_id, MAX(run_date) latest, status FROM run_log GROUP BY signal_id ORDER BY signal_id;"
```
- [ ] `status='ok'`, `fn8='acaf70e9'`, **all five** signals at the same latest `run_date`, all `ok`.

**Certifier first unattended run:**
```bash
journalctl -u btcbot-certify.service --since "24 hours ago" --no-pager
sudo -u btcbot sqlite3 -header -column $BOT/data/btc_trading.db \
 "SELECT day, verdict, platform FROM daily_certifier_log ORDER BY day DESC LIMIT 5;"
```
- [ ] A verdict row exists for yesterday. **AMBER is expected** on testnet (C7).

**Runtime unchanged (§16 item 7 / INV-5):**
- [ ] `coverage_pct` and `reconcile_count` statistically unchanged vs the 0e baseline.

**Observability plane:**
- [ ] All Prometheus targets `up` (incl. btcbot exporter).
- [ ] Grafana reachable via SSH tunnel; **admin password rotated** after first login.

### T+24 h to T+7 d — ADR D7 obligation
- [ ] **Re-measure the §13 runtime constants** across at least one 12:00 UTC
  settlement rollover (Step 8 perturbed the runtime process; the constants are
  budgets that can be invalidated silently). Update CLAUDE.md §13 if any moved.
- [ ] Watch Python RSS growth over 24h+ (exporter adds a thread + listener).
- [ ] Watch `btc_trading.db` growth (new index adds per-row storage).

---

## Deployment complete when

All §16 items pass (incl. item-7 24h), `--check` clean and chain valid
(26 receipts) before **and** after, heartbeat `ok=5/5 fn_hash=ok gov=ok`,
ATR Phase E signed off, a certifier verdict row from an unattended run,
`user_version=5` with `SEARCH` plan, all targets `up`, API loopback-only and
authenticated, `ss -tlnp` shows no public bind except `:22`, coverage/reconcile
unchanged, and the §13 constants re-measured.

---

## Explicitly NOT part of this deployment
- **BK3 off-host backup enablement** — separate gated sequence, `docs/ops/offhost-backup.md §8`.
- **Proposal 1 activation** — inert until a real Validated promotion; nothing to do.
- **Any Phase 5/6 work** — gated, not in scope.
