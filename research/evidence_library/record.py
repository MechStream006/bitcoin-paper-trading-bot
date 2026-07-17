"""
Evidence Library — ledger.db operations.

Append-only SQLite store for evidence runs, gate results, and state transitions.
No rows are ever updated or deleted; all history is preserved.

PR-5 (docs/governance/evidence_reporting_architecture.md) additions:
  - `runs.run_kind` — the second half of a run's evidence LANE
    (provenance, run_kind). Immutable, assigned once at append_run(), never
    recomputed or updated (Law 2). See LANES / _VALID_LANE_PAIRS below.
  - `current_run_per_lane` VIEW — the only sanctioned way to select a
    "current" run (Law 3: selection is within-lane, never across-lane).
  - `derive_state_at()` / `derive_all_states_at()` — a family's lifecycle
    state, derived purely from the append-only `state_transitions` ledger,
    optionally as of a past date. registry.yaml is a cache of this, not a
    second source of truth.
  - `get_all_latest_runs()` and `latest_run_per_family` are DELETED, not
    deprecated — see evidence_reporting_architecture.md §6.3. There is no
    replacement that selects "the latest run" across lanes; that operation
    is the defect this design exists to eliminate.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT PRIMARY KEY,
    family_id       TEXT NOT NULL,
    run_ts          TEXT NOT NULL,
    git_sha         TEXT,
    fn_hash         TEXT,
    provenance      TEXT NOT NULL,
    run_kind        TEXT,           -- lane axis part 2; NOT NULL enforced by append_run(), not the DB
    window_start    TEXT,
    window_end      TEXT,
    engine_version  TEXT,
    n_trials        INTEGER,
    sharpe          REAL,
    oos_sharpe      REAL,
    evidence_score  REAL,
    verdict         TEXT,
    n_trades        INTEGER,
    dsr_prob        REAL,
    bh_p            REAL,
    metrics_json    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gate_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL REFERENCES runs(run_id),
    gate_code       TEXT NOT NULL,
    role            TEXT,
    passed          INTEGER NOT NULL,
    observed        TEXT,
    threshold       TEXT
);

CREATE TABLE IF NOT EXISTS state_transitions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    family_id           TEXT NOT NULL,
    from_state          TEXT NOT NULL,
    to_state            TEXT NOT NULL,
    ts                  TEXT NOT NULL,
    reason              TEXT,
    justifying_run_id   TEXT REFERENCES runs(run_id),
    actor               TEXT DEFAULT 'operator'
);

-- Law 3: "current run for (family, lane) = latest run in that lane."
-- The ONLY sanctioned way to select a "current" run. Explicit run_id
-- tiebreak (evidence_reporting_architecture.md §1.4) removes the
-- non-determinism of the old MAX(run_ts) GROUP BY join.
CREATE VIEW IF NOT EXISTS current_run_per_lane AS
    SELECT * FROM (
        SELECT r.*,
               ROW_NUMBER() OVER (
                   PARTITION BY family_id, provenance, run_kind
                   ORDER BY run_ts DESC, run_id DESC
               ) AS rn
        FROM runs r
    ) WHERE rn = 1;

CREATE VIEW IF NOT EXISTS candidate_gate_status AS
    SELECT
        gr.run_id,
        r.family_id,
        r.run_ts,
        r.provenance,
        gr.gate_code,
        gr.role,
        gr.passed,
        gr.observed,
        gr.threshold
    FROM gate_results gr
    JOIN runs r ON gr.run_id = r.run_id;

-- Redefined (was: DISTINCT family_id WHERE provenance != 'offline-corpus',
-- which reports "has live evidence" for a bridge-written row with
-- n_trades=0 -- see evidence_reporting_architecture.md §1.4).
CREATE VIEW IF NOT EXISTS has_live_evidence AS
    SELECT DISTINCT family_id
    FROM runs
    WHERE provenance != 'offline-corpus' AND n_trades > 0;
"""

# "sealed" removed: semantics were undefined and it would bypass the Validated
# non-offline-corpus provenance check. Re-add only after semantics are documented.
_VALID_PROVENANCE = {"offline-corpus", "live-forward", "mainnet"}

# Axis A (evidence_reporting_architecture.md §3): a run's lane is the pair
# (provenance, run_kind). run_kind is NEW and orthogonal to provenance --
# provenance alone cannot separate L0 (discovery) from L1 (confirmatory),
# both offline-corpus.
_VALID_RUN_KIND = {"discovery", "confirmatory", "forward", "production"}

