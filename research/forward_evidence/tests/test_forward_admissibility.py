"""
Gate FB-8 — ForwardAdmissibilityRule.

The rule answers one question: is this L2 run admissible as pinnable evidence?
These tests exist mainly to prove it cannot be talked into saying yes. Every
incomplete, unpinned, mis-laned or unevaluable input must fail closed.
"""
from __future__ import annotations

import ast
import json
import pathlib
import sqlite3
import sys

import pytest

_HERE = pathlib.Path(__file__).resolve().parent
_FWD = _HERE.parent
_RESEARCH_ROOT = _FWD.parent
_EV_LIB = _RESEARCH_ROOT / "evidence_library"
if str(_RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_ROOT))

from forward_evidence import forward_admissibility as FA  # noqa: E402
from forward_evidence import observation_floors as OF  # noqa: E402

_SCHEMA = (_FWD / "schema_forward.sql").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers — build a real FloorReport via FB-7, then a payload via FB-8
# ---------------------------------------------------------------------------

def _prereg(root: pathlib.Path, floors: dict | None, name="p.json") -> pathlib.Path:
    d = root / "research" / "forward_evidence"
    d.mkdir(parents=True, exist_ok=True)
    doc: dict = {"signal_id": "fam_a", "version": "1.0.0"}
    if floors is not None:
        doc["observation_floors"] = floors
    p = d / name
    p.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return p


def _registry(root, prereg, hash_override=None):
    if prereg is None:
        return {"fam_a": {"state": "candidate", "preregistration": None}}
    return {"fam_a": {"state": "candidate", "preregistration": {
        "path": prereg.relative_to(root).as_posix(),
        "hash": hash_override or OF._sha256_lf(prereg)}}}


def _fwd_db(path, trades, run_dates, signal_id="fam_a"):
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    for rd in run_dates:
        conn.execute("INSERT INTO run_log (signal_id, run_date, fn_hash, status) "
                     "VALUES (?,?,?,'ok')", (signal_id, rd, "h"))
    for e, x, d in trades:
        cur = conn.execute(
            "INSERT INTO trade_entries (signal_id, entry_date, direction, "
            "entry_close_price, planned_exit_date, open) VALUES (?,?,?,?,?,0)",
            (signal_id, e, d, 100.0, x))
        conn.execute("INSERT INTO trade_exits (entry_id, exit_date, exit_close_price, "
                     "cost_bps) VALUES (?,?,?,?)", (cur.lastrowid, x, 101.0, 15.0))
    conn.commit(); conn.close()
    return path


def _metrics(report, provenance="live-forward", run_kind="forward", extra=None):
    m = {"n_trades": 3, "sharpe": 0.5,
         FA.FORWARD_ADMISSIBILITY_KEY: FA.build_payload(report, provenance, run_kind)}
    if extra:
        m[FA.FORWARD_ADMISSIBILITY_KEY].update(extra)
    return m


@pytest.fixture()
def passing(tmp_path):
    """A family whose every declared floor is MET."""
    root = tmp_path / "repo"
    pre = _prereg(root, {"minimum_trades": 3,
                         "minimum_calendar_months_before_assessment": 2,
                         "minimum_both_long_and_short_trades": True})
    db = _fwd_db(tmp_path / "f.db",
                 trades=[("2026-01-05", "2026-01-12", 1),
                         ("2026-01-20", "2026-01-27", -1),
                         ("2026-02-03", "2026-02-10", 1)],
                 run_dates=["2026-01-05", "2026-02-03", "2026-03-06"])
    rep = OF.evaluate("fam_a", _registry(root, pre), db, "2026-03-06", repo_root=root)
    assert rep.floors_satisfied, "fixture must start from a genuinely passing report"
    return {"root": root, "prereg": pre, "db": db, "report": rep}


RULE = FA.ForwardAdmissibilityRule()


# ---------------------------------------------------------------------------
# 1 / 2 / 3 — the floor outcomes
# ---------------------------------------------------------------------------

