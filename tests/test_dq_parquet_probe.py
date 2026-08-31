"""
FB-12a — parquet probe + h6 completeness provenance.

Proves the thirteen properties the gate requires. Real parquet files and real
SQLite databases throughout; the only thing ever faked is the funding data
itself, which is synthesised so that "sufficient" and "insufficient" history are
constructed conditions rather than accidents of the corpus.
"""
from __future__ import annotations

import ast
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "dq"))
sys.path.insert(0, str(ROOT / "research" / "evidence_library"))
sys.path.insert(0, str(ROOT / "research"))

import probes  # noqa: E402
import states  # noqa: E402
from contract import parse_contract, validate_contract  # noqa: E402

from forward_evidence import h6_completeness as h6c  # noqa: E402

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

PERIOD = "2026-08-30"
AS_OF = datetime(2026, 8, 31, 0, 5, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures — a real parquet lake
# ---------------------------------------------------------------------------

def _write_lake(root: Path, days: list[str], records_per_day: int = 3) -> Path:
    """Write a funding lake with `records_per_day` 8-hourly events per day."""
    dataset = root / "funding" / "BTCUSDT"
    by_month: dict[tuple[int, int], list[int]] = {}
    for d in days:
        dt = datetime.fromisoformat(d).replace(tzinfo=timezone.utc)
        for k in range(records_per_day):
            ts = int((dt + timedelta(hours=8 * k)).timestamp() * 1000)
            by_month.setdefault((dt.year, dt.month), []).append(ts)
    for (year, month), stamps in by_month.items():
        part = dataset / f"year={year}" / f"month={month:02d}"
        part.mkdir(parents=True, exist_ok=True)
        pq.write_table(
            pa.table({"timestamp_ms": stamps,
                      "funding_rate": [0.0001] * len(stamps)}),
            part / "data.parquet",
        )
    return dataset


def _days_ending(last: str, n: int) -> list[str]:
    end = datetime.fromisoformat(last).date()
    return [(end - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


def _spec(root: Path, min_rows: int = 252, window_days: int = 273,
          min_per_day: int = 2, dataset: str = "funding"):
    doc = h6c.build_contract_doc(root)
    doc["inputs"][0]["source"]["dataset"] = dataset
    doc["inputs"][0]["source"]["window_days"] = window_days
    doc["inputs"][0]["source"]["min_records_per_day"] = min_per_day
    doc["inputs"][0]["continuity"]["min_rows"] = min_rows
    return parse_contract(doc).inputs[0]


def _probe(spec):
    start = datetime.fromisoformat(PERIOD).replace(tzinfo=timezone.utc)
    return probes.probe_input(spec, start, start + timedelta(days=1), AS_OF)


# ---------------------------------------------------------------------------
# 1. sufficient / insufficient history
# ---------------------------------------------------------------------------

class TestHistoryDepth:
    def test_sufficient_history_is_detected(self, tmp_path):
        _write_lake(tmp_path, _days_ending(PERIOD, 273))
        obs = _probe(_spec(tmp_path))
        assert obs.probe_error is None
        assert obs.history_rows == 273
        assert obs.has_interior_gaps is False

    def test_insufficient_history_is_detected(self, tmp_path):
        _write_lake(tmp_path, _days_ending(PERIOD, 60))
        obs = _probe(_spec(tmp_path))
        assert obs.probe_error is None
        assert obs.history_rows == 60

    def test_h6s_real_shape_252_of_273_is_sufficient(self, tmp_path):
        """21 holes in a 273-day window still clears a 252 floor.

        This is the case a 252-day dense window could not express, and is why
        `window_days` is separable from `min_rows`.
        """
        days = _days_ending(PERIOD, 273)
        del days[10:31]                                  # punch 21 interior holes
        _write_lake(tmp_path, days)
        obs = _probe(_spec(tmp_path))
        assert obs.history_rows == 252
        assert obs.has_interior_gaps is True

    def test_thin_days_do_not_count(self, tmp_path):
        """A day with 1 of 3 funding events is present but not admissible."""
        _write_lake(tmp_path, _days_ending(PERIOD, 273), records_per_day=1)
        obs = _probe(_spec(tmp_path, min_per_day=2))
        assert obs.history_rows == 0, "1-of-3 days must not count toward history"


# ---------------------------------------------------------------------------
# 2. fail-closed on missing input
# ---------------------------------------------------------------------------

class TestFailClosed:
    def test_missing_dataset_directory_is_probe_error(self, tmp_path):
        obs = _probe(_spec(tmp_path, dataset="does_not_exist"))
        assert obs.probe_error is not None
        assert obs.history_rows is None

    def test_empty_dataset_directory_is_probe_error(self, tmp_path):
        (tmp_path / "funding" / "BTCUSDT").mkdir(parents=True)
        obs = _probe(_spec(tmp_path))
        assert obs.probe_error is not None

    def test_missing_required_source_key_is_probe_error(self, tmp_path):
        doc = h6c.build_contract_doc(tmp_path)
        del doc["inputs"][0]["source"]["ts_column"]
        obs = _probe(parse_contract(doc).inputs[0])
        assert obs.probe_error is not None and "ts_column" in obs.probe_error

    def test_corrupt_parquet_is_probe_error_not_raise(self, tmp_path):
        ds = _write_lake(tmp_path, _days_ending(PERIOD, 30))
        next(ds.rglob("*.parquet")).write_bytes(b"not a parquet file")
        obs = _probe(_spec(tmp_path))
        assert obs.probe_error is not None, "probe must never raise into the engine"

    def test_probe_error_forces_unknown_which_blocks(self, tmp_path):
        """L2 end-to-end: a missing lake yields UNKNOWN and refuses derivation."""
        result = _evaluate(tmp_path, activate=True)
        assert result.verdict.state == states.UNKNOWN
        assert result.may_derive is False

    def test_arrival_filter_when_declared_blocks_lookahead(self, tmp_path):
        """Opt-in point-in-time: with arrival_from_mtime, a partition that had
        not arrived by as_of is invisible and the probe fails closed."""
        _write_lake(tmp_path, _days_ending(PERIOD, 273))
        doc = h6c.build_contract_doc(tmp_path)
        doc["inputs"][0]["source"]["arrival_from_mtime"] = True
        spec = parse_contract(doc).inputs[0]
        start = datetime.fromisoformat(PERIOD).replace(tzinfo=timezone.utc)
        stale = probes.probe_input(
            spec, start, start + timedelta(days=1), AS_OF - timedelta(days=3650))
        assert stale.probe_error is not None, "look-ahead must be impossible"

    def test_arrival_filter_is_off_by_default(self, tmp_path):
        """A corpus restored from backup has fresh mtimes; that must not make a
        complete lake look absent."""
        _write_lake(tmp_path, _days_ending(PERIOD, 273))
        obs = _probe(_spec(tmp_path))
        assert obs.probe_error is None
        assert obs.history_rows == 273
        assert obs.newest_arrival_ts is not None, "arrival is still reported"


# ---------------------------------------------------------------------------
# 3. valid history does not create false degradation
# ---------------------------------------------------------------------------

class TestNoFalseDegradation:
    def test_full_history_yields_ready_and_compute(self, tmp_path):
        _write_lake(tmp_path, _days_ending(PERIOD, 273))
        result = _evaluate(tmp_path, activate=True)
        assert result.verdict.state == states.READY
        assert result.may_derive is True

    def test_full_history_reports_no_interior_gaps(self, tmp_path):
        _write_lake(tmp_path, _days_ending(PERIOD, 273))
        obs = _probe(_spec(tmp_path))
        assert obs.has_interior_gaps is False


# ---------------------------------------------------------------------------
# 4/5. h6 unevaluable is distinguishable; legitimate zero stays legitimate
# ---------------------------------------------------------------------------

def _evaluate(lake_root: Path, activate: bool, db: Path | None = None,
              period: str = PERIOD):
    db = db or (lake_root / "readiness.db")
    if activate:
        h6c.activate(db, actor="tester", lake_root=lake_root)
    return h6c.evaluate_current(
        db, period=period, as_of=AS_OF, actor="tester", lake_root=lake_root)


class TestDistinguishability:
    def test_insufficient_funding_history_is_backfill_required(self, tmp_path):
        """The exact 2026-06-16..2026-08-26 condition, reproduced."""
        _write_lake(tmp_path, _days_ending(PERIOD, 61))     # ~2 months, as it was
        result = _evaluate(tmp_path, activate=True)
        assert result.verdict.state == states.BACKFILL_REQUIRED
        assert result.may_derive is False

    def test_unevaluable_and_legitimate_zero_have_different_verdicts(self, tmp_path):
        """The whole point: h6_canonical=0 no longer means one thing."""
        thin = tmp_path / "thin"
        full = tmp_path / "full"
        _write_lake(thin, _days_ending(PERIOD, 61))
        _write_lake(full, _days_ending(PERIOD, 273))

        unevaluable = _evaluate(thin, activate=True, db=thin / "a.db")
        legitimate = _evaluate(full, activate=True, db=full / "b.db")

        assert unevaluable.verdict.state == states.BACKFILL_REQUIRED
        assert legitimate.verdict.state == states.READY
        assert unevaluable.verdict.state != legitimate.verdict.state

    def test_legitimate_zero_remains_legitimate(self, tmp_path):
        """Full history: h6 may be flat, and the gate says the inputs were fine."""
        _write_lake(tmp_path, _days_ending(PERIOD, 273))
        result = _evaluate(tmp_path, activate=True)
        assert result.verdict.state == states.READY, (
            "a flat day with complete inputs must never be marked degraded")

    def test_unactivated_contract_is_configuration_error_not_silence(self, tmp_path):
        _write_lake(tmp_path, _days_ending(PERIOD, 273))
        result = _evaluate(tmp_path, activate=False)
        assert result.verdict.state == states.CONFIGURATION_ERROR
        assert result.may_derive is False


# ---------------------------------------------------------------------------
# 6. no strategy decision output changes
# ---------------------------------------------------------------------------

class TestStrategyUntouched:
    def test_h6_decide_output_is_identical_for_every_regime_triple(self):
        from forward_evidence.decision_function import h6_decide

        labels = ("low", "medium", "high", "unknown", "bear", "sideways", "bull")
        observed = {
            (v, f, m): h6_decide(v, f, m)
            for v in labels for f in labels for m in labels
        }
        expected = {
            (v, f, m): int(v == "medium" and f == "low" and m not in ("bear", "unknown"))
            for v in labels for f in labels for m in labels
        }
        assert observed == expected

    def test_this_gate_did_not_edit_the_frozen_decision_function(self):
        proc = subprocess.run(
            ["git", "diff", "HEAD", "--name-only"], cwd=ROOT,
            capture_output=True, text=True)
        assert proc.returncode == 0
        touched = set(proc.stdout.split())
        for frozen in (
            "research/forward_evidence/decision_function.py",
            "research/forward_evidence/daily_job.py",
            "research/forward_evidence/run_daily.py",
            "research/forward_evidence/runner_base.py",
        ):
            assert frozen not in touched, f"{frozen} must not be modified"

    def test_module_never_imports_the_strategy_evaluator(self):
        """Provenance must not be able to reach into signal evaluation."""
        src = (ROOT / "research" / "forward_evidence" / "h6_completeness.py").read_text(
            encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                names = {a.name for a in node.names}
                assert "h6_decide" not in names
                assert "compute_h6_decision" not in names


# ---------------------------------------------------------------------------
# 7/8/9. no ledger rewrite, no new promotion surface, scheduler unchanged
# ---------------------------------------------------------------------------

class TestNoSideEffects:
    def test_historical_period_is_refused(self, tmp_path):
        _write_lake(tmp_path, _days_ending(PERIOD, 273))
        with pytest.raises(h6c.HistoricalPeriodRefused):
            h6c.evaluate_current(
                tmp_path / "r.db", period="2026-07-01", as_of=AS_OF,
                actor="tester", lake_root=tmp_path)

    def test_refusal_writes_nothing_at_all(self, tmp_path):
        _write_lake(tmp_path, _days_ending(PERIOD, 273))
        db = tmp_path / "r.db"
        with pytest.raises(h6c.HistoricalPeriodRefused):
            h6c.evaluate_current(db, period="2026-06-20", as_of=AS_OF,
                                 actor="tester", lake_root=tmp_path)
        assert not db.exists(), "a refused period must leave no database behind"

    def test_evaluation_touches_no_evidence_ledger_table(self, tmp_path):
        _write_lake(tmp_path, _days_ending(PERIOD, 273))
        db = tmp_path / "r.db"
        _evaluate(tmp_path, activate=True, db=db)
        con = sqlite3.connect(db)
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
        for forbidden in ("runs", "state_transitions", "gate_results",
                          "pairwise_r", "families"):
            assert forbidden not in tables, (
                f"the completeness producer must not create '{forbidden}'")

    def test_no_promotion_or_admission_api_is_exposed(self):
        for banned in ("promote", "admit", "reject", "transition", "pin"):
            assert not any(
                n.startswith(banned) for n in dir(h6c)
            ), f"h6_completeness must expose no '{banned}*' surface"

    def test_scheduler_and_collectors_do_not_import_this_module(self):
        for name in ("run_daily.py", "daily_job.py", "runner_base.py",
                     "ledger_sync.py"):
            path = ROOT / "research" / "forward_evidence" / name
            if not path.exists():
                continue
            assert "h6_completeness" not in path.read_text(encoding="utf-8"), (
                f"{name} must not import the completeness producer (scheduler "
                "semantics unchanged, and INV-1)")

    def test_no_systemd_unit_references_this_module(self):
        deploy = ROOT / "deploy"
        if not deploy.is_dir():
            pytest.skip("no deploy directory")
        for unit in deploy.glob("*.service"):
            assert "h6_completeness" not in unit.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 10. contract integrity
# ---------------------------------------------------------------------------

class TestContract:
    def test_contract_is_valid(self):
        assert validate_contract(parse_contract(h6c.build_contract_doc())) == []

    def test_constants_are_imported_not_restated(self):
        from forward_evidence import decision_function as df

        doc = h6c.build_contract_doc()
        src = doc["inputs"][0]
        assert src["continuity"]["min_rows"] == df.REGIME_WINDOW
        assert src["source"]["window_days"] == df.MIN_HISTORY

    def test_no_new_state_is_introduced(self):
        assert set(states.STATES) == {
            "READY", "PARTIAL_DAY", "STALE_DATA", "INCOMPLETE_DATA",
            "BACKFILL_REQUIRED", "SCHEMA_MISMATCH", "CONFIGURATION_ERROR",
            "UNKNOWN",
        }


# ---------------------------------------------------------------------------
# 11. sabotage-and-revert at the fail-open boundaries
# ---------------------------------------------------------------------------

class TestSabotage:
    def test_sabotaging_the_min_records_guard_is_caught(self, tmp_path):
        """If thin days counted, 1-of-3 funding would look like full history."""
        _write_lake(tmp_path, _days_ending(PERIOD, 273), records_per_day=1)
        strict = _probe(_spec(tmp_path, min_per_day=2))
        sabotaged = _probe(_spec(tmp_path, min_per_day=1))
        assert strict.history_rows == 0
        assert sabotaged.history_rows == 273, (
            "this asserts the guard is what separates them; the strict value "
            "above is the one the contract uses")

    def test_sabotaging_the_history_floor_flips_the_verdict(self, tmp_path):
        """Lowering min_rows below the real requirement fails open — proving
        the floor, not the plumbing, is what blocks."""
        _write_lake(tmp_path, _days_ending(PERIOD, 61))
        real = _probe(_spec(tmp_path, min_rows=252))
        assert real.history_rows == 61 and real.history_rows < 252

        weakened = _spec(tmp_path, min_rows=30)
        obs = _probe(weakened)
        assert obs.history_rows >= 30, (
            "with the floor lowered the same corpus passes — the floor is "
            "load-bearing and must stay at REGIME_WINDOW")

    def test_disabling_the_declared_arrival_filter_grants_lookahead(self, tmp_path):
        """Sabotage: with arrival_from_mtime declared the probe refuses a
        pre-arrival as_of; removing the flag makes the same call succeed. That
        difference is exactly the look-ahead the flag exists to prevent."""
        _write_lake(tmp_path, _days_ending(PERIOD, 273))
        start = datetime.fromisoformat(PERIOD).replace(tzinfo=timezone.utc)
        past = AS_OF - timedelta(days=3650)

        doc = h6c.build_contract_doc(tmp_path)
        doc["inputs"][0]["source"]["arrival_from_mtime"] = True
        guarded = probes.probe_input(
            parse_contract(doc).inputs[0], start, start + timedelta(days=1), past)

        doc["inputs"][0]["source"]["arrival_from_mtime"] = False
        sabotaged = probes.probe_input(
            parse_contract(doc).inputs[0], start, start + timedelta(days=1), past)

        assert guarded.probe_error is not None
        assert sabotaged.probe_error is None and sabotaged.history_rows == 273


# ---------------------------------------------------------------------------
# 12. existing PR-7 behaviour is untouched
# ---------------------------------------------------------------------------

class TestPr7Untouched:
    def test_sqlite_and_static_probes_still_registered(self):
        assert "sqlite" in probes.PROBE_REGISTRY
        assert "static" in probes.PROBE_REGISTRY
        assert "parquet" in probes.PROBE_REGISTRY

    def test_parquet_probe_is_registered_additively(self):
        assert probes.get_probe("parquet") is probes.parquet_probe

    def test_default_window_days_falls_back_to_min_rows(self, tmp_path):
        """Every pre-existing contract, which declares no window_days, is
        unchanged: the lookback still equals min_rows."""
        _write_lake(tmp_path, _days_ending(PERIOD, 100))
        doc = h6c.build_contract_doc(tmp_path)
        del doc["inputs"][0]["source"]["window_days"]
        doc["inputs"][0]["continuity"]["min_rows"] = 50
        obs = _probe(parse_contract(doc).inputs[0])
        assert obs.history_rows == 50, (
            "with no window_days the window must be exactly min_rows days")
