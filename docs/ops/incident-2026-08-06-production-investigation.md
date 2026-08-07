# Incident Investigation — Production Host, 2026-08-06

**Investigator:** Claude Opus 5 · **Mode:** read-only · **Host:** `3.110.103.217` (`ip-172-31-7-223`)
**Trigger:** Phase 4 D0 deployment halted at Step 0 pre-flight.
**Status:** Investigation complete (2026-08-06, read-only). **Recovery executed
2026-08-07 — see §10.** B1 and B2 RESOLVED; B5 partially; B3/B4/B6 open on a
credential.

> **Disclosure — one inadvertent change, since cleaned up.** A `sqlite3` probe
> against a non-existent path created a **0-byte `ledger.db`** at
> `/home/ubuntu/bitcoin-paper-trading-bot/research/evidence_library/`
> (sqlite3 creates on open). That path had no ledger before; it is **not** the
> production ledger and no production data was touched. Removed 2026-08-07
> during recovery, restoring the prior state.

---

## Executive summary

The deployment was halted for the right reason but the wrong magnitude. Step 0
found six blockers; investigation shows **three independent, unrelated root
causes**, the oldest dating to 2026-07-18, and **two active data-loss
conditions** that have been running unobserved for weeks.

| | Finding | Since | Ongoing loss |
|---|---|---|---|
| 🔴 | **Market data collection dead** — wrong WebSocket platform | 2026-08-03 12:33 UTC (77h) | Yes |
| 🔴 | **Forward evidence collection dead** — unmigrated ledger | 2026-07-18 (20 days) | Yes |
| 🟠 | **Monitoring never installed** — healthcheck/certify/api units absent | since host build | n/a |

The three are causally independent but share one enabling condition: **the
health-check dead-man that would have caught all of them was never installed
on this host.** Both outages were silent because nothing was watching.

---

## Evidence classification

### Confirmed facts (directly observed)

| # | Fact | Evidence |
|---|---|---|
| F1 | Last WebSocket message: **2026-08-03T12:33:07Z** | `last_msg=192982.0s` at `2026-08-05T18:09:29Z`, arithmetic verified |
| F2 | **80,860** `option_quotes` rows written after F1, sharing **1 distinct `exch_ts`** | SQL over `option_quotes` |
| F3 | Healthy baseline: 94,984 rows / **1,497** distinct `exch_ts` per 24h | same query, prior day |
| F4 | `wss://testnet-socket.delta.exchange` accepts + ACKs, delivers **0 messages/20s** | local probe |
| F5 | `wss://socket-ind.testnet.deltaex.org` delivers **4 messages/12s**, real marks | local probe |
| F6 | `.env` has `DELTA_BASE_URL=…cdn-ind.testnet.deltaex.org` (India) but `DELTA_WS_URL=…testnet-socket.delta.exchange` (**Global**) | host `.env` |
| F7 | `ledger.db` mtime **2026-06-26 06:59**, 86,016 bytes; every daily backup Jul 01–Aug 06 is **also 86,016 bytes** | `ls`, backup dir |
| F8 | `runs.run_kind`, `governance_receipts`, `pairwise_r` **absent**; stale `latest_run_per_family` view **present** | `PRAGMA table_info`, `.tables` |
| F9 | All three `migrate_*.py` scripts and `governance_verification.py` **are present** on host | `ls` |
| F10 | `ledger.pre-run_kind-migration.…audit.txt` is **git-tracked** → arrived by checkout | `git ls-files` |
| F11 | Forward `run_log` last `run_date` = **2026-07-17** for all 5 signals | `evidence_forward.db` |
| F12 | `btcbot-fwd-hg` failed **28 times**, every day journal retains (Jul 24–Aug 06) | `journalctl` |
| F13 | `/etc/systemd/system/` has **no** healthcheck, certify, or api unit; **no** `venv-api` | `ls` |
| F14 | Last successful pull: **2026-07-18 07:12 UTC** → `7fd201e`; host is **21 commits** behind | `git reflog` |
| F15 | **No** credential helper, `.git-credentials`, `.netrc`, or `.ssh` for `btcbot`; `ubuntu` clone also cannot fetch | config + live test |
| F16 | Host drift = `g3_atr_expansion.enabled: false→true`, plus `.bak.20260628` | `git diff`, `ls` |
| F17 | `main`'s version of that file is **already `enabled: true`** | `git show main:` |
| F18 | `btcbot-evidence.service` (DVOL ingest) **succeeds** — `Result=success` | `systemctl show` |
| F19 | `btcbot.service` up 6 days; REST chain refresh working (`mark=64802`) | journal |

