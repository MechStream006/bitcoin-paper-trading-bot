# Program P1 — Final Assurance Declaration

**As of:** 2026-07-26 (program closure). **Program status: CLOSED. Increments
1–7 complete, including Increment 5 (production migration, executed on the
production host). Increment 6's host-side activation remains unstarted.**

This document states, plainly and per-claim, what Program P1 has and has not
established. Its purpose is to prevent "seven completed increments" from
being misread as "the governance store is durable in production." One of
the program's four durability objectives below remains explicitly **NOT
ESTABLISHED** (claim 8, off-host durability), and one rested on operator
attestation rather than committed evidence (claim 4 — **revised 2026-08-26**:
the production ledger is now migrated and evidenced, but by a **2026-08-07**
execution, not by the 2026-07-26 attestation, whose transcript is
unavailable; see §3).

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
| 4 | **The production `ledger.db` has been migrated and is drift-free** | **DEMONSTRATED (as of 2026-08-07)** — but **NOT by Increment 5**; see the date-separation note below | The D-0 sequence was executed against the production ledger on **2026-08-07**, with output committed to this repository: `docs/ops/incident-2026-08-06-production-investigation.md` §10 B2 — `report.py --check` OK (35 families, 9/22/4), `verify_chain()` `ok=True, n_receipts=26`, `PRAGMA integrity_check` ok, re-run idempotent (0 pending). The **2026-07-26 Increment 5 attestation is separate and uncorroborated** (below). | Production host, verification output committed |
| 5 | Ledger backup freshness (local, on-host) is monitored, CLI and API in parity | **DEMONSTRATED** | `scripts/healthcheck.py::check_backup_freshness()`; `tests/test_healthcheck.py` (Increment 1) | Repository |
| 6 | The restore path (`scripts/restore.py`) is regression-covered against real SQLite artifacts | **DEMONSTRATED** | `tests/test_restore.py`, 21 tests (Increment 2) | Repository |
| 7 | Off-host transfer failure is no longer silent — a persisted signal exists and is monitored | **DEMONSTRATED** | `scripts/backup.py::_write_offhost_status()`; `scripts/healthcheck.py::check_offhost_freshness()`; `tests/test_offhost_backup.py`, `tests/test_healthcheck.py` (Increment 6) | Repository |
| 8 | **Off-host backup is active and durable (BK3, ADR-0001 §12) — GOVERNANCE LEDGER ONLY** | **DEMONSTRATED (ledger)** / **NOT ESTABLISHED (all other stores)** | Post-P1 operator activation + `docs/ops/bk2-offhost-drill-2026-08-12.md`: production ciphertext retrieved from S3 to a clean host, decrypted, SHA-256 identical, `verify_chain()` `ok=True, n_receipts=26`, `report.py --check` exit 0, wrong-key control failed closed | **Ledger only.** Target provisioned (S3 ap-southeast-1, versioned, 90-day Object Lock COMPLIANCE), key exists with the private identity held off-host, `--encrypt` deployed in `btcbot-ledger-backup.service`. **`btc_trading.db`, `evidence_forward.db` and `forward_evidence.db` have no off-host artifact and are therefore NOT recoverable after host loss.** Corpus and `.env` likewise uncovered. |
| 9 | The encryption/decryption mechanism itself (`age`, asymmetric recipient) is correct | **DEMONSTRATED (LOCAL)** | `docs/ops/offhost-backup.md` §5 — a full local DR drill against a real ledger backup: recovered SHA-256 identical, `verify_chain` ok, `report.py --check` exit 0 | Local drill only, not a production artifact |

---

## 3. Explicit non-claims

Stated rather than left to be inferred from what is absent above:

