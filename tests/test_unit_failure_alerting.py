"""Gate 5C.27: failure escalation for the forward-evidence unit.

Gate 5C.26 established the gap by evidence: btcbot-fwd-hg.service exited
status=1/FAILURE on 19 consecutive days (2026-07-19 .. 2026-08-06) with the
governance pre-check raising `IndexError: No item with that key` on
`r["run_kind"]`. The timer fired correctly every day, the host never rebooted,
and `systemctl list-timers` looked healthy throughout. Nothing alerted, because
no unit in the project carried OnFailure= and no notifier unit existed.

Two independent detectors are asserted here:
  1. OnFailure= on the unit           -> immediate, on the FIRST failure
  2. healthcheck's forward_cycle      -> staleness, as the cycle falls behind

Either alone would have caught the outage. The second already existed and was
simply not running at the time (its first invocation was 2026-08-07T02:41,
after the outage ended); the first is what this gate adds.
"""
from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
FWD_UNIT = DEPLOY / "btcbot-fwd-hg.service"
FWD_TIMER = DEPLOY / "btcbot-fwd-hg.timer"
ALERT_UNIT = DEPLOY / "btcbot-alert@.service"
HC_TIMER = DEPLOY / "btcbot-healthcheck.timer"
HC_UNIT = DEPLOY / "btcbot-healthcheck.service"


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def alert():
    return _load("alert_unit_failure", ROOT / "scripts" / "alert_unit_failure.py")


def _directives(path: pathlib.Path) -> list[str]:
    """Active directives only -- commented prose must never satisfy a test.

    This project has been bitten repeatedly by substring matches landing in
    comments rather than configuration.
    """
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


# --- detector 1: OnFailure= wiring ------------------------------------------

class TestOnFailureWiring:
    def test_forward_unit_declares_onfailure(self):
        d = _directives(FWD_UNIT)
        hits = [x for x in d if x.startswith("OnFailure=")]
        assert hits, "btcbot-fwd-hg.service has no active OnFailure= directive"
        assert hits[0] == "OnFailure=btcbot-alert@%n.service", hits

    def test_notifier_unit_exists_and_is_a_template(self):
        assert ALERT_UNIT.exists(), "notifier unit missing"
        assert "@" in ALERT_UNIT.name, "notifier must be a template unit"
        d = _directives(ALERT_UNIT)
        assert any(x.startswith("ExecStart=") and "alert_unit_failure.py" in x
                   for x in d), d
        assert "Type=oneshot" in d

    def test_notifier_passes_the_failed_unit_name(self):
        exec_line = [x for x in _directives(ALERT_UNIT) if x.startswith("ExecStart=")][0]
        assert "%i" in exec_line, "notifier does not receive the failed unit name"

    def test_notifier_is_read_only(self):
        """A notifier must never be able to modify state."""
        d = _directives(ALERT_UNIT)
        assert not any(x.startswith("ReadWritePaths=") for x in d), \
            "notifier granted write access it does not need"
        assert "ProtectSystem=strict" in d
        assert "NoNewPrivileges=yes" in d

    def test_no_retry_semantics_introduced(self):
        """run_daily aborts fail-closed; re-running it is not the fix."""
        d = _directives(FWD_UNIT)
        for banned in ("Restart=", "RestartSec=", "StartLimitBurst="):
            assert not any(x.startswith(banned) for x in d), \
                f"{banned} introduced: retry is explicitly out of scope"

    def test_timer_cadence_and_persistence_unchanged(self):
        d = _directives(FWD_TIMER)
        assert "OnCalendar=*-*-* 01:00:00 UTC" in d
        assert "Persistent=true" in d

    def test_forward_unit_still_oneshot_and_unprivileged(self):
        d = _directives(FWD_UNIT)
        assert "Type=oneshot" in d
        assert "User=btcbot" in d
        assert any(x.startswith("ExecStart=") and "forward_evidence.run_daily" in x
                   for x in d)


# --- the notifier itself -----------------------------------------------------

