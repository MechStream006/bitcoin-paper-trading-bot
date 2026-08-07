"""Dataset Registry — ADR-I012 §6, §7, §10, §11, §12 Tier A.

The Registry is the authority on **what datasets exist and what they bind**.
It holds specifications and their lifecycle state, and **never holds dataset
content** (§6). A Dataset Specification is the unit of identity; a Snapshot is
bytes. Deleting every Snapshot loses nothing.

WHAT THIS MODULE OWNS (WBS 4.1)
    - Content-addressed Dataset Specifications (§6: hash of the declaration).
    - The seven-phase lifecycle and its allowed arcs (§11).
    - Tier A structural validation over every §7.2 mandatory field (§12).
    - Append-only storage with tamper enforcement at the DATABASE layer, so a
      sealed record cannot be edited even by a direct sqlite3 connection.

WHAT THIS MODULE DELIBERATELY DOES NOT OWN
    - The citation index (§6.1) -- WBS 4.2.
    - Reference resolution against the Feature/Label/Evidence stores, and the
      RF1-RF6 refusals (§8.1) -- WBS 4.3. Tier A here therefore validates the
      *shape and internal consistency* of a declaration, not whether a cited
      hash resolves to an Active specification in another store. That check
      needs the upstream registries and is the substance of Resolution.
    - Tier B (temporal), Tier C (admissibility) and Tier D (statistical
      hygiene) -- WBS 4.4.
    - Sealing receipts (§13) -- WBS 4.5. `seal()` here performs the mechanical
      arc only; the human-gated governance receipt is 4.5's scope.
    - Materialisation (§8.2) -- WBS 4.8.

The transition functions below are therefore *mechanical arcs*: they enforce
the state machine and refuse illegal moves, but they do not themselves decide
whether the substantive gate was met. This mirrors the Label Store's
`retire_specification()`, which performs the transition and explicitly defers
its citation check to this very ADR.

ASSERT, NEVER REPAIR (IQROS-P3). Tier A reports by name and refuses. It never
defaults a missing field, never widens a window, never drops a duplicate.
"No field is defaultable" (§7.2).

SINGLE WRITER (IQROS-P2). One process writes `dataset_builder.db`. There is no
default database path anywhere in this module or its CLI; a caller must always
say which database it means, and a schema assertion refuses any database that
is not a Dataset Builder store before a single byte is written (LR4).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Lifecycle (ADR-I012 §11)
# ---------------------------------------------------------------------------

PHASES: frozenset[str] = frozenset({
    "Declared", "Resolved", "Validated", "Sealed",
    "Superseded", "Retired", "Rejected",
})

#: Citable from Sealed only. "Nothing may reference a dataset before seal."
CITABLE_PHASES: frozenset[str] = frozenset({"Sealed", "Superseded"})

TERMINAL_PHASES: frozenset[str] = frozenset({"Rejected", "Superseded", "Retired"})

# Transcribed from §11's state diagram. Every arc it draws, and no other.
ALLOWED_TRANSITIONS: frozenset[tuple[str, str]] = frozenset({
    ("Declared", "Resolved"),
    ("Declared", "Rejected"),
    ("Resolved", "Validated"),
    ("Resolved", "Rejected"),
    ("Validated", "Sealed"),
    ("Validated", "Rejected"),
    ("Sealed", "Superseded"),
    ("Sealed", "Retired"),
})

# §10.1 change classification. Recorded on a successor at supersession time so
# the refinement/redefinition boundary is explicit rather than inferred.
CHANGE_CLASSES: frozenset[str] = frozenset({"extension", "refinement", "redefinition"})

# ---------------------------------------------------------------------------
# Mandatory declaration content (ADR-I012 §7.2)
# ---------------------------------------------------------------------------

#: Top-level field classes. Every one is mandatory; none is defaultable.
MANDATORY_FIELDS: tuple[str, ...] = (
    "intent",
    "temporal_scope",
    "conditioning",
    "evidence_references",
    "feature_references",
    "label_references",
    "sealed_region_exclusions",
    "admissibility_floor",
    "lane",
)

#: Mandatory keys within each mapping-valued field class.
MANDATORY_SUBFIELDS: dict[str, tuple[str, ...]] = {
    "intent": ("research_question", "declaring_actor", "rationale"),
    "temporal_scope": (
        "window_start", "window_end", "as_of", "timezone", "calendar_convention",
    ),
    "conditioning": ("statement",),
    "evidence_references": (
        "sources", "partition_revision_sets",
        "required_certification_verdict", "required_lane",
    ),
}

#: Mandatory keys on every entry of `label_references` (§7.2, ADR-I011 §15 DI2:
#: a label reference without a maturity requirement is not a reference).
MANDATORY_LABEL_REF_KEYS: tuple[str, ...] = ("spec_hash", "maturity_requirement")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS dataset_specifications (
    spec_hash         TEXT PRIMARY KEY,
    lineage           TEXT NOT NULL,
    version_ordinal   INTEGER NOT NULL,
    declaration_json  TEXT NOT NULL,
    phase             TEXT NOT NULL,
    declared_by       TEXT NOT NULL,
    declared_at       TEXT NOT NULL,
    rejection_reason  TEXT,
    superseded_by     TEXT,
    change_class      TEXT,
    CHECK (phase IN ('Declared','Resolved','Validated','Sealed',
                     'Superseded','Retired','Rejected'))
);

CREATE INDEX IF NOT EXISTS idx_dataset_specifications_lineage
    ON dataset_specifications(lineage, version_ordinal);

CREATE INDEX IF NOT EXISTS idx_dataset_specifications_phase
    ON dataset_specifications(phase);

-- Storage-level immutability (IQROS S6, §6 "Integrity"). The declaration and
-- its identity are frozen for the row's entire life; the phase may advance
-- only along the arcs §11 draws. A Sealed record is editable in exactly one
-- respect -- advancing to Superseded or Retired -- and in no other, which is
-- what "mutation of any sealed record is refused" (§6.2) means in practice.
CREATE TRIGGER IF NOT EXISTS trg_dataset_specifications_guard_update
BEFORE UPDATE ON dataset_specifications
BEGIN
    SELECT CASE
        WHEN NEW.spec_hash IS NOT OLD.spec_hash
            THEN RAISE(ABORT, 'DATASET_TAMPER: spec_hash is immutable')
        WHEN NEW.declaration_json IS NOT OLD.declaration_json
            THEN RAISE(ABORT, 'DATASET_TAMPER: declaration is immutable once written')
        WHEN NEW.lineage IS NOT OLD.lineage
            THEN RAISE(ABORT, 'DATASET_TAMPER: lineage is immutable')
        WHEN NEW.version_ordinal IS NOT OLD.version_ordinal
            THEN RAISE(ABORT, 'DATASET_TAMPER: version_ordinal is immutable')
        WHEN NEW.declared_by IS NOT OLD.declared_by
            THEN RAISE(ABORT, 'DATASET_TAMPER: declared_by is immutable')
        WHEN NEW.declared_at IS NOT OLD.declared_at
            THEN RAISE(ABORT, 'DATASET_TAMPER: declared_at is immutable')
        WHEN OLD.phase = 'Sealed' AND NEW.phase NOT IN ('Superseded', 'Retired')
            THEN RAISE(ABORT,
                'DATASET_TAMPER: a Sealed specification may only advance to Superseded or Retired')
        WHEN OLD.phase IN ('Rejected', 'Retired', 'Superseded')
             AND NEW.phase IS NOT OLD.phase
            THEN RAISE(ABORT, 'DATASET_TAMPER: terminal phase may not be left')
    END;
END;

CREATE TRIGGER IF NOT EXISTS trg_dataset_specifications_no_delete
BEFORE DELETE ON dataset_specifications
BEGIN
    SELECT RAISE(ABORT, 'dataset_specifications is append-only: DELETE forbidden');
END;
"""

