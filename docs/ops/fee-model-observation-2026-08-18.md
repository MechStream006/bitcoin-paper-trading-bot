# Fee-reserve observation — supervised order gate, 2026-08-18

Record of the one direct measurement of Delta's fee-margin reservation, taken
during the first and only supervised testnet order. Written because the
multiplier it establishes is **not exposed by any API field** and would
otherwise exist only as a magic number in `src/risk/limits.py`.

## 1. The measurement

Exchange order `2169335031`, placed 08:41:25 UTC, cancelled 08:41:59 UTC.
Never filled; no position at any point.

| Field | Value |
|---|---|
| symbol / product_id | BTCUSD / 84 |
| side / size / type | buy / 1 contract / limit_order |
| limit_price | 63051.2 |
| mark_price at submit | 64272.36695161 |
| `order_margin` (while resting) | **6.30512** |
| `blocked_margin` (while resting) | **6.379520416** |
| implied fee reserve | **0.074400416** |

Wallet before and after: `balance=200, available=200, order_margin=0,
position_margin=0, blocked_margin=0`. Zero P&L.

## 2. What the margin leg confirms

```
notional_at_limit = 0.001 × 63051.2            = 63.0512
notional / default_leverage = 63.0512 / 10     =  6.30512   ← matches exactly
notional × initial_margin   = 63.0512 × 0.005  =  0.31526   ← the pre-F2 model
```

Two findings, both now pinned by tests:

1. **The leverage leg binds, not the `initial_margin` percentage floor.** This
   was the F2 correction (commit `dcb064e9`), previously reasoned from the
   product spec. It is now *measured*: the exchange reserved exactly 20× what
   the pre-F2 arithmetic predicted.
2. **Margin is computed on the ORDER price, not the mark.** `6.30512` derives
   from 63051.2, not from 64272.37.

## 3. What the fee leg shows

```
raw round-trip taker = 63.0512 × 0.0005 × 2 = 0.0630512
observed reserve                            = 0.074400416
ratio                                       = 1.180000000  (exact to 1e-12)
```

`0.0630512 × 1.18 = 0.074400416` reproduces the observation with no residual.

### 3.1 The multiplier is inferred, not documented

Searched read-only, all negative:

| Endpoint | Result |
|---|---|
| `GET /v2/products` (BTCUSD) | no `0.18`/`1.18`/`18` value anywhere in the payload; fee fields are only `taker_commission_rate=0.0005`, `maker_commission_rate=0.0002` |
| `GET /v2/assets` | withdrawal-fee fields only |
| `GET /v2/wallet/balances` | `commission`, `cross_commission`, `trading_fee_credit`, `pending_trading_fee_credit` — none carries the ratio |
| `GET /v2/profile` | no fee/tax fields |
| `GET /v2/users/trading_preferences` | `deto_for_commission` only |
| `GET /v2/wallet/transactions` | no fee/tax fields |

A ratio of exactly 1.18 is consistent with an **18% tax applied to trading
fees on the India platform**. That reading is plausible and arithmetically
exact, but it is **an inference from a single observation**, not a value read
from a documented field. It is recorded here as such.

### 3.2 Known limits of this evidence

- **n = 1.** One order, one price, one side, one size, one day.
- **Buy side only.** Sell-side reservation is unobserved.
- **Never filled.** This is the *reservation* on a resting order. The fee
  actually charged on a **fill** has not been observed and may differ in basis
  (fill price rather than order price) or in rate (maker vs taker).
- **Testnet.** Per CLAUDE.md §11 testnet is pipeline-validation only; mainnet
  fee treatment must be re-measured, not assumed.

## 4. The correction

`limits.FEE_RESERVE_MULTIPLIER = 1.18`, applied in
`manager.round_trip_fees_usd()`.

Deliberately applied as a **conservatism factor**, not as a claim about tax
law. It can only raise the fee estimate, so if the inference is wrong the cost
is budget headroom, never a breached limit. Both consumers of the fee model —
the per-trade risk budget and the equity guard — become strictly stricter.

Effect at the reference mark (64187.5, 1 contract):

| | before | after |
|---|---|---|
| round-trip fees | $0.0642 | $0.0758 |
| net risk on the default intent | $0.9642 | $0.9758 |
| margin + fees | $6.4830 | $6.4946 |

One contract remains viable inside the $1.00 budget with $0.024 of headroom.

### 4.1 Deliberately NOT changed

**The fee basis stays `mark_price`.** The exchange reserves on the order price,
but `round_trip_fees_usd(mark, size)` is called from two places that legitimately
have only the mark. Within the 2% `MAX_PRICE_DEVIATION_PCT` band the basis
difference is at most ~2% of the fee — about $0.0015 — versus the 18% term
this change addresses. For a **buy** (limit below mark) using the mark is
already the conservative choice; for a **sell** it is marginally optimistic by
that same $0.0015. Changing the signature to thread an order price through
both call sites was judged disproportionate to a $0.0015 effect. Revisit if
sizing grows or the deviation band widens.

## 5. Re-measurement triggers

Re-run this measurement and update the constant if any of these change:

1. First **fill** on any order (the charged fee, as opposed to the reserve, is
   still unobserved).
2. First **sell**-side order.
3. Any move to **mainnet** or to the Global platform.
4. Size beyond 1 contract, or a materially different price level.
5. Any Delta change to `taker_commission_rate` / `maker_commission_rate`.

Regression tests: `tests/test_demo_execution.py::TestObservedExchangeBehaviour`
pins all the numbers above. If Delta's behaviour changes, those tests are where
it should surface first.
