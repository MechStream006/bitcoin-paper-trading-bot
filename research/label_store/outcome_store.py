"""
outcome_store.py
================
Deterministic execution-outcome labels, keyed by decision identity.

For each `decision_id` persisted by `feature_store/decision_store.py`, answers:
did a trade occur, in which direction, at what entry and exit, for what realised
PnL, fees, risk and excursion, by which exit route, over what holding period --
and, when the answer is not yet knowable, says so explicitly instead of
pretending.

THE CENTRAL ASYMMETRY IS THE WHOLE POINT
----------------------------------------
ADR-I011 §1.1, already stated in this repository's own
`label_store/register_conventions.py`: *a label depends on data after t and a
feature does not*. Everything here is downstream of the decision instant, and
nothing here may ever flow back into feature construction. Two structural
guards enforce that rather than convention: every label asserts
`entry_ts >= decision_ts` (a violation raises `LabelLeakageError`), and the
alignment package must never import this module -- asserted by test, because a
single import is all it would take to make features clairvoyant.

WHY A SIBLING STORE
-------------------
`label_store.db` is governed: registered Definitions and Specifications,
adjudication receipts, an activation path and a row-count fingerprint in
`tests/test_backup.py`. Its Specifications C1/C2/C3 are also a different kind of
label -- a *forward return over a fixed calendar horizon*, filled at daily
closes with a flat 15 bps cost, for signal research. These labels are
event-driven outcomes of orders that actually executed, filled at exchange
prices with the fees really paid. Same asymmetry, different convention, so per
§11.1's own reconvention rule they belong to a different Specification, not a
redefinition of an existing one.

The contract below therefore mirrors `register_conventions.py`'s field
vocabulary exactly -- fill, cost, direction, maturity, validity -- so the two
can be compared field by field when someone eventually wants to register this
one formally. Registering it is an operator act and is not done here.

MATURITY IS NOT COMPLETENESS
----------------------------
`OPEN` is not a missing value and must never be averaged with `COMPLETED`. A
position still running has no realised outcome; its PnL is not zero, it is
unknown. `UNMATCHED` is different again -- the decision has no execution
linkage at all, which is a fact about the record rather than about the market.
`NO_TRADE` means the decision produced no fill: the order was rejected or never
filled. All four are written; none is dropped.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sqlite3
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Iterable, Mapping, Optional, Sequence

_HERE = pathlib.Path(__file__).resolve().parent
_RESEARCH_ROOT = _HERE.parent
if str(_RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_ROOT))

from alignment.timebase import to_utc  # noqa: E402
from execution_evidence.completed_trades import (  # noqa: E402
    CompletedTrade, build_dataset, connect_ro, has_table,
)

DEFAULT_DB = _HERE / "decision_outcomes.db"
SCHEMA_VERSION = 1

COMPLETED = "COMPLETED"
OPEN = "OPEN"
NO_TRADE = "NO_TRADE"
UNMATCHED = "UNMATCHED"
STATUSES = (COMPLETED, OPEN, NO_TRADE, UNMATCHED)

RESOLVED_STATUSES = (COMPLETED,)

# Mirrors register_conventions.py's field vocabulary so the two conventions are
# comparable field by field. Any change here changes LABEL_CONTRACT_HASH and so
# forces a new label version -- history is never restated in place.
LABEL_CONTRACT: dict[str, Any] = {
    "label": "X1",
    "definition_name": "execution_outcome",
    "horizon_governance": "event_driven",
    "horizon_days": None,
    "fill_convention": (
        "Exchange fill prices on both legs, taken from order_fills via the "
        "position cycle: entry_price at the entry fill, exit_price at the fill "
        "that flattened the position. Not daily closes."
    ),
    "cost_convention": (
        "Fees actually charged, both legs, from order_fills.fee. Not a flat "
        "bps assumption."
    ),
    "direction_convention": (
        "Signed from the position side: +1 long, -1 short. Both are permitted; "
        "unlike C1 this pipeline is not long-only."
    ),
    "maturity_rule": (
        "Matures when the position is flat and an exit fill is recorded. An "
        "open position is OPEN, never a completed outcome with a zero result."
    ),
    "validity_condition": (
        "COMPLETED requires an entry fill and an exit fill for the decision's "
        "cycle, and entry_ts >= decision_ts."
    ),
    "conditioning_propagation": "preserving",
    "binding_id": "execution_evidence.completed_trades.build_dataset",
    "linkage": (
        "decision_id == orders.client_order_id, joined to the fill ledger by "
        "delta_order_id. positions.strategy is NOT used: it reads 'manual' for "
        "orders whose orders.strategy was the automated strategy, so it is not "
        "a reliable attribution key."
    ),
}


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _content_hash(obj: Any) -> str:
    return hashlib.sha256(_canonical_json(obj).encode("utf-8")).hexdigest()


LABEL_CONTRACT_HASH = _content_hash(LABEL_CONTRACT)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS label_contracts (
    label_name    TEXT    NOT NULL,
    label_version INTEGER NOT NULL,
    contract_hash TEXT    NOT NULL,
    contract_json TEXT    NOT NULL,
    registered_at TEXT    NOT NULL,
    PRIMARY KEY (label_name, label_version),
    UNIQUE (label_name, contract_hash)
);

CREATE TABLE IF NOT EXISTS decision_outcomes (
    decision_id       TEXT    NOT NULL,
    label_name        TEXT    NOT NULL,
    label_version     INTEGER NOT NULL,
    contract_hash     TEXT    NOT NULL,
    decision_ts       TEXT    NOT NULL,
    status            TEXT    NOT NULL,
    trade_occurred    INTEGER NOT NULL,
    direction         INTEGER,
    quantity          REAL,
    entry_ts          TEXT,
    entry_price       REAL,
    exit_ts           TEXT,
    exit_price        REAL,
    realised_pnl_usd  REAL,
    gross_return      REAL,
    fees_usd          REAL,
    realised_risk_usd REAL,
    configured_risk_usd REAL,
    mae_pts           REAL,
    mfe_pts           REAL,
    mae_pct           REAL,
    mfe_pct           REAL,
    exit_reason       TEXT,
    stop_hit          INTEGER,
    holding_seconds   REAL,
    trade_id          INTEGER,
    provenance_json   TEXT    NOT NULL,
    detail            TEXT    NOT NULL DEFAULT '',
    schema_version    INTEGER NOT NULL,
    written_at        TEXT    NOT NULL,
    PRIMARY KEY (decision_id, label_name, label_version),
    FOREIGN KEY (label_name, label_version)
        REFERENCES label_contracts (label_name, label_version)
);

CREATE INDEX IF NOT EXISTS ix_decision_outcomes_status
    ON decision_outcomes (status);

CREATE TRIGGER IF NOT EXISTS trg_decision_outcomes_no_update
BEFORE UPDATE ON decision_outcomes
BEGIN
    SELECT RAISE(ABORT,
        'decision_outcomes is append-only: bump the label version instead');
END;

CREATE TRIGGER IF NOT EXISTS trg_label_contracts_no_update
BEFORE UPDATE ON label_contracts
BEGIN
    SELECT RAISE(ABORT, 'a registered label contract is immutable');
END;
"""


