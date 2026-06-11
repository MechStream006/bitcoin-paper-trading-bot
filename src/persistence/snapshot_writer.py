"""SnapshotWriter — periodic persistence component for the BTC bot runtime.

Responsibilities
----------------
- Pull immutable data snapshots from MarketState.
- Write option_quotes and funding_snapshots rows to SQLite.
- Track per-cycle metrics and expose health.
- Escalate via ComponentFailure after repeated full-cycle failures.

What SnapshotWriter does NOT do
--------------------------------
- It does not own a background thread (the Scheduler calls snapshot()).
- It does not register with the Scheduler itself.
- It does not write to Telegram.
- It does not read from the WebSocket directly.
- It does not manage the instrument chain (it reads instrument IDs from the DB
  on each snapshot so it is always consistent with ChainRefresher updates).

Dependency direction
--------------------
  persistence.snapshot_writer
    -> data.storage          (write_option_quote, write_funding_snapshot)
    -> state.market_state    (reads live data snapshots)
    -> runtime.health        (produces HealthStatus)
    -> runtime.errors        (raises ComponentFailure on escalation)

Concurrency contract
--------------------
  snapshot() is called from a single scheduler thread.
  health() may be called from any thread at any time.
  _lock guards only the metrics fields; it is never held during DB writes
  or MarketState reads.  No lock-inversion risk with MarketState._lock.
"""
from __future__ import annotations

import dataclasses
import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from src.data.storage import write_funding_snapshot, write_option_quote
from src.runtime.errors import ComponentFailure
from src.runtime.health import HealthStatus
from src.state.market_state import MarketState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class SnapshotResult:
    """Outcome metrics for one snapshot() invocation.

    All counts refer to the single snapshot cycle that produced this result.
    """
    ts: str                # ISO-8601 UTC timestamp written into each row
    wrote_quotes: int      # option_quotes rows successfully written
    wrote_funding: int     # funding_snapshots rows successfully written
    skipped_quotes: int    # symbols in MarketState but absent from instruments table
    failed_quotes: int     # rows attempted but raised an exception
    failed_funding: int    # funding rows attempted but raised an exception

    def __str__(self) -> str:
        total_q = self.wrote_quotes + self.skipped_quotes + self.failed_quotes
        total_f = self.wrote_funding + self.failed_funding
        return (
            f"ts={self.ts[:19]}Z "
            f"quotes={self.wrote_quotes}/{total_q} "
            f"(skip={self.skipped_quotes} fail={self.failed_quotes}) "
            f"funding={self.wrote_funding}/{total_f}"
        )


# ---------------------------------------------------------------------------
# SnapshotWriter
# ---------------------------------------------------------------------------

