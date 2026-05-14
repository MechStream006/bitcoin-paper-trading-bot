"""ChainRefresher — supervised universe-refresh component for the BTC bot runtime.

Responsibilities
----------------
- Fetch live BTC option chain from Delta REST API (via refresh_chain).
- Upsert instruments into the DB.
- Recompute the selected universe (ATM +/- n_strikes, next n_expiries).
- Store the result as an immutable last-known-good RefreshSnapshot.
- Log and emit on_change callbacks when the symbol set evolves.
- Report health and expose the current universe for other components.

What ChainRefresher does NOT do
---------------------------------
- Does not own WS subscription logic.  The Runtime wires on_change to WSManager.
- Does not mutate MarketState.  MarketState continues receiving WS ticks unchanged.
- Does not own the scheduler.  The SchedulerService calls refresh() on a cadence.
- Does not write option_quotes or funding_snapshots (those are SnapshotWriter's domain).
- Does not send Telegram alerts.

Failure contract
----------------
On any refresh() exception the last-known-good RefreshSnapshot is preserved
unchanged.  The failure counter increments.  ComponentFailure is raised only
after failure_threshold consecutive full-cycle failures.

Dependency direction
--------------------
  ingestion.chain_refresher
    -> exchange.instruments        (refresh_chain, get_btc_mark_price, select_universe)
    -> runtime.health              (produces HealthStatus)
    -> runtime.errors              (raises ComponentFailure on escalation)

Concurrency contract
--------------------
  refresh() is called from a single scheduler thread.
  health() / universe_symbols() / current_snapshot() may be called from any thread.
  _lock guards _snapshot and _consecutive_failures only.
  REST calls and DB writes happen entirely outside _lock.
  No lock held across network I/O.
"""
from __future__ import annotations

import dataclasses
import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from src.exchange.delta_client import DeltaClient
from src.exchange.instruments import (
    get_btc_mark_price,
    refresh_chain,
    select_universe,
)
from src.runtime.errors import ComponentFailure
from src.runtime.health import HealthStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Refresh snapshot
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class RefreshSnapshot:
    """Immutable point-in-time record of a successful chain refresh.

    Treat fields as read-only after construction.  ChainRefresher replaces
    the whole snapshot object on each successful refresh; it never mutates
    an existing one.

    Attributes:
        symbols:       Ordered list of option symbols in the selected universe.
        universe:      Full instrument dicts returned by select_universe()
                       (id, symbol, contract_type, strike_price, settlement_time,
                        atm_distance).
        mark_price:    BTC perpetual mark price at refresh time.
        n_chain:       Total live BTC option instruments upserted to the DB.
        duration_sec:  Wall-clock time the refresh took (REST + DB).
        refreshed_at:  time.monotonic() when this snapshot was captured.
    """
    symbols: list[str]
    universe: list[dict]
    mark_price: float
    n_chain: int
    duration_sec: float
    refreshed_at: float


# ---------------------------------------------------------------------------
# ChainRefresher
# ---------------------------------------------------------------------------

