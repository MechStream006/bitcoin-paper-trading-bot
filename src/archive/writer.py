"""ArchiveWriter -- orchestrates one partition's PENDING -> VERIFIED export.

Ties together partition_reader (read), content_hash (canonicalize + hash),
parquet_writer (atomic write), manifest (sidecar), and catalog_port (state
transitions) into a single one-shot export_partition() call. Styled after
src/persistence/snapshot_writer.py: single-threaded, no background thread,
invoked by an external caller (a manual CLI in this implementation
package; scheduler wiring is explicitly out of scope and belongs to M1.5).

Any failure before the catalog mark_exported() call must leave the
catalog untouched (still PENDING) and no partial file at the canonical
Parquet path (atomic rename in parquet_writer.py) -- so a failed run is
always safely retryable and never leaves a stuck half-state.

TODO(T7): implement ArchiveWriter (export_partition, health) tying
T1/T3/T4/T5/T6 together, following the SnapshotWriter Result/HealthStatus
conventions already used elsewhere in this codebase.

No orchestration logic, no SQLite access, no Parquet writes, and no
executable archive functionality is implemented in this file yet.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ArchiveResult:
    """Outcome of one export_partition() invocation."""
    partition_key: str
    row_count: int
    content_hash: str
    exported: bool
    verified: bool
    reason: str | None
