"""Tests for the joined feature-outcome view.

Synthetic stores throughout, plus a real-corpus block gated on the extracted
production slices.
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

from alignment import align as AL  # noqa: E402
from alignment import features as FT  # noqa: E402
from alignment import timebase as TB  # noqa: E402
from decision_view import joined as JV  # noqa: E402
from feature_store import decision_store as DS  # noqa: E402
from label_store import outcome_store as OS  # noqa: E402

UTC = timezone.utc
WRITTEN_AT = "2026-09-02T16:00:00+00:00"
D0 = "2026-09-01T10:00:00.000000+00:00"
E_PROC = "2026-09-01T10:01:00.000000+00:00"
X_PROC = "2026-09-01T11:01:00.000000+00:00"
BASE_TS = "2026-09-01T09:59:30.000000+00:00"
BASE_US = int(datetime(2026, 9, 1, 9, 59, 30, tzinfo=UTC).timestamp() * 1_000_000)


def T(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def spec(name="f", field_name="spot_price", tolerance_s=300.0, validity=None):
    return FT.FeatureSpec(
        name=name,
        inputs=(FT.InputRequirement(
            name="x", source="option_quotes", field=field_name,
            tolerance_s=tolerance_s, key_field="instrument_id", key=84,
            validity=validity),),
        compute=lambda v: v["x"],
        description="test")


def quote_load(spot=100000.0):
    return AL.load_observations(
        [{"ts": BASE_TS, "exch_ts": BASE_US, "instrument_id": 84,
          "spot_price": spot, "mark_iv": 0.5}],
        TB.get_spec("option_quotes"),
        payload_fields=("spot_price", "mark_iv"))


@pytest.fixture()
def stores(tmp_path):
    f = tmp_path / "features.db"
    o = tmp_path / "outcomes.db"
    DS.init_db(f)
    OS.init_db(o)
    return f, o


def persist_features(fdb, decision_id, specs, ts=D0, load=None):
    row = FT.build_row(T(ts), specs, {"option_quotes": load or quote_load()})
    DS.persist_row(fdb, row, specs, WRITTEN_AT, decision_id=decision_id)
    return row


def trading_db(tmp_path, name="btc.db"):
    p = tmp_path / name
    c = sqlite3.connect(p)
    c.executescript("""
CREATE TABLE instruments (id INTEGER PRIMARY KEY, symbol TEXT, contract_type TEXT,
  underlying TEXT, strike_price REAL, settlement_time TEXT, tick_size REAL,
  contract_value REAL, state TEXT, raw_json TEXT, fetched_at TEXT);
CREATE TABLE orders (id INTEGER PRIMARY KEY AUTOINCREMENT, client_order_id TEXT,
  delta_order_id TEXT, instrument_id INTEGER NOT NULL, side TEXT NOT NULL,
  order_type TEXT NOT NULL, quantity REAL NOT NULL, price REAL, status TEXT NOT NULL,
  strategy TEXT, placed_ts TEXT NOT NULL, filled_ts TEXT);
CREATE TABLE order_fills (fill_id TEXT PRIMARY KEY, client_order_id TEXT,
  delta_order_id TEXT, instrument_id INTEGER NOT NULL, side TEXT NOT NULL,
  size REAL NOT NULL, price REAL NOT NULL, fee REAL NOT NULL DEFAULT 0.0,
  role TEXT NOT NULL, realised_pnl REAL, exch_ts TEXT, processed_ts TEXT NOT NULL);
CREATE TABLE order_rejections (client_order_id TEXT PRIMARY KEY, ts TEXT NOT NULL,
  http_status INTEGER, error_code TEXT, body TEXT);
CREATE TABLE positions (id INTEGER PRIMARY KEY AUTOINCREMENT, instrument_id INTEGER,
  side TEXT NOT NULL, quantity REAL NOT NULL, entry_price REAL NOT NULL,
  entry_ts TEXT NOT NULL, exit_price REAL, exit_ts TEXT, pnl REAL,
  status TEXT NOT NULL, strategy TEXT);
CREATE TABLE protective_stops (client_order_id TEXT PRIMARY KEY, delta_order_id TEXT,
  position_id INTEGER, side TEXT NOT NULL, size INTEGER NOT NULL,
  entry_price REAL NOT NULL, trigger_price REAL NOT NULL, limit_price REAL NOT NULL,
  reduce_only INTEGER NOT NULL DEFAULT 1, placed_ts TEXT NOT NULL);
