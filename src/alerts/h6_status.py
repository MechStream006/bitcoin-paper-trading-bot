"""h6_status — read-only summary of the H6 daily corpus-refresh cycle.

Why this module exists
----------------------
Mirrors ``src/alerts/forward_status.py``'s pattern for the H6 pipeline
(``research/forward_evidence/daily_job.py`` -> ``forward_evidence.db``):
reads ``run_log`` READ-ONLY and produces a compact, never-raising summary
for the heartbeat (``src/alerts/heartbeat.py``).

Relationship to scripts/healthcheck.py::check_h6_cycle()
----------------------------------------------------------
Severity semantics here are DELIBERATELY IDENTICAL to
``scripts/healthcheck.py::check_h6_cycle()`` (same constants, same priority
order, same evidence: 2026-07-01 and 2026-08-01 are the only two proven-
benign isolated no_new_data days, so a single isolated occurrence never
escalates; 2+ consecutive has no benign precedent and does).

The logic is duplicated rather than imported: ``scripts/healthcheck.py`` is
a standalone CLI script (argparse, subprocess calls, a ``__main__`` block)
that is not designed to be imported into the long-running bot process. If
the two ever need to diverge, ``check_h6_cycle()`` remains the source of
truth for severity semantics -- update this module to match, not the other
way around.

Governance note
----------------
Like ``forward_status.py``, this module writes nothing and mutates
nothing. It never implies sufficiency or Validated-readiness from any
count -- that judgment is out of scope for a heartbeat/dead-man read.
"""
from __future__ import annotations

import dataclasses
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# Mirrors scripts/healthcheck.py's H6_GRACE_HOUR_UTC / H6_NO_NEW_DATA_WARN_STREAK /
# FORWARD_WARN_DAYS / FORWARD_CRIT_DAYS / _H6_STREAK_LOOKBACK exactly.
_GRACE_HOUR_UTC = 2
_NO_NEW_DATA_WARN_STREAK = 2
_WARN_DAYS = 1
_CRIT_DAYS = 2
_STREAK_LOOKBACK = 14


@dataclasses.dataclass(frozen=True)
class H6Status:
    """Immutable, point-in-time summary of the H6 daily corpus-refresh cycle.

    state:
        "ok"         fresh run, status='ok'
        "warning"    1 day stale, OR >=2 consecutive no_new_data rows, OR
                     latest row status='warning'
        "critical"   >=2 days stale, OR latest row status='error' (fn_hash
                     tamper), OR run_log exists but has never written a row
        "missing"    forward_evidence.db file not present (neutral; never
                     escalates on its own)
        "error"      file present but unreadable / corrupt / unexpected schema
    """

    state: str
    severity: str                  # "ok" | "degraded" | "critical"
    latest_date: Optional[str]
    expected_date: str
    days_behind: Optional[int]
    latest_status: Optional[str]
    no_new_data_streak: int
    detail: str = ""

    @staticmethod
    def missing(expected: str) -> "H6Status":
        return H6Status("missing", "ok", None, expected, None, None, 0,
                         "forward_evidence.db not found")

    @staticmethod
    def read_error(expected: str, detail: str) -> "H6Status":
        return H6Status("error", "critical", None, expected, None, None, 0, detail)


def expected_date(now: Optional[datetime] = None) -> date:
    """Most recent calendar date the H6 run_log should show a row for.

    Mirrors forward_status.expected_run_date()'s grace-hour shape, but
    daily_job.py's own run_log stamps run_ts on the SAME calendar date it
    executes on (unlike H5's run_date, which is "yesterday" by convention)
    -- so the expected date is "today" once past the grace hour, else
    "yesterday", with no extra day offset.
    """
    d = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return d.date() if d.hour >= _GRACE_HOUR_UTC else (d.date() - timedelta(days=1))


