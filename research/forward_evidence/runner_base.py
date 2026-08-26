"""
runner_base.py
==============
Shared daily forward-evidence collection logic for H5, G1, G3 runners.

Public interface:
    run_signal(...) -> Literal['ok', 'skipped']   (optional aux_series for multi-input signals)
    run_ohlcv_signal(...) -> Literal['ok', 'skipped']  (OHLCV-based signals, e.g. G3 ATR)
    load_daily_closes(...) -> pd.Series
    load_daily_dvol(...) -> pd.Series
    load_daily_funding(...) -> pd.Series
    load_daily_ohlcv(...) -> pd.DataFrame
    DataUnavailableError

Design decisions (approved 2026-06-15):
    - Idempotency check BEFORE fn_hash check.
    - Exit price = closes[planned_exit_date], NOT closes[run_date].
    - No early exits: hold until planned_exit_date regardless of signal changes.
    - closes filtered to run_date inclusive before signal computation (look-ahead guard).
    - DataUnavailableError NOT written to run_log (transient; retry is automatic).
    - Unexpected exceptions DO write 'error' to run_log (requires manual clear to retry).
    - Per-signal isolation: caller (run_daily.py) catches per-signal exceptions.
"""
from __future__ import annotations

import calendar
import logging
import pathlib
import sqlite3
from datetime import date, timedelta
from typing import Callable, Literal

import numpy as np
import pandas as pd

from forward_evidence.pre_reg_guard import FnHashViolation, _sha256_lf, verify_fn_hash

log = logging.getLogger(__name__)

_HERE          = pathlib.Path(__file__).resolve().parent
_RESEARCH_ROOT = _HERE.parent


class DataUnavailableError(RuntimeError):
    """Raised when required OHLCV price data is absent from the Parquet store."""


# ---------------------------------------------------------------------------
# Price loading
# ---------------------------------------------------------------------------

def load_daily_closes(
    parquet_dir: pathlib.Path,
    through_date: str,
    min_rows: int,
) -> pd.Series:
    """
    Load daily BTC close prices from the Parquet partition store.

    Reads 1-minute candles (open_time int64 ms UTC, close float64),
    resamples to UTC daily (last minute bar = daily close).

    Parameters
    ----------
    parquet_dir : e.g. research/data/raw/spot/BTCUSDT
    through_date : 'YYYY-MM-DD' inclusive upper bound
    min_rows : minimum daily rows required (= signal MIN_HISTORY)

    Returns
    -------
    pd.Series indexed by 'YYYY-MM-DD' strings, chronological, ending at through_date.

    Raises
    ------
    DataUnavailableError if through_date absent or len < min_rows.
    """
    through_dt = date.fromisoformat(through_date)
    # Load min_rows + 32 calendar days of partitions (buffer for month boundaries)
    start_dt   = through_dt - timedelta(days=min_rows + 32)

    partitions = _select_partitions(parquet_dir, start_dt, through_dt)
    if not partitions:
        raise DataUnavailableError(
            f"No OHLCV partitions found in {parquet_dir} for "
            f"{start_dt}..{through_dt}. Run ingest/binance_ohlcv.py to populate."
        )

    frames = [pd.read_parquet(p, columns=['open_time', 'close']) for p in partitions]
    raw = pd.concat(frames, ignore_index=True)

    # Normalise timestamps: 2024 and earlier = 13-digit ms; 2025+ could be 16-digit µs
    ts = raw['open_time'].astype('int64')
    if ts.max() > 1_500_000_000_000_000:
        ts = ts // 1000
    raw['dt'] = pd.to_datetime(ts, unit='ms', utc=True)

    daily = (
        raw.set_index('dt')
           .sort_index()['close']
           .resample('D')
           .last()
           .dropna()
    )
    daily.index = daily.index.strftime('%Y-%m-%d')
    daily = daily[daily.index <= through_date]

    if through_date not in daily.index:
        latest = daily.index[-1] if len(daily) > 0 else 'none'
        raise DataUnavailableError(
            f"Close price for {through_date} not in OHLCV store. "
            f"Latest available: {latest}. Run ingest/binance_ohlcv.py to update."
        )
    if len(daily) < min_rows:
        raise DataUnavailableError(
            f"Insufficient OHLCV history: need {min_rows} rows, have {len(daily)}. "
            "Run ingest/binance_ohlcv.py to extend the Parquet store."
        )
    return daily


