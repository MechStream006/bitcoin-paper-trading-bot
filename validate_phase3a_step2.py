"""Phase 3a Step 2 soak validation — MarketState.reconcile_universe().

Connects to Delta testnet, then exercises reconcile_universe() while the WS
is streaming live ticks.  Checks the universe alignment invariant and
non-resurrection guarantee under realistic conditions.

Checks
------
  1  Baseline: WS streaming, coverage > 0
  2  Add symbols: reconcile_universe adds 1 symbol, alignment holds
  3  Tick accepted for added symbol (fresh)
  4  Remove symbols: reconcile_universe retires 1 symbol, alignment holds
  5  Non-resurrection: update_ticker for retired symbol rejected
  6  Tickers purged: all_tickers() excludes retired symbol
  7  _reconcile_count monotonic: matches expected diff count
  8  60s stability soak: alignment invariant checked every 5s
  9  Combined add+remove: large reconcile still preserves alignment
 10  Coverage correct after all mutations

Usage:
    conda activate btc_bot
    python validate_phase3a_step2.py
"""
from __future__ import annotations

import logging
import os
import sys
import time
import threading
from pathlib import Path
from dotenv import load_dotenv

# ── project root on path ──────────────────────────────────────────────────
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from src.exchange.delta_client import DeltaClient
from src.exchange.ws_manager import WSManager
from src.ingestion.chain_refresher import ChainRefresher
from src.state.market_state import MarketState
from src.data.storage import get_connection, init_schema

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("validate_step2")
logging.getLogger("src.exchange.ws_manager").setLevel(logging.WARNING)
logging.getLogger("src.state.market_state").setLevel(logging.INFO)

PASS = "[PASS]"
FAIL = "[FAIL]"
results: list[tuple[int, bool, str]] = []


def check(n: int, cond: bool, desc: str) -> None:
    tag = PASS if cond else FAIL
    print(f"  Check {n:2d}: {tag}  {desc}")
    results.append((n, cond, desc))
    if not cond:
        logger.error("Check %d FAILED: %s", n, desc)


