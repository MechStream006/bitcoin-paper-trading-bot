"""
decision_store.py
=================
Persistence for decision-time feature rows produced by
`research/alignment/features.py`.

A row is keyed by decision identity and a feature-contract version, and carries
every input's state, both timestamps, age, tolerance, criticality and
provenance -- so a persisted row can be audited, and re-checked for look-ahead,
without recomputing it or consulting the sources again.

WHY A SIBLING STORE AND NOT `feature_store.db`
----------------------------------------------
`feature_store.db` is governed: 17 Active Feature Specifications, 17
`feature.register` receipts, its own backup path, and a row-count fingerprint
asserted by `tests/test_backup.py`. Its grain is also different -- values there
are keyed `(spec_hash, corpus_version, as_of)`, a corpus-wide series for one
specification, whereas these rows are one decision instant across several
features. Writing a new grain into a governed store would be a governance act
disguised as a schema change, so this is a separate file in the same directory,
following the same conventions.

`registry.register_definition()` is likewise not used: it always mints the NEXT
ordinal version and requires a rationale, an intended role and an actor. That
is an operator decision, correctly, and must not fire once per persisted row.
Contract versioning here is local and derived (see below).

VERSIONING IS DERIVED, AND A CHANGED DEFINITION CANNOT OVERWRITE HISTORY
-----------------------------------------------------------------------
Every `FeatureSpec` hashes to a `contract_hash` over the things that change
what the number means: the feature name, each input's source, field,
criticality, tolerance and panel key, and the source text of the compute
function. `register_contract` returns the existing version for a known hash and
mints the next version for a new one, so re-registering an unchanged contract
is a no-op and changing a definition necessarily lands on a new version.

Writing a row whose `(decision_id, feature, version)` already exists is allowed
only when the payload is identical; a differing payload raises
`DivergenceError` rather than updating. There is no UPDATE path in this module,
and a DB trigger blocks one from outside it. Historical values therefore cannot
be silently restated -- the failure is loud and the fix is a version bump.

ABSENCE IS PERSISTED
--------------------
An unavailable, stale or incomplete feature is written like any other, with its
state, its action, the age that made it stale and the detail that explains it.
Only the value is NULL. Dropping such rows would make a stored corpus look
better than the data it came from, and would destroy exactly the evidence that
tells you why a decision had nothing to work with.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import pathlib
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping, Optional, Sequence

_HERE = pathlib.Path(__file__).resolve().parent
_RESEARCH_ROOT = _HERE.parent
if str(_RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_ROOT))

from alignment.align import check_no_lookahead  # noqa: E402
from alignment.features import (  # noqa: E402
    FeatureRow, FeatureSpec, FeatureValue, InputRecord, policy, states,
)
from alignment.timebase import to_utc  # noqa: E402

DEFAULT_DB = _HERE / "decision_features.db"

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS feature_contracts (
    feature_name     TEXT    NOT NULL,
    contract_version INTEGER NOT NULL,
    contract_hash    TEXT    NOT NULL,
    contract_json    TEXT    NOT NULL,
    registered_at    TEXT    NOT NULL,
    PRIMARY KEY (feature_name, contract_version),
    UNIQUE (feature_name, contract_hash)
);

CREATE TABLE IF NOT EXISTS feature_rows (
    decision_id      TEXT    NOT NULL,
    decision_ts      TEXT    NOT NULL,
    feature_name     TEXT    NOT NULL,
    contract_version INTEGER NOT NULL,
    contract_hash    TEXT    NOT NULL,
    value_json       TEXT,
    state            TEXT    NOT NULL,
    action           TEXT    NOT NULL,
    row_state        TEXT    NOT NULL,
    row_action       TEXT    NOT NULL,
    schema_version   INTEGER NOT NULL,
    written_at       TEXT    NOT NULL,
    PRIMARY KEY (decision_id, feature_name, contract_version),
    FOREIGN KEY (feature_name, contract_version)
        REFERENCES feature_contracts (feature_name, contract_version)
);

CREATE TABLE IF NOT EXISTS feature_row_inputs (
    decision_id      TEXT    NOT NULL,
    feature_name     TEXT    NOT NULL,
    contract_version INTEGER NOT NULL,
    input_name       TEXT    NOT NULL,
    source           TEXT    NOT NULL,
    state            TEXT    NOT NULL,
    action           TEXT    NOT NULL,
    value_json       TEXT,
    event_ts         TEXT,
    available_ts     TEXT,
    age_s            REAL,
    tolerance_s      REAL,
    criticality      TEXT    NOT NULL,
    provenance_json  TEXT    NOT NULL,
    detail           TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (decision_id, feature_name, contract_version, input_name),
    FOREIGN KEY (decision_id, feature_name, contract_version)
        REFERENCES feature_rows (decision_id, feature_name, contract_version)
);

CREATE INDEX IF NOT EXISTS ix_feature_rows_decision_ts
    ON feature_rows (decision_ts);
CREATE INDEX IF NOT EXISTS ix_feature_rows_feature
    ON feature_rows (feature_name, contract_version);

CREATE TRIGGER IF NOT EXISTS trg_feature_rows_no_update
BEFORE UPDATE ON feature_rows
BEGIN
    SELECT RAISE(ABORT,
        'feature_rows is append-only: bump the contract version instead');
END;

CREATE TRIGGER IF NOT EXISTS trg_feature_row_inputs_no_update
BEFORE UPDATE ON feature_row_inputs
BEGIN
    SELECT RAISE(ABORT, 'feature_row_inputs is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_feature_contracts_no_update
BEFORE UPDATE ON feature_contracts
BEGIN
    SELECT RAISE(ABORT,
        'a registered contract is immutable; register a new version');
END;
"""


