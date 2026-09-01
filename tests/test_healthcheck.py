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
import sqlite3
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
        feature_store_backup_dir=tmp_path / "feature-store-backups",
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
        feature_store_backup_dir=tmp_path / "feature-store-backups",
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


# ---------------------------------------------------------------------------
# Feature Store backup freshness -- operational-defect fix
#
# The Feature Store had no backup path at all, so it also had no freshness
# check: nothing could have reported the gap. These mirror the Label Store's
# checks exactly.
# ---------------------------------------------------------------------------


def test_feature_store_missing_dir_is_warning(tmp_path: Path):
    result = hc.check_backup_freshness(
        tmp_path / "feature-store-backups", label="feature_store",
        check_name="feature_store_backup",
    )
    assert result.name == "feature_store_backup"
    assert result.severity == hc.WARNING
    assert "does not exist" in result.message


def test_feature_store_no_matching_backups_is_warning(tmp_path: Path):
    bdir = tmp_path / "feature-store-backups"
    bdir.mkdir()
    result = hc.check_backup_freshness(
        bdir, label="feature_store", check_name="feature_store_backup")
    assert result.name == "feature_store_backup"
    assert result.severity == hc.WARNING
    assert "no backups found" in result.message


def test_feature_store_fresh_backup_is_ok(tmp_path: Path):
    bdir = tmp_path / "feature-store-backups"
    _touch_backup(bdir, "feature_store", "20260808_020000", age_hours=1)
    result = hc.check_backup_freshness(
        bdir, label="feature_store", check_name="feature_store_backup")
    assert result.name == "feature_store_backup"
    assert result.severity == hc.OK
    assert result.detail["newest_backup"] == "feature_store_20260808_020000.db"


def test_feature_store_stale_backup_is_warning(tmp_path: Path):
    bdir = tmp_path / "feature-store-backups"
    _touch_backup(bdir, "feature_store", "20260701_020000",
                  age_hours=hc.BACKUP_WARN_HOURS + 1)
    result = hc.check_backup_freshness(
        bdir, label="feature_store", check_name="feature_store_backup")
    assert result.name == "feature_store_backup"
    assert result.severity == hc.WARNING
    assert "backup stale" in result.message


def test_feature_store_label_does_not_pick_up_other_stores(tmp_path: Path):
    """One directory, three labels. `feature_store` must match only its own.

    `label_store_*` and `feature_store_*` share the `_store_` substring, so a
    sloppier glob would cross-match and report a healthy Feature Store backup
    that does not exist.
    """
    bdir = tmp_path / "mixed-backups"
    _touch_backup(bdir, "label_store", "20260808_020000", age_hours=1)
    _touch_backup(bdir, "dataset_builder", "20260808_020000", age_hours=1)
    _touch_backup(bdir, "feature_store", "20260701_020000",
                  age_hours=hc.BACKUP_WARN_HOURS + 1)

    fs = hc.check_backup_freshness(
        bdir, label="feature_store", check_name="feature_store_backup")
    ls = hc.check_backup_freshness(
        bdir, label="label_store", check_name="label_store_backup")

    assert fs.severity == hc.WARNING
    assert fs.detail["newest_backup"].startswith("feature_store_")
    assert ls.severity == hc.OK
    assert ls.detail["newest_backup"].startswith("label_store_")


def test_feature_store_check_is_registered_in_the_cli(tmp_path: Path):
    """The check must be wired into main(), not merely available.

    The defect was one of absence, so a check that exists but is never
    invoked would reproduce it exactly.
    """
    import inspect

    src = inspect.getsource(hc.main)
    assert "feature_store_backup" in src
    assert "feature_store_backup_dir" in src


def test_feature_store_cli_flag_exists_with_expected_default():
    import inspect

    src = inspect.getsource(hc.main)
    assert "--feature-store-backup-dir" in src
    assert "feature-store-backups" in src