class TestFloorOutcomes:
    def test_all_floors_met_is_admissible(self, passing):
        ok, gates = RULE.evaluate({}, _metrics(passing["report"]), {})
        assert ok is True, [g for g in gates if not g["passed"]]
        assert all(g["passed"] for g in gates)
        assert {"FORWARD_LANE_IS_L2", "FORWARD_AS_OF_PRESENT",
                "PREREGISTRATION_PIN_VERIFIED"} <= {g["gate_code"] for g in gates}

    def test_not_met_floor_is_rejected(self, tmp_path):
        root = tmp_path / "r"
        pre = _prereg(root, {"minimum_trades": 99})
        db = _fwd_db(tmp_path / "f.db", [("2026-01-05", "2026-01-12", 1)], ["2026-01-05"])
        rep = OF.evaluate("fam_a", _registry(root, pre), db, "2026-06-01", repo_root=root)
        ok, gates = RULE.evaluate({}, _metrics(rep), {})
        assert ok is False
        bad = [g for g in gates if not g["passed"]]
        assert [g["gate_code"] for g in bad] == ["FLOOR_minimum_trades"]

    def test_unevaluable_floor_is_rejected(self, tmp_path):
        """A regime-conditioned floor cannot be computed and must block."""
        root = tmp_path / "r"
        pre = _prereg(root, {"minimum_trades": 1, "minimum_regimes_observed": 2})
        db = _fwd_db(tmp_path / "f.db", [("2026-01-05", "2026-01-12", 1)], ["2026-01-05"])
        rep = OF.evaluate("fam_a", _registry(root, pre), db, "2026-06-01", repo_root=root)
        ok, gates = RULE.evaluate({}, _metrics(rep), {})
        assert ok is False
        g = {x["gate_code"]: x for x in gates}
        assert g["FLOOR_minimum_trades"]["passed"] is True
        assert g["FLOOR_minimum_regimes_observed"]["passed"] is False


# ---------------------------------------------------------------------------
# 4 / 5 / 6 / 7 — fail-closed on provenance and lane
# ---------------------------------------------------------------------------

class TestFailsClosed:
    def test_missing_registry_pointer_is_rejected(self, tmp_path):
        root = tmp_path / "r"
        _prereg(root, {"minimum_trades": 1})
        db = _fwd_db(tmp_path / "f.db", [("2026-01-05", "2026-01-12", 1)], ["2026-01-05"])
        rep = OF.evaluate("fam_a", _registry(root, None), db, "2026-06-01", repo_root=root)
        ok, gates = RULE.evaluate({}, _metrics(rep), {})
        assert ok is False
        assert {x["gate_code"]: x for x in gates}["PREREGISTRATION_PIN_VERIFIED"]["passed"] is False

    def test_prereg_hash_mismatch_is_rejected(self, passing):
        doc = json.loads(passing["prereg"].read_text(encoding="utf-8"))
        doc["observation_floors"]["minimum_trades"] = 1          # weaken after pinning
        passing["prereg"].write_text(json.dumps(doc, indent=2), encoding="utf-8")
        rep = OF.evaluate("fam_a", _registry(passing["root"], passing["prereg"],
                                             hash_override="0" * 64),
                          passing["db"], "2026-03-06", repo_root=passing["root"])
        ok, gates = RULE.evaluate({}, _metrics(rep), {})
        assert ok is False
        assert {x["gate_code"]: x for x in gates}["PREREGISTRATION_PIN_VERIFIED"]["passed"] is False

    def test_missing_observation_floors_is_rejected(self, tmp_path):
        root = tmp_path / "r"
        pre = _prereg(root, None)
        db = _fwd_db(tmp_path / "f.db", [], ["2026-01-05"])
        rep = OF.evaluate("fam_a", _registry(root, pre), db, "2026-06-01", repo_root=root)
        ok, gates = RULE.evaluate({}, _metrics(rep), {})
        assert ok is False
        codes = {x["gate_code"] for x in gates if not x["passed"]}
        assert "OBSERVATION_FLOORS_DECLARED" in codes

    @pytest.mark.parametrize("prov,kind", [
        ("offline-corpus", "discovery"),
        ("offline-corpus", "confirmatory"),
        ("mainnet", "production"),
        ("live-forward", "discovery"),
    ])
    def test_non_l2_run_is_rejected(self, passing, prov, kind):
        ok, gates = RULE.evaluate({}, _metrics(passing["report"], prov, kind), {})
        assert ok is False
        assert {x["gate_code"]: x for x in gates}["FORWARD_LANE_IS_L2"]["passed"] is False

    def test_absent_payload_is_rejected(self):
        ok, gates = RULE.evaluate({}, {"n_trades": 99, "sharpe": 9.9}, {})
        assert ok is False and gates[0]["gate_code"] == "FORWARD_PAYLOAD_PRESENT"

    def test_unrecognised_payload_schema_is_rejected(self, passing):
        m = _metrics(passing["report"], extra={"schema": "forward-admissibility/99"})
        ok, gates = RULE.evaluate({}, m, {})
        assert ok is False and gates[0]["gate_code"] == "FORWARD_PAYLOAD_SCHEMA"

    def test_missing_as_of_is_rejected(self, passing):
        m = _metrics(passing["report"], extra={"as_of": None})
        ok, gates = RULE.evaluate({}, m, {})
        assert ok is False
        assert {x["gate_code"]: x for x in gates}["FORWARD_AS_OF_PRESENT"]["passed"] is False

    def test_empty_metrics_is_rejected(self):
        assert RULE.evaluate({}, {}, {})[0] is False
        assert RULE.evaluate({}, None, {})[0] is False


