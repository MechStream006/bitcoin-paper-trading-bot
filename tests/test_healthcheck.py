"""Tests for scripts/healthcheck.py — P1 Increment 1 (ledger backup freshness).

First-ever test coverage for this module. Scope, deliberately narrow (matches
the approved Increment 1 plan):

  - Regression: check_backup_freshness()'s default arguments (label="btc_trading",
    check_name="backup") behave exactly as before the generalization.
  - New: the same function, parametrized for the governance ledger's backups
    (label="ledger", check_name="ledger_backup").
  - Label isolation: a directory holding both trading and ledger backup files
    must not cross-contaminate between the two labeled checks.
  - Wiring: src.api.services.runtime_health() includes the ledger_backup check
    alongside the existing backup check, so the CLI and the read-only API do
    not diverge on backup health.

Run from repo root:
    conda run -n btc_bot pytest tests/test_healthcheck.py -v
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Module loader — scripts/ is not a package; load as a file module (mirrors
# tests/test_backup.py's _load_backup_module()).
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


def _load_healthcheck_module():
    spec = importlib.util.spec_from_file_location(
        "healthcheck_module", _SCRIPTS_DIR / "healthcheck.py"
    )
    mod = importlib.util.module_from_spec(spec)
    # healthcheck.py defines a @dataclass (CheckResult), and dataclass
    # processing looks itself up via sys.modules[cls.__module__] -- the
    # module must be registered before exec_module() runs, unlike a plain
    # module with no dataclasses (e.g. backup.py's equivalent loader).
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


hc = _load_healthcheck_module()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _touch_backup(bdir: Path, label: str, ts: str, age_hours: float = 0.0) -> Path:
    """Create an empty `{label}_{ts}.db` file in bdir, backdated by age_hours."""
    bdir.mkdir(parents=True, exist_ok=True)
    f = bdir / f"{label}_{ts}.db"
    f.write_bytes(b"")
    if age_hours:
        old = time.time() - age_hours * 3600
        os.utime(f, (old, old))
    return f


def _write_offhost_status(
    bdir: Path,
    *,
    configured: bool = True,
    status: str = "ok",
    success_age_hours: float | None = 0.0,
) -> Path:
    """Write a `.offhost-status.json` sidecar matching scripts/backup.py's
    `_write_offhost_status()` shape, for testing `check_offhost_freshness()`
    without running a real backup."""
    bdir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    last_success_at = None
    if success_age_hours is not None:
        last_success_at = (now - timedelta(hours=success_age_hours)).isoformat()
    payload = {
        "schema": "btcbot-offhost-status/1",
        "attempted_at": now.isoformat(),
        "last_success_at": last_success_at,
        "db_label": "ledger",
        "configured": configured,
        "encrypted": True,
        "status": status,
        "exit_code": 0 if status == "ok" else 1,
        "artifact": "ledger_20260725_020000.db.age",
        "artifact_sha256": "deadbeef" * 8,
    }
    f = bdir / hc.OFFHOST_STATUS_FILENAME
    f.write_text(json.dumps(payload), encoding="utf-8")
    return f


# ---------------------------------------------------------------------------
# Regression: default arguments unchanged (btc_trading / "backup")
# ---------------------------------------------------------------------------


def test_default_missing_dir_is_warning(tmp_path: Path):
    result = hc.check_backup_freshness(tmp_path / "does-not-exist")
    assert result.name == "backup"
    assert result.severity == hc.WARNING
    assert "does not exist" in result.message


def test_default_no_matching_backups_is_warning(tmp_path: Path):
    bdir = tmp_path / "backups"
    bdir.mkdir()
    (bdir / "irrelevant_file.db").write_bytes(b"")
    result = hc.check_backup_freshness(bdir)
    assert result.name == "backup"
    assert result.severity == hc.WARNING
    assert "no backups found" in result.message


def test_default_fresh_backup_is_ok(tmp_path: Path):
    bdir = tmp_path / "backups"
    _touch_backup(bdir, "btc_trading", "20260725_020000", age_hours=1)
    result = hc.check_backup_freshness(bdir)
    assert result.name == "backup"
    assert result.severity == hc.OK
    assert result.detail["newest_backup"] == "btc_trading_20260725_020000.db"


def test_default_stale_backup_is_warning(tmp_path: Path):
    bdir = tmp_path / "backups"
    _touch_backup(bdir, "btc_trading", "20260701_020000",
                  age_hours=hc.BACKUP_WARN_HOURS + 1)
    result = hc.check_backup_freshness(bdir)
    assert result.name == "backup"
    assert result.severity == hc.WARNING
    assert "backup stale" in result.message


# ---------------------------------------------------------------------------
# New: ledger-labeled invocation
# ---------------------------------------------------------------------------


def test_ledger_missing_dir_is_warning(tmp_path: Path):
    result = hc.check_backup_freshness(
        tmp_path / "ledger-backups", label="ledger", check_name="ledger_backup"
    )
    assert result.name == "ledger_backup"
    assert result.severity == hc.WARNING
    assert "does not exist" in result.message


def test_ledger_no_matching_backups_is_warning(tmp_path: Path):
    bdir = tmp_path / "ledger-backups"
    bdir.mkdir()
    result = hc.check_backup_freshness(bdir, label="ledger", check_name="ledger_backup")
    assert result.name == "ledger_backup"
    assert result.severity == hc.WARNING
    assert "no backups found" in result.message


def test_ledger_fresh_backup_is_ok(tmp_path: Path):
    bdir = tmp_path / "ledger-backups"
    _touch_backup(bdir, "ledger", "20260725_020000", age_hours=1)
    result = hc.check_backup_freshness(bdir, label="ledger", check_name="ledger_backup")
    assert result.name == "ledger_backup"
    assert result.severity == hc.OK
    assert result.detail["newest_backup"] == "ledger_20260725_020000.db"


def test_ledger_stale_backup_is_warning(tmp_path: Path):
    bdir = tmp_path / "ledger-backups"
    _touch_backup(bdir, "ledger", "20260701_020000",
                  age_hours=hc.BACKUP_WARN_HOURS + 1)
    result = hc.check_backup_freshness(bdir, label="ledger", check_name="ledger_backup")
    assert result.name == "ledger_backup"
    assert result.severity == hc.WARNING
    assert "backup stale" in result.message


# ---------------------------------------------------------------------------
# New: label_store-labeled invocation (Phase 3 WBS 3.10, LS-DEC-001)
# ---------------------------------------------------------------------------


def test_label_store_missing_dir_is_warning(tmp_path: Path):
    result = hc.check_backup_freshness(
        tmp_path / "label-store-backups", label="label_store", check_name="label_store_backup"
    )
    assert result.name == "label_store_backup"
    assert result.severity == hc.WARNING
    assert "does not exist" in result.message


def test_label_store_no_matching_backups_is_warning(tmp_path: Path):
    bdir = tmp_path / "label-store-backups"
    bdir.mkdir()
    result = hc.check_backup_freshness(bdir, label="label_store", check_name="label_store_backup")
    assert result.name == "label_store_backup"
    assert result.severity == hc.WARNING
    assert "no backups found" in result.message


def test_label_store_fresh_backup_is_ok(tmp_path: Path):
    bdir = tmp_path / "label-store-backups"
    _touch_backup(bdir, "label_store", "20260806_020000", age_hours=1)
    result = hc.check_backup_freshness(bdir, label="label_store", check_name="label_store_backup")
    assert result.name == "label_store_backup"
    assert result.severity == hc.OK
    assert result.detail["newest_backup"] == "label_store_20260806_020000.db"


def test_label_store_stale_backup_is_warning(tmp_path: Path):
    bdir = tmp_path / "label-store-backups"
    _touch_backup(bdir, "label_store", "20260701_020000",
                  age_hours=hc.BACKUP_WARN_HOURS + 1)
    result = hc.check_backup_freshness(bdir, label="label_store", check_name="label_store_backup")
    assert result.name == "label_store_backup"
    assert result.severity == hc.WARNING
    assert "backup stale" in result.message


# ---------------------------------------------------------------------------
# Label isolation — one directory, both labels present
# ---------------------------------------------------------------------------


def test_labels_do_not_cross_contaminate(tmp_path: Path):
    bdir = tmp_path / "mixed-backups"
    # Fresh trading backup, stale ledger backup, same directory.
    _touch_backup(bdir, "btc_trading", "20260725_020000", age_hours=1)
    _touch_backup(bdir, "ledger", "20260701_020000",
                  age_hours=hc.BACKUP_WARN_HOURS + 1)

    trading_result = hc.check_backup_freshness(bdir)
    ledger_result = hc.check_backup_freshness(
        bdir, label="ledger", check_name="ledger_backup"
    )

    assert trading_result.name == "backup"
    assert trading_result.severity == hc.OK
    assert trading_result.detail["newest_backup"] == "btc_trading_20260725_020000.db"

    assert ledger_result.name == "ledger_backup"
    assert ledger_result.severity == hc.WARNING
    assert ledger_result.detail["newest_backup"] == "ledger_20260701_020000.db"


# ---------------------------------------------------------------------------
# Wiring: src.api.services.runtime_health() includes the ledger_backup check
# ---------------------------------------------------------------------------


def test_runtime_health_includes_ledger_backup_check(tmp_path: Path):
    from src.api.config import ApiSettings
    from src.api import services

    ledger_backup_dir = tmp_path / "ledger-backups"
    _touch_backup(ledger_backup_dir, "ledger", "20260725_020000", age_hours=1)

    settings = ApiSettings(
        host="127.0.0.1",
        port=9109,
        token="t" * 40,
        trading_db_path=tmp_path / "btc_trading.db",
        forward_db_path=tmp_path / "evidence_forward.db",
        ledger_db_path=tmp_path / "ledger.db",
        registry_path=tmp_path / "registry.yaml",
        thresholds_path=tmp_path / "thresholds.yaml",
        backup_dir=tmp_path / "backups",
        ledger_backup_dir=ledger_backup_dir,
        label_store_backup_dir=tmp_path / "label-store-backups",
        dataset_builder_backup_dir=tmp_path / "dataset-builder-backups",
        service_name="btcbot.service",
        log_level="INFO",
        cache_ttl_seconds=0.0,
    )

    result = services.runtime_health(settings)
    names = [c["name"] for c in result["checks"]]
    assert "backup" in names
    assert "ledger_backup" in names
    assert "offhost_backup" in names
    assert "ledger_offhost_backup" in names

    ledger_check = next(c for c in result["checks"] if c["name"] == "ledger_backup")
    assert ledger_check["severity"] == hc.OK


def test_runtime_health_includes_label_store_backup_check(tmp_path: Path):
    """Mirrors test_runtime_health_includes_ledger_backup_check exactly, for
    the Label Store's own freshness check (Phase 3 WBS 3.10). Deliberately
    does NOT assert an offhost_backup check for label_store — off-host
    wiring is out of this milestone's scope (feature_store_backup_strategy.md
    §4's own "not recommended at the same time as local backup" carve-out)."""
    from src.api.config import ApiSettings
    from src.api import services

    label_store_backup_dir = tmp_path / "label-store-backups"
    _touch_backup(label_store_backup_dir, "label_store", "20260806_020000", age_hours=1)

    settings = ApiSettings(
        host="127.0.0.1",
        port=9109,
        token="t" * 40,
        trading_db_path=tmp_path / "btc_trading.db",
        forward_db_path=tmp_path / "evidence_forward.db",
        ledger_db_path=tmp_path / "ledger.db",
        registry_path=tmp_path / "registry.yaml",
        thresholds_path=tmp_path / "thresholds.yaml",
        backup_dir=tmp_path / "backups",
        ledger_backup_dir=tmp_path / "ledger-backups",
        label_store_backup_dir=label_store_backup_dir,
        dataset_builder_backup_dir=tmp_path / "dataset-builder-backups",
        service_name="btcbot.service",
        log_level="INFO",
        cache_ttl_seconds=0.0,
    )

    result = services.runtime_health(settings)
    names = [c["name"] for c in result["checks"]]
    assert "label_store_backup" in names

    label_store_check = next(c for c in result["checks"] if c["name"] == "label_store_backup")
    assert label_store_check["severity"] == hc.OK


