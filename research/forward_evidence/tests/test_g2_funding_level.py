"""
Gate 6A.8 tests — g2_funding_level decision function, pre-registration, runner
and the run_daily funding branch.

Run from research/:
    conda run -n btc_bot pytest forward_evidence/tests/test_g2_funding_level.py -v

No test in this module touches the sealed lockbox, submits an order, or wires
execution. The real-corpus value-identity test skips where the gitignored
corpus or duckdb is absent.
"""
from __future__ import annotations

import json
import pathlib
import sqlite3

import numpy as np
import pandas as pd
import pytest
import yaml

import forward_evidence.decision_function_g2_funding_level as df
from forward_evidence import runner_g2
from forward_evidence.init_forward_db import init_forward_db
from forward_evidence.pre_reg_guard import FnHashViolation, verify_fn_hash
from forward_evidence.runner_base import DataUnavailableError

_FE_ROOT = pathlib.Path(__file__).resolve().parents[1]
_PRE_REG = _FE_ROOT / "pre_registration_g2_funding_level.json"
_FN_FILE = _FE_ROOT / "decision_function_g2_funding_level.py"
_CONFIG  = _FE_ROOT / "collection_config.yaml"
_RUN_DAILY = (_FE_ROOT / "run_daily.py").read_text(encoding="utf-8")

LOCKBOX = ("2025-06-01", "2026-05-31")


def _dates(n, start="2021-01-01"):
    return pd.date_range(start, periods=n, freq="D").strftime("%Y-%m-%d")


def make_funding(n, seed=5, level=1.0, vol=3.0):
    rng = np.random.default_rng(seed)
    return pd.Series(level + rng.normal(0, vol, n), index=_dates(n), dtype=float)


def make_closes(n, seed=7):
    rng = np.random.default_rng(seed)
    price = 40_000.0 * np.exp(np.cumsum(rng.normal(0, 0.03, n)))
    return pd.Series(price, index=_dates(n), dtype=float)


# ===========================================================================
# Locked constants
# ===========================================================================

def test_constants_locked():
    assert df.PCTILE == 33
    assert df.RW == 252
    assert df.MINP == 126
    assert df.HOLD_DAYS == 7
    assert df.MIN_HISTORY == 253
    assert df.MIN_HISTORY == df.RW + 1


# ===========================================================================
# Value identity against the battery function (the core requirement)
# ===========================================================================

def test_value_identical_to_sig_funding_level_on_real_corpus():
    """
    The port must be VALUE-IDENTICAL to evidence/signals.py sig_funding_level,
    not merely similar: the pre-registration's evidence basis is the battery's
    evaluation of that function, so any divergence would mean forward
    collection measures something the recorded evidence never evaluated.

    engine.py is loaded by path under a process-unique key rather than
    `import engine`, so this does not add an instance of hazard EH-002
    (sys.modules collision with research/dq/engine.py).
    """
    import importlib.util
    import sys

    engine_path = _FE_ROOT.parent / "evidence" / "engine.py"
    signals_path = _FE_ROOT.parent / "evidence" / "signals.py"
    if not (engine_path.exists() and signals_path.exists()):
        pytest.skip("research engine/signals not present")

    def _load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod

    # signals.py does `from engine import SignalSpec`, a bare name that
    # research/dq/engine.py also claims (hazard EH-002). Bind the CORRECT
    # engine to that name only for the duration of the load, saving and
    # restoring whatever was there before, so no poisoned global survives this
    # test in a full-suite process.
    _MISSING = object()
    prior_engine = sys.modules.get("engine", _MISSING)
    loaded = []
    try:
        eng = _load("_g2_test_engine", engine_path)
        loaded.append("_g2_test_engine")
        sys.modules["engine"] = eng
        sig = _load("_g2_test_signals", signals_path)
        loaded.append("_g2_test_signals")
        m = eng.load_master()
    except Exception as exc:
        for n in loaded:
            sys.modules.pop(n, None)
        if prior_engine is _MISSING:
            sys.modules.pop("engine", None)
        else:
            sys.modules["engine"] = prior_engine
        pytest.skip(f"research corpus/engine unavailable: {exc}")

    try:
        battery = np.nan_to_num(sig.sig_funding_level(m, 33, 252).to_numpy(float))
        ported = np.nan_to_num(
            df.compute_signal(m["daily_mean_funding_bps"]).to_numpy(float))
        assert len(battery) == len(ported) > 1000
        assert np.array_equal(battery, ported), (
            f"{int((battery != ported).sum())} rows differ; "
            f"max abs diff {np.abs(battery - ported).max()}")
    finally:
        for n in loaded:
            sys.modules.pop(n, None)
        if prior_engine is _MISSING:
            sys.modules.pop("engine", None)
        else:
            sys.modules["engine"] = prior_engine


