"""MetricsExporter -- ADR-0001 Phase 2 in-process Prometheus exporter.

Covers: localhost-only binding, never-raise behavior per metric group (and
end-to-end when every stub component is broken), correct parsing of existing
health()-details strings, the forward-evidence cache-only contract (never
touches evidence_forward.db itself), scheduler job-age labeling, the generic
component-health rollup, and idempotent start/stop.

Run from repo root:
    conda run -n btc_bot pytest tests/test_metrics_exporter.py -v
"""
from __future__ import annotations

import time
import urllib.request
from datetime import datetime, timezone

import pytest

from src.alerts import forward_status as fs_mod
from src.observability.metrics_exporter import MetricsExporter, _days_behind, _parse_age_seconds
from src.runtime.health import HealthStatus
from src.state.market_state import MarketState


# ---------------------------------------------------------------------------
# Stub components (mirrors the style of heartbeat.py's own __main__ stubs)
# ---------------------------------------------------------------------------

class _FakeWSManager:
    def __init__(self, healthy=True, msgs=500, reconnects=2, last_msg="2.1s"):
        self._healthy = healthy
        self._details = f"state=STREAMING last_msg={last_msg} msgs={msgs} reconnects={reconnects}"

    def health(self):
        return HealthStatus(
            name="ws_manager", healthy=self._healthy, fatal=False,
            details=self._details, last_check_at=datetime.now(timezone.utc),
        )


class _RaisingComponent:
    def health(self):
        raise RuntimeError("boom")


class _FakeSnapshotWriter:
    def health(self):
        return HealthStatus.ok("snapshot_writer", "last_snap=10s total_rows=100 last_rows=5 consec_failures=0")


class _FakeChainRefresher:
    def health(self):
        return HealthStatus.ok("chain_refresher", "ok")


class _JobAge:
    def __init__(self, last_success_age, interval_sec=60.0):
        self.last_success_age = last_success_age
        self.interval_sec = interval_sec


class _FakeScheduler:
    def __init__(self, healthy=True, job_ages=None, raise_on_job_ages=False):
        self._healthy = healthy
        self._job_ages = job_ages or {}
        self._raise_on_job_ages = raise_on_job_ages

    def health(self):
        return HealthStatus(
            name="scheduler", healthy=self._healthy, fatal=False,
            details="running=True worst_cf=0", last_check_at=datetime.now(timezone.utc),
        )

    def job_ages(self):
        if self._raise_on_job_ages:
            raise RuntimeError("job_ages boom")
        return self._job_ages


class _FakeHeartbeat:
    def __init__(self, healthy=True, total_sent=3, last_send="5s", forward_status=None):
        self._healthy = healthy
        self._details = f"total_sent={total_sent} last_send={last_send}"
        self._forward_status = forward_status

    def health(self):
        return HealthStatus(
            name="heartbeat", healthy=self._healthy, fatal=False,
            details=self._details, last_check_at=datetime.now(timezone.utc),
        )

    def last_forward_status(self):
        return self._forward_status


def _make_market_state(universe_n=4, ticked_n=3):
    universe = [f"C-BTC-{95000 + i}-280526" for i in range(universe_n)]
    ms = MarketState(universe=universe)
    for sym in universe[:ticked_n]:
        ms.update_ticker(sym, {"mark_price": "95000.0"})
    return ms


def _make_exporter(**overrides):
    defaults = dict(
        market_state=_make_market_state(),
        ws_manager=_FakeWSManager(),
        snapshot_writer=_FakeSnapshotWriter(),
        chain_refresher=_FakeChainRefresher(),
        scheduler=_FakeScheduler(),
        heartbeat=_FakeHeartbeat(),
    )
    defaults.update(overrides)
    return MetricsExporter(**defaults)


def _samples_by_name(families):
    """Flatten collect() output into {metric_name: [(labels_dict, value), ...]}."""
    out: dict = {}
    for fam in families:
        for sample in fam.samples:
            out.setdefault(sample.name, []).append((sample.labels, sample.value))
    return out