class OutcomeStoreError(ValueError):
    """Base error for this store."""


class LabelLeakageError(OutcomeStoreError):
    """An outcome referenced execution that predates its own decision."""


class DivergenceError(OutcomeStoreError):
    """A label already exists for this key with a different payload."""


@dataclass(frozen=True)
class OutcomeLabel:
    decision_id: str
    decision_ts: str
    status: str
    trade_occurred: bool
    direction: Optional[int] = None
    quantity: Optional[float] = None
    entry_ts: Optional[str] = None
    entry_price: Optional[float] = None
    exit_ts: Optional[str] = None
    exit_price: Optional[float] = None
    realised_pnl_usd: Optional[float] = None
    gross_return: Optional[float] = None
    fees_usd: Optional[float] = None
    realised_risk_usd: Optional[float] = None
    configured_risk_usd: Optional[float] = None
    mae_pts: Optional[float] = None
    mfe_pts: Optional[float] = None
    mae_pct: Optional[float] = None
    mfe_pct: Optional[float] = None
    exit_reason: Optional[str] = None
    stop_hit: Optional[bool] = None
    holding_seconds: Optional[float] = None
    trade_id: Optional[int] = None
    provenance: dict[str, Any] = None
    detail: str = ""

    @property
    def resolved(self) -> bool:
        return self.status in RESOLVED_STATUSES


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
            raise OutcomeStoreError(f"database not found: {p}")
        con = sqlite3.connect(f"{p.as_uri()}?mode=ro", uri=True, timeout=10.0)
    else:
        con = sqlite3.connect(p, timeout=10.0)
        con.execute("PRAGMA foreign_keys = ON")
    con.row_factory = sqlite3.Row
    return con