# ---------------------------------------------------------------------------
# 9 — replay determinism at a fixed as_of
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_payload_yields_identical_result(self, passing):
        m = _metrics(passing["report"])
        a = RULE.evaluate({}, m, {})
        b = RULE.evaluate({}, json.loads(json.dumps(m)), {})
        assert a == b

    def test_verdict_is_stable_when_the_corpus_grows(self, passing):
        """The payload is frozen in the run row; later trades must not change
        a historical verdict. This is why floors are passed in, not read."""
        frozen = json.loads(json.dumps(_metrics(passing["report"])))
        before = RULE.evaluate({}, frozen, {})
        conn = sqlite3.connect(passing["db"])
        for i in range(20):
            cur = conn.execute(
                "INSERT INTO trade_entries (signal_id, entry_date, direction, "
                "entry_close_price, planned_exit_date, open) "
                "VALUES ('fam_a','2026-05-01',-1,100.0,'2026-05-08',0)")
            conn.execute("INSERT INTO trade_exits (entry_id, exit_date, "
                         "exit_close_price, cost_bps) VALUES (?,'2026-05-08',90.0,15.0)",
                         (cur.lastrowid,))
        conn.commit(); conn.close()
        assert RULE.evaluate({}, frozen, {}) == before

    def test_payload_is_json_round_trippable(self, passing):
        p = FA.build_payload(passing["report"], "live-forward", "forward")
        assert json.loads(json.dumps(p)) == p


# ---------------------------------------------------------------------------
# 10 / 8 / 12 — protocol conformance, registration, and the existing rule
# ---------------------------------------------------------------------------

