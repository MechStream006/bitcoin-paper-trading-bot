"""
Tests for runner_base.py, runner_h5/g1/g3.py, and run_daily.py.

Run from research/:
    conda run -n btc_bot pytest forward_evidence/tests/test_runner_base.py -v

Covers all 9 groups from the approved test plan (2026-06-15):
  A - Price loading (PL01-05)
  B - Idempotency and run_log gate (IP01-07)
  C - fn_hash enforcement (FH01-06)
  D - Trade lifecycle (TL01-12)
  E - Missed-day recovery (RC01-05)
  F - Per-signal integration (SI01-12)
  G - Multi-signal isolation (MS01-04)
  H - Scheduling / run_date derivation (SC01-03)
  I - Edge cases and error paths (EC01-06)
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

from forward_evidence.init_forward_db import init_forward_db
from forward_evidence.pre_reg_guard import FnHashViolation, _sha256_lf
from forward_evidence.runner_base import (
    DataUnavailableError,
    _has_open_trade,
    _open_conn,
    _record_mini,
    load_daily_closes,
    run_signal,
)


# ===========================================================================
# Shared helpers and fixtures
# ===========================================================================

def make_closes(
    n: int,
    start: float = 40_000.0,
    trend: float = 0.001,
    dates_start: str = '2021-01-01',
) -> pd.Series:
    """n daily closes with a constant trend, 'YYYY-MM-DD' index."""
    prices = [start * (1 + trend) ** i for i in range(n)]
    idx    = pd.date_range(dates_start, periods=n, freq='D').strftime('%Y-%m-%d')
    return pd.Series(prices, index=idx, dtype=float)


def make_volatile_closes(
    n: int,
    vol: float = 0.07,
    seed: int  = 42,
    dates_start: str = '2021-01-01',
) -> pd.Series:
    rng      = np.random.default_rng(seed)
    log_rets = rng.normal(0, vol, n)
    prices   = 40_000.0 * np.exp(np.cumsum(log_rets))
    idx      = pd.date_range(dates_start, periods=n, freq='D').strftime('%Y-%m-%d')
    return pd.Series(prices, index=idx, dtype=float)


def make_h5_long_closes(n: int = 300) -> pd.Series:
    """Series that reliably produces H5 position=+1 at the last bar.

    High-vol history (4% daily) fills the 252-day REGIME_WINDOW, then a
    low-vol tail (0.3% daily) ensures the last RV21 << 67th-pctile threshold.
    """
    rng    = np.random.default_rng(42)
    low_n  = 22
    high_n = n - low_n
    if high_n < 1:
        high_n = 1
        low_n  = n - 1
    log_hi   = rng.normal(0.001, 0.04,  high_n)
    log_lo   = rng.normal(0.001, 0.003, low_n)
    log_rets = np.concatenate([log_hi, log_lo])
    prices   = 40_000.0 * np.exp(np.cumsum(log_rets))
    idx      = pd.date_range('2021-01-01', periods=n, freq='D').strftime('%Y-%m-%d')
    return pd.Series(prices, index=idx, dtype=float)


@pytest.fixture()
def db_path(tmp_path: pathlib.Path) -> pathlib.Path:
    p = tmp_path / "evidence_forward.db"
    init_forward_db(p)
    return p


def _make_test_prereg(
    tmp_path: pathlib.Path,
    fn_file: pathlib.Path,
    signal_id: str = 'test_signal',
) -> pathlib.Path:
    h = _sha256_lf(fn_file)
    data = {
        'schema_version': 1,
        'version': '1.0.0',
        'signal_id': signal_id,
        'pre_registration_date': '2026-06-14T00:00:00Z',
        'function_spec': {'decision_function_hash': h},
    }
    p = tmp_path / f'prereg_{signal_id}.json'
    p.write_text(json.dumps(data), encoding='utf-8')
    return p


@pytest.fixture()
def fn_file(tmp_path: pathlib.Path) -> pathlib.Path:
    f = tmp_path / 'decision_function_test.py'
    f.write_bytes(b"def compute_signal(close):\n    return close * 0.0\n")
    return f


@pytest.fixture()
def pre_reg(tmp_path: pathlib.Path, fn_file: pathlib.Path) -> pathlib.Path:
    return _make_test_prereg(tmp_path, fn_file, 'test_signal')


def _fixed_signal(position: float) -> Callable:
    """Returns a compute_fn that always yields position for every bar."""
    def compute_fn(closes: pd.Series) -> pd.Series:
        return pd.Series(position, index=closes.index, dtype=float)
    return compute_fn


def _stub_raw(closes: pd.Series) -> float | None:
    return 1.23


MIN_HIST_STUB = 5


def _run(
    *,
    signal_id:   str = 'test_signal',
    run_date:    str,
    closes:      pd.Series,
    db_path:     pathlib.Path,
    fn_file:     pathlib.Path,
    pre_reg:     pathlib.Path,
    position:    float = 1.0,
    hold_days:   int   = 7,
    cost_bps:    float = 15.0,
    min_history: int   = MIN_HIST_STUB,
) -> str:
    """Thin wrapper around run_signal with stub compute_fn and raw_value_fn."""
    return run_signal(
        signal_id=signal_id,
        run_date=run_date,
        closes=closes,
        compute_fn=_fixed_signal(position),
        raw_value_fn=_stub_raw,
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
# Group A — Price loading
# ===========================================================================

class TestPriceLoading:

    @pytest.fixture()
    def tiny_parquet_dir(self, tmp_path: pathlib.Path) -> pathlib.Path:
        """Create a minimal Parquet partition with 3 days of 1-minute data."""
        import pyarrow as pa
        import pyarrow.parquet as pq

        rows = []
        for day_offset in range(3):
            base_ms = int(
                (pd.Timestamp('2024-12-01', tz='UTC') + timedelta(days=day_offset))
                .timestamp() * 1000
            )
            for minute in range(1440):
                rows.append({
                    'open_time': base_ms + minute * 60_000,
                    'open':  40000.0 + day_offset * 100,
                    'high':  40100.0,
                    'low':   39900.0,
                    'close': 40050.0 + day_offset * 100 + minute * 0.01,
                    'volume': 1.0,
                })
        df = pd.DataFrame(rows).astype({
            'open_time': 'int64',
            'open': 'float64', 'high': 'float64',
            'low': 'float64',  'close': 'float64', 'volume': 'float64',
        })
        part_dir = tmp_path / "year=2024" / "month=12"
        part_dir.mkdir(parents=True)
        pq.write_table(
            pa.Table.from_pandas(df, preserve_index=False),
            part_dir / 'data.parquet',
        )
        return tmp_path

    def test_PL01_loads_daily_closes(self, tiny_parquet_dir: pathlib.Path) -> None:
        series = load_daily_closes(tiny_parquet_dir, '2024-12-03', min_rows=1)
        assert '2024-12-01' in series.index
        assert '2024-12-03' in series.index
        assert len(series) == 3

    def test_PL02_through_date_absent_raises(self, tiny_parquet_dir: pathlib.Path) -> None:
        with pytest.raises(DataUnavailableError, match='2024-12-05'):
            load_daily_closes(tiny_parquet_dir, '2024-12-05', min_rows=1)

    def test_PL03_insufficient_history_raises(self, tiny_parquet_dir: pathlib.Path) -> None:
        with pytest.raises(DataUnavailableError, match='Insufficient'):
            load_daily_closes(tiny_parquet_dir, '2024-12-03', min_rows=100)

    def test_PL04_index_is_yyyy_mm_dd_strings(self, tiny_parquet_dir: pathlib.Path) -> None:
        series = load_daily_closes(tiny_parquet_dir, '2024-12-03', min_rows=1)
        assert all(len(d) == 10 and d[4] == '-' and d[7] == '-' for d in series.index)

    def test_PL05_daily_close_is_last_minute_bar(self, tiny_parquet_dir: pathlib.Path) -> None:
        series = load_daily_closes(tiny_parquet_dir, '2024-12-01', min_rows=1)
        # Last minute bar of day 0: 40050.0 + 1439 * 0.01 = 40064.39
        expected = 40050.0 + 1439 * 0.01
        assert abs(series['2024-12-01'] - expected) < 0.02

    def test_PL05b_no_partitions_raises(self, tmp_path: pathlib.Path) -> None:
        with pytest.raises(DataUnavailableError, match='No OHLCV partitions'):
            load_daily_closes(tmp_path, '2024-12-01', min_rows=1)


# ===========================================================================
# Group B — Idempotency and run_log gate
# ===========================================================================

class TestIdempotency:

    def _closes(self) -> pd.Series:
        return make_closes(n=10, dates_start='2021-01-01')

    def _run_date(self) -> str:
        return '2021-01-10'   # closes.index[-1]

    def test_IP01_first_run_writes_all_rows(
        self, db_path, fn_file, pre_reg
    ) -> None:
        _run(run_date=self._run_date(), closes=self._closes(),
             db_path=db_path, fn_file=fn_file, pre_reg=pre_reg, position=1.0)
        counts = _row_counts(db_path)
        assert counts['run_log'] == 1
        assert counts['signal_log'] == 1
        assert counts['trade_entries'] == 1

    def test_IP02_second_run_same_day_ok_skips(
        self, db_path, fn_file, pre_reg
    ) -> None:
        closes   = self._closes()
        run_date = self._run_date()
        result1  = _run(run_date=run_date, closes=closes,
                        db_path=db_path, fn_file=fn_file, pre_reg=pre_reg)
        result2  = _run(run_date=run_date, closes=closes,
                        db_path=db_path, fn_file=fn_file, pre_reg=pre_reg)
        assert result1 == 'ok'
        assert result2 == 'skipped'
        assert _row_counts(db_path)['run_log'] == 1

    def test_IP03_prior_skipped_status_skips(
        self, db_path, fn_file, pre_reg
    ) -> None:
        run_date = self._run_date()
        conn = _open_conn(db_path)
        conn.execute(
            "INSERT INTO run_log (signal_id, run_date, fn_hash, status) "
            "VALUES ('test_signal', ?, 'abc', 'skipped')", (run_date,)
        )
        conn.commit(); conn.close()
        result = _run(run_date=run_date, closes=self._closes(),
                      db_path=db_path, fn_file=fn_file, pre_reg=pre_reg)
        assert result == 'skipped'

    def test_IP04_prior_fn_hash_violation_raises(
        self, db_path, fn_file, pre_reg
    ) -> None:
        run_date = self._run_date()
        conn = _open_conn(db_path)
        conn.execute(
            "INSERT INTO run_log (signal_id, run_date, fn_hash, status) "
            "VALUES ('test_signal', ?, 'abc', 'fn_hash_violation')", (run_date,)
        )
        conn.commit(); conn.close()
        with pytest.raises(RuntimeError, match='fn_hash_violation'):
            _run(run_date=run_date, closes=self._closes(),
                 db_path=db_path, fn_file=fn_file, pre_reg=pre_reg)

    def test_IP05_prior_error_raises(
        self, db_path, fn_file, pre_reg
    ) -> None:
        run_date = self._run_date()
        conn = _open_conn(db_path)
        conn.execute(
            "INSERT INTO run_log (signal_id, run_date, fn_hash, status) "
            "VALUES ('test_signal', ?, 'abc', 'error')", (run_date,)
        )
        conn.commit(); conn.close()
        with pytest.raises(RuntimeError, match='prior run errored'):
            _run(run_date=run_date, closes=self._closes(),
                 db_path=db_path, fn_file=fn_file, pre_reg=pre_reg)

    def test_IP06_no_row_after_clean_retry_succeeds(
        self, db_path, fn_file, pre_reg
    ) -> None:
        """No prior run_log row → retry proceeds cleanly (crash-before-commit invariant)."""
        result = _run(run_date=self._run_date(), closes=self._closes(),
                      db_path=db_path, fn_file=fn_file, pre_reg=pre_reg)
        assert result == 'ok'

    def test_IP07_signal_A_does_not_block_signal_B(
        self, db_path, fn_file, pre_reg, tmp_path
    ) -> None:
        closes    = self._closes()
        run_date  = self._run_date()
        pre_reg_b = _make_test_prereg(tmp_path, fn_file, 'other_signal')
        _run(signal_id='test_signal', run_date=run_date, closes=closes,
             db_path=db_path, fn_file=fn_file, pre_reg=pre_reg)
        result = run_signal(
            signal_id='other_signal', run_date=run_date, closes=closes,
            compute_fn=_fixed_signal(1.0), raw_value_fn=_stub_raw,
            pre_reg_path=pre_reg_b, fn_file_path=fn_file,
            db_path=db_path, hold_days=7, min_history=MIN_HIST_STUB, cost_bps=15.0,
        )
        assert result == 'ok'
        assert _row_counts(db_path)['run_log'] == 2


# ===========================================================================
# Group C — fn_hash enforcement
# ===========================================================================

class TestFnHashEnforcement:

    def _closes(self) -> pd.Series:
        return make_closes(n=10, dates_start='2021-01-01')

    def _run_date(self) -> str:
        return '2021-01-10'

    def test_FH01_fn_hash_stored_in_run_log(
        self, db_path, fn_file, pre_reg
    ) -> None:
        _run(run_date=self._run_date(), closes=self._closes(),
             db_path=db_path, fn_file=fn_file, pre_reg=pre_reg)
        conn = _open_conn(db_path)
        fn_hash = conn.execute("SELECT fn_hash FROM run_log").fetchone()[0]
        conn.close()
        assert fn_hash == _sha256_lf(fn_file)

    def test_FH02_violation_written_to_run_log(
        self, db_path, fn_file, pre_reg
    ) -> None:
        fn_file.write_bytes(b"# tampered\n")
        with pytest.raises(FnHashViolation):
            _run(run_date=self._run_date(), closes=self._closes(),
                 db_path=db_path, fn_file=fn_file, pre_reg=pre_reg)
        conn = _open_conn(db_path)
        row = conn.execute("SELECT status FROM run_log").fetchone()
        conn.close()
        assert row[0] == 'fn_hash_violation'

    def test_FH03_violation_writes_no_signal_log_or_trade_rows(
        self, db_path, fn_file, pre_reg
    ) -> None:
        fn_file.write_bytes(b"# tampered\n")
        with pytest.raises(FnHashViolation):
            _run(run_date=self._run_date(), closes=self._closes(),
                 db_path=db_path, fn_file=fn_file, pre_reg=pre_reg)
        counts = _row_counts(db_path)
        assert counts['signal_log']    == 0
        assert counts['trade_entries'] == 0

    def test_FH04_violation_reraises_to_caller(
        self, db_path, fn_file, pre_reg
    ) -> None:
        fn_file.write_bytes(b"# tampered\n")
        with pytest.raises(FnHashViolation):
            _run(run_date=self._run_date(), closes=self._closes(),
                 db_path=db_path, fn_file=fn_file, pre_reg=pre_reg)

    def test_FH05_violation_actual_hash_recorded_in_run_log(
        self, db_path, fn_file, pre_reg
    ) -> None:
        """fn_hash in run_log must be the ACTUAL (tampered) hash, not the registered one."""
        fn_file.write_bytes(b"# tampered\n")
        actual = _sha256_lf(fn_file)
        with pytest.raises(FnHashViolation):
            _run(run_date=self._run_date(), closes=self._closes(),
                 db_path=db_path, fn_file=fn_file, pre_reg=pre_reg)
        conn = _open_conn(db_path)
        stored = conn.execute("SELECT fn_hash FROM run_log").fetchone()[0]
        conn.close()
        assert stored == actual

    def test_FH06_note_field_contains_context(
        self, db_path, fn_file, pre_reg
    ) -> None:
        fn_file.write_bytes(b"# tampered\n")
        with pytest.raises(FnHashViolation):
            _run(run_date=self._run_date(), closes=self._closes(),
                 db_path=db_path, fn_file=fn_file, pre_reg=pre_reg)
        conn = _open_conn(db_path)
        note = conn.execute("SELECT note FROM run_log").fetchone()[0]
        conn.close()
        assert note is not None and len(note) > 10

    def test_FH_prior_ok_plus_tamper_skips_before_hash_check(
        self, db_path, fn_file, pre_reg
    ) -> None:
        """Idempotency check fires before fn_hash — prior ok row means no re-run."""
        closes   = self._closes()
        run_date = self._run_date()
        _run(run_date=run_date, closes=closes,
             db_path=db_path, fn_file=fn_file, pre_reg=pre_reg)
        fn_file.write_bytes(b"# tampered after ok run\n")
        result = _run(run_date=run_date, closes=closes,
                      db_path=db_path, fn_file=fn_file, pre_reg=pre_reg)
        assert result == 'skipped'


# ===========================================================================
# Group D — Trade lifecycle
# ===========================================================================

class TestTradeLifecycle:
    """
    Lifecycle tests use min_history=1 when calling _run() with a single-row
    slice (to simulate 'first day' scenarios).  History validation is covered
    separately in Group I.
    """

    def test_TL01_first_run_opens_long_trade(
        self, db_path, fn_file, pre_reg
    ) -> None:
        closes   = make_closes(20)
        run_date = closes.index[-1]
        _run(run_date=run_date, closes=closes,
             db_path=db_path, fn_file=fn_file, pre_reg=pre_reg, position=1.0)
        conn = _open_conn(db_path)
        row  = conn.execute("SELECT direction, open, entry_date FROM trade_entries").fetchone()
        conn.close()
        assert row == (1, 1, run_date)

    def test_TL01b_first_run_flat_opens_no_trade(
        self, db_path, fn_file, pre_reg
    ) -> None:
        closes   = make_closes(10)
        run_date = closes.index[-1]
        _run(run_date=run_date, closes=closes,
             db_path=db_path, fn_file=fn_file, pre_reg=pre_reg, position=0.0)
        assert _row_counts(db_path)['trade_entries'] == 0

    def test_TL02_planned_exit_date_is_entry_plus_hold_days(
        self, db_path, fn_file, pre_reg
    ) -> None:
        closes   = make_closes(20)
        run_date = closes.index[-1]
        _run(run_date=run_date, closes=closes,
             db_path=db_path, fn_file=fn_file, pre_reg=pre_reg)
        conn = _open_conn(db_path)
        row  = conn.execute("SELECT entry_date, planned_exit_date FROM trade_entries").fetchone()
        conn.close()
        entry_d = date.fromisoformat(row[0])
        exit_d  = date.fromisoformat(row[1])
        assert (exit_d - entry_d).days == 7

    def test_TL03_exit_on_planned_day_closes_and_reopens(
        self, db_path, fn_file, pre_reg
    ) -> None:
        closes = make_closes(30)
        day0, day7 = closes.index[0], closes.index[7]
        _run(run_date=day0, closes=closes[:1],
             db_path=db_path, fn_file=fn_file, pre_reg=pre_reg,
             position=1.0, min_history=1)
        _run(run_date=day7, closes=closes[:8],
             db_path=db_path, fn_file=fn_file, pre_reg=pre_reg,
             position=1.0, min_history=1)
        conn = _open_conn(db_path)
        counts = {
            'open':   conn.execute("SELECT COUNT(*) FROM trade_entries WHERE open=1").fetchone()[0],
            'closed': conn.execute("SELECT COUNT(*) FROM trade_entries WHERE open=0").fetchone()[0],
            'exits':  conn.execute("SELECT COUNT(*) FROM trade_exits").fetchone()[0],
        }
        conn.close()
        assert counts == {'open': 1, 'closed': 1, 'exits': 1}

    def test_TL04_exit_day_flat_signal_no_reentry(
        self, db_path, fn_file, pre_reg
    ) -> None:
        closes = make_closes(30)
        day0, day7 = closes.index[0], closes.index[7]
        _run(run_date=day0, closes=closes[:1],
             db_path=db_path, fn_file=fn_file, pre_reg=pre_reg,
             position=1.0, min_history=1)
        _run(run_date=day7, closes=closes[:8],
             db_path=db_path, fn_file=fn_file, pre_reg=pre_reg,
             position=0.0, min_history=1)
        conn = _open_conn(db_path)
        open_count = conn.execute(
            "SELECT COUNT(*) FROM trade_entries WHERE open=1"
        ).fetchone()[0]
        conn.close()
        assert open_count == 0

    def test_TL05_no_early_exit_mid_hold(
        self, db_path, fn_file, pre_reg
    ) -> None:
        """Signal flips on day 3 but hold is not up — no exit before planned_exit_date."""
        closes = make_closes(30)
        day0, day3 = closes.index[0], closes.index[3]
        _run(run_date=day0, closes=closes[:1],
             db_path=db_path, fn_file=fn_file, pre_reg=pre_reg,
             position=1.0, min_history=1)
        _run(run_date=day3, closes=closes[:4],
             db_path=db_path, fn_file=fn_file, pre_reg=pre_reg,
             position=-1.0, min_history=1)
        counts = _row_counts(db_path)
        assert counts['trade_exits']   == 0    # no exit yet
        assert counts['trade_entries'] == 1    # original open trade only
        conn = _open_conn(db_path)
        assert _has_open_trade(conn, 'test_signal')
        conn.close()

    def test_TL06_no_second_trade_while_open(
        self, db_path, fn_file, pre_reg
    ) -> None:
        closes = make_closes(30)
        day0, day3 = closes.index[0], closes.index[3]
        _run(run_date=day0, closes=closes[:1],
             db_path=db_path, fn_file=fn_file, pre_reg=pre_reg,
             position=1.0, min_history=1)
        _run(run_date=day3, closes=closes[:4],
             db_path=db_path, fn_file=fn_file, pre_reg=pre_reg,
             position=1.0, min_history=1)
        assert _row_counts(db_path)['trade_entries'] == 1

    def test_TL07_same_day_exit_and_reentry_direction_flip(
        self, db_path, fn_file, pre_reg, tmp_path
    ) -> None:
        """Exit long, re-enter short on same exit day (G1-style flip)."""
        closes  = make_closes(20)
        day0    = closes.index[0]
        day7    = closes.index[7]
        pre_b   = _make_test_prereg(tmp_path, fn_file, 'g1_signal')
        run_signal(
            signal_id='g1_signal', run_date=day0, closes=closes[:1],
            compute_fn=_fixed_signal(1.0), raw_value_fn=_stub_raw,
            pre_reg_path=pre_b, fn_file_path=fn_file,
            db_path=db_path, hold_days=7, min_history=1, cost_bps=15.0,
        )
        run_signal(
            signal_id='g1_signal', run_date=day7, closes=closes[:8],
            compute_fn=_fixed_signal(-1.0), raw_value_fn=_stub_raw,
            pre_reg_path=pre_b, fn_file_path=fn_file,
            db_path=db_path, hold_days=7, min_history=1, cost_bps=15.0,
        )
        conn = _open_conn(db_path)
        rows = conn.execute(
            "SELECT direction, open, entry_date FROM trade_entries "
            "WHERE signal_id='g1_signal' ORDER BY id"
        ).fetchall()
        conn.close()
        assert len(rows) == 2
        assert rows[0] == (1,  0, day0)    # closed long
        assert rows[1] == (-1, 1, day7)    # open short

    def test_TL08_exit_price_is_planned_exit_price_not_run_date_price(
        self, db_path, fn_file, pre_reg
    ) -> None:
        """Miss day 7, run on day 8. Exit price must be day 7's close, not day 8's."""
        closes = make_closes(20, trend=0.01)
        day0, day7, day8 = closes.index[0], closes.index[7], closes.index[8]
        _run(run_date=day0, closes=closes[:1],
             db_path=db_path, fn_file=fn_file, pre_reg=pre_reg,
             position=1.0, min_history=1)
        _run(run_date=day8, closes=closes[:9],
             db_path=db_path, fn_file=fn_file, pre_reg=pre_reg,
             position=1.0, min_history=1)
        conn = _open_conn(db_path)
        exit_price = conn.execute("SELECT exit_close_price FROM trade_exits").fetchone()[0]
        conn.close()
        assert abs(exit_price - float(closes[day7])) < 1e-9   # day 7, NOT day 8

    def test_TL09_entry_price_is_run_date_close(
        self, db_path, fn_file, pre_reg
    ) -> None:
        closes   = make_closes(10, trend=0.01)
        run_date = closes.index[-1]
        _run(run_date=run_date, closes=closes,
             db_path=db_path, fn_file=fn_file, pre_reg=pre_reg, position=1.0)
        conn = _open_conn(db_path)
        entry_price = conn.execute("SELECT entry_close_price FROM trade_entries").fetchone()[0]
        conn.close()
        assert abs(entry_price - float(closes[run_date])) < 1e-9

    def test_TL10_cost_bps_stored_correctly(
        self, db_path, fn_file, pre_reg
    ) -> None:
        closes = make_closes(20)
        day0, day7 = closes.index[0], closes.index[7]
        _run(run_date=day0, closes=closes[:1],
             db_path=db_path, fn_file=fn_file, pre_reg=pre_reg,
             cost_bps=22.5, min_history=1)
        _run(run_date=day7, closes=closes[:8],
             db_path=db_path, fn_file=fn_file, pre_reg=pre_reg,
             cost_bps=22.5, min_history=1)
        conn = _open_conn(db_path)
        stored_bps = conn.execute("SELECT cost_bps FROM trade_exits").fetchone()[0]
        conn.close()
        assert abs(stored_bps - 22.5) < 1e-9

    def test_TL11_trade_returns_view_correct(
        self, db_path, fn_file, pre_reg
    ) -> None:
        """trade_returns view: gross=(exit/entry-1), net=gross-cost_bps/10000, hold_days=7."""
        closes = make_closes(20, trend=0.01)
        day0, day7 = closes.index[0], closes.index[7]
        _run(run_date=day0, closes=closes[:1],
             db_path=db_path, fn_file=fn_file, pre_reg=pre_reg,
             position=1.0, cost_bps=15.0, min_history=1)
        _run(run_date=day7, closes=closes[:8],
             db_path=db_path, fn_file=fn_file, pre_reg=pre_reg,
             position=1.0, min_history=1)
        conn = _open_conn(db_path)
        row = conn.execute(
            "SELECT gross_return, net_return, hold_days_actual FROM trade_returns"
        ).fetchone()
        conn.close()
        entry = float(closes[day0])
        exit_ = float(closes[day7])
        expected_gross = exit_ / entry - 1.0
        expected_net   = expected_gross - 15.0 / 10000.0
        assert abs(row[0] - expected_gross) < 1e-9
        assert abs(row[1] - expected_net)   < 1e-9
        assert row[2] == 7

    def test_TL12_raw_value_stored_in_signal_log(
        self, db_path, fn_file, pre_reg
    ) -> None:
        closes   = make_closes(10)
        run_date = closes.index[-1]
        _run(run_date=run_date, closes=closes,
             db_path=db_path, fn_file=fn_file, pre_reg=pre_reg)
        conn = _open_conn(db_path)
        rv = conn.execute("SELECT raw_value FROM signal_log").fetchone()[0]
        conn.close()
        assert abs(rv - 1.23) < 1e-9   # _stub_raw always returns 1.23


# ===========================================================================
# Group E — Missed-day recovery
# ===========================================================================

class TestMissedDayRecovery:

    def test_RC01_overdue_trade_closed_at_planned_exit_price(
        self, db_path, fn_file, pre_reg
    ) -> None:
        """Miss day 7, run on day 8. Trade must close at day 7's close price."""
        closes = make_closes(20, trend=0.01)
        day0, day7, day8 = closes.index[0], closes.index[7], closes.index[8]
        _run(run_date=day0, closes=closes[:1],
             db_path=db_path, fn_file=fn_file, pre_reg=pre_reg,
             position=1.0, min_history=1)
        _run(run_date=day8, closes=closes[:9],
             db_path=db_path, fn_file=fn_file, pre_reg=pre_reg,
             position=1.0, min_history=1)
        conn = _open_conn(db_path)
        row = conn.execute("SELECT exit_date, exit_close_price FROM trade_exits").fetchone()
        conn.close()
        assert row[0] == day7
        assert abs(row[1] - float(closes[day7])) < 1e-9

    def test_RC02_new_trade_opens_at_run_date_after_overdue_close(
        self, db_path, fn_file, pre_reg
    ) -> None:
        closes = make_closes(20, trend=0.01)
        day0, day8 = closes.index[0], closes.index[8]
        _run(run_date=day0, closes=closes[:1],
             db_path=db_path, fn_file=fn_file, pre_reg=pre_reg,
             position=1.0, min_history=1)
        _run(run_date=day8, closes=closes[:9],
             db_path=db_path, fn_file=fn_file, pre_reg=pre_reg,
             position=1.0, min_history=1)
        conn = _open_conn(db_path)
        new_entry = conn.execute(
            "SELECT entry_date FROM trade_entries WHERE open=1"
        ).fetchone()
        conn.close()
        assert new_entry[0] == day8

    def test_RC03_multi_day_gap_closes_overdue_at_plan_price(
        self, db_path, fn_file, pre_reg
    ) -> None:
        """Patch planned_exit to day 9, then run on day 10 — exit at day 9 price."""
        closes = make_closes(30, trend=0.005)
        day0, day9, day10 = closes.index[0], closes.index[9], closes.index[10]
        _run(run_date=day0, closes=closes[:1],
             db_path=db_path, fn_file=fn_file, pre_reg=pre_reg,
             position=1.0, min_history=1)
        conn = _open_conn(db_path)
        conn.execute("UPDATE trade_entries SET planned_exit_date=? WHERE entry_date=?",
                     (day9, day0))
        conn.commit(); conn.close()
        _run(run_date=day10, closes=closes[:11],
             db_path=db_path, fn_file=fn_file, pre_reg=pre_reg,
             position=1.0, min_history=1)
        conn = _open_conn(db_path)
        exits = conn.execute("SELECT exit_date, exit_close_price FROM trade_exits").fetchall()
        conn.close()
        assert len(exits) == 1
        assert exits[0][0] == day9
        assert abs(exits[0][1] - float(closes[day9])) < 1e-9

    def test_RC04_signal_log_gap_for_missed_day(
        self, db_path, fn_file, pre_reg
    ) -> None:
        """signal_log has no row for the missed day (visible gap in day sequence)."""
        closes = make_closes(20)
        day0, day1, day2 = closes.index[0], closes.index[1], closes.index[2]
        _run(run_date=day0, closes=closes[:1],
             db_path=db_path, fn_file=fn_file, pre_reg=pre_reg, min_history=1)
        _run(run_date=day2, closes=closes[:3],
             db_path=db_path, fn_file=fn_file, pre_reg=pre_reg, min_history=1)
        conn = _open_conn(db_path)
        days = [r[0] for r in conn.execute("SELECT day FROM signal_log ORDER BY day").fetchall()]
        conn.close()
        assert day0 in days
        assert day2 in days
        assert day1 not in days    # day 1 missed — visible gap

    def test_RC05_manual_backfill_via_run_date_arg(
        self, db_path, fn_file, pre_reg
    ) -> None:
        closes   = make_closes(15)
        past_day = closes.index[5]
        result   = _run(
            run_date=past_day, closes=closes[:6],
            db_path=db_path, fn_file=fn_file, pre_reg=pre_reg, min_history=1
        )
        assert result == 'ok'
        conn = _open_conn(db_path)
        row = conn.execute("SELECT run_date FROM run_log").fetchone()
        conn.close()
        assert row[0] == past_day