def test_positions_are_only_minus_one_zero_or_plus_one():
    pos = df.compute_signal(make_funding(600))
    assert set(np.unique(pos.to_numpy(float))) <= {-1.0, 0.0, 1.0}


def test_contrarian_direction():
    """Low funding -> long, high funding -> short. Sign convention is locked."""
    f = make_funding(400)
    pos = df.compute_signal(f)
    lo = f.rolling(df.RW, min_periods=df.MINP).quantile(0.33)
    hi = f.rolling(df.RW, min_periods=df.MINP).quantile(0.67)
    assert (pos[(f < lo)] == 1.0).all()
    assert (pos[(f > hi)] == -1.0).all()


def test_index_preserved():
    f = make_funding(300)
    assert list(df.compute_signal(f).index) == list(f.index)


# ===========================================================================
# MIN_HISTORY / insufficient history
# ===========================================================================

def test_warmup_is_flat_before_min_periods():
    """min_periods=MINP means the first valid quantile is at row MINP, i.e.
    index MINP-1; everything strictly before that must be flat."""
    pos = df.compute_signal(make_funding(300))
    assert (pos.iloc[: df.MINP - 1] == 0.0).all()


def test_short_series_is_entirely_flat():
    pos = df.compute_signal(make_funding(50))
    assert (pos == 0.0).all()


def test_nan_funding_yields_flat():
    f = make_funding(400)
    f.iloc[-1] = np.nan
    assert df.compute_signal(f).iloc[-1] == 0.0


def test_runner_refuses_insufficient_history(tmp_path):
    db = tmp_path / "fwd.db"
    init_forward_db(db)
    n = 60
    with pytest.raises((DataUnavailableError, RuntimeError, ValueError)):
        runner_g2.collect("2021-03-01", make_closes(n), make_funding(n), db, 15.0)


def test_runner_refuses_short_funding_with_sufficient_closes(tmp_path):
    """
    Gate 6A.11 regression. The pre-fix Step 3 length check covered `closes`
    only, so long closes + short funding reached compute_fn, whose rolling
    windows were undefined, and the warmup 0.0 was persisted as a decision --
    a silent flat from missing data. It must be a transient skip instead.

    The older test above cannot catch this: it shortens BOTH series, so the
    closes check trips first and the aux path is never exercised.
    """
    db = tmp_path / "fwd.db"
    init_forward_db(db)
    n = 400
    closes = make_closes(n)
    short_funding = make_funding(n).iloc[-40:]          # 40 rows << MIN_HISTORY

    with pytest.raises(DataUnavailableError, match="Insufficient auxiliary history"):
        runner_g2.collect(closes.index[-1], closes, short_funding, db, 15.0)

    # Transient: nothing persisted, so the next run retries automatically.
    with sqlite3.connect(db) as c:
        for table in ("run_log", "signal_log", "trade_entries"):
            assert c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_runner_accepts_sufficient_funding_unchanged(tmp_path):
    """The fix must not alter behaviour when the aux series IS long enough."""
    db = tmp_path / "fwd.db"
    init_forward_db(db)
    n = 400
    closes, funding = make_closes(n), make_funding(n)
    assert runner_g2.collect(closes.index[-1], closes, funding, db, 15.0) == "ok"
    with sqlite3.connect(db) as c:
        pos = c.execute("SELECT position FROM signal_log").fetchone()[0]
    assert pos in (-1.0, 0.0, 1.0)


