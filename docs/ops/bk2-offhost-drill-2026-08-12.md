# BK2 Off-Host Restore Drill — Clean Host — 2026-08-12

| Field | Value |
|---|---|
| **Obligation** | ADR-0001 §12 **BK2** — "a quarterly drill on a clean host: restore, then verify `receipt_ledger.verify_chain()` valid and `report.py --check` clean" |
| **Scope** | Off-host encrypted recovery of the governance ledger from S3 onto a host that has never held project state |
| **Executed** | 2026-08-12, 06:10:59 → 06:18:46 UTC |
| **Drill host** | `i-0b7b79bbc3aceebd8`, t3.small, Ubuntu 24.04.4 LTS, ap-south-1, **no IAM instance profile**, terminated |
| **Production contact** | **None after artifact selection.** No SSH to `3.110.103.217` during execution |
| **Result** | ✅ **PASS — 11 / 11 criteria demonstrated**, with one undocumented dependency (§D) and two documentation mismatches (§E) |

Supersedes nothing. Extends `disaster-recovery-report-2026-08-08.md`, which passed
on-host recovery for six stores and explicitly listed off-host recovery as
**not established** because BK3 was inactive at that time. BK3 is now active for
the ledger, and this drill closes exactly that gap.

---

## 1. Method

A disposable EC2 instance was provisioned with no IAM role and no AWS
credentials of any kind. The encrypted artifact was retrieved from S3 by the
**non-production** principal — IAM user `Rohith-admin` on the operator
workstation, **not** the production instance profile
(`btcbot-offhost-backup-role`, which is write-only and cannot read the bucket)
— and hand-carried to the drill host over SSH. The
private `age` identity was placed on the drill host by `scp`, used, and
`shred -u -z`'d before termination. Nothing was decrypted, verified, or
executed on production.

Verification-only: `restore.py` was never invoked, no live database was
replaced, and no S3 object was written or modified.

## 2. Artifacts

| | Primary | Secondary |
|---|---|---|
| Object | `ledger_20260812_022125.db.age` | `ledger_20260811_022140.db.age` |
| Bucket | `s3://btcbot-offhost-backup-a71f9c2e/prod/` (ap-southeast-1) | same |
| Ciphertext size | 180,456 B | 180,456 B |
| Recipient | `age10npeurzd3643wqjkwl0c4rem7w9zsrmwrvp5cwanyzg79ydpsyyqtfthsd` | same |
| Plaintext SHA-256 | `72152b7f6d07810bc2298366c151f719b5403b52370cf90445133cf4e9f7a51b` | identical |
| `git_sha` | `48a3bc8` | `48a3bc8` |

**Note on the secondary sample:** both generations carry *identical plaintext*
(the ledger saw no governance activity between 2026-08-11 and 2026-08-12), but
their **ciphertexts differ** — verified by `cmp`. The second generation
therefore proves the identity decrypts an independently-encrypted artifact; it
does **not** provide content diversity.

## 3. Pass / Fail Matrix

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | Artifact retrieval from S3 by non-production principal | **PASS** | 4 objects total = 2 × 180,456 B ciphertext + 2 × 1,192 B manifest; `s3:GetObject` exercised for real |
| 2 | Ciphertext verification **without** the private key | **PASS** | both generations `ciphertext OK`, exit 0 |
| 3 | Decryption | **PASS** | exit 0 in **0.060 s** |
| 4 | Plaintext SHA-256 match | **PASS** | `72152b7f…` expected == actual, captured **before** any rw access |
| 5 | `receipt_ledger.verify_chain()` | **PASS** | `ChainVerificationResult(ok=True, n_receipts=26, errors=[])` |
| 6 | `report.py --check` exit 0 | **PASS** | `Registry check: OK` · 35 families · 9 candidate / 22 discovery / 4 disqualified |
| 7 | Row counts match manifest | **PASS** | `gate_results=145 governance_receipts=26 pairwise_r=0 runs=71 state_transitions=13` — all exact |
| 8 | Second generation decrypts | **PASS** | exit 0, SHA match, `ok=True, n_receipts=26` |
| 9 | Wrong-key negative control fails closed | **PASS** | exit 1, `no identity matched any of the recipients`, **no output file, no `.partial` residue** |
| 10 | Cleanup — key material removed | **PASS** | `identity.txt` and `wrong.txt` shredded; no `AGE-SECRET-KEY` outside repo doc/test fixtures |
| 11 | Instance terminated | **PASS** | `terminated`; SG `sg-057753998c978d1de` deleted; keypair deleted (`InvalidKeyPair.NotFound`) |

**Source integrity confirmed post-drill.** The primary S3 object's ETag
(`5a18a2c58fabecc41dda829039b2316f`), VersionId
(`Ba0sqj0ux6u3eXImFF1CPl_ufn9Fj057`), LastModified, and `ObjectLockMode:
COMPLIANCE` are byte-for-byte identical to the Phase 0 baseline. Nothing was
written to the bucket.