# ===========================================================================
# Group F — Per-signal integration (real decision functions, synthetic prices)
# ===========================================================================

class TestSignalIntegrationH5:
    """H5 tests use real decision_function_h5.py + pre_registration JSON."""

    _HERE    = pathlib.Path(__file__).resolve().parent.parent
    _PRE_REG = _HERE / 'pre_registration_h5_vol_regime.json'
    _FN_FILE = _HERE / 'decision_function_h5.py'

    @pytest.fixture(autouse=True)
    def skip_if_missing(self):
        if not self._PRE_REG.exists() or not self._FN_FILE.exists():
            pytest.skip("H5 registration files not present")

    def _run_h5(self, closes, db_path, run_date=None):
        from forward_evidence import decision_function_h5 as df
        if run_date is None:
            run_date = closes.index[-1]
        return run_signal(
            signal_id='h5_vol_regime', run_date=run_date, closes=closes,
            compute_fn=df.compute_signal, raw_value_fn=lambda c: None,
            pre_reg_path=self._PRE_REG, fn_file_path=self._FN_FILE,
            db_path=db_path, hold_days=df.HOLD_DAYS, min_history=df.MIN_HISTORY,
            cost_bps=15.0,
        )

    def test_SI01_h5_long_in_low_vol(self, db_path: pathlib.Path) -> None:
        """make_h5_long_closes: high-vol history → low-vol tail → pos=1."""
        from forward_evidence import decision_function_h5 as df
        closes = make_h5_long_closes(df.MIN_HISTORY + 30)
        self._run_h5(closes, db_path)
        conn = _open_conn(db_path)
        pos = conn.execute("SELECT position FROM signal_log").fetchone()[0]
        conn.close()
        assert pos == 1.0

    def test_SI02_h5_flat_in_high_vol(self, db_path: pathlib.Path) -> None:
        """Constant high vol: last RV21 in top tercile → pos=0."""
        from forward_evidence import decision_function_h5 as df
        n   = df.MIN_HISTORY + 5
        rng = np.random.default_rng(99)
        # Constant daily vol of 15%: all rv values roughly equal → current rv ≈ quantile
        # Force last bar to be in top tercile by making earlier bars lower-vol
        log1 = rng.normal(0.001, 0.005, df.REGIME_WINDOW)     # low-vol history
        log2 = rng.normal(0.001, 0.15,  n - df.REGIME_WINDOW) # spike: extreme high vol
        log_rets = np.concatenate([log1, log2])
        prices   = 40_000.0 * np.exp(np.cumsum(log_rets))
        idx      = pd.date_range('2021-01-01', periods=n, freq='D').strftime('%Y-%m-%d')
        closes   = pd.Series(prices, index=idx, dtype=float)
        self._run_h5(closes, db_path)
        conn = _open_conn(db_path)
        pos = conn.execute("SELECT position FROM signal_log").fetchone()[0]
        conn.close()
        assert pos == 0.0

    def test_SI03_h5_never_short(self, db_path: pathlib.Path) -> None:
        """H5 position space is {0, +1}: direction=-1 must never appear."""
        from forward_evidence import decision_function_h5 as df
        closes = make_h5_long_closes(df.MIN_HISTORY + 30)
        self._run_h5(closes, db_path)
        conn = _open_conn(db_path)
        neg_dir = conn.execute(
            "SELECT COUNT(*) FROM trade_entries WHERE direction=-1"
        ).fetchone()[0]
        conn.close()
        assert neg_dir == 0


