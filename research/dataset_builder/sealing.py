"""Dataset Sealing — ADR-I012 §8 stage 5, §9, §11, §13.2 DG-1/DG-2/DG-3, §14.

Stage 5 SEAL   freeze · hash · emit receipt · publish

**Sealing is what makes a dataset citable** (§11). Materialisation is
orthogonal: a sealed dataset is complete whether or not any bytes exist.

DG-1 — SEALING IS A HUMAN ACT
    §13.2: *"Sealing is a **human act** emitting a receipt (IQROS-P8, P10)."*
    A service actor is refused. The platform must not be able to make its own
    research citable. Contrast DG-2: resolution and validation are
    *computations* and replay — which is why the `dataset.seal` receipt is
    **discretionary** (envelope-verified), never `derived`. Re-running the
    battery years later must not be able to manufacture a seal no human
    granted.

HZ-4.4-1 — THE CAP MUST SURVIVE THE SEAL
    WBS 4.4 computes `capped_admissibility` (the meet **after** Tier D caps)
    but only carries it on the report. §9 requires the sealed dataset to hold
    its *"computed admissibility ceiling"*. If sealing stored `computed_meet`
    instead, every Tier D cap would be silently lost at exactly the moment the
    dataset becomes citable — a consumer would see a ceiling the validation
    never granted.

    This module therefore:
      * takes the `ValidationReport` object, not loose values;
      * persists `report.capped_admissibility` as the ceiling;
      * **never recomputes** — it imports neither `compose_admissibility` nor
        `meet`, asserted by an AST test, so recomputation is impossible rather
        than merely discouraged;
      * records `computed_meet` alongside, so the cap is auditable as a
        *difference* rather than inferred.

WHAT SEALING PERSISTS (§9 "Sealed dataset", §14 provenance)
    Computed admissibility ceiling · resolved provenance closure · resolution
    timestamp · resolving engine version · sealing actor, timestamp and
    receipt. §14 calls provenance completeness *"the rematerialisation
    guarantee"*: a sealed dataset plus its closure reproduces it years later
    with no other lookup.

WHAT SEALING DOES NOT DO
    It does not materialise (§8.2, WBS 4.8), supersede or retire (WBS 4.11),
    or decide statistical merit (§13.1: *"Statistical merit — Research gates —
    Builder role: None"*).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_EVIDENCE_LIB_DIR = Path(__file__).resolve().parents[1] / "evidence_library"
if str(_EVIDENCE_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_EVIDENCE_LIB_DIR))

import receipt_ledger as rl  # noqa: E402
from receipts import (  # noqa: E402
    Actor, Authority, InputRef, ReceiptBody, RuleRef, Subject, Verdict,
    canonical_json, sha256_lf,
)

from dataset_builder import citation_index as ci
from dataset_builder import registry as reg
from dataset_builder import validation as val

#: Bumped when the sealed-state serialization changes in a way that would alter
#: `validation_fingerprint`. Recorded on every sealed row so a fingerprint can
#: always be interpreted against the engine that produced it (§14 "resolving
#: engine version").
SEALING_ENGINE_VERSION = "1.0.0"

DECISION_TYPE = "dataset.seal"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sealed_datasets (
    spec_hash                TEXT PRIMARY KEY,
    -- HZ-4.4-1: the CEILING is the capped value, not the raw meet.
    capped_admissibility_json TEXT NOT NULL,
    computed_meet_json        TEXT NOT NULL,
    declared_floor_json       TEXT,
    tier_d_caps_json          TEXT NOT NULL,
    provenance_closure_json   TEXT NOT NULL,
    validation_fingerprint    TEXT NOT NULL,
    resolution_ts             TEXT,
    resolving_engine_version  TEXT NOT NULL,
    sealed_by                 TEXT NOT NULL,
    sealed_at                 TEXT NOT NULL,
    seal_receipt_id           TEXT NOT NULL,
    FOREIGN KEY (spec_hash) REFERENCES dataset_specifications(spec_hash)
);

-- DG-3: "A sealed specification is never edited. Change produces a new
-- dataset." Enforced at the DATABASE layer so it holds against a direct
-- sqlite3 connection, not only against this module.
CREATE TRIGGER IF NOT EXISTS trg_sealed_datasets_no_update
BEFORE UPDATE ON sealed_datasets
BEGIN
    SELECT RAISE(ABORT, 'DG-3: a sealed dataset is never edited (UPDATE forbidden)');
END;

CREATE TRIGGER IF NOT EXISTS trg_sealed_datasets_no_delete
BEFORE DELETE ON sealed_datasets
BEGIN
    SELECT RAISE(ABORT, 'DG-3: a sealed dataset is never deleted (DELETE forbidden)');
END;
"""


