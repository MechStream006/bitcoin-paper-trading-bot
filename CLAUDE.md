# Bitcoin Paper Trading Bot — Project Charter

This is a **brand new, standalone** project. It is the BTC counterpart to my existing NIFTY options paper trading bot (`C:\Users\Rohith S\My_paper_trading_bot`), but the two projects share **zero code, zero config, zero database, zero Telegram channel**.

If you ever feel tempted to import or symlink anything from the NIFTY bot — don't. Copy-paste instead.

---

## 1. Project Identity

| Item | Value |
|---|---|
| Folder | `C:\Users\Rohith S\Bitcoin_paper_trading_bot` |
| GitHub repo | `bitcoin-paper-trading-bot` (private, to be created) |
| Broker | Delta Exchange (testnet first, then live) |
| Underlying | BTC options (USDT-settled) |
| Mode | Paper trading on testnet for first 2–4 weeks minimum |
| Telegram bot | New BotFather bot (e.g. `RohithBtcPaperBot`) — separate from NIFTY |
| Telegram channel | New private channel (e.g. `BTC Paper Alerts`) — separate from NIFTY |
| Database | `data/btc_trading.db` (SQLite) |
| Server | Local dev first; later possibly same AWS EC2 ap-south-1 as NIFTY but as separate `systemd` service |
| Owner | Rohith |
| Started | 2026-04-27 |

---

## 2. Hard Separation Rules

1. No shared code with NIFTY bot.
2. No shared `.env`, no shared DB, no shared logs, no shared Telegram credentials.
3. Claude memory for this project lives in its own auto-created folder (`C--Users-Rohith-S-Bitcoin-paper-trading-bot/memory/`). Do NOT copy memories from the NIFTY bot's memory folder.
4. If a utility from the NIFTY bot is genuinely useful (e.g. a logging helper), copy-paste it into this repo and own the divergence.

---

## 3. Delta Exchange Setup (do this BEFORE coding Layer 1)

1. **Sign up for testnet**: https://testnet.delta.exchange — separate account from live Delta, comes with free play USDT.
2. **Generate testnet API keys**:
   - Account → API Management → Create New Key
   - Permissions: enable **Read** + **Trading**, leave **Withdrawal** OFF
   - IP whitelist: leave open for local dev; lock to EC2 IP later
   - Save **API Key** and **API Secret** immediately — secret shown only once
3. **Bookmark docs**:
   - REST: https://docs.delta.exchange/#introduction
   - WebSocket: https://docs.delta.exchange/#websocket-feed
   - Auth signing: HMAC-SHA256 over `method + timestamp + path + query + body`
4. **BTC option symbol convention**: `C-BTC-95000-280426` = Call, BTC, strike 95000, expiry 26-Apr-2028. Daily, weekly, and monthly expiries exist.
5. **Lot size**: 0.001 BTC per BTC option contract on Delta (verify on each product page — can vary).
6. **Funding cycle (perps, future use)**: every 8 hours at 00:00 / 08:00 / 16:00 UTC.

**Credentials policy:** API keys live ONLY in `.env`. Never in chat, never in memory, never committed. `.env` must be in `.gitignore` from the first commit.

---

## 4. Tech Stack

- **Python 3.11+** (live bot venv) / **Python 3.10.x** (`btc_bot` research conda env — pinset validated on 3.10.20)
- Libraries:
  - `requests` — REST
  - `websockets` — live ticks
  - `pandas`, `numpy` — data
  - `python-telegram-bot` — alerts
  - `python-dotenv` — `.env` loader
  - `apscheduler` — periodic jobs (matters more than NIFTY because crypto is 24/7)
  - `sqlite3` (stdlib) — positions/candles/OI
  - `pytest` — tests
- **No third-party Delta SDK.** Roll a thin client; full control of auth and rate limits.

---

## 5. Folder Structure (target — build incrementally)

```
Bitcoin_paper_trading_bot/
├── .env.example              # template, no secrets
├── .env                      # gitignored, real keys
├── .gitignore                # ignore .env, data/, logs/, venv/, __pycache__/
├── README.md
├── CLAUDE.md                 # this file
├── requirements.txt
├── config/
│   ├── settings.py           # loads .env, exposes constants
│   └── strategies.yaml       # strategy params (thresholds etc.)
├── src/
│   ├── exchange/
│   │   ├── delta_client.py   # REST: auth signing, products, orders, balances
│   │   ├── delta_ws.py       # WebSocket: ticks, orderbook, OI
│   │   └── instruments.py    # Build BTC options universe (ATM ± N strikes, near expiries)
│   ├── data/
│   │   ├── candles.py        # OHLC build from ticks
│   │   ├── oi_tracker.py     # Periodic OI snapshots
│   │   └── storage.py        # SQLite read/write
│   ├── strategies/
│   │   ├── base.py           # Strategy interface (signal -> action)
│   │   └── __init__.py       # BTC-native strategies added in Layer 3
│   ├── risk/
│   │   ├── manager.py        # Portfolio-level limits
│   │   └── position_sizer.py # Per-trade sizing
│   ├── execution/
│   │   ├── paper_executor.py # Pure simulation (no API calls)
│   │   └── testnet_executor.py # Sends orders to Delta testnet
│   ├── alerts/
│   │   └── telegram.py
│   ├── reports/
│   │   └── eod.py            # "EOD" = chosen UTC daily cutoff (e.g. 00:00 UTC)
│   ├── scheduler.py          # APScheduler jobs
│   └── main.py               # Entry point — boots scheduler + WS listener
├── data/                     # gitignored
│   ├── btc_trading.db
│   └── logs/
└── tests/                    # mirror src/ structure
```

---

## 6. Phased Roadmap (6 Layers)

### Layer 1 — Foundation (week 1)
- Init repo, `.env.example`, `.gitignore`, `requirements.txt`, `README.md`
- Build `delta_client.py`: auth signing + `get_products()`, `get_ticker()`, `get_orderbook()`, `get_candles()`
- Build `storage.py`: SQLite schema for `instruments`, `candles`, `oi_snapshots`, `positions`, `orders`, `trades`
- Smoke test: pull live BTC option chain from testnet, store to DB
- **Done when:** option chain refresh writes rows to SQLite

### Layer 2 — Data Ingestion (week 2)
- `instruments.py`: pick universe — start with **BTC options at ATM ± 5 strikes**, **next 3 expiries**
- `delta_ws.py`: subscribe to mark price, ticker, OI updates for that universe
- `oi_tracker.py`: snapshot OI every 3 min (NIFTY cadence; revisit for BTC after observing data)
- `candles.py`: aggregate ticks to 1m / 5m / 15m
- **Done when:** continuous live data flows to DB with no gaps over 24h

### Layer 3 — Strategy Layer (weeks 3–4) — research first
- `base.py`: define interface `generate_signal(market_state) -> Signal | None`
- **Research candidates for BTC-native edge** before coding any real strategy:
  - Funding rate divergence (perp basis vs spot)
  - Options OI walls at round strikes (95k, 100k)
  - IV crush around daily expiry (Delta has daily BTC option expiries)
  - Liquidation cascade detection (Coinglass-style data)
- Build ONE placeholder strategy first to wire the pipeline end-to-end, then replace with researched ones
- Regime detection: BTC vol regimes — start with realized vol; consider Deribit DVOL/BVIV later
- **Done when:** signals generated and logged (no execution yet)

### Layer 4 — Risk & Paper Execution (week 5)
- `paper_executor.py`: pure in-memory fill simulator, uses live mark price, no API calls
- `risk/manager.py`: portfolio caps (max open positions, max delta exposure, daily loss cap)
- `position_sizer.py`: contract count from risk-per-trade
- **Done when:** end-to-end signal → paper fill → position tracked in DB

### Layer 5 — Alerts & Monitoring (week 6)
- New Telegram bot + channel (steps in §7)
- Entry/exit alerts, daily summary at chosen UTC cutoff
- Health check: heartbeat every 30 min — if scheduler stalls, alert
- **Done when:** Telegram pings on every signal, fill, and EOD

### Layer 6 — Trade Lifecycle & Testnet Promotion (weeks 7–8)
- SL/TP logic per strategy
- Promote from `paper_executor` to `testnet_executor` (real orders on testnet)
- Telegram commands: `/positions`, `/pnl`, `/pause`, `/resume`
- Soak on testnet for 2–4 weeks before any live capital discussion
- **Done when:** bot runs autonomously on testnet for 2+ weeks with full lifecycle and no manual intervention

---

## 7. Telegram Setup (10 minutes, do once)

1. Open Telegram → search `@BotFather`
2. `/newbot` → give name (e.g. `BTC Paper Trading Bot`) → username must end in `bot` (e.g. `RohithBtcPaperBot`)
3. Save the **bot token** → goes in `.env` as `TELEGRAM_BOT_TOKEN`
4. Create a **new private channel** (e.g. `BTC Paper Alerts`) — distinct from the NIFTY channel
5. Add the bot to channel as **Admin** with post-message permission
6. Get channel ID: send any message in the channel, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` — `chat.id` will be a negative number like `-1001234567890`. Save as `TELEGRAM_CHANNEL_ID` in `.env`

---

## 8. `.env.example` (create on first commit)

```
# Delta Exchange (testnet)
DELTA_API_KEY=your_testnet_api_key
DELTA_API_SECRET=your_testnet_api_secret
DELTA_BASE_URL=https://testnet-api.delta.exchange
DELTA_WS_URL=wss://testnet-socket.delta.exchange

# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHANNEL_ID=-1001234567890

# Runtime
ENV=dev
LOG_LEVEL=INFO
DB_PATH=data/btc_trading.db
```

---

## 9. Key Architectural Differences from NIFTY Bot

| Aspect | NIFTY bot | BTC bot |
|---|---|---|
| Market hours | 09:15–15:30 IST, Mon–Fri | 24/7 |
| Expiry day | Tuesday (NIFTY weekly) | Daily / weekly / monthly |
| Pre-market layer | Yes (Layer 4) | **N/A** — no pre-market in crypto |
| Session boundaries | Natural (market open/close) | **Artificial** — pick a UTC cutoff (e.g. 00:00 UTC) for "EOD" |
| Regime detection | Built for NIFTY vol | Must be re-tuned with BTC data |
| OI source | NSE via SmartAPI | Delta product API + WebSocket |
| Settlement | INR | USDT |

The 24/7 nature is the biggest architectural difference. Design the scheduler and "EOD" concept with that in mind from day one.

---

## 10. Operating Principles for Claude (in this project)

- This is paper trading. Bias toward **safety and observability** over speed of feature delivery.
- Never assume a Delta API behavior — read the docs or hit the testnet first.
- Every signal, fill, and rejection must be logged AND stored in SQLite. We need full audit trail.
- Don't add live-trading code paths until Layer 6 is complete and the user explicitly says go.
- If you find yourself writing strategy code in Layer 1 or 2, stop — that's Layer 3.
- If a Delta endpoint behaves differently from docs, write down the actual behavior in this file under a new "Delta API Quirks" section.

---

## 11. Delta API Quirks (observed, not from docs)

Append new entries here as we discover them. Each entry: where seen, what surprised us, what to do about it.

### Platform: Delta India vs Delta Global (CRITICAL)
- Delta operates two separate platforms: **Delta India** (`india.delta.exchange`) and **Delta Global** (`delta.exchange`). API keys, accounts, and balances are platform-specific — a key created on one platform returns `invalid_api_key` on the other. This was the root cause of all `invalid_api_key` failures during P1 Auth Gate (2026-06-09).
- **India testnet REST URL**: `https://cdn-ind.testnet.deltaex.org` ← use this in `.env`
- **India production REST URL**: `https://api.india.delta.exchange` ← for when live trading begins
- The old URL `https://testnet-api.delta.exchange` is the Global platform testnet. The docs now explicitly state it "cannot be used here" for India-platform keys.
- **WebSocket** `wss://testnet-socket.delta.exchange` remains valid for **public** subscriptions on India testnet — public WS streams have no auth so the platform separation does not affect them. Confirmed working through all of Layers 2–3a.

