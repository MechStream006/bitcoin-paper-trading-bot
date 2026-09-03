"""Strategy evaluation job -- the signal -> intent -> risk -> (dry-run | submit) wiring.

Nothing here changes strategy logic, sizing, risk rules or reconciliation: the
job delegates to bridge.build_intent(), RiskManager.validate() and
TestnetExecutor.submit(), and these tests fix the wiring around them.
"""
from __future__ import annotations

import ast
import json
import inspect
import hashlib
import pathlib
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data import storage  # noqa: E402
from src.execution.order_state import OrderState  # noqa: E402
from src.risk.manager import AccountSnapshot, Decision, OrderIntent  # noqa: E402
from src.strategies import bridge, strategy_jobs as SJ  # noqa: E402

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
DAY = "2026-09-01"



# --------------------------------------------------------------------- guards

def _code_only(src: str) -> str:
    """Module source with every docstring removed.

    Prose must be correctable without weakening a guard, so the structural pins
    below compare code and ignore documentation.
    """
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and ast.get_docstring(node):
            node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def _content_sha(src: str) -> str:
    """SHA-256 over the LF-normalised source, the project's own convention.

    NOT a hash of `ast.unparse` output. That was tried and is unusable as a
    pin: the same bridge.py unparses to 01d27b3b... on CPython 3.13 and
    a5ad0169... on 3.10, so the guard would fail on whichever interpreter did
    not mint it. Raw content is byte-identical on both. `\r\n` is folded to
    `\n` because this checkout converts line endings (pre_reg_guard._sha256_lf
    and receipts.sha256_lf normalise for exactly the same reason).
    """
    return hashlib.sha256(
        src.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def _module_surface(src: str) -> dict:
    """Top-level names mapped to their declared form: signatures and values."""
    tree = ast.parse(src)
    out: dict = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            out[node.name] = f"def({ast.unparse(node.args)})"
        elif isinstance(node, ast.ClassDef):
            out[node.name] = "class"
        elif (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            out[node.targets[0].id] = ast.unparse(node.value)
    return out


BRIDGE_PY = ROOT / "src" / "strategies" / "bridge.py"

#: Modules this job must never reopen. bridge.py is NOT here: it carries one
#: authorized change and is pinned by content below instead, because a `git
#: diff` guard only ever sees UNSTAGED edits and goes blind the moment a change
#: is committed -- it could not have told an authorized bridge change from any
#: other one.
UNTOUCHED_MODULES = (
    "src/execution/reconciliation_jobs.py",
    "src/execution/reconciler.py",
    "src/risk/manager.py",
    "src/risk/limits.py",
    "src/execution/testnet_executor.py",
    "src/execution/protection.py",
)

#: bridge.py's authorized content identity. Re-pinned deliberately for
#: ADR-0003 (P-MIRROR): the bridge gains the forward-entry reader
#: (`ForwardEntry`, `read_forward_entries`, `mirrorable_entry`,
#: `expired_entries`, `forward_exit`) and the 1-hour `MIRROR_WINDOW` decided by
#: the strategy owner. `latest_direction` is retained unchanged as a diagnostic
#: but is no longer the live entry trigger. Earlier pins covered the
#: availability-bounded capture and the pinned `direction` parameter.
#:
#: This freezes the WHOLE file, prose included. That is the point -- the module
#: it protects sets stop distance, the order-id bound and the ledger query, and
#: a guard that argued about which edits were cosmetic would be a guard with a
#: loophole. Changing bridge.py again means authorizing the change and re-pinning
#: this deliberately. `BRIDGE_OTHER_SURFACE` below is the structural half, and
#: says WHAT changed when the change is structural.
BRIDGE_CONTENT_SHA256 = (
    "309ed52f444e5a61928a8e1ff1c3bbae46cc4e70592c42f8ebdc52d56abc4000")

#: The authorized signature, spelled out so a drifting default or a dropped
#: parameter names itself rather than only moving a hash.
BRIDGE_CAPTURE_SIGNATURE = (
    "def(conn, client_order_id: str, forward_db_path: str, "
    "signal_id: str=SIGNAL_ID, direction: Any=_REREAD_DIRECTION, "
    "decision_ts: datetime | None=None)")

#: Everything else bridge.py declares. Pinned so an added, removed or
#: re-signatured function is reported by name.
BRIDGE_OTHER_SURFACE = {
    "log": "logging.getLogger(__name__)",
    "SIGNAL_ID": "'g3_momentum_tsmom'",
    "_MAX_STOP_SHRINK_TICKS": "8",
    "STOP_DISTANCE_PCT": "1.4",
    "StrategyDisabled": "class",
    "_utc_today": "def()",
    "read_latest_signal": "def(forward_db_path: str, signal_id: str=SIGNAL_ID)",
    "latest_direction": "def(forward_db_path: str, signal_id: str=SIGNAL_ID)",
    "MIRROR_WINDOW": "timedelta(hours=1)",
    "ForwardEntry": "class",
    "read_forward_entries": "def(forward_db_path: str, signal_id: str=SIGNAL_ID)",
    "_parse_utc": "def(value)",
    "mirrorable_entry": "def(entries, now: datetime)",
    "expired_entries": "def(entries, now: datetime, considered_ids)",
    "forward_exit": "def(forward_db_path: str, entry_id: int)",
    "_REREAD_DIRECTION": "object()",
    "_signal_availability_delay": "def()",
    "CLIENT_ORDER_ID_MAX_LEN": "32",
    "_SIGNAL_DIGEST_LEN": "6",
    "_signal_token": "def(signal_id: str, budget: int)",
    "client_order_id": "def(signal_id: str, utc_day: str, side: str, seq: int)",
    "executable_limit_price":
        "def(direction: int, best_bid: float, best_ask: float)",
    "affordable_stop_distance": "_affordable_stop_distance",
    "stop_distance": "def(limit_price: float, mark_price: float, size: int=1)",
    "build_intent": "def(*, direction: int, mark_price: float, enabled: bool, "
                    "best_bid: float, best_ask: float, utc_day: str | None=None, "
                    "seq: int=1)",
}

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
    """Stands in for TestnetExecutor, and returns what it really returns.

    `submit` yields ``(OrderState, Decision)`` and RAISES only for
    InstrumentMissing -- every failure it understands comes back as a state.
    The earlier stub returned the string "state", which no caller could
    distinguish from success, so no test could see a non-ACKED submission.
    """

    def __init__(self, state=OrderState.ACKED, decision=None):
        self.submits = []
        self.state = state
        self.decision = decision or Decision.approve(1.0, 0.1)

    def submit(self, intent, account, now=None):
        self.submits.append((intent, account))
        return (self.state, self.decision)


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


#: The forward entry in the default fixture becomes readable 10 minutes before
#: NOW, i.e. inside `bridge.MIRROR_WINDOW`. ADR-0003 makes the ENTRY EVENT the
#: trigger, so a fixture must state when that event became readable; the
#: collector's own `run_log.created_at` is that instant.
READABLE = "2026-09-01T11:50:00Z"


def _forward_db(tmp_path, direction, name="evidence_forward.db",
                readable_ts=READABLE, entry_date=None, exited=False):
    """A forward corpus carrying one entry EVENT.

    ADR-0003 (P-MIRROR): the live path mirrors the forward entry event, so what
    matters is `run_log.created_at` (when the row became readable) rather than
    whether some trade happens to be open. `exited=True` adds the governed
    `trade_exits` row, which is the authoritative horizon-exit signal.
    """
    day = entry_date or DAY
    p = tmp_path / name
    c = sqlite3.connect(p)
    c.executescript((ROOT / "research" / "forward_evidence" /
                     "schema_forward.sql").read_text(encoding="utf-8"))
    c.execute("INSERT INTO run_log (signal_id, run_date, fn_hash, status,"
              " created_at) VALUES (?,?,?, 'ok', ?)",
              (bridge.SIGNAL_ID, day, "h", readable_ts))
    if direction is not None:
        c.execute("INSERT INTO trade_entries (signal_id, entry_date, direction,"
                  " entry_close_price, planned_exit_date, open)"
                  " VALUES (?,?,?,?,?,?)",
                  (bridge.SIGNAL_ID, day, direction, 70000.0, "2026-09-08",
                   0 if exited else 1))
        if exited:
            eid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            c.execute("INSERT INTO trade_exits (entry_id, exit_date,"
                      " exit_close_price, cost_bps) VALUES (?,?,?,?)",
                      (eid, "2026-09-08", 71000.0, 15.0))
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


class TestOnlyAnAckedSubmissionCounts:
    """`submit()` returns for every failure it understands.

    BLOCKED (its own risk re-validation), REJECTED (HTTP 4xx) and UNKNOWN (a
    timeout, a 5xx, a transport error, an uninterpretable 200) all come back as
    states, not exceptions. Treating "did not raise" as success reported five
    distinct failures as SUBMITTED and wrote a durable context row for each.
    """

    NOT_ACCEPTED = [OrderState.BLOCKED, OrderState.REJECTED, OrderState.UNKNOWN]

    def _context_rows(self, conn):
        return conn.execute("SELECT COUNT(*) FROM trade_signal_context").fetchone()[0]

    def test_acked_is_the_only_accepted_state(self):
        assert SJ.ACCEPTED_STATE is OrderState.ACKED

    def test_an_acked_submission_is_reported_and_captured(self, fwd, conn):
        res = _run(fwd, conn, dry_run=False,
                   executor=FakeExecutor(state=OrderState.ACKED))
        assert res.outcome == SJ.SUBMITTED and res.submitted is True
        assert res.order_state == OrderState.ACKED.value
        assert self._context_rows(conn) == 1

    @pytest.mark.parametrize("state", NOT_ACCEPTED)
    def test_a_non_acked_state_writes_no_context(self, fwd, conn, state):
        res = _run(fwd, conn, dry_run=False, executor=FakeExecutor(state=state))
        assert self._context_rows(conn) == 0, f"context written for {state}"

    @pytest.mark.parametrize("state", NOT_ACCEPTED)
    def test_a_non_acked_state_is_not_relabelled_as_submitted(self, fwd, conn, state):
        res = _run(fwd, conn, dry_run=False, executor=FakeExecutor(state=state))
        assert res.outcome == SJ.NOT_ACKED and res.submitted is False
        assert res.order_state == state.value
        assert state.value in res.detail

    def test_an_exception_still_writes_no_context(self, fwd, conn):
        class Exploding:
            def submit(self, intent, account, now=None):
                raise RuntimeError("delta said no")

        res = _run(fwd, conn, dry_run=False, executor=Exploding())
        assert res.outcome == SJ.ERROR and res.submitted is False
        assert res.order_state is None
        assert self._context_rows(conn) == 0

    def test_an_unreadable_return_fails_closed(self, fwd, conn):
        """A differently shaped return must not be assumed successful."""
        class Vague:
            def submit(self, intent, account, now=None):
                return None

        res = _run(fwd, conn, dry_run=False, executor=Vague())
        assert res.outcome == SJ.NOT_ACKED
        assert res.order_state == OrderState.UNKNOWN.value
        assert self._context_rows(conn) == 0

    def test_a_non_acked_submission_does_not_disturb_the_order_row(self, fwd, conn):
        """The executor owns the orders table; this branch only reports."""
        before = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        _run(fwd, conn, dry_run=False, executor=FakeExecutor(state=OrderState.UNKNOWN))
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == before

    def test_every_non_acked_state_is_covered(self):
        """The parametrisation must not silently miss a failure state."""
        reachable = {OrderState.BLOCKED, OrderState.REJECTED, OrderState.UNKNOWN,
                     OrderState.ACKED}
        assert set(self.NOT_ACCEPTED) | {OrderState.ACKED} == reachable


class TestCapturedDirectionIsTheSubmittedOne:
    """The recorded direction must be the order's, not a later re-read.

    Capture runs after the quote, the wallet read, risk validation and an HTTP
    round-trip. The 01:00 UTC collector can close or open a forward trade
    inside that window, so a re-read can pin a direction the order never
    carried.
    """

    def test_a_ledger_change_during_submit_cannot_alter_the_record(self, tmp_path, conn):
        fwd = _forward_db(tmp_path, -1)

        class FlippingExecutor:
            """Submits, then flips the open forward trade underneath us."""

            def __init__(self):
                self.submits = []

            def submit(self, intent, account, now=None):
                self.submits.append(intent)
                c = sqlite3.connect(fwd)
                c.execute("UPDATE trade_entries SET open = 0")
                c.execute(
                    "INSERT INTO trade_entries (signal_id, entry_date, direction,"
                    " entry_close_price, planned_exit_date, open)"
                    " VALUES (?,?,?,?,?,1)",
                    (bridge.SIGNAL_ID, "2026-09-02", 1, 71000.0, "2026-09-09"))
                c.commit(); c.close()
                return (OrderState.ACKED, Decision.approve(1.0, 0.1))

        ex = FlippingExecutor()
        res = _run(fwd, conn, dry_run=False, executor=ex)
        assert res.outcome == SJ.SUBMITTED
        assert ex.submits[0].side == "sell", "the order that went out was a sell"

        row = conn.execute(
            "SELECT direction FROM trade_signal_context").fetchone()
        assert row[0] == -1, "recorded the order's direction, not the new ledger state"
        assert sqlite3.connect(fwd).execute(
            "SELECT direction FROM trade_entries WHERE open = 1").fetchone()[0] == 1

    def test_the_recorded_direction_matches_the_order_side(self, tmp_path, conn):
        for direction, side in ((-1, "sell"), (1, "buy")):
            c = conn
            c.execute("DELETE FROM trade_signal_context")
            c.execute("DELETE FROM orders")
            c.execute("DELETE FROM mirrored_forward_entry")
            c.commit()
            fwd = _forward_db(tmp_path, direction, name=f"fwd{direction}.db")
            ex = FakeExecutor()
            res = _run(fwd, c, dry_run=False, executor=ex)
            assert res.outcome == SJ.SUBMITTED
            assert ex.submits[0][0].side == side
            assert c.execute(
                "SELECT direction FROM trade_signal_context").fetchone()[0] == direction

    def test_the_helper_still_re_reads_when_no_direction_is_given(self, tmp_path, conn):
        """The original behaviour is preserved for any other caller."""
        fwd = _forward_db(tmp_path, -1)
        assert bridge.capture_signal_context(conn, "coid-legacy", fwd) is True
        assert conn.execute(
            "SELECT direction FROM trade_signal_context WHERE client_order_id=?",
            ("coid-legacy",)).fetchone()[0] == -1

    def test_a_pinned_direction_overrides_the_ledger(self, tmp_path, conn):
        fwd = _forward_db(tmp_path, -1)
        assert bridge.capture_signal_context(
            conn, "coid-pinned", fwd, bridge.SIGNAL_ID, direction=1) is True
        assert conn.execute(
            "SELECT direction FROM trade_signal_context WHERE client_order_id=?",
            ("coid-pinned",)).fetchone()[0] == 1


class TestSignalFieldsAreBoundedByAvailability:

    """signal_day / raw_value / context_json must be what the DECISION could see.

    They are read after submit(), so without a bound they are whatever is
    newest at capture time -- a few seconds later, and able to straddle the
    01:00 UTC collector commit (measured 01:00:06-01:00:42). The bound is the
    same one alignment.timebase declares for signal_log.
    """

    SIG = bridge.SIGNAL_ID
    OTHER = "h5_vol_regime"

    _seq = 0

    def _fwd(self, tmp_path, days, name=None):
        """A forward corpus carrying two signals on each of `days`."""
        TestSignalFieldsAreBoundedByAvailability._seq += 1
        path = tmp_path / (name or f"avail_{self._seq}.db")
        c = sqlite3.connect(path)
        c.executescript((ROOT / "research" / "forward_evidence" /
                         "schema_forward.sql").read_text(encoding="utf-8"))
        for i, day in enumerate(days):
            c.execute("INSERT INTO run_log (signal_id, run_date, fn_hash, status)"
                      " VALUES (?,?,?, 'ok')", (self.SIG, day, "h"))
            rid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            c.execute("INSERT INTO signal_log (signal_id, day, position, raw_value,"
                      " run_id) VALUES (?,?,?,?,?)",
                      (self.SIG, day, 1.0, float(i), rid))
            c.execute("INSERT INTO run_log (signal_id, run_date, fn_hash, status)"
                      " VALUES (?,?,?, 'ok')", (self.OTHER, day, "h"))
            rid2 = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            c.execute("INSERT INTO signal_log (signal_id, day, position, raw_value,"
                      " run_id) VALUES (?,?,?,?,?)",
                      (self.OTHER, day, -1.0, float(i) + 100.0, rid2))
        c.execute("INSERT INTO trade_entries (signal_id, entry_date, direction,"
                  " entry_close_price, planned_exit_date, open) VALUES (?,?,?,?,?,1)",
                  (self.SIG, days[-1], -1, 70000.0, "2099-01-01"))
        c.commit(); c.close()
        return str(path)

    DAYS = ("2026-08-30", "2026-08-31", "2026-09-01")

    def _capture(self, tmp_path, conn, decision_ts, coid="coid-x", **kw):
        fwd = self._fwd(tmp_path, self.DAYS)
        assert bridge.capture_signal_context(
            conn, coid, fwd, self.SIG, decision_ts=decision_ts, **kw) is True
        return dict(zip(
            ("signal_day", "raw_value", "context_json", "direction"),
            conn.execute("SELECT signal_day, raw_value, context_json, direction "
                         "FROM trade_signal_context WHERE client_order_id=?",
                         (coid,)).fetchone()))

    def test_the_delay_is_the_one_the_timebase_declares(self):
        sys.path.insert(0, str(ROOT / "research"))
        from alignment.timebase import get_spec
        assert (bridge._signal_availability_delay().total_seconds()
                == get_spec("signal_log").availability_offset_s == 25 * 3600)

    def test_the_position_six_style_case_takes_the_available_day(self, tmp_path, conn):
        """00:01:20 on 09-02: the 09-01 row is not written until 01:00 on 09-02."""
        got = self._capture(tmp_path, conn,
                            datetime(2026, 9, 2, 0, 1, 20, 167341, tzinfo=timezone.utc))
        assert got["signal_day"] == "2026-08-31"

    def test_exactly_twenty_five_hours_admits_the_day(self, tmp_path, conn):
        got = self._capture(tmp_path, conn,
                            datetime(2026, 9, 2, 1, 0, 0, tzinfo=timezone.utc))
        assert got["signal_day"] == "2026-09-01"

    def test_one_microsecond_short_of_the_boundary_excludes_it(self, tmp_path, conn):
        got = self._capture(tmp_path, conn,
                            datetime(2026, 9, 2, 0, 59, 59, 999999, tzinfo=timezone.utc))
        assert got["signal_day"] == "2026-08-31"

    def test_a_not_yet_available_signal_is_never_recorded(self, tmp_path, conn):
        """Every row in the corpus post-dates this decision."""
        fwd = self._fwd(tmp_path, ("2026-09-05", "2026-09-06"))
        assert bridge.capture_signal_context(
            conn, "coid-none", fwd, self.SIG,
            decision_ts=datetime(2026, 9, 2, 12, tzinfo=timezone.utc)) is True
        row = conn.execute("SELECT signal_day, raw_value, context_json FROM "
                           "trade_signal_context WHERE client_order_id=?",
                           ("coid-none",)).fetchone()
        assert row[0] is None and row[1] is None
        assert json.loads(row[2]) == {}, "context must not reach past the bound"

    def test_all_three_fields_share_one_eligible_vintage(self, tmp_path, conn):
        """signal_day, raw_value and context_json must agree on the day."""
        got = self._capture(tmp_path, conn,
                            datetime(2026, 9, 2, 0, 30, tzinfo=timezone.utc))
        assert got["signal_day"] == "2026-08-31"
        assert got["raw_value"] == 1.0, "the 08-31 row's own raw_value"
        ctx = json.loads(got["context_json"])
        assert set(ctx) == {self.SIG, self.OTHER}
        assert ctx[self.SIG] == 1.0 and ctx[self.OTHER] == -1.0

    def test_context_json_cannot_reach_a_day_the_signal_field_could_not(self, tmp_path, conn):
        """The bound applies to the MAX(day) query too, not only to the signal."""
        got = self._capture(tmp_path, conn,
                            datetime(2026, 9, 2, 0, 30, tzinfo=timezone.utc))
        ctx_day_values = json.loads(got["context_json"])
        fwd = self._fwd(tmp_path, self.DAYS, name="probe.db")
        c = sqlite3.connect(f"{pathlib.Path(fwd).as_uri()}?mode=ro", uri=True)
        newest = c.execute("SELECT MAX(day) FROM signal_log").fetchone()[0]
        c.close()
        assert newest == "2026-09-01", "the corpus does hold a newer day"
        assert ctx_day_values[self.SIG] == 1.0, "but context came from 08-31"

    def test_a_naive_decision_ts_is_read_as_utc(self, tmp_path, conn):
        aware = self._capture(tmp_path, conn,
                              datetime(2026, 9, 2, 0, 30, tzinfo=timezone.utc))
        conn.execute("DELETE FROM trade_signal_context"); conn.commit()
        naive = self._capture(tmp_path, conn, datetime(2026, 9, 2, 0, 30))
        assert aware["signal_day"] == naive["signal_day"] == "2026-08-31"

    def test_direction_stays_pinned_to_the_intent(self, tmp_path, conn):
        """The bound must not disturb the already-pinned direction."""
        got = self._capture(tmp_path, conn,
                            datetime(2026, 9, 2, 0, 30, tzinfo=timezone.utc),
                            direction=1)
        assert got["direction"] == 1, "pinned, not the ledger's -1"

    def test_an_unbounded_caller_keeps_the_original_behaviour(self, tmp_path, conn):
        """No decision instant to offer -> latest at capture time, as before."""
        fwd = self._fwd(tmp_path, self.DAYS)
        assert bridge.capture_signal_context(conn, "coid-legacy", fwd, self.SIG) is True
        row = conn.execute("SELECT signal_day, direction FROM trade_signal_context "
                           "WHERE client_order_id=?", ("coid-legacy",)).fetchone()
        assert row[0] == "2026-09-01", "unbounded: the newest row"
        assert row[1] == -1, "and the ledger's direction, re-read"

    def test_captured_ts_is_still_capture_time_not_the_decision(self, tmp_path, conn):
        before = datetime.now(timezone.utc)
        self._capture(tmp_path, conn,
                      datetime(2026, 9, 2, 0, 30, tzinfo=timezone.utc))
        stamp = conn.execute("SELECT captured_ts FROM trade_signal_context "
                             "WHERE client_order_id=?", ("coid-x",)).fetchone()[0]
        assert datetime.fromisoformat(stamp) >= before

    def test_every_signal_read_shares_one_transaction(self):
        """A collector commit mid-call cannot split the three queries.

        Structural: the reads sit inside an explicit BEGIN, so they take one
        snapshot instead of three.
        """
        src = inspect.getsource(bridge.capture_signal_context)
        begin = src.index('fwd.execute("BEGIN")')
        for needle in ("FROM signal_log WHERE signal_id", "MAX(day) FROM signal_log",
                       "SELECT signal_id, position FROM signal_log"):
            assert src.index(needle) > begin, f"{needle} escapes the snapshot"
        assert 'fwd.execute("COMMIT")' in src

    def test_the_job_passes_its_decision_instant(self):
        """And adds no signal_log read of its own to do it."""
        src = (ROOT / "src" / "strategies" / "strategy_jobs.py").read_text(
            encoding="utf-8")
        assert "decision_ts=now" in src
        assert "signal_log" not in _code_only(src), (
            "the job must not read signal_log; the bound belongs in the capture")



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
        """The registration itself, pinned by structure rather than by a diff.

        The `git diff` below still catches uncommitted drift in the modules
        this job never reopens, but it cannot speak for anything already
        committed -- so what the test claims in its name is asserted directly.
        """
        src = (ROOT / "src" / "execution" / "reconciliation_jobs.py").read_text(
            encoding="utf-8")
        surface = _module_surface(src)
        assert surface["register_reconciliation_jobs"] == (
            "def(scheduler, reconciler, *, "
            "interval_seconds: int=DEFAULT_RECONCILE_INTERVAL_SEC, "
            "job_id: str=RECONCILE_JOB_ID)")
        code = _code_only(src)
        assert "'execution_reconcile'" in code
        assert "trigger='interval'" in code
        assert "scheduler.has_job(job_id)" in code, "idempotency guard removed"

        out = subprocess.run(
            ["git", "diff", "--", *UNTOUCHED_MODULES],
            cwd=str(ROOT), capture_output=True, text=True, timeout=30).stdout
        assert out.strip() == "", f"untouched module changed:\n{out[:500]}"


class TestBridgeIsPinnedToItsAuthorizedChange:
    """bridge.py carries exactly one authorized change and no other.

    It used to sit in the `git diff` list above, which compares the working
    tree against the index: it reported green again the moment a change was
    committed, so it could neither permit the authorized change nor reject an
    unrelated one. Content pinning replaces it.
    """

    def _src(self):
        return BRIDGE_PY.read_text(encoding="utf-8")

    def test_the_authorized_change_is_present_and_exact(self):
        assert (_module_surface(self._src())["capture_signal_context"]
                == BRIDGE_CAPTURE_SIGNATURE)

    def test_the_sentinel_is_not_none(self):
        """None is a real direction -- flat -- so it cannot mean "unset"."""
        assert _module_surface(self._src())["_REREAD_DIRECTION"] == "object()"
        assert bridge._REREAD_DIRECTION is not None

    def test_nothing_else_in_the_module_changed_shape(self):
        surface = _module_surface(self._src())
        surface.pop("capture_signal_context")
        assert surface == BRIDGE_OTHER_SURFACE

    def test_the_module_content_is_pinned(self):
        assert _content_sha(self._src()) == BRIDGE_CONTENT_SHA256, (
            "bridge.py changed beyond its authorized change; authorize the "
            "change and re-pin BRIDGE_CONTENT_SHA256 deliberately")

    def test_the_pin_is_interpreter_independent(self):
        """It must not matter which Python runs the suite.

        An `ast.unparse` pin was tried first and rejected: the same source
        unparses differently on 3.10 and 3.13, so the guard would have failed
        on whichever interpreter did not mint the hash. Content hashing is
        stable, and folding CRLF keeps it stable across checkouts too.
        """
        src = self._src()
        assert _content_sha(src.replace("\n", "\r\n")) == BRIDGE_CONTENT_SHA256

    def test_even_a_prose_edit_moves_the_pin(self):
        """Deliberate: the file is frozen whole, so there is no cosmetic-edit
        loophole. The structural pin is what stays prose-insensitive."""
        edited = self._src().replace(
            "Record the signal state that motivated one live entry, at submit time.",
            "Totally different prose that says nothing useful.")
        assert edited != self._src(), "the docstring anchor moved"
        assert _content_sha(edited) != BRIDGE_CONTENT_SHA256
        surface = _module_surface(edited)
        surface.pop("capture_signal_context")
        assert surface == BRIDGE_OTHER_SURFACE, "structure is prose-insensitive"

    def test_the_docstring_no_longer_denies_having_a_caller(self):
        """It said "NOT called from the execution path"; that stopped being
        true when strategy_jobs became the caller."""
        doc = bridge.capture_signal_context.__doc__
        assert "NOT called from the execution path" not in doc
        assert "strategy_jobs.evaluate_once" in doc
        assert "ACKED" in doc
        assert "direction" in doc

    @pytest.mark.parametrize("old,new,what", [
        ("STOP_DISTANCE_PCT = 1.4", "STOP_DISTANCE_PCT = 2.0", "a risk constant"),
        ("CLIENT_ORDER_ID_MAX_LEN = 32", "CLIENT_ORDER_ID_MAX_LEN = 64",
         "an exchange limit"),
        ("AND open = 1 ", "AND open = 0 ", "the ledger query"),
        ('SIGNAL_ID = "g3_momentum_tsmom"', 'SIGNAL_ID = "something_else"',
         "the signal identity"),
    ])
    def test_an_unrelated_bridge_change_is_rejected(self, old, new, what):
        src = self._src()
        assert old in src, f"anchor for {what} moved"
        assert _content_sha(src.replace(old, new, 1)) != BRIDGE_CONTENT_SHA256, (
            f"changing {what} slipped past the pin")

    def test_a_new_function_is_rejected(self):
        edited = self._src() + "\n\ndef sneak_in():\n    return 1\n"
        assert _content_sha(edited) != BRIDGE_CONTENT_SHA256
        surface = _module_surface(edited)
        surface.pop("capture_signal_context")
        assert surface != BRIDGE_OTHER_SURFACE

    def test_dropping_a_pinned_parameter_is_rejected(self):
        """The regression this whole pin exists to prevent."""
        edited = self._src().replace(
            ",\n                           decision_ts: datetime | None = None", "", 1)
        assert edited != self._src(), "the signature anchor moved"
        assert (_module_surface(edited)["capture_signal_context"]
                != BRIDGE_CAPTURE_SIGNATURE)