class DecisionStoreError(ValueError):
    """Base error for this store."""


class DivergenceError(DecisionStoreError):
    """A row already exists for this key with a different payload."""


class ContractChanged(DecisionStoreError):
    """A feature definition changed under an already-used version."""


def init_db(path: pathlib.Path | str = DEFAULT_DB) -> None:
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.executescript(_SCHEMA)
        con.commit()
    finally:
        con.close()


def _connect(path, *, read_only: bool = False) -> sqlite3.Connection:
    p = pathlib.Path(path).resolve()
    if read_only:
        if not p.exists():
            raise DecisionStoreError(f"database not found: {p}")
        con = sqlite3.connect(f"{p.as_uri()}?mode=ro", uri=True, timeout=10.0)
    else:
        con = sqlite3.connect(p, timeout=10.0)
        con.execute("PRAGMA foreign_keys = ON")
    con.row_factory = sqlite3.Row
    return con


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _content_hash(obj: Any) -> str:
    return hashlib.sha256(_canonical_json(obj).encode("utf-8")).hexdigest()


def contract_of(spec: FeatureSpec) -> dict[str, Any]:
    """The parts of a FeatureSpec that change what its number means.

    The compute function's source text is included: two specs with identical
    inputs but different arithmetic are different features, and a hash that
    ignored the body would let one silently replace the other.
    """
    try:
        body = inspect.getsource(spec.compute)
    except (OSError, TypeError):
        body = repr(spec.compute)
    return {
        "feature_name": spec.name,
        "compute_source": " ".join(body.split()),
        "inputs": [
            {
                "name": i.name, "source": i.source, "field": i.field,
                "criticality": i.criticality, "tolerance_s": i.tolerance_s,
                "key_field": i.key_field, "key": i.key,
                "degradation_declared": i.degradation_declared,
            }
            for i in spec.inputs
        ],
    }


def contract_hash(spec: FeatureSpec) -> str:
    return _content_hash(contract_of(spec))


def register_contract(db_path, spec: FeatureSpec, registered_at: str) -> int:
    """Version for this contract: the existing one, or the next ordinal.

    Idempotent by hash. An unchanged spec re-registers to the same version; a
    changed one necessarily lands on a new version rather than redefining an
    old one.
    """
    h = contract_hash(spec)
    con = _connect(db_path)
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT contract_version FROM feature_contracts "
            "WHERE feature_name = ? AND contract_hash = ?", (spec.name, h)
        ).fetchone()
        if row is not None:
            con.commit()
            return int(row["contract_version"])
        nxt = (con.execute(
            "SELECT MAX(contract_version) FROM feature_contracts WHERE feature_name = ?",
            (spec.name,)).fetchone()[0] or 0) + 1
        con.execute(
            "INSERT INTO feature_contracts "
            "(feature_name, contract_version, contract_hash, contract_json, registered_at) "
            "VALUES (?,?,?,?,?)",
            (spec.name, nxt, h, _canonical_json(contract_of(spec)), registered_at))
        con.commit()
        return nxt
    finally:
        con.close()