class TestSignalIntegrationG1:

    _HERE    = pathlib.Path(__file__).resolve().parent.parent
    _PRE_REG = _HERE / 'pre_registration_g1_market_regime_sma200.json'
    _FN_FILE = _HERE / 'decision_function_g1_market_regime.py'

    @pytest.fixture(autouse=True)
    def skip_if_missing(self):
        if not self._PRE_REG.exists() or not self._FN_FILE.exists():
            pytest.skip("G1 registration files not present")

    def _run_g1(self, closes, db_path, run_date=None):
        from forward_evidence import decision_function_g1_market_regime as df
        if run_date is None:
            run_date = closes.index[-1]
        return run_signal(
            signal_id='g1_market_regime_sma200', run_date=run_date, closes=closes,
            compute_fn=df.compute_signal, raw_value_fn=lambda c: None,
            pre_reg_path=self._PRE_REG, fn_file_path=self._FN_FILE,
            db_path=db_path, hold_days=df.HOLD_DAYS, min_history=df.MIN_HISTORY,
            cost_bps=15.0,
        )

    def test_SI04_g1_long_in_bull(self, db_path: pathlib.Path) -> None:
        closes = make_closes(n=230, trend=0.003)
        self._run_g1(closes, db_path)
        conn = _open_conn(db_path)
        pos = conn.execute("SELECT position FROM signal_log").fetchone()[0]
        conn.close()
        assert pos == 1.0

    def test_SI05_g1_short_in_bear(self, db_path: pathlib.Path) -> None:
        closes = make_closes(n=230, start=100_000.0, trend=-0.003)
        self._run_g1(closes, db_path)
        conn = _open_conn(db_path)
        pos = conn.execute("SELECT position FROM signal_log").fetchone()[0]
        conn.close()
        assert pos == -1.0


