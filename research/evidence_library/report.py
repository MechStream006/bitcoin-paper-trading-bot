"""
Evidence Library — report generator and registry consistency validator.

PR-5 (docs/governance/evidence_reporting_architecture.md): the Evidence
Matrix with a Pinned-Decision Anchor. Governance state is derived from the
append-only `state_transitions` ledger, never read from registry.yaml
directly; `registry.yaml` is a cache, validated against the ledger (see
`run_check` / `lifecycle.validate_registry`, invariants C1-C9). Every
report accepts `--as-of`, reconstructing the exact board a reviewer would
have seen on any past date, from the ledger alone.

Usage:
    python report.py                          # generate all reports to evidence_library/
    python report.py --as-of 2026-06-14       # reconstruct the board as of that date
    python report.py --check                  # validate registry vs ledger (no reports)
    python report.py --check --as-of 2026-06-14
    python report.py --out /some/path         # write reports to a different directory
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

_THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS_DIR))

import record
import lifecycle

# ---------------------------------------------------------------------------
# Constants (evidence_reporting_architecture.md §5.4)
# ---------------------------------------------------------------------------

MIN_TRADES_FOR_SHARPE = 2
FWD_STALE_DAYS = 3
REPRO_DRIFT_THRESHOLD = 0.10

NO_RUN = "—"  # rule 1: renders ONLY when no run exists in the lane

LANE_ORDER = ["discovery", "confirmatory", "forward", "production"]
LANE_TITLE = {
    "discovery": "L0 Discovery",
    "confirmatory": "L1 Confirmatory",
    "forward": "L2 Forward",
    "production": "L3 Production",
}


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _load_registry(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_thresholds(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def normalize_as_of(as_of: str | None) -> str | None:
    """
    A bare 'YYYY-MM-DD' is normalised to end-of-day UTC so that same-day
    events (e.g. the 2026-06-14 re-seed) are included -- lexicographic
    comparison against a bare date string would otherwise exclude every
    timestamp on that date, since "2026-06-14T09:37:56" > "2026-06-14" is
    a longer-string comparison, not an inclusive same-day one. An
    already-qualified datetime (contains 'T') passes through unchanged.
    None (no --as-of given) means "now" -- no filter is applied anywhere.
    """
    if as_of is None:
        return None
    if "T" not in as_of:
        return f"{as_of}T23:59:59.999999+00:00"
    return as_of


def _as_of_display(as_of: str | None) -> str:
    return as_of[:10] if as_of else datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _as_of_datetime(as_of: str | None) -> datetime:
    if as_of is None:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(as_of)


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


def _sort_families(fids: list[str], row_state: dict[str, str]) -> list[str]:
    """
    Rule 5: sort by state rank, then family_id. NEVER by a metric a nightly
    job can change -- a board whose row order changes overnight cannot be
    diffed by a human. (The old `_sort_sharpe` secondary key is removed:
    it was always a no-op in practice -- the field does not exist in
    registry.yaml -- and keeping a metric-shaped key in the sort tuple is
    exactly the kind of thing that quietly becomes load-bearing later.)
    """
    return sorted(fids, key=lambda fid: (_state_rank(row_state.get(fid, "discovery")), fid))


# ---------------------------------------------------------------------------
# Report context — one pure snapshot of (ledger, registry, thresholds, as_of)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FamilyRow:
    family_id: str
    fam: dict
    state: str
    pinned_run_id: str | None
    state_ts: str | None
    pinned_run: dict | None
    pinned_run_kind: str | None
    pinned_lane_code: str | None
    lane_runs: dict[str, dict]          # {run_kind: run}
    is_pin_stale: bool
    flags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReportContext:
    registry: dict
    thresholds: dict
    as_of: str | None            # normalised (or None)
    as_of_dt: datetime
    n_runs: int
    n_transitions: int
    rows: dict[str, FamilyRow]   # family_id -> FamilyRow


def build_context(
    registry_path: Path,
    thresholds_path: Path,
    ledger_path: Path,
    as_of: str | None = None,
) -> ReportContext:
    """
    The single point where (ledger.db, registry.yaml, thresholds.yaml, as_of)
    become a pure, fully-derived snapshot. Every report generator consumes
    only this object -- none of them touch the ledger or registry.yaml
    directly. This is what makes the report a pure function of its inputs
    (acceptance criterion 4).
    """
    registry = _load_registry(registry_path)
    thresholds = _load_thresholds(thresholds_path)
    as_of_norm = normalize_as_of(as_of)
    as_of_dt = _as_of_datetime(as_of_norm)

    states = record.derive_all_states_at(ledger_path, as_of_norm)
    runs_by_lane = record.get_current_runs_by_lane(ledger_path, as_of_norm)

    all_runs = record.get_all_runs(ledger_path)
    n_runs = sum(1 for r in all_runs if as_of_norm is None or r["run_ts"] <= as_of_norm)
    all_transitions = record.get_all_transitions(ledger_path)
    n_transitions = sum(1 for t in all_transitions if as_of_norm is None or t["ts"] <= as_of_norm)

    rows: dict[str, FamilyRow] = {}
    for fid, fam in registry.items():
        state, pinned_run_id, state_ts = states.get(fid, ("discovery", None, None))
        pinned_run = record.get_run(ledger_path, pinned_run_id) if pinned_run_id else None
        pinned_run_kind = pinned_run["run_kind"] if pinned_run else None
        pinned_lane_code = record.LANE_CODE_BY_RUN_KIND.get(pinned_run_kind) if pinned_run_kind else None
        lane_runs = runs_by_lane.get(fid, {})

        current_in_pinned_lane = lane_runs.get(pinned_run_kind) if pinned_run_kind else None
        is_pin_stale = bool(
            pinned_run is not None
            and current_in_pinned_lane is not None
            and current_in_pinned_lane["run_id"] != pinned_run["run_id"]
        )

        row = FamilyRow(
            family_id=fid, fam=fam, state=state, pinned_run_id=pinned_run_id,
            state_ts=state_ts, pinned_run=pinned_run, pinned_run_kind=pinned_run_kind,
            pinned_lane_code=pinned_lane_code, lane_runs=lane_runs, is_pin_stale=is_pin_stale,
        )
        flags = compute_flags(row, as_of_dt)
        rows[fid] = FamilyRow(**{**row.__dict__, "flags": flags})

    return ReportContext(
        registry=registry, thresholds=thresholds, as_of=as_of_norm, as_of_dt=as_of_dt,
        n_runs=n_runs, n_transitions=n_transitions, rows=rows,
    )


# ---------------------------------------------------------------------------
# Flags (evidence_reporting_architecture.md §5.4) — pure function
# ---------------------------------------------------------------------------

def _sign(x: float) -> int:
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


def compute_flags(row: FamilyRow, as_of_dt: datetime) -> list[str]:
    """
    Pure function: (row data, as_of) -> flags. Flags never change a state;
    they direct human attention. Computed from the as-of snapshot only.
    """
    flags: list[str] = []

    if row.is_pin_stale:
        flags.append("PIN-STALE")

    l0 = row.lane_runs.get("discovery")
    l1 = row.lane_runs.get("confirmatory")
    l2 = row.lane_runs.get("forward")

    if l0 is not None and l1 is not None:
        l0_sharpe, l1_sharpe = l0.get("sharpe"), l1.get("sharpe")
        if l0_sharpe is not None and l1_sharpe is not None and abs(l1_sharpe - l0_sharpe) > REPRO_DRIFT_THRESHOLD:
            flags.append("REPRO-DRIFT")

    if l2 is not None:
        l2_sharpe = l2.get("sharpe")
        l2_n = l2.get("n_trades") or 0
        pinned_sharpe = row.pinned_run.get("sharpe") if row.pinned_run else None
        if (
            l2_sharpe is not None and pinned_sharpe is not None
            and l2_n >= MIN_TRADES_FOR_SHARPE
            and _sign(l2_sharpe) != 0 and _sign(pinned_sharpe) != 0
            and _sign(l2_sharpe) != _sign(pinned_sharpe)
        ):
            flags.append("FWD-CONTRA")

        if l2_n < MIN_TRADES_FOR_SHARPE:
            flags.append("FWD-THIN")

        l2_ts = l2.get("run_ts")
        if l2_ts and (as_of_dt - datetime.fromisoformat(l2_ts)).days > FWD_STALE_DAYS:
            flags.append("FWD-STALE")

        if row.state == "candidate" and l2.get("dsr_prob") is None:
            flags.append("GATE-BLOCKED")

    return flags


def _fmt_flags(flags: list[str]) -> str:
    return " ".join(f"⚠ {f}" for f in flags)


# ---------------------------------------------------------------------------
# Rendering primitives (§5.3, normative)
# ---------------------------------------------------------------------------

def _fmt_signed(v: float | None, null_reason: str, fmt: str = "+.2f") -> str:
    """Rule 2: a NULL metric on an EXISTING run renders its reason, never '-'."""
    if v is None:
        return null_reason
    return format(float(v), fmt)


def _fmt_l0_cell(run: dict | None) -> str:
    """L0: 'Sharpe / OOS / n'. Rule 1: '—' only when no run exists."""
    if run is None:
        return NO_RUN
    sharpe = _fmt_signed(run.get("sharpe"), "not-computed")
    oos = _fmt_signed(run.get("oos_sharpe"), "not-computed")
    n = run.get("n_trades")
    n_str = str(n) if n is not None else "not-computed"
    return f"{sharpe} / {oos} / {n_str}"


def _fmt_l1_cell(l1: dict | None, l0: dict | None) -> str:
    """L1: rendered as delta vs L0 (§3, L1), not absolute."""
    if l1 is None:
        return NO_RUN
    date = (l1.get("run_ts") or "")[:10] or "?"
    l1_sharpe, l0_sharpe = l1.get("sharpe"), (l0 or {}).get("sharpe")
    if l0 is None or l1_sharpe is None or l0_sharpe is None:
        return f"not-computed ({date})"
    delta = l1_sharpe - l0_sharpe
    return f"{delta:+.2f} ({date})"


def _fmt_l2l3_cell(run: dict | None, as_of_dt: datetime) -> str:
    """L2/L3: 'Sharpe / n / days'. Sharpe NULL -> 'insufficient' (n < 2, rule 2)."""
    if run is None:
        return NO_RUN
    sharpe = _fmt_signed(run.get("sharpe"), "insufficient")
    n = run.get("n_trades")
    n_str = str(n) if n is not None else "0"
    run_ts = run.get("run_ts")
    days = (as_of_dt - datetime.fromisoformat(run_ts)).days if run_ts else "?"
    return f"{sharpe} / {n_str} / {days}d"


def _fmt_pinned_sharpe(pinned_run: dict | None) -> str:
    if pinned_run is None:
        return NO_RUN
    if pinned_run.get("sharpe") is not None:
        return _fmt_signed(pinned_run.get("sharpe"), "not-computed")
    n = pinned_run.get("n_trades") or 0
    return "insufficient" if n < MIN_TRADES_FOR_SHARPE else "not-computed"


def _fmt_pinned_stat(pinned_run: dict | None, key: str) -> str:
    """DSR / BH-p on the decision anchor: never computable by a cron
    (deliberately NULL on every bridge-written run) -> 'not-computed', not '-'."""
    if pinned_run is None:
        return NO_RUN
    v = pinned_run.get(key)
    return _fmt_signed(v, "not-computed", fmt=".3f" if key == "dsr_prob" else ".4f")


def _role_str(fam: dict) -> str:
    r = fam.get("role", [])
    if isinstance(r, str):
        return r
    return ", ".join(r) if r else "-"


# ---------------------------------------------------------------------------
# Report: state_board.md — the Evidence Matrix
# ---------------------------------------------------------------------------

def generate_state_board(ctx: ReportContext, out_path: Path, generated_at: str | None = None) -> None:
    generated_at = generated_at or _now()
    lines = [
        "# Evidence Library — Governance Board",
        f"_Generated {generated_at} · as-of {_as_of_display(ctx.as_of)} · "
        f"ledger {ctx.n_runs} runs / {ctx.n_transitions} transitions_",
        "",
        "## Decision anchors",
        "_What each state RESTS ON — never automated._",
        "",
        "| Family | Role | State | Since | Decided on (pinned) | Lane | Sharpe | DSR | BH-p | Flags |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]

    anchored = [fid for fid, row in ctx.rows.items() if row.pinned_run_id is not None]
    for fid in _sort_families(anchored, {f: r.state for f, r in ctx.rows.items()}):
        row = ctx.rows[fid]
        since = (row.state_ts or "")[:10] or "?"
        pinned_short = (row.pinned_run_id or "")[:8]
        lane_code = row.pinned_lane_code or "?"
        sharpe = _fmt_pinned_sharpe(row.pinned_run)
        dsr = _fmt_pinned_stat(row.pinned_run, "dsr_prob")
        bh = _fmt_pinned_stat(row.pinned_run, "bh_p")
        lines.append(
            f"| {fid} | {_role_str(row.fam)} | **{row.state}** | {since} | "
            f"{pinned_short} | {lane_code} | {sharpe} | {dsr} | {bh} | {_fmt_flags(row.flags)} |"
        )

    lines += [
        "",
        "## Evidence by lane",
        "_What we CURRENTLY MEASURE, per lane, side by side. The nightly job can only ever change this table._",
        "",
        "| Family | L0 Discovery (Sharpe/OOS/n) | L1 Confirmatory (Δ vs L0) | "
        "L2 Forward (Sharpe/n/days) | L3 Production | Flags |",
        "|---|---|---|---|---|---|",
    ]

    all_fids = _sort_families(list(ctx.rows.keys()), {f: r.state for f, r in ctx.rows.items()})
    for fid in all_fids:
        row = ctx.rows[fid]
        l0 = row.lane_runs.get("discovery")
        l1 = row.lane_runs.get("confirmatory")
        l2 = row.lane_runs.get("forward")
        l3 = row.lane_runs.get("production")
        lines.append(
            f"| {fid} | {_fmt_l0_cell(l0)} | {_fmt_l1_cell(l1, l0)} | "
            f"{_fmt_l2l3_cell(l2, ctx.as_of_dt)} | {_fmt_l2l3_cell(l3, ctx.as_of_dt)} | {_fmt_flags(row.flags)} |"
        )

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  state_board.md -> {out_path}")


# ---------------------------------------------------------------------------
# Report: library.md — per-family deep drill-down
# ---------------------------------------------------------------------------

def generate_library(ctx: ReportContext, out_path: Path, generated_at: str | None = None) -> None:
    generated_at = generated_at or _now()
    lines = [
        "# Evidence Library",
        f"_Generated {generated_at} · as-of {_as_of_display(ctx.as_of)}_",
        f"_Families: {len(ctx.registry)}_",
        "",
    ]

    all_fids = _sort_families(list(ctx.rows.keys()), {f: r.state for f, r in ctx.rows.items()})
    for fid in all_fids:
        row = ctx.rows[fid]
        fam = row.fam

        lines += [
            f"## {fid}",
            "",
            f"**Display name:** {fam.get('display_name', fid)}  ",
            f"**State:** {row.state} (since {(row.state_ts or '?')[:10]})  ",
            f"**Role:** {_role_str(fam)}  ",
            f"**Group:** {fam.get('group', '-')}  ",
            f"**Hypothesis:** {fam.get('hypothesis', '-')}  ",
            f"**Mechanism:** {fam.get('mechanism', '-')}  ",
            f"**Data source:** {fam.get('data_source', '-')}  ",
            "",
        ]

        lines.append("### Decision anchor")
        lines.append("")
        if row.pinned_run is not None:
            lines += [
                f"**Pinned run:** `{row.pinned_run_id}` (lane {row.pinned_lane_code}, "
                f"{row.pinned_run.get('provenance')}/{row.pinned_run_kind})  ",
                f"**Pinned metrics:** Sharpe {_fmt_pinned_sharpe(row.pinned_run)} | "
                f"DSR {_fmt_pinned_stat(row.pinned_run, 'dsr_prob')} | "
                f"BH-p {_fmt_pinned_stat(row.pinned_run, 'bh_p')} | "
                f"N={row.pinned_run.get('n_trades') if row.pinned_run.get('n_trades') is not None else 'not-computed'}  ",
            ]
            if row.is_pin_stale:
                lines.append("**⚠ PIN-STALE** — the pinned run is not the current run in its own lane.  ")
        else:
            lines.append(f"**Pinned run:** {NO_RUN} (no governance decision has been made yet)  ")
        lines.append("")

        lines.append("### Evidence by lane")
        lines.append("")
        for run_kind in LANE_ORDER:
            run = row.lane_runs.get(run_kind)
            title = LANE_TITLE[run_kind]
            if run is None:
                lines.append(f"- **{title}:** {NO_RUN}")
                continue
            if run_kind == "confirmatory":
                l0 = row.lane_runs.get("discovery")
                lines.append(f"- **{title}:** {_fmt_l1_cell(run, l0)} (run `{run['run_id'][:8]}`)")
            elif run_kind in ("forward", "production"):
                lines.append(
                    f"- **{title}:** {_fmt_l2l3_cell(run, ctx.as_of_dt)} (run `{run['run_id'][:8]}`)"
                )
            else:
                lines.append(f"- **{title}:** {_fmt_l0_cell(run)} (run `{run['run_id'][:8]}`)")
        lines.append("")

        if row.flags:
            lines.append(f"**Flags:** {_fmt_flags(row.flags)}  ")
            lines.append("")

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
    print(f"  library.md -> {out_path}")


# ---------------------------------------------------------------------------
# Report: evidence_timeline.md — runs + transitions, chronological (NEW)
# ---------------------------------------------------------------------------

def generate_evidence_timeline(
    ctx: ReportContext,
    ledger_path: Path,
    out_path: Path,
    generated_at: str | None = None,
) -> None:
    generated_at = generated_at or _now()
    lines = [
        "# Evidence Library — Evidence Timeline",
        f"_Generated {generated_at} · as-of {_as_of_display(ctx.as_of)}_",
        "_The audit substrate underneath the board: every run and every transition, in order._",
        "",
    ]

    all_runs = record.get_all_runs(ledger_path)
    all_transitions = record.get_all_transitions(ledger_path)

    all_fids = _sort_families(list(ctx.rows.keys()), {f: r.state for f, r in ctx.rows.items()})
    for fid in all_fids:
        fam_runs = [r for r in all_runs if r["family_id"] == fid and (ctx.as_of is None or r["run_ts"] <= ctx.as_of)]
        fam_transitions = [
            t for t in all_transitions
            if t["family_id"] == fid and (ctx.as_of is None or t["ts"] <= ctx.as_of)
        ]
        if not fam_runs and not fam_transitions:
            continue

        events: list[tuple[str, str, dict]] = []
        for r in fam_runs:
            events.append((r["run_ts"], "run", r))
        for t in fam_transitions:
            events.append((t["ts"], "transition", t))
        events.sort(key=lambda e: e[0])

        lines.append(f"## {fid}")
        lines.append("")
        lines.append("| Timestamp | Event | Detail |")
        lines.append("|---|---|---|")
        for ts, kind, payload in events:
            if kind == "run":
                lane_code = record.LANE_CODE_BY_RUN_KIND.get(payload.get("run_kind"), "?")
                detail = (
                    f"run `{payload['run_id'][:8]}` [{lane_code} {payload.get('run_kind')}] "
                    f"sharpe={_fmt_signed(payload.get('sharpe'), 'not-computed')}"
                )
            else:
                run_short = (payload.get("justifying_run_id") or "")[:8] or "-"
                reason = (payload.get("reason") or "-")[:80]
                detail = (
                    f"{payload['from_state']} → {payload['to_state']} "
                    f"(actor={payload.get('actor', '-')}, run=`{run_short}`) — {reason}"
                )
            lines.append(f"| {ts[:19]} | {kind} | {detail} |")
        lines.append("")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  evidence_timeline.md -> {out_path}")


# ---------------------------------------------------------------------------
# Report: ml_readiness.md
# ---------------------------------------------------------------------------

def generate_ml_readiness(ctx: ReportContext, out_path: Path, generated_at: str | None = None) -> None:
    generated_at = generated_at or _now()
    ml_t = ctx.thresholds.get("ml_readiness", {})
    low = ml_t.get("target_low", 10)
    high = ml_t.get("target_high", 20)

    validated_fids = [fid for fid, row in ctx.rows.items() if row.state == "validated"]
    candidate_fids = [fid for fid, row in ctx.rows.items() if row.state == "candidate"]
    n_v = len(validated_fids)

    if n_v < low:
        status = f"BELOW THRESHOLD — {n_v}/{low} validated families"
    elif n_v <= high:
        status = f"IN RANGE — {n_v} validated families (target {low}–{high}); ML review eligible"
    else:
        status = f"ABOVE RANGE — {n_v} validated families; ML review required"

    lines = [
        "# ML-Readiness Dashboard",
        f"_Generated {generated_at} · as-of {_as_of_display(ctx.as_of)}_",
        "",
        f"**Validated families:** {n_v}",
        f"**Target band:** {low}–{high}",
        f"**Status:** {status}",
        "",
        f"**Candidate families (forward collection in progress):** {len(candidate_fids)}",
        "",
    ]

    if validated_fids:
        lines += [
            "## Validated signal families (ML input set)",
            "_Rendered on the L2/L3 lane explicitly — never the offline lane. It is",
            "structurally impossible to satisfy the ML gate with offline metrics._",
            "",
            "| Family | Role | Lane | Sharpe | n | Days | DSR | BH-p |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for fid in sorted(validated_fids):
            row = ctx.rows[fid]
            l2 = row.lane_runs.get("forward")
            l3 = row.lane_runs.get("production")
            live = l3 or l2
            lane_code = record.LANE_CODE_BY_RUN_KIND.get(live["run_kind"]) if live else NO_RUN
            sharpe = _fmt_l2l3_cell(live, ctx.as_of_dt).split(" / ")[0] if live else NO_RUN
            n = str(live.get("n_trades")) if live and live.get("n_trades") is not None else NO_RUN
            days = _fmt_l2l3_cell(live, ctx.as_of_dt).split(" / ")[-1] if live else NO_RUN
            dsr = _fmt_pinned_stat(row.pinned_run, "dsr_prob")
            bh = _fmt_pinned_stat(row.pinned_run, "bh_p")
            lines.append(f"| {fid} | {_role_str(row.fam)} | {lane_code} | {sharpe} | {n} | {days} | {dsr} | {bh} |")
        lines.append("")
    else:
        lines += [
            "_No validated families yet._",
            "",
            "_Forward evidence collection is in progress for candidate families._",
            "",
        ]

    if candidate_fids:
        lines += [
            "## Candidate families — blocked-on worklist",
            "_What is missing before each candidate can reach Validated._",
            "",
            "| Family | Role | Blocked on |",
            "|---|---|---|",
        ]
        for fid in sorted(candidate_fids):
            row = ctx.rows[fid]
            blocked_on = _blocked_on(row, ctx.thresholds)
            lines.append(f"| {fid} | {_role_str(row.fam)} | {blocked_on} |")
        lines.append("")

    lines += [
        "## Governance",
        "",
        f"- ML-readiness review is triggered when **Validated** family count reaches {low}.",
        "- Review consumes the **Validated** set (edge + feature roles), rendered on L2/L3 only.",
        "- Promotion-ready and Production-ready are NOT required for ML review.",
        "- After ML review, model must beat rule-based baseline OOS before deployment.",
    ]

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  ml_readiness.md -> {out_path}")


def _blocked_on(row: FamilyRow, thresholds: dict) -> str:
    """§5.5: enumerate exactly which Validated precondition is missing.
    Reuses lifecycle.check_validated_preconditions() -- not reimplemented."""
    if row.pinned_run is None:
        return "no pinned run"
    ok, errors = lifecycle.check_validated_preconditions(row.fam, row.pinned_run, thresholds)
    if ok:
        return "nothing — eligible for Validated review"
    return "; ".join(errors)


# ---------------------------------------------------------------------------
# Report: transitions.md
# ---------------------------------------------------------------------------

def generate_transitions(ctx: ReportContext, ledger_path: Path, out_path: Path, generated_at: str | None = None) -> None:
    generated_at = generated_at or _now()
    transitions = [
        t for t in record.get_all_transitions(ledger_path)
        if ctx.as_of is None or t["ts"] <= ctx.as_of
    ]

    lines = [
        "# Evidence Library — State Transition Audit Log",
        f"_Generated {generated_at} · as-of {_as_of_display(ctx.as_of)}_",
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
    print(f"  transitions.md -> {out_path}")


# ---------------------------------------------------------------------------
# Registry consistency check (invariants C1-C9 — see lifecycle.validate_registry)
# ---------------------------------------------------------------------------

def run_check(
    registry_path: Path,
    ledger_path: Path,
    thresholds_path: Path,
    as_of: str | None = None,
    check_evidence_governance: bool = True,
) -> int:
    registry = _load_registry(registry_path)
    thresholds = _load_thresholds(thresholds_path)
    as_of_norm = normalize_as_of(as_of)
    errors = lifecycle.validate_registry(registry, ledger_path, thresholds, as_of=as_of_norm)

    # PR-6 (evidence_governance.py): additive hook, appended after C1-C9 --
    # never folded into or renumbering them. "EG"-prefixed errors are a
    # SEPARATE subsystem (governance receipts for evidence admission/
    # rejection), not a Core Evidence Library invariant. This check is
    # inherently "now"-only (governance_receipts has no as_of derivation
    # equivalent to state_transitions in this PR), so it is skipped for a
    # historical --check --as-of run rather than silently misapplied.
    if check_evidence_governance and as_of_norm is None:
        import evidence_governance
        # Idempotent schema-ensure (CREATE TABLE IF NOT EXISTS only, no ALTER)
        # -- report.py has never called init_db() before (it is a read-only
        # consumer of an already-seeded ledger.db); a ledger that predates
        # this PR, or a fresh test fixture, must not crash --check with
        # "no such table: governance_receipts" just because nothing has
        # written a receipt into it yet.
        evidence_governance.init_db(ledger_path)
        errors = errors + evidence_governance.validate_evidence_governance(ledger_path, registry)

    if errors:
        print(f"REGISTRY CHECK FAILED — {len(errors)} error(s):")
        for e in errors:
            print(f"  X {e}")
        return 1

    families_by_state: dict[str, list[str]] = {}
    for fid, fam in registry.items():
        s = fam.get("state", "discovery")
        families_by_state.setdefault(s, []).append(fid)

    print("Registry check: OK")
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
        "--as-of", default=None, dest="as_of",
        help="Reconstruct the board as of this date (YYYY-MM-DD) or datetime. Default: now.",
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
        return run_check(registry_path, ledger_path, thresholds_path, as_of=args.as_of)

    out_dir.mkdir(parents=True, exist_ok=True)
    ctx = build_context(registry_path, thresholds_path, ledger_path, as_of=args.as_of)

    print(f"Generating reports -> {out_dir} (as-of {_as_of_display(ctx.as_of)})")
    generate_state_board(ctx, out_dir / "state_board.md")
    generate_library(ctx, out_dir / "library.md")
    generate_evidence_timeline(ctx, ledger_path, out_dir / "evidence_timeline.md")
    generate_ml_readiness(ctx, out_dir / "ml_readiness.md")
    generate_transitions(ctx, ledger_path, out_dir / "transitions.md")
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
