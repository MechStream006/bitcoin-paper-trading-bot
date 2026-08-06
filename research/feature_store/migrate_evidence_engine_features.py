"""
Feature Specification migration: research/evidence/engine.py's load_master()
derived columns -> registered Feature Specifications (ADR-I010; engineering
roadmap Phase 2, WBS 2.4).

WRAP, DON'T REWRITE. research/evidence/engine.py is not modified by this
milestone at all. Every migrated column's binding is a thin extraction
function in evidence_engine_bindings.py that calls the existing, unmodified
load_master() -- the computation itself is reused verbatim, never
reimplemented, matching the roadmap's own instruction exactly ("the
features are not rewritten, only wrapped: existing function becomes the
Specification's executable binding").

EXCLUDED, WITH REASON: load_master()'s `ret_fwd1` column is NOT migrated.
ADR-I011 §1.1's Central Asymmetry: "A feature is a deterministic function
of the corpus up to t... A label depends on data after t." ret_fwd1 is
computed via `m["close"].shift(-1)` -- it depends on data AFTER t by
construction. Registering it as a Feature Specification would violate the
very type distinction ADR-I010/ADR-I011 establish (this is not a hypothetical
concern -- it is the exact distinction those two ADRs exist to enforce).
ret_fwd1 belongs to a future Label Store migration (Phase 3), not this one.
17 of load_master()'s 18 derived columns are migrated; this is the one
deliberate, documented exclusion.

EQUIVALENCE (Tier B, per this session's explicit requirement that "every
migrated feature must demonstrate behavioural equivalence with its current
implementation"): each Specification's activation check calls its binding
function AND independently calls engine.load_master() directly, then
compares the two column extractions for exact equality (via a JSON-string
comparison, which is NaN-safe -- see _equivalence_check()'s docstring for
why plain list `==` is NOT safe here). A real behavioural difference
between the wrapped binding and calling load_master() directly -- e.g. a
wrong column name, an off-by-one, an accidental transformation -- fails
this check and the Specification is Rejected, a permanent record (FG3).

WARM-UP PERIODS are read directly from engine.py's own module-level
constants (RV_WINDOW, REGIME_WINDOW, market_regimes.SMA_WINDOW +
SLOPE_LAG, etc.) or engine.py's own inline literals, cited per entry below
-- not independently re-derived or guessed.

Usage:
    python migrate_evidence_engine_features.py              # dry run
    python migrate_evidence_engine_features.py --commit      # writes

Properties, matching this repository's established migrate_*.py precedent:
    - Idempotent: re-running after a successful --commit re-registers the
      same content-addressed Specifications (a no-op, per registry.py's
      own duplicate-content handling) and re-activates only what is not
      already Active.
    - Requires --commit to write anything; the default is always a dry run.
    - Prints a full plan before writing.
    - Lower risk profile than the ledger.db migrate_*.py scripts this
      precedent is drawn from: feature_store.db is a brand-new, empty
      database with no pre-existing consumers, unlike ledger.db's 70 runs
      / 26 receipts of governed history -- no backup step is needed because
      there is nothing yet to lose. The dry-run/--commit gate is kept for
      consistency and reviewability, not because production data is at risk.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_DB_PATH = _THIS_DIR / "feature_store.db"

sys.path.insert(0, str(_THIS_DIR))
import evidence_engine_bindings as bindings  # noqa: E402
import registry as reg  # noqa: E402
import warmup  # noqa: E402
import conditioning as cond  # noqa: E402

ENGINE_VERSION = "v1"
BINDING_PREFIX = "evidence_engine"
DEFINITION_NAME_PREFIX = ""  # feature names are used as-is; no prefix needed
ACTOR = "migrate_evidence_engine_features"
NOW = "2026-08-05T00:00:00+00:00"  # registration timestamp for this migration run

# ---------------------------------------------------------------------------
# Declarative migration table. One entry per load_master() derived column
# being migrated (17 of 18 -- ret_fwd1 excluded, see module docstring).
# warmup_periods cites the exact engine.py constant or literal it is drawn
# from, so this table can be checked against the source rather than trusted
# blindly.
# ---------------------------------------------------------------------------

FEATURES: list[dict] = [
    dict(
        name="basis_bps",
        rationale="Perp basis vs spot, in basis points; positive = contango (perp rich). "
                   "engine.py: m['basis_bps'] = (perp_close/close - 1) * 10_000.",
        intended_role="basis/carry regime input",
        conditioning_rule="preserving",  # ADR-I010 §9.1 -- WBS 2.7; declared per Specification
        input_requirements=[
            {"source": "spot_1m", "fields": ["close"]},
            {"source": "perp_1m", "fields": ["close"]},
        ],
        warmup_periods=0,  # pointwise; no rolling window
    ),
    dict(
        name="trend_1h",
        rationale="1-hour timeframe 50/200 MA crossover trend state, sampled to end-of-day.",
        intended_role="multi-timeframe trend structure input",
        conditioning_rule="preserving",  # ADR-I010 §9.1 -- WBS 2.7; declared per Specification
        input_requirements=[{"source": "spot_1m", "fields": ["close"]}],
        warmup_periods=200,  # slow MA in _tf_trend_daily(df_1h, 50, 200)
    ),
    dict(
        name="trend_4h",
        rationale="4-hour timeframe 50/200 MA crossover trend state, sampled to end-of-day.",
        intended_role="multi-timeframe trend structure input",
        conditioning_rule="preserving",  # ADR-I010 §9.1 -- WBS 2.7; declared per Specification
        input_requirements=[{"source": "spot_1m", "fields": ["close"]}],
        warmup_periods=200,  # slow MA in _tf_trend_daily(df_4h, 50, 200)
    ),
    dict(
        name="trend_1d",
        rationale="Daily 50/200 MA crossover trend state (same construction as trend_1h/4h).",
        intended_role="multi-timeframe trend structure input",
        conditioning_rule="preserving",  # ADR-I010 §9.1 -- WBS 2.7; declared per Specification
        input_requirements=[{"source": "spot_1m", "fields": ["close"]}],
        warmup_periods=200,  # f1d/s1d rolling(50)/rolling(200)
    ),
    dict(
        name="log_ret",
        rationale="Daily log return -- the building block for realised volatility.",
        intended_role="return series input",
        conditioning_rule="preserving",  # ADR-I010 §9.1 -- WBS 2.7; declared per Specification
        input_requirements=[{"source": "spot_1m", "fields": ["close"]}],
        warmup_periods=1,
    ),
    dict(
        name="rv_21d",
        rationale="21-day realised volatility (log-return std, annualised by sqrt(365)).",
        intended_role="volatility regime input",
        conditioning_rule="preserving",  # ADR-I010 §9.1 -- WBS 2.7; declared per Specification
        input_requirements=[{"source": "spot_1m", "fields": ["close"]}],
        warmup_periods=21,  # RV_WINDOW
    ),
    dict(
        name="vol_regime",
        rationale="Rolling-tercile realised-vol regime label (high/medium/low), "
                   "252-day backward window over rv_21d.",
        intended_role="volatility regime classification",
        conditioning_rule="preserving",  # ADR-I010 §9.1 -- WBS 2.7; declared per Specification
        input_requirements=[{"source": "spot_1m", "fields": ["close"]}],
        warmup_periods=273,  # RV_WINDOW(21) + REGIME_WINDOW(252)
    ),
    dict(
        name="funding_regime",
        rationale="Rolling-tercile funding-rate regime label (high/medium/low), "
                   "252-day backward window.",
        intended_role="funding regime classification",
        conditioning_rule="preserving",  # ADR-I010 §9.1 -- WBS 2.7; declared per Specification
        input_requirements=[{"source": "funding_raw", "fields": ["funding_rate"]}],
        warmup_periods=252,  # REGIME_WINDOW
    ),
    dict(
        name="market_regime",
        rationale="SMA200 + 20-day-slope market regime label (bull/bear/sideways), "
                   "per hypotheses.shared.market_regimes.build_market_regimes().",
        intended_role="trend regime classification",
        conditioning_rule="preserving",  # ADR-I010 §9.1 -- WBS 2.7; declared per Specification
        input_requirements=[{"source": "spot_1m", "fields": ["close"]}],
        warmup_periods=220,  # market_regimes.py: SMA_WINDOW(200) + SLOPE_LAG(20)
    ),
    dict(
        name="dvol_frac",
        rationale="Deribit DVOL (30-day forward implied-vol index) converted from vol "
                   "points to a fraction (dvol_close / 100).",
        intended_role="implied-vol level input",
        conditioning_rule="preserving",  # ADR-I010 §9.1 -- WBS 2.7; declared per Specification
        input_requirements=[{"source": "dvol_daily", "fields": ["close"]}],
        warmup_periods=0,
    ),
    dict(
        name="rv30",
        rationale="30-day realised volatility, annualised -- horizon-matched to DVOL.",
        intended_role="realised-vol input, DVOL-comparable horizon",
        conditioning_rule="preserving",  # ADR-I010 §9.1 -- WBS 2.7; declared per Specification
        input_requirements=[{"source": "spot_1m", "fields": ["close"]}],
        warmup_periods=30,
    ),
    dict(
        name="atr_ratio",
        rationale="ATR(14) vs ATR(14) 10 bars prior, ratio-of-change (volatility-dynamics "
                   "feature).",
        intended_role="volatility dynamics input",
        conditioning_rule="preserving",  # ADR-I010 §9.1 -- WBS 2.7; declared per Specification
        input_requirements=[{"source": "spot_1m", "fields": ["high", "low", "close"]}],
        warmup_periods=24,  # atr14 window(14) + shift(10)
    ),
    dict(
        name="dvol_pctile",
        rationale="DVOL trailing percentile rank, 252-day backward window "
                   "(min_periods=126).",
        intended_role="implied-vol regime input",
        conditioning_rule="preserving",  # ADR-I010 §9.1 -- WBS 2.7; declared per Specification
        input_requirements=[{"source": "dvol_daily", "fields": ["close"]}],
        warmup_periods=126,  # min_periods used by engine.py's own rolling().apply()
    ),
    dict(
        name="dvol_ivrank",
        rationale="DVOL min-max IV-rank, 252-day backward window (min_periods=126).",
        intended_role="implied-vol regime input",
        conditioning_rule="preserving",  # ADR-I010 §9.1 -- WBS 2.7; declared per Specification
        input_requirements=[{"source": "dvol_daily", "fields": ["close"]}],
        warmup_periods=126,
    ),
    dict(
        name="vol_of_vol",
        rationale="21-day std of DVOL daily log-changes (vol-of-vol).",
        intended_role="vol-of-vol input",
        conditioning_rule="preserving",  # ADR-I010 §9.1 -- WBS 2.7; declared per Specification
        input_requirements=[{"source": "dvol_daily", "fields": ["close"]}],
        warmup_periods=22,  # 21d window + 1 for the log-difference itself
    ),
    dict(
        name="vrp_spread",
        rationale="Variance risk premium: DVOL fraction minus RV30 (spread form).",
        intended_role="variance risk premium input, primary",
        conditioning_rule="preserving",  # ADR-I010 §9.1 -- WBS 2.7; declared per Specification
        input_requirements=[
            {"source": "dvol_daily", "fields": ["close"]},
            {"source": "spot_1m", "fields": ["close"]},
        ],
        warmup_periods=30,  # bound by rv30's own warmup
    ),
    dict(
        name="vrp_logratio",
        rationale="Variance risk premium: log(DVOL fraction / RV30) (log-ratio variant).",
        intended_role="variance risk premium input, variant",
        conditioning_rule="preserving",  # ADR-I010 §9.1 -- WBS 2.7; declared per Specification
        input_requirements=[
            {"source": "dvol_daily", "fields": ["close"]},
            {"source": "spot_1m", "fields": ["close"]},
        ],
        warmup_periods=30,
    ),
]

_MIGRATED_NAMES = {f["name"] for f in FEATURES}
assert _MIGRATED_NAMES == set(bindings.BINDINGS.keys()), (
    "FEATURES and evidence_engine_bindings.BINDINGS have drifted apart -- "
    "every declared feature must have exactly one binding function and "
    "vice versa"
)
assert "ret_fwd1" not in _MIGRATED_NAMES, (
    "ret_fwd1 is a label (ADR-I011 Central Asymmetry), not a feature -- "
    "must never be migrated here"
)


def _equivalence_check(binding_fn, column_name: str):
    """
    Tier B (ADR-I010 §13): the wrapped binding must produce IDENTICAL
    output to calling load_master() directly. Compared via JSON-string
    equality, not Python list `==` -- NaN != NaN in Python, so a naive
    list comparison would report two IDENTICAL NaN-laden feature series as
    unequal. json.dumps() renders NaN as the literal token "NaN" both
    times; string equality of that rendering is NaN-safe without adding
    any NaN-handling logic of our own.
    """
    # Reuse the exact module object evidence_engine_bindings.py itself loaded
    # (see that module's _load_evidence_engine() docstring for why a bare
    # "import engine" here is unsafe -- it can silently resolve to
    # research/dq/engine.py instead, depending on process import order).
    engine = bindings.engine

    from_binding = binding_fn()
    from_direct = engine.load_master()[column_name].tolist()
    return json.dumps(from_binding, sort_keys=True) == json.dumps(from_direct, sort_keys=True)


def plan() -> list[dict]:
    """Pure: describes what would be registered, without writing anything."""
    return [
        {
            "name": f["name"],
            "binding_id": f"{BINDING_PREFIX}.{f['name']}",
            "engine_version": ENGINE_VERSION,
            "warmup_periods": f["warmup_periods"],
        }
        for f in FEATURES
    ]


def print_plan() -> None:
    print(f"{'name':<18} {'binding_id':<28} {'warmup_periods'}")
    for row in plan():
        print(f"{row['name']:<18} {row['binding_id']:<28} {row['warmup_periods']}")
    print(f"\n{len(FEATURES)} feature(s) would be registered and activated.")
    print("Excluded (label, not a feature -- see module docstring): ret_fwd1")


def _get_or_register_definition(
    db_path: Path, name: str, rationale: str, intended_role: str,
) -> reg.FeatureDefinitionHandle:
    """
    Idempotency guard (INC-0002): register_definition() always creates the
    NEXT version unconditionally (correct for a direct human call -- see
    its own docstring). Calling it unconditionally on every migration run
    would create a new Definition version -- and therefore a new
    content-addressed Specification hash, since definition_version is part
    of that hash -- on every single re-run, silently duplicating the
    registry. This checks for an existing, content-identical latest
    version first and reuses it; a NEW version is registered only if the
    rationale or intended_role has genuinely changed.
    """
    existing = reg.get_latest_definition(db_path, name)
    if existing is not None and existing["rationale"] == rationale and existing["intended_role"] == intended_role:
        return reg.FeatureDefinitionHandle(name=existing["name"], version=existing["version"])
    return reg.register_definition(db_path, name, rationale, intended_role, ACTOR, NOW)


def run(db_path: Path, *, warmup_mode: str | None = None) -> list[dict]:
    """
    Registers a Feature Definition + Feature Specification for every entry
    in FEATURES, registers its binding, and activates it (Tier B). Returns
    a list of {"name", "spec_hash", "phase", "warmup_action"} results.

    Idempotent: re-running reuses the existing Definition version when its
    content is unchanged (_get_or_register_definition(), see INC-0002),
    which resolves to the same content-addressed Specification hash
    (registry.py's own duplicate-content handling), and skips re-activation
    of an already-Active spec.

    Warm-up (WBS 2.6, ADR-I010 §8.4): before registering/activating each
    feature, its declared warmup_periods is checked (warmup.check_warmup())
    against the real corpus's available history (len(load_master())) --
    the resolution this migration script performs is the only real
    "resolve()-shaped" consumer of these Specifications that exists in
    this codebase today, so it is where Tier C is exercised. `warmup_mode`
    defaults to whatever warmup_policy.yaml currently declares (the
    rollout default is "warn": never blocks registration, only annotates
    the result). In "refuse" mode, a feature whose available history is
    insufficient is SKIPPED (not registered), reported with phase
    "Warmup-Refused" -- one refused feature does not abort the batch.

    Conditioning propagation (WBS 2.7, ADR-I010 §9.1): after the warm-up
    check, each feature's declared `conditioning_rule` and
    `input_requirements` are resolved to a real, propagated conditioning
    statement (cond.conditioning_for_specification()), read from the real
    ingest writers' own recorded population statements. Per §9.1's own
    words -- "a Specification that cannot state its propagation rule
    cannot be registered" -- an unresolvable source is a hard registration
    blocker, unlike warm-up: there is no rollout flag for this check,
    since (unlike warm-up's real historical-data-availability risk) a
    declared source either has a known conditioning statement or it
    doesn't, with no plausible legitimate reason to disable the check.
    A feature whose conditioning cannot be resolved is SKIPPED, reported
    with phase "Conditioning-Refused" -- one refusal does not abort the
    batch. Every one of the 17 migrated features' declared sources are
    known today, so this never fires against the real FEATURES table.
    """
    reg.init_db(db_path)
    results: list[dict] = []
    available_periods = len(bindings.engine.load_master())

    for f in FEATURES:
        try:
            warmup_result = warmup.check_warmup(
                spec_name=f["name"],
                warmup_periods=f["warmup_periods"],
                available_periods=available_periods,
                mode=warmup_mode,
            )
        except warmup.WarmupRefusal as exc:
            print(f"  REFUSED (warm-up): {exc}")
            results.append({
                "name": f["name"], "spec_hash": None, "phase": "Warmup-Refused",
                "warmup_action": "refused",
            })
            continue

        if warmup_result.action == "proceed_with_warning":
            print(f"  WARNING: {warmup_result.detail}")

        try:
            conditioning_result = cond.conditioning_for_specification(
                conditioning_rule=f["conditioning_rule"],
                input_requirements=f["input_requirements"],
            )
        except cond.ConditioningPropagationError as exc:
            print(f"  REFUSED (conditioning): {f['name']}: {exc}")
            results.append({
                "name": f["name"], "spec_hash": None, "phase": "Conditioning-Refused",
                "warmup_action": warmup_result.action,
            })
            continue

        definition = _get_or_register_definition(
            db_path, f["name"], f["rationale"], f["intended_role"],
        )
        binding_fn = bindings.BINDINGS[f["name"]]
        binding_id = f"{BINDING_PREFIX}.{f['name']}"
        reg.register_binding(binding_id, ENGINE_VERSION, binding_fn)

        spec_hash = reg.register_specification(
            db_path,
            definition_name=definition.name,
            definition_version=definition.version,
            parameters={},
            input_requirements=f["input_requirements"],
            warmup_periods=f["warmup_periods"],
            conditioning_rule=f["conditioning_rule"],  # WBS 2.7 -- read from FEATURES, not hardcoded
            binding_id=binding_id,
            engine_version=ENGINE_VERSION,
            validity_rule=f"valid once {f['warmup_periods']} period(s) of history are available",
            sensitivity_variants=[],
            actor=ACTOR,
            registered_at=NOW,
        )

        spec = reg.get_specification(db_path, spec_hash)
        if spec["phase"] == "Registered":
            reg.activate_specification(
                db_path, spec_hash,
                lambda bf=binding_fn, cn=f["name"]: _equivalence_check(bf, cn),
            )
            spec = reg.get_specification(db_path, spec_hash)

        results.append({
            "name": f["name"], "spec_hash": spec_hash, "phase": spec["phase"],
            "warmup_action": warmup_result.action,
            "conditioning_rule": conditioning_result.rule,
            "conditioning_statement": conditioning_result.statement,
            "conditioning_input_count": len(conditioning_result.input_statements),
        })

    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--commit", action="store_true", help="Write the registrations (default: dry run)")
    parser.add_argument("--db", default=str(_DB_PATH), help="Path to feature_store.db")
    parser.add_argument(
        "--warmup-mode", choices=sorted(warmup.MODES), default=None,
        help="Override warmup_policy.yaml's mode for this run only (disabled|warn|refuse). "
             "Default: whatever warmup_policy.yaml currently declares.",
    )
    args = parser.parse_args(argv)

    print_plan()

    if not args.commit:
        print("\nDry run only. Re-run with --commit to write.")
        return 0

    results = run(Path(args.db), warmup_mode=args.warmup_mode)
    print()
    for r in results:
        hash_display = f"{r['spec_hash'][:16]}..." if r["spec_hash"] else "(not registered)"
        print(f"  {r['name']:<18} -> {hash_display}  phase={r['phase']}")
    n_active = sum(1 for r in results if r["phase"] == "Active")
    n_other = len(results) - n_active
    print(f"\n{n_active} Active, {n_other} not Active, out of {len(results)}.")
    return 0 if n_other == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
