"""
Evidence Library — report generator and registry consistency validator.

Usage:
    python report.py                   # generate all reports to evidence_library/
    python report.py --check           # validate registry vs ledger (no reports)
    python report.py --out /some/path  # write reports to a different directory
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

_THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS_DIR))

import record
import lifecycle


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _load_registry(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _load_thresholds(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ---------------------------------------------------------------------------
# State ordering for reports (most-advanced first)
# ---------------------------------------------------------------------------

_STATE_ORDER = [
    "production_ready", "promotion_ready", "validated",
    "candidate", "discovery", "insufficient_sample",
    "rejected", "disqualified", "retired",
]

def _state_rank(state: str) -> int:
    try:
        return _STATE_ORDER.index(state)
    except ValueError:
        return len(_STATE_ORDER)


def _sort_registry(registry: dict) -> list[tuple[str, dict]]:
    return sorted(
        registry.items(),
        key=lambda kv: (_state_rank(kv[1].get("state", "discovery")),
                        -abs(float(kv[1].get("_sort_sharpe", 0) or 0)),
                        kv[0]),
    )


# ---------------------------------------------------------------------------
# Report: state_board.md
# ---------------------------------------------------------------------------

def generate_state_board(
    registry: dict,
    runs: dict[str, dict],
    out_path: Path,
) -> None:
    lines = [
        "# Evidence Library — State Board",
        f"_Generated {_now()}_",
        "",
        "| Family | Role | State | Provenance | Sharpe | OOS Sh | DSR | n | Evidence | Verdict | Notes |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    for fid, fam in _sort_registry(registry):
        run = runs.get(fid, {})
        roles = _role_str(fam)
        state = fam.get("state", "discovery")
        prov = run.get("provenance", "-") if run else "-"
        sharpe = _fmt_f(run.get("sharpe"), "+.2f") if run else "-"
        oos = _fmt_f(run.get("oos_sharpe"), "+.2f") if run else "-"
        dsr = _fmt_f(run.get("dsr_prob"), ".2f") if run else "-"
        n = str(run.get("n_trades") or "-") if run else "-"
        ev = _fmt_f(run.get("evidence_score"), ".1f") if run else "-"
        verdict = run.get("verdict", "-") if run else "-"
        notes = _first_weakness(fam)
        lines.append(
            f"| {fid} | {roles} | **{state}** | {prov} | "
            f"{sharpe} | {oos} | {dsr} | {n} | {ev} | {verdict} | {notes} |"
        )

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  state_board.md → {out_path}")


# ---------------------------------------------------------------------------
# Report: library.md
# ---------------------------------------------------------------------------

def generate_library(
    registry: dict,
    runs: dict[str, dict],
    out_path: Path,
) -> None:
    lines = [
        "# Evidence Library",
        f"_Generated {_now()}_",
        f"_Families: {len(registry)}_",
        "",
    ]

    for fid, fam in _sort_registry(registry):
        run = runs.get(fid, {})
        state = fam.get("state", "discovery")
        roles = _role_str(fam)

        lines += [
            f"## {fid}",
            "",
            f"**Display name:** {fam.get('display_name', fid)}  ",
            f"**State:** {state} (since {fam.get('state_since', '?')})  ",
            f"**Role:** {roles}  ",
            f"**Group:** {fam.get('group', '-')}  ",
            f"**Hypothesis:** {fam.get('hypothesis', '-')}  ",
            f"**Mechanism:** {fam.get('mechanism', '-')}  ",
            f"**Data source:** {fam.get('data_source', '-')}  ",
            "",
        ]

        if run:
            prov = run.get("provenance", "-")
            sharpe = _fmt_f(run.get("sharpe"), "+.2f")
            oos = _fmt_f(run.get("oos_sharpe"), "+.2f")
            dsr = _fmt_f(run.get("dsr_prob"), ".2f")
            ev = _fmt_f(run.get("evidence_score"), ".1f")
            verdict = run.get("verdict", "-")
            n = run.get("n_trades") or "-"
            lines += [
                f"**Pinned run:** `{fam.get('pinned_run') or 'null'}` ({prov})  ",
                f"**Metrics:** Sharpe {sharpe} | OOS {oos} | DSR {dsr} | "
                f"Evidence {ev} | Verdict {verdict} | N={n}  ",
                "",
            ]

        state_reason = fam.get("state_reason")
        if state_reason:
            lines.append(f"**State reason:** {str(state_reason).strip()}  ")
            lines.append("")

        weaknesses = fam.get("weaknesses", [])
        if weaknesses:
            lines.append("**Weaknesses:**")
            for w in weaknesses:
                lines.append(f"- {w}")
            lines.append("")

        dq = fam.get("dq_reasons", [])
        if dq:
            lines.append("**DQ reasons:**")
            for d in dq:
                lines.append(f"- {d}")
            lines.append("")

        if fam.get("preregistration"):
            pr = fam["preregistration"]
            lines.append(f"**Pre-registration:** `{pr.get('path', '-')}` (hash `{str(pr.get('hash', '-'))[:16]}...`)  ")
            lines.append("")

        if fam.get("superseded_by"):
            lines.append(f"**Superseded by:** {fam['superseded_by']}  ")
            lines.append("")

        lines += ["---", ""]

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  library.md → {out_path}")


# ---------------------------------------------------------------------------
# Report: ml_readiness.md
# ---------------------------------------------------------------------------

def generate_ml_readiness(
    registry: dict,
    runs: dict[str, dict],
    thresholds: dict,
    out_path: Path,
) -> None:
    ml_t = thresholds.get("ml_readiness", {})
    low = ml_t.get("target_low", 10)
    high = ml_t.get("target_high", 20)

    validated = [
        (fid, fam) for fid, fam in registry.items()
        if fam.get("state") == "validated"
    ]
    n_v = len(validated)
    candidates = [
        (fid, fam) for fid, fam in registry.items()
        if fam.get("state") == "candidate"
    ]

    if n_v < low:
        status = f"BELOW THRESHOLD — {n_v}/{low} validated families"
    elif n_v <= high:
        status = f"IN RANGE — {n_v} validated families (target {low}–{high}); ML review eligible"
    else:
        status = f"ABOVE RANGE — {n_v} validated families; ML review required"

    lines = [
        "# ML-Readiness Dashboard",
        f"_Generated {_now()}_",
        "",
        f"**Validated families:** {n_v}",
        f"**Target band:** {low}–{high}",
        f"**Status:** {status}",
        "",
        f"**Candidate families (forward collection in progress):** {len(candidates)}",
        "",
    ]

    if validated:
        lines += [
            "## Validated signal families (ML input set)",
            "",
            "| Family | Role | Provenance | Sharpe | OOS Sh | DSR | n | Evidence |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for fid, fam in sorted(validated, key=lambda kv: kv[0]):
            run = runs.get(fid, {})
            roles = _role_str(fam)
            prov = run.get("provenance", "-") if run else "-"
            sharpe = _fmt_f(run.get("sharpe"), "+.2f") if run else "-"
            oos = _fmt_f(run.get("oos_sharpe"), "+.2f") if run else "-"
            dsr = _fmt_f(run.get("dsr_prob"), ".2f") if run else "-"
            n = str(run.get("n_trades") or "-") if run else "-"
            ev = _fmt_f(run.get("evidence_score"), ".1f") if run else "-"
            lines.append(
                f"| {fid} | {roles} | {prov} | {sharpe} | {oos} | {dsr} | {n} | {ev} |"
            )
        lines.append("")
    else:
        lines += [
            "_No validated families yet._",
            "",
            "_Forward evidence collection is in progress for candidate families._",
            "",
        ]

    if candidates:
        lines += [
            "## Candidate families (pending Validated gate)",
            "",
            "| Family | Role | State reason (brief) |",
            "|---|---|---|",
        ]
        for fid, fam in sorted(candidates, key=lambda kv: kv[0]):
            roles = _role_str(fam)
            reason = str(fam.get("state_reason") or "").strip().split("\n")[0][:80]
            lines.append(f"| {fid} | {roles} | {reason} |")
        lines.append("")

    lines += [
        "## Governance",
        "",
        f"- ML-readiness review is triggered when **Validated** family count reaches {low}.",
        "- Review consumes the **Validated** set (edge + feature roles).",
        "- Promotion-ready and Production-ready are NOT required for ML review.",
        "- After ML review, model must beat rule-based baseline OOS before deployment.",
    ]

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  ml_readiness.md → {out_path}")


# ---------------------------------------------------------------------------
# Report: transitions.md
# ---------------------------------------------------------------------------

def generate_transitions(db_path: Path, out_path: Path) -> None:
    transitions = record.get_all_transitions(db_path)

    lines = [
        "# Evidence Library — State Transition Audit Log",
        f"_Generated {_now()}_",
        f"_Total transitions: {len(transitions)}_",
        "",
        "| Timestamp | Family | From | To | Actor | Reason | Run |",
        "|---|---|---|---|---|---|---|",
    ]

    for t in transitions:
        ts = t.get("ts", "")[:19]
        run_short = (t.get("justifying_run_id") or "-")[:8]
        reason = (t.get("reason") or "-")[:60]
        lines.append(
            f"| {ts} | {t['family_id']} | {t['from_state']} | "
            f"{t['to_state']} | {t.get('actor', '-')} | {reason} | `{run_short}` |"
        )

    if not transitions:
        lines.append("| — | — | — | — | — | — | — |")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  transitions.md → {out_path}")


# ---------------------------------------------------------------------------
# Registry consistency check
# ---------------------------------------------------------------------------

def run_check(
    registry_path: Path,
    ledger_path: Path,
    thresholds_path: Path,
) -> int:
    registry = _load_registry(registry_path)
    thresholds = _load_thresholds(thresholds_path)
    errors = lifecycle.validate_registry(registry, ledger_path, thresholds)

    if errors:
        print(f"REGISTRY CHECK FAILED — {len(errors)} error(s):")
        for e in errors:
            print(f"  ✗ {e}")
        return 1

    families_by_state: dict[str, list[str]] = {}
    for fid, fam in registry.items():
        s = fam.get("state", "discovery")
        families_by_state.setdefault(s, []).append(fid)

    print(f"Registry check: OK")
    print(f"  Total families: {len(registry)}")
    for state in _STATE_ORDER:
        if state in families_by_state:
            print(f"  {state}: {len(families_by_state[state])}")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate Evidence Library reports or validate registry consistency."
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Validate registry vs ledger only; exit non-zero on errors",
    )
    parser.add_argument(
        "--out", default=str(_THIS_DIR),
        help="Output directory for reports (default: evidence_library/)",
    )
    args = parser.parse_args(argv)

    registry_path   = _THIS_DIR / "registry.yaml"
    thresholds_path = _THIS_DIR / "thresholds.yaml"
    ledger_path     = _THIS_DIR / "ledger.db"
    out_dir         = Path(args.out)

    if args.check:
        return run_check(registry_path, ledger_path, thresholds_path)

    out_dir.mkdir(parents=True, exist_ok=True)
    registry   = _load_registry(registry_path)
    thresholds = _load_thresholds(thresholds_path)
    runs       = record.get_all_latest_runs(ledger_path)

    print(f"Generating reports → {out_dir}")
    generate_state_board(registry, runs, out_dir / "state_board.md")
    generate_library(registry, runs, out_dir / "library.md")
    generate_ml_readiness(registry, runs, thresholds, out_dir / "ml_readiness.md")
    generate_transitions(ledger_path, out_dir / "transitions.md")
    print("Done.")
    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _role_str(fam: dict) -> str:
    r = fam.get("role", [])
    if isinstance(r, str):
        return r
    return ", ".join(r) if r else "-"


def _first_weakness(fam: dict) -> str:
    w = fam.get("weaknesses", [])
    if not w:
        return fam.get("dq_reasons", ["-"])[0] if fam.get("dq_reasons") else "-"
    return w[0][:60] if w else "-"


def _fmt_f(v: Any, fmt: str) -> str:
    if v is None:
        return "-"
    try:
        return format(float(v), fmt)
    except (TypeError, ValueError):
        return "-"


if __name__ == "__main__":
    sys.exit(main())
