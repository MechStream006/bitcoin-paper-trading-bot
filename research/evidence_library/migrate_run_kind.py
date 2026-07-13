"""
One-time backfill: classify every existing `runs` row into a `run_kind`
lane (docs/governance/evidence_reporting_architecture.md §9).

WHAT IS TOUCHED:    the run_kind column only, on rows where it is currently
                    NULL.
WHAT IS NEVER TOUCHED: every evidence value -- sharpe, oos_sharpe, dsr_prob,
                    bh_p, metrics_json, provenance, run_ts, verdict, and
                    every other column. This is a CLASSIFICATION of rows
                    that already exist, not a revision of them. The
                    append-only invariant on evidence is preserved
                    absolutely.

Deterministic backfill rule (§9), applied independently per family_id:
    - earliest offline-corpus run (by run_ts, run_id tiebreak) -> discovery
    - every later offline-corpus run                            -> confirmatory
    - any live-forward run                                      -> forward
    - any mainnet run                                           -> production

Usage:
    python migrate_run_kind.py              # dry run: prints the
                                             # before/after classification
                                             # table, writes nothing
    python migrate_run_kind.py --commit     # backs up ledger.db, then writes

Properties:
    - Idempotent: refuses to overwrite a non-NULL run_kind. Re-running after
      a successful --commit is a safe no-op (prints "0 rows to classify").
    - Requires --commit to write anything; the default is always a dry run.
    - Takes a timestamped backup of ledger.db before writing.
    - Prints a full before/after table for every row it classifies.
    - Writes an audit note (this script's rule, git_sha if available, and
      the row count) alongside the backup, so the classification's own
      provenance is on the record.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_DB_PATH = _THIS_DIR / "ledger.db"

sys.path.insert(0, str(_THIS_DIR))
import record  # noqa: E402  (needs sys.path set first)


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=_THIS_DIR, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return "unknown"


def classify(db_path: Path) -> list[tuple[str, str, str, str, str | None]]:
    """
    Returns [(run_id, family_id, provenance, run_ts, computed_run_kind), ...]
    for every row whose run_kind is currently NULL, in the deterministic
    order the rule is applied (family_id, then run_ts, run_id).

    Pure computation -- does not write anything.
    """
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT run_id, family_id, provenance, run_ts, run_kind FROM runs "
            "ORDER BY family_id, run_ts, run_id"
        ).fetchall()
    finally:
        con.close()

    seen_offline_discovery: set[str] = set()
    plan: list[tuple[str, str, str, str, str | None]] = []

    for row in rows:
        if row["run_kind"] is not None:
            continue  # never overwrite a non-NULL run_kind

        prov = row["provenance"]
        fam = row["family_id"]

        if prov == "offline-corpus":
            if fam not in seen_offline_discovery:
                computed = "discovery"
                seen_offline_discovery.add(fam)
            else:
                computed = "confirmatory"
        elif prov == "live-forward":
            computed = "forward"
        elif prov == "mainnet":
            computed = "production"
        else:
            computed = None  # unrecognised provenance -- surfaced, not silently classified

        plan.append((row["run_id"], fam, prov, row["run_ts"], computed))

    return plan


def apply(db_path: Path, plan: list[tuple[str, str, str, str, str | None]]) -> None:
    con = sqlite3.connect(db_path)
    try:
        for run_id, _fam, _prov, _run_ts, computed in plan:
            if computed is None:
                continue
            # Refuse to overwrite a non-NULL run_kind -- idempotency guard,
            # defense in depth against a concurrent/duplicate invocation.
            cur = con.execute(
                "UPDATE runs SET run_kind = ? WHERE run_id = ? AND run_kind IS NULL",
                (computed, run_id),
            )
            if cur.rowcount != 1:
                raise RuntimeError(
                    f"refusing to proceed: run_id {run_id} run_kind was not NULL "
                    f"at write time (concurrent modification?)"
                )
        con.commit()
    finally:
        con.close()


def backup(db_path: Path) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = db_path.with_name(f"{db_path.stem}.pre-run_kind-migration.{ts}.db")
    shutil.copy2(db_path, backup_path)
    return backup_path


def write_audit_note(backup_path: Path, plan: list[tuple[str, str, str, str, str | None]]) -> Path:
    note_path = backup_path.with_suffix(".audit.txt")
    classified = [p for p in plan if p[4] is not None]
    unclassified = [p for p in plan if p[4] is None]
    lines = [
        "migrate_run_kind.py audit note",
        f"ts (UTC): {datetime.now(timezone.utc).isoformat()}",
        f"git_sha: {_git_sha()}",
        f"backup: {backup_path.name}",
        f"rows classified: {len(classified)}",
        f"rows left unclassified (unrecognised provenance): {len(unclassified)}",
        "rule: earliest offline-corpus run per family -> discovery; "
        "every later offline-corpus run -> confirmatory; "
        "live-forward -> forward; mainnet -> production "
        "(evidence_reporting_architecture.md §9)",
        "",
        "classification:",
    ]
    for run_id, fam, prov, run_ts, computed in plan:
        lines.append(f"  {run_ts}  {fam:<30} {prov:<14} {run_id[:8]} -> {computed}")
    note_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return note_path


def print_table(plan: list[tuple[str, str, str, str, str | None]]) -> None:
    if not plan:
        print("0 rows to classify (already fully backfilled, or ledger is empty).")
        return
    print(f"{'run_ts':<32} {'family_id':<30} {'provenance':<14} {'run_id':<10} {'run_kind (before -> after)'}")
    for run_id, fam, prov, run_ts, computed in plan:
        after = computed if computed is not None else "UNCLASSIFIED (unrecognised provenance)"
        print(f"{run_ts:<32} {fam:<30} {prov:<14} {run_id[:8]:<10} NULL -> {after}")
    print(f"\n{len(plan)} row(s) would be classified.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--commit", action="store_true", help="Write the classification (default: dry run)")
    parser.add_argument("--db", default=str(_DB_PATH), help="Path to ledger.db (default: evidence_library/ledger.db)")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: {db_path} not found.", file=sys.stderr)
        return 1

    record.init_db(db_path)  # ensures the run_kind column exists (nullable)

    plan = classify(db_path)
    print_table(plan)

    unclassified = [p for p in plan if p[4] is None]
    if unclassified:
        print(
            f"\nERROR: {len(unclassified)} row(s) have an unrecognised provenance and "
            f"cannot be classified by this script's rule. Refusing to --commit.",
            file=sys.stderr,
        )
        return 1

    if not args.commit:
        print("\nDry run only. Re-run with --commit to write.")
        return 0

    if not plan:
        return 0

    backup_path = backup(db_path)
    print(f"\nBacked up {db_path} -> {backup_path}")
    note_path = write_audit_note(backup_path, plan)
    print(f"Audit note -> {note_path}")

    apply(db_path, plan)
    print(f"Committed {len(plan)} row(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
