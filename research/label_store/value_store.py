"""
Label Value Store (ADR-I011 §1.1, §7, §9.1, §9.2, §9.3, §12 Tier C, §12.2,
LG2, LG4; Phase 3 Label Store roadmap v1.1.0, WBS 3.2 -- the second Label
Store milestone).

Two tiers, split by the §1.1 asymmetry -- NOT by convenience, and with NO
shared implementation between them, because the asymmetry is the whole
design problem this ADR exists to solve:

  - Research tier: Research Labels. Evictable cache. No authority. If
    lost, nothing is lost (§7). Addressed by (spec_hash, corpus_version,
    as_of) -- identical to ADR-I010 §7.2 (§9.1).
  - Forward tier: Forward Labels (incl. the placeholder for what will
    become a Promotion Label). Indefinite retention. No authority --
    verifiable, not authoritative. If lost, IRREPLACEABLE (§7). Addressed
    by (spec_hash, subject_id, declared_at) -- the declaration instant is
    part of the address because that IS the fact being recorded (§9.1).

RESEARCH TIER, §9.3 "APPLIES VERBATIM" -- READ THIS BEFORE ASSUMING IT
MIRRORS FEATURE STORE'S CACHE TIER. `research/feature_store/value_store.py`
has TWO tiers (cache: no verification, freely overwritten; materialised:
write-once, verified). ADR-I011 §7's table gives Research Labels only ONE
tier ("Evictable cache"), yet §9.3 explicitly invokes "ADR-I010 §7.3
verbatim: a stored value disagreeing with recomputation is wrong by
definition, and the disagreement is a reported divergence" for Research
Labels specifically. Since correct recomputation is deterministic, an
observed disagreement at at a fixed address is always a bug signal (a
non-deterministic binding, a corpus-version collision), never a
legitimate revision -- so this tier folds cache-tier storage AND
materialisation-tier verification into the one concept ADR-I011 actually
gives it, rather than mirroring Feature Store's laxer, unverified cache.
Eviction (an explicit, intentional DELETE) is unaffected by this: §7
still says "if lost, nothing is lost," so evicting and later recomputing
fresh at the same address is expected and safe -- it is an unrequested
differing WRITE at a still-populated address that signals a defect.

FORWARD TIER -- NO ANALOGUE IN THE FEATURE STORE (§12.2). Six states:
Declared -> Pending -> Matured -> Flagged -> Adjudicated, with Pending ->
Void as the other branch. Exactly the five arcs ADR-I011's own
stateDiagram-v2 draws, no more: Declared->Pending, Pending->Matured,
Pending->Void, Matured->Flagged, Flagged->Adjudicated. Void and
Adjudicated are permanent (LG4) purely because no arc ever leaves them --
not because of a separately-coded "terminal state" rule, which would be
redundant given the arc table already forbids it.

STORAGE-LAYER FILL-ONCE (§9.2, §12 Tier C, LG2) -- THE CENTRAL DELIVERABLE
OF THIS MILESTONE. `research/feature_store/registry.py`'s phase
transitions are Python-guarded only (an allowed-arc table plus a
WHERE-clause concurrency check on UPDATE) -- explicitly NOT sufficient
here. WBS 3.1's audit (`A-1`) found the real H6 collector's fill-once
guard is application-level only (a bare `WHERE label_available = 0`
clause, zero triggers on the real database) and named the precedent to
follow instead: `research/evidence_library/contract_registry.py`'s
`trg_contracts_guard_update`, a `BEFORE UPDATE` trigger that permits ONLY
documented lifecycle fields to change, along ONLY documented arcs, and
`RAISE(ABORT, ...)`s on anything else. `trg_label_value_forward_guard`
below is the same pattern, applied to `outcome_json` /
`input_revision_set_json` / `matured_at`: once a row has ever been
`Matured` (state history is monotonic per the arc table, so this is
equivalent to "state is currently Matured, Flagged, or Adjudicated"),
those three columns are frozen at the DATABASE layer -- a raw `sqlite3`
`UPDATE` bypassing every Python function in this module still fails.

WHAT THIS MODULE DOES NOT DO, DELIBERATELY. It does not check `spec_hash`
against `research/label_store/registry.py` (no import, no foreign key) --
mirrors `feature_store/value_store.py`'s identical scoped simplification:
"this module holds no binding-resolution logic of its own." It does not
validate maturity-rule timing (e.g., that a horizon truly has elapsed
before `mature_forward_label()` is called) -- Tier B (declaration
integrity) and the real timing discipline belong to WBS 3.4/3.5, which
apply this mechanism to real H6 data; this milestone provides the
mechanism only, exactly as `registry.py`'s Tier A validated field
completeness without validating binding equivalence (a later caller's
job). It does not interpret `input_revision_set` against
`research/storage/raw_lake.py`'s actual revision index -- that
integration is WBS 3.6's, which is the first milestone that lists
`raw_lake.py` as a dependency; here it is accepted and stored verbatim as
whatever JSON-serialisable value the caller supplies.

WBS 3.5 ADDITION -- EVIDENTIARY STANDING (§10.1, §12 Tier B's
standing-at-declaration half, LG3). `standing` is a write-once column,
guarded by the same `trg_label_value_forward_guard` trigger this
docstring already describes: `NULL -> <value>` is the only legal
transition (the first, and only, assignment), mirroring
`contract_registry.py`'s own `activation_receipt_status` precedent
exactly ("NULL -> anything... is legal (first write)... every other
transition... is tamper"). `declare_forward_label()` gained an OPTIONAL
`standing` keyword argument (default `None`, fully backward compatible --
every WBS 3.2/3.4 caller and test is unaffected) so a LIVE declaration can
set standing inline, satisfying LG3's literal "assigned at declaration,
not later." For rows already declared before this column existed (WBS
3.4's 148 H6 labels), `set_standing()` performs the same one-time
`NULL -> value` backfill assignment as a separate, explicit act -- this is
LG3-compliant because these rows never had a standing value at all, not a
reclassification of an existing one. The resolution LOGIC (which value to
assign) lives in `research/label_store/standing.py`, not here -- this
module only stores and protects the value once decided, exactly as it
already does for every other forward-tier field.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

# ADR-I011 §10.1's evidentiary standing vocabulary, verbatim. Promotion
# evidence is a valid stored value (the schema/CHECK constraint admits it,
# since §8.1 describes it as a Forward Label that has matured, is in an
# admissible lane, and is use-counted) but no function in this module or
# in research/label_store/standing.py (WBS 3.5) ever writes it -- that
# upgrade path is LG7's use-counting against a sealed decision, Phase 4's
# own concern, not built here.
STANDING_VALUES = frozenset({"Context", "Forward evidence", "Promotion evidence"})

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS label_value_research (
    spec_hash       TEXT NOT NULL,
    corpus_version  TEXT NOT NULL,
    as_of           TEXT NOT NULL,
    values_json     TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    computed_at     TEXT NOT NULL,
    PRIMARY KEY (spec_hash, corpus_version, as_of)
);

CREATE TABLE IF NOT EXISTS label_value_forward (
    spec_hash                TEXT NOT NULL,
    subject_id                TEXT NOT NULL,
    declared_at                TEXT NOT NULL,
    declared_by                TEXT NOT NULL,
    maturity_state            TEXT NOT NULL,
    observed_at                TEXT,
    outcome_json               TEXT,
    input_revision_set_json TEXT,
    matured_at                 TEXT,
    flagged_at                 TEXT,
    flag_reason                TEXT,
    adjudicated_at             TEXT,
    adjudication_decision   TEXT,
    void_at                    TEXT,
    void_reason                TEXT,
    standing                   TEXT
                                   CHECK (standing IS NULL OR standing IN
                                          ('Context','Forward evidence','Promotion evidence')),
    PRIMARY KEY (spec_hash, subject_id, declared_at)
);
"""

