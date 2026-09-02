"""
align.py
========
As-of alignment across sources, on availability time, with an explicit
look-ahead check.

Read-only. Writes nothing.

THE ONE RULE
------------
A decision made at instant T may use an observation only if that observation's
**availability time** is <= T. Not its event time -- `timebase` explains why the
two differ and by how much. Every join in this module is backward-only, and
`check_no_lookahead` re-verifies the result independently of the join that
produced it, so a wrong `direction=` argument cannot pass silently.

WHY NOT A RESAMPLED GRID
------------------------
The obvious way to align these sources is to snap everything to a regular grid
and join on it. That is wrong here twice over. The quote/OI cycle is nominally
60s but only 1 of 299 consecutive production gaps was exactly 60.000s, with a
160.6s maximum -- so a synthetic grid invents observations at instants nothing
was observed. And forward-filling onto a grid silently converts "no data yet"
into "the last value still holds", which is the look-ahead this layer exists to
prevent. Alignment therefore happens on the *observed* instants, and any
regularisation is the caller's explicit choice.

STALENESS IS RETURNED, NOT HIDDEN
---------------------------------
An as-of join always finds something if any earlier row exists, however old.
`as_of_join` returns `age_s` on every matched row and accepts a `tolerance`
beyond which the match is dropped rather than silently accepted. A feature built
on a four-hour-old quote is not the same feature as one built on a fresh quote,
and the caller has to be able to tell.
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional, Sequence

_HERE = pathlib.Path(__file__).resolve().parent
_RESEARCH_ROOT = _HERE.parent
if str(_RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_ROOT))

from alignment.timebase import (  # noqa: E402
    SourceSpec, availability_time, cadence_profile, event_time, get_spec,
    oi_source, to_utc,
)

_DEFAULT_TRADING_DB = _RESEARCH_ROOT.parent / "data" / "btc_trading.db"
_DEFAULT_FORWARD_DB = _RESEARCH_ROOT / "data" / "evidence_forward.db"


class LookAheadError(AssertionError):
    """An aligned row carried information that post-dated its decision instant."""


@dataclass(frozen=True)
class Observation:
    """One row, reduced to the two times that matter plus its payload."""

    source: str
    event_ts: Optional[datetime]
    available_ts: datetime
    payload: dict[str, Any]
    provenance: dict[str, Any]

    @property
    def arrival_delay_s(self) -> Optional[float]:
        if self.event_ts is None:
            return None
        return (self.available_ts - self.event_ts).total_seconds()


def to_observations(rows: Iterable[Any], spec: SourceSpec,
                    payload_fields: Sequence[str] = ()) -> list[Observation]:
    """Normalise raw rows into Observations, dropping only what has no clock.

    A row whose availability time cannot be parsed is skipped -- it cannot be
    placed in time, so it cannot be aligned. A row whose *event* time is missing
    is kept: `option_quotes` has 0.234% NULL `exch_ts`, and those rows still
    arrived at a known instant and are still usable for decisions.
    """
    out: list[Observation] = []
    for row in rows:
        avail = availability_time(row, spec)
        if avail is None:
            continue
        out.append(Observation(
            source=spec.name,
            event_ts=event_time(row, spec),
            available_ts=avail,
            payload={f: _get(row, f) for f in payload_fields},
            provenance={f: _get(row, f) for f in spec.provenance_fields},
        ))
    out.sort(key=lambda o: (o.available_ts, str(o.provenance)))
    return out


@dataclass(frozen=True)
class SourceLoad:
    """Observations plus what was discarded getting to them.

    The counts matter downstream: "no rows at all" and "rows existed but none
    could be placed in time" are different failures. The first is incomplete
    data; the second means the store cannot say when anything arrived, which
    must fail closed rather than be dated from the event clock.
    """

    source: str
    observations: tuple[Observation, ...]
    rows_seen: int
    rows_without_availability: int

    @property
    def unusable_clock(self) -> bool:
        return self.rows_seen > 0 and not self.observations             and self.rows_without_availability > 0


def load_observations(rows: Iterable[Any], spec: SourceSpec,
                      payload_fields: Sequence[str] = ()) -> SourceLoad:
    """to_observations, but keeping the discard counts."""
    materialised = list(rows)
    obs = to_observations(materialised, spec, payload_fields)
    return SourceLoad(
        source=spec.name,
        observations=tuple(obs),
        rows_seen=len(materialised),
        rows_without_availability=len(materialised) - len(obs),
    )


def as_of_join(decision_times: Sequence[datetime],
               observations: Sequence[Observation],
               tolerance_s: float | None = None,
               allow_exact: bool = True) -> list[dict[str, Any]]:
    """For each decision instant, the newest observation available by then.

    Backward only. `allow_exact` admits an observation whose availability equals
    the decision instant -- it was available, if only just. `tolerance_s` drops
    matches older than that many seconds instead of returning a stale one.

    Returns one row per decision, with `matched=False` where nothing qualified.
    """
    obs = sorted(observations, key=lambda o: o.available_ts)
    times = [o.available_ts for o in obs]
    results: list[dict[str, Any]] = []
    for t in decision_times:
        idx = _rightmost_le(times, t, allow_exact)
        if idx is None:
            results.append({"decision_ts": t, "matched": False, "age_s": None,
                            "source": None, "event_ts": None, "available_ts": None,
                            "payload": {}, "provenance": {}})
            continue
        o = obs[idx]
        age = (t - o.available_ts).total_seconds()
        if tolerance_s is not None and age > tolerance_s:
            results.append({"decision_ts": t, "matched": False, "age_s": age,
                            "source": o.source, "event_ts": None,
                            "available_ts": None, "payload": {}, "provenance": {},
                            "dropped_stale": True})
            continue
        results.append({
            "decision_ts": t, "matched": True, "age_s": age, "source": o.source,
            "event_ts": o.event_ts, "available_ts": o.available_ts,
            "payload": dict(o.payload), "provenance": dict(o.provenance),
        })
    return results


def _rightmost_le(times: Sequence[datetime], t: datetime,
                  allow_exact: bool) -> Optional[int]:
    lo, hi = 0, len(times)
    while lo < hi:
        mid = (lo + hi) // 2
        ok = times[mid] <= t if allow_exact else times[mid] < t
        if ok:
            lo = mid + 1
        else:
            hi = mid
    return lo - 1 if lo else None


def observation_key(obs: Observation, key_field: str) -> Any:
    """The panel key of an observation, from provenance or payload."""
    if key_field in obs.provenance:
        return obs.provenance[key_field]
    return obs.payload.get(key_field)


def as_of_panel(decision_times: Sequence[datetime],
                observations: Sequence[Observation],
                key_field: str,
                tolerance_s: float | None = None,
                allow_exact: bool = True) -> dict[Any, list[dict[str, Any]]]:
    """As-of join per panel member, keyed by `key_field`.

    Most sources here are panels, not single series: one `option_quotes` cycle
    writes ~56 instruments under the same `ts`, and `signal_log` writes one row
    per signal under the same `day`. A plain as-of join over such a source
    returns whichever member happened to sort last, which is deterministic but
    meaningless -- validating against production showed it silently answering
    "the newest quote" with an arbitrary instrument, and "the newest signal"
    with whichever family sorted last rather than the one being decided on.

    Callers that mean one series must partition first; this does it for them.
    """
    grouped: dict[Any, list[Observation]] = {}
    for o in observations:
        grouped.setdefault(observation_key(o, key_field), []).append(o)
    return {
        key: as_of_join(decision_times, obs, tolerance_s, allow_exact)
        for key, obs in sorted(grouped.items(), key=lambda kv: str(kv[0]))
    }


def check_no_lookahead(joined: Iterable[dict[str, Any]],
                       allow_exact: bool = True) -> list[dict[str, Any]]:
    """Re-verify the join independently. Returns violations, never raises.

    Deliberately does not trust `as_of_join`: it re-reads the timestamps on the
    result, so a future change to the join's direction shows up here rather than
    being certified by the same code that caused it.
    """
    violations = []
    for r in joined:
        if not r.get("matched"):
            continue
        avail, dec = r.get("available_ts"), r.get("decision_ts")
        if avail is None or dec is None:
            violations.append({**r, "reason": "matched row without timestamps"})
            continue
        bad = avail > dec if allow_exact else avail >= dec
        if bad:
            violations.append({
                **r, "reason": f"available_ts {avail.isoformat()} is after "
                               f"decision_ts {dec.isoformat()}"})
        if (r.get("age_s") or 0) < 0:
            violations.append({**r, "reason": "negative age"})
    return violations


def assert_no_lookahead(joined: Iterable[dict[str, Any]],
                        allow_exact: bool = True) -> None:
    v = check_no_lookahead(joined, allow_exact)
    if v:
        raise LookAheadError(f"{len(v)} look-ahead violation(s); first: {v[0]['reason']}")


def _connect_ro(db_path) -> sqlite3.Connection:
    p = pathlib.Path(db_path).resolve()
    if not p.exists():
        raise FileNotFoundError(p)
    conn = sqlite3.connect(f"{p.as_uri()}?mode=ro", uri=True, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def load_sqlite_source(db_path, source: str, payload_fields: Sequence[str] = (),
                       where: str = "", params: Sequence[Any] = (),
                       limit: int | None = None) -> list[Observation]:
    """Load one declared SQLite-backed source as Observations.

    `where` is caller-supplied SQL appended verbatim; this is a research tool
    reading a read-only handle, not a public interface.
    """
    spec = get_spec(source)
    conn = _connect_ro(db_path)
    try:
        cols = {spec.event_field}
        if spec.availability_field:
            cols.add(spec.availability_field)
        cols.update(payload_fields)
        cols.update(spec.provenance_fields)
        sql = f"SELECT {', '.join(sorted(cols))} FROM {spec.table}"
        if where:
            sql += f" WHERE {where}"
        sql += f" ORDER BY {spec.availability_field or spec.event_field}"
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = conn.execute(sql, tuple(params)).fetchall()
    finally:
        conn.close()
    return to_observations(rows, spec, payload_fields)


def load_oi(db_path, instrument_id: int | None = None,
            limit: int | None = None) -> tuple[list[Observation], dict[str, Any]]:
    """The open-interest series, from wherever it actually lives.

    Returns the observations and the measured cadence, because OI is the source
    whose grid is most often assumed and least often regular.
    """
    spec = oi_source()
    where = "oi IS NOT NULL"
    params: list[Any] = []
    if instrument_id is not None:
        where += " AND instrument_id = ?"
        params.append(instrument_id)
    obs = load_sqlite_source(db_path, spec.name,
                            payload_fields=("oi", "oi_contracts", "oi_value_usd"),
                            where=where, params=params, limit=limit)
    cadence = cadence_profile([o.available_ts for o in obs], unit="iso",
                              nominal_seconds=180.0)
    cadence["declared_source"] = "oi_snapshots"
    cadence["actual_source"] = spec.name
    cadence["note"] = ("oi_snapshots is empty on production; OI is carried by "
                       "option_quotes at the quote cadence, not on a 180s grid")
    return obs, cadence


def load_lake_source(source: str, base_dir=None,
                     payload_fields: Sequence[str] = ()) -> list[Observation]:
    """Load a Parquet-lake source, one partition at a time, with provenance.

    Read partition-by-partition rather than through one merged DuckDB view.
    `storage.duck.register_view` builds an explicit file list without hive
    partitioning, so a row selected from the merged view cannot say which
    partition it came from -- and a feature input whose provenance is a row of
    Nones is not reproducible. Reading per partition attaches the dataset,
    symbol, year, month and the manifest's `parquet_sha256` to every row, which
    is the hook that lets a stored feature be traced back to an exact file.
    """
    spec = get_spec(source)
    if spec.store != "parquet_lake":
        raise ValueError(f"{source} is not a lake source")
    from storage import lake  # noqa: PLC0415 -- optional heavy dep, lake-only path

    dataset, symbol = spec.table.split("/", 1)
    base = pathlib.Path(base_dir) if base_dir else (_RESEARCH_ROOT / "data" / "raw")
    out: list[Observation] = []
    for part in lake.list_partitions(base, dataset, symbol):
        manifest_path = part["path"].parent / "_manifest.json"
        sha = None
        if manifest_path.exists():
            try:
                sha = json.loads(manifest_path.read_text()).get("parquet_sha256")
            except (ValueError, OSError):
                sha = None
        prov = {"dataset": dataset, "symbol": symbol, "year": part["year"],
                "month": part["month"], "parquet_sha256": sha}
        frame = lake.read_partition(base, dataset, symbol, part["year"], part["month"])
        keep = [c for c in {spec.event_field, *payload_fields} if c in frame.columns]
        for row in frame[keep].to_dict("records"):
            avail = availability_time(row, spec)
            if avail is None:
                continue
            out.append(Observation(
                source=spec.name, event_ts=event_time(row, spec),
                available_ts=avail,
                payload={f: row.get(f) for f in payload_fields},
                provenance=dict(prov)))
    out.sort(key=lambda o: (o.available_ts, str(o.provenance)))
    return out


def align_many(decision_times: Sequence[datetime],
               sources: dict[str, Sequence[Observation]],
               tolerance_s: dict[str, float] | None = None,
               key_fields: dict[str, str] | None = None,
               keys: dict[str, Any] | None = None
               ) -> list[dict[str, Any]]:
    """One row per decision instant, carrying an as-of match from each source.

    Source timestamps and provenance are preserved per source rather than
    flattened, so a downstream consumer can always tell which observation of
    which vintage produced a given field.
    """
    tolerance_s = tolerance_s or {}
    key_fields = key_fields or {}
    keys = keys or {}
    per_source: dict[str, list[dict[str, Any]]] = {}
    for name, obs in sources.items():
        kf = key_fields.get(name)
        if kf is None:
            per_source[name] = as_of_join(decision_times, obs, tolerance_s.get(name))
            continue
        # A panel source must be told which member it is being asked about;
        # answering with an arbitrary one is worse than answering with nothing.
        if name not in keys:
            raise ValueError(
                f"{name!r} is keyed by {kf!r}; pass keys[{name!r}] to select "
                "the panel member, or drop key_fields to join the raw series")
        selected = [o for o in obs if observation_key(o, kf) == keys[name]]
        per_source[name] = as_of_join(decision_times, selected, tolerance_s.get(name))
    out = []
    for i, t in enumerate(decision_times):
        row: dict[str, Any] = {"decision_ts": t, "sources": {}}
        for name, joined in per_source.items():
            j = joined[i]
            row["sources"][name] = {
                "matched": j["matched"],
                "age_s": j["age_s"],
                "event_ts": j["event_ts"],
                "available_ts": j["available_ts"],
                "payload": j["payload"],
                "provenance": j["provenance"],
            }
        out.append(row)
    return out


def coverage(aligned: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Match rate and staleness per source over an aligned set."""
    if not aligned:
        return {"decisions": 0, "sources": {}}
    names = sorted(aligned[0]["sources"])
    out: dict[str, Any] = {"decisions": len(aligned), "sources": {}}
    for n in names:
        ages = [r["sources"][n]["age_s"] for r in aligned
                if r["sources"][n]["matched"] and r["sources"][n]["age_s"] is not None]
        matched = sum(1 for r in aligned if r["sources"][n]["matched"])
        entry: dict[str, Any] = {
            "matched": matched,
            "match_rate": matched / len(aligned),
        }
        if ages:
            ages_sorted = sorted(ages)
            entry["age_s"] = {
                "min": ages_sorted[0],
                "median": ages_sorted[len(ages_sorted) // 2],
                "max": ages_sorted[-1],
            }
        out["sources"][n] = entry
    return out


def _get(row: Any, field: str) -> Any:
    if isinstance(row, dict):
        return row.get(field)
    try:
        return row[field]
    except (TypeError, KeyError, IndexError):
        return getattr(row, field, None)
