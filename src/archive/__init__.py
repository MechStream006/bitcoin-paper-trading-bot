"""Cold-archive package (M1.2 — Archive Writer).

Implements the PENDING -> EXPORTED -> VERIFIED portion of the M1.1 v1.0
catalog state machine: read a hot-DB day partition, compute its
deterministic content_hash (M1.1 v1.0 sec.10, hash_spec_version=1), write
an immutable Parquet artifact + manifest, and self-verify the result.

Everything from VERIFIED onward (PUBLISHED, REPLICATED, PRUNED) belongs to
M1.3 (offsite driver) and M1.5 (sealer orchestration) and is out of scope
for this package.

Implementation Package I1 (Tasks T0-T7) is complete:
  - content_hash.py    -- canonical row encoder + digest (T1)
  - golden_vectors.py  -- frozen GV-0/1/2 test fixtures (T2, test-only)
  - partition_reader.py -- read-only hot-DB day-partition reader (T3)
  - parquet_writer.py  -- atomic, deterministic Parquet artifact writer (T4)
  - manifest.py        -- deterministic JSON sidecar manifest (T5)
  - catalog_port.py     -- CatalogPort dependency-inversion boundary (T6)
  - writer.py          -- ArchiveWriter orchestration (T7)

Architecture is frozen per M1.1-M1.5 v1.0. This package must not diverge
from the approved specifications; unfreeze only on implementation defect,
safety-invariant violation, governance conflict, or reproducibility
failure.
"""
