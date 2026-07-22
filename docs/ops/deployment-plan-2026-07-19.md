# Production Deployment Plan — Phases 2–4, ENG-017, ATR, Certifier

**Status: PLAN ONLY. Not executed. No deployment performed.**

Prepared 2026-07-19 as Item 4 of the Platform Engineering Readiness Review.
Governs the deployment of everything currently uncommitted on `main` to the
production host (`ubuntu@3.110.103.217`, `/opt/btcbot`).

Authoritative references, not superseded by this document:
`docs/adr/0001-infrastructure-architecture.md` (§13 ordering/rules, §14
rollback, §16 verification block, §17 invariants), `docs/runbook.md`,
`deploy/install.md`, `research/forward_evidence/ATR_ACTIVATION_RUNBOOK.md`,
`docs/ops/atr-activation-card-2026-07-19.md`.

---

## 0. Scope correction — read this first

The task names "Phases 3 and 4." **Phase 2 (the in-process metrics exporter)
is also undeployed and cannot be separated from Phase 3.**

`src/observability/metrics_exporter.py`, and its wiring in
`config/settings.py`, `src/main.py`, `src/runtime/lifecycle.py`, and
`src/alerts/heartbeat.py`, are all uncommitted. Prometheus (Phase 3) exists
to scrape that exporter on `127.0.0.1:9108`. Deploying Phase 3 without
Phase 2 produces a stack whose primary scrape target is permanently
`up == 0` — an observability plane that observes nothing, plus a standing
false alert.

This matters beyond bookkeeping: **Phase 2 is the only runtime code change
in this deployment.** It modifies the bootstrap sequence, component start
order, and drain order of the live 24/7 writer. Under ADR §13.2 that makes
it the highest-risk item and it goes **last**, not second. The ADR's own
ordering table lists "Exporter" at step 2 and "Runtime code changes" at
step 5; for this deployment those are the same change, and the plan resolves
the ambiguity toward the stricter reading (last).

**Total scope: Phases 2, 3, 4 + ENG-017 migration + ATR activation +
certifier scheduling.**

---

## 1. Prerequisites

### 1.1 Gate — must be true before any host command

| # | Prerequisite | How to confirm | Blocking? |
|---|---|---|---|
| P1 | All work committed and pushed to `main` | `git status --porcelain` empty locally | **Yes** — nothing is deployable until it is committed |
| P2 | CI green on that commit (ADR D1, no manual override) | GitHub Actions run for the merge commit | **Yes** |
| P3 | `report.py --check` clean **locally** on the deploy commit | exit 0 | **Yes** (INV-4) |
| P4 | `receipt_ledger.verify_chain()` valid locally | `ok=True` | **Yes** (INV-4) |
| P5 | Full test suite passes | expect **1303 passed**, 2 known date-dependent expiry failures | **Yes** |
| P6 | Phase 4 gate confirmed open | ADR §19: "Phases 4–6 … require an explicit gate from Rohith" | **Yes** — Rohith authorised Phase 4 implementation; confirm that extends to production deployment |
| P7 | Restore drill complete | `docs/ops/restore-drill-2026-07-19.md` | Done (Item 3) |

**Note on P1.** Everything in this deployment is currently *uncommitted
working-tree state* (31 paths). This plan assumes a normal
commit → push → CI → pull flow. Deploying by copying uncommitted files to
the host is explicitly out of scope and would violate D1 and R1
(revertibility).

### 1.2 Host facts to establish before scheduling the window

These determine the maintenance window length and cannot be answered
off-host. Collect all of them in one read-only session **before** the deploy
day.

```bash
# [VERIFY ON HOST] — read-only reconnaissance, safe to run any time
ssh -i "$KEY" ubuntu@3.110.103.217

# a. Current schema level — determines how many migration steps run
sudo -u btcbot sqlite3 /opt/btcbot/data/btc_trading.db "PRAGMA user_version;"

# b. option_quotes row count — determines ENG-017 index build time
sudo -u btcbot sqlite3 /opt/btcbot/data/btc_trading.db \
    "SELECT COUNT(*) FROM option_quotes;"

# c. DB size and free disk — index needs transient space + the backup needs room
ls -lh /opt/btcbot/data/btc_trading.db
df -h /opt/btcbot

# d. Current deployed commit — the rollback target
sudo -u btcbot git -C /opt/btcbot rev-parse HEAD

# e. Ledger backups actually exist (restore-drill recommendation 1)
ls -lh /opt/btcbot/data/ledger-backups/ | tail -5

# f. Confirm ATR is still disabled on the host (pre-pull state)
grep -n "enabled:" /opt/btcbot/research/forward_evidence/collection_config.yaml

# g. Python/venv layout for the API's separate venv
ls -d /opt/btcbot/venv /opt/btcbot/venv-api 2>/dev/null
```

