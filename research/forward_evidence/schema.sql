-- Phase E Forward Evidence Collection Schema
-- Pre-registered: 2026-06-10
-- Purpose: collect trustworthy forward evidence on frozen H6; support attribution,
--          MAE/MFE tracking, forward labels, and promotion decisions.
-- All day_ts values are UTC epoch milliseconds aligned to 00:00:00 UTC.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ============================================================================
-- Table 1: corpus_daily
-- Raw daily market inputs from Binance (canonical data source).
-- One row per calendar day. Populated by the daily evidence job (M2).
-- ============================================================================
CREATE TABLE IF NOT EXISTS corpus_daily (
    day_ts              INTEGER PRIMARY KEY,  -- UTC epoch ms, midnight-aligned
    spot_close          REAL    NOT NULL,
    spot_open           REAL,
    spot_high           REAL,
    spot_low            REAL,
    spot_volume         REAL,
    funding_bps         REAL,                 -- daily mean funding rate × 10000; null if <2 readings
    perp_close          REAL,                 -- Binance USDT-perp close
    basis_bps           REAL,                 -- (perp/spot - 1) × 10000
    dvol_close          REAL,                 -- Deribit DVOL (vol-points); null before 2021-03-24
    rv_21d              REAL,                 -- 21d realized vol annualized
    atr14               REAL,                 -- ATR(14)
    atr_ratio           REAL,                 -- atr14 / atr14[t-10] - 1
    strata_funding_bps  REAL,                 -- StrataTrade funding bps if accessible; null otherwise
    ingested_at         INTEGER NOT NULL      -- epoch ms when this row was written
);

-- ============================================================================
-- Table 2: regime_states
-- Daily regime labels computed from corpus_daily using the frozen parameters.
-- One row per calendar day. Written by the daily job after corpus_daily.
-- ============================================================================
CREATE TABLE IF NOT EXISTS regime_states (
    day_ts                  INTEGER PRIMARY KEY REFERENCES corpus_daily(day_ts),
    vol_regime              TEXT    NOT NULL,  -- 'low'/'medium'/'high'/'unknown'
    funding_regime          TEXT    NOT NULL,  -- 'low'/'medium'/'high'/'unknown'
    market_regime           TEXT    NOT NULL,  -- 'bull'/'bear'/'sideways'/'unknown'
    sma200                  REAL,
    trend_dist              REAL,              -- (close - sma200) / sma200
    vrp_spread              REAL,              -- dvol_frac - rv30; null if DVOL unavailable
    strata_funding_regime   TEXT,              -- shadow regime from StrataTrade; null if unavailable
    regime_diverges         INTEGER            -- 1 if canonical != shadow funding regime; else 0; null if no shadow
);

-- ============================================================================
-- Table 3: signal_log
-- Daily H6 decision record. One row per calendar day.
-- h6_canonical is the authoritative signal; h6_shadow is for divergence tracking only.
-- ============================================================================
CREATE TABLE IF NOT EXISTS signal_log (
    day_ts              INTEGER PRIMARY KEY REFERENCES regime_states(day_ts),
    h6_canonical        INTEGER NOT NULL,     -- 0 or 1
    decision_hash       TEXT    NOT NULL,     -- SHA-256 of {day_ts|vol_r|fund_r|mkt_r|VERSION}
    h6_shadow           INTEGER,              -- shadow signal; null if StrataTrade data unavailable
    in_hold             INTEGER NOT NULL DEFAULT 0,  -- 1 if an active trade is open today
    active_trade_id     INTEGER              -- FK to trade_entries if in_hold = 1; null otherwise
);

-- ============================================================================
-- Table 4: trade_entries
-- One row per H6 trade entry. Written when h6_canonical flips to 1 outside
-- an existing hold (non-overlapping 7d rule).
-- ============================================================================
CREATE TABLE IF NOT EXISTS trade_entries (
    trade_id                INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_day_ts            INTEGER NOT NULL REFERENCES signal_log(day_ts),
    entry_price             REAL    NOT NULL,  -- spot_close at entry day
    direction               INTEGER NOT NULL DEFAULT 1,  -- always 1 (long) for H6
    planned_exit_day_ts     INTEGER NOT NULL,  -- entry_day_ts + 7 × 86400000
    decision_hash           TEXT    NOT NULL,
    vol_regime_entry        TEXT    NOT NULL,
    funding_regime_entry    TEXT    NOT NULL,
    market_regime_entry     TEXT    NOT NULL,
    funding_bps_entry       REAL,
    rv_21d_entry            REAL,
    atr_ratio_entry         REAL,
    vrp_spread_entry        REAL,              -- null if DVOL unavailable
    created_at              INTEGER NOT NULL
);

