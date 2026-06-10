"""
BTC Paper Trading Bot --Server Deployment Report

Checks service health, timer schedules, last evidence run, last backup,
and evidence DB row counts. Produces a PASS / FAIL verdict for each item.

Run on the target server:
    python scripts/deployment_report.py

Also safe to run on Windows dev (systemd items marked NOT TESTED).
"""
from __future__ import annotations

import glob
import hashlib
import json
import pathlib
import platform
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent

# ── colours (disabled on Windows) ───────────────────────────────────────────
_COLOUR = sys.stdout.isatty() and platform.system() != "Windows"
_G = "\033[32m" if _COLOUR else ""
_Y = "\033[33m" if _COLOUR else ""
_R = "\033[31m" if _COLOUR else ""
_B = "\033[1m"  if _COLOUR else ""
_N = "\033[0m"  if _COLOUR else ""

PASS_  = f"{_G}PASS{_N}"
WARN_  = f"{_Y}WARN{_N}"
FAIL_  = f"{_R}FAIL{_N}"
NT_    = "NOT TESTED"

_issues: list[tuple[str, str]] = []   # (severity, message)


def _p(label: str, verdict: str, detail: str = "") -> None:
    pad = max(0, 42 - len(label))
    suffix = f"  {detail}" if detail else ""
    print(f"  {label}{' ' * pad}{verdict}{suffix}")


def _fail(label: str, detail: str) -> None:
    _issues.append(("FAIL", f"{label}: {detail}"))
    _p(label, FAIL_, detail)


def _warn(label: str, detail: str) -> None:
    _issues.append(("WARN", f"{label}: {detail}"))
    _p(label, WARN_, detail)


def _pass(label: str, detail: str = "") -> None:
    _p(label, PASS_, detail)


def _section(title: str) -> None:
    print(f"\n{_B}{'-'*62}{_N}")
    print(f"{_B}  {title}{_N}")
    print(f"{'-'*62}")


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _ts_to_str(ts_ms: int | None) -> str:
    if ts_ms is None:
        return "—"
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ── systemd helpers ─────────────────────────────────────────────────────────

def _systemctl(*args: str) -> tuple[int, str]:
    """Run systemctl and return (returncode, stdout). Returns (-1, '') if not available."""
    if not shutil.which("systemctl"):
        return -1, ""
    try:
        r = subprocess.run(
            ["systemctl", *args], capture_output=True, text=True, timeout=10
        )
        return r.returncode, r.stdout.strip()
    except Exception as exc:
        return -1, str(exc)


def check_services() -> None:
    _section("1. SERVICE STATUS")
    services = [
        ("btcbot.service",              "Layer 2 trading runtime"),
        ("btcbot-evidence.service",     "Phase E evidence (oneshot --inactive when idle)"),
        ("btcbot-backup.service",       "Backup (oneshot --inactive when idle)"),
        ("btcbot-healthcheck.service",  "Healthcheck (oneshot --inactive when idle)"),
    ]
    for svc, desc in services:
        rc, out = _systemctl("is-active", svc)
        if rc == -1:
            _p(svc, NT_, desc)
            continue
        state = out or "unknown"
        if svc == "btcbot.service":
            if state == "active":
                _pass(svc, f"{state}  ({desc})")
            else:
                _fail(svc, f"{state} --expected active ({desc})")
        else:
            # oneshot services are "inactive" between runs --that is correct
            if state in ("inactive", "active"):
                _pass(svc, f"{state}  ({desc})")
            elif state == "failed":
                _fail(svc, f"{state} --last run exited non-zero ({desc})")
            else:
                _warn(svc, f"{state} ({desc})")


def check_timers() -> None:
    _section("2. TIMER STATUS")
    timers = [
        ("btcbot-evidence.timer",    "*-*-* 00:05:00 UTC"),
        ("btcbot-backup.timer",      "*-*-* 02:00:00 UTC"),
        ("btcbot-healthcheck.timer", "*-*-* *:0/10 UTC"),
    ]
    rc, out = _systemctl("list-timers", "--no-pager", "--all")
    if rc == -1:
        for name, expected in timers:
            _p(name, NT_, f"expected: {expected}")
        return

    for name, expected in timers:
        if name in out:
            # Extract the next-fire line for this timer
            line = next((l for l in out.splitlines() if name in l), "")
            parts = line.split()
            next_fire = " ".join(parts[:2]) if len(parts) >= 2 else "?"
            _pass(name, f"next={next_fire}  spec={expected}")
        else:
            _fail(name, f"not found in systemctl list-timers (is it enabled?)")


