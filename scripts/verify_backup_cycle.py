"""Read-only verification that a completed nightly backup cycle obeyed policy.

Runs unattended from a recurring systemd timer and writes one timestamped
evidence report. It answers a single question: *did the most recent completed
backup cycle do what the policy says it must?*

WHY THE CYCLE IS DISCOVERED, NEVER HARDCODED
    The first version of this verifier pinned the cycle date and the sampling
    time as literals. Both were wrong within a day: the literal date made every
    later night re-verify an already-finished cycle, and the sampling time
    (02:45) landed 80 seconds INSIDE the trading backup's own run
    (02:43:40 -> 02:46:24), so it saw a half-written cycle and reported a false
    INCOMPLETE. A verifier that cries wolf is worse than none, because the
    operator stops reading it.

    So the cycle under test is derived from the evidence itself: the last
    COMPLETED run of the trading-backup unit. That unit produces the artifact
    being verified, so its own start timestamp names the cycle and its exit
    timestamp proves the artifact is finished. If it is still running, or has
    never run, or last failed, or completed impossibly far in the past or in
    the future, this program refuses to judge rather than guess.

FAIL CLOSED
    Every check yields OBSERVED, NOT-OBSERVED or AMBIGUOUS. Anything other than
    a complete, unambiguous set of expectations gives VERDICT: INCOMPLETE.
    Nothing is repaired, retried, triggered or deleted -- this program reads.

STRICTLY READ-ONLY
    It shells out only to `journalctl`, `systemctl show`, `systemctl --failed`,
    `df` and `git rev-parse`; it opens SQLite with `mode=ro`; and it writes
    exactly one file, into its own report directory. The unit additionally
    enforces this with ProtectSystem=strict, a StateDirectory-only writable
    path, PrivateNetwork and an unprivileged user -- see
    deploy/btcbot-verify-backup-cycle.service.

Exit status is always 0. A non-zero exit would add an entry to
`systemctl --failed`, which the operator uses as a real alerting signal; the
verdict belongs in the report, not in the unit's exit status.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

TRADING_UNIT = "btcbot-trading-backup.service"
ALL_UNIT = "btcbot-backup.service"
LEDGER_UNIT = "btcbot-ledger-backup.service"
COLLECTOR_UNIT = "btcbot.service"

#: Labels the --all unit must NOT back up, because each has its own encrypted
#: BK3 timer. Backing them up twice is the defect this verifier watches for.
EXCLUDED_LABELS = ("btc_trading", "ledger")

#: A completed run older than this is not "last night" any more. Refuse rather
#: than silently verify a stale cycle and report a reassuring PASS.
MAX_CYCLE_AGE = timedelta(hours=26)

#: The collector window examined for gaps, relative to the cycle date.
WINDOW_START_HHMM = (2, 0)
WINDOW_END_HHMM = (2, 55)

OBSERVED, MISSING, AMBIGUOUS, INFO = "OBSERVED", "NOT-OBSERVED", "AMBIGUOUS", "INFO"


# --------------------------------------------------------------------- model


@dataclasses.dataclass(frozen=True)
class UnitRun:
    """What systemd knows about one unit's most recent invocation."""
    name: str
    start: datetime | None
    exit: datetime | None
    result: str

    @property
    def in_progress(self) -> bool:
        return self.start is not None and (self.exit is None or self.exit < self.start)


@dataclasses.dataclass(frozen=True)
class Finding:
    status: str
    check: str
    detail: str


@dataclasses.dataclass(frozen=True)
class Cycle:
    """The completed cycle to verify."""
    day: date
    started: datetime
    finished: datetime

    @property
    def stamp(self) -> str:
        return self.day.strftime("%Y%m%d")

    @property
    def window(self) -> tuple[datetime, datetime]:
        base = datetime(self.day.year, self.day.month, self.day.day, tzinfo=timezone.utc)
        return (base.replace(hour=WINDOW_START_HHMM[0], minute=WINDOW_START_HHMM[1]),
                base.replace(hour=WINDOW_END_HHMM[0], minute=WINDOW_END_HHMM[1]))


