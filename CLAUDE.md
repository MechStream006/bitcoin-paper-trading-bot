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

- **Python 3.11+**
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

## 11. Immediate Next Actions for Rohith

1. Create Delta Exchange **testnet** account + generate API keys (§3)
2. Create new GitHub repo `bitcoin-paper-trading-bot` (private), `git init` here, push
3. Create new Telegram bot + private channel (§7)
4. Tell Claude in next conversation: "Start Layer 1"

Once those three things are done, this project starts building.
