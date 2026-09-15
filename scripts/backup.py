"""BTC Paper Trading Bot — SQLite backup script.

Creates a consistent hot backup using VACUUM INTO (SQLite 3.27+), writes a
SHA-256 manifest, applies 7-daily/4-weekly/12-monthly rotation, then
optionally runs a configurable off-host transfer command.

Backs up five databases:
  1. data/btc_trading.db                    (Layer 2 live trading runtime)
  2. research/data/forward_evidence.db       (H6 forward evidence; --evidence)
  3. research/data/evidence_forward.db       (H5/G1/G3/ivrv collector; --fwd-hg)
  4. research/evidence_library/ledger.db     (governance ledger; --ledger)
  5. research/label_store/label_store.db     (Label Store; --label-store)
  6. research/dataset_builder/dataset_builder.db (Dataset Registry; --dataset-builder)
  7. research/feature_store/feature_store.db  (Feature Store; --feature-store)
  8. research/data/decision_features.db      (decision-time feature capture; --decision-features)
  9. research/data/decision_outcomes.db      (execution-outcome capture; --decision-outcomes)

Label Store note (Phase 3 WBS 3.10, LS-DEC-001): the registry, research
tier, and forward tier are co-located in one file by design (LS-DEC-001,
approved 2026-08-06) -- so this is one backup unit, not three. Recovery
*requirements* still differ by table (the registry is regenerable via
`research/label_store/register_conventions.py --commit`; the forward tier
is irreplaceable from its first row, ADR-I011 §7) even though the backup
*mechanism* below operates on the whole file, exactly as it already does
for `ledger.db`'s own mix of table criticality.

Usage:
    python scripts/backup.py [--db-path PATH] [--backup-dir PATH] [--dry-run]
    python scripts/backup.py --evidence [--dry-run]
    python scripts/backup.py --fwd-hg [--dry-run]
    python scripts/backup.py --ledger [--dry-run]
    python scripts/backup.py --label-store [--dry-run]
    python scripts/backup.py --all [--dry-run]   # backs up all five databases
    python scripts/backup.py --all --encrypt     # + encrypt for off-host (BK3)

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

# Kept in sync with offhost_backup.py's own constants -- duplicated rather than
# imported so retention (which runs on every backup, encrypted or not) never
# forces an unconditional import of the encryption module.
CIPHERTEXT_SUFFIX   = ".age"
AGE_MANIFEST_SUFFIX = ".age-manifest.json"

OFFHOST_STATUS_FILENAME = ".offhost-status.json"

#: Every db_label `--all` can produce, in the order it produces them.
#: `--exclude` is validated against this so a typo in a systemd unit fails the
#: backup loudly at 02:00 instead of silently backing up the wrong set.
ALL_LABELS = (
    "evidence", "fwd_hg", "ledger", "label_store",
    "dataset_builder", "feature_store", "decision_features",
    "decision_outcomes", "btc_trading",
)


class UnknownLabel(ValueError):
    """--exclude named a db_label that does not exist."""


def _validate_excludes(excludes) -> set[str]:
    unknown = sorted(set(excludes) - set(ALL_LABELS))
    if unknown:
        raise UnknownLabel(
            f"unknown --exclude label(s): {', '.join(unknown)}; "
            f"valid labels are: {', '.join(ALL_LABELS)}"
        )
    return set(excludes)


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


def _backup_date(path: Path, db_label: str) -> datetime | None:
    """UTC date encoded in `{db_label}_YYYYMMDD_HHMMSS...`, or None if unreadable.

    Reads the name rather than mtime on purpose: a file copied or restored
    keeps its logical backup date, and retention must not be fooled into
    treating a recovered artifact as brand new.
    """
    name = path.name[len(db_label) + 1:] if path.name.startswith(db_label + "_") else path.name
    for part in name.split("_"):
        if part.isdigit() and len(part) == 8:
            try:
                return datetime.strptime(part, "%Y%m%d").replace(tzinfo=timezone.utc)
            except ValueError:
                return None
    return None


def _within_retention(bdate: datetime, now: datetime) -> bool:
    """The 7-daily / 4-weekly / 12-monthly rotation, unchanged."""
    age_days = (now - bdate).days
    return (age_days <= KEEP_DAILY_DAYS
            or (bdate.weekday() == 6 and age_days <= KEEP_WEEKLY_DAYS)
            or (bdate.day == 1 and age_days <= KEEP_MONTHLY_DAYS))


def _discard_shipped_ciphertext(ciphertext: Path, age_manifest: Path) -> bool:
    """Drop the local `.age` once it is safely off-host. False if it could not be.

    The ciphertext is an intermediate: it exists to be shipped. Keeping it for
    the whole retention window stored every backup twice on a volume with room
    for one -- 36 GB of a 58 GB disk, half of it a byte-for-byte duplicate of
    something already in S3 under versioning and a 90-day Object Lock.

    Removed only when the transfer command reported success AND the local file
    still matches the `ciphertext_sha256` recorded in its manifest. An exit
    code says the command ran, not that it shipped this artifact intact, and a
    backup is the wrong thing to discard on that alone.

    Two things are deliberately KEPT:
      - the plaintext backup, the documented on-host fast restore path, whose
        sole owner remains GFS retention;
      - the `.age-manifest.json`, a few hundred bytes carrying the ciphertext
        and plaintext checksums needed to verify and decrypt the S3 copy.
        Discarding it would damage the restore path this change exists to
        protect.
    """
    try:
        import offhost_backup
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import offhost_backup

    try:
        offhost_backup.verify_ciphertext(ciphertext, age_manifest)
    except Exception as exc:  # noqa: BLE001
        print(f"[error] not discarding {ciphertext.name}: {exc}", file=sys.stderr)
        return False

    try:
        size = ciphertext.stat().st_size
        ciphertext.unlink()
    except Exception as exc:  # noqa: BLE001
        # Never silent: failing to reclaim is how the disk filled in the first
        # place, and an unnoticed failure here reproduces it exactly.
        print(f"[error] could not remove shipped ciphertext {ciphertext}: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        print("[error] backup and off-host transfer both SUCCEEDED; only the "
              "local cleanup failed", file=sys.stderr)
        return False

    print(f"[backup] discarded shipped ciphertext {ciphertext.name} "
          f"({size // 1024} KB reclaimed; manifest and plaintext kept)")
    return True


def _apply_retention(backup_dir: Path, now: datetime, dry_run: bool,
                     db_label: str = "btc_trading") -> None:
    """Delete backup files outside the retention window.

    Two passes, because a ciphertext can outlive its plaintext. The first pass
    expires plaintext snapshots and takes their `.age` / `.age-manifest.json`
    with them. The second sweeps ciphertexts that no longer HAVE a plaintext
    sibling: any plaintext removed by something other than this function -- an
    operator reclaiming disk, a storage-preservation gate, a stray `rm` --
    used to strand its ciphertext permanently, because the only path that
    could ever delete a `.age` was the deletion of the plaintext whose name
    derived it. Twelve such orphans had accumulated on production by
    2026-08-21, ~14 GB that retention could no longer see.

    The second pass applies the SAME date rule as the first: a stranded
    ciphertext ages out on its own schedule rather than living forever.
    """
    kept, deleted = [], []

    for f in sorted(backup_dir.glob(f"{db_label}_*.db")):
        bdate = _backup_date(f, db_label)
        if bdate is None:
            kept.append(f)
            continue
        (kept if _within_retention(bdate, now) else deleted).append(f)

    for f in deleted:
        manifest   = f.parent / (f.stem + ".manifest.json")
        ciphertext = f.parent / (f.name + CIPHERTEXT_SUFFIX)
        age_manifest = f.parent / (f.name + AGE_MANIFEST_SUFFIX)
        if dry_run:
            print(f"[DRY RUN] would delete {f.name}")
        else:
            f.unlink(missing_ok=True)
            manifest.unlink(missing_ok=True)
            ciphertext.unlink(missing_ok=True)
            age_manifest.unlink(missing_ok=True)
            print(f"[retention] deleted {f.name}")

    orphans_kept, orphans_deleted = [], []
    for ct in sorted(backup_dir.glob(f"{db_label}_*{CIPHERTEXT_SUFFIX}")):
        plaintext = ct.parent / ct.name[: -len(CIPHERTEXT_SUFFIX)]
        if plaintext.exists():
            continue                      # pass 1 owns this pair
        bdate = _backup_date(ct, db_label)
        if bdate is None:
            orphans_kept.append(ct)
            continue
        (orphans_kept if _within_retention(bdate, now)
         else orphans_deleted).append(ct)

    for ct in orphans_deleted:
        age_manifest = ct.parent / (ct.name[: -len(CIPHERTEXT_SUFFIX)]
                                    + AGE_MANIFEST_SUFFIX)
        if dry_run:
            print(f"[DRY RUN] would delete orphaned ciphertext {ct.name}")
        else:
            ct.unlink(missing_ok=True)
            age_manifest.unlink(missing_ok=True)
            print(f"[retention] deleted orphaned ciphertext {ct.name}")

    print(f"[retention] kept={len(kept)}  deleted={len(deleted)}"
          f"  orphan_ciphertext_kept={len(orphans_kept)}"
          f"  orphan_ciphertext_deleted={len(orphans_deleted)}")


def _write_offhost_status(
    backup_dir: Path,
    db_label: str,
    now: datetime,
    configured: bool,
    encrypted: bool,
    status: str,
    exit_code: int | None,
    artifact: Path | None,
) -> None:
    """Record the outcome of the off-host transfer leg (P1 Increment 6 / BK3).

    `backup.py`'s off-host transfer failure is deliberately non-fatal (the
    local backup is intact and is the fast restore path), which means a
    broken off-host leg previously had no persisted signal at all -- only a
    stderr line inside a oneshot unit's journal. This sidecar is that signal.

    `last_success_at` is carried forward from the prior sidecar and only
    updated on a genuine success, so a staleness check can detect "off-host
    has been failing every day" even though `attempted_at` refreshes daily.
    """
    status_file = backup_dir / OFFHOST_STATUS_FILENAME
    last_success_at = None
    if status_file.exists():
        try:
            prior = json.loads(status_file.read_text(encoding="utf-8"))
            last_success_at = prior.get("last_success_at")
        except Exception:
            last_success_at = None
    if status == "ok":
        last_success_at = now.isoformat()

    payload = {
        "schema":          "btcbot-offhost-status/1",
        "attempted_at":    now.isoformat(),
        "last_success_at": last_success_at,
        "db_label":        db_label,
        "configured":      configured,
        "encrypted":       encrypted,
        "status":          status,       # "ok" | "failed" | "not_configured"
        "exit_code":       exit_code,
        "artifact":        artifact.name if artifact else None,
        "artifact_sha256": _sha256(artifact) if artifact and artifact.exists() else None,
    }
    status_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Database classification (Option A, ruled 2026-08-06 -- PROD-1).
#
# REQUIRED: the operational trading database. Its absence is a real failure --
#   the bot cannot be running without it -- so a missing file is an ERROR and
#   the run exits non-zero.
#
# OPTIONAL: the four research databases. Each is created by a research process
#   that may legitimately never have run on a given host. On EC2 today,
#   label_store.db and feature_store.db do not exist at all, and never will
#   until their phases are deployed. Treating their absence as failure made
#   `--all` exit 1 every day while all four real backups succeeded (PROD-1),
#   destroying the trustworthiness of the backup signal itself.
#
# A missing OPTIONAL database is logged as SKIPPED and does not affect the
# exit code. A missing REQUIRED database still fails, loudly -- which is the
# case that actually matters.
# ---------------------------------------------------------------------------
REQUIRED_DB_LABELS = frozenset({"btc_trading"})


def is_required(db_label: str) -> bool:
    """True when this database's absence is a failure rather than a skip."""
    return db_label in REQUIRED_DB_LABELS


