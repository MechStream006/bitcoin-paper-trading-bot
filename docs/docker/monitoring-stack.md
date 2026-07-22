# Observability Stack — build, deploy, verify

Implements ADR-0001 Phase 3 (§6, §8, §9 S2/S3, §10 L3, §11, §15.5-15.8).
Read that ADR first — this doc is the operational how-to plus two
documented deviations, not the design rationale.

## What this is, and is not

- **Is:** Prometheus, Alertmanager, Grafana, node_exporter — the
  observability plane, containers only.
- **Is not:** a place the bot or a future API ever runs (INV-1, ADR §2/§5.2
  — both stay on bare systemd). Not wired into CI. Not a replacement for
  the Telegram heartbeat (layer 1) or `scripts/healthcheck.py` (layer 2) —
  both remain the primary/dead-man channels; this stack is layer 3 (trend).
- **Consumes, does not modify**: `src/observability/metrics_exporter.py`
  (Phase 2) is read-only here. Nothing in this phase touches that file.

## Two documented deviations from ADR-0001's literal text

Both were necessary, not stylistic. Both are explained in full at the top
of `docker-compose.yml` — this is the short version.

**1. All four services use `network_mode: host`, not the ADR §6 bridge
network.** The btcbot metrics exporter is hardcoded to bind `127.0.0.1`
only (Phase 2's explicit "localhost only" requirement, which this phase
must not touch). A socket bound to `127.0.0.1` refuses connections that
arrive via any other interface, including a Docker bridge/gateway path —
that's a kernel-level fact about loopback binding, not a config detail.
Host networking is the only topology where "127.0.0.1" is the same
loopback for the host process and the container. Every service therefore
pins its own listen address explicitly (`--web.listen-address` /
`GF_SERVER_HTTP_ADDR`) since Compose `ports:` is a silent no-op under host
networking.

**2. node_exporter mounts only `/proc` and `/sys`, not host `/` (ADR §15.8's
literal recipe).** Resolved via an explicit question back to Rohith rather
than guessed: the stricter reading was chosen — zero project paths are
ever reachable inside any container, full stop, at the cost of
`node_filesystem_*` (disk-space) metrics from node_exporter. Disk-free
remains covered by the bot's own heartbeat storage line, which needs no
container at all.

## Compose architecture

```
deploy/compose/
  docker-compose.yml
  .env.example                        -> copy to .env (gitignored), set GRAFANA_ADMIN_PASSWORD
  prometheus/
    prometheus.yml                    scrape configs (all 127.0.0.1, see deviation 1)
    alert_rules.yml                   9 rules: 1 watchdog + 8 trend/degradation
  alertmanager/
    alertmanager.yml                  routing + Telegram receiver (chat_id placeholder, CHANGEME)
    secrets/
      README.md                       (tracked)
      telegram_bot_token               (gitignored, operator-provisioned)
  grafana/
    provisioning/
      datasources/datasource.yml      Prometheus, http://127.0.0.1:9090, non-editable
      dashboards/dashboards.yml       provider config, allowUiUpdates: false
    dashboards/
      btcbot-operations.json          the one dashboard (see below)
```

No Dockerfile — all four images are upstream, pulled, digest-pinned:

| Service | Image | Tag (human reference) |
|---|---|---|
| prometheus | `prom/prometheus@sha256:63805ebb...` | v3.5.0 |
| alertmanager | `prom/alertmanager@sha256:27c475db...` | v0.28.1 |
| grafana | `grafana/grafana@sha256:62d2b9d2...` | 11.6.0 |
| node-exporter | `prom/node-exporter@sha256:d00a542e...` | v1.9.1 |

