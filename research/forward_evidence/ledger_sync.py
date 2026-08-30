"""
ledger_sync.py
==============
Publish accumulated forward evidence into the Evidence Library ledger as
`live-forward` runs (lane **L2**).

WHY THIS EXISTS
---------------
Forward collection writes only to `evidence_forward.db`. The governance ledger
is written only by offline seeding/admission scripts. Nothing connected the two,
so every run in `ledger.db` carried `provenance='offline-corpus'`.

`lifecycle.check_validated_preconditions()` reads provenance from a family's
pinned run, and `thresholds.yaml` sets
`validated.<role>.requires_non_offline_provenance: true`. With no live-forward
run in the ledger, **no family could ever reach Validated** -- regardless of how
many days of forward evidence accumulated. This module is the missing sender.

The receiving end was already built: `record.append_run()` accepts
`provenance='live-forward'`, `LANES["L2"] = ("live-forward", "forward")` is a
legal lane pair, and `ledger.db` ships a `has_live_evidence` view.

WHAT THIS DOES
--------------
Appends ONE `live-forward` / `forward` run row per family, summarising the
CLOSED forward trades accumulated to an as-of date. Nothing else.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
No state transition, no gate_result, no registry edit, and DSR / BH-FDR are left
NULL -- the safe value, since `check_validated_preconditions()` hard-fails on
NULL. The sync supplies evidence; it does not open gates. A human performs
promotion.

POINT-IN-TIME CORRECTNESS
-------------------------
Only trades CLOSED as of `as_of` are included. Open trades are structurally
absent from the `trade_returns` view (no exit row -> no join), so no unrealised
P&L can leak; the `exit_date <= as_of` filter additionally excludes exits that
had not happened yet at the as-of date.

DETERMINISM
-----------
For a fixed (`evidence_forward.db` content, `as_of`) the trade set is fully
ordered by `(entry_date, entry_id)` and every statistic is a pure function of
that set. `run_id` (uuid4) and `run_ts` (wall clock) are non-deterministic
metadata assigned by `record.append_run()`; they identify the row, they are not
evidence.

IDEMPOTENCY
-----------
Re-running for the same `as_of` is a no-op: `sync_family()` first checks whether
a live-forward run already exists for `(family_id, window_end == as_of)` and
returns without writing. The ledger is append-only, so a duplicate is prevented
by not writing it, never by deleting one.

SCHEMA POLICY -- ASSERT, NEVER REPAIR (ADR-P1-1, Gate FB-1b decision 3)
-----------------------------------------------------------------------
This module NEVER calls `record.init_db()` and never issues DDL. An unattended
nightly job must not silently ALTER the governance ledger's schema (the registered
EH-001 hazard). If the ledger is missing or its schema is not what this module
requires, `sync_family()` raises `LedgerSchemaError` and writes nothing.
"""
from __future__ import annotations

import logging
import math
import pathlib
import sqlite3
import subprocess
import sys
from typing import Any, Literal

_HERE          = pathlib.Path(__file__).resolve().parent
_RESEARCH_ROOT = _HERE.parent
_EV_LIB        = _RESEARCH_ROOT / "evidence_library"

log = logging.getLogger(__name__)


def _record():
    """Import `record` lazily, at call time rather than import time.

    `record.py` lives in evidence_library/, which `Dockerfile.research`
    deliberately excludes (INV-1). A module-level import here would make
    `run_daily` -- which imports this module -- unimportable inside the research
    image, breaking every test that touches it. The dependency is real but it is
    only needed when a sync actually runs, and a sync can only run where the
    ledger exists. Deferring the import matches that reality instead of
    forcing evidence_library into an image that is defined by its absence.
    """
    if str(_EV_LIB) not in sys.path:
        sys.path.insert(0, str(_EV_LIB))
    import record  # noqa: PLC0415 -- deliberate: see docstring
    return record

# Annualisation basis. Must match evidence/engine.py ANN and
# thresholds.yaml governance.ann -- crypto trades 365 days/yr.
ANN = 365.0

# Frozen multiple-testing denominator (CLAUDE.md sec.16 governance freeze).
# Recorded on the run so a later DSR computation has the denominator in-band.
N_TRIALS = 34

ENGINE_VERSION = "forward-collect-v1"

# A Sharpe from fewer than this many closed trades is not reported (stored as
# NULL). It is not a gate -- n_trades is always recorded so a reader can judge
# sufficiency -- it only refuses to publish a statistic that cannot exist.
MIN_TRADES_FOR_SHARPE = 2

