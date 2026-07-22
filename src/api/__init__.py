"""Read-only operational API (ADR-0001 Phase 4, §15.3).

This package is the Observability plane (ADR-0001 §4). It is read-only by
construction: no module here imports, references, or exposes any governance
mutation, state transition, promotion, or execution capability (B1/B2), and
`tests/test_api.py` enforces that with source-scanning guards rather than
convention.

It runs as its own systemd unit (`btcbot-api.service`), never in-process with
`btcbot.service` -- a web framework must not share a process with the WS and
scheduler loop (§15.3). It has no startup dependency on the bot: it reads the
databases directly, so the bot being down is reportable data, not an outage
of this service.
"""
