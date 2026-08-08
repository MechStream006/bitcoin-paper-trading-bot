"""Training Dataset structure and partition roles — ADR-I012 §7.1, §15, §15.1,
§15.2, DG-7; resolves PS5 structurally.

§15 opens with the sentence that governs this module:

> *"The distinction is not degree. It is a difference in **obligation**."*

| Property | Research Dataset | Training Dataset |
|---|---|---|
| Materialisation | optional | **required and pinned** |
| Byte guarantee | none | **exact bytes recoverable** |
| Partition role | none | **train / validation / holdout** |
| Admissibility floor | any | **>= forward lane** |
| Use semantics | freely re-citable | **holdout is single-use** |
| Siblings | none | knows its siblings by hash |

THREE DATASETS, NOT THREE SLICES (§15.1)
    > *"Train, validation, and holdout are **three separate sealed datasets**,
    > not three slices of one."*

    That is why this module registers *partitions of a sibling group* rather
    than carving one dataset up. Non-overlap is then verified **across sealed
    siblings at registration**, which is what makes leakage *"structurally
    impossible, not a review finding"*.

RF4 REMAINS THE SINGLE AUTHORITY ON ADMISSIBILITY
    §15.2: *"Building a model on offline-corpus outcomes and calling the result
    forward evidence would invert the platform's entire lane discipline. The
    floor makes that inversion a **refusal (RF4)** rather than a convention."*

    This module therefore **does not implement an admissibility check.** It
    supplies the floor a Training Dataset must declare (`training_floor()`) and
    routes it into WBS 4.4's existing `validation.check_tier_c()`, where RF4
    already lives. Re-deriving the comparison here would create a second
    authority on admissibility — exactly what IQROS-P4 forbids and what
    `verify_rf4_is_sole_authority()` and an AST test both guard against.

THE FLOOR CONSTRAINS THE LANE, AND ONLY THE LANE
    §15.2 requires *"an admissibility floor at or above the forward lane"* and
    says nothing about the other four dimensions. `training_floor()` therefore
    sets `Provenance.LIVE_FORWARD` and leaves certification, readiness,
    maturity and integrity at `UNKNOWN` — the lattice's bottom rung, which
    constrains nothing. Raising them would be inventing governance the ADR did
    not write.

WHY NO REAL TRAINING DATASET EXISTS TODAY (`A-3`, `T-4`)
    All 148 labels in the Label Store are `Context`; no Forward-evidence label
    exists. Every dataset's computed meet is therefore capped at
    `OFFLINE_CORPUS`, which is strictly below the forward-lane floor — so RF4
    refuses **every** Training Dataset declaration that can be written today.
    That is not a defect and not a limitation of this module: it is the floor
    doing its job. `P4-6` requires it be *proven* by negative test, and the
    roadmap warns that *"a test that passes because the feature is missing is
    worthless; it must fail if RF4 stops refusing"* — so the tests assert the
    refusal **and** demonstrate the same declaration passing once its meet
    reaches the forward lane.

GOVERNANCE NOTE ON `T-4`
    `T-4` ("Training Dataset scope reduces to structure only") is recorded as
    **pending** in roadmap §3. It is **not** an entry condition for this
    milestone: 4.10's dependency column reads "4.5, 4.8 merged", unlike 4.6's
    which explicitly read "`T-3` accepted", and no `DS-DEC` in the register is
    assigned to `T-4`. It also *reduces* scope, so building structure-only is
    the conservative reading under either resolution — and `A-3` makes a real
    instance impossible regardless.
"""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_ADMISSIBILITY_DIR = Path(__file__).resolve().parents[1] / "admissibility"
if str(_ADMISSIBILITY_DIR) not in sys.path:
    sys.path.insert(0, str(_ADMISSIBILITY_DIR))

from lattice import (  # noqa: E402
    Admissibility, Certification, Integrity, Maturity, Provenance, Readiness,
)

from dataset_builder import materialisation as mat
from dataset_builder import registry as reg
from dataset_builder import validation as val

#: §15: train / validation / holdout. Three separate sealed datasets.
PARTITION_ROLES: tuple[str, ...] = ("train", "validation", "holdout")