def register_contract(db_path, registered_at: str,
                      contract: Mapping[str, Any] | None = None) -> int:
    """Version for the label contract: existing for a known hash, else next."""
    body = dict(contract or LABEL_CONTRACT)
    name = body["label"]
    h = _content_hash(body)
    con = _connect(db_path)
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT label_version FROM label_contracts "
            "WHERE label_name = ? AND contract_hash = ?", (name, h)).fetchone()
        if row is not None:
            con.commit()
            return int(row["label_version"])
        nxt = (con.execute(
            "SELECT MAX(label_version) FROM label_contracts WHERE label_name = ?",
            (name,)).fetchone()[0] or 0) + 1
        con.execute(
            "INSERT INTO label_contracts "
            "(label_name, label_version, contract_hash, contract_json, registered_at) "
            "VALUES (?,?,?,?,?)",
            (name, nxt, h, _canonical_json(body), registered_at))
        con.commit()
        return nxt
    finally:
        con.close()


def _open_cycles(trading_db) -> dict[str, dict[str, Any]]:
    """Decision ids whose entry filled but whose position is still open.

    Read from the fill ledger and the open position rather than from
    `positions.strategy`, which mis-attributes automated orders as 'manual'.
    """
    con = connect_ro(trading_db)
    try:
        if not has_table(con, "order_fills"):
            return {}
        out: dict[str, dict[str, Any]] = {}
        for pos in con.execute(
                "SELECT * FROM positions WHERE status <> 'closed' "
                "ORDER BY entry_ts").fetchall():
            fill = con.execute(
                "SELECT * FROM order_fills WHERE role='entry' AND processed_ts = ?",
                (pos["entry_ts"],)).fetchone()
            if fill is None:
                continue
            order = con.execute(
                "SELECT client_order_id, strategy FROM orders WHERE delta_order_id = ? "
                "ORDER BY id LIMIT 1", (str(fill["delta_order_id"]),)).fetchone()
            if order is None or not order["client_order_id"]:
                continue
            out[str(order["client_order_id"])] = {
                "position_id": pos["id"],
                "entry_ts": pos["entry_ts"],
                "entry_price": float(pos["entry_price"]),
                "quantity": float(fill["size"]),
                "side": pos["side"],
                "entry_fee": float(fill["fee"]) if fill["fee"] is not None else None,
                "delta_order_id": fill["delta_order_id"],
                "strategy": order["strategy"],
            }
        return out
    finally:
        con.close()


def _rejections(trading_db) -> dict[str, dict[str, Any]]:
    con = connect_ro(trading_db)
    try:
        if not has_table(con, "order_rejections"):
            return {}
        return {str(r["client_order_id"]): dict(r) for r in con.execute(
            "SELECT * FROM order_rejections").fetchall()}
    finally:
        con.close()


def _order_status(trading_db) -> dict[str, list[dict[str, Any]]]:
    con = connect_ro(trading_db)
    try:
        out: dict[str, list[dict[str, Any]]] = {}
        for r in con.execute(
                "SELECT id, client_order_id, delta_order_id, status, strategy, placed_ts "
                "FROM orders WHERE client_order_id IS NOT NULL ORDER BY id"):
            out.setdefault(str(r["client_order_id"]), []).append(dict(r))
        return out
    finally:
        con.close()


