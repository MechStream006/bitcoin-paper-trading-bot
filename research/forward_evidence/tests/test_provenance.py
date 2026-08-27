"""
Gate 6B.6 — observation provenance (defects B-04 and B-04a).

B-04 : nothing recorded WHICH bytes produced an observation. The live ingest
       writer overwrites whole monthly partitions, so the input file mutates by
       design after collection and no observation could be reconstructed.
B-04a: run_log.fn_hash pins only the decision function. runner_base.py changed
       at 6B.1 and run_daily.py at 6B.3 — both changing WHAT gets collected —
       yet fn_hash is byte-identical across those changes.

Also covers the 6B.5 rider: a negative cost_bps was silently accepted and
manufactured profit. Bounds only — the registered per-trade cost CONVENTION
(label spec 57ce016e51a0) is deliberately unchanged.

Run from research/:
    pytest forward_evidence/tests/test_provenance.py -v
"""
from __future__ import annotations

import json
import pathlib
import sqlite3

import numpy as np
import pandas as pd
import pytest

from forward_evidence import run_daily as _run_daily
from forward_evidence.init_forward_db import init_forward_db
from forward_evidence.pre_reg_guard import _sha256_lf
from forward_evidence.runner_base import (
    DataUnavailableError,
    framework_hash,
    resolve_input_identity,
    run_signal,
)

_HERE = pathlib.Path(__file__).resolve().parents[1]


# ===========================================================================
# Fixtures
# ===========================================================================

def write_store(root: pathlib.Path, months: int = 3, rows: int = 100) -> pathlib.Path:
    """A tiny multi-partition 1m store (identity only; not used for pricing)."""
    for i in range(months):
        y, m = (2026, 1 + i)
        ts = pd.date_range(f"{y}-{m:02d}-01", periods=rows, freq="min", tz="UTC")
        df = pd.DataFrame({"open_time": ts.astype("int64") // 10 ** 6,
                           "close": np.linspace(100, 200, rows)})
        p = root / f"year={y}" / f"month={m:02d}"
        p.mkdir(parents=True, exist_ok=True)
        df.to_parquet(p / "data.parquet", index=False)
    return root


@pytest.fixture()
def env(tmp_path):
    fn = tmp_path / "decision_function_probe.py"
    fn.write_bytes(b"def compute_signal(c):\n    return c * 0.0 + 1.0\n")
    pre = tmp_path / "prereg.json"
    pre.write_text(json.dumps({
        "schema_version": 1, "version": "1.0.0", "signal_id": "probe",
        "pre_registration_date": "2026-06-14T00:00:00Z",
        "function_spec": {"decision_function_hash": _sha256_lf(fn)},
    }), encoding="utf-8")
    db = tmp_path / "evidence_forward.db"
    init_forward_db(db)
    return fn, pre, db


def closes(n=40):
    idx = pd.date_range("2026-01-01", periods=n, freq="D").strftime("%Y-%m-%d")
    return pd.Series(np.linspace(40_000, 41_000, n), index=idx, dtype=float)


def collect(env, run_date=None, inputs=None, cost=15.0):
    fn, pre, db = env
    s = closes()
    return run_signal(
        signal_id="probe", run_date=run_date or s.index[-1], closes=s,
        compute_fn=lambda c: pd.Series(1.0, index=c.index),
        raw_value_fn=lambda c: float(c.iloc[-1]),
        pre_reg_path=pre, fn_file_path=fn, db_path=db,
        hold_days=7, min_history=30, cost_bps=cost, inputs=inputs,
    )


# ===========================================================================
# PV — provenance capture
# ===========================================================================

