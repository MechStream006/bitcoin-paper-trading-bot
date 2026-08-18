"""Resolve the BTCUSD perpetual into the local `instruments` table (B5).

`orders.instrument_id` references `instruments(id)`, so an order row cannot be
written for a product that is not there. The collector only ever populates the
options universe, so the perp is absent until this runs.

Read-only against the exchange (public `/v2/products`), additive against the DB
(one `INSERT OR REPLACE` via the existing `upsert_instrument`). Idempotent: safe
to re-run, and re-running refreshes tick size / contract value if Delta changes
them.

Usage:
    python scripts/resolve_perp_instrument.py [--dry-run] [--symbol BTCUSD]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config.settings as cfg           # noqa: E402
from src.data import storage            # noqa: E402
from src.exchange.delta_client import DeltaClient  # noqa: E402

EXPECTED_PRODUCT_ID = 84
EXPECTED_CONTRACT_TYPE = "perpetual_futures"


def fetch_perp(client: DeltaClient, symbol: str) -> dict | None:
    resp = client._public_get(
        "/v2/products",
        {"contract_types": EXPECTED_CONTRACT_TYPE, "states": "live"},
    )
    for p in resp.get("result", []):
        if p.get("symbol") == symbol:
            return p
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Resolve BTCUSD perp into instruments")
    ap.add_argument("--symbol", default="BTCUSD")
    ap.add_argument("--db-path", type=Path, default=cfg.DB_PATH)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    client = DeltaClient(cfg.DELTA_BASE_URL)
    product = fetch_perp(client, args.symbol)
    if product is None:
        print(f"[resolve] ERROR: {args.symbol} not found in live perpetual products",
              file=sys.stderr)
        return 1

    pid = product.get("id")
    print(f"[resolve] symbol          : {product.get('symbol')}")
    print(f"[resolve] product_id      : {pid}")
    print(f"[resolve] contract_type   : {product.get('contract_type')}")
    print(f"[resolve] state           : {product.get('state')}")
    print(f"[resolve] tick_size       : {product.get('tick_size')}")
    print(f"[resolve] contract_value  : {product.get('contract_value')}")

    if product.get("contract_type") != EXPECTED_CONTRACT_TYPE:
        print(f"[resolve] ERROR: unexpected contract_type", file=sys.stderr)
        return 1
    if pid != EXPECTED_PRODUCT_ID:
        # Not fatal -- Delta owns the id -- but it must be seen, since the risk
        # allowlist pins 84.
        print(f"[resolve] WARNING: product_id {pid} != expected "
              f"{EXPECTED_PRODUCT_ID}; risk allowlist pins the expected value",
              file=sys.stderr)

    if args.dry_run:
        print("[resolve] DRY RUN — nothing written")
        return 0

    conn = storage.get_connection(args.db_path)
    try:
        storage.init_schema(conn)
        storage.init_execution_schema(conn)
        storage.upsert_instrument(conn, product, datetime.now(timezone.utc).isoformat())
        conn.commit()
        ok = storage.instrument_exists(conn, pid)
    finally:
        conn.close()

    print(f"[resolve] instruments row present: {ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