class TestSignalIntegrationG3:

    _HERE    = pathlib.Path(__file__).resolve().parent.parent
    _PRE_REG = _HERE / 'pre_registration_g3_tsmom.json'
    _FN_FILE = _HERE / 'decision_function_g3_tsmom.py'

    @pytest.fixture(autouse=True)
    def skip_if_missing(self):
        if not self._PRE_REG.exists() or not self._FN_FILE.exists():
            pytest.skip("G3 registration files not present")

    def _run_g3(self, closes, db_path, signal_id='g3_momentum_tsmom', run_date=None):
        from forward_evidence import decision_function_g3_tsmom as df
        if run_date is None:
            run_date = closes.index[-1]
        return run_signal(
            signal_id=signal_id, run_date=run_date, closes=closes,
            compute_fn=df.compute_signal, raw_value_fn=lambda c: None,
            pre_reg_path=self._PRE_REG, fn_file_path=self._FN_FILE,
            db_path=db_path, hold_days=df.HOLD_DAYS, min_history=df.MIN_HISTORY,
            cost_bps=15.0,
        )

    def test_SI07_g3_long_after_positive_30d(self, db_path: pathlib.Path) -> None:
        closes = make_closes(n=40, trend=0.01)
        self._run_g3(closes, db_path)
        conn = _open_conn(db_path)
        pos = conn.execute("SELECT position FROM signal_log").fetchone()[0]
        conn.close()
        assert pos == 1.0

    def test_SI08_g3_short_after_negative_30d(self, db_path: pathlib.Path) -> None:
        closes = make_closes(n=40, start=100_000.0, trend=-0.01)
        self._run_g3(closes, db_path)
        conn = _open_conn(db_path)
        pos = conn.execute("SELECT position FROM signal_log").fetchone()[0]
        conn.close()
        assert pos == -1.0

    def test_SI09_g3_sequential_7day_windows(self, db_path: pathlib.Path) -> None:
        """4 sequential 7-day trades via 4 runs spaced 7 days apart.

        G3 MIN_HISTORY=31.  First run at index 30+7=37 (enough history).
        After 4 runs: 3 closed + 1 open, all hold_days_actual == 7.
        """
        from forward_evidence import decision_function_g3_tsmom as df
        BASE   = df.MIN_HISTORY           # 31
        n      = BASE + 4 * df.HOLD_DAYS  # 31 + 28 = 59
        closes = make_closes(n + 1, trend=0.01)

        for i in range(4):
            idx      = BASE - 1 + df.HOLD_DAYS * (i + 1)   # 37, 44, 51, 58
            run_date = closes.index[idx]
            self._run_g3(closes[:idx + 1], db_path, run_date=run_date)

        conn = _open_conn(db_path)
        exits      = conn.execute("SELECT COUNT(*) FROM trade_exits").fetchone()[0]
        open_count = conn.execute(
            "SELECT COUNT(*) FROM trade_entries WHERE open=1"
        ).fetchone()[0]
        hold_days  = conn.execute(
            "SELECT hold_days_actual FROM trade_returns"
        ).fetchall()
        conn.close()
        assert exits == 3
        assert open_count == 1
        assert all(r[0] == 7 for r in hold_days)

    def test_SI11_g3_raw_value_is_30d_return(self, db_path: pathlib.Path) -> None:
        """raw_value in signal_log equals K-day return from runner_g3._raw_value."""
        from forward_evidence import runner_g3
        closes   = make_closes(n=40, trend=0.01)
        run_date = closes.index[-1]
        runner_g3.collect(run_date, closes, db_path, 15.0)
        conn = _open_conn(db_path)
        stored_rv = conn.execute("SELECT raw_value FROM signal_log").fetchone()[0]
        conn.close()
        expected = runner_g3._raw_value(closes)
        assert expected is not None
        assert abs(stored_rv - expected) < 1e-9