def run_backup(db_path: Path, backup_dir: Path, dry_run: bool,
               db_label: str = "btc_trading", encrypt: bool = False) -> int:
    if not db_path.exists():
        if is_required(db_label):
            print(f"[error] required DB not found: {db_path}", file=sys.stderr)
            return 1
        print(f"[skip] optional DB not present, nothing to back up: {db_path}")
        return 0

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
        conn.close()

        # Manifest metadata is read from the SNAPSHOT, never from the live
        # database. VACUUM INTO freezes the artifact at time T; reading counts
        # from the live connection afterwards describes time T+duration, and on
        # a large continuously-written database that gap spans real writes. The
        # 2026-08-17 btc_trading.db backup recorded 66 option_quotes and 1
        # collection_metrics row more than the artifact actually contained --
        # exactly one 1-minute collection cycle at a 66-symbol universe, with
        # ts_range.max 60s ahead of the snapshot's true max. The artifact was
        # sound; the manifest describing it was not.
        #
        # mode=ro is load-bearing, not stylistic: _sha256(backup_file) runs
        # immediately after this block, so the artifact's bytes must be
        # provably untouched by the inspection. A read-write open could update
        # the header or create -wal/-shm siblings and invalidate the hash the
        # manifest is about to record.
        snap_uri = backup_file.resolve().as_uri() + "?mode=ro"
        snap = sqlite3.connect(snap_uri, uri=True)
        try:
            counts   = _row_counts(snap)
            ts_range = _ts_range(snap)
        finally:
            snap.close()
    except Exception as exc:
        # Covers both stages: creating the snapshot and opening it to describe
        # it. Fail-closed -- an artifact that cannot be opened is never hashed,
        # encrypted, or shipped off-host.
        print(f"[error] snapshot creation or inspection failed: {exc}", file=sys.stderr)
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

    # --- Optional encryption before the artifact leaves the host (BK3) ------
    # When --encrypt is set, the off-host command ships the CIPHERTEXT and its
    # .age-manifest.json instead of the plaintext DB. The local plaintext
    # backup is deliberately left in place: it is the fast on-host restore
    # path, and BK3 is about what leaves the host, not what lives on it.
    #
    # Fail-closed: if encryption is requested and fails, the off-host transfer
    # is SKIPPED entirely rather than falling back to shipping plaintext. A
    # silent downgrade to unencrypted transfer would be the worst possible
    # outcome -- it looks like success while defeating the whole control.
    xfer_file, xfer_manifest = backup_file, manifest_file
    if encrypt:
        try:
            import offhost_backup
        except ImportError:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import offhost_backup
        try:
            recipient = _load_env_var("BACKUP_AGE_RECIPIENT")
            ct, age_mf = offhost_backup.encrypt_backup(backup_file, recipient)
            xfer_file, xfer_manifest = ct, age_mf
            print(f"[backup] encrypted: {ct.name} ({ct.stat().st_size // 1024} KB)")
        except Exception as exc:
            print(f"[error] encryption failed: {exc}", file=sys.stderr)
            print("[error] off-host transfer SKIPPED — refusing to ship plaintext",
                  file=sys.stderr)
            _apply_retention(backup_dir, now, dry_run=False, db_label=db_label)
            return 1

    # Optional off-host transfer (non-fatal if it fails). Only attempted when
    # encryption was actually requested and succeeded -- BACKUP_OFFHOST_CMD
    # being configured is not sufficient on its own; that alone previously
    # let an unencrypted run (e.g. the real --all unit) ship plaintext,
    # exactly what BK3 exists to prevent.
    offhost_cmd = _load_env_var("BACKUP_OFFHOST_CMD")
    shipped = False
    if offhost_cmd and encrypt:
        cmd = offhost_cmd.format(file=xfer_file, manifest=xfer_manifest)
        print(f"[backup] off-host: {cmd}")
        result = subprocess.run(cmd, shell=True, timeout=300)
        shipped = result.returncode == 0
        if result.returncode != 0:
            print(
                f"[warning] off-host command exited {result.returncode} — local backup is intact",
                file=sys.stderr,
            )
        _write_offhost_status(
            backup_dir, db_label, now,
            configured=True, encrypted=encrypt,
            status="ok" if result.returncode == 0 else "failed",
            exit_code=result.returncode,
            artifact=xfer_file,
        )
    elif offhost_cmd:
        # Configured, but this invocation was unencrypted (e.g. the real
        # --all unit's ledger leg once a dedicated encrypted timer also
        # exists for the same db_label/backup_dir) -- it made no transfer
        # attempt, so it has nothing to report. Do NOT overwrite whatever a
        # real encrypted attempt already recorded here; that would defeat
        # the staleness escalation the sidecar exists for (a broken/missing
        # encrypted run would otherwise be reset to a harmless-looking
        # "not_configured" every day and never escalate).
        pass
    else:
        _write_offhost_status(
            backup_dir, db_label, now,
            configured=False, encrypted=encrypt,
            status="not_configured",
            exit_code=None,
            artifact=None,
        )

    reclaimed = True
    if encrypt and shipped:
        reclaimed = _discard_shipped_ciphertext(xfer_file, xfer_manifest)

    _apply_retention(backup_dir, now, dry_run=False, db_label=db_label)
    print("[backup] done")
    return 0 if reclaimed else 1


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
        help="Back up research/data/forward_evidence.db (H6 forward evidence)",
    )
    parser.add_argument(
        "--fwd-hg", dest="fwd_hg", action="store_true",
        help="Back up research/data/evidence_forward.db (H5/G1/G3/ivrv collector)",
    )
    parser.add_argument(
        "--ledger", action="store_true",
        help="Back up research/evidence_library/ledger.db (governance ledger)",
    )
    parser.add_argument(
        "--label-store", dest="label_store", action="store_true",
        help="Back up research/label_store/label_store.db (Label Store registry + "
             "research + forward tiers, co-located per LS-DEC-001)",
    )
    parser.add_argument(
        "--dataset-builder", dest="dataset_builder", action="store_true",
        help="Back up research/dataset_builder/dataset_builder.db (Dataset "
             "Registry, Phase 4 WBS 4.1, DS-DEC-005)",
    )
    parser.add_argument(
        "--feature-store", dest="feature_store", action="store_true",
        help="Back up research/feature_store/feature_store.db (Feature Store "
             "registry: Feature Specifications + feature.register receipts)",
    )
    parser.add_argument(
        "--decision-features", dest="decision_features", action="store_true",
        help="Back up research/data/decision_features.db (decision-time "
             "feature contract capture for ACKED live entries)",
    )
    parser.add_argument(
        "--decision-outcomes", dest="decision_outcomes", action="store_true",
        help="Back up research/data/decision_outcomes.db (execution-outcome "
             "capture for captured ACKED decisions)",
    )
    parser.add_argument(
        "--all", dest="backup_all", action="store_true",
        help="Back up all six databases (btc_trading, forward_evidence, "
             "evidence_forward, ledger, label_store, dataset_builder)",
    )
    parser.add_argument(
        "--exclude", action="append", default=[], metavar="LABEL",
        help="Skip this db_label (repeatable). Exists so a database that has "
             "its OWN dedicated encrypted timer is not ALSO backed up by the "
             "--all unit: btc_trading and ledger each acquired a BK3 timer "
             "without being removed from --all's scope, which produced two "
             "full snapshots of the same 1.5 GB database every night. "
             f"Valid labels: {', '.join(ALL_LABELS)}",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would happen without writing anything",
    )
    parser.add_argument(
        "--encrypt", action="store_true",
        help="Encrypt each artifact with age before off-host transfer (BK3). "
             "Requires BACKUP_AGE_RECIPIENT. Fail-closed: if encryption fails, "
             "the off-host transfer is skipped rather than shipping plaintext.",
    )
    args = parser.parse_args()

    try:
        excluded = _validate_excludes(args.exclude)
    except UnknownLabel as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    def skip(label: str) -> bool:
        """Exclusions apply to --all only.

        An explicit single-database flag is an operator asking for that exact
        backup; silently doing nothing would be worse than ignoring the
        exclusion, so `--ledger --exclude ledger` still backs up the ledger.
        """
        if label in excluded and args.backup_all:
            print(f"[skip] {label}: excluded by --exclude")
            return True
        return False

    rc_evidence    = 0
    rc_fwd_hg      = 0
    rc_ledger      = 0
    rc_label_store = 0
    rc_dataset_builder = 0
    rc_feature_store = 0
    rc_decision_features = 0
    rc_decision_outcomes = 0
    rc_trading     = 0

    if (args.evidence or args.backup_all) and not skip("evidence"):
        evidence_db  = ROOT / "research" / "data" / "forward_evidence.db"
        evidence_dir = ROOT / "data" / "evidence-backups"
        rc_evidence  = run_backup(evidence_db, evidence_dir, args.dry_run,
                                  db_label="evidence", encrypt=args.encrypt)
        if args.evidence and not args.backup_all:
            return rc_evidence

    if (args.fwd_hg or args.backup_all) and not skip("fwd_hg"):
        rc_fwd_hg = run_backup(
            ROOT / "research" / "data" / "evidence_forward.db",
            ROOT / "data" / "evidence-fwd-backups",
            args.dry_run,
            db_label="fwd_hg",
            encrypt=args.encrypt,
        )
        if args.fwd_hg and not args.backup_all:
            return rc_fwd_hg

    if (args.ledger or args.backup_all) and not skip("ledger"):
        rc_ledger = run_backup(
            ROOT / "research" / "evidence_library" / "ledger.db",
            ROOT / "data" / "ledger-backups",
            args.dry_run,
            db_label="ledger",
            encrypt=args.encrypt,
        )
        if args.ledger and not args.backup_all:
            return rc_ledger

    if (args.label_store or args.backup_all) and not skip("label_store"):
        rc_label_store = run_backup(
            ROOT / "research" / "label_store" / "label_store.db",
            ROOT / "data" / "label-store-backups",
            args.dry_run,
            db_label="label_store",
            encrypt=args.encrypt,
        )
        if args.label_store and not args.backup_all:
            return rc_label_store

    if (args.dataset_builder or args.backup_all) and not skip("dataset_builder"):
        rc_dataset_builder = run_backup(
            ROOT / "research" / "dataset_builder" / "dataset_builder.db",
            ROOT / "data" / "dataset-builder-backups",
            args.dry_run,
            db_label="dataset_builder",
            encrypt=args.encrypt,
        )
        if args.dataset_builder and not args.backup_all:
            return rc_dataset_builder

    if (args.feature_store or args.backup_all) and not skip("feature_store"):
        rc_feature_store = run_backup(
            ROOT / "research" / "feature_store" / "feature_store.db",
            ROOT / "data" / "feature-store-backups",
            args.dry_run,
            db_label="feature_store",
            encrypt=args.encrypt,
        )
        if args.feature_store and not args.backup_all:
            return rc_feature_store

    if (args.decision_features or args.backup_all) and not skip("decision_features"):
        rc_decision_features = run_backup(
            ROOT / "research" / "data" / "decision_features.db",
            ROOT / "data" / "decision-features-backups",
            args.dry_run,
            db_label="decision_features",
            encrypt=args.encrypt,
        )
        if args.decision_features and not args.backup_all:
            return rc_decision_features

    if (args.decision_outcomes or args.backup_all) and not skip("decision_outcomes"):
        rc_decision_outcomes = run_backup(
            ROOT / "research" / "data" / "decision_outcomes.db",
            ROOT / "data" / "decision-outcomes-backups",
            args.dry_run,
            db_label="decision_outcomes",
            encrypt=args.encrypt,
        )
        if args.decision_outcomes and not args.backup_all:
            return rc_decision_outcomes

    if (args.backup_all or not (args.evidence or args.fwd_hg or args.ledger
                                or args.label_store or args.dataset_builder
                                or args.feature_store or args.decision_features
                                or args.decision_outcomes)) and not skip("btc_trading"):
        rc_trading = run_backup(args.db_path, args.backup_dir, args.dry_run,
                                db_label="btc_trading", encrypt=args.encrypt)

    if args.backup_all:
        return max(rc_trading, rc_evidence, rc_fwd_hg, rc_ledger,
                   rc_label_store, rc_dataset_builder, rc_feature_store,
                   rc_decision_features, rc_decision_outcomes)
    return rc_trading


if __name__ == "__main__":
    sys.exit(main())
