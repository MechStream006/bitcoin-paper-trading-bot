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
from typing import Callable

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
    #: "entry" opens or increases exposure and consumes a daily order slot.
    #: "exit" and "protective" may only REDUCE exposure and are exempt from the
    #: ORDER-COUNT and DAILY-LOSS caps -- a risk limit that traps you inside a
    #: position is not a risk limit. "protective" is additionally exempt from
    #: the settlement dead zone, because a position must not sit unprotected
    #: across a window merely because new entries are paused there.
    #: Every other guard still applies. See RiskManager.validate.
    intent_kind: str = "entry"


@dataclasses.dataclass(frozen=True)
class AccountSnapshot:
    """Caller-supplied account state. The risk manager never fetches this."""

    equity_usd: float
    available_usd: float
    open_positions: int
    mark_price: float
    #: Signed open exposure in contracts (+long / -short / 0 flat), derived
    #: from the fill ledger. An exit is validated against this, so an "exit"
    #: cannot be used as a disguised entry.
    position_size: int = 0


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
    """Taker in, taker out -- the worst case, so the budget is never understated.

    Scaled by FEE_RESERVE_MULTIPLIER, which the exchange applies on top of the
    published commission rates (measured, not documented -- see limits.py).
    """
    notional = limits.BTCUSD_CONTRACT_VALUE * mark_price * size
    return notional * limits.TAKER_FEE_RATE * 2 * limits.FEE_RESERVE_MULTIPLIER


def affordable_stop_distance(price: float, size: int = 1) -> float:
    """Largest stop distance the per-trade budget can fund, after fees.

    Derived from compute_trade_risk_usd's OWN arithmetic rather than a parallel
    formula:

        |limit - stop| * contract_value * size + round_trip_fees_usd(price, size)

    solved for the distance. round_trip_fees_usd is called, not reimplemented,
    so a change to the fee model (FEE_RESERVE_MULTIPLIER, the taker rate) moves
    both sides together and this cannot silently drift out of agreement with
    the guard it must satisfy.

    Lives here, beside the fee model and the risk identity it inverts, because
    BOTH the entry sizing (strategies.bridge) and the protective sizing
    (execution.protection) must obey the same budget. Keeping one copy is what
    stops those two paths diverging -- which is exactly what happened between
    Gate 5C.4 and Gate 5C.11, when the entry path was made budget-aware and the
    protection path was left on a flat percentage.
    """
    budget = limits.MAX_TRADE_RISK_USD - round_trip_fees_usd(price, size)
    if budget <= 0:
        return 0.0
    return budget / (limits.BTCUSD_CONTRACT_VALUE * size)