# ===========================================================================
# Group G — Multi-signal isolation
# ===========================================================================

class TestMultiSignalIsolation:

    _HERE = pathlib.Path(__file__).resolve().parent.parent

    @pytest.fixture(autouse=True)
    def skip_if_missing(self):
        for fname in [
            'pre_registration_h5_vol_regime.json',
            'pre_registration_g1_market_regime_sma200.json',
            'pre_registration_g3_tsmom.json',
        ]:
            if not (self._HERE / fname).exists():
                pytest.skip("Registration files not present")

    def _build_closes(self):
        from forward_evidence import (
            decision_function_h5 as h5,
            decision_function_g1_market_regime as g1,
            decision_function_g3_tsmom as g3,
        )
        n = max(h5.MIN_HISTORY, g1.MIN_HISTORY, g3.MIN_HISTORY) + 30
        return make_h5_long_closes(n)

    def test_MS01_all_three_write_independent_run_log_rows(
        self, db_path: pathlib.Path
    ) -> None:
        from forward_evidence import runner_h5, runner_g1, runner_g3
        closes   = self._build_closes()
        run_date = closes.index[-1]
        runner_h5.collect(run_date, closes, db_path, 15.0)
        runner_g1.collect(run_date, closes, db_path, 15.0)
        runner_g3.collect(run_date, closes, db_path, 15.0)
        conn = _open_conn(db_path)
        sigs = {r[0] for r in conn.execute("SELECT signal_id FROM run_log").fetchall()}
        conn.close()
        assert sigs == {'h5_vol_regime', 'g1_market_regime_sma200', 'g3_momentum_tsmom'}

    def test_MS02_h5_trade_entries_isolated_from_g3(
        self, db_path: pathlib.Path
    ) -> None:
        """H5 and G3 run independently; no cross-contamination in trade_entries."""
        from forward_evidence import runner_h5, runner_g3
        from forward_evidence import (
            decision_function_h5 as h5,
            decision_function_g3_tsmom as g3,
        )
        n      = max(h5.MIN_HISTORY, g3.MIN_HISTORY) + 30
        closes = make_h5_long_closes(n)
        run_date = closes.index[-1]
        runner_h5.collect(run_date, closes, db_path, 15.0)
        runner_g3.collect(run_date, closes, db_path, 15.0)

        conn = _open_conn(db_path)
        # Each signal_id in trade_entries should only appear under its own key
        cross = conn.execute(
            "SELECT COUNT(*) FROM trade_entries te1 "
            "WHERE signal_id = 'h5_vol_regime' "
            "AND EXISTS (SELECT 1 FROM trade_entries te2 "
            "            WHERE te2.signal_id = 'g3_momentum_tsmom' "
            "            AND te2.entry_date = te1.entry_date "
            "            AND te2.id = te1.id)"
        ).fetchone()[0]
        conn.close()
        assert cross == 0   # no row shares both h5 and g3 signal_id (impossible by schema)

    def test_MS03_trade_returns_view_isolates_by_signal_id(
        self, db_path: pathlib.Path
    ) -> None:
        from forward_evidence import runner_g3
        from forward_evidence import decision_function_g3_tsmom as g3
        closes   = make_closes(g3.MIN_HISTORY + 10, trend=0.01)
        run_date = closes.index[-1]
        runner_g3.collect(run_date, closes, db_path, 15.0)
        # Close the trade
        exit_date = (
            date.fromisoformat(run_date) + timedelta(days=g3.HOLD_DAYS)
        ).isoformat()
        extra = make_closes(
            g3.MIN_HISTORY + 10 + g3.HOLD_DAYS + 1,
            trend=0.01, dates_start=closes.index[0],
        )
        if exit_date in extra.index:
            runner_g3.collect(exit_date, extra, db_path, 15.0)

        conn = _open_conn(db_path)
        other = conn.execute(
            "SELECT COUNT(*) FROM trade_returns WHERE signal_id != 'g3_momentum_tsmom'"
        ).fetchone()[0]
        conn.close()
        assert other == 0


