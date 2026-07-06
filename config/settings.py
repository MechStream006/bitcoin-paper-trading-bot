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

# Path to the daily forward-evidence DB (written by btcbot-fwd-hg.service /
# run_daily.py).  The heartbeat reads this READ-ONLY to surface the daily
# signal-collection cycle (latest run_date, per-signal status, fn_hash). Empty
# string disables the forward section of the heartbeat. Default points at the
# conventional research path so production picks it up automatically; if the
# file is absent the heartbeat shows a neutral "n/a" note.
FORWARD_DB_PATH = os.getenv("FORWARD_DB_PATH", str(ROOT / "research" / "data" / "evidence_forward.db"))

# Heartbeat storage monitoring (Phase B): free-disk %, db/wal sizes, and
# free-disk escalation of the heartbeat status.  Set HEARTBEAT_DISK_MONITORING=0
# to disable the storage line and disk-based escalation at runtime (kill-switch,
# no redeploy) without affecting the rest of the heartbeat.
HEARTBEAT_DISK_MONITORING = os.getenv("HEARTBEAT_DISK_MONITORING", "1") not in ("0", "false", "False", "")

# Heartbeat per-interval deltas (Phase C): msgs/reconnects/rows since the last
# heartbeat + worst staleness age.  Display-only (never escalates status).  Set
# HEARTBEAT_DELTAS=0 to revert the coverage and deltas lines to their prior
# format at runtime (kill-switch, no redeploy).
HEARTBEAT_DELTAS = os.getenv("HEARTBEAT_DELTAS", "1") not in ("0", "false", "False", "")

# Heartbeat universe-age warning (Phase D1): flag the universe DEGRADED when the
# chain-refresh snapshot is older than 2x the refresh interval (a frozen refresh
# that coverage masks).  Set HEARTBEAT_UNIVERSE_WARN=0 to disable at runtime.
HEARTBEAT_UNIVERSE_WARN = os.getenv("HEARTBEAT_UNIVERSE_WARN", "1") not in ("0", "false", "False", "")

# Heartbeat per-job last-run ages (Phase D2): render each scheduler job's
# last-success age and flag a stalled interval job DEGRADED.  Set
# HEARTBEAT_JOB_AGES=0 to disable the jobs line and job-stall escalation.
HEARTBEAT_JOB_AGES = os.getenv("HEARTBEAT_JOB_AGES", "1") not in ("0", "false", "False", "")

# Platform discriminator — "testnet" or "mainnet".
# Default is "testnet" (safe-fail: evidence never silently promoted to mainnet
# status by misconfiguration). Set PLATFORM=mainnet in .env only after the
# mainnet collector is validated and the certifier thresholds are calibrated.
PLATFORM = os.getenv("PLATFORM", "testnet")

# --- M1.2 Archive Writer (Implementation Package I1, T0 skeleton) ----------
# Placeholders only. Nothing in src/archive/ reads these yet (T0 is a
# skeleton — no executable archive functionality). Do not wire these into
# any running service before the Archive Writer implementation (T1-T9) is
# complete and reviewed.
COLD_ARCHIVE_ROOT = os.getenv("COLD_ARCHIVE_ROOT", str(ROOT / "data" / "cold_archive"))
ARCHIVE_HASH_SPEC_VERSION = int(os.getenv("ARCHIVE_HASH_SPEC_VERSION", "1"))
ARCHIVE_WRITER_ENABLED = os.getenv("ARCHIVE_WRITER_ENABLED", "0") not in ("0", "false", "False", "")
