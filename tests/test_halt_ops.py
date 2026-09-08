"""Product-scoped reconciliation, and the operator halt-clear command.

Two findings from the 2026-09-08 audit:

  * `/v2/positions/margined` takes no product filter, so its response carries
    every product the account holds, and the reconciler summed it whole against
    BTCUSD-only local exposure.
  * a persisted halt had no operator surface to retire it.

The payload shapes here are the ones production actually returns, captured
2026-09-08: a top-level `product_id` int plus `product_symbol`, with
`product.id` repeating the id.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import clear_halt as ch  # noqa: E402
from src.data import storage  # noqa: E402
from src.execution.reconciler import Reconciler, exchange_signed_position  # noqa: E402

BTCUSD = 84
ETHUSD = 1699


def _pos(product_id, size, symbol=None):
    return {"product_id": product_id, "size": size,
            "product_symbol": symbol or ("BTCUSD" if product_id == BTCUSD
                                         else "OTHER"),
            "product": {"id": product_id}}


@pytest.fixture
def conn(tmp_path):
    c = storage.get_connection(tmp_path / "t.db")
    storage.init_schema(c)
    storage.init_execution_schema(c)
    c.execute(
        "INSERT OR REPLACE INTO instruments "
        "(id, symbol, contract_type, underlying, quoting, tick_size, "
        "contract_value, state, fetched_at) "
        "VALUES (84,'BTCUSD','perpetual_futures','BTC','USD',0.1,0.001,'live',"
        "'2026-08-18T00:00:00+00:00')")
    c.commit()
    return c


def _local_long_one(conn):
    """One filled buy, so fills-derived local exposure is +1."""
    conn.execute(
        "INSERT INTO order_fills (fill_id, delta_order_id, instrument_id, side, "
        "size, price, role, processed_ts) VALUES ('f1', '1', 84, 'buy', 1, "
        "77216.0, 'entry', '2026-09-03T00:13:01+00:00')")
    conn.commit()


class _Client:
    """Reconciliation-shaped fake. Records every call, so a test can prove
    that no write endpoint was reached."""

    def __init__(self, positions=None, raises=False, success=True):
        self._positions = positions if positions is not None else []
        self._raises = raises
        self._success = success
        self.calls: list[str] = []

    def get_live_orders(self, product_id=None):
        self.calls.append("get_live_orders")
        return {"success": True, "result": []}

    def get_fills(self, product_id=None):
        self.calls.append("get_fills")
        return {"success": True, "result": []}

    def get_positions(self):
        self.calls.append("get_positions")
        if self._raises:
            raise ConnectionError("exchange unreachable")
        if not self._success:
            return {"success": False, "error": {"code": "rate_limited"}}
        return {"success": True, "result": self._positions}

    def place_order(self, **kw):
        raise AssertionError("halt tooling must never place an order")

    def cancel_order(self, *a, **kw):
        raise AssertionError("halt tooling must never cancel an order")


# ============================================ 1. product-scoped reconciliation

class TestExchangeSignedPositionIsProductScoped:

    def test_matching_btcusd_is_clean(self):
        assert exchange_signed_position([_pos(BTCUSD, 1)], BTCUSD) == 1

    def test_an_unrelated_product_does_not_count(self):
        """The latent bug: a foreign position read as BTCUSD divergence."""
        rows = [_pos(BTCUSD, 1), _pos(ETHUSD, 1)]
        assert exchange_signed_position(rows, BTCUSD) == 1

    def test_two_opposite_foreign_positions_cannot_mask_a_real_one(self):
        """The subtler half: an aggregate sum can also cancel out.

        Foreign +1 and -1 summed to zero, so a genuine BTCUSD divergence would
        have looked reconciled -- a false CLEAN, which is worse than a false
        halt.
        """
        rows = [_pos(BTCUSD, 2), _pos(ETHUSD, 1), _pos(ETHUSD, -1)]
        assert exchange_signed_position(rows, BTCUSD) == 2

    def test_direction_is_preserved(self):
        assert exchange_signed_position([_pos(BTCUSD, -3)], BTCUSD) == -3

    def test_multiple_rows_for_the_configured_product_are_summed(self):
        rows = [_pos(BTCUSD, 1), _pos(BTCUSD, 2)]
        assert exchange_signed_position(rows, BTCUSD) == 3

    def test_a_row_with_no_identifier_is_counted_not_dropped(self):
        """Unattributable exposure must not vanish.

        Silently dropping it is the same omission bug pointing the other way,
        and unexplained exposure is exactly what this comparison exists to
        catch.
        """
        assert exchange_signed_position([{"size": 1}], BTCUSD) == 1

    def test_nested_product_id_is_honoured_when_top_level_is_absent(self):
        rows = [{"size": 5, "product": {"id": ETHUSD}}]
        assert exchange_signed_position(rows, BTCUSD) == 0

    def test_empty_and_missing_sizes(self):
        assert exchange_signed_position([], BTCUSD) == 0
        assert exchange_signed_position([_pos(BTCUSD, None)], BTCUSD) == 0


class TestReconciliationUsesTheProductScope:
    """End to end through reconcile(), which is where the bug actually bit."""

    def _rec(self, conn, positions):
        return Reconciler(conn, _Client(positions), product_id=BTCUSD,
                          enforce_protection=False, ingest_fills=False)

    def test_a_btcusd_plus_one_against_local_plus_one_is_clean(self, conn):
        _local_long_one(conn)
        r = self._rec(conn, [_pos(BTCUSD, 1)])
        assert r.reconcile().clean
        assert not r.halted

    def test_an_unrelated_product_does_not_halt_btcusd(self, conn):
        """Case b: this halted the stack before the fix."""
        _local_long_one(conn)
        r = self._rec(conn, [_pos(BTCUSD, 1), _pos(ETHUSD, 1)])
        assert r.reconcile().clean
        assert not r.halted

    def test_a_real_btcusd_divergence_still_halts(self, conn):
        """Case c: the fix must not buy quiet by weakening the check."""
        _local_long_one(conn)
        r = self._rec(conn, [_pos(BTCUSD, 2)])
        result = r.reconcile()
        assert not result.clean
        assert r.halted
        assert "exchange=+2 local=+1" in r.halt_reason

    def test_a_real_divergence_still_halts_beside_foreign_positions(self, conn):
        _local_long_one(conn)
        r = self._rec(conn, [_pos(BTCUSD, 2), _pos(ETHUSD, 7)])
        assert not r.reconcile().clean
        assert "exchange=+2 local=+1" in r.halt_reason


# ================================================ 2. operator halt-clear command

class TestOperatorHaltClear:

    def _halt_file(self, tmp_path, halted=True,
                   reason="position mismatch: exchange=+2 local=+1"):
        p = tmp_path / ".reconciler-halt.json"
        p.write_text(json.dumps({"halted": halted, "halt_reason": reason,
                                 "ts": "2026-09-05T07:51:39+00:00"}),
                     encoding="utf-8")
        return p

    def _run(self, halt_path, conn, client, out=None):
        lines: list[str] = []
        rc = ch.clear_halt(halt_path, conn, client, BTCUSD,
                           out=lambda s: lines.append(str(s)))
        return rc, "\n".join(lines)

    def test_matching_exposure_allows_the_clear(self, conn, tmp_path):
        _local_long_one(conn)
        p = self._halt_file(tmp_path)
        rc, out = self._run(p, conn, _Client([_pos(BTCUSD, 1)]))
        assert rc == ch.CLEARED
        assert "CLEARED" in out
        assert json.loads(p.read_text(encoding="utf-8"))["halted"] is False

    def test_the_previous_reason_is_reported_and_recorded(self, conn, tmp_path):
        _local_long_one(conn)
        p = self._halt_file(tmp_path)
        rc, out = self._run(p, conn, _Client([_pos(BTCUSD, 1)]))
        assert "position mismatch: exchange=+2 local=+1" in out
        payload = json.loads(p.read_text(encoding="utf-8"))
        assert payload["cleared_previous_reason"] == (
            "position mismatch: exchange=+2 local=+1")

    def test_mismatch_refuses_and_leaves_the_halt_intact(self, conn, tmp_path):
        _local_long_one(conn)
        p = self._halt_file(tmp_path)
        before = p.read_text(encoding="utf-8")
        rc, out = self._run(p, conn, _Client([_pos(BTCUSD, 2)]))
        assert rc == ch.REFUSED
        assert "REFUSED" in out
        assert p.read_text(encoding="utf-8") == before

    def test_an_unrelated_product_cannot_be_used_to_fake_agreement(self, conn,
                                                                  tmp_path):
        """Local +1, BTCUSD 0, a foreign +1. An unscoped sum would agree."""
        _local_long_one(conn)
        p = self._halt_file(tmp_path)
        rc, _ = self._run(p, conn, _Client([_pos(ETHUSD, 1)]))
        assert rc == ch.REFUSED
        assert json.loads(p.read_text(encoding="utf-8"))["halted"] is True

    def test_exchange_read_failure_refuses(self, conn, tmp_path):
        _local_long_one(conn)
        p = self._halt_file(tmp_path)
        rc, out = self._run(p, conn, _Client(raises=True))
        assert rc == ch.REFUSED
        assert "cannot read" in out or "raised" in out
        assert json.loads(p.read_text(encoding="utf-8"))["halted"] is True

    def test_unsuccessful_exchange_response_refuses(self, conn, tmp_path):
        _local_long_one(conn)
        p = self._halt_file(tmp_path)
        rc, _ = self._run(p, conn, _Client(success=False))
        assert rc == ch.REFUSED
        assert json.loads(p.read_text(encoding="utf-8"))["halted"] is True

    def test_malformed_positions_payload_refuses(self, conn, tmp_path):
        _local_long_one(conn)
        p = self._halt_file(tmp_path)

        class Weird(_Client):
            def get_positions(self):
                return {"success": True, "result": "not-a-list"}

        rc, _ = self._run(p, conn, Weird())
        assert rc == ch.REFUSED
        assert json.loads(p.read_text(encoding="utf-8"))["halted"] is True

    def test_corrupt_halt_state_refuses_and_is_not_overwritten(self, conn,
                                                              tmp_path):
        p = tmp_path / ".reconciler-halt.json"
        p.write_text("{not json", encoding="utf-8")
        rc, out = self._run(p, conn, _Client([_pos(BTCUSD, 1)]))
        assert rc == ch.REFUSED
        assert "unreadable" in out
        assert p.read_text(encoding="utf-8") == "{not json"

    def test_repeated_clear_is_harmless(self, conn, tmp_path):
        _local_long_one(conn)
        p = self._halt_file(tmp_path)
        assert self._run(p, conn, _Client([_pos(BTCUSD, 1)]))[0] == ch.CLEARED
        first = p.read_text(encoding="utf-8")
        rc, out = self._run(p, conn, _Client([_pos(BTCUSD, 1)]))
        assert rc == ch.CLEARED
        assert "Nothing to clear" in out
        assert json.loads(p.read_text(encoding="utf-8"))["halted"] is False
        assert json.loads(first)["cleared_previous_reason"] == (
            json.loads(p.read_text(encoding="utf-8"))["cleared_previous_reason"])

    def test_absent_state_is_nothing_to_clear(self, conn, tmp_path):
        rc, out = self._run(tmp_path / "nope.json", conn,
                            _Client([_pos(BTCUSD, 1)]))
        assert rc == ch.CLEARED
        assert "Nothing to clear" in out

    def test_a_refused_clear_reads_the_exchange_but_writes_nothing(self, conn,
                                                                  tmp_path):
        _local_long_one(conn)
        p = self._halt_file(tmp_path)
        client = _Client([_pos(BTCUSD, 2)])
        self._run(p, conn, client)
        assert client.calls == ["get_positions"]

    def test_a_cleared_halt_no_longer_restores_into_a_new_reconciler(
            self, conn, tmp_path):
        """The clear must actually retire the latch for the next process."""
        _local_long_one(conn)
        p = self._halt_file(tmp_path)
        assert Reconciler(conn, _Client(), product_id=BTCUSD,
                          halt_state_path=p).halted
        self._run(p, conn, _Client([_pos(BTCUSD, 1)]))
        assert not Reconciler(conn, _Client(), product_id=BTCUSD,
                              halt_state_path=p).halted

    def test_a_refused_clear_still_restores_the_halt(self, conn, tmp_path):
        _local_long_one(conn)
        p = self._halt_file(tmp_path)
        self._run(p, conn, _Client([_pos(BTCUSD, 2)]))
        r = Reconciler(conn, _Client(), product_id=BTCUSD, halt_state_path=p)
        assert r.halted
        assert r.halt_reason == "position mismatch: exchange=+2 local=+1"


class TestClearIsNotWiredIntoTheRuntime:
    """It must stay operator-initiated: nothing may call it automatically."""

    def test_no_runtime_module_imports_the_clear_script(self):
        """`clear_halt_if_clean` on the Reconciler is a different thing and is
        allowed to exist; what must not exist is the runtime reaching for this
        SCRIPT, which would make clearing automatic again."""
        for path in (ROOT / "src").rglob("*.py"):
            src = path.read_text(encoding="utf-8")
            assert "scripts.clear_halt" not in src, path
            assert "import clear_halt" not in src, path

    def test_the_script_offers_no_force_flag(self):
        import ast
        tree = ast.parse((ROOT / "scripts" / "clear_halt.py").read_text(
            encoding="utf-8"))
        added = [n.args[0].value for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "add_argument"
                 and n.args and isinstance(n.args[0], ast.Constant)]
        assert added == ["--db", "--halt-state", "--product-id"], added
        assert not any("force" in a for a in added)

    def test_clear_halt_takes_no_override_parameter(self):
        import inspect
        params = list(inspect.signature(ch.clear_halt).parameters)
        assert params == ["halt_path", "conn", "client", "product_id", "out"]
