"""
Checkpoint 3 tests for the H-IVRV MVP — run_daily integration + deployed artifacts.

Run from research/:
    conda run -n btc_bot pytest forward_evidence/tests/test_ivrv_checkpoint3.py -v

Strategy:
  - run_daily.run_all is exercised with the price loaders and runner.collect
    functions monkeypatched (fast; no parquet fixtures). This isolates the NEW
    wiring (ivrv branch, isolation, DVOL-unavailable handling).
  - The REAL collection_config.yaml and pre_registration_ivrv.json are validated
    directly (config has ivrv + dvol; pre-reg fn_hash matches the locked file).
"""
from __future__ import annotations

import json
import pathlib

import pandas as pd
import pytest
import yaml

from forward_evidence import run_daily
from forward_evidence.pre_reg_guard import FnHashViolation, _sha256_lf, verify_fn_hash
from forward_evidence.runner_base import DataUnavailableError

_FWD = pathlib.Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Fixtures: temp config + monkeypatched loaders/runners
# ---------------------------------------------------------------------------

def _series(n: int = 300) -> pd.Series:
    idx = pd.date_range("2021-01-01", periods=n, freq="D").strftime("%Y-%m-%d")
    return pd.Series(range(n), index=idx, dtype=float)


def _write_cfg(tmp_path: pathlib.Path, include_ivrv: bool = True) -> pathlib.Path:
    sigs = {
        "h5_vol_regime":           {"cost_bps": 15.0},
        "g1_market_regime_sma200": {"cost_bps": 15.0},
        "g3_momentum_tsmom":       {"cost_bps": 15.0},
    }
    if include_ivrv:
        sigs["ivrv_vrp_spread"] = {"cost_bps": 15.0}
    cfg = {
        "signals": sigs,
        "ohlcv": {"parquet_dir": "data/raw/spot/BTCUSDT", "symbol": "BTCUSDT"},
        "dvol":  {"parquet_dir": "data/raw/dvol/BTC", "symbol": "BTC"},
    }
    p = tmp_path / "collection_config.yaml"
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return p


@pytest.fixture()
def wired(tmp_path, monkeypatch):
    """Monkeypatch loaders + runner.collect; return a calls-recorder dict."""
    calls = {"h5": None, "g1": None, "g3": None, "ivrv": None, "dvol_loaded": False}

    monkeypatch.setattr(run_daily, "_CONFIG_PATH", _write_cfg(tmp_path))
    monkeypatch.setattr(run_daily, "init_forward_db", lambda *_a, **_k: None)
    monkeypatch.setattr(run_daily, "load_daily_closes", lambda *a, **k: _series())

    def _dvol_ok(*a, **k):
        calls["dvol_loaded"] = True
        return _series()
    monkeypatch.setattr(run_daily, "load_daily_dvol", _dvol_ok)

    def mk(name):
        def collect(run_date, closes, db_path, cost_bps, inputs=None):
            calls[name] = "called"
            return "ok"
        return collect
    monkeypatch.setattr(run_daily.runner_h5, "collect", mk("h5"))
    monkeypatch.setattr(run_daily.runner_g1, "collect", mk("g1"))
    monkeypatch.setattr(run_daily.runner_g3, "collect", mk("g3"))

    def ivrv_collect(run_date, closes, dvol, db_path, cost_bps, inputs=None):
        calls["ivrv"] = {"has_dvol": dvol is not None}
        return "ok"
    monkeypatch.setattr(run_daily.runner_ivrv, "collect", ivrv_collect)

    return calls, monkeypatch


# ---------------------------------------------------------------------------
# run_all wiring
# ---------------------------------------------------------------------------

def test_C3_01_all_four_run(wired):
    calls, _ = wired
    res = run_daily.run_all("2021-10-01", db_path=pathlib.Path("x.db"))
    assert res == {"h5_vol_regime": "ok", "g1_market_regime_sma200": "ok",
                   "g3_momentum_tsmom": "ok", "ivrv_vrp_spread": "ok"}
    assert calls["dvol_loaded"] is True
    assert calls["ivrv"] == {"has_dvol": True}     # ivrv received the DVOL series


