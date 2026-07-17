"""
Probes — docs/governance/data_completeness_gate.md §5.2, L3.

A probe answers "what does the source look like?" with METADATA ONLY: counts,
bounds, arrival times, a schema, a fingerprint. **A probe that loads a full
dataframe is a failed review.** Gating must be orders of magnitude cheaper than
deriving, or it will be skipped under load, and a gate that gets skipped is not
a gate.

Probes are registered **by source TYPE, not by feature** — one `sqlite` probe
serves every sqlite-backed contract that will ever exist. Adding a source type
is the only reason to write a new probe, and it is an additive registration,
never an engine change (L1). That is what makes the "one contract file, zero
framework changes" claim true rather than aspirational.

**A probe never raises into the engine.** Any failure is captured as
`InputObservation.probe_error`, which the probe check turns into UNKNOWN, which
BLOCKS (L2). A gate that dies with a traceback writes no receipt, and a period
with no receipt is a period nobody looked at — which must never be
indistinguishable from a period that passed.

No pandas, no pyarrow, no third-party dependency in this module: the core must
import cleanly in both the venv bot and the conda research env. A `parquet`
probe is a natural addition and would live here as one more `@register_probe`,
with its heavy import local to the function.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

_EVIDENCE_LIBRARY = Path(__file__).resolve().parents[1] / "evidence_library"
if str(_EVIDENCE_LIBRARY) not in sys.path:
    sys.path.insert(0, str(_EVIDENCE_LIBRARY))

from receipts import canonical_json, sha256_lf  # noqa: E402

from contract import Contract, InputSpec, period_bounds  # noqa: E402
from observation import InputObservation  # noqa: E402

# (input_spec, period_start, period_end, as_of) -> InputObservation
Probe = Callable[[InputSpec, datetime, datetime, datetime], InputObservation]

PROBE_REGISTRY: dict[str, Probe] = {}


def register_probe(source_type: str, probe: Probe) -> None:
    PROBE_REGISTRY[source_type] = probe


def get_probe(source_type: str) -> Probe | None:
    return PROBE_REGISTRY.get(source_type)


def probe_input(
    spec: InputSpec,
    period_start: datetime,
    period_end: datetime,
    as_of: datetime,
) -> InputObservation:
    """
    Dispatch to the probe registered for this input's source type. An unknown
    source type is an OBSERVATION with a probe_error (-> UNKNOWN -> BLOCK), not
    an exception: fail closed, and leave a receipt saying why.
    """
    source_type = spec.source.get("type")
    probe = get_probe(source_type)
    if probe is None:
        return InputObservation(
            input_id=spec.id,
            probe_error=(
                f"no probe registered for source type '{source_type}'; "
                f"registered types: {sorted(PROBE_REGISTRY)}"
            ),
        )
    try:
        return probe(spec, period_start, period_end, as_of)
    except Exception as exc:  # noqa: BLE001 — deliberate: a probe never raises into the engine
        return InputObservation(
            input_id=spec.id, probe_error=f"{type(exc).__name__}: {exc}"
        )


def probe_all(
    contract: Contract, period: str, as_of: datetime
) -> dict[str, InputObservation]:
    start, end = period_bounds(contract.grain, period)
    return {
        spec.id: probe_input(spec, start, end, as_of) for spec in contract.inputs
    }


# ---------------------------------------------------------------------------
# static — observations supplied by the caller.
#
# The pipeline already computed the metadata (e.g. SnapshotWriter's
# collection_metrics already records coverage_pct and stale_count per cycle,
# with semantics precisely defined in CLAUDE.md §11). The gate should CONSUME
# those numbers, not recompute them — recomputing would create a second,
# divergent definition of coverage, which is the exact failure mode this
# framework exists to end.
# ---------------------------------------------------------------------------

def static_probe(
    spec: InputSpec, period_start: datetime, period_end: datetime, as_of: datetime
) -> InputObservation:
    payload = spec.source.get("observation")
    if not isinstance(payload, dict):
        return InputObservation(
            input_id=spec.id,
            probe_error="source type 'static' requires an 'observation' mapping in source",
        )
    return InputObservation.from_dict({**payload, "input_id": spec.id})


register_probe("static", static_probe)


# ---------------------------------------------------------------------------
# sqlite — stdlib only, metadata-only queries.
# ---------------------------------------------------------------------------

def _to_epoch(dt: datetime, unit: str) -> int | str:
    if unit == "iso":
        return dt.isoformat()
    seconds = dt.timestamp()
    return int(seconds * {"s": 1, "ms": 1_000, "us": 1_000_000}[unit])


def _from_epoch(value, unit: str) -> str | None:
    if value is None:
        return None
    if unit == "iso":
        return str(value)
    divisor = {"s": 1, "ms": 1_000, "us": 1_000_000}[unit]
    return datetime.fromtimestamp(float(value) / divisor, tz=timezone.utc).isoformat()


def sqlite_probe(
    spec: InputSpec, period_start: datetime, period_end: datetime, as_of: datetime
) -> InputObservation:
    """
    source:
      type: sqlite
      path: research/data/x.db
      table: funding
      ts_column: ts             # period membership
      ts_unit: ms | s | us | iso
      arrival_column: ingested_at   # optional; ISO-8601. Enables point-in-time
                                    # correctness: rows that had not ARRIVED by
                                    # as_of are invisible to the probe, so a
                                    # replay cannot acquire look-ahead from
                                    # data that arrived later.

    Every query here is an aggregate (COUNT / MIN / MAX) or a PRAGMA. No row is
    ever materialised.
    """
    source = spec.source
    path = Path(source["path"])
    table = source["table"]
    ts_col = source["ts_column"]
    ts_unit = source.get("ts_unit", "ms")
    arrival_col = source.get("arrival_column")

    if not path.exists():
        return InputObservation(input_id=spec.id, probe_error=f"source database not found: {path}")

    if ts_unit not in ("s", "ms", "us", "iso"):
        return InputObservation(input_id=spec.id, probe_error=f"unknown ts_unit '{ts_unit}'")

    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        cols = {r["name"]: r["type"] or "" for r in con.execute(f'PRAGMA table_info("{table}")')}
        if not cols:
            return InputObservation(input_id=spec.id, probe_error=f"table '{table}' does not exist")

        lo = _to_epoch(period_start, ts_unit)
        hi = _to_epoch(period_end, ts_unit)

        # Point-in-time correctness (L6 / §6): only rows that had ARRIVED by
        # as_of are visible. Without this, a backfill silently acquires
        # look-ahead and every historical replay quietly benefits from data
        # that did not exist when the verdict was rendered.
        arrival_clause, arrival_params = "", []
        if arrival_col and arrival_col in cols:
            arrival_clause = f' AND "{arrival_col}" <= ?'
            arrival_params = [as_of.isoformat()]

        where = f'"{ts_col}" >= ? AND "{ts_col}" < ?{arrival_clause}'
        params = [lo, hi, *arrival_params]

        row = con.execute(
            f'SELECT COUNT(*) AS n, MIN("{ts_col}") AS lo, MAX("{ts_col}") AS hi '
            f'FROM "{table}" WHERE {where}',
            params,
        ).fetchone()
        rows, min_ts, max_ts = row["n"], row["lo"], row["hi"]

        newest_arrival = None
        if arrival_col and arrival_col in cols:
            r = con.execute(
                f'SELECT MAX("{arrival_col}") AS a FROM "{table}" WHERE "{arrival_col}" <= ?',
                [as_of.isoformat()],
            ).fetchone()
            newest_arrival = r["a"]
        elif max_ts is not None:
            newest_arrival = _from_epoch(max_ts, ts_unit)

        # Value bounds — only for the columns the contract actually BANDS.
        # Probing bounds for every column would be work nobody asked for.
        value_bounds: dict[str, list[str]] = {}
        if spec.schema:
            for col in spec.schema.bands:
                if col not in cols:
                    continue
                b = con.execute(
                    f'SELECT MIN("{col}") AS lo, MAX("{col}") AS hi FROM "{table}" WHERE {where}',
                    params,
                ).fetchone()
                if b["lo"] is not None:
                    value_bounds[col] = [str(b["lo"]), str(b["hi"])]

        history_rows, has_gaps = None, None
        if spec.continuity and spec.continuity.min_rows > 0:
            history_rows, has_gaps = _sqlite_history(
                con, table, ts_col, ts_unit, where, arrival_params,
                period_start, spec.continuity.min_rows,
            )

        fingerprint = sha256_lf(canonical_json({
            "table": table, "rows": rows,
            "min_ts": str(min_ts), "max_ts": str(max_ts),
        }))

        return InputObservation(
            input_id=spec.id,
            rows=rows,
            min_ts=_from_epoch(min_ts, ts_unit),
            max_ts=_from_epoch(max_ts, ts_unit),
            newest_arrival_ts=newest_arrival,
            schema={k: v for k, v in sorted(cols.items())},
            value_bounds=value_bounds or None,
            coverage_count=rows,
            coverage_denominator=None,
            history_rows=history_rows,
            has_interior_gaps=has_gaps,
            content_hash=fingerprint,
        )
    finally:
        con.close()


def _sqlite_history(
    con, table, ts_col, ts_unit, _where, arrival_params, period_start, min_rows
) -> tuple[int, bool]:
    """
    Distinct UTC days present in the trailing `min_rows`-day window ending at
    (and including) the period. `has_interior_gaps` is True when a day BETWEEN
    the first and last present day is absent — a hole in the middle of a
    rolling window, which silently corrupts every statistic computed across it
    (rv_21d over a window with a hole is not rv_21d).
    """
    window_start = period_start - timedelta(days=min_rows - 1)
    lo = _to_epoch(window_start, ts_unit)
    hi = _to_epoch(period_start + timedelta(days=1), ts_unit)

    if ts_unit == "iso":
        day_expr = f'substr("{ts_col}", 1, 10)'
    else:
        divisor = {"s": 1, "ms": 1_000, "us": 1_000_000}[ts_unit]
        day_expr = f'date("{ts_col}" / {divisor}, \'unixepoch\')'

    rows = con.execute(
        f'SELECT DISTINCT {day_expr} AS d FROM "{table}" '
        f'WHERE "{ts_col}" >= ? AND "{ts_col}" < ? ORDER BY d',
        [lo, hi],
    ).fetchall()
    days = [r["d"] for r in rows if r["d"] is not None]
    if not days:
        return 0, True

    first = datetime.strptime(days[0], "%Y-%m-%d")
    last = datetime.strptime(days[-1], "%Y-%m-%d")
    span = (last - first).days + 1
    return len(days), len(days) < span


register_probe("sqlite", sqlite_probe)
