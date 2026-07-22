"""Short-TTL result cache with single-flight (ADR-0001 §15.3).

Why this exists: `verify_governance()` replays receipts and `build_context()`
derives every family's state from the full transition ledger. Both are pure
reads, but neither is free. Without a cache, a polling client (or a Prometheus
scrape, or an operator refreshing a page) turns into repeated ledger reads
against the same file the daily writer uses -- which is exactly the contention
§15.3's "short-lived DB connections ... to avoid blocking the daily writer"
exists to prevent.

Single-flight matters as much as the TTL: N concurrent requests for a cold key
must produce ONE ledger read, not N. Without it, the cache would remove
steady-state load but leave the thundering herd on expiry, which is the moment
load is most likely to coincide.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class _Entry:
    value: Any
    expires_at: float


class TTLCache:
    """Thread-safe TTL cache. FastAPI runs sync endpoints in a threadpool, so
    concurrent access is the normal case, not an edge case."""

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()
        # One lock per key so a slow governance sweep never blocks an unrelated
        # forward-status read.
        self._key_locks: dict[str, threading.Lock] = {}

    def _key_lock(self, key: str) -> threading.Lock:
        with self._lock:
            return self._key_locks.setdefault(key, threading.Lock())

    def get_or_compute(self, key: str, compute: Callable[[], Any]) -> tuple[Any, bool]:
        """Return `(value, cache_hit)`, computing at most once across threads."""
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry.expires_at > now:
                return entry.value, True

        with self._key_lock(key):
            # Re-check: another thread may have populated this key while we
            # waited for the per-key lock. Without this second check the
            # single-flight winner's result would be recomputed by every
            # thread queued behind it.
            now = time.monotonic()
            with self._lock:
                entry = self._entries.get(key)
                if entry is not None and entry.expires_at > now:
                    return entry.value, True

            value = compute()
            with self._lock:
                self._entries[key] = _Entry(value, time.monotonic() + self._ttl)
            return value, False

    def invalidate(self) -> None:
        with self._lock:
            self._entries.clear()
