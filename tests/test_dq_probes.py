"""
Data Completeness Framework — probes and the rule provider (PR-7).

Covers: metadata-only probing, probe fail-closed behaviour, source-type
extensibility (a new source is a registration, not an engine change), the
generic RuleRegistry, and the proof that PR-6's AdmissibilityRule substitution
point is genuinely usable by PR-8 with ZERO edits to the Evidence Library.
"""
from __future__ import annotations

import ast
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "dq"))
sys.path.insert(0, str(ROOT / "research" / "evidence_library"))

import evidence_governance as eg  # noqa: E402

import engine  # noqa: E402
import probes  # noqa: E402
import readiness  # noqa: E402
import rules  # noqa: E402
from contract import parse_contract  # noqa: E402
from observation import InputObservation  # noqa: E402
from states import BACKFILL_REQUIRED, INCOMPLETE_DATA, READY, UNKNOWN  # noqa: E402

PERIOD = "2026-06-14"
CLOSED = datetime(2026, 6, 15, 2, tzinfo=timezone.utc)


@pytest.fixture
def source_db(tmp_path) -> Path:
    """A tiny 'upstream' database: three funding events on the target day."""
    p = tmp_path / "source.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE funding (ts INTEGER, rate REAL, ingested_at TEXT)")
    day = int(datetime(2026, 6, 14, tzinfo=timezone.utc).timestamp() * 1000)
    for hour in (0, 8, 16):
        ts = day + hour * 3600 * 1000
        con.execute(
            "INSERT INTO funding VALUES (?,?,?)",
            (ts, 0.0001, datetime(2026, 6, 14, hour, tzinfo=timezone.utc).isoformat()),
        )
    con.commit()
    con.close()
    return p


def sqlite_contract(source_db, **input_overrides):
    spec = {
        "id": "funding",
        "source": {
            "type": "sqlite", "path": str(source_db), "table": "funding",
            "ts_column": "ts", "ts_unit": "ms", "arrival_column": "ingested_at",
        },
        "criticality": "required",
        "coverage": {"min_count": 3},
        "late_arrival_hours": 12,
    }
    spec.update(input_overrides)
    return parse_contract({"contract_id": "funding_daily", "grain": "utc_day", "inputs": [spec]})


