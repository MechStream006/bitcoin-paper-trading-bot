"""
forward_dsr_bh.py
=================
The **sole** forward DSR/BH producer, implementing the adopted contract in
`docs/governance/fb10b_forward_dsr_bh_contract.md` (FDB-1 … FDB-13).

Battery-wide and operator-invoked (FDB-11). It computes; it does not write.
Persistence is `forward_snapshot`'s job, so this module has no ledger write
path at all -- not "does not currently write", but *cannot*.

WHAT IT COMPUTES
----------------
1. **Battery membership** (FDB-5) -- cheap, and evaluated FIRST. A family is a
   member iff it has an operator-supplied frozen DSR minimum, meets it at the
   `as_of`, and has `n >= 5`. If nobody qualifies the producer returns a
   refusal **without ever loading the research corpus**.
2. **Dispersion** (FDB-1/FDB-2/FDB-2a) -- `sr_trials_std` = `stdev(ddof=1)` of
   the per-trade Sharpes of ALL 34 Phase B library signals, re-measured on the
   frozen train slice at `hold_days = 7`, `cost_bps = 15`. No conversion of any
   kind is applied; `run_harmonization`'s `sqrt(7)` scaling is rejected by
   FDB-2 and appears nowhere here.
3. **Per-member statistics** -- `deflated_sr` and `psr` from
   `research/evidence/governance.py`, the sole statistical authority (FDB-11).
4. **BH** -- `bh_adjusted_p` over the realized battery only (FDB-6), stored as
   `bh_p` per FDB-13/OD-4.

THE TRADE CONSTRUCTION IS THE FORWARD ONE, NOT AN OFFLINE ANALOGUE
-------------------------------------------------------------------
FDB-2 says the 34 are re-measured "at the forward trade-return basis". That
basis has exactly one implementation -- `runner_base.run_signal()` steps 5a/5b
plus the `trade_returns` view -- and `build_trades()` below replays it:
close the overdue trade before opening a new one (so a trade may exit and the
next open on the same day), at most one open trade at a time, an unconditional
exit `hold_days` CALENDAR days after entry that does not depend on signal
reversal, `direction = int(position)` in {-1, +1}, and

    net = (exit_close / entry_close - 1) * direction - cost_bps / 10_000

`engine._runs` (variable-length runs) and `run_harmonization._h6_native_trades`
(long-only) are the OFFLINE constructions and are deliberately unused: the
first is not non-overlapping-7-day, the second cannot express a short leg.

WHY MEMBER SHARPES ARE COMPUTED HERE AND NOT VIA psr()
-------------------------------------------------------
FDB-1 asks for Sharpe RATIOS, not PSR. `governance._sr_stats` returns
`0.0, n, 0.0, 3.0` for `n < 5` or zero variance -- silently, with no NaN and no
exception. Routing a dispersion member through it would inject invented zeros
into `sr_trials_std` and therefore into every `sr_star`. So the member Sharpe is
a direct `mean / stdev(ddof=1)`, and a member that cannot produce one
(`n < 2`, or zero dispersion) **refuses the entire battery** (OD-2/T1).

NO LOCKBOX
----------
The master is sliced to `< 2025-06-01` before any position is computed, so
rolling windows never see a lockbox row and no statistic is ever formed over
one. `_train_slice_master()` is the only loader and it returns the slice --
callers never hold the full frame.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import logging
import math
import pathlib
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable

_HERE = pathlib.Path(__file__).resolve().parent
_RESEARCH_ROOT = _HERE.parent
for _p in (_RESEARCH_ROOT, _RESEARCH_ROOT / "evidence", _RESEARCH_ROOT / "forward_evidence"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

log = logging.getLogger(__name__)

_EVIDENCE_DIR = _RESEARCH_ROOT / "evidence"
_BINDING_KEY = "research.evidence.%s (forward_dsr_bh binding)"
_ABSENT = object()


def _load_evidence_module(name: str):
    """Load `research/evidence/<name>.py` under a process-unique sys.modules key.

    `sys.modules` is process-global and `research/dq/engine.py` (PR-7) registers
    under the same bare name `engine` as `research/evidence/engine.py`. Whichever
    imports first shadows the other, and `signals.py:16`'s bare
    `from engine import SignalSpec` then resolves to the wrong module -- observed
    as `ImportError: cannot import name 'SignalSpec'`.

    Same remedy as `research/feature_store/evidence_engine_bindings.py`, which
    documents this collision by name. One extension is needed that the precedent
    did not require: `signals` cannot be loaded by path alone, because its own
    bare import still goes through `sys.modules["engine"]`. So the correct
    engine is bound to the bare name **only while the module executes**, and the
    previous binding -- including its absence -- is restored in a `finally`,
    so a caller's interpreter state survives unchanged either way.

    Neither `research/dq/engine.py`, `research/evidence/engine.py` nor
    `signals.py` is modified, and no global import hook is installed.
    """
    key = _BINDING_KEY % name
    cached = sys.modules.get(key)
    if cached is not None:
        return cached

    engine_mod = _load_evidence_module("engine") if name != "engine" else None
    spec = importlib.util.spec_from_file_location(key, _EVIDENCE_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module

    saved = sys.modules.get("engine", _ABSENT)
    sys.modules["engine"] = module if engine_mod is None else engine_mod
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(key, None)
        raise
    finally:
        if saved is _ABSENT:
            sys.modules.pop("engine", None)
        else:
            sys.modules["engine"] = saved
    return module

# --- Frozen constants -------------------------------------------------------

#: FDB-2a. The frozen training / non-lockbox window. The lockbox
#: (2025-06-01 .. 2026-05-31) is NOT spanned; see run_date_policy.yaml.
TRAIN_START = "2021-01-01"
TRAIN_END = "2025-05-31"
LOCKBOX_START_MS = int(datetime(2025, 6, 1, tzinfo=timezone.utc).timestamp() * 1000)

HOLD_DAYS = 7          # FDB-2
COST_BPS = 15          # FDB-2, == engine.REALISTIC_COST
N_TRIALS = 34          # frozen; CLAUDE.md sec.16
Q = 0.10               # FDB-9; thresholds.yaml governance.fdr_q
MIN_N_COMPUTABLE = 5   # FDB-8 -- the mathematical guard, never sufficiency
MIN_TRADES_FOR_SHARPE = 2

CALC_VERSION = "forward-dsr-bh-v1"
PAYLOAD_SCHEMA = "forward-dsr-bh/1"
RETURN_BASIS = (
    "evidence_forward.db:trade_returns.net_return; non-overlapping "
    f"{HOLD_DAYS}-day trades; cost_bps={COST_BPS}; exit_date <= as_of"
)

#: signals.REGISTRY groups forming the frozen 34. 'dvol' is excluded: those four
#: are the discovery screen that produced the retired n_trials=38 denominator.
LIBRARY_GROUPS = ("seeds", "group1", "group2", "group3", "group4", "group5", "group6")

#: The seven forward families, in run_daily's order.
FORWARD_FAMILIES = (
    "h5_vol_regime",
    "g1_market_regime_sma200",
    "g3_momentum_tsmom",
    "g1_trend_regime_ma_cross",
    "ivrv_vrp_spread",
    "g2_funding_level",
    "g3_atr_expansion",
)

_REFUSED_ACTOR_TYPES = frozenset({"service", "cron", "system"})


class BatteryRefused(RuntimeError):
    """The battery cannot be computed. Carries a machine-readable reason."""

    def __init__(self, reason: str, detail: str, context: dict | None = None):
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.context = context or {}


class ActorRefusedError(RuntimeError):
    """A non-human actor attempted a battery computation (FDB-11)."""


# ---------------------------------------------------------------------------
# Population (FDB-1)
# ---------------------------------------------------------------------------

def library_specs() -> list:
    """The frozen 34 Phase B library SignalSpecs, in REGISTRY order."""
    S = _load_evidence_module("signals")

    specs = [sp for g in LIBRARY_GROUPS for sp in S.REGISTRY[g]]
    if len(specs) != N_TRIALS:
        raise BatteryRefused(
            "population_size",
            f"expected {N_TRIALS} library signals, found {len(specs)}. FDB-1 fixes "
            "the population; it may not be changed to accommodate n_trials.",
        )
    names = [sp.name for sp in specs]
    if len(set(names)) != N_TRIALS:
        raise BatteryRefused("population_duplicate", "duplicate signal names in the population")
    return specs


def population_hash(names: Iterable[str]) -> str:
    """Content hash of the ordered population, so a stored result names the
    exact 34 it was deflated against."""
    payload = "\n".join(names).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# Trade construction -- the forward basis, replayed (FDB-2 / OD-1)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Trade:
    entry_date: str
    exit_date: str
    direction: int
    entry_close: float
    exit_close: float

    @property
    def net_return(self) -> float:
        return (self.exit_close / self.entry_close - 1.0) * self.direction - COST_BPS / 10_000.0


def build_trades(
    days: list[str],
    closes: list[float],
    positions: list[float],
    hold_days: int = HOLD_DAYS,
) -> list[Trade]:
    """Replay `runner_base.run_signal()` steps 5a/5b over a daily series.

    Pure and deterministic. `days` must be ascending ISO dates; the three lists
    are aligned. Order of operations matters and mirrors production exactly:
    5a (close the overdue trade) runs BEFORE 5b (open a new one), so a trade can
    exit and the next enter on the same day.

    A trade whose planned exit day is absent from the series is never closed --
    the same outcome production reaches, where the exit raises and the trade
    stays open. Such a trade has no exit row and is therefore structurally
    absent from the result, exactly as it is absent from `trade_returns`.
    """
    if not (len(days) == len(closes) == len(positions)):
        raise ValueError("days, closes and positions must be the same length")
    index = {d: i for i, d in enumerate(days)}

    trades: list[Trade] = []
    open_entry: str | None = None
    open_dir = 0
    open_close = 0.0
    open_exit_day: str | None = None

    for i, day in enumerate(days):
        if open_entry is not None and open_exit_day is not None and open_exit_day <= day:
            j = index.get(open_exit_day)
            if j is not None:
                trades.append(Trade(open_entry, open_exit_day, open_dir,
                                    open_close, float(closes[j])))
                open_entry = None
        if open_entry is None:
            pos = positions[i]
            if pos is not None and not (isinstance(pos, float) and math.isnan(pos)) and pos != 0.0:
                open_entry = day
                open_dir = int(pos)
                open_close = float(closes[i])
                open_exit_day = (date.fromisoformat(day) + timedelta(days=hold_days)).isoformat()
    return trades


def direct_sharpe(returns: list[float]) -> float | None:
    """Per-trade Sharpe: `mean / stdev(ddof=1)`, no annualisation.

    Returns None -- never 0.0 -- when it does not exist (`n < 2`, or zero
    dispersion). The caller must treat None as a battery-level refusal (OD-2);
    it must never be substituted, dropped or imputed.
    """
    n = len(returns)
    if n < MIN_TRADES_FOR_SHARPE:
        return None
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    sd = math.sqrt(var)
    if sd <= 0.0:
        return None
    return mean / sd


# ---------------------------------------------------------------------------
# Dispersion (FDB-1 / FDB-2 / FDB-2a)
# ---------------------------------------------------------------------------

def _train_slice_master():
    """The master frame sliced to the frozen train window, and nothing wider.

    The slice is applied here so no caller ever holds a lockbox row and no
    rolling window can see one. Loading and immediately discarding is
    *existence*, not *spending* (FB-10b-J), and follows run_harmonization.py's
    established convention.
    """
    E = _load_evidence_module("engine")

    m_full = E.load_master()
    m = m_full[m_full["day_ts"] < LOCKBOX_START_MS].reset_index(drop=True)
    if m.empty:
        raise BatteryRefused("empty_train_slice", "the train slice contains no rows")
    return m


def expected_train_days() -> list[str]:
    """Every calendar day the frozen train slice must contain.

    Derived from FDB-2a's two dates, never hardcoded. It comes to 1612, which
    is exactly the row count the pre-registrations record for this window
    ("train only, 1612 rows, 2021-01-01..2025-05-31; 365 lockbox rows
    excluded") -- a test asserts that agreement, so a drift in either direction
    is caught rather than absorbed.
    """
    start = date.fromisoformat(TRAIN_START)
    end = date.fromisoformat(TRAIN_END)
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]


def validate_train_slice(master) -> None:
    """Refuse a corpus that does not cover the frozen window, BEFORE any
    statistic is formed (D-5).

    Without this, an under-provisioned host proceeds on whatever months it
    happens to hold and fails later inside `member_sharpes` as
    `member_sharpe_uncomputable` -- naming a signal, when the real fault is a
    missing corpus. Both refusals are fail-closed, but only one of them is
    true, and an operator acting on the wrong one would go looking at the
    statistics instead of the provisioning.

    This never truncates the window to what is available, never substitutes a
    later end date, and never reaches past `TRAIN_END`: it is handed the
    already-sliced frame, so the sealed window is not present to be read.
    """
    if master is None or len(master) == 0:
        raise BatteryRefused(
            "corpus_incomplete",
            f"the frozen train slice {TRAIN_START}..{TRAIN_END} is empty. The "
            "producer does not truncate the window to whatever is available.",
            {"expected_days": len(expected_train_days()), "observed_days": 0},
        )
    for column in ("dt", "close"):
        if column not in master:
            raise BatteryRefused(
                "corpus_incomplete",
                f"the train-slice frame has no {column!r} column; it is not a "
                "master frame this producer can read.",
            )

    observed = {d.strftime("%Y-%m-%d") for d in master["dt"]}
    beyond = sorted(d for d in observed if d > TRAIN_END)
    if beyond:
        raise BatteryRefused(
            "corpus_beyond_train_slice",
            f"{len(beyond)} day(s) after {TRAIN_END} reached the statistical path, "
            f"earliest {beyond[0]}. FDB-2a's window is closed and the sealed "
            "lockbox begins the next day; this frame was not sliced.",
            {"beyond": beyond[:5]},
        )

    expected = expected_train_days()
    missing = [d for d in expected if d not in observed]
    if missing:
        raise BatteryRefused(
            "corpus_incomplete",
            f"the frozen train slice {TRAIN_START}..{TRAIN_END} is missing "
            f"{len(missing)} of {len(expected)} day(s), earliest {missing[0]}, "
            f"latest {missing[-1]}. This is a PROVISIONING fault, not a "
            "statistical one: no DSR/BH is attempted and no member Sharpe is "
            "blamed. Provision the missing partitions, or run the battery where "
            "the frozen corpus is complete.",
            {"expected_days": len(expected), "observed_days": len(observed),
             "missing_days": len(missing), "first_missing": missing[0],
             "last_missing": missing[-1]},
        )


def member_sharpes(master, specs) -> dict[str, float]:
    """Per-trade Sharpe for every one of the 34, on the train slice at
    hold_days=7. Refuses the WHOLE battery on the first member that cannot
    produce one -- never drops it, never substitutes a value (OD-2/T1)."""
    import pandas as pd

    days = [d.strftime("%Y-%m-%d") for d in master["dt"]]
    closes = [float(c) for c in master["close"]]

    out: dict[str, float] = {}
    for spec in specs:
        raw = spec.fn(master, **spec.params)
        positions = pd.Series(raw, index=master.index).reindex(master.index).fillna(0.0)
        trades = build_trades(days, closes, [float(p) for p in positions])
        sr = direct_sharpe([t.net_return for t in trades])
        if sr is None:
            raise BatteryRefused(
                "member_sharpe_uncomputable",
                f"library signal {spec.name!r} produced no valid per-trade Sharpe on "
                f"{TRAIN_START}..{TRAIN_END} (n={len(trades)}). FDB-1 fixes the "
                "population at 34 and forbids dropping, substituting or zeroing a "
                "member, so the entire battery is refused.",
                {"signal": spec.name, "n_trades": len(trades)},
            )
        out[spec.name] = sr
    return out


def dispersion(sharpes: dict[str, float]) -> float:
    """`stdev(ddof=1)` over ALL 34 member Sharpes."""
    values = list(sharpes.values())
    if len(values) != N_TRIALS:
        raise BatteryRefused(
            "dispersion_population",
            f"dispersion computed over {len(values)} members, not {N_TRIALS}. FDB-1: "
            "the population and n_trials may not diverge.",
        )
    mean = sum(values) / len(values)
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))
    if not (sd > 0.0) or math.isinf(sd) or math.isnan(sd):
        raise BatteryRefused(
            "dispersion_degenerate",
            f"sr_trials_std={sd!r} is not usable; deflated_sr would silently fall "
            "back to an undeflated sr_star=0.0",
        )
    return sd


# ---------------------------------------------------------------------------
# Battery membership (FDB-5 / FDB-7 / FDB-8)
# ---------------------------------------------------------------------------

@dataclass
class Member:
    family_id: str
    n: int
    minimum: int
    returns: list[float] = field(default_factory=list)


@dataclass
class Membership:
    members: list[Member]
    excluded: list[dict]


def resolve_membership(forward_db, as_of: str, minima: dict[str, int]) -> Membership:
    """Who is in the battery at this `as_of`, and why everyone else is not.

    Evaluated before any corpus work: an empty battery costs nothing to find.
    """
    import ledger_sync as ls

    missing = [f for f in FORWARD_FAMILIES if f not in minima]
    if missing:
        raise BatteryRefused(
            "minimum_not_supplied",
            f"no frozen DSR minimum supplied for {missing}. FDB-8: a minimum is "
            "never borrowed and never defaulted, and a family without one may not "
            "participate.",
            {"families": missing},
        )

    members: list[Member] = []
    excluded: list[dict] = []
    for family_id in FORWARD_FAMILIES:
        minimum = int(minima[family_id])
        trades = ls.load_closed_trades(forward_db, family_id, as_of)
        n = len(trades)
        if n < minimum:
            excluded.append({"family_id": family_id,
                             "reason": f"below_minimum(n={n}, min={minimum})"})
            continue
        if n < MIN_N_COMPUTABLE:
            excluded.append({"family_id": family_id,
                             "reason": f"below_computability_floor(n={n}, floor={MIN_N_COMPUTABLE})"})
            continue
        members.append(Member(family_id, n, minimum,
                              [float(t["net_return"]) for t in trades]))
    return Membership(members, excluded)


# ---------------------------------------------------------------------------
# The battery
# ---------------------------------------------------------------------------

@dataclass
class BatteryResult:
    status: str                       # 'computed' | 'refused'
    as_of: str
    members: list[str]
    excluded: list[dict]
    payloads: dict[str, dict] = field(default_factory=dict)
    reason: str | None = None
    detail: str | None = None

    @property
    def computed(self) -> bool:
        return self.status == "computed"


def compute_battery(
    forward_db,
    as_of: str,
    minima: dict[str, int],
    master=None,
    prereg: dict[str, dict] | None = None,
) -> BatteryResult:
    """The whole battery at one `as_of`. Writes nothing, anywhere.

    Refuses -- rather than raising -- when there is simply nobody eligible:
    an empty battery is not a failure (FDB-7). Every other refusal reason
    raises `BatteryRefused` at the point of detection and is caught here so a
    caller always receives a result object it can render.
    """
    import governance as G

    try:
        membership = resolve_membership(forward_db, as_of, minima)
    except BatteryRefused as exc:
        return BatteryResult("refused", as_of, [], [], reason=exc.reason, detail=exc.detail)

    if not membership.members:
        return BatteryResult(
            "refused", as_of, [], membership.excluded,
            reason="empty_battery",
            detail=("no family meets its frozen DSR minimum at this as_of. Nothing is "
                    "computed and nothing is written; absence is not a failure (FDB-7)."),
        )

    try:
        specs = library_specs()
        m = master if master is not None else _train_slice_master()
        # D-5: corpus completeness is established BEFORE any statistic, and for
        # an injected frame too -- an off-host caller must clear the same bar.
        validate_train_slice(m)
        sharpes = member_sharpes(m, specs)
        sr_trials_std = dispersion(sharpes)
    except BatteryRefused as exc:
        return BatteryResult(
            "refused", as_of, [x.family_id for x in membership.members],
            membership.excluded, reason=exc.reason, detail=exc.detail)

    names = [sp.name for sp in specs]
    basis = {
        "population": "34 Phase B library signals (signals.REGISTRY seeds..group6)",
        "population_size": len(names),
        "population_members": names,
        "population_hash": population_hash(names),
        "units": "per-trade",
        "hold_days": HOLD_DAYS,
        "cost_bps": COST_BPS,
        "window": [TRAIN_START, TRAIN_END],
        "conversion_applied": "none",
        "construction_rule": (
            "runner_base.run_signal 5a/5b replay; signed direction; "
            "non-overlapping; unconditional calendar-day exit"
        ),
    }

    raw_p: dict[str, float] = {}
    per_family: dict[str, dict] = {}
    for mem in membership.members:
        import numpy as np

        arr = np.asarray(mem.returns, dtype=float)
        dsr_prob, sr_star, sr_obs = G.deflated_sr(arr, sr_trials_std, N_TRIALS)
        psr0, _ = G.psr(arr, 0.0)
        if math.isnan(psr0) or math.isnan(dsr_prob):
            return BatteryResult(
                "refused", as_of, [x.family_id for x in membership.members],
                membership.excluded, reason="statistic_nan",
                detail=f"{mem.family_id}: psr0/dsr_prob is NaN at n={mem.n}")
        raw_p[mem.family_id] = 1.0 - psr0
        per_family[mem.family_id] = {
            "dsr_prob": float(dsr_prob),
            "psr0": float(psr0),
            "sr_star": float(sr_star),
            "sr_observed": float(sr_obs),
            "n": mem.n,
        }

    adjusted = G.bh_adjusted_p(raw_p)
    ranked = G.bh_fdr(raw_p, q=Q)

    battery_members = [
        {"family_id": mem.family_id, "n": mem.n, "minimum": mem.minimum,
         "p": float(raw_p[mem.family_id]),
         "rank": ranked[mem.family_id]["rank"],
         "bh_threshold": ranked[mem.family_id]["bh_threshold"],
         "reject": bool(ranked[mem.family_id]["reject"])}
        for mem in membership.members
    ]

    payloads: dict[str, dict] = {}
    for mem in membership.members:
        f = per_family[mem.family_id]
        block = {
            "schema": PAYLOAD_SCHEMA,
            "as_of": as_of,
            "calc_version": CALC_VERSION,
            "return_basis": RETURN_BASIS,
            "n": f["n"],
            "n_trials": N_TRIALS,
            "q": Q,
            "psr0": f["psr0"],
            "sr_star": f["sr_star"],
            "sr_observed": f["sr_observed"],
            "sr_trials_std": sr_trials_std,
            "sr_trials_std_basis": basis,
            "battery_members": battery_members,
            "battery_excluded": membership.excluded,
        }
        if prereg and mem.family_id in prereg:
            block["preregistration"] = prereg[mem.family_id]
        payloads[mem.family_id] = {
            "block": block,
            "governance": {"dsr_prob": f["dsr_prob"],
                           "bh_p": float(adjusted[mem.family_id])},
        }

    return BatteryResult("computed", as_of,
                         [x.family_id for x in membership.members],
                         membership.excluded, payloads=payloads)


# ---------------------------------------------------------------------------
# Operator entry point -- diagnostic only; this module never writes
# ---------------------------------------------------------------------------

def _parse_minima(pairs: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SystemExit(f"--minimum expects family=N, got {pair!r}")
        fam, _, value = pair.partition("=")
        out[fam.strip()] = int(value)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Forward DSR/BH battery -- diagnostic, non-writing.")
    p.add_argument("--forward-db", type=pathlib.Path, required=True)
    p.add_argument("--as-of", required=True, metavar="YYYY-MM-DD")
    p.add_argument("--actor", required=True)
    p.add_argument("--actor-type", default="human")
    p.add_argument("--minimum", action="append", metavar="family=N",
                   help="REQUIRED, once per forward family. No defaults exist: "
                        "FDB-8 forbids borrowing or defaulting a minimum.")
    p.add_argument("--out", type=pathlib.Path, default=None,
                   help="write the full battery result, payloads included, to "
                        "this JSON file for forward_snapshot --dsr-payload. "
                        "Writes a FILE only -- never the ledger.")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)sZ %(levelname)s %(message)s")
    if not args.actor:
        raise ActorRefusedError("a battery computation requires a named actor")
    if args.actor_type.lower() in _REFUSED_ACTOR_TYPES:
        raise ActorRefusedError(
            f"actor_type={args.actor_type!r} is refused: DSR/BH is operator-invoked "
            "and must never run from an unattended process (FDB-11)")

    result = compute_battery(args.forward_db, args.as_of, _parse_minima(args.minimum))
    summary = {
        "status": result.status,
        "as_of": result.as_of,
        "reason": result.reason,
        "detail": result.detail,
        "members": result.members,
        "excluded": result.excluded,
    }
    if args.out is not None:
        pathlib.Path(args.out).write_text(
            json.dumps({**summary, "payloads": result.payloads},
                       indent=2, sort_keys=True, default=str),
            encoding="utf-8")
        print(f"battery written to {args.out}")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
