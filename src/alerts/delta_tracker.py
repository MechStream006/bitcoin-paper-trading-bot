"""delta_tracker — correct per-interval deltas for monotonic counters.

Why this module exists
----------------------
The heartbeat reports cumulative-since-boot counters (WS messages, reconnects,
snapshot rows).  An operator wants "how many since the last heartbeat", but a
naive ``current - previous`` is wrong across the cases that actually happen in
production: the first heartbeat, a process restart (counters reset to 0), a
counter reset / reconnect-zeroing, a missed/failed heartbeat (baseline older
than one interval), and a transient read failure.

DeltaTracker computes deltas that are either CORRECT or reported as ``None``
(rendered "n/a") — never a misleading value.

Correctness contract
--------------------
A metric's delta is ``None`` (n/a) whenever ANY of these holds:
  - no baseline exists yet           (first heartbeat, or post-restart)
  - the current value is None         (counter could not be read this cycle)
  - current < baseline                (counter reset / restart / reconnect zero)
  - the metric was absent from the    (needs two consecutive clean reads before
    last committed baseline            a delta is trustworthy)

The baseline is committed ONLY by the caller, and ONLY on a successful heartbeat
send.  On commit, only successfully-read (non-None) values are stored and any
other metric is dropped from the baseline.  This guarantees every metric in the
baseline was read at the baseline timestamp, so a computed delta's window always
equals the reported ``elapsed`` — even when intervals were missed (the baseline
timestamp simply stretches; the delta still spans the true gap).

Threading
---------
All state is guarded by ``_lock``.  ``compute()`` never mutates; ``commit()`` is
the only mutator.  In the runtime both are called from the single scheduler
thread, but the lock makes concurrent reads safe regardless.
"""
from __future__ import annotations

import threading
from typing import Dict, Optional, Tuple


class DeltaTracker:
    """Track a baseline snapshot of integer counters and compute safe deltas."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._prev_values: Optional[Dict[str, int]] = None
        self._prev_at: Optional[float] = None

    def compute(
        self, current: Dict[str, Optional[int]], now: float
    ) -> Tuple[Dict[str, Optional[int]], Optional[float]]:
        """Return ``(deltas, elapsed)`` against the committed baseline.

        Does NOT mutate state.  ``deltas[name]`` is the per-interval increase, or
        None when it cannot be computed confidently (see the module contract).
        ``elapsed`` is seconds since the baseline timestamp, or None if there is
        no baseline yet.
        """
        with self._lock:
            prev_values = self._prev_values
            prev_at = self._prev_at

        elapsed = (now - prev_at) if prev_at is not None else None
        deltas: Dict[str, Optional[int]] = {}
        for name, value in current.items():
            prev = prev_values.get(name) if prev_values is not None else None
            if value is None or prev is None or value < prev:
                deltas[name] = None
            else:
                deltas[name] = value - prev
        return deltas, elapsed

    def commit(self, current: Dict[str, Optional[int]], now: float) -> None:
        """Advance the baseline.  Call ONLY after a successful heartbeat send.

        Stores only successfully-read (non-None) values; metrics that were None
        this cycle are dropped from the baseline so the next cycle reports n/a
        for them until two consecutive clean reads exist.
        """
        good = {k: v for k, v in current.items() if v is not None}
        with self._lock:
            self._prev_values = good
            self._prev_at = now

    def has_baseline(self) -> bool:
        with self._lock:
            return self._prev_values is not None


# ---------------------------------------------------------------------------
# Self-test / smoke
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    t = DeltaTracker()

    # First cycle: no baseline -> all n/a, elapsed None.
    cur = {"msgs": 100, "reconnects": 0, "rows": 30}
    d, e = t.compute(cur, now=1000.0)
    assert d == {"msgs": None, "reconnects": None, "rows": None}, d
    assert e is None
    t.commit(cur, now=1000.0)
    print("[t1] first cycle -> all n/a:", d)

    # Second cycle, normal increase -> correct deltas, elapsed = 30s.
    cur = {"msgs": 640, "reconnects": 0, "rows": 60}
    d, e = t.compute(cur, now=1030.0)
    assert d == {"msgs": 540, "reconnects": 0, "rows": 30}, d
    assert e == 30.0
    t.commit(cur, now=1030.0)
    print("[t2] normal deltas:", d, "elapsed", e)

    # Counter reset (msgs dropped) -> msgs n/a, others fine.
    cur = {"msgs": 5, "reconnects": 1, "rows": 90}
    d, e = t.compute(cur, now=1060.0)
    assert d["msgs"] is None, d
    assert d["reconnects"] == 1 and d["rows"] == 30, d
    t.commit(cur, now=1060.0)
    print("[t3] counter reset -> msgs n/a:", d)

    # Missed interval: compute against committed baseline at a later time.
    # (commit at 1060 was the last; no commit since.)  Window stretches.
    cur = {"msgs": 5 + 1200, "reconnects": 1, "rows": 90 + 60}
    d, e = t.compute(cur, now=1660.0)   # 600s later
    assert d == {"msgs": 1200, "reconnects": 0, "rows": 60}, d
    assert e == 600.0, e
    print("[t4] missed interval -> delta spans true window:", d, "elapsed", e)

    # Read failure: rows None this cycle -> rows n/a; not committed.
    cur = {"msgs": 1205, "reconnects": 1, "rows": None}
    d, e = t.compute(cur, now=1665.0)
    assert d["rows"] is None, d
    t.commit(cur, now=1665.0)            # rows dropped from baseline
    d2, _ = t.compute({"msgs": 1210, "reconnects": 1, "rows": 200}, now=1670.0)
    assert d2["rows"] is None, d2        # needs two clean reads -> still n/a
    assert d2["msgs"] == 5, d2
    print("[t5] read failure -> rows n/a until two clean reads:", d, "then", d2)

    # Process restart: a fresh tracker has no baseline -> n/a.
    t2 = DeltaTracker()
    d, e = t2.compute({"msgs": 50, "reconnects": 0, "rows": 5}, now=2000.0)
    assert all(v is None for v in d.values()) and e is None
    assert not t2.has_baseline()
    print("[t6] process restart -> n/a:", d)

    print("\ndelta_tracker.py smoke test passed.")
