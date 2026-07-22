# ADR-0001 — Infrastructure Architecture

| Field | Value |
|---|---|
| Status | **Accepted** — implementation contract |
| Date | 2026-07-18 |
| Supersedes | — |
| Depends on | Approved infrastructure roadmap (Phases 0–6) |
| Governs | All subsequent infrastructure work |
| Owner | Rohith |

---

## 1. Context

The platform today is a single-VPS, bare-systemd deployment: one long-lived
process (`btcbot.service`) plus five timer-driven one-shots, all running as a
non-root `btcbot` account under `ProtectSystem=strict` with explicit
`ReadWritePaths=` grants. State is SQLite in WAL mode. There is no HTTP
surface, no CI, and no metrics retention beyond journald and the Telegram
heartbeat.

Three properties of the existing system constrain every decision below:

1. **The governance ledgers are the highest-value, lowest-migratability asset.**
   `ledger.db` enforces append-only semantics through DB-level
   `RAISE(ABORT)` triggers; receipts are hash-chained with byte-identical
   canonical serialization (`sha256_lf`). Bootstrap migrations have been run
   once and are documented as non-re-runnable.
2. **Data volume does not justify new datastores.** `btc_trading.db` is 11 MB
   / ~40 k `option_quotes` rows. Ledgers are 168 KB–584 KB.
3. **The systemd hardening model is operationally proven, and its failure mode
   is subtle.** The 2026-07-18 heartbeat outage was caused by a missing
   `ReadWritePaths=/opt/btcbot/research/data` grant: a WAL reader must
   open/lock `-wal`/`-shm` siblings, requiring *directory write access* even
   at `mode=ro`. Innocent code, innocent data, guilty environment.

The platform is additionally in the CLAUDE.md §15 stabilization phase, where
operational intelligence is explicitly preferred over new architecture.

---

## 2. Decision

We adopt a **hybrid host/container topology** governed by one invariant:

> ### INV-1 — The Data Plane Boundary
> **No container may read or write project data.** Containers consume HTTP
> endpoints only. Every process that touches SQLite, Parquet, `.env`, or the
> governance ledgers runs on the host under systemd.

This is the load-bearing decision of this ADR. It is what makes
containerization safe to adopt at all: it removes bind-mounted SQLite, WAL
locking over overlay filesystems, container-UID/host-UID ownership mismatch,
and re-derivation of the `ProtectSystem`/`ReadWritePaths` sandbox from the
risk surface entirely — the exact class of defect that caused the last
production incident.

Containerization buys reproducibility for stateless, off-the-shelf
observability components. It buys nothing for a stateful WS runtime on a
single VPS. We take the benefit and decline the risk.

### Consequences

- **Accepted:** two deployment mechanisms (systemd + Compose) instead of one.
  Justified: the boundary between them is a bright line (data plane vs.
  observability plane), not a judgement call per service.
- **Accepted:** the bot is not portable to a container orchestrator without a
  future ADR. Correct for a single-VPS, single-operator system.
- **Rejected:** containerizing `btcbot.service`. Revisit only on a genuine
  multi-node requirement.
- **Rejected:** Redis. No queue, no cache pressure, no fanout, no shared
  session state exists. See §14.
- **Deferred:** PostgreSQL. Conditional on measured trigger evidence, and
  scoped to operational telemetry only — never governance. See §13.

---

## 3. Target Production Architecture

