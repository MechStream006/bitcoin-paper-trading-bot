# Decision-Time Feature Contract — Governance Record (v3, frozen)

**Status: permanent governance record. Documentation-only — this record
changes no runtime semantics, no schema, and no code path.**

---

## 1. Decision

`decision_store.feature_contracts` (`research/feature_store/decision_store.py`,
backing `research/data/decision_features.db`) is the **authoritative record**
of the decision-time feature contract used by `capture_decision_features`
(`src/strategies/bridge.py`).

**`contract_registry` and `feature_store.registry` are NOT used for this
contract, and are not to be used for it going forward absent the trigger in
§8.** No `contract_id` has been minted in `contract_registry`. No
`FeatureDefinition`/`FeatureSpecification` has been registered in
`feature_store.registry` for these four features.

This decision was reached by evaluating `contract_registry`'s Data
Completeness scope, `feature_store.registry`'s corpus-wide Specification
grain, the existing `decision_store`/`outcome_store` sibling-store precedent,
and the absence of any consumer dependency, before this record was written.

---

## 2. Frozen feature set (contract v3)

Defined by `alignment.features.starter_features()`. Hashes are
`feature_store.decision_store.contract_hash(spec)`, full SHA-256, taken
verbatim from `tests/test_mirror_lifecycle.py::test_DF14_contract_hashes_are_unchanged_by_this_change`:

| Feature | Full contract hash |
|---|---|
| `spot_price` | `d0c3da2b9c736a45e96c6ba14ff6d29382c40c21823d536fb01798c5adeda691` |
| `option_atm_iv` | `545794bcf17f15b5f21318011f341e9c51100a2e9f39028148932f0fd5cd19a8` |
| `signal_position` | `6a2dcdb053c8f7eadcb1e9441513b034b4a90d8abd41e51c138dcee643d4d7dc` |
| `consumed_direction` | `e0c0af82039ecb977e547b215daa3442b92038187af9e96c0b4323c1d2389b79` |

Four features, four distinct hashes. Test DF14 pins these values and asserts
recomputation is stable; a change to any one of them is a CI failure, not a
silent drift.

---

## 3. Evolution history

- **v2** removed `option_oi_contracts` and `quote_spread_bps` (no consumer
  anywhere in the research tree; OI cannot be validated on testnet; execution
  cost is already measured elsewhere). Recorded in
  `alignment.features.REMOVED_IN_V2`, guard-tested.
- **v3** removed `market_close_1m` in commit `cfedd7e` (the Parquet lake
  refresh schedule made it structurally `INCOMPLETE_DATA` at every live
  decision, and it duplicated `spot_price`). Recorded in
  `alignment.features.REMOVED_IN_V3`, guard-tested. Removing it moved none of
  the other three hashes — each spec is hashed on its own.

No feature has been added or removed since `cfedd7e`.

---

## 4. Authority and resolution

- **Intended membership** is defined by `starter_features(signal_id)` —
  currently the four features in §2.
- **Canonical body and hash** for each feature are stored in
  `decision_store.feature_contracts`, keyed `(feature_name, contract_version)`
  with `(feature_name, contract_hash)` unique. `register_contract` is
  idempotent by hash; a `trg_feature_contracts_no_update` trigger makes a
  registered contract immutable.
- **Stored feature rows resolve by hash, never by "latest."**
  `decision_view/joined.py` looks up `feature_contracts` by
  `(feature_name, contract_hash)` and raises `UnregisteredContract` if a hash
  was never registered.

---

## 5. Change rule

1. An existing contract is **never edited in place** — `feature_contracts`
   enforces this at the database layer.
2. Any changed or new feature contract receives a **new hash**, which
   `register_contract` turns into the next per-feature ordinal version. Old
   rows keep citing their original hash and are unaffected.
3. A **membership change** (adding, removing, or renaming a feature) requires
   an explicit update to this governance record — a new dated section, not an
   edit that erases what this record originally said.

---

## 6. Evidence status

**This record freezes the specification. It does not claim that real
production feature rows or research evidence exist for it.**

- `decision_features.db` is gitignored, local-writable, and outside the
  governed evidence chain (`research/evidence_library`, `research/dq`,
  `research/forward_evidence`) — none of those reference `decision_store`,
  `starter_features`, or these hashes.
- Local rows in `decision_features.db`, including any written during
  development or testing, **must not be treated as evidence** of a
  production feature-capture history. `capture_decision_features` runs
  best-effort for ACKED live entries only; whether any row exists for a
  given decision is an operational fact, not something this record asserts.

---

## 7. Future trigger

If a sealed dataset, ADR-I010 §11 lifecycle promotion, or a governance gate
ever needs to **formally cite** this feature contract as evidence input —
the situation `feature_store.registry`'s Definition/Specification/Tier A/B
machinery exists for — reassess at that point whether registering these
features in `feature_store.registry`, or a `contract_registry` entry, is then
warranted. Until that trigger occurs, `decision_store.feature_contracts`
remains sufficient and `contract_registry`/`feature_store.registry` stay
untouched by this contract.

---

## 8. Known operational gap (not fixed here)

`decision_features.db` is not referenced by `scripts/backup.py` and has no
off-host or on-host backup coverage. The frozen contract bodies in this
record are independently reconstructable from git plus the DF14 pin, but any
captured feature rows in the live database are not currently covered by any
backup process. Logged as a follow-up; not scoped or fixed as part of this
record.
