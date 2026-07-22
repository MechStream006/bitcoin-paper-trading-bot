"""Structured logging for the read-only API (ADR-0001 §10).

journald is the authoritative log store (§10 decision: no Loki/ELK). This unit
therefore logs JSON to **stdout only** and lets systemd capture it. It
deliberately does NOT write `data/logs/*.jsonl` the way `btcbot.service` does:
that would give the API write access to the runtime plane's directory for the
sake of a log file journald already holds, which is a boundary crossing (B1)
bought for nothing.

The field shape intentionally mirrors `src/main.py::_JsonlFormatter`
(`ts`/`level`/`logger`/`msg`/`exc`) so both units' logs parse with one reader.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

# Keys already present on every LogRecord. Anything outside this set that a
# caller attaches via `extra=` is a structured field we want in the JSON.
_STD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
) | {"asctime", "message", "taskName"}


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with `extra=` fields promoted to top level."""

    def format(self, record: logging.LogRecord) -> str:
        obj: dict = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STD_ATTRS and not key.startswith("_"):
                obj[key] = value
        if record.exc_info:
            obj["exc"] = self.formatException(record.exc_info)
        # default=str keeps a stray Path or datetime from turning a log call
        # into an exception inside the logging subsystem.
        return json.dumps(obj, ensure_ascii=True, default=str)


def init_logging(level: str = "INFO") -> None:
    """Configure the root logger for JSON-to-stdout. Idempotent."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)

    # uvicorn installs its own colourised handlers; drop them so every line on
    # stdout is valid JSON rather than a mix of formats journald has to guess at.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
