"""
Feature Value Store (ADR-I010 §7, §12; engineering roadmap Phase 2, WBS 2.2
-- part of the first Feature Store milestone).

The Value Store holds numbers. It holds no authority (ADR-I010 §14.1: "the
Value Store never issues judgments"). Two tiers, distinguished by
OBLIGATION, not content (§7.1):

  - Cache tier: speed. Evictable at any moment. No authority. On loss,
    nothing is lost.
  - Materialisation tier: exact-bytes recoverability. Write-once. No
    authority -- verifiable, not authoritative. On loss, the citing
    artifact loses its byte guarantee.

Addressing is the triple (spec_hash, corpus_version, as_of) per ADR-I010
§7.2 -- the same triple always yields the same values, or the platform has
a defect.

VERIFICATION RULE (§7.3): a stored value disagreeing with recomputation is
WRONG BY DEFINITION. This module never "repairs" a materialised value to
match a recomputation, and never silently overwrites a materialised value
that already exists with different content -- both are reported as a
DivergenceEvent, never silently reconciled (IQROS-P3).

DELIBERATE DISTINCTION FROM research/storage/raw_lake.py: raw_lake.py's
`land_raw()` treats a differing re-fetch as a LEGITIMATE new revision
(sources genuinely republish over time -- ADR-I009 §16.1). This module does
NOT use that pattern for materialised feature values, because the
underlying invariant is different: a value set is addressed by a FIXED
(spec_hash, corpus_version, as_of) triple over an already-pinned corpus, so
two computations at the same address should be deterministically IDENTICAL
-- a difference is always a bug (a non-deterministic binding, a corpus
version collision, etc.), never a legitimate revision. This is stated here
explicitly because the two modules could otherwise look like they should
share one revisioning mechanism; they intentionally do not.

SCOPED SIMPLIFICATION: this module has no knowledge of admissibility floors
or warm-up requirements (ADR-I010 §12.4's full resolve() flowchart also
checks those BEFORE reaching the cache/materialisation logic here). Per
ADR-I010 §14.1, that is a consumer-facing policy concern, not something the
generic Value Store enforces -- a future consumer (WBS 2.4+, or the Dataset
Builder) is expected to perform those checks before calling resolve() here.
This module implements exactly the cache/materialisation/compute portion of
that flowchart.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS feature_value_cache (
    spec_hash       TEXT NOT NULL,
    corpus_version  TEXT NOT NULL,
    as_of           TEXT NOT NULL,
    values_json     TEXT NOT NULL,
    computed_at     TEXT NOT NULL,
    PRIMARY KEY (spec_hash, corpus_version, as_of)
);

CREATE TABLE IF NOT EXISTS feature_value_materialized (
    spec_hash            TEXT NOT NULL,
    corpus_version       TEXT NOT NULL,
    as_of                TEXT NOT NULL,
    values_json          TEXT NOT NULL,
    content_hash         TEXT NOT NULL,
    citing_artifact       TEXT NOT NULL,
    retention_obligation TEXT NOT NULL,
    materialized_at       TEXT NOT NULL,
    PRIMARY KEY (spec_hash, corpus_version, as_of)
);
"""


class ValueStoreError(ValueError):
    """Base class for value-store failures."""


class DivergenceEvent(ValueStoreError):
    """
    A stored value disagrees with a recomputation, or a materialisation was
    attempted with content differing from what is already materialised at
    the same address (ADR-I010 §7.3). Reported, never silently reconciled --
    this module never overwrites a materialised value to make a divergence
    go away.
    """

    def __init__(self, spec_hash: str, corpus_version: str, as_of: str, detail: str):
        self.spec_hash = spec_hash
        self.corpus_version = corpus_version
        self.as_of = as_of
        self.detail = detail
        super().__init__(
            f"divergence at ({spec_hash[:12]}..., {corpus_version}, {as_of}): {detail}"
        )


@dataclass(frozen=True)
class ResolvedValue:
    values: Any
    source: str  # "materialized" | "cache" | "computed"