### Hypotheses (consistent with evidence, not independently confirmed)

| # | Hypothesis | Confidence | What would confirm it |
|---|---|---|---|
| H1 | Delta retired Global-testnet WS service for India-platform products on 2026-08-03 ~12:33 UTC | **High** — F4/F5 are decisive about the *current* state; only the *cause and date* are inferred | A Delta changelog/announcement |
| H2 | The Increment 5 "production migration" was **never executed against `/opt/btcbot`** (rather than executed and failed) | **High** — F7 shows the file untouched since Jun 26; no `.pre-*.db` backup exists, and the scripts always create one | Operator recollection; shell history |
| H3 | `install.md` steps 5/6/7 (certify, observability, API) were never performed on this host | **High** — F13 | Deployment records |
| H4 | The ATR activation was performed ~2026-06-28 per the activation card | **Medium** — `.bak.20260628` matches the card's Phase A naming exactly, and a sibling `btcbot-fwd-hg.service.bak.20260628` shows one operator session that day | Operator confirmation |

### Unknowns (cannot be determined from the host)

- Whether Delta published notice of the WS platform change.
- Whether the 80,860 stale rows have propagated into any downstream research corpus.
- Why healthcheck/certify/api installation was skipped — no record exists either way.
- Whether ATR g3 was ever *intended* to run before the ledger broke collection.

---

## B1 — Market data collection dead

**Root cause.** `.env` points `DELTA_WS_URL` at the **Global** testnet socket
(`wss://testnet-socket.delta.exchange`) while `DELTA_BASE_URL` was correctly
migrated to the **India** platform (F6). This is precisely the platform
separation CLAUDE.md §11 documents as "the root cause of all `invalid_api_key`
failures" — REST was fixed then; **WebSocket was left behind.** It kept working
only because Delta continued serving India products on the Global socket. That
stopped on 2026-08-03 12:33:07 UTC.

The failure is silent by design of the exchange: CLAUDE.md §11 already records
that **Delta accepts subscriptions and returns a `subscriptions` ACK, then
publishes nothing.** My probe reproduced exactly that (F4) while the India
endpoint returned live data on the same payload (F5).

**Why the service stayed "healthy" — the specific defect.** The supervisor's
health predicate keys on the **FSM state**, not on data flow:

```
SUBSCRIBED  → HEALTHY          (regardless of last_msg age)
  ↓ 60s with no message
STALE → DISCONNECTED → UNHEALTHY
  ↓ supervisor restarts ws_manager
CONNECTING → SUBSCRIBED → HEALTHY   ← resets to healthy having received nothing
```

Observed: `'ws_manager' -> HEALTHY` logged 10 s after a restart whose own
warning line read `last_msg=192982.0s` (53 hours). The loop ran **327
reconnects / 320 supervisor restarts**. Any sample taken during a SUBSCRIBED
window — including my Step 0g check — reports HEALTHY. This is a genuine
monitoring defect, independent of the URL: **a component with a 53-hour-old
last message must never be reported HEALTHY.**

**Earliest observable failure:** `2026-08-03T12:34:07Z` — `FSM: STREAMING -> STALE
reason='no message for 60s'`. This line was correct and unheeded.

**Timeline**

| UTC | Event |
|---|---|
| 08-03 12:05:00 | Settlement rollover completes normally, resubscribe sent, `quotes=64/64` |
| **08-03 12:33:07** | **Last WebSocket message ever received** |
| 08-03 12:34:07 | `STREAMING -> STALE`; reconnect loop begins |
| 08-03 12:33 → 08-05 18:06 | **80,860 stale rows written**, all one `exch_ts` (F2) |
| 08-05 18:06:42 | Reconcile retires the last ever-ticked symbols → `coverage_pct` 0.0, writes stop |
| 08-06 17:46 | Investigation: `universe=66, coverage=0.0, wrote=0, stale_count=66` |

**Impact.** 77h+ of genuine market data permanently lost, ongoing. Plus 80,860
rows of stale duplication in `option_quotes`.

**Current production risk.** 🔴 Highest. Loss accrues continuously on a 24/7
market. Corpus is actively accumulating pollution-free but data-free cycles.

**Data recoverable?** **No** for the missing ticks — WS is not replayable and
Delta offers no historical option-quote backfill. **Yes** for the pollution:
the 80,860 bad rows share exactly **one** `exch_ts` against a healthy baseline
of ~1,497/day (F2 vs F3), giving a perfect, unambiguous discriminator for
surgical deletion.

**Deployment depends on it?** No — independent. But deploying while collection
is dead makes any post-deploy verification meaningless.