class TestProvenanceCapture:

    def test_PV01_happy_path_records_framework_identity(self, env):
        _, _, db = env
        assert collect(env) == "ok"
        c = sqlite3.connect(db)
        row = c.execute("SELECT run_id, framework_hash, git_sha FROM run_provenance").fetchone()
        n_log = c.execute("SELECT id FROM run_log").fetchone()[0]
        c.close()
        assert row[0] == n_log, "provenance must be keyed to the run it describes"
        assert row[1] == framework_hash()
        assert len(row[1]) == 64

    def test_PV02_multiple_input_partitions_all_recorded(self, env, tmp_path):
        _, _, db = env
        store = write_store(tmp_path / "spot", months=3)
        ids = resolve_input_identity(store, "2026-03-01", 40, "price")
        assert len(ids) == 3, "fixture must span several partitions"
        collect(env, inputs=ids)
        c = sqlite3.connect(db)
        rows = c.execute("SELECT dataset, partition, parquet_sha256, rows, role "
                         "FROM run_inputs ORDER BY partition").fetchall()
        c.close()
        assert len(rows) == 3
        assert [r[1] for r in rows] == ["year=2026/month=01", "year=2026/month=02",
                                        "year=2026/month=03"]

    def test_PV03_sha_and_rowcount_match_the_actual_files(self, env, tmp_path):
        import hashlib
        _, _, db = env
        store = write_store(tmp_path / "spot", months=2, rows=137)
        ids = resolve_input_identity(store, "2026-02-01", 40, "price")
        collect(env, inputs=ids)
        c = sqlite3.connect(db)
        rows = dict(c.execute("SELECT partition, parquet_sha256 FROM run_inputs"))
        counts = dict(c.execute("SELECT partition, rows FROM run_inputs"))
        c.close()
        for part, digest in rows.items():
            f = store / part.split("/")[0] / part.split("/")[1] / "data.parquet"
            assert digest == hashlib.sha256(f.read_bytes()).hexdigest(), (
                "must be a RAW sha256 of the parquet bytes, not CRLF-normalised")
            assert counts[part] == 137

    def test_PV04_decision_function_identity_still_recorded(self, env):
        fn, _, db = env
        collect(env)
        c = sqlite3.connect(db)
        h = c.execute("SELECT fn_hash FROM run_log").fetchone()[0]
        c.close()
        assert h == _sha256_lf(fn)

    def test_PV05_framework_hash_differs_from_decision_function_hash(self, env):
        fn, _, _ = env
        assert framework_hash() != _sha256_lf(fn)

    def test_PV06_role_is_recorded_per_input(self, env, tmp_path):
        _, _, db = env
        price = resolve_input_identity(write_store(tmp_path / "s", 1), "2026-01-01", 40, "price")
        aux = resolve_input_identity(write_store(tmp_path / "a", 1), "2026-01-01", 40, "aux")
        collect(env, inputs=price + aux)
        c = sqlite3.connect(db)
        roles = {r[0] for r in c.execute("SELECT role FROM run_inputs")}
        c.close()
        assert roles == {"price", "aux"}


# ===========================================================================
# FI — framework identity (B-04a)
# ===========================================================================

class TestFrameworkIdentity:

    def test_FI01_changed_framework_cannot_masquerade(self, tmp_path, monkeypatch):
        """
        The whole point of B-04a: mutate a framework file and the identity must
        change. Uses a copied tree so the real sources are never touched.
        """
        import shutil
        import forward_evidence.runner_base as rb
        before = framework_hash()
        fake = tmp_path / "fw"
        fake.mkdir()
        for name in rb._FRAMEWORK_FILES:
            shutil.copy2(_HERE / name, fake / name)
        monkeypatch.setattr(rb, "_HERE", fake)
        assert framework_hash() == before, "a byte-identical copy must hash the same"
        (fake / "runner_base.py").write_bytes(
            (fake / "runner_base.py").read_bytes() + b"\n# semantic change\n")
        assert framework_hash() != before

    def test_FI02_every_framework_file_participates(self, tmp_path, monkeypatch):
        import shutil
        import forward_evidence.runner_base as rb
        fake = tmp_path / "fw"
        fake.mkdir()
        for name in rb._FRAMEWORK_FILES:
            shutil.copy2(_HERE / name, fake / name)
        monkeypatch.setattr(rb, "_HERE", fake)
        base = framework_hash()
        for name in rb._FRAMEWORK_FILES:
            orig = (fake / name).read_bytes()
            (fake / name).write_bytes(orig + b"\n#x\n")
            assert framework_hash() != base, f"{name} must affect the identity"
            (fake / name).write_bytes(orig)
        assert framework_hash() == base

    def test_FI03_missing_framework_file_fails_closed(self, tmp_path, monkeypatch):
        import shutil
        import forward_evidence.runner_base as rb
        fake = tmp_path / "fw"
        fake.mkdir()
        for name in rb._FRAMEWORK_FILES:
            shutil.copy2(_HERE / name, fake / name)
        (fake / "run_date_policy.py").unlink()
        monkeypatch.setattr(rb, "_HERE", fake)
        with pytest.raises(DataUnavailableError, match="framework file missing"):
            framework_hash()

    def test_FI04_git_sha_is_advisory_and_may_be_null(self, env):
        """framework_hash is the guarantee; git_sha is a convenience."""
        _, _, db = env
        collect(env)
        c = sqlite3.connect(db)
        fh, git = c.execute("SELECT framework_hash, git_sha FROM run_provenance").fetchone()
        c.close()
        assert fh is not None          # NOT NULL in schema
        assert git is None or isinstance(git, str)