CREATE TABLE spot_series (ts TEXT PRIMARY KEY, spot_price REAL NOT NULL);
CREATE TABLE trade_signal_context (client_order_id TEXT PRIMARY KEY,
  signal_id TEXT NOT NULL, direction INTEGER, raw_value REAL, signal_day TEXT,
  context_json TEXT, captured_ts TEXT NOT NULL);""")
    c.execute("INSERT INTO instruments (id,symbol,contract_value) VALUES (84,'BTCUSD',0.001)")
    c.commit()
    return p, c


def add_completed(c, coid, *, entry=100000.0, exit_px=99000.0, de="1001", dx="1002"):
    pnl = (exit_px - entry) * 0.001 - 0.10
    c.execute("INSERT INTO orders (client_order_id,delta_order_id,instrument_id,side,"
              "order_type,quantity,price,status,strategy,placed_ts) VALUES "
              "(?,?,84,'buy','limit_order',1,?, 'filled','g3',?)", (coid, de, entry, D0))
    c.execute("INSERT INTO order_fills VALUES (?,NULL,?,84,'buy',1,?,0.05,'entry',"
              "NULL,'2026-09-01T10:00:55Z',?)", (f"fe-{de}", de, entry, E_PROC))
    c.execute("INSERT INTO order_fills VALUES (?,NULL,?,84,'sell',1,?,0.05,'exit',"
              "?,'2026-09-01T11:00:55Z',?)", (f"fx-{dx}", dx, exit_px, pnl, X_PROC))
    c.execute("INSERT INTO positions (instrument_id,side,quantity,entry_price,entry_ts,"
              "exit_price,exit_ts,pnl,status,strategy) VALUES "
              "(84,'buy',1,?,?,?,?,?,'closed','g3')", (entry, E_PROC, exit_px, X_PROC, pnl))
    c.commit()


OPEN_PROC = "2026-09-01T12:01:00.000000+00:00"


def add_open(c, coid, de="2001"):
    """A still-open position. Its entry instant must differ from any completed
    trade's, exactly as it would in reality -- the cycle lookup keys on it."""
    c.execute("INSERT INTO orders (client_order_id,delta_order_id,instrument_id,side,"
              "order_type,quantity,price,status,strategy,placed_ts) VALUES "
              "(?,?,84,'buy','limit_order',1,100.0,'filled','g3',?)", (coid, de, D0))
    c.execute("INSERT INTO order_fills VALUES (?,NULL,?,84,'buy',1,100000.0,0.05,"
              "'entry',NULL,'2026-09-01T12:00:55Z',?)", (f"fe-{de}", de, OPEN_PROC))
    c.execute("INSERT INTO positions (instrument_id,side,quantity,entry_price,entry_ts,"
              "status,strategy) VALUES (84,'buy',1,100000.0,?,'open','g3')", (OPEN_PROC,))
    c.commit()


def add_rejected(c, coid):
    c.execute("INSERT INTO orders (client_order_id,delta_order_id,instrument_id,side,"
              "order_type,quantity,price,status,strategy,placed_ts) VALUES "
              "(?,NULL,84,'buy','limit_order',1,1.0,'rejected','g3',?)", (coid, D0))
    c.execute("INSERT INTO order_rejections VALUES (?,?,400,'bad_schema','{}')", (coid, D0))
    c.commit()


def label_all(odb, tdb, decision_ids):
    labs = OS.build_labels({d: T(D0) for d in decision_ids}, tdb)
    OS.persist_labels(odb, labs, WRITTEN_AT)
    return labs