def test_runtime_health_includes_feature_store_backup_check(tmp_path: Path):
    """CLI/API parity, established at P1 Increment 1 and preserved since.

    A check present in the CLI but absent from the read-only API would let the
    two disagree about backup health, which is the divergence that parity rule
    exists to prevent.
    """
    from src.api.config import ApiSettings
    from src.api import services

    feature_store_backup_dir = tmp_path / "feature-store-backups"
    _touch_backup(feature_store_backup_dir, "feature_store", "20260808_020000",
                  age_hours=1)

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
        label_store_backup_dir=tmp_path / "label-store-backups",
        dataset_builder_backup_dir=tmp_path / "dataset-builder-backups",
        feature_store_backup_dir=feature_store_backup_dir,
        service_name="btcbot.service",
        log_level="INFO",
        cache_ttl_seconds=0.0,
    )

    result = services.runtime_health(settings)
    names = [c["name"] for c in result["checks"]]
    assert "feature_store_backup" in names

    check = next(c for c in result["checks"] if c["name"] == "feature_store_backup")
    assert check["severity"] == hc.OK


def test_runtime_health_still_includes_every_other_backup_check(tmp_path: Path):
    """Regression: adding a sixth check must not displace any existing one."""
    from src.api.config import ApiSettings
    from src.api import services

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
        label_store_backup_dir=tmp_path / "label-store-backups",
        dataset_builder_backup_dir=tmp_path / "dataset-builder-backups",
        feature_store_backup_dir=tmp_path / "feature-store-backups",
        service_name="btcbot.service",
        log_level="INFO",
        cache_ttl_seconds=0.0,
    )

    names = [c["name"] for c in services.runtime_health(settings)["checks"]]
    for expected in ("backup", "ledger_backup", "label_store_backup",
                     "dataset_builder_backup", "feature_store_backup",
                     "offhost_backup", "ledger_offhost_backup"):
        assert expected in names, expected


def test_cli_feature_store_check_reads_its_own_label(tmp_path: Path, monkeypatch, capsys):
    """End-to-end through `main()`: the registered check must use the right label.

    Added after TV3: swapping the registered label to `label_store` failed
    nothing, because the earlier tests called `check_backup_freshness`
    directly and only grepped `main()`'s source. This drives the real CLI and
    reads the emitted JSON, so a wrong label changes an observable value.

    The directory holds a *fresh* `label_store_*` decoy and a *stale*
    `feature_store_*` file, so a mis-labelled check reports OK instead of
    WARNING -- the failure mode that matters, since it would claim a healthy
    backup for a store that has none.
    """
    shared = tmp_path / "shared-backups"
    _touch_backup(shared, "label_store", "20260808_020000", age_hours=1)
    _touch_backup(shared, "feature_store", "20260701_020000",
                  age_hours=hc.BACKUP_WARN_HOURS + 1)

    import sqlite3

    db = tmp_path / "btc_trading.db"
    sqlite3.connect(str(db)).close()

    monkeypatch.setattr(sys, "argv", [
        "healthcheck.py",
        "--db-path", str(db),
        "--feature-store-backup-dir", str(shared),
        "--no-telegram",
        "--no-forward-check",
    ])
    hc.main()
    summary = json.loads(capsys.readouterr().out)

    check = next(c for c in summary["checks"] if c["name"] == "feature_store_backup")
    assert check["severity"] == hc.WARNING
    assert check["newest_backup"].startswith("feature_store_")


# ---------------------------------------------------------------------------
# AF-4 -- a fresh backup of an EMPTY database must not report OK
#
# Freshness alone cannot distinguish a healthy backup from a backup of nothing.
# On EC2 `feature_store.db` and `label_store.db` are zero-table placeholder
# files, so before this fix a fresh backup of one reported OK -- a green signal
# for a store holding no data.
#
# INFO, not WARNING: an empty optional store is a normal state on a host where
# its research process never ran, and INFO can never escalate `overall` or send
# Telegram (the PROD-2 discipline).
# ---------------------------------------------------------------------------


def _backup_of(tmp_path: Path, tables: dict, label: str = "feature_store"):
    """Produce a real backup + manifest from a database with given contents."""
    import sqlite3
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "backup_for_af4", Path(__file__).resolve().parent.parent / "scripts" / "backup.py"
    )
    backup = importlib.util.module_from_spec(spec)
    sys.modules["backup_for_af4"] = backup
    spec.loader.exec_module(backup)

    src = tmp_path / f"{label}.db"
    con = sqlite3.connect(str(src))
    for t, n in tables.items():
        con.execute(f'CREATE TABLE "{t}" (id INTEGER PRIMARY KEY)')
        for _ in range(n):
            con.execute(f'INSERT INTO "{t}" VALUES (NULL)')
    con.commit()
    con.close()

    bdir = tmp_path / f"{label}-backups"
    backup.run_backup(src, bdir, dry_run=False, db_label=label)
    return bdir