# The frozen lane table (§3). Adding a lane is adding one entry here plus
# one report column -- never a new selection code path (Law 3).
LANES: dict[str, tuple[str, str]] = {
    "L0": ("offline-corpus", "discovery"),
    "L1": ("offline-corpus", "confirmatory"),
    "L2": ("live-forward", "forward"),
    "L3": ("mainnet", "production"),
}
_VALID_LANE_PAIRS: frozenset[tuple[str, str]] = frozenset(LANES.values())
LANE_CODE_BY_RUN_KIND: dict[str, str] = {run_kind: code for code, (_, run_kind) in LANES.items()}

# Transitions dated before this are the pre-ledger bootstrap/migration
# history and are grandfathered past the justifying_run_id requirement --
# and ONLY by this (evidence_reporting_architecture.md §9's back-compat note).
_JUSTIFYING_RUN_ID_CUTOFF = "2026-07-01T00:00:00+00:00"


def init_db(path: Path | str) -> None:
    """
    Idempotent. Adds `run_kind` to a pre-existing table that predates this
    column (nullable at the schema level -- NOT NULL is enforced by
    append_run() for all new writes, per §6.1; existing rows stay NULL
    until migrate_run_kind.py classifies them, deliberately kept as a
    separate, explicit, audited step rather than folded into this function).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.executescript(_SCHEMA_SQL)
        columns = {row[1] for row in con.execute("PRAGMA table_info(runs)")}
        if "run_kind" not in columns:
            con.execute("ALTER TABLE runs ADD COLUMN run_kind TEXT")
        con.commit()
    finally:
        con.close()


def append_run(
    db_path: Path | str,
    family_id: str,
    metrics: dict[str, Any],
    provenance: str,
    run_kind: str,
    window_start: str | None = None,
    window_end: str | None = None,
    git_sha: str | None = None,
    fn_hash: str | None = None,
    engine_version: str | None = None,
    n_trials: int | None = None,
    governance: dict[str, Any] | None = None,
) -> str:
    """
    run_kind is required (Law 2: a run's lane is written once, at
    append_run(), never recomputed or updated). (provenance, run_kind) must
    be one of the frozen lane pairs in LANES -- an offline-corpus run
    claiming run_kind='forward' is a contradiction and raises, exactly as
    an invalid provenance does today.
    """
    if provenance not in _VALID_PROVENANCE:
        raise ValueError(f"Invalid provenance '{provenance}'. Must be one of {sorted(_VALID_PROVENANCE)}")
    if run_kind not in _VALID_RUN_KIND:
        raise ValueError(f"Invalid run_kind '{run_kind}'. Must be one of {sorted(_VALID_RUN_KIND)}")
    if (provenance, run_kind) not in _VALID_LANE_PAIRS:
        raise ValueError(
            f"Invalid lane (provenance='{provenance}', run_kind='{run_kind}'). "
            f"Legal lanes: {sorted(_VALID_LANE_PAIRS)}"
        )

    run_id = str(uuid.uuid4())
    ts = datetime.now(timezone.utc).isoformat()
    full_json = {**metrics, **(governance or {})}

    con = sqlite3.connect(db_path)
    con.execute(
        """
        INSERT INTO runs (
            run_id, family_id, run_ts, git_sha, fn_hash, provenance, run_kind,
            window_start, window_end, engine_version, n_trials,
            sharpe, oos_sharpe, evidence_score, verdict, n_trades,
            dsr_prob, bh_p, metrics_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            run_id, family_id, ts, git_sha, fn_hash, provenance, run_kind,
            window_start, window_end, engine_version, n_trials,
            _float(metrics.get("sharpe")),
            _float(metrics.get("oos_sharpe")),
            _float(metrics.get("evidence_score")),
            metrics.get("verdict"),
            _int(metrics.get("n_trades")),
            _float((governance or {}).get("dsr_prob")),
            _float((governance or {}).get("bh_p")),
            json.dumps(full_json),
        ),
    )
    con.commit()
    con.close()
    return run_id


def append_gate_result(
    db_path: Path | str,
    run_id: str,
    gate_code: str,
    role: str | None,
    passed: bool,
    observed: Any,
    threshold: Any,
) -> None:
    con = sqlite3.connect(db_path)
    con.execute(
        """
        INSERT INTO gate_results (run_id, gate_code, role, passed, observed, threshold)
        VALUES (?,?,?,?,?,?)
        """,
        (run_id, gate_code, role, int(passed), str(observed), str(threshold)),
    )
    con.commit()
    con.close()