class TestOneToOneJoin:
    def test_one_row_per_decision(self, stores, tmp_path):
        f, o = stores
        tdb, c = trading_db(tmp_path)
        specs = [spec()]
        for did in ("d1", "d2", "d3"):
            persist_features(f, did, specs)
        add_completed(c, "d1")
        add_open(c, "d2", de="2001")
        add_rejected(c, "d3")
        label_all(o, tdb, ["d1", "d2", "d3"])
        rows = JV.build_view(f, o, specs)
        assert len(rows) == 3
        assert len({r.decision_id for r in rows}) == 3

    def test_join_is_strictly_by_decision_id(self, stores, tmp_path):
        f, o = stores
        tdb, c = trading_db(tmp_path)
        specs = [spec()]
        persist_features(f, "d1", specs)
        add_completed(c, "d1")
        label_all(o, tdb, ["d1"])
        row = JV.build_view(f, o, specs)[0]
        assert row.decision_id == "d1"
        assert row.outcome["decision_id"] == "d1"

    def test_rows_are_ordered_by_decision_ts(self, stores, tmp_path):
        f, o = stores
        specs = [spec()]
        persist_features(f, "late", specs, ts="2026-09-01T12:00:00+00:00")
        persist_features(f, "early", specs, ts=D0)
        rows = JV.build_view(f, o, specs)
        assert [r.decision_id for r in rows] == ["early", "late"]

    def test_a_decision_with_only_a_label_still_appears(self, stores, tmp_path):
        """A labelled decision whose features were never persisted is carried.

        Another decision supplies the contract registration, as it would in
        practice -- the contract exists because the pipeline ran, even if this
        particular decision's rows are absent.
        """
        f, o = stores
        tdb, c = trading_db(tmp_path)
        specs = [spec()]
        persist_features(f, "has_features", specs)
        add_completed(c, "has_features")
        add_completed(c, "orphan", de="3001", dx="3002")
        label_all(o, tdb, ["has_features", "orphan"])
        rows = {r.decision_id: r for r in JV.build_view(f, o, specs)}
        assert set(rows) == {"has_features", "orphan"}
        assert rows["orphan"].features["f"].state == JV.MISSING_FROM_STORE
        assert rows["orphan"].outcome_status == OS.COMPLETED
        assert rows["has_features"].features["f"].available is True

    def test_an_empty_store_reports_the_unregistered_contract(self, stores):
        """Joining against a contract the store never saw is an error, not an
        empty result that looks like 'no decisions'."""
        f, o = stores
        with pytest.raises(JV.UnregisteredContract):
            JV.build_view(f, o, [spec()])


class TestVersionSelection:
    def test_selects_by_contract_hash_not_by_latest(self, stores):
        f, o = stores
        v1 = [spec(tolerance_s=300.0)]
        v2 = [spec(tolerance_s=60.0)]
        persist_features(f, "d1", v1)
        persist_features(f, "d1", v2)
        assert DS.register_contract(f, v1[0], WRITTEN_AT) == 1
        assert DS.register_contract(f, v2[0], WRITTEN_AT) == 2
        # asking for v1's contract must return v1 even though v2 is newer
        row = JV.build_view(f, o, v1)[0]
        assert row.features["f"].contract_version == 1

    def test_a_newer_unrelated_version_is_not_picked_up(self, stores):
        f, o = stores
        wanted = [spec(tolerance_s=300.0)]
        experimental = [spec(tolerance_s=1.0)]
        persist_features(f, "d1", wanted)
        persist_features(f, "d1", experimental)
        row = JV.build_view(f, o, wanted)[0]
        assert row.features["f"].contract_version == 1
        assert row.features["f"].contract_hash == DS.contract_hash(wanted[0])

    def test_unregistered_contract_raises_naming_the_feature(self, stores):
        f, o = stores
        with pytest.raises(JV.UnregisteredContract, match="f"):
            JV.build_view(f, o, [spec()])

    def test_intended_versions_maps_name_to_version_and_hash(self, stores):
        f, o = stores
        specs = [spec()]
        persist_features(f, "d1", specs)
        got = JV.intended_versions(f, specs)
        assert got["f"] == (1, DS.contract_hash(specs[0]))

    def test_the_v2_starter_set_resolves_to_its_intended_versions(self, stores):
        """spot_price v2 and option_atm_iv v2 alongside two v1 features."""
        f, o = stores
        specs = FT.starter_features("g3")
        con = sqlite3.connect(f)
        for s in specs:
            v = 2 if s.name in ("spot_price", "option_atm_iv") else 1
            if v == 2:                       # seed a v1 so v2 is genuinely later
                con.execute("INSERT INTO feature_contracts VALUES (?,?,?,?,?)",
                            (s.name, 1, "older" + s.name, "{}", WRITTEN_AT))
            con.execute("INSERT INTO feature_contracts VALUES (?,?,?,?,?)",
                        (s.name, v, DS.contract_hash(s), "{}", WRITTEN_AT))
        con.commit()
        con.close()
        got = JV.intended_versions(f, specs)
        assert got["spot_price"][0] == 2 and got["option_atm_iv"][0] == 2
        assert got["signal_position"][0] == 1 and got["consumed_direction"][0] == 1


