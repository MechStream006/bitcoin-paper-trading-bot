# Owner Decision Pack — 2026-08-08

| Field | Value |
|---|---|
| **Subject** | `DS-DEC-006`, `T-2`, `T-4`, `T-5`, `T-6`, and the 146 RC items across the 14 Phase 4 WBS verification records |
| **Prepared by** | Claude Opus 5, as a focused reconciliation (2026-08-08) |
| **Decided by** | Rohith Suresh |
| **Status** | ✅ **G1 CLOSED 2026-08-08 — `OD-1` through `OD-9` all decided; all 146 RC items across 14 records signed** (see §10) |
| **Nature** | Group-level governance acceptance (`OD-1`–`OD-9`) plus, in the same sitting, the actual per-item sign-off it authorized. §1–§9 record the acceptances; §10 records the signing and its adjudication |

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

## OD-9 — `4.6/RC10`: `BASELINE_46.json` RETAINED (decided 2026-08-08, second sitting)

**RETAIN. Do not remove or modify.**

> `4.6/RC10` — RETAINED as committed, load-bearing evidence. Confirmed
> git-tracked and read by five test modules (`test_experiments.py`,
> `test_lanes.py`, `test_materialisation.py`, `test_sealed_regions.py` —
> which hard-asserts its existence — and `test_training.py`), four of which
> postdate WBS 4.6 and independently chose to reuse this baseline rather than
> create their own. Actively repaired, not removed, at `852987e`. Removal is
> not a documentation action; it would require rewriting assertions in all
> five files, an unscoped engineering task not decided here.

---

## 9. Reconciliation — what is and is not closed by this record

| | Before this record | After this record |
|---|---|---|
| `T-2`, `T-4`, `T-5`, `T-6` | Unratified | ✅ **Ratified** (OD-1) |
| `DS-DEC-006` | Owed, untouched | ✅ **Formally deferred** (OD-2) — substance still undecided, by design |
| 55 decision-bearing RC items | Not accepted | ✅ **Accepted**, group-by-group (OD-4–OD-8), one (Group C) with a binding condition |
| 91 verifiable + Groups F/G/H-minus-RC3 (114 items) | Not confirmed | ✅ **Confirmed in bulk** (OD-3) |
| **146 individual RC checkboxes / signature lines** | 0 signed | ✅ **146/146 signed 2026-08-08** — see §10 |
| `TD-4.4-3` | Open debt | **Still open** — carries a binding Phase 5 entry pre-condition (`OD-6`), attached specifically to `4.10/RC10` at sign-off |
| Governance receipt chain | 27, `ok=True` | **27, `ok=True` — unchanged** |

**`G4` and `G1` are both now closed.** This pack's acceptances (`OD-1`–`OD-9`)
were carried into all 14 records' actual signature lines on 2026-08-08 — see
§10 for the adjudication of the three items that needed correction or
clarification before signing could proceed honestly.

---

## 10. G1 CLOSED — 2026-08-08 — sign-off adjudication and confirmation

Before any checkbox was touched, three items surfaced during signing
preparation that this pack's original group-level language did not cleanly
resolve. Each was adjudicated against primary source — the actual RC wording,
the TD register, and the roadmap — not assumed, and none was resolved by
stretching a reading to force closure.

**`4.4/RC9` vs `4.10/RC10` — corrected, not identical.** `HZ-4.4-1` (`4.4/RC9`)
and `TD-4.4-3` are different defects: `HZ-4.4-1` is about whether a Tier D cap
survives into the sealed record (fixed and verified at `4.5/RC9` — "the cap
survives the seal, proven by sabotage"); `TD-4.4-3` is about the meet being
caller-supplied with nothing deriving it. Only `TD-4.10-1` (`4.10/RC10`) ties
to `TD-4.4-3` — its own wording says so directly: *"the fix belongs with
`TD-4.4-3`."* Signed accordingly: `4.4/RC9` under `OD-3` (mechanical, no
condition), `4.10/RC10` under `OD-6` (binding condition attached).

**`4.6/RC10` — resolved by `OD-9` above**, RETAIN, decided the same sitting as
this closure.

**`4.10/RC7` — signed with a contemporaneous note, record not amended.** The
RC item's structural claim ("`T-4` is not an entry condition for 4.10") is
unaffected by `T-4`'s ratification — `4.10` never named `T-4` as a gate the
way `4.6` named `T-3`/`DS-DEC-004`. The historical phrase "proceeding without
ratification" describes what happened, not a present-tense claim. A note was
appended at the RC7 line; the original §1 text was not rewritten, per this
project's standing convention (historical records are annotated, not edited).

**The 11 items mapped "by content" in the prior adjudication turn were
re-verified against their actual source sections** (`wbs-4.3` §HZ table,
`wbs-4.6` §2, `wbs-4.9` §2/§3, `wbs-4.10` §2/§4) before signing. All 9 not
already covered above confirmed legitimately within their assigned `OD`'s
character — no separate owner decision was required for any of them. Two
minor double-namings in the original Group A–H prose (`4.9/RC11` in both
Group B and Group G; `TD-4.11-1`/`4.11/RC9` in both Group D and Group G) carry
the same no-condition treatment under either reading, so the duplication does
not affect any signature.

**Result: all 146 RC items across all 14 records signed** — `Reviewer: Rohith
Suresh`, `Date: 2026-08-08`, using the sign-off mechanism already defined by
the records (the same identity/date convention as the ten Phase 3 records
signed 2026-08-07). No signature identity was invented. No code, schema,
production, or research data changed. No governance receipt was created — a
signature is not in `receipts.DECISION_TAXONOMY`, and none was added to it.
