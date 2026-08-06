"""
Canonical provenance manifest for research lake partitions (WI-6).

A manifest is a JSON sidecar (_manifest.json) written alongside each
data.parquet file.  It records SHA-256, row count, timestamp bounds,
data source, and config version — enough to detect tampering, verify
completeness, and reproduce the ingest exactly.

Sidecar location: <parquet_file_dir>/_manifest.json

Schema version: 3  (increment when fields are added or removed)

Public API:
    write_manifest(parquet_path, *, dataset, symbol, year, month,
                   source, source_url, rows, min_ts, max_ts, ts_column,
                   ingest_ts, config_version,
                   normalization_contract_version=None,
                   conditioning=None, admissibility=None,
                   raw_content_hash=None) -> dict
    read_manifest(parquet_path) -> dict
    verify_hash(parquet_path) -> bool
"""
from __future__ import annotations

import hashlib
import json
import pathlib
from typing import Any

SIDECAR_NAME    = "_manifest.json"
SCHEMA_VERSION  = 3
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
    normalization_contract_version: int | None = None,  # ADR-I009 §13.3
    conditioning: str | None = None,        # ADR-I009 §13.2 -- WBS 1.5
    admissibility: dict[str, Any] | None = None,  # ADR-I014 ceiling -- WBS 1.4
    raw_content_hash: str | None = None,    # links to storage.raw_lake -- WBS 1.3
) -> dict:
    """
    Compute SHA-256 of *parquet_path*, build the canonical manifest dict,
    and write it as _manifest.json in the same directory.
    Returns the manifest dict.
    Overwrites any existing sidecar (idempotent on re-ingest).

    normalization_contract_version: the version of the SOURCE-SPECIFIC
    normalisation logic (dtype coercion, ordering, dedup rule, boundary
    handling — ADR-I009 §13.3) that produced this partition. Distinct from
    config_version (sources.yaml's acquisition-config version) and from
    schema_version above (this manifest's own field-shape version) -- three
    different things that happen to look similar.

    conditioning: the population statement this partition's observations
    exist under (ADR-I009 §13.2 — "the population statement under which
    observations exist"). A free-form, human-readable string; the caller
    (an ingest writer) owns choosing it, this function only carries it.

    admissibility: the computed ceiling (research/admissibility/,
    research/quality/admissibility.py) this partition carries, as a plain
    dict — {"certification": "GREEN"|"AMBER"|"RED",
    "readiness": "READY"|"PARTIAL"|"NOT_READY", "lattice_version": str}.
    Stored as a nested dict rather than five more flat keyword parameters to
    keep this function's signature from growing without bound; the shape is
    owned by the caller, not validated here (validation is
    research/quality/admissibility.py's and research/admissibility/'s job,
    not storage's — this module records facts, it does not certify them,
    per ADR-I009 §18.1: "Storage governs facts... it records verdicts; it
    never issues them.").

    raw_content_hash: the content hash (storage.raw_lake.content_hash) of
    the raw-lake revision this canonical partition was normalised from —
    the link ADR-I009 §11's boundary-verification discipline needs to trace
    a canonical partition back to its as-received origin.

    All four new parameters are optional and default to None so every
    existing caller of this function (research/build_corpus.py,
    research/build_perp_corpus.py, research/validate_wi6.py) is completely
    unaffected — additive, per this milestone's backward-compatibility
    discipline (WBS 1.1's identical reasoning, extended).
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
        "normalization_contract_version": normalization_contract_version,
        "conditioning":      conditioning,
        "admissibility":     admissibility,
        "raw_content_hash":  raw_content_hash,
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
