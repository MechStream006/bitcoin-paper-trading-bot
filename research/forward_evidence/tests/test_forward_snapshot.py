"""
Gate FB-9 — forward evidence snapshot producer.

A snapshot appends one L2 run carrying a frozen admissibility payload. These
tests check that it appends and never rewrites, that it refuses to run
unattended, that the payload it freezes is the payload FB-8 later reads, and
that the resulting run is still blocked at Validated.
"""
from __future__ import annotations

import ast
import json
import pathlib
import sqlite3
import sys

import pytest

_HERE = pathlib.Path(__file__).resolve().parent
_FWD = _HERE.parent
_RESEARCH_ROOT = _FWD.parent
_EV_LIB = _RESEARCH_ROOT / "evidence_library"
if str(_RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_ROOT))

from forward_evidence import forward_admissibility as FA  # noqa: E402
from forward_evidence import forward_snapshot as FS  # noqa: E402
from forward_evidence import observation_floors as OF  # noqa: E402

if not _EV_LIB.exists():
    pytest.skip("evidence_library not present (Dockerfile.research excludes it "
                "by INV-1)", allow_module_level=True)
if str(_EV_LIB) not in sys.path:
    sys.path.insert(0, str(_EV_LIB))

import evidence_governance as EG  # noqa: E402
import record  # noqa: E402

_SCHEMA = (_FWD / "schema_forward.sql").read_text(encoding="utf-8")
RULE = FA.ForwardAdmissibilityRule()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _prereg(root, floors, name="p.json"):
    d = root / "research" / "forward_evidence"
    d.mkdir(parents=True, exist_ok=True)
    doc = {"signal_id": "fam_a", "version": "1.0.0"}
    if floors is not None:
        doc["observation_floors"] = floors
    p = d / name
    p.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return p


def _registry(root, prereg, hash_override=None):
    if prereg is None:
        return {"fam_a": {"state": "candidate", "preregistration": None}}
    return {"fam_a": {"state": "candidate", "preregistration": {
        "path": prereg.relative_to(root).as_posix(),
        "hash": hash_override or OF._sha256_lf(prereg)}}}


def _fwd_db(path, trades, run_dates, signal_id="fam_a"):
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    for rd in run_dates:
        conn.execute("INSERT INTO run_log (signal_id, run_date, fn_hash, status) "
                     "VALUES (?,?,?,'ok')", (signal_id, rd, "abc123"))
    for e, x, d in trades:
        cur = conn.execute(
            "INSERT INTO trade_entries (signal_id, entry_date, direction, "
            "entry_close_price, planned_exit_date, open) VALUES (?,?,?,?,?,0)",
            (signal_id, e, d, 100.0, x))
        conn.execute("INSERT INTO trade_exits (entry_id, exit_date, exit_close_price, "
                     "cost_bps) VALUES (?,?,?,?)", (cur.lastrowid, x, 108.0, 15.0))
    conn.commit(); conn.close()
    return path


TRADES = [("2026-01-05", "2026-01-12", 1),
          ("2026-01-20", "2026-01-27", -1),
          ("2026-02-03", "2026-02-10", 1)]
RUNS = ["2026-01-05", "2026-01-20", "2026-02-03", "2026-03-06"]


@pytest.fixture()
def env(tmp_path):
    root = tmp_path / "repo"
    pre = _prereg(root, {"minimum_trades": 3,
                         "minimum_calendar_months_before_assessment": 2,
                         "minimum_both_long_and_short_trades": True})
    led = tmp_path / "ledger.db"
    EG.init_db(led)   # full governance schema, so 'untouched' is checkable
    return {"root": root, "prereg": pre, "registry": _registry(root, pre),
            "fwd": _fwd_db(tmp_path / "f.db", TRADES, RUNS), "led": led}


def _write(env, as_of="2026-03-06", **kw):
    kw.setdefault("actor", "rohith")
    return FS.write_snapshot("fam_a", "fam_a", env["registry"], env["fwd"],
                             env["led"], as_of, repo_root=env["root"], **kw)


def _l2(led):
    con = sqlite3.connect(f"file:{pathlib.Path(led).as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM runs WHERE provenance='live-forward' "
                       "ORDER BY window_end").fetchall()
    con.close()
    return rows


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------

