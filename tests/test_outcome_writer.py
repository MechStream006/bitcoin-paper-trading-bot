"""Tests for the execution-outcome sweep (research/label_store/outcome_writer.py).

Synthetic trading databases with explicit instants. The sweep is exercised
end to end: feature-store index -> build_labels -> final-outcome gate ->
append-only persist.
"""
from __future__ import annotations

import ast
import pathlib
import sqlite3
import sys
from datetime import datetime, timezone

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_RESEARCH = _ROOT / "research"
if str(_RESEARCH) not in sys.path:
    sys.path.insert(0, str(_RESEARCH))

from feature_store import decision_store as DS  # noqa: E402
from label_store import outcome_store as OS  # noqa: E402
from label_store import outcome_writer as OW  # noqa: E402
from alignment.timebase import to_utc  # noqa: E402

UTC = timezone.utc
D0 = "2026-09-01T10:00:00.000000+00:00"
WRITTEN_AT = "2026-09-02T12:00:00+00:00"

_TRADING_SCHEMA = """
CREATE TABLE instruments (
    id INTEGER PRIMARY KEY, symbol TEXT, contract_type TEXT, underlying TEXT,
    quoting TEXT, strike_price REAL, settlement_time TEXT, tick_size REAL,
    contract_value REAL, state TEXT, raw_json TEXT, fetched_at TEXT);
CREATE TABLE orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT, client_order_id TEXT,
    delta_order_id TEXT, instrument_id INTEGER NOT NULL, side TEXT NOT NULL,
    order_type TEXT NOT NULL, quantity REAL NOT NULL, price REAL,
    status TEXT NOT NULL, strategy TEXT, placed_ts TEXT NOT NULL, filled_ts TEXT);
CREATE TABLE order_fills (
    fill_id TEXT PRIMARY KEY, client_order_id TEXT, delta_order_id TEXT,
    instrument_id INTEGER NOT NULL, side TEXT NOT NULL, size REAL NOT NULL,
    price REAL NOT NULL, fee REAL NOT NULL DEFAULT 0.0, role TEXT NOT NULL,
    realised_pnl REAL, exch_ts TEXT, processed_ts TEXT NOT NULL);
CREATE TABLE order_rejections (
    client_order_id TEXT PRIMARY KEY, ts TEXT NOT NULL, http_status INTEGER,
    error_code TEXT, body TEXT);
CREATE TABLE positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, instrument_id INTEGER NOT NULL,
    side TEXT NOT NULL, quantity REAL NOT NULL, entry_price REAL NOT NULL,
    entry_ts TEXT NOT NULL, exit_price REAL, exit_ts TEXT, pnl REAL,
    status TEXT NOT NULL, strategy TEXT);
CREATE TABLE protective_stops (
    client_order_id TEXT PRIMARY KEY, delta_order_id TEXT, position_id INTEGER,
    side TEXT NOT NULL, size INTEGER NOT NULL, entry_price REAL NOT NULL,
    trigger_price REAL NOT NULL, limit_price REAL NOT NULL,
    reduce_only INTEGER NOT NULL DEFAULT 1, placed_ts TEXT NOT NULL);
CREATE TABLE spot_series (ts TEXT PRIMARY KEY, spot_price REAL NOT NULL);
CREATE TABLE trade_signal_context (
    client_order_id TEXT PRIMARY KEY, signal_id TEXT NOT NULL, direction INTEGER,
    raw_value REAL, signal_day TEXT, context_json TEXT, captured_ts TEXT NOT NULL);
"""

E_PROC = "2026-09-01T10:01:00.000000+00:00"
E_EXCH = "2026-09-01T10:00:05.000000Z"
X_PROC = "2026-09-01T11:01:00.000000+00:00"
X_EXCH = "2026-09-01T11:00:05.000000Z"


@pytest.fixture()
def trading(tmp_path):
    p = tmp_path / "btc_trading.db"
    c = sqlite3.connect(p)
    c.executescript(_TRADING_SCHEMA)
    c.execute("INSERT INTO instruments (id,symbol,contract_value,tick_size) "
              "VALUES (84,'BTCUSD',0.001,0.1)")
    c.commit()
    return p, c