def get_contract(db_path, feature_name: str, version: int) -> Optional[dict]:
    con = _connect(db_path, read_only=True)
    try:
        row = con.execute(
            "SELECT * FROM feature_contracts WHERE feature_name = ? AND contract_version = ?",
            (feature_name, version)).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def decision_identity(decision_ts: datetime, decision_id: str | None = None) -> str:
    """Caller-supplied identity, or a deterministic one from the instant."""
    if decision_id:
        return decision_id
    return decision_ts.isoformat()


@dataclass(frozen=True)
class PersistResult:
    decision_id: str
    written: int
    unchanged: int
    features: dict[str, int]


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return None if dt is None else dt.isoformat()


def _row_payload(row: FeatureRow, fv: FeatureValue, version: int,
                 chash: str, decision_id: str) -> dict[str, Any]:
    return {
        "decision_id": decision_id,
        "decision_ts": row.decision_ts.isoformat(),
        "feature_name": fv.name,
        "contract_version": version,
        "contract_hash": chash,
        "value_json": _canonical_json(fv.value) if fv.value is not None else None,
        "state": fv.state,
        "action": fv.action,
        "row_state": row.state,
        "row_action": row.action,
        "schema_version": SCHEMA_VERSION,
    }


def _input_payload(rec: InputRecord, fv: FeatureValue, version: int,
                   decision_id: str) -> dict[str, Any]:
    return {
        "decision_id": decision_id,
        "feature_name": fv.name,
        "contract_version": version,
        "input_name": rec.name,
        "source": rec.source,
        "state": rec.state,
        "action": rec.action,
        "value_json": _canonical_json(rec.value) if rec.value is not None else None,
        "event_ts": _iso(rec.event_ts),
        "available_ts": _iso(rec.available_ts),
        "age_s": rec.age_s,
        "tolerance_s": rec.tolerance_s,
        "criticality": rec.criticality,
        "provenance_json": _canonical_json(rec.provenance),
        "detail": rec.detail or "",
    }


def persist_row(db_path, row: FeatureRow, specs: Sequence[FeatureSpec],
                written_at: str, decision_id: str | None = None) -> PersistResult:
    """Persist every feature of one decision row, including unavailable ones.

    Idempotent: re-persisting an identical row writes nothing and raises
    nothing. A differing payload under the same key raises DivergenceError --
    `written_at` is excluded from that comparison, since re-running later is
    not a change to the evidence.
    """
    by_name = {s.name: s for s in specs}
    missing = sorted(set(row.features) - set(by_name))
    if missing:
        raise DecisionStoreError(f"no FeatureSpec supplied for {missing}")

    did = decision_identity(row.decision_ts, decision_id)
    versions = {n: register_contract(db_path, by_name[n], written_at)
                for n in sorted(row.features)}

    written = unchanged = 0
    con = _connect(db_path)
    try:
        con.execute("BEGIN IMMEDIATE")
        for name in sorted(row.features):
            fv = row.features[name]
            spec = by_name[name]
            version = versions[name]
            chash = contract_hash(spec)
            payload = _row_payload(row, fv, version, chash, did)
            inputs = [_input_payload(r, fv, version, did) for r in fv.inputs]

            existing = con.execute(
                "SELECT * FROM feature_rows WHERE decision_id=? AND feature_name=? "
                "AND contract_version=?", (did, name, version)).fetchone()
            if existing is not None:
                stored = {k: existing[k] for k in payload}
                if stored != payload:
                    diff = sorted(k for k in payload if stored[k] != payload[k])
                    raise DivergenceError(
                        f"{did}/{name} v{version} already stored with different "
                        f"values in {diff}; bump the contract version rather "
                        f"than restating history")
                stored_inputs = con.execute(
                    "SELECT * FROM feature_row_inputs WHERE decision_id=? AND "
                    "feature_name=? AND contract_version=? ORDER BY input_name",
                    (did, name, version)).fetchall()
                have = [{k: r[k] for k in inputs[0]} for r in stored_inputs] if inputs else []
                if have != sorted(inputs, key=lambda d: d["input_name"]):
                    raise DivergenceError(
                        f"{did}/{name} v{version} already stored with different inputs")
                unchanged += 1
                continue

            cols = list(payload) + ["written_at"]
            con.execute(
                f"INSERT INTO feature_rows ({','.join(cols)}) "
                f"VALUES ({','.join('?' * len(cols))})",
                [payload[c] for c in payload] + [written_at])
            for ip in inputs:
                con.execute(
                    f"INSERT INTO feature_row_inputs ({','.join(ip)}) "
                    f"VALUES ({','.join('?' * len(ip))})", list(ip.values()))
            written += 1
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return PersistResult(decision_id=did, written=written, unchanged=unchanged,
                         features=versions)


