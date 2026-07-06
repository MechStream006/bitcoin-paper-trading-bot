"""Frozen golden-vector fixtures for content_hash.py conformance testing.

Three fixed-input vectors with authoritative expected digests, used to
prove content_hash.py reproduces the exact M1.1 v1.0 sec.10 canonicalization:

  GV-0: empty partition (zero rows).
  GV-1: single row with a NULL column, a NaN float column, and a present
        int exch_ts -- exercises null/NaN/int/float/string encoding.
  GV-2: two rows fed in non-canonical input order, one with a NULL int
        column and one with a -0.0 float column -- exercises canonical
        sort and order-independence.

Test-only fixtures. This module must never be imported by production
(non-test) code paths.

TODO(T2): populate GV_0_ROWS/GV_1_ROWS/GV_2_ROWS and their expected
content_hash digests from the authoritative reference encoding, and add
the byte-exact reproduction tests in tests/archive/test_content_hash_golden.py.

No executable archive functionality is implemented in this file yet.
"""
from __future__ import annotations