def test_af4_empty_database_backup_is_info_not_ok(tmp_path: Path):
    """The defect: a zero-table database produced a fresh, healthy-looking backup."""
    bdir = _backup_of(tmp_path, {})
    r = hc.check_backup_freshness(
        bdir, label="feature_store", check_name="feature_store_backup")
    assert r.severity == hc.INFO
    assert "EMPTY" in r.message
    assert r.detail["rows"] == 0


def test_af4_tables_present_but_all_empty_is_also_info(tmp_path: Path):
    """A schema with no rows is just as empty for recovery purposes."""
    bdir = _backup_of(tmp_path, {"feature_specifications": 0})
    r = hc.check_backup_freshness(
        bdir, label="feature_store", check_name="feature_store_backup")
    assert r.severity == hc.INFO
    assert r.detail["rows"] == 0


def test_af4_populated_database_is_still_ok(tmp_path: Path):
    """Regression: the fix must not downgrade a genuinely healthy backup."""
    bdir = _backup_of(tmp_path, {"feature_specifications": 17, "governance_receipts": 17})
    r = hc.check_backup_freshness(
        bdir, label="feature_store", check_name="feature_store_backup")
    assert r.severity == hc.OK
    assert r.detail["rows"] == 34
    assert "34 rows" in r.message


def test_af4_missing_manifest_is_not_read_as_empty(tmp_path: Path):
    """Absence of evidence is not evidence of absence.

    An unreadable manifest must yield `None`, never 0 -- otherwise deleting a
    manifest would make a healthy backup look empty.
    """
    bdir = _backup_of(tmp_path, {"feature_specifications": 17})
    for m in bdir.glob("*.manifest.json"):
        m.unlink()
    r = hc.check_backup_freshness(
        bdir, label="feature_store", check_name="feature_store_backup")
    assert r.severity == hc.OK
    assert r.detail["rows"] is None


def test_af4_corrupt_manifest_is_not_read_as_empty(tmp_path: Path):
    bdir = _backup_of(tmp_path, {"feature_specifications": 17})
    for m in bdir.glob("*.manifest.json"):
        m.write_text("{not json", encoding="utf-8")
    r = hc.check_backup_freshness(
        bdir, label="feature_store", check_name="feature_store_backup")
    assert r.severity == hc.OK
    assert r.detail["rows"] is None


def test_af4_staleness_still_outranks_emptiness(tmp_path: Path):
    """A stale backup is the more urgent signal and must stay WARNING.

    An empty backup is INFO; a stale one is WARNING. If a backup is both, the
    operator needs to hear about the staleness.
    """
    bdir = tmp_path / "feature-store-backups"
    f = _touch_backup(bdir, "feature_store", "20260701_020000",
                      age_hours=hc.BACKUP_WARN_HOURS + 1)
    (bdir / (f.stem + ".manifest.json")).write_text(
        json.dumps({"row_counts": {}}), encoding="utf-8")
    r = hc.check_backup_freshness(
        bdir, label="feature_store", check_name="feature_store_backup")
    assert r.severity == hc.WARNING
    assert "stale" in r.message


def test_af4_empty_backup_never_escalates_overall(tmp_path: Path):
    """INFO must not raise `overall` -- the PROD-2 trust-in-the-signal rule."""
    assert hc._RANK[hc.INFO] < hc._RANK[hc.WARNING]
    assert hc._RANK[hc.INFO] < hc._RANK[hc.CRITICAL]


def test_af4_applies_to_every_labelled_store(tmp_path: Path):
    """The check is shared, so the fix must hold for all labels, not just one."""
    for label in ("ledger", "label_store", "dataset_builder", "btc_trading"):
        sub = tmp_path / label
        sub.mkdir(parents=True, exist_ok=True)
        bdir = _backup_of(sub, {}, label=label)
        r = hc.check_backup_freshness(bdir, label=label, check_name=f"{label}_backup")
        assert r.severity == hc.INFO, label
        assert r.detail["rows"] == 0, label


