"""
Tests for Phase 2 ATR Expansion infrastructure.

Run from research/:
    conda run -n btc_bot pytest forward_evidence/tests/test_atr_expansion.py -v

Groups:
  OL — OHLCV loader (load_daily_ohlcv)
  DFA — decision_function_g3_atr_expansion
  RO — run_ohlcv_signal
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
from datetime import date, timedelta
from typing import Callable

import numpy as np
import pandas as pd
import pytest

from forward_evidence import decision_function_g3_atr_expansion as df_atr
from forward_evidence.init_forward_db import init_forward_db
from forward_evidence.pre_reg_guard import FnHashViolation, _sha256_lf
from forward_evidence.runner_base import (
    DataUnavailableError,
    load_daily_ohlcv,
    run_ohlcv_signal,
    run_signal,          # imported for regression check RO09
    load_daily_closes,   # imported for regression check RO09
)


# ===========================================================================
# Shared helpers and fixtures
# ===========================================================================

def make_ohlcv(n: int, seed: int = 42, dates_start: str = '2021-01-01') -> pd.DataFrame:
    """n daily rows with random walk prices; high >= close >= low guaranteed."""
    rng   = np.random.default_rng(seed)
    close = 40_000.0 * np.exp(np.cumsum(rng.normal(0, 0.015, n)))
    spread = np.abs(rng.normal(0, 0.008, n)) * close
    high  = close + spread
    low   = close - spread
    idx   = pd.date_range(dates_start, periods=n, freq='D').strftime('%Y-%m-%d')
    return pd.DataFrame({'high': high, 'low': low, 'close': close}, index=idx)


def make_ohlcv_trending_up(n: int = 40) -> pd.DataFrame:
    """Steadily rising prices with expanding intraday ranges (ATR grows)."""
    close  = np.linspace(40_000.0, 50_000.0, n)
    spread = np.linspace(100.0, 1_500.0, n)   # widening range → ATR expands
    high   = close + spread
    low    = close - spread
    idx    = pd.date_range('2021-01-01', periods=n, freq='D').strftime('%Y-%m-%d')
    return pd.DataFrame({'high': high, 'low': low, 'close': close}, index=idx)


def make_ohlcv_trending_down(n: int = 40) -> pd.DataFrame:
    """Steadily falling prices with expanding intraday ranges."""
    close  = np.linspace(50_000.0, 40_000.0, n)
    spread = np.linspace(100.0, 1_500.0, n)
    high   = close + spread
    low    = close - spread
    idx    = pd.date_range('2021-01-01', periods=n, freq='D').strftime('%Y-%m-%d')
    return pd.DataFrame({'high': high, 'low': low, 'close': close}, index=idx)


@pytest.fixture()
def db_path(tmp_path: pathlib.Path) -> pathlib.Path:
    p = tmp_path / "evidence_forward.db"
    init_forward_db(p)
    return p


@pytest.fixture()
def atr_fn_file() -> pathlib.Path:
    """The real decision_function_g3_atr_expansion.py file."""
    here = pathlib.Path(__file__).resolve().parent.parent
    f = here / "decision_function_g3_atr_expansion.py"
    assert f.exists(), f"Decision function not found: {f}"
    return f


@pytest.fixture()
def atr_pre_reg(tmp_path: pathlib.Path, atr_fn_file: pathlib.Path) -> pathlib.Path:
    h = _sha256_lf(atr_fn_file)
    data = {
        'schema_version': 1,
        'version': '1.0.0',
        'signal_id': 'g3_atr_expansion',
        'pre_registration_date': '2026-06-26T00:00:00Z',
        'function_spec': {'decision_function_hash': h},
    }
    p = tmp_path / 'prereg_g3_atr_expansion.json'
    p.write_text(json.dumps(data), encoding='utf-8')
    return p


@pytest.fixture()
def stub_fn_file(tmp_path: pathlib.Path) -> pathlib.Path:
    """Tiny stub for runner tests that don't need the real ATR logic."""
    f = tmp_path / 'decision_function_stub.py'
    f.write_bytes(b"def compute_signal(ohlcv):\n    import pandas as pd\n    return pd.Series(1.0, index=ohlcv.index)\n")
    return f


