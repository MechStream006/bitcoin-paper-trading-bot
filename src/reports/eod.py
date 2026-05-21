"""ExpiryAnalyticsWriter — append-only JSONL telemetry for expiry-cycle events.

Writes three event types:
  posture_transition       — emitted on every posture change (0–5 per cycle)
  settlement_trigger_fired — emitted when emit_cycle_summary fires on_settlement_trigger
  cycle_summary            — emitted once per 24 h cycle by the expiry_cycle_eod
                             scheduler job at 12:05 UTC

Never raises from public methods.  Best-effort I/O: a failed write is logged
and discarded.  No read path; no DB dependency; no internal lock (file-append
atomicity is delegated to the OS).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_UTC = timezone.utc


class ExpiryAnalyticsWriter:
    """Append-only JSONL emitter for expiry cycle telemetry.

    Thread safety: append_event() opens the file for each write (open-for-append
    + write + close), which is atomic on most filesystems for small records.
    Concurrent callers are safe under normal OS guarantees.

    Args:
        output_path: Path to the JSONL file.
                     Parent directories are created on first write.
    """

    def __init__(self, output_path: Path | str) -> None:
        self._path = Path(output_path)

    # -----------------------------------------------------------------------
    # Low-level append
    # -----------------------------------------------------------------------

    def append_event(self, record: dict) -> None:
        """Append one JSON line to the JSONL file.  Never raises."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
        except Exception as exc:
            logger.warning(
                "[eod_writer] append failed (event=%s): %s: %s",
                record.get("event", "?"), type(exc).__name__, exc,
            )

    # -----------------------------------------------------------------------
    # Structured event emitters
    # -----------------------------------------------------------------------

    def posture_transition(
        self,
        *,
        prev_posture: str,
        new_posture: str,
        seconds_to_nearest: Optional[float],
        nearest_class: Optional[str],
        nearest_settlement_iso: Optional[str],
        cycle_fraction: float,
        observe_count: int,
    ) -> None:
        """Record a posture state change between two consecutive observe ticks."""
        self.append_event({
            "event": "posture_transition",
            "ts": datetime.now(_UTC).isoformat(),
            "prev_posture": prev_posture,
            "new_posture": new_posture,
            "seconds_to_nearest": seconds_to_nearest,
            "nearest_class": nearest_class,
            "nearest_settlement": nearest_settlement_iso,
            "cycle_fraction": round(cycle_fraction, 4),
            "observe_count": observe_count,
        })

    def settlement_trigger_fired(
        self,
        *,
        crossing_count: int,
        crossings: List[dict],
    ) -> None:
        """Record that emit_cycle_summary fired the on_settlement_trigger callback.

        Emitted once per settlement cycle when settlement crossings are present,
        immediately before cycle_summary.  Provides an audit trail distinguishing
        settlement-triggered chain refreshes from scheduled hourly refreshes.
        """
        self.append_event({
            "event": "settlement_trigger_fired",
            "ts": datetime.now(_UTC).isoformat(),
            "crossing_count": crossing_count,
            "crossings": crossings,
        })

    def cycle_summary(
        self,
        *,
        cycle_anchor_iso: str,
        posture_ticks: Dict[str, int],
        class_ticks: Dict[str, int],
        total_ticks: int,
        divergence_count: int,
        settlement_crossings: List[dict],
        final_snapshot_summary: dict,
    ) -> None:
        """Record end-of-cycle rollup.  Called by expiry_cycle_eod job at 12:05Z."""
        self.append_event({
            "event": "cycle_summary",
            "ts": datetime.now(_UTC).isoformat(),
            "cycle_anchor": cycle_anchor_iso,
            "posture_ticks": posture_ticks,
            "class_ticks": class_ticks,
            "total_observe_ticks": total_ticks,
            "divergence_count": divergence_count,
            "settlement_crossings": settlement_crossings,
            "final_snapshot": final_snapshot_summary,
        })