### REST `/v2/products`
- BTC perpetual symbol on India testnet is **`BTCUSD`** (not `BTCUSDT`). Spot is **`BTC_USDT`** (with underscore). **Correction 2026-06-11:** symbol was previously documented as `BTCUSDT`; live API probe confirmed it is `BTCUSD` (product_id=84, perpetual_futures). `GET /v2/tickers/BTCUSDT` returns `{"success": true, "result": null}`.
- Live BTC options follow `C-BTC-{strike}-{DDMMYY}` / `P-BTC-{strike}-{DDMMYY}` — e.g. `C-BTC-76500-010526` settles `2026-05-01T12:00:00Z`. Confirmed expiry format is **DDMMYY**, not YYMMDD. (The example in §3 has the date description reversed — keep the format note here.)
- Daily expiries settle at **12:00:00 UTC**.

### Testnet data quality
- **Open interest is always 0 on testnet** for BTC options (no real activity). Any OI-based strategy (OI walls, OI shifts) cannot be validated on testnet — must wait for live data.
- `close` (last trade price) on testnet can be stale for hours. Use `mark_price`.

### Research-integrity boundaries (CRITICAL — read before treating any collected data as evidence)
- **Testnet data is PIPELINE-VALIDATION ONLY, never research evidence.** Delta testnet has no real order flow: OI ≈ 0, quotes are economically synthetic (observed `ask_iv` as low as `0.000005`, degenerate bid/ask), `close` is stale for hours. Testnet data validates that the *collection plumbing* works (schema, cadence, coverage, rollover, exch_ts capture) — it does **NOT** validate any economic signal, IV level, smile shape, or funding/OI relationship. Genuine "evidence accumulation" in the roadmap (Evidence Library → Signal Validation → Evidence Accumulation → ML Readiness → Model Design) cannot begin until **mainnet** data is flowing AND the daily certifier reports green. Classify everything collected on testnet as plumbing-validation, not evidence.
- **The option_quotes corpus is ATM±5 / 3-expiry CONDITIONED — it is a sliding window, not a complete record.** `select_universe` (and therefore the WS subscription, and therefore `option_quotes`) only ever captures options that are within ATM ± 5 strikes across the nearest 3 expiries *at sample time*. An option's life before it enters that window and after it leaves (drifts OTM, or rolls past the 3rd expiry) is **absent from the corpus**. Consequences: you cannot reconstruct a full option lifecycle, a full volatility smile, or wing behavior from this data. Any analysis must treat the corpus as `P(observed | near-ATM and near-dated at sample time)`. This is structural (by design), not a bug — do not "fix" it by widening the universe without an explicit hypothesis that needs the wings (that would be scope expansion, gated).

### Dormant: funding_snapshots not wired (verified 2026-06-12, audit gap #1)
- **`funding_snapshots` is intentionally EMPTY at Layer 2d.** The live runtime (`lifecycle.py`) subscribes the WS to **only** `v2/ticker` over the option universe. There is **no `funding_rate` channel** and **no `BTCUSD` perp** in the subscription, so `_dispatch` never receives a `funding_rate` message → `MarketState.update_funding()` is never called → `funding_snapshots` is never written.
- The full write path exists and is correct (`update_funding` → `all_funding` → `write_funding_snapshot`) but is **unfed** in production — exercised by tests only. `BTCUSD` appears at runtime only in `get_btc_mark_price()` (a one-shot REST call to center the universe), which is **not** funding and is **not** persisted.
- **Do not treat funding as collected evidence.** Any prior statement implying "funding for BTCUSD is collecting" (including earlier review notes) is **incorrect** and retracted. The Binance/CCXT funding notes elsewhere in this file and in `docs/research/` refer to the *offline research corpus*, not this live collector.
- **Confirm on host:** `SELECT COUNT(*) FROM funding_snapshots;` → expect `0`.
- **Future work (deferred, NOT in scope now):** to actually collect live funding, subscribe a `funding_rate` channel + `BTCUSD` and first **probe** whether Delta India testnet publishes `funding_rate` frames over the public WS (unverified). This is a capture-path expansion — gated, do not implement without an explicit decision.

### Collection-quality observability (collection_metrics + exch_ts band)
- **`collection_metrics` table** (schema v3): one row per SnapshotWriter cycle — `ts, universe_size, coverage_pct, wrote, skipped, failed, reconcile_count, stale_count`. Makes coverage auditable from the DB alone; journald rotates, so per-cycle coverage that is not persisted here is lost. `coverage_pct` is a fraction in `[0.0, 1.0]`. `wrote/skipped/failed` are option_quote counts (funding excluded). Written best-effort: a metrics-write failure logs a warning but never aborts the quote write.
- **`stale_count`** (schema v3) is the freshness counterpart to `coverage_pct`. `coverage_pct` measures *ever-ticked-and-still-in-universe* and stays `1.0` during a ticker stall; `stale_count` = universe symbols with no tick within 30s at sample time. Read them together: `coverage_pct < 1.0` ⇒ a symbol **never** ticked (new/dead/unsubscribed); `coverage_pct = 1.0 AND stale_count > 0` ⇒ symbols ticked once but are **now stale** (the stall the old metric hid). A brief `stale_count > 0` right after a rollover is **normal** — new symbols count stale until their first tick; correlate with `reconcile_count` to suppress that transient. 30s matches HeartbeatService's operator-facing "N stale" line.
- **exch_ts sane band**: `exch_ts` is band-checked on write via `coerce_exch_ts()` to `[1e15, 1e16)` µs (≈ 2001..2286). Out-of-band values (e.g. Delta silently switching to milliseconds — the documented Binance failure mode) are stored as **NULL**, not as a wrong-magnitude number that would poison the `wall_ts − exch_ts` staleness proxy. SnapshotWriter emits one aggregated WARNING per cycle (`N/M exch_ts out of band`), never per-row, so a systematic format shift is loud without flooding the log.

### WebSocket
- URL: `wss://testnet-socket.delta.exchange` (confirmed working).
- Subscribe payload: `{"type":"subscribe","payload":{"channels":[{"name":"v2/ticker","symbols":[...]}]}}`.
- **Invalid symbols are silently accepted.** Subscription confirmation comes back as if everything is fine, but no data ever publishes. Burned us in Layer 2a (used `BTCUSDT` instead of `BTCUSD` — got "subscriptions" confirmation, then 30s recv timeout). **Always validate symbols against `/v2/products` before subscribing.**
- Confirmation message uses `type: "subscriptions"` (plural).
- `v2/ticker` message fields: `type`, `symbol`, `mark_price`, `close`, `timestamp`.
- **Timestamps are microseconds since epoch** (e.g. `1777316036302005`). Divide by 1_000_000 before `datetime.fromtimestamp()`.
- `mark_price` updates ~every 3 seconds on BTCUSD regardless of trade activity.
- `close` is the last trade price and can be stale for hours on testnet (low activity). **Use `mark_price` for signals, never `close`, on testnet.** Re-evaluate on mainnet.

### v2/ticker payload shape (option products)
- **All numeric fields are JSON strings**, not numbers. `mark_price`, IVs, Greeks, OI — all strings. Cast to `float` in the storage layer.
- **Exception: `timestamp` is a native int**, not a string. Verified 146/146 option tickers (2026-06-11 live capture). Value is microseconds since epoch (e.g. `1781169922396908`). Do NOT call `float()` on it before `int()` — use `int(float(x))` only as a fallback for the string case. The `_to_int()` helper in `storage.py` handles both cases safely.
- **Redundant `time` field**: every option ticker also carries a top-level `time` key containing an ISO-8601 UTC string (e.g. `"2026-06-10T09:24:11.9638336Z"`). This is the same instant as `timestamp` in human-readable form. It is not stored (the int `exch_ts` is canonical), but it is a free fallback for log readability if needed.
- **5 Greeks** (not 4): `greeks.{delta, gamma, theta, vega, rho}`.
- **IV is in `quotes` sub-dict**: `quotes.mark_iv`, `quotes.bid_iv`, `quotes.ask_iv`. Top-level `mark_vol` exists too (≈ same as `quotes.mark_iv`).
- **Top-of-book is in `quotes`**: `best_bid`, `best_ask`, `bid_size`, `ask_size`, `impact_mid_price`. No need to also subscribe to `l1_orderbook` — same data.
- **Spot price embedded in every option ticker** as top-level `spot_price` (BTC index). Saves a separate spot subscription.
- OI fields: `oi` (BTC), `oi_contracts` (count), `oi_value` (BTC notional), `oi_value_usd` (USD notional), `oi_change_usd_6h`. Some testnet options DO have non-zero OI (`oi_contracts > 0`), so don't assume 0 across the board — but volume is sparse.

### `mark_price` WS channel
- For `BTCUSD` perp on testnet: nearly silent (got 1 stale message in a 120s smoke). Use `v2/ticker` subscribed to `BTCUSD` instead — gives `mark_price` field at ~3s cadence reliably.

### Subscribe limits
- 64 symbols on a single `v2/ticker` channel subscription works cleanly (no chunking needed at this size).

### Authenticated REST schemas (observed 2026-06-09)
- **All four authenticated endpoints confirmed working**: `GET /v2/wallet/balances`, `GET /v2/orders`, `GET /v2/positions/margined`, `GET /v2/fills` — all return HTTP 200 on India testnet.
- **Rejection shape**: `{"error": {"code": "<error_code>"}, "success": false}` with HTTP 400. Observed codes: `negative_order_size` (size ≤ 0), `invalid_contract` (nonexistent product_id).
- **Wallet/balances**: `meta.net_equity` is the total equity string. `result[]` has one entry per asset (`USD`, `INR`, `BTC`, `ETH`, `REF_USD`). Key fields: `asset_symbol`, `balance`, `available_balance`, `blocked_margin`, `order_margin`, `position_margin`. **All values are strings.** Testnet account starts with 200 USD (`available_balance: "200"`).
- **BTC option contract units**: `contract_value = "0.001"` BTC, `contract_unit_currency = "BTC"`, `settling_asset.symbol = "USD"`. 1 contract = 0.001 BTC notional at the strike price. Confirmed on `C-BTC-65400-090626` (product_id 184953).
- **Order/fill/position full schemas**: not yet observable (fresh testnet account, no historical orders). Cursor-based pagination: `meta.after`, `meta.before`, `meta.limit`, `meta.total_count`. Schemas will be captured during Layer 4 / testnet order placement.
- **Order placement fields** (from docs + rejection probes): `product_id` (int), `size` (int, contracts), `side` (`"buy"` / `"sell"`), `order_type` (`"limit_order"` / `"market_order"`), `limit_price` (string, required for limit).

### Binance Vision OHLCV timestamp precision change (observed 2026-06-09, research corpus build)
- Binance Vision monthly klines CSV files **switched from milliseconds to microseconds** sometime in 2025. Files for 2024 and earlier use 13-digit millisecond epoch timestamps (e.g. `1735689600000`). Files for 2025 and later use 16-digit microsecond epoch timestamps (e.g. `1735689600000000`).
- **Symptom if unhandled:** all rows are silently trimmed by the month-bounds check because microsecond timestamps are 1000× larger than the millisecond window, producing a 0-row partition.
- **Fix in `ingest/binance_ohlcv.py`:** `_normalize()` detects microseconds by threshold (`max > 1_500_000_000_000_000`) and divides by 1000 before storing. Downstream code always receives millisecond timestamps.
- **What to watch for:** if Binance switches back or introduces nanoseconds, the same symptom (0-row partitions) will recur — check the manifest `rows` field after any re-ingest of recent months.