# ---------------------------------------------------------------------------
# H6 daily-corpus dead-man (check_h6_cycle)
#
# Severity is evidence-backed, not invented: 2026-07-01 and 2026-08-01 are
# real production no_new_data days, confirmed via journalctl to be a fully
# successful ingest with a genuinely unchanged corpus, both self-recovering
# the next cycle -- so a single isolated no_new_data row must stay OK
# (test_h6_single_no_new_data_day_is_ok_20260701 /
# _20260801 lock this in directly). There is no historical precedent for 2+
# consecutive no_new_data rows, so that is where it becomes WARNING-worthy.
# Liveness staleness (no row at all for the expected day) reuses
# FORWARD_WARN_DAYS/FORWARD_CRIT_DAYS unmodified, mirroring
# check_forward_cycle()'s own ladder.
# ---------------------------------------------------------------------------


def _write_h6_run_log(db_path: Path, rows: list[tuple[int, str]]) -> None:
    """rows: list of (run_ts_ms, status), any order. Creates a minimal but
    schema-faithful run_log table (run_ts INTEGER PRIMARY KEY, status TEXT)."""
    con = sqlite3.connect(db_path)
    con.execute(
        "CREATE TABLE run_log (run_ts INTEGER PRIMARY KEY, status TEXT NOT NULL)"
    )
    con.executemany("INSERT INTO run_log (run_ts, status) VALUES (?, ?)", rows)
    con.commit()
    con.close()


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def test_h6_missing_db_is_info(tmp_path: Path):
    r = hc.check_h6_cycle(tmp_path / "forward_evidence.db")
    assert r.severity == hc.INFO
    assert "not present" in r.message


def test_h6_never_ran_empty_run_log_is_critical(tmp_path: Path):
    db_path = tmp_path / "forward_evidence.db"
    _write_h6_run_log(db_path, [])
    r = hc.check_h6_cycle(db_path)
    assert r.severity == hc.CRITICAL
    assert "never run" in r.message


def test_h6_unreadable_db_is_critical(tmp_path: Path):
    db_path = tmp_path / "forward_evidence.db"
    db_path.write_bytes(b"not a sqlite database at all")
    r = hc.check_h6_cycle(db_path)
    assert r.severity == hc.CRITICAL
    assert "unreadable" in r.message


def test_h6_fresh_ok_is_ok(tmp_path: Path):
    now = datetime(2026, 8, 11, 14, 0, 0, tzinfo=timezone.utc)
    db_path = tmp_path / "forward_evidence.db"
    _write_h6_run_log(db_path, [(_ms(now), "ok")])
    r = hc.check_h6_cycle(db_path, now=now)
    assert r.severity == hc.OK
    assert r.detail["days_behind"] == 0
    assert r.detail["no_new_data_streak"] == 0


def test_h6_one_day_stale_no_row_is_warning(tmp_path: Path):
    now = datetime(2026, 8, 11, 14, 0, 0, tzinfo=timezone.utc)
    db_path = tmp_path / "forward_evidence.db"
    _write_h6_run_log(db_path, [(_ms(now - timedelta(days=1)), "ok")])
    r = hc.check_h6_cycle(db_path, now=now)
    assert r.severity == hc.WARNING
    assert "stale" in r.message
    assert r.detail["days_behind"] == 1


def test_h6_two_days_stale_no_row_is_critical(tmp_path: Path):
    now = datetime(2026, 8, 11, 14, 0, 0, tzinfo=timezone.utc)
    db_path = tmp_path / "forward_evidence.db"
    _write_h6_run_log(db_path, [(_ms(now - timedelta(days=2)), "ok")])
    r = hc.check_h6_cycle(db_path, now=now)
    assert r.severity == hc.CRITICAL
    assert "stale" in r.message
    assert r.detail["days_behind"] == 2


def test_h6_single_no_new_data_day_is_ok_20260701(tmp_path: Path):
    """Regression lock: 2026-07-01 was a fully successful ingest with a
    genuinely unchanged corpus (confirmed via journalctl), self-recovering
    the next cycle. An isolated no_new_data row must never alert."""
    now = datetime(2026, 7, 1, 0, 6, 0, tzinfo=timezone.utc)
    db_path = tmp_path / "forward_evidence.db"
    _write_h6_run_log(db_path, [
        (_ms(datetime(2026, 6, 30, 0, 5, 0, tzinfo=timezone.utc)), "ok"),
        (_ms(now), "no_new_data"),
    ])
    r = hc.check_h6_cycle(db_path, now=now)
    assert r.severity == hc.OK
    assert r.detail["no_new_data_streak"] == 1


