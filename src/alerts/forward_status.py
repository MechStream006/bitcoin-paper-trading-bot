"""forward_status — read-only summary of the daily forward-evidence cycle.

Why this module exists
----------------------
The Telegram heartbeat runs inside the long-lived WS-collector process
(``btcbot.service``).  The daily signal/evidence collection runs in a SEPARATE
one-shot process (``btcbot-fwd-hg.service`` -> ``run_daily.py``) at 01:00 UTC.
Before this module, the heartbeat had no visibility into whether that daily
cycle ran, whether governance passed, or whether the decision functions were
tampered with.

This module closes that blind spot by reading ``evidence_forward.db`` READ-ONLY
and producing a compact, never-raising summary.  It writes nothing, mutates
nothing, and imports nothing from the research/forward-evidence code — it only
reads the ``run_log`` table (schema: ``research/forward_evidence/schema_forward.sql``).

Governance note
---------------
Governance pass/fail is NOT persisted anywhere.  ``run_daily._check_governance()``
gates the run: on failure the run aborts and NO ``run_log`` rows are written for
that date.  Consequently a governance failure manifests as a STALE forward run
(the latest ``run_date`` does not advance).  We therefore only report ``gov=ok``
when it is provably true — a fresh, complete run, which cannot exist unless the
governance gate passed.  We never execute ``report.py`` from the heartbeat.

Total functions
---------------
``summarize()`` is designed to NEVER raise.  Any error (missing file, locked DB,
corrupt DB, unexpected schema) is captured and returned as a ForwardStatus whose
``state`` reflects the failure, so the heartbeat can always render a line.
"""
from __future__ import annotations

import dataclasses
import logging
import os
import pathlib
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# The five signals collected by run_daily.py.  ivrv/atr may legitimately have no
# run_log row on a data-unavailable day, so a count < 5 is a coverage note, not a
# failure on its own.
CANONICAL_SIGNALS = (
    "h5_vol_regime",
    "g1_market_regime_sma200",
    "g3_momentum_tsmom",
    "ivrv_vrp_spread",
    "g3_atr_expansion",
)

# run_log statuses that are operational problems (per schema CHECK constraint).
_FAILED_STATUSES = ("error",)
_VIOLATION_STATUS = "fn_hash_violation"

# Grace hour: the 01:00 UTC cycle should have produced yesterday's run_date by
# 02:00 UTC.  Before 02:00 UTC we expect the day-before-yesterday's run_date.
_GRACE_HOUR_UTC = 2


@dataclasses.dataclass(frozen=True)
class ForwardStatus:
    """Immutable, point-in-time summary of the forward-evidence cycle.

    state:
        "ok"        fresh run, no errors, no violations
        "stale"     latest run_date older than expected (missed/late cycle, or
                    governance failure which aborts before any write)
        "degraded"  fresh run but >=1 signal status='error'
        "critical"  >=1 signal status='fn_hash_violation' (tamper)
        "missing"   evidence_forward.db file not present (neutral in a split
                    deployment; never escalates on its own)
        "error"     file present but unreadable / corrupt / unexpected schema
    """

    state: str
    latest_run_date: Optional[str]
    expected_run_date: str
    signals: Dict[str, str]          # signal_id -> status, for latest_run_date
    n_ok: int
    n_logged: int
    detail: str = ""

    # ------------------------------------------------------------------
    # Convenience constructors for the non-DB terminal states
    # ------------------------------------------------------------------

    @staticmethod
    def missing(expected: str) -> "ForwardStatus":
        return ForwardStatus("missing", None, expected, {}, 0, 0,
                             "evidence_forward.db not found")

    @staticmethod
    def read_error(expected: str, detail: str) -> "ForwardStatus":
        return ForwardStatus("error", None, expected, {}, 0, 0, detail)

    # ------------------------------------------------------------------
    # Severity for the heartbeat overall-status roll-up
    # ------------------------------------------------------------------

    def severity(self) -> str:
        """Map state -> ops severity: 'critical' | 'degraded' | 'neutral' | 'ok'."""
        if self.state == "critical":
            return "critical"
        if self.state in ("stale", "degraded", "error"):
            return "degraded"
        if self.state == "missing":
            return "neutral"
        return "ok"


def expected_run_date(now: Optional[datetime] = None) -> str:
    """The most recent run_date we expect to see in run_log, given the clock.

    The forward job fires at 01:00 UTC for run_date = yesterday.  We allow a
    one-hour grace: at/after 02:00 UTC today, yesterday's run should exist;
    before that, the most recent completed run collected the day before.
    """
    d = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    base = d.date()
    delta_days = 1 if d.hour >= _GRACE_HOUR_UTC else 2
    return (base - timedelta(days=delta_days)).isoformat()