def init_db(path: Path | str) -> None:
    """Idempotent CREATE-only schema-ensure. See registry.py's init_db()
    docstring for the same never-ALTER discipline."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.executescript(_SCHEMA_SQL)
        con.commit()
    finally:
        con.close()


def _values_json(values: Any) -> str:
    return json.dumps(values, sort_keys=True, separators=(",", ":"))


def _content_hash(values: Any) -> str:
    return hashlib.sha256(_values_json(values).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Cache tier -- evictable, no authority
# ---------------------------------------------------------------------------

def put_cache(
    db_path: Path | str, spec_hash: str, corpus_version: str, as_of: str,
    values: Any, computed_at: str,
) -> None:
    """Best-effort write. Overwrites any prior cache entry at this address
    freely -- the cache carries no obligation and no authority (§7.1)."""
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "INSERT INTO feature_value_cache "
            "(spec_hash, corpus_version, as_of, values_json, computed_at) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT (spec_hash, corpus_version, as_of) "
            "DO UPDATE SET values_json = excluded.values_json, computed_at = excluded.computed_at",
            (spec_hash, corpus_version, as_of, _values_json(values), computed_at),
        )
        con.commit()
    finally:
        con.close()


def get_cache(db_path: Path | str, spec_hash: str, corpus_version: str, as_of: str) -> Any | None:
    con = sqlite3.connect(db_path)
    try:
        row = con.execute(
            "SELECT values_json FROM feature_value_cache "
            "WHERE spec_hash = ? AND corpus_version = ? AND as_of = ?",
            (spec_hash, corpus_version, as_of),
        ).fetchone()
        return json.loads(row[0]) if row is not None else None
    finally:
        con.close()


def evict_cache(db_path: Path | str, spec_hash: str, corpus_version: str, as_of: str) -> None:
    """Evicting a cache entry loses nothing of record (§7.1) -- always safe."""
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "DELETE FROM feature_value_cache "
            "WHERE spec_hash = ? AND corpus_version = ? AND as_of = ?",
            (spec_hash, corpus_version, as_of),
        )
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Materialisation tier -- write-once, obligation-bound, no authority
# ---------------------------------------------------------------------------

def materialize(
    db_path: Path | str, spec_hash: str, corpus_version: str, as_of: str,
    values: Any, citing_artifact: str, retention_obligation: str, materialized_at: str,
) -> str:
    """
    Write-once (§12.3). If nothing is materialised yet at this address,
    writes it and returns the new content hash. If something IS already
    materialised at this address:
      - identical content -> idempotent no-op, returns the existing hash
      - differing content -> DivergenceEvent (never overwritten; a fixed
        address should be deterministic, so a difference is always a
        defect -- see this module's docstring for why this differs from
        raw_lake.py's revisioning)
    """
    new_hash = _content_hash(values)
    con = sqlite3.connect(db_path)
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT content_hash FROM feature_value_materialized "
            "WHERE spec_hash = ? AND corpus_version = ? AND as_of = ?",
            (spec_hash, corpus_version, as_of),
        ).fetchone()
        if row is not None:
            con.rollback()
            if row[0] == new_hash:
                return row[0]
            raise DivergenceEvent(
                spec_hash, corpus_version, as_of,
                f"materialisation already exists with content_hash={row[0][:12]}..., "
                f"new computation hashes to {new_hash[:12]}... -- never overwritten",
            )

        con.execute(
            "INSERT INTO feature_value_materialized "
            "(spec_hash, corpus_version, as_of, values_json, content_hash, "
            " citing_artifact, retention_obligation, materialized_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                spec_hash, corpus_version, as_of, _values_json(values), new_hash,
                citing_artifact, retention_obligation, materialized_at,
            ),
        )
        con.commit()
        return new_hash
    finally:
        con.close()


def get_materialized(db_path: Path | str, spec_hash: str, corpus_version: str, as_of: str) -> Any | None:
    """
    Read a materialised value set, verifying it against its own recorded
    content hash first. A hash mismatch (on-disk corruption, or a defect
    that bypassed materialize()'s own write path) raises DivergenceEvent
    rather than returning corrupted data silently.
    """
    con = sqlite3.connect(db_path)
    try:
        row = con.execute(
            "SELECT values_json, content_hash FROM feature_value_materialized "
            "WHERE spec_hash = ? AND corpus_version = ? AND as_of = ?",
            (spec_hash, corpus_version, as_of),
        ).fetchone()
    finally:
        con.close()

    if row is None:
        return None

    values_json, stored_hash = row
    actual_hash = hashlib.sha256(values_json.encode("utf-8")).hexdigest()
    if actual_hash != stored_hash:
        raise DivergenceEvent(
            spec_hash, corpus_version, as_of,
            f"stored content_hash={stored_hash[:12]}... does not match recomputed "
            f"hash={actual_hash[:12]}... of the stored bytes -- possible corruption",
        )
    return json.loads(values_json)


# ---------------------------------------------------------------------------
# resolve() -- ADR-I010 §12.4's cache/materialisation/compute path
# ---------------------------------------------------------------------------

def resolve(
    db_path: Path | str,
    spec_hash: str,
    corpus_version: str,
    as_of: str,
    compute_fn: Callable[[], Any],
    *,
    computed_at: str,
) -> ResolvedValue:
    """
    Serve a value set for (spec_hash, corpus_version, as_of):
      1. materialised set exists -> verify hash, serve ("materialized")
      2. else cache hit -> serve ("cache")
      3. else compute via `compute_fn()`, populate cache best-effort,
         serve ("computed")

    compute_fn is the CALLER's responsibility -- this module holds no
    binding-resolution logic of its own (that is registry.py's job; see
    this module's docstring). Admissibility-floor and warm-up checks
    (ADR-I010 §12.4's earlier flowchart steps) are also the caller's
    responsibility, performed BEFORE calling resolve().
    """
    materialized = get_materialized(db_path, spec_hash, corpus_version, as_of)
    if materialized is not None:
        return ResolvedValue(values=materialized, source="materialized")

    cached = get_cache(db_path, spec_hash, corpus_version, as_of)
    if cached is not None:
        return ResolvedValue(values=cached, source="cache")

    computed = compute_fn()
    put_cache(db_path, spec_hash, corpus_version, as_of, computed, computed_at)
    return ResolvedValue(values=computed, source="computed")
