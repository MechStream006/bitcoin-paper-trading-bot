"""
Evidence Library — state machine and transition predicates.

Pure functions only: no DB I/O, no file I/O.
All inputs come from registry.yaml entries and run metric dicts.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import yaml

VALID_STATES = frozenset({
    "discovery",
    "insufficient_sample",
    "disqualified",
    "candidate",
    "validated",
    "promotion_ready",
    "production_ready",
    "rejected",
    "retired",
})

# Allowed forward and demotion transitions.
# Any state (except terminal ones) can also transition to "disqualified" or "retired"
# on hard DQ — those arcs are added programmatically below.
_BASE_TRANSITIONS: dict[str, set[str]] = {
    "discovery":           {"insufficient_sample", "candidate"},
    "insufficient_sample": {"discovery", "candidate"},
    "candidate":           {"validated", "rejected"},
    "validated":           {"promotion_ready", "rejected"},
    "promotion_ready":     {"production_ready", "validated"},   # demotion: validated
    "production_ready":    {"validated"},                       # demotion if forward decays
    "disqualified":        set(),
    "rejected":            set(),
    "retired":             set(),
}

_TERMINAL = frozenset({"disqualified", "rejected", "retired"})

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {}
for _s, _targets in _BASE_TRANSITIONS.items():
    _extra: set[str] = set()
    if _s not in _TERMINAL:
        _extra |= {"disqualified", "retired"}
    ALLOWED_TRANSITIONS[_s] = frozenset(_targets | _extra)


def can_transition(from_state: str, to_state: str) -> bool:
    return to_state in ALLOWED_TRANSITIONS.get(from_state, frozenset())


# ---------------------------------------------------------------------------
# Candidate gate
# ---------------------------------------------------------------------------

def check_candidate_gate(
    family_meta: dict,
    run_metrics: dict,
    thresholds: dict,
) -> tuple[bool, list[dict]]:
    """
    Evaluate Candidate gate for a signal given its latest run metrics.

    Returns (overall_passed, gate_results).
    gate_results is a list of dicts: {gate_code, role, passed, observed, threshold}.

    Applies Edge or Feature paths based on family_meta['role'] (list).
    Hard DQ (E3 knife-edge) is checked first and short-circuits on failure.
    """
    results: list[dict] = []
    t_c = thresholds["candidate"]

    sharpe          = _f(run_metrics.get("sharpe"))
    oos_sharpe      = _f(run_metrics.get("oos_sharpe"))
    sensitivity     = _f(run_metrics.get("sens_pass_frac"))
    wf_frac         = _f(run_metrics.get("wf_pass_frac"))
    loo             = _f(run_metrics.get("loo_sign_consistency"))
    breakeven       = _f(run_metrics.get("breakeven_cost_bps"))
    struct_ok       = bool(run_metrics.get("struct_sign_ok", False))
    n_trades        = int(run_metrics.get("n_trades") or 0)

    roles = _roles(family_meta)

    def _gate(code: str, role: str | None, passed: bool, observed: Any, threshold: Any) -> bool:
        results.append({
            "gate_code": code,
            "role": role,
            "passed": passed,
            "observed": observed,
            "threshold": threshold,
        })
        return passed

    # Hard DQ — sensitivity knife-edge
    knife_edge_thr = t_c["hard_dq"]["knife_edge_sensitivity"]
    if not _gate("E3_KNIFE_EDGE", None, sensitivity >= knife_edge_thr,
                 sensitivity, knife_edge_thr):
        return False, results

    passed_any = False

    if "edge" in roles:
        t_e = t_c["edge"]
        p = True
        p &= _gate("B_SHARPE",      "edge", sharpe    >= t_e["min_sharpe"],
                   sharpe, t_e["min_sharpe"])
        p &= _gate("B_WF",          "edge", wf_frac   >= t_e["min_wf_agreement"],
                   wf_frac, t_e["min_wf_agreement"])
        p &= _gate("B_SENSITIVITY", "edge", sensitivity >= t_e["min_sensitivity"],
                   sensitivity, t_e["min_sensitivity"])
        p &= _gate("B_LOO",         "edge", loo >= t_e["min_loo_consistency"],
                   loo, t_e["min_loo_consistency"])
        p &= _gate("B_BREAKEVEN",   "edge", breakeven >= t_e["min_breakeven_bps"],
                   breakeven, t_e["min_breakeven_bps"])
        if t_e.get("structural_sign_required"):
            p &= _gate("B_STRUCTURAL", "edge", struct_ok, struct_ok, True)
        if p:
            passed_any = True

    if "feature" in roles:
        t_f = t_c["feature"]
        p = True
        p &= _gate("B_SENSITIVITY",   "feature", sensitivity >= t_f["min_sensitivity"],
                   sensitivity, t_f["min_sensitivity"])
        oos_ok = not math.isnan(oos_sharpe) and oos_sharpe > 0
        p &= _gate("B_OOS_POSITIVE", "feature", oos_ok, oos_sharpe, ">0")
        if p:
            passed_any = True

    return passed_any, results


# ---------------------------------------------------------------------------
# Hard DQ check (standalone — for ad-hoc disqualification decisions)
# ---------------------------------------------------------------------------

def check_hard_dq(
    run_metrics: dict,
    thresholds: dict,
) -> tuple[bool, list[str]]:
    """Returns (is_dq, [reasons]). Used to decide disqualified transition."""
    reasons: list[str] = []
    t = thresholds["candidate"]["hard_dq"]

    sensitivity = _f(run_metrics.get("sens_pass_frac"))
    thr = t["knife_edge_sensitivity"]
    if sensitivity < thr:
        reasons.append(
            f"E3: knife-edge sensitivity {sensitivity:.0%} < {thr:.0%} threshold"
        )
    return bool(reasons), reasons


# ---------------------------------------------------------------------------
# Validated gate pre-conditions (checked structurally — stats are in the run)
# ---------------------------------------------------------------------------

def check_validated_preconditions(
    family_meta: dict,
    pinned_run: dict,
    thresholds: dict,
) -> tuple[bool, list[str]]:
    """
    Structural pre-conditions for Validated state.

    Reads ALL thresholds from thresholds.yaml validated.<role> sub-dicts.
    No hardcoded fallback values — every requirement is driven by the YAML.
    Changing a threshold in thresholds.yaml takes effect immediately.

    For signals with multiple roles the requirements union across roles;
    numeric floors/ceilings use the most conservative value (highest min_*,
    lowest max_*).

    Returns (ok, [errors]).
    """
    errors: list[str] = []
    t_validated = thresholds.get("validated", {})
    roles = _roles(family_meta)

    if not roles:
        errors.append(
            "Signal has no declared role — cannot evaluate Validated gate. "
            "Set role: [edge] or role: [feature] in registry.yaml before promoting."
        )
        return False, errors

    # Resolve per-role threshold sub-dicts; fail loudly for undefined roles.
    role_tds: list[tuple[str, dict]] = []
    for role in roles:
        td = t_validated.get(role)
        if td is None:
            errors.append(
                f"No threshold definition for validated.{role} in thresholds.yaml — "
                f"add a 'validated.{role}' section before promoting."
            )
            return False, errors
        role_tds.append((role, td))

    # ── Provenance ────────────────────────────────────────────────────────────
    prov = pinned_run.get("provenance", "offline-corpus")
    roles_req_prov = [r for r, td in role_tds if td.get("requires_non_offline_provenance")]
    if roles_req_prov and prov == "offline-corpus":
        errors.append(
            f"Validated gate {roles_req_prov}: requires live-forward or mainnet "
            f"provenance; pinned run provenance is '{prov}'"
        )

    # ── DSR probability ───────────────────────────────────────────────────────
    # Applied only when min_dsr_prob is defined in the role's threshold sub-dict.
    dsr_floors = [td["min_dsr_prob"] for _, td in role_tds if "min_dsr_prob" in td]
    if dsr_floors:
        min_dsr = max(dsr_floors)  # most conservative across roles
        dsr_prob = pinned_run.get("dsr_prob")
        if dsr_prob is None or math.isnan(float(dsr_prob)):
            errors.append(
                "DSR probability is absent from pinned run — must be computed "
                "(governance.dsr()) before Validated promotion"
            )
        elif float(dsr_prob) < min_dsr:
            errors.append(
                f"DSR probability {float(dsr_prob):.3f} < {min_dsr} "
                "(thresholds.yaml validated.<role>.min_dsr_prob)"
            )

    # ── BH-FDR p-value ────────────────────────────────────────────────────────
    # Applied only when max_bh_p is defined in the role's threshold sub-dict.
    bh_ceilings = [td["max_bh_p"] for _, td in role_tds if "max_bh_p" in td]
    if bh_ceilings:
        max_bh = min(bh_ceilings)  # most conservative across roles
        bh_p = pinned_run.get("bh_p")
        if bh_p is None or math.isnan(float(bh_p)):
            errors.append(
                "BH-FDR p-value is absent from pinned run — must be computed "
                "(governance.bh_fdr()) before Validated promotion"
            )
        elif float(bh_p) > max_bh:
            errors.append(
                f"BH-FDR p {float(bh_p):.4f} > q={max_bh} "
                "(thresholds.yaml validated.<role>.max_bh_p)"
            )

    return len(errors) == 0, errors


# ---------------------------------------------------------------------------
# Registry consistency validator
# ---------------------------------------------------------------------------

def validate_registry(
    registry: dict,
    ledger_db_path: Path | str,
    thresholds: dict,
) -> list[str]:
    """
    Check that every state in registry.yaml is supported by evidence in ledger.db.
    Returns a list of error strings (empty = clean).
    """
    import record

    errors: list[str] = []
    states_needing_run = {"candidate", "validated", "promotion_ready", "production_ready"}

    for fam_id, fam in registry.items():
        state = fam.get("state", "discovery")

        if state not in VALID_STATES:
            errors.append(f"{fam_id}: unknown state '{state}'")
            continue

        if state in states_needing_run:
            pinned_run_id = fam.get("pinned_run")
            if not pinned_run_id:
                errors.append(
                    f"{fam_id}: state={state} but pinned_run is null — "
                    "run seed_from_scoreboard.py and set pinned_run"
                )
                continue

            run = record.get_run(ledger_db_path, pinned_run_id)
            if run is None:
                errors.append(
                    f"{fam_id}: pinned_run '{pinned_run_id}' not found in ledger.db"
                )
                continue

            gate_results = record.get_gate_results(ledger_db_path, pinned_run_id)
            if not gate_results and state == "candidate":
                errors.append(
                    f"{fam_id}: candidate state but no gate_results in ledger for run "
                    f"'{pinned_run_id[:8]}' — re-run screen_candidate()"
                )

            if state == "validated":
                prec_ok, prec_errs = check_validated_preconditions(fam, run, thresholds)
                for e in prec_errs:
                    errors.append(f"{fam_id}: {e}")

        claimed_state = fam.get("state")
        claimed_superseded_by = fam.get("superseded_by")
        if claimed_superseded_by and claimed_state not in ("retired",):
            errors.append(
                f"{fam_id}: superseded_by is set but state is '{claimed_state}' — "
                "a superseded family must be in 'retired' state"
            )

    return errors


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _f(v: Any) -> float:
    if v is None:
        return float("nan")
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def _roles(family_meta: dict) -> list[str]:
    r = family_meta.get("role", [])
    if isinstance(r, str):
        return [r]
    return list(r)


def load_thresholds(path: Path | str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _THIS_DIR = Path(__file__).resolve().parent
    t = load_thresholds(_THIS_DIR / "thresholds.yaml")

    assert can_transition("discovery", "candidate")
    assert can_transition("candidate", "validated")
    assert can_transition("validated", "rejected")
    assert can_transition("candidate", "disqualified")
    assert not can_transition("retired", "candidate")
    assert not can_transition("disqualified", "candidate")
    assert not can_transition("rejected", "validated")

    # H6-like run — should pass Edge gate
    run_h6 = {
        "sharpe": 0.90, "oos_sharpe": -0.15, "sens_pass_frac": 1.0,
        "wf_pass_frac": 0.67, "loo_sign_consistency": 1.0,
        "breakeven_cost_bps": 100, "struct_sign_ok": True, "n_trades": 22,
    }
    fam_h6 = {"role": ["edge"]}
    passed, results = check_candidate_gate(fam_h6, run_h6, t)
    assert passed, f"H6 should pass edge gate: {[r for r in results if not r['passed']]}"

    # Inside Bar — sensitivity 0% — should DQ
    run_insidebar = {
        "sharpe": 0.66, "oos_sharpe": 0.53, "sens_pass_frac": 0.0,
        "wf_pass_frac": 0.67, "loo_sign_consistency": 1.0,
        "breakeven_cost_bps": 50, "struct_sign_ok": True, "n_trades": 148,
    }
    fam_ib = {"role": ["edge"]}
    passed, results = check_candidate_gate(fam_ib, run_insidebar, t)
    assert not passed, "Inside Bar (sens=0%) should fail DQ"
    assert results[0]["gate_code"] == "E3_KNIFE_EDGE"

    # ATR Expansion — sensitivity 50% — should fail (gray zone, not DQ, but below 60%)
    run_atr = {
        "sharpe": 0.48, "oos_sharpe": 0.67, "sens_pass_frac": 0.50,
        "wf_pass_frac": 0.67, "loo_sign_consistency": 1.0,
        "breakeven_cost_bps": 50, "struct_sign_ok": True, "n_trades": 152,
    }
    fam_atr = {"role": ["edge"]}
    passed, results = check_candidate_gate(fam_atr, run_atr, t)
    assert not passed, "ATR Expansion (sens=50%) should fail candidate gate"

    # H5 Vol Regimes — Feature gate: OOS +0.68, sens 100%
    run_h5 = {
        "sharpe": 0.13, "oos_sharpe": 0.68, "sens_pass_frac": 1.0,
        "wf_pass_frac": 0.75, "loo_sign_consistency": 0.67,
        "breakeven_cost_bps": 50, "struct_sign_ok": True, "n_trades": 30,
    }
    fam_h5 = {"role": ["feature"]}
    passed, results = check_candidate_gate(fam_h5, run_h5, t)
    assert passed, f"H5 should pass feature gate: {[r for r in results if not r['passed']]}"

    print("lifecycle.py smoke test: OK")
