"""
FB-10b-U -- the forward DSR/BH producer and the BH-adjusted-q statistic.

Covers the frozen contract's mechanics (population, window, construction,
dispersion), the OD-2 battery-level refusal, OD-4's adjusted q-value, OD-5's
engine-scoped placement, and the static safety properties that keep the
in-sample machinery out of the forward gate.
"""
from __future__ import annotations

import ast
import hashlib
import math
import random
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_EVLIB = ROOT / "research" / "evidence_library"
_EVID = ROOT / "research" / "evidence"
_FWD = ROOT / "research" / "forward_evidence"
for _p in (_EVLIB, _EVID, _FWD, ROOT / "research"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import governance as G  # noqa: E402
import forward_dsr_bh as P  # noqa: E402


# ---------------------------------------------------------------------------
# 1. governance.bh_adjusted_p
# ---------------------------------------------------------------------------

class TestBhAdjustedP:
    def test_matches_the_frozen_rejection_rule(self):
        """adj_p(i) <= q  <=>  bh_fdr(...)['reject'], for random batteries.
        This is what makes the new function a refinement of bh_fdr(), not a
        reinterpretation of it."""
        rng = random.Random(20260901)
        for _ in range(300):
            m = rng.randint(1, 12)
            pvals = {f"f{i}": rng.random() for i in range(m)}
            q = rng.choice([0.01, 0.05, 0.10, 0.25, 0.5, 0.9])
            adj = G.bh_adjusted_p(pvals)
            ranked = G.bh_fdr(pvals, q=q)
            for name in pvals:
                assert (adj[name] <= q) == ranked[name]["reject"], (pvals, q, name)

    def test_monotone_non_decreasing_in_rank(self):
        rng = random.Random(7)
        for _ in range(200):
            pvals = {f"f{i}": rng.random() for i in range(rng.randint(2, 15))}
            adj = G.bh_adjusted_p(pvals)
            ordered = [adj[n] for n, _ in sorted(pvals.items(), key=lambda kv: kv[1])]
            assert all(a <= b + 1e-12 for a, b in zip(ordered, ordered[1:]))

    def test_always_within_unit_interval(self):
        rng = random.Random(11)
        for _ in range(200):
            pvals = {f"f{i}": rng.random() for i in range(rng.randint(1, 10))}
            assert all(0.0 <= v <= 1.0 for v in G.bh_adjusted_p(pvals).values())

    def test_ties_get_equal_adjusted_values(self):
        adj = G.bh_adjusted_p({"a": 0.04, "b": 0.04, "c": 0.9})
        assert adj["a"] == adj["b"]

    def test_m_equals_one(self):
        assert G.bh_adjusted_p({"a": 0.03}) == {"a": 0.03}
        assert G.bh_adjusted_p({"a": 1.0}) == {"a": 1.0}

    def test_boundary_p_values(self):
        adj = G.bh_adjusted_p({"a": 0.0, "b": 1.0})
        assert adj["a"] == 0.0 and adj["b"] == 1.0

    def test_p_of_one_never_exceeds_one(self):
        adj = G.bh_adjusted_p({f"f{i}": 1.0 for i in range(6)})
        assert set(adj.values()) == {1.0}

    def test_empty_input(self):
        assert G.bh_adjusted_p({}) == {}

    def test_worked_example(self):
        # m=3, p = 0.01, 0.04, 0.30
        #   i=3: min(1, 3/3*0.30) = 0.30
        #   i=2: min(0.30, 3/2*0.04) = 0.06
        #   i=1: min(0.06, 3/1*0.01) = 0.03
        adj = G.bh_adjusted_p({"a": 0.01, "b": 0.04, "c": 0.30})
        assert adj["a"] == pytest.approx(0.03)
        assert adj["b"] == pytest.approx(0.06)
        assert adj["c"] == pytest.approx(0.30)


class TestBhFdrPreserved:
    def test_bh_fdr_is_byte_identical_to_committed_head(self):
        import subprocess
        head = subprocess.run(
            ["git", "show", "HEAD:research/evidence/governance.py"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=30).stdout

        def fn(src, name):
            tree = ast.parse(src)
            for n in tree.body:
                if isinstance(n, ast.FunctionDef) and n.name == name:
                    return "\n".join(src.split("\n")[n.lineno - 1:n.end_lineno])
            return None

        current = (_EVID / "governance.py").read_text(encoding="utf-8")
        for name in ("bh_fdr", "psr", "deflated_sr", "_sr_stats"):
            assert fn(head, name) == fn(current, name), f"{name} changed"

    def test_only_one_statistical_authority(self):
        """bh_adjusted_p lives in governance.py, not in the producer."""
        src = (_EVLIB / "forward_dsr_bh.py").read_text(encoding="utf-8")
        assert "def bh_adjusted_p" not in src
        assert "def deflated_sr" not in src and "def psr" not in src


# ---------------------------------------------------------------------------
# 2. Population (FDB-1)
# ---------------------------------------------------------------------------

class TestPopulation:
    def test_exactly_34_unique_members(self):
        specs = P.library_specs()
        names = [s.name for s in specs]
        assert len(specs) == 34 == P.N_TRIALS
        assert len(set(names)) == 34

    def test_matches_the_evidence_library_exactly(self):
        import json
        import re
        raw = re.sub(r":\s*NaN\b", ": null",
                     (ROOT / "research" / "reports" / "evidence_library" /
                      "evidence_library.json").read_text(encoding="utf-8"))
        assert {s.name for s in P.library_specs()} == set(json.loads(raw))

    def test_dvol_family_is_excluded(self):
        # via the D-3 boundary: a bare `import signals` here would resolve
        # through sys.modules["engine"] and inherit the very collision the
        # boundary exists to avoid.
        S = P._load_evidence_module("signals")
        dvol = {s.name for s in S.REGISTRY["dvol"]}
        assert dvol and not (dvol & {s.name for s in P.library_specs()})

    def test_population_hash_is_order_sensitive_and_stable(self):
        names = [s.name for s in P.library_specs()]
        assert P.population_hash(names) == P.population_hash(list(names))
        assert P.population_hash(names) != P.population_hash(list(reversed(names)))
        assert len(P.population_hash(names)) == 64


# ---------------------------------------------------------------------------
# 3. Frozen window (FDB-2a)
# ---------------------------------------------------------------------------

class TestFrozenWindow:
    def test_train_slice_boundary_is_the_non_lockbox_window(self):
        """FDB-2a: 2021-01-01 .. 2025-05-31. 2026-05-31 is the LOCKBOX end and
        must never be the recomputation boundary."""
        assert P.TRAIN_START == "2021-01-01"
        assert P.TRAIN_END == "2025-05-31"

    def test_lockbox_start_matches_the_policy_file(self):
        import yaml
        from datetime import datetime, timezone
        pol = yaml.safe_load((_FWD / "run_date_policy.yaml").read_text(encoding="utf-8"))
        expected = int(datetime.fromisoformat(pol["lockbox"]["start"])
                       .replace(tzinfo=timezone.utc).timestamp() * 1000)
        assert P.LOCKBOX_START_MS == expected

    def test_slice_predicate_excludes_the_lockbox(self):
        import pandas as pd
        from datetime import datetime, timezone
        inside = int(datetime(2025, 5, 31, tzinfo=timezone.utc).timestamp() * 1000)
        boundary = int(datetime(2025, 6, 1, tzinfo=timezone.utc).timestamp() * 1000)
        after = int(datetime(2026, 5, 31, tzinfo=timezone.utc).timestamp() * 1000)
        df = pd.DataFrame({"day_ts": [inside, boundary, after]})
        kept = df[df["day_ts"] < P.LOCKBOX_START_MS]
        assert list(kept["day_ts"]) == [inside]

    def test_constants_are_frozen(self):
        assert P.HOLD_DAYS == 7
        assert P.COST_BPS == 15
        assert P.N_TRIALS == 34
        assert P.Q == 0.10
        assert P.MIN_N_COMPUTABLE == 5


# ---------------------------------------------------------------------------
# 4. Trade construction (OD-1)
# ---------------------------------------------------------------------------

def _days(start: str, n: int) -> list[str]:
    from datetime import date, timedelta
    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


class TestTradeConstruction:
    def test_single_long_trade_exits_after_seven_calendar_days(self):
        days = _days("2026-01-01", 20)
        closes = [100.0 + i for i in range(20)]
        pos = [0.0] * 20
        pos[0] = 1.0
        trades = P.build_trades(days, closes, pos)
        assert len(trades) == 1
        t = trades[0]
        assert t.entry_date == "2026-01-01" and t.exit_date == "2026-01-08"
        assert t.direction == 1
        assert t.net_return == pytest.approx((107.0 / 100.0 - 1.0) - 0.0015)

    def test_short_trade_is_signed(self):
        days = _days("2026-01-01", 20)
        closes = [100.0] * 20
        closes[7] = 90.0
        pos = [0.0] * 20
        pos[0] = -1.0
        t = P.build_trades(days, closes, pos)[0]
        assert t.direction == -1
        # price fell 10%; a short earns +10% gross, minus 15bps
        assert t.net_return == pytest.approx(0.10 - 0.0015)

    def test_trades_never_overlap(self):
        days = _days("2026-01-01", 60)
        closes = [100.0] * 60
        pos = [1.0] * 60  # signal always on
        trades = P.build_trades(days, closes, pos)
        assert len(trades) >= 2
        for a, b in zip(trades, trades[1:]):
            assert a.exit_date <= b.entry_date

    def test_exit_and_reentry_on_the_same_day(self):
        """5a runs before 5b, so a continuously-on signal re-enters on the very
        day it exits -- back to back, no gap."""
        days = _days("2026-01-01", 30)
        closes = [100.0] * 30
        pos = [1.0] * 30
        trades = P.build_trades(days, closes, pos)
        assert trades[0].exit_date == trades[1].entry_date == "2026-01-08"

    def test_exit_is_unconditional_not_on_signal_reversal(self):
        days = _days("2026-01-01", 20)
        closes = [100.0] * 20
        pos = [0.0] * 20
        pos[0] = 1.0
        pos[2] = -1.0          # reversal mid-hold must NOT close the trade early
        trades = P.build_trades(days, closes, pos)
        assert trades[0].exit_date == "2026-01-08"
        assert trades[0].direction == 1

    def test_flat_signal_produces_no_trades(self):
        days = _days("2026-01-01", 30)
        assert P.build_trades(days, [100.0] * 30, [0.0] * 30) == []

    def test_trade_without_an_observable_exit_is_absent(self):
        """The series ends before the planned exit: production leaves the trade
        open, and an open trade has no row in trade_returns."""
        days = _days("2026-01-01", 4)
        pos = [1.0, 0.0, 0.0, 0.0]
        assert P.build_trades(days, [100.0] * 4, pos) == []

    def test_nan_position_is_not_an_entry(self):
        days = _days("2026-01-01", 20)
        pos = [float("nan")] * 20
        assert P.build_trades(days, [100.0] * 20, pos) == []

    def test_deterministic(self):
        days = _days("2026-01-01", 40)
        closes = [100.0 + (i % 5) for i in range(40)]
        pos = [1.0 if i % 3 else -1.0 for i in range(40)]
        a = P.build_trades(days, closes, pos)
        b = P.build_trades(days, closes, pos)
        assert a == b

    def test_misaligned_inputs_raise(self):
        with pytest.raises(ValueError):
            P.build_trades(["2026-01-01"], [1.0, 2.0], [0.0])


# ---------------------------------------------------------------------------
# 5. Member Sharpe + dispersion (FDB-1, OD-2)
# ---------------------------------------------------------------------------

class TestMemberSharpe:
    def test_direct_mean_over_sample_stdev(self):
        rets = [0.01, -0.02, 0.03, 0.00, 0.05]
        mean = sum(rets) / 5
        sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / 4)
        assert P.direct_sharpe(rets) == pytest.approx(mean / sd)

    def test_n_below_two_is_none_never_zero(self):
        assert P.direct_sharpe([]) is None
        assert P.direct_sharpe([0.01]) is None

    def test_zero_variance_is_none_never_zero(self):
        assert P.direct_sharpe([0.02] * 9) is None

    def test_does_not_route_through_sr_stats(self):
        """_sr_stats returns sr=0.0 for n<5 -- silently, no NaN, no raise. That
        is the trap OD-2 names; direct_sharpe must not fall into it."""
        import numpy as np
        assert G._sr_stats(np.array([0.01, 0.02, 0.03]))[0] == 0.0     # the trap
        assert P.direct_sharpe([0.01, 0.02, 0.03]) not in (None, 0.0)  # not the trap

    def test_no_annualisation(self):
        rets = [0.01, 0.02, 0.03, 0.04]
        mean = sum(rets) / 4
        sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / 3)
        assert P.direct_sharpe(rets) == pytest.approx(mean / sd)  # no sqrt(365) etc.