# ---------------------------------------------------------------------------
# check_offhost_freshness() — P1 Increment 6 / BK3 monitoring gap
# ---------------------------------------------------------------------------


def test_offhost_missing_sidecar_is_info(tmp_path: Path):
    """No sidecar at all -- e.g. a host that has never enabled off-host
    backup -- must be silent, never a warning."""
    result = hc.check_offhost_freshness(tmp_path / "ledger-backups")
    assert result.name == "offhost_backup"
    assert result.severity == hc.INFO


def test_offhost_not_configured_is_info(tmp_path: Path):
    bdir = tmp_path / "ledger-backups"
    _write_offhost_status(bdir, configured=False, status="not_configured",
                          success_age_hours=None)
    result = hc.check_offhost_freshness(bdir)
    assert result.severity == hc.INFO


def test_offhost_configured_never_succeeded_is_critical(tmp_path: Path):
    bdir = tmp_path / "ledger-backups"
    _write_offhost_status(bdir, configured=True, status="failed", success_age_hours=None)
    result = hc.check_offhost_freshness(bdir)
    assert result.severity == hc.CRITICAL
    assert "never succeeded" in result.message


def test_offhost_fresh_success_is_ok(tmp_path: Path):
    bdir = tmp_path / "ledger-backups"
    _write_offhost_status(bdir, status="ok", success_age_hours=1)
    result = hc.check_offhost_freshness(bdir)
    assert result.severity == hc.OK