```
┌─────────────────────────── VPS (Ubuntu 22.04) ────────────────────────────┐
│                                                                            │
│  HOST / systemd — DATA PLANE (owns all state)                             │
│  ┌──────────────────────────────────────────────────────────────────┐    │
│  │ btcbot.service          long-lived: WS, scheduler, heartbeat     │    │
│  │   └─ metrics exporter   in-process, binds 127.0.0.1:9108         │    │
│  │ btcbot-api.service      read-only FastAPI, 127.0.0.1:9109        │    │
│  │                                                                   │    │
│  │ timers (one-shot):                                                │    │
│  │   btcbot-evidence     00:05 UTC   corpus refresh + evidence job   │    │
│  │   btcbot-fwd-hg       01:00 UTC   H5/G1/G3/ivrv collection        │    │
│  │   btcbot-backup       02:00 UTC   VACUUM INTO + SHA-256 manifest  │    │
│  │   btcbot-mapsnap      12:30 UTC   instrument map snapshot         │    │
│  │   btcbot-healthcheck  */10 min    independent dead-man            │    │
│  │                                                                   │    │
│  │ STATE:  /opt/btcbot/data/            (SQLite WAL, logs, backups)  │    │
│  │         /opt/btcbot/research/data/   (evidence DBs, Parquet)      │    │
│  │         /opt/btcbot/research/evidence_library/ledger.db           │    │
│  │         /opt/btcbot/.env             (0600, root:btcbot)          │    │
│  └──────────────────────────────────────────────────────────────────┘    │
│                    ▲ scrape (HTTP, loopback only)                         │
│  ══════════════════╬══════════ INV-1 boundary ═══════════════════════     │
│                    │  no volume crosses this line                         │
│  CONTAINERS / Compose — OBSERVABILITY PLANE (no project data)             │
│  ┌──────────────────────────────────────────────────────────────────┐    │
│  │ prometheus    scrapes :9108 :9109 :9100    TSDB (named volume)    │    │
│  │ alertmanager  → Telegram (existing bot)    config only            │    │
│  │ grafana       dashboards-as-code           named volume           │    │
│  │ node_exporter host metrics                 network_mode: host     │    │
│  └──────────────────────────────────────────────────────────────────┘    │
│                                                                            │
│  EXTERNAL: Delta Exchange (WS/REST) · Binance/Deribit (research) ·        │
│            Telegram · off-host backup target                              │
└────────────────────────────────────────────────────────────────────────────┘

Operator access: SSH only. No port published to 0.0.0.0. Grafana via tunnel.
```

---

## 4. Service Boundaries

Four planes. A component belongs to exactly one.

| Plane | Owns | Members | May write project data |
|---|---|---|---|
| **Runtime** | live market state, quote capture | `btcbot.service` | yes (`data/`) |
| **Research** | evidence collection, governance | evidence/fwd-hg timers, CLI tools | yes (`research/data/`, ledger) |
| **Operations** | backup, health, snapshots | backup/healthcheck/mapsnap timers | yes (backups only) |
| **Observability** | metrics, dashboards, alerts | exporter, API, Prometheus, Grafana, Alertmanager | **never** |

### Binding boundary rules

- **B1.** Observability is strictly read-only, and read-only by *construction*
  (no write path exists), not by convention.
- **B2.** The API plane exposes **no governance mutation**. No promote, no
  transition, no admit/reject over HTTP. Governance mutations remain
  CLI-and-human-gated. This preserves the PR-8 property that a service actor
  is refused — the platform must not promote itself.
- **B3.** Research and Runtime planes never share a process. They already run
  different Python versions (3.10 conda vs. 3.11 venv); this ADR freezes that
  separation as intentional.
- **B4.** `healthcheck.py` remains an **independent dead-man** and is not
  replaced or subsumed by Prometheus. Two independent detection paths with
  different failure modes is the point; a monitoring stack that can fail
  silently alongside the thing it monitors is not a dead-man.

---

## 5. Docker Strategy

### 5.1 What is containerized

| Image | Source | Rationale |
|---|---|---|
| `prom/prometheus` | upstream, pinned digest | stateless, config-driven |
| `grafana/grafana` | upstream, pinned digest | stateless, provisioned as code |
| `prom/alertmanager` | upstream, pinned digest | stateless |
| `prom/node-exporter` | upstream, pinned digest | host metrics |

**No first-party image is built for production.** Every container is an
upstream artifact pinned by **digest, not tag** (`image: prom/prometheus@sha256:…`).
Tag-pinning is insufficient: tags are mutable and a silent upstream retag is
an unreviewed production change.

### 5.2 What is not containerized, and why

| Component | Reason |
|---|---|
| `btcbot.service` | stateful WS + SQLite WAL; systemd sandbox is proven; containerizing re-derives it |
| research timers | conda env + Parquet + ledger writes; INV-1 |
| `btcbot-api.service` | needs WAL read access to ledgers; containerizing reintroduces the directory-write-access bug class |
| backup / healthcheck | must observe the host and its filesystem directly |

### 5.3 Research image (CI only)

`Dockerfile.research` (Python 3.10 + pinned conda set) is built **for CI
reproducibility only** and is never deployed to production. It exists so
corpus builds are byte-reproducible in CI; it has no production role.

### 5.4 Build rules

