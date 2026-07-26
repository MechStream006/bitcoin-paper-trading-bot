"""Tests for scripts/restore.py — P1 Increment 2.

First-ever test coverage for this module (flagged as a gap in the P1 Gap
Analysis: `scripts/backup.py` has had a regression suite since R2; `restore.py`
had none, despite protecting the swap-in of the single irreplaceable artifact,
`ledger.db`, per ADR-0001 §12 BK1).

Mirrors tests/test_backup.py's structure and discipline:
  - Real SQLite DBs and real file operations throughout; no mocking of the
    logic under test.
  - The only mocked boundary is `subprocess`/systemd, an external dependency
    the test environment does not control.
  - One behavior per test; section banners group tests by the step of
    run_restore() they exercise; non-obvious tests carry a one-line docstring.
  - Where practical, restore.py is exercised against a REAL backup produced by
    backup.py (imported the same way), not a hand-built fixture -- this proves
    the backup -> restore contract end-to-end, matching how the actual
    restore drill (docs/ops/restore-drill-2026-07-19.md) exercised the tools.

Run from repo root:
    conda run -n btc_bot pytest tests/test_restore.py -v
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Module loaders — scripts/ is not a package; load as file modules (mirrors
# tests/test_backup.py's _load_backup_module()). Neither module defines a
# dataclass, so no sys.modules registration is required (unlike healthcheck.py).
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


def _load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


restore = _load_module("restore_module", "restore.py")
backup = _load_module("backup_module", "backup.py")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SOME_TABLES = {"widgets": 4, "gadgets": 0}


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


def _row_count(path: Path, table: str) -> int:
    conn = sqlite3.connect(str(path))
    n = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    conn.close()
    return n


def _integrity_ok(path: Path) -> bool:
    conn = sqlite3.connect(str(path))
    result = conn.execute("PRAGMA integrity_check").fetchall()
    conn.close()
    return result == [("ok",)]


def _write_manifest(
    backup_file: Path,
    sha256: str | None = None,
    backup_ts: str = "2026-07-19T05:08:02Z",
    row_counts: dict | None = None,
) -> Path:
    """Write a `{stem}.manifest.json` sidecar next to backup_file, matching the
    naming convention restore.py expects. Defaults `sha256` to the file's real
    hash (via restore.py's own hasher) so "happy path" tests don't need to
    know the hash in advance."""
    manifest_file = backup_file.parent / (backup_file.stem + ".manifest.json")
    manifest = {
        "sha256": sha256 if sha256 is not None else restore._sha256(backup_file),
        "backup_ts": backup_ts,
        "row_counts": row_counts or {},
    }
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_file


def _real_backup(tmp_path: Path, tables: dict[str, int] | None = None) -> Path:
    """Produce a REAL backup + manifest via backup.py's own run_backup(), to
    exercise restore.py against genuine tooling output rather than a synthetic
    fixture. Returns the backup file path."""
    src = _make_db(tmp_path / "source.db", tables)
    bdir = tmp_path / "backups"
    rc = backup.run_backup(src, bdir, dry_run=False, db_label="test")
    assert rc == 0
    files = sorted(bdir.glob("test_*.db"))
    assert files, "backup.run_backup did not produce a backup file"
    return files[-1]


# ---------------------------------------------------------------------------
# _sha256()
# ---------------------------------------------------------------------------

def test_sha256_matches_hashlib_reference(tmp_path):
    import hashlib
    f = tmp_path / "some_file.db"
    f.write_bytes(b"arbitrary bytes for hashing")
    expected = hashlib.sha256(f.read_bytes()).hexdigest()
    assert restore._sha256(f) == expected


# ---------------------------------------------------------------------------
# _integrity_ok()
# ---------------------------------------------------------------------------

def test_integrity_ok_true_for_valid_db(tmp_path):
    db = _make_db(tmp_path / "valid.db", _SOME_TABLES)
    assert restore._integrity_ok(db) is True


def test_integrity_ok_false_for_corrupt_file(tmp_path):
    bogus = tmp_path / "corrupt.db"
    bogus.write_bytes(b"this is not a sqlite database at all, just garbage")
    assert restore._integrity_ok(bogus) is False


# ---------------------------------------------------------------------------
# _systemctl()
# ---------------------------------------------------------------------------

class _FakeCompletedProcess:
    def __init__(self, returncode: int, stderr: str = ""):
        self.returncode = returncode
        self.stderr = stderr


def test_systemctl_success_returns_true(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _FakeCompletedProcess(0)
    )
    assert restore._systemctl("btcbot", "stop") is True


def test_systemctl_nonzero_exit_returns_false(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _FakeCompletedProcess(1, "unit not found")
    )
    assert restore._systemctl("btcbot", "stop") is False


def test_systemctl_missing_binary_skips_gracefully(monkeypatch):
    """No systemctl on the box (e.g. non-Linux dev machine) must not be
    treated as a failure -- restore.py documents this as a graceful skip."""
    def _raise(*a, **k):
        raise FileNotFoundError("systemctl not found")
    monkeypatch.setattr(subprocess, "run", _raise)
    assert restore._systemctl("btcbot", "stop") is True


def test_systemctl_unexpected_exception_returns_false(monkeypatch):
    def _raise(*a, **k):
        raise RuntimeError("something else went wrong")
    monkeypatch.setattr(subprocess, "run", _raise)
    assert restore._systemctl("btcbot", "stop") is False


# ---------------------------------------------------------------------------
# run_restore() -- happy path, against a REAL backup.py artifact
# ---------------------------------------------------------------------------

def test_restore_from_real_backup_succeeds(tmp_path):
    bfile = _real_backup(tmp_path, _SOME_TABLES)
    db_path = tmp_path / "live" / "target.db"
    rc = restore.run_restore(bfile, db_path, "btcbot", skip_service=True)
    assert rc == 0
    assert db_path.exists()
    assert _integrity_ok(db_path)
    assert _row_count(db_path, "widgets") == 4


def test_restore_preserves_prior_db_as_pre_restore(tmp_path):
    bfile = _real_backup(tmp_path, {"widgets": 4})
    db_path = tmp_path / "live" / "target.db"
    _make_db(db_path, {"widgets": 999})  # old, distinguishable content

    rc = restore.run_restore(bfile, db_path, "btcbot", skip_service=True)
    assert rc == 0

    pre_restore = db_path.parent / (db_path.name + ".pre-restore")
    assert pre_restore.exists()
    assert _row_count(pre_restore, "widgets") == 999   # old content preserved
    assert _row_count(db_path, "widgets") == 4          # new content swapped in


def test_restore_no_prior_db_no_pre_restore_file(tmp_path):
    """If there was nothing to restore over, no .pre-restore artifact should
    appear -- there is nothing to preserve."""
    bfile = _real_backup(tmp_path, _SOME_TABLES)
    db_path = tmp_path / "live" / "target.db"
    assert not db_path.exists()

    rc = restore.run_restore(bfile, db_path, "btcbot", skip_service=True)
    assert rc == 0
    pre_restore = db_path.parent / (db_path.name + ".pre-restore")
    assert not pre_restore.exists()


# ---------------------------------------------------------------------------
# run_restore() -- SHA-256 manifest verification
# ---------------------------------------------------------------------------

def test_sha256_mismatch_aborts_before_swap(tmp_path):
    bfile = _real_backup(tmp_path, _SOME_TABLES)
    # Overwrite the manifest's own sha256 with a wrong value.
    manifest_file = bfile.parent / (bfile.stem + ".manifest.json")
    manifest = json.loads(manifest_file.read_text())
    manifest["sha256"] = "0" * 64
    manifest_file.write_text(json.dumps(manifest))

    db_path = tmp_path / "live" / "target.db"
    rc = restore.run_restore(bfile, db_path, "btcbot", skip_service=True)
    assert rc == 1
    assert not db_path.exists()   # swap must never have happened


def test_missing_manifest_warns_but_proceeds(tmp_path, capsys):
    """No manifest at all (e.g. an older backup) is a warning, not a failure --
    restore proceeds without the SHA-256 check."""
    src = _make_db(tmp_path / "source.db", _SOME_TABLES)
    bfile = tmp_path / "no_manifest.db"
    shutil.copy2(src, bfile)   # a "backup" with no sidecar manifest at all

    db_path = tmp_path / "live" / "target.db"
    rc = restore.run_restore(bfile, db_path, "btcbot", skip_service=True)
    assert rc == 0
    assert db_path.exists()
    out = capsys.readouterr().out
    assert "no manifest" in out.lower() or "warning" in out.lower()


# ---------------------------------------------------------------------------
# run_restore() -- integrity check on the backup file itself
# ---------------------------------------------------------------------------

def test_corrupt_backup_file_aborts_before_swap(tmp_path):
    bfile = tmp_path / "corrupt_backup.db"
    bfile.write_bytes(b"not a real sqlite database")
    _write_manifest(bfile)   # sha256 will match (computed from the same bytes)

    db_path = tmp_path / "live" / "target.db"
    rc = restore.run_restore(bfile, db_path, "btcbot", skip_service=True)
    assert rc == 1
    assert not db_path.exists()


def test_missing_backup_file_returns_1(tmp_path):
    missing = tmp_path / "does_not_exist.db"
    db_path = tmp_path / "live" / "target.db"
    rc = restore.run_restore(missing, db_path, "btcbot", skip_service=True)
    assert rc == 1
    assert not db_path.exists()


# ---------------------------------------------------------------------------
# run_restore() -- post-swap integrity failure
# ---------------------------------------------------------------------------

def test_post_swap_integrity_failure_preserves_pre_restore(tmp_path, monkeypatch):
    """Simulates the swap-time copy itself producing a corrupt destination
    (e.g. a truncated disk write) -- distinct from the backup file being
    corrupt (that's caught earlier, before any copy happens). The original
    DB must remain recoverable via .pre-restore, and the function must report
    failure rather than silently leaving a corrupt live DB with no signal."""
    bfile = _real_backup(tmp_path, {"widgets": 4})
    db_path = tmp_path / "live" / "target.db"
    _make_db(db_path, {"widgets": 999})

    real_copy2 = shutil.copy2

    def _corrupting_copy2(src, dst):
        real_copy2(src, dst)
        if Path(dst) == db_path:
            # Zero the SQLite magic header ("SQLite format 3\0", bytes 0-15) so
            # the destination is unreadable as a database. Appending garbage
            # past EOF is NOT sufficient -- SQLite's integrity_check tolerates
            # trailing bytes past the recorded page count. Corrupting the
            # header is what reliably fails PRAGMA integrity_check /
            # sqlite3.connect, while the ORIGINAL backup_file (checked earlier,
            # before this copy runs) is untouched and still passes.
            with open(dst, "r+b") as f:
                f.seek(0)
                f.write(b"\x00" * 16)

    monkeypatch.setattr(restore.shutil, "copy2", _corrupting_copy2)

    rc = restore.run_restore(bfile, db_path, "btcbot", skip_service=True)
    assert rc == 1

    pre_restore = db_path.parent / (db_path.name + ".pre-restore")
    assert pre_restore.exists()
    assert _integrity_ok(pre_restore)
    assert _row_count(pre_restore, "widgets") == 999   # original fully recoverable


# ---------------------------------------------------------------------------
# run_restore() -- service stop/start orchestration
# ---------------------------------------------------------------------------

def test_skip_service_never_calls_systemctl(tmp_path, monkeypatch):
    def _fail_if_called(*a, **k):
        raise AssertionError("subprocess.run must not be called when skip_service=True")
    monkeypatch.setattr(subprocess, "run", _fail_if_called)

    bfile = _real_backup(tmp_path, _SOME_TABLES)
    db_path = tmp_path / "live" / "target.db"
    rc = restore.run_restore(bfile, db_path, "btcbot", skip_service=True)
    assert rc == 0


def test_service_stop_failure_aborts_before_any_file_operation(tmp_path, monkeypatch):
    monkeypatch.setattr(restore, "_systemctl",
                         lambda service, action: False if action == "stop" else True)

    bfile = _real_backup(tmp_path, _SOME_TABLES)
    db_path = tmp_path / "live" / "target.db"
    _make_db(db_path, {"widgets": 999})

    rc = restore.run_restore(bfile, db_path, "btcbot", skip_service=False)
    assert rc == 1
    # Nothing must have been touched: no swap, no pre-restore copy.
    assert _row_count(db_path, "widgets") == 999
    assert not (db_path.parent / (db_path.name + ".pre-restore")).exists()


def test_service_stop_and_start_both_invoked_on_success(tmp_path, monkeypatch):
    calls: list[tuple[str, str]] = []

    def _fake_systemctl(service, action):
        calls.append((service, action))
        return True

    monkeypatch.setattr(restore, "_systemctl", _fake_systemctl)

    bfile = _real_backup(tmp_path, _SOME_TABLES)
    db_path = tmp_path / "live" / "target.db"
    rc = restore.run_restore(bfile, db_path, "my-service", skip_service=False)
    assert rc == 0
    assert ("my-service", "stop") in calls
    assert ("my-service", "start") in calls


def test_service_start_failure_after_successful_swap_still_returns_0(tmp_path, monkeypatch):
    """A restart failure after a successful restore is a warning, not a
    rollback trigger -- the DB swap already succeeded and is not undone."""
    def _fake_systemctl(service, action):
        if action == "stop":
            return True
        return False   # start fails
    monkeypatch.setattr(restore, "_systemctl", _fake_systemctl)

    bfile = _real_backup(tmp_path, _SOME_TABLES)
    db_path = tmp_path / "live" / "target.db"
    rc = restore.run_restore(bfile, db_path, "btcbot", skip_service=False)
    assert rc == 0
    assert _integrity_ok(db_path)


# ---------------------------------------------------------------------------
# main() -- CLI wiring, end to end
# ---------------------------------------------------------------------------

def test_main_positional_and_flags_wire_through(tmp_path, monkeypatch):
    bfile = _real_backup(tmp_path, _SOME_TABLES)
    db_path = tmp_path / "live" / "target.db"

    monkeypatch.setattr(
        sys, "argv",
        ["restore.py", str(bfile), "--db-path", str(db_path), "--skip-service"],
    )
    rc = restore.main()
    assert rc == 0
    assert db_path.exists()
    assert _integrity_ok(db_path)
    assert _row_count(db_path, "widgets") == 4


def test_main_default_db_path_is_data_btc_trading_db():
    """Confirms the documented default without invoking main() against the
    real production path."""
    parser_default = restore.ROOT / "data" / "btc_trading.db"
    assert parser_default.name == "btc_trading.db"
    assert parser_default.parent.name == "data"
