"""Bidirectional Citation Index — ADR-I012 §6.1, §6.2; DG-5; scope transfer T-6.

§6.1 calls this "the Registry's most consequential function". It answers two
questions, and the second is the one that carries the architecture:

    FORWARD   what does this dataset cite?    -> rematerialisation, provenance
    REVERSE   what cites this input?          -> corpus-correction blast radius,
                                                 retirement refusal,
                                                 superseded-input propagation

The reverse edge is what makes three obligations in three other ADRs
**enforceable rather than aspirational**:

    ADR-I009 §15.1  partition-revision citation constraint
    ADR-I010 §11    "Retirement requires zero citations" (Feature Store)
    ADR-I011 LG5    a Specification cited by a sealed artifact can never be
                    retired or made unresolvable (Label Store) -- DI7

Both upstream stores' `retire_specification()` shipped with a docstring saying
the check was "deferred to whichever future milestone builds the index". This
is that milestone (T-6).

WHAT COUNTS AS A CITATION THAT BLOCKS RETIREMENT
    ADR-I010 §11 says "cited by any **sealed** artifact". A Declared dataset
    that names a feature does NOT pin it: that dataset is not citable (§11,
    "nothing may reference a dataset before seal") and may still be Rejected.
    So `is_cited()` considers only datasets in a CITABLE phase by default --
    Sealed and Superseded, the latter because §10.2 keeps a superseded dataset
    resolvable and citable by its existing citations. `include_phases` can
    widen this for blast-radius analysis, where an unsealed dataset's inputs
    are still worth knowing about.

DEPENDENCY DIRECTION (LR-constraint preserved)
    The upstream stores do NOT import this module. `retire_specification()` in
    each store accepts an optional `citation_check` callable and refuses when
    it reports the specification is cited. `make_citation_check()` here builds
    that callable. The Dataset Builder therefore depends on nothing upstream,
    and the upstream stores depend on nothing downstream -- the authority is
    injected, not imported. Roadmap J7 authorises the upstream edit; this keeps
    it to one optional parameter per store with a backward-compatible default.

APPEND-ONLY
    Citations are facts about what a declaration said. They are never updated
    and never deleted; both are blocked at the database layer. A dataset that
    is Rejected or Retired keeps its citation rows -- the record of what it
    would have cited is itself evidence.

SCOPE (WBS 4.2 only)
    Not here: reference *resolution* against the upstream stores and the
    RF1-RF6 refusals (WBS 4.3); superseded-input flag propagation (ADR-I011
    §10.3, a Label Store concern); `--as-of` reconstruction (WBS 4.11).
    This module records and answers citations. It does not decide whether a
    cited hash resolves to an Active specification -- that needs the upstream
    registries and is the substance of Resolution.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from dataset_builder.registry import (  # noqa: F401 -- CITABLE_PHASES is re-exported by intent
    CITABLE_PHASES,
    DatasetRegistryError,
    NotADatasetBuilderStore,
    PHASES,
    _assert_dataset_builder_schema,
)

# ---------------------------------------------------------------------------
# Citation kinds (ADR-I012 §7.2 reference classes)
# ---------------------------------------------------------------------------

#: The three upstream reference classes a declaration may cite. `evidence`
#: covers partition revisions (ADR-I009 §15.1); `feature` and `label` are
#: Specification hashes (ADR-I010 §8, ADR-I011 §15 DI2).
CITED_KINDS: frozenset[str] = frozenset({"evidence", "feature", "label"})

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS citations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_hash  TEXT NOT NULL,
    cited_kind    TEXT NOT NULL,
    cited_id      TEXT NOT NULL,
    cited_detail  TEXT,
    recorded_at   TEXT NOT NULL,
    UNIQUE (dataset_hash, cited_kind, cited_id),
    CHECK (cited_kind IN ('evidence', 'feature', 'label')),
    CHECK (length(dataset_hash) > 0),
    CHECK (length(cited_id) > 0),
    FOREIGN KEY (dataset_hash) REFERENCES dataset_specifications(spec_hash)
);

CREATE INDEX IF NOT EXISTS idx_citations_forward
    ON citations(dataset_hash);

-- The reverse edge. §6.1 calls this the index's most consequential function,
-- so it gets its own covering index rather than relying on a scan.
CREATE INDEX IF NOT EXISTS idx_citations_reverse
    ON citations(cited_kind, cited_id);

CREATE TRIGGER IF NOT EXISTS trg_citations_no_update
BEFORE UPDATE ON citations
BEGIN
    SELECT RAISE(ABORT, 'citations is append-only: UPDATE forbidden');
END;

CREATE TRIGGER IF NOT EXISTS trg_citations_no_delete
BEFORE DELETE ON citations
BEGIN
    SELECT RAISE(ABORT, 'citations is append-only: DELETE forbidden');
END;
"""