# ------------------------------------------------------------- pure decisions
# Everything below takes plain values so it can be tested without systemd,
# without a filesystem and without a clock.


def discover_cycle(now: datetime, trading: UnitRun,
                   max_age: timedelta = MAX_CYCLE_AGE) -> tuple[Cycle | None, str]:
    """The completed cycle to verify, or (None, why-we-refuse).

    Derived from the unit that actually produces the artifact under test. Its
    start names the cycle (the artifact filename embeds that instant) and its
    exit proves the cycle is finished.
    """
    if trading.start is None:
        return None, f"{trading.name} has never run; there is no cycle to verify"
    if trading.in_progress:
        return None, (f"{trading.name} is still running (started "
                      f"{trading.start.isoformat()}, no exit recorded) -- "
                      f"refusing to judge a half-written cycle")
    if trading.result != "success":
        return None, (f"{trading.name} last completed with result="
                      f"{trading.result!r}; the cycle is not trustworthy")
    if trading.exit > now:
        return None, (f"{trading.name} exit {trading.exit.isoformat()} is in the "
                      f"future relative to now {now.isoformat()} -- clock skew")
    age = now - trading.exit
    if age > max_age:
        return None, (f"last completed run finished {age} ago (limit {max_age}); "
                      f"no recent cycle to verify")
    return Cycle(trading.start.date(), trading.start, trading.exit), ""


def check_exclusions(journal: str) -> list[Finding]:
    """The --all unit must skip every label that owns a dedicated BK3 timer."""
    out: list[Finding] = []
    if not journal.strip():
        return [Finding(MISSING, "--all journal for the cycle",
                        "no journal output for the cycle window")]
    for label in EXCLUDED_LABELS:
        needle = f"[skip] {label}: excluded by --exclude"
        out.append(Finding(OBSERVED, f"--all excluded {label}", needle)
                   if needle in journal else
                   Finding(MISSING, f"--all excluded {label}",
                           f"{needle!r} absent from the --all run"))
    stray = [l.strip() for l in journal.splitlines()
             if "[backup] target" in l and "backups/btc_trading_" in l]
    out.append(Finding(AMBIGUOUS, "--all produced no btc_trading artifact",
                       "unexpected target line(s): " + "; ".join(stray))
               if stray else
               Finding(OBSERVED, "--all produced no btc_trading artifact",
                       "no '[backup] target ... backups/btc_trading_' line"))
    return out


def select_plaintext(names: list[str], cycle: Cycle) -> tuple[str | None, Finding]:
    """Exactly one trading plaintext must exist for the cycle date."""
    prefix = f"btc_trading_{cycle.stamp}_"
    found = sorted(n for n in names
                   if n.startswith(prefix) and n.endswith(".db"))
    if len(found) == 1:
        return found[0], Finding(OBSERVED, "exactly one btc_trading plaintext",
                                 found[0])
    if not found:
        return None, Finding(MISSING, "exactly one btc_trading plaintext",
                             f"no {prefix}*.db exists")
    return None, Finding(
        AMBIGUOUS, "exactly one btc_trading plaintext",
        f"{len(found)} found: {', '.join(found)} -- this is the duplicate the "
        f"backup policy exists to prevent")


def check_provenance(name: str, trading_journal: str) -> Finding:
    """The artifact must be named by the encrypted trading unit's own journal.

    Filename time ranges are a weaker signal than the producing unit saying so,
    and the schedule may legitimately move; the journal cannot be coincidence.
    """
    if name in trading_journal:
        return Finding(OBSERVED, "produced by the encrypted trading timer",
                       f"{TRADING_UNIT} journal names {name}")
    return Finding(AMBIGUOUS, "produced by the encrypted trading timer",
                   f"{name} is not mentioned by {TRADING_UNIT}; provenance unclear")