_REQUIRED_TABLES: frozenset[str] = frozenset({"dataset_specifications"})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class DatasetRegistryError(ValueError):
    """Base class for Dataset Registry construction and validation failures."""


class SpecificationRejected(DatasetRegistryError):
    """Tier A refused the declaration. A permanent negative record was written
    (§12, IQROS-P15) -- rejection is recorded, never silently discarded."""

    def __init__(self, spec_hash: str, errors: list[str]) -> None:
        self.spec_hash = spec_hash
        self.errors = list(errors)
        super().__init__(
            f"declaration rejected ({len(errors)} Tier A failure(s)): "
            + "; ".join(errors)
        )


class UnknownSpecificationError(DatasetRegistryError):
    """No specification with the given hash exists in this registry."""


class TransitionError(DatasetRegistryError):
    """An attempted lifecycle move is not an arc §11 draws, or the row was not
    in the expected phase at write time."""


class NotADatasetBuilderStore(DatasetRegistryError):
    """LR4: refusing to write to a database that is not a Dataset Builder."""


# ---------------------------------------------------------------------------
# Content addressing (§6 "Addressing", IQROS-P5)
# ---------------------------------------------------------------------------

def canonical_json(obj: Any) -> bytes:
    """Deterministic serialization. Byte-identical to the convention used by
    the Feature Store, Label Store and governance receipt ledger, so a hash
    computed here means the same thing everywhere on the platform."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def content_hash(obj: Any) -> str:
    """SHA-256 over the canonical serialization."""
    return hashlib.sha256(canonical_json(obj)).hexdigest()


def declaration_hash(declaration: dict) -> str:
    """The specification's identity: the hash of its declaration (§10). Any
    declared field changing produces a different dataset, by construction."""
    return content_hash(declaration)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def _assert_dataset_builder_schema(db_path: Path | str) -> None:
    """Refuse to write to anything that is not a Dataset Builder store (LR4).

    Runs before any write. A registry pointed at the Label Store, the trading
    database, or an unrelated file must fail loudly rather than create stray
    tables in it.
    """
    path = Path(db_path)
    if not path.exists():
        raise NotADatasetBuilderStore(
            f"{path} does not exist. Call init_db() explicitly to create a new "
            f"Dataset Builder store; this module never creates one implicitly."
        )
    con = sqlite3.connect(path)
    try:
        names = {
            r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    finally:
        con.close()
    missing = _REQUIRED_TABLES - names
    if missing:
        raise NotADatasetBuilderStore(
            f"{path} does not look like a Dataset Builder database "
            f"(missing table(s): {', '.join(sorted(missing))}). Refusing to write."
        )


def init_db(db_path: Path | str) -> None:
    """Create the schema. Idempotent. `db_path` is required -- there is no
    default location, so no caller can write to the wrong store by omission."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.executescript(_SCHEMA_SQL)
        con.commit()
    finally:
        con.close()