**Recovery procedure**

```bash
# 1. Confirm the endpoint from the host itself (do not trust an off-host probe)
sudo -u btcbot /opt/btcbot/venv/bin/python - <<'EOF'
import json, time, websocket
ws = websocket.create_connection("wss://socket-ind.testnet.deltaex.org", timeout=10)
ws.send(json.dumps({"type":"subscribe","payload":{"channels":[{"name":"v2/ticker","symbols":["BTCUSD"]}]}}))
ws.settimeout(12); n=0; t0=time.time()
while time.time()-t0<12:
    d=json.loads(ws.recv())
    if d.get("type")!="subscriptions": n+=1
print("DATA MESSAGES:", n)   # expect > 0
EOF

# 2. Back up .env, then repoint the WS URL only
sudo cp /opt/btcbot/.env /opt/btcbot/.env.bak.$(date -u +%Y%m%dT%H%M%SZ)
sudo sed -i 's#^DELTA_WS_URL=.*#DELTA_WS_URL=wss://socket-ind.testnet.deltaex.org#' /opt/btcbot/.env
sudo grep '^DELTA_WS_URL' /opt/btcbot/.env

# 3. Restart the bot and confirm real data within ~3 minutes
sudo systemctl restart btcbot.service
sleep 180
journalctl -u btcbot --since '-3m' | grep snapshot_writer | tail -3
# EXPECT quotes=N/N with N>0, NOT 0/0
```

**Risk of recovery.** Low. One `.env` line, backed up first; `restart` is the
documented recovery for a stalled writer. Worst case the new URL also fails and
the state is no worse than now — it is already collecting nothing. The stale-row
cleanup is a **separate, later** decision and must not be bundled in.

**Effort.** ~10 minutes including verification.

---

## B2 — Forward evidence collection dead (unmigrated ledger)

**Root cause.** The production ledger at `/opt/btcbot/research/evidence_library/
ledger.db` was **never migrated** — no `run_kind` column, no `governance_receipts`,
no `pairwise_r` (F8), and the file has not been written since **2026-06-26**
(F7). The pull on 2026-07-18 brought `lifecycle.py`'s C1 invariant, which reads
`r["run_kind"]` unconditionally. `run_daily` calls `report.py --check` as a
precondition, so every run since aborts with `IndexError: No item with that key`.

**Why the migration "completed successfully" but did not happen.** CLAUDE.md
§18 records Increment 5 as *"Reported outcome: completed successfully"* while
itself flagging that no transcript was captured, rating it `EXECUTED
(ATTESTED)` rather than DEMONSTRATED. That caveat is now resolved **negatively**:

- The ledger file is untouched since Jun 26 (F7) — a migration writes it.
- Every daily backup Jul 01 → Aug 06 is byte-identical in size, 86,016 (F7) —
  the ledger has been frozen across the entire claimed migration window.
- **No `ledger.pre-*.db` backup exists on the host.** All three migration
  scripts create one unconditionally before `--commit`.
- The only `pre-run_kind-migration` artifact present is a **git-tracked
  `.audit.txt`** (F10) that arrived by checkout — it is dev-machine evidence
  that was mistaken for host evidence.
- The scripts *are* present and runnable (F9), so this was not a missing-tooling
  failure. It was simply never run here.

A second clone exists at `/home/ubuntu/bitcoin-paper-trading-bot` (HEAD
`c14ba38`) — it has **no ledger at all**, so the migration was not
mis-targeted there either.

**Earliest observable failure:** first `btcbot-fwd-hg` run after the 2026-07-18
pull. Journal retention (14 days) starts at Jul 24, but `run_log` proves the
last successful collection was **2026-07-17** (F11).

**Timeline**

| UTC | Event |
|---|---|
| 06-26 06:59 | Last write to `ledger.db` |
| **07-17** | **Last successful forward-evidence collection** (all 5 signals) |
| 07-18 07:12 | Pull → `7fd201e`, introducing the `run_kind` invariant |
| 07-18 → 08-06 | `btcbot-fwd-hg` fails daily — **20 days** |
| 07-26 | Increment 5 recorded as "Production Migration complete" |

**Impact.** **20 consecutive days** of lost H5, G1, G3, TSMOM and IVRV forward
observations. This includes the entire post-activation life of `g3_atr_expansion`
— ATR was switched on but has produced **nothing**, so the activation card's
Phase E sign-off would have caught this on 2026-07-19 had it been performed.

**Current production risk.** 🔴 High and permanent-accruing. Forward evidence is
calendar-day data; a missing day cannot be reconstructed at any cost.