def test_h6_single_no_new_data_day_is_ok_20260801(tmp_path: Path):
    """Regression lock: the second real occurrence, same shape as 07-01."""
    now = datetime(2026, 8, 1, 0, 6, 0, tzinfo=timezone.utc)
    db_path = tmp_path / "forward_evidence.db"
    _write_h6_run_log(db_path, [
        (_ms(datetime(2026, 7, 31, 0, 6, 0, tzinfo=timezone.utc)), "ok"),
        (_ms(now), "no_new_data"),
    ])
    r = hc.check_h6_cycle(db_path, now=now)
    assert r.severity == hc.OK
    assert r.detail["no_new_data_streak"] == 1


def test_h6_two_consecutive_no_new_data_is_warning(tmp_path: Path):
    """No historical precedent for 2+ consecutive no_new_data days -- unlike
    a single occurrence, this must alert."""
    now = datetime(2026, 8, 11, 0, 6, 0, tzinfo=timezone.utc)
    db_path = tmp_path / "forward_evidence.db"
    _write_h6_run_log(db_path, [
        (_ms(datetime(2026, 8, 9, 0, 6, 0, tzinfo=timezone.utc)), "ok"),
        (_ms(datetime(2026, 8, 10, 0, 6, 0, tzinfo=timezone.utc)), "no_new_data"),
        (_ms(now), "no_new_data"),
    ])
    r = hc.check_h6_cycle(db_path, now=now)
    assert r.severity == hc.WARNING
    assert "no new data" in r.message
    assert r.detail["no_new_data_streak"] == 2


def test_h6_three_consecutive_no_new_data_streak_is_reported_accurately(tmp_path: Path):
    now = datetime(2026, 8, 11, 0, 6, 0, tzinfo=timezone.utc)
    db_path = tmp_path / "forward_evidence.db"
    _write_h6_run_log(db_path, [
        (_ms(datetime(2026, 8, 8, 0, 6, 0, tzinfo=timezone.utc)), "ok"),
        (_ms(datetime(2026, 8, 9, 0, 6, 0, tzinfo=timezone.utc)), "no_new_data"),
        (_ms(datetime(2026, 8, 10, 0, 6, 0, tzinfo=timezone.utc)), "no_new_data"),
        (_ms(now), "no_new_data"),
    ])
    r = hc.check_h6_cycle(db_path, now=now)
    assert r.severity == hc.WARNING
    assert r.detail["no_new_data_streak"] == 3


def test_h6_warning_status_is_warning(tmp_path: Path):
    """Mirrors check_forward_cycle()'s degraded/signal-error -> WARNING."""
    now = datetime(2026, 8, 11, 14, 0, 0, tzinfo=timezone.utc)
    db_path = tmp_path / "forward_evidence.db"
    _write_h6_run_log(db_path, [(_ms(now), "warning")])
    r = hc.check_h6_cycle(db_path, now=now)
    assert r.severity == hc.WARNING
    assert "signal error" in r.message


def test_h6_error_status_is_critical_regardless_of_freshness(tmp_path: Path):
    """Mirrors check_forward_cycle()'s fn_hash_violation -> CRITICAL. Must
    win even though the row is perfectly fresh (0 days behind)."""
    now = datetime(2026, 8, 11, 14, 0, 0, tzinfo=timezone.utc)
    db_path = tmp_path / "forward_evidence.db"
    _write_h6_run_log(db_path, [(_ms(now), "error")])
    r = hc.check_h6_cycle(db_path, now=now)
    assert r.severity == hc.CRITICAL
    assert "fn_hash violation" in r.message


def test_h6_error_status_outranks_no_new_data_streak(tmp_path: Path):
    """status='error' must be reported even if it also happens to be preceded
    by a no_new_data run -- the tamper signal is strictly more informative."""
    now = datetime(2026, 8, 11, 14, 0, 0, tzinfo=timezone.utc)
    db_path = tmp_path / "forward_evidence.db"
    _write_h6_run_log(db_path, [
        (_ms(datetime(2026, 8, 10, 14, 0, 0, tzinfo=timezone.utc)), "no_new_data"),
        (_ms(now), "error"),
    ])
    r = hc.check_h6_cycle(db_path, now=now)
    assert r.severity == hc.CRITICAL
    assert "fn_hash violation" in r.message