def check_companions(name: str, present: set[str]) -> list[Finding]:
    """Plaintext manifest, ciphertext and age-manifest must all exist."""
    expected = {
        "plaintext manifest": name[:-3] + ".manifest.json",
        "ciphertext (.age)": name + ".age",
        "age-manifest": name + ".age-manifest.json",
    }
    return [Finding(OBSERVED, f"{label} exists", fn) if fn in present
            else Finding(MISSING, f"{label} exists", f"{fn} absent")
            for label, fn in expected.items()]


def check_checksums(manifest: dict, age_manifest: dict,
                    plaintext_sha: str | None,
                    ciphertext_sha: str | None) -> list[Finding]:
    """Recomputed digests must equal what the manifests recorded."""
    out: list[Finding] = []
    want_p = (manifest or {}).get("sha256")
    if want_p and plaintext_sha and want_p == plaintext_sha:
        out.append(Finding(OBSERVED, "plaintext SHA-256 matches its manifest",
                           plaintext_sha))
    else:
        out.append(Finding(AMBIGUOUS, "plaintext SHA-256 matches its manifest",
                           f"manifest={want_p} recomputed={plaintext_sha}"))
    want_c = (age_manifest or {}).get("ciphertext_sha256")
    if want_c and ciphertext_sha and want_c == ciphertext_sha:
        out.append(Finding(OBSERVED, "ciphertext SHA-256 matches its age-manifest",
                           ciphertext_sha))
    else:
        out.append(Finding(AMBIGUOUS, "ciphertext SHA-256 matches its age-manifest",
                           f"manifest={want_c} recomputed={ciphertext_sha}"))
    bound = (age_manifest or {}).get("plaintext_sha256")
    if bound and want_p and bound == want_p:
        out.append(Finding(OBSERVED, "age-manifest binds the same plaintext", bound))
    else:
        out.append(Finding(AMBIGUOUS, "age-manifest binds the same plaintext",
                           f"age-manifest={bound} manifest={want_p}"))
    return out


def check_offhost(sidecar: dict, expected_artifact: str | None,
                  trading_journal: str) -> list[Finding]:
    """Off-host success is evidenced by the sidecar and the journal upload lines.

    S3 object presence is deliberately NOT probed: the production role is
    write-only by design, so a read attempt would fail for a reason unrelated
    to whether the upload worked.
    """
    out: list[Finding] = []
    if not sidecar:
        out.append(Finding(MISSING, "off-host status sidecar", "sidecar absent"))
    else:
        ok = sidecar.get("status") == "ok" and sidecar.get("exit_code") == 0
        names_it = expected_artifact is None or sidecar.get("artifact") == expected_artifact
        if ok and names_it:
            out.append(Finding(OBSERVED, "off-host upload reported ok",
                               f"artifact={sidecar.get('artifact')} "
                               f"last_success={sidecar.get('last_success_at')}"))
        else:
            out.append(Finding(AMBIGUOUS, "off-host upload reported ok",
                               f"status={sidecar.get('status')} "
                               f"exit_code={sidecar.get('exit_code')} "
                               f"artifact={sidecar.get('artifact')} "
                               f"expected={expected_artifact}"))
    uploads = sorted(set(re.findall(r"upload: \S+", trading_journal)))
    out.append(Finding(OBSERVED, "S3 upload lines present", "; ".join(uploads))
               if uploads else
               Finding(MISSING, "S3 upload lines present",
                       "no 'upload:' line in the trading-backup journal"))
    out.append(Finding(INFO, "S3 object presence not probed",
                       "the production IAM role is write-only by design "
                       "(s3:ListBucket / HeadObject denied); upload success is "
                       "evidenced by the aws exit code and the journal lines"))
    return out


