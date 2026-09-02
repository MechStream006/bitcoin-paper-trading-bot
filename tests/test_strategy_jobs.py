"""Strategy evaluation job -- the signal -> intent -> risk -> (dry-run | submit) wiring.

Nothing here changes strategy logic, sizing, risk rules or reconciliation: the
job delegates to bridge.build_intent(), RiskManager.validate() and
TestnetExecutor.submit(), and these tests fix the wiring around them.
"""
from __future__ import annotations

import ast
import pathlib
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data import storage  # noqa: E402
from src.risk.manager import AccountSnapshot, Decision, OrderIntent  # noqa: E402
from src.strategies import bridge, strategy_jobs as SJ  # noqa: E402

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
DAY = "2026-09-01"


# --------------------------------------------------------------------------- stubs

class FakeClient:
    """Only the two read endpoints the job uses. place_order is a tripwire."""

    def __init__(self, mark=70000.0, bid=69990.0, ask=70010.0, balance=200.0):
        self._t = {"result": {"mark_price": str(mark),
                              "quotes": {"best_bid": str(bid), "best_ask": str(ask)}}}
        self._w = {"result": [{"asset_symbol": "USD", "balance": str(balance),
                               "available_balance": str(balance)}]}
        self.place_order_calls = 0

    def get_ticker(self, symbol):
        return self._t

    def get_wallet_balances(self):
        return self._w

    def place_order(self, *a, **k):          # pragma: no cover - must never run
        self.place_order_calls += 1
        raise AssertionError("place_order must not be reached in dry-run")


class FakeExecutor:
    def __init__(self):
        self.submits = []

    def submit(self, intent, account, now=None):
        self.submits.append((intent, account))
        return ("state", Decision.approve(1.0, 0.1))


class FakeRisk:
    def __init__(self, decision=None):
        self.decision = decision or Decision.approve(5.0, 0.5)
        self.calls = []

    def validate(self, intent, account, now=None):
        self.calls.append((intent, account))
        return self.decision


class FakeReconciler:
    def __init__(self, permitted=True, reason=""):
        self.trading_permitted = permitted
        self.halt_reason = reason


class FakeScheduler:
    def __init__(self):
        self.jobs = {}

    def has_job(self, job_id):
        return job_id in self.jobs

    def register(self, name, fn, trigger, job_id=None, **kw):
        self.jobs[job_id or name] = (name, fn, trigger, kw)


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(tmp_path / "btc_trading.db")
    storage.init_schema(c)
    storage.init_execution_schema(c)
    return c


def _forward_db(tmp_path, direction, name="evidence_forward.db"):
    """A forward corpus with (or without) an OPEN trade.

    bridge.latest_direction() reads trade_entries WHERE open = 1 -- the
    collector's currently-open forward trade -- NOT signal_log. The strategy
    therefore mirrors the research position, not the raw daily signal.
    """
    p = tmp_path / name
    c = sqlite3.connect(p)
    c.executescript((ROOT / "research" / "forward_evidence" /
                     "schema_forward.sql").read_text(encoding="utf-8"))
    c.execute("INSERT INTO run_log (signal_id, run_date, fn_hash, status) "
              "VALUES (?,?,?, 'ok')", (bridge.SIGNAL_ID, DAY, "h"))
    if direction is not None:
        c.execute("INSERT INTO trade_entries (signal_id, entry_date, direction,"
                  " entry_close_price, planned_exit_date, open)"
                  " VALUES (?,?,?,?,?,1)",
                  (bridge.SIGNAL_ID, DAY, direction, 70000.0, "2026-09-08"))
    c.commit(); c.close()
    return str(p)


@pytest.fixture
def fwd(tmp_path):
    """Latest open g3_momentum_tsmom trade is SHORT."""
    return _forward_db(tmp_path, -1)


def _insert_order(conn, client_order_id, side):
    """Minimal orders row matching storage's real schema (instrument_id,
    quantity, placed_ts -- not symbol/size)."""
    conn.execute(
        "INSERT INTO orders (client_order_id, instrument_id, side, order_type,"
        " quantity, price, status, strategy, placed_ts)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (client_order_id, 1, side, "limit_order", 1, 70000.0, "open",
         bridge.SIGNAL_ID, NOW.isoformat()))
    conn.commit()


