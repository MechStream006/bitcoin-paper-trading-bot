"""
Corpus conformance backfill by LOCAL RE-DERIVATION (Phase 4 roadmap
v1.0.2, WBS 4.0; `DS-DEC-002` ratified 2026-08-06; `G-CORPUS` opened
under conditions C1-C6).

WHAT THIS DOES:        gives each already-landed canonical partition the
                        three things `ADR-I012` needs and it currently
                        lacks -- a certification verdict (§12 Tier C), a
                        conditioning statement (§7.2), and a `_raw_lake`
                        revision reference (§14) -- by RE-READING the
                        partition that is already on disk.
WHAT IS NEVER TOUCHED: `data.parquet`. Not read-modify-written, not
                        re-encoded, not moved. Only its `_manifest.json`
                        sidecar is rewritten, and only to ADD fields.

NO NETWORK. NO RE-DOWNLOAD. `DS-DEC-002` rejected full re-download
because Binance Vision has silently restated data before (CLAUDE.md §11
records a ms->us precision change), and absorbing a restatement would
alter partition content and could change a published ledger number.
Re-derivation cannot do that: `land_raw()` takes a DataFrame and
`partition_ceiling()` computes from a gauntlet report over that same
data, so conformance is a pure function of bytes already on disk.

CONDITION C4 IS THE STRUCTURAL GUARANTEE. Every landed content hash must
equal the partition's pre-existing `parquet_sha256`. Verified achievable
before this script was written: `storage.lake.write_partition()` and
`storage.raw_lake.content_hash()` use the identical serialisation
(`pq.write_table`, `preserve_index=False`, zstd), differing only in file
vs buffer sink, and all 64 dvol partitions round-trip to an exact match.
A content change is therefore a FAILED PRECONDITION that aborts the
partition, not a regression detected afterwards.

WHAT THE RAW-LAKE REVISION ACTUALLY CONTAINS -- stated plainly rather
than implied. `land_raw()`'s docstring says it lands data "as-received"
(zone Z2, pre-normalisation). The original as-received bytes were NEVER
PERSISTED by any prior ingest: only the canonical Z3 partition was kept.
So the revision this backfill lands contains the CANONICAL content, not
the original as-received content. That is what condition C4 requires
(hash equality with the canonical `parquet_sha256`), and it is the only
honest option available -- but it means `raw_content_hash` on a
backfilled partition links to a re-derived canonical revision, not to a
recovered pre-normalisation original. A future partition ingested
through `dvol.py` proper will carry the stronger meaning. This
distinction is recorded here and in the milestone record; it is not
papered over.

NO VALUE IS INVENTED. Every field either comes from the partition's own
existing manifest (`ingest_ts`, `source`, `source_url`, `config_version`)
or from the owning ingest module's own module-level constants
(`CONDITIONING`, `NORMALIZATION_CONTRACT_VERSION`). In particular
`fetched_at` and `ingest_ts` are the TRUE HISTORICAL instants from the
original ingest -- never `datetime.now()` -- mirroring WBS 3.4's
identical discipline for the 148 H6 forward labels.

INVOCATION IS MODULE-FORM, AND THAT IS NOT OPTIONAL. Run from
`research/`:

    python -m ingest.backfill_conformance --dataset dvol                  # dry run
    python -m ingest.backfill_conformance --dataset dvol --commit          # writes
    python -m ingest.backfill_conformance --dataset dvol --verify-sample 5 # drift report

Running it as a path (`python ingest/backfill_conformance.py`) puts
`research/ingest/` at `sys.path[0]`, where `calendar.py` (the real WI-5
macro-event module) shadows the stdlib `calendar` that `pandas` imports
transitively via `_strptime` -- failing with the confusing
`AttributeError: module 'calendar' has no attribute 'day_abbr'`.

This is `INC-0004`'s mechanism reaching a second class of victim. That
incident fixed `feature_store/conditioning.py`, which inserted
`research/ingest/` explicitly; it did not address the fact that EVERY
module living inside `research/ingest/` is unrunnable as a path-form
script for the same reason. Verified this session: the pre-existing
`ingest/dvol.py` fails identically, so this is a repository defect this
milestone documents rather than introduces, and `-m` is the established
working form for both.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
import sys
from datetime import datetime, timezone

import pandas as pd

_THIS_DIR = pathlib.Path(__file__).resolve().parent
_RESEARCH_DIR = _THIS_DIR.parent
if str(_RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_DIR))

from quality.admissibility import partition_ceiling      # noqa: E402
from quality.gauntlet import run_gauntlet                # noqa: E402
from storage.lake import partition_path                  # noqa: E402
from storage.manifest import write_manifest              # noqa: E402
from storage.raw_lake import content_hash, land_raw      # noqa: E402

DEFAULT_BASE_DIR = _RESEARCH_DIR / "data" / "raw"


def _dvol_source() -> dict:
    """dvol's own constants, read from the module that owns them rather
    than restated here -- so a change there cannot silently diverge."""
    from ingest import dvol  # noqa: PLC0415
    return {
        "dataset": dvol.DATASET,
        "symbol": dvol.SYMBOL_LAKE,
        "ts_column": "open_time",
        "conditioning": dvol.CONDITIONING,
        "normalization_contract_version": dvol.NORMALIZATION_CONTRACT_VERSION,
    }


# Only sources whose conformance has actually been exercised are registered.
# D0a is the dvol pilot; funding/perp/spot are added at D0b, additively.
# Mirrors standing.py's own "only populate what's real" precedent (WBS 3.5):
# an unregistered dataset refuses cleanly rather than being guessed at.
_SOURCES = {
    "dvol": _dvol_source,
}


class ConformanceError(RuntimeError):
    """Raised when a precondition fails. Never downgraded to a warning."""


def source_spec(dataset: str) -> dict:
    if dataset not in _SOURCES:
        raise ConformanceError(
            f"no conformance source registered for dataset {dataset!r}; "
            f"registered: {sorted(_SOURCES)}. Sprint D0a covers dvol only "
            f"(Phase 4 roadmap v1.0.2 §5); D0b adds the rest."
        )
    return _SOURCES[dataset]()


def _sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def list_partitions(base_dir: pathlib.Path, dataset: str, symbol: str) -> list[dict]:
    """Every partition on disk with both a parquet and a manifest."""
    root = pathlib.Path(base_dir) / dataset / symbol
    out = []
    for man_path in sorted(root.glob("year=*/month=*/_manifest.json")):
        pq_path = man_path.parent / "data.parquet"
        if not pq_path.exists():
            continue
        man = json.loads(man_path.read_text(encoding="utf-8"))
        out.append({
            "year": man["year"], "month": man["month"],
            "dir": man_path.parent, "parquet": pq_path, "manifest": man,
        })
    return sorted(out, key=lambda p: (p["year"], p["month"]))


def is_conformed(manifest: dict) -> bool:
    """A partition is conformed when it carries all three ADR-I012 needs."""
    return (
        manifest.get("admissibility") is not None
        and manifest.get("conditioning") is not None
        and manifest.get("raw_content_hash") is not None
    )


def plan(base_dir: pathlib.Path, dataset: str, symbol: str) -> list[dict]:
    """Partitions still needing conformance. Pure: reads only."""
    return [p for p in list_partitions(base_dir, dataset, symbol) if not is_conformed(p["manifest"])]


def conform_partition(part: dict, base_dir: pathlib.Path, spec: dict) -> dict:
    """
    Re-derive one partition's conformance fields.

    Enforces condition C4 BEFORE landing anything: if the re-read
    DataFrame does not hash to the partition's recorded
    `parquet_sha256`, the partition is refused rather than landed. That
    inverts the usual order -- a content change becomes a failed
    precondition, not a regression found later.
    """
    man = part["manifest"]
    df = pd.read_parquet(part["parquet"])

    derived = content_hash(df)
    recorded = man["parquet_sha256"]
    if derived != recorded:
        raise ConformanceError(
            f"C4 VIOLATION for {spec['dataset']}/{spec['symbol']} "
            f"{man['year']}-{man['month']:02d}: re-derived content hash "
            f"{derived[:16]}... != recorded parquet_sha256 {recorded[:16]}.... "
            f"Refusing to land: the partition's content is not what its "
            f"manifest says it is."
        )

    # fetched_at is the ORIGINAL ingest instant, never now() -- WBS 3.4 discipline.
    raw_receipt = land_raw(
        df, base_dir,
        dataset=spec["dataset"], symbol=spec["symbol"],
        year=man["year"], month=man["month"],
        source=man["source"], source_url=man.get("source_url"),
        conditioning=spec["conditioning"],
        fetched_at=man["ingest_ts"],
    )

    report = run_gauntlet(
        df, dataset=spec["dataset"], symbol=spec["symbol"],
        year=man["year"], month=man["month"], ts_column=spec["ts_column"],
    )
    ceiling = partition_ceiling(report)

    write_manifest(
        part["parquet"],
        dataset=spec["dataset"], symbol=spec["symbol"],
        year=man["year"], month=man["month"],
        source=man["source"], source_url=man.get("source_url"),
        rows=man["rows"], min_ts=man["min_ts"], max_ts=man["max_ts"],
        ts_column=man["ts_column"],
        ingest_ts=man["ingest_ts"],            # preserved, not restamped
        config_version=man["config_version"],  # preserved
        normalization_contract_version=spec["normalization_contract_version"],
        conditioning=spec["conditioning"],
        raw_content_hash=raw_receipt["hash"],
        admissibility={
            "certification": ceiling.certification.name,
            "readiness": ceiling.readiness.name,
            "lattice_version": ceiling.lattice_version,
        },
    )
    return {
        "year": man["year"], "month": man["month"], "rows": man["rows"],
        "content_hash": derived,
        "revision_ordinal": raw_receipt["revision_ordinal"],
        "is_new_revision": raw_receipt["is_new_revision"],
        "certification": ceiling.certification.name,
        "readiness": ceiling.readiness.name,
    }


def verify_sample(base_dir: pathlib.Path, dataset: str, symbol: str, n: int) -> list[dict]:
    """
    `DS-DEC-002`'s upstream-drift check. Re-fetches `n` partitions and
    COMPARES against what is on disk. Reports divergence; never lands it,
    never mutates anything -- the same "flag, never silently reconcile"
    discipline `ADR-I012` §8.2 requires of the materialiser.
    """
    if dataset != "dvol":
        raise ConformanceError(f"no sampled re-fetch is wired for dataset {dataset!r}")
    from ingest import dvol  # noqa: PLC0415

    parts = list_partitions(base_dir, dataset, symbol)
    if not parts:
        return []
    step = max(1, len(parts) // max(1, n))
    sampled = parts[::step][:n]

    findings = []
    for part in sampled:
        man = part["manifest"]
        lo, hi = dvol._month_bounds_ms(man["year"], man["month"])
        try:
            fetched = dvol._fetch_range(lo, hi)
            fresh = dvol._normalize(fetched)
            fresh = fresh[fresh["open_time"].between(lo, hi)].copy().reset_index(drop=True)
            upstream = content_hash(fresh)
            findings.append({
                "year": man["year"], "month": man["month"],
                "on_disk": man["parquet_sha256"], "upstream": upstream,
                "agrees": upstream == man["parquet_sha256"],
                "error": None,
            })
        except Exception as exc:  # noqa: BLE001 -- a fetch failure is a finding, not a crash
            findings.append({
                "year": man["year"], "month": man["month"],
                "on_disk": man["parquet_sha256"], "upstream": None,
                "agrees": None, "error": f"{type(exc).__name__}: {exc}",
            })
    return findings


def backup(base_dir: pathlib.Path, dataset: str, symbol: str) -> pathlib.Path:
    """Condition C1. Copies the dataset subtree before any write."""
    src = pathlib.Path(base_dir) / dataset / symbol
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dst = pathlib.Path(base_dir) / f"_backup_{dataset}_{symbol}_{ts}"
    shutil.copytree(src, dst)
    return dst


def parquet_fingerprint(base_dir: pathlib.Path, dataset: str, symbol: str) -> dict[str, str]:
    """Condition C2 evidence: SHA-256 of every data.parquet, for before/after."""
    return {
        str(p["parquet"].relative_to(base_dir)): _sha256_file(p["parquet"])
        for p in list_partitions(base_dir, dataset, symbol)
    }


def print_plan(parts: list[dict], total: int) -> None:
    if not parts:
        print(f"0 of {total} partitions need conformance (already conformed, or none exist).")
        return
    print(f"{'partition':<20} {'rows':>8}  {'conditioning':<12} {'admissibility':<13} raw_ref")
    for p in parts:
        m = p["manifest"]
        print(
            f"{m['year']}-{m['month']:02d}{'':<13} {m['rows']:>8}  "
            f"{'MISSING':<12} {'MISSING':<13} MISSING"
        )
    print(f"\n{len(parts)} of {total} partition(s) would be conformed.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dataset", required=True, help="Dataset to conform (D0a: dvol only)")
    parser.add_argument("--symbol", default=None, help="Override the source's default symbol")
    parser.add_argument("--base-dir", default=str(DEFAULT_BASE_DIR))
    parser.add_argument("--commit", action="store_true", help="Write (default: dry run)")
    parser.add_argument("--verify-sample", type=int, default=0, metavar="N",
                        help="Re-fetch N partitions and report upstream drift; lands nothing")
    args = parser.parse_args(argv)

    base_dir = pathlib.Path(args.base_dir)
    try:
        spec = source_spec(args.dataset)
    except ConformanceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    symbol = args.symbol or spec["symbol"]

    if not (base_dir / spec["dataset"] / symbol).exists():
        print(f"ERROR: {base_dir / spec['dataset'] / symbol} not found.", file=sys.stderr)
        return 1

    if args.verify_sample:
        findings = verify_sample(base_dir, spec["dataset"], symbol, args.verify_sample)
        print(f"=== upstream drift check ({len(findings)} sampled) ===")
        for f in findings:
            if f["error"]:
                print(f"  {f['year']}-{f['month']:02d}  FETCH FAILED  {f['error'][:70]}")
            else:
                print(f"  {f['year']}-{f['month']:02d}  "
                      f"{'AGREES' if f['agrees'] else 'DRIFT'}  upstream={f['upstream'][:16]}...")
        drift = [f for f in findings if f["agrees"] is False]
        print(f"\n{len(drift)} partition(s) diverge from upstream. Reported only; nothing landed.")
        return 0

    all_parts = list_partitions(base_dir, spec["dataset"], symbol)
    pending = [p for p in all_parts if not is_conformed(p["manifest"])]
    print_plan(pending, len(all_parts))

    if not args.commit:
        print("\nDry run only. Re-run with --commit to write.")
        return 0
    if not pending:
        return 0

    before = parquet_fingerprint(base_dir, spec["dataset"], symbol)   # C2
    backup_path = backup(base_dir, spec["dataset"], symbol)           # C1
    print(f"\nBacked up -> {backup_path}")

    results = []
    for part in pending:
        results.append(conform_partition(part, base_dir, spec))

    after = parquet_fingerprint(base_dir, spec["dataset"], symbol)    # C2
    changed = [k for k in before if before[k] != after.get(k)]
    if changed:
        raise ConformanceError(
            f"C2 VIOLATION: {len(changed)} data.parquet file(s) changed during conformance: "
            f"{changed[:3]}. Restore from {backup_path}."
        )

    certs = {}
    for r in results:
        certs[r["certification"]] = certs.get(r["certification"], 0) + 1
    print(f"Conformed {len(results)} partition(s). Certification: {certs}")
    print(f"C2 verified: all {len(before)} data.parquet files byte-identical.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