#: DG-7: *"Holdout partitions are single-use. Consumption is recorded against
#: the consuming decision."* Only this role carries the restriction.
SINGLE_USE_ROLES: frozenset[str] = frozenset({"holdout"})

#: A Training Dataset may only bind a dataset that is citable AND pinned.
BINDABLE_PHASES: frozenset[str] = frozenset({"Sealed", "Superseded"})

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS training_partitions (
    spec_hash       TEXT PRIMARY KEY,
    sibling_group   TEXT NOT NULL,
    role            TEXT NOT NULL,
    window_start    TEXT NOT NULL,
    window_end      TEXT NOT NULL,
    snapshot_sha256 TEXT NOT NULL,
    registered_at   TEXT NOT NULL,
    registered_by   TEXT NOT NULL,
    CHECK (role IN ('train', 'validation', 'holdout')),
    UNIQUE (sibling_group, role),
    FOREIGN KEY (spec_hash) REFERENCES dataset_specifications(spec_hash)
);

CREATE INDEX IF NOT EXISTS idx_training_group
    ON training_partitions(sibling_group, role);

-- Immutable. A partition whose role or window could be edited after the fact
-- would let a holdout be relabelled once its result was known -- the precise
-- abuse the single-use rule exists to prevent.
CREATE TRIGGER IF NOT EXISTS trg_training_no_update
BEFORE UPDATE ON training_partitions
BEGIN
    SELECT RAISE(ABORT, 'a training partition is immutable: UPDATE forbidden');
END;

CREATE TRIGGER IF NOT EXISTS trg_training_no_delete
BEFORE DELETE ON training_partitions
BEGIN
    SELECT RAISE(ABORT, 'a training partition is immutable: DELETE forbidden');
END;

-- DG-7. Append-only for the same reason the use-count is (WBS 4.9): a
-- consumption that could be deleted would make "single-use" unenforceable.
CREATE TABLE IF NOT EXISTS holdout_consumptions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    spec_hash      TEXT NOT NULL,
    decision_ref   TEXT NOT NULL,
    consumed_at    TEXT NOT NULL,
    note           TEXT,
    CHECK (length(decision_ref) > 0),
    FOREIGN KEY (spec_hash) REFERENCES training_partitions(spec_hash)
);

CREATE INDEX IF NOT EXISTS idx_holdout_spec
    ON holdout_consumptions(spec_hash, id);

CREATE TRIGGER IF NOT EXISTS trg_holdout_no_update
BEFORE UPDATE ON holdout_consumptions
BEGIN
    SELECT RAISE(ABORT, 'holdout_consumptions is append-only: UPDATE forbidden');
END;

CREATE TRIGGER IF NOT EXISTS trg_holdout_no_delete
BEFORE DELETE ON holdout_consumptions
BEGIN
    SELECT RAISE(ABORT, 'holdout_consumptions is append-only: DELETE forbidden');
END;
"""


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class TrainingError(reg.DatasetRegistryError):
    """Base class for Training Dataset failures."""


class NotPinnedError(TrainingError):
    """§15: a Training Dataset's materialisation is *required and pinned*.

    Its defining property is exact-bytes recoverability, which an unpinned
    dataset cannot offer — there is nothing to recover *to*.
    """


class PartitionOverlapError(TrainingError):
    """§15.1: non-overlap is *"verified at seal across siblings; leakage becomes
    structurally impossible, not a review finding."*"""


class HoldoutSpentError(TrainingError):
    """DG-7: holdout partitions are single-use."""


class AdmissibilityFloorError(TrainingError):
    """The dataset does not meet the forward-lane floor (§15.2).

    Raised only after **RF4** has said so — this module never decides
    admissibility itself.
    """


# ---------------------------------------------------------------------------
# The floor (§15.2)
# ---------------------------------------------------------------------------

def training_floor() -> Admissibility:
    """The admissibility floor a Training Dataset must declare.

    Constrains **provenance only**, to `LIVE_FORWARD`. §15.2 requires *"an
    admissibility floor at or above the forward lane"* and says nothing about
    the other four dimensions, so they sit at `UNKNOWN` — the lattice's bottom
    rung, which constrains nothing. Raising them would invent governance the
    ADR did not write.
    """
    return Admissibility(
        provenance=Provenance.LIVE_FORWARD,
        certification=Certification.UNKNOWN,
        readiness=Readiness.UNKNOWN,
        maturity=Maturity.UNKNOWN,
        integrity=Integrity.UNKNOWN,
    )


