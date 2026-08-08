# Owner Decision Pack — 2026-08-08

| Field | Value |
|---|---|
| **Subject** | `DS-DEC-006`, `T-2`, `T-4`, `T-5`, `T-6`, and the 146 RC items across the 14 Phase 4 WBS verification records |
| **Prepared by** | Claude Opus 5, as a focused reconciliation (2026-08-08) |
| **Decided by** | Rohith Suresh |
| **Status** | ✅ **DECIDED 2026-08-08 — OD-1 through OD-8, all ACCEPTED (OD-6 with a binding condition)** |
| **Nature** | Group-level governance acceptance of the *readings and scope transfers* described below. **This is not the same act as signing the 146 individual RC checklist items** — no RC item's checkbox or signature line was touched by this record. See §9 |

---

## What this record does not do

It does not sign any RC item. It does not modify code, schema, production, or
research data. It does not open Phase 5. It does not implement `TD-4.4-3`. It
does not create a governance-receipt-ledger entry — no entry in
`receipts.DECISION_TAXONOMY` covers a roadmap-wording or scope-transfer
decision, and inventing one here would be inventing governance, which is what
`AF-2`'s own rationale exists to forbid repeating. The chain remains
27 receipts, `ok=True`, unchanged by this record.

---

## OD-1 — `T-2`, `T-4`, `T-5`, `T-6` ratified as scope transfers

**ACCEPTED.**

> `T-2`, `T-4`, `T-5`, `T-6` — RATIFIED as scope transfers, as recommended in
> `phase_4_dataset_builder_roadmap.md` §3. `DS-DEC-003` (corpus version,
> `FS-GAP-005`) remains separately deferred; the do-not-invent instruction
> continues to stand and is not addressed by this ratification.

## OD-2 — `DS-DEC-006` formally deferred

**ACCEPTED.**

> `DS-DEC-006` — DEFERRED. Confirmed out of Phase 4 scope per `ADR-I012` §18
> R9. No Phase 4 action required. Revisit when historical-run-to-dataset
> citation is designed as part of wiring the Dataset Builder into an
> evaluation pipeline — not before.

## OD-3 — 114 mechanical RC items bulk-confirmed

**ACCEPTED.**

> The 91 fact-verifiable RC items, Group F (4), Group G (14), and Group H
> minus `4.0-D0a/RC3` (5) — 114 items total — are ACCEPTED in bulk as
> confirmed by the evidence already recorded per-item. This does not close
> any technical-debt entry; Group G items remain open in the TD register.

## OD-4 — Group A accepted: governed doorway, not a wall (4 items)

**ACCEPTED.**

> Group A (`4.6/RC9`, `4.7/RC10`, RF2/RF3 scope items) — ACCEPTED. The
> Dataset Builder is confirmed as a governed doorway for declarations that
> pass through it, not a wall against all corpus access.

## OD-5 — Group B accepted: recorded but unalerted (5 items)

**ACCEPTED.**

> Group B (`4.8/RC10`, `4.9/RC11`, `4.10` holdout, `4.11/RC13`, divergence
> item) — ACCEPTED as an accurate description of current behaviour:
> governance events are queryable but unalerted. Governance alerting remains
> a separate, unowned, undecided work item.

## OD-6 — Group C accepted, with a binding condition (3 items)

**ACCEPTED WITH BINDING CONDITION.**

> Group C (`4.4/RC9`, `4.10/RC10`, `TD-4.4-3`) — ACCEPTED as documented, on
> the explicit condition that `TD-4.4-3` (admissibility computed, not merely
> caller-supplied) is resolved **before any Phase 5 code path calls the
> Dataset Builder**. This condition is binding, not advisory, and is a
> **Phase 5 entry pre-condition** from this point forward.

`TD-4.4-3` is **not implemented by this record** — the condition only takes
effect at the point something tries to invoke the Dataset Builder from a
Phase 5 code path. Propagated into the TD register (§ below) so it is not
lost between now and then.

## OD-7 — Groups D+E ratified as authoritative interpretation (19 items)

**ACCEPTED.**

> Groups D and E (19 items: `4.2/RC7`; `4.3/RC7,8`; `4.4/RC7,8`; `4.5/RC8`;
> `4.6/RC8`; `4.7/RC7,8`; `4.8/RC7,8`; `4.9/RC9`; `4.11/RC9`; `4.1/RC7,8`;
> `4.2/RC8`; `4.5/RC7,10`; `4.11/RC7,8`) — ACCEPTED as the platform's
> authoritative interpretation of each named ambiguity. Includes explicit
> acknowledgment that `research/dataset_builder/registry.py`'s specifications
> table is not append-only (`4.11/RC7`), contrary to `ADR-I012` §16's framing.

## OD-8 — `4.0-D0a/RC3` accepted as a `DS-DEC-002` consequence (1 item)

**ACCEPTED.**

> `4.0-D0a/RC3` — ACCEPTED. Confirmed as the correct, already-ratified
> consequence of `DS-DEC-002`: corpus provenance is "canonical as re-derived
> by this platform," not "as originally received from the exchange."

---

## 9. Reconciliation — what is and is not closed by this record

| | Before this record | After this record |
|---|---|---|
| `T-2`, `T-4`, `T-5`, `T-6` | Unratified | ✅ **Ratified** (OD-1) |
| `DS-DEC-006` | Owed, untouched | ✅ **Formally deferred** (OD-2) — substance still undecided, by design |
| 55 decision-bearing RC items | Not accepted | ✅ **Accepted**, group-by-group (OD-4–OD-8), one (Group C) with a binding condition |
| 91 verifiable + Groups F/G/H-minus-RC3 (114 items) | Not confirmed | ✅ **Confirmed in bulk** (OD-3) |
| **146 individual RC checkboxes / signature lines** | 0 signed | **Still 0 signed** — group-level acceptance is not per-item signature. `G1` / `E1` remain open |
| `TD-4.4-3` | Open debt | **Still open** — now carries a binding Phase 5 entry pre-condition (OD-6) |
| Governance receipt chain | 27, `ok=True` | **27, `ok=True` — unchanged** |

**What actually closes as a Phase 4 governance blocker:** `G4` (`DS-DEC-006`,
`T-2`, `T-4`, `T-5`, `T-6`) — all five are now decided (ratified or formally
deferred). **What remains:** `G1` — 146 RC items still require your individual
signature per record, even though this pack records what you'd be signing
against.
