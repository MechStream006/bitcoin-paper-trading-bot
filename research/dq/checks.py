"""
The check families — docs/governance/data_completeness_gate.md §2, §5.2.

Every check is PURE: no I/O, no clock, no randomness, no unpinned config.
`as_of` is injected (L6). Given the same (contract, observation, period,
as_of), a check returns the same CheckResult forever. That is what makes
replay bit-stable, and any check that reads a clock is a defect.

Every check always reports OBSERVED vs EXPECTED. A gate that says "failed"
without saying "1 funding event, expected 3" has not helped anyone (§5.2).

## Check identity is frozen (contract_versioning.md §3)

`CHECK_REGISTRY` is keyed by versioned `check_id`. A check's SEMANTICS may
never be redefined under an existing id — that would silently change what
every historical receipt replays to, while every hash still matches. A
semantics change requires a NEW id (`coverage.min_count.v2`), registered
alongside the old one, which contracts opt into explicitly by name. **Old
check ids are retained and executable forever.** Deleting one makes every
receipt that named it unreplayable.

## The ordering is normative, and it is a safety property, not a style choice

    1. CONFIGURATION  -> CONFIGURATION_ERROR
    2. SCHEMA         -> SCHEMA_MISMATCH
    3. TEMPORAL       -> PARTIAL_DAY
    4. FRESHNESS      -> STALE_DATA
    5. COVERAGE       -> INCOMPLETE_DATA / BACKFILL_REQUIRED
    6. CONTINUITY     -> BACKFILL_REQUIRED

If coverage ran before schema, the Binance ms->us switch — which silently
produces a **0-row partition** (CLAUDE.md §11) — would present as
INCOMPLETE_DATA, and an operator would "backfill" the same broken month
forever, chasing a data problem that is actually a code problem. Schema drift
must never be allowed to masquerade as missing data. The engine enforces the
order; this module states it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Callable

from contract import Contract, ContractConfigurationError, InputSpec, period_bounds
from observation import InputObservation, ObservationError
from states import (
    BACKFILL_REQUIRED,
    CONFIGURATION_ERROR,
    INCOMPLETE_DATA,
    PARTIAL_DAY,
    READY,
    SCHEMA_MISMATCH,
    STALE_DATA,
    UNKNOWN,
)

# Engine-emitted, not contract-declared: the "we could not look" check.
PROBE_CHECK: str = "probe.observation.v1"
CONFIGURATION_CHECK: str = "configuration.contract.v1"


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    state: str
    observed: str
    expected: str
    detail: str = ""
    input_id: str | None = None  # None for contract-level checks

    @property
    def passed(self) -> bool:
        return self.state == READY

    def to_dict(self) -> dict:
        return {
            "check_id": self.check_id,
            "input_id": self.input_id,
            "state": self.state,
            "observed": self.observed,
            "expected": self.expected,
            "detail": self.detail,
            "passed": self.passed,
        }


@dataclass(frozen=True)
class CheckContext:
    """Everything a check is permitted to see. Note what is absent: a clock,
    a database handle, a filesystem path, a config module."""
    contract: Contract
    period: str
    as_of: datetime                       # injected (L6)
    spec: InputSpec | None = None         # None for contract-level checks
    observation: InputObservation | None = None

    def bounds(self) -> tuple[datetime, datetime]:
        return period_bounds(self.contract.grain, self.period)

    @property
    def period_closed(self) -> bool:
        _start, end = self.bounds()
        return self.as_of >= end


def _ok(check_id: str, ctx: CheckContext, observed: str, expected: str, detail: str = "") -> CheckResult:
    return CheckResult(
        check_id=check_id, state=READY, observed=observed, expected=expected,
        detail=detail, input_id=None if ctx.spec is None else ctx.spec.id,
    )


def _fail(
    check_id: str, ctx: CheckContext, state: str, observed: str, expected: str, detail: str = ""
) -> CheckResult:
    return CheckResult(
        check_id=check_id, state=state, observed=observed, expected=expected,
        detail=detail, input_id=None if ctx.spec is None else ctx.spec.id,
    )


# ---------------------------------------------------------------------------
# 0. PROBE — the gate could not look at all.
# ---------------------------------------------------------------------------

def check_probe(ctx: CheckContext) -> CheckResult:
    """
    UNKNOWN when the observation is absent or the probe recorded an error.
    This is L2 (fail closed) at its most literal: absence of evidence of
    completeness is not evidence of completeness, and UNKNOWN blocks.
    """
    if ctx.observation is None:
        return _fail(
            PROBE_CHECK, ctx, UNKNOWN, "no observation", "an observation",
            "the probe produced no observation for this input",
        )
    if ctx.observation.probe_error:
        return _fail(
            PROBE_CHECK, ctx, UNKNOWN, f"probe_error: {ctx.observation.probe_error}",
            "a successful probe", "the probe could not complete",
        )
    return _ok(PROBE_CHECK, ctx, "probe ok", "a successful probe")


# ---------------------------------------------------------------------------
# 1. CONFIGURATION — is the contract even usable?
# ---------------------------------------------------------------------------

def check_configuration(ctx: CheckContext) -> CheckResult:
    """
    CONFIGURATION_ERROR when the contract itself is unusable: unknown grain,
    a check_id this engine does not implement, an unsatisfiable coverage
    floor, a malformed policy override, an unparseable period. The gate never
    got as far as looking at data, so it must not pretend to have an opinion
    about the data.
    """
    from contract import validate_contract  # local: avoids an import cycle

    errors = validate_contract(ctx.contract)

    try:
        ctx.bounds()
    except ContractConfigurationError as exc:
        errors.append(str(exc))

    if errors:
        return _fail(
            CONFIGURATION_CHECK, ctx, CONFIGURATION_ERROR,
            f"{len(errors)} configuration error(s)", "a usable contract",
            "; ".join(errors),
        )
    return _ok(CONFIGURATION_CHECK, ctx, "contract usable", "a usable contract")


# ---------------------------------------------------------------------------
# 2. SCHEMA — is the data shaped as declared?
# ---------------------------------------------------------------------------

def check_schema(ctx: CheckContext) -> CheckResult:
    """
    SCHEMA_MISMATCH when observed columns, dtypes, or value-magnitude bands
    contradict the declared schema.

    The band rule is the one that earns this check its position ahead of
    coverage: a Binance ms->us switch moves `open_time` from ~1.7e12 to
    ~1.7e15 (CLAUDE.md §11). Declared as a band, that is caught here, as a
    CODE problem. Undeclared, it silently yields a 0-row partition that looks
    exactly like missing data, and gets "backfilled" forever.
    """
    spec, obs = ctx.spec, ctx.observation
    assert spec is not None and obs is not None  # engine guarantees; see check_probe
    check_id = spec.schema.check if spec.schema else "schema.declared.v1"
    declared = spec.schema

    if declared is None:
        return _ok(check_id, ctx, "no schema declared", "no schema declared")

    if declared.columns:
        if obs.schema is None:
            return _fail(
                check_id, ctx, UNKNOWN, "schema not observed",
                f"{len(declared.columns)} declared column(s)",
                "the probe could not read the source's schema; the gate cannot confirm "
                "the data is shaped as declared, and must not assume it is",
            )
        missing = sorted(set(declared.columns) - set(obs.schema))
        if missing:
            return _fail(
                check_id, ctx, SCHEMA_MISMATCH, f"missing column(s): {missing}",
                f"columns {sorted(declared.columns)}",
                "a column declared in the contract is absent from the source. This is the "
                "rv_21d/atr14 class: declared in the schema, never written by any INSERT, "
                "for any row, ever, and nothing noticed.",
            )
        wrong = sorted(
            f"{col}: {obs.schema[col]} != {dtype}"
            for col, dtype in declared.columns.items()
            if obs.schema[col] != dtype
        )
        if wrong:
            return _fail(
                check_id, ctx, SCHEMA_MISMATCH, f"dtype mismatch: {wrong}",
                f"dtypes {dict(sorted(declared.columns.items()))}",
                "the source's dtype contradicts the contract's declaration",
            )

    for col, (lo, hi) in sorted(declared.bands.items()):
        try:
            observed_bound = obs.bound(col)
        except ObservationError as exc:
            return _fail(
                check_id, ctx, UNKNOWN, str(exc), f"{col} in [{lo}, {hi}]",
                "the observed value bound is unreadable",
            )
        if observed_bound is None:
            return _fail(
                check_id, ctx, UNKNOWN, f"no observed bound for '{col}'",
                f"{col} in [{lo}, {hi}]",
                "the contract declares a magnitude band but the probe did not observe one; "
                "the gate cannot confirm the units are what the contract says they are",
            )
        obs_lo, obs_hi = observed_bound
        if obs_lo < lo or obs_hi > hi:
            return _fail(
                check_id, ctx, SCHEMA_MISMATCH,
                f"{col} in [{obs_lo}, {obs_hi}]", f"{col} in [{lo}, {hi}]",
                "an observed value is outside the declared magnitude band. A unit change "
                "upstream (the ms->us class) corrupts everything downstream, retroactively, "
                "with no other symptom.",
            )

    return _ok(
        check_id, ctx, "schema and bands as declared",
        f"{len(declared.columns)} column(s), {len(declared.bands)} band(s)",
    )


# ---------------------------------------------------------------------------
# 3. TEMPORAL — is the period closed?
# ---------------------------------------------------------------------------

def check_temporal(ctx: CheckContext) -> CheckResult:
    """
    PARTIAL_DAY when the observation period has not fully elapsed as of
    `as_of`. The data is not WRONG — it is NOT FINISHED.

    This is H6's F1, exactly: `daily_job` processed `corpus.iloc[-1]`, which at
    its 00:05 UTC run time was *today, five minutes old*, and wrote a 00:05
    intraday tick into `spot_close` as if it were a daily close.

    Nobody is at fault and nothing needs fixing. Wait. (§3: never warn on this
    — a routine 00:05 deferral that pages someone is a defect in the alerting.)
    """
    check_id = ctx.contract.temporal_check
    start, end = ctx.bounds()
    if ctx.as_of < end:
        return _fail(
            check_id, ctx, PARTIAL_DAY,
            f"as_of={ctx.as_of.isoformat()}", f"as_of >= period_end={end.isoformat()}",
            f"the {ctx.contract.grain} period '{ctx.period}' has not fully elapsed; "
            f"it is not incomplete, it is unfinished",
        )
    return _ok(
        check_id, ctx, f"as_of={ctx.as_of.isoformat()}",
        f"as_of >= period_end={end.isoformat()}",
        f"period '{ctx.period}' is closed",
    )


# ---------------------------------------------------------------------------
# 4. FRESHNESS — is the newest data recent enough?
# ---------------------------------------------------------------------------

def check_freshness(ctx: CheckContext) -> CheckResult:
    """
    STALE_DATA when the newest input record is older than the input's declared
    freshness SLA. The upstream collector has stalled.

    Distinct from INCOMPLETE_DATA: the data that EXISTS is fine, there just
    is not any RECENT data. Different root cause (the collector), different
    remedy. Collapsing the two is how a dead collector stays invisible.
    """
    spec, obs = ctx.spec, ctx.observation
    assert spec is not None and obs is not None
    if spec.freshness is None:
        return _ok("freshness.max_age.v1", ctx, "no freshness SLA declared", "no freshness SLA declared")

    check_id = spec.freshness.check
    max_age = spec.freshness.max_age_hours

    if obs.newest_arrival_ts is None:
        return _fail(
            check_id, ctx, UNKNOWN, "newest_arrival_ts not observed",
            f"age <= {max_age}h",
            "the probe did not report an arrival time; the gate cannot tell whether the "
            "collector is alive, and must not assume it is",
        )

    newest = _parse_ts(obs.newest_arrival_ts)
    if newest is None:
        return _fail(
            check_id, ctx, UNKNOWN, f"newest_arrival_ts={obs.newest_arrival_ts!r}",
            f"age <= {max_age}h", "arrival timestamp is not a valid ISO-8601 instant",
        )

    age_hours = Decimal((ctx.as_of - newest).total_seconds()) / Decimal(3600)
    if age_hours > max_age:
        return _fail(
            check_id, ctx, STALE_DATA,
            f"age={_fmt(age_hours)}h (newest arrival {obs.newest_arrival_ts})",
            f"age <= {max_age}h",
            "the newest record predates the freshness SLA: the collector has stalled",
        )
    return _ok(check_id, ctx, f"age={_fmt(age_hours)}h", f"age <= {max_age}h")


# ---------------------------------------------------------------------------
# 5. COVERAGE — is the closed period fully populated?
# ---------------------------------------------------------------------------

def check_coverage(ctx: CheckContext) -> CheckResult:
    """
    The three-way split that matters most (§2). A coverage shortfall is the
    IDENTICAL physical symptom — "there are fewer rows than I expected" — in
    three situations that demand three different remedies, and are separated
    by two clocks:

        period_end > as_of ................................. PARTIAL_DAY
            not finished. Time fixes it. DEFER.
        period closed, coverage < floor, within late budget . INCOMPLETE_DATA
            the source may still deliver. RETRY.
        period closed, coverage < floor, late budget expired  BACKFILL_REQUIRED
            it will NEVER self-heal. Only an operator fixes it. BLOCK.

    Collapsing these into one state is precisely how H6 happened: the pipeline
    saw a NULL and could not distinguish "not finished yet" from "broken
    forever". Note in particular the first line — a shortfall on an OPEN period
    is reported as PARTIAL_DAY, never as INCOMPLETE_DATA. At 00:05 UTC, 1 of 3
    funding events having fired is not a data problem at all; two more are
    coming.
    """
    spec, obs = ctx.spec, ctx.observation
    assert spec is not None and obs is not None
    if spec.coverage is None:
        return _ok("coverage.min_count.v1", ctx, "no coverage floor declared", "no coverage floor declared")

    cov = spec.coverage
    check_id = cov.check
    expected = _coverage_expected(cov)

    count = obs.coverage_count if obs.coverage_count is not None else obs.rows
    if count is None:
        return _fail(
            check_id, ctx, UNKNOWN, "coverage not observed", expected,
            "the probe reported neither coverage_count nor rows",
        )

    shortfalls: list[str] = []
    if cov.min_count is not None and count < cov.min_count:
        shortfalls.append(f"count={count} < min_count={cov.min_count}")

    if cov.min_fraction is not None:
        denom = obs.coverage_denominator
        if denom is None:
            return _fail(
                check_id, ctx, UNKNOWN, f"count={count}, denominator not observed", expected,
                "the contract declares a min_fraction but the probe did not report a "
                "coverage_denominator; the fraction cannot be computed",
            )
        if denom <= 0:
            return _fail(
                check_id, ctx, UNKNOWN, f"coverage_denominator={denom}", expected,
                "a non-positive coverage denominator makes the fraction undefined",
            )
        fraction = Decimal(count) / Decimal(denom)
        if fraction < cov.min_fraction:
            shortfalls.append(
                f"fraction={_fmt(fraction)} ({count}/{denom}) < min_fraction={cov.min_fraction}"
            )

    if not shortfalls:
        return _ok(check_id, ctx, f"count={count}", expected)

    observed = "; ".join(shortfalls)

    if not ctx.period_closed:
        _start, end = ctx.bounds()
        return _fail(
            check_id, ctx, PARTIAL_DAY, observed, expected,
            f"coverage is below the floor, but period '{ctx.period}' is still OPEN "
            f"(ends {end.isoformat()}). This is not incomplete data — it is unfinished "
            f"data. The remaining records have not had the chance to arrive yet.",
        )

    if spec.late_arrival_hours is None:
        # validate_contract() already rejects this combination; reaching it means
        # the configuration check was bypassed. Fail closed rather than guess a budget.
        return _fail(
            check_id, ctx, UNKNOWN, observed, expected,
            "coverage is short and no late_arrival_hours budget is declared, so the gate "
            "cannot tell INCOMPLETE_DATA from BACKFILL_REQUIRED",
        )

    _start, end = ctx.bounds()
    deadline = end + timedelta(hours=float(spec.late_arrival_hours))
    if ctx.as_of > deadline:
        return _fail(
            check_id, ctx, BACKFILL_REQUIRED, observed, expected,
            f"coverage is short and the late-arrival budget "
            f"({spec.late_arrival_hours}h after period end) expired at "
            f"{deadline.isoformat()}. This period will NEVER self-heal.",
        )
    return _fail(
        check_id, ctx, INCOMPLETE_DATA, observed, expected,
        f"coverage is short but the late-arrival budget "
        f"({spec.late_arrival_hours}h after period end) does not expire until "
        f"{deadline.isoformat()}; the source may still deliver",
    )


def _coverage_expected(cov) -> str:
    parts = []
    if cov.min_count is not None:
        parts.append(f"count >= {cov.min_count}")
    if cov.min_fraction is not None:
        parts.append(f"fraction >= {cov.min_fraction}")
    return " and ".join(parts) if parts else "a coverage floor"


# ---------------------------------------------------------------------------
# 6. CONTINUITY — interior gaps in the history window.
# ---------------------------------------------------------------------------

def check_continuity(ctx: CheckContext) -> CheckResult:
    """
    BACKFILL_REQUIRED when the trailing history window a derivation reads is
    too short, or has interior gaps.

    `rv_21d` needs 21 clean rows. The gate REFUSES a 20-row window rather than
    returning a quietly-wrong number — which is the whole difference between a
    framework and a comment.
    """
    spec, obs = ctx.spec, ctx.observation
    assert spec is not None and obs is not None
    if spec.continuity is None:
        return _ok("continuity.no_interior_gaps.v1", ctx, "no history window declared",
                   "no history window declared")

    cont = spec.continuity
    check_id = cont.check
    expected = f"history_rows >= {cont.min_rows}" + (
        " and no interior gaps" if cont.no_interior_gaps else ""
    )

    if cont.min_rows > 0:
        if obs.history_rows is None:
            return _fail(
                check_id, ctx, UNKNOWN, "history_rows not observed", expected,
                "the probe did not report the history-window row count",
            )
        if obs.history_rows < cont.min_rows:
            return _fail(
                check_id, ctx, BACKFILL_REQUIRED,
                f"history_rows={obs.history_rows}", expected,
                f"the trailing history window is too short for the declared lookback; "
                f"deriving from a short window is not recoverable after the fact",
            )

    if cont.no_interior_gaps:
        if obs.has_interior_gaps is None:
            return _fail(
                check_id, ctx, UNKNOWN, "interior gaps not observed", expected,
                "the probe did not report whether the history window has interior gaps",
            )
        if obs.has_interior_gaps:
            return _fail(
                check_id, ctx, BACKFILL_REQUIRED,
                "interior gaps present", expected,
                "the trailing history window has interior gaps; a rolling statistic "
                "computed across a hole is silently wrong",
            )

    return _ok(check_id, ctx, f"history_rows={obs.history_rows}", expected)


# ---------------------------------------------------------------------------
# Registry — frozen by id, retained forever.
# ---------------------------------------------------------------------------

CHECK_REGISTRY: dict[str, Callable[[CheckContext], CheckResult]] = {
    PROBE_CHECK: check_probe,
    CONFIGURATION_CHECK: check_configuration,
    "schema.declared.v1": check_schema,
    "temporal.period_closed.v1": check_temporal,
    "freshness.max_age.v1": check_freshness,
    "coverage.min_count.v1": check_coverage,
    "continuity.no_interior_gaps.v1": check_continuity,
}


class UnknownCheckError(KeyError):
    """
    Raised when a contract or a receipt names a check_id this engine cannot
    resolve. contract_versioning.md §3: "If the current engine cannot honour a
    historical check_id, replay FAILS LOUDLY — it never silently substitutes
    the modern check." A silent substitution would change what a historical
    receipt means while every hash still verified.
    """


def get_check(check_id: str) -> Callable[[CheckContext], CheckResult]:
    try:
        return CHECK_REGISTRY[check_id]
    except KeyError:
        raise UnknownCheckError(
            f"check_id '{check_id}' is not implemented by this engine. Check semantics are "
            f"frozen by id and retained forever; an unresolvable id means this evaluation "
            f"cannot be honoured. Registered: {sorted(CHECK_REGISTRY)}"
        ) from None


def register_check(check_id: str, fn: Callable[[CheckContext], CheckResult]) -> None:
    """
    Additive only. Re-registering an existing check_id with a different
    implementation is refused: that is exactly the silent-semantics-change
    attack contract_versioning.md §3 exists to prevent (freeze the contract
    perfectly, then change what `min_count: 3` MEANS, and every historical
    receipt replays to a different verdict while every hash still matches).
    A new meaning needs a new id.
    """
    existing = CHECK_REGISTRY.get(check_id)
    if existing is not None and existing is not fn:
        raise UnknownCheckError(
            f"check_id '{check_id}' is already registered to a different implementation. "
            f"A check's semantics may never be redefined under an existing id — register "
            f"a new id (e.g. '{check_id.rsplit('.', 1)[0]}.v2') and have contracts opt in."
        )
    CHECK_REGISTRY[check_id] = fn


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _parse_ts(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if dt.tzinfo is None:
        from datetime import timezone as _tz
        dt = dt.replace(tzinfo=_tz.utc)
    return dt


def _fmt(d: Decimal) -> str:
    return format(d.quantize(Decimal("0.001")), "f")