**Index build sizing.** `migrate.py` reports the 4→5 index step as
"~4s per 3M rows, holds a write lock". Multiply (b) accordingly. The live
runtime writes to this DB every 60s with `busy_timeout=5000` — **if the
index build exceeds ~5s, concurrent SnapshotWriter writes will fail.** This
is the single reason a maintenance window is required at all (§5).

### 1.3 Artefacts that must exist on the host before their phase runs

| Phase | Artefact | Action |
|---|---|---|
| 3 | `deploy/compose/.env` with a real `GRAFANA_ADMIN_PASSWORD` | Create from `.env.example`; gitignored; **never** reuse `/opt/btcbot/.env` (S2/S3) |
| 3 | Docker + Compose plugin installed | `docker --version && docker compose version` |
| 4 | `/opt/btcbot/venv-api` | Create separate venv; `pip install -r requirements-api.txt` |
| 4 | `API_TOKEN` in `/opt/btcbot/.env` | Generate: `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Service refuses to start if unset or too short (by design) |
| 2 | *(none)* | Uses existing `/opt/btcbot/venv`; `requirements.txt` gained one line — reinstall before restart |

---

## 2. Deployment order

Derived from ADR §13.2, with two deviations forced by this deployment's
specific content, both justified inline.

```
S0  Pre-flight            read-only checks, backups            no risk
S1  ATR Phase A+B         on-host backup + verify (PRE-PULL)   no risk   ← hazard fix
S2  git pull              code+config to host, nothing restarted  low
S3  ATR Phase D           dry-run against the pulled config    no risk   ← hazard fix
S4  ENG-017 migration     schema 0/4 → 5   [WINDOW, bot stopped]  MEDIUM
S5  Certifier units       install, enable timer                low
S6  Observability plane   docker compose up -d                 low
S7  API service           new unit, additive                   low
S8  Exporter / runtime    restart btcbot.service               HIGHEST
S9  Verification          ADR §16 seven-item block             —
S10 24h observation       coverage/reconcile baseline, ATR Phase E  —
```

### Why this order

- **S1/S3 straddle the pull (the ATR hazard fix).** See §3.
- **S4 before S5** — the certifier requires `user_version >= 4`
  (`daily_certifier_log`). Enabling the timer first guarantees a failing unit.
- **S4 before S8** — ADR D6: schema migrations are applied and verified
  *before* the dependent code is deployed. ENG-017's index is also what makes
  `healthcheck.py`'s `SELECT max(ts)` fast (512.8 ms → 0.01 ms), and
  healthcheck is used to verify every later step.
- **S6 before S7 before S8** — ADR §13.2 verbatim: observability plane
  cannot affect the runtime, API is additive, runtime restart is last.
- **S8 last** — the only `btcbot.service` restart in the entire deployment.
  Everything before it is either non-runtime or a stopped-bot window.

---

## 3. The ATR activation ordering hazard — how this plan defuses it

**The hazard.** `research/forward_evidence/collection_config.yaml` in the
repo now reads `enabled: true` (Item 1 set it, correctly, so a redeploy
cannot silently revert ATR). The consequence, stated in the activation card:
**`git pull` alone activates ATR**, bypassing the on-host Phase B/D
verification gates the runbook requires.

Nothing catches this automatically. The next 01:00 UTC `btcbot-fwd-hg` run
after the pull will simply start writing ATR rows into
`evidence_forward.db` — real forward-evidence rows, on a signal whose
`fn_hash` was never verified on that host. Because `run_log` is
append-only-by-convention and keyed `(signal_id, run_date)`, cleaning up bad
rows is a governance-adjacent `DELETE`, which the runbook permits **only**
for `fn_hash_violation`/`error` rows.

**The fix: split the activation card around the pull.**

| Card phase | Original position | This plan | Why |
|---|---|---|---|
| A — backup config | before flip | **S1, before pull** | Captures the genuine pre-activation `enabled: false` state. After the pull it is too late — the backup would capture `enabled: true` and be useless for rollback |
| B — verify `fn_hash` + governance on host | before flip | **S1, before pull** | Both are verifiable against the *old* checkout; `fn_hash` covers `decision_function_g3_atr_expansion.py`, unchanged by this deployment. Verifying before means a failure stops the deploy at zero cost |
| C — `sed` flip | manual edit | **deleted — the pull performs it** | Running `sed` on a file `git pull` is about to overwrite is redundant and creates a merge conflict |
| D — dry-run | after flip | **S3, immediately after pull** | This is the real gate. It runs against exactly the config that will be live, and it runs **before** any 01:00 UTC cycle can fire |
| E — sign-off | next day | **S10, unchanged** | Still requires one unattended timer-driven cycle |
| F — rollback | — | **available throughout** | `cp "$CFG.bak.$STAMP" "$CFG"` — see §6 |

**Hard scheduling constraint this creates:** the deployment must complete
S3 (ATR dry-run) **before the next 01:00 UTC `btcbot-fwd-hg` firing.**
Combined with D3's preferred 03:00–11:00 UTC window, deploying in the
morning UTC leaves ~14 hours of margin. **Do not deploy between 23:00 and
01:00 UTC** — that compresses the ATR gate to under an hour.

If S3's dry-run fails (`unavailable` or `error` for `g3_atr_expansion`),
**stop the deployment** and roll back the config per §6 before proceeding.
Per the activation card: on the host, `daily_ingest.py` keeps OHLCV current,
so `unavailable` there is a real failure, not the dev-corpus staleness seen
locally.

---

## 4. Host-side commands

Session setup, used throughout:

```bash
KEY=D:/My_paper_trading_bot/bitcoin-key.pem
ssh -i "$KEY" -o StrictHostKeyChecking=no ubuntu@3.110.103.217