def test_h6_check_is_registered_in_the_cli(tmp_path: Path):
    """The check must be wired into main(), not merely available."""
    import inspect

    src = inspect.getsource(hc.main)
    assert "check_h6_cycle" in src
    assert "h6_db_path" in src


def test_h6_cli_flag_exists_with_expected_default():
    import inspect

    src = inspect.getsource(hc.main)
    assert "--h6-db-path" in src
    assert "forward_evidence.db" in src


def test_h6_no_check_flag_is_an_explicit_opt_out(tmp_path: Path):
    """Default behavior must ENABLE the H6 check; --no-h6-check is the only
    way to skip it, mirroring --no-forward-check's existing shape exactly."""
    import inspect

    src = inspect.getsource(hc.main)
    assert "--no-h6-check" in src
    assert "if not args.no_h6_check:" in src


def test_runtime_health_includes_h6_check(tmp_path: Path):
    """CLI/API parity: the H6 check must also be reachable from the
    read-only API, exactly like check_forward_cycle. No new ApiSettings
    field is introduced -- the H6 db is derived as a sibling of
    forward_db_path, matching scripts/healthcheck.py's own default."""
    from src.api.config import ApiSettings
    from src.api import services

    now = datetime.now(timezone.utc)
    forward_db_path = tmp_path / "evidence_forward.db"
    h6_db_path = tmp_path / "forward_evidence.db"
    _write_h6_run_log(h6_db_path, [(_ms(now), "ok")])

    settings = ApiSettings(
        host="127.0.0.1",
        port=9109,
        token="t" * 40,
        trading_db_path=tmp_path / "btc_trading.db",
        forward_db_path=forward_db_path,
        ledger_db_path=tmp_path / "ledger.db",
        registry_path=tmp_path / "registry.yaml",
        thresholds_path=tmp_path / "thresholds.yaml",
        backup_dir=tmp_path / "backups",
        ledger_backup_dir=tmp_path / "ledger-backups",
        label_store_backup_dir=tmp_path / "label-store-backups",
        dataset_builder_backup_dir=tmp_path / "dataset-builder-backups",
        feature_store_backup_dir=tmp_path / "feature-store-backups",
        service_name="btcbot.service",
        log_level="INFO",
        cache_ttl_seconds=0.0,
    )

    result = services.runtime_health(settings)
    names = [c["name"] for c in result["checks"]]
    assert "h6_cycle" in names

    h6_check = next(c for c in result["checks"] if c["name"] == "h6_cycle")
    assert h6_check["severity"] == hc.OK


def test_runtime_health_still_includes_forward_cycle_check_unchanged(tmp_path: Path):
    """Adding H6 must not disturb the pre-existing forward_cycle check."""
    from src.api.config import ApiSettings
    from src.api import services

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
        label_store_backup_dir=tmp_path / "label-store-backups",
        dataset_builder_backup_dir=tmp_path / "dataset-builder-backups",
        feature_store_backup_dir=tmp_path / "feature-store-backups",
        service_name="btcbot.service",
        log_level="INFO",
        cache_ttl_seconds=0.0,
    )

    result = services.runtime_health(settings)
    names = [c["name"] for c in result["checks"]]
    assert "forward_cycle" in names


# ---------------------------------------------------------------------------
# check_forward_trades -- visibility-only trade accrual (FB-10b-W follow-up)
# ---------------------------------------------------------------------------

