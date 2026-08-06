"""
Tests for research/label_store/activation.py (ADR-I011 §12.1, DI4;
WBS 3.11 -- owner-authorised, not roadmap-ratified).

THE CENTRAL TEST IS TestSafetyInversion. `registry.activate_
specification()` is fail-closed and IRREVERSIBLE: a failing check
transitions Registered -> Rejected permanently, with no arc back and no
recovery (re-registering identical content returns the same content hash
and leaves the row Rejected). This module must therefore never hand a
failing check to the registry. That property is what these tests exist
to prove -- not merely that activation works when equivalence holds.

Second in importance is the non-vacuous guard: a Specification with zero
declared values would make any comparison pass trivially (0 == 0) and
activate a binding that was never demonstrated.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_LABEL_STORE_DIR = _REPO_ROOT / "research" / "label_store"
REAL_LABEL_STORE_DB = _LABEL_STORE_DIR / "label_store.db"
REAL_H6_DB = _REPO_ROOT / "research" / "data" / "forward_evidence.db"


def _load(module_file: Path, registered_name: str):
    if registered_name in sys.modules:
        return sys.modules[registered_name]
    spec = importlib.util.spec_from_file_location(registered_name, module_file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[registered_name] = mod
    spec.loader.exec_module(mod)
    return mod


reg = _load(_LABEL_STORE_DIR / "registry.py", "label_store_registry")
vs = _load(_LABEL_STORE_DIR / "value_store.py", "label_store_value_store")
act = _load(_LABEL_STORE_DIR / "activation.py", "label_store_activation")

NOW = "2026-08-06T00:00:00+00:00"
DECLARED = "2026-01-01T00:00:00+00:00"
MATURED = "2026-02-01T00:00:00+00:00"


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _make_spec(db_path, *, horizon_days=5, binding_id=act.BINDING_H6_TRADE_LABELS,
               definition_name="test_outcome"):
    existing = reg.get_latest_definition(db_path, definition_name)
    version = existing["version"] if existing else reg.register_definition(
        db_path, name=definition_name, rationale="r" * 40, interpretation="i" * 40,
        actor="rohith", registered_at=NOW,
    ).version
    return reg.register_specification(
        db_path,
        definition_name=definition_name, definition_version=version,
        horizon_days=horizon_days, horizon_governance="calendar_span",
        fill_convention="f" * 20, cost_convention="c" * 20,
        direction_convention="d" * 20, maturity_rule="m" * 20,
        validity_condition="v" * 20, conditioning_propagation="preserving",
        binding_id=binding_id, engine_version="v1",
        actor="register_conventions", registered_at=NOW,
    )


def _declare_matured(db_path, spec_hash, trade_id, value):
    subject_id = act._H6_SUBJECT_ID_TEMPLATE.format(trade_id=trade_id)
    vs.declare_forward_label(db_path, spec_hash, subject_id, DECLARED, declared_by="test")
    vs.observe_forward_label(db_path, spec_hash, subject_id, DECLARED, observed_at=DECLARED)
    vs.mature_forward_label(
        db_path, spec_hash, subject_id, DECLARED,
        outcome={act._OUTCOME_FIELD: value}, matured_at=MATURED,
        input_revision_set=["synthetic"],
    )
    return subject_id


def _make_h6(path: Path, rows: list[tuple[int, int, float]], available=1):
    """rows: (trade_id, horizon_days, forward_return)"""
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE IF NOT EXISTS trade_labels ("
        "trade_id INTEGER, horizon_days INTEGER, label_day_ts TEXT, "
        "forward_return REAL, label_available INTEGER, labeled_at TEXT)"
    )
    con.executemany(
        "INSERT INTO trade_labels (trade_id, horizon_days, forward_return, label_available) "
        "VALUES (?,?,?,?)",
        [(t, h, v, available) for t, h, v in rows],
    )
    con.commit()
    con.close()
    return path


@pytest.fixture
def db_path(tmp_path) -> Path:
    p = tmp_path / "label_store.db"
    reg.init_db(p)
    vs.init_db(p)
    return p


@pytest.fixture
def h6_db(tmp_path) -> Path:
    return tmp_path / "forward_evidence.db"


# ---------------------------------------------------------------------------
# equivalence_report()
# ---------------------------------------------------------------------------

class TestEquivalenceReport:
    def test_identical_values_are_ok(self, db_path, h6_db):
        h = _make_spec(db_path, horizon_days=5)
        _declare_matured(db_path, h, 1, 0.10)
        _declare_matured(db_path, h, 2, -0.05)
        _make_h6(h6_db, [(1, 5, 0.10), (2, 5, -0.05)])
        r = act.equivalence_report(db_path, reg.get_specification(db_path, h), h6_db)
        assert r["ok"] and r["compared"] == 2 and r["matched"] == 2
        assert r["mismatched"] == []

    def test_a_single_differing_value_fails(self, db_path, h6_db):
        h = _make_spec(db_path, horizon_days=5)
        _declare_matured(db_path, h, 1, 0.10)
        _declare_matured(db_path, h, 2, -0.05)
        _make_h6(h6_db, [(1, 5, 0.10), (2, 5, -0.06)])   # 2 differs
        r = act.equivalence_report(db_path, reg.get_specification(db_path, h), h6_db)
        assert not r["ok"]
        assert len(r["mismatched"]) == 1
        assert r["mismatched"][0]["subject_id"] == "h6-trade-2"

    def test_subject_absent_from_h6_fails(self, db_path, h6_db):
        h = _make_spec(db_path, horizon_days=5)
        _declare_matured(db_path, h, 1, 0.10)
        _declare_matured(db_path, h, 9, 0.20)
        _make_h6(h6_db, [(1, 5, 0.10)])
        r = act.equivalence_report(db_path, reg.get_specification(db_path, h), h6_db)
        assert not r["ok"] and r["missing_in_source"] == ["h6-trade-9"]

    def test_empty_population_is_refused_not_vacuously_passed(self, db_path, h6_db):
        """0 == 0 must never activate a never-demonstrated binding."""
        h = _make_spec(db_path, horizon_days=5)
        _make_h6(h6_db, [(1, 5, 0.10)])
        r = act.equivalence_report(db_path, reg.get_specification(db_path, h), h6_db)
        assert not r["ok"] and r["compared"] == 0
        assert "vacuously" in r["reason"]

    def test_unregistered_binding_is_deferred(self, db_path, h6_db):
        h = _make_spec(db_path, horizon_days=5, binding_id="research_evidence.engine.ret_fwd1")
        _declare_matured(db_path, h, 1, 0.10)
        _make_h6(h6_db, [(1, 5, 0.10)])
        r = act.equivalence_report(db_path, reg.get_specification(db_path, h), h6_db)
        assert not r["ok"] and "no equivalence source" in r["reason"]

    def test_missing_h6_database_is_deferred(self, db_path, tmp_path):
        h = _make_spec(db_path, horizon_days=5)
        _declare_matured(db_path, h, 1, 0.10)
        r = act.equivalence_report(db_path, reg.get_specification(db_path, h), tmp_path / "absent.db")
        assert not r["ok"] and "not present" in r["reason"]

    def test_horizon_is_part_of_the_comparison(self, db_path, h6_db):
        """A 5-day Specification must not be satisfied by 7-day H6 values."""
        h = _make_spec(db_path, horizon_days=5)
        _declare_matured(db_path, h, 1, 0.10)
        _make_h6(h6_db, [(1, 7, 0.10)])   # right trade, wrong horizon
        r = act.equivalence_report(db_path, reg.get_specification(db_path, h), h6_db)
        assert not r["ok"] and r["missing_in_source"] == ["h6-trade-1"]

    def test_unavailable_h6_labels_are_excluded(self, db_path, h6_db):
        h = _make_spec(db_path, horizon_days=5)
        _declare_matured(db_path, h, 1, 0.10)
        _make_h6(h6_db, [(1, 5, 0.10)], available=0)
        r = act.equivalence_report(db_path, reg.get_specification(db_path, h), h6_db)
        assert not r["ok"] and r["missing_in_source"] == ["h6-trade-1"]

    def test_outcome_shape_change_fails_loudly(self, db_path, h6_db):
        """A matured row cannot be UPDATEd (WBS 3.2's fill-once trigger
        correctly refuses), so the malformed row is INSERTed directly --
        the only way this state could arise is a direct write bypassing
        the application layer, which is exactly what must fail loudly
        rather than compare against None."""
        h = _make_spec(db_path, horizon_days=5)
        _declare_matured(db_path, h, 1, 0.10)
        con = sqlite3.connect(db_path)
        con.execute(
            "INSERT INTO label_value_forward "
            "(spec_hash, subject_id, declared_at, declared_by, maturity_state, outcome_json) "
            "VALUES (?,?,?,?,?,?)",
            (h, "h6-trade-2", DECLARED, "direct-write", "Matured", json.dumps({"unexpected": 1.0})),
        )
        con.commit(); con.close()
        _make_h6(h6_db, [(1, 5, 0.10), (2, 5, 0.20)])
        with pytest.raises(act.ActivationError, match="forward_return"):
            act.equivalence_report(db_path, reg.get_specification(db_path, h), h6_db)


# ---------------------------------------------------------------------------
# THE SAFETY INVERSION -- a failing check must never reach the registry
# ---------------------------------------------------------------------------

class TestSafetyInversion:
    def test_failing_equivalence_leaves_phase_registered_not_rejected(self, db_path, h6_db):
        """The property this module exists for. Naively passing the check to
        activate_specification() would permanently Reject the Specification."""
        h = _make_spec(db_path, horizon_days=5)
        _declare_matured(db_path, h, 1, 0.10)
        _make_h6(h6_db, [(1, 5, 0.99)])   # mismatch

        assert act.main(["--db", str(db_path), "--h6-db", str(h6_db), "--commit"]) == 0
        assert reg.get_specification(db_path, h)["phase"] == "Registered"

    def test_empty_population_leaves_phase_registered_not_rejected(self, db_path, h6_db):
        h = _make_spec(db_path, horizon_days=5)
        _make_h6(h6_db, [(1, 5, 0.10)])
        assert act.main(["--db", str(db_path), "--h6-db", str(h6_db), "--commit"]) == 0
        assert reg.get_specification(db_path, h)["phase"] == "Registered"

    def test_no_specification_is_ever_rejected_by_this_module(self, db_path, h6_db):
        ok_h = _make_spec(db_path, horizon_days=5)
        bad_h = _make_spec(db_path, horizon_days=7)
        _declare_matured(db_path, ok_h, 1, 0.10)
        _declare_matured(db_path, bad_h, 1, 0.10)
        _make_h6(h6_db, [(1, 5, 0.10), (1, 7, 0.99)])

        assert act.main(["--db", str(db_path), "--h6-db", str(h6_db), "--commit"]) == 0
        con = sqlite3.connect(db_path)
        phases = {r[0] for r in con.execute("SELECT phase FROM label_specifications")}
        con.close()
        assert "Rejected" not in phases
        assert reg.get_specification(db_path, ok_h)["phase"] == "Active"
        assert reg.get_specification(db_path, bad_h)["phase"] == "Registered"

    def test_apply_refuses_when_equivalence_regresses_between_plan_and_apply(self, db_path, h6_db):
        """Guards the TOCTOU window: if the world changes after planning,
        apply() raises rather than letting the registry Reject."""
        h = _make_spec(db_path, horizon_days=5)
        _declare_matured(db_path, h, 1, 0.10)
        _make_h6(h6_db, [(1, 5, 0.10)])
        reports = act.plan(db_path, h6_db)
        assert reports[0]["ok"]

        con = sqlite3.connect(h6_db)   # break equivalence after planning
        con.execute("UPDATE trade_labels SET forward_return = 0.99")
        con.commit(); con.close()

        with pytest.raises(act.ActivationError, match="passed at plan time but fails now"):
            act.apply(db_path, reports, h6_db)
        assert reg.get_specification(db_path, h)["phase"] == "Registered"


# ---------------------------------------------------------------------------
# plan() / apply() / idempotency
# ---------------------------------------------------------------------------

class TestPlanAndApply:
    def test_plan_only_considers_registered_specifications(self, db_path, h6_db):
        h = _make_spec(db_path, horizon_days=5)
        _declare_matured(db_path, h, 1, 0.10)
        _make_h6(h6_db, [(1, 5, 0.10)])
        assert len(act.plan(db_path, h6_db)) == 1
        act.apply(db_path, act.plan(db_path, h6_db), h6_db)
        assert act.plan(db_path, h6_db) == []

    def test_activation_transitions_registered_to_active(self, db_path, h6_db):
        h = _make_spec(db_path, horizon_days=5)
        _declare_matured(db_path, h, 1, 0.10)
        _make_h6(h6_db, [(1, 5, 0.10)])
        assert reg.get_specification(db_path, h)["phase"] == "Registered"
        act.apply(db_path, act.plan(db_path, h6_db), h6_db)
        assert reg.get_specification(db_path, h)["phase"] == "Active"

    def test_second_commit_is_idempotent(self, db_path, h6_db):
        h = _make_spec(db_path, horizon_days=5)
        _declare_matured(db_path, h, 1, 0.10)
        _make_h6(h6_db, [(1, 5, 0.10)])
        assert act.main(["--db", str(db_path), "--h6-db", str(h6_db), "--commit"]) == 0
        after = _sha256(db_path)
        assert act.main(["--db", str(db_path), "--h6-db", str(h6_db), "--commit"]) == 0
        assert _sha256(db_path) == after
        assert reg.get_specification(db_path, h)["phase"] == "Active"


# ---------------------------------------------------------------------------
# Dry run, LR4, LR1
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_is_the_default_and_changes_nothing(self, db_path, h6_db):
        h = _make_spec(db_path, horizon_days=5)
        _declare_matured(db_path, h, 1, 0.10)
        _make_h6(h6_db, [(1, 5, 0.10)])
        before = _sha256(db_path)
        assert act.main(["--db", str(db_path), "--h6-db", str(h6_db)]) == 0
        assert _sha256(db_path) == before
        assert reg.get_specification(db_path, h)["phase"] == "Registered"

    def test_dry_run_takes_no_backup(self, db_path, h6_db, tmp_path):
        h = _make_spec(db_path, horizon_days=5)
        _declare_matured(db_path, h, 1, 0.10)
        _make_h6(h6_db, [(1, 5, 0.10)])
        assert act.main(["--db", str(db_path), "--h6-db", str(h6_db)]) == 0
        assert list(tmp_path.glob("*pre-activation*")) == []

    def test_commit_takes_a_backup(self, db_path, h6_db, tmp_path):
        h = _make_spec(db_path, horizon_days=5)
        _declare_matured(db_path, h, 1, 0.10)
        _make_h6(h6_db, [(1, 5, 0.10)])
        assert act.main(["--db", str(db_path), "--h6-db", str(h6_db), "--commit"]) == 0
        assert len(list(tmp_path.glob("*pre-activation*"))) == 1

    def test_missing_database_returns_1(self, tmp_path):
        assert act.main(["--db", str(tmp_path / "nope.db")]) == 1


class TestSchemaAssertionAndH6ReadOnly:
    def test_refuses_a_database_without_label_tables(self, tmp_path):
        foreign = tmp_path / "not_a_label_store.db"
        con = sqlite3.connect(foreign)
        con.execute("CREATE TABLE trade_labels (trade_id INTEGER)")
        con.commit(); con.close()
        with pytest.raises(ValueError, match="does not look like a Label Store"):
            act._assert_label_store_schema(foreign)

    def test_h6_connection_is_structurally_read_only(self, h6_db):
        """LR1 enforced by SQLite, not by convention."""
        _make_h6(h6_db, [(1, 5, 0.10)])
        con = act._connect_readonly(h6_db)
        try:
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                con.execute("UPDATE trade_labels SET forward_return = 0.99")
        finally:
            con.close()

    def test_h6_is_byte_identical_after_a_full_commit(self, db_path, h6_db):
        h = _make_spec(db_path, horizon_days=5)
        _declare_matured(db_path, h, 1, 0.10)
        _make_h6(h6_db, [(1, 5, 0.10)])
        before = _sha256(h6_db)
        assert act.main(["--db", str(db_path), "--h6-db", str(h6_db), "--commit"]) == 0
        assert _sha256(h6_db) == before

    def test_module_never_imports_or_calls_daily_job(self):
        """LR1: equivalence is behavioural, never a shared code path.

        `daily_job` legitimately appears inside the binding-identity
        string the Specification itself declares
        (`forward_evidence.daily_job.trade_labels`) -- that is recorded
        identity, not a code path. What must not exist is any import of,
        or module load from, daily_job.py."""
        source = (_LABEL_STORE_DIR / "activation.py").read_text(encoding="utf-8")
        code = source.split('"""', 2)[2]

        # Checked against docstring-stripped code: the module docstring
        # legitimately discusses daily_job.py in order to state that it is
        # NOT imported.
        assert "import daily_job" not in code
        assert "daily_job.py" not in code
        assert "forward_evidence import" not in code

        # Every occurrence of the name must be inside a quoted string.
        for line in code.splitlines():
            if "daily_job" in line:
                assert '"' in line or "'" in line, f"unquoted daily_job reference: {line!r}"
                assert not line.strip().startswith(("import ", "from ")), line

    def test_module_loads_no_sibling_beyond_the_registry(self):
        """The only sibling module loaded is registry.py -- activation
        touches no value-store or receipt code path."""
        source = (_LABEL_STORE_DIR / "activation.py").read_text(encoding="utf-8")
        loaded = set(re.findall(r'_load_sibling\(\s*"([^"]+)"', source))
        assert loaded == {"registry.py"}