def test_aux_length_boundary_is_min_history(tmp_path):
    """Exactly MIN_HISTORY rows is sufficient; one fewer is not."""
    n = 400
    closes = make_closes(n)
    funding = make_funding(n)
    rd = closes.index[-1]

    db_ok = tmp_path / "ok.db"
    init_forward_db(db_ok)
    assert runner_g2.collect(
        rd, closes, funding.iloc[-df.MIN_HISTORY:], db_ok, 15.0) == "ok"

    db_bad = tmp_path / "bad.db"
    init_forward_db(db_bad)
    with pytest.raises(DataUnavailableError, match="Insufficient auxiliary history"):
        runner_g2.collect(
            rd, closes, funding.iloc[-(df.MIN_HISTORY - 1):], db_bad, 15.0)


# ===========================================================================
# Pre-registration + hash verification
# ===========================================================================

def _prereg() -> dict:
    return json.loads(_PRE_REG.read_text(encoding="utf-8"))


def test_pre_registration_uses_current_schema():
    d = _prereg()
    assert d["signal_id"] == "g2_funding_level"          # top level
    assert d["function_spec"]["decision_function_hash"]  # under function_spec
    assert "decision_function_hash" not in d, "must not use h6's outlier shape"


def test_fn_hash_verifies():
    assert verify_fn_hash(_PRE_REG, _FN_FILE, "g2_funding_level")


def test_fn_hash_violation_on_modified_function(tmp_path):
    fake = tmp_path / "decision_function_g2_funding_level.py"
    fake.write_text(_FN_FILE.read_text(encoding="utf-8") + "\n# tampered\n",
                    encoding="utf-8")
    with pytest.raises(FnHashViolation):
        verify_fn_hash(_PRE_REG, fake, "g2_funding_level")


def test_wrong_signal_id_rejected():
    with pytest.raises(FnHashViolation):
        verify_fn_hash(_PRE_REG, _FN_FILE, "h6_funding_vol_regime")


def test_pre_registration_parameters_match_module():
    p = _prereg()["function_spec"]["parameters"]
    assert p["PCTILE"] == df.PCTILE and p["RW"] == df.RW
    assert p["MINP"] == df.MINP and p["HOLD_DAYS"] == df.HOLD_DAYS
    assert p["MIN_HISTORY"] == df.MIN_HISTORY


def test_role_is_feature_not_edge():
    d = _prereg()
    assert d["hypothesis"]["role"] == "feature"
    assert "FAIL Edge gate" in d["evidence_basis"]["candidate_gate_result"]


def test_independence_risk_declared():
    """The 0.8187 cluster correlation must be recorded, not omitted."""
    eb = _prereg()["evidence_basis"]
    assert eb["independence_maxcorr_existing"] == pytest.approx(0.8187)
    assert eb["independence_maxcorr_collecting"] == pytest.approx(0.3774)
    assert any("0.8187" in f for f in eb["notable_flags"])


def test_governance_stats_are_null_not_fabricated():
    eb = _prereg()["evidence_basis"]
    assert eb["dsr_prob"] is None and eb["bh_p"] is None
    assert eb["n_trials"] == 34


def test_lockbox_declared_unspent_and_gating_is_train_slice():
    d = _prereg()
    assert d["lockbox_status"]["spent"] is False
    assert list(d["lockbox_status"]["window"]) == list(LOCKBOX)
    assert "2025-05-31" in d["evidence_basis"]["gating_basis"]