- `.dockerignore` **must** exclude `.env`, `data/`, `research/data/`,
  `research/evidence_library/`, `*.db`, `__pycache__/`.
- Images run as non-root with an explicit `user:` in Compose.
- No `latest`. No mutable tags. No build-time secrets, no secret ARGs.

---

## 6. Compose Topology

Single file, `deploy/compose/docker-compose.yml`, scope: observability plane only.

```
services:
  node-exporter   network_mode: host          no deps
  prometheus      depends_on: node-exporter   volume: prom-data
  alertmanager    depends_on: none            config bind (ro)
  grafana         depends_on: prometheus      volume: grafana-data

networks:
  obs             bridge, internal to the stack

volumes:
  prom-data       named, docker-managed
  grafana-data    named, docker-managed
```

### Rules

- **C1.** All four use `restart: unless-stopped`.
- **C2.** No service publishes to `0.0.0.0`. Bindings are `127.0.0.1:PORT:PORT`.
- **C3.** No bind mount from `/opt/btcbot/data`, `/opt/btcbot/research`, or any
  path containing project state. Config-only bind mounts, mounted `:ro`.
- **C4.** Compose lifecycle is independent of the bot: `docker compose down`
  must never affect trading runtime or evidence collection. Losing
  observability degrades visibility, not operation.
- **C5.** Resource limits are mandatory (`mem_limit`, `cpus`) so the
  observability plane cannot starve the runtime on a 1–2 GB VPS.

---

## 7. Volume Layout

### 7.1 Host (authoritative state — never in a container)

| Path | Contents | Owner | Mode | Backed up |
|---|---|---|---|---|
| `/opt/btcbot/` | source tree | `btcbot:btcbot` | 0755 | git |
| `/opt/btcbot/.env` | secrets | `root:btcbot` | **0640** | **manual, off-host** |
| `/opt/btcbot/data/` | `btc_trading.db` (WAL), logs | `btcbot:btcbot` | 0750 | yes |
| `/opt/btcbot/data/backups/` | rotated backups + manifests | `btcbot:btcbot` | 0750 | off-host |
| `/opt/btcbot/research/data/` | `evidence_forward.db`, `forward_evidence.db`, Parquet | `btcbot:btcbot` | 0750 | yes |
| `/opt/btcbot/research/evidence_library/ledger.db` | **governance ledger** | `btcbot:btcbot` | 0640 | yes, highest priority |

### 7.2 Container volumes (reconstructible — deliberately not backed up)

| Volume | Service | Retention | Backed up |
|---|---|---|---|
| `prom-data` | prometheus | 90d, `--storage.tsdb.retention.size` capped | **no** |
| `grafana-data` | grafana | — | **no** (dashboards provisioned from git) |

Both are derived data. Prometheus TSDB is re-observable; Grafana state is
declared in the repo. Backing them up would consume the same 25–80 GB disk
that holds the corpus, for no recovery value.

### 7.3 WAL constraints (binding — derived from the 2026-07-18 incident)

- **V1.** Every service unit that *reads* a WAL database must list its
  containing directory in `ReadWritePaths=`, even for `mode=ro` access. WAL
  readers open and lock `-wal`/`-shm` siblings, which requires directory write
  permission regardless of SQL-level mode.
- **V2.** No WAL database may reside on a network, overlay, or
  container-managed filesystem. WAL requires working POSIX `fcntl` locking.
- **V3.** Adding a new unit that reads `research/data/` requires an explicit
  `ReadWritePaths` review. Default-deny.
- **V4.** The heartbeat line `fwd-evid: … ok=5/5 fn_hash=ok gov=ok` is the
  designated **canary** for this failure class — it transits the sandbox,
  filesystem, and WAL layers in one assertion. It must be verified after every
  infrastructure change.

---

## 8. Networking

| Interface | Bind | Exposure |
|---|---|---|
| bot metrics exporter | `127.0.0.1:9108` | loopback |
| read-only API | `127.0.0.1:9109` | loopback |
| node_exporter | `127.0.0.1:9100` | loopback |
| prometheus | `127.0.0.1:9090` | loopback |
| alertmanager | `127.0.0.1:9093` | loopback |
| grafana | `127.0.0.1:3000` | loopback |
| SSH | `0.0.0.0:22` | **only public port**, key-only |

### Rules

