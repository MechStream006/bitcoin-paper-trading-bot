"""Response models -- the OpenAPI contract (ADR-0001 §15.3).

These exist to make `/docs` a real specification rather than a list of
`{}`-shaped endpoints. They mirror the upstream dataclasses
(`healthcheck.CheckResult`, `forward_status.ForwardStatus`,
`governance_verification.VerificationReport`, `report.FamilyRow`) without
redefining any of their logic.

Where an upstream structure is genuinely open-ended -- a check's `detail` dict,
a run's metric bag -- the field is typed as a permissive dict on purpose.
Pinning those to a closed schema here would mean this module has to be edited
every time an upstream check adds a detail key, and the API would silently drop
fields until someone did.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Liveness only: does this process answer HTTP."""

    status: str = Field(examples=["ok"])
    service: str = Field(examples=["btcbot-api"])
    version: str = Field(examples=["1.0.0"])
    ts: str = Field(description="UTC ISO-8601 timestamp.")


class ReadyzResponse(BaseModel):
    """Readiness: ledger readable and receipt chain valid (§15.3)."""

    ready: bool
    checks: dict[str, Any]
    detail: str
    ts: str


class CheckModel(BaseModel):
    name: str = Field(examples=["db_liveness"])
    severity: str = Field(description="OK | INFO | WARNING | CRITICAL")
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


class RuntimeHealthResponse(BaseModel):
    """`scripts/healthcheck.py`'s default check set and worst-of roll-up."""

    overall: str = Field(description="Worst severity across all checks.")
    checks: list[CheckModel]
    integrity_check_included: bool = Field(
        description=(
            "Always false. PRAGMA integrity_check is excluded from the HTTP path "
            "as a long-running scan; run it from the CLI with --full-integrity."
        )
    )
    ts: str


class ForwardStatusResponse(BaseModel):
    """`src/alerts/forward_status.summarize()` -- the heartbeat's own view."""

    state: str = Field(description="ok | stale | degraded | critical | missing | error")
    severity: str = Field(description="ok | neutral | degraded | critical")
    latest_run_date: str | None
    expected_run_date: str
    signals: dict[str, str] = Field(description="signal_id -> status for latest_run_date")
    n_ok: int
    n_logged: int
    detail: str
    line: str = Field(description="The rendered heartbeat line, verbatim.")
    ts: str


class FindingModel(BaseModel):
    code: str = Field(examples=["GV-CHAIN"])
    severity: str = Field(description="error | warning | info")
    message: str
    subsystem: str = Field(examples=["receipt_chain"])


class GovernanceResponse(BaseModel):
    """`governance_verification.VerificationReport` (PR-9), read-only.

    `ok: false` is a governance finding to report, not an API error -- these
    endpoints return 200 with `ok: false`, never 5xx, so a monitoring client
    can distinguish "governance has a problem" from "the API has a problem".
    """

    ok: bool
    as_of: str | None
    counts: dict[str, int]
    subsystems_run: list[str]
    findings: list[FindingModel]
    summary: str
    ts: str


class PinnedRunModel(BaseModel):
    sharpe: float | None = None
    dsr_prob: float | None = None
    bh_p: float | None = None
    # FDB-13. Derived at serialisation time from the run already being
    # returned; NOT a stored column and NOT a ledger schema change. `bh_p`
    # carries legacy in-sample raw p-values alongside BH-adjusted q-values,
    # so a consumer receiving the bare float cannot tell which it has.
    # Additive and optional: existing consumers are unaffected.
    bh_p_convention: str | None = None
    n_trades: int | None = None
    run_ts: str | None = None
    provenance: str | None = None


class LaneRunModel(BaseModel):
    run_id: str | None = None
    run_ts: str | None = None
    sharpe: float | None = None
    provenance: str | None = None


class FamilyModel(BaseModel):
    family_id: str
    state: str
    role: str
    hypothesis: str | None = None
    pinned_run_id: str | None = None
    pinned_run_kind: str | None = None
    pinned_lane_code: str | None = None
    state_ts: str | None = None
    is_pin_stale: bool
    flags: list[str]
    pinned_run: PinnedRunModel | None = None
    lanes: dict[str, LaneRunModel] = Field(default_factory=dict)


class FamilyDetailResponse(FamilyModel):
    as_of: str | None = None
    ts: str | None = None


class EvidenceSummaryResponse(BaseModel):
    """Projection of PR-5's `build_context()` snapshot."""

    as_of: str | None
    n_families: int
    n_runs: int
    n_transitions: int
    counts_by_state: dict[str, int]
    families: list[FamilyModel]
    ts: str


class ErrorResponse(BaseModel):
    detail: str
