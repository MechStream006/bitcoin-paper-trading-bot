"""Property and negative tests for src/archive/content_hash.py (T2).

Covers canonicalization rules not already pinned by the GV-0/1/2 golden
vectors: canonical ordering with tie-breaks, NULL-vs-float-zero
distinction, arbitrary NaN payload/sign normalization, -0.0
normalization, mutation sensitivity, full-permutation row-order
independence, and fail-closed behavior on malformed input.

Run from repo root:
    conda run -n btc_bot pytest tests/archive/test_content_hash_properties.py -v
"""
from __future__ import annotations

import itertools
import math
import struct

import pytest

from src.archive import content_hash as ch
from src.archive import golden_vectors as gv


def _row(**overrides):
    """A copy of GV_1_ROW with the given fields overridden."""
    base = dict(gv.GV_1_ROW)
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Canonical ordering
# ---------------------------------------------------------------------------

class TestCanonicalSort:
    def test_sorts_by_symbol_then_ts(self):
        r_b_late = _row(symbol="B-X", ts="2026-01-02T00:00:00Z", instrument_id=1)
        r_b_early = _row(symbol="B-X", ts="2026-01-01T00:00:00Z", instrument_id=1)
        r_a = _row(symbol="A-X", ts="2026-01-01T00:00:00Z", instrument_id=1)
        out = ch.canonical_sort([r_b_late, r_a, r_b_early])
        assert [r["symbol"] for r in out] == ["A-X", "B-X", "B-X"]
        assert out[1]["ts"] < out[2]["ts"]

    def test_tie_breaks_on_instrument_id(self):
        r_hi = _row(symbol="A-X", ts="2026-01-01T00:00:00Z", instrument_id=99)
        r_lo = _row(symbol="A-X", ts="2026-01-01T00:00:00Z", instrument_id=1)
        out = ch.canonical_sort([r_hi, r_lo])
        assert [r["instrument_id"] for r in out] == [1, 99]

    def test_does_not_mutate_input_list(self):
        rows = [_row(symbol="B-X"), _row(symbol="A-X")]
        original_order = list(rows)
        ch.canonical_sort(rows)
        assert rows == original_order

    def test_returns_new_list_object(self):
        rows = [_row()]
        assert ch.canonical_sort(rows) is not rows

    def test_missing_sort_key_raises(self):
        malformed = {"symbol": "A-X", "ts": "2026-01-01T00:00:00Z"}  # no instrument_id
        with pytest.raises(KeyError):
            ch.canonical_sort([malformed])

    def test_full_permutation_order_independence(self):
        rows = [_row(symbol=s, instrument_id=i)
                for i, s in enumerate(["A-X", "B-X", "C-X"])]
        hashes = {ch.content_hash(list(perm))
                  for perm in itertools.permutations(rows)}
        assert len(hashes) == 1


# ---------------------------------------------------------------------------
# NULL / NaN / -0.0 encoding
# ---------------------------------------------------------------------------

class TestValueEncoding:
    def test_null_encodes_distinctly_from_float_zero(self):
        row_null = _row(close=None)
        row_zero = _row(close=0.0)
        assert ch.content_hash([row_null]) != ch.content_hash([row_zero])

    def test_null_encodes_distinctly_from_int_zero(self):
        row_null = _row(exch_ts=None)
        row_zero = _row(exch_ts=0)
        assert ch.content_hash([row_null]) != ch.content_hash([row_zero])

    def test_empty_row_dict_encodes_all_nulls(self):
        assert ch.encode_row({}) == b"\x00" * len(ch.COLUMN_ORDER)

    @pytest.mark.parametrize("nan_value", [
        float("nan"),
        -float("nan"),
        math.nan,
        struct.unpack(">d", struct.pack(">Q", 0x7FF8000000000001))[0],  # quiet NaN, nonzero payload
        struct.unpack(">d", struct.pack(">Q", 0xFFF8000000000000))[0],  # NaN, sign bit set
    ])
    def test_all_nan_payloads_normalize_identically(self, nan_value):
        assert math.isnan(nan_value)  # sanity: fixture really is a NaN
        row = _row(rho=nan_value)
        assert ch.content_hash([row]) == ch.content_hash([_row(rho=float("nan"))])

    def test_negative_zero_normalizes_to_positive_zero(self):
        row_neg = _row(theta=-0.0)
        row_pos = _row(theta=0.0)
        assert ch.content_hash([row_neg]) == ch.content_hash([row_pos])

    def test_negative_zero_and_nan_are_not_confused(self):
        row_neg_zero = _row(theta=-0.0)
        row_nan = _row(theta=float("nan"))
        assert ch.content_hash([row_neg_zero]) != ch.content_hash([row_nan])

    def test_string_length_prefix_counts_utf8_bytes_not_characters(self):
        # U+20BF (BITCOIN SIGN) is a 3-byte UTF-8 character -- proves the
        # length prefix is the encoded byte length, not the Python str length.
        symbol = "C-BTC-₿"
        encoded = ch._encode_value("symbol", symbol)
        expected_len = len(symbol.encode("utf-8"))
        assert len(symbol) != expected_len  # sanity: multi-byte char present
        length_field = struct.unpack(">I", encoded[1:5])[0]
        assert length_field == expected_len

    def test_hand_computed_row_bytes_for_minimal_row(self):
        row = {"symbol": "AB", "instrument_id": 1, "ts": "T", "exch_ts": None}
        expected = (
            b"\x03" + struct.pack(">I", 2) + b"AB"   # symbol (str)
            + b"\x01" + struct.pack(">q", 1)          # instrument_id (int)
            + b"\x03" + struct.pack(">I", 1) + b"T"   # ts (str)
            + b"\x00"                                 # exch_ts (null)
            + b"\x00" * (len(ch.COLUMN_ORDER) - 4)    # remaining columns, all null
        )
        assert ch.encode_row(row) == expected


# ---------------------------------------------------------------------------
# Determinism / mutation sensitivity
# ---------------------------------------------------------------------------

class TestDeterminismAndSensitivity:
    def test_content_hash_format(self):
        h = ch.content_hash(gv.GV_1_ROWS)
        assert h.startswith("sha256:")
        assert len(h) == len("sha256:") + 64
        int(h.split(":", 1)[1], 16)  # must be valid hex

    def test_repeated_calls_are_byte_identical(self):
        assert ch.material(gv.GV_1_ROWS) == ch.material(gv.GV_1_ROWS)

    def test_smallest_float_delta_changes_hash(self):
        mutated = [_row(mark_price=gv.GV_1_ROW["mark_price"] + 1e-9)]
        assert ch.content_hash(gv.GV_1_ROWS) != ch.content_hash(mutated)

    def test_string_change_changes_hash(self):
        mutated = [_row(symbol=gv.GV_1_ROW["symbol"] + "X")]
        assert ch.content_hash(gv.GV_1_ROWS) != ch.content_hash(mutated)

    def test_int_change_changes_hash(self):
        mutated = [_row(instrument_id=gv.GV_1_ROW["instrument_id"] + 1)]
        assert ch.content_hash(gv.GV_1_ROWS) != ch.content_hash(mutated)

    def test_unknown_extra_key_is_ignored(self):
        row_with_extra = dict(gv.GV_1_ROW)
        row_with_extra["not_a_real_column"] = "ignored"
        assert ch.content_hash([row_with_extra]) == ch.content_hash(gv.GV_1_ROWS)
