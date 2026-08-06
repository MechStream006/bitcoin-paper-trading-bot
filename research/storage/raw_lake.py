"""
Raw Data Lake (Z2) -- as-received landing, content-hash-addressed, revisioned.

ADR-I009 §9 Z2 / §13.1: "The immutable store of as-received acquisitions...
with acquisition metadata and no normalisation applied... Write-once; a
differing re-fetch produces a new revision." §16.1: "A re-ingest produces a
new revision alongside the old, never an overwrite."

This module lands the DataFrame a source's own fetch function returns,
BEFORE that source's _normalize() runs -- this platform's own normalisation
logic (dtype coercion beyond what the source client itself performs,
sorting, deduplication) has not yet been applied, so source quirks are
preserved exactly as received, per §13.1's "source quirks are preserved,
not corrected."

SCOPED LIMITATION, stated explicitly rather than implied: what is landed
here is the parsed DataFrame a source's fetch function returns, not the
literal HTTP response bytes. For research/ingest/dvol.py (this milestone's
pilot), _fetch_range() already performs int()/float() coercion while
parsing Deribit's JSON response, before this module ever sees the data --
capturing bytes one layer further upstream would require restructuring
dvol.py's fetch functions themselves, which this milestone's blast-radius
discipline does not authorise. What this module DOES preserve, faithfully,
is everything downstream of that parse and upstream of THIS platform's own
normalisation contract (research/ingest/dvol.py's own _normalize(): sort,
dedup, dtype finalisation) -- the boundary that actually matters for
ADR-I009 PS1 (a normalisation defect must not be unfixable without
re-fetching an otherwise-preserved input).

Engineering roadmap Phase 1, WBS 1.2/1.3 pilot. Wired only into
research/ingest/dvol.py (the smallest-volume ingest path) for now; WBS 1.6
repeats this pattern for the remaining paths, one milestone each -- this
module itself is already dataset-agnostic and requires no change to extend.

Disk layout, sibling to storage.lake's canonical partitions:
    <base_dir>/_raw_lake/<dataset>/<symbol>/year=YYYY/month=MM/
        <sha256>.parquet          -- one file per distinct revision, content-addressed
        _revisions.json           -- append-only index: ordinal, hash, fetched_at, rows

Public API:
    raw_partition_dir(base_dir, dataset, symbol, year, month) -> Path
    content_hash(df) -> str
    land_raw(df, base_dir, *, dataset, symbol, year, month, source,
             source_url, conditioning, fetched_at) -> dict
    read_revisions(base_dir, dataset, symbol, year, month) -> list[dict]
"""
from __future__ import annotations

import hashlib
import json
import pathlib

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

_REVISIONS_NAME = "_revisions.json"


def raw_partition_dir(
    base_dir: str | pathlib.Path,
    dataset: str,
    symbol: str,
    year: int,
    month: int,
) -> pathlib.Path:
    """Directory holding every landed revision for one raw partition."""
    return (
        pathlib.Path(base_dir)
        / "_raw_lake"
        / dataset
        / symbol
        / f"year={year}"
        / f"month={month:02d}"
    )


def content_hash(df: pd.DataFrame) -> str:
    """
    Deterministic SHA-256 of *df*'s content (ADR-I009 IQROS-P5), independent
    of where or when it is called. Computed over canonical Parquet bytes
    (fixed compression codec; PyArrow embeds no wall-clock value in a
    Parquet file's structural metadata) -- byte-identical DataFrame content
    hashes identically on every call in this environment.
    """
    buf = pa.BufferOutputStream()
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), buf, compression="zstd")
    return hashlib.sha256(buf.getvalue().to_pybytes()).hexdigest()


def read_revisions(
    base_dir: str | pathlib.Path,
    dataset: str,
    symbol: str,
    year: int,
    month: int,
) -> list[dict]:
    """The revision index for one partition, oldest first. Empty list if
    nothing has ever been landed for this (dataset, symbol, year, month)."""
    idx_path = raw_partition_dir(base_dir, dataset, symbol, year, month) / _REVISIONS_NAME
    if not idx_path.exists():
        return []
    return json.loads(idx_path.read_text(encoding="utf-8"))


def land_raw(
    df: pd.DataFrame,
    base_dir: str | pathlib.Path,
    *,
    dataset: str,
    symbol: str,
    year: int,
    month: int,
    source: str,
    source_url: str | None,
    conditioning: str,
    fetched_at: str,
) -> dict:
    """
    Land *df* as-received into the raw lake.

    Idempotent: landing byte-identical content twice writes the Parquet
    file once (the second call finds the same content hash already present
    and does nothing further) and appends no new revision-index entry.
    Landing content that differs from every prior revision writes a NEW,
    content-addressed file alongside every existing one and appends a new
    revision-index entry -- NEVER an overwrite (ADR-I009 §16.1: "A re-ingest
    produces a new revision alongside the old").

    conditioning is REQUIRED and refused if blank (Ph6: no field is
    defaultable) -- this is a brand-new artifact class with no pre-existing
    caller to preserve backward compatibility with, unlike
    storage.manifest.write_manifest()'s optional
    normalization_contract_version/conditioning fields (WBS 1.1/1.5), which
    stayed optional specifically to avoid breaking build_corpus.py /
    build_perp_corpus.py.

    Returns a dict describing the landing:
        {"hash": str, "revision_ordinal": int, "is_new_revision": bool,
         "path": str, "rows": int}
    """
    if not conditioning or not conditioning.strip():
        raise ValueError(
            "conditioning is required and must be non-empty (ADR-I009 §13.2: "
            "'Conditioning is mandatory and non-defaultable')"
        )

    part_dir = raw_partition_dir(base_dir, dataset, symbol, year, month)
    part_dir.mkdir(parents=True, exist_ok=True)

    h = content_hash(df)
    file_path = part_dir / f"{h}.parquet"
    revisions = read_revisions(base_dir, dataset, symbol, year, month)

    existing = next((r for r in revisions if r["hash"] == h), None)
    if existing is not None:
        return {
            "hash": h,
            "revision_ordinal": existing["revision_ordinal"],
            "is_new_revision": False,
            "path": str(file_path),
            "rows": len(df),
        }

    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), file_path, compression="zstd")

    ordinal = len(revisions) + 1
    entry = {
        "revision_ordinal": ordinal,
        "hash": h,
        "rows": len(df),
        "dataset": dataset,
        "symbol": symbol,
        "year": year,
        "month": month,
        "source": source,
        "source_url": source_url,
        "conditioning": conditioning,
        "fetched_at": fetched_at,
    }
    revisions.append(entry)
    (part_dir / _REVISIONS_NAME).write_text(json.dumps(revisions, indent=2), encoding="utf-8")

    return {
        "hash": h,
        "revision_ordinal": ordinal,
        "is_new_revision": True,
        "path": str(file_path),
        "rows": len(df),
    }
