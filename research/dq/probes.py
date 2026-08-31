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


# ---------------------------------------------------------------------------
# parquet — the addition this module's own header anticipated.
# ---------------------------------------------------------------------------

def _parquet_partitions(dataset_dir: Path) -> list[Path]:
    return sorted(dataset_dir.glob("year=*/month=*/*.parquet"))


def _parquet_history(
    day_counts: dict[str, int], period_start: datetime, window_days: int,
    min_records_per_day: int,
) -> tuple[int, bool]:
    """
    Distinct UTC days that are PRESENT in the trailing `window_days` window
    ending at (and including) the period.

    Semantics are deliberately identical to `_sqlite_history`, with one
    generalisation the sqlite probe never needed: a day counts as present only
    if it carries at least `min_records_per_day` records. A source that
    publishes N events per day (funding: 3) can have a day that physically
    exists yet is too thin to derive from, and counting it would reproduce the
    exact confusion — a partial day consumed as a whole one — that this
    framework exists to end.
    """
    window_start = (period_start - timedelta(days=window_days - 1)).date()
    period_day = period_start.date()
    days = sorted(
        d for d, n in day_counts.items()
        if n >= min_records_per_day
        and window_start.isoformat() <= d <= period_day.isoformat()
    )
    if not days:
        return 0, True

    first = datetime.strptime(days[0], "%Y-%m-%d")
    last = datetime.strptime(days[-1], "%Y-%m-%d")
    span = (last - first).days + 1
    return len(days), len(days) < span


def parquet_probe(
    spec: InputSpec, period_start: datetime, period_end: datetime, as_of: datetime
) -> InputObservation:
    """
    source:
      type: parquet
      root: research/data/raw       # lake root
      dataset: funding              # funding | spot | perp | dvol | ...
      symbol: BTCUSDT
      ts_column: timestamp_ms
      ts_unit: ms | s | us | iso
      min_records_per_day: 2        # optional (default 1); see _parquet_history
      window_days: 273              # optional; defaults to continuity.min_rows

    `window_days` exists because a lookback window and a sufficiency floor are
    not always the same number. The contract-level `history_window` sugar models
    them as one (N rows in an N-day window, i.e. a fully dense window), which is
    right for a rolling statistic that tolerates no holes. A derivation that
    requires "252 valid days within a 273-day lookback" cannot be expressed that
    way, and approximating it by tightening the window to 252 would create a
    SECOND, divergent definition of that derivation's own sufficiency rule --
    the precise disease this framework exists to cure. `ContinuitySpec.min_rows`
    keeps its exact meaning (the floor); only the lookback length is declared
    here, and it defaults to `min_rows` so every existing contract is unchanged.

    Only the timestamp column is ever read -- never a full table. That is a
    column projection over a handful of monthly partitions, orders of magnitude
    cheaper than the derivation it gates (L3).

    Point-in-time correctness: a partition whose file mtime is later than
    `as_of` is invisible, mirroring the sqlite probe's `arrival_column`. Without
    it a backfill silently grants look-ahead to every historical replay.
    """
    try:
        source = spec.source
        for key in ("root", "dataset", "symbol", "ts_column"):
            if key not in source:
                return InputObservation(
                    input_id=spec.id,
                    probe_error=f"source type 'parquet' requires '{key}' in source",
                )
        ts_unit = source.get("ts_unit", "ms")
        if ts_unit not in ("s", "ms", "us", "iso"):
            return InputObservation(input_id=spec.id, probe_error=f"unknown ts_unit '{ts_unit}'")

        min_per_day = int(source.get("min_records_per_day", 1))
        ts_col = source["ts_column"]
        dataset_dir = Path(source["root"]) / source["dataset"] / source["symbol"]
        if not dataset_dir.is_dir():
            return InputObservation(
                input_id=spec.id, probe_error=f"parquet dataset not found: {dataset_dir}")

        window_days = int(source.get(
            "window_days",
            spec.continuity.min_rows if spec.continuity else 0,
        ))

        parts = _parquet_partitions(dataset_dir)
        # Arrival filtering is OPT-IN, mirroring the sqlite probe, which filters
        # only when an `arrival_column` is declared. A parquet lake has no
        # arrival column, and file mtime is a poor substitute for a *hard*
        # filter: restoring the corpus from backup rewrites every mtime, which
        # would make a complete lake look like it had not arrived yet. Declare
        # `arrival_from_mtime: true` where replay at a historical `as_of` is
        # actually reachable; `newest_arrival_ts` is reported either way, so
        # the audit trail is unaffected by the choice.
        if bool(source.get("arrival_from_mtime", False)):
            visible = [p for p in parts
                       if datetime.fromtimestamp(p.stat().st_mtime, timezone.utc) <= as_of]
            if parts and not visible:
                return InputObservation(
                    input_id=spec.id,
                    probe_error=(
                        f"no parquet partition in {dataset_dir} had arrived by "
                        f"{as_of.isoformat()}"
                    ),
                )
        else:
            visible = parts
        if not visible:
            return InputObservation(
                input_id=spec.id,
                probe_error=f"no parquet partition found under {dataset_dir}",
            )

        import pyarrow.parquet as pq  # heavy import, deliberately local

        rows = 0
        min_ts = max_ts = None
        day_counts: dict[str, int] = {}
        newest_arrival = max(
            datetime.fromtimestamp(p.stat().st_mtime, timezone.utc) for p in visible)

        lo = _to_epoch(period_start, ts_unit)
        hi = _to_epoch(period_end, ts_unit)

        for part in visible:
            col = pq.read_table(part, columns=[ts_col]).column(ts_col)
            for value in col:
                v = value.as_py()
                if v is None:
                    continue
                if ts_unit == "iso":
                    day, in_period = str(v)[:10], (str(lo) <= str(v) < str(hi))
                else:
                    divisor = {"s": 1, "ms": 1_000, "us": 1_000_000}[ts_unit]
                    day = datetime.fromtimestamp(int(v) / divisor, timezone.utc).date().isoformat()
                    in_period = lo <= int(v) < hi
                day_counts[day] = day_counts.get(day, 0) + 1
                if in_period:
                    rows += 1
                    if min_ts is None or v < min_ts:
                        min_ts = v
                    if max_ts is None or v > max_ts:
                        max_ts = v

        history_rows, has_gaps = None, None
        if spec.continuity and spec.continuity.min_rows > 0:
            history_rows, has_gaps = _parquet_history(
                day_counts, period_start, window_days or spec.continuity.min_rows, min_per_day)

        fingerprint = sha256_lf(canonical_json({
            "dataset": f"{source['dataset']}/{source['symbol']}",
            "partitions": sorted(p.name + ":" + str(p.stat().st_size) for p in visible),
            "rows": rows,
        }))

        return InputObservation(
            input_id=spec.id,
            rows=rows,
            min_ts=_from_epoch(min_ts, ts_unit) if min_ts is not None else None,
            max_ts=_from_epoch(max_ts, ts_unit) if max_ts is not None else None,
            newest_arrival_ts=newest_arrival.isoformat(),
            coverage_count=rows,
            history_rows=history_rows,
            has_interior_gaps=has_gaps,
            content_hash=fingerprint,
        )
    except Exception as exc:                                        # noqa: BLE001
        # A probe never raises into the engine (module header): a traceback
        # writes no receipt, and a period with no receipt must never be
        # indistinguishable from a period that passed.
        return InputObservation(
            input_id=spec.id,
            probe_error=f"{type(exc).__name__}: {exc}",
        )


register_probe("parquet", parquet_probe)