class ChainRefresher:
    """Supervised component that refreshes the BTC option universe on a cadence.

    Usage (wired by Runtime in Step 9)
    -----------------------------------
      refresher = ChainRefresher(client, conn, on_change=ws_manager.resubscribe)
      scheduler.register("chain_refresh", refresher.refresh, "interval", hours=1)
      refresher.start()
      ...
      symbols = refresher.universe_symbols()   # passed to WSManager at boot

    Args:
        client:            DeltaClient instance (REST only, no WS).
        conn:              SQLite connection with schema initialised.
        n_strikes:         Strikes above and below ATM to include per expiry.
        n_expiries:        Number of upcoming expiries to cover.
        failure_threshold: Consecutive refresh failures before ComponentFailure.
        on_change:         Optional callback (prev_symbols, new_symbols) called
                           when the selected universe changes between two
                           successful refreshes.  Called outside _lock.
    """

    COMPONENT_NAME = "chain_refresher"

    def __init__(
        self,
        client: DeltaClient,
        conn: sqlite3.Connection,
        n_strikes: int = 5,
        n_expiries: int = 3,
        failure_threshold: int = 3,
        on_change: Optional[Callable[[list[str], list[str]], None]] = None,
    ) -> None:
        self._client = client
        self._conn = conn
        self._n_strikes = n_strikes
        self._n_expiries = n_expiries
        self._failure_threshold = failure_threshold
        self._on_change = on_change

        # Guarded by _lock: snapshot and failure counter only.
        # No DB or network operations happen while _lock is held.
        self._lock = threading.Lock()
        self._snapshot: Optional[RefreshSnapshot] = None
        self._consecutive_failures: int = 0

        self._started: bool = False

    # -----------------------------------------------------------------------
    # Lifecycle (Component contract)
    # -----------------------------------------------------------------------

    def start(self) -> None:
        """Mark component as active.  Does NOT trigger an immediate refresh."""
        if self._started:
            logger.warning("[chain_refresher] start() called twice — ignoring")
            return
        self._started = True
        logger.info(
            "[chain_refresher] ready (n_strikes=%d n_expiries=%d threshold=%d)",
            self._n_strikes, self._n_expiries, self._failure_threshold,
        )

    def stop(self) -> None:
        """Mark component as inactive."""
        self._started = False
        logger.info("[chain_refresher] stopped")

    # -----------------------------------------------------------------------
    # Health reporting  (safe from any thread)
    # -----------------------------------------------------------------------

    def health(self) -> HealthStatus:
        """Return a HealthStatus snapshot.

        healthy=True  when consecutive_failures == 0 (including before first refresh).
        fatal=False   always — the Supervisor decides escalation policy.
        """
        with self._lock:
            snap = self._snapshot
            cf = self._consecutive_failures

        now = time.monotonic()
        if snap is not None:
            age_str = f"{now - snap.refreshed_at:.0f}s"
            universe_size = len(snap.symbols)
            dur_str = f"{snap.duration_sec:.2f}s"
        else:
            age_str = "never"
            universe_size = 0
            dur_str = "n/a"

        details = (
            f"last_refresh={age_str} universe={universe_size} "
            f"consec_failures={cf} last_duration={dur_str}"
        )
        return HealthStatus(
            name=self.COMPONENT_NAME,
            healthy=cf == 0,
            fatal=False,
            details=details,
            last_check_at=datetime.now(timezone.utc),
        )

    # -----------------------------------------------------------------------
    # Universe accessors  (safe from any thread)
    # -----------------------------------------------------------------------

    def current_snapshot(self) -> Optional[RefreshSnapshot]:
        """Return the most recent successful RefreshSnapshot, or None."""
        with self._lock:
            return self._snapshot

    def universe_symbols(self) -> list[str]:
        """Current universe symbol list.  Returns [] if no refresh has succeeded."""
        with self._lock:
            snap = self._snapshot
        return list(snap.symbols) if snap is not None else []

    # -----------------------------------------------------------------------
    # Refresh  (called by SchedulerService; must not be called concurrently)
    # -----------------------------------------------------------------------

    def refresh(self) -> RefreshSnapshot:
        """Fetch the chain, upsert instruments, recompute universe.

        Phases
        ------
        1. Capture previous snapshot reference under _lock (released immediately).
        2. REST: refresh_chain() — network call, no lock held.
        3. REST: get_btc_mark_price() — network call, no lock held.
        4. DB: select_universe() — read-only query, no lock held.
        5. Compute diff vs previous symbols; log additions/removals.
        6. Under _lock: atomically replace _snapshot, reset consecutive_failures.
        7. Outside _lock: emit on_change callback if symbols changed.

        Failure path
        ------------
        Any exception leaves _snapshot unchanged (last-known-good preserved).
        consecutive_failures increments; ComponentFailure raised at threshold.
        Below threshold the original exception is re-raised.

        Raises
        ------
        ComponentFailure  after failure_threshold consecutive full-cycle failures.
        Exception         the raw exception on failures below the threshold.
        """
        t0 = time.monotonic()

        # Phase 1: snapshot prev reference while lock is briefly held.
        # REST calls below must NOT hold _lock — they can take several seconds.
        with self._lock:
            prev_snap = self._snapshot
        prev_syms = set(prev_snap.symbols) if prev_snap is not None else set()

        try:
            # Phase 2 + 3: REST calls — no lock held
            n_chain = refresh_chain(self._client, self._conn)
            mark = get_btc_mark_price(self._client)

            # Phase 4: DB read — no lock held
            universe = select_universe(
                self._conn, mark,
                n_strikes=self._n_strikes,
                n_expiries=self._n_expiries,
            )

            duration = time.monotonic() - t0
            symbols = [u["symbol"] for u in universe]

            snapshot = RefreshSnapshot(
                symbols=symbols,
                universe=universe,
                mark_price=mark,
                n_chain=n_chain,
                duration_sec=duration,
                refreshed_at=time.monotonic(),
            )

            # Phase 5: compute and log universe diff
            new_syms = set(symbols)
            added = new_syms - prev_syms
            removed = prev_syms - new_syms
            _log_universe_diff(added, removed, is_first=(prev_snap is None))

            # Phase 6: atomically update snapshot and reset failure counter
            with self._lock:
                self._snapshot = snapshot
                self._consecutive_failures = 0

            logger.info(
                "[chain_refresher] ok: mark=%.0f universe=%d chain=%d in %.2fs",
                mark, len(symbols), n_chain, duration,
            )

            # Phase 7: emit on_change OUTSIDE lock.
            # Only fire after the first successful refresh (prev_snap is not None)
            # and only when symbols actually changed.
            if (added or removed) and self._on_change is not None and prev_snap is not None:
                try:
                    self._on_change(prev_snap.symbols, snapshot.symbols)
                except Exception as cb_exc:
                    logger.warning(
                        "[chain_refresher] on_change callback raised %s: %s",
                        type(cb_exc).__name__, cb_exc,
                    )

            return snapshot

        except ComponentFailure:
            raise  # already structured — let it propagate as-is

        except Exception as exc:
            duration = time.monotonic() - t0
            with self._lock:
                self._consecutive_failures += 1
                cf = self._consecutive_failures
            logger.error(
                "[chain_refresher] FAILED in %.2fs (consecutive=%d threshold=%d): %s: %s",
                duration, cf, self._failure_threshold, type(exc).__name__, exc,
            )
            if cf >= self._failure_threshold:
                raise ComponentFailure(
                    f"chain refresh failed {cf} consecutive times: "
                    f"{type(exc).__name__}: {exc}",
                    component=self.COMPONENT_NAME,
                ) from exc
            raise  # below threshold: re-raise original so Scheduler can log it


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _log_universe_diff(
    added: set[str],
    removed: set[str],
    *,
    is_first: bool,
) -> None:
    """Emit structured logs describing universe additions/removals."""
    if is_first:
        return  # nothing to diff against on initial refresh
    if not added and not removed:
        return  # no change
    logger.info(
        "[chain_refresher] universe changed: +%d added, -%d removed",
        len(added), len(removed),
    )
    if added:
        logger.info("[chain_refresher]   added:   %s", sorted(added))
    if removed:
        logger.info("[chain_refresher]   removed: %s", sorted(removed))


