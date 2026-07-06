"""Canonical content-hash encoder (M1.1 v1.0 sec.10, hash_spec_version=1).

Pure, dependency-free encoding of a partition's rows into the canonical
byte material whose SHA-256 digest is the partition's content_hash. This
module must never import sqlite3, pyarrow, or any I/O-capable library --
it operates only on plain Python dict/list values already in memory.

Canonicalization rules this module is responsible for (per M1.1 v1.0):
  - Fixed column order (schema-defined, not input-defined).
  - Row sort key: (symbol, ts, instrument_id) -- deterministic regardless
    of input order.
  - Type-tagged big-endian encoding: null / int64 / float64 / UTF-8 string.
  - NaN payloads normalized to a single canonical quiet-NaN bit pattern.
  - -0.0 normalized to +0.0.
  - Row-count-prefixed material; sha256 digest rendered as "sha256:<hex>".

TODO(T1): implement canonical_sort(), encode_row(), material(), and
content_hash() exactly per M1.1 v1.0 sec.10. Golden-vector conformance
(GV-0/GV-1/GV-2, see golden_vectors.py) is the acceptance bar for this
module -- it must reproduce the frozen digests byte-for-byte.

No executable archive functionality is implemented in this file yet.
"""
from __future__ import annotations

# A partition row as consumed by the canonical encoder. Deferred: the
# authoritative column set is fixed in T1 alongside canonical_sort/encode_row.
Row = dict[str, object]
