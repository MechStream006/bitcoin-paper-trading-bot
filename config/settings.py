"""Loads .env and exposes runtime constants."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DELTA_API_KEY = os.getenv("DELTA_API_KEY", "")
DELTA_API_SECRET = os.getenv("DELTA_API_SECRET", "")
DELTA_BASE_URL = os.getenv("DELTA_BASE_URL", "https://cdn-ind.testnet.deltaex.org")
DELTA_WS_URL = os.getenv("DELTA_WS_URL", "wss://testnet-socket.delta.exchange")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "")

ENV = os.getenv("ENV", "dev")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
DB_PATH = ROOT / os.getenv("DB_PATH", "data/btc_trading.db")

# Platform discriminator — "testnet" or "mainnet".
# Default is "testnet" (safe-fail: evidence never silently promoted to mainnet
# status by misconfiguration). Set PLATFORM=mainnet in .env only after the
# mainnet collector is validated and the certifier thresholds are calibrated.
PLATFORM = os.getenv("PLATFORM", "testnet")
