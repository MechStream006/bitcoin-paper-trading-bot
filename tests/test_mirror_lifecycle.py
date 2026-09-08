"""ADR-0003 (P-MIRROR): the live path mirrors the forward ENTRY EVENT.

The trigger is a new `trade_entries` row inside its 1-hour mirror window, not
an open forward trade; the exit follows the collector's own `trade_exits` row,
not a live timer; and the protective stop remains an independent risk overlay
whose exits are recorded as risk exits, never as strategy exits.

Every fixture states when a forward entry became readable, because under
ADR-0003 that instant -- not the wall-clock day -- is what the live path keys on.
"""
from __future__ import annotations

import pathlib
import sqlite3
import sys
import threading
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data import storage  # noqa: E402
from src.execution.order_state import OrderState  # noqa: E402
from src.risk.manager import AccountSnapshot, Decision  # noqa: E402
from src.strategies import bridge, strategy_jobs as SJ  # noqa: E402

UTC = timezone.utc
READABLE = datetime(2026, 9, 1, 1, 0, 41, tzinfo=UTC)   # a real collector commit
NOW = READABLE + timedelta(minutes=10)                   # inside the window
ENTRY_DATE = "2026-08-31"
EXIT_DATE = "2026-09-07"


# --------------------------------------------------------------------- stubs

class FakeClient:
    def __init__(self, mark=70000.0, bid=69990.0, ask=70010.0, balance=200.0):
        self._t = {"result": {"mark_price": str(mark),
                              "timestamp": NOW.timestamp() * 1_000_000,
                              "quotes": {"best_bid": str(bid), "best_ask": str(ask)}}}
        self._w = {"result": [{"asset_symbol": "USD", "balance": str(balance),
                               "available_balance": str(balance)}]}

    def get_ticker(self, symbol):
        return self._t

    def get_wallet_balances(self):
        return self._w


class FakeExecutor:
    def __init__(self, state=OrderState.ACKED, record_to=None):
        self.submits = []
        self.state = state
        self._record_to = record_to

    def submit(self, intent, account, now=None):
        self.submits.append(intent)
        if self._record_to is not None:
            _insert_order(self._record_to, intent.client_order_id, intent.side)
        return (self.state, Decision.approve(1.0, 0.1))


class FakeRisk:
    def __init__(self, decision=None):
        self.decision = decision or Decision.approve(5.0, 0.5)

    def validate(self, intent, account, now=None):
        return self.decision


class FakeReconciler:
    def __init__(self, permitted=True):
        self.trading_permitted = permitted
        self.halt_reason = "" if permitted else "halted"


def _insert_order(conn, coid, side, status="acked", strategy=None):
    conn.execute(
        "INSERT OR REPLACE INTO orders (client_order_id, instrument_id, side,"
        " order_type, quantity, price, status, strategy, placed_ts)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (coid, 84, side, "limit_order", 1.0, 70000.0, status,
         strategy or bridge.SIGNAL_ID, NOW.isoformat()))
    conn.commit()



def _fill_entry(conn, delta_id="900001"):
    """Give the mirrored entry order a real fill.

    Required because a mirror is only eligible for a risk exit once the entry
    actually opened a position: flatness between the ACK and the fill is not a
    closed trade.
    """
    coid = bridge.client_order_id(bridge.SIGNAL_ID, ENTRY_DATE, "buy", 1)
    conn.execute("UPDATE orders SET delta_order_id = ? WHERE client_order_id = ?",
                 (delta_id, coid))
    conn.execute(
        "INSERT INTO order_fills (fill_id, delta_order_id, instrument_id, side,"
        " size, price, fee, role, exch_ts, processed_ts)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("f1", delta_id, 84, "buy", 1.0, 70000.0, 0.05, "entry",
         NOW.isoformat(), NOW.isoformat()))
    conn.commit()