def summarize(h6_db_path: str, now: Optional[datetime] = None) -> H6Status:
    """Read forward_evidence.db's run_log READ-ONLY and return an H6Status.

    Never raises: any unexpected error is folded into an "error" H6Status.
    """
    expected = expected_date(now)

    if not h6_db_path or not Path(h6_db_path).exists():
        return H6Status.missing(expected.isoformat())

    conn = None
    try:
        uri = Path(h6_db_path).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT run_ts, status FROM run_log ORDER BY run_ts DESC LIMIT ?",
            (_STREAK_LOOKBACK,),
        ).fetchall()
    except Exception as exc:
        return H6Status.read_error(expected.isoformat(), f"{type(exc).__name__}: {exc}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    if not rows:
        return H6Status(
            "critical", "critical", None, expected.isoformat(), None, None, 0,
            "H6 cycle has never run",
        )

    latest = rows[0]
    latest_date = datetime.fromtimestamp(latest["run_ts"] / 1000, tz=timezone.utc).date()
    days = max(0, (expected - latest_date).days)

    streak = 0
    for r in rows:
        if r["status"] == "no_new_data":
            streak += 1
        else:
            break

    latest_status = latest["status"]
    common = (latest_date.isoformat(), expected.isoformat(), days, latest_status, streak)

    if latest_status == "error":
        return H6Status("critical", "critical", *common, "fn_hash violation")
    if days >= _CRIT_DAYS:
        return H6Status("critical", "critical", *common, "cycle stale")
    if days >= _WARN_DAYS:
        return H6Status("warning", "degraded", *common, "cycle stale")
    if streak >= _NO_NEW_DATA_WARN_STREAK:
        return H6Status(
            "warning", "degraded", *common,
            f"no new data for {streak} consecutive runs",
        )
    if latest_status == "warning":
        return H6Status("warning", "degraded", *common, "signal error, cycle ran")
    return H6Status("ok", "ok", *common, "")


# ---------------------------------------------------------------------------
# Self-test / smoke (no live deps; builds a tiny in-place SQLite fixture)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    import shutil
    import tempfile

    def _make_db(path: str, rows: list) -> None:
        c = sqlite3.connect(path)
        c.execute(
            "CREATE TABLE run_log (run_ts INTEGER PRIMARY KEY, status TEXT NOT NULL)"
        )
        c.executemany("INSERT INTO run_log (run_ts, status) VALUES (?, ?)", rows)
        c.commit()
        c.close()

    def _ms(dt: datetime) -> int:
        return int(dt.timestamp() * 1000)

    NOW = datetime(2026, 8, 11, 14, 0, 0, tzinfo=timezone.utc)

    fs = summarize("/no/such/file.db", NOW)
    assert fs.state == "missing" and fs.severity == "ok"
    print("[t1] missing file -> missing/ok:", fs.detail)

    d = tempfile.mkdtemp()
    try:
        p = os.path.join(d, "empty.db")
        _make_db(p, [])
        fs = summarize(p, NOW)
        assert fs.state == "critical" and "never run" in fs.detail, fs
        print("[t2] empty run_log -> critical:", fs.detail)

        p = os.path.join(d, "fresh.db")
        _make_db(p, [(_ms(NOW), "ok")])
        fs = summarize(p, NOW)
        assert fs.state == "ok" and fs.severity == "ok", fs
        print("[t3] fresh ok -> ok:", fs)

        p = os.path.join(d, "single_no_new_data.db")
        _make_db(p, [
            (_ms(NOW - timedelta(days=1)), "ok"),
            (_ms(NOW), "no_new_data"),
        ])
        fs = summarize(p, NOW)
        assert fs.state == "ok" and fs.no_new_data_streak == 1, fs
        print("[t4] single isolated no_new_data -> ok (proven benign):", fs)

        p = os.path.join(d, "double_no_new_data.db")
        _make_db(p, [
            (_ms(NOW - timedelta(days=2)), "ok"),
            (_ms(NOW - timedelta(days=1)), "no_new_data"),
            (_ms(NOW), "no_new_data"),
        ])
        fs = summarize(p, NOW)
        assert fs.state == "warning" and fs.severity == "degraded", fs
        print("[t5] 2 consecutive no_new_data -> warning/degraded:", fs)

        p = os.path.join(d, "error.db")
        _make_db(p, [(_ms(NOW), "error")])
        fs = summarize(p, NOW)
        assert fs.state == "critical" and fs.severity == "critical", fs
        print("[t6] status='error' -> critical:", fs)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    print("\nh6_status.py smoke test passed.")
