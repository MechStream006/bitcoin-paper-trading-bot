"""
One-time backfill: bootstrap-attest a `feature.register` governance receipt
for every `feature_specifications` row that predates the receipt substrate
(docs/governance/governance_receipts.md §6, "bootstrap honesty"; Phase 3
Label Store roadmap v1.1.0, WBS 3.8b -- `FS-GAP-004` closure, `ADR-I010` FG1,
S-3's mandatory-backfill amendment).

WHAT IS TOUCHED:       a new row per pre-existing Feature Specification in
                        `feature_store.db`'s OWN `governance_receipts` table
                        only -- the same per-store placement WBS 3.8a used
                        for Label receipts in `label_store.db`.
WHAT IS NEVER TOUCHED: `feature_specifications`, `feature_definitions`.
                        This is a pure ADDITION of governance receipts for
                        Specifications that were already registered -- it
                        does not revise, re-judge, or reinterpret any of
                        them. Mirrors `migrate_bootstrap_transition_
                        receipts.py`'s own "state_transitions/runs/
                        gate_results/registry.yaml never touched" guarantee,
                        applied to the Feature Store's own tables instead.

Each receipt is written with `attestation='bootstrap'`
(`receipts.ATTESTATION_BOOTSTRAP`): the Tier A evaluation that justified
each of these 17 real Specifications was not captured at the time in a
replayable form, so `replay()` on them REFUSES rather than approximating --
`receipts.replay()`'s own bootstrap check fires before `decision_class` is
even consulted, exactly the precedent PR-1's `migrate_bootstrap_
transitions.py`, PR-6's `migrate_bootstrap_evidence_admission.py`, and
PR-8's `migrate_bootstrap_transition_receipts.py` all already set.
`verify()` (hash-only) holds forever regardless.

Note the honest asymmetry, stated the same way each of those three
predecessors states it: these receipts say "this Specification was
registered, and here is the outcome", NOT "here is a re-derivable
computation of why". Fabricating an `evaluation[]` for a Tier A check that
was never recorded at the time would be a lie with a valid hash -- worse
than the admitted gap this migration records instead.

NO LIVE `feature.register` EMITTER IS BUILT OR IMPLIED. Unlike WBS 3.8a's
`label_receipts.py` (a live emitter wrapping `registry.
register_specification()`), this milestone's own roadmap deliverable is
named as a backfill script only (`§4.1`, `§13`'s deliverable table: "17
receipts, idempotent, chain valid"). `I9`'s own text: "3.8b adds 17
bootstrap receipts. Both additive; neither modifies `registry.py` or
`value_store.py`." Building a live `feature.register` emitter analogous to
`label_receipts.py` would be a new capability this roadmap item does not
name -- out of scope ("no architectural redesign").

Usage:
    python migrate_bootstrap_feature_receipts.py             # dry run
    python migrate_bootstrap_feature_receipts.py --commit     # writes
    python migrate_bootstrap_feature_receipts.py --db PATH [--commit]

Properties:
    - Idempotent: skips any Specification that already has a receipt.
    - Requires --commit to write anything; default is always a dry run.
    - Takes a timestamped backup of feature_store.db before writing.
    - Prints a full before/after table.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_FEATURE_STORE_DIR = _THIS_DIR.parent / "feature_store"
_DB_PATH = _FEATURE_STORE_DIR / "feature_store.db"

if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))
import receipts  # noqa: E402
import receipt_ledger as rl  # noqa: E402

DECISION_TYPE = "feature.register"
SUBJECT_KIND = "feature"


def _governance_receipts_table_exists(db_path: Path) -> bool:
    """True dry-run safety: a fresh feature_store.db has no
    governance_receipts table at all (confirmed against the real local
    file -- see this milestone's own verification record). plan() must
    never call rl.init_db() to find out, since CREATE TABLE IF NOT EXISTS
    is itself a schema write, and a dry run must write nothing, not even
    an empty table. If the table is absent, no receipt could possibly
    exist yet -- every Specification is planned, with zero I/O against a
    receipts table."""
    con = sqlite3.connect(db_path)
    try:
        row = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'governance_receipts'"
        ).fetchone()
        return row is not None
    finally:
        con.close()


def _get_registration_receipt(db_path: Path, spec_hash: str) -> dict | None:
    """The feature.register receipt for spec_hash, if one exists. At most
    one may ever exist -- mirrors label_receipts.get_registration_receipt()
    exactly (WBS 3.8a), reused by shape rather than by import, since this
    script's own subject kind and decision type differ."""
    rows = rl.get_receipts_by_subject(db_path, SUBJECT_KIND, spec_hash)
    reg_rows = [r for r in rows if r["decision_type"] == DECISION_TYPE]
    if not reg_rows:
        return None
    if len(reg_rows) > 1:
        raise ValueError(
            f"spec_hash {spec_hash[:12]}... has {len(reg_rows)} feature.register receipts; "
            f"exactly one is permitted (direct DB write?)"
        )
    return reg_rows[0]


def _all_specifications(db_path: Path) -> list[dict]:
    """Read-only: every row in feature_specifications. No public bulk-read
    function exists in feature_store/registry.py (only get_specification()
    by single spec_hash) -- a direct SELECT is the minimal, honest choice
    for a one-time migration script, mirroring the same pragmatic pattern
    other migrate_*.py scripts in this repository already use when no
    equivalent public function exists."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("SELECT * FROM feature_specifications ORDER BY registered_at, spec_hash").fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


def plan(db_path: Path) -> list[dict]:
    """Every Feature Specification with no governance receipt. Pure
    computation, no writes -- including no schema write: if
    governance_receipts does not exist yet, every Specification is
    planned directly, with no attempt to query a table that is not
    there (see _governance_receipts_table_exists())."""
    specs = _all_specifications(db_path)
    if not _governance_receipts_table_exists(db_path):
        return specs
    return [
        spec for spec in specs
        if _get_registration_receipt(db_path, spec["spec_hash"]) is None
    ]


def _bootstrap_feature_receipt(db_path: Path, spec: dict, attestation_note: str, actor: str = "bootstrap") -> dict:
    verdict_result = "rejected" if spec["phase"] == "Rejected" else "registered"
    body = receipts.ReceiptBody(
        decision_type=DECISION_TYPE,
        decision_class="derived",
        action="register",
        subject=receipts.Subject(kind=SUBJECT_KIND, id=spec["spec_hash"], version_or_hash=None),
        as_of=spec["registered_at"],
        decided_at=receipts.now_iso(),
        actor=receipts.Actor(type="service", id=actor, key_id=None),
        authority=receipts.Authority(
            policy_id="bootstrap", policy_hash=receipts.sha256_lf(b"bootstrap"), role=None,
        ),
        rule=receipts.RuleRef(rule_id="bootstrap", rule_hash=None, engine_version=None, thresholds_hash=None),
        verdict=receipts.Verdict(result=verdict_result, reason_code=None),
        attestation=receipts.ATTESTATION_BOOTSTRAP,
        attestation_note=attestation_note,
    )
    return rl.append_receipt(db_path, body)


def apply(db_path: Path, items: list[dict]) -> None:
    for spec in items:
        note = (
            f"Reconstructed {datetime.now(timezone.utc).isoformat()} from feature_specifications "
            f"row spec_hash={spec['spec_hash'][:16]}... (definition_name={spec['definition_name']!r}, "
            f"phase={spec['phase']!r}, registered_by={spec['registered_by']!r}, "
            f"registered_at={spec['registered_at']}). This Specification predates the governance "
            f"receipt substrate reaching the Feature Store (WBS 3.8a/3.8b), so the Tier A evaluation "
            f"that justified it was never captured in a replayable form. The registration fact and "
            f"outcome are attested; the computation is not reproducible. verify() holds; replay() "
            f"refuses."
        )
        _bootstrap_feature_receipt(db_path, spec, note)


def print_table(items: list[dict]) -> None:
    if not items:
        print("0 Feature Specifications need a bootstrap receipt (already fully backfilled, or none exist).")
        return
    print(f"{'spec_hash':<20} {'definition_name':<18} {'phase':<10} registered_at")
    for spec in items:
        short_hash = spec["spec_hash"][:16] + "..."
        print(f"{short_hash:<20} {spec['definition_name']:<18} {spec['phase']:<10} {spec['registered_at'][:19]}")
    print(f"\n{len(items)} receipt(s) would be written.")


def backup(db_path: Path) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = db_path.with_name(f"{db_path.stem}.pre-feature-receipts-migration.{ts}.db")
    shutil.copy2(db_path, backup_path)
    return backup_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--commit", action="store_true", help="Write the receipts (default: dry run)")
    parser.add_argument("--db", default=str(_DB_PATH), help="Path to feature_store.db")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: {db_path} not found.", file=sys.stderr)
        return 1

    items = plan(db_path)  # read-only, including schema -- see plan()'s own docstring
    print_table(items)

    if not args.commit:
        print("\nDry run only. Re-run with --commit to write.")
        return 0

    if not items:
        return 0

    backup_path = backup(db_path)
    print(f"\nBacked up {db_path} -> {backup_path}")

    rl.init_db(db_path)  # ensures governance_receipts exists (additive, idempotent) -- only now, after backup, and only because there is something to commit

    apply(db_path, items)
    print(f"Committed {len(items)} bootstrap receipt(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
