"""Reconciliation between exchange state and local state.

One rule governs everything here: **the exchange is the source of truth.** Local
state is a cache of it, and where they disagree the exchange wins.

The reconciler never places an order and never cancels one. It reads, compares,
and either adopts the exchange's view or halts trading. Anything it cannot
explain is a halt, not a repair -- an execution stack that silently "fixes"
divergence it does not understand is worse than one that stops.

Divergence classes:

  IN_SYNC          local matches exchange
  LOCAL_STALE      exchange has moved on; adopt the exchange state
  ORPHAN_LOCAL     local SUBMITTED/UNKNOWN with no exchange order; resolve via
                   fills if it actually traded, else mark REJECTED (never sent)
  ORPHAN_EXCHANGE  an exchange order we have no record of -> HALT
  POSITION_MISMATCH exchange position differs from local -> HALT
"""
from __future__ import annotations

import dataclasses
import enum
import logging
from datetime import datetime, timezone

from src.data import storage
from src.execution.order_state import OPEN_STATES, OrderState, transition
from src.runtime.health import HealthStatus

log = logging.getLogger(__name__)


class Divergence(str, enum.Enum):
    IN_SYNC = "in_sync"
    LOCAL_STALE = "local_stale"
    ORPHAN_LOCAL = "orphan_local"
    ORPHAN_EXCHANGE = "orphan_exchange"
    POSITION_MISMATCH = "position_mismatch"


@dataclasses.dataclass(frozen=True)
class ReconcileResult:
    clean: bool
    halt_reason: str = ""
    findings: tuple[tuple[str, Divergence, str], ...] = ()

    @property
    def divergences(self) -> list[Divergence]:
        return [d for _, d, _ in self.findings]


#: Delta order states -> our state machine.
_EXCHANGE_STATE_MAP = {
    "open": OrderState.ACKED,
    "pending": OrderState.ACKED,
    "partially_filled": OrderState.PARTIAL,
    "closed": OrderState.FILLED,
    "filled": OrderState.FILLED,
    "cancelled": OrderState.CANCELLED,
    "canceled": OrderState.CANCELLED,
}


