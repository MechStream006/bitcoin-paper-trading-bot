# ADR-0002: Program P1 — Governance Store Consolidation

**Status:** Accepted. Program P1 is **CLOSED** (2026-07-26): Increments 1–7
complete, including Increment 5 (production migration, executed — recorded
at EXECUTED (ATTESTED), see the Assurance Declaration claim 4). Increment
6's host-side off-host activation remains unstarted and is tracked as
operator work, not as an open increment.

**Relationship to ADR-0001:** Extends, does not supersede. ADR-0001 §12
(Backup Strategy) and §17 (Preserved Invariants) remain authoritative;
this ADR formalizes decisions made *while implementing* ADR-0001's backup
and schema-integrity commitments that were not themselves architectural
choices at ADR-0001's time of writing.

**Full engineering narrative:** `CLAUDE.md` §18 (Program P1 — Governance
Store Consolidation — Engineering Record). This ADR records *decisions*;
§18 records *history* — what was built, what broke, what was fixed, and the
evidence for each increment. This document cross-references §18 rather than
repeating it. If you need "what happened," read §18. If you need "what was
decided and why it's binding," read this.

---

## 1. Context

Program P1 was chartered (Gap Analysis, 2026-07-25) against a specific,
confirmed production incident: `report.py --check` crashed
(`lifecycle.py` C1's `r["run_kind"]` raising `IndexError` on a
`sqlite3.Row`) when run against a ledger that predated the `run_kind`
column. The crash was silent in the sense that nothing *prevented* it —
governance code assumed a schema shape it never verified.

