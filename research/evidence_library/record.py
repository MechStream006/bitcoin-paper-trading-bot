"""
Evidence Library — ledger.db operations.

Append-only SQLite store for evidence runs, gate results, and state transitions.
No rows are ever updated or deleted; all history is preserved.
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

CREATE VIEW IF NOT EXISTS latest_run_per_family AS
    SELECT r.*
    FROM runs r
    JOIN (
        SELECT family_id, MAX(run_ts) AS max_ts
        FROM runs GROUP BY family_id
    ) lr ON r.family_id = lr.family_id AND r.run_ts = lr.max_ts;

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

CREATE VIEW IF NOT EXISTS has_live_evidence AS
    SELECT DISTINCT family_id
    FROM runs
    WHERE provenance != 'offline-corpus';
"""

# "sealed" removed: semantics were undefined and it would bypass the Validated
# non-offline-corpus provenance check. Re-add only after semantics are documented.
_VALID_PROVENANCE = {"offline-corpus", "live-forward", "mainnet"}


def init_db(path: Path | str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(_SCHEMA_SQL)
    con.commit()
    con.close()


def append_run(
    db_path: Path | str,
    family_id: str,
    metrics: dict[str, Any],
    provenance: str,
    window_start: str | None = None,
    window_end: str | None = None,
    git_sha: str | None = None,
    fn_hash: str | None = None,
    engine_version: str | None = None,
    n_trials: int | None = None,
    governance: dict[str, Any] | None = None,
) -> str:
    if provenance not in _VALID_PROVENANCE:
        raise ValueError(f"Invalid provenance '{provenance}'. Must be one of {_VALID_PROVENANCE}")

    run_id = str(uuid.uuid4())
    ts = datetime.now(timezone.utc).isoformat()
    full_json = {**metrics, **(governance or {})}

    con = sqlite3.connect(db_path)
    con.execute(
        """
        INSERT INTO runs (
            run_id, family_id, run_ts, git_sha, fn_hash, provenance,
            window_start, window_end, engine_version, n_trials,
            sharpe, oos_sharpe, evidence_score, verdict, n_trades,
            dsr_prob, bh_p, metrics_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            run_id, family_id, ts, git_sha, fn_hash, provenance,
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


def append_transition(
    db_path: Path | str,
    family_id: str,
    from_state: str,
    to_state: str,
    reason: str | None = None,
    justifying_run_id: str | None = None,
    actor: str = "operator",
    ts: str | None = None,
) -> None:
    """
    Append a state transition to the append-only ledger.

    Validates the arc against lifecycle.ALLOWED_TRANSITIONS before writing.
    Raises ValueError for invalid arcs so no bad row ever enters the ledger.

    `ts` is ISO-8601 UTC; defaults to now. Pass an explicit value only for
    bootstrap/migration backfill — normal callers should omit it.
    """
    from lifecycle import can_transition, ALLOWED_TRANSITIONS as _AT
    if not can_transition(from_state, to_state):
        allowed = sorted(_AT.get(from_state, frozenset()))
        raise ValueError(
            f"Invalid lifecycle arc '{from_state}' → '{to_state}' for '{family_id}'. "
            f"Allowed from '{from_state}': {allowed}"
        )
    ts = ts or datetime.now(timezone.utc).isoformat()
    con = sqlite3.connect(db_path)
    con.execute(
        """
        INSERT INTO state_transitions
            (family_id, from_state, to_state, ts, reason, justifying_run_id, actor)
        VALUES (?,?,?,?,?,?,?)
        """,
        (family_id, from_state, to_state, ts, reason, justifying_run_id, actor),
    )
    con.commit()
    con.close()


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


def get_latest_run(db_path: Path | str, family_id: str) -> dict | None:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.execute(
        "SELECT * FROM runs WHERE family_id=? ORDER BY run_ts DESC LIMIT 1",
        (family_id,),
    )
    row = cur.fetchone()
    con.close()
    if row is None:
        return None
    d = dict(row)
    d["metrics"] = json.loads(d.pop("metrics_json"))
    return d


def get_all_latest_runs(db_path: Path | str) -> dict[str, dict]:
    """Returns {family_id: run_dict} for the most recent run per family."""
    if not Path(db_path).exists():
        return {}
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.execute("""
        SELECT r.*
        FROM runs r
        JOIN (SELECT family_id, MAX(run_ts) AS mx FROM runs GROUP BY family_id) l
          ON r.family_id = l.family_id AND r.run_ts = l.mx
    """)
    rows = {}
    for row in cur.fetchall():
        d = dict(row)
        d["metrics"] = json.loads(d.pop("metrics_json"))
        rows[d["family_id"]] = d
    con.close()
    return rows


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
            provenance="offline-corpus",
            window_start="2021-01-01", window_end="2026-05-31",
            engine_version="test", n_trials=34,
        )
        append_gate_result(tmp, run_id, "B_SHARPE", "edge", True, 0.42, 0.30)
        append_gate_result(tmp, run_id, "B_SENSITIVITY", "edge", False, 0.50, 0.60)
        append_transition(tmp, "test_signal", "discovery", "candidate",
                          reason="passed edge gate", justifying_run_id=run_id)

        run = get_run(tmp, run_id)
        assert run["sharpe"] == 0.42
        gates = get_gate_results(tmp, run_id)
        assert len(gates) == 2
        transitions = get_all_transitions(tmp)
        assert len(transitions) == 1

        print(f"record.py smoke test: OK (run_id={run_id[:8]}...)")
    finally:
        os.unlink(tmp)
