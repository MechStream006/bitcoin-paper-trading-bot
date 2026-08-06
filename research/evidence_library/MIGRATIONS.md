# Migration Script Inventory

Declarative archive manifest for every `migrate_*.py` script in this
directory. This is a record, not an archive mechanism: **no script listed
here is moved, renamed, or deleted by this document.** See §3 for why, and
for the specific hazard that makes physical relocation unsafe right now.

Decision record: `docs/adr/0002-p1-governance-store-consolidation.md`
(ADR-P1-3). Full history: `CLAUDE.md` §18.

---

## 1. Inventory

| Script | Class | Status | Idempotent? | Where run | Retirement criteria |
|---|---|---|---|---|---|
| `migrate_run_kind.py` | P1 D-0 sequence | Run locally (verified, Increment 4). **Run on production 2026-07-26** (Increment 5, operator-attested). | Yes — refuses to overwrite a non-NULL `run_kind` | Local dev (verified); production (executed, attested) | Increment 5 is complete. Remaining criterion: `report.py --check` / GV-SCHEMA confirmed clean **on production, with that output recorded** — see §3 |
| `migrate_bootstrap_evidence_admission.py` | P1 D-0 sequence | Same | Yes — skips runs that already carry a receipt | Same | Same |
| `migrate_bootstrap_transition_receipts.py` | P1 D-0 sequence | Same | Yes — skips transitions that already carry a receipt | Same | Same |
| `migrate_bootstrap_transitions.py` | Historical bootstrap (PR-1, pre-P1) | **Already run** on the real ledger, 2026-06-13 (11 transitions, actor=bootstrap). Explicitly documented: *do not re-run.* | Yes, by construction (skips families with an existing transition) — but re-running is unnecessary, not merely safe | Production, once, historically | Retire when Increment 5's migration set (which does not include this script) is folded into the same archival decision — now unblocked, see §3 |
| `migrate_ivrv_admission.py` | Family-specific (H-IVRV admission, outside P1 scope) | Already run on the server, 2026-06-21 (per its own docstring). Idempotent (skips if `ivrv_vrp_spread` already has a transition). | Yes | Production (already applied) | Not evaluated by this program — outside P1 scope; status stated as documented, not independently re-verified here |
| `migrate_ivrv_repair_local.py` | Family-specific, local-only repair | One-time local-ledger repair for a documented server/local divergence (server ran the admission migration; local dev's gitignored `ledger.db` never received the rows). Applies only if that specific divergence exists on a given machine. | Yes, per its own docstring | Local dev machines only | Not evaluated by this program — outside P1 scope |
| `migrate_g3_atr_admission.py` | Family-specific (g3_atr_expansion admission, outside P1 scope) | Documented as run 2026-06-26; guards against double-execution. | Yes | Production (per docstring) | Not evaluated by this program — outside P1 scope |
| `migrate_g3_atr_server_ledger_sync.py` | Family-specific, server/local sync | One-time sync of a specific run (`0c7e1bf9…`) missing from the server ledger, a prerequisite step for `migrate_g3_atr_admission.py`. | Documented as one-time; not designed for repeated use | Production (per docstring), as the first of a documented 3-step remediation order | Not evaluated by this program — outside P1 scope |

**Status entries for the four family-specific scripts are stated as
documented in each script's own docstring, not independently re-verified by
Program P1.** Verifying them would be scope expansion beyond schema
integrity, restore-path coverage, and off-host monitoring — the three
things this program actually audited.

---

## 2. Reading the table

- **"P1 D-0 sequence"** — the three scripts Increment 4's local harness
  proved work cleanly and idempotently, and that Increment 5 ran against
  production on 2026-07-26. These are the only scripts this program's own testing
  (`tests/test_migration_harness.py`) directly exercises.
- **"Idempotent?"** describes the script's own designed behavior (dry-run
  default, `--commit`-gated, self-backing-up, skip-if-already-applied) —
  not a claim that running it twice is *useful*, only that it is *safe*.
- A script marked "already run" is stated as such from its own docstring or
  from `CLAUDE.md`'s engineering record — not re-verified against the live
  production ledger by this document.

---

## 3. Why nothing here is physically archived

Three concrete couplings make relocation of any script unsafe right now:

1. **The P1 D-0 sequence is invoked at a fixed path.**
   `tests/test_migration_harness.py` constructs
   `EVLIB / script` for exactly these three filenames (`_MIGRATION_SCRIPTS`)
   and calls them via subprocess CLI. Renaming or moving any of the three
   breaks Increment 4's harness silently — the harness would report a
   `FileNotFoundError`, not a governance finding.
2. **The same three scripts are the documented remedy in live source code.**
   `report.py:729` and `record.py:186` both name `migrate_run_kind.py`
   directly in operator-facing comments as "the fix," under ADR-P1-1's
   assert-never-repair discipline. A reader following that comment to a
   moved or deleted file finds nothing.
3. **Fifteen files under `docs/governance/*.md`** reference these scripts by
   name as part of already-approved, already-implemented PR designs
   (PR-1 through PR-9). Those documents describe history; moving the
   scripts they describe would make historical documentation inaccurate
   without changing any historical fact.

**Increment 5 has now completed, so the first archival precondition is met.
Do not "finish the job" by moving these scripts without first updating this
manifest and the three coupling points above.**
This manifest exists specifically so that decision is deliberate, not
incidental.

Physical archival (a real `archive/` directory, deletion, or a
`.deprecated` marker) is explicitly **deferred** until the retirement
criteria in §1 are met for the P1 D-0 sequence, and is not itself part of
Program P1's scope even then — it is a separate, future decision.