class TestDispersion:
    def test_requires_all_34(self):
        with pytest.raises(P.BatteryRefused) as exc:
            P.dispersion({f"s{i}": 0.1 * i for i in range(33)})
        assert exc.value.reason == "dispersion_population"

    def test_degenerate_dispersion_refuses(self):
        with pytest.raises(P.BatteryRefused) as exc:
            P.dispersion({f"s{i}": 0.5 for i in range(34)})
        assert exc.value.reason == "dispersion_degenerate"

    def test_sample_stdev_over_all_members(self):
        vals = {f"s{i}": float(i) for i in range(34)}
        mean = sum(vals.values()) / 34
        expected = math.sqrt(sum((v - mean) ** 2 for v in vals.values()) / 33)
        assert P.dispersion(vals) == pytest.approx(expected)


class TestBatteryRefusalOnUncomputableMember:
    def test_one_bad_member_refuses_the_whole_battery(self, monkeypatch):
        class Spec:
            def __init__(self, name, flat):
                self.name, self.params = name, {}
                self.fn = (lambda m, **k: [0.0] * len(m)) if flat else \
                          (lambda m, **k: [1.0] * len(m))

        import pandas as pd
        from datetime import date, timedelta
        n = 200
        d0 = date(2021, 1, 1)
        master = pd.DataFrame({
            "dt": [d0 + timedelta(days=i) for i in range(n)],
            "close": [100.0 + (i % 7) for i in range(n)],
        })
        specs = [Spec(f"s{i}", flat=(i == 17)) for i in range(34)]
        with pytest.raises(P.BatteryRefused) as exc:
            P.member_sharpes(master, specs)
        assert exc.value.reason == "member_sharpe_uncomputable"
        assert exc.value.context["signal"] == "s17"

    def test_refusal_names_the_member_and_never_drops_it(self, monkeypatch):
        src = (_EVLIB / "forward_dsr_bh.py").read_text(encoding="utf-8")
        # there is no code path that continues past an uncomputable member
        assert "continue" not in src.split("def member_sharpes")[1].split("def ")[0]


