"""FastAPI application -- read-only operational API (ADR-0001 Phase 4, §15.3).

EVERY route in this module is a GET. There is no POST, PUT, PATCH, or DELETE,
and there is no code path from an HTTP request to a governance mutation, a
state transition, a promotion, or an order. That is a binding architectural
constraint (B1/B2, INV-3: "promotion is a judgment, not a computation; the
platform must not promote itself"), and it is enforced by source-scanning
guards in `tests/test_api.py` -- not by this docstring and not by review
discipline.

If you are adding a route here and it is not a GET, the correct action is to
stop and write a superseding ADR.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from src.api import services
from src.api.auth import require_token
from src.api.cache import TTLCache
from src.api.config import ApiSettings, load_settings
from src.api.schemas import (
    ErrorResponse,
    EvidenceSummaryResponse,
    FamilyDetailResponse,
    ForwardStatusResponse,
    GovernanceResponse,
    HealthResponse,
    ReadyzResponse,
    RuntimeHealthResponse,
)

log = logging.getLogger(__name__)

API_VERSION = "1.0.0"
SERVICE_NAME = "btcbot-api"

_DESCRIPTION = """
Read-only operational API for the BTC paper trading bot.

**This API can only read.** It exposes no write endpoint, no state transition,
no promotion, and no execution capability (ADR-0001 B1/B2). Governance
mutations remain CLI-and-human-gated by design.

All data is projected from the same modules the production CLI and Telegram
heartbeat use, so this API cannot disagree with them.

Authentication: `Authorization: Bearer <API_TOKEN>` on every `/api/v1/*`
endpoint. `/health` and `/readyz` are unauthenticated liveness/readiness
probes and expose no operational data.