# ---------------------------------------------------------------------------
# Self-test / smoke  (mock client — no live network)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from src.data.storage import init_schema

    # -----------------------------------------------------------------------
    # Mock client factory
    # -----------------------------------------------------------------------

    def _make_products(strikes: list[float], settlement: str) -> list[dict]:
        """Build minimal product dicts understood by upsert_instrument + select_universe."""
        products = []
        for i, strike in enumerate(strikes):
            for side, ctype in [("C", "call_options"), ("P", "put_options")]:
                products.append({
                    "id": len(products) + 1,
                    "symbol": f"{side}-BTC-{int(strike)}-280526",
                    "contract_type": ctype,
                    "underlying_asset": {"symbol": "BTC"},
                    "quoting_asset": {"symbol": "USDT"},
                    "strike_price": str(strike),
                    "settlement_time": settlement,
                    "tick_size": "0.01",
                    "contract_value": "0.001",
                    "state": "live",
                })
        return products

    class MockClient:
        def __init__(self, products, mark):
            self._products = products
            self._mark = mark

        def get_products(self, **_):
            return {"result": self._products}

        def get_ticker(self, symbol):
            return {"result": {"mark_price": str(self._mark)}}

    class FailingClient:
        def get_products(self, **_):
            raise ConnectionError("simulated REST failure")

        def get_ticker(self, _symbol):
            raise ConnectionError("simulated REST failure")

    # -- Setup in-memory DB --------------------------------------------------
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    init_schema(conn)

    strikes = [93000.0, 94000.0, 95000.0, 96000.0, 97000.0]
    settlement = "2099-12-31T12:00:00+00:00"  # safely in the future
    products_v1 = _make_products(strikes, settlement)
    mock_client = MockClient(products_v1, mark=95000.0)

    # -----------------------------------------------------------------------
    # Test 1: first successful refresh
    # -----------------------------------------------------------------------
    changes = []

    def on_change(prev, new):
        changes.append((list(prev), list(new)))

    # n_strikes=3: with 5 v1 strikes, selects all 5 (ATM at centre).
    # With 7 v2 strikes, selects 6 (adds the one strike just above ATM+2).
    # This ensures on_change fires when v2 products are loaded.
    refresher = ChainRefresher(
        mock_client, conn,
        n_strikes=3, n_expiries=1,
        failure_threshold=3,
        on_change=on_change,
    )
    refresher.start()

    snap1 = refresher.refresh()
    print(f"[t1] mark={snap1.mark_price} universe={len(snap1.symbols)} chain={snap1.n_chain}")
    assert snap1.n_chain == len(products_v1), f"expected {len(products_v1)}, got {snap1.n_chain}"
    assert len(snap1.symbols) > 0, "universe must be non-empty"
    assert snap1.mark_price == 95000.0

    h = refresher.health()
    print(f"[t1] health: {h}")
    assert h.healthy, f"expected healthy: {h}"
    assert refresher.universe_symbols() == snap1.symbols
    print("[t1] first refresh passed.")

    # -----------------------------------------------------------------------
    # Test 2: on_change fires when universe changes
    # -----------------------------------------------------------------------
    # Add two new strikes, remove none — on_change should fire
    new_strikes = [93000.0, 94000.0, 95000.0, 96000.0, 97000.0, 98000.0, 99000.0]
    products_v2 = _make_products(new_strikes, settlement)
    mock_client._products = products_v2
    mock_client._mark = 95000.0

    snap2 = refresher.refresh()
    print(f"[t2] universe grew to {len(snap2.symbols)} symbols")
    assert len(changes) == 1, f"expected 1 on_change call, got {len(changes)}"
    prev_syms, new_syms = changes[0]
    added_syms = set(new_syms) - set(prev_syms)
    assert len(added_syms) > 0, "should have added symbols"
    print(f"[t2] on_change fired: +{len(added_syms)} symbols added")
    print("[t2] universe-change callback test passed.")

    # -----------------------------------------------------------------------
    # Test 3: failure preserves last-known-good snapshot
    # -----------------------------------------------------------------------
    bad_refresher = ChainRefresher(
        mock_client, conn,
        n_strikes=2, n_expiries=1,
        failure_threshold=3,
    )
    _ = bad_refresher.refresh()   # seed with a good snapshot
    good_snap = bad_refresher.current_snapshot()
    assert good_snap is not None

    # Now inject a failing client
    bad_refresher._client = FailingClient()

    for attempt in range(1, 3):  # below threshold — original exc re-raised
        try:
            bad_refresher.refresh()
            assert False, "expected exception"
        except ComponentFailure:
            assert False, f"should not raise ComponentFailure before threshold (attempt={attempt})"
        except Exception as exc:
            with bad_refresher._lock:
                cf = bad_refresher._consecutive_failures
            assert cf == attempt, f"cf={cf}"
            # Last-known-good must be unchanged
            current = bad_refresher.current_snapshot()
            assert current is good_snap, "snapshot must not change on failure"
            print(f"[t3] attempt {attempt}: {type(exc).__name__} raised, snapshot preserved")

    # At threshold (attempt 3) → ComponentFailure
    try:
        bad_refresher.refresh()
        assert False, "expected ComponentFailure"
    except ComponentFailure as exc:
        print(f"[t3] ComponentFailure at threshold: {exc}")
        assert exc.component == ChainRefresher.COMPONENT_NAME
        current = bad_refresher.current_snapshot()
        assert current is good_snap, "snapshot must not change even at threshold"
    print("[t3] failure preservation test passed.")

    # -----------------------------------------------------------------------
    # Test 4: health reflects consecutive_failures correctly
    # -----------------------------------------------------------------------
    h_bad = bad_refresher.health()
    print(f"[t4] health after failures: {h_bad}")
    assert not h_bad.healthy, f"expected unhealthy: {h_bad}"
    assert h_bad.fatal is False
    print("[t4] health degradation test passed.")

    refresher.stop()
    bad_refresher.stop()
    print("\nChainRefresher smoke test passed.")
