"""Deterministic Parquet writer tests -- src/archive/parquet_writer.py (T4).

Covers: schema correctness (column order + explicit physical types),
embedded metadata correctness, canonical row ordering, deterministic
output across repeated writes, atomic tmp -> fsync -> rename behavior,
temporary-file cleanup on failure, overwrite protection, parent-directory
creation, and fail-closed behavior on malformed input.

Run from repo root:
    conda run -n btc_bot pytest tests/archive/test_parquet_writer.py -v
"""
from __future__ import annotations

import os

import pyarrow.parquet as pq
import pytest

from src.archive import content_hash as ch
from src.archive import golden_vectors as gv
from src.archive import parquet_writer as pw
from src.archive.partition_reader import PartitionKey


def _tmp_files(dest_dir):
    return [p for p in dest_dir.iterdir() if p.name.endswith(".tmp")]


# ---------------------------------------------------------------------------
# Schema correctness
# ---------------------------------------------------------------------------

class TestSchema:
    def test_column_order_matches_content_hash(self, tmp_path):
        path = pw.write_partition_parquet(gv.GV_1_ROWS, tmp_path, PartitionKey("2026-07-01"))
        schema = pq.read_schema(str(path))
        assert schema.names == list(ch.COLUMN_ORDER)

    def test_explicit_physical_types(self, tmp_path):
        path = pw.write_partition_parquet(gv.GV_1_ROWS, tmp_path, PartitionKey("2026-07-01"))
        schema = pq.read_schema(str(path))
        assert schema.field("symbol").type == "string"
        assert schema.field("instrument_id").type == "int64"
        assert schema.field("ts").type == "string"
        assert schema.field("exch_ts").type == "int64"
        # every remaining column defaults to float64 (DOUBLE), per SI-7
        floaty = [c for c in ch.COLUMN_ORDER
                  if c not in ("symbol", "instrument_id", "ts", "exch_ts")]
        for column in floaty:
            assert schema.field(column).type == "double", column

    def test_empty_partition_is_valid_zero_row_file(self, tmp_path):
        path = pw.write_partition_parquet(gv.GV_0_ROWS, tmp_path, PartitionKey("2026-07-01"))
        table = pq.read_table(str(path))
        assert table.num_rows == 0
        assert table.schema.names == list(ch.COLUMN_ORDER)


# ---------------------------------------------------------------------------
# Embedded metadata
# ---------------------------------------------------------------------------

class TestMetadata:
    def test_metadata_matches_content_hash_and_row_count(self, tmp_path):
        key = PartitionKey("2026-07-01")
        path = pw.write_partition_parquet(gv.GV_2_ROWS, tmp_path, key)
        schema = pq.read_schema(str(path))
        meta = schema.metadata
        assert meta[b"hash_spec_version"] == b"1"
        assert meta[b"content_hash"].decode() == ch.content_hash(gv.GV_2_ROWS)
        assert meta[b"row_count"] == b"2"
        assert meta[b"partition_key"] == b"2026-07-01"

    def test_metadata_no_wallclock_or_environment_fields(self, tmp_path):
        path = pw.write_partition_parquet(gv.GV_1_ROWS, tmp_path, PartitionKey("2026-07-01"))
        meta = pq.read_schema(str(path)).metadata
        assert set(meta.keys()) == {
            b"hash_spec_version", b"content_hash", b"row_count", b"partition_key"
        }


# ---------------------------------------------------------------------------
# Row ordering
# ---------------------------------------------------------------------------

