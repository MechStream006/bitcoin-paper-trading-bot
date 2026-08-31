"""
forward_snapshot.py
===================
Operator-invoked producer of a **forward evidence snapshot**: one new append-only
L2 run carrying a frozen `forward_admissibility` payload.

WHY THIS EXISTS
---------------
`ForwardAdmissibilityRule` (FB-8) reads its evidence from the run's own
`metrics_json`, because `evidence_governance.admit_evidence()` calls
`rule.evaluate(family_meta, run["metrics"], thresholds)` and records `inputs=()`.
Replay therefore re-reads the run row and re-runs the rule. A rule that read the
live corpus would let a historical verdict drift as that corpus grows; a payload
frozen inside an append-only row cannot.

`ledger_sync` -- the nightly collector -- does not build that payload, and must
not: evaluating observation floors inside the scheduled path would put an
assessment step in an unattended job. This module is the operator-invoked
alternative.

WHAT IT DOES
------------
For one family at one `as_of`: reads the forward corpus, evaluates the FB-7
observation floors against the hash-pinned pre-registration, builds the FB-8
payload, builds the FB-12e readiness block over the run's own evidence window,
and appends ONE new L2 run containing both.

Readiness is frozen into the row at creation (FB-12g) because that is the only
moment at which it is contemporaneous, and because `DefaultReadinessProvider`
reads a `readiness` block **as recorded** and never recomputes it. Exclusions
come solely from `run_date_policy.yaml immutable_gaps`; a payload excluding any
undeclared day aborts the snapshot before the row is written.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
- **No DSR / BH-p.** `governance=None` always, so both stay NULL. Computing them
  is a different authority and a different gate.
- **No admission, re-pin, transition, gate_result, receipt or promotion.** This
  module appends a run row and nothing else.
- **No modification of any existing row.** The ledger is append-only; a snapshot
  is always a *new* `(family_id, provenance, window_end)`.

OPERATOR-INVOKED, STRUCTURALLY
------------------------------
`actor` is required and a service actor is refused before anything is read,
mirroring `transition_governance`'s existing refusal. Nothing in the scheduled
path imports this module.

REUSE
-----
Schema assertions, the read-only corpus readers, the metric computation and the
idempotency key all come from `ledger_sync`; the floors from
`observation_floors`; the payload shape from `forward_admissibility`. No new
table, no new registry, no new governance mechanism.
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
from typing import Any

_HERE = pathlib.Path(__file__).resolve().parent
_RESEARCH_ROOT = _HERE.parent
_REPO_ROOT = _RESEARCH_ROOT.parent
if str(_RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_ROOT))

from forward_evidence import forward_admissibility as fa  # noqa: E402
from forward_evidence import ledger_sync as ls  # noqa: E402
from forward_evidence import forward_readiness as fr  # noqa: E402
from forward_evidence import observation_floors as of  # noqa: E402

log = logging.getLogger(__name__)

#: Identifies rows written by THIS producer, distinct from the nightly
#: collector's "forward-collect-v1", so the two are separable in the ledger.
ENGINE_VERSION = "forward-snapshot-v1"

#: Refused outright. A snapshot is an operator act.
_REFUSED_ACTOR_TYPES = frozenset({"service", "cron", "system"})

_DEFAULT_FORWARD_DB = _RESEARCH_ROOT / "data" / "evidence_forward.db"


class ActorRefusedError(RuntimeError):
    """A non-human actor attempted a snapshot."""


def build_snapshot_metrics(
    family_id: str,
    signal_id: str,
    registry: dict,
    forward_db: pathlib.Path,
    as_of: str,
    repo_root: pathlib.Path | None = None,
) -> dict[str, Any] | None:
    """The COMPLETE payload for one snapshot, or None when there is no
    closed-trade evidence.

    "Complete" is the point: this returns exactly what `write_snapshot()`
    persists -- metrics, `forward_admissibility` and `readiness` -- so a
    `--dry-run` and a real write render the same bytes. Nothing is added to the
    payload after this function returns.

    Deterministic for a fixed (corpus, as_of): the trade set is fully ordered
    and every statistic is a pure function of it. Returns None -- rather than a
    zero-trade row -- because a family with no closed trade has no forward
    evidence, and a row asserting otherwise would make `has_live_evidence`
    report something untrue (the FB-1b decision, applied here unchanged).

    Reads only. The immutable-gap invariant is enforced here, so it also guards
    a dry run, and a payload with an undeclared exclusion raises before any
    caller can persist it.
    """
    trades = ls.load_closed_trades(forward_db, signal_id, as_of)
    if not trades:
        return None

    metrics = ls.compute_forward_metrics(trades)
    metrics["window_first_entry"] = trades[0]["entry_date"]
    metrics["window_last_exit"] = trades[-1]["exit_date"]
    metrics["source"] = "evidence_forward.db:trade_returns"
    metrics["closed_trades_only"] = True

    report = of.evaluate(
        family_id, registry, forward_db, as_of,
        signal_id=signal_id, repo_root=repo_root,
    )
    metrics[fa.FORWARD_ADMISSIBILITY_KEY] = fa.build_payload(
        report, ls.PROVENANCE, ls.RUN_KIND,
    )

    # FB-12i-R: readiness is built HERE, not in write_snapshot, so a --dry-run
    # renders the exact payload a real write would persist. Building it after
    # the dry-run early-return was the FB-12i defect: the dry run displayed a
    # payload the write would never produce, which is worse than no dry run at
    # all, because it looks like verification.
    #
    # `window_start` is the run's own, computed once here and carried inside the
    # block, so `write_snapshot` reads it back rather than recomputing it and
    # the row can never disagree with the block describing it.
    window_start = ls._first_run_date(forward_db, signal_id) or metrics["window_first_entry"]
    readiness = fr.build_readiness_block(
        family_id, window_start, as_of, forward_db=forward_db, signal_id=signal_id)
    fr.assert_exclusions_declared(readiness)
    metrics[fr.READINESS_KEY] = readiness
    return metrics


def write_snapshot(
    family_id: str,
    signal_id: str,
    registry: dict,
    forward_db: pathlib.Path,
    ledger_db: pathlib.Path,
    as_of: str,
    actor: str,
    actor_type: str = "human",
    dry_run: bool = False,
    repo_root: pathlib.Path | None = None,
) -> tuple[str | None, str]:
    """Append one L2 snapshot run. Returns (run_id | None, outcome).

    outcome is one of:
      'written'      a new snapshot run was appended
      'skipped'      a live-forward run already exists at this as_of (no write)
      'no-evidence'  no closed trades as of this date (no write)
      'dry-run'      the payload was built; nothing was written
    """
    if not actor:
        raise ActorRefusedError("a snapshot requires a named actor")
    if actor_type.lower() in _REFUSED_ACTOR_TYPES:
        raise ActorRefusedError(
            f"actor_type={actor_type!r} is refused: a forward snapshot is an "
            "operator act and must not be produced by an unattended process"
        )

    ls.assert_ledger_schema(ledger_db)
    ls.assert_forward_schema(forward_db)

    existing = ls._existing_live_forward_run(ledger_db, family_id, as_of)
    if existing:
        log.info("[snapshot] %s: live-forward run already present for %s (skip)",
                 family_id, as_of)
        return existing, "skipped"

    metrics = build_snapshot_metrics(
        family_id, signal_id, registry, forward_db, as_of, repo_root=repo_root)
    if metrics is None:
        log.info("[snapshot] %s: no closed forward trades as of %s (nothing written)",
                 family_id, as_of)
        return None, "no-evidence"

    if dry_run:
        log.info("[snapshot] %s: dry-run, nothing written", family_id)
        return None, "dry-run"

    # Read back the window the payload was built for. NOT recomputed: one
    # source of truth means the row and its readiness block cannot diverge,
    # and it is the same value a --dry-run already displayed.
    window_start = metrics[fr.READINESS_KEY]["window_start"]

    run_id = ls._record().append_run(
        ledger_db,
        family_id=family_id,
        metrics=metrics,
        provenance=ls.PROVENANCE,
        run_kind=ls.RUN_KIND,
        window_start=window_start,
        window_end=as_of,
        git_sha=ls._git_sha(),
        fn_hash=ls._fn_hash(forward_db, signal_id, as_of),
        engine_version=ENGINE_VERSION,
        n_trials=ls.N_TRIALS,
        # governance omitted on purpose -> dsr_prob / bh_p stay NULL. Computing
        # them is a separate gate with a separate authority.
        governance=None,
    )
    payload = metrics[fa.FORWARD_ADMISSIBILITY_KEY]
    blocking = [f["name"] for f in payload["floors"] if f["status"] != "MET"]
    log.info("[snapshot] %s: run %s  n_trades=%d  floors_blocking=%s",
             family_id, run_id[:8], metrics["n_trades"], blocking or "none")
    return run_id, "written"


def _load_registry(path: pathlib.Path) -> dict:
    import yaml
    return yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Append one operator-invoked forward evidence snapshot.")
    parser.add_argument("--family", required=True)
    parser.add_argument("--signal-id", default=None,
                        help="forward-corpus signal_id (default: same as --family)")
    parser.add_argument("--as-of", required=True, metavar="YYYY-MM-DD")
    parser.add_argument("--actor", required=True,
                        help="the person performing this snapshot")
    parser.add_argument("--actor-type", default="human")
    parser.add_argument("--forward-db", type=pathlib.Path, default=_DEFAULT_FORWARD_DB)
    parser.add_argument("--ledger-db", type=pathlib.Path, default=ls.DEFAULT_LEDGER_PATH)
    parser.add_argument("--registry", type=pathlib.Path,
                        default=_RESEARCH_ROOT / "evidence_library" / "registry.yaml")
    parser.add_argument("--dry-run", action="store_true",
                        help="build and print the payload; write nothing")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)sZ %(levelname)s %(message)s")

    registry = _load_registry(args.registry)
    if args.dry_run:
        metrics = build_snapshot_metrics(
            args.family, args.signal_id or args.family, registry,
            args.forward_db, args.as_of)
        print(json.dumps(metrics, indent=2, sort_keys=True, default=str)
              if metrics else "no closed forward trades as of %s" % args.as_of)
        return 0

    run_id, outcome = write_snapshot(
        args.family, args.signal_id or args.family, registry,
        args.forward_db, args.ledger_db, args.as_of,
        actor=args.actor, actor_type=args.actor_type,
    )
    print(f"{outcome}  {run_id or ''}".strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