def _fill_close(conn, delta_id="900002"):
    """A closing fill, so the live book reads flat again."""
    _insert_order(conn, "closer", "sell", status="filled")
    conn.execute("UPDATE orders SET delta_order_id = ? WHERE client_order_id = 'closer'",
                 (delta_id,))
    conn.execute(
        "INSERT INTO order_fills (fill_id, delta_order_id, instrument_id, side,"
        " size, price, fee, role, exch_ts, processed_ts)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("f2", delta_id, 84, "sell", 1.0, 69000.0, 0.05, "exit",
         NOW.isoformat(), NOW.isoformat()))
    conn.commit()


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(tmp_path / "btc_trading.db")
    storage.init_schema(c)
    storage.init_execution_schema(c)
    return c


def forward_db(tmp_path, *, direction=1, readable=READABLE, entry_date=ENTRY_DATE,
               exited=False, name="fwd.db"):
    p = tmp_path / name
    c = sqlite3.connect(p)
    c.executescript((ROOT / "research" / "forward_evidence" /
                     "schema_forward.sql").read_text(encoding="utf-8"))
    c.execute("INSERT INTO run_log (signal_id, run_date, fn_hash, status,"
              " created_at) VALUES (?,?,?, 'ok', ?)",
              (bridge.SIGNAL_ID, entry_date, "h",
               readable.strftime("%Y-%m-%dT%H:%M:%SZ")))
    c.execute("INSERT INTO trade_entries (signal_id, entry_date, direction,"
              " entry_close_price, planned_exit_date, open) VALUES (?,?,?,?,?,?)",
              (bridge.SIGNAL_ID, entry_date, direction, 70000.0, EXIT_DATE,
               0 if exited else 1))
    if exited:
        eid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.execute("INSERT INTO trade_exits (entry_id, exit_date, exit_close_price,"
                  " cost_bps) VALUES (?,?,?,?)", (eid, EXIT_DATE, 71500.0, 15.0))
    c.commit(); c.close()
    return str(p)


def run_entry(fwd, conn, *, now=NOW, executor=None, dry_run=False,
              risk=None, reconciler=None, started=None):
    return SJ.evaluate_once(
        forward_db_path=fwd, conn=conn, client=FakeClient(),
        risk_manager=risk or FakeRisk(), executor=executor or FakeExecutor(),
        reconciler=reconciler or FakeReconciler(), strategy_enabled=True,
        dry_run=dry_run, now=now, service_started_at=started)


def run_exit(fwd, conn, *, now=NOW, executor=None, dry_run=False, reconciler=None):
    return SJ.evaluate_exit_once(
        forward_db_path=fwd, conn=conn, client=FakeClient(),
        risk_manager=FakeRisk(), executor=executor or FakeExecutor(),
        reconciler=reconciler or FakeReconciler(), strategy_enabled=True,
        dry_run=dry_run, now=now)


# ---------------------------------------------------------------- the trigger

