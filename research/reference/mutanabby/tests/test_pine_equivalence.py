"""
Gate M-4 equivalence battery: the Python port versus the FROZEN M-3 TradingView
fixture.

The reference is the fixture itself -- real TradingView output -- never a second
Python implementation of the same logic. A port checked against a re-derivation
of its own assumptions proves nothing.

Fixture: 5,813 BINANCE:BTCUSDT 4H bars, 2024-01-01T00:00Z .. 2026-08-26T16:00Z
         sha256 33825fbe7a10e5a84289fdd1da64c207d6d610c905d295dac7c2738d8cefe0cc

The fixture is opened read-only and never repaired, reordered or interpolated.
Tests skip (never silently pass) when it is absent, and hard-fail if its hash
does not match -- so a swapped fixture cannot masquerade as a passing run.

Run from research/:
    conda run -n btc_bot pytest reference/mutanabby/tests/test_pine_equivalence.py -v
"""
from __future__ import annotations

import hashlib
import os
import pathlib

import numpy as np
import pandas as pd
import pytest

from reference.mutanabby import pine_port as pp

FIXTURE_SHA256 = "33825fbe7a10e5a84289fdd1da64c207d6d610c905d295dac7c2738d8cefe0cc"
FIXTURE_PATH = pathlib.Path(
    os.environ.get(
        "MUTANABBY_M3_FIXTURE",
        r"D:\pine-logs-M3 REF Mutanabby_AI _ Ultimate Algo _ Remastered+.csv",
    )
)

COLS = [
    "timestamp_ms", "open", "high", "low", "close", "atr", "upperBasic",
    "lowerBasic", "upperBand", "lowerBand", "direction", "sma4", "sma5",
    "sma8", "sma9", "sma13", "sma21", "sma34", "bull", "bear", "strength",
    "tr", "supertrend",
]

#: Fixture values are printed to 10 decimals, so a parsed value can differ from
#: the true float by up to 5e-11. 1e-9 sits two orders above that noise floor
#: and still resolves any genuine formula divergence.
TOL = 1e-9

REAL_LADDER = ["tr", "atr", "upperBasic", "lowerBasic", "upperBand",
               "lowerBand", "supertrend", "sma4", "sma5", "sma8", "sma9",
               "sma13", "sma21", "sma34"]
EXACT_LADDER = ["direction", "bull", "bear", "strength"]


@pytest.fixture(scope="module")
def fixture() -> pd.DataFrame:
    if not FIXTURE_PATH.exists():
        pytest.skip(f"M-3 fixture not present at {FIXTURE_PATH}")
    raw = FIXTURE_PATH.read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    assert got == FIXTURE_SHA256, (
        f"fixture hash mismatch — refusing to validate against an unknown file.\n"
        f"  expected {FIXTURE_SHA256}\n  got      {got}"
    )
    df = pd.read_csv(FIXTURE_PATH)
    msg = df["Message"].astype(str)
    rows = msg[msg.str.startswith("ROW|")].str.split("|")
    out = pd.DataFrame(rows.tolist(), columns=["tag"] + COLS).drop(columns="tag")
    for c in COLS:
        out[c] = pd.to_numeric(out[c].replace("NaN", np.nan), errors="coerce")
    return out.reset_index(drop=True)


@pytest.fixture(scope="module")
def ported(fixture: pd.DataFrame) -> pd.DataFrame:
    return pp.compute(fixture[["open", "high", "low", "close"]]).reset_index(drop=True)


def _cmp_real(name: str, exp: np.ndarray, got: np.ndarray) -> tuple[bool, str]:
    """NaN pattern must match exactly; finite values within TOL."""
    e_nan, g_nan = np.isnan(exp), np.isnan(got)
    if not np.array_equal(e_nan, g_nan):
        bad = int(np.sum(e_nan != g_nan))
        first = int(np.argmax(e_nan != g_nan))
        return False, (f"{name}: NaN pattern differs on {bad} bars; first row {first} "
                       f"(pine_nan={bool(e_nan[first])} py_nan={bool(g_nan[first])})")
    m = ~e_nan
    if not m.any():
        return True, f"{name}: all-NaN on both sides"
    dev = np.abs(exp[m] - got[m])
    worst = int(np.argmax(dev))
    idx = int(np.arange(len(exp))[m][worst])
    ok = bool(dev.max() <= TOL)
    return ok, (f"{name}: max|dev|={dev.max():.3e} at row {idx} "
                f"(pine={exp[idx]!r} py={got[idx]!r}); {int((dev > TOL).sum())} bars > {TOL:g}")


# ===========================================================================
# Ladder — ordered so pytest -x stops at the first divergent stage
# ===========================================================================

def test_L00_bar_alignment(fixture, ported):
    assert len(fixture) == 5813, f"expected 5813 bars, got {len(fixture)}"
    assert len(ported) == len(fixture)
    ts = fixture["timestamp_ms"].astype("int64")
    assert ts.is_monotonic_increasing and not ts.duplicated().any()
    assert int(ts.iloc[0]) == 1704067200000
    assert int(ts.iloc[-1]) == 1787760000000
    assert set(np.diff(ts.to_numpy())) == {14_400_000}