@pytest.fixture()
def features(tmp_path):
    p = tmp_path / "decision_features.db"
    DS.init_db(p)
    c = sqlite3.connect(p)
    c.execute("INSERT INTO feature_contracts VALUES "
              "('f',1,'h','{}',?)", (WRITTEN_AT,))
    c.commit()
    c.close()
    return p


@pytest.fixture()
def outcomes(tmp_path):
    return tmp_path / "decision_outcomes.db"


def capture(features_db, decision_id, decision_ts=D0):
    """Record one captured decision in the feature store."""
    c = sqlite3.connect(features_db)
    c.execute(
        "INSERT INTO feature_rows (decision_id,decision_ts,feature_name,"
        "contract_version,contract_hash,value_json,state,action,row_state,"
        "row_action,schema_version,written_at) VALUES "
        "(?,?,'f',1,'h',NULL,'READY','proceed','READY','proceed',1,?)",
        (decision_id, decision_ts, WRITTEN_AT))
    c.commit()
    c.close()


def add_completed(c, coid, *, entry=100000.0, exit_px=99000.0, side="buy",
                  entry_proc=E_PROC, exit_proc=X_PROC, delta_e="1001",
                  delta_x="1002"):
    exit_side = "sell" if side == "buy" else "buy"
    direction = 1 if side == "buy" else -1
    pnl = (exit_px - entry) * direction * 0.001 - 0.1
    c.execute("INSERT INTO orders (client_order_id,delta_order_id,instrument_id,"
              "side,order_type,quantity,price,status,strategy,placed_ts) VALUES "
              "(?,?,84,?,'limit_order',1,?, 'filled','g3',?)",
              (coid, delta_e, side, entry + 1, D0))
    c.execute("INSERT INTO order_fills VALUES (?,NULL,?,84,?,1,?,0.05,'entry',"
              "NULL,?,?)", (f"f-{delta_e}", delta_e, side, entry, E_EXCH, entry_proc))
    c.execute("INSERT INTO order_fills VALUES (?,NULL,?,84,?,1,?,0.05,'exit',?,?,?)",
              (f"f-{delta_x}", delta_x, exit_side, exit_px, pnl, X_EXCH, exit_proc))
    c.execute("INSERT INTO positions (instrument_id,side,quantity,entry_price,"
              "entry_ts,exit_price,exit_ts,pnl,status,strategy) VALUES "
              "(84,?,1,?,?,?,?,?,'closed','g3')",
              (side, entry, entry_proc, exit_px, exit_proc, pnl))
    c.commit()
    return pnl


def add_terminal_no_fill(c, coid, *, status="rejected", delta_id=None):
    c.execute("INSERT INTO orders (client_order_id,delta_order_id,instrument_id,"
              "side,order_type,quantity,price,status,strategy,placed_ts) VALUES "
              "(?,?,84,'buy','limit_order',1,100.0,?,'g3',?)",
              (coid, delta_id, status, D0))
    if status == "rejected":
        c.execute("INSERT INTO order_rejections VALUES (?,?,400,'bad_schema','{}')",
                  (coid, D0))
    c.commit()


def add_resting(c, coid, *, status="acked"):
    c.execute("INSERT INTO orders (client_order_id,delta_order_id,instrument_id,"
              "side,order_type,quantity,price,status,strategy,placed_ts) VALUES "
              "(?,?,84,'buy','limit_order',1,100.0,?,'g3',?)",
              (coid, f"d-{coid}", status, D0))
    c.commit()


def add_open_position(c, coid, *, entry_proc="2026-09-01T10:05:00.000000+00:00",
                      delta_e="3001"):
    c.execute("INSERT INTO orders (client_order_id,delta_order_id,instrument_id,"
              "side,order_type,quantity,price,status,strategy,placed_ts) VALUES "
              "(?,?,84,'buy','limit_order',1,100.0,'filled','g3',?)",
              (coid, delta_e, D0))
    c.execute("INSERT INTO order_fills VALUES (?,NULL,?,84,'buy',1,100000.0,0.05,"
              "'entry',NULL,?,?)", (f"f-{delta_e}", delta_e, E_EXCH, entry_proc))
    c.execute("INSERT INTO positions (instrument_id,side,quantity,entry_price,"
              "entry_ts,status,strategy) VALUES (84,'buy',1,100000.0,?,'open','g3')",
              (entry_proc,))
    c.commit()