**Data recoverable?** **No.** Forward observations require the data to have been
captured on the day. The 20 days are gone. The *ledger* itself is intact and
migratable — only the collection window is lost.

**Deployment depends on it?** **Partially, and in the opposite direction to
intuition.** Deploying will not fix it (the migration is a separate action), and
deploying *without* migrating carries the same crash forward. Increment 3's
`GV-SCHEMA` fingerprint — which exists precisely to convert this crash into a
named diagnostic — is in the undeployed code.

**Recovery procedure**

```bash
# 0. Independent backup FIRST (the migration scripts also make their own)
sudo -u btcbot cp /opt/btcbot/research/evidence_library/ledger.db \
  /opt/btcbot/research/evidence_library/ledger.manual-pre-migration.$(date -u +%Y%m%dT%H%M%SZ).db
sudo -u btcbot sqlite3 /opt/btcbot/research/evidence_library/ledger.db "PRAGMA integrity_check;"

# 1. Dry-run all three, in order, reading output before proceeding
cd /opt/btcbot/research/evidence_library
for s in migrate_run_kind.py migrate_bootstrap_evidence_admission.py migrate_bootstrap_transition_receipts.py; do
  echo "=== $s (dry-run)"; sudo -u btcbot /opt/miniconda3/envs/btc_bot/bin/python $s --db ledger.db
done

# 2. Commit, same order, one at a time
#    (append --commit to each; re-verify between steps)

# 3. Verify
sudo -u btcbot /opt/miniconda3/envs/btc_bot/bin/python report.py --check
sudo -u btcbot /opt/miniconda3/envs/btc_bot/bin/python -c \
  "import receipt_ledger as r; print(r.verify_chain('ledger.db'))"
sudo systemctl start btcbot-fwd-hg.service && systemctl is-failed btcbot-fwd-hg.service
```

**Risk of recovery.** **Medium — the highest in this report.** This mutates the
only irreplaceable governance artifact (ADR-0001 §12 BK1). Mitigations: the
sequence is rehearsed (`tests/test_migration_harness.py`, 12 tests), proven
idempotent, and each script takes its own backup; **`TD-P1-002` applies** — if
any restore becomes necessary, checkpoint and clear `-wal`/`-shm` first.
Note the **dev-machine rehearsal ran against a *dev* ledger** — production
content is not identical, so the dry-run output must be read, not skimmed.

**Effort.** 30–45 minutes with careful verification between steps.

---

## B3 — Git authentication failing

**Root cause.** The remote is **HTTPS** on a **private** repository, and the host
has **no non-interactive credential path whatsoever**: no `credential.helper`
(local or global), no `~/.git-credentials`, no `.netrc`, and **no `.ssh`
directory for the `btcbot` user at all** (F15). Every historical pull in the
reflog was performed **interactively**, with an operator supplying a PAT at the
prompt. Under `ssh … 'git fetch'` there is no TTY, so git fails immediately with
`could not read Username`.

This is not a regression or an expired token — it is an **absence of
automation** that has always existed and was masked by the operator being
present at every previous pull.

**Earliest observable failure:** the first non-interactive fetch ever attempted
— Step 0e of this deployment.

**Impact.** No unattended deployment is possible. Directly causes B6.

**Current production risk.** 🟡 Medium — no runtime impact, but it blocks every
fix in this report that requires new code.

**Data recoverable?** N/A.

**Deployment depends on it?** **Yes — absolutely blocking.** No code can reach
the host until this is resolved.

**Recovery procedure — three options, in preference order**

| Option | Mechanism | Trade-off |
|---|---|---|
| **A. Deploy key (recommended)** | `ssh-keygen -t ed25519` as `btcbot`; add public key as a **read-only deploy key** on the GitHub repo; `git remote set-url origin git@github.com:MechStream006/bitcoin-paper-trading-bot.git` | Least privilege, no expiry, scoped to this one repo |
| B. Fine-grained PAT + store helper | `git config credential.helper store` + write `~/.git-credentials` | Token sits in plaintext on disk; expires |
| C. Keep interactive | Operator types PAT each pull | No automation; perpetuates this failure |

**Risk of recovery.** Low for A — additive, revocable from GitHub, and
read-only. Requires a GitHub UI action only you can perform.

**Effort.** ~10 minutes.

---

## B4 — Working tree drift — **INTENTIONAL, and already reconciled upstream**

**Root cause.** Not a defect. The drift is exactly one semantic change —
`g3_atr_expansion.enabled: false → true` (F16) — which is **precisely what
`docs/ops/atr-activation-card-2026-07-19.md` prescribes**:

