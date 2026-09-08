"""Regression tests for the 2026-09-08 production audit fixes (F-2, F-4..F-7).

Each class pins one confirmed defect. Where a fix depends on production
evidence -- the stale `time` field, the observed quote-age distribution, the
latched halt -- the evidence is stated in the test that encodes it, so a future
reader can tell a deliberate bound from an arbitrary one.
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import healthcheck as hc  # noqa: E402
from src.data import storage  # noqa: E402
from src.execution.order_state import OrderState  # noqa: E402
from src.risk.manager import Decision  # noqa: E402
from src.strategies import bridge, strategy_jobs as SJ  # noqa: E402

UTC = timezone.utc
READABLE = datetime(2026, 9, 11, 1, 0, 41, tzinfo=UTC)
NOW = READABLE + timedelta(minutes=10)
ENTRY_DATE = "2026-09-10"


# ------------------------------------------------------------------ fixtures

class Client:
    """Local stand-in. `timestamp` is what production actually publishes."""

    def __init__(self, age_s=1.0, mark=70000.0, bid=69990.0, ask=70010.0,
                 timestamp="auto", now=None):
        self._age = age_s
        self._mark, self._bid, self._ask = mark, bid, ask
        self._timestamp = timestamp
        self._now = now or NOW

    def get_ticker(self, symbol):
        ts = self._timestamp
        if ts == "auto":
            ts = (self._now - timedelta(seconds=self._age)).timestamp() * 1_000_000
        return {"result": {
            "mark_price": str(self._mark),
            # Production 2026-09-08: this field was frozen a full day behind
            # `timestamp` across 8 consecutive calls. Reading it would refuse
            # every quote, so the code must ignore it.
            "time": "2026-09-07T16:45:24.725969536Z",
            **({} if ts is None else {"timestamp": ts}),
            "quotes": {"best_bid": str(self._bid), "best_ask": str(self._ask)},
        }}

    def get_wallet_balances(self):
        return {"result": [{"asset_symbol": "USD", "balance": "200",
                            "available_balance": "200"}]}


class Executor:
    def __init__(self, state=OrderState.ACKED, record_to=None):
        self.submits = []
        self.state = state
        self._record_to = record_to

    def submit(self, intent, account, now=None):
        self.submits.append(intent)
        if self._record_to is not None:
            _order(self._record_to, intent.client_order_id, intent.side)
        return (self.state, Decision.approve(1.0, 0.1))


class Risk:
    def __init__(self, decision=None):
        self.decision = decision or Decision.approve(5.0, 0.5)

    def validate(self, intent, account, now=None):
        return self.decision


class Reconciler:
    trading_permitted = True
    halt_reason = ""


def _order(conn, coid, side, status="acked", strategy=None, delta_id=None):
    conn.execute(
        "INSERT OR REPLACE INTO orders (client_order_id, delta_order_id,"
        " instrument_id, side, order_type, quantity, price, status, strategy,"
        " placed_ts) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (coid, delta_id, 84, side, "limit_order", 1.0, 70000.0, status,
         strategy or bridge.SIGNAL_ID, NOW.isoformat()))
    conn.commit()


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(tmp_path / "btc.db")
    storage.init_schema(c)
    storage.init_execution_schema(c)
    return c


def forward_db(tmp_path, *, direction=1, readable=READABLE, name="fwd.db"):
    path = tmp_path / name
    c = sqlite3.connect(path)
    c.executescript((ROOT / "research" / "forward_evidence" /
                     "schema_forward.sql").read_text(encoding="utf-8"))
    c.execute("INSERT INTO run_log (signal_id, run_date, fn_hash, status,"
              " created_at) VALUES (?,?,?, 'ok', ?)",
              (bridge.SIGNAL_ID, ENTRY_DATE, "h",
               readable.strftime("%Y-%m-%dT%H:%M:%SZ")))
    c.execute("INSERT INTO trade_entries (signal_id, entry_date, direction,"
              " entry_close_price, planned_exit_date, open) VALUES (?,?,?,?,?,1)",
              (bridge.SIGNAL_ID, ENTRY_DATE, direction, 70000.0, "2026-09-17"))
    c.commit(); c.close()
    return str(path)


def run_entry(fwd, conn, *, now=NOW, executor=None, client=None, risk=None,
              dry_run=False, started=None):
    return SJ.evaluate_once(
        forward_db_path=fwd, conn=conn, client=client or Client(),
        risk_manager=risk or Risk(), executor=executor or Executor(),
        reconciler=Reconciler(), strategy_enabled=True, dry_run=dry_run,
        now=now, service_started_at=started)


# =========================================================== F-2 healthcheck

class TestF2ExecutionStatusCheck:
    """The verdict must not read OK while the execution stack is halted."""

    def _write(self, d, **payload):
        (d / hc.EXECUTION_STATUS_FILENAME).write_text(
            json.dumps(payload), encoding="utf-8")

    def test_absent_is_info_not_alert_noise(self, tmp_path):
        r = hc.check_execution_status(tmp_path)
        assert r.severity == hc.INFO and r.name == "execution"

    def test_halted_is_critical(self, tmp_path):
        self._write(tmp_path, ts=datetime.now(UTC).isoformat(), fatal=True,
                    trading_permitted=False,
                    halt_reason="position mismatch: exchange=+2 local=+1")
        r = hc.check_execution_status(tmp_path)
        assert r.severity == hc.CRITICAL
        assert "position mismatch" in r.message
        assert r.detail["trading_permitted"] is False

    def test_not_permitted_alone_is_critical(self, tmp_path):
        self._write(tmp_path, ts=datetime.now(UTC).isoformat(), fatal=False,
                    trading_permitted=False, halt_reason="halted")
        assert hc.check_execution_status(tmp_path).severity == hc.CRITICAL

    def test_clean_is_ok(self, tmp_path):
        self._write(tmp_path, ts=datetime.now(UTC).isoformat(), fatal=False,
                    trading_permitted=True, detail="last run ok, 0 finding(s)")
        assert hc.check_execution_status(tmp_path).severity == hc.OK

    def test_stale_status_is_warning(self, tmp_path):
        old = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
        self._write(tmp_path, ts=old, fatal=False, trading_permitted=True)
        r = hc.check_execution_status(tmp_path)
        assert r.severity == hc.WARNING and "stale" in r.message

    def test_unreadable_is_warning_not_ok(self, tmp_path):
        (tmp_path / hc.EXECUTION_STATUS_FILENAME).write_text("{not json",
                                                             encoding="utf-8")
        assert hc.check_execution_status(tmp_path).severity == hc.WARNING

    def test_the_check_is_in_the_default_set(self):
        import inspect
        src = inspect.getsource(hc.main)
        assert "check_execution_status" in src

    def test_api_and_cli_do_not_diverge(self):
        import inspect
        from src.api import services
        assert "check_execution_status" in inspect.getsource(services.runtime_health)

    def test_reconciler_health_is_what_gets_persisted(self):
        from src.execution import reconciliation_jobs as rj

        class R:
            trading_permitted = False
            halt_reason = "position mismatch: exchange=+2 local=+1"

            def health(self):
                from src.runtime.health import HealthStatus
                return HealthStatus.degraded("reconciler", self.halt_reason,
                                             fatal=True)

        d = pathlib.Path(__file__).parent
        import tempfile
        tmp = pathlib.Path(tempfile.mkdtemp()) / ".execution-status.json"
        rj.write_execution_status(R(), tmp)
        payload = json.loads(tmp.read_text(encoding="utf-8"))
        assert payload["fatal"] is True
        assert payload["trading_permitted"] is False
        assert "position mismatch" in payload["halt_reason"]
        # The payload must reflect the real HealthStatus fields. Reading it
        # with defensive getattr under guessed names produced "healthy": true
        # (it resolved the HealthStatus.ok CONSTRUCTOR, a truthy function) and
        # an empty detail, on a stack that was in fact halted.
        assert payload["healthy"] is False
        assert payload["component"] == "reconciler"
        assert payload["detail"] == "position mismatch: exchange=+2 local=+1"

    def test_status_payload_is_honest_when_healthy(self):
        from src.execution import reconciliation_jobs as rj

        class R:
            trading_permitted = True
            halt_reason = ""

            def health(self):
                from src.runtime.health import HealthStatus
                return HealthStatus.ok("reconciler", "last run ok, 0 finding(s)")

        import tempfile
        tmp = pathlib.Path(tempfile.mkdtemp()) / ".execution-status.json"
        rj.write_execution_status(R(), tmp)
        payload = json.loads(tmp.read_text(encoding="utf-8"))
        assert payload["healthy"] is True
        assert payload["fatal"] is False
        assert payload["trading_permitted"] is True
        assert payload["detail"] == "last run ok, 0 finding(s)"

    def test_status_write_never_raises(self):
        from src.execution import reconciliation_jobs as rj

        class Broken:
            trading_permitted = True
            halt_reason = ""

            def health(self):
                raise RuntimeError("health exploded")

        rj.write_execution_status(Broken(), "/nonexistent-dir/x/y.json")
        rj.write_execution_status(Broken(), None)


# ====================================================== F-4 claim never leaks

class TestF4ClaimNeverLeaks:
    def test_duplicate_order_path_releases_the_claim(self, tmp_path, conn):
        """The leak: the claim was taken, then this return skipped release."""
        fwd = forward_db(tmp_path)
        coid = bridge.client_order_id(bridge.SIGNAL_ID, ENTRY_DATE, "buy", 1)
        _order(conn, coid, "buy")                      # order exists, no mirror row
        r = run_entry(fwd, conn)
        assert r.outcome == SJ.DUPLICATE
        assert storage.list_mirrors(conn) == [], "claim leaked into PENDING"

    def test_exception_during_claim_check_releases(self, tmp_path, conn, monkeypatch):
        fwd = forward_db(tmp_path)

        def boom(*a, **k):
            raise RuntimeError("get_order exploded")

        monkeypatch.setattr(storage, "get_order", boom)
        r = run_entry(fwd, conn)
        assert r.outcome == SJ.ERROR
        assert storage.list_mirrors(conn) == []

    def test_a_stale_pending_claim_is_swept(self, tmp_path, conn):
        """Simulates a hard kill between claim and promotion."""
        fwd = forward_db(tmp_path)
        storage.claim_forward_entry(
            conn, forward_entry_id=1, signal_id=bridge.SIGNAL_ID,
            entry_date=ENTRY_DATE, direction=1, planned_exit_date="2026-09-17",
            readable_ts=READABLE.isoformat(),
            now_ts=(NOW - timedelta(hours=5)).isoformat())
        assert storage.list_mirrors(conn)[0]["state"] == "PENDING"
        run_entry(fwd, conn, now=NOW + timedelta(hours=3))
        rows = storage.list_mirrors(conn)
        assert rows == [] or rows[0]["state"] == "NOT_MIRRORED", \
            "a stale claim must not survive as PENDING"

    def test_a_swept_claim_can_still_be_entered_inside_its_window(self, tmp_path, conn):
        fwd = forward_db(tmp_path)
        storage.claim_forward_entry(
            conn, forward_entry_id=1, signal_id=bridge.SIGNAL_ID,
            entry_date=ENTRY_DATE, direction=1, planned_exit_date="2026-09-17",
            readable_ts=READABLE.isoformat(),
            now_ts=(READABLE - timedelta(hours=2)).isoformat())
        ex = Executor(record_to=conn)
        r = run_entry(fwd, conn, executor=ex)
        assert r.outcome == SJ.SUBMITTED and len(ex.submits) == 1
        assert storage.list_mirrors(conn)[0]["state"] == "ENTERED"

    def test_a_fresh_claim_is_not_swept(self, conn):
        storage.claim_forward_entry(
            conn, forward_entry_id=9, signal_id=bridge.SIGNAL_ID,
            entry_date=ENTRY_DATE, direction=1, planned_exit_date="2026-09-17",
            readable_ts=READABLE.isoformat(), now_ts=NOW.isoformat())
        freed = storage.release_stale_claims(
            conn, (NOW - bridge.MIRROR_WINDOW).isoformat())
        assert freed == []
        assert storage.list_mirrors(conn)[0]["state"] == "PENDING"

    def test_the_sweep_never_touches_a_real_position(self, conn):
        storage.claim_forward_entry(
            conn, forward_entry_id=9, signal_id=bridge.SIGNAL_ID,
            entry_date=ENTRY_DATE, direction=1, planned_exit_date="2026-09-17",
            readable_ts=READABLE.isoformat(),
            now_ts=(NOW - timedelta(days=3)).isoformat())
        storage.update_mirror(conn, 9, now_ts=NOW.isoformat(), state="ENTERED",
                              entry_client_order_id="x")
        freed = storage.release_stale_claims(conn, NOW.isoformat())
        assert freed == []
        assert storage.list_mirrors(conn)[0]["state"] == "ENTERED"


# ============================================ F-5 strategy owns its connection

class TestF5StrategyConnectionIsNotShared:
    def test_lifecycle_gives_the_strategy_its_own_handle(self):
        src = (ROOT / "src" / "runtime" / "lifecycle.py").read_text(encoding="utf-8")
        assert '_open_owned_connection("strategy")' in src
        assert "conn=strategy_conn," in src
        assert "conn=reconciler.connection," not in src, \
            "the strategy job must not transact on the reconciler's connection"

    def test_the_strategy_joins_the_distinct_connection_assertion(self):
        src = (ROOT / "src" / "runtime" / "lifecycle.py").read_text(encoding="utf-8")
        # Two call sites exist: an early pre-execution one (chain refresher +
        # snapshot writer) and the execution-armed one. The strategy belongs
        # to the latter, so anchor on the block that names the reconciler.
        block = src[src.index("execution=reconciler.connection"):]
        block = block[:block.index(")")]
        assert "strategy=strategy_conn" in block

    def test_the_executor_still_shares_the_reconciler_handle(self):
        """The one sharing that IS deliberate must survive."""
        src = (ROOT / "src" / "runtime" / "lifecycle.py").read_text(encoding="utf-8")
        assert "executor.connection is not reconciler.connection" in src

    def test_assertion_rejects_a_shared_handle(self, tmp_path):
        c = sqlite3.connect(tmp_path / "a.db")
        with pytest.raises(storage.SharedConnectionError):
            storage.assert_distinct_connections(execution=c, strategy=c)


# ================================================ F-6 keyed risk attribution

class TestF6RiskExitAttributionIsKeyed:
    def _mirror_with_position(self, conn, *, stop_position_id, stop_status):
        coid = bridge.client_order_id(bridge.SIGNAL_ID, ENTRY_DATE, "buy", 1)
        _order(conn, coid, "buy", status="filled", delta_id="D1")
        conn.execute(
            "INSERT INTO order_fills (fill_id, delta_order_id, instrument_id,"
            " side, size, price, fee, role, exch_ts, processed_ts)"
            " VALUES ('f1','D1',84,'buy',1.0,70000.0,0.05,'entry',?,?)",
            (NOW.isoformat(), NOW.isoformat()))
        conn.execute(
            "INSERT INTO positions (id, instrument_id, side, quantity,"
            " entry_price, entry_ts, status, strategy)"
            " VALUES (7,84,'buy',1.0,70000.0,?, 'open', ?)",
            (NOW.isoformat(), bridge.SIGNAL_ID))
        _order(conn, "stop-pX-q1", "sell", status=stop_status,
               strategy="protection")
        storage.write_protective_stop(
            conn, client_order_id="stop-pX-q1", delta_order_id="D9",
            position_id=stop_position_id, side="sell", size=1,
            entry_price=70000.0, trigger_price=69000.0, limit_price=68900.0,
            reduce_only=True, placed_ts=NOW.isoformat())
        conn.commit()
        return {"entry_client_order_id": coid, "claimed_ts": NOW.isoformat()}

    def test_a_filled_stop_on_this_position_is_risk_stop(self, conn):
        m = self._mirror_with_position(conn, stop_position_id=7,
                                       stop_status="filled")
        assert SJ._risk_exit_cause(conn, m) == "risk_stop"

    def test_a_filled_stop_on_ANOTHER_position_is_not_attributed_here(self, conn):
        """The old query counted any protection fill after the claim."""
        m = self._mirror_with_position(conn, stop_position_id=99,
                                       stop_status="filled")
        assert SJ._risk_exit_cause(conn, m) == "external"

    def test_an_unfilled_stop_is_external(self, conn):
        m = self._mirror_with_position(conn, stop_position_id=7,
                                       stop_status="acked")
        assert SJ._risk_exit_cause(conn, m) == "external"

    def test_a_mirror_with_no_entry_order_is_external(self, conn):
        assert SJ._risk_exit_cause(conn, {"entry_client_order_id": None}) == "external"

    def test_attribution_does_not_depend_on_max_open_positions(self):
        import inspect
        src = inspect.getsource(SJ._risk_exit_cause)
        assert "protective_stops" in src and "position_id" in src


# ================================================== F-7 quote freshness bound

class TestF7QuoteFreshness:
    def test_a_fresh_quote_is_accepted(self):
        assert SJ._quote(Client(age_s=1.0), NOW) is not None

    def test_the_observed_production_range_is_accepted(self):
        """min 1.08 / median 3.81 / max 5.99s over 8 calls, 2026-09-08."""
        for age in (1.08, 3.81, 5.99):
            assert SJ._quote(Client(age_s=age), NOW) is not None, age

    def test_a_stale_quote_is_refused(self):
        assert SJ._quote(Client(age_s=30.0), NOW) is None

    def test_the_bound_derives_from_the_documented_cadence_budget(self):
        assert SJ.MARK_CADENCE_BUDGET_S == 5.0
        assert SJ.QUOTE_MAX_AGE_S == 2 * SJ.MARK_CADENCE_BUDGET_S

    def test_a_missing_timestamp_is_refused_not_trusted(self):
        assert SJ._quote(Client(timestamp=None), NOW) is None

    def test_an_unparseable_timestamp_is_refused(self):
        assert SJ._quote(Client(timestamp="not-a-number"), NOW) is None

    def test_the_stale_time_field_is_ignored(self):
        """Production 2026-09-08: `time` was ~24h behind `timestamp`."""
        c = Client(age_s=1.0)
        assert c.get_ticker("BTCUSD")["result"]["time"].startswith("2026-09-07")
        assert SJ._quote(c, NOW) is not None

    def test_a_stale_quote_stops_the_pass_before_any_claim(self, tmp_path, conn):
        fwd = forward_db(tmp_path)
        ex = Executor(record_to=conn)
        r = run_entry(fwd, conn, client=Client(age_s=45.0), executor=ex)
        assert r.outcome == SJ.QUOTE_UNAVAILABLE
        assert ex.submits == []
        assert storage.list_mirrors(conn) == [], "claim must not leak"

    def test_malformed_book_still_refused(self):
        assert SJ._quote(Client(bid=0.0), NOW) is None
        assert SJ._quote(Client(bid=100.0, ask=90.0), NOW) is None
