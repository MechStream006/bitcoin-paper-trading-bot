"""Risk manager for the testnet demo execution stack.

Contract: ``validate()`` is PURE with respect to the network. It performs no
HTTP call of any kind. Every input it needs -- mark price, equity, open
positions -- is passed in by the caller, which has already fetched them. That
is what makes "a rejected order made zero network calls" a testable property
rather than a hope.

Rejection is the default. ``Decision.approved`` is True only if every guard
passes; any failure short-circuits with a specific, loggable reason.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

from src.risk import limits


@dataclasses.dataclass(frozen=True)
class OrderIntent:
    """What a strategy wants. Carries no authority to execute."""

    client_order_id: str
    symbol: str
    product_id: int
    side: str                    # "buy" | "sell"
    size: int                    # integer contracts
    order_type: str              # must be "limit_order"
    limit_price: float
    stop_price: float | None     # required to bound risk
    strategy: str


@dataclasses.dataclass(frozen=True)
class AccountSnapshot:
    """Caller-supplied account state. The risk manager never fetches this."""

    equity_usd: float
    available_usd: float
    open_positions: int
    mark_price: float


@dataclasses.dataclass(frozen=True)
class Decision:
    approved: bool
    reason: str = ""
    risk_usd: float | None = None
    fees_usd: float | None = None

    @staticmethod
    def approve(risk_usd: float, fees_usd: float) -> "Decision":
        return Decision(True, "", risk_usd, fees_usd)

    @staticmethod
    def reject(reason: str, risk_usd: float | None = None) -> "Decision":
        return Decision(False, reason, risk_usd, None)


def round_trip_fees_usd(mark_price: float, size: int) -> float:
    """Taker in, taker out -- the worst case, so the budget is never understated."""
    notional = limits.BTCUSD_CONTRACT_VALUE * mark_price * size
    return notional * limits.TAKER_FEE_RATE * 2


def compute_trade_risk_usd(intent: OrderIntent, mark_price: float) -> float:
    """Net risk: adverse move to the stop PLUS round-trip fees.

    Fees are included by operator decision (2026-08-18): at 1 contract they are
    roughly 6.4% of the $1 budget, too large to treat as noise.
    """
    if intent.stop_price is None:
        return float("inf")
    distance = abs(intent.limit_price - intent.stop_price)
    price_risk = distance * limits.BTCUSD_CONTRACT_VALUE * intent.size
    return price_risk + round_trip_fees_usd(mark_price, intent.size)


def _in_settlement_window(now: datetime) -> bool:
    s_h, s_m = limits.SETTLEMENT_BLOCK_START
    e_h, e_m = limits.SETTLEMENT_BLOCK_END
    minute_of_day = now.hour * 60 + now.minute
    return (s_h * 60 + s_m) <= minute_of_day <= (e_h * 60 + e_m)


def _tick_aligned(price: float, tick: float) -> bool:
    # Integer arithmetic: 64185.3 / 0.1 is not exact in binary floating point.
    scaled = round(price / tick)
    return abs(scaled * tick - price) < 1e-9


class RiskManager:
    """Stateless-per-call validator. Daily counters are injected by the caller."""

    def __init__(
        self,
        *,
        kill_switch_engaged: bool,
        platform: str,
        base_url: str,
        daily_realised_pnl: float = 0.0,
        orders_placed_today: int = 0,
        halted: bool = False,
        halt_reason: str = "",
    ) -> None:
        self.kill_switch_engaged = kill_switch_engaged
        self.platform = platform
        self.base_url = base_url
        self.daily_realised_pnl = daily_realised_pnl
        self.orders_placed_today = orders_placed_today
        self.halted = halted
        self.halt_reason = halt_reason

    def validate(
        self,
        intent: OrderIntent,
        account: AccountSnapshot,
        now: datetime | None = None,
    ) -> Decision:
        now = now or datetime.now(timezone.utc)

        # 1. kill switch -- first, unconditional
        if self.kill_switch_engaged:
            return Decision.reject("kill switch engaged")

        # 2. halt latch
        if self.halted:
            reason = self.halt_reason or "unspecified"
            return Decision.reject("trading halted: " + reason)

        # 3. platform guard -- belt AND braces
        if self.platform != limits.REQUIRED_PLATFORM:
            return Decision.reject(f"platform is {self.platform!r}, not testnet")
        if limits.REQUIRED_URL_SUBSTRING not in (self.base_url or ""):
            return Decision.reject(f"base_url {self.base_url!r} is not a testnet endpoint")

        # 4. instrument allowlist
        if intent.symbol not in limits.ALLOWED_SYMBOLS:
            return Decision.reject(f"symbol {intent.symbol!r} not allowlisted")
        if intent.product_id not in limits.ALLOWED_PRODUCT_IDS:
            return Decision.reject(f"product_id {intent.product_id} not allowlisted")

        # 5. order shape
        if intent.order_type not in limits.ALLOWED_ORDER_TYPES:
            return Decision.reject(
                f"order_type {intent.order_type!r} not permitted (limit only)")
        if intent.side not in ("buy", "sell"):
            return Decision.reject(f"invalid side {intent.side!r}")
        if intent.size < 1:
            return Decision.reject(f"size {intent.size} below minimum 1")
        if intent.size > limits.MAX_CONTRACTS_PER_ORDER:
            return Decision.reject(
                f"size {intent.size} exceeds max {limits.MAX_CONTRACTS_PER_ORDER}")

        # 6. one position at a time
        if account.open_positions >= limits.MAX_OPEN_POSITIONS:
            return Decision.reject(
                f"{account.open_positions} open position(s), "
                f"max {limits.MAX_OPEN_POSITIONS}")

        # 7. daily order budget
        if self.orders_placed_today >= limits.MAX_ORDERS_PER_DAY:
            return Decision.reject(
                f"{self.orders_placed_today} orders today, "
                f"max {limits.MAX_ORDERS_PER_DAY}")

        # 8. daily loss cap
        if self.daily_realised_pnl <= -limits.MAX_DAILY_LOSS_USD:
            return Decision.reject(
                f"daily loss {self.daily_realised_pnl:.2f} at or beyond cap "
                f"-{limits.MAX_DAILY_LOSS_USD:.2f}")

        # 9. settlement dead zone (CLAUDE.md section 14)
        if _in_settlement_window(now):
            return Decision.reject("inside settlement dead zone 11:58-12:06 UTC")

        # 10. price sanity
        if account.mark_price <= 0:
            return Decision.reject("mark price unavailable")
        if intent.limit_price <= 0:
            return Decision.reject("limit price must be positive")
        deviation = abs(intent.limit_price - account.mark_price) / account.mark_price * 100
        if deviation > limits.MAX_PRICE_DEVIATION_PCT:
            return Decision.reject(
                f"limit price {deviation:.2f}% from mark, "
                f"max {limits.MAX_PRICE_DEVIATION_PCT}%")
        if not _tick_aligned(intent.limit_price, limits.BTCUSD_TICK_SIZE):
            return Decision.reject(
                f"limit price {intent.limit_price} not aligned to tick "
                f"{limits.BTCUSD_TICK_SIZE}")

        # 11. risk budget -- fees included
        if intent.stop_price is None:
            return Decision.reject("no stop price: trade risk is unbounded")
        risk = compute_trade_risk_usd(intent, account.mark_price)
        fees = round_trip_fees_usd(account.mark_price, intent.size)
        if risk > limits.HARD_RISK_CEILING_USD:
            return Decision.reject(
                f"risk ${risk:.4f} exceeds HARD ceiling "
                f"${limits.HARD_RISK_CEILING_USD:.2f}", risk)
        if risk > limits.MAX_TRADE_RISK_USD:
            return Decision.reject(
                f"risk ${risk:.4f} exceeds per-trade budget "
                f"${limits.MAX_TRADE_RISK_USD:.2f}", risk)

        # 12. equity floor
        if account.available_usd < limits.MIN_FREE_EQUITY_USD:
            return Decision.reject(
                f"available ${account.available_usd:.2f} below floor "
                f"${limits.MIN_FREE_EQUITY_USD:.2f}")
        notional = limits.BTCUSD_CONTRACT_VALUE * account.mark_price * intent.size
        required = notional * 0.005 + fees          # initial margin 0.5% + fees
        if account.available_usd < required:
            return Decision.reject(
                f"available ${account.available_usd:.2f} below required "
                f"${required:.4f} (margin + fees)")

        return Decision.approve(risk, fees)