def append_transition_on_connection(
    con: sqlite3.Connection,
    family_id: str,
    from_state: str,
    to_state: str,
    reason: str | None = None,
    justifying_run_id: str | None = None,
    actor: str = "operator",
    ts: str | None = None,
) -> int:
    """
    The core of append_transition(), operating on a connection the CALLER
    manages the transaction for (BEGIN/COMMIT/ROLLBACK are the caller's
    responsibility -- this function issues no transaction control of its own).
    Returns the new row's `id`.

    Exists so a caller writing to another table in the same transaction
    (PR-8's transition_governance.py, which must write a state_transitions row
    and its governance_receipts row atomically -- neither may ever exist
    without the other) can include the transition write without a second,
    unshareable connection.

    Same validation and behavior as append_transition() -- this IS that logic,
    not a reimplementation of it. Written exactly as PR-4 did for
    receipt_ledger.append_receipt_on_connection(), and for the same reason:
    duplicating the arc / re-pin / grandfathering rules into a second call
    site is precisely the divergence between enforcement points that
    evidence_reporting_architecture.md §7.3 warns against.
    """
    from lifecycle import can_transition, ALLOWED_TRANSITIONS as _AT, STATES_REQUIRING_RUN
    if not can_transition(from_state, to_state):
        allowed = sorted(_AT.get(from_state, frozenset()))
        raise ValueError(
            f"Invalid lifecycle arc '{from_state}' → '{to_state}' for '{family_id}'. "
            f"Allowed from '{from_state}': {allowed}"
        )

    if from_state == to_state and not justifying_run_id:
        raise ValueError(
            f"Self-transition '{from_state}' -> '{to_state}' for '{family_id}' is a re-pin "
            f"and requires a non-NULL justifying_run_id."
        )

    is_grandfathered = bool(ts) and ts < _JUSTIFYING_RUN_ID_CUTOFF
    if to_state in STATES_REQUIRING_RUN and not justifying_run_id and not is_grandfathered:
        raise ValueError(
            f"Transition into '{to_state}' for '{family_id}' requires a non-NULL "
            f"justifying_run_id (grandfathered only for ts < {_JUSTIFYING_RUN_ID_CUTOFF})."
        )

    ts = ts or datetime.now(timezone.utc).isoformat()
    cur = con.execute(
        """
        INSERT INTO state_transitions
            (family_id, from_state, to_state, ts, reason, justifying_run_id, actor)
        VALUES (?,?,?,?,?,?,?)
        """,
        (family_id, from_state, to_state, ts, reason, justifying_run_id, actor),
    )
    return cur.lastrowid


def append_transition(
    db_path: Path | str,
    family_id: str,
    from_state: str,
    to_state: str,
    reason: str | None = None,
    justifying_run_id: str | None = None,
    actor: str = "operator",
    ts: str | None = None,
) -> int:
    """
    Append a state transition to the append-only ledger.

    Validates the arc against lifecycle.ALLOWED_TRANSITIONS before writing.
    Raises ValueError for invalid arcs so no bad row ever enters the ledger.

    `ts` is ISO-8601 UTC; defaults to now. Pass an explicit value only for
    bootstrap/migration backfill — normal callers should omit it.

    PR-5 additions:
      - A self-transition (from_state == to_state) is a RE-PIN: the family's
        state is unchanged, but justifying_run_id points at new evidence.
        It is legal (can_transition() permits X -> X structurally) but
        REQUIRES a non-NULL justifying_run_id and a reason -- this is the
        enforcement point for that requirement.
      - Any transition INTO a run-requiring state (STATES_REQUIRING_RUN)
        requires a non-NULL justifying_run_id, UNLESS `ts` is explicitly
        supplied and predates _JUSTIFYING_RUN_ID_CUTOFF (the grandfathered
        pre-ledger bootstrap history, and only that history).

    PR-8: now a thin wrapper around append_transition_on_connection() (opens
    its own connection and transaction, since most callers do not need to
    share one). Behavior is unchanged; it additionally RETURNS the new row's
    id, which every pre-PR-8 caller ignores.

    This remains the RAW primitive: it emits no governance receipt. The
    governed front door is transition_governance.transition_with_receipt().
    A transition written through this function is reported as an ungoverned
    transition (TG1) by transition_governance.validate_transition_governance()
    -- the same relationship contract_lifecycle.activate_contract() (raw) has
    to contract_receipts.activate_contract_with_receipt() (governed).
    """
    con = sqlite3.connect(db_path)
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            transition_id = append_transition_on_connection(
                con, family_id, from_state, to_state,
                reason=reason, justifying_run_id=justifying_run_id, actor=actor, ts=ts,
            )
        except Exception:
            con.rollback()
            raise
        else:
            con.commit()
    finally:
        con.close()
    return transition_id


