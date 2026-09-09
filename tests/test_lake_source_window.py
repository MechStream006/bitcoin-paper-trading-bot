"""Windowed reads for `align.load_lake_source`.

Unbounded, the loader reads every partition of a source: for `spot_1m` that is
69 partitions and ~104 MB of 1m bars since 2021, materialised as Observations.
That is fine for an offline sweep and unusable for a caller that needs one
recent bar. These tests pin the window's semantics, that unbounded callers are
unchanged, and -- the point of the exercise -- that pruning happens at the
PARTITION level rather than by loading everything and filtering afterwards.
"""
from __future__ import annotations

import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

_RESEARCH = pathlib.Path(__file__).resolve().parent.parent / "research"
if str(_RESEARCH) not in sys.path:
    sys.path.insert(0, str(_RESEARCH))

from alignment import align as AL  # noqa: E402
from alignment.timebase import get_spec  # noqa: E402

pq = pytest.importorskip("pyarrow.parquet")
pa = pytest.importorskip("pyarrow")

DAY_MS = 86_400_000
MIN_MS = 60_000


def _ts(y, m, d, hh=0, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


@pytest.fixture()
def lake(tmp_path: pathlib.Path) -> pathlib.Path:
    """Three monthly spot partitions, one bar per day, with manifests."""
    base = tmp_path / "raw"
    for year, month, ndays in ((2026, 6, 30), (2026, 7, 31), (2026, 8, 31)):
        part = base / "spot" / "BTCUSDT" / f"year={year}" / f"month={month:02d}"
        part.mkdir(parents=True, exist_ok=True)
        rows = [{"open_time": _ms(_ts(year, month, d + 1)),
                 "close": 1000.0 + d} for d in range(ndays)]
        pq.write_table(pa.Table.from_pandas(pd.DataFrame(rows)),
                       part / "data.parquet")
        (part / "_manifest.json").write_text(
            '{"parquet_sha256": "sha-%d-%02d"}' % (year, month), encoding="utf-8")
    return base


def _days(obs) -> list[str]:
    return [o.available_ts.date().isoformat() for o in obs]


class TestUnboundedIsUnchanged:
    def test_LW01_no_bounds_reads_every_partition(self, lake):
        obs = AL.load_lake_source("spot_1m", base_dir=lake,
                                  payload_fields=("close",))
        assert len(obs) == 30 + 31 + 31
        months = {(o.provenance["year"], o.provenance["month"]) for o in obs}
        assert months == {(2026, 6), (2026, 7), (2026, 8)}

    def test_LW02_provenance_and_sha_still_attached(self, lake):
        obs = AL.load_lake_source("spot_1m", base_dir=lake,
                                  payload_fields=("close",))
        first = obs[0]
        assert first.provenance["dataset"] == "spot"
        assert first.provenance["symbol"] == "BTCUSDT"
        assert first.provenance["parquet_sha256"].startswith("sha-2026-")

    def test_LW03_still_sorted_by_available_ts(self, lake):
        obs = AL.load_lake_source("spot_1m", base_dir=lake,
                                  payload_fields=("close",))
        assert [o.available_ts for o in obs] == sorted(o.available_ts for o in obs)


class TestBoundedWindow:
    def test_LW04_window_returns_only_rows_inside_it(self, lake):
        lo, hi = _ts(2026, 7, 10), _ts(2026, 7, 12)
        obs = AL.load_lake_source("spot_1m", base_dir=lake,
                                  payload_fields=("close",), lo=lo, hi=hi)
        assert all(lo <= o.available_ts <= hi for o in obs)
        # available_ts = open_time + 60s, so day D's bar lands at D 00:01.
        # The 12th's bar is available at 00:01, one minute PAST hi, so the
        # window ends at the 11th. Bounding on event time would wrongly
        # include it -- which is exactly the distinction being pinned.
        assert _days(obs) == ["2026-07-10", "2026-07-11"]

    def test_LW05_bounds_are_inclusive_on_available_ts(self, lake):
        """The bound is on availability, not event time -- the same field the
        loader sorts by and as-of matching consumes."""
        spec = get_spec("spot_1m")
        assert spec.availability_field is None and spec.availability_offset_s == 60.0
        exact = _ts(2026, 7, 10) + timedelta(seconds=60)
        obs = AL.load_lake_source("spot_1m", base_dir=lake,
                                  payload_fields=("close",), lo=exact, hi=exact)
        assert [o.available_ts for o in obs] == [exact]
        # One second inside the bar's event time is NOT enough: availability is
        # the bar's close, so bounding at open_time alone would over-select.
        obs2 = AL.load_lake_source("spot_1m", base_dir=lake,
                                   payload_fields=("close",),
                                   lo=_ts(2026, 7, 10), hi=_ts(2026, 7, 10))
        assert obs2 == []

    def test_LW06_lo_only_and_hi_only(self, lake):
        lo_only = AL.load_lake_source("spot_1m", base_dir=lake,
                                      payload_fields=("close",),
                                      lo=_ts(2026, 8, 29))
        assert _days(lo_only) == ["2026-08-29", "2026-08-30", "2026-08-31"]
        hi_only = AL.load_lake_source("spot_1m", base_dir=lake,
                                      payload_fields=("close",),
                                      hi=_ts(2026, 6, 3))
        # Same +60s edge: the 3rd's bar is available at 00:01, past hi.
        assert _days(hi_only) == ["2026-06-01", "2026-06-02"]

    def test_LW07_empty_window_returns_nothing(self, lake):
        obs = AL.load_lake_source("spot_1m", base_dir=lake,
                                  payload_fields=("close",),
                                  lo=_ts(2027, 1, 1), hi=_ts(2027, 2, 1))
        assert obs == []

    def test_LW08_inverted_window_is_refused(self, lake):
        with pytest.raises(ValueError, match="is after"):
            AL.load_lake_source("spot_1m", base_dir=lake,
                                lo=_ts(2026, 8, 1), hi=_ts(2026, 7, 1))


class TestPruningActuallyHappens:
    """The whole point: a windowed read must be proportional to the window."""

    def _record_reads(self, monkeypatch):
        seen: list[tuple[int, int]] = []
        real = AL_lake_read = __import__("storage.lake", fromlist=["lake"]).read_partition

        def spy(base, dataset, symbol, year, month):
            seen.append((year, month))
            return real(base, dataset, symbol, year, month)

        monkeypatch.setattr("storage.lake.read_partition", spy)
        return seen

    def test_LW09_only_the_covering_partition_is_read(self, lake, monkeypatch):
        seen = self._record_reads(monkeypatch)
        AL.load_lake_source("spot_1m", base_dir=lake, payload_fields=("close",),
                            lo=_ts(2026, 7, 10), hi=_ts(2026, 7, 12))
        assert seen == [(2026, 7)], f"read {seen}; must not touch other partitions"

    def test_LW10_window_spanning_two_months_reads_exactly_two(self, lake, monkeypatch):
        seen = self._record_reads(monkeypatch)
        AL.load_lake_source("spot_1m", base_dir=lake, payload_fields=("close",),
                            lo=_ts(2026, 7, 30), hi=_ts(2026, 8, 2))
        assert seen == [(2026, 7), (2026, 8)]

    def test_LW11_unbounded_still_reads_all(self, lake, monkeypatch):
        seen = self._record_reads(monkeypatch)
        AL.load_lake_source("spot_1m", base_dir=lake, payload_fields=("close",))
        assert seen == [(2026, 6), (2026, 7), (2026, 8)]

    def test_LW12_offset_is_honoured_at_the_partition_edge(self, lake, monkeypatch):
        """A bar opened 23:59 on the last of a month becomes available at 00:00
        the next -- so a window starting there must still read the OLD
        partition, or that bar is silently lost."""
        seen = self._record_reads(monkeypatch)
        AL.load_lake_source("spot_1m", base_dir=lake, payload_fields=("close",),
                            lo=_ts(2026, 8, 1), hi=_ts(2026, 8, 1, 0, 5))
        assert (2026, 7) in seen, (
            "the July partition can still supply availability at 2026-08-01T00:00")


class TestNoLookaheadPreserved:
    def test_LW13_windowed_rows_never_precede_their_event(self, lake):
        obs = AL.load_lake_source("spot_1m", base_dir=lake,
                                  payload_fields=("close",),
                                  lo=_ts(2026, 7, 1), hi=_ts(2026, 7, 31))
        assert obs
        for o in obs:
            assert o.available_ts >= o.event_ts, "availability precedes the event"

    def test_LW14_window_is_a_subset_of_the_unbounded_read(self, lake):
        """Windowing may only remove rows -- never alter or invent one."""
        full = AL.load_lake_source("spot_1m", base_dir=lake,
                                   payload_fields=("close",))
        lo, hi = _ts(2026, 7, 5), _ts(2026, 8, 5)
        win = AL.load_lake_source("spot_1m", base_dir=lake,
                                  payload_fields=("close",), lo=lo, hi=hi)
        expected = [o for o in full if lo <= o.available_ts <= hi]
        assert [(o.event_ts, o.available_ts, o.payload) for o in win] == \
               [(o.event_ts, o.available_ts, o.payload) for o in expected]

    def test_LW15_check_no_lookahead_passes_on_a_windowed_read(self, lake):
        decision = _ts(2026, 7, 15)
        obs = AL.load_lake_source("spot_1m", base_dir=lake,
                                  payload_fields=("close",),
                                  lo=decision - timedelta(days=1), hi=decision)
        rows = [{"decision_ts": decision, "matched": True,
                 "available_ts": o.available_ts, "age_s": 0.0} for o in obs]
        assert AL.check_no_lookahead(rows) == []
