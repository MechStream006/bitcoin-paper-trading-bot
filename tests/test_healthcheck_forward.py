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
import os
import sqlite3
import stat
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


# ---------------------------------------------------------------------------
# Production bug regression (2026-07-18): WAL-mode reader needs directory
# write access, not just file read access.
#
# Root cause: evidence_forward.db is journal_mode=WAL (init_forward_db.py).
# Opening a WAL database in mode=ro still requires SQLite to open/lock the
# -wal/-shm sibling files, which requires WRITE access to the *containing
# directory* -- regardless of the SQL-level read-only mode. btcbot.service's
# systemd sandbox (ProtectSystem=strict) originally granted
# ReadWritePaths=/opt/btcbot/data only, leaving /opt/btcbot/research/data
# (where evidence_forward.db lives) read-only inside that process's mount
# namespace. Every summarize() call from the live HeartbeatService therefore
# failed with "unable to open database file", while the identical file opened
# via CLI or a standalone script (outside the sandbox) succeeded. Fixed by
# adding that path to ReadWritePaths= in deploy/btcbot.service.
#
# POSIX-only: os.chmod on a directory does not enforce write-denial on
# Windows, so this only reproduces on the Linux target the bot deploys to.
# Also skipped for root, which bypasses directory permission checks.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(os.name != "posix", reason="directory permission enforcement is POSIX-only")
@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root bypasses directory permission checks",
)
class TestForwardStatusReadOnlyDirectory:
    def _mk_wal(self, path, rows):
        c = sqlite3.connect(str(path))
        c.execute("PRAGMA journal_mode = WAL")
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
        # Drop the -wal/-shm side files so the next open must recreate them --
        # this is precisely what forces SQLite to need directory write access.
        for ext in ("-wal", "-shm"):
            side = Path(str(path) + ext)
            if side.exists():
                side.unlink()

    def test_readonly_directory_reproduces_unable_to_open(self, tmp_path):
        db = tmp_path / "evidence_forward.db"
        self._mk_wal(db, _rows(EXPECTED))

        mode = tmp_path.stat().st_mode
        os.chmod(tmp_path, stat.S_IRUSR | stat.S_IXUSR)  # r-x, no write
        try:
            result = fs.summarize(str(db))
        finally:
            os.chmod(tmp_path, mode)

        assert result.state == "error", result
        assert "unable to open database file" in result.detail

    def test_readwrite_directory_is_the_fix(self, tmp_path):
        """Same DB, directory left writable (what ReadWritePaths= grants) -> ok.

        Regression guard for the actual production fix: adding
        /opt/btcbot/research/data to ReadWritePaths= in deploy/btcbot.service
        is exactly equivalent to leaving this directory writable.
        """
        db = tmp_path / "evidence_forward.db"
        self._mk_wal(db, _rows(EXPECTED))

        result = fs.summarize(str(db))

        assert result.state == "ok", result
        assert result.n_ok == len(fs.CANONICAL_SIGNALS)

    def test_failure_is_logged(self, tmp_path, caplog):
        """The read-error path must log. Previously silent -- only visible in
        the Telegram heartbeat text, once per ~30 min, with no correlation to
        a systemd/sandbox change.
        """
        db = tmp_path / "evidence_forward.db"
        self._mk_wal(db, _rows(EXPECTED))

        mode = tmp_path.stat().st_mode
        os.chmod(tmp_path, stat.S_IRUSR | stat.S_IXUSR)
        try:
            with caplog.at_level("WARNING", logger="src.alerts.forward_status"):
                fs.summarize(str(db))
        finally:
            os.chmod(tmp_path, mode)

        assert any("summarize" in rec.message for rec in caplog.records)
