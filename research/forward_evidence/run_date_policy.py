"""
run_date policy for forward-evidence collection (Gate 6B.3, defect B-03).

The single place that decides whether a run_date may be collected at all.
Pure and side-effect free: it reads a policy dict and returns a Decision, so
the caller owns every write and a refusal cannot leave a partial record.

Ordering is the safety argument
-------------------------------
Unconditional refusals (lockbox, immutable gaps) are evaluated BEFORE the
authorization branch, so `--backfill-authorized` can never unlock a sealed
window. A future reordering that put authorization first would silently make
the flag omnipotent; `test_ADV_*` pins the order.

Scope boundary (deliberate, stated rather than implied)
-------------------------------------------------------
This policy is enforced at the run_daily.main() entry point, per the gate's
"one centralized choke point" requirement. A caller that imports run_signal()
directly still bypasses it -- that is how the runner primitive's own tests
(RC01-RC05, SC02) exercise arbitrary historical dates, and why those tests
needed no weakening. Closing that bypass would mean pushing date policy into
the shared runner all six signals depend on, which is a larger blast radius
than this gate authorises.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import yaml

_HERE        = pathlib.Path(__file__).resolve().parent
_POLICY_PATH = _HERE / "run_date_policy.yaml"

#: Collection modes recorded as provenance. A scheduled observation and an
#: authorized backfill must never be indistinguishable after the fact.
MODE_SCHEDULED = "scheduled"
MODE_BACKFILL  = "backfill_authorized"

#: Machine-readable refusal codes (stable; safe to assert on in tests/alerts).
REFUSE_MALFORMED     = "malformed_run_date"
REFUSE_FUTURE        = "future_run_date"
REFUSE_LOCKBOX       = "sealed_lockbox_window"
REFUSE_IMMUTABLE_GAP = "immutable_provenance_gap"
REFUSE_TOO_OLD       = "beyond_backfill_horizon"
REFUSE_UNAUTHORIZED  = "backfill_not_authorized"


class PolicyConfigError(RuntimeError):
    """The policy file is missing, unparseable, or structurally invalid."""


@dataclass(frozen=True)
class Decision:
    allowed: bool
    mode:    str | None   # MODE_* when allowed, else None
    code:    str | None   # REFUSE_* when refused, else None
    detail:  str          # operator-facing explanation, always populated


def load_policy(path: pathlib.Path | None = None) -> dict[str, Any]:
    """
    Load and structurally validate the policy file.

    Raises PolicyConfigError rather than returning a permissive default: a
    missing or malformed policy must fail closed, never silently disable the
    protection it exists to provide.
    """
    p = path or _POLICY_PATH
    if not p.exists():
        raise PolicyConfigError(f"run_date policy file not found: {p}")
    try:
        cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PolicyConfigError(f"run_date policy file is not valid YAML: {exc}") from exc
    if not isinstance(cfg, dict):
        raise PolicyConfigError(f"run_date policy must be a mapping, got {type(cfg).__name__}")

    lb = cfg.get("lockbox")
    if not isinstance(lb, dict) or "start" not in lb or "end" not in lb:
        raise PolicyConfigError("run_date policy: 'lockbox' must define start and end")
    gaps = cfg.get("immutable_gaps", [])
    if not isinstance(gaps, list):
        raise PolicyConfigError("run_date policy: 'immutable_gaps' must be a list")
    for g in gaps:
        if not isinstance(g, dict) or "start" not in g or "end" not in g:
            raise PolicyConfigError("run_date policy: each immutable gap needs start and end")
    horizon = cfg.get("backfill_horizon_days")
    if not isinstance(horizon, int) or isinstance(horizon, bool) or horizon < 0:
        raise PolicyConfigError(
            "run_date policy: 'backfill_horizon_days' must be a non-negative int"
        )
    for label, value in _every_window(cfg):
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise PolicyConfigError(
                f"run_date policy: {label} is not an ISO date: {value!r}"
            ) from exc
    return cfg


def _every_window(cfg: dict[str, Any]):
    yield "lockbox.start", cfg["lockbox"]["start"]
    yield "lockbox.end", cfg["lockbox"]["end"]
    for i, g in enumerate(cfg.get("immutable_gaps", [])):
        yield f"immutable_gaps[{i}].start", g["start"]
        yield f"immutable_gaps[{i}].end", g["end"]


def _within(d: date, start: str, end: str) -> bool:
    """Inclusive on both ends -- a window boundary is inside the window."""
    return date.fromisoformat(start) <= d <= date.fromisoformat(end)


def evaluate_run_date(
    run_date: str,
    *,
    today: date,
    policy: dict[str, Any],
    authorized: bool = False,
) -> Decision:
    """
    Decide whether `run_date` may be collected.

    `today` is injected rather than read from the clock so the policy is
    deterministic and testable at any simulated date.
    """
    try:
        d = date.fromisoformat(run_date)
    except (ValueError, TypeError):
        return Decision(False, None, REFUSE_MALFORMED,
                        f"run_date {run_date!r} is not an ISO YYYY-MM-DD date")

    yesterday = today - timedelta(days=1)

    if d > yesterday:
        return Decision(
            False, None, REFUSE_FUTURE,
            f"run_date {run_date} is not a completed UTC day "
            f"(newest collectable is {yesterday.isoformat()})",
        )

    # --- UNCONDITIONAL refusals. Evaluated before authorization on purpose:
    #     no flag may unlock a sealed or immutable window. ---
    lb = policy["lockbox"]
    if _within(d, lb["start"], lb["end"]):
        return Decision(
            False, None, REFUSE_LOCKBOX,
            f"run_date {run_date} falls inside the sealed lockbox "
            f"{lb['start']}..{lb['end']}: {str(lb.get('reason', '')).strip()}",
        )
    for gap in policy.get("immutable_gaps", []):
        if _within(d, gap["start"], gap["end"]):
            return Decision(
                False, None, REFUSE_IMMUTABLE_GAP,
                f"run_date {run_date} falls inside an immutable provenance gap "
                f"{gap['start']}..{gap['end']}: {str(gap.get('reason', '')).strip()}",
            )

    # --- The ordinary scheduled path. Never needs authorization. ---
    if d == yesterday:
        return Decision(True, MODE_SCHEDULED, None,
                        f"scheduled collection for {run_date} (yesterday UTC)")

    # --- Everything else is manual historical collection. ---
    horizon = policy["backfill_horizon_days"]
    oldest  = yesterday - timedelta(days=horizon)
    if d < oldest:
        return Decision(
            False, None, REFUSE_TOO_OLD,
            f"run_date {run_date} is older than the {horizon}-day backfill "
            f"horizon (oldest allowed is {oldest.isoformat()})",
        )
    if not authorized:
        return Decision(
            False, None, REFUSE_UNAUTHORIZED,
            f"run_date {run_date} is a historical backfill and requires "
            f"--backfill-authorized (scheduled date is {yesterday.isoformat()})",
        )
    return Decision(True, MODE_BACKFILL, None,
                    f"authorized backfill for {run_date} "
                    f"(within {horizon}-day horizon)")
