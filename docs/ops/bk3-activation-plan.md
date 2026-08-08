# BK3 — Activation Plan

| Field | Value |
|---|---|
| **Status** | ⬜ **PREPARED — NOT ACTIVATED.** Awaiting owner approval |
| **Completed** | `age` 1.1.1 installed and verified on production (§2) |
| **Blocked on** | key custody decision (§3) · target provisioning (§4) |
| **Date** | 2026-08-08 |

---

## 1. Verified host state

| | **Production** (EC2 ap-south-1) | **Canonical** (local dev, Windows) |
|---|---|---|
| Platform | Ubuntu 24.04.4 LTS, amd64 | Windows 11 (10.0.26200) |
| `age` | ✅ **1.1.1 installed today** | ❌ absent (`winget`/`choco` available) |
| Existing keys | none | none |
| Backup automation | ✅ `btcbot-backup.timer`, daily 02:00 UTC | ❌ **none — manual only** |
| `BACKUP_AGE_RECIPIENT` | unset | unset |
| `BACKUP_OFFHOST_CMD` | unset | unset |
| Off-host sidecars | 4 × `not_configured` | 7 × `not_configured` |
| Egress 443 | OK | OK |
| Privilege | passwordless sudo | UAC elevation required |

### 1.1 The finding that shapes this plan

**The irreplaceable data and the backup automation are on different machines.**

| Asset | Canonical | Production |
|---|---|---|
| Evidence ledger | **27 receipts** | 26 receipts |
| Feature Store | **17 receipts, 17 Active specs** | 0-byte file |
| Label Store | **8 receipts, 4 Active specs** | 0-byte file |
| Dataset Builder store | **2 specs, 1 sealed dataset** | absent |
| Corpus | **548 MB, 274 partitions** | present |
| Daily automated backup | **none** | yes |

Activating BK3 only on production would replicate the *trading* database and a
ledger that is one receipt behind, while leaving the Feature Store, Label Store,
Dataset Builder store and the sealed dataset — **everything Phase 4 produced** —
on a workstation with no automated backup and no replication.

**BK3 must cover the canonical host, or it does not solve the problem it exists
to solve.**

## 2. `age` installation — DONE on production

Installed from the official Ubuntu 24.04 repository
(`age 1.1.1-1ubuntu0.24.04.3`), not a third-party source.

- **Justification:** required for BK3; no service dependency; universe package.
- **Verification:** `/usr/bin/age` → 1.1.1, `/usr/bin/age-keygen` → 1.1.1;
  encrypt→decrypt round-trip **PASS** using an **ephemeral** key destroyed
  immediately after. Services `active`, 0 failed units, disk unchanged, `.env`
  unchanged, backup unit unchanged.
- **No BK3 key was generated** — per instruction.
- **Rollback:** `sudo apt-get remove --purge age` (nothing depends on it).

**Canonical host: not installed.** `winget install FiloSottile.age` requires UAC
elevation, which cannot be answered from a non-interactive session. Owner action.

## 3. Encryption and key custody

### 3.1 How `age` is used here

Backups are encrypted **to a recipient** (public key). The **identity** (private
key) is never needed to make a backup — only to read one. So the bot host needs
only the recipient, and the identity can live entirely off the host.

That asymmetry is the whole safety property: **compromising the bot host does
not expose any backup.**

### 3.2 Recovery implications — the failure that matters

If the identity is lost after encryption begins, **every off-host artifact
becomes permanently unreadable.** The failure is silent: backups keep
succeeding, the sidecar keeps reporting `ok`, the healthcheck stays green. You
discover it during a disaster, which is the worst possible moment.

This is why key generation is deliberately **not** part of this session's work.

### 3.3 Every secret that must survive host loss

