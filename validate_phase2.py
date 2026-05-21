"""Phase 2 operational validation harness.

Runs the full Runtime with tighter cadences so posture, telemetry, and
heartbeat behaviour can be observed quickly.  Designed to run for a bounded
window (VALIDATE_DURATION_SEC) and then exit cleanly.

Intervals (vs production defaults):
  expiry_observe   60 s   (vs 300 s)
  heartbeat        60 s   (vs 1800 s)
  snapshot         30 s   (vs 60 s)
  chain_refresh   300 s   (vs 3600 s)

Telemetry output: data/logs/expiry_telemetry.jsonl
Log output:       data/logs/btc_bot_validate.jsonl  (separate from production)
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import config.settings as cfg
from src.runtime.lifecycle import Runtime

_UTC = timezone.utc
VALIDATE_DURATION_SEC = int(sys.argv[1]) if len(sys.argv) > 1 else 180   # 3 min default


# ---------------------------------------------------------------------------
# Logging  (separate validation log file)
# ---------------------------------------------------------------------------

class _UTCFmt(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        return datetime.fromtimestamp(record.created, tz=_UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class _JsonlFmt(logging.Formatter):
    def format(self, record):
        obj = {
            "ts":    datetime.fromtimestamp(record.created, tz=_UTC).isoformat(),
            "level": record.levelname,
            "name":  record.name,
            "msg":   record.getMessage(),
        }
        if record.exc_info:
            obj["exc"] = self.formatException(record.exc_info)
        return json.dumps(obj, ensure_ascii=True)


def _setup_logging():
    log_dir = cfg.DB_PATH.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "btc_bot_validate.jsonl"

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(_UTCFmt("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))
    root.addHandler(ch)

    fh = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=20 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    fh.setFormatter(_JsonlFmt())
    root.addHandler(fh)

    return logging.getLogger("validate")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    logger = _setup_logging()
    now_utc = datetime.now(_UTC)
    logger.info("=" * 60)
    logger.info("Phase 2 Operational Validation")
    logger.info("UTC now: %s", now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"))
    logger.info("Duration: %d s", VALIDATE_DURATION_SEC)
    logger.info("=" * 60)

    rt = Runtime(
        db_path=cfg.DB_PATH,
        delta_base_url=cfg.DELTA_BASE_URL,
        delta_api_key=cfg.DELTA_API_KEY,
        delta_api_secret=cfg.DELTA_API_SECRET,
        delta_ws_url=cfg.DELTA_WS_URL,
        telegram_bot_token=cfg.TELEGRAM_BOT_TOKEN,
        telegram_channel_id=cfg.TELEGRAM_CHANNEL_ID,
        expiry_observe_interval_sec=60,
        heartbeat_interval_sec=60,
        snapshot_interval_sec=30,
        chain_refresh_interval_sec=300,
        warmup_timeout_sec=60,
    )

    # Watchdog: fire SIGINT after VALIDATE_DURATION_SEC
    def _watchdog():
        time.sleep(VALIDATE_DURATION_SEC)
        logger.info("[watchdog] duration elapsed — requesting shutdown")
        signal.raise_signal(signal.SIGINT)

    t = threading.Thread(target=_watchdog, daemon=True)
    t.start()

    try:
        rt.bootstrap()
    except Exception as exc:
        logger.critical("[validate] bootstrap failed: %s: %s", type(exc).__name__, exc)
        rt.drain()
        return 1

    try:
        rt.run()
        logger.info("[validate] run() returned cleanly")
        return 0
    except KeyboardInterrupt:
        logger.info("[validate] interrupted")
        return 0
    except Exception as exc:
        logger.critical("[validate] exception: %s: %s", type(exc).__name__, exc, exc_info=True)
        return 2
    finally:
        rt.drain()
        logger.info("[validate] exited (state=%s)", rt.state.value)


if __name__ == "__main__":
    sys.exit(main())
