"""BTC Paper Trading Bot — daily certifier scheduler wrapper.

Runs `certify_day.py` for every UTC day in a bounded lookback window that has
collection data but no verdict yet. This is the ExecStart target of
`btcbot-certify.service`.

Why a wrapper exists at all
---------------------------
`Persistent=true` on a systemd timer replays only ONE missed activation, not
one per missed day. If the host is down from Monday to Thursday, the catch-up
run certifies Wednesday and Monday/Tuesday are never certified — a permanent,
silent hole in the evidence record. On a platform whose admissibility argument
is "the certifier reported green", a silently uncertified day is exactly the
failure this must not have.

This wrapper contains NO certification logic. It decides only *which days to
ask about* and shells out to `certify_day.py` unmodified, one day per
invocation, through its documented `--date` CLI. Verdict semantics, checks,
thresholds, and persistence all remain owned by `certify_day.py`.

Idempotency
-----------
A day already present in `daily_certifier_log` is skipped, so repeated runs on
the same day do no work and cannot produce duplicate or conflicting verdicts.
(`certify_day.py` itself is also idempotent — `INSERT OR REPLACE` keyed on
`day` — so the skip is a second, cheaper layer, not the only one.) To
deliberately re-certify a day, call `certify_day.py --date` directly; this
wrapper will never overwrite an existing verdict.

Exit codes
----------
    0  every candidate day now has a verdict (GREEN, AMBER, or RED)
    3  at least one day could not be certified (certifier error / exit 3)

Note that AMBER and RED are *successful* runs of the certifier: the job's
purpose is to produce a verdict, not to produce a particular verdict. Only an
inability to reach a verdict is a failure. `btcbot-certify.service` mirrors
this with `SuccessExitStatus=0 1 2`.
"""
from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CERTIFY_SCRIPT = ROOT / "scripts" / "certify_day.py"

DEFAULT_LOOKBACK_DAYS = 7

EXIT_OK = 0
EXIT_ERROR = 3

# certify_day.py: 0 GREEN, 1 AMBER, 2 RED are all "a verdict was reached".
VERDICT_EXIT_CODES = {0, 1, 2}
_VERDICT_BY_CODE = {0: "GREEN", 1: "AMBER", 2: "RED"}


def utc_yesterday(now: datetime | None = None) -> date:
    """Yesterday in UTC, independent of host timezone.

    `certify_day.py`'s own default uses `date.today()`, which is LOCAL. The
    production VPS is UTC so the two agree there, but this wrapper passes
    `--date` explicitly so a host with a non-UTC clock certifies the intended
    day rather than silently drifting by one.
    """
    return ((now or datetime.now(timezone.utc)).date()) - timedelta(days=1)


def missing_tables(conn: sqlite3.Connection) -> list[str]:
    """Required tables absent from the schema, if any.

    Fail-closed: with no `collection_metrics` table every day looks like "no
    data" and the wrapper would report a clean success on a database that
    cannot be certified at all. That is the difference between "nothing to
    certify" and "wrong schema", and reporting the second as the first would
    make a misconfigured deploy look healthy.

    `certify_day.py` remains the authoritative schema check (it enforces exact
    `user_version` requirements and exits 3). This is only the cheaper guard
    that stops the wrapper from claiming success before ever calling it.
    """
    present = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    return [t for t in ("collection_metrics", "daily_certifier_log") if t not in present]


def _certified_days(conn: sqlite3.Connection) -> set[str]:
    try:
        return {r[0] for r in conn.execute("SELECT day FROM daily_certifier_log")}
    except sqlite3.OperationalError:
        # Table absent => schema older than v4. certify_day.py reports this
        # authoritatively (exit 3); do not duplicate its schema check here.
        return set()


def _days_with_data(conn: sqlite3.Connection, days: list[str]) -> set[str]:
    """Days that actually have collection cycles.

    Days with no `collection_metrics` rows are skipped rather than certified:
    before deployment, or during a full-day outage, there is nothing to certify
    and a RED verdict would misrepresent 'we did not collect' as 'we collected
    badly'. An outage is a gap in the collection record, which the heartbeat and
    healthcheck dead-man already report; it is not a data-quality verdict.
    """
    if not days:
        return set()
    placeholders = ",".join("?" * len(days))
    try:
        rows = conn.execute(
            f"SELECT DISTINCT DATE(ts) FROM collection_metrics WHERE DATE(ts) IN ({placeholders})",
            days,
        )
        return {r[0] for r in rows}
    except sqlite3.OperationalError:
        return set()