class CitationIndexError(DatasetRegistryError):
    """Base class for citation-index failures."""


class OrphanCitationError(CitationIndexError):
    """A citation names a dataset that does not exist in this registry.

    The forward direction's orphan: an edge whose tail is missing. Refused at
    write time by the foreign key, and re-checked in Python so the message
    names the dataset rather than surfacing a bare FOREIGN KEY constraint.
    """


class CitedSpecificationError(CitationIndexError):
    """Refusing to retire a specification that a sealed dataset cites.

    ADR-I010 §11 / ADR-I011 LG5 / DG-5. This is the reverse edge doing the one
    job the three ADRs each said only a citation index could do.
    """

    def __init__(self, cited_kind: str, cited_id: str, citing: list[str]) -> None:
        self.cited_kind = cited_kind
        self.cited_id = cited_id
        self.citing = list(citing)
        super().__init__(
            f"refusing to retire {cited_kind} {cited_id[:12]}...: cited by "
            f"{len(citing)} sealed dataset(s): "
            + ", ".join(h[:12] + "..." for h in citing[:5])
            + ("" if len(citing) <= 5 else f" (+{len(citing) - 5} more)")
        )


@dataclass(frozen=True)
class Citation:
    dataset_hash: str
    cited_kind: str
    cited_id: str
    cited_detail: str | None
    recorded_at: str


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def init_db(db_path: Path | str) -> None:
    """Create the citation tables. Idempotent.

    Requires the Dataset Registry schema to exist first -- a citation with no
    registry to point at is meaningless, and the foreign key would be
    unenforceable. `db_path` is required; there is no default (LR4 discipline
    carried over from `registry.py`).
    """
    _assert_dataset_builder_schema(db_path)
    con = sqlite3.connect(db_path)
    try:
        con.executescript(_SCHEMA_SQL)
        con.commit()
    finally:
        con.close()


