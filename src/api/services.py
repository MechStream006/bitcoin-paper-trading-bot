"""Adapters over existing business logic (ADR-0001 §15.3).

Every function here is a **thin projection** of logic that already exists and is
already the production source of truth. Nothing in this module re-derives a
health severity, re-implements a governance check, or re-generates a report:

    runtime health      -> scripts/healthcheck.py       (the same functions the
                                                         10-min dead-man timer runs)
    forward evidence    -> src/alerts/forward_status.py (the same summarize()
                                                         the Telegram heartbeat renders)
    governance verify   -> research/evidence_library/governance_verification.py
                           (PR-9's read-only engine, itself a fold over PR-1..PR-8)
    evidence summary    -> research/evidence_library/report.py::build_context
                           (PR-5's single pure-snapshot point)

That is the whole design. A second implementation of any of these would be a
second enforcement point that can disagree with the first, which
`evidence_reporting_architecture.md` §7.3 explicitly forbids -- and an API that
reports different governance state than `report.py --check` is worse than no
API, because it would be believed.

`build_context()` is deliberately consumed directly rather than through
`generate_state_board()` / `generate_library()`: those render **markdown to
files**, and calling them would mean writing to the repo from an HTTP request.
The ADR's "do not duplicate report generation" is satisfied by reusing the
snapshot the generators consume, not by re-running the generators.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from src.api.config import ROOT

log = logging.getLogger(__name__)


class SubsystemUnavailable(RuntimeError):
    """A subsystem could not be read. Surfaced as 503, never as a false green.

    Fail-closed mirrors `governance_verification`'s own stated contract: a
    verification that cannot run is a failure, not a pass.
    """


# ---------------------------------------------------------------------------
# Lazy imports
#
# These modules live outside `src/` and each performs its own sys.path
# insertion at import time. They are imported on first use, not at module
# import, for two reasons: an import failure in the research tree must degrade
# ONE endpoint to 503 rather than prevent the whole API from starting, and the
# API process should not pay for parsing the governance stack to answer
# /health.
# ---------------------------------------------------------------------------

def _import_healthcheck():
    scripts_dir = ROOT / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        import healthcheck  # type: ignore
        return healthcheck
    except Exception as exc:  # noqa: BLE001
        raise SubsystemUnavailable(f"healthcheck module unavailable: {exc}") from exc


def _import_evidence_library():
    lib_dir = ROOT / "research" / "evidence_library"
    if str(lib_dir) not in sys.path:
        sys.path.insert(0, str(lib_dir))
    try:
        import governance_verification as gv  # type: ignore
        import report as report_mod  # type: ignore
        return gv, report_mod
    except Exception as exc:  # noqa: BLE001
        raise SubsystemUnavailable(f"evidence library unavailable: {exc}") from exc


def _import_forward_status():
    try:
        from src.alerts import forward_status
        return forward_status
    except Exception as exc:  # noqa: BLE001
        raise SubsystemUnavailable(f"forward_status unavailable: {exc}") from exc


# ---------------------------------------------------------------------------
# Runtime health
# ---------------------------------------------------------------------------

def runtime_health(settings) -> dict:
    """Reuse healthcheck.py's default check set and its worst-of roll-up.

    The set matches `healthcheck.main()` exactly, with one deliberate exclusion:
    `check_integrity()` (PRAGMA integrity_check). In healthcheck.py that is
    gated behind `--full-integrity` and documented as "expensive -- seconds for
    large DB"; a full-table integrity scan triggered by an unauthenticated-
    adjacent HTTP request is precisely the "synchronous long-running
    transaction" this phase forbids. Integrity remains available where it
    already lives: the CLI, on demand.

    Includes the ledger backup freshness check (P1 Increment 1) alongside the
    trading-DB one, matching `healthcheck.main()`'s addition of the same check
    -- the CLI and this API surface must not silently diverge on what "backup
    health" means.
    """
    hc = _import_healthcheck()

    db_path = settings.trading_db_path
    checks = [
        hc.check_service(settings.service_name),
        hc.check_disk(db_path.parent),
        hc.check_db_liveness(db_path),
        hc.check_wal(db_path),
        hc.check_backup_freshness(settings.backup_dir),
        # required=False mirrors scripts/healthcheck.py exactly -- CLI and
        # read-only API must never disagree about health (P1 Increment 1).
        hc.check_backup_freshness(
            settings.ledger_backup_dir, label="ledger", check_name="ledger_backup",
            required=False,
        ),
        hc.check_backup_freshness(
            settings.label_store_backup_dir, label="label_store",
            check_name="label_store_backup", required=False,
        ),
        hc.check_backup_freshness(
            settings.feature_store_backup_dir, label="feature_store",
            check_name="feature_store_backup", required=False,
        ),
        hc.check_backup_freshness(
            settings.dataset_builder_backup_dir, label="dataset_builder",
            check_name="dataset_builder_backup", required=False,
        ),
        hc.check_execution_status(db_path.parent),
        hc.check_offhost_freshness(settings.backup_dir, check_name="offhost_backup"),
        hc.check_offhost_freshness(
            settings.ledger_backup_dir, check_name="ledger_offhost_backup"
        ),
        hc.check_forward_cycle(settings.forward_db_path),
        # H6 lives alongside evidence_forward.db in the same research/data
        # directory -- no new ApiSettings field needed, matching
        # scripts/healthcheck.py's own default derivation.
        hc.check_h6_cycle(settings.forward_db_path.parent / "forward_evidence.db"),
    ]

    overall = hc.OK
    for r in checks:
        if hc._RANK[r.severity] > hc._RANK[overall]:
            overall = r.severity

    return {
        "overall": overall,
        "checks": [
            {
                "name": r.name,
                "severity": r.severity,
                "message": r.message,
                "detail": r.detail,
            }
            for r in checks
        ],
        "integrity_check_included": False,
    }


# ---------------------------------------------------------------------------
# Forward evidence
# ---------------------------------------------------------------------------

def forward_evidence_status(settings) -> dict:
    """Reuse `forward_status.summarize()` -- the exact call the heartbeat makes.

    `summarize()` already opens the DB with a `mode=ro` file URI and a 2s
    timeout, and is documented to never raise. It is therefore WAL-safe and
    bounded by construction, with no wrapper needed here.
    """
    fs_mod = _import_forward_status()
    fs = fs_mod.summarize(str(settings.forward_db_path))
    return {
        "state": fs.state,
        "severity": fs.severity(),
        "latest_run_date": fs.latest_run_date,
        "expected_run_date": fs.expected_run_date,
        "signals": dict(fs.signals),
        "n_ok": fs.n_ok,
        "n_logged": fs.n_logged,
        "detail": fs.detail,
        "line": fs_mod.render_line(fs),
    }


# ---------------------------------------------------------------------------
# Governance verification
# ---------------------------------------------------------------------------

def governance_chain(settings) -> dict:
    """Hash-chain integrity only (PR-1). The cheap, dedicated entry point."""
    gv, _ = _import_evidence_library()
    report = gv.verify_receipt_chain(settings.ledger_db_path)
    return report.to_dict() | {"summary": report.summary()}


def governance_verification(settings, as_of: str | None = None) -> dict:
    """Full read-only governance sweep (PR-9), point-in-time correct.

    `as_of` is threaded verbatim into `verify_governance`, which already
    implements per-subsystem `as_of` handling. Reconstructing that here would
    be the divergence this module exists to avoid.
    """
    gv, report_mod = _import_evidence_library()

    registry = thresholds = None
    try:
        registry = report_mod._load_registry(settings.registry_path)
        thresholds = report_mod._load_thresholds(settings.thresholds_path)
    except Exception as exc:  # noqa: BLE001
        # verify_governance() explicitly tolerates missing registry/thresholds
        # by skipping those subsystems with an `info` finding rather than
        # crashing. Pass None and let it say so, rather than inventing a verdict.
        log.warning(
            "registry/thresholds unreadable; governance sweep will skip those subsystems",
            extra={"event": "registry_load_failed", "error": str(exc)},
        )

    report = gv.verify_governance(
        settings.ledger_db_path,
        registry=registry,
        thresholds=thresholds,
        as_of=as_of,
    )
    return report.to_dict() | {"summary": report.summary()}


# ---------------------------------------------------------------------------
# Evidence summary
# ---------------------------------------------------------------------------

def _family_to_dict(row, report_mod) -> dict:
    fam = row.fam or {}
    pinned = row.pinned_run or {}
    return {
        "family_id": row.family_id,
        "state": row.state,
        "role": report_mod._role_str(fam),
        "hypothesis": fam.get("hypothesis"),
        "pinned_run_id": row.pinned_run_id,
        "pinned_run_kind": row.pinned_run_kind,
        "pinned_lane_code": row.pinned_lane_code,
        "state_ts": row.state_ts,
        "is_pin_stale": row.is_pin_stale,
        "flags": list(row.flags),
        "pinned_run": {
            "sharpe": pinned.get("sharpe"),
            "dsr_prob": pinned.get("dsr_prob"),
            "bh_p": pinned.get("bh_p"),
            "bh_p_convention": report_mod.bh_convention.classify(pinned),
            "n_trades": pinned.get("n_trades"),
            "run_ts": pinned.get("run_ts"),
            "provenance": pinned.get("provenance"),
        } if row.pinned_run else None,
        "lanes": {
            kind: {
                "run_id": run.get("run_id"),
                "run_ts": run.get("run_ts"),
                "sharpe": run.get("sharpe"),
                "provenance": run.get("provenance"),
            }
            for kind, run in (row.lane_runs or {}).items()
        },
    }


def evidence_summary(settings, as_of: str | None = None) -> dict:
    """Project PR-5's `build_context()` snapshot as JSON.

    State is derived from the append-only `state_transitions` ledger by
    `build_context`, never read from `registry.yaml` -- that property is
    inherited, not re-established here.
    """
    _, report_mod = _import_evidence_library()

    ctx = report_mod.build_context(
        registry_path=settings.registry_path,
        thresholds_path=settings.thresholds_path,
        ledger_path=settings.ledger_db_path,
        as_of=as_of,
    )

    families = [_family_to_dict(row, report_mod) for row in ctx.rows.values()]
    families.sort(key=lambda f: (report_mod._state_rank(f["state"]), f["family_id"]))

    by_state: dict[str, int] = {}
    for f in families:
        by_state[f["state"]] = by_state.get(f["state"], 0) + 1

    return {
        "as_of": ctx.as_of,
        "n_families": len(families),
        "n_runs": ctx.n_runs,
        "n_transitions": ctx.n_transitions,
        "counts_by_state": by_state,
        "families": families,
    }


def evidence_family(settings, family_id: str, as_of: str | None = None) -> dict | None:
    """One family from the same snapshot. Returns None if unknown."""
    summary = evidence_summary(settings, as_of=as_of)
    for fam in summary["families"]:
        if fam["family_id"] == family_id:
            return fam | {"as_of": summary["as_of"]}
    return None


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------

def readiness(settings) -> dict:
    """`/readyz` per §15.3: ledger readable + chain valid.

    Uses the cheap chain-only verifier, not the full sweep: readiness answers
    "can this service serve", not "is governance in good standing". A
    governance *finding* is data this API exists to report, so it must not make
    the API itself look unavailable.
    """
    checks: dict[str, Any] = {}
    ok = True

    ledger = settings.ledger_db_path
    checks["ledger_present"] = ledger.exists()
    if not ledger.exists():
        return {"ready": False, "checks": checks, "detail": f"ledger not found: {ledger}"}

    try:
        chain = governance_chain(settings)
        checks["chain_valid"] = bool(chain.get("ok"))
        checks["n_receipts"] = chain.get("counts", {}).get("receipts", 0)
        ok = ok and bool(chain.get("ok"))
    except SubsystemUnavailable as exc:
        checks["chain_valid"] = False
        return {"ready": False, "checks": checks, "detail": str(exc)}

    return {"ready": ok, "checks": checks, "detail": "ok" if ok else "receipt chain invalid"}