# ON THE SERVER:
BOT=/opt/btcbot
CFG=$BOT/research/forward_evidence/collection_config.yaml
PY=/opt/miniconda3/envs/btc_bot/bin/python
VENV=$BOT/venv/bin/python
EVDB=$BOT/research/data/evidence_forward.db
STAMP=$(date -u +%Y%m%d)
```

### S0 — Pre-flight

```bash
# Record the rollback target — write this down, it is the rollback commit
sudo -u btcbot git -C $BOT rev-parse HEAD | tee /tmp/rollback_commit.txt

# Full backup of all four DBs (ADR D6 — before any schema change)
sudo -u btcbot $VENV $BOT/scripts/backup.py --all; echo "exit=$?"

# Verify the backups are real, not assumed (BK4)
ls -lh $BOT/data/backups/ $BOT/data/ledger-backups/ \
       $BOT/data/evidence-backups/ $BOT/data/evidence-fwd-backups/ | tail -20

# Governance baseline BEFORE (INV-4)
cd $BOT/research/evidence_library
sudo -u btcbot $PY report.py --check; echo "check_exit=$?"
sudo -u btcbot $PY -c "import receipt_ledger; print(receipt_ledger.verify_chain('ledger.db'))"

# Runtime baseline for the §16 item-7 comparison — record these numbers
sudo -u btcbot sqlite3 -header -column $BOT/data/btc_trading.db \
 "SELECT ts, universe_size, coverage_pct, reconcile_count, stale_count
  FROM collection_metrics ORDER BY ts DESC LIMIT 10;"

systemctl is-active btcbot.service
sudo -u btcbot $VENV $BOT/scripts/healthcheck.py --no-telegram; echo "hc_exit=$?"
```

### S1 — ATR Phase A + B (PRE-PULL, hazard fix)

```bash
# Phase A — backup the genuine pre-activation config
test -f "$CFG.bak.$STAMP" && echo "BACKUP EXISTS — not overwriting" || cp "$CFG" "$CFG.bak.$STAMP"
md5sum "$CFG" "$CFG.bak.$STAMP"      # expect two identical hashes

# Confirm pre-pull state is genuinely disabled
grep -n "enabled:" "$CFG"            # expect a single: 52:    enabled: false

