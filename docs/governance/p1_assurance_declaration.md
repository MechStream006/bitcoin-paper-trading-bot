# Program P1 — Final Assurance Declaration

**As of:** 2026-07-26 (Increment 7). **Program status: Increments 1–4 and 6
(repository scope) complete; Increment 5 (production migration) and
Increment 6's host-side activation are unstarted.**

This document states, plainly and per-claim, what Program P1 has and has not
established. Its purpose is to prevent "seven completed increments" from
being misread as "the governance store is durable in production." Two of
the program's four durability objectives below are explicitly **NOT
ESTABLISHED**.

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
| **DESIGNED, NOT ACTIVATED** | Code exists and is tested. The control is deliberately inert pending a separate, gated operator action. |
| **NOT ESTABLISHED** | No evidence exists for this claim. This is not a weak claim of success — it is an explicit claim of nothing. |

---

## 2. Claims

| # | Claim | Level | Evidence | Scope |
|---|---|---|---|---|
| 1 | Governance schema drift is asserted, never silently repaired | **DEMONSTRATED** | `governance_verification.check_schema_fingerprint()`; `tests/test_governance_verification.py` (Increment 3, incl. the as-of conditionality regression fix) | Repository + local dev ledger |
| 2 | Governance entry points (`report.py --check`) fail closed, by name, on a drifted ledger, with zero schema mutation | **DEMONSTRATED** | Increment 3's read-only-contract verification (byte-identical `sqlite_master` before/after a failing check) | Repository + local dev ledger |
| 3 | The D-0 migration sequence (`migrate_run_kind.py` → `migrate_bootstrap_evidence_admission.py` → `migrate_bootstrap_transition_receipts.py`) closes documented drift cleanly and idempotently | **DEMONSTRATED (LOCAL)** | `tests/test_migration_harness.py` (12 tests); `docs/ops/migration-harness-dry-run-2026-07-26.md` | **Disposable local copy only** |
| 4 | **The production `ledger.db` has been migrated and is drift-free** | **NOT ESTABLISHED** | — | **Increment 5 has not run.** Claim 3 is evidence that the sequence *would* work, not that it *has* been applied. |
| 5 | Ledger backup freshness (local, on-host) is monitored, CLI and API in parity | **DEMONSTRATED** | `scripts/healthcheck.py::check_backup_freshness()`; `tests/test_healthcheck.py` (Increment 1) | Repository |
| 6 | The restore path (`scripts/restore.py`) is regression-covered against real SQLite artifacts | **DEMONSTRATED** | `tests/test_restore.py`, 21 tests (Increment 2) | Repository |
| 7 | Off-host transfer failure is no longer silent — a persisted signal exists and is monitored | **DEMONSTRATED** | `scripts/backup.py::_write_offhost_status()`; `scripts/healthcheck.py::check_offhost_freshness()`; `tests/test_offhost_backup.py`, `tests/test_healthcheck.py` (Increment 6) | Repository |
| 8 | **Off-host backup is active and durable (BK3, ADR-0001 §12)** | **NOT ESTABLISHED** | Increment 6 shipped monitoring code only | **No off-host target is provisioned, no key exists, `--encrypt` is not in the deployed unit, and no decrypt drill has ever been run against a real production artifact.** |
| 9 | The encryption/decryption mechanism itself (`age`, asymmetric recipient) is correct | **DEMONSTRATED (LOCAL)** | `docs/ops/offhost-backup.md` §5 — a full local DR drill against a real ledger backup: recovered SHA-256 identical, `verify_chain` ok, `report.py --check` exit 0 | Local drill only, not a production artifact |

---

## 3. Explicit non-claims

Stated rather than left to be inferred from what is absent above:

- This program makes **no claim** about the current state of the production
  `ledger.db` schema. It may or may not already have drifted further since
  the incident that chartered this program.
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

1. **Increment 5 executes.** At that point claim 4 must be updated from NOT
   ESTABLISHED to a dated, evidenced claim — success or failure.
2. **BK3 host-side activation completes** (target provisioned, key
   generated and dual-custodied, `--encrypt` deployed, decrypt drill passed
   on a real production artifact). Claim 8 updates accordingly.
3. **Any GV-SCHEMA hard finding appears against the production ledger**,
   whether from routine `report.py --check` or an ad hoc audit. This is a
   new incident, not a P1 regression, but it directly bears on claim 4.
4. **Any receipt-chain verification failure** (`receipt_ledger.verify_chain()`
   returning `ok=False`) on any ledger this program's tooling touches.
5. **Increments 1–6's uncommitted working-tree state** (see
   `docs/adr/0002-p1-governance-store-consolidation.md` §1 context and
   CLAUDE.md §18) is committed, reverted, or lost. Until committed, this
   entire program's output exists only on one machine — a durability gap
   in the durability program itself, noted here rather than left implicit.

---

## 5. Summary line

**Program P1 has demonstrated, in the repository and in local testing, that
governance schema drift is detected rather than silently tolerated, that
the known migration sequence works cleanly and idempotently against a
disposable copy, and that off-host backup failures are no longer invisible.
It has not yet touched the production ledger, has not provisioned or
activated any off-host durability target, and has not conducted a live
disaster-recovery drill in production. Treat BK2 and BK3 (ADR-0001 §12) as
open until Increments 5 and 6's host-side activation say otherwise.**