def _connect(db_path: Path | str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Tier A — structural validation (ADR-I012 §12)
# ---------------------------------------------------------------------------

def _is_blank(value: Any) -> bool:
    """A present-but-empty field is a missing field. `0` and `False` are
    legitimate values and are never treated as blank."""
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, (list, tuple, dict)) and len(value) == 0:
        return True
    return False


def validate_tier_a(declaration: Any) -> tuple[bool, list[str]]:
    """Tier A structural validation over every §7.2 mandatory field.

    Returns `(ok, errors)`. Every failure names the field that failed, so a
    rejection is actionable without reading this source. Errors accumulate --
    a caller sees every problem at once rather than fixing them one per run.

    Scope note: §12's Tier A also requires that references be "resolvable and
    Active at as_of" and the window be "within corpus coverage". Both need the
    upstream stores and the corpus, which Resolution (WBS 4.3) owns. What is
    checked here is everything decidable from the declaration alone.
    """
    errors: list[str] = []

    if not isinstance(declaration, dict):
        return False, [f"declaration must be a mapping, got {type(declaration).__name__}"]

    # -- presence of every top-level field class -------------------------
    for field in MANDATORY_FIELDS:
        if field not in declaration:
            errors.append(f"{field}: missing (no field is defaultable, §7.2)")
        elif _is_blank(declaration[field]):
            errors.append(f"{field}: present but empty")

    # -- mandatory sub-keys ----------------------------------------------
    for field, subkeys in MANDATORY_SUBFIELDS.items():
        value = declaration.get(field)
        if not isinstance(value, dict):
            if field in declaration and not _is_blank(value):
                errors.append(f"{field}: must be a mapping, got {type(value).__name__}")
            continue
        for sub in subkeys:
            if sub not in value:
                errors.append(f"{field}.{sub}: missing")
            elif _is_blank(value[sub]):
                errors.append(f"{field}.{sub}: present but empty")

    # -- window non-empty (§12 Tier A) -----------------------------------
    scope = declaration.get("temporal_scope")
    if isinstance(scope, dict):
        start, end = scope.get("window_start"), scope.get("window_end")
        if not _is_blank(start) and not _is_blank(end):
            if str(start) >= str(end):
                errors.append(
                    f"temporal_scope: window is empty or inverted "
                    f"(window_start={start!r} >= window_end={end!r})"
                )
        as_of = scope.get("as_of")
        if not _is_blank(as_of) and not _is_blank(end) and str(as_of) < str(end):
            errors.append(
                f"temporal_scope: as_of ({as_of!r}) precedes window_end ({end!r}); "
                f"the window would extend past the declared knowledge cutoff"
            )

    # -- list-valued reference fields ------------------------------------
    for field in ("feature_references", "label_references", "sealed_region_exclusions"):
        value = declaration.get(field)
        if field in declaration and not isinstance(value, list):
            errors.append(f"{field}: must be a list, got {type(value).__name__}")

    # -- no duplicate reference (§12 Tier A) -----------------------------
    features = declaration.get("feature_references")
    if isinstance(features, list):
        if len(features) != len({json.dumps(f, sort_keys=True) for f in features}):
            errors.append("feature_references: contains duplicate entries")

    labels = declaration.get("label_references")
    if isinstance(labels, list):
        hashes: list[str] = []
        for i, ref in enumerate(labels):
            if not isinstance(ref, dict):
                errors.append(
                    f"label_references[{i}]: must be a mapping, got {type(ref).__name__}"
                )
                continue
            for key in MANDATORY_LABEL_REF_KEYS:
                if key not in ref:
                    errors.append(f"label_references[{i}].{key}: missing")
                elif _is_blank(ref[key]):
                    errors.append(f"label_references[{i}].{key}: present but empty")
            if not _is_blank(ref.get("spec_hash")):
                hashes.append(str(ref["spec_hash"]))
        if len(hashes) != len(set(hashes)):
            errors.append("label_references: contains duplicate spec_hash entries")

    return (not errors), errors


