"""D3a forward-cycle dead-man — scripts/healthcheck.py::check_forward_cycle.

External (healthcheck-timer) dead-man for the daily forward-evidence cycle,
reusing src.alerts.forward_status as the single source of truth.

Severity mapping (approved design):
  missing DB               -> INFO (skip)
  fresh + clean            -> OK
  1 day behind             -> WARNING
  >= 2 days behind         -> CRITICAL
  fn_hash_violation        -> CRITICAL
  a signal errored         -> WARNING
  unreadable run_log       -> CRITICAL
  never ran (empty)        -> CRITICAL

Every non-OK alert message must carry: expected run_date, latest run_date,
days behind, and the latest per-signal statuses.

Run from repo root:
    conda run -n btc_bot pytest tests/test_healthcheck_forward.py -v
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.alerts import forward_status as fs

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load_healthcheck():
    spec = importlib.util.spec_from_file_location("healthcheck_mod", _SCRIPTS / "healthcheck.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["healthcheck_mod"] = mod   # required so @dataclass resolves
    spec.loader.exec_module(mod)
    return mod


hc = _load_healthcheck()
EXPECTED = fs.expected_run_date()


def _mk(path, rows):
    c = sqlite3.connect(str(path))
    c.execute(
        "CREATE TABLE run_log (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " signal_id TEXT, run_date TEXT, fn_hash TEXT, status TEXT DEFAULT 'ok',"
        " note TEXT, created_at TEXT, UNIQUE(signal_id,run_date));"
    )
    c.executemany(
        "INSERT INTO run_log (signal_id, run_date, fn_hash, status) VALUES (?,?,?,?);",
        [(s, d, "ab" * 32, st) for (s, d, st) in rows],
    )
    c.commit()
    c.close()


def _rows(day, viol=False, err=False):
    out = []
    for s in fs.CANONICAL_SIGNALS:
        st = "ok"
        if viol and s == "g3_atr_expansion":
            st = "fn_hash_violation"
        if err and s == "g3_momentum_tsmom":
            st = "error"
        out.append((s, day, st))
    return out


def _minus(days):
    return (date.fromisoformat(EXPECTED) - timedelta(days=days)).isoformat()


# ---------------------------------------------------------------------------
# Severity mapping
# ---------------------------------------------------------------------------

class TestForwardCycleSeverity:
    def test_missing_db_is_info_skip(self):
        r = hc.check_forward_cycle(Path("/no/such/evidence_forward.db"))
        assert r.severity == hc.INFO

    def test_fresh_is_ok(self, tmp_path):
        p = tmp_path / "ev.db"; _mk(p, _rows(EXPECTED))
        r = hc.check_forward_cycle(p)
        assert r.severity == hc.OK

    def test_one_day_behind_is_warning(self, tmp_path):
        p = tmp_path / "ev.db"; _mk(p, _rows(_minus(1)))
        r = hc.check_forward_cycle(p)
        assert r.severity == hc.WARNING

    def test_two_days_behind_is_critical(self, tmp_path):
        p = tmp_path / "ev.db"; _mk(p, _rows(_minus(2)))
        r = hc.check_forward_cycle(p)
        assert r.severity == hc.CRITICAL

    def test_three_days_behind_is_critical(self, tmp_path):
        p = tmp_path / "ev.db"; _mk(p, _rows(_minus(3)))
        r = hc.check_forward_cycle(p)
        assert r.severity == hc.CRITICAL

    def test_fn_hash_violation_is_critical(self, tmp_path):
        p = tmp_path / "ev.db"; _mk(p, _rows(EXPECTED, viol=True))
        r = hc.check_forward_cycle(p)
        assert r.severity == hc.CRITICAL

    def test_signal_error_is_warning(self, tmp_path):
        p = tmp_path / "ev.db"; _mk(p, _rows(EXPECTED, err=True))
        r = hc.check_forward_cycle(p)
        assert r.severity == hc.WARNING

    def test_unreadable_run_log_is_critical(self, tmp_path):
        p = tmp_path / "ev.db"; p.write_bytes(b"not a database")
        r = hc.check_forward_cycle(p)
        assert r.severity == hc.CRITICAL

    def test_empty_run_log_never_ran_is_critical(self, tmp_path):
        p = tmp_path / "ev.db"; _mk(p, [])
        r = hc.check_forward_cycle(p)
        assert r.severity == hc.CRITICAL


# ---------------------------------------------------------------------------
# Alert message content (must carry the required fields)
# ---------------------------------------------------------------------------

class TestForwardCycleMessage:
    def test_message_has_required_fields(self, tmp_path):
        p = tmp_path / "ev.db"; _mk(p, _rows(_minus(2)))
        r = hc.check_forward_cycle(p)
        msg = r.message
        assert f"expected {EXPECTED}" in msg
        assert f"latest {_minus(2)}" in msg
        assert "2 days behind" in msg
        # latest signal statuses, aliased
        assert "h5=ok" in msg and "atr=ok" in msg

    def test_detail_is_structured(self, tmp_path):
        p = tmp_path / "ev.db"; _mk(p, _rows(_minus(1)))
        r = hc.check_forward_cycle(p)
        assert r.detail["expected_run_date"] == EXPECTED
        assert r.detail["latest_run_date"] == _minus(1)
        assert r.detail["days_behind"] == 1
        assert r.detail["signals"]["h5_vol_regime"] == "ok"

    def test_violation_message_names_signal(self, tmp_path):
        p = tmp_path / "ev.db"; _mk(p, _rows(EXPECTED, viol=True))
        r = hc.check_forward_cycle(p)
        assert "fn_hash_violation" in r.message
        assert "atr=fn_hash_violation" in r.message


# ---------------------------------------------------------------------------
# Wiring: --no-forward-check excludes the check; default includes it
# ---------------------------------------------------------------------------

class TestForwardCheckWiring:
    def test_days_behind_helper(self):
        assert hc._days_behind("2026-06-28", "2026-06-30") == 2
        assert hc._days_behind(None, "2026-06-30") is None
        assert hc._days_behind("2026-06-30", "2026-06-30") == 0
        # future latest clamps to 0 (never negative)
        assert hc._days_behind("2026-07-05", "2026-06-30") == 0

    def test_info_severity_does_not_alert(self):
        # INFO ranks below WARNING, so a missing forward DB never pages.
        assert hc._RANK[hc.INFO] < hc._RANK[hc.WARNING]
