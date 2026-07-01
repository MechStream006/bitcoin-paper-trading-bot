"""storage_status — read-only disk / database-file size telemetry.

Why this module exists
----------------------
An unattended service that runs for years dies most often from disk exhaustion
or an unbounded WAL.  Before this module the heartbeat had no view of free disk,
database size, or WAL size — so the most common silent killers were invisible.

This module provides pure, read-only filesystem telemetry: free-disk percentage
on a volume, and the on-disk sizes of a SQLite database plus its ``-wal`` and
``-shm`` sidecars.  It touches no database (no connection, no query) — only
``shutil.disk_usage`` and ``os.path.getsize``.

Total functions
---------------
``disk_free()`` and ``db_sizes()`` are designed to NEVER raise.  Any error
(missing path, permission, OS error) is captured and returned as an object whose
``ok`` flag is False, so the heartbeat can always render a line.

Escalation policy
-----------------
Only free disk escalates the heartbeat status (it is the headline unattended
killer).  Database / WAL sizes are display-only — there is no universal "too big"
threshold, and escalating on size would produce false alarms.
"""
from __future__ import annotations

import dataclasses
import os
import shutil
from typing import Optional

# Free-disk escalation thresholds (percent of volume free).  Conservative so a
# healthy box never false-alarms; tune here if the data volume is small.
DISK_CRITICAL_PCT = 5.0
DISK_DEGRADED_PCT = 15.0


@dataclasses.dataclass(frozen=True)
class DiskInfo:
    """Free-space snapshot for the volume holding a path."""

    ok: bool
    free_pct: Optional[float]      # 0..100
    free_bytes: Optional[int]
    total_bytes: Optional[int]
    detail: str = ""

    def severity(self) -> str:
        """'critical' | 'degraded' | 'ok' | 'neutral' (unknown -> neutral)."""
        if not self.ok or self.free_pct is None:
            return "neutral"
        if self.free_pct < DISK_CRITICAL_PCT:
            return "critical"
        if self.free_pct < DISK_DEGRADED_PCT:
            return "degraded"
        return "ok"


@dataclasses.dataclass(frozen=True)
class FileSizes:
    """On-disk sizes of a SQLite database and its WAL/SHM sidecars (bytes)."""

    ok: bool
    db_bytes: int = 0
    wal_bytes: int = 0
    shm_bytes: int = 0
    detail: str = ""


def disk_free(path: str) -> DiskInfo:
    """Free-space info for the volume containing ``path``.  Never raises.

    ``path`` may be a file (its directory's volume is measured) or a directory.
    """
    try:
        target = path if os.path.isdir(path) else os.path.dirname(path) or "."
        if not os.path.exists(target):
            return DiskInfo(False, None, None, None, "path not found")
        usage = shutil.disk_usage(target)
        pct = (usage.free / usage.total * 100.0) if usage.total else None
        return DiskInfo(True, pct, usage.free, usage.total, "")
    except Exception as exc:
        return DiskInfo(False, None, None, None, f"{type(exc).__name__}: {exc}")


def db_sizes(db_path: str) -> FileSizes:
    """Sizes of ``db_path`` and its ``-wal`` / ``-shm`` sidecars.  Never raises.

    A missing sidecar counts as 0 bytes (normal after a checkpoint).  A missing
    main DB file returns ok=False so the caller can distinguish "not present".
    """
    try:
        if not db_path or not os.path.exists(db_path):
            return FileSizes(False, detail="db not found")
        db_b = os.path.getsize(db_path)
        wal_b = os.path.getsize(db_path + "-wal") if os.path.exists(db_path + "-wal") else 0
        shm_b = os.path.getsize(db_path + "-shm") if os.path.exists(db_path + "-shm") else 0
        return FileSizes(True, db_b, wal_b, shm_b, "")
    except Exception as exc:
        return FileSizes(False, detail=f"{type(exc).__name__}: {exc}")


def human_bytes(n: Optional[int]) -> str:
    """Compact human-readable size: 0B, 41MB, 1.1GB, 2.0TB.  '?' for None."""
    if n is None:
        return "?"
    if n < 1024:
        return f"{n}B"
    units = ["KB", "MB", "GB", "TB", "PB"]
    val = float(n)
    for u in units:
        val /= 1024.0
        if val < 1024.0:
            # one decimal for GB+ readability, none for KB/MB
            return f"{val:.1f}{u}" if u in ("GB", "TB", "PB") else f"{val:.0f}{u}"
    return f"{val:.1f}PB"


# ---------------------------------------------------------------------------
# Self-test / smoke
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sqlite3
    import tempfile

    # human_bytes
    assert human_bytes(0) == "0B"
    assert human_bytes(512) == "512B"
    assert human_bytes(1024) == "1KB"
    assert human_bytes(41 * 1024 * 1024) == "41MB"
    assert human_bytes(int(1.1 * 1024 ** 3)) == "1.1GB"
    assert human_bytes(None) == "?"
    print("[t1] human_bytes formatting: PASS")

    # disk_free on a real existing dir
    di = disk_free(tempfile.gettempdir())
    assert di.ok and di.free_pct is not None and 0 <= di.free_pct <= 100, di
    assert di.severity() in ("ok", "degraded", "critical")
    print(f"[t2] disk_free real dir: {di.free_pct:.1f}% free, severity={di.severity()}")

    # disk_free on a missing path -> neutral, never raises
    di2 = disk_free("/no/such/volume/path")
    assert not di2.ok and di2.severity() == "neutral", di2
    print("[t3] disk_free missing path -> neutral: PASS")

    # severity thresholds
    assert DiskInfo(True, 3.0, 1, 100).severity() == "critical"
    assert DiskInfo(True, 10.0, 1, 100).severity() == "degraded"
    assert DiskInfo(True, 50.0, 1, 100).severity() == "ok"
    assert DiskInfo(False, None, None, None).severity() == "neutral"
    print("[t4] disk severity thresholds: PASS")

    # db_sizes on a real sqlite db (WAL mode -> -wal/-shm appear)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "x.db")
        c = sqlite3.connect(p)
        c.execute("PRAGMA journal_mode=WAL;")
        c.execute("CREATE TABLE t (a INTEGER);")
        c.execute("INSERT INTO t VALUES (1);")
        c.commit()
        fsz = db_sizes(p)
        assert fsz.ok and fsz.db_bytes > 0, fsz
        print(f"[t5] db_sizes: db={human_bytes(fsz.db_bytes)} "
              f"wal={human_bytes(fsz.wal_bytes)} shm={human_bytes(fsz.shm_bytes)}")
        c.close()

    # db_sizes on missing file -> ok=False, never raises
    fsz2 = db_sizes("/no/such/file.db")
    assert not fsz2.ok and fsz2.db_bytes == 0, fsz2
    print("[t6] db_sizes missing file -> not ok: PASS")

    print("\nstorage_status.py smoke test passed.")