def summarize(db_path: str, now: Optional[datetime] = None) -> ForwardStatus:
    """Read run_log READ-ONLY and return a ForwardStatus.  Never raises.

    Args:
        db_path: path to evidence_forward.db.
        now:     reference time (UTC) for freshness; defaults to now.
    """
    expected = expected_run_date(now)

    if not db_path or not os.path.exists(db_path):
        return ForwardStatus.missing(expected)

    conn = None
    try:
        # mode=ro file URI: cannot create or write the database.
        uri = pathlib.Path(db_path).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        cur = conn.cursor()

        cur.execute("SELECT MAX(run_date) FROM run_log;")
        row = cur.fetchone()
        latest = row[0] if row else None

        if latest is None:
            # Empty run_log — no cycle has ever written.  Treat as stale.
            return ForwardStatus("stale", None, expected, {}, 0, 0,
                                 "run_log empty (no cycle on record)")

        cur.execute(
            "SELECT signal_id, status FROM run_log WHERE run_date = ?;",
            (latest,),
        )
        signals: Dict[str, str] = {sid: st for sid, st in cur.fetchall()}
    except Exception as exc:  # corrupt DB, locked beyond timeout, bad schema
        # Logged (not just returned) because this state is otherwise invisible
        # between Telegram heartbeats. "unable to open database file" on a
        # WAL-mode DB almost always means the *directory* lacks write access
        # in the caller's process (WAL readers must open/lock the -wal/-shm
        # sibling files even in mode=ro) -- check the caller's sandboxing
        # (e.g. systemd ReadWritePaths=) before suspecting this file or code.
        logger.warning(
            "[forward_status] summarize(%s) failed: %s: %s",
            db_path, type(exc).__name__, exc,
        )
        return ForwardStatus.read_error(expected, f"{type(exc).__name__}: {exc}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    n_logged = len(signals)
    n_ok = sum(1 for st in signals.values() if st == "ok")
    violations = [s for s, st in signals.items() if st == _VIOLATION_STATUS]
    errors = [s for s, st in signals.items() if st in _FAILED_STATUSES]

    if violations:
        state = "critical"
        detail = "fn_hash_violation: " + ",".join(sorted(violations))
    elif errors:
        state = "degraded"
        detail = "error: " + ",".join(sorted(errors))
    elif latest < expected:
        state = "stale"
        detail = f"latest={latest} < expected={expected}"
    else:
        state = "ok"
        detail = ""

    return ForwardStatus(
        state=state,
        latest_run_date=latest,
        expected_run_date=expected,
        signals=signals,
        n_ok=n_ok,
        n_logged=n_logged,
        detail=detail,
    )


def render_line(fs: ForwardStatus) -> str:
    """Render a single fixed-width heartbeat line for the forward summary.

    Examples
    --------
      fwd-evid     : 2026-06-28  ok=5/5  fn_hash=ok  gov=ok
      fwd-evid     : STALE  latest=2026-06-25 expected=2026-06-28  (ok=5/5)
      fwd-evid     : CRITICAL fn_hash_violation: g3_atr_expansion  (2026-06-28)
      fwd-evid     : DEGRADED error: g3_momentum_tsmom  (2026-06-28, ok=4/5)
      fwd-evid     : n/a (evidence_forward.db not found)
      fwd-evid     : [read error: DatabaseError: ...]
    """
    label = "fwd-evid     : "
    if fs.state == "missing":
        return label + "n/a (evidence_forward.db not found)"
    if fs.state == "error":
        return label + f"[read error: {fs.detail}]"

    counts = f"ok={fs.n_ok}/{fs.n_logged}"
    if fs.state == "ok":
        return label + f"{fs.latest_run_date}  {counts}  fn_hash=ok  gov=ok"
    if fs.state == "stale":
        return label + (
            f"STALE  latest={fs.latest_run_date} "
            f"expected={fs.expected_run_date}  ({counts})"
        )
    if fs.state == "critical":
        return label + f"CRITICAL {fs.detail}  ({fs.latest_run_date})"
    # degraded
    return label + f"DEGRADED {fs.detail}  ({fs.latest_run_date}, {counts})"


# ---------------------------------------------------------------------------
# Self-test / smoke (no live deps; builds a tiny in-place SQLite fixture)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import shutil
    import tempfile

    def _make_db(path: str, rows: list) -> None:
        c = sqlite3.connect(path)
        c.execute(
            "CREATE TABLE run_log (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " signal_id TEXT NOT NULL, run_date TEXT NOT NULL, fn_hash TEXT NOT NULL,"
            " status TEXT NOT NULL DEFAULT 'ok', note TEXT,"
            " created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),"
            " UNIQUE (signal_id, run_date));"
        )
        c.executemany(
            "INSERT INTO run_log (signal_id, run_date, fn_hash, status) VALUES (?,?,?,?);",
            rows,
        )
        c.commit()
        c.close()

    NOW = datetime(2026, 6, 30, 14, 0, 0, tzinfo=timezone.utc)
    assert expected_run_date(NOW) == "2026-06-29", expected_run_date(NOW)
    # before grace hour -> day before yesterday
    early = datetime(2026, 6, 30, 1, 0, 0, tzinfo=timezone.utc)
    assert expected_run_date(early) == "2026-06-28", expected_run_date(early)
    print("[t1] expected_run_date grace logic: PASS")

    # missing file
    fs = summarize("/no/such/file.db", NOW)
    assert fs.state == "missing" and fs.severity() == "neutral"
    print("[t2] missing file -> missing/neutral:", render_line(fs))

    d = tempfile.mkdtemp()
    try:
        # fresh, all ok (5 signals on expected date)
        p = os.path.join(d, "ok.db")
        _make_db(p, [(s, "2026-06-29", "ab" * 32, "ok") for s in CANONICAL_SIGNALS])
        fs = summarize(p, NOW)
        assert fs.state == "ok", fs
        assert fs.severity() == "ok"
        assert "gov=ok" in render_line(fs)
        print("[t3] fresh complete -> ok:", render_line(fs))

        # stale (governance-failure signature: run_date did not advance)
        p = os.path.join(d, "stale.db")
        _make_db(p, [(s, "2026-06-26", "ab" * 32, "ok") for s in CANONICAL_SIGNALS])
        fs = summarize(p, NOW)
        assert fs.state == "stale" and fs.severity() == "degraded", fs
        print("[t4] stale run -> stale/degraded:", render_line(fs))

        # fn_hash violation -> critical
        p = os.path.join(d, "viol.db")
        rows = [(s, "2026-06-29", "ab" * 32, "ok") for s in CANONICAL_SIGNALS[:-1]]
        rows.append(("g3_atr_expansion", "2026-06-29", "ab" * 32, "fn_hash_violation"))
        _make_db(p, rows)
        fs = summarize(p, NOW)
        assert fs.state == "critical" and fs.severity() == "critical", fs
        print("[t5] fn_hash_violation -> critical:", render_line(fs))

        # error status -> degraded (replace one signal's status, do not duplicate)
        p = os.path.join(d, "err.db")
        rows = [
            (s, "2026-06-29", "ab" * 32,
             "error" if s == "g3_momentum_tsmom" else "ok")
            for s in CANONICAL_SIGNALS
        ]
        _make_db(p, rows)
        fs = summarize(p, NOW)
        assert fs.state == "degraded" and fs.severity() == "degraded", fs
        print("[t6] error status -> degraded:", render_line(fs))

        # ivrv legitimately absent (4 rows) but fresh+clean -> still ok
        p = os.path.join(d, "partial.db")
        _make_db(p, [(s, "2026-06-29", "ab" * 32, "ok")
                     for s in CANONICAL_SIGNALS if s != "ivrv_vrp_spread"])
        fs = summarize(p, NOW)
        assert fs.state == "ok" and fs.n_logged == 4, fs
        print("[t7] partial coverage (ivrv absent) -> ok:", render_line(fs))

        # corrupt / non-sqlite file -> error, never raises
        p = os.path.join(d, "corrupt.db")
        with open(p, "wb") as fh:
            fh.write(b"not a sqlite database at all")
        fs = summarize(p, NOW)
        assert fs.state == "error" and fs.severity() == "degraded", fs
        print("[t8] corrupt file -> error (no raise):", render_line(fs))
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # all render outputs must be pure ASCII (Telegram heartbeat is ASCII-only)
    for st in ("ok", "stale", "critical", "degraded", "missing", "error"):
        sample = ForwardStatus(st, "2026-06-29", "2026-06-29",
                               {"x": "ok"}, 5, 5, "detail text")
        render_line(sample).encode("ascii")
    print("[t9] all render_line outputs are ASCII: PASS")

    print("\nforward_status.py smoke test passed.")
