"""Phase C heartbeat observability — per-interval deltas + worst staleness age.

Covers:
  TestDeltaTracker        -- first/normal/reset/missed/read-fail/restart correctness
  TestHeartbeatDeltas     -- deltas line: first sample, real deltas, reset, missed send
  TestWorstStaleness      -- worst staleness age on the coverage line
  TestPhaseCBackCompat    -- emit_deltas=False reverts both lines

The deltas are display-only; none of these tests expect a status escalation from
Phase C metrics.

Run from repo root:
    conda run -n btc_bot pytest tests/test_heartbeat_phase_c.py -v
"""
from __future__ import annotations

import time

import pytest

from src.alerts.delta_tracker import DeltaTracker
from src.alerts.heartbeat import HeartbeatService
from src.alerts.rate_limiter import RateLimiter
from src.runtime.health import HealthStatus
from src.state.market_state import MarketState


# ---------------------------------------------------------------------------
# Mutable fakes (counters advance between heartbeats)
# ---------------------------------------------------------------------------

class _FakeWS:
    def __init__(self, msgs=100, reconnects=0):
        self.msgs = msgs
        self.reconnects = reconnects

    def health(self):
        return HealthStatus.ok(
            "ws_manager",
            f"state=STREAMING last_msg=2.1s msgs={self.msgs} reconnects={self.reconnects}")


class _FakeSnap:
    def __init__(self, rows=30):
        self.rows = rows

    def health(self):
        return HealthStatus.ok(
            "snapshot_writer",
            f"last_snap=45s total_rows={self.rows} last_rows=20 consec_failures=0")


class _FakeChain:
    def current_snapshot(self):
        return None

    def health(self):
        return HealthStatus.ok("chain_refresher", "ok")


class _FakeMarket:
    """Controllable market state for worst-staleness tests."""
    def __init__(self, symbols, ages):
        self._symbols = symbols
        self._ages = ages   # symbol -> age sec (None = never ticked)

    def coverage_pct(self):
        seen = sum(1 for a in self._ages.values() if a is not None)
        return seen / len(self._symbols) if self._symbols else 0.0

    def universe_symbols(self):
        return list(self._symbols)

    def stale_symbols(self, threshold_sec):
        return [s for s in self._symbols
                if (self._ages.get(s) is None or self._ages[s] > threshold_sec)]

    def freshness_sec(self, symbol):
        return self._ages.get(symbol)


def _make_hb(ws=None, snap=None, market=None, emit_deltas=True, sender=None):
    if market is None:
        market = MarketState(universe=["C-BTC-95000-280526"])
        market.update_ticker("C-BTC-95000-280526", {"mark_price": "95100.0"})
    return HeartbeatService(
        sender=sender if sender is not None else (lambda t: None),
        ws_manager=ws if ws is not None else _FakeWS(),
        market_state=market,
        snapshot_writer=snap if snap is not None else _FakeSnap(),
        chain_refresher=_FakeChain(),
        rate_limiter=RateLimiter(default_cooldown_sec=0),
        started_at=time.monotonic() - 100,
        emit_deltas=emit_deltas,
    )


# ---------------------------------------------------------------------------
# DeltaTracker
# ---------------------------------------------------------------------------

class TestDeltaTracker:
    def test_first_cycle_all_na(self):
        t = DeltaTracker()
        d, e = t.compute({"msgs": 100, "rows": 30}, now=1000.0)
        assert d == {"msgs": None, "rows": None}
        assert e is None

    def test_normal_delta(self):
        t = DeltaTracker()
        t.commit({"msgs": 100, "rows": 30}, now=1000.0)
        d, e = t.compute({"msgs": 640, "rows": 60}, now=1030.0)
        assert d == {"msgs": 540, "rows": 30}
        assert e == 30.0

    def test_counter_reset_is_na(self):
        t = DeltaTracker()
        t.commit({"msgs": 100}, now=1000.0)
        d, _ = t.compute({"msgs": 5}, now=1030.0)
        assert d["msgs"] is None

    def test_missed_interval_spans_true_window(self):
        t = DeltaTracker()
        t.commit({"msgs": 100}, now=1000.0)
        d, e = t.compute({"msgs": 700}, now=1600.0)   # 600s gap
        assert d["msgs"] == 600 and e == 600.0

    def test_read_failure_needs_two_clean_reads(self):
        t = DeltaTracker()
        t.commit({"msgs": 100, "rows": 30}, now=1000.0)
        # rows None this cycle -> n/a, and dropped from baseline on commit
        d, _ = t.compute({"msgs": 200, "rows": None}, now=1030.0)
        assert d["rows"] is None and d["msgs"] == 100
        t.commit({"msgs": 200, "rows": None}, now=1030.0)
        d2, _ = t.compute({"msgs": 300, "rows": 90}, now=1060.0)
        assert d2["rows"] is None   # rows still n/a until two clean reads
        assert d2["msgs"] == 100

    def test_restart_has_no_baseline(self):
        t = DeltaTracker()
        assert not t.has_baseline()
        d, e = t.compute({"msgs": 50}, now=2000.0)
        assert d["msgs"] is None and e is None