class TestEntryTrigger:
    def test_a_forward_entry_event_inside_the_window_is_mirrored(self, tmp_path, conn):
        ex = FakeExecutor(record_to=conn)
        r = run_entry(forward_db(tmp_path), conn, executor=ex)
        assert r.outcome == SJ.SUBMITTED and r.submitted is True
        assert len(ex.submits) == 1

    def test_direction_is_pinned_from_the_forward_entry_row(self, tmp_path, conn):
        ex = FakeExecutor(record_to=conn)
        run_entry(forward_db(tmp_path, direction=-1), conn, executor=ex)
        assert ex.submits[0].side == "sell"
        m = storage.list_mirrors(conn)[0]
        assert m["direction"] == -1

    def test_the_client_order_id_is_keyed_on_the_entry_date_not_the_submit_day(
            self, tmp_path, conn):
        ex = FakeExecutor(record_to=conn)
        run_entry(forward_db(tmp_path), conn, executor=ex)
        expected = bridge.client_order_id(bridge.SIGNAL_ID, ENTRY_DATE, "buy", 1)
        assert ex.submits[0].client_order_id == expected
        assert len(expected) <= bridge.CLIENT_ORDER_ID_MAX_LEN
        assert NOW.strftime("%Y%m%d") not in expected

    def test_an_open_forward_trade_alone_does_not_trigger_an_entry(self, tmp_path, conn):
        """The old trigger. A trade held for days is not an entry EVENT."""
        fwd = forward_db(tmp_path, readable=NOW - timedelta(days=3))
        assert bridge.latest_direction(fwd) == 1, "a trade IS open"
        ex = FakeExecutor(record_to=conn)
        r = run_entry(fwd, conn, executor=ex)
        assert r.outcome == SJ.NO_SIGNAL and ex.submits == []

    def test_nothing_readable_yet_produces_no_entry(self, tmp_path, conn):
        fwd = forward_db(tmp_path, readable=NOW + timedelta(hours=2))
        r = run_entry(fwd, conn, executor=FakeExecutor(record_to=conn))
        assert r.outcome == SJ.NO_SIGNAL


class TestMirrorWindow:
    def test_the_window_is_one_hour(self):
        assert bridge.MIRROR_WINDOW == timedelta(hours=1)

    def test_the_open_boundary_is_inclusive(self, tmp_path, conn):
        fwd = forward_db(tmp_path, readable=NOW)
        assert run_entry(fwd, conn, executor=FakeExecutor(record_to=conn)
                         ).outcome == SJ.SUBMITTED

    def test_one_second_inside_the_close_boundary_still_enters(self, tmp_path, conn):
        fwd = forward_db(tmp_path, readable=NOW - timedelta(minutes=59, seconds=59))
        assert run_entry(fwd, conn, executor=FakeExecutor(record_to=conn)
                         ).outcome == SJ.SUBMITTED

    def test_exactly_one_hour_later_is_already_expired(self, tmp_path, conn):
        fwd = forward_db(tmp_path, readable=NOW - timedelta(hours=1))
        assert run_entry(fwd, conn, executor=FakeExecutor(record_to=conn)
                         ).outcome == SJ.NO_SIGNAL

    def test_the_window_is_never_extended_by_planned_exit_date(self, tmp_path, conn):
        """Explicitly forbidden: a mid-hold entry is not comparable."""
        fwd = forward_db(tmp_path, readable=NOW - timedelta(days=2))
        r = run_entry(fwd, conn, executor=FakeExecutor(record_to=conn))
        assert r.outcome == SJ.NO_SIGNAL
        assert storage.list_mirrors(conn)[0]["state"] == "NOT_MIRRORED"


class TestNotMirroredReasons:
    def test_readable_before_this_process_started_is_cold_start(self, tmp_path, conn):
        fwd = forward_db(tmp_path, readable=NOW - timedelta(hours=5))
        run_entry(fwd, conn, started=NOW - timedelta(hours=1))
        m = storage.list_mirrors(conn)[0]
        assert m["state"] == "NOT_MIRRORED" and m["not_mirrored_reason"] == "cold_start"

    def test_readable_while_running_is_missed(self, tmp_path, conn):
        fwd = forward_db(tmp_path, readable=NOW - timedelta(hours=5))
        run_entry(fwd, conn, started=NOW - timedelta(days=1))
        m = storage.list_mirrors(conn)[0]
        assert m["state"] == "NOT_MIRRORED" and m["not_mirrored_reason"] == "missed"

    def test_a_not_mirrored_entry_is_never_entered_later(self, tmp_path, conn):
        fwd = forward_db(tmp_path, readable=NOW - timedelta(hours=5))
        ex = FakeExecutor(record_to=conn)
        run_entry(fwd, conn, executor=ex, started=NOW - timedelta(days=1))
        assert run_entry(fwd, conn, executor=ex, now=NOW + timedelta(hours=1)
                         ).outcome == SJ.NO_SIGNAL
        assert ex.submits == []

    def test_the_first_classification_stands(self, tmp_path, conn):
        fwd = forward_db(tmp_path, readable=NOW - timedelta(hours=5))
        run_entry(fwd, conn, started=NOW - timedelta(days=1))          # missed
        run_entry(fwd, conn, started=NOW)                              # would be cold_start
        assert storage.list_mirrors(conn)[0]["not_mirrored_reason"] == "missed"


