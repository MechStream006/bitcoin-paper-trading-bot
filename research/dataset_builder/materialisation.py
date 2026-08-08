"""Dataset Materialiser — ADR-I012 §8.2, §7.1, §16 L1/L2.

> §8.2: *"Given a sealed hash, the materialiser resolves the provenance
> closure, obtains feature values and label values, assembles a **Snapshot**,
> and writes a **Manifest**. It is idempotent and verifiable. A materialisation
> that does not reproduce the expected content is a **divergence event** —
> reported, never silently reconciled."*

MATERIALISATION IS OFF-LIFECYCLE
    A sealed dataset is complete whether or not any bytes exist (§7.1). This
    module therefore **never** changes a specification's phase, never writes to
    the registry's own tables, and never makes a dataset more or less citable.
    Deleting every snapshot loses nothing.

THE THREE REPRODUCIBILITY LEVELS (§16), AND WHICH TWO LAND HERE
    L1  bit-reproducible      byte-identical re-materialisation
                              requires a PINNED snapshot + deterministic
                              assembly. Applies to Training Datasets.
    L2  semantically reprod.  equivalent content from the specification,
                              requires the provenance closure + available
                              inputs. Applies to ALL sealed datasets.
    L3  historically reconst. `as_of` reconstruction -> WBS 4.11, not here.

    §16 is explicit that conflating them *"is how reproducibility claims become
    unfalsifiable"*, so the two implemented here are kept structurally apart:
    `verify_l1()` compares bytes against a pin; `rematerialise_l2()` compares a
    fresh assembly against a recorded content hash and reports divergence.

    **L1's mechanism is implemented; its Training-Dataset binding is WBS 4.10.**
    `pin_snapshot()` works today, but nothing yet declares a Training Dataset,
    so no dataset is pinned by default. Stated rather than implied.

WHY L2 DIVERGENCE IS A CORRECT OUTCOME
    §16: *"If an input revision was corrected, rematerialisation yields
    different content — and that is the **correct** outcome, surfaced as a
    divergence rather than hidden."* `rematerialise_l2()` therefore returns a
    `DivergenceEvent` rather than raising: a divergence is information, not an
    error, and the caller decides. It is recorded in the append-only
    `materialisations` table either way, so a divergence cannot be discovered
    and then quietly forgotten.

DETERMINISM IS THE WHOLE GAME
    "Repeated materialisation of the same sealed dataset must produce
    byte-identical outputs." Four rules make that true rather than hoped for:

      1. Rows are emitted in a **canonical sort order** derived from the row's
         own key fields, never in provider iteration order.
      2. Columns are emitted **sorted by name**, so a provider that adds a key
         in a different order cannot change the bytes.
      3. Serialisation is canonical JSON (`sort_keys`, fixed separators, LF),
         the same convention the receipt ledger and every store already use --
         which **refuses raw IEEE floats**, because a float's repr is not
         guaranteed byte-stable. Values are normalised to decimal strings at
         the assembly boundary (`normalise_value`), with explicit sentinels
         for NaN and the infinities. This is not incidental: without it, L1
         would be a guarantee the platform could not actually keep.
      4. **No wall-clock, no run id, no environment ever enters the snapshot.**
         Those live in the *manifest*, which is deliberately not part of the
         content hash. A timestamp inside the bytes would break L1 on its very
         first re-run -- the most obvious way to make the guarantee a lie.

    `SNAPSHOT_FORMAT_VERSION` is part of the hashed payload: a change to the
    serialisation is a change to the content, and pretending otherwise would
    make two incompatible snapshots compare equal.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, Sequence, runtime_checkable

_EVIDENCE_LIB_DIR = Path(__file__).resolve().parents[1] / "evidence_library"
if str(_EVIDENCE_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_EVIDENCE_LIB_DIR))

from receipts import canonical_json, decimal_str, sha256_lf  # noqa: E402

from dataset_builder import citation_index as ci
from dataset_builder import registry as reg
from dataset_builder import sealing as sl

#: Bumped when the snapshot serialisation changes. Part of the hashed payload:
#: a serialisation change IS a content change.
SNAPSHOT_FORMAT_VERSION = "1.0.0"

#: Bumped when the assembly algorithm changes in a way that could alter
#: content for identical inputs. Recorded on every materialisation so a
#: divergence can be attributed to the engine rather than to the data.
MATERIALISER_ENGINE_VERSION = "1.0.0"

#: Phases whose datasets may be materialised. §8.2 says "given a sealed hash";
#: Superseded is included because §10.2 keeps a superseded dataset resolvable
#: and citable, and a citation that cannot be materialised is not resolvable in
#: any useful sense.
MATERIALISABLE_PHASES: frozenset[str] = frozenset({"Sealed", "Superseded"})

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS materialisations (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    spec_hash             TEXT NOT NULL,
    snapshot_sha256       TEXT NOT NULL,
    manifest_sha256       TEXT NOT NULL,
    row_count             INTEGER NOT NULL,
    engine_version        TEXT NOT NULL,
    format_version        TEXT NOT NULL,
    materialised_at       TEXT NOT NULL,
    is_pin                INTEGER NOT NULL DEFAULT 0,
    divergence_from       TEXT,
    FOREIGN KEY (spec_hash) REFERENCES dataset_specifications(spec_hash)
);

CREATE INDEX IF NOT EXISTS idx_materialisations_spec
    ON materialisations(spec_hash, materialised_at);

-- Write-once (§7.1: Snapshot and Manifest are both write-once). A
-- materialisation is a historical fact; correcting one would erase evidence of
-- a divergence, which is the one thing §8.2 forbids.
CREATE TRIGGER IF NOT EXISTS trg_materialisations_no_update
BEFORE UPDATE ON materialisations
BEGIN
    SELECT RAISE(ABORT, 'materialisations is write-once: UPDATE forbidden');
END;

CREATE TRIGGER IF NOT EXISTS trg_materialisations_no_delete
BEFORE DELETE ON materialisations
BEGIN
    SELECT RAISE(ABORT, 'materialisations is write-once: DELETE forbidden');
END;

-- The L1 pin. At most one per dataset, enforced structurally rather than by
-- remembering: a second pin would make "the" pinned snapshot ambiguous.
CREATE TABLE IF NOT EXISTS snapshot_pins (
    spec_hash        TEXT PRIMARY KEY,
    snapshot_sha256  TEXT NOT NULL,
    manifest_sha256  TEXT NOT NULL,
    pinned_at        TEXT NOT NULL,
    pinned_by        TEXT NOT NULL,
    FOREIGN KEY (spec_hash) REFERENCES dataset_specifications(spec_hash)
);

CREATE TRIGGER IF NOT EXISTS trg_snapshot_pins_no_update
BEFORE UPDATE ON snapshot_pins
BEGIN
    SELECT RAISE(ABORT, 'a snapshot pin is immutable: UPDATE forbidden');
END;

CREATE TRIGGER IF NOT EXISTS trg_snapshot_pins_no_delete
BEFORE DELETE ON snapshot_pins
BEGIN
    SELECT RAISE(ABORT, 'a snapshot pin is immutable: DELETE forbidden');
END;
"""


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class MaterialisationError(reg.DatasetRegistryError):
    """Base class for materialisation failures."""