- Card Phase A: `cp "$CFG" "$CFG.bak.$STAMP"` → host has `.bak.20260628` ✅
- Card Phase C: `sed -i 's/^    enabled: false$/    enabled: true/'` → host content matches ✅

A sibling `btcbot-fwd-hg.service.bak.20260628` in `/etc/systemd/system` shows a
single deliberate operator session that day (H4).

**Critically, `main` already carries `enabled: true`** (F17), with a comment
stating the runbook's *"required post-activation step is that this repo file
match the server, so a redeploy cannot silently revert ATR to disabled."* The
reconciliation was done correctly in the repository; the host simply never
received it.

**Impact.** None functionally. But it **mechanically blocks `git pull`**: commit
`593edae` modifies this file, and git refuses to overwrite local modifications.

**Current production risk.** 🟢 Low.

**Data recoverable?** N/A.

**Deployment depends on it?** **Yes — mechanically.** The pull will fail until
the working tree is clean.

**Recovery procedure**

```bash
# Prove the local edit is semantically identical to what main will deliver
sudo -u btcbot git -C /opt/btcbot fetch origin
git -C /opt/btcbot diff origin/main -- research/forward_evidence/collection_config.yaml
# EXPECT: only comment-text differences; `enabled: true` on both sides.
# ONLY THEN discard the local edit so the pull can proceed:
sudo -u btcbot git -C /opt/btcbot checkout -- research/forward_evidence/collection_config.yaml
```

⚠️ **Do not run the `checkout --` before the diff confirms `enabled: true` on
the incoming side.** If that ever changes, this command silently disables ATR.
The `.bak.20260628` is the safety net and must not be deleted.

**Risk of recovery.** Low **with** the diff gate; **high without it** — this is
the one step in this report that can silently destroy a live activation.

**Effort.** ~2 minutes.

---

## B5 — Monitoring and API units never installed

**Root cause.** `/etc/systemd/system/` contains **no** `btcbot-healthcheck`,
**no** `btcbot-certify`, **no** `btcbot-api` unit, and there is **no `venv-api`**
(F13). These were never installed — `install.md` steps 5/6/7 were never
performed on this host (H3). The earlier `systemctl is-active` reading of
`inactive` was misleading: for a non-existent unit systemd reports `inactive`,
and only `is-enabled` reveals `not-found`.

**This is the enabling condition for the entire incident.** The 10-minute
health-check dead-man exists specifically to catch a stalled writer and a
stale backup. Had it been installed, B1 would have alerted within ~10 minutes
instead of running 77 hours unobserved.

**Note on PROD-2.** The blocker that motivated the entire Option A fix — 144
false `label_store` alerts/day — **could never have fired on this host**,
because the health-check timer does not exist here. The fix remains correct and
necessary (it would fire the moment the timer is installed), but its urgency was
overstated in the deployment plan, which verified units in `deploy/` and assumed
`deploy/` ≡ host.

**Earliest observable failure:** none — a unit that does not exist emits nothing.
That is the defect.

**Impact.** No automated health monitoring, no daily certifier, no read-only API.
Two multi-week outages went undetected.

**Current production risk.** 🟠 High — every future failure is equally silent.

**Data recoverable?** N/A.

**Deployment depends on it?** No, but deploying **without** installing the
health-check leaves the platform exactly as blind as it was.

**Recovery procedure**

```bash
sudo cp /opt/btcbot/deploy/btcbot-healthcheck.{service,timer} /etc/systemd/system/
sudo cp /opt/btcbot/deploy/btcbot-certify.{service,timer}     /etc/systemd/system/
sudo systemctl daemon-reload
# Dry-run BEFORE enabling the timer, so a WARNING storm cannot start unattended
sudo -u btcbot /opt/btcbot/venv/bin/python /opt/btcbot/scripts/healthcheck.py --no-telegram
sudo systemctl enable --now btcbot-healthcheck.timer btcbot-certify.timer
```

⚠️ **Sequencing hazard:** installing the health-check **before** deploying the
Option A fix reintroduces PROD-1/PROD-2 for real. Either deploy the code first,
or install the timer **without** `--alert-on-warning` initially.
`btcbot-api.service` additionally needs a `venv-api` built — treat as separate.

**Risk of recovery.** Low-to-medium; the alert-storm hazard is real but fully
avoided by ordering.

**Effort.** ~15 minutes (API/venv-api: +30).

---

## B6 — Host 21 commits behind

**Root cause.** A direct consequence of **B3**. The last successful pull was
**2026-07-18 07:12 UTC** (F14) — the last time an operator was present to type a
PAT. Everything merged since (21 commits, including the entire Program P1
release and Phases 0–4) has never reached the host.

