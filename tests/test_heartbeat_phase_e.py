"""Phase E heartbeat observability — EVIDENCE block (research-evidence
collection/accumulation status).

Covers:
  TestEvidenceRendering    -- realistic data, per-signal counts, H6, gov, gaps
  TestEvidenceDeltas       -- row-count delta across successive heartbeats
  TestEvidenceH6States     -- H6 sub-line across ok/warning/critical/missing
  TestEvidenceGovernance   -- gov=OK/FAIL/n-a derivation
  TestEvidenceGracefulFailure -- corrupt/missing DB never breaks the heartbeat
  TestEvidenceSeverityIsolation -- counts/deltas/H6-ok never escalate severity;
                                   H6 warning/critical DOES escalate, same
                                   semantics as scripts/healthcheck.py::check_h6_cycle()
  TestEvidenceStatusLabel  -- fixed, informational, never data-derived
  TestEvidenceBackCompat   -- forward_status_path=None preserves prior behavior;
                              existing fwd-evid line and all other sections
                              are byte-for-byte unaffected

No live network, no scheduler start, no production DB touched -- every test
builds a disposable tmp_path SQLite fixture.

Run from repo root:
    conda run -n btc_bot pytest tests/test_heartbeat_phase_e.py -v
"""
from __future__ import annotations

import ast
import pathlib
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.alerts.forward_status import expected_run_date
from src.alerts.heartbeat import HeartbeatService
from src.alerts.rate_limiter import RateLimiter
from src.runtime.health import HealthStatus
from src.state.market_state import MarketState


def _fresh_run_date() -> str:
    """The run_date the heartbeat considers CURRENT at the real clock.

    Derived, never hardcoded. ``forward_status.expected_run_date()`` moves with
    the calendar (yesterday, or the day before that inside the 02:00 UTC grace
    window), so a literal fixture date silently goes stale at the next UTC
    midnight: ``fwd-evid`` flips to STALE, that escalates the roll-up to
    DEGRADED, and every test asserting a fresh/[OK] heartbeat starts failing on
    a day nobody changed any code. That is exactly what happened on
    2026-08-12 to the three tests this helper now feeds.

    Tests that want a STALE fixture must pass ``latest_date`` explicitly.
    """
    return expected_run_date()


# ---------------------------------------------------------------------------
# Fakes + fixture builders
# ---------------------------------------------------------------------------

class _FakeWS:
    def health(self):
        return HealthStatus.ok(
            "ws_manager", "state=STREAMING last_msg=2.1s msgs=500 reconnects=0")


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


def _make_hb(forward_status_path=None, emit_deltas=True):
    ms = MarketState(universe=["C-BTC-95000-280526"])
    return HeartbeatService(
        sender=lambda t: None,
        ws_manager=_FakeWS(),
        market_state=ms,
        snapshot_writer=_FakeSnap(),
        chain_refresher=_FakeChain(),
        rate_limiter=RateLimiter(default_cooldown_sec=0),
        started_at=time.monotonic() - 100,
        forward_status_path=forward_status_path,
        emit_deltas=emit_deltas,
    )


_SIG_COUNTS = {
    # signal_id: (run_log rows, closed trades, open trades)
    "g1_market_regime_sma200": (38, 5, 1),
    "g3_momentum_tsmom": (38, 5, 1),
    "h5_vol_regime": (38, 4, 1),
    "ivrv_vrp_spread": (34, 3, 1),
    "g3_atr_expansion": (25, 2, 0),
}


