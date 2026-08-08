"""
Phase 4 operational closure — create the governed store and seal the first
Research Dataset against real Phase 1–3 data (exit criterion `P4-1`).

This is an **operational** script, not a development fixture. Everything it
declares is read from the real corpus, the real Feature Store and the real
Label Store; nothing is invented and nothing is a placeholder.

Two properties make it safe to keep in the repository and re-run:

- **Idempotent.** The specification hash is the content hash of the
  declaration (DG-3), so a second run resolves to the same hash, finds it
  already Sealed, and stops without a second seal or a second receipt.
- **Refuses rather than adapts.** If the corpus no longer supports what the
  declaration claims, resolution or validation refuses and the refusal is
  recorded permanently. The script never relaxes a declaration to make it pass.

Run:  python research/dataset_builder/bootstrap_first_dataset.py [--commit]
"""

from __future__ import annotations

import argparse
import collections
import glob
import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT / "research", ROOT / "research" / "admissibility",
           ROOT / "research" / "evidence_library"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import receipt_ledger as rl  # noqa: E402
from lattice import (  # noqa: E402
    Admissibility, Certification, Integrity, Maturity, Provenance, Readiness,
)

from dataset_builder import citation_index as ci  # noqa: E402
from dataset_builder import history as hist  # noqa: E402
from dataset_builder import materialisation as mat  # noqa: E402
from dataset_builder import registry as reg  # noqa: E402
from dataset_builder import resolution as res  # noqa: E402
from dataset_builder import sealing as sl  # noqa: E402
from dataset_builder import validation as val  # noqa: E402

STORE = ROOT / "research" / "dataset_builder" / "dataset_builder.db"
LEDGER = ROOT / "research" / "evidence_library" / "ledger.db"
FEATURE_STORE = ROOT / "research" / "feature_store" / "feature_store.db"
LABEL_STORE = ROOT / "research" / "label_store" / "label_store.db"
RAW = ROOT / "research" / "data" / "raw"

LINEAGE = "ivrv-vrp-substrate"
DECLARED_BY = "rohith"

#: Window deliberately ends the day before the sealed lockbox opens, so the
#: declaration never intersects it. The exclusion is declared anyway — RF2 is
#: about what the dataset *states*, not only about what it happens to touch.
#:
#: `window_start` is set by RF6, not by preference. DVOL's inception is
#: 2021-03-24 and the widest warm-up among the declared features is 30 periods
#: (`rv30`, `vrp_spread`), so the earliest admissible start is 2021-04-23. An
#: earlier start was declared first and **refused by RF6**; that refusal is
#: permanently recorded in the store as a Rejected specification, and is
#: deliberately not erased. 2021-05-01 is the first clean month boundary at or
#: after the RF6 floor.
WINDOW_START = "2021-05-01"
WINDOW_END = "2025-05-31"
AS_OF = "2026-08-08"

LOCKBOX = {"start": "2025-06-01", "end": "2026-05-31", "reason": "sealed lockbox (d5)"}

#: The H-IVRV substrate: DVOL and spot. Named by definition, resolved to the
#: real Active spec hash at run time — a hard-coded hash would silently bind to
#: a superseded specification.
FEATURE_NAMES = ("dvol_frac", "rv30", "vrp_spread")
LABEL_NAME = "spot_one_day_forward_return"
LABEL_HORIZON = 5

EVIDENCE_SOURCES = ("dvol", "spot")

_CERT_ORDER = {"RED": 1, "AMBER": 2, "GREEN": 3}
_CERT_ENUM = {"RED": Certification.RED, "AMBER": Certification.AMBER,
              "GREEN": Certification.GREEN}
_READY_ORDER = {"NOT_READY": 1, "PARTIAL": 2, "READY": 3}
_READY_ENUM = {"NOT_READY": Readiness.NOT_READY, "PARTIAL": Readiness.PARTIAL,
               "READY": Readiness.READY}


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%d")


