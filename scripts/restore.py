"""BTC Paper Trading Bot — SQLite restore script.

Verifies a backup file against its manifest (SHA-256 + integrity check),
then swaps it in as the live database. Optionally stops/starts the systemd
service around the swap.

Restore sequence:
  1. Verify SHA-256 against manifest (if manifest exists)
  2. PRAGMA integrity_check on the backup file
  3. Stop systemd service (unless --skip-service)
  4. Preserve current DB as <db>.pre-restore
  5. Copy backup to live DB path
  6. PRAGMA integrity_check on the newly swapped-in DB
  7. Start systemd service (unless --skip-service)

Usage:
    python scripts/restore.py <backup_file> [options]

Exit codes:
    0  success (DB was swapped)
    1  failure (DB was NOT swapped — original is intact)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _integrity_ok(db_path: Path) -> bool:
    try:
        conn = sqlite3.connect(str(db_path))
        result = conn.execute("PRAGMA integrity_check").fetchall()
        conn.close()
        return result == [("ok",)]
    except Exception as exc:
        print(f"[error] integrity_check raised: {exc}", file=sys.stderr)
        return False


def _systemctl(service: str, action: str) -> bool:
    """Run systemctl <action> <service>. Returns True on success or if systemctl absent."""
    try:
        r = subprocess.run(
            ["systemctl", action, service],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            print(
                f"[error] systemctl {action} {service} failed: {r.stderr.strip()}",
                file=sys.stderr,
            )
            return False
        return True
    except FileNotFoundError:
        print(f"[warning] systemctl not found — skipping '{action}'", file=sys.stderr)
        return True
    except Exception as exc:
        print(f"[error] systemctl {action}: {exc}", file=sys.stderr)
        return False


def run_restore(
    backup_file: Path,
    db_path: Path,
    service_name: str,
    skip_service: bool,
) -> int:
    if not backup_file.exists():
        print(f"[error] backup file not found: {backup_file}", file=sys.stderr)
        return 1

    # --- Step 1: SHA-256 verification ---
    manifest_file = backup_file.parent / (backup_file.stem + ".manifest.json")
    if manifest_file.exists():
        print("[restore] verifying SHA-256 against manifest...")
        manifest      = json.loads(manifest_file.read_text(encoding="utf-8"))
        expected_sha  = manifest.get("sha256", "")
        actual_sha    = _sha256(backup_file)
        if expected_sha and actual_sha != expected_sha:
            print(
                f"[error] SHA-256 mismatch\n"
                f"  expected : {expected_sha}\n"
                f"  actual   : {actual_sha}",
                file=sys.stderr,
            )
            return 1
        print(f"[restore] SHA-256 OK ({actual_sha[:16]}...)")
        print(f"[restore] backup_ts  : {manifest.get('backup_ts')}")
        print(f"[restore] row_counts : {manifest.get('row_counts')}")
    else:
        print(f"[warning] no manifest at {manifest_file.name} — skipping SHA-256 check")

    # --- Step 2: Integrity check on backup ---
    print("[restore] integrity_check on backup file...")
    if not _integrity_ok(backup_file):
        print("[error] backup file failed integrity_check — restore aborted", file=sys.stderr)
        return 1
    print("[restore] integrity_check OK")

    # --- Step 3: Stop service ---
    if not skip_service:
        print(f"[restore] stopping {service_name}...")
        if not _systemctl(service_name, "stop"):
            print("[error] could not stop service — restore aborted", file=sys.stderr)
            return 1
        print("[restore] service stopped")
    else:
        print("[restore] skipping service stop (--skip-service)")

    # --- Step 4: Preserve current DB ---
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        pre_restore = db_path.parent / (db_path.name + ".pre-restore")
        print(f"[restore] preserving current DB -> {pre_restore.name}")
        shutil.copy2(str(db_path), str(pre_restore))

    # --- Step 5: Swap ---
    print(f"[restore] copying {backup_file.name} -> {db_path}")
    shutil.copy2(str(backup_file), str(db_path))

    # --- Step 6: Post-swap integrity check ---
    print("[restore] post-swap integrity_check...")
    if not _integrity_ok(db_path):
        print(
            f"[error] post-swap integrity_check FAILED — {db_path} may be corrupt.\n"
            f"        Original preserved as {db_path.name}.pre-restore",
            file=sys.stderr,
        )
        if not skip_service:
            _systemctl(service_name, "start")
        return 1
    print("[restore] post-swap integrity_check OK")

    # --- Step 7: Start service ---
    if not skip_service:
        print(f"[restore] starting {service_name}...")
        if not _systemctl(service_name, "start"):
            print(
                f"[warning] service did not start — check: systemctl status {service_name}"
            )
        else:
            print("[restore] service started")

    print(f"\n[restore] done - {db_path} restored from {backup_file.name}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="BTC bot SQLite restore")
    parser.add_argument("backup_file", type=Path, help="Path to the .db backup file")
    parser.add_argument(
        "--db-path", type=Path, default=ROOT / "data" / "btc_trading.db",
        help="Live database to replace (default: data/btc_trading.db)",
    )
    parser.add_argument(
        "--service-name", default="btcbot",
        help="systemd service to stop/start (default: btcbot)",
    )
    parser.add_argument(
        "--skip-service", action="store_true",
        help="Skip systemd stop/start (use when service is not installed)",
    )
    args = parser.parse_args()
    return run_restore(args.backup_file, args.db_path, args.service_name, args.skip_service)


if __name__ == "__main__":
    sys.exit(main())