**This invalidates the deployment plan's central premise.** The plan computed
the delta as `program-p1-closed..HEAD` = "four changed production files,
53 insertions." The host is not at `program-p1-closed` — it is **5 commits
before it**. The true delta is:

```
git diff --stat 7fd201e..main -- src/ scripts/ deploy/ config/ requirements.txt
  35 files changed, 3502 insertions(+), 34 deletions(-)
```

including the entire `src/api/` package (9 new files),
`src/observability/metrics_exporter.py` (499 lines), and changes to
`src/runtime/lifecycle.py` and `src/main.py` — the latter two in the **live
collection path**. `requirements.txt` also gains `prometheus_client`, so the
"no dependency change" conclusion is false for this host.

**Earliest observable failure:** 2026-07-22, when `593edae` merged unpulled.

**Impact.** Host runs 3-week-old code lacking every P1 safety mechanism —
including `GV-SCHEMA`, which would have named B2's root cause instead of
crashing on it.

**Current production risk.** 🟠 High — and the risk *increases* with the size of
the eventual catch-up deployment.

**Data recoverable?** N/A.

**Deployment depends on it?** It **is** the deployment — but the authorized
plan does not describe this deployment. **The Phase 4 D0 plan and runbook must
be re-authored against the true `7fd201e..main` delta before execution.**

**Recovery procedure.** Resolve B3 → resolve B4 → re-audit the real delta →
produce a new deployment plan → re-authorize. Do **not** execute the existing
runbook.

**Risk of recovery.** **Medium-to-high** — this is a 3,502-line delta touching
the live collection path, `requirements.txt`, and adding a metrics exporter that
starts by default. It deserves its own plan, not an amendment to the current one.

**Effort.** Re-audit + new plan: 2–4 hours. Execution: 1 hour.

---

## Answers to the specific questions asked

**Why WebSocket collection stopped while the service remained healthy.**
Two independent causes stacked. The *stop* was a platform mismatch — `.env`
still points the WS at Delta **Global** while REST was migrated to **India**;
Delta stopped serving India products on the Global socket at 2026-08-03
12:33:07Z, and its documented behaviour is to ACK the subscription and publish
nothing. The *silence* was a supervisor defect: health is derived from FSM state,
so every reconnect returns the component to `HEALTHY` having received zero
messages — the same log line reporting the restart showed `last_msg=192982.0s`.

**Why `btcbot-healthcheck.timer` and `btcbot-certify.timer` are disabled.**
They are not disabled — **they were never installed.** No unit file exists for
either. `systemctl is-active` reports `inactive` for a non-existent unit, which
is what made this look like a configuration choice.