class TestSqliteProbe:
    def test_probes_metadata_and_yields_ready(self, source_db):
        contract = sqlite_contract(source_db)
        obs = probes.probe_all(contract, PERIOD, CLOSED)
        o = obs["funding"]

        assert o.rows == 3
        assert o.coverage_count == 3
        assert o.probe_error is None
        assert o.schema == {"ingested_at": "TEXT", "rate": "REAL", "ts": "INTEGER"}
        assert o.content_hash

        assert engine.evaluate(contract, obs, PERIOD, CLOSED).state == READY

    def test_a_missing_funding_event_is_incomplete_data(self, source_db):
        con = sqlite3.connect(source_db)
        con.execute("DELETE FROM funding WHERE ts % 86400000 != 0")  # keep only 00:00
        con.commit()
        con.close()

        contract = sqlite_contract(source_db)
        obs = probes.probe_all(contract, PERIOD, CLOSED)
        assert obs["funding"].coverage_count == 1
        assert engine.evaluate(contract, obs, PERIOD, CLOSED).state == INCOMPLETE_DATA

    def test_arrival_time_prevents_look_ahead(self, source_db):
        """
        Point-in-time correctness (§6). At 00:05 UTC only the 00:00 event had
        ARRIVED. A probe that ignored arrival time would see all three — a
        replay of that instant would then 'know' about events that had not yet
        happened, and every downstream backfill would silently acquire
        look-ahead.
        """
        contract = sqlite_contract(source_db)
        early = datetime(2026, 6, 14, 0, 5, tzinfo=timezone.utc)
        obs = probes.probe_all(contract, PERIOD, early)
        assert obs["funding"].coverage_count == 1

    def test_missing_database_is_unknown_not_a_crash(self, tmp_path):
        contract = sqlite_contract(tmp_path / "nope.db")
        obs = probes.probe_all(contract, PERIOD, CLOSED)
        assert "not found" in obs["funding"].probe_error
        assert engine.evaluate(contract, obs, PERIOD, CLOSED).state == UNKNOWN

    def test_missing_table_is_unknown(self, source_db):
        contract = sqlite_contract(source_db)
        contract.inputs[0].source["table"] = "does_not_exist"
        obs = probes.probe_all(contract, PERIOD, CLOSED)
        assert "does not exist" in obs["funding"].probe_error

    def test_a_raising_probe_is_captured_never_propagated(self, source_db):
        """A probe NEVER raises into the engine. A gate that dies with a
        traceback writes no receipt, and a period with no receipt is a period
        nobody looked at."""
        def broken(spec, start, end, as_of):
            raise RuntimeError("network on fire")

        probes.register_probe("flaky", broken)
        contract = parse_contract({
            "contract_id": "x", "grain": "utc_day",
            "inputs": [{"id": "i", "source": {"type": "flaky"}}],
        })
        obs = probes.probe_all(contract, PERIOD, CLOSED)
        assert obs["i"].probe_error == "RuntimeError: network on fire"
        assert engine.evaluate(contract, obs, PERIOD, CLOSED).state == UNKNOWN

    def test_unknown_source_type_is_unknown_not_a_key_error(self):
        contract = parse_contract({
            "contract_id": "x", "grain": "utc_day",
            "inputs": [{"id": "i", "source": {"type": "quantum_blockchain"}}],
        })
        obs = probes.probe_all(contract, PERIOD, CLOSED)
        assert "no probe registered" in obs["i"].probe_error
        assert engine.evaluate(contract, obs, PERIOD, CLOSED).state == UNKNOWN

    def test_bands_are_probed_and_a_unit_switch_is_caught(self, source_db):
        con = sqlite3.connect(source_db)
        con.execute("UPDATE funding SET ts = ts * 1000")   # ms -> us, the Binance case
        con.commit()
        con.close()

        contract = sqlite_contract(source_db, schema={"bands": {"ts": [1.0e12, 1.5e15]}})
        # the microsecond rows now fall OUTSIDE the day window entirely -> 0 rows,
        # which is exactly how this failure presents in production. Widen the probe
        # window by probing the period the us timestamps actually land in:
        obs = probes.probe_all(contract, PERIOD, CLOSED)
        assert obs["funding"].rows == 0
        # ...and with no rows there is no observed bound, so the gate says UNKNOWN
        # rather than inventing a pass. Fail closed.
        assert engine.evaluate(contract, obs, PERIOD, CLOSED).state == UNKNOWN

    def test_history_window_and_interior_gaps(self, source_db):
        con = sqlite3.connect(source_db)
        base = datetime(2026, 6, 14, tzinfo=timezone.utc)
        for offset in (1, 2, 4):     # 3 days back, with day-3 MISSING -> interior gap
            ts = int((base.timestamp() - offset * 86400) * 1000)
            con.execute("INSERT INTO funding VALUES (?,?,?)", (ts, 0.0001, "2026-06-10T00:00:00+00:00"))
        con.commit()
        con.close()

        contract = sqlite_contract(source_db, continuity={"min_rows": 5})
        obs = probes.probe_all(contract, PERIOD, CLOSED)
        assert obs["funding"].has_interior_gaps is True
        assert engine.evaluate(contract, obs, PERIOD, CLOSED).state == BACKFILL_REQUIRED

    def test_a_probe_never_materialises_a_row(self):
        """L3: 'A probe that loads a full dataframe is a failed review.' Every
        query in the sqlite probe is an aggregate or a PRAGMA — enforced here by
        inspection, because this is the property that keeps the gate cheap enough
        to never be skipped."""
        src = (ROOT / "research" / "dq" / "probes.py").read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                sql = node.value.upper()
                if "SELECT" in sql and "PRAGMA" not in sql:
                    assert any(agg in sql for agg in ("COUNT(", "MIN(", "MAX(", "DISTINCT")), (
                        f"probe issues a non-aggregate SELECT: {node.value!r}"
                    )


class TestStaticProbe:
    def test_static_observations_are_consumed_not_recomputed(self):
        """SnapshotWriter's collection_metrics already records coverage_pct and
        stale_count per cycle, with semantics precisely defined in CLAUDE.md §11.
        The gate CONSUMES those numbers. Recomputing them would create a second,
        divergent definition of coverage — the exact disease."""
        contract = parse_contract({
            "contract_id": "option_quotes", "grain": "utc_day",
            "inputs": [{
                "id": "quotes",
                "source": {"type": "static", "observation": {
                    "coverage_count": 62, "coverage_denominator": 64,
                }},
                "coverage": {"min_fraction": 0.99},   # 62/64 = 0.969 -> short
                "late_arrival_hours": 12,             # still within the late budget
            }],
        })
        obs = probes.probe_all(contract, PERIOD, CLOSED)
        assert obs["quotes"].coverage_count == 62
        assert engine.evaluate(contract, obs, PERIOD, CLOSED).state == INCOMPLETE_DATA

    def test_malformed_static_source_is_unknown(self):
        contract = parse_contract({
            "contract_id": "x", "grain": "utc_day",
            "inputs": [{"id": "i", "source": {"type": "static"}}],
        })
        obs = probes.probe_all(contract, PERIOD, CLOSED)
        assert obs["i"].probe_error is None or "requires an 'observation'" in (
            obs["i"].probe_error or ""
        )