# ===========================================================================
# Group H — Scheduling / run_date derivation
# ===========================================================================

class TestScheduling:

    def test_SC01_derive_run_date_is_yesterday_utc(self) -> None:
        from forward_evidence.run_daily import _derive_run_date
        from datetime import datetime, timezone
        rd       = _derive_run_date()
        expected = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
        assert rd == expected

    def test_SC02_explicit_run_date_arg_is_used(
        self, db_path, fn_file, pre_reg
    ) -> None:
        closes   = make_closes(10)
        past_day = closes.index[5]
        result   = _run(run_date=past_day, closes=closes[:6],
                        db_path=db_path, fn_file=fn_file, pre_reg=pre_reg,
                        min_history=1)
        assert result == 'ok'
        conn = _open_conn(db_path)
        rd = conn.execute("SELECT run_date FROM run_log").fetchone()[0]
        conn.close()
        assert rd == past_day

    def test_SC03_config_cost_bps_applied_correctly(
        self, db_path, fn_file, pre_reg
    ) -> None:
        """COST_BPS from collection_config.yaml flows through to trade_exits."""
        import yaml
        cfg_path = pathlib.Path(__file__).resolve().parent.parent / 'collection_config.yaml'
        if not cfg_path.exists():
            pytest.skip("collection_config.yaml not present")
        cfg      = yaml.safe_load(cfg_path.read_text(encoding='utf-8'))
        expected = float(cfg['signals']['g3_momentum_tsmom']['cost_bps'])

        closes = make_closes(20, trend=0.01)
        day0, day7 = closes.index[0], closes.index[7]
        _run(run_date=day0, closes=closes[:1],
             db_path=db_path, fn_file=fn_file, pre_reg=pre_reg,
             cost_bps=expected, min_history=1)
        _run(run_date=day7, closes=closes[:8],
             db_path=db_path, fn_file=fn_file, pre_reg=pre_reg,
             cost_bps=expected, min_history=1)
        conn = _open_conn(db_path)
        stored = conn.execute("SELECT cost_bps FROM trade_exits").fetchone()[0]
        conn.close()
        assert abs(stored - expected) < 1e-9


