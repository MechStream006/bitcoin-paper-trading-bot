"""Scheduler registration for the execution-outcome sweep.

Separate from `strategy_jobs` for the same structural reason
`execution/reconciliation_jobs` is separate from `Reconciler`: job ownership
must not follow component lifetime, and this sweep must sit as far from the
trading path as the scheduler allows.

NOT safety-critical, and it holds no connection into the live runtime. When the
job fires it opens its own read-only handles on `btc_trading.db` and
`decision_features.db` and (only to write a proven-final outcome) a handle on
`decision_outcomes.db` -- exactly as `research/execution_evidence/`
`completed_trades.py` does. Every exception is swallowed and recorded in
`.outcome-status.json`, read out-of-process by `scripts/healthcheck.py`, so a
research failure can never reach `EvaluationResult` or the scheduler's failure
counter.
"""
from __future__ import annotations

import json
import logging
import pathlib
import sys
from datetime import datetime, timezone

log = logging.getLogger(__name__)

OUTCOME_JOB_ID = "outcome_writer"

OUTCOME_STATUS_FILENAME = ".outcome-status.json"

#: Hourly. The sweep is idempotent and event-driven work (a captured decision
#: resolves once its order terminalises or its position closes, on the order of
#: days), so a tighter cadence would only re-scan an unchanged trading DB.
DEFAULT_OUTCOME_INTERVAL_SEC = 3600


def _run_sweep(trading_db_path, features_db, outcomes_db):
    """Import the research sweep lazily and run it. Same lazy `src -> research`
    pattern `bridge.capture_decision_features` already uses."""
    research_root = pathlib.Path(__file__).resolve().parents[2] / "research"
    if str(research_root) not in sys.path:
        sys.path.insert(0, str(research_root))
    from feature_store import decision_store as DS
    from label_store import outcome_store as OS
    from label_store import outcome_writer

    return outcome_writer.sweep(
        features_db or DS.DEFAULT_DB,
        outcomes_db or OS.DEFAULT_DB,
        str(trading_db_path),
    )


def write_outcome_status(status_path, *, ok: bool, detail: str = "",
                         result=None, now: datetime | None = None) -> None:
    """Persist the outcome of the most recent sweep for an out-of-process check.

    Last attempt wins, like `.capture-status.json` / `.execution-status.json`.
    Best-effort by construction: a status-write failure is logged and dropped,
    never raised.
    """
    if status_path is None:
        return
    try:
        payload = {
            "schema": "btcbot-outcome-status/1",
            "ts": (now or datetime.now(timezone.utc)).isoformat(),
            "status": "ok" if ok else "failed",
            "detail": str(detail or ""),
        }
        if result is not None:
            payload.update({
                "scanned": result.scanned,
                "already_labelled": result.already_labelled,
                "persisted": result.persisted,
                "unchanged": result.unchanged,
                "pending": result.pending,
                "anomalies": list(result.anomalies),
            })
        path = pathlib.Path(status_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception as exc:  # noqa: BLE001 -- never disturb the runtime
        log.warning("[outcome] could not write outcome status: %s: %s",
                    type(exc).__name__, exc)


def run_outcome_sweep(trading_db_path, *, features_db=None, outcomes_db=None,
                      status_path=None) -> None:
    """One sweep. Never raises -- the failure is recorded, not propagated."""
    try:
        result = _run_sweep(trading_db_path, features_db, outcomes_db)
    except Exception as exc:  # noqa: BLE001 -- research must not disturb trading
        log.warning("[outcome] sweep failed: %s: %s", type(exc).__name__, exc)
        write_outcome_status(status_path, ok=False,
                             detail=f"{type(exc).__name__}: {exc}")
        return

    if result.anomalies:
        log.warning("[outcome] sweep recorded %d anomal%s: %s",
                    len(result.anomalies),
                    "y" if len(result.anomalies) == 1 else "ies",
                    "; ".join(result.anomalies))
    log.info("[outcome] sweep: scanned=%d labelled=%d persisted=%d unchanged=%d "
             "pending=%d", result.scanned, result.already_labelled,
             result.persisted, result.unchanged, result.pending)
    write_outcome_status(status_path, ok=True, result=result)


def register_outcome_writer_job(
    scheduler,
    *,
    trading_db_path,
    strategy_enabled: bool,
    features_db=None,
    outcomes_db=None,
    status_path=None,
    interval_seconds: int = DEFAULT_OUTCOME_INTERVAL_SEC,
    job_id: str = OUTCOME_JOB_ID,
) -> bool:
    """Register the periodic execution-outcome sweep. Idempotent.

    Returns True if this call registered it. A disabled strategy registers no
    job -- with no live entries there is nothing to capture and nothing to
    resolve. Registration never raises on a duplicate.
    """
    if not strategy_enabled:
        log.info("[outcome] not registered (DEMO_STRATEGY_ENABLED=0)")
        return False
    if scheduler.has_job(job_id):
        log.info("[outcome] job %r already registered; not re-registering", job_id)
        return False

    def _job() -> None:
        try:
            run_outcome_sweep(trading_db_path, features_db=features_db,
                              outcomes_db=outcomes_db, status_path=status_path)
        except Exception as exc:  # noqa: BLE001 -- never raise into the scheduler
            log.error("[outcome] job raised %s: %s", type(exc).__name__, exc)

    scheduler.register("outcome_writer", _job, trigger="interval",
                       job_id=job_id, seconds=interval_seconds)
    log.info("[outcome] registered execution-outcome sweep every %ss",
             interval_seconds)
    return True