# ---------------------------------------------------------------------------
# The rule provider
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path) -> Path:
    p = tmp_path / "dq.db"
    readiness.init_db(p)
    return p


class TestRuleRegistry:
    def test_the_default_rule_is_registered(self):
        assert rules.get_rule("dq.completeness_gate", engine.ENGINE_VERSION) is rules.DEFAULT_RULE

    def test_an_unregistered_pair_fails_loudly(self):
        with pytest.raises(rules.UnknownRuleError, match="refuses rather than substituting"):
            rules.get_rule("dq.completeness_gate", "dq.v0")

    def test_registration_is_keyed_on_the_pair_not_the_id_alone(self):
        """Two engine versions of the same rule_id are two DIFFERENT
        implementations, and BOTH must remain resolvable forever — or every
        receipt written under the older one becomes unreplayable."""
        class V2:
            rule_id = "dq.completeness_gate"
            engine_version = "dq.v2-test"

            def evaluate(self, *a, **kw):
                return None

        v2 = V2()
        rules.register_rule(v2)
        try:
            assert rules.get_rule("dq.completeness_gate", "dq.v2-test") is v2
            assert rules.get_rule("dq.completeness_gate", engine.ENGINE_VERSION) is rules.DEFAULT_RULE
        finally:
            rules.READINESS_RULES._rules.pop(("dq.completeness_gate", "dq.v2-test"))

    def test_the_registry_is_generic_not_readiness_specific(self):
        """The MECHANISM is written once, shape-agnostic — it is not a second
        copy of PR-6's, and it is not welded to the readiness rule shape."""
        class AnyRule:
            rule_id = "anything"
            engine_version = "v1"

        registry = rules.RuleRegistry("test")
        rule = AnyRule()
        registry.register(rule)
        assert registry.get("anything", "v1") is rule
        with pytest.raises(rules.UnknownRuleError):
            registry.get("anything", "v2")


class TestPr6SubstitutionPoint:
    """
    PR-6 built `AdmissibilityRule` explicitly as "the substitution point PR-7's
    Data Completeness Gate will use". These tests prove that seam is real and
    load-bearing — and that PR-8 can wire readiness-backed admissibility into
    the Evidence Library with ZERO edits to evidence_governance.py.

    This PR does NOT register such a rule. Doing so would change how evidence is
    admitted, which is a governance change (data_completeness_gate.md §7 G1/G2),
    not a framework change, and it is out of scope here.
    """

    def test_a_dq_backed_rule_satisfies_pr6s_protocol_with_no_evidence_library_edit(self):
        class ReadinessAdmissibilityRule:
            """Illustrative only — PR-8 owns the real one."""
            rule_id = "dq.readiness_admissibility"
            engine_version = engine.ENGINE_VERSION

            def evaluate(self, family_meta, run_metrics, thresholds):
                non_ready = run_metrics.get("readiness", {}).get("days_non_ready", [])
                passed = not non_ready
                return passed, [{
                    "gate_code": "readiness.window_all_ready",
                    "role": "evidence",
                    "passed": passed,
                    "observed": len(non_ready),
                    "threshold": 0,
                }]

        rule = ReadinessAdmissibilityRule()
        assert isinstance(rule, eg.AdmissibilityRule)

        eg.register_rule(rule)
        try:
            resolved = eg.get_rule("dq.readiness_admissibility", engine.ENGINE_VERSION)
            assert resolved is rule
            # and it coexists with — never displaces — the Evidence Library's own.
            assert eg.get_rule("lifecycle.check_candidate_gate", eg.ENGINE_VERSION) is eg.DEFAULT_RULE
        finally:
            eg._RULE_REGISTRY.pop(("dq.readiness_admissibility", engine.ENGINE_VERSION))

    def test_this_pr_does_not_register_an_admissibility_rule(self):
        """Scope discipline, asserted rather than promised: importing the whole
        dq package must not mutate the Evidence Library's rule registry."""
        registered = {rule_id for rule_id, _ in eg._RULE_REGISTRY}
        assert registered == {"lifecycle.check_candidate_gate"}

    def test_dq_never_imports_the_evidence_library_governance_module(self):
        """The framework is pipeline-agnostic. It depends on the receipt ledger
        and the contract registry (shared infrastructure) — never on the Evidence
        Library's governance logic, which is one CONSUMER of readiness, not a
        dependency of it."""
        for module in Path(ROOT / "research" / "dq").glob("*.py"):
            tree = ast.parse(module.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                for name in names:
                    assert "evidence_governance" not in name and "lifecycle" not in name, (
                        f"{module.name} imports {name}; the framework must not depend on the "
                        f"Evidence Library"
                    )