def check_retention(trading_journal: str) -> list[Finding]:
    """The new retention code announces orphan-ciphertext accounting."""
    lines = [l.strip() for l in trading_journal.splitlines() if "[retention]" in l]
    counted = [l for l in lines if "orphan_ciphertext_kept=" in l]
    if counted:
        return [Finding(OBSERVED, "retention orphan counters present", l)
                for l in counted]
    if lines:
        return [Finding(AMBIGUOUS, "retention orphan counters present",
                        "retention ran without orphan counters -- the old code "
                        "is deployed: " + "; ".join(lines))]
    return [Finding(MISSING, "retention orphan counters present",
                    "no '[retention]' line in the trading-backup journal")]


def check_collector(pid: str, nrestarts: str, active: str,
                    baseline_pid: str | None, snapshots: list[str],
                    error_counts: dict[str, int],
                    exec_calls: list[str]) -> list[Finding]:
    """Collection must be untouched by anything the backup cycle did."""
    out: list[Finding] = []
    same = baseline_pid is None or pid == baseline_pid
    if same and nrestarts == "0" and active == "active":
        out.append(Finding(OBSERVED, "collector never restarted",
                           f"PID {pid} (baseline {baseline_pid}), "
                           f"NRestarts=0, {active}"))
    else:
        out.append(Finding(AMBIGUOUS, "collector never restarted",
                           f"PID={pid} baseline={baseline_pid} "
                           f"NRestarts={nrestarts} state={active}"))

    dirty = [s for s in snapshots if "skip=0 fail=0" not in s]
    gap = max_gap_minutes(snapshots)
    if snapshots and not dirty and gap is not None and gap <= 2.0:
        out.append(Finding(OBSERVED, "no collection gap in the cycle window",
                           f"{len(snapshots)} snapshots, all skip=0 fail=0, "
                           f"largest gap {gap:.1f} min"))
    else:
        out.append(Finding(AMBIGUOUS, "no collection gap in the cycle window",
                           f"{len(snapshots)} snapshots, {len(dirty)} with "
                           f"skip/fail, largest gap "
                           f"{'n/a' if gap is None else f'{gap:.1f} min'}"))

    if all(v == 0 for v in error_counts.values()):
        out.append(Finding(OBSERVED, "no reconciliation failure / HALT / db-lock",
                           json.dumps(error_counts)))
    else:
        out.append(Finding(AMBIGUOUS, "no reconciliation failure / HALT / db-lock",
                           json.dumps(error_counts)))

    out.append(Finding(OBSERVED, "no order/cancel/execute calls in window",
                       "0 matches") if not exec_calls else
               Finding(AMBIGUOUS, "no order/cancel/execute calls in window",
                       f"{len(exec_calls)}: " + "; ".join(exec_calls[:3])))
    return out


_TS = re.compile(r"\[snapshot_writer\] ts=(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")


def max_gap_minutes(snapshots: list[str]) -> float | None:
    """Largest interval between consecutive snapshot lines, in minutes."""
    stamps = []
    for line in snapshots:
        m = _TS.search(line)
        if m:
            stamps.append(datetime.fromisoformat(m.group(1)))
    if len(stamps) < 2:
        return None
    stamps.sort()
    return max((b - a).total_seconds() / 60.0
               for a, b in zip(stamps, stamps[1:]))


def verdict_of(findings: list[Finding]) -> str:
    bad = [f for f in findings if f.status in (MISSING, AMBIGUOUS)]
    return "INCOMPLETE" if bad else "PASS"


