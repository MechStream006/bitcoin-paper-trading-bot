"""
Tests for Phase 4 ATR Expansion integration.

Run from research/:
    conda run -n btc_bot pytest forward_evidence/tests/test_atr_runner.py -v

Groups:
  AR — runner_atr_expansion.py unit tests
  GC — _check_governance() and main() gate
  DR — dry-run pipeline (no DB writes)
  GA — ATR gating in run_daily.run_all()
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import sys

import numpy as np
import pandas as pd
import pytest
import yaml

from forward_evidence import run_daily as _run_daily
from forward_evidence import runner_atr_expansion
from forward_evidence import decision_function_g3_atr_expansion as _df_atr
from forward_evidence.init_forward_db import init_forward_db
from forward_evidence.pre_reg_guard import FnHashViolation, _sha256_lf
from forward_evidence.runner_base import DataUnavailableError, run_ohlcv_signal


# ===========================================================================
# Shared helpers and fixtures
# ===========================================================================

def _make_ohlcv(n: int, seed: int = 42) -> pd.DataFrame:
    """n daily OHLCV rows with random-walk prices; high >= close >= low guaranteed."""
    rng    = np.random.default_rng(seed)
    log_r  = rng.normal(0, 0.02, n)
    close  = 40_000.0 * np.exp(np.cumsum(log_r))
    spread = np.abs(rng.normal(0, 0.008, n)) * close
    high   = close + spread
    low    = close - spread
    idx    = pd.date_range('2024-01-01', periods=n, freq='D').strftime('%Y-%m-%d')
    return pd.DataFrame({'high': high, 'low': low, 'close': close}, index=idx)


def _make_closes(n: int = 300, seed: int = 42) -> pd.Series:
    """n daily close prices with random-walk returns (σ=2%)."""
    rng   = np.random.default_rng(seed)
    log_r = rng.normal(0, 0.02, n)
    vals  = 40_000.0 * np.exp(np.cumsum(log_r))
    idx   = pd.date_range('2024-01-01', periods=n, freq='D').strftime('%Y-%m-%d')
    return pd.Series(vals, index=idx)


@pytest.fixture()
def db_path(tmp_path: pathlib.Path) -> pathlib.Path:
    p = tmp_path / "evidence_forward.db"
    init_forward_db(p)
    return p


@pytest.fixture()
def atr_fn_file() -> pathlib.Path:
    here = pathlib.Path(__file__).resolve().parent.parent
    f = here / "decision_function_g3_atr_expansion.py"
    assert f.exists(), f"ATR decision function not found: {f}"
    return f


@pytest.fixture()
def atr_pre_reg(tmp_path: pathlib.Path, atr_fn_file: pathlib.Path) -> pathlib.Path:
    """A pre-registration JSON whose fn_hash matches the REAL ATR decision function."""
    h = _sha256_lf(atr_fn_file)
    data = {
        'schema_version': 1,
        'signal_id': 'g3_atr_expansion',
        'pre_registration_date': '2026-06-26T00:00:00Z',
        'function_spec': {'decision_function_hash': h},
    }
    p = tmp_path / 'atr_pre_reg.json'
    p.write_text(json.dumps(data), encoding='utf-8')
    return p


@pytest.fixture()
def stub_fn_file(tmp_path: pathlib.Path) -> pathlib.Path:
    """A different function file — used to trigger fn_hash_violation."""
    f = tmp_path / 'stub_fn.py'
    f.write_bytes(
        b"def compute_signal(ohlcv):\n"
        b"    import pandas as pd\n"
        b"    return pd.Series(1.0, index=ohlcv.index)\n"
    )
    return f


class _SubprocessResult:
    """Minimal stand-in for subprocess.CompletedProcess."""
    def __init__(self, returncode: int, stdout: str = '', stderr: str = '') -> None:
        self.returncode = returncode
        self.stdout     = stdout
        self.stderr     = stderr


def _sp_mock(returncode: int, stdout: str = '', stderr: str = '') -> object:
    """Return a fake subprocess module whose .run() always yields a fixed result."""
    result = _SubprocessResult(returncode, stdout, stderr)

    class _FakeSP:
        @staticmethod
        def run(*args, **kwargs) -> _SubprocessResult:
            return result

    return _FakeSP()


def _sp_timeout_mock() -> object:
    """Return a fake subprocess module whose .run() always raises TimeoutExpired."""
    import subprocess as _subprocess_real

    class _FakeSP:
        TimeoutExpired = _subprocess_real.TimeoutExpired

        @staticmethod
        def run(*args, **kwargs) -> None:
            raise _subprocess_real.TimeoutExpired(cmd=['report.py'], timeout=30)

    return _FakeSP()


# ===========================================================================
# Group AR — runner_atr_expansion.py
# ===========================================================================

class TestAtrRunner:
    def test_AR01_collect_returns_ok(self, db_path: pathlib.Path) -> None:
        """collect() with 30-row OHLCV returns 'ok' on first call."""
        ohlcv    = _make_ohlcv(30)
        run_date = ohlcv.index[-1]
        result   = runner_atr_expansion.collect(run_date, ohlcv, db_path, 15.0)
        assert result == 'ok'

    def test_AR02_second_call_returns_skipped(self, db_path: pathlib.Path) -> None:
        """Second collect() call for the same run_date is idempotent → 'skipped'."""
        ohlcv    = _make_ohlcv(30)
        run_date = ohlcv.index[-1]
        runner_atr_expansion.collect(run_date, ohlcv, db_path, 15.0)
        result = runner_atr_expansion.collect(run_date, ohlcv, db_path, 15.0)
        assert result == 'skipped'

    def test_AR03_fn_hash_violation_raises(
        self,
        db_path: pathlib.Path,
        atr_pre_reg: pathlib.Path,
        stub_fn_file: pathlib.Path,
    ) -> None:
        """A stub fn_file whose hash != pre_reg.decision_function_hash raises FnHashViolation."""
        ohlcv    = _make_ohlcv(30)
        run_date = ohlcv.index[-1]
        # atr_pre_reg hashes the REAL ATR file; stub_fn_file is a different file.
        with pytest.raises(FnHashViolation):
            run_ohlcv_signal(
                signal_id='g3_atr_expansion',
                run_date=run_date,
                ohlcv=ohlcv,
                compute_fn=_df_atr.compute_signal,
                raw_value_fn=None,
                pre_reg_path=atr_pre_reg,
                fn_file_path=stub_fn_file,
                db_path=db_path,
                hold_days=_df_atr.HOLD_DAYS,
                min_history=_df_atr.MIN_HISTORY,
                cost_bps=15.0,
            )

    def test_AR04_missing_run_date_raises(self, db_path: pathlib.Path) -> None:
        """run_date not in OHLCV index raises DataUnavailableError."""
        ohlcv    = _make_ohlcv(30)
        run_date = '2099-12-31'   # well outside the 2024 synthetic range
        with pytest.raises(DataUnavailableError):
            runner_atr_expansion.collect(run_date, ohlcv, db_path, 15.0)

    def test_AR05_raw_value_returns_float(self) -> None:
        """_raw_value() returns a float when ATR and its lag are computable."""
        ohlcv  = _make_ohlcv(30)   # 30 > MIN_HISTORY=25
        result = runner_atr_expansion._raw_value(ohlcv)
        assert isinstance(result, float)

    def test_AR06_raw_value_returns_none_insufficient_history(self) -> None:
        """_raw_value() returns None when fewer rows than N+LAG+1 (=25)."""
        ohlcv  = _make_ohlcv(5)
        result = runner_atr_expansion._raw_value(ohlcv)
        assert result is None


# ===========================================================================
# Group GC — _check_governance() and main() gate
# ===========================================================================

class TestGovernanceCheck:
    def test_GC01_returns_true_when_report_exits_zero(self, monkeypatch) -> None:
        """_check_governance() returns True when report.py --check exits 0."""
        monkeypatch.setattr(_run_daily, 'subprocess', _sp_mock(0, 'Registry check: OK'))
        assert _run_daily._check_governance(_run_daily._RESEARCH_ROOT) is True

    def test_GC02_returns_false_when_report_exits_nonzero(self, monkeypatch) -> None:
        """_check_governance() returns False and does not raise when exit code != 0."""
        monkeypatch.setattr(
            _run_daily, 'subprocess',
            _sp_mock(1, 'Registry check: 2 error(s)\n  some_family: pinned_run missing'),
        )
        assert _run_daily._check_governance(_run_daily._RESEARCH_ROOT) is False

    def test_GC03_main_aborts_before_collection_when_governance_fails(
        self, monkeypatch,
    ) -> None:
        """main() returns 1 immediately and never calls run_all() if governance fails."""
        monkeypatch.setattr(_run_daily, '_check_governance', lambda _: False)
        monkeypatch.setattr(sys, 'argv', ['run_daily.py', '--run-date', '2026-01-01'])

        run_all_calls: list[tuple] = []
        monkeypatch.setattr(
            _run_daily, 'run_all',
            lambda *a, **kw: run_all_calls.append((a, kw)) or {},
        )

        rc = _run_daily.main()
        assert rc == 1
        assert run_all_calls == [], "run_all must not be called after governance failure"

    def test_GC04_returns_false_on_subprocess_timeout(self, monkeypatch) -> None:
        """subprocess.TimeoutExpired during --check → _check_governance returns False without raising."""
        monkeypatch.setattr(_run_daily, 'subprocess', _sp_timeout_mock())
        assert _run_daily._check_governance(_run_daily._RESEARCH_ROOT) is False


# ===========================================================================
# Group DR — dry-run (no DB writes)
# ===========================================================================

class TestDryRun:
    def test_DR01_dry_run_writes_no_db_rows(
        self, monkeypatch, db_path: pathlib.Path,
    ) -> None:
        """run_all(dry_run=True) must not write any rows to signal_log or trade_entries."""
        fake_closes = _make_closes(300)
        run_date    = fake_closes.index[-1]

        monkeypatch.setattr(_run_daily, 'load_daily_closes', lambda *a, **kw: fake_closes)
        def _dvol_miss(*a, **kw): raise DataUnavailableError("no dvol in test")
        monkeypatch.setattr(_run_daily, 'load_daily_dvol', _dvol_miss)

        def _row_counts() -> dict:
            con = sqlite3.connect(db_path)
            counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                      for t in ('signal_log', 'trade_entries')}
            con.close()
            return counts

        before = _row_counts()
        _run_daily.run_all(run_date, db_path=db_path, dry_run=True)
        after  = _row_counts()

        assert before == after, f"dry-run wrote rows: before={before} after={after}"

    def test_DR02_dry_run_returns_dry_run_outcomes(
        self, monkeypatch, db_path: pathlib.Path,
    ) -> None:
        """Computable signals return 'dry-run'; DVOL-unavailable IVRV returns 'unavailable'."""
        fake_closes = _make_closes(300)
        run_date    = fake_closes.index[-1]

        monkeypatch.setattr(_run_daily, 'load_daily_closes', lambda *a, **kw: fake_closes)
        def _dvol_miss(*a, **kw): raise DataUnavailableError("no dvol in test")
        monkeypatch.setattr(_run_daily, 'load_daily_dvol', _dvol_miss)

        results = _run_daily.run_all(run_date, db_path=db_path, dry_run=True)

        assert results.get('h5_vol_regime')           == 'dry-run'
        assert results.get('g1_market_regime_sma200') == 'dry-run'
        assert results.get('g3_momentum_tsmom')       == 'dry-run'
        assert results.get('ivrv_vrp_spread')         == 'unavailable'
        # g3_atr_expansion enabled since Phase 5 activation (2026-07-19) —
        # now computes alongside the other signals rather than being absent.
        assert results.get('g3_atr_expansion')        == 'dry-run'

    def test_DR03_atr_dry_run_computes_when_enabled(
        self, monkeypatch, tmp_path: pathlib.Path,
    ) -> None:
        """When g3_atr_expansion enabled:true in config, dry-run computes position."""
        # Minimal test config with ATR enabled and H5/G1/G3 present
        test_cfg = {
            'signals': {
                'h5_vol_regime':           {'cost_bps': 15.0},
                'g1_market_regime_sma200': {'cost_bps': 15.0},
                'g3_momentum_tsmom':       {'cost_bps': 15.0},
                'g3_atr_expansion':        {'enabled': True, 'cost_bps': 15.0},
            },
            'ohlcv': {'parquet_dir': 'data/raw/spot/BTCUSDT', 'symbol': 'BTCUSDT'},
            'dvol':  {'parquet_dir': 'data/raw/dvol/BTC',     'symbol': 'BTC'},
        }
        cfg_path = tmp_path / 'test_config.yaml'
        cfg_path.write_text(yaml.dump(test_cfg), encoding='utf-8')
        monkeypatch.setattr(_run_daily, '_CONFIG_PATH', cfg_path)

        ohlcv       = _make_ohlcv(300)
        fake_closes = pd.Series(ohlcv['close'].values, index=ohlcv.index)
        run_date    = ohlcv.index[-1]

        monkeypatch.setattr(_run_daily, 'load_daily_closes', lambda *a, **kw: fake_closes)
        monkeypatch.setattr(_run_daily, 'load_daily_ohlcv',  lambda *a, **kw: ohlcv)
        def _dvol_miss(*a, **kw): raise DataUnavailableError("no dvol in test")
        monkeypatch.setattr(_run_daily, 'load_daily_dvol', _dvol_miss)

        tmp_db = tmp_path / 'ev.db'   # dry-run never writes here, so no init needed
        results = _run_daily.run_all(run_date, db_path=tmp_db, dry_run=True)

        assert results.get('g3_atr_expansion') == 'dry-run'
        assert results.get('h5_vol_regime')    == 'dry-run'

    def test_DR04_ivrv_compute_exception_returns_error_not_propagated(
        self, monkeypatch, tmp_path: pathlib.Path,
    ) -> None:
        """An unexpected exception from _ivrv.compute_signal in dry-run yields 'error', not a crash."""
        fake_closes = _make_closes(300)
        fake_dvol   = _make_closes(300, seed=7)
        run_date    = fake_closes.index[-1]

        monkeypatch.setattr(_run_daily, 'load_daily_closes', lambda *a, **kw: fake_closes)
        monkeypatch.setattr(_run_daily, 'load_daily_dvol',   lambda *a, **kw: fake_dvol)

        def _ivrv_raise(*a, **kw):
            raise ValueError("alignment error from test")
        monkeypatch.setattr(_run_daily._ivrv, 'compute_signal', _ivrv_raise)

        tmp_db  = tmp_path / 'ev.db'
        results = _run_daily.run_all(run_date, db_path=tmp_db, dry_run=True)

        assert results.get('ivrv_vrp_spread')         == 'error'
        assert results.get('h5_vol_regime')           == 'dry-run'
        assert results.get('g1_market_regime_sma200') == 'dry-run'
        assert results.get('g3_momentum_tsmom')       == 'dry-run'

    def test_DR05_atr_compute_exception_returns_error_not_propagated(
        self, monkeypatch, tmp_path: pathlib.Path,
    ) -> None:
        """An unexpected exception from _atr.compute_signal in dry-run yields 'error', not a crash."""
        test_cfg = {
            'signals': {
                'h5_vol_regime':           {'cost_bps': 15.0},
                'g1_market_regime_sma200': {'cost_bps': 15.0},
                'g3_momentum_tsmom':       {'cost_bps': 15.0},
                'g3_atr_expansion':        {'enabled': True, 'cost_bps': 15.0},
            },
            'ohlcv': {'parquet_dir': 'data/raw/spot/BTCUSDT', 'symbol': 'BTCUSDT'},
            'dvol':  {'parquet_dir': 'data/raw/dvol/BTC',     'symbol': 'BTC'},
        }
        cfg_path = tmp_path / 'test_config.yaml'
        cfg_path.write_text(yaml.dump(test_cfg), encoding='utf-8')
        monkeypatch.setattr(_run_daily, '_CONFIG_PATH', cfg_path)

        ohlcv       = _make_ohlcv(300)
        fake_closes = pd.Series(ohlcv['close'].values, index=ohlcv.index)
        run_date    = ohlcv.index[-1]

        monkeypatch.setattr(_run_daily, 'load_daily_closes', lambda *a, **kw: fake_closes)
        monkeypatch.setattr(_run_daily, 'load_daily_ohlcv',  lambda *a, **kw: ohlcv)
        def _dvol_miss(*a, **kw): raise DataUnavailableError("no dvol in test")
        monkeypatch.setattr(_run_daily, 'load_daily_dvol', _dvol_miss)

        def _atr_raise(*a, **kw):
            raise ValueError("shape mismatch from test")
        monkeypatch.setattr(_run_daily._atr, 'compute_signal', _atr_raise)

        tmp_db  = tmp_path / 'ev.db'
        results = _run_daily.run_all(run_date, db_path=tmp_db, dry_run=True)

        assert results.get('g3_atr_expansion')        == 'error'
        assert results.get('h5_vol_regime')           == 'dry-run'
        assert results.get('g1_market_regime_sma200') == 'dry-run'
        assert results.get('g3_momentum_tsmom')       == 'dry-run'


# ===========================================================================
# Group GA — ATR gating in run_daily.run_all()
# ===========================================================================

class TestAtrGating:
    def test_GA01_production_config_has_atr_enabled(self) -> None:
        """Phase 5 activation gate: g3_atr_expansion must be enabled:true in production
        config (activated 2026-07-19; see collection_config.yaml's own comment).
        A redeploy must not silently revert this to disabled."""
        cfg     = yaml.safe_load(_run_daily._CONFIG_PATH.read_text(encoding='utf-8'))
        signals = cfg.get('signals', {})
        assert 'g3_atr_expansion' in signals, (
            "g3_atr_expansion missing from collection_config.yaml signals section"
        )
        atr_cfg = signals['g3_atr_expansion']
        assert atr_cfg.get('enabled', False) is True, (
            "g3_atr_expansion must have enabled: true — Phase 5 activation "
            "(2026-07-19) is in effect"
        )

    def test_GA02_atr_included_in_results_when_enabled(
        self, monkeypatch, db_path: pathlib.Path,
    ) -> None:
        """With enabled:true (production config, Phase 5), g3_atr_expansion appears
        in run_all() results, and core signals remain unaffected either way."""
        fake_closes = _make_closes(300)
        run_date    = fake_closes.index[-1]

        monkeypatch.setattr(_run_daily, 'load_daily_closes', lambda *a, **kw: fake_closes)
        monkeypatch.setattr(_run_daily.runner_h5, 'collect', lambda *a, **kw: 'ok')
        monkeypatch.setattr(_run_daily.runner_g1, 'collect', lambda *a, **kw: 'ok')
        monkeypatch.setattr(_run_daily.runner_g3, 'collect', lambda *a, **kw: 'ok')
        def _dvol_miss(*a, **kw): raise DataUnavailableError("no dvol in test")
        monkeypatch.setattr(_run_daily, 'load_daily_dvol', _dvol_miss)

        results = _run_daily.run_all(run_date, db_path=db_path)

        assert 'g3_atr_expansion' in results
        # Core signals must be unaffected by the ATR gate check
        assert results['h5_vol_regime']           == 'ok'
        assert results['g1_market_regime_sma200'] == 'ok'
        assert results['g3_momentum_tsmom']       == 'ok'

    def test_GA03_atr_ohlcv_failure_isolated_from_other_signals(
        self, monkeypatch, tmp_path: pathlib.Path, db_path: pathlib.Path,
    ) -> None:
        """An OHLCV load failure for ATR degrades only ATR; H5/G1/G3 remain 'ok'."""
        # Config with ATR enabled to exercise the OHLCV branch
        test_cfg = {
            'signals': {
                'h5_vol_regime':           {'cost_bps': 15.0},
                'g1_market_regime_sma200': {'cost_bps': 15.0},
                'g3_momentum_tsmom':       {'cost_bps': 15.0},
                'g3_atr_expansion':        {'enabled': True, 'cost_bps': 15.0},
            },
            'ohlcv': {'parquet_dir': 'data/raw/spot/BTCUSDT', 'symbol': 'BTCUSDT'},
            'dvol':  {'parquet_dir': 'data/raw/dvol/BTC',     'symbol': 'BTC'},
        }
        cfg_path = tmp_path / 'test_config.yaml'
        cfg_path.write_text(yaml.dump(test_cfg), encoding='utf-8')
        monkeypatch.setattr(_run_daily, '_CONFIG_PATH', cfg_path)

        fake_closes = _make_closes(300)
        run_date    = fake_closes.index[-1]

        monkeypatch.setattr(_run_daily, 'load_daily_closes', lambda *a, **kw: fake_closes)
        monkeypatch.setattr(_run_daily.runner_h5, 'collect', lambda *a, **kw: 'ok')
        monkeypatch.setattr(_run_daily.runner_g1, 'collect', lambda *a, **kw: 'ok')
        monkeypatch.setattr(_run_daily.runner_g3, 'collect', lambda *a, **kw: 'ok')

        def _ohlcv_miss(*a, **kw): raise DataUnavailableError("no ohlcv in test")
        monkeypatch.setattr(_run_daily, 'load_daily_ohlcv', _ohlcv_miss)
        def _dvol_miss(*a, **kw): raise DataUnavailableError("no dvol in test")
        monkeypatch.setattr(_run_daily, 'load_daily_dvol', _dvol_miss)

        results = _run_daily.run_all(run_date, db_path=db_path)

        # ATR gracefully degraded
        assert results.get('g3_atr_expansion') == 'unavailable'
        # H5/G1/G3 completely unaffected
        assert results['h5_vol_regime']           == 'ok'
        assert results['g1_market_regime_sma200'] == 'ok'
        assert results['g3_momentum_tsmom']       == 'ok'