class TestForwardTrades:
    """Accrual visibility. `check_forward_cycle` watches whether the daily RUN
    happened; a family can run cleanly for weeks and open no trade because its
    signal never fires. This reports counts only and never alerts."""

    def _db(self, tmp_path, closed, open_):
        import sqlite3
        from pathlib import Path
        db = tmp_path / "evidence_forward.db"
        schema = (Path(__file__).resolve().parents[1] /
                  "research" / "forward_evidence" / "schema_forward.sql")
        con = sqlite3.connect(db)
        con.executescript(schema.read_text(encoding="utf-8"))
        for fam, n in closed.items():
            for i in range(n):
                con.execute(
                    "INSERT INTO trade_entries (signal_id, entry_date, direction,"
                    " entry_close_price, planned_exit_date, open) VALUES (?,?,?,?,?,0)",
                    (fam, f"2026-01-{i+1:02d}", 1, 100.0, f"2026-01-{i+8:02d}"))
                eid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
                con.execute(
                    "INSERT INTO trade_exits (entry_id, exit_date, exit_close_price,"
                    " cost_bps) VALUES (?,?,?,?)", (eid, f"2026-01-{i+8:02d}", 101.0, 15.0))
        for fam, n in open_.items():
            for i in range(n):
                con.execute(
                    "INSERT INTO trade_entries (signal_id, entry_date, direction,"
                    " entry_close_price, planned_exit_date, open) VALUES (?,?,?,?,?,1)",
                    (fam, "2026-02-01", 1, 100.0, "2026-02-08"))
        con.commit(); con.close()
        return db

    def test_reports_closed_and_open_counts(self, tmp_path):
        db = self._db(tmp_path, {"h5_vol_regime": 7, "g3_momentum_tsmom": 8},
                      {"g2_funding_level": 1})
        r = hc.check_forward_trades(db)
        assert r.severity == hc.OK
        assert r.detail["closed"] == {"h5_vol_regime": 7, "g3_momentum_tsmom": 8}
        assert r.detail["open"] == {"g2_funding_level": 1}
        assert "15 total" in r.message

    def test_never_escalates_above_ok(self, tmp_path):
        """Paging on a normal trade close would be noise. Any populated corpus
        must stay OK regardless of the counts."""
        for i, closed in enumerate(({}, {"h5_vol_regime": 1}, {"h5_vol_regime": 25})):
            sub = tmp_path / f"case{i}"
            sub.mkdir()
            assert hc.check_forward_trades(self._db(sub, closed, {})).severity == hc.OK

    def test_missing_db_is_info_not_a_failure(self, tmp_path):
        r = hc.check_forward_trades(tmp_path / "absent.db")
        assert r.severity == hc.INFO

    def test_unreadable_db_is_info_not_a_crash(self, tmp_path):
        bad = tmp_path / "evidence_forward.db"
        bad.write_bytes(b"not a database")
        assert hc.check_forward_trades(bad).severity == hc.INFO

    def test_knows_no_minimum(self):
        """FDB-8: a DSR minimum is operator-supplied with no default. Hardcoding
        the seven here would be the second source of truth OD-3 rejected."""
        import ast
        tree = ast.parse((_SCRIPTS_DIR / "healthcheck.py").read_text(encoding="utf-8"))
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "check_forward_trades")
        # strip the docstring: it EXPLAINS why no minimum is known, and naming
        # the rule in prose is the opposite of encoding a value.
        if ast.get_docstring(fn):
            fn.body = fn.body[1:]
        code = ast.unparse(fn).lower()
        for token in ("minimum", "dsr", "threshold", "floor"):
            assert token not in code, f"{token} must not appear in executable code"
        # and no literal family -> number mapping anywhere in the function
        families = ("h5_vol_regime", "g1_market_regime_sma200", "g3_momentum_tsmom",
                    "g1_trend_regime_ma_cross", "ivrv_vrp_spread",
                    "g2_funding_level", "g3_atr_expansion")
        for node in ast.walk(fn):
            if isinstance(node, ast.Dict):
                keys = [k.value for k in node.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)]
                assert not set(keys) & set(families), "no family->value table here"
        assert not any(f in code for f in families), "no family id is named"

    def test_opens_the_db_read_only(self, tmp_path):
        db = self._db(tmp_path, {"h5_vol_regime": 2}, {})
        before = db.read_bytes()
        hc.check_forward_trades(db)
        assert db.read_bytes() == before

    def _with_signal(self, tmp_path, fam, positions, open_trade=False):
        import sqlite3
        db = self._db(tmp_path, {}, {fam: 1} if open_trade else {})
        con = sqlite3.connect(db)
        for i, pos in enumerate(positions, start=1):
            con.execute("INSERT INTO run_log (signal_id, run_date, fn_hash, status)"
                        " VALUES (?,?,?, 'ok')", (fam, f"2026-08-{i:02d}", "h"))
            rid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
            con.execute("INSERT INTO signal_log (signal_id, day, position, run_id)"
                        " VALUES (?,?,?,?)", (fam, f"2026-08-{i:02d}", pos, rid))
        con.commit(); con.close()
        return db

    def test_flat_latest_signal_with_no_open_trade_is_reported_stalled(self, tmp_path):
        db = self._with_signal(tmp_path, "h5_vol_regime", [1.0, 1.0, 0.0])
        r = hc.check_forward_trades(db)
        assert r.detail["stalled"] == ["h5_vol_regime"]
        assert "stalled" in r.message
        assert r.severity == hc.OK, "a stall is visibility, not a page"

    def test_open_trade_suppresses_the_stalled_label(self, tmp_path):
        db = self._with_signal(tmp_path, "h5_vol_regime", [1.0, 0.0], open_trade=True)
        assert hc.check_forward_trades(db).detail["stalled"] == []

    def test_active_signal_is_not_stalled(self, tmp_path):
        db = self._with_signal(tmp_path, "h5_vol_regime", [0.0, 0.0, -1.0])
        assert hc.check_forward_trades(db).detail["stalled"] == []


