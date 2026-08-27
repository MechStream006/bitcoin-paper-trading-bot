"""
Regression tests for Gate 6B.1 — framework integrity hardening.

Two defects, both found by executable probe during the Gate 6A read-only audit
and both previously invisible to a fully green suite (253 passed):

  B-01  load_daily_closes / load_daily_ohlcv accepted a TRUNCATED final UTC day.
        resample('D') built a "daily" row from a partial day, so an ingest that
        died mid-day produced an intraday price labelled as the daily close, and
        (via agg max/min) an understated daily high/low. Measured on synthetic
        data: a day truncated at minute 60 reported a close 5.5% away from the
        true one, with no error and no warning.

  B-02  run_signal() accepted a present-but-NaN close AT run_date. Decision
        functions map NaN to 0.0 by convention, so a missing price was persisted
        as a flat DECISION rather than skipped as missing DATA. run_ohlcv_signal
        and the aux_series branch already guarded this; the primary closes path
        was protected only incidentally, by load_daily_closes calling .dropna().

Run from research/:
    pytest forward_evidence/tests/test_framework_integrity.py -v
"""
from __future__ import annotations

import glob
import json
import pathlib
import sqlite3

import numpy as np
import pandas as pd
import pytest

from forward_evidence.init_forward_db import init_forward_db
from forward_evidence.pre_reg_guard import _sha256_lf
from forward_evidence.runner_base import (
    MIN_DAILY_BARS,
    DataUnavailableError,
    load_daily_closes,
    load_daily_ohlcv,
    run_signal,
)

_RESEARCH_ROOT = pathlib.Path(__file__).resolve().parents[2]


# ===========================================================================
# Helpers
# ===========================================================================

def write_1m_store(
    root: pathlib.Path,
    start: str = '2026-01-01',
    end: str = '2026-03-05',
    final_day_bars: int | None = None,
    short_day: tuple[str, int] | None = None,
) -> pathlib.Path:
    """
    Build a partitioned 1m OHLCV store.

    final_day_bars : truncate the LAST day to this many bars (None = complete).
    short_day      : (date, n_bars) — truncate an INTERIOR day, used to prove the
                     guard is scoped to the final bucket only.

    Within each day the price ramps monotonically, so a mid-day bar is always
    distinguishable from the true daily close.
    """
    frames = []
    for day in pd.date_range(start, end, freq='D', tz='UTC'):
        n = 1440
        if final_day_bars is not None and day == pd.Timestamp(end, tz='UTC'):
            n = final_day_bars
        if short_day is not None and day == pd.Timestamp(short_day[0], tz='UTC'):
            n = short_day[1]
        ts = day + pd.to_timedelta(np.arange(n), unit='m')
        px = 100.0 + day.dayofyear + np.arange(n) / 1440.0 * 10.0
        frames.append(pd.DataFrame({
            'open_time': ts.astype('int64') // 10 ** 6,
            'open': px, 'high': px, 'low': px, 'close': px, 'volume': 1.0,
        }))
    df = pd.concat(frames, ignore_index=True)
    dt = pd.to_datetime(df['open_time'], unit='ms', utc=True)
    for (y, m), g in df.groupby([dt.dt.year, dt.dt.month]):
        p = root / f'year={y}' / f'month={m:02d}'
        p.mkdir(parents=True, exist_ok=True)
        g.to_parquet(p / 'data.parquet', index=False)
    return root


def make_prereg(tmp_path: pathlib.Path, fn_file: pathlib.Path, signal_id: str) -> pathlib.Path:
    p = tmp_path / f'prereg_{signal_id}.json'
    p.write_text(json.dumps({
        'schema_version': 1,
        'version': '1.0.0',
        'signal_id': signal_id,
        'pre_registration_date': '2026-06-14T00:00:00Z',
        'function_spec': {'decision_function_hash': _sha256_lf(fn_file)},
    }), encoding='utf-8')
    return p


def nan_to_flat(closes: pd.Series) -> pd.Series:
    """
    The NaN convention every real decision function uses, e.g.
    decision_function_g2_funding_level.compute_signal:
        pos[f.isna() | lo.isna() | hi.isna()] = 0.0
    This is what turns missing DATA into a flat DECISION absent a runner guard.
    """
    pos = pd.Series(1.0, index=closes.index)
    pos[closes.isna()] = 0.0
    return pos