def test_C3_02_dvol_unavailable_isolates(wired):
    calls, monkeypatch = wired

    def _raise(*a, **k):
        raise DataUnavailableError("no DVOL for run_date")
    monkeypatch.setattr(run_daily, "load_daily_dvol", _raise)

    res = run_daily.run_all("2021-10-01", db_path=pathlib.Path("x.db"))
    # H5/G1/G3 unaffected; ivrv degrades to 'unavailable'; collect NOT called
    assert res["h5_vol_regime"] == "ok"
    assert res["g1_market_regime_sma200"] == "ok"
    assert res["g3_momentum_tsmom"] == "ok"
    assert res["ivrv_vrp_spread"] == "unavailable"
    assert calls["ivrv"] is None


def test_C3_03_ivrv_absent_from_config(tmp_path, monkeypatch):
    monkeypatch.setattr(run_daily, "_CONFIG_PATH", _write_cfg(tmp_path, include_ivrv=False))
    monkeypatch.setattr(run_daily, "init_forward_db", lambda *_a, **_k: None)
    monkeypatch.setattr(run_daily, "load_daily_closes", lambda *a, **k: _series())
    called = {"dvol": False, "ivrv": False}
    def _dvol(*a, **k):
        called["dvol"] = True; return _series()
    monkeypatch.setattr(run_daily, "load_daily_dvol", _dvol)
    for m in (run_daily.runner_h5, run_daily.runner_g1, run_daily.runner_g3):
        monkeypatch.setattr(m, "collect", lambda *a, **k: "ok")
    def _ivrv(*a, **k):
        called["ivrv"] = True; return "ok"
    monkeypatch.setattr(run_daily.runner_ivrv, "collect", _ivrv)

    res = run_daily.run_all("2021-10-01", db_path=pathlib.Path("x.db"))
    assert "ivrv_vrp_spread" not in res          # not run when absent from config
    assert called["dvol"] is False and called["ivrv"] is False
    assert set(res) == {"h5_vol_regime", "g1_market_regime_sma200", "g3_momentum_tsmom"}


def test_C3_04_ivrv_fn_hash_violation_isolated(wired):
    calls, monkeypatch = wired
    def _viol(*a, **k):
        raise FnHashViolation("tampered")
    monkeypatch.setattr(run_daily.runner_ivrv, "collect", _viol)
    res = run_daily.run_all("2021-10-01", db_path=pathlib.Path("x.db"))
    assert res["ivrv_vrp_spread"] == "fn_hash_violation"
    assert res["h5_vol_regime"] == "ok"          # others unaffected


def test_C3_05_closes_unavailable_marks_all(wired):
    calls, monkeypatch = wired
    def _raise(*a, **k):
        raise DataUnavailableError("no closes")
    monkeypatch.setattr(run_daily, "load_daily_closes", _raise)
    res = run_daily.run_all("2021-10-01", db_path=pathlib.Path("x.db"))
    # Early return marks every configured signal (incl. ivrv) unavailable
    assert res["ivrv_vrp_spread"] == "unavailable"
    assert all(v == "unavailable" for v in res.values())
    assert calls["ivrv"] is None


def test_C3_06_max_history_includes_ivrv():
    from forward_evidence import (decision_function_h5 as h5,
                                  decision_function_g1_market_regime as g1,
                                  decision_function_g3_tsmom as g3,
                                  decision_function_ivrv as iv)
    # h5 (273) currently dominates; assert ivrv is genuinely in the max() and <= it.
    assert iv.MIN_HISTORY == 253
    assert max(h5.MIN_HISTORY, g1.MIN_HISTORY, g3.MIN_HISTORY, iv.MIN_HISTORY) == 273


# ---------------------------------------------------------------------------
# Deployed-artifact validation (real files)
# ---------------------------------------------------------------------------

def test_C3_07_real_config_has_ivrv_and_dvol():
    cfg = yaml.safe_load((_FWD / "collection_config.yaml").read_text(encoding="utf-8"))
    assert "ivrv_vrp_spread" in cfg["signals"]
    assert cfg["signals"]["ivrv_vrp_spread"]["cost_bps"] == 15.0
    assert cfg["dvol"]["parquet_dir"] == "data/raw/dvol/BTC"


def test_C3_08_real_prereg_fn_hash_matches_locked_file():
    pre = _FWD / "pre_registration_ivrv.json"
    fn  = _FWD / "decision_function_ivrv.py"
    # The guard must verify cleanly against the deployed pre-reg + locked fn.
    h = verify_fn_hash(pre, fn, "ivrv_vrp_spread")
    assert h == _sha256_lf(fn)
    data = json.loads(pre.read_text(encoding="utf-8"))
    assert data["hypothesis"]["role"] == "feature"
    assert data["evidence_basis"]["n_trials"] == 34
    assert data["lockbox_status"]["spent"] is False
