"""Thread-safe shared market state for the BTC bot runtime.

MarketState is the canonical in-memory store for live WS data.  The WS layer
writes via update_ticker / update_funding; the persistence and strategy layers
read via all_tickers / all_funding.  No scheduler, persistence, or strategy
logic lives here.
"""
import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)


class MarketState:
    """Thread-safe store for live ticker and funding-rate payloads.

    Constructed once per runtime with the initial option universe.  The WS
    listener calls update_ticker / update_funding on each received message;
    every other component reads via the snapshot methods.

    The universe is mutable: reconcile_universe() atomically adds new symbols
    and retires removed ones under _lock.  WSManager.resubscribe() must be
    called BEFORE reconcile_universe() so that WS subscription intent and
    MarketState universe remain aligned (Phase 3a design invariant).

    Args:
        universe: Initial option symbols to track.  Funding symbols like
                  BTCUSD are stored unconditionally in funding regardless
                  of this list.

    Lock discipline
    ---------------
    _lock guards _universe, _tickers, _ticker_ts, _funding, _funding_ts,
                 _reconcile_count.
    No I/O, no sleep, no external calls happen while _lock is held.
    update_ticker() checks universe membership INSIDE the lock to prevent
    a TOCTOU race with concurrent reconcile_universe() calls: if the check
    were outside the lock, a retiring reconcile could purge _tickers[sym]
    and then update_ticker() could re-write it, creating a zombie ticker
    entry for a retired symbol.
    """

    def __init__(self, universe: list[str]) -> None:
        if not universe:
            raise ValueError("universe must be non-empty")
        self._universe: set[str] = set(universe)
        self._tickers: dict[str, dict] = {}
        self._funding: dict[str, dict] = {}
        self._ticker_ts: dict[str, float] = {}   # monotonic seconds of last update
        self._funding_ts: dict[str, float] = {}
        self._lock = threading.Lock()
        self._reconcile_count: int = 0            # non-no-op reconcile calls (monotonic)
        logger.debug("[market_state] created with %d universe symbols", len(self._universe))

    # ------------------------------------------------------------------
    # Write path — update_ticker called by WS thread;
    #              reconcile_universe called by scheduler/lifecycle thread
    # ------------------------------------------------------------------

    def update_ticker(self, symbol: str, payload: dict) -> None:
        """Store the latest v2/ticker payload for `symbol`.

        Universe membership is checked INSIDE _lock to prevent a TOCTOU race
        with concurrent reconcile_universe() calls.  If symbol is not in the
        universe, the payload is silently dropped and a WARNING is emitted
        outside the lock.
        """
        now = time.monotonic()
        rejected = False
        with self._lock:
            if symbol not in self._universe:
                rejected = True
            else:
                self._tickers[symbol] = payload
                self._ticker_ts[symbol] = now
        if rejected:
            logger.warning("[market_state] update_ticker: %s not in universe, ignoring", symbol)

    def update_funding(self, symbol: str, payload: dict) -> None:
        """Store the latest funding_rate payload for `symbol` (e.g. BTCUSD)."""
        now = time.monotonic()
        with self._lock:
            self._funding[symbol] = payload
            self._funding_ts[symbol] = now

    # ------------------------------------------------------------------
    # Universe mutation — called by lifecycle._on_universe_change
    # ------------------------------------------------------------------

    def reconcile_universe(self, prev_symbols: list[str], new_symbols: list[str]) -> None:
        """Atomically expand additions and retire removals from the option universe.

        Diff is computed against the *current* _universe, not the caller's
        prev_symbols.  This makes repeated calls with the same new_symbols
        idempotent (no-op if the universe already matches).

        Addition semantics
        ------------------
        Added symbols are inserted into _universe immediately.
          - latest_ticker(sym)  returns None (no data yet)
          - freshness_sec(sym)  returns None (no data yet)
          - stale_symbols(any)  includes them immediately (age ≈ epoch)
          - coverage_pct()      drops proportionally

        Retirement semantics
        --------------------
        Retired symbols are removed from _universe and purged from _tickers /
        _ticker_ts atomically under _lock.  After return:
          - universe_symbols()   excludes them
          - all_tickers()        excludes them
          - update_ticker()      rejects future ticks (no hidden resurrection)
          - stale_symbols()      does not include them (not in _universe)
          - freshness_sec(sym)   returns None

        Funding state (_funding, _funding_ts) is keyed by perpetual symbols
        (e.g. BTCUSD), not option symbols.  It is never touched here.

        Dependency direction
        --------------------
        MarketState never writes to the DB or calls external components.
        The retirement log is observability-only.

        Args:
            prev_symbols: Prior symbol list — used only for logging context.
                          The actual diff is against the current _universe.
            new_symbols:  Desired new universe.  Must be non-empty.
        """
        if not new_symbols:
            logger.error(
                "[market_state] reconcile_universe: new_symbols is empty — "
                "refusing (universe must remain non-empty)"
            )
            return

        new_set = set(new_symbols)
        retired_info: list = []   # [(sym, last_payload_or_None, last_ts_or_None)]

        with self._lock:
            old_set = set(self._universe)
            added   = new_set - old_set
            removed = old_set - new_set

            if added or removed:
                self._universe = new_set
                for sym in removed:
                    last_payload = self._tickers.pop(sym, None)
                    last_ts      = self._ticker_ts.pop(sym, None)
                    retired_info.append((sym, last_payload, last_ts))
                self._reconcile_count += 1

            has_diff = bool(added or removed)
            old_size = len(old_set)
            new_size = len(new_set)

        if not has_diff:
            logger.debug(
                "[market_state] reconcile_universe: no diff — no-op (universe=%d)", old_size
            )
            return

        # All logging outside the lock (I/O discipline)
        now_mono = time.monotonic()
        for sym, last_payload, last_ts in retired_info:
            if last_payload is not None and last_ts is not None:
                age_sec   = now_mono - last_ts
                last_mark = last_payload.get("mark_price", "?")
                logger.info(
                    "[market_state] retired: sym=%s last_mark=%s age_sec=%.1f",
                    sym, last_mark, age_sec,
                )
            else:
                logger.info(
                    "[market_state] retired: sym=%s (no tick received before retirement)", sym
                )

        logger.info(
            "[market_state] reconcile: +%d -%d (universe %d -> %d)",
            len(added), len(removed), old_size, new_size,
        )
        if added:
            logger.info("[market_state] reconcile adding:   %s", sorted(added))
        if removed:
            logger.info("[market_state] reconcile removing: %s", sorted(removed))

    # ------------------------------------------------------------------
    # Read path (called by main / persistence / strategy threads)
    # ------------------------------------------------------------------

    def latest_ticker(self, symbol: str) -> Optional[dict]:
        with self._lock:
            return self._tickers.get(symbol)

    def latest_funding(self, symbol: str) -> Optional[dict]:
        with self._lock:
            return self._funding.get(symbol)

    def all_tickers(self) -> dict[str, dict]:
        """Shallow copy of all ticker payloads keyed by symbol."""
        with self._lock:
            return dict(self._tickers)

    def all_funding(self) -> dict[str, dict]:
        """Shallow copy of all funding payloads keyed by symbol."""
        with self._lock:
            return dict(self._funding)

    def universe_symbols(self) -> list[str]:
        """Returns a sorted copy of the current universe symbol list."""
        with self._lock:
            return sorted(self._universe)

    def universe_size(self) -> int:
        """Return the current universe size without allocating a list."""
        with self._lock:
            return len(self._universe)

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def freshness_sec(self, symbol: str) -> Optional[float]:
        """Seconds since the ticker for `symbol` was last updated.

        Returns None if the symbol has never been seen, was retired, or
        was added to the universe but has not yet received its first tick.
        """
        with self._lock:
            ts = self._ticker_ts.get(symbol)
        if ts is None:
            return None
        return time.monotonic() - ts

    def coverage_pct(self) -> float:
        """Fraction of universe symbols that have received at least one ticker update."""
        with self._lock:
            n = len(self._universe)
            if n == 0:
                return 0.0
            covered = sum(1 for s in self._universe if s in self._tickers)
        return covered / n

    def stale_symbols(self, threshold_sec: float) -> list[str]:
        """Universe symbols whose last ticker update is older than `threshold_sec`.

        Newly added symbols with no data appear here immediately because
        _ticker_ts.get(sym, 0.0) = 0.0 (epoch), giving an age of ~1.7 billion
        seconds.  This is expected: they need a tick before they are fresh.
        Used by health monitoring to detect WS data gaps.
        """
        now = time.monotonic()
        with self._lock:
            return [
                s for s in self._universe
                if now - self._ticker_ts.get(s, 0.0) > threshold_sec
            ]

    def __repr__(self) -> str:
        with self._lock:
            n_universe = len(self._universe)
            n_tickers  = len(self._tickers)
            n_funding  = len(self._funding)
            covered    = sum(1 for s in self._universe if s in self._tickers)
        cov = covered / n_universe if n_universe > 0 else 0.0
        return (
            f"MarketState(universe={n_universe}, "
            f"tickers={n_tickers}, funding={n_funding}, "
            f"coverage={cov:.1%})"
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")

    syms = ["C-BTC-95000-280526", "P-BTC-95000-280526", "C-BTC-96000-280526"]
    ms = MarketState(universe=syms)
    print(ms)

    ms.update_ticker("C-BTC-95000-280526", {"mark_price": "95100.5", "greeks": {"delta": "0.52"}})
    ms.update_ticker("P-BTC-95000-280526", {"mark_price": "400.0"})
    ms.update_funding("BTCUSD", {"funding_rate": "0.0001"})

    print(f"coverage      : {ms.coverage_pct():.1%}")
    fresh = ms.freshness_sec("C-BTC-95000-280526")
    print(f"freshness (C) : {fresh:.4f}s")
    print(f"latest  (C)   : {ms.latest_ticker('C-BTC-95000-280526')}")
    print(f"stale >0s     : {ms.stale_symbols(0)}")
    print(f"universe_size : {ms.universe_size()}")
    print(ms)

    ms.update_ticker("INVALID-SYM", {"mark_price": "0"})
    assert "INVALID-SYM" not in ms.all_tickers(), "non-universe symbol must be rejected"
    print("Non-universe symbol correctly rejected.")

    # --- reconcile_universe tests ---

    # Remove P-BTC-95000-280526; add C-BTC-97000-280526
    ms.reconcile_universe(
        syms,
        ["C-BTC-95000-280526", "C-BTC-96000-280526", "C-BTC-97000-280526"],
    )
    assert "P-BTC-95000-280526" not in ms.universe_symbols(), "retired sym in universe"
    assert "P-BTC-95000-280526" not in ms.all_tickers(), "retired sym in tickers"
    assert ms.freshness_sec("P-BTC-95000-280526") is None, "retired sym has freshness"
    assert "C-BTC-97000-280526" in ms.universe_symbols(), "added sym not in universe"
    assert ms.latest_ticker("C-BTC-97000-280526") is None, "added sym has phantom data"
    print("reconcile: remove + add semantics verified.")

    # Non-resurrection: ticks for retired sym must be rejected
    ms.update_ticker("P-BTC-95000-280526", {"mark_price": "999"})
    assert "P-BTC-95000-280526" not in ms.all_tickers(), "retired sym resurrected!"
    print("Non-resurrection: retired symbol tick correctly rejected.")

    # Idempotency
    rc_before = ms._reconcile_count
    ms.reconcile_universe(
        ["C-BTC-95000-280526", "C-BTC-96000-280526", "C-BTC-97000-280526"],
        ["C-BTC-95000-280526", "C-BTC-96000-280526", "C-BTC-97000-280526"],
    )
    assert ms._reconcile_count == rc_before, "no-op incremented reconcile_count"
    print("Idempotency: no-op reconcile_count stable.")

    # Alignment invariant check
    assert set(ms.universe_symbols()) == ms._universe, "universe_symbols out of sync"
    print("Alignment: universe_symbols() matches internal set.")

    print("\nMarketState reconcile smoke test passed.")
