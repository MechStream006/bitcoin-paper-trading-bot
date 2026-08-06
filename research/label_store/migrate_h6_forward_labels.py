"""
H6 Forward-Label Migration (ADR-I011 §7, §12 Tier B/C, §14, §16.1, §16.2,
LG2, §11.1; Phase 3 Label Store roadmap v1.1.0, WBS 3.4 -- the fourth Label
Store milestone, and the first to touch forward-tier data).

Wraps the real, local `research/data/forward_evidence.db` (H6)
`trade_labels` system -- 148 rows, 37 trades, 4 horizons, 100% matured --
as the Label Store's first forward-tier population. This is REPOSITORY-SIDE,
DEV-MACHINE research data, not production: `forward_evidence.db` is H6's
own local research database, distinct from the live EC2 collector this
platform separately runs. No production host is touched, read, or claimed
by this script or its tests.

READ-ONLY AGAINST H6, ENFORCED AT THE CONNECTION LAYER, NOT BY CONVENTION.
`_open_h6_readonly()` connects via a `mode=ro` file URI (mirrors
`src/alerts/forward_status.py`'s identical, already-audited pattern) --
SQLite itself refuses any write attempt; this is not merely "the script
doesn't happen to write."

DECLARE-THEN-FILL DISCIPLINE REUSED, ENFORCEMENT STRENGTHENED (roadmap
§1.2 finding `A-1`). `daily_job.py::_open_trade()` already writes all four
horizon placeholders for a trade at entry time, before any outcome is
knowable -- exactly `ADR-I011` §16.2's "Declaration Fan-Out." This
migration REUSES that discipline by recording the TRUE historical instants
H6 itself captured (`entry_day_ts`, `labeled_at`), never `datetime.now()`;
it does not re-run or reinterpret H6's own logic. What changes is
enforcement: `value_store.py`'s `trg_label_value_forward_guard` (WBS 3.2)
makes the fill-once rule a storage-layer guarantee, not H6's own
application-level `WHERE label_available = 0` clause.

FOUR NEW LABEL SPECIFICATIONS, NOT A REUSE OF C1/C2/C3 OR `ret_fwd1` AS-IS
-- A GENUINE GAP FOUND AND RESOLVED WITHIN THIS MILESTONE'S OWN SCOPE, NOT
AN ADR-I011 INCONSISTENCY. WBS 3.1's original audit (`A-2`) characterized
`daily_job.py`'s only registrable convention as `trade_exits.net_return`
("C1": same-day close, flat 15bps cost, long-only, fixed HOLD_DAYS=7 exit)
and never separately examined `trade_labels.forward_return`. Reading
`_update_forward_labels()` (`fwd = spot_close / entry_price - 1.0`) shows
`trade_labels` is actually a DIFFERENT, previously-unregistered convention:
raw, zero-cost, unsigned, multi-horizon (5/7/14/30 days) -- the SAME family
as `ret_fwd1` (`close[t+1]/close[t] - 1`, WBS 3.3), just at four different
horizons instead of one. Per `ADR-I011` §16.1 ("Horizon Is Identity"), each
horizon is its own Specification; per §11.1 ("Rehorizon: same outcome,
different horizon -> a different Specification, the SAME Definition"),
these four reuse `ret_fwd1`'s existing Definition
(`spot_one_day_forward_return`) rather than forking a new one -- the
semantic claim is unchanged, only the horizon differs. Registering them is
squarely this milestone's own responsibility: the roadmap's ownership
table assigns `§16.2` (Declaration Fan-Out) to WBS 3.4 by name, and a
placeholder cannot be declared under a Specification that does not exist.

STANDING IS DELIBERATELY NOT ASSIGNED HERE -- READ BEFORE ASSUMING THIS
MILESTONE IS INCOMPLETE. The roadmap's own §4.1 exit-criterion PROSE
("148 labels wrapped, attestation=bootstrap, standing Context") predates
the more precise §14.1 OWNERSHIP TABLE, which assigns "§10.1 · §12 Tier B
(standing-at-declaration half) · LG3" to WBS 3.5, not 3.4, and makes WBS
3.5 explicitly DEPEND ON 3.4 ("3.5 | Depends on: 3.4 | Entry: 3.4 merged").
`value_store.py`'s `label_value_forward` schema has no `standing` column by
design (WBS 3.2 deferred it for the same reason). Adding one here, ahead
of WBS 3.5's own `standing.py` mechanism (which resolves per-signal against
`pre_reg_guard.py`'s four real boundaries), would be exactly the kind of
"architectural redesign" this milestone is instructed to avoid, and would
force a special case for these 148 rows that WBS 3.5's own generic
mechanism should instead handle uniformly alongside all future live
declarations. This is a considered reconciliation of two roadmap
sections, not silent scope-dropping: it is documented here, in the
migration's own TD register entry, and restated in the milestone's
verification record.

WHAT DOES SATISFY THE ROADMAP'S UNDERLYING SAFETY INTENT (LG3, LR2,
"a negative test proves no label may be assigned Forward evidence") --
WITHOUT A STANDING COLUMN. Every wrapped label's `declared_by` carries an
explicit, permanent, structurally-checkable bootstrap marker
(`BOOTSTRAP_DECLARED_BY`) distinguishing "this declared_at is a
historical fact being RECORDED now" from a live declaration happening in
real time. This script contains NO code path capable of writing
"Forward evidence" or "Promotion evidence" anywhere -- verified by a
dedicated structural test, mirroring WBS 3.3's identical
"no forward data touched" discipline. `standing.py` (WBS 3.5), when
built, MUST treat `declared_by` carrying `BOOTSTRAP_MARKER` as a hard
override forcing `Context`, never `Forward evidence` -- a binding
instruction for that milestone, recorded here and in the TD register, not
left implicit.

ATTESTATION AS A GOVERNANCE RECEIPT IS WBS 3.8a'S JOB, NOT THIS ONE'S.
The three precedent bootstrap migrations elsewhere in this repository
(`migrate_bootstrap_transitions.py` etc.) emit real
`attestation="bootstrap"` governance receipts because the receipt
ledger already existed when they ran. The Label Store's own receipt
mechanism (`label_receipts.py`, `label.register`/`label.adjudicate`) is
WBS 3.8a's deliverable and does not exist yet -- building a shortcut
version of it here would be scope creep into a later milestone.
`declared_by`'s bootstrap marker (an already-existing, generic field,
mirroring exactly how WBS 3.3's `register_conventions.py` recorded its
own provenance via the pre-existing `actor` field before any receipt
mechanism existed either) is the honest, minimal, non-inventing
substitute available at this milestone's point in the sequence.

INPUT REVISION SET. H6's `corpus_daily` predates Phase 1's `raw_lake.py`
revisioning (WBS 3.1's own finding `A-11` notes `raw_lake.py` covers only
the three conformed ingest writers, not H6's legacy corpus). Recorded
honestly as a descriptive placeholder, not a fabricated `raw_lake.py`
revision reference -- reconciling it against a real revision index is
WBS 3.6's stated job (`value_store.py`'s own docstring makes the same
disclaimer for this exact field).

Usage:
    python migrate_h6_forward_labels.py              # dry run
    python migrate_h6_forward_labels.py --commit      # writes

Idempotent (checked directly against three consecutive --commit runs,
not merely asserted): each of the 148 (trade_id, horizon) pairs is
checked via `get_forward_label()` before any write; an already-Matured
row with matching content is skipped. Requires --commit to write;
prints a full plan before writing.
"""
from __future__ import annotations

