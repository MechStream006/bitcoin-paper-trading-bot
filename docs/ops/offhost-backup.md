# Encrypted Off-Host Backup (ADR-0001 §12 BK3)

Closes **BK3**: *"`BACKUP_OFFHOST_CMD` must be configured. Local-only backups
do not survive VPS loss, which is the scenario backups exist for."*

**Status: implemented and verified locally. NOT deployed to production.**
Enabling it in production is a deliberate, gated step — see §8.

---

## 1. Architecture

```
  PRODUCTION HOST (untrusted for secrets)      OFF-HOST TARGET      OPERATOR (offline)
  ─────────────────────────────────────        ───────────────      ──────────────────
  ledger.db ──VACUUM INTO──► backup .db
                                 │
                                 ├─ .manifest.json  (sha256 of plaintext)
                                 │
                          age --recipient age1...        (PUBLIC key only)
                                 │
                                 ▼
                          backup.db.age  ─────rsync────►  backup.db.age
                          .age-manifest.json              .age-manifest.json
                                                                │
                                                                │  disaster
                                                                ▼
                                                       decrypt with identity ◄── AGE-SECRET-KEY-1...
                                                                                  (NEVER on the host)
```

### 1.1 Why asymmetric encryption, not a passphrase

**This is the central design decision.** The host holds only the *public*
recipient key. It can encrypt; it cannot decrypt. The private identity is
never present on the production host at any point.

A symmetric passphrase in `/opt/btcbot/.env` would mean whoever compromises
the host also gets every backup — *precisely the scenario off-host backups
exist to survive*. With an asymmetric recipient, host compromise yields
ciphertext plus an encrypt-only key, and nothing else. The blast radius of a
host compromise stops at the host.

This directly satisfies the requirement to *avoid long-lived plaintext
outside the production host*: nothing readable ever leaves, and the material
needed to make it readable never arrives.

### 1.2 Why `age` and not GPG

| | `age` | GPG |
|---|---|---|
| Recipient handling | `-r age1...` on the command line | requires an imported keyring owned by `btcbot` |
| Persistent state | none | keyring, trustdb, `gpg-agent` |
| Non-interactive systemd | works | agent/tty/`--trust-model always` footguns |
| Config surface | ~zero | large |

For an unattended `Type=oneshot` unit, GPG's persistent-state and agent
requirements are a reliability liability with no compensating benefit here.
`age` is `apt install age` on Ubuntu.

### 1.3 Integrity model — three independent checks

Encryption must not weaken **BK4** (*"integrity is verified via the SHA-256
manifest, not assumed from exit code 0"*). It doesn't; it adds a layer:

| # | Check | When | Needs private key? |
|---|---|---|---|
| 1 | `ciphertext_sha256` | **before** decrypt | **No** |
| 2 | `plaintext_sha256` | after decrypt | Yes |
| 3 | `PRAGMA integrity_check` | after decrypt | Yes |

Check 1 is what makes **routine off-host auditing possible**: the backup
target can be checked for bit-rot or truncation from any machine, by anyone,
without the decryption key ever being handled. Check 2 is the end-to-end
proof that the recovered bytes are identical to what the host produced.

### 1.4 The `.age-manifest.json` sidecar is deliberately cleartext

It carries hashes, row counts, timestamps and the original backup manifest
embedded verbatim — **no secrets** (asserted by
`test_manifest_contains_no_private_key_material`). It is cleartext because
disaster recovery must read it *before* it has anything decrypted; encrypting
it creates a bootstrap problem with zero security benefit.

Embedding the source manifest makes the off-host copy **self-describing**: in
a real disaster the host is gone, and the off-host artifact alone must be
enough to know what it is, when it was taken, and what it should contain.

---

## 2. Files changed

| File | Change |
|---|---|
| `scripts/offhost_backup.py` | **New** — `encrypt` / `verify` / `decrypt` subcommands |
| `tests/test_offhost_backup.py` | **New** — 23 tests (15 real-crypto, skipped when `age` absent) |
| `scripts/backup.py` | **Additive** — `--encrypt` flag (default **off**), fail-closed |
| `.env.example` | `BACKUP_AGE_RECIPIENT`, `BACKUP_OFFHOST_CMD` |
| `docs/ops/offhost-backup.md` | **New** — this document |
| `docs/runbook.md` | Off-host backup + DR sections |

**Deliberately NOT changed: `deploy/btcbot-backup.service`.** See §8 — this
avoids repeating the ATR ordering hazard, where a repo change silently
activated behaviour on deploy.

### 2.1 P1 Increment 6 additions (2026-07-26) — monitoring the transfer leg

The design above (encrypt/verify/decrypt, key custody, integrity model) is
unchanged. This increment closes a gap discovered during Program P1's review
of it: `backup.py`'s off-host transfer failure is deliberately non-fatal (see
§4), and until now that failure had **no persisted signal at all** — only a
stderr line inside a `oneshot` unit's journal, easy to miss for months.

