"""
Label Specification activation via behavioural equivalence (ADR-I011
§12.1 Specification lifecycle, DI4; WBS 3.11 -- owner-authorised, NOT
roadmap-ratified; see this milestone's verification record §0).

WHAT THIS DOES:        transitions eligible Label Specifications
                        Registered -> Active, so a future sealed dataset
                        may cite them (ADR-I011 DI4: "Only Active
                        Specifications may be referenced by a NEW sealed
                        dataset").
WHAT IS NEVER TOUCHED: label_definitions, label_value_forward,
                        label_value_research, governance_receipts, and
                        -- critically -- H6's forward_evidence.db, which
                        is opened READ-ONLY via a `mode=ro` URI, not by
                        convention (roadmap risk LR1).

EQUIVALENCE IS BEHAVIOURAL, NEVER A SHARED CODE PATH (LR1).
`daily_job.py` is not imported, not called, and not modified. This
module independently re-reads H6's own `trade_labels.forward_return`
straight from SQLite and compares it against what the Label Store
materialised at WBS 3.4. Two independent reads of the same fact,
compared -- which is strictly stronger than proving the same function
ran, and is the same discipline `feature_store/
migrate_evidence_engine_features.py::_equivalence_check()` already
established (it calls the binding AND `engine.load_master()`
separately, then compares NaN-safely).

THE SAFETY INVERSION THAT MATTERS.
`registry.activate_specification()` is FAIL-CLOSED AND IRREVERSIBLE: a
check returning False (or raising) transitions the Specification to
Rejected, a permanent record (LG4), and there is no arc back --
re-registering byte-identical content returns the SAME content hash and
leaves the row Rejected forever. Verified empirically; see the Phase 4
Readiness Resolution finding `E-2`.

Therefore this module NEVER hands a failing check to the registry.
`apply()` re-computes the equivalence report immediately before each
activation and REFUSES to call `activate_specification()` at all unless
it passes. A failing Specification is skipped and reported, never
consumed. The registry's own fail-closed rejection path remains intact
for any other caller; this module simply never reaches it.

NON-VACUOUS BY CONSTRUCTION.
A Specification with zero declared values would make any comparison
trivially "pass" (0 == 0) and would activate a binding that was never
demonstrated. `equivalence_report()` requires a non-empty comparable
population; an empty one is `ok=False` with a stated reason, so such a
Specification is deferred, not activated.

Usage:
    python activation.py                 # dry run: runs every equivalence
                                         # check and prints the verdicts,
                                         # activating nothing
    python activation.py --commit        # activates only what passed
    python activation.py --db PATH --h6-db PATH [--commit]

Properties:
    - Dry run is the default and is the whole point: because activation
      is irreversible, the operator sees every verdict BEFORE anything
      is transitioned.
    - Requires --commit to write anything.
    - Asserts the target is a Label Store before writing (LR4).
    - Takes a timestamped backup of label_store.db before writing.
    - Idempotent: an already-Active Specification is skipped.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_DB_PATH = _THIS_DIR / "label_store.db"
_H6_DB_PATH = _THIS_DIR.parent / "data" / "forward_evidence.db"

# Mirrors migrate_h6_forward_labels.py's own construction (line 313:
# `subject_id = f"h6-trade-{row['trade_id']}"`). Built in the SAME
# direction here -- H6 trade_id -> subject_id -- so no fragile parsing of
# the subject_id string is ever performed.
_H6_SUBJECT_ID_TEMPLATE = "h6-trade-{trade_id}"

# The single numeric field WBS 3.4 materialised into outcome_json, and the
# H6 column it came from. Presence is asserted, never defaulted: if either
# shape changes, this module fails loudly rather than comparing None.
_OUTCOME_FIELD = "forward_return"
_H6_RETURN_COLUMN = "forward_return"

BINDING_H6_TRADE_LABELS = "forward_evidence.daily_job.trade_labels"


def _load_sibling(module_file_name: str, registered_name: str):
    """Collision-safe sibling loader -- see migrate_h6_forward_labels.py's
    identical helper and its docstring."""
    if registered_name in sys.modules:
        return sys.modules[registered_name]
    spec = importlib.util.spec_from_file_location(registered_name, _THIS_DIR / module_file_name)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[registered_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_registry():
    return _load_sibling("registry.py", "label_store_registry")


class ActivationError(RuntimeError):
    """Raised when activation is attempted against a Specification whose
    equivalence has not been demonstrated."""


def _assert_label_store_schema(db_path: Path) -> None:
    """Refuse to write to anything that is not a Label Store (LR4)."""
    con = sqlite3.connect(db_path)
    try:
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        con.close()
    missing = {"label_specifications", "label_definitions"} - names
    if missing:
        raise ValueError(
            f"{db_path} does not look like a Label Store database "
            f"(missing table(s): {', '.join(sorted(missing))}). Refusing to write."
        )


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    """Structurally read-only connection. LR1 is preserved by the OS/SQLite,
    not by this module remembering not to write."""
    con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _all_specifications(db_path: Path) -> list[dict]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT * FROM label_specifications ORDER BY definition_name, horizon_days, spec_hash"
        ).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


def h6_forward_returns(h6_db: Path, horizon_days: int) -> dict[str, object]:
    """H6's own value for each trade at `horizon_days`, keyed by the
    subject_id WBS 3.4 would have assigned. Read-only, independent of the
    Label Store -- this is one half of the behavioural comparison."""
    con = _connect_readonly(h6_db)
    try:
        rows = con.execute(
            f"SELECT trade_id, {_H6_RETURN_COLUMN} FROM trade_labels "
            "WHERE horizon_days = ? AND label_available = 1",
            (horizon_days,),
        ).fetchall()
    finally:
        con.close()
    return {
        _H6_SUBJECT_ID_TEMPLATE.format(trade_id=r["trade_id"]): r[_H6_RETURN_COLUMN]
        for r in rows
    }


def label_store_outcomes(db_path: Path, spec_hash: str) -> dict[str, object]:
    """What the Label Store materialised for this Specification, keyed by
    subject_id -- the other half of the comparison."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT subject_id, outcome_json FROM label_value_forward "
            "WHERE spec_hash = ? AND outcome_json IS NOT NULL",
            (spec_hash,),
        ).fetchall()
    finally:
        con.close()
    out: dict[str, object] = {}
    for r in rows:
        payload = json.loads(r["outcome_json"])
        if _OUTCOME_FIELD not in payload:
            raise ActivationError(
                f"label_value_forward row ({spec_hash[:12]}..., {r['subject_id']!r}) has no "
                f"{_OUTCOME_FIELD!r} in outcome_json (keys: {sorted(payload)}) -- the outcome "
                f"shape changed; refusing to compare rather than defaulting to None"
            )
        out[r["subject_id"]] = payload[_OUTCOME_FIELD]
    return out


