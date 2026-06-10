"""
Phase B Evidence Discovery — orchestrator.

Usage:
    python run_evidence.py seeds group1 [group2 ...]

Runs every signal in the requested groups through the evidence engine,
prints a scoreboard, and updates the cumulative Evidence Library:
    reports/evidence_library/evidence_library.json   (machine-readable rows)
    reports/evidence_library/SCOREBOARD.md            (human scoreboard)
"""
from __future__ import annotations

import json
import pathlib
import sys
import time
from datetime import datetime, timezone

_THIS_DIR     = pathlib.Path(__file__).resolve().parent
_RESEARCH_DIR = _THIS_DIR.parent
for p in (str(_THIS_DIR), str(_RESEARCH_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from engine import load_master, evaluate_signal           # noqa: E402
from signals import REGISTRY                               # noqa: E402

REPORT_DIR = _RESEARCH_DIR / "reports" / "evidence_library"
LIB_JSON   = REPORT_DIR / "evidence_library.json"
SCOREBOARD = REPORT_DIR / "SCOREBOARD.md"

VERDICT_RANK = {"Strong": 3, "Moderate": 2, "Weak": 1, "Reject": 0}


def _fmt(v, fmt="+.2f", na="n/a"):
    try:
        if v is None or (isinstance(v, float) and (v != v)):
            return na
        return f"{v:{fmt}}"
    except (ValueError, TypeError):
        return na


def _print_row(r: dict) -> None:
    print(
        f"  {r['name'][:32]:32s}  "
        f"Sh={_fmt(r['sharpe'],'6.2f')}  "
        f"So={_fmt(r['sortino'],'6.2f')}  "
        f"Ca={_fmt(r['calmar'],'5.2f')}  "
        f"MDD={_fmt(r['max_dd_pct'],'5.1f')}%  "
        f"Win={_fmt(r['win_rate_pct'],'4.1f')}%  "
        f"PF={_fmt(r['profit_factor'],'5.2f')}  "
        f"N={r['n_trades']:>3}  "
        f"OOS={_fmt(r['oos_sharpe'],'+.2f')}  "
        f"Stab={_fmt(r['stability_score'],'.2f')}  "
        f"Ev={_fmt(r['evidence_score'],'5.1f')}  "
        f"[{r['verdict']}]"
    )


def _load_library() -> dict:
    if LIB_JSON.exists():
        with open(LIB_JSON) as fh:
            return json.load(fh)
    return {}


def _save_library(lib: dict) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    with open(LIB_JSON, "w") as fh:
        json.dump(lib, fh, indent=2)


def _render_scoreboard(lib: dict) -> None:
    rows = list(lib.values())
    rows.sort(key=lambda r: (VERDICT_RANK.get(r["verdict"], 0),
                             r["evidence_score"]), reverse=True)
    lines = []
    lines.append("# Phase B — Evidence Library Scoreboard\n")
    lines.append(f"_Last updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_  ")
    lines.append(f"_Signals evaluated: {len(rows)}_  ")
    lines.append("_Headline metrics at 15 bps cost. Scoring is robustness-weighted "
                 "(profitability is only 10/100)._\n")
    lines.append("| Signal | Group | CAGR | Sharpe | MaxDD | Win% | PF | N | OOS Sh | Stab | Evidence | Verdict |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        lines.append(
            f"| {r['name']} | {r['group']} | {_fmt(r['cagr_pct'],'+.1f')}% | "
            f"{_fmt(r['sharpe'],'+.2f')} | {_fmt(r['max_dd_pct'],'.1f')}% | "
            f"{_fmt(r['win_rate_pct'],'.1f')}% | {_fmt(r['profit_factor'],'.2f')} | "
            f"{r['n_trades']} | {_fmt(r['oos_sharpe'],'+.2f')} | "
            f"{_fmt(r['stability_score'],'.2f')} | {_fmt(r['evidence_score'],'.1f')} | "
            f"**{r['verdict']}** |"
        )
    lines.append("\n## Per-signal detail\n")
    for r in rows:
        lines.append(f"### {r['name']}  —  {r['verdict']} (evidence {_fmt(r['evidence_score'],'.1f')})\n")
        lines.append(f"- **Group:** {r['group']}")
        lines.append(f"- **Economic rationale:** {r['rationale']}")
        lines.append(f"- **Data source:** {r['data_source']}")
        lines.append(f"- **Feature definition:** {r['feature_def']}")
        lines.append(f"- **Metrics @15bps:** CAGR {_fmt(r['cagr_pct'],'+.1f')}%, "
                     f"Sharpe {_fmt(r['sharpe'],'+.2f')}, Sortino {_fmt(r['sortino'],'+.2f')}, "
                     f"Calmar {_fmt(r['calmar'],'+.2f')}, MaxDD {_fmt(r['max_dd_pct'],'.1f')}%, "
                     f"Win {_fmt(r['win_rate_pct'],'.1f')}%, PF {_fmt(r['profit_factor'],'.2f')}, "
                     f"Trades {r['n_trades']}")
        lines.append(f"- **In/Out-of-sample:** IS Sharpe {_fmt(r['is_sharpe'],'+.2f')}, "
                     f"OOS Sharpe {_fmt(r['oos_sharpe'],'+.2f')} "
                     f"(sign agree: {r['oos_is_agree']})")
        wf_str = ", ".join(f"{y}:{_fmt(s,'+.2f')}" for y, s in r['wf_folds'].items())
        lines.append(f"- **Walk-forward:** {r['wf_pass']} folds Sharpe>0  (per-year: {wf_str})")
        lines.append(f"- **Sensitivity:** {_fmt(r['sens_pass_frac']*100,'.0f')}% of variants sign-stable")
        reg_str = ", ".join(f"{k}:{_fmt(v,'+.2f')}" for k, v in r['regime_sharpe'].items())
        lines.append(f"- **Regime Sharpe:** {reg_str}")
        lines.append(f"- **Transaction cost:** break-even (Sharpe>0) up to {r['breakeven_cost_bps']} bps")
        lines.append(f"- **Structural-event robustness:** clean Sharpe "
                     f"{_fmt(r['clean_sharpe'],'+.2f')} (sign preserved: {r['struct_sign_ok']})")
        lines.append(f"- **Leave-one-year-out:** sign consistency "
                     f"{_fmt(r['loo_sign_consistency']*100,'.0f')}%, "
                     f"Sharpe CV {_fmt(r['loo_sharpe_cv'],'.2f')}")
        lines.append(f"- **Stability score:** {_fmt(r['stability_score'],'.2f')}  |  "
                     f"**Evidence score:** {_fmt(r['evidence_score'],'.1f')}/100")
        lines.append("")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    with open(SCOREBOARD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def main(groups: list[str]) -> None:
    t0 = time.time()
    print("=" * 110)
    print("PHASE B — EVIDENCE DISCOVERY PROGRAM")
    print(f"Groups: {groups}")
    print(f"Run date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 110)

    m = load_master()
    print(f"Master loaded: {len(m)} daily bars "
          f"({m['dt'].iloc[0].date()} .. {m['dt'].iloc[-1].date()})\n")

    lib = _load_library()
    for g in groups:
        specs = REGISTRY.get(g)
        if not specs:
            print(f"[skip] unknown group '{g}'")
            continue
        print(f"\n--- {g} ({len(specs)} signals) ---")
        for spec in specs:
            row = evaluate_signal(m, spec)
            lib[row["name"]] = row
            _print_row(row)

    _save_library(lib)
    _render_scoreboard(lib)

    # quick verdict tally
    tally: dict = {}
    for r in lib.values():
        tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1
    print(f"\n{'='*110}")
    print(f"Library now holds {len(lib)} signals: "
          + "  ".join(f"{k}={v}" for k, v in sorted(tally.items(),
                      key=lambda kv: -VERDICT_RANK.get(kv[0], 0))))
    print(f"Scoreboard -> {SCOREBOARD}")
    print(f"Library    -> {LIB_JSON}")
    print(f"Elapsed: {round(time.time()-t0,1)}s")
    print("=" * 110)


if __name__ == "__main__":
    args = sys.argv[1:] or ["seeds", "group1"]
    main(args)