- **Date separation on claim 4 (revised 2026-08-26, Gate 5C.29).** The
  paragraph that stood here recorded Increment 5 at EXECUTED (ATTESTED) and
  described the missing transcript as "an evidence-archival gap, not a known
  defect — nothing indicates the migration was unsuccessful." That last
  clause is **superseded**. Two dates are now held separately, and this
  program makes **no claim** that they can be reconciled:

  | | Date | Basis |
  |---|---|---|
  | **(a) Attested migration date** | **2026-07-26** | Operator attestation at program closure. **The original execution transcript and post-migration verification output are unavailable** — never captured into this repository, and not recoverable from the host afterwards. Procedure: `docs/ops/production-migration-runbook-increment5.md` |
  | **(b) First directly evidenced effective production state** | **2026-08-07** | The production ledger was directly observed on 2026-08-06 to lack `runs.run_kind`, `governance_receipts` and `pairwise_r`; the D-0 sequence run on 2026-08-07 created them, with verification output committed |

  Production behaviour between 2026-07-18 and 2026-08-05 — 19 consecutive
  daily `IndexError` failures on `r["run_kind"]` — is **inconsistent with an
  effective migration having taken place on this host on 2026-07-26**. **No
  explanation for the discrepancy is offered, because none was established**,
  and neither date is edited to agree with the other. Incident record:
  `docs/engineering/incidents/INC-0005-forward-evidence-collection-outage.md`.
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

1. ~~**Increment 5 executes.**~~ **Fired 2026-07-26; reopened and re-closed
   2026-08-07.** Claim 4 moved to EXECUTED (ATTESTED) at Increment 5, then to
   **DEMONSTRATED** on 2026-08-07 when the D-0 sequence was executed against
   the production ledger with its verification output committed. The
   2026-07-26 transcript remains **unavailable** and the two dates are held
   separately — see §3.
2. **BK3 host-side activation completes** (target provisioned, key
   generated and dual-custodied, `--encrypt` deployed, decrypt drill passed
   on a real production artifact). Claim 8 updates accordingly.
3. ~~**Any GV-SCHEMA hard finding appears against the production ledger**,
   whether from routine `report.py --check` or an ad hoc audit. This is a
   new incident, not a P1 regression, but it directly bears on claim 4.~~
   **FIRED 2026-08-06, closed 2026-08-07.** The production ledger was found
   to lack `runs.run_kind`, `governance_receipts` and `pairwise_r` — the
   drift this class of finding exists to surface. It reached production as an
   unnamed `IndexError` rather than a GV-SCHEMA diagnostic, because
   Increment 3's fingerprint code was undeployed on that host at the time.
   Closed by the 2026-08-07 migration. Consequence for claim 4 recorded in
   §3; data-loss consequence recorded as `INC-0005`. **This trigger remains
   armed for any future occurrence.**
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
The program itself did not provision or activate any off-host durability
target, and conducted no live disaster-recovery drill.

**Updated 2026-08-12, after program closure.** Both gaps have since been
partially closed by operator action and a live drill
(`docs/ops/bk2-offhost-drill-2026-08-12.md`). **BK2 off-host recovery of the
governance ledger is PROVEN**: a real production ciphertext was recovered onto
a clean host, cryptographically verified, chain-verified, and passed through
the project's own governance checker, with the wrong-key negative control
failing closed. **BK3 is established for the governance ledger only.**

**This does not mean the platform is recoverable.** The other three production
stores — `btc_trading.db`, `evidence_forward.db`, `forward_evidence.db` — have
no off-host artifact, so their recovery after host loss is **NOT ESTABLISHED**;
the same is true of the Parquet corpus and `.env`. Full-service restoration
(rebuilding the runtime environment and bringing the bot back up) has never
been attempted. Treat the proven claim as exactly what it is: the single
irreplaceable asset under BK1 is recoverable, and nothing broader.**

**Updated 2026-08-26 (Gate 5C.29) — claim 4.** The production `ledger.db` **is**
migrated and drift-free, evidenced by an execution on **2026-08-07** whose
verification output is committed here. That is **not** the 2026-07-26 Increment 5
attestation, whose transcript is unavailable and which production behaviour
between 2026-07-18 and 2026-08-05 does not corroborate. Both dates are retained;
neither is edited to agree with the other, and **no explanation for the
difference is offered, because none was established**. The unresolved drift cost
19 consecutive days of irreplaceable forward evidence, recorded as
`docs/engineering/incidents/INC-0005-forward-evidence-collection-outage.md` —
a permanent gap that must never be backfilled or interpolated.