class TestMissingFeatures:
    def test_absent_row_is_marked_not_imputed(self, stores, tmp_path):
        f, o = stores
        tdb, c = trading_db(tmp_path)
        a, b = spec("a"), spec("b", field_name="mark_iv")
        persist_features(f, "d1", [a, b])
        add_completed(c, "d1")
        label_all(o, tdb, ["d1"])
        con = sqlite3.connect(f)
        con.execute("DELETE FROM feature_rows WHERE feature_name='b'")
        con.commit()
        con.close()
        row = JV.build_view(f, o, [a, b])[0]
        assert row.features["b"].state == JV.MISSING_FROM_STORE
        assert row.features["b"].value is None
        assert row.features["b"].available is False
        assert any("no persisted row" in x for x in row.findings)

    def test_missing_from_store_is_not_a_dq_state(self):
        from dq import states as _s  # noqa: PLC0415
        assert JV.MISSING_FROM_STORE not in _s.STATES

    def test_an_unavailable_feature_keeps_its_state_and_provenance(self, stores):
        f, o = stores
        specs = [spec()]
        # decision before the observation -> INCOMPLETE_DATA, still joined
        persist_features(f, "d1", specs, ts="2026-09-01T09:00:00+00:00")
        row = JV.build_view(f, o, specs)[0]
        assert row.features["f"].state == "INCOMPLETE_DATA"
        assert row.features["f"].available is False
        assert row.features["f"].inputs[0]["source"] == "option_quotes"

    def test_values_returns_only_available_features(self, stores):
        f, o = stores
        a, b = spec("a"), spec("b", field_name="mark_iv")
        persist_features(f, "d1", [a, b])
        row = JV.build_view(f, o, [a, b])[0]
        assert set(row.values()) == {"a", "b"}
        assert row.all_features_available is True


class TestOutcomeStatusPreservation:
    def test_all_statuses_survive_the_join(self, stores, tmp_path):
        f, o = stores
        tdb, c = trading_db(tmp_path)
        specs = [spec()]
        for did in ("done", "open", "rej", "ghost"):
            persist_features(f, did, specs)
        add_completed(c, "done")
        add_open(c, "open", de="2001")
        add_rejected(c, "rej")
        label_all(o, tdb, ["done", "open", "rej", "ghost"])
        got = {r.decision_id: r.outcome_status for r in JV.build_view(f, o, specs)}
        assert got == {"done": OS.COMPLETED, "open": OS.OPEN,
                       "rej": OS.NO_TRADE, "ghost": OS.UNMATCHED}

    def test_non_completed_decisions_are_not_dropped(self, stores, tmp_path):
        f, o = stores
        tdb, c = trading_db(tmp_path)
        specs = [spec()]
        for did in ("done", "open", "rej"):
            persist_features(f, did, specs)
        add_completed(c, "done")
        add_open(c, "open", de="2001")
        add_rejected(c, "rej")
        label_all(o, tdb, ["done", "open", "rej"])
        rows = JV.build_view(f, o, specs)
        assert len(rows) == 3
        assert sum(1 for r in rows if r.resolved) == 1

    def test_open_outcome_carries_no_pnl(self, stores, tmp_path):
        f, o = stores
        tdb, c = trading_db(tmp_path)
        specs = [spec()]
        persist_features(f, "open", specs)
        add_open(c, "open", de="2001")
        label_all(o, tdb, ["open"])
        row = JV.build_view(f, o, specs)[0]
        assert row.outcome_status == OS.OPEN
        assert row.outcome["realised_pnl_usd"] is None
        assert row.resolved is False

    def test_a_decision_without_a_label_is_marked(self, stores):
        f, o = stores
        specs = [spec()]
        persist_features(f, "d1", specs)
        row = JV.build_view(f, o, specs)[0]
        assert row.outcome_status == JV.NO_LABEL and row.outcome is None
        assert any("no outcome label" in x for x in row.findings)

    def test_labels_are_not_recomputed(self):
        """The view reads stored labels; it must not call the label builder."""
        src = _strip_docstrings(
            (_RESEARCH / "decision_view" / "joined.py").read_text(encoding="utf-8"))
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                assert name not in ("build_labels", "persist_labels",
                                    "build_row", "build_rows", "persist_row")