# ===========================================================================
# IN — input identity resolution and tamper detection
# ===========================================================================

class TestInputIdentity:

    def test_IN01_tampered_partition_is_detectable(self, tmp_path):
        """
        The realistic threat is a silent RE-INGEST, not corruption: the file
        stays a valid parquet with different contents (storage/lake.py
        write_partition overwrites in place). A corrupt file is covered by
        IN02; this one must stay readable so the digest actually changes.
        """
        store = write_store(tmp_path / "s", months=1)
        before = resolve_input_identity(store, "2026-01-01", 40, "price")
        f = store / "year=2026" / "month=01" / "data.parquet"
        df = pd.read_parquet(f)
        df.loc[0, "close"] = df.loc[0, "close"] + 1.0      # one value differs
        df.to_parquet(f, index=False)
        after = resolve_input_identity(store, "2026-01-01", 40, "price")
        assert before[0]["rows"] == after[0]["rows"], "same shape, different bytes"
        assert before[0]["parquet_sha256"] != after[0]["parquet_sha256"]

    def test_IN02_unreadable_partition_fails_closed(self, tmp_path):
        store = write_store(tmp_path / "s", months=1)
        (store / "year=2026" / "month=01" / "data.parquet").write_bytes(b"not parquet")
        with pytest.raises(DataUnavailableError, match="cannot establish input identity"):
            resolve_input_identity(store, "2026-01-01", 40, "price")

    def test_IN03_no_partitions_yields_empty_not_error(self, tmp_path):
        empty = tmp_path / "none"
        empty.mkdir()
        assert resolve_input_identity(empty, "2026-01-01", 40, "price") == []

    def test_IN05_digest_is_raw_not_crlf_normalised(self, tmp_path):
        """
        Kills adversarial mutation M08, which survived the first battery: the
        numeric fixtures contained no b'\\r\\n', so _sha256_lf and a raw sha256
        produced the SAME digest and the convention was unfalsifiable. This
        fixture embeds CRLF in a string column, so the two differ — and the
        recorded digest must be the raw one. _sha256_lf is correct for source
        text and would silently corrupt a binary Parquet digest.
        """
        import hashlib
        p = tmp_path / "year=2026" / "month=01"
        p.mkdir(parents=True)
        f = p / "data.parquet"
        pd.DataFrame({"open_time": [0, 1], "note": ["a\r\nb", "c\r\nd"]}).to_parquet(
            f, index=False)
        raw = f.read_bytes()
        assert b"\r\n" in raw, "fixture must actually contain CRLF to be falsifying"
        raw_digest = hashlib.sha256(raw).hexdigest()
        lf_digest = hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()
        assert raw_digest != lf_digest
        got = resolve_input_identity(tmp_path, "2026-01-01", 40, "price")
        assert got[0]["parquet_sha256"] == raw_digest
        assert got[0]["parquet_sha256"] != lf_digest

    def test_IN04_identity_is_deterministic(self, tmp_path):
        store = write_store(tmp_path / "s", months=2)
        a = resolve_input_identity(store, "2026-02-01", 40, "price")
        b = resolve_input_identity(store, "2026-02-01", 40, "price")
        assert a == b