@pytest.mark.parametrize("col", REAL_LADDER)
def test_L01_real_valued_ladder(fixture, ported, col):
    ok, detail = _cmp_real(col, fixture[col].to_numpy(float), ported[col].to_numpy(float))
    assert ok, detail


@pytest.mark.parametrize("col", EXACT_LADDER)
def test_L02_exact_integer_ladder(fixture, ported, col):
    exp = fixture[col].to_numpy(np.int64)
    got = ported[col].to_numpy(np.int64)
    diff = np.flatnonzero(exp != got)
    assert diff.size == 0, (
        f"{col}: {diff.size} mismatched bars; first row {int(diff[0])} "
        f"(pine={exp[diff[0]]} py={got[diff[0]]})"
    )


# ===========================================================================
# Warm-up (rows 0-33) asserted explicitly
# ===========================================================================

def test_L03_warmup_rows_0_to_33(fixture, ported):
    for col in REAL_LADDER + EXACT_LADDER:
        e = fixture[col].to_numpy(float)[:34]
        g = ported[col].to_numpy(float)[:34]
        assert np.array_equal(np.isnan(e), np.isnan(g)), f"{col}: warm-up NaN pattern differs"
        m = ~np.isnan(e)
        assert np.all(np.abs(e[m] - g[m]) <= TOL), f"{col}: warm-up values differ"


def test_L03b_warmup_structural_invariants(ported):
    """The nz()->0 latch and the forced initial direction, per M-2 and confirmed
    by the fixture. Pinned so a port that merely averages out to the same values
    cannot pass."""
    assert ported["upperBand"][0] == 0.0 and ported["lowerBand"][0] == 0.0
    assert np.all(np.isnan(ported["upperBand"].to_numpy(float)[1:10]))
    assert np.all(ported["lowerBand"].to_numpy(float)[0:10] == 0.0)
    assert np.all(ported["direction"].to_numpy()[0:11] == 1)
    assert np.isnan(ported["atr"][9]) and not np.isnan(ported["atr"][10])
    assert ported["bull"].to_numpy()[:34].sum() == 0
    assert ported["bear"].to_numpy()[:34].sum() == 0


def test_L03c_atr_is_wilder_not_rolling_mean(fixture, ported):
    """A rolling-mean ATR would pass casual inspection and fail here."""
    tr = fixture["tr"].to_numpy(float)
    assert abs(ported["atr"][10] - tr[:11].mean()) <= TOL, "ATR seed is not the SMA of the first 11 TR"
    recur = (ported["atr"][10] * (pp.ST_ATR_LEN - 1) + tr[11]) / pp.ST_ATR_LEN
    assert abs(ported["atr"][11] - recur) <= TOL, "ATR does not follow Wilder recursion"
    rolling = pd.Series(tr).rolling(pp.ST_ATR_LEN, min_periods=pp.ST_ATR_LEN).mean().to_numpy()
    late = ~np.isnan(rolling)
    assert np.nanmax(np.abs(rolling[late] - ported["atr"].to_numpy(float)[late])) > 1.0, (
        "Wilder RMA and a rolling mean are indistinguishable on this fixture — "
        "the discriminating power of this test is gone"
    )


# ===========================================================================
# Signals: index set and strength composition
# ===========================================================================

def test_L04_signal_bar_indices_identical(fixture, ported):
    for col in ("bull", "bear"):
        e = set(np.flatnonzero(fixture[col].to_numpy(np.int64) == 1).tolist())
        g = set(np.flatnonzero(ported[col].to_numpy(np.int64) == 1).tolist())
        assert e == g, (f"{col}: index sets differ — missing {sorted(e - g)[:10]}, "
                        f"extra {sorted(g - e)[:10]}")


def test_L05_signal_counts_and_strength_composition(fixture, ported):
    assert int(fixture["bull"].sum()) == 51 and int(fixture["bear"].sum()) == 50
    assert int(ported["bull"].sum()) == 51 and int(ported["bear"].sum()) == 50
    exp = fixture["strength"].value_counts().sort_index().to_dict()
    got = ported["strength"].value_counts().sort_index().to_dict()
    assert exp == got, f"strength histogram differs: pine={exp} py={got}"
    # all four labels must actually occur, or the test proves nothing about them
    assert {int(k) for k in got} == {-2, -1, 0, 1, 2}


def test_L06_strength_indices_per_class(fixture, ported):
    for code in (-2, -1, 1, 2):
        e = set(np.flatnonzero(fixture["strength"].to_numpy(np.int64) == code).tolist())
        g = set(np.flatnonzero(ported["strength"].to_numpy(np.int64) == code).tolist())
        assert e == g, f"strength=={code}: index sets differ"


def test_L07_strength_polarity_is_the_source_polarity(fixture, ported):
    """Guards the inverted rule: Strong fires when the FAST ma is BELOW the medium."""
    b = ported["bull"].to_numpy(np.int64) == 1
    strong = ported["strength"].to_numpy(np.int64)[b] == 2
    fast_below = ported["sma4"].to_numpy(float)[b] < ported["sma5"].to_numpy(float)[b]
    assert np.array_equal(strong, fast_below)