def scan_corpus() -> dict:
    """Read every real partition manifest overlapping the declared window.

    The `_backup_*` directories are skipped: they are WBS 4.0's pre-conformance
    snapshots, not corpus members, and including them would understate the
    certification meet by re-counting superseded partitions.
    """
    agg: dict[str, dict] = collections.defaultdict(
        lambda: {"n": 0, "cert": collections.Counter(), "ready": collections.Counter(),
                 "min": None, "max": None, "hashes": [],
                 "full_min": None, "full_max": None}
    )
    for f in glob.glob(str(RAW / "*" / "**" / "_manifest.json"), recursive=True):
        rel = Path(f).relative_to(RAW)
        source = rel.parts[0]
        if source.startswith("_"):
            continue
        m = json.loads(Path(f).read_text())
        lo, hi = _iso(m["min_ts"]), _iso(m["max_ts"])
        a = agg[source]
        # Full coverage is tracked separately from the window-restricted
        # aggregate. RF6 asks whether warm-up data exists *before* the window,
        # so feeding it a window-clipped coverage map would make it refuse on
        # an artefact of the clipping rather than on the corpus.
        a["full_min"] = lo if a["full_min"] is None else min(a["full_min"], lo)
        a["full_max"] = hi if a["full_max"] is None else max(a["full_max"], hi)
        if hi < WINDOW_START or lo > WINDOW_END:
            continue
        a["n"] += 1
        adm = m.get("admissibility") or {}
        a["cert"][adm.get("certification", "ABSENT")] += 1
        a["ready"][adm.get("readiness", "ABSENT")] += 1
        a["min"] = lo if a["min"] is None else min(a["min"], lo)
        a["max"] = hi if a["max"] is None else max(a["max"], hi)
        a["hashes"].append((str(rel.parent), m["parquet_sha256"]))

    for source, a in agg.items():
        a["cert_meet"] = min(a["cert"], key=lambda k: _CERT_ORDER.get(k, 0))
        a["ready_meet"] = min(a["ready"], key=lambda k: _READY_ORDER.get(k, 0))
        # A content-addressed revision: any partition byte change moves it, so
        # the pin means something rather than labelling a moment.
        payload = json.dumps(sorted(a["hashes"]), separators=(",", ":")).encode()
        a["revision"] = "rev-" + hashlib.sha256(payload).hexdigest()[:16]
    return dict(agg)


def active_specs() -> tuple[dict, dict]:
    feats: dict[str, dict] = {}
    con = sqlite3.connect(str(FEATURE_STORE))
    con.row_factory = sqlite3.Row
    for r in con.execute(
        "SELECT spec_hash, definition_name, input_requirements_json, warmup_periods "
        "FROM feature_specifications WHERE phase = 'Active'"
    ):
        feats[r["definition_name"]] = dict(r)
    con.close()

    labs: dict[tuple[str, int], dict] = {}
    con = sqlite3.connect(str(LABEL_STORE))
    con.row_factory = sqlite3.Row
    for r in con.execute(
        "SELECT spec_hash, definition_name, horizon_days, maturity_rule "
        "FROM label_specifications WHERE phase = 'Active'"
    ):
        labs[(r["definition_name"], int(r["horizon_days"]))] = dict(r)
    con.close()
    return feats, labs


def _sources_of(feature_row: dict) -> set[str]:
    """Which corpus sources a feature actually reads, from its own spec."""
    reqs = json.loads(feature_row["input_requirements_json"] or "[]")
    out = set()
    for r in reqs:
        s = str(r.get("source", ""))
        for known in EVIDENCE_SOURCES + ("funding", "perp"):
            if s.startswith(known):
                out.add(known)
    return out