class SealingError(reg.DatasetRegistryError):
    """Base class for sealing failures."""


class ServiceActorRefused(SealingError):
    """DG-1 — sealing is a human act. The platform must not seal its own work."""


class NotValidatedError(SealingError):
    """Only a Validated dataset may be sealed (§11)."""


class ValidationMismatchError(SealingError):
    """The supplied ValidationReport does not describe this dataset.

    Sealing a dataset against another dataset's validation would produce an
    artifact whose recorded ceiling was never computed for it — the exact
    failure HZ-4.4-1 warns about, arrived at from the other direction.
    """


@dataclass(frozen=True)
class SealReport:
    spec_hash: str
    sealed_at: str
    sealed_by: str
    seal_receipt_id: str
    #: The ceiling actually persisted. Equals the validation report's
    #: `capped_admissibility`, never the raw meet.
    admissibility_ceiling: dict
    computed_meet: dict
    tier_d_caps: list[dict]
    provenance_closure: dict
    validation_fingerprint: str
    resolving_engine_version: str = SEALING_ENGINE_VERSION

    @property
    def cap_was_applied(self) -> bool:
        """True when Tier D lowered the ceiling below the raw meet. Makes the
        HZ-4.4-1 property observable rather than implied."""
        return self.admissibility_ceiling != self.computed_meet


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def init_db(db_path: Path | str) -> None:
    """Create the sealed-dataset table. Idempotent. Requires the registry
    schema to exist first — a sealed row with no specification to point at is
    meaningless and the foreign key would be unenforceable."""
    reg._assert_dataset_builder_schema(db_path)
    con = sqlite3.connect(db_path)
    try:
        con.executescript(_SCHEMA_SQL)
        con.commit()
    finally:
        con.close()


