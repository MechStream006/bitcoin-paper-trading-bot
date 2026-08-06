"""
Convention Specification registration (ADR-I011 §8.3, §16.1 applied, PS1,
PS2; Phase 3 Label Store roadmap v1.1.0, WBS 3.3 -- the third Label Store
milestone).

Registers the THREE real fill conventions this repository actually uses --
not the three the ADR's own text names (§1's "next-open with per-side
slippage" does not exist anywhere in this codebase; WBS 3.1's audit
finding `A-2` replaces it with the verified inventory below) -- plus
`ret_fwd1` as the research-tier Specification claiming it for the Label
Store (`S-6`).

RECONVENTION (§11.1), NOT THREE UNRELATED OUTCOMES. C1/C2/C3 all measure
the SAME semantic outcome -- "the return a directional position would have
earned, net of cost" -- via three different fill/cost/direction
conventions. Per §11.1: "Reconvention: same outcome, different
fill/cost/direction convention -> a DIFFERENT Specification[, the SAME
Definition]." All three therefore share one Label Definition
(`trade_forward_return`); `ret_fwd1` measures a genuinely different
outcome (a raw spot-price differential with no position, no cost, no
direction) and gets its own Definition (`spot_one_day_forward_return`).

SOURCE OF TRUTH FOR EVERY DECLARED FIELD BELOW. Every convention field
(fill/cost/direction/maturity/validity) is transcribed from having read
the real, current, unmodified source -- never guessed or copied from the
ADR's own (partly incorrect, per `A-2`) text:
  - C1: research/forward_evidence/daily_job.py, _open_trade()/_close_trade()
  - C2: research/evidence/engine.py, _net_daily()
  - C3: research/forward_evidence/runner_base.py + schema_forward.sql's
    trade_returns VIEW
  - ret_fwd1: research/evidence/engine.py, load_master(), line computing
    m["ret_fwd1"]

NO FORWARD DATA TOUCHED (this milestone's own exit criterion). This script
registers Specifications only -- it never reads forward_evidence.db or
evidence_forward.db, never calls anything in
research/label_store/value_store.py, and never calls
register_binding()/activate_specification(). A Specification's declared
binding_id/engine_version is recorded as identity only; resolving it to a
real callable and demonstrating equivalence against real data is
deliberately left to a later milestone (mirrors WBS 3.1's registry.py
scope: Tier A validates field completeness without validating binding
equivalence, a later caller's job).

CONDITIONING PROPAGATION. All four Specifications declare "preserving" --
every one is built from already-conformed Phase 1 ingest sources (spot
close, in every case). This mirrors WBS 2.4's identical choice for all 17
Feature Specifications, made before WBS 2.7 wired real conditioning
resolution; WBS 3.7 does the equivalent wiring here, later, not this
milestone.

HORIZON GOVERNANCE (LR7, §17's calendar trap). All four declare
"calendar_span" -- every one is computed via literal calendar-day
arithmetic in its source (`day_ts + HOLD_DAYS * 86_400_000`,
`JULIANDAY(exit_date) - JULIANDAY(entry_date)`, one row per calendar day),
not a count of N observations independent of gaps. BTC trades 24/7, so
this coincides with observation-count in practice, but the Specification
still states which governs, per §17's own instruction.

Usage:
    python register_conventions.py              # dry run
    python register_conventions.py --commit      # writes

Idempotent (registry.py's own content-address deduplication); requires
--commit to write; prints a full plan before writing -- matching this
repository's established migrate_*.py precedent
(migrate_evidence_engine_features.py). Lower risk profile than that
precedent for the same reason stated there: label_store.db is a
brand-new, empty database with no pre-existing consumers -- the
dry-run/--commit gate is kept for consistency and reviewability, not
because production data is at risk.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_DB_PATH = _THIS_DIR / "label_store.db"

sys.path.insert(0, str(_THIS_DIR))
import registry as reg  # noqa: E402

ENGINE_VERSION = "v1"
ACTOR = "register_conventions"
NOW = "2026-08-05T00:00:00+00:00"

# ---------------------------------------------------------------------------
# Label Definitions
# ---------------------------------------------------------------------------

DEFINITIONS: list[dict] = [
    dict(
        name="trade_forward_return",
        rationale=(
            "The return earned by holding a directional position over a forward horizon, "
            "net of transaction cost -- the outcome measured by every position-based "
            "trade convention this repository actually runs."
        ),
        interpretation=(
            "Answers 'what would a position implied by this signal have earned', as "
            "distinct from ret_fwd1's raw spot-price differential (no position, no cost). "
            "The three Specifications registered under this Definition are the three ways "
            "this repository computes it (PS1) -- Reconvention (ADR-I011 §11.1): same "
            "outcome, different fill/cost/direction convention, different Specification, "
            "same Definition."
        ),
    ),
    dict(
        name="spot_one_day_forward_return",
        rationale=(
            "The raw one-day-forward simple return of the underlying spot close: "
            "close[t+1] / close[t] - 1. No position, no cost, no direction -- the building "
            "block C2 (research/evidence/engine.py::_net_daily()) multiplies by position "
            "and nets of cost to produce trade_forward_return under convention C2."
        ),
        interpretation=(
            "Underpins all 70 ledger evaluation runs (research/evidence/engine.py's "
            "load_master(), ret_fwd1 column) as the research label this repository has "
            "computed in memory since before any Label Store existed (ADR-I011 §1's "
            "audited reference platform). Registering it here claims it for the Label "
            "Store (S-6) -- it was previously reserved and excluded from the Feature Store "
            "by an import-time assertion (migrate_evidence_engine_features.py), precisely "
            "because a label depends on data after t and a feature does not (ADR-I011 "
            "§1.1's Central Asymmetry)."
        ),
    ),
]

# ---------------------------------------------------------------------------
# Label Specifications
# ---------------------------------------------------------------------------

SPECIFICATIONS: list[dict] = [
    dict(
        label="C1",
        definition_name="trade_forward_return",
        horizon_days=7,  # HOLD_DAYS (research/forward_evidence/decision_function.py)
        horizon_governance="calendar_span",
        fill_convention=(
            "Same-day spot close, both sides: entry_price at trade open, exit_price at the "
            "fixed HOLD_DAYS exit -- both read from the daily close "
            "(research/forward_evidence/daily_job.py _open_trade()/_close_trade())."
        ),
        cost_convention=(
            "Flat 15 bps (COST_BPS), charged once at exit: net = gross - COST_BPS / 10_000.0."
        ),
        direction_convention=(
            "Long-only, hardcoded: every trade_entries.direction = 1. No short trades exist "
            "in this pipeline."
        ),
        maturity_rule=(
            "Matures when the scheduled HOLD_DAYS exit is reached and trade_exits is written "
            "(exit_reason='scheduled') -- a single fixed exit, not a multi-horizon fill."
        ),
        validity_condition=(
            "Valid once both entry_price (trade_entries) and exit_price (trade_exits) are "
            "recorded for the trade."
        ),
        conditioning_propagation="preserving",
        binding_id="forward_evidence.daily_job.close_trade",
    ),
    dict(
        label="C2",
        definition_name="trade_forward_return",
        horizon_days=1,
        horizon_governance="calendar_span",
        fill_convention=(
            "Close-to-close, applied daily: position[t] held into day t+1 earns "
            "ret_fwd1[t] = close[t+1]/close[t] - 1 (research/evidence/engine.py "
            "_net_daily()), compounded by summing net_daily() across every day a position "
            "is held."
        ),
        cost_convention=(
            "Turnover-based: cost_bps * |position[t] - position[t-1]|, charged on every "
            "daily position change -- a held position costs nothing to maintain, unlike "
            "C1/C3's flat per-trade charge."
        ),
        direction_convention=(
            "Signed continuous position: position[t] from the strategy's own position "
            "array (via _apply_hold()), long, short, or flat."
        ),
        maturity_rule=(
            "Matures at the close of the day the return applies to -- ret_fwd1[t] requires "
            "close[t+1]; computed directly from the corpus at read time, with no separate "
            "placeholder/fill step."
        ),
        validity_condition=(
            "Valid wherever ret_fwd1 is not NaN, i.e. every row except the corpus's final "
            "day (no t+1 close exists yet)."
        ),
        conditioning_propagation="preserving",
        binding_id="research_evidence.engine.net_daily",
    ),
    dict(
        label="C3",
        definition_name="trade_forward_return",
        horizon_days=7,  # HOLD_DAYS (research/forward_evidence/decision_function.py)
        horizon_governance="calendar_span",
        fill_convention=(
            "Close-to-close per trade: entry_close_price and exit_close_price, both "
            "spot/OHLCV close (research/forward_evidence/runner_base.py's two collection "
            "paths)."
        ),
        cost_convention=(
            "Flat cost_bps per trade, charged once: net_return = gross_return - "
            "cost_bps / 10000.0, where gross_return is direction-adjusted."
        ),
        direction_convention=(
            "Explicit +-1 column: trade_entries.direction, CHECK-constrained, no default -- "
            "supports both long and short."
        ),
        maturity_rule=(
            "Matures when trade_exits is written for the entry (UNIQUE(entry_id): exactly "
            "one exit per entry); the trade_returns VIEW then serves the computed columns."
        ),
        validity_condition="Valid once a trade_exits row exists for the entry_id.",
        conditioning_propagation="preserving",
        binding_id="forward_evidence.runner_base.trade_returns_view",
    ),
    dict(
        label="ret_fwd1",
        definition_name="spot_one_day_forward_return",
        horizon_days=1,
        horizon_governance="calendar_span",
        fill_convention=(
            "Single price differential, not a trade: entry = close[t], exit = close[t+1], "
            "both spot close. No order fill or slippage model of any kind."
        ),
        cost_convention="None -- zero transaction cost is modelled or subtracted.",
        direction_convention=(
            "Unsigned: the return is reported as-is (close[t+1]/close[t] - 1), with no "
            "direction multiplier -- there is no position concept to sign."
        ),
        maturity_rule=(
            "Immediate -- fully mature at computation over a fixed historical corpus "
            "(ADR-I011 §8.1: Research Labels are 'Fully mature at computation'). No "
            "pending/waiting state applies; this is a research label, not a forward label."
        ),
        validity_condition=(
            "Valid wherever close[t+1] exists, i.e. every row except the corpus's final day."
        ),
        conditioning_propagation="preserving",
        binding_id="research_evidence.engine.ret_fwd1",
    ),
]

_SPEC_LABELS = {s["label"] for s in SPECIFICATIONS}
assert _SPEC_LABELS == {"C1", "C2", "C3", "ret_fwd1"}, (
    "SPECIFICATIONS must register exactly C1, C2, C3, and ret_fwd1 -- no more, no fewer "
    "(WBS 3.3's own exit criterion: four Specifications, not an open-ended list)"
)
assert {s["definition_name"] for s in SPECIFICATIONS if s["label"] in ("C1", "C2", "C3")} == {
    "trade_forward_return"
}, "C1/C2/C3 must share one Definition (Reconvention, ADR-I011 §11.1)"


def plan() -> list[dict]:
    """Pure: describes what would be registered, without writing anything."""
    return [
        {"label": s["label"], "definition_name": s["definition_name"],
         "horizon_days": s["horizon_days"], "binding_id": s["binding_id"]}
        for s in SPECIFICATIONS
    ]


def print_plan() -> None:
    print(f"{'label':<10} {'definition':<28} {'horizon_days':<14} {'binding_id'}")
    for row in plan():
        print(f"{row['label']:<10} {row['definition_name']:<28} {row['horizon_days']:<14} {row['binding_id']}")
    print(f"\n{len(SPECIFICATIONS)} specification(s) would be registered "
          f"under {len(DEFINITIONS)} definition(s).")


def _get_or_register_definition(db_path: Path, name: str, rationale: str, interpretation: str) -> reg.LabelDefinitionHandle:
    """Idempotency guard, mirroring migrate_evidence_engine_features.py's
    identical precedent (INC-0002): register_definition() always creates
    the NEXT version unconditionally, which is correct for a direct human
    call but would silently duplicate the registry on every migration
    re-run. Reuses the latest version when its content is unchanged;
    registers a new version only if rationale/interpretation genuinely
    changed."""
    existing = reg.get_latest_definition(db_path, name)
    if existing is not None and existing["rationale"] == rationale and existing["interpretation"] == interpretation:
        return reg.LabelDefinitionHandle(name=existing["name"], version=existing["version"])
    return reg.register_definition(db_path, name, rationale, interpretation, ACTOR, NOW)


def run(db_path: Path) -> list[dict]:
    """
    Registers the two Label Definitions and four Label Specifications.
    Returns a list of {"label", "spec_hash", "phase"} results.

    Idempotent: re-running reuses existing, content-identical Definition
    versions (_get_or_register_definition()) and resolves to the same
    content-addressed Specification hash on every Specification
    (registry.py's own duplicate-content handling) -- a second run writes
    nothing new.

    Every Specification is left at phase "Registered" -- this milestone
    does not call activate_specification() (no binding is resolved or
    executed; see the module docstring's "NO FORWARD DATA TOUCHED" note).
    """
    reg.init_db(db_path)

    definitions: dict[str, reg.LabelDefinitionHandle] = {}
    for d in DEFINITIONS:
        definitions[d["name"]] = _get_or_register_definition(
            db_path, d["name"], d["rationale"], d["interpretation"],
        )

    results: list[dict] = []
    for s in SPECIFICATIONS:
        definition = definitions[s["definition_name"]]
        spec_hash = reg.register_specification(
            db_path,
            definition_name=definition.name,
            definition_version=definition.version,
            horizon_days=s["horizon_days"],
            horizon_governance=s["horizon_governance"],
            fill_convention=s["fill_convention"],
            cost_convention=s["cost_convention"],
            direction_convention=s["direction_convention"],
            maturity_rule=s["maturity_rule"],
            validity_condition=s["validity_condition"],
            conditioning_propagation=s["conditioning_propagation"],
            binding_id=s["binding_id"],
            engine_version=ENGINE_VERSION,
            actor=ACTOR,
            registered_at=NOW,
        )
        spec = reg.get_specification(db_path, spec_hash)
        results.append({"label": s["label"], "spec_hash": spec_hash, "phase": spec["phase"]})

    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--commit", action="store_true", help="Write the registrations (default: dry run)")
    parser.add_argument("--db", default=str(_DB_PATH), help="Path to label_store.db")
    args = parser.parse_args(argv)

    print_plan()

    if not args.commit:
        print("\nDry run only. Re-run with --commit to write.")
        return 0

    results = run(Path(args.db))
    print()
    for r in results:
        print(f"  {r['label']:<10} -> {r['spec_hash'][:16]}...  phase={r['phase']}")
    hashes = {r["spec_hash"] for r in results}
    print(f"\n{len(results)} specification(s) registered, {len(hashes)} distinct hash(es).")
    return 0 if len(hashes) == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