class Reconciler:
    """Compares local orders/positions against the exchange.

    Implements the HealthStatus protocol so the existing Supervisor can carry
    it alongside ws_manager, chain_refresher and snapshot_writer.
    """

    def __init__(self, conn, client, *, product_id: int) -> None:
        self._conn = conn
        self._client = client
        self._product_id = product_id
        self._last_result: ReconcileResult | None = None
        self._last_run: datetime | None = None
        self._halted = False
        self._halt_reason = ""

    # -- state -------------------------------------------------------------

    @property
    def halted(self) -> bool:
        return self._halted

    @property
    def halt_reason(self) -> str:
        return self._halt_reason

    def _halt(self, reason: str) -> None:
        self._halted = True
        self._halt_reason = reason
        log.critical("[reconcile] HALT: %s", reason)

    # -- core --------------------------------------------------------------

    def reconcile(self) -> ReconcileResult:
        self._last_run = datetime.now(timezone.utc)
        findings: list[tuple[str, Divergence, str]] = []

        live_resp = self._client.get_live_orders(self._product_id)
        fills_resp = self._client.get_fills(self._product_id)
        pos_resp = self._client.get_positions()

        for name, resp in (("orders", live_resp), ("fills", fills_resp),
                           ("positions", pos_resp)):
            if not isinstance(resp, dict) or resp.get("success") is not True:
                reason = f"exchange {name} query failed or malformed"
                self._halt(reason)
                return ReconcileResult(False, reason, tuple(findings))

        live_orders = live_resp.get("result") or []
        fills = fills_resp.get("result") or []
        positions = pos_resp.get("result") or []

        by_coid = {
            o.get("client_order_id"): o for o in live_orders if o.get("client_order_id")
        }
        filled_coids = {
            f.get("client_order_id") for f in fills if f.get("client_order_id")
        }

        local_open = storage.get_open_orders(
            self._conn, tuple(s.value for s in OPEN_STATES)
        )
        local_coids = {r["client_order_id"] for r in local_open if r["client_order_id"]}

        # --- local orders vs exchange ------------------------------------
        for row in local_open:
            coid = row["client_order_id"]
            current = OrderState(row["status"])
            exch = by_coid.get(coid)

            if exch is not None:
                mapped = _EXCHANGE_STATE_MAP.get(str(exch.get("state", "")).lower())
                if mapped is None:
                    findings.append((coid, Divergence.ORPHAN_EXCHANGE,
                                     f"unmapped exchange state {exch.get('state')!r}"))
                    self._halt(f"{coid}: unmapped exchange state {exch.get('state')!r}")
                    continue
                if mapped is current:
                    findings.append((coid, Divergence.IN_SYNC, mapped.value))
                else:
                    self._adopt(row, mapped, exch.get("id"))
                    findings.append((coid, Divergence.LOCAL_STALE,
                                     f"{current.value} -> {mapped.value}"))
                continue

            # Not live on the exchange. Did it fill, or never arrive?
            if coid in filled_coids:
                self._adopt(row, OrderState.FILLED, None)
                findings.append((coid, Divergence.LOCAL_STALE,
                                 f"{current.value} -> filled (via fills)"))
            elif current is OrderState.UNKNOWN:
                # Ambiguous submit that left no trace: it never reached matching.
                self._adopt(row, OrderState.REJECTED, None)
                findings.append((coid, Divergence.ORPHAN_LOCAL,
                                 "unknown with no exchange trace -> rejected"))
            else:
                self._adopt(row, OrderState.CANCELLED, None)
                findings.append((coid, Divergence.ORPHAN_LOCAL,
                                 f"{current.value} absent from exchange -> cancelled"))

        # --- exchange orders we have never seen --------------------------
        for coid, exch in by_coid.items():
            if coid not in local_coids and storage.get_order(self._conn, coid) is None:
                findings.append((coid, Divergence.ORPHAN_EXCHANGE,
                                 f"exchange order id={exch.get('id')} unknown locally"))
                self._halt(
                    f"orphan exchange order {coid} (id={exch.get('id')}) — "
                    "never auto-cancelled; operator must resolve"
                )

        # --- positions ----------------------------------------------------
        exch_size = sum(abs(int(p.get("size") or 0)) for p in positions)
        local_size = self._local_position_size()
        if exch_size != local_size:
            findings.append(("<position>", Divergence.POSITION_MISMATCH,
                             f"exchange={exch_size} local={local_size}"))
            self._halt(f"position mismatch: exchange={exch_size} local={local_size}")

        clean = not self._halted and all(
            d in (Divergence.IN_SYNC, Divergence.LOCAL_STALE, Divergence.ORPHAN_LOCAL)
            for _, d, _ in findings
        )
        result = ReconcileResult(clean, self._halt_reason, tuple(findings))
        self._last_result = result
        return result

    def _local_position_size(self) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(quantity), 0) FROM positions WHERE status = 'open'"
        ).fetchone()
        return int(abs(row[0])) if row and row[0] is not None else 0

    def _adopt(self, row: dict, new_state: OrderState, delta_id) -> None:
        """Adopt the exchange's view. reconcile_only=True is what permits an
        UNKNOWN order to leave that state -- nothing else in the codebase may."""
        current = OrderState(row["status"])
        new_state = transition(current, new_state, reconcile_only=True)
        storage.write_order(
            self._conn,
            client_order_id=row["client_order_id"],
            instrument_id=row["instrument_id"],
            side=row["side"],
            order_type=row["order_type"],
            quantity=row["quantity"],
            price=row["price"],
            status=new_state.value,
            strategy=row["strategy"],
            placed_ts=row["placed_ts"],
            delta_order_id=str(delta_id) if delta_id else row.get("delta_order_id"),
        )
        log.info("[reconcile] adopted %s: %s -> %s",
                 row["client_order_id"], current.value, new_state.value)

    # -- boot gate ---------------------------------------------------------

    def boot_reconcile(self) -> ReconcileResult:
        """Must run, and be clean, before trading is enabled."""
        log.info("[reconcile] boot reconciliation starting")
        result = self.reconcile()
        if not result.clean:
            log.critical("[reconcile] boot reconciliation NOT clean: %s", result.halt_reason)
        return result

    # -- Supervisor protocol ------------------------------------------------

    def health(self) -> HealthStatus:
        if self._halted:
            return HealthStatus.degraded("reconciler", self._halt_reason, fatal=True)
        if self._last_run is None:
            return HealthStatus.degraded("reconciler", "never run")
        n = len(self._last_result.findings) if self._last_result else 0
        return HealthStatus.ok("reconciler", f"last run ok, {n} finding(s)")