# ---------------------------------------------------------------------------
# 6. Membership + the production refusal path (FDB-5, FDB-7, FDB-8)
# ---------------------------------------------------------------------------

def _forward_db(tmp_path, counts: dict[str, int]) -> Path:
    db = tmp_path / "evidence_forward.db"
    con = sqlite3.connect(db)
    con.executescript((_FWD / "schema_forward.sql").read_text(encoding="utf-8"))
    from datetime import date, timedelta
    for fam, n in counts.items():
        for i in range(n):
            entry = (date(2026, 1, 1) + timedelta(days=8 * i)).isoformat()
            exit_ = (date(2026, 1, 1) + timedelta(days=8 * i + 7)).isoformat()
            con.execute(
                "INSERT INTO trade_entries (signal_id, entry_date, direction, "
                "entry_close_price, planned_exit_date, open) VALUES (?,?,?,?,?,0)",
                (fam, entry, 1 if i % 2 else -1, 100.0, exit_))
            eid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
            con.execute(
                "INSERT INTO trade_exits (entry_id, exit_date, exit_close_price, cost_bps) "
                "VALUES (?,?,?,?)", (eid, exit_, 100.0 + (i % 5) - 2, 15.0))
    con.commit()
    con.close()
    return db


FROZEN_MINIMA = {
    "h5_vol_regime": 15,
    "g1_market_regime_sma200": 10,
    "g3_momentum_tsmom": 15,
    "g1_trend_regime_ma_cross": 30,
    "ivrv_vrp_spread": 30,
    "g2_funding_level": 30,
    "g3_atr_expansion": 26,
}

