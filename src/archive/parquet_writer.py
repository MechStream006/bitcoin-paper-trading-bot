"""Deterministic Parquet archive writer (M1.2).

Writes one partition's canonically-ordered rows to an immutable Parquet
file: a fixed schema (column order + explicit physical types matching
content_hash.py's canonicalization contract), a fixed compression codec,
and an atomic tmp -> fsync -> rename write. No silent overwrite of an
already-exported partition.

This module's responsibility ends the moment a complete, durable Parquet
file exists at its canonical destination path. It does not write a
manifest, touch the catalog, replicate offsite, run under any scheduler,
or perform pruning/orchestration -- those are separate, later tasks.

Determinism scope
------------------
"Deterministic" here means the same logical rows always produce the same
schema, the same column order, and the same row order (canonical_sort,
per content_hash.py). It does NOT claim byte-identical compressed output
across pyarrow versions/platforms -- that guarantee is neither offered by
Parquet/pyarrow nor needed, because a partition's authoritative identity
is content_hash() computed over the Python row objects (T1), never over
the Parquet file's own bytes. In particular, the physical NaN bit pattern
pyarrow chooses to write is irrelevant to SI-7: nothing ever reconstructs
a content_hash by reading Parquet bytes back. SI-7's requirement on this
module is limited to using the float64 (DOUBLE) physical type.

Fail-closed behavior
---------------------
Refuses to overwrite an existing artifact at the canonical destination
path (immutable archive -- a supplemental revision is a distinct,
explicit operation belonging to a later task, not a silent overwrite
here). Any failure while building or writing the file removes the
temporary file and re-raises; the canonical destination path is only
ever reached via a single atomic os.replace, so it is never observable
in a partially written state.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from src.archive.content_hash import (
    COLUMN_ORDER,
    COLUMN_TYPES,
    Row,
    canonical_sort,
    content_hash,
)
from src.archive.partition_reader import PartitionKey

# Compression codec applied to every column. SNAPPY is the Parquet
# ecosystem's long-standing, widely supported, low-CPU-overhead default,
# and is readable by the DuckDB-based read layer (M1.4). Chosen for
# operational safety, not dictated by any hash/determinism requirement --
# content_hash never reads these bytes back (see module docstring).
_COMPRESSION = "SNAPPY"

# Pin the Parquet format version so a future pyarrow upgrade cannot
# silently change the on-disk logical-type mapping used for new writes.
_PARQUET_VERSION = "2.6"


def _pyarrow_type(column: str) -> pa.DataType:
    """Map a column to its explicit Parquet physical type.

    Mirrors content_hash.COLUMN_TYPES exactly (single source of truth for
    which columns are int/str vs. the float64 default) so the physical
    schema can never silently drift from the canonicalization contract.
    """
    column_type = COLUMN_TYPES.get(column, "float")
    if column_type == "int":
        return pa.int64()
    if column_type == "str":
        return pa.string()
    return pa.float64()  # DOUBLE physical type, per M1.1 v1.0 SI-7


def _build_schema(content_hash_digest: str, row_count: int, key: PartitionKey) -> pa.Schema:
    """Build the fixed-order, explicitly-typed schema plus embedded metadata.

    Embedded metadata is limited to values derived deterministically from
    the row data itself (content_hash, row_count, partition_key) plus the
    fixed hash_spec_version -- nothing wall-clock-dependent or
    environment-dependent is embedded here (that provenance belongs to
    the separate manifest sidecar, a later task).
    """
    fields = [pa.field(column, _pyarrow_type(column)) for column in COLUMN_ORDER]
    metadata = {
        b"hash_spec_version": b"1",
        b"content_hash": content_hash_digest.encode("utf-8"),
        b"row_count": str(row_count).encode("utf-8"),
        b"partition_key": key.utc_date.encode("utf-8"),
    }
    return pa.schema(fields, metadata=metadata)


def _build_table(sorted_rows: list[Row], schema: pa.Schema) -> pa.Table:
    """Build a pyarrow Table with columns in COLUMN_ORDER, rows already sorted."""
    arrays = [
        pa.array([row.get(column) for row in sorted_rows], type=schema.field(column).type)
        for column in COLUMN_ORDER
    ]
    return pa.Table.from_arrays(arrays, schema=schema)


def write_partition_parquet(rows: list[Row], dest_dir: Path, key: PartitionKey) -> Path:
    """Write one partition's rows to an immutable Parquet file. Returns its path.

    Rows are canonically sorted (content_hash.canonical_sort) before
    writing, so the Parquet file's row order is deterministic and matches
    the order used to compute the embedded content_hash. An empty `rows`
    list is a valid input and produces a valid, schema-complete, zero-row
    Parquet file.

    Raises:
        FileExistsError: an artifact already exists at the canonical
            destination path -- refuses to silently overwrite it.
        KeyError: a row is missing a required canonical-sort key
            (propagated from content_hash.canonical_sort); no file is
            created in this case.
    """
    dest_dir = Path(dest_dir)
    final_path = dest_dir / f"{key.utc_date}.parquet"

    if final_path.exists():
        raise FileExistsError(f"partition already exported: {final_path}")

    dest_dir.mkdir(parents=True, exist_ok=True)

    sorted_rows = canonical_sort(rows)
    digest = content_hash(sorted_rows)
    schema = _build_schema(digest, len(sorted_rows), key)
    table = _build_table(sorted_rows, schema)

    tmp_fd, tmp_name = tempfile.mkstemp(
        dir=str(dest_dir), prefix=f".{key.utc_date}.", suffix=".parquet.tmp"
    )
    os.close(tmp_fd)
    tmp_path = Path(tmp_name)

    try:
        pq.write_table(table, str(tmp_path), compression=_COMPRESSION, version=_PARQUET_VERSION)
        with open(tmp_path, "rb+") as fh:
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp_path), str(final_path))
    except Exception:
        try:
            os.remove(str(tmp_path))
        except OSError:
            pass
        raise

    return final_path
