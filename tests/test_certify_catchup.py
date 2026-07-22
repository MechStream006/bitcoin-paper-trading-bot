"""certify_catchup.py — the daily certifier's scheduling wrapper.

The wrapper owns exactly one decision: WHICH days to ask certify_day.py about.
These tests pin that decision, plus the two properties the systemd unit depends
on (idempotency, and treating AMBER/RED as successful runs).

Deliberately NOT tested here: certification logic, thresholds, or verdicts.
Those belong to certify_day.py and tests/test_certify_day.py, and this ticket
changed neither.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

_spec = importlib.util.spec_from_file_location(
    "certify_catchup", ROOT / "scripts" / "certify_catchup.py"
)
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)

REF = date(2026, 7, 18)  # stands in for "yesterday UTC"


def _db(days_with_data=(), days_certified=()) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE collection_metrics (ts TEXT PRIMARY KEY, universe_size INTEGER,"
        " coverage_pct REAL, wrote INTEGER, skipped INTEGER, failed INTEGER,"
        " reconcile_count INTEGER, stale_count INTEGER)"
    )
    conn.execute(
        "CREATE TABLE daily_certifier_log (day TEXT PRIMARY KEY, run_ts TEXT NOT NULL,"
        " verdict TEXT NOT NULL, platform TEXT, certifier_version TEXT,"
        " checks_json TEXT NOT NULL)"
    )
    for d in days_with_data:
        conn.execute(
            "INSERT INTO collection_metrics VALUES (?,66,1.0,66,0,0,0,0)",
            (f"{d}T00:00:00+00:00",),
        )
    for d in days_certified:
        conn.execute(
            "INSERT INTO daily_certifier_log VALUES (?,?,?,?,?,?)",
            (d, f"{d}T01:30:00Z", "GREEN", "testnet", "x", "{}"),
        )
    conn.commit()
    return conn


class TestWindow:
    def test_window_includes_the_reference_day(self):
        """Regression: an off-by-one here silently never certifies yesterday --
        the one day the timer exists to certify. The first implementation used
        range(lookback, 0, -1) and produced yesterday-7 .. yesterday-1."""
        conn = _db(days_with_data=[REF.isoformat()])
        to_certify, _, _ = cc.plan(conn, 7, REF)
        assert REF.isoformat() in to_certify

    def test_window_length_and_bounds(self):
        days = [f"2026-07-{d:02d}" for d in range(12, 19)]  # 12th..18th inclusive
        conn = _db(days_with_data=days)
        to_certify, _, _ = cc.plan(conn, 7, REF)
        assert to_certify == days
        assert len(to_certify) == 7

    def test_oldest_first(self):
        days = [f"2026-07-{d:02d}" for d in range(16, 19)]
        conn = _db(days_with_data=days)
        to_certify, _, _ = cc.plan(conn, 7, REF)
        assert to_certify == sorted(to_certify)

    def test_lookback_of_one_is_just_yesterday(self):
        conn = _db(days_with_data=["2026-07-17", "2026-07-18"])
        to_certify, _, _ = cc.plan(conn, 1, REF)
        assert to_certify == ["2026-07-18"]


class TestIdempotency:
    def test_already_certified_days_are_skipped(self):
        conn = _db(days_with_data=["2026-07-17", "2026-07-18"],
                   days_certified=["2026-07-17"])
        to_certify, already, _ = cc.plan(conn, 7, REF)
        assert to_certify == ["2026-07-18"]
        assert already == ["2026-07-17"]

    def test_fully_certified_window_is_a_noop(self):
        days = ["2026-07-17", "2026-07-18"]
        conn = _db(days_with_data=days, days_certified=days)
        to_certify, already, _ = cc.plan(conn, 7, REF)
        assert to_certify == []
        assert already == days

    def test_never_overwrites_an_existing_verdict(self):
        """The wrapper must not re-certify a recorded day. Re-certification is a
        deliberate operator act via certify_day.py --date, not a side effect of
        the timer firing."""
        conn = _db(days_with_data=["2026-07-18"], days_certified=["2026-07-18"])
        to_certify, _, _ = cc.plan(conn, 7, REF)
        assert to_certify == []


class TestNoDataDays:
    def test_days_without_collection_data_are_skipped(self):
        """A full-day outage is a gap in the COLLECTION record, already reported
        by the heartbeat and healthcheck dead-man. Certifying it RED would
        misrepresent 'we did not collect' as 'we collected badly'."""
        conn = _db(days_with_data=["2026-07-18"])
        to_certify, _, no_data = cc.plan(conn, 3, REF)
        assert to_certify == ["2026-07-18"]
        assert no_data == ["2026-07-16", "2026-07-17"]

    def test_empty_db_certifies_nothing(self):
        conn = _db()
        to_certify, already, no_data = cc.plan(conn, 7, REF)
        assert to_certify == []
        assert len(no_data) == 7

    def test_missing_tables_do_not_raise(self):
        """A pre-v4 schema has no daily_certifier_log. certify_day.py reports
        that authoritatively (exit 3); the wrapper must not crash first."""
        conn = sqlite3.connect(":memory:")
        to_certify, already, no_data = cc.plan(conn, 7, REF)
        assert (to_certify, already) == ([], [])


class TestSchemaGuard:
    """Fail-closed: 'wrong schema' must not be reported as 'nothing to certify'.

    Without collection_metrics every day looks like a no-data day, so plan()
    returns an empty work list and the run would otherwise exit 0 on a database
    that cannot be certified at all — making a misconfigured deploy look healthy.
    """

    def test_detects_both_missing_tables(self):
        conn = sqlite3.connect(":memory:")
        assert cc.missing_tables(conn) == ["collection_metrics", "daily_certifier_log"]

    def test_detects_partially_migrated_schema(self):
        conn = _db()
        conn.execute("DROP TABLE daily_certifier_log")
        assert cc.missing_tables(conn) == ["daily_certifier_log"]

    def test_complete_schema_reports_nothing_missing(self):
        assert cc.missing_tables(_db()) == []


class TestExitSemantics:
    def test_verdict_codes_are_successes(self):
        """0/1/2 = GREEN/AMBER/RED all mean 'a verdict was reached'. This is
        what btcbot-certify.service encodes as SuccessExitStatus=0 1 2."""
        assert cc.VERDICT_EXIT_CODES == {0, 1, 2}

    def test_certifier_error_is_not_a_verdict(self):
        assert 3 not in cc.VERDICT_EXIT_CODES
        assert cc.EXIT_ERROR == 3

    def test_utc_yesterday_is_timezone_independent(self):
        from datetime import datetime, timezone
        got = cc.utc_yesterday(datetime(2026, 7, 19, 0, 30, tzinfo=timezone.utc))
        assert got == date(2026, 7, 18)


class TestSystemdUnitContract:
    """The unit files encode assumptions made above; drift between them and the
    wrapper is a silent operational failure, so assert the contract directly."""

    def _unit(self, name: str) -> str:
        return (ROOT / "deploy" / name).read_text(encoding="utf-8")

    def test_service_accepts_amber_and_red_as_success(self):
        assert "SuccessExitStatus=0 1 2" in self._unit("btcbot-certify.service")

    def test_service_does_not_whitelist_certifier_error(self):
        line = [l for l in self._unit("btcbot-certify.service").splitlines()
                if l.startswith("SuccessExitStatus=")][0]
        assert "3" not in line.split("=", 1)[1].split()

    def test_service_runs_the_wrapper_not_the_certifier_directly(self):
        exec_line = [l for l in self._unit("btcbot-certify.service").splitlines()
                     if l.startswith("ExecStart=")][0]
        assert "certify_catchup.py" in exec_line
        assert "certify_day.py" not in exec_line

    def test_timer_is_persistent(self):
        assert "Persistent=true" in self._unit("btcbot-certify.timer")

    def test_timer_avoids_the_evidence_to_fwdhg_window_and_backup(self):
        """ADR-0001 §15.4 protects 00:05->01:00; backup owns 02:00."""
        unit = self._unit("btcbot-certify.timer")
        assert "OnCalendar=*-*-* 01:30:00 UTC" in unit
        jitter = int([l for l in unit.splitlines()
                      if l.startswith("RandomizedDelaySec=")][0].split("=")[1])
        # 01:30 + jitter must stay clear of 02:00.
        assert jitter <= 1800

    def test_service_grants_wal_directory_write(self):
        assert "ReadWritePaths=/opt/btcbot/data" in self._unit("btcbot-certify.service")
