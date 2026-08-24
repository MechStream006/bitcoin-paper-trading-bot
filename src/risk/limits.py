"""Frozen risk limits for the testnet demo execution stack.

These are OPERATIONAL choices, not evidence-derived thresholds. They exist to
bound the blast radius of a first execution path, not to express an edge. No
strategy result justifies changing them; only an explicit operator decision does.

Sizing rationale (measured 2026-08-18, BTCUSD mark $64,187.51):

    contract_value        0.001 BTC
    notional / contract   $64.19
    initial margin (0.5%) $0.32
    round-trip taker fee  $0.064   <- 6.4% of the per-trade budget

  MAX_TRADE_RISK_USD ($1.00) at 1 contract implies a ~1.56% adverse move,
  i.e. a stop roughly $1,000 below entry. The exchange minimum (1 contract)
  and the risk budget therefore coincide: no fractional sizing is possible
  and none is needed.

Fees count against the budget (operator decision, 2026-08-18): risk is
measured NET, as (entry - stop) * contract_value * qty + round_trip_fees.
"""
from __future__ import annotations

# --- account baseline ------------------------------------------------------
DEMO_EQUITY_BASELINE_USD = 200.0

# --- per-trade -------------------------------------------------------------
MAX_TRADE_RISK_USD = 1.00          # 0.5% of baseline — normal budget
HARD_RISK_CEILING_USD = 2.00       # 1.0% of baseline — absolute veto
MAX_DAILY_LOSS_USD = 4.00          # 2.0% of baseline — halts the UTC day

# --- position / order shape ------------------------------------------------
MAX_CONTRACTS_PER_ORDER = 1
MAX_OPEN_POSITIONS = 1
MAX_ORDERS_PER_DAY = 2             # one entry, one exit
ALLOWED_ORDER_TYPES = ("limit_order",)

# --- instrument allowlist --------------------------------------------------
ALLOWED_SYMBOLS = ("BTCUSD",)
ALLOWED_PRODUCT_IDS = (84,)
BTCUSD_PRODUCT_ID = 84
BTCUSD_TICK_SIZE = 0.1
BTCUSD_CONTRACT_VALUE = 0.001

# --- protective stops (verified on Delta India testnet 2026-08-19) --------
# Probe order 2169420335 established these accepted values on product 84:
# a native stop_loss_order with mark_price triggering and reduce_only=true is
# accepted while flat, rests as state="pending", reserves ZERO margin, and is
# independent of brackets (all bracket_* fields stayed null).
STOP_ORDER_TYPE = "stop_loss_order"
STOP_TRIGGER_METHOD = "mark_price"

# MAXIMUM distance from the position's entry price at which protection
# triggers -- not a mandatory distance. protection.protective_stop_distance()
# takes the tighter of this and what MAX_TRADE_RISK_USD can fund at the entry
# price, exactly as the strategy bridge does for entry sizing.
#
# The previous comment here claimed this "matches the strategy bridge's
# STOP_DISTANCE_PCT so the stop that authorised the trade's risk budget is the
# stop that actually exists". That stopped being true at Gate 5C.4, which made
# entry sizing budget-aware and left protection on a flat percentage; Gate
# 5C.11 measured the resulting gap live at 12-22%. The invariant is now
# enforced in code rather than asserted in a comment.
PROTECTIVE_STOP_DISTANCE_PCT = 1.4

# The stop is a stop-LIMIT: once triggered it becomes a limit order at this
# extra distance beyond the trigger. Slippage room, so a triggered stop can
# actually fill instead of resting unfilled through the move it exists to escape.
PROTECTIVE_STOP_LIMIT_SLIPPAGE_PCT = 0.15

# Hard bound on how far a protective order's LIMIT may sit from its own
# TRIGGER. A protective stop is deliberately away from market -- that is its
# function -- so it is checked against its trigger rather than against mark
# (see RiskManager.validate guard 10). This is what keeps the exemption
# narrow: the limit must stay adjacent to the trigger, so a "protective"
# order cannot carry an arbitrary price. Design uses 0.15%; the bound leaves
# headroom for tick rounding without becoming permissive.
MAX_PROTECTIVE_LIMIT_OFFSET_PCT = 0.5

# --- margin (Delta India testnet, /v2/products 2026-08-18) -----------------
# Delta exposes TWO margin inputs and the binding one is whichever is LARGER:
#
#   initial_margin    0.5   -> a PERCENT floor, i.e. 0.005 as a fraction
#   default_leverage  10.0  -> notional / 10 = 10% of notional
#
# At the live BTCUSD spec the leverage requirement is 20x the percentage floor
# ($6.41 vs $0.32 per contract at a ~$64,120 mark), so quoting only the 0.5%
# floor understates what the exchange actually reserves by that factor.
BTCUSD_INITIAL_MARGIN_PCT = 0.5     # percent, as Delta reports it
BTCUSD_DEFAULT_LEVERAGE = 10.0

# --- fees (Delta India testnet, /v2/products 2026-08-18) -------------------
TAKER_FEE_RATE = 0.0005
MAKER_FEE_RATE = 0.0002

# Delta reserves MORE fee margin than the raw commission rates predict.
#
# Measured on the supervised order gate (2026-08-18, exchange order 2169335031,
# 1 contract @ limit 63051.2): reserved fee margin was 0.074400416 USD where
# notional * taker * 2 predicts 0.0630512 -- a ratio of exactly 1.18, matching
# to 1e-12.
#
# The multiplier is NOT exposed by the API. It was absent from /v2/products,
# /v2/assets, /v2/profile, /v2/users/trading_preferences and
# /v2/wallet/transactions. It is consistent with an 18% tax on trading fees on
# the India platform, but that reading is inferred from ONE observation, not
# read from any documented field -- see docs/ops/fee-model-observation-2026-08-18.md.
#
# Applied as a conservatism factor: it can only ever raise the fee estimate, so
# a wrong inference costs budget headroom, never a breached limit.
FEE_RESERVE_MULTIPLIER = 1.18

# --- price sanity ----------------------------------------------------------
MAX_PRICE_DEVIATION_PCT = 2.0      # limit price must be within +/-2% of mark

# --- equity floor ----------------------------------------------------------
MIN_FREE_EQUITY_USD = 10.0         # refuse to trade a near-empty account

# --- settlement dead zone (CLAUDE.md 14) -----------------------------------
SETTLEMENT_BLOCK_START = (11, 58)  # UTC HH, MM inclusive
SETTLEMENT_BLOCK_END = (12, 6)     # UTC HH, MM inclusive

# --- platform guard --------------------------------------------------------
REQUIRED_PLATFORM = "testnet"
REQUIRED_URL_SUBSTRING = "testnet"