class TestPayload:
    def test_payload_is_complete(self, env):
        run_id, outcome = _write(env)
        assert outcome == "written" and run_id
        m = record.get_run(env["led"], run_id)["metrics"]
        p = m[FA.FORWARD_ADMISSIBILITY_KEY]
        assert p["schema"] == FA.PAYLOAD_SCHEMA
        assert p["as_of"] == "2026-03-06"
        assert (p["provenance"], p["run_kind"]) == ("live-forward", "forward")
        assert p["prereg_pin_verified"] is True and p["error"] is None
        assert {f["name"] for f in p["floors"]} == {
            "minimum_trades", "minimum_calendar_months_before_assessment",
            "minimum_both_long_and_short_trades"}
        assert m["closed_trades_only"] is True
        assert m["source"] == "evidence_forward.db:trade_returns"

    def test_run_columns_are_correct(self, env):
        run_id, _ = _write(env)
        r = record.get_run(env["led"], run_id)
        assert (r["provenance"], r["run_kind"]) == ("live-forward", "forward")
        assert r["window_end"] == "2026-03-06"
        assert r["window_start"] == "2026-01-05"          # first ok run_date
        assert r["engine_version"] == FS.ENGINE_VERSION   # distinct from the collector
        assert r["n_trials"] == 34
        assert r["fn_hash"] == "abc123"

    def test_payload_is_deterministic(self, env, tmp_path):
        a = FS.build_snapshot_metrics("fam_a", "fam_a", env["registry"], env["fwd"],
                                      "2026-03-06", repo_root=env["root"])
        b = FS.build_snapshot_metrics("fam_a", "fam_a", env["registry"], env["fwd"],
                                      "2026-03-06", repo_root=env["root"])
        assert a == b
        assert json.loads(json.dumps(a, default=str)) == json.loads(
            json.dumps(b, default=str))

    def test_readiness_present_and_no_dsr_bh(self, env):
        """FB-12g reversed half of this test's original contract.

        The readiness block IS now written (governance-approved Option B); the
        DSR/BH half is unchanged and still load-bearing.
        """
        run_id, _ = _write(env)
        r = record.get_run(env["led"], run_id)
        assert "readiness" in r["metrics"]
        assert r["dsr_prob"] is None and r["bh_p"] is None
        assert r["oos_sharpe"] is None and r["verdict"] is None


# ---------------------------------------------------------------------------
# Append-only behaviour
# ---------------------------------------------------------------------------

class TestAppendOnly:
    def test_same_as_of_is_idempotent(self, env):
        id1, o1 = _write(env)
        id2, o2 = _write(env)
        assert o1 == "written" and o2 == "skipped" and id1 == id2
        assert len(_l2(env["led"])) == 1

    def test_new_as_of_appends(self, env):
        _write(env, as_of="2026-02-10")
        _write(env, as_of="2026-03-06")
        rows = _l2(env["led"])
        assert len(rows) == 2
        assert [r["window_end"] for r in rows] == ["2026-02-10", "2026-03-06"]

    def test_zero_closed_trades_writes_nothing(self, env):
        run_id, outcome = _write(env, as_of="2026-01-06")
        assert outcome == "no-evidence" and run_id is None
        assert _l2(env["led"]) == []

    def test_dry_run_writes_nothing(self, env):
        run_id, outcome = _write(env, dry_run=True)
        assert outcome == "dry-run" and run_id is None
        assert _l2(env["led"]) == []

    def test_existing_rows_are_never_rewritten(self, env):
        """A pre-existing L2 row -- the analogue of the 10 historical rows --
        must be byte-identical after a later snapshot at a different as_of."""
        old = record.append_run(
            env["led"], family_id="fam_a", metrics={"n_trades": 7, "sharpe": 4.13},
            provenance="live-forward", run_kind="forward",
            window_start="2026-01-05", window_end="2026-02-10",
            engine_version="forward-collect-v1")
        before = dict(record.get_run(env["led"], old))
        _write(env, as_of="2026-03-06")
        assert dict(record.get_run(env["led"], old)) == before

    def test_offline_runs_are_untouched(self, env):
        disc = record.append_run(env["led"], family_id="fam_a",
                                 metrics={"sharpe": 0.9, "n_trades": 29},
                                 provenance="offline-corpus", run_kind="discovery")
        before = dict(record.get_run(env["led"], disc))
        _write(env)
        assert dict(record.get_run(env["led"], disc)) == before


