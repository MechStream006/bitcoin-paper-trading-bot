"""Tests for decision-time feature-row persistence.

Synthetic fixtures with explicit instants, plus a real-corpus block gated on
the extracted production slices.
"""
from __future__ import annotations

import ast
import json
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
from feature_store import decision_store as DS  # noqa: E402

UTC = timezone.utc
S = FT.states
P = FT.policy
WRITTEN_AT = "2026-09-02T12:00:00+00:00"
BASE_TS = "2026-09-01T10:00:00.000000+00:00"
BASE_US = int(datetime(2026, 9, 1, 10, 0, tzinfo=UTC).timestamp() * 1_000_000)


def T(*a) -> datetime:
    return datetime(*a, tzinfo=UTC)


@pytest.fixture()
def db(tmp_path):
    p = tmp_path / "decision_features.db"
    DS.init_db(p)
    return p


def quote_load(rows):
    return AL.load_observations(
        rows, TB.get_spec("option_quotes"),
        payload_fields=("spot_price", "mark_iv", "best_bid", "best_ask"))


def one_quote(spot=100.0, ts=BASE_TS, iid=84, **extra):
    row = {"ts": ts, "exch_ts": BASE_US, "instrument_id": iid, "spot_price": spot}
    row.update(extra)
    return quote_load([row])


def spec(name="f", field_name="spot_price", tolerance_s=300.0,
         criticality=P.REQUIRED, compute=None, key=84):
    return FT.FeatureSpec(
        name=name,
        inputs=(FT.InputRequirement(
            name="x", source="option_quotes", field=field_name,
            tolerance_s=tolerance_s, criticality=criticality,
            key_field="instrument_id", key=key),),
        compute=compute or (lambda v: v["x"]),
        description="test feature")


def build(decision, specs, load):
    return FT.build_row(decision, specs, {"option_quotes": load})


class TestSchemaAndIsolation:
    def test_init_is_idempotent(self, tmp_path):
        p = tmp_path / "x.db"
        DS.init_db(p)
        DS.init_db(p)
        con = sqlite3.connect(p)
        names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
        assert {"feature_contracts", "feature_rows", "feature_row_inputs"} <= names

    def test_it_does_not_touch_the_governed_feature_store(self):
        """feature_store.db is governed; this module must not write to it."""
        src = _strip_docstrings(
            (_RESEARCH / "feature_store" / "decision_store.py").read_text(
                encoding="utf-8"))
        assert "feature_definitions" not in src
        assert "feature_specifications" not in src
        assert "register_definition" not in src
        assert "feature_store.db" not in src

    def test_default_db_is_a_separate_file(self):
        assert DS.DEFAULT_DB.name == "decision_features.db"

    def test_no_import_from_src(self):
        src = (_RESEARCH / "feature_store" / "decision_store.py").read_text(
            encoding="utf-8")
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                assert not (getattr(node, "module", "") or "").startswith("src")


class TestContractVersioning:
    def test_same_contract_registers_once(self, db):
        s = spec()
        assert DS.register_contract(db, s, WRITTEN_AT) == 1
        assert DS.register_contract(db, s, WRITTEN_AT) == 1

    def test_changed_tolerance_is_a_new_version(self, db):
        assert DS.register_contract(db, spec(tolerance_s=300.0), WRITTEN_AT) == 1
        assert DS.register_contract(db, spec(tolerance_s=60.0), WRITTEN_AT) == 2

    def test_changed_compute_body_is_a_new_version(self, db):
        assert DS.register_contract(db, spec(compute=lambda v: v["x"]), WRITTEN_AT) == 1
        v = DS.register_contract(db, spec(compute=lambda v: v["x"] * 2.0), WRITTEN_AT)
        assert v == 2, "different arithmetic must not share a version"

    def test_changed_criticality_is_a_new_version(self, db):
        assert DS.register_contract(db, spec(), WRITTEN_AT) == 1
        assert DS.register_contract(db, spec(criticality=P.OPTIONAL), WRITTEN_AT) == 2

    def test_changed_panel_key_is_a_new_version(self, db):
        assert DS.register_contract(db, spec(key=84), WRITTEN_AT) == 1
        assert DS.register_contract(db, spec(key=99), WRITTEN_AT) == 2

    def test_different_features_version_independently(self, db):
        assert DS.register_contract(db, spec(name="a"), WRITTEN_AT) == 1
        assert DS.register_contract(db, spec(name="b"), WRITTEN_AT) == 1

    def test_contract_body_is_retrievable(self, db):
        DS.register_contract(db, spec(), WRITTEN_AT)
        c = DS.get_contract(db, "f", 1)
        body = json.loads(c["contract_json"])
        assert body["feature_name"] == "f"
        assert body["inputs"][0]["tolerance_s"] == 300.0

    def test_a_registered_contract_cannot_be_updated(self, db):
        DS.register_contract(db, spec(), WRITTEN_AT)
        con = sqlite3.connect(db)
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            con.execute("UPDATE feature_contracts SET contract_hash='x'")
        con.close()


