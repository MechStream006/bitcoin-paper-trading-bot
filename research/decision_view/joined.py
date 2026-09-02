"""
joined.py
=========
One row per decision: the features known at the decision instant, joined to the
execution outcome that followed.

Read-only. Reads `decision_features.db` and `decision_outcomes.db` and writes to
neither. Nothing is recomputed -- both halves are read exactly as persisted, so
this view cannot disagree with the stores it summarises.

VERSION SELECTION IS BY HASH, NOT BY "LATEST"
---------------------------------------------
The obvious rule -- take the highest `contract_version` per feature -- happens
to give the right answer today, and would quietly stop doing so the moment
anyone registers an experimental v3. The intended set is resolved instead by
hashing the live `FeatureSpec`s and looking up the version registered for each
hash. A spec whose hash was never registered is an error naming the feature,
never a silent fallback to some other version.

THE JOIN IS STRICTLY BY decision_id, AND LOSES NOBODY
-----------------------------------------------------
Every decision present in either store appears exactly once. A decision with no
label, or a feature never persisted under the intended contract, is carried with
an explicit marker rather than dropped -- an inner join here would quietly
delete the OPEN and NO_TRADE decisions, which are precisely the ones that show
what the pipeline does when there is no outcome to report.

`MISSING_FROM_STORE` is deliberately not one of `dq.states`. Those eight states
describe whether *data* was ready at a decision instant; this says the *store*
never received a row for this contract, which is a different failure with a
different fix. Conflating them would let a persistence gap read as a market
condition.

TEMPORAL INTEGRITY IS RE-CHECKED, NOT ASSUMED
---------------------------------------------
Both halves were validated when written, by different code. This view re-checks
the boundary they share: every feature input must be available at or before the
decision instant, and every outcome timestamp must fall at or after it. The
check reads only the joined rows, so it holds without either source store being
reachable.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping, Optional, Sequence

_HERE = pathlib.Path(__file__).resolve().parent
_RESEARCH_ROOT = _HERE.parent
if str(_RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_ROOT))

from alignment.features import FeatureSpec, starter_features  # noqa: E402
from alignment.timebase import to_utc  # noqa: E402
from feature_store import decision_store as DS  # noqa: E402
from label_store import outcome_store as OS  # noqa: E402

#: A feature the store holds no row for under the intended contract. Not a
#: `dq.states` value: those describe data readiness at a decision instant,
#: this describes an absent persisted row.
MISSING_FROM_STORE = "MISSING_FROM_STORE"

#: A decision with feature rows but no outcome label.
NO_LABEL = "NO_LABEL"

RESEARCH_USE_ONLY = (
    "Research-derived view. Not training data: the sample is tiny, the option "
    "inputs come from testnet quotes that CLAUDE.md sec.11 classifies as "
    "pipeline-validation only, and non-COMPLETED outcomes are deliberately "
    "retained, so any naive fit would be over a mixture of resolved and "
    "unresolved decisions."
)


class JoinError(ValueError):
    """The view could not be constructed from the stores as given."""


class UnregisteredContract(JoinError):
    """A live FeatureSpec hashes to a contract the store never registered."""


@dataclass(frozen=True)
class JoinedFeature:
    name: str
    contract_version: Optional[int]
    contract_hash: Optional[str]
    state: str
    action: Optional[str]
    value: Any
    available: bool
    inputs: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class JoinedRow:
    decision_id: str
    decision_ts: Optional[str]
    feature_row_state: Optional[str]
    feature_row_action: Optional[str]
    features: dict[str, JoinedFeature]
    outcome_status: str
    outcome: Optional[dict[str, Any]]
    findings: tuple[str, ...] = ()

    @property
    def resolved(self) -> bool:
        return self.outcome_status == OS.COMPLETED

    @property
    def all_features_available(self) -> bool:
        return bool(self.features) and all(f.available for f in self.features.values())

    def values(self) -> dict[str, Any]:
        """Only the features that actually resolved. Absent means absent."""
        return {n: f.value for n, f in self.features.items() if f.available}


def intended_versions(feature_db, specs: Sequence[FeatureSpec]
                      ) -> dict[str, tuple[int, str]]:
    """feature name -> (contract_version, contract_hash), resolved by hash."""
    out: dict[str, tuple[int, str]] = {}
    missing: list[str] = []
    for spec in specs:
        h = DS.contract_hash(spec)
        version = _version_for_hash(feature_db, spec.name, h)
        if version is None:
            missing.append(f"{spec.name}({h[:12]})")
            continue
        out[spec.name] = (version, h)
    if missing:
        raise UnregisteredContract(
            "no persisted contract for " + ", ".join(sorted(missing)) +
            "; persist the feature rows under this contract before joining")
    return out


def _version_for_hash(feature_db, name: str, contract_hash: str) -> Optional[int]:
    con = DS._connect(feature_db, read_only=True)
    try:
        row = con.execute(
            "SELECT contract_version FROM feature_contracts "
            "WHERE feature_name = ? AND contract_hash = ?",
            (name, contract_hash)).fetchone()
        return None if row is None else int(row["contract_version"])
    finally:
        con.close()


def build_view(feature_db, outcome_db, specs: Sequence[FeatureSpec] | None = None,
               signal_id: str = "g3_momentum_tsmom") -> list[JoinedRow]:
    """One JoinedRow per decision found in either store, ordered by decision_ts."""
    specs = list(specs if specs is not None else starter_features(signal_id))
    versions = intended_versions(feature_db, specs)

    rows_by_decision: dict[str, dict[str, dict[str, Any]]] = {}
    ts_by_decision: dict[str, str] = {}
    rowmeta: dict[str, tuple[Optional[str], Optional[str]]] = {}
    for rec in DS.load_rows(feature_db):
        want = versions.get(rec["feature_name"])
        if want is None or rec["contract_version"] != want[0]:
            continue
        did = rec["decision_id"]
        rows_by_decision.setdefault(did, {})[rec["feature_name"]] = rec
        ts_by_decision.setdefault(did, rec["decision_ts"])
        rowmeta.setdefault(did, (rec["row_state"], rec["row_action"]))

    labels = {rec["decision_id"]: rec for rec in OS.load_labels(outcome_db)}
    for did, rec in labels.items():
        ts_by_decision.setdefault(did, rec["decision_ts"])

    out: list[JoinedRow] = []
    for did in sorted(ts_by_decision, key=lambda d: (ts_by_decision[d], d)):
        stored = rows_by_decision.get(did, {})
        features: dict[str, JoinedFeature] = {}
        findings: list[str] = []
        for spec in specs:
            version, chash = versions[spec.name]
            rec = stored.get(spec.name)
            if rec is None:
                findings.append(f"{spec.name}: no persisted row at v{version}")
                features[spec.name] = JoinedFeature(
                    name=spec.name, contract_version=version, contract_hash=chash,
                    state=MISSING_FROM_STORE, action=None, value=None,
                    available=False)
                continue
            available = rec["value"] is not None and rec["action"] == "COMPUTE"
            features[spec.name] = JoinedFeature(
                name=spec.name, contract_version=rec["contract_version"],
                contract_hash=rec["contract_hash"], state=rec["state"],
                action=rec["action"], value=rec["value"], available=available,
                inputs=tuple(dict(i) for i in rec["inputs"]))

        label = labels.get(did)
        if label is None:
            findings.append("no outcome label for this decision")
        state, action = rowmeta.get(did, (None, None))
        out.append(JoinedRow(
            decision_id=did,
            decision_ts=ts_by_decision[did],
            feature_row_state=state,
            feature_row_action=action,
            features=features,
            outcome_status=label["status"] if label else NO_LABEL,
            outcome=dict(label) if label else None,
            findings=tuple(findings),
        ))
    return out


def verify_temporal(rows: Iterable[JoinedRow]) -> list[dict[str, Any]]:
    """Re-check the boundary the two halves share, from the joined rows alone.

    A feature input may not be available after the decision; an outcome may not
    occur before it. Both stores checked their own side when written -- this
    checks that the join did not put a feature next to an outcome that precedes
    it.
    """
    violations: list[dict[str, Any]] = []

    def add(row: JoinedRow, reason: str, **extra: Any) -> None:
        violations.append({"decision_id": row.decision_id,
                           "decision_ts": row.decision_ts,
                           "reason": reason, **extra})

    for row in rows:
        d = to_utc(row.decision_ts, "iso")
        if d is None:
            add(row, "decision_ts is unparseable")
            continue
        for name, f in row.features.items():
            for i in f.inputs:
                a = to_utc(i.get("available_ts"), "iso")
                if a is not None and a > d:
                    add(row, "feature input available after the decision",
                        feature=name, input=i.get("input_name"),
                        available_ts=i.get("available_ts"))
                if (i.get("age_s") or 0) < 0:
                    add(row, "negative input age", feature=name,
                        input=i.get("input_name"))
        o = row.outcome or {}
        e, x = to_utc(o.get("entry_ts"), "iso"), to_utc(o.get("exit_ts"), "iso")
        if e is not None and e < d:
            add(row, "outcome entry precedes the decision", entry_ts=o.get("entry_ts"))
        if x is not None and e is not None and x < e:
            add(row, "outcome exit precedes its entry", exit_ts=o.get("exit_ts"))
        if row.outcome_status == OS.OPEN and o.get("exit_ts") is not None:
            add(row, "OPEN outcome carries an exit")
        if row.outcome_status == OS.COMPLETED and o.get("exit_ts") is None:
            add(row, "COMPLETED outcome without an exit")
    return violations


def coverage(rows: Sequence[JoinedRow]) -> dict[str, Any]:
    """Counts by outcome status, and the feature readiness matrix."""
    if not rows:
        return {"decisions": 0, "by_status": {}, "features": {},
                "research_use_only": RESEARCH_USE_ONLY}
    by_status: dict[str, int] = {}
    for r in rows:
        by_status[r.outcome_status] = by_status.get(r.outcome_status, 0) + 1

    names = list(rows[0].features)
    matrix: dict[str, Any] = {}
    for n in names:
        states: dict[str, int] = {}
        avail = 0
        for r in rows:
            f = r.features[n]
            states[f.state] = states.get(f.state, 0) + 1
            avail += 1 if f.available else 0
        versions = sorted({r.features[n].contract_version for r in rows
                           if r.features[n].contract_version is not None})
        matrix[n] = {"available": avail, "of": len(rows),
                     "states": dict(sorted(states.items())),
                     "contract_versions": versions}

    resolved = [r for r in rows if r.resolved]
    return {
        "decisions": len(rows),
        "by_status": dict(sorted(by_status.items())),
        "resolved": len(resolved),
        "resolved_with_all_features": sum(1 for r in resolved
                                          if r.all_features_available),
        "features": matrix,
        "decisions_with_findings": sum(1 for r in rows if r.findings),
        "research_use_only": RESEARCH_USE_ONLY,
    }


def to_records(rows: Iterable[JoinedRow]) -> list[dict[str, Any]]:
    """Audit-complete records: identity, contracts, states, provenance, outcome."""
    out = []
    for r in rows:
        out.append({
            "decision_id": r.decision_id,
            "decision_ts": r.decision_ts,
            "feature_row_state": r.feature_row_state,
            "feature_row_action": r.feature_row_action,
            "outcome_status": r.outcome_status,
            "entry_ts": (r.outcome or {}).get("entry_ts"),
            "exit_ts": (r.outcome or {}).get("exit_ts"),
            "realised_pnl_usd": (r.outcome or {}).get("realised_pnl_usd"),
            "features": {
                n: {"value": f.value, "state": f.state, "action": f.action,
                    "available": f.available,
                    "contract_version": f.contract_version,
                    "contract_hash": f.contract_hash,
                    "inputs": [
                        {k: i.get(k) for k in
                         ("input_name", "source", "state", "event_ts",
                          "available_ts", "age_s", "tolerance_s", "criticality",
                          "provenance", "detail")}
                        for i in f.inputs],
                    }
                for n, f in r.features.items()},
            "findings": list(r.findings),
            "research_use_only": True,
        })
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Joined feature-outcome view.")
    ap.add_argument("--feature-db", default=str(DS.DEFAULT_DB))
    ap.add_argument("--outcome-db", default=str(OS.DEFAULT_DB))
    ap.add_argument("--signal-id", default="g3_momentum_tsmom")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    rows = build_view(args.feature_db, args.outcome_db, signal_id=args.signal_id)
    violations = verify_temporal(rows)
    cov = coverage(rows)

    if args.json:
        print(json.dumps({"coverage": cov, "temporal_violations": violations,
                          "rows": to_records(rows)}, indent=2, sort_keys=True,
                         default=str))
        return 0

    print(f"decisions: {cov['decisions']}   by status: {cov['by_status']}")
    print(f"resolved with every feature available: "
          f"{cov['resolved_with_all_features']}/{cov['resolved']}")
    print("\nfeature readiness:")
    for n, m in cov["features"].items():
        print(f"  {n:20s} {m['available']}/{m['of']}  v{m['contract_versions']}  "
              f"{m['states']}")
    print(f"\ntemporal violations: {len(violations)}")
    for v in violations[:5]:
        print(f"  {v['decision_id']}: {v['reason']}")
    if cov["decisions_with_findings"]:
        print(f"\ndecisions carrying findings: {cov['decisions_with_findings']}")
    print(f"\n{RESEARCH_USE_ONLY}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