class NotMaterialisableError(MaterialisationError):
    """Only a sealed (or superseded) dataset may be materialised (§8.2)."""


class AlreadyPinnedError(MaterialisationError):
    """A dataset already has an L1 pin. A second pin would make "the" pinned
    snapshot ambiguous, and §7.1 makes a pinned snapshot write-once."""


# ---------------------------------------------------------------------------
# Value provider
# ---------------------------------------------------------------------------

@runtime_checkable
class ValueProvider(Protocol):
    """What the materialiser needs from the world to assemble content.

    Deliberately narrow and injected, exactly as WBS 4.3's `ReferenceResolver`
    is. The Feature Store (`ADR-I010` §12) and Label Store (`ADR-I011` §7) own
    the real implementations; keeping the materialiser behind a protocol means
    determinism can be tested without either store on disk, and means this
    module holds no path to operational data (§8.3).
    """

    def feature_values(self, spec_hash: str, window: tuple[str, str]) -> Sequence[dict]:
        """Rows for one Feature Specification over the window."""

    def label_values(self, spec_hash: str, window: tuple[str, str]) -> Sequence[dict]:
        """Rows for one Label Specification over the window."""

    def evidence_rows(self, source: str, revision: str | None,
                      window: tuple[str, str]) -> Sequence[dict]:
        """Rows for one pinned evidence source over the window."""


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Snapshot:
    """The assembled bytes (§7.1). Content only — no timestamps, no run id."""
    spec_hash: str
    content: bytes
    sha256: str
    row_count: int

    def __len__(self) -> int:
        return len(self.content)


