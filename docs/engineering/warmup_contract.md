# Warm-up Contract — WBS 2.6

| Field | Value |
|---|---|
| **Roadmap phase** | Phase 2 — Feature Store |
| **WBS item** | 2.6 |
| **Resolves** | ADR-I010 PS4 ("Warm-up requirements are implicit... insufficient warm-up degrades silently into fewer valid rows rather than a refusal") |
| **Governed by** | ADR-I010 §8.4 (contract term), §12.4 (resolution flowchart, Tier C), §13 (validation tiers) |
| **Code** | `research/feature_store/warmup.py`, wired into `research/feature_store/migrate_evidence_engine_features.py` |
| **Config** | `research/feature_store/warmup_policy.yaml` |
| **Date** | 2026-08-05 |

---

## 1. What this is

Every Feature Specification has always declared a `warmup_periods` field (mandatory since WBS 2.1's Tier A validation — `registry.py`'s `_validate_tier_a()` already refuses a Specification with no warm-up declared, or a negative one). What was missing, until this milestone, was **enforcement**: something that actually compares a Specification's declared warm-up against the history available at resolution time, and does something other than silently proceed when it's insufficient.

ADR-I010 §8.4 names the resolved behaviour precisely:

> A resolution whose available history is shorter than the declared warm-up is **refused**, not silently truncated... Warm-up is a property of the Specification, not of the caller's discipline.

`research/feature_store/warmup.py`'s `check_warmup()` is that enforcement. It is a small, pure, additive module — it does not modify `registry.py` (which already had the `warmup_periods` field) or `value_store.py` (whose own docstring already, deliberately, left this check to a future consumer — see §3 below).

## 2. Warm-up lifecycle

A Specification's warm-up requirement passes through three distinct stages, each already-existing except the third:

1. **Declaration (WBS 2.1, unchanged).** `registry.register_specification()`'s `warmup_periods` parameter is mandatory; Tier A refuses a missing or negative value. This is a property of the *Specification*, fixed at registration, immutable thereafter (Specifications are immutable once registered — ADR-I010 §8.2).
2. **Migration (WBS 2.4, unchanged).** `migrate_evidence_engine_features.py`'s declarative `FEATURES` table names each of the 17 migrated features' `warmup_periods`, cited against the exact `engine.py` constant or literal it is drawn from (e.g. `vol_regime`'s 273 = `RV_WINDOW(21) + REGIME_WINDOW(252)`).
3. **Enforcement (WBS 2.6, new).** At the point a Specification's binding would actually be resolved against a corpus, `warmup.check_warmup(spec_name, warmup_periods, available_periods, mode=...)` compares the declared requirement against `available_periods` (the corpus's actual row count) and acts according to the governed mode (§3).

There is no fourth stage yet where an *arbitrary* future consumer (the Dataset Builder, Phase 4) calls this check with an `as_of`-scoped history length rather than the full corpus length — that is out of this milestone's scope; see §6 Technical Debt in the verification record.

## 3. Where enforcement runs today, and why

`value_store.py`'s own docstring, written at WBS 2.2, already scoped this exactly: *"this module has no knowledge of admissibility floors or warm-up requirements... that is a consumer-facing policy concern... a future consumer... is expected to perform those checks before calling resolve() here."* This milestone does not revisit that decision — `value_store.resolve()` remains unchanged, and warm-up enforcement lives entirely on the caller's side, exactly where the architecture already said it belongs.

The only real "resolve()-shaped" consumer of Feature Specifications that exists in this codebase today is `migrate_evidence_engine_features.py`'s own Tier B equivalence check, which genuinely resolves every migrated feature's binding against the real corpus. `run()` now calls `check_warmup()` once per feature, immediately before that feature would be registered, using `len(engine.load_master())` as `available_periods` — the real corpus's actual row count. This is a deliberate, stated choice: it is the most concrete, verifiable enforcement point available right now, not a claim that the full Tier C resolution path (admissibility floor + lane + warm-up + corpus pinning + `as_of` closure, per ADR-I010 §13) is complete. Floor/lane enforcement is separate roadmap scope (WBS 4.4).

## 4. Refusal behaviour

`check_warmup()` is governed by a single `mode`, read from `warmup_policy.yaml` unless a caller passes one explicitly:

