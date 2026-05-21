"""Phase 3a Step 1 — WSManager.resubscribe() operational validation soak.

Boots the full Runtime (scheduler + supervisor running normally), then runs
a structured validation thread alongside it to exercise resubscribe() paths
while observing runtime stability.

Duration: default 300 s (~5 min); phases consume ~200 s, leaving 100 s margin.

Usage:
    python validate_phase3a_step1.py [duration_sec]

Validates:
    Phase 1 — STREAMING baseline          : health subscribed=N, FSM correct
    Phase 2 — Rapid resubscribes (5 calls): diffs correct, no reconnect storm
    Phase 3 — Reconnect-during-resubscribe: socket force-close, rebuild check
    Phase 4 — No-op idempotency           : repeated same-universe calls
    Phase 5 — Stability soak (60 s)       : data flowing, count stable, no storm

Exit code: 0=all pass, 1=bootstrap/runtime error, 2=one or more checks FAIL.

Log file: data/logs/btc_bot_validate_p3a1.jsonl (separate from production log)
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

import config.settings as cfg
from src.exchange.ws_manager import WSState
from src.runtime.lifecycle import Runtime

_UTC = timezone.utc
VALIDATE_DURATION_SEC = int(sys.argv[1]) if len(sys.argv) > 1 else 300


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

class _UTCFmt(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        return datetime.fromtimestamp(record.created, tz=_UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class _JsonlFmt(logging.Formatter):
    def format(self, record):
        obj = {
            "ts":    datetime.fromtimestamp(record.created, tz=_UTC).isoformat(),
            "level": record.levelname,
            "name":  record.name,
            "msg":   record.getMessage(),
        }
        if record.exc_info:
            obj["exc"] = self.formatException(record.exc_info)
        return json.dumps(obj, ensure_ascii=True)


def _setup_logging():
    log_dir = cfg.DB_PATH.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "btc_bot_validate_p3a1.jsonl"

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(_UTCFmt("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))
    root.addHandler(ch)

    fh = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=20 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    fh.setFormatter(_JsonlFmt())
    root.addHandler(fh)

    return logging.getLogger("validate")


# ---------------------------------------------------------------------------
# Check result accumulator
# ---------------------------------------------------------------------------

class _Check:
    def __init__(self, name: str) -> None:
        self.name = name
        self.status: str = "PENDING"
        self.note: str = ""

    def passed(self, note: str = "") -> None:
        self.status = "PASS"
        self.note = note

    def failed(self, note: str) -> None:
        self.status = "FAIL"
        self.note = note

    def warned(self, note: str) -> None:
        self.status = "WARN"
        self.note = note


CHECK_NAMES = [
    "p1_streaming_baseline",
    "p2_rapid_resubscribes",
    "p3_sub_state_post_forceclose",
    "p3_reconnect_rebuild",
    "p4_noop_idempotency",
    "p5_data_flowing",
    "p5_subscription_stable",
    "p5_no_reconnect_storms",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wait_streaming(ws_mgr, timeout: float = 90, poll: float = 0.5):
    """Poll until STREAMING or timeout. Returns (reached, elapsed_sec)."""
    t0 = time.monotonic()
    deadline = t0 + timeout
    while time.monotonic() < deadline:
        with ws_mgr._lock:
            state = ws_mgr._ws_state
        if state == WSState.STREAMING:
            return True, time.monotonic() - t0
        time.sleep(poll)
    return False, timeout


def _force_reconnect(ws_mgr, logger) -> bool:
    """Close the live WS socket to trigger backoff reconnect. Returns True on success."""
    with ws_mgr._lock:
        ws = ws_mgr._live_ws
    if ws is None:
        logger.warning("[val] force_reconnect: _live_ws is None — not STREAMING")
        return False
    try:
        ws.close()
        logger.info("[val] force_reconnect: live socket closed")
        return True
    except Exception as exc:
        logger.warning("[val] force_reconnect: close() raised %s: %s", type(exc).__name__, exc)
        return False


def _health_sub_count(ws_mgr) -> int:
    return len(ws_mgr.subscribed_symbols())


def _reconnect_count(ws_mgr) -> int:
    with ws_mgr._lock:
        return ws_mgr._reconnect_count


def _msg_count(ws_mgr) -> int:
    with ws_mgr._lock:
        return ws_mgr._msg_count


# ---------------------------------------------------------------------------
# Validator thread
# ---------------------------------------------------------------------------

def _validator_thread(rt: Runtime, checks: Dict[str, _Check], logger) -> None:
    """Runs structured validation phases alongside rt.run().

    On completion (pass or fail), raises SIGINT to end the soak.
    All exceptions are caught so the thread never crashes silently.
    """
    try:
        _run_validation(rt, checks, logger)
    except Exception as exc:
        logger.error("[val] validator thread raised: %s: %s", type(exc).__name__, exc, exc_info=True)
        for c in checks.values():
            if c.status == "PENDING":
                c.failed(f"validator crashed: {exc}")
    finally:
        logger.info("[val] validator thread done — requesting shutdown")
        signal.raise_signal(signal.SIGINT)


def _run_validation(rt: Runtime, checks: Dict[str, _Check], logger) -> None:
    # ------------------------------------------------------------------
    # Wait for Runtime to reach RUNNING state
    # ------------------------------------------------------------------
    logger.info("[val] waiting for Runtime RUNNING state (up to 120 s)...")
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if rt.state.value == "RUNNING":
            break
        time.sleep(0.5)
    else:
        msg = "Runtime did not reach RUNNING within 120 s"
        logger.error("[val] %s", msg)
        for c in checks.values():
            c.failed(msg)
        return

    ws_mgr = rt._ws_manager
    logger.info("[val] Runtime RUNNING. Beginning validation phases.")

    # ==================================================================
    # Phase 1: STREAMING baseline
    # ==================================================================
    logger.info("[val] " + "=" * 55)
    logger.info("[val] Phase 1: STREAMING baseline")
    logger.info("[val] " + "=" * 55)
    c1 = checks["p1_streaming_baseline"]

    ok, elapsed = _wait_streaming(ws_mgr, timeout=60)
    if not ok:
        c1.failed("did not reach STREAMING within 60 s")
        logger.error("[val] Phase 1 FAIL: %s", c1.note)
        return

    sub_initial = _health_sub_count(ws_mgr)
    h = ws_mgr.health()
    if f"subscribed={sub_initial}" not in h.details:
        c1.failed(f"health subscribed mismatch: {h.details}")
    elif "state=STREAMING" not in h.details:
        c1.failed(f"unexpected state in health: {h.details}")
    else:
        c1.passed(f"STREAMING in {elapsed:.1f} s, subscribed={sub_initial}")

    logger.info("[val] Phase 1: %s — %s", c1.status, c1.note)

    # Capture the initial universe as the canonical reference
    universe_a: list[str] = sorted(ws_mgr.subscribed_symbols())
    n = len(universe_a)
    logger.info("[val] initial universe size: %d symbols", n)

    # Build subsets — all from universe_a to avoid MarketState universe warnings
    u_b = universe_a[:max(n - 14, 1)]          # remove last 14
    u_c = universe_a[:max(n - 9, 1)]            # add 5 back (first n-9)
    u_d = universe_a[5:max(n - 9, 6)]           # remove first 5, keep rest
    u_noop = universe_a                          # same as a → no-op

    # ==================================================================
    # Phase 2: Rapid resubscribes (5 calls with explicit diffs)
    # ==================================================================
    logger.info("[val] " + "=" * 55)
    logger.info("[val] Phase 2: Rapid resubscribes")
    logger.info("[val] " + "=" * 55)
    c2 = checks["p2_rapid_resubscribes"]

    rc_before_p2 = _reconnect_count(ws_mgr)
    phase2_ok = True
    phase2_notes: list[str] = []

    steps = [
        ("a→b", universe_a, u_b),       # remove 14
        ("b→c", u_b,        u_c),       # add 5
        ("c→d", u_c,        u_d),       # remove 5 from front
        ("d→a", u_d,        universe_a),# restore full set
        ("a→a", universe_a, u_noop),    # no-op
    ]

    for step_name, prev, new in steps:
        ws_mgr.resubscribe(prev, new)
        got = ws_mgr.subscribed_symbols()
        exp = set(new)
        h2 = ws_mgr.health()
        sub_ok = (got == exp)
        health_ok = (f"subscribed={len(exp)}" in h2.details)

        if not sub_ok:
            phase2_ok = False
            note = f"step {step_name}: got {len(got)} want {len(exp)}"
            phase2_notes.append(note)
            logger.error("[val] Phase 2 FAIL %s", note)
        elif not health_ok:
            phase2_ok = False
            note = f"step {step_name}: health={h2.details}"
            phase2_notes.append(note)
            logger.error("[val] Phase 2 FAIL %s", note)
        else:
            logger.info("[val] Phase 2 %s: subscribed=%d OK", step_name, len(exp))

    rc_after_p2 = _reconnect_count(ws_mgr)
    if rc_after_p2 != rc_before_p2:
        phase2_ok = False
        note = f"reconnect storm: count went {rc_before_p2}→{rc_after_p2}"
        phase2_notes.append(note)
        logger.error("[val] Phase 2 FAIL: %s", note)

    if phase2_ok:
        c2.passed(f"5 resubscribes correct, reconnect_count stable at {rc_before_p2}")
    else:
        c2.failed("; ".join(phase2_notes))
    logger.info("[val] Phase 2: %s — %s", c2.status, c2.note)

    # Restore full universe before Phase 3
    ws_mgr.resubscribe([], list(universe_a))
    time.sleep(0.5)

    # ==================================================================
    # Phase 3: Reconnect during resubscribe
    # ==================================================================
    logger.info("[val] " + "=" * 55)
    logger.info("[val] Phase 3: Reconnect-during-resubscribe")
    logger.info("[val] " + "=" * 55)
    c3_sub    = checks["p3_sub_state_post_forceclose"]
    c3_rebuild = checks["p3_reconnect_rebuild"]

    # Target universe for phase 3 (50 symbols, different from full 64)
    universe_r: list[str] = universe_a[7:57]
    rc_before_p3 = _reconnect_count(ws_mgr)

    # Step 3a: resubscribe then immediately force-close
    ws_mgr.resubscribe(list(universe_a), universe_r)
    time.sleep(0.15)    # let live send attempt go (best-effort)
    closed = _force_reconnect(ws_mgr, logger)

    if not closed:
        c3_sub.warned("force-close skipped: socket was not live")
        c3_rebuild.warned("force-close skipped: reconnect test inconclusive")
        logger.warning("[val] Phase 3: skipped (ws was not STREAMING at close time)")
    else:
        # _subscribed_symbols must be universe_r immediately (committed before close)
        got_r = ws_mgr.subscribed_symbols()
        if got_r != set(universe_r):
            c3_sub.failed(
                f"_subscribed_symbols wrong immediately after force-close: "
                f"got {len(got_r)}, want {len(universe_r)}"
            )
        else:
            c3_sub.passed(
                f"_subscribed_symbols == universe_r ({len(universe_r)} syms) "
                f"immediately after force-close"
            )
        logger.info("[val] Phase 3 sub check: %s — %s", c3_sub.status, c3_sub.note)

        # Step 3b: wait for reconnect to STREAMING
        ok3, elapsed3 = _wait_streaming(ws_mgr, timeout=60)
        if not ok3:
            c3_rebuild.failed("did not return to STREAMING within 60 s after force-close")
        else:
            rc_after_p3 = _reconnect_count(ws_mgr)
            got_sub    = ws_mgr.subscribed_symbols()
            ch_syms    = set(ws_mgr._current_channels()[0]["symbols"])
            h3         = ws_mgr.health()

            rebuild_ok = True
            rebuild_notes: list[str] = []

            if got_sub != set(universe_r):
                rebuild_ok = False
                rebuild_notes.append(f"_subscribed_symbols drifted after reconnect: {len(got_sub)} vs {len(universe_r)}")
            if ch_syms != set(universe_r):
                rebuild_ok = False
                rebuild_notes.append(f"_current_channels wrong: {len(ch_syms)} vs {len(universe_r)}")
            if f"subscribed={len(universe_r)}" not in h3.details:
                rebuild_ok = False
                rebuild_notes.append(f"health subscribed wrong: {h3.details}")
            if rc_after_p3 <= rc_before_p3:
                rebuild_ok = False
                rebuild_notes.append(f"reconnect_count did not increase ({rc_before_p3}→{rc_after_p3})")

            if rebuild_ok:
                c3_rebuild.passed(
                    f"reconnected (rc={rc_after_p3}) in {elapsed3:.1f} s; "
                    f"subscribed={len(universe_r)}; _current_channels correct"
                )
            else:
                c3_rebuild.failed("; ".join(rebuild_notes))

        logger.info("[val] Phase 3 rebuild: %s — %s", c3_rebuild.status, c3_rebuild.note)

    # ==================================================================
    # Phase 4: No-op idempotency under live conditions
    # ==================================================================
    logger.info("[val] " + "=" * 55)
    logger.info("[val] Phase 4: No-op idempotency")
    logger.info("[val] " + "=" * 55)
    c4 = checks["p4_noop_idempotency"]

    current_syms = sorted(ws_mgr.subscribed_symbols())
    rc_before_p4 = _reconnect_count(ws_mgr)
    state_before = None
    with ws_mgr._lock:
        state_before = ws_mgr._ws_state

    phase4_ok = True
    phase4_notes: list[str] = []
    for i in range(5):
        ws_mgr.resubscribe(current_syms, current_syms)
        got4 = ws_mgr.subscribed_symbols()
        if got4 != set(current_syms):
            phase4_ok = False
            phase4_notes.append(f"call {i}: subscribed changed")
        with ws_mgr._lock:
            state_now = ws_mgr._ws_state
        if state_now != state_before:
            phase4_ok = False
            phase4_notes.append(f"call {i}: FSM state changed {state_before.value}→{state_now.value}")

    rc_after_p4 = _reconnect_count(ws_mgr)
    if rc_after_p4 != rc_before_p4:
        phase4_ok = False
        phase4_notes.append(f"reconnect_count changed {rc_before_p4}→{rc_after_p4}")

    if phase4_ok:
        c4.passed(f"5 no-op calls, subscribed/FSM stable, reconnect_count={rc_before_p4}")
    else:
        c4.failed("; ".join(phase4_notes))
    logger.info("[val] Phase 4: %s — %s", c4.status, c4.note)

    # ==================================================================
    # Phase 5: Stability soak (60 s)
    # ==================================================================
    logger.info("[val] " + "=" * 55)
    logger.info("[val] Phase 5: Stability soak (60 s)")
    logger.info("[val] " + "=" * 55)
    c5_data   = checks["p5_data_flowing"]
    c5_stable = checks["p5_subscription_stable"]
    c5_storms = checks["p5_no_reconnect_storms"]

    soak_sub_count = _health_sub_count(ws_mgr)
    soak_rc        = _reconnect_count(ws_mgr)
    soak_msgs      = _msg_count(ws_mgr)
    sub_drifted    = False
    STORM_THRESHOLD = 3  # reconnects acceptable during 60 s

    logger.info(
        "[val] soak baseline: subscribed=%d rc=%d msgs=%d",
        soak_sub_count, soak_rc, soak_msgs,
    )

    for tick in range(6):      # 6 × 10 s = 60 s
        time.sleep(10)
        cur_sub = _health_sub_count(ws_mgr)
        cur_rc  = _reconnect_count(ws_mgr)
        cur_msg = _msg_count(ws_mgr)
        h5 = ws_mgr.health()
        logger.info(
            "[val] soak %d/6: subscribed=%d rc=%d msgs=%d (+%d) state=%s",
            tick + 1, cur_sub, cur_rc, cur_msg, cur_msg - soak_msgs,
            h5.details.split()[0],
        )
        if cur_sub != soak_sub_count:
            sub_drifted = True
            logger.error(
                "[val] soak: subscribed drifted %d → %d at tick %d",
                soak_sub_count, cur_sub, tick + 1,
            )

    final_rc   = _reconnect_count(ws_mgr)
    final_msgs = _msg_count(ws_mgr)
    msgs_delta = final_msgs - soak_msgs
    rc_delta   = final_rc - soak_rc

    if msgs_delta > 0:
        c5_data.passed(f"+{msgs_delta} msgs over 60 s")
    else:
        c5_data.failed("msg_count did not increase during 60 s soak")

    if not sub_drifted:
        c5_stable.passed(f"subscribed={soak_sub_count} stable for 60 s")
    else:
        c5_stable.failed("subscribed count drifted during soak")

    if rc_delta <= STORM_THRESHOLD:
        c5_storms.passed(f"+{rc_delta} reconnects over 60 s (≤ threshold {STORM_THRESHOLD})")
    else:
        c5_storms.failed(f"reconnect storm: +{rc_delta} over 60 s (threshold {STORM_THRESHOLD})")

    logger.info("[val] Phase 5 data:   %s — %s", c5_data.status, c5_data.note)
    logger.info("[val] Phase 5 stable: %s — %s", c5_stable.status, c5_stable.note)
    logger.info("[val] Phase 5 storms: %s — %s", c5_storms.status, c5_storms.note)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _print_report(checks: Dict[str, _Check], logger) -> int:
    """Print final report. Returns exit code (0=all pass, 2=any fail)."""
    logger.info("")
    logger.info("=" * 60)
    logger.info("Phase 3a Step 1 — Validation Report")
    logger.info("=" * 60)
    any_fail = False
    for name, c in checks.items():
        sym = {"PASS": "OK ", "FAIL": "XX ", "WARN": "WW ", "PENDING": "?? "}.get(c.status, "?? ")
        logger.info("[%s] %s  %s", sym, name, c.note)
        if c.status == "FAIL":
            any_fail = True
    logger.info("=" * 60)
    if any_fail:
        logger.error("RESULT: FAIL — one or more checks failed")
        return 2
    logger.info("RESULT: PASS — all checks passed")
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    logger = _setup_logging()
    now_utc = datetime.now(_UTC)
    logger.info("=" * 60)
    logger.info("Phase 3a Step 1 — Operational Validation Soak")
    logger.info("UTC now:  %s", now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"))
    logger.info("Duration: %d s", VALIDATE_DURATION_SEC)
    logger.info("=" * 60)

    checks: Dict[str, _Check] = {name: _Check(name) for name in CHECK_NAMES}
    exit_code_holder: list[int] = [0]

    rt = Runtime(
        db_path=cfg.DB_PATH,
        delta_base_url=cfg.DELTA_BASE_URL,
        delta_api_key=cfg.DELTA_API_KEY,
        delta_api_secret=cfg.DELTA_API_SECRET,
        delta_ws_url=cfg.DELTA_WS_URL,
        telegram_bot_token=cfg.TELEGRAM_BOT_TOKEN,
        telegram_channel_id=cfg.TELEGRAM_CHANNEL_ID,
        # Tighter cadences for validation
        expiry_observe_interval_sec=60,
        heartbeat_interval_sec=60,
        snapshot_interval_sec=30,
        chain_refresh_interval_sec=600,
        warmup_timeout_sec=90,
    )

    # Watchdog: hard deadline in case validator thread hangs
    def _watchdog():
        time.sleep(VALIDATE_DURATION_SEC)
        logger.warning("[watchdog] hard deadline reached — forcing shutdown")
        signal.raise_signal(signal.SIGINT)

    # Validator thread: runs test phases, then signals SIGINT
    def _val_entry():
        _validator_thread(rt, checks, logger)
        code = _print_report(checks, logger)
        exit_code_holder[0] = code

    t_watchdog = threading.Thread(target=_watchdog, name="watchdog", daemon=True)
    t_validator = threading.Thread(target=_val_entry, name="validator", daemon=True)
    t_watchdog.start()

    try:
        rt.bootstrap()
    except Exception as exc:
        logger.critical("[validate] bootstrap failed: %s: %s", type(exc).__name__, exc)
        rt.drain()
        return 1

    # Start validator after bootstrap (ws_manager reference is set)
    t_validator.start()

    try:
        rt.run()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        logger.critical("[validate] rt.run() raised: %s: %s", type(exc).__name__, exc, exc_info=True)
        return 2
    finally:
        rt.drain()
        # Wait briefly for validator to finish its report
        t_validator.join(timeout=5)
        logger.info("[validate] exited (state=%s)", rt.state.value)

    return exit_code_holder[0]


if __name__ == "__main__":
    sys.exit(main())