@pytest.fixture()
def signal_env(tmp_path: pathlib.Path):
    fn = tmp_path / 'decision_function_probe.py'
    fn.write_bytes(b"def compute_signal(close):\n    return close * 0.0\n")
    db = tmp_path / 'evidence_forward.db'
    init_forward_db(db)
    return fn, make_prereg(tmp_path, fn, 'probe_signal'), db


# ===========================================================================
# Group B01 — incomplete final UTC day
# ===========================================================================

class TestB01IncompleteFinalDay:

    def test_B01_01_complete_final_day_is_accepted_by_closes(self, tmp_path):
        store = write_1m_store(tmp_path / 'a')
        s = load_daily_closes(store, '2026-03-05', min_rows=30)
        assert s.index[-1] == '2026-03-05'

    def test_B01_02_truncated_final_day_is_rejected_by_closes(self, tmp_path):
        store = write_1m_store(tmp_path / 'b', final_day_bars=60)
        with pytest.raises(DataUnavailableError, match='Incomplete final day'):
            load_daily_closes(store, '2026-03-05', min_rows=30)

    def test_B01_03_complete_final_day_is_accepted_by_ohlcv(self, tmp_path):
        store = write_1m_store(tmp_path / 'c')
        d = load_daily_ohlcv(store, '2026-03-05', min_rows=30)
        assert d.index[-1] == '2026-03-05'

    def test_B01_04_truncated_final_day_is_rejected_by_ohlcv(self, tmp_path):
        store = write_1m_store(tmp_path / 'd', final_day_bars=60)
        with pytest.raises(DataUnavailableError, match='Incomplete final day'):
            load_daily_ohlcv(store, '2026-03-05', min_rows=30)

    # -- boundary ---------------------------------------------------------

    def test_B01_05_boundary_exactly_1440_bars_passes(self, tmp_path):
        store = write_1m_store(tmp_path / 'e', final_day_bars=MIN_DAILY_BARS)
        s = load_daily_closes(store, '2026-03-05', min_rows=30)
        assert s.index[-1] == '2026-03-05'
        d = load_daily_ohlcv(store, '2026-03-05', min_rows=30)
        assert d.index[-1] == '2026-03-05'

    def test_B01_06_boundary_one_bar_short_fails(self, tmp_path):
        store = write_1m_store(tmp_path / 'f', final_day_bars=MIN_DAILY_BARS - 1)
        with pytest.raises(DataUnavailableError, match=r'has 1439 1m bars, need 1440'):
            load_daily_closes(store, '2026-03-05', min_rows=30)
        with pytest.raises(DataUnavailableError, match=r'has 1439 1m bars, need 1440'):
            load_daily_ohlcv(store, '2026-03-05', min_rows=30)

    def test_B01_07_message_names_the_actual_bar_count(self, tmp_path):
        store = write_1m_store(tmp_path / 'g', final_day_bars=7)
        with pytest.raises(DataUnavailableError) as exc:
            load_daily_closes(store, '2026-03-05', min_rows=30)
        assert 'has 7 1m bars' in str(exc.value)

    # -- scope: final bucket only ----------------------------------------

    def test_B01_08_interior_short_day_is_preserved_not_dropped(self, tmp_path):
        """
        The guard is deliberately scoped to the final bucket. Dropping a
        historically short day would change the history that already-registered
        signals were computed against — a research-integrity change, not a
        data-quality fix. The real corpus contains 7 such days.
        """
        store = write_1m_store(tmp_path / 'h', short_day=('2026-02-10', 1200))
        s = load_daily_closes(store, '2026-03-05', min_rows=30)
        assert '2026-02-10' in s.index, 'interior short day must survive'
        d = load_daily_ohlcv(store, '2026-03-05', min_rows=30)
        assert '2026-02-10' in d.index

    def test_B01_09_interior_short_day_does_not_block_a_complete_final_day(self, tmp_path):
        store = write_1m_store(tmp_path / 'i', short_day=('2026-02-10', 1200))
        s = load_daily_closes(store, '2026-03-05', min_rows=30)
        assert s.index[-1] == '2026-03-05'

    # -- existing behaviour preserved -------------------------------------

    def test_B01_10_complete_day_values_are_unchanged(self, tmp_path):
        """close == last bar, high == intraday max, low == intraday min."""
        store = write_1m_store(tmp_path / 'j')
        raw = pd.read_parquet(store / 'year=2026' / 'month=03' / 'data.parquet')
        dt = pd.to_datetime(raw['open_time'], unit='ms', utc=True)
        day = raw[dt.dt.strftime('%Y-%m-%d') == '2026-03-05']
        d = load_daily_ohlcv(store, '2026-03-05', min_rows=30)
        row = d.loc['2026-03-05']
        assert row['close'] == pytest.approx(day['close'].iloc[-1])
        assert row['high'] == pytest.approx(day['high'].max())
        assert row['low'] == pytest.approx(day['low'].min())
        s = load_daily_closes(store, '2026-03-05', min_rows=30)
        assert s.loc['2026-03-05'] == pytest.approx(day['close'].iloc[-1])

    def test_B01_11_absent_day_keeps_its_own_clearer_message(self, tmp_path):
        """Ordering matters: a wholly-absent day is 'not in store', not 'incomplete'."""
        store = write_1m_store(tmp_path / 'k')
        with pytest.raises(DataUnavailableError, match='not in OHLCV store'):
            load_daily_closes(store, '2026-03-09', min_rows=30)
        with pytest.raises(DataUnavailableError, match='not in store'):
            load_daily_ohlcv(store, '2026-03-09', min_rows=30)

    def test_B01_12_constant_is_pinned(self):
        assert MIN_DAILY_BARS == 1440, 'a complete UTC day is 1440 one-minute bars'

    # -- counting window, with data PRESENT AFTER through_date ------------
    #
    # These three exist because adversarial mutation M06/M07 survived the first
    # version of this battery: every fixture above ends AT through_date, so the
    # window's upper bound was never load-bearing. Production is never that
    # shape — the job asks for yesterday while the current month's partition
    # already holds today's partial bars — so a broken upper bound is directly
    # exploitable there:
    #   M06 (no upper bound):  1000 bars yesterday + 500 today = 1500 -> accepted
    #   M07 (bound inclusive): 1439 bars + next day's 00:00 bar = 1440 -> accepted
    # Both let a truncated day through. The window must count [day, day+1) only.

    def test_B01_14_later_bars_do_not_inflate_a_complete_day(self, tmp_path):
        store = write_1m_store(tmp_path / 'l', end='2026-03-07')
        s = load_daily_closes(store, '2026-03-05', min_rows=30)
        assert s.index[-1] == '2026-03-05', 'series must stop at through_date'

    def test_B01_15_later_complete_days_cannot_rescue_a_truncated_through_date(self, tmp_path):
        """Kills M06: bars after through_date must not count toward its total."""
        store = write_1m_store(tmp_path / 'm', end='2026-03-07',
                               short_day=('2026-03-05', 1000))
        with pytest.raises(DataUnavailableError, match=r'has 1000 1m bars'):
            load_daily_closes(store, '2026-03-05', min_rows=30)
        with pytest.raises(DataUnavailableError, match=r'has 1000 1m bars'):
            load_daily_ohlcv(store, '2026-03-05', min_rows=30)

    def test_B01_16_next_day_bar_cannot_complete_a_one_short_day(self, tmp_path):
        """Kills M07: the next day's 00:00 bar must not be counted."""
        store = write_1m_store(tmp_path / 'n', end='2026-03-07',
                               short_day=('2026-03-05', MIN_DAILY_BARS - 1))
        with pytest.raises(DataUnavailableError, match=r'has 1439 1m bars, need 1440'):
            load_daily_closes(store, '2026-03-05', min_rows=30)
        with pytest.raises(DataUnavailableError, match=r'has 1439 1m bars, need 1440'):
            load_daily_ohlcv(store, '2026-03-05', min_rows=30)

    # -- contract against the real corpus ---------------------------------

    @pytest.mark.parametrize('dataset', ['spot', 'perp'])
    def test_B01_13_guard_is_retroactively_inert_on_the_real_corpus(self, dataset):
        """
        Every real short day must predate the first forward run_date (2026-06-15),
        so this guard cannot alter an observation that was already collected.
        Skips when the gitignored corpus is absent (CI).
        """
        files = sorted(glob.glob(
            str(_RESEARCH_ROOT / 'data' / 'raw' / dataset / 'BTCUSDT' / '**' / '*.parquet'),
            recursive=True,
        ))
        if not files:
            pytest.skip(f'{dataset} corpus not present')
        raw = pd.concat(
            [pd.read_parquet(f, columns=['open_time']) for f in files], ignore_index=True
        )
        ts = raw['open_time'].astype('int64')
        if ts.max() > 1_500_000_000_000_000:
            ts = ts // 1000
        counts = pd.to_datetime(ts, unit='ms', utc=True).dt.strftime('%Y-%m-%d').value_counts()
        short = sorted(counts[counts < MIN_DAILY_BARS].index)
        assert [d for d in short if d >= '2026-06-15'] == [], (
            f'{dataset} short days on/after the first forward run_date: {short}'
        )


