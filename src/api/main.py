"""Entry point for `btcbot-api.service` (ADR-0001 §15.3).

    python -m src.api.main

Binding is loopback-only and not overridable from the environment (§8 N1);
see `src/api/config.py` for why the host is a constant rather than a setting.
"""
from __future__ import annotations

import logging
import sys

import uvicorn

from src.api.app import create_app
from src.api.config import ConfigError, load_settings
from src.api.logging_setup import init_logging

EXIT_OK = 0
EXIT_CONFIG = 2

log = logging.getLogger(__name__)


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        # Log before logging is configured as JSON -- this must be readable in
        # journald even when the process is about to die.
        logging.basicConfig(level=logging.ERROR)
        logging.getLogger(__name__).error("configuration error: %s", exc)
        return EXIT_CONFIG

    init_logging(settings.log_level)
    app = create_app(settings)

    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        # Our JsonFormatter is already installed on the root logger and
        # uvicorn's own handlers were cleared; log_config=None stops uvicorn
        # from reinstalling them and splitting stdout into two formats.
        log_config=None,
        access_log=False,  # the access-log middleware in app.py replaces it
        # Loopback-only + single operator: one worker is correct. Multiple
        # workers would multiply SQLite readers against the daily writer for
        # no throughput that matters here.
        workers=1,
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