| Mode | Sufficient history | Insufficient history |
|---|---|---|
| `disabled` | Proceeds | **Proceeds silently** — today's pre-WBS-2.6 behaviour, unchanged. `WarmupCheckResult.action == "proceed"`, `sufficient == False`. |
| `warn` | Proceeds | **Proceeds, reported** — `action == "proceed_with_warning"`, never raises. `migrate_evidence_engine_features.py` prints a `WARNING:` line and registers/activates the feature normally. |
| `refuse` | Proceeds | **Refused** — raises `warmup.WarmupRefusal`. In the migration script, the offending feature is **skipped** (not registered — no Definition, no Specification written for it), reported with `phase == "Warmup-Refused"`, and the batch **continues** to the next feature. One refusal never aborts the whole run. |

"Sufficient" is `available_periods >= warmup_periods` — the boundary (`available_periods == warmup_periods`) counts as sufficient, matching "the declared warm-up" being exactly satisfiable, not requiring strictly more history than declared.

An explicitly-passed or explicitly-configured mode outside `{disabled, warn, refuse}` is always a hard error (`WarmupPolicyError`) — there is no silent fallback for a malformed configuration, only for a genuinely *absent* one (§5).

## 5. Activation conditions

**Current shipped state: `mode: warn`.** This is the rollout default named explicitly in the roadmap's own risk analysis (§16, R5): *"recommend running both [warm-up refusal and floor enforcement] in 'warn, don't refuse' mode for one full increment before flipping to hard refusal."* WBS 2.6 is that first increment. Shipping directly to `refuse` would be a real behaviour change with no staging — exactly the risk R5 names.

**What "warn mode" means in practice, today:** nothing observable changes. Every one of the 17 migrated features' declared `warmup_periods` (max 273, `vol_regime`) is far below the real corpus's 1987 rows, so `check_warmup()` reports `action == "proceed"` (not even `"proceed_with_warning"`) for all 17 in the current, real, unmodified corpus — confirmed by `TestAgainstRealLocalData::test_real_corpus_satisfies_every_declared_warmup_even_in_refuse_mode`, which runs the **hard-refuse** mode against real data and asserts zero refusals. Warn mode today is a no-op in observable terms; its value is that the *mechanism* is live, tested, and provably correct (via mocked-corpus tests and a manual TV3 exercise against the real script — see the WBS 2.6 verification record), ready to be flipped without further code change.

**Conditions for flipping `mode: warn` → `mode: refuse`** (a future, separate decision — not made by this milestone):

1. At least one full increment (this one) has run with `warn` mode live, per R5's own stated bar.
2. A review of any `WARNING:` lines actually logged during that period (none expected today, given §above, but this should be confirmed rather than assumed at flip time).
3. Explicit authorisation to flip the mode — this is a governed, deliberate action (edit `warmup_policy.yaml`'s `mode` key from `warn` to `refuse`), not something this milestone or any future milestone should do automatically.

## 6. Rollback conditions

Per the roadmap's own named lever (§14): *"Remove/comment the Specification's `warmup` requirement in `thresholds.yaml`-style config → Resolution falls back to best-effort truncation (today's silent behaviour) — a deliberate, logged regression, never the default."*

Concretely, for this implementation:

- **Full rollback to pre-WBS-2.6 behaviour:** set `mode: disabled` in `warmup_policy.yaml`, or delete the file entirely (`load_warmup_mode()` falls back to `"warn"` if the file is absent — **not** `"disabled"`; to fully silence the check, `disabled` must be set explicitly, not achieved by deleting the file). This requires **no code change** — the policy file is the entire rollback surface.
- **Code-level rollback** (if the mechanism itself needs to be removed, not just disabled): delete `research/feature_store/warmup.py` and `warmup_policy.yaml`; revert `migrate_evidence_engine_features.py`'s `run()`/`main()` to their pre-WBS-2.6 form (the diff is isolated to the `warmup` import, the per-feature `check_warmup()` call, the `--warmup-mode` CLI flag, and the `warmup_action` field on each result — no other function is touched).
- **No production data implication.** `feature_store.db`'s 17 Active Specifications are entirely unaffected by any rollback lever above — none of the 17 has ever been refused under any mode (real corpus always sufficient today), so no row needs to be un-registered or reverted as part of a rollback.

---

## Cross-references

- Verification record: `docs/engineering/verification/records/wbs-2.6-warmup-contract.md`
- Feature Migration Register: `docs/engineering/feature_migration_register.md` §6
- Roadmap: `docs/engineering/implementation_roadmap_v1.md` WBS 2.6, §14, §16 R5
- ADR: `docs/architecture/adr/ADR-I010-feature-store-architecture.md` §8.4, §12.4, §13