#: The production counts observed on 2026-09-01.
PRODUCTION_COUNTS = {
    "h5_vol_regime": 7,
    "g1_market_regime_sma200": 7,
    "g3_momentum_tsmom": 8,
    "ivrv_vrp_spread": 6,
    "g3_atr_expansion": 3,
    "g1_trend_regime_ma_cross": 0,
    "g2_funding_level": 0,
}


class TestMembership:
    def test_todays_production_battery_is_refused(self, tmp_path):
        """The primary production safety test: every family is below its frozen
        minimum, so nothing is computed and nothing is written."""
        db = _forward_db(tmp_path, PRODUCTION_COUNTS)
        res = P.compute_battery(db, "2026-09-01", FROZEN_MINIMA)
        assert res.status == "refused"
        assert res.reason == "empty_battery"
        assert res.members == []
        assert res.payloads == {}
        assert len(res.excluded) == 7
        for row in res.excluded:
            assert "below_minimum" in row["reason"] or \
                   "below_computability_floor" in row["reason"]

    def test_refusal_never_loads_the_corpus(self, tmp_path, monkeypatch):
        """An empty battery is found before any research data is touched."""
        called = []
        monkeypatch.setattr(P, "_train_slice_master",
                            lambda: called.append(1) or (_ for _ in ()).throw(AssertionError))
        db = _forward_db(tmp_path, PRODUCTION_COUNTS)
        assert P.compute_battery(db, "2026-09-01", FROZEN_MINIMA).status == "refused"
        assert called == []

    def test_missing_minimum_refuses_the_run(self, tmp_path):
        db = _forward_db(tmp_path, PRODUCTION_COUNTS)
        partial = dict(FROZEN_MINIMA)
        del partial["g2_funding_level"]
        res = P.compute_battery(db, "2026-09-01", partial)
        assert res.status == "refused" and res.reason == "minimum_not_supplied"

    def test_minima_have_no_defaults_anywhere(self):
        src = (_EVLIB / "forward_dsr_bh.py").read_text(encoding="utf-8")
        for fam, value in FROZEN_MINIMA.items():
            assert f'"{fam}": {value}' not in src, "a minimum must never be hardcoded"

    def test_exactly_at_the_minimum_is_a_member(self, tmp_path):
        counts = {f: 0 for f in FROZEN_MINIMA}
        counts["g1_market_regime_sma200"] = 10   # exactly its minimum
        db = _forward_db(tmp_path, counts)
        mem = P.resolve_membership(db, "2027-01-01", FROZEN_MINIMA)
        assert [m.family_id for m in mem.members] == ["g1_market_regime_sma200"]
        assert mem.members[0].n == 10 and mem.members[0].minimum == 10

    def test_one_below_the_minimum_is_excluded_with_a_reason(self, tmp_path):
        counts = {f: 0 for f in FROZEN_MINIMA}
        counts["g1_market_regime_sma200"] = 9
        db = _forward_db(tmp_path, counts)
        mem = P.resolve_membership(db, "2027-01-01", FROZEN_MINIMA)
        assert mem.members == []
        reason = [e for e in mem.excluded
                  if e["family_id"] == "g1_market_regime_sma200"][0]["reason"]
        assert reason == "below_minimum(n=9, min=10)"

    def test_observation_floors_are_not_substituted(self):
        """FDB-8: the DSR minimum and the observation floor stay separate. The
        producer reads neither pre-registration floors nor thresholds.yaml."""
        src = (_EVLIB / "forward_dsr_bh.py").read_text(encoding="utf-8")
        for banned in ("minimum_trades", "observation_floors", "promotion_criteria"):
            assert banned not in src, f"{banned} must not be read by the producer"


