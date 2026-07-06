"""Golden-vector conformance tests for src/archive/content_hash.py (T2).

Proves content_hash.py reproduces the exact M1.1 v1.0 sec.10
canonicalization byte-for-byte against the three frozen fixtures
(GV-0/GV-1/GV-2, see src/archive/golden_vectors.py). A failure here is an
implementation defect against the frozen spec -- fix content_hash.py, do
not adjust the expected digest.

Run from repo root:
    conda run -n btc_bot pytest tests/archive/test_content_hash_golden.py -v
"""
from __future__ import annotations

import struct

from src.archive import content_hash as ch
from src.archive import golden_vectors as gv


class TestGV0Empty:
    def test_content_hash_matches_frozen_digest(self):
        assert ch.content_hash(gv.GV_0_ROWS) == gv.GV_0_CONTENT_HASH

    def test_material_is_bare_zero_row_count(self):
        assert ch.material(gv.GV_0_ROWS) == struct.pack(">Q", 0)


class TestGV1SingleRow:
    def test_content_hash_matches_frozen_digest(self):
        assert ch.content_hash(gv.GV_1_ROWS) == gv.GV_1_CONTENT_HASH

    def test_row_count_prefix_is_one(self):
        assert ch.material(gv.GV_1_ROWS).startswith(struct.pack(">Q", 1))

    def test_repeated_calls_are_identical(self):
        assert ch.content_hash(gv.GV_1_ROWS) == ch.content_hash(gv.GV_1_ROWS)


class TestGV2TwoRowsOrderIndependence:
    def test_content_hash_matches_frozen_digest(self):
        assert ch.content_hash(gv.GV_2_ROWS) == gv.GV_2_CONTENT_HASH

    def test_reversed_input_order_matches_frozen_digest(self):
        reversed_rows = list(reversed(gv.GV_2_ROWS))
        assert ch.content_hash(reversed_rows) == gv.GV_2_CONTENT_HASH

    def test_canonical_sort_places_lower_symbol_first(self):
        sorted_rows = ch.canonical_sort(gv.GV_2_ROWS)
        assert sorted_rows[0]["symbol"] < sorted_rows[1]["symbol"]
        # GV_2_ROWS is fed as [P-..., C-...] -- canonical order must flip it.
        assert sorted_rows[0] is gv.GV_2_ROW_B
        assert sorted_rows[1] is gv.GV_2_ROW_A