def _build_evidence_db(path: Path, sig_counts=None, latest_date=None, statuses=None):
    """Build a schema-faithful evidence_forward.db (run_log + trade_entries).

    sig_counts:  {signal_id: (rows, closed, open)}, defaults to _SIG_COUNTS.
    latest_date: newest run_date, default = _fresh_run_date() so the fixture is
                 CURRENT at whatever the real clock is. Pass an explicit date
                 only when a test deliberately wants a stale corpus.
    statuses:    optional {signal_id: status} override for the latest row of
                 that signal (e.g. to inject a governance-failure 'error' row).
    """
    sig_counts = sig_counts if sig_counts is not None else _SIG_COUNTS
    latest_date = latest_date if latest_date is not None else _fresh_run_date()
    statuses = statuses or {}
    con = sqlite3.connect(str(path))
    con.execute(
        "CREATE TABLE run_log (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "signal_id TEXT NOT NULL, run_date TEXT NOT NULL, fn_hash TEXT NOT NULL, "
        "status TEXT NOT NULL DEFAULT 'ok', note TEXT, "
        "UNIQUE(signal_id, run_date))"
    )
    con.execute(
        "CREATE TABLE trade_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "signal_id TEXT NOT NULL, entry_date TEXT NOT NULL, direction INTEGER NOT NULL, "
        "entry_close_price REAL, planned_exit_date TEXT NOT NULL, "
        "open INTEGER NOT NULL DEFAULT 1)"
    )
    base = datetime.fromisoformat(latest_date).replace(tzinfo=timezone.utc)
    for sid, (rows, closed, open_) in sig_counts.items():
        for i in range(rows):
            d = (base - timedelta(days=rows - 1 - i)).date().isoformat()
            status = "ok"
            if i == rows - 1 and sid in statuses:
                status = statuses[sid]
            con.execute(
                "INSERT INTO run_log (signal_id, run_date, fn_hash, status) VALUES (?,?,?,?)",
                (sid, d, "ab" * 32, status),
            )
        for _ in range(closed):
            con.execute(
                "INSERT INTO trade_entries "
                "(signal_id, entry_date, direction, planned_exit_date, open) "
                "VALUES (?,?,?,?,0)",
                (sid, latest_date, 1, latest_date),
            )
        for _ in range(open_):
            con.execute(
                "INSERT INTO trade_entries "
                "(signal_id, entry_date, direction, planned_exit_date, open) "
                "VALUES (?,?,?,?,1)",
                (sid, latest_date, 1, latest_date),
            )
    con.commit()
    con.close()


def _append_run_days(path: Path, signal_id: str, n: int) -> list[str]:
    """Append `n` consecutive run_log days AFTER whatever the fixture already holds.

    Derived from the corpus, never from a literal. A hardcoded start date
    collides with the fixture the moment the calendar reaches it: the fixture
    ends at ``_fresh_run_date()``, which is *yesterday*, so a run of days
    beginning at 2026-08-20 sat clear of a fixture ending 2026-08-19 and then
    overlapped it exactly one day later, failing UNIQUE(signal_id, run_date) on
    a morning nobody changed any code. Same failure mode ``_fresh_run_date``
    already exists to prevent, one layer further in.

    Returns the dates written, so a caller can assert on them.
    """
    con = sqlite3.connect(str(path))
    try:
        newest = con.execute(
            "SELECT MAX(run_date) FROM run_log WHERE signal_id = ?", (signal_id,)
        ).fetchone()[0]
        base = datetime.fromisoformat(newest).replace(tzinfo=timezone.utc)
        written = []
        for i in range(1, n + 1):
            d = (base + timedelta(days=i)).date().isoformat()
            con.execute(
                "INSERT INTO run_log (signal_id, run_date, fn_hash, status) "
                "VALUES (?,?,?,?)",
                (signal_id, d, "ab" * 32, "ok"),
            )
            written.append(d)
        con.commit()
        return written
    finally:
        con.close()


def _build_h6_db(path: Path, rows):
    """rows: list of (run_ts_ms, status)."""
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE run_log (run_ts INTEGER PRIMARY KEY, status TEXT NOT NULL)")
    con.executemany("INSERT INTO run_log (run_ts, status) VALUES (?, ?)", rows)
    con.commit()
    con.close()


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


# ---------------------------------------------------------------------------
# 1 + 2: rendering + per-signal counts
# ---------------------------------------------------------------------------