# Phase B — verify fn_hash ON HOST (do not trust the off-host run)
sudo -u btcbot bash -c "cd $BOT/research && $PY -c \"from forward_evidence.pre_reg_guard import verify_fn_hash; import pathlib as P; b='forward_evidence/'; print('FNHASH OK:', verify_fn_hash(P.Path(b+'pre_registration_g3_atr_expansion.json'), P.Path(b+'decision_function_g3_atr_expansion.py'), 'g3_atr_expansion')[:8])\""
# expect: FNHASH OK: acaf70e9   — any other value => STOP
```

### S2 — Pull

```bash
sudo -u btcbot git -C $BOT fetch --all
sudo -u btcbot git -C $BOT pull --ff-only
sudo -u btcbot git -C $BOT rev-parse HEAD          # confirm the intended commit

# ATR is now ACTIVE in config. Confirm the pull did what was expected:
grep -n "enabled:" "$CFG"            # expect 52:    enabled: true
diff "$CFG.bak.$STAMP" "$CFG"        # expect exactly one hunk (false -> true)

# Nothing has restarted yet. btcbot.service is still running the OLD code.
systemctl is-active btcbot.service
```

### S3 — ATR Phase D (immediately after pull, hazard fix)

```bash
sudo -u btcbot bash -c "cd $BOT/research && $PY -m forward_evidence.run_daily --dry-run"; echo "exit=$?"
# expect: [dry-run] g3_atr_expansion ... position=<-1|0|1> (no DB write), exit 0
# 'unavailable' or 'error' for g3_atr_expansion => STOP, roll back per §6.1
```

### S4 — ENG-017 migration [MAINTENANCE WINDOW]

```bash
# Stop the writer. SIGTERM drain is preserved (D4) — never kill -9.
sudo systemctl stop btcbot.service
systemctl is-active btcbot.service            # expect: inactive

# Confirm WAL checkpointed cleanly
ls -lh $BOT/data/btc_trading.db*              # -wal should be absent or 0 bytes

# Dry-run first — shows exactly which steps will run
sudo -u btcbot $VENV $BOT/scripts/migrate.py --dry-run

# Apply
time sudo -u btcbot $VENV $BOT/scripts/migrate.py

# Verify
sudo -u btcbot sqlite3 $BOT/data/btc_trading.db "PRAGMA user_version;"        # expect 5
sudo -u btcbot sqlite3 $BOT/data/btc_trading.db "PRAGMA integrity_check;"     # expect ok
sudo -u btcbot sqlite3 $BOT/data/btc_trading.db \
 "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_option_quotes_ts';"

# Prove the index is actually used (SEARCH, not SCAN — the ENG-017 signal)
sudo -u btcbot sqlite3 $BOT/data/btc_trading.db \
 "EXPLAIN QUERY PLAN SELECT max(ts) FROM option_quotes;"
# expect a plan containing SEARCH / covering index — NOT 'SCAN option_quotes'

# Restart the writer (still OLD code — exporter comes at S8)
sudo systemctl start btcbot.service
sleep 90
sudo -u btcbot $VENV $BOT/scripts/healthcheck.py --no-telegram; echo "hc_exit=$?"
```

**Window ends here.** Everything after S4 is additive or a single further
restart.

### S5 — Certifier units

```bash
sudo cp $BOT/deploy/btcbot-certify.service /etc/systemd/system/
sudo cp $BOT/deploy/btcbot-certify.timer   /etc/systemd/system/
sudo systemctl daemon-reload

# Dry-run the wrapper BEFORE enabling the timer — proves schema + plan
sudo -u btcbot $VENV $BOT/scripts/certify_catchup.py --dry-run; echo "exit=$?"
# exit 3 => schema/DB problem, do NOT enable the timer
# exit 0 => prints the plan (to_certify / already / no_data)

sudo systemctl enable btcbot-certify.timer
sudo systemctl start  btcbot-certify.timer
systemctl list-timers btcbot-certify.timer --no-pager   # confirm next run 01:30 UTC

# Optional: one manual run to prove the path end-to-end
sudo systemctl start btcbot-certify.service
journalctl -u btcbot-certify.service -n 50 --no-pager
systemctl show btcbot-certify.service -p Result -p ExecMainStatus
# Result=success expected even for an AMBER/RED verdict (SuccessExitStatus=0 1 2)
```

### S6 — Observability plane (Phase 3)

```bash
cd $BOT/deploy/compose
test -f .env || sudo -u btcbot cp .env.example .env
sudo -u btcbot nano .env          # set a real GRAFANA_ADMIN_PASSWORD

