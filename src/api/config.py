"""API configuration (ADR-0001 §9, §15.3).

Deliberately separate from `config/settings.py`: that module is the runtime
plane's config and importing it here would pull `DELTA_API_KEY` /
`DELTA_API_SECRET` into the API process's memory for no reason. The API needs
paths and one token -- nothing else. Least privilege applies to process memory,
not only to files.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent.parent

# Bind is loopback-only and NOT configurable to a wider interface by accident.
# ADR-0001 §8 lists 127.0.0.1:9109 as the only permitted exposure; N1 says no
# service is reachable from the public internet. Making the host an env var
# would turn a one-character typo into a public exposure, so the host is a
# module constant and only the port is tunable.
API_HOST = "127.0.0.1"
DEFAULT_API_PORT = 9109


class ConfigError(RuntimeError):
    """Raised at startup when configuration is missing or unsafe."""


@dataclass(frozen=True)
class ApiSettings:
    host: str
    port: int
    token: str
    trading_db_path: Path
    forward_db_path: Path
    ledger_db_path: Path
    registry_path: Path
    thresholds_path: Path
    backup_dir: Path
    ledger_backup_dir: Path
    label_store_backup_dir: Path
    service_name: str
    log_level: str
    cache_ttl_seconds: float

    def redacted(self) -> dict:
        """Config safe to log or return. The token is never included, not even
        truncated -- a prefix is still a meaningful brute-force head start."""
        return {
            "host": self.host,
            "port": self.port,
            "trading_db_path": str(self.trading_db_path),
            "forward_db_path": str(self.forward_db_path),
            "ledger_db_path": str(self.ledger_db_path),
            "service_name": self.service_name,
            "log_level": self.log_level,
            "cache_ttl_seconds": self.cache_ttl_seconds,
        }


def _min_token_length() -> int:
    return 32


def load_settings(env: dict | None = None) -> ApiSettings:
    """Build settings from the environment, failing closed.

    A missing or weak `API_TOKEN` is a hard startup failure, never a warning
    and never a generated-default. An API that silently starts unauthenticated
    is worse than one that refuses to start: the operator finds out about the
    second immediately and about the first from someone else.
    """
    if env is None:
        load_dotenv(ROOT / ".env")
        env = dict(os.environ)

    token = (env.get("API_TOKEN") or "").strip()
    if not token:
        raise ConfigError(
            "API_TOKEN is not set. Set it in /opt/btcbot/.env (0640 root:btcbot). "
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )
    if len(token) < _min_token_length():
        raise ConfigError(
            f"API_TOKEN is too short ({len(token)} chars, minimum {_min_token_length()}). "
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )

    research_data = ROOT / "research" / "data"
    evidence_lib = ROOT / "research" / "evidence_library"

    return ApiSettings(
        host=API_HOST,
        port=int(env.get("API_PORT", DEFAULT_API_PORT)),
        token=token,
        trading_db_path=Path(env.get("DB_PATH") or (ROOT / "data" / "btc_trading.db")),
        forward_db_path=Path(env.get("FORWARD_DB_PATH") or (research_data / "evidence_forward.db")),
        ledger_db_path=Path(env.get("LEDGER_DB_PATH") or (evidence_lib / "ledger.db")),
        registry_path=Path(env.get("REGISTRY_PATH") or (evidence_lib / "registry.yaml")),
        thresholds_path=Path(env.get("THRESHOLDS_PATH") or (evidence_lib / "thresholds.yaml")),
        backup_dir=Path(env.get("BACKUP_DIR") or (ROOT / "data" / "backups")),
        ledger_backup_dir=Path(
            env.get("LEDGER_BACKUP_DIR") or (ROOT / "data" / "ledger-backups")
        ),
        label_store_backup_dir=Path(
            env.get("LABEL_STORE_BACKUP_DIR") or (ROOT / "data" / "label-store-backups")
        ),
        service_name=env.get("API_TARGET_SERVICE", "btcbot.service"),
        log_level=env.get("API_LOG_LEVEL", "INFO"),
        cache_ttl_seconds=float(env.get("API_CACHE_TTL_SECONDS", "10")),
    )