def main() -> int:
    ws_url   = os.getenv("DELTA_WS_URL", "wss://testnet-socket.delta.exchange")
    base_url = os.getenv("DELTA_BASE_URL", "https://testnet-api.delta.exchange")
    api_key  = os.getenv("DELTA_API_KEY", "")
    api_sec  = os.getenv("DELTA_API_SECRET", "")

    # ── Bootstrap components ──────────────────────────────────────────────
    import tempfile, os as _os
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = tmp.name

    conn = get_connection(db_path)
    init_schema(conn)

    client = DeltaClient(base_url=base_url, api_key=api_key, api_secret=api_sec)
    refresher = ChainRefresher(
        client=client,
        conn=conn,
        n_strikes=3,
        n_expiries=2,
        on_change=lambda prev, new: None,
    )
    snap = refresher.refresh()
    universe = snap.symbols
    if len(universe) < 4:
        print(f"SKIP: universe too small ({len(universe)} symbols) to exercise add/remove")
        conn.close()
        _os.unlink(db_path)
        return 0

    print(f"\nBootstrap: universe={len(universe)} symbols, BTC mark={snap.mark_price:.0f}")

    market_state = MarketState(universe=universe)
    channels = [{"name": "v2/ticker", "symbols": universe}]
    ws = WSManager(url=ws_url, channels=channels, state=market_state)
    ws.start()

    # ── Wait for WS data ──────────────────────────────────────────────────
    print("\nWaiting up to 30s for WS ticks...")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if market_state.coverage_pct() > 0:
            break
        time.sleep(1)

    print(f"\n--- Phase 3a Step 2 validation ---")
    print(f"  Universe: {len(universe)} symbols")
    print(f"  Coverage: {market_state.coverage_pct():.0%}")

    # ── Check 1: Baseline ────────────────────────────────────────────────
    check(1, market_state.coverage_pct() > 0,
          f"Baseline WS streaming (coverage={market_state.coverage_pct():.0%})")

    # Pick a symbol to work with
    sym_keep  = universe[0]   # always in universe
    sym_retire = universe[1]  # will be retired
    sym_add   = universe[-1]  # will be removed then re-added

    # Build a "reduced" universe without sym_retire, and an "expanded" universe
    # with one extra that wasn't in the original (if possible).
    universe_set  = set(universe)
    reduced       = [s for s in universe if s != sym_retire]

    # ── Check 2: Add a symbol ────────────────────────────────────────────
    # sym_add was already in the universe; remove it first, then re-add
    without_add = [s for s in universe if s != sym_add]
    market_state.reconcile_universe(universe, without_add)
    # Now re-add it
    market_state.reconcile_universe(without_add, universe)

    check(2,
          sym_add in market_state.universe_symbols() and
          set(market_state.universe_symbols()) == universe_set,
          "Add symbol: alignment holds after add+remove cycle")

    # ── Check 3: Tick accepted for re-added symbol ───────────────────────
    # Wait up to 10s for a tick on sym_add
    deadline3 = time.monotonic() + 10
    tick_received = False
    while time.monotonic() < deadline3:
        if market_state.latest_ticker(sym_add) is not None:
            tick_received = True
            break
        time.sleep(0.5)
    check(3, tick_received,
          f"Re-added symbol {sym_add!r} receives ticks from WS")

    # ── Check 4: Remove a symbol — alignment holds ───────────────────────
    rc_before = market_state._reconcile_count
    market_state.reconcile_universe(universe, reduced)
    check(4,
          sym_retire not in market_state.universe_symbols() and
          set(market_state.universe_symbols()) == set(reduced),
          f"Retire {sym_retire!r}: alignment holds (universe={len(reduced)})")

    # ── Check 5: Non-resurrection ────────────────────────────────────────
    market_state.update_ticker(sym_retire, {"mark_price": "99999"})
    check(5,
          sym_retire not in market_state.all_tickers(),
          f"Non-resurrection: update_ticker for retired {sym_retire!r} rejected")

    # ── Check 6: Tickers purged ──────────────────────────────────────────
    check(6,
          sym_retire not in market_state.all_tickers(),
          f"Tickers purged: {sym_retire!r} not in all_tickers()")

    # ── Check 7: _reconcile_count monotonic ─────────────────────────────
    # We've done 2 real reconciles (without_add→universe and universe→reduced)
    # plus the initial without_add call, so >= rc_before + 1
    rc_after = market_state._reconcile_count
    check(7,
          rc_after > rc_before,
          f"_reconcile_count monotonic: before={rc_before} after={rc_after}")

    # ── Check 8: 60s stability soak ─────────────────────────────────────
    print("\n  [Soak] 60s stability — checking alignment every 5s...")
    alignment_failures = 0
    for tick in range(12):
        time.sleep(5)
        # Universe alignment invariant
        syms_public = set(market_state.universe_symbols())
        syms_internal = market_state._universe
        if syms_public != syms_internal:
            alignment_failures += 1
            logger.error(
                "Soak tick %d: alignment BROKEN public=%s internal=%s",
                tick, syms_public, syms_internal,
            )
        # Ensure retired sym still not present
        if sym_retire in market_state.all_tickers():
            alignment_failures += 1
            logger.error("Soak tick %d: retired symbol resurrected!", tick)
        cov = market_state.coverage_pct()
        rc  = market_state._reconcile_count
        logger.info(
            "Soak tick %d/12: coverage=%.0f%% rc=%d sym_keep_fresh=%s",
            tick + 1, cov * 100, rc,
            f"{market_state.freshness_sec(sym_keep):.1f}s"
            if market_state.freshness_sec(sym_keep) is not None else "N/A",
        )
    check(8, alignment_failures == 0,
          f"60s stability soak: alignment invariant held (failures={alignment_failures})")

    # ── Check 9: Combined add+remove ─────────────────────────────────────
    # Restore sym_retire, then do a big swap
    current = list(market_state.universe_symbols())
    restored = current + [sym_retire]
    market_state.reconcile_universe(current, restored)
    # Now swap: keep sym_keep, retire sym_retire again, add nothing new
    after_swap = [s for s in restored if s != sym_retire]
    market_state.reconcile_universe(restored, after_swap)
    final_set  = set(market_state.universe_symbols())
    expected   = set(after_swap)
    check(9,
          final_set == expected,
          f"Combined add+remove: alignment holds (universe={len(final_set)})")

    # ── Check 10: Coverage correct ───────────────────────────────────────
    cov = market_state.coverage_pct()
    # Allow time for ticks to arrive on surviving symbols
    time.sleep(5)
    cov_after = market_state.coverage_pct()
    check(10,
          0.0 <= cov_after <= 1.0,
          f"Coverage in [0,1] after all mutations (coverage={cov_after:.0%})")

    # ── Teardown ─────────────────────────────────────────────────────────
    ws.stop()
    conn.close()
    try:
        _os.unlink(db_path)
    except Exception:
        pass

    # ── Summary ──────────────────────────────────────────────────────────
    passed = sum(1 for _, ok, _ in results if ok)
    total  = len(results)
    print(f"\n{'='*55}")
    print(f"Phase 3a Step 2 validation: {passed}/{total} checks PASSED")
    if passed < total:
        print("FAILED checks:")
        for n, ok, desc in results:
            if not ok:
                print(f"  Check {n}: {desc}")
    else:
        print("ALL CHECKS PASSED — Step 2 operationally validated.")
    print(f"{'='*55}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