def _run(fwd, conn, *, enabled=True, dry_run=True, client=None, risk=None,
         executor=None, reconciler=None):
    return SJ.evaluate_once(
        forward_db_path=fwd, conn=conn, client=client or FakeClient(),
        risk_manager=risk or FakeRisk(), executor=executor or FakeExecutor(),
        reconciler=reconciler or FakeReconciler(),
        strategy_enabled=enabled, dry_run=dry_run, now=NOW)


# --------------------------------------------------------------------------- gates

class TestGates:
    def test_disabled_strategy_does_no_evaluation(self, fwd, conn):
        risk, ex = FakeRisk(), FakeExecutor()
        r = _run(fwd, conn, enabled=False, risk=risk, executor=ex)
        assert r.outcome == SJ.DISABLED
        assert risk.calls == [] and ex.submits == []
        assert r.intent is None and r.decision is None

    def test_enabled_strategy_evaluates(self, fwd, conn):
        risk = FakeRisk()
        r = _run(fwd, conn, risk=risk)
        assert r.outcome == SJ.DRY_RUN
        assert len(risk.calls) == 1
        assert r.intent is not None and r.decision.approved

    def test_halted_reconciler_blocks_before_any_work(self, fwd, conn):
        risk, ex = FakeRisk(), FakeExecutor()
        r = _run(fwd, conn, risk=risk, executor=ex,
                 reconciler=FakeReconciler(permitted=False, reason="halted: 502"))
        assert r.outcome == SJ.NOT_PERMITTED
        assert "502" in r.detail
        assert risk.calls == [] and ex.submits == []

    def test_no_open_trade_produces_no_intent(self, tmp_path, conn):
        """Flat, for this bridge, means no OPEN forward trade."""
        r = _run(_forward_db(tmp_path, None, "flat.db"), conn)
        assert r.outcome == SJ.NO_SIGNAL and r.intent is None

    def test_long_open_trade_yields_a_buy(self, tmp_path, conn):
        r = _run(_forward_db(tmp_path, 1, "long.db"), conn)
        assert r.outcome == SJ.DRY_RUN and r.intent.side == "buy"

    def test_missing_forward_db_is_no_signal_not_a_crash(self, tmp_path, conn):
        r = _run(str(tmp_path / "absent.db"), conn)
        assert r.outcome in (SJ.NO_SIGNAL, SJ.ERROR)
        assert r.intent is None


# --------------------------------------------------------------------------- dry run

class TestDryRun:
    def test_dry_run_produces_intent_and_decision_but_no_submission(self, fwd, conn):
        client, ex = FakeClient(), FakeExecutor()
        r = _run(fwd, conn, client=client, executor=ex, dry_run=True)
        assert r.outcome == SJ.DRY_RUN
        assert r.intent is not None and r.decision is not None
        assert r.decision.approved is True
        assert r.submitted is False
        assert ex.submits == [], "executor must not be called in dry-run"
        assert client.place_order_calls == 0, "Delta must not be reached"

    def test_dry_run_intent_is_the_bridge_s_own(self, fwd, conn):
        """The job must not invent price, size or id -- all come from bridge."""
        r = _run(fwd, conn)
        expected = bridge.client_order_id(bridge.SIGNAL_ID, DAY, "sell", 1)
        assert r.intent.client_order_id == expected
        assert r.intent.side == "sell"          # signal was -1
        assert r.intent.symbol == "BTCUSD"
        assert r.intent.strategy == bridge.SIGNAL_ID
        assert r.intent.intent_kind == "entry"

    def test_dry_run_writes_no_order_row(self, fwd, conn):
        _run(fwd, conn)
        n = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        assert n == 0

    def test_live_mode_submits(self, fwd, conn):
        ex = FakeExecutor()
        r = _run(fwd, conn, executor=ex, dry_run=False)
        assert r.outcome == SJ.SUBMITTED and r.submitted is True
        assert len(ex.submits) == 1


# --------------------------------------------------------------------------- risk