# ---------------------------------------------------------------------------
# Operator boundary
# ---------------------------------------------------------------------------

class TestActor:
    @pytest.mark.parametrize("bad", ["service", "cron", "system", "SERVICE"])
    def test_non_human_actor_is_refused(self, env, bad):
        with pytest.raises(FS.ActorRefusedError):
            _write(env, actor_type=bad)
        assert _l2(env["led"]) == []

    def test_empty_actor_is_refused(self, env):
        with pytest.raises(FS.ActorRefusedError):
            _write(env, actor="")
        assert _l2(env["led"]) == []

    def test_refusal_happens_before_any_write(self, env):
        try:
            _write(env, actor_type="service")
        except FS.ActorRefusedError:
            pass
        assert _l2(env["led"]) == []


# ---------------------------------------------------------------------------
# The payload as FB-8 later reads it
# ---------------------------------------------------------------------------

class TestAdmissibilityOfTheResult:
    def test_met_floors_pass_the_rule(self, env):
        run_id, _ = _write(env)
        m = record.get_run(env["led"], run_id)["metrics"]
        ok, gates = RULE.evaluate({}, m, {})
        assert ok is True, [g for g in gates if not g["passed"]]

    def test_not_met_floors_fail_the_rule(self, tmp_path):
        root = tmp_path / "r"
        pre = _prereg(root, {"minimum_trades": 99})
        led = tmp_path / "l.db"; EG.init_db(led)
        fwd = _fwd_db(tmp_path / "f.db", TRADES, RUNS)
        run_id, _ = FS.write_snapshot("fam_a", "fam_a", _registry(root, pre), fwd,
                                      led, "2026-03-06", actor="rohith", repo_root=root)
        ok, gates = RULE.evaluate({}, record.get_run(led, run_id)["metrics"], {})
        assert ok is False
        assert any(g["gate_code"] == "FLOOR_minimum_trades" and not g["passed"]
                   for g in gates)

    def test_unevaluable_floor_fails_the_rule(self, tmp_path):
        root = tmp_path / "r"
        pre = _prereg(root, {"minimum_trades": 1, "minimum_regimes_observed": 2})
        led = tmp_path / "l.db"; EG.init_db(led)
        fwd = _fwd_db(tmp_path / "f.db", TRADES, RUNS)
        run_id, _ = FS.write_snapshot("fam_a", "fam_a", _registry(root, pre), fwd,
                                      led, "2026-03-06", actor="rohith", repo_root=root)
        ok, _ = RULE.evaluate({}, record.get_run(led, run_id)["metrics"], {})
        assert ok is False

    def test_missing_pin_produces_a_fail_closed_payload(self, tmp_path):
        root = tmp_path / "r"
        _prereg(root, {"minimum_trades": 1})
        led = tmp_path / "l.db"; EG.init_db(led)
        fwd = _fwd_db(tmp_path / "f.db", TRADES, RUNS)
        run_id, outcome = FS.write_snapshot("fam_a", "fam_a", _registry(root, None),
                                            fwd, led, "2026-03-06", actor="rohith",
                                            repo_root=root)
        assert outcome == "written"          # the row is written, honestly labelled
        p = record.get_run(led, run_id)["metrics"][FA.FORWARD_ADMISSIBILITY_KEY]
        assert p["prereg_pin_verified"] is False and p["error"]
        assert RULE.evaluate({}, record.get_run(led, run_id)["metrics"], {})[0] is False

    def test_hash_mismatch_produces_a_fail_closed_payload(self, tmp_path):
        root = tmp_path / "r"
        pre = _prereg(root, {"minimum_trades": 1})
        led = tmp_path / "l.db"; EG.init_db(led)
        fwd = _fwd_db(tmp_path / "f.db", TRADES, RUNS)
        reg = _registry(root, pre, hash_override="0" * 64)
        run_id, _ = FS.write_snapshot("fam_a", "fam_a", reg, fwd, led,
                                      "2026-03-06", actor="rohith", repo_root=root)
        p = record.get_run(led, run_id)["metrics"][FA.FORWARD_ADMISSIBILITY_KEY]
        assert p["prereg_pin_verified"] is False and "hash mismatch" in p["error"]
        assert RULE.evaluate({}, record.get_run(led, run_id)["metrics"], {})[0] is False

    def test_payload_is_frozen_when_the_corpus_grows(self, env):
        run_id, _ = _write(env)
        before = record.get_run(env["led"], run_id)["metrics"]
        conn = sqlite3.connect(env["fwd"])
        for _ in range(25):
            cur = conn.execute(
                "INSERT INTO trade_entries (signal_id, entry_date, direction, "
                "entry_close_price, planned_exit_date, open) "
                "VALUES ('fam_a','2026-04-01',-1,100.0,'2026-04-08',0)")
            conn.execute("INSERT INTO trade_exits (entry_id, exit_date, "
                         "exit_close_price, cost_bps) VALUES (?,'2026-04-08',80.0,15.0)",
                         (cur.lastrowid,))
        conn.commit(); conn.close()
        after = record.get_run(env["led"], run_id)["metrics"]
        assert after == before
        assert RULE.evaluate({}, after, {}) == RULE.evaluate({}, before, {})