class TestRowOrdering:
    def test_rows_written_in_canonical_order(self, tmp_path):
        # GV_2_ROWS is fed as [P-..., C-...] -- non-canonical input order.
        path = pw.write_partition_parquet(gv.GV_2_ROWS, tmp_path, PartitionKey("2026-07-01"))
        table = pq.read_table(str(path))
        symbols = table.column("symbol").to_pylist()
        assert symbols == sorted(symbols)
        assert symbols[0] == gv.GV_2_ROW_B["symbol"]  # C-... sorts before P-...
        assert symbols[1] == gv.GV_2_ROW_A["symbol"]

    def test_reversed_input_order_yields_identical_row_order(self, tmp_path):
        d1, d2 = tmp_path / "a", tmp_path / "b"
        p1 = pw.write_partition_parquet(gv.GV_2_ROWS, d1, PartitionKey("2026-07-01"))
        p2 = pw.write_partition_parquet(list(reversed(gv.GV_2_ROWS)), d2, PartitionKey("2026-07-01"))
        t1, t2 = pq.read_table(str(p1)), pq.read_table(str(p2))
        assert t1.column("symbol").to_pylist() == t2.column("symbol").to_pylist()
        assert t1.column("instrument_id").to_pylist() == t2.column("instrument_id").to_pylist()


# ---------------------------------------------------------------------------
# Determinism across repeated writes
# ---------------------------------------------------------------------------

class TestDeterministicOutput:
    def test_repeated_writes_are_byte_identical(self, tmp_path):
        d1, d2 = tmp_path / "a", tmp_path / "b"
        p1 = pw.write_partition_parquet(gv.GV_1_ROWS, d1, PartitionKey("2026-07-01"))
        p2 = pw.write_partition_parquet(gv.GV_1_ROWS, d2, PartitionKey("2026-07-01"))
        assert p1.read_bytes() == p2.read_bytes()

    def test_compression_codec_is_snappy(self, tmp_path):
        path = pw.write_partition_parquet(gv.GV_1_ROWS, tmp_path, PartitionKey("2026-07-01"))
        pf = pq.ParquetFile(str(path))
        col_meta = pf.metadata.row_group(0).column(0)
        assert col_meta.compression == "SNAPPY"


# ---------------------------------------------------------------------------
# Atomicity / temp-file cleanup / failure handling
# ---------------------------------------------------------------------------

class TestAtomicityAndCleanup:
    def test_no_tmp_files_left_after_success(self, tmp_path):
        pw.write_partition_parquet(gv.GV_1_ROWS, tmp_path, PartitionKey("2026-07-01"))
        assert _tmp_files(tmp_path) == []

    def test_failure_during_write_leaves_no_tmp_or_final_file(self, tmp_path, monkeypatch):
        def _boom(*args, **kwargs):
            raise RuntimeError("simulated write failure")

        monkeypatch.setattr(pw.pq, "write_table", _boom)
        with pytest.raises(RuntimeError):
            pw.write_partition_parquet(gv.GV_1_ROWS, tmp_path, PartitionKey("2026-07-01"))

        assert _tmp_files(tmp_path) == []
        assert not (tmp_path / "2026-07-01.parquet").exists()

    def test_malformed_row_raises_before_any_file_is_created(self, tmp_path):
        malformed = [{"symbol": "A-X"}]  # missing ts/instrument_id
        with pytest.raises(KeyError):
            pw.write_partition_parquet(malformed, tmp_path, PartitionKey("2026-07-01"))
        assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# Overwrite protection
# ---------------------------------------------------------------------------

class TestOverwriteProtection:
    def test_second_write_to_same_partition_raises(self, tmp_path):
        key = PartitionKey("2026-07-01")
        path = pw.write_partition_parquet(gv.GV_1_ROWS, tmp_path, key)
        original_bytes = path.read_bytes()

        with pytest.raises(FileExistsError):
            pw.write_partition_parquet(gv.GV_2_ROWS, tmp_path, key)

        assert path.read_bytes() == original_bytes  # untouched


# ---------------------------------------------------------------------------
# Parent directory creation
# ---------------------------------------------------------------------------

class TestParentDirectoryCreation:
    def test_nested_dest_dir_is_created(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c"
        assert not nested.exists()
        path = pw.write_partition_parquet(gv.GV_1_ROWS, nested, PartitionKey("2026-07-01"))
        assert path.exists()
        assert path.parent == nested