- **N1.** No service is reachable from the public internet. Operator access to
  Grafana is via SSH tunnel (`ssh -L 3000:127.0.0.1:3000`). No reverse proxy,
  no TLS termination, no public dashboard in this ADR.
- **N2.** UFW default-deny inbound; allow 22 only. Docker bypasses UFW by
  default via its iptables chain — Compose port bindings **must** be written
  as `127.0.0.1:PORT:PORT` so the bypass is never exercised. Verify with
  `ss -tlnp` after every stack change; a bare `PORT:PORT` is a public exposure.
- **N3.** Egress required: Delta (WS/REST), Binance/Deribit (research),
  Telegram, backup target, distro/PyPI mirrors during maintenance.
- **N4.** Prometheus reaches host exporters via `host.docker.internal` mapped
  through `extra_hosts: host-gateway`. This is an HTTP path only — it does not
  breach INV-1.

---

## 9. Secrets Management

| Secret | Location | Consumer | Rotation |
|---|---|---|---|
| `DELTA_API_KEY` / `_SECRET` | `/opt/btcbot/.env` (0640 `root:btcbot`) | bot | manual, Delta console |
| `TELEGRAM_BOT_TOKEN` / `CHANNEL_ID` | same `.env` | bot, healthcheck, alertmanager | manual, BotFather |
| `API_TOKEN` (new) | same `.env` | `btcbot-api` | manual |
| `GF_SECURITY_ADMIN_PASSWORD` | `deploy/compose/.env.grafana` (0600) | grafana | manual |
| `BACKUP_OFFHOST_CMD` + SSH key | `.env` + `~btcbot/.ssh/` (0600) | backup timer | manual |

### Rules

- **S1.** `.env` is loaded by systemd `EnvironmentFile=` or `python-dotenv`.
  It is never copied into an image, never in Compose YAML, never in git,
  never in a log line, never in a receipt, never in chat, never in memory.
- **S2.** Alertmanager needs the Telegram token but must not read
  `/opt/btcbot/.env` (INV-1). It gets a **separate, minimal** env file
  containing only the token and chat ID — least privilege, and it keeps the
  data-plane secret file out of container reach.
- **S3.** Grafana admin password is set on first boot and rotated immediately.
  Anonymous access disabled; sign-up disabled.
- **S4.** No secret is a Docker build ARG or image layer.
- **S5.** `.gitignore` must cover `.env`, `.env.*`, `*.key`, `*.pem`.
  `.env.example` and `.env.grafana.example` carry placeholders only.
- **S6.** Secret rotation is manual and documented in the runbook. Automated
  rotation is out of scope.

---

## 10. Logging

**Decision: journald remains the authoritative log store. No log-shipping
stack (Loki/ELK) is adopted.**

| Source | Destination | Retention |
|---|---|---|
| `btcbot.service` | journald (`SyslogIdentifier=btcbot`) + `data/logs/btc_bot.jsonl` | journald rotation; JSONL rotating handler |
| timer one-shots | journald, per-unit identifiers | journald rotation |
| containers | `json-file` driver, `max-size: 10m`, `max-file: 3` | capped |

### Rules

- **L1.** Existing dual-sink logging (`_UTCFormatter` console → journald,
  `_JsonlFormatter` → file) is preserved unchanged.
- **L2.** `journald` must be configured with explicit
  `SystemMaxUse=` — uncapped journal growth on a 25–80 GB disk shared with the
  corpus is a real outage mode.
- **L3.** Container logging **must** be size-capped. An uncapped json-file
  driver is the most common single-VPS disk-exhaustion cause.
- **L4.** Logs are diagnostic, not evidentiary. The evidentiary record is the
  hash-chained receipt ledger. No governance decision may depend on a log line.
- **L5.** Prometheus is for metrics; journald is for narrative. Do not
  synthesize metrics from log parsing.

---

## 11. Monitoring

### 11.1 Layered, independent detection

Four layers with deliberately different failure modes. This redundancy is
intentional and must be preserved.

| Layer | Mechanism | Cadence | Detects |
|---|---|---|---|
| 1 | Telegram heartbeat (in-process) | ~30 min | runtime state, coverage, `fwd-evid` canary |
| 2 | `healthcheck.py` dead-man (external timer) | 10 min | process death, disk, WAL, backup freshness, integrity, forward-cycle lag |
| 3 | Prometheus + Alertmanager | 15–30 s scrape | trends: RSS slope, coverage drift, reconnect rate, scheduler drift |
| 4 | Grafana | on demand | §15 characterization, historical analysis |