def _label_from_trade(decision_id: str, decision_dt: datetime,
                      t: CompletedTrade) -> OutcomeLabel:
    entry_dt = to_utc(t.entry_ts, "iso")
    if entry_dt is not None and entry_dt < decision_dt:
        raise LabelLeakageError(
            f"{decision_id}: entry {t.entry_ts} precedes decision "
            f"{decision_dt.isoformat()}; a label may only use information after "
            "the decision instant")
    exit_dt = to_utc(t.exit_ts, "iso")
    if entry_dt is not None and exit_dt is not None and exit_dt < entry_dt:
        raise LabelLeakageError(
            f"{decision_id}: exit {t.exit_ts} precedes entry {t.entry_ts}")

    gross = None
    if t.entry_price:
        gross = (t.exit_price / t.entry_price - 1.0) * t.direction
    return OutcomeLabel(
        decision_id=decision_id,
        decision_ts=decision_dt.isoformat(),
        status=COMPLETED,
        trade_occurred=True,
        direction=t.direction,
        quantity=t.quantity,
        entry_ts=t.entry_ts,
        entry_price=t.entry_price,
        exit_ts=t.exit_ts,
        exit_price=t.exit_price,
        realised_pnl_usd=t.realised_pnl,
        gross_return=gross,
        fees_usd=t.fees_total,
        realised_risk_usd=t.realised_risk_usd,
        configured_risk_usd=t.configured_risk_usd,
        mae_pts=t.mae_pts,
        mfe_pts=t.mfe_pts,
        mae_pct=t.mae_pct,
        mfe_pct=t.mfe_pct,
        exit_reason=t.exited_via,
        stop_hit=(t.exited_via == "protective_stop"),
        holding_seconds=t.holding_seconds,
        trade_id=t.trade_id,
        provenance={
            "entry_delta_order_id": t.entry_delta_order_id,
            "exit_delta_order_id": t.exit_delta_order_id,
            "entry_exch_ts": t.entry_exch_ts,
            "exit_exch_ts": t.exit_exch_ts,
            "fill_ids": list(t.fill_ids),
            "stop_client_order_id": t.stop_client_order_id,
            "symbol": t.symbol,
            "strategy": t.strategy,
        },
    )


def build_labels(decisions: Mapping[str, datetime], trading_db) -> list[OutcomeLabel]:
    """One label per decision id. Deterministic; never drops a decision.

    `decisions` maps decision_id -> decision instant, normally taken from the
    persisted feature rows so that features and labels describe the same set.
    """
    trades = build_dataset(trading_db)
    by_coid: dict[str, list[CompletedTrade]] = {}
    for t in trades:
        if t.entry_client_order_id:
            by_coid.setdefault(str(t.entry_client_order_id), []).append(t)
    open_by_coid = _open_cycles(trading_db)
    rejected = _rejections(trading_db)
    orders = _order_status(trading_db)

    out: list[OutcomeLabel] = []
    for decision_id in sorted(decisions):
        decision_dt = decisions[decision_id]
        matched = by_coid.get(decision_id, [])
        if len(matched) > 1:
            # Deterministic and loud: pick the earliest by entry, and say so.
            matched = sorted(matched, key=lambda x: (x.entry_ts, x.trade_id))
            detail = (f"{len(matched)} completed trades share this decision id; "
                      f"labelled from the earliest (trade {matched[0].trade_id})")
        else:
            detail = ""

        if matched:
            lab = _label_from_trade(decision_id, decision_dt, matched[0])
            out.append(lab if not detail else OutcomeLabel(**{**asdict(lab),
                                                             "detail": detail}))
            continue

        op = open_by_coid.get(decision_id)
        if op is not None:
            entry_dt = to_utc(op["entry_ts"], "iso")
            if entry_dt is not None and entry_dt < decision_dt:
                raise LabelLeakageError(
                    f"{decision_id}: open entry {op['entry_ts']} precedes decision")
            out.append(OutcomeLabel(
                decision_id=decision_id, decision_ts=decision_dt.isoformat(),
                status=OPEN, trade_occurred=True,
                direction=1 if str(op["side"]).lower() == "buy" else -1,
                quantity=op["quantity"], entry_ts=op["entry_ts"],
                entry_price=op["entry_price"], fees_usd=op["entry_fee"],
                provenance={"position_id": op["position_id"],
                            "entry_delta_order_id": op["delta_order_id"],
                            "strategy": op["strategy"]},
                detail="position still open; outcome not yet knowable"))
            continue

        rows = orders.get(decision_id, [])
        if not rows:
            out.append(OutcomeLabel(
                decision_id=decision_id, decision_ts=decision_dt.isoformat(),
                status=UNMATCHED, trade_occurred=False, provenance={},
                detail="no order carries this client_order_id"))
            continue

        statuses = sorted({r["status"] for r in rows})
        rej = rejected.get(decision_id)
        out.append(OutcomeLabel(
            decision_id=decision_id, decision_ts=decision_dt.isoformat(),
            status=NO_TRADE, trade_occurred=False,
            provenance={"order_ids": [r["id"] for r in rows],
                        "order_statuses": statuses,
                        "rejection_code": (rej or {}).get("error_code")},
            detail=(f"order status {statuses}" if not rej
                    else f"rejected: {rej.get('error_code')}")))
    return out


