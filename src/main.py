"""BTC paper trading bot -- process entry point.

Responsibilities (this file only):
  - Load config from .env via config.settings
  - Initialize logging (console + rotating JSONL file)
  - Construct Runtime
  - Call bootstrap() then run()
  - Handle top-level exceptions with structured logging
  - Exit with deterministic codes

Exit codes:
  0  -- clean shutdown (SIGINT, SIGTERM, or normal supervisor exit)
  1  -- bootstrap failure (bad config, network error, empty universe)
  2  -- runtime fatal (warmup timeout, unrecoverable component)
  3  -- unexpected / unhandled exception

All orchestration logic lives in src/runtime/lifecycle.py.
This file contains no component construction, no business logic,
and no hidden globals.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import config.settings as cfg
from src.runtime.lifecycle import Runtime

# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------
EXIT_OK        = 0
EXIT_BOOTSTRAP = 1
EXIT_FATAL     = 2
EXIT_UNKNOWN   = 3

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

class _UTCFormatter(logging.Formatter):
    """Console formatter: UTC timestamps, human-readable."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        ct = datetime.fromtimestamp(record.created, tz=timezone.utc)
        return ct.strftime("%Y-%m-%dT%H:%M:%SZ")


class _JsonlFormatter(logging.Formatter):
    """File formatter: one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        obj: dict = {
            "ts":     datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level":  record.levelname,
            "logger": record.name,
            "msg":    record.getMessage(),
        }
        if record.exc_info:
            obj["exc"] = self.formatException(record.exc_info)
        return json.dumps(obj, ensure_ascii=True)


def _init_logging(log_level: str, log_dir: Path) -> None:
    """Configure root logger: console (human) + rotating JSONL file."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "btc_bot.jsonl"

    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(_UTCFormatter(
        fmt="%(asctime)s %(levelname)-8s %(name)s: %(message)s"
    ))
    root.addHandler(console)

    file_h = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_h.setFormatter(_JsonlFormatter())
    root.addHandler(file_h)

    logging.getLogger(__name__).info(
        "[main] logging ready: level=%s file=%s", log_level, log_file
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    log_dir = cfg.DB_PATH.parent / "logs"
    _init_logging(cfg.LOG_LEVEL, log_dir)
    logger = logging.getLogger(__name__)

    logger.info("[main] ================================================")
    logger.info("[main]   BTC Paper Trading Bot  (Layer 2d)")
    logger.info("[main]   env=%-6s  db=%s", cfg.ENV, cfg.DB_PATH)
    logger.info("[main] ================================================")

    runtime = Runtime(
        db_path=cfg.DB_PATH,
        delta_base_url=cfg.DELTA_BASE_URL,
        delta_api_key=cfg.DELTA_API_KEY,
        delta_api_secret=cfg.DELTA_API_SECRET,
        delta_ws_url=cfg.DELTA_WS_URL,
        telegram_bot_token=cfg.TELEGRAM_BOT_TOKEN,
        telegram_channel_id=cfg.TELEGRAM_CHANNEL_ID,
        forward_status_path=cfg.FORWARD_DB_PATH,
        heartbeat_disk_monitoring=cfg.HEARTBEAT_DISK_MONITORING,
        heartbeat_deltas=cfg.HEARTBEAT_DELTAS,
    )

    # Bootstrap: fatal on error, non-zero exit
    try:
        runtime.bootstrap()
    except KeyboardInterrupt:
        logger.info("[main] interrupted during bootstrap -- exiting cleanly")
        runtime.drain()
        return EXIT_OK
    except Exception as exc:
        logger.critical(
            "[main] bootstrap failed: %s: %s", type(exc).__name__, exc,
            exc_info=True,
        )
        runtime.drain()
        return EXIT_BOOTSTRAP

    # Run: blocks until shutdown; drain() called inside run() always
    try:
        runtime.run()
        logger.info("[main] run() returned cleanly")
        return EXIT_OK
    except KeyboardInterrupt:
        # Should not reach here if signal handlers are installed,
        # but guard against the narrow window before they are.
        logger.info("[main] KeyboardInterrupt -- clean exit")
        return EXIT_OK
    except RuntimeError as exc:
        logger.critical("[main] runtime fatal: %s", exc, exc_info=True)
        return EXIT_FATAL
    except Exception as exc:
        logger.critical(
            "[main] unexpected exception: %s: %s", type(exc).__name__, exc,
            exc_info=True,
        )
        return EXIT_UNKNOWN
    finally:
        # drain() is idempotent; run() already called it but this guards
        # against any code path that bypasses run()'s own finally block.
        runtime.drain()
        logger.info("[main] process exiting (state=%s)", runtime.state.value)


if __name__ == "__main__":
    sys.exit(main())