# ------------------------------------------------------- idempotency / racing

class TestIdempotencyAndConcurrency:
    def test_a_second_pass_stands_down(self, tmp_path, conn):
        fwd = forward_db(tmp_path)
        ex = FakeExecutor(record_to=conn)
        assert run_entry(fwd, conn, executor=ex).outcome == SJ.SUBMITTED
        assert run_entry(fwd, conn, executor=ex).outcome == SJ.DUPLICATE
        assert len(ex.submits) == 1

    def test_crossing_utc_midnight_does_not_re_enter(self, tmp_path, conn):
        """The regression the entry-date key exists to prevent."""
        fwd = forward_db(tmp_path)
        ex = FakeExecutor(record_to=conn)
        run_entry(fwd, conn, executor=ex)
        later = run_entry(fwd, conn, executor=ex,
                          now=NOW + timedelta(days=1))          # new UTC day
        assert later.outcome in (SJ.DUPLICATE, SJ.NO_SIGNAL)
        assert len(ex.submits) == 1

    def test_restart_re_derives_the_same_id(self, tmp_path, conn):
        fwd = forward_db(tmp_path)
        a = bridge.client_order_id(bridge.SIGNAL_ID, ENTRY_DATE, "buy", 1)
        ex = FakeExecutor(record_to=conn)
        run_entry(fwd, conn, executor=ex, started=NOW)
        run_entry(fwd, conn, executor=ex, started=NOW + timedelta(minutes=5))
        assert ex.submits[0].client_order_id == a and len(ex.submits) == 1

    def test_two_racing_passes_submit_once(self, tmp_path):
        """Real threads, separate connections, one forward entry."""
        db = tmp_path / "race.db"
        setup = sqlite3.connect(db)
        storage.init_schema(setup); storage.init_execution_schema(setup)
        setup.close()
        fwd = forward_db(tmp_path)
        submits, errors = [], []
        lock = threading.Lock()

        def worker():
            try:
                c = sqlite3.connect(db, timeout=10.0)
                c.row_factory = sqlite3.Row
                ex = FakeExecutor(record_to=c)
                r = run_entry(fwd, c, executor=ex)
                with lock:
                    submits.extend(ex.submits)
                    if r.outcome not in (SJ.SUBMITTED, SJ.DUPLICATE):
                        errors.append(r.outcome)
                c.close()
            except Exception as exc:                      # noqa: BLE001
                with lock:
                    errors.append(f"{type(exc).__name__}: {exc}")

        ts = [threading.Thread(target=worker) for _ in range(5)]
        for t in ts: t.start()
        for t in ts: t.join()
        assert errors == [], errors
        assert len(submits) == 1, f"{len(submits)} submissions raced through"

    def test_a_failed_pass_releases_the_claim_for_retry(self, tmp_path, conn):
        fwd = forward_db(tmp_path)
        rejected = run_entry(fwd, conn, risk=FakeRisk(Decision.reject("no")))
        assert rejected.outcome == SJ.REJECTED
        assert storage.list_mirrors(conn) == [], "claim must not linger"
        ex = FakeExecutor(record_to=conn)
        assert run_entry(fwd, conn, executor=ex).outcome == SJ.SUBMITTED

    def test_a_dry_run_pass_claims_nothing(self, tmp_path, conn):
        fwd = forward_db(tmp_path)
        assert run_entry(fwd, conn, dry_run=True).outcome == SJ.DRY_RUN
        assert storage.list_mirrors(conn) == []


