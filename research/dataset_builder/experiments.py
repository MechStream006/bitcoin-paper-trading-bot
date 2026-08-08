"""Experiment Datasets and Use-Counting — ADR-I012 §7.1, §9, §15.3, DG-6, DG-8;
ADR-I011 LG7/DI8 (scope transfer `T-5`); resolves PS6.

PS6, in the ADR's own words:

> *"Trial context is not carried. The trial denominator is frozen and correct,
> but a dataset reused across trials records nothing about that reuse."*
> **Consequence:** *"Multiple-comparisons burden is tracked at the family level
> and invisible at the substrate level."*

DG-8 is the answer, and the ADR is explicit about why it matters:

> *"**DG-8 is the multiple-comparisons defence at the substrate layer.** The
> platform already freezes its trial denominator by governance session. DG-8
> makes substrate reuse legible so the denominator can be **checked** rather
> than merely declared."*

WHAT AN EXPERIMENT DATASET IS (§7.1, §15.3)
    A Research Dataset **bound to a trial context**: the trial registration it
    serves, its position within the frozen trial denominator, and a use-count.
    *"This is what an evaluation run cites."* A Training Dataset may also be an
    Experiment Dataset — §15.3 says the roles are **orthogonal**, so nothing
    here assumes or forbids the other.

THE DESIGN DECISION THAT CARRIES DG-8
    **The use-count is derived from append-only rows, never stored as a
    counter.** A stored integer can be decremented, reset, or corrected; rows
    in a write-once table cannot. If reuse is to be *checkable* rather than
    *declared* — DG-8's exact distinction — then the count must be a
    consequence of evidence, not an assertion about it.

    `use_count()` is therefore a `SELECT COUNT(*)`, and there is deliberately
    no `set_use_count()` anywhere in this module.

THE DENOMINATOR IS DECLARED, NOT INVENTED
    The platform's Phase B battery freezes `n_trials = 34`, but that number
    belongs to *that* battery, not to this module. A trial registration
    declares its own denominator size; this module enforces **internal
    consistency** — every experiment in one registration must agree on the
    size, and no two may occupy the same position — and refuses out-of-range
    positions. Hard-coding 34 here would be inventing governance and would
    silently break the first registration with a different denominator.

WHAT IS DELIBERATELY OUT OF SCOPE
    `ADR-I012` R9: *"Nothing in the audited platform cites a dataset today ...
    **Out of scope here.** This ADR specifies the object; wiring it into
    evaluation is gated and separate."* This module builds the object.
    Nothing here modifies an evaluation pipeline, and `DS-DEC-006`
    (retro-binding the 70 historical ledger runs) remains **Owed** and
    untouched.
"""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_EVIDENCE_LIB_DIR = Path(__file__).resolve().parents[1] / "evidence_library"
if str(_EVIDENCE_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_EVIDENCE_LIB_DIR))

from receipts import canonical_json, sha256_lf  # noqa: E402

from dataset_builder import citation_index as ci
from dataset_builder import registry as reg

#: A dataset may be bound to a trial only once it is citable. §7.1: an
#: Experiment Dataset is *"what an evaluation run cites"*, and §11 says nothing
#: may reference a dataset before seal. Superseded is included for the same
#: reason the citation index includes it (§10.2 keeps it citable).
BINDABLE_PHASES: frozenset[str] = frozenset({"Sealed", "Superseded"})

#: Purposes a use may declare. `promotion` is called out separately because
#: ADR-I011 DI8 attaches a specific obligation to it: *"the same forward
#: evidence cannot silently support repeated promotion attempts."*
USE_PURPOSES: frozenset[str] = frozenset({
    "evaluation", "promotion", "validation", "exploration",
})

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS experiment_datasets (
    experiment_id         TEXT PRIMARY KEY,
    dataset_hash          TEXT NOT NULL,
    trial_registration    TEXT NOT NULL,
    denominator_position  INTEGER NOT NULL,
    denominator_size      INTEGER NOT NULL,
    created_at            TEXT NOT NULL,
    created_by            TEXT NOT NULL,
    CHECK (denominator_position >= 1),
    CHECK (denominator_size >= 1),
    CHECK (denominator_position <= denominator_size),
    UNIQUE (trial_registration, denominator_position),
    FOREIGN KEY (dataset_hash) REFERENCES dataset_specifications(spec_hash)
);

CREATE INDEX IF NOT EXISTS idx_experiments_dataset
    ON experiment_datasets(dataset_hash);
