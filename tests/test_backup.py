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
    (tmp / "research" / "data").mkdir(parents=True, exist_ok=True)
    (tmp / "research" / "evidence_library").mkdir(parents=True, exist_ok=True)


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


def test_run_backup_missing_db_returns_1(tmp_path):
    missing = tmp_path / "does_not_exist.db"
    bdir = tmp_path / "backups"
    rc = backup.run_backup(missing, bdir, dry_run=False, db_label="test")
    assert rc == 1
    assert list(bdir.glob("test_*.db")) == []


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
# Extended --all flag tests
# ---------------------------------------------------------------------------

def test_all_flag_covers_all_four_dbs(tmp_path, monkeypatch):
    _setup_root(tmp_path)
    _make_db(tmp_path / "data" / "btc_trading.db", _TRADING_TABLES)
    _make_db(tmp_path / "research" / "data" / "forward_evidence.db", _H6_TABLES)
    _make_db(tmp_path / "research" / "data" / "evidence_forward.db", _EVIDENCE_FWD_TABLES)
    _make_db(tmp_path / "research" / "evidence_library" / "ledger.db", _LEDGER_TABLES)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--all"])
    rc = backup.main()
    assert rc == 0
    assert _latest_backup(tmp_path / "data" / "backups", "btc_trading") is not None
    assert _latest_backup(tmp_path / "data" / "evidence-backups", "evidence") is not None
    assert _latest_backup(tmp_path / "data" / "evidence-fwd-backups", "fwd_hg") is not None
    assert _latest_backup(tmp_path / "data" / "ledger-backups", "ledger") is not None


def test_all_flag_return_code_is_max_of_four(tmp_path, monkeypatch):
    """--all exit code is the max of all four individual backup return codes."""
    _setup_root(tmp_path)
    # Provide only 3 of the 4 DBs; the missing one causes rc=1 for that backup.
    _make_db(tmp_path / "data" / "btc_trading.db", _TRADING_TABLES)
    _make_db(tmp_path / "research" / "data" / "forward_evidence.db", _H6_TABLES)
    _make_db(tmp_path / "research" / "evidence_library" / "ledger.db", _LEDGER_TABLES)
    # evidence_forward.db intentionally missing → rc_fwd_hg = 1
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--all"])
    rc = backup.main()
    assert rc == 1  # max(0, 0, 1, 0)


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


def test_all_dry_run_prints_four_lines(tmp_path, monkeypatch, capsys):
    _setup_root(tmp_path)
    # DBs don't need to exist for dry-run (run_backup checks existence first,
    # but dry-run branch actually IS guarded — missing DB returns 1 even in dry-run).
    # Create all four so we get clean [DRY RUN] output for all four.
    _make_db(tmp_path / "data" / "btc_trading.db")
    _make_db(tmp_path / "research" / "data" / "forward_evidence.db")
    _make_db(tmp_path / "research" / "data" / "evidence_forward.db")
    _make_db(tmp_path / "research" / "evidence_library" / "ledger.db")
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--all", "--dry-run"])
    rc = backup.main()
    assert rc == 0
    out = capsys.readouterr().out
    dry_run_lines = [ln for ln in out.splitlines() if "[DRY RUN] would create" in ln]
    assert len(dry_run_lines) == 4


def test_all_with_specific_flag_still_backs_up_trading(tmp_path, monkeypatch):
    """--all combined with a specific flag must still produce all four backups.

    Regression for the L285 edge case: before the fix, `--all --evidence`
    evaluated `if not (True or False or False)` → False and silently skipped
    btc_trading.db. After the fix, `args.backup_all` short-circuits the guard.
    """
    _setup_root(tmp_path)
    _make_db(tmp_path / "data" / "btc_trading.db", _TRADING_TABLES)
    _make_db(tmp_path / "research" / "data" / "forward_evidence.db", _H6_TABLES)
    _make_db(tmp_path / "research" / "data" / "evidence_forward.db", _EVIDENCE_FWD_TABLES)
    _make_db(tmp_path / "research" / "evidence_library" / "ledger.db", _LEDGER_TABLES)
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["backup.py", "--all", "--evidence"])
    rc = backup.main()
    assert rc == 0
    # All four must be present — trading must not be skipped.
    assert _latest_backup(tmp_path / "data" / "backups", "btc_trading") is not None
    assert _latest_backup(tmp_path / "data" / "evidence-backups", "evidence") is not None
    assert _latest_backup(tmp_path / "data" / "evidence-fwd-backups", "fwd_hg") is not None
    assert _latest_backup(tmp_path / "data" / "ledger-backups", "ledger") is not None


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
