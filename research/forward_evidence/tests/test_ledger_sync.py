"""
test_ledger_sync.py
===================
Tests for the forward-evidence -> governance-ledger bridge (Gate FB-1b).

Covers the four properties the bridge must hold:
  1. Integration      -- a live-forward run actually lands in ledger.db.
  2. Point-in-time    -- open trades are never counted; exit_date <= as_of.
  3. Determinism      -- identical inputs produce identical metrics.
  4. Safety           -- no promotion: no state transition, DSR/BH-p stay NULL,
                         and a live-forward run can never satisfy the Validated
                         gate on its own.
Plus idempotency, lane (L2) correctness, pinned-run authority, schema
assert-never-repair, and fault isolation up to run_daily's exit code.
"""
from __future__ import annotations

import math
import pathlib
import sqlite3
import sys

import pytest

_HERE          = pathlib.Path(__file__).resolve().parent
_FWD           = _HERE.parent
_RESEARCH_ROOT = _FWD.parent
_EV_LIB        = _RESEARCH_ROOT / "evidence_library"
for _p in (str(_RESEARCH_ROOT), str(_EV_LIB)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from forward_evidence import ledger_sync  # noqa: E402

# evidence_library is deliberately absent from Dockerfile.research (INV-1), so
# `record` / `lifecycle` cannot be imported there. Skip the whole module for that
# one reason only -- an ImportError for any other cause must still fail loudly.
if not _EV_LIB.exists():
    pytest.skip(
        "evidence_library not present (Dockerfile.research excludes it by INV-1)",
        allow_module_level=True,
    )

import lifecycle  # noqa: E402
import record  # noqa: E402

_SCHEMA = (_FWD / "schema_forward.sql").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_forward_db(path: pathlib.Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def _add_run(conn, signal_id: str, run_date: str, fn_hash: str = "abc123") -> int:
    cur = conn.execute(
        "INSERT INTO run_log (signal_id, run_date, fn_hash, status) VALUES (?,?,?,'ok')",
        (signal_id, run_date, fn_hash),
    )
    return cur.lastrowid


def _add_closed_trade(
    conn, signal_id: str, entry_date: str, exit_date: str,
    entry_px: float, exit_px: float, direction: int = 1, cost_bps: float = 15.0,
) -> int:
    cur = conn.execute(
        "INSERT INTO trade_entries "
        "(signal_id, entry_date, direction, entry_close_price, planned_exit_date, open) "
        "VALUES (?,?,?,?,?,0)",
        (signal_id, entry_date, direction, entry_px, exit_date),
    )
    entry_id = cur.lastrowid
    conn.execute(
        "INSERT INTO trade_exits (entry_id, exit_date, exit_close_price, cost_bps) "
        "VALUES (?,?,?,?)",
        (entry_id, exit_date, exit_px, cost_bps),
    )
    return entry_id


def _add_open_trade(
    conn, signal_id: str, entry_date: str, planned_exit: str,
    entry_px: float, direction: int = 1,
) -> int:
    cur = conn.execute(
        "INSERT INTO trade_entries "
        "(signal_id, entry_date, direction, entry_close_price, planned_exit_date, open) "
        "VALUES (?,?,?,?,?,1)",
        (signal_id, entry_date, direction, entry_px, planned_exit),
    )
    return cur.lastrowid


@pytest.fixture()
def fwd_db(tmp_path):
    p = tmp_path / "evidence_forward.db"
    conn = _make_forward_db(p)
    _add_run(conn, "h5_vol_regime", "2026-06-15")
    _add_run(conn, "h5_vol_regime", "2026-06-20")
    # 3 closed trades, 5-day holds
    _add_closed_trade(conn, "h5_vol_regime", "2026-06-15", "2026-06-20", 100.0, 110.0)   # +10% gross
    _add_closed_trade(conn, "h5_vol_regime", "2026-06-21", "2026-06-26", 100.0,  95.0)   # -5%  gross
    _add_closed_trade(conn, "h5_vol_regime", "2026-06-27", "2026-07-02", 100.0, 104.0)   # +4%  gross
    conn.commit()
    conn.close()
    return p


@pytest.fixture()
def ledger_db(tmp_path):
    p = tmp_path / "ledger.db"
    record.init_db(p)
    return p


# ---------------------------------------------------------------------------
# 1. Integration -- the run actually lands, with live-forward provenance
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_live_forward_run_is_written(self, fwd_db, ledger_db):
        outcome, run_id = ledger_sync.sync_family(
            "h5_vol_regime", "2026-07-02", fwd_db, ledger_db)
        assert outcome == "ok" and run_id
        run = record.get_run(ledger_db, run_id)
        assert run["provenance"] == "live-forward"
        assert run["family_id"] == "h5_vol_regime"
        assert run["n_trades"] == 3
        assert run["window_end"] == "2026-07-02"

    def test_has_live_evidence_view_now_sees_the_family(self, fwd_db, ledger_db):
        con = sqlite3.connect(ledger_db)
        before = {r[0] for r in con.execute("SELECT family_id FROM has_live_evidence")}
        con.close()
        assert before == set()
        ledger_sync.sync_family("h5_vol_regime", "2026-07-02", fwd_db, ledger_db)
        con = sqlite3.connect(ledger_db)
        after = {r[0] for r in con.execute("SELECT family_id FROM has_live_evidence")}
        con.close()
        assert after == {"h5_vol_regime"}

    def test_no_evidence_when_no_closed_trades(self, tmp_path, ledger_db):
        p = tmp_path / "empty_forward.db"
        conn = _make_forward_db(p)
        _add_run(conn, "g2_funding_level", "2026-08-26")
        _add_open_trade(conn, "g2_funding_level", "2026-08-26", "2026-09-02", 100.0)
        conn.commit()
        conn.close()
        outcome, run_id = ledger_sync.sync_family(
            "g2_funding_level", "2026-08-29", p, ledger_db)
        assert outcome == "no-evidence" and run_id is None

    def test_window_start_is_first_ok_run_not_first_trade(self, fwd_db, ledger_db):
        """The forward observation window opens when collection opens."""
        _, run_id = ledger_sync.sync_family(
            "h5_vol_regime", "2026-07-02", fwd_db, ledger_db)
        assert record.get_run(ledger_db, run_id)["window_start"] == "2026-06-15"

    def test_fn_hash_is_carried_onto_the_run(self, fwd_db, ledger_db):
        _, run_id = ledger_sync.sync_family(
            "h5_vol_regime", "2026-07-02", fwd_db, ledger_db)
        assert record.get_run(ledger_db, run_id)["fn_hash"] == "abc123"


# ---------------------------------------------------------------------------
# 2. Point-in-time -- open trades never count; exit_date <= as_of
# ---------------------------------------------------------------------------

class TestPointInTime:
    def test_open_trades_are_excluded(self, fwd_db, ledger_db):
        conn = sqlite3.connect(fwd_db)
        _add_open_trade(conn, "h5_vol_regime", "2026-07-03", "2026-07-10", 100.0)
        conn.commit()
        conn.close()
        _, run_id = ledger_sync.sync_family(
            "h5_vol_regime", "2026-07-31", fwd_db, ledger_db)
        assert record.get_run(ledger_db, run_id)["n_trades"] == 3

    def test_as_of_truncates_future_exits(self, fwd_db, ledger_db):
        _, run_id = ledger_sync.sync_family(
            "h5_vol_regime", "2026-06-26", fwd_db, ledger_db)
        assert record.get_run(ledger_db, run_id)["n_trades"] == 2

    def test_as_of_before_any_exit_yields_no_evidence(self, fwd_db, ledger_db):
        outcome, run_id = ledger_sync.sync_family(
            "h5_vol_regime", "2026-06-16", fwd_db, ledger_db)
        assert outcome == "no-evidence" and run_id is None


# ---------------------------------------------------------------------------
# 3. Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_metrics_are_stable_across_invocations(self, fwd_db):
        a = ledger_sync.compute_forward_metrics(
            ledger_sync.load_closed_trades(fwd_db, "h5_vol_regime", "2026-07-02"))
        b = ledger_sync.compute_forward_metrics(
            ledger_sync.load_closed_trades(fwd_db, "h5_vol_regime", "2026-07-02"))
        assert a == b

    def test_metrics_match_hand_computed_values(self, fwd_db):
        trades = ledger_sync.load_closed_trades(fwd_db, "h5_vol_regime", "2026-07-02")
        m = ledger_sync.compute_forward_metrics(trades)
        nets = [float(t["net_return"]) for t in trades]
        n = len(nets)
        mean = sum(nets) / n
        var = sum((r - mean) ** 2 for r in nets) / (n - 1)
        stdev = math.sqrt(var)
        assert m["n_trades"] == 3
        assert m["mean_net_return"] == pytest.approx(mean)
        assert m["stdev_net_return"] == pytest.approx(stdev)
        assert m["win_rate"] == pytest.approx(2 / 3)
        assert m["mean_hold_days"] == pytest.approx(5.0)
        expected = (mean / stdev) * math.sqrt(ledger_sync.ANN / 5.0)
        assert m["sharpe"] == pytest.approx(expected)

    def test_trade_order_is_deterministic(self, fwd_db):
        t = ledger_sync.load_closed_trades(fwd_db, "h5_vol_regime", "2026-07-02")
        assert [x["entry_date"] for x in t] == sorted(x["entry_date"] for x in t)

    def test_sharpe_is_none_below_min_trades(self):
        m = ledger_sync.compute_forward_metrics(
            [{"net_return": 0.05, "hold_days_actual": 5, "direction": 1}])
        assert m["n_trades"] == 1 and m["sharpe"] is None

    def test_sharpe_is_none_on_zero_dispersion(self):
        m = ledger_sync.compute_forward_metrics([
            {"net_return": 0.02, "hold_days_actual": 5, "direction": 1},
            {"net_return": 0.02, "hold_days_actual": 5, "direction": 1},
        ])
        assert m["stdev_net_return"] == 0.0 and m["sharpe"] is None

    def test_empty_trade_set(self):
        m = ledger_sync.compute_forward_metrics([])
        assert m["n_trades"] == 0
        assert m["sharpe"] is None and m["mean_net_return"] is None
        assert m["n_long"] == 0 and m["n_short"] == 0

    def test_long_short_counts(self):
        m = ledger_sync.compute_forward_metrics([
            {"net_return": 0.01, "hold_days_actual": 5, "direction": 1},
            {"net_return": -0.01, "hold_days_actual": 5, "direction": -1},
            {"net_return": 0.02, "hold_days_actual": 5, "direction": -1},
        ])
        assert m["n_long"] == 1 and m["n_short"] == 2


# ---------------------------------------------------------------------------
# 4. Lane correctness -- L2, and only L2
# ---------------------------------------------------------------------------

class TestLane:
    def test_run_kind_is_forward(self, fwd_db, ledger_db):
        _, run_id = ledger_sync.sync_family(
            "h5_vol_regime", "2026-07-02", fwd_db, ledger_db)
        assert record.get_run(ledger_db, run_id)["run_kind"] == "forward"

    def test_lane_pair_is_the_frozen_L2(self, fwd_db, ledger_db):
        assert record.LANES["L2"] == (ledger_sync.PROVENANCE, ledger_sync.RUN_KIND)
        _, run_id = ledger_sync.sync_family(
            "h5_vol_regime", "2026-07-02", fwd_db, ledger_db)
        run = record.get_run(ledger_db, run_id)
        assert (run["provenance"], run["run_kind"]) in record._VALID_LANE_PAIRS

    def test_module_constants_cannot_drift_to_an_illegal_lane(self):
        """A future edit to PROVENANCE/RUN_KIND must not create an illegal lane."""
        assert (ledger_sync.PROVENANCE, ledger_sync.RUN_KIND) in record._VALID_LANE_PAIRS

    def test_c1_lane_invariant_holds_after_sync(self, fwd_db, ledger_db):
        """C1: every run has a valid run_kind and a legal lane."""
        ledger_sync.sync_family("h5_vol_regime", "2026-07-02", fwd_db, ledger_db)
        con = sqlite3.connect(ledger_db)
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT provenance, run_kind FROM runs").fetchall()
        con.close()
        for r in rows:
            assert (r["provenance"], r["run_kind"]) in record._VALID_LANE_PAIRS

    def test_c2_one_current_run_per_lane(self, fwd_db, ledger_db):
        """C2: the (family, provenance, run_kind) partition must be unique."""
        ledger_sync.sync_family("h5_vol_regime", "2026-07-02", fwd_db, ledger_db)
        ledger_sync.sync_family("h5_vol_regime", "2026-07-31", fwd_db, ledger_db)
        con = sqlite3.connect(ledger_db)
        dupes = con.execute(
            "SELECT family_id, provenance, run_kind, COUNT(*) n "
            "FROM current_run_per_lane GROUP BY 1,2,3 HAVING n > 1"
        ).fetchall()
        con.close()
        assert dupes == []


# ---------------------------------------------------------------------------
# 5. No promotion -- the bridge supplies evidence, it does not open gates
# ---------------------------------------------------------------------------

class TestNoPromotion:
    def test_no_state_transition_is_written(self, fwd_db, ledger_db):
        con = sqlite3.connect(ledger_db)
        before = con.execute("SELECT COUNT(*) FROM state_transitions").fetchone()[0]
        con.close()
        ledger_sync.sync_family("h5_vol_regime", "2026-07-02", fwd_db, ledger_db)
        con = sqlite3.connect(ledger_db)
        after = con.execute("SELECT COUNT(*) FROM state_transitions").fetchone()[0]
        con.close()
        assert before == after

    def test_dsr_and_bh_p_are_null(self, fwd_db, ledger_db):
        _, run_id = ledger_sync.sync_family(
            "h5_vol_regime", "2026-07-02", fwd_db, ledger_db)
        run = record.get_run(ledger_db, run_id)
        assert run["dsr_prob"] is None and run["bh_p"] is None

    def test_live_forward_run_alone_cannot_pass_validated_gate(self, fwd_db, ledger_db):
        _, run_id = ledger_sync.sync_family(
            "h5_vol_regime", "2026-07-02", fwd_db, ledger_db)
        run = record.get_run(ledger_db, run_id)
        thresholds = {"validated": {"feature": {
            "requires_non_offline_provenance": True,
            "min_dsr_prob": 0.50, "max_bh_p": 0.10,
        }}}
        ok, errors = lifecycle.check_validated_preconditions(
            {"role": ["feature"]}, run, thresholds)
        assert not ok, "a live-forward run with NULL DSR/BH-p must not pass"
        assert any("dsr" in e.lower() or "null" in e.lower() for e in errors), errors

    def test_no_gate_results_are_written(self, fwd_db, ledger_db):
        con = sqlite3.connect(ledger_db)
        before = con.execute("SELECT COUNT(*) FROM gate_results").fetchone()[0]
        con.close()
        ledger_sync.sync_family("h5_vol_regime", "2026-07-02", fwd_db, ledger_db)
        con = sqlite3.connect(ledger_db)
        after = con.execute("SELECT COUNT(*) FROM gate_results").fetchone()[0]
        con.close()
        assert before == after

    def test_oos_sharpe_evidence_score_and_verdict_stay_null(self, fwd_db, ledger_db):
        _, run_id = ledger_sync.sync_family(
            "h5_vol_regime", "2026-07-02", fwd_db, ledger_db)
        run = record.get_run(ledger_db, run_id)
        assert run["oos_sharpe"] is None
        assert run["evidence_score"] is None
        assert run["verdict"] is None


# ---------------------------------------------------------------------------
# 6. Pinned-run authority -- an L2 run must never displace the decision anchor
# ---------------------------------------------------------------------------

class TestPinnedRunAuthority:
    def test_pinned_discovery_run_is_unchanged_by_sync(self, fwd_db, ledger_db):
        pinned = record.append_run(
            ledger_db, family_id="h5_vol_regime",
            metrics={"sharpe": 0.91, "n_trades": 29},
            provenance="offline-corpus", run_kind="discovery",
            window_start="2021-01-01", window_end="2025-05-31",
        )
        before = dict(record.get_run(ledger_db, pinned))
        ledger_sync.sync_family("h5_vol_regime", "2026-07-02", fwd_db, ledger_db)
        assert dict(record.get_run(ledger_db, pinned)) == before

    def test_sync_does_not_become_the_current_run_of_the_pinned_lane(
            self, fwd_db, ledger_db):
        """The L2 run forms its own partition; the discovery lane's current run
        is still the discovery run. This is what PR-5's lane separation buys."""
        pinned = record.append_run(
            ledger_db, family_id="h5_vol_regime",
            metrics={"sharpe": 0.91, "n_trades": 29},
            provenance="offline-corpus", run_kind="discovery",
        )
        ledger_sync.sync_family("h5_vol_regime", "2026-07-02", fwd_db, ledger_db)
        con = sqlite3.connect(ledger_db)
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT run_id FROM current_run_per_lane "
            "WHERE family_id=? AND run_kind='discovery'", ("h5_vol_regime",)
        ).fetchone()
        con.close()
        assert row["run_id"] == pinned

    def test_discovery_metrics_are_not_overwritten_by_forward_metrics(
            self, fwd_db, ledger_db):
        pinned = record.append_run(
            ledger_db, family_id="h5_vol_regime",
            metrics={"sharpe": 0.91, "n_trades": 29},
            provenance="offline-corpus", run_kind="discovery",
        )
        ledger_sync.sync_family("h5_vol_regime", "2026-07-02", fwd_db, ledger_db)
        run = record.get_run(ledger_db, pinned)
        assert run["sharpe"] == pytest.approx(0.91)
        assert run["n_trades"] == 29


# ---------------------------------------------------------------------------
# 7. Idempotency
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_second_sync_same_as_of_is_a_noop(self, fwd_db, ledger_db):
        o1, id1 = ledger_sync.sync_family("h5_vol_regime", "2026-07-02", fwd_db, ledger_db)
        o2, id2 = ledger_sync.sync_family("h5_vol_regime", "2026-07-02", fwd_db, ledger_db)
        assert o1 == "ok" and o2 == "skipped" and id1 == id2
        con = sqlite3.connect(ledger_db)
        n = con.execute(
            "SELECT COUNT(*) FROM runs WHERE provenance='live-forward'").fetchone()[0]
        con.close()
        assert n == 1

    def test_new_as_of_appends_a_new_run(self, fwd_db, ledger_db):
        ledger_sync.sync_family("h5_vol_regime", "2026-06-26", fwd_db, ledger_db)
        ledger_sync.sync_family("h5_vol_regime", "2026-07-02", fwd_db, ledger_db)
        con = sqlite3.connect(ledger_db)
        n = con.execute(
            "SELECT COUNT(*) FROM runs WHERE provenance='live-forward'").fetchone()[0]
        con.close()
        assert n == 2

    def test_existing_offline_runs_are_untouched(self, fwd_db, ledger_db):
        record.append_run(
            ledger_db, family_id="h5_vol_regime",
            metrics={"sharpe": 0.91, "n_trades": 29},
            provenance="offline-corpus", run_kind="discovery",
        )
        con = sqlite3.connect(ledger_db)
        before = con.execute(
            "SELECT run_id, sharpe, n_trades FROM runs "
            "WHERE provenance='offline-corpus' ORDER BY run_id").fetchall()
        con.close()
        ledger_sync.sync_family("h5_vol_regime", "2026-07-02", fwd_db, ledger_db)
        con = sqlite3.connect(ledger_db)
        after = con.execute(
            "SELECT run_id, sharpe, n_trades FROM runs "
            "WHERE provenance='offline-corpus' ORDER BY run_id").fetchall()
        con.close()
        assert before == after


# ---------------------------------------------------------------------------
# 8. Schema policy -- assert, never repair
# ---------------------------------------------------------------------------

class TestSchemaAssertNeverRepair:
    def test_missing_ledger_raises_and_creates_nothing(self, fwd_db, tmp_path):
        absent = tmp_path / "no_such_ledger.db"
        with pytest.raises(ledger_sync.LedgerSchemaError):
            ledger_sync.sync_family("h5_vol_regime", "2026-07-02", fwd_db, absent)
        assert not absent.exists(), "ledger_sync must not create the ledger"

    def test_missing_run_kind_column_raises_without_altering(self, fwd_db, tmp_path):
        """A pre-PR-5 ledger must fail loudly, not be silently migrated."""
        p = tmp_path / "legacy_ledger.db"
        con = sqlite3.connect(p)
        con.execute(
            "CREATE TABLE runs (run_id TEXT PRIMARY KEY, family_id TEXT, "
            "run_ts TEXT, provenance TEXT, window_start TEXT, window_end TEXT, "
            "sharpe REAL, n_trades INTEGER, dsr_prob REAL, bh_p REAL)")
        con.commit()
        con.close()
        with pytest.raises(ledger_sync.LedgerSchemaError) as exc:
            ledger_sync.sync_family("h5_vol_regime", "2026-07-02", fwd_db, p)
        assert "run_kind" in str(exc.value)
        con = sqlite3.connect(p)
        cols = {r[1] for r in con.execute("PRAGMA table_info(runs)")}
        con.close()
        assert "run_kind" not in cols, "schema must be left exactly as found"

    def test_module_never_calls_init_db_or_issues_ddl(self):
        """Structural, not behavioural: the repair path must not exist at all."""
        src = pathlib.Path(ledger_sync.__file__).read_text(encoding="utf-8")
        body = src.split('"""', 2)[-1]          # strip the module docstring
        for banned in ("init_db(", "CREATE TABLE", "ALTER TABLE", "executescript"):
            assert banned not in body, f"{banned} must not appear in ledger_sync"

    def test_missing_forward_db_raises(self, ledger_db, tmp_path):
        with pytest.raises(ledger_sync.ForwardSchemaError):
            ledger_sync.sync_family(
                "h5_vol_regime", "2026-07-02", tmp_path / "nope.db", ledger_db)


# ---------------------------------------------------------------------------
# 9. Isolation -- a sync fault never propagates
# ---------------------------------------------------------------------------

class TestIsolation:
    def test_sync_all_isolates_per_family_failures(self, fwd_db, ledger_db, monkeypatch):
        real = ledger_sync.sync_family

        def boom(family_id, *a, **k):
            if family_id == "boom_family":
                raise RuntimeError("injected")
            return real(family_id, *a, **k)

        monkeypatch.setattr(ledger_sync, "sync_family", boom)
        out = ledger_sync.sync_all(
            ["boom_family", "h5_vol_regime"], "2026-07-02", fwd_db, ledger_db)
        assert out["boom_family"] == "error"
        assert out["h5_vol_regime"] == "ok"

    def test_forward_db_is_opened_read_only(self, fwd_db, ledger_db):
        conn = ledger_sync._connect_ro(fwd_db)
        try:
            with pytest.raises(sqlite3.OperationalError):
                conn.execute("INSERT INTO run_log (signal_id, run_date, fn_hash, status) "
                             "VALUES ('x','2026-01-01','h','ok')")
        finally:
            conn.close()

    def test_run_daily_exit_code_is_unaffected_by_sync_failure(self, monkeypatch):
        """Fault injection at the run_daily boundary: a sync that raises must
        leave the collection outcome untouched."""
        from forward_evidence import run_daily

        class Args:
            dry_run = False
            no_ledger_sync = False
            db_path = pathlib.Path("unused.db")

        def boom(*a, **k):
            raise RuntimeError("injected ledger failure")

        monkeypatch.setattr(run_daily.ledger_sync, "sync_all", boom)
        # _run_ledger_sync must swallow it and return None, never raise.
        assert run_daily._run_ledger_sync(Args(), "2026-08-29", {"h5_vol_regime": "ok"}) is None

    def test_run_daily_skips_sync_on_dry_run(self, monkeypatch):
        from forward_evidence import run_daily
        called = []
        monkeypatch.setattr(run_daily.ledger_sync, "sync_all",
                            lambda *a, **k: called.append(1) or {})

        class Args:
            dry_run = True
            no_ledger_sync = False
            db_path = pathlib.Path("unused.db")

        run_daily._run_ledger_sync(Args(), "2026-08-29", {})
        assert called == [], "dry-run must never write to the ledger"

    def test_run_daily_kill_switch_skips_sync(self, monkeypatch):
        from forward_evidence import run_daily
        called = []
        monkeypatch.setattr(run_daily.ledger_sync, "sync_all",
                            lambda *a, **k: called.append(1) or {})

        class Args:
            dry_run = False
            no_ledger_sync = True
            db_path = pathlib.Path("unused.db")

        run_daily._run_ledger_sync(Args(), "2026-08-29", {})
        assert called == []

    def test_sync_is_placed_after_the_exit_code_is_determined(self):
        """Structural: the sync call must come after both early-return guards,
        so a hard collection failure never triggers an unattended ledger write."""
        import inspect
        from forward_evidence import run_daily
        src = inspect.getsource(run_daily.main)
        assert "_run_ledger_sync" in src
        assert src.index("if failed:") < src.index("_run_ledger_sync(")
        assert src.index("collected nothing") < src.index("_run_ledger_sync(")
