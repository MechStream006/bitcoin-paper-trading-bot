"""
M2/M3/M4/M5 — Daily Evidence Job.

Loads the Binance corpus, computes H6 signals using the frozen decision
function, and writes evidence to the SQLite forward_evidence.db.

Handles:
  M2 — Daily compute + corpus_daily / regime_states / signal_log
  M3 — Decision logging (decision_hash + fn_hash in run_log)
  M4 — Trade lifecycle (trade_entries, trade_exits, attribution)
  M5 — Forward labels (trade_labels, mae_mfe_path)

Modes:
  --backfill  Process all days in corpus not yet in corpus_daily (first run).
  (no flag)   Process only the most recent corpus day (daily cron).

Anti-lookahead guarantee:
  compute_h6_decision() only sees data up to and including the row being
  processed. The rolling window is always [0 .. t] — no future rows.

Forward evidence definition:
  Trades with entry_day_ts >= PRE_REGISTRATION_DAY_TS are "forward evidence."
  Historical trades are stored for regime context but excluded from
  evidence snapshots and promotion-criteria evaluation.

Run from research/:
    python forward_evidence/daily_job.py --backfill
    python forward_evidence/daily_job.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sqlite3
import sys
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

_HERE     = pathlib.Path(__file__).resolve().parent
_RESEARCH = _HERE.parent
for p in (str(_HERE), str(_RESEARCH)):
    if p not in sys.path:
        sys.path.insert(0, p)

from forward_evidence.decision_function import (          # noqa: E402
    FUNCTION_SPEC,
    MIN_HISTORY,
    HOLD_DAYS,
    compute_h6_decision,
)
from forward_evidence.init_db import DB_PATH, init_db    # noqa: E402

# Pre-registration date — trades on/after this are "forward evidence"
PRE_REGISTRATION_DAY_TS = int(
    datetime(2026, 6, 10, tzinfo=timezone.utc).timestamp() * 1000
)

# Cost assumption (basis points, one-way)
COST_BPS = 15.0

# Horizons for forward labels (calendar days)
LABEL_HORIZONS = [5, 7, 14, 30]

# ---- compute function file hash for determinism check ----------------------
_FN_FILE  = _HERE / "decision_function.py"
_FN_HASH  = hashlib.sha256(_FN_FILE.read_bytes()).hexdigest()


# ============================================================================
# Data loading (from Binance corpus via engine.load_master)
# ============================================================================

def _load_corpus() -> pd.DataFrame:
    """
    Load the full daily corpus from the Binance research lake.
    Returns a DataFrame with columns:
        day_ts, spot_close, spot_open, spot_high, spot_low, spot_volume,
        funding_bps, perp_close, basis_bps, dvol_close
    sorted ascending by day_ts, all numeric columns as float.
    """
    from storage.duck import open_db, query, register_view   # noqa: E402

    BASE = _RESEARCH / "data" / "raw"
    con  = open_db()
    register_view(con, "spot_1m",     BASE, "spot",    "BTCUSDT")
    register_view(con, "funding_raw", BASE, "funding", "BTCUSDT")
    register_view(con, "perp_1m",     BASE, "perp",    "BTCUSDT")

    df_ohlc = query(con, """
    SELECT day_ts, o AS spot_open, h AS spot_high, l AS spot_low,
           c AS spot_close, v AS spot_volume
    FROM (
        SELECT (open_time // 86400000) * 86400000 AS day_ts,
               FIRST(open  ORDER BY open_time ASC) AS o,
               MAX(high)                           AS h,
               MIN(low)                            AS l,
               LAST(close ORDER BY open_time ASC)  AS c,
               SUM(volume)                         AS v
        FROM spot_1m
        GROUP BY open_time // 86400000
    ) ORDER BY day_ts
    """)

    df_fund = query(con, """
    SELECT (timestamp_ms // 86400000) * 86400000 AS day_ts,
           AVG(funding_rate) * 10000.0           AS funding_bps
    FROM funding_raw
    GROUP BY timestamp_ms // 86400000
    HAVING COUNT(*) >= 2
    ORDER BY day_ts
    """)

    df_perp = query(con, """
    SELECT day_ts, perp_close
    FROM (
        SELECT (open_time // 86400000) * 86400000   AS day_ts,
               LAST(close ORDER BY open_time ASC)   AS perp_close
        FROM perp_1m
        GROUP BY open_time // 86400000
    ) ORDER BY day_ts
    """)

    try:
        register_view(con, "dvol_daily", BASE, "dvol", "BTC")
        df_dvol = query(con, """
        SELECT open_time AS day_ts, close AS dvol_close
        FROM dvol_daily ORDER BY open_time
        """)
    except Exception:
        df_dvol = pd.DataFrame(columns=["day_ts", "dvol_close"])

    con.close()

    m = (df_ohlc
         .merge(df_fund,  on="day_ts", how="left")
         .merge(df_perp,  on="day_ts", how="left")
         .merge(df_dvol,  on="day_ts", how="left"))
    m["basis_bps"] = (m["perp_close"] / m["spot_close"] - 1.0) * 10_000.0
    m = m.sort_values("day_ts").reset_index(drop=True)
    return m


# ============================================================================
# Database helpers
# ============================================================================

def _already_processed(con: sqlite3.Connection) -> set[int]:
    rows = con.execute("SELECT day_ts FROM corpus_daily").fetchall()
    return {r[0] for r in rows}


def _get_active_trade(con: sqlite3.Connection) -> tuple[int | None, int | None, float | None]:
    """Returns (trade_id, planned_exit_day_ts, entry_price) or (None, None, None)."""
    row = con.execute("""
        SELECT te.trade_id, te.planned_exit_day_ts, te.entry_price
        FROM trade_entries te
        LEFT JOIN trade_exits tx ON te.trade_id = tx.trade_id
        WHERE tx.trade_id IS NULL
        ORDER BY te.entry_day_ts DESC LIMIT 1
    """).fetchone()
    if row:
        return int(row[0]), int(row[1]), float(row[2])
    return None, None, None


def _hash_feature_vector(d: dict) -> str:
    payload = json.dumps({k: str(v) for k, v in sorted(d.items())}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


# ============================================================================
# Trade lifecycle
# ============================================================================

def _open_trade(con: sqlite3.Connection, signal: dict, day_ts: int,
                entry_price: float, now_ms: int) -> int:
    """Insert trade_entries + placeholder trade_labels. Returns trade_id."""
    exit_ts = day_ts + HOLD_DAYS * 86_400_000
    cur = con.execute("""
        INSERT INTO trade_entries
            (entry_day_ts, entry_price, direction, planned_exit_day_ts,
             decision_hash, vol_regime_entry, funding_regime_entry,
             market_regime_entry, funding_bps_entry, rv_21d_entry,
             atr_ratio_entry, vrp_spread_entry, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        day_ts, entry_price, 1, exit_ts,
        signal["decision_hash"],
        signal["vol_regime"], signal["funding_regime"], signal["market_regime"],
        None,   # funding_bps inserted below via update
        signal.get("rv_21d"), None, None,
        now_ms,
    ))
    trade_id = cur.lastrowid

    # Insert placeholder label rows (forward_return filled in later)
    for h in LABEL_HORIZONS:
        label_ts = day_ts + h * 86_400_000
        con.execute("""
            INSERT INTO trade_labels (trade_id, horizon_days, label_day_ts)
            VALUES (?,?,?)
        """, (trade_id, h, label_ts))

    return trade_id


def _close_trade(con: sqlite3.Connection, trade_id: int, exit_day_ts: int,
                 exit_price: float, entry_price: float, now_ms: int) -> None:
    gross = exit_price / entry_price - 1.0
    net   = gross - COST_BPS / 10_000.0
    con.execute("""
        INSERT INTO trade_exits
            (trade_id, exit_day_ts, exit_price, exit_reason,
             gross_return, cost_bps, net_return, created_at)
        VALUES (?,?,?,?,?,?,?,?)
    """, (trade_id, exit_day_ts, exit_price, "scheduled",
          gross, COST_BPS, net, now_ms))


def _update_forward_labels(con: sqlite3.Connection, day_ts: int,
                           spot_close: float) -> None:
    """Fill any trade_labels whose label_day_ts == day_ts."""
    rows = con.execute("""
        SELECT tl.trade_id, tl.horizon_days, te.entry_price
        FROM trade_labels tl
        JOIN trade_entries te ON tl.trade_id = te.trade_id
        WHERE tl.label_day_ts = ? AND tl.label_available = 0
    """, (day_ts,)).fetchall()
    now_ms = int(time.time() * 1000)
    for row in rows:
        fwd = spot_close / float(row[2]) - 1.0
        con.execute("""
            UPDATE trade_labels
            SET forward_return=?, label_available=1, labeled_at=?
            WHERE trade_id=? AND horizon_days=?
        """, (fwd, now_ms, row[0], row[1]))


def _update_mae_mfe(con: sqlite3.Connection, trade_id: int, day_ts: int,
                    day_in_hold: int, spot_close: float, entry_price: float,
                    vol_r: str, fund_r: str, mkt_r: str) -> None:
    cum = spot_close / entry_price - 1.0
    # fetch current best MAE/MFE
    prev = con.execute("""
        SELECT mae_so_far, mfe_so_far FROM mae_mfe_path
        WHERE trade_id=? ORDER BY day_in_hold DESC LIMIT 1
    """, (trade_id,)).fetchone()
    mae = min(cum, float(prev[0]) if prev else cum)
    mfe = max(cum, float(prev[1]) if prev else cum)
    con.execute("""
        INSERT OR REPLACE INTO mae_mfe_path
            (trade_id, day_ts, day_in_hold, spot_close, cum_return,
             mae_so_far, mfe_so_far, vol_regime, funding_regime, market_regime)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (trade_id, day_ts, day_in_hold, spot_close, cum,
          mae, mfe, vol_r, fund_r, mkt_r))


def _write_attribution(con: sqlite3.Connection, trade_id: int,
                       mae_rows: list, entry_price: float,
                       net_return: float, now_ms: int) -> None:
    if not mae_rows:
        return
    closes = [r["spot_close"] for r in mae_rows]
    mkt_return = closes[-1] / entry_price - 1.0
    signal_alpha = net_return - mkt_return
    # regime stability
    vol_stable  = int(len({r["vol_regime"]    for r in mae_rows}) == 1)
    fund_stable = int(len({r["funding_regime"] for r in mae_rows}) == 1)
    mkt_stable  = int(len({r["market_regime"]  for r in mae_rows}) == 1)
    any_bear    = int(any(r["market_regime"] == "bear" for r in mae_rows))
    con.execute("""
        INSERT OR REPLACE INTO attribution
            (trade_id, market_return, carry_return, signal_alpha,
             vol_regime_stable, funding_regime_stable, market_regime_stable,
             any_hold_day_bear, created_at)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (trade_id, mkt_return, None, signal_alpha,
          vol_stable, fund_stable, mkt_stable, any_bear, now_ms))


# ============================================================================
# Process a single day
# ============================================================================

def process_day(con: sqlite3.Connection, idx: int, corpus: pd.DataFrame,
                active_trade: tuple, now_ms: int) -> tuple:
    """
    Process corpus row at index `idx`.
    active_trade = (trade_id, planned_exit_day_ts, entry_price) or (None, None, None)
    Returns updated active_trade tuple.
    """
    row       = corpus.iloc[idx]
    day_ts    = int(row["day_ts"])
    spot_c    = float(row["spot_close"])
    fund_bps  = float(row["funding_bps"]) if pd.notna(row["funding_bps"]) else float("nan")

    # --- corpus_daily ---
    con.execute("""
        INSERT OR IGNORE INTO corpus_daily
            (day_ts, spot_close, spot_open, spot_high, spot_low, spot_volume,
             funding_bps, perp_close, basis_bps, dvol_close,
             strata_funding_bps, ingested_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        day_ts, spot_c,
        _f(row, "spot_open"), _f(row, "spot_high"),
        _f(row, "spot_low"),  _f(row, "spot_volume"),
        fund_bps if not np.isnan(fund_bps) else None,
        _f(row, "perp_close"), _f(row, "basis_bps"),
        _f(row, "dvol_close"),
        None,   # strata_funding_bps: populated later if available
        now_ms,
    ))

    # --- H6 decision (frozen function on rolling window) ---
    start   = max(0, idx - MIN_HISTORY + 1)
    window  = corpus.iloc[start:idx + 1][["day_ts", "spot_close", "funding_bps"]].copy()
    window  = window.rename(columns={"spot_close": "spot_close"})
    signal  = compute_h6_decision(window)

    # --- regime_states ---
    rv30   = _compute_rv30(corpus, idx)
    dvol_c = _f(row, "dvol_close")
    vrp    = (dvol_c / 100.0 - rv30) if (dvol_c and rv30 and not np.isnan(dvol_c) and not np.isnan(rv30)) else None
    sma200 = signal.get("sma200")
    trend_dist = None
    if sma200 and not np.isnan(sma200) and sma200 > 0:
        trend_dist = (spot_c - sma200) / sma200

    con.execute("""
        INSERT OR IGNORE INTO regime_states
            (day_ts, vol_regime, funding_regime, market_regime,
             sma200, trend_dist, vrp_spread,
             strata_funding_regime, regime_diverges)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        day_ts,
        signal["vol_regime"], signal["funding_regime"], signal["market_regime"],
        sma200, trend_dist, vrp,
        signal.get("strata_funding_regime"), signal.get("regime_diverges"),
    ))

    # --- signal_log (written before trade logic; in_hold/active_trade_id updated after) ---
    con.execute("""
        INSERT OR IGNORE INTO signal_log
            (day_ts, h6_canonical, decision_hash, h6_shadow, in_hold, active_trade_id)
        VALUES (?,?,?,?,?,?)
    """, (
        day_ts, signal["h6_canonical"], signal["decision_hash"],
        signal.get("h6_shadow"), 0, None,
    ))

    # --- Trade lifecycle ---
    trade_id, exit_ts, entry_price = active_trade

    # Check if active trade should be closed today
    if trade_id is not None and day_ts >= exit_ts:
        _close_trade(con, trade_id, day_ts, spot_c, entry_price, now_ms)
        mae_rows = [dict(r) for r in con.execute(
            "SELECT * FROM mae_mfe_path WHERE trade_id=? ORDER BY day_in_hold",
            (trade_id,)
        ).fetchall()]
        net_ret = con.execute(
            "SELECT net_return FROM trade_exits WHERE trade_id=?", (trade_id,)
        ).fetchone()[0]
        _write_attribution(con, trade_id, mae_rows, entry_price, net_ret, now_ms)
        trade_id, exit_ts, entry_price = None, None, None

    # Update MAE/MFE for currently open trade
    if trade_id is not None:
        day_in_hold = (day_ts - (exit_ts - HOLD_DAYS * 86_400_000)) // 86_400_000
        _update_mae_mfe(
            con, trade_id, day_ts, int(day_in_hold),
            spot_c, entry_price,
            signal["vol_regime"], signal["funding_regime"], signal["market_regime"],
        )

    # Open a new trade if signal fires and no active hold
    if signal["h6_canonical"] == 1 and trade_id is None:
        trade_id    = _open_trade(con, signal, day_ts, spot_c, now_ms)
        exit_ts     = day_ts + HOLD_DAYS * 86_400_000
        entry_price = spot_c

    # Update signal_log with final in_hold / active_trade_id
    in_hold = int(trade_id is not None)
    con.execute("""
        UPDATE signal_log SET in_hold=?, active_trade_id=? WHERE day_ts=?
    """, (in_hold, trade_id if in_hold else None, day_ts))

    # --- Fill any forward labels that arrive today ---
    _update_forward_labels(con, day_ts, spot_c)

    return (trade_id, exit_ts, entry_price)


# ============================================================================
# Helpers
# ============================================================================

def _f(row: pd.Series, col: str) -> float | None:
    v = row.get(col, float("nan"))
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    return float(v)


def _compute_rv30(corpus: pd.DataFrame, idx: int) -> float | None:
    if idx < 30:
        return None
    closes = corpus["spot_close"].iloc[idx - 30:idx + 1].to_numpy(float)
    lr = np.log(closes[1:] / closes[:-1])
    return float(np.std(lr, ddof=1) * np.sqrt(365.0))


# ============================================================================
# Main
# ============================================================================

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true",
                    help="Process all unprocessed corpus days (first run)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Load data and compute but do not write to DB")
    args = ap.parse_args()

    t0 = time.time()
    now_ms = int(t0 * 1000)

    print("=" * 70)
    mode = "BACKFILL" if args.backfill else "DAILY"
    print(f"  M2 DAILY EVIDENCE JOB  [{mode}]"
          f"  —  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 70)

    # Load corpus
    print("  Loading corpus...", end=" ", flush=True)
    corpus = _load_corpus()
    print(f"{len(corpus)} daily rows ({corpus['day_ts'].iloc[0]} .. {corpus['day_ts'].iloc[-1]})")

    # Open (or create) evidence DB
    con = init_db(DB_PATH)
    already = _already_processed(con)

    # Determine days to process
    if args.backfill:
        to_process = [i for i in range(len(corpus))
                      if int(corpus["day_ts"].iloc[i]) not in already]
    else:
        # Daily: only the most recent day
        last_ts = int(corpus["day_ts"].iloc[-1])
        to_process = [] if last_ts in already else [len(corpus) - 1]

    if not to_process:
        print("  Nothing new to process. DB is up to date.")
        # Previously returned here with zero trace of the invocation: systemd
        # saw a clean exit but no run_log row was ever written, making a
        # genuine "already up to date" no-op indistinguishable from an
        # upstream corpus refresh that silently hadn't advanced yet before
        # this job ran (the exact ambiguity found on 2026-07-01 and 08-01).
        # Recording status='no_new_data' here closes that gap without
        # touching the normal-path run_log write below, which is unchanged.
        if not args.dry_run:
            latest_day_ts = int(corpus["day_ts"].iloc[-1])
            con.execute("""
                INSERT OR REPLACE INTO run_log
                    (run_ts, data_day_ts, status, signals_computed,
                     corpus_rows_loaded, fn_hash, day_features_hash,
                     anomalies_json, runtime_ms, notes)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (
                now_ms, latest_day_ts, "no_new_data", 0,
                len(corpus), _FN_HASH, None,
                "[]", int((time.time() - t0) * 1000),
                f"mode={mode}; corpus latest day already in corpus_daily",
            ))
            con.commit()
        con.close()
        return

    print(f"  Days to process: {len(to_process)}")

    # Recover current trade state from DB
    active_trade = _get_active_trade(con)
    if active_trade[0] is not None:
        print(f"  Active trade: id={active_trade[0]} "
              f"exit={datetime.fromtimestamp(active_trade[1]/1000, tz=timezone.utc).date()}")
    else:
        print("  Active trade: none")

    # Process days
    processed = skipped = errors = 0
    anomalies: list[str] = []

    for i, idx in enumerate(to_process):
        day_ts = int(corpus["day_ts"].iloc[idx])
        if args.dry_run:
            skipped += 1
            continue
        try:
            active_trade = process_day(con, idx, corpus, active_trade, now_ms)
            processed += 1
            if (i + 1) % 100 == 0:
                con.commit()
                print(f"  ... {i+1}/{len(to_process)} days "
                      f"({datetime.fromtimestamp(day_ts/1000, tz=timezone.utc).date()})",
                      flush=True)
        except Exception as exc:
            errors += 1
            anomalies.append(f"day {day_ts}: {repr(exc)[:120]}")
            print(f"  [ERROR] day {day_ts}: {repr(exc)[:80]}", flush=True)

    if not args.dry_run:
        con.commit()

    elapsed_ms = int((time.time() - t0) * 1000)
    status = "ok" if errors == 0 else "warning"

    # Verify fn_hash hasn't changed
    pre_reg = _HERE / "pre_registration.json"
    expected_fn_hash = None
    if pre_reg.exists():
        reg = json.loads(pre_reg.read_text(encoding="utf-8"))
        expected_fn_hash = reg.get("decision_function_hash", "")
        if expected_fn_hash and _FN_HASH != expected_fn_hash:
            msg = (f"CRITICAL: fn_hash mismatch! "
                   f"Expected {expected_fn_hash[:16]}... "
                   f"Got {_FN_HASH[:16]}...")
            anomalies.append(msg)
            status = "error"
            print(f"\n  [CRITICAL] {msg}")
        else:
            print(f"\n  fn_hash OK: {_FN_HASH[:32]}...")

    # Write run_log
    if not args.dry_run and to_process:
        last_day_ts = int(corpus["day_ts"].iloc[to_process[-1]])
        con.execute("""
            INSERT OR REPLACE INTO run_log
                (run_ts, data_day_ts, status, signals_computed,
                 corpus_rows_loaded, fn_hash, day_features_hash,
                 anomalies_json, runtime_ms, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            now_ms, last_day_ts, status,
            processed, len(corpus), _FN_HASH, None,
            json.dumps(anomalies), elapsed_ms,
            f"mode={mode}",
        ))
        con.commit()

    print(f"\n  processed={processed}  skipped={skipped}  errors={errors}"
          f"  elapsed={elapsed_ms}ms  status={status}")

    # Summary stats
    if not args.dry_run:
        n_trades = con.execute("SELECT COUNT(*) FROM trade_entries").fetchone()[0]
        n_exits  = con.execute("SELECT COUNT(*) FROM trade_exits").fetchone()[0]
        n_fwd    = con.execute(
            "SELECT COUNT(*) FROM trade_entries WHERE entry_day_ts >= ?",
            (PRE_REGISTRATION_DAY_TS,)
        ).fetchone()[0]
        n_labels = con.execute(
            "SELECT COUNT(*) FROM trade_labels WHERE label_available=1"
        ).fetchone()[0]
        print(f"  DB: {n_trades} total trades  {n_exits} closed  "
              f"{n_fwd} forward (post-registration)  {n_labels} labels filled")

    con.close()
    print("=" * 70)


if __name__ == "__main__":
    main()