# ===========================================================================
# Group B02 — NaN close at run_date must not become a flat decision
# ===========================================================================

class TestB02NaNCloseAtRunDate:

    @staticmethod
    def _closes(n=40, start='2026-01-01'):
        idx = pd.date_range(start, periods=n, freq='D').strftime('%Y-%m-%d')
        return pd.Series(np.linspace(40_000, 41_000, n), index=idx, dtype=float)

    def _call(self, env, closes, compute_fn=nan_to_flat):
        fn, pre, db = env
        return run_signal(
            signal_id='probe_signal', run_date=closes.index[-1], closes=closes,
            compute_fn=compute_fn, raw_value_fn=lambda s: None,
            pre_reg_path=pre, fn_file_path=fn, db_path=db,
            hold_days=7, min_history=30, cost_bps=15.0,
        )

    def test_B02_01_nan_close_at_run_date_raises(self, signal_env):
        closes = self._closes()
        closes.iloc[-1] = np.nan
        with pytest.raises(DataUnavailableError, match='is NaN'):
            self._call(signal_env, closes)

    def test_B02_02_nothing_is_persisted_on_the_unavailable_path(self, signal_env):
        _, _, db = signal_env
        closes = self._closes()
        closes.iloc[-1] = np.nan
        with pytest.raises(DataUnavailableError):
            self._call(signal_env, closes)
        conn = sqlite3.connect(db)
        for table in ('run_log', 'signal_log', 'trade_entries', 'trade_exits'):
            n = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
            assert n == 0, f'{table} must stay empty when the price is unavailable'
        conn.close()

    def test_B02_03_message_forbids_the_flat_interpretation(self, signal_env):
        closes = self._closes()
        closes.iloc[-1] = np.nan
        with pytest.raises(DataUnavailableError) as exc:
            self._call(signal_env, closes)
        assert 'must not be recorded as a flat decision' in str(exc.value)

    def test_B02_04_valid_close_still_collects(self, signal_env):
        _, _, db = signal_env
        assert self._call(signal_env, self._closes()) == 'ok'
        conn = sqlite3.connect(db)
        assert conn.execute('SELECT COUNT(*) FROM run_log').fetchone()[0] == 1
        assert conn.execute('SELECT status FROM run_log').fetchone()[0] == 'ok'
        conn.close()

    def test_B02_05_a_genuine_flat_decision_is_still_recorded(self, signal_env):
        """The guard must reject missing DATA only — a real 0.0 decision still collects."""
        _, _, db = signal_env
        assert self._call(
            signal_env, self._closes(),
            compute_fn=lambda c: pd.Series(0.0, index=c.index),
        ) == 'ok'
        conn = sqlite3.connect(db)
        assert conn.execute('SELECT position FROM signal_log').fetchone()[0] == 0.0
        assert conn.execute('SELECT COUNT(*) FROM trade_entries').fetchone()[0] == 0
        conn.close()

    def test_B02_06_nan_check_precedes_the_history_check(self, signal_env):
        """A NaN close with short history reports the NaN, the more specific fault."""
        closes = self._closes(n=31)
        closes.iloc[-1] = np.nan
        with pytest.raises(DataUnavailableError, match='is NaN'):
            self._call(signal_env, closes)

    def test_B02_07_scope_boundary_interior_nan_is_not_covered(self, signal_env):
        """
        Documents the deliberate scope of Gate 6B.1: only the run_date value is
        validated. An interior NaN cannot arise through any production loader
        (all four call dropna), so it is out of scope here — recorded so a later
        reader does not mistake this for coverage that exists.
        """
        closes = self._closes()
        closes.iloc[5] = np.nan
        assert self._call(
            signal_env, closes,
            compute_fn=lambda c: pd.Series(1.0, index=c.index),
        ) == 'ok'