def _connect(db_path: Path | str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _assert_citation_schema(db_path: Path | str) -> None:
    _assert_dataset_builder_schema(db_path)
    con = sqlite3.connect(db_path)
    try:
        names = {
            r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    finally:
        con.close()
    if "citations" not in names:
        raise NotADatasetBuilderStore(
            f"{db_path} has no citation index (missing table 'citations'). "
            f"Call citation_index.init_db() first. Refusing to write."
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dataset_exists(con: sqlite3.Connection, dataset_hash: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM dataset_specifications WHERE spec_hash = ?", (dataset_hash,)
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# Recording citations
# ---------------------------------------------------------------------------

def record_citation(
    db_path: Path | str,
    dataset_hash: str,
    cited_kind: str,
    cited_id: str,
    *,
    cited_detail: str | None = None,
) -> bool:
    """Record one edge. Returns True if written, False if already present.

    Idempotent by `(dataset_hash, cited_kind, cited_id)`: re-recording the same
    edge is a no-op, not a duplicate row and not an error. A declaration that
    legitimately cites the same label at two maturities is two different
    `cited_id`s upstream, so collapsing on the triple loses nothing.
    """
    _assert_citation_schema(db_path)
    if cited_kind not in CITED_KINDS:
        raise CitationIndexError(
            f"cited_kind must be one of {sorted(CITED_KINDS)}, got {cited_kind!r}"
        )
    if not str(dataset_hash).strip():
        raise CitationIndexError("dataset_hash is required")
    if not str(cited_id).strip():
        raise CitationIndexError(
            f"cited_id is required (a blank {cited_kind} reference is an orphan "
            f"in the reverse direction: nothing could ever match it)"
        )

    con = _connect(db_path)
    try:
        if not _dataset_exists(con, dataset_hash):
            raise OrphanCitationError(
                f"dataset {dataset_hash[:12]}... is not in this registry; refusing "
                f"to record a citation from a dataset that does not exist"
            )
        try:
            con.execute(
                "INSERT INTO citations "
                "(dataset_hash, cited_kind, cited_id, cited_detail, recorded_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (dataset_hash, cited_kind, cited_id, cited_detail, _utc_now()),
            )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc).upper():
                return False
            raise
        con.commit()
        return True
    finally:
        con.close()


def index_declaration(db_path: Path | str, dataset_hash: str, declaration: dict) -> int:
    """Derive and record every citation a declaration makes. Returns the count
    of edges newly written.

    Reads the three §7.2 reference classes:

        evidence_references.sources          -> kind 'evidence'
        feature_references[]                 -> kind 'feature'
        label_references[].spec_hash         -> kind 'label'
                          .maturity_requirement -> cited_detail

    A label's maturity requirement is carried as `cited_detail` because
    ADR-I011 §15 DI2 makes it part of the reference, not an annotation on it.
    Evidence sources carry their pinned partition revision as detail when the
    declaration provides one.

    Idempotent: re-indexing the same declaration writes nothing new.
    """
    _assert_citation_schema(db_path)
    if not isinstance(declaration, dict):
        raise CitationIndexError("declaration must be a mapping")

    written = 0

    evidence = declaration.get("evidence_references")
    if isinstance(evidence, dict):
        revisions = evidence.get("partition_revision_sets")
        revisions = revisions if isinstance(revisions, dict) else {}
        for source in evidence.get("sources") or []:
            if not str(source).strip():
                raise CitationIndexError(
                    "evidence_references.sources contains a blank entry; a blank "
                    "reference is an orphan in the reverse direction"
                )
            detail = revisions.get(source)
            written += int(record_citation(
                db_path, dataset_hash, "evidence", str(source),
                cited_detail=None if detail is None else str(detail),
            ))

    for ref in declaration.get("feature_references") or []:
        if not str(ref).strip():
            raise CitationIndexError(
                "feature_references contains a blank entry; a blank reference is "
                "an orphan in the reverse direction"
            )
        written += int(record_citation(db_path, dataset_hash, "feature", str(ref)))

    for ref in declaration.get("label_references") or []:
        if not isinstance(ref, dict):
            raise CitationIndexError(
                f"label_references entries must be mappings, got {type(ref).__name__}"
            )
        spec_hash = ref.get("spec_hash")
        if not str(spec_hash or "").strip():
            raise CitationIndexError(
                "label_references entry has no spec_hash; a blank reference is an "
                "orphan in the reverse direction"
            )
        maturity = ref.get("maturity_requirement")
        written += int(record_citation(
            db_path, dataset_hash, "label", str(spec_hash),
            cited_detail=None if maturity is None else str(maturity),
        ))

    return written


# ---------------------------------------------------------------------------
# FORWARD — what does this dataset cite?
# ---------------------------------------------------------------------------

def cites(db_path: Path | str, dataset_hash: str, *, kind: str | None = None) -> list[Citation]:
    """Forward edge (§6.1): every input this dataset cites.

    Used for rematerialisation and provenance closure. Returns [] for a dataset
    with no recorded citations -- which is not the same as a dataset that does
    not exist; use `registry.get_specification()` to distinguish, or
    `find_orphans()` to detect the pathological case.
    """
    if kind is not None and kind not in CITED_KINDS:
        raise CitationIndexError(f"unknown kind {kind!r}")
    con = _connect(db_path)
    try:
        if kind is None:
            rows = con.execute(
                "SELECT dataset_hash, cited_kind, cited_id, cited_detail, recorded_at "
                "FROM citations WHERE dataset_hash = ? ORDER BY cited_kind, cited_id",
                (dataset_hash,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT dataset_hash, cited_kind, cited_id, cited_detail, recorded_at "
                "FROM citations WHERE dataset_hash = ? AND cited_kind = ? "
                "ORDER BY cited_id",
                (dataset_hash, kind),
            ).fetchall()
    finally:
        con.close()
    return [Citation(*tuple(r)) for r in rows]


# ---------------------------------------------------------------------------
# REVERSE — what cites this input?
# ---------------------------------------------------------------------------

def cited_by(
    db_path: Path | str,
    cited_kind: str,
    cited_id: str,
    *,
    include_phases: Iterable[str] | None = None,
) -> list[str]:
    """Reverse edge (§6.1): every dataset citing this input.

    `include_phases` defaults to `CITABLE_PHASES` (Sealed, Superseded) --
    the set ADR-I010 §11 means by "cited by any sealed artifact". Pass a wider
    set for blast-radius analysis, where an unsealed dataset's dependency on a
    corrected corpus revision still matters.

    Returned in a deterministic order so a blast-radius report is diffable.
    """
    if cited_kind not in CITED_KINDS:
        raise CitationIndexError(
            f"cited_kind must be one of {sorted(CITED_KINDS)}, got {cited_kind!r}"
        )
    phases = set(CITABLE_PHASES if include_phases is None else include_phases)
    unknown = phases - PHASES
    if unknown:
        raise CitationIndexError(f"unknown phase(s): {sorted(unknown)}")
    if not phases:
        return []

    placeholders = ",".join("?" for _ in phases)
    con = _connect(db_path)
    try:
        rows = con.execute(
            f"SELECT c.dataset_hash FROM citations c "
            f"JOIN dataset_specifications d ON d.spec_hash = c.dataset_hash "
            f"WHERE c.cited_kind = ? AND c.cited_id = ? "
            f"AND d.phase IN ({placeholders}) "
            f"ORDER BY c.dataset_hash",
            (cited_kind, cited_id, *sorted(phases)),
        ).fetchall()
    finally:
        con.close()
    return [r["dataset_hash"] for r in rows]


def is_cited(
    db_path: Path | str,
    cited_kind: str,
    cited_id: str,
    *,
    include_phases: Iterable[str] | None = None,
) -> bool:
    """Whether any citable dataset cites this input. The predicate behind the
    retirement refusal (ADR-I010 §11, ADR-I011 LG5, DG-5)."""
    return bool(cited_by(db_path, cited_kind, cited_id, include_phases=include_phases))


def blast_radius(db_path: Path | str, cited_kind: str, cited_id: str) -> dict:
    """Every dataset touching this input, grouped by phase (§6.1: "corpus-
    correction blast radius"). Unlike `cited_by`, this deliberately spans all
    phases -- when a corpus revision is corrected, a Declared dataset that
    depends on it still needs re-resolution before it can seal."""
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT d.phase, c.dataset_hash FROM citations c "
            "JOIN dataset_specifications d ON d.spec_hash = c.dataset_hash "
            "WHERE c.cited_kind = ? AND c.cited_id = ? "
            "ORDER BY d.phase, c.dataset_hash",
            (cited_kind, cited_id),
        ).fetchall()
    finally:
        con.close()
    out: dict[str, list[str]] = {}
    for row in rows:
        out.setdefault(row["phase"], []).append(row["dataset_hash"])
    return out


# ---------------------------------------------------------------------------
# Retirement refusal — the injected authority (T-6)
# ---------------------------------------------------------------------------

def make_citation_check(
    db_path: Path | str, cited_kind: str
) -> Callable[[str], bool]:
    """Build the callable that an upstream store's `retire_specification()`
    consults.

    Injected rather than imported: the Feature Store and Label Store never
    reference this package, so the dependency arrow keeps pointing one way.
    The upstream signature is `citation_check(spec_hash) -> bool`, where True
    means "cited, refuse to retire".
    """
    if cited_kind not in CITED_KINDS:
        raise CitationIndexError(
            f"cited_kind must be one of {sorted(CITED_KINDS)}, got {cited_kind!r}"
        )

    def _check(spec_hash: str) -> bool:
        return is_cited(db_path, cited_kind, spec_hash)

    return _check


def assert_retirable(db_path: Path | str, cited_kind: str, cited_id: str) -> None:
    """Raise `CitedSpecificationError` if a citable dataset cites this input.

    The explicit form, for callers that want the refusal to carry the citing
    dataset hashes rather than a bare boolean.
    """
    citing = cited_by(db_path, cited_kind, cited_id)
    if citing:
        raise CitedSpecificationError(cited_kind, cited_id, citing)


# ---------------------------------------------------------------------------
# Integrity — orphans in both directions
# ---------------------------------------------------------------------------

def find_orphans(db_path: Path | str) -> list[dict]:
    """Citations whose citing dataset is absent from the registry.

    Should always be empty: the foreign key and `record_citation()`'s explicit
    check both prevent it. This exists so the invariant is *auditable* rather
    than merely asserted -- a database restored from an older backup, or
    hand-edited, can be checked without trusting the code that wrote it.
    """
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT c.dataset_hash, c.cited_kind, c.cited_id FROM citations c "
            "LEFT JOIN dataset_specifications d ON d.spec_hash = c.dataset_hash "
            "WHERE d.spec_hash IS NULL "
            "ORDER BY c.dataset_hash, c.cited_kind, c.cited_id"
        ).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


def verify_bidirectional_consistency(db_path: Path | str) -> tuple[bool, list[str]]:
    """Every forward edge must be answerable in reverse, and vice versa.

    The two directions are one table, so inconsistency is structurally
    impossible -- which is the point of storing them once rather than twice.
    This verifies that property holds rather than assuming it: for every
    recorded edge, the dataset must appear in `cites()` and the input must
    appear in `cited_by()` under the all-phases widening.
    """
    errors: list[str] = []

    for orphan in find_orphans(db_path):
        errors.append(
            f"orphan citation: dataset {orphan['dataset_hash'][:12]}... is absent "
            f"from the registry but cites {orphan['cited_kind']} {orphan['cited_id']}"
        )

    con = _connect(db_path)
    try:
        edges = con.execute(
            "SELECT dataset_hash, cited_kind, cited_id FROM citations"
        ).fetchall()
        fk_violations = con.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        con.close()

    if fk_violations:
        errors.append(f"foreign key violations: {len(fk_violations)}")

    for edge in edges:
        forward = {(c.cited_kind, c.cited_id) for c in cites(db_path, edge["dataset_hash"])}
        if (edge["cited_kind"], edge["cited_id"]) not in forward:
            errors.append(
                f"forward edge missing: {edge['dataset_hash'][:12]}... -> "
                f"{edge['cited_kind']}:{edge['cited_id']}"
            )
        reverse = cited_by(
            db_path, edge["cited_kind"], edge["cited_id"], include_phases=PHASES
        )
        if edge["dataset_hash"] not in reverse:
            errors.append(
                f"reverse edge missing: {edge['cited_kind']}:{edge['cited_id']} <- "
                f"{edge['dataset_hash'][:12]}..."
            )

    return (not errors), errors


# ---------------------------------------------------------------------------
# CLI — inspection only; --db required, never defaulted
# ---------------------------------------------------------------------------

def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bidirectional Citation Index (ADR-I012 §6.1). Read-only.",
    )
    parser.add_argument(
        "--db", required=True,
        help="Path to the Dataset Builder store. Required: there is no default.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--cites", metavar="DATASET_HASH",
                       help="Forward: what does this dataset cite?")
    group.add_argument("--cited-by", nargs=2, metavar=("KIND", "ID"),
                       help="Reverse: what cites this input?")
    group.add_argument("--blast-radius", nargs=2, metavar=("KIND", "ID"),
                       help="Every dataset touching this input, by phase.")
    group.add_argument("--verify", action="store_true",
                       help="Orphan and bidirectional-consistency check.")
    parser.add_argument("--all-phases", action="store_true",
                        help="With --cited-by: include non-citable datasets.")
    args = parser.parse_args(argv)

    try:
        _assert_citation_schema(args.db)
    except NotADatasetBuilderStore as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.cites:
        for c in cites(args.db, args.cites):
            detail = f"  ({c.cited_detail})" if c.cited_detail else ""
            print(f"  {c.cited_kind:<9} {c.cited_id}{detail}")
        return 0

    if args.cited_by:
        kind, cited_id = args.cited_by
        try:
            hashes = cited_by(
                args.db, kind, cited_id,
                include_phases=PHASES if args.all_phases else None,
            )
        except CitationIndexError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        for h in hashes:
            print(f"  {h}")
        return 0

    if args.blast_radius:
        kind, cited_id = args.blast_radius
        try:
            groups = blast_radius(args.db, kind, cited_id)
        except CitationIndexError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        for phase in sorted(groups):
            print(f"  {phase}:")
            for h in groups[phase]:
                print(f"    {h}")
        return 0

    if args.verify:
        ok, errors = verify_bidirectional_consistency(args.db)
        if ok:
            print("citation index OK")
            return 0
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        return 1

    con = _connect(args.db)
    try:
        rows = con.execute(
            "SELECT cited_kind, COUNT(*) AS n, COUNT(DISTINCT cited_id) AS distinct_ids "
            "FROM citations GROUP BY cited_kind ORDER BY cited_kind"
        ).fetchall()
        total = con.execute("SELECT COUNT(*) AS n FROM citations").fetchone()["n"]
    finally:
        con.close()
    print(f"Citation index: {total} edge(s)")
    for row in rows:
        print(f"  {row['cited_kind']:<9} {row['n']:>5} edge(s), "
              f"{row['distinct_ids']} distinct input(s)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
