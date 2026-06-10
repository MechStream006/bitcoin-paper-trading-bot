"""BTC Paper Trading Bot — SQLite backup script.

Creates a consistent hot backup using VACUUM INTO (SQLite 3.27+), writes a
SHA-256 manifest, applies 7-daily/4-weekly/12-monthly rotation, then
optionally runs a configurable off-host transfer command.

Backs up two databases:
  1. data/btc_trading.db         (Layer 2 live trading runtime)
  2. research/data/forward_evidence.db  (Phase E forward evidence; --evidence)

Usage:
    python scripts/backup.py [--db-path PATH] [--backup-dir PATH] [--dry-run]
    python scripts/backup.py --evidence [--dry-run]
    python scripts/backup.py --all [--dry-run]   # backs up both databases

Environment variable:
    BACKUP_OFFHOST_CMD  Optional shell template with {file} and {manifest}
                        placeholders. Empty/unset = local-only backup.
                        Example: "rsync -az {file} {manifest} user@host:/backups/"

Exit codes:
    0  success
    1  one or more backups failed
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

KEEP_DAILY_DAYS   = 7    # all backups within the last 7 days
KEEP_WEEKLY_DAYS  = 28   # Sunday backups within the last 28 days (4 weeks)
KEEP_MONTHLY_DAYS = 365  # 1st-of-month backups within the last 365 days (12 months)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    return {
        t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        for (t,) in tables
    }


def _ts_range(conn: sqlite3.Connection) -> dict[str, str | None]:
    # Try Layer 2 DB table first, then evidence DB table, then give up gracefully.
    for sql in (
        "SELECT min(ts), max(ts) FROM option_quotes",
        "SELECT min(day_ts), max(day_ts) FROM corpus_daily",
    ):
        try:
            row = conn.execute(sql).fetchone()
            return {"min": row[0], "max": row[1]}
        except Exception:
            continue
    return {"min": None, "max": None}


def _git_sha() -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT, capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() if r.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def _load_env_var(key: str) -> str:
    """Read a single env var from environment or .env file (plain text fallback)."""
    val = os.environ.get(key, "")
    if val:
        return val
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(key + "="):
                return line[len(key) + 1:].strip()
    return ""


def _apply_retention(backup_dir: Path, now: datetime, dry_run: bool,
                     db_label: str = "btc_trading") -> None:
    """Delete backup files outside the retention window."""
    backups = sorted(backup_dir.glob(f"{db_label}_*.db"))
    kept, deleted = [], []

    for f in backups:
        # Filename: {db_label}_YYYYMMDD_HHMMSS.db — find the 8-digit date part.
        try:
            parts = f.stem.split("_")
            date_str = next(p for p in parts if p.isdigit() and len(p) == 8)
            bdate = datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=timezone.utc)
        except (ValueError, StopIteration):
            kept.append(f)
            continue

        age_days     = (now - bdate).days
        in_daily     = age_days <= KEEP_DAILY_DAYS
        in_weekly    = bdate.weekday() == 6 and age_days <= KEEP_WEEKLY_DAYS
        in_monthly   = bdate.day == 1 and age_days <= KEEP_MONTHLY_DAYS

        if in_daily or in_weekly or in_monthly:
            kept.append(f)
        else:
            deleted.append(f)

    for f in deleted:
        manifest = f.parent / (f.stem + ".manifest.json")
        if dry_run:
            print(f"[DRY RUN] would delete {f.name}")
        else:
            f.unlink(missing_ok=True)
            manifest.unlink(missing_ok=True)
            print(f"[retention] deleted {f.name}")

    print(f"[retention] kept={len(kept)}  deleted={len(deleted)}")


def run_backup(db_path: Path, backup_dir: Path, dry_run: bool,
               db_label: str = "btc_trading") -> int:
    if not db_path.exists():
        print(f"[error] DB not found: {db_path}", file=sys.stderr)
        return 1

    # VACUUM INTO requires SQLite >= 3.27.0
    sqlite_ver = tuple(int(x) for x in sqlite3.sqlite_version.split("."))
    if sqlite_ver < (3, 27, 0):
        print(
            f"[error] VACUUM INTO requires SQLite >= 3.27.0, found {sqlite3.sqlite_version}",
            file=sys.stderr,
        )
        return 1

    backup_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    ts_str = now.strftime("%Y%m%d_%H%M%S")
    backup_file  = backup_dir / f"{db_label}_{ts_str}.db"
    manifest_file = backup_dir / f"{db_label}_{ts_str}.manifest.json"

    if dry_run:
        print(f"[DRY RUN] would create {backup_file}")
        _apply_retention(backup_dir, now, dry_run=True)
        return 0

    print(f"[backup] source : {db_path}")
    print(f"[backup] target : {backup_file}")

    # VACUUM INTO produces a consistent snapshot while the bot is running
    try:
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"VACUUM INTO '{backup_file}'")
        counts   = _row_counts(conn)
        ts_range = _ts_range(conn)
        conn.close()
    except Exception as exc:
        print(f"[error] VACUUM INTO failed: {exc}", file=sys.stderr)
        return 1

    sha       = _sha256(backup_file)
    file_size = backup_file.stat().st_size
    manifest  = {
        "backup_ts":       now.isoformat(),
        "db_path":         str(db_path),
        "db_label":        db_label,
        "backup_file":     str(backup_file),
        "file_size_bytes": file_size,
        "sha256":          sha,
        "git_sha":         _git_sha(),
        "row_counts":      counts,
        "ts_range":        ts_range,
    }
    manifest_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"[backup] sha256={sha[:16]}...  size={file_size // 1024} KB")
    print(f"[backup] manifest: {manifest_file.name}")

    # Optional off-host transfer (non-fatal if it fails)
    offhost_cmd = _load_env_var("BACKUP_OFFHOST_CMD")
    if offhost_cmd:
        cmd = offhost_cmd.format(file=backup_file, manifest=manifest_file)
        print(f"[backup] off-host: {cmd}")
        result = subprocess.run(cmd, shell=True, timeout=300)
        if result.returncode != 0:
            print(
                f"[warning] off-host command exited {result.returncode} — local backup is intact",
                file=sys.stderr,
            )

    _apply_retention(backup_dir, now, dry_run=False, db_label=db_label)
    print("[backup] done")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="BTC bot SQLite backup")
    parser.add_argument(
        "--db-path", type=Path, default=ROOT / "data" / "btc_trading.db",
        help="Path to btc_trading.db (default: data/btc_trading.db)",
    )
    parser.add_argument(
        "--backup-dir", type=Path, default=ROOT / "data" / "backups",
        help="Directory to store backup files (default: data/backups/)",
    )
    parser.add_argument(
        "--evidence", action="store_true",
        help="Back up research/data/forward_evidence.db (instead of btc_trading.db)",
    )
    parser.add_argument(
        "--all", dest="backup_all", action="store_true",
        help="Back up both btc_trading.db and forward_evidence.db",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would happen without writing anything",
    )
    args = parser.parse_args()

    if args.evidence or args.backup_all:
        evidence_db  = ROOT / "research" / "data" / "forward_evidence.db"
        evidence_dir = ROOT / "data" / "evidence-backups"
        rc_evidence  = run_backup(evidence_db, evidence_dir, args.dry_run,
                                  db_label="evidence")
        if args.evidence:
            return rc_evidence

    if not args.evidence:
        rc_trading = run_backup(args.db_path, args.backup_dir, args.dry_run,
                                db_label="btc_trading")
        if args.backup_all:
            return max(rc_trading, rc_evidence)     # type: ignore[possibly-undefined]
        return rc_trading

    return 0


if __name__ == "__main__":
    sys.exit(main())