def add_filled_no_position(c, coid, *, entry_proc="2026-09-01T10:03:00.000000+00:00",
                           delta_e="7001"):
    """probe6 Case 1: FILLED order with an entry fill, but no position row."""
    c.execute("INSERT INTO orders (client_order_id,delta_order_id,instrument_id,"
              "side,order_type,quantity,price,status,strategy,placed_ts) VALUES "
              "(?,?,84,'buy','limit_order',1,100.0,'filled','g3',?)",
              (coid, delta_e, D0))
    c.execute("INSERT INTO order_fills VALUES (?,NULL,?,84,'buy',1,100000.0,0.05,"
              "'entry',NULL,?,?)", (f"f-{delta_e}", delta_e, E_EXCH, entry_proc))
    c.commit()


def sweep(features_db, outcomes_db, trading_db, **kw):
    return OW.sweep(features_db, outcomes_db, str(trading_db), **kw)


# ---------------------------------------------------------------------------
# COMPLETED
# ---------------------------------------------------------------------------

class TestCompleted:
    def test_completed_from_a_closed_position(self, trading, features, outcomes):
        p, c = trading
        add_completed(c, "g3-done")
        capture(features, "g3-done")

        res = sweep(features, outcomes, p)
        assert res.persisted == 1 and res.anomalies == ()
        labs = OS.load_labels(outcomes)
        assert [(l["decision_id"], l["status"]) for l in labs] == [
            ("g3-done", "COMPLETED")]

    def test_completed_needs_no_mirror_row(self, trading, features, outcomes):
        """The trading schema here has no mirrored_forward_entry table at all;
        COMPLETED must still resolve."""
        p, c = trading
        assert c.execute("SELECT name FROM sqlite_master WHERE "
                         "name='mirrored_forward_entry'").fetchone() is None
        add_completed(c, "g3-done")
        capture(features, "g3-done")
        assert sweep(features, outcomes, p).persisted == 1

    def test_pmirror_presence_does_not_change_the_outcome(
            self, trading, features, outcomes, tmp_path):
        p, c = trading
        add_completed(c, "g3-done")
        capture(features, "g3-done")
        without = sweep(features, outcomes, p)

        # Same inputs, now with a populated mirror table.
        p2 = tmp_path / "with_mirror.db"
        c2 = sqlite3.connect(p2)
        c2.executescript(_TRADING_SCHEMA)
        c2.execute("INSERT INTO instruments (id,symbol,contract_value,tick_size) "
                   "VALUES (84,'BTCUSD',0.001,0.1)")
        c2.execute("CREATE TABLE mirrored_forward_entry (forward_entry_id INTEGER "
                   "PRIMARY KEY, entry_client_order_id TEXT, state TEXT, "
                   "exit_cause TEXT)")
        c2.execute("INSERT INTO mirrored_forward_entry VALUES "
                   "(1,'g3-done','CLOSED_AT_HORIZON','horizon')")
        c2.commit()
        add_completed(c2, "g3-done")
        f2 = tmp_path / "f2.db"
        DS.init_db(f2)
        cc = sqlite3.connect(f2)
        cc.execute("INSERT INTO feature_contracts VALUES ('f',1,'h','{}',?)",
                   (WRITTEN_AT,))
        cc.commit()
        cc.close()
        capture(f2, "g3-done")
        with_mirror = sweep(f2, tmp_path / "o2.db", p2)

        assert (without.persisted, without.pending, without.anomalies) == \
               (with_mirror.persisted, with_mirror.pending, with_mirror.anomalies)

    def test_decision_ts_comes_from_feature_capture(self, trading, features, outcomes):
        p, c = trading
        add_completed(c, "g3-done")
        capture(features, "g3-done", decision_ts="2026-09-01T10:00:00.123456+00:00")
        sweep(features, outcomes, p)
        lab = OS.load_labels(outcomes)[0]
        assert to_utc(lab["decision_ts"], "iso") == to_utc(
            "2026-09-01T10:00:00.123456+00:00", "iso")