class TestEvidenceRendering:
    def test_evidence_block_renders_with_realistic_data(self, tmp_path: Path):
        ev = tmp_path / "evidence_forward.db"
        _build_evidence_db(ev)
        now = datetime.now(timezone.utc)
        _build_h6_db(tmp_path / "forward_evidence.db", [(_ms(now), "ok")])

        hb = _make_hb(forward_status_path=str(ev))
        text = hb._compose_heartbeat()

        assert "EVIDENCE" in text
        assert "live-fwd :" in text
        assert "rows     : 173 total" in text
        assert "trades   : 23 entries | 19 closed | 4 open" in text
        assert "H6       :" in text
        assert "gov      :" in text
        assert "gaps     : 19 missing days (2026-07-18..08-05, documented)" in text
        assert "status   : ACCUMULATING -- NOT VALIDATED" in text

    def test_per_signal_counts_in_fixed_order(self, tmp_path: Path):
        ev = tmp_path / "evidence_forward.db"
        _build_evidence_db(ev)
        hb = _make_hb(forward_status_path=str(ev))
        text = hb._compose_heartbeat()

        line = next(l for l in text.splitlines() if l.strip().startswith("signals"))
        assert line.strip() == "signals  : g1 5/1 | g3 5/1 | h5 4/1 | ivrv 3/1 | atr 2/0"

    def test_signals_line_when_no_trades_yet(self, tmp_path: Path):
        ev = tmp_path / "evidence_forward.db"
        _build_evidence_db(ev, sig_counts={"h5_vol_regime": (5, 0, 0)})
        hb = _make_hb(forward_status_path=str(ev))
        text = hb._compose_heartbeat()
        assert "signals  : none yet" in text


# ---------------------------------------------------------------------------
# 3: row/trade deltas
# ---------------------------------------------------------------------------

class TestEvidenceDeltas:
    def test_first_heartbeat_shows_first_sample_not_misleading_delta(self, tmp_path: Path):
        ev = tmp_path / "evidence_forward.db"
        _build_evidence_db(ev)
        hb = _make_hb(forward_status_path=str(ev))
        hb.send_heartbeat()
        sent = []
        hb2 = _make_hb(forward_status_path=str(ev))
        hb2._sender = sent.append
        text = hb2._compose_heartbeat()
        assert "rows     : 173 total (first sample)" in text

    def test_second_heartbeat_shows_correct_positive_delta(self, tmp_path: Path):
        ev = tmp_path / "evidence_forward.db"
        _build_evidence_db(ev, sig_counts={"h5_vol_regime": (10, 1, 0)})
        sent: list = []
        hb = _make_hb(forward_status_path=str(ev))
        hb._sender = sent.append
        hb.send_heartbeat()  # commits baseline at 10 rows

        # advance: add 5 more rows for h5
        _append_run_days(ev, "h5_vol_regime", 5)

        hb.send_heartbeat()  # the delta must be captured from THIS send's own
        # composition -- composing again afterward would see the baseline
        # already advanced by this send's own commit, showing +0.
        assert len(sent) == 2
        assert "(+5 since last)" in sent[1]

    def test_restart_does_not_produce_false_growth(self, tmp_path: Path):
        """A fresh HeartbeatService (simulating process restart) has no
        baseline -- must show 'first sample', never a misleading '+N' derived
        from a stale/foreign baseline."""
        ev = tmp_path / "evidence_forward.db"
        _build_evidence_db(ev, sig_counts={"h5_vol_regime": (100, 1, 0)})
        hb_before_restart = _make_hb(forward_status_path=str(ev))
        hb_before_restart.send_heartbeat()  # baseline committed at 100 rows, in-process only

        hb_after_restart = _make_hb(forward_status_path=str(ev))  # fresh tracker
        text = hb_after_restart._compose_heartbeat()
        assert "rows     : 100 total (first sample)" in text

    def test_deltas_disabled_omits_delta_suffix(self, tmp_path: Path):
        ev = tmp_path / "evidence_forward.db"
        _build_evidence_db(ev, sig_counts={"h5_vol_regime": (10, 1, 0)})
        hb = _make_hb(forward_status_path=str(ev), emit_deltas=False)
        text = hb._compose_heartbeat()
        assert "rows     : 10 total" in text
        assert "since last" not in text
        assert "first sample" not in text.split("rows")[1].split("\n")[0]