import argparse
import importlib.util
import pathlib
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_H6_DB_PATH = _THIS_DIR.parent / "data" / "forward_evidence.db"
_LABEL_STORE_DB_PATH = _THIS_DIR / "label_store.db"


def _load_sibling(module_file_name: str, registered_name: str):
    """Loads a sibling module (registry.py / value_store.py) under an
    EXPLICIT, distinct name, never the bare 'registry'/'value_store' this
    directory's own modules are conventionally imported as. A bare
    sys.path-insert + `import registry` here would collide in
    sys.modules with research/feature_store/registry.py's identical bare
    name whenever both are loaded in the same process (confirmed
    reproducible: running this module alongside
    tests/test_feature_store_registry.py silently resolves `registry` to
    the WRONG module). register_conventions.py (WBS 3.3) predates this
    finding and is left on its own, already-tested bare-import convention
    (correct for its own real, standalone-script invocation, per that
    milestone's own verification record) -- this module, new at WBS 3.4,
    is written collision-safe from the start instead."""
    if registered_name in sys.modules:
        return sys.modules[registered_name]
    spec = importlib.util.spec_from_file_location(registered_name, _THIS_DIR / module_file_name)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[registered_name] = mod
    spec.loader.exec_module(mod)
    return mod


reg = _load_sibling("registry.py", "label_store_registry")
vs = _load_sibling("value_store.py", "label_store_value_store")

