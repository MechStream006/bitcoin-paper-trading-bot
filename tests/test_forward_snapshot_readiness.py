"""
FB-12g — the readiness block is frozen into every new L2 snapshot row.

Reuses the FB-12e implementation; asserts only what wiring it into
`forward_snapshot.write_snapshot()` must guarantee.
"""
from __future__ import annotations

import copy
import datetime
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in ("research/dq", "research/evidence_library", "research"):
    sys.path.insert(0, str(ROOT / p))

import readiness_gate  # noqa: E402
import record  # noqa: E402

from forward_evidence import forward_admissibility as fa  # noqa: E402
from forward_evidence import forward_readiness as fr  # noqa: E402
from forward_evidence import forward_snapshot as fs  # noqa: E402

FAMILY = "h5_vol_regime"
AS_OF = "2026-08-29"
FIRST = "2026-06-15"
GAP_START, GAP_END = "2026-07-18", "2026-08-05"


# ---------------------------------------------------------------------------
# A real forward corpus: run_log with the INC-0005 hole, plus closed trades
# ---------------------------------------------------------------------------

def _corpus(tmp_path, skip_day=None, trades=3):
    db = tmp_path / "evidence_forward.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE run_log (id INTEGER PRIMARY KEY, signal_id TEXT,
            run_date TEXT, fn_hash TEXT, status TEXT, note TEXT, created_at TEXT);
        CREATE TABLE trade_entries (id INTEGER PRIMARY KEY, signal_id TEXT,
            entry_date TEXT, direction INTEGER, entry_close_price REAL,
            planned_exit_date TEXT, open INTEGER);
        CREATE TABLE trade_exits (id INTEGER PRIMARY KEY, entry_id INTEGER,
            exit_date TEXT, exit_close_price REAL, cost_bps REAL);
        CREATE TABLE signal_log (id INTEGER PRIMARY KEY, signal_id TEXT,
            day TEXT, position REAL, raw_value REAL, run_id INTEGER);
        CREATE VIEW trade_returns AS
        SELECT e.id AS entry_id, e.signal_id, e.entry_date, e.direction,
               e.entry_close_price, x.exit_date, x.exit_close_price, x.cost_bps,
               (x.exit_close_price/e.entry_close_price - 1.0) AS gross_return,
               (x.exit_close_price/e.entry_close_price - 1.0) - x.cost_bps/10000.0
                   AS net_return,
               7 AS hold_days_actual
        FROM trade_entries e JOIN trade_exits x ON x.entry_id = e.id;
    """)
    d0 = datetime.date.fromisoformat(FIRST)
    d1 = datetime.date.fromisoformat(AS_OF)
    for i in range((d1 - d0).days + 1):
        day = (d0 + datetime.timedelta(days=i)).isoformat()
        if GAP_START <= day <= GAP_END or day == skip_day:
            continue
        con.execute("INSERT INTO run_log (signal_id,run_date,fn_hash,status) "
                    "VALUES (?,?,'h',' ok')", (FAMILY, day))
    con.execute("UPDATE run_log SET status='ok'")
    for k in range(trades):
        entry = (d0 + datetime.timedelta(days=7 * k)).isoformat()
        exit_ = (d0 + datetime.timedelta(days=7 * k + 7)).isoformat()
        con.execute("INSERT INTO trade_entries (id,signal_id,entry_date,direction,"
                    "entry_close_price,planned_exit_date,open) VALUES (?,?,?,?,?,?,0)",
                    (k + 1, FAMILY, entry, 1, 100.0, exit_))
        con.execute("INSERT INTO trade_exits (entry_id,exit_date,exit_close_price,"
                    "cost_bps) VALUES (?,?,?,15.0)", (k + 1, exit_, 101.0 + k))
    con.commit()
    con.close()
    return db


def _ledger(tmp_path):
    db = tmp_path / "ledger.db"
    record.init_db(db)
    return db


@pytest.fixture
def registry():
    import yaml
    return yaml.safe_load(
        (ROOT / "research/evidence_library/registry.yaml").read_text("utf-8"))


def _write(tmp_path, registry, forward_db=None, ledger_db=None, as_of=AS_OF):
    return fs.write_snapshot(
        FAMILY, FAMILY, registry,
        forward_db or _corpus(tmp_path), ledger_db or _ledger(tmp_path),
        as_of, actor="tester", repo_root=ROOT)


def _row_metrics(ledger_db, family=FAMILY):
    con = sqlite3.connect(ledger_db)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM runs WHERE family_id=? ORDER BY rowid DESC LIMIT 1",
                      (family,)).fetchone()
    con.close()
    return json.loads(row["metrics_json"]), dict(row)


# ---------------------------------------------------------------------------
# The block is present, correct and frozen
# ---------------------------------------------------------------------------

class TestBlockEmbedded:
    def test_new_snapshot_carries_metrics_readiness(self, tmp_path, registry):
        fdb, ldb = _corpus(tmp_path), _ledger(tmp_path)
        run_id, outcome = _write(tmp_path, registry, fdb, ldb)
        assert outcome == "written" and run_id
        metrics, _ = _row_metrics(ldb)
        assert fr.READINESS_KEY in metrics

    def test_block_covers_the_rows_own_window(self, tmp_path, registry):
        fdb, ldb = _corpus(tmp_path), _ledger(tmp_path)
        _write(tmp_path, registry, fdb, ldb)
        metrics, row = _row_metrics(ldb)
        b = metrics[fr.READINESS_KEY]
        assert b["window_start"] == row["window_start"][:10]
        assert b["window_end"] == row["window_end"][:10]

    def test_block_is_deterministic(self, tmp_path, registry):
        fdb = _corpus(tmp_path)
        a = fr.build_readiness_block(FAMILY, FIRST, AS_OF, forward_db=fdb)
        b = fr.build_readiness_block(FAMILY, FIRST, AS_OF, forward_db=fdb)
        assert a == b

    def test_block_is_frozen_in_the_row(self, tmp_path, registry):
        """Later corpus growth cannot change what the row recorded."""
        fdb, ldb = _corpus(tmp_path), _ledger(tmp_path)
        _write(tmp_path, registry, fdb, ldb)
        before, _ = _row_metrics(ldb)
        con = sqlite3.connect(fdb)
        con.execute("INSERT INTO run_log (signal_id,run_date,fn_hash,status) "
                    "VALUES (?,?,'h','ok')", (FAMILY, "2026-07-20"))
        con.commit(); con.close()
        after, _ = _row_metrics(ldb)
        assert after[fr.READINESS_KEY] == before[fr.READINESS_KEY]


# ---------------------------------------------------------------------------
# INC-0005 exclusions
# ---------------------------------------------------------------------------

class TestExclusions:
    def test_exactly_the_declared_gap_is_excluded(self, tmp_path, registry):
        fdb, ldb = _corpus(tmp_path), _ledger(tmp_path)
        _write(tmp_path, registry, fdb, ldb)
        b, _ = _row_metrics(ldb)
        block = b[fr.READINESS_KEY]
        assert set(block["days_excluded"]) == fr.declared_gap_days()
        assert len(block["days_excluded"]) == 19
        assert block["exclusion_basis"] == fr.EXCLUSION_BASIS
        assert block["days_non_ready"] == []

    def test_excluded_days_are_never_counted_ready(self, tmp_path, registry):
        fdb, ldb = _corpus(tmp_path), _ledger(tmp_path)
        _write(tmp_path, registry, fdb, ldb)
        b, _ = _row_metrics(ldb)
        block = b[fr.READINESS_KEY]
        assert block["days_ready"] + len(block["days_excluded"]) == block["days_total"]
        assert block["days_ready"] != block["days_total"]

    def test_undeclared_missing_day_is_non_ready_not_excluded(self, tmp_path, registry):
        fdb = _corpus(tmp_path, skip_day="2026-08-20")
        ldb = _ledger(tmp_path)
        _write(tmp_path, registry, fdb, ldb)
        b, _ = _row_metrics(ldb)
        block = b[fr.READINESS_KEY]
        assert block["days_non_ready"] == ["2026-08-20"]
        assert "2026-08-20" not in block["days_excluded"]

    def test_undeclared_missing_day_blocks_the_gate(self, tmp_path, registry):
        fdb = _corpus(tmp_path, skip_day="2026-08-20")
        ldb = _ledger(tmp_path)
        _write(tmp_path, registry, fdb, ldb)
        metrics, row = _row_metrics(ldb)
        run = {"family_id": FAMILY, "window_start": row["window_start"],
               "window_end": row["window_end"], "run_ts": row["run_ts"],
               "metrics": metrics}
        assert readiness_gate.DEFAULT_PROVIDER.assess(ldb, run).ok is False


# ---------------------------------------------------------------------------
# Consumption
# ---------------------------------------------------------------------------

class TestConsumption:
    def test_provider_consumes_the_embedded_block(self, tmp_path, registry):
        fdb, ldb = _corpus(tmp_path), _ledger(tmp_path)
        _write(tmp_path, registry, fdb, ldb)
        metrics, row = _row_metrics(ldb)
        run = {"family_id": FAMILY, "window_start": row["window_start"],
               "window_end": row["window_end"], "run_ts": row["run_ts"],
               "metrics": metrics}
        a = readiness_gate.DEFAULT_PROVIDER.assess(ldb, run)
        assert a.ok is True and a.source == "run_block"

    def test_run_block_takes_precedence_over_receipts(self, tmp_path, registry):
        """The block short-circuits: no receipt exists, yet the run passes."""
        fdb, ldb = _corpus(tmp_path), _ledger(tmp_path)
        _write(tmp_path, registry, fdb, ldb)
        metrics, row = _row_metrics(ldb)
        con = sqlite3.connect(ldb)
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
        assert "data_readiness" not in tables, "no receipts were written"
        run = {"family_id": FAMILY, "window_start": row["window_start"],
               "window_end": row["window_end"], "run_ts": row["run_ts"],
               "metrics": metrics}
        assert readiness_gate.DEFAULT_PROVIDER.assess(ldb, run).source == "run_block"


# ---------------------------------------------------------------------------
# Everything else preserved
# ---------------------------------------------------------------------------

class TestPreserved:
    def test_forward_admissibility_payload_intact(self, tmp_path, registry):
        fdb, ldb = _corpus(tmp_path), _ledger(tmp_path)
        _write(tmp_path, registry, fdb, ldb)
        metrics, _ = _row_metrics(ldb)
        payload = metrics[fa.FORWARD_ADMISSIBILITY_KEY]
        assert payload["schema"] == fa.PAYLOAD_SCHEMA
        assert "floors" in payload and payload["provenance"] == "live-forward"

    def test_dsr_and_bh_remain_null(self, tmp_path, registry):
        fdb, ldb = _corpus(tmp_path), _ledger(tmp_path)
        _write(tmp_path, registry, fdb, ldb)
        _, row = _row_metrics(ldb)
        assert row["dsr_prob"] is None and row["bh_p"] is None

    def test_same_as_of_is_idempotent(self, tmp_path, registry):
        fdb, ldb = _corpus(tmp_path), _ledger(tmp_path)
        first_id, first = _write(tmp_path, registry, fdb, ldb)
        second_id, second = _write(tmp_path, registry, fdb, ldb)
        assert first == "written" and second == "skipped"
        assert second_id == first_id
        con = sqlite3.connect(ldb)
        n = con.execute("SELECT COUNT(*) FROM runs WHERE family_id=?", (FAMILY,)).fetchone()[0]
        con.close()
        assert n == 1

    def test_zero_trade_family_still_writes_nothing(self, tmp_path, registry):
        fdb = _corpus(tmp_path, trades=0)
        ldb = _ledger(tmp_path)
        run_id, outcome = _write(tmp_path, registry, fdb, ldb)
        assert (run_id, outcome) == (None, "no-evidence")
        con = sqlite3.connect(ldb)
        assert con.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        con.close()

    def test_dry_run_writes_nothing(self, tmp_path, registry):
        fdb, ldb = _corpus(tmp_path), _ledger(tmp_path)
        run_id, outcome = fs.write_snapshot(
            FAMILY, FAMILY, registry, fdb, ldb, AS_OF, actor="t",
            dry_run=True, repo_root=ROOT)
        assert (run_id, outcome) == (None, "dry-run")
        con = sqlite3.connect(ldb)
        assert con.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        con.close()

    def test_no_receipt_transition_or_gate_result_written(self, tmp_path, registry):
        fdb, ldb = _corpus(tmp_path), _ledger(tmp_path)
        _write(tmp_path, registry, fdb, ldb)
        con = sqlite3.connect(ldb)
        for tbl in ("state_transitions", "gate_results"):
            assert con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0] == 0
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
        assert "contract_registry" not in tables


# ---------------------------------------------------------------------------
# Isolation and protected artifacts
# ---------------------------------------------------------------------------

class TestIsolation:
    def test_scheduler_and_collectors_do_not_import_the_snapshot_producer(self):
        for name in ("run_daily.py", "runner_base.py", "daily_job.py", "ledger_sync.py"):
            path = ROOT / "research/forward_evidence" / name
            if path.exists():
                src = path.read_text("utf-8")
                assert "forward_snapshot" not in src
                assert "forward_readiness" not in src
        for unit in (ROOT / "deploy").glob("*.service"):
            text = unit.read_text("utf-8")
            assert "forward_snapshot" not in text and "forward_readiness" not in text

    def test_protected_files_unmodified(self):
        proc = subprocess.run(["git", "diff", "HEAD", "--name-only"],
                              cwd=ROOT, capture_output=True, text=True)
        touched = set(proc.stdout.split())
        for frozen in (
            "research/evidence_library/readiness_gate.py",
            "research/evidence_library/transition_governance.py",
            "research/evidence_library/record.py",
            "research/evidence_library/registry.yaml",
            "research/evidence_library/thresholds.yaml",
            "research/forward_evidence/run_daily.py",
            "research/forward_evidence/runner_base.py",
            "research/forward_evidence/daily_job.py",
            "research/forward_evidence/ledger_sync.py",
            "research/forward_evidence/decision_function.py",
            "research/forward_evidence/run_date_policy.py",
            "research/forward_evidence/run_date_policy.yaml",
            "research/forward_evidence/observation_floors.py",
            "research/forward_evidence/forward_admissibility.py",
            "research/dq/states.py", "research/dq/engine.py",
            "research/dq/checks.py", "research/dq/readiness.py",
        ):
            assert frozen not in touched, f"{frozen} must not be modified"


# ---------------------------------------------------------------------------
# Sabotage the fail-closed exclusion guard
# ---------------------------------------------------------------------------

class TestSabotage:
    def test_forged_exclusion_aborts_before_the_row_is_written(
            self, tmp_path, registry, monkeypatch):
        """The invariant runs BEFORE append_run: a payload excluding an
        undeclared day must leave the ledger empty, not write a passing row."""
        fdb = _corpus(tmp_path, skip_day="2026-08-20")
        ldb = _ledger(tmp_path)

        real = fr.build_readiness_block

        def forged(*args, **kwargs):
            block = copy.deepcopy(real(*args, **kwargs))
            block["days_non_ready"] = []
            block["days_excluded"] = sorted(block["days_excluded"] + ["2026-08-20"])
            return block

        monkeypatch.setattr(fr, "build_readiness_block", forged)
        with pytest.raises(fr.UndeclaredGapError):
            _write(tmp_path, registry, fdb, ldb)

        con = sqlite3.connect(ldb)
        assert con.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0, (
            "a forged exclusion must never reach the ledger")
        con.close()

    def test_guard_restored_writes_normally(self, tmp_path, registry):
        """Byte-exact restoration: the same corpus writes cleanly once the
        monkeypatch is gone."""
        fdb = _corpus(tmp_path, skip_day="2026-08-20")
        ldb = _ledger(tmp_path)
        run_id, outcome = _write(tmp_path, registry, fdb, ldb)
        assert outcome == "written" and run_id
        b, _ = _row_metrics(ldb)
        assert b[fr.READINESS_KEY]["days_non_ready"] == ["2026-08-20"]

    def test_readiness_failure_is_never_silently_omitted(
            self, tmp_path, registry, monkeypatch):
        """If the block cannot be built, the snapshot fails; it does not write
        a row lacking readiness."""
        fdb, ldb = _corpus(tmp_path), _ledger(tmp_path)

        def boom(*a, **k):
            raise RuntimeError("policy unreadable")

        monkeypatch.setattr(fr, "build_readiness_block", boom)
        with pytest.raises(RuntimeError):
            _write(tmp_path, registry, fdb, ldb)
        con = sqlite3.connect(ldb)
        assert con.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        con.close()