class TestEvidenceDeltaDateIndependence:
    """The +5 delta must not depend on what day it is run.

    CI #76 went red with UNIQUE(signal_id, run_date) on a morning nobody
    changed any code: the advance rows started at a hardcoded 2026-08-20 while
    the fixture ends at _fresh_run_date(), which is *yesterday*. On 2026-08-20
    that was 08-19 and the ranges were disjoint; at the next UTC midnight the
    fixture's newest row became 08-20 and the two collided on exactly one day.
    These pin the scenario against the clock instead of hoping.
    """

    @pytest.mark.parametrize("latest_date", [
        "2026-08-19",   # the last date on which the old literal did not collide
        "2026-08-20",   # the date it started colliding
        "2026-08-21",
        "2026-08-24",   # inside the old hardcoded run
        "2026-12-31",   # year boundary
        "2027-03-01",
    ])
    def test_positive_delta_holds_on_any_calendar_date(self, tmp_path: Path,
                                                       latest_date: str):
        ev = tmp_path / "evidence_forward.db"
        _build_evidence_db(ev, sig_counts={"h5_vol_regime": (10, 1, 0)},
                           latest_date=latest_date)
        sent: list = []
        hb = _make_hb(forward_status_path=str(ev))
        hb._sender = sent.append
        hb.send_heartbeat()
        _append_run_days(ev, "h5_vol_regime", 5)
        hb.send_heartbeat()
        assert len(sent) == 2
        assert "(+5 since last)" in sent[1]

    def test_appended_days_are_strictly_after_the_fixture(self, tmp_path: Path):
        ev = tmp_path / "evidence_forward.db"
        _build_evidence_db(ev, sig_counts={"h5_vol_regime": (10, 1, 0)})
        con = sqlite3.connect(str(ev))
        before = con.execute(
            "SELECT MAX(run_date) FROM run_log WHERE signal_id='h5_vol_regime'"
        ).fetchone()[0]
        con.close()
        written = _append_run_days(ev, "h5_vol_regime", 5)
        assert len(written) == 5
        assert all(d > before for d in written)
        assert written == sorted(written)

    def test_appending_twice_never_collides(self, tmp_path: Path):
        """Idempotent in the sense that matters: it never raises on re-use."""
        ev = tmp_path / "evidence_forward.db"
        _build_evidence_db(ev, sig_counts={"h5_vol_regime": (10, 1, 0)})
        first = _append_run_days(ev, "h5_vol_regime", 5)
        second = _append_run_days(ev, "h5_vol_regime", 5)
        assert not set(first) & set(second)
        con = sqlite3.connect(str(ev))
        n = con.execute(
            "SELECT COUNT(*) FROM run_log WHERE signal_id='h5_vol_regime'").fetchone()[0]
        con.close()
        assert n == 20

    def test_the_delta_test_carries_no_calendar_literal(self):
        """The defect was a literal date. Refuse its return."""
        src = pathlib.Path("tests/test_heartbeat_phase_e.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        target = next(
            n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
            and n.name == "test_second_heartbeat_shows_correct_positive_delta")
        for node in ast.walk(target):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id == "datetime":
                raise AssertionError(
                    "hardcoded datetime(...) is back in the delta test; derive "
                    "the advance days from the fixture instead")


# ---------------------------------------------------------------------------
# 4: H6 status rendering
# ---------------------------------------------------------------------------

class TestEvidenceH6States:
    def test_h6_ok(self, tmp_path: Path):
        ev = tmp_path / "evidence_forward.db"
        _build_evidence_db(ev)
        now = datetime.now(timezone.utc)
        _build_h6_db(tmp_path / "forward_evidence.db", [(_ms(now), "ok")])
        hb = _make_hb(forward_status_path=str(ev))
        text = hb._compose_heartbeat()
        assert "H6       : " in text
        line = next(l for l in text.splitlines() if "H6       :" in l)
        assert "OK" in line
        assert "no-new-data=0" in line

    def test_h6_missing_db_renders_unavailable(self, tmp_path: Path):
        ev = tmp_path / "evidence_forward.db"
        _build_evidence_db(ev)
        # no forward_evidence.db created in tmp_path
        hb = _make_hb(forward_status_path=str(ev))
        text = hb._compose_heartbeat()
        assert "H6       : [unavailable]" in text

    def test_h6_no_new_data_streak_reported(self, tmp_path: Path):
        ev = tmp_path / "evidence_forward.db"
        _build_evidence_db(ev)
        now = datetime.now(timezone.utc)
        _build_h6_db(tmp_path / "forward_evidence.db", [
            (_ms(now - timedelta(days=2)), "ok"),
            (_ms(now - timedelta(days=1)), "no_new_data"),
            (_ms(now), "no_new_data"),
        ])
        hb = _make_hb(forward_status_path=str(ev))
        text = hb._compose_heartbeat()
        line = next(l for l in text.splitlines() if "H6       :" in l)
        assert "WARNING" in line
        assert "no-new-data=2" in line

    def test_h6_never_run_is_critical(self, tmp_path: Path):
        ev = tmp_path / "evidence_forward.db"
        _build_evidence_db(ev)
        _build_h6_db(tmp_path / "forward_evidence.db", [])
        hb = _make_hb(forward_status_path=str(ev))
        text = hb._compose_heartbeat()
        line = next(l for l in text.splitlines() if "H6       :" in l)
        assert "CRITICAL" in line