class TestNotifier:
    def test_builds_a_message_naming_the_unit_and_exit(self, alert):
        msg = alert.build_message(
            "btcbot-fwd-hg.service",
            {"Result": "exit-code", "ExecMainStatus": "1",
             "ExecMainExitTimestamp": "Sun 2026-07-19 01:00:36 UTC"},
            "ERROR Aborting: fix registry errors and retry.")
        assert "btcbot-fwd-hg.service" in msg
        assert "exit=1" in msg and "exit-code" in msg
        assert "Aborting" in msg, "the cause was dropped from the alert"

    def test_message_is_ascii_safe(self, alert):
        """It is printed to stdout when Telegram is unconfigured; a cp1252
        console raised UnicodeEncodeError on an emoji, crashing the notifier."""
        msg = alert.build_message("u.service", {"Result": "exit-code"}, "tail")
        msg.encode("ascii")          # raises if a non-ASCII char returns
        msg.encode("cp1252")

    def test_message_is_truncated_below_the_telegram_limit(self, alert):
        msg = alert.build_message("u.service", {"Result": "exit-code"}, "x" * 20000)
        assert len(msg) <= 4096, len(msg)
        assert "u.service" in msg, "truncation destroyed the unit name"

    def test_exit_status_is_always_zero(self, alert, monkeypatch):
        """A notifier that fails its own unit turns one incident into two."""
        monkeypatch.setattr(alert, "unit_status",
                            lambda u: (_ for _ in ()).throw(RuntimeError("boom")))
        with pytest.raises(RuntimeError):
            alert.unit_status("x")          # confirms the monkeypatch is live
        monkeypatch.setattr(alert, "unit_status", lambda u: {"Result": "exit-code"})
        monkeypatch.setattr(alert, "journal_tail", lambda u, lines=15: "tail")
        monkeypatch.setattr(alert, "send",
                            lambda t: (_ for _ in ()).throw(RuntimeError("net")))
        with pytest.raises(RuntimeError):
            alert.main(["u.service"])       # send() raising is surfaced...
        monkeypatch.setattr(alert, "send", lambda t: False)
        assert alert.main(["u.service"]) == 0   # ...but a failed send is not

    def test_dry_run_sends_nothing(self, alert, monkeypatch, capsys):
        sent = []
        monkeypatch.setattr(alert, "send", lambda t: sent.append(t) or True)
        monkeypatch.setattr(alert, "unit_status", lambda u: {"Result": "exit-code"})
        monkeypatch.setattr(alert, "journal_tail", lambda u, lines=15: "t")
        assert alert.main(["u.service", "--dry-run"]) == 0
        assert sent == []
        assert "u.service" in capsys.readouterr().out

    def test_missing_telegram_config_degrades_to_stdout(self, alert, monkeypatch, capsys):
        monkeypatch.setattr(alert, "_load_env", lambda k: "")
        assert alert.send("hello") is False
        assert "hello" in capsys.readouterr().out

    def test_subprocess_failures_are_contained(self, alert, monkeypatch):
        def boom(*a, **k):
            raise FileNotFoundError("systemctl")
        monkeypatch.setattr(subprocess, "run", boom)
        assert "_error" in alert.unit_status("u.service")
        assert "unavailable" in alert.journal_tail("u.service")


# --- detector 2: healthcheck freshness --------------------------------------

class TestHealthcheckForwardFreshness:
    """The pre-existing dead-man. Asserted here so a regression is visible."""

    @pytest.fixture(scope="class")
    def hc(self):
        return _load("healthcheck_5c27", ROOT / "scripts" / "healthcheck.py")

    def test_forward_cycle_check_exists(self, hc):
        assert hasattr(hc, "check_forward_cycle")

    def test_healthcheck_timer_is_enabled_and_frequent(self):
        d = _directives(HC_TIMER)
        assert any(x.startswith("OnCalendar=") for x in d)
        assert "WantedBy=timers.target" in d, "timer would not start at boot"
        cal = [x for x in d if x.startswith("OnCalendar=")][0]
        assert "0/10" in cal, f"expected a 10-minute cadence, got {cal}"

    def test_healthcheck_alerts_on_warning(self):
        """Staleness is WARNING before it is CRITICAL; the unit must escalate it."""
        exec_line = [x for x in _directives(HC_UNIT) if x.startswith("ExecStart=")][0]
        assert "--alert-on-warning" in exec_line, exec_line

    def test_stale_forward_cycle_escalates(self, hc, tmp_path):
        """Missing/behind runs must not read as OK."""
        import sqlite3
        db = tmp_path / "evidence_forward.db"
        c = sqlite3.connect(db)
        c.executescript(
            "CREATE TABLE run_log (id INTEGER PRIMARY KEY, signal_id TEXT,"
            " run_date TEXT, fn_hash TEXT, status TEXT, note TEXT, created_at TEXT);")
        c.execute("INSERT INTO run_log (signal_id,run_date,fn_hash,status,created_at)"
                  " VALUES ('g3_momentum_tsmom','2000-01-01','h','ok','2000-01-01T01:00:00Z')")
        c.commit(); c.close()
        res = hc.check_forward_cycle(db)
        assert res.severity != hc.OK, \
            "a decades-stale forward cycle was reported healthy"

    def test_absent_forward_db_does_not_read_as_healthy(self, hc, tmp_path):
        res = hc.check_forward_cycle(tmp_path / "nope.db")
        assert res.severity != hc.OK


# --- governance boundaries ---------------------------------------------------

class TestNoScopeCreep:
    def test_no_backfill_or_reconstruction_anywhere_in_the_change(self):
        src = (ROOT / "scripts" / "alert_unit_failure.py").read_text(encoding="utf-8")
        low = src.lower()
        for banned in ("backfill", "reconstruct", "insert into", "run_daily",
                       "trade_entries", "trade_exits", "signal_log"):
            assert banned not in low, f"notifier references {banned!r}"

    def test_notifier_touches_no_database(self):
        src = (ROOT / "scripts" / "alert_unit_failure.py").read_text(encoding="utf-8")
        assert "sqlite3" not in src, "notifier imports a database driver"

    def test_only_the_forward_unit_gained_onfailure(self):
        """Do not modify unrelated units."""
        changed = []
        for unit in DEPLOY.glob("*.service"):
            if unit.name in ("btcbot-fwd-hg.service", "btcbot-alert@.service"):
                continue
            if any(x.startswith("OnFailure=") for x in _directives(unit)):
                changed.append(unit.name)
        assert changed == [], f"unrelated units gained OnFailure=: {changed}"