def build_declaration(corpus: dict, feats: dict, labs: dict) -> dict:
    missing = [n for n in FEATURE_NAMES if n not in feats]
    if missing:
        raise SystemExit(f"REFUSED: features not Active in the real store: {missing}")
    if (LABEL_NAME, LABEL_HORIZON) not in labs:
        raise SystemExit(
            f"REFUSED: label {LABEL_NAME}@{LABEL_HORIZON}d not Active in the real store"
        )
    for s in EVIDENCE_SOURCES:
        if s not in corpus:
            raise SystemExit(f"REFUSED: corpus source {s!r} has no partition in window")

    # The declaration may not claim a verdict the corpus does not support.
    required_verdict = min(
        (corpus[s]["cert_meet"] for s in EVIDENCE_SOURCES),
        key=lambda k: _CERT_ORDER.get(k, 0),
    )

    return {
        "intent": {
            "research_question":
                "Does the DVOL-implied minus realised volatility spread carry "
                "information about subsequent spot direction?",
            "declaring_actor": DECLARED_BY,
            "rationale":
                "H-IVRV (ivrv_vrp_spread) is an admitted feature candidate in the "
                "Evidence Library. This is the first governed substrate for it, "
                "and the first Research Dataset sealed on this platform.",
        },
        "temporal_scope": {
            "window_start": WINDOW_START,
            "window_end": WINDOW_END,
            "as_of": AS_OF,
            "timezone": "UTC",
            "calendar_convention": "exchange_daily_1200Z",
        },
        "conditioning": {
            "statement":
                "P(observed | daily corpus partition present and conformed at "
                "WBS 4.0 schema_version=3, normalization_contract_version=1). "
                "DVOL begins 2021-03-24; the window starts after that inception "
                "so no source is imputed.",
        },
        "evidence_references": {
            "sources": list(EVIDENCE_SOURCES),
            "partition_revision_sets": {
                s: corpus[s]["revision"] for s in EVIDENCE_SOURCES
            },
            "required_certification_verdict": required_verdict,
            "required_lane": "offline-corpus",
        },
        "feature_references": [feats[n]["spec_hash"] for n in FEATURE_NAMES],
        "label_references": [
            {
                "spec_hash": labs[(LABEL_NAME, LABEL_HORIZON)]["spec_hash"],
                "maturity_requirement": "matured",
            }
        ],
        "sealed_region_exclusions": [LOCKBOX],
        "admissibility_floor": "offline-corpus",
        "lane": "offline-corpus",
    }