class TestPersistAndIdempotency:
    def test_persists_every_feature_of_a_row(self, db):
        specs = [spec("a"), spec("b", field_name="mark_iv")]
        row = build(T(2026, 9, 1, 10, 0, 30), specs, one_quote(mark_iv=0.5))
        res = DS.persist_row(db, row, specs, WRITTEN_AT)
        assert res.written == 2 and res.unchanged == 0
        assert len(DS.load_rows(db)) == 2

    def test_rewriting_the_identical_row_changes_nothing(self, db):
        specs = [spec()]
        row = build(T(2026, 9, 1, 10, 0, 30), specs, one_quote())
        DS.persist_row(db, row, specs, WRITTEN_AT)
        res = DS.persist_row(db, row, specs, WRITTEN_AT)
        assert res.written == 0 and res.unchanged == 1
        assert len(DS.load_rows(db)) == 1

    def test_a_later_written_at_is_not_a_change(self, db):
        specs = [spec()]
        row = build(T(2026, 9, 1, 10, 0, 30), specs, one_quote())
        DS.persist_row(db, row, specs, WRITTEN_AT)
        res = DS.persist_row(db, row, specs, "2027-01-01T00:00:00+00:00")
        assert res.unchanged == 1

    def test_persisting_is_deterministic(self, db, tmp_path):
        other = tmp_path / "b.db"
        DS.init_db(other)
        specs = [spec()]
        row = build(T(2026, 9, 1, 10, 0, 30), specs, one_quote())
        DS.persist_row(db, row, specs, WRITTEN_AT)
        DS.persist_row(other, row, specs, WRITTEN_AT)
        strip = lambda rs: [{k: v for k, v in r.items() if k != "written_at"} for r in rs]
        assert strip(DS.load_rows(db)) == strip(DS.load_rows(other))

    def test_decision_identity_defaults_to_the_instant(self, db):
        specs = [spec()]
        row = build(T(2026, 9, 1, 10, 0, 30), specs, one_quote())
        res = DS.persist_row(db, row, specs, WRITTEN_AT)
        assert res.decision_id == "2026-09-01T10:00:30+00:00"

    def test_supplied_decision_identity_is_used(self, db):
        specs = [spec()]
        row = build(T(2026, 9, 1, 10, 0, 30), specs, one_quote())
        DS.persist_row(db, row, specs, WRITTEN_AT, decision_id="coid-1")
        assert DS.load_rows(db)[0]["decision_id"] == "coid-1"

    def test_missing_spec_is_refused(self, db):
        specs = [spec("a")]
        row = build(T(2026, 9, 1, 10, 0, 30), specs, one_quote())
        with pytest.raises(DS.DecisionStoreError, match="no FeatureSpec"):
            DS.persist_row(db, row, [], WRITTEN_AT)


