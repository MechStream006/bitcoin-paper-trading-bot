"""One real APScheduler instance drives the capture + outcome-writer pipeline.

Every prior test exercises `capture_decision_features` and
`outcome_writer.sweep()` individually, against fixtures, called directly.
None boots the actual registration path -- `register_strategy_job` and
`register_outcome_writer_job` together on one real scheduler -- the gap
`docs/adr/0003-live-entry-policy.md` and CLAUDE.md sec.22 name explicitly:
"has never run against a real forward store or a real Delta ACK." This closes
that gap locally, on fixtures only.

Fixtures/doubles are reused verbatim from the existing suites
(`tests/test_strategy_jobs.py`, `tests/test_outcome_writer.py`); no fake
scheduler is added here -- `src.scheduler.runner.SchedulerService`, the real
production wrapper around APScheduler's `BackgroundScheduler`, is used
directly. Both jobs are registered with a long interval so APScheduler never
fires them on its own background thread (which would race the single
`sqlite3.Connection` used for setup and assertions); each job's own wrapped
callable -- the exact object APScheduler would call on schedule -- is invoked
once, synchronously, to make ordering deterministic without touching
`register_strategy_job`/`register_outcome_writer_job` or the scheduler
wrapper itself.
"""
from __future__ import annotations

import pathlib
import sys
from datetime import datetime, timedelta, timezone

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scheduler.runner import SchedulerService  # noqa: E402
from src.strategies import strategy_jobs as SJ  # noqa: E402
from src.strategies.outcome_jobs import (  # noqa: E402
    OUTCOME_JOB_ID, register_outcome_writer_job,
)

from tests.test_strategy_jobs import (  # noqa: E402
    FakeClient, FakeExecutor, FakeReconciler, FakeRisk, _forward_db, conn,
)
from tests.test_outcome_writer import add_completed  # noqa: E402

_RESEARCH = _ROOT / "research"
if str(_RESEARCH) not in sys.path:
    sys.path.insert(0, str(_RESEARCH))
from feature_store import decision_store as DS  # noqa: E402
from label_store import outcome_store as OS  # noqa: E402


def test_capture_and_outcome_pipeline_runs_end_to_end_on_one_real_scheduler(
    tmp_path, conn, monkeypatch,
):
    # --- fixtures: tmp-only, never production paths ------------------------
    features_db = tmp_path / "decision_features.db"
    outcomes_db = tmp_path / "decision_outcomes.db"
    trading_db_path = tmp_path / "btc_trading.db"
    assert features_db.is_relative_to(tmp_path)
    assert outcomes_db.is_relative_to(tmp_path)
    assert trading_db_path.is_relative_to(tmp_path)

    # register_strategy_job() -> evaluate_once() -> capture_decision_features()
    # has no db_path parameter to override; it resolves DS.DEFAULT_DB at call
    # time, so redirecting it for this test means patching that one attribute.
    monkeypatch.setattr(DS, "DEFAULT_DB", features_db)

    conn.execute(
        "INSERT INTO instruments "
        "(id, symbol, contract_type, contract_value, tick_size, fetched_at) "
        "VALUES (84, 'BTCUSD', 'perpetual_futures', 0.001, 0.1, ?)",
        (datetime.now(timezone.utc).isoformat(),))
    conn.commit()

    # A forward entry event readable a few minutes ago in REAL wall-clock
    # time: the registered job path calls evaluate_once() with no `now=`
    # override, so it uses datetime.now(timezone.utc), and the entry must be
    # inside bridge.MIRROR_WINDOW (1h) when that fires.
    readable = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    fwd = _forward_db(tmp_path, direction=-1, readable_ts=readable)

    # FakeClient bakes in a fixed quote timestamp (module constant NOW); the
    # registered job path calls _quote() with real wall-clock `now` and
    # refuses anything older than 10s, so the canned quote must be re-timed
    # to the instant this test actually runs.
    client = FakeClient()
    client._t["result"]["timestamp"] = (
        datetime.now(timezone.utc).timestamp() * 1_000_000)

    # --- one real APScheduler instance, both jobs registered on it ---------
    scheduler = SchedulerService()

    assert SJ.register_strategy_job(
        scheduler, forward_db_path=fwd, conn=conn, client=client,
        risk_manager=FakeRisk(), executor=FakeExecutor(),
        reconciler=FakeReconciler(), strategy_enabled=True, dry_run=False,
        interval_seconds=3600) is True
    assert register_outcome_writer_job(
        scheduler, trading_db_path=trading_db_path, strategy_enabled=True,
        features_db=features_db, outcomes_db=outcomes_db,
        status_path=tmp_path / ".outcome-status.json",
        interval_seconds=3600) is True

    assert scheduler.has_job(SJ.STRATEGY_JOB_ID)
    assert scheduler.has_job(OUTCOME_JOB_ID)

    # No scheduler registration collision: re-registering either job is a
    # documented no-op (idempotent False), not a raised error or a duplicate.
    assert SJ.register_strategy_job(
        scheduler, forward_db_path=fwd, conn=conn, client=FakeClient(),
        risk_manager=FakeRisk(), executor=FakeExecutor(),
        reconciler=FakeReconciler(), strategy_enabled=True,
        dry_run=False) is False
    assert register_outcome_writer_job(
        scheduler, trading_db_path=trading_db_path,
        strategy_enabled=True) is False

    scheduler.start()
    try:
        # Both jobs were registered with a 1h interval, so APScheduler's own
        # background thread will not fire either during this test. Each
        # job's wrapped callable -- the identical object APScheduler holds
        # for its own interval trigger -- is invoked directly, once, on this
        # thread: real registration and accounting, deterministic ordering,
        # no second thread touching the shared sqlite3.Connection.
        strategy_job = scheduler._scheduler.get_job(SJ.STRATEGY_JOB_ID)
        outcome_job = scheduler._scheduler.get_job(OUTCOME_JOB_ID)

        strategy_job.func()

        # --- exactly one decision-feature capture landed --------------------
        index = DS.decision_index(features_db)
        assert len(index) == 1, index
        coid = next(iter(index))
        rows = DS.load_rows(features_db, decision_id=coid)
        assert len(rows) == 4, "one row per contract feature (v3: 4 features)"
        assert {r["feature_name"] for r in rows} == {
            "spot_price", "option_atm_iv", "signal_position",
            "consumed_direction"}

        # Terminalize the fixture position/order with the outcome-writer's own
        # existing mechanism for a COMPLETED trade (test_outcome_writer.py).
        # Its default timestamps are a fixed 2026-09-01 fixture date; the real
        # decision instant just captured is "now", so the fill/exit instants
        # must be overridden to occur after it or build_labels() correctly
        # raises LabelLeakageError (entry preceding its own decision).
        after_decision = datetime.now(timezone.utc) + timedelta(seconds=5)
        add_completed(
            conn, coid,
            entry_proc=after_decision.isoformat(),
            exit_proc=(after_decision + timedelta(seconds=5)).isoformat())

        outcome_job.func()
    finally:
        scheduler.stop(wait=True)

    # --- the corresponding terminal outcome landed, exactly once -----------
    labels = OS.load_labels(outcomes_db)
    assert len(labels) == 1
    assert labels[0]["decision_id"] == coid
    assert labels[0]["status"] == OS.COMPLETED

    # --- no duplicate outcome on a second sweep -----------------------------
    outcome_job.func()
    assert len(OS.load_labels(outcomes_db)) == 1