CREATE INDEX IF NOT EXISTS idx_experiments_trial
    ON experiment_datasets(trial_registration, denominator_position);

-- Immutable (§7.1: an Experiment Dataset is immutable). Rebinding a trial
-- context after the fact would let a denominator position be moved once a
-- result was known, which is the precise abuse DG-8 exists to prevent.
CREATE TRIGGER IF NOT EXISTS trg_experiments_no_update
BEFORE UPDATE ON experiment_datasets
BEGIN
    SELECT RAISE(ABORT, 'an Experiment Dataset is immutable: UPDATE forbidden');
END;

CREATE TRIGGER IF NOT EXISTS trg_experiments_no_delete
BEFORE DELETE ON experiment_datasets
BEGIN
    SELECT RAISE(ABORT, 'an Experiment Dataset is immutable: DELETE forbidden');
END;

-- The use-count substrate. DG-8 requires reuse to be CHECKABLE, so uses are
-- append-only rows and the count is derived from them. A stored counter could
-- be reset; these cannot.
CREATE TABLE IF NOT EXISTS dataset_uses (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id  TEXT NOT NULL,
    consuming_ref  TEXT NOT NULL,
    purpose        TEXT NOT NULL,
    used_at        TEXT NOT NULL,
    note           TEXT,
    CHECK (length(consuming_ref) > 0),
    FOREIGN KEY (experiment_id) REFERENCES experiment_datasets(experiment_id)
);

CREATE INDEX IF NOT EXISTS idx_uses_experiment
    ON dataset_uses(experiment_id, id);
CREATE INDEX IF NOT EXISTS idx_uses_purpose
    ON dataset_uses(purpose, experiment_id);

CREATE TRIGGER IF NOT EXISTS trg_uses_no_update
BEFORE UPDATE ON dataset_uses
BEGIN
    SELECT RAISE(ABORT, 'dataset_uses is append-only: UPDATE forbidden');
END;

CREATE TRIGGER IF NOT EXISTS trg_uses_no_delete
BEFORE DELETE ON dataset_uses
BEGIN
    SELECT RAISE(ABORT, 'dataset_uses is append-only: DELETE forbidden');
