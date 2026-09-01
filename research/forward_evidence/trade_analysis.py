"""
trade_analysis.py
=================
Read-only descriptive analysis of the accumulating forward-evidence trade
corpus. Research surface only: it writes nothing, anywhere.

WHAT IT IS FOR
--------------
Turning collected trades into legible evidence while families accrue toward
their frozen minima -- per-trade excursions, weekday structure, the INC-0005
segment split, and data-quality findings.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
- **No DSR, BH-p, PSR, deflated Sharpe or pairwise correlation.** Those belong
  to `forward_dsr_bh` under the frozen FB-10b contract, and only once a family
  actually reaches its minimum. Nothing here approximates them.
- **No minimum is hardcoded.** FDB-8 makes a family's DSR minimum
  operator-supplied with no default; a table of the seven here would be the
  second source of truth OD-3 rejected. `--minimum family=N` is optional and
  purely for display.
- **No gap is authored.** The INC-0005 segment boundary is read from
  `run_date_policy.yaml immutable_gaps`, the same frozen declaration
  `forward_readiness` reads. Gap trades are never removed, adjusted or
  "corrected" -- the re-anchoring is a documented characteristic of the
  evidence, and quantifying it is the point.
- **No parameter search, no optimisation, no signal change.**

EXCURSIONS
----------
MAE/MFE are derived, not stored -- `schema_forward.sql` deliberately has no
`mae_mfe_path` table. They come from `runner_base.load_daily_ohlcv`, the
collector's own price surface, so the excursion and the realised return are
measured off the same bars. Daily high/low give the true intraday range.

They are **gross**: `net_return` carries the 15 bps cost, excursions do not.
The reference is the entry close, and the window is `(entry_date, exit_date]`
-- the entry bar itself is excursion zero by construction.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable

_HERE = pathlib.Path(__file__).resolve().parent
_RESEARCH_ROOT = _HERE.parent
if str(_RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_ROOT))

DOW = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

_DEFAULT_FORWARD_DB = _RESEARCH_ROOT / "data" / "evidence_forward.db"
_DEFAULT_PARQUET = _RESEARCH_ROOT / "data" / "raw" / "spot" / "BTCUSDT"


@dataclass
class Trade:
    signal_id: str
    entry_date: str
    exit_date: str
    direction: int
    entry_close: float
    exit_close: float
    net_return: float
    gross_return: float
    cost_bps: float
    hold_days: int
    seq: int = 0                       # 1-based position within the family
    segment: str = "unsegmented"       # pre / post, relative to a declared gap
    mfe: float | None = None           # gross favourable excursion (intraday)
    mae: float | None = None           # gross adverse excursion (intraday)
    mfe_close: float | None = None     # gross favourable, close-only
    mae_close: float | None = None

    @property
    def entry_dow(self) -> str:
        return DOW[date.fromisoformat(self.entry_date).weekday()]

    @property
    def exit_dow(self) -> str:
        return DOW[date.fromisoformat(self.exit_date).weekday()]

    @property
    def edge_captured(self) -> float | None:
        """Realised net return as a fraction of the best excursion offered.
        None when no favourable excursion existed, because the ratio is then
        undefined rather than zero."""
        if self.mfe is None or self.mfe <= 0:
            return None
        return self.net_return / self.mfe

    def as_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items()}
        d.update(entry_dow=self.entry_dow, exit_dow=self.exit_dow,
                 edge_captured=self.edge_captured)
        return d


# ---------------------------------------------------------------------------
# Loading (read-only)
# ---------------------------------------------------------------------------

def _connect_ro(db_path) -> sqlite3.Connection:
    uri = pathlib.Path(db_path).resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=10.0)
    con.row_factory = sqlite3.Row
    return con


def load_trades(forward_db, family_id: str | None = None) -> list[Trade]:
    """Every CLOSED trade, ordered. Open trades are structurally absent from
    `trade_returns` (no exit row, no join), which is what keeps this
    point-in-time correct without a filter."""
    con = _connect_ro(forward_db)
    try:
        sql = ("SELECT signal_id, entry_date, exit_date, direction, "
               "entry_close_price, exit_close_price, net_return, gross_return, "
               "cost_bps, hold_days_actual FROM trade_returns")
        params: tuple = ()
        if family_id:
            sql += " WHERE signal_id = ?"
            params = (family_id,)
        sql += " ORDER BY signal_id, entry_date, entry_id"
        rows = con.execute(sql, params).fetchall()
    finally:
        con.close()

    out: list[Trade] = []
    seq: Counter = Counter()
    for r in rows:
        seq[r["signal_id"]] += 1
        out.append(Trade(
            signal_id=r["signal_id"], entry_date=r["entry_date"],
            exit_date=r["exit_date"], direction=int(r["direction"]),
            entry_close=float(r["entry_close_price"]),
            exit_close=float(r["exit_close_price"]),
            net_return=float(r["net_return"]), gross_return=float(r["gross_return"]),
            cost_bps=float(r["cost_bps"]), hold_days=int(r["hold_days_actual"]),
            seq=seq[r["signal_id"]]))
    return out


def declared_gaps(policy_path=None) -> list[tuple[str, str]]:
    """INC-0005 and any sibling, read from frozen policy. Never authored here."""
    from forward_evidence import run_date_policy
    cfg = run_date_policy.load_policy(policy_path)
    return [(g["start"], g["end"]) for g in cfg.get("immutable_gaps", [])]


def assign_segments(trades: Iterable[Trade], gaps: list[tuple[str, str]]) -> None:
    """Label each trade by its position relative to the declared gaps.

    Trades are never dropped or adjusted. CLAUDE.md sec.20 requires the series
    be treated as segments for weekday-sensitive work; labelling is how that
    requirement is honoured without touching the evidence.
    """
    if not gaps:
        return
    last_end = max(e for _, e in gaps)
    first_start = min(s for s, _ in gaps)
    for t in trades:
        if t.entry_date < first_start:
            t.segment = "pre"
        elif t.entry_date > last_end:
            t.segment = "post"
        else:
            t.segment = "in-gap"


# ---------------------------------------------------------------------------
# Excursions
# ---------------------------------------------------------------------------

def enrich_excursions(trades: Iterable[Trade], ohlcv) -> int:
    """Attach MAE/MFE from a daily OHLCV frame indexed by 'YYYY-MM-DD'.

    Window is (entry_date, exit_date]: the entry bar is the reference, so its
    own excursion is zero by construction and including it would floor MAE at 0.

    Favourable/adverse are direction-aware -- a short's best case is the low,
    its worst the high. Both are GROSS; `net_return` alone carries the cost.

    Returns the number of trades enriched; a trade whose window is not fully
    covered by the frame is left with `None` rather than a partial figure.
    """
    n = 0
    for t in trades:
        d0, d1 = date.fromisoformat(t.entry_date), date.fromisoformat(t.exit_date)
        days = [(d0 + timedelta(days=i)).isoformat() for i in range(1, (d1 - d0).days + 1)]
        if not days or any(d not in ohlcv.index for d in days):
            continue
        win = ohlcv.loc[days]
        hi, lo = float(win["high"].max()), float(win["low"].min())
        cl_hi, cl_lo = float(win["close"].max()), float(win["close"].min())
        fav, adv = (hi, lo) if t.direction > 0 else (lo, hi)
        fav_c, adv_c = (cl_hi, cl_lo) if t.direction > 0 else (cl_lo, cl_hi)
        t.mfe = (fav / t.entry_close - 1.0) * t.direction
        t.mae = (adv / t.entry_close - 1.0) * t.direction
        t.mfe_close = (fav_c / t.entry_close - 1.0) * t.direction
        t.mae_close = (adv_c / t.entry_close - 1.0) * t.direction
        n += 1
    return n


# ---------------------------------------------------------------------------
# Weekday re-anchoring (the INC-0005 characteristic)
# ---------------------------------------------------------------------------

def weekday_anchoring(trades: list[Trade]) -> dict[str, Any]:
    """Quantify the entry-weekday anchor per segment, and the shift between.

    A `hold_days = 7` cadence preserves the entry weekday across consecutive
    trades, so each uninterrupted run has a single anchor. An outage whose
    length is not a multiple of 7 necessarily re-anchors the series to the
    resumption weekday. This measures that, it does not correct it.
    """
    by_seg: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        by_seg[t.segment].append(t)

    out: dict[str, Any] = {"segments": {}}
    for seg, ts in by_seg.items():
        c = Counter(t.entry_dow for t in ts)
        anchor, n_anchor = (c.most_common(1)[0] if c else (None, 0))
        out["segments"][seg] = {
            "n": len(ts),
            "weekdays": dict(c),
            "anchor": anchor,
            "anchor_purity": (n_anchor / len(ts)) if ts else None,
            "first_entry": min((t.entry_date for t in ts), default=None),
            "last_entry": max((t.entry_date for t in ts), default=None),
        }

    pre, post = out["segments"].get("pre"), out["segments"].get("post")
    if pre and post and pre["anchor"] and post["anchor"]:
        shift = (DOW.index(post["anchor"]) - DOW.index(pre["anchor"])) % 7
        out["reanchor"] = {
            "from": pre["anchor"], "to": post["anchor"], "shift_days": shift,
            "confounded": True,
            "note": ("entry weekday is perfectly confounded with segment: every "
                     "pre-gap entry is one weekday and every post-gap entry "
                     "another, so no weekday effect is separable from a period "
                     "effect. Segments must be analysed separately "
                     "(CLAUDE.md sec.20)."),
        }
    return out


# ---------------------------------------------------------------------------
# Accrual state -- whether a family can currently produce a trade at all
# ---------------------------------------------------------------------------

def accrual_state(forward_db) -> dict[str, dict[str, Any]]:
    """Per family: is the signal on, and is a trade open?

    Closed-trade counts alone cannot answer "when does this family reach its
    minimum": a count stays put whether the signal is firing or flat, so a
    stalled family and a slow one look identical. A 7-day cadence only holds
    while the signal is continuously non-flat -- `_apply_hold`-style chaining
    re-enters on the exit day, but only if `position != 0`.

    `trailing_flat_days` is the live figure: how many consecutive days the
    signal has been flat up to the latest run. With no open trade, that is a
    stall, and no arrival estimate built on the historical median gap applies.
    """
    con = _connect_ro(forward_db)
    try:
        fams = [r[0] for r in con.execute(
            "SELECT DISTINCT signal_id FROM signal_log ORDER BY signal_id")]
        out: dict[str, dict[str, Any]] = {}
        for fam in fams:
            rows = con.execute(
                "SELECT day, position FROM signal_log WHERE signal_id = ? "
                "ORDER BY day", (fam,)).fetchall()
            pos = [r["position"] for r in rows]
            runs: list[int] = []
            cur = 0
            for v in pos:
                if v == 0:
                    cur += 1
                elif cur:
                    runs.append(cur)
                    cur = 0
            trailing = cur
            if cur:
                runs.append(cur)
            open_n = con.execute(
                "SELECT COUNT(*) FROM trade_entries WHERE signal_id = ? AND open = 1",
                (fam,)).fetchone()[0]
            last_active = next((r["day"] for r in reversed(rows) if r["position"] != 0), None)
            out[fam] = {
                "days_observed": len(pos),
                "flat_days": sum(1 for v in pos if v == 0),
                "flat_fraction": (sum(1 for v in pos if v == 0) / len(pos)) if pos else None,
                "trailing_flat_days": trailing,
                "longest_flat_run": max(runs) if runs else 0,
                "open_trades": open_n,
                "last_active_day": last_active,
                "latest_day": rows[-1]["day"] if rows else None,
                "accruing": bool(open_n) or trailing == 0,
            }
        return out
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Concentration and co-movement (descriptive decompositions of THIS sample)
# ---------------------------------------------------------------------------

def concentration(nets):
    """How much of a sample's total absolute movement sits in its largest
    trades, and what the sample looks like without its single worst.

    A decomposition of the observed set, not a robustness claim and not an
    estimate: with a handful of trades, removing the worst is arithmetic, not
    evidence. Reported so a large single contributor is visible rather than
    buried inside a mean.
    """
    if not nets:
        return {}
    total_abs = sum(abs(x) for x in nets)
    order = sorted(nets, key=abs, reverse=True)
    worst = min(nets)
    cum_ex = 1.0
    dropped = False
    for x in nets:
        if x == worst and not dropped:      # drop one instance only
            dropped = True
            continue
        cum_ex *= (1.0 + x)
    return {
        "top1_share_of_abs": (abs(order[0]) / total_abs) if total_abs else None,
        "top2_share_of_abs": (sum(abs(x) for x in order[:2]) / total_abs) if total_abs else None,
        "best": max(nets),
        "worst": worst,
        "sum_wins": sum(x for x in nets if x > 0),
        "sum_losses": sum(x for x in nets if x < 0),
        "cum_excluding_worst": cum_ex - 1.0,
    }


def co_movement(trades):
    """Where families entered on the same date, and whether they agreed on
    direction.

    A co-occurrence count only. Pairwise correlation is a Validated-gate
    statistic under the frozen thresholds and is deliberately NOT computed
    here -- this says how often families act on the same day, nothing about
    how their returns relate.
    """
    by_date = defaultdict(list)
    for t in trades:
        by_date[t.entry_date].append(t)
    shared = {d: ts for d, ts in by_date.items() if len(ts) > 1}
    unanimous = sum(1 for ts in shared.values()
                    if len({t.direction for t in ts}) == 1)
    return {
        "shared_entry_dates": len(shared),
        "unanimous_direction": unanimous,
        "split_direction": len(shared) - unanimous,
        "dates": {d: sorted(t.signal_id for t in ts) for d, ts in sorted(shared.items())},
        "note": ("co-occurrence only; pairwise correlation is a Validated-gate "
                 "statistic and is not computed here"),
    }


# ---------------------------------------------------------------------------
# Quality findings
# ---------------------------------------------------------------------------

def quality_findings(forward_db, trades: list[Trade],
                     gaps: list[tuple[str, str]]) -> list[str]:
    """Actual findings only. An empty list means the corpus is clean."""
    findings: list[str] = []
    con = _connect_ro(forward_db)
    try:
        dupes = con.execute(
            "SELECT signal_id, entry_date, COUNT(*) n FROM trade_entries "
            "GROUP BY signal_id, entry_date HAVING n > 1").fetchall()
        if dupes:
            findings.append(f"{len(dupes)} duplicate (signal, entry_date) rows")
        dx = con.execute("SELECT entry_id, COUNT(*) n FROM trade_exits "
                         "GROUP BY entry_id HAVING n > 1").fetchall()
        if dx:
            findings.append(f"{len(dx)} entries carry more than one exit")
        statuses = {r[0] for r in con.execute(
            "SELECT DISTINCT status FROM run_log")}
        if statuses - {"ok"}:
            findings.append(f"non-ok run_log statuses present: {sorted(statuses - {'ok'})}")
    finally:
        con.close()

    if any(t.exit_date <= t.entry_date for t in trades):
        findings.append("a trade exits on or before its entry date")
    holds = {t.hold_days for t in trades}
    if len(holds) > 1:
        findings.append(f"mixed hold_days_actual: {sorted(holds)}")
    by_fam: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        by_fam[t.signal_id].append(t)
    for fam, ts in by_fam.items():
        s = sorted(ts, key=lambda x: x.entry_date)
        for a, b in zip(s, s[1:]):
            if b.entry_date < a.exit_date:
                findings.append(f"{fam}: overlapping trades {a.entry_date}/{b.entry_date}")
    for lo, hi in gaps:
        inside = [t for t in trades if lo <= t.entry_date <= hi]
        if inside:
            findings.append(
                f"{len(inside)} trade(s) ENTERED inside the immutable gap {lo}..{hi}")
    return findings


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _fmt(v, spec="+.4f", na="-"):
    return na if v is None else format(v, spec)


def build_report(forward_db, parquet_dir=None, minima: dict[str, int] | None = None,
                 policy_path=None) -> dict[str, Any]:
    trades = load_trades(forward_db)
    gaps = declared_gaps(policy_path)
    assign_segments(trades, gaps)

    enriched = 0
    if parquet_dir is not None:
        try:
            from forward_evidence import runner_base
            latest = max((t.exit_date for t in trades), default=None)
            earliest = min((t.entry_date for t in trades), default=None)
            if latest and earliest:
                # load_daily_ohlcv treats min_rows as a LOOKBACK
                # (start = through - min_rows - 32), so it must span the whole
                # trade history. min_rows=1 silently returns only the last ~33
                # days and leaves every earlier trade unenriched -- which reads
                # as "no excursion" rather than "not loaded".
                span = (date.fromisoformat(latest) - date.fromisoformat(earliest)).days
                ohlcv = runner_base.load_daily_ohlcv(
                    pathlib.Path(parquet_dir), latest, min_rows=span + 1)
                enriched = enrich_excursions(trades, ohlcv)
                if enriched < len(trades):
                    print(f"  (note: {len(trades) - enriched} trade(s) outside the "
                          f"loaded price window {ohlcv.index[0]}..{ohlcv.index[-1]})",
                          file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 -- excursions are optional enrichment
            enriched = -1
            print(f"  (excursions unavailable: {type(exc).__name__}: {exc})",
                  file=sys.stderr)

    by_fam: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        by_fam[t.signal_id].append(t)

    families: dict[str, Any] = {}
    for fam, ts in sorted(by_fam.items()):
        nets = [t.net_return for t in ts]
        cum = 1.0
        for r in nets:
            cum *= (1.0 + r)
        mfes = [t.mfe for t in ts if t.mfe is not None]
        maes = [t.mae for t in ts if t.mae is not None]
        caps = [t.edge_captured for t in ts if t.edge_captured is not None]
        families[fam] = {
            "n": len(ts),
            "minimum": (minima or {}).get(fam),
            "short_by": ((minima or {}).get(fam) - len(ts)) if (minima or {}).get(fam) else None,
            "wins": sum(1 for r in nets if r > 0),
            "losses": sum(1 for r in nets if r < 0),
            "mean_net": statistics.mean(nets) if nets else None,
            "median_net": statistics.median(nets) if nets else None,
            "cum_net": cum - 1.0,
            "n_long": sum(1 for t in ts if t.direction > 0),
            "n_short": sum(1 for t in ts if t.direction < 0),
            "mean_mfe": statistics.mean(mfes) if mfes else None,
            "mean_mae": statistics.mean(maes) if maes else None,
            "median_edge_captured": statistics.median(caps) if caps else None,
            "entry_weekdays": dict(Counter(t.entry_dow for t in ts)),
            "exit_weekdays": dict(Counter(t.exit_dow for t in ts)),
            "entry_exit_dow": dict(Counter(f"{t.entry_dow}->{t.exit_dow}" for t in ts)),
            "segments": dict(Counter(t.segment for t in ts)),
            "anchoring": weekday_anchoring(ts),
            "concentration": concentration(nets),
            "mfe_over_mae": (statistics.mean(mfes) / abs(statistics.mean(maes))
                             if mfes and maes and statistics.mean(maes) else None),
            "entry_gaps_days": [
                (date.fromisoformat(b.entry_date) - date.fromisoformat(a.entry_date)).days
                for a, b in zip(ts, ts[1:])],
            "trades": [t.as_dict() for t in ts],
        }

    all_nets = [t.net_return for t in trades]
    pooled = {
        "n": len(trades),
        "wins": sum(1 for x in all_nets if x > 0),
        "losses": sum(1 for x in all_nets if x < 0),
        "mean": statistics.mean(all_nets) if all_nets else None,
        "median": statistics.median(all_nets) if all_nets else None,
        "stdev": statistics.stdev(all_nets) if len(all_nets) > 1 else None,
        "n_long": sum(1 for t in trades if t.direction > 0),
        "n_short": sum(1 for t in trades if t.direction < 0),
        "entry_weekdays": dict(Counter(t.entry_dow for t in trades)),
        "hold_days": sorted({t.hold_days for t in trades}),
        "cost_bps": sorted({t.cost_bps for t in trades}),
        "concentration": concentration(all_nets),
        "co_movement": co_movement(trades),
        "note": ("families trade the same underlying on overlapping days, so "
                 "these are NOT independent observations -- shape only"),
    }
    return {
        "n_trades": len(trades),
        "accrual": accrual_state(forward_db),
        "excursions_enriched": enriched,
        "declared_gaps": [{"start": a, "end": b} for a, b in gaps],
        "families": families,
        "pooled": pooled,
        "quality_findings": quality_findings(forward_db, trades, gaps),
    }


def print_report(rep: dict[str, Any]) -> None:
    print(f"\nFORWARD TRADE EVIDENCE - {rep['n_trades']} closed trades, "
          f"{rep['excursions_enriched']} with excursions")
    print(f"declared immutable gaps: "
          f"{[g['start'] + '..' + g['end'] for g in rep['declared_gaps']] or 'none'}")

    print(f"\n{'family':<26}{'n':>4}{'min':>5}{'need':>5}{'W/L':>7}{'mean':>10}"
          f"{'cum':>10}{'mfe':>9}{'mae':>9}{'cap':>7}")
    for fam, d in rep["families"].items():
        need = d["short_by"]
        print(f"  {fam:<24}{d['n']:>4}{d['minimum'] or '-':>5}"
              f"{need if need is not None else '-':>5}"
              f"{str(d['wins']) + '/' + str(d['losses']):>7}"
              f"{_fmt(d['mean_net']):>10}{_fmt(d['cum_net']):>10}"
              f"{_fmt(d['mean_mfe']):>9}{_fmt(d['mean_mae']):>9}"
              f"{_fmt(d['median_edge_captured'], '.2f'):>7}")

    for fam, d in rep["families"].items():
        print(f"\n-- {fam}  (n={d['n']}, {d['n_long']}L/{d['n_short']}S) "
              f"segments={d['segments']}")
        ra = d["anchoring"].get("reanchor")
        if ra:
            print(f"   RE-ANCHOR {ra['from']} -> {ra['to']}  (+{ra['shift_days']}d), "
                  f"confounded with segment")
        for seg, s in sorted(d["anchoring"]["segments"].items()):
            print(f"     {seg:<6} n={s['n']:<3} anchor={s['anchor']} "
                  f"purity={_fmt(s['anchor_purity'], '.0%')} "
                  f"{s['first_entry']}..{s['last_entry']}  {s['weekdays']}")
        print(f"   {'#':>3} {'entry':<12}{'exit':<12}{'dir':>4}"
              f"{'net':>10}{'mfe':>9}{'mae':>9}{'cap':>7}  seg")
        for t in d["trades"]:
            cap = t["edge_captured"]
            print(f"   {t['seq']:>3} {t['entry_date']} ({t['entry_dow']})"
                  f" {t['exit_date']} ({t['exit_dow']}) {t['direction']:>3}"
                  f"{_fmt(t['net_return']):>10}{_fmt(t['mfe']):>9}"
                  f"{_fmt(t['mae']):>9}{_fmt(cap, '.2f'):>7}  {t['segment']}")

    acc = rep.get("accrual") or {}
    if acc:
        print("\nACCRUAL STATE (a count alone cannot distinguish stalled from slow)")
        print(f"   {'family':<26}{'obs':>5}{'flat%':>7}{'trailing':>10}{'open':>6}  status")
        for fam, a in sorted(acc.items()):
            status = "accruing" if a["accruing"] else \
                     f"STALLED since {a['last_active_day']}"
            print(f"   {fam:<26}{a['days_observed']:>5}"
                  f"{_fmt(a['flat_fraction'], '.0%'):>7}"
                  f"{a['trailing_flat_days']:>10}{a['open_trades']:>6}  {status}")

    p = rep.get("pooled") or {}
    if p.get("n"):
        c, cm = p["concentration"], p["co_movement"]
        print("\nPOOLED (not independent observations - shape only)")
        print(f"   n={p['n']} wins={p['wins']} losses={p['losses']} "
              f"{p['n_long']}L/{p['n_short']}S  mean={_fmt(p['mean'])} "
              f"median={_fmt(p['median'])} stdev={_fmt(p['stdev'], '.4f')}")
        print(f"   best={_fmt(c['best'])} worst={_fmt(c['worst'])} "
              f"top1={_fmt(c['top1_share_of_abs'], '.0%')} of abs movement")
        print(f"   hold_days={p['hold_days']} cost_bps={p['cost_bps']} "
              f"weekdays={p['entry_weekdays']}")
        print(f"   co-movement: {cm['shared_entry_dates']} shared entry dates, "
              f"{cm['unanimous_direction']} unanimous / {cm['split_direction']} split")

    print("\nDATA-QUALITY FINDINGS:")
    for f in rep["quality_findings"] or ["  none - corpus clean"]:
        print(f"  {f}" if rep["quality_findings"] else f)


def _parse_minima(pairs) -> dict[str, int]:
    out: dict[str, int] = {}
    for p in pairs or []:
        fam, _, v = p.partition("=")
        out[fam.strip()] = int(v)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Descriptive forward-trade evidence. Read-only; no DSR/BH.")
    p.add_argument("--forward-db", type=pathlib.Path, default=_DEFAULT_FORWARD_DB)
    p.add_argument("--parquet-dir", type=pathlib.Path, default=_DEFAULT_PARQUET,
                   help="spot partitions, for MAE/MFE. Omit with --no-excursions.")
    p.add_argument("--no-excursions", action="store_true")
    p.add_argument("--minimum", action="append", metavar="family=N",
                   help="display only; no minimum is ever defaulted (FDB-8)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    rep = build_report(
        args.forward_db,
        None if args.no_excursions else args.parquet_dir,
        _parse_minima(args.minimum))
    if args.json:
        print(json.dumps(rep, indent=2, sort_keys=True, default=str))
    else:
        print_report(rep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