@dataclass(frozen=True)
class Manifest:
    """The self-describing record accompanying a Snapshot (§7.1).

    *"A Snapshot plus its Manifest is interpretable **without registry
    access**."* Everything needed to read the bytes is here, including the full
    provenance closure — so the manifest is deliberately larger than a pointer.
    """
    spec_hash: str
    snapshot_sha256: str
    row_count: int
    column_inventory: dict[str, list[str]]
    provenance_closure: dict
    admissibility_ceiling: dict
    format_version: str
    engine_version: str
    materialised_at: str
    verification: dict

    def to_dict(self) -> dict:
        return {
            "spec_hash": self.spec_hash,
            "snapshot_sha256": self.snapshot_sha256,
            "row_count": self.row_count,
            "column_inventory": self.column_inventory,
            "provenance_closure": self.provenance_closure,
            "admissibility_ceiling": self.admissibility_ceiling,
            "format_version": self.format_version,
            "engine_version": self.engine_version,
            "materialised_at": self.materialised_at,
            "verification": self.verification,
        }

    def to_bytes(self) -> bytes:
        return canonical_json(self.to_dict())

    @property
    def sha256(self) -> str:
        return sha256_lf(self.to_bytes())


@dataclass(frozen=True)
class DivergenceEvent:
    """A re-materialisation that did not reproduce the expected content.

    §8.2: *"reported, never silently reconciled"* (IQROS-P3). This is returned,
    not raised — under L2 a divergence is the **correct** outcome when an input
    revision was corrected (§16), so it is information for the caller, not an
    error. It is recorded either way.
    """
    spec_hash: str
    expected_sha256: str
    actual_sha256: str
    expected_rows: int
    actual_rows: int
    detected_at: str

    @property
    def row_delta(self) -> int:
        return self.actual_rows - self.expected_rows

    def __str__(self) -> str:
        return (
            f"divergence on {self.spec_hash[:12]}...: expected "
            f"{self.expected_sha256[:12]}... ({self.expected_rows} rows), got "
            f"{self.actual_sha256[:12]}... ({self.actual_rows} rows)"
        )


@dataclass
class MaterialisationResult:
    snapshot: Snapshot
    manifest: Manifest
    divergence: DivergenceEvent | None = None
    #: Which §16 level this call verified. Never both at once — conflating them
    #: is what §16 warns against.
    level_verified: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.divergence is None


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def init_db(db_path: Path | str) -> None:
    """Create the materialisation tables. Idempotent. Requires the registry
    schema first — a materialisation with no specification to point at is
    meaningless and the foreign key would be unenforceable."""
    reg._assert_dataset_builder_schema(db_path)
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