sudo -u btcbot docker compose config >/dev/null && echo "COMPOSE CONFIG OK"
sudo -u btcbot docker compose up -d
sudo -u btcbot docker compose ps          # all healthy

curl -sf http://127.0.0.1:9090/-/healthy  && echo " prometheus OK"
curl -sf http://127.0.0.1:9093/-/healthy  && echo " alertmanager OK"
curl -sf http://127.0.0.1:3000/api/health && echo " grafana OK"
curl -sf http://127.0.0.1:9100/metrics >/dev/null && echo " node-exporter OK"

# EXPECTED at this point: the btcbot exporter target is DOWN.
# Phase 2 has not been deployed yet (S8). This is not a defect.
curl -s http://127.0.0.1:9090/api/v1/targets | grep -o '"health":"[a-z]*"' | sort | uniq -c
```

### S7 — API service (Phase 4)

```bash
# Separate venv — a FastAPI/pydantic upgrade must never alter the runtime's deps
sudo -u btcbot python3 -m venv $BOT/venv-api
sudo -u btcbot $BOT/venv-api/bin/pip install --upgrade pip
sudo -u btcbot $BOT/venv-api/bin/pip install -r $BOT/requirements-api.txt

# Token — the service refuses to start without a sufficiently long one
python3 -c "import secrets; print('API_TOKEN=' + secrets.token_urlsafe(32))"
sudo -u btcbot nano $BOT/.env            # append that line

sudo cp $BOT/deploy/btcbot-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable btcbot-api.service
sudo systemctl start  btcbot-api.service
systemctl is-active btcbot-api.service

TOKEN=$(sudo grep '^API_TOKEN=' $BOT/.env | cut -d= -f2-)
curl -sf http://127.0.0.1:9109/health && echo " api health OK"
curl -sf -H "Authorization: Bearer $TOKEN" http://127.0.0.1:9109/governance/verify | head -20
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:9109/governance/verify   # expect 401/403

# Loopback-only binding (ADR §16 item 6)
ss -tlnp | grep 9109        # expect 127.0.0.1:9109 — never 0.0.0.0
```

### S8 — Exporter + runtime code [the one restart]

```bash
# requirements.txt gained a line — install into the RUNTIME venv
sudo -u btcbot $BOT/venv/bin/pip install -r $BOT/requirements.txt

# Restart the live writer. SIGTERM drain, TimeoutStopSec=30 (D4).
sudo systemctl restart btcbot.service
sleep 120

systemctl is-active btcbot.service
journalctl -u btcbot.service -n 80 --no-pager | grep -iE "exporter|error|traceback"

# Exporter live and loopback-only
curl -sf http://127.0.0.1:9108/metrics | head -20
ss -tlnp | grep 9108        # expect 127.0.0.1:9108

