"""
Contract resolution — docs/governance/contract_versioning.md §5.

**Three different operations need a contract, and they must resolve it by three
different mechanisms. Conflating them is the bug.** This module therefore
exposes them as three separately-named functions, and deliberately provides NO
function called `get_contract()`:

    resolve_for_derivation(contract_id)   -> the unique ACTIVE version
    resolve_for_replay(contract_hash)     -> dereference the exact bytes
    resolve_for_reprocessing(..., policy) -> an EXPLICIT human choice

"Ambiguity at the call site is the defect this whole document exists to
eliminate; do not reintroduce it in the API."

## Why hash-addressing, not date-resolution

The intuitive design — "look up which contract version was active on
2026-06-14" — is wrong, and it will be proposed, so here is why it is refused:

  * **Backfills invert the clocks.** A day derived on 2026-07-20 for period
    2026-06-14 has two dates. Either choice is defensible, which means the
    choice is a bug waiting to be re-litigated.
  * **Activation intervals are mutable metadata.** If `active_from` is a
    column, it can be edited, and history silently re-judges itself.
  * **A git checkout changes history.** If replay reads `contracts/*.yaml`,
    then checking out an older commit changes what a 2026-06-14 receipt MEANS.
    The filesystem is not an archive.

Hash-addressing dissolves all three: the receipt recorded `contract_hash=9f3c…`,
and replay fetches exactly those bytes from an append-only registry. **There is
no question to get wrong.**

## Replay never reads the filesystem

Enforced structurally, not by convention: `resolve_for_replay()` reaches
`contract_registry.resolve_by_hash()`, which contains no file-reading code path
at all. The ONE function in this module that touches the filesystem is
`load_working_copy()`, which is an AUTHORING/ACTIVATION tool and is never
reachable from replay. A test asserts this by AST inspection rather than by
trusting the docstring.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

_EVIDENCE_LIBRARY = Path(__file__).resolve().parents[1] / "evidence_library"
if str(_EVIDENCE_LIBRARY) not in sys.path:
    sys.path.insert(0, str(_EVIDENCE_LIBRARY))

import contract_registry as cr  # noqa: E402

from contract import (  # noqa: E402
    Contract,
    ContractConfigurationError,
    parse_contract_text,
)

# §5.1: "There is no default. A reprocessing job invoked without
# --contract-policy REFUSES TO RUN. The distinction is too consequential to be
# inherited from a config file, and the person running a repair at 2am is
# exactly the person who must be made to state which one they mean."
CONTRACT_POLICY_PINNED: Final = "pinned"
CONTRACT_POLICY_CURRENT: Final = "current"
CONTRACT_POLICIES: Final = frozenset({CONTRACT_POLICY_PINNED, CONTRACT_POLICY_CURRENT})


class ContractResolutionError(ContractConfigurationError):
    """Base class for a contract that could not be resolved."""


class NoActiveContractError(ContractResolutionError):
    """
    No ACTIVE version exists for this contract_id.

    Not a fallback to a draft, and not a fallback to the filesystem: a draft
    contract in a live pipeline is a CONFIGURATION_ERROR (§2.1). A pipeline
    gated by a contract nobody activated is a pipeline gated by nothing.
    """


class ContractPolicyRequiredError(ContractResolutionError):
    """Raised when a reprocessing entry point is invoked with no explicit policy."""


class ContractTamperError(ContractResolutionError):
    """Working copy diverges from the ACTIVE registry bytes. HALT (§2.3)."""


@dataclass(frozen=True)
class ResolvedContract:
    """A contract plus the registry identity it was resolved under. The version
    and hash travel with it into the receipt — a contract without its identity
    is exactly the ambiguity this module exists to remove."""
    contract: Contract
    contract_hash: str
    version: int
    phase: str

    @property
    def contract_id(self) -> str:
        return self.contract.contract_id


def _to_resolved(record: dict) -> ResolvedContract:
    contract = parse_contract_text(record["body"])
    if contract.contract_id != record["contract_id"]:
        raise ContractResolutionError(
            f"registry row declares contract_id '{record['contract_id']}' but its frozen "
            f"body declares '{contract.contract_id}' — the archive is inconsistent"
        )
    # Defence in depth: the registry's own hash must agree with a fresh hash of
    # the bytes it returned. A mismatch means the archive was tampered with
    # under the append-only triggers, which is a hard stop, never a warning.
    if not cr.verify_contract_hash(record):
        raise ContractResolutionError(
            f"CONTRACT_TAMPER: registry body for {record['contract_hash']} does not hash "
            f"to its own contract_hash"
        )
    return ResolvedContract(
        contract=contract,
        contract_hash=record["contract_hash"],
        version=record["version"],
        phase=record["phase"],
    )


def resolve_for_derivation(db_path: Path | str, contract_id: str) -> ResolvedContract:
    """
    FRESH DERIVATION (no receipt exists yet).

    Resolves by contract_id to the unique ACTIVE version. Not by date. Not by
    interval search. Exactly one active version exists per contract_id — a
    partial unique index in `contract_registry` enforces it at the DATABASE
    layer — so there is nothing to choose between and nothing to get wrong.
    """
    record = cr.get_active(db_path, contract_id)
    if record is None:
        raise NoActiveContractError(
            f"no ACTIVE contract version for contract_id '{contract_id}'. A draft binds "
            f"nothing and may not gate a live derivation; activate it through the contract "
            f"registry (contract_lifecycle.activate_contract) first."
        )
    return _to_resolved(record)


def resolve_for_replay(db_path: Path | str, contract_hash: str) -> ResolvedContract:
    """
    REPLAY / AUDIT (a receipt exists).

    **Dereferences.** It does not choose. The receipt names a hash; the registry
    returns exactly those bytes — including for a version that was superseded
    three times and retired years ago, because nothing is ever pruned and a
    receipt from 2026 must remain dereferenceable in 2031.

    Raises (via contract_registry.ContractNotFoundError) if the hash does not
    resolve. There is no filesystem fallback, by construction.
    """
    record = cr.resolve_by_hash(db_path, contract_hash)
    return _to_resolved(record)


def resolve_for_reprocessing(
    db_path: Path | str,
    contract_id: str,
    pinned_hash: str | None,
    contract_policy: str | None,
) -> ResolvedContract:
    """
    REPROCESSING / REPAIR (a receipt exists, but we intend to re-derive).

    **The one place a human must choose, and the choice is never defaulted.**

        --contract-policy pinned    use the historical contract_hash from the
                                    existing receipt. Reproduces the original
                                    verdict exactly.
                                    USE WHEN: the contract was right and
                                    something ELSE was wrong (a derivation bug,
                                    a lost row, an audit re-run).

        --contract-policy current   use today's active contract. DELIBERATELY
                                    re-judges history under new rules.
                                    USE WHEN: the CONTRACT ITSELF was wrong.

    H6 is the worked example of `current` (§5.2): the old, implicit contract
    admitted a day missing an entire funding event and enforced no day-closure
    at all. Replaying those rows `pinned` would faithfully reproduce the
    corruption — which is precisely the RIGHT answer to "what did we believe on
    2026-07-12?" and precisely the WRONG basis for the corpus going forward.

    This converts a silent re-admission into a signed one. Law V1: activating a
    relaxed contract NEVER retroactively re-admits anything. Re-judging history
    is a separate, explicit, governed act — this function is the point at which
    a human says so, on the record.
    """
    if contract_policy is None:
        raise ContractPolicyRequiredError(
            "reprocessing requires an explicit --contract-policy: 'pinned' (reproduce the "
            "original verdict under the contract that was in force) or 'current' "
            "(deliberately re-judge history under today's contract). There is no default: "
            "re-judging history must be something a human states, on the record."
        )
    if contract_policy not in CONTRACT_POLICIES:
        raise ContractPolicyRequiredError(
            f"unknown contract policy '{contract_policy}'; expected one of {sorted(CONTRACT_POLICIES)}"
        )

    if contract_policy == CONTRACT_POLICY_PINNED:
        if not pinned_hash:
            raise ContractPolicyRequiredError(
                "contract_policy 'pinned' requires the historical contract_hash from the "
                "existing receipt; none was supplied"
            )
        return resolve_for_replay(db_path, pinned_hash)

    return resolve_for_derivation(db_path, contract_id)


# ---------------------------------------------------------------------------
# Authoring / activation only. NEVER reachable from replay.
# ---------------------------------------------------------------------------

def load_working_copy(path: Path | str) -> Contract:
    """
    Parse a working-copy contract file (`contracts/*.yaml`).

    **Authoring and activation only.** The filesystem `contracts/` directory is
    a WORKING COPY; the registry is the archive. A contract loaded here binds
    nothing until it is activated through `contract_lifecycle.activate_contract()`,
    which freezes its bytes and stamps its monotonic version.

    The activation tool assigns `version` — the author never writes it. Two
    authors on two branches each hand-writing `version: 4` would produce two
    v4s with different hashes, an ambiguity that would silently corrupt every
    conversation about the system thereafter.
    """
    text = Path(path).read_text(encoding="utf-8")
    return parse_contract_text(text)


def check_tamper(db_path: Path | str, working_copy: Contract) -> None:
    """
    CONTRACT_TAMPER detection (§2.3): the working copy's hash must equal the
    registry's ACTIVE hash for that contract_id.

    This is `FnHashViolation` for contracts, and it exists for the same reason:
    a change to what "complete" MEANS, made without going through activation,
    retroactively alters the admissibility of evidence with NO TRACE. That is
    precisely the class of silent corruption the platform exists to prevent.

    CRLF/LF is normalised away by sha256_lf (the platform's one hashing rule),
    so a Windows working copy and a Linux deploy of identical content do not
    fire a spurious violation. That lesson is already paid for; it is inherited,
    not re-learned.
    """
    active = resolve_for_derivation(db_path, working_copy.contract_id)
    if working_copy.contract_hash() != active.contract_hash:
        raise ContractTamperError(
            f"CONTRACT_TAMPER: working copy of '{working_copy.contract_id}' hashes to "
            f"{working_copy.contract_hash()[:12]}… but the ACTIVE registry version is "
            f"{active.contract_hash[:12]}… (v{active.version}). An activated contract is "
            f"frozen forever. Editing one in place, rather than activating a new version, "
            f"changes what 'complete' means with no trace and no governance record."
        )