- **M1.** Layers 1 and 2 are **not** replaced by layer 3. Layer 2 is the
  dead-man of record; it survives Prometheus being down.
- **M2.** Alert severity reuses `healthcheck.py`'s approved taxonomy
  (`OK`/`INFO`/`WARNING`/`CRITICAL`, `_RANK` ordering). No second, divergent
  severity scheme.
- **M3.** Division of labour to avoid fatigue: layer 2 alerts on **state**,
  layer 3 alerts on **trend**. Same condition must not page twice.

### 11.2 Exporter constraints

- **M4.** Metrics are read from **in-memory runtime state only**. The scrape
  path must never open SQLite — that would couple scrape latency to DB locks
  and put a monitoring concern on the data plane.
- **M5.** The exporter must never raise into the runtime. It follows the
  established never-raise discipline of `forward_status.summarize()`.
- **M6.** Exporter failure degrades to no-metrics, never to a runtime fault.

### 11.3 Required metrics (initial set)

Runtime: `coverage_pct`, `stale_count`, `universe_size`, `reconcile_count`,
`ws_reconnects_total`, `scheduler_job_age_seconds`, `rss_bytes`.
Evidence: `forward_days_behind`, `forward_signals_ok`, `fn_hash_violations`.
Governance: `receipt_chain_valid`, `registry_check_passing`.
Host (node_exporter): disk free, load, memory.

---

## 12. Backup Strategy

`scripts/backup.py` is already correct in design (`VACUUM INTO` hot backup,
SHA-256 manifest, 7-daily/4-weekly/12-monthly rotation, optional off-host
transfer). **This ADR extends its operational envelope; it does not redesign it.**

| Asset | Method | Frequency | Off-host | Priority |
|---|---|---|---|---|
| `ledger.db` (governance) | `VACUUM INTO` + manifest | daily 02:00 UTC | **required** | **1 — irreplaceable** |
| `evidence_forward.db` | `VACUUM INTO` + manifest | daily | required | 2 |
| `forward_evidence.db` | `VACUUM INTO` + manifest | daily | required | 2 |
| `btc_trading.db` | `VACUUM INTO` + manifest | daily | recommended | 3 — re-collectable |
| Parquet corpus | rsync | weekly | recommended | 3 — re-ingestable |
| `.env` | manual, encrypted | on change | required | 1 |
| Compose config / dashboards | git | on change | git remote | 4 |
| `prom-data`, `grafana-data` | **none** | — | — | derived |

### Rules

- **BK1.** The governance ledger is the only truly irreplaceable artifact.
  Market data is re-collectable; the corpus is re-ingestable; the hash-chained
  decision record is not.
- **BK2.** Backups are worthless until a **restore drill** proves them. A
  quarterly drill on a clean host is mandatory: restore, then verify
  `receipt_ledger.verify_chain()` valid and `report.py --check` clean. A
  backup that has never been restored is an untested assumption.
- **BK3.** `BACKUP_OFFHOST_CMD` must be configured. Local-only backups do not
  survive VPS loss, which is the scenario backups exist for.
- **BK4.** Backup integrity is verified via the existing SHA-256 manifest, not
  assumed from exit code 0.
- **BK5.** 02:00 UTC scheduling is preserved — clear of the 11:58–12:06 UTC
  settlement dead zone and of the 00:05/01:00 evidence jobs.

---

## 13. Deployment Strategy

### 13.1 Two independent mechanisms, mirroring the plane boundary

| Plane | Mechanism | Change procedure |
|---|---|---|
| Data (host) | git pull + systemd | `git pull --ff-only` → `cp deploy/*.service /etc/systemd/system/` → `daemon-reload` → `restart` |
| Observability | Compose | edit config → `docker compose up -d` |

### 13.2 Ordering (risk-minimizing)

Deploy strictly in this order. Each step is independently reversible.

```
0. CI green (all tests + governance --check + verify_chain)
1. Observability plane      docker compose up -d      ← cannot affect runtime
2. Exporter                 restart btcbot.service    ← first data-plane touch
3. API service              new unit, additive
4. Timer/unit changes       daemon-reload + restart
5. Runtime code changes     restart btcbot.service    ← highest risk, last
```

### 13.3 Rules

