"""
Superseded-Input Flagging (ADR-I011 §11.3, §14, DG3, PS4; Phase 3 Label
Store roadmap v1.1.0, WBS 3.6 -- the sixth Label Store milestone).

Resolves PS4 ("Corpus-correction propagation is Unknown... a corrected
partition may invalidate a label with no mechanism to detect it") for the
Label Store's forward tier, by detecting when a Forward Label's cited
`raw_lake.py` revision has been superseded by a newer landing of the same
partition, and flagging exactly the affected labels.

THE MATURITY-STATE MECHANISM ALREADY EXISTS -- THIS MODULE BUILDS THE
DETECTION LAYER ONLY. `value_store.py`'s `flag_forward_label()` and
`adjudicate_forward_label()` (Matured -> Flagged -> Adjudicated) were
built at WBS 3.2, exercised there only with hand-written test data, and
are UNCHANGED by this milestone -- no modification to `value_store.py` at
all. This module's entire job is `find_affected_labels()`: given one
`raw_lake.py` partition, decide WHICH currently-Matured labels cite a
now-superseded revision of it, then call the already-existing
`flag_forward_label()` on exactly those.

"NEITHER REWRITTEN NOR TRUSTED" (§11.3) IS ALREADY STRUCTURALLY
GUARANTEED, NOT REBUILT HERE. "Neither rewritten": WBS 3.2's
`trg_label_value_forward_guard` already refuses any attempt to change
`outcome_json`/`input_revision_set_json`/`matured_at` once a label has
ever been Matured, including during the Matured -> Flagged transition --
re-verified in this milestone's own tests, not re-implemented. "Nor
trusted": a Flagged label's `maturity_state` is, by construction, no
longer `'Matured'` -- any consumer querying "admissible matured labels"
(`WHERE maturity_state = 'Matured'`) already excludes it, with zero new
admissibility machinery. Building a dedicated admissibility flag/column
here would be WBS 3.7's own scope (conditioning + admissibility ceiling),
not this milestone's.

"FLAG HISTORY" (§14) NEEDS NO NEW TABLE. ADR-I011's own five-arc state
diagram permits at most one `Matured -> Flagged` transition and one
`Flagged -> Adjudicated` transition per label (no arc ever re-enters
`Flagged`, and `Adjudicated` is terminal) -- so the existing
`flagged_at`/`flag_reason`/`adjudicated_at`/`adjudication_decision`
columns already ARE the complete flag history for any label, by
construction. An already-Flagged or already-Adjudicated label is
therefore correctly excluded from `find_affected_labels()`'s own
`WHERE maturity_state = 'Matured'` filter -- this module cannot, and does
not attempt to, re-flag one; that would require an arc `ADR-I011` does
not draw.

STRUCTURED REVISION REFERENCES, DISTINCT FROM WBS 3.4'S PLACEHOLDER.
`value_store.mature_forward_label()`'s `input_revision_set` accepts any
JSON-serialisable list. This module recognises entries shaped like
`{"dataset": str, "symbol": str, "year": int, "month": int, "hash": str}`
-- a `raw_lake.py` partition-and-revision reference -- and ignores
anything else (`_is_structured_reference()`). WBS 3.4's 148 real H6
labels all carry a single descriptive PLACEHOLDER STRING, not a
structured reference (that migration's own docstring names WBS 3.6 as
the milestone that would define this format; its own record explicitly
disclaims fabricating one). Those 148 rows are therefore, correctly and
by design, NEVER matched by this module -- H6 predates `raw_lake.py`
revisioning entirely, so no real correction could ever legitimately
affect them. Verified directly this session: running `find_affected_labels()`
against every real local `raw_lake.py` partition (there are none locally
yet -- WBS 1.2/1.3's dvol pilot has never landed real data on this dev
machine) finds nothing, and even a hypothetical correction could not
match H6's placeholder strings, confirmed by a dedicated test. WBS 3.4's
and WBS 3.5's already-written data and code are untouched by this
milestone -- no modification to `migrate_h6_forward_labels.py`,
`standing.py`, or any of their real output.

`raw_lake.py` IS NOT IMPORTED AT MODULE LEVEL. It carries a real `pandas`/
`pyarrow` dependency (needed for `land_raw()`'s Parquet writing); this
module defers that import to the one function that needs it
(`_import_raw_lake()`, mirroring the deferred-import convention this
repository already uses for pandas-dependent research modules), so
`import superseded` itself, and `flag_forward_label`/
`adjudicate_forward_label`-shaped functions that never touch `raw_lake.py`,
remain usable in the base env.

Usage:
    python superseded.py --dataset D --symbol S --year Y --month M \\
        --flagged-at ISO_INSTANT [--base-dir PATH] [--label-store-db PATH]
        [--commit]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_LABEL_STORE_DB_PATH = _THIS_DIR / "label_store.db"

STRUCTURED_KEYS = frozenset({"dataset", "symbol", "year", "month", "hash"})


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


def _import_raw_lake():
    """Deferred, pandas-carrying import -- see module docstring."""
    storage_dir = _THIS_DIR.parent / "storage"
    if str(storage_dir) not in sys.path:
        sys.path.insert(0, str(storage_dir))
    import raw_lake  # noqa: PLC0415
    return raw_lake


def _is_structured_reference(entry) -> bool:
    return isinstance(entry, dict) and STRUCTURED_KEYS.issubset(entry.keys())


def find_affected_labels(
    label_store_db: Path | str, base_dir: Path | str,
    dataset: str, symbol: str, year: int, month: int,
) -> list[dict]:
    """
    Pure detection, no writes. For the one named `raw_lake.py` partition,
    finds every currently-Matured forward-tier label that cites a
    structured revision reference to it whose `hash` is no longer the
    partition's latest revision (`raw_lake.read_revisions()` orders
    oldest-first, per that module's own docstring, so `revisions[-1]` is
    always the current latest).

    Returns a list of {"spec_hash", "subject_id", "declared_at",
    "cited_hash", "latest_hash"} -- empty if the partition has never been
    landed, or nothing cites a stale revision of it.
    """
    raw_lake = _import_raw_lake()
    revisions = raw_lake.read_revisions(base_dir, dataset, symbol, year, month)
    if not revisions:
        return []
    latest_hash = revisions[-1]["hash"]

    con = sqlite3.connect(label_store_db)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT spec_hash, subject_id, declared_at, input_revision_set_json "
            "FROM label_value_forward WHERE maturity_state = 'Matured'"
        ).fetchall()
    finally:
        con.close()

    affected: list[dict] = []
    for row in rows:
        if row["input_revision_set_json"] is None:
            continue
        for entry in json.loads(row["input_revision_set_json"]):
            if not _is_structured_reference(entry):
                continue
            if (
                entry["dataset"] == dataset and entry["symbol"] == symbol
                and entry["year"] == year and entry["month"] == month
                and entry["hash"] != latest_hash
            ):
                affected.append({
                    "spec_hash": row["spec_hash"], "subject_id": row["subject_id"],
                    "declared_at": row["declared_at"],
                    "cited_hash": entry["hash"], "latest_hash": latest_hash,
                })
                break  # one stale citation is sufficient to flag this label
    return affected


def flag_affected_labels(
    label_store_db: Path | str, base_dir: Path | str,
    dataset: str, symbol: str, year: int, month: int,
    flagged_at: str, *, commit: bool = False,
) -> dict:
    """
    Detects (find_affected_labels()) then, only if commit=True, flags
    each affected label via value_store.flag_forward_label() -- the
    already-existing, already-guarded transition, not reimplemented here.
    `flagged_at` is caller-supplied (LR5 injected-clock discipline; this
    module never calls datetime.now()).

    Idempotent by construction: a re-run's own detection query
    (`WHERE maturity_state = 'Matured'`) naturally excludes any label
    already flagged by a prior run, since its state is now 'Flagged', not
    'Matured' -- no separate bookkeeping is needed. The
    LabelTransitionError guard below is a defensive backstop for a
    concurrent write racing between detection and flagging within one
    run, not the primary idempotency mechanism.
    """
    affected = find_affected_labels(label_store_db, base_dir, dataset, symbol, year, month)
    if not commit:
        return {"would_flag": len(affected), "flagged": 0, "commit": False, "affected": affected}

    vs = _load_sibling("value_store.py", "label_store_value_store")
    flagged = 0
    for item in affected:
        reason = (
            f"cited revision {item['cited_hash'][:16]}... superseded by "
            f"{item['latest_hash'][:16]}... for {dataset}/{symbol}/{year}-{month:02d} "
            f"(ADR-I011 §11.3)"
        )
        try:
            vs.flag_forward_label(
                label_store_db, item["spec_hash"], item["subject_id"], item["declared_at"],
                flagged_at=flagged_at, flag_reason=reason,
            )
            flagged += 1
        except vs.LabelTransitionError:
            continue  # already flagged / no longer Matured -- not this run's job
    return {"would_flag": len(affected), "flagged": flagged, "commit": True, "affected": affected}


def print_plan(result: dict, dataset: str, symbol: str, year: int, month: int) -> None:
    print(f"partition: {dataset}/{symbol}/{year}-{month:02d}")
    print(f"{result['would_flag']} label(s) cite a superseded revision of this partition:")
    for item in result["affected"]:
        print(f"  {item['subject_id']}: cited {item['cited_hash'][:16]}..., "
              f"latest is {item['latest_hash'][:16]}...")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--commit", action="store_true", help="Write the flags (default: dry run)")
    parser.add_argument("--label-store-db", type=Path, default=_LABEL_STORE_DB_PATH)
    parser.add_argument("--base-dir", type=Path, required=True, help="raw_lake.py base_dir")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--month", type=int, required=True)
    parser.add_argument("--flagged-at", required=True, help="ISO-8601 instant to record (injected, never wall-clock)")
    args = parser.parse_args(argv)

    if not args.label_store_db.exists():
        print(f"[error] label_store.db not found: {args.label_store_db}", file=sys.stderr)
        return 1

    result = flag_affected_labels(
        args.label_store_db, args.base_dir, args.dataset, args.symbol, args.year, args.month,
        args.flagged_at, commit=args.commit,
    )
    print_plan(result, args.dataset, args.symbol, args.year, args.month)

    if not args.commit:
        print("\nDry run only. Re-run with --commit to write.")
        return 0

    print(f"\nflagged={result['flagged']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
