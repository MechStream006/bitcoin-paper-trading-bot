"""Phase D heartbeat observability — universe-age warning (D1) + per-job ages (D2).

Covers:
  TestSchedulerJobAges     -- read-only job_ages() accessor + interval capture
  TestUniverseAgeWarning   -- D1: STALE flag + DEGRADED escalation, kill-switch
  TestPerJobAges           -- D2: jobs line, stall escalation, cron exclusion
  TestPhaseDBackCompat     -- warn/job-ages off preserve prior behavior

No live network, no scheduler start.

Run from repo root:
    conda run -n btc_bot pytest tests/test_heartbeat_phase_d.py -v
"""
from __future__ import annotations

import time

import pytest

from src.alerts.heartbeat import HeartbeatService
from src.alerts.rate_limiter import RateLimiter
from src.runtime.health import HealthStatus
from src.scheduler.runner import JobAge, SchedulerService
from src.state.market_state import MarketState


# ---------------------------------------------------------------------------
# Fakes
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


class _ChainSnap:
    def __init__(self, n, age_sec):
        self.symbols = ["s"] * n
        self.refreshed_at = time.monotonic() - age_sec


class _FakeChain:
    def __init__(self, snap):
        self._snap = snap

    def current_snapshot(self):
        return self._snap

    def health(self):
        return HealthStatus.ok("chain_refresher", "ok")


class _FakeScheduler:
    def __init__(self, ages):
        self._ages = ages

    def job_ages(self):
        return self._ages


def _make_hb(chain=None, interval=None, scheduler=None, emit_job_ages=True):
    ms = MarketState(universe=["C-BTC-95000-280526"])
    ms.update_ticker("C-BTC-95000-280526", {"mark_price": "95100.0"})
    hb = HeartbeatService(
        sender=lambda t: None,
        ws_manager=_FakeWS(),
        market_state=ms,
        snapshot_writer=_FakeSnap(),
        chain_refresher=chain if chain is not None else _FakeChain(_ChainSnap(60, 60)),
        rate_limiter=RateLimiter(default_cooldown_sec=0),
        started_at=time.monotonic() - 100,
        chain_refresh_interval_sec=interval,
        emit_job_ages=emit_job_ages,
    )
    if scheduler is not None:
        hb.set_scheduler(scheduler)
    return hb


# ---------------------------------------------------------------------------
# SchedulerService.job_ages()
# ---------------------------------------------------------------------------

class TestSchedulerJobAges:
    def test_interval_captured_seconds(self):
        svc = SchedulerService()
        svc.register("snap", lambda: None, trigger="interval", seconds=60)
        ages = svc.job_ages()
        assert ages["snap"].interval_sec == 60.0
        assert ages["snap"].last_success_age is None   # never ran

    def test_interval_captured_minutes_hours(self):
        svc = SchedulerService()
        svc.register("m", lambda: None, trigger="interval", minutes=5)
        svc.register("h", lambda: None, trigger="interval", hours=1)
        ages = svc.job_ages()
        assert ages["m"].interval_sec == 300.0
        assert ages["h"].interval_sec == 3600.0

    def test_cron_has_no_interval(self):
        svc = SchedulerService()
        svc.register("eod", lambda: None, trigger="cron", hour=12, minute=5)
        ages = svc.job_ages()
        assert ages["eod"].interval_sec is None

    def test_job_ages_is_read_only_snapshot(self):
        svc = SchedulerService()
        svc.register("snap", lambda: None, trigger="interval", seconds=60)
        a = svc.job_ages()
        a["snap"] = None            # mutating the result must not affect service
        assert svc.job_ages()["snap"].name == "snap"


# ---------------------------------------------------------------------------
# D1 — universe-age warning
# ---------------------------------------------------------------------------