class TestRisk:
    def test_rejection_is_reported_and_never_submitted(self, fwd, conn):
        ex = FakeExecutor()
        risk = FakeRisk(Decision.reject("daily order cap reached"))
        r = _run(fwd, conn, risk=risk, executor=ex, dry_run=False)
        assert r.outcome == SJ.REJECTED
        assert r.detail == "daily order cap reached"
        assert r.decision.approved is False
        assert ex.submits == [], "a rejected intent must never be submitted"

    def test_job_uses_the_real_risk_manager_verdict(self, fwd, conn):
        """No second opinion: the job returns what validate() said."""
        risk = FakeRisk(Decision.reject("kill switch engaged"))
        assert _run(fwd, conn, risk=risk).decision.reason == "kill switch engaged"

    def test_account_snapshot_carries_ledger_exposure_and_wallet(self, fwd, conn):
        risk = FakeRisk()
        _run(fwd, conn, risk=risk, client=FakeClient(balance=250.0))
        _, account = risk.calls[0]
        assert isinstance(account, AccountSnapshot)
        assert account.equity_usd == 250.0 and account.available_usd == 250.0
        assert account.position_size == 0 and account.open_positions == 0

    def test_unusable_quote_stops_before_risk(self, fwd, conn):
        risk = FakeRisk()
        bad = FakeClient(); bad._t = {"result": {"mark_price": None, "quotes": {}}}
        r = _run(fwd, conn, client=bad, risk=risk)
        assert r.outcome == SJ.QUOTE_UNAVAILABLE
        assert risk.calls == []

    def test_crossed_book_is_refused(self, fwd, conn):
        bad = FakeClient(bid=70020.0, ask=70010.0)   # ask < bid
        assert _run(fwd, conn, client=bad).outcome == SJ.QUOTE_UNAVAILABLE


# --------------------------------------------------------------------------- idempotency

class TestIdempotency:
    def test_existing_order_for_the_day_blocks_a_second_intent(self, fwd, conn):
        coid = bridge.client_order_id(bridge.SIGNAL_ID, DAY, "sell", 1)
        _insert_order(conn, coid, "sell")
        risk, ex = FakeRisk(), FakeExecutor()
        r = _run(fwd, conn, risk=risk, executor=ex, dry_run=False)
        assert r.outcome == SJ.DUPLICATE
        assert risk.calls == [] and ex.submits == []

    def test_two_live_passes_submit_only_once(self, fwd, conn, monkeypatch):
        """The second pass sees the first pass's row and stands down."""
        ex = FakeExecutor()
        real_submit = ex.submit

        def submit_and_record(intent, account, now=None):
            _insert_order(conn, intent.client_order_id, intent.side)
            return real_submit(intent, account, now=now)

        ex.submit = submit_and_record
        first = _run(fwd, conn, executor=ex, dry_run=False)
        second = _run(fwd, conn, executor=ex, dry_run=False)
        assert first.outcome == SJ.SUBMITTED
        assert second.outcome == SJ.DUPLICATE
        assert len(ex.submits) == 1

    def test_client_order_id_is_deterministic_per_day_and_side(self):
        a = bridge.client_order_id(bridge.SIGNAL_ID, DAY, "sell", 1)
        b = bridge.client_order_id(bridge.SIGNAL_ID, DAY, "sell", 1)
        c = bridge.client_order_id(bridge.SIGNAL_ID, "2026-09-02", "sell", 1)
        assert a == b and a != c


# --------------------------------------------------------------------------- registration