class TestNoUnrelatedMutation:
    def test_values_definitions_and_receipts_are_untouched(self, db_path, h6_db):
        h = _make_spec(db_path, horizon_days=5)
        _declare_matured(db_path, h, 1, 0.10)
        _make_h6(h6_db, [(1, 5, 0.10)])
        con = sqlite3.connect(db_path)
        before_vals = con.execute("SELECT * FROM label_value_forward").fetchall()
        before_defs = con.execute("SELECT * FROM label_definitions").fetchall()
        con.close()

        assert act.main(["--db", str(db_path), "--h6-db", str(h6_db), "--commit"]) == 0

        con = sqlite3.connect(db_path)
        after_vals = con.execute("SELECT * FROM label_value_forward").fetchall()
        after_defs = con.execute("SELECT * FROM label_definitions").fetchall()
        con.close()
        assert before_vals == after_vals and before_defs == after_defs

    def test_only_the_phase_column_changes(self, db_path, h6_db):
        h = _make_spec(db_path, horizon_days=5)
        _declare_matured(db_path, h, 1, 0.10)
        _make_h6(h6_db, [(1, 5, 0.10)])
        cols = [c[1] for c in sqlite3.connect(db_path).execute("PRAGMA table_info(label_specifications)")]
        con = sqlite3.connect(db_path); con.row_factory = sqlite3.Row
        before = dict(con.execute("SELECT * FROM label_specifications WHERE spec_hash=?", (h,)).fetchone())
        con.close()

        assert act.main(["--db", str(db_path), "--h6-db", str(h6_db), "--commit"]) == 0

        con = sqlite3.connect(db_path); con.row_factory = sqlite3.Row
        after = dict(con.execute("SELECT * FROM label_specifications WHERE spec_hash=?", (h,)).fetchone())
        con.close()
        changed = [c for c in cols if before[c] != after[c]]
        assert changed == ["phase"]
        assert before["spec_hash"] == after["spec_hash"]   # content hash is stable


