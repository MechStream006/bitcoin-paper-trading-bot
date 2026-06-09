# BTC Research Lake (`research/`)

Offline historical-research workspace. **Isolated by design:** this tree imports
nothing from `src/`, never reads or writes the live `data/btc_trading.db`, and
runs only on the local research box — not the VPS.

See `docs/research/consolidated_roadmap.md` for the authoritative plan. This
directory implements **Phase A.1 only** (free data acquisition + storage +
validation + DuckDB harness). Phases B+ are not started until explicitly approved.

## Scope of A.1

Free-tier datasets only:
- BTC spot 1m OHLCV (Binance)
- BTC perp 1m OHLCV (Binance USDT-M)
- Funding rate history (Binance, via CCXT)
- Macro event calendar (hand-curated CSV)

Options / OI / liquidation data is **A.2**, gated behind a Phase D.1 signal — not built here.

## Layout

```
research/
├── config/sources.yaml        # what to ingest (no secrets)
├── data/
│   ├── raw/        (gitignored) immutable, as-downloaded Parquet
│   ├── derived/    (gitignored) regenerable computed outputs
│   └── calendar/   (tracked)    hand-curated macro_events.csv
├── ingest/                     # WI-3..WI-5  (not yet built)
├── storage/                    # WI-2, WI-6  (not yet built)
├── quality/                    # WI-7        (not yet built)
└── smoke_test_phaseA1.py       # WI-8        (not yet built)
```

## Setup

```powershell
conda activate btc_bot
pip install -r research/requirements-research.txt
```

## Status

| WI | Item | Status |
|----|------|--------|
| WI-1 | Scaffolding & config | ✅ done |
| WI-2 | Storage conventions (lake/duck) | ⬜ pending |
| WI-3 | Spot + perp OHLCV ingestion | ⬜ pending |
| WI-4 | Funding ingestion | ⬜ pending |
| WI-5 | Macro event calendar | ⬜ pending |
| WI-6 | Provenance manifest | ⬜ pending |
| WI-7 | Quality gauntlet | ⬜ pending |
| WI-8 | Smoke test | ⬜ pending |
