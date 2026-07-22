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

# States whose entry requires a justifying_run_id -- the evidence the
# family was promoted on. Shared with record.append_transition() (PR-5,
# evidence_reporting_architecture.md §7.3) and validate_registry() below,
# so the two enforcement points (write-time, check-time) can never diverge.
STATES_REQUIRING_RUN = frozenset({"candidate", "validated", "promotion_ready", "production_ready"})

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {}
for _s, _targets in _BASE_TRANSITIONS.items():
    _extra: set[str] = set()
    if _s not in _TERMINAL:
        _extra |= {"disqualified", "retired"}
    ALLOWED_TRANSITIONS[_s] = frozenset(_targets | _extra)


def can_transition(from_state: str, to_state: str) -> bool:
    """
    PR-5 (docs/governance/evidence_reporting_architecture.md §9 PR-2, item 4)
    — THE ONE STATE-MACHINE TOUCH IN THIS DESIGN. Flagged explicitly here,
    in the migration docs, and in the PR-5 deliverables for review: a
    self-transition (from_state == to_state) is now structurally legal, for
    every state including terminal ones. It adds NO new arc between
    distinct states — the existing ALLOWED_TRANSITIONS graph is unchanged.

    A self-transition is a RE-PIN: the family's state does not change, but
    record.append_transition() requires a non-NULL justifying_run_id for
    it — enforced there, not here, because this function has no visibility
    into justifying_run_id. can_transition() only answers "is this arc
    shape legal"; append_transition() answers "is this specific write
    legal", which includes the re-pin evidence requirement.
    """
    if from_state == to_state:
        return True
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

def check_validated_independence(
    family_id: str,
    family_meta: dict,
    thresholds: dict,
    pairwise_rows: dict,
    validated_peers: set[str] | frozenset[str],
) -> tuple[bool, list[str]]:
    """
    Proposal 1 (CLAUDE.md §16): pairwise independence at the Validated gate.

    Enforces the already-frozen `max_pairwise_r` threshold. Introduces no new
    governance rule -- the threshold has been declared in thresholds.yaml since
    2026-06-13 and was, until now, enforced nowhere.

    `pairwise_rows` is `record.get_pairwise_r()` output (latest row per
    canonical pair). `validated_peers` is the set of family ids currently in
    the Validated state, EXCLUDING `family_id` itself.

    Returns (ok, [errors]).

    Three ways a family fails, all fail-closed:

      1. **Missing measurement.** A Validated peer with no recorded pair is a
         failure, not a pass. This follows R1's precedent exactly -- "NULL or
         NaN for either governance stat is a hard failure; there is no silent
         bypass" -- because the alternative is not enforcement at all: a rule
         that applies only when someone volunteers the data can always be
         satisfied by volunteering nothing.
      2. **Exceeds the ceiling.** |r| > max_pairwise_r.
      3. **Unusable value.** NaN reaching the comparison.

    Correlation is compared as |r|. `max_pairwise_r`'s stated purpose is that
    a "signal must be independent of other validated signals", and a family at
    r = -0.95 to an existing Validated family is a near-perfect inverse
    duplicate carrying the same risk factor with the sign flipped -- it is not
    independent in any sense the threshold is protecting. Comparing raw r would
    admit exactly that case. This is the standard reading of a correlation
    ceiling, not a change of statistical methodology.
    """
    errors: list[str] = []
    t_validated = thresholds.get("validated", {})
    roles = _roles(family_meta)

    ceilings = [
        t_validated[role]["max_pairwise_r"]
        for role in roles
        if role in t_validated and "max_pairwise_r" in t_validated[role]
    ]
    if not ceilings:
        # No ceiling declared for any of this family's roles => nothing to
        # enforce. Threshold-driven, exactly like min_dsr_prob / max_bh_p.
        return True, []
    max_r = min(ceilings)  # most conservative across roles

    for peer in sorted(validated_peers):
        if peer == family_id:
            continue
        key = (family_id, peer) if family_id < peer else (peer, family_id)
        row = pairwise_rows.get(key)

        if row is None:
            errors.append(
                f"no pairwise correlation recorded against Validated family "
                f"'{peer}' — compute it and record it with "
                f"record.append_pairwise_r() before promoting "
                f"(thresholds.yaml validated.<role>.max_pairwise_r={max_r})"
            )
            continue

        r = row["r"]
        if r is None or math.isnan(float(r)):
            errors.append(
                f"pairwise correlation against '{peer}' is NaN (row id={row['id']}) "
                "— not a measurement; recompute and re-record"
            )
            continue

        if abs(float(r)) > max_r:
            errors.append(
                f"pairwise |r|={abs(float(r)):.4f} against Validated family "
                f"'{peer}' exceeds max_pairwise_r={max_r} "
                f"(measured {row['ts']} via {row['method']}, row id={row['id']})"
            )

    return len(errors) == 0, errors


