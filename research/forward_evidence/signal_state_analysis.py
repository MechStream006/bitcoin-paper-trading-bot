"""
signal_state_analysis.py
========================
Historical signal-STATE characterisation. Read-only research; writes nothing.

THE QUESTION IT ANSWERS
-----------------------
"Is the current forward flat run unusual for this signal process?"

`trade_analysis.accrual_state()` reports the LIVE state -- flat since when,
how many trailing days, is a trade open. It cannot say whether that state is
normal, because the forward corpus is 59 days long and holds one flat run.
This module applies the FROZEN, hash-locked decision function to the frozen
FDB-2a train slice (2021-01-01..2025-05-31) and reports the run-length
distribution the process actually produced there, so a live run can be placed
against it.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
- **No returns, P&L, Sharpe, DSR, BH or performance of any kind.** It reads a
  position series and measures run lengths. Nothing is priced.
- **No lockbox.** The slice ends at `TRAIN_END`; the sealed window
  (2025-06-01..2026-05-31) is never loaded and the boundary is asserted.
- **Nothing predictive.** A run-length distribution says what the process HAS
  done. It is not a forecast, not a hazard rate, and no expected resumption
  date may be derived from it. Percentiles are a RANK POSITION among a small
  number of observed runs, not a calibrated probability.
- **No parameter change, no filtering, no optimisation.**
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
from collections import Counter
from typing import Any, Iterable, Sequence

_HERE = pathlib.Path(__file__).resolve().parent
_RESEARCH_ROOT = _HERE.parent
if str(_RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_ROOT))

TRAIN_START = "2021-01-01"
TRAIN_END = "2025-05-31"          # FDB-2a; the lockbox begins the next day
_DEFAULT_PARQUET = _RESEARCH_ROOT / "data" / "raw" / "spot" / "BTCUSDT"

#: family -> the frozen decision-function module name it is collected with.
#: These are the SAME modules run_daily runs, so the characterisation is of the
#: deployed signal rather than a reimplementation of it.
DECISION_MODULES = {
    "g1_market_regime_sma200": "decision_function_g1_market_regime",
    "h5_vol_regime": "decision_function_h5",
    "g3_momentum_tsmom": "decision_function_g3_tsmom",
    "g1_trend_regime_ma_cross": "decision_function_g1_trend_regime",
    "ivrv_vrp_spread": "decision_function_ivrv",
    "g2_funding_level": "decision_function_g2_funding_level",
    "g3_atr_expansion": "decision_function_g3_atr_expansion",
}


def runs(values: Sequence[float]) -> list[tuple[float, int]]:
    """(value, length) for each maximal constant run."""
    out: list[tuple[float, int]] = []
    cur: float | None = None
    n = 0
    for v in values:
        if cur is not None and v == cur:
            n += 1
        else:
            if cur is not None:
                out.append((cur, n))
            cur, n = v, 1
    if cur is not None:
        out.append((cur, n))
    return out


def rank_position(distribution: Iterable[int], value: int) -> dict[str, Any] | None:
    """Where `value` sits among observed runs.

    Reported as a rank among n observations, with n stated, because a
    percentile over a couple of dozen runs is an ordering -- not a calibrated
    probability, and never a hazard rate.
    """
    d = sorted(distribution)
    if not d:
        return None
    n_longer = sum(1 for x in d if x > value)
    return {
        "value": value,
        "n_observed_runs": len(d),
        "percentile_le": 100.0 * sum(1 for x in d if x <= value) / len(d),
        "n_longer": n_longer,
        "fraction_longer": n_longer / len(d),
        "caveat": ("a rank among %d observed runs, not a probability and not a "
                   "hazard rate; nothing predictive follows from it" % len(d)),
    }


def describe_runs(values: Sequence[float]) -> dict[str, Any]:
    """Flat/active run structure and state transitions of a position series."""
    r = runs(values)
    flat = [n for v, n in r if v == 0.0]
    active = [n for v, n in r if v != 0.0]
    trans: Counter = Counter()
    for a, b in zip(values, values[1:]):
        if a != b:
            trans["%+d -> %+d" % (a, b)] += 1

    def stats(x):
        if not x:
            return None
        s = sorted(x)
        return {"n": len(x), "min": min(x), "median": statistics.median(x),
                "mean": statistics.mean(x), "max": max(x),
                "p75": s[min(int(0.75 * len(s)), len(s) - 1)],
                "p90": s[min(int(0.90 * len(s)), len(s) - 1)]}

    return {
        "days": len(values),
        "flat_days": sum(1 for v in values if v == 0.0),
        "flat_fraction": (sum(1 for v in values if v == 0.0) / len(values)) if values else None,
        "distinct_positions": sorted({float(v) for v in values}),
        "n_runs": len(r),
        "flat_runs": stats(flat),
        "active_runs": stats(active),
        "long_runs": stats([n for v, n in r if v > 0]),
        "short_runs": stats([n for v, n in r if v < 0]),
        "transitions": dict(trans),
        "flat_runs_ge_7d": sum(1 for n in flat if n >= 7),
        "_flat_distribution": flat,
    }


def train_slice_positions(family_id: str, parquet_dir=None):
    """The frozen decision function's position series over the train slice.

    The slice boundary is asserted, not assumed: this must never see a sealed
    day.
    """
    import importlib

    from forward_evidence import runner_base

    if family_id not in DECISION_MODULES:
        raise ValueError(f"unknown family {family_id!r}")
    mod = importlib.import_module(
        f"forward_evidence.{DECISION_MODULES[family_id]}")

    oh = runner_base.load_daily_ohlcv(
        pathlib.Path(parquet_dir or _DEFAULT_PARQUET), TRAIN_END, min_rows=1600)
    oh = oh[(oh.index >= TRAIN_START) & (oh.index <= TRAIN_END)]
    if oh.empty:
        raise RuntimeError("train slice is empty")
    if oh.index[-1] > TRAIN_END:
        raise RuntimeError(f"lockbox boundary breached: {oh.index[-1]}")

    pos = mod.compute_signal(oh["close"]).dropna()
    return pos


def characterise(family_id: str, current_flat_days: int | None = None,
                 parquet_dir=None) -> dict[str, Any]:
    pos = train_slice_positions(family_id, parquet_dir)
    vals = [float(v) for v in pos]
    out = describe_runs(vals)
    out["family_id"] = family_id
    out["window"] = [str(pos.index[0]), str(pos.index[-1])]
    if current_flat_days is not None:
        out["current_flat_run"] = rank_position(out["_flat_distribution"],
                                                current_flat_days)
    out.pop("_flat_distribution", None)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Signal-state run-length characterisation over the frozen "
                    "train slice. Read-only; no returns, no DSR/BH, no lockbox.")
    p.add_argument("--family", action="append", required=True,
                   choices=sorted(DECISION_MODULES))
    p.add_argument("--current-flat", action="append", metavar="family=DAYS",
                   help="place a live flat run against the historical distribution")
    p.add_argument("--parquet-dir", type=pathlib.Path, default=_DEFAULT_PARQUET)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    live = {}
    for pair in args.current_flat or []:
        k, _, v = pair.partition("=")
        live[k.strip()] = int(v)

    out = {f: characterise(f, live.get(f), args.parquet_dir) for f in args.family}
    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True, default=str))
        return 0

    for fam, d in out.items():
        print(f"\n{fam}   {d['window'][0]} .. {d['window'][1]}  ({d['days']} days)")
        print(f"  flat {d['flat_days']} ({d['flat_fraction']:.0%})  "
              f"positions {d['distinct_positions']}  runs {d['n_runs']}")
        for k in ("flat_runs", "active_runs"):
            s = d[k]
            if s:
                print(f"  {k:<13} n={s['n']:<4} min={s['min']:<4} median={s['median']:<6.0f} "
                      f"mean={s['mean']:<6.1f} p90={s['p90']:<5} max={s['max']}")
        print(f"  flat runs >= 7d: {d['flat_runs_ge_7d']}   transitions {d['transitions']}")
        cur = d.get("current_flat_run")
        if cur:
            print(f"  LIVE flat run {cur['value']}d -> {cur['percentile_le']:.0f}th pct of "
                  f"{cur['n_observed_runs']} runs; {cur['n_longer']} were longer "
                  f"({cur['fraction_longer']:.0%})")
            print(f"    {cur['caveat']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
