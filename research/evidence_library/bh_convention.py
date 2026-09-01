"""
bh_convention.py
================
Classify a stored `runs.bh_p` value by the statistical convention that produced
it. Implements **FDB-13** of `docs/governance/fb10b_forward_dsr_bh_contract.md`.

WHY THIS EXISTS
---------------
`runs.bh_p` is one `REAL` column now carrying two different statistics. Three
rows written before the FB-10b contract hold a **raw p-value**
(`1 - psr(returns, 0.0)`, in-sample, `hold_days = 1`) produced by
`compute_dsr_for_registration.py`. Every value written under the contract holds
a **BH-adjusted q-value** (OD-4 = D2). A surface rendering both under one label
invites a reader to compare quantities that are not comparable, and a raw p is
not a BH statistic at all -- it is the input BH consumes.

FDB-7 already binds the reporting layer ("computed, stored **or reported**").
FDB-13 makes the labelling duty explicit and general.

THE DISCRIMINATOR IS THE CONTRACT'S OWN VALIDITY CONDITION
----------------------------------------------------------
FDB-9 requires every stored DSR/BH result to record twelve fields, and states
that "a result missing any of these is uninterpretable and invalid". So a
`bh_p` accompanied by the complete FDB-9 block was produced under this contract;
one without it was not.

That is not a heuristic and not a proxy. It reads a condition the contract
already states, rather than inferring convention from lane, engine_version or
date -- any of which would be a correlate that a future row could break.
Verified against the real ledger: all three legacy rows carry **zero** of the
twelve fields, and their metrics keys are the disjoint Phase-B discovery set.

WHAT THIS MODULE DOES NOT DO
----------------------------
It never reads, parses, compares, rescales or transforms the numeric `bh_p`; it
inspects **key presence** only. It imports no statistical machinery, computes
nothing, converts no raw p into a q, opens no database, and rewrites nothing.
Classification is a pure function of a run dict already in memory.
"""
from __future__ import annotations

from typing import Any

#: The twelve fields FDB-9 requires of every stored DSR/BH result, verbatim and
#: in the contract's own order. Presence of ALL of them is what identifies a
#: result as having been produced under the FB-10b contract.
FDB9_REQUIRED_FIELDS: tuple[str, ...] = (
    "battery_members",
    "battery_excluded",
    "sr_trials_std",
    "sr_trials_std_basis",
    "n_trials",
    "q",
    "psr0",
    "sr_star",
    "return_basis",
    "n",
    "as_of",
    "calc_version",
)

#: `bh_p` is NULL (or NaN): nobody computed it. Distinct from both conventions,
#: and never collapsed into either -- absence is not a value.
UNCOMPUTED = "uncomputed"

#: `bh_p` is present but the FDB-9 block is not: an in-sample raw p-value
#: written before the contract existed. Never a BH statistic.
LEGACY_RAW_P = "legacy_raw_p"

#: `bh_p` is present with the complete FDB-9 block: the BH-adjusted q-value
#: defined by FDB-13, comparable against `q` and against other members of its
#: own frozen battery.
FORWARD_ADJUSTED_Q = "forward_adjusted_q"

_SHORT_LABEL: dict[str, str] = {
    LEGACY_RAW_P: "legacy raw p",
    FORWARD_ADJUSTED_Q: "BH-adjusted q",
}

_DESCRIPTION: dict[str, str] = {
    UNCOMPUTED: "not computed",
    LEGACY_RAW_P: (
        "legacy raw p-value, in-sample, recorded before the FB-10b contract "
        "and NOT a BH-adjusted statistic"
    ),
    FORWARD_ADJUSTED_Q: "BH-adjusted q-value (FDB-13)",
}


def has_fdb9_block(metrics: Any) -> bool:
    """True when `metrics` carries the complete FDB-9 field set.

    Checked at the top level and one nesting level down, because FDB-9 requires
    the fields to be *recorded* without fixing where. Deliberately imposing no
    location requirement of its own: the producer gate remains free to nest the
    block under a named key or keep it flat, and this rule works either way.

    Completeness is required. FDB-9 makes a result missing ANY of the twelve
    "uninterpretable and invalid", so a partial block is not a weaker form of a
    valid result -- it is a defect that FDB-9 forbids writing at all.
    """
    if not isinstance(metrics, dict):
        return False
    required = set(FDB9_REQUIRED_FIELDS)
    if required <= set(metrics):
        return True
    return any(isinstance(v, dict) and required <= set(v) for v in metrics.values())


def classify(run: Any) -> str:
    """The convention of this run's `bh_p`: one of the three module constants.

    NaN is treated as UNCOMPUTED, mirroring
    `lifecycle.check_validated_preconditions`, which already refuses to
    distinguish NULL from NaN. This function does not otherwise look at the
    value.
    """
    if not isinstance(run, dict):
        return UNCOMPUTED
    value = run.get("bh_p")
    if value is None or (isinstance(value, float) and value != value):
        return UNCOMPUTED
    return FORWARD_ADJUSTED_Q if has_fdb9_block(run.get("metrics")) else LEGACY_RAW_P


def short_label(convention: str) -> str | None:
    """Compact marker for a rendered value, or None when the rendered text is
    already self-describing (UNCOMPUTED renders as its own reason)."""
    return _SHORT_LABEL.get(convention)


def describe(convention: str) -> str:
    """Prose naming the convention, for operator-facing explanations."""
    return _DESCRIPTION.get(convention, convention)
