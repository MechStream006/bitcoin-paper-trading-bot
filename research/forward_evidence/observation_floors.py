"""
observation_floors.py
=====================
Enforcement of the **already-frozen** observation floors that each family
declared in its pre-registration, before any forward observation existed.

WHAT THIS IS
------------
A pure evaluator. It reads a family's hash-pinned pre-registration, reads the
forward-evidence corpus, and reports which declared floors are met. It invents
no floor, relaxes none, and supplies no default for one that is absent.

WHAT THIS IS NOT
----------------
Not an assessment, not a gate, not a promotion path. It computes no Sharpe, no
DSR, no BH-p and no verdict. It writes nothing -- not to the ledger, not to the
registry, not to the forward DB. It is deliberately NOT imported by run_daily:
scheduled collection neither calls it nor is affected by it.

Gate FB-8 will consume this module. FB-8 is not implemented here.

THE PRE-REGISTRATION IS AUTHORITATIVE, AND THAT IS ENFORCED
-----------------------------------------------------------
Floors are read from the file the REGISTRY pins, and the pin is verified under
the A2a convention (`preregistration.hash` is the LF-normalised SHA-256 of the
JSON at `preregistration.path` -- docs/governance/a2a_preregistration_hash_
convention.md). A family with no pointer, a broken pointer, or a pre-registration
whose bytes no longer match its recorded hash is **UNEVALUABLE**. It does not
fall back to a default and it does not pass.

FAIL-CLOSED IS THE WHOLE POINT
------------------------------
Three distinct states, never collapsed into two:

    MET          the floor is declared, computable, and satisfied
    NOT_MET      the floor is declared, computable, and not satisfied
    UNEVALUABLE  the floor is declared but cannot be computed from the
                 available evidence, OR the pre-registration itself cannot be
                 trusted

`floors_satisfied` is True only when every declared floor is MET. An
UNEVALUABLE floor blocks exactly as an unmet one does. This follows R1's NULL
discipline (CLAUDE.md sec.16): absence must never be satisfiable by recording
nothing.

Six declared floors are regime-conditioned -- `minimum_bull_regime_entries`,
`minimum_bear_regime_entries`, `minimum_regimes_observed`,
`minimum_bull_regime_periods`, `minimum_sideways_regime_periods`,
`minimum_high_vol_periods_observed`. `evidence_forward.db` carries no regime
labelling (verified: its objects are run_log, signal_log, trade_entries,
trade_exits, trade_returns, run_inputs, run_provenance, collection_provenance),
so these are UNEVALUABLE here and are reported as such rather than skipped.
Making them computable is a separate, unstarted piece of work.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import pathlib
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Literal

_HERE = pathlib.Path(__file__).resolve().parent
_RESEARCH_ROOT = _HERE.parent
_REPO_ROOT = _RESEARCH_ROOT.parent

Status = Literal["MET", "NOT_MET", "UNEVALUABLE"]

#: Floors this module can compute from evidence_forward.db, and how.
#: A declared floor absent from this mapping is UNEVALUABLE -- never ignored.
COMPUTABLE_FLOORS: tuple[str, ...] = (
    "minimum_trades",
    "minimum_calendar_months_before_assessment",
    "minimum_active_days",
    "minimum_both_long_and_short_trades",
)

#: Declared floors that are known to need regime labelling the forward corpus
#: does not carry. Named explicitly so their UNEVALUABLE status is a documented
#: fact rather than an unexplained gap.
REGIME_CONDITIONED_FLOORS: tuple[str, ...] = (
    "minimum_bull_regime_entries",
    "minimum_bear_regime_entries",
    "minimum_regimes_observed",
    "minimum_bull_regime_periods",
    "minimum_sideways_regime_periods",
    "minimum_high_vol_periods_observed",
)

#: Keys inside observation_floors that are prose, not floors.
_NON_FLOOR_KEYS = frozenset({"rationale", "u1_basis", "u3_basis", "frozen_at"})


class PreRegistrationError(RuntimeError):
    """The pre-registration cannot be trusted: absent, unpinned, or altered."""


@dataclass(frozen=True)
class FloorResult:
    name: str
    required: Any
    observed: Any
    status: Status
    detail: str = ""


@dataclass(frozen=True)
class FloorReport:
    family_id: str
    as_of: str
    prereg_path: str | None
    results: list[FloorResult] = field(default_factory=list)
    error: str | None = None

    @property
    def floors_satisfied(self) -> bool:
        """True only if at least one floor is declared and every one is MET."""
        if self.error is not None or not self.results:
            return False
        return all(r.status == "MET" for r in self.results)

    @property
    def blocking(self) -> list[FloorResult]:
        return [r for r in self.results if r.status != "MET"]


# ---------------------------------------------------------------------------
# Pre-registration resolution (hash-pinned; A2a convention)
# ---------------------------------------------------------------------------

def _sha256_lf(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def resolve_pinned_prereg(
    family_id: str,
    registry: dict,
    repo_root: pathlib.Path | None = None,
) -> pathlib.Path:
    """Return the pre-registration the registry pins, or raise.

    Verifies the A2a convention. Never falls back to a path guessed from the
    family name: an unpinned family has no authoritative pre-registration, and
    substituting one would invent the very provenance this module exists to
    enforce.
    """
    root = pathlib.Path(repo_root) if repo_root is not None else _REPO_ROOT
    fam = registry.get(family_id)
    if fam is None:
        raise PreRegistrationError(f"'{family_id}' is not in the registry")
    pin = fam.get("preregistration")
    if not pin:
        raise PreRegistrationError(
            f"'{family_id}' has no registry preregistration pointer; its floors "
            "are not pinned to any document and cannot be enforced"
        )
    path = root / pin["path"]
    if not path.exists():
        raise PreRegistrationError(
            f"'{family_id}' pins {pin['path']}, which does not exist")
    actual = _sha256_lf(path)
    if actual != pin["hash"]:
        raise PreRegistrationError(
            f"'{family_id}' pre-registration hash mismatch: registry pins "
            f"{pin['hash'][:16]}… but {pin['path']} is {actual[:16]}… — the "
            "pre-registration was amended after registration"
        )
    return path


def declared_floors(prereg_path: pathlib.Path) -> dict[str, Any]:
    """The observation_floors block, prose keys stripped. Raises if absent."""
    doc = json.loads(pathlib.Path(prereg_path).read_text(encoding="utf-8"))
    floors = doc.get("observation_floors")
    if not floors:
        raise PreRegistrationError(
            f"{pathlib.Path(prereg_path).name} declares no observation_floors; "
            "an absent floor block is not an absent requirement"
        )
    return {k: v for k, v in floors.items() if k not in _NON_FLOOR_KEYS}


# ---------------------------------------------------------------------------
# Forward-evidence observations (read-only)
# ---------------------------------------------------------------------------

def _connect_ro(db_path: pathlib.Path) -> sqlite3.Connection:
    uri = pathlib.Path(db_path).resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=10.0)


def observe(forward_db: pathlib.Path, signal_id: str, as_of: str) -> dict[str, Any]:
    """Counts needed by the computable floors. Closed trades only."""
    conn = _connect_ro(forward_db)
    try:
        conn.row_factory = sqlite3.Row
        trades = conn.execute(
            "SELECT direction FROM trade_returns "
            "WHERE signal_id = ? AND exit_date <= ?", (signal_id, as_of)
        ).fetchall()
        first = conn.execute(
            "SELECT MIN(run_date) FROM run_log WHERE signal_id = ? AND status = 'ok'",
            (signal_id,)
        ).fetchone()[0]
        active = conn.execute(
            "SELECT COUNT(DISTINCT run_date) FROM run_log "
            "WHERE signal_id = ? AND status = 'ok' AND run_date <= ?",
            (signal_id, as_of)
        ).fetchone()[0]
    finally:
        conn.close()
    return {
        "n_trades": len(trades),
        "n_long": sum(1 for t in trades if t["direction"] > 0),
        "n_short": sum(1 for t in trades if t["direction"] < 0),
        "first_observation": first,
        "active_days": active,
        "calendar_months": _months_between(first, as_of) if first else None,
    }


def _months_between(start: str, end: str) -> int:
    """Whole calendar months elapsed. A partial month does not count."""
    a = _dt.date.fromisoformat(start)
    b = _dt.date.fromisoformat(end)
    months = (b.year - a.year) * 12 + (b.month - a.month)
    if b.day < a.day:
        months -= 1
    return max(months, 0)


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def _evaluate_one(name: str, required: Any, obs: dict[str, Any]) -> FloorResult:
    if name == "minimum_trades":
        got = obs["n_trades"]
        return FloorResult(name, required, got,
                           "MET" if got >= required else "NOT_MET")

    if name == "minimum_calendar_months_before_assessment":
        got = obs["calendar_months"]
        if got is None:
            return FloorResult(name, required, None, "UNEVALUABLE",
                               "no ok observation on record; elapsed months undefined")
        return FloorResult(name, required, got,
                           "MET" if got >= required else "NOT_MET")

    if name == "minimum_active_days":
        got = obs["active_days"]
        return FloorResult(name, required, got,
                           "MET" if got >= required else "NOT_MET")

    if name == "minimum_both_long_and_short_trades":
        got = (obs["n_long"], obs["n_short"])
        if not required:
            # Pre-registered as not required. Recorded, not silently dropped.
            return FloorResult(name, required, got, "MET",
                               "not required by this pre-registration")
        ok = obs["n_long"] > 0 and obs["n_short"] > 0
        return FloorResult(name, required, got, "MET" if ok else "NOT_MET",
                           f"{obs['n_long']} long / {obs['n_short']} short")

    if name in REGIME_CONDITIONED_FLOORS:
        return FloorResult(
            name, required, None, "UNEVALUABLE",
            "regime-conditioned; evidence_forward.db carries no regime labelling")

    return FloorResult(name, required, None, "UNEVALUABLE",
                       "no evaluator is defined for this declared floor")


def evaluate(
    family_id: str,
    registry: dict,
    forward_db: pathlib.Path,
    as_of: str,
    signal_id: str | None = None,
    repo_root: pathlib.Path | None = None,
) -> FloorReport:
    """Evaluate every floor the family declared. Never raises; never writes.

    A pre-registration problem becomes `error` on the report, and
    `floors_satisfied` is False -- it never becomes a pass.
    """
    try:
        prereg = resolve_pinned_prereg(family_id, registry, repo_root)
        floors = declared_floors(prereg)
    except PreRegistrationError as exc:
        return FloorReport(family_id, as_of, None, [], str(exc))

    obs = observe(forward_db, signal_id or family_id, as_of)
    results = [_evaluate_one(k, v, obs) for k, v in sorted(floors.items())]
    return FloorReport(family_id, as_of, str(prereg.relative_to(
        pathlib.Path(repo_root) if repo_root is not None else _REPO_ROOT)), results)