class TestTemporalIntegrity:
    def test_clean_view_has_no_violations(self, stores, tmp_path):
        f, o = stores
        tdb, c = trading_db(tmp_path)
        specs = [spec()]
        persist_features(f, "d1", specs)
        add_completed(c, "d1")
        label_all(o, tdb, ["d1"])
        assert JV.verify_temporal(JV.build_view(f, o, specs)) == []

    def test_a_feature_available_after_the_decision_is_caught(self, stores):
        f, o = stores
        specs = [spec()]
        persist_features(f, "d1", specs)
        con = sqlite3.connect(f)
        con.execute("DROP TRIGGER trg_feature_row_inputs_no_update")
        con.execute("UPDATE feature_row_inputs SET available_ts='2026-09-01T23:00:00+00:00'")
        con.commit()
        con.close()
        v = JV.verify_temporal(JV.build_view(f, o, specs))
        assert v and "available after the decision" in v[0]["reason"]

    def test_an_outcome_before_the_decision_is_caught(self, stores, tmp_path):
        f, o = stores
        tdb, c = trading_db(tmp_path)
        specs = [spec()]
        persist_features(f, "d1", specs)
        add_completed(c, "d1")
        label_all(o, tdb, ["d1"])
        con = sqlite3.connect(o)
        con.execute("DROP TRIGGER trg_decision_outcomes_no_update")
        con.execute("UPDATE decision_outcomes SET entry_ts='2026-08-01T00:00:00+00:00'")
        con.commit()
        con.close()
        v = JV.verify_temporal(JV.build_view(f, o, specs))
        assert any("entry precedes the decision" in x["reason"] for x in v)

    def test_open_with_an_exit_is_caught(self, stores, tmp_path):
        f, o = stores
        tdb, c = trading_db(tmp_path)
        specs = [spec()]
        persist_features(f, "open", specs)
        add_open(c, "open", de="2001")
        label_all(o, tdb, ["open"])
        con = sqlite3.connect(o)
        con.execute("DROP TRIGGER trg_decision_outcomes_no_update")
        con.execute("UPDATE decision_outcomes SET exit_ts='2026-09-01T12:00:00+00:00'")
        con.commit()
        con.close()
        v = JV.verify_temporal(JV.build_view(f, o, specs))
        assert any("OPEN outcome carries an exit" in x["reason"] for x in v)

    def test_verification_needs_neither_source_store(self, stores, tmp_path):
        """It reads the joined rows, so it holds without the stores present."""
        f, o = stores
        tdb, c = trading_db(tmp_path)
        specs = [spec()]
        persist_features(f, "d1", specs)
        add_completed(c, "d1")
        label_all(o, tdb, ["d1"])
        rows = JV.build_view(f, o, specs)
        src = _strip_docstrings(
            (_RESEARCH / "decision_view" / "joined.py").read_text(encoding="utf-8"))
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef) and n.name == "verify_temporal")
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                assert name not in ("connect", "execute", "load_rows", "load_labels")
        assert JV.verify_temporal(rows) == []


