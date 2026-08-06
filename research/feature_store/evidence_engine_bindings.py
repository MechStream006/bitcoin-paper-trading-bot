"""
Feature bindings wrapping research/evidence/engine.py's load_master()
(engineering roadmap Phase 2, WBS 2.4 -- the Feature Specification
migration milestone).

WRAP, DON'T REWRITE. engine.py is not modified by this milestone at all --
every function below is a THIN EXTRACTION over the existing, unmodified
load_master(): it calls load_master() (the real computation, verbatim) and
returns exactly one already-computed column as a plain, JSON-serialisable
list. The only new logic this module adds is "which column, and convert to
a list" -- no rolling window, no dtype coercion, no regime threshold is
reimplemented here.

Registered against research/feature_store/registry.py's binding mechanism
by research/feature_store/migrate_evidence_engine_features.py, under
engine_version="v1" and one binding_id per column (e.g.
"evidence_engine.rv_21d").

NOT WRAPPED, DELIBERATELY: load_master()'s `ret_fwd1` column. See
migrate_evidence_engine_features.py's module docstring for why -- it is a
label (depends on data after t), not a feature, per ADR-I011 §1.1's
Central Asymmetry, and registering it here would violate that constitutional
distinction.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

_RESEARCH_DIR = Path(__file__).resolve().parents[1]
_EVIDENCE_DIR = _RESEARCH_DIR / "evidence"
for _p in (_RESEARCH_DIR, _EVIDENCE_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# research/evidence/engine.py is loaded under a process-unique sys.modules key
# rather than the bare "engine" this repository's convention would normally
# use (import engine). sys.modules is process-global: research/dq/engine.py
# (PR-7's Data Completeness Framework) is a DIFFERENT module that ALSO
# registers itself under the bare name "engine" when something imports it
# first, and whichever one wins that race silently shadows the other for
# every subsequent bare "import engine" in the process -- observed as
# AttributeError: module 'engine' has no attribute 'load_master' when
# tests/test_dq_engine.py collects before this module under pytest's
# alphabetical ordering. Loading by explicit file path with a unique key
# sidesteps the collision entirely without changing research/evidence/
# engine.py or research/dq/engine.py at all.
_ENGINE_MODULE_KEY = "research.evidence.engine (feature_store binding)"


def _load_evidence_engine():
    existing = sys.modules.get(_ENGINE_MODULE_KEY)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(_ENGINE_MODULE_KEY, _EVIDENCE_DIR / "engine.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_ENGINE_MODULE_KEY] = module
    spec.loader.exec_module(module)
    return module


engine = _load_evidence_engine()


def _column(name: str) -> list[Any]:
    """The one piece of new logic in this module: extract a single already-
    computed column from load_master()'s existing output as a plain list."""
    df = engine.load_master()
    return df[name].tolist()


def basis_bps() -> list[Any]:
    return _column("basis_bps")


def trend_1h() -> list[Any]:
    return _column("trend_1h")


def trend_4h() -> list[Any]:
    return _column("trend_4h")


def trend_1d() -> list[Any]:
    return _column("trend_1d")


def log_ret() -> list[Any]:
    return _column("log_ret")


def rv_21d() -> list[Any]:
    return _column("rv_21d")


def vol_regime() -> list[Any]:
    return _column("vol_regime")


def funding_regime() -> list[Any]:
    return _column("funding_regime")


def market_regime() -> list[Any]:
    return _column("market_regime")


def dvol_frac() -> list[Any]:
    return _column("dvol_frac")


def rv30() -> list[Any]:
    return _column("rv30")


def atr_ratio() -> list[Any]:
    return _column("atr_ratio")


def dvol_pctile() -> list[Any]:
    return _column("dvol_pctile")


def dvol_ivrank() -> list[Any]:
    return _column("dvol_ivrank")


def vol_of_vol() -> list[Any]:
    return _column("vol_of_vol")


def vrp_spread() -> list[Any]:
    return _column("vrp_spread")


def vrp_logratio() -> list[Any]:
    return _column("vrp_logratio")


# name -> extraction function, used by the migration script so the
# declarative metadata table (there) and the bindings (here) cannot drift
# apart silently -- one place enumerates "every wrapped column".
BINDINGS = {
    "basis_bps": basis_bps,
    "trend_1h": trend_1h,
    "trend_4h": trend_4h,
    "trend_1d": trend_1d,
    "log_ret": log_ret,
    "rv_21d": rv_21d,
    "vol_regime": vol_regime,
    "funding_regime": funding_regime,
    "market_regime": market_regime,
    "dvol_frac": dvol_frac,
    "rv30": rv30,
    "atr_ratio": atr_ratio,
    "dvol_pctile": dvol_pctile,
    "dvol_ivrank": dvol_ivrank,
    "vol_of_vol": vol_of_vol,
    "vrp_spread": vrp_spread,
    "vrp_logratio": vrp_logratio,
}
