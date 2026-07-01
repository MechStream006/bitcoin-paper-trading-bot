"""Phase A heartbeat observability — overall status, as_of, forward-evidence line.

Covers:
  TestForwardStatusSummarize  -- read-only run_log summary across all states
  TestForwardRenderLine       -- ASCII rendering for every state
  TestHeartbeatBackCompat     -- forward_status_path=None preserves old behavior
  TestHeartbeatStatusHeader   -- OK / DEGRADED / CRITICAL roll-up + as_of line
  TestHeartbeatForwardLine    -- forward line appears and reflects DB state

No live network, no live DB.  Builds tiny SQLite run_log fixtures on disk.

Run from repo root:
    conda run -n btc_bot pytest tests/test_heartbeat_phase_a.py -v
"""
from __future__ import annotations

import re
import sqlite3
import time
from datetime import datetime, timezone

import pytest

from src.alerts import forward_status as fs
from src.alerts.heartbeat import HeartbeatService
from src.alerts.rate_limiter import RateLimiter
from src.runtime.health import HealthStatus
from src.state.market_state import MarketState

NOW = datetime(2026, 6, 30, 14, 0, 0, tzinfo=timezone.utc)
EXPECTED = "2026-06-29"   # expected_run_date(NOW)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_run_log(path, rows):
    """rows: list of (signal_id, run_date, status)."""
    c = sqlite3.connect(str(path))
    c.execute(
        "CREATE TABLE run_log (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " signal_id TEXT NOT NULL, run_date TEXT NOT NULL, fn_hash TEXT NOT NULL,"
        " status TEXT NOT NULL DEFAULT 'ok', note TEXT,"
        " created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),"
        " UNIQUE (signal_id, run_date));"
    )
    c.executemany(
        "INSERT INTO run_log (signal_id, run_date, fn_hash, status) VALUES (?,?,?,?);",
        [(s, d, "ab" * 32, st) for (s, d, st) in rows],
    )
    c.commit()
    c.close()


def _fresh_rows(date=EXPECTED, status="ok"):
    return [(s, date, status) for s in fs.CANONICAL_SIGNALS]


class _FakeWS:
    def __init__(self, healthy=True, fatal=False):
        self._h = healthy
        self._f = fatal

    def health(self):
        if self._h:
            return HealthStatus.ok(
                "ws_manager", "state=STREAMING last_msg=2.1s msgs=500 reconnects=1")
        return HealthStatus.degraded(
            "ws_manager", "state=STALE last_msg=90s msgs=200 reconnects=5",
            fatal=self._f)


class _FakeSnap:
    def health(self):
        return HealthStatus.ok(
            "snapshot_writer",
            "last_snap=45s total_rows=300 last_rows=20 consec_failures=0")


class _FakeChain:
    def current_snapshot(self):
        return None

    def health(self):
        return HealthStatus.ok("chain_refresher", "ok")


def _make_hb(forward_path=None, ws=None, sender=None):
    ms = MarketState(universe=["C-BTC-95000-280526", "P-BTC-95000-280526"])
    ms.update_ticker("C-BTC-95000-280526", {"mark_price": "95100.0"})
    return HeartbeatService(
        sender=sender if sender is not None else (lambda t: None),
        ws_manager=ws if ws is not None else _FakeWS(),
        market_state=ms,
        snapshot_writer=_FakeSnap(),
        chain_refresher=_FakeChain(),
        rate_limiter=RateLimiter(default_cooldown_sec=0),
        started_at=time.monotonic() - 100,
        forward_status_path=forward_path,
    )


# ---------------------------------------------------------------------------
# forward_status.summarize
# ---------------------------------------------------------------------------

class TestForwardStatusSummarize:
    def test_missing_file_is_neutral(self):
        s = fs.summarize("/no/such/path.db", NOW)
        assert s.state == "missing"
        assert s.severity() == "neutral"

    def test_fresh_complete_is_ok(self, tmp_path):
        p = tmp_path / "ev.db"
        _make_run_log(p, _fresh_rows())
        s = fs.summarize(str(p), NOW)
        assert s.state == "ok"
        assert s.severity() == "ok"
        assert s.n_ok == 5 and s.n_logged == 5

    def test_stale_run_is_degraded(self, tmp_path):
        p = tmp_path / "ev.db"
        _make_run_log(p, _fresh_rows(date="2026-06-26"))
        s = fs.summarize(str(p), NOW)
        assert s.state == "stale"
        assert s.severity() == "degraded"
        assert s.latest_run_date == "2026-06-26"

    def test_fn_hash_violation_is_critical(self, tmp_path):
        p = tmp_path / "ev.db"
        rows = [(s, EXPECTED, "ok") for s in fs.CANONICAL_SIGNALS[:-1]]
        rows.append(("g3_atr_expansion", EXPECTED, "fn_hash_violation"))
        _make_run_log(p, rows)
        s = fs.summarize(str(p), NOW)
        assert s.state == "critical"
        assert s.severity() == "critical"
        assert "g3_atr_expansion" in s.detail

    def test_error_status_is_degraded(self, tmp_path):
        p = tmp_path / "ev.db"
        rows = [(s, EXPECTED, "error" if s == "g3_momentum_tsmom" else "ok")
                for s in fs.CANONICAL_SIGNALS]
        _make_run_log(p, rows)
        s = fs.summarize(str(p), NOW)
        assert s.state == "degraded"
        assert s.n_ok == 4

    def test_partial_coverage_still_ok(self, tmp_path):
        # ivrv legitimately absent (data-unavailable day) but fresh + clean
        p = tmp_path / "ev.db"
        rows = [(s, EXPECTED, "ok")
                for s in fs.CANONICAL_SIGNALS if s != "ivrv_vrp_spread"]
        _make_run_log(p, rows)
        s = fs.summarize(str(p), NOW)
        assert s.state == "ok"
        assert s.n_logged == 4

    def test_empty_run_log_is_stale(self, tmp_path):
        p = tmp_path / "ev.db"
        _make_run_log(p, [])
        s = fs.summarize(str(p), NOW)
        assert s.state == "stale"

    def test_corrupt_file_is_error_never_raises(self, tmp_path):
        p = tmp_path / "ev.db"
        p.write_bytes(b"definitely not sqlite")
        s = fs.summarize(str(p), NOW)
        assert s.state == "error"
        assert s.severity() == "degraded"

    def test_summarize_does_not_create_missing_file(self, tmp_path):
        # READ-ONLY guarantee: summarizing a missing path must NOT create it.
        p = tmp_path / "ghost.db"
        fs.summarize(str(p), NOW)
        assert not p.exists()

    def test_expected_run_date_grace(self):
        assert fs.expected_run_date(NOW) == "2026-06-29"
        early = datetime(2026, 6, 30, 1, 0, 0, tzinfo=timezone.utc)
        assert fs.expected_run_date(early) == "2026-06-28"


