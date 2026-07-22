# Read-Only Operational API — deploy, verify, roll back

Implements ADR-0001 Phase 4 (§15.3, §4 B1/B2, §7.3 V1, §8, §9, §10, INV-3).
Read the ADR first — this doc is the operational how-to, not the rationale.

## What this is, and is not

- **Is:** a loopback-only, token-authenticated, **read-only** HTTP projection of
  operational state that already exists — runtime health, forward-evidence
  status, governance verification, and the Evidence Library.
- **Is not:** a control plane. There is no write endpoint, no state transition,
  no promotion, no execution capability, and no code path to any of them
  (B2, INV-3: *promotion is a judgment, not a computation; the platform must
  not promote itself*). Governance mutation stays CLI-and-human-gated.
- **Is not** a second source of truth. Every response is projected from the
  same modules the CLI and Telegram heartbeat use, so it **cannot disagree**
  with them. Verified: `/api/v1/evidence/summary` and `report.py --check`
  independently returned 35 families / 9 candidate / 22 discovery / 4
  disqualified.
- **Is not** in the Prometheus/Grafana stack (Phase 3) and does not replace the
  heartbeat (layer 1) or `healthcheck.py` (layer 2). It is a pull-based
  operator/programmatic surface, not a detection layer.

## Endpoints

| Method | Path | Auth | Reuses |
|---|---|---|---|
| GET | `/health` | no | nothing — touches no DB or disk |
| GET | `/readyz` | no | `verify_receipt_chain()` |
| GET | `/api/v1/runtime/health` | **yes** | `scripts/healthcheck.py` |
| GET | `/api/v1/forward/status` | **yes** | `alerts/forward_status.summarize()` |
| GET | `/api/v1/governance/chain` | **yes** | `verify_receipt_chain()` (PR-1) |
| GET | `/api/v1/governance/verify` | **yes** | `verify_governance()` (PR-9) |
| GET | `/api/v1/evidence/summary` | **yes** | `report.build_context()` (PR-5) |
| GET | `/api/v1/evidence/families/{id}` | **yes** | `report.build_context()` (PR-5) |

Interactive docs at `/docs` (Swagger) and `/redoc`; schema at `/openapi.json`.

`/api/v1/governance/verify` and both evidence endpoints accept `?as_of=` to
reconstruct state from the append-only ledger as it stood at any past instant.
Verified: `as_of=2026-06-01` returns 0 transitions and all 35 families in
`discovery` — the correct pre-governance board.

**`ok: false` is returned with HTTP 200**, never a 5xx. A governance finding is
data this API exists to report; conflating it with an API fault would make a
monitoring client unable to distinguish "governance has a problem" from "the
API has a problem". 503 means a data source is unreadable; 5xx never means
"governance failed".

### Two deliberate exclusions

1. **`PRAGMA integrity_check` is not exposed.** In `healthcheck.py` it is gated
   behind `--full-integrity` and documented as "expensive — seconds for large
   DB". A full-table scan triggered by an HTTP request is exactly the
   "synchronous long-running transaction" this phase forbids. It remains
   available from the CLI. `integrity_check_included: false` is returned
   explicitly so a consumer knows the roll-up excludes it rather than assuming
   it passed.
2. **Report generators are not invoked.** `build_context()` — the pure snapshot
   every generator consumes — is projected directly. Calling
   `generate_state_board()` / `generate_library()` would mean an HTTP GET
   writes markdown into the repo. A test asserts no `generate_*` is ever called.

## Authentication

`Authorization: Bearer <API_TOKEN>`, compared with `secrets.compare_digest`
(constant-time, so response latency does not leak how many characters matched).

- Token lives **only** in `/opt/btcbot/.env` (0640 `root:btcbot`), per §9. Never
  in a config file, an image layer, git, a log line, or the OpenAPI schema.
- **Fails closed at startup**: a missing, blank, or <32-char token makes the
  process exit 2 rather than start unauthenticated. Verified.
- Missing and wrong tokens return **byte-identical** 401 bodies — no oracle
  telling an attacker whether the header was absent or merely wrong.
- `/health` and `/readyz` are unauthenticated on purpose: they are probes and
  expose no operational data (`/health` returns only status/service/version/ts).

The token is the **second** control. The first is that the socket is unreachable
from anywhere but the host (§8 N1).

## Deployment

