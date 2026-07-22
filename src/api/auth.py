"""Bearer-token authentication (ADR-0001 §9, §15.3).

Single static token from `API_TOKEN` in `/opt/btcbot/.env` (0640 `root:btcbot`),
rotated manually (§9 S6 -- automated rotation is explicitly out of scope). This
is deliberately the simplest thing that satisfies §15.3's "token auth": the
service is loopback-only behind an SSH tunnel with one operator, so sessions,
refresh flows, or a user table would add attack surface and operational failure
modes to defend against a threat model that does not exist here.

The token is a *second* control, not the primary one. The primary control is
that the socket is unreachable from anywhere but the host itself (§8 N1).
"""
from __future__ import annotations

import logging
import secrets

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

log = logging.getLogger(__name__)

# auto_error=False so a *missing* credential reaches our handler and is logged
# and shaped identically to a *wrong* one. With auto_error=True, FastAPI would
# short-circuit missing credentials with its own 403 and a different body,
# letting a caller distinguish "no header" from "bad token" by shape alone.
_bearer = HTTPBearer(auto_error=False, description="Operator API token")

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or missing API token.",
    headers={"WWW-Authenticate": "Bearer"},
)


def require_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    """FastAPI dependency: reject anything without the exact configured token.

    Uses `secrets.compare_digest` so comparison time does not vary with how
    many leading characters matched -- the standard defence against a timing
    oracle that would otherwise let an attacker recover the token byte by byte.
    """
    settings = request.app.state.settings
    presented = credentials.credentials if credentials else ""

    # compare_digest raises on non-ASCII str input; encode both sides first so a
    # hostile header can never turn an auth check into a 500.
    ok = secrets.compare_digest(
        presented.encode("utf-8", "replace"),
        settings.token.encode("utf-8"),
    )
    if not ok:
        # Log the fact and the source, never the presented value -- a near-miss
        # token in a log file is still a secret, and journald is readable by
        # more principals than `.env` is.
        log.warning(
            "auth rejected",
            extra={
                "event": "auth_rejected",
                "path": request.url.path,
                "client": request.client.host if request.client else "unknown",
                "credential_present": bool(presented),
            },
        )
        raise _UNAUTHORIZED
