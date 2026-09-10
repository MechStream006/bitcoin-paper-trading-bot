"""Tests for the execution-outcome scheduler job (src/strategies/outcome_jobs.py).

The sweep logic itself is covered by test_outcome_writer.py. These cover the
wiring: registration gating, `.outcome-status.json`, and the guarantee that a
research failure never propagates.
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
from datetime import datetime, timezone

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.strategies import outcome_jobs as OJ  # noqa: E402


class FakeScheduler:
    def __init__(self):
        self.jobs = {}

    def has_job(self, job_id):
        return job_id in self.jobs

    def register(self, name, fn, trigger, job_id=None, **kw):
        self.jobs[job_id or name] = (name, fn, trigger, kw)


class _Result:
    def __init__(self, **kw):
        self.scanned = kw.get("scanned", 0)
        self.already_labelled = kw.get("already_labelled", 0)
        self.persisted = kw.get("persisted", 0)
        self.unchanged = kw.get("unchanged", 0)
        self.pending = kw.get("pending", 0)
        self.anomalies = tuple(kw.get("anomalies", ()))


def _status(data_dir):
    p = data_dir / OJ.OUTCOME_STATUS_FILENAME
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_disabled_strategy_registers_no_job(tmp_path):
    s = FakeScheduler()
    assert OJ.register_outcome_writer_job(
        s, trading_db_path=tmp_path / "x.db", strategy_enabled=False) is False
    assert s.jobs == {}


def test_enabled_registers_one_interval_job(tmp_path):
    s = FakeScheduler()
    assert OJ.register_outcome_writer_job(
        s, trading_db_path=tmp_path / "x.db", strategy_enabled=True) is True
    assert OJ.OUTCOME_JOB_ID in s.jobs
    _, _, trigger, kw = s.jobs[OJ.OUTCOME_JOB_ID]
    assert trigger == "interval"
    assert kw["seconds"] == OJ.DEFAULT_OUTCOME_INTERVAL_SEC


def test_registration_is_idempotent(tmp_path):
    s = FakeScheduler()
    OJ.register_outcome_writer_job(s, trading_db_path=tmp_path / "x.db",
                                  strategy_enabled=True)
    assert OJ.register_outcome_writer_job(
        s, trading_db_path=tmp_path / "x.db", strategy_enabled=True) is False
    assert len(s.jobs) == 1


# ---------------------------------------------------------------------------
# Status sidecar
# ---------------------------------------------------------------------------

def test_a_clean_sweep_writes_an_ok_status(tmp_path, monkeypatch):
    monkeypatch.setattr(OJ, "_run_sweep",
                        lambda *a: _Result(scanned=3, persisted=1, pending=2))
    OJ.run_outcome_sweep(tmp_path / "btc.db",
                         status_path=tmp_path / OJ.OUTCOME_STATUS_FILENAME)
    s = _status(tmp_path)
    assert s["status"] == "ok" and s["persisted"] == 1 and s["pending"] == 2
    assert s["anomalies"] == []


def test_a_sweep_exception_writes_a_failed_status_and_does_not_raise(
        tmp_path, monkeypatch):
    def boom(*a):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(OJ, "_run_sweep", boom)
    OJ.run_outcome_sweep(tmp_path / "btc.db",
                         status_path=tmp_path / OJ.OUTCOME_STATUS_FILENAME)
    s = _status(tmp_path)
    assert s["status"] == "failed"
    assert "database is locked" in s["detail"]


def test_anomalies_are_carried_into_the_status(tmp_path, monkeypatch):
    monkeypatch.setattr(OJ, "_run_sweep", lambda *a: _Result(
        scanned=1, anomalies=("g3-x: NO_TRADE contradicted: an order is FILLED",)))
    OJ.run_outcome_sweep(tmp_path / "btc.db",
                         status_path=tmp_path / OJ.OUTCOME_STATUS_FILENAME)
    s = _status(tmp_path)
    assert s["status"] == "ok"
    assert len(s["anomalies"]) == 1


def test_a_status_write_failure_is_swallowed(tmp_path, monkeypatch, caplog):
    import logging
    monkeypatch.setattr(OJ, "_run_sweep", lambda *a: _Result(scanned=0))
    monkeypatch.setattr(OJ.json, "dumps",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no space")))
    with caplog.at_level(logging.WARNING):
        OJ.run_outcome_sweep(tmp_path / "btc.db",
                             status_path=tmp_path / OJ.OUTCOME_STATUS_FILENAME)
    assert _status(tmp_path) is None
    assert any("could not write outcome status" in m for m in caplog.messages)


def test_the_registered_job_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(OJ, "_run_sweep",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("kaboom")))
    s = FakeScheduler()
    OJ.register_outcome_writer_job(
        s, trading_db_path=tmp_path / "btc.db", strategy_enabled=True,
        status_path=tmp_path / OJ.OUTCOME_STATUS_FILENAME)
    _, fn, _, _ = s.jobs[OJ.OUTCOME_JOB_ID]
    fn()                       # must not raise into the scheduler
    assert _status(tmp_path)["status"] == "failed"


def test_last_run_wins(tmp_path, monkeypatch):
    sp = tmp_path / OJ.OUTCOME_STATUS_FILENAME
    monkeypatch.setattr(OJ, "_run_sweep",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    OJ.run_outcome_sweep(tmp_path / "btc.db", status_path=sp)
    assert _status(tmp_path)["status"] == "failed"
    monkeypatch.setattr(OJ, "_run_sweep", lambda *a: _Result(scanned=1, persisted=1))
    OJ.run_outcome_sweep(tmp_path / "btc.db", status_path=sp)
    assert _status(tmp_path)["status"] == "ok"


# ---------------------------------------------------------------------------
# Trading isolation
# ---------------------------------------------------------------------------

def test_the_job_holds_no_connection_and_only_reads_trading(tmp_path):
    """The job is handed a path, never a connection, and opens the trading DB
    read-only when it fires -- it cannot participate in a trading transaction."""
    import ast
    src = (_ROOT / "src" / "strategies" / "outcome_jobs.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "register_outcome_writer_job":
            arg_names = {a.arg for a in node.args.args + node.args.kwonlyargs}
            assert "conn" not in arg_names
            assert "trading_db_path" in arg_names

    # lifecycle wires it with a path, not strategy_conn.
    lc = (_ROOT / "src" / "runtime" / "lifecycle.py").read_text(encoding="utf-8")
    assert "register_outcome_writer_job(" in lc
    i = lc.index("register_outcome_writer_job(")
    call = lc[i:i + 400]
    assert "trading_db_path=self._db_path" in call
    assert "conn=" not in call
