"""BTC Paper Trading Bot — Daily Evidence Certifier.

Certifies the evidence quality for a single UTC collection day.
Runs Tier-A checks (C1–C7) against the live SQLite DB and writes a
verdict to daily_certifier_log.  Optionally sends a Telegram alert on
AMBER or RED.

Tier-A checks (all DB-only):
  C1  coverage_completeness  — collection_metrics cycle-count vs expected
  C2  exch_ts_presence       — fraction of option_quotes rows with valid exch_ts
  C3  clock_latency          — ts − exch_ts/1e6 distribution (F8)
  C4  stale_repersist        — per-instrument duplicate exch_ts rate (F10)
  C5  iv_quality             — null / degenerate mark_iv rate
  C6  db_integrity           — PRAGMA integrity_check
  C7  platform_tag           — testnet data is AMBER by policy

Tier-B (C8–C12) and Tier-C (C13–C15) are deferred — see CLAUDE.md.

Checks marked PROVISIONAL require 7+ days of mainnet collection to
calibrate thresholds; they are flagged in the output but still enforce
conservative defaults.

B4 note — settlement_time timezone comparison:
  instruments.settlement_time uses 'Z' suffix; option_quotes.ts uses
  '+00:00'. A naive SQL string comparison is wrong (Z > + in ASCII).
  C10 (settlement integrity, Tier-B) MUST use REPLACE(settlement_time,
  'Z', '+00:00') when implemented.  B4 is documented here so it is not
  forgotten at Tier-B implementation time.

Usage:
    python scripts/certify_day.py [--date YYYY-MM-DD] [--db-path PATH]
                                   [--platform testnet|mainnet]
                                   [--no-persist] [--no-telegram]

Exit codes:
    0  GREEN  — all Tier-A checks pass; day is admissible evidence
    1  AMBER  — ≥1 AMBER, no RED; requires adjudication before research use
    2  RED    — ≥1 RED; day is non-admissible
    3  error  — certifier could not run (schema too old, DB missing, etc.)
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import socket
import sqlite3
import statistics
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REQUIRED_SCHEMA_VERSION = 3  # collection_metrics + stale_count (user_version >= 3)
CERTIFIER_LOG_SCHEMA_VERSION = 4  # daily_certifier_log (user_version >= 4)

# C1: expected collection_metrics rows per full day at the default 60 s cadence.
# PROVISIONAL (B5): if snapshot_interval_sec is changed in lifecycle.py this
# value is wrong.  Document any cadence change in CLAUDE.md §11 and update here.
EXPECTED_CYCLES_PER_DAY = 1440

# C1: a collection session is declared "partial" when its first or last row is
# more than 30 minutes from the UTC day boundary.  Partial days are capped at
# AMBER (not RED) on C1 because starting/stopping the bot is not a data-
# quality failure.
PARTIAL_DAY_THRESHOLD_SEC = 1800  # 30 minutes

# C3: exch_ts sane band (µs since epoch) — same values as storage.py
EXCH_TS_MIN = 1_000_000_000_000_000
EXCH_TS_MAX = 10_000_000_000_000_000

# Severity rank for aggregation
_RANK: dict[str, int] = {"GREEN": 0, "AMBER": 1, "RED": 2, "SKIP": -1}


# ---------------------------------------------------------------------------
# CheckResult dataclass
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class CheckResult:
    """Outcome of one Tier-A check.

    result:      GREEN / AMBER / RED / SKIP
    value:       measured quantity (float, dict, str, or None)
    threshold:   threshold applied (same type as value, or None)
    note:        human-readable detail — one line, no trailing punctuation
    provisional: True when the threshold needs mainnet calibration data;
                 the check still runs and enforces conservative defaults
    """
    check_id: str
    name: str
    result: str
    value: Any
    threshold: Any
    note: str
    provisional: bool = False

    def to_dict(self) -> dict:
        return {
            "result": self.result,
            "value": self.value,
            "threshold": self.threshold,
            "note": self.note,
            "provisional": self.provisional,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_ts(ts_str: str) -> datetime:
    """Parse ISO-8601 UTC string, tolerating both 'Z' and '+00:00' suffixes."""
    return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))


def _git_sha() -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT, capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() if r.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(
        row[1] == column
        for row in conn.execute(f"PRAGMA table_info({table})")
    )


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


# ---------------------------------------------------------------------------
# Individual Tier-A check functions (C1–C7)
# ---------------------------------------------------------------------------

def _check_c1_coverage(conn: sqlite3.Connection, day: str) -> CheckResult:
    """C1: coverage_completeness — fraction of expected 60 s cycles collected."""
    if not _has_table(conn, "collection_metrics"):
        return CheckResult("C1", "coverage_completeness", "AMBER", None, 0.95,
                           "collection_metrics table absent — run python scripts/migrate.py")

    row = conn.execute(
        "SELECT COUNT(*), MIN(ts), MAX(ts) FROM collection_metrics WHERE DATE(ts) = ?",
        (day,),
    ).fetchone()
    actual_cycles, min_ts, max_ts = row

    if actual_cycles == 0:
        return CheckResult("C1", "coverage_completeness", "RED", 0.0, 0.95,
                           f"no collection_metrics rows for {day}")

    # Partial day detection
    year, mon, dom = int(day[:4]), int(day[5:7]), int(day[8:10])
    day_start = datetime(year, mon, dom, 0, 0, 0, tzinfo=timezone.utc)
    day_end   = datetime(year, mon, dom, 23, 59, 0, tzinfo=timezone.utc)
    min_dt    = _parse_ts(min_ts)
    max_dt    = _parse_ts(max_ts)

    started_late = (min_dt - day_start).total_seconds() > PARTIAL_DAY_THRESHOLD_SEC
    ended_early  = (day_end - max_dt).total_seconds() > PARTIAL_DAY_THRESHOLD_SEC
    is_partial   = started_late or ended_early

    coverage = actual_cycles / EXPECTED_CYCLES_PER_DAY

    if is_partial:
        # Cap at AMBER for partial days — not a data-quality failure
        return CheckResult(
            "C1", "coverage_completeness", "AMBER", round(coverage, 4), 0.95,
            f"partial day: {actual_cycles} cycles, "
            f"started={min_ts[:16]}Z ended={max_ts[:16]}Z",
            provisional=True,
        )

    if coverage >= 0.95:
        result = "GREEN"
    elif coverage >= 0.85:
        result = "AMBER"
    else:
        result = "RED"

    return CheckResult(
        "C1", "coverage_completeness", result, round(coverage, 4), 0.95,
        f"{actual_cycles}/{EXPECTED_CYCLES_PER_DAY} cycles "
        f"(assumes 60 s cadence — PROVISIONAL)",
        provisional=True,
    )


def _check_c2_exch_ts_presence(conn: sqlite3.Connection, day: str) -> CheckResult:
    """C2: exch_ts_presence — fraction of option_quotes rows with non-NULL exch_ts."""
    if not _has_column(conn, "option_quotes", "exch_ts"):
        return CheckResult("C2", "exch_ts_presence", "AMBER", None, 0.99,
                           "exch_ts column absent — run python scripts/migrate.py")

    row = conn.execute(
        """
        SELECT COUNT(*) as total,
               SUM(CASE WHEN exch_ts IS NULL THEN 1 ELSE 0 END) as nulls
        FROM option_quotes WHERE DATE(ts) = ?
        """,
        (day,),
    ).fetchone()
    total, nulls = row[0], (row[1] or 0)

    if total == 0:
        return CheckResult("C2", "exch_ts_presence", "AMBER", None, 0.99,
                           f"no option_quotes rows for {day}")

    presence = 1.0 - nulls / total
    note = f"{total - nulls}/{total} rows have exch_ts"
    if nulls == total:
        note += " — likely pre-migration historical data (expected)"

    if presence >= 0.99:
        result = "GREEN"
    elif presence >= 0.95:
        result = "AMBER"
    else:
        result = "RED"

    return CheckResult("C2", "exch_ts_presence", result, round(presence, 4), 0.99, note)


def _check_c3_clock_latency(conn: sqlite3.Connection, day: str) -> CheckResult:
    """C3: clock_latency — ts − exch_ts/1e6 distribution detects clock drift (F8).

    Latency = wall-clock snapshot time minus exchange tick time.
    Expected range at 3 s WS cadence: 0–10 s (fresh ticks captured at snapshot).
    Systematic negatives indicate VPS clock is behind exchange time (NTP issue).

    PROVISIONAL: thresholds require 7-day mainnet calibration.
    """
    if not _has_column(conn, "option_quotes", "exch_ts"):
        return CheckResult("C3", "clock_latency", "AMBER", None, None,
                           "exch_ts column absent — run python scripts/migrate.py")

    rows = conn.execute(
        "SELECT ts, exch_ts FROM option_quotes WHERE DATE(ts) = ? AND exch_ts IS NOT NULL",
        (day,),
    ).fetchall()

    if not rows:
        return CheckResult("C3", "clock_latency", "AMBER", None, None,
                           f"no rows with valid exch_ts for {day}")

    latencies: list[float] = []
    for ts_str, exch_ts in rows:
        ts_dt   = _parse_ts(ts_str)
        exch_dt = datetime.fromtimestamp(exch_ts / 1_000_000.0, tz=timezone.utc)
        latencies.append((ts_dt - exch_dt).total_seconds())

    latencies.sort()
    n         = len(latencies)
    median_l  = statistics.median(latencies)
    p99_l     = latencies[min(int(0.99 * n), n - 1)]
    neg_count = sum(1 for x in latencies if x < 0)

    value     = {"median_sec": round(median_l, 3),
                 "p99_sec":    round(p99_l, 3),
                 "negatives":  neg_count,
                 "n":          n}
    threshold = {"median_max_sec": 10, "p99_max_sec": 30, "negatives_max": 0}

    is_red   = median_l > 60 or p99_l > 120 or neg_count > 5
    is_amber = not is_red and (median_l > 10 or p99_l > 30 or neg_count > 0)
    result   = "RED" if is_red else ("AMBER" if is_amber else "GREEN")

    return CheckResult(
        "C3", "clock_latency", result, value, threshold,
        f"median={median_l:.1f}s p99={p99_l:.1f}s negatives={neg_count}/{n}",
        provisional=True,
    )


def _check_c4_stale_repersist(conn: sqlite3.Connection, day: str) -> CheckResult:
    """C4: stale_repersist — per-instrument duplicate exch_ts rate (F10).

    A symbol whose exchange state did not change between two snapshot cycles
    produces two rows with the same exch_ts.  High duplication = stale ticks
    being re-persisted, inflating observation counts.

    PROVISIONAL: 10 % / 25 % thresholds require 7-day reconnect baseline.
    """
    if not _has_column(conn, "option_quotes", "exch_ts"):
        return CheckResult("C4", "stale_repersist", "AMBER", None, 0.10,
                           "exch_ts column absent — run python scripts/migrate.py")

    row = conn.execute(
        """
        SELECT SUM(total) AS grand_total, SUM(dupes) AS grand_dupes
        FROM (
            SELECT instrument_id,
                   COUNT(*)                              AS total,
                   COUNT(*) - COUNT(DISTINCT exch_ts)   AS dupes
            FROM option_quotes
            WHERE DATE(ts) = ? AND exch_ts IS NOT NULL
            GROUP BY instrument_id
        )
        """,
        (day,),
    ).fetchone()
    grand_total, grand_dupes = (row[0] or 0), (row[1] or 0)

    if grand_total == 0:
        return CheckResult("C4", "stale_repersist", "AMBER", None, 0.10,
                           f"no rows with valid exch_ts for {day}")

    dup_ratio = grand_dupes / grand_total

    if dup_ratio > 0.25:
        result = "RED"
    elif dup_ratio > 0.10:
        result = "AMBER"
    else:
        result = "GREEN"

    return CheckResult(
        "C4", "stale_repersist", result, round(dup_ratio, 4), 0.10,
        f"{grand_dupes} duplicates / {grand_total} rows ({dup_ratio:.1%})",
        provisional=True,
    )


def _check_c5_iv_quality(conn: sqlite3.Connection, day: str) -> CheckResult:
    """C5: iv_quality — null/degenerate mark_iv rate.

    Degenerate: mark_iv IS NULL, OR < 0.001 (0.1 % annualised — catches
    the testnet 0.000005 artefacts), OR > 5.0 (500 % — extreme outlier).
    """
    row = conn.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN mark_iv IS NULL
                             OR mark_iv < 0.001
                             OR mark_iv > 5.0
                        THEN 1 ELSE 0 END) AS bad
        FROM option_quotes WHERE DATE(ts) = ?
        """,
        (day,),
    ).fetchone()
    total, bad = row[0], (row[1] or 0)

    if total == 0:
        return CheckResult("C5", "iv_quality", "AMBER", None, 0.01,
                           f"no option_quotes rows for {day}")

    bad_pct = bad / total

    if bad_pct > 0.10:
        result = "RED"
    elif bad_pct > 0.01:
        result = "AMBER"
    else:
        result = "GREEN"

    return CheckResult(
        "C5", "iv_quality", result, round(bad_pct, 4), 0.01,
        f"{bad}/{total} degenerate IV rows",
    )


