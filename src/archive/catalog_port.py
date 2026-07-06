"""Catalog boundary for the Archive Writer -- interface only.

ArchiveWriter depends on the M1.1 v1.0 catalog.db only through the narrow
CatalogPort interface below: checking whether a partition is PENDING, and
recording its own EXPORTED/VERIFIED transitions. Full catalog ownership
(schema, PENDING-row seeding at day-rollover, PRUNED, revisions) belongs
to a separate implementation package (M1.1 catalog + M1.5 sealer, per the
approved M1 implementation order) -- this module intentionally does not
implement catalog behavior, only the interface this writer consumes.

TODO(T6): implement SqliteCatalogPort against the M1.1 v1.0 catalog.db
schema, backing this writer's own tests and Phase 0 shadow runs. The real
catalog package may replace or extend this reference implementation
without any change to writer.py, since writer.py only ever depends on the
CatalogPort protocol below.

No catalog behavior and no executable archive functionality is
implemented in this file yet.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from src.archive.manifest import Manifest


class CatalogPort(Protocol):
    """Narrow interface the Archive Writer needs from the M1.1 catalog."""

    def is_pending(self, partition_key: str) -> bool:
        ...

    def mark_exported(self, partition_key: str, manifest: "Manifest") -> None:
        ...

    def mark_verified(self, partition_key: str) -> None:
        ...