def check_training_admissibility(
    declaration: dict,
    inputs,
    computed_meet: Admissibility,
) -> list[val.Finding]:
    """Ask **RF4** whether this dataset clears the forward-lane floor.

    Delegates to `validation.check_tier_c()` — the same function WBS 4.4 uses,
    called with the training floor. This module contributes the *floor*, never
    the *comparison*: RF4 stays the single authority on admissibility
    (IQROS-P4).

    Returns only the RF4 findings, so a caller cannot mistake an unrelated
    Tier C finding for an admissibility refusal.
    """
    findings = val.check_tier_c(declaration, inputs, computed_meet, training_floor())
    return [f for f in findings if f.code == "RF4"]


def verify_rf4_is_sole_authority() -> tuple[bool, list[str]]:
    """Assert this module contains no admissibility comparison of its own.

    A second authority would be free to drift from RF4 — and the drift would
    be invisible, because both would appear to be "checking admissibility".
    Guarded here at runtime and by an AST test at build time.
    """
    import ast
    import inspect
    errors: list[str] = []
    src = inspect.getsource(sys.modules[__name__])
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", "") or getattr(node.func, "attr", "")
            if name == "satisfies":
                errors.append("training.py calls satisfies() directly; RF4 must decide")
        if isinstance(node, ast.Compare):
            # a bare `ceiling.provenance < floor.provenance` style comparison
            left = getattr(node.left, "attr", "")
            if left in {"provenance", "certification", "readiness",
                        "maturity", "integrity"}:
                errors.append(
                    f"training.py compares admissibility dimension {left!r} "
                    f"directly; RF4 must decide"
                )
    return (not errors), errors


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TrainingPartition:
    spec_hash: str
    sibling_group: str
    role: str
    window_start: str
    window_end: str
    snapshot_sha256: str
    registered_at: str
    registered_by: str


@dataclass(frozen=True)
class HoldoutConsumption:
    id: int
    spec_hash: str
    decision_ref: str
    consumed_at: str
    note: str | None = None


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def init_db(db_path: Path | str) -> None:
    """Create the training tables. Idempotent. Requires the registry schema."""
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


