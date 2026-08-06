"""BTC Paper Trading Bot — health check script.

Read-only monitoring: no writes to the runtime DB, no runtime mutations.
Checks service status, disk space, DB liveness, WAL size, backup freshness,
and (optionally) PRAGMA integrity_check. Sends Telegram alerts when
conditions breach configured thresholds.

Usage:
    python scripts/healthcheck.py [options]

Exit codes:
    0  all OK / informational
    1  at least one WARNING (no CRITICAL)
    2  at least one CRITICAL
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Severity
# ---------------------------------------------------------------------------
OK       = "OK"
INFO     = "INFO"
WARNING  = "WARNING"
CRITICAL = "CRITICAL"

_RANK = {OK: 0, INFO: 1, WARNING: 2, CRITICAL: 3}


@dataclass
class CheckResult:
    name:     str
    severity: str
    message:  str
    detail:   dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Thresholds (from approved design review)
# ---------------------------------------------------------------------------
DB_LIVENESS_WARN_SEC  = 60
DB_LIVENESS_CRIT_SEC  = 300
DISK_WARN_BYTES       = 5  * 1024 ** 3   # 5 GB
DISK_CRIT_BYTES       = 2  * 1024 ** 3   # 2 GB
WAL_WARN_BYTES        = 200 * 1024 ** 2   # 200 MB
MEM_WARN_BYTES        = 512 * 1024 ** 2   # 512 MB
MEM_CRIT_BYTES        = 1024 * 1024 ** 2  # 1 GB
BACKUP_WARN_HOURS     = 48
OFFHOST_WARN_HOURS    = 48
OFFHOST_CRIT_HOURS    = 24 * 7
OFFHOST_STATUS_FILENAME = ".offhost-status.json"

# Forward-cycle dead-man (D3a): days behind expected run_date -> severity.
FORWARD_WARN_DAYS     = 1   # 1 day stale  -> WARNING
FORWARD_CRIT_DAYS     = 2   # 2 days stale -> CRITICAL

# Short display labels for forward signal ids (Telegram alert readability).
_FWD_SIG_ALIASES = {
    "h5_vol_regime":            "h5",
    "g1_market_regime_sma200":  "g1",
    "g3_momentum_tsmom":        "g3",
    "ivrv_vrp_spread":          "ivrv",
    "g3_atr_expansion":         "atr",
}


# ---------------------------------------------------------------------------
# Env helper (reads .env if env var not set)
# ---------------------------------------------------------------------------

def _load_env(key: str) -> str:
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


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def check_service(service_name: str) -> CheckResult:
    """systemd service status + memory. Skipped gracefully on non-Linux."""
    if platform.system() != "Linux":
        return CheckResult(
            "service", INFO,
            f"systemd checks skipped ({platform.system()})",
            {"platform": platform.system()},
        )
    try:
        r = subprocess.run(
            ["systemctl", "is-active", service_name],
            capture_output=True, text=True, timeout=10,
        )
        state = r.stdout.strip()
        if state != "active":
            return CheckResult(
                "service", CRITICAL,
                f"{service_name} is not active (state={state})",
                {"state": state},
            )

        r2 = subprocess.run(
            ["systemctl", "show", service_name,
             "--property=MainPID,MemoryCurrent"],
            capture_output=True, text=True, timeout=10,
        )
        props: dict[str, str] = {}
        for line in r2.stdout.strip().splitlines():
            k, _, v = line.partition("=")
            props[k] = v

        detail: dict = {"state": "active", "MainPID": props.get("MainPID", "?")}
        severity = OK
        msg      = f"{service_name} active"

        mem_str = props.get("MemoryCurrent", "")
        if mem_str and mem_str.isdigit():
            mem_bytes = int(mem_str)
            mem_mb    = round(mem_bytes / 1024 ** 2, 1)
            detail["memory_mb"] = mem_mb
            if mem_bytes >= MEM_CRIT_BYTES:
                severity = CRITICAL
                msg = (f"{service_name} memory {mem_mb} MB "
                       f"(>= {MEM_CRIT_BYTES // 1024**2} MB limit)")
            elif mem_bytes >= MEM_WARN_BYTES:
                severity = WARNING
                msg = (f"{service_name} memory {mem_mb} MB "
                       f"(>= {MEM_WARN_BYTES // 1024**2} MB warning)")

        return CheckResult("service", severity, msg, detail)

    except FileNotFoundError:
        return CheckResult("service", INFO, "systemctl not found — skipping", {})
    except Exception as exc:
        return CheckResult("service", WARNING, f"service check error: {exc}", {})


def check_disk(data_dir: Path) -> CheckResult:
    try:
        usage   = shutil.disk_usage(str(data_dir))
        free_gb = usage.free / 1024 ** 3
        detail  = {
            "free_gb":  round(free_gb, 2),
            "total_gb": round(usage.total / 1024 ** 3, 2),
            "used_pct": round(100 * usage.used / usage.total, 1),
        }
        if usage.free < DISK_CRIT_BYTES:
            return CheckResult(
                "disk", CRITICAL,
                f"disk critical: {free_gb:.1f} GB free (< {DISK_CRIT_BYTES // 1024**3} GB)",
                detail,
            )
        if usage.free < DISK_WARN_BYTES:
            return CheckResult(
                "disk", WARNING,
                f"disk low: {free_gb:.1f} GB free (< {DISK_WARN_BYTES // 1024**3} GB)",
                detail,
            )
        return CheckResult("disk", OK, f"disk OK: {free_gb:.1f} GB free", detail)
    except Exception as exc:
        return CheckResult("disk", WARNING, f"disk check error: {exc}", {})


def check_db_liveness(db_path: Path) -> CheckResult:
    if not db_path.exists():
        return CheckResult(
            "db_liveness", CRITICAL, f"DB not found: {db_path}", {}
        )
    try:
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        row  = conn.execute("SELECT max(ts) FROM option_quotes").fetchone()
        conn.close()

        latest_ts_str = row[0] if row else None
        if not latest_ts_str:
            return CheckResult(
                "db_liveness", WARNING, "option_quotes is empty", {}
            )

        latest_ts = datetime.fromisoformat(
            latest_ts_str.replace("Z", "+00:00")
        )
        age_sec = (datetime.now(timezone.utc) - latest_ts).total_seconds()
        detail  = {"latest_ts": latest_ts_str, "age_sec": round(age_sec, 1)}

        if age_sec >= DB_LIVENESS_CRIT_SEC:
            return CheckResult(
                "db_liveness", CRITICAL,
                f"DB stale: last tick {age_sec:.0f}s ago "
                f"(>= {DB_LIVENESS_CRIT_SEC}s)",
                detail,
            )
        if age_sec >= DB_LIVENESS_WARN_SEC:
            return CheckResult(
                "db_liveness", WARNING,
                f"DB stale: last tick {age_sec:.0f}s ago "
                f"(>= {DB_LIVENESS_WARN_SEC}s)",
                detail,
            )
        return CheckResult(
            "db_liveness", OK, f"DB live: last tick {age_sec:.0f}s ago", detail
        )
    except Exception as exc:
        return CheckResult("db_liveness", CRITICAL, f"DB liveness error: {exc}", {})


def check_wal(db_path: Path) -> CheckResult:
    wal_path = db_path.parent / (db_path.name + "-wal")
    if not wal_path.exists():
        return CheckResult("wal", OK, "no WAL file (clean checkpoint)", {})
    size    = wal_path.stat().st_size
    size_mb = round(size / 1024 ** 2, 1)
    detail  = {"wal_size_mb": size_mb}
    if size >= WAL_WARN_BYTES:
        return CheckResult(
            "wal", WARNING,
            f"WAL large: {size_mb} MB (>= {WAL_WARN_BYTES // 1024**2} MB)",
            detail,
        )
    return CheckResult("wal", OK, f"WAL OK: {size_mb} MB", detail)


def check_backup_freshness(
    backup_dir: Path, label: str = "btc_trading", check_name: str = "backup",
    required: bool = True,
) -> CheckResult:
    """Freshness of the most recent `{label}_*.db` backup in `backup_dir`.

    `label`/`check_name` default to the original trading-DB behavior so every
    existing caller is unaffected. Pass `label="ledger"`,
    `check_name="ledger_backup"` to check the governance ledger's backup
    (written by `scripts/backup.py --ledger` to `data/ledger-backups/`)
    instead -- same glob convention, same staleness threshold, since both are
    produced by the same daily `--all` cadence.
    """
    # `required` (Option A, ruled 2026-08-06 -- PROD-2). An OPTIONAL database
    # that has never been backed up on this host is not a fault: its research
    # process may legitimately never have run here. Reporting INFO keeps the
    # check visible in the JSON summary while ensuring it can never escalate
    # `overall` (INFO ranks below WARNING) and therefore never sends Telegram.
    #
    # Freshness is still enforced for optional databases once backups DO
    # exist -- only the never-configured case is downgraded. A stale optional
    # backup is a real signal and stays a WARNING below.
    absent_severity = WARNING if required else INFO
    absent_suffix = "" if required else " (optional database, not configured on this host)"

    if not backup_dir.exists():
        return CheckResult(
            check_name, absent_severity,
            f"backup directory does not exist{absent_suffix}", {}
        )
    backups = sorted(backup_dir.glob(f"{label}_*.db"), reverse=True)
    if not backups:
        return CheckResult(
            check_name, absent_severity,
            f"no backups found in backup directory{absent_suffix}", {}
        )
    newest    = backups[0]
    mtime     = datetime.fromtimestamp(newest.stat().st_mtime, tz=timezone.utc)
    age_hours = (datetime.now(timezone.utc) - mtime).total_seconds() / 3600
    detail    = {"newest_backup": newest.name, "age_hours": round(age_hours, 1)}

    if age_hours >= BACKUP_WARN_HOURS:
        return CheckResult(
            check_name, WARNING,
            f"backup stale: {newest.name} ({age_hours:.1f}h ago, "
            f">= {BACKUP_WARN_HOURS}h)",
            detail,
        )
    return CheckResult(
        check_name, OK,
        f"backup OK: {newest.name} ({age_hours:.1f}h ago)",
        detail,
    )


def check_offhost_freshness(
    backup_dir: Path, check_name: str = "offhost_backup"
) -> CheckResult:
    """Freshness of the off-host transfer leg (P1 Increment 6 / BK3).

    Reads the `.offhost-status.json` sidecar `scripts/backup.py` writes after
    every off-host transfer attempt. Deliberately silent (INFO) whenever
    off-host backup is not configured -- this must never generate alert noise
    on a host or database that has not opted in, or the check would be
    ignored before it ever matters.

    `last_success_at` (carried forward across runs by `_write_offhost_status`)
    is what staleness is measured against, not `attempted_at` -- a host that
    attempts a transfer every day and fails every day must still show growing
    staleness, not a perpetually "just attempted" timestamp.
    """
    status_file = backup_dir / OFFHOST_STATUS_FILENAME
    if not status_file.exists():
        return CheckResult(
            check_name, INFO,
            "off-host backup not configured or not yet run", {},
        )
    try:
        payload = json.loads(status_file.read_text(encoding="utf-8"))
    except Exception as exc:
        return CheckResult(
            check_name, WARNING, f"off-host status file unreadable: {exc}", {}
        )

    if not payload.get("configured"):
        return CheckResult(check_name, INFO, "off-host backup not configured", {})

    status          = payload.get("status")
    last_success_at = payload.get("last_success_at")
    detail = {
        "status": status,
        "last_success_at": last_success_at,
        "attempted_at": payload.get("attempted_at"),
    }

    if not last_success_at:
        return CheckResult(
            check_name, CRITICAL,
            "off-host backup configured but has never succeeded", detail,
        )

    try:
        success_ts = datetime.fromisoformat(last_success_at)
    except Exception:
        return CheckResult(
            check_name, WARNING, "off-host status last_success_at unparsable", detail,
        )

    age_hours = (datetime.now(timezone.utc) - success_ts).total_seconds() / 3600
    detail["age_hours"] = round(age_hours, 1)

    if age_hours >= OFFHOST_CRIT_HOURS:
        return CheckResult(
            check_name, CRITICAL,
            f"off-host backup stale: last success {age_hours:.0f}h ago "
            f"(>= {OFFHOST_CRIT_HOURS}h)",
            detail,
        )
    if age_hours >= OFFHOST_WARN_HOURS:
        return CheckResult(
            check_name, WARNING,
            f"off-host backup stale: last success {age_hours:.0f}h ago "
            f"(>= {OFFHOST_WARN_HOURS}h)",
            detail,
        )
    if status == "failed":
        return CheckResult(
            check_name, WARNING,
            f"off-host backup: latest attempt failed (last success "
            f"{age_hours:.0f}h ago)",
            detail,
        )
    return CheckResult(
        check_name, OK,
        f"off-host backup OK: last success {age_hours:.0f}h ago", detail,
    )


def check_integrity(db_path: Path) -> CheckResult:
    if not db_path.exists():
        return CheckResult(
            "integrity", CRITICAL, f"DB not found: {db_path}", {}
        )
    try:
        conn   = sqlite3.connect(str(db_path), check_same_thread=False)
        result = conn.execute("PRAGMA integrity_check").fetchall()
        conn.close()
        if result == [("ok",)]:
            return CheckResult("integrity", OK, "integrity_check OK", {})
        errors = [r[0] for r in result]
        return CheckResult(
            "integrity", CRITICAL,
            f"integrity_check FAILED: {errors[0]}"
            + (f" (+{len(errors)-1} more)" if len(errors) > 1 else ""),
            {"errors": errors},
        )
    except Exception as exc:
        return CheckResult("integrity", CRITICAL, f"integrity_check error: {exc}", {})


# ---------------------------------------------------------------------------
# Forward-cycle dead-man (D3a)
# ---------------------------------------------------------------------------

def _days_behind(latest: Optional[str], expected: str) -> Optional[int]:
    """Whole days between latest run_date and expected (>=0), or None if unknown."""
    if not latest:
        return None
    try:
        delta = (date.fromisoformat(expected) - date.fromisoformat(latest)).days
        return max(0, delta)
    except Exception:
        return None


def _fwd_signals_str(signals: dict) -> str:
    """Compact 'h5=ok g1=ok ...' rendering of the latest per-signal statuses."""
    if not signals:
        return "no signals"
    return " ".join(
        f"{_FWD_SIG_ALIASES.get(sid, sid)}={st}"
        for sid, st in sorted(signals.items())
    )


def check_forward_cycle(forward_db_path: Path) -> CheckResult:
    """D3a dead-man for the daily forward-evidence cycle.

    Independent of the bot process (this runs on the healthcheck timer), so it
    catches a missed 01:00 cycle or a governance failure even when the collector
    is down.  Reuses src.alerts.forward_status as the single source of truth so
    the external check and the in-process heartbeat never diverge.

    Severity (per approved design):
      fn_hash_violation / unreadable run_log / never-ran -> CRITICAL
      >= 2 days behind                                    -> CRITICAL
      1 day behind / a signal errored                     -> WARNING
      missing DB                                          -> INFO (skip)
      fresh + clean                                       -> OK
    """
    try:
        from src.alerts import forward_status as fs
    except Exception as exc:
        return CheckResult(
            "forward_cycle", WARNING, f"forward_status import failed: {exc}", {}
        )

    try:
        fsum = fs.summarize(str(forward_db_path))
    except Exception as exc:  # summarize is total, but guard anyway
        return CheckResult(
            "forward_cycle", CRITICAL, f"forward summarize error: {exc}", {}
        )

    expected = fsum.expected_run_date
    latest   = fsum.latest_run_date
    days     = _days_behind(latest, expected)
    sig_str  = _fwd_signals_str(fsum.signals)
    base     = f"expected {expected}, latest {latest or 'none'}, {('%d' % days) if days is not None else '?'} days behind | {sig_str}"
    detail   = {
        "state":             fsum.state,
        "expected_run_date": expected,
        "latest_run_date":   latest,
        "days_behind":       days,
        "signals":           fsum.signals,
    }

    state = fsum.state
    if state == "missing":
        return CheckResult(
            "forward_cycle", INFO,
            f"forward DB not present ({forward_db_path.name}) — skipping", detail,
        )
    if state == "error":
        return CheckResult(
            "forward_cycle", CRITICAL,
            f"forward run_log unreadable: {fsum.detail} | {base}", detail,
        )
    if state == "critical":   # fn_hash_violation
        return CheckResult(
            "forward_cycle", CRITICAL,
            f"forward fn_hash_violation ({fsum.detail}) | {base}", detail,
        )
    if state == "degraded":   # a signal errored, but the cycle ran
        return CheckResult(
            "forward_cycle", WARNING, f"forward signal error ({fsum.detail}) | {base}", detail,
        )
    if state == "stale":
        if latest is None:    # empty run_log — the cycle has never run
            return CheckResult(
                "forward_cycle", CRITICAL, f"forward cycle has never run | {base}", detail,
            )
        if days is not None and days >= FORWARD_CRIT_DAYS:
            return CheckResult(
                "forward_cycle", CRITICAL, f"forward cycle stale | {base}", detail,
            )
        return CheckResult(
            "forward_cycle", WARNING, f"forward cycle stale | {base}", detail,
        )
    return CheckResult("forward_cycle", OK, f"forward cycle fresh | {base}", detail)


# ---------------------------------------------------------------------------
# Alert formatting
# ---------------------------------------------------------------------------

def _format_alert(results: List[CheckResult], hostname: str, overall: str) -> str:
    lines = [f"[{overall}] BTC Bot Health — {hostname}"]
    for r in results:
        if _RANK[r.severity] >= _RANK[WARNING]:
            lines.append(f"  [{r.severity}] {r.name}: {r.message}")
    return "\n".join(lines)


def _send_telegram(text: str) -> None:
    bot_token  = _load_env("TELEGRAM_BOT_TOKEN")
    channel_id = _load_env("TELEGRAM_CHANNEL_ID")
    if not bot_token or not channel_id:
        print(
            "[healthcheck] TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID not set — "
            "skipping alert",
            file=sys.stderr,
        )
        return
    try:
        from src.alerts.telegram import send_message
        send_message(bot_token, channel_id, text)
    except Exception as exc:
        print(f"[healthcheck] telegram send failed: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="BTC bot health check")
    parser.add_argument(
        "--alert-on-warning", action="store_true",
        help="Send Telegram alert on WARNING too (default: CRITICAL only)",
    )
    parser.add_argument(
        "--no-telegram", action="store_true",
        help="Skip Telegram alerts; print summary to stdout only",
    )
    parser.add_argument(
        "--full-integrity", action="store_true",
        help="Run PRAGMA integrity_check (expensive — seconds for large DB)",
    )
    parser.add_argument(
        "--db-path", type=Path, default=ROOT / "data" / "btc_trading.db",
    )
    parser.add_argument(
        "--ledger-backup-dir", type=Path,
        default=ROOT / "data" / "ledger-backups",
        help="Directory holding ledger_*.db backups (scripts/backup.py --ledger)",
    )
    parser.add_argument(
        "--label-store-backup-dir", type=Path,
        default=ROOT / "data" / "label-store-backups",
        help="Directory holding label_store_*.db backups (scripts/backup.py --label-store)",
    )
    parser.add_argument(
        "--forward-db-path", type=Path,
        default=ROOT / "research" / "data" / "evidence_forward.db",
        help="Path to evidence_forward.db for the D3a forward-cycle dead-man",
    )
    parser.add_argument(
        "--no-forward-check", action="store_true",
        help="Skip the forward-cycle dead-man check",
    )
    parser.add_argument("--service-name", default="btcbot")
    args = parser.parse_args()

    db_path    = args.db_path
    backup_dir = db_path.parent / "backups"

    results: List[CheckResult] = [
        check_service(args.service_name),
        check_disk(db_path.parent),
        check_db_liveness(db_path),
        check_wal(db_path),
        check_backup_freshness(backup_dir),
        check_backup_freshness(
            args.ledger_backup_dir, label="ledger", check_name="ledger_backup",
            required=False,
        ),
        check_backup_freshness(
            args.label_store_backup_dir, label="label_store",
            check_name="label_store_backup", required=False,
        ),
        check_offhost_freshness(backup_dir, check_name="offhost_backup"),
        check_offhost_freshness(
            args.ledger_backup_dir, check_name="ledger_offhost_backup"
        ),
    ]
    if not args.no_forward_check:
        results.append(check_forward_cycle(args.forward_db_path))
    if args.full_integrity:
        results.append(check_integrity(db_path))

    # Overall severity = worst individual check
    overall = OK
    for r in results:
        if _RANK[r.severity] > _RANK[overall]:
            overall = r.severity

    # JSON summary to stdout (machine-readable; captured by systemd journal)
    summary = {
        "ts":      datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "overall": overall,
        "checks":  [
            {"name": r.name, "severity": r.severity, "message": r.message,
             **r.detail}
            for r in results
        ],
    }
    print(json.dumps(summary, indent=2))

    # Telegram alert
    should_alert = (
        overall == CRITICAL
        or (overall == WARNING and args.alert_on_warning)
    )
    if should_alert and not args.no_telegram:
        import socket
        hostname   = socket.gethostname()
        alert_text = _format_alert(results, hostname, overall)
        _send_telegram(alert_text)

    if overall == CRITICAL:
        return 2
    if overall == WARNING:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
