"""Tests for the durable backup-cycle verifier.

The first verifier hardcoded both the cycle date and the sampling time. Within
one day it was reporting a false INCOMPLETE against an already-verified cycle,
because 02:45 landed 80 seconds inside the trading backup's own run
(02:43:40 -> 02:46:24). These tests pin the two properties that replaced those
literals: the cycle is DISCOVERED from the producing unit's own run record, and
anything unfinished, stale, failed or ambiguous refuses to be judged.

Every decision under test is a pure function, so none of this needs systemd, a
real filesystem or a real clock.

Run from repo root:
    conda run -n btc_bot pytest tests/test_verify_backup_cycle.py -v
"""
from __future__ import annotations

import ast
import importlib.util
import pathlib
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"


def _load():
    spec = importlib.util.spec_from_file_location(
        "verify_backup_cycle", _SCRIPTS / "verify_backup_cycle.py")
    mod = importlib.util.module_from_spec(spec)
    # Register before exec: @dataclass resolves cls.__module__ through
    # sys.modules, and fails with AttributeError if the module is not there.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


v = _load()

UTC = timezone.utc


def _run(start=None, exit=None, result="success", name=v.TRADING_UNIT):
    return v.UnitRun(name=name, start=start, exit=exit, result=result)


def _dt(y, m, d, hh=2, mm=43, ss=40):
    return datetime(y, m, d, hh, mm, ss, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Cycle discovery -- the replacement for the hardcoded date
# ---------------------------------------------------------------------------

class TestCycleDiscovery:
    def test_cycle_comes_from_the_producing_units_own_run(self):
        start, end = _dt(2026, 8, 24), _dt(2026, 8, 24, 2, 46, 24)
        cycle, why = v.discover_cycle(_dt(2026, 8, 24, 2, 55), _run(start, end))
        assert why == ""
        assert cycle.day == date(2026, 8, 24)
        assert cycle.stamp == "20260824"
        assert (cycle.started, cycle.finished) == (start, end)

    def test_job_still_in_progress_refuses(self):
        """The exact false-negative shape: sampled mid-run."""
        cycle, why = v.discover_cycle(
            _dt(2026, 8, 24, 2, 45), _run(_dt(2026, 8, 24), exit=None))
        assert cycle is None
        assert "still running" in why and "half-written" in why

    def test_exit_before_start_is_also_in_progress(self):
        """A stale exit stamp from the PREVIOUS run must not look complete."""
        cycle, why = v.discover_cycle(
            _dt(2026, 8, 24, 2, 45),
            _run(_dt(2026, 8, 24), exit=_dt(2026, 8, 23, 2, 46, 24)))
        assert cycle is None and "still running" in why

    def test_never_run_refuses(self):
        cycle, why = v.discover_cycle(_dt(2026, 8, 24, 2, 55), _run(None, None))
        assert cycle is None and "never run" in why

    def test_failed_run_refuses(self):
        cycle, why = v.discover_cycle(
            _dt(2026, 8, 24, 2, 55),
            _run(_dt(2026, 8, 24), _dt(2026, 8, 24, 2, 46), result="exit-code"))
        assert cycle is None and "exit-code" in why

    def test_stale_cycle_refuses_rather_than_reassuring(self):
        """A week-old success must not be re-reported as last night's PASS."""
        cycle, why = v.discover_cycle(
            _dt(2026, 8, 24, 2, 55),
            _run(_dt(2026, 8, 17), _dt(2026, 8, 17, 2, 46)))
        assert cycle is None and "no recent cycle" in why

    def test_a_cycle_just_inside_the_age_limit_is_accepted(self):
        now = _dt(2026, 8, 24, 2, 55)
        end = now - v.MAX_CYCLE_AGE + timedelta(minutes=1)
        cycle, why = v.discover_cycle(now, _run(end - timedelta(minutes=3), end))
        assert cycle is not None, why

    def test_future_exit_refuses_as_clock_skew(self):
        cycle, why = v.discover_cycle(
            _dt(2026, 8, 24, 2, 55),
            _run(_dt(2026, 8, 25), _dt(2026, 8, 25, 2, 46)))
        assert cycle is None and "future" in why

    @pytest.mark.parametrize("y,m,d", [
        (2026, 8, 31),      # month rollover
        (2026, 12, 31),     # year rollover
        (2028, 2, 29),      # leap day
        (2027, 1, 1),       # first of month
    ])
    def test_date_rollover_is_derived_not_assumed(self, y, m, d):
        start = _dt(y, m, d)
        cycle, why = v.discover_cycle(
            start + timedelta(minutes=12), _run(start, start + timedelta(minutes=3)))
        assert why == ""
        assert cycle.day == date(y, m, d)
        assert cycle.stamp == f"{y:04d}{m:02d}{d:02d}"

    def test_window_is_derived_from_the_cycle_day(self):
        cycle, _ = v.discover_cycle(
            _dt(2026, 12, 31, 3, 0),
            _run(_dt(2026, 12, 31), _dt(2026, 12, 31, 2, 46)))
        w_from, w_to = cycle.window
        assert w_from == datetime(2026, 12, 31, 2, 0, tzinfo=UTC)
        assert w_to == datetime(2026, 12, 31, 2, 55, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Artifact selection
# ---------------------------------------------------------------------------

def _cycle(day="20260824"):
    d = datetime.strptime(day, "%Y%m%d").replace(tzinfo=UTC)
    return v.Cycle(d.date(), d, d + timedelta(minutes=3))


class TestPlaintextSelection:
    def test_exactly_one_is_accepted(self):
        names = ["btc_trading_20260824_024340.db",
                 "btc_trading_20260824_024340.db.age",
                 "btc_trading_20260823_024340.db"]
        art, f = v.select_plaintext(names, _cycle())
        assert art == "btc_trading_20260824_024340.db"
        assert f.status == v.OBSERVED

    def test_missing_artifact_fails_closed(self):
        art, f = v.select_plaintext(["btc_trading_20260823_024340.db"], _cycle())
        assert art is None and f.status == v.MISSING

    def test_duplicate_artifacts_fail_closed(self):
        """Two plaintexts for one date is the defect the policy exists to stop."""
        names = ["btc_trading_20260824_020340.db", "btc_trading_20260824_024340.db"]
        art, f = v.select_plaintext(names, _cycle())
        assert art is None
        assert f.status == v.AMBIGUOUS
        assert "duplicate" in f.detail

    def test_ciphertext_is_never_mistaken_for_a_plaintext(self):
        art, f = v.select_plaintext(["btc_trading_20260824_024340.db.age"], _cycle())
        assert art is None and f.status == v.MISSING

    def test_another_days_artifact_is_not_selected(self):
        art, f = v.select_plaintext(["btc_trading_20260825_024340.db"], _cycle())
        assert art is None and f.status == v.MISSING


class TestProvenance:
    def test_journal_naming_the_artifact_is_the_proof(self):
        f = v.check_provenance("btc_trading_20260824_024340.db",
                               "[backup] target : .../btc_trading_20260824_024340.db")
        assert f.status == v.OBSERVED

    def test_unnamed_artifact_is_ambiguous(self):
        f = v.check_provenance("btc_trading_20260824_024340.db", "nothing here")
        assert f.status == v.AMBIGUOUS


class TestCompanions:
    def test_all_three_present(self):
        n = "btc_trading_20260824_024340.db"
        present = {n, n[:-3] + ".manifest.json", n + ".age", n + ".age-manifest.json"}
        assert all(f.status == v.OBSERVED for f in v.check_companions(n, present))

    def test_each_absent_companion_is_reported(self):
        n = "btc_trading_20260824_024340.db"
        out = v.check_companions(n, {n})
        assert [f.status for f in out] == [v.MISSING] * 3

    def test_shipped_ciphertext_is_expected_to_be_absent(self):
        """Once shipped, the .age is reclaimed -- absence is the good outcome."""
        n = "btc_trading_20260824_024340.db"
        present = {n, n[:-3] + ".manifest.json", n + ".age-manifest.json"}
        out = v.check_companions(n, present, shipped=True)
        assert all(f.status == v.OBSERVED for f in out)

    def test_a_shipped_ciphertext_still_present_is_flagged(self):
        """It means the reclaim did not happen, which is what filled the disk."""
        n = "btc_trading_20260824_024340.db"
        present = {n, n[:-3] + ".manifest.json", n + ".age",
                   n + ".age-manifest.json"}
        out = v.check_companions(n, present, shipped=True)
        assert [f.status for f in out if "reclaimed" in f.check] == [v.AMBIGUOUS]

    def test_an_unshipped_ciphertext_is_still_required(self):
        n = "btc_trading_20260824_024340.db"
        present = {n, n[:-3] + ".manifest.json", n + ".age-manifest.json"}
        out = v.check_companions(n, present, shipped=False)
        assert [f.status for f in out if "ciphertext" in f.check] == [v.MISSING]

    def test_recorded_ciphertext_digest_is_enough_once_reclaimed(self):
        out = v.check_checksums({"sha256": "aa"},
                                {"ciphertext_sha256": "bb", "plaintext_sha256": "aa"},
                                "aa", None)
        assert [f.status for f in out] == [v.OBSERVED] * 3


# ---------------------------------------------------------------------------
# Exclusions, checksums, off-host, retention
# ---------------------------------------------------------------------------

class TestExclusions:
    GOOD = ("[skip] ledger: excluded by --exclude\n"
            "[skip] btc_trading: excluded by --exclude\n"
            "[backup] target : /opt/btcbot/data/evidence-backups/evidence_x.db\n")

    def test_both_exclusions_observed(self):
        out = v.check_exclusions(self.GOOD)
        assert all(f.status == v.OBSERVED for f in out)

    def test_missing_exclusion_is_caught(self):
        out = v.check_exclusions("[skip] ledger: excluded by --exclude\n")
        assert any(f.status == v.MISSING and "btc_trading" in f.check for f in out)

    def test_all_producing_a_trading_artifact_is_caught(self):
        out = v.check_exclusions(
            self.GOOD + "[backup] target : /opt/btcbot/data/backups/btc_trading_x.db\n")
        assert any(f.status == v.AMBIGUOUS for f in out)

    def test_empty_journal_fails_closed(self):
        out = v.check_exclusions("   ")
        assert out and out[0].status == v.MISSING


class TestChecksums:
    def test_all_three_bindings_match(self):
        out = v.check_checksums({"sha256": "aa"}, {"ciphertext_sha256": "bb",
                                                   "plaintext_sha256": "aa"}, "aa", "bb")
        assert all(f.status == v.OBSERVED for f in out)

    def test_plaintext_mismatch_is_caught(self):
        out = v.check_checksums({"sha256": "aa"}, {"ciphertext_sha256": "bb",
                                                   "plaintext_sha256": "aa"}, "zz", "bb")
        assert out[0].status == v.AMBIGUOUS

    def test_age_manifest_binding_a_different_plaintext_is_caught(self):
        out = v.check_checksums({"sha256": "aa"}, {"ciphertext_sha256": "bb",
                                                   "plaintext_sha256": "cc"}, "aa", "bb")
        assert out[2].status == v.AMBIGUOUS

    def test_unreadable_digests_fail_closed(self):
        out = v.check_checksums({}, {}, None, None)
        assert all(f.status == v.AMBIGUOUS for f in out)


class TestOffhost:
    SIDE = {"status": "ok", "exit_code": 0, "artifact": "a.db.age",
            "last_success_at": "2026-08-24T02:43:40+00:00"}

    def test_ok_sidecar_and_upload_lines(self):
        out = v.check_offhost(self.SIDE, "a.db.age", "upload: data/backups/a.db.age")
        assert out[0].status == v.OBSERVED and out[1].status == v.OBSERVED

    def test_sidecar_naming_a_different_artifact_is_caught(self):
        """Yesterday's success must not vouch for tonight's cycle."""
        out = v.check_offhost(self.SIDE, "b.db.age", "upload: x")
        assert out[0].status == v.AMBIGUOUS

    def test_failed_upload_is_caught(self):
        bad = dict(self.SIDE, status="failed", exit_code=1)
        out = v.check_offhost(bad, "a.db.age", "upload: x")
        assert out[0].status == v.AMBIGUOUS

    def test_missing_upload_lines_are_caught(self):
        out = v.check_offhost(self.SIDE, "a.db.age", "")
        assert any(f.status == v.MISSING for f in out)

    def test_s3_is_never_probed(self):
        out = v.check_offhost(self.SIDE, "a.db.age", "upload: x")
        assert any("write-only by design" in f.detail for f in out)


class TestRetention:
    def test_orphan_counters_observed(self):
        out = v.check_retention(
            "[retention] kept=5 deleted=0 orphan_ciphertext_kept=12 "
            "orphan_ciphertext_deleted=0")
        assert out[0].status == v.OBSERVED

    def test_old_code_without_counters_is_caught(self):
        out = v.check_retention("[retention] kept=5  deleted=0")
        assert out[0].status == v.AMBIGUOUS and "old code" in out[0].detail

    def test_no_retention_line_at_all_fails_closed(self):
        assert v.check_retention("")[0].status == v.MISSING


# ---------------------------------------------------------------------------
# Collector continuity
# ---------------------------------------------------------------------------

def _snaps(n, start_min=0, step=1, dirty=False):
    out = []
    for i in range(n):
        m = start_min + i * step
        flag = "skip=1 fail=0" if (dirty and i == 0) else "skip=0 fail=0"
        out.append(f"[snapshot_writer] ts=2026-08-24T02:{m:02d}:52Z "
                   f"quotes=66/66 ({flag})")
    return out


class TestCollector:
    CLEAN = {"periodic pass failed": 0, "HALT": 0, "database is locked": 0}

    # The cycle under test, and a process that was already running before it.
    CYCLE = datetime(2026, 9, 9, 2, 40, 40, tzinfo=timezone.utc)
    BEFORE = datetime(2026, 9, 8, 18, 15, 56, tzinfo=timezone.utc)
    DURING = datetime(2026, 9, 9, 2, 42, 0, tzinfo=timezone.utc)

    def test_healthy_window(self):
        out = v.check_collector("414071", "0", "active", self.BEFORE,
                                self.CYCLE, _snaps(45), self.CLEAN, [])
        assert all(f.status == v.OBSERVED for f in out)

    def test_restart_is_caught(self):
        out = v.check_collector("999", "1", "active", self.BEFORE,
                                self.CYCLE, _snaps(45), self.CLEAN, [])
        assert out[0].status == v.AMBIGUOUS

    def test_collection_gap_is_caught(self):
        gapped = _snaps(5) + _snaps(5, start_min=30)
        out = v.check_collector("414071", "0", "active", self.BEFORE,
                                self.CYCLE, gapped, self.CLEAN, [])
        assert out[1].status == v.AMBIGUOUS

    def test_skip_or_fail_is_caught(self):
        out = v.check_collector("414071", "0", "active", self.BEFORE,
                                self.CYCLE, _snaps(45, dirty=True), self.CLEAN, [])
        assert out[1].status == v.AMBIGUOUS

    def test_errors_are_caught(self):
        out = v.check_collector("414071", "0", "active", self.BEFORE,
                                self.CYCLE, _snaps(45), dict(self.CLEAN, HALT=1), [])
        assert out[2].status == v.AMBIGUOUS

    def test_order_calls_are_caught(self):
        out = v.check_collector("414071", "0", "active", self.BEFORE,
                                self.CYCLE, _snaps(45), self.CLEAN,
                                ["[exec] ACKED something"])
        assert out[3].status == v.AMBIGUOUS

    # -- the stale-baseline defect this replaced --------------------------

    def test_an_authorized_restart_before_the_cycle_is_not_flagged(self):
        """The defect: a PID pinned once on 2026-08-22 was compared every night.

        After the authorized restart of 2026-09-08 18:15:56 it could never
        match again, so the check reported the same AMBIG nightly no matter
        what the backup did. A process that started 8h before the cycle is
        exactly what "untouched by the backup" means.
        """
        out = v.check_collector("581989", "0", "active", self.BEFORE,
                                self.CYCLE, _snaps(45), self.CLEAN, [])
        assert out[0].status == v.OBSERVED
        assert "581989" in out[0].detail

    def test_a_restart_during_the_cycle_is_still_caught(self):
        """Detection must not be weakened: this is the case that matters."""
        out = v.check_collector("999", "0", "active", self.DURING,
                                self.CYCLE, _snaps(45), self.CLEAN, [])
        assert out[0].status == v.AMBIGUOUS

    def test_a_crash_restart_is_still_caught_by_nrestarts(self):
        """NRestarts carries crash detection and is unchanged.

        systemd increments it on a supervisor-driven restart under
        Restart=always; a deliberate `systemctl restart` does not.
        """
        out = v.check_collector("999", "2", "active", self.BEFORE,
                                self.CYCLE, _snaps(45), self.CLEAN, [])
        assert out[0].status == v.AMBIGUOUS

    def test_inactive_is_still_caught(self):
        out = v.check_collector("0", "0", "failed", self.BEFORE,
                                self.CYCLE, _snaps(45), self.CLEAN, [])
        assert out[0].status == v.AMBIGUOUS

    def test_unknown_start_time_fails_closed(self):
        out = v.check_collector("581989", "0", "active", None,
                                self.CYCLE, _snaps(45), self.CLEAN, [])
        assert out[0].status == v.AMBIGUOUS

    def test_unknown_cycle_fails_closed(self):
        out = v.check_collector("581989", "0", "active", self.BEFORE,
                                None, _snaps(45), self.CLEAN, [])
        assert out[0].status == v.AMBIGUOUS

    def test_a_start_exactly_at_the_cycle_start_is_not_before_it(self):
        out = v.check_collector("581989", "0", "active", self.CYCLE,
                                self.CYCLE, _snaps(45), self.CLEAN, [])
        assert out[0].status == v.AMBIGUOUS

    def test_no_baseline_file_is_consulted_any_more(self):
        """The stale-prone dependency is removed, not merely tolerated.

        Docstrings are stripped first: the history is deliberately explained
        in prose there, so a substring match over the raw file would be
        satisfied by the explanation rather than by the code.
        """
        import ast
        tree = ast.parse(pathlib.Path(v.__file__).read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))                     and ast.get_docstring(n):
                n.body = n.body[1:] or [ast.Pass()]
        code = ast.unparse(tree)
        assert "baseline.json" not in code
        assert "baseline_pid" not in code

    def test_max_gap_helper(self):
        assert v.max_gap_minutes(_snaps(3)) == pytest.approx(1.0)
        assert v.max_gap_minutes([]) is None
        assert v.max_gap_minutes(_snaps(1)) is None


# ---------------------------------------------------------------------------
# Verdict + report shape
# ---------------------------------------------------------------------------

class TestVerdict:
    def test_pass_only_when_everything_observed(self):
        assert v.verdict_of([v.Finding(v.OBSERVED, "a", "b"),
                             v.Finding(v.INFO, "c", "d")]) == "PASS"

    def test_any_missing_is_incomplete(self):
        assert v.verdict_of([v.Finding(v.OBSERVED, "a", "b"),
                             v.Finding(v.MISSING, "c", "d")]) == "INCOMPLETE"

    def test_any_ambiguous_is_incomplete(self):
        assert v.verdict_of([v.Finding(v.AMBIGUOUS, "c", "d")]) == "INCOMPLETE"

    def test_info_alone_never_fails_the_verdict(self):
        assert v.verdict_of([v.Finding(v.INFO, "pre-existing", "x")]) == "PASS"

    def test_report_separates_the_three_sections(self):
        text = v.render(
            [v.Finding(v.OBSERVED, "ok-check", "d1"),
             v.Finding(v.MISSING, "bad-check", "d2"),
             v.Finding(v.INFO, "info-check", "d3")],
            {"cycle_under_test": "2026-08-24"}, ["raw line"])
        assert "VERDICT: INCOMPLETE" in text
        one = text.index("1. OBSERVED FACTS")
        two = text.index("2. EXPECTED BUT NOT OBSERVED")
        three = text.index("3. UNRELATED / PRE-EXISTING")
        assert one < two < three
        assert text.index("ok-check") < two
        assert two < text.index("bad-check") < three
        assert three < text.index("info-check")


# ---------------------------------------------------------------------------
# Structural guards -- the properties that must not regress
# ---------------------------------------------------------------------------

class TestNoHardcodedCycle:
    """The original defect: a literal date and a literal sampling time."""

    def _tree(self):
        return ast.parse((_SCRIPTS / "verify_backup_cycle.py").read_text(encoding="utf-8"))

    def test_no_module_level_cycle_date_constant(self):
        for node in self._tree().body:
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        assert t.id not in ("CYCLE_DATE", "WINDOW_FROM", "WINDOW_TO"), \
                            f"{t.id} is back: the cycle must be discovered, not pinned"

    def test_no_literal_calendar_date_in_executable_code(self):
        tree = self._tree()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
                b = node.body
                if b and isinstance(b[0], ast.Expr) and isinstance(
                        getattr(b[0], "value", None), ast.Constant):
                    node.body = b[1:]
        code = ast.unparse(tree)
        import re as _re
        hits = _re.findall(r"['\"]20\d{2}-\d{2}-\d{2}", code) + \
            _re.findall(r"['\"]20\d{6}['\"]", code)
        assert not hits, f"hardcoded calendar literal(s) in code: {hits}"

    def test_discover_cycle_is_actually_used_by_main(self):
        src = (_SCRIPTS / "verify_backup_cycle.py").read_text(encoding="utf-8")
        assert "discover_cycle(now, trading)" in src


class TestReadOnlyByConstruction:
    def _tree(self):
        return ast.parse((_SCRIPTS / "verify_backup_cycle.py").read_text(encoding="utf-8"))

    def test_every_external_command_is_read_only(self):
        allowed = {"journalctl", "systemctl", "df", "git", "date"}
        read_verbs = {"show", "--failed", "rev-parse", "-u", "-h"}
        for n in ast.walk(self._tree()):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "sh":
                argv = [a.value if isinstance(a, ast.Constant) else "<var>"
                        for a in n.args]
                assert argv[0] in allowed, f"non-allowlisted binary: {argv}"
                if argv[0] == "systemctl":
                    assert set(argv[1:]) & read_verbs, f"systemctl write verb: {argv}"

    def test_no_destructive_or_control_call(self):
        tree = self._tree()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
                b = node.body
                if b and isinstance(b[0], ast.Expr) and isinstance(
                        getattr(b[0], "value", None), ast.Constant):
                    node.body = b[1:]
        code = ast.unparse(tree)
        for bad in ("os.remove", "rmtree", "os.rename", ".unlink(", "shutil.move"):
            assert bad not in code, f"destructive call present: {bad}"

    def test_sqlite_is_never_opened_read_write(self):
        src = (_SCRIPTS / "verify_backup_cycle.py").read_text(encoding="utf-8")
        assert "sqlite3.connect" not in src or "mode=ro" in src

    def test_backup_script_is_never_invoked(self):
        src = (_SCRIPTS / "verify_backup_cycle.py").read_text(encoding="utf-8")
        assert "backup.py" not in src


class TestUnitContainment:
    def _unit(self, name):
        return (pathlib.Path("deploy") / name).read_text(encoding="utf-8")

    def test_timer_is_recurring_not_a_one_shot_date(self):
        t = self._unit("btcbot-verify-backup-cycle.timer")
        line = next(l for l in t.splitlines() if l.startswith("OnCalendar="))
        assert line.strip() == "OnCalendar=*-*-* 02:55:00 UTC", line
        import re as _re
        assert not _re.search(r"OnCalendar=20\d{2}-", t), "one-shot date is back"

    def test_timer_runs_after_the_trading_backup_completes(self):
        t = self._unit("btcbot-verify-backup-cycle.timer")
        hh, mm = t.split("OnCalendar=*-*-* ")[1][:5].split(":")
        assert (int(hh), int(mm)) >= (2, 50), "must be after the 02:46 completion"

    def _directives(self, name):
        """Active directives only. A commented-out hardening line is NOT in force."""
        return [l.strip() for l in self._unit(name).splitlines()
                if l.strip() and not l.strip().startswith("#")]

    def test_service_is_contained(self):
        active = self._directives("btcbot-verify-backup-cycle.service")
        for required in ("Type=oneshot", "User=btcbot", "ProtectSystem=strict",
                         "PrivateNetwork=yes", "NoNewPrivileges=yes",
                         "StateDirectory=btcbot-verification"):
            assert required in active, f"{required} is not an active directive"

    def test_service_has_no_dependency_on_the_collector(self):
        s = self._unit("btcbot-verify-backup-cycle.service")
        directives = [l.strip() for l in s.splitlines()
                      if l.strip() and not l.strip().startswith("#")]
        for d in directives:
            if d.split("=", 1)[0] in ("After", "Before", "Requires", "Requisite",
                                      "Wants", "BindsTo", "PartOf", "Conflicts"):
                assert "btcbot.service" not in d, d

    def test_service_does_not_run_as_root(self):
        s = self._unit("btcbot-verify-backup-cycle.service")
        assert "User=root" not in s