| File | Change |
|---|---|
| `scripts/backup.py` | **Additive** — writes a `.offhost-status.json` sidecar into each backup directory after every off-host transfer attempt (success, failure, or not-configured). Retention (`_apply_retention()`) now also prunes `.age` / `.age-manifest.json` alongside their expired plaintext — closing the accumulation gap in §7 below. |
| `scripts/healthcheck.py` | **Additive** — new `check_offhost_freshness()`, wired into `main()` for both the trading and ledger backup directories. |
| `src/api/services.py` | **Additive** — `runtime_health()` runs the same two checks, preserving CLI/API parity. |
| `tests/test_offhost_backup.py`, `tests/test_backup.py`, `tests/test_healthcheck.py` | Extended with sidecar, retention, and severity-matrix coverage. |

**Not changed in this increment:** `deploy/btcbot-backup.service` (still
gated behind §8's host-side sequence), `scripts/offhost_backup.py` itself
(its encrypt/verify/decrypt contract needed no change), and no new
`ApiSettings` field — the sidecar path is derived from the existing
`backup_dir` / `ledger_backup_dir`, so `tests/test_api.py` required no edit
this time (unlike P1 Increment 1).

**Severity model** (`check_offhost_freshness`, mirrors `check_backup_freshness`'s
existing OK/WARNING pattern but adds a CRITICAL tier — the first on any
backup-family check):

| Condition | Severity |
|---|---|
| No sidecar (off-host never enabled) | INFO |
| Sidecar present, `configured: false` | INFO |
| Configured, but no successful transfer has ever been recorded | **CRITICAL** |
| Last success < 48h ago, latest attempt ok | OK |
| Last success ≥ 48h ago | WARNING |
| Last success ≥ 7 days ago | **CRITICAL** |
| Configured, last success recent, but the *latest* attempt failed | WARNING |

`last_success_at` is carried forward in the sidecar across runs (not
overwritten by a failed attempt), so a host that attempts and fails every
day still shows growing staleness rather than a perpetually-fresh timestamp.
This is Tier 1 of the three-tier model in §7 — it proves the transfer
*command* succeeded, not that the bytes at the target are intact. Tiers 2
(remote `verify`) and 3 (decrypt drill) remain manual/deferred; see §7 and
§9.

---

## 3. Key management

### 3.1 Generating the keypair (once, on a trusted machine — NOT the host)

```bash
age-keygen -o btcbot-backup-identity.txt
# Public key: age1m882un7hzz30ht090urm07t2zwr6x6qhs63fx3n08mzs4jmme9tqyr4tkp
```

The file contains the **private identity** (`AGE-SECRET-KEY-1...`). The
comment line contains the **public recipient** (`age1...`).

### 3.2 Where each half lives

| Material | Location | Never |
|---|---|---|
| Public recipient `age1...` | `/opt/btcbot/.env` as `BACKUP_AGE_RECIPIENT` | — |
| Private identity `AGE-SECRET-KEY-1...` | Password manager **+** printed paper copy in a physically separate location | **Never on the production host. Never in git. Never in `.env`. Never pasted into a chat.** |

**Minimum two independent copies of the private key**, because §3.4.

The code enforces the boundary rather than trusting discipline:
`validate_recipient()` **rejects** a value starting with `AGE-SECRET-KEY-`
with an explicit "rotate this key immediately: it must be considered
compromised". Pasting the private key where the public one belongs is the
single most damaging plausible mistake, and it is made impossible rather than
merely documented.

### 3.3 Host configuration

```bash
# /opt/btcbot/.env
BACKUP_AGE_RECIPIENT=age1m882un7hzz30ht090urm07t2zwr6x6qhs63fx3n08mzs4jmme9tqyr4tkp
BACKUP_OFFHOST_CMD=rsync -az --timeout=120 {file} {manifest} backup@target:/srv/btcbot-backups/
```

`{file}` and `{manifest}` resolve to the **ciphertext** and its
`.age-manifest.json` when `--encrypt` is used — never the plaintext DB
(asserted by `test_offhost_cmd_receives_ciphertext_not_plaintext`).

### 3.4 The irreversible risk — state it plainly

**If the private key is lost, every off-host backup is permanently
unrecoverable.** No recovery path, no vendor, no brute force. For
`ledger.db` — which **BK1** calls the only truly irreplaceable artifact —
losing the key is equivalent to losing the ledger.

This is a real risk *added* by encryption, and it is the reason for:

- **two independent copies** of the identity (digital + paper),
- a **quarterly decrypt drill** (§7) — verifying a ciphertext hash proves it
  is *intact*, **not** that it is *decryptable*. Only a real decrypt proves
  the key still works. The `verify` subcommand prints this caveat every run
  so it cannot be quietly forgotten.

### 3.5 Key rotation

Each `.age-manifest.json` records the `recipient` used, so it is always
answerable which key decrypts which artifact. To rotate: generate a new
keypair, update `BACKUP_AGE_RECIPIENT`, and **retain the old identity for as
long as any backup encrypted to it is still within retention** (up to 365
days under the existing monthly rule). Do not destroy an old key on the day
you rotate.

---

## 4. Implementation notes

**Fail-closed everywhere.** If encryption is requested and fails for any
reason — missing recipient, missing binary, `age` error — the off-host
transfer is **skipped entirely** and `run_backup` returns 1. It never falls
back to shipping plaintext. A silent downgrade to unencrypted transfer would
be the worst possible outcome: it looks like success while defeating the
entire control. Asserted by
`test_missing_recipient_fails_closed_and_skips_transfer`.

**Atomic writes.** Both encrypt and decrypt write to a `.partial` file and
`replace()` on success. A crash mid-operation cannot leave a truncated
artifact that later looks like a valid backup — a silently-corrupt backup is
worse than an obviously-missing one.

**The identity is passed as `--identity <file>`, never inline.** Process
arguments are world-readable via `/proc` on Linux; an inline key would be
exposed to every local user for the process lifetime.

**Local plaintext backups are retained.** BK3 governs what *leaves* the host.
The on-host plaintext backup remains the fast restore path (proven at 0.428 s
in the Item 3 drill) and is unaffected.

---

## 5. Verification results

Full suite: **23 passed** with a real `age` v1.2.1 binary; **8 passed,
15 skipped** without it (CI-safe). `tests/test_backup.py`: **28 passed,
unmodified** — no regression to existing backup behaviour.

### End-to-end DR drill on the real governance ledger

Performed against the actual `ledger.db` backup produced in the Item 3 drill:

| Step | Result |
|---|---|
| Encrypt real ledger backup (168 KB) | `ledger_20260719_050802.db.age` |
| Ciphertext header | `age-encryption.org/v1` |
| `SQLite format 3` present in ciphertext | **False** |
| Leaks `governance_receipts` / `state_transitions` / `offline-corpus` / `validated` | **False** on all four |
| `verify` **without** private key | OK — row counts readable from manifest |
| Decrypt with offline identity | **0.188 s** |
| `plaintext_sha256` match | ✅ |
| `PRAGMA integrity_check` | `ok` |
| **SHA-256 recovered vs. original** | **IDENTICAL** (`0a3a82af…`) |
| `receipt_ledger.verify_chain()` | `ok=True, n_receipts=26` |
| `report.py --check` | exit 0, 35 families |
| Decrypt with **wrong** key | fails; no partial output written |

The last two rows are the ones that matter: **BK2's exact criteria pass on a
ledger that was encrypted, shipped, and decrypted** — and a wrong key fails
closed.

---

## 6. Disaster recovery procedure

**Scenario: the VPS is gone.** You have the off-host backup target and the
private identity.

```bash
# --- 0. Prerequisites on the recovery machine ---------------------------
sudo apt-get install -y age sqlite3
git clone <repo> btcbot && cd btcbot

# --- 1. Retrieve artifacts from the off-host target ---------------------
rsync -az backup@target:/srv/btcbot-backups/ledger_*.db.age            ./recover/
rsync -az backup@target:/srv/btcbot-backups/ledger_*.age-manifest.json ./recover/

# --- 2. Verify BEFORE using the key (works without it) ------------------
python scripts/offhost_backup.py verify ./recover/ledger_<ts>.db.age

# --- 3. Restore the private identity ------------------------------------
#     From the password manager, or retype from the paper copy.
#     Write to a file; never pass it as a command-line argument.
chmod 600 identity.txt

# --- 4. Decrypt (verifies both hashes + integrity_check automatically) --
python scripts/offhost_backup.py decrypt ./recover/ledger_<ts>.db.age \
    --identity identity.txt --out ./recover/ledger.db

# --- 5. Verify governance BEFORE trusting the ledger (BK2) --------------
cd research/evidence_library
python -c "import receipt_ledger; print(receipt_ledger.verify_chain('../../recover/ledger.db'))"
python -c "
from pathlib import Path; import report
print(report.run_check(Path('registry.yaml'), Path('../../recover/ledger.db'), Path('thresholds.yaml')))
"
# REQUIRED: verify_chain ok=True  AND  run_check exit 0.
# If either fails, STOP and escalate — do not put this ledger into service.

# --- 6. Put into service ------------------------------------------------
cp ./recover/ledger.db research/evidence_library/ledger.db

# --- 7. Repeat 2-5 for the other three databases ------------------------
#     btc_trading.db, forward_evidence.db, evidence_forward.db
#     Then follow docs/runbook.md "Restore from backup", including the
#     post-restore migrate.py --dry-run check.
```

**Recovery order matters.** Restore `ledger.db` **first** and verify its
chain before anything else. Per **BK1** it is the only irreplaceable
artifact; the other three are re-collectable or re-ingestable. If the ledger
cannot be recovered, that is the incident — the rest is cleanup.

### 6.1 `.env` disaster recovery (ADR-0001 §20.3 — Program P1 Increment 7)

`.env` holds Delta API keys and Telegram tokens. It is **not** covered by
the automated backup mechanism above — `scripts/backup.py` never reads or
copies it, deliberately, since it is a secrets file rather than a database.
Recovering it after host loss is a separate, manual, low-frequency
procedure:

```bash
# --- On the current host, whenever .env changes (not on a schedule) -----
age -r "$BACKUP_AGE_RECIPIENT" -o env.age .env
# Copy env.age off-host by hand (same target as the ledger backups, or
# elsewhere — it is small and rare enough not to need automation).
# NEVER commit env.age to git; it is ciphertext but the recipient is
# already public in .env.example, so treat it with the same care as .env.

# --- On a recovery host, with the identity in hand -----------------------
age --decrypt --identity identity.txt --output .env env.age
chmod 600 .env
chown btcbot:btcbot .env
```

**Why this stays manual, not folded into `scripts/backup.py --all`:**

1. `.env` changes rarely (a key rotation, a new integration) — a daily
   automated encryption of a file that changes maybe once a quarter adds a
   scheduled write of secret-derived ciphertext for no operational benefit.
2. Automating it would mean `backup.py` reads a secrets file on every run.
   The script's entire threat model today is that it only ever touches
   SQLite databases; keeping `.env` out of its reach is a smaller attack
   surface than adding a "backs up secrets too" code path.
3. The same `age` recipient and identity used for database backups can be
   reused here safely — encrypting `.env` reveals nothing about it beyond
   what encrypting a database backup already reveals (the fact that
   *something* was encrypted to that recipient).

This procedure uses the same key custody rules as §3: the identity that
decrypts `env.age` is the same one that decrypts ledger/database backups,
and is never present on the production host.

---

## 7. Operational impact

| Dimension | Impact |
|---|---|
| Runtime | **None.** `btcbot.service` is untouched; backup is a separate oneshot |
| Backup duration | +~0.2 s for the ledger; roughly linear in size |
| Disk | Ciphertext ≈ plaintext + ~200 bytes, alongside the existing plaintext backup (~2× the backup dir) |
| Network | Unchanged volume; `age` output is not compressed, so a compressing transport gains less |
| New dependency | `age` binary (`apt install age`) |
| New failure mode | Backup exits 1 if encryption fails (**intended** — fail-closed) |
| Recurring obligation | **Quarterly decrypt drill** (§3.4) |

**Retention asymmetry — resolved (P1 Increment 6, 2026-07-26):**
`_apply_retention()` now prunes `.age`/`.age-manifest.json` alongside their
expired `.db`/`.manifest.json`. On-host ciphertext no longer accumulates
unbounded once `--encrypt` is enabled. The fix landed in the same commit as
the monitoring signal below and must be deployed **before** `--encrypt` is
ever added to `deploy/btcbot-backup.service`'s `ExecStart` — see §8.

---

## 8. Deployment gating — not enabled by default

`deploy/btcbot-backup.service` still runs `backup.py --all` **without**
`--encrypt`. Deploying this code changes production behaviour **not at all**.

This is deliberate, and it is the lesson from the ATR ordering hazard: a repo
change that silently activates on deploy is a hazard. If `--encrypt` were
added to the unit now, the first post-deploy backup would **fail closed**
(exit 1) on every DB, because `BACKUP_AGE_RECIPIENT` would not yet exist on
the host — turning a security improvement into a backup outage.

Enabling is a deliberate host-side sequence, **in this order**:

1. Generate the keypair off-host (§3.1); store the identity in two places.
2. Provision the off-host target; set `BACKUP_OFFHOST_CMD`.
3. Add `BACKUP_AGE_RECIPIENT` to `/opt/btcbot/.env`.
4. `apt-get install -y age`.
5. **Test manually first:**
   `sudo -u btcbot /opt/btcbot/venv/bin/python scripts/backup.py --ledger --encrypt`
6. Verify the artifact landed off-host and `verify` passes.
7. **Only then** add `--encrypt` to `ExecStart` in
   `deploy/btcbot-backup.service`, `daemon-reload`, and run once manually.
8. Perform a full decrypt drill (§6) before considering BK3 closed.

**BK3 is not closed until step 8 passes on real production artifacts.**

---

## 9. Open items

1. **Off-host target not yet provisioned** — ADR §20.1. This implementation
   is target-agnostic (`BACKUP_OFFHOST_CMD` is any shell command: rsync, S3
   CLI, rclone), so the choice does not block the code. **Still open** —
   this is Rohith's host-side decision (Program P1 Increment 6 recommends
   append-only-key object storage; see the Increment 6 plan/report).
2. **`.env` disaster recovery** — ADR §20.3. **Resolved (Program P1
   Increment 7, 2026-07-26)**: documented at §6.1 above. Deliberately
   remains a manual, low-frequency procedure — not folded into
   `scripts/backup.py`'s automated path; see §6.1 for why.
3. **Retention for `.age` artifacts** — resolved, §7 (P1 Increment 6).
4. **Drill cadence** — quarterly proposed (ADR §20.4, still unconfirmed);
   fold the decrypt drill into the same session as the BK2 restore drill.
5. **Remote `verify` on a schedule (Tier 2 of the monitoring model, §7)** —
   the `verify` subcommand needs no private key and could run weekly against
   the off-host target with no code change, but no timer invokes it yet.
   Deliberately deferred out of P1 Increment 6 to keep its deployment
   footprint at zero new systemd units; the Tier 1 sidecar (§2.1) closes the
   more urgent "silent forever" gap. Revisit once a target is provisioned
   (item 1).