def _select_partitions(
    parquet_dir: pathlib.Path,
    start_dt: date,
    end_dt: date,
) -> list[pathlib.Path]:
    result = []
    for p in sorted(parquet_dir.glob("year=*/month=*/data.parquet")):
        try:
            y = int(p.parent.parent.name.split('=')[1])
            m = int(p.parent.name.split('=')[1])
        except (IndexError, ValueError):
            continue
        month_start = date(y, m, 1)
        month_end   = date(y, m, calendar.monthrange(y, m)[1])
        if month_end >= start_dt and month_start <= end_dt:
            result.append(p)
    return result


def load_daily_dvol(
    parquet_dir: pathlib.Path,
    through_date: str,
    min_rows: int,
) -> pd.Series:
    """
    Load daily Deribit DVOL close prices from the Parquet partition store.

    DVOL is already a daily (1D) series — one row per UTC day at the 00:00
    boundary. The resample('D').last() is a safe no-op that also collapses any
    accidental duplicate. Mirrors load_daily_closes (same partition selection,
    timestamp-normalisation, and DataUnavailableError semantics) so the forward
    DVOL path behaves identically to the spot path.

    Parameters
    ----------
    parquet_dir : e.g. research/data/raw/dvol/BTC
    through_date : 'YYYY-MM-DD' inclusive upper bound
    min_rows : minimum daily rows required (= signal MIN_HISTORY)

    Returns
    -------
    pd.Series indexed by 'YYYY-MM-DD' strings, chronological, ending at through_date.
    Values are DVOL in annualised vol points (e.g. 48.1 = 48.1%).

    Raises
    ------
    DataUnavailableError if through_date absent or len < min_rows. This is the
    fail-safe that converts silent DVOL staleness (DVOL ingest is non-critical in
    daily_ingest.py) into a transient skip rather than a corrupt signal.
    """
    through_dt = date.fromisoformat(through_date)
    # Load min_rows + 32 calendar days of partitions (buffer for month boundaries)
    start_dt   = through_dt - timedelta(days=min_rows + 32)

    partitions = _select_partitions(parquet_dir, start_dt, through_dt)
    if not partitions:
        raise DataUnavailableError(
            f"No DVOL partitions found in {parquet_dir} for "
            f"{start_dt}..{through_dt}. Run ingest/dvol.py to populate."
        )

    frames = [pd.read_parquet(p, columns=['open_time', 'close']) for p in partitions]
    raw = pd.concat(frames, ignore_index=True)

    # Normalise timestamps: DVOL stores 13-digit ms; guard for µs by the same
    # threshold used in load_daily_closes (defensive — DVOL has been ms to date).
    ts = raw['open_time'].astype('int64')
    if ts.max() > 1_500_000_000_000_000:
        ts = ts // 1000
    raw['dt'] = pd.to_datetime(ts, unit='ms', utc=True)

    daily = (
        raw.set_index('dt')
           .sort_index()['close']
           .resample('D')
           .last()
           .dropna()
    )
    daily.index = daily.index.strftime('%Y-%m-%d')
    daily = daily[daily.index <= through_date]

    if through_date not in daily.index:
        latest = daily.index[-1] if len(daily) > 0 else 'none'
        raise DataUnavailableError(
            f"DVOL close for {through_date} not in store. "
            f"Latest available: {latest}. Run ingest/dvol.py to update."
        )
    if len(daily) < min_rows:
        raise DataUnavailableError(
            f"Insufficient DVOL history: need {min_rows} rows, have {len(daily)}. "
            "Run ingest/dvol.py to extend the Parquet store."
        )
    return daily