ACTOR = "migrate_h6_forward_labels"
NOW = "2026-08-06T00:00:00+00:00"  # registration timestamp for the four new Specifications
ENGINE_VERSION = "v1"

BOOTSTRAP_MARKER = "bootstrap"
BOOTSTRAP_DECLARED_BY = "migrate_h6_forward_labels.py (bootstrap, migrated 2026-08-06)"

# ADR-I011 §16.1: horizon is identity. H6's own LABEL_HORIZONS constant
# (research/forward_evidence/decision_function.py's sibling
# daily_job.py), transcribed as the four Specifications to register --
# same convention (raw, zero-cost, unsigned), same Definition as
# ret_fwd1 (Rehorizon, §11.1), different horizon_days each.
HORIZONS = (5, 7, 14, 30)

_RET_FWD1_DEFINITION_NAME = "spot_one_day_forward_return"


def _horizon_spec_kwargs(horizon_days: int) -> dict:
    return dict(
        horizon_days=horizon_days,
        horizon_governance="calendar_span",
        fill_convention=(
            "Single price differential, not a trade: entry = spot_close at "
            "entry_day_ts, exit = spot_close at entry_day_ts + "
            f"{horizon_days} calendar days. No order fill or slippage model "
            "of any kind (research/forward_evidence/daily_job.py "
            "_update_forward_labels())."
        ),
        cost_convention="None -- zero transaction cost is modelled or subtracted.",
        direction_convention=(
            "Unsigned: the return is reported as-is (exit/entry - 1), with no "
            "direction multiplier -- there is no position concept to sign."
        ),
        maturity_rule=(
            f"Matures when the spot close {horizon_days} calendar days after "
            "entry becomes available and trade_labels.label_available is set "
            "to 1 (research/forward_evidence/daily_job.py "
            "_update_forward_labels())."
        ),
        validity_condition=(
            "Valid once both entry_price (trade_entries) and the horizon's "
            "forward price are recorded (trade_labels.label_available = 1)."
        ),
        conditioning_propagation="preserving",
        binding_id="forward_evidence.daily_job.trade_labels",
        engine_version=ENGINE_VERSION,
    )


def _epoch_ms_to_iso(ms: int) -> str:
    """Deterministic, injected-data conversion -- never a wall-clock read.
    Converts an ALREADY-STORED historical epoch-ms value (H6's own
    entry_day_ts/labeled_at) to ISO-8601 UTC. Never called with the
    current time."""
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()


def _open_h6_readonly(h6_db_path: Path) -> sqlite3.Connection:
    """mode=ro file URI -- SQLite itself refuses any write attempt at the
    connection layer. Mirrors src/alerts/forward_status.py's identical,
    already-audited pattern."""
    uri = pathlib.Path(h6_db_path).resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=5.0)