(Full digests are in `docker-compose.yml`; resolved via real `docker pull` +
`docker inspect`, same discipline as Phase 1's `Dockerfile.research`.)

## Networking

| Service | Bind | Mechanism |
|---|---|---|
| node_exporter | `127.0.0.1:9100` | `--web.listen-address` (host network) |
| prometheus | `127.0.0.1:9090` | `--web.listen-address` (host network) |
| alertmanager | `127.0.0.1:9093` | `--web.listen-address` (host network) |
| grafana | `127.0.0.1:3000` | `GF_SERVER_HTTP_ADDR` (host network) |

No `obs` bridge network exists — none is needed since every cross-service
call resolves to `127.0.0.1` regardless of source. **After every deploy on
the real VPS**, run `ss -tlnp` and confirm all four ports show
`127.0.0.1`, never `0.0.0.0` (ADR §16 item 6) — this matters more here
than in the ADR's original bridge design, because a missed listen-address
flag under host networking is a real public exposure, not a
container-local one.

## Security review

- **INV-1 / "no project data mounted"**: the only bind mounts into any
  container are this directory's own config files (`:ro`) and `/proc`,
  `/sys` for node_exporter. Verified empirically, not just by reading the
  compose file: `docker exec` into each running container and `find`/`ls`
  for `.env`, `*.db`, `research/`, `data/` all came back empty during
  deployment verification.
- **Secrets (S2)**: the Telegram bot token is read via alertmanager's
  native `bot_token_file` (validated against v0.28.1 with `amtool
  check-config` before being used in this config, and again during full-stack
  verification, where Alertmanager made a real Telegram API call using it —
  see "Deployment verification" below). It never touches the bot's own
  `.env` and is never embedded in a committed file.
- **Grafana (S3)**: anonymous access off, sign-up off, admin password from
  `deploy/compose/.env` (gitignored) — rotate immediately after first
  login, same as any other initial-password flow.
- **Resource limits (C5)**: `mem_limit`/`cpus` set on all four (prometheus
  512m/1.0, grafana 256m/0.5, alertmanager 64m/0.25, node-exporter
  64m/0.25) so the observability plane cannot starve the runtime on a
  1-2GB VPS.
- **Log caps (L3)**: `json-file` driver, `max-size: 10m`, `max-file: 3` on
  all four.
- **Volumes (§7.2)**: `prom-data` and `grafana-data` are named,
  docker-managed, and **not backed up** — both are derived (TSDB is
  re-observable; dashboards are provisioned from git, so the Grafana
  volume holds no source of truth).

## Alert strategy (M1-M3)

9 rules in `prometheus/alert_rules.yml`. Every rule's `description` states
explicitly which layer-1/layer-2 signal it's related to and why it isn't a
duplicate — either it requires sustained persistence (15m-3h) where the
heartbeat/healthcheck.py only ever look at a point in time, or it measures
something neither of those layers measures at all (RSS growth trend,
reconnect-storm rate, the heartbeat's own silence).

- `Watchdog` — always firing, routed to a `null` receiver. Its **absence**
  from Prometheus's firing list is the signal (proves the eval+notify pipe
  is alive), not its presence — the "scheduled synthetic alert" ADR §15.6
  requires. Verified firing during deployment verification; verified it
  never attempted a Telegram send (checked Alertmanager's own logs).
- 7 trend/degradation warnings (coverage, WS reconnect storm, forward-cycle
  staleness, scheduler job stall, RSS growth, exporter-target-down).
- 1 instant critical (`fn_hash_violation`) — deliberately NOT trend-gated,
  matching the heartbeat's own instant-CRITICAL treatment of tamper
  detection, as a genuinely independent second detection path (M1), not a
  duplicate suppression.
- One inhibit rule: a tamper CRITICAL suppresses the staleness WARNING for
  the same underlying cycle.
- Telegram messages are prefixed `[Prometheus TREND]`, visually distinct
  from the heartbeat's `--- BTC Bot Heartbeat ---` framing, reusing the
  **same** bot/channel (no second channel exists yet — ADR §20 open item 2
  remains a good future improvement, not required here).

## Dashboard summary

One dashboard, **not two** — 28 exported metrics don't justify padding a
second one. `BTC Bot Operations`, provisioned in a `BTC Bot` folder,
`allowUiUpdates: false`:

- **Runtime row**: coverage, universe size, stale symbols, reconcile
  count, WS connected/message-rate/reconnects/last-message-age, scheduler
  job ages, heartbeat healthy/last-send-age, process RSS, a component
  health-rollup table.
- **Forward Evidence row**: days-behind, signals ok/logged, fn_hash
  violations.
- **Host row**: CPU %, load average, memory available (node_exporter, no
  disk-space panel — see deviation 2).

No governance/ledger panels — Phase 2 deliberately does not export
`receipt_chain_valid`/`registry_check_passing` (would require a SQLite
read on the scrape path, forbidden by M4), so there is nothing to chart.

## Deployment verification (actually run, not assumed)

Performed end-to-end on this dev machine before calling this done:

1. `promtool check config` / `check rules` on the real `prometheus.yml` +
   `alert_rules.yml` — SUCCESS, 9 rules found.
2. `amtool check-config` on the real `alertmanager.yml` with a real
   `bot_token_file` mount — SUCCESS.
3. `docker compose up -d` — all four containers reached `healthy`.
   (Caught and fixed a real bug in this process: the first `docker-
   compose.yml` draft bind-mounted the dashboards directory *inside*
   the already-read-only `provisioning/dashboards` mount, which Docker
   correctly refused — "make mountpoint: read-only file system". Fixed
   by mounting dashboard JSON files at a sibling path instead.)
4. Ran a real instance of `MetricsExporter` (via the Phase 2 test stubs)
   in a container on the **same** `--network host`, simulating the real
   VPS scenario of a `127.0.0.1`-bound host process alongside a
   host-networked container. Prometheus's own `/api/v1/targets` showed
   `job=btcbot` transition to `health: up`, and `/api/v1/query?query=
   btcbot_coverage_ratio` returned a real value (`0.75`, matching the
   stub's 3-of-4-ticked universe) — proof the full scrape path works, not
   an assumption.
5. Confirmed `BtcBotCoverageDegradedTrend` correctly entered `pending`
   state (0.75 < 0.8 threshold) and `Watchdog` was `firing`, both via
   `/api/v1/rules`.
6. Confirmed Alertmanager's `/api/v2/alerts` showed `Watchdog` as
   `active`, and its own logs showed **zero** notify attempts for it
   (null-receiver routing confirmed). Separately, `BtcBotExporterTargetDown`
   fired (before step 4's container existed) and Alertmanager's logs showed
   a real attempted Telegram API call that failed with `401 Unauthorized`
   — expected, since a deliberately fake token was used for this test; the
   401 itself is the proof the token-file plumbing and the real HTTPS call
   both work correctly.
7. Confirmed via Grafana's own API: the `Prometheus` datasource
   (`readOnly: true`, pointing at `127.0.0.1:9090`) and the `BTC Bot
   Operations` dashboard were both provisioned automatically, no manual
   UI steps.
8. All test artifacts (containers, the throwaway `.env`, the dummy secret
   token, the named volumes) were torn down afterward — nothing was left
   running.

**Not verifiable on this dev machine, must be re-checked on the real
Ubuntu VPS**: `ss -tlnp` port-binding confirmation. Docker Desktop for
Windows runs `network_mode: host` inside its own Linux VM, not the actual
Windows host network stack, so a Windows-side port scan is meaningless
here — this is exactly why step 4 above used a host-networked *container*
to stand in for the real host process, rather than relying on Windows-side
verification at all.

## Rollback plan

`docker compose down` (add `-v` to also drop `prom-data`/`grafana-data` —
both derived, safe to lose) stops and removes all four containers in under
a minute. Nothing here touches the bot's runtime, its systemd units, or
any project data — full rollback is `git revert` this directory plus
`docker compose down -v`. The bot itself never depended on this stack
existing (Prometheus scraping it is one-directional; if this stack is
down, the exporter just sits unscraped, exactly as it did before Phase 3).