# ---------------------------------------------------------------------------
# 7. A fully computed battery (payload / FDB-9 completeness)
# ---------------------------------------------------------------------------

@pytest.fixture
def computed(tmp_path):
    """A battery with two members, using an injected master so no corpus is
    required and no lockbox row can exist."""
    import pandas as pd
    from datetime import date, timedelta

    counts = {f: 0 for f in FROZEN_MINIMA}
    counts["g1_market_regime_sma200"] = 12
    counts["h5_vol_regime"] = 16
    db = _forward_db(tmp_path, counts)

    # D-5: the frame must cover the frozen window exactly, or compute_battery
    # refuses as corpus_incomplete before any statistic is formed.
    days = P.expected_train_days()
    n = len(days)
    rng = random.Random(3)
    master = pd.DataFrame({
        "dt": [date.fromisoformat(d) for d in days],
        "close": [100.0 * (1.0 + 0.002 * rng.gauss(0, 1)) ** i for i in range(n)],
    })

    class Spec:
        def __init__(self, name, seed):
            self.name, self.params = name, {}
            r = random.Random(seed)
            pattern = [r.choice([-1.0, 0.0, 1.0]) for _ in range(n)]
            self.fn = lambda m, _p=pattern, **k: _p[:len(m)]

    specs = [Spec(f"sig{i}", i) for i in range(34)]
    import forward_dsr_bh as _P
    orig = _P.library_specs
    _P.library_specs = lambda: specs
    try:
        res = _P.compute_battery(db, "2027-01-01", FROZEN_MINIMA, master=master)
    finally:
        _P.library_specs = orig
    return res