| Secret | Purpose | Where today | Backed up? |
|---|---|---|---|
| **`age` identity** | decrypt every off-host backup | *does not exist yet* | — |
| `DELTA_API_KEY` / `_SECRET` | exchange access | `/opt/btcbot/.env` | ❌ **no** |
| `TELEGRAM_BOT_TOKEN` | alerting | `.env` | ❌ **no** |
| `TELEGRAM_CHANNEL_ID` | alerting | `.env` | ❌ **no** |
| `API_TOKEN` | read-only API auth | `.env` | ❌ **no** |
| EC2 SSH key `bitcoin-key.pem` | host access | operator workstation (`D:\`) | ❌ **no** |
| Object-store credentials | write backups | *do not exist yet* | — |

**`.env` is not covered by any backup path.** `docs/ops/offhost-backup.md` §6.1
documents a `.env` DR procedure using the same `age` recipient, but it is manual
and has never been executed. Restoring databases without `.env` yields a host
that cannot trade, alert, or authenticate.

**A recovery that restores data but not secrets is not a recovery.**

### 3.4 Recommended custody model

**Two-copy, two-medium, zero-on-host:**

1. **Primary** — password manager (1Password / Bitwarden), entry
   `btcbot-backup-age-identity`, containing the identity, its creation date, and
   the matching recipient.
2. **Secondary** — printed or written on paper, stored physically separately
   from the workstation. An `age` identity is one short line; this is practical.
3. **Never** on the EC2 instance, and **never** in the repository (which is
   private but not a secret store).
4. **Record the recipient publicly** — in `.env` on both hosts and in this
   document's activation record. The recipient is not a secret; treating it as
   one causes confusion later.
5. **Verify custody before trusting it:** decrypt a real artifact using *only*
   the stored copy, on a machine that has never held the identity.

**Rejected:** storing the identity on the EC2 host (defeats the purpose),
storing only in the password manager (single point of failure), and encrypting
the identity with another secret (moves the problem).

## 4. Off-host target — comparison

| Option | Durability | Append-only safety | Cost @ ~600 MB + 550 KB/day | Ops burden | Verdict |
|---|---|---|---|---|---|
| **Second EC2** | EBS, single AZ | ❌ deletable by anyone with the key | ~$8–15/mo + EBS | patching, monitoring, a second host to secure | ✗ adds a machine to protect |
| **rsync target** | depends on host | ❌ `rsync --delete` propagates deletion | host cost | SSH keys, host lifecycle | ✗ same problem, plus delete-propagation risk |
| **Another VPS** | provider-dependent | ❌ | ~$5/mo | full host ownership | ✗ |
| **S3 (plain)** | 11 nines, multi-AZ | ⚠️ versioning helps; delete still possible | **< $0.05/mo** | IAM only | ◐ good |
| **S3 + Versioning + Object Lock** | 11 nines, multi-AZ | ✅ **WORM — cannot be deleted or overwritten for the retention period, even with valid credentials** | **< $0.10/mo** | IAM + one-time bucket config | ✅ **recommended** |

### 4.1 Recommendation — S3 with Object Lock (Compliance mode) + Versioning

**Rationale, in the order that matters:**

1. **It matches the data's own semantics.** Every governance chain is
   append-only and hash-linked. A backup target that permits deletion
   contradicts the property being protected. Object Lock is the only option here
   that makes deletion *impossible* rather than *discouraged*.
2. **It survives host compromise.** With a write-only IAM policy
   (`s3:PutObject`, **no** `s3:DeleteObject`), an attacker with full root on the
   bot host can neither read prior backups (they are `age`-encrypted) nor delete
   them (Object Lock). Ransomware against the bot host cannot reach the backups.
3. **No machine to own.** Every other option adds a host that itself needs
   patching, monitoring, and its own backup story.
4. **Cost is negligible** — the T1 governance set is ~550 KB/day; even with the
   548 MB corpus weekly, this is cents per month.
5. **Cross-region** placement (e.g. `ap-southeast-1`) removes the single-region
   dependency that `ap-south-1` currently represents.

**Compliance mode, not Governance mode:** Governance mode can be bypassed by a
user holding `s3:BypassGovernanceRetention`. Compliance mode cannot be bypassed
by anyone, including the root account. Choose a retention period you are willing
to be held to — **90 days** is a reasonable starting point, since storage is
effectively free and the cost of a too-short window is unrecoverable.

**The trade-off, stated honestly:** Compliance-mode objects cannot be deleted
before expiry *even by you*. A mistaken upload of a very large object costs
storage until retention lapses. At these volumes that is negligible, and the
protection is worth far more.

## 5. Activation plan

### 5.1 Prerequisites — owner actions

| # | Action | Why it cannot be automated here |
|---|---|---|
| P1 | Create the S3 bucket with Versioning + Object Lock (Compliance, 90d) in a non-`ap-south-1` region | Requires AWS console/credentials |
| P2 | Create an IAM user/role with `s3:PutObject` **only** (no `Delete`, no `List` needed) | Requires AWS IAM |
| P3 | Generate the `age` keypair; place the identity per §3.4 | **The decision that must not be rushed** |
| P4 | Install `age` on the canonical host (`winget install FiloSottile.age`) | UAC elevation |
| P5 | Decide whether the 548 MB corpus is in scope for T2 | Owner call on cost/benefit |

### 5.2 Commands — production

```bash
# 1. Record the recipient (public key — not a secret)
sudo tee -a /opt/btcbot/.env >/dev/null <<'EOF'
BACKUP_AGE_RECIPIENT=age1<recipient>
BACKUP_OFFHOST_CMD=aws s3 cp {file} s3://<bucket>/prod/ && aws s3 cp {manifest} s3://<bucket>/prod/
EOF

# 2. Single-store dry run FIRST — never start with --all
sudo -u btcbot /opt/btcbot/venv/bin/python /opt/btcbot/scripts/backup.py --ledger --encrypt

# 3. Only after §5.4 passes, change the unit
sudo sed -i 's|backup.py --all|backup.py --all --encrypt|' /etc/systemd/system/btcbot-backup.service
sudo systemctl daemon-reload
```

### 5.3 Commands — canonical host

The canonical host has **no backup automation at all**. Minimum viable coverage
is a scheduled task running the same script:

```powershell
schtasks /create /tn "btcbot-backup" /tr "conda run -n btc_bot python <repo>\scripts\backup.py --all --encrypt" /sc daily /st 02:30
```

This is the higher-value half of BK3 and should not be deferred behind
production.

### 5.4 Verification — the decrypt drill is mandatory

```bash
python scripts/offhost_backup.py verify  <artifact>.age
python scripts/offhost_backup.py decrypt <artifact>.age --identity <identity> --out /tmp/r.db
sqlite3 /tmp/r.db "PRAGMA integrity_check;"
python -c "import sys;sys.path.insert(0,'research/evidence_library');import receipt_ledger as r;print(r.verify_chain('/tmp/r.db'))"
```

**Pass condition:** `integrity_check: ok` **and** `verify_chain ok=True` with the
expected receipt count (ledger 27 canonical / 26 production, feature 17,
label 8). The chain check is the one that matters — bytes can survive while the
chain does not.

**Custody proof:** perform the decrypt using only the stored identity copy, on a
machine that has never held it.

### 5.5 Expected evidence

- `<store>_<ts>.db.age` + `.age-manifest.json` in the backup directory
- `.offhost-status.json` → `status: ok`, `encrypted: true`, `last_success_at` set
- `healthcheck.py` → `offhost_backup` moves INFO → **OK**
- Object present in S3 with a retention date set

### 5.6 Failure modes

| Mode | Detection | Response |
|---|---|---|
| Recipient malformed | `backup.py` fails closed; **no plaintext ships** | fix `.env`; local backup unaffected |
| `age` missing at runtime | encryption raises; transfer aborted | reinstall |
| S3 credentials expire | off-host cmd non-zero; sidecar `failed` | healthcheck WARNING at 48h, CRITICAL at 7d |
| Object Lock rejects overwrite | upload error on a duplicate key | timestamped filenames make collisions impossible |
| **Identity lost** | **none — silent** | **unrecoverable.** Only §3.4 prevents this |
| Disk fills from `.age` artifacts | disk check | retention already prunes `.age` with its plaintext |

### 5.7 Rollback

Remove `--encrypt` from the unit, or unset `BACKUP_OFFHOST_CMD`. Local plaintext
backups are unaffected — the off-host leg is deliberately non-fatal. Objects
already in S3 remain until retention lapses (by design).

## 6. The two 0-byte production files

### 6.1 What they are

`/opt/btcbot/research/feature_store/feature_store.db` and
`/opt/btcbot/research/label_store/label_store.db` — **0 bytes, both timestamped
2026-08-08 12:26**, created by my own read-only-intended `sqlite3` probe during
an earlier audit (`sqlite3` creates a database file on open). Both gitignored;
neither was ever deployed.

### 6.2 Are they referenced?

**Only as backup sources.** A repository-wide search finds them in
`scripts/backup.py` — its docstring and the `--label-store` / `--feature-store`
help text — and nowhere else. No collector, scheduler, API, or research process
reads either path. `--feature-store` does not exist in the deployed code at all.

### 6.3 Does leaving them change runtime behaviour?

**Yes, and on a known date.** Verified empirically: a 0-byte file is a valid
SQLite database with 0 tables; `run_backup` succeeds on it (`rc=0`) and produces
a 4 KB artifact plus manifest.

So at **Sun 2026-08-09 02:01 UTC** the next `--all` will back up `label_store`,
create `data/label-store-backups/`, and flip the `label_store_backup` check from
`INFO — not configured on this host` to **`OK`** under the currently deployed
code, which lacks the AF-4 fix.

**That is a false green for a store containing nothing** — precisely AF-4's
failure mode, on production, caused by my probe.

### 6.4 Is removal safe?

**Yes.** They contain no data, no deployed code reads them, and removal restores
the exact pre-probe state in which the healthcheck reported them correctly as
not configured.

### 6.5 Recommended remediation — approval required

```bash
sudo -u btcbot rm -f /opt/btcbot/research/feature_store/feature_store.db \
                     /opt/btcbot/research/label_store/label_store.db
```

- **Justification:** reverts an unintended change of mine and prevents a
  false-green backup signal appearing at 02:01 UTC.
- **Rollback:** none needed — the files are empty; any `sqlite3` open recreates
  one, and the genuine stores are created by their own research processes.
- **Verification:** `healthcheck.py` should report `label_store_backup` as
  `INFO … not configured on this host`, and no
  `data/label-store-backups/` directory should exist after the next run.
- **Timing:** best done before 02:01 UTC. After that, the stale backup directory
  must be removed too.

**Alternative if you prefer no production change:** deploy Phase 4 (which
carries the AF-4 fix) and the signal becomes `INFO … 0 rows` instead of `OK` —
honest, though it still creates a backup directory for an empty store.

## 7. GO / NO-GO

**NO-GO for activation** — three prerequisites are unmet (P1–P3), and two are
owner decisions that cannot be delegated: key custody and target provisioning.

**GO for the preparatory step already taken:** `age` is installed and verified
on production, with no key generated and no configuration changed.