class SnapshotWriter:
    """Snapshot-oriented persistence boundary between MarketState and SQLite.

    The Scheduler calls snapshot() on a fixed cadence (e.g. every 60 s).
    SnapshotWriter has no timer or thread of its own.

    Args:
        conn:              SQLite connection (must have schema initialised).
        state:             Shared MarketState; read via all_tickers/all_funding.
        failure_threshold: Consecutive full-cycle failures before ComponentFailure
                           is raised (defaults to 3).
    """

    COMPONENT_NAME = "snapshot_writer"

    def __init__(
        self,
        conn: sqlite3.Connection,
        state: MarketState,
        failure_threshold: int = 3,
    ) -> None:
        self._conn = conn
        self._state = state
        self._failure_threshold = failure_threshold

        # Metrics — guarded by _lock; written by scheduler thread, read by any.
        self._lock = threading.Lock()
        self._consecutive_failures: int = 0
        self._total_rows_written: int = 0
        self._last_snapshot_at: Optional[float] = None
        self._last_snapshot_rows: int = 0
        self._started: bool = False

    # -----------------------------------------------------------------------
    # Lifecycle (Component contract — lightweight, no background thread)
    # -----------------------------------------------------------------------

    def start(self) -> None:
        """Mark the component as active.  Idempotent."""
        if self._started:
            logger.warning("[snapshot_writer] start() called twice — ignoring")
            return
        self._started = True
        logger.info(
            "[snapshot_writer] ready (scheduler-driven; no background thread)"
        )

    def stop(self) -> None:
        """Mark the component as inactive."""
        self._started = False
        logger.info("[snapshot_writer] stopped")

    # -----------------------------------------------------------------------
    # Health reporting (safe to call from any thread)
    # -----------------------------------------------------------------------

    def health(self) -> HealthStatus:
        """Return a point-in-time HealthStatus.

        healthy=True  when consecutive_failures == 0 (including before first snapshot).
        fatal=False   always — the Supervisor decides escalation policy.
        """
        with self._lock:
            last_snap = self._last_snapshot_at
            total_rows = self._total_rows_written
            last_rows = self._last_snapshot_rows
            cf = self._consecutive_failures

        age_str = f"{time.time() - last_snap:.0f}s" if last_snap is not None else "never"
        healthy = cf == 0
        details = (
            f"last_snap={age_str} total_rows={total_rows} "
            f"last_rows={last_rows} consec_failures={cf}"
        )
        return HealthStatus(
            name=self.COMPONENT_NAME,
            healthy=healthy,
            fatal=False,
            details=details,
            last_check_at=datetime.now(timezone.utc),
        )

    # -----------------------------------------------------------------------
    # Snapshot execution
    # -----------------------------------------------------------------------

    def snapshot(self) -> SnapshotResult:
        """Pull data from MarketState and persist to SQLite in one transaction.

        Phases
        ------
        1. Capture timestamp (once for the whole batch).
        2. Pull immutable copies from MarketState (lock released immediately).
        3. Load instrument_id map from DB (single SELECT — always fresh).
        4. Write option_quotes rows — per-row exceptions are logged and skipped;
           the rest of the batch continues.
        5. Write funding_snapshots rows — same per-row isolation.
        6. COMMIT.  On commit failure: ROLLBACK, record failure, re-raise
           (or raise ComponentFailure after threshold).
        7. Update metrics under _lock on success.

        DB writes happen entirely OUTSIDE any MarketState lock.
        _lock is never held during DB operations or MarketState reads.

        Raises
        ------
        ComponentFailure  — after `failure_threshold` consecutive full-cycle
                            failures (commit failure or instrument-map read fail).
        Exception         — the raw exception on failures below the threshold.
        """
        # ------------------------------------------------------------------
        # Phase 1: capture timestamp once for the entire batch
        # ------------------------------------------------------------------
        ts = datetime.now(timezone.utc).isoformat()

        # ------------------------------------------------------------------
        # Phase 2: pull immutable data copies from MarketState.
        #          all_tickers / all_funding acquire MarketState._lock internally
        #          and return a shallow copy — lock is released before we return.
        #          No MarketState lock is held for any subsequent operation.
        # ------------------------------------------------------------------
        tickers = self._state.all_tickers()
        fundings = self._state.all_funding()

        # ------------------------------------------------------------------
        # Phase 3: load current instrument symbol->id map from DB.
        #          Single SELECT covers any chain refreshes since last snapshot.
        # ------------------------------------------------------------------
        try:
            instrument_map = self._load_instrument_map()
        except Exception as exc:
            self._record_failure(exc, context="load instrument map")
            raise  # only reached if below failure_threshold

        # ------------------------------------------------------------------
        # Phase 4: write option_quotes — one row per symbol.
        #          Per-row failures are isolated: log, skip, continue.
        #          No rollback on row failure; commit may still succeed for
        #          rows that did write.
        # ------------------------------------------------------------------
        wrote_q, skipped_q, failed_q = 0, 0, 0

        for sym, msg in tickers.items():
            iid = instrument_map.get(sym)
            if iid is None:
                skipped_q += 1
                logger.debug(
                    "[snapshot_writer] skipping %s — not in instruments table", sym
                )
                continue
            try:
                write_option_quote(self._conn, iid, ts, msg)
                wrote_q += 1
            except Exception as exc:
                failed_q += 1
                logger.warning(
                    "[snapshot_writer] option_quote write failed for %s: %s: %s",
                    sym, type(exc).__name__, exc,
                )

        # ------------------------------------------------------------------
        # Phase 5: write funding_snapshots
        # ------------------------------------------------------------------
        wrote_f, failed_f = 0, 0

        for sym, msg in fundings.items():
            try:
                write_funding_snapshot(self._conn, sym, ts, msg)
                wrote_f += 1
            except Exception as exc:
                failed_f += 1
                logger.warning(
                    "[snapshot_writer] funding_snapshot write failed for %s: %s: %s",
                    sym, type(exc).__name__, exc,
                )

        # ------------------------------------------------------------------
        # Phase 6: COMMIT — all-or-nothing for the rows that reached here.
        #          On failure: ROLLBACK to prevent partial-commit corruption,
        #          then escalate via _record_failure.
        # ------------------------------------------------------------------
        try:
            self._conn.commit()
        except Exception as exc:
            try:
                self._conn.rollback()
            except Exception as rb_exc:
                logger.error(
                    "[snapshot_writer] rollback also failed: %s", rb_exc
                )
            self._record_failure(exc, context="commit")
            raise  # only reached if below failure_threshold

        # ------------------------------------------------------------------
        # Phase 7: update metrics on success.
        #          _lock is never held during DB operations above.
        # ------------------------------------------------------------------
        total_wrote = wrote_q + wrote_f
        with self._lock:
            self._consecutive_failures = 0
            self._total_rows_written += total_wrote
            self._last_snapshot_at = time.time()
            self._last_snapshot_rows = total_wrote

        result = SnapshotResult(
            ts=ts,
            wrote_quotes=wrote_q,
            wrote_funding=wrote_f,
            skipped_quotes=skipped_q,
            failed_quotes=failed_q,
            failed_funding=failed_f,
        )
        logger.info("[snapshot_writer] %s", result)
        return result

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _load_instrument_map(self) -> dict[str, int]:
        """Return current {symbol: id} mapping from the instruments table.

        Called once per snapshot() invocation so the map is always current
        even after ChainRefresher adds or expires instruments.
        """
        rows = self._conn.execute(
            "SELECT symbol, id FROM instruments"
        ).fetchall()
        return {row[0]: row[1] for row in rows}

    def _record_failure(self, exc: Exception, *, context: str) -> None:
        """Increment consecutive failure counter; raise ComponentFailure at threshold.

        This method either returns (below threshold) or raises ComponentFailure
        (at/above threshold).  The caller should re-raise the original exception
        when this method returns normally.
        """
        with self._lock:
            self._consecutive_failures += 1
            cf = self._consecutive_failures

        logger.error(
            "[snapshot_writer] full-cycle failure during %s "
            "(consecutive=%d, threshold=%d): %s: %s",
            context, cf, self._failure_threshold,
            type(exc).__name__, exc,
        )
        if cf >= self._failure_threshold:
            raise ComponentFailure(
                f"snapshot failed {cf} consecutive times "
                f"(last: {context}: {type(exc).__name__}: {exc})",
                component=self.COMPONENT_NAME,
            ) from exc
        # Below threshold: caller re-raises original exc


