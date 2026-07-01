"""Phase B heartbeat observability — disk %, db/wal sizes, write-failure reason.

Covers:
  TestStorageStatus        -- disk_free / db_sizes / human_bytes / severity (read-only, total)
  TestSnapshotFailReason   -- SnapshotWriter retains + clears last_failure_reason
  TestHeartbeatStorageLine -- storage line present/absent, disk folds into status
  TestHeartbeatSnapReason  -- write-failure reason surfaced on the snapshot line
  TestPhaseBBackCompat     -- data_db_path=None preserves prior behavior

No live network, no live disk pressure (disk escalation tested via crafted DiskInfo
and monkeypatched disk_free).

Run from repo root:
    conda run -n btc_bot pytest tests/test_heartbeat_phase_b.py -v
"""
from __future__ import annotations

import sqlite3
import time

import pytest

from src.alerts import storage_status as ss
from src.alerts.heartbeat import HeartbeatService
from src.alerts.rate_limiter import RateLimiter
from src.runtime.health import HealthStatus
from src.state.market_state import MarketState


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeWS:
    def health(self):
        return HealthStatus.ok("ws_manager", "state=STREAMING last_msg=2.1s msgs=500 reconnects=0")


class _FakeSnap:
    """Snapshot writer fake: healthy by default; can carry a failure reason."""
    def __init__(self, healthy=True, reason=None, cf=0):
        self._healthy = healthy
        self._reason = reason
        self._cf = cf

    def health(self):
        if self._healthy:
            return HealthStatus.ok(
                "snapshot_writer",
                "last_snap=45s total_rows=300 last_rows=20 consec_failures=0")
        return HealthStatus.degraded(
            "snapshot_writer",
            f"last_snap=120s total_rows=300 last_rows=0 consec_failures={self._cf}")

    def last_failure_reason(self):
        return self._reason


class _FakeChain:
    def current_snapshot(self):
        return None

    def health(self):
        return HealthStatus.ok("chain_refresher", "ok")


def _make_hb(data_db_path=None, forward_path=None, snap=None):
    ms = MarketState(universe=["C-BTC-95000-280526"])
    ms.update_ticker("C-BTC-95000-280526", {"mark_price": "95100.0"})
    return HeartbeatService(
        sender=lambda t: None,
        ws_manager=_FakeWS(),
        market_state=ms,
        snapshot_writer=snap if snap is not None else _FakeSnap(),
        chain_refresher=_FakeChain(),
        rate_limiter=RateLimiter(default_cooldown_sec=0),
        started_at=time.monotonic() - 100,
        forward_status_path=forward_path,
        data_db_path=data_db_path,
    )


# ---------------------------------------------------------------------------
# storage_status
# ---------------------------------------------------------------------------

class TestStorageStatus:
    def test_human_bytes(self):
        assert ss.human_bytes(0) == "0B"
        assert ss.human_bytes(1024) == "1KB"
        assert ss.human_bytes(41 * 1024 * 1024) == "41MB"
        assert ss.human_bytes(int(1.1 * 1024 ** 3)) == "1.1GB"
        assert ss.human_bytes(None) == "?"

    def test_disk_free_real_dir(self, tmp_path):
        di = ss.disk_free(str(tmp_path))
        assert di.ok and di.free_pct is not None
        assert 0 <= di.free_pct <= 100

    def test_disk_free_missing_is_neutral(self):
        di = ss.disk_free("/no/such/volume")
        assert not di.ok
        assert di.severity() == "neutral"

    def test_disk_severity_thresholds(self):
        assert ss.DiskInfo(True, 3.0, 1, 100).severity() == "critical"
        assert ss.DiskInfo(True, 10.0, 1, 100).severity() == "degraded"
        assert ss.DiskInfo(True, 50.0, 1, 100).severity() == "ok"
        assert ss.DiskInfo(False, None, None, None).severity() == "neutral"

    def test_db_sizes_real(self, tmp_path):
        p = tmp_path / "x.db"
        c = sqlite3.connect(str(p))
        c.execute("PRAGMA journal_mode=WAL;")
        c.execute("CREATE TABLE t (a INTEGER);")
        c.execute("INSERT INTO t VALUES (1);")
        c.commit()
        f = ss.db_sizes(str(p))
        c.close()
        assert f.ok and f.db_bytes > 0

    def test_db_sizes_missing_never_raises(self):
        f = ss.db_sizes("/no/such/file.db")
        assert not f.ok and f.db_bytes == 0


# ---------------------------------------------------------------------------
# SnapshotWriter.last_failure_reason  (real object, no DB writes asserted)
# ---------------------------------------------------------------------------