# ADR-I011 §12.2's stateDiagram-v2, transcribed as an allowed-arc table --
# exactly the five drawn arcs, nothing more.
_ALLOWED_MATURITY_ARCS: frozenset[tuple[str, str]] = frozenset({
    ("Declared", "Pending"),
    ("Pending", "Matured"),
    ("Pending", "Void"),
    ("Matured", "Flagged"),
    ("Flagged", "Adjudicated"),
})

_ARC_STR = ", ".join(f"({a}->{b})" for a, b in sorted(_ALLOWED_MATURITY_ARCS))

# The BEFORE UPDATE trigger implementing the module docstring's central
# claim. Deliberately mirrors contract_registry.py's
# trg_contracts_guard_update in shape: static RAISE() message literals only
# (no OLD./NEW. concatenation -- observed to be rejected by some SQLite
# builds), Python-level pre-checks in this module raise the detailed,
# dynamic message first; this trigger is the last-resort backstop for a
# write that bypasses Python entirely.
_GUARD_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS trg_label_value_forward_guard
BEFORE UPDATE ON label_value_forward
BEGIN
    SELECT CASE
        WHEN NEW.spec_hash   != OLD.spec_hash
          OR NEW.subject_id   != OLD.subject_id
          OR NEW.declared_at != OLD.declared_at
          OR NEW.declared_by != OLD.declared_by
        THEN RAISE(ABORT, 'LABEL_TAMPER: immutable identity field changed')
    END;
    SELECT CASE
        WHEN OLD.maturity_state IN ('Matured','Flagged','Adjudicated')
         AND (NEW.outcome_json             IS NOT OLD.outcome_json
              OR NEW.input_revision_set_json IS NOT OLD.input_revision_set_json
              OR NEW.matured_at              IS NOT OLD.matured_at)
        THEN RAISE(ABORT, 'LABEL_TAMPER: a matured Forward Label is never rewritten (fill-once, ADR-I011 §9.2)')
    END;
    SELECT CASE
        WHEN OLD.observed_at IS NOT NULL AND NEW.observed_at IS NOT OLD.observed_at
        THEN RAISE(ABORT, 'LABEL_TAMPER: observed_at is write-once')
    END;
    SELECT CASE
        WHEN OLD.void_at IS NOT NULL AND NEW.void_at IS NOT OLD.void_at
        THEN RAISE(ABORT, 'LABEL_TAMPER: void_at is write-once')
    END;
    SELECT CASE
        WHEN OLD.flagged_at IS NOT NULL AND NEW.flagged_at IS NOT OLD.flagged_at
        THEN RAISE(ABORT, 'LABEL_TAMPER: flagged_at is write-once')
    END;
    SELECT CASE
        WHEN OLD.adjudicated_at IS NOT NULL AND NEW.adjudicated_at IS NOT OLD.adjudicated_at
        THEN RAISE(ABORT, 'LABEL_TAMPER: adjudicated_at is write-once')
    END;
    SELECT CASE
        WHEN OLD.standing IS NOT NULL AND NEW.standing IS NOT OLD.standing
        THEN RAISE(ABORT, 'LABEL_TAMPER: standing is assigned at declaration and is immutable thereafter (ADR-I011 LG3)')
    END;
    SELECT CASE
        WHEN NEW.maturity_state != OLD.maturity_state AND NOT (
             (OLD.maturity_state = 'Declared' AND NEW.maturity_state = 'Pending')
          OR (OLD.maturity_state = 'Pending'  AND NEW.maturity_state = 'Matured')
          OR (OLD.maturity_state = 'Pending'  AND NEW.maturity_state = 'Void')
          OR (OLD.maturity_state = 'Matured'  AND NEW.maturity_state = 'Flagged')
          OR (OLD.maturity_state = 'Flagged'  AND NEW.maturity_state = 'Adjudicated')
        )
        THEN RAISE(ABORT, 'LABEL_TAMPER: illegal maturity_state transition')
    END;
