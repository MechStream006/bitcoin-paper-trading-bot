"""Partition manifest -- sidecar metadata for each exported Parquet file.

The manifest records everything needed to independently re-verify a
partition's content_hash and provenance: partition key, row count, the
canonical content_hash, the hash_spec_version it was computed under, the
collector's runtime_provenance (git SHA, per the M1.0 addendum), and the
writer's own code version. Serialized as a JSON sidecar next to the
Parquet file.

The manifest is a cold-storage artifact destined for eventual offsite
replication (M1.3) -- it must never contain credentials, API keys,
tokens, or any other secret value. Secrets policy: keys live only in
.env, never in chat, memory, or any committed or archived artifact.

TODO(T5): implement write_manifest(manifest, dest_dir, key) -> Path and
read_manifest(path) -> Manifest.

No executable archive functionality is implemented in this file yet.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Manifest:
    """Immutable record describing one exported+verified partition."""
    partition_key: str
    row_count: int
    content_hash: str
    hash_spec_version: int
    schema_version: int
    collector_code_sha: str | None
    writer_code_version: str
    created_at: str