# ---------------------------------------------------------------------------
# Still blocked at Validated
# ---------------------------------------------------------------------------

class TestStillBlockedAtValidated:
    def test_dsr_bh_absent_blocks_validated(self, env):
        import lifecycle
        run_id, _ = _write(env)
        run = record.get_run(env["led"], run_id)
        th = {"validated": {"feature": {"requires_non_offline_provenance": True,
                                        "min_dsr_prob": 0.50, "max_bh_p": 0.10}}}
        ok, errors = lifecycle.check_validated_preconditions(
            {"role": ["feature"]}, run, th)
        assert ok is False
        assert any("dsr" in e.lower() for e in errors), errors

    def test_readiness_is_now_present_and_consumed_via_the_run_block(self, env):
        """FB-12g: this test previously asserted readiness was ABSENT and the
        gate therefore could not pass. Option B reversed that deliberately --
        the block is embedded at run creation and consumed via the run-block
        path. The gate's verdict now depends on the window's own completeness,
        which is the point.
        """
        import readiness_gate
        run_id, _ = _write(env)
        run = record.get_run(env["led"], run_id)
        a = readiness_gate.DEFAULT_PROVIDER.assess(env["led"], run)
        assert a.source == "run_block"
        assert "readiness" in run["metrics"]


# ---------------------------------------------------------------------------
# No governance side effects; scheduler isolation
# ---------------------------------------------------------------------------

class TestNoSideEffects:
    def test_only_the_runs_table_changes(self, env):
        def counts():
            con = sqlite3.connect(f"file:{env['led'].as_posix()}?mode=ro", uri=True)
            q = lambda s: con.execute(s).fetchone()[0]
            out = {t: q(f"SELECT COUNT(*) FROM {t}") for t in
                   ("runs", "state_transitions", "gate_results",
                    "governance_receipts", "pairwise_r")}
            con.close(); return out
        before = counts()
        _write(env)
        after = counts()
        assert after["runs"] == before["runs"] + 1
        for t in ("state_transitions", "gate_results", "governance_receipts",
                  "pairwise_r"):
            assert after[t] == before[t], t

    def test_module_has_no_promotion_surface(self):
        tree = ast.parse(pathlib.Path(FS.__file__).read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body \
                    and isinstance(n.body[0], ast.Expr) \
                    and isinstance(n.body[0].value, ast.Constant) \
                    and isinstance(n.body[0].value.value, str):
                n.body = n.body[1:] or [ast.Pass()]
        code = ast.unparse(tree)
        for banned in ("admit_evidence", "reject_evidence", "pin_evidence",
                       "repin_evidence", "append_transition", "append_gate_result",
                       "append_receipt", "promote", "UPDATE", "DELETE",
                       # "readiness" was banned here until FB-12g; the block is
                       # now written deliberately. Every other token stands,
                       # dsr_prob and bh_p emphatically included.
                       "dsr_prob", "bh_p"):
            assert banned not in code, f"{banned} must not appear in executable code"

    def test_scheduler_does_not_import_the_producer(self):
        for mod in ("run_daily.py", "ledger_sync.py"):
            src = (_FWD / mod).read_text(encoding="utf-8")
            assert "forward_snapshot" not in src, mod

    def test_governance_is_never_passed_to_append_run(self):
        src = pathlib.Path(FS.__file__).read_text(encoding="utf-8")
        assert "governance=None," in src
        assert "governance={" not in src
