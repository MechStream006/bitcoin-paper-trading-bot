"""
WBS 4.11 -- retirement, supersession, and ``--as-of`` reconstruction (L3).

ADR-I012 §10.2 (supersession never invalidates), §11 (lifecycle), §16 L3
(historically reconstructable), DG-5 (a cited dataset can never be retired).

§16 grounds L3 in *"append-only registry + as_of derivation"*. The registry as
built at WBS 4.1 is not append-only in that sense: ``phase`` is a mutable column
advanced in place under a guarded trigger, so ``dataset_specifications`` records
only where a dataset stands *now*. Its past is unrecoverable from it, and the
question §16 says L3 answers -- *"what did we believe this dataset contained on
the day we promoted?"* -- has no source to read.

This module supplies the missing substrate as an append-only transition ledger:
the same shape ``evidence_library.state_transitions`` already uses, read back
the way ``record.derive_state_at()`` reads it.

The ledger is populated by DATABASE TRIGGERS, not by Python calls. Two phase
write paths already exist -- ``registry._transition`` and ``sealing``'s direct
``UPDATE`` -- so a Python-side hook would have to be added to both, and to every
future third path. A forgotten hook yields a *partial* history that still reads
as complete, which is worse than none: it answers the audit question with a
confident wrong answer. A trigger cannot be forgotten, and cannot be bypassed by
a direct ``sqlite3`` write.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from dataset_builder import citation_index as ci
from dataset_builder import registry as reg

ENGINE_VERSION = "dataset_builder.history/1.0.0"

#: A phase from which retirement is reachable at all (§11's state diagram).
RETIRABLE_FROM: frozenset[str] = frozenset({"Sealed"})

#: The three citation classes DG-5 names, mapped to the table that evidences
#: each. Deliberately not widened: see `dg5_citations` for the binding case.
DG5_CITATION_KINDS: frozenset[str] = frozenset({"run", "model", "decision"})

_TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


class HistoryError(reg.DatasetRegistryError):
    """Base class for WBS 4.11 failures."""


class RetirementRefused(HistoryError):
    """DG-5: the dataset is cited by a run, model, or decision.

    Refusal is unconditional and has no override. DG-5 does not say "should
    not"; a cited dataset can *never* be retired, because retirement removes it
    from `CITABLE_PHASES` and would strand every evaluation that referenced it.
    """


class SupersessionError(HistoryError):
    """A supersession would not preserve the predecessor (§10.2)."""


class HistoryIncompleteError(HistoryError):
    """History was requested for a dataset whose transitions are not recorded.

    Raised rather than returning a plausible-looking phase. A dataset created
    before this module's triggers were installed has no genesis row, and any
    derived phase would be a guess presented as an audit answer.
    """


@dataclass(frozen=True)
class Transition:
    """One recorded phase change. Immutable, append-only, trigger-written."""

    id: int
    spec_hash: str
    from_phase: str | None
    to_phase: str
    ts: str
    source: str


@dataclass(frozen=True)
class CitationRef:
    """One DG-5 citation of a dataset by a run, model, or decision."""

    kind: str
    ref: str
    detail: str


@dataclass(frozen=True)
class RetirementReport:
    """Outcome of a retirement attempt, refused or granted."""

    spec_hash: str
    retired: bool
    blocking_citations: tuple[CitationRef, ...] = ()
    bindings_without_uses: tuple[str, ...] = ()
    note: str = ""

    @property
    def refused(self) -> bool:
        return not self.retired


@dataclass(frozen=True)
class StalenessFlag:
    """A recorded doubt about a sealed dataset's admissibility ceiling."""

    id: int
    spec_hash: str
    cited_kind: str
    cited_id: str
    reason: str
    flagged_at: str
    flagged_by: str