- **D1.** CI must be green before any production deploy. No manual override.
- **D2.** **CD never auto-restarts `btcbot.service`.** Delivery is automated;
  restart of the live trading runtime is manually gated. A 24/7 process with
  drain semantics does not get restarted by a push.
- **D3.** No deploy during the **11:58–12:06 UTC settlement dead zone**
  (CLAUDE.md §14). Preferred window: 03:00–11:00 UTC — after backup, clear of
  evidence jobs and settlement.
- **D4.** SIGTERM drain semantics are preserved: `TimeoutStopSec=30`,
  `KillSignal=SIGTERM`. Never `kill -9` the runtime.
- **D5.** Every deploy ends with the **post-deploy verification block** (§15).
- **D6.** Schema migrations are backed up first (`backup.py --all`), applied
  via `scripts/migrate.py`, and verified before the dependent code is deployed.
- **D7.** §13 runtime constants must be **re-measured** after any change that
  perturbs the runtime process. They are budgets derived from a specific
  process configuration and can be invalidated silently.

---

## 14. Rollback Strategy

Every component has a defined rollback with a known blast radius. Rollback is
manual and explicit — consistent with the platform's existing precedent that
governance-adjacent recovery is operator-driven, not automatic.

| Component | Rollback | Time | Data risk |
|---|---|---|---|
| Compose stack | `docker compose down` / redeploy pinned digest | < 1 min | none — derived data only |
| Grafana dashboards | `git revert` + `up -d` | < 1 min | none |
| Prometheus rules | `git revert` + reload | < 1 min | none |
| Exporter | revert commit, `restart btcbot` | < 2 min | none — read-only |
| API service | `systemctl stop btcbot-api` | < 1 min | none — read-only |
| systemd unit change | revert file, `daemon-reload`, `restart` | < 2 min | none |
| Runtime code | `git checkout <prev>`, `restart` | < 5 min | none if no schema change |
| Schema migration | **restore from backup** — see below | 10–30 min | **real** |

### Rules

- **R1.** Every deployed commit must be revertible without a data migration.
  Schema changes are additive-only wherever possible.
- **R2.** **The governance ledger is never rolled back by mutation.** It is
  append-only by DB trigger. Recovery is restore-from-backup only —
  `cp ledger.pre-<migration>.<ts>.db ledger.db` — the pattern already
  established by the run_kind and transition-receipt migrations. Never attempt
  an `UPDATE`/`DELETE` repair.
- **R3.** Ledger-affecting migrations must write a timestamped backup **before**
  mutating, and the backup's fidelity must be verified
  (`PRAGMA integrity_check` + `verify_chain()`), not assumed.
- **R4.** Rollback of the observability plane is always safe and always
  available. This is what makes it correct to deploy first.
- **R5.** If rollback requires touching the ledger, **stop and escalate to the
  operator.** No autonomous governance-state repair.

---

## 15. Component Specifications

Per the required schema: purpose · container/host · persistent storage ·
startup dependency · healthcheck · security.

### 15.1 `btcbot.service` — live runtime

- **Purpose.** WS ingestion, universe reconciliation, snapshot writing,
  scheduler, Telegram heartbeat.
- **Placement.** **Host** (systemd). Not containerized — stateful WAL writer;
  the sandbox is proven and re-deriving it is the dominant risk.
- **Storage.** RW `/opt/btcbot/data/`; RO-but-directory-writable
  `/opt/btcbot/research/data/` (per V1).
- **Startup deps.** `network-online.target`, `time-sync.target`. Time sync is
  load-bearing — settlement logic is UTC-clock-dependent.
- **Healthcheck.** Telegram heartbeat (~30 min); `healthcheck.py check_service`
  (10 min); `restart=always`, `RestartSec=10`; exporter liveness on :9108.
- **Security.** Non-root `btcbot`; `NoNewPrivileges`, `PrivateTmp`,
  `ProtectSystem=strict`, explicit `ReadWritePaths`,
  `PYTHONDONTWRITEBYTECODE=1`. Secrets from `.env` (0640). Binds loopback only.

### 15.2 Metrics exporter — in-process