#: Minimum funding observations required before a UTC day yields a daily mean.
#: Binance USDT-perp funding settles 8-hourly (00:00/08:00/16:00 UTC), so a
#: complete day has exactly 3. The value MUST stay equal to
#: research/evidence/engine.py MIN_DAILY_FUND -- that module computed the
#: funding history every registered funding hypothesis was evaluated against, so
#: a divergence here would silently change what "daily mean funding" means
#: between research and forward collection. engine.py is not imported (it pulls
#: duckdb into the forward runtime); equality is asserted by test instead.
MIN_DAILY_FUND = 2


def load_daily_funding(
    parquet_dir: pathlib.Path,
    through_date: str,
    min_rows: int,
) -> pd.Series:
    """
    Load daily mean Binance USDT-perp funding, in basis points, from Parquet.

    Semantics are those of research/evidence/engine.py load_master(), reproduced
    exactly rather than reinterpreted:

        AVG(funding_rate) * 10000.0  grouped by UTC day,
        HAVING COUNT(*) >= MIN_DAILY_FUND

    A day with fewer than MIN_DAILY_FUND observations is DROPPED, not averaged
    over what happens to be present -- a single 8-hour print is not a daily mean,
    and admitting one would bias the trailing-tercile regime boundaries that
    funding hypotheses are defined on.

    Mirrors load_daily_dvol (same partition selection, timestamp normalisation
    and DataUnavailableError semantics) so the funding path behaves identically
    to the spot and DVOL paths.

    Parameters
    ----------
    parquet_dir : e.g. research/data/raw/funding/BTCUSDT
    through_date : 'YYYY-MM-DD' inclusive upper bound
    min_rows : minimum daily rows required (= signal MIN_HISTORY)

    Returns
    -------
    pd.Series indexed by 'YYYY-MM-DD' strings, chronological, ending at
    through_date. Values are daily mean funding in bps (e.g. 1.0827).

    Raises
    ------
    DataUnavailableError if through_date is absent or fewer than min_rows days
    are available. Callers convert this into a per-signal skip, never a failure
    of the whole daily job.
    """
    through_dt = date.fromisoformat(through_date)
    start_dt   = through_dt - timedelta(days=min_rows + 32)

    partitions = _select_partitions(parquet_dir, start_dt, through_dt)
    if not partitions:
        raise DataUnavailableError(
            f"No funding partitions found in {parquet_dir} for "
            f"{start_dt}..{through_dt}. Run ingest/funding.py to populate."
        )

    frames = [
        pd.read_parquet(p, columns=['timestamp_ms', 'funding_rate'])
        for p in partitions
    ]
    raw = pd.concat(frames, ignore_index=True)

    # Defensive µs guard, mirroring load_daily_closes/load_daily_dvol. Funding
    # has been 13-digit ms to date; this is a no-op for correct data and stops a
    # silent unit switch from producing a 1000x-wrong grouping.
    ts = raw['timestamp_ms'].astype('int64')
    if ts.max() > 1_500_000_000_000_000:
        ts = ts // 1000
    raw['dt'] = pd.to_datetime(ts, unit='ms', utc=True)

    # count() excludes NaN and mean() skips it, so a NaN print is not counted
    # toward MIN_DAILY_FUND and cannot bias the mean -- no explicit dropna is
    # needed here, and adding one would be unreachable code.
    grouped = raw.set_index('dt').sort_index()['funding_rate'].resample('D')
    daily   = grouped.mean() * 10_000.0
    counts  = grouped.count()
    daily   = daily[counts >= MIN_DAILY_FUND].dropna()

    daily.index = daily.index.strftime('%Y-%m-%d')
    daily = daily[daily.index <= through_date]

    if through_date not in daily.index:
        latest = daily.index[-1] if len(daily) > 0 else 'none'
        raise DataUnavailableError(
            f"Daily mean funding for {through_date} not in store "
            f"(or fewer than {MIN_DAILY_FUND} observations that day). "
            f"Latest available: {latest}. Run ingest/funding.py to update."
        )
    if len(daily) < min_rows:
        raise DataUnavailableError(
            f"Insufficient funding history: need {min_rows} rows, have {len(daily)}. "
            "Run ingest/funding.py to extend the Parquet store."
        )
    return daily