# ---------------------------------------------------------------------------
# 5: governance status
# ---------------------------------------------------------------------------

class TestEvidenceGovernance:
    def test_gov_ok_when_no_errors(self, tmp_path: Path):
        ev = tmp_path / "evidence_forward.db"
        _build_evidence_db(ev)
        hb = _make_hb(forward_status_path=str(ev))
        text = hb._compose_heartbeat()
        assert "gov      : gov=OK" in text

    def test_gov_fail_when_governance_error_row_present(self, tmp_path: Path):
        ev = tmp_path / "evidence_forward.db"
        _build_evidence_db(ev, statuses={"h5_vol_regime": "error"})
        hb = _make_hb(forward_status_path=str(ev))
        text = hb._compose_heartbeat()
        assert "gov      : gov=FAIL" in text

    def test_gov_na_when_no_data(self, tmp_path: Path):
        ev = tmp_path / "evidence_forward.db"
        _build_evidence_db(ev, sig_counts={})
        hb = _make_hb(forward_status_path=str(ev))
        text = hb._compose_heartbeat()
        assert "gov      : gov=n/a" in text


# ---------------------------------------------------------------------------
# 6: graceful failure
# ---------------------------------------------------------------------------

class TestEvidenceGracefulFailure:
    def test_corrupt_evidence_db_does_not_break_heartbeat(self, tmp_path: Path):
        ev = tmp_path / "evidence_forward.db"
        ev.write_bytes(b"not a sqlite database at all")
        hb = _make_hb(forward_status_path=str(ev))
        text = hb._compose_heartbeat()  # must not raise
        assert "BTC Bot Heartbeat" in text
        assert "EVIDENCE" in text
        assert "rows     : [unavailable]" in text
        assert "trades   : [unavailable]" in text
        assert "H6       : [unavailable]" in text or "H6       : [error]" in text

    def test_evidence_db_missing_run_log_table_degrades_gracefully(self, tmp_path: Path):
        ev = tmp_path / "evidence_forward.db"
        con = sqlite3.connect(str(ev))
        con.execute("CREATE TABLE unrelated (x INTEGER)")
        con.commit()
        con.close()
        hb = _make_hb(forward_status_path=str(ev))
        text = hb._compose_heartbeat()  # must not raise
        assert "BTC Bot Heartbeat" in text
        assert "rows     : [unavailable]" in text

    def test_other_sections_survive_evidence_failure(self, tmp_path: Path):
        """A totally broken evidence DB must not take down ws/coverage/snapshot."""
        ev = tmp_path / "evidence_forward.db"
        ev.write_bytes(b"garbage")
        hb = _make_hb(forward_status_path=str(ev))
        text = hb._compose_heartbeat()
        assert "ws           : STREAMING" in text
        assert "snapshot     :" in text


# ---------------------------------------------------------------------------
# 7: ASCII-safety
# ---------------------------------------------------------------------------