def _same(a: object, b: object) -> bool:
    """NaN-safe equality, mirroring migrate_evidence_engine_features.py's
    own rationale: NaN != NaN in Python, so a naive == would report two
    identical NaN-laden series as unequal. json.dumps renders NaN as the
    literal token "NaN" both times."""
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def equivalence_report(db_path: Path, spec: dict, h6_db: Path) -> dict:
    """
    Behavioural-equivalence verdict for one Specification. Pure: reads
    only, writes nothing, and never transitions a phase.

    `ok` is True only when a NON-EMPTY population was compared and every
    pair matched. An empty population is a refusal, not a vacuous pass.
    """
    base = {
        "spec_hash": spec["spec_hash"],
        "binding_id": spec["binding_id"],
        "horizon_days": spec["horizon_days"],
        "phase": spec["phase"],
        "compared": 0,
        "matched": 0,
        "mismatched": [],
        "missing_in_source": [],
        "ok": False,
        "reason": None,
    }

    if spec["binding_id"] != BINDING_H6_TRADE_LABELS:
        base["reason"] = (
            f"no equivalence source is registered for binding {spec['binding_id']!r}; "
            f"only {BINDING_H6_TRADE_LABELS!r} has an independently-readable counterpart "
            f"in this repository"
        )
        return base

    if not Path(h6_db).exists():
        base["reason"] = f"H6 database {h6_db} not present; equivalence cannot be demonstrated"
        return base

    ours = label_store_outcomes(db_path, spec["spec_hash"])
    theirs = h6_forward_returns(h6_db, spec["horizon_days"])

    if not ours:
        base["reason"] = (
            "Specification has no declared values with an outcome; a comparison over an empty "
            "population would pass vacuously and activate a binding that was never demonstrated"
        )
        return base

    mismatched, missing = [], []
    matched = 0
    for subject_id, our_value in sorted(ours.items()):
        if subject_id not in theirs:
            missing.append(subject_id)
            continue
        if _same(our_value, theirs[subject_id]):
            matched += 1
        else:
            mismatched.append(
                {"subject_id": subject_id, "label_store": our_value, "h6": theirs[subject_id]}
            )

    base.update(
        compared=len(ours), matched=matched, mismatched=mismatched, missing_in_source=missing,
    )
    if missing:
        base["reason"] = f"{len(missing)} subject(s) absent from H6: {missing[:5]}"
    elif mismatched:
        base["reason"] = f"{len(mismatched)} value(s) differ from H6"
    else:
        base["ok"] = True
        base["reason"] = f"{matched}/{len(ours)} values identical to H6"
    return base