-- ============================================================================
-- Table 5: trade_exits
-- One row per completed trade (matched to trade_entries via trade_id).
-- Written when the hold expires (or early stop fires, if ever enabled).
-- ============================================================================
CREATE TABLE IF NOT EXISTS trade_exits (
    trade_id        INTEGER PRIMARY KEY REFERENCES trade_entries(trade_id),
    exit_day_ts     INTEGER NOT NULL,
    exit_price      REAL    NOT NULL,  -- spot_close at exit day
    exit_reason     TEXT    NOT NULL,  -- 'scheduled' (only option in Phase E demo)
    gross_return    REAL    NOT NULL,  -- exit_price / entry_price - 1
    cost_bps        REAL    NOT NULL DEFAULT 15.0,
    net_return      REAL    NOT NULL,  -- gross_return - cost_bps / 10000
    created_at      INTEGER NOT NULL
);

-- ============================================================================
-- Table 6: trade_labels
-- Forward returns at multiple horizons. One row per (trade, horizon).
-- Rows are inserted at entry with forward_return = null; filled in as each
-- horizon day arrives. Enables multi-horizon learning curves.
-- ============================================================================
CREATE TABLE IF NOT EXISTS trade_labels (
    trade_id        INTEGER NOT NULL REFERENCES trade_entries(trade_id),
    horizon_days    INTEGER NOT NULL,   -- 5, 7, 14, or 30
    label_day_ts    INTEGER NOT NULL,   -- entry_day_ts + horizon_days × 86400000
    forward_return  REAL,               -- null until label_day arrives
    label_available INTEGER NOT NULL DEFAULT 0,
    labeled_at      INTEGER,            -- epoch ms when null → filled
    PRIMARY KEY (trade_id, horizon_days)
);

-- ============================================================================
-- Table 7: mae_mfe_path
-- Daily close + cumulative MAE/MFE for every day of each open hold.
-- One row per (trade_id, day_ts) for days 1 through 7 (and beyond if extended).
-- Enables hold-path analysis and entry-timing study.
-- ============================================================================
CREATE TABLE IF NOT EXISTS mae_mfe_path (
    trade_id        INTEGER NOT NULL REFERENCES trade_entries(trade_id),
    day_ts          INTEGER NOT NULL,
    day_in_hold     INTEGER NOT NULL,   -- 1-based; day 1 is entry_day_ts + 1d
    spot_close      REAL    NOT NULL,
    cum_return      REAL    NOT NULL,   -- spot_close / entry_price - 1
    mae_so_far      REAL    NOT NULL,   -- min cum_return so far (most negative)
    mfe_so_far      REAL    NOT NULL,   -- max cum_return so far (most positive)
    vol_regime      TEXT,
    funding_regime  TEXT,
    market_regime   TEXT,
    PRIMARY KEY (trade_id, day_ts)
);

-- ============================================================================
-- Table 8: attribution
-- Per-trade return decomposition. Written at trade exit.
-- Separates market beta, carry (funding), and signal alpha.
-- ============================================================================
CREATE TABLE IF NOT EXISTS attribution (
    trade_id                INTEGER PRIMARY KEY REFERENCES trade_exits(trade_id),
    market_return           REAL,   -- BTC buy-and-hold over the same 7d window
    carry_return            REAL,   -- cumulative funding bps over hold (as fraction)
    signal_alpha            REAL,   -- net_return - market_return
    vol_regime_stable       INTEGER,  -- 1 if vol_regime unchanged for all hold days
    funding_regime_stable   INTEGER,
    market_regime_stable    INTEGER,
    any_hold_day_bear       INTEGER,  -- 1 if market_regime == 'bear' on any hold day
    created_at              INTEGER NOT NULL
);

-- ============================================================================
-- Table 9: run_log
-- Daily job execution record. One row per run (one per day).
-- Supports determinism audit: fn_hash must match the pre-registered hash.
-- Anomalies are logged as a JSON array of strings.
-- ============================================================================
CREATE TABLE IF NOT EXISTS run_log (
    run_ts              INTEGER PRIMARY KEY,   -- epoch ms when job started
    data_day_ts         INTEGER NOT NULL,      -- calendar day being processed
    status              TEXT    NOT NULL,      -- 'ok' / 'warning' / 'error'
    signals_computed    INTEGER,
    corpus_rows_loaded  INTEGER,
    fn_hash             TEXT,                  -- SHA-256 of decision_function.py
    day_features_hash   TEXT,                  -- SHA-256 of this day's feature vector
    anomalies_json      TEXT,                  -- JSON array; '[]' if clean
    runtime_ms          INTEGER,
    notes               TEXT
);

-- ============================================================================
-- Indexes
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_trade_entries_entry_day  ON trade_entries(entry_day_ts);
CREATE INDEX IF NOT EXISTS idx_trade_labels_label_day   ON trade_labels(label_day_ts);
CREATE INDEX IF NOT EXISTS idx_mae_mfe_trade            ON mae_mfe_path(trade_id);
CREATE INDEX IF NOT EXISTS idx_signal_log_canonical     ON signal_log(h6_canonical);
CREATE INDEX IF NOT EXISTS idx_run_log_data_day         ON run_log(data_day_ts);