## 4. Timing

| Phase | Elapsed |
|---|---|
| Provision → instance running | 63 s |
| Artifact download (4 objects) | 26 s |
| SSH available | 14 s |
| Bootstrap (apt + repo) | 101 s |
| **Recovery data path** — verify → decrypt → SHA → chain → `--check` | **62 s** |
| Second generation + negative control | 43 s |
| Cleanup + termination | 141 s |
| **Total wall clock** | **7 min 47 s** |
| **Provision → trustworthy ledger in hand** | **4 min 26 s** |

Recovery is not data-bound. The 176 KB ledger decrypts in 60 ms; wall clock is
dominated by instance provisioning and package installation.

## 5. Dependencies on a Clean Host

**Expected (from `offhost-backup.md` §6):** `age`, `sqlite3`, git clone, Python.

**Actually required, measured:**

| Dependency | Source | Notes |
|---|---|---|
| `age 1.1.1-1ubuntu0.24.04.3` | stock Ubuntu 24.04 `apt` | |
| `sqlite3 3.45.1-1ubuntu2.7` | stock Ubuntu 24.04 `apt` | row-count verification only |
| `git 2.43.0` | stock Ubuntu 24.04 `apt` | |
| Python 3.12.3 | **preinstalled** on the cloud image | |
| PyYAML 6.0.1 | **preinstalled** (cloud-init dependency) | the only third-party Python import in the whole verification path |
| **GitHub credentials** | **not documented — see §6** | |

**Zero `pip` installs were needed.** `scripts/offhost_backup.py` is pure stdlib
plus the `age` binary; the governance path (`report`, `lifecycle`,
`receipt_ledger`, `record`, `governance_verification`, `evidence_governance`)
imports only `yaml` beyond stdlib. This is a very small recovery surface and it
held up exactly as predicted.

**PEP 668 confirmed active** (`/usr/lib/python3.12/EXTERNALLY-MANAGED` present).
Any future runbook that says `pip install --user …` will fail on Ubuntu 24.04.

## 6. Undocumented Recovery Dependency (GAP)

**🔴 The repository is private, and the documented recovery procedure cannot
complete on a credential-less host.**

`offhost-backup.md` §6 step 0 reads `git clone <repo> btcbot`. On the clean
host this failed:

```
fatal: could not read Username for 'https://github.com': terminal prompts disabled
```

The verification code (`offhost_backup.py`, `receipt_ledger.py`, `report.py`,
`registry.yaml`, `thresholds.yaml`) is **only** obtainable from the private
GitHub repository. In a genuine disaster — VPS gone — an operator therefore
needs a **third** credential alongside the S3 read principal and the `age`
identity: a GitHub token or SSH key with read access to
`MechStream006/bitcoin-paper-trading-bot`. This is documented nowhere in the
recovery path, and its absence would stall recovery at step 0.

**Workaround used, recorded rather than silently applied:** a `git bundle` of
`main` was created on the operator workstation and transferred over SSH. The
drill host independently confirmed `HEAD=48a3bc85abe98b3c130e15df32dde2eb10d3bc2e`,
matching the `git_sha` recorded in the artifact manifest — so the code used for
verification is provably the code that produced the backup. This is
operationally equivalent to an authenticated clone and fakes nothing, but it
does mean **criterion "recovery from documented procedure alone" was not met.**

## 7. Documentation Mismatches

> **Superseded (2026-08-12, commit `21415f0`).** E1, E2 and the documentation
> items in §11 (follow-ups 1, 2 and 5) were reconciled after this drill:
> `offhost-backup.md` now carries the correct recipient, the real S3 retrieval
> path and the three-credential prerequisite; ADR-0001 §12/§20.1,
> `CLAUDE.md` §18 and `p1_assurance_declaration.md` claim 8 now state current
> BK2/BK3 status; and `report.py`'s comment was corrected. **The findings below
> are preserved verbatim as the record of what was true at drill time** — they
> are not restated as still-open.

**E1 — 🟠 `offhost-backup.md` §3.1/§3.3 document the wrong recipient.** The doc
shows `age1m882un7hzz30ht090urm07t2zwr6x6qhs63fx3n08mzs4jmme9tqyr4tkp`;
production `.env` and every real `.age-manifest.json` use
`age10npeurzd3643wqjkwl0c4rem7w9zsrmwrvp5cwanyzg79ydpsyyqtfthsd`. The
documented value is a placeholder from before the real key was generated. An
operator following §6 verbatim during an incident would reach for the wrong
identity. **Not corrected during the drill** (no-silent-fix rule).

