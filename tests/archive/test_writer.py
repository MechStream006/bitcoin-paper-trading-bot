"""Archive Writer orchestration tests -- src/archive/writer.py (T7).

Covers: full pipeline success, the not-pending no-op / idempotency case,
failure propagation from each composed component (partition_reader,
parquet_writer, catalog), the intentional fail-closed non-retry behavior
after a partial failure, and construction-time CatalogPort conformance
enforcement.

Run from repo root:
    conda run -n btc_bot pytest tests/archive/test_writer.py -v
"""
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest

from src.archive import content_hash as ch
from src.archive import manifest as mf
from src.archive import parquet_writer as pw
from src.archive import writer as w
from src.archive.catalog_port import CatalogPort
from src.archive.partition_reader import PartitionKey, read_partition
from src.data.storage import init_schema, write_option_quote


# ---------------------------------------------------------------------------
# Hot-DB fixture helpers (mirrors tests/archive/test_partition_reader.py)
# ---------------------------------------------------------------------------

def _make_hot_db(path):
    conn = sqlite3.connect(str(path))
    init_schema(conn)
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
        "INSERT OR IGNORE INTO instrument_map_log (seen_date, id, symbol) VALUES (?, ?, ?)",
        (seen_date, instrument_id, symbol),
    )
    conn.commit()
    conn.close()


def _insert_quote(path, instrument_id, ts, mark_price="100.0"):
    conn = sqlite3.connect(str(path))
    write_option_quote(conn, instrument_id, ts, {"mark_price": mark_price})
    conn.commit()
    conn.close()


def _seeded_hot_db(tmp_path, utc_date="2026-07-01"):
    db = tmp_path / "hot.db"
    _make_hot_db(db)
    _insert_instrument(db, 1, "C-BTC-95000-010726")
    _insert_map_log(db, utc_date, 1, "C-BTC-95000-010726")
    _insert_quote(db, 1, f"{utc_date}T00:00:00Z")
    return db


# ---------------------------------------------------------------------------
# A pure in-memory test double -- same shape as tests/archive/test_catalog_port.py,
# duplicated locally (deliberately: test files should not import each other's
# private fixtures) to drive realistic orchestration-flow tests.
# ---------------------------------------------------------------------------

class _FakeCatalog:
    def __init__(self, pending_keys=()):
        self._pending = set(pending_keys)
        self._exported = {}
        self._verified = set()

    def is_pending(self, partition_key: str) -> bool:
        return partition_key in self._pending

    def mark_exported(self, partition_key: str, manifest) -> None:
        self._pending.discard(partition_key)
        self._exported[partition_key] = manifest

    def mark_verified(self, partition_key: str) -> None:
        self._verified.add(partition_key)


# ---------------------------------------------------------------------------
# Full pipeline success
# ---------------------------------------------------------------------------

class TestFullPipelineSuccess:
    def test_export_partition_succeeds_end_to_end(self, tmp_path):
        db = _seeded_hot_db(tmp_path)
        dest_dir = tmp_path / "cold"
        catalog = _FakeCatalog(pending_keys=["2026-07-01"])
        writer = w.ArchiveWriter(str(db), dest_dir, catalog)

        result = writer.export_partition(PartitionKey("2026-07-01"))

        assert result.exported is True
        assert result.verified is True
        assert result.reason is None
        assert result.row_count == 1
        assert result.partition_key == "2026-07-01"

        assert "2026-07-01" not in catalog._pending
        assert "2026-07-01" in catalog._exported
        assert "2026-07-01" in catalog._verified

        assert (dest_dir / "2026-07-01.parquet").exists()
        assert (dest_dir / "2026-07-01.manifest.json").exists()

    def test_result_content_hash_matches_independent_recomputation(self, tmp_path):
        db = _seeded_hot_db(tmp_path)
        dest_dir = tmp_path / "cold"
        catalog = _FakeCatalog(pending_keys=["2026-07-01"])
        writer = w.ArchiveWriter(str(db), dest_dir, catalog)

        result = writer.export_partition(PartitionKey("2026-07-01"))

        rows = read_partition(str(db), PartitionKey("2026-07-01"))
        assert result.content_hash == ch.content_hash(rows)

        written_manifest = mf.read_manifest(dest_dir / "2026-07-01.manifest.json")
        assert written_manifest.content_hash == result.content_hash

    def test_empty_partition_exports_successfully(self, tmp_path):
        db = tmp_path / "hot.db"
        _make_hot_db(db)  # no rows for this day at all
        dest_dir = tmp_path / "cold"
        catalog = _FakeCatalog(pending_keys=["2026-07-01"])
        writer = w.ArchiveWriter(str(db), dest_dir, catalog)

        result = writer.export_partition(PartitionKey("2026-07-01"))

        assert result.exported and result.verified
        assert result.row_count == 0


# ---------------------------------------------------------------------------
# Not-pending no-op / idempotency
# ---------------------------------------------------------------------------

