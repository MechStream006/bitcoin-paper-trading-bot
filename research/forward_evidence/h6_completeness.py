"""
h6_completeness.py
==================
Operator-invoked completeness provenance for `h6_funding_vol_regime`.

WHY THIS EXISTS
---------------
FB-10c-H6 established that h6 was **collected but not evaluable** for 70 days
(2026-06-16 .. 2026-08-26): `_funding_regime()` returns the string `"unknown"`
when fewer than `REGIME_WINDOW` valid funding observations exist, and
`h6_decide()` then tests `funding_regime == "low"` by positive equality, so an
unevaluable input collapses into the same `h6_canonical = 0` a genuinely flat
day produces. `daily_job` records `status='ok'` either way.

This module restores the missing distinction **as provenance**, and nothing
else. It answers "were h6's inputs fit to derive from on this day?" without
touching, re-deriving, or second-guessing what h6 decided.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
- **No change to h6's strategy semantics.** `h6_decide()`,
  `compute_h6_decision()` and `decision_function.py` are frozen and hash-pinned
  by `run_log.fn_hash` and the pre-registration. This module imports
  `REGIME_WINDOW` and `MIN_HISTORY` rather than restating them, so the contract
  cannot drift from the function it describes.
- **No write suppression and no gating.** A verdict is recorded; no row is
  withheld from any collector. Gating is a separate, later decision (FB-12 §5).
- **No historical reinterpretation.** `evaluate_current()` refuses any period
  other than the caller's stated current one. The 70 historical rows are
  governed by `docs/governance/fb11_h6_unevaluable_window_provenance.md` and
  are never re-verdicted here -- PR-7's L4 and its reserved `NO_RECEIPT` count
  both require that days nobody evaluated stay that way.
- **No scheduler wiring.** Nothing in `run_daily.py`, `daily_job.py` or any
  systemd unit imports this module. It is invoked by an operator.
- **No new state, table, or governance surface.** The verdict is a
  `readiness.verdict` receipt, reserved in `DECISION_TAXONOMY` since PR-1 and
  produced by PR-7's existing `dq.readiness.evaluate_and_record`.

THE CONTRACT
------------
h6 requires `REGIME_WINDOW` (252) valid funding days inside a `MIN_HISTORY`
(273) row window, and the corpus query counts a funding day only when it
carries at least `MIN_DAILY_FUND` (2) of the day's 3 events. Those three
numbers are the entire completeness rule, and all three are imported from the
modules that already own them.
"""
from __future__ import annotations

import argparse
import pathlib
import sys
from datetime import datetime, timedelta, timezone

_HERE = pathlib.Path(__file__).resolve().parent
_RESEARCH_ROOT = _HERE.parent
if str(_RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_ROOT))

from forward_evidence.decision_function import MIN_HISTORY, REGIME_WINDOW  # noqa: E402

#: A funding day is admissible only with at least this many of the day's three
#: 8-hourly events. Mirrors the `HAVING COUNT(*) >= 2` guard in `daily_job`'s
#: corpus query -- the guard whose NULL output the derivation then consumed as
#: a value (data_completeness_gate.md §0, failure class F2).
MIN_DAILY_FUND = 2

CONTRACT_ID = "h6_funding_vol_regime.completeness"
CONTRACT_VERSION = 1

#: h6's own identity for the readiness projection. Distinct from the family_id
#: in the evidence ledger: this names a *derivation's inputs*, not a family.
FEATURE_ID = "h6_funding_vol_regime"

_DEFAULT_LAKE_ROOT = _RESEARCH_ROOT / "data" / "raw"


class HistoricalPeriodRefused(RuntimeError):
    """A period other than the current one was submitted for evaluation."""


def _dq(name: str):
    """Import a `research/dq` module by bare name, lazily.

    The dq package imports its siblings by bare name, so its directory must be
    on sys.path -- the same convention its own tests use. Done at call time,
    never at import time: `run_daily.py` and `daily_job.py` must remain
    importable in the research Docker image, which excludes
    `research/evidence_library/` (INV-1), and dq depends on it.
    """
    import importlib  # noqa: PLC0415

    for extra in (_RESEARCH_ROOT / "dq", _RESEARCH_ROOT / "evidence_library"):
        if str(extra) not in sys.path:
            sys.path.insert(0, str(extra))
    return importlib.import_module(name)