```bash
# 1. Its OWN venv -- never /opt/btcbot/venv (see below).
python3.11 -m venv /opt/btcbot/venv-api
/opt/btcbot/venv-api/bin/pip install -r /opt/btcbot/requirements-api.txt

# 2. Generate and store the token.
python3 -c "import secrets; print('API_TOKEN=' + secrets.token_urlsafe(32))" \
  | sudo tee -a /opt/btcbot/.env
sudo chown root:btcbot /opt/btcbot/.env && sudo chmod 640 /opt/btcbot/.env

# 3. Install and start the unit.
sudo cp /opt/btcbot/deploy/btcbot-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now btcbot-api.service
```

### Why a separate venv

"API failure must never affect `btcbot.service`" is not satisfied by process
separation alone. A shared venv would mean a FastAPI/Starlette/pydantic upgrade
for the API silently changes the dependency set underneath the live trading
runtime. Separate venvs make that impossible rather than unlikely.

### Why no systemd dependency on the bot

The unit deliberately has **no** `Requires=`, `BindsTo=`, `PartOf=`, or
`Wants=btcbot.service`. Those are the one mechanism that could let API failure
propagate to the runtime. The API reads the databases directly and has no
startup dependency on the bot (§15.3) — if the bot is down, that is data this
API *reports*, not a reason for it to stop serving.

### Why `ReadWritePaths` on a read-only service (V1)

`ReadWritePaths=/opt/btcbot/data /opt/btcbot/research/data
/opt/btcbot/research/evidence_library` is required by ADR §7.3 **V1**: a WAL
reader opens and locks the `-wal`/`-shm` siblings, which needs write permission
on the containing *directory* regardless of SQL-level mode. Omitting any of the
three produces `sqlite3.OperationalError: unable to open database file` on a
perfectly readable database — the exact 2026-07-18 failure class V1 exists to
prevent.

This grants directory-level write to satisfy WAL locking. It does **not** make
this service a writer — see the security review.

## Security review

| Control | Status |
|---|---|
| Loopback-only bind | `127.0.0.1:9109`; **host is a module constant, not an env var**, so a typo cannot create a public exposure (test asserts `API_HOST=0.0.0.0` is ignored) |
| Token auth | constant-time compare; fail-closed startup; ≥32 chars |
| Secret handling | `.env` only; never logged, never in `/openapi.json` (tested) |
| Write endpoints | none — 4 structural guards + real HTTP 405 on POST/PUT/PATCH/DELETE |
| Execution capability | `src.execution` / `src.exchange` imports banned by test |
| Runtime plane isolation | own venv, own unit, no systemd dependency, no shared process |
| Secrets in memory | `config/settings.py` is deliberately **not** imported, so `DELTA_API_KEY`/`_SECRET` never enter this process |
| Resource ceiling | `MemoryMax=384M`, `CPUQuota=50%` — cannot starve the runtime |
| Hardening | `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`, `PrivateDevices`, `PrivateTmp`, `RestrictSUIDSGID`, `LockPersonality` |
| Logging | JSON to stdout → journald; query strings logged as a presence flag, not verbatim |

### On "read-only by construction" — the honest limit

INV-1/B1 want read-only *by construction, not convention*. The strongest
OS-level enforcement — mounting the ledger paths read-only — is **unavailable
here**, because V1 requires directory write for WAL locking. The ADR
acknowledges this and specifies the enforcement that is achievable
(§15.3: "enforced by an AST/source-scan test ... not by convention").

That enforcement is implemented in `tests/test_api.py`:

1. no non-GET route or generic `add_api_route` anywhere in the package;
2. the built app registers only `GET`/`HEAD`/`OPTIONS`;
3. no mutation function is imported **or called** — `append_transition`,
   `transition_with_receipt`, `promote_with_receipt`, `admit_evidence`,
   `activate_contract`, `commit`, `execute`, … (a verb-only check would still
   pass if someone called `promote_with_receipt()` inside a GET handler);
4. no DML/DDL string literal (`INSERT`/`UPDATE`/`DELETE`/`DROP`/…) exists;
5. no execution/exchange module is importable.

Plus an empirical check: SHA-256 the real `ledger.db`, hit every endpoint, hash
again. Verified byte-identical, with no `-wal`/`-shm` sidecars left behind.

## Performance

Measured against the real 26-receipt ledger and 35-family registry:

