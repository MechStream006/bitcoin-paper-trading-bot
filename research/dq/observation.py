"""
InputObservation — docs/governance/data_completeness_gate.md §5.2.

The complete set of facts a Check is permitted to see about one input. Two
properties matter more than anything else about this class:

1. **It is metadata, never payload (L3).** Row counts, bounds, arrival times,
   schema, coverage counts, a content fingerprint. A probe that loads a full
   dataframe to build one of these is a failed review. Gating must be orders
   of magnitude cheaper than deriving, or it will be skipped under load, and
   a gate that gets skipped is not a gate.

2. **It is the replay substrate.** An observation is fully serialisable both
   ways, and is persisted with the verdict. That is what makes
   `(contract_hash, engine_version, observations, as_of) -> verdict` replayable
   with NO access to the original sources, forever (§6, Reproducibility) —
   an audit in 2031 must not depend on a Parquet partition still existing in
   the state it was in.

Timestamps are ISO-8601 UTC strings, not floats and not epoch ints, so that
canonicalisation is exact and no float ever enters a hash (governance_receipts.md
§1.3 rule 3). Numeric bounds observed from data (`min_value`/`max_value`, used
by the band check to catch the ms->us class of failure) are DECIMAL STRINGS
for the same reason.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any


class ObservationError(ValueError):
    """Raised for a malformed InputObservation."""


@dataclass(frozen=True)
class InputObservation:
    """
    What the gate knows about one input for one period, as of one instant.

    Every field is optional EXCEPT input_id, because a probe that could not
    determine a field must say so by leaving it None rather than by guessing
    — and a check that needs a field it did not get returns UNKNOWN (L2), it
    does not assume a default.

    probe_error: set by the probe layer when it could not complete. Its mere
    presence forces UNKNOWN for this input regardless of every other field.
    """
    input_id: str

    rows: int | None = None

    # Period bounds actually observed in the data (not the declared period).
    min_ts: str | None = None
    max_ts: str | None = None

    # When the newest record ARRIVED (not what period it describes). This is
    # the field that makes point-in-time correctness honest: a replay must not
    # benefit from data that had not yet arrived at `as_of`. Without
    # arrival-time tracking, backfills silently acquire look-ahead (§6).
    newest_arrival_ts: str | None = None

    # column name -> declared dtype string, as OBSERVED at the source.
    schema: dict[str, str] | None = None

    # Observed value bounds per column, as decimal STRINGS. Used by the band
    # check. e.g. {"open_time": ["1735689600000", "1738367940000"]}
    value_bounds: dict[str, list[str]] | None = None

    coverage_count: int | None = None
    coverage_denominator: int | None = None

    # Interior-gap detection for a history window: the count of distinct
    # periods present, and whether any interior gap was found.
    history_rows: int | None = None
    has_interior_gaps: bool | None = None

    # Fingerprint of the input partitions consumed. If a partition is ever
    # silently rewritten upstream, this changes, and the mismatch is
    # detectable against every receipt that ever consumed it (§9).
    content_hash: str | None = None

    probe_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_id": self.input_id,
            "rows": self.rows,
            "min_ts": self.min_ts,
            "max_ts": self.max_ts,
            "newest_arrival_ts": self.newest_arrival_ts,
            "schema": self.schema,
            "value_bounds": self.value_bounds,
            "coverage_count": self.coverage_count,
            "coverage_denominator": self.coverage_denominator,
            "history_rows": self.history_rows,
            "has_interior_gaps": self.has_interior_gaps,
            "content_hash": self.content_hash,
            "probe_error": self.probe_error,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "InputObservation":
        if "input_id" not in d or not d["input_id"]:
            raise ObservationError("input_id is required")
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(d) - known
        if unknown:
            raise ObservationError(
                f"unknown observation field(s): {sorted(unknown)}. An observation read "
                f"back from a receipt must round-trip exactly; a silently-dropped field "
                f"would change what replay sees."
            )
        return cls(**d)

    def bound(self, column: str) -> tuple[Decimal, Decimal] | None:
        """Observed (min, max) for a column, as Decimals. None if not observed."""
        if not self.value_bounds or column not in self.value_bounds:
            return None
        pair = self.value_bounds[column]
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ObservationError(
                f"value_bounds['{column}'] must be a [min, max] pair, got {pair!r}"
            )
        try:
            return Decimal(str(pair[0])), Decimal(str(pair[1]))
        except InvalidOperation as exc:
            raise ObservationError(f"value_bounds['{column}'] is not numeric: {pair!r}") from exc


def observations_to_list(observations: dict[str, InputObservation]) -> list[dict]:
    """Deterministic, input_id-sorted serialisation. The hash substrate."""
    return [observations[k].to_dict() for k in sorted(observations)]


def observations_from_list(items: list[dict]) -> dict[str, InputObservation]:
    out: dict[str, InputObservation] = {}
    for item in items:
        obs = InputObservation.from_dict(item)
        out[obs.input_id] = obs
    return out