# ---------------------------------------------------------------------------
# Helper function unit tests
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_parse_age_seconds(self):
        assert _parse_age_seconds("2.1s") == 2.1
        assert _parse_age_seconds("never") is None
        assert _parse_age_seconds(None) is None
        assert _parse_age_seconds("garbage") is None

    def test_days_behind(self):
        assert _days_behind(None, "2026-06-30") is None
        assert _days_behind("2026-06-28", "2026-06-30") == 2
        assert _days_behind("2026-06-30", "2026-06-30") == 0
        # latest ahead of expected clamps to 0, never negative
        assert _days_behind("2026-07-05", "2026-06-30") == 0


# ---------------------------------------------------------------------------
# Metric content correctness
# ---------------------------------------------------------------------------

class TestMarketStateMetrics:
    def test_coverage_universe_stale_reconcile(self):
        ms = _make_market_state(universe_n=4, ticked_n=3)
        exp = _make_exporter(market_state=ms)
        samples = _samples_by_name(exp.collect())

        assert samples["btcbot_universe_size"][0][1] == 4.0
        assert samples["btcbot_coverage_ratio"][0][1] == pytest.approx(0.75)
        # 1 symbol never ticked -> stale under any positive threshold
        assert samples["btcbot_stale_symbols"][0][1] == 1.0
        assert samples["btcbot_reconcile_count_total"][0][1] == 0.0

    def test_reconcile_count_reflects_real_reconciles(self):
        ms = _make_market_state(universe_n=2, ticked_n=2)
        universe = ms.universe_symbols()
        ms.reconcile_universe(universe, [universe[0], "C-BTC-99999-280526"])
        exp = _make_exporter(market_state=ms)
        samples = _samples_by_name(exp.collect())
        assert samples["btcbot_reconcile_count_total"][0][1] == 1.0


class TestWSMetrics:
    def test_healthy_ws_parsed(self):
        exp = _make_exporter(ws_manager=_FakeWSManager(healthy=True, msgs=500, reconnects=2, last_msg="2.1s"))
        samples = _samples_by_name(exp.collect())
        assert samples["btcbot_ws_connected"][0][1] == 1.0
        assert samples["btcbot_ws_messages_total"][0][1] == 500.0
        assert samples["btcbot_ws_reconnects_total"][0][1] == 2.0
        assert samples["btcbot_ws_last_message_age_seconds"][0][1] == pytest.approx(2.1)

    def test_never_ticked_omits_age_metric(self):
        exp = _make_exporter(ws_manager=_FakeWSManager(last_msg="never"))
        samples = _samples_by_name(exp.collect())
        assert "btcbot_ws_last_message_age_seconds" not in samples

    def test_unhealthy_ws_reported(self):
        exp = _make_exporter(ws_manager=_FakeWSManager(healthy=False))
        samples = _samples_by_name(exp.collect())
        assert samples["btcbot_ws_connected"][0][1] == 0.0


class TestSchedulerMetrics:
    def test_job_ages_labeled(self):
        ages = {"snapshot_quotes": _JobAge(12.5), "chain_refresh": _JobAge(300.0), "never_ran": _JobAge(None)}
        exp = _make_exporter(scheduler=_FakeScheduler(job_ages=ages))
        samples = _samples_by_name(exp.collect())
        job_samples = {labels["job"]: val for labels, val in samples["btcbot_scheduler_job_last_success_age_seconds"]}
        assert job_samples["snapshot_quotes"] == 12.5
        assert job_samples["chain_refresh"] == 300.0
        assert "never_ran" not in job_samples   # None last_success_age is skipped

    def test_scheduler_healthy_flag(self):
        exp = _make_exporter(scheduler=_FakeScheduler(healthy=False))
        samples = _samples_by_name(exp.collect())
        assert samples["btcbot_scheduler_healthy"][0][1] == 0.0

    def test_job_ages_exception_does_not_break_other_groups(self):
        exp = _make_exporter(scheduler=_FakeScheduler(raise_on_job_ages=True))
        samples = _samples_by_name(exp.collect())
        # scheduler_healthy still present (job_ages() failure is isolated)
        assert "btcbot_scheduler_healthy" in samples
        assert "btcbot_scheduler_job_last_success_age_seconds" not in samples
        # unrelated groups still present
        assert "btcbot_universe_size" in samples


