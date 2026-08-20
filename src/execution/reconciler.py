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
import sqlite3
from datetime import datetime, timezone

from src.data import storage
from src.execution import fills as fills_mod
from src.execution import protection as protection_mod
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

    def __init__(self, conn, client, *, product_id: int,
                 ingest_fills: bool = True, risk_state=None,
                 executor=None, enforce_protection: bool = True) -> None:
        self._conn = conn
        self._client = client
        self._product_id = product_id
        self._ingest_fills = ingest_fills
        self._risk_state = risk_state
        #: Supervised executor used to establish protective stops. Without one
        #: protection is detect-only: an unprotected position still HALTS, it
        #: just cannot be repaired automatically. Detect-only fails closed.
        self._executor = executor
        self._enforce_protection = enforce_protection
        self._last_protection: protection_mod.ProtectionStatus | None = None
        self._last_result: ReconcileResult | None = None
        self._last_run: datetime | None = None
        self._halted = False
        self._halt_reason = ""
        self._running = False
        self._booted = False
        self._pass_halted = False

    @property
    def connection(self) -> sqlite3.Connection:
        """The handle this component transacts on.

        Exposed so the runtime can assert at boot that no two independently
        scheduled components hold the same one -- a check on what a component
        actually holds, not on what the wiring code meant to hand it.
        """
        return self._conn

    # -- state -------------------------------------------------------------

    @property
    def halted(self) -> bool:
        return self._halted

    @property
    def halt_reason(self) -> str:
        return self._halt_reason

    def _halt(self, reason: str) -> None:
        """Latch a halt.

        Two flags, deliberately: ``_halted`` is STICKY and survives every
        subsequent pass, restart included -- that is what makes a halt a real
        safety control rather than a transient warning. ``_pass_halted`` is
        scoped to the current pass, so a later pass can be judged on its own
        evidence without the latch making the verdict a foregone conclusion.
        Without that separation a halted reconciler could never be shown clean
        again, and the halt could only be cleared by restarting the process.
        """
        self._halted = True
        self._pass_halted = True
        self._halt_reason = reason
        log.critical("[reconcile] HALT: %s", reason)

    @property
    def running(self) -> bool:
        return self._running

    @property
    def booted(self) -> bool:
        return self._booted

    @property
    def trading_permitted(self) -> bool:
        """The single question the risk layer asks.

        Trading requires a reconciler that is started, has completed a boot
        reconciliation, and is not halted. Every one of those is False by
        default, so the permissive state is only ever reached deliberately.
        """
        return self._running and self._booted and not self._halted

    def gate_state(self) -> tuple[bool, str]:
        """``(blocked, reason)`` for ``RiskManager(halt_provider=...)``.

        Pure: reads cached state only, never queries the exchange, so the risk
        manager's network-free contract survives this wiring.
        """
        if self._halted:
            return True, "reconciler halted: " + (self._halt_reason or "unspecified")
        if not self._running:
            return True, "reconciler not running"
        if not self._booted:
            return True, "boot reconciliation has not run"
        return False, ""

    # -- core --------------------------------------------------------------

    def reconcile(self) -> ReconcileResult:
        self._last_run = datetime.now(timezone.utc)
        self._pass_halted = False
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

        # --- fills BEFORE positions ---------------------------------------
        # Ordering is the correctness property. A real fill must become local
        # state within the SAME pass that observes it, otherwise the position
        # check below compares an exchange position against a local view that
        # has not been told about it yet and halts on a divergence that is
        # purely an artifact of processing order.
        if self._ingest_fills:
            try:
                fills_result = fills_mod.ingest_fills(
                    self._conn, fills, instrument_id=self._product_id,
                    risk_state=self._risk_state)
                if fills_result.ingested:
                    findings.append(("<fills>", Divergence.LOCAL_STALE,
                                     f"ingested {fills_result.ingested} fill(s), "
                                     f"position now {fills_result.position_after:+d}"))
            except Exception as exc:
                # A fill we cannot read is not a fill we may ignore: it means
                # local state is provably behind the exchange.
                reason = f"fill ingestion failed: {type(exc).__name__}: {exc}"
                self._halt(reason)
                return ReconcileResult(False, reason, tuple(findings))

        # --- positions ----------------------------------------------------
        exch_size = sum(abs(int(p.get("size") or 0)) for p in positions)
        local_size = self._local_position_size()
        if exch_size != local_size:
            findings.append(("<position>", Divergence.POSITION_MISMATCH,
                             f"exchange={exch_size} local={local_size}"))
            self._halt(f"position mismatch: exchange={exch_size} local={local_size}")

        # --- protection: the invariant, checked AFTER positions are current -
        # open_position != 0 => a valid reduce-only stop must exist.
        # Deliberately last: it must see the position this pass produced, not
        # the one it started with.
        if self._enforce_protection:
            status = protection_mod.evaluate(self._conn, live_orders)
            if not status.is_safe:
                status = protection_mod.ensure(
                    self._conn, self._executor, self._client, status,
                    mark_price=self._mark_price_hint())
                # Re-derive from the exchange rather than trusting the attempt.
                refreshed = self._client.get_live_orders(self._product_id)
                if isinstance(refreshed, dict) and refreshed.get("success") is True:
                    status = protection_mod.evaluate(
                        self._conn, refreshed.get("result") or [])
            self._last_protection = status
            if not status.is_safe:
                findings.append(("<protection>", Divergence.POSITION_MISMATCH,
                                 f"{status.state.value}: {status.detail}"))
                self._halt(f"position {status.position_size:+d} is "
                           f"{status.state.value}: {status.detail}")

        clean = not self._pass_halted and all(
            d in (Divergence.IN_SYNC, Divergence.LOCAL_STALE, Divergence.ORPHAN_LOCAL)
            for _, d, _ in findings
        )
        result = ReconcileResult(clean, self._halt_reason, tuple(findings))
        self._last_result = result
        return result

    def set_executor(self, executor) -> None:
        """Attach the supervised executor after construction.

        The runtime wiring is genuinely cyclic: the risk manager needs this
        reconciler's gate_state, the executor needs that risk manager, and
        protection needs the executor. Something has to be attached second.
        Doing it here -- rather than building a second executor for protection
        -- keeps exactly one supervised execution path, which is the property
        that makes the daily caps and write-ahead persistence meaningful.

        Must be called BEFORE start(): boot reconciliation is the first pass
        that can find an unprotected position, and it should be able to fix it
        rather than merely halt.
        """
        self._executor = executor

    @property
    def protection(self) -> "protection_mod.ProtectionStatus | None":
        return self._last_protection

    def _mark_price_hint(self) -> float:
        """Best-effort mark for the protective intent's deviation guard.

        Public endpoint only. A failure here must not itself halt: the caller
        already halts if protection cannot be established.
        """
        try:
            t = self._client._public_get("/v2/tickers/BTCUSD", None)
            return float(t["result"]["mark_price"])
        except Exception:
            pos = fills_mod.open_position(self._conn)
            return float(pos["entry_price"]) if pos else 0.0

    def _local_position_size(self) -> int:
        """Absolute local exposure, from the fill-derived positions projection."""
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
        """Must run, and be clean, before trading is enabled.

        A query that raises is treated exactly like a divergence: halt. An
        exchange we cannot read is an exchange we cannot claim to agree with.
        """
        log.info("[reconcile] boot reconciliation starting")
        try:
            result = self.reconcile()
        except Exception as exc:
            reason = f"boot reconciliation failed: {type(exc).__name__}: {exc}"
            self._halt(reason)
            result = ReconcileResult(False, reason, ())
            self._last_result = result
        self._booted = True
        if not result.clean:
            log.critical("[reconcile] boot reconciliation NOT clean: %s",
                         result.halt_reason)
        return result

    # -- lifecycle (Supervisor component protocol) --------------------------

    def start(self) -> None:
        """Start, and boot-reconcile before trading may be permitted.

        Restart-safe in the one direction that matters: a stop()/start() cycle
        re-runs boot reconciliation but CANNOT clear an existing halt. A halt
        survives restart by design -- if restarting cleared it, the supervisor's
        own restart policy would become an automatic halt-bypass.

        Registers nothing. Scheduler jobs are owned by
        `reconciliation_jobs.register_reconciliation_jobs`, so restarting this
        component can never duplicate a job.
        """
        self._running = True
        log.info("[reconcile] start (halted=%s)", self._halted)
        self.boot_reconcile()

    def stop(self) -> None:
        """Stop. Deliberately does NOT clear the halt latch or the boot flag."""
        self._running = False
        log.info("[reconcile] stop (halted=%s)", self._halted)

    # -- operator halt resolution -------------------------------------------

    def clear_halt_if_clean(self) -> bool:
        """Explicit, operator-initiated halt resolution.

        Runs a fresh reconciliation and clears the latch ONLY if that pass
        finds nothing wrong. Deliberately NOT called from the scheduled job:
        automatic self-healing is precisely what an execution stack must not
        do. A halt is cleared because someone looked, not because time passed.
        """
        if not self._halted:
            return True
        result = self.reconcile()
        if result.clean:
            log.warning("[reconcile] halt cleared by explicit clean reconciliation "
                        "(was: %s)", self._halt_reason)
            self._halted = False
            self._halt_reason = ""
            return True
        log.critical("[reconcile] halt NOT cleared; still divergent: %s",
                     result.halt_reason or "see findings")
        return False

    # -- Supervisor protocol ------------------------------------------------

    def health(self) -> HealthStatus:
        if self._halted:
            return HealthStatus.degraded("reconciler", self._halt_reason, fatal=True)
        if self._last_run is None:
            return HealthStatus.degraded("reconciler", "never run")
        n = len(self._last_result.findings) if self._last_result else 0
        return HealthStatus.ok("reconciler", f"last run ok, {n} finding(s)")