def _assert_training_schema(db_path: Path | str) -> None:
    reg._assert_dataset_builder_schema(db_path)
    con = sqlite3.connect(db_path)
    try:
        names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        con.close()
    missing = {"training_partitions", "holdout_consumptions"} - names
    if missing:
        raise reg.NotADatasetBuilderStore(
            f"{db_path} has no training tables (missing "
            f"{', '.join(sorted(missing))}). Call training.init_db() first."
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse(value: object):
    from datetime import datetime as _dt
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return _dt.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def windows_overlap(a: tuple[str, str], b: tuple[str, str]) -> bool:
    """Inclusive overlap. Two partitions sharing a single day overlap — one
    leaked observation is still leakage."""
    a0, a1 = _parse(a[0]), _parse(a[1])
    b0, b1 = _parse(b[0]), _parse(b[1])
    if None in (a0, a1, b0, b1):
        return False
    return a0 <= b1 and b0 <= a1


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_partition(
    db_path: Path | str,
    spec_hash: str,
    *,
    sibling_group: str,
    role: str,
    registered_by: str,
    inputs=None,
    computed_meet: Admissibility,
) -> TrainingPartition:
    """Register one sealed dataset as a Training Dataset partition.

    Every §15 obligation is checked, in this order, and each refuses:

    1. **Role** is one of train / validation / holdout.
    2. **Citable** — sealed or superseded (§11: nothing references a dataset
       before seal).
    3. **Pinned** — §15 makes materialisation *required and pinned*; exact-bytes
       recoverability is meaningless without a pin (WBS 4.8).
    4. **RF4** — the forward-lane floor, decided by `validation.check_tier_c()`,
       never here. `computed_meet` is **mandatory** (`TD-4.10-1`, closed WBS
       4.12): a caller can no longer omit it to silently skip this check —
       Python itself refuses the call if it is not supplied.
    5. **Non-overlap** with every already-registered sibling (§15.1).

    A Training Dataset is therefore constructible **only** through the governed
    lifecycle: declare -> resolve -> validate -> seal -> materialise -> pin ->
    register. Skipping any step is refused by name.
    """
    _assert_training_schema(db_path)

    if role not in PARTITION_ROLES:
        raise TrainingError(
            f"role must be one of {list(PARTITION_ROLES)}, got {role!r} (§15)")
    if not str(sibling_group or "").strip():
        raise TrainingError("sibling_group is required: a partition without "
                            "siblings is not a Training Dataset (§15.1)")
    if not str(registered_by or "").strip():
        raise TrainingError("registered_by is required")

    spec = reg.get_specification(db_path, spec_hash)
    if spec is None:
        raise TrainingError(f"no such specification: {spec_hash}")
    if spec["phase"] not in BINDABLE_PHASES:
        raise TrainingError(
            f"dataset {spec_hash[:12]}... is in phase {spec['phase']!r}; a "
            f"Training Dataset partition must be sealed (§11)")

    pin = mat.get_pin(db_path, spec_hash)
    if pin is None:
        raise NotPinnedError(
            f"dataset {spec_hash[:12]}... has no pinned snapshot. §15 makes a "
            f"Training Dataset's materialisation REQUIRED and pinned — its "
            f"defining property is exact-bytes recoverability, and there is "
            f"nothing to recover to without a pin"
        )

    rf4 = check_training_admissibility(
        spec["declaration"], inputs or [], computed_meet)
    if rf4:
        raise AdmissibilityFloorError(
            f"RF4 refuses {spec_hash[:12]}... as a Training Dataset: "
            + "; ".join(f.detail for f in rf4)
        )

    scope = spec["declaration"].get("temporal_scope") or {}
    window = (str(scope.get("window_start") or ""), str(scope.get("window_end") or ""))
    if not window[0] or not window[1]:
        raise TrainingError(
            f"dataset {spec_hash[:12]}... has no usable window; non-overlap "
            f"cannot be verified (§15.1)")

    con = _connect(db_path)
    try:
        existing = con.execute(
            "SELECT * FROM training_partitions WHERE spec_hash = ?", (spec_hash,)
        ).fetchone()
        if existing is not None:
            return TrainingPartition(**dict(existing))

        siblings = con.execute(
            "SELECT * FROM training_partitions WHERE sibling_group = ?",
            (sibling_group,),
        ).fetchall()
        for sib in siblings:
            if sib["role"] == role:
                raise TrainingError(
                    f"sibling group {sibling_group!r} already has a {role!r} "
                    f"partition ({sib['spec_hash'][:12]}...)")
            if windows_overlap(window, (sib["window_start"], sib["window_end"])):
                raise PartitionOverlapError(
                    f"window {window[0]}..{window[1]} overlaps the {sib['role']!r} "
                    f"partition {sib['window_start']}..{sib['window_end']}. §15.1 "
                    f"verifies non-overlap across siblings so leakage is "
                    f"structurally impossible, not a review finding"
                )

        registered_at = _utc_now()
        con.execute(
            "INSERT INTO training_partitions "
            "(spec_hash, sibling_group, role, window_start, window_end, "
            " snapshot_sha256, registered_at, registered_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (spec_hash, sibling_group, role, window[0], window[1],
             pin["snapshot_sha256"], registered_at, registered_by),
        )
        con.commit()
    finally:
        con.close()

    return TrainingPartition(
        spec_hash=spec_hash, sibling_group=sibling_group, role=role,
        window_start=window[0], window_end=window[1],
        snapshot_sha256=pin["snapshot_sha256"],
        registered_at=registered_at, registered_by=registered_by,
    )


def get_partition(db_path: Path | str, spec_hash: str) -> TrainingPartition | None:
    con = _connect(db_path)
    try:
        row = con.execute(
            "SELECT * FROM training_partitions WHERE spec_hash = ?", (spec_hash,)
        ).fetchone()
    finally:
        con.close()
    return None if row is None else TrainingPartition(**dict(row))


