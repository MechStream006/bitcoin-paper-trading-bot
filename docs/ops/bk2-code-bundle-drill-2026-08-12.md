# BK2 Code-Bundle Recovery Drill — Clean Host — 2026-08-12

| Field | Value |
|---|---|
| **Objective** | Prove the encrypted S3 code bundle replaces the GitHub credential in disaster recovery |
| **Origin** | `bk2-offhost-drill-2026-08-12.md` §6 — the documented procedure could not complete on a clean host because the repository is private |
| **Executed** | 2026-08-12, 09:29:25 → 09:35:05 UTC (**5 min 40 s**) |
| **Drill host** | `i-055923f5910a640be`, t3.small, Ubuntu 24.04.4 LTS, ap-south-1, **no IAM instance profile**, terminated |
| **Production contact** | **None after artifact selection.** No SSH to the production host during execution |
| **Result** | ✅ **PASS** — recovery completed on **two** credentials with GitHub deliberately unreachable |

Companion to `bk2-offhost-drill-2026-08-12.md`, which proved off-host recovery
of the governance ledger and identified the GitHub dependency as its one
undocumented gap. That record stands unaltered; this one closes the gap it
found.

---

## 1. Method

A disposable EC2 instance was provisioned with no IAM role and no credentials
of any kind. `github.com` was **blackholed in `/etc/hosts` before any recovery
step**, and its unreachability was proven in both directions before proceeding.
Artifacts were retrieved from S3 by the non-production principal
(IAM user `Rohith-admin`, `GetObject` only) on the operator workstation and
hand-carried over SSH; the drill host never held AWS credentials. The private
`age` identity was placed, used, and `shred -u -z`'d before termination.

Verification-only: no S3 object was written, deleted, or overwritten; no
production service, database, unit, IAM policy, or bucket setting was touched.

## 2. Artifacts

| | Code bundle | Governance ledger |
|---|---|---|
| Key | `code/btcbot-f340c58a84276e759ab9a4881febce1fbf7b6924.bundle.age` | `prod/ledger_20260812_022125.db.age` |
| Size | 2,882,576 B | 180,456 B |
| VersionId | `uTqzjhdwUyIRkUZPqPgfsWpLhRun3I75` | `Ba0sqj0ux6u3eXImFF1CPl_ufn9Fj057` |
| Object Lock | COMPLIANCE → 2026-11-10T09:19:21Z | COMPLIANCE |
| Ciphertext SHA-256 | `d7bb103b8d5b138bacf0524132234887ae6145e89a3eca1a99338a9dfbfaaf51` | `3ac4ad9f7fcbd52d35da482282ad41fac9cadaf75a600cc8f0e193a39fd78995` |
| Plaintext SHA-256 | `dd1fae2e99f34d7c9f0f5871e51d091f211dbc078d858c98b93416ede8d9c80c` | `72152b7f6d07810bc2298366c151f719b5403b52370cf90445133cf4e9f7a51b` |
| `git_sha` | `f340c58a84276e759ab9a4881febce1fbf7b6924` | `48a3bc8` |

**The two SHAs differ deliberately.** The ledger artifact was produced at
`48a3bc8`; the bundle is at `f340c58`. Recovery must therefore check out an
**ancestor** from a newer bundle — the property that lets one current bundle
serve every retained ledger generation. This drill tested it against real
production artifacts rather than a fixture.

## 3. GitHub Unavailability — the control the drill rests on

```
git clone https://github.com/MechStream006/bitcoin-paper-trading-bot
  → fatal: unable to access ... Failed to connect to github.com port 443
  → clone directory created? NO

curl https://github.com              → curl (7) Failed to connect, HTTP 000
curl https://s3...amazonaws.com      → HTTP 307   (general internet intact)
```

The host additionally had no `~/.aws`, no `~/.git-credentials`, no
`~/.ssh/id_*`, and an empty home directory. GitHub was unusable while S3
remained reachable — so the recovery below cannot have silently fallen back.

## 4. Results

| # | Step | Result | Evidence |
|---|---|---|---|
| 1 | Retrieval by non-production principal | **PASS** | `user/Rohith-admin`, `GetObject` only |
| 2 | Ciphertext verified **without** the key | **PASS** | both artifacts SHA-256 MATCH; header `age-encryption.org/v1` |
| 3 | Bundle decrypt via **stock `age` binary** | **PASS** | exit 0 in **0.009 s**, zero repo code involved |
| 4 | Bundle plaintext SHA-256 | **PASS** | `dd1fae2e…` expected == actual |
| 5 | `git bundle verify` | **PASS** | `records a complete history`, ref `f340c58… refs/heads/main` |
| 6 | Repository restored | **PASS** | clone HEAD `f340c58a84276e759ab9a4881febce1fbf7b6924`, **177 commits** |
| 7 | **Ancestor checkout to the ledger's SHA** | **PASS** | `48a3bc85abe98b3c130e15df32dde2eb10d3bc2e` (short `48a3bc8`) |
| 8 | Recovery-critical files present | **PASS** | all five, see §5 |
| 9 | Ledger decrypted **using the restored repo's own code** | **PASS** | exit 0; plaintext `72152b7f…` MATCH |
| 10 | `receipt_ledger.verify_chain()` | **PASS** | `ok=True, n_receipts=26, errors=[]` |
| 11 | `report.py --check` | **PASS** | exit 0 · 35 families · 9 candidate / 22 discovery / 4 disqualified |
| 12 | Row counts vs manifest | **PASS** | `receipts=26 transitions=13 runs=71 gate_results=145` — exact |
| 13 | Wrong-key negative control | **PASS** | see §6 |
| 14 | Cleanup + termination | **PASS** | see §7 |