def _check_c6_integrity(conn: sqlite3.Connection) -> CheckResult:
    """C6: db_integrity — PRAGMA integrity_check (expensive; once per day)."""
    rows = conn.execute("PRAGMA integrity_check").fetchall()
    if rows == [("ok",)]:
        return CheckResult("C6", "db_integrity", "GREEN", "ok", "ok",
                           "integrity_check passed")
    errors = [r[0] for r in rows]
    return CheckResult(
        "C6", "db_integrity", "RED", errors[0], "ok",
        f"integrity_check FAILED ({len(errors)} error(s)): {errors[0]}",
    )


def _check_c7_platform(platform: str) -> CheckResult:
    """C7: platform_tag — testnet evidence is AMBER by policy (CLAUDE.md §11)."""
    if platform == "mainnet":
        return CheckResult("C7", "platform_tag", "GREEN", platform, "mainnet",
                           "platform=mainnet; evidence eligible for research use")
    return CheckResult(
        "C7", "platform_tag", "AMBER", platform, "mainnet",
        f"platform={platform!r}; testnet evidence is AMBER by policy "
        f"(OI≈0, degenerate quotes) — see CLAUDE.md §11",
    )


# ---------------------------------------------------------------------------
# Verdict aggregation
# ---------------------------------------------------------------------------