class TestDeterminismAndScope:
    def test_repeated_construction_is_identical(self, stores, tmp_path):
        f, o = stores
        tdb, c = trading_db(tmp_path)
        specs = [spec()]
        for did in ("d1", "d2"):
            persist_features(f, did, specs)
        add_completed(c, "d1")
        add_open(c, "d2", de="2001")
        label_all(o, tdb, ["d1", "d2"])
        assert JV.build_view(f, o, specs) == JV.build_view(f, o, specs)

    def test_construction_writes_nothing(self, stores, tmp_path):
        import hashlib
        f, o = stores
        tdb, c = trading_db(tmp_path)
        specs = [spec()]
        persist_features(f, "d1", specs)
        add_completed(c, "d1")
        label_all(o, tdb, ["d1"])
        before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (f, o)]
        JV.build_view(f, o, specs)
        after = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (f, o)]
        assert before == after

    def test_no_write_statement_in_the_module(self):
        src = _strip_docstrings(
            (_RESEARCH / "decision_view" / "joined.py").read_text(encoding="utf-8"))
        for verb in ("INSERT ", "UPDATE ", "DELETE ", "CREATE ", "DROP "):
            assert verb not in src.upper()

    def test_it_is_not_promoted_to_training_data(self):
        """No export/fit surface, and every record says so."""
        src = _strip_docstrings(
            (_RESEARCH / "decision_view" / "joined.py").read_text(encoding="utf-8"))
        tree = ast.parse(src)
        names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        for banned in ("to_training_set", "to_xy", "fit", "train", "to_numpy",
                       "to_dataframe", "split"):
            assert banned not in names
        assert JV.RESEARCH_USE_ONLY

    def test_records_carry_the_research_only_marker(self, stores):
        f, o = stores
        specs = [spec()]
        persist_features(f, "d1", specs)
        rec = JV.to_records(JV.build_view(f, o, specs))[0]
        assert rec["research_use_only"] is True

    def test_records_are_audit_complete(self, stores, tmp_path):
        f, o = stores
        tdb, c = trading_db(tmp_path)
        specs = [spec()]
        persist_features(f, "d1", specs)
        add_completed(c, "d1")
        label_all(o, tdb, ["d1"])
        rec = JV.to_records(JV.build_view(f, o, specs))[0]
        for k in ("decision_id", "decision_ts", "outcome_status", "entry_ts",
                  "exit_ts", "realised_pnl_usd", "features"):
            assert k in rec
        feat = rec["features"]["f"]
        assert feat["contract_version"] and feat["contract_hash"]
        for k in ("input_name", "source", "state", "event_ts", "available_ts",
                  "age_s", "tolerance_s", "criticality", "provenance"):
            assert k in feat["inputs"][0]

    def test_coverage_reports_status_and_readiness(self, stores, tmp_path):
        f, o = stores
        tdb, c = trading_db(tmp_path)
        specs = [spec()]
        persist_features(f, "d1", specs)
        persist_features(f, "d2", specs)
        add_completed(c, "d1")
        add_open(c, "d2", de="2001")
        label_all(o, tdb, ["d1", "d2"])
        cov = JV.coverage(JV.build_view(f, o, specs))
        assert cov["decisions"] == 2
        assert cov["by_status"] == {OS.COMPLETED: 1, OS.OPEN: 1}
        assert cov["features"]["f"]["available"] == 2
        assert cov["features"]["f"]["contract_versions"] == [1]
        assert cov["research_use_only"]


def _strip_docstrings(src: str) -> str:
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and ast.get_docstring(node):
            node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def _env(var):
    v = os.environ.get(var)
    p = pathlib.Path(v) if v else None
    return p if p and p.exists() else None


_FEAT = _env("BTCBOT_FEATURE_DB")
_OUT = _env("BTCBOT_OUTCOME_DB")


@pytest.mark.skipif(_FEAT is None or _OUT is None,
                    reason="no persisted research stores")
class TestAgainstRealStores:
    def test_view_builds_over_the_real_decision_slice(self):
        rows = JV.build_view(_FEAT, _OUT)
        assert len(rows) == 8

    def test_statuses_match_the_label_store(self):
        cov = JV.coverage(JV.build_view(_FEAT, _OUT))
        assert cov["by_status"] == {OS.COMPLETED: 5, OS.NO_TRADE: 2, OS.OPEN: 1}

    def test_intended_versions_are_the_v2_starter_set(self):
        got = JV.intended_versions(_FEAT, FT.starter_features("g3_momentum_tsmom"))
        assert got["spot_price"][0] == 2
        assert got["option_atm_iv"][0] == 2
        assert got["signal_position"][0] == 1

    def test_every_feature_resolves_on_real_data(self):
        cov = JV.coverage(JV.build_view(_FEAT, _OUT))
        for name, m in cov["features"].items():
            assert m["available"] == m["of"], name

    def test_real_view_is_temporally_clean(self):
        assert JV.verify_temporal(JV.build_view(_FEAT, _OUT)) == []

    def test_real_view_is_deterministic(self):
        assert JV.build_view(_FEAT, _OUT) == JV.build_view(_FEAT, _OUT)
