# Label Maturity & Standing Contract — WBS 3.5

| Field | Value |
|---|---|
| **Roadmap phase** | Phase 3 — Label Store |
| **WBS item** | 3.5 (this document's own scope). **3.9 will extend this document** with Tier D set-hygiene content (maturity fraction, horizon coverage, outcome-distribution disclosure, standing composition) once that milestone ships — do not treat this document as covering Tier D yet. |
| **Resolves** | ADR-I011 §10.1 ("Evidentiary standing is a date comparison... re-derived by every consumer, and can be re-derived differently"), §12 Tier B's standing-at-declaration half, LG3 |
| **Governed by** | ADR-I011 §10.1, §12 Tier B, §14 (Provenance), LG3 |
| **Code** | `research/label_store/standing.py`, `research/label_store/value_store.py`'s `standing` column + `STANDING_VALUES` + `set_standing()` + `declare_forward_label()`'s optional `standing` parameter |
| **Date** | 2026-08-06 |

---

## 1. What this is

Every Forward Label has always had a maturity lifecycle (`value_store.py`, WBS 3.2) but no evidentiary **standing** — the property ADR-I011 §10.1 requires: *"Standing is recorded on the label, never re-derived by a consumer... This converts the audited date-comparison into a property, so two consumers cannot reach different conclusions about the same label's standing."* WBS 3.4 wrapped 148 real H6 labels with the mechanism deliberately left standing-less (`value_store.py`'s own docstring, and that milestone's verification record §0.2, explain why: standing is WBS 3.5's named ownership, not 3.4's). This milestone closes that gap.

## 2. Standing lifecycle

Standing passes through two stages, distinct from the six-state maturity lifecycle it sits alongside (a label can be `Pending` or `Matured` independently of whether it is `Context` or `Forward evidence` — the two properties are orthogonal, per §14's Provenance table listing "Maturation instant" and "Standing" as separate rows):

1. **Resolution (new, WBS 3.5).** `standing.resolve_standing(subject_id, declared_at, declared_by)` — pure, deterministic, no database access — decides one of two outcomes:
   - **Bootstrap override.** `declared_by` carries the literal marker `"bootstrap"` (WBS 3.4's own `BOOTSTRAP_DECLARED_BY` convention) → **`Context`**, unconditionally. No boundary comparison is attempted; a label the Label Store is only now being told about, after its outcome is already known to the script writing it, cannot honestly be forward evidence regardless of what the underlying collector's own historical discipline was.
   - **Live resolution.** Otherwise, the label's owning signal is resolved from its `subject_id` (a documented prefix convention — only `h6-` is populated today), that signal's real pre-registration boundary is read fresh from its own JSON file in `research/forward_evidence/`, and `declared_at` is compared against it: before the boundary → `Context`; at or after → **`Forward evidence`**.
2. **Assignment (write-once, `value_store.py`).** Exactly one of two paths writes the resolved value:
   - **Inline, at declaration** (the correct path for live data, per LG3's literal "assigned at declaration, not later") — `standing.declare_with_resolved_standing()` resolves and passes `standing=` straight into `declare_forward_label()`'s new optional parameter, so the value is present from the row's very first `INSERT`.
   - **Backfill, for pre-existing bootstrap rows** (WBS 3.4's 148 H6 labels, which predate this column) — `standing.backfill_bootstrap_standing()` finds every `standing IS NULL` row and calls `value_store.set_standing()`, a dedicated, guarded setter performing the same `NULL -> value` transition the trigger allows for a first write. This is LG3-compliant because these rows never held a standing value at all — assigning one for the first time is not a reclassification of an existing one.

**Never returns or writes `Promotion evidence`.** That upgrade (§8.1: a Forward Label that has matured, is in an admissible lane, and is use-counted against a sealed decision, LG7) is Phase 4's own concern — the schema's `CHECK` constraint admits the value (so a future milestone can write it without another migration), but no function in `standing.py` or `value_store.py` today has any code path capable of producing it — verified structurally, not just asserted (see the verification record's TV5).

## 3. Where resolution runs today, and why

`ADR-I011`'s own §1.2 roadmap audit (`A-3`) claimed standing "is resolved via `research/forward_evidence/pre_reg_guard.py`, which already performs that lookup." Reading that module directly (this milestone) shows it does not — `verify_fn_hash()` reads a signal's pre-registration JSON but extracts only `function_spec.decision_function_hash`, for fn-hash integrity checking, and exposes no boundary-lookup function. The boundary value (`pre_registration_date`) does live in the same JSON files, confirmed by reading all six real files this session — `standing.py` reads them directly with its own small function, rather than importing or modifying `pre_reg_guard.py`, which remains exactly as WBS 3.1 found it.

The only real forward-tier data this repository holds today is WBS 3.4's 148 bootstrap-attested H6 rows, so the **live** resolution branch (boundary comparison) is exercised only by this milestone's own tests, using synthetic dates around H6's real, verified boundary (`2026-06-10T16:50:41.111245+00:00`) — not yet by any real declaration. This mirrors WBS 2.6's own `warmup.py` precedent exactly: the mechanism is live, tested, and provably correct, ready for a future live declaration to exercise it for real without further code change.

## 4. Refusal behaviour

| Condition | Behaviour |
|---|---|
| `subject_id` matches no known signal prefix | `standing.UnknownSubjectError` — refuses rather than guessing a default boundary (Ph6) |
| A caller passes an invalid `standing` string to `declare_forward_label()` or `set_standing()` | `value_store.LabelValueStoreError` — validated in Python before ever reaching the database |
| A direct `sqlite3` `UPDATE` attempts to change `standing` on a row where it is already set | `sqlite3.IntegrityError: LABEL_TAMPER: standing is assigned at declaration and is immutable thereafter (ADR-I011 LG3)` — the storage-layer trigger, the backstop for any write bypassing this module entirely |
| A direct `sqlite3` `UPDATE` attempts to set `standing` to a value outside `{Context, Forward evidence, Promotion evidence}` | `sqlite3.IntegrityError` from the `CHECK` constraint |
| `set_standing()` is called on a row that already has a standing value | `value_store.LabelTransitionError`, naming the existing value and citing LG3 |
| `set_standing()` is called on a `(spec_hash, subject_id, declared_at)` that has no declared row at all | `value_store.LabelValueStoreError` |

There is no "warn" mode for standing, unlike `warmup.py`'s three-mode policy — LG3 admits no conditional (mirroring the roadmap's own §10 exception for the fill-once trigger: *"A configurable switch that turns off falsification protection is a switch that will eventually be left off."*). Standing immutability is enforced unconditionally, from this milestone's first commit.

## 5. Activation conditions

**Already active for the only real data that exists.** Unlike `warmup.py`'s deliberate `warn`-mode staged rollout, this milestone's real backfill was run to completion this session: all 148 real H6 rows carry `standing = 'Context'`, verified directly against `research/label_store/label_store.db` and confirmed idempotent across three consecutive `--commit` runs. There is no staged activation here because there is no behaviour to stage — the trigger's write-once guarantee is unconditional by design (§4), and the only real forward-tier data in this repository was bootstrap-attested from the moment it was declared (WBS 3.4), so it was always going to resolve to `Context` regardless of when this milestone's backfill ran.

**Conditions for the live-resolution branch to matter for real** (a future event, not a decision this milestone makes): the first time any future milestone declares a genuinely live Forward Label (not bootstrap-attested), `standing.declare_with_resolved_standing()` must be the entry point used — declaring via bare `value_store.declare_forward_label()` without resolving standing first would leave that row's standing `NULL` indefinitely, since no automatic backfill exists for non-bootstrap rows (backfill is scoped, deliberately, to the bootstrap case only — see the verification record's Technical Debt table).

## 6. Rollback conditions

- **Backfill rollback.** There is no data-mutating rollback lever for the 148 already-backfilled rows — `standing`, once set, is permanently write-once by design (§4), matching the roadmap's own accepted exception for the fill-once trigger. This is a deliberate, accepted asymmetry: the lever is "do not run the backfill" (available only before the first `--commit`, already exercised), not "undo it."
- **Code-level rollback** (if the mechanism itself needs to be removed): delete `research/label_store/standing.py`; revert `value_store.py`'s three additive changes (`STANDING_VALUES` constant, the `standing` column + its `CHECK` constraint + the migration branch in `init_db()`, the guard-trigger clause, `declare_forward_label()`'s optional `standing` parameter, and the new `set_standing()` function). Every WBS 3.2/3.4 caller and test remains valid after this revert, since the additions were backward-compatible from the start (default `None`, confirmed by re-running both milestones' full test suites unchanged before and after this milestone's changes).
- **No production data implication.** All data touched by this milestone is the real local, gitignored `research/label_store/label_store.db` — not production. `research/data/forward_evidence.db` (H6) was never opened by `standing.py` at all, and its SHA-256 was confirmed byte-identical before and after all three real backfill runs.

---

## Cross-references

- Verification record: `docs/engineering/verification/records/wbs-3.5-evidentiary-standing.md`
- Roadmap: `docs/engineering/phase_3_label_store_roadmap.md` WBS 3.5, §1.2 `A-3`, §14.1
- ADR: `docs/architecture/adr/ADR-I011-label-store-architecture.md` §10.1, §12, §14, LG3
- Prior milestone: `docs/engineering/verification/records/wbs-3.4-h6-forward-label-migration.md` §0.2 (the binding instruction this milestone honours)