def check_validated_preconditions(
    family_meta: dict,
    pinned_run: dict,
    thresholds: dict,
    pairwise_rows: dict | None = None,
    validated_peers: set[str] | frozenset[str] | None = None,
    family_id: str | None = None,
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

    # ── Pairwise independence (Proposal 1) ────────────────────────────────────
    # Evaluated only when the caller supplies the peer set. `None` means "this
    # caller cannot determine the Validated peers" -- not "there are none" --
    # so the check is skipped rather than silently passed on an empty set,
    # which would be a fail-open bypass. Every caller that CAN determine the
    # peer set passes it; see validate_registry() and validate_transition().
    if validated_peers is not None:
        indep_ok, indep_errs = check_validated_independence(
            family_id or family_meta.get("family_id") or "<unknown>",
            family_meta,
            thresholds,
            pairwise_rows or {},
            validated_peers,
        )
        errors.extend(indep_errs)

    return len(errors) == 0, errors


# ---------------------------------------------------------------------------
# Registry consistency validator
# ---------------------------------------------------------------------------

def validate_registry(
    registry: dict,
    ledger_db_path: Path | str,
    thresholds: dict,
    as_of: str | None = None,
) -> list[str]:
    """
    Check that every state in registry.yaml is supported by evidence in ledger.db.
    Returns a list of error strings (empty = clean).

    PR-5 (evidence_reporting_architecture.md §8) adds invariants C1-C9,
    appended after the pre-existing checks below (unchanged). `as_of`, if
    given, must already be a fully-qualified ISO datetime (report.py
    normalises a bare date before calling this).

    C4/C5/C8/C9 compare against `registry` as passed in -- there is only
    ever one copy of registry.yaml (it is not versioned per-date), so those
    four invariants are always evaluated against the CURRENT registry
    regardless of `as_of`. The remaining invariants (C1/C2/C3/C6/C7) are
    pure ledger-internal checks and DO respect `as_of` -- "was the ledger
    internally consistent as of that date" is a meaningful question; "does
    today's registry.yaml match a past ledger state" is not, and this
    function does not pretend otherwise.
    """
    import record

    errors: list[str] = []

    # Proposal 1 substrate, loaded once for the whole pass rather than per
    # family. `as_of` is honoured so a historical check evaluates the
    # independence evidence that existed then, not evidence recorded later.
    pairwise_rows = record.get_pairwise_r(ledger_db_path, as_of=as_of)
    # Peers come from the REGISTRY here, because that is what this function
    # validates: if registry.yaml claims two families are simultaneously
    # Validated, they must be independent of each other. (validate_transition()
    # instead derives peers from the ledger -- see its own note. The two
    # sources answer different questions and are deliberately not unified.)
    registry_validated = {
        fid for fid, f in registry.items() if f.get("state") == "validated"
    }

    for fam_id, fam in registry.items():
        state = fam.get("state", "discovery")

        if state not in VALID_STATES:
            errors.append(f"{fam_id}: unknown state '{state}'")
            continue

        if state in STATES_REQUIRING_RUN:
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
                prec_ok, prec_errs = check_validated_preconditions(
                    fam, run, thresholds,
                    pairwise_rows=pairwise_rows,
                    validated_peers=registry_validated - {fam_id},
                    family_id=fam_id,
                )
                for e in prec_errs:
                    errors.append(f"{fam_id}: {e}")

        claimed_state = fam.get("state")
        claimed_superseded_by = fam.get("superseded_by")
        if claimed_superseded_by and claimed_state not in ("retired",):
            errors.append(
                f"{fam_id}: superseded_by is set but state is '{claimed_state}' — "
                "a superseded family must be in 'retired' state"
            )

    errors += _check_invariants_c1_to_c9(registry, ledger_db_path, as_of)
    return errors


# ---------------------------------------------------------------------------
# Invariants C1-C9 (evidence_reporting_architecture.md §8)
# ---------------------------------------------------------------------------

def _check_invariants_c1_to_c9(
    registry: dict,
    ledger_db_path: Path | str,
    as_of: str | None,
) -> list[str]:
    import sqlite3
    import record

    errors: list[str] = []
    con = sqlite3.connect(ledger_db_path)
    con.row_factory = sqlite3.Row
    try:
        run_filter = " WHERE run_ts <= ?" if as_of is not None else ""
        run_params = (as_of,) if as_of is not None else ()
        runs = con.execute(f"SELECT * FROM runs{run_filter}", run_params).fetchall()

        # C1 — every run has a valid run_kind, and (provenance, run_kind) is a legal lane.
        for r in runs:
            rk = r["run_kind"]
            if rk not in record._VALID_RUN_KIND:
                errors.append(
                    f"C1: run '{r['run_id'][:8]}' ({r['family_id']}) has invalid run_kind "
                    f"{rk!r} — must be one of {sorted(record._VALID_RUN_KIND)}"
                )
                continue
            if (r["provenance"], rk) not in record._VALID_LANE_PAIRS:
                errors.append(
                    f"C1: run '{r['run_id'][:8]}' ({r['family_id']}) has an illegal lane "
                    f"(provenance={r['provenance']!r}, run_kind={rk!r})"
                )

        # C2 — exactly one current run per (family, lane); asserts the tiebreak is total.
        dupe_filter = " WHERE run_ts <= ?" if as_of is not None else ""
        c2_sql = f"""
            SELECT family_id, provenance, run_kind, COUNT(*) AS n FROM (
                SELECT r.*, ROW_NUMBER() OVER (
                    PARTITION BY family_id, provenance, run_kind
                    ORDER BY run_ts DESC, run_id DESC
                ) AS rn
                FROM runs r{dupe_filter}
            ) WHERE rn = 1
            GROUP BY family_id, provenance, run_kind
            HAVING COUNT(*) > 1
        """
        for row in con.execute(c2_sql, run_params).fetchall():
            errors.append(
                f"C2: {row['n']} current runs found for "
                f"({row['family_id']}, {row['provenance']}/{row['run_kind']}) — tiebreak is not total"
            )

        # C3 — at most one L0/discovery run per family.
        c3_sql = f"SELECT family_id, COUNT(*) AS n FROM runs{run_filter} " \
                 f"{'AND' if run_filter else 'WHERE'} run_kind = 'discovery' GROUP BY family_id HAVING COUNT(*) > 1"
        for row in con.execute(c3_sql, run_params).fetchall():
            errors.append(f"C3: {row['family_id']} has {row['n']} L0/discovery runs — at most one is allowed")

        # C6 — every transition into a run-requiring state has a non-NULL
        # justifying_run_id that exists in runs.
        t_filter = " WHERE ts <= ?" if as_of is not None else ""
        t_params = (as_of,) if as_of is not None else ()
        transitions = con.execute(f"SELECT * FROM state_transitions{t_filter}", t_params).fetchall()
        run_ids = {r["run_id"] for r in runs}
        for t in transitions:
            if t["to_state"] in STATES_REQUIRING_RUN:
                jrid = t["justifying_run_id"]
                if not jrid:
                    errors.append(
                        f"C6: transition #{t['id']} ({t['family_id']} -> {t['to_state']}) "
                        f"has no justifying_run_id"
                    )
                elif jrid not in run_ids:
                    errors.append(
                        f"C6: transition #{t['id']} ({t['family_id']} -> {t['to_state']}) "
                        f"justifying_run_id '{jrid[:8]}' does not exist in runs"
                    )

        # C7 — state=validated => pinned run's lane in {L2, L3} (forward/production).
        derived = record.derive_all_states_at(ledger_db_path, as_of)
        for fam_id, (state, pinned_run_id, _ts) in derived.items():
            if state != "validated":
                continue
            if not pinned_run_id:
                errors.append(f"C7: {fam_id} is validated with no pinned run")
                continue
            run = next((r for r in runs if r["run_id"] == pinned_run_id), None)
            if run is None:
                errors.append(f"C7: {fam_id} validated pinned run '{pinned_run_id[:8]}' not found (as-of window)")
                continue
            if run["run_kind"] not in ("forward", "production"):
                errors.append(
                    f"C7: {fam_id} is validated but pinned run is lane "
                    f"{run['provenance']}/{run['run_kind']} — must be L2 or L3"
                )
    finally:
        con.close()

    # C4/C5/C8/C9 — always evaluated against the CURRENT registry (see docstring).
    current_derived = record.derive_all_states_at(ledger_db_path, as_of=None)
    transition_family_ids = {row[0] for row in _all_transition_family_ids(ledger_db_path)}

    for fam_id, fam in registry.items():
        has_transitions = fam_id in current_derived
        derived_state, derived_pinned, _ts = current_derived.get(fam_id, ("discovery", None, None))
        registry_state = fam.get("state", "discovery")

        # C4 — registry.yaml: state == state derived from state_transitions.
        if registry_state != derived_state:
            errors.append(
                f"C4: {fam_id} registry.yaml state={registry_state!r} but ledger-derived "
                f"state={derived_state!r} — hand-edit bypassing the ledger?"
            )

        # C5 — registry.yaml: pinned_run == justifying_run_id of the latest transition.
        # Only meaningful once the ledger has an opinion, i.e. the family has at
        # least one transition. A pure-'discovery' family's registry.yaml
        # `pinned_run` (if set) records which run classified/evaluated it -- not
        # a governance decision the ledger tracks -- and is not what this
        # invariant polices; comparing it against a non-existent "latest
        # transition" would be a false positive, not a detected re-pin gap.
        if has_transitions:
            registry_pinned = fam.get("pinned_run")
            if (registry_pinned or None) != (derived_pinned or None):
                errors.append(
                    f"C5: {fam_id} registry.yaml pinned_run={registry_pinned!r} but ledger-derived "
                    f"pinned_run={derived_pinned!r} — a re-pin that was never recorded?"
                )

        # C8 — no family has registry.yaml: provenance set (deleted field).
        if "provenance" in fam:
            errors.append(
                f"C8: {fam_id} has registry.yaml 'provenance' set — this field is deleted "
                f"(denormalised copy of a per-run property); remove it"
            )

        # C9b — every family with a non-default state must appear in state_transitions.
        if registry_state != "discovery" and fam_id not in transition_family_ids:
            errors.append(
                f"C9: {fam_id} has state={registry_state!r} in registry.yaml but no "
                f"state_transitions row exists for it"
            )

    # C9a — every family_id in state_transitions must exist in registry.yaml.
    for fam_id in transition_family_ids:
        if fam_id not in registry:
            errors.append(f"C9: state_transitions references family_id={fam_id!r}, not present in registry.yaml")

    return errors


def _all_transition_family_ids(ledger_db_path: Path | str) -> list[tuple[str]]:
    import sqlite3
    con = sqlite3.connect(ledger_db_path)
    try:
        return con.execute("SELECT DISTINCT family_id FROM state_transitions").fetchall()
    finally:
        con.close()


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