# ---------------------------------------------------------------------------
# Declaration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SpecificationHandle:
    spec_hash: str
    lineage: str
    version_ordinal: int
    phase: str


def _next_version_ordinal(con: sqlite3.Connection, lineage: str) -> int:
    row = con.execute(
        "SELECT COALESCE(MAX(version_ordinal), 0) + 1 AS n "
        "FROM dataset_specifications WHERE lineage = ?",
        (lineage,),
    ).fetchone()
    return int(row["n"])


def declare_dataset(
    db_path: Path | str,
    declaration: dict,
    *,
    lineage: str,
    declared_by: str,
) -> SpecificationHandle:
    """Register a Dataset Specification (§7.1: the unit of identity).

    Tier A runs first. On pass the row lands in phase `Declared`. On failure
    the row lands in phase `Rejected` **with its reasons recorded** and
    `SpecificationRejected` is raised -- the rejection is persisted before the
    exception propagates, so a caller that swallows the error still cannot
    erase the negative record (§12, IQROS-P15).

    Re-declaring byte-identical content is idempotent: the same hash is
    returned and no second row is written. That is content addressing working
    as intended, not a silent overwrite.
    """
    _assert_dataset_builder_schema(db_path)

    if _is_blank(lineage):
        raise DatasetRegistryError("lineage is required (§10: the named research line)")
    if _is_blank(declared_by):
        raise DatasetRegistryError("declared_by is required (§7.2 intent: declaring actor)")

    spec_hash = declaration_hash(declaration)
    ok, errors = validate_tier_a(declaration)

    con = _connect(db_path)
    try:
        existing = con.execute(
            "SELECT spec_hash, lineage, version_ordinal, phase "
            "FROM dataset_specifications WHERE spec_hash = ?",
            (spec_hash,),
        ).fetchone()
        if existing is not None:
            handle = SpecificationHandle(
                existing["spec_hash"], existing["lineage"],
                int(existing["version_ordinal"]), existing["phase"],
            )
            if existing["phase"] == "Rejected":
                raise SpecificationRejected(spec_hash, errors or ["previously rejected"])
            return handle

        ordinal = _next_version_ordinal(con, lineage)
        phase = "Declared" if ok else "Rejected"
        con.execute(
            "INSERT INTO dataset_specifications "
            "(spec_hash, lineage, version_ordinal, declaration_json, phase, "
            " declared_by, declared_at, rejection_reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                spec_hash, lineage, ordinal,
                canonical_json(declaration).decode("utf-8"),
                phase, declared_by, _utc_now(),
                None if ok else "; ".join(errors),
            ),
        )
        con.commit()
    finally:
        con.close()

    if not ok:
        raise SpecificationRejected(spec_hash, errors)
    return SpecificationHandle(spec_hash, lineage, ordinal, "Declared")