**E2 — 🟡 `report.py`'s inline comment is inaccurate, but the concern did not
materialise.** The comment claims the schema-ensure is *"CREATE TABLE IF NOT
EXISTS only, no ALTER"*, while `record.py:194-197` contains a guarded
`ALTER TABLE runs ADD COLUMN run_kind TEXT`. Predicted risk was that
`report.py --check` might mutate the restored artifact.

**Tested directly. It did not.** After `run_check` returned 0, the restored
ledger was **byte-identical** to the pristine copy (`cmp` clean, same SHA-256),
no `-wal`/`-shm` sidecars were created, and `PRAGMA integrity_check` returned
`ok`. Root cause: `VACUUM INTO` output is in `journal_mode=delete`, not WAL, and
`run_kind` already exists on a post-Increment-5 ledger, so the guarded ALTER
never fires. **E2 is a comment-accuracy defect only.** Capturing the SHA before
the check remains prudent practice but is not load-bearing.

**E3 — ✅ No mismatch.** `verify_chain(db_path)` and
`run_check(registry_path, ledger_path, thresholds_path)` match §6's usage
exactly, including positional order and the required `cd research/evidence_library`.

## 8. What This Drill Establishes

**PASS — demonstrated:**
- The production `age` identity in operator custody **decrypts real production
  ciphertext**. This was the single largest unproven assumption in the entire
  durability chain: §3.4 states that a lost key makes every off-host backup
  permanently unrecoverable, and `verify` prints on every run that it proves
  intactness, never decryptability. **Decryptability is now proven, not assumed.**
- End-to-end byte fidelity across encrypt → S3 → download → SSH → decrypt.
- A recovered production ledger is **governance-trustworthy**: chain verifies at
  26 receipts and `report.py --check` passes clean with the exact known family split.
- Encryption **fails closed** against a wrong key, leaving no partial artifact.
- Recovery works on a host with **no AWS credentials and no prior project state**.
- The recovery dependency surface is three apt packages and no pip.

## 9. What This Drill Does NOT Establish

**NOT TESTED:**
- **Recovery of the other three production databases.** `btc_trading.db`
  (1.39 GB), `evidence_forward.db`, and `forward_evidence.db` have **no off-host
  copy at all** — the 02:00 `--all` unit runs without `--encrypt`, and
  `backup.py:339` requires `offhost_cmd and encrypt`. Only the ledger ships.
  On-host recovery for these was proven 2026-08-08; off-host was not, and cannot
  be until they are shipped.
- **Corpus recovery.** 58 MB / 129 parquet partitions on production, backed up
  by no path.
- **`.env` recovery.** The §6.1 manual encrypted procedure exists but has never
  been executed; no `env.age` was found off-host.
- **Full service restoration.** This was verification-only. No bot was brought
  up, no systemd unit installed, no runtime environment rebuilt. The
  `requirements.txt`-vs-deployed-venv gap (11 packages declared, 46 installed,
  Python 3.12.3) was not exercised.
- **Recovery from the documented procedure alone** — see §6.

**GAP — known limitations:**
- Content diversity between generations (§2).
- Off-host retention is 90 days of Object Lock COMPLIANCE; artifacts older than
  that are not protected from deletion.
- Two secrets — the S3 read credential and the `age` identity — currently live
  on the same operator workstation, collapsing the asymmetric design's
  separation. `offhost-backup.md` §3.2 asks for two independent copies of the
  identity; whether a second copy exists was not verified.

## 10. Verdict

**BK2 is SATISFIED for off-host recovery of the governance ledger** — the
highest-priority asset under BK1 and the only truly irreplaceable one.

**BK2 remains UNSATISFIED for the remaining production stores**, not because
recovery failed, but because no off-host copy exists to recover from. That is a
BK3 coverage gap, not a BK2 failure.

## 11. Recommended Follow-Ups

None were performed during this drill (no-additional-improvements rule). In
priority order:

1. **Correct E1** — the wrong recipient in `offhost-backup.md` §3.1/§3.3.
   One-line documentation fix; highest consequence-per-effort item here.
2. **Document the GitHub credential requirement** in §6 step 0, or place a
   `git bundle` of the recovery-critical code alongside the ciphertext in S3 so
   recovery needs two credentials rather than three.
3. **Extend off-host coverage** to `evidence_forward.db` and
   `forward_evidence.db` (both under 150 KB — negligible cost, T1 tier).
4. **Execute the §6.1 `.env` procedure once**, so a recovered ledger is
   accompanied by a bot that can actually authenticate.
5. **Fix E2's comment** in `report.py`.
6. **Confirm a second copy of the `age` identity exists**, per §3.2.

---

*Drill executed 2026-08-12 06:10:59–06:18:46 UTC. No production service,
database, systemd unit, registry, ledger, `.env`, IAM policy, or S3 object was
modified. All drill infrastructure was destroyed on completion.*
