"""
forward_readiness.py
====================
Generalized, operator-invoked forward readiness producer for the seven
`run_daily` families (FB-12e, implementing FB-12d Option B).

WHY READINESS COMES FROM `run_log`, NOT FROM A PROBE
----------------------------------------------------
`runner_base.load_daily_closes()` raises `DataUnavailableError` when a day is
absent, carries fewer than `MIN_DAILY_BARS = 1440` one-minute bars, **or when
`len < min_rows`** (the family's own `MIN_HISTORY`), and a raise writes **no**
`run_log` row. So an `ok` `run_log` row for family F on day D is a
**contemporaneous, fail-closed record** that D's inputs were complete, fresh and
deep enough for F. That record was made on the day, by a process that refused to
write when the inputs were insufficient.

Probing the corpus instead would be **retroactive**, and FB-12c demonstrated
exactly why that is forbidden: the funding lake was backfilled on 2026-08-26, so
a probe run today for 2026-07-01 would report a full window and return READY --
for one of the 70 days FB-11 permanently recorded as BACKFILL_REQUIRED. A
readiness producer must never manufacture historical completeness merely because
it is being run later.

EXCLUSIONS ARE READ, NEVER DECLARED
-----------------------------------
INC-0005's 19 days are excluded because `run_date_policy.yaml` declares them in
`immutable_gaps` -- a frozen, reviewable, already-enforced declaration. This
module **reads** that list. It has no way to express an exclusion that is not in
it, which is what stops "declared-gap tolerance" from decaying into "whatever
the producer felt like skipping". An excluded day is never counted as READY and
never receives a receipt; it is reported explicitly, with the basis named.

A missing day that is NOT inside a declared gap is a hard failure. It appears in
`days_non_ready`, and `_from_run_block`'s `ok = not days_non_ready` then blocks.

WHAT THIS MODULE DOES NOT DO
----------------------------
- **No scheduler wiring.** Operator-invoked; nothing imports it.
- **No strategy effect.** It never imports or calls a decision function.
- **No admission, pinning, promotion, DSR/BH or pairwise.**
- **No rewriting of existing rows.** It only appends receipts and, on request,
  returns a payload for a caller to embed in a NEW run.
"""
from __future__ import annotations

import argparse
import importlib
import pathlib
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any

_HERE = pathlib.Path(__file__).resolve().parent
_RESEARCH_ROOT = _HERE.parent
if str(_RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_ROOT))

from forward_evidence import run_date_policy  # noqa: E402

#: The seven forward families, in `run_daily`'s own order. A test asserts this
#: equals run_daily's roster, because duplicating that roster is precisely what
#: once let g1_trend_regime_ma_cross be wired into run_all() while missing from
#: the governance-failure audit.
FAMILIES: tuple[str, ...] = (
    "h5_vol_regime",
    "g1_market_regime_sma200",
    "g3_momentum_tsmom",
    "g1_trend_regime_ma_cross",
    "ivrv_vrp_spread",
    "g2_funding_level",
    "g3_atr_expansion",
)

ENGINE_ACTOR_TYPE = "human"

#: Names the frozen declaration an exclusion rests on. Recorded in every payload
#: so a reader can trace any excluded day back to reviewable config.
EXCLUSION_BASIS = "run_date_policy.yaml#immutable_gaps"

#: The key under which the block is frozen into a run's metrics_json.
#: `readiness_gate._from_run_block` reads exactly this name.
READINESS_KEY = "readiness"

_DEFAULT_FORWARD_DB = _RESEARCH_ROOT / "data" / "evidence_forward.db"


class UndeclaredGapError(RuntimeError):
    """A day was proposed for exclusion that no immutable_gap declares."""


def _dq(name: str):
    """Import a `research/dq` / `evidence_library` module lazily, by bare name.

    At call time, never import time: the collectors must stay importable in the
    research Docker image, which excludes `research/evidence_library/` (INV-1).
    """
    for extra in (_RESEARCH_ROOT / "dq", _RESEARCH_ROOT / "evidence_library"):
        if str(extra) not in sys.path:
            sys.path.insert(0, str(extra))
    return importlib.import_module(name)


# ---------------------------------------------------------------------------
# Declared gaps -- read from frozen policy, never authored here
# ---------------------------------------------------------------------------

def declared_gap_days(policy_path: pathlib.Path | None = None) -> set[str]:
    """Every UTC day inside any declared `immutable_gaps` window."""
    cfg = run_date_policy.load_policy(policy_path)
    days: set[str] = set()
    for gap in cfg.get("immutable_gaps", []):
        start = date.fromisoformat(gap["start"])
        end = date.fromisoformat(gap["end"])
        for i in range((end - start).days + 1):
            days.add((start + timedelta(days=i)).isoformat())
    return days


