"""
FB-12i-R — a --dry-run must render exactly what a real write persists.

The FB-12i production dry-run displayed a payload with no `readiness` key,
because the block was built after the dry-run early-return. A dry run that
shows something the write would never produce is worse than no dry run: it
looks like verification. These tests pin the two paths together.
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
from forward_evidence import ledger_sync as ls  # noqa: E402

FAMILY = "h5_vol_regime"
FIRST, AS_OF = "2026-06-15", "2026-08-28"
GAP_START, GAP_END = "2026-07-18", "2026-08-05"


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
    d0, d1 = datetime.date.fromisoformat(FIRST), datetime.date.fromisoformat(AS_OF)
    for i in range((d1 - d0).days + 1):
        day = (d0 + datetime.timedelta(days=i)).isoformat()
        if GAP_START <= day <= GAP_END or day == skip_day:
            continue
        con.execute("INSERT INTO run_log (signal_id,run_date,fn_hash,status) "
                    "VALUES (?,?,'h','ok')", (FAMILY, day))
    for k in range(trades):
        entry = (d0 + datetime.timedelta(days=7 * k)).isoformat()
        exit_ = (d0 + datetime.timedelta(days=7 * k + 7)).isoformat()
        con.execute("INSERT INTO trade_entries (id,signal_id,entry_date,direction,"
                    "entry_close_price,planned_exit_date,open) VALUES (?,?,?,1,?,?,0)",
                    (k + 1, FAMILY, entry, 100.0, exit_))
        con.execute("INSERT INTO trade_exits (entry_id,exit_date,exit_close_price,"
                    "cost_bps) VALUES (?,?,?,15.0)", (k + 1, exit_, 101.0 + k))
    con.commit(); con.close()
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


def _dry(fdb, ldb, registry):
    return fs.write_snapshot(FAMILY, FAMILY, registry, fdb, ldb, AS_OF,
                             actor="tester", dry_run=True, repo_root=ROOT)


def _payload(fdb, registry):
    return fs.build_snapshot_metrics(FAMILY, FAMILY, registry, fdb, AS_OF,
                                     repo_root=ROOT)


def _counts(db):
    con = sqlite3.connect(db)
    out = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
           for t in ("runs", "state_transitions", "gate_results")}
    out["tables"] = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    return out


# ---------------------------------------------------------------------------
# The defect itself
# ---------------------------------------------------------------------------

class TestDryRunCompleteness:
    def test_dry_run_payload_contains_readiness(self, tmp_path, registry):
        fdb = _corpus(tmp_path)
        payload = _payload(fdb, registry)
        assert fr.READINESS_KEY in payload, (
            "the FB-12i defect: a dry run must render the readiness block")

    def test_dry_run_payload_contains_admissibility(self, tmp_path, registry):
        payload = _payload(_corpus(tmp_path), registry)
        assert fa.FORWARD_ADMISSIBILITY_KEY in payload
        assert payload[fa.FORWARD_ADMISSIBILITY_KEY]["schema"] == fa.PAYLOAD_SCHEMA

    def test_dry_run_payload_contains_exclusions_and_basis(self, tmp_path, registry):
        block = _payload(_corpus(tmp_path), registry)[fr.READINESS_KEY]
        assert len(block["days_excluded"]) == 19
        assert set(block["days_excluded"]) == fr.declared_gap_days()
        assert block["exclusion_basis"] == fr.EXCLUSION_BASIS

    def test_cli_dry_run_prints_readiness(self, tmp_path, registry, capsys):
        fdb = _corpus(tmp_path)
        rc = fs.main([
            "--family", FAMILY, "--as-of", AS_OF, "--actor", "tester",
            "--dry-run", "--forward-db", str(fdb),
            "--registry", str(ROOT / "research/evidence_library/registry.yaml"),
        ])
        assert rc == 0
        printed = json.loads(capsys.readouterr().out)
        assert fr.READINESS_KEY in printed
        assert len(printed[fr.READINESS_KEY]["days_excluded"]) == 19


# ---------------------------------------------------------------------------
# Parity: the dry-run payload IS the persisted payload
# ---------------------------------------------------------------------------

class TestParity:
    def test_dry_run_equals_what_append_run_receives(self, tmp_path, registry,
                                                     monkeypatch):
        """Capture the metrics argument at the append boundary and compare."""
        fdb, ldb = _corpus(tmp_path), _ledger(tmp_path)
        dry = _payload(fdb, registry)

        captured = {}
        real_record = ls._record()
        real_append = real_record.append_run

        def spy(db, **kw):
            captured["metrics"] = copy.deepcopy(kw["metrics"])
            captured["window_start"] = kw["window_start"]
            return real_append(db, **kw)

        monkeypatch.setattr(real_record, "append_run", spy)
        fs.write_snapshot(FAMILY, FAMILY, registry, fdb, ldb, AS_OF,
                          actor="tester", repo_root=ROOT)

        assert captured["metrics"] == dry, "dry-run and write payloads diverged"
        assert json.dumps(captured["metrics"], sort_keys=True, default=str) == \
               json.dumps(dry, sort_keys=True, default=str)

    def test_window_start_matches_the_block(self, tmp_path, registry, monkeypatch):
        fdb, ldb = _corpus(tmp_path), _ledger(tmp_path)
        captured = {}
        rec = ls._record()
        real_append = rec.append_run

        def spy(db, **kw):
            captured.update(kw)
            return real_append(db, **kw)

        monkeypatch.setattr(rec, "append_run", spy)
        fs.write_snapshot(FAMILY, FAMILY, registry, fdb, ldb, AS_OF,
                          actor="tester", repo_root=ROOT)
        assert captured["window_start"] == \
               captured["metrics"][fr.READINESS_KEY]["window_start"]

    def test_sabotaged_divergence_is_caught(self, tmp_path, registry, monkeypatch):
        """Sabotage: make the write mutate the payload after it was built.
        The parity assertion must fail."""
        fdb, ldb = _corpus(tmp_path), _ledger(tmp_path)
        dry = _payload(fdb, registry)

        real_build = fs.build_snapshot_metrics

        def diverging(*a, **k):
            m = real_build(*a, **k)
            if m is not None:
                m = dict(m)
                m["injected_after_dry_run"] = True
            return m

        captured = {}
        rec = ls._record()
        real_append = rec.append_run

        def spy(db, **kw):
            captured["metrics"] = copy.deepcopy(kw["metrics"])
            return real_append(db, **kw)

        monkeypatch.setattr(rec, "append_run", spy)
        monkeypatch.setattr(fs, "build_snapshot_metrics", diverging)
        fs.write_snapshot(FAMILY, FAMILY, registry, fdb, ldb, AS_OF,
                          actor="tester", repo_root=ROOT)
        assert captured["metrics"] != dry, (
            "the parity guard must detect a payload that diverges after build")

    def test_parity_restored_after_sabotage(self, tmp_path, registry, monkeypatch):
        """Byte-exact restoration: unpatched, the payloads match again."""
        fdb, ldb = _corpus(tmp_path), _ledger(tmp_path)
        dry = _payload(fdb, registry)
        captured = {}
        rec = ls._record()
        real_append = rec.append_run

        def spy(db, **kw):
            captured["metrics"] = copy.deepcopy(kw["metrics"])
            return real_append(db, **kw)

        monkeypatch.setattr(rec, "append_run", spy)
        fs.write_snapshot(FAMILY, FAMILY, registry, fdb, ldb, AS_OF,
                          actor="tester", repo_root=ROOT)
        assert captured["metrics"] == dry


# ---------------------------------------------------------------------------
# Dry-run writes nothing
# ---------------------------------------------------------------------------

class TestDryRunWritesNothing:
    def test_zero_persistent_mutation(self, tmp_path, registry):
        fdb, ldb = _corpus(tmp_path), _ledger(tmp_path)
        before = _counts(ldb)
        digest = ldb.read_bytes()
        run_id, outcome = _dry(fdb, ldb, registry)
        assert (run_id, outcome) == (None, "dry-run")
        assert _counts(ldb) == before
        assert ldb.read_bytes() == digest, "the ledger file must be byte-identical"

    def test_dry_run_leaves_the_corpus_untouched(self, tmp_path, registry):
        fdb, ldb = _corpus(tmp_path), _ledger(tmp_path)
        digest = fdb.read_bytes()
        _dry(fdb, ldb, registry)
        assert fdb.read_bytes() == digest

    def test_dry_run_creates_no_governance_tables(self, tmp_path, registry):
        fdb, ldb = _corpus(tmp_path), _ledger(tmp_path)
        _dry(fdb, ldb, registry)
        tables = _counts(ldb)["tables"]
        for forbidden in ("contract_registry", "data_readiness", "governance_receipts"):
            assert forbidden not in tables


# ---------------------------------------------------------------------------
# Everything FB-12g guaranteed still holds
# ---------------------------------------------------------------------------

class TestUnchangedBehaviour:
    def test_undeclared_gap_fails_closed_in_dry_run_too(self, tmp_path, registry,
                                                        monkeypatch):
        fdb, ldb = _corpus(tmp_path, skip_day="2026-08-20"), _ledger(tmp_path)
        real = fr.build_readiness_block

        def forged(*a, **k):
            b = dict(real(*a, **k))
            b["days_non_ready"] = []
            b["days_excluded"] = sorted(b["days_excluded"] + ["2026-08-20"])
            return b

        monkeypatch.setattr(fr, "build_readiness_block", forged)
        with pytest.raises(fr.UndeclaredGapError):
            _dry(fdb, ldb, registry)
        assert _counts(ldb)["runs"] == 0

    def test_undeclared_missing_day_is_non_ready(self, tmp_path, registry):
        payload = _payload(_corpus(tmp_path, skip_day="2026-08-20"), registry)
        assert payload[fr.READINESS_KEY]["days_non_ready"] == ["2026-08-20"]

    def test_dsr_bh_absent(self, tmp_path, registry):
        fdb, ldb = _corpus(tmp_path), _ledger(tmp_path)
        fs.write_snapshot(FAMILY, FAMILY, registry, fdb, ldb, AS_OF,
                          actor="tester", repo_root=ROOT)
        con = sqlite3.connect(ldb); con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM runs LIMIT 1").fetchone()
        con.close()
        assert row["dsr_prob"] is None and row["bh_p"] is None

    def test_same_as_of_idempotent(self, tmp_path, registry):
        fdb, ldb = _corpus(tmp_path), _ledger(tmp_path)
        a_id, a = fs.write_snapshot(FAMILY, FAMILY, registry, fdb, ldb, AS_OF,
                                    actor="t", repo_root=ROOT)
        b_id, b = fs.write_snapshot(FAMILY, FAMILY, registry, fdb, ldb, AS_OF,
                                    actor="t", repo_root=ROOT)
        assert (a, b) == ("written", "skipped") and a_id == b_id
        assert _counts(ldb)["runs"] == 1

    def test_existing_rows_untouched_by_a_second_family(self, tmp_path, registry):
        fdb, ldb = _corpus(tmp_path), _ledger(tmp_path)
        fs.write_snapshot(FAMILY, FAMILY, registry, fdb, ldb, AS_OF,
                          actor="t", repo_root=ROOT)
        con = sqlite3.connect(ldb); con.row_factory = sqlite3.Row
        before = dict(con.execute("SELECT * FROM runs").fetchone())
        con.close()
        fs.write_snapshot(FAMILY, FAMILY, registry, fdb, ldb, "2026-08-27",
                          actor="t", repo_root=ROOT)
        con = sqlite3.connect(ldb); con.row_factory = sqlite3.Row
        after = dict(con.execute("SELECT * FROM runs WHERE run_id=?",
                                 (before["run_id"],)).fetchone())
        con.close()
        assert after == before

    def test_provider_consumes_the_embedded_block(self, tmp_path, registry):
        fdb, ldb = _corpus(tmp_path), _ledger(tmp_path)
        fs.write_snapshot(FAMILY, FAMILY, registry, fdb, ldb, AS_OF,
                          actor="t", repo_root=ROOT)
        con = sqlite3.connect(ldb); con.row_factory = sqlite3.Row
        row = dict(con.execute("SELECT * FROM runs LIMIT 1").fetchone())
        con.close()
        run = {"family_id": FAMILY, "window_start": row["window_start"],
               "window_end": row["window_end"], "run_ts": row["run_ts"],
               "metrics": json.loads(row["metrics_json"])}
        a = readiness_gate.DEFAULT_PROVIDER.assess(ldb, run)
        assert a.ok is True and a.source == "run_block"

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
            "research/forward_evidence/ledger_sync.py",
            "research/forward_evidence/daily_job.py",
            "research/forward_evidence/run_date_policy.py",
            "research/forward_evidence/run_date_policy.yaml",
            "research/forward_evidence/forward_readiness.py",
            "research/forward_evidence/observation_floors.py",
            "research/forward_evidence/forward_admissibility.py",
            "research/dq/engine.py", "research/dq/readiness.py",
        ):
            assert frozen not in touched, f"{frozen} must not be modified"
