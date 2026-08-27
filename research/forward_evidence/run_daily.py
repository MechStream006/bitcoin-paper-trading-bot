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

import sqlite3

from forward_evidence import runner_h5, runner_g1, runner_g3, runner_ivrv, runner_g2
from forward_evidence import runner_atr_expansion as runner_atr
from forward_evidence.init_forward_db import init_forward_db
from forward_evidence.pre_reg_guard import FnHashViolation
from forward_evidence.runner_base import (
    DataUnavailableError, load_daily_closes, load_daily_dvol, load_daily_funding,
    load_daily_ohlcv,
)
from forward_evidence.run_date_policy import (
    MODE_BACKFILL, MODE_SCHEDULED, PolicyConfigError, evaluate_run_date, load_policy,
)

from forward_evidence import (
    decision_function_h5               as _h5,
    decision_function_g1_market_regime as _g1,
    decision_function_g3_tsmom         as _g3,
    decision_function_ivrv             as _ivrv,
    decision_function_g3_atr_expansion as _atr,
    decision_function_g2_funding_level as _g2,
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


def _check_governance(research_root: pathlib.Path) -> tuple[bool, str]:
    """
    Call evidence_library/report.py --check via subprocess.

    Returns (True, stdout) if registry is clean (exit 0); (False, detail) and
    logs all errors otherwise. `detail` is the failure reason, suitable for
    persisting into run_log's `note` column by the caller.
    Uses sys.executable so the check runs in the same Python environment as the
    caller — no env activation required.
    """
    ev_lib = research_root / "evidence_library"
    try:
        result = subprocess.run(
            [sys.executable, str(ev_lib / "report.py"), "--check"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        log.error("GOVERNANCE CHECK TIMED OUT after 30s — aborting collection.")
        return False, "governance check subprocess timed out after 30s"
    if result.returncode == 0:
        log.info("GOVERNANCE: %s", result.stdout.strip())
        return True, result.stdout.strip()
    log.error(
        "GOVERNANCE CHECK FAILED:\nstdout: %s\nstderr: %s",
        result.stdout.strip(),
        result.stderr.strip(),
    )
    detail = (result.stdout.strip() + " " + result.stderr.strip()).strip()
    return False, detail or f"report.py --check exited {result.returncode}"


# fn_hash is a hex SHA-256 (64 chars); this placeholder is deliberately a
# different shape so a future reader can never mistake it for a real one.
_GOVERNANCE_FAILURE_FN_HASH = "N/A-GOVERNANCE-CHECK-FAILED"


def _blocked_signal_ids(sigs: dict) -> list[str]:
    """The signal_ids run_all() would have attempted for this config, mirroring
    its own inclusion rules exactly (h5/g1/g3 always; ivrv and g2 whenever
    configured; atr only when explicitly enabled) — so a governance-failure
    record never claims a disabled signal was blocked when it was never going
    to run. Order mirrors run_all()'s branch order."""
    ids = ['h5_vol_regime', 'g1_market_regime_sma200', 'g3_momentum_tsmom']
    ids = [sid for sid in ids if sid in sigs]
    if sigs.get('ivrv_vrp_spread') is not None:
        ids.append('ivrv_vrp_spread')
    if sigs.get('g2_funding_level') is not None:
        ids.append('g2_funding_level')
    atr_cfg = sigs.get('g3_atr_expansion')
    if atr_cfg is not None and atr_cfg.get('enabled', False):
        ids.append('g3_atr_expansion')
    return ids


def _record_collection_provenance(
    db_path: pathlib.Path,
    run_date: str,
    mode: str,
    detail: str,
) -> None:
    """
    Record HOW this run_date came to be collected (Gate 6B.3, defect B-03).

    Best-effort by design, and deliberately so: provenance is an audit aid, not
    a precondition for evidence. A provenance-write failure must never abort a
    collection that policy has already authorised, exactly as SnapshotWriter
    treats collection_metrics. The refusal path is what protects the record;
    this row explains an ACCEPTED one.

    INSERT OR IGNORE against UNIQUE(run_date, mode) keeps re-invocation and
    idempotent skips from accumulating rows.
    """
    try:
        init_forward_db(db_path)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO collection_provenance (run_date, mode, detail) "
                "VALUES (?,?,?)",
                (run_date, mode, detail),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:                                    # noqa: BLE001
        log.warning("could not record collection provenance for %s: %s", run_date, exc)


def _record_governance_check_failure(
    db_path: pathlib.Path,
    run_date: str,
    signal_ids: list[str],
    detail: str,
) -> None:
    """
    Persist a governance-check failure into evidence_forward.db's run_log.

    Before this, a subprocess crash in _check_governance() aborted main()
    with only a log line -- no queryable record survived in the evidence
    DB itself, so a run of failures was invisible to anything but the
    systemd journal (the exact gap the 2026-07-18..08-05 outage exposed).

    Writes one status='error' row per tracked signal rather than a single
    sentinel row: this is truthful (every signal really was blocked, none
    ran), requires no schema change (status='error' is already a valid
    run_log value), and needs no change to src/alerts/forward_status.py's
    consumption logic -- MAX(run_date) advancing here correctly reflects
    "an attempt happened and failed", which forward_status.py already
    reports as state='degraded' (all signals in `errors`), not silently
    masked as fresh and not misread as a missed cycle either.

    INSERT OR IGNORE: idempotent under retry, per the schema's own stated
    crash/recovery contract ("a crash before commit leaves no row here;
    retries start clean") -- a second same-day invocation after an
    already-recorded failure does not raise on the UNIQUE(signal_id,
    run_date) constraint.
    """
    init_forward_db(db_path)
    note = f"governance check failed: {detail[:500]}"
    con = sqlite3.connect(db_path)
    try:
        con.execute("PRAGMA foreign_keys = ON")
        con.executemany(
            """
            INSERT OR IGNORE INTO run_log (signal_id, run_date, fn_hash, status, note)
            VALUES (?, ?, ?, 'error', ?)
            """,
            [(sid, run_date, _GOVERNANCE_FAILURE_FN_HASH, note) for sid in signal_ids],
        )
        con.commit()
    finally:
        con.close()


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
        except Exception as exc:
            log.error("[dry-run] %s compute failed: %s", ivrv_id, exc)
            results[ivrv_id] = 'error'

    # ── G2 Funding Level (funding-only input; closes price trades) ───────────
    g2_id = 'g2_funding_level'
    if g2_id in sigs:
        try:
            funding_dir = _RESEARCH_ROOT / cfg['funding']['parquet_dir']
            funding     = load_daily_funding(funding_dir, run_date,
                                             min_rows=_g2.MIN_HISTORY)
            funding_to  = funding[funding.index <= run_date]
            pos         = float(_g2.compute_signal(funding_to).iloc[-1])
            log.info("[dry-run] %-35s position=%.1f (no DB write)", g2_id, pos)
            results[g2_id] = 'dry-run'
        except DataUnavailableError as exc:
            results[g2_id] = 'unavailable'
            log.warning("[dry-run] %s funding unavailable: %s", g2_id, exc)
        except Exception as exc:
            log.error("[dry-run] %s compute failed: %s", g2_id, exc)
            results[g2_id] = 'error'

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
        except Exception as exc:
            log.error("[dry-run] %s compute failed: %s", atr_id, exc)
            results[atr_id] = 'error'

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

    # ── G2 Funding Level (funding input). Isolated branch — runs AFTER the
    #    single-input loop and H-IVRV (both already committed), and loads funding
    #    in its own try/except, so a funding outage degrades ONLY g2 coverage and
    #    can never block or alter H5/G1/G3/IVRV. Skipped entirely if not in
    #    config. Funding is passed as aux_series; closes still price the trade. ─
    g2_id  = 'g2_funding_level'
    g2_cfg = sigs.get(g2_id)
    if g2_cfg is not None:
        funding = None
        try:
            funding_dir = _RESEARCH_ROOT / cfg['funding']['parquet_dir']
            funding = load_daily_funding(funding_dir, run_date,
                                         min_rows=_g2.MIN_HISTORY)
        except DataUnavailableError as exc:
            results[g2_id] = 'unavailable'
            log.error("[%s] funding unavailable: %s", g2_id, exc)
        except Exception as exc:
            results[g2_id] = 'error'
            log.error("[%s] funding load failed: %s", g2_id, exc, exc_info=True)
        if funding is not None:
            try:
                results[g2_id] = runner_g2.collect(
                    run_date, closes, funding, db_path, float(g2_cfg['cost_bps'])
                )
            except FnHashViolation:
                results[g2_id] = 'fn_hash_violation'
                log.error("[%s] fn_hash_violation on %s", g2_id, run_date)
            except DataUnavailableError as exc:
                results[g2_id] = 'unavailable'
                log.error("[%s] data unavailable: %s", g2_id, exc)
            except RuntimeError as exc:
                results[g2_id] = 'blocked'
                log.error("[%s] blocked: %s", g2_id, exc)
            except Exception as exc:
                results[g2_id] = 'error'
                log.error("[%s] unexpected error: %s", g2_id, exc, exc_info=True)

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
    parser.add_argument(
        '--backfill-authorized',
        action='store_true',
        help=(
            'Authorise manual historical collection of a --run-date other than '
            'yesterday UTC, within the configured backfill horizon. Never lifts the '
            'unconditional refusals (sealed lockbox, immutable provenance gaps). '
            'Authorised backfills are recorded in collection_provenance.'
        ),
    )
    args = parser.parse_args()

    run_date  = args.run_date or _derive_run_date()
    scheduled = args.run_date is None

    # ── run_date policy choke point (Gate 6B.3, defect B-03) ─────────────────
    # Before governance, before any loader, before any collection: a refused
    # date must write NOTHING. Placed here rather than in runner_base so the
    # shared path all six signals use is untouched, and so there is exactly one
    # place where "may this date be collected at all?" is answered.
    try:
        policy = load_policy()
    except PolicyConfigError as exc:
        log.error("run_date policy unavailable: %s", exc)
        log.error("Refusing to collect without an enforceable run_date policy.")
        return 1

    decision = evaluate_run_date(
        run_date,
        today=datetime.now(timezone.utc).date(),
        policy=policy,
        authorized=args.backfill_authorized,
    )
    if not decision.allowed:
        log.error("run_date REFUSED [%s]: %s", decision.code, decision.detail)
        log.error("Nothing was written. Collection did not start.")
        return 1
    if decision.mode == MODE_BACKFILL:
        log.warning("AUTHORISED BACKFILL: %s", decision.detail)

    log.info("run_date=%s  mode=%s  db=%s  dry_run=%s",
             run_date, decision.mode, args.db_path, args.dry_run)

    gov_ok, gov_detail = _check_governance(_RESEARCH_ROOT)
    if not gov_ok:
        log.error("Aborting: fix registry errors and retry.")
        if not args.dry_run:
            cfg = yaml.safe_load(_CONFIG_PATH.read_text(encoding='utf-8'))
            _record_governance_check_failure(
                args.db_path, run_date, _blocked_signal_ids(cfg['signals']), gov_detail,
            )
        return 1

    if not args.dry_run:
        _record_collection_provenance(
            args.db_path, run_date, decision.mode, decision.detail,
        )

    results = run_all(run_date, db_path=args.db_path, dry_run=args.dry_run)
    for sig, outcome in results.items():
        log.info("  %-35s %s", sig, outcome)

    failed = [s for s, r in results.items() if r in ('fn_hash_violation', 'error', 'blocked')]
    if failed:
        return 1

    # A MANUAL invocation that collected nothing must not report success
    # (Gate 6B.3, defect B-09's observability half). 'unavailable' stays
    # non-fatal for SCHEDULED runs on purpose: per-signal isolation is a
    # documented guarantee -- a DVOL or funding outage marks one signal
    # 'unavailable' and must never fail the daily job. The distinction is the
    # operator's intent, not the outcome: an explicit manual attempt that
    # produced no observation is a failed attempt and should be visible.
    if not scheduled and results and all(r == 'unavailable' for r in results.values()):
        log.error("Manual run for %s collected nothing (all signals unavailable).", run_date)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
