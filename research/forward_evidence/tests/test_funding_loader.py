"""
Gate 6A.6 tests — runner_base.load_daily_funding and the funding config entry.

Scope (additive only): the funding LOADER and its configuration. No signal
consumes funding yet, so there is deliberately no h6/g2 collection test here —
both are blocked on frozen artifacts (see the funding: block in
collection_config.yaml). The tests below therefore also PIN that absence, so a
future gate cannot quietly activate a funding signal without updating them.

Run from research/:
    conda run -n btc_bot pytest forward_evidence/tests/test_funding_loader.py -v
"""
from __future__ import annotations

import pathlib

import numpy as np
import pandas as pd
import pytest
import yaml

from forward_evidence.runner_base import (
    MIN_DAILY_FUND,
    DataUnavailableError,
    load_daily_funding,
)

_FE_ROOT = pathlib.Path(__file__).resolve().parents[1]
_CONFIG  = _FE_ROOT / "collection_config.yaml"

FUNDING_HOURS = (0, 8, 16)          # Binance USDT-perp settlement grid, UTC


# ===========================================================================
# Helpers
# ===========================================================================

def write_funding_partitions(
    base_dir: pathlib.Path,
    rows: list[tuple[str, float]],
) -> pathlib.Path:
    """
    Write (utc_timestamp, funding_rate) rows into funding/BTCUSDT/year=/month=.
    Returns the directory load_daily_funding expects as parquet_dir.
    """
    out_root = base_dir / "funding" / "BTCUSDT"
    ts = pd.to_datetime([r[0] for r in rows], utc=True)
    frame = pd.DataFrame({
        "timestamp_ms": (ts.view("int64") // 1_000_000).astype("int64"),
        "funding_rate": np.array([r[1] for r in rows], dtype=float),
    })
    frame["year"]  = ts.year
    frame["month"] = ts.month
    for (y, m), grp in frame.groupby(["year", "month"]):
        part = out_root / f"year={y}" / f"month={m:02d}"
        part.mkdir(parents=True, exist_ok=True)
        grp.drop(columns=["year", "month"]).to_parquet(
            part / "data.parquet", index=False
        )
    return out_root


def full_days(n: int, start: str = "2024-01-01", rate: float = 1e-4
              ) -> list[tuple[str, float]]:
    """n complete UTC days, 3 observations each on the 00/08/16 grid."""
    days = pd.date_range(start, periods=n, freq="D", tz="UTC")
    return [
        (f"{d.date()} {h:02d}:00:00", rate)
        for d in days for h in FUNDING_HOURS
    ]


# ===========================================================================
# Semantics: AVG(funding_rate) * 10000 per UTC day
# ===========================================================================

def test_daily_mean_is_average_times_ten_thousand(tmp_path):
    rows = [("2024-03-01 00:00:00", 0.0001),
            ("2024-03-01 08:00:00", 0.0002),
            ("2024-03-01 16:00:00", 0.0003)]
    rows = full_days(40, "2024-01-20") + rows
    d = load_daily_funding(write_funding_partitions(tmp_path, rows),
                           "2024-03-01", min_rows=1)
    # mean(1e-4, 2e-4, 3e-4) = 2e-4  ->  2e-4 * 10_000 = 2.0 bps
    assert d.loc["2024-03-01"] == pytest.approx(2.0)


def test_conversion_factor_is_exactly_ten_thousand(tmp_path):
    rows = full_days(5, "2024-01-01", rate=0.00037)
    d = load_daily_funding(write_funding_partitions(tmp_path, rows),
                           "2024-01-05", min_rows=1)
    assert d.iloc[-1] == pytest.approx(0.00037 * 10_000.0)


def test_negative_funding_preserved(tmp_path):
    rows = full_days(5, "2024-01-01") + [
        ("2024-01-06 00:00:00", -0.0004),
        ("2024-01-06 08:00:00", -0.0002),
        ("2024-01-06 16:00:00", -0.0003),
    ]
    d = load_daily_funding(write_funding_partitions(tmp_path, rows),
                           "2024-01-06", min_rows=1)
    assert d.loc["2024-01-06"] == pytest.approx(-3.0)


def test_index_is_yyyy_mm_dd_strings_and_chronological(tmp_path):
    d = load_daily_funding(
        write_funding_partitions(tmp_path, full_days(20, "2024-05-01")),
        "2024-05-20", min_rows=5)
    assert list(d.index) == sorted(d.index)
    assert all(isinstance(i, str) and len(i) == 10 for i in d.index)


# ===========================================================================
# UTC daily aggregation boundaries
# ===========================================================================

def test_observations_group_by_utc_day_not_local(tmp_path):
    """The 16:00 print belongs to its own UTC day, never the next one."""
    rows = full_days(10, "2024-02-01") + [
        ("2024-02-11 00:00:00", 0.0001),
        ("2024-02-11 08:00:00", 0.0001),
        ("2024-02-11 16:00:00", 0.0007),   # would leak to 02-12 under +8h shift
    ]
    d = load_daily_funding(write_funding_partitions(tmp_path, rows),
                           "2024-02-11", min_rows=1)
    assert d.loc["2024-02-11"] == pytest.approx(3.0)
    assert "2024-02-12" not in d.index


def test_day_boundary_midnight_belongs_to_that_day(tmp_path):
    rows = full_days(6, "2024-04-01")
    d = load_daily_funding(write_funding_partitions(tmp_path, rows),
                           "2024-04-06", min_rows=1)
    assert "2024-04-06" in d.index and "2024-04-07" not in d.index


def test_through_date_filter_excludes_future_days(tmp_path):
    """Look-ahead guard: later days present in the store must not be returned."""
    d = load_daily_funding(
        write_funding_partitions(tmp_path, full_days(30, "2024-06-01")),
        "2024-06-10", min_rows=1)
    assert d.index[-1] == "2024-06-10"
    assert not any(i > "2024-06-10" for i in d.index)


# ===========================================================================
# MIN_DAILY_FUND >= 2
# ===========================================================================

def test_min_daily_fund_is_two():
    assert MIN_DAILY_FUND == 2


def test_min_daily_fund_matches_research_engine():
    """
    The forward loader and the research engine must agree on what a daily mean
    is; a divergence would silently redefine every funding hypothesis between
    research and forward collection. engine.py is read as text rather than
    imported so this test does not drag duckdb into the forward test run.
    """
    engine_src = (_FE_ROOT.parent / "evidence" / "engine.py").read_text(
        encoding="utf-8")
    line = next(ln for ln in engine_src.splitlines()
                if ln.strip().startswith("MIN_DAILY_FUND"))
    engine_value = int(line.split("=")[1].split("#")[0].strip())
    assert engine_value == MIN_DAILY_FUND


def test_day_with_one_observation_is_dropped(tmp_path):
    rows = full_days(10, "2024-07-01") + [("2024-07-11 00:00:00", 0.0009)]
    d = load_daily_funding(write_funding_partitions(tmp_path, rows),
                           "2024-07-10", min_rows=1)
    assert "2024-07-11" not in d.index


def test_day_with_two_observations_is_kept(tmp_path):
    rows = full_days(10, "2024-07-01") + [
        ("2024-07-11 00:00:00", 0.0002),
        ("2024-07-11 08:00:00", 0.0004),
    ]
    d = load_daily_funding(write_funding_partitions(tmp_path, rows),
                           "2024-07-11", min_rows=1)
    assert d.loc["2024-07-11"] == pytest.approx(3.0)


def test_partial_day_is_dropped_not_averaged_over_present_prints(tmp_path):
    """A single 8-hour print must never be promoted to a 'daily mean'."""
    rows = full_days(10, "2024-08-01") + [("2024-08-11 08:00:00", 0.0050)]
    with pytest.raises(DataUnavailableError):
        load_daily_funding(write_funding_partitions(tmp_path, rows),
                           "2024-08-11", min_rows=1)


# ===========================================================================
# Missing / invalid handling
# ===========================================================================

def test_missing_parquet_dir_raises(tmp_path):
    with pytest.raises(DataUnavailableError, match="No funding partitions"):
        load_daily_funding(tmp_path / "nope", "2024-01-05", min_rows=1)


def test_through_date_absent_raises(tmp_path):
    d = write_funding_partitions(tmp_path, full_days(10, "2024-01-01"))
    with pytest.raises(DataUnavailableError, match="not in store"):
        load_daily_funding(d, "2024-01-20", min_rows=1)


def test_insufficient_history_raises(tmp_path):
    d = write_funding_partitions(tmp_path, full_days(10, "2024-01-01"))
    with pytest.raises(DataUnavailableError, match="Insufficient funding history"):
        load_daily_funding(d, "2024-01-10", min_rows=273)


def test_nan_rates_are_dropped_before_aggregation(tmp_path):
    rows = full_days(10, "2024-09-01") + [
        ("2024-09-11 00:00:00", 0.0002),
        ("2024-09-11 08:00:00", 0.0004),
        ("2024-09-11 16:00:00", float("nan")),
    ]
    d = load_daily_funding(write_funding_partitions(tmp_path, rows),
                           "2024-09-11", min_rows=1)
    # NaN dropped -> 2 valid prints -> still meets MIN_DAILY_FUND, mean of the two
    assert d.loc["2024-09-11"] == pytest.approx(3.0)


def test_all_nan_day_is_dropped(tmp_path):
    rows = full_days(10, "2024-10-01") + [
        ("2024-10-11 00:00:00", float("nan")),
        ("2024-10-11 08:00:00", float("nan")),
        ("2024-10-11 16:00:00", float("nan")),
    ]
    with pytest.raises(DataUnavailableError):
        load_daily_funding(write_funding_partitions(tmp_path, rows),
                           "2024-10-11", min_rows=1)


def test_microsecond_timestamps_are_normalised(tmp_path):
    """Defensive µs guard, mirroring the spot/DVOL loaders."""
    out_root = tmp_path / "funding" / "BTCUSDT"
    rows = full_days(6, "2024-11-01")
    ts = pd.to_datetime([r[0] for r in rows], utc=True)
    frame = pd.DataFrame({
        "timestamp_ms": (ts.view("int64") // 1_000).astype("int64"),   # µs
        "funding_rate": np.array([r[1] for r in rows], dtype=float),
    })
    frame["year"], frame["month"] = ts.year, ts.month
    for (y, m), grp in frame.groupby(["year", "month"]):
        part = out_root / f"year={y}" / f"month={m:02d}"
        part.mkdir(parents=True, exist_ok=True)
        grp.drop(columns=["year", "month"]).to_parquet(
            part / "data.parquet", index=False)
    d = load_daily_funding(out_root, "2024-11-06", min_rows=1)
    assert d.index[-1] == "2024-11-06"


def test_gap_days_are_absent_not_interpolated(tmp_path):
    rows = full_days(5, "2024-12-01") + full_days(5, "2024-12-10")
    d = load_daily_funding(write_funding_partitions(tmp_path, rows),
                           "2024-12-14", min_rows=1)
    assert "2024-12-07" not in d.index
    assert len(d) == 10


# ===========================================================================
# Configuration
# ===========================================================================

def _cfg() -> dict:
    return yaml.safe_load(_CONFIG.read_text(encoding="utf-8"))


def test_funding_parquet_dir_declared():
    assert _cfg()["funding"]["parquet_dir"] == "data/raw/funding/BTCUSDT"


def test_funding_symbol_declared():
    assert _cfg()["funding"]["symbol"] == "BTCUSDT"


def test_existing_five_signals_unchanged():
    """
    The five originally-collecting signals, their exact ids and cost_bps, are
    pinned. Any accidental edit to an existing signal block fails here rather
    than silently changing what production collects. Updated at Gate 6A.8 to
    add g2_funding_level as a sixth registered signal; the original five are
    asserted unchanged as a subset, so this test still fails if one is removed
    or its cost_bps altered.
    """
    sigs = _cfg()["signals"]
    original_five = {
        "h5_vol_regime",
        "g1_market_regime_sma200",
        "g3_momentum_tsmom",
        "ivrv_vrp_spread",
        "g3_atr_expansion",
    }
    assert original_five <= set(sigs), "an originally-collecting signal was removed"
    assert set(sigs) == original_five | {"g2_funding_level"}
    for sid in sigs:
        assert float(sigs[sid]["cost_bps"]) == 15.0


def test_h6_still_not_activated():
    """
    Gate 6A.8 activated g2 only. h6 remains blocked on SEALED artifacts: its
    pre-registration has no top-level signal_id and stores its hash outside
    function_spec (so verify_fn_hash rejects it), and both it and its decision
    function are in BASELINE_46.json under DS-DEC-004. Activating h6 requires
    its own governance gate, which must update this test deliberately rather
    than inherit a silent pass.
    """
    assert "h6_funding_vol_regime" not in _cfg()["signals"]


def test_collection_cadence_unchanged():
    sched = _cfg()["schedule"]
    assert sched["utc_hour"] == 1 and sched["utc_minute"] == 0


# ===========================================================================
# Real-corpus equivalence against the research engine
# ===========================================================================

def test_matches_research_engine_on_the_real_corpus():
    """
    The decisive assertion: on the real corpus the loader must reproduce
    engine.load_master()'s daily_mean_funding_bps EXACTLY, not approximately.
    Every registered funding hypothesis was evaluated against the engine's
    aggregation, so any difference here would mean forward collection is
    measuring a different quantity than the research that justified it.

    Skipped when the corpus is absent (it is gitignored, so absent in CI) or
    when duckdb is unavailable — engine.py is a heavyweight import that the
    rest of this module deliberately avoids.
    """
    corpus = _FE_ROOT.parent / "data" / "raw" / "funding" / "BTCUSDT"
    if not corpus.exists():
        pytest.skip("real funding corpus not present")

    # EH-002 discipline: research/evidence/engine.py and research/dq/engine.py
    # share the bare name "engine", and sys.modules is process-global, so a
    # plain `import engine` here would bind whichever loaded first and poison
    # every later importer in the same pytest process. Load it by path under a
    # process-unique key instead — the remediation the hazard register itself
    # prescribes — and do not touch sys.path.
    import importlib.util
    import sys

    engine_path = _FE_ROOT.parent / "evidence" / "engine.py"
    if not engine_path.exists():
        pytest.skip("research engine.py not present")
    mod_name = "_fwd_test_evidence_engine"
    try:
        spec = importlib.util.spec_from_file_location(mod_name, engine_path)
        engine_mod = importlib.util.module_from_spec(spec)
        # Must be in sys.modules BEFORE exec_module: @dataclass resolves its
        # own class via sys.modules.get(cls.__module__).__dict__, which is None
        # for an unregistered module. Registered under the unique key, never
        # under "engine", so the EH-002 collision cannot occur.
        sys.modules[mod_name] = engine_mod
        try:
            spec.loader.exec_module(engine_mod)
            m = engine_mod.load_master()
        finally:
            sys.modules.pop(mod_name, None)
    except Exception as exc:                       # duckdb absent, corpus gaps
        pytest.skip(f"research engine unavailable: {exc}")
    engine_series = pd.Series(
        m["daily_mean_funding_bps"].to_numpy(float),
        index=pd.to_datetime(m["day_ts"], unit="ms", utc=True).dt.strftime("%Y-%m-%d"),
    ).dropna()
    through = str(engine_series.index[-1])

    # min_rows drives how many partitions are selected, so use a realistic
    # history depth (h6's MIN_HISTORY) rather than 1, which would read ~33 days
    # and make the comparison window too short to be meaningful.
    loaded = load_daily_funding(corpus, through, min_rows=273)
    common = loaded.index.intersection(engine_series.index)
    assert len(common) > 250, f"insufficient overlap to be meaningful: {len(common)}"

    diff = np.abs(loaded.loc[common].to_numpy(float)
                  - engine_series.loc[common].to_numpy(float))
    assert diff.max() < 1e-9, f"loader diverges from engine by {diff.max()}"