def check_evidence_run() -> None:
    _section("3. LAST EVIDENCE RUN (run_log)")
    db_path = ROOT / "research" / "data" / "forward_evidence.db"
    if not db_path.exists():
        _fail("forward_evidence.db", "not found --run init_db.py + daily_job.py --backfill")
        return

    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row

    # Most recent run_log entry
    row = con.execute(
        "SELECT run_ts, data_day_ts, status, fn_hash, corpus_rows_loaded, anomalies_json "
        "FROM run_log ORDER BY run_ts DESC LIMIT 1"
    ).fetchone()

    if row is None:
        _fail("run_log", "no entries --daily_job.py has never run")
        con.close()
        return

    run_time  = _ts_to_str(int(row["run_ts"]))
    data_day  = _ts_to_str(int(row["data_day_ts"]))
    status    = row["status"]
    fn_hash   = (row["fn_hash"] or "")[:16]
    anomalies = json.loads(row["anomalies_json"] or "[]")

    label = "last run"
    detail = f"{run_time}  data_day={data_day}  status={status}  fn_hash={fn_hash}..."
    if status == "ok" and not anomalies:
        _pass(label, detail)
    elif status == "ok":
        _warn(label, f"{detail}  anomalies={anomalies}")
    else:
        _fail(label, detail)

    # Age check --warn if last run > 30h ago (missed a day)
    now_ms  = int(datetime.now(timezone.utc).timestamp() * 1000)
    age_h   = (now_ms - int(row["run_ts"])) / 3_600_000
    age_label = "run age"
    if age_h <= 26:
        _pass(age_label, f"{age_h:.1f}h ago")
    elif age_h <= 50:
        _warn(age_label, f"{age_h:.1f}h ago (>26h --possible missed run)")
    else:
        _fail(age_label, f"{age_h:.1f}h ago (>50h --evidence gap likely)")

    # fn_hash verification
    fn_file   = ROOT / "research" / "forward_evidence" / "decision_function.py"
    pre_reg   = ROOT / "research" / "forward_evidence" / "pre_registration.json"
    hash_label = "fn_hash vs pre_registration"
    if fn_file.exists() and pre_reg.exists():
        actual = hashlib.sha256(fn_file.read_bytes()).hexdigest()
        sealed = json.loads(pre_reg.read_text())["decision_function_hash"]
        if actual == sealed:
            _pass(hash_label, f"{actual[:16]}...  match")
        else:
            _fail(hash_label, f"MISMATCH --sealed={sealed[:16]}  actual={actual[:16]}")
    else:
        _warn(hash_label, "pre_registration.json or decision_function.py not found")

    con.close()


def check_backups() -> None:
    _section("4. LAST BACKUP RUN")
    specs = [
        ("evidence",    ROOT / "data" / "evidence-backups"),
        ("btc_trading", ROOT / "data" / "backups"),
    ]
    for label, backup_dir in specs:
        manifests = sorted(glob.glob(str(backup_dir / f"{label}_*.manifest.json")))
        if not manifests:
            _fail(f"{label} backup", f"no manifests found in {backup_dir}")
            continue

        mf = pathlib.Path(manifests[-1])
        m  = json.loads(mf.read_text())
        ts = m.get("backup_ts", "?")[:19]

        # Age
        try:
            bkp_dt  = datetime.fromisoformat(m["backup_ts"]).astimezone(timezone.utc)
            age_h   = (datetime.now(timezone.utc) - bkp_dt).total_seconds() / 3600
        except Exception:
            age_h   = float("inf")

        # SHA-256 integrity
        bkp_file = pathlib.Path(m.get("backup_file", ""))
        if bkp_file.exists():
            actual_sha = hashlib.sha256(bkp_file.read_bytes()).hexdigest()
            sha_ok = actual_sha == m.get("sha256")
        else:
            sha_ok = False

        detail = f"{ts} UTC  age={age_h:.1f}h  sha256={'ok' if sha_ok else 'MISMATCH'}  size={m.get('file_size_bytes',0)//1024}KB"
        if age_h <= 26 and sha_ok:
            _pass(f"{label} backup", detail)
        elif age_h <= 50 and sha_ok:
            _warn(f"{label} backup", f"{detail}  (>26h old)")
        elif not sha_ok:
            _fail(f"{label} backup", f"{detail}  SHA-256 MISMATCH --backup may be corrupt")
        else:
            _fail(f"{label} backup", f"{detail}  (>{50}h old --missed rotation)")