#: The lane this module writes, and the only one it may write.
PROVENANCE = "live-forward"
RUN_KIND   = "forward"

#: Default destination. Owned HERE, not by the collector: `run_daily.py` is held
#: by a scope guard (test_provenance SC01) that forbids it from naming any
#: protected artifact, `ledger.db` included. The collector should not know where
#: governance storage lives -- the bridge does.
DEFAULT_LEDGER_PATH = _EV_LIB / "ledger.db"

#: Ledger objects this module reads or writes. Asserted, never created.
_REQUIRED_LEDGER_TABLES = ("runs",)
_REQUIRED_RUN_COLUMNS = (
    "run_id", "family_id", "run_ts", "provenance", "run_kind",
    "window_start", "window_end", "sharpe", "n_trades", "dsr_prob", "bh_p",
)

#: Forward-DB objects this module reads.
_REQUIRED_FORWARD_OBJECTS = ("trade_returns", "run_log")

_SyncOutcome = Literal["ok", "skipped", "no-evidence"]


class LedgerSchemaError(RuntimeError):
    """The ledger is absent, or its schema is not what this module requires.

    Raised instead of repairing. See the module docstring's SCHEMA POLICY.
    """


class ForwardSchemaError(RuntimeError):
    """evidence_forward.db is absent or lacks an object this module reads."""


# ---------------------------------------------------------------------------
# Schema assertions (never DDL)
# ---------------------------------------------------------------------------

def assert_ledger_schema(ledger_db: pathlib.Path) -> None:
    """Verify the ledger can receive a live-forward run. Never creates anything.

    Checks existence, then the `runs` table and every column this module writes
    through. A missing column means the ledger predates the lane schema (PR-5),
    and appending to it would either fail mid-write or silently drop the lane.
    """
    p = pathlib.Path(ledger_db)
    if not p.exists():
        raise LedgerSchemaError(
            f"ledger not found at {p}. ledger_sync asserts schema and never "
            "creates it -- run the documented migration sequence first "
            "(research/evidence_library/MIGRATIONS.md)."
        )
    con = sqlite3.connect(f"{p.resolve().as_uri()}?mode=ro", uri=True, timeout=10.0)
    try:
        names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        missing = [t for t in _REQUIRED_LEDGER_TABLES if t not in names]
        if missing:
            raise LedgerSchemaError(
                f"ledger {p} is missing required table(s): {missing}. "
                "Not creating them -- assert, never repair (ADR-P1-1)."
            )
        cols = {r[1] for r in con.execute("PRAGMA table_info(runs)")}
        missing_cols = [c for c in _REQUIRED_RUN_COLUMNS if c not in cols]
        if missing_cols:
            raise LedgerSchemaError(
                f"ledger {p} `runs` table is missing column(s): {missing_cols}. "
                "This ledger predates the lane schema (PR-5). Migrate it "
                "deliberately; ledger_sync will not ALTER it."
            )
    finally:
        con.close()


def assert_forward_schema(forward_db: pathlib.Path) -> None:
    """Verify evidence_forward.db exposes what this module reads."""
    p = pathlib.Path(forward_db)
    if not p.exists():
        raise ForwardSchemaError(f"forward evidence DB not found at {p}")
    con = _connect_ro(p)
    try:
        names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        missing = [o for o in _REQUIRED_FORWARD_OBJECTS if o not in names]
        if missing:
            raise ForwardSchemaError(
                f"forward DB {p} is missing: {missing}. `trade_returns` is the "
                "designated analysis surface; ledger_sync will not reconstruct it."
            )
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Forward-evidence readers (read-only; never writes evidence_forward.db)
# ---------------------------------------------------------------------------

def _connect_ro(db_path: pathlib.Path) -> sqlite3.Connection:
    """Open evidence_forward.db strictly read-only.

    ledger_sync is a reader of the forward DB. Opening read-only makes that
    structural rather than merely intended.
    """
    uri = pathlib.Path(db_path).resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=10.0)