# ===========================================================================
# AT — atomicity, idempotency, failure isolation
# ===========================================================================

class TestAtomicityAndFailure:

    def test_AT01_duplicate_run_is_idempotent(self, env, tmp_path):
        _, _, db = env
        ids = resolve_input_identity(write_store(tmp_path / "s", 1), "2026-01-01", 40, "price")
        assert collect(env, inputs=ids) == "ok"
        assert collect(env, inputs=ids) == "skipped"
        c = sqlite3.connect(db)
        assert c.execute("SELECT COUNT(*) FROM run_provenance").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM run_inputs").fetchone()[0] == 1
        c.close()

    def test_AT01b_provenance_primary_key_blocks_a_second_row(self, env):
        """
        Kills adversarial mutation M16, which survived the first battery: AT01
        exercises idempotency at the run_log gate and returns 'skipped' before
        ever reaching the provenance insert, so the PRIMARY KEY — the
        defence-in-depth that makes a duplicate structurally impossible — was
        never actually tested. This drives it directly.
        """
        _, _, db = env
        collect(env, inputs=[])
        c = sqlite3.connect(db)
        run_id = c.execute("SELECT run_id FROM run_provenance").fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("INSERT INTO run_provenance (run_id, framework_hash) VALUES (?,?)",
                      (run_id, "b" * 64))
            c.commit()
        c.close()

    def test_AT02_failed_collection_writes_no_successful_provenance(self, env):
        """A DataUnavailableError run must leave no run_log AND no provenance."""
        fn, pre, db = env
        s = closes()
        s.iloc[-1] = np.nan                       # 6B.1 B-02 guard fires
        with pytest.raises(DataUnavailableError):
            run_signal(signal_id="probe", run_date=s.index[-1], closes=s,
                       compute_fn=lambda c: pd.Series(1.0, index=c.index),
                       raw_value_fn=lambda c: 1.0, pre_reg_path=pre,
                       fn_file_path=fn, db_path=db, hold_days=7,
                       min_history=30, cost_bps=15.0, inputs=[])
        c = sqlite3.connect(db)
        for t in ("run_log", "run_provenance", "run_inputs"):
            assert c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0, t
        c.close()

    def test_AT03_provenance_rolls_back_with_the_observation(self, env, monkeypatch):
        """A provenance failure must abort the whole collection, not orphan it."""
        _, _, db = env
        import forward_evidence.runner_base as rb
        monkeypatch.setattr(rb, "framework_hash",
                            lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        with pytest.raises(Exception):
            collect(env, inputs=[])
        c = sqlite3.connect(db)
        assert c.execute("SELECT COUNT(*) FROM signal_log").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM run_provenance").fetchone()[0] == 0
        ok = c.execute("SELECT COUNT(*) FROM run_log WHERE status='ok'").fetchone()[0]
        c.close()
        assert ok == 0, "no 'ok' observation may survive a provenance failure"

    def test_AT04_historical_rows_are_not_retroactively_provenanced(self, env, tmp_path):
        """Requirement 4: pre-existing observations keep NO provenance."""
        _, _, db = env
        c = sqlite3.connect(db)
        c.execute("INSERT INTO run_log (signal_id, run_date, fn_hash, status) "
                  "VALUES ('legacy','2026-01-05','f'*0 || 'abc','ok')")
        c.commit()
        before = c.execute("SELECT id, signal_id, run_date, fn_hash, status, created_at "
                           "FROM run_log WHERE signal_id='legacy'").fetchone()
        c.close()
        ids = resolve_input_identity(write_store(tmp_path / "s", 1), "2026-01-01", 40, "price")
        collect(env, inputs=ids)
        c = sqlite3.connect(db)
        after = c.execute("SELECT id, signal_id, run_date, fn_hash, status, created_at "
                          "FROM run_log WHERE signal_id='legacy'").fetchone()
        orphan = c.execute("SELECT COUNT(*) FROM run_provenance WHERE run_id=?",
                           (before[0],)).fetchone()[0]
        c.close()
        assert after == before, "historical row must be byte-identical"
        assert orphan == 0, "must not fabricate provenance for a legacy observation"


# ===========================================================================
# CB — cost_bps bounds (6B.5 rider)
# ===========================================================================

class TestCostBpsBounds:

    def test_CB01_negative_cost_rejected_at_config_read(self):
        with pytest.raises(ValueError, match="must be >= 0"):
            _run_daily._validated_cost_bps({"cost_bps": -5.0}, "probe")

    def test_CB02_zero_cost_remains_valid(self):
        assert _run_daily._validated_cost_bps({"cost_bps": 0.0}, "probe") == 0.0

    def test_CB03_normal_cost_passes_through_unchanged(self):
        assert _run_daily._validated_cost_bps({"cost_bps": 15.0}, "probe") == 15.0

    def test_CB04_missing_and_malformed_still_fail_closed(self):
        with pytest.raises(KeyError):
            _run_daily._validated_cost_bps({}, "probe")
        with pytest.raises(ValueError):
            _run_daily._validated_cost_bps({"cost_bps": "abc"}, "probe")

    def test_CB05_schema_rejects_negative_cost_on_new_databases(self, env):
        _, _, db = env
        c = sqlite3.connect(db)
        c.execute("INSERT INTO trade_entries (signal_id, entry_date, direction, "
                  "entry_close_price, planned_exit_date) VALUES ('p','2026-01-01',1,100.0,"
                  "'2026-01-08')")
        c.commit()
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("INSERT INTO trade_exits (entry_id, exit_date, exit_close_price, "
                      "cost_bps) VALUES (1,'2026-01-08',100.0,-9999.0)")
            c.commit()
        c.close()

    def test_CB06_registered_cost_convention_is_unchanged(self, env):
        """
        6B.5 established the per-trade single charge is a REGISTERED convention
        (label spec 57ce016e51a0), not a defect. This gate must not alter it.
        """
        _, _, db = env
        c = sqlite3.connect(db)
        c.execute("INSERT INTO trade_entries (signal_id, entry_date, direction, "
                  "entry_close_price, planned_exit_date, open) VALUES "
                  "('p','2026-01-01',1,100.0,'2026-01-08',0)")
        c.execute("INSERT INTO trade_exits (entry_id, exit_date, exit_close_price, "
                  "cost_bps) VALUES (1,'2026-01-08',100.0,15.0)")
        c.commit()
        net = c.execute("SELECT net_return FROM trade_returns").fetchone()[0]
        c.close()
        assert net == pytest.approx(-0.0015), "cost still charged ONCE per trade"


# ===========================================================================
# SC — scope guards
# ===========================================================================

class TestScopeGuards:

    def test_SC01_provenance_code_touches_no_protected_artifact(self):
        """No new code path may reference registry/thresholds/lockbox/ledger."""
        # Artifact PATHS, not bare words: run_date_policy.py legitimately
        # discusses the lockbox window — refusing dates inside it is its job.
        # What must never appear is a reference to a protected artifact FILE.
        forbidden = ("registry.yaml", "thresholds.yaml", "ledger.db",
                     "BASELINE_46.json", "lockbox_validation.json")
        for name in ("runner_base.py", "run_daily.py", "run_date_policy.py"):
            src = (_HERE / name).read_text(encoding="utf-8")
            code = "\n".join(l for l in src.splitlines()
                             if not l.strip().startswith(("#", '"', "'")))
            for f in forbidden:
                assert f not in code, f"{name} must not reference {f}"

    def test_SC02_signal_calculation_is_untouched(self):
        """Provenance must not appear inside any decision function."""
        for p in _HERE.glob("decision_function*.py"):
            src = p.read_text(encoding="utf-8")
            for tok in ("run_inputs", "run_provenance", "framework_hash"):
                assert tok not in src, f"{p.name} must stay free of provenance concerns"