# Prometheus target should now flip to up
sleep 60
curl -s http://127.0.0.1:9090/api/v1/targets | grep -o '"health":"[a-z]*"' | sort | uniq -c
```

**Kill switch, no redeploy required:** if the exporter misbehaves, set
`METRICS_EXPORTER_ENABLED=0` in `/opt/btcbot/.env` and restart. The bind
address is hardcoded to loopback in `metrics_exporter.py` and is not
env-settable, so no misconfiguration here can widen exposure.

---

## 5. Maintenance window requirements

### 5.1 Only S4 requires downtime

Every other step is additive (new units, containers, a separate venv) or a
single graceful restart. S4 requires the bot **stopped** for one specific
reason:

`migrate.py`'s 4→5 step builds `idx_option_quotes_ts` over the whole
`option_quotes` table, holding a write lock for ~4s per 3M rows. The live
`SnapshotWriter` writes every 60s with `busy_timeout=5000`. If the build
exceeds ~5s, concurrent writes fail — silently losing collection cycles and
corrupting the very `collection_metrics` record the certifier will grade.
Stopping the bot converts a data-loss risk into planned, bounded downtime.

### 5.2 Window sizing

| Item | Estimate |
|---|---|
| Stop + WAL checkpoint | ~30 s |
| `migrate.py` (index build) | **[VERIFY ON HOST]** — `rows / 3,000,000 × 4 s`; at the projected 30-day 2.85M rows ≈ **4 s**; at 10× that ≈ 40 s |
| Post-migration verification | ~60 s |
| Start + warmup to first tick | ~90–120 s |
| **Total bot downtime** | **~4 minutes** at current scale |
| **Total session (S0–S9)** | **60–90 minutes** |

### 5.3 Scheduling constraints (all must hold simultaneously)

| Constraint | Source | Rule |
|---|---|---|
| Settlement dead zone | CLAUDE.md §14, ADR D3 | **No deploy 11:58–12:06 UTC** |
| Evidence → fwd-hg ordering | ADR §15.4 | Avoid 00:05–01:00 UTC (hard ordering precondition) |
| Certifier | Item 2 | Avoid 01:30 UTC ±5 min |
| Backup | ADR BK5 | Avoid 02:00 UTC ±10 min |
| Mapsnap | existing | Avoid 12:30 UTC |
| ATR gate margin | §3 above | S3 must complete well before the next 01:00 UTC |

**Recommended window: 03:00–08:00 UTC.** Clear of every timer, after the
02:00 backup (so the freshest backup predates the change), and leaves ~17
hours before the next fwd-hg cycle for the ATR gate. This matches ADR D3's
stated preference of 03:00–11:00 UTC.

**Do not deploy Friday–Sunday** if the operator is not available for the
S10 24-hour observation and the ATR Phase E sign-off.

---

## 6. Rollback plan

Blast radius is bounded per component. Roll back the **narrowest** failing
component; a full rollback is rarely correct.

### 6.1 By component

| Failed at | Rollback | Time | Data risk |
|---|---|---|---|
| S3 ATR dry-run | `cp "$CFG.bak.$STAMP" "$CFG"` — no restart needed, the one-shot re-reads YAML each run | <1 min | none |
| S4 migration | **Restore from backup** — see 6.2 | 10–30 min | **real** |
| S5 certifier | `systemctl disable --now btcbot-certify.timer; rm /etc/systemd/system/btcbot-certify.{service,timer}; daemon-reload` | <1 min | none |
| S6 observability | `docker compose down` | <1 min | none — derived data only |
| S7 API | `systemctl stop btcbot-api` (optionally `disable`) | <1 min | none — read-only |
| S8 exporter | `METRICS_EXPORTER_ENABLED=0` in `.env` + restart (preferred), or `git checkout <rollback_commit>` + restart | <2 min | none |
| Whole deploy | `git -C $BOT checkout $(cat /tmp/rollback_commit.txt)` + restart btcbot | <5 min | none if S4 not yet run |

### 6.2 The one asymmetry — S4 is the point of no return

Everything before S4 rolls back by reverting a file. **S4 is the only step
whose rollback requires restoring data.** ENG-017's migration is additive
(one `CREATE INDEX`), and `migrate.py` guards on actual schema state rather
than `user_version`, so a partial run self-heals on re-run. But `PRAGMA
user_version` cannot be walked backward safely alongside a code rollback.

If S4's post-migration `integrity_check` fails:

```bash
sudo systemctl stop btcbot.service
sudo -u btcbot $VENV $BOT/scripts/restore.py \
    $BOT/data/backups/btc_trading_<S0_timestamp>.db --skip-service
