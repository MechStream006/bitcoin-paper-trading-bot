"""
Evidentiary Standing Assignment (ADR-I011 §10.1, §12 Tier B's
standing-at-declaration half, LG3; Phase 3 Label Store roadmap v1.1.0,
WBS 3.5 -- the fifth Label Store milestone).

Resolves and assigns the one property `value_store.py` intentionally left
unresolved: whether a Forward Label is `Context` or `Forward evidence`
(§10.1). `Promotion evidence` -- the further upgrade requiring maturity,
an admissible lane, and use-counting against a sealed decision (§8.1,
LG7) -- is Phase 4's own concern and is never written by this module; see
`value_store.STANDING_VALUES`'s own docstring for why the column still
admits the value.

TWO RESOLUTION PATHS, ONE RULE EACH.

1. BOOTSTRAP OVERRIDE (WBS 3.4's own binding instruction, honoured
   verbatim). A label whose `declared_by` carries `BOOTSTRAP_MARKER` is
   ALWAYS `Context`, unconditionally -- no boundary comparison is even
   attempted. This is not a heuristic: a label the Label Store is only
   now being told about, well after its outcome was already known to the
   migration script that wrote it, cannot honestly be forward evidence
   from THIS system's own vantage point, regardless of what the
   underlying collector's own historical discipline was (ADR-I011's
   Standing Note: "A label declared after its outcome is known is not
   forward evidence, whatever it is called").

2. LIVE DECLARATION (the general mechanism, exercised by zero real rows
   today, since every real forward-tier row this repository holds is
   WBS 3.4's bootstrap-wrapped H6 data). The label's OWNING SIGNAL is
   resolved from its `subject_id` (via a documented prefix convention --
   `h6-` is the only real prefix in use today, since only H6 has been
   wrapped), that signal's real pre-registration boundary is read fresh
   from its own JSON file, and `declared_at` is compared against it:
   before the boundary -> `Context`; at or after -> `Forward evidence`.

`PRE_REG_GUARD.PY` DOES NOT ALREADY PERFORM THIS LOOKUP -- A ROADMAP
CITATION CORRECTED WITHIN THIS MILESTONE'S OWN SCOPE, NOT AN ADR-I011
INCONSISTENCY. The roadmap's own §1.2 finding `A-3` states standing
"is resolved via research/forward_evidence/pre_reg_guard.py, which
already performs that lookup." Reading that module directly (WBS 3.5)
shows it does not: `verify_fn_hash()` reads a signal's pre-registration
JSON and extracts ONLY `function_spec.decision_function_hash`, for fn-hash
integrity checking -- it exposes no boundary-lookup function at all. The
boundary DOES live in the same JSON files (`pre_registration_date`,
confirmed by reading all six real files this session), so this module
reads them directly, with its OWN small function, rather than importing
or modifying `pre_reg_guard.py` (which stays exactly as WBS 3.1 found it
-- "no unrelated refactoring"). This is the same class of finding as WBS
3.4's own §0.1 (a prior audit's engineering citation was imprecise about
a real file's contents) -- resolved by reading the real source, not
escalated.

SIX REAL PRE-REGISTRATION BOUNDARIES, FOUR DISTINCT TIMESTAMPS -- verified
by reading all six files this session, matching the roadmap's own audit
(`A-3`) exactly:

    h6                        2026-06-10T16:50:41.111245+00:00
    g1_market_regime_sma200   2026-06-14T17:48:39+00:00   (shared)
    g3_momentum_tsmom         2026-06-14T17:48:39+00:00   (shared)
    h5_vol_regime              2026-06-14T17:48:39+00:00   (shared)
    ivrv_vrp_spread            2026-06-19T00:00:00+00:00
    g3_atr_expansion           2026-06-26T00:00:00+00:00

WHY THIS MILESTONE DOES NOT RE-RUN OR MODIFY WBS 3.4'S MIGRATION.
`migrate_h6_forward_labels.py` and its 148 already-declared rows are
untouched -- this milestone's only interaction with them is
`backfill_bootstrap_standing()`, a NEW, separate, additive action that
performs the one-time `NULL -> Context` assignment their existing
`declared_by` bootstrap marker already entitles them to (§0.2 of this
milestone's own verification record explains why this is LG3-compliant:
these rows never held a standing value, so this is a first assignment,
not a retroactive change). No `declared_at`, `matured_at`, `outcome`, or
`declared_by` value written by WBS 3.4 is read for any purpose other than
computing the standing that assignment already implies.

Usage:
    python standing.py              # dry run (backfill plan only)
    python standing.py --commit      # writes
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from datetime import datetime
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_FORWARD_EVIDENCE_DIR = _THIS_DIR.parent / "forward_evidence"
_LABEL_STORE_DB_PATH = _THIS_DIR / "label_store.db"


def _load_sibling(module_file_name: str, registered_name: str):
    """Collision-safe sibling loader -- see migrate_h6_forward_labels.py's
    identical helper and its docstring for why a bare `import value_store`
    here would be unsafe in a multi-module test process."""
    if registered_name in sys.modules:
        return sys.modules[registered_name]
    spec = importlib.util.spec_from_file_location(registered_name, _THIS_DIR / module_file_name)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[registered_name] = mod
    spec.loader.exec_module(mod)
    return mod


vs = _load_sibling("value_store.py", "label_store_value_store")

BOOTSTRAP_MARKER = "bootstrap"

# subject_id prefix -> owning signal key. Only "h6-" is real today (the
# only prefix any wrapped forward-tier data actually uses, per
# migrate_h6_forward_labels.py's own subject_id convention,
# f"h6-trade-{trade_id}"). A future signal's own migration must register
# its own prefix here when it first wraps real data -- not invented ahead
# of that need.
_SUBJECT_PREFIX_TO_SIGNAL: dict[str, str] = {
    "h6-": "h6",
}

# signal key -> its real pre-registration JSON file. All six read and
# confirmed this session (module docstring above).
_PRE_REGISTRATION_FILES: dict[str, Path] = {
    "h6": _FORWARD_EVIDENCE_DIR / "pre_registration.json",
    "g1_market_regime_sma200": _FORWARD_EVIDENCE_DIR / "pre_registration_g1_market_regime_sma200.json",
    "g3_momentum_tsmom": _FORWARD_EVIDENCE_DIR / "pre_registration_g3_tsmom.json",
    "h5_vol_regime": _FORWARD_EVIDENCE_DIR / "pre_registration_h5_vol_regime.json",
    "ivrv_vrp_spread": _FORWARD_EVIDENCE_DIR / "pre_registration_ivrv.json",
    "g3_atr_expansion": _FORWARD_EVIDENCE_DIR / "pre_registration_g3_atr_expansion.json",
}


class StandingError(ValueError):
    """Base class for standing-resolution failures."""


class UnknownSubjectError(StandingError):
    """Raised when a subject_id matches no known signal prefix -- assert,
    never guess (Ph6): an unrecognised subject gets no default boundary."""


def _parse_iso(value: str) -> datetime:
    # Defensive Z-normalisation, mirroring src/alerts/forward_status.py's
    # identical precedent -- no real timestamp in this repository's
    # pre-registration files or WBS 3.4's own ISO rendering actually uses
    # 'Z' (all use explicit +00:00 offsets, confirmed by reading every
    # real file this session), but Python 3.10's datetime.fromisoformat()
    # cannot parse a bare 'Z' suffix, so this guards against a future file
    # that does.
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _load_boundary(signal_key: str) -> str:
    """Reads the real pre-registration JSON file fresh on every call (no
    caching, no hardcoded snapshot) and returns its `pre_registration_date`
    field verbatim. Fails loudly (KeyError/FileNotFoundError) rather than
    substituting a default if the file or field is missing -- assert,
    never repair."""
    import json
    path = _PRE_REGISTRATION_FILES[signal_key]
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["pre_registration_date"]


def resolve_signal_key(subject_id: str) -> str:
    """Maps a Forward Label's subject_id to its owning signal, via the
    documented prefix convention. Refuses rather than guessing for an
    unrecognised subject."""
    for prefix, signal_key in _SUBJECT_PREFIX_TO_SIGNAL.items():
        if subject_id.startswith(prefix):
            return signal_key
    raise UnknownSubjectError(
        f"subject_id {subject_id!r} matches no known signal prefix "
        f"({sorted(_SUBJECT_PREFIX_TO_SIGNAL)}) -- refusing to guess an owning "
        f"signal or pre-registration boundary"
    )


def resolve_standing(subject_id: str, declared_at: str, declared_by: str) -> str:
    """
    Pure, deterministic: the same three inputs always produce the same
    standing (§10.1: "two consumers cannot reach different conclusions").

    1. Bootstrap override: `declared_by` carrying BOOTSTRAP_MARKER ->
       'Context', unconditionally.
    2. Otherwise: resolve the owning signal from `subject_id`, read its
       real pre-registration boundary, and compare `declared_at` against
       it. Before the boundary -> 'Context'; at or after -> 'Forward
       evidence'.

    Never returns 'Promotion evidence' -- that upgrade is Phase 4's own
    concern (module docstring).
    """
    if BOOTSTRAP_MARKER in declared_by:
        return "Context"

    signal_key = resolve_signal_key(subject_id)
    boundary = _parse_iso(_load_boundary(signal_key))
    declared_dt = _parse_iso(declared_at)
    return "Context" if declared_dt < boundary else "Forward evidence"


def declare_with_resolved_standing(
    db_path: Path | str, spec_hash: str, subject_id: str, declared_at: str, declared_by: str,
) -> str:
    """
    The correct entry point for a LIVE declaration (LG3: standing assigned
    at declaration, not later). Resolves standing first, then declares
    with it set inline in the same INSERT -- never a separate later call
    for genuinely live data. Returns the assigned standing.
    """
    standing = resolve_standing(subject_id, declared_at, declared_by)
    vs.declare_forward_label(db_path, spec_hash, subject_id, declared_at, declared_by, standing=standing)
    return standing


# ---------------------------------------------------------------------------
# Backfill -- the one-time WBS 3.4 bootstrap case
# ---------------------------------------------------------------------------

def plan_backfill(db_path: Path | str) -> list[dict]:
    """Pure read: every forward-tier row with standing still NULL, and
    what resolve_standing() would assign it. Writes nothing."""
    import sqlite3
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT spec_hash, subject_id, declared_at, declared_by "
            "FROM label_value_forward WHERE standing IS NULL "
            "ORDER BY subject_id, declared_at"
        ).fetchall()
    finally:
        con.close()

    return [
        {
            "spec_hash": r["spec_hash"], "subject_id": r["subject_id"],
            "declared_at": r["declared_at"], "declared_by": r["declared_by"],
            "resolved_standing": resolve_standing(r["subject_id"], r["declared_at"], r["declared_by"]),
        }
        for r in rows
    ]


def print_plan(db_path: Path | str) -> None:
    plan = plan_backfill(db_path)
    by_standing: dict[str, int] = {}
    for item in plan:
        by_standing[item["resolved_standing"]] = by_standing.get(item["resolved_standing"], 0) + 1
    print(f"{len(plan)} forward-tier row(s) with standing IS NULL would be backfilled:")
    for standing, n in sorted(by_standing.items()):
        print(f"  {standing}: {n}")


def backfill_bootstrap_standing(db_path: Path | str, *, commit: bool = False) -> dict:
    """
    Assigns standing to every forward-tier row that does not have one yet
    (WBS 3.4's 148 H6 rows, concretely, today). Idempotent: a row whose
    standing is already set is excluded by `plan_backfill()`'s own query
    (`WHERE standing IS NULL`), so a second run finds nothing left to do.
    Dry run by default (no `commit=True` -> no write, returns the plan
    only).
    """
    plan = plan_backfill(db_path)
    if not commit:
        return {"would_assign": len(plan), "assigned": 0, "commit": False}

    assigned = 0
    for item in plan:
        vs.set_standing(db_path, item["spec_hash"], item["subject_id"], item["declared_at"], item["resolved_standing"])
        assigned += 1
    return {"would_assign": len(plan), "assigned": assigned, "commit": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--commit", action="store_true", help="Write the backfill (default: dry run)")
    parser.add_argument("--label-store-db", type=Path, default=_LABEL_STORE_DB_PATH, help="Path to label_store.db")
    args = parser.parse_args(argv)

    if not args.label_store_db.exists():
        print(f"[error] label_store.db not found: {args.label_store_db}", file=sys.stderr)
        return 1

    vs.init_db(args.label_store_db)
    print_plan(args.label_store_db)

    if not args.commit:
        print("\nDry run only. Re-run with --commit to write.")
        return 0

    result = backfill_bootstrap_standing(args.label_store_db, commit=True)
    print(f"\nassigned={result['assigned']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
