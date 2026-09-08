#!/usr/bin/env python3
"""Operator-initiated clearing of a persisted reconciler halt.

A halt is cleared because someone looked, not because time passed. Nothing in
the runtime calls this: the scheduled reconciliation job returns early while
halted and never clears, and `Reconciler.__init__` restores the latch instead.
This script is the only surface that retires one, and it is deliberately not
importable-and-callable as a side effect of anything else.

There is no force flag, by construction. The one situation where an operator
most wants to override a halt is the one where the halt is most likely right,
so the command verifies rather than obeys: it independently re-reads the
exchange and the local ledger and refuses unless they agree exactly.

Read-only against Delta. It issues one signed GET and never places, cancels or
amends an order, so it cannot disturb a position or its protective stop while
resolving a halt about them.

Exit codes: 0 cleared or nothing to clear, 2 refused, 3 usage/config error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data import storage  # noqa: E402
from src.execution import fills as fills_mod  # noqa: E402
from src.execution.reconciler import exchange_signed_position  # noqa: E402

DEFAULT_PRODUCT_ID = 84  # BTCUSD perpetual on Delta India
HALT_STATE_FILENAME = ".reconciler-halt.json"

CLEARED = 0
REFUSED = 2
CONFIG_ERROR = 3


def read_halt_state(path: Path) -> tuple[str, dict]:
    """``(status, payload)`` where status is present/absent/corrupt.

    Corrupt is its own answer rather than an exception: a latch we cannot parse
    may be a latch that is still in force, so it must refuse rather than fall
    through to the not-halted path.
    """
    if not path.exists():
        return "absent", {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return "corrupt", {}
    if not isinstance(payload, dict):
        return "corrupt", {}
    return "present", payload


def retire_halt(path: Path, previous_reason: str) -> None:
    """Atomically mark the latch cleared: temp file, fsync, rename.

    The record is retired rather than deleted -- the file keeps saying who
    cleared what, and an absent file and a cleared file then mean the same
    thing to `Reconciler._restore_halt`, which only acts on `halted: true`.
    """
    payload = {
        "halted": False,
        "halt_reason": "",
        "ts": datetime.now(timezone.utc).isoformat(),
        "cleared_by": "scripts/clear_halt.py",
        "cleared_previous_reason": previous_reason,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def read_exchange_position(client, product_id: int) -> tuple[bool, int, str]:
    """``(ok, signed_size, detail)``. An unreadable exchange is never ``ok``.

    Scoped to one product for the same reason reconciliation is: the endpoint
    returns every product the account holds.
    """
    try:
        resp = client.get_positions()
    except Exception as exc:  # noqa: BLE001
        return False, 0, f"exchange query raised {type(exc).__name__}: {exc}"
    if not isinstance(resp, dict) or resp.get("success") is not True:
        return False, 0, f"exchange query failed or malformed: {resp!r}"
    result = resp.get("result")
    if not isinstance(result, list):
        return False, 0, f"exchange positions payload malformed: {result!r}"
    try:
        return True, exchange_signed_position(result, product_id), ""
    except Exception as exc:  # noqa: BLE001
        return False, 0, f"could not read exchange size: {type(exc).__name__}: {exc}"


def clear_halt(halt_path: Path, conn, client, product_id: int,
               out=print) -> int:
    """Verify, then clear. Every refusal leaves the latch exactly as it was."""
    status, payload = read_halt_state(halt_path)

    if status == "corrupt":
        out(f"REFUSED: halt state at {halt_path} is unreadable.")
        out("         It may record a halt that is still in force. Inspect it "
            "by hand; this command will not overwrite what it cannot read.")
        return REFUSED

    if status == "absent" or not payload.get("halted"):
        # Idempotent: running it twice is a no-op, not a second clear.
        out("Nothing to clear: no halt is currently persisted.")
        return CLEARED

    previous_reason = str(payload.get("halt_reason") or "no reason recorded")
    out(f"Persisted halt: {previous_reason}")
    out(f"  latched at:   {payload.get('ts') or 'unknown'}")

    ok, exch, detail = read_exchange_position(client, product_id)
    if not ok:
        out(f"REFUSED: {detail}")
        out("         An exchange we cannot read is an exchange we cannot "
            "claim to agree with.")
        return REFUSED

    local = fills_mod.position_size(conn)
    out(f"  exchange:     {exch:+d}")
    out(f"  local:        {local:+d}")

    if exch != local:
        out(f"REFUSED: exchange {exch:+d} != local {local:+d}.")
        out("         The halt stands. Resolve the divergence first.")
        return REFUSED

    retire_halt(halt_path, previous_reason)
    out("CLEARED: exchange and local agree exactly.")
    out(f"         previous halt reason: {previous_reason}")
    out("         The running process keeps its in-memory latch until it is "
        "restarted; restart btcbot.service for this to take effect.")
    return CLEARED


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=os.environ.get("DB_PATH", "data/btc_trading.db"))
    ap.add_argument("--halt-state", default=None,
                    help=f"defaults to <db dir>/{HALT_STATE_FILENAME}")
    ap.add_argument("--product-id", type=int, default=DEFAULT_PRODUCT_ID)
    args = ap.parse_args(argv)

    db_path = Path(args.db)
    halt_path = (Path(args.halt_state) if args.halt_state
                 else db_path.parent / HALT_STATE_FILENAME)

    status, _ = read_halt_state(halt_path)
    if status == "absent":
        print("Nothing to clear: no halt is currently persisted.")
        return CLEARED

    if not db_path.exists():
        print(f"ERROR: database not found: {db_path}", file=sys.stderr)
        return CONFIG_ERROR

    try:
        from dotenv import load_dotenv
        load_dotenv()
        from src.exchange.delta_client import DeltaClient
        client = DeltaClient(
            base_url=os.environ["DELTA_BASE_URL"],
            api_key=os.environ["DELTA_API_KEY"],
            api_secret=os.environ["DELTA_API_SECRET"],
        )
    except KeyError as exc:
        print(f"ERROR: missing credential in environment: {exc}", file=sys.stderr)
        return CONFIG_ERROR

    conn = storage.get_connection(db_path)
    return clear_halt(halt_path, conn, client, args.product_id)


if __name__ == "__main__":
    raise SystemExit(main())