class TestEvidenceAsciiSafety:
    def test_full_evidence_block_is_ascii(self, tmp_path: Path):
        ev = tmp_path / "evidence_forward.db"
        _build_evidence_db(ev)
        now = datetime.now(timezone.utc)
        _build_h6_db(tmp_path / "forward_evidence.db", [(_ms(now), "ok")])
        hb = _make_hb(forward_status_path=str(ev))
        text = hb._compose_heartbeat()
        text.encode("ascii")  # must not raise

    def test_status_label_has_no_unicode_dash(self, tmp_path: Path):
        ev = tmp_path / "evidence_forward.db"
        _build_evidence_db(ev)
        hb = _make_hb(forward_status_path=str(ev))
        text = hb._compose_heartbeat()
        assert "—" not in text  # em dash
        assert "ACCUMULATING -- NOT VALIDATED" in text


# ---------------------------------------------------------------------------
# 8: severity isolation
# ---------------------------------------------------------------------------

class TestEvidenceSeverityIsolation:
    def test_large_counts_never_escalate_severity(self, tmp_path: Path):
        ev = tmp_path / "evidence_forward.db"
        # Huge counts, but fresh + all ok -> must stay OK.
        _build_evidence_db(ev, sig_counts={"h5_vol_regime": (500, 400, 50)})
        now = datetime.now(timezone.utc)
        _build_h6_db(tmp_path / "forward_evidence.db", [(_ms(now), "ok")])
        hb = _make_hb(forward_status_path=str(ev))
        text = hb._compose_heartbeat()
        assert "[OK]" in text
        assert "rows     : 500 total" in text

    def test_zero_counts_never_escalate_severity(self, tmp_path: Path):
        ev = tmp_path / "evidence_forward.db"
        _build_evidence_db(ev, sig_counts={})
        now = datetime.now(timezone.utc)
        _build_h6_db(tmp_path / "forward_evidence.db", [(_ms(now), "ok")])
        hb = _make_hb(forward_status_path=str(ev))
        text = hb._compose_heartbeat()
        assert "[CRITICAL]" not in text
        assert "[DEGRADED]\n" not in text  # no unhealthy-component block appended

    def test_h6_warning_state_escalates_to_degraded(self, tmp_path: Path):
        ev = tmp_path / "evidence_forward.db"
        _build_evidence_db(ev)
        now = datetime.now(timezone.utc)
        # 2 consecutive no_new_data rows -- no benign precedent, must escalate
        # (unlike a single isolated occurrence, see test below).
        _build_h6_db(tmp_path / "forward_evidence.db", [
            (_ms(now - timedelta(days=1)), "no_new_data"),
            (_ms(now), "no_new_data"),
        ])
        hb = _make_hb(forward_status_path=str(ev))
        text = hb._compose_heartbeat()
        assert text.startswith("--- BTC Bot Heartbeat [DEGRADED]")

    def test_h6_critical_state_escalates_to_critical(self, tmp_path: Path):
        ev = tmp_path / "evidence_forward.db"
        _build_evidence_db(ev)
        _build_h6_db(tmp_path / "forward_evidence.db", [])  # never run -> critical
        hb = _make_hb(forward_status_path=str(ev))
        text = hb._compose_heartbeat()
        assert text.startswith("--- BTC Bot Heartbeat [CRITICAL]")

    def test_h6_isolated_no_new_data_does_not_escalate(self, tmp_path: Path):
        """Regression lock: a single isolated no_new_data day (proven benign
        2026-07-01 / 2026-08-01) must never escalate severity."""
        ev = tmp_path / "evidence_forward.db"
        _build_evidence_db(ev)
        now = datetime.now(timezone.utc)
        _build_h6_db(tmp_path / "forward_evidence.db", [
            (_ms(now - timedelta(days=1)), "ok"),
            (_ms(now), "no_new_data"),
        ])
        hb = _make_hb(forward_status_path=str(ev))
        text = hb._compose_heartbeat()
        assert text.startswith("--- BTC Bot Heartbeat [OK]")


# ---------------------------------------------------------------------------
# 9: fixed, informational status label
# ---------------------------------------------------------------------------

