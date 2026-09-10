"""
outcome_writer.py
=================
Resolve and persist execution-outcome labels for captured ACKED decisions.

Read-only over `decision_features.db` and `btc_trading.db`; the only thing it
writes is a COMPLETED or NO_TRADE row into `decision_outcomes.db`, and only when
that outcome is provably final.

WHY THIS EXISTS SEPARATELY FROM `outcome_store.build_labels`
----------------------------------------------------------
`build_labels` is the deterministic per-decision interpreter and is reused here
verbatim -- nothing about how a trade becomes COMPLETED / OPEN / NO_TRADE /
UNMATCHED is re-implemented. What it does NOT do is decide *whether a
non-COMPLETED interpretation is safe to freeze*. The outcome store is
append-only: one immutable slot per decision for the life of the contract. So a
NO_TRADE that `build_labels` returns for a decision whose order actually FILLED
(position row missing, or `entry_ts` drifted) would permanently record a
falsehood for a decision that DID trade. This module is the gate that only lets
a final outcome through.

THE INDEX IS THE FEATURE STORE, NOT THE ORDER TABLE
--------------------------------------------------
The set of decisions to resolve is exactly the set `decision_features.db`
captured -- `decision_store.decision_index()`, a single
`SELECT DISTINCT decision_id, decision_ts`. `decision_ts` is taken from there
too, so the label and the feature row describe the same instant and
`decision_view.joined` cannot find them inconsistent. P-MIRROR is not consulted
anywhere: an execution outcome does not require a `mirrored_forward_entry` row.

WHAT IS NEVER WRITTEN
--------------------
- OPEN (no realised outcome yet) and UNMATCHED (a fact about the record, not
  the market) -- left unlabelled, surfaced as NO_LABEL by the view.
- A NO_TRADE whose order is still open (a resting ACKED entry) -- left
  unlabelled, retried on the next sweep, never aged out.
- A NO_TRADE that contradicts the order state (terminal-but-FILLED, or a fill
  linked through `orders.delta_order_id`) -- refused and recorded as an
  anomaly, never persisted as an outcome.

RETRY
-----
A decision with no outcome row is eligible every sweep. `persist_labels` is
atomic, so a failed write leaves nothing and the next sweep re-derives the same
work. An identical re-derivation is idempotent (`persist_labels` reports it
`unchanged`); a divergent one makes `persist_labels` raise `DivergenceError`,
which is caught and recorded as an anomaly rather than overwriting. Nothing
here issues UPDATE or touches the append-only guards.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

_HERE = pathlib.Path(__file__).resolve().parent
_RESEARCH_ROOT = _HERE.parent
if str(_RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_ROOT))

from alignment.timebase import to_utc  # noqa: E402
from execution_evidence.completed_trades import connect_ro, has_table  # noqa: E402
from feature_store import decision_store as DS  # noqa: E402
from label_store import outcome_store as OS  # noqa: E402

_DEFAULT_TRADING_DB = _RESEARCH_ROOT.parent / "data" / "btc_trading.db"

#: Order states that will never fill from here. Mirrors the terminal set in
#: `src/execution/order_state.py`, kept as a literal because the research tree
#: does not import `src` (the same rule `completed_trades.py` follows for the
#: contract-value constant). "filled" is in the set: it is terminal, and the
#: NO_TRADE guard rejects it explicitly by name.
_TERMINAL_ORDER_STATES = frozenset({"filled", "cancelled", "rejected", "blocked"})


@dataclass(frozen=True)
class SweepResult:
    scanned: int                 # decisions in the feature index
    already_labelled: int        # already carried an outcome row
    persisted: int               # COMPLETED / NO_TRADE written this sweep
    unchanged: int               # re-derived identical to an existing row
    pending: int                 # not yet resolvable (OPEN, resting order, ...)
    anomalies: tuple[str, ...]   # "<decision_id>: <reason>"
    label_version: Optional[int]


def _build_labels_isolated(decisions, trading_db):
    """`build_labels` for the batch, falling back to per-decision on leakage.

    A `LabelLeakageError` means one decision's entry precedes its own decision
    instant -- impossible for a legitimately captured ACKED decision, so it is
    an anomaly, and the other decisions must still be resolved.
    """
    try:
        return list(OS.build_labels(decisions, trading_db)), []
    except OS.LabelLeakageError:
        labels, anomalies = [], []
        for did, dt in decisions.items():
            try:
                labels.extend(OS.build_labels({did: dt}, trading_db))
            except OS.LabelLeakageError as exc:
                anomalies.append(f"{did}: {type(exc).__name__}: {exc}")
        return labels, anomalies


def _no_trade_disposition(trading_db, decision_id: str) -> tuple[str, str]:
    """Is a `build_labels` NO_TRADE safe to freeze?

    Returns one of:
      "final"   -- every conjunct holds; persist NO_TRADE.
      "wait"    -- an order for this decision is still open; leave unlabelled.
      "anomaly" -- NO_TRADE contradicts the order state; refuse and record.
    """
    con = connect_ro(trading_db)
    try:
        orders = con.execute(
            "SELECT id, status, delta_order_id FROM orders "
            "WHERE client_order_id = ? ORDER BY id", (decision_id,)).fetchall()
        if not orders:
            return "wait", "no order carries this client_order_id"

        states = {str(o["status"]).lower() for o in orders}
        non_terminal = states - _TERMINAL_ORDER_STATES
        if non_terminal:
            return "wait", f"order state(s) not terminal: {sorted(non_terminal)}"
        if "filled" in states:
            return "anomaly", (
                "build_labels returned NO_TRADE but an order for this decision "
                "is FILLED")

        if has_table(con, "order_fills"):
            for o in orders:
                doid = o["delta_order_id"]
                if doid is None:
                    continue
                hit = con.execute(
                    "SELECT 1 FROM order_fills WHERE delta_order_id = ? LIMIT 1",
                    (str(doid),)).fetchone()
                if hit is not None:
                    return "anomaly", (
                        f"build_labels returned NO_TRADE but a fill is linked to "
                        f"order {o['id']} via delta_order_id {doid}")
        return "final", ""
    finally:
        con.close()


def sweep(features_db, outcomes_db, trading_db, *,
          written_at: str | None = None) -> SweepResult:
    """One idempotent pass: resolve every captured decision that has no outcome.

    Only COMPLETED and a fully-guarded NO_TRADE are persisted. Everything else
    is left for a later sweep or recorded as an anomaly.
    """
    features_db = pathlib.Path(features_db)
    if not features_db.exists():
        return SweepResult(0, 0, 0, 0, 0, (), None)

    index = DS.decision_index(features_db)
    if not index:
        return SweepResult(0, 0, 0, 0, 0, (), None)

    OS.init_db(outcomes_db)
    labelled = {r["decision_id"] for r in OS.load_labels(outcomes_db)}
    pending_ids = {did: ts for did, ts in index.items() if did not in labelled}
    if not pending_ids:
        return SweepResult(len(index), len(labelled), 0, 0, 0, (), None)

    decisions = {did: to_utc(ts, "iso") for did, ts in pending_ids.items()}
    labels, anomalies = _build_labels_isolated(decisions, trading_db)
    anomalies = list(anomalies)

    finals: list[OS.OutcomeLabel] = []
    pending = 0
    for lab in labels:
        if lab.status == OS.COMPLETED:
            finals.append(lab)
        elif lab.status == OS.NO_TRADE:
            disposition, why = _no_trade_disposition(trading_db, lab.decision_id)
            if disposition == "final":
                finals.append(lab)
            elif disposition == "anomaly":
                anomalies.append(f"{lab.decision_id}: {why}")
            else:
                pending += 1
        else:  # OPEN, UNMATCHED -- never frozen
            pending += 1

    written_at = written_at or datetime.now(timezone.utc).isoformat()
    persisted = unchanged = 0
    version: Optional[int] = None
    for lab in finals:
        try:
            res = OS.persist_labels(outcomes_db, [lab], written_at)
        except OS.DivergenceError as exc:
            anomalies.append(f"{lab.decision_id}: divergence on persist: {exc}")
            continue
        persisted += res["written"]
        unchanged += res["unchanged"]
        version = res["label_version"]

    return SweepResult(
        scanned=len(index),
        already_labelled=len(labelled),
        persisted=persisted,
        unchanged=unchanged,
        pending=pending,
        anomalies=tuple(anomalies),
        label_version=version,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[2])
    ap.add_argument("--features-db", default=str(DS.DEFAULT_DB))
    ap.add_argument("--outcomes-db", default=str(OS.DEFAULT_DB))
    ap.add_argument("--trading-db", default=str(_DEFAULT_TRADING_DB))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    result = sweep(args.features_db, args.outcomes_db, args.trading_db)
    if args.json:
        print(json.dumps(result.__dict__, indent=2, sort_keys=True, default=list))
    else:
        print(f"scanned={result.scanned} already_labelled={result.already_labelled} "
              f"persisted={result.persisted} unchanged={result.unchanged} "
              f"pending={result.pending}")
        for a in result.anomalies:
            print(f"  ANOMALY  {a}")
    return 1 if result.anomalies else 0


if __name__ == "__main__":
    raise SystemExit(main())