def test_offhost_stale_48h_is_warning(tmp_path: Path):
    bdir = tmp_path / "ledger-backups"
    _write_offhost_status(bdir, status="ok",
                          success_age_hours=hc.OFFHOST_WARN_HOURS + 1)
    result = hc.check_offhost_freshness(bdir)
    assert result.severity == hc.WARNING
    assert "stale" in result.message


def test_offhost_stale_7d_is_critical(tmp_path: Path):
    bdir = tmp_path / "ledger-backups"
    _write_offhost_status(bdir, status="ok",
                          success_age_hours=hc.OFFHOST_CRIT_HOURS + 1)
    result = hc.check_offhost_freshness(bdir)
    assert result.severity == hc.CRITICAL


def test_offhost_recent_failure_with_fresh_last_success_is_warning(tmp_path: Path):
    """Latest attempt failed, but a success exists within 48h -- still a
    WARNING, not silently OK, so a broken transfer is visible immediately."""
    bdir = tmp_path / "ledger-backups"
    _write_offhost_status(bdir, status="failed", success_age_hours=2)
    result = hc.check_offhost_freshness(bdir)
    assert result.severity == hc.WARNING
    assert "failed" in result.message


def test_offhost_malformed_sidecar_is_warning(tmp_path: Path):
    bdir = tmp_path / "ledger-backups"
    bdir.mkdir(parents=True)
    (bdir / hc.OFFHOST_STATUS_FILENAME).write_text("{not valid json")
    result = hc.check_offhost_freshness(bdir)
    assert result.severity == hc.WARNING
    assert "unreadable" in result.message


def test_offhost_label_isolation_uses_own_directory(tmp_path: Path):
    """Trading-DB off-host status and ledger off-host status must not
    cross-contaminate -- each backup_dir carries its own sidecar."""
    trading_dir = tmp_path / "backups"
    ledger_dir = tmp_path / "ledger-backups"
    _write_offhost_status(trading_dir, status="ok", success_age_hours=1)
    _write_offhost_status(ledger_dir, status="ok",
                          success_age_hours=hc.OFFHOST_CRIT_HOURS + 1)

    trading_result = hc.check_offhost_freshness(trading_dir, check_name="offhost_backup")
    ledger_result = hc.check_offhost_freshness(
        ledger_dir, check_name="ledger_offhost_backup"
    )
    assert trading_result.severity == hc.OK
    assert ledger_result.severity == hc.CRITICAL