# ---------------------------------------------------------------------------
# Heartbeat deltas line
# ---------------------------------------------------------------------------

class TestHeartbeatDeltas:
    def test_first_heartbeat_shows_na(self):
        sent = []
        hb = _make_hb(sender=sent.append)
        hb.send_heartbeat()
        assert "deltas" in sent[0]
        assert "msgs n/a" in sent[0]
        assert "(first sample)" in sent[0]

    def test_second_heartbeat_shows_real_deltas(self):
        sent = []
        ws, snap = _FakeWS(msgs=100), _FakeSnap(rows=30)
        hb = _make_hb(ws=ws, snap=snap, sender=sent.append)
        hb.send_heartbeat()                 # first: baseline committed
        ws.msgs, snap.rows = 640, 60
        hb.send_heartbeat()                 # second: deltas computed
        assert "msgs +540" in sent[1]
        assert "rows +30" in sent[1]
        assert "reconn +0" in sent[1]

    def test_counter_reset_renders_na(self):
        sent = []
        ws = _FakeWS(msgs=100)
        hb = _make_hb(ws=ws, sender=sent.append)
        hb.send_heartbeat()
        ws.msgs = 5                         # reset (reconnect / restart)
        hb.send_heartbeat()
        assert "msgs n/a" in sent[1]

    def test_failed_send_does_not_advance_baseline(self):
        # A failed send must not commit the baseline: the next successful send is
        # treated as the first sample (n/a), never a bogus delta.
        calls = {"n": 0}

        def flaky(_text):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("telegram down")

        sent = []

        def sender(text):
            flaky(text)
            sent.append(text)

        ws = _FakeWS(msgs=100)
        hb = _make_hb(ws=ws, sender=sender)
        hb.send_heartbeat()                 # send fails -> baseline NOT committed
        ws.msgs = 640
        hb.send_heartbeat()                 # success, but still first sample
        assert "msgs n/a" in sent[0]
        assert "(first sample)" in sent[0]
        ws.msgs = 700
        hb.send_heartbeat()                 # now a real delta
        assert "msgs +60" in sent[1]

    def test_deltas_compose_never_raises_on_bad_counter(self):
        class _BadWS:
            def health(self):
                return HealthStatus.ok("ws_manager", "state=STREAMING msgs=NaN reconnects=x")
        hb = _make_hb(ws=_BadWS())
        text = hb._compose_heartbeat()      # must not raise
        assert "msgs n/a" in text


class TestWorstStaleness:
    def test_worst_age_appended_when_stale(self):
        market = _FakeMarket(
            symbols=["a", "b", "c"],
            ages={"a": 5.0, "b": 65.0, "c": 12.0},   # b is stale (>30s)
        )
        hb = _make_hb(market=market)
        text = hb._compose_heartbeat()
        assert "1 stale" in text
        assert "worst 1m" in text            # 65s -> "1m 05s"

    def test_no_worst_when_all_fresh(self):
        market = _FakeMarket(symbols=["a"], ages={"a": 5.0})
        hb = _make_hb(market=market)
        text = hb._compose_heartbeat()
        assert "all fresh" in text
        assert "worst" not in text

    def test_never_ticked_excluded_from_worst(self):
        # never-ticked symbol is a coverage gap, not staleness -> no worst age
        market = _FakeMarket(symbols=["a"], ages={"a": None})
        hb = _make_hb(market=market)
        text = hb._compose_heartbeat()
        assert "stale" in text
        assert "worst" not in text           # freshness_sec None -> excluded


class TestPhaseCBackCompat:
    def test_emit_deltas_false_no_deltas_line(self):
        hb = _make_hb(emit_deltas=False)
        text = hb._compose_heartbeat()
        assert "deltas" not in text

    def test_emit_deltas_false_no_worst_on_coverage(self):
        market = _FakeMarket(symbols=["a"], ages={"a": 65.0})
        hb = _make_hb(market=market, emit_deltas=False)
        text = hb._compose_heartbeat()
        assert "1 stale" in text
        assert "worst" not in text

    def test_ascii_clean(self):
        sent = []
        hb = _make_hb(sender=sent.append)
        hb.send_heartbeat()
        sent[0].encode("ascii")              # must not raise