class TestHeartbeatMetrics:
    def test_healthy_heartbeat_parsed(self):
        exp = _make_exporter(heartbeat=_FakeHeartbeat(healthy=True, total_sent=7, last_send="5s"))
        samples = _samples_by_name(exp.collect())
        assert samples["btcbot_heartbeat_healthy"][0][1] == 1.0
        assert samples["btcbot_heartbeat_sent_total"][0][1] == 7.0
        assert samples["btcbot_heartbeat_last_send_age_seconds"][0][1] == pytest.approx(5.0)

    def test_never_sent_omits_age_metric(self):
        exp = _make_exporter(heartbeat=_FakeHeartbeat(last_send="never"))
        samples = _samples_by_name(exp.collect())
        assert "btcbot_heartbeat_last_send_age_seconds" not in samples


class TestForwardEvidenceMetrics:
    """Forward-evidence metrics must come ONLY from the heartbeat's cache."""

    def test_none_cache_emits_nothing(self):
        exp = _make_exporter(heartbeat=_FakeHeartbeat(forward_status=None))
        samples = _samples_by_name(exp.collect())
        assert not any(name.startswith("btcbot_forward_") for name in samples)

    def test_ok_status_metrics(self):
        status = fs_mod.ForwardStatus(
            state="ok", latest_run_date="2026-06-29", expected_run_date="2026-06-29",
            signals={s: "ok" for s in fs_mod.CANONICAL_SIGNALS}, n_ok=5, n_logged=5,
        )
        exp = _make_exporter(heartbeat=_FakeHeartbeat(forward_status=status))
        samples = _samples_by_name(exp.collect())
        assert samples["btcbot_forward_days_behind"][0][1] == 0.0
        assert samples["btcbot_forward_signals_ok"][0][1] == 5.0
        assert samples["btcbot_forward_signals_logged"][0][1] == 5.0
        assert samples["btcbot_forward_fn_hash_violations"][0][1] == 0.0

    def test_stale_status_days_behind(self):
        status = fs_mod.ForwardStatus(
            state="stale", latest_run_date="2026-06-25", expected_run_date="2026-06-29",
            signals={s: "ok" for s in fs_mod.CANONICAL_SIGNALS}, n_ok=5, n_logged=5,
        )
        exp = _make_exporter(heartbeat=_FakeHeartbeat(forward_status=status))
        samples = _samples_by_name(exp.collect())
        assert samples["btcbot_forward_days_behind"][0][1] == 4.0

    def test_fn_hash_violation_counted(self):
        signals = {s: "ok" for s in fs_mod.CANONICAL_SIGNALS}
        signals["g3_atr_expansion"] = "fn_hash_violation"
        status = fs_mod.ForwardStatus(
            state="critical", latest_run_date="2026-06-29", expected_run_date="2026-06-29",
            signals=signals, n_ok=4, n_logged=5,
        )
        exp = _make_exporter(heartbeat=_FakeHeartbeat(forward_status=status))
        samples = _samples_by_name(exp.collect())
        assert samples["btcbot_forward_fn_hash_violations"][0][1] == 1.0

    def test_missing_state_has_no_latest_date_but_other_metrics_present(self):
        status = fs_mod.ForwardStatus.missing(expected="2026-06-29")
        exp = _make_exporter(heartbeat=_FakeHeartbeat(forward_status=status))
        samples = _samples_by_name(exp.collect())
        assert "btcbot_forward_days_behind" not in samples   # latest_run_date is None
        assert samples["btcbot_forward_signals_ok"][0][1] == 0.0

    def test_collect_never_touches_evidence_forward_db(self, monkeypatch):
        """Guard against regression: collect() must never call summarize()."""
        called = {"n": 0}

        def _poisoned_summarize(*a, **kw):
            called["n"] += 1
            raise AssertionError("collect() must never call forward_status.summarize()")

        monkeypatch.setattr(fs_mod, "summarize", _poisoned_summarize)
        status = fs_mod.ForwardStatus(
            state="ok", latest_run_date="2026-06-29", expected_run_date="2026-06-29",
            signals={s: "ok" for s in fs_mod.CANONICAL_SIGNALS}, n_ok=5, n_logged=5,
        )
        exp = _make_exporter(heartbeat=_FakeHeartbeat(forward_status=status))
        list(exp.collect())
        assert called["n"] == 0