# ------------------------------------------------------------------ the exit

class TestHorizonExit:
    def _entered(self, tmp_path, conn):
        fwd = forward_db(tmp_path)
        run_entry(fwd, conn, executor=FakeExecutor(record_to=conn))
        _fill_entry(conn)
        return fwd

    def test_no_exit_while_the_forward_trade_is_still_held(self, tmp_path, conn):
        fwd = self._entered(tmp_path, conn)
        ex = FakeExecutor()
        assert run_exit(fwd, conn, executor=ex).outcome == SJ.NO_EXIT
        assert ex.submits == []

    def test_the_forward_exit_row_triggers_the_horizon_exit(self, tmp_path, conn):
        self._entered(tmp_path, conn)
        closed = forward_db(tmp_path, exited=True, name="closed.db")
        ex = FakeExecutor(record_to=conn)
        r = run_exit(closed, conn, executor=ex)
        assert r.outcome == SJ.EXIT_SUBMITTED
        assert len(ex.submits) == 1 and ex.submits[0].intent_kind == "exit"
        assert ex.submits[0].side == "sell", "opposite the +1 entry"
        assert storage.list_mirrors(conn)[0]["state"] == "EXIT_SUBMITTED"

    def test_no_live_horizon_timer_exists(self):
        """The exit must key on the collector's row, never on elapsed time."""
        import inspect
        src = inspect.getsource(SJ.evaluate_exit_once)
        assert "forward_exit" in src
        for banned in ("timedelta(days=7)", "HOLD_DAYS", "planned_exit_date <"):
            assert banned not in src, f"{banned} is a live horizon timer"

    def test_dry_run_withholds_the_exit(self, tmp_path, conn):
        self._entered(tmp_path, conn)
        closed = forward_db(tmp_path, exited=True, name="c.db")
        ex = FakeExecutor()
        assert run_exit(closed, conn, executor=ex, dry_run=True).outcome == SJ.DRY_RUN
        assert ex.submits == []

    def test_a_halted_reconciler_defers_the_exit(self, tmp_path, conn):
        self._entered(tmp_path, conn)
        closed = forward_db(tmp_path, exited=True, name="c2.db")
        ex = FakeExecutor()
        r = run_exit(closed, conn, executor=ex, reconciler=FakeReconciler(False))
        assert r.outcome == SJ.NOT_PERMITTED and ex.submits == []

    def test_the_governed_outcome_is_recorded_on_the_mirror(self, tmp_path, conn):
        self._entered(tmp_path, conn)
        closed = forward_db(tmp_path, exited=True, name="c3.db")
        run_exit(closed, conn, executor=FakeExecutor(record_to=conn))
        m = storage.list_mirrors(conn)[0]
        assert m["forward_exit_date"] == EXIT_DATE
        assert m["forward_exit_price"] == 71500.0