class TestUniverseAgeWarning:
    def test_fresh_universe_not_stale(self):
        hb = _make_hb(chain=_FakeChain(_ChainSnap(60, 1000)), interval=3600)
        text = hb._compose_heartbeat()
        assert "(STALE)" not in text
        assert "[OK]" in text

    def test_old_universe_flagged_stale_and_degraded(self):
        # 8000s > 2 * 3600 = 7200 -> stale
        hb = _make_hb(chain=_FakeChain(_ChainSnap(60, 8000)), interval=3600)
        text = hb._compose_heartbeat()
        assert "(STALE)" in text
        assert "[DEGRADED]" in text

    def test_just_under_threshold_not_stale(self):
        hb = _make_hb(chain=_FakeChain(_ChainSnap(60, 7000)), interval=3600)
        text = hb._compose_heartbeat()
        assert "(STALE)" not in text
        assert "[OK]" in text

    def test_warn_disabled_never_stale(self):
        hb = _make_hb(chain=_FakeChain(_ChainSnap(60, 99999)), interval=None)
        text = hb._compose_heartbeat()
        assert "(STALE)" not in text
        assert "[OK]" in text

    def test_none_snapshot_not_stale(self):
        hb = _make_hb(chain=_FakeChain(None), interval=3600)
        text = hb._compose_heartbeat()
        assert "not yet loaded" in text
        assert "[OK]" in text


# ---------------------------------------------------------------------------
# D2 — per-job last-run ages
# ---------------------------------------------------------------------------

class TestPerJobAges:
    def test_jobs_line_present(self):
        sched = _FakeScheduler({
            "snapshot_quotes": JobAge("snapshot_quotes", 12.0, 60.0),
            "chain_refresh":   JobAge("chain_refresh", 300.0, 3600.0),
        })
        hb = _make_hb(scheduler=sched)
        text = hb._compose_heartbeat()
        assert "jobs" in text
        assert "snap 12s" in text
        assert "chain 5m" in text
        assert "[OK]" in text

    def test_stalled_interval_job_degrades(self):
        # 500s > 3 * 60 = 180 -> stalled
        sched = _FakeScheduler({
            "snapshot_quotes": JobAge("snapshot_quotes", 500.0, 60.0),
        })
        hb = _make_hb(scheduler=sched)
        text = hb._compose_heartbeat()
        assert "[DEGRADED]" in text

    def test_cron_job_never_stalls(self):
        sched = _FakeScheduler({
            "daily_summary": JobAge("daily_summary", 100000.0, None),
        })
        hb = _make_hb(scheduler=sched)
        text = hb._compose_heartbeat()
        assert "daily" in text
        assert "[OK]" in text

    def test_never_succeeded_shows_never_no_escalation(self):
        sched = _FakeScheduler({
            "snapshot_quotes": JobAge("snapshot_quotes", None, 60.0),
        })
        hb = _make_hb(scheduler=sched)
        text = hb._compose_heartbeat()
        assert "snap never" in text
        assert "[OK]" in text

    def test_no_scheduler_no_jobs_line(self):
        hb = _make_hb(scheduler=None)
        text = hb._compose_heartbeat()
        assert "jobs         :" not in text

    def test_bad_scheduler_never_raises(self):
        class _BadSched:
            def job_ages(self):
                raise RuntimeError("boom")
        hb = _make_hb(scheduler=_BadSched())
        text = hb._compose_heartbeat()   # must not raise
        assert "jobs         :" not in text   # None job_ages -> no line
        assert "BTC Bot Heartbeat" in text


class TestPhaseDBackCompat:
    def test_job_ages_disabled_no_line(self):
        sched = _FakeScheduler({"snapshot_quotes": JobAge("snapshot_quotes", 12.0, 60.0)})
        hb = _make_hb(scheduler=sched, emit_job_ages=False)
        text = hb._compose_heartbeat()
        assert "jobs         :" not in text

    def test_ascii_clean(self):
        sched = _FakeScheduler({"snapshot_quotes": JobAge("snapshot_quotes", 12.0, 60.0)})
        hb = _make_hb(chain=_FakeChain(_ChainSnap(60, 8000)), interval=3600, scheduler=sched)
        hb._compose_heartbeat().encode("ascii")   # must not raise