# ---------------------------------------------------------------------------
# Real-data tier -- hash-guarded
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def _guard_real_h6_untouched():
    """H6 must be byte-identical after every test in this file (LR1)."""
    if not REAL_H6_DB.exists():
        yield
        return
    before = _sha256(REAL_H6_DB)
    yield
    assert _sha256(REAL_H6_DB) == before, "the real H6 forward_evidence.db was modified by a test"


real_db = pytest.mark.skipif(
    not (REAL_LABEL_STORE_DB.exists() and REAL_H6_DB.exists()),
    reason="real label_store.db / forward_evidence.db not present (gitignored; absent in CI)",
)


@real_db
class TestRealLabelStore:
    def test_the_four_h6_horizon_specifications_are_active(self):
        con = sqlite3.connect(REAL_LABEL_STORE_DB)
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(
            "SELECT horizon_days, phase FROM label_specifications "
            "WHERE binding_id = ? ORDER BY horizon_days", (act.BINDING_H6_TRADE_LABELS,)
        )]
        con.close()
        assert [r["horizon_days"] for r in rows] == [5, 7, 14, 30]
        assert all(r["phase"] == "Active" for r in rows)

    def test_the_four_unverifiable_specifications_remain_registered(self):
        con = sqlite3.connect(REAL_LABEL_STORE_DB)
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(
            "SELECT binding_id, phase FROM label_specifications WHERE binding_id != ?",
            (act.BINDING_H6_TRADE_LABELS,)
        )]
        con.close()
        assert len(rows) == 4
        assert all(r["phase"] == "Registered" for r in rows)

    def test_no_real_specification_is_rejected(self):
        con = sqlite3.connect(REAL_LABEL_STORE_DB)
        phases = {r[0] for r in con.execute("SELECT DISTINCT phase FROM label_specifications")}
        con.close()
        assert phases == {"Active", "Registered"}

    def test_real_equivalence_still_holds_for_every_active_specification(self):
        con = sqlite3.connect(REAL_LABEL_STORE_DB)
        con.row_factory = sqlite3.Row
        specs = [dict(r) for r in con.execute(
            "SELECT * FROM label_specifications WHERE binding_id = ?", (act.BINDING_H6_TRADE_LABELS,)
        )]
        con.close()
        total = 0
        for spec in specs:
            r = act.equivalence_report(REAL_LABEL_STORE_DB, spec, REAL_H6_DB)
            assert r["compared"] == 37 and r["matched"] == 37, f"{spec['spec_hash'][:12]}: {r['reason']}"
            total += r["matched"]
        assert total == 148

    def test_plan_against_real_db_reports_nothing_left_to_activate(self):
        assert [r for r in act.plan(REAL_LABEL_STORE_DB, REAL_H6_DB) if r["ok"]] == []

    def test_a_pre_activation_backup_exists(self):
        assert list(_LABEL_STORE_DIR.glob("label_store.pre-activation.*.db"))

    def test_the_148_forward_values_are_unchanged(self):
        con = sqlite3.connect(REAL_LABEL_STORE_DB)
        n = con.execute("SELECT COUNT(*) FROM label_value_forward").fetchone()[0]
        standing = {r[0] for r in con.execute("SELECT DISTINCT standing FROM label_value_forward")}
        con.close()
        assert n == 148 and standing == {"Context"}
