# INC-0004 — `sys.path.insert(0, ...)` Shadowed the Stdlib `calendar` Module

| Field | Value |
|---|---|
| **Status** | Closed — root-caused, fixed, verified |
| **Severity** | Medium (would have broken `pandas` import for the rest of any process that called the affected function, but caught before shipping — never reached a merged test run or the real database) |
| **Discovered during** | Engineering roadmap Phase 2, WBS 2.7 (Conditioning Propagation Rule milestone), initial manual verification of `research/feature_store/conditioning.py`'s `_source_conditioning_map()` — before any pytest test was even written |
| **Discovered by** | Running the new function directly against real data as a first sanity check, per this session's own standing habit of manually exercising new code before writing formal tests |
| **Affected system** | `research/feature_store/conditioning.py` only (new file, this milestone) |
| **Author** | Claude Sonnet 5 (paired session), recorded per standing instruction: incidents are recorded when verification reveals a real engineering failure |
| **Date filed** | 2026-08-05 |

---

## Summary

`conditioning.py`'s `_source_conditioning_map()` needs to import the real ingest writers (`research/ingest/dvol.py`, `funding.py`, `binance_ohlcv.py`) to read their conditioning text. Following the exact `sys.path.insert(0, str(<dir>))` pattern used throughout this codebase for sibling-import access, the first implementation inserted `research/ingest/` at the **front** of `sys.path`.

`research/ingest/` contains a real, legitimate, unrelated module — `calendar.py` (the WI-5 macro event calendar) — that happens to share its name with Python's own stdlib `calendar` module. With `research/ingest/` at `sys.path[0]`, any subsequent bare `import calendar` anywhere in the process — including one buried inside `pandas`'s own stdlib usage (`_strptime.py`, itself imported transitively by `pandas._libs.tslibs`) — silently resolved to the wrong module. The result: importing `dvol.py` (which imports `pandas`) failed with `AttributeError: module 'calendar' has no attribute 'day_abbr'`, an error with no obvious connection to the actual root cause.

Caught immediately, before any test was written, by running the new function once against real data as a first manual check — not by a dedicated hunt.

---

## Timeline

1. **Implementation.** `_source_conditioning_map()` written with `sys.path.insert(0, str(_INGEST_DIR))`, matching this codebase's established sibling-import convention exactly (the same pattern `evidence_engine_bindings.py` and `migrate_evidence_engine_features.py` use for their own directories).
2. **First manual run — the trigger.** A standalone probe script calling `conditioning._source_conditioning_map()` failed with `AttributeError: module 'calendar' has no attribute 'day_abbr'`, deep inside `pandas`'s own import chain (`_strptime.py` → `pandas._libs.tslibs.fields` → `pandas._libs.tslibs.timedeltas` → ... → `pandas.__init__`).
3. **Reproduction check.** Confirmed `dvol.py` imports cleanly under the *existing* test suite (`tests/test_dvol_pilot_conformance.py`, 15/15 passed) — ruling out a pre-existing defect in `dvol.py` itself, and narrowing the cause to something specific to how the new probe script imported it.
4. **Root-cause investigation.** `find . -iname "calendar.py"` found exactly one match: `research/ingest/calendar.py` — a real WI-5 module, not a stray file. Confirmed `research/ingest/` was the only directory being inserted at `sys.path[0]` by any code path in the reproduction (`grep -rn "sys.path.insert(0.*ingest"` found only the one new line in `conditioning.py` itself — this is a defect novel to this milestone's own new file, not a pre-existing, latent risk anywhere else in the repository).
5. **Fix.** Changed `sys.path.insert(0, str(_INGEST_DIR))` to `sys.path.append(str(_INGEST_DIR))` — `research/ingest/` is now searched only *after* the stdlib and every other already-present path entry, so the real `calendar` module is always found first.
6. **Verification.** Re-ran the same probe script: `_source_conditioning_map()` now returns all four expected source→text mappings correctly, and `conditioning_for_specification()` correctly computes both single-input and multi-input "preserving" propagation against the real conditioning text.

---

## Root Cause

**`sys.path.insert(0, ...)` is not a safe default for sibling-import access when the target directory might contain a module whose name collides with something the *importing* code (or anything it transitively imports) also needs.** This codebase's established sibling-import convention (`sys.path.insert(0, str(<dir>))`, used throughout `research/feature_store/`, `research/forward_evidence/`, etc.) has always been safe in practice because none of those target directories previously contained a file named after a *stdlib* module. `research/ingest/calendar.py` is the first such case this session has encountered, and it was hit by the first new code that ever needed to import from `research/ingest/` via this pattern.

This is a different mechanism from `INC-0003`'s `sys.modules['engine']` collision (two research modules colliding with each other via a bare import), but the same *shape* of defect this session's incident record has now seen four times: a convention correct everywhere previously examined breaks the first time it meets a name it was never tested against.

---

## Detection

Not caught by:
- Code review of the new function in isolation — the `sys.path.insert(0, ...)` line is, read on its own, indistinguishable from every other sibling-import line in this codebase.
- Any existing test — nothing in the pre-existing suite imports `conditioning.py` (it did not exist before this milestone).

Caught by:
- Manually exercising the new function against real data, as a first sanity check, before writing any formal test — the same "run it for real before trusting it" habit that has caught every incident this session (`INC-0001` through `INC-0003`), applied here at an earlier stage (before a test even existed) rather than via a named Verification Standard step.

---

## Resolution

1. `sys.path.insert(0, str(_INGEST_DIR))` → `sys.path.append(str(_INGEST_DIR))` in `conditioning.py`'s `_source_conditioning_map()`. One line changed.
2. A code comment was added at the call site explaining exactly why `append` (not `insert(0, ...)`) is required here, naming the specific collision (`research/ingest/calendar.py` vs. stdlib `calendar`) and the specific downstream symptom (`pandas` import failure), so a future edit does not silently "simplify" this back to the more common `insert(0, ...)` pattern.

---

## Prevention

- **Applied in this milestone:** the specific fix above.
- **No Hazard Register entry filed.** Unlike `EH-002` (which had a second, real, currently-dormant instance elsewhere in the repository), this defect was novel, fully self-contained to the one new file this milestone introduced, found and fixed before it was ever exercised by any test or any other caller, and confirmed (by direct `grep`) to have no other instance anywhere in the repository. `EH-001`/`EH-002`'s own registration bar — "a pattern that recurs beyond the file(s) this milestone touched" — is not met.
- **Generalisable habit, recorded here for future milestones:** before adding a *new* `sys.path.insert(0, ...)` sibling-import against a directory this codebase has not previously imported from this way, check whether that directory contains a file whose name collides with a stdlib module (`python -c "import <name>"` before vs. after the insert is a fast, sufficient check). `sys.path.append(...)` is very slightly less convenient (a locally-shadowing sibling module would not be found ahead of an identically-named module already on `sys.path`) but is the safer default precisely because it cannot silently break the stdlib or a third-party package — the failure mode this incident actually hit.
