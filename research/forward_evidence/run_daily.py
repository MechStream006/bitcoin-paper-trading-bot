"""
run_daily.py
============
Daily forward evidence collection — runs H5, G1, G3, H-IVRV, then G3 ATR Expansion.

H5/G1/G3 are single-input (spot closes). H-IVRV is two-input (spot closes +
DVOL) and runs on its own isolated branch after the others have committed. G3 ATR
Expansion is OHLCV-based (high/low/close) and runs on a third isolated branch.

Usage:
    conda run -n btc_bot python forward_evidence/run_daily.py
    conda run -n btc_bot python forward_evidence/run_daily.py --run-date 2026-06-14
    conda run -n btc_bot python forward_evidence/run_daily.py --dry-run

run_date defaults to yesterday UTC (the last complete BTC calendar day).
Scheduled: 01:00 UTC (see collection_config.yaml for rationale and pre-conditions).

Governance check runs before any collection. Pass --dry-run to compute signals
and validate the data pipeline without writing to the DB.

Per-signal isolation (DQ3): one failure does not block the others.
Returns exit code 0 if all signals ok/skipped; 1 if any signal errored.
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import yaml

# Allow direct script invocation from research/: python forward_evidence/run_daily.py
_HERE          = pathlib.Path(__file__).resolve().parent
_RESEARCH_ROOT = _HERE.parent
for _p in (str(_HERE), str(_RESEARCH_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from forward_evidence import runner_h5, runner_g1, runner_g3, runner_ivrv
from forward_evidence import runner_atr_expansion as runner_atr
from forward_evidence.init_forward_db import init_forward_db
from forward_evidence.pre_reg_guard import FnHashViolation
from forward_evidence.runner_base import (
    DataUnavailableError, load_daily_closes, load_daily_dvol, load_daily_ohlcv,
)

from forward_evidence import (
    decision_function_h5               as _h5,
    decision_function_g1_market_regime as _g1,
    decision_function_g3_tsmom         as _g3,
    decision_function_ivrv             as _ivrv,
    decision_function_g3_atr_expansion as _atr,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%Y-%m-%dT%H:%M:%SZ',
)
log = logging.getLogger(__name__)

_CONFIG_PATH   = _HERE / "collection_config.yaml"
_DB_PATH       = _RESEARCH_ROOT / "data" / "evidence_forward.db"


def _derive_run_date() -> str:
    """Yesterday in UTC ('YYYY-MM-DD') — the last complete BTC calendar day."""
    return (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()


def _check_governance(research_root: pathlib.Path) -> bool:
    """
    Call evidence_library/report.py --check via subprocess.

    Returns True if registry is clean (exit 0); False and logs all errors otherwise.
    Uses sys.executable so the check runs in the same Python environment as the
    caller — no env activation required.
    """
    ev_lib = research_root / "evidence_library"
    result = subprocess.run(
        [sys.executable, str(ev_lib / "report.py"), "--check"],
        capture_output=True,
        text=True,
    )
    output = result.stdout.strip()
    if result.returncode == 0:
        log.info("GOVERNANCE: %s", output)
        return True
    log.error("GOVERNANCE CHECK FAILED:\n%s", output)
    return False


def _run_dry(
    run_date: str,
    closes: "pd.Series",
    ohlcv_dir: pathlib.Path,
    sigs: dict,
    cfg: dict,
) -> dict[str, str]:
    """
    Compute each signal position without writing to the DB.

    Loads data and calls decision functions directly so the full data pipeline is
    exercised. Log lines show what *would* be written. Returns 'dry-run' for each
    signal that computed successfully, 'unavailable' if data was missing.
    """
    import pandas as pd  # local import — only needed in dry-run path

    results: dict[str, str] = {}
    closes_to = closes[closes.index <= run_date]

    # ── Close-only signals ────────────────────────────────────────────────────
    for sig_id, df_mod in [
        ('h5_vol_regime',           _h5),
        ('g1_market_regime_sma200', _g1),
        ('g3_momentum_tsmom',       _g3),
    ]:
        if sig_id not in sigs:
            continue
        try:
            pos = float(df_mod.compute_signal(closes_to).iloc[-1])
            log.info("[dry-run] %-35s position=%.1f (no DB write)", sig_id, pos)
            results[sig_id] = 'dry-run'
        except Exception as exc:
            log.error("[dry-run] %s compute failed: %s", sig_id, exc)
            results[sig_id] = 'error'

    # ── H-IVRV (two-input: spot closes + DVOL) ───────────────────────────────
    ivrv_id = 'ivrv_vrp_spread'
    if ivrv_id in sigs:
        try:
            dvol_dir = _RESEARCH_ROOT / cfg['dvol']['parquet_dir']
            dvol     = load_daily_dvol(dvol_dir, run_date, min_rows=_ivrv.MIN_HISTORY)
            dvol_to  = dvol[dvol.index <= run_date]
            pos      = float(_ivrv.compute_signal(closes_to, dvol_to).iloc[-1])
            log.info("[dry-run] %-35s position=%.1f (no DB write)", ivrv_id, pos)
            results[ivrv_id] = 'dry-run'
        except DataUnavailableError as exc:
            results[ivrv_id] = 'unavailable'
            log.warning("[dry-run] %s DVOL unavailable: %s", ivrv_id, exc)

    # ── G3 ATR Expansion (OHLCV — H/L/C required) ────────────────────────────
    atr_id  = 'g3_atr_expansion'
    atr_cfg = sigs.get(atr_id)
    if atr_cfg is not None and atr_cfg.get('enabled', False):
        try:
            ohlcv    = load_daily_ohlcv(ohlcv_dir, run_date, min_rows=_atr.MIN_HISTORY)
            ohlcv_to = ohlcv[ohlcv.index <= run_date]
            pos      = float(_atr.compute_signal(ohlcv_to).iloc[-1])
            log.info("[dry-run] %-35s position=%.1f (no DB write)", atr_id, pos)
            results[atr_id] = 'dry-run'
        except DataUnavailableError as exc:
            results[atr_id] = 'unavailable'
            log.warning("[dry-run] %s OHLCV unavailable: %s", atr_id, exc)

    return results


def run_all(
    run_date: str,
    db_path: pathlib.Path = _DB_PATH,
    dry_run: bool = False,
) -> dict[str, str]:
    """
    Run H5, G1, G3, H-IVRV, and (when enabled) G3 ATR Expansion for run_date.

    Per-signal isolation (DQ3): all signals attempted; one failure does not block.
    In dry-run mode, signals are computed but no DB writes are made.

    Returns
    -------
    dict mapping signal_id → outcome string:
        'ok'               — collection succeeded
        'skipped'          — already ran today (idempotent)
        'dry-run'          — computed in dry-run mode (no DB write)
        'fn_hash_violation'— decision function tampered
        'unavailable'      — data missing (DataUnavailableError)
        'blocked'          — prior error/violation on record (RuntimeError)
        'error'            — unexpected exception
    """
    cfg       = yaml.safe_load(_CONFIG_PATH.read_text(encoding='utf-8'))
    ohlcv_dir = _RESEARCH_ROOT / cfg['ohlcv']['parquet_dir']
    sigs      = cfg['signals']

    if not dry_run:
        init_forward_db(db_path)

    # Load closes once; use max MIN_HISTORY across all signals
    max_history = max(
        _h5.MIN_HISTORY, _g1.MIN_HISTORY, _g3.MIN_HISTORY,
        _ivrv.MIN_HISTORY, _atr.MIN_HISTORY,
    )
    try:
        closes = load_daily_closes(ohlcv_dir, run_date, min_rows=max_history)
    except DataUnavailableError as exc:
        log.error("OHLCV data unavailable for %s: %s", run_date, exc)
        return {sid: 'unavailable' for sid in sigs}

    if dry_run:
        return _run_dry(run_date, closes, ohlcv_dir, sigs, cfg)

    runners = {
        'h5_vol_regime':           (runner_h5,  sigs['h5_vol_regime']),
        'g1_market_regime_sma200': (runner_g1,  sigs['g1_market_regime_sma200']),
        'g3_momentum_tsmom':       (runner_g3,  sigs['g3_momentum_tsmom']),
    }

    results: dict[str, str] = {}
    for sig_id, (runner_mod, sig_cfg) in runners.items():
        cost_bps = float(sig_cfg['cost_bps'])
        try:
            results[sig_id] = runner_mod.collect(run_date, closes, db_path, cost_bps)
        except FnHashViolation:
            results[sig_id] = 'fn_hash_violation'
            log.error("[%s] fn_hash_violation on %s", sig_id, run_date)
        except DataUnavailableError as exc:
            results[sig_id] = 'unavailable'
            log.error("[%s] data unavailable: %s", sig_id, exc)
        except RuntimeError as exc:
            results[sig_id] = 'blocked'
            log.error("[%s] blocked: %s", sig_id, exc)
        except Exception as exc:
            results[sig_id] = 'error'
            log.error("[%s] unexpected error: %s", sig_id, exc, exc_info=True)

    # ── H-IVRV (two-input: spot closes + DVOL). Isolated branch — runs AFTER the
    #    single-input loop above (which has already committed), and loads DVOL in
    #    its own try/except, so a DVOL outage degrades ONLY ivrv coverage and can
    #    never block or alter H5/G1/G3. Skipped entirely if not in config. ──────
    ivrv_id  = 'ivrv_vrp_spread'
    ivrv_cfg = sigs.get(ivrv_id)
    if ivrv_cfg is not None:
        dvol = None
        try:
            dvol_dir = _RESEARCH_ROOT / cfg['dvol']['parquet_dir']
            dvol = load_daily_dvol(dvol_dir, run_date, min_rows=_ivrv.MIN_HISTORY)
        except DataUnavailableError as exc:
            results[ivrv_id] = 'unavailable'
            log.error("[%s] DVOL unavailable: %s", ivrv_id, exc)
        if dvol is not None:
            try:
                results[ivrv_id] = runner_ivrv.collect(
                    run_date, closes, dvol, db_path, float(ivrv_cfg['cost_bps'])
                )
            except FnHashViolation:
                results[ivrv_id] = 'fn_hash_violation'
                log.error("[%s] fn_hash_violation on %s", ivrv_id, run_date)
            except DataUnavailableError as exc:
                results[ivrv_id] = 'unavailable'
                log.error("[%s] data unavailable: %s", ivrv_id, exc)
            except RuntimeError as exc:
                results[ivrv_id] = 'blocked'
                log.error("[%s] blocked: %s", ivrv_id, exc)
            except Exception as exc:
                results[ivrv_id] = 'error'
                log.error("[%s] unexpected error: %s", ivrv_id, exc, exc_info=True)

    # ── G3 ATR Expansion (OHLCV-based signal). Isolated branch — runs AFTER all
    #    close-only and two-input signals (both already committed). Loads its own
    #    OHLCV Series via load_daily_ohlcv() so a data failure here degrades ONLY
    #    ATR coverage. Gated by enabled: true in collection_config.yaml; currently
    #    false (Phase 4 wired but not active — Phase 5 flips the gate). ─────────
    atr_id  = 'g3_atr_expansion'
    atr_cfg = sigs.get(atr_id)
    if atr_cfg is not None and atr_cfg.get('enabled', False):
        ohlcv = None
        try:
            ohlcv = load_daily_ohlcv(ohlcv_dir, run_date, min_rows=_atr.MIN_HISTORY)
        except DataUnavailableError as exc:
            results[atr_id] = 'unavailable'
            log.error("[%s] OHLCV unavailable: %s", atr_id, exc)
        if ohlcv is not None:
            try:
                results[atr_id] = runner_atr.collect(
                    run_date, ohlcv, db_path, float(atr_cfg['cost_bps'])
                )
            except FnHashViolation:
                results[atr_id] = 'fn_hash_violation'
                log.error("[%s] fn_hash_violation on %s", atr_id, run_date)
            except DataUnavailableError as exc:
                results[atr_id] = 'unavailable'
                log.error("[%s] data unavailable: %s", atr_id, exc)
            except RuntimeError as exc:
                results[atr_id] = 'blocked'
                log.error("[%s] blocked: %s", atr_id, exc)
            except Exception as exc:
                results[atr_id] = 'error'
                log.error("[%s] unexpected error: %s", atr_id, exc, exc_info=True)

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily forward evidence collection")
    parser.add_argument(
        '--run-date',
        default=None,
        metavar='YYYY-MM-DD',
        help='Override run date (default: yesterday UTC)',
    )
    parser.add_argument(
        '--db-path',
        type=pathlib.Path,
        default=_DB_PATH,
        help=f'Path to evidence_forward.db (default: {_DB_PATH})',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help=(
            'Compute signals and validate the data pipeline without writing to the DB. '
            'Governance check still runs. Exits 0 if all enabled signals are computable.'
        ),
    )
    args = parser.parse_args()

    run_date = args.run_date or _derive_run_date()
    log.info("run_date=%s  db=%s  dry_run=%s", run_date, args.db_path, args.dry_run)

    if not _check_governance(_RESEARCH_ROOT):
        log.error("Aborting: fix registry errors and retry.")
        return 1

    results = run_all(run_date, db_path=args.db_path, dry_run=args.dry_run)
    for sig, outcome in results.items():
        log.info("  %-35s %s", sig, outcome)

    failed = [s for s, r in results.items() if r in ('fn_hash_violation', 'error', 'blocked')]
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