def build_contract_doc(lake_root: pathlib.Path | None = None) -> dict:
    """h6's completeness contract, as data (L1: declare, don't code).

    Only the funding input is declared. Spot completeness is already enforced
    fail-closed inside `runner_base` for the seven `run_daily` collectors, and
    h6's own row-count guard covers window length; funding validity is the one
    requirement nothing checks, and it is the one that failed.
    """
    root = pathlib.Path(lake_root or _DEFAULT_LAKE_ROOT)
    return {
        "contract_id": CONTRACT_ID,
        "version": CONTRACT_VERSION,
        "grain": "utc_day",
        "produces": [FEATURE_ID],
        "inputs": [
            {
                "id": "funding",
                "criticality": "required",
                "source": {
                    "type": "parquet",
                    "root": str(root),
                    "dataset": "funding",
                    "symbol": "BTCUSDT",
                    "ts_column": "timestamp_ms",
                    "ts_unit": "ms",
                    "min_records_per_day": MIN_DAILY_FUND,
                    "window_days": MIN_HISTORY,
                },
                "continuity": {
                    "check": "continuity.no_interior_gaps.v1",
                    "min_rows": REGIME_WINDOW,
                    "no_interior_gaps": False,
                },
                "remediation": (
                    "python research/forward_evidence/daily_ingest.py   "
                    "# backfill funding months until >= "
                    f"{REGIME_WINDOW} valid days exist in the trailing "
                    f"{MIN_HISTORY}-day window"
                ),
            },
        ],
    }


def evaluate_current(
    db_path: pathlib.Path | str,
    period: str,
    as_of: str | datetime,
    actor: str,
    lake_root: pathlib.Path | None = None,
    today: str | None = None,
):
    """Record h6's completeness verdict for the CURRENT period only.

    The anchor is `today`, defaulting to the UTC date of `as_of`; the accepted
    periods are that day and the one before it (the day in progress, and the
    last complete UTC day). Anything older is refused rather than evaluated:
    re-verdicting a historical day would manufacture a governance row for a day
    nobody evaluated, which PR-7's L4 forbids and which FB-11 recorded as
    permanently out of bounds.
    """
    as_of_dt = (
        datetime.fromisoformat(as_of) if isinstance(as_of, str) else as_of
    )
    if as_of_dt.tzinfo is None:
        as_of_dt = as_of_dt.replace(tzinfo=timezone.utc)

    # "Present" means the day in progress or the last complete UTC day -- the
    # same two framings `run_daily._derive_run_date()` already recognises (it
    # returns yesterday UTC, "the last complete BTC calendar day"). Anything
    # older is history.
    anchor = (
        datetime.fromisoformat(today).date() if today else as_of_dt.date()
    )
    allowed = {anchor.isoformat(), (anchor - timedelta(days=1)).isoformat()}
    if period not in allowed:
        raise HistoricalPeriodRefused(
            f"period {period!r} is not the current period (allowed: "
            f"{sorted(allowed)}). This producer records the present state only; "
            "historical h6 days are governed by "
            "docs/governance/fb11_h6_unevaluable_window_provenance.md and are "
            "never re-verdicted."
        )

    dq_readiness = _dq("readiness")
    _dq("probes")                               # registers the 'parquet' probe

    # NOTE: evaluation NEVER activates or mutates the contract registry. If the
    # contract is not active, `evaluate_and_record` renders CONFIGURATION_ERROR
    # and still writes a receipt -- fail-closed, and a period nobody could
    # evaluate stays visibly distinct from one that passed. Activation is a
    # separate operator act; see `activate()`.
    dq_readiness.init_db(db_path)
    return dq_readiness.evaluate_and_record(
        db_path,
        contract_id=CONTRACT_ID,
        period=period,
        as_of=as_of_dt,
        actor=actor,
        actor_type="human",
    )


def activate(
    db_path: pathlib.Path | str,
    actor: str,
    reason: str = "FB-12a: declare h6 funding-history completeness",
    lake_root: pathlib.Path | None = None,
) -> dict:
    """Activate this contract through the EXISTING PR-3 lifecycle. Operator act.

    Deliberately separate from `evaluate_current()`: a gate that could activate
    its own contract could also silently redefine what it was gating. Uses
    `contract_lifecycle.activate_contract` unchanged -- no new registry
    machinery, no new receipt type.
    """
    contract_lifecycle = _dq("contract_lifecycle")
    _dq("contract_registry").init_db(db_path)   # idempotent schema-ensure

    return contract_lifecycle.activate_contract(
        db_path,
        contract_id=CONTRACT_ID,
        body=build_contract_doc(lake_root),
        actor=actor,
        reason=reason,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Record h6's current-period input-completeness verdict.")
    p.add_argument("--db", required=True, type=pathlib.Path)
    p.add_argument("--period", required=True, metavar="YYYY-MM-DD")
    p.add_argument("--as-of", required=True)
    p.add_argument("--actor", required=True)
    p.add_argument("--lake-root", type=pathlib.Path, default=None)
    args = p.parse_args(argv)

    result = evaluate_current(
        args.db, args.period, args.as_of, args.actor, lake_root=args.lake_root)
    v = result.verdict
    print(f"{v.state}  action={v.action}  may_derive={result.may_derive}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
