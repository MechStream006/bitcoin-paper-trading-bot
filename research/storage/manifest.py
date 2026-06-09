"""
Canonical provenance manifest for research lake partitions (WI-6).

A manifest is a JSON sidecar (_manifest.json) written alongside each
data.parquet file.  It records SHA-256, row count, timestamp bounds,
data source, and config version — enough to detect tampering, verify
completeness, and reproduce the ingest exactly.

Sidecar location: <parquet_file_dir>/_manifest.json

Schema version: 1  (increment when fields are added or removed)

Public API:
    write_manifest(parquet_path, *, dataset, symbol, year, month,
                   source, source_url, rows, min_ts, max_ts, ts_column,
                   ingest_ts, config_version) -> dict
    read_manifest(parquet_path) -> dict
    verify_hash(parquet_path) -> bool
"""
from __future__ import annotations

import hashlib
import json
import pathlib

SIDECAR_NAME    = "_manifest.json"
SCHEMA_VERSION  = 1
_CHUNK_BYTES    = 65_536   # 64 KB read chunks for SHA-256


def _sha256(path: pathlib.Path) -> str:
    """Return hex-encoded SHA-256 of the file at *path*."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK_BYTES), b""):
            h.update(chunk)
    return h.hexdigest()


def _sidecar(parquet_path: pathlib.Path) -> pathlib.Path:
    return parquet_path.parent / SIDECAR_NAME


def write_manifest(
    parquet_path: str | pathlib.Path,
    *,
    dataset: str,
    symbol: str,
    year: int,
    month: int,
    source: str,
    source_url: str | None,
    rows: int,
    min_ts: int,
    max_ts: int,
    ts_column: str,
    ingest_ts: str,       # ISO 8601 UTC string, e.g. datetime.now(UTC).isoformat()
    config_version: int,  # sources.yaml `version` field
) -> dict:
    """
    Compute SHA-256 of *parquet_path*, build the canonical manifest dict,
    and write it as _manifest.json in the same directory.
    Returns the manifest dict.
    Overwrites any existing sidecar (idempotent on re-ingest).
    """
    parquet_path = pathlib.Path(parquet_path)
    manifest = {
        "schema_version":  SCHEMA_VERSION,
        "dataset":         dataset,
        "symbol":          symbol,
        "year":            year,
        "month":           month,
        "parquet_file":    parquet_path.name,
        "parquet_sha256":  _sha256(parquet_path),
        "rows":            rows,
        "min_ts":          min_ts,
        "max_ts":          max_ts,
        "ts_column":       ts_column,
        "source":          source,
        "source_url":      source_url,
        "ingest_ts":       ingest_ts,
        "config_version":  config_version,
    }
    _sidecar(parquet_path).write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return manifest


def read_manifest(parquet_path: str | pathlib.Path) -> dict:
    """
    Read and return the _manifest.json sidecar for *parquet_path*.
    Raises FileNotFoundError if the sidecar does not exist.
    """
    sidecar = _sidecar(pathlib.Path(parquet_path))
    if not sidecar.exists():
        raise FileNotFoundError(f"Manifest not found: {sidecar}")
    return json.loads(sidecar.read_text(encoding="utf-8"))


def verify_hash(parquet_path: str | pathlib.Path) -> bool:
    """
    Recompute SHA-256 of *parquet_path* and compare with the stored value
    in _manifest.json.  Returns True if they match, False on mismatch
    (indicating tampering or corruption).
    Raises FileNotFoundError if the parquet or sidecar is missing.
    """
    parquet_path = pathlib.Path(parquet_path)
    manifest     = read_manifest(parquet_path)
    stored       = manifest.get("parquet_sha256", "")
    actual       = _sha256(parquet_path)
    return actual == stored