def load_closed_trades(
    forward_db: pathlib.Path,
    signal_id: str,
    as_of: str,
) -> list[dict[str, Any]]:
    """Return every trade for `signal_id` closed on or before `as_of`.

    Reads the `trade_returns` VIEW, which is the schema's designated analysis
    surface: it joins entries to exits and derives gross/net return at query
    time (so a cost-assumption change can never corrupt stored history).

    Open trades are structurally absent from the view (no exit row -> no join),
    which is what makes this point-in-time correct.

    Ordered by (entry_date, entry_id) so the trade set -- and therefore every
    statistic derived from it -- is deterministic.
    """
    conn = _connect_ro(forward_db)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT entry_id, entry_date, exit_date, direction,
                   gross_return, net_return, cost_bps, hold_days_actual
            FROM trade_returns
            WHERE signal_id = ? AND exit_date <= ?
            ORDER BY entry_date, entry_id
            """,
            (signal_id, as_of),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _first_run_date(forward_db: pathlib.Path, signal_id: str) -> str | None:
    """Earliest ok run_date on record -- the true start of the forward
    observation window, which begins when collection begins, not when the first
    trade happens to open."""
    conn = _connect_ro(forward_db)
    try:
        row = conn.execute(
            "SELECT MIN(run_date) FROM run_log WHERE signal_id = ? AND status = 'ok'",
            (signal_id,),
        ).fetchone()
        return row[0] if row and row[0] else None
    finally:
        conn.close()


def _fn_hash(forward_db: pathlib.Path, signal_id: str, as_of: str) -> str | None:
    """fn_hash of the locked decision function as of the latest ok run.

    Carrying it onto the ledger run row ties the ledger evidence back to the
    exact decision-function bytes that produced it.
    """
    conn = _connect_ro(forward_db)
    try:
        row = conn.execute(
            "SELECT fn_hash FROM run_log "
            "WHERE signal_id = ? AND run_date <= ? AND status = 'ok' "
            "ORDER BY run_date DESC LIMIT 1",
            (signal_id, as_of),
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Metrics (pure functions of the closed-trade set)
# ---------------------------------------------------------------------------

def compute_forward_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarise closed forward trades. Pure; no I/O; deterministic.

    Sharpe is computed on per-trade NET returns and annualised by the observed
    trade frequency:

        periods_per_year = ANN / mean_hold_days
        sharpe           = mean(net) / stdev(net) * sqrt(periods_per_year)

    stdev is the SAMPLE standard deviation (ddof=1) -- with a handful of trades
    the population form would flatter the result.

    Returns sharpe=None (-> NULL in the ledger) when it cannot honestly exist:
    fewer than MIN_TRADES_FOR_SHARPE closed trades, zero dispersion, or a
    non-positive mean hold. n_trades is ALWAYS reported so a reader can see the
    sample size behind (or the absence of) the statistic.

    oos_sharpe / evidence_score / verdict are None by construction, each for a
    principled reason: forward evidence IS out-of-sample so there is no split;
    the Phase-B evidence score is a discovery construct with no forward meaning;
    a verdict is adjudicated by a human, never by a cron.
    """
    n = len(trades)
    out: dict[str, Any] = {
        "n_trades":       n,
        "sharpe":         None,
        "oos_sharpe":     None,   # forward evidence IS out-of-sample; no split
        "evidence_score": None,   # Phase-B discovery score; not defined forward
        "verdict":        None,   # adjudicated by a human, never by this job
        "mean_net_return":   None,
        "stdev_net_return":  None,
        "cum_net_return":    None,
        "win_rate":          None,
        "mean_hold_days":    None,
        "n_long":            sum(1 for t in trades if t["direction"] > 0),
        "n_short":           sum(1 for t in trades if t["direction"] < 0),
    }
    if n == 0:
        return out

    nets  = [float(t["net_return"]) for t in trades]
    holds = [float(t["hold_days_actual"]) for t in trades]

    mean_net  = sum(nets) / n
    mean_hold = sum(holds) / n

    cum = 1.0
    for r in nets:
        cum *= (1.0 + r)

    out["mean_net_return"] = mean_net
    out["cum_net_return"]  = cum - 1.0
    out["win_rate"]        = sum(1 for r in nets if r > 0) / n
    out["mean_hold_days"]  = mean_hold

    if n < MIN_TRADES_FOR_SHARPE:
        return out

    var   = sum((r - mean_net) ** 2 for r in nets) / (n - 1)   # ddof=1
    stdev = math.sqrt(var)
    out["stdev_net_return"] = stdev

    if stdev <= 0.0 or mean_hold <= 0.0:
        return out

    periods_per_year = ANN / mean_hold
    out["sharpe"] = (mean_net / stdev) * math.sqrt(periods_per_year)
    return out


# ---------------------------------------------------------------------------
# Ledger writer
# ---------------------------------------------------------------------------

