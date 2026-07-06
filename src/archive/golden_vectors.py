"""Frozen golden-vector fixtures for content_hash.py conformance testing.

Three fixed-input vectors with authoritative expected digests, proving
content_hash.py reproduces the exact M1.1 v1.0 sec.10 canonicalization:

  GV-0: empty partition (zero rows).
  GV-1: single row with a NULL column (close), a NaN float column (rho),
        and a present int exch_ts -- exercises null/NaN/int/float/string
        encoding together in one row.
  GV-2: two rows fed in non-canonical input order (P-... before C-...,
         which sorts after it), one row with a NULL exch_ts and the other
        with a -0.0 theta -- exercises canonical sort and order
        independence together.

The expected digests below were produced by an independent reference
implementation of the M1.1 v1.0 sec.10 encoding during the M1.2 spec
review, and re-verified byte-for-byte against src/archive/content_hash.py
(T1) before being frozen here. A mismatch against these constants is an
implementation defect in content_hash.py, not a reason to update this
file.

Test-only fixtures. This module must never be imported by production
(non-test) code paths.
"""
from __future__ import annotations

from src.archive.content_hash import Row

# ---------------------------------------------------------------------------
# GV-0: empty partition
# ---------------------------------------------------------------------------

GV_0_ROWS: list[Row] = []
GV_0_CONTENT_HASH = "sha256:af5570f5a1810b7af78caf4bc70a660f0df51e42baf91d4de5b2328de0e83dfc"

# ---------------------------------------------------------------------------
# GV-1: single row -- NULL close, NaN rho, present int exch_ts
# ---------------------------------------------------------------------------

GV_1_ROW: Row = {
    "symbol": "C-BTC-95000-010726", "instrument_id": 184953,
    "ts": "2026-07-01T00:00:00Z", "exch_ts": 1782000000000000,
    "spot_price": 95000.5, "mark_price": 1234.5, "close": None,
    "best_bid": 1200.0, "best_ask": 1250.0, "bid_qty": 0.1, "ask_qty": 0.2,
    "mark_iv": 0.55, "bid_iv": 0.54, "ask_iv": 0.56, "delta": 0.5,
    "gamma": 0.001, "theta": -1.2, "vega": 3.4, "rho": float("nan"),
    "oi": 10.0, "oi_contracts": 100.0, "oi_value_usd": 950000.0,
    "volume_24h": 5.0,
}
GV_1_ROWS: list[Row] = [GV_1_ROW]
GV_1_CONTENT_HASH = "sha256:6c1031a1655b2bdf7a9856cc6c15f29098385cf7b7b68c670413bcc332ed4ddd"

# ---------------------------------------------------------------------------
# GV-2: two rows, non-canonical input order, NULL exch_ts, -0.0 theta
# ---------------------------------------------------------------------------

GV_2_ROW_A: Row = {
    "symbol": "P-BTC-95000-010726", "instrument_id": 184954,
    "ts": "2026-07-01T00:01:00Z", "exch_ts": None,
    "spot_price": 95000.5, "mark_price": 800.0, "close": 810.0,
    "best_bid": 790.0, "best_ask": 815.0, "bid_qty": 0.3, "ask_qty": 0.4,
    "mark_iv": 0.60, "bid_iv": 0.59, "ask_iv": 0.61, "delta": -0.5,
    "gamma": 0.001, "theta": -0.0, "vega": 3.4, "rho": 0.01,
    "oi": 20.0, "oi_contracts": 200.0, "oi_value_usd": 1900000.0,
    "volume_24h": 7.0,
}
GV_2_ROW_B: Row = dict(GV_1_ROW)

# Deliberately non-canonical: "P-..." sorts after "C-..." (GV_2_ROW_B's
# symbol), so this input order is the reverse of the canonical order.
GV_2_ROWS: list[Row] = [GV_2_ROW_A, GV_2_ROW_B]
GV_2_CONTENT_HASH = "sha256:33c5d42a99649e01406f97f4dc52a4e8c318dc2bb447577b42cf44459a1fe7c9"