# ---------------------------------------------------------------------------
# Self-test / smoke  (in-memory SQLite — no live network required)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from src.data.storage import init_schema

    # -- Setup in-memory DB --------------------------------------------------
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    init_schema(conn)

    conn.execute("""
        INSERT INTO instruments
            (id, symbol, contract_type, underlying, quoting,
             strike_price, settlement_time, tick_size, contract_value,
             state, raw_json, fetched_at)
        VALUES
            (1, 'C-BTC-95000-280526', 'call_options', 'BTC', 'USDT',
             95000.0, '2026-05-28T12:00:00Z', 0.01, 0.001,
             'live', '{}', '2026-05-14T00:00:00Z')
    """)
    conn.commit()

    # -- Populate MarketState ------------------------------------------------
    ms = MarketState(universe=["C-BTC-95000-280526"])
    ms.update_ticker("C-BTC-95000-280526", {
        "type": "v2/ticker",
        "symbol": "C-BTC-95000-280526",
        "mark_price": "95100.5",
        "spot_price": "95000.0",
        "close": "95050.0",
        "quotes": {
            "best_bid": "95000.0", "best_ask": "95200.0",
            "mark_iv": "0.65", "bid_iv": "0.64", "ask_iv": "0.66",
        },
        "greeks": {
            "delta": "0.52", "gamma": "0.001",
            "theta": "-10.0", "vega": "50.0", "rho": "0.1",
        },
        "oi": "0", "oi_contracts": "5", "oi_value_usd": "475502.5",
        "volume": "12",
    })
    ms.update_funding("BTCUSD", {
        "type": "funding_rate",
        "symbol": "BTCUSD",
        "funding_rate": "0.0001",
        "funding_rate_8h": "0.0003",
        "predicted_funding_rate": "0.00012",
        "predicted_funding_rate_8h": "0.00036",
    })

    # -- Test 1: normal snapshot ---------------------------------------------
    sw = SnapshotWriter(conn, ms, failure_threshold=3)
    sw.start()
    result = sw.snapshot()
    print(f"[test 1] {result}")
    assert result.wrote_quotes == 1, f"expected 1, got {result.wrote_quotes}"
    assert result.wrote_funding == 1, f"expected 1, got {result.wrote_funding}"
    assert result.skipped_quotes == 0
    assert result.failed_quotes == 0
    assert result.failed_funding == 0

    h = sw.health()
    print(f"[test 1] health: {h}")
    assert h.healthy, f"expected healthy, got: {h}"
    assert h.fatal is False

    # -- Verify DB contents --------------------------------------------------
    q_row = conn.execute(
        "SELECT mark_price, delta, oi_contracts FROM option_quotes"
    ).fetchone()
    assert q_row[0] == 95100.5, f"mark_price={q_row[0]}"
    assert q_row[1] == 0.52,    f"delta={q_row[1]}"
    assert q_row[2] == 5.0,     f"oi_contracts={q_row[2]}"

    f_row = conn.execute(
        "SELECT funding_rate FROM funding_snapshots"
    ).fetchone()
    assert f_row[0] == 0.0001, f"funding_rate={f_row[0]}"
    print("[test 1] DB contents verified.")

    # -- Test 2: second snapshot (different ts) uses INSERT OR REPLACE -------
    time.sleep(0.01)
    result2 = sw.snapshot()
    assert result2.wrote_quotes == 1
    total_q_rows = conn.execute(
        "SELECT COUNT(*) FROM option_quotes"
    ).fetchone()[0]
    assert total_q_rows == 2, f"expected 2 rows, got {total_q_rows}"
    print(f"[test 2] second snapshot ok, total option_quotes rows={total_q_rows}")

    # -- Test 3: symbol absent from instruments table → skipped, not failed --
    ms2 = MarketState(universe=["C-BTC-95000-280526", "C-BTC-99999-280526"])
    ms2.update_ticker(
        "C-BTC-95000-280526",
        ms.all_tickers()["C-BTC-95000-280526"],
    )
    ms2.update_ticker("C-BTC-99999-280526", {
        "type": "v2/ticker", "symbol": "C-BTC-99999-280526",
        "mark_price": "0",
    })
    sw2 = SnapshotWriter(conn, ms2, failure_threshold=3)
    result3 = sw2.snapshot()
    print(f"[test 3] {result3}")
    assert result3.skipped_quotes == 1, f"expected 1 skip, got {result3.skipped_quotes}"
    assert result3.wrote_quotes == 1

    # -- Test 4: consecutive failure tracking --------------------------------
    bad_conn = sqlite3.connect(":memory:")  # no schema → commit will still work but
    # to simulate a failure, we'll close the connection so writes raise
    bad_conn.close()

    sw_bad = SnapshotWriter(bad_conn, ms, failure_threshold=3)
    for attempt in range(1, 3):  # below threshold — should raise original exc, not ComponentFailure
        try:
            sw_bad.snapshot()
            assert False, "expected exception"
        except ComponentFailure:
            assert False, f"should not raise ComponentFailure before threshold (attempt {attempt})"
        except Exception as exc:
            print(f"[test 4] attempt {attempt} raised {type(exc).__name__} (expected)")
            with sw_bad._lock:
                cf = sw_bad._consecutive_failures
            assert cf == attempt, f"expected cf={attempt}, got {cf}"

    # At threshold (attempt 3) → ComponentFailure expected
    try:
        sw_bad.snapshot()
        assert False, "expected ComponentFailure"
    except ComponentFailure as exc:
        print(f"[test 4] ComponentFailure raised at threshold=3: {exc}")
        assert exc.component == SnapshotWriter.COMPONENT_NAME

    # -- Teardown ------------------------------------------------------------
    sw.stop()
    print("SnapshotWriter smoke test passed.")
