"""Phase 2 expiry telemetry tests.

Covers:
  - ExpiryAnalyticsWriter  (JSONL append, event structure, error resilience)
  - ExpiryContext telemetry additions  (posture transitions, cycle stats,
    settlement crossings, emit_cycle_summary, analytics=None no-op)
  - HeartbeatService expiry block  (format, None fallback, degraded propagation)

All tests are fully self-contained: no live network, no real files unless
tmp_path is used, no scheduler.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.alerts.heartbeat import HeartbeatService
from src.alerts.rate_limiter import RateLimiter
from src.expiry.expiry_context import ExpiryContext
from src.expiry.models import ExpiryConfig, ExpiryPosture, ExpirySnapshot
from src.reports.eod import ExpiryAnalyticsWriter
from src.runtime.health import HealthStatus
from src.state.market_state import MarketState

_UTC = timezone.utc

# ---------------------------------------------------------------------------
# Reference dates (all settlements are DAILY: Thursday 2026-05-21)
# ---------------------------------------------------------------------------
_TODAY = datetime(2026, 5, 21, 12, 0, 0, tzinfo=_UTC)       # DAILY settle today
_FRIDAY = datetime(2026, 5, 22, 12, 0, 0, tzinfo=_UTC)      # WEEKLY settle tomorrow
_PAST   = datetime(2026, 5, 20, 12, 0, 0, tzinfo=_UTC)      # DAILY settle yesterday (< now)


def _at(dt: datetime):
    return lambda: dt


def _option(symbol, settle_iso, strike=95000.0, contract_type="call_options", atm=0.0):
    return {
        "symbol": symbol,
        "contract_type": contract_type,
        "strike_price": strike,
        "settlement_time": settle_iso,
        "atm_distance": atm,
    }


class _MockRefresher:
    def __init__(self, universe, mark_price=95000.0):
        self._universe = universe
        self._mark_price = mark_price

    def current_snapshot(self):
        class _Snap:
            pass
        s = _Snap()
        s.universe = self._universe
        s.mark_price = self._mark_price
        return s


class _CapturingWriter:
    """Captures calls to ExpiryAnalyticsWriter-compatible methods."""

    def __init__(self):
        self.transitions: list = []
        self.summaries: list = []
        self.trigger_events: list = []

    def settlement_trigger_fired(self, **kwargs):
        self.trigger_events.append(kwargs)

    def posture_transition(self, **kwargs):
        self.transitions.append(kwargs)

    def cycle_summary(self, **kwargs):
        self.summaries.append(kwargs)


# ===========================================================================
# TestExpiryAnalyticsWriter
# ===========================================================================

class TestExpiryAnalyticsWriter:

    def test_posture_transition_writes_jsonl(self, tmp_path):
        path = tmp_path / "tel.jsonl"
        w = ExpiryAnalyticsWriter(path)
        w.posture_transition(
            prev_posture="NORMAL",
            new_posture="APPROACHING",
            seconds_to_nearest=40_000.0,
            nearest_class="DAILY",
            nearest_settlement_iso="2026-05-21T12:00:00+00:00",
            cycle_fraction=0.25,
            observe_count=5,
        )
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["event"] == "posture_transition"
        assert rec["prev_posture"] == "NORMAL"
        assert rec["new_posture"] == "APPROACHING"
        assert rec["nearest_class"] == "DAILY"
        assert rec["observe_count"] == 5

    def test_cycle_summary_writes_jsonl(self, tmp_path):
        path = tmp_path / "tel.jsonl"
        w = ExpiryAnalyticsWriter(path)
        w.cycle_summary(
            cycle_anchor_iso="2026-05-21T12:00:00+00:00",
            posture_ticks={"NORMAL": 100, "APPROACHING": 10},
            class_ticks={"DAILY": 110},
            total_ticks=110,
            divergence_count=0,
            settlement_crossings=[],
            final_snapshot_summary={"posture": "NORMAL", "cycle_fraction": 0.0},
        )
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["event"] == "cycle_summary"
        assert rec["total_observe_ticks"] == 110
        assert rec["posture_ticks"]["NORMAL"] == 100

    def test_multiple_events_append_separate_lines(self, tmp_path):
        path = tmp_path / "tel.jsonl"
        w = ExpiryAnalyticsWriter(path)
        for i in range(3):
            w.append_event({"event": "test", "i": i})
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3
        for i, line in enumerate(lines):
            assert json.loads(line)["i"] == i

    def test_creates_parent_directories(self, tmp_path):
        path = tmp_path / "nested" / "deep" / "tel.jsonl"
        w = ExpiryAnalyticsWriter(path)
        w.append_event({"event": "test"})
        assert path.exists()

    def test_append_event_never_raises_on_bad_path(self):
        w = ExpiryAnalyticsWriter(Path("/no_such_drive_xyz:/bad/path.jsonl"))
        w.append_event({"event": "test"})   # must not raise


# ===========================================================================
# TestExpiryContextTelemetry
# ===========================================================================

class TestExpiryContextTelemetry:

    def _simple_universe(self, settle_iso):
        return [_option("C-BTC-95000-210526", settle_iso)]

    def test_no_transition_on_first_observe(self):
        writer = _CapturingWriter()
        refresher = _MockRefresher(
            universe=self._simple_universe(_TODAY.isoformat()),
        )
        now = _TODAY - timedelta(hours=20)
        ctx = ExpiryContext(
            chain_refresher=refresher,
            now_fn=_at(now),
            analytics_writer=writer,
        )
        ctx.start()
        ctx.observe()   # first tick — prev_posture is None → no transition event
        assert writer.transitions == [], "first observe must not emit posture_transition"

    def test_posture_transition_emitted_on_change(self):
        writer = _CapturingWriter()
        universe = self._simple_universe(_TODAY.isoformat())
        refresher = _MockRefresher(universe=universe)

        now_holder = [_TODAY - timedelta(hours=20)]   # NORMAL posture
        ctx = ExpiryContext(
            chain_refresher=refresher,
            now_fn=lambda: now_holder[0],
            analytics_writer=writer,
        )
        ctx.start()
        ctx.observe()                              # tick 1 — NORMAL, no transition
        assert writer.transitions == []

        now_holder[0] = _TODAY - timedelta(hours=10)   # APPROACHING (< 12h)
        ctx.observe()                              # tick 2 — posture changed
        assert len(writer.transitions) == 1
        t = writer.transitions[0]
        assert t["prev_posture"] == "NORMAL"
        assert t["new_posture"] == "APPROACHING"
        assert t["observe_count"] == 2

    def test_no_transition_when_posture_unchanged(self):
        writer = _CapturingWriter()
        universe = self._simple_universe(_TODAY.isoformat())
        refresher = _MockRefresher(universe=universe)

        now_holder = [_TODAY - timedelta(hours=20)]
        ctx = ExpiryContext(
            chain_refresher=refresher,
            now_fn=lambda: now_holder[0],
            analytics_writer=writer,
        )
        ctx.start()
        ctx.observe()                          # tick 1 — NORMAL
        now_holder[0] = _TODAY - timedelta(hours=18)   # still NORMAL (> 12h)
        ctx.observe()                          # tick 2 — still NORMAL
        assert writer.transitions == []

    def test_settlement_crossing_detected_once(self):
        universe = [_option("C-BTC-95000-200526", _PAST.isoformat())]
        refresher = _MockRefresher(universe=universe)

        # First observe with now BEFORE settlement → positive secs, no crossing
        now_holder = [_PAST - timedelta(hours=1)]
        ctx = ExpiryContext(
            chain_refresher=refresher,
            now_fn=lambda: now_holder[0],
        )
        ctx.start()
        snap1 = ctx.observe()
        assert snap1 is not None
        with ctx._lock:
            crossings_after_1 = list(ctx._cycle_settlement_crossings)
        assert crossings_after_1 == [], "no crossing before settlement"

        # Second observe with now AFTER settlement → crossing detected
        now_holder[0] = _PAST + timedelta(hours=1)
        ctx.observe()
        with ctx._lock:
            crossings_after_2 = list(ctx._cycle_settlement_crossings)
        assert len(crossings_after_2) == 1
        assert crossings_after_2[0]["symbol"] == "C-BTC-95000-200526"

        # Third observe — same symbol, no duplicate crossing
        ctx.observe()
        with ctx._lock:
            crossings_after_3 = list(ctx._cycle_settlement_crossings)
        assert len(crossings_after_3) == 1, "crossing must not be duplicated"

    def test_cycle_stats_accumulate(self):
        universe = self._simple_universe(_TODAY.isoformat())
        refresher = _MockRefresher(universe=universe)

        now_holder = [_TODAY - timedelta(hours=20)]   # NORMAL
        ctx = ExpiryContext(
            chain_refresher=refresher,
            now_fn=lambda: now_holder[0],
        )
        ctx.start()
        for _ in range(3):
            ctx.observe()

        with ctx._lock:
            stats = ctx._cycle_stats
        assert stats["total_ticks"] == 3
        assert stats["posture_ticks"].get("NORMAL", 0) == 3

    def test_analytics_none_is_noop(self):
        universe = self._simple_universe(_TODAY.isoformat())
        refresher = _MockRefresher(universe=universe)

        now_holder = [_TODAY - timedelta(hours=20)]
        ctx = ExpiryContext(
            chain_refresher=refresher,
            now_fn=lambda: now_holder[0],
            analytics_writer=None,
        )
        ctx.start()
        ctx.observe()                        # no analytics → no crash
        now_holder[0] = _TODAY - timedelta(hours=10)
        ctx.observe()                        # posture transition → no crash


# ===========================================================================
# TestExpiryContextEmitCycleSummary
# ===========================================================================

class TestExpiryContextEmitCycleSummary:

    def _ctx_with_writer(self, now_fn):
        writer = _CapturingWriter()
        universe = [
            _option("C-BTC-95000-210526", _TODAY.isoformat()),
            _option("C-BTC-95000-220526", _FRIDAY.isoformat()),
        ]
        refresher = _MockRefresher(universe=universe)
        ctx = ExpiryContext(
            chain_refresher=refresher,
            now_fn=now_fn,
            analytics_writer=writer,
        )
        ctx.start()
        return ctx, writer

    def test_emit_cycle_summary_writes_event(self):
        ctx, writer = self._ctx_with_writer(_at(_TODAY - timedelta(hours=20)))
        ctx.observe()
        ctx.observe()
        ctx.emit_cycle_summary()

        assert len(writer.summaries) == 1
        s = writer.summaries[0]
        assert s["total_ticks"] == 2
        assert "NORMAL" in s["posture_ticks"]

    def test_emit_cycle_summary_resets_stats(self):
        ctx, writer = self._ctx_with_writer(_at(_TODAY - timedelta(hours=20)))
        ctx.observe()
        ctx.observe()
        ctx.emit_cycle_summary()

        with ctx._lock:
            stats_after = dict(ctx._cycle_stats)
            crossings_after = list(ctx._cycle_settlement_crossings)
            settled_after = set(ctx._settled_symbols)

        assert stats_after["total_ticks"] == 0
        assert stats_after["posture_ticks"] == {}
        assert crossings_after == []
        assert settled_after == set()

    def test_emit_cycle_summary_no_analytics(self):
        universe = [_option("C-BTC-95000-210526", _TODAY.isoformat())]
        refresher = _MockRefresher(universe=universe)
        ctx = ExpiryContext(
            chain_refresher=refresher,
            now_fn=_at(_TODAY - timedelta(hours=20)),
            analytics_writer=None,
        )
        ctx.start()
        ctx.observe()
        ctx.emit_cycle_summary()    # must not raise

    def test_cycle_summary_includes_settlement_crossings(self):
        writer = _CapturingWriter()
        past_sym = "C-BTC-95000-200526"
        universe = [_option(past_sym, _PAST.isoformat())]
        refresher = _MockRefresher(universe=universe)

        now_holder = [_PAST - timedelta(hours=1)]
        ctx = ExpiryContext(
            chain_refresher=refresher,
            now_fn=lambda: now_holder[0],
            analytics_writer=writer,
        )
        ctx.start()
        ctx.observe()   # no crossing yet

        now_holder[0] = _PAST + timedelta(hours=1)
        ctx.observe()   # crossing detected

        now_holder[0] = _PAST + timedelta(hours=2)
        ctx.emit_cycle_summary()

        assert len(writer.summaries) == 1
        assert len(writer.summaries[0]["settlement_crossings"]) == 1
        assert writer.summaries[0]["settlement_crossings"][0]["symbol"] == past_sym


# ===========================================================================
# TestHeartbeatExpiryBlock
# ===========================================================================

class _FakeSnap:
    """Minimal namespace pretending to be a ChainRefresher snapshot."""
    def __init__(self, symbols, refreshed_at):
        self.symbols = symbols
        self.refreshed_at = refreshed_at


class _FakeWSManager:
    def health(self):
        return HealthStatus.ok("ws_manager", "state=STREAMING last_msg=2.1s msgs=500 reconnects=0")


class _FakeSnapshotWriter:
    def health(self):
        return HealthStatus.ok("snapshot_writer", "last_snap=45s total_rows=300 last_rows=20 consec_failures=0")


class _FakeChainRefresher:
    def current_snapshot(self):
        return _FakeSnap(symbols=["C-BTC-95000-210526"] * 10, refreshed_at=time.monotonic() - 60)

    def health(self):
        return HealthStatus.ok("chain_refresher", "ok")


def _make_hb(expiry_context=None):
    ms = MarketState(universe=["C-BTC-95000-210526"])
    rl = RateLimiter(default_cooldown_sec=0)
    sent: list = []
    hb = HeartbeatService(
        sender=sent.append,
        ws_manager=_FakeWSManager(),
        market_state=ms,
        snapshot_writer=_FakeSnapshotWriter(),
        chain_refresher=_FakeChainRefresher(),
        rate_limiter=rl,
        started_at=time.monotonic() - 3600,
        expiry_context=expiry_context,
    )
    hb.start()
    return hb, sent


class TestHeartbeatExpiryBlock:

    def _real_expiry_context(self, posture_now: datetime) -> ExpiryContext:
        """Build a real ExpiryContext with a known snapshot."""
        universe = [
            _option("C-BTC-95000-210526", _TODAY.isoformat()),
            _option("C-BTC-90000-210526", _TODAY.isoformat(), strike=90000.0),
            _option("C-BTC-95000-220526", _FRIDAY.isoformat()),
        ]
        refresher = _MockRefresher(universe=universe)
        ctx = ExpiryContext(
            chain_refresher=refresher,
            now_fn=_at(posture_now),
        )
        ctx.start()
        ctx.observe()
        return ctx

    def test_no_expiry_block_when_context_is_none(self):
        hb, sent = _make_hb(expiry_context=None)
        hb.send_heartbeat()
        assert sent
        assert "expiry" not in sent[0]

    def test_expiry_block_not_yet_observed(self):
        # ExpiryContext with no snapshot yet (refresher returns None)
        class _NoSnapRefresher:
            def current_snapshot(self):
                return None
        ctx = ExpiryContext(chain_refresher=_NoSnapRefresher())
        ctx.start()
        # no ctx.observe() — snapshot is None
        hb, sent = _make_hb(expiry_context=ctx)
        hb.send_heartbeat()
        assert "not yet observed" in sent[0]

    def test_expiry_block_format_normal_posture(self):
        # 20h before settlement → NORMAL
        ctx = self._real_expiry_context(_TODAY - timedelta(hours=20))
        hb, sent = _make_hb(expiry_context=ctx)
        hb.send_heartbeat()
        msg = sent[0]
        assert "expiry       : NORMAL" in msg
        assert "cycle=" in msg
        assert "classes    : D:" in msg
        assert "CONF:" in msg
        assert "div:" in msg

    def test_expiry_block_format_approaching_posture(self):
        # 10h before TODAY settlement → APPROACHING
        ctx = self._real_expiry_context(_TODAY - timedelta(hours=10))
        hb, sent = _make_hb(expiry_context=ctx)
        hb.send_heartbeat()
        msg = sent[0]
        assert "expiry       : APPROACHING" in msg
        # nearest settlement should appear in the line
        assert "12:00Z" in msg

    def test_expiry_block_nearest_none_shows_none_string(self):
        # Universe with all contracts settled → nearest=None → NORMAL
        universe = [_option("C-BTC-95000-200526", _PAST.isoformat())]
        refresher = _MockRefresher(universe=universe)
        ctx = ExpiryContext(
            chain_refresher=refresher,
            now_fn=_at(_PAST + timedelta(hours=2)),  # past settlement
        )
        ctx.start()
        ctx.observe()
        hb, sent = _make_hb(expiry_context=ctx)
        hb.send_heartbeat()
        assert "none" in sent[0]

    def test_degraded_expiry_context_appears_in_degraded_block(self):
        class _DegradedExpiryCtx:
            def current_snapshot(self):
                return None
            def health(self):
                return HealthStatus(
                    name="expiry_context",
                    healthy=False,
                    fatal=False,
                    details="no_snapshot observe_count=0 consec_failures=3",
                    last_check_at=datetime.now(_UTC),
                )
        hb, sent = _make_hb(expiry_context=_DegradedExpiryCtx())
        hb.send_heartbeat()
        assert "[DEGRADED]" in sent[0]
        assert "expiry_context" in sent[0]

    def test_healthy_expiry_context_not_in_degraded_block(self):
        ctx = self._real_expiry_context(_TODAY - timedelta(hours=20))
        hb, sent = _make_hb(expiry_context=ctx)
        # Force stale_threshold large so health() is healthy
        ctx._stale_sec = 999_999.0
        hb.send_heartbeat()
        msg = sent[0]
        assert "[DEGRADED]" not in msg

    def test_expiry_block_error_resilience(self):
        class _BrokenCtx:
            def current_snapshot(self):
                raise RuntimeError("explosion")
            def health(self):
                return HealthStatus.ok("expiry_context", "ok")
        hb, sent = _make_hb(expiry_context=_BrokenCtx())
        hb.send_heartbeat()   # must not raise
        assert "error reading context" in sent[0]

    def test_heartbeat_is_pure_ascii(self):
        ctx = self._real_expiry_context(_TODAY - timedelta(hours=5))
        hb, sent = _make_hb(expiry_context=ctx)
        hb.send_heartbeat()
        sent[0].encode("ascii")   # raises if non-ASCII present