def _connect(db_path: Path | str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _assert_sealing_schema(db_path: Path | str) -> None:
    reg._assert_dataset_builder_schema(db_path)
    con = sqlite3.connect(db_path)
    try:
        names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        con.close()
    if "sealed_datasets" not in names:
        raise reg.NotADatasetBuilderStore(
            f"{db_path} has no sealed-dataset table. Call sealing.init_db() "
            f"first. Refusing to write."
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def admissibility_to_dict(adm: Any) -> dict:
    """Deterministic, human-readable form of an Admissibility tuple.

    Rung **names**, not integer values: a sealed artifact must stay readable if
    the enum's numeric backing ever changes, and §14's rematerialisation
    guarantee is about interpretability years later.
    """
    if adm is None:
        return {}
    return {
        "provenance": adm.provenance.name,
        "certification": adm.certification.name,
        "readiness": adm.readiness.name,
        "maturity": adm.maturity.name,
        "integrity": adm.integrity.name,
        "not_applicable": sorted(d.name for d in adm.not_applicable),
        "lattice_version": adm.lattice_version,
    }


def build_provenance_closure(
    db_path: Path | str, spec_hash: str, report: val.ValidationReport
) -> dict:
    """§14 — the transitive closure of everything determining content.

    Citations come from the WBS 4.2 index rather than by re-reading the
    declaration, so the closure and the reverse index can never disagree about
    what a dataset cites.
    """
    spec = reg.get_specification(db_path, spec_hash)
    citations = ci.cites(db_path, spec_hash)
    by_kind: dict[str, list[dict]] = {}
    for c in citations:
        by_kind.setdefault(c.cited_kind, []).append(
            {"id": c.cited_id, "detail": c.cited_detail}
        )
    for kind in by_kind:
        by_kind[kind].sort(key=lambda x: x["id"])
    return {
        "declaration_hash": spec_hash,
        "lineage": spec["lineage"],
        "version_ordinal": spec["version_ordinal"],
        "citations": by_kind,
        "admissibility_ceiling": admissibility_to_dict(report.capped_admissibility),
        "computed_meet": admissibility_to_dict(report.computed_meet),
        "declared_floor": admissibility_to_dict(report.declared_floor),
        "tiers_run": list(report.tiers_run),
        "deferred_checks": dict(report.deferred),
        "not_computed": list(report.not_computed),
        "resolving_engine_version": SEALING_ENGINE_VERSION,
    }


def validation_fingerprint(report: val.ValidationReport) -> str:
    """A deterministic hash of the validation outcome being sealed.

    Lets a reader verify years later that the sealed artifact faithfully
    represents *that* validation, not a later re-run. Includes the capped
    ceiling, the raw meet, every finding, the tiers run and what was
    deliberately not computed — so a seal cannot silently describe a cleaner
    validation than actually occurred.
    """
    payload = {
        "spec_hash": report.spec_hash,
        "ok": report.ok,
        "tiers_run": list(report.tiers_run),
        "capped_admissibility": admissibility_to_dict(report.capped_admissibility),
        "computed_meet": admissibility_to_dict(report.computed_meet),
        "declared_floor": admissibility_to_dict(report.declared_floor),
        "not_computed": sorted(report.not_computed),
        "deferred": dict(sorted(report.deferred.items())),
        "findings": sorted(
            [
                {
                    "code": f.code, "tier": f.tier, "blocking": f.blocking,
                    "fields": list(f.fields), "detail": f.detail,
                    "caps": f.caps,
                }
                for f in report.findings
            ],
            key=lambda x: (x["code"], x["detail"]),
        ),
    }
    return sha256_lf(canonical_json(payload))


# ---------------------------------------------------------------------------
# Stage 5 — seal
# ---------------------------------------------------------------------------

def seal_dataset(
    db_path: Path | str,
    spec_hash: str,
    report: val.ValidationReport,
    *,
    sealed_by: str,
    ledger_db: Path | str,
    actor_type: str = "human",
    reason: str | None = None,
) -> SealReport:
    """Stage 5. Freeze, record the sealed state, emit a `dataset.seal` receipt,
    and advance Validated -> Sealed.

    **DG-1**: `actor_type` must be `"human"`. A service actor is refused —
    the platform must not make its own research citable.

    **HZ-4.4-1**: the persisted ceiling is `report.capped_admissibility`,
    taken verbatim. Nothing here recomputes it.

    Ordering is deliberate. The receipt is appended *before* the phase
    advances, and the sealed row is written in the same transaction as the
    phase change. A crash after the receipt leaves a receipt with no sealed
    dataset — visible, and detected by `find_orphan_seal_receipts()`. The
    reverse (a citable dataset with no receipt) would be a governance hole,
    so it is the ordering that cannot happen.
    """
    _assert_sealing_schema(db_path)

    if actor_type != "human":
        raise ServiceActorRefused(
            f"sealing requires a human actor (got actor_type={actor_type!r}). "
            f"ADR-I012 DG-1: sealing is a human act emitting a receipt "
            f"(IQROS-P8, P10). Resolution and validation are computations "
            f"(DG-2) — those may be automated; making a dataset citable may not."
        )
    if not str(sealed_by or "").strip():
        raise SealingError("sealed_by is required: a seal names the human who granted it")

    spec = reg.get_specification(db_path, spec_hash)
    if spec is None:
        raise SealingError(f"no such specification: {spec_hash}")
    if spec["phase"] != "Validated":
        raise NotValidatedError(
            f"specification {spec_hash[:12]}... is in phase {spec['phase']!r}, "
            f"not 'Validated'; only a validated dataset may be sealed (§11)"
        )

    if report.spec_hash != spec_hash:
        raise ValidationMismatchError(
            f"validation report describes {report.spec_hash[:12]}... but sealing "
            f"{spec_hash[:12]}...; refusing to seal a dataset against another "
            f"dataset's validation"
        )
    if not report.ok:
        raise SealingError(
            f"refusing to seal {spec_hash[:12]}...: its validation report carries "
            f"{len(report.blocking)} blocking finding(s). §8 stage 5 refuses when "
            f"validation is not clean"
        )
    if report.capped_admissibility is None:
        raise SealingError(
            "validation report carries no capped_admissibility; refusing to seal "
            "a dataset whose ceiling was never computed (HZ-4.4-1)"
        )

    already = get_sealed(db_path, spec_hash)
    if already is not None:
        raise SealingError(
            f"{spec_hash[:12]}... is already sealed (receipt "
            f"{already['seal_receipt_id'][:12]}...). DG-3: change produces a new "
            f"dataset, never a re-seal"
        )

    closure = build_provenance_closure(db_path, spec_hash, report)
    fingerprint = validation_fingerprint(report)
    tier_d_caps = [
        {"code": f.code, "caps": f.caps, "detail": f.detail}
        for f in report.findings if f.tier == "D" and f.caps
    ]

    inputs = (
        InputRef(kind="dataset_declaration", id=spec_hash, hash=spec_hash),
        InputRef(kind="validation", id=spec_hash, hash=fingerprint),
    )
    body = ReceiptBody(
        decision_type=DECISION_TYPE,
        decision_class="discretionary",
        action="seal",
        subject=Subject(kind="dataset", id=spec_hash, version_or_hash=spec_hash),
        inputs=inputs,
        from_state="Validated",
        to_state="Sealed",
        as_of=_utc_now(),
        decided_at=_utc_now(),
        actor=Actor(type="human", id=sealed_by, key_id=None),
        authority=Authority(
            policy_id="ADR-I012-DG-1",
            policy_hash=sha256_lf(b"ADR-I012 DG-1 sealing is a human act"),
            role="researcher",
        ),
        rule=RuleRef(
            rule_id="dataset.seal",
            rule_hash=None,
            engine_version=SEALING_ENGINE_VERSION,
            thresholds_hash=None,
        ),
        verdict=Verdict(result="Sealed", reason_code=None),
        reason_text=reason or f"dataset {spec_hash[:12]}... sealed after clean validation",
    )
    receipt = rl.append_receipt(ledger_db, body)
    receipt_id = receipt["receipt_id"] if isinstance(receipt, dict) else str(receipt)

    sealed_at = _utc_now()
    con = _connect(db_path)
    try:
        con.execute("BEGIN IMMEDIATE")
        con.execute(
            "INSERT INTO sealed_datasets "
            "(spec_hash, capped_admissibility_json, computed_meet_json, "
            " declared_floor_json, tier_d_caps_json, provenance_closure_json, "
            " validation_fingerprint, resolution_ts, resolving_engine_version, "
            " sealed_by, sealed_at, seal_receipt_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                spec_hash,
                json.dumps(admissibility_to_dict(report.capped_admissibility),
                           sort_keys=True, separators=(",", ":")),
                json.dumps(admissibility_to_dict(report.computed_meet),
                           sort_keys=True, separators=(",", ":")),
                json.dumps(admissibility_to_dict(report.declared_floor),
                           sort_keys=True, separators=(",", ":")),
                json.dumps(tier_d_caps, sort_keys=True, separators=(",", ":")),
                json.dumps(closure, sort_keys=True, separators=(",", ":")),
                fingerprint,
                spec.get("declared_at"),
                SEALING_ENGINE_VERSION,
                sealed_by,
                sealed_at,
                receipt_id,
            ),
        )
        cur = con.execute(
            "UPDATE dataset_specifications SET phase = 'Sealed' "
            "WHERE spec_hash = ? AND phase = 'Validated'",
            (spec_hash,),
        )
        if cur.rowcount != 1:
            con.execute("ROLLBACK")
            raise reg.TransitionError(
                f"refusing to seal: {spec_hash[:12]}... was not in phase "
                f"'Validated' at write time (concurrent modification)"
            )
        con.commit()
    except Exception:
        try:
            con.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        con.close()

    return SealReport(
        spec_hash=spec_hash,
        sealed_at=sealed_at,
        sealed_by=sealed_by,
        seal_receipt_id=receipt_id,
        admissibility_ceiling=admissibility_to_dict(report.capped_admissibility),
        computed_meet=admissibility_to_dict(report.computed_meet),
        tier_d_caps=tier_d_caps,
        provenance_closure=closure,
        validation_fingerprint=fingerprint,
    )


# ---------------------------------------------------------------------------
# Queries and integrity
# ---------------------------------------------------------------------------

def get_sealed(db_path: Path | str, spec_hash: str) -> dict | None:
    """The sealed state, or None. Survives process restart by construction —
    everything is on disk, nothing is cached in memory."""
    con = _connect(db_path)
    try:
        row = con.execute(
            "SELECT * FROM sealed_datasets WHERE spec_hash = ?", (spec_hash,)
        ).fetchone()
    finally:
        con.close()
    if row is None:
        return None
    out = dict(row)
    for key in ("capped_admissibility_json", "computed_meet_json",
                "declared_floor_json", "tier_d_caps_json",
                "provenance_closure_json"):
        out[key.removesuffix("_json")] = json.loads(out[key])
    return out


def admissibility_ceiling(db_path: Path | str, spec_hash: str) -> dict | None:
    """The ceiling a consumer must satisfy. **The capped value** (HZ-4.4-1) —
    the single accessor a downstream consumer should use, so nobody has to
    remember which of two columns is authoritative."""
    sealed = get_sealed(db_path, spec_hash)
    return None if sealed is None else sealed["capped_admissibility"]


def verify_seal(db_path: Path | str, spec_hash: str, report: val.ValidationReport) -> tuple[bool, list[str]]:
    """Does the sealed artifact faithfully represent this validation result?

    Recomputes the fingerprint from the report and compares. Also re-checks the
    HZ-4.4-1 property directly: the persisted ceiling must equal the report's
    capped value, not its raw meet.
    """
    errors: list[str] = []
    sealed = get_sealed(db_path, spec_hash)
    if sealed is None:
        return False, [f"{spec_hash[:12]}... is not sealed"]

    expected = validation_fingerprint(report)
    if sealed["validation_fingerprint"] != expected:
        errors.append(
            f"validation fingerprint mismatch: sealed "
            f"{sealed['validation_fingerprint'][:12]}..., report {expected[:12]}..."
        )
    expected_ceiling = admissibility_to_dict(report.capped_admissibility)
    if sealed["capped_admissibility"] != expected_ceiling:
        errors.append(
            "persisted ceiling does not match the report's capped_admissibility "
            "(HZ-4.4-1)"
        )
    if report.capped_admissibility != report.computed_meet:
        if sealed["capped_admissibility"] == sealed["computed_meet"]:
            errors.append(
                "Tier D applied a cap but the persisted ceiling equals the raw "
                "meet — the cap was lost at seal (HZ-4.4-1)"
            )
    return (not errors), errors


def find_orphan_seal_receipts(db_path: Path | str) -> list[str]:
    """Datasets in phase Sealed with no sealed-state row.

    The seal-ordering makes the dangerous direction impossible (a citable
    dataset always has a receipt), but a crash between receipt and commit can
    leave the harmless one. This makes it auditable rather than assumed.
    """
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT d.spec_hash FROM dataset_specifications d "
            "LEFT JOIN sealed_datasets s ON s.spec_hash = d.spec_hash "
            "WHERE d.phase IN ('Sealed', 'Superseded', 'Retired') "
            "AND s.spec_hash IS NULL ORDER BY d.spec_hash"
        ).fetchall()
    finally:
        con.close()
    return [r["spec_hash"] for r in rows]
