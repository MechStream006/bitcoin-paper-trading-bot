"""Canonical content-hash encoder (M1.1 v1.0 sec.10, hash_spec_version=1).

Pure, dependency-free encoding of a partition's rows into the canonical
byte material whose SHA-256 digest is the partition's content_hash. This
module must never import sqlite3, pyarrow, or any I/O-capable library --
it operates only on plain Python dict/list values already in memory, and
every function here is a pure function: same input always produces the
same output, no filesystem/network/catalog access, no module-level
mutable state, no side effects.

Canonicalization rules implemented here (per M1.1 v1.0 sec.10):
  - Fixed column order (COLUMN_ORDER below), independent of input order.
  - Row sort key: (symbol, ts, instrument_id) -- deterministic regardless
    of input order (canonical_sort).
  - Type-tagged big-endian encoding per column:
        0x00              null
        0x01 + int64 BE   integer (symbol/ts columns excluded)
        0x02 + float64 BE IEEE-754 double
        0x03 + uint32 BE length prefix + UTF-8 bytes   string
  - NaN payloads normalized to a single canonical quiet-NaN bit pattern
    (0x7ff8000000000000) regardless of sign or payload bits.
  - -0.0 normalized to +0.0 before encoding.
  - Row-count-prefixed material (big-endian uint64); digest rendered as
    "sha256:<hex>".

Column order and type map are a frozen contract: any change here changes
every content_hash ever computed and must go through the M1 architecture
freeze process, not an ordinary code change.
"""
from __future__ import annotations

import hashlib
import math
import struct

# A partition row as consumed by the canonical encoder.
Row = dict[str, object]

# Fixed column order (hash_spec_version=1). Do not reorder, add, or
# remove columns without an architecture-freeze exception -- this order
# is baked into every content_hash already computed under this version.
COLUMN_ORDER: tuple[str, ...] = (
    "symbol", "instrument_id", "ts", "exch_ts",
    "spot_price", "mark_price", "close", "best_bid", "best_ask",
    "bid_qty", "ask_qty",
    "mark_iv", "bid_iv", "ask_iv", "delta", "gamma", "theta", "vega", "rho",
    "oi", "oi_contracts", "oi_value_usd", "volume_24h",
)

# Per-column type tag. Any column not listed defaults to "float" (float64).
COLUMN_TYPES: dict[str, str] = {
    "symbol": "str",
    "instrument_id": "int",
    "ts": "str",
    "exch_ts": "int",
}

_TAG_NULL = b"\x00"
_TAG_INT64 = b"\x01"
_TAG_FLOAT64 = b"\x02"
_TAG_STRING = b"\x03"

# IEEE-754 binary64 bit pattern for a canonical quiet NaN. Packing this
# fixed integer (rather than struct.pack(">d", float("nan"))) guarantees
# the same bytes on every platform, regardless of how the local libm
# represents NaN payloads/signs.
_CANONICAL_NAN_BITS = 0x7FF8000000000000


def _encode_value(column: str, value: object) -> bytes:
    """Encode one column value under its canonical type tag.

    Pure function: does not mutate `value` or any module state.
    """
    if value is None:
        return _TAG_NULL

    column_type = COLUMN_TYPES.get(column, "float")

    if column_type == "int":
        return _TAG_INT64 + struct.pack(">q", int(value))

    if column_type == "str":
        encoded = value.encode("utf-8")
        return _TAG_STRING + struct.pack(">I", len(encoded)) + encoded

    # float64 (default for every column not in COLUMN_TYPES)
    as_float = float(value)
    if math.isnan(as_float):
        return _TAG_FLOAT64 + struct.pack(">Q", _CANONICAL_NAN_BITS)
    if as_float == 0.0:
        as_float = 0.0  # normalize -0.0 -> +0.0 (0.0 == -0.0 is True in IEEE-754)
    return _TAG_FLOAT64 + struct.pack(">d", as_float)


def canonical_sort(rows: list[Row]) -> list[Row]:
    """Return `rows` sorted by the canonical key (symbol, ts, instrument_id).

    Does not mutate the input list; returns a new list. Two inputs that
    differ only in row order sort to the same sequence.
    """
    return sorted(rows, key=lambda row: (row["symbol"], row["ts"], row["instrument_id"]))


def encode_row(row: Row) -> bytes:
    """Encode one row as its canonically-ordered, type-tagged column bytes.

    Columns are read in COLUMN_ORDER regardless of the row dict's own key
    order or the presence of extra keys not in COLUMN_ORDER.
    """
    return b"".join(_encode_value(column, row.get(column)) for column in COLUMN_ORDER)


def material(rows: list[Row]) -> bytes:
    """Build the canonical byte material for a partition's rows.

    Rows are canonically sorted first, so the result is independent of
    input row order. Prefixed with a big-endian uint64 row count so an
    empty partition and a partition of empty rows never collide.
    """
    sorted_rows = canonical_sort(rows)
    prefix = struct.pack(">Q", len(sorted_rows))
    return prefix + b"".join(encode_row(row) for row in sorted_rows)


def content_hash(rows: list[Row]) -> str:
    """Return the partition's content_hash: "sha256:<hex>" over material(rows)."""
    return "sha256:" + hashlib.sha256(material(rows)).hexdigest()