class TestComputedPayload:
    def test_status_and_members(self, computed):
        assert computed.status == "computed"
        assert set(computed.members) == {"g1_market_regime_sma200", "h5_vol_regime"}

    def test_fdb9_required_fields_are_all_present(self, computed):
        import bh_convention
        for fam, payload in computed.payloads.items():
            block = payload["block"]
            missing = [f for f in bh_convention.FDB9_REQUIRED_FIELDS if f not in block]
            assert missing == [], f"{fam} missing {missing}"

    def test_classifier_sees_a_forward_q_not_a_legacy_raw_p(self, computed):
        """FDB-13/F5 end-to-end: a row carrying this payload must classify as
        the forward convention."""
        import bh_convention
        import forward_snapshot as FS
        for fam, payload in computed.payloads.items():
            run = {"bh_p": payload["governance"]["bh_p"],
                   "metrics": {FS.DSR_BH_KEY: payload["block"]}}
            assert bh_convention.classify(run) == bh_convention.FORWARD_ADJUSTED_Q

    def test_n_trials_is_34_and_q_is_frozen(self, computed):
        for payload in computed.payloads.values():
            assert payload["block"]["n_trials"] == 34
            assert payload["block"]["q"] == 0.10

    def test_dispersion_basis_declares_no_conversion(self, computed):
        for payload in computed.payloads.values():
            basis = payload["block"]["sr_trials_std_basis"]
            assert basis["conversion_applied"] == "none"
            assert basis["units"] == "per-trade"
            assert basis["hold_days"] == 7 and basis["cost_bps"] == 15
            assert basis["window"] == ["2021-01-01", "2025-05-31"]
            assert basis["population_size"] == 34
            assert len(basis["population_members"]) == 34
            assert len(basis["population_hash"]) == 64

    def test_battery_membership_is_frozen_with_order_and_reasons(self, computed):
        for payload in computed.payloads.values():
            members = payload["block"]["battery_members"]
            assert [m["family_id"] for m in members] == computed.members
            for m in members:
                assert {"family_id", "n", "minimum", "p", "rank",
                        "bh_threshold", "reject"} <= set(m)
            for e in payload["block"]["battery_excluded"]:
                assert e["reason"]

    def test_bh_p_is_the_adjusted_q_not_the_raw_p(self, computed):
        raw = {m["family_id"]: m["p"]
               for m in next(iter(computed.payloads.values()))["block"]["battery_members"]}
        expected = G.bh_adjusted_p(raw)
        for fam, payload in computed.payloads.items():
            assert payload["governance"]["bh_p"] == pytest.approx(expected[fam])

    def test_dsr_comes_from_the_authoritative_machinery(self, computed):
        import numpy as np
        block = next(iter(computed.payloads.values()))["block"]
        sr_std = block["sr_trials_std"]
        for fam, payload in computed.payloads.items():
            b = payload["block"]
            # sr_star is deflated_sr's, reproducible from the recorded inputs
            _, sr_star, _ = G.deflated_sr(np.array([0.0] * 10), sr_std, 34)
            assert b["sr_star"] == pytest.approx(sr_star)

    def test_no_ledger_write_path_exists_in_the_producer(self):
        src = (_EVLIB / "forward_dsr_bh.py").read_text(encoding="utf-8")
        for banned in ("append_run", "append_transition", "append_gate_result",
                       "append_receipt", "INSERT", "UPDATE", "DELETE", "init_db"):
            assert banned not in src, f"{banned} must not appear in the producer"