Binding: `127.0.0.1:9109` only. Reach it via an SSH tunnel
(`ssh -L 9109:127.0.0.1:9109 <host>`).
""".strip()

_TAGS = [
    {"name": "probes", "description": "Unauthenticated liveness and readiness."},
    {"name": "runtime", "description": "Live runtime health (reuses healthcheck.py)."},
    {"name": "forward", "description": "Daily forward-evidence collection cycle."},
    {"name": "governance", "description": "Read-only governance and receipt-chain verification."},
    {"name": "evidence", "description": "Evidence Library state, derived from the append-only ledger."},
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_as_of(as_of: str | None) -> str | None:
    """Reject a malformed `as_of` at the boundary with 422.

    Downstream, `report._as_of_datetime()` calls `datetime.fromisoformat()`,
    which raises `ValueError` on garbage -- that would surface as a 500 and read
    as a server fault when it is really a client input error.
    """
    if as_of is None:
        return None
    candidate = as_of if "T" in as_of else f"{as_of}T00:00:00"
    try:
        datetime.fromisoformat(candidate)
    except ValueError:
        # Literal 422 rather than the status constant: Starlette renamed
        # HTTP_422_UNPROCESSABLE_ENTITY to ..._CONTENT and deprecated the old
        # name, so the constant emits a DeprecationWarning on new versions and
        # does not exist on older ones. The number is stable across both.
        raise HTTPException(
            status_code=422,
            detail=(
                "as_of must be an ISO-8601 date (YYYY-MM-DD) or datetime "
                "(YYYY-MM-DDTHH:MM:SS)."
            ),
        ) from None
    return as_of


AsOf = Query(
    None,
    description=(
        "Reconstruct governance state as it stood at this instant, from the "
        "append-only ledger. Omit for 'now'."
    ),
    examples=["2026-06-14"],
)


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    settings = settings or load_settings()
    cache = TTLCache(settings.cache_ttl_seconds)

    app = FastAPI(
        title="BTC Bot Read-Only Operational API",
        description=_DESCRIPTION,
        version=API_VERSION,
        openapi_tags=_TAGS,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    app.state.settings = settings
    app.state.cache = cache

    # -----------------------------------------------------------------
    # Middleware: structured access log + request id
    # -----------------------------------------------------------------
    @app.middleware("http")
    async def access_log(request: Request, call_next):
        request_id = str(uuid.uuid4())
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # Log and re-raise: the exception handler below shapes the body.
            log.exception(
                "request failed",
                extra={
                    "event": "request_error",
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            raise
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        log.info(
            "request",
            extra={
                "event": "request",
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                # Query strings are logged as a presence flag, not verbatim: the
                # token is never a query param, but this keeps it that way even
                # if a client misuses the API.
                "has_query": bool(request.url.query),
                "status": response.status_code,
                "duration_ms": duration_ms,
                "client": request.client.host if request.client else "unknown",
            },
        )
        return response

    @app.exception_handler(services.SubsystemUnavailable)
    async def _subsystem_unavailable(request: Request, exc: services.SubsystemUnavailable):
        # 503, not 500: the API is fine, a data source it reads is not.
        log.error(
            "subsystem unavailable",
            extra={"event": "subsystem_unavailable", "path": request.url.path, "error": str(exc)},
        )
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    def _cached(key: str, compute):
        value, hit = cache.get_or_compute(key, compute)
        if not hit:
            log.debug("cache miss", extra={"event": "cache_miss", "key": key})
        return value

    # -----------------------------------------------------------------
    # Probes -- unauthenticated by design
    # -----------------------------------------------------------------
    @app.get("/health", response_model=HealthResponse, tags=["probes"],
             summary="Liveness probe (unauthenticated)")
    def health() -> HealthResponse:
        """Does this process answer HTTP. Touches no database and no disk, so it
        stays truthful and instant even when a data source is degraded --
        which is the entire point of separating liveness from readiness."""
        return HealthResponse(status="ok", service=SERVICE_NAME, version=API_VERSION, ts=_now())

    @app.get("/readyz", response_model=ReadyzResponse, tags=["probes"],
             summary="Readiness probe (unauthenticated)")
    def readyz() -> ReadyzResponse:
        """Ledger readable and receipt chain valid (§15.3). Returns 200 with
        `ready: false` rather than a 5xx, so a probe can distinguish
        not-ready from unreachable."""
        result = _cached("readyz", lambda: services.readiness(settings))
        return ReadyzResponse(**result, ts=_now())

    # -----------------------------------------------------------------
    # Runtime
    # -----------------------------------------------------------------
    @app.get("/api/v1/runtime/health", response_model=RuntimeHealthResponse,
             tags=["runtime"], dependencies=[Depends(require_token)],
             responses={401: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
             summary="Runtime health roll-up")
    def runtime_health() -> RuntimeHealthResponse:
        """The same checks and the same worst-of roll-up that the 10-minute
        `btcbot-healthcheck` dead-man timer runs, minus the flag-gated
        `PRAGMA integrity_check`."""
        result = _cached("runtime_health", lambda: services.runtime_health(settings))
        return RuntimeHealthResponse(**result, ts=_now())

    # -----------------------------------------------------------------
    # Forward evidence
    # -----------------------------------------------------------------
    @app.get("/api/v1/forward/status", response_model=ForwardStatusResponse,
             tags=["forward"], dependencies=[Depends(require_token)],
             responses={401: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
             summary="Forward-evidence cycle status")
    def forward_status() -> ForwardStatusResponse:
        """`forward_status.summarize()` -- the exact structure the Telegram
        heartbeat renders as its `fwd-evid:` line (the ADR V4 canary)."""
        result = _cached("forward_status", lambda: services.forward_evidence_status(settings))
        return ForwardStatusResponse(**result, ts=_now())

    # -----------------------------------------------------------------
    # Governance
    # -----------------------------------------------------------------
    @app.get("/api/v1/governance/chain", response_model=GovernanceResponse,
             tags=["governance"], dependencies=[Depends(require_token)],
             responses={401: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
             summary="Receipt hash-chain integrity")
    def governance_chain() -> GovernanceResponse:
        """`receipt_ledger.verify_chain()` via PR-9's dedicated entry point."""
        result = _cached("governance_chain", lambda: services.governance_chain(settings))
        return GovernanceResponse(**result, ts=_now())

    @app.get("/api/v1/governance/verify", response_model=GovernanceResponse,
             tags=["governance"], dependencies=[Depends(require_token)],
             responses={401: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
                        503: {"model": ErrorResponse}},
             summary="Full governance verification sweep")
    def governance_verify(as_of: str | None = AsOf) -> GovernanceResponse:
        """PR-9's `verify_governance()`: chain integrity, C1-C9, EG1/EG2,
        TG1-TG6, DQ-L*, per-receipt replay, and cross-subsystem checks.

        Fail-closed: a subsystem that cannot run reports an error finding and
        `ok: false`; it never silently passes.
        """
        as_of = _validate_as_of(as_of)
        result = _cached(
            f"governance_verify:{as_of}",
            lambda: services.governance_verification(settings, as_of=as_of),
        )
        return GovernanceResponse(**result, ts=_now())

    # -----------------------------------------------------------------
    # Evidence
    # -----------------------------------------------------------------
    @app.get("/api/v1/evidence/summary", response_model=EvidenceSummaryResponse,
             tags=["evidence"], dependencies=[Depends(require_token)],
             responses={401: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
                        503: {"model": ErrorResponse}},
             summary="Evidence Library summary")
    def evidence_summary(as_of: str | None = AsOf) -> EvidenceSummaryResponse:
        """Every family's derived state, pinned decision anchor, and per-lane
        runs -- projected from PR-5's `build_context()` snapshot."""
        as_of = _validate_as_of(as_of)
        result = _cached(
            f"evidence_summary:{as_of}",
            lambda: services.evidence_summary(settings, as_of=as_of),
        )
        return EvidenceSummaryResponse(**result, ts=_now())

    @app.get("/api/v1/evidence/families/{family_id}", response_model=FamilyDetailResponse,
             tags=["evidence"], dependencies=[Depends(require_token)],
             responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
                        422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
             summary="One evidence family")
    def evidence_family(family_id: str, as_of: str | None = AsOf) -> FamilyDetailResponse:
        as_of = _validate_as_of(as_of)
        result = _cached(
            f"evidence_family:{family_id}:{as_of}",
            lambda: services.evidence_family(settings, family_id, as_of=as_of),
        )
        if result is None:
            raise HTTPException(status_code=404, detail=f"unknown family_id: {family_id}")
        return FamilyDetailResponse(**result, ts=_now())

    log.info(
        "api ready",
        extra={"event": "api_ready", "version": API_VERSION, **settings.redacted()},
    )
    return app
