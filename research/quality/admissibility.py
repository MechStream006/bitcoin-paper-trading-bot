"""
Certification -> Admissibility bridge (ADR-I009 §18, ADR-I014).

research/quality/gauntlet.py already runs a real quality battery and
produces a report dict -- but, per the engineering roadmap's Gap Ledger,
"gates nothing": no storage or research path reads its verdict. This module
is the "connective work" WBS 1.4 calls for: it maps a gauntlet report onto
the five-dimension Admissibility lattice built in Phase 0
(research/admissibility/), so a certification verdict becomes an
admissibility CEILING an artifact actually carries (ADR-I009 GV2: "An
uncertified span caps admissibility"), rather than a report nobody reads.

This module computes a ceiling. It never refuses, blocks, or gates
ingestion on the result (ADR-I012 DA3: "A dataset seals successfully at any
ceiling. A low ceiling is a label, not a failure.") -- that discipline is
preserved here by construction: nothing in this module raises based on a
report's content, only on malformed input.

Engineering roadmap Phase 1, WBS 1.4 pilot -- wired only into
research/ingest/dvol.py so far.

Public API:
    certification_from_gauntlet(report) -> lattice.Certification
    readiness_from_gauntlet(report) -> lattice.Readiness
    partition_ceiling(report, *, provenance=Provenance.OFFLINE_CORPUS) -> lattice.Admissibility
    corpus_ceiling(*ceilings) -> lattice.Admissibility
"""
from __future__ import annotations

import sys
from pathlib import Path

_ADMISSIBILITY_DIR = Path(__file__).resolve().parents[1] / "admissibility"
if str(_ADMISSIBILITY_DIR) not in sys.path:
    sys.path.insert(0, str(_ADMISSIBILITY_DIR))

import composition  # noqa: E402
from lattice import (  # noqa: E402
    Admissibility,
    Certification,
    Dimension,
    Integrity,
    Maturity,
    Provenance,
    Readiness,
)


def certification_from_gauntlet(report: dict) -> Certification:
    """
    Map a research/quality/gauntlet.py report onto the Certification chain.

    gauntlet.py's own docstring defines its two severities: CRITICAL ("data
    corruption or structural errors that make the partition unsuitable for
    analysis") and WARNING ("anomalies that warrant inspection but do not
    invalidate the data"). This is a direct, mechanical translation of that
    existing severity model onto ADR-I014's Certification chain -- no new
    quality rule is introduced here.

        any CRITICAL finding  -> RED     (unsuitable for analysis)
        WARNING, no CRITICAL  -> AMBER   (usable, flagged for inspection)
        no findings at all    -> GREEN   (clean)
    """
    if report.get("critical_count", 0) > 0:
        return Certification.RED
    if report.get("warning_count", 0) > 0:
        return Certification.AMBER
    return Certification.GREEN


def readiness_from_gauntlet(report: dict) -> Readiness:
    """
    Map a gauntlet report's coverage_validation finding onto the Readiness
    chain -- "were the inputs complete?" (ADR-I014 §2.2).

        a CRITICAL coverage_validation finding -> NOT_READY
        a WARNING coverage_validation finding  -> PARTIAL
        no coverage_validation finding         -> READY
    """
    coverage_findings = [
        f for f in report.get("findings", []) if f.get("check") == "coverage_validation"
    ]
    if any(f.get("severity") == "CRITICAL" for f in coverage_findings):
        return Readiness.NOT_READY
    if any(f.get("severity") == "WARNING" for f in coverage_findings):
        return Readiness.PARTIAL
    return Readiness.READY


def partition_ceiling(
    report: dict,
    *,
    provenance: Provenance = Provenance.OFFLINE_CORPUS,
) -> Admissibility:
    """
    The admissibility ceiling for one certified partition.

    provenance defaults to OFFLINE_CORPUS: every source wired to this
    module ingests from a live production API (Deribit/Binance) into what
    this platform's own governance vocabulary already calls the offline
    research corpus (CLAUDE.md, evidence_reporting_architecture.md) -- not
    "live-forward" (forward-collected evidence for out-of-sample
    validation) and not "mainnet" (live trading), neither of which apply to
    a historical-backfill ingest partition.

    Maturity is marked not-applicable (ADR-I014 §5.1): a raw/canonical
    market-data partition has no maturity concept -- that is specific to
    Label Values (ADR-I011), a different artifact class this module does
    not produce.

    Integrity is CLEAN: a freshly-landed, freshly-certified partition has
    no recorded flag against it. Superseded-input flag propagation
    (ADR-I011 §10.3) is not implemented by this module -- correctly out of
    scope; nothing in this pilot yet corrects a prior revision in a way
    that would need to flag a dependent.
    """
    return Admissibility(
        provenance=provenance,
        certification=certification_from_gauntlet(report),
        readiness=readiness_from_gauntlet(report),
        maturity=Maturity.FULL,
        integrity=Integrity.CLEAN,
        not_applicable=frozenset({Dimension.MATURITY}),
    )


def corpus_ceiling(*ceilings: Admissibility) -> Admissibility:
    """
    The admissibility ceiling of a corpus spanning several partitions --
    the meet across their individual ceilings (ADR-I014 §4.1, IQROS-P4:
    "nothing may be declared more admissible than its weakest input").

    Thin, intentional wrapper around research/admissibility/composition.py's
    meet() -- this module adds no new composition logic of its own.
    """
    return composition.meet(*ceilings)