def plan(db_path: Path, h6_db: Path) -> list[dict]:
    """Every Registered Specification with its equivalence verdict.
    Already-Active (or otherwise non-Registered) Specifications are
    excluded -- activation only applies to a freshly registered one."""
    return [
        equivalence_report(db_path, spec, h6_db)
        for spec in _all_specifications(db_path)
        if spec["phase"] == "Registered"
    ]


def apply(db_path: Path, reports: list[dict], h6_db: Path) -> list[dict]:
    """
    Activates only Specifications whose equivalence RE-VERIFIES as ok at
    this moment. Never hands a failing check to
    `registry.activate_specification()`, whose failure path is a
    permanent, irreversible Rejection.
    """
    reg = _load_registry()
    activated = []
    for report in reports:
        if not report["ok"]:
            continue
        spec = reg.get_specification(db_path, report["spec_hash"])
        if spec is None:
            raise ActivationError(f"spec_hash {report['spec_hash'][:12]}... vanished before activation")
        if spec["phase"] != "Registered":
            continue
        fresh = equivalence_report(db_path, spec, h6_db)
        if not fresh["ok"]:
            raise ActivationError(
                f"equivalence for {report['spec_hash'][:12]}... passed at plan time but fails now "
                f"({fresh['reason']}) -- refusing to activate; the registry would permanently "
                f"Reject this Specification"
            )
        reg.activate_specification(db_path, report["spec_hash"], lambda: True)
        activated.append(fresh)
    return activated


def print_table(reports: list[dict]) -> None:
    if not reports:
        print("0 Registered Specifications remain (already activated, or none exist).")
        return
    print(f"{'spec_hash':<20} {'h':>3} {'compared':>8} {'matched':>7}  {'verdict':<8} reason")
    for r in reports:
        verdict = "ACTIVATE" if r["ok"] else "defer"
        print(
            f"{r['spec_hash'][:16] + '...':<20} {r['horizon_days']:>3} {r['compared']:>8} "
            f"{r['matched']:>7}  {verdict:<8} {r['reason']}"
        )
    n_ok = sum(1 for r in reports if r["ok"])
    print(f"\n{n_ok} Specification(s) would be activated; {len(reports) - n_ok} deferred.")


def backup(db_path: Path) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = db_path.with_name(f"{db_path.stem}.pre-activation.{ts}.db")
    shutil.copy2(db_path, backup_path)
    return backup_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--commit", action="store_true", help="Activate what passed (default: dry run)")
    parser.add_argument("--db", default=str(_DB_PATH), help="Path to label_store.db")
    parser.add_argument("--h6-db", default=str(_H6_DB_PATH), help="Path to forward_evidence.db (read-only)")
    args = parser.parse_args(argv)

    db_path, h6_db = Path(args.db), Path(args.h6_db)
    if not db_path.exists():
        print(f"ERROR: {db_path} not found.", file=sys.stderr)
        return 1

    reports = plan(db_path, h6_db)
    print_table(reports)

    if not args.commit:
        print("\nDry run only. Re-run with --commit to activate.")
        print("NOTE: activation is IRREVERSIBLE -- a Specification cannot return to Registered.")
        return 0

    to_activate = [r for r in reports if r["ok"]]
    if not to_activate:
        return 0

    _assert_label_store_schema(db_path)
    backup_path = backup(db_path)
    print(f"\nBacked up {db_path} -> {backup_path}")

    activated = apply(db_path, reports, h6_db)
    print(f"Activated {len(activated)} Specification(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
