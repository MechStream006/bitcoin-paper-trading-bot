"""
Regression tests for daily_job.py's "nothing new to process" path.

Before this fix, main() returned from the `if not to_process:` branch with
zero trace of the invocation in run_log -- systemd saw a clean exit, but a
genuine no-op (already up to date) was indistinguishable from an upstream
corpus refresh that silently hadn't advanced yet before this job ran. That
ambiguity is exactly what produced the undocumented 2026-07-01 and 08-01
gaps found by the evidence-collection integrity audit. This file proves the
fix without touching the real forward_evidence.db.

Run from research/:
    conda run -n btc_bot pytest forward_evidence/tests/test_daily_job_no_new_data.py -v
"""
from __future__ import annotations

import pathlib
import sqlite3
import sys

import pandas as pd
import pytest

from forward_evidence import daily_job
from forward_evidence.init_db import init_db


def _synthetic_corpus(n_days: int = 40, start_ts: int = 1_700_000_000_000) -> pd.DataFrame:
    """Minimal corpus shaped like _load_corpus()'s real return value."""
    day_ms = 86_400_000
    day_ts = [start_ts + i * day_ms for i in range(n_days)]
    return pd.DataFrame({
        "day_ts": day_ts,
        "spot_open": [50000.0 + i for i in range(n_days)],
        "spot_high": [50100.0 + i for i in range(n_days)],
        "spot_low":  [49900.0 + i for i in range(n_days)],
        "spot_close": [50050.0 + i for i in range(n_days)],
        "spot_volume": [1000.0] * n_days,
        "funding_bps": [1.0] * n_days,
        "perp_close": [50060.0 + i for i in range(n_days)],
        "dvol_close": [55.0] * n_days,
        "basis_bps": [2.0] * n_days,
    })


@pytest.fixture()
def db_path(tmp_path: pathlib.Path) -> pathlib.Path:
    p = tmp_path / "forward_evidence.db"
    init_db(p)
    return p


def _mark_processed(db_path: pathlib.Path, day_ts: int, spot_close: float = 50000.0) -> None:
    """Insert a minimal-but-schema-valid corpus_daily row so
    _already_processed() reports this day as done, forcing the 'nothing new
    to process' branch deterministically. Only day_ts, spot_close, and
    ingested_at are NOT NULL; everything else is left at its natural NULL."""
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO corpus_daily (day_ts, spot_close, ingested_at) VALUES (?, ?, ?)",
        (day_ts, spot_close, day_ts),
    )
    con.commit()
    con.close()


class TestNoNewDataRecording:
    def test_NND01_writes_run_log_row_when_nothing_to_process(
        self, monkeypatch, db_path: pathlib.Path,
    ) -> None:
        """A day already in corpus_daily must still leave a run_log row behind."""
        corpus = _synthetic_corpus()
        last_day_ts = int(corpus["day_ts"].iloc[-1])

        _mark_processed(db_path, last_day_ts)

        monkeypatch.setattr(daily_job, "_load_corpus", lambda: corpus)
        monkeypatch.setattr(daily_job, "DB_PATH", db_path)
        monkeypatch.setattr(sys, "argv", ["daily_job.py"])

        daily_job.main()

        con = sqlite3.connect(db_path)
        rows = con.execute(
            "SELECT status, data_day_ts, signals_computed, notes FROM run_log"
        ).fetchall()
        con.close()

        assert len(rows) == 1, "exactly one run_log row must exist for the no-op invocation"
        status, data_day_ts, signals_computed, notes = rows[0]
        assert status == "no_new_data"
        assert data_day_ts == last_day_ts
        assert signals_computed == 0
        assert "already in corpus_daily" in notes

    def test_NND02_dry_run_writes_no_run_log_row(
        self, monkeypatch, db_path: pathlib.Path,
    ) -> None:
        """--dry-run must never write, including on the no-op path."""
        corpus = _synthetic_corpus()
        last_day_ts = int(corpus["day_ts"].iloc[-1])
        _mark_processed(db_path, last_day_ts)

        monkeypatch.setattr(daily_job, "_load_corpus", lambda: corpus)
        monkeypatch.setattr(daily_job, "DB_PATH", db_path)
        monkeypatch.setattr(sys, "argv", ["daily_job.py", "--dry-run"])

        daily_job.main()

        con = sqlite3.connect(db_path)
        n = con.execute("SELECT COUNT(*) FROM run_log").fetchone()[0]
        con.close()
        assert n == 0

    def test_NND03_normal_processing_path_unaffected(
        self, monkeypatch, db_path: pathlib.Path,
    ) -> None:
        """When there IS new data, the existing (unchanged) run_log write at
        the end of main() must still fire exactly once, with status derived
        from errors as before -- this fix must not touch that path."""
        corpus = _synthetic_corpus()
        # Nothing marked processed -> --backfill will find every day new.
        monkeypatch.setattr(daily_job, "_load_corpus", lambda: corpus)
        monkeypatch.setattr(daily_job, "DB_PATH", db_path)
        monkeypatch.setattr(sys, "argv", ["daily_job.py", "--backfill"])

        daily_job.main()

        con = sqlite3.connect(db_path)
        rows = con.execute("SELECT status, signals_computed FROM run_log").fetchall()
        con.close()

        assert len(rows) == 1, "backfill of N new days is still one run_log row, per existing design"
        status, signals_computed = rows[0]
        assert status in ("ok", "warning")
        assert signals_computed == len(corpus)

    def test_NND04_second_invocation_same_day_now_correctly_recorded(
        self, monkeypatch, db_path: pathlib.Path,
    ) -> None:
        """Simulates the exact real-world sequence: a normal run processes the
        corpus, then a second same-day invocation (corpus not yet advanced)
        must record status='no_new_data' rather than vanish silently."""
        corpus = _synthetic_corpus()
        monkeypatch.setattr(daily_job, "_load_corpus", lambda: corpus)
        monkeypatch.setattr(daily_job, "DB_PATH", db_path)

        monkeypatch.setattr(sys, "argv", ["daily_job.py", "--backfill"])
        daily_job.main()

        monkeypatch.setattr(sys, "argv", ["daily_job.py"])  # daily mode, same corpus
        daily_job.main()

        con = sqlite3.connect(db_path)
        rows = con.execute(
            "SELECT status FROM run_log ORDER BY run_ts"
        ).fetchall()
        con.close()

        assert len(rows) == 2
        assert rows[1][0] == "no_new_data"