class TestDuplicatePreventionAndNoSilentOverwrite:
    def test_same_key_with_a_different_value_is_refused(self, db):
        specs = [spec()]
        a = build(T(2026, 9, 1, 10, 0, 30), specs, one_quote(spot=100.0))
        b = build(T(2026, 9, 1, 10, 0, 30), specs, one_quote(spot=999.0))
        DS.persist_row(db, a, specs, WRITTEN_AT, decision_id="d1")
        with pytest.raises(DS.DivergenceError, match="value_json"):
            DS.persist_row(db, b, specs, WRITTEN_AT, decision_id="d1")

    def test_the_original_value_survives_a_refused_write(self, db):
        specs = [spec()]
        a = build(T(2026, 9, 1, 10, 0, 30), specs, one_quote(spot=100.0))
        b = build(T(2026, 9, 1, 10, 0, 30), specs, one_quote(spot=999.0))
        DS.persist_row(db, a, specs, WRITTEN_AT, decision_id="d1")
        with pytest.raises(DS.DivergenceError):
            DS.persist_row(db, b, specs, WRITTEN_AT, decision_id="d1")
        assert DS.load_rows(db)[0]["value"] == 100.0

    def test_a_changed_definition_lands_on_a_new_version_beside_the_old(self, db):
        v1 = [spec(compute=lambda v: v["x"])]
        v2 = [spec(compute=lambda v: v["x"] * 2.0)]
        row1 = build(T(2026, 9, 1, 10, 0, 30), v1, one_quote(spot=100.0))
        row2 = build(T(2026, 9, 1, 10, 0, 30), v2, one_quote(spot=100.0))
        DS.persist_row(db, row1, v1, WRITTEN_AT, decision_id="d1")
        DS.persist_row(db, row2, v2, WRITTEN_AT, decision_id="d1")
        rows = DS.load_rows(db, decision_id="d1")
        assert {r["contract_version"] for r in rows} == {1, 2}
        assert {r["value"] for r in rows} == {100.0, 200.0}

    def test_history_cannot_be_updated_through_the_database(self, db):
        specs = [spec()]
        row = build(T(2026, 9, 1, 10, 0, 30), specs, one_quote())
        DS.persist_row(db, row, specs, WRITTEN_AT)
        con = sqlite3.connect(db)
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            con.execute("UPDATE feature_rows SET value_json='1'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            con.execute("UPDATE feature_row_inputs SET age_s=0")
        con.close()

    def test_no_update_statement_exists_in_the_module(self):
        src = _strip_docstrings(
            (_RESEARCH / "feature_store" / "decision_store.py").read_text(
                encoding="utf-8"))
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call) and getattr(
                    node.func, "attr", None) in ("execute", "executescript"):
                for arg in ast.walk(node):
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        body = arg.value.strip().upper()
                        assert not body.startswith("UPDATE")


class TestUnavailableStatesArePersisted:
    def test_incomplete_input_is_stored_not_dropped(self, db):
        specs = [spec()]
        row = build(T(2026, 9, 1, 9), specs, one_quote())   # before the observation
        DS.persist_row(db, row, specs, WRITTEN_AT)
        stored = DS.load_rows(db)
        assert len(stored) == 1
        assert stored[0]["state"] == S.INCOMPLETE_DATA
        assert stored[0]["value"] is None

    def test_stale_input_keeps_its_age_and_reason(self, db):
        specs = [spec(tolerance_s=300.0)]
        row = build(T(2026, 9, 1, 11), specs, one_quote())
        DS.persist_row(db, row, specs, WRITTEN_AT)
        i = DS.load_rows(db)[0]["inputs"][0]
        assert DS.load_rows(db)[0]["state"] == S.STALE_DATA
        assert i["age_s"] == 3600.0 and "exceeds tolerance" in i["detail"]

    def test_unknown_state_from_a_null_arrival_is_stored(self, db):
        load = quote_load([{"ts": None, "exch_ts": BASE_US, "instrument_id": 84,
                            "spot_price": 100.0}])
        specs = [spec()]
        row = build(T(2026, 9, 1, 12), specs, load)
        DS.persist_row(db, row, specs, WRITTEN_AT)
        stored = DS.load_rows(db)[0]
        assert stored["state"] == S.UNKNOWN and stored["action"] == P.BLOCK
        assert stored["inputs"][0]["available_ts"] is None

    def test_row_level_state_is_stored_alongside_the_feature_state(self, db):
        specs = [spec("ok"), spec("missing", field_name="mark_iv")]
        row = build(T(2026, 9, 1, 10, 0, 30), specs, one_quote())
        DS.persist_row(db, row, specs, WRITTEN_AT)
        stored = {r["feature_name"]: r for r in DS.load_rows(db)}
        assert stored["ok"]["state"] == S.READY
        assert stored["ok"]["row_state"] == S.INCOMPLETE_DATA


