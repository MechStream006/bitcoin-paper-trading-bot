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

import importlib.util
import json
import pathlib
import sqlite3
import sys
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
