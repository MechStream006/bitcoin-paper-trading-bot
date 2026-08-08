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
