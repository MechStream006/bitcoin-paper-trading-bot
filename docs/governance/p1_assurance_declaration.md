# Program P1 — Final Assurance Declaration

**As of:** 2026-07-26 (program closure). **Program status: CLOSED. Increments
1–7 complete, including Increment 5 (production migration, executed on the
production host). Increment 6's host-side activation remains unstarted.**

This document states, plainly and per-claim, what Program P1 has and has not
established. Its purpose is to prevent "seven completed increments" from
being misread as "the governance store is durable in production." One of
the program's four durability objectives below remains explicitly **NOT
ESTABLISHED** (claim 8, off-host durability), and one rests on operator
attestation rather than committed evidence (claim 4).

For history and evidence detail behind every row, see `CLAUDE.md` §18
(Program P1 — Governance Store Consolidation — Engineering Record) and
`docs/adr/0002-p1-governance-store-consolidation.md`. This document does not
repeat that narrative — it states bounded claims and points at the evidence.

---

## 1. Assurance vocabulary

| Level | Meaning |
|---|---|
| **DEMONSTRATED** | The property holds and is proven by executed, repeatable evidence committed to this repository. |
| **DEMONSTRATED (LOCAL)** | Proven, but only against a local or disposable artifact — never exercised on the production host or the live production ledger. |
| **EXECUTED (ATTESTED)** | The action was performed on production and reported complete by the operator. No execution transcript is committed to this repository, so the claim rests on attestation rather than on repeatable, independently re-runnable evidence. Weaker than DEMONSTRATED; stronger than NOT ESTABLISHED. |
| **DESIGNED, NOT ACTIVATED** | Code exists and is tested. The control is deliberately inert pending a separate, gated operator action. |
| **NOT ESTABLISHED** | No evidence exists for this claim. This is not a weak claim of success — it is an explicit claim of nothing. |

---

## 2. Claims

| # | Claim | Level | Evidence | Scope |
|---|---|---|---|---|
| 1 | Governance schema drift is asserted, never silently repaired | **DEMONSTRATED** | `governance_verification.check_schema_fingerprint()`; `tests/test_governance_verification.py` (Increment 3, incl. the as-of conditionality regression fix) | Repository + local dev ledger |
| 2 | Governance entry points (`report.py --check`) fail closed, by name, on a drifted ledger, with zero schema mutation | **DEMONSTRATED** | Increment 3's read-only-contract verification (byte-identical `sqlite_master` before/after a failing check) | Repository + local dev ledger |
| 3 | The D-0 migration sequence (`migrate_run_kind.py` → `migrate_bootstrap_evidence_admission.py` → `migrate_bootstrap_transition_receipts.py`) closes documented drift cleanly and idempotently | **DEMONSTRATED (LOCAL)** | `tests/test_migration_harness.py` (12 tests); `docs/ops/migration-harness-dry-run-2026-07-26.md` | **Disposable local copy only** |
| 4 | **The production `ledger.db` has been migrated and is drift-free** | **EXECUTED (ATTESTED)** | Increment 5 executed on the production host, 2026-07-26, per operator attestation at program closure. Procedure: `docs/ops/production-migration-runbook-increment5.md`. **No execution transcript, scenario classification, or post-migration verification output is committed to this repository.** | Production host — attested, not independently re-verified from this repository |
| 5 | Ledger backup freshness (local, on-host) is monitored, CLI and API in parity | **DEMONSTRATED** | `scripts/healthcheck.py::check_backup_freshness()`; `tests/test_healthcheck.py` (Increment 1) | Repository |
| 6 | The restore path (`scripts/restore.py`) is regression-covered against real SQLite artifacts | **DEMONSTRATED** | `tests/test_restore.py`, 21 tests (Increment 2) | Repository |
| 7 | Off-host transfer failure is no longer silent — a persisted signal exists and is monitored | **DEMONSTRATED** | `scripts/backup.py::_write_offhost_status()`; `scripts/healthcheck.py::check_offhost_freshness()`; `tests/test_offhost_backup.py`, `tests/test_healthcheck.py` (Increment 6) | Repository |
| 8 | **Off-host backup is active and durable (BK3, ADR-0001 §12)** | **NOT ESTABLISHED** | Increment 6 shipped monitoring code only | **No off-host target is provisioned, no key exists, `--encrypt` is not in the deployed unit, and no decrypt drill has ever been run against a real production artifact.** |
| 9 | The encryption/decryption mechanism itself (`age`, asymmetric recipient) is correct | **DEMONSTRATED (LOCAL)** | `docs/ops/offhost-backup.md` §5 — a full local DR drill against a real ledger backup: recovered SHA-256 identical, `verify_chain` ok, `report.py --check` exit 0 | Local drill only, not a production artifact |