def plan(conn: sqlite3.Connection, lookback: int, today_ref: date) -> tuple[list[str], list[str], list[str]]:
    """Return (to_certify, already_certified, no_data) for the lookback window.

    Oldest first, so a backfill fills the record in chronological order.
    """
    # `today_ref` IS the newest day to certify (yesterday UTC), so offsets run
    # lookback-1 .. 0 inclusive. Using range(lookback, 0, -1) here would produce
    # yesterday-7 .. yesterday-1 and silently never certify yesterday itself --
    # the one day this job exists to certify.
    window = [
        (today_ref - timedelta(days=offset)).isoformat()
        for offset in range(lookback - 1, -1, -1)
    ]
    certified = _certified_days(conn)
    with_data = _days_with_data(conn, window)

    to_certify, already, no_data = [], [], []
    for day in window:
        if day in certified:
            already.append(day)
        elif day not in with_data:
            no_data.append(day)
        else:
            to_certify.append(day)
    return to_certify, already, no_data


def run_certifier(day: str, db_path: Path | None, platform: str | None,
                  send_telegram: bool) -> int:
    cmd = [sys.executable, str(CERTIFY_SCRIPT), "--date", day]
    if db_path:
        cmd += ["--db-path", str(db_path)]
    if platform:
        cmd += ["--platform", platform]
    if not send_telegram:
        cmd += ["--no-telegram"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    # certify_day.py prints its full JSON result to stdout; forward it so the
    # per-day verdict lands in journald and stays greppable.
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Certify uncertified UTC days in a bounded window")
    ap.add_argument("--lookback", type=int, default=DEFAULT_LOOKBACK_DAYS,
                    help=f"Days back from yesterday to consider (default {DEFAULT_LOOKBACK_DAYS})")
    ap.add_argument("--db-path", type=Path, default=None)
    ap.add_argument("--platform", default=None, help="testnet | mainnet (default: PLATFORM env var)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the plan without invoking the certifier")
    ap.add_argument("--no-telegram", action="store_true",
                    help="Suppress alerts for every day, including the most recent")
    args = ap.parse_args(argv)

    from config.settings import DB_PATH
    db_path = args.db_path or Path(DB_PATH)

    if not Path(db_path).exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        return EXIT_ERROR

    yesterday = utc_yesterday()
    # Read-only: this wrapper only decides which days to ask about. All writes
    # to daily_certifier_log are made by certify_day.py's own connection.
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    try:
        absent = missing_tables(conn)
        if absent:
            print(
                f"ERROR: schema too old — missing table(s): {', '.join(absent)}. "
                "Run: python scripts/migrate.py --dry-run && python scripts/migrate.py",
                file=sys.stderr,
            )
            return EXIT_ERROR
        to_certify, already, no_data = plan(conn, args.lookback, yesterday)
    finally:
        conn.close()

    print(f"[certify-catchup] window={args.lookback}d ending {yesterday.isoformat()} | "
          f"to_certify={len(to_certify)} already={len(already)} no_data={len(no_data)}")
    for day in no_data:
        print(f"  skip {day}: no collection_metrics rows")
    for day in already:
        print(f"  skip {day}: verdict already recorded")

    if args.dry_run:
        for day in to_certify:
            print(f"  would certify {day}")
        return EXIT_OK

    failures = []
    for day in to_certify:
        # Alert only for the most recent day. A backfill after an outage would
        # otherwise emit one Telegram message per missed day, which is an alert
        # storm at exactly the moment the operator is already dealing with the
        # outage. Every verdict is still permanently recorded in
        # daily_certifier_log, so the audit record stays complete either way.
        send_telegram = (day == yesterday.isoformat()) and not args.no_telegram
        code = run_certifier(day, args.db_path, args.platform, send_telegram)
        if code in VERDICT_EXIT_CODES:
            print(f"[certify-catchup] {day}: {_VERDICT_BY_CODE[code]}")
        else:
            print(f"[certify-catchup] {day}: CERTIFIER ERROR (exit {code})", file=sys.stderr)
            failures.append(day)

    if failures:
        print(f"[certify-catchup] FAILED for {len(failures)} day(s): {', '.join(failures)}",
              file=sys.stderr)
        return EXIT_ERROR

    print(f"[certify-catchup] done; {len(to_certify)} day(s) certified")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