@pytest.fixture()
def stub_pre_reg(tmp_path: pathlib.Path, stub_fn_file: pathlib.Path) -> pathlib.Path:
    h = _sha256_lf(stub_fn_file)
    data = {
        'schema_version': 1,
        'signal_id': 'test_atr',
        'function_spec': {'decision_function_hash': h},
    }
    p = tmp_path / 'prereg_stub.json'
    p.write_text(json.dumps(data), encoding='utf-8')
    return p


def _fixed_ohlcv_signal(position: float) -> Callable:
    def compute_fn(ohlcv: pd.DataFrame) -> pd.Series:
        return pd.Series(position, index=ohlcv.index, dtype=float)
    return compute_fn


def _stub_raw_ohlcv(ohlcv: pd.DataFrame) -> float | None:
    return 1.23


MIN_HIST_STUB = 5


def _run_ohlcv(
    *,
    signal_id:   str = 'test_atr',
    run_date:    str,
    ohlcv:       pd.DataFrame,
    db_path:     pathlib.Path,
    fn_file:     pathlib.Path,
    pre_reg:     pathlib.Path,
    position:    float = 1.0,
    hold_days:   int   = 7,
    cost_bps:    float = 15.0,
    min_history: int   = MIN_HIST_STUB,
) -> str:
    return run_ohlcv_signal(
        signal_id=signal_id,
        run_date=run_date,
        ohlcv=ohlcv,
        compute_fn=_fixed_ohlcv_signal(position),
        raw_value_fn=_stub_raw_ohlcv,
        pre_reg_path=pre_reg,
        fn_file_path=fn_file,
        db_path=db_path,
        hold_days=hold_days,
        min_history=min_history,
        cost_bps=cost_bps,
    )


def _row_counts(db: pathlib.Path) -> dict:
    c = sqlite3.connect(db)
    r = {
        'run_log':       c.execute("SELECT COUNT(*) FROM run_log").fetchone()[0],
        'signal_log':    c.execute("SELECT COUNT(*) FROM signal_log").fetchone()[0],
        'trade_entries': c.execute("SELECT COUNT(*) FROM trade_entries").fetchone()[0],
        'trade_exits':   c.execute("SELECT COUNT(*) FROM trade_exits").fetchone()[0],
    }
    c.close()
    return r


# ===========================================================================
# Group OL — OHLCV loader
# ===========================================================================