# ===========================================================================
# Runner integration
# ===========================================================================

def _rows(db, table):
    with sqlite3.connect(db) as c:
        return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_runner_collects_and_writes(tmp_path):
    db = tmp_path / "fwd.db"
    init_forward_db(db)
    n = 400
    closes, funding = make_closes(n), make_funding(n)
    run_date = closes.index[-1]
    assert runner_g2.collect(run_date, closes, funding, db, 15.0) == "ok"
    assert _rows(db, "run_log") == 1
    assert _rows(db, "signal_log") == 1
    with sqlite3.connect(db) as c:
        sid, pos = c.execute(
            "SELECT signal_id, position FROM signal_log").fetchone()
    assert sid == "g2_funding_level"
    assert pos in (-1.0, 0.0, 1.0)


def test_runner_is_idempotent(tmp_path):
    db = tmp_path / "fwd.db"
    init_forward_db(db)
    n = 400
    closes, funding = make_closes(n), make_funding(n)
    run_date = closes.index[-1]
    assert runner_g2.collect(run_date, closes, funding, db, 15.0) == "ok"
    assert runner_g2.collect(run_date, closes, funding, db, 15.0) == "skipped"
    assert _rows(db, "run_log") == 1


def test_runner_records_funding_as_raw_value(tmp_path):
    db = tmp_path / "fwd.db"
    init_forward_db(db)
    n = 400
    closes, funding = make_closes(n), make_funding(n)
    run_date = closes.index[-1]
    runner_g2.collect(run_date, closes, funding, db, 15.0)
    with sqlite3.connect(db) as c:
        raw = c.execute("SELECT raw_value FROM signal_log").fetchone()[0]
    assert raw == pytest.approx(float(funding.iloc[-1]))


def test_runner_signal_id_and_paths():
    assert runner_g2.SIGNAL_ID == "g2_funding_level"
    assert runner_g2._PRE_REG == _PRE_REG
    assert runner_g2._FN_FILE == _FN_FILE


def test_missing_funding_at_run_date_is_transient(tmp_path):
    """Absence of data must never be recorded as a flat decision."""
    db = tmp_path / "fwd.db"
    init_forward_db(db)
    n = 400
    closes, funding = make_closes(n), make_funding(n)
    with pytest.raises(DataUnavailableError):
        runner_g2.collect(closes.index[-1], closes, funding.iloc[:-5], db, 15.0)


# ===========================================================================
# run_daily wiring + funding-failure isolation
# ===========================================================================

def test_run_daily_imports_runner_and_decision_function():
    assert "runner_g2" in _RUN_DAILY
    assert "decision_function_g2_funding_level as _g2" in _RUN_DAILY
    assert "load_daily_funding" in _RUN_DAILY


def test_run_daily_g2_branch_is_isolated():
    """
    The funding load must sit in its own try/except and the collect call must be
    guarded by `if funding is not None`, so a funding outage cannot propagate
    into the shared path that H5/G1/G3/IVRV run through.
    """
    assert "g2_id  = 'g2_funding_level'" in _RUN_DAILY
    branch = _RUN_DAILY.split("g2_id  = 'g2_funding_level'", 1)[1]
    branch = branch.split("g3_atr_expansion", 1)[0]
    assert "funding = None" in branch
    assert "except DataUnavailableError" in branch
    assert "if funding is not None:" in branch
    assert "runner_g2.collect(" in branch


def test_g2_branch_runs_after_the_shared_loop():
    """Ordering guarantee: the single-input loop and ivrv commit before g2."""
    loop = _RUN_DAILY.index("for sig_id, (runner_mod, sig_cfg) in runners.items()")
    ivrv = _RUN_DAILY.index("ivrv_id  = 'ivrv_vrp_spread'")
    g2 = _RUN_DAILY.index("g2_id  = 'g2_funding_level'")
    assert loop < ivrv < g2