def required_margin_usd(notional: float) -> float:
    """Margin the exchange will actually reserve for `notional`.

    Delta exposes two inputs and the BINDING one is whichever is larger:

        initial_margin   0.5  -> a PERCENT floor (0.005 as a fraction)
        default_leverage 10.0 -> notional / 10, i.e. 10% of notional

    Quoting only the 0.5% floor understates the real requirement by 20x at the
    live BTCUSD spec ($0.32 vs $6.41 per contract at a ~$64,120 mark). Taking
    the max is what the exchange does, so it is what the equity guard must do.
    """
    floor = notional * limits.BTCUSD_INITIAL_MARGIN_PCT / 100.0
    leveraged = notional / limits.BTCUSD_DEFAULT_LEVERAGE
    return max(floor, leveraged)


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
        halt_provider: "Callable[[], tuple[bool, str]] | None" = None,
        counters_provider: "Callable[[], tuple[float, int]] | None" = None,
    ) -> None:
        self.kill_switch_engaged = kill_switch_engaged
        self.platform = platform
        self.base_url = base_url
        self.daily_realised_pnl = daily_realised_pnl
        self.orders_placed_today = orders_placed_today
        self.halted = halted
        self.halt_reason = halt_reason
        #: Live halt source, normally ``Reconciler.gate_state``. Consulted on
        #: every validate() so a halt raised between two calls takes effect on
        #: the next one, rather than at the next process restart. Must be pure:
        #: validate()'s network-free contract depends on it.
        self.halt_provider = halt_provider
        #: Live daily counters, normally ``RiskStateStore.counters``. Consulted
        #: on every validate() so a cap tripped between two calls binds on the
        #: next one. If it raises -- corrupt persisted state -- validate()
        #: REJECTS rather than falling back to the constructor values, which
        #: would silently trade against counters known to be wrong.
        self.counters_provider = counters_provider

    def validate(
        self,
        intent: OrderIntent,
        account: AccountSnapshot,
        now: datetime | None = None,
    ) -> Decision:
        now = now or datetime.now(timezone.utc)

        # 0. intent kind, resolved first so the guards below can distinguish
        # opening exposure from reducing it. An unknown kind is rejected here
        # and can therefore never be treated as protective.
        if intent.intent_kind not in ("entry", "exit", "protective"):
            return Decision.reject(f"unknown intent_kind {intent.intent_kind!r}")
        is_protective = intent.intent_kind == "protective"
        is_exit = intent.intent_kind in ("exit", "protective")

        # 1. kill switch -- first, and unconditional for anything that OPENS or
        # discretionarily closes exposure.
        #
        # A protective stop is exempt, and only a protective stop. The kill
        # switch exists to stop the system taking on risk; if it also prevented
        # protecting risk already held, engaging it would convert a bounded
        # position into an unbounded one. That is the opposite of what an
        # operator means by "stop". Normal exits stay blocked: an exit is a
        # discretionary decision to trade, and the operator has said no.
        if self.kill_switch_engaged and not is_protective:
            return Decision.reject("kill switch engaged")

        # 2. halt latch -- static flag first, then the live source. Either
        # blocks; neither can override the other into permissiveness.
        if self.halted:
            reason = self.halt_reason or "unspecified"
            return Decision.reject("trading halted: " + reason)
        if self.halt_provider is not None:
            blocked, reason = self.halt_provider()
            if blocked:
                return Decision.reject("trading halted: " + (reason or "unspecified"))

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
        # The per-order size cap governs how much exposure may be OPENED. It
        # must not apply to reducing orders: a position of N contracts needs a
        # stop of N, and capping that at the entry size would make a larger
        # position permanently unprotectable -- the cap would create exactly
        # the exposure it exists to limit. Reducing orders are bounded instead
        # by |position_size|, checked below, which is strictly tighter.
        if (intent.intent_kind == "entry"
                and intent.size > limits.MAX_CONTRACTS_PER_ORDER):
            return Decision.reject(
                f"size {intent.size} exceeds max {limits.MAX_CONTRACTS_PER_ORDER}")

        # 5b. an exit must PROVABLY reduce exposure. These checks are what
        # earn it the cap exemption below; without them "exit" would just be a
        # keyword that bypasses the daily limits.
        if is_exit:
            if account.position_size == 0:
                return Decision.reject("exit requested with no open position")
            wanted = "sell" if account.position_size > 0 else "buy"
            if intent.side != wanted:
                return Decision.reject(
                    f"exit side {intent.side!r} would increase exposure "
                    f"(position {account.position_size:+d} needs {wanted!r})")
            if intent.size > abs(account.position_size):
                return Decision.reject(
                    f"exit size {intent.size} exceeds open position "
                    f"{abs(account.position_size)}; would flip exposure")

        # 6. one position at a time -- an exit is the thing that ENDS a
        # position, so this cap must not apply to it.
        if not is_exit and account.open_positions >= limits.MAX_OPEN_POSITIONS:
            return Decision.reject(
                f"{account.open_positions} open position(s), "
                f"max {limits.MAX_OPEN_POSITIONS}")

        # 6b. live daily counters -- fail closed if the state is unreadable
        realised_pnl = self.daily_realised_pnl
        orders_today = self.orders_placed_today
        if self.counters_provider is not None:
            try:
                realised_pnl, orders_today = self.counters_provider()
            except Exception as exc:
                return Decision.reject(
                    f"daily risk state unreadable ({type(exc).__name__}): {exc}")

        # 7. daily order budget -- ENTRIES ONLY.
        if not is_exit and orders_today >= limits.MAX_ORDERS_PER_DAY:
            return Decision.reject(
                f"{orders_today} orders today, "
                f"max {limits.MAX_ORDERS_PER_DAY}")

        # 8. daily loss cap -- ENTRIES ONLY. Blocking an exit once the loss cap
        # is hit would leave the account stuck in the losing position that
        # tripped it, turning the cap into the cause of further loss.
        if not is_exit and realised_pnl <= -limits.MAX_DAILY_LOSS_USD:
            return Decision.reject(
                f"daily loss {realised_pnl:.2f} at or beyond cap "
                f"-{limits.MAX_DAILY_LOSS_USD:.2f}")

        # 9. settlement dead zone (CLAUDE.md section 14). A protective stop is
        # exempt: refusing to protect an open position during the window would
        # make the dead zone the most dangerous part of the day.
        if not is_protective and _in_settlement_window(now):
            return Decision.reject("inside settlement dead zone 11:58-12:06 UTC")

        # 10. price sanity
        if account.mark_price <= 0:
            return Decision.reject("mark price unavailable")
        if intent.limit_price <= 0:
            return Decision.reject("limit price must be positive")
        if is_protective:
            # A protective stop is SUPPOSED to sit away from market -- that is
            # its entire function -- so measuring it against mark rejects
            # exactly the stop you need after an adverse move. The limit is
            # bounded against its own TRIGGER instead, which is what keeps a
            # triggered stop filling near where it triggered. This cannot
            # become an entry or a flip: side, size and reduce_only are proved
            # against the open position in guard 5b above.
            if intent.stop_price is None or intent.stop_price <= 0:
                return Decision.reject("protective order requires a stop trigger")
            offset = abs(intent.limit_price - intent.stop_price) / intent.stop_price * 100
            if offset > limits.MAX_PROTECTIVE_LIMIT_OFFSET_PCT:
                return Decision.reject(
                    f"protective limit {offset:.2f}% from its trigger, "
                    f"max {limits.MAX_PROTECTIVE_LIMIT_OFFSET_PCT}%")
        else:
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

        # 12. equity floor / margin -- ENTRIES ONLY.
        #
        # A reduce-only order reserves NO margin: measured on probe order
        # 2169420335, which rested with order_margin=0 while flat. Requiring
        # free equity before a position may be protected or closed would make
        # a drawdown -- exactly when equity is scarce -- the moment protection
        # becomes impossible. Reducing exposure can never require funding it.
        if is_exit:
            return Decision.approve(risk, fees)

        if account.available_usd < limits.MIN_FREE_EQUITY_USD:
            return Decision.reject(
                f"available ${account.available_usd:.2f} below floor "
                f"${limits.MIN_FREE_EQUITY_USD:.2f}")
        notional = limits.BTCUSD_CONTRACT_VALUE * account.mark_price * intent.size
        required = required_margin_usd(notional) + fees
        if account.available_usd < required:
            return Decision.reject(
                f"available ${account.available_usd:.2f} below required "
                f"${required:.4f} (margin + fees)")

        return Decision.approve(risk, fees)
