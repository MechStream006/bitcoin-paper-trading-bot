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
    cost_bps            REAL    NOT NULL
                            CHECK (cost_bps >= 0), -- NO DEFAULT; explicit insert required.
                                                   -- Gate 6B.6 rider (from 6B.5): a negative
                                                   -- cost was silently accepted and produced a
                                                   -- manufactured profit (cost_bps = -9999 gave
                                                   -- net_return = +0.9999). Zero stays valid.
                                                   -- Bounds only -- the registered per-trade
                                                   -- cost CONVENTION (label spec 57ce016e51a0)
                                                   -- is unchanged.
                                                   -- NOTE: CREATE TABLE IF NOT EXISTS means this
                                                   -- CHECK reaches NEW databases only; SQLite
                                                   -- cannot ALTER a CHECK onto the existing
                                                   -- production table without a full rebuild, so
                                                   -- run_daily also rejects a negative cost at
                                                   -- config-read time.
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


-- ---------------------------------------------------------------------------
-- collection_provenance (Gate 6B.3, defect B-03)
--
-- Records HOW each run_date came to be collected. Before 6B.3 an authorized
-- historical backfill and a scheduled observation were byte-for-byte
-- indistinguishable in run_log/signal_log, so a mistyped or deliberate
-- backfill entered the permanent evidence record with no trace.
--
-- Written by run_daily.main() only -- never by runner_base -- so the shared
-- collection path used by all six signals is untouched. Additive: no existing
-- table or view is modified, and rows collected before this table existed stay
-- valid (their provenance is simply unrecorded, which is itself informative:
-- absence means "collected before 6B.3", not "scheduled").
--
-- INSERT OR IGNORE against the UNIQUE key makes re-invocation idempotent, so a
-- repeated scheduled run or an idempotent skip does not accumulate rows.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS collection_provenance (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date    TEXT    NOT NULL,          -- 'YYYY-MM-DD' UTC
    mode        TEXT    NOT NULL
                    CHECK (mode IN ('scheduled', 'backfill_authorized')),
    detail      TEXT,                      -- policy decision detail
    invoked_at  TEXT    NOT NULL
                    DEFAULT (STRFTIME('%Y-%m-%dT%H:%M:%SZ', 'NOW')),
    UNIQUE (run_date, mode)
);


-- ---------------------------------------------------------------------------
-- run_provenance / run_inputs (Gate 6B.6, defects B-04 and B-04a)
--
-- B-04a: run_log.fn_hash identifies only the decision-function file. The
-- COLLECTION FRAMEWORK is invisible to it -- runner_base.py changed at 6B.1
-- (bar-count + NaN guards) and run_daily.py at 6B.3 (run_date policy), both
-- changing WHAT gets collected, yet fn_hash is byte-identical across those
-- changes. framework_hash closes that: a changed framework cannot present the
-- previous identity.
--
-- B-04: nothing recorded WHICH bytes produced an observation. The live ingest
-- writer rewrites whole monthly partitions (storage/lake.py write_partition:
-- "overwrites any existing file"), so the input file mutates by design after
-- collection. run_inputs pins dataset + partition + parquet_sha256 + rows at
-- collection time, making a later divergence detectable by re-hashing.
--
-- Both are written INSIDE run_signal's existing transaction, so provenance
-- commits atomically with the observation it describes: no observation can
-- exist without its provenance, and a rolled-back collection leaves neither.
--
-- Deliberately NEW tables, not columns on run_log: adding columns would need
-- ALTER TABLE inside init_db, which is registered hazard EH-001. It also keeps
-- requirement 4 true by construction -- rows collected before this gate simply
-- have no row here, which reads as "never recorded", not as a fabricated
-- retroactive claim.
--
-- parquet_sha256 is a RAW chunked SHA-256 (storage/manifest.py _sha256), NOT
-- pre_reg_guard._sha256_lf: that one normalises CRLF->LF, which is correct for
-- source text and would corrupt the digest of a binary Parquet file.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS run_provenance (
    run_id          INTEGER PRIMARY KEY REFERENCES run_log(id),  -- 1:1, idempotent
    framework_hash  TEXT    NOT NULL,   -- content hash of the collection framework
    git_sha         TEXT,               -- deployed commit; best-effort, may be NULL
    recorded_at     TEXT    NOT NULL
                        DEFAULT (STRFTIME('%Y-%m-%dT%H:%M:%SZ', 'NOW'))
);

CREATE TABLE IF NOT EXISTS run_inputs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES run_log(id),
    role            TEXT    NOT NULL,   -- 'price' | 'aux' | 'ohlcv'
    dataset         TEXT    NOT NULL,   -- e.g. 'spot/BTCUSDT'
    partition       TEXT    NOT NULL,   -- e.g. 'year=2026/month=08'
    parquet_sha256  TEXT    NOT NULL,   -- raw SHA-256 of the partition file
    rows            INTEGER NOT NULL,   -- row count read from that partition
    UNIQUE (run_id, dataset, partition)
);