- **Purpose.** Expose existing in-memory runtime state as Prometheus text.
- **Placement.** **Host**, in-process within `btcbot.service` (not a separate
  unit — the state it reports lives in that process's memory).
- **Storage.** None.
- **Startup deps.** Runtime bootstrap complete; must not block it.
- **Healthcheck.** Prometheus `up{job="btcbot"}`; scrape latency alert.
- **Security.** `127.0.0.1:9108` only. No secrets, no PII, no API keys in
  labels. Must never raise into the runtime (M5).

### 15.3 `btcbot-api.service` — read-only API

- **Purpose.** Programmatic read of evidence matrix, timeline, chain
  verification, forward status.
- **Placement.** **Host**, separate unit. Never in-process with the runtime; a
  web framework must not share a process with the WS/scheduler loop.
- **Storage.** Read-only access to ledgers — **with** `ReadWritePaths` for
  their directories per V1.
- **Startup deps.** None on the bot (reads DBs directly). `After=network.target`.
- **Healthcheck.** `/health` (liveness), `/readyz` (ledger readable + chain
  valid); Prometheus-scraped.
- **Security.** `127.0.0.1:9109`; token auth; **no write endpoints** (B2),
  enforced by an AST/source-scan test in the style of the existing PR-6/PR-8
  structural guards, not by convention. Short-lived DB connections with
  timeout to avoid blocking the daily writer.

### 15.4 Timer one-shots (evidence, fwd-hg, backup, mapsnap, healthcheck)

- **Purpose.** Corpus refresh + evidence job (00:05); H5/G1/G3/ivrv collection
  (01:00); backup (02:00); instrument map snapshot (12:30); dead-man (10 min).
- **Placement.** **Host**, `Type=oneshot`. Conda 3.10 for research units,
  venv 3.11 for operational units — separation preserved (B3).
- **Storage.** RW `data/` and `research/data/` as already granted.
- **Startup deps.** `Persistent=true` on evidence/fwd-hg/backup/mapsnap so
  missed runs execute after downtime — **no silently skipped evidence days.**
  `Persistent=false` on healthcheck (point-in-time read). fwd-hg's 55-minute
  margin after the evidence job is a **hard ordering precondition**, not a
  tuning parameter.
- **Healthcheck.** Exit codes → journald; `check_forward_cycle` dead-man
  (1 day behind = WARNING, ≥2 = CRITICAL); `forward_days_behind` in Prometheus.
- **Security.** Same hardening as the runtime. `RandomizedDelaySec` prevents
  thundering-herd disk I/O.

### 15.5 `prometheus`

- **Purpose.** Metric collection and retention; alert rule evaluation.
- **Placement.** **Container** (pinned digest).
- **Storage.** Named volume `prom-data`. 90d retention, **size-capped**. Not
  backed up (derived).
- **Startup deps.** node-exporter; degrades gracefully if targets are down.
- **Healthcheck.** `/-/healthy`, `/-/ready`; Compose healthcheck; monitored by
  `up{job="prometheus"}` and, ultimately, by layer-2 dead-man independence.
- **Security.** `127.0.0.1:9090`; non-root user; config bind-mounted `:ro`; no
  project-data volume (INV-1); `mem_limit` set.

### 15.6 `alertmanager`

- **Purpose.** Deduplicate, group, and route alerts to the existing Telegram
  channel.
- **Placement.** **Container**.
- **Storage.** Config bind `:ro`; ephemeral notification state.
- **Startup deps.** None (Prometheus pushes to it).
- **Healthcheck.** `/-/healthy`; a scheduled synthetic alert proves the full
  path end-to-end — an alerting system that has never fired is unverified.
- **Security.** `127.0.0.1:9093`. Telegram token from a **dedicated minimal
  env file**, never `/opt/btcbot/.env` (S2).

### 15.7 `grafana`

- **Purpose.** Visualize §15 stabilization observations and evidence health.
- **Placement.** **Container**.
- **Storage.** Named volume `grafana-data`; dashboards **provisioned from git**
  so the volume holds no source of truth. Not backed up.
- **Startup deps.** `depends_on: prometheus`.
- **Healthcheck.** `/api/health`; Compose healthcheck.
- **Security.** `127.0.0.1:3000`, SSH tunnel only. Anonymous access **off**,
  sign-up **off**, admin password rotated on first boot. No datasource other
  than the local Prometheus.

### 15.8 `node_exporter`

- **Purpose.** Host CPU, memory, disk — feeds disk-exhaustion and RSS-growth
  alerts (§15 observations 3 and 5).
- **Placement.** **Container**, `network_mode: host`, `pid: host`.
- **Storage.** None. Host filesystem mounted `:ro` for metric collection only —
  this is host *metrics*, not project data, and does not breach INV-1.
- **Startup deps.** None.
- **Healthcheck.** `/metrics` responds; `up{job="node"}`.
- **Security.** `127.0.0.1:9100`; read-only mounts; no write capability
  anywhere; drop all capabilities.

---

## 16. Post-Deploy Verification Block (mandatory)

Every infrastructure change must pass all seven before being called done.

```
1. systemctl is-active btcbot.service                      → active
2. Telegram heartbeat renders:
     fwd-evid : YYYY-MM-DD  ok=5/5  fn_hash=ok  gov=ok     → the V4 canary
3. python research/evidence_library/report.py --check       → clean
4. receipt_ledger.verify_chain()                            → valid
5. python scripts/healthcheck.py                            → OK
6. ss -tlnp                                                 → no 0.0.0.0 except :22
7. coverage_pct and reconcile_count unchanged vs. baseline  → 24h observation
```

Item 2 is not ceremonial: it is the single cheapest end-to-end assertion
covering the sandbox, filesystem-permission, and WAL layers simultaneously —
precisely the failure class this architecture is designed around.

---

## 17. Preserved Invariants (non-negotiable)

Any future work that violates one of these requires a superseding ADR.

1. **Governance ledgers remain SQLite, append-only, hash-chained, host-resident.**
   No migration, no containerization, no network write path, no ORM.
2. **Testnet data remains pipeline-validation only.** Infrastructure work
   generates zero research evidence (CLAUDE.md §11).
3. **No governance mutation over HTTP.** Promotion is a judgment, not a
   computation; the platform must not promote itself (PR-8).
4. **`report.py --check` clean and `verify_chain()` valid** before and after
   every change.
5. **Runtime behavior is unchanged.** Coverage, reconcile, rollover, and the
   §14 settlement semantics are untouched by all infrastructure work.
6. **The §13 runtime constants are re-measured, not assumed**, after any
   runtime-process change.
7. **`healthcheck.py` remains an independent dead-man**, never subsumed by
   Prometheus.
8. **Frozen governance thresholds are untouched**: `n_trials = 34`,
   `min_dsr_prob = 0.50`, `max_bh_p = 0.10`, `max_pairwise_r = 0.70`.

---

## 18. Deferred Decisions

| Item | Status | Trigger to revisit |
|---|---|---|
| PostgreSQL | Deferred | Measured SQLite contention (`collection_metrics.failed`), or `btc_trading.db` > 10 GB. **Telemetry only — never governance.** |
| Redis | Rejected | Multi-process shared rate-limit state, or a real task queue replacing systemd timers |
| Containerizing the bot | Rejected | Genuine multi-node requirement |
| Log aggregation (Loki) | Deferred | journald retention proves insufficient for incident forensics |
| Public TLS ingress | Rejected | Remote non-operator access requirement |
| Automated CD to production | Deferred | After ≥3 months of incident-free manual deploys |
| Secret manager (Vault/SOPS) | Deferred | More than one host, or more than one operator |

---

## 19. Phase → Contract Mapping

| Roadmap phase | Contract sections |
|---|---|
| 0 — CI/CD | §13.1, §13.3 D1 |
| 1 — Docker | §5, §7.2, INV-1 |
| 2 — Exporter | §11.2, §15.2 |
| 3 — Prometheus/Grafana | §6, §11, §15.5–15.8 |
| 4 — FastAPI | §15.3, B2 |
| 5 — PostgreSQL | §18 (gated) |
| 6 — Compose / prod | §6, §13, §14 |

Phases 0–3 fall within the CLAUDE.md §15 stabilization mandate (CI and metrics
*are* operational intelligence). **Phases 4–6 are feature expansion and require
an explicit gate from Rohith before implementation begins.**

---

## 20. Open Questions

1. Off-host backup target — not yet provisioned. **Blocks BK3**, and BK3 is
   what makes backups survive the scenario they exist for.
2. Alert routing: single Telegram channel for both heartbeat and Prometheus
   alerts, or a second channel to separate trend from state? Recommend a
   second channel.
3. `.env` disaster-recovery: currently manual. Needs a documented encrypted
   off-host copy procedure.
4. Restore-drill cadence: quarterly proposed, unconfirmed.