**Step 9 is the closing of the loop.** The ledger was decrypted not by a tool
the operator brought, but by `scripts/offhost_backup.py` *recovered from the
bundle itself* — so the code path that certifies the governance record was
itself obtained from S3, with no GitHub access.

## 5. Recovery-critical files at the ledger's SHA

The five files `offhost-backup.md` §6 names, present at `48a3bc8`:

| File | Size |
|---|---|
| `scripts/offhost_backup.py` | 16,474 B |
| `research/evidence_library/receipt_ledger.py` | 12,756 B |
| `research/evidence_library/report.py` | 33,658 B |
| `research/evidence_library/registry.yaml` | 39,870 B |
| `research/evidence_library/thresholds.yaml` | 3,347 B |

## 6. Negative and positive controls

| Control | Exit | Output produced |
|---|---|---|
| `age` wrong key vs bundle | **1** | none |
| restored `offhost_backup.py` wrong key vs ledger | **1** | none |
| `.partial` residue | — | **0 files** |
| **positive control** — correct key vs bundle | **0** | succeeds |

`age: error: no identity matched any of the recipients`. The positive control
is recorded because a negative result alone cannot distinguish "fails closed"
from "fails always".

## 7. Dependencies and cleanup

**Bootstrap — three stock apt packages, zero `pip`:** `age 1.1.1`,
`sqlite3 3.45.1`, `git 2.43.0`. Python 3.12.3 and PyYAML 6.0.1 are preinstalled
on the Ubuntu 24.04 cloud image. Identical to the dependency surface measured
in the first drill.

**Cleanup:** `identity.txt` and the throwaway wrong key `shred -u -z`'d; **0**
files containing `AGE-SECRET-KEY` outside repo test fixtures; **0** loose
`.txt` in home. Instance terminated, security group `sg-0a7d0deabe7e93c74`
deleted, keypair deleted (`InvalidKeyPair.NotFound`), local `.pem` shredded.

**S3 unchanged:** 11 objects before and after; both drilled objects retain
their original VersionIds. No write, delete, or overwrite.

## 8. What this drill establishes

**PROVEN:**

- The encrypted S3 code bundle **replaces the GitHub credential**. Recovery
  completed end to end on **two** credentials — S3 read and the `age` identity
  — with GitHub provably unreachable.
- The bundle is byte-faithful across encrypt → S3 → transfer → decrypt, and is
  opened by the stock `age` binary with no bootstrap paradox.
- **One current bundle serves older ledger generations.** Checking out
  `48a3bc8` from an `f340c58` bundle produced the exact code that made that
  ledger backup.
- A ledger recovered and verified entirely with bundle-supplied code is
  governance-trustworthy: chain valid at 26 receipts, `report.py --check`
  clean at the known 35-family split.
- Encryption fails closed against a wrong key, for both artifact types.

## 9. What this drill does NOT establish

**NOT ESTABLISHED — unchanged by this drill:**

- **Full bot / service recovery.** Code and a ledger were restored and
  verified, then the host was destroyed. No bot was started, no systemd unit
  installed, no venv rebuilt, no WS connection made. The
  `requirements.txt`-vs-deployed-venv gap (11 declared, 46 installed) was not
  exercised.
- **The other three production databases.** `btc_trading.db`,
  `evidence_forward.db` and `forward_evidence.db` have **no off-host
  artifact** — nothing exists to recover from.
- **Parquet corpus** — 58 MB / 129 partitions on production, backed up by no
  path.
- **`.env`** — the §6.1 procedure exists but has never been executed; no
  `env.age` is off-host.
- **Bundle freshness over time.** One bundle at one SHA was drilled. The
  weekly timer is **not enabled**, so the bundle goes stale as `main` advances
  until it is.
- **Key-custody separation.** The S3 read credential and the `age` identity
  still live on the same operator workstation.

## 10. Verdict

**Code-bundle recovery is RECOVERY-DRILL PROVEN.** The GitHub dependency
identified by `bk2-offhost-drill-2026-08-12.md` §6 is closed.

**BK2/BK3 scope is unchanged.** BK2 governance-ledger recovery remains PROVEN,
now on two credentials rather than three. BK3 remains established **for the
governance ledger only**. A bundle carries code, never data, and cannot
broaden that verdict by a single store.

---

*Executed 2026-08-12 09:29:25–09:35:05 UTC. No production service, database,
systemd unit, registry, ledger, `.env`, IAM policy, or S3 object was modified.
All drill infrastructure was destroyed on completion.*