def _assert_materialisation_schema(db_path: Path | str) -> None:
    reg._assert_dataset_builder_schema(db_path)
    con = sqlite3.connect(db_path)
    try:
        names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        con.close()
    missing = {"materialisations", "snapshot_pins"} - names
    if missing:
        raise reg.NotADatasetBuilderStore(
            f"{db_path} has no materialisation tables (missing "
            f"{', '.join(sorted(missing))}). Call materialisation.init_db() "
            f"first. Refusing to write."
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Deterministic assembly
# ---------------------------------------------------------------------------

#: Sentinels for values a decimal string cannot express. JSON has no native
#: NaN/Infinity, and the platform's canonical serialisation rejects raw floats
#: outright, so these are encoded explicitly rather than silently coerced.
#: Real research content contains them -- the published lockbox record carries
#: `"sortino": NaN` -- so dropping or zeroing them would corrupt content.
NON_FINITE_SENTINELS: dict[str, str] = {
    "nan": "__NaN__",
    "inf": "__Infinity__",
    "-inf": "__-Infinity__",
}


def normalise_value(value: Any) -> Any:
    """Convert one value into a byte-stable form, recursively.

    **Why this exists at all.** `receipts.canonical_json` deliberately refuses
    raw IEEE floats (`NonDecimalNumberError`), because a float's `repr` is not
    guaranteed byte-stable across platforms or interpreter versions — and an
    unstable repr makes L1 bit-reproducibility a claim that cannot be kept.
    `decimal_str()` is the platform's boundary converter and its own docstring
    says floats are accepted *"for the caller converting at the boundary"*.
    Assembly is that boundary.

    Ints are left as ints: they are already exact and stable, and converting
    them would change the content of every existing integer column for no gain.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        if value != value:
            return NON_FINITE_SENTINELS["nan"]
        if value == float("inf"):
            return NON_FINITE_SENTINELS["inf"]
        if value == float("-inf"):
            return NON_FINITE_SENTINELS["-inf"]
        return decimal_str(value)
    if isinstance(value, dict):
        return {k: normalise_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalise_value(v) for v in value]
    return value


def _row_sort_key(row: dict) -> str:
    """A total, content-derived ordering.

    Sorting on the canonical serialisation of the whole row makes the order a
    function of content alone: it cannot depend on provider iteration order,
    dict insertion order, or which keys happen to exist. Two providers
    returning the same rows in different orders produce the same bytes, which
    is exactly what determinism requires.
    """
    return canonical_json(row).decode("utf-8")


def _normalise_rows(rows: Sequence[dict]) -> list[dict]:
    """Sort keys within each row, then sort rows canonically."""
    normalised = [
        {k: normalise_value(row[k]) for k in sorted(row)}
        for row in rows
        if isinstance(row, dict)
    ]
    normalised.sort(key=_row_sort_key)
    return normalised


def assemble_content(
    declaration: dict,
    provider: ValueProvider,
) -> tuple[dict, dict[str, list[str]], int]:
    """Assemble the deterministic payload. Returns `(payload, columns, rows)`.

    Sections are keyed by kind and then by reference, both emitted sorted, so
    the payload's shape is a function of the declaration rather than of
    iteration order. **No wall-clock, run id, or environment value is ever
    placed in the payload** — those belong to the manifest.
    """
    scope = declaration.get("temporal_scope") or {}
    window = (str(scope.get("window_start") or ""), str(scope.get("window_end") or ""))

    evidence_section: dict[str, list[dict]] = {}
    feature_section: dict[str, list[dict]] = {}
    label_section: dict[str, list[dict]] = {}

    evidence = declaration.get("evidence_references")
    if isinstance(evidence, dict):
        revisions = evidence.get("partition_revision_sets")
        revisions = revisions if isinstance(revisions, dict) else {}
        for source in sorted(str(s) for s in (evidence.get("sources") or [])):
            rows = provider.evidence_rows(source, revisions.get(source), window)
            evidence_section[source] = _normalise_rows(rows or [])

    for ref in sorted(str(r) for r in (declaration.get("feature_references") or [])):
        feature_section[ref] = _normalise_rows(provider.feature_values(ref, window) or [])

    label_refs = declaration.get("label_references") or []
    for ref in sorted(
        str(r.get("spec_hash")) for r in label_refs
        if isinstance(r, dict) and r.get("spec_hash")
    ):
        label_section[ref] = _normalise_rows(provider.label_values(ref, window) or [])

    payload = {
        "format_version": SNAPSHOT_FORMAT_VERSION,
        "spec_window": list(window),
        "evidence": evidence_section,
        "features": feature_section,
        "labels": label_section,
    }

    columns: dict[str, list[str]] = {}
    total_rows = 0
    for kind, section in (("evidence", evidence_section),
                          ("features", feature_section),
                          ("labels", label_section)):
        for ref, rows in section.items():
            total_rows += len(rows)
            keys: set[str] = set()
            for row in rows:
                keys |= set(row)
            columns[f"{kind}:{ref}"] = sorted(keys)

    return payload, columns, total_rows


# ---------------------------------------------------------------------------
# Materialisation
# ---------------------------------------------------------------------------

def _load_materialisable(db_path: Path | str, spec_hash: str) -> dict:
    spec = reg.get_specification(db_path, spec_hash)
    if spec is None:
        raise MaterialisationError(f"no such specification: {spec_hash}")
    if spec["phase"] not in MATERIALISABLE_PHASES:
        raise NotMaterialisableError(
            f"specification {spec_hash[:12]}... is in phase {spec['phase']!r}; "
            f"§8.2 materialises a SEALED dataset. Materialisation is "
            f"off-lifecycle -- it never advances a phase to make itself legal"
        )
    return spec


def materialise(
    db_path: Path | str,
    spec_hash: str,
    provider: ValueProvider,
    *,
    record: bool = True,
) -> MaterialisationResult:
    """Assemble a Snapshot and Manifest for a sealed dataset (§8.2).

    Idempotent in the sense §8.2 means: calling it twice on unchanged inputs
    produces byte-identical content. It is *not* idempotent in the sense of
    writing nothing the second time — each materialisation is recorded, because
    the record of having materialised is itself provenance.

    Never changes a phase, never writes to the registry's tables, never touches
    the citation index. Materialisation is off-lifecycle.
    """
    _assert_materialisation_schema(db_path)
    spec = _load_materialisable(db_path, spec_hash)

    payload, columns, row_count = assemble_content(spec["declaration"], provider)
    content = canonical_json(payload)
    snapshot = Snapshot(spec_hash, content, sha256_lf(content), row_count)

    sealed = sl.get_sealed(db_path, spec_hash)
    closure = (sealed or {}).get("provenance_closure") or {}
    ceiling = (sealed or {}).get("capped_admissibility") or {}
    notes: list[str] = []
    if sealed is None:
        notes.append(
            "no sealed-state row found; the manifest carries a closure derived "
            "from the registry and citation index alone (WBS 4.5 records the "
            "richer one at seal time)"
        )
        closure = _fallback_closure(db_path, spec_hash, spec)

    manifest = Manifest(
        spec_hash=spec_hash,
        snapshot_sha256=snapshot.sha256,
        row_count=row_count,
        column_inventory=columns,
        provenance_closure=closure,
        admissibility_ceiling=ceiling,
        format_version=SNAPSHOT_FORMAT_VERSION,
        engine_version=MATERIALISER_ENGINE_VERSION,
        materialised_at=_utc_now(),
        verification={
            "content_hash_algorithm": "sha256",
            "canonicalisation": "json sort_keys, separators=(',',':'), utf-8, LF",
            "row_ordering": "canonical serialisation of each row, ascending",
            "excluded_from_content": [
                "materialised_at", "engine_version", "environment",
            ],
        },
    )

    if record:
        _record_materialisation(db_path, snapshot, manifest, is_pin=False,
                                divergence_from=None)

    return MaterialisationResult(snapshot=snapshot, manifest=manifest, notes=notes)


def _fallback_closure(db_path: Path | str, spec_hash: str, spec: dict) -> dict:
    """A closure built from the registry and citation index when no sealed row
    exists. Deliberately marked, so a reader can tell it apart from WBS 4.5's
    richer sealed closure rather than assuming equivalence."""
    by_kind: dict[str, list[dict]] = {}
    for c in ci.cites(db_path, spec_hash):
        by_kind.setdefault(c.cited_kind, []).append(
            {"id": c.cited_id, "detail": c.cited_detail})
    for kind in by_kind:
        by_kind[kind].sort(key=lambda x: x["id"])
    return {
        "declaration_hash": spec_hash,
        "lineage": spec["lineage"],
        "version_ordinal": spec["version_ordinal"],
        "citations": by_kind,
        "source": "derived-at-materialisation (no sealed-state row)",
    }


def _record_materialisation(
    db_path: Path | str,
    snapshot: Snapshot,
    manifest: Manifest,
    *,
    is_pin: bool,
    divergence_from: str | None,
) -> None:
    con = _connect(db_path)
    try:
        con.execute(
            "INSERT INTO materialisations "
            "(spec_hash, snapshot_sha256, manifest_sha256, row_count, "
            " engine_version, format_version, materialised_at, is_pin, "
            " divergence_from) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (snapshot.spec_hash, snapshot.sha256, manifest.sha256,
             snapshot.row_count, MATERIALISER_ENGINE_VERSION,
             SNAPSHOT_FORMAT_VERSION, manifest.materialised_at,
             1 if is_pin else 0, divergence_from),
        )
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# L1 — bit reproducibility
# ---------------------------------------------------------------------------

def pin_snapshot(
    db_path: Path | str,
    spec_hash: str,
    result: MaterialisationResult,
    *,
    pinned_by: str,
) -> None:
    """Pin a snapshot for L1 (§16).

    A pinned snapshot is what makes byte-identity checkable rather than merely
    asserted. At most one pin per dataset — a second would make "the" pinned
    snapshot ambiguous.

    **The mechanism is complete; its Training-Dataset binding is WBS 4.10.**
    Nothing declares a Training Dataset yet, so no dataset is pinned by
    default. Pinning a Research Dataset is permitted and harmless: it simply
    grants L1 to something §16 only requires L2 of.
    """
    _assert_materialisation_schema(db_path)
    _load_materialisable(db_path, spec_hash)
    if not str(pinned_by or "").strip():
        raise MaterialisationError("pinned_by is required")
    if result.snapshot.spec_hash != spec_hash:
        raise MaterialisationError(
            f"result describes {result.snapshot.spec_hash[:12]}... but pinning "
            f"{spec_hash[:12]}...; refusing to pin another dataset's snapshot"
        )

    if get_pin(db_path, spec_hash) is not None:
        raise AlreadyPinnedError(
            f"{spec_hash[:12]}... is already pinned; a pinned snapshot is "
            f"write-once (§7.1)"
        )

    con = _connect(db_path)
    try:
        con.execute(
            "INSERT INTO snapshot_pins "
            "(spec_hash, snapshot_sha256, manifest_sha256, pinned_at, pinned_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (spec_hash, result.snapshot.sha256, result.manifest.sha256,
             _utc_now(), pinned_by),
        )
        con.commit()
    finally:
        con.close()


def get_pin(db_path: Path | str, spec_hash: str) -> dict | None:
    con = _connect(db_path)
    try:
        row = con.execute(
            "SELECT * FROM snapshot_pins WHERE spec_hash = ?", (spec_hash,)
        ).fetchone()
    finally:
        con.close()
    return None if row is None else dict(row)


def verify_l1(
    db_path: Path | str,
    spec_hash: str,
    provider: ValueProvider,
) -> MaterialisationResult:
    """L1 — bit reproducibility. Re-materialise and require **byte identity**
    with the pinned snapshot.

    Under L1 a difference is never acceptable: §16 says L1 *"exists precisely
    for the cases where different-but-correct is unacceptable: a model's
    training data."* The divergence is still returned rather than raised, so
    the caller records it — but `ok` is False and `level_verified` is `"L1"`.
    """
    pin = get_pin(db_path, spec_hash)
    if pin is None:
        raise MaterialisationError(
            f"{spec_hash[:12]}... has no pinned snapshot; L1 is only defined "
            f"against a pin (§16). Use rematerialise_l2() for a sealed dataset "
            f"without one"
        )
    result = materialise(db_path, spec_hash, provider, record=False)
    result.level_verified = "L1"
    if result.snapshot.sha256 != pin["snapshot_sha256"]:
        result.divergence = DivergenceEvent(
            spec_hash=spec_hash,
            expected_sha256=pin["snapshot_sha256"],
            actual_sha256=result.snapshot.sha256,
            expected_rows=-1,
            actual_rows=result.snapshot.row_count,
            detected_at=_utc_now(),
        )
    _record_materialisation(
        db_path, result.snapshot, result.manifest, is_pin=False,
        divergence_from=pin["snapshot_sha256"] if result.divergence else None,
    )
    return result


# ---------------------------------------------------------------------------
# L2 — semantic reproducibility
# ---------------------------------------------------------------------------

def rematerialise_l2(
    db_path: Path | str,
    spec_hash: str,
    provider: ValueProvider,
    *,
    expected_sha256: str | None = None,
) -> MaterialisationResult:
    """L2 — semantic reproducibility. Re-assemble from the provenance closure
    and compare against the most recent recorded materialisation (or an
    explicitly supplied hash).

    **A divergence here is not a failure.** §16: if an input revision was
    corrected, different content is the *correct* outcome. The event is
    returned and recorded; the caller decides what it means. Nothing is
    reconciled, repaired, or hidden (IQROS-P3).
    """
    previous = None
    if expected_sha256 is None:
        history = materialisation_history(db_path, spec_hash)
        if not history:
            raise MaterialisationError(
                f"{spec_hash[:12]}... has never been materialised; L2 compares "
                f"against a prior materialisation. Call materialise() first"
            )
        previous = history[-1]
        expected_sha256 = previous["snapshot_sha256"]

    result = materialise(db_path, spec_hash, provider, record=False)
    result.level_verified = "L2"
    if result.snapshot.sha256 != expected_sha256:
        result.divergence = DivergenceEvent(
            spec_hash=spec_hash,
            expected_sha256=expected_sha256,
            actual_sha256=result.snapshot.sha256,
            expected_rows=int(previous["row_count"]) if previous else -1,
            actual_rows=result.snapshot.row_count,
            detected_at=_utc_now(),
        )
    _record_materialisation(
        db_path, result.snapshot, result.manifest, is_pin=False,
        divergence_from=expected_sha256 if result.divergence else None,
    )
    return result


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def materialisation_history(db_path: Path | str, spec_hash: str) -> list[dict]:
    """Every materialisation of a dataset, oldest first. Append-only, so this
    is a complete record including every divergence ever detected."""
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT * FROM materialisations WHERE spec_hash = ? "
            "ORDER BY id",
            (spec_hash,),
        ).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


def divergences(db_path: Path | str, spec_hash: str | None = None) -> list[dict]:
    """Every recorded divergence. A divergence cannot be discovered and then
    quietly forgotten, because the row is append-only."""
    con = _connect(db_path)
    try:
        if spec_hash is None:
            rows = con.execute(
                "SELECT * FROM materialisations WHERE divergence_from IS NOT NULL "
                "ORDER BY id").fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM materialisations WHERE divergence_from IS NOT NULL "
                "AND spec_hash = ? ORDER BY id", (spec_hash,)).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


def verify_manifest_self_describing(manifest: Manifest) -> tuple[bool, list[str]]:
    """§7.1: *"A Snapshot plus its Manifest is interpretable without registry
    access."* Checks the manifest carries everything a reader would otherwise
    have to look up."""
    errors: list[str] = []
    d = manifest.to_dict()
    for key in ("spec_hash", "snapshot_sha256", "row_count", "column_inventory",
                "provenance_closure", "format_version", "verification"):
        if key not in d or d[key] in (None, ""):
            errors.append(f"manifest lacks {key}: not interpretable standalone")
    closure = d.get("provenance_closure") or {}
    if "citations" not in closure:
        errors.append("provenance closure carries no citations: inputs unknowable")
    return (not errors), errors
