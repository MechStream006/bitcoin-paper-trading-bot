"""Catalog boundary for the Archive Writer -- interface only.

ArchiveWriter (T7) depends on the M1.1 v1.0 catalog.db only through the
narrow CatalogPort interface below: checking whether a partition is
PENDING, and recording its own EXPORTED/VERIFIED transitions. Full
catalog ownership (schema, PENDING-row seeding at day-rollover, PRUNED,
revisions) belongs to a separate implementation package (M1.1 catalog +
M1.5 sealer, per the approved M1 implementation order).

This module intentionally contains no catalog backend of any kind -- not
SQLite-backed, not in-memory, not anything else. It defines only the
interface shape and the boundary-enforcement helper (ensure_catalog_port)
that lets a caller fail closed if something not shaped like a
CatalogPort is handed to the Archive Writer. A concrete implementation
(SQLite-backed, per M1.1's catalog.db schema) is out of scope here and
belongs to that separate implementation package; this module must never
import sqlite3 or contain any catalog persistence logic, so the Archive
Writer's dependency on "a catalog" stays purely structural (dependency
inversion) rather than a dependency on any particular backend.

Behavioral contract for implementers (not enforceable by the Protocol
itself, since Python protocols check shape, not behavior -- documented
here for whoever writes the real backend):
  - is_pending(key) must return False (never raise) for any partition_key
    it does not recognize as PENDING -- fail-closed, not fail-open. A
    missing/unknown partition must never be silently treated as
    exportable.
  - mark_exported(key, manifest) should only be callable for a partition
    currently PENDING; calling it for a partition not PENDING is a
    caller error the backend is expected to reject (e.g. by raising).
  - mark_verified(key) should only be callable for a partition currently
    EXPORTED; calling it before mark_exported is a caller error the
    backend is expected to reject.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from src.archive.manifest import Manifest


@runtime_checkable
class CatalogPort(Protocol):
    """Narrow interface the Archive Writer needs from the M1.1 catalog.

    Decorated @runtime_checkable so `isinstance(obj, CatalogPort)` is a
    valid structural check (presence of the three named methods). For a
    stricter check (methods present AND callable), use
    ensure_catalog_port() below rather than bare isinstance.
    """

    def is_pending(self, partition_key: str) -> bool:
        ...

    def mark_exported(self, partition_key: str, manifest: "Manifest") -> None:
        ...

    def mark_verified(self, partition_key: str) -> None:
        ...


_REQUIRED_METHODS = ("is_pending", "mark_exported", "mark_verified")


def ensure_catalog_port(candidate: object) -> CatalogPort:
    """Fail closed unless `candidate` structurally satisfies CatalogPort.

    This is the integration point: the Archive Writer (T7) is expected to
    call this once, at construction time, against whatever catalog object
    it is given. Checking here -- explicitly, with a clear error -- means
    a malformed or incomplete catalog implementation is rejected
    immediately, rather than failing later with a confusing AttributeError
    partway through an export.

    Stricter than a bare `isinstance(candidate, CatalogPort)` check: this
    also verifies each required member is callable, not merely present
    (a runtime_checkable Protocol's isinstance check only checks
    attribute presence via hasattr, so e.g. `candidate.is_pending = True`
    would satisfy isinstance but must not satisfy this function).

    Returns `candidate` unchanged (narrowed to CatalogPort) so this can be
    used inline, e.g. `self._catalog = ensure_catalog_port(catalog)`.

    Raises:
        TypeError: `candidate` is missing one or more required methods,
            or one of them is present but not callable.
    """
    missing = [
        name for name in _REQUIRED_METHODS
        if not callable(getattr(candidate, name, None))
    ]
    if missing:
        raise TypeError(
            f"{candidate!r} does not satisfy CatalogPort: "
            f"missing or non-callable method(s): {', '.join(missing)}"
        )
    return candidate  # type: ignore[return-value]