### Binance funding timestamp precision (observed 2026-06-09, research WI-4)
- `fetch_funding_rate_history` via CCXT returns `timestamp` values that are **not always exactly on 8-hour UTC grid boundaries**. Observed: most timestamps are exactly on grid (e.g., 1704067200000 = 2024-01-01 00:00:00.000 UTC), but some are off by **1 ms** (e.g., 1711900800001 vs expected 1711900800000 for 2024-03-31 16:00:00 UTC). In Q1 2024 data: 37 out of 273 records had non-zero remainders when divided by 28,800,000, all within 1 ms of the grid.
- **Implication for research code:** do not use `timestamp_ms % 28_800_000 == 0` as a validity check. Use `timestamp_ms % 28_800_000 <= 1` (or `>= 28_799_999`) instead (±1 ms tolerance). Similarly, consecutive-diff checks should use `abs(diff - 28_800_000) <= 1_000` (±1 second) rather than exact equality.
- **Implication for joins:** when joining funding data with OHLCV candles, round or snap funding `timestamp_ms` to the nearest 8h boundary before joining.
- The raw data is stored as-is (no snapping in the ingest layer) so downstream code owns the alignment decision.

### Live `unsubscribe` is best-effort (confirmed testnet, 2026-05-21)
- Delta testnet **accepts** the unsubscribe payload without socket error or protocol rejection.
- However, Delta **continues streaming ticks for "unsubscribed" symbols** for the remainder of the current TCP connection's lifetime. The exchange does not honour live mid-stream unsubscription.
- **Operational implication:** live unsubscribe should be treated as a hint, not a guarantee. The authoritative correction is reconnect — every `_run_once()` cycle calls `_current_channels()` (which reads `_subscribed_symbols`) and subscribes only the current intended set. Removed symbols will stop arriving after the next reconnect.
- **Architecture implication:** this validates the designed eventual-consistency model. Do not attempt to filter out ticks from "unsubscribed" symbols in the recv loop — it adds complexity for a transient window. Let reconnect be the source of truth.

### Deribit DVOL endpoint (research data, verified 2026-06-10)
- Endpoint: `GET https://www.deribit.com/api/v2/public/get_volatility_index_data?currency=BTC&start_timestamp=<ms>&end_timestamp=<ms>&resolution=1D` — **public, free, no auth**.
- Returns `result.data` as rows of `[timestamp_ms, open, high, low, close]` (DVOL in annualized vol **points**, e.g. 48.1 = 48.1%). Resolutions: `1`, `60`, `3600`, `43200`, `1D`.
- **BTC DVOL inception: 2021-03-24.** History contiguous daily through present; verified 0 gaps / 0 dupes / 0 null-or-zero closes over 2021-03-24..2026-06-10 (~1,905 rows). Range observed 32.4–156.2.
- **1,000-row cap per request.** The `continuation` field is returned but **does NOT page backward when passed back as a param** (re-returns the latest window). Paginate by explicit shrinking `end_timestamp` windows (≤1000 days each) and dedup by timestamp.
- Coverage vs research master (spot 2021-01-01..2026-05-31): ~1,894 joinable days (~96%); Jan–Mar 2021 has no DVOL → NaN → drop, never impute.

---

## 15. Stabilization & Operational Hardening Phase

**Active as of 2026-05-21.** Phase 3a is complete. The runtime has crossed an important maturity boundary: dynamic reconciliation exists, settlement rollover is operationally validated, and exchange-behavior constants are emerging. Operational intelligence is now more valuable than additional architecture.

### Current mode: observation and characterization — not feature expansion

During this phase, do not propose or implement new infrastructure. Propose observations, measurements, and CLAUDE.md §13/§14 updates instead.

**What to observe and document over multiple days:**
1. Long-duration runtime stability (uptime, crash frequency, unhandled exceptions)
2. Reconnect frequency and patterns (WS drop rate, recovery time, backoff behavior)
3. Memory and process stability (Python RSS growth over multi-hour sessions)
4. Scheduler cadence drift (APScheduler job timing accuracy over 24h+)
5. DB growth characteristics (row count growth rate, WAL behavior, lock contention)
6. Stale-symbol behavior (how long retired symbols linger in ticks after reconnect)
7. Reconcile frequency (how often universe diffs are non-empty vs. no-op)
8. Settlement behavior over multiple cycles (does the 12:02 lag repeat? does it vary?)
9. Overnight liquidity and IV degradation (mark_iv behavior during low-activity hours)
10. mark_price cadence consistency (does ~3s hold overnight? does it degrade?)
11. WS degradation and recovery patterns (partial outage, slow-tick, reconnect storm)
12. Telemetry noise (JSONL event volume, any spurious events during steady-state)
13. Data-quality anomalies (NaN Greeks, zero IV, mark_price spikes)
14. Coverage stability over long sessions (does coverage drift from 100%? when and why?)

### What is explicitly out of scope during this phase

- ML work of any kind
- Strategy complexity expansion
- Distributed infrastructure
- Tier 4 capture or retention hierarchy
- Aggressive optimization or premature scaling
- Phase 3b (Tier 1 ring buffer) — held at Rohith's gate

### Likely next major step (post-stabilization)

**Phase 3b — Tier 1 ring buffer.** Gated behind stabilization. Do not design or propose Phase 3b until Rohith explicitly opens the gate.

---

## 12. Immediate Next Actions for Rohith

1. Create Delta Exchange **testnet** account + generate API keys (§3)
2. Create new GitHub repo `bitcoin-paper-trading-bot` (private), `git init` here, push
3. Create new Telegram bot + private channel (§7)
4. Tell Claude in next conversation: "Start Layer 1"

Once those three things are done, this project starts building.

---

## 13. Runtime Operational Constants

Observed and validated during Phase 3a Step 3 live settlement soak (2026-05-21). These are not estimates — they are measured values from Delta testnet at the 12:00 UTC daily settlement boundary. Treat as design budgets for strategy gating, cooldown logic, stale-warning tuning, and event-capture policy.

| Constant | Observed | Design Budget |
|---|---|---|
| REST round-trip (chain refresh) | ~3.0s | 4s |
| Trigger → stable universe (end-to-end) | ~3.1s | 5s |
| Rollover symbol first-tick latency | <3s | 10s |
| Greek/IV stabilization on new rollover symbols | — (not directly measured) | 30s |
| Residual streaming after retirement | 1 tick observed at +8s | budget 1–15s |
| Settlement dead zone (settled but still in universe) | 12:00–12:05 UTC | gate entries by suffix + time |
| Early pre-settlement chain disappearance | up to 13 min before 12:00 UTC | treat as settled if absent from REST chain |
| ExpiryContext crossing detection lag (production, 5-min cadence) | up to 2 min | 12:05 cron corrects this |
| Post-settlement scheduled chain refresh | ~47 min (hourly cron) | 60 min |
| v2/ticker mark_price cadence | ~3s | 3–5s |

**How to apply these in strategy code (Layer 3+):**
- After any `_reconcile_count` change: suppress coverage-based signals for **10s**.
- After any `_reconcile_count` change: suppress Greek/IV-dependent signals on new symbols for **30s**.
- Between 11:58 UTC and 12:05 UTC: block new entries on any instrument where `suffix == today_ddmmyy`.
- If a symbol is absent from the chain REST response: treat as retired regardless of formal expiry time.
- Never trust `coverage_pct()` as a signal gate without checking `_reconcile_count` stability.

---

## 14. Settlement & Rollover Exchange Behavior

Observed properties of Delta Exchange at the 12:00 UTC daily settlement boundary. These describe how the exchange and runtime interact — not how the code is designed, but what actually happens in production.

### REST is authoritative immediately after 12:00 UTC

The Delta chain REST endpoint returns the post-settlement universe with zero observable lag at the settlement boundary. There is no transition window, no stale data, no partial response. Immediately after 12:00 UTC, the REST chain response excludes today's expired instruments and reflects the new ATM-centered universe. This strongly validates the architectural decision: **REST is the sole retirement authority**. ExpiryContext crossing detection exists to trigger the refresh; the REST response makes the retirement decision.

### Pre-settlement early disappearance (up to ~13 min before)

Economically dead options (near-zero mark value, bid=0, ask=0) may silently disappear from the chain REST response **up to ~13 minutes before** the formal 12:00 UTC settlement time. Observed: 2 symbols dropped from chain at 11:47 UTC (active: 64 → 62), with ExpiryContext reporting `settled=0` (no crossing registered for them). Delta appears to delist zero-value instruments before their nominal expiry. **Implication:** do not rely on the formal expiry timestamp as the sole gate for retirement. If a symbol is absent from the REST chain, treat it as retired regardless of clock time.

### Residual streaming after retirement (1–15s)

After `reconcile_universe()` retires a symbol, Delta continues streaming v2/ticker ticks for that symbol for a brief window (observed: 1 tick at +8s post-retirement). This is a consequence of the eventual-consistency WS model — live unsubscribe is best-effort and Delta ignores it (§11). In practice, residual ticks collapse quickly for settled instruments because Delta stops generating mark_price updates for expired products. The `update_ticker()` non-resurrection guard handles all residual ticks correctly. **Do not add recv-loop filtering** — the guard is sufficient.

### resubscribe-before-reconcile creates implicit WS warmup overlap

In `_on_universe_change()`, `ws_manager.resubscribe()` fires **before** `market_state.reconcile_universe()`. This is not an accident — it creates a ~3s WS warmup window during the REST call. By the time `reconcile_universe()` adds new rollover symbols to `_universe`, those symbols have already been subscribed to the WS and Delta has had time to deliver first ticks at its ~3s cadence. The result: **coverage does not dip during rollover**. All 26 new rollover symbols showed 100% coverage immediately after reconcile. **This ordering must be preserved**. Reversing it (reconcile-then-resubscribe) would create a coverage dip window equal to one WS tick cadence (~3s) for every new symbol.

### The 12:00–12:05 UTC dead zone

Between 12:00 UTC (settlement) and 12:05 UTC (when `expiry_cycle_eod` cron fires `emit_cycle_summary()`), today's expired symbols remain in the universe. The REST chain already excludes them, but the runtime universe has not yet been reconciled. During this 5-minute window:
- Ticks for expired symbols continue to arrive (Delta streams them).
- `coverage_pct()` may appear healthy but includes stale expired-symbol tickers.
- `all_tickers()` still returns mark prices for expired instruments.

**Strategy gating rule:** block all new entries on instruments where `symbol.endswith(today_ddmmyy)` between 11:58 UTC and 12:06 UTC (58 min into the settlement approach, with 1 min margin past the cron). This is a hard gate — do not rely on universe membership alone during this window.

### No-coverage-dip rollover (confirmed)

Zero coverage dip was observed during the settlement-triggered rollover, across both the validate script and the background monitoring soak. This is a direct consequence of the resubscribe-before-reconcile warmup overlap described above, combined with Delta's fast first-tick delivery for newly subscribed symbols. For strategy architecture: rollover is not a blackout event. Coverage-based signal gating does not need to treat 12:00–12:10 UTC as a forced pause — only the dead-zone entry gate (§ above) applies.

---

## 16. Evidence Library — Governance Architecture Notes

Governance session: 2026-06-13. Implemented and audited. This section records frozen design decisions and deferred-but-designed proposals. Do not implement deferred items without explicit gate from Rohith.

### Implemented (2026-06-13)