class TestEvidenceStatusLabel:
    def test_label_identical_regardless_of_volume(self, tmp_path: Path):
        ev_small = tmp_path / "small.db"
        ev_big = tmp_path / "big.db"
        _build_evidence_db(ev_small, sig_counts={"h5_vol_regime": (1, 0, 1)})
        _build_evidence_db(ev_big, sig_counts={"h5_vol_regime": (500, 400, 50)})

        hb_small = _make_hb(forward_status_path=str(ev_small))
        hb_big = _make_hb(forward_status_path=str(ev_big))

        assert "status   : ACCUMULATING -- NOT VALIDATED" in hb_small._compose_heartbeat()
        assert "status   : ACCUMULATING -- NOT VALIDATED" in hb_big._compose_heartbeat()

    def test_label_never_says_validated_or_sufficient(self, tmp_path: Path):
        ev = tmp_path / "evidence_forward.db"
        _build_evidence_db(ev, sig_counts={"h5_vol_regime": (500, 400, 50)})
        hb = _make_hb(forward_status_path=str(ev))
        text = hb._compose_heartbeat()
        assert "NOT VALIDATED" in text
        # must never appear as a bare claim of validation/sufficiency
        assert "sufficient" not in text.lower()
        assert text.count("VALIDATED") == 1  # only inside "NOT VALIDATED"


# ---------------------------------------------------------------------------
# 10: existing behavior unchanged
# ---------------------------------------------------------------------------

class TestEvidenceBackCompat:
    def test_no_forward_status_path_omits_evidence_block(self):
        hb = _make_hb(forward_status_path=None)
        text = hb._compose_heartbeat()
        assert "EVIDENCE" not in text
        assert "fwd-evid" not in text

    def test_no_forward_status_path_preserves_other_sections(self):
        hb = _make_hb(forward_status_path=None)
        text = hb._compose_heartbeat()
        assert "BTC Bot Heartbeat" in text
        assert "ws           : STREAMING" in text
        assert "snapshot     :" in text
        assert "-------------------------" in text

    def test_existing_fwd_evid_line_unaffected_by_evidence_block(self, tmp_path: Path):
        ev = tmp_path / "evidence_forward.db"
        _build_evidence_db(ev)
        hb = _make_hb(forward_status_path=str(ev))
        text = hb._compose_heartbeat()
        assert f"fwd-evid     : {_fresh_run_date()}  ok=5/5  fn_hash=ok  gov=ok" in text


# ---------------------------------------------------------------------------
# Reconciler visibility (2026-09-01)
#
# A halted execution stack reports fatal=True, but the reconciler was absent
# from BOTH of _collect_degraded()'s / _health_rollup()'s component lists. The
# halt that latched 2026-08-27 12:34 UTC on a three-minute Delta 502 burst ran
# silently for five days while heartbeats went out every 30 minutes.
# ---------------------------------------------------------------------------

class _StubHealth:
    def __init__(self, healthy, fatal, name, details):
        self.healthy, self.fatal, self.name, self.details = healthy, fatal, name, details


class _StubReconciler:
    def __init__(self, healthy, fatal=False, details="halted: 3 consecutive failures"):
        self._h = _StubHealth(healthy, fatal, "reconciler", details)

    def health(self):
        return self._h


def test_reconciler_absent_by_default():
    """A research-only runtime may omit it; nothing must break."""
    hb = _make_hb()
    assert hb._reconciler is None
    assert isinstance(hb._collect_degraded(), list)


def test_healthy_reconciler_adds_no_line():
    hb = _make_hb()
    hb.set_reconciler(_StubReconciler(healthy=True))
    assert not [d for d in hb._collect_degraded() if "reconciler" in d]


def test_halted_reconciler_is_reported():
    hb = _make_hb()
    hb.set_reconciler(_StubReconciler(healthy=False, fatal=True))
    lines = hb._collect_degraded()
    assert any("reconciler" in d and "halted" in d for d in lines), lines


def test_halted_reconciler_reaches_the_rendered_message():
    hb = _make_hb()
    hb.set_reconciler(_StubReconciler(healthy=False, fatal=True))
    assert "reconciler" in hb._compose_heartbeat()


def test_heartbeat_never_drives_or_clears_reconciliation():
    """Observability only: health() may be called, nothing else."""
    import ast
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "src" / "alerts" / "heartbeat.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    called = {n.func.attr for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    for banned in ("reconcile", "clear_halt_if_clean", "halt",
                   "place_order", "cancel_order", "set_executor"):
        assert banned not in called, f"heartbeat must not call {banned}"
