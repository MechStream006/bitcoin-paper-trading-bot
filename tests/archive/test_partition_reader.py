"""Read-only partition-reader tests -- src/archive/partition_reader.py (T3).

Covers: read-only guarantees (no file creation on a missing path), empty
vs populated partitions, day-boundary correctness (no cross-day leakage),
the two-tier instrument_id -> symbol resolution (instrument_map_log
preferred, live instruments as fallback), and the fail-closed error when
neither source resolves a symbol.

Run from repo root:
    conda run -n btc_bot pytest tests/archive/test_partition_reader.py -v
"""
from __future__ import annotations

import sqlite3

import pytest

from src.archive import content_hash as ch
from src.archive.partition_reader import PartitionKey, read_partition
from src.data.storage import init_schema, write_option_quote


def _make_db(path):
    conn = sqlite3.connect(str(path))
    init_schema(conn)
    conn.commit()
    conn.close()


def _insert_quote(path, instrument_id, ts, mark_price="100.0"):
    conn = sqlite3.connect(str(path))
    write_option_quote(conn, instrument_id, ts, {"mark_price": mark_price})
    conn.commit()
    conn.close()


def _insert_instrument(path, instrument_id, symbol):
    conn = sqlite3.connect(str(path))
    conn.execute(
        "INSERT OR REPLACE INTO instruments "
        "(id, symbol, contract_type, fetched_at) VALUES (?, ?, 'call_options', ?)",
        (instrument_id, symbol, "2026-07-01T00:00:00Z"),
    )
    conn.commit()
    conn.close()


def _insert_map_log(path, seen_date, instrument_id, symbol):
    conn = sqlite3.connect(str(path))
    conn.execute(
        "INSERT OR IGNORE INTO instrument_map_log (seen_date, id, symbol) "
        "VALUES (?, ?, ?)",
        (seen_date, instrument_id, symbol),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Read-only guarantee
# ---------------------------------------------------------------------------

class TestReadOnlyGuarantee:
    def test_missing_db_raises_and_never_creates_file(self, tmp_path):
        ghost = tmp_path / "ghost.db"
        with pytest.raises(FileNotFoundError):
            read_partition(str(ghost), PartitionKey(utc_date="2026-07-01"))
        assert not ghost.exists()

    def test_open_connection_is_actually_read_only(self, tmp_path):
        # Proves mode=ro is really enforced, not just documented intent.
        import pathlib
        db = tmp_path / "ev.db"
        _make_db(db)
        uri = pathlib.Path(db).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            with pytest.raises(sqlite3.OperationalError):
                conn.execute("INSERT INTO instruments (id, symbol, contract_type, fetched_at) "
                             "VALUES (1, 'X', 'call_options', 't')")
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Partition selection
# ---------------------------------------------------------------------------

class TestPartitionSelection:
    def test_empty_partition_returns_empty_list(self, tmp_path):
        db = tmp_path / "ev.db"
        _make_db(db)
        assert read_partition(str(db), PartitionKey(utc_date="2026-07-01")) == []

    def test_only_matching_day_rows_are_returned(self, tmp_path):
        db = tmp_path / "ev.db"
        _make_db(db)
        _insert_instrument(db, 1, "C-BTC-95000-010726")
        _insert_map_log(db, "2026-07-01", 1, "C-BTC-95000-010726")
        _insert_quote(db, 1, "2026-06-30T23:59:59Z")   # previous day
        _insert_quote(db, 1, "2026-07-01T00:00:00Z")   # target day, boundary
        _insert_quote(db, 1, "2026-07-01T23:59:59Z")   # target day, boundary
        _insert_quote(db, 1, "2026-07-02T00:00:00Z")   # next day

        rows = read_partition(str(db), PartitionKey(utc_date="2026-07-01"))
        assert {r["ts"] for r in rows} == {"2026-07-01T00:00:00Z", "2026-07-01T23:59:59Z"}

    def test_rows_encode_cleanly_through_content_hash(self, tmp_path):
        db = tmp_path / "ev.db"
        _make_db(db)
        _insert_instrument(db, 1, "C-BTC-95000-010726")
        _insert_map_log(db, "2026-07-01", 1, "C-BTC-95000-010726")
        _insert_quote(db, 1, "2026-07-01T00:00:00Z")

        rows = read_partition(str(db), PartitionKey(utc_date="2026-07-01"))
        h = ch.content_hash(rows)   # must not raise
        assert h.startswith("sha256:")


# ---------------------------------------------------------------------------
# Symbol resolution
# ---------------------------------------------------------------------------

class TestSymbolResolution:
    def test_map_log_preferred_over_live_instruments(self, tmp_path):
        db = tmp_path / "ev.db"
        _make_db(db)
        # Live table has a DIFFERENT (e.g. reused/updated) symbol than the
        # day-accurate snapshot -- map_log for that seen_date must win.
        _insert_instrument(db, 1, "STALE-LIVE-SYMBOL")
        _insert_map_log(db, "2026-07-01", 1, "C-BTC-95000-010726")
        _insert_quote(db, 1, "2026-07-01T00:00:00Z")

        rows = read_partition(str(db), PartitionKey(utc_date="2026-07-01"))
        assert rows[0]["symbol"] == "C-BTC-95000-010726"

    def test_falls_back_to_live_instruments_when_map_log_absent(self, tmp_path):
        db = tmp_path / "ev.db"
        _make_db(db)
        _insert_instrument(db, 1, "C-BTC-95000-010726")
        # No instrument_map_log row for this seen_date at all.
        _insert_quote(db, 1, "2026-07-01T00:00:00Z")

        rows = read_partition(str(db), PartitionKey(utc_date="2026-07-01"))
        assert rows[0]["symbol"] == "C-BTC-95000-010726"

    def test_unresolvable_symbol_raises(self, tmp_path):
        db = tmp_path / "ev.db"
        _make_db(db)
        # instrument_id 999 in neither instruments nor instrument_map_log.
        _insert_quote(db, 999, "2026-07-01T00:00:00Z")

        with pytest.raises(ValueError, match="999"):
            read_partition(str(db), PartitionKey(utc_date="2026-07-01"))