def get_run(db_path: Path | str, run_id: str) -> dict | None:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.execute("SELECT * FROM runs WHERE run_id=?", (run_id,))
    row = cur.fetchone()
    con.close()
    if row is None:
        return None
    d = dict(row)
    d["metrics"] = json.loads(d.pop("metrics_json"))
    return d


def get_latest_run(db_path: Path | str, family_id: str, run_kind: str) -> dict | None:
    """
    The latest run for (family_id, run_kind) -- i.e. within one lane.
    `run_kind` is REQUIRED (evidence_reporting_architecture.md §6.3: this
    function "may remain only if it gains a required lane argument"). There
    is no lane-less "latest run" left anywhere in this module.
    """
    if run_kind not in _VALID_RUN_KIND:
        raise ValueError(f"Invalid run_kind '{run_kind}'. Must be one of {sorted(_VALID_RUN_KIND)}")
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.execute(
        "SELECT * FROM runs WHERE family_id=? AND run_kind=? ORDER BY run_ts DESC, run_id DESC LIMIT 1",
        (family_id, run_kind),
    )
    row = cur.fetchone()
    con.close()
    if row is None:
        return None
    d = dict(row)
    d["metrics"] = json.loads(d.pop("metrics_json"))
    return d


def get_current_runs_by_lane(db_path: Path | str, as_of: str | None = None) -> dict[str, dict[str, dict]]:
    """
    Returns {family_id: {run_kind: run_dict}} -- the current run in every
    lane, for every family. This is the ONLY function that should be used
    to populate the Evidence Matrix's "Evidence by lane" table.

    `as_of`, if given, must already be a fully-qualified ISO-8601 datetime
    string (callers filtering by a bare calendar date must normalise to
    end-of-day first -- see report.py's _normalize_as_of(); this function
    does no normalisation itself, so a bare "YYYY-MM-DD" would silently
    exclude same-day events, which is why that responsibility is pushed to
    the one CLI-facing call site rather than duplicated here).
    """
    if not Path(db_path).exists():
        return {}
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        if as_of is not None:
            cur = con.execute(
                """
                SELECT * FROM (
                    SELECT r.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY family_id, provenance, run_kind
                               ORDER BY run_ts DESC, run_id DESC
                           ) AS rn
                    FROM runs r
                    WHERE run_ts <= ?
                ) WHERE rn = 1
                """,
                (as_of,),
            )
        else:
            cur = con.execute("SELECT * FROM current_run_per_lane")

        result: dict[str, dict[str, dict]] = {}
        for row in cur.fetchall():
            d = dict(row)
            d.pop("rn", None)
            d["metrics"] = json.loads(d.pop("metrics_json"))
            result.setdefault(d["family_id"], {})[d["run_kind"]] = d
        return result
    finally:
        con.close()


def get_current_run(db_path: Path | str, family_id: str, run_kind: str, as_of: str | None = None) -> dict | None:
    """Current run for exactly one (family_id, run_kind). See get_current_runs_by_lane()."""
    return get_current_runs_by_lane(db_path, as_of=as_of).get(family_id, {}).get(run_kind)