def persist_rows(db_path, rows: Sequence[FeatureRow], specs: Sequence[FeatureSpec],
                 written_at: str,
                 decision_ids: Mapping[str, str] | None = None) -> list[PersistResult]:
    out = []
    for r in rows:
        did = (decision_ids or {}).get(r.decision_ts.isoformat())
        out.append(persist_row(db_path, r, specs, written_at, decision_id=did))
    return out


def load_rows(db_path, decision_id: str | None = None,
              feature_name: str | None = None) -> list[dict[str, Any]]:
    """Persisted rows with their inputs attached, oldest decision first."""
    con = _connect(db_path, read_only=True)
    try:
        sql = "SELECT * FROM feature_rows"
        where, params = [], []
        if decision_id:
            where.append("decision_id = ?")
            params.append(decision_id)
        if feature_name:
            where.append("feature_name = ?")
            params.append(feature_name)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY decision_ts, feature_name, contract_version"
        out = []
        for r in con.execute(sql, params).fetchall():
            rec = dict(r)
            rec["value"] = (json.loads(r["value_json"])
                            if r["value_json"] is not None else None)
            rec["inputs"] = [
                {**dict(i),
                 "value": json.loads(i["value_json"]) if i["value_json"] is not None else None,
                 "provenance": json.loads(i["provenance_json"])}
                for i in con.execute(
                    "SELECT * FROM feature_row_inputs WHERE decision_id=? AND "
                    "feature_name=? AND contract_version=? ORDER BY input_name",
                    (r["decision_id"], r["feature_name"], r["contract_version"])).fetchall()
            ]
            out.append(rec)
        return out
    finally:
        con.close()


def revalidate_no_lookahead(db_path, decision_id: str | None = None
                            ) -> list[dict[str, Any]]:
    """Re-check persisted rows with the SAME checker the builder used.

    Reads only what was stored, reconstructs the shape `align.check_no_lookahead`
    expects, and delegates. Nothing is recomputed from the sources, so this
    answers the question a stored corpus actually raises: does what we wrote
    down still satisfy the rule, months later, without the sources present?
    """
    joined = []
    for rec in load_rows(db_path, decision_id=decision_id):
        dts = to_utc(rec["decision_ts"], "iso")
        for i in rec["inputs"]:
            joined.append({
                "decision_ts": dts,
                "matched": i["available_ts"] is not None,
                "available_ts": to_utc(i["available_ts"], "iso"),
                "age_s": i["age_s"],
                "feature": rec["feature_name"],
                "input": i["input_name"],
                "decision_id": rec["decision_id"],
            })
    return check_no_lookahead(joined)


def coverage(db_path) -> dict[str, Any]:
    con = _connect(db_path, read_only=True)
    try:
        n_dec = con.execute(
            "SELECT COUNT(DISTINCT decision_id) FROM feature_rows").fetchone()[0]
        n_rows = con.execute("SELECT COUNT(*) FROM feature_rows").fetchone()[0]
        n_inputs = con.execute("SELECT COUNT(*) FROM feature_row_inputs").fetchone()[0]
        per: dict[str, Any] = {}
        for r in con.execute(
                "SELECT feature_name, contract_version, state, COUNT(*) n "
                "FROM feature_rows GROUP BY feature_name, contract_version, state "
                "ORDER BY feature_name, contract_version, state"):
            key = f"{r['feature_name']}@v{r['contract_version']}"
            per.setdefault(key, {})[r["state"]] = r["n"]
        contracts = [dict(c) for c in con.execute(
            "SELECT feature_name, contract_version, contract_hash FROM feature_contracts "
            "ORDER BY feature_name, contract_version")]
        return {"decisions": n_dec, "feature_rows": n_rows,
                "input_records": n_inputs, "by_feature": per,
                "contracts": contracts, "schema_version": SCHEMA_VERSION}
    finally:
        con.close()