class TestRiskOverlayExit:
    def _entered_then_flat(self, tmp_path, conn, protection_filled):
        fwd = forward_db(tmp_path)
        run_entry(fwd, conn, executor=FakeExecutor(record_to=conn))
        _fill_entry(conn)
        _fill_close(conn)
        if protection_filled:
            # F-6: attribution is now joined through protective_stops.position_id
            # rather than "any filled protection order after the claim", so the
            # fixture must build the real chain: entry order -> its fill ->
            # the position that fill opened -> the stop registered against it.
            _insert_order(conn, "stop-p1-q1", "sell", status="filled",
                          strategy="protection")
            conn.execute(
                "INSERT INTO positions (id, instrument_id, side, quantity,"
                " entry_price, entry_ts, status, strategy)"
                " VALUES (1,84,'buy',1.0,70000.0,?,'open',?)",
                (NOW.isoformat(), bridge.SIGNAL_ID))
            storage.write_protective_stop(
                conn, client_order_id="stop-p1-q1", delta_order_id="D9",
                position_id=1, side="sell", size=1, entry_price=70000.0,
                trigger_price=69000.0, limit_price=68900.0, reduce_only=True,
                placed_ts=NOW.isoformat())
            conn.commit()
        return fwd

    def test_a_stopped_position_is_a_risk_exit_not_a_strategy_exit(self, tmp_path, conn):
        fwd = self._entered_then_flat(tmp_path, conn, protection_filled=True)
        r = run_exit(fwd, conn, executor=FakeExecutor())
        assert r.outcome == SJ.RISK_EXITED
        m = storage.list_mirrors(conn)[0]
        assert m["state"] == "CLOSED_BY_RISK" and m["exit_cause"] == "risk_stop"

    def test_an_unexplained_flat_position_is_external(self, tmp_path, conn):
        fwd = self._entered_then_flat(tmp_path, conn, protection_filled=False)
        run_exit(fwd, conn, executor=FakeExecutor())
        assert storage.list_mirrors(conn)[0]["exit_cause"] == "external"

    def test_no_re_entry_after_a_risk_exit_while_the_entry_is_open(self, tmp_path, conn):
        fwd = self._entered_then_flat(tmp_path, conn, protection_filled=True)
        run_exit(fwd, conn, executor=FakeExecutor())
        ex = FakeExecutor(record_to=conn)
        later = run_entry(fwd, conn, executor=ex, now=NOW + timedelta(minutes=20))
        assert later.outcome == SJ.DUPLICATE and ex.submits == []

    def test_a_stopped_trade_still_earns_its_governed_outcome(self, tmp_path, conn):
        """ADR-LE-5: the risk exit ends the live position, never the forward trade."""
        self._entered_then_flat(tmp_path, conn, protection_filled=True)  # noqa
        run_exit(forward_db(tmp_path, name="d.db"), conn, executor=FakeExecutor())
        closed = forward_db(tmp_path, exited=True, name="e.db")
        ex = FakeExecutor()
        run_exit(closed, conn, executor=ex)
        m = storage.list_mirrors(conn)[0]
        assert m["state"] == "CLOSED_BY_RISK", "state is not overwritten"
        assert m["forward_exit_date"] == EXIT_DATE, "governed outcome still recorded"
        assert ex.submits == [], "no order for a position that no longer exists"

    def test_the_stop_parameters_are_untouched(self):
        from src.risk import limits
        assert bridge.STOP_DISTANCE_PCT == 1.4
        assert limits.MAX_TRADE_RISK_USD == 1.00
        assert limits.HARD_RISK_CEILING_USD == 2.00
        assert limits.MAX_CONTRACTS_PER_ORDER == 1
        assert limits.MAX_OPEN_POSITIONS == 1


class TestExistingProtectionsPreserved:
    def test_disabled_strategy_does_nothing(self, tmp_path, conn):
        fwd = forward_db(tmp_path)
        ex = FakeExecutor()
        r = SJ.evaluate_once(forward_db_path=fwd, conn=conn, client=FakeClient(),
                             risk_manager=FakeRisk(), executor=ex,
                             reconciler=FakeReconciler(), strategy_enabled=False,
                             dry_run=False, now=NOW)
        assert r.outcome == SJ.DISABLED and ex.submits == []
        assert storage.list_mirrors(conn) == []

    def test_halted_reconciler_blocks_entry(self, tmp_path, conn):
        ex = FakeExecutor()
        r = run_entry(forward_db(tmp_path), conn, executor=ex,
                      reconciler=FakeReconciler(False))
        assert r.outcome == SJ.NOT_PERMITTED and ex.submits == []

    def test_a_non_acked_submission_writes_no_mirror(self, tmp_path, conn):
        ex = FakeExecutor(state=OrderState.REJECTED)
        r = run_entry(forward_db(tmp_path), conn, executor=ex)
        assert r.outcome == SJ.NOT_ACKED
        assert storage.list_mirrors(conn) == []