# ---------------------------------------------------------------------------
# 8. Static safety (item 8 of the gate)
# ---------------------------------------------------------------------------

class TestStaticSafety:
    SRC = None

    def setup_method(self):
        self.SRC = (_EVLIB / "forward_dsr_bh.py").read_text(encoding="utf-8")

    def test_does_not_import_the_in_sample_machinery(self):
        tree = ast.parse(self.SRC)
        names = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                names |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module:
                names.add(n.module.split(".")[0])
        for banned in ("compute_dsr_for_registration", "run_harmonization",
                       "run_dvol_family", "report"):
            assert banned not in names, f"must not import {banned}"

    def test_no_rejected_conversions_appear(self):
        """Docstrings are stripped first: this module DOCUMENTS that sqrt(7) is
        rejected, and naming a rejected method in prose is the opposite of using
        it. The repo uses the same strip-then-scan convention elsewhere."""
        tree = ast.parse(self.SRC)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)) and ast.get_docstring(node):
                node.body = node.body[1:] or [ast.Pass()]
        code = ast.unparse(tree)
        for banned in ("sqrt(7", "_dsr_proxy", "psr_approx", "n_trials=38",
                       "N_TRIALS = 38", "* np.sqrt(7", "0.5)"):
            assert banned not in code, f"{banned} must not appear in executable code"
        assert "N_TRIALS = 34" in self.SRC

    def test_member_sharpe_never_routes_through_psr_or_sr_stats(self):
        body = self.SRC.split("def member_sharpes")[1].split("\ndef ")[0]
        body += self.SRC.split("def direct_sharpe")[1].split("\ndef ")[0]
        for banned in ("psr(", "_sr_stats", "deflated_sr"):
            assert banned not in body, f"{banned} must not build a member Sharpe"

    def test_deflated_sr_is_called_with_exactly_34(self):
        assert "G.deflated_sr(arr, sr_trials_std, N_TRIALS)" in self.SRC

    def test_protected_artifacts_unchanged(self):
        import subprocess
        out = subprocess.run(["git", "diff", "--name-only", "HEAD"],
                             cwd=str(ROOT), capture_output=True, text=True, timeout=30).stdout
        changed = set(out.split())
        forbidden = {
            "research/forward_evidence/ledger_sync.py",
            "research/evidence_library/thresholds.yaml",
            "research/evidence_library/registry.yaml",
            "research/evidence_library/record.py",
            "research/forward_evidence/run_date_policy.yaml",
        }
        assert not (changed & forbidden), f"protected artifact changed: {changed & forbidden}"
        assert not any(c.startswith("research/forward_evidence/pre_registration")
                       for c in changed)


