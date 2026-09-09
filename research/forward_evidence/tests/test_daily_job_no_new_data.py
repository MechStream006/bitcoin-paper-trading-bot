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


class TestMonthRolloverGap:
    """The 1st of the month was collected on none of three attempts.

    `daily_ingest` writes only the current month, so at 00:05 UTC on the 1st
    the new partition has no bar yet and the corpus still ends on the last day
    of the previous month. The run correctly records `no_new_data`. By the
    next run the corpus holds BOTH day 1 and day 2 -- but daily mode took only
    `corpus.iloc[-1]`, so day 1 was skipped and never revisited.

    Confirmed on production three times: 2026-07-01, 2026-08-01, 2026-09-01,
    the last of which was predicted by FB-10c-H6 before it happened.
    """

    def _run(self, monkeypatch, db_path, corpus, argv=("daily_job.py",)):
        monkeypatch.setattr(daily_job, "_load_corpus", lambda: corpus)
        monkeypatch.setattr(daily_job, "DB_PATH", db_path)
        monkeypatch.setattr(sys, "argv", list(argv))
        daily_job.main()

    def _processed_days(self, db_path):
        con = sqlite3.connect(db_path)
        rows = [r[0] for r in con.execute(
            "SELECT day_ts FROM corpus_daily ORDER BY day_ts")]
        con.close()
        return rows

    def test_MR01_day_after_a_gap_also_collects_the_gap_day(
        self, monkeypatch, db_path: pathlib.Path,
    ) -> None:
        """The exact production sequence, reproduced end to end."""
        full = _synthetic_corpus(n_days=10)
        # Run 1: lake ends at index 6 -- the "last day of the previous month".
        self._run(monkeypatch, db_path, full.iloc[:7].copy(),
                  ["daily_job.py", "--backfill"])
        assert len(self._processed_days(db_path)) == 7

        # Run 2 (the 1st): lake has not advanced. Correctly a no-op.
        self._run(monkeypatch, db_path, full.iloc[:7].copy())
        assert len(self._processed_days(db_path)) == 7

        # Run 3 (the 2nd): lake now carries BOTH day 7 and day 8.
        self._run(monkeypatch, db_path, full.iloc[:9].copy())

        got = self._processed_days(db_path)
        want = [int(x) for x in full["day_ts"].iloc[:9]]
        assert got == want, "the skipped day-1 row must now be collected too"

    def test_MR02_consecutive_month_boundaries_lose_nothing(
        self, monkeypatch, db_path: pathlib.Path,
    ) -> None:
        """Three consecutive rollovers, mirroring Jul/Aug/Sep 2026."""
        full = _synthetic_corpus(n_days=30)
        self._run(monkeypatch, db_path, full.iloc[:5].copy(),
                  ["daily_job.py", "--backfill"])
        for stall_at in (5, 12, 19):
            self._run(monkeypatch, db_path, full.iloc[:stall_at].copy())      # the 1st: no-op
            self._run(monkeypatch, db_path, full.iloc[:stall_at + 2].copy())  # the 2nd: catch up
        got = self._processed_days(db_path)
        want = [int(x) for x in full["day_ts"].iloc[:21]]
        assert got == want, "no day may be lost across three rollovers"

    def test_MR03_multi_day_outage_is_recovered_forward(
        self, monkeypatch, db_path: pathlib.Path,
    ) -> None:
        """A longer stall recovers every missed day, in order."""
        full = _synthetic_corpus(n_days=20)
        self._run(monkeypatch, db_path, full.iloc[:5].copy(),
                  ["daily_job.py", "--backfill"])
        self._run(monkeypatch, db_path, full.iloc[:12].copy())
        assert self._processed_days(db_path) == [int(x) for x in full["day_ts"].iloc[:12]]

    def test_MR04_no_retroactive_backfill_below_the_high_water_mark(
        self, monkeypatch, db_path: pathlib.Path,
    ) -> None:
        """Deploying this must not resurrect the three historical gaps.

        A day already BELOW the high-water mark stays absent: reprocessing
        history is --backfill's job, and this task must alter no recorded
        evidence.
        """
        full = _synthetic_corpus(n_days=10)
        con = sqlite3.connect(db_path)
        for i in (0, 1, 3, 4):                    # index 2 is a pre-existing gap
            con.execute(
                "INSERT INTO corpus_daily (day_ts, spot_close, ingested_at) VALUES (?,?,?)",
                (int(full["day_ts"].iloc[i]), 50000.0, 0))
        con.commit(); con.close()

        self._run(monkeypatch, db_path, full.iloc[:6].copy())

        got = self._processed_days(db_path)
        assert int(full["day_ts"].iloc[2]) not in got, "historical gap must NOT be backfilled"
        assert int(full["day_ts"].iloc[5]) in got, "the new day above the mark is collected"

    def test_MR05_idempotent_when_nothing_advanced(
        self, monkeypatch, db_path: pathlib.Path,
    ) -> None:
        """Repeat runs on an unchanged corpus stay a no-op."""
        full = _synthetic_corpus(n_days=8)
        self._run(monkeypatch, db_path, full, ["daily_job.py", "--backfill"])
        before = self._processed_days(db_path)
        self._run(monkeypatch, db_path, full)
        self._run(monkeypatch, db_path, full)
        assert self._processed_days(db_path) == before
        con = sqlite3.connect(db_path)
        statuses = [r[0] for r in con.execute("SELECT status FROM run_log ORDER BY run_ts")]
        con.close()
        # Asserted by content, not by row count: run_log is INSERT OR REPLACE
        # keyed on run_ts = int(time*1000), so two main() calls inside the same
        # millisecond collapse into one row. Counting rows here made the test
        # depend on how fast the suite happened to run -- it passed alone and
        # failed under the full suite. Production runs daily and never collides.
        assert statuses[-1] == "no_new_data"
        assert set(statuses[1:]) == {"no_new_data"}, statuses

    def test_MR06_fresh_db_daily_mode_still_takes_one_day(
        self, monkeypatch, db_path: pathlib.Path,
    ) -> None:
        """Unchanged behaviour: an empty DB in daily mode seeds one day, not a
        whole history. Backfilling stays an explicit --backfill choice."""
        full = _synthetic_corpus(n_days=25)
        self._run(monkeypatch, db_path, full)
        assert self._processed_days(db_path) == [int(full["day_ts"].iloc[-1])]

    def test_MR07_signal_semantics_and_timestamps_preserved(
        self, monkeypatch, db_path: pathlib.Path,
    ) -> None:
        """Catching up day-by-day must equal processing the same days as one
        backfill -- identical day_ts and identical signal values."""
        full = _synthetic_corpus(n_days=14)

        self._run(monkeypatch, db_path, full.iloc[:5].copy(), ["daily_job.py", "--backfill"])
        self._run(monkeypatch, db_path, full.iloc[:9].copy())      # gap recovery
        con = sqlite3.connect(db_path)
        caught_up = con.execute(
            "SELECT day_ts, h6_canonical, in_hold FROM signal_log ORDER BY day_ts").fetchall()
        con.close()

        other = db_path.parent / "reference.db"
        init_db(other)
        self._run(monkeypatch, other, full.iloc[:9].copy(), ["daily_job.py", "--backfill"])
        con = sqlite3.connect(other)
        reference = con.execute(
            "SELECT day_ts, h6_canonical, in_hold FROM signal_log ORDER BY day_ts").fetchall()
        con.close()

        assert caught_up == reference, "recovered days must be identical to a straight backfill"
