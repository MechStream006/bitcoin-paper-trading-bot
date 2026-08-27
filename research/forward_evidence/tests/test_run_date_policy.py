"""
Gate 6B.3 — run_date provenance boundary (defect B-03).

B-03: run_daily accepted ANY --run-date. A date inside the sealed lockbox or
inside the INC-0005 immutable provenance gap produced a complete, valid-looking
observation (run_log + signal_log + trade_entry) indistinguishable from genuine
scheduled forward evidence, and invisible to every existing detector --
healthcheck's forward_cycle compares only MAX(run_date), so a past-dated row
never moves `latest`. Reproduced end-to-end during the Gate 6B.2 audit.

Two layers are tested:
  P*   the pure policy engine (run_date_policy.evaluate_run_date)
  M*   the choke point as wired into run_daily.main()
  ADV* ordering/structural invariants that a plausible refactor could break

Run from research/:
    pytest forward_evidence/tests/test_run_date_policy.py -v
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone

import pytest
import yaml

from forward_evidence import run_daily as _run_daily
from forward_evidence.init_forward_db import init_forward_db
from forward_evidence.run_date_policy import (
    MODE_BACKFILL,
    MODE_SCHEDULED,
    REFUSE_FUTURE,
    REFUSE_IMMUTABLE_GAP,
    REFUSE_LOCKBOX,
    REFUSE_MALFORMED,
    REFUSE_TOO_OLD,
    REFUSE_UNAUTHORIZED,
    PolicyConfigError,
    evaluate_run_date,
    load_policy,
)

_RESEARCH_ROOT = pathlib.Path(__file__).resolve().parents[2]
TODAY = date(2026, 8, 28)          # fixed simulated clock for the P* layer


@pytest.fixture(scope="module")
def policy():
    return load_policy()


@pytest.fixture()
def db_path(tmp_path: pathlib.Path) -> pathlib.Path:
    p = tmp_path / "evidence_forward.db"
    init_forward_db(p)
    return p


def _ev(rd, policy, authorized=False, today=TODAY):
    return evaluate_run_date(rd, today=today, policy=policy, authorized=authorized)


# ===========================================================================
# P — policy engine
# ===========================================================================

class TestPolicyEngine:

    def test_P01_scheduled_yesterday_allowed_without_authorization(self, policy):
        d = _ev("2026-08-27", policy)
        assert d.allowed and d.mode == MODE_SCHEDULED

    def test_P02_today_is_refused_as_future(self, policy):
        """Today is an incomplete UTC day; only completed days are collectable."""
        assert _ev("2026-08-28", policy).code == REFUSE_FUTURE

    def test_P03_future_date_refused(self, policy):
        assert _ev("2027-01-01", policy, authorized=True).code == REFUSE_FUTURE

    def test_P04_malformed_refused(self, policy):
        for bad in ["2026-8-27", "27-08-2026", "not-a-date", "2026-02-30", "", "20260827"]:
            assert _ev(bad, policy, authorized=True).code == REFUSE_MALFORMED, bad

    def test_P05_none_and_non_string_refused(self, policy):
        assert _ev(None, policy).code == REFUSE_MALFORMED

    # -- unconditional windows -------------------------------------------

    def test_P06_lockbox_refused_even_when_authorized(self, policy):
        d = _ev("2025-12-01", policy, authorized=True)
        assert d.code == REFUSE_LOCKBOX and not d.allowed

    @pytest.mark.parametrize("boundary", ["2025-06-01", "2026-05-31"])
    def test_P07_lockbox_boundaries_are_inclusive(self, policy, boundary):
        assert _ev(boundary, policy, authorized=True).code == REFUSE_LOCKBOX

    def test_P08_gap_refused_even_when_authorized(self, policy):
        d = _ev("2026-07-25", policy, authorized=True)
        assert d.code == REFUSE_IMMUTABLE_GAP and not d.allowed

    @pytest.mark.parametrize("boundary", ["2026-07-18", "2026-08-05"])
    def test_P09_gap_boundaries_are_inclusive(self, policy, boundary):
        assert _ev(boundary, policy, authorized=True).code == REFUSE_IMMUTABLE_GAP

    def test_P10_day_after_lockbox_is_not_a_lockbox_refusal(self, policy):
        """Off-by-one guard: 2026-06-01 must fail on horizon, not on lockbox."""
        assert _ev("2026-06-01", policy, authorized=True).code == REFUSE_TOO_OLD

    def test_P11_day_after_gap_is_not_a_gap_refusal(self, policy):
        assert _ev("2026-08-06", policy, authorized=True).code == REFUSE_TOO_OLD

    # -- horizon and authorization ---------------------------------------

    def test_P12_recent_backfill_requires_authorization(self, policy):
        assert _ev("2026-08-20", policy, authorized=False).code == REFUSE_UNAUTHORIZED

    def test_P13_recent_backfill_allowed_when_authorized(self, policy):
        d = _ev("2026-08-20", policy, authorized=True)
        assert d.allowed and d.mode == MODE_BACKFILL

    def test_P14_horizon_boundary_exact(self, policy):
        """14-day horizon measured from yesterday: 08-13 in, 08-12 out."""
        assert policy["backfill_horizon_days"] == 14
        assert _ev("2026-08-13", policy, authorized=True).allowed
        assert _ev("2026-08-12", policy, authorized=True).code == REFUSE_TOO_OLD

    def test_P15_horizon_does_not_apply_to_scheduled(self, policy):
        """Even a 0-day horizon must not block the scheduled date."""
        p = dict(policy, backfill_horizon_days=0)
        assert _ev("2026-08-27", p).mode == MODE_SCHEDULED

    def test_P16_every_refusal_has_code_and_detail_and_no_mode(self, policy):
        for rd, auth in [("2025-12-01", True), ("2026-07-25", True), ("2027-01-01", True),
                         ("2026-08-12", True), ("2026-08-20", False), ("bad", True)]:
            d = _ev(rd, policy, authorized=auth)
            assert not d.allowed and d.code and d.detail and d.mode is None

    def test_P18_authorization_defaults_to_denied(self, policy):
        """
        Pins the DEFAULT, not just the explicit argument. Adversarial mutation
        M09 (`authorized: bool = False` -> `True`) survived the first battery
        because every other test passes `authorized=` explicitly, so the
        default was never exercised. main() always passes it, so production
        impact is nil — but a fail-open default is one careless caller away
        from unlocking historical collection silently.
        """
        d = evaluate_run_date("2026-08-20", today=TODAY, policy=policy)   # no kwarg
        assert not d.allowed and d.code == REFUSE_UNAUTHORIZED

    def test_P17_allowed_decisions_always_carry_a_mode(self, policy):
        for rd, auth in [("2026-08-27", False), ("2026-08-20", True)]:
            d = _ev(rd, policy, authorized=auth)
            assert d.allowed and d.mode in (MODE_SCHEDULED, MODE_BACKFILL) and d.code is None


# ===========================================================================
# Policy config integrity
# ===========================================================================

class TestPolicyConfig:

    def test_C01_lockbox_matches_the_canonical_sealed_artifact(self, policy):
        """
        The policy's lockbox copy must not drift from the source of truth,
        research/reports/evidence_library/lockbox_validation.json ("lockbox").
        That file is SEALED and read-only here.
        """
        canonical = _RESEARCH_ROOT / "reports" / "evidence_library" / "lockbox_validation.json"
        if not canonical.exists():
            pytest.skip("sealed lockbox artifact not present")
        lb = json.loads(canonical.read_text(encoding="utf-8"))["lockbox"]
        assert [policy["lockbox"]["start"], policy["lockbox"]["end"]] == list(lb)

    def test_C02_gap_matches_the_documented_incident_window(self, policy):
        """CLAUDE.md Sec.20: run_dates 2026-07-18..2026-08-05 inclusive."""
        gaps = policy["immutable_gaps"]
        assert any(g["start"] == "2026-07-18" and g["end"] == "2026-08-05" for g in gaps)

    def test_C03_missing_policy_file_fails_closed(self, tmp_path):
        with pytest.raises(PolicyConfigError, match="not found"):
            load_policy(tmp_path / "nope.yaml")

    def test_C04_malformed_yaml_fails_closed(self, tmp_path):
        p = tmp_path / "p.yaml"
        p.write_text("lockbox: [unclosed\n", encoding="utf-8")
        with pytest.raises(PolicyConfigError):
            load_policy(p)

    @pytest.mark.parametrize("mutation", [
        {"lockbox": {"start": "2025-06-01"}},                       # missing end
        {"backfill_horizon_days": -1},
        {"backfill_horizon_days": "fourteen"},
        {"immutable_gaps": "not-a-list"},
        {"lockbox": {"start": "not-a-date", "end": "2026-05-31"}},
    ])
    def test_C05_structurally_invalid_policy_fails_closed(self, tmp_path, policy, mutation):
        cfg = {"lockbox": dict(policy["lockbox"]),
               "immutable_gaps": [dict(g) for g in policy["immutable_gaps"]],
               "backfill_horizon_days": policy["backfill_horizon_days"]}
        cfg.update(mutation)
        p = tmp_path / "p.yaml"
        p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        with pytest.raises(PolicyConfigError):
            load_policy(p)


# ===========================================================================
# M — the choke point in run_daily.main()
# ===========================================================================

def _argv(db_path, run_date=None, authorized=False, dry_run=False):
    a = ["run_daily.py", "--db-path", str(db_path)]
    if run_date is not None:
        a += ["--run-date", run_date]
    if authorized:
        a += ["--backfill-authorized"]
    if dry_run:
        a += ["--dry-run"]
    return a


def _yesterday() -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()


def _all_tables_empty(db_path) -> bool:
    con = sqlite3.connect(db_path)
    try:
        return all(
            con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0
            for t in ("run_log", "signal_log", "trade_entries",
                      "trade_exits", "collection_provenance")
        )
    finally:
        con.close()


@pytest.fixture()
def no_collect(monkeypatch):
    """Governance passes; run_all is stubbed so no real data access occurs."""
    calls: list = []
    monkeypatch.setattr(_run_daily, "_check_governance", lambda _: (True, "ok"))
    monkeypatch.setattr(_run_daily, "run_all",
                        lambda *a, **kw: calls.append((a, kw)) or {})
    return calls


class TestMainChokePoint:

    REFUSED = ["2025-12-01", "2026-07-25", "2027-01-01", "2020-01-01", "not-a-date"]

    @pytest.mark.parametrize("bad", REFUSED)
    def test_M01_refused_date_returns_nonzero(self, monkeypatch, db_path, no_collect, bad):
        monkeypatch.setattr(sys, "argv", _argv(db_path, bad, authorized=True))
        assert _run_daily.main() == 1

    @pytest.mark.parametrize("bad", REFUSED)
    def test_M02_refused_date_writes_nothing(self, monkeypatch, db_path, no_collect, bad):
        monkeypatch.setattr(sys, "argv", _argv(db_path, bad, authorized=True))
        _run_daily.main()
        assert _all_tables_empty(db_path)

    @pytest.mark.parametrize("bad", REFUSED)
    def test_M03_refused_date_never_starts_collection(self, monkeypatch, db_path, no_collect, bad):
        monkeypatch.setattr(sys, "argv", _argv(db_path, bad, authorized=True))
        _run_daily.main()
        assert no_collect == [], "run_all must not be called for a refused date"

    def test_M04_refusal_precedes_the_governance_check(self, monkeypatch, db_path):
        """
        Ordering is load-bearing: _record_governance_check_failure WRITES rows,
        so if governance ran first a refused date would violate "write nothing".
        """
        gov_calls: list = []
        monkeypatch.setattr(_run_daily, "_check_governance",
                            lambda _: gov_calls.append(1) or (False, "should not run"))
        monkeypatch.setattr(_run_daily, "run_all", lambda *a, **kw: {})
        monkeypatch.setattr(sys, "argv", _argv(db_path, "2025-12-01", authorized=True))
        assert _run_daily.main() == 1
        assert gov_calls == [], "governance must not run for a refused date"
        assert _all_tables_empty(db_path)

    # -- accepted paths ---------------------------------------------------

    def test_M05_scheduled_path_needs_no_run_date_arg(self, monkeypatch, db_path, no_collect):
        monkeypatch.setattr(sys, "argv", _argv(db_path))
        assert _run_daily.main() == 0
        assert len(no_collect) == 1
        assert no_collect[0][0][0] == _yesterday(), "scheduled run_date must be yesterday UTC"

    def test_M06_scheduled_records_scheduled_provenance(self, monkeypatch, db_path, no_collect):
        monkeypatch.setattr(sys, "argv", _argv(db_path))
        _run_daily.main()
        con = sqlite3.connect(db_path)
        rows = con.execute("SELECT run_date, mode FROM collection_provenance").fetchall()
        con.close()
        assert rows == [(_yesterday(), MODE_SCHEDULED)]

    def test_M07_explicit_yesterday_is_still_scheduled_and_needs_no_flag(
        self, monkeypatch, db_path, no_collect
    ):
        monkeypatch.setattr(sys, "argv", _argv(db_path, _yesterday()))
        assert _run_daily.main() == 0
        con = sqlite3.connect(db_path)
        mode = con.execute("SELECT mode FROM collection_provenance").fetchone()[0]
        con.close()
        assert mode == MODE_SCHEDULED

    def test_M08_recent_backfill_refused_without_authorization(
        self, monkeypatch, db_path, no_collect
    ):
        target = (datetime.now(timezone.utc).date() - timedelta(days=3)).isoformat()
        monkeypatch.setattr(sys, "argv", _argv(db_path, target))
        assert _run_daily.main() == 1
        assert _all_tables_empty(db_path)
        assert no_collect == []

    def test_M09_same_date_succeeds_with_authorization(self, monkeypatch, db_path, no_collect):
        target = (datetime.now(timezone.utc).date() - timedelta(days=3)).isoformat()
        monkeypatch.setattr(sys, "argv", _argv(db_path, target, authorized=True))
        assert _run_daily.main() == 0
        assert len(no_collect) == 1 and no_collect[0][0][0] == target

    def test_M10_authorized_backfill_provenance_is_distinguishable(
        self, monkeypatch, db_path, no_collect
    ):
        """Requirement 8: a backfill must never look like scheduled evidence."""
        target = (datetime.now(timezone.utc).date() - timedelta(days=3)).isoformat()
        monkeypatch.setattr(sys, "argv", _argv(db_path, target, authorized=True))
        _run_daily.main()
        con = sqlite3.connect(db_path)
        row = con.execute(
            "SELECT mode, detail FROM collection_provenance WHERE run_date=?", (target,)
        ).fetchone()
        con.close()
        assert row[0] == MODE_BACKFILL != MODE_SCHEDULED
        assert "authorized backfill" in row[1]

    def test_M11_provenance_is_idempotent(self, monkeypatch, db_path, no_collect):
        monkeypatch.setattr(sys, "argv", _argv(db_path))
        _run_daily.main()
        _run_daily.main()
        con = sqlite3.connect(db_path)
        n = con.execute("SELECT COUNT(*) FROM collection_provenance").fetchone()[0]
        con.close()
        assert n == 1, "re-invocation must not duplicate provenance rows"

    def test_M12_dry_run_writes_no_provenance(self, monkeypatch, db_path, no_collect):
        monkeypatch.setattr(sys, "argv", _argv(db_path, dry_run=True))
        assert _run_daily.main() == 0
        assert _all_tables_empty(db_path)

    def test_M13_authorization_flag_alone_does_not_change_scheduled_mode(
        self, monkeypatch, db_path, no_collect
    ):
        monkeypatch.setattr(sys, "argv", _argv(db_path, authorized=True))
        _run_daily.main()
        con = sqlite3.connect(db_path)
        mode = con.execute("SELECT mode FROM collection_provenance").fetchone()[0]
        con.close()
        assert mode == MODE_SCHEDULED

    # -- observability (B-09's half) --------------------------------------

    def test_M14_manual_run_collecting_nothing_returns_nonzero(
        self, monkeypatch, db_path
    ):
        target = (datetime.now(timezone.utc).date() - timedelta(days=3)).isoformat()
        monkeypatch.setattr(_run_daily, "_check_governance", lambda _: (True, "ok"))
        monkeypatch.setattr(_run_daily, "run_all",
                            lambda *a, **kw: {"h5_vol_regime": "unavailable"})
        monkeypatch.setattr(sys, "argv", _argv(db_path, target, authorized=True))
        assert _run_daily.main() == 1

    def test_M15_scheduled_run_with_one_unavailable_signal_still_succeeds(
        self, monkeypatch, db_path
    ):
        """
        Per-signal isolation is a documented guarantee: a DVOL or funding
        outage marks ONE signal 'unavailable' and must never fail the daily
        job. The manual-intent rule above must not erode it.
        """
        monkeypatch.setattr(_run_daily, "_check_governance", lambda _: (True, "ok"))
        monkeypatch.setattr(_run_daily, "run_all",
                            lambda *a, **kw: {"h5_vol_regime": "ok",
                                              "ivrv_vrp_spread": "unavailable"})
        monkeypatch.setattr(sys, "argv", _argv(db_path))
        assert _run_daily.main() == 0

    def test_M16_scheduled_run_all_unavailable_still_returns_zero(
        self, monkeypatch, db_path
    ):
        """A scheduled data outage is not an operator error; freshness alerting owns it."""
        monkeypatch.setattr(_run_daily, "_check_governance", lambda _: (True, "ok"))
        monkeypatch.setattr(_run_daily, "run_all",
                            lambda *a, **kw: {"h5_vol_regime": "unavailable"})
        monkeypatch.setattr(sys, "argv", _argv(db_path))
        assert _run_daily.main() == 0

    def test_M17_policy_config_failure_refuses_to_collect(
        self, monkeypatch, db_path, no_collect
    ):
        def boom():
            raise PolicyConfigError("simulated missing policy")
        monkeypatch.setattr(_run_daily, "load_policy", boom)
        monkeypatch.setattr(sys, "argv", _argv(db_path))
        assert _run_daily.main() == 1
        assert no_collect == [], "must not collect without an enforceable policy"
        assert _all_tables_empty(db_path)

    # -- all six signals --------------------------------------------------

    def test_M18_refusal_covers_every_configured_signal(self, monkeypatch, db_path, no_collect):
        """
        The choke point precedes per-signal dispatch, so a refusal protects
        every configured signal at once. Asserted against the real config so
        a newly registered signal is covered automatically.
        """
        cfg = yaml.safe_load(_run_daily._CONFIG_PATH.read_text(encoding="utf-8"))
        assert len(cfg["signals"]) == 7
        monkeypatch.setattr(sys, "argv", _argv(db_path, "2025-12-01", authorized=True))
        _run_daily.main()
        assert no_collect == []
        assert _all_tables_empty(db_path)


# ===========================================================================
# ADV — structural invariants a plausible refactor could silently break
# ===========================================================================

class TestAdversarialInvariants:

    def test_ADV01_authorization_cannot_unlock_any_protected_window(self, policy):
        """The whole safety argument: sealed windows ignore the flag."""
        for rd in ["2025-06-01", "2025-12-01", "2026-05-31",
                   "2026-07-18", "2026-07-25", "2026-08-05"]:
            assert not _ev(rd, policy, authorized=True).allowed, rd

    def test_ADV02_unconditional_checks_precede_authorization_in_source(self):
        """
        Ordering pinned structurally, not just behaviourally: if a refactor put
        the authorization branch first, the flag would become omnipotent.
        """
        src = (_RESEARCH_ROOT / "forward_evidence" / "run_date_policy.py").read_text(
            encoding="utf-8")
        body = src.split("def evaluate_run_date", 1)[1]
        assert body.index("REFUSE_LOCKBOX") < body.index("REFUSE_UNAUTHORIZED")
        assert body.index("REFUSE_IMMUTABLE_GAP") < body.index("REFUSE_UNAUTHORIZED")

    def test_ADV03_policy_engine_is_pure(self):
        """No DB, no filesystem, no clock inside the decision function."""
        src = (_RESEARCH_ROOT / "forward_evidence" / "run_date_policy.py").read_text(
            encoding="utf-8")
        body = src.split("def evaluate_run_date", 1)[1]
        for forbidden in ("sqlite3", "open(", "read_text", "datetime.now", "date.today"):
            assert forbidden not in body, f"{forbidden} must not appear in evaluate_run_date"

    def test_ADV04_choke_point_precedes_run_all_in_source(self):
        src = (_RESEARCH_ROOT / "forward_evidence" / "run_daily.py").read_text(encoding="utf-8")
        body = src.split("def main(", 1)[1]
        assert body.index("evaluate_run_date") < body.index("run_all(")

    def test_ADV05_provenance_never_written_before_the_decision(self):
        src = (_RESEARCH_ROOT / "forward_evidence" / "run_daily.py").read_text(encoding="utf-8")
        body = src.split("def main(", 1)[1]
        assert body.index("evaluate_run_date") < body.index("_record_collection_provenance")

    def test_ADV06_scheduled_derivation_is_still_yesterday_utc(self):
        expected = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
        assert _run_daily._derive_run_date() == expected