def test_funding_failure_isolation_does_not_touch_other_signals():
    """No existing signal id appears inside the g2 branch."""
    branch = _RUN_DAILY.split("g2_id  = 'g2_funding_level'", 1)[1]
    branch = branch.split("g3_atr_expansion", 1)[0]
    for other in ("h5_vol_regime", "g1_market_regime_sma200",
                  "g3_momentum_tsmom", "ivrv_vrp_spread"):
        assert other not in branch


def test_g2_included_in_governance_failure_records():
    """
    Regression guard. On a governance pre-check failure every signal run_all()
    would have attempted must get a run_log error row. g2 was initially absent
    from _blocked_signal_ids(), so a governance failure would have silently
    omitted it — the one signal whose absence looks identical to 'never
    configured'. Its inclusion rule must mirror ivrv's (whenever configured).
    """
    from forward_evidence.run_daily import _blocked_signal_ids

    cfg_sigs = _cfg()["signals"]
    assert "g2_funding_level" in _blocked_signal_ids(cfg_sigs)
    assert set(_blocked_signal_ids(cfg_sigs)) == set(cfg_sigs) - {
        s for s, c in cfg_sigs.items()
        if s == "g3_atr_expansion" and not c.get("enabled", False)
    }
    # not configured -> not claimed as blocked
    without = {k: v for k, v in cfg_sigs.items() if k != "g2_funding_level"}
    assert "g2_funding_level" not in _blocked_signal_ids(without)


def test_existing_signals_do_not_depend_on_funding():
    """
    The shared closes load and the single-input runner loop must contain no
    funding reference, so funding cannot become a precondition for H5/G1/G3.
    """
    head = _RUN_DAILY.split("ivrv_id  = 'ivrv_vrp_spread'", 1)[0]
    loop_start = head.index("runners = {")
    assert "funding" not in head[loop_start:]


# ===========================================================================
# Config
# ===========================================================================

def _cfg() -> dict:
    return yaml.safe_load(_CONFIG.read_text(encoding="utf-8"))


def test_g2_registered_with_correct_config():
    g2 = _cfg()["signals"]["g2_funding_level"]
    assert float(g2["cost_bps"]) == 15.0
    assert g2["pre_reg_file"].endswith("pre_registration_g2_funding_level.json")
    assert g2["decision_function_module"].endswith(
        "decision_function_g2_funding_level")


def test_cadence_and_existing_signals_unchanged():
    c = _cfg()
    assert c["schedule"]["utc_hour"] == 1 and c["schedule"]["utc_minute"] == 0
    for sid in ("h5_vol_regime", "g1_market_regime_sma200", "g3_momentum_tsmom",
                "ivrv_vrp_spread", "g3_atr_expansion"):
        assert float(c["signals"][sid]["cost_bps"]) == 15.0


# ===========================================================================
# Governance guards
# ===========================================================================

def test_no_execution_or_order_wiring():
    for src in (_FN_FILE.read_text(encoding="utf-8"),
                (_FE_ROOT / "runner_g2.py").read_text(encoding="utf-8")):
        low = src.lower()
        for token in ("place_order", "submit", "delta_client", "kill_switch",
                      "testnet_executor", "client_order_id"):
            assert token not in low, f"execution token {token!r} present"


def test_decision_function_does_not_import_battery():
    src = _FN_FILE.read_text(encoding="utf-8")
    tree_src = "\n".join(
        l for l in src.splitlines() if not l.strip().startswith("#"))
    assert "import signals" not in tree_src
    assert "sig_funding_level(" not in tree_src.split('"""')[-1]


def test_sealed_h6_artifacts_not_referenced_by_g2():
    for f in (_FN_FILE, _FE_ROOT / "runner_g2.py", _PRE_REG):
        src = f.read_text(encoding="utf-8")
        assert "decision_function.py" not in src.replace(
            "decision_function_g2_funding_level.py", "")
