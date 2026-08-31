"""
forward_admissibility.py
========================
`ForwardAdmissibilityRule` — the L2 analogue of `CandidateGateRule`, answering
exactly one question:

    Is this live-forward run admissible as pinnable evidence?

= the run is in lane L2, its pre-registration pin verified, and **every**
declared observation floor is MET. Nothing else.

WHAT IT DELIBERATELY DOES NOT ENFORCE (Gate FB-8 decisions)
-----------------------------------------------------------
- **DSR / BH-p / pairwise independence.** Already enforced by
  `lifecycle.check_validated_preconditions` from `thresholds.yaml` at the
  Validated gate. Duplicating them here would create two enforcement points for
  one frozen threshold.
- **Family-specific `promotion_criteria.gates`** (Q1). They are an unspecified
  platform contract: no schema artifact defines them, no code reads them, they
  appear in three incompatible shapes across eight pre-registrations, and their
  `threshold` values are prose expressions naming variables absent from the
  forward corpus.
- **`failure_analysis_required`** (Q2). A human/documentary obligation with no
  computable predicate.

IT READS NOTHING (Q3)
---------------------
`evaluate()` is a pure function of its arguments. It opens no database, reads no
file, and consults no registry. The observation-floor result is computed
**outside** the rule -- by `observation_floors.evaluate()` -- and handed in
through `run_metrics`, under the key `FORWARD_ADMISSIBILITY_KEY`.

That is not a stylistic choice. `evidence_governance.admit_evidence()` calls
`rule.evaluate(family_meta, run["metrics"], thresholds)`, where `run["metrics"]`
is the run row's immutable `metrics_json`, and records `inputs=()` on the
receipt. Replay therefore re-reads the run row and re-runs the rule. If the rule
read the live forward corpus instead, replay would drift as that corpus grows
and a historical verdict could silently change. Because the payload is frozen
inside an append-only row, replay at a fixed `as_of` is reproducible by
construction.

CONSEQUENCE, STATED PLAINLY
---------------------------
The ten L2 rows that exist today do **not** carry this payload -- `ledger_sync`
does not build one, and FB-8 does not change `ledger_sync` (doing so would put
floor evaluation inside the scheduled collection path, which governance
forbids). So this rule is correct and usable, but cannot be applied to the
existing rows through `admit_evidence` until some later, operator-invoked gate
supplies the payload. That is a wiring gap, not a defect in this rule.

FAIL-CLOSED
-----------
Missing payload, unrecognised schema, unverified pin, missing `as_of`, non-L2
lane, absent floors, or any floor NOT_MET / UNEVALUABLE -> not admissible. There
is no path through this module that returns True on incomplete information.
"""
from __future__ import annotations

from typing import Any

#: Where the caller must place the floor evidence inside the run's metrics.
FORWARD_ADMISSIBILITY_KEY = "forward_admissibility"

#: Payload schema this rule understands. An unrecognised schema fails closed
#: rather than being parsed on a guess.
PAYLOAD_SCHEMA = "forward-admissibility/1"

#: Identity recorded verbatim in every receipt this rule produces. Distinct from
#: CandidateGateRule's, so no receipt written under that rule changes meaning.
RULE_ID = "forward_evidence.observation_floors"
ENGINE_VERSION = "forward-admissibility-v1"

#: The only lane this rule will evaluate (record.LANES["L2"]).
LANE_PROVENANCE = "live-forward"
LANE_RUN_KIND = "forward"


def build_payload(
    floor_report: Any,
    provenance: str,
    run_kind: str,
) -> dict[str, Any]:
    """Serialise an `observation_floors.FloorReport` into the metrics payload.

    Deterministic and JSON-safe: the result is embedded in a run's
    `metrics_json` at append time and is what replay will see. Accepts the
    FloorReport structurally (duck-typed) so this module needs no import of
    observation_floors, keeping the rule free of file/DB dependencies.
    """
    return {
        "schema": PAYLOAD_SCHEMA,
        "as_of": floor_report.as_of,
        "provenance": provenance,
        "run_kind": run_kind,
        "prereg_path": floor_report.prereg_path,
        "prereg_pin_verified": floor_report.error is None,
        "error": floor_report.error,
        "floors": [
            {
                "name": r.name,
                "required": r.required,
                "observed": list(r.observed) if isinstance(r.observed, tuple) else r.observed,
                "status": r.status,
                "detail": r.detail,
            }
            for r in floor_report.results
        ],
    }


def _fail(gate_code: str, observed: Any, threshold: Any) -> list[dict]:
    return [{"gate_code": gate_code, "role": LANE_RUN_KIND,
             "passed": False, "observed": observed, "threshold": threshold}]


class ForwardAdmissibilityRule:
    """Implements the `evidence_governance.AdmissibilityRule` protocol.

    Structural conformance only -- the protocol is a `typing.Protocol`, so this
    class deliberately does not import evidence_library. That keeps the forward
    package importable inside `Dockerfile.research`, which excludes
    evidence_library by INV-1.
    """

    rule_id = RULE_ID
    engine_version = ENGINE_VERSION

    def evaluate(
        self,
        family_meta: dict,
        run_metrics: dict,
        thresholds: dict,
    ) -> tuple[bool, list[dict]]:
        """Return (passed, gate_results). Pure; reads nothing external."""
        payload = (run_metrics or {}).get(FORWARD_ADMISSIBILITY_KEY)
        if not isinstance(payload, dict):
            return False, _fail("FORWARD_PAYLOAD_PRESENT", None, PAYLOAD_SCHEMA)

        schema = payload.get("schema")
        if schema != PAYLOAD_SCHEMA:
            return False, _fail("FORWARD_PAYLOAD_SCHEMA", schema, PAYLOAD_SCHEMA)

        results: list[dict] = []

        lane = (payload.get("provenance"), payload.get("run_kind"))
        lane_ok = lane == (LANE_PROVENANCE, LANE_RUN_KIND)
        results.append({
            "gate_code": "FORWARD_LANE_IS_L2", "role": LANE_RUN_KIND,
            "passed": lane_ok, "observed": f"{lane[0]}/{lane[1]}",
            "threshold": f"{LANE_PROVENANCE}/{LANE_RUN_KIND}",
        })

        as_of = payload.get("as_of")
        results.append({
            "gate_code": "FORWARD_AS_OF_PRESENT", "role": LANE_RUN_KIND,
            "passed": bool(as_of), "observed": as_of, "threshold": "non-empty",
        })

        pin_ok = bool(payload.get("prereg_pin_verified")) and payload.get("error") is None
        results.append({
            "gate_code": "PREREGISTRATION_PIN_VERIFIED", "role": LANE_RUN_KIND,
            "passed": pin_ok,
            "observed": payload.get("error") or payload.get("prereg_path"),
            "threshold": "registry-pinned pre-registration, hash verified (A2a)",
        })

        floors = payload.get("floors")
        if not floors:
            # An absent or empty floor set is not an absent requirement.
            results.append({
                "gate_code": "OBSERVATION_FLOORS_DECLARED", "role": LANE_RUN_KIND,
                "passed": False, "observed": floors,
                "threshold": "at least one declared observation floor",
            })
        else:
            for f in floors:
                # MET passes. NOT_MET and UNEVALUABLE both fail closed.
                results.append({
                    "gate_code": f"FLOOR_{f.get('name')}", "role": LANE_RUN_KIND,
                    "passed": f.get("status") == "MET",
                    "observed": f.get("observed"),
                    "threshold": f.get("required"),
                })

        return all(r["passed"] for r in results), results