class TestProtocolAndRegistration:
    @pytest.fixture(autouse=True)
    def _needs_evidence_library(self):
        if not _EV_LIB.exists():
            pytest.skip("evidence_library not present (Dockerfile.research "
                        "excludes it by INV-1)")
        if str(_EV_LIB) not in sys.path:
            sys.path.insert(0, str(_EV_LIB))

    def test_conforms_to_the_protocol(self):
        import evidence_governance as EG
        assert isinstance(RULE, EG.AdmissibilityRule)

    def test_rule_id_is_distinct_from_candidate_gate(self):
        import evidence_governance as EG
        assert RULE.rule_id != EG.CandidateGateRule.rule_id
        assert RULE.engine_version != EG.CandidateGateRule.engine_version

    def test_registers_and_resolves(self):
        import evidence_governance as EG
        EG.register_rule(RULE)
        got = EG.get_rule(RULE.rule_id, RULE.engine_version)
        assert got.rule_id == RULE.rule_id

    def test_unknown_rule_pair_raises_rather_than_substituting(self):
        import evidence_governance as EG
        with pytest.raises(EG.UnknownRuleError):
            EG.get_rule("forward_evidence.observation_floors", "no-such-version")

    def test_failed_rule_cannot_produce_an_admission(self, tmp_path, passing):
        """admit_evidence must refuse to attest a verdict the rule did not give."""
        import evidence_governance as EG
        import record
        led = tmp_path / "ledger.db"
        EG.init_db(led)
        root = tmp_path / "r2"
        pre = _prereg(root, {"minimum_trades": 99}, name="p2.json")
        db = _fwd_db(tmp_path / "f2.db", [("2026-01-05", "2026-01-12", 1)], ["2026-01-05"])
        rep = OF.evaluate("fam_a", _registry(root, pre), db, "2026-06-01", repo_root=root)
        run_id = record.append_run(
            led, family_id="fam_a", metrics=_metrics(rep),
            provenance="live-forward", run_kind="forward",
            window_start="2026-01-05", window_end="2026-06-01")
        with pytest.raises(EG.EvidenceGovernanceError):
            EG.admit_evidence(led, run_id, {"role": ["feature"]}, {}, rule=RULE)

    def test_candidate_gate_rule_is_unchanged(self):
        """FB-8 must not alter what CandidateGateRule means."""
        import evidence_governance as EG
        import lifecycle
        assert EG.CandidateGateRule.rule_id == "lifecycle.check_candidate_gate"
        src = ast.unparse(ast.parse(
            (_EV_LIB / "lifecycle.py").read_text(encoding="utf-8")))
        assert "def check_candidate_gate" in src
        assert EG.DEFAULT_RULE.rule_id == EG.CandidateGateRule.rule_id
        assert hasattr(lifecycle, "check_candidate_gate")


# ---------------------------------------------------------------------------
# 11 — structural: no writes, no promotion, no collection dependency
# ---------------------------------------------------------------------------

class TestStructural:
    def test_module_writes_nothing_and_cannot_promote(self):
        src = pathlib.Path(FA.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        for n in ast.walk(tree):
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body \
                    and isinstance(n.body[0], ast.Expr) \
                    and isinstance(n.body[0].value, ast.Constant) \
                    and isinstance(n.body[0].value.value, str):
                n.body = n.body[1:] or [ast.Pass()]
        code = ast.unparse(tree)
        for banned in ("append_run", "append_transition", "append_gate_result",
                       "append_receipt", "INSERT", "UPDATE", "DELETE", "promote",
                       "sqlite3", "open(", "dsr_prob", "bh_p", "pairwise"):
            assert banned not in code, f"{banned} must not appear in executable code"

    def test_evaluate_reads_nothing_external(self):
        """No DB, no filesystem, no registry — Q3's requirement, structurally.

        Docstrings are stripped first: the module's own prose legitimately names
        `thresholds.yaml` when explaining what it does NOT enforce, and prose
        must not be able to fail (or satisfy) a structural check.
        """
        tree = ast.parse(pathlib.Path(FA.__file__).read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body                     and isinstance(n.body[0], ast.Expr)                     and isinstance(n.body[0].value, ast.Constant)                     and isinstance(n.body[0].value.value, str):
                n.body = n.body[1:] or [ast.Pass()]
        code = ast.unparse(tree)
        for banned in ("connect", "read_text", "read_bytes", "Path(", "yaml", "json.load"):
            assert banned not in code, f"evaluate() must not use {banned}"

    def test_not_imported_by_run_daily(self):
        rd = (_FWD / "run_daily.py").read_text(encoding="utf-8")
        assert "forward_admissibility" not in rd

    def test_does_not_import_evidence_library_at_module_level(self):
        """Keeps the forward package importable inside Dockerfile.research."""
        tree = ast.parse(pathlib.Path(FA.__file__).read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                mod = getattr(n, "module", None) or ""
                names = [a.name for a in n.names]
                assert "evidence_library" not in mod
                assert not any(x in ("record", "lifecycle", "evidence_governance")
                               for x in names + [mod])