def _late_arrival_hours(policy_path: pathlib.Path | None = None) -> int:
    """The late-arrival budget, read from `backfill_horizon_days`.

    Not a number chosen here: the policy already fixes how late an explicitly
    authorized collection may still reach a past day, and that is exactly the
    boundary between "the source may still deliver" (INCOMPLETE_DATA) and "it
    never will" (BACKFILL_REQUIRED).
    """
    return int(run_date_policy.load_policy(policy_path)["backfill_horizon_days"]) * 24


def _utc_days(window_start: str, window_end: str) -> list[str]:
    start = date.fromisoformat(window_start[:10])
    end = date.fromisoformat(window_end[:10])
    if end < start:
        raise ValueError(f"window_end {window_end} precedes window_start {window_start}")
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]


# ---------------------------------------------------------------------------
# Contemporaneous evidence
# ---------------------------------------------------------------------------

def collected_days(forward_db: pathlib.Path | str, family_id: str) -> set[str]:
    """Days on which the collector recorded an `ok` run for this family.

    `status='ok'` is the fail-closed record described in the module docstring.
    Any other status, or no row, is not evidence of a complete day.
    """
    conn = sqlite3.connect(f"file:{pathlib.Path(forward_db)}?mode=ro", uri=True)
    try:
        return {
            r[0][:10]
            for r in conn.execute(
                "SELECT run_date FROM run_log WHERE signal_id = ? AND status = 'ok'",
                (family_id,),
            )
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Contract -- one per family, contract_id == family_id (frozen identity)
# ---------------------------------------------------------------------------

def build_contract_doc(family_id: str) -> dict:
    """The readiness contract for one family, as data (L1: declare, don't code).

    `contract_id == family_id` is the frozen identity boundary (FB-12d §5).
    `engine` resolves `feature = feature_id or contract.contract_id`, and
    `readiness_gate` looks up `run["family_id"]`, so the three namespaces
    coincide and the verdict is discoverable. This is what FB-12b found broken.

    The declared source is `static` with no embedded observation: this contract
    is always fed contemporaneous observations by `evaluate_day()`. Probing it
    directly yields `probe_error -> UNKNOWN -> BLOCK`, which is the correct
    answer to "what is this day's readiness?" asked without the evidence.
    """
    if family_id not in FAMILIES:
        raise ValueError(f"unknown forward family {family_id!r}; known: {list(FAMILIES)}")
    return {
        "contract_id": family_id,
        "version": 1,
        "grain": "utc_day",
        "produces": [family_id],
        "inputs": [
            {
                "id": "collection",
                "criticality": "required",
                "source": {"type": "static"},
                "coverage": {"check": "coverage.min_count.v1", "min_count": 1},
                # Derived from frozen policy, not chosen here: `backfill_horizon_days`
                # is how late an EXPLICITLY AUTHORIZED collection may still legitimately
                # arrive for a past day. Inside it a missing day is INCOMPLETE_DATA (an
                # authorized backfill could still deliver); beyond it, BACKFILL_REQUIRED
                # -- which for forward evidence means never, since §20 permits no
                # backfill of the record itself.
                "late_arrival_hours": _late_arrival_hours(),
                "remediation": (
                    "no ok run_log row for this family and day; forward evidence "
                    "for a past day cannot be recreated -- see CLAUDE.md §20"
                ),
            },
        ],
    }


def _observation(family_id: str, collected: bool):
    obs_mod = _dq("observation")
    return obs_mod.InputObservation(
        input_id="collection",
        rows=1 if collected else 0,
        coverage_count=1 if collected else 0,
        coverage_denominator=1,
    )


# ---------------------------------------------------------------------------
# Producer
# ---------------------------------------------------------------------------

def evaluate_day(
    db_path: pathlib.Path | str,
    family_id: str,
    day: str,
    as_of: str | datetime,
    actor: str,
    forward_db: pathlib.Path | str | None = None,
    policy_path: pathlib.Path | None = None,
):
    """Record one day's readiness verdict from contemporaneous evidence.

    Refuses a day inside a declared immutable gap: such a day has no
    contemporaneous evidence and never will, so any verdict would be invented.
    Its absence is carried by `build_readiness_block()` instead.
    """
    if day in declared_gap_days(policy_path):
        raise UndeclaredGapError(
            f"{day} lies inside a declared immutable gap; it has no contemporaneous "
            "evidence and must be reported as excluded, never evaluated"
        )
    dq_readiness = _dq("readiness")
    as_of_dt = datetime.fromisoformat(as_of) if isinstance(as_of, str) else as_of
    if as_of_dt.tzinfo is None:
        as_of_dt = as_of_dt.replace(tzinfo=timezone.utc)

    collected = day in collected_days(forward_db or _DEFAULT_FORWARD_DB, family_id)
    dq_readiness.init_db(db_path)
    return dq_readiness.evaluate_and_record(
        db_path,
        contract_id=family_id,
        period=day,
        as_of=as_of_dt,
        observations={"collection": _observation(family_id, collected)},
        actor=actor,
        actor_type=ENGINE_ACTOR_TYPE,
    )


def build_readiness_block(
    family_id: str,
    window_start: str,
    window_end: str,
    forward_db: pathlib.Path | str | None = None,
    policy_path: pathlib.Path | None = None,
    signal_id: str | None = None,
) -> dict[str, Any]:
    """The `readiness` payload for a run over this window.

    Shape is `_from_run_block`'s existing contract (`days_total`, `days_ready`,
    `days_non_ready`) plus two additive keys it ignores but a human needs:
    `days_excluded` and `exclusion_basis`. `readiness_gate` is unmodified.

    A day is:
      ready     -- a contemporaneous ok run_log row exists;
      excluded  -- inside a declared immutable gap (never ready, never imputed);
      non-ready -- missing for any other reason. That fails closed.
    """
    days = _utc_days(window_start, window_end)
    # The corpus is keyed by signal_id, the ledger by family_id. They coincide
    # for all seven forward families; the parameter exists so a caller that
    # holds both never has to assume it.
    collected = collected_days(forward_db or _DEFAULT_FORWARD_DB, signal_id or family_id)
    excluded_universe = declared_gap_days(policy_path)

    ready, excluded, non_ready = [], [], []
    for day in days:
        if day in collected:
            ready.append(day)
        elif day in excluded_universe:
            excluded.append(day)
        else:
            non_ready.append(day)

    return {
        "days_total": len(days),
        "days_ready": len(ready),
        "days_non_ready": non_ready,
        "days_excluded": excluded,
        "exclusion_basis": EXCLUSION_BASIS if excluded else None,
        "window_start": days[0],
        "window_end": days[-1],
        "source": "run_log:status=ok",
    }


def assert_exclusions_declared(block: dict, policy_path: pathlib.Path | None = None) -> None:
    """Fail closed if a payload excludes any day no immutable_gap declares.

    The invariant FB-12d made a condition of Option B. `_from_run_block` reads
    the block as recorded and cannot verify it, so the check lives here and in
    the test suite.
    """
    declared = declared_gap_days(policy_path)
    invented = sorted(set(block.get("days_excluded") or ()) - declared)
    if invented:
        raise UndeclaredGapError(
            f"payload excludes {len(invented)} day(s) that no immutable_gap declares: "
            f"{invented[:5]}. Exclusions must come from run_date_policy.yaml."
        )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Forward readiness: per-day verdicts and run payloads.")
    p.add_argument("--family", required=True, choices=list(FAMILIES))
    p.add_argument("--window-start", required=True)
    p.add_argument("--window-end", required=True)
    p.add_argument("--forward-db", type=pathlib.Path, default=_DEFAULT_FORWARD_DB)
    p.add_argument("--block-only", action="store_true",
                   help="print the readiness payload; emit no receipts")
    p.add_argument("--db", type=pathlib.Path, help="readiness/receipt database")
    p.add_argument("--as-of")
    p.add_argument("--actor")
    args = p.parse_args(argv)

    block = build_readiness_block(
        args.family, args.window_start, args.window_end, forward_db=args.forward_db)
    assert_exclusions_declared(block)

    if args.block_only:
        import json
        print(json.dumps(block, indent=2, sort_keys=True))
        return 0

    if not (args.db and args.as_of and args.actor):
        p.error("--db, --as-of and --actor are required unless --block-only")
    gaps = declared_gap_days()
    for day in _utc_days(args.window_start, args.window_end):
        if day in gaps:
            continue
        evaluate_day(args.db, args.family, day, args.as_of, args.actor,
                     forward_db=args.forward_db)
    print(f"{args.family}: {block['days_ready']}/{block['days_total']} ready, "
          f"{len(block['days_excluded'])} excluded, "
          f"{len(block['days_non_ready'])} non-ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