# ---------------------------------------------------------------------------
# 9. Engine-scoped placement (OD-5 / C2)
# ---------------------------------------------------------------------------

class TestEngineScoping:
    def test_probe_matches_engine_version(self):
        import forward_snapshot as FS
        src = Path(FS.__file__).read_text(encoding="utf-8")
        assert "AND engine_version = ?" in src
        body = src.split("def _existing_snapshot_run")[1].split("\ndef ")[0]
        assert "ENGINE_VERSION" in body
        assert "ls.PROVENANCE" in body

    def test_ledger_sync_is_unchanged(self):
        import subprocess
        out = subprocess.run(
            ["git", "diff", "--", "research/forward_evidence/ledger_sync.py"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=30).stdout
        assert out.strip() == "", "ledger_sync.py must not change"

    def test_collector_row_does_not_suppress_a_snapshot(self, tmp_path):
        import record
        import forward_snapshot as FS
        db = tmp_path / "ledger.db"
        record.init_db(db)
        record.append_run(db, family_id="fam_a", metrics={"n_trades": 7},
                          provenance="live-forward", run_kind="forward",
                          window_start="2026-01-01", window_end="2026-02-10",
                          engine_version="forward-collect-v1")
        assert FS._existing_snapshot_run(db, "fam_a", "2026-02-10") is None

    def test_own_row_does_suppress_a_snapshot(self, tmp_path):
        import record
        import forward_snapshot as FS
        db = tmp_path / "ledger.db"
        record.init_db(db)
        rid = record.append_run(db, family_id="fam_a", metrics={"n_trades": 7},
                                provenance="live-forward", run_kind="forward",
                                window_start="2026-01-01", window_end="2026-02-10",
                                engine_version=FS.ENGINE_VERSION)
        assert FS._existing_snapshot_run(db, "fam_a", "2026-02-10") == rid

    def test_engine_identity_is_explicit(self):
        import forward_snapshot as FS
        assert FS.ENGINE_VERSION == "forward-snapshot-v1"
        assert P.CALC_VERSION == "forward-dsr-bh-v1"
        assert FS.DSR_BH_KEY == "forward_dsr_bh"


# ---------------------------------------------------------------------------
# 10. Legacy safety
# ---------------------------------------------------------------------------

class TestLegacySafety:
    REAL_LEDGER = _EVLIB / "ledger.db"

    @pytest.mark.skipif(not (_EVLIB / "ledger.db").exists(), reason="no real ledger")
    def test_legacy_rows_still_classify_as_raw_p_and_are_untouched(self):
        import json
        import bh_convention
        before = hashlib.sha256(self.REAL_LEDGER.read_bytes()).hexdigest()
        con = sqlite3.connect(f"file:{self.REAL_LEDGER}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT * FROM runs WHERE bh_p IS NOT NULL").fetchall()
        con.close()
        assert len(rows) == 3
        for r in rows:
            run = dict(r)
            run["metrics"] = json.loads(run.pop("metrics_json"))
            assert bh_convention.classify(run) == bh_convention.LEGACY_RAW_P
        assert hashlib.sha256(self.REAL_LEDGER.read_bytes()).hexdigest() == before

    def test_producer_never_reads_bh_p_from_an_existing_row(self):
        src = (_EVLIB / "forward_dsr_bh.py").read_text(encoding="utf-8")
        assert 'get("bh_p")' not in src and "['bh_p']" not in src
