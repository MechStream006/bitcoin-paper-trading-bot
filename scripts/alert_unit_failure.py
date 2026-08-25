#!/usr/bin/env python3
"""Escalate a failed systemd unit to Telegram. Invoked via OnFailure=.

Gate 5C.26 established the gap this closes: btcbot-fwd-hg.service exited
status=1/FAILURE on 19 consecutive days (2026-07-19 .. 2026-08-06) while the
timer itself stayed healthy and `systemctl list-timers` looked normal. The
failure was written loudly to the journal every single day and nothing was
listening -- no unit in the project carried OnFailure=, and no notifier unit
existed.

Deliberately NOT a retry: the forward-evidence job aborts fail-closed on a
governance pre-check failure, and that refusal is correct. Re-running it would
either fail identically or, worse, write evidence against a registry that has
already been judged invalid. The only thing missing was somebody being told.

Reuses the Telegram convention scripts/healthcheck.py already uses -- same
env keys, same .env fallback, same send_message -- so there is one alert path
in this project rather than two that can drift.

Exit status is always 0. A notifier that fails the unit it is reporting on
would turn one incident into two.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Journal lines to include so the alert says WHY, not merely THAT.
JOURNAL_LINES = 15


def _load_env(key: str) -> str:
    """Same resolution order as healthcheck.py: environment, then .env."""
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


def unit_status(unit: str) -> dict:
    """Best-effort systemd properties for the failed unit."""
    props = ("Result", "ExecMainStatus", "ExecMainExitTimestamp", "ActiveState")
    out: dict = {}
    try:
        raw = subprocess.run(
            ["systemctl", "show", unit, *(f"-p{p}" for p in props)],
            capture_output=True, text=True, timeout=10,
        ).stdout
        for line in raw.splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                out[k] = v
    except Exception as exc:                      # systemd absent, or a test host
        out["_error"] = f"{type(exc).__name__}: {exc}"
    return out


def journal_tail(unit: str, lines: int = JOURNAL_LINES) -> str:
    """The last journal lines for the unit -- the actual cause, not a guess."""
    try:
        return subprocess.run(
            ["journalctl", "-u", unit, "-n", str(lines), "--no-pager", "-o", "cat"],
            capture_output=True, text=True, timeout=15,
        ).stdout.strip()
    except Exception as exc:
        return f"(journal unavailable: {type(exc).__name__}: {exc})"


def build_message(unit: str, status: dict, tail: str) -> str:
    # ASCII only. This text is printed to stdout whenever Telegram is not
    # configured, and a non-UTF-8 console (cp1252, observed) raises
    # UnicodeEncodeError on an emoji -- crashing the one component whose
    # entire job is to not fail. Visibility does not require a pictograph.
    head = (
        f"[ALERT] UNIT FAILED: {unit}\n"
        f"result={status.get('Result', '?')} "
        f"exit={status.get('ExecMainStatus', '?')} "
        f"at={status.get('ExecMainExitTimestamp', '?')}\n"
    )
    if tail:
        head += f"\nlast {JOURNAL_LINES} journal lines:\n{tail}"
    # Telegram rejects messages over 4096 characters; truncate the tail, never
    # the header, so the unit name and exit status always survive.
    if len(head) > 3900:
        head = head[:3900] + "\n... (truncated)"
    return head


def send(text: str) -> bool:
    bot_token = _load_env("TELEGRAM_BOT_TOKEN")
    channel_id = _load_env("TELEGRAM_CHANNEL_ID")
    if not bot_token or not channel_id:
        print("[alert] TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID not set - "
              "printing instead of sending", file=sys.stderr)
        print(text)
        return False
    try:
        sys.path.insert(0, str(ROOT))
        from src.alerts.telegram import send_message
        send_message(bot_token, channel_id, text)
        return True
    except Exception as exc:
        print(f"[alert] telegram send failed: {exc}", file=sys.stderr)
        print(text)
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("unit", help="failed unit name, normally passed as %%n")
    ap.add_argument("--dry-run", action="store_true",
                    help="build and print the message, send nothing")
    args = ap.parse_args(argv)

    status = unit_status(args.unit)
    tail = journal_tail(args.unit)
    text = build_message(args.unit, status, tail)

    if args.dry_run:
        print(text)
        return 0
    send(text)
    # Always 0: a notifier that fails the unit it reports on turns one incident
    # into two, and OnFailure= chains would then alert about the alerter.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