class TestNotPendingNoOp:
    def test_not_pending_returns_soft_result_no_side_effects(self, tmp_path):
        db = _seeded_hot_db(tmp_path)
        dest_dir = tmp_path / "cold"
        mock_catalog = MagicMock(spec=CatalogPort)
        mock_catalog.is_pending.return_value = False
        writer = w.ArchiveWriter(str(db), dest_dir, mock_catalog)

        result = writer.export_partition(PartitionKey("2026-07-01"))

        assert result.exported is False
        assert result.verified is False
        assert result.reason == "not pending"
        mock_catalog.mark_exported.assert_not_called()
        mock_catalog.mark_verified.assert_not_called()
        assert not dest_dir.exists()  # nothing was ever written

    def test_second_call_after_success_is_idempotent_noop(self, tmp_path):
        db = _seeded_hot_db(tmp_path)
        dest_dir = tmp_path / "cold"
        catalog = _FakeCatalog(pending_keys=["2026-07-01"])
        writer = w.ArchiveWriter(str(db), dest_dir, catalog)

        first = writer.export_partition(PartitionKey("2026-07-01"))
        assert first.exported is True

        parquet_bytes_before = (dest_dir / "2026-07-01.parquet").read_bytes()
        second = writer.export_partition(PartitionKey("2026-07-01"))

        assert second.exported is False
        assert second.reason == "not pending"
        assert (dest_dir / "2026-07-01.parquet").read_bytes() == parquet_bytes_before


# ---------------------------------------------------------------------------
# Failure propagation
# ---------------------------------------------------------------------------

class TestFailurePropagation:
    def test_missing_hot_db_propagates_filenotfounderror(self, tmp_path):
        mock_catalog = MagicMock(spec=CatalogPort)
        mock_catalog.is_pending.return_value = True
        writer = w.ArchiveWriter(str(tmp_path / "ghost.db"), tmp_path / "cold", mock_catalog)

        with pytest.raises(FileNotFoundError):
            writer.export_partition(PartitionKey("2026-07-01"))

        mock_catalog.mark_exported.assert_not_called()
        mock_catalog.mark_verified.assert_not_called()

    def test_parquet_writer_failure_propagates_before_mark_exported(self, tmp_path, monkeypatch):
        db = _seeded_hot_db(tmp_path)
        mock_catalog = MagicMock(spec=CatalogPort)
        mock_catalog.is_pending.return_value = True
        writer = w.ArchiveWriter(str(db), tmp_path / "cold", mock_catalog)

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated parquet failure")

        monkeypatch.setattr(w, "write_partition_parquet", _boom)

        with pytest.raises(RuntimeError):
            writer.export_partition(PartitionKey("2026-07-01"))

        mock_catalog.mark_exported.assert_not_called()
        mock_catalog.mark_verified.assert_not_called()

    def test_catalog_mark_exported_failure_propagates_and_verify_not_reached(self, tmp_path):
        db = _seeded_hot_db(tmp_path)
        dest_dir = tmp_path / "cold"
        mock_catalog = MagicMock(spec=CatalogPort)
        mock_catalog.is_pending.return_value = True
        mock_catalog.mark_exported.side_effect = RuntimeError("simulated catalog failure")
        writer = w.ArchiveWriter(str(db), dest_dir, mock_catalog)

        with pytest.raises(RuntimeError):
            writer.export_partition(PartitionKey("2026-07-01"))

        mock_catalog.mark_verified.assert_not_called()
        # Writes already happened before the catalog call -- artifacts exist.
        assert (dest_dir / "2026-07-01.parquet").exists()
        assert (dest_dir / "2026-07-01.manifest.json").exists()

    def test_retry_after_partial_failure_raises_fileexists_not_silently_recovered(self, tmp_path):
        db = _seeded_hot_db(tmp_path)
        dest_dir = tmp_path / "cold"
        mock_catalog = MagicMock(spec=CatalogPort)
        mock_catalog.is_pending.return_value = True  # backend never recorded EXPORTED
        mock_catalog.mark_exported.side_effect = RuntimeError("simulated catalog failure")
        writer = w.ArchiveWriter(str(db), dest_dir, mock_catalog)

        with pytest.raises(RuntimeError):
            writer.export_partition(PartitionKey("2026-07-01"))

        # Retry: catalog still reports PENDING, but the artifact from the
        # first attempt is still on disk -- this must fail closed, not
        # silently overwrite or silently "resume".
        with pytest.raises(FileExistsError):
            writer.export_partition(PartitionKey("2026-07-01"))


# ---------------------------------------------------------------------------
# Construction-time CatalogPort conformance
# ---------------------------------------------------------------------------

class TestConstructorEnforcesCatalogPort:
    def test_non_conformant_catalog_rejected_at_construction(self, tmp_path):
        with pytest.raises(TypeError):
            w.ArchiveWriter(str(tmp_path / "hot.db"), tmp_path / "cold", object())