END;
"""


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ExperimentError(reg.DatasetRegistryError):
    """Base class for Experiment Dataset failures."""


class NotBindableError(ExperimentError):
    """Only a citable (sealed or superseded) dataset may be bound to a trial."""


class DenominatorError(ExperimentError):
    """The declared trial denominator is internally inconsistent.

    A denominator that changes size mid-registration, or two experiments at
    the same position, would make the multiple-comparisons count unverifiable
    — which is the one thing DG-8 exists to prevent.
    """


class UnknownExperimentError(ExperimentError):
    """No such Experiment Dataset."""


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExperimentDataset:
    experiment_id: str
    dataset_hash: str
    trial_registration: str
    denominator_position: int
    denominator_size: int
    created_at: str
    created_by: str


@dataclass(frozen=True)
class Use:
    id: int
    experiment_id: str
    consuming_ref: str
    purpose: str
    used_at: str
    note: str | None = None


@dataclass(frozen=True)
class SubstrateReuse:
    """DG-8's whole point, made queryable: how visibly is one sealed dataset
    reused across trials?"""
    dataset_hash: str
    experiment_count: int
    trial_registrations: tuple[str, ...]
    total_uses: int

    @property
    def is_reused(self) -> bool:
        return self.experiment_count > 1

    @property
    def spans_multiple_trials(self) -> bool:
        return len(self.trial_registrations) > 1


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def init_db(db_path: Path | str) -> None:
    """Create the experiment tables. Idempotent. Requires the registry schema
    first — an experiment with no dataset to bind is meaningless."""
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


def _assert_experiment_schema(db_path: Path | str) -> None:
    reg._assert_dataset_builder_schema(db_path)
    con = sqlite3.connect(db_path)
    try:
        names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        con.close()
    missing = {"experiment_datasets", "dataset_uses"} - names
    if missing:
        raise reg.NotADatasetBuilderStore(
            f"{db_path} has no experiment tables (missing "
            f"{', '.join(sorted(missing))}). Call experiments.init_db() first. "
            f"Refusing to write."
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def experiment_id(dataset_hash: str, trial_registration: str,
                  denominator_position: int) -> str:
    """Content-addressed identity (IQROS-P5).

    The trial context *is* the identity: the same dataset at the same position
    of the same registration is the same experiment, so re-registering it is
    idempotent rather than a second entry that would inflate the reuse count.
    """
    return sha256_lf(canonical_json({
        "dataset_hash": dataset_hash,
        "trial_registration": trial_registration,
        "denominator_position": int(denominator_position),
    }))


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_experiment(
    db_path: Path | str,
    dataset_hash: str,
    *,
    trial_registration: str,
    denominator_position: int,
    denominator_size: int,
    created_by: str,
) -> ExperimentDataset:
    """Bind a citable dataset to a trial context (§7.1, DG-6).

    Refuses:
      * a dataset that is not yet citable (§11 — nothing references a dataset
        before seal);
      * a position outside `1..denominator_size`;
      * a denominator size that disagrees with the one already declared for
        this registration;
      * a position already occupied in this registration.

    Idempotent on identical context: the same dataset at the same position of
    the same registration returns the existing record rather than creating a
    second one, which would inflate the reuse count DG-8 exists to make
    trustworthy.
    """
    _assert_experiment_schema(db_path)

    if not str(trial_registration or "").strip():
        raise ExperimentError("trial_registration is required (§9)")
    if not str(created_by or "").strip():
        raise ExperimentError("created_by is required")

    try:
        position = int(denominator_position)
        size = int(denominator_size)
    except (TypeError, ValueError) as exc:
        raise DenominatorError(f"denominator values must be integers: {exc}") from exc

    if size < 1:
        raise DenominatorError(f"denominator_size must be >= 1, got {size}")
    if position < 1 or position > size:
        raise DenominatorError(
            f"denominator_position {position} is outside 1..{size}. A position "
            f"outside the declared denominator makes the multiple-comparisons "
            f"count unverifiable (DG-8)"
        )

    spec = reg.get_specification(db_path, dataset_hash)
    if spec is None:
        raise ExperimentError(f"no such specification: {dataset_hash}")
    if spec["phase"] not in BINDABLE_PHASES:
        raise NotBindableError(
            f"dataset {dataset_hash[:12]}... is in phase {spec['phase']!r}; only "
            f"a citable dataset may be bound to a trial (§7.1: an Experiment "
            f"Dataset is what an evaluation run cites; §11: nothing may "
            f"reference a dataset before seal)"
        )

    exp_id = experiment_id(dataset_hash, trial_registration, position)

    con = _connect(db_path)
    try:
        existing = con.execute(
            "SELECT * FROM experiment_datasets WHERE experiment_id = ?",
            (exp_id,),
        ).fetchone()
        if existing is not None:
            if int(existing["denominator_size"]) != size:
                raise DenominatorError(
                    f"experiment {exp_id[:12]}... already exists with "
                    f"denominator_size {existing['denominator_size']}, not {size}"
                )
            return ExperimentDataset(**dict(existing))

        declared = con.execute(
            "SELECT DISTINCT denominator_size FROM experiment_datasets "
            "WHERE trial_registration = ?",
            (trial_registration,),
        ).fetchall()
        if declared and int(declared[0]["denominator_size"]) != size:
            raise DenominatorError(
                f"trial registration {trial_registration!r} already declares "
                f"denominator_size {declared[0]['denominator_size']}; refusing "
                f"{size}. A denominator that changes mid-registration makes the "
                f"multiple-comparisons count unverifiable (DG-8)"
            )

        occupant = con.execute(
            "SELECT dataset_hash FROM experiment_datasets "
            "WHERE trial_registration = ? AND denominator_position = ?",
            (trial_registration, position),
        ).fetchone()
        if occupant is not None:
            raise DenominatorError(
                f"position {position} of {trial_registration!r} is already held "
                f"by dataset {occupant['dataset_hash'][:12]}...; two experiments "
                f"cannot occupy one denominator position"
            )

        created_at = _utc_now()
        con.execute(
            "INSERT INTO experiment_datasets "
            "(experiment_id, dataset_hash, trial_registration, "
            " denominator_position, denominator_size, created_at, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (exp_id, dataset_hash, trial_registration, position, size,
             created_at, created_by),
        )
        con.commit()
    finally:
        con.close()

    return ExperimentDataset(
        experiment_id=exp_id, dataset_hash=dataset_hash,
        trial_registration=trial_registration, denominator_position=position,
        denominator_size=size, created_at=created_at, created_by=created_by,
    )


def get_experiment(db_path: Path | str, exp_id: str) -> ExperimentDataset | None:
    con = _connect(db_path)
    try:
        row = con.execute(
            "SELECT * FROM experiment_datasets WHERE experiment_id = ?", (exp_id,)
        ).fetchone()
    finally:
        con.close()
    return None if row is None else ExperimentDataset(**dict(row))


def experiments_for_dataset(db_path: Path | str, dataset_hash: str) -> list[ExperimentDataset]:
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT * FROM experiment_datasets WHERE dataset_hash = ? "
            "ORDER BY trial_registration, denominator_position",
            (dataset_hash,),
        ).fetchall()
    finally:
        con.close()
    return [ExperimentDataset(**dict(r)) for r in rows]


def experiments_in_trial(db_path: Path | str, trial_registration: str) -> list[ExperimentDataset]:
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT * FROM experiment_datasets WHERE trial_registration = ? "
            "ORDER BY denominator_position",
            (trial_registration,),
        ).fetchall()
    finally:
        con.close()
    return [ExperimentDataset(**dict(r)) for r in rows]


# ---------------------------------------------------------------------------
# Use-counting (DG-8, ADR-I011 LG7/DI8)
# ---------------------------------------------------------------------------

def record_use(
    db_path: Path | str,
    exp_id: str,
    *,
    consuming_ref: str,
    purpose: str = "evaluation",
    note: str | None = None,
) -> Use:
    """Record one consumption of an Experiment Dataset.

    Append-only by construction: there is no update path and no stored
    counter, so a use once recorded cannot be walked back. That is what makes
    the count *checkable* rather than *declared* (DG-8).

    `consuming_ref` identifies what consumed it — a run id, a decision id, a
    promotion attempt. It is required: an anonymous use records that something
    happened without recording what, which would make the count useless for
    the multiple-comparisons purpose it exists to serve.
    """
    _assert_experiment_schema(db_path)
    if purpose not in USE_PURPOSES:
        raise ExperimentError(
            f"purpose must be one of {sorted(USE_PURPOSES)}, got {purpose!r}"
        )
    if not str(consuming_ref or "").strip():
        raise ExperimentError(
            "consuming_ref is required: an anonymous use records that something "
            "happened without recording what, which is useless for tracking "
            "multiple comparisons (DG-8)"
        )
    if get_experiment(db_path, exp_id) is None:
        raise UnknownExperimentError(f"no such experiment: {exp_id}")

    used_at = _utc_now()
    con = _connect(db_path)
    try:
        cur = con.execute(
            "INSERT INTO dataset_uses "
            "(experiment_id, consuming_ref, purpose, used_at, note) "
            "VALUES (?, ?, ?, ?, ?)",
            (exp_id, consuming_ref, purpose, used_at, note),
        )
        con.commit()
        use_id = int(cur.lastrowid)
    finally:
        con.close()
    return Use(use_id, exp_id, consuming_ref, purpose, used_at, note)


def use_count(db_path: Path | str, exp_id: str, *, purpose: str | None = None) -> int:
    """The use-count, **derived** from append-only rows.

    Deliberately a COUNT over evidence rather than a stored integer: a counter
    can be reset or corrected, and DG-8 requires reuse to be checkable rather
    than asserted. There is no `set_use_count()` in this module, and that
    absence is the design.
    """
    con = _connect(db_path)
    try:
        if purpose is None:
            row = con.execute(
                "SELECT COUNT(*) AS n FROM dataset_uses WHERE experiment_id = ?",
                (exp_id,),
            ).fetchone()
        else:
            row = con.execute(
                "SELECT COUNT(*) AS n FROM dataset_uses "
                "WHERE experiment_id = ? AND purpose = ?",
                (exp_id, purpose),
            ).fetchone()
    finally:
        con.close()
    return int(row["n"])


def uses(db_path: Path | str, exp_id: str) -> list[Use]:
    """Every recorded use, oldest first. Append-only, so this is complete."""
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT * FROM dataset_uses WHERE experiment_id = ? ORDER BY id",
            (exp_id,),
        ).fetchall()
    finally:
        con.close()
    return [Use(**dict(r)) for r in rows]


def substrate_reuse(db_path: Path | str, dataset_hash: str) -> SubstrateReuse:
    """DG-8 made queryable: how visibly is one sealed dataset reused?

    *"Reuse of one substrate across trials is visible, not inferred."* This is
    the function that makes it visible — and `spans_multiple_trials` is the
    signal that matters for the frozen denominator, because one substrate
    serving two registrations means the two are not independent.
    """
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT experiment_id, trial_registration FROM experiment_datasets "
            "WHERE dataset_hash = ? ORDER BY trial_registration",
            (dataset_hash,),
        ).fetchall()
        total = con.execute(
            "SELECT COUNT(*) AS n FROM dataset_uses u "
            "JOIN experiment_datasets e ON e.experiment_id = u.experiment_id "
            "WHERE e.dataset_hash = ?",
            (dataset_hash,),
        ).fetchone()["n"]
    finally:
        con.close()
    return SubstrateReuse(
        dataset_hash=dataset_hash,
        experiment_count=len(rows),
        trial_registrations=tuple(sorted({r["trial_registration"] for r in rows})),
        total_uses=int(total),
    )


def promotion_uses(db_path: Path | str, dataset_hash: str) -> list[Use]:
    """Every `promotion` use of any experiment bound to this dataset.

    ADR-I011 DI8: *"A dataset citing Promotion Labels records that consumption,
    so the same forward evidence cannot silently support repeated promotion
    attempts."* Repeated promotion is not refused here — that is a governance
    judgement, not a builder one (§13.1: *"Promotion — Human — Builder role:
    supply use-counted evidence; never decide"*). What this makes impossible is
    doing it **silently**.
    """
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT u.* FROM dataset_uses u "
            "JOIN experiment_datasets e ON e.experiment_id = u.experiment_id "
            "WHERE e.dataset_hash = ? AND u.purpose = 'promotion' "
            "ORDER BY u.id",
            (dataset_hash,),
        ).fetchall()
    finally:
        con.close()
    return [Use(**dict(r)) for r in rows]


# ---------------------------------------------------------------------------
# Integrity
# ---------------------------------------------------------------------------

def verify_denominator(db_path: Path | str, trial_registration: str) -> tuple[bool, list[str]]:
    """Is this trial registration's denominator internally coherent?

    Checks one declared size, no duplicate positions, and every position in
    range. Reports gaps as information rather than error: a registration may
    legitimately be partially populated while trials are still being bound.
    """
    errors: list[str] = []
    rows = experiments_in_trial(db_path, trial_registration)
    if not rows:
        return True, []

    sizes = {r.denominator_size for r in rows}
    if len(sizes) > 1:
        errors.append(
            f"denominator_size disagrees within {trial_registration!r}: "
            f"{sorted(sizes)}"
        )
    size = max(sizes)

    positions = [r.denominator_position for r in rows]
    if len(positions) != len(set(positions)):
        errors.append(f"duplicate denominator positions in {trial_registration!r}")
    out_of_range = [p for p in positions if p < 1 or p > size]
    if out_of_range:
        errors.append(
            f"positions outside 1..{size} in {trial_registration!r}: {sorted(out_of_range)}"
        )
    return (not errors), errors


def find_orphan_experiments(db_path: Path | str) -> list[str]:
    """Experiments whose dataset is no longer citable.

    Should be empty: registration refuses a non-citable dataset, and retirement
    is refused while cited (§6.2, enforced at WBS 4.2). This exists so the
    invariant is auditable rather than assumed.
    """
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT e.experiment_id FROM experiment_datasets e "
            "JOIN dataset_specifications d ON d.spec_hash = e.dataset_hash "
            "WHERE d.phase NOT IN ('Sealed', 'Superseded') "
            "ORDER BY e.experiment_id"
        ).fetchall()
    finally:
        con.close()
    return [r["experiment_id"] for r in rows]


def trial_context(db_path: Path | str, exp_id: str) -> dict:
    """Everything §9 requires an Experiment Dataset to carry: trial
    registration, denominator position, use-count, and consuming run
    references — plus the substrate's citations, so the whole provenance chain
    is answerable from one call."""
    exp = get_experiment(db_path, exp_id)
    if exp is None:
        raise UnknownExperimentError(f"no such experiment: {exp_id}")
    recorded = uses(db_path, exp_id)
    return {
        "experiment_id": exp.experiment_id,
        "dataset_hash": exp.dataset_hash,
        "trial_registration": exp.trial_registration,
        "denominator_position": exp.denominator_position,
        "denominator_size": exp.denominator_size,
        "use_count": len(recorded),
        "consuming_refs": [u.consuming_ref for u in recorded],
        "purposes": sorted({u.purpose for u in recorded}),
        "substrate_citations": {
            c.cited_kind: sorted(
                x.cited_id for x in ci.cites(db_path, exp.dataset_hash)
                if x.cited_kind == c.cited_kind
            )
            for c in ci.cites(db_path, exp.dataset_hash)
        },
    }