class TestSignalContextCapture:
    """The submit path must pin the signal state that motivated the entry.

    `bridge.capture_signal_context` existed with no caller anywhere, which left
    `trade_signal_context` empty and forced research to attribute strategy from
    `positions.strategy` -- a projection that was wrong for four trades.
    """

    def test_a_successful_submission_writes_exactly_one_context_row(self, fwd, conn):
        res = _run(fwd, conn, dry_run=False)
        assert res.outcome == SJ.SUBMITTED and res.submitted is True
        rows = conn.execute("SELECT * FROM trade_signal_context").fetchall()
        assert len(rows) == 1

    def test_the_stored_linkage_is_correct(self, fwd, conn):
        res = _run(fwd, conn, dry_run=False)
        ctx = storage.get_trade_signal_context(conn, res.intent.client_order_id)
        assert ctx is not None
        assert ctx["client_order_id"] == res.intent.client_order_id
        assert ctx["signal_id"] == bridge.SIGNAL_ID
        assert ctx["signal_id"] == res.intent.strategy,             "context must key to the same strategy the order carries"
        assert ctx["direction"] == -1, "the open forward trade is short"
        assert ctx["captured_ts"]

    def test_repeated_execution_does_not_duplicate_context(self, fwd, conn):
        """The executor must persist its order for the duplicate guard to see
        it, exactly as the real one does -- FakeExecutor does not."""
        ex = FakeExecutor()
        real_submit = ex.submit

        def submit_and_record(intent, account, now=None):
            _insert_order(conn, intent.client_order_id, intent.side)
            return real_submit(intent, account, now=now)

        ex.submit = submit_and_record
        first = _run(fwd, conn, executor=ex, dry_run=False)
        second = _run(fwd, conn, executor=ex, dry_run=False)
        assert first.outcome == SJ.SUBMITTED
        assert second.outcome == SJ.DUPLICATE
        assert len(ex.submits) == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM trade_signal_context").fetchone()[0] == 1

    def test_capture_is_idempotent_if_called_twice_for_one_order(self, fwd, conn):
        """write_trade_signal_context upserts on client_order_id."""
        res = _run(fwd, conn, dry_run=False)
        bridge.capture_signal_context(conn, res.intent.client_order_id, fwd,
                                      bridge.SIGNAL_ID)
        assert conn.execute(
            "SELECT COUNT(*) FROM trade_signal_context").fetchone()[0] == 1

    def test_a_capture_failure_does_not_fail_the_order(self, fwd, conn, monkeypatch):
        """A research record must never turn a filled order into a failure."""
        def boom(*a, **k):
            raise RuntimeError("context store exploded")

        monkeypatch.setattr(bridge, "capture_signal_context", boom)
        ex = FakeExecutor()
        res = _run(fwd, conn, dry_run=False, executor=ex)
        assert res.outcome == SJ.SUBMITTED and res.submitted is True
        assert len(ex.submits) == 1, "the order still went out"
        assert conn.execute(
            "SELECT COUNT(*) FROM trade_signal_context").fetchone()[0] == 0

    def test_a_missing_forward_db_does_not_fail_the_order(self, tmp_path, conn):
        """capture_signal_context's own best-effort path, exercised live."""
        fwd = _forward_db(tmp_path, -1)
        res = _run(fwd, conn, dry_run=False)
        assert res.outcome == SJ.SUBMITTED
        pathlib.Path(fwd).unlink()
        conn.execute("DELETE FROM orders")
        conn.execute("DELETE FROM trade_signal_context")
        conn.commit()
        assert _run(fwd, conn, dry_run=False).outcome == SJ.NO_SIGNAL

    def test_no_context_when_the_pass_does_not_submit(self, fwd, conn):
        for kwargs in ({"dry_run": True},
                       {"enabled": False, "dry_run": False}):
            conn.execute("DELETE FROM trade_signal_context")
            conn.commit()
            res = _run(fwd, conn, **kwargs)
            assert res.submitted is False
            assert conn.execute(
                "SELECT COUNT(*) FROM trade_signal_context").fetchone()[0] == 0,                 f"context written without a submission ({kwargs})"

    def test_no_context_when_risk_rejects(self, fwd, conn):
        res = _run(fwd, conn, dry_run=False, risk=FakeRisk(Decision.reject("blocked")))
        assert res.outcome == SJ.REJECTED and res.submitted is False
        assert conn.execute(
            "SELECT COUNT(*) FROM trade_signal_context").fetchone()[0] == 0

    def test_no_context_when_the_submission_itself_fails(self, fwd, conn):
        """A rejected or erroring submit never reaches the capture call."""
        class Exploding:
            def submit(self, intent, account, now=None):
                raise RuntimeError("delta said no")

        res = _run(fwd, conn, dry_run=False, executor=Exploding())
        assert res.outcome == SJ.ERROR and res.submitted is False
        assert conn.execute(
            "SELECT COUNT(*) FROM trade_signal_context").fetchone()[0] == 0

    def test_capture_happens_only_after_a_successful_submit(self):
        """Structural: the call must sit after submit(), not before it."""
        src = (ROOT / "src" / "strategies" / "strategy_jobs.py").read_text(
            encoding="utf-8")
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "evaluate_once")
        submit_line = capture_line = None
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                label = getattr(node.func, "attr", None)
                if label == "submit":
                    submit_line = node.lineno
                if label == "capture_signal_context":
                    capture_line = node.lineno
        assert submit_line and capture_line
        assert capture_line > submit_line