def sibling_group(db_path: Path | str, group: str) -> list[TrainingPartition]:
    """Every partition in a group, in role order. §15: a Training Dataset
    *"knows its sibling partitions by hash"*."""
    order = {r: i for i, r in enumerate(PARTITION_ROLES)}
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT * FROM training_partitions WHERE sibling_group = ?", (group,)
        ).fetchall()
    finally:
        con.close()
    return sorted((TrainingPartition(**dict(r)) for r in rows),
                  key=lambda p: order[p.role])


def sibling_hashes(db_path: Path | str, spec_hash: str) -> dict[str, str]:
    """`{role: spec_hash}` for the whole group — what a model cites so *"the
    split is reconstructable"* (§15.1)."""
    part = get_partition(db_path, spec_hash)
    if part is None:
        return {}
    return {p.role: p.spec_hash for p in sibling_group(db_path, part.sibling_group)}


def verify_group_complete(db_path: Path | str, group: str) -> tuple[bool, list[str]]:
    """Does the group hold all three roles, with no overlapping windows?

    An incomplete group is reported, not refused: partitions are registered one
    at a time, so a group is legitimately incomplete while being built.
    """
    parts = sibling_group(db_path, group)
    errors: list[str] = []
    if not parts:
        return False, [f"sibling group {group!r} has no partitions"]

    present = {p.role for p in parts}
    missing = set(PARTITION_ROLES) - present
    if missing:
        errors.append(f"group {group!r} is missing role(s): {sorted(missing)}")

    for i, a in enumerate(parts):
        for b in parts[i + 1:]:
            if windows_overlap((a.window_start, a.window_end),
                               (b.window_start, b.window_end)):
                errors.append(
                    f"{a.role} and {b.role} windows overlap in {group!r}")
    return (not errors), errors


# ---------------------------------------------------------------------------
# DG-7 — holdout is single-use
# ---------------------------------------------------------------------------

def consume_holdout(
    db_path: Path | str,
    spec_hash: str,
    *,
    decision_ref: str,
    note: str | None = None,
) -> HoldoutConsumption:
    """Record the one permitted consumption of a holdout partition (DG-7).

    *"Holdout partitions are single-use. Consumption is recorded against the
    consuming decision."* A second attempt raises `HoldoutSpentError` and names
    the decision that spent it — the point is not to hide the first use but to
    make the second impossible.

    Consumption rows are append-only: a consumption that could be deleted would
    make "single-use" unenforceable.
    """
    _assert_training_schema(db_path)
    if not str(decision_ref or "").strip():
        raise TrainingError(
            "decision_ref is required: DG-7 records consumption AGAINST the "
            "consuming decision, so an anonymous consumption records nothing")

    part = get_partition(db_path, spec_hash)
    if part is None:
        raise TrainingError(f"{spec_hash[:12]}... is not a training partition")
    if part.role not in SINGLE_USE_ROLES:
        raise TrainingError(
            f"{part.role!r} partitions are not single-use; only "
            f"{sorted(SINGLE_USE_ROLES)} are (DG-7)")

    spent = holdout_consumptions(db_path, spec_hash)
    if spent:
        raise HoldoutSpentError(
            f"holdout {spec_hash[:12]}... was already consumed by "
            f"{spent[0].decision_ref!r} at {spent[0].consumed_at}. DG-7 makes "
            f"holdout partitions single-use"
        )

    consumed_at = _utc_now()
    con = _connect(db_path)
    try:
        cur = con.execute(
            "INSERT INTO holdout_consumptions "
            "(spec_hash, decision_ref, consumed_at, note) VALUES (?, ?, ?, ?)",
            (spec_hash, decision_ref, consumed_at, note),
        )
        con.commit()
        row_id = int(cur.lastrowid)
    finally:
        con.close()
    return HoldoutConsumption(row_id, spec_hash, decision_ref, consumed_at, note)


def holdout_consumptions(db_path: Path | str, spec_hash: str) -> list[HoldoutConsumption]:
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT * FROM holdout_consumptions WHERE spec_hash = ? ORDER BY id",
            (spec_hash,),
        ).fetchall()
    finally:
        con.close()
    return [HoldoutConsumption(**dict(r)) for r in rows]


def is_holdout_spent(db_path: Path | str, spec_hash: str) -> bool:
    return bool(holdout_consumptions(db_path, spec_hash))