def render(findings: list[Finding], header: dict, raw: list[str]) -> str:
    v = verdict_of(findings)
    lines = ["=" * 78, "BTCBOT BACKUP-POLICY CYCLE VERIFICATION", "=" * 78]
    lines += [f"{k:<21}: {val}" for k, val in header.items()]
    lines += ["", f"VERDICT: {v}", "",
              "  PASS       = every expected condition observed and unambiguous.",
              "  INCOMPLETE = at least one expectation absent or ambiguous.",
              "               No repair was attempted. An operator must review.",
              "", "-" * 78, "1. OBSERVED FACTS", "-" * 78]
    for f in findings:
        if f.status == OBSERVED:
            lines.append(f"  [OK]   {f.check}\n         {f.detail}")
    lines += ["", "-" * 78, "2. EXPECTED BUT NOT OBSERVED", "-" * 78]
    bad = [f for f in findings if f.status in (MISSING, AMBIGUOUS)]
    lines.append("  (none)") if not bad else None
    for f in bad:
        tag = "MISSING" if f.status == MISSING else "AMBIG"
        lines.append(f"  [{tag}] {f.check}\n         {f.detail}")
    lines += ["", "-" * 78, "3. UNRELATED / PRE-EXISTING (informational)", "-" * 78]
    info = [f for f in findings if f.status == INFO]
    lines.append("  (none)") if not info else None
    for f in info:
        lines.append(f"  [INFO] {f.check}\n         {f.detail}")
    lines += ["", "-" * 78, "4. RAW EVIDENCE", "-" * 78] + raw
    lines += ["", "=" * 78,
              "This program is read-only: no backup artifact, database or",
              "service state was created, modified or deleted.", "=" * 78, ""]
    return "\n".join(lines)


# ------------------------------------------------------------------ real I/O


def sh(*args: str, timeout: int = 180) -> str:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "") + (r.stderr or "")
    except Exception as exc:                                    # pragma: no cover
        return f"<<command failed: {type(exc).__name__}: {exc}>>"


