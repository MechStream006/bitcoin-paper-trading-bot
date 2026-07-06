"""Partition manifest -- deterministic sidecar metadata for a Parquet artifact.

The manifest describes one already-written Parquet artifact (produced by
parquet_writer.write_partition_parquet, T4): which partition it is, how
many rows it has, the canonical content_hash of its row data, and a
file_sha256 integrity hash of the artifact's own bytes on disk. Every
field is derived from the artifact itself or from constants fixed in
this module -- nothing wall-clock-dependent, host-dependent, or otherwise
environment-specific is included (no created_at, no hostname, no PID, no
collector/writer code-version stamp). That kind of operational provenance
is out of scope here; this manifest's only job is to let a reader verify
a partition's data integrity and identity, reproducibly, from the
artifact alone.

content_hash vs. file_sha256
-----------------------------
content_hash (from content_hash.py, embedded in the Parquet file's own
schema metadata by T4) is the partition's LOGICAL identity: computed over
the canonical row material, independent of how those rows happen to be
encoded on disk. file_sha256 is the artifact's PHYSICAL integrity check:
a SHA-256 over the exact bytes of the Parquet file, which changes if the
file is corrupted or re-encoded even when its logical content_hash would
not. Both are carried in the manifest because they answer different
questions (data identity vs. bytes-on-disk integrity) -- this matters for
the offsite verification step in M1.3.

Secrets policy: the manifest is a cold-storage artifact destined for
eventual offsite replication -- it must never contain credentials, API
keys, tokens, or any other secret value. Keys live only in .env, never in
chat, memory, or any committed or archived artifact.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pyarrow.parquet as pq

from src.archive.partition_reader import PartitionKey

# Version of THIS manifest's JSON shape (field set/meaning), independent
# of hash_spec_version (content_hash.py's canonicalization version, which
# is carried through per-artifact from the Parquet file's own metadata).
MANIFEST_SCHEMA_VERSION = 1

_READ_CHUNK_SIZE = 1 << 20  # 1 MiB


@dataclass(frozen=True)
class Manifest:
    """Immutable, fully-deterministic record describing one Parquet artifact."""
    partition_key: str
    artifact_filename: str
    row_count: int
    content_hash: str
    file_sha256: str
    hash_spec_version: int
    manifest_schema_version: int


def compute_file_sha256(path: Path) -> str:
    """Stream-hash a file's raw bytes. Returns "sha256:<hex>" (same format as content_hash)."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_READ_CHUNK_SIZE), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def build_manifest(parquet_path: Path, key: PartitionKey) -> Manifest:
    """Build a Manifest describing an already-written Parquet artifact.

    Reads content_hash/row_count/hash_spec_version from the artifact's
    own embedded schema metadata (written by parquet_writer.py, T4) --
    the manifest never recomputes these from raw row data, so it always
    describes exactly what is actually on disk. file_sha256 is computed
    independently by hashing the artifact's raw bytes.

    Raises:
        ValueError: the artifact's embedded partition_key metadata does
            not match `key` -- the caller passed a mismatched file/key
            pair, or the artifact metadata is missing/malformed.
    """
    schema = pq.read_schema(str(parquet_path))
    meta = schema.metadata or {}

    try:
        embedded_partition_key = meta[b"partition_key"].decode("utf-8")
        row_count = int(meta[b"row_count"].decode("utf-8"))
        embedded_content_hash = meta[b"content_hash"].decode("utf-8")
        hash_spec_version = int(meta[b"hash_spec_version"].decode("utf-8"))
    except (KeyError, ValueError) as exc:
        raise ValueError(
            f"artifact at {parquet_path} is missing or has malformed "
            f"embedded metadata: {type(exc).__name__}: {exc}"
        ) from exc

    if embedded_partition_key != key.utc_date:
        raise ValueError(
            f"artifact partition_key mismatch: file at {parquet_path} embeds "
            f"{embedded_partition_key!r}, expected {key.utc_date!r}"
        )

    return Manifest(
        partition_key=key.utc_date,
        artifact_filename=Path(parquet_path).name,
        row_count=row_count,
        content_hash=embedded_content_hash,
        file_sha256=compute_file_sha256(parquet_path),
        hash_spec_version=hash_spec_version,
        manifest_schema_version=MANIFEST_SCHEMA_VERSION,
    )


def validate_manifest(manifest: Manifest) -> None:
    """Check internal structural consistency of a manifest. Raises ValueError.

    This checks well-formedness (non-negative counts, correctly-prefixed
    hashes, positive version numbers, non-empty identity fields) -- it
    does not assert that hash_spec_version/manifest_schema_version equal
    any particular "current" value, since a well-formed manifest from an
    older-but-still-valid spec version should not be rejected here; policy
    about which versions a given reader accepts belongs to that reader.
    """
    if not manifest.partition_key:
        raise ValueError("partition_key must be non-empty")
    if not manifest.artifact_filename:
        raise ValueError("artifact_filename must be non-empty")
    if manifest.row_count < 0:
        raise ValueError(f"row_count must be >= 0, got {manifest.row_count}")
    if not manifest.content_hash.startswith("sha256:"):
        raise ValueError(f"content_hash must be sha256-prefixed: {manifest.content_hash!r}")
    if not manifest.file_sha256.startswith("sha256:"):
        raise ValueError(f"file_sha256 must be sha256-prefixed: {manifest.file_sha256!r}")
    if manifest.hash_spec_version < 1:
        raise ValueError(f"hash_spec_version must be >= 1, got {manifest.hash_spec_version}")
    if manifest.manifest_schema_version < 1:
        raise ValueError(
            f"manifest_schema_version must be >= 1, got {manifest.manifest_schema_version}"
        )


def _serialize(manifest: Manifest) -> bytes:
    """Deterministic JSON serialization: sorted keys, fixed separators, trailing newline."""
    payload = json.dumps(
        dataclasses.asdict(manifest), sort_keys=True, separators=(",", ": "), indent=2,
    )
    return (payload + "\n").encode("utf-8")


def write_manifest(manifest: Manifest, dest_dir: Path, key: PartitionKey) -> Path:
    """Write a manifest atomically as a JSON sidecar. Returns its path.

    Same safety principles as parquet_writer.write_partition_parquet
    (T4): refuses to overwrite an existing manifest at the canonical
    destination path, writes to a unique temp file in dest_dir, fsyncs
    it, then atomically renames into place. Any failure removes the temp
    file and re-raises; the final path is only ever reached via a single
    atomic os.replace.

    Raises:
        FileExistsError: a manifest already exists at the canonical
            destination path -- refuses to silently overwrite it.
    """
    dest_dir = Path(dest_dir)
    final_path = dest_dir / f"{key.utc_date}.manifest.json"

    if final_path.exists():
        raise FileExistsError(f"manifest already exists: {final_path}")

    dest_dir.mkdir(parents=True, exist_ok=True)

    payload_bytes = _serialize(manifest)

    tmp_fd, tmp_name = tempfile.mkstemp(
        dir=str(dest_dir), prefix=f".{key.utc_date}.", suffix=".manifest.json.tmp"
    )
    tmp_path = Path(tmp_name)

    try:
        with os.fdopen(tmp_fd, "wb") as fh:
            fh.write(payload_bytes)
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


def read_manifest(path: Path) -> Manifest:
    """Read and reconstruct a Manifest from its JSON sidecar.

    Raises TypeError/KeyError if the JSON does not have exactly the
    expected fields (a corrupt or foreign-format manifest fails to
    construct rather than silently producing a partially-populated
    object) -- pair with validate_manifest() for content-level checks.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return Manifest(**data)