**R1 — DSR/BH-FDR NULL enforcement** (`lifecycle.check_validated_preconditions()`): NULL or NaN for either governance stat is a hard failure. There is no silent bypass. All thresholds are read from `thresholds.yaml validated.<role>` — no hardcoded fallbacks. Changing a threshold in the YAML takes effect on the next `report.py --check` run.

**R2 — Bootstrap transition log** (`ledger.db state_transitions`): 11 bootstrap transitions inserted via `migrate_bootstrap_transitions.py` (actor=bootstrap, ts=2026-06-13T00:00:00). Append-only; already run; do not re-run.

**R3 — Arc validation** (`record.append_transition()`): Every transition attempt is validated against `lifecycle.ALLOWED_TRANSITIONS` before any DB write. Invalid arcs raise `ValueError` and never enter the ledger.

**R4 — "sealed" provenance removed** (`record._VALID_PROVENANCE`): Valid values are `{"offline-corpus", "live-forward", "mainnet"}` only. "sealed" was undefined and would have bypassed the Validated non-offline provenance requirement.

**Proposal 2 — `validate_registry()` delegates to authoritative gate** (`lifecycle.validate_registry()`): The `if state == "validated":` block previously had a hardcoded provenance-only check that diverged from `check_validated_preconditions()`. It now delegates to that function, so `report.py --check` catches provenance, DSR, and BH-FDR violations for any manually-edited Validated state. Regression-tested with 5 scenarios (2026-06-13).

### Proposal 1: Pairwise independence enforcement — IMPLEMENTED 2026-07-19

**Status: Implemented.** Reference: `docs/governance/pairwise_independence_schema_reference.md`.
Landed while zero families are Validated, per this section's own instruction to
implement before the first promotion window rather than on promotion day.

All six spec steps implemented as frozen (table with `CHECK (a < b)`,
`append_pairwise_r`, `check_validated_independence`, wiring into
`check_validated_preconditions` and `validate_registry`, operator workflow).
Three things the frozen text did not settle, resolved and documented rather
than guessed silently:

1. **Also wired into `transition_governance.validate_transition` (TG-PRE).**
   The spec predates PR-8; `validate_registry` alone would only catch a
   violation *after* a promotion. This is the spec's intent applied to the
   current architecture, not an expansion.
2. **Missing coverage = hard failure.** A Validated peer with no recorded pair
   blocks promotion, following R1's NULL discipline. Silence would make the
   rule satisfiable by recording nothing.
3. **Comparison on `|r|`.** `r = -0.95` is an inverse duplicate; raw-`r`
   comparison would admit it.

Migration was purely additive (empty table via the existing idempotent
schema-ensure; all 70 runs / 142 gate_results / 13 transitions / 26 receipts
byte-identical, chain valid). Fastest rollback lever: remove `max_pairwise_r`
from `thresholds.yaml` — the check becomes a no-op with no code change.

**Original frozen design follows, retained verbatim for reference.**

**What it does:** `max_pairwise_r: 0.70` is declared in `thresholds.yaml validated.edge` and `validated.feature` but currently enforced nowhere. Proposal 1 would enforce it structurally.

**Reference architecture (implement exactly as designed when the gate opens):**

1. **New `pairwise_r` table in `ledger.db`**:
   ```sql
   CREATE TABLE IF NOT EXISTS pairwise_r (
       id          INTEGER PRIMARY KEY AUTOINCREMENT,
       ts          TEXT NOT NULL,
       family_id_a TEXT NOT NULL,
       family_id_b TEXT NOT NULL,
       r           REAL NOT NULL,
       method      TEXT NOT NULL,
       actor       TEXT NOT NULL DEFAULT 'operator',
       CHECK (family_id_a < family_id_b)   -- canonical ordering, prevents duplicates
   );
   ```
   Append-only (no UPDATEs, no DELETEs). The `CHECK` constraint enforces `a < b` lexicographic ordering — always pass the lesser ID as `a`.

2. **New function `record.append_pairwise_r(db_path, family_id_a, family_id_b, r, method, actor)`**: Inserts one row. Raises `ValueError` if `family_id_a >= family_id_b` (caller must sort).

3. **New function `lifecycle.check_validated_independence(family_id, family_meta, thresholds, pairwise_rows)`**: Looks up all rows in `pairwise_r` involving `family_id` against any currently-Validated family. Returns `(ok, [errors])`. Errors cite the specific correlated pair and the observed `r`.

4. **Wire into `check_validated_preconditions()`**: Accept an optional `pairwise_rows` argument (default `[]`). Call `check_validated_independence()` if the argument is non-empty. This keeps the function testable without a DB.

5. **Wire into `validate_registry()`**: When checking `state == "validated"`, load `pairwise_r` rows from the ledger and pass them to `check_validated_preconditions()`.

6. **Operator workflow**: Before promoting a Candidate to Validated, compute correlation of its returns against all existing Validated-family returns. Record the result with `record.append_pairwise_r()`. Then run `report.py --check`. The check will fail if any pair exceeds `max_pairwise_r`.

**Why deferred:** No Validated families exist yet (2026-06-13). Building pairwise infrastructure before there are any pairs to check has no benefit and risks implementing against a hypothetical. The design is fully specified — implementation is a mechanical translation of this spec.

**When to implement:** As soon as a first Candidate enters the realistic Validated promotion window (live-forward evidence + DSR computed). Do not wait until promotion day — implement at least one full `report.py --check` cycle before the promotion attempt.

### Governance-frozen constraints

These are not reopened without a formal governance session. Do not propose relaxing them:
- `n_trials = 34` for the full Phase B battery (DSR denominator is fixed at this value)
- `min_dsr_prob = 0.50` for both edge and feature roles
- `max_bh_p = 0.10` for both roles
- `max_pairwise_r = 0.70` at Validated (deferred enforcement, but threshold is frozen)
- Validated is the ML-readiness gating state (not Promotion-ready)
- Testnet data is pipeline-validation only — no research evidence accumulation until mainnet

### H-IVRV (ivrv_vrp_spread) feature-candidate admission (2026-06-19)

First registry entry **outside** the original Phase B 34-signal seeding. H-IVRV is the DVOL-family **HV-2 VRP Spread** (DVOL/100 − RV30, contrarian on the trailing tercile), wired into forward collection in `run_daily.py` as a **two-input** signal (spot closes + DVOL via `run_signal(aux_series=...)`).

- **Role = feature, state = candidate.** It is **edge-ineligible**: on the lockbox-disciplined **train slice** (2021-01..2025-05) the canonical Sharpe is **0.265 < 0.30** (`candidate.edge` floor). The full-sample Sharpe (0.329) would pass Edge, but computing it **spends the sealed lockbox**, so it is not used. It clears the **feature** gate: sensitivity 100%, OOS +0.50, WF 60%, clone_r 0.37.
- **Lockbox 2025-06-01..2026-05-31 remains SEALED** — the final pre-promotion OOS test; do not spend it for interim gating.
- **IS DSR = 0.1646, BH-p = 0.2893** (n_trials=**34** frozen, train slice, hold_days=1) — fail Validated in-sample, as expected; the Validated gate is evaluated on forward returns. These are **not** the `dvol_family_report.json` values (0.161 used n_trials=38 / DSR_CONF=0.95, a discovery screen).
- **RESOLVED (2026-07-22) — n_trials 34 vs 38:** canonical denominator is **34** (frozen). Reconciliation: `docs/governance/n_trials_denominator_reconciliation.md`. `38` was only ever the DVOL discovery screen (`run_dvol_family.py`, with `DSR_CONF=0.95`, not governance `min_dsr_prob=0.50`) plus one display-only proxy; it never entered the ledger (all 70 runs store 34) and had zero gating consequence (H-IVRV fails the ≥0.50 IS bar at both 0.1646/n=34 and 0.161/n=38; the Validated gate uses forward returns). Growing the denominator was **considered and not adopted** — it would reopen a frozen constraint and is re-openable only by a future session that explicitly puts the freeze on the table. No migration/replay impact.
- **First pairwise-independence test case:** at IS max corr 0.37 it should clear `max_pairwise_r = 0.70`, making it the natural first exercise of the deferred **Proposal 1** enforcement at any future promotion.
- **P&L semantics:** spot-directional VRP timing, **not** literal short-vol P&L.
- **DVOL freshness:** refreshed daily 00:05 UTC by `daily_ingest.py` (non-critical); a stale/missing DVOL day yields ivrv `unavailable` (fail-safe skip), never blocking H5/G1/G3.
- **Ledger admission:** `evidence_library/migrate_ivrv_admission.py` (discovery → candidate) must be run once to produce the `pinned_run`; until then `report.py --check` reports one expected error for this family.

---

## 17. Governance Receipt Architecture — Rollout Log

Design: `docs/governance/governance_receipts.md` (depends on
`evidence_reporting_architecture.md`, `data_completeness_gate.md`,
`contract_versioning.md`, same session, 2026-07-13). Objective: every
irreversible governance decision reconstructable, by hash, years later.
Rollout is staged PR-by-PR; do not implement a later PR's scope early.

### Governance Architecture — PR-1 Complete (2026-07-13)

**Governance Receipt Foundation** — merged to `main` via
`feat/governance-receipt-foundation`. Implements the receipt data model,
canonical JSON + decimal-string serialization, content-addressed hashing
(`sha256_lf`, byte-identical convention to `pre_reg_guard._sha256_lf`),
validation against the derived/discretionary taxonomy, replay-foundation
interfaces (`verify`/`replay`/`fold_reconstruct`), and an append-only
SQLite ledger (`governance_receipts` table, DB-level `RAISE(ABORT,...)`
triggers blocking `UPDATE`/`DELETE`).

- Code: `research/evidence_library/receipts.py`,
  `research/evidence_library/receipt_ledger.py`.
- Tests: `tests/test_governance_receipts.py` (63 tests: canonicalization,
  hash stability, decimal canonicalization, validation, replay foundation,
  duplicate detection, append-only enforcement, corruption/chain-tamper
  detection, registry queries).
- Reference: `docs/governance/receipt_schema_reference.md`.
- **Foundation only — nothing on the platform emits a receipt yet.** No
  existing governance behavior, table, or call path was changed.
- **Noted for PR-2 (not a blocker for PR-1):** add `receipt_schema_version`
  to emitted receipts before receipt emission begins. Since no receipts are
  emitted yet, this is a clean addition with no compatibility cost.
