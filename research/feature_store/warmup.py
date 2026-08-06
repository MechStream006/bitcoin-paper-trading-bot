"""
Warm-up as a governed contract term (ADR-I010 §8.4, §12.4 Tier C;
engineering roadmap Phase 2, WBS 2.6).

ADR-I010 §8.4 (PS4's resolution): "A resolution whose available history is
shorter than the declared warm-up is refused, not silently truncated...
Warm-up is a property of the Specification, not of the caller's
discipline." §12.4's resolve() flowchart places the warm-up check EARLY --
before the materialised/cache/compute steps -- as part of "Tier C:
Resolution-time integrity" (§13).

WHY THIS IS A SEPARATE MODULE, NOT A CHANGE TO value_store.py OR
registry.py. value_store.py's own docstring already states the scoped
simplification this module fills: "this module has no knowledge of
admissibility floors or warm-up requirements... that is a consumer-facing
policy concern... a future consumer... is expected to perform those checks
before calling resolve() here." registry.py's `warmup_periods` field
(WBS 2.1) already declares the requirement on every Specification --
Tier A already refuses a Specification with no warm-up declared. What was
missing, and what this module adds, is the Tier C check itself: comparing
a declared `warmup_periods` against actually-available history at
resolution time. Neither existing module is modified; this is purely
additive, matching the architecture's own already-documented separation
of concerns (registration-time validation lives in registry.py;
resolution-time validation is a distinct concern, deliberately left open
by value_store.py for exactly this).

GOVERNED ROLLOUT (roadmap §16 R5, §14). Warm-up refusal is a genuine
behaviour change -- flipping straight to hard refusal could surface
currently-hidden inadmissibility across the real research corpus with no
staging. The roadmap's own risk mitigation: "recommend running... in
'warn, don't refuse' mode for one full increment before flipping to hard
refusal." `warmup_policy.yaml`'s `mode` key is that flag:

    disabled -- never checked (pre-WBS-2.6 behaviour, unchanged)
    warn     -- insufficient history is reported, resolution proceeds
                (the rollout default)
    refuse   -- insufficient history raises WarmupRefusal (ADR-I010 §8.4's
                fully resolved behaviour)

Rollback lever (roadmap §14): set `mode: disabled` in warmup_policy.yaml,
or delete the file entirely -- both fully restore pre-WBS-2.6 behaviour
with no code change, matching the roadmap's own stated lever exactly
("Remove/comment the Specification's warmup requirement in
thresholds.yaml-style config").
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

_THIS_DIR = Path(__file__).resolve().parent
_POLICY_PATH = _THIS_DIR / "warmup_policy.yaml"

MODES = frozenset({"disabled", "warn", "refuse"})

_DEFAULT_MODE = "warn"


class WarmupPolicyError(ValueError):
    """Raised when a warm-up mode (from the policy file, or passed
    explicitly) is not one of MODES. Absence of a mode/file is NOT an
    error -- it falls back to _DEFAULT_MODE -- but a PRESENT, malformed
    value is (Ph6: no silent substitution for a malformed config)."""


class WarmupRefusal(ValueError):
    """
    Raised only in "refuse" mode when available history is shorter than a
    Specification's declared warmup_periods. ADR-I010 §8.4: "refused, not
    silently truncated" -- this is that refusal, never a truncated,
    partially-valid result.
    """

    def __init__(self, spec_name: str, warmup_periods: int, available_periods: int):
        self.spec_name = spec_name
        self.warmup_periods = warmup_periods
        self.available_periods = available_periods
        super().__init__(
            f"{spec_name}: warm-up refused -- requires {warmup_periods} periods "
            f"of history, only {available_periods} available"
        )


@dataclass(frozen=True)
class WarmupCheckResult:
    ok: bool                  # True unless this call raised WarmupRefusal
    sufficient: bool          # True iff available_periods >= warmup_periods
    mode: str
    spec_name: str
    warmup_periods: int
    available_periods: int
    action: str                # "proceed" | "proceed_with_warning" | "refused"
    detail: str | None = None


def load_warmup_mode(policy_path: Path | str | None = None) -> str:
    """
    Reads the `mode` key from warmup_policy.yaml. A missing file, or a
    missing `mode` key within an existing file, both fall back to
    _DEFAULT_MODE ("warn") -- the safe rollout default (roadmap §16 R5).
    An explicitly-present but unrecognised mode value is a hard error.
    """
    path = Path(policy_path) if policy_path is not None else _POLICY_PATH
    if not path.exists():
        return _DEFAULT_MODE
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    mode = data.get("mode", _DEFAULT_MODE)
    if mode not in MODES:
        raise WarmupPolicyError(
            f"warmup_policy.yaml names unknown mode {mode!r}; must be one of {sorted(MODES)}"
        )
    return mode


def check_warmup(
    spec_name: str,
    warmup_periods: int,
    available_periods: int,
    *,
    mode: str | None = None,
) -> WarmupCheckResult:
    """
    Tier C warm-up check (ADR-I010 §12.4, §13).

    Sufficient history (available_periods >= warmup_periods) ALWAYS
    proceeds, in every mode -- `mode` only changes what happens when
    history is insufficient:

        disabled -> proceeds silently (today's pre-WBS-2.6 behaviour)
        warn     -> proceeds, insufficiency reported in the returned
                    result (never raises)
        refuse   -> raises WarmupRefusal

    `mode` defaults to whatever warmup_policy.yaml currently declares
    (load_warmup_mode()) if not passed explicitly.
    """
    if mode is None:
        mode = load_warmup_mode()
    if mode not in MODES:
        raise WarmupPolicyError(f"unknown warm-up mode {mode!r}; must be one of {sorted(MODES)}")

    sufficient = available_periods >= warmup_periods

    if sufficient:
        return WarmupCheckResult(
            ok=True, sufficient=True, mode=mode, spec_name=spec_name,
            warmup_periods=warmup_periods, available_periods=available_periods,
            action="proceed",
        )

    detail = (
        f"{spec_name}: available history ({available_periods}) is shorter than "
        f"declared warm-up ({warmup_periods})"
    )

    if mode == "disabled":
        return WarmupCheckResult(
            ok=True, sufficient=False, mode=mode, spec_name=spec_name,
            warmup_periods=warmup_periods, available_periods=available_periods,
            action="proceed", detail=detail,
        )
    if mode == "warn":
        return WarmupCheckResult(
            ok=True, sufficient=False, mode=mode, spec_name=spec_name,
            warmup_periods=warmup_periods, available_periods=available_periods,
            action="proceed_with_warning", detail=detail,
        )

    # mode == "refuse"
    raise WarmupRefusal(spec_name, warmup_periods, available_periods)
