"""
run_daily.py
============
Daily forward evidence collection — runs H5, G1, G3 in sequence.

Usage:
    conda run -n btc_bot python forward_evidence/run_daily.py
    conda run -n btc_bot python forward_evidence/run_daily.py --run-date 2026-06-14

run_date defaults to yesterday UTC (the last complete BTC day).
Scheduled: 01:00 UTC (see collection_config.yaml for rationale and pre-conditions).

Per-signal isolation (DQ3): one failure does not block the others.
Returns exit code 0 if all signals ok/skipped; 1 if any signal errored.
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import yaml

from forward_evidence import runner_h5, runner_g1, runner_g3
from forward_evidence.init_forward_db import init_forward_db
from forward_evidence.pre_reg_guard import FnHashViolation
from forward_evidence.runner_base import DataUnavailableError, load_daily_closes

from forward_evidence import (
    decision_function_h5         as _h5,
    decision_function_g1_market_regime as _g1,
    decision_function_g3_tsmom   as _g3,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%Y-%m-%dT%H:%M:%SZ',
)
log = logging.getLogger(__name__)

_HERE          = pathlib.Path(__file__).resolve().parent
_RESEARCH_ROOT = _HERE.parent
_CONFIG_PATH   = _HERE / "collection_config.yaml"
_DB_PATH       = _RESEARCH_ROOT / "data" / "evidence_forward.db"


def _derive_run_date() -> str:
    """Yesterday in UTC ('YYYY-MM-DD') — the last complete BTC calendar day."""
    return (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()


def run_all(
    run_date: str,
    db_path: pathlib.Path = _DB_PATH,
) -> dict[str, str]:
    """
    Run H5, G1, G3 for run_date. Per-signal isolation: all three attempted.

    Returns
    -------
    dict mapping signal_id → outcome string:
        'ok'               — collection succeeded
        'skipped'          — already ran today (idempotent)
        'fn_hash_violation'— decision function tampered
        'unavailable'      — OHLCV data missing (DataUnavailableError)
        'blocked'          — prior error/violation on record (RuntimeError)
        'error'            — unexpected exception
    """
    cfg       = yaml.safe_load(_CONFIG_PATH.read_text(encoding='utf-8'))
    ohlcv_dir = _RESEARCH_ROOT / cfg['ohlcv']['parquet_dir']
    sigs      = cfg['signals']

    init_forward_db(db_path)

    # Load closes once; use max MIN_HISTORY across all signals
    max_history = max(_h5.MIN_HISTORY, _g1.MIN_HISTORY, _g3.MIN_HISTORY)
    try:
        closes = load_daily_closes(ohlcv_dir, run_date, min_rows=max_history)
    except DataUnavailableError as exc:
        log.error("OHLCV data unavailable for %s: %s", run_date, exc)
        return {sid: 'unavailable' for sid in sigs}

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
    args = parser.parse_args()

    run_date = args.run_date or _derive_run_date()
    log.info("run_date=%s  db=%s", run_date, args.db_path)

    results = run_all(run_date, db_path=args.db_path)
    for sig, outcome in results.items():
        log.info("  %-35s %s", sig, outcome)

    failed = [s for s, r in results.items() if r in ('fn_hash_violation', 'error', 'blocked')]
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