- **Deferred to later PRs (per the design's own scope boundaries):**
  signing (Ed25519 key registry), `--verify-chain` CLI + daily cron,
  anchoring (git/Telegram/backup-manifest head publication), and wiring
  actual decision paths — `family.transition`/`family.promote` into
  `record.append_transition`, `readiness.verdict` into the Data
  Completeness Gate, `contract.activate`/`.retire` into the Contract
  Registry, unifying `data_readiness` as a receipt projection.

### Governance Architecture — PR-2 Complete (2026-07-13)

**Contract Registry (storage layer)** — merged to `main` via
`feat/contract-registry`. Implements the immutable, hash-addressed
`contract_registry` archive per `docs/governance/contract_versioning.md`
§2/§11 (PR-A), deliberately narrowed to persistence only: canonical
serialization + hashing (reuses `receipts.sha256_lf`/`canonical_json` from
PR-1 verbatim — no reimplementation), the contract metadata model, registry
querying (by hash, version, contract_id/"type", latest draft, all
historically-activated versions), validation, and hash-only replay
resolution (`resolve_by_hash()` — no filesystem access anywhere in the
module, enforced by a source-scanning test).

- Code: `research/evidence_library/contract_registry.py`.
- Tests: `tests/test_contract_registry.py` (54 tests).
- Reference: `docs/governance/contract_registry_schema_reference.md`.
- **Storage only — no `activate()`/`supersede()`/`retire()` orchestration,
  no tamper detection, no receipt emission.** `phase` vocabulary includes
  `draft` (a narrowing beyond the original PR-A DDL comment) so the archive
  can hold any lifecycle point without an activation tool existing yet.
  Append-only triggers currently block **all** `UPDATE`/`DELETE` — the
  spec's one documented "phase only advances" exception does not exist yet
  because nothing needs it yet; PR-3 must introduce that exception
  explicitly rather than loosen the trigger silently.
- **Carried into PR-3 (per review, not implemented here):** every activated
  contract must permanently reference the governance receipt that
  activated it — new nullable `contract_registry.activated_receipt_id`
  column, populated only by `activate()`. Strengthens lineage and gives
  `replay()` a direct contract→receipt join.
- Full suite post-merge: 469 passed, same 14 pre-existing failures
  (missing `apscheduler` in this dev interpreter, unrelated), no
  regressions, no merge conflicts.

### Governance Architecture — PR-3 Complete (2026-07-13)

**Contract Lifecycle (activation, retirement, tamper detection)** — merged
to `main` via `feat/contract-lifecycle`. Implements `activate_contract()` /
`retire_contract()` as atomic `BEGIN IMMEDIATE` transactions on top of
PR-2's archive: monotonic version assignment computed under the write lock
(concurrent activators for the same `contract_id` serialize, never race),
supersede-the-incumbent, and `CONTRACT_TAMPER` enforcement at the
**database layer** (`contract_registry.trg_contracts_guard_update` —
frozen fields, write-once lineage fields, only-documented-arcs phase
transitions), independent of any Python code path.

- Code: `research/evidence_library/contract_lifecycle.py`. Modifies
  `research/evidence_library/contract_registry.py` exactly as PR-2's own
  comments said a later PR would have to (new `activated_receipt_id`
  column + idempotent migration; blanket update-block trigger replaced with
  the guarded one).
- Tests: `tests/test_contract_lifecycle.py` (48 tests, incl. real-thread
  concurrency, re-run 5x clean). One PR-2 test in
  `tests/test_contract_registry.py` repointed at a still-illegal operation
  (mutating `body`), since the guarded trigger now legitimately permits
  `phase='retired'` as a direct `UPDATE` — documented, deliberate, not a
  regression.
- Reference: `docs/governance/contract_lifecycle_schema_reference.md`.
- **Dependency check performed before implementation** (per the task's
  explicit stop condition): receipt emission is cleanly separable from
  lifecycle management. `activated_receipt_id` is written as `NULL`; no
  `governance_receipts`/`receipt_ledger.py` reference anywhere in this PR.
- **Carried into PR-4 (per review, not implemented here):** an explicit
  receipt-lifecycle status (or equivalent invariant) so `active` +
  `activated_receipt_id IS NULL` can be told apart between legacy
  activation (permanent), pending receipt emission (transient), a
  migration state, and an implementation defect (the only alertable case).
- Full suite post-merge: 517 passed, same 14 pre-existing failures
  (missing `apscheduler`, unrelated), no regressions, no merge conflicts.

### Governance Architecture — PR-4 Complete (2026-07-13)

**Contract Governance Receipt Emission (`contract.activate` /
`contract.retire`)** — merged to `main` via `feat/contract-receipts`. Wires
PR-1's receipt ledger into PR-3's lifecycle as one atomic transaction per
event: activate/retire → generate receipt → validate → persist → store
receipt reference → commit, any failure rolls back everything (verified by
forcing failure at three different points, including the final
reference-storage step alone).

- Code: `research/evidence_library/contract_receipts.py`. Required minimal
  refactors, not behavior changes, of two existing modules so a contract
  write and a receipt write could share one SQLite transaction:
  `receipt_ledger.py` gained `append_receipt_on_connection()`;
  `contract_lifecycle.py` gained `_activate_on_connection()` /
  `_retire_on_connection()`. All PR-1 (63) and PR-3 (48) tests pass
  unmodified against both refactors.
- Tests: `tests/test_contract_receipts.py` (46 tests). One line changed in
  `tests/test_contract_registry.py`'s `make_record()` helper (defaults the
  new required status field, mirroring what `append_contract()` already
  defaults automatically).
- Reference: `docs/governance/contract_receipts_schema_reference.md`.
- **Resolved the PR-3 carry-over**: `activation_receipt_status` /
  `retirement_receipt_status` ∈ `{PENDING_RECEIPT, RECORDED, LEGACY_IMPORT,
  BOOTSTRAP}` replace bare-`NULL` ambiguity on `activated_receipt_id` /
  `retired_receipt_id`. `LEGACY_IMPORT` is written **exclusively** by
  `contract_registry.init_db()`'s one-time migration backfill — never a
  live code path — resolving an extra distinction (legacy vs. a live
  receipt-less call post-PR-4) discovered during implementation that the
  original four-state framing hadn't separated.
- **Migration-asymmetry note** (`contract_registry_schema_reference.md`
  PR-4 section): SQLite validates a `CHECK` added via `ALTER TABLE ADD
  COLUMN` against every existing row immediately (confirmed empirically),
  so the cross-column status invariants are DB-enforced only on freshly
  created databases; a migrated (pre-PR-4) database relies on
  `validate_record()` in Python for the same invariants. Deliberate,
  documented trade-off — a full table-rebuild migration was considered and
  rejected as disproportionate risk for a platform with no live production
  data yet.
- **Dependency check performed before implementation**: no receipt type
  beyond `contract.activate`/`.retire` was found necessary — both were
  already in `receipts.DECISION_TAXONOMY` since PR-1, unused until now.
- **Carried into PR-5 (per review, not implemented here):** every
  governance receipt should support deterministic chain verification and
  future anchoring with no change to receipt contents. Confirmed
  empirically before merge that this already holds for free — contract
  receipts go through `append_receipt_on_connection()` and pass
  `receipt_ledger.verify_chain()` with zero new code; PR-5's job is the
  operational surface (`--verify-chain` CLI/cron, the three
  zero-infrastructure anchors from `governance_receipts.md` §3.3), not
  chain-linking logic.
- Full suite post-merge: 563 passed, same 14 pre-existing failures
  (missing `apscheduler`, unrelated), no regressions, no merge conflicts.

### Governance Architecture — PR-5 Complete (2026-07-13): Evidence Matrix & Governance Reporting

**Not the same track as the "PR-5" referenced just above** (that one is the
contract-receipts chain-verification/anchoring work, still unimplemented).
This PR-5 is `docs/governance/evidence_reporting_architecture.md`'s
Evidence Matrix with Pinned-Decision Anchor — merged to `main` via
`feat/evidence-matrix-reporting`. Bundles that design's own internal PR-1
through PR-4 (schema/lanes, point-in-time substrate, reports, invariants)
into one reviewable unit; its own internal PR-5 ("Operationalise": systemd
timer, daily commit, heartbeat/alert wiring) is explicitly out of scope.

- Code: `research/evidence_library/record.py` (`runs.run_kind`, the frozen
  `(provenance, run_kind)` lane table, `current_run_per_lane` view with an
  explicit `run_id` tiebreak, `derive_state_at()`/`derive_all_states_at()`
  reading only the append-only `state_transitions` ledger, never
  `registry.yaml`), `lifecycle.py` (the one flagged state-machine touch —
  `can_transition()` permits `X -> X` re-pin structurally, gated on a
  `justifying_run_id` by `append_transition()`; invariants C1-C9),
  `report.py` (full rewrite: `build_context()` as the single pure-snapshot
  point every generator consumes, the two-table Decision-anchors /
  Evidence-by-lane board, `--as-of` reconstruction, the four-way rendering
  distinction — no evidence / insufficient / not-computed / n/a, never a
  bare `-` — `compute_flags()`, new `evidence_timeline.md`),
  `migrate_run_kind.py` (one-time backfill, idempotent, `--commit`-gated).
- **Deleted (not deprecated), per spec**: `get_all_latest_runs()` and
  `latest_run_per_family` — the cross-lane "latest run" selection the
  whole design exists to eliminate. Zero stale callers confirmed by
  repo-wide search.
- **Real data touched, with backups**: `migrate_run_kind.py --commit` run
  against the real `ledger.db` (70 rows classified: 35 discovery / 35
  confirmatory offline-corpus; matches the documented known-correct L0/L1
  split exactly). `registry.yaml: provenance` surgically removed from all
  35 entries (line-level deletion; a first attempt via `yaml.safe_dump`
  was reverted because it destroyed every comment and reformatted the
  whole file). The real registry now passes all nine invariants.
- Tests: `tests/test_evidence_lanes.py`, `test_evidence_pointintime.py`,
  `test_evidence_report.py` (94 tests): lane validation, deterministic
  tiebreak, backfill idempotency/correctness, `derive_state_at`
  reconstruction, re-pin recording, all four rendering-rule distinctions,
  every flag's trigger/non-trigger, the offline-immutability acceptance
  test (inject a simulated L2 run, decision-anchor evidence columns stay
  byte-identical), `--as-of` reconstruction, one targeted failure per
  invariant plus the real registry passing all nine.
- Reference: `docs/governance/evidence_matrix_schema_reference.md`.
- **Two literal-spec ambiguities resolved and documented, not guessed
  silently**: invariants C5 and C9, read literally, false-positive on the
  real registry's 22 untouched-`discovery` families (which have
  `pinned_run` set to their evaluation run but zero transitions, and were
  never meant to be governed yet). Both were scoped to apply only where a
  transition history actually exists — the alternative literal reading
  would have required fabricating governance history to pass, which the
  task's "must not change governance decisions" constraint forbids.
- **Independent pre-merge audit** (read-only, zero files modified):
  registry.yaml diff proven semantically lossless via parsed-YAML
  structural comparison, not just line-diff; governance regression audit
  comparing the live ledger against the pre-migration backup byte-for-byte
  on every evidence column and the full `state_transitions` table;
  historical replay re-verified identical pre/post migration at two
  `as_of` points; migration idempotency and backup `PRAGMA integrity_check`
  verified live; lane independence demonstrated on a throwaway ledger copy
  with the real files hash-verified untouched; all nine invariants
  reconfirmed via SQL/YAML spot-checks independent of `validate_registry()`.
- **Rollback is manual, by design, accepted at review**: no `--rollback`
  flag exists; recovery is `cp ledger.pre-run_kind-migration.<ts>.db
  ledger.db`, and the backup's fidelity was verified, not merely asserted.
- Full suite post-merge: 657 passed, same 14 pre-existing failures
  (missing `apscheduler`, unrelated), no regressions, no merge conflicts.

### Governance Architecture — PR-6 Complete (2026-07-13): Evidence Library Governance Integration