def derive_state_at(db_path: Path | str, family_id: str, as_of: str | None = None) -> tuple[str | None, str | None, str | None]:
    """
    Derive a family's state PURELY from state_transitions -- never from
    registry.yaml. Returns (state, pinned_run_id, ts) for the last
    transition at or before `as_of` (None = now / no filter), or
    (None, None, None) if the family has no transitions at or before that
    point (i.e. it is still 'discovery' and was never written to the
    ledger, or as_of predates its first transition).

    `as_of`, like get_current_runs_by_lane(), must already be a fully
    end-of-day-normalised ISO datetime if it originated from a bare date.
    """
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        if as_of is not None:
            cur = con.execute(
                "SELECT to_state, justifying_run_id, ts FROM state_transitions "
                "WHERE family_id = ? AND ts <= ? ORDER BY ts DESC, id DESC LIMIT 1",
                (family_id, as_of),
            )
        else:
            cur = con.execute(
                "SELECT to_state, justifying_run_id, ts FROM state_transitions "
                "WHERE family_id = ? ORDER BY ts DESC, id DESC LIMIT 1",
                (family_id,),
            )
        row = cur.fetchone()
    finally:
        con.close()
    if row is None:
        return None, None, None
    return row["to_state"], row["justifying_run_id"], row["ts"]


def derive_all_states_at(db_path: Path | str, as_of: str | None = None) -> dict[str, tuple[str, str | None, str]]:
    """Returns {family_id: (state, pinned_run_id, ts)} for every family with
    at least one transition at or before `as_of`. See derive_state_at()."""
    if not Path(db_path).exists():
        return {}
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        if as_of is not None:
            cur = con.execute(
                "SELECT family_id, to_state, justifying_run_id, ts FROM state_transitions "
                "WHERE ts <= ? ORDER BY family_id, ts, id",
                (as_of,),
            )
        else:
            cur = con.execute(
                "SELECT family_id, to_state, justifying_run_id, ts FROM state_transitions "
                "ORDER BY family_id, ts, id"
            )
        result: dict[str, tuple[str, str | None, str]] = {}
        for row in cur.fetchall():
            result[row["family_id"]] = (row["to_state"], row["justifying_run_id"], row["ts"])
        return result
    finally:
        con.close()


def get_gate_results(db_path: Path | str, run_id: str) -> list[dict]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.execute("SELECT * FROM gate_results WHERE run_id=?", (run_id,))
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows


def get_all_transitions(db_path: Path | str, family_id: str | None = None) -> list[dict]:
    if not Path(db_path).exists():
        return []
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    if family_id:
        cur = con.execute(
            "SELECT * FROM state_transitions WHERE family_id=? ORDER BY ts",
            (family_id,),
        )
    else:
        cur = con.execute("SELECT * FROM state_transitions ORDER BY family_id, ts")
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows


def get_all_runs(db_path: Path | str, family_id: str | None = None) -> list[dict]:
    """Every run, every lane, in chronological order. Used by the evidence
    timeline (audit substrate) -- never for "current value" selection."""
    if not Path(db_path).exists():
        return []
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    if family_id:
        cur = con.execute("SELECT * FROM runs WHERE family_id=? ORDER BY run_ts, run_id", (family_id,))
    else:
        cur = con.execute("SELECT * FROM runs ORDER BY family_id, run_ts, run_id")
    rows = []
    for r in cur.fetchall():
        d = dict(r)
        d["metrics"] = json.loads(d.pop("metrics_json"))
        rows.append(d)
    con.close()
    return rows


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile, os

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        tmp = f.name

    try:
        init_db(tmp)
        init_db(tmp)  # idempotent

        run_id = append_run(
            tmp, "test_signal",
            metrics={"sharpe": 0.42, "oos_sharpe": 0.31, "evidence_score": 55.0,
                     "verdict": "Moderate", "n_trades": 45},
            provenance="offline-corpus", run_kind="discovery",
            window_start="2021-01-01", window_end="2026-05-31",
            engine_version="test", n_trials=34,
        )
        append_gate_result(tmp, run_id, "B_SHARPE", "edge", True, 0.42, 0.30)
        append_gate_result(tmp, run_id, "B_SENSITIVITY", "edge", False, 0.50, 0.60)
        append_transition(tmp, "test_signal", "discovery", "candidate",
                          reason="passed edge gate", justifying_run_id=run_id)

        run = get_run(tmp, run_id)
        assert run["sharpe"] == 0.42
        assert run["run_kind"] == "discovery"
        gates = get_gate_results(tmp, run_id)
        assert len(gates) == 2
        transitions = get_all_transitions(tmp)
        assert len(transitions) == 1

        current = get_current_run(tmp, "test_signal", "discovery")
        assert current["run_id"] == run_id

        state, pinned, ts = derive_state_at(tmp, "test_signal")
        assert state == "candidate"
        assert pinned == run_id

        print(f"record.py smoke test: OK (run_id={run_id[:8]}...)")
    finally:
        os.unlink(tmp)
