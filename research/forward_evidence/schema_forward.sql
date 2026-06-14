-- schema_forward.sql
-- Evidence forward collection schema — multi-signal, v1
-- Database: research/data/evidence_forward.db
--
-- This is a SEPARATE database from H6's forward_evidence.db (H6 unchanged).
--
-- Initialise via init_forward_db.py (sets pragmas and runs this file).
--
-- ── Required per-connection setup (NOT persisted — runners must set on every connect) ──
--     PRAGMA foreign_keys = ON;
--
-- ── Persisted at init (stored in DB header after first set) ──
--     PRAGMA journal_mode = WAL;   ← set programmatically in init_forward_db.py
--
-- ── Hashing convention (all fn_hash values) ──
--     SHA-256 of LF-normalised bytes (raw.replace(b'\r\n', b'\n'))
--     Matches pre_reg_guard._sha256_lf()
--
-- ── Design decisions ──
--     - No signals table: registration records live in git-tracked pre-registration JSONs
--     - No mae_mfe_path table: deferred until a specific intra-trade research question requires it
--     - No fn_hash in child tables: fn_hash lives in run_log only; child rows link via run_id FK
--     - No stored gross_return / net_return: derived at query time in trade_returns view
--     - direction NOT NULL CHECK(direction IN (-1,1)) with NO DEFAULT: explicit insert required
--     - cost_bps NOT NULL with NO DEFAULT: explicit insert required
--     - signal_id NOT in trade_exits: normalised; join to trade_entries via entry_id
--     - created_at in run_log only: child rows are written in the same transaction;
--       their wall-clock times are redundant

-- ─────────────────────────────────────────────────────────────────────────────
-- run_log
-- One row per (signal_id, run_date).
-- fn_hash for the entire day's collection lives here — not in child tables.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS run_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id   TEXT    NOT NULL,
    run_date    TEXT    NOT NULL,   -- 'YYYY-MM-DD' UTC logical collection date
    fn_hash     TEXT    NOT NULL,   -- actual SHA-256 of decision function file at run time
    status      TEXT    NOT NULL DEFAULT 'ok'
                    CHECK (status IN ('ok', 'skipped', 'error', 'fn_hash_violation')),
    note        TEXT,               -- required detail when status != 'ok'
    created_at  TEXT    NOT NULL
                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (signal_id, run_date)
    -- CRASH/RECOVERY: all writes for a given day are wrapped in a single
    -- BEGIN/COMMIT transaction. A crash before commit leaves no row here;
    -- retries start clean. A fn_hash_violation row is a permanent audit
    -- record — same-day recovery requires deleting it explicitly.
);

-- ─────────────────────────────────────────────────────────────────────────────
-- signal_log
-- One row per (signal_id, day) — daily decision function output.
-- run_id FK enables fn_hash traceability without per-row hash storage.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS signal_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id   TEXT    NOT NULL,
    day         TEXT    NOT NULL,   -- 'YYYY-MM-DD' UTC (equals run_log.run_date for normal runs)
    position    REAL    NOT NULL,   -- compute_signal() output: -1.0, 0.0, or +1.0
    raw_value   REAL,               -- signal-specific intermediate float (nullable):
                                    --   G3: 30-day return; H5: RV21 annualised; G1: SMA slope delta
    run_id      INTEGER NOT NULL REFERENCES run_log(id),
    UNIQUE (signal_id, day)
);

-- ─────────────────────────────────────────────────────────────────────────────
-- trade_entries
-- One row per opened trade (one per hold period per signal).
-- NO UNIQUE on (signal_id, entry_date): allows same-day exit-and-re-entry,
-- which is required for G1 long/short direction flips at hold boundaries.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trade_entries (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id           TEXT    NOT NULL,
    entry_date          TEXT    NOT NULL,          -- 'YYYY-MM-DD' UTC
    direction           INTEGER NOT NULL
                            CHECK (direction IN (-1, 1)),  -- NO DEFAULT; explicit insert required
    entry_close_price   REAL,                      -- nullable: defensive for recovery / backfill runs
    planned_exit_date   TEXT    NOT NULL,          -- 'YYYY-MM-DD' UTC = entry_date + hold_days
    open                INTEGER NOT NULL DEFAULT 1
                            CHECK (open IN (0, 1)) -- 1 = open, 0 = closed by trade_exits
);

-- ─────────────────────────────────────────────────────────────────────────────
-- trade_exits
-- One row per closed trade. Raw prices only — returns computed in view.
-- signal_id is NOT stored here: always obtain via JOIN to trade_entries.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trade_exits (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id            INTEGER NOT NULL REFERENCES trade_entries(id),
    exit_date           TEXT    NOT NULL,          -- 'YYYY-MM-DD' UTC
    exit_close_price    REAL    NOT NULL,          -- always available (job runs after close)
    cost_bps            REAL    NOT NULL,          -- NO DEFAULT; explicit insert required
    UNIQUE (entry_id)                              -- one exit per entry
);

-- ─────────────────────────────────────────────────────────────────────────────
-- trade_returns (VIEW)
-- Primary analysis surface. Computes all return metrics at query time.
-- signal_id comes from trade_entries (normalised — no denormalised copy in exits).
-- ─────────────────────────────────────────────────────────────────────────────
CREATE VIEW IF NOT EXISTS trade_returns AS
SELECT
    te.id                   AS entry_id,
    te.signal_id,
    te.entry_date,
    te.direction,
    te.entry_close_price,
    tx.exit_date,
    tx.exit_close_price,
    tx.cost_bps,
    -- direction-adjusted gross return
    (tx.exit_close_price / te.entry_close_price - 1.0)
        * te.direction                                          AS gross_return,
    -- net return after transaction cost
    (tx.exit_close_price / te.entry_close_price - 1.0)
        * te.direction - tx.cost_bps / 10000.0                 AS net_return,
    -- actual hold duration in calendar days
    CAST(
        JULIANDAY(tx.exit_date) - JULIANDAY(te.entry_date)
    AS INTEGER)                                                 AS hold_days_actual
FROM trade_entries te
JOIN trade_exits   tx ON tx.entry_id = te.id;