def get_specification(db_path: Path | str, spec_hash: str) -> dict | None:
    """Resolve a specification by hash alone -- no filesystem or environment
    dependency (§6 "Resolution")."""
    con = _connect(db_path)
    try:
        row = con.execute(
            "SELECT * FROM dataset_specifications WHERE spec_hash = ?", (spec_hash,)
        ).fetchone()
    finally:
        con.close()
    if row is None:
        return None
    record = dict(row)
    record["declaration"] = json.loads(record["declaration_json"])
    return record


def get_lineage(db_path: Path | str, lineage: str) -> list[dict]:
    """Every specification in a lineage, in version order (§10)."""
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT * FROM dataset_specifications WHERE lineage = ? "
            "ORDER BY version_ordinal",
            (lineage,),
        ).fetchall()
    finally:
        con.close()
    out = []
    for row in rows:
        record = dict(row)
        record["declaration"] = json.loads(record["declaration_json"])
        out.append(record)
    return out


def is_citable(db_path: Path | str, spec_hash: str) -> bool:
    """§11: citable from Sealed. Nothing may reference a dataset before seal.
    A Superseded dataset remains citable by its existing citations (§10.2)."""
    spec = get_specification(db_path, spec_hash)
    return spec is not None and spec["phase"] in CITABLE_PHASES


# ---------------------------------------------------------------------------
# Lifecycle transitions (§11) -- mechanical arcs only
# ---------------------------------------------------------------------------

def _transition(
    db_path: Path | str,
    spec_hash: str,
    from_phase: str,
    to_phase: str,
    *,
    rejection_reason: str | None = None,
    superseded_by: str | None = None,
    change_class: str | None = None,
) -> None:
    if (from_phase, to_phase) not in ALLOWED_TRANSITIONS:
        raise TransitionError(
            f"{from_phase!r} -> {to_phase!r} is not an arc ADR-I012 §11 draws"
        )
    _assert_dataset_builder_schema(db_path)
    con = _connect(db_path)
    try:
        cur = con.execute(
            "UPDATE dataset_specifications "
            "SET phase = ?, "
            "    rejection_reason = COALESCE(?, rejection_reason), "
            "    superseded_by    = COALESCE(?, superseded_by), "
            "    change_class     = COALESCE(?, change_class) "
            "WHERE spec_hash = ? AND phase = ?",
            (to_phase, rejection_reason, superseded_by, change_class,
             spec_hash, from_phase),
        )
        if cur.rowcount != 1:
            raise TransitionError(
                f"refusing to proceed: specification {spec_hash[:12]}... was not in "
                f"phase {from_phase!r} at write time (concurrent modification, or "
                f"unknown spec_hash)"
            )
        con.commit()
    finally:
        con.close()


def mark_resolved(db_path: Path | str, spec_hash: str) -> None:
    """Declared -> Resolved. **Mechanical arc only.** The substantive gate --
    every reference bound to a hash at `as_of`, corpus revisions pinned, and
    refusals RF1/RF5/RF6 -- is WBS 4.3's Resolution stage. Calling this does
    not assert that resolution happened; 4.3 will own that assertion."""
    _transition(db_path, spec_hash, "Declared", "Resolved")


def mark_validated(db_path: Path | str, spec_hash: str) -> None:
    """Resolved -> Validated. **Mechanical arc only.** The Tier B/C/D battery
    is WBS 4.4. Tier A already ran at declaration time."""
    _transition(db_path, spec_hash, "Resolved", "Validated")