# ---------------------------------------------------------------------------
# Newest-backup selection (2026-09-01)
#
# The glob admits more than the scheduled `{label}_{ts}.db` shape. A manual
# snapshot named `ledger_predeploy_<sha>_<ts>.db` sorts after every timestamped
# backup ('p' > '2'), so a name sort reported ITS age as the backup's age: a
# healthy 13h-old ledger backup was alerted as 48.7h stale.
# ---------------------------------------------------------------------------

class TestNewestBackupSelection:
    def test_manual_snapshot_does_not_shadow_a_newer_scheduled_backup(self, tmp_path):
        bdir = tmp_path / "ledger-backups"
        # older manual file whose NAME sorts last
        _touch_backup(bdir, "ledger", "predeploy_4cf0923_20260830T153100Z",
                      age_hours=48.7)
        # newer scheduled backup
        _touch_backup(bdir, "ledger", "20260901_022503", age_hours=13.4)
        r = hc.check_backup_freshness(bdir, label="ledger", check_name="ledger_backup")
        assert r.detail["newest_backup"] == "ledger_20260901_022503.db"
        assert r.severity == hc.OK, r.message

    def test_a_recent_manual_file_cannot_mask_stale_scheduled_backups(self, tmp_path):
        """The dangerous direction: mtime selection must still pick the newest
        file, so a fresh manual snapshot legitimately clears the check -- but a
        name sort could equally have hidden a stale one. Both cases resolve on
        mtime, which is the only honest ordering."""
        bdir = tmp_path / "ledger-backups"
        _touch_backup(bdir, "ledger", "20260825_020000", age_hours=200)
        _touch_backup(bdir, "ledger", "predeploy_abc_20260901T000000Z", age_hours=1)
        r = hc.check_backup_freshness(bdir, label="ledger", check_name="ledger_backup")
        assert r.detail["newest_backup"] == "ledger_predeploy_abc_20260901T000000Z.db"
        assert r.severity == hc.OK

    def test_all_stale_still_warns(self, tmp_path):
        bdir = tmp_path / "ledger-backups"
        _touch_backup(bdir, "ledger", "20260825_020000", age_hours=200)
        _touch_backup(bdir, "ledger", "predeploy_abc_20260826T000000Z", age_hours=150)
        r = hc.check_backup_freshness(bdir, label="ledger", check_name="ledger_backup")
        assert r.severity == hc.WARNING
        assert r.detail["newest_backup"] == "ledger_predeploy_abc_20260826T000000Z.db"

    def test_timestamped_only_dir_is_unaffected(self, tmp_path):
        """The pre-existing btc_trading case, where name and mtime agree."""
        bdir = tmp_path / "backups"
        _touch_backup(bdir, "btc_trading", "20260831_024000", age_hours=37)
        _touch_backup(bdir, "btc_trading", "20260901_024340", age_hours=13.5)
        r = hc.check_backup_freshness(bdir)
        assert r.detail["newest_backup"] == "btc_trading_20260901_024340.db"
        assert r.severity == hc.OK