| Endpoint | Cold | Warm (cached) |
|---|---|---|
| `/api/v1/governance/verify` (full 5-subsystem sweep) | 351 ms | ~5 ms |
| `/health` | ~5 ms | n/a (touches nothing) |

A 10-second TTL cache with **single-flight** fronts every DB-backed endpoint.
Single-flight matters as much as the TTL: N concurrent cold requests produce
**one** ledger read, not N — otherwise the cache would remove steady-state load
but leave a thundering herd at expiry, which is when load is most likely to
coincide with the daily writer. `as_of` is part of the cache key, so a cached
"now" answer is never served for a historical query (tested).

## Deployment verification

Run after install; mirrors ADR §16.

```bash
systemctl is-active btcbot-api.service                    # -> active
systemctl is-active btcbot.service                        # -> active (unaffected)
ss -tlnp | grep 9109                                      # -> 127.0.0.1:9109 ONLY, never 0.0.0.0
curl -s localhost:9109/health                             # -> {"status":"ok",...}
curl -s localhost:9109/readyz                             # -> {"ready":true,...}
curl -s -o /dev/null -w '%{http_code}\n' \
     localhost:9109/api/v1/governance/chain               # -> 401 (no token)
curl -s -H "Authorization: Bearer $API_TOKEN" \
     localhost:9109/api/v1/governance/verify | jq .ok     # -> true
# Cross-check the API against the CLI -- they must agree:
cd /opt/btcbot/research/evidence_library && python report.py --check
journalctl -u btcbot-api -n 20 -o cat                     # -> one JSON object per line
```

Then re-run ADR §16 items 2–5 (heartbeat V4 canary, `report.py --check`,
`verify_chain()`, `healthcheck.py`) to confirm the runtime is untouched.

### What was actually verified on the dev machine

Not assumed — run end-to-end against the **real** governance ledger:

1. **1266 tests pass** (79 new in `tests/test_api.py`). The 2 failures are
   pre-existing date-dependent expiry tests, proven pre-existing by re-running
   them with the Phase 4 work stashed.
2. Real uvicorn server, real HTTP: all 8 endpoints, `/docs`, `/redoc`,
   `/openapi.json`.
3. **Loopback binding confirmed on a live socket**: `Get-NetTCPConnection`
   reported `LocalAddress 127.0.0.1`, not `0.0.0.0`.
4. **API and CLI agree exactly**: 35 families, 9 candidate / 22 discovery /
   4 disqualified — matching `report.py --check`.
5. **Real values match CLAUDE.md §16** for H-IVRV: Sharpe 0.2649, DSR 0.1646,
   BH-p 0.2893, provenance `offline-corpus`.
6. Full governance sweep ran all 5 subsystems: `receipt_chain`,
   `registry_c1_c9`, `evidence_governance`, `transition_governance`,
   `cross_subsystem` — 26 receipts, 13 transitions, `ok: true`, with one
   expected `GV-SKIP` info finding (PR-7 readiness framework has no adopters).
7. **Ledger unmodified**: SHA-256 identical before/after 60+ requests.
8. POST/PUT/PATCH/DELETE → 405 at the real HTTP layer.
9. 401 for missing, wrong, prefix, wrong-scheme, and non-ASCII credentials.
10. **96/96 stdout lines were valid JSON, with 0 token leaks.**
11. Fail-closed startup: no token → exit code 2.

**Not verifiable on the dev machine, must be re-checked on the VPS:**
`systemctl` behaviour and `ss -tlnp` (the unit file, `ReadWritePaths`, and the
hardening directives are Linux-only; the `service` check correctly self-reported
`systemd checks skipped (Windows)` here).

## Rollback

```bash
sudo systemctl disable --now btcbot-api.service
sudo rm /etc/systemd/system/btcbot-api.service
sudo systemctl daemon-reload
```

Under a minute, and **nothing else is affected**: the bot has no dependency on
this unit, this unit writes nothing, and removing it cannot leave partial state.
Full rollback is the above plus `git revert` of `src/api/`,
`tests/test_api.py`, `requirements-api.txt`, `deploy/btcbot-api.service`, and
the `ci.yml` install step. Optionally `rm -rf /opt/btcbot/venv-api` and drop
`API_TOKEN` from `.env`.

The one shared-file change is the `ci.yml` install step (CI now installs
`requirements-api.txt`, because `pytest tests/` collects `tests/test_api.py`).
Reverting the API without reverting that step leaves CI installing two unused
packages — harmless, but revert both together for cleanliness.