**Why the production ledger migration never completed.**
It was never executed against `/opt/btcbot`. The ledger is untouched since
2026-06-26, every daily backup is byte-identical in size across the claimed
window, and no `ledger.pre-*.db` backup exists — which the scripts create
unconditionally. The only migration artifact present is a **git-tracked
`.audit.txt` that arrived by checkout**. CLAUDE.md §18's own `EXECUTED
(ATTESTED)` caveat was well-placed; the attestation is now falsified.

**Why the host is 21 commits behind.**
Because git authentication is interactive-only (B3) and no operator has pulled
since 2026-07-18 07:12 UTC.

**Whether the working tree drift is intentional or accidental.**
**Intentional and documented.** It matches the ATR activation card's Phase A/C
byte-for-byte, including the operator-made `.bak.20260628`. `main` already
carries the same `enabled: true`, so it is not even a divergence — only an
undelivered reconciliation.

**Why Git authentication is failing on the EC2 host.**
There is no non-interactive credential path at all — no helper, no stored
credentials, no `.netrc`, no SSH key for `btcbot`. Every past pull was typed by
hand. Nothing broke; automation never existed.

---

## Recommended recovery order

Ordered so each step is verifiable before the next, and so no step can mask
another's failure.

| # | Action | Blocker | Why here |
|---|---|---|---|
| **1** | **Repoint `DELTA_WS_URL` + restart `btcbot.service`** | B1 | Stops ongoing, unrecoverable data loss. Independent of everything else. Do first. |
| **2** | Remove the 0-byte `ledger.db` I created | — | Clears my own artifact before ledger work begins |
| **3** | Establish a deploy key | B3 | Unblocks everything downstream |
| **4** | Diff-gate, then clear the config drift | B4 | Mechanically required before pull |
| **5** | **Migrate the production ledger** (backup → dry-run → commit) | B2 | Restores forward collection. Independent of the code deploy. |
| **6** | Verify `btcbot-fwd-hg` succeeds | B2 | Confirms 5 before anything else moves |
| **7** | **Re-audit the true `7fd201e..main` delta; author a new deployment plan** | B6 | The current plan is invalid for this host |
| **8** | Deploy under the new plan | B6 | |
| **9** | Install healthcheck + certify units (post-deploy, so Option A is present) | B5 | Ordering avoids a real alert storm |
| **10** | Decide on the 80,860 stale rows | B1 | Separate governance decision; not urgent |

Steps 1, 2, 3, 5 can each be done today and are individually valuable.
Steps 7–8 need fresh planning and fresh authorization.

## Which items can be fixed independently

| Independent | Coupled |
|---|---|
| **B1** — `.env` + restart, needs nothing else | **B6** requires B3 **and** B4 first |
| **B2** — migration is host-local; scripts already present | **B5** should follow the code deploy, or install without `--alert-on-warning` |
| **B3** — deploy key, no dependencies | |
| **B4** — trivial once diff-gated | |

## Which items require repository changes

| Item | Change | Priority |
|---|---|---|
| `.env.example` | `DELTA_WS_URL` → `wss://socket-ind.testnet.deltaex.org` | High — or the next fresh install repeats B1 |
| `CLAUDE.md` §11 | Retract *"WebSocket … remains valid for public subscriptions on India testnet"* — false since 2026-08-03; record the India testnet WS URL and this outage | High |
| `src/runtime/supervisor.py` | Health must consider `last_msg` age, not FSM state alone | **High — this is a real defect** |
| `docs/ops/deployment-plan-phase4-d0-baseline.md` | §2.1 verified units in `deploy/`, not on the host; §1's delta baseline is wrong for this host | High |
| `CLAUDE.md` §18 Increment 5 | Correct the migration claim from attested-complete to **not performed** | High — governance record accuracy |
| `scripts/healthcheck.py` | Add rate limiting (`R6`, still open) | Medium |

**None of these are needed to stop the bleeding.** B1's fix is `.env`-only.

## Which items require production-only actions

- **B1** — `.env` edit + `btcbot.service` restart
- **B2** — ledger migration (host-local DB; scripts already deployed)
- **B3** — SSH keypair on host (+ a GitHub UI action only you can take)
- **B4** — `git checkout --` on host
- **B5** — copying unit files, `daemon-reload`, `enable --now`; `venv-api` build
- Removal of the 0-byte `ledger.db` I created

## Which items require your approval before execution

**All of them.** Nothing in this report has been executed. Explicitly:

| Needs approval | Why it matters |
|---|---|
| 1. Remove the 0-byte `ledger.db` I created | My error; cleanup is still a production modification |
| 2. `.env` WS URL change + `btcbot.service` restart | Restarting the writer; brief gap on an already-dead feed |
| 3. **Production ledger migration** | Mutates the only irreplaceable artifact (BK1). **Highest-risk item here.** |
| 4. Deploy key creation | Grants the host persistent repo read access |
| 5. Discarding the config drift | Can silently disable ATR if the diff gate is skipped |
| 6. Installing healthcheck/certify units | Can start an alert storm if ordered wrongly |
| 7. Any code deployment | Current plan is invalid; needs re-authoring and re-authorization |
| 8. Deleting the 80,860 stale rows | Irreversible data deletion; governance decision |

---

## Corrections to my own earlier reporting

Stated plainly, since both understated the problem:

1. **I reported the collection outage as ~23h 38m.** It is **77h+**. I anchored
   on the last *written row* (2026-08-05 18:06); the last *received tick* was
   2026-08-03 12:33:07. The 29.5-hour gap between them is the stale-write window,
   which I initially misread as healthy collection.
2. **I reported forward-evidence failure as "14+ days" from journal evidence.**
   `run_log` shows the true last success was **2026-07-17** — **20 days**.
   Journal retention (14 days) truncated the view.

---

| Role | Name | Date |
|---|---|---|
| Investigation | Claude Opus 5 | 2026-08-06 |
| Recovery authorisation | **Rohith Suresh** | **_____ PENDING _____** |

---

# §10 — RESOLUTION (2026-08-07)

Executed under standing operating authority. Read-only investigation first,
then repair, each step backed up and verified. **B1 and B2 are RESOLVED; B5 is
partially resolved. B3, B4 and B6 remain open pending a credential.**

## B1 — Collection dead 86h → RESOLVED 02:33 UTC

