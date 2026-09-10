"""Tests for deterministic execution-outcome labelling.

Synthetic trading databases with explicit instants, plus a real-corpus block
gated on the extracted production slices.
"""
from __future__ import annotations

import ast
import os
import pathlib
import sqlite3
import sys
from datetime import datetime, timezone

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_RESEARCH = _ROOT / "research"
if str(_RESEARCH) not in sys.path:
    sys.path.insert(0, str(_RESEARCH))

from label_store import outcome_store as OS  # noqa: E402

UTC = timezone.utc
WRITTEN_AT = "2026-09-02T13:00:00+00:00"

SCHEMA = """
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

D0 = "2026-09-01T10:00:00.000000+00:00"          # decision
E_EXCH, E_PROC = "2026-09-01T10:00:05.000000Z", "2026-09-01T10:01:00.000000+00:00"
X_EXCH, X_PROC = "2026-09-01T11:00:05.000000Z", "2026-09-01T11:01:00.000000+00:00"


def T(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


@pytest.fixture()
def trading(tmp_path):
    p = tmp_path / "btc.db"
    c = sqlite3.connect(p)
    c.executescript(SCHEMA)
    c.execute("INSERT INTO instruments (id,symbol,contract_value,tick_size) "
              "VALUES (84,'BTCUSD',0.001,0.1)")
    c.commit()
    return p, c


@pytest.fixture()
def store(tmp_path):
    p = tmp_path / "outcomes.db"
    OS.init_db(p)
    return p


def add_completed(c, coid="c1", *, entry=100000.0, exit_px=99000.0, side="buy",
                  trigger=99100.0, with_stop=True, entry_proc=E_PROC,
                  exit_proc=X_PROC, entry_exch=E_EXCH, exit_exch=X_EXCH,
                  entry_fee=0.05, exit_fee=0.05, delta_e="1001", delta_x="1002",
                  strategy="g3"):
    exit_side = "sell" if side == "buy" else "buy"
    direction = 1 if side == "buy" else -1
    pnl = (exit_px - entry) * direction * 0.001 - entry_fee - exit_fee
    c.execute("INSERT INTO orders (client_order_id,delta_order_id,instrument_id,side,"
              "order_type,quantity,price,status,strategy,placed_ts) VALUES "
              "(?,?,84,?,'limit_order',1,?, 'filled',?,?)",
              (coid, delta_e, side, entry + 1, strategy, D0))
    c.execute("INSERT INTO orders (client_order_id,delta_order_id,instrument_id,side,"
              "order_type,quantity,price,status,strategy,placed_ts) VALUES "
              "(?,?,84,?,'limit_order',1,?, 'filled','protection',?)",
              (f"stop-{delta_x}", delta_x, exit_side, exit_px - 100, entry_proc))
    c.execute("INSERT INTO order_fills VALUES (?,NULL,?,84,?,1,?,?,'entry',NULL,?,?)",
              (f"f-{delta_e}-e", delta_e, side, entry, entry_fee, entry_exch, entry_proc))
    c.execute("INSERT INTO order_fills VALUES (?,NULL,?,84,?,1,?,?,'exit',?,?,?)",
              (f"f-{delta_x}-x", delta_x, exit_side, exit_px, exit_fee, pnl,
               exit_exch, exit_proc))
    c.execute("INSERT INTO positions (instrument_id,side,quantity,entry_price,entry_ts,"
              "exit_price,exit_ts,pnl,status,strategy) VALUES (84,?,1,?,?,?,?,?,'closed',?)",
              (side, entry, entry_proc, exit_px, exit_proc, pnl, strategy))
    if with_stop:
        pid = c.execute("SELECT MAX(id) FROM positions").fetchone()[0]
        c.execute("INSERT INTO protective_stops VALUES (?,?,?,?,1,?,?,?,1,?)",
                  (f"stop-{delta_x}", delta_x, pid, exit_side, entry, trigger,
                   exit_px - 100, entry_proc))
    c.commit()
    return pnl


def add_open(c, coid="o1", *, entry=100000.0, side="buy", entry_proc=E_PROC,
             delta_e="2001", strategy="g3"):
    c.execute("INSERT INTO orders (client_order_id,delta_order_id,instrument_id,side,"
              "order_type,quantity,price,status,strategy,placed_ts) VALUES "
              "(?,?,84,?,'limit_order',1,?, 'filled',?,?)",
              (coid, delta_e, side, entry + 1, strategy, D0))
    c.execute("INSERT INTO order_fills VALUES (?,NULL,?,84,?,1,?,0.05,'entry',NULL,?,?)",
              (f"f-{delta_e}-e", delta_e, side, entry, E_EXCH, entry_proc))
    c.execute("INSERT INTO positions (instrument_id,side,quantity,entry_price,entry_ts,"
              "status,strategy) VALUES (84,?,1,?,?,'open',?)",
              (side, entry, entry_proc, strategy))
    c.commit()


def add_rejected(c, coid="r1", code="bad_schema"):
    c.execute("INSERT INTO orders (client_order_id,delta_order_id,instrument_id,side,"
              "order_type,quantity,price,status,strategy,placed_ts) VALUES "
              "(?,NULL,84,'buy','limit_order',1,100.0,'rejected','g3',?)", (coid, D0))
    c.execute("INSERT INTO order_rejections VALUES (?,?,400,?,'{}')", (coid, D0, code))
    c.commit()


def dec(*coids, ts=D0):
    return {c: T(ts) for c in coids}


class TestLabelContract:
    def test_contract_mirrors_the_existing_convention_vocabulary(self):
        for field in ("label", "definition_name", "horizon_governance",
                      "fill_convention", "cost_convention",
                      "direction_convention", "maturity_rule",
                      "validity_condition", "conditioning_propagation",
                      "binding_id"):
            assert field in OS.LABEL_CONTRACT and OS.LABEL_CONTRACT[field] is not None \
                or field == "horizon_days"

    def test_contract_hash_is_stable(self):
        assert OS.LABEL_CONTRACT_HASH == OS._content_hash(OS.LABEL_CONTRACT)
        assert len(OS.LABEL_CONTRACT_HASH) == 64

    def test_four_statuses_and_only_completed_is_resolved(self):
        assert set(OS.STATUSES) == {"COMPLETED", "OPEN", "NO_TRADE", "UNMATCHED"}
        assert OS.RESOLVED_STATUSES == ("COMPLETED",)

    def test_it_does_not_write_to_the_governed_label_store(self):
        src = _strip_docstrings(
            (_RESEARCH / "label_store" / "outcome_store.py").read_text(encoding="utf-8"))
        assert "label_definitions" not in src
        assert "label_specifications" not in src
        assert "label_store.db" not in src

    def test_default_db_resolves_under_research_data(self):
        """Relocated to research/data/ -- the subtree the production systemd
        sandbox (ProtectSystem=strict) grants write access to -- alongside
        decision_features.db."""
        resolved = OS.DEFAULT_DB.resolve()
        assert resolved.name == "decision_outcomes.db"
        assert resolved.parent == (_RESEARCH / "data").resolve()
        assert resolved.parent.is_dir(), "research/data/ must already exist"

    def test_default_db_is_not_under_the_governed_store_directory(self):
        parts = OS.DEFAULT_DB.resolve().parts
        assert "label_store" not in parts
        assert "feature_store" not in parts


class TestCompletedTrade:
    def test_completed_label_carries_the_outcome(self, trading, store):
        p, c = trading
        pnl = add_completed(c, "c1")
        lab = OS.build_labels(dec("c1"), p)[0]
        assert lab.status == OS.COMPLETED and lab.trade_occurred is True
        assert lab.direction == 1 and lab.quantity == 1.0
        assert lab.entry_price == 100000.0 and lab.exit_price == 99000.0
        assert lab.realised_pnl_usd == pytest.approx(pnl)
        assert lab.resolved is True

    def test_gross_return_is_direction_signed(self, trading, store):
        p, c = trading
        add_completed(c, "c1", entry=100000.0, exit_px=99000.0, side="sell",
                      trigger=101000.0)
        lab = OS.build_labels(dec("c1"), p)[0]
        assert lab.direction == -1
        assert lab.gross_return == pytest.approx(0.01)

    def test_fees_and_holding_duration(self, trading, store):
        p, c = trading
        add_completed(c, "c1", entry_fee=0.04, exit_fee=0.06)
        lab = OS.build_labels(dec("c1"), p)[0]
        assert lab.fees_usd == pytest.approx(0.10)
        assert lab.holding_seconds == pytest.approx(3600.0)

    def test_realised_and_configured_risk(self, trading, store):
        p, c = trading
        add_completed(c, "c1", entry=100000.0, exit_px=99000.0, trigger=99100.0,
                      entry_fee=0.04, exit_fee=0.06)
        lab = OS.build_labels(dec("c1"), p)[0]
        assert lab.realised_risk_usd == pytest.approx(-lab.realised_pnl_usd)
        assert lab.configured_risk_usd == pytest.approx(900 * 0.001 + 0.10)

    def test_a_winner_has_no_realised_risk(self, trading, store):
        p, c = trading
        add_completed(c, "c1", entry=99000.0, exit_px=100000.0, trigger=98000.0)
        lab = OS.build_labels(dec("c1"), p)[0]
        assert lab.realised_pnl_usd > 0 and lab.realised_risk_usd is None


class TestExitReason:
    def test_stop_exit_is_flagged(self, trading, store):
        p, c = trading
        add_completed(c, "c1", with_stop=True)
        lab = OS.build_labels(dec("c1"), p)[0]
        assert lab.exit_reason == "protective_stop" and lab.stop_hit is True

    def test_non_stop_exit_is_distinguished(self, trading, store):
        p, c = trading
        add_completed(c, "c1", with_stop=False)
        lab = OS.build_labels(dec("c1"), p)[0]
        assert lab.exit_reason == "other" and lab.stop_hit is False


class TestOpenTrade:
    def test_open_position_is_not_a_completed_outcome(self, trading, store):
        p, c = trading
        add_open(c, "o1")
        lab = OS.build_labels(dec("o1"), p)[0]
        assert lab.status == OS.OPEN and lab.resolved is False
        assert lab.trade_occurred is True and lab.direction == 1
        assert lab.entry_price == 100000.0

    def test_open_label_has_no_outcome_fields(self, trading, store):
        p, c = trading
        add_open(c, "o1")
        lab = OS.build_labels(dec("o1"), p)[0]
        assert lab.exit_ts is None and lab.exit_price is None
        assert lab.realised_pnl_usd is None, "unknown is not zero"
        assert lab.gross_return is None and lab.holding_seconds is None

    def test_open_label_says_why(self, trading, store):
        p, c = trading
        add_open(c, "o1")
        assert "not yet knowable" in OS.build_labels(dec("o1"), p)[0].detail


class TestMissingAndUnmatched:
    def test_rejected_order_is_no_trade_with_its_code(self, trading, store):
        p, c = trading
        add_rejected(c, "r1", "bad_schema")
        lab = OS.build_labels(dec("r1"), p)[0]
        assert lab.status == OS.NO_TRADE and lab.trade_occurred is False
        assert lab.provenance["rejection_code"] == "bad_schema"

    def test_unknown_decision_id_is_unmatched_not_dropped(self, trading, store):
        p, c = trading
        add_completed(c, "c1")
        labs = OS.build_labels(dec("c1", "ghost"), p)
        assert len(labs) == 2
        ghost = [l for l in labs if l.decision_id == "ghost"][0]
        assert ghost.status == OS.UNMATCHED
        assert "no order carries" in ghost.detail

    def test_no_decision_is_ever_silently_dropped(self, trading, store):
        p, c = trading
        add_completed(c, "c1")
        add_open(c, "o1")
        add_rejected(c, "r1")
        labs = OS.build_labels(dec("c1", "o1", "r1", "ghost"), p)
        assert {l.decision_id for l in labs} == {"c1", "o1", "r1", "ghost"}
        assert {l.status for l in labs} == {OS.COMPLETED, OS.OPEN, OS.NO_TRADE,
                                            OS.UNMATCHED}


class TestMultipleExecutions:
    def test_two_trades_on_one_decision_id_pick_the_earliest_and_say_so(
            self, trading, store):
        p, c = trading
        add_completed(c, "c1", delta_e="1001", delta_x="1002")
        add_completed(c, "c1", entry=200000.0, exit_px=199000.0,
                      delta_e="3001", delta_x="3002",
                      entry_proc="2026-09-01T12:01:00.000000+00:00",
                      exit_proc="2026-09-01T13:01:00.000000+00:00",
                      entry_exch="2026-09-01T12:00:05.000000Z",
                      exit_exch="2026-09-01T13:00:05.000000Z")
        lab = OS.build_labels(dec("c1"), p)[0]
        assert lab.entry_price == 100000.0, "earliest entry wins"
        assert "2 completed trades share this decision id" in lab.detail

    def test_selection_is_deterministic(self, trading, store):
        p, c = trading
        add_completed(c, "c1", delta_e="1001", delta_x="1002")
        add_completed(c, "c1", entry=200000.0, exit_px=199000.0,
                      delta_e="3001", delta_x="3002",
                      entry_proc="2026-09-01T12:01:00.000000+00:00",
                      exit_proc="2026-09-01T13:01:00.000000+00:00",
                      entry_exch="2026-09-01T12:00:05.000000Z",
                      exit_exch="2026-09-01T13:00:05.000000Z")
        assert OS.build_labels(dec("c1"), p) == OS.build_labels(dec("c1"), p)


class TestTimestampOrderingAndLeakage:
    def test_entry_before_decision_is_refused(self, trading, store):
        p, c = trading
        add_completed(c, "c1")
        late = {"c1": T("2026-09-01T23:00:00+00:00")}
        with pytest.raises(OS.LabelLeakageError, match="precedes decision"):
            OS.build_labels(late, p)

    def test_open_entry_before_decision_is_refused(self, trading, store):
        p, c = trading
        add_open(c, "o1")
        with pytest.raises(OS.LabelLeakageError, match="precedes decision"):
            OS.build_labels({"o1": T("2026-09-01T23:00:00+00:00")}, p)

    def test_entry_exactly_at_the_decision_instant_is_allowed(self, trading, store):
        p, c = trading
        add_completed(c, "c1", entry_proc=D0)
        assert OS.build_labels(dec("c1"), p)[0].status == OS.COMPLETED

    def test_stored_labels_revalidate_temporally(self, trading, store):
        p, c = trading
        add_completed(c, "c1")
        add_open(c, "o1")
        OS.persist_labels(store, OS.build_labels(dec("c1", "o1"), p), WRITTEN_AT)
        assert OS.verify_temporal_order(store) == []

    def test_a_tampered_stored_label_is_caught(self, trading, store):
        p, c = trading
        add_completed(c, "c1")
        OS.persist_labels(store, OS.build_labels(dec("c1"), p), WRITTEN_AT)
        con = sqlite3.connect(store)
        con.execute("DROP TRIGGER trg_decision_outcomes_no_update")
        con.execute("UPDATE decision_outcomes SET entry_ts='2026-08-01T00:00:00+00:00'")
        con.commit()
        con.close()
        v = OS.verify_temporal_order(store)
        assert v and v[0]["reason"] == "entry precedes decision"

    def test_alignment_never_imports_the_label_module(self):
        """The Central Asymmetry, enforced structurally.

        One import in the feature layer is all it would take to make features
        depend on data after t.
        """
        for name in ("features.py", "align.py", "timebase.py"):
            src = (_RESEARCH / "alignment" / name).read_text(encoding="utf-8")
            assert "outcome_store" not in src
            assert "label_store" not in src

    def test_feature_store_never_imports_the_label_module(self):
        src = (_RESEARCH / "feature_store" / "decision_store.py").read_text(
            encoding="utf-8")
        assert "outcome_store" not in src and "label_store" not in src


class TestExcursions:
    def test_mae_mfe_are_carried_from_the_trade_record(self, trading, store):
        p, c = trading
        add_completed(c, "c1", entry=100000.0, exit_px=99000.0)
        c.executemany("INSERT INTO spot_series VALUES (?,?)",
                      [("2026-09-01T10:30:00.000000+00:00", 100500.0),
                       ("2026-09-01T10:45:00.000000+00:00", 99200.0)])
        c.commit()
        lab = OS.build_labels(dec("c1"), p)[0]
        assert lab.mfe_pts == pytest.approx(500.0)
        assert lab.mae_pts == pytest.approx(-1000.0)
        assert lab.mae_pct is not None and lab.mfe_pct is not None

    def test_open_trades_carry_no_excursion(self, trading, store):
        p, c = trading
        add_open(c, "o1")
        lab = OS.build_labels(dec("o1"), p)[0]
        assert lab.mae_pts is None and lab.mfe_pts is None


class TestPersistenceAndVersioning:
    def test_write_then_identical_rewrite(self, trading, store):
        p, c = trading
        add_completed(c, "c1")
        labs = OS.build_labels(dec("c1"), p)
        assert OS.persist_labels(store, labs, WRITTEN_AT)["written"] == 1
        r = OS.persist_labels(store, labs, "2027-01-01T00:00:00+00:00")
        assert r["written"] == 0 and r["unchanged"] == 1

    def test_duplicate_decision_id_with_a_different_outcome_is_refused(
            self, trading, store):
        p, c = trading
        add_completed(c, "c1", entry=100000.0, exit_px=99000.0)
        labs = OS.build_labels(dec("c1"), p)
        OS.persist_labels(store, labs, WRITTEN_AT)
        tampered = [OS.OutcomeLabel(**{**labs[0].__dict__,
                                       "realised_pnl_usd": 999.0})]
        with pytest.raises(OS.DivergenceError, match="realised_pnl_usd"):
            OS.persist_labels(store, tampered, WRITTEN_AT)
        assert OS.load_labels(store)[0]["realised_pnl_usd"] != 999.0

    def test_changed_contract_is_a_new_version(self, store):
        v1 = OS.register_contract(store, WRITTEN_AT)
        changed = {**OS.LABEL_CONTRACT, "cost_convention": "different"}
        v2 = OS.register_contract(store, WRITTEN_AT, changed)
        assert v1 == 1 and v2 == 2

    def test_same_contract_registers_once(self, store):
        assert OS.register_contract(store, WRITTEN_AT) == 1
        assert OS.register_contract(store, WRITTEN_AT) == 1

    def test_contracts_are_immutable(self, store):
        OS.register_contract(store, WRITTEN_AT)
        con = sqlite3.connect(store)
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            con.execute("UPDATE label_contracts SET contract_hash='x'")
        con.close()

    def test_labels_are_append_only(self, trading, store):
        p, c = trading
        add_completed(c, "c1")
        OS.persist_labels(store, OS.build_labels(dec("c1"), p), WRITTEN_AT)
        con = sqlite3.connect(store)
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            con.execute("UPDATE decision_outcomes SET realised_pnl_usd=0")
        con.close()

    def test_no_update_statement_in_the_module(self):
        src = _strip_docstrings(
            (_RESEARCH / "label_store" / "outcome_store.py").read_text(encoding="utf-8"))
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call) and getattr(
                    node.func, "attr", None) in ("execute", "executescript"):
                for arg in ast.walk(node):
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        assert not arg.value.strip().upper().startswith("UPDATE")


class TestDeterminism:
    def test_repeated_builds_are_identical(self, trading, store):
        p, c = trading
        add_completed(c, "c1")
        add_open(c, "o1")
        add_rejected(c, "r1")
        d = dec("c1", "o1", "r1", "ghost")
        assert OS.build_labels(d, p) == OS.build_labels(d, p)

    def test_output_order_is_stable_regardless_of_input_order(self, trading, store):
        p, c = trading
        add_completed(c, "c1")
        add_open(c, "o1")
        a = [l.decision_id for l in OS.build_labels(dec("c1", "o1"), p)]
        b = [l.decision_id for l in OS.build_labels(dec("o1", "c1"), p)]
        assert a == b == sorted(a)

    def test_build_reads_no_clock(self):
        src = _strip_docstrings(
            (_RESEARCH / "label_store" / "outcome_store.py").read_text(encoding="utf-8"))
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef) and n.name == "build_labels")
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                label = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                assert label not in ("now", "utcnow", "today", "time")


class TestCoverageReport:
    def test_counts_by_status_and_resolved_rate(self, trading, store):
        p, c = trading
        add_completed(c, "c1")
        add_open(c, "o1")
        OS.persist_labels(store, OS.build_labels(dec("c1", "o1"), p), WRITTEN_AT)
        cov = OS.coverage(store)
        assert cov["labels"] == 2
        assert cov["by_status"] == {OS.COMPLETED: 1, OS.OPEN: 1}
        assert cov["resolved"] == 1 and cov["resolved_rate"] == 0.5


def _strip_docstrings(src: str) -> str:
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and ast.get_docstring(node):
            node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def _env_db(var):
    v = os.environ.get(var)
    p = pathlib.Path(v) if v else None
    return p if p and p.exists() else None


_EXEC = _env_db("BTCBOT_EXEC_SLICE")


@pytest.mark.skipif(_EXEC is None, reason="no execution slice")
class TestAgainstRealCorpus:
    DECISIONS = {
        "g3_momentum_tsmom-2026-08-19-sell-1": "2026-08-19T09:59:55.974332+00:00",
        "g3_momentum_tsmom-2026-08-20-sell-1": "2026-08-20T07:34:04.297865+00:00",
        "g3momentumtsmo3ad48e-20260820-s1": "2026-08-20T20:43:11.998289+00:00",
        "g3momentumtsmo3ad48e-20260824-b1": "2026-08-24T14:27:41.703267+00:00",
        "g3momentumtsmo3ad48e-20260824-b2": "2026-08-24T15:58:34.612751+00:00",
        "g3momentumtsmo3ad48e-20260825-b1": "2026-08-25T06:44:13.693173+00:00",
        "g3momentumtsmo3ad48e-20260901-b1": "2026-09-01T17:16:20.174497+00:00",
        "g3momentumtsmo3ad48e-20260902-b1": "2026-09-02T00:01:20.167341+00:00",
    }

    def _decisions(self):
        return {k: T(v) for k, v in self.DECISIONS.items()}

    def test_every_decision_is_labelled(self):
        labs = OS.build_labels(self._decisions(), _EXEC)
        assert len(labs) == len(self.DECISIONS)

    def test_all_four_statuses_appear(self):
        got = {l.status for l in OS.build_labels(self._decisions(), _EXEC)}
        assert {OS.COMPLETED, OS.OPEN, OS.NO_TRADE} <= got

    def test_the_stopped_out_trade_labels_correctly(self):
        labs = {l.decision_id: l for l in OS.build_labels(self._decisions(), _EXEC)}
        l = labs["g3momentumtsmo3ad48e-20260901-b1"]
        assert l.status == OS.COMPLETED and l.stop_hit is True
        assert l.realised_pnl_usd == pytest.approx(-1.01338508)
        assert l.mae_pts == pytest.approx(-922.5)
        assert l.holding_seconds == pytest.approx(5280.008672)
        assert l.provenance["stop_client_order_id"] == "stop-p5-q1"

    def test_the_open_trade_is_not_resolved(self):
        labs = {l.decision_id: l for l in OS.build_labels(self._decisions(), _EXEC)}
        l = labs["g3momentumtsmo3ad48e-20260902-b1"]
        assert l.status == OS.OPEN and l.resolved is False
        assert l.realised_pnl_usd is None

    def test_the_rejected_decision_is_no_trade(self):
        labs = {l.decision_id: l for l in OS.build_labels(self._decisions(), _EXEC)}
        l = labs["g3_momentum_tsmom-2026-08-20-sell-1"]
        assert l.status == OS.NO_TRADE and l.trade_occurred is False

    def test_real_labels_are_deterministic_and_temporally_clean(self, store):
        d = self._decisions()
        assert OS.build_labels(d, _EXEC) == OS.build_labels(d, _EXEC)
        OS.persist_labels(store, OS.build_labels(d, _EXEC), WRITTEN_AT)
        assert OS.verify_temporal_order(store) == []
        assert OS.persist_labels(store, OS.build_labels(d, _EXEC),
                                 "2027-01-01T00:00:00+00:00")["written"] == 0