sudo -u btcbot sqlite3 $BOT/data/btc_trading.db "PRAGMA integrity_check;"
sudo systemctl start btcbot.service
```

This procedure is **proven, not assumed** — exercised end-to-end in
`docs/ops/restore-drill-2026-07-19.md` (0.428 s for a 10.4 MB DB, integrity
and row counts verified).

### 6.3 Governance rollback is forbidden (ADR R2/R5)

Nothing in this deployment mutates `ledger.db`. If a rollback ever appears
to require touching the governance ledger: **stop and escalate to Rohith.**
The ledger is append-only by DB trigger; recovery is restore-from-backup
only. Never `UPDATE`/`DELETE`. No autonomous governance-state repair.

### 6.4 ATR rows are a special case

If ATR rows were written before a problem was noticed, the runbook's
conditional-recovery `DELETE` applies **only** to rows with
`fn_hash_violation` or `error` status. **Never purge ATR rows with
`status='ok'`** — those are legitimate forward evidence, and deleting them
is evidence destruction, not rollback.

---

## 7. Production verification checklist

### 7.1 ADR §16 seven-item block (mandatory, run after S8)

| # | Check | Command | Expected |
|---|---|---|---|
| 1 | Runtime active | `systemctl is-active btcbot.service` | `active` |
| 2 | **Heartbeat canary** | Telegram message | `fwd-evid : YYYY-MM-DD ok=5/5 fn_hash=ok gov=ok` |
| 3 | Governance check | `report.py --check` | clean, exit 0 |
| 4 | Chain valid | `receipt_ledger.verify_chain()` | `ok=True, n_receipts=26` |
| 5 | Healthcheck | `scripts/healthcheck.py` | OK |
| 6 | No public bind | `ss -tlnp` | no `0.0.0.0` except `:22` |
| 7 | Coverage/reconcile unchanged | vs. S0 baseline | 24h observation |

**Item 2 is the highest-value single check in this deployment.** It is
`ok=5/5`, not `4/5` — the 5th signal is ATR, so this line simultaneously
confirms ATR activation, `fn_hash` integrity, governance health, and the
sandbox/filesystem/WAL layers. It is the cheapest end-to-end assertion
available and it is not ceremonial.

### 7.2 Deployment-specific additions

| Area | Check | Expected |
|---|---|---|
| ENG-017 | `PRAGMA user_version` | `5` |
| ENG-017 | `EXPLAIN QUERY PLAN SELECT max(ts) FROM option_quotes` | `SEARCH`, not `SCAN` |
| ENG-017 | healthcheck latency | sub-millisecond (was 512.8 ms) |
| Certifier | `systemctl list-timers btcbot-certify.timer` | next run 01:30 UTC |
| Certifier | `certify_catchup.py --dry-run` | exit 0 |
| Phase 3 | `docker compose ps` | all healthy |
| Phase 3 | Prometheus targets | all `up` **after** S8 |
| Phase 4 | `curl 127.0.0.1:9109/health` | 200 |
| Phase 4 | unauthenticated request | 401/403 |
| Phase 4 | `ss -tlnp \| grep 9109` | `127.0.0.1` only |
| Phase 2 | `curl 127.0.0.1:9108/metrics` | metrics render |
| Phase 2 | `ss -tlnp \| grep 9108` | `127.0.0.1` only |
| ATR | `run_log` next day | `status='ok'`, `fn8='acaf70e9'` |

---

## 8. Post-deployment validation (T+1 to T+7)

### T+90 min
- §16 items 1–6 all pass.
- No `ERROR`/`Traceback` in `journalctl -u btcbot.service` since restart.
- `collection_metrics` shows at least one new cycle with `coverage_pct`
  matching the S0 baseline.

### T+24 h — the real gates

**ATR Phase E sign-off** (requires one *unattended* timer-driven 01:00 UTC
cycle — a manual `systemctl start` is an in-window smoke test and is **not**
sufficient):

```bash
journalctl -u btcbot-fwd-hg.service -n 30 --no-pager | tail -20
sudo -u btcbot sqlite3 -header -column "$EVDB" \
 "SELECT signal_id, run_date, status, substr(fn_hash,1,8) fn8
  FROM run_log WHERE signal_id='g3_atr_expansion' ORDER BY run_date DESC LIMIT 3;"
sudo -u btcbot sqlite3 -header -column "$EVDB" \
 "SELECT signal_id, MAX(run_date) latest, status FROM run_log
  GROUP BY signal_id ORDER BY signal_id;"
```
Expect `status='ok'`, `fn8='acaf70e9'`, and **all five** signals at the same
latest `run_date`, all `ok`.

**Certifier first unattended run:**
```bash
journalctl -u btcbot-certify.service --since "24 hours ago" --no-pager
sudo -u btcbot sqlite3 -header -column $BOT/data/btc_trading.db \
 "SELECT day, verdict, platform FROM daily_certifier_log ORDER BY day DESC LIMIT 5;"