Four workstreams were scoped (see the approved Gap Analysis for the full
12-project breakdown): schema integrity (this ADR's primary subject),
restore-path assurance, off-host durability, and program-level assurance
reporting. Increments 1–7 implement workstreams A–C plus this closing
documentation increment; workstream D (production migration, Increment 5)
was gated behind Rohith's explicit authorization and executed on
2026-07-26 following that authorization.

---

## 2. Decisions

Five decisions were made in the course of implementing Increments 1–6.
Each is cited from source as `ADR-P1-N`; this section is their formal home.

### ADR-P1-1 — Assert, never repair

Schema-validation code reports a gap by name and returns a failure. It never
mutates the ledger, applies a migration, or otherwise "fixes" what it finds,
as a side effect of a read-only diagnostic call.

**Rationale:** the original incident was a *validation* function crashing on
an unexpected schema. Giving that same code path the power to silently
correct what it finds would convert a diagnostic tool into an unaudited
write path — precisely the class of hazard the ATR ordering incident
(referenced in `CLAUDE.md` §15) already demonstrated for deploy-triggered
behavior changes.

**Implementing code:** `research/evidence_library/governance_verification.py`
(`check_schema_fingerprint()`), `research/evidence_library/report.py`
(`run_check()`'s guard, which returns before calling `validate_registry()`
on any hard finding). Both files cite this decision by name in comments.

**Evidence:** CLAUDE.md §18, "Increment 3 Complete."

### ADR-P1-2 — Schema requirements are a declarative manifest

The set of tables/columns governance code depends on is expressed as a data
structure (`SCHEMA_FINGERPRINT`), not as scattered procedural checks. The
manifest is scoped to what evidence shows the code actually depends on — it
is deliberately not expanded into a full schema mirror.

**Rationale:** a declarative manifest is auditable by inspection and cannot
silently grow scope through an unrelated code change. Scoping it to
evidence-derived entries (rather than "everything") keeps false positives
from unrelated future tables out of a governance gate.

**Implementing code:** `governance_verification.py::SCHEMA_FINGERPRINT`.

**Evidence:** CLAUDE.md §18, "Increment 3 Complete" (includes the rejected
alternative — a full schema mirror — and why it was rejected).

### ADR-P1-3 — Migration remains explicit, operator-authorized, idempotent, self-backing-up

No migration script runs automatically, as a side effect of any other
process (deploy, healthcheck, `report.py --check`, or another migration
script). Every migration script defaults to dry-run, requires an explicit
`--commit` flag, refuses to double-apply already-applied changes, and takes
its own timestamped backup before writing.

**Rationale:** this is the existing pattern `migrate_run_kind.py` and its
successors already followed (established PR-5/PR-6/PR-8, before Program P1
existed); P1 formalizes it as binding rather than merely customary, because
Increment 5's production migration depends on the pattern holding without
exception.

**Implementing code:** all eight scripts in
`research/evidence_library/migrate_*.py` — see
`research/evidence_library/MIGRATIONS.md` (this increment) for the full
inventory and per-script status.

### ADR-P1-4 — Production migration requires local pre-validation first

Before any migration sequence runs against the production ledger, it must
be proven — on a disposable local copy reproducing the documented
pre-migration schema shape — to close the drift cleanly and idempotently,
using the scripts' real, unmodified CLI.

**Rationale:** the D-0 migration sequence had never been executed as a
sequence end-to-end before Increment 4; validating it locally first is
strictly cheaper than discovering a defect during the one production
attempt.

**Implementing code:** `tests/test_migration_harness.py` (12 tests).

**Evidence:** CLAUDE.md §18, "Increment 4 Complete";
`docs/ops/migration-harness-dry-run-2026-07-26.md` (the transcript).

**Status:** DEMONSTRATED against a local, disposable copy only. Does not
itself establish anything about the production ledger — see the Assurance
Declaration (`docs/governance/p1_assurance_declaration.md`).

### ADR-P1-5 — Durability controls require an independent monitoring signal

A backup, restore path, or off-host transfer that can fail silently is not
considered a completed control, regardless of whether its underlying
mechanism (encryption, transfer, integrity check) is itself correct.

**Rationale:** discovered directly — `scripts/backup.py`'s off-host
transfer failure was, by design, non-fatal to the overall backup (the local
plaintext backup is the fast restore path and must not be blocked by a
transfer failure). That correct design choice had an uncompensated side
effect: a broken off-host leg had no persisted signal at all, only a
stderr line inside a `oneshot` unit's journal.

**Implementing code:** `scripts/healthcheck.py::check_backup_freshness()`
(Increment 1), `check_offhost_freshness()` (Increment 6);
`scripts/backup.py::_write_offhost_status()` (Increment 6).

**Evidence:** CLAUDE.md §18, "Increment 1 Complete," "Increment 6 Complete."

**Status:** repository-side monitoring is DEMONSTRATED. The off-host control
itself (target, key, activation) is **NOT ESTABLISHED** — see the Assurance
Declaration.

---

## 3. Consequences

- Any future change to governance-ledger schema validation must preserve
  ADR-P1-1: a schema check that starts silently repairing what it finds
  would need a superseding decision, not a quiet edit.
- Any new durability control (a fourth backup target, a new replication
  leg) inherits ADR-P1-5's requirement for an independent failure signal as
  a condition of being considered complete, not an optional enhancement.
- **Any** production ledger migration — Increment 5 was the first, not the
  last — is bound by ADR-P1-3 and ADR-P1-4: it may not skip local
  pre-validation, and it may not run outside the explicit
  dry-run/`--commit` discipline, regardless of time pressure.
- This ADR does not, by itself, authorize any production migration.
  Authorization remains a separate, explicit act by Rohith (ADR-0001 §17.3:
  promotion/production action is a judgment, not a computation the platform
  performs on itself — the same principle applied here to migration
  execution). Increment 5 received that authorization on 2026-07-26.

---

## 4. Where to look for more

| Question | Answer is in |
|---|---|
| What was actually built, and when? | `CLAUDE.md` §18 |
| Is Program P1's work production-safe right now? | `docs/governance/p1_assurance_declaration.md` |
| What is the status of each `migrate_*.py` script? | `research/evidence_library/MIGRATIONS.md` |
| What does BK1–BK5 require, and what's still open? | `docs/adr/0001-infrastructure-architecture.md` §12, §20 |
| How does off-host backup work end to end? | `docs/ops/offhost-backup.md` |
