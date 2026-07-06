"""Cold-archive package (M1.2 — Archive Writer).

Implements the PENDING -> EXPORTED -> VERIFIED portion of the M1.1 v1.0
catalog state machine: read a hot-DB day partition, compute its
deterministic content_hash (M1.1 v1.0 sec.10, hash_spec_version=1), write
an immutable Parquet artifact + manifest, and self-verify the result.

Everything from VERIFIED onward (PUBLISHED, REPLICATED, PRUNED) belongs to
M1.3 (offsite driver) and M1.5 (sealer orchestration) and is out of scope
for this package.

SKELETON ONLY (Implementation Package I1, Task T0). No module in this
package contains executable archive functionality yet. See the TODO
marker in each module for its owning implementation task (T1-T7).

Architecture is frozen per M1.1-M1.5 v1.0. This package must not diverge
from the approved specifications; unfreeze only on implementation defect,
safety-invariant violation, governance conflict, or reproducibility
failure.
"""