END;
"""


class LabelValueStoreError(ValueError):
    """Base class for label value-store failures."""


class LabelDivergenceEvent(LabelValueStoreError):
    """
    Research tier only (§9.3, ADR-I010 §7.3 verbatim): a stored value
    disagrees with a recomputation, or a write was attempted with content
    differing from what is already stored at the same address. Reported,
    never silently reconciled (IQROS-P3) -- this module never overwrites
    a research value to make a divergence go away.
    """

    def __init__(self, spec_hash: str, corpus_version: str, as_of: str, detail: str):
        self.spec_hash = spec_hash
        self.corpus_version = corpus_version
        self.as_of = as_of
        self.detail = detail
        super().__init__(
            f"divergence at ({spec_hash[:12]}..., {corpus_version}, {as_of}): {detail}"
        )


class LabelTransitionError(LabelValueStoreError):
    """
    Forward tier only: raised by this module's Python-level pre-check when
    a requested maturity-state transition is not one of the five allowed
    arcs, or the row's current state does not match what the caller
    expected (concurrent-modification guard). The database trigger is the
    storage-layer backstop for the same rule -- this is the clear,
    early-raising counterpart for a well-behaved caller going through this
    module's own functions.
    """


@dataclass(frozen=True)
class ResolvedResearchValue:
    values: Any
    source: str  # "cached" | "computed"


def init_db(path: Path | str) -> None:
    """Idempotent CREATE-only schema-ensure, mirroring registry.py's
    never-ALTER discipline for FRESH tables. One narrow exception, mirroring
    contract_registry.py's own init_db() precedent: WBS 3.5 adds the
    `standing` column to a table WBS 3.4 already populated for real (148
    H6 rows) in this repository's own local database, so a bare `CREATE
    TABLE IF NOT EXISTS` would silently do nothing on that pre-existing
    file. `PRAGMA table_info` is checked first; `ALTER TABLE ADD COLUMN`
    only fires when the column is genuinely absent -- a true schema
    migration, gated and explicit, not a repair of data. Installs the
    forward-tier guard trigger on every call (`CREATE TRIGGER IF NOT
    EXISTS` -- a no-op if already present; the migrated trigger body always
    reflects the current, standing-aware guard, matching
    contract_registry.py's own drop-and-recreate discipline for a trigger
    whose body changed)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.executescript(_SCHEMA_SQL)

        columns = {row[1] for row in con.execute("PRAGMA table_info(label_value_forward)")}
        if "standing" not in columns:
            con.execute(
                "ALTER TABLE label_value_forward ADD COLUMN standing TEXT "
                "CHECK (standing IS NULL OR standing IN "
                "('Context','Forward evidence','Promotion evidence'))"
            )

        con.execute("DROP TRIGGER IF EXISTS trg_label_value_forward_guard")
        con.executescript(_GUARD_TRIGGER_SQL)
        con.commit()
    finally:
        con.close()


def _values_json(values: Any) -> str:
    return json.dumps(values, sort_keys=True, separators=(",", ":"))


def _content_hash(values: Any) -> str:
    return hashlib.sha256(_values_json(values).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Research tier -- evictable cache, §9.3 verification applied at write time
# ---------------------------------------------------------------------------

def put_research_value(
    db_path: Path | str, spec_hash: str, corpus_version: str, as_of: str,
    values: Any, computed_at: str,
) -> str:
    """
    Write a Research Label value set. If nothing is stored yet at this
    address, writes it and returns the new content hash. If something IS
    already stored at this address:
      - identical content -> idempotent no-op, returns the existing hash
      - differing content -> LabelDivergenceEvent (never overwritten --
        §9.3, a fixed address should be deterministic, so a difference is
        always a bug, never a legitimate revision)
    """
    new_hash = _content_hash(values)
    con = sqlite3.connect(db_path)
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT content_hash FROM label_value_research "
            "WHERE spec_hash = ? AND corpus_version = ? AND as_of = ?",
            (spec_hash, corpus_version, as_of),
        ).fetchone()
        if row is not None:
            con.rollback()
            if row[0] == new_hash:
                return row[0]
            raise LabelDivergenceEvent(
                spec_hash, corpus_version, as_of,
                f"a value already exists with content_hash={row[0][:12]}..., "
                f"new computation hashes to {new_hash[:12]}... -- never overwritten",
            )

        con.execute(
            "INSERT INTO label_value_research "
            "(spec_hash, corpus_version, as_of, values_json, content_hash, computed_at) "
            "VALUES (?,?,?,?,?,?)",
            (spec_hash, corpus_version, as_of, _values_json(values), new_hash, computed_at),
        )
        con.commit()
        return new_hash
    finally:
        con.close()


def get_research_value(db_path: Path | str, spec_hash: str, corpus_version: str, as_of: str) -> Any | None:
    """
    Read a Research Label value set, verifying it against its own recorded
    content hash first (§9.3). A hash mismatch (on-disk corruption, or a
    defect that bypassed put_research_value()'s own write path) raises
    LabelDivergenceEvent rather than returning corrupted data silently.
    """
    con = sqlite3.connect(db_path)
    try:
        row = con.execute(
            "SELECT values_json, content_hash FROM label_value_research "
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
        raise LabelDivergenceEvent(
            spec_hash, corpus_version, as_of,
            f"stored content_hash={stored_hash[:12]}... does not match recomputed "
            f"hash={actual_hash[:12]}... of the stored bytes -- possible corruption",
        )
    return json.loads(values_json)


def evict_research_value(db_path: Path | str, spec_hash: str, corpus_version: str, as_of: str) -> None:
    """Evicting a Research Label value loses nothing of record (§7: "if
    lost, nothing is lost") -- always safe, and distinct from an
    unrequested differing write at a still-populated address."""
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "DELETE FROM label_value_research "
            "WHERE spec_hash = ? AND corpus_version = ? AND as_of = ?",
            (spec_hash, corpus_version, as_of),
        )
        con.commit()
    finally:
        con.close()


def resolve_research(
    db_path: Path | str,
    spec_hash: str,
    corpus_version: str,
    as_of: str,
    compute_fn: Callable[[], Any],
    *,
    computed_at: str,
) -> ResolvedResearchValue:
    """
    Serve a Research Label value set for (spec_hash, corpus_version,
    as_of): a stored, verified value if one exists ("cached"); otherwise
    compute via `compute_fn()`, store it, and serve it ("computed"). A
    Research Label is fully mature and deterministic at computation
    (§8.1) -- there is no maturity lifecycle here, unlike the forward
    tier below.
    """
    existing = get_research_value(db_path, spec_hash, corpus_version, as_of)
    if existing is not None:
        return ResolvedResearchValue(values=existing, source="cached")

    computed = compute_fn()
    put_research_value(db_path, spec_hash, corpus_version, as_of, computed, computed_at)
    return ResolvedResearchValue(values=computed, source="computed")


# ---------------------------------------------------------------------------
# Forward tier -- materialised records, six-state maturity lifecycle
# ---------------------------------------------------------------------------

def declare_forward_label(
    db_path: Path | str, spec_hash: str, subject_id: str, declared_at: str, declared_by: str,
    standing: str | None = None,
) -> None:
    """
    Write the Declared placeholder (§12.2: "Placeholder exists. Outcome
    unknown and unknowable."). This is the act of asking the question
    without knowing the answer -- the property that distinguishes forward
    evidence from retrospective fitting (§12.2's own note).

    This function performs NO check that `declared_at` truly precedes the
    subject's horizon opening (Tier B, declaration integrity) -- that
    validation belongs to whichever milestone applies this mechanism to
    real data (WBS 3.4/3.5), exactly as registry.py's Tier A validates
    field completeness without validating binding equivalence.

    `standing` (WBS 3.5, §10.1, §12 Tier B): OPTIONAL, default `None`
    -- every WBS 3.2/3.4 caller that omits it is unaffected. When a caller
    DOES pass it (the correct path for a live, real-time declaration, per
    LG3's "assigned at declaration, not later"), it must be one of
    `STANDING_VALUES`; validated here, and re-validated by the CHECK
    constraint if this check is ever bypassed. Once written, it is
    write-once -- see `trg_label_value_forward_guard`.
    """
    for field_name, value in (
        ("spec_hash", spec_hash), ("subject_id", subject_id),
        ("declared_at", declared_at), ("declared_by", declared_by),
    ):
        if not value or not value.strip():
            raise LabelValueStoreError(f"{field_name} is required and must be non-empty (Ph6)")
    if standing is not None and standing not in STANDING_VALUES:
        raise LabelValueStoreError(
            f"standing must be one of {sorted(STANDING_VALUES)} or None, got {standing!r}"
        )

    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "INSERT INTO label_value_forward "
            "(spec_hash, subject_id, declared_at, declared_by, maturity_state, standing) "
            "VALUES (?,?,?,?,'Declared',?)",
            (spec_hash, subject_id, declared_at, declared_by, standing),
        )
        con.commit()
    finally:
        con.close()


def set_standing(
    db_path: Path | str, spec_hash: str, subject_id: str, declared_at: str, standing: str,
) -> None:
    """
    One-time backfill assignment for a row declared before `standing`
    existed or before a caller supplied it inline (WBS 3.4's 148 H6 rows,
    concretely). LG3-compliant: these rows never held a standing value, so
    this is the row's first assignment, not a reclassification of an
    existing one -- exactly the `NULL -> value` arc
    `trg_label_value_forward_guard` permits.

    Refuses (does not silently no-op) if the row does not exist, or if
    standing is already set -- both are `LabelTransitionError`, mirroring
    `_apply_forward_transition()`'s own concurrency-guard shape elsewhere
    in this module.
    """
    if standing not in STANDING_VALUES:
        raise LabelValueStoreError(f"standing must be one of {sorted(STANDING_VALUES)}, got {standing!r}")

    con = sqlite3.connect(db_path)
    try:
        current = con.execute(
            f"SELECT standing FROM label_value_forward WHERE {_forward_key_where()}",
            (spec_hash, subject_id, declared_at),
        ).fetchone()
        if current is None:
            raise LabelValueStoreError(
                f"no forward label declared at (spec_hash={spec_hash[:12]}..., "
                f"subject_id={subject_id!r}, declared_at={declared_at!r})"
            )
        if current[0] is not None:
            raise LabelTransitionError(
                f"refusing to proceed: standing is already {current[0]!r} for this label "
                f"-- standing is assigned at declaration and is immutable thereafter (LG3)"
            )

        cur = con.execute(
            f"UPDATE label_value_forward SET standing = ? "
            f"WHERE {_forward_key_where()} AND standing IS NULL",
            (standing, spec_hash, subject_id, declared_at),
        )
        if cur.rowcount != 1:
            raise LabelTransitionError(
                "refusing to proceed: standing was set concurrently between the read and this write"
            )
        con.commit()
    finally:
        con.close()


def get_forward_label(db_path: Path | str, spec_hash: str, subject_id: str, declared_at: str) -> dict | None:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT * FROM label_value_forward "
            "WHERE spec_hash = ? AND subject_id = ? AND declared_at = ?",
            (spec_hash, subject_id, declared_at),
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        if d["outcome_json"] is not None:
            d["outcome"] = json.loads(d["outcome_json"])
        if d["input_revision_set_json"] is not None:
            d["input_revision_set"] = json.loads(d["input_revision_set_json"])
        return d
    finally:
        con.close()


def _forward_key_where() -> str:
    return "spec_hash = ? AND subject_id = ? AND declared_at = ?"


def _apply_forward_transition(
    db_path: Path | str, spec_hash: str, subject_id: str, declared_at: str,
    from_state: str, to_state: str, set_clause: str, set_values: tuple,
) -> None:
    """Shared plumbing for every forward-tier transition: a Python-level
    pre-check raising LabelTransitionError with a clear message (mirroring
    contract_lifecycle.py's precedent of checking before writing), then a
    WHERE-guarded UPDATE (concurrency backstop), with
    trg_label_value_forward_guard as the final, un-bypassable backstop
    against any write that skips this module's functions entirely."""
    if (from_state, to_state) not in _ALLOWED_MATURITY_ARCS:
        raise LabelTransitionError(
            f"{from_state!r} -> {to_state!r} is not an allowed arc (ADR-I011 §12.2). "
            f"Allowed arcs: {_ARC_STR}"
        )

    con = sqlite3.connect(db_path)
    try:
        current = con.execute(
            f"SELECT maturity_state FROM label_value_forward WHERE {_forward_key_where()}",
            (spec_hash, subject_id, declared_at),
        ).fetchone()
        if current is None:
            raise LabelValueStoreError(
                f"no forward label declared at (spec_hash={spec_hash[:12]}..., "
                f"subject_id={subject_id!r}, declared_at={declared_at!r})"
            )
        if current[0] != from_state:
            raise LabelTransitionError(
                f"refusing to proceed: forward label is in state {current[0]!r}, "
                f"not {from_state!r} at write time (concurrent modification, or wrong "
                f"expected state)"
            )

        cur = con.execute(
            f"UPDATE label_value_forward SET maturity_state = ?, {set_clause} "
            f"WHERE {_forward_key_where()} AND maturity_state = ?",
            (to_state, *set_values, spec_hash, subject_id, declared_at, from_state),
        )
        if cur.rowcount != 1:
            raise LabelTransitionError(
                f"refusing to proceed: forward label was not in state {from_state!r} "
                f"at write time (concurrent modification)"
            )
        con.commit()
    finally:
        con.close()


def observe_forward_label(
    db_path: Path | str, spec_hash: str, subject_id: str, declared_at: str, observed_at: str,
) -> None:
    """Declared -> Pending: "subject observed, horizon open" (§12.2)."""
    if not observed_at or not observed_at.strip():
        raise LabelValueStoreError("observed_at is required and must be non-empty (Ph6)")
    _apply_forward_transition(
        db_path, spec_hash, subject_id, declared_at, "Declared", "Pending",
        "observed_at = ?", (observed_at,),
    )


def mature_forward_label(
    db_path: Path | str, spec_hash: str, subject_id: str, declared_at: str,
    outcome: Any, input_revision_set: Any, matured_at: str,
) -> None:
    """
    Pending -> Matured: "horizon elapsed, outcome recorded" (§12.2). This
    is the fill-once moment (§9.2, LG2) -- after this write,
    trg_label_value_forward_guard freezes outcome_json,
    input_revision_set_json, and matured_at permanently, at the storage
    layer, regardless of which code path attempts to change them.

    `input_revision_set` records the corpus revision set the outcome was
    computed from (§12 Tier C, §11.3) -- accepted and stored verbatim as
    whatever JSON-serialisable value the caller supplies; reconciling it
    against `research/storage/raw_lake.py`'s actual revision index is
    WBS 3.6's job, not this milestone's.
    """
    if not matured_at or not matured_at.strip():
        raise LabelValueStoreError("matured_at is required and must be non-empty (Ph6)")
    if input_revision_set is None:
        raise LabelValueStoreError(
            "input_revision_set is required (ADR-I011 §12 Tier C: 'input revision set recorded') "
            "-- an empty list is a valid declaration, omission is not"
        )
    _apply_forward_transition(
        db_path, spec_hash, subject_id, declared_at, "Pending", "Matured",
        "outcome_json = ?, input_revision_set_json = ?, matured_at = ?",
        (_values_json(outcome), _values_json(input_revision_set), matured_at),
    )


def void_forward_label(
    db_path: Path | str, spec_hash: str, subject_id: str, declared_at: str,
    void_at: str, void_reason: str,
) -> None:
    """Pending -> Void: "subject invalidated before maturity" (§12.2).
    Permanent record (LG4) -- no arc ever leaves Void."""
    if not void_at or not void_at.strip():
        raise LabelValueStoreError("void_at is required and must be non-empty (Ph6)")
    if not void_reason or not void_reason.strip():
        raise LabelValueStoreError("void_reason is required and must be non-empty (Ph6)")
    _apply_forward_transition(
        db_path, spec_hash, subject_id, declared_at, "Pending", "Void",
        "void_at = ?, void_reason = ?", (void_at, void_reason),
    )


def flag_forward_label(
    db_path: Path | str, spec_hash: str, subject_id: str, declared_at: str,
    flagged_at: str, flag_reason: str,
) -> None:
    """
    Matured -> Flagged: "cited corpus revision corrected" (§12.2, §11.3).
    The label is neither rewritten nor trusted (§11.3) -- this transition
    changes only maturity_state and the flag fields; the guard trigger
    still refuses any attempt to also change outcome_json,
    input_revision_set_json, or matured_at in the same UPDATE.
    """
    if not flagged_at or not flagged_at.strip():
        raise LabelValueStoreError("flagged_at is required and must be non-empty (Ph6)")
    if not flag_reason or not flag_reason.strip():
        raise LabelValueStoreError("flag_reason is required and must be non-empty (Ph6)")
    _apply_forward_transition(
        db_path, spec_hash, subject_id, declared_at, "Matured", "Flagged",
        "flagged_at = ?, flag_reason = ?", (flagged_at, flag_reason),
    )


def adjudicate_forward_label(
    db_path: Path | str, spec_hash: str, subject_id: str, declared_at: str,
    adjudicated_at: str, adjudication_decision: str,
) -> None:
    """Flagged -> Adjudicated: "human decision recorded" (§12.2, LG1/IQROS-P8:
    adjudication is a human act). Permanent record -- no arc ever leaves
    Adjudicated."""
    if not adjudicated_at or not adjudicated_at.strip():
        raise LabelValueStoreError("adjudicated_at is required and must be non-empty (Ph6)")
    if not adjudication_decision or not adjudication_decision.strip():
        raise LabelValueStoreError("adjudication_decision is required and must be non-empty (Ph6)")
    _apply_forward_transition(
        db_path, spec_hash, subject_id, declared_at, "Flagged", "Adjudicated",
        "adjudicated_at = ?, adjudication_decision = ?", (adjudicated_at, adjudication_decision),
    )