class TestProvenancePreservation:
    def test_every_declared_field_round_trips(self, db):
        specs = [spec()]
        row = build(T(2026, 9, 1, 10, 0, 30), specs, one_quote())
        DS.persist_row(db, row, specs, WRITTEN_AT)
        i = DS.load_rows(db)[0]["inputs"][0]
        assert i["source"] == "option_quotes"
        assert i["event_ts"] == "2026-09-01T10:00:00+00:00"
        assert i["available_ts"] == "2026-09-01T10:00:00+00:00"
        assert i["age_s"] == 30.0
        assert i["tolerance_s"] == 300.0
        assert i["criticality"] == P.REQUIRED
        assert i["provenance"] == {"instrument_id": 84}

    def test_multi_input_features_store_one_record_per_input(self, db):
        s = FT.FeatureSpec(
            name="spread",
            inputs=(FT.InputRequirement(name="bid", source="option_quotes",
                                        field="best_bid", tolerance_s=300.0,
                                        key_field="instrument_id", key=84),
                    FT.InputRequirement(name="ask", source="option_quotes",
                                        field="best_ask", tolerance_s=300.0,
                                        key_field="instrument_id", key=84)),
            compute=FT._spread_bps)
        row = build(T(2026, 9, 1, 10, 0, 5), [s],
                    one_quote(best_bid=99.0, best_ask=101.0))
        DS.persist_row(db, row, [s], WRITTEN_AT)
        stored = DS.load_rows(db)[0]
        assert {i["input_name"] for i in stored["inputs"]} == {"bid", "ask"}
        assert stored["value"] == pytest.approx(200.0)

    def test_contract_hash_is_stored_on_every_row(self, db):
        specs = [spec()]
        row = build(T(2026, 9, 1, 10, 0, 30), specs, one_quote())
        DS.persist_row(db, row, specs, WRITTEN_AT)
        assert DS.load_rows(db)[0]["contract_hash"] == DS.contract_hash(specs[0])

    def test_schema_version_is_recorded(self, db):
        specs = [spec()]
        row = build(T(2026, 9, 1, 10, 0, 30), specs, one_quote())
        DS.persist_row(db, row, specs, WRITTEN_AT)
        assert DS.load_rows(db)[0]["schema_version"] == DS.SCHEMA_VERSION


class TestLookAheadRevalidation:
    def test_clean_store_revalidates(self, db):
        specs = [spec()]
        row = build(T(2026, 9, 1, 10, 0, 30), specs, one_quote())
        DS.persist_row(db, row, specs, WRITTEN_AT)
        assert DS.revalidate_no_lookahead(db) == []

    def test_a_tampered_availability_is_caught(self, db):
        """Revalidation reads the store, so corruption there must surface."""
        specs = [spec()]
        row = build(T(2026, 9, 1, 10, 0, 30), specs, one_quote())
        DS.persist_row(db, row, specs, WRITTEN_AT)
        con = sqlite3.connect(db)
        con.execute("DROP TRIGGER trg_feature_row_inputs_no_update")
        con.execute("UPDATE feature_row_inputs SET available_ts = ?",
                    ("2026-09-01T11:00:00+00:00",))
        con.commit()
        con.close()
        v = DS.revalidate_no_lookahead(db)
        assert v and "is after" in v[0]["reason"]

    def test_revalidation_uses_the_shared_checker(self):
        src = (_RESEARCH / "feature_store" / "decision_store.py").read_text(
            encoding="utf-8")
        assert "from alignment.align import check_no_lookahead" in src
        assert "def check_no_lookahead" not in src, "must delegate, not reimplement"

    def test_revalidation_needs_no_source_data(self, db):
        """Stored rows alone must answer the question, months later."""
        specs = [spec()]
        row = build(T(2026, 9, 1, 10, 0, 30), specs, one_quote())
        DS.persist_row(db, row, specs, WRITTEN_AT)
        src = _strip_docstrings(
            (_RESEARCH / "feature_store" / "decision_store.py").read_text(
                encoding="utf-8"))
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "revalidate_no_lookahead")
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                label = getattr(node.func, "attr", None) or getattr(
                    node.func, "id", None)
                assert label not in ("build_row", "build_rows", "load_lake_source",
                                     "load_sqlite_source", "now", "utcnow")
        assert DS.revalidate_no_lookahead(db) == []

    def test_unavailable_inputs_do_not_produce_false_violations(self, db):
        specs = [spec()]
        row = build(T(2026, 9, 1, 9), specs, one_quote())
        DS.persist_row(db, row, specs, WRITTEN_AT)
        assert DS.revalidate_no_lookahead(db) == []


