"""ArchiveWriter -- orchestrates one partition's PENDING -> VERIFIED export.

Ties together partition_reader (read, T3), content_hash (canonicalize +
hash, T1), parquet_writer (atomic write, T4), manifest (build + persist
the sidecar, T5), and catalog_port (state transitions, T6) into a single
one-shot export_partition() call. This module contains no hashing, no
Parquet/JSON encoding, and no catalog persistence logic of its own -- it
only calls the already-implemented, independently-tested functions from
those modules and propagates whatever they raise.

Single-threaded, one-shot per export_partition() call; no background
thread, no scheduler wiring (that belongs to a later task -- M1.5).

Idempotency and partial failure
---------------------------------
Calling export_partition() again for a partition the catalog no longer
reports as PENDING (e.g. because a prior call already completed it) is a
safe, side-effect-free no-op: ArchiveResult(exported=False, reason="not
pending"). Neither the filesystem nor the catalog is touched in that case.

Idempotency is NOT claimed across a PARTIAL failure. If a call fails
after write_partition_parquet succeeds but before the catalog records
EXPORTED (e.g. mark_exported itself raises), the Parquet artifact remains
on disk while the catalog still reports the partition PENDING. A naive
retry will then reach write_partition_parquet again and get
FileExistsError (T4's overwrite protection) -- this is intentional,
fail-closed behavior, not a bug to be papered over with retry logic.
Deciding whether the orphaned artifact is trustworthy and how to proceed
is an operator decision; building that recovery path is out of scope for
this thin orchestrator (no retry orchestration beyond the specification).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.archive.catalog_port import CatalogPort, ensure_catalog_port
from src.archive.content_hash import Row, content_hash
from src.archive.manifest import (
    build_manifest,
    compute_file_sha256,
    validate_manifest,
    write_manifest,
)
from src.archive.parquet_writer import write_partition_parquet
from src.archive.partition_reader import PartitionKey, read_partition


@dataclass
class ArchiveResult:
    """Outcome of one export_partition() invocation."""
    partition_key: str
    row_count: int
    content_hash: str
    exported: bool
    verified: bool
    reason: str | None


class ArchiveWriter:
    """Thin orchestrator: PENDING -> EXPORTED -> VERIFIED for one partition."""

    def __init__(self, hot_db_path: str, dest_dir: Path, catalog: CatalogPort) -> None:
        self._hot_db_path = hot_db_path
        self._dest_dir = Path(dest_dir)
        # Fail closed immediately if `catalog` isn't shaped like a
        # CatalogPort, rather than discovering that partway through export.
        self._catalog = ensure_catalog_port(catalog)

    def export_partition(self, key: PartitionKey) -> ArchiveResult:
        """Run one partition through PENDING -> EXPORTED -> VERIFIED.

        Returns ArchiveResult(exported=False, verified=False,
        reason="not pending") without touching the filesystem or the
        catalog if the partition is not currently PENDING. Any other
        failure (read, write, or catalog error) propagates as the
        original exception -- this orchestrator never catches and
        converts a component failure into a "soft" result.
        """
        if not self._catalog.is_pending(key.utc_date):
            return ArchiveResult(
                partition_key=key.utc_date,
                row_count=0,
                content_hash="",
                exported=False,
                verified=False,
                reason="not pending",
            )

        rows: list[Row] = read_partition(self._hot_db_path, key)
        parquet_path = write_partition_parquet(rows, self._dest_dir, key)
        manifest = build_manifest(parquet_path, key)
        validate_manifest(manifest)

        # Independent re-derivation check: recompute content_hash directly
        # from the rows this writer itself read, and confirm it matches
        # what got embedded in the artifact (T4) and carried into the
        # manifest (T5). Catches any divergence between read -> encode.
        expected_hash = content_hash(rows)
        if manifest.content_hash != expected_hash:
            raise ValueError(
                f"content_hash mismatch for partition {key.utc_date}: "
                f"artifact={manifest.content_hash!r} recomputed={expected_hash!r}"
            )

        write_manifest(manifest, self._dest_dir, key)
        self._catalog.mark_exported(key.utc_date, manifest)

        # Local verify before promoting EXPORTED -> VERIFIED: re-hash the
        # artifact's bytes on disk right now and confirm they still match
        # what was recorded a moment ago in the manifest.
        if compute_file_sha256(parquet_path) != manifest.file_sha256:
            raise ValueError(
                f"file_sha256 mismatch verifying partition {key.utc_date} "
                f"-- artifact changed on disk after export"
            )
        self._catalog.mark_verified(key.utc_date)

        return ArchiveResult(
            partition_key=key.utc_date,
            row_count=manifest.row_count,
            content_hash=manifest.content_hash,
            exported=True,
            verified=True,
            reason=None,
        )