class TestSnapshotFailReason:
    def _writer(self):
        from src.persistence.snapshot_writer import SnapshotWriter
        conn = sqlite3.connect(":memory:")
        return SnapshotWriter(conn=conn, state=MarketState(universe=["X"]))

    def test_initial_reason_is_none(self):
        w = self._writer()
        assert w.last_failure_reason() is None

    def test_record_failure_sets_reason(self):
        w = self._writer()
        try:
            w._record_failure(ValueError("disk I/O error"), context="commit")
        except Exception:
            pass
        r = w.last_failure_reason()
        assert r is not None
        assert "ValueError@commit" in r
        assert "disk I/O error" in r

    def test_reason_is_single_line_bounded(self):
        w = self._writer()
        try:
            w._record_failure(RuntimeError("x\ny\n" + "z" * 500), context="ctx")
        except Exception:
            pass
        r = w.last_failure_reason()
        assert "\n" not in r
        assert len(r) <= 120


# ---------------------------------------------------------------------------
# Heartbeat storage line + disk escalation
# ---------------------------------------------------------------------------

class TestHeartbeatStorageLine:
    def test_storage_line_present_when_configured(self, tmp_path):
        p = tmp_path / "btc.db"
        sqlite3.connect(str(p)).close()
        hb = _make_hb(data_db_path=str(p))
        text = hb._compose_heartbeat()
        assert "storage" in text
        assert "disk" in text

    def test_storage_line_absent_when_not_configured(self):
        hb = _make_hb(data_db_path=None)
        text = hb._compose_heartbeat()
        assert "storage      :" not in text

    def test_critical_disk_sets_header(self, tmp_path, monkeypatch):
        p = tmp_path / "btc.db"
        sqlite3.connect(str(p)).close()
        monkeypatch.setattr(
            "src.alerts.storage_status.disk_free",
            lambda path: ss.DiskInfo(True, 2.0, 100, 5000),
        )
        hb = _make_hb(data_db_path=str(p))
        text = hb._compose_heartbeat()
        assert "[CRITICAL]" in text

    def test_low_disk_sets_degraded(self, tmp_path, monkeypatch):
        p = tmp_path / "btc.db"
        sqlite3.connect(str(p)).close()
        monkeypatch.setattr(
            "src.alerts.storage_status.disk_free",
            lambda path: ss.DiskInfo(True, 12.0, 600, 5000),
        )
        hb = _make_hb(data_db_path=str(p))
        text = hb._compose_heartbeat()
        assert "[DEGRADED]" in text

    def test_unknown_disk_is_neutral(self, tmp_path, monkeypatch):
        p = tmp_path / "btc.db"
        sqlite3.connect(str(p)).close()
        monkeypatch.setattr(
            "src.alerts.storage_status.disk_free",
            lambda path: ss.DiskInfo(False, None, None, None, "err"),
        )
        hb = _make_hb(data_db_path=str(p))
        text = hb._compose_heartbeat()
        assert "[OK]" in text
        assert "disk n/a" in text

    def test_compose_never_raises_on_storage_error(self, tmp_path, monkeypatch):
        p = tmp_path / "btc.db"
        sqlite3.connect(str(p)).close()
        monkeypatch.setattr(
            "src.alerts.storage_status.disk_free",
            lambda path: (_ for _ in ()).throw(OSError("boom")),
        )
        hb = _make_hb(data_db_path=str(p))
        text = hb._compose_heartbeat()   # must not raise
        assert "BTC Bot Heartbeat" in text


class TestHeartbeatSnapReason:
    def test_reason_surfaced_on_snapshot_line(self):
        snap = _FakeSnap(healthy=False, reason="OperationalError@commit: disk I/O error", cf=3)
        hb = _make_hb(snap=snap)
        text = hb._compose_heartbeat()
        assert "cf=3" in text
        assert "reason=OperationalError@commit: disk I/O error" in text

    def test_no_reason_when_healthy(self):
        hb = _make_hb(snap=_FakeSnap(healthy=True))
        text = hb._compose_heartbeat()
        assert "reason=" not in text

    def test_missing_accessor_is_tolerated(self):
        class _OldSnap:
            def health(self):
                return HealthStatus.degraded(
                    "snapshot_writer", "consec_failures=2")
        hb = _make_hb(snap=_OldSnap())
        text = hb._compose_heartbeat()   # must not raise
        assert "cf=2" in text
        assert "reason=" not in text


class TestPhaseBBackCompat:
    def test_no_data_db_path_preserves_behavior(self):
        hb = _make_hb(data_db_path=None)
        text = hb._compose_heartbeat()
        assert "[OK]" in text
        assert "storage      :" not in text
        text.encode("ascii")