def check_db_rows() -> None:
    _section("5. EVIDENCE DB ROW COUNTS")
    db_path = ROOT / "research" / "data" / "forward_evidence.db"
    if not db_path.exists():
        _fail("forward_evidence.db", "not found")
        return

    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row

    tables = {
        "corpus_daily":  ("days of market data",   1),
        "regime_states": ("days of regime labels",  1),
        "signal_log":    ("days of H6 decisions",   1),
        "trade_entries": ("trade entries",          0),
        "trade_exits":   ("matched trade exits",    0),
        "trade_labels":  ("forward label rows",     0),
        "run_log":       ("daily job runs",         1),
    }
    all_ok = True
    for table, (desc, min_rows) in tables.items():
        n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        label = f"{table}"
        ok = n >= min_rows
        if not ok:
            all_ok = False
        verdict = PASS_ if ok else FAIL_
        _p(label, verdict, f"{n:,}  ({desc})")

    # Integrity
    ic = con.execute("PRAGMA integrity_check").fetchone()[0]
    _pass("integrity_check", ic) if ic == "ok" else _fail("integrity_check", ic)

    # FK violations
    fk = con.execute("PRAGMA foreign_key_check").fetchall()
    if fk:
        _fail("FK violations", str(len(fk)))
    else:
        _pass("FK violations", "0")

    # Forward evidence trades (post-registration)
    pre_reg_path = ROOT / "research" / "forward_evidence" / "pre_registration.json"
    if pre_reg_path.exists():
        reg = json.loads(pre_reg_path.read_text())
        reg_date = reg.get("pre_registration_date", "")[:10]
        pre_reg_ts = int(datetime.fromisoformat(
            reg.get("pre_registration_date", "2026-01-01T00:00:00+00:00")
        ).timestamp() * 1000)
        n_fwd = con.execute(
            "SELECT COUNT(*) FROM trade_entries WHERE entry_day_ts >= ?", (pre_reg_ts,)
        ).fetchone()[0]
        n_fwd_closed = con.execute(
            """SELECT COUNT(*) FROM trade_entries te
               JOIN trade_exits tx ON te.trade_id = tx.trade_id
               WHERE te.entry_day_ts >= ?""", (pre_reg_ts,)
        ).fetchone()[0]
        _pass("forward trades", f"{n_fwd} opened  {n_fwd_closed} closed  (since {reg_date})")

    con.close()


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    width = 64
    print(f"\n{'='*width}")
    print(f"{_B}  BTC PAPER TRADING BOT -- SERVER DEPLOYMENT REPORT{_N}")
    print(f"  Generated: {_now_utc()}")
    print(f"  Host:      {platform.node()}  ({platform.system()} {platform.release()})")
    print(f"{'='*width}")

    check_services()
    check_timers()
    check_evidence_run()
    check_backups()
    check_db_rows()

    # ── verdict ──────────────────────────────────────────────────────────────
    _section("VERDICT")
    fails = [m for sev, m in _issues if sev == "FAIL"]
    warns = [m for sev, m in _issues if sev == "WARN"]

    if fails:
        print(f"  {_R}{_B}FAIL{_N}  --{len(fails)} blocking issue(s):")
        for m in fails:
            print(f"    x  {m}")
        if warns:
            print(f"\n  {_Y}WARN{_N}  -- {len(warns)} warning(s):")
            for m in warns:
                print(f"    !  {m}")
        print()
        return 1

    if warns:
        print(f"  {_Y}{_B}PASS WITH WARNINGS{_N}  --{len(warns)} warning(s):")
        for m in warns:
            print(f"    !  {m}")
        print()
        return 0

    print(f"  {_G}{_B}PASS{_N}  --all checks clear")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