class TestForwardRenderLine:
    @pytest.mark.parametrize("state", ["ok", "stale", "critical", "degraded",
                                       "missing", "error"])
    def test_render_is_ascii_and_prefixed(self, state):
        sample = fs.ForwardStatus(state, EXPECTED, EXPECTED,
                                  {"x": "ok"}, 5, 5, "detail text")
        line = fs.render_line(sample)
        line.encode("ascii")             # must not raise
        assert line.startswith("fwd-evid")

    def test_ok_line_shows_gov_ok(self, tmp_path):
        p = tmp_path / "ev.db"
        _make_run_log(p, _fresh_rows())
        line = fs.render_line(fs.summarize(str(p), NOW))
        assert "gov=ok" in line and "fn_hash=ok" in line

    def test_stale_line_omits_gov_ok(self, tmp_path):
        p = tmp_path / "ev.db"
        _make_run_log(p, _fresh_rows(date="2026-06-20"))
        line = fs.render_line(fs.summarize(str(p), NOW))
        assert "gov=ok" not in line and "STALE" in line


# ---------------------------------------------------------------------------
# Heartbeat integration
# ---------------------------------------------------------------------------

class TestHeartbeatBackCompat:
    def test_no_forward_path_omits_section(self):
        hb = _make_hb(forward_path=None)
        text = hb._compose_heartbeat()
        assert "fwd-evid" not in text
        assert "BTC Bot Heartbeat" in text
        text.encode("ascii")

    def test_healthy_state_is_ok_no_degraded(self):
        hb = _make_hb(forward_path=None)
        text = hb._compose_heartbeat()
        assert "[OK]" in text
        assert "[DEGRADED]" not in text

    def test_sender_still_called(self):
        sent = []
        hb = _make_hb(forward_path=None, sender=sent.append)
        hb.send_heartbeat()
        assert len(sent) == 1
        assert hb.health().healthy


class TestHeartbeatStatusHeader:
    def test_as_of_timestamp_present(self):
        hb = _make_hb(forward_path=None)
        text = hb._compose_heartbeat()
        m = re.search(r"as_of\s*:\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)", text)
        assert m, text

    def test_degraded_component_sets_header(self):
        hb = _make_hb(forward_path=None, ws=_FakeWS(healthy=False))
        text = hb._compose_heartbeat()
        assert "[DEGRADED]" in text

    def test_fatal_component_sets_critical(self):
        hb = _make_hb(forward_path=None, ws=_FakeWS(healthy=False, fatal=True))
        text = hb._compose_heartbeat()
        assert "[CRITICAL]" in text

    def test_fresh_forward_keeps_ok(self, tmp_path):
        p = tmp_path / "ev.db"
        _make_run_log(p, _fresh_rows())
        hb = _make_hb(forward_path=str(p))
        text = hb._compose_heartbeat()
        assert "[OK]" in text
        assert "gov=ok" in text

    def test_stale_forward_degrades_header(self, tmp_path):
        p = tmp_path / "ev.db"
        _make_run_log(p, _fresh_rows(date="2026-06-25"))
        hb = _make_hb(forward_path=str(p))
        text = hb._compose_heartbeat()
        assert "[DEGRADED]" in text
        assert "STALE" in text

    def test_fn_hash_violation_forward_criticals_header(self, tmp_path):
        p = tmp_path / "ev.db"
        rows = [(s, EXPECTED, "ok") for s in fs.CANONICAL_SIGNALS[:-1]]
        rows.append(("g3_atr_expansion", EXPECTED, "fn_hash_violation"))
        _make_run_log(p, rows)
        hb = _make_hb(forward_path=str(p))
        text = hb._compose_heartbeat()
        assert "[CRITICAL]" in text

    def test_missing_forward_db_is_neutral(self, tmp_path):
        # configured but file absent -> neutral note, header stays OK
        hb = _make_hb(forward_path=str(tmp_path / "absent.db"))
        text = hb._compose_heartbeat()
        assert "[OK]" in text
        assert "n/a" in text


class TestHeartbeatForwardLine:
    def test_forward_line_present_when_configured(self, tmp_path):
        p = tmp_path / "ev.db"
        _make_run_log(p, _fresh_rows())
        hb = _make_hb(forward_path=str(p))
        text = hb._compose_heartbeat()
        assert "fwd-evid" in text

    def test_compose_never_raises_on_bad_db(self, tmp_path):
        p = tmp_path / "ev.db"
        p.write_bytes(b"garbage")
        hb = _make_hb(forward_path=str(p))
        text = hb._compose_heartbeat()   # must not raise
        assert "fwd-evid" in text
        assert "[DEGRADED]" in text
