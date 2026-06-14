"""
runner_base.py
==============
Shared daily forward-evidence collection logic for H5, G1, G3 runners.

Public interface:
    run_signal(...) -> Literal['ok', 'skipped']
    load_daily_closes(...) -> pd.Series
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
    compute_fn:   Callable[[pd.Series], pd.Series],
    raw_value_fn: Callable[[pd.Series], float | None],
    pre_reg_path: pathlib.Path,
    fn_file_path: pathlib.Path,
    db_path:      pathlib.Path,
    hold_days:    int,
    min_history:  int,
    cost_bps:     float,
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

    # ── Step 4: Compute signal ───────────────────────────────────────────────
    try:
        pos_series = compute_fn(closes_to_date)
        position   = float(pos_series.iloc[-1])
        raw_value  = raw_value_fn(closes_to_date)
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