class TestComponentHealthRollup:
    def test_all_components_labeled(self):
        exp = _make_exporter()
        samples = _samples_by_name(exp.collect())
        labels = {l["component"] for l, v in samples["btcbot_component_healthy"]}
        assert labels == {"ws_manager", "snapshot_writer", "chain_refresher", "scheduler", "heartbeat"}

    def test_expiry_context_included_when_provided(self):
        class _FakeExpiry:
            def health(self):
                return HealthStatus.ok("expiry_context", "ok")

        exp = _make_exporter(expiry_context=_FakeExpiry())
        samples = _samples_by_name(exp.collect())
        labels = {l["component"] for l, v in samples["btcbot_component_healthy"]}
        assert "expiry_context" in labels


# ---------------------------------------------------------------------------
# Never-raise guarantee (M5) -- end to end with every component broken
# ---------------------------------------------------------------------------

class TestNeverRaise:
    def test_all_components_raising_does_not_raise(self):
        exp = MetricsExporter(
            market_state=_make_market_state(),   # MarketState itself is not stubbed-broken
            ws_manager=_RaisingComponent(),
            snapshot_writer=_RaisingComponent(),
            chain_refresher=_RaisingComponent(),
            scheduler=_RaisingComponent(),
            heartbeat=_RaisingComponent(),
        )
        # Must not raise. Market-state metrics still present; everything else
        # gracefully absent.
        samples = _samples_by_name(exp.collect())
        assert "btcbot_universe_size" in samples
        assert "btcbot_ws_connected" not in samples
        assert "btcbot_component_healthy" not in samples  # every comp.health() raised

    def test_broken_market_state_does_not_break_other_groups(self):
        class _BrokenMarketState:
            def universe_size(self):
                raise RuntimeError("boom")
            def coverage_pct(self):
                raise RuntimeError("boom")
            def stale_symbols(self, threshold_sec):
                raise RuntimeError("boom")
            def reconcile_count(self):
                raise RuntimeError("boom")

        exp = _make_exporter(market_state=_BrokenMarketState())
        samples = _samples_by_name(exp.collect())
        assert "btcbot_universe_size" not in samples
        assert "btcbot_ws_connected" in samples   # unrelated group unaffected


# ---------------------------------------------------------------------------
# HTTP server: binding, localhost-only, real scrape, lifecycle
# ---------------------------------------------------------------------------

class TestHttpServer:
    def test_binds_127_0_0_1_only(self):
        exp = _make_exporter(port=0)   # port 0 -> OS picks an ephemeral free port
        try:
            exp.start()
            assert exp._server is not None
            assert exp._server.server_address[0] == "127.0.0.1"
        finally:
            exp.stop()

    def test_real_scrape_returns_expected_metrics(self):
        exp = _make_exporter(port=0)
        try:
            exp.start()
            port = exp._server.server_address[1]
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5) as resp:
                body = resp.read().decode("utf-8")
            assert "btcbot_coverage_ratio" in body
            assert "btcbot_universe_size" in body
            assert "btcbot_component_healthy" in body
        finally:
            exp.stop()

    def test_stop_before_start_is_safe(self):
        exp = _make_exporter(port=0)
        exp.stop()   # must not raise

    def test_start_is_idempotent(self):
        exp = _make_exporter(port=0)
        try:
            exp.start()
            first_port = exp._server.server_address[1]
            exp.start()   # second call: must log + no-op, not raise or rebind
            assert exp._server.server_address[1] == first_port
        finally:
            exp.stop()

    def test_health_reflects_bound_state(self):
        exp = _make_exporter(port=0)
        assert exp.health().healthy is False
        try:
            exp.start()
            assert exp.health().healthy is True
        finally:
            exp.stop()
        assert exp.health().healthy is False

    def test_stop_is_idempotent(self):
        exp = _make_exporter(port=0)
        exp.start()
        exp.stop()
        exp.stop()   # second call must not raise