@pytest.fixture()
def ohlcv_parquet_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """
    3-day Parquet fixture with intraday high/low spikes on day 0.

    Day 0 minute 0: high=50000 (spike), low=30000 (spike)
    Day 0 minute 1..1439: high=40100, low=39900
    → true daily high=50000, daily low=30000, daily close=last-minute close
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    rows = []
    for day_offset in range(3):
        base_ms = int(
            (pd.Timestamp('2024-12-01', tz='UTC') + timedelta(days=day_offset))
            .timestamp() * 1000
        )
        for minute in range(1440):
            if day_offset == 0 and minute == 0:
                h, l = 50_000.0, 30_000.0
            else:
                h, l = 40_100.0, 39_900.0
            rows.append({
                'open_time': base_ms + minute * 60_000,
                'open':  40_000.0,
                'high':  h,
                'low':   l,
                'close': 40_050.0 + day_offset * 100 + minute * 0.01,
                'volume': 1.0,
            })
    df = pd.DataFrame(rows).astype({
        'open_time': 'int64', 'open': 'float64', 'high': 'float64',
        'low': 'float64', 'close': 'float64', 'volume': 'float64',
    })
    part_dir = tmp_path / "year=2024" / "month=12"
    part_dir.mkdir(parents=True)
    pq.write_table(
        pa.Table.from_pandas(df, preserve_index=False),
        part_dir / 'data.parquet',
    )
    return tmp_path


class TestOhlcvLoader:

    def test_OL01_returns_dataframe_with_hlc_columns(
        self, ohlcv_parquet_dir: pathlib.Path
    ) -> None:
        df = load_daily_ohlcv(ohlcv_parquet_dir, '2024-12-03', min_rows=1)
        assert isinstance(df, pd.DataFrame)
        assert set(df.columns) == {'high', 'low', 'close'}

    def test_OL02_daily_high_is_max_not_last(
        self, ohlcv_parquet_dir: pathlib.Path
    ) -> None:
        df = load_daily_ohlcv(ohlcv_parquet_dir, '2024-12-03', min_rows=1)
        # Day 0 has a spike high of 50000 at minute 0; last-minute high is 40100.
        assert df['high']['2024-12-01'] == pytest.approx(50_000.0)

    def test_OL03_daily_low_is_min_not_last(
        self, ohlcv_parquet_dir: pathlib.Path
    ) -> None:
        df = load_daily_ohlcv(ohlcv_parquet_dir, '2024-12-03', min_rows=1)
        # Day 0 has a spike low of 30000 at minute 0; last-minute low is 39900.
        assert df['low']['2024-12-01'] == pytest.approx(30_000.0)

    def test_OL04_daily_close_is_last_minute_bar(
        self, ohlcv_parquet_dir: pathlib.Path
    ) -> None:
        df = load_daily_ohlcv(ohlcv_parquet_dir, '2024-12-01', min_rows=1)
        expected = 40_050.0 + 1439 * 0.01   # last minute of day 0
        assert df['close']['2024-12-01'] == pytest.approx(expected, abs=0.02)

    def test_OL05_through_date_absent_raises(
        self, ohlcv_parquet_dir: pathlib.Path
    ) -> None:
        with pytest.raises(DataUnavailableError, match='2024-12-05'):
            load_daily_ohlcv(ohlcv_parquet_dir, '2024-12-05', min_rows=1)

    def test_OL06_insufficient_history_raises(
        self, ohlcv_parquet_dir: pathlib.Path
    ) -> None:
        with pytest.raises(DataUnavailableError, match='Insufficient'):
            load_daily_ohlcv(ohlcv_parquet_dir, '2024-12-03', min_rows=100)

    def test_OL07_microsecond_timestamps_normalised(self, tmp_path: pathlib.Path) -> None:
        """16-digit µs timestamps are divided by 1000; result same as ms version."""
        import pyarrow as pa
        import pyarrow.parquet as pq

        base_ms = int(pd.Timestamp('2024-12-01', tz='UTC').timestamp() * 1000)
        rows_ms = [{
            'open_time': base_ms + m * 60_000,
            'open': 40_000.0, 'high': 40_100.0, 'low': 39_900.0,
            'close': 40_050.0 + m * 0.01, 'volume': 1.0,
        } for m in range(1440)]
        rows_us = [{**r, 'open_time': r['open_time'] * 1000} for r in rows_ms]

        def _write(rows, sub):
            df = pd.DataFrame(rows).astype({'open_time': 'int64',
                'open': 'float64', 'high': 'float64',
                'low': 'float64', 'close': 'float64', 'volume': 'float64'})
            d = tmp_path / sub / "year=2024" / "month=12"
            d.mkdir(parents=True)
            pq.write_table(pa.Table.from_pandas(df, preserve_index=False), d / 'data.parquet')
            return tmp_path / sub

        dir_ms = _write(rows_ms, 'ms')
        dir_us = _write(rows_us, 'us')

        ohlcv_ms = load_daily_ohlcv(dir_ms, '2024-12-01', min_rows=1)
        ohlcv_us = load_daily_ohlcv(dir_us, '2024-12-01', min_rows=1)
        pd.testing.assert_frame_equal(ohlcv_ms, ohlcv_us)

    def test_OL08_no_partitions_raises(self, tmp_path: pathlib.Path) -> None:
        with pytest.raises(DataUnavailableError, match='No OHLCV partitions'):
            load_daily_ohlcv(tmp_path, '2024-12-01', min_rows=1)


# ===========================================================================
# Group DFA — decision_function_g3_atr_expansion
# ===========================================================================

class TestDecisionFnAtr:

    def test_DFA01_output_values_in_set(self) -> None:
        ohlcv = make_ohlcv(50)
        pos   = df_atr.compute_signal(ohlcv)
        assert set(pos.dropna().unique()).issubset({-1.0, 0.0, 1.0})

    def test_DFA02_warmup_rows_are_zero(self) -> None:
        """Rows 0..N+LAG-2 (indices 0..22, i.e. first 23 rows) are always 0."""
        ohlcv = make_ohlcv(50)
        pos   = df_atr.compute_signal(ohlcv)
        warmup_end = df_atr.N + df_atr.LAG - 1   # = 23
        assert (pos.iloc[:warmup_end] == 0.0).all()

    def test_DFA03_flat_when_insufficient_rows(self) -> None:
        """With fewer rows than N+LAG-1, all positions are 0."""
        ohlcv = make_ohlcv(23)   # exactly N+LAG-1 rows — last row still has NaN lagged ATR
        pos   = df_atr.compute_signal(ohlcv)
        assert (pos == 0.0).all()

    def test_DFA04_flat_when_atr_not_expanding(self) -> None:
        """Constant prices → TR=0 always → ATR constant → not expanding → all flat."""
        n = 50
        const = 40_000.0
        idx   = pd.date_range('2021-01-01', periods=n, freq='D').strftime('%Y-%m-%d')
        ohlcv = pd.DataFrame({
            'high': [const] * n,
            'low':  [const] * n,
            'close':[const] * n,
        }, index=idx)
        pos = df_atr.compute_signal(ohlcv)
        assert (pos == 0.0).all()

    def test_DFA05_positive_when_expanding_uptrend(self) -> None:
        """Rising close + widening range → ATR expands → position = +1 at last bar."""
        ohlcv = make_ohlcv_trending_up(n=40)
        pos   = df_atr.compute_signal(ohlcv)
        assert pos.iloc[-1] == pytest.approx(1.0)

    def test_DFA06_negative_when_expanding_downtrend(self) -> None:
        """Falling close + widening range → ATR expands → position = -1 at last bar."""
        ohlcv = make_ohlcv_trending_down(n=40)
        pos   = df_atr.compute_signal(ohlcv)
        assert pos.iloc[-1] == pytest.approx(-1.0)

    def test_DFA07_cross_validation_vs_signals_py(self) -> None:
        """compute_signal values must exactly match sig_atr_expansion (signals.py)."""
        import sys
        research_evidence = pathlib.Path(__file__).resolve().parents[2] / "evidence"
        sys.path.insert(0, str(research_evidence))
        from signals import sig_atr_expansion  # noqa: PLC0415

        rng   = np.random.default_rng(7)
        n     = 200
        close = 40_000.0 * np.exp(np.cumsum(rng.normal(0, 0.015, n)))
        sp    = np.abs(rng.normal(0, 0.008, n)) * close
        high  = close + sp
        low   = close - sp

        dates = pd.date_range('2021-01-01', periods=n, freq='D').strftime('%Y-%m-%d')

        # signals.py expects integer-indexed m with high/low/close columns
        m     = pd.DataFrame({'high': high, 'low': low, 'close': close})
        ohlcv = pd.DataFrame({'high': high, 'low': low, 'close': close}, index=dates)

        ref_pos = sig_atr_expansion(m, n=df_atr.N, lag=df_atr.LAG)
        df_pos  = df_atr.compute_signal(ohlcv)

        np.testing.assert_array_equal(ref_pos.values, df_pos.values,
            err_msg="compute_signal diverges from sig_atr_expansion in signals.py")

    def test_DFA08_sensitivity_variants_has_five_entries(self) -> None:
        variants = df_atr.sensitivity_variants()
        assert len(variants) == 5
        assert {'n': 21, 'lag': 20} in variants


# ===========================================================================
# Group RO — run_ohlcv_signal
# ===========================================================================

class TestRunOhlcvSignal:

    def _ohlcv(self, n: int = 10) -> pd.DataFrame:
        return make_ohlcv(n, dates_start='2021-01-01')

    def _run_date(self, ohlcv: pd.DataFrame) -> str:
        return ohlcv.index[-1]

    def test_RO01_first_call_returns_ok(self, db_path, stub_fn_file, stub_pre_reg) -> None:
        ohlcv = self._ohlcv()
        result = _run_ohlcv(
            run_date=self._run_date(ohlcv),
            ohlcv=ohlcv, db_path=db_path,
            fn_file=stub_fn_file, pre_reg=stub_pre_reg,
        )
        assert result == 'ok'

    def test_RO02_second_call_is_skipped(self, db_path, stub_fn_file, stub_pre_reg) -> None:
        ohlcv = self._ohlcv()
        rd    = self._run_date(ohlcv)
        _run_ohlcv(run_date=rd, ohlcv=ohlcv, db_path=db_path,
                   fn_file=stub_fn_file, pre_reg=stub_pre_reg)
        result = _run_ohlcv(run_date=rd, ohlcv=ohlcv, db_path=db_path,
                            fn_file=stub_fn_file, pre_reg=stub_pre_reg)
        assert result == 'skipped'

    def test_RO03_prior_error_raises_runtime_error(
        self, db_path, stub_fn_file, stub_pre_reg
    ) -> None:
        ohlcv = self._ohlcv()
        rd    = self._run_date(ohlcv)
        # inject error row directly
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO run_log (signal_id, run_date, fn_hash, status) VALUES (?,?,?,?)",
            ('test_atr', rd, 'aabbcc', 'error'),
        )
        conn.commit()
        conn.close()
        with pytest.raises(RuntimeError, match='prior run errored'):
            _run_ohlcv(run_date=rd, ohlcv=ohlcv, db_path=db_path,
                       fn_file=stub_fn_file, pre_reg=stub_pre_reg)

    def test_RO04_fn_hash_violation_raises(
        self, tmp_path, db_path, stub_fn_file
    ) -> None:
        """Pre-reg JSON with wrong hash → FnHashViolation."""
        bad_prereg = tmp_path / 'bad_prereg.json'
        bad_prereg.write_text(json.dumps({
            'schema_version': 1,
            'signal_id': 'test_atr',
            'function_spec': {'decision_function_hash': 'deadbeef' * 8},
        }), encoding='utf-8')
        ohlcv = self._ohlcv()
        with pytest.raises(FnHashViolation):
            _run_ohlcv(
                run_date=self._run_date(ohlcv), ohlcv=ohlcv, db_path=db_path,
                fn_file=stub_fn_file, pre_reg=bad_prereg,
            )

    def test_RO05_all_rows_written_on_ok(self, db_path, stub_fn_file, stub_pre_reg) -> None:
        ohlcv = self._ohlcv()
        _run_ohlcv(run_date=self._run_date(ohlcv), ohlcv=ohlcv, db_path=db_path,
                   fn_file=stub_fn_file, pre_reg=stub_pre_reg, position=1.0)
        counts = _row_counts(db_path)
        assert counts['run_log']       == 1
        assert counts['signal_log']    == 1
        assert counts['trade_entries'] == 1

    def test_RO06_entry_price_equals_close_at_run_date(
        self, db_path, stub_fn_file, stub_pre_reg
    ) -> None:
        ohlcv = self._ohlcv()
        rd    = self._run_date(ohlcv)
        _run_ohlcv(run_date=rd, ohlcv=ohlcv, db_path=db_path,
                   fn_file=stub_fn_file, pre_reg=stub_pre_reg, position=1.0)
        conn = sqlite3.connect(db_path)
        row  = conn.execute(
            "SELECT entry_close_price FROM trade_entries WHERE signal_id='test_atr'"
        ).fetchone()
        conn.close()
        expected_close = float(ohlcv['close'].loc[rd])
        assert row[0] == pytest.approx(expected_close, rel=1e-9)

    def test_RO07_missing_run_date_raises(self, db_path, stub_fn_file, stub_pre_reg) -> None:
        ohlcv = self._ohlcv()
        with pytest.raises(DataUnavailableError, match='not in frame'):
            _run_ohlcv(run_date='2025-12-31', ohlcv=ohlcv, db_path=db_path,
                       fn_file=stub_fn_file, pre_reg=stub_pre_reg)

    def test_RO08_insufficient_history_raises(self, db_path, stub_fn_file, stub_pre_reg) -> None:
        ohlcv = self._ohlcv(n=3)
        with pytest.raises(DataUnavailableError, match='Insufficient'):
            _run_ohlcv(
                run_date=self._run_date(ohlcv), ohlcv=ohlcv, db_path=db_path,
                fn_file=stub_fn_file, pre_reg=stub_pre_reg,
                min_history=10,
            )

    def test_RO09_overdue_trade_closed_at_exit_date_close(
        self, db_path, stub_fn_file, stub_pre_reg
    ) -> None:
        """Open trade then advance past exit; exit price = ohlcv['close'].loc[exit_date]."""
        n     = 20
        ohlcv = make_ohlcv(n, dates_start='2021-01-01')
        d0    = ohlcv.index[n - 11]   # entry date (10 days before last)
        d_exit= ohlcv.index[n - 11 + 7]  # planned_exit = entry + 7 days
        d_run = ohlcv.index[-1]         # run date is after planned_exit

        # Step 1: open trade on d0 (position=+1, hold=7)
        _run_ohlcv(run_date=d0, ohlcv=ohlcv[:ohlcv.index.get_loc(d0) + 1],
                   db_path=db_path, fn_file=stub_fn_file, pre_reg=stub_pre_reg,
                   position=1.0, hold_days=7)

        # Step 2: run on d_run (past d_exit) — trade should be closed
        _run_ohlcv(run_date=d_run, ohlcv=ohlcv, db_path=db_path,
                   fn_file=stub_fn_file, pre_reg=stub_pre_reg,
                   position=1.0, hold_days=7)

        conn = sqlite3.connect(db_path)
        exit_row = conn.execute(
            "SELECT exit_date, exit_close_price FROM trade_exits"
        ).fetchone()
        conn.close()

        assert exit_row is not None
        assert exit_row[0] == d_exit
        expected = float(ohlcv['close'].loc[d_exit])
        assert exit_row[1] == pytest.approx(expected, rel=1e-9)

    def test_RO10_regression_run_signal_unchanged(
        self, tmp_path, db_path
    ) -> None:
        """Verify existing run_signal() still works after runner_base.py additions."""
        closes_fn = tmp_path / 'decision_fn_closes.py'
        closes_fn.write_bytes(b"def compute_signal(c):\n    import pandas as pd\n    return pd.Series(1.0, index=c.index)\n")
        h = _sha256_lf(closes_fn)
        prereg = tmp_path / 'prereg_closes.json'
        prereg.write_text(json.dumps({
            'schema_version': 1, 'signal_id': 'reg_test',
            'function_spec': {'decision_function_hash': h},
        }), encoding='utf-8')

        n      = 10
        closes = pd.Series(
            [40_000.0 * (1.001 ** i) for i in range(n)],
            index=pd.date_range('2021-01-01', periods=n, freq='D').strftime('%Y-%m-%d'),
        )
        result = run_signal(
            signal_id='reg_test',
            run_date=closes.index[-1],
            closes=closes,
            compute_fn=lambda c: pd.Series(1.0, index=c.index),
            raw_value_fn=lambda c: 1.23,
            pre_reg_path=prereg,
            fn_file_path=closes_fn,
            db_path=db_path,
            hold_days=7,
            min_history=5,
            cost_bps=15.0,
        )
        assert result == 'ok'