def seal(db_path: Path | str, spec_hash: str) -> None:
    """Validated -> Sealed. **Mechanical arc only** -- the human-gated sealing
    receipt (§13) is WBS 4.5.

    Sealing is what makes a dataset citable (§11). From here the declaration is
    frozen at the storage layer: the guard trigger permits no further change
    except advancing to Superseded or Retired."""
    _transition(db_path, spec_hash, "Validated", "Sealed")


def reject(db_path: Path | str, spec_hash: str, from_phase: str, reason: str) -> None:
    """-> Rejected from Declared, Resolved or Validated. A permanent negative
    record (§12, IQROS-P15); the arc out of Rejected does not exist."""
    if _is_blank(reason):
        raise DatasetRegistryError("a rejection must state its reason")
    _transition(db_path, spec_hash, from_phase, "Rejected", rejection_reason=reason)


def supersede(
    db_path: Path | str,
    spec_hash: str,
    *,
    successor_hash: str,
    change_class: str,
) -> None:
    """Sealed -> Superseded (§10.2). **Supersession never invalidates**: the
    superseded specification remains fully resolvable and citable by every
    evaluation run that already referenced it.

    `change_class` is mandatory and must be `extension` or `refinement`.
    `redefinition` is rejected here by design -- §10.1 states a redefinition is
    "a new lineage, not a new version", so recording one as a supersession
    within the same lineage would assert a comparability that does not hold.
    """
    if change_class not in CHANGE_CLASSES:
        raise DatasetRegistryError(
            f"change_class must be one of {sorted(CHANGE_CLASSES)}, got {change_class!r}"
        )
    if change_class == "redefinition":
        raise DatasetRegistryError(
            "a redefinition is a new lineage, not a supersession (§10.1). Declare "
            "the successor under its own lineage instead."
        )
    if _is_blank(successor_hash):
        raise DatasetRegistryError("successor_hash is required to supersede")
    if successor_hash == spec_hash:
        raise DatasetRegistryError("a specification cannot supersede itself")

    successor = get_specification(db_path, successor_hash)
    if successor is None:
        raise UnknownSpecificationError(
            f"successor {successor_hash[:12]}... is not in this registry"
        )
    if successor["phase"] != "Sealed":
        raise TransitionError(
            f"successor {successor_hash[:12]}... is in phase {successor['phase']!r}; "
            f"§11 requires a successor to be Sealed before it can supersede"
        )
    _transition(
        db_path, spec_hash, "Sealed", "Superseded",
        superseded_by=successor_hash, change_class=change_class,
    )


def retire(db_path: Path | str, spec_hash: str) -> None:
    """Sealed -> Retired. §6.2 additionally refuses retirement of any
    specification with a live citation; the citation index is WBS 4.2, so that
    check is deferred by name rather than silently skipped -- exactly the
    deferral the Label Store's own `retire_specification()` recorded when it
    pointed at this ADR."""
    _transition(db_path, spec_hash, "Sealed", "Retired")


# ---------------------------------------------------------------------------
# CLI -- inspection only; --db is required, never defaulted
# ---------------------------------------------------------------------------

def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dataset Registry inspection (ADR-I012 §6). Read-only.",
    )
    parser.add_argument(
        "--db", required=True,
        help="Path to the Dataset Builder store. Required: there is no default.",
    )
    parser.add_argument("--spec-hash", help="Show one specification.")
    parser.add_argument("--lineage", help="List a lineage in version order.")
    args = parser.parse_args(argv)

    try:
        _assert_dataset_builder_schema(args.db)
    except NotADatasetBuilderStore as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.spec_hash:
        spec = get_specification(args.db, args.spec_hash)
        if spec is None:
            print(f"no such specification: {args.spec_hash}", file=sys.stderr)
            return 1
        print(json.dumps(spec, indent=2, sort_keys=True))
        return 0

    if args.lineage:
        for row in get_lineage(args.db, args.lineage):
            print(f"{row['version_ordinal']:>4}  {row['phase']:<11} {row['spec_hash']}")
        return 0

    con = _connect(args.db)
    try:
        rows = con.execute(
            "SELECT phase, COUNT(*) AS n FROM dataset_specifications "
            "GROUP BY phase ORDER BY phase"
        ).fetchall()
    finally:
        con.close()
    total = sum(int(r["n"]) for r in rows)
    print(f"Dataset Registry: {total} specification(s)")
    for row in rows:
        print(f"  {row['phase']:<12} {row['n']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
