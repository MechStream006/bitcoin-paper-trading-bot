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

    Constructed once per runtime with the option universe.  The WS listener
    calls update_ticker / update_funding on each received message; every other
    component reads via the snapshot methods.

    Args:
        universe: symbols the bot is tracking (option symbols only; funding
                  symbols like BTCUSDT are stored unconditionally in funding).
    """

    def __init__(self, universe: list[str]) -> None:
        if not universe:
            raise ValueError("universe must be non-empty")
        self._universe: list[str] = list(universe)
        self._tickers: dict[str, dict] = {}
        self._funding: dict[str, dict] = {}
        self._ticker_ts: dict[str, float] = {}   # monotonic seconds of last update
        self._funding_ts: dict[str, float] = {}
        self._lock = threading.Lock()
        logger.debug("MarketState created with %d universe symbols", len(self._universe))

    # ------------------------------------------------------------------
    # Write path (called by WS thread only)
    # ------------------------------------------------------------------

    def update_ticker(self, symbol: str, payload: dict) -> None:
        """Store the latest v2/ticker payload for `symbol`.

        Silently ignores symbols outside the universe — the WS subscription
        should match the universe exactly, so this is a defensive guard only.
        """
        if symbol not in self._universe:
            logger.warning("update_ticker: %s not in universe, ignoring", symbol)
            return
        now = time.monotonic()
        with self._lock:
            self._tickers[symbol] = payload
            self._ticker_ts[symbol] = now

    def update_funding(self, symbol: str, payload: dict) -> None:
        """Store the latest funding_rate payload for `symbol` (e.g. BTCUSDT)."""
        now = time.monotonic()
        with self._lock:
            self._funding[symbol] = payload
            self._funding_ts[symbol] = now

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
        """Returns a copy of the universe symbol list (immutable after init)."""
        return list(self._universe)

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def freshness_sec(self, symbol: str) -> Optional[float]:
        """Seconds since the ticker for `symbol` was last updated.

        Returns None if the symbol has never been seen.
        """
        with self._lock:
            ts = self._ticker_ts.get(symbol)
        if ts is None:
            return None
        return time.monotonic() - ts

    def coverage_pct(self) -> float:
        """Fraction of universe symbols that have received at least one ticker update."""
        if not self._universe:
            return 0.0
        with self._lock:
            covered = sum(1 for s in self._universe if s in self._tickers)
        return covered / len(self._universe)

    def stale_symbols(self, threshold_sec: float) -> list[str]:
        """Universe symbols whose last ticker update is older than `threshold_sec`.

        Used by the health checker to detect WS data gaps.
        """
        now = time.monotonic()
        with self._lock:
            return [
                s for s in self._universe
                if now - self._ticker_ts.get(s, 0.0) > threshold_sec
            ]

    def __repr__(self) -> str:
        with self._lock:
            n_tickers = len(self._tickers)
            n_funding = len(self._funding)
        return (
            f"MarketState(universe={len(self._universe)}, "
            f"tickers={n_tickers}, funding={n_funding}, "
            f"coverage={self.coverage_pct():.1%})"
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")

    syms = ["C-BTC-95000-280526", "P-BTC-95000-280526", "C-BTC-96000-280526"]
    ms = MarketState(universe=syms)
    print(ms)

    ms.update_ticker("C-BTC-95000-280526", {"mark_price": "95100.5", "greeks": {"delta": "0.52"}})
    ms.update_ticker("P-BTC-95000-280526", {"mark_price": "400.0"})
    ms.update_funding("BTCUSDT", {"funding_rate": "0.0001"})

    print(f"coverage      : {ms.coverage_pct():.1%}")
    fresh = ms.freshness_sec("C-BTC-95000-280526")
    print(f"freshness (C) : {fresh:.4f}s")
    print(f"latest  (C)   : {ms.latest_ticker('C-BTC-95000-280526')}")
    print(f"stale >0s     : {ms.stale_symbols(0)}")
    print(ms)

    ms.update_ticker("INVALID-SYM", {"mark_price": "0"})
    assert "INVALID-SYM" not in ms.all_tickers(), "non-universe symbol must be rejected"
    print("Non-universe symbol correctly rejected.")