---

## 3. Explicit non-claims

Stated rather than left to be inferred from what is absent above:

- Increment 5's migration is recorded at **EXECUTED (ATTESTED)**, not
  DEMONSTRATED, and the distinction is deliberate. This program makes **no
  claim** that the production `ledger.db`'s post-migration state has been
  independently verified from this repository. Capturing the execution
  transcript and post-migration verification output (GV-SCHEMA result,
  `report.py --check` output, `verify_chain()` result, and the §3 scenario
  observed) would raise claim 4 to DEMONSTRATED; until then the claim rests
  on attestation. This is an evidence-archival gap, not a known defect —
  nothing indicates the migration was unsuccessful.
- This program makes **no claim** that any backup — local or off-host — of
  any production database has ever been restored on the production host
  itself. The restore path is regression-tested (claim 6); it has not been
  exercised as a live disaster-recovery drill per ADR-0001 §12 BK2's own
  requirement.
- This program makes **no claim** about `tests/test_api.py`. It cannot be
  collected in any local development environment (missing `fastapi`
  dependency, a pre-existing gap noted at Increment 1) and has not run as
  part of any increment's verification.
- This program makes **no claim** about `pairwise_r` enforcement, contract
  registry activation, or any other governance subsystem outside schema
  integrity, restore-path coverage, and off-host backup monitoring — those
  are PR-1 through PR-9's subject, not Program P1's.
- Two full-suite test failures are known, both investigated and confirmed
  unrelated to this program, and **neither is fixed**:
  - A timestamp-tie class in `current_run_per_lane`'s `run_id`-based
    tiebreak (`test_pin_stale_flag`, `test_selects_latest_within_lane`) —
    non-deterministic, pre-existing, out of scope.
  - A `sys.modules['engine']` name collision between `research/dq/engine.py`
    and `research/evidence/engine.py`, order-dependent in full-suite runs —
    reproduced identically on a clean checkout with zero P1 changes, out of
    scope.
- This program makes **no claim** about disk, network, or cost impact of
  off-host backup once activated in production — those are estimated in
  `docs/ops/offhost-backup.md` §7 but not measured against a real target.

---

## 4. Reopening triggers

Program P1 is not "done" in a sense that forecloses revisiting it. The
following events explicitly reopen it:

1. ~~**Increment 5 executes.**~~ **Fired and closed, 2026-07-26.** Claim 4
   moved from NOT ESTABLISHED to EXECUTED (ATTESTED). It reopens once more,
   at DEMONSTRATED, if and when the execution transcript and post-migration
   verification output are committed to this repository.
2. **BK3 host-side activation completes** (target provisioned, key
   generated and dual-custodied, `--encrypt` deployed, decrypt drill passed
   on a real production artifact). Claim 8 updates accordingly.
3. **Any GV-SCHEMA hard finding appears against the production ledger**,
   whether from routine `report.py --check` or an ad hoc audit. This is a
   new incident, not a P1 regression, but it directly bears on claim 4.
4. **Any receipt-chain verification failure** (`receipt_ledger.verify_chain()`
   returning `ok=False`) on any ledger this program's tooling touches.
5. ~~**Increments 1–6's uncommitted working-tree state** is committed,
   reverted, or lost.~~ **Fired and closed, 2026-07-26.** The program's
   entire output was committed as `62cabdc` (code + tests) and `c14ba38`
   (documentation + governance) and pushed to `origin/main`. The
   single-machine durability gap in the durability program itself is closed.

---

## 5. Summary line

**Program P1 is CLOSED. It has demonstrated, in the repository and in local
testing, that governance schema drift is detected rather than silently
tolerated, that the known migration sequence works cleanly and idempotently
against a disposable copy, and that off-host backup failures are no longer
invisible. The production migration (Increment 5) has been executed on the
attestation of the operator, with no execution transcript committed here.
The program has not provisioned or activated any off-host durability target,
and has not conducted a live disaster-recovery drill in production. Treat
BK3 (ADR-0001 §12) as open until Increment 6's host-side activation says
otherwise, and BK2 as open until a production restore drill is performed.**