Wires PR-1's receipt ledger into the pre-existing Evidence Library, for
exactly the two decision types already reserved in
`receipts.DECISION_TAXONOMY` since PR-1: `evidence.admit` /
`evidence.reject`. Not the contract-receipts track's still-pending "PR-5"
(chain verification/anchoring, referenced under PR-4's entry above) — a
different track again, reusing the "PR-6" outer label.

- **Dependency check performed before implementation** (per the task's
  explicit stop condition): `evidence.admit`'s abstract rule
  (`governance_receipts.md` §2) is keyed to the Data Completeness Gate's
  readiness verdicts, whose engine was never built (only the Contract
  *Registry*, which versions contracts, not a contract *evaluator*, exists).
  Building it is out of scope. Proceeded on a narrower, honest basis
  instead of stopping: `evidence.admit`/`.reject` attest gate-check
  pass/fail via the pre-existing `lifecycle.check_candidate_gate()` — the
  same function that already produced the real ledger's entire
  candidate/disqualified split — not input-data completeness. Stated
  explicitly, not implied.
- Code: `research/evidence_library/evidence_governance.py`. `admit_evidence`/
  `reject_evidence` refuse to record a false verdict (raise if the gate
  outcome doesn't match the call). `pin_evidence`/`repin_evidence` call
  `record.append_transition()` **verbatim, unchanged** — no new receipt
  type for the transition itself, per the "no state transition receipts"
  exclusion; the new value is governance validation wrapped around the
  existing call (a pin now requires the justifying run to already carry
  the matching admission/rejection receipt).
- **First real exercise of PR-1's `derived` replay path**: `evidence.admit`/
  `.reject` are `derived` (unlike PR-4's `discretionary` contract
  receipts, which never needed a `rule_fn`). `verify_admission_receipt()`
  injects a real `rule_fn` that re-runs `check_candidate_gate()` and
  compares bit-for-bit; verified to both replay clean against original
  thresholds and correctly `diverge` against mutated ones.
- Tests: `tests/test_evidence_governance.py` (70 tests, see correction below).
- Reference: `docs/governance/evidence_library_governance_schema_reference.md`.
- **Real ledger backfilled**: `migrate_bootstrap_evidence_admission.py`
  (mirrors `migrate_run_kind.py`'s dry-run/`--commit`/backup pattern) wrote
  13 `attestation='bootstrap'` receipts (9 `evidence.admit`, 4
  `evidence.reject`) for the real ledger's 13 pre-existing pinned runs —
  the same "bootstrap honesty" precedent PR-1's own
  `migrate_bootstrap_transitions.py` set. Idempotent re-run: 0 pending.
  `receipt_ledger.verify_chain()`: all 13 valid. No separate audit-note
  file was needed — each bootstrap receipt carries its own
  `attestation_note` and is itself the permanent, hash-chained audit
  record, a stronger trail than PR-5's companion `.audit.txt` pattern.
- `report.py` `run_check()` gained an additive `EG1`/`EG2`-prefixed check
  (default on, skipped for `--as-of` — inherently "now"-only), appended
  after `C1`–`C9`, never modifying them. Real ledger: `--check` still
  passes clean post-backfill.

**Post-review architectural correction (same PR, before merge):** the
first implementation called `lifecycle.check_candidate_gate()` directly by
name in three places, and `verify_admission_receipt()` ignored a receipt's
own recorded `rule_id`/`engine_version` at replay time, always
re-executing whatever `check_candidate_gate` happened to be current —
violating `contract_versioning.md` §3's engine-pinning discipline (a
future in-place edit to that function would have silently changed what
historical receipts replay to, while `replay()` kept reporting success).
Corrected: `AdmissibilityRule` (a `typing.Protocol`) is now the genuine
dependency — `evidence_governance.py` calls `check_candidate_gate` from
exactly one place (`CandidateGateRule.evaluate()`, verified byte-for-byte
behavior-identical, checked by an AST-based structural test, not a
docstring-foolable substring match). Replay resolves the exact historical
rule via `get_rule(receipt.rule.rule_id, receipt.rule.engine_version)`
from a `register_rule()`-populated registry, raising `UnknownRuleError`
(never silently substituting the default) if unresolvable — demonstrated
with an adversarial case where the substituted default would have
produced the *opposite* verdict from what was recorded. A standalone rule
defined entirely outside the module was registered and replayed correctly
alongside `CandidateGateRule` in the same process, proving the
substitution point PR-7's Data Completeness Gate will use actually works.
14 tests added for the correction; all 56 pre-correction tests pass
unmodified.
- Full suite post-merge: 727 passed, same 14 pre-existing failures
  (missing `apscheduler`, unrelated), no regressions, no merge conflicts.
  Real ledger re-verified post-merge: `--check` clean, receipt chain valid
  (13 receipts), untouched by the correction (bootstrap receipts refuse
  replay before `rule_fn` runs, so never depended on the fix).

### Governance Architecture — PR-7 Complete (2026-07-14): Data Completeness Framework

**The generic readiness engine** — merged to `main` via
`feat/data-completeness-framework`. Implements `data_completeness_gate.md`
§12's PR-1 (core engine) and `contract_versioning.md` §11's PR-B
(engine/check versioning) + §5 (the three resolution operations) + §2.3
(tamper detection), on top of PR-1's receipt ledger and PR-2/3/4's contract
registry.

- Code: new package `research/dq/` (11 modules): `states`/`policy` (the
  8 states, 6 actions, and the (state, criticality) matrix — "the entire
  safety argument"), `contract` (contracts as versioned, hashable **data**;
  no completeness logic), `observation` (metadata only, and the replay
  substrate), `checks` (6 pure check families, clock injected, frozen by
  versioned `check_id`), `engine`, `rules` (a generic, shape-agnostic
  `RuleRegistry`), `probes`, `contract_loader` (the 3 resolution ops as 3
  functions; deliberately **no** `get_contract()`), `readiness` (receipts +
  `data_readiness` as a receipt **projection**, resolving
  `governance_receipts.md`'s own deferral), `validation`.
- Tests: `tests/test_dq_engine.py`, `test_dq_contract.py`,
  `test_dq_receipts.py`, `test_dq_probes.py` (187 tests).
- Reference: `docs/governance/data_completeness_framework_schema_reference.md`.
- **Pipeline-agnostic — H6 is the motivating example, not the scope.** Zero
  pipeline concepts in executable code (AST-verified with docstrings
  stripped). **No adopters yet**; adoption (`corpus_daily`, the readiness-aware
  `_already_processed()`) is deferred.
- **Independent pre-merge audit** (read-only): replay proven
  filesystem-independent by *poisoning* `Path.read_text`/`read_bytes` and
  replaying anyway; history proven pinned against a tightened contract (v1
  READY replays READY while v2 says INCOMPLETE_DATA); replay proven to refuse
  an unregistered historical rule rather than substitute one that would have
  **inverted** the verdict; the per-input short-circuit brute-forced over the
  full state lattice — **0 action downgrades in 1,080 scenarios**, the one
  state difference naming the root cause (SCHEMA_MISMATCH) over a downstream
  symptom and yielding the *stricter* action.
- **One additive line to existing code**: `receipts.ACTIONS` gains
  `"evaluate"`. `readiness.verdict` had been in `DECISION_TAXONOMY` since
  PR-1 but no verb described what a readiness evaluation *does*.
  `"admit"`/`"reject"` were deliberately NOT reused: a READY verdict means
  **complete**, not **admissible** (§7 G5 — independent gates).
- **Three documented deviations** from a literal spec reading (schema ref
  §15), all resolved toward the spec's stated intent: per-input rather than
  global short-circuit (a global one would let `UNKNOWN` mask
  `SCHEMA_MISMATCH` and downgrade a required HALT to BLOCK); `STALE_DATA`
  required → `RETRY` (the matrix cell describes a sequence across time, which
  a pure `(state, criticality) -> action` cannot encode); `history_window`
  applied per-input at parse time.
- **Known cosmetic finding, not fixed**: `checks.py:213` names `rv_21d/atr14`
  in an operator-facing `detail` string — the one place a platform concept
  surfaces in framework output. Behaviour is unaffected.
- Full suite post-merge: 914 passed, same 14 pre-existing failures.

### Governance Architecture — PR-8 Complete (2026-07-17): State Transition Governance

Makes the platform's state-transition system **consume** the governance
architecture PR-1..PR-7 built — merged to `main` via
`feat/state-transition-governance`. Introduces **no new governance concept**:
`family.transition` / `.promote` / `.demote` were reserved as
**discretionary** in `DECISION_TAXONOMY` since PR-1 and are finally emitted.

- **Dependency check performed before implementation** (per the task's stop
  condition): integrates with **no architectural change** to PR-1..PR-7. The
  taxonomy, actions, `family` subject kind, `from_state`/`to_state`, and an
  **indexed** `Subject.version_or_hash` (which pins the exact
  `state_transitions.id` a receipt governs) all already existed — so **no
  schema change** to PR-5's table and no taxonomy change.
- Code: `research/evidence_library/transition_governance.py`,
  `migrate_bootstrap_transition_receipts.py`. One precedented refactor to
  `record.py`: `append_transition_on_connection()` extracted (mirroring PR-4's
  `append_receipt_on_connection`) so a transition and its receipt can share one
  transaction — and so the arc/re-pin/grandfathering rules are **not**
  duplicated into a second enforcement point (§7.3).
- Tests: `tests/test_transition_governance.py` (74). Reference:
  `docs/governance/transition_governance_schema_reference.md`.
- **Promotion is verified, never derived.** Discretionary ⇒ `replay()` is
  `envelope-verified`. No `auto_promote()` exists (asserted by test); a
  **service actor is refused** (the platform must not promote itself); a
  promotion **survives a later threshold change** — proven adversarially:
  after tightening `min_dsr_prob` to 0.999 `validate_transition()` says the
  promotion would not be granted today, yet replay stays `ok`. *Evidence
  admissibility is a computation; promotion is a judgment.*
- **`report.py --check` gains a third additive hook** (TG1–TG6), appended
  after C1–C9 and EG1/EG2, never renumbering either.
- **Review-required correction, made before merge — the TOCTOU race.** The
  architecture audit reproduced two concurrent transitioners both reading
  `discovery` outside the lock, both validating against it, and both writing:
  a permanently inconsistent chain in an **append-only** table (TG4 detected it
  after the fact; nothing could repair it). Fixed by moving the authoritative
  derivation **inside** `BEGIN IMMEDIATE` on the locked connection —
  verbatim PR-3's `contract_lifecycle.next_version()` contract.
  **A second bug surfaced from the fix's own 5-writer test**: `ts` was also
  stamped before the lock, so a writer acquiring the lock *second* could carry
  an *earlier* timestamp, making TG4 report a broken chain for a correctly
  serialised ledger. `ts` is now stamped under the lock.
  `promote_with_receipt()` also passes its observed `current` as `from_state`,
  so a concurrent move makes a promotion **refuse** rather than be silently
  re-classified as a legal `validated→validated` re-pin.
  Guarded by real-thread concurrency tests (2/5-writer + promotion variant,
  re-run 5× clean) and 2 AST guards asserting `BEGIN IMMEDIATE` precedes the
  authoritative derivation and that the connection-less `derive_state_at()`
  never appears in the write path.
- **Real ledger backfilled**: 13 bootstrap transition receipts (13 → 26).
  Chain valid, `--check` clean, idempotent re-run reports 0 pending. **A
  lineage bug was caught during the migration itself**: the first run wrote
  receipts with no `inputs[]`, so receipts reconstructed
  `pinned_run_id=None` while the ledger returned the real run — **TG5 did not
  catch it** (it compares only `state`). The `justifying_run_id` is a fact
  already on the transition row, so carrying it fabricates nothing. Ledger
  restored from its integrity-checked backup and re-migrated; the two
  derivations now agree on **state and run**, asserted by a test against the
  real ledger.
- **Deferred to PR-9, with the reason recorded**: comparing the recorded
  `policy_hash` at replay (the audit's optional item). Not trivial —
  `_policy_hash()` mixes the state machine *and* thresholds into one digest and
  replay receives no thresholds, so it needs either a signature change or a
  separate recorded `state_machine_hash` (the right, additive design). No data
  is lost meanwhile: the hash is already recorded, so history stays diagnosable
  retroactively.
- Full suite post-merge: **988 passed**, same 14 pre-existing failures
  (12 missing `apscheduler`, 2 date-dependent expiry tests), no regressions,
  no merge conflicts. Real ledger re-verified post-merge: `--check` clean,
  chain valid (26), TG1–TG6 clean, both derivations agree, file untouched.

## 18. Program P1 — Governance Store Consolidation — Engineering Record

Permanent record of Program P1 (Governance Store Consolidation), scoped by
the approved P1 Implementation Specification and Gap Analysis (2026-07-25).
Program P1 targets ledger schema integrity, durability/replication, and an
honest assurance-level declaration — see the Gap Analysis for the full
12-project breakdown across Workstreams A–D. Increments are merged only after
explicit review and approval; this section is updated on each merge, never
retroactively rewritten.

### Increment 1 Complete (2026-07-25): Ledger Backup Freshness Monitoring

Closes the confirmed Workstream C3 gap: `scripts/healthcheck.py`'s backup
freshness check covered only `btc_trading_*.db`, leaving `ledger.db` — the
artifact ADR-0001 §12 BK1 calls "the only truly irreplaceable asset" — with
no automated freshness signal.

- **Code:** `scripts/healthcheck.py` (`check_backup_freshness()` generalized
  with `label`/`check_name` params, defaults preserve prior behavior exactly;
  new `--ledger-backup-dir` CLI arg; `main()` runs a second, ledger-labeled
  check). `src/api/config.py` (`ApiSettings.ledger_backup_dir`, symmetric with
  `backup_dir`, no default). `src/api/services.py` (`runtime_health()` runs
  the same ledger-labeled check, keeping the CLI and read-only API in sync).
- **Consequence, not scope change:** `tests/test_api.py`'s two direct
  `ApiSettings(...)` fixtures required the new field — flagged as a deviation
  from the approved file list before being made, resolved by explicit
  authorization (extend `ApiSettings` symmetrically rather than add a
  dataclass-only default to avoid touching tests).
- **Tests:** `tests/test_healthcheck.py` (new, 10 tests — first-ever coverage
  for this module). Full suite excluding `test_api.py` (pre-existing,
  unrelated `fastapi`-environment gap — no local conda env has the API's
  dependency set, which lives in a separate `venv-api`): 1285 passed, 0
  regressions.
- **No deployment change required** — the new check's default path matches
  `scripts/backup.py`'s actual write location, so the already-deployed
  `btcbot-healthcheck.timer` picks it up with no redeploy action.
- **Discovered technical debt (not fixed, carried forward):** no local
  conda env can run `tests/test_api.py`; `check_backup_freshness()` has no
  CRITICAL tier for either DB, only OK/WARNING (deliberate, matched existing
  pattern); the off-host replication leg (`scripts/offhost_backup.py`) still
  has no freshness/success monitoring signal.

### Increment 2 Complete (2026-07-25): Restore-Path Regression Coverage

Closes the confirmed Workstream C2 gap: `scripts/restore.py` — the script
that swaps the ledger back into service during disaster recovery — had zero
test coverage, unlike `scripts/backup.py` (`tests/test_backup.py`).

- **Tests only, no source change:** `tests/test_restore.py` (new, 21 tests),
  mirroring `tests/test_backup.py`'s discipline — real SQLite DBs and real
  file operations throughout, mocking only the `subprocess`/systemd boundary.
  Covers `_sha256()`, `_integrity_ok()`, `_systemctl()` (success, non-zero
  exit, missing-binary graceful skip, unexpected exception), the happy path
  against a **real** `backup.run_backup()` artifact, SHA-256 manifest
  verification (mismatch aborts before swap; missing manifest warns but
  proceeds), backup-file integrity, post-swap integrity failure (original
  stays recoverable via `.pre-restore`), full service stop/start
  orchestration (including the documented "start failure after successful
  swap still returns 0" branch), and `main()`'s CLI wiring end to end.
- **Implementation correction found while writing tests (test-construction
  only, no `restore.py` change):** simulating "swap produces a corrupt
  destination" by appending bytes past EOF does not reliably fail SQLite's
  `integrity_check` (it tolerates trailing bytes past the recorded page
  count); corrected to zero the file's magic header instead.
- **Pre-existing, unrelated full-suite failure investigated before merge:**
  `tests/test_evidence_report.py::TestFlags::test_pin_stale_flag`. Root
  cause: the test creates two same-lane runs via back-to-back
  `datetime.now()` calls with no forced separation; when the two `run_ts`
  values tie, `current_run_per_lane`'s explicit tiebreak (`run_id DESC`, a
  random UUID comparison) can select either run, making the test's outcome
  non-deterministic under timing/load. Confirmed via (a) zero import-level
  intersection between the governance-report dependency chain and any file
  this program has touched, and (b) empirical reproduction — 8/8 passed in
  isolation, then 1 fail then 1 clean pass across two identical-code
  full-suite runs. **Not fixed** (out of program scope); logged as
  discovered technical debt against `tests/test_evidence_report.py`, unrelated
  to Program P1.
- Full suite excluding `test_api.py`: 1305–1306 passed (the pin-stale flake
  aside), 18 skipped, 0 regressions attributable to this increment.

### Increment 3 Complete (2026-07-25): Schema Assertion and Entry-Point Protection

Closes the highest-severity confirmed gap in Program P1: `report.py::run_check()`
called `lifecycle.validate_registry()` directly and unguarded — the exact code
path that crashed in production at Step 0d (`lifecycle.py` C1's
`r["run_kind"]` on a `sqlite3.Row`, raising `IndexError` on a pre-migration
ledger missing that column).

- **Code:** `research/evidence_library/governance_verification.py` — a new,
  purely-declarative `SCHEMA_FINGERPRINT` manifest (5 entries: `runs.run_kind`,
  `state_transitions`, `gate_results`, `governance_receipts` — all hard/error
  — and `pairwise_r` at warning severity, since `record.get_pairwise_r()`
  already tolerates its absence) and `check_schema_fingerprint(db_path,
  as_of=None)`, a read-only `PRAGMA table_info`/`sqlite_master` introspection
  that inspects **only** what the manifest names — additional/unknown tables
  or columns are never flagged. Wired as the first step of
  `verify_governance()`, short-circuiting the rest of the sweep (with an
  explanatory `GV-SKIP`) on any hard gap, never raising. `report.py` gained
  one new import and one early call in `run_check()`, before
  `validate_registry()`: a hard gap is reported by name and returns
  immediately, with **no call to `validate_registry()` and no schema
  mutation** — "assert, never repair" (ADR-P1-1) preserved by construction,
  not by convention.
- **Design alternative considered and rejected:** reordering the existing
  idempotent `evidence_governance.init_db()` (which already does a guarded
  `ALTER TABLE runs ADD COLUMN run_kind`) to run before `validate_registry()`
  would also have fixed the crash, with less new code. Rejected: it would
  make `report.py --check` — documented elsewhere as a read-only consumer —
  silently mutate production schema as a side effect of a diagnostic command,
  contradicting the already-approved "assert, never repair" decision.
- **Real regression found and fixed before merge, not after:** the first
  version required `governance_receipts` unconditionally, breaking
  `tests/test_evidence_governance.py::test_report_check_as_of_skips_evidence_governance`
  — a historical `--as-of` run legitimately never touches that table (both
  entry points already skip the receipt-consuming EG/TG subsystems for
  `as_of is not None`). Fixed by adding an `as_of_none_only` flag to the
  affected manifest entry and threading `as_of` through both call sites;
  locked in with a dedicated regression test
  (`test_governance_receipts_not_required_when_as_of_given`).
- **Tests:** `tests/test_governance_verification.py` (+16: 9 direct
  fingerprint tests including the unknown-structure regression and the
  as_of-conditionality regression, 4 `report.py::run_check()` integration
  tests, 3 real-ledger checks). Full suite: **1319 passed, 18 skipped, 0
  failed** (the previously-investigated `test_pin_stale_flag` flake did not
  recur in this run — consistent with it being an unrelated timing issue).
- **Read-only contract independently verified**, not just asserted by test:
  a standalone script ran `report.py --check` against the real production-
  shaped dev ledger and diffed `sqlite_master` before/after — byte-identical,
  exit 0, 35 families (9 candidate / 22 discovery / 4 disqualified),
  matching the known real ledger state.
- **No rollback exercised** — purely additive (one new function, two call
  sites in existing functions, no removed logic); a `git revert` requires no
  data/schema rollback since nothing is mutated by the new code's presence.

### Midpoint Engineering Review (2026-07-25)

After Increments 1–3, reassessed the remaining backlog. No completed
increment altered any P1 architectural assumption (verified against
ADR-P1-1 through ADR-P1-5 individually). Two favorable, previously-invisible
couplings identified: Increment 3's schema fingerprint makes Increment 4's
pre-validation harness both easier to build and more informative to run;
Increment 2's `restore.py` coverage strengthens confidence in Increment 6's
eventual off-host activation. Governance decision: execute Increment 4 next,
then Increment 6, then Increment 7, keeping Increment 5 last (revising the
original suggested order, which had Increment 6 before 4).

### Increment 4 Complete (2026-07-26): Local Pre-Validation Harness

Closes the direct ADR-P1-4 prerequisite for Increment 5: proves, on a
disposable copy of the real local ledger, that the D-0-determined migration
sequence — `migrate_run_kind.py` → `migrate_bootstrap_evidence_admission.py`
→ `migrate_bootstrap_transition_receipts.py`, invoked via their real,
unmodified CLI — closes the historically-documented production drift
cleanly and idempotently.

- **Tests only, no source change:** `tests/test_migration_harness.py` (new,
  12 tests). Builds the synthetic pre-migration ledger by copying the real
  local ledger and stripping it to the documented drift shape (missing
  `runs.run_kind`, `governance_receipts`, `pairwise_r`; dropped
  `current_run_per_lane`/`candidate_gate_status`/`has_live_evidence` views).
  Verifies GV-SCHEMA and `report.py --check` both name the gap before
  migration with zero mutation; runs the real migration sequence via
  subprocess; verifies GV-SCHEMA clean, `report.py --check` clean (35
  families: 9/22/4), `verify_chain` ok (26 receipts), and all three views
  correctly recreated; re-runs the full sequence a second time and proves
  it is a true no-op (zero new writes, zero new backup files, identical
  state). Full transcript: `docs/ops/migration-harness-dry-run-2026-07-26.md`.
- **Real, previously-undocumented artifact discovered:** a fourth view,
  `latest_run_per_family`, exists on the real ledger — a stale leftover from
  before its deletion at PR-5 (`record.py` explicitly documents it as
  "DELETED, not deprecated"; no current code creates or queries it). It
  blocked the `runs` table rebuild and was dropped as a mechanical
  necessity, deliberately **not** recreated (dead schema, out of scope).
  Logged as discovered technical debt, not fixed.
- **Local-only, by construction:** every migration-script invocation passes
  an explicit `--db`/`--registry` at a `tmp_path` copy; an assertion before
  every invocation refuses any path resolving to the real ledger; a module-
  scoped autouse fixture hashes the real ledger before and confirms it
  byte-identical after all 12 tests. Skips entirely if the real local
  ledger is absent (gitignored, absent in CI).
- **Pre-existing, unrelated flake recurred in a sibling form, not fixed:**
  the full suite hit `tests/test_evidence_lanes.py::TestCurrentRunPerLane::
  test_selects_latest_within_lane` once — the same root-cause class as the
  `test_pin_stale_flag` flake investigated at Increment 2 (a timestamp tie
  in `current_run_per_lane`'s `run_id DESC` tiebreak, from two back-to-back
  `datetime.now()` calls with no forced separation). Confirmed unrelated:
  zero file changes to `record.py`/`lifecycle.py`/`test_evidence_lanes.py`
  this session, 6/6 passed in isolation, and a second full-suite run passed
  clean. Not fixed — out of P1 scope.
- Full suite excluding `test_api.py`: 1330–1331 passed (the flake aside), 18
  skipped, 0 regressions attributable to this increment.

### Increment 6 Complete (2026-07-26): Off-Host Backup Monitoring (repository scope)

Closes the confirmed Workstream C gap logged at Increment 1: *"the off-host
replication leg (`scripts/offhost_backup.py`) still has no freshness/success
monitoring signal."* `scripts/backup.py`'s off-host transfer failure is
deliberately non-fatal (the local plaintext backup is the fast restore path),
which meant a broken off-host leg previously had **no persisted signal at
all** — only a stderr line inside a `oneshot` unit's journal.

- **Code:** `scripts/backup.py` — `_write_offhost_status()` writes a
  `.offhost-status.json` sidecar into each backup directory after every
  off-host transfer attempt (ok / failed / not_configured), carrying
  `last_success_at` forward across runs so a host that attempts-and-fails
  daily still shows growing staleness rather than a perpetually-fresh
  timestamp. `_apply_retention()` now also prunes `.age`/`.age-manifest.json`
  alongside their expired plaintext, closing the on-host ciphertext
  accumulation gap noted in `docs/ops/offhost-backup.md` §7.
  `scripts/healthcheck.py` — new `check_offhost_freshness()`, wired into
  `main()` for both the trading and ledger backup directories.
  `src/api/services.py` — `runtime_health()` runs the same two checks,
  preserving CLI/API parity established at Increment 1.
- **Severity model** (new — first CRITICAL tier on any backup-family check):
  silent (INFO) until off-host is actually configured; OK under 48h since
  last success; WARNING at ≥48h or on a failed latest attempt; CRITICAL at
  ≥7 days stale or if off-host is configured but has **never** succeeded even
  once. Mirrors the existing three-tier integrity model in
  `offhost_backup.py`'s own docstring (Tier 1 here; remote `verify` and the
  decrypt drill remain Tiers 2/3, both still manual).
- **Deliberately no new `ApiSettings` field** — the sidecar path derives from
  the existing `backup_dir`/`ledger_backup_dir`, avoiding a repeat of
  Increment 1's forced `tests/test_api.py` edit (that file still cannot be
  collected in any local conda env — pre-existing `fastapi` gap).
- **Tests:** `tests/test_offhost_backup.py` (+7, `TestOffhostStatusSidecar`),
  `tests/test_backup.py` (+3, retention pruning of `.age` artifacts),
  `tests/test_healthcheck.py` (+9, full severity matrix + label isolation).
  One test-construction bug found and fixed before merge: driving two real
  `run_backup()` calls back-to-back collided on `VACUUM INTO`'s same-second
  output filename (unrelated to the sidecar itself); rewritten to exercise
  `_write_offhost_status()` directly with controlled timestamps.
- **Scope deliberately narrowed, per the approved plan's own gating and the
  "minimum necessary changes" instruction:** `deploy/btcbot-backup.service`
  (`--encrypt` activation) is **not touched** — it remains gated behind the
  host-side sequence in `docs/ops/offhost-backup.md` §8, none of which can be
  performed or verified from this session. Three items from the approved
  plan's open recommendations were deferred rather than silently decided:
  `.env` disaster-recovery documentation (→ Increment 7, consolidated docs),
  a weekly automated remote-`verify` timer (Tier 2 — no code change needed,
  but adds a new systemd unit, deferred to keep this increment's deployment
  footprint at zero new units), and the pre-existing `--dry-run` retention
  label cosmetic bug at `backup.py` (dry-run only, no production effect,
  logged as discovered technical debt, not fixed).
- **Full suite:** one pre-existing, unrelated failure investigated and
  confirmed independent before merge —
  `research/forward_evidence/tests/test_atr_expansion.py::TestDecisionFnAtr::
  test_DFA07_cross_validation_vs_signals_py` fails only in full-suite runs
  (passes in isolation) due to a `sys.modules['engine']` name collision
  between `research/dq/engine.py` (PR-7's Data Completeness Framework) and
  `research/evidence/engine.py` (the Phase B signals engine) — reproduced
  **identically on a clean `git stash -u` checkout of HEAD with zero changes
  from this session** (1 failed / 1456 passed / 18 skipped both before and
  after). Not fixed — pre-existing, unrelated, out of P1 scope. With this
  increment's changes: 1529 passed, 19 skipped, same single unrelated
  failure, 0 regressions attributable to this increment.
- **Production impact of this commit alone:** zero behavior change on
  deploy. Encryption stays off; nothing ships off-host; the only observable
  effect is an inert `.offhost-status.json` sidecar per backup directory and
  two new healthcheck entries that report INFO until a host operator
  completes the separate, gated activation sequence (plan §4, `offhost-
  backup.md` §8) and only then adds `--encrypt` to the unit.
- **Host-side activation (target provisioning, key generation/custody, the
  `--encrypt` unit change, and the full decrypt drill) is unstarted** — it is
  Rohith's operational work, tracked in the Increment 6 plan and
  `docs/ops/offhost-backup.md` §8/§9, not part of this repository commit.

### Increment 7 Complete (2026-07-26): Assurance Declaration, Archival, and Final Documentation

Closes Program P1's documentation scope. Three new files, four surgical
edits to existing documents — no code, no test, no systemd unit touched.

- **New:** `docs/adr/0002-p1-governance-store-consolidation.md` — formal
  home for `ADR-P1-1` through `ADR-P1-5`, previously cited by name in
  shipped code (`governance_verification.py`, `report.py`) with no
  repository record to resolve to. Extends ADR-0001; does not supersede it.
- **New:** `docs/governance/p1_assurance_declaration.md` — the program's
  bounded closure statement. Four-level vocabulary (DEMONSTRATED /
  DEMONSTRATED (LOCAL) / DESIGNED, NOT ACTIVATED / NOT ESTABLISHED). Nine
  claims, two of which — production ledger migration and BK3 off-host
  durability — are stated as **NOT ESTABLISHED**, not implied by omission.
  Includes an explicit non-claims section and five reopening triggers.
- **New:** `research/evidence_library/MIGRATIONS.md` — declarative
  inventory of all eight `migrate_*.py` scripts (the three-script P1 D-0
  sequence plus five pre-existing family-specific/historical scripts).
  Records why none are physically archived yet: the D-0 three are invoked
  at a fixed path by `tests/test_migration_harness.py`, named directly in
  `report.py`/`record.py` comments under ADR-P1-1, and described (accurately,
  as history) across fifteen `docs/governance/*.md` files. Archival is
  deferred to a future decision, gated on Increment 5 completing on
  production with a clean GV-SCHEMA result there.
- **Modified:** `docs/adr/0001-infrastructure-architecture.md` — four
  surgical edits: header cross-reference to ADR-0002; §12 BK3 status note
  (repo-side monitoring done, BK3 itself still open); §17 gains a ninth
  preserved invariant (assert-never-repair, ADR-P1-1); §20 open items 1/3/4
  annotated with current status (item 3, `.env` DR, resolved).
- **Modified:** `docs/ops/offhost-backup.md` — new §6.1, the `.env`
  disaster-recovery procedure deferred at Increment 6 (ADR §20.3), using
  the same `age` recipient/identity as database backups; §9 item 2 updated
  to resolved.
- **Modified:** `docs/runbook.md` — one pointer block to the assurance
  declaration, ADR-0002, and the migrations manifest. No content
  duplicated.
- **D-1 resolved** (the one blocking decision surfaced during planning):
  `CLAUDE.md` was gitignored and had never been committed, meaning every
  cross-reference into this §18 record from a new ADR would dangle on a
  fresh clone or on the production host — directly undermining the
  "cross-reference, don't duplicate" instruction this increment was given.
  Rohith chose to un-ignore and commit it. `.gitignore`'s `CLAUDE.md` line
  is removed as part of this increment; the decision to actually run
  `git commit` for `.gitignore`/`CLAUDE.md` (and for the rest of Increments
  1–7's still-uncommitted working-tree state) is deliberately left to
  Rohith, consistent with this session's standing git policy of never
  committing without an explicit, in-the-moment instruction to do so.
- **Verification:** documentation-only change; full regression suite
  re-run to confirm zero effect — unchanged from the Increment 6 baseline
  (same pre-existing, unrelated `engine`-module-collision failure; see
  Increment 6 entry above). `report.py --check` and `receipt_ledger.
  verify_chain()` re-confirmed clean on the real local ledger, unaffected
  by any documentation edit (ADR-0001 §17.4).
- **Not done, flagged rather than acted on (outside this increment's
  scope):** Increments 1–6's code/test changes and this increment's own
  new/modified files remain uncommitted working-tree state on one machine.
  This was named as risk R6 at planning and remains true after
  implementation — committing it is Rohith's call.

### Remaining open items (Program P1, as of Increment 7)

Program P1's documentation scope (Increments 1–4, 6, 7) is complete.
**Increment 5 (production migration) remains unstarted and is the only
remaining item** — still blocked pending Rohith's explicit authorization,
de-risked by Increment 3 (named schema diagnostics) and Increment 4 (the
migration sequence proven clean and idempotent against a local, disposable
copy). Increment 6's host-side off-host-backup activation (target
provisioning, key generation/custody, `--encrypt` deployment, decrypt
drill) is separately unstarted and is host-side operator work, not a
queued engineering increment. See
`docs/governance/p1_assurance_declaration.md` for the bounded statement of
what is and is not established as of this entry.

---

## 19. Post-P1 Maintenance Backlog

Technical debt discovered during or after Program P1, tracked for future
work but explicitly **not** part of Program P1's own increment sequence
(§18) or its Go/No-Go gating for Increment 5. Entries here are logged, not
acted on, until separately scoped and approved. First entry recorded
2026-07-26; ID sequence starts at 002 as given — 001 is not tracked in
this repository.

### TD-P1-002 — WAL-aware restore support

| Field | Value |
|---|---|
| Priority | Medium |
| Program | Post-P1 Maintenance |
| Status | Open — logged, not scoped or approved for implementation |

**Current state:** `scripts/restore.py`'s swap step (`shutil.copy2`) copies
only the single `.db` file. It never checkpoints or clears `-wal`/`-shm`
sidecar files at the destination.

**Desired state:** `restore.py` becomes fully WAL-aware — checkpoints the
source before reading it, clears stale sidecars at the destination before
and after the swap, and validates the result accounts for WAL state rather
than assuming a clean single-file swap is sufficient.

**Why this is tracked:** Discovered during the Increment 5 production
migration runbook's WAL investigation (2026-07-26). `research/evidence_library/ledger.db`
is confirmed WAL-mode in production — `scripts/backup.py:235` sets
`PRAGMA journal_mode=WAL` on it directly during every `--ledger`/`--all`
backup run (`btcbot-backup.timer`, daily 02:00 UTC), independently
corroborated by `deploy/btcbot-api.service`'s own comment already calling
it a "WAL database." `tests/test_restore.py` has zero test coverage for a
live-WAL-sidecar restore scenario, and the one restore drill performed
(`docs/ops/restore-drill-2026-07-19.md`) happened to run against a
database with no `-wal` file present ("clean checkpoint") — the gap was
never actually exercised, only missed by circumstance.

**Interim mitigation (already in place, not a substitute for this ticket):**
`docs/ops/production-migration-runbook-increment5.md` §5 works around the
gap at the runbook level — explicit `PRAGMA wal_checkpoint(TRUNCATE)` +
manual `-wal`/`-shm` removal before every `cp`/`restore.py` invocation in
Increment 5's specific rollback path. This protects the one-time migration
only. It does not fix `restore.py` itself, which remains unsafe by default
for any *other* WAL-mode restore (the BK2 quarterly drill, a future ad hoc
recovery) unless the operator remembers to checkpoint manually first.

**Not fixed as part of this entry** — logging only, per instruction. Scope
this properly (schema-ensure interaction, `tests/test_restore.py`
coverage for a live-WAL scenario, `docs/ops/offhost-backup.md`'s decrypt
path which also produces a plain `.db` file that could exhibit the same
gap on its own eventual restore) before implementing.