def build_inputs(corpus: dict, feats: dict, labs: dict) -> list:
    """Honest per-input ceilings, derived from the real corpus, not asserted.

    A feature's ceiling cannot exceed the meet of the sources it reads —
    `rv30` computed on AMBER spot is not GREEN because its own specification
    is Active.
    """
    inputs = []

    for s in EVIDENCE_SOURCES:
        a = corpus[s]
        inputs.append(val.InputAdmissibility(
            kind="evidence",
            reference=s,
            ceiling=Admissibility(
                Provenance.OFFLINE_CORPUS,
                _CERT_ENUM[a["cert_meet"]],
                _READY_ENUM[a["ready_meet"]],
                Maturity.FULL,
                Integrity.CLEAN,
            ),
            certification_verdict=a["cert_meet"],
            lane="offline-corpus",
            window=(a["min"], a["max"]),
        ))

    for name in FEATURE_NAMES:
        row = feats[name]
        srcs = _sources_of(row) or set(EVIDENCE_SOURCES)
        known = [s for s in srcs if s in corpus]
        cert = min((corpus[s]["cert_meet"] for s in known),
                   key=lambda k: _CERT_ORDER.get(k, 0)) if known else "RED"
        ready = min((corpus[s]["ready_meet"] for s in known),
                    key=lambda k: _READY_ORDER.get(k, 0)) if known else "NOT_READY"
        inputs.append(val.InputAdmissibility(
            kind="feature",
            reference=row["spec_hash"],
            ceiling=Admissibility(
                Provenance.OFFLINE_CORPUS, _CERT_ENUM[cert], _READY_ENUM[ready],
                Maturity.FULL, Integrity.CLEAN,
            ),
            lane="offline-corpus",
            window=(WINDOW_START, WINDOW_END),
        ))

    lab = labs[(LABEL_NAME, LABEL_HORIZON)]
    spot = corpus["spot"]
    inputs.append(val.InputAdmissibility(
        kind="label",
        reference=lab["spec_hash"],
        ceiling=Admissibility(
            Provenance.OFFLINE_CORPUS,
            _CERT_ENUM[spot["cert_meet"]],
            _READY_ENUM[spot["ready_meet"]],
            Maturity.FULL,
            Integrity.CLEAN,
        ),
        lane="offline-corpus",
        window=(WINDOW_START, WINDOW_END),
    ))
    return inputs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true",
                    help="create the store and seal; without it, dry-run only")
    args = ap.parse_args(argv)

    print("=" * 74)
    print("Phase 4 operational closure — first governed Research Dataset (P4-1)")
    print("=" * 74)

    corpus = scan_corpus()
    feats, labs = active_specs()
    print("\n[1] Real corpus, restricted to the declared window")
    for s in EVIDENCE_SOURCES:
        a = corpus[s]
        print(f"    {s:7s} n={a['n']:3d}  {a['min']}..{a['max']}  "
              f"cert_meet={a['cert_meet']:5s} ready_meet={a['ready_meet']:9s} "
              f"{a['revision']}")

    declaration = build_declaration(corpus, feats, labs)
    inputs = build_inputs(corpus, feats, labs)
    expected = reg.declaration_hash(declaration)
    print(f"\n[2] Declaration built — {len(declaration['feature_references'])} features, "
          f"{len(declaration['label_references'])} label, "
          f"required_verdict={declaration['evidence_references']['required_certification_verdict']}")
    print(f"    deterministic spec_hash = {expected}")

    if not args.commit:
        print("\nDRY RUN — nothing written. Re-run with --commit.")
        return 0

    print(f"\n[3] Store: {STORE}")
    if not STORE.exists():
        STORE.parent.mkdir(parents=True, exist_ok=True)
        print("    creating (did not exist)")
    for mod in (reg, ci, sl, mat, hist):
        mod.init_db(STORE)
    print("    registry, citations, sealing, materialisation, history initialised")

    existing = reg.get_specification(STORE, expected)
    if existing is not None:
        print(f"\n    ALREADY PRESENT in phase {existing['phase']!r} — idempotent, "
              "no second seal, no second receipt.")
        return 0

    if LEDGER.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = LEDGER.with_name(f"ledger.pre-first-dataset-seal.{stamp}.db")
        shutil.copy2(LEDGER, backup)
        print(f"\n[4] Ledger backed up before the irreversible append:\n    {backup.name}")

    print("\n[5] Governed lifecycle")
    handle = res.declare(STORE, declaration, lineage=LINEAGE, declared_by=DECLARED_BY)
    spec_hash = handle.spec_hash
    assert spec_hash == expected, "declaration hash is not deterministic"
    print(f"    declare  -> Declared   {spec_hash[:16]}")

    resolver = res.StoreResolver(
        feature_store_db=FEATURE_STORE,
        label_store_db=LABEL_STORE,
        corpus_coverage_map={
            s: (corpus[s]["full_min"], corpus[s]["full_max"]) for s in corpus
        },
        revision_map={s: corpus[s]["revision"] for s in corpus},
    )
    rep = res.resolve(STORE, spec_hash, resolver)
    print(f"    resolve  -> Resolved   {len(rep.resolved)} references bound, "
          f"{len(rep.refusals)} refusals, pins={list(rep.pinned_revisions)}")

    vrep = val.validate(STORE, spec_hash, inputs)
    print(f"    validate -> Validated  meet={vrep.computed_meet}")
    print(f"                           capped={vrep.capped_admissibility}")

    srep = sl.seal_dataset(STORE, spec_hash, vrep, sealed_by=DECLARED_BY,
                           ledger_db=LEDGER, actor_type="human",
                           reason="First governed Research Dataset — Phase 4 P4-1.")
    print(f"    seal     -> Sealed     receipt={srep.seal_receipt_id}")

    print("\n[6] Post-seal verification")
    print(f"    phase             : {reg.get_specification(STORE, spec_hash)['phase']}")
    print(f"    citable           : {reg.is_citable(STORE, spec_hash)}")
    print(f"    citations recorded: {len(ci.cites(STORE, spec_hash))}")
    ok, errs = ci.verify_bidirectional_consistency(STORE)
    print(f"    citation graph    : {'consistent' if ok else errs}")
    print(f"    orphans           : {len(ci.find_orphans(STORE))}")
    print(f"    history           : {[t.to_phase for t in hist.phase_history(STORE, spec_hash)]}")
    hok, herrs = hist.history_completeness(STORE)
    print(f"    history complete  : {hok} {herrs or ''}")
    print(f"    ledger chain      : {rl.verify_chain(LEDGER)}")
    print(f"\n    SPEC HASH: {spec_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