class TestCoverage:
    def test_reports_states_per_feature_and_version(self, db):
        specs = [spec()]
        DS.persist_row(db, build(T(2026, 9, 1, 10, 0, 30), specs, one_quote()),
                       specs, WRITTEN_AT, decision_id="d1")
        DS.persist_row(db, build(T(2026, 9, 1, 9), specs, one_quote()),
                       specs, WRITTEN_AT, decision_id="d2")
        cov = DS.coverage(db)
        assert cov["decisions"] == 2 and cov["feature_rows"] == 2
        assert cov["by_feature"]["f@v1"] == {S.READY: 1, S.INCOMPLETE_DATA: 1}
        assert cov["contracts"][0]["contract_version"] == 1


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
_OQ = _env_db("BTCBOT_OQ_SLICE")
_FWD = _env_db("BTCBOT_FWD_SLICE")


@pytest.mark.skipif(_EXEC is None or _OQ is None or _FWD is None,
                    reason="production slices not available")
class TestAgainstRealCorpus:
    _cache = None

    def _built(self):
        if TestAgainstRealCorpus._cache is None:
            TestAgainstRealCorpus._cache = self._build()
        return TestAgainstRealCorpus._cache

    def _build(self):
        def load(dbp, src, pf):
            sp = TB.get_spec(src)
            con = sqlite3.connect(
                pathlib.Path(dbp).resolve().as_uri() + "?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            cols = set(pf) | {sp.event_field} | set(sp.provenance_fields)
            if sp.availability_field:
                cols.add(sp.availability_field)
            rows = con.execute(
                f"SELECT {', '.join(sorted(cols))} FROM {sp.table}").fetchall()
            con.close()
            return AL.load_observations(rows, sp, pf)

        orders = AL.load_sqlite_source(
            _EXEC, "orders", payload_fields=("client_order_id", "strategy"))
        auto = [o for o in orders
                if str(o.provenance.get("strategy")) == "g3_momentum_tsmom"
                and str(o.provenance.get("client_order_id", "")).startswith("g3")]
        ids = {o.available_ts.isoformat(): str(o.provenance["client_order_id"])
               for o in auto}
        lake = AL.load_lake_source("spot_1m", payload_fields=("close",))
        loads = {
            "option_quotes": load(_OQ, "option_quotes",
                                  ("spot_price", "mark_iv", "oi_contracts",
                                   "best_bid", "best_ask")),
            "signal_log": load(_FWD, "signal_log", ("position",)),
            "spot_1m": AL.SourceLoad("spot_1m", tuple(lake), len(lake), 0),
        }
        specs = FT.starter_features(197889, "g3_momentum_tsmom")
        rows = FT.build_rows([o.available_ts for o in auto], specs, loads)
        return rows, specs, ids

    def test_persists_the_full_decision_slice(self, db):
        rows, specs, ids = self._built()
        res = DS.persist_rows(db, rows, specs, WRITTEN_AT, decision_ids=ids)
        assert sum(r.written for r in res) == len(rows) * len(specs)
        assert DS.coverage(db)["decisions"] == len(rows)

    def test_real_write_is_idempotent(self, db):
        rows, specs, ids = self._built()
        DS.persist_rows(db, rows, specs, WRITTEN_AT, decision_ids=ids)
        again = DS.persist_rows(db, rows, specs, "2027-01-01T00:00:00+00:00",
                                decision_ids=ids)
        assert sum(r.written for r in again) == 0

    def test_unavailable_states_survive_on_real_data(self, db):
        rows, specs, ids = self._built()
        DS.persist_rows(db, rows, specs, WRITTEN_AT, decision_ids=ids)
        states_seen = set()
        for r in DS.load_rows(db):
            states_seen.add(r["state"])
        assert S.READY in states_seen
        assert S.INCOMPLETE_DATA in states_seen, "absence must be persisted"

    def test_real_rows_revalidate_clean(self, db):
        rows, specs, ids = self._built()
        DS.persist_rows(db, rows, specs, WRITTEN_AT, decision_ids=ids)
        assert DS.revalidate_no_lookahead(db) == []

    def test_lake_provenance_survives_persistence(self, db):
        rows, specs, ids = self._built()
        DS.persist_rows(db, rows, specs, WRITTEN_AT, decision_ids=ids)
        mk = [r for r in DS.load_rows(db) if r["feature_name"] == "market_close_1m"]
        assert mk
        assert all(r["inputs"][0]["provenance"].get("parquet_sha256") for r in mk)

    def test_decision_ids_are_the_real_client_order_ids(self, db):
        rows, specs, ids = self._built()
        DS.persist_rows(db, rows, specs, WRITTEN_AT, decision_ids=ids)
        got = {r["decision_id"] for r in DS.load_rows(db)}
        assert any(d.startswith("g3momentumtsmo") for d in got)