# ===========================================================================
# Leakage
# ===========================================================================

def test_L08_prefix_invariance(fixture):
    """compute(bars[:t]) must equal compute(bars)[:t] exactly, for every cut."""
    full = pp.compute(fixture[["open", "high", "low", "close"]]).reset_index(drop=True)
    for cut in (100, 517, 1234, 2500, 4001, 5000, 5812):
        pre = pp.compute(
            fixture[["open", "high", "low", "close"]].iloc[:cut]).reset_index(drop=True)
        for col in REAL_LADDER + EXACT_LADDER:
            e = full[col].to_numpy(float)[:cut]
            g = pre[col].to_numpy(float)
            assert np.array_equal(np.isnan(e), np.isnan(g)), f"cut {cut}: {col} NaN pattern"
            m = ~np.isnan(e)
            assert np.all(np.abs(e[m] - g[m]) <= TOL), f"cut {cut}: {col} values differ"


def test_L09_future_mutation_cannot_change_the_past(fixture):
    """Corrupting the tail must leave every earlier bar bit-identical."""
    src = fixture[["open", "high", "low", "close"]].copy()
    base = pp.compute(src).reset_index(drop=True)
    half = len(src) // 2
    mut = src.copy()
    for c in ("open", "high", "low", "close"):
        mut.iloc[half:, mut.columns.get_loc(c)] *= 3.0
    got = pp.compute(mut).reset_index(drop=True)
    for col in REAL_LADDER + EXACT_LADDER:
        e = base[col].to_numpy(float)[:half]
        g = got[col].to_numpy(float)[:half]
        assert np.array_equal(np.isnan(e), np.isnan(g)), f"{col}: future leaked (NaN)"
        m = ~np.isnan(e)
        assert np.all(np.abs(e[m] - g[m]) <= TOL), f"{col}: future leaked (values)"


# ===========================================================================
# Boundary semantics the fixture CANNOT exercise
#
# ta.crossover/crossunder are non-strict on the previous bar, and the sma13
# filters are inclusive. Real BTC floats never land on exact equality, so no
# amount of fixture data discriminates `<=` from `<` here -- adversarial
# mutation of those four operators went undetected by the ladder above.
# These tests close that gap directly against the Pine source semantics
# (M-2 §4 and source lines 213-214), not against the fixture.
# ===========================================================================

def test_L11_crossover_is_non_strict_on_the_previous_bar():
    """Pine: ta.crossover(a,b) == a > b and a[1] <= b[1]. Equality on the
    previous bar MUST still count as a cross."""
    a = np.array([1.0, 2.0])
    b = np.array([1.0, 1.0])          # a[0] == b[0] exactly
    assert pp.ta_crossover(a, b)[1], "crossover must fire when a[1] == b[1] (non-strict)"


def test_L12_crossunder_is_non_strict_on_the_previous_bar():
    """Pine: ta.crossunder(a,b) == a < b and a[1] >= b[1]."""
    a = np.array([1.0, 0.0])
    b = np.array([1.0, 1.0])          # a[0] == b[0] exactly
    assert pp.ta_crossunder(a, b)[1], "crossunder must fire when a[1] == b[1] (non-strict)"


def test_L13_crossover_still_requires_strict_current_bar():
    a = np.array([1.0, 1.0])
    b = np.array([1.0, 1.0])
    assert not pp.ta_crossover(a, b)[1], "equality on the CURRENT bar is not a cross"
    assert not pp.ta_crossunder(a, b)[1]


def test_L14_signal_filters_are_inclusive():
    """Source L213-214 use `close >= sma9` / `close <= sma9` (inclusive).

    Structural guard: the fixture cannot reach close == sma13 exactly, so this
    asserts the operator in the ported expression rather than its behaviour.
    Labelled as such deliberately -- it is weaker than a behavioural test and
    exists only because the data cannot supply the boundary case.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(pp.compute))
    ops = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) in ("bull", "bear"):
            cmps = [c for c in ast.walk(node.value) if isinstance(c, ast.Compare)]
            ops[node.targets[0].id] = [type(c.ops[0]).__name__ for c in cmps]
    assert "GtE" in ops.get("bull", []), f"bull filter must be >= (inclusive); got {ops.get('bull')}"
    assert "LtE" in ops.get("bear", []), f"bear filter must be <= (inclusive); got {ops.get('bear')}"


def test_L10_past_mutation_does_change_the_future(fixture):
    """Guards against an inert port that would pass L08/L09 trivially."""
    src = fixture[["open", "high", "low", "close"]].copy()
    base = pp.compute(src).reset_index(drop=True)
    half = len(src) // 2
    mut = src.copy()
    for c in ("open", "high", "low", "close"):
        mut.iloc[:half, mut.columns.get_loc(c)] *= 3.0
    got = pp.compute(mut).reset_index(drop=True)
    assert not np.array_equal(
        base["supertrend"].to_numpy(float)[half:],
        got["supertrend"].to_numpy(float)[half:],
    )