# ---------------------------------------------------------------------------
# NO_TRADE
# ---------------------------------------------------------------------------

class TestNoTrade:
    def test_no_trade_from_a_terminal_non_filled_order(self, trading, features,
                                                       outcomes):
        p, c = trading
        add_terminal_no_fill(c, "g3-rej", status="rejected")
        capture(features, "g3-rej")
        res = sweep(features, outcomes, p)
        assert res.persisted == 1 and res.anomalies == ()
        assert OS.load_labels(outcomes)[0]["status"] == "NO_TRADE"

    def test_cancelled_resting_order_is_no_trade(self, trading, features, outcomes):
        p, c = trading
        add_terminal_no_fill(c, "g3-cxl", status="cancelled", delta_id="d-cxl")
        capture(features, "g3-cxl")
        assert sweep(features, outcomes, p).persisted == 1
        assert OS.load_labels(outcomes)[0]["status"] == "NO_TRADE"

    def test_acked_resting_order_stays_unlabelled(self, trading, features, outcomes):
        p, c = trading
        add_resting(c, "g3-rest", status="acked")
        capture(features, "g3-rest")
        res = sweep(features, outcomes, p)
        assert res.persisted == 0 and res.pending == 1 and res.anomalies == ()
        assert OS.load_labels(outcomes) == []
        # And again on the next sweep -- never aged out.
        assert sweep(features, outcomes, p).pending == 1

    def test_filled_but_missing_position_is_refused_not_no_trade(
            self, trading, features, outcomes):
        p, c = trading
        add_filled_no_position(c, "g3-ghostfill")
        capture(features, "g3-ghostfill")
        res = sweep(features, outcomes, p)
        assert res.persisted == 0
        assert len(res.anomalies) == 1 and "FILLED" in res.anomalies[0]
        assert OS.load_labels(outcomes) == []

    def test_filled_but_unlinkable_position_is_refused(self, trading, features,
                                                       outcomes):
        """probe6 Case 2: the position exists but entry_ts drifted from the
        fill's processed_ts, so build_dataset cannot link it."""
        p, c = trading
        add_filled_no_position(c, "g3-drift", delta_e="8001",
                               entry_proc="2026-09-01T10:04:00.000000+00:00")
        # a closed position whose entry_ts does NOT match the fill's processed_ts
        c.execute("INSERT INTO positions (instrument_id,side,quantity,entry_price,"
                  "entry_ts,exit_price,exit_ts,pnl,status,strategy) VALUES "
                  "(84,'buy',1,100000.0,'2026-09-01T10:04:00.000001+00:00',"
                  "99000.0,?,-1.0,'closed','g3')", (X_PROC,))
        c.commit()
        capture(features, "g3-drift")
        res = sweep(features, outcomes, p)
        assert res.persisted == 0 and len(res.anomalies) == 1
        assert OS.load_labels(outcomes) == []


# ---------------------------------------------------------------------------
# What is never written
# ---------------------------------------------------------------------------

class TestNeverWritten:
    def test_open_is_never_persisted(self, trading, features, outcomes):
        p, c = trading
        add_open_position(c, "g3-open")
        capture(features, "g3-open")
        res = sweep(features, outcomes, p)
        assert res.persisted == 0 and res.pending == 1 and res.anomalies == ()
        assert OS.load_labels(outcomes) == []

    def test_unmatched_is_never_persisted(self, trading, features, outcomes):
        p, c = trading
        capture(features, "g3-ghost")          # captured, but no order at all
        res = sweep(features, outcomes, p)
        assert res.persisted == 0 and res.pending == 1 and res.anomalies == ()
        assert OS.load_labels(outcomes) == []


# ---------------------------------------------------------------------------
# Retry / idempotency / append-only
# ---------------------------------------------------------------------------