def _aggregate_verdict(results: list[CheckResult]) -> str:
    """Return the worst severity across all non-SKIP check results."""
    worst = "GREEN"
    for cr in results:
        if cr.result == "SKIP":
            continue
        if _RANK.get(cr.result, 0) > _RANK.get(worst, 0):
            worst = cr.result
    return worst


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _persist_verdict(
    conn: sqlite3.Connection,
    day: str,
    verdict: str,
    platform: str,
    certifier_version: str,
    check_results: list[CheckResult],
) -> None:
    checks_json = json.dumps(
        {cr.check_id: cr.to_dict() for cr in check_results}, indent=2
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO daily_certifier_log
        (day, run_ts, verdict, platform, certifier_version, checks_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            day,
            datetime.now(timezone.utc).isoformat(),
            verdict,
            platform,
            certifier_version,
            checks_json,
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Telegram alert
# ---------------------------------------------------------------------------

def _send_telegram_alert(
    result_dict: dict,
    *,
    bot_token: str,
    channel_id: str,
) -> None:
    verdict = result_dict["verdict"]
    day     = result_dict["day"]
    checks  = result_dict["checks"]

    hostname = socket.gethostname()
    lines = [f"[{verdict}] BTC Certifier {day} — {hostname}"]
    for check_id, data in checks.items():
        if data["result"] in ("AMBER", "RED"):
            lines.append(f"  [{data['result']}] {check_id}: {data['note']}")

    text = "\n".join(lines)
    try:
        from src.alerts.telegram import send_message
        send_message(bot_token, channel_id, text)
    except Exception as exc:
        print(f"[certifier] Telegram alert failed: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main certify function (callable; used directly by tests and by main())
# ---------------------------------------------------------------------------

def certify_day(
    conn: sqlite3.Connection,
    day: str,
    platform: str,
    *,
    persist: bool = True,
    certifier_version: str | None = None,
) -> dict:
    """Run all Tier-A checks for `day` and return a summary dict.

    Args:
        conn:               SQLite connection (must be at user_version >= 3).
        day:                YYYY-MM-DD UTC date to certify.
        platform:           "testnet" or "mainnet".
        persist:            Write result to daily_certifier_log if True.
        certifier_version:  Git SHA override; defaults to HEAD.

    Returns:
        {
          "day":               "YYYY-MM-DD",
          "verdict":           "GREEN" | "AMBER" | "RED",
          "platform":          "...",
          "certifier_version": "...",
          "checks": {
            "C1": {"result": "...", "value": ..., "threshold": ...,
                   "note": "...", "provisional": bool},
            ...
          }
        }

    Raises:
        RuntimeError: if user_version < REQUIRED_SCHEMA_VERSION.
    """
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < REQUIRED_SCHEMA_VERSION:
        raise RuntimeError(
            f"DB schema version {version} < required {REQUIRED_SCHEMA_VERSION}. "
            f"Run: python scripts/migrate.py"
        )

    if certifier_version is None:
        certifier_version = _git_sha()

    checks: list[CheckResult] = [
        _check_c1_coverage(conn, day),
        _check_c2_exch_ts_presence(conn, day),
        _check_c3_clock_latency(conn, day),
        _check_c4_stale_repersist(conn, day),
        _check_c5_iv_quality(conn, day),
        _check_c6_integrity(conn),
        _check_c7_platform(platform),
    ]

    verdict = _aggregate_verdict(checks)

    if persist:
        if _has_table(conn, "daily_certifier_log"):
            _persist_verdict(conn, day, verdict, platform, certifier_version, checks)
        else:
            print(
                "[certifier] WARNING: daily_certifier_log missing "
                "— run python scripts/migrate.py (v4) to persist verdicts",
                file=sys.stderr,
            )

    return {
        "day":               day,
        "verdict":           verdict,
        "platform":          platform,
        "certifier_version": certifier_version,
        "checks":            {cr.check_id: cr.to_dict() for cr in checks},
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="BTC bot daily evidence certifier")
    ap.add_argument(
        "--date", default=None,
        help="YYYY-MM-DD UTC date to certify (default: yesterday UTC)",
    )
    ap.add_argument("--db-path", type=Path, default=None)
    ap.add_argument(
        "--platform", default=None,
        help="Override platform tag: testnet | mainnet (default: PLATFORM env var)",
    )
    ap.add_argument(
        "--no-persist", action="store_true",
        help="Run checks without writing to daily_certifier_log",
    )
    ap.add_argument(
        "--no-telegram", action="store_true",
        help="Skip Telegram alert",
    )
    args = ap.parse_args()

    # Resolve defaults from settings (imported here so module is testable without .env)
    from config.settings import (
        DB_PATH,
        PLATFORM as CFG_PLATFORM,
        TELEGRAM_BOT_TOKEN,
        TELEGRAM_CHANNEL_ID,
    )

    db_path  = args.db_path or DB_PATH
    platform = args.platform or CFG_PLATFORM
    day      = args.date or (date.today() - timedelta(days=1)).isoformat()

    if not Path(db_path).exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        return 3

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    try:
        result = certify_day(conn, day, platform, persist=not args.no_persist)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3
    finally:
        conn.close()

    print(json.dumps(result, indent=2))

    verdict = result["verdict"]
    if verdict in ("AMBER", "RED") and not args.no_telegram:
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_ID:
            _send_telegram_alert(
                result, bot_token=TELEGRAM_BOT_TOKEN, channel_id=TELEGRAM_CHANNEL_ID
            )

    if verdict == "RED":
        return 2
    if verdict == "AMBER":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