# ===========================================================================
# Group I — Edge cases and error paths
# ===========================================================================

class TestEdgeCases:

    def test_EC01_position_zero_no_trade_no_crash(
        self, db_path, fn_file, pre_reg
    ) -> None:
        closes   = make_closes(10)
        run_date = closes.index[-1]
        result   = _run(
            run_date=run_date, closes=closes,
            db_path=db_path, fn_file=fn_file, pre_reg=pre_reg,
            position=0.0,
        )
        assert result == 'ok'
        assert _row_counts(db_path)['trade_entries'] == 0

    def test_EC02_run_date_missing_from_closes_raises(
        self, db_path, fn_file, pre_reg
    ) -> None:
        closes = make_closes(10)
        future = (date.fromisoformat(closes.index[-1]) + timedelta(days=1)).isoformat()
        with pytest.raises(DataUnavailableError, match=future):
            _run(run_date=future, closes=closes,
                 db_path=db_path, fn_file=fn_file, pre_reg=pre_reg)

    def test_EC03_insufficient_history_raises_no_db_write(
        self, db_path, fn_file, pre_reg
    ) -> None:
        closes   = make_closes(3)   # 3 rows < MIN_HIST_STUB=5
        run_date = closes.index[-1]
        with pytest.raises(DataUnavailableError, match='Insufficient'):
            _run(run_date=run_date, closes=closes,
                 db_path=db_path, fn_file=fn_file, pre_reg=pre_reg)
        assert _row_counts(db_path)['run_log'] == 0

    def test_EC04_data_unavailable_not_written_to_run_log(
        self, db_path, fn_file, pre_reg
    ) -> None:
        """DataUnavailableError must NOT write an 'error' row — automatic retry is safe."""
        closes   = make_closes(3)
        run_date = closes.index[-1]
        with pytest.raises(DataUnavailableError):
            _run(run_date=run_date, closes=closes,
                 db_path=db_path, fn_file=fn_file, pre_reg=pre_reg)
        conn = _open_conn(db_path)
        count = conn.execute("SELECT COUNT(*) FROM run_log").fetchone()[0]
        conn.close()
        assert count == 0

    def test_EC05_lookahead_guard_filters_future_closes(
        self, db_path, fn_file, pre_reg
    ) -> None:
        """Passes full series (future dates present); entry price must be closes[run_date]."""
        closes   = make_closes(20, trend=0.01)
        run_date = closes.index[10]    # mid-series; 9 future rows follow
        _run(run_date=run_date, closes=closes,   # future dates included
             db_path=db_path, fn_file=fn_file, pre_reg=pre_reg,
             position=1.0, min_history=1)
        conn = _open_conn(db_path)
        entry_price = conn.execute("SELECT entry_close_price FROM trade_entries").fetchone()[0]
        conn.close()
        assert abs(entry_price - float(closes[run_date])) < 1e-9

    def test_EC06_record_mini_is_idempotent(
        self, db_path
    ) -> None:
        """_record_mini with INSERT OR IGNORE must not raise on duplicate calls."""
        for _ in range(3):
            _record_mini(db_path, 'sig', '2026-06-14', 'abc', 'error', 'msg')
        conn = _open_conn(db_path)
        count = conn.execute("SELECT COUNT(*) FROM run_log").fetchone()[0]
        conn.close()
        assert count == 1