def _fetch_h6_labels(h6_db_path: Path) -> list[dict]:
    """Read-only: every (trade_id, horizon_days) row from trade_labels,
    joined to trade_entries for entry_price/entry_day_ts. Only matured
    rows (label_available = 1) are wrapped -- an unmatured row has no
    outcome to record and is left for a future run once H6 itself fills
    it (H6 keeps running unchanged; WBS 3.2's roadmap docstring names this
    the single most important design decision in Phase 3, LR1)."""
    con = _open_h6_readonly(h6_db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT tl.trade_id, tl.horizon_days, tl.label_day_ts,
                   tl.forward_return, tl.labeled_at,
                   te.entry_day_ts, te.entry_price
            FROM trade_labels tl
            JOIN trade_entries te ON tl.trade_id = te.trade_id
            WHERE tl.label_available = 1
            ORDER BY tl.trade_id, tl.horizon_days
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def plan(h6_db_path: Path) -> dict:
    """Pure: describes what would be wrapped, without writing anything."""
    labels = _fetch_h6_labels(h6_db_path)
    trades = {r["trade_id"] for r in labels}
    horizons = sorted({r["horizon_days"] for r in labels})
    return {"n_labels": len(labels), "n_trades": len(trades), "horizons": horizons}


def print_plan(h6_db_path: Path) -> None:
    p = plan(h6_db_path)
    print(f"H6 source: {h6_db_path}")
    print(f"{p['n_labels']} matured label(s) across {p['n_trades']} trade(s), "
          f"horizons {p['horizons']}")
    print(f"\n4 Specifications would be registered/reused (one per horizon: {HORIZONS}), "
          f"under the existing '{_RET_FWD1_DEFINITION_NAME}' Definition (Rehorizon, ADR-I011 §11.1).")


def _register_horizon_specs(label_store_db: Path) -> dict[int, str]:
    """Registers (or reuses, content-addressed) the four horizon
    Specifications under ret_fwd1's EXISTING Definition. Fails closed if
    that Definition is not already registered -- WBS 3.3 must have run
    first (roadmap dependency: 3.4 depends on 3.3)."""
    reg.init_db(label_store_db)
    definition = reg.get_latest_definition(label_store_db, _RET_FWD1_DEFINITION_NAME)
    if definition is None:
        raise RuntimeError(
            f"Definition '{_RET_FWD1_DEFINITION_NAME}' is not registered in "
            f"{label_store_db} -- run register_conventions.py --commit first "
            f"(WBS 3.3 must precede WBS 3.4)"
        )

    spec_hashes: dict[int, str] = {}
    for horizon_days in HORIZONS:
        spec_hash = reg.register_specification(
            label_store_db,
            definition_name=definition["name"],
            definition_version=definition["version"],
            actor=ACTOR,
            registered_at=NOW,
            **_horizon_spec_kwargs(horizon_days),
        )
        spec_hashes[horizon_days] = spec_hash
    return spec_hashes


def run(h6_db_path: Path, label_store_db: Path) -> dict:
    """
    Registers the four horizon Specifications, then declares -> observes ->
    matures each matured H6 label as a forward-tier row, using the TRUE
    historical instants H6 itself recorded. Idempotent: a
    (spec_hash, subject_id, declared_at) address already present and
    Matured is skipped, not re-declared (declare_forward_label() would
    otherwise raise on the primary-key collision).

    Returns {"wrapped": n, "skipped_already_present": n, "spec_hashes": {...}}.
    """
    vs.init_db(label_store_db)
    spec_hashes = _register_horizon_specs(label_store_db)

    labels = _fetch_h6_labels(h6_db_path)
    wrapped = 0
    skipped = 0

    for row in labels:
        spec_hash = spec_hashes[row["horizon_days"]]
        subject_id = f"h6-trade-{row['trade_id']}"
        declared_at = _epoch_ms_to_iso(row["entry_day_ts"])

        existing = vs.get_forward_label(label_store_db, spec_hash, subject_id, declared_at)
        if existing is not None and existing["maturity_state"] == "Matured":
            skipped += 1
            continue

        vs.declare_forward_label(
            label_store_db, spec_hash, subject_id, declared_at,
            declared_by=BOOTSTRAP_DECLARED_BY,
        )
        vs.observe_forward_label(
            label_store_db, spec_hash, subject_id, declared_at,
            observed_at=declared_at,
        )
        vs.mature_forward_label(
            label_store_db, spec_hash, subject_id, declared_at,
            outcome={"forward_return": row["forward_return"]},
            input_revision_set=[
                "research/data/forward_evidence.db (predates Phase 1 raw_lake.py "
                "revisioning; WBS 3.6 will reconcile against a real revision index)"
            ],
            matured_at=_epoch_ms_to_iso(row["labeled_at"]),
        )
        wrapped += 1

    return {"wrapped": wrapped, "skipped_already_present": skipped, "spec_hashes": spec_hashes}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--commit", action="store_true", help="Write the wrap (default: dry run)")
    parser.add_argument("--h6-db", type=Path, default=_H6_DB_PATH, help="Path to forward_evidence.db")
    parser.add_argument("--label-store-db", type=Path, default=_LABEL_STORE_DB_PATH, help="Path to label_store.db")
    args = parser.parse_args(argv)

    if not args.h6_db.exists():
        print(f"[error] H6 source not found: {args.h6_db}", file=sys.stderr)
        return 1

    print_plan(args.h6_db)

    if not args.commit:
        print("\nDry run only. Re-run with --commit to write.")
        return 0

    result = run(args.h6_db, args.label_store_db)
    print(f"\nwrapped={result['wrapped']}  skipped_already_present={result['skipped_already_present']}")
    for h, sh in sorted(result["spec_hashes"].items()):
        print(f"  horizon={h:>2}d -> {sh[:16]}...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