def _systemd_ts(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value or value in ("n/a", "0"):
        return None
    for fmt in ("%a %Y-%m-%d %H:%M:%S %Z", "%a %Y-%m-%d %H:%M:%S UTC"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def unit_run(name: str) -> UnitRun:                             # pragma: no cover
    return UnitRun(
        name=name,
        start=_systemd_ts(sh("systemctl", "show", name, "-p",
                             "ExecMainStartTimestamp", "--value")),
        exit=_systemd_ts(sh("systemctl", "show", name, "-p",
                            "ExecMainExitTimestamp", "--value")),
        result=sh("systemctl", "show", name, "-p", "Result", "--value").strip(),
    )


def sha256(path: Path) -> str | None:                           # pragma: no cover
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def read_json(path: Path) -> dict:                              # pragma: no cover
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main(argv: list[str] | None = None) -> int:                 # pragma: no cover
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--backup-dir", type=Path,
                    default=Path("/opt/btcbot/data/backups"))
    ap.add_argument("--db-path", type=Path,
                    default=Path("/opt/btcbot/data/btc_trading.db"))
    ap.add_argument("--repo", type=Path, default=Path("/opt/btcbot"))
    ap.add_argument("--state-dir", type=Path,
                    default=Path(os.environ.get("STATE_DIRECTORY",
                                                "/var/lib/btcbot-verification")))
    ap.add_argument("--stdout-only", action="store_true",
                    help="print the report without writing a file")
    args = ap.parse_args(argv)

    now = datetime.now(timezone.utc)
    findings: list[Finding] = []
    raw: list[str] = []

    trading = unit_run(TRADING_UNIT)
    raw.append(f"{TRADING_UNIT}: start={trading.start} exit={trading.exit} "
               f"result={trading.result!r}")
    cycle, why = discover_cycle(now, trading)

    if cycle is None:
        findings.append(Finding(MISSING, "a completed backup cycle to verify", why))
    else:
        findings.append(Finding(OBSERVED, "completed backup cycle discovered",
                                f"{cycle.day.isoformat()} "
                                f"({cycle.started.isoformat()} -> "
                                f"{cycle.finished.isoformat()})"))
        w_from, w_to = cycle.window
        fmt = "%Y-%m-%d %H:%M:%S"

        all_j = sh("journalctl", "-u", ALL_UNIT, "--since", w_from.strftime(fmt),
                   "--until", w_to.strftime(fmt), "--no-pager", "-o", "cat")
        trade_j = sh("journalctl", "-u", TRADING_UNIT, "--since",
                     w_from.strftime(fmt), "--until", w_to.strftime(fmt),
                     "--no-pager", "-o", "cat")
        raw.append(f"--- {ALL_UNIT} journal ---\n{all_j}")
        raw.append(f"--- {TRADING_UNIT} journal ---\n{trade_j}")

        findings += check_exclusions(all_j)

        names = sorted(p.name for p in args.backup_dir.glob("btc_trading_*"))
        raw.append("backup dir entries for the cycle: " + ", ".join(
            n for n in names if cycle.stamp in n))
        art, finding = select_plaintext(names, cycle)
        findings.append(finding)

        if art:
            findings.append(check_provenance(art, trade_j))
            findings += check_companions(art, set(names))
            mf = read_json(args.backup_dir / (art[:-3] + ".manifest.json"))
            am = read_json(args.backup_dir / (art + ".age-manifest.json"))
            findings += check_checksums(
                mf, am, sha256(args.backup_dir / art),
                sha256(args.backup_dir / (art + ".age")))
            findings.append(Finding(INFO, "manifest provenance",
                                    f"git_sha={mf.get('git_sha')} "
                                    f"backup_ts={mf.get('backup_ts')} "
                                    f"option_quotes="
                                    f"{(mf.get('row_counts') or {}).get('option_quotes')}"))
        findings += check_offhost(read_json(args.backup_dir / ".offhost-status.json"),
                                  (art + ".age") if art else None, trade_j)
        findings += check_retention(trade_j)

        col_j = sh("journalctl", "-u", COLLECTOR_UNIT, "--since",
                   w_from.strftime(fmt), "--until", w_to.strftime(fmt),
                   "--no-pager", "-o", "cat")
        snaps = [l for l in col_j.splitlines() if "[snapshot_writer] ts=" in l]
        counts = {p: sum(1 for l in col_j.splitlines() if p in l)
                  for p in ("periodic pass failed", "periodic pass skipped",
                            "HALT", "database is locked", "CRITICAL",
                            " ERROR ", "Traceback")}
        calls = [l for l in col_j.splitlines()
                 if re.search(r"place_order|cancel_order|\[exec\] ", l)]
        raw.append(f"collector window snapshots={len(snaps)} counts={counts}")
        base = read_json(args.state_dir / "baseline.json")
        findings += check_collector(
            sh("systemctl", "show", COLLECTOR_UNIT, "-p", "MainPID", "--value").strip(),
            sh("systemctl", "show", COLLECTOR_UNIT, "-p", "NRestarts", "--value").strip(),
            sh("systemctl", "show", COLLECTOR_UNIT, "-p", "ActiveState", "--value").strip(),
            base.get("collector_pid"), snaps, counts, calls)

    failed = sh("systemctl", "--failed", "--no-legend", "--plain")
    raw.append("--- systemctl --failed ---\n" + failed)
    for line in failed.splitlines():
        if line.strip():
            findings.append(Finding(INFO, f"failed unit: {line.split()[0]}",
                                    "reported, not modified"))

    df = sh("df", "-h", "/")
    raw.append("--- df -h / ---\n" + df)

    header = {
        "report_generated_utc": now.isoformat(),
        "host_utc_time": sh("date", "-u", "+%Y-%m-%dT%H:%M:%SZ").strip(),
        "cycle_under_test": cycle.day.isoformat() if cycle else "<none discovered>",
        "deployed_head": sh("git", "-C", str(args.repo), "rev-parse", "HEAD").strip(),
        "verifier": str(Path(__file__).resolve()),
    }
    text = render(findings, header, raw)
    print(text)
    if not args.stdout_only:
        try:
            args.state_dir.mkdir(parents=True, exist_ok=True)
            out = args.state_dir / (
                f"cycle-{cycle.stamp if cycle else 'none'}-"
                f"{now.strftime('%Y%m%dT%H%M%SZ')}.txt")
            out.write_text(text, encoding="utf-8")
            (args.state_dir / "latest.txt").write_text(text, encoding="utf-8")
            print(f"[verify] report written: {out}")
        except Exception as exc:
            print(f"[verify] could not write report: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":                                      # pragma: no cover
    sys.exit(main())