def _existing_live_forward_run(
    ledger_db: pathlib.Path, family_id: str, as_of: str
) -> str | None:
    """run_id of an existing live-forward run for this family at this as_of.

    This is the idempotency key. The ledger is append-only, so a duplicate is
    prevented by not writing it, never by deleting one.
    """
    p = pathlib.Path(ledger_db)
    if not p.exists():
        return None
    con = sqlite3.connect(f"{p.resolve().as_uri()}?mode=ro", uri=True, timeout=10.0)
    try:
        row = con.execute(
            "SELECT run_id FROM runs "
            "WHERE family_id = ? AND provenance = ? AND window_end = ? "
            "LIMIT 1",
            (family_id, PROVENANCE, as_of),
        ).fetchone()
        return row[0] if row else None
    finally:
        con.close()


def _git_sha() -> str | None:
    """Short git SHA of the working tree, or None outside a repo."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_RESEARCH_ROOT),
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None
    except Exception:
        return None


def sync_family(
    family_id: str,
    as_of: str,
    forward_db: pathlib.Path,
    ledger_db: pathlib.Path,
) -> tuple[_SyncOutcome, str | None]:
    """Append one live-forward (L2) run row for `family_id` as of `as_of`.

    Returns (outcome, run_id):
      ('ok',          run_id)  -- a new live-forward run was appended
      ('skipped',     run_id)  -- one already existed for this as_of (idempotent)
      ('no-evidence', None)    -- no closed trades yet; NOTHING is written

    The 'no-evidence' case writes no row at all (Gate FB-1b decision 2). A row
    asserting live evidence for a family with zero closed trades would make
    `has_live_evidence` -- which answers "has a live row", not "has live
    evidence" -- report something untrue.

    Writes NOTHING except a single row in ledger.db `runs`. No state transition,
    no gate_result, no registry edit, and `governance=None` so dsr_prob / bh_p
    stay NULL.
    """
    assert_ledger_schema(ledger_db)
    assert_forward_schema(forward_db)

    existing = _existing_live_forward_run(ledger_db, family_id, as_of)
    if existing:
        log.info("[ledger_sync] %s: live-forward run already present for %s (skip)",
                 family_id, as_of)
        return "skipped", existing

    trades = load_closed_trades(forward_db, family_id, as_of)
    if not trades:
        log.info("[ledger_sync] %s: no closed forward trades as of %s "
                 "(nothing recorded -- a family with no closed trade has no "
                 "live-forward evidence)", family_id, as_of)
        return "no-evidence", None

    metrics = compute_forward_metrics(trades)

    window_start = _first_run_date(forward_db, family_id) or trades[0]["entry_date"]
    metrics["window_first_entry"] = trades[0]["entry_date"]
    metrics["window_last_exit"]   = trades[-1]["exit_date"]
    metrics["source"]             = "evidence_forward.db:trade_returns"
    metrics["closed_trades_only"] = True

    run_id = _record().append_run(
        ledger_db,
        family_id=family_id,
        metrics=metrics,
        provenance=PROVENANCE,
        run_kind=RUN_KIND,
        window_start=window_start,
        window_end=as_of,
        git_sha=_git_sha(),
        fn_hash=_fn_hash(forward_db, family_id, as_of),
        engine_version=ENGINE_VERSION,
        n_trials=N_TRIALS,
        # governance omitted on purpose -> dsr_prob / bh_p stay NULL.
        # check_validated_preconditions() hard-fails on NULL, so this run can
        # supply provenance without ever unlocking Validated on its own.
        governance=None,
    )
    sharpe = metrics["sharpe"]
    log.info(
        "[ledger_sync] %s: %s/%s run %s  n_trades=%d  sharpe=%s  window=%s..%s",
        family_id, PROVENANCE, RUN_KIND, run_id[:8], metrics["n_trades"],
        f"{sharpe:.3f}" if sharpe is not None else "n/a",
        window_start, as_of,
    )
    return "ok", run_id


def sync_all(
    family_ids: list[str],
    as_of: str,
    forward_db: pathlib.Path,
    ledger_db: pathlib.Path | None = None,
) -> dict[str, _SyncOutcome]:
    """Sync every family. Per-family isolation: one failure never blocks another.

    Mirrors the fault-isolation contract already used by run_daily.py (DQ3).
    """
    ledger_db = DEFAULT_LEDGER_PATH if ledger_db is None else ledger_db
    results: dict[str, _SyncOutcome] = {}
    for fid in family_ids:
        try:
            outcome, _ = sync_family(fid, as_of, forward_db, ledger_db)
            results[fid] = outcome
        except Exception as exc:  # noqa: BLE001 -- isolation boundary
            log.error("[ledger_sync] %s: sync failed: %s: %s",
                      fid, type(exc).__name__, exc, exc_info=True)
            results[fid] = "error"  # type: ignore[assignment]
    return results