Root cause confirmed from the host: `.env` had REST on India
(`cdn-ind.testnet.deltaex.org`) and WS on **Global**
(`testnet-socket.delta.exchange`). Delta stopped serving India products over
the Global socket at 2026-08-03 12:33:07 UTC.

Host-side probe, identical payload, same network path:

| Endpoint | Symbols with data | Messages / 20s |
|---|---|---|
| `wss://testnet-socket.delta.exchange` (Global) | **0 / 4** | **0** |
| `wss://socket-ind.testnet.deltaex.org` (India testnet) | **4 / 4** | **17** |

**Change:** one line in `.env`. Backed up first to `.env.bak.20260807T023344Z`
(md5-verified identical), diff confirmed to touch exactly that line, then
`systemctl restart btcbot.service`.

| Metric | Before | After |
|---|---|---|
| `snapshot_writer` | `quotes=0/0` | **`quotes=66/66`** |
| `coverage_pct` | `0.0` | **`1.0`** |
| `stale_count` | `66` | **`0`** |
| distinct `exch_ts` | 1 (frozen) | **moving** |

**Rollback:** `sudo cp /opt/btcbot/.env.bak.20260807T023344Z /opt/btcbot/.env && sudo systemctl restart btcbot.service`

**Not recovered:** 86 hours of ticks. Unrecoverable by nature.

## B2 — Forward evidence dead 21 days → RESOLVED 02:39 UTC

Confirmed the migration had never run against `/opt/btcbot`: ledger untouched
since 2026-06-26, no `ledger.pre-*.db` backup present, and the only
`pre-run_kind-migration` artifact was a **git-tracked `.audit.txt`** that
arrived by checkout. CLAUDE.md §18's `EXECUTED (ATTESTED)` caveat is now
**falsified** — Increment 5 did not take effect here.

**Procedure:** `PRAGMA wal_checkpoint(TRUNCATE)` (TD-P1-002 discipline) →
independent md5-verified backup → dry-run → commit, per script, in order.

| Step | Result |
|---|---|
| `migrate_run_kind.py` | 71 rows → 36 confirmatory / 35 discovery |
| `migrate_bootstrap_evidence_admission.py` | 13 receipts (9 admit / 4 reject) |
| `migrate_bootstrap_transition_receipts.py` | 13 receipts |
| `report.py --check` | **OK** — 35 families, 9 / 22 / 4 |
| `verify_chain()` | **ok=True, n_receipts=26** |
| `PRAGMA integrity_check` | ok |
| Re-run all three | **0 pending — idempotent** |
| `btcbot-fwd-hg.service` | **`Result=success`, exit 0, all 5 signals ok** |

`g3_atr_expansion` collected for the first time since activation.

**Rollback:** `sudo -u btcbot cp .../ledger.manual-pre-migration.20260807T023716Z.db .../ledger.db`
(plus three per-script backups).

**Not recovered:** 21 calendar days of forward observations.

## B5 — Monitoring absent → PARTIALLY RESOLVED

`btcbot-healthcheck.timer` installed, enabled, **active on a 10-minute
cadence**; first run exit 0, no spurious alert.

Verified safe to install *before* the code deploy: the host at `7fd201e` has
**zero** label-store wiring (`grep` → 0 hits in both `backup.py` and
`healthcheck.py`), so **PROD-1/PROD-2 cannot fire here** — those defects
arrived in `c41e592`, which this host never received. `backup.py --all`
exits 0.

`btcbot-certify` **not installed** — `scripts/certify_catchup.py` and both unit
files do not exist at `7fd201e`; they arrive in `593edae`. Blocked on B3.

## Post-recovery verification

| Check | Result |
|---|---|
| `healthcheck.py --no-telegram` | **overall: OK**, all 6 checks OK |
| `db_liveness` | OK — last tick 49s ago |
| `forward_cycle` | OK — fresh, latest 2026-08-06, all 5 signals ok |
| Telegram positive test | **delivered: True** |
| `backup.py --all` | exit **0**; fresh trading + ledger artifacts |
| Integrity — trading / ledger / forward | **ok / ok / ok** |
| Collection sustained | `66/66` across successive cycles |

## Still open

| # | Item | Blocker |
|---|---|---|
| **B3** | Host cannot fetch from GitHub | **Credential — owner action** |
| **B4** | Config drift (intentional ATR activation) | Clears on pull; needs B3 |
| **B6** | Host 21 commits behind | Needs B3 |
| **B5b** | `btcbot-certify` not installed | Needs B3 |
| — | 80,860 stale rows, single `exch_ts` | Separate governance decision |
| — | Supervisor health ignores `last_msg` age | Code fix; needs B3 to deploy |