```
A verdict row must exist for yesterday. **AMBER is expected** while
`PLATFORM=testnet` (check C7 marks all testnet data AMBER by policy) — a
green unit does not mean a green day.

**§16 item 7 — coverage/reconcile unchanged** vs. the S0 baseline. This is
the ADR's INV-5 assertion that runtime behaviour is untouched by
infrastructure work.

### T+24 h to T+7 d — ADR D7 obligation

> **D7.** §13 runtime constants must be **re-measured** after any change that
> perturbs the runtime process.

S8 perturbs the runtime process (new component in bootstrap, start, and
drain order). The §13 constants — REST round-trip, trigger→stable universe,
rollover first-tick latency, `mark_price` cadence, residual streaming — are
**budgets derived from a specific process configuration and can be
invalidated silently.** Re-measure across at least one 12:00 UTC settlement
rollover and update CLAUDE.md §13 if any constant has moved.

Also observe: Python RSS growth over 24h+ (the exporter adds a thread and an
HTTP listener — CLAUDE.md §15 observation 3), and `btc_trading.db` growth
rate now that the new index adds storage per row.

---

## 9. Success criteria

The deployment is **complete** when all of the following hold:

1. All seven ADR §16 items pass, including item 7's 24-hour observation.
2. `report.py --check` clean and `verify_chain()` valid — both **before and
   after** (INV-4). Receipt count unchanged at 26: this deployment creates no
   governance decisions.
3. Telegram heartbeat renders `ok=5/5 fn_hash=ok gov=ok`.
4. ATR Phase E signed off against an unattended 01:00 UTC cycle, with all
   five signals at the same `run_date`, all `ok`, `fn8=acaf70e9`.
5. `daily_certifier_log` has a verdict row from an unattended 01:30 UTC run.
6. `user_version = 5`; `max(ts)` plan shows `SEARCH`; healthcheck
   sub-millisecond.
7. All Prometheus targets `up`; Grafana reachable via SSH tunnel with a
   rotated admin password; anonymous access off.
8. API serves authenticated reads on `127.0.0.1:9109`, rejects
   unauthenticated ones, and **`btcbot.service` was unaffected by every API
   start/stop** during the session.
9. `ss -tlnp` shows no `0.0.0.0` binding except `:22`.
10. Coverage and reconcile counts statistically unchanged vs. the S0 baseline
    (INV-5).
11. §13 constants re-measured (D7) and CLAUDE.md updated if any moved.

## 10. Failure criteria — stop and roll back

**Abort immediately, do not continue to the next step:**

| Trigger | Step | Action |
|---|---|---|
| `fn_hash != acaf70e9` on host | S1 | **Stop before pulling.** Pre-registration integrity failure — escalate, do not deploy |
| `report.py --check` non-zero at baseline | S0 | **Stop.** Never deploy onto a governance-failing ledger |
| ATR dry-run `unavailable`/`error` | S3 | Roll back config (6.1); do not proceed to S4 |
| `integrity_check` != ok post-migration | S4 | Restore from backup (6.2) |
| `user_version` != 5 after migrate | S4 | Stop; investigate before restarting the bot |
| `btcbot.service` fails to start | S4/S8 | Roll back code, restart, verify; escalate if it persists |
| `certify_catchup.py --dry-run` exit 3 | S5 | Do not enable the timer; fix schema first |
| Any service binds `0.0.0.0` | S6/S7/S8 | **Immediate stop of that service.** Security invariant breach |
| Coverage drops materially vs. baseline | S8/T+24h | Roll back the exporter (`METRICS_EXPORTER_ENABLED=0`); INV-5 breach |
| Heartbeat shows `ok=4/5` or `fn_hash!=ok` | T+24h | Investigate ATR; roll back config per 6.1 if `fn_hash_violation` |
| Rollback appears to require touching `ledger.db` | any | **STOP. Escalate to Rohith.** (R5) |

**Degraded-but-acceptable — do NOT roll back for these:**

- Prometheus exporter target `down` between S6 and S8 — expected by design.
- Certifier verdict AMBER — expected on testnet (C7); a verdict is a success.
- `healthcheck.py` `backup` WARNING immediately post-deploy if the window
  crossed 02:00 — resolves on the next backup.
- Grafana dashboards empty for the first minutes — Prometheus needs scrape
  history.

---

## 11. Open items to resolve before executing

1. **[VERIFY ON HOST]** all of §1.2 — especially `option_quotes` row count,
   which is the only real input to the window length.
2. **[VERIFY ON HOST]** that `/opt/btcbot/data/ledger-backups/` is populated
   (restore-drill recommendation 1). `backup.py --all` in the unit covers it,
   but this has never been confirmed on the host.
3. **Confirm P6** — Rohith's Phase 4 gate covers production deployment, not
   just implementation (ADR §19).
4. **BK3 remains open** — off-host backup is Item 5, not this deployment. All
   backups taken during this deploy are local-only and do not survive VPS
   loss. Worth accepting knowingly for one deployment; worth closing soon.
5. **Confirm the Grafana admin password** is rotated after first login (S3
   in the ADR's security rules), not left at the `.env` bootstrap value.
