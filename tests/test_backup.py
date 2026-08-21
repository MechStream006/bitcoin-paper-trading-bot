"""Tests for scripts/backup.py — R2 additions (--fwd-hg, --ledger, --all extension).

Covers:
  - Regression: existing --evidence and default (btc_trading) behavior unchanged.
  - New --fwd-hg flag: backs up evidence_forward.db → data/evidence-fwd-backups/
  - New --ledger flag: backs up ledger.db → data/ledger-backups/
  - Extended --all: backs up all four databases.
  - Dry-run, early-return, failure-propagation, and retention-isolation behavior.

Run from repo root:
    conda run -n btc_bot pytest tests/test_backup.py -v
"""
from __future__ import annotations

import ast
import importlib.util
import json
import pathlib
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Module loader — scripts/ is not a package; load as a file module.
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


def _load_backup_module():
    spec = importlib.util.spec_from_file_location(
        "backup_module", _SCRIPTS_DIR / "backup.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


backup = _load_backup_module()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EVIDENCE_FWD_TABLES = {
    "run_log": 5, "signal_log": 5, "trade_entries": 2, "trade_exits": 0,
}
_LEDGER_TABLES = {
    "runs": 3, "gate_results": 6, "state_transitions": 2,
}
_TRADING_TABLES = {"option_quotes": 10}
_H6_TABLES = {"corpus_daily": 8}
_LABEL_STORE_TABLES = {
    "label_definitions": 2, "label_specifications": 4,
}


def _make_db(path: Path, tables: dict[str, int] | None = None) -> Path:
    """Create a minimal SQLite DB; optionally populate named tables with N rows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    if tables:
        for tbl, n in tables.items():
            conn.execute(f'CREATE TABLE "{tbl}" (id INTEGER PRIMARY KEY)')
            for _ in range(n):
                conn.execute(f'INSERT INTO "{tbl}" VALUES (NULL)')
    conn.commit()
    conn.close()
    return path


def _latest_backup(bdir: Path, label: str) -> Path | None:
    """Return the most-recently modified backup file for a given label, or None."""
    files = sorted(bdir.glob(f"{label}_*.db"), key=lambda f: f.stat().st_mtime)
    return files[-1] if files else None


def _integrity_ok(path: Path) -> bool:
    conn = sqlite3.connect(str(path))
    result = conn.execute("PRAGMA integrity_check").fetchall()
    conn.close()
    return result == [("ok",)]


def _setup_root(tmp: Path) -> None:
    """Create the directory tree expected by main() under a fake ROOT."""
    (tmp / "data" / "backups").mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "evidence-backups").mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "evidence-fwd-backups").mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "ledger-backups").mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "label-store-backups").mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "feature-store-backups").mkdir(parents=True, exist_ok=True)
    (tmp / "research" / "data").mkdir(parents=True, exist_ok=True)
    (tmp / "research" / "evidence_library").mkdir(parents=True, exist_ok=True)
    (tmp / "research" / "label_store").mkdir(parents=True, exist_ok=True)
    (tmp / "research" / "feature_store").mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Direct run_backup() tests — behavior of the core backup engine
# ---------------------------------------------------------------------------

def test_run_backup_creates_backup_file(tmp_path):
    src = _make_db(tmp_path / "source.db")
    bdir = tmp_path / "backups"
    rc = backup.run_backup(src, bdir, dry_run=False, db_label="test")
    assert rc == 0
    assert _latest_backup(bdir, "test") is not None


def test_run_backup_integrity_ok(tmp_path):
    src = _make_db(tmp_path / "source.db")
    bdir = tmp_path / "backups"
    backup.run_backup(src, bdir, dry_run=False, db_label="test")
    bfile = _latest_backup(bdir, "test")
    assert _integrity_ok(bfile)


def test_run_backup_manifest_row_counts_populated(tmp_path):
    src = _make_db(tmp_path / "source.db", _EVIDENCE_FWD_TABLES)
    bdir = tmp_path / "backups"
    backup.run_backup(src, bdir, dry_run=False, db_label="fwd_hg")
    bfile = _latest_backup(bdir, "fwd_hg")
    mfile = bfile.parent / (bfile.stem + ".manifest.json")
    manifest = json.loads(mfile.read_text())
    counts = manifest["row_counts"]
    assert counts["run_log"] == 5
    assert counts["signal_log"] == 5
    assert counts["trade_entries"] == 2
    assert counts["trade_exits"] == 0


def test_run_backup_manifest_ts_range_null_for_evidence_forward_schema(tmp_path):
    """ts_range is always null for evidence_forward.db and ledger.db schemas."""
    src = _make_db(tmp_path / "evidence_forward.db", _EVIDENCE_FWD_TABLES)
    bdir = tmp_path / "backups"
    backup.run_backup(src, bdir, dry_run=False, db_label="fwd_hg")
    bfile = _latest_backup(bdir, "fwd_hg")
    mfile = bfile.parent / (bfile.stem + ".manifest.json")
    manifest = json.loads(mfile.read_text())
    assert manifest["ts_range"] == {"min": None, "max": None}


def test_run_backup_manifest_ts_range_null_for_ledger_schema(tmp_path):
    src = _make_db(tmp_path / "ledger.db", _LEDGER_TABLES)
    bdir = tmp_path / "backups"
    backup.run_backup(src, bdir, dry_run=False, db_label="ledger")
    bfile = _latest_backup(bdir, "ledger")
    mfile = bfile.parent / (bfile.stem + ".manifest.json")
    manifest = json.loads(mfile.read_text())
    assert manifest["ts_range"] == {"min": None, "max": None}


def test_run_backup_dry_run_no_files(tmp_path):
    src = _make_db(tmp_path / "source.db")
    bdir = tmp_path / "backups"
    rc = backup.run_backup(src, bdir, dry_run=True, db_label="fwd_hg")
    assert rc == 0
    assert list(bdir.glob("fwd_hg_*.db")) == []


def test_run_backup_missing_required_db_returns_1(tmp_path):
    """A missing REQUIRED database is still a hard failure (Option A)."""
    missing = tmp_path / "does_not_exist.db"
    bdir = tmp_path / "backups"
    rc = backup.run_backup(missing, bdir, dry_run=False, db_label="btc_trading")
    assert rc == 1
    assert list(bdir.glob("btc_trading_*.db")) == []


def test_run_backup_missing_optional_db_returns_0(tmp_path):
    """A missing OPTIONAL database is SKIPPED, not failed (PROD-1 fix).
    This test previously asserted rc == 1 for db_label="test"; that label is
    not in REQUIRED_DB_LABELS, so under Option A it is optional."""
    missing = tmp_path / "does_not_exist.db"
    bdir = tmp_path / "backups"
    rc = backup.run_backup(missing, bdir, dry_run=False, db_label="label_store")
    assert rc == 0
    assert list(bdir.glob("label_store_*.db")) == []


def test_run_backup_manifest_db_label_matches(tmp_path):
    src = _make_db(tmp_path / "source.db")
    bdir = tmp_path / "backups"
    backup.run_backup(src, bdir, dry_run=False, db_label="fwd_hg")
    bfile = _latest_backup(bdir, "fwd_hg")
    mfile = bfile.parent / (bfile.stem + ".manifest.json")
    manifest = json.loads(mfile.read_text())
    assert manifest["db_label"] == "fwd_hg"


def test_run_backup_ledger_row_counts(tmp_path):
    src = _make_db(tmp_path / "ledger.db", _LEDGER_TABLES)
    bdir = tmp_path / "backups"
    backup.run_backup(src, bdir, dry_run=False, db_label="ledger")
    bfile = _latest_backup(bdir, "ledger")
    mfile = bfile.parent / (bfile.stem + ".manifest.json")
    manifest = json.loads(mfile.read_text())
    counts = manifest["row_counts"]
    assert counts["runs"] == 3
    assert counts["gate_results"] == 6
    assert counts["state_transitions"] == 2


# ---------------------------------------------------------------------------
# main() regression tests — existing flags must be byte-identical in behavior
# ---------------------------------------------------------------------------

def test_default_backs_up_trading_db(tmp_path, monkeypatch):
    _setup_root(tmp_path)
    _make_db(tmp_path / "data" / "btc_trading.db", _TRADING_TABLES)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py"])
    rc = backup.main()
    assert rc == 0
    bdir = tmp_path / "data" / "backups"
    assert _latest_backup(bdir, "btc_trading") is not None
    # Other backup dirs must remain empty
    assert _latest_backup(tmp_path / "data" / "evidence-fwd-backups", "fwd_hg") is None
    assert _latest_backup(tmp_path / "data" / "ledger-backups", "ledger") is None


def test_evidence_flag_backs_up_h6_db(tmp_path, monkeypatch):
    _setup_root(tmp_path)
    _make_db(tmp_path / "research" / "data" / "forward_evidence.db", _H6_TABLES)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--evidence"])
    rc = backup.main()
    assert rc == 0
    bdir = tmp_path / "data" / "evidence-backups"
    bfile = _latest_backup(bdir, "evidence")
    assert bfile is not None
    manifest = json.loads((bfile.parent / (bfile.stem + ".manifest.json")).read_text())
    assert manifest["row_counts"]["corpus_daily"] == 8


def test_evidence_early_return_does_not_back_up_trading(tmp_path, monkeypatch):
    """--evidence alone must not touch btc_trading.db backup directory."""
    _setup_root(tmp_path)
    _make_db(tmp_path / "research" / "data" / "forward_evidence.db", _H6_TABLES)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--evidence"])
    backup.main()
    assert _latest_backup(tmp_path / "data" / "backups", "btc_trading") is None


# ---------------------------------------------------------------------------
# New --fwd-hg flag tests
# ---------------------------------------------------------------------------

def test_fwd_hg_flag_backs_up_ivrv_db(tmp_path, monkeypatch):
    _setup_root(tmp_path)
    _make_db(tmp_path / "research" / "data" / "evidence_forward.db", _EVIDENCE_FWD_TABLES)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--fwd-hg"])
    rc = backup.main()
    assert rc == 0
    bfile = _latest_backup(tmp_path / "data" / "evidence-fwd-backups", "fwd_hg")
    assert bfile is not None
    assert _integrity_ok(bfile)


def test_fwd_hg_correct_label_in_filename(tmp_path, monkeypatch):
    _setup_root(tmp_path)
    _make_db(tmp_path / "research" / "data" / "evidence_forward.db", _EVIDENCE_FWD_TABLES)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--fwd-hg"])
    backup.main()
    bfile = _latest_backup(tmp_path / "data" / "evidence-fwd-backups", "fwd_hg")
    assert bfile.name.startswith("fwd_hg_")


def test_fwd_hg_correct_backup_dir(tmp_path, monkeypatch):
    _setup_root(tmp_path)
    _make_db(tmp_path / "research" / "data" / "evidence_forward.db", _EVIDENCE_FWD_TABLES)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--fwd-hg"])
    backup.main()
    # Must appear in evidence-fwd-backups, NOT in the default backups dir
    assert _latest_backup(tmp_path / "data" / "evidence-fwd-backups", "fwd_hg") is not None
    assert _latest_backup(tmp_path / "data" / "backups", "fwd_hg") is None


def test_fwd_hg_early_return_no_other_backups(tmp_path, monkeypatch):
    """--fwd-hg alone must not create ledger or trading backups."""
    _setup_root(tmp_path)
    _make_db(tmp_path / "research" / "data" / "evidence_forward.db", _EVIDENCE_FWD_TABLES)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--fwd-hg"])
    backup.main()
    assert _latest_backup(tmp_path / "data" / "ledger-backups", "ledger") is None
    assert _latest_backup(tmp_path / "data" / "backups", "btc_trading") is None


def test_fwd_hg_dry_run_no_files(tmp_path, monkeypatch):
    _setup_root(tmp_path)
    _make_db(tmp_path / "research" / "data" / "evidence_forward.db", _EVIDENCE_FWD_TABLES)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--fwd-hg", "--dry-run"])
    rc = backup.main()
    assert rc == 0
    assert list((tmp_path / "data" / "evidence-fwd-backups").glob("fwd_hg_*.db")) == []


# ---------------------------------------------------------------------------
# New --ledger flag tests
# ---------------------------------------------------------------------------

def test_ledger_flag_backs_up_ledger_db(tmp_path, monkeypatch):
    _setup_root(tmp_path)
    _make_db(tmp_path / "research" / "evidence_library" / "ledger.db", _LEDGER_TABLES)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--ledger"])
    rc = backup.main()
    assert rc == 0
    bfile = _latest_backup(tmp_path / "data" / "ledger-backups", "ledger")
    assert bfile is not None
    assert _integrity_ok(bfile)


def test_ledger_correct_label_in_filename(tmp_path, monkeypatch):
    _setup_root(tmp_path)
    _make_db(tmp_path / "research" / "evidence_library" / "ledger.db", _LEDGER_TABLES)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--ledger"])
    backup.main()
    bfile = _latest_backup(tmp_path / "data" / "ledger-backups", "ledger")
    assert bfile.name.startswith("ledger_")


def test_ledger_correct_backup_dir(tmp_path, monkeypatch):
    _setup_root(tmp_path)
    _make_db(tmp_path / "research" / "evidence_library" / "ledger.db", _LEDGER_TABLES)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--ledger"])
    backup.main()
    assert _latest_backup(tmp_path / "data" / "ledger-backups", "ledger") is not None
    assert _latest_backup(tmp_path / "data" / "backups", "ledger") is None


def test_ledger_early_return_no_other_backups(tmp_path, monkeypatch):
    """--ledger alone must not create fwd_hg or trading backups."""
    _setup_root(tmp_path)
    _make_db(tmp_path / "research" / "evidence_library" / "ledger.db", _LEDGER_TABLES)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--ledger"])
    backup.main()
    assert _latest_backup(tmp_path / "data" / "evidence-fwd-backups", "fwd_hg") is None
    assert _latest_backup(tmp_path / "data" / "backups", "btc_trading") is None


def test_ledger_dry_run_no_files(tmp_path, monkeypatch):
    _setup_root(tmp_path)
    _make_db(tmp_path / "research" / "evidence_library" / "ledger.db", _LEDGER_TABLES)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--ledger", "--dry-run"])
    rc = backup.main()
    assert rc == 0
    assert list((tmp_path / "data" / "ledger-backups").glob("ledger_*.db")) == []


# ---------------------------------------------------------------------------
# New --label-store flag tests (Phase 3 WBS 3.10, LS-DEC-001)
# ---------------------------------------------------------------------------

def test_label_store_flag_backs_up_label_store_db(tmp_path, monkeypatch):
    _setup_root(tmp_path)
    _make_db(tmp_path / "research" / "label_store" / "label_store.db", _LABEL_STORE_TABLES)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--label-store"])
    rc = backup.main()
    assert rc == 0
    bfile = _latest_backup(tmp_path / "data" / "label-store-backups", "label_store")
    assert bfile is not None
    assert _integrity_ok(bfile)


def test_label_store_correct_label_in_filename(tmp_path, monkeypatch):
    _setup_root(tmp_path)
    _make_db(tmp_path / "research" / "label_store" / "label_store.db", _LABEL_STORE_TABLES)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--label-store"])
    backup.main()
    bfile = _latest_backup(tmp_path / "data" / "label-store-backups", "label_store")
    assert bfile.name.startswith("label_store_")


def test_label_store_correct_backup_dir(tmp_path, monkeypatch):
    _setup_root(tmp_path)
    _make_db(tmp_path / "research" / "label_store" / "label_store.db", _LABEL_STORE_TABLES)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--label-store"])
    backup.main()
    assert _latest_backup(tmp_path / "data" / "label-store-backups", "label_store") is not None
    assert _latest_backup(tmp_path / "data" / "backups", "label_store") is None


def test_label_store_early_return_no_other_backups(tmp_path, monkeypatch):
    """--label-store alone must not create ledger, fwd_hg, or trading backups."""
    _setup_root(tmp_path)
    _make_db(tmp_path / "research" / "label_store" / "label_store.db", _LABEL_STORE_TABLES)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--label-store"])
    backup.main()
    assert _latest_backup(tmp_path / "data" / "ledger-backups", "ledger") is None
    assert _latest_backup(tmp_path / "data" / "evidence-fwd-backups", "fwd_hg") is None
    assert _latest_backup(tmp_path / "data" / "backups", "btc_trading") is None


def test_label_store_dry_run_no_files(tmp_path, monkeypatch):
    _setup_root(tmp_path)
    _make_db(tmp_path / "research" / "label_store" / "label_store.db", _LABEL_STORE_TABLES)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--label-store", "--dry-run"])
    rc = backup.main()
    assert rc == 0
    assert list((tmp_path / "data" / "label-store-backups").glob("label_store_*.db")) == []


def test_label_store_row_counts_in_manifest(tmp_path, monkeypatch):
    """The manifest must reflect BOTH tables co-located per LS-DEC-001 —
    proving the whole-file backup unit covers registry and value-store
    content together, not just whichever table happens to be non-empty."""
    _setup_root(tmp_path)
    _make_db(tmp_path / "research" / "label_store" / "label_store.db", _LABEL_STORE_TABLES)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--label-store"])
    backup.main()
    bfile = _latest_backup(tmp_path / "data" / "label-store-backups", "label_store")
    manifest = json.loads((bfile.parent / (bfile.stem + ".manifest.json")).read_text())
    assert manifest["row_counts"]["label_definitions"] == 2
    assert manifest["row_counts"]["label_specifications"] == 4


# ---------------------------------------------------------------------------
# Extended --all flag tests
# ---------------------------------------------------------------------------

def test_all_flag_covers_all_five_dbs(tmp_path, monkeypatch):
    _setup_root(tmp_path)
    _make_db(tmp_path / "data" / "btc_trading.db", _TRADING_TABLES)
    _make_db(tmp_path / "research" / "data" / "forward_evidence.db", _H6_TABLES)
    _make_db(tmp_path / "research" / "data" / "evidence_forward.db", _EVIDENCE_FWD_TABLES)
    _make_db(tmp_path / "research" / "evidence_library" / "ledger.db", _LEDGER_TABLES)
    _make_db(tmp_path / "research" / "label_store" / "label_store.db", _LABEL_STORE_TABLES)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--all"])
    rc = backup.main()
    assert rc == 0
    assert _latest_backup(tmp_path / "data" / "backups", "btc_trading") is not None
    assert _latest_backup(tmp_path / "data" / "evidence-backups", "evidence") is not None
    assert _latest_backup(tmp_path / "data" / "evidence-fwd-backups", "fwd_hg") is not None
    assert _latest_backup(tmp_path / "data" / "ledger-backups", "ledger") is not None
    assert _latest_backup(tmp_path / "data" / "label-store-backups", "label_store") is not None


def test_all_flag_return_code_is_max_of_all(tmp_path, monkeypatch):
    """--all exit code is still the max of the individual return codes; what
    changed under Option A (2026-08-06, PROD-1) is that a missing OPTIONAL
    database contributes 0 rather than 1.

    This test previously provided 3 of 4 DBs and asserted rc == 1. The
    missing one (evidence_forward.db) is optional, so it is now SKIPPED and
    the max is 0. The max semantics themselves are unchanged -- proven by
    the required-database case below."""
    _setup_root(tmp_path)
    _make_db(tmp_path / "data" / "btc_trading.db", _TRADING_TABLES)
    _make_db(tmp_path / "research" / "data" / "forward_evidence.db", _H6_TABLES)
    _make_db(tmp_path / "research" / "evidence_library" / "ledger.db", _LEDGER_TABLES)
    # evidence_forward.db intentionally missing -> optional -> rc_fwd_hg = 0
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--all"])
    rc = backup.main()
    assert rc == 0  # max(0, 0, 0, 0, 0)


def test_all_flag_still_propagates_a_required_failure(tmp_path, monkeypatch):
    """The max must still surface a REQUIRED database's absence."""
    _setup_root(tmp_path)
    _make_db(tmp_path / "research" / "data" / "forward_evidence.db", _H6_TABLES)
    _make_db(tmp_path / "research" / "evidence_library" / "ledger.db", _LEDGER_TABLES)
    # btc_trading.db intentionally missing -> required -> rc_trading = 1
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--all"])
    assert backup.main() == 1


def test_all_failure_in_one_does_not_abort_others(tmp_path, monkeypatch):
    """A missing DB for one backup must not prevent the others from completing."""
    _setup_root(tmp_path)
    # Only evidence_forward.db is missing; the other three exist.
    _make_db(tmp_path / "data" / "btc_trading.db", _TRADING_TABLES)
    _make_db(tmp_path / "research" / "data" / "forward_evidence.db", _H6_TABLES)
    _make_db(tmp_path / "research" / "evidence_library" / "ledger.db", _LEDGER_TABLES)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--all"])
    backup.main()
    # The three present DBs must have been backed up despite one failing.
    assert _latest_backup(tmp_path / "data" / "backups", "btc_trading") is not None
    assert _latest_backup(tmp_path / "data" / "evidence-backups", "evidence") is not None
    assert _latest_backup(tmp_path / "data" / "ledger-backups", "ledger") is not None


def test_all_dry_run_prints_five_lines(tmp_path, monkeypatch, capsys):
    _setup_root(tmp_path)
    # DBs don't need to exist for dry-run (run_backup checks existence first,
    # but dry-run branch actually IS guarded — missing DB returns 1 even in dry-run).
    # Create all five so we get clean [DRY RUN] output for all five.
    _make_db(tmp_path / "data" / "btc_trading.db")
    _make_db(tmp_path / "research" / "data" / "forward_evidence.db")
    _make_db(tmp_path / "research" / "data" / "evidence_forward.db")
    _make_db(tmp_path / "research" / "evidence_library" / "ledger.db")
    _make_db(tmp_path / "research" / "label_store" / "label_store.db")
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--all", "--dry-run"])
    rc = backup.main()
    assert rc == 0
    out = capsys.readouterr().out
    dry_run_lines = [ln for ln in out.splitlines() if "[DRY RUN] would create" in ln]
    assert len(dry_run_lines) == 5


def test_all_with_specific_flag_still_backs_up_trading(tmp_path, monkeypatch):
    """--all combined with a specific flag must still produce all five backups.

    Regression for the L285 edge case: before the fix, `--all --evidence`
    evaluated `if not (True or False or False)` → False and silently skipped
    btc_trading.db. After the fix, `args.backup_all` short-circuits the guard.
    """
    _setup_root(tmp_path)
    _make_db(tmp_path / "data" / "btc_trading.db", _TRADING_TABLES)
    _make_db(tmp_path / "research" / "data" / "forward_evidence.db", _H6_TABLES)
    _make_db(tmp_path / "research" / "data" / "evidence_forward.db", _EVIDENCE_FWD_TABLES)
    _make_db(tmp_path / "research" / "evidence_library" / "ledger.db", _LEDGER_TABLES)
    _make_db(tmp_path / "research" / "label_store" / "label_store.db", _LABEL_STORE_TABLES)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--all", "--evidence"])
    rc = backup.main()
    assert rc == 0
    # All five must be present — trading must not be skipped.
    assert _latest_backup(tmp_path / "data" / "backups", "btc_trading") is not None
    assert _latest_backup(tmp_path / "data" / "evidence-backups", "evidence") is not None
    assert _latest_backup(tmp_path / "data" / "evidence-fwd-backups", "fwd_hg") is not None
    assert _latest_backup(tmp_path / "data" / "ledger-backups", "ledger") is not None
    assert _latest_backup(tmp_path / "data" / "label-store-backups", "label_store") is not None


# ---------------------------------------------------------------------------
# Retention isolation test
# ---------------------------------------------------------------------------

def test_retention_globs_do_not_cross_directories(tmp_path):
    """fwd_hg_*.db in evidence-fwd-backups must not affect ledger_*.db in ledger-backups."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    fwd_dir    = tmp_path / "evidence-fwd-backups"
    ledger_dir = tmp_path / "ledger-backups"
    fwd_dir.mkdir()
    ledger_dir.mkdir()

    # Plant an old-looking fwd_hg file (would be deleted by retention) in fwd_dir.
    old_fwd = fwd_dir / "fwd_hg_20200101_000000.db"
    old_fwd.touch()

    # Plant a current ledger file (should be kept) in ledger_dir. Built from
    # `now` rather than a hardcoded date, so it always falls inside the daily
    # retention window (KEEP_DAILY_DAYS) no matter when the test runs.
    cur_ledger = ledger_dir / f"ledger_{now.strftime('%Y%m%d_%H%M%S')}.db"
    cur_ledger.touch()

    # Run retention for fwd_hg in fwd_dir — should NOT touch ledger_dir.
    backup._apply_retention(fwd_dir, now, dry_run=False, db_label="fwd_hg")
    backup._apply_retention(ledger_dir, now, dry_run=False, db_label="ledger")

    assert not old_fwd.exists()     # correctly deleted by fwd_hg retention
    assert cur_ledger.exists()      # untouched by fwd_hg retention


# ---------------------------------------------------------------------------
# Retention: .age / .age-manifest.json pruned alongside their plaintext
# (P1 Increment 6 -- closes the on-host ciphertext accumulation gap)
# ---------------------------------------------------------------------------

def test_retention_prunes_age_artifacts_with_expired_plaintext(tmp_path):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    bdir = tmp_path / "ledger-backups"
    bdir.mkdir()

    old = bdir / "ledger_20200101_000000.db"
    old.touch()
    (bdir / "ledger_20200101_000000.manifest.json").write_text("{}")
    ciphertext   = bdir / "ledger_20200101_000000.db.age"
    age_manifest = bdir / "ledger_20200101_000000.db.age-manifest.json"
    ciphertext.write_bytes(b"age-encryption.org/v1\n...")
    age_manifest.write_text("{}")

    backup._apply_retention(bdir, now, dry_run=False, db_label="ledger")

    assert not old.exists()
    assert not ciphertext.exists()
    assert not age_manifest.exists()


def test_retention_keeps_age_artifacts_for_in_window_plaintext(tmp_path):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    bdir = tmp_path / "ledger-backups"
    bdir.mkdir()

    cur = bdir / f"ledger_{now.strftime('%Y%m%d_%H%M%S')}.db"
    cur.touch()
    ciphertext   = bdir / (cur.name + ".age")
    age_manifest = bdir / (cur.name + ".age-manifest.json")
    ciphertext.write_bytes(b"age-encryption.org/v1\n...")
    age_manifest.write_text("{}")

    backup._apply_retention(bdir, now, dry_run=False, db_label="ledger")

    assert cur.exists()
    assert ciphertext.exists()
    assert age_manifest.exists()


def test_retention_dry_run_does_not_delete_age_artifacts(tmp_path):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    bdir = tmp_path / "ledger-backups"
    bdir.mkdir()

    old = bdir / "ledger_20200101_000000.db"
    old.touch()
    ciphertext = bdir / "ledger_20200101_000000.db.age"
    ciphertext.write_bytes(b"age-encryption.org/v1\n...")

    backup._apply_retention(bdir, now, dry_run=True, db_label="ledger")

    assert old.exists()
    assert ciphertext.exists()


# ---------------------------------------------------------------------------
# Feature Store backup -- operational-defect fix
#
# `feature_store.db` held 17 Active Feature Specifications and 17
# `feature.register` receipts with NO backup path of any kind: no flag, no
# `run_backup()` call, and `--all` did not reach it. These tests mirror the
# Label Store's exactly, because the requirement was identical operational
# treatment, not a new mechanism.
# ---------------------------------------------------------------------------

_FEATURE_STORE_TABLES = {
    "feature_definitions": 3, "feature_specifications": 17,
    "governance_receipts": 17,
}

_sha256 = backup._sha256


def _load_restore_module():
    spec = importlib.util.spec_from_file_location(
        "restore_module_fs", _SCRIPTS_DIR / "restore.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _feature_store_db(tmp_path: Path) -> Path:
    return _make_db(
        tmp_path / "research" / "feature_store" / "feature_store.db",
        _FEATURE_STORE_TABLES,
    )


def test_feature_store_backup_succeeds(tmp_path, monkeypatch):
    _setup_root(tmp_path)
    _feature_store_db(tmp_path)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--feature-store"])
    assert backup.main() == 0
    bfile = _latest_backup(tmp_path / "data" / "feature-store-backups", "feature_store")
    assert bfile is not None
    assert _integrity_ok(bfile)


def test_feature_store_correct_label_in_filename(tmp_path, monkeypatch):
    _setup_root(tmp_path)
    _feature_store_db(tmp_path)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--feature-store"])
    backup.main()
    bfile = _latest_backup(tmp_path / "data" / "feature-store-backups", "feature_store")
    assert bfile.name.startswith("feature_store_")


def test_feature_store_correct_backup_dir(tmp_path, monkeypatch):
    _setup_root(tmp_path)
    _feature_store_db(tmp_path)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--feature-store"])
    backup.main()
    assert _latest_backup(
        tmp_path / "data" / "feature-store-backups", "feature_store") is not None
    assert _latest_backup(tmp_path / "data" / "backups", "feature_store") is None


def test_feature_store_early_return_no_other_backups(tmp_path, monkeypatch):
    """--feature-store alone must not create trading, ledger or other backups."""
    _setup_root(tmp_path)
    _feature_store_db(tmp_path)
    _make_db(tmp_path / "data" / "btc_trading.db", _TRADING_TABLES)
    _make_db(tmp_path / "research" / "evidence_library" / "ledger.db", _LEDGER_TABLES)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--feature-store"])
    backup.main()
    assert _latest_backup(tmp_path / "data" / "backups", "btc_trading") is None
    assert _latest_backup(tmp_path / "data" / "ledger-backups", "ledger") is None


def test_feature_store_dry_run_writes_nothing(tmp_path, monkeypatch):
    _setup_root(tmp_path)
    _feature_store_db(tmp_path)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--feature-store", "--dry-run"])
    assert backup.main() == 0
    bdir = tmp_path / "data" / "feature-store-backups"
    if bdir.exists():
        assert list(bdir.glob("feature_store_*.db")) == []


def test_feature_store_manifest_written_with_row_counts(tmp_path, monkeypatch):
    _setup_root(tmp_path)
    _feature_store_db(tmp_path)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--feature-store"])
    backup.main()
    bfile = _latest_backup(tmp_path / "data" / "feature-store-backups", "feature_store")
    manifest = bfile.parent / (bfile.stem + ".manifest.json")
    assert manifest.exists()
    data = json.loads(manifest.read_text())
    assert data["row_counts"]["feature_specifications"] == 17
    assert data["row_counts"]["governance_receipts"] == 17


def test_feature_store_manifest_sha_matches_the_written_bytes(tmp_path, monkeypatch):
    """The manifest must describe the file that was actually written.

    This is the property `restore.py` depends on: it aborts the swap when the
    recorded SHA-256 does not match the backup on disk.
    """
    _setup_root(tmp_path)
    _feature_store_db(tmp_path)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--feature-store"])
    backup.main()
    bfile = _latest_backup(tmp_path / "data" / "feature-store-backups", "feature_store")
    manifest = json.loads((bfile.parent / (bfile.stem + ".manifest.json")).read_text())
    assert manifest["sha256"] == _sha256(bfile)


def test_feature_store_missing_is_skipped_not_failed(tmp_path, monkeypatch):
    """OPTIONAL classification (PROD-1): absence must not fail the run.

    On EC2 the Feature Store does not exist. Treating that as an error would
    make `--all` exit 1 every day, which is the exact failure PROD-1 fixed.
    """
    _setup_root(tmp_path)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--feature-store"])
    assert backup.main() == 0


def test_all_flag_now_covers_feature_store(tmp_path, monkeypatch):
    """The defect itself: --all reached five stores and silently skipped this one."""
    _setup_root(tmp_path)
    _make_db(tmp_path / "data" / "btc_trading.db", _TRADING_TABLES)
    _make_db(tmp_path / "research" / "data" / "forward_evidence.db", _H6_TABLES)
    _make_db(tmp_path / "research" / "data" / "evidence_forward.db", _EVIDENCE_FWD_TABLES)
    _make_db(tmp_path / "research" / "evidence_library" / "ledger.db", _LEDGER_TABLES)
    _make_db(tmp_path / "research" / "label_store" / "label_store.db", _LABEL_STORE_TABLES)
    _feature_store_db(tmp_path)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--all"])
    assert backup.main() == 0
    assert _latest_backup(
        tmp_path / "data" / "feature-store-backups", "feature_store") is not None


def test_all_still_covers_every_other_store(tmp_path, monkeypatch):
    """Regression: adding a sixth store must not drop any of the other five."""
    _setup_root(tmp_path)
    _make_db(tmp_path / "data" / "btc_trading.db", _TRADING_TABLES)
    _make_db(tmp_path / "research" / "data" / "forward_evidence.db", _H6_TABLES)
    _make_db(tmp_path / "research" / "data" / "evidence_forward.db", _EVIDENCE_FWD_TABLES)
    _make_db(tmp_path / "research" / "evidence_library" / "ledger.db", _LEDGER_TABLES)
    _make_db(tmp_path / "research" / "label_store" / "label_store.db", _LABEL_STORE_TABLES)
    _feature_store_db(tmp_path)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--all"])
    backup.main()
    for bdir, label in (("backups", "btc_trading"), ("evidence-backups", "evidence"),
                        ("evidence-fwd-backups", "fwd_hg"), ("ledger-backups", "ledger"),
                        ("label-store-backups", "label_store")):
        assert _latest_backup(tmp_path / "data" / bdir, label) is not None, label


def test_all_dry_run_mentions_feature_store(tmp_path, monkeypatch, capsys):
    _setup_root(tmp_path)
    _make_db(tmp_path / "data" / "btc_trading.db")
    _feature_store_db(tmp_path)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--all", "--dry-run"])
    backup.main()
    assert "feature_store" in capsys.readouterr().out


def test_feature_store_restores_through_the_generic_restore_path(tmp_path, monkeypatch):
    """`restore.py` is store-agnostic and needed no change.

    The defect was the absent backup, not absent restore support. This proves
    the existing restore path recovers a Feature Store backup end to end,
    including SHA-256 manifest verification.
    """
    restore_mod = _load_restore_module()

    _setup_root(tmp_path)
    live = _feature_store_db(tmp_path)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--feature-store"])
    backup.main()
    bfile = _latest_backup(tmp_path / "data" / "feature-store-backups", "feature_store")

    conn = sqlite3.connect(str(live))
    conn.execute("DROP TABLE feature_specifications")
    conn.commit()
    conn.close()

    assert restore_mod.run_restore(bfile, live, "btcbot", skip_service=True) == 0
    conn = sqlite3.connect(str(live))
    n = conn.execute("SELECT COUNT(*) FROM feature_specifications").fetchone()[0]
    conn.close()
    assert n == 17


def test_feature_store_restore_refuses_on_sha_mismatch(tmp_path, monkeypatch):
    """A tampered Feature Store backup must not reach the live database."""
    restore_mod = _load_restore_module()

    _setup_root(tmp_path)
    live = _feature_store_db(tmp_path)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--feature-store"])
    backup.main()
    bfile = _latest_backup(tmp_path / "data" / "feature-store-backups", "feature_store")

    manifest_file = bfile.parent / (bfile.stem + ".manifest.json")
    manifest = json.loads(manifest_file.read_text())
    manifest["sha256"] = "0" * 64
    manifest_file.write_text(json.dumps(manifest))

    before = _sha256(live)
    assert restore_mod.run_restore(bfile, live, "btcbot", skip_service=True) != 0
    assert _sha256(live) == before


def test_all_exit_code_surfaces_a_feature_store_failure(tmp_path, monkeypatch):
    """`--all` must fold rc_feature_store into its max.

    Added after TV3: removing `rc_feature_store` from the `max(...)` call
    failed nothing, because no test made this backup fail. A store whose
    failure cannot change the exit code is not really monitored.
    """
    _setup_root(tmp_path)
    _make_db(tmp_path / "data" / "btc_trading.db", _TRADING_TABLES)
    _feature_store_db(tmp_path)
    monkeypatch.setattr(backup, "ROOT", tmp_path)

    real_run_backup = backup.run_backup

    def failing(db_path, backup_dir, dry_run, db_label=None, **kw):
        if db_label == "feature_store":
            return 1
        return real_run_backup(db_path, backup_dir, dry_run, db_label=db_label, **kw)

    monkeypatch.setattr(backup, "run_backup", failing)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--all"])
    assert backup.main() == 1


def test_feature_store_only_exit_code_is_its_own(tmp_path, monkeypatch):
    """`--feature-store` alone returns that store's own return code."""
    _setup_root(tmp_path)
    monkeypatch.setattr(backup, "ROOT", tmp_path)

    def failing(db_path, backup_dir, dry_run, db_label=None, **kw):
        return 1 if db_label == "feature_store" else 0

    monkeypatch.setattr(backup, "run_backup", failing)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--feature-store"])
    assert backup.main() == 1


# ---------------------------------------------------------------------------
# Manifest snapshot-consistency (2026-08-17 defect)
#
# VACUUM INTO freezes the artifact at time T. Reading row counts from the LIVE
# connection afterwards describes time T+duration, and on a large continuously
# written database that gap spans real writes: the production btc_trading.db
# backup recorded 66 option_quotes + 1 collection_metrics row MORE than the
# artifact contained -- exactly one 1-minute cycle at a 66-symbol universe.
# The manifest must describe the artifact, exactly, with no tolerance.
# ---------------------------------------------------------------------------

def _sha256_of(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def test_manifest_describes_snapshot_not_rows_added_after_vacuum(tmp_path, monkeypatch):
    """Deterministic reproduction of the race, with no large fixture.

    A writer commits to the SOURCE database at the exact moment the metadata
    read begins -- i.e. after VACUUM INTO has already frozen the artifact.
    Under the old code (counts read from the live connection) the manifest
    reported 15; the artifact held 10. This asserts it reports 10.
    """
    src = _make_db(tmp_path / "source.db", {"option_quotes": 10})
    bdir = tmp_path / "backups"

    real_counts = backup._row_counts
    state = {"injected": False}

    def _inject_then_count(conn):
        if not state["injected"]:
            state["injected"] = True          # simulate one collection cycle
            w = sqlite3.connect(str(src))
            for _ in range(5):
                w.execute('INSERT INTO "option_quotes" VALUES (NULL)')
            w.commit()
            w.close()
        return real_counts(conn)

    monkeypatch.setattr(backup, "_row_counts", _inject_then_count)
    assert backup.run_backup(src, bdir, dry_run=False, db_label="test") == 0
    assert state["injected"], "writer never ran — test would be vacuous"

    bfile = _latest_backup(bdir, "test")
    manifest = json.loads((bfile.parent / (bfile.stem + ".manifest.json")).read_text())

    # The live DB now holds 15; the artifact holds 10. The manifest must say 10.
    live = sqlite3.connect(str(src))
    assert live.execute("SELECT COUNT(*) FROM option_quotes").fetchone()[0] == 15
    live.close()

    snap = sqlite3.connect(str(bfile))
    assert snap.execute("SELECT COUNT(*) FROM option_quotes").fetchone()[0] == 10
    snap.close()

    assert manifest["row_counts"]["option_quotes"] == 10


def test_manifest_row_counts_match_the_artifact_exactly(tmp_path):
    """Every table in the manifest equals the artifact's own count."""
    src = _make_db(tmp_path / "source.db",
                   {"option_quotes": 7, "collection_metrics": 3, "instruments": 2})
    bdir = tmp_path / "backups"
    assert backup.run_backup(src, bdir, dry_run=False, db_label="test") == 0

    bfile = _latest_backup(bdir, "test")
    manifest = json.loads((bfile.parent / (bfile.stem + ".manifest.json")).read_text())

    snap = sqlite3.connect(str(bfile))
    try:
        for tbl, expected in manifest["row_counts"].items():
            actual = snap.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0]
            assert actual == expected, f"{tbl}: manifest={expected} artifact={actual}"
    finally:
        snap.close()


def test_manifest_ts_range_matches_the_artifact(tmp_path, monkeypatch):
    """ts_range describes the snapshot, even when the live DB gains later rows."""
    src = tmp_path / "source.db"
    conn = sqlite3.connect(str(src))
    conn.execute("CREATE TABLE option_quotes (id INTEGER PRIMARY KEY, ts TEXT)")
    for t in ("2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00"):
        conn.execute("INSERT INTO option_quotes (ts) VALUES (?)", (t,))
    conn.commit()
    conn.close()

    bdir = tmp_path / "backups"
    real_ts = backup._ts_range
    state = {"injected": False}

    def _inject_then_ts(c):
        if not state["injected"]:
            state["injected"] = True
            w = sqlite3.connect(str(src))
            w.execute("INSERT INTO option_quotes (ts) VALUES (?)",
                      ("2099-12-31T23:59:59+00:00",))
            w.commit()
            w.close()
        return real_ts(c)

    monkeypatch.setattr(backup, "_ts_range", _inject_then_ts)
    assert backup.run_backup(src, bdir, dry_run=False, db_label="test") == 0

    bfile = _latest_backup(bdir, "test")
    manifest = json.loads((bfile.parent / (bfile.stem + ".manifest.json")).read_text())

    snap = sqlite3.connect(str(bfile))
    lo, hi = snap.execute("SELECT min(ts), max(ts) FROM option_quotes").fetchone()
    snap.close()

    assert manifest["ts_range"] == {"min": lo, "max": hi}
    assert manifest["ts_range"]["max"] == "2026-01-02T00:00:00+00:00"
    assert "2099" not in str(manifest["ts_range"]), "live-DB row leaked into manifest"


def test_metadata_inspection_leaves_the_artifact_byte_identical(tmp_path):
    """mode=ro is load-bearing: _sha256 runs after the inspection, so the read
    must not touch the header or create -wal/-shm siblings."""
    src = _make_db(tmp_path / "source.db", {"option_quotes": 4})
    bdir = tmp_path / "backups"
    assert backup.run_backup(src, bdir, dry_run=False, db_label="test") == 0

    bfile = _latest_backup(bdir, "test")
    manifest = json.loads((bfile.parent / (bfile.stem + ".manifest.json")).read_text())

    assert _sha256_of(bfile) == manifest["sha256"]
    assert manifest["file_size_bytes"] == bfile.stat().st_size
    assert not (bfile.parent / (bfile.name + "-wal")).exists()
    assert not (bfile.parent / (bfile.name + "-shm")).exists()


def test_snapshot_open_failure_is_fail_closed(tmp_path, monkeypatch, capsys):
    """If the artifact cannot be opened for inspection, abort before hashing,
    encryption or upload -- never ship something we could not even read."""
    src = _make_db(tmp_path / "source.db", {"option_quotes": 3})
    bdir = tmp_path / "backups"

    real_connect = sqlite3.connect

    def _fail_on_readonly_open(target, *a, **kw):
        if isinstance(target, str) and "mode=ro" in target:
            raise sqlite3.OperationalError("simulated: unable to open snapshot")
        return real_connect(target, *a, **kw)

    monkeypatch.setattr(sqlite3, "connect", _fail_on_readonly_open)
    monkeypatch.setattr(backup, "_load_env_var",
                        lambda k: "age1xxx" if k == "BACKUP_AGE_RECIPIENT"
                        else "echo SHIPPED {file}")

    rc = backup.run_backup(src, bdir, dry_run=False, db_label="test", encrypt=True)
    out = capsys.readouterr()

    assert rc == 1
    assert "snapshot creation or inspection failed" in out.err
    assert "SHIPPED" not in out.out                 # upload never ran
    assert list(bdir.glob("*.age")) == []           # nothing encrypted
    assert list(bdir.glob("*.manifest.json")) == [] # no manifest written


# ---------------------------------------------------------------------------
# Backup-policy remediation (2026-08-21): duplicate prevention, orphan
# ciphertext retention, and failure isolation.
# ---------------------------------------------------------------------------

def _dated(bdir: Path, label: str, stamp: str, *, ciphertext=False,
           plaintext=True) -> None:
    """Materialise a backup artifact set for `{label}_{stamp}`."""
    bdir.mkdir(parents=True, exist_ok=True)
    if plaintext:
        (bdir / f"{label}_{stamp}.db").write_bytes(b"x" * 16)
        (bdir / f"{label}_{stamp}.manifest.json").write_text("{}", encoding="utf-8")
    if ciphertext:
        (bdir / f"{label}_{stamp}.db.age").write_bytes(b"y" * 16)
        (bdir / f"{label}_{stamp}.db.age-manifest.json").write_text("{}", encoding="utf-8")


class TestNoDuplicateFullTradingBackup:
    """The disk defect: two timers backed up the same 1.5 GB DB every night.

    btcbot-backup (--all, unencrypted, never shipped off-host) and
    btcbot-trading-backup (--encrypt, shipped) both wrote btc_trading into
    data/backups/. The BK3 timer was added without narrowing --all's scope.
    """

    def test_all_excluding_trading_makes_no_trading_backup(self, tmp_path, monkeypatch):
        _setup_root(tmp_path)
        _make_db(tmp_path / "data" / "btc_trading.db", _TRADING_TABLES)
        _make_db(tmp_path / "research" / "data" / "forward_evidence.db", _H6_TABLES)
        monkeypatch.setattr(backup, "ROOT", tmp_path)
        monkeypatch.setattr(sys, "argv",
                            ["backup.py", "--all", "--exclude", "btc_trading"])
        assert backup.main() == 0
        assert _latest_backup(tmp_path / "data" / "backups", "btc_trading") is None

    def test_the_other_databases_are_still_backed_up(self, tmp_path, monkeypatch):
        """Excluding one label must not silently disable the rest."""
        _setup_root(tmp_path)
        _make_db(tmp_path / "data" / "btc_trading.db", _TRADING_TABLES)
        _make_db(tmp_path / "research" / "data" / "forward_evidence.db", _H6_TABLES)
        _make_db(tmp_path / "research" / "data" / "evidence_forward.db",
                 _EVIDENCE_FWD_TABLES)
        monkeypatch.setattr(backup, "ROOT", tmp_path)
        monkeypatch.setattr(sys, "argv",
                            ["backup.py", "--all", "--exclude", "btc_trading"])
        assert backup.main() == 0
        assert _latest_backup(tmp_path / "data" / "evidence-backups", "evidence")
        assert _latest_backup(tmp_path / "data" / "evidence-fwd-backups", "fwd_hg")

    def test_ledger_can_also_be_excluded(self, tmp_path, monkeypatch):
        _setup_root(tmp_path)
        _make_db(tmp_path / "data" / "btc_trading.db", _TRADING_TABLES)
        _make_db(tmp_path / "research" / "evidence_library" / "ledger.db",
                 _LEDGER_TABLES)
        monkeypatch.setattr(backup, "ROOT", tmp_path)
        monkeypatch.setattr(sys, "argv", ["backup.py", "--all",
                                          "--exclude", "ledger",
                                          "--exclude", "btc_trading"])
        assert backup.main() == 0
        assert _latest_backup(tmp_path / "data" / "ledger-backups", "ledger") is None
        assert _latest_backup(tmp_path / "data" / "backups", "btc_trading") is None

    def test_without_exclusions_all_still_backs_up_trading(self, tmp_path, monkeypatch):
        """The default must be unchanged: --exclude is opt-in."""
        _setup_root(tmp_path)
        _make_db(tmp_path / "data" / "btc_trading.db", _TRADING_TABLES)
        monkeypatch.setattr(backup, "ROOT", tmp_path)
        monkeypatch.setattr(sys, "argv", ["backup.py", "--all"])
        assert backup.main() == 0
        assert _latest_backup(tmp_path / "data" / "backups", "btc_trading") is not None

    def test_unknown_label_fails_loudly(self, tmp_path, monkeypatch):
        """A typo in a systemd unit must fail, not silently back up the wrong set."""
        _setup_root(tmp_path)
        _make_db(tmp_path / "data" / "btc_trading.db", _TRADING_TABLES)
        monkeypatch.setattr(backup, "ROOT", tmp_path)
        monkeypatch.setattr(sys, "argv",
                            ["backup.py", "--all", "--exclude", "btc-trading"])
        assert backup.main() == 1
        assert _latest_backup(tmp_path / "data" / "backups", "btc_trading") is None

    def test_explicit_single_flag_ignores_exclusion(self, tmp_path, monkeypatch):
        """`--ledger --exclude ledger` is a contradiction; the explicit ask wins."""
        _setup_root(tmp_path)
        _make_db(tmp_path / "research" / "evidence_library" / "ledger.db",
                 _LEDGER_TABLES)
        monkeypatch.setattr(backup, "ROOT", tmp_path)
        monkeypatch.setattr(sys, "argv",
                            ["backup.py", "--ledger", "--exclude", "ledger"])
        assert backup.main() == 0
        assert _latest_backup(tmp_path / "data" / "ledger-backups", "ledger")

    def test_deployed_unit_carries_the_exclusions(self):
        """The fix is worthless if the unit does not pass them."""
        unit = pathlib.Path("deploy/btcbot-backup.service").read_text(encoding="utf-8")
        exec_line = next(l for l in unit.splitlines() if l.startswith("ExecStart="))
        assert "--all" in exec_line
        assert "--exclude btc_trading" in exec_line
        assert "--exclude ledger" in exec_line

    def test_the_two_encrypted_timers_still_own_those_labels(self):
        """Excluding them from --all is only safe because these still run."""
        trading = pathlib.Path(
            "deploy/btcbot-trading-backup.service").read_text(encoding="utf-8")
        ledger = pathlib.Path(
            "deploy/btcbot-ledger-backup.service").read_text(encoding="utf-8")
        assert "--encrypt" in trading
        assert "--exclude" not in trading
        assert "--ledger" in ledger and "--encrypt" in ledger


class TestOrphanCiphertextRetention:
    """A .age whose plaintext vanished used to be unreachable forever.

    _apply_retention could only delete a ciphertext as a side effect of
    deleting its plaintext sibling, so any plaintext removed by another path
    stranded its .age permanently -- 12 orphans (~14 GB) on production.
    """

    def test_orphan_ciphertext_outside_the_window_is_reclaimed(self, tmp_path):
        bdir = tmp_path / "backups"
        now = datetime(2026, 8, 21, tzinfo=timezone.utc)
        _dated(bdir, "btc_trading", "20260715_020000", ciphertext=True, plaintext=False)
        backup._apply_retention(bdir, now, dry_run=False, db_label="btc_trading")
        assert not (bdir / "btc_trading_20260715_020000.db.age").exists()
        assert not (bdir / "btc_trading_20260715_020000.db.age-manifest.json").exists()

    def test_recent_orphan_ciphertext_is_kept(self, tmp_path):
        """Age it out on the same schedule -- do not delete it for being orphaned."""
        bdir = tmp_path / "backups"
        now = datetime(2026, 8, 21, tzinfo=timezone.utc)
        _dated(bdir, "btc_trading", "20260820_020000", ciphertext=True, plaintext=False)
        backup._apply_retention(bdir, now, dry_run=False, db_label="btc_trading")
        assert (bdir / "btc_trading_20260820_020000.db.age").exists()

    def test_paired_ciphertext_is_still_handled_by_the_plaintext_pass(self, tmp_path):
        bdir = tmp_path / "backups"
        now = datetime(2026, 8, 21, tzinfo=timezone.utc)
        _dated(bdir, "btc_trading", "20260715_020000", ciphertext=True, plaintext=True)
        backup._apply_retention(bdir, now, dry_run=False, db_label="btc_trading")
        for suffix in (".db", ".manifest.json", ".db.age", ".db.age-manifest.json"):
            assert not (bdir / f"btc_trading_20260715_020000{suffix}").exists()

    def test_orphan_sweep_does_not_touch_a_live_pair(self, tmp_path):
        bdir = tmp_path / "backups"
        now = datetime(2026, 8, 21, tzinfo=timezone.utc)
        _dated(bdir, "btc_trading", "20260820_020000", ciphertext=True, plaintext=True)
        backup._apply_retention(bdir, now, dry_run=False, db_label="btc_trading")
        assert (bdir / "btc_trading_20260820_020000.db").exists()
        assert (bdir / "btc_trading_20260820_020000.db.age").exists()

    def test_live_pairs_are_not_reported_as_orphans(self, tmp_path, capsys):
        """The counter is what an operator reads to judge orphan accumulation.

        Deletion is already safe without the plaintext-exists guard -- pass 1
        removes expired pairs, so whatever survives into pass 2 is inside the
        window anyway. What the guard actually protects is the reported number:
        without it, every live pair is counted as a kept orphan and the line
        stops meaning anything.
        """
        bdir = tmp_path / "backups"
        now = datetime(2026, 8, 21, tzinfo=timezone.utc)
        _dated(bdir, "btc_trading", "20260820_020000", ciphertext=True, plaintext=True)
        _dated(bdir, "btc_trading", "20260819_020000", ciphertext=True, plaintext=True)
        backup._apply_retention(bdir, now, dry_run=False, db_label="btc_trading")
        out = capsys.readouterr().out
        assert "orphan_ciphertext_kept=0" in out, out
        assert "orphan_ciphertext_deleted=0" in out, out

    def test_orphan_sweep_is_label_scoped(self, tmp_path):
        """One label's retention must never reach into another's artifacts."""
        bdir = tmp_path / "backups"
        now = datetime(2026, 8, 21, tzinfo=timezone.utc)
        _dated(bdir, "ledger", "20260715_020000", ciphertext=True, plaintext=False)
        backup._apply_retention(bdir, now, dry_run=False, db_label="btc_trading")
        assert (bdir / "ledger_20260715_020000.db.age").exists()

    def test_dry_run_deletes_no_orphan(self, tmp_path):
        bdir = tmp_path / "backups"
        now = datetime(2026, 8, 21, tzinfo=timezone.utc)
        _dated(bdir, "btc_trading", "20260715_020000", ciphertext=True, plaintext=False)
        backup._apply_retention(bdir, now, dry_run=True, db_label="btc_trading")
        assert (bdir / "btc_trading_20260715_020000.db.age").exists()

    def test_undated_orphan_is_never_deleted(self, tmp_path):
        """Unparseable name => keep. Never guess at an artifact's age."""
        bdir = tmp_path / "backups"
        bdir.mkdir(parents=True, exist_ok=True)
        (bdir / "btc_trading_manual.db.age").write_bytes(b"z")
        backup._apply_retention(bdir, datetime(2026, 8, 21, tzinfo=timezone.utc),
                                dry_run=False, db_label="btc_trading")
        assert (bdir / "btc_trading_manual.db.age").exists()

    def test_the_production_orphan_set_would_be_reclaimed(self, tmp_path):
        """The 12 real stranded ciphertexts, by name, at a later clock."""
        bdir = tmp_path / "backups"
        stamps = ["20260817_085042", "20260817_093115", "20260818_024325",
                  "20260818_060822", "20260818_162704", "20260819_024040",
                  "20260819_035039", "20260819_062304", "20260819_095157",
                  "20260820_024340", "20260820_053754", "20260821_024440"]
        for s in stamps:
            _dated(bdir, "btc_trading", s, ciphertext=True, plaintext=False)
        backup._apply_retention(bdir, datetime(2026, 9, 30, tzinfo=timezone.utc),
                                dry_run=False, db_label="btc_trading")
        assert list(bdir.glob("*.age")) == []


class TestRetentionWindowUnchanged:
    """The rotation itself is NOT being redesigned in this change."""

    def test_constants_are_untouched(self):
        assert backup.KEEP_DAILY_DAYS == 7
        assert backup.KEEP_WEEKLY_DAYS == 28
        assert backup.KEEP_MONTHLY_DAYS == 365

    def test_daily_weekly_monthly_rule_is_preserved(self):
        now = datetime(2026, 8, 21, tzinfo=timezone.utc)
        assert backup._within_retention(datetime(2026, 8, 20, tzinfo=timezone.utc), now)
        sunday = datetime(2026, 8, 2, tzinfo=timezone.utc)
        assert sunday.weekday() == 6
        assert backup._within_retention(sunday, now)
        assert backup._within_retention(datetime(2026, 3, 1, tzinfo=timezone.utc), now)
        assert not backup._within_retention(
            datetime(2026, 3, 17, tzinfo=timezone.utc), now)

    def test_backup_date_reads_the_name_not_the_mtime(self, tmp_path):
        p = tmp_path / "btc_trading_20260415_101112.db"
        p.write_bytes(b"x")
        assert backup._backup_date(p, "btc_trading") == datetime(
            2026, 4, 15, tzinfo=timezone.utc)

    def test_backup_date_handles_the_ciphertext_suffix(self, tmp_path):
        p = tmp_path / "btc_trading_20260415_101112.db.age"
        p.write_bytes(b"x")
        assert backup._backup_date(p, "btc_trading") == datetime(
            2026, 4, 15, tzinfo=timezone.utc)


class TestBackupCannotDisturbTheCollector:
    """The collector is the irreplaceable asset; backups must stay out of its way."""

    def test_backup_script_imports_no_runtime_module(self):
        src = pathlib.Path("scripts/backup.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    assert not a.name.startswith("src."), a.name
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("src."), node.module

    def test_backup_never_changes_the_source_data(self, tmp_path):
        """Backup may re-journal the source, but must not touch a single row.

        run_backup sets PRAGMA journal_mode=WAL on the source (long-standing,
        see CLAUDE.md TD-P1-002), so the file header legitimately changes. What
        must never change is the data the collector owns.
        """
        src = _make_db(tmp_path / "source.db", _TRADING_TABLES)
        conn = sqlite3.connect(str(src))
        before = conn.execute("SELECT COUNT(*) FROM option_quotes").fetchone()[0]
        before_rows = conn.execute(
            "SELECT * FROM option_quotes ORDER BY id").fetchall()
        conn.close()

        backup.run_backup(src, tmp_path / "backups", dry_run=False,
                          db_label="btc_trading")

        conn = sqlite3.connect(str(src))
        assert conn.execute("SELECT COUNT(*) FROM option_quotes").fetchone()[0] == before
        assert conn.execute(
            "SELECT * FROM option_quotes ORDER BY id").fetchall() == before_rows
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        conn.close()

    def test_retention_dates_used_by_these_tests_are_outside_every_tier(self):
        """Guards the tests above: a 1st-of-month or Sunday date is KEPT."""
        now = datetime(2026, 8, 21, tzinfo=timezone.utc)
        chosen = datetime(2026, 7, 15, tzinfo=timezone.utc)
        assert chosen.day != 1 and chosen.weekday() != 6
        assert not backup._within_retention(chosen, now)

    def test_units_are_oneshot_and_independent_of_the_collector(self):
        """No ordering or dependency DIRECTIVE may bind a backup to the collector.

        Checks directives, not prose: these units legitimately mention
        btcbot.service in comments to record that it keeps running throughout.
        """
        for name in ("btcbot-backup.service", "btcbot-trading-backup.service",
                     "btcbot-ledger-backup.service"):
            text = (pathlib.Path("deploy") / name).read_text(encoding="utf-8")
            directives = [l.strip() for l in text.splitlines()
                          if l.strip() and not l.strip().startswith("#")]
            assert "Type=oneshot" in directives
            for d in directives:
                key = d.split("=", 1)[0]
                if key in ("After", "Before", "Requires", "Requisite", "Wants",
                           "BindsTo", "PartOf", "Conflicts"):
                    assert "btcbot.service" not in d, f"{name}: {d}"
