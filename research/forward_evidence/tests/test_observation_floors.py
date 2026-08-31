"""
Gate FB-7 — observation-floor enforcement.

The floors themselves are frozen elsewhere (each family's hash-pinned
pre-registration). Nothing here defines, relaxes or defaults a floor; these
tests check that the evaluator reads the authoritative source, computes what it
can, and FAILS CLOSED on everything else.

The sabotage group is the point of this file: an evaluator that silently passes
an unpinned, altered, absent or uncomputable floor is worse than no evaluator,
because it manufactures permission.
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import sys

import pytest

_HERE = pathlib.Path(__file__).resolve().parent
_FWD = _HERE.parent
_RESEARCH_ROOT = _FWD.parent
_REPO = _RESEARCH_ROOT.parent
for _p in (str(_RESEARCH_ROOT),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from forward_evidence import observation_floors as OF  # noqa: E402

_SCHEMA = (_FWD / "schema_forward.sql").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Fixtures — a synthetic repo root with a pinned pre-registration
# ---------------------------------------------------------------------------

def _write_prereg(root: pathlib.Path, name: str, floors: dict | None,
                  signal_id: str = "fam_a") -> pathlib.Path:
    d = root / "research" / "forward_evidence"
    d.mkdir(parents=True, exist_ok=True)
    doc: dict = {"signal_id": signal_id, "version": "1.0.0"}
    if floors is not None:
        doc["observation_floors"] = floors
    p = d / name
    p.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return p


def _registry(root: pathlib.Path, family: str, prereg: pathlib.Path | None,
              hash_override: str | None = None) -> dict:
    if prereg is None:
        return {family: {"state": "candidate", "preregistration": None}}
    rel = prereg.relative_to(root).as_posix()
    h = hash_override or OF._sha256_lf(prereg)
    return {family: {"state": "candidate",
                     "preregistration": {"path": rel, "hash": h}}}


def _forward_db(path: pathlib.Path, signal_id: str, trades, run_dates) -> pathlib.Path:
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    for rd in run_dates:
        conn.execute("INSERT INTO run_log (signal_id, run_date, fn_hash, status) "
                     "VALUES (?,?,?,'ok')", (signal_id, rd, "h"))
    for entry_date, exit_date, direction in trades:
        cur = conn.execute(
            "INSERT INTO trade_entries (signal_id, entry_date, direction, "
            "entry_close_price, planned_exit_date, open) VALUES (?,?,?,?,?,0)",
            (signal_id, entry_date, direction, 100.0, exit_date))
        conn.execute("INSERT INTO trade_exits (entry_id, exit_date, "
                     "exit_close_price, cost_bps) VALUES (?,?,?,?)",
                     (cur.lastrowid, exit_date, 101.0, 15.0))
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def env(tmp_path):
    root = tmp_path / "repo"
    prereg = _write_prereg(root, "pre_registration_fam_a.json", {
        "minimum_trades": 3,
        "minimum_calendar_months_before_assessment": 2,
        "minimum_both_long_and_short_trades": True,
        "rationale": "prose that must not be treated as a floor",
    })
    reg = _registry(root, "fam_a", prereg)
    db = _forward_db(tmp_path / "fwd.db", "fam_a",
                     trades=[("2026-01-05", "2026-01-12", 1),
                             ("2026-01-20", "2026-01-27", -1),
                             ("2026-02-03", "2026-02-10", 1)],
                     run_dates=["2026-01-05", "2026-01-20", "2026-02-03", "2026-03-06"])
    return {"root": root, "prereg": prereg, "registry": reg, "db": db}


# ---------------------------------------------------------------------------
# 1. The happy path
# ---------------------------------------------------------------------------

class TestSatisfied:
    def test_all_declared_floors_met(self, env):
        r = OF.evaluate("fam_a", env["registry"], env["db"], "2026-03-06",
                        repo_root=env["root"])
        assert r.error is None
        assert r.floors_satisfied is True, [f"{x.name}:{x.status}" for x in r.results]
        assert r.blocking == []

    def test_prose_keys_are_not_floors(self, env):
        r = OF.evaluate("fam_a", env["registry"], env["db"], "2026-03-06",
                        repo_root=env["root"])
        assert "rationale" not in {x.name for x in r.results}
        assert len(r.results) == 3

    def test_report_names_the_pinned_document(self, env):
        r = OF.evaluate("fam_a", env["registry"], env["db"], "2026-03-06",
                        repo_root=env["root"])
        assert r.prereg_path.endswith("pre_registration_fam_a.json")


# ---------------------------------------------------------------------------
# 2. Floors that are genuinely not met
# ---------------------------------------------------------------------------

class TestNotMet:
    def test_too_few_trades(self, env):
        r = OF.evaluate("fam_a", env["registry"], env["db"], "2026-01-12",
                        repo_root=env["root"])
        assert r.floors_satisfied is False
        got = {x.name: x for x in r.results}
        assert got["minimum_trades"].status == "NOT_MET"
        assert got["minimum_trades"].observed == 1

    def test_not_enough_calendar_months(self, env):
        r = OF.evaluate("fam_a", env["registry"], env["db"], "2026-02-10",
                        repo_root=env["root"])
        got = {x.name: x for x in r.results}
        assert got["minimum_calendar_months_before_assessment"].observed == 1
        assert got["minimum_calendar_months_before_assessment"].status == "NOT_MET"
        assert r.floors_satisfied is False

    def test_one_sided_trades_fail_the_both_direction_floor(self, tmp_path):
        root = tmp_path / "r"
        pre = _write_prereg(root, "p.json", {
            "minimum_trades": 2, "minimum_both_long_and_short_trades": True})
        db = _forward_db(tmp_path / "f.db", "fam_a",
                         trades=[("2026-01-05", "2026-01-12", 1),
                                 ("2026-01-20", "2026-01-27", 1)],
                         run_dates=["2026-01-05"])
        r = OF.evaluate("fam_a", _registry(root, "fam_a", pre), db,
                        "2026-06-01", repo_root=root)
        got = {x.name: x for x in r.results}
        assert got["minimum_both_long_and_short_trades"].status == "NOT_MET"
        assert got["minimum_trades"].status == "MET"
        assert r.floors_satisfied is False

    def test_both_direction_false_is_recorded_not_dropped(self, tmp_path):
        """A pre-registration that does not require both directions is honoured,
        but the decision is still reported rather than silently omitted."""
        root = tmp_path / "r"
        pre = _write_prereg(root, "p.json", {
            "minimum_trades": 1, "minimum_both_long_and_short_trades": False})
        db = _forward_db(tmp_path / "f.db", "fam_a",
                         trades=[("2026-01-05", "2026-01-12", 1)],
                         run_dates=["2026-01-05"])
        r = OF.evaluate("fam_a", _registry(root, "fam_a", pre), db,
                        "2026-06-01", repo_root=root)
        got = {x.name: x for x in r.results}
        assert got["minimum_both_long_and_short_trades"].status == "MET"
        assert "not required" in got["minimum_both_long_and_short_trades"].detail

    def test_active_days_floor(self, tmp_path):
        root = tmp_path / "r"
        pre = _write_prereg(root, "p.json", {"minimum_active_days": 5})
        db = _forward_db(tmp_path / "f.db", "fam_a", trades=[],
                         run_dates=["2026-01-0%d" % i for i in range(1, 4)])
        r = OF.evaluate("fam_a", _registry(root, "fam_a", pre), db,
                        "2026-06-01", repo_root=root)
        assert r.results[0].name == "minimum_active_days"
        assert r.results[0].observed == 3 and r.results[0].status == "NOT_MET"


# ---------------------------------------------------------------------------
# 3. SABOTAGE — every one of these must FAIL CLOSED, never pass
# ---------------------------------------------------------------------------

class TestFailsClosed:
    def test_no_registry_pointer_is_not_a_pass(self, env):
        r = OF.evaluate("fam_a", _registry(env["root"], "fam_a", None),
                        env["db"], "2026-06-01", repo_root=env["root"])
        assert r.floors_satisfied is False
        assert "no registry preregistration pointer" in r.error

    def test_family_absent_from_registry_is_not_a_pass(self, env):
        r = OF.evaluate("ghost", env["registry"], env["db"], "2026-06-01",
                        repo_root=env["root"])
        assert r.floors_satisfied is False and "not in the registry" in r.error

    def test_amended_prereg_breaks_the_pin_and_fails(self, env):
        """The registry hash still names the ORIGINAL bytes. Editing the
        pre-registration after registration must be detected, not honoured."""
        doc = json.loads(env["prereg"].read_text(encoding="utf-8"))
        doc["observation_floors"]["minimum_trades"] = 1      # weaken it
        env["prereg"].write_text(json.dumps(doc, indent=2), encoding="utf-8")
        r = OF.evaluate("fam_a", env["registry"], env["db"], "2026-06-01",
                        repo_root=env["root"])
        assert r.floors_satisfied is False
        assert "hash mismatch" in r.error and "amended after registration" in r.error

    def test_missing_prereg_file_is_not_a_pass(self, env):
        env["prereg"].unlink()
        r = OF.evaluate("fam_a", env["registry"], env["db"], "2026-06-01",
                        repo_root=env["root"])
        assert r.floors_satisfied is False and "does not exist" in r.error

    def test_absent_observation_floors_block_is_not_a_pass(self, tmp_path):
        root = tmp_path / "r"
        pre = _write_prereg(root, "p.json", None)
        db = _forward_db(tmp_path / "f.db", "fam_a", trades=[], run_dates=["2026-01-01"])
        r = OF.evaluate("fam_a", _registry(root, "fam_a", pre), db,
                        "2026-06-01", repo_root=root)
        assert r.floors_satisfied is False
        assert "declares no observation_floors" in r.error

    def test_empty_observation_floors_block_is_not_a_pass(self, tmp_path):
        root = tmp_path / "r"
        pre = _write_prereg(root, "p.json", {})
        db = _forward_db(tmp_path / "f.db", "fam_a", trades=[], run_dates=["2026-01-01"])
        r = OF.evaluate("fam_a", _registry(root, "fam_a", pre), db,
                        "2026-06-01", repo_root=root)
        assert r.floors_satisfied is False

    def test_regime_floor_is_UNEVALUABLE_not_MET(self, tmp_path):
        """The forward corpus has no regime labelling. A declared regime floor
        must block, not be skipped."""
        root = tmp_path / "r"
        pre = _write_prereg(root, "p.json", {
            "minimum_trades": 1, "minimum_regimes_observed": 2})
        db = _forward_db(tmp_path / "f.db", "fam_a",
                         trades=[("2026-01-05", "2026-01-12", 1)],
                         run_dates=["2026-01-05"])
        r = OF.evaluate("fam_a", _registry(root, "fam_a", pre), db,
                        "2026-06-01", repo_root=root)
        got = {x.name: x for x in r.results}
        assert got["minimum_trades"].status == "MET"
        assert got["minimum_regimes_observed"].status == "UNEVALUABLE"
        assert r.floors_satisfied is False, "an uncomputable floor must block"

    def test_unknown_declared_floor_is_UNEVALUABLE_not_ignored(self, tmp_path):
        root = tmp_path / "r"
        pre = _write_prereg(root, "p.json", {
            "minimum_trades": 1, "minimum_something_invented_later": 4})
        db = _forward_db(tmp_path / "f.db", "fam_a",
                         trades=[("2026-01-05", "2026-01-12", 1)],
                         run_dates=["2026-01-05"])
        r = OF.evaluate("fam_a", _registry(root, "fam_a", pre), db,
                        "2026-06-01", repo_root=root)
        got = {x.name: x for x in r.results}
        assert got["minimum_something_invented_later"].status == "UNEVALUABLE"
        assert r.floors_satisfied is False

    def test_no_observations_makes_months_UNEVALUABLE(self, tmp_path):
        root = tmp_path / "r"
        pre = _write_prereg(root, "p.json", {
            "minimum_calendar_months_before_assessment": 1})
        db = _forward_db(tmp_path / "f.db", "fam_a", trades=[], run_dates=[])
        r = OF.evaluate("fam_a", _registry(root, "fam_a", pre), db,
                        "2026-06-01", repo_root=root)
        assert r.results[0].status == "UNEVALUABLE"
        assert r.floors_satisfied is False

    def test_empty_report_is_never_satisfied(self):
        assert OF.FloorReport("f", "2026-01-01", None, [], None).floors_satisfied is False


# ---------------------------------------------------------------------------
# 4. Month arithmetic
# ---------------------------------------------------------------------------

class TestMonths:
    @pytest.mark.parametrize("a,b,want", [
        ("2026-01-15", "2026-02-15", 1),
        ("2026-01-15", "2026-02-14", 0),     # partial month does not count
        ("2026-01-15", "2027-01-15", 12),
        ("2026-01-31", "2026-02-28", 0),
        ("2026-06-15", "2026-08-30", 2),
        ("2026-01-01", "2026-01-01", 0),
        ("2026-06-01", "2026-05-01", 0),     # never negative
    ])
    def test_whole_calendar_months(self, a, b, want):
        assert OF._months_between(a, b) == want


# ---------------------------------------------------------------------------
# 5. Boundaries and containment
# ---------------------------------------------------------------------------

class TestBoundaries:
    def test_exactly_at_the_floor_passes(self, tmp_path):
        root = tmp_path / "r"
        pre = _write_prereg(root, "p.json", {"minimum_trades": 2})
        db = _forward_db(tmp_path / "f.db", "fam_a",
                         trades=[("2026-01-05", "2026-01-12", 1),
                                 ("2026-01-20", "2026-01-27", -1)],
                         run_dates=["2026-01-05"])
        r = OF.evaluate("fam_a", _registry(root, "fam_a", pre), db,
                        "2026-06-01", repo_root=root)
        assert r.results[0].observed == 2 and r.results[0].status == "MET"

    def test_open_trades_never_count(self, tmp_path):
        root = tmp_path / "r"
        pre = _write_prereg(root, "p.json", {"minimum_trades": 2})
        db = tmp_path / "f.db"
        _forward_db(db, "fam_a", trades=[("2026-01-05", "2026-01-12", 1)],
                    run_dates=["2026-01-05"])
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO trade_entries (signal_id, entry_date, direction, "
                     "entry_close_price, planned_exit_date, open) "
                     "VALUES ('fam_a','2026-02-01',1,100.0,'2026-02-08',1)")
        conn.commit(); conn.close()
        r = OF.evaluate("fam_a", _registry(root, "fam_a", pre), db,
                        "2026-06-01", repo_root=root)
        assert r.results[0].observed == 1 and r.results[0].status == "NOT_MET"

    def test_as_of_truncates_later_exits(self, env):
        r = OF.evaluate("fam_a", env["registry"], env["db"], "2026-01-27",
                        repo_root=env["root"])
        assert {x.name: x for x in r.results}["minimum_trades"].observed == 2

    def test_module_writes_nothing_and_has_no_promotion_surface(self):
        src = pathlib.Path(OF.__file__).read_text(encoding="utf-8")
        body = src.split('"""', 2)[-1]
        for banned in ("append_run", "append_transition", "append_gate_result",
                       "INSERT", "UPDATE", "DELETE", "promote", "dsr_prob", "bh_p"):
            assert banned not in body, f"{banned} must not appear in this module"

    def test_not_imported_by_run_daily(self):
        """Scheduled collection must remain unaffected by this gate."""
        rd = (_FWD / "run_daily.py").read_text(encoding="utf-8")
        assert "observation_floors" not in rd