def _payload(lab: OutcomeLabel, version: int) -> dict[str, Any]:
    d = asdict(lab)
    prov = d.pop("provenance") or {}
    d.pop("detail")
    return {
        "decision_id": lab.decision_id,
        "label_name": LABEL_CONTRACT["label"],
        "label_version": version,
        "contract_hash": LABEL_CONTRACT_HASH,
        **{k: (int(v) if isinstance(v, bool) else v)
           for k, v in d.items() if k not in ("decision_id",)},
        "provenance_json": _canonical_json(prov),
        "detail": lab.detail or "",
        "schema_version": SCHEMA_VERSION,
    }


def persist_labels(db_path, labels: Sequence[OutcomeLabel],
                   written_at: str) -> dict[str, int]:
    """Idempotent. A differing payload under the same key raises rather than
    overwriting; `written_at` is excluded from that comparison."""
    version = register_contract(db_path, written_at)
    written = unchanged = 0
    con = _connect(db_path)
    try:
        con.execute("BEGIN IMMEDIATE")
        for lab in labels:
            payload = _payload(lab, version)
            existing = con.execute(
                "SELECT * FROM decision_outcomes WHERE decision_id=? AND "
                "label_name=? AND label_version=?",
                (lab.decision_id, payload["label_name"], version)).fetchone()
            if existing is not None:
                stored = {k: existing[k] for k in payload}
                if stored != payload:
                    diff = sorted(k for k in payload if stored[k] != payload[k])
                    raise DivergenceError(
                        f"{lab.decision_id} already labelled differently in {diff}; "
                        "bump the label version rather than restating history")
                unchanged += 1
                continue
            cols = list(payload) + ["written_at"]
            con.execute(
                f"INSERT INTO decision_outcomes ({','.join(cols)}) "
                f"VALUES ({','.join('?' * len(cols))})",
                [payload[c] for c in payload] + [written_at])
            written += 1
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return {"written": written, "unchanged": unchanged, "label_version": version}


def load_labels(db_path, status: str | None = None) -> list[dict[str, Any]]:
    con = _connect(db_path, read_only=True)
    try:
        sql = "SELECT * FROM decision_outcomes"
        params: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY decision_ts, decision_id, label_version"
        out = []
        for r in con.execute(sql, params).fetchall():
            rec = dict(r)
            rec["provenance"] = json.loads(r["provenance_json"])
            rec["trade_occurred"] = bool(r["trade_occurred"])
            rec["stop_hit"] = None if r["stop_hit"] is None else bool(r["stop_hit"])
            out.append(rec)
        return out
    finally:
        con.close()


def verify_temporal_order(db_path) -> list[dict[str, Any]]:
    """Re-check stored labels: entry after decision, exit not before entry.

    Reads only what was written, so a stored corpus can be re-checked without
    the trading database present.
    """
    violations = []
    for rec in load_labels(db_path):
        d = to_utc(rec["decision_ts"], "iso")
        e = to_utc(rec["entry_ts"], "iso")
        x = to_utc(rec["exit_ts"], "iso")
        if e is not None and d is not None and e < d:
            violations.append({**rec, "reason": "entry precedes decision"})
        if x is not None and e is not None and x < e:
            violations.append({**rec, "reason": "exit precedes entry"})
        if rec["status"] == OPEN and rec["exit_ts"] is not None:
            violations.append({**rec, "reason": "OPEN label carries an exit"})
        if rec["status"] == COMPLETED and rec["exit_ts"] is None:
            violations.append({**rec, "reason": "COMPLETED label without an exit"})
    return violations


def coverage(db_path) -> dict[str, Any]:
    con = _connect(db_path, read_only=True)
    try:
        by_status = {r["status"]: r["n"] for r in con.execute(
            "SELECT status, COUNT(*) n FROM decision_outcomes GROUP BY status "
            "ORDER BY status")}
        total = sum(by_status.values())
        resolved = sum(v for k, v in by_status.items() if k in RESOLVED_STATUSES)
        contracts = [dict(c) for c in con.execute(
            "SELECT label_name, label_version, contract_hash FROM label_contracts "
            "ORDER BY label_version")]
        return {"labels": total, "by_status": by_status, "resolved": resolved,
                "resolved_rate": (resolved / total) if total else 0.0,
                "contracts": contracts, "schema_version": SCHEMA_VERSION}
    finally:
        con.close()
