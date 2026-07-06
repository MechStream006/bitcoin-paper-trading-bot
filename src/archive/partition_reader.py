"""Read-only hot-DB day-partition reader.

Reads a single UTC-day partition of option_quotes from the hot SQLite DB
for handoff to content_hash.py / parquet_writer.py. Must open the DB via
a read-only ("mode=ro") URI, the same discipline already used by
src/alerts/forward_status.py -- this module is a consumer of the hot
path, never a writer, and must never create a missing DB file.

TODO(T3): implement read_partition(db_path, key) -> list[Row]. Zero rows
for an empty/missing partition must be a valid result, never an error.

No SQLite access and no executable archive functionality is implemented
in this file yet.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PartitionKey:
    """Identifies one day-partition to export.

    utc_date is stored verbatim as a "YYYY-MM-DD" string (see M1.1 v1.0
    decision D-1: ts/date values are never reparsed or reformatted).
    """
    utc_date: str