def load_daily_ohlcv(
    parquet_dir: pathlib.Path,
    through_date: str,
    min_rows: int,
) -> pd.DataFrame:
    """
    Load daily BTC OHLCV data from the Parquet partition store.

    Reads 1-minute candles (open_time int64 ms UTC, high/low/close float64),
    resamples to UTC daily using max(high), min(low), last(close) — so each
    daily row captures the true intraday range, not the last bar's H/L.

    Parameters
    ----------
    parquet_dir : e.g. research/data/raw/spot/BTCUSDT
    through_date : 'YYYY-MM-DD' inclusive upper bound
    min_rows : minimum daily rows required (= signal MIN_HISTORY)

    Returns
    -------
    pd.DataFrame with columns ['high', 'low', 'close'], indexed by 'YYYY-MM-DD'
    strings, chronological, ending at through_date. Index is unique (one row/day).

    Raises
    ------
    DataUnavailableError if through_date absent or len < min_rows.
    """
    through_dt = date.fromisoformat(through_date)
    start_dt   = through_dt - timedelta(days=min_rows + 32)

    partitions = _select_partitions(parquet_dir, start_dt, through_dt)
    if not partitions:
        raise DataUnavailableError(
            f"No OHLCV partitions found in {parquet_dir} for "
            f"{start_dt}..{through_dt}. Run ingest/binance_ohlcv.py to populate."
        )

    frames = [
        pd.read_parquet(p, columns=['open_time', 'high', 'low', 'close'])
        for p in partitions
    ]
    raw = pd.concat(frames, ignore_index=True)

    # Normalise timestamps: 2024 and earlier = 13-digit ms; 2025+ could be 16-digit µs
    ts = raw['open_time'].astype('int64')
    if ts.max() > 1_500_000_000_000_000:
        ts = ts // 1000
    raw['dt'] = pd.to_datetime(ts, unit='ms', utc=True)

    daily = (
        raw.set_index('dt')
           .sort_index()[['high', 'low', 'close']]
           .resample('D')
           .agg({'high': 'max', 'low': 'min', 'close': 'last'})
           .dropna(subset=['close'])
    )
    daily.index = daily.index.strftime('%Y-%m-%d')
    daily = daily[daily.index <= through_date]

    if through_date not in daily.index:
        latest = daily.index[-1] if len(daily) > 0 else 'none'
        raise DataUnavailableError(
            f"OHLCV for {through_date} not in store. "
            f"Latest available: {latest}. Run ingest/binance_ohlcv.py to update."
        )
    if len(daily) < min_rows:
        raise DataUnavailableError(
            f"Insufficient OHLCV history: need {min_rows} rows, have {len(daily)}. "
            "Run ingest/binance_ohlcv.py to extend the Parquet store."
        )
    return daily


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _open_conn(db_path: pathlib.Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _get_run_log_status(
    conn: sqlite3.Connection, signal_id: str, run_date: str
) -> str | None:
    row = conn.execute(
        "SELECT status FROM run_log WHERE signal_id=? AND run_date=?",
        (signal_id, run_date),
    ).fetchone()
    return row[0] if row else None


def _get_overdue_open_trades(
    conn: sqlite3.Connection, signal_id: str, run_date: str
) -> list[dict]:
    rows = conn.execute(
        "SELECT id, planned_exit_date FROM trade_entries "
        "WHERE signal_id=? AND open=1 AND planned_exit_date<=?",
        (signal_id, run_date),
    ).fetchall()
    return [{'id': r[0], 'planned_exit_date': r[1]} for r in rows]


def _has_open_trade(conn: sqlite3.Connection, signal_id: str) -> bool:
    return conn.execute(
        "SELECT COUNT(*) FROM trade_entries WHERE signal_id=? AND open=1",
        (signal_id,),
    ).fetchone()[0] > 0


def _record_mini(
    db_path: pathlib.Path,
    signal_id: str,
    run_date: str,
    fn_hash: str,
    status: str,
    note: str,
) -> None:
    """Write a run_log row in its own mini-transaction (violations and errors)."""
    conn = _open_conn(db_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO run_log "
            "(signal_id, run_date, fn_hash, status, note) VALUES (?,?,?,?,?)",
            (signal_id, run_date, fn_hash, status, note[:500]),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_signal(
    *,
    signal_id:    str,
    run_date:     str,
    closes:       pd.Series,
    compute_fn:   Callable[..., pd.Series],
    raw_value_fn: Callable[..., float | None],
    pre_reg_path: pathlib.Path,
    fn_file_path: pathlib.Path,
    db_path:      pathlib.Path,
    hold_days:    int,
    min_history:  int,
    cost_bps:     float,
    aux_series:   pd.Series | None = None,
) -> Literal['ok', 'skipped']:
    """
    Execute one day's forward evidence collection for a registered signal.

    Parameters
    ----------
    signal_id     : Registry key, e.g. 'h5_vol_regime'.
    run_date      : 'YYYY-MM-DD' UTC — the date whose close price drives logic.
    closes        : Daily close price Series, index='YYYY-MM-DD', chronological.
                    Filtered to run_date before signal computation (look-ahead guard).
                    Caller may pass a longer series.
    compute_fn    : Locked decision function: close -> position Series {-1, 0, +1}.
    raw_value_fn  : Auxiliary intermediate value (not covered by fn_hash).
    pre_reg_path  : Path to the final pre-registration JSON.
    fn_file_path  : Path to the locked decision function file to hash-verify.
    db_path       : Path to evidence_forward.db.
    hold_days     : Calendar days per hold period.
    min_history   : Minimum rows for compute_fn (signal MIN_HISTORY constant).
    cost_bps      : Transaction cost in bps (from collection_config.yaml).
    aux_series    : Optional second daily input for multi-input signals (e.g.
                    DVOL for H-IVRV). Defaults None — single-input signals (H5,
                    G1, G3) leave it unset and the code path is unchanged. When
                    provided, it is filtered to run_date (same look-ahead guard as
                    closes), validated for presence+non-NaN at run_date, and
                    passed as the 2nd positional arg to compute_fn/raw_value_fn.
                    Never used for trade pricing — entries/exits price on closes.

    Returns
    -------
    'ok'      — new run_log, signal_log, and trade rows written.
    'skipped' — prior ok/skipped row found; this call was a no-op.

    Raises
    ------
    FnHashViolation      — decision function tampered; run_log row written.
    DataUnavailableError — closes missing run_date or insufficient history.
                           NOT written to run_log (transient; retry is automatic).
    RuntimeError         — idempotency block (prior error/violation on record).
    Exception            — unexpected; run_log row written as 'error'.
    """
    actual_hash: str | None = None

    # ── Step 1: Idempotency pre-check ────────────────────────────────────────
    conn = _open_conn(db_path)
    try:
        prior = _get_run_log_status(conn, signal_id, run_date)
    finally:
        conn.close()

    if prior in ('ok', 'skipped'):
        log.info("[%s] already ran on %s (status=%s)", signal_id, run_date, prior)
        return 'skipped'
    if prior == 'fn_hash_violation':
        raise RuntimeError(
            f"[{signal_id}] fn_hash_violation on record for {run_date}. "
            "Fix the decision function (new pre-registration required if intentional), "
            "then delete the run_log row to retry."
        )
    if prior == 'error':
        raise RuntimeError(
            f"[{signal_id}] prior run errored on {run_date}. "
            "Investigate, then delete the run_log row to retry."
        )
    # prior is None — proceed

    # ── Step 2: fn_hash guard ────────────────────────────────────────────────
    try:
        actual_hash = verify_fn_hash(pre_reg_path, fn_file_path, signal_id)
    except FnHashViolation as exc:
        actual_hash = _sha256_lf(fn_file_path)
        _record_mini(db_path, signal_id, run_date, actual_hash, 'fn_hash_violation', str(exc))
        log.error("[%s] fn_hash_violation on %s", signal_id, run_date)
        raise

    # ── Step 3: Price validation ─────────────────────────────────────────────
    closes_to_date = closes[closes.index <= run_date]
    if run_date not in closes_to_date.index:
        latest = closes_to_date.index[-1] if len(closes_to_date) > 0 else 'none'
        raise DataUnavailableError(
            f"[{signal_id}] Close for {run_date} not in series. Latest: {latest}"
        )
    if len(closes_to_date) < min_history:
        raise DataUnavailableError(
            f"[{signal_id}] Insufficient history: need {min_history} rows, "
            f"have {len(closes_to_date)}"
        )

    # ── Step 3b: Auxiliary-series validation (only when aux_series is used) ────
    # Backward-compatible: aux_series defaults None, so single-input signals
    # (H5, G1, G3) skip this block and aux_to_date stays None. A missing or NaN
    # aux value at run_date is a transient DataUnavailableError (auto-retry),
    # never a silent flat — absence of data must not be recorded as a decision.
    aux_to_date: pd.Series | None = None
    if aux_series is not None:
        aux_to_date = aux_series[aux_series.index <= run_date]
        if run_date not in aux_to_date.index or pd.isna(aux_to_date.loc[run_date]):
            latest = aux_to_date.index[-1] if len(aux_to_date) > 0 else 'none'
            raise DataUnavailableError(
                f"[{signal_id}] Auxiliary series unavailable for {run_date}. "
                f"Latest: {latest}"
            )

    # ── Step 4: Compute signal ───────────────────────────────────────────────
    try:
        if aux_to_date is None:
            pos_series = compute_fn(closes_to_date)
            position   = float(pos_series.iloc[-1])
            raw_value  = raw_value_fn(closes_to_date)
        else:
            pos_series = compute_fn(closes_to_date, aux_to_date)
            position   = float(pos_series.iloc[-1])
            raw_value  = raw_value_fn(closes_to_date, aux_to_date)
    except Exception as exc:
        _record_mini(
            db_path, signal_id, run_date, actual_hash, 'error',
            f"Signal computation failed: {type(exc).__name__}: {exc}",
        )
        raise

    # ── Step 5: Single atomic transaction ────────────────────────────────────
    conn = _open_conn(db_path)
    try:
        conn.execute("BEGIN")

        # 5a — Close overdue trades (planned_exit_date <= run_date)
        overdue = _get_overdue_open_trades(conn, signal_id, run_date)
        for trade in overdue:
            exit_date = trade['planned_exit_date']
            if exit_date not in closes_to_date.index:
                raise DataUnavailableError(
                    f"[{signal_id}] Close for planned_exit_date {exit_date} not in series. "
                    "Cannot close overdue trade."
                )
            exit_price = float(closes_to_date.loc[exit_date])
            conn.execute(
                "INSERT INTO trade_exits "
                "(entry_id, exit_date, exit_close_price, cost_bps) VALUES (?,?,?,?)",
                (trade['id'], exit_date, exit_price, cost_bps),
            )
            conn.execute("UPDATE trade_entries SET open=0 WHERE id=?", (trade['id'],))

        # 5b — Open new trade if warranted
        if not _has_open_trade(conn, signal_id) and position != 0.0:
            direction    = int(position)
            entry_price  = float(closes_to_date.loc[run_date])
            planned_exit = (
                date.fromisoformat(run_date) + timedelta(days=hold_days)
            ).isoformat()
            conn.execute(
                "INSERT INTO trade_entries "
                "(signal_id, entry_date, direction, entry_close_price, planned_exit_date, open) "
                "VALUES (?,?,?,?,?,1)",
                (signal_id, run_date, direction, entry_price, planned_exit),
            )

        # 5c — run_log (must be last in logical order; lastrowid gives run_id for FK)
        conn.execute(
            "INSERT INTO run_log (signal_id, run_date, fn_hash, status) "
            "VALUES (?,?,?,'ok')",
            (signal_id, run_date, actual_hash),
        )
        run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # 5d — signal_log
        conn.execute(
            "INSERT INTO signal_log (signal_id, day, position, raw_value, run_id) "
            "VALUES (?,?,?,?,?)",
            (signal_id, run_date, position, raw_value, run_id),
        )

        conn.execute("COMMIT")
        log.info("[%s] ok  run_date=%s  position=%.1f", signal_id, run_date, position)
        return 'ok'

    except DataUnavailableError:
        conn.execute("ROLLBACK")
        raise

    except Exception as exc:
        conn.execute("ROLLBACK")
        _record_mini(
            db_path, signal_id, run_date, actual_hash, 'error',
            f"Transaction failed: {type(exc).__name__}: {exc}",
        )
        raise

    finally:
        conn.close()


def run_ohlcv_signal(
    *,
    signal_id:    str,
    run_date:     str,
    ohlcv:        pd.DataFrame,
    compute_fn:   Callable[..., pd.Series],
    raw_value_fn: Callable[..., float | None],
    pre_reg_path: pathlib.Path,
    fn_file_path: pathlib.Path,
    db_path:      pathlib.Path,
    hold_days:    int,
    min_history:  int,
    cost_bps:     float,
) -> Literal['ok', 'skipped']:
    """
    Execute one day's forward evidence collection for an OHLCV-based signal.

    Parallel to run_signal() but accepts ohlcv (DataFrame columns: high, low,
    close) instead of a closes Series. Trade pricing always uses close prices:
    entry at ohlcv['close'].loc[run_date], exit at ohlcv['close'].loc[exit_date].
    No aux_series: OHLCV signals receive their full context via the ohlcv frame.

    Parameters
    ----------
    signal_id     : Registry key, e.g. 'g3_atr_expansion'.
    run_date      : 'YYYY-MM-DD' UTC — the date whose close price drives logic.
    ohlcv         : Daily OHLCV DataFrame indexed by 'YYYY-MM-DD' strings,
                    columns ['high', 'low', 'close'], chronological, unique index.
                    Caller may pass a longer frame; filtered to run_date here.
    compute_fn    : Locked decision function: ohlcv_df -> position Series {-1, 0, +1}.
    raw_value_fn  : Auxiliary intermediate value (not covered by fn_hash).
    pre_reg_path  : Path to the final pre-registration JSON.
    fn_file_path  : Path to the locked decision function file to hash-verify.
    db_path       : Path to evidence_forward.db.
    hold_days     : Calendar days per hold period.
    min_history   : Minimum rows for compute_fn (= signal MIN_HISTORY constant).
    cost_bps      : Transaction cost in bps (from collection_config.yaml).

    Returns
    -------
    'ok'      — new run_log, signal_log, and trade rows written.
    'skipped' — prior ok/skipped row found; this call was a no-op.

    Raises
    ------
    FnHashViolation      — decision function tampered; run_log row written.
    DataUnavailableError — ohlcv missing run_date, invalid high/low, or insufficient
                           history. NOT written to run_log (transient; auto-retry).
    RuntimeError         — idempotency block (prior error/violation on record).
    Exception            — unexpected; run_log row written as 'error'.
    """
    actual_hash: str | None = None

    # ── Step 1: Idempotency pre-check ────────────────────────────────────────
    conn = _open_conn(db_path)
    try:
        prior = _get_run_log_status(conn, signal_id, run_date)
    finally:
        conn.close()

    if prior in ('ok', 'skipped'):
        log.info("[%s] already ran on %s (status=%s)", signal_id, run_date, prior)
        return 'skipped'
    if prior == 'fn_hash_violation':
        raise RuntimeError(
            f"[{signal_id}] fn_hash_violation on record for {run_date}. "
            "Fix the decision function (new pre-registration required if intentional), "
            "then delete the run_log row to retry."
        )
    if prior == 'error':
        raise RuntimeError(
            f"[{signal_id}] prior run errored on {run_date}. "
            "Investigate, then delete the run_log row to retry."
        )
    # prior is None — proceed

    # ── Step 2: fn_hash guard ────────────────────────────────────────────────
    try:
        actual_hash = verify_fn_hash(pre_reg_path, fn_file_path, signal_id)
    except FnHashViolation as exc:
        actual_hash = _sha256_lf(fn_file_path)
        _record_mini(db_path, signal_id, run_date, actual_hash, 'fn_hash_violation', str(exc))
        log.error("[%s] fn_hash_violation on %s", signal_id, run_date)
        raise

    # ── Step 3: OHLCV validation ─────────────────────────────────────────────
    ohlcv_to_date = ohlcv[ohlcv.index <= run_date]
    if run_date not in ohlcv_to_date.index:
        latest = ohlcv_to_date.index[-1] if len(ohlcv_to_date) > 0 else 'none'
        raise DataUnavailableError(
            f"[{signal_id}] OHLCV for {run_date} not in frame. Latest: {latest}"
        )
    if len(ohlcv_to_date) < min_history:
        raise DataUnavailableError(
            f"[{signal_id}] Insufficient history: need {min_history} rows, "
            f"have {len(ohlcv_to_date)}"
        )
    row_hlc = ohlcv_to_date.loc[run_date]
    if (
        pd.isna(row_hlc['close'])
        or pd.isna(row_hlc['high'])
        or pd.isna(row_hlc['low'])
        or float(row_hlc['high']) < float(row_hlc['low'])
    ):
        raise DataUnavailableError(
            f"[{signal_id}] Invalid OHLCV row at {run_date}: "
            f"high={row_hlc['high']} low={row_hlc['low']} close={row_hlc['close']}"
        )

    # ── Step 4: Compute signal ───────────────────────────────────────────────
    try:
        pos_series = compute_fn(ohlcv_to_date)
        position   = float(pos_series.iloc[-1])
        raw_value  = raw_value_fn(ohlcv_to_date)
    except Exception as exc:
        _record_mini(
            db_path, signal_id, run_date, actual_hash, 'error',
            f"Signal computation failed: {type(exc).__name__}: {exc}",
        )
        raise

    # ── Step 5: Single atomic transaction ────────────────────────────────────
    conn = _open_conn(db_path)
    try:
        conn.execute("BEGIN")

        # 5a — Close overdue trades (planned_exit_date <= run_date)
        overdue = _get_overdue_open_trades(conn, signal_id, run_date)
        for trade in overdue:
            exit_date = trade['planned_exit_date']
            if exit_date not in ohlcv_to_date.index:
                raise DataUnavailableError(
                    f"[{signal_id}] Close for planned_exit_date {exit_date} not in frame. "
                    "Cannot close overdue trade."
                )
            exit_price = float(ohlcv_to_date['close'].loc[exit_date])
            conn.execute(
                "INSERT INTO trade_exits "
                "(entry_id, exit_date, exit_close_price, cost_bps) VALUES (?,?,?,?)",
                (trade['id'], exit_date, exit_price, cost_bps),
            )
            conn.execute("UPDATE trade_entries SET open=0 WHERE id=?", (trade['id'],))

        # 5b — Open new trade if warranted
        if not _has_open_trade(conn, signal_id) and position != 0.0:
            direction    = int(position)
            entry_price  = float(ohlcv_to_date['close'].loc[run_date])
            planned_exit = (
                date.fromisoformat(run_date) + timedelta(days=hold_days)
            ).isoformat()
            conn.execute(
                "INSERT INTO trade_entries "
                "(signal_id, entry_date, direction, entry_close_price, planned_exit_date, open) "
                "VALUES (?,?,?,?,?,1)",
                (signal_id, run_date, direction, entry_price, planned_exit),
            )

        # 5c — run_log
        conn.execute(
            "INSERT INTO run_log (signal_id, run_date, fn_hash, status) "
            "VALUES (?,?,?,'ok')",
            (signal_id, run_date, actual_hash),
        )
        run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # 5d — signal_log
        conn.execute(
            "INSERT INTO signal_log (signal_id, day, position, raw_value, run_id) "
            "VALUES (?,?,?,?,?)",
            (signal_id, run_date, position, raw_value, run_id),
        )

        conn.execute("COMMIT")
        log.info("[%s] ok  run_date=%s  position=%.1f", signal_id, run_date, position)
        return 'ok'

    except DataUnavailableError:
        conn.execute("ROLLBACK")
        raise

    except Exception as exc:
        conn.execute("ROLLBACK")
        _record_mini(
            db_path, signal_id, run_date, actual_hash, 'error',
            f"Transaction failed: {type(exc).__name__}: {exc}",
        )
        raise

    finally:
        conn.close()