class TestRetryAndIdempotency:
    def test_repeated_sweep_is_idempotent(self, trading, features, outcomes):
        p, c = trading
        add_completed(c, "g3-done")
        add_terminal_no_fill(c, "g3-rej")
        capture(features, "g3-done")
        capture(features, "g3-rej")

        first = sweep(features, outcomes, p)
        assert first.persisted == 2
        for _ in range(3):
            again = sweep(features, outcomes, p)
            assert again.persisted == 0 and again.already_labelled == 2
        assert len(OS.load_labels(outcomes)) == 2

    def test_a_failed_persist_is_retried_next_sweep(self, trading, features,
                                                    outcomes, monkeypatch):
        p, c = trading
        add_completed(c, "g3-done")
        capture(features, "g3-done")

        calls = {"n": 0}
        real = OS.persist_labels

        def flaky(db, labels, written_at):
            calls["n"] += 1
            if calls["n"] == 1:
                raise sqlite3.OperationalError("disk I/O error")
            return real(db, labels, written_at)

        monkeypatch.setattr(OS, "persist_labels", flaky)
        with pytest.raises(sqlite3.OperationalError):
            sweep(features, outcomes, p)
        assert OS.load_labels(outcomes) == [], "nothing written on the failed pass"

        # Next sweep: the decision is still unlabelled, and it now succeeds.
        res = sweep(features, outcomes, p)
        assert res.persisted == 1
        assert OS.load_labels(outcomes)[0]["status"] == "COMPLETED"

    def test_an_existing_outcome_row_is_never_updated(self, trading, features,
                                                      outcomes):
        p, c = trading
        add_terminal_no_fill(c, "g3-rej", status="rejected")
        capture(features, "g3-rej")
        sweep(features, outcomes, p)
        row = OS.load_labels(outcomes)[0]

        # The trading DB moves on: the same coid now also has a FILLED order and
        # a completed position. A second sweep must NOT rewrite the frozen row.
        add_completed(c, "g3-rej", delta_e="9101", delta_x="9102")
        res = sweep(features, outcomes, p)
        assert res.persisted == 0 and res.already_labelled == 1
        after = OS.load_labels(outcomes)[0]
        assert after == row, "the append-only row is unchanged"

    def test_a_divergent_persist_is_recorded_as_an_anomaly_not_raised(
            self, trading, features, outcomes, monkeypatch):
        """persist_labels raises DivergenceError on a conflicting key. The sweep
        records it and moves on -- it never overwrites and never crashes."""
        p, c = trading
        add_completed(c, "g3-a")
        add_terminal_no_fill(c, "g3-b")
        capture(features, "g3-a")
        capture(features, "g3-b")

        real = OS.persist_labels

        def one_diverges(db, labels, written_at):
            if labels[0].decision_id == "g3-a":
                raise OS.DivergenceError("g3-a already labelled differently in "
                                         "['gross_return']")
            return real(db, labels, written_at)

        monkeypatch.setattr(OS, "persist_labels", one_diverges)
        res = sweep(features, outcomes, p)
        assert res.persisted == 1                      # g3-b still went through
        assert any("divergence" in a for a in res.anomalies)
        assert [l["decision_id"] for l in OS.load_labels(outcomes)] == ["g3-b"]


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------

class TestStructure:
    def _code(self, name):
        src = (_RESEARCH / "label_store" / name).read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)) and ast.get_docstring(node):
                node.body = node.body[1:] or [ast.Pass()]
        return ast.unparse(tree)

    def test_the_writer_is_mirror_free(self):
        code = self._code("outcome_writer.py")
        assert "mirror" not in code.lower()
        assert "mirrored_forward" not in code

    def test_the_writer_does_not_touch_the_label_contract(self):
        code = self._code("outcome_writer.py")
        for banned in ("LABEL_CONTRACT", "register_contract", "label_version ="):
            assert banned not in code

    def test_label_contract_hash_is_unchanged(self):
        assert OS.LABEL_CONTRACT_HASH == OS._content_hash(OS.LABEL_CONTRACT)

    def test_decision_index_is_a_single_query(self, features):
        capture(features, "a", decision_ts="2026-09-01T10:00:00.111111+00:00")
        capture(features, "b", decision_ts="2026-09-01T10:00:00.222222+00:00")
        idx = DS.decision_index(features)
        assert set(idx) == {"a", "b"}
        assert idx["a"] == "2026-09-01T10:00:00.111111+00:00"
