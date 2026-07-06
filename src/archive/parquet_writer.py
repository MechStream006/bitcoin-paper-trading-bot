"""Atomic Parquet writer for exported partitions.

Writes a partition's rows to an immutable Parquet file in the cold-archive
directory. Must write to a temporary path and atomically rename
(os.replace) into place, so a crash mid-write can never leave a partial
file observable at the canonical destination path. Must enforce DOUBLE
(not FLOAT) physical type on every float column per M1.1 v1.0 SI-7
(deterministic content_hash requires DOUBLE precision). The ts column is
written as a verbatim UTF-8 string (decision D-1), never reparsed.

TODO(T4): implement write_partition_parquet(rows, dest_dir, key) -> Path.

No Parquet is written and no executable archive functionality is
implemented in this file yet.
"""
from __future__ import annotations
