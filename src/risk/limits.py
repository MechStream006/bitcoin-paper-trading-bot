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

# --- fees (Delta India testnet, /v2/products 2026-08-18) -------------------
TAKER_FEE_RATE = 0.0005
MAKER_FEE_RATE = 0.0002

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