class TestRegistration:
    def _register(self, sched, enabled, fwd, conn, dry_run=True):
        return SJ.register_strategy_job(
            sched, forward_db_path=fwd, conn=conn, client=FakeClient(),
            risk_manager=FakeRisk(), executor=FakeExecutor(),
            reconciler=FakeReconciler(), strategy_enabled=enabled, dry_run=dry_run)

    def test_disabled_registers_no_job_at_all(self, fwd, conn):
        s = FakeScheduler()
        assert self._register(s, False, fwd, conn) is False
        assert s.jobs == {}

    def test_enabled_registers_one_job(self, fwd, conn):
        s = FakeScheduler()
        assert self._register(s, True, fwd, conn) is True
        assert SJ.STRATEGY_JOB_ID in s.jobs
        name, fn, trigger, kw = s.jobs[SJ.STRATEGY_JOB_ID]
        assert trigger == "interval"
        assert kw["seconds"] == SJ.DEFAULT_STRATEGY_INTERVAL_SEC

    def test_registration_is_idempotent(self, fwd, conn):
        s = FakeScheduler()
        assert self._register(s, True, fwd, conn) is True
        assert self._register(s, True, fwd, conn) is False
        assert len(s.jobs) == 1

    def test_registered_job_runs_and_never_raises(self, fwd, conn):
        s = FakeScheduler()
        self._register(s, True, fwd, conn)
        _, fn, _, _ = s.jobs[SJ.STRATEGY_JOB_ID]
        fn()                      # must not raise
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0

    def test_job_swallows_an_exploding_dependency(self, fwd, conn):
        class Boom:
            def validate(self, *a, **k):
                raise RuntimeError("boom")
        s = FakeScheduler()
        SJ.register_strategy_job(
            s, forward_db_path=fwd, conn=conn, client=FakeClient(),
            risk_manager=Boom(), executor=FakeExecutor(),
            reconciler=FakeReconciler(), strategy_enabled=True, dry_run=True)
        _, fn, _, _ = s.jobs[SJ.STRATEGY_JOB_ID]
        fn()                      # a scheduler must not see this


# --------------------------------------------------------------------------- untouched

class TestExistingBehaviourUntouched:
    def test_job_never_clears_a_halt_or_places_protection(self):
        src = (ROOT / "src" / "strategies" / "strategy_jobs.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        called = {n.func.attr for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        for banned in ("clear_halt_if_clean", "halt", "reconcile", "place_order",
                       "cancel", "close_position", "set_executor"):
            assert banned not in called, f"strategy job must not call {banned}"

    def test_job_does_not_reimplement_sizing_or_pricing(self):
        """Sizing lives in limits.MAX_CONTRACTS_PER_ORDER via build_intent;
        pricing in bridge.executable_limit_price. Neither is duplicated."""
        src = (ROOT / "src" / "strategies" / "strategy_jobs.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        for n in ast.walk(tree):
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and                     ast.get_docstring(n):
                n.body = n.body[1:] or [ast.Pass()]
        code = ast.unparse(tree)
        for banned in ("MAX_CONTRACTS_PER_ORDER", "executable_limit_price",
                       "stop_distance", "BTCUSD_TICK_SIZE"):
            assert banned not in code, f"{banned} must not be reimplemented here"

    def test_kill_switch_semantics_are_the_risk_manager_s(self):
        """The job holds no kill-switch logic of its own."""
        src = (ROOT / "src" / "strategies" / "strategy_jobs.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        for n in ast.walk(tree):
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and                     ast.get_docstring(n):
                n.body = n.body[1:] or [ast.Pass()]
        assert "kill_switch" not in ast.unparse(tree)

    def test_reconciliation_job_registration_is_unchanged(self):
        import subprocess
        out = subprocess.run(
            ["git", "diff", "--", "src/execution/reconciliation_jobs.py",
             "src/execution/reconciler.py", "src/risk/manager.py",
             "src/risk/limits.py", "src/strategies/bridge.py",
             "src/execution/testnet_executor.py", "src/execution/protection.py"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=30).stdout
        assert out.strip() == "", f"untouched module changed:\n{out[:500]}"