@dataclass
class ReconstructionReport:
    """The registry as it stood at one instant (§16 L3)."""

    as_of: str | None
    phases: dict[str, str] = field(default_factory=dict)
    absent: tuple[str, ...] = ()

    def citable(self) -> tuple[str, ...]:
        return tuple(
            sorted(h for h, p in self.phases.items() if p in reg.CITABLE_PHASES)
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime(_TS_FORMAT)


def _connect(db_path: Path | str) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def init_db(db_path: Path | str) -> None:
    """Create the transition ledger, the staleness register, and their triggers.

    Idempotent. Requires the WBS 4.1 registry to exist first -- the ledger is
    meaningless without the table whose phase changes it records, and a
    foreign key would fail obscurely rather than saying so.
    """
    reg._assert_dataset_builder_schema(db_path)

    con = _connect(db_path)
    try:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS dataset_transitions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                spec_hash  TEXT NOT NULL,
                from_phase TEXT,
                to_phase   TEXT NOT NULL,
                ts         TEXT NOT NULL,
                source     TEXT NOT NULL,
                FOREIGN KEY (spec_hash)
                    REFERENCES dataset_specifications (spec_hash)
            );

            CREATE INDEX IF NOT EXISTS idx_dataset_transitions_lookup
                ON dataset_transitions (spec_hash, ts, id);

            CREATE TRIGGER IF NOT EXISTS trg_dataset_transitions_no_update
            BEFORE UPDATE ON dataset_transitions
            BEGIN
                SELECT RAISE(ABORT,
                    'HISTORY_TAMPER: dataset_transitions is append-only');
            END;

            CREATE TRIGGER IF NOT EXISTS trg_dataset_transitions_no_delete
            BEFORE DELETE ON dataset_transitions
            BEGIN
                SELECT RAISE(ABORT,
                    'HISTORY_TAMPER: dataset_transitions is append-only');
            END;

            CREATE TRIGGER IF NOT EXISTS trg_dataset_genesis
            AFTER INSERT ON dataset_specifications
            BEGIN
                INSERT INTO dataset_transitions
                    (spec_hash, from_phase, to_phase, ts, source)
                VALUES (
                    NEW.spec_hash, NULL, NEW.phase,
                    strftime('%Y-%m-%dT%H:%M:%f000Z', 'now'), 'declare');
            END;

            CREATE TRIGGER IF NOT EXISTS trg_dataset_phase_change
            AFTER UPDATE OF phase ON dataset_specifications
            WHEN OLD.phase <> NEW.phase
            BEGIN
                INSERT INTO dataset_transitions
                    (spec_hash, from_phase, to_phase, ts, source)
                VALUES (
                    NEW.spec_hash, OLD.phase, NEW.phase,
                    strftime('%Y-%m-%dT%H:%M:%f000Z', 'now'), 'transition');
            END;

            CREATE TABLE IF NOT EXISTS ceiling_staleness_flags (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                spec_hash  TEXT NOT NULL,
                cited_kind TEXT NOT NULL,
                cited_id   TEXT NOT NULL,
                reason     TEXT NOT NULL,
                flagged_at TEXT NOT NULL,
                flagged_by TEXT NOT NULL,
                UNIQUE (spec_hash, cited_kind, cited_id, reason),
                FOREIGN KEY (spec_hash)
                    REFERENCES dataset_specifications (spec_hash)
            );

            CREATE TRIGGER IF NOT EXISTS trg_staleness_no_update
            BEFORE UPDATE ON ceiling_staleness_flags
            BEGIN
                SELECT RAISE(ABORT,
                    'HISTORY_TAMPER: ceiling_staleness_flags is append-only');
            END;

            CREATE TRIGGER IF NOT EXISTS trg_staleness_no_delete
            BEFORE DELETE ON ceiling_staleness_flags
            BEGIN
                SELECT RAISE(ABORT,
                    'HISTORY_TAMPER: ceiling_staleness_flags is append-only');
            END;
            """
        )
        con.commit()
    finally:
        con.close()


def _assert_history_schema(db_path: Path | str) -> None:
    con = _connect(db_path)
    try:
        if not _table_exists(con, "dataset_transitions"):
            raise reg.NotADatasetBuilderStore(
                f"{db_path} has no dataset_transitions table; "
                "call history.init_db() before reconstructing history"
            )
    finally:
        con.close()


def phase_history(db_path: Path | str, spec_hash: str) -> list[Transition]:
    """Every recorded phase change for one dataset, oldest first.

    Ordered by ``(ts, id)``. The autoincrement tiebreak is not decorative: two
    transitions can share a millisecond, and ``ts`` alone would make the order
    of a fast declare-then-resolve sequence undefined.
    """
    _assert_history_schema(db_path)
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT id, spec_hash, from_phase, to_phase, ts, source "
            "FROM dataset_transitions WHERE spec_hash = ? ORDER BY ts, id",
            (spec_hash,),
        ).fetchall()
        return [Transition(**dict(r)) for r in rows]
    finally:
        con.close()


def derive_phase_at(
    db_path: Path | str, spec_hash: str, as_of: str | None = None
) -> str | None:
    """The phase this dataset stood at, at ``as_of`` (None = now).

    Derived PURELY from the append-only ledger, never from
    ``dataset_specifications.phase`` -- reading the mutable column would answer
    "now" to every question, which is precisely the defect L3 exists to fix.

    Returns ``None`` if the dataset did not yet exist at that instant.
    """
    _assert_history_schema(db_path)
    con = _connect(db_path)
    try:
        if as_of is None:
            row = con.execute(
                "SELECT to_phase FROM dataset_transitions WHERE spec_hash = ? "
                "ORDER BY ts DESC, id DESC LIMIT 1",
                (spec_hash,),
            ).fetchone()
        else:
            row = con.execute(
                "SELECT to_phase FROM dataset_transitions "
                "WHERE spec_hash = ? AND ts <= ? ORDER BY ts DESC, id DESC LIMIT 1",
                (spec_hash, as_of),
            ).fetchone()
        return row["to_phase"] if row else None
    finally:
        con.close()


def reconstruct_registry_at(
    db_path: Path | str, as_of: str | None = None
) -> ReconstructionReport:
    """The whole registry as it stood at one instant (§16 L3).

    A dataset declared *after* ``as_of`` is absent from ``phases`` rather than
    reported in some default phase. "Did not exist yet" and "existed as Draft"
    are different historical facts, and collapsing them would let a reviewer
    conclude a dataset was available when it was not.
    """
    _assert_history_schema(db_path)
    con = _connect(db_path)
    try:
        all_hashes = [
            r["spec_hash"]
            for r in con.execute(
                "SELECT DISTINCT spec_hash FROM dataset_transitions ORDER BY spec_hash"
            ).fetchall()
        ]
    finally:
        con.close()

    phases: dict[str, str] = {}
    absent: list[str] = []
    for h in all_hashes:
        phase = derive_phase_at(db_path, h, as_of)
        if phase is None:
            absent.append(h)
        else:
            phases[h] = phase
    return ReconstructionReport(
        as_of=as_of, phases=phases, absent=tuple(sorted(absent))
    )


def reconstruct_specification_at(
    db_path: Path | str, spec_hash: str, as_of: str | None = None
) -> dict | None:
    """A full specification record as it stood at ``as_of``.

    The declaration body is immutable by construction (the spec hash *is* its
    content hash, DG-3), so only ``phase`` is time-varying and only ``phase``
    is reconstructed. Sealing metadata is included only if the seal had
    happened by then.
    """
    phase = derive_phase_at(db_path, spec_hash, as_of)
    if phase is None:
        return None

    record = reg.get_specification(db_path, spec_hash)
    if record is None:
        return None

    record = dict(record)
    record["phase"] = phase
    record["as_of"] = as_of
    record["phase_is_reconstructed"] = True

    con = _connect(db_path)
    try:
        if _table_exists(con, "sealed_datasets"):
            row = con.execute(
                "SELECT sealed_at FROM sealed_datasets WHERE spec_hash = ?",
                (spec_hash,),
            ).fetchone()
            sealed_by_then = bool(
                row and (as_of is None or row["sealed_at"] <= as_of)
            )
            record["was_sealed"] = sealed_by_then
    finally:
        con.close()
    return record


def history_completeness(db_path: Path | str) -> tuple[bool, list[str]]:
    """Assert the ledger can answer for every dataset. Never repair it.

    A specification with no genesis transition predates trigger installation.
    Its history is unrecoverable -- the information was never written -- so this
    reports the gap by name. Back-filling a plausible genesis row would
    manufacture audit evidence, which ADR-P1-1's assert-never-repair discipline
    forbids for exactly this reason.
    """
    _assert_history_schema(db_path)
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT s.spec_hash FROM dataset_specifications s "
            "WHERE NOT EXISTS (SELECT 1 FROM dataset_transitions t "
            "                  WHERE t.spec_hash = s.spec_hash) "
            "ORDER BY s.spec_hash"
        ).fetchall()
    finally:
        con.close()

    errors = [
        f"spec {r['spec_hash'][:12]} has no recorded genesis transition; "
        "it predates history.init_db() and its past is not reconstructable"
        for r in rows
    ]
    return (not errors, errors)


def dg5_citations(db_path: Path | str, spec_hash: str) -> list[CitationRef]:
    """Every run, model, or decision citing this dataset (DG-5's three classes).

    Scoped to exactly what DG-5 names. A bound-but-unused Experiment Dataset is
    reported separately by `bindings_without_uses` and does NOT block: a trial
    binding is not yet a run, and widening a governance rule beyond its written
    text would be inventing governance rather than enforcing it.
    """
    con = _connect(db_path)
    refs: list[CitationRef] = []
    try:
        if _table_exists(con, "dataset_uses") and _table_exists(
            con, "experiment_datasets"
        ):
            for r in con.execute(
                "SELECT u.consuming_ref, u.purpose, e.trial_registration "
                "FROM dataset_uses u JOIN experiment_datasets e "
                "  ON e.experiment_id = u.experiment_id "
                "WHERE e.dataset_hash = ? ORDER BY u.id",
                (spec_hash,),
            ).fetchall():
                refs.append(
                    CitationRef(
                        kind="run",
                        ref=r["consuming_ref"],
                        detail=f"purpose={r['purpose']} trial={r['trial_registration']}",
                    )
                )

        if _table_exists(con, "holdout_consumptions"):
            for r in con.execute(
                "SELECT decision_ref, consumed_at FROM holdout_consumptions "
                "WHERE spec_hash = ? ORDER BY id",
                (spec_hash,),
            ).fetchall():
                refs.append(
                    CitationRef(
                        kind="decision",
                        ref=r["decision_ref"],
                        detail=f"holdout consumed at {r['consumed_at']}",
                    )
                )

        if _table_exists(con, "training_partitions"):
            for r in con.execute(
                "SELECT sibling_group, role FROM training_partitions "
                "WHERE spec_hash = ?",
                (spec_hash,),
            ).fetchall():
                refs.append(
                    CitationRef(
                        kind="model",
                        ref=r["sibling_group"],
                        detail=f"training partition role={r['role']}",
                    )
                )
    finally:
        con.close()
    return refs


def bindings_without_uses(db_path: Path | str, spec_hash: str) -> list[str]:
    """Experiment Datasets bound to this spec that have recorded no use yet."""
    con = _connect(db_path)
    try:
        if not (
            _table_exists(con, "experiment_datasets")
            and _table_exists(con, "dataset_uses")
        ):
            return []
        rows = con.execute(
            "SELECT e.experiment_id FROM experiment_datasets e "
            "WHERE e.dataset_hash = ? AND NOT EXISTS ("
            "  SELECT 1 FROM dataset_uses u WHERE u.experiment_id = e.experiment_id"
            ") ORDER BY e.experiment_id",
            (spec_hash,),
        ).fetchall()
        return [r["experiment_id"] for r in rows]
    finally:
        con.close()


def is_retirable(db_path: Path | str, spec_hash: str) -> tuple[bool, list[str]]:
    """Report whether DG-5 permits retirement, without attempting it."""
    refs = dg5_citations(db_path, spec_hash)
    errors = [
        f"DG-5: cited by {r.kind} '{r.ref}' ({r.detail})" for r in refs
    ]
    return (not errors, errors)


def retire_dataset(
    db_path: Path | str,
    spec_hash: str,
    *,
    actor: str,
    note: str = "",
) -> RetirementReport:
    """Retire a dataset, refusing under DG-5 if anything cites it.

    Retirement removes the dataset from `CITABLE_PHASES`. That is the whole
    point of the phase, and also the whole danger: every evaluation that cited
    it would become unreconstructable. So the citation check is the gate, and
    it precedes the transition rather than accompanying it.

    History is preserved unconditionally -- retirement appends a transition and
    edits nothing. A retired dataset's declaration, seal, materialisations, and
    citations all remain readable; only its *future* citability ends.
    """
    if not str(actor).strip():
        raise HistoryError("retirement requires a named actor")

    record = reg.get_specification(db_path, spec_hash)
    if record is None:
        raise reg.UnknownSpecificationError(
            f"no specification {spec_hash!r} in this registry"
        )

    refs = dg5_citations(db_path, spec_hash)
    pending = bindings_without_uses(db_path, spec_hash)

    if refs:
        report = RetirementReport(
            spec_hash=spec_hash,
            retired=False,
            blocking_citations=tuple(refs),
            bindings_without_uses=tuple(pending),
            note="refused under DG-5",
        )
        listed = "; ".join(f"{r.kind} '{r.ref}' ({r.detail})" for r in refs)
        raise RetirementRefused(
            f"DG-5: dataset {spec_hash[:12]} is cited and can never be retired. "
            f"Cited by: {listed}"
        )

    reg.retire(db_path, spec_hash)

    return RetirementReport(
        spec_hash=spec_hash,
        retired=True,
        bindings_without_uses=tuple(pending),
        note=note or f"retired by {actor}",
    )


def supersede_dataset(
    db_path: Path | str,
    old_hash: str,
    new_hash: str,
    *,
    change_class: str,
    actor: str,
) -> dict:
    """Supersede one dataset by another, then prove the predecessor survived.

    §10.2: *"A superseded dataset remains fully resolvable and citable."*
    Supersession is therefore not a soft delete. This delegates the transition
    to WBS 4.1 -- which already enforces the arc and records ``superseded_by``
    -- and then verifies the property §10.2 actually asserts, because a
    transition that succeeded is not the same as a predecessor that survived.
    """
    if not str(actor).strip():
        raise HistoryError("supersession requires a named actor")

    before = _predecessor_fingerprint(db_path, old_hash)

    reg.supersede(
        db_path, old_hash, successor_hash=new_hash, change_class=change_class
    )

    ok, errors = verify_supersession_preserved(db_path, old_hash, before=before)
    if not ok:
        raise SupersessionError(
            "supersession did not preserve the predecessor (§10.2): "
            + "; ".join(errors)
        )

    return {
        "superseded": old_hash,
        "superseded_by": new_hash,
        "change_class": change_class,
        "actor": actor,
        "predecessor_still_citable": True,
        "ts": _utc_now(),
    }


def _predecessor_fingerprint(db_path: Path | str, spec_hash: str) -> dict:
    """Everything about a dataset that supersession must leave untouched."""
    con = _connect(db_path)
    try:
        sealed = None
        if _table_exists(con, "sealed_datasets"):
            row = con.execute(
                "SELECT * FROM sealed_datasets WHERE spec_hash = ?", (spec_hash,)
            ).fetchone()
            sealed = dict(row) if row else None
        cites = [
            (c.cited_kind, c.cited_id) for c in ci.cites(db_path, spec_hash)
        ]
        record = reg.get_specification(db_path, spec_hash)
        declaration = record.get("declaration_json") if record else None
    finally:
        con.close()
    return {"sealed": sealed, "cites": sorted(cites), "declaration": declaration}


def verify_supersession_preserved(
    db_path: Path | str, spec_hash: str, *, before: dict | None = None
) -> tuple[bool, list[str]]:
    """§10.2 -- the predecessor is still resolvable and citable."""
    errors: list[str] = []

    record = reg.get_specification(db_path, spec_hash)
    if record is None:
        return (False, [f"predecessor {spec_hash[:12]} is no longer resolvable"])

    if record["phase"] != "Superseded":
        errors.append(
            f"predecessor phase is {record['phase']!r}, expected 'Superseded'"
        )

    if not reg.is_citable(db_path, spec_hash):
        errors.append(
            f"predecessor {spec_hash[:12]} is no longer citable; §10.2 requires "
            "a superseded dataset to remain citable"
        )

    if before is not None:
        after = _predecessor_fingerprint(db_path, spec_hash)
        if after["sealed"] != before["sealed"]:
            errors.append("predecessor's sealed record was mutated")
        if after["cites"] != before["cites"]:
            errors.append("predecessor's citations were mutated")
        if after["declaration"] != before["declaration"]:
            errors.append("predecessor's declaration was mutated")

    return (not errors, errors)


def flag_stale_ceiling(
    db_path: Path | str,
    spec_hash: str,
    *,
    cited_kind: str,
    cited_id: str,
    reason: str,
    flagged_by: str,
) -> int:
    """Record that a sealed dataset's admissibility ceiling may be stale.

    Resolves HZ-4.5-2 by *flagging*, never recomputing.

    A sealed dataset's ceiling was computed from its inputs as they stood at
    seal time. If an input is later superseded or flagged, the ceiling may no
    longer be justified. The tempting fix -- recompute and update the sealed
    row -- is forbidden: DG-3 makes a sealed specification immutable, and a
    ceiling that silently moved would destroy the reproducibility of every
    evaluation that already cited it. A dataset whose recorded ceiling changes
    after the fact is no longer the dataset that was evaluated.

    So the doubt is recorded *beside* the seal, never inside it. Downstream
    governance decides what to do about it; this layer only makes it visible.
    """
    _assert_history_schema(db_path)
    if not str(flagged_by).strip():
        raise HistoryError("a staleness flag requires a named flagger")
    if not str(reason).strip():
        raise HistoryError("a staleness flag requires a reason")

    if reg.get_specification(db_path, spec_hash) is None:
        raise reg.UnknownSpecificationError(
            f"no specification {spec_hash!r} in this registry"
        )

    con = _connect(db_path)
    try:
        cur = con.execute(
            "INSERT OR IGNORE INTO ceiling_staleness_flags "
            "(spec_hash, cited_kind, cited_id, reason, flagged_at, flagged_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (spec_hash, cited_kind, cited_id, reason, _utc_now(), flagged_by),
        )
        con.commit()
        if cur.rowcount == 0:
            row = con.execute(
                "SELECT id FROM ceiling_staleness_flags WHERE spec_hash = ? "
                "AND cited_kind = ? AND cited_id = ? AND reason = ?",
                (spec_hash, cited_kind, cited_id, reason),
            ).fetchone()
            return int(row["id"])
        return int(cur.lastrowid)
    finally:
        con.close()


def stale_ceilings(
    db_path: Path | str, spec_hash: str | None = None
) -> list[StalenessFlag]:
    """Every recorded ceiling-staleness flag, optionally for one dataset."""
    _assert_history_schema(db_path)
    con = _connect(db_path)
    try:
        if spec_hash is None:
            rows = con.execute(
                "SELECT * FROM ceiling_staleness_flags ORDER BY id"
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM ceiling_staleness_flags WHERE spec_hash = ? "
                "ORDER BY id",
                (spec_hash,),
            ).fetchall()
        return [StalenessFlag(**dict(r)) for r in rows]
    finally:
        con.close()


def detect_stale_ceilings(
    db_path: Path | str, *, cited_kind: str, cited_id: str, flagged_by: str
) -> list[str]:
    """Flag every sealed dataset citing an input that has just been invalidated.

    The blast radius is computed by the WBS 4.2 citation index rather than
    rediscovered here -- that index exists precisely so this question is
    answerable, and a second traversal would be free to disagree with it.
    """
    affected = ci.cited_by(db_path, cited_kind, cited_id)
    flagged: list[str] = []
    for dataset_hash in sorted(set(affected)):
        record = reg.get_specification(db_path, dataset_hash)
        if record is None or record["phase"] not in reg.CITABLE_PHASES:
            continue
        flag_stale_ceiling(
            db_path,
            dataset_hash,
            cited_kind=cited_kind,
            cited_id=cited_id,
            reason=f"cited {cited_kind} {cited_id} was invalidated after seal",
            flagged_by=flagged_by,
        )
        flagged.append(dataset_hash)
    return flagged


def verify_l3_composes(
    db_path: Path | str, spec_hash: str, as_of: str | None = None
) -> tuple[bool, list[str]]:
    """L3 must compose with L1/L2 rather than replace them (§16).

    L3 answers *which dataset, in what phase, at what time*. L1 answers *which
    exact bytes*. A reconstruction that named the right dataset but could not
    recover its bytes would not be an audit answer. This checks the two agree:
    if the dataset was sealed by ``as_of``, its L1 pin must still verify.
    """
    errors: list[str] = []

    phase = derive_phase_at(db_path, spec_hash, as_of)
    if phase is None:
        return (False, [f"dataset {spec_hash[:12]} did not exist at {as_of}"])

    con = _connect(db_path)
    try:
        has_pins = _table_exists(con, "snapshot_pins")
        pinned = None
        if has_pins:
            row = con.execute(
                "SELECT snapshot_sha256 FROM snapshot_pins WHERE spec_hash = ?",
                (spec_hash,),
            ).fetchone()
            pinned = row["snapshot_sha256"] if row else None
    finally:
        con.close()

    if phase in reg.CITABLE_PHASES and has_pins and pinned is None:
        errors.append(
            f"dataset {spec_hash[:12]} was {phase} at {as_of} but has no L1 pin; "
            "L3 can name it but L1 cannot recover its bytes"
        )

    return (not errors, errors)


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="history",
        description="WBS 4.11 -- retirement, supersession, --as-of reconstruction",
    )
    parser.add_argument("--db", required=True, help="dataset builder store")
    parser.add_argument(
        "--as-of",
        default=None,
        help="reconstruct the registry as it stood at this UTC instant",
    )
    parser.add_argument("--spec", default=None, help="restrict to one spec hash")
    parser.add_argument(
        "--check-completeness",
        action="store_true",
        help="assert every specification has recorded history",
    )
    args = parser.parse_args(argv)

    if args.check_completeness:
        ok, errors = history_completeness(args.db)
        for e in errors:
            print(f"  HISTORY-GAP: {e}")
        print("History completeness:", "OK" if ok else f"{len(errors)} gap(s)")
        return 0 if ok else 1

    if args.spec:
        record = reconstruct_specification_at(args.db, args.spec, args.as_of)
        if record is None:
            print(f"spec {args.spec[:12]} did not exist at {args.as_of}")
            return 1
        print(json.dumps(record, indent=2, default=str, sort_keys=True))
        return 0

    report = reconstruct_registry_at(args.db, args.as_of)
    print(f"Registry as of {report.as_of or 'now'}:")
    for spec_hash, phase in sorted(report.phases.items()):
        print(f"  {spec_hash[:12]}  {phase}")
    print(f"  ({len(report.phases)} present, {len(report.absent)} not yet declared)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
