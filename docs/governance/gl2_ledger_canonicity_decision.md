# GL-2 — Governance Ledger Canonicity Decision

**Date: 2026-08-28 (UTC) · Decision: OPTION A — the PRODUCTION ledger is canonical**

Governance decision only. **No ledger was modified, rebuilt, copied, reseeded,
truncated, updated, deleted or appended to at this gate.** Production, the
registry, `thresholds.yaml`, the sealed lockbox and every sealed artifact are
untouched. Evidence base: the GL-1 read-only forensic audit (2026-08-28).

The canonical governance ledger is:

```
/opt/btcbot/research/evidence_library/ledger.db      (production host 3.110.103.217)
```

---

## 1. The repository contradiction this decision resolves

Two committed documents assert **different** canonical ledgers. Both are quoted
verbatim; neither was ambiguous, and neither could be dismissed as loose wording.

| Source | Assertion |
|---|---|
| `docs/ops/production-migration-runbook-increment5.md:11` | *"This is a one-time production operation against the **canonical ledger**."* — the operation targeted the production host's `ledger.db` |
| `docs/ops/bk3-activation-plan.md:214` | *"expected receipt count (ledger **27 canonical** / 26 production, feature 17, label 8)"* — 27 is the **local** receipt count; production is named as the other thing |

**This decision makes `production-migration-runbook-increment5.md` correct and
`bk3-activation-plan.md:214` wrong.** That line must be corrected; the
correction is scoped to GL-3 and is not made here, because this gate writes no
file other than this record.

Neither document is at fault for the divergence itself. `ledger.db` is
`.gitignore`d (`.gitignore:36`) and untracked, so git has never reconciled the
two copies and never could. The contradiction is a symptom of that, not a
cause.

## 2. The state being decided over

| | Local (`research/evidence_library/ledger.db`) | Production |
|---|---|---|
| runs | 70 | **71** |
| gate_results | 142 | **145** |
| receipts | **27** | 26 |
| state_transitions | 13 (9 →candidate, 4 →disqualified) | 13 (identical) |
| lanes | L0 35 / L1 35 | L0 35 / **L1 36** |
| `verify_chain()` | `ok=True, n=27` | `ok=True, n=26` |
| receipt `decided_at` span | 2026-07-13 → 2026-08-08 | **2026-08-07T02:38:27–:28** (one 0.5 s burst) |
| receipt_id overlap | **zero — the chains are wholly disjoint** | |
| `registry.yaml` | identical on both | identical on both |
| non-offline runs / `has_live_evidence` / `pairwise_r` | 0 / 0 / 0 | 0 / 0 / 0 |

Both chains verify. **Neither ledger is corrupt.** This is a provenance
decision, not a repair.

## 3. Why production is selected

**3.1 It is the ledger the platform actually consults.** `run_daily.py` runs
`report.py --check` against the production ledger before every scheduled
collection. It is the copy that gates real evidence being written. A canonical
ledger that no runtime reads is canonical in name only.

**3.2 It is the artifact the durability program protects and has proven
recoverable.** `btcbot-ledger-backup.service` ships the production ledger
off-host daily. The BK2 drill of 2026-08-12 retrieved a real production
ciphertext onto a clean host and passed both gates — `verify_chain()`
`ok=True, n_receipts=26` and `report.py --check` exit 0. **26 is the production
count.** BK1 calls the ledger "the only truly irreplaceable artifact"; the
artifact that was actually made irreplaceable is production's.

**3.3 Its `ivrv_vrp_spread` record is demonstrably the more faithful.**
Production holds the natural pair every other family has — a discovery run
followed by a confirmatory run:

| run_id | run_kind | run_ts |
|---|---|---|
| `efbe6acb-3d2c-4bca-8bb2-591c1fdfcda6` | discovery | `2026-06-19T14:56:16.635687+00:00` |
| `b98c9685-134b-410f-ac98-684b0ee01da6` | confirmatory | `2026-06-19T14:59:22.312707+00:00` |

Local has only the second, and holds it with a **reconstructed** timestamp
(§6). The 3-minute discovery→confirmatory interval on production is the shape
of a real session; local's single row is the shape of a repair.

**3.4 The Increment-5 migration — the one irreversible governance operation
this platform has performed on a ledger — was executed against production, by
a runbook that called it canonical.** Selecting local would retroactively
declare that operation to have been performed against a non-canonical copy.

## 4. Why local is not selected

- Its `b98c9685` row carries a **timestamp its own repair script admits is a
  guess** (§6), and a `run_kind` that disagrees with production.
- It is **missing `efbe6acb` entirely** — a legitimate, well-formed discovery
  run with three complete `gate_results`.
- Nothing reads it at collection time; no scheduled job gates on it.
- It has no off-host backup and no proven recovery path.

Local's one genuine advantage — it holds the `dataset.seal` act (§5) — is
addressed by re-attestation rather than by inverting the decision.

## 5. Treatment of the legitimate local-only `dataset.seal`

This receipt is **legitimate and must not be discarded as noise.**

| Field | Value |
|---|---|
| `receipt_id` | `871a4e54b5ab144f99637dc31bf11d11168a068b757e03b31a35d666900c6378` |
| `seq` / `prev_receipt_hash` | 27 / `7698c56866404cf48d3d66cdafa31f834310e75d021f8e6d7e947a97c08b616c` |
| `decision_type` / class / action | `dataset.seal` / `discretionary` / `seal` |
| subject | `dataset` `f444c132b2a5d666b9946e08e204b46720690fb79928892a066dce333d245444` |
| actor | `human` / `rohith` |
| authority | `policy_id: ADR-I012-DG-1`, `policy_hash 72cf2f17dc8ea497…` |
| `decided_at` | `2026-08-08T11:57:35.797294+00:00` |

`dataset.seal` is a reserved `discretionary` entry in `DECISION_TAXONOMY`,
added with the explicit rationale *"ADR-I012 DG-1: Sealing is a human act
emitting a receipt (IQROS-P8, P10). No existing verb described sealing."* The
receipt is well-formed, correctly chained, human-actored and policy-referenced.

It post-dates production's ledger mtime (`2026-08-07T02:54:29Z`) by one day, so
production never had the opportunity to receive it. Its absence there is an
accident of timing, not a judgement.

**Ruling: the sealing ACT stands; its RECEIPT cannot be transplanted.** Moving
the receipt into production's chain is impossible without rewriting every
`receipt_id` after it (§7). It must therefore be **re-attested into the
canonical ledger as a new receipt bearing its own, later `decided_at`**, whose
body records that it re-attests an act originally performed at
`2026-08-08T11:57:35.797294+00:00` and originally receipted as
`871a4e54b5ab144f…` in the superseded local chain.

**It must never be presented as the historical original.** A re-attestation is
a new governance act that points at an older one. Writing it with a
back-dated `decided_at`, or omitting the pointer, would be fabricating history —
which is precisely the failure this platform's receipt architecture exists to
prevent.

## 6. Treatment of local's reconstructed `b98c9685`

The same `run_id` carries different data in the two ledgers. This is a
**primary-key content conflict, not a missing row**:

| Field | Local | Production |
|---|---|---|
| `run_ts` | `2026-06-21T14:08:41+00:00` | `2026-06-19T14:59:22.312707+00:00` |
| `run_kind` | `discovery` | `confirmatory` |
| sharpe / oos / dsr / bh_p / n_trades / verdict | identical | identical |
| `gate_results` | identical (3 rows) | identical (3 rows) |
| ivrv `state_transition.ts` | `2026-06-21T14:08:41` | `2026-06-19T14:59:22.355957` |

The cause is documented in the repository by the script that created the local
row. `migrate_ivrv_repair_local.py`'s own docstring states:

> *"Because `ledger.db` is untracked, the local dev machine's `ledger.db` never
> received those rows… The `run_ts` **approximates** the server run time
> (inferred from commit 2bd1ebd)."*

It guessed `2026-06-21T14:08:41`; production's actual value is
`2026-06-19T14:59:22`. `migrate_run_kind.py` then classified each host
independently, yielding `discovery` locally and `confirmatory` on production.

**Ruling: production's values are authoritative. The local row is a
reconstruction that its author flagged as approximate at the time, and it is
superseded — not corrected in place.** No `UPDATE` will be issued against
either ledger; the local row ceases to exist when local is rebuilt from
production (GL-3).

**Recorded so it is not lost:** the divergence has always been bidirectional.
`migrate_g3_atr_server_ledger_sync.py` copied a **local-origin** run
(`0c7e1bf9`) **to production**, while `migrate_ivrv_repair_local.py`
reconstructed a **production-origin** run **into local**. Selecting production
as canonical does not imply local never originated a legitimate act — it means
that from this date forward it may not do so without the synchronization
discipline GL-3 defines.

## 7. Why the chains cannot be merged

Three independent, individually sufficient reasons:

1. **Zero shared receipts and a hash-linked chain.** Every receipt chains on
   `prev_receipt_hash`. Interleaving or appending one chain's entries into the
   other changes `prev_receipt_hash` for every subsequent entry, which changes
   every downstream `receipt_id`. That is a rewrite, not an append.
2. **Append-only is enforced at the database layer.** `receipt_ledger` installs
   `RAISE(ABORT, …)` triggers blocking `UPDATE` and `DELETE` on
   `governance_receipts`. A merge would require exactly those operations.
3. **`b98c9685` is a conflict, not a gap.** A merge must *choose* one `run_ts`
   and one `run_kind`; it cannot hold both. There is no integrity signal to
   arbitrate on, because **both chains verify** — only provenance judgement,
   which is what this record supplies.

**Therefore reconciliation is adoption-and-rebuild, never merge:** one ledger is
adopted wholesale; the other is replaced by a copy of it; and any legitimate act
that existed only in the replaced copy is re-attested forward as a new,
honestly-dated receipt.

## 8. Why formally separate ledgers (Option C) is rejected

Option C — declaring the two ledgers permanently separate under a
synchronization contract — is rejected on governance grounds, not convenience:

- **It yields no single governance history.** Two hash-chains means two answers
  to "what was decided". For an append-only, content-addressed audit system
  whose entire purpose is that a decision be reconstructable years later, that
  is a defeat of the design.
- **`check_validated_preconditions()` would give host-dependent answers.** The
  first `family.promote` would exist on one host and not the other. Promotion
  is irreversible; a promotion that is real on one machine and absent on
  another is not a governed decision.
- **It ratifies drift as policy.** Separate-with-a-contract is what is
  happening now, unmanaged. Writing a contract around it would legitimise the
  condition that produced two disjoint chains in the first place.
- **It multiplies the durability obligation** — BK1/BK2/BK3 would have to cover
  both, and the drill would have to prove both.

Option C is rejected. It is recorded here so that a future session can see it
was considered on its merits rather than overlooked.

## 9. What this decision does and does not authorize

**Authorizes:** nothing operational. This is a statement of which ledger is
canonical, and the reasoning behind it.

**Does not authorize:** any ledger write, rebuild, copy, reseed, truncation,
update, delete or append; any production change; any registry, thresholds,
lockbox or sealed-artifact change. The rebuild of local from production, the
re-attestation of the `dataset.seal`, and the correction of
`bk3-activation-plan.md:214` are all **GL-3 scope and require separate explicit
authorization.**

**Standing rule from this date:** the production ledger is the canonical
governance record. No governance act may be recorded solely on a local ledger.
Until GL-3 executes, **local's ledger must be treated as a stale working copy
and must not be the basis of any governance claim.**

## 10. Unaffected by this decision

`GL-1`'s two structural blockers on the first Validated family are untouched and
remain open independently:

- **B1** — no `live-forward` (L2) run exists in **either** ledger, and no bridge
  from `evidence_forward.db` into `ledger.runs` exists. `validated.*
  .requires_non_offline_provenance` cannot be satisfied by any family today.
- **B2** — `pairwise_r` is empty on both ledgers, and Proposal 1 makes a missing
  pair a hard failure at promotion.

Forward collection is unaffected. The scheduled run at **2026-08-29 01:00:24
UTC** — `g1_trend_regime_ma_cross`'s first natural production observation — is
not touched by this gate and must proceed on its own schedule.

## 11. Status at this record

| | |
|---|---|
| Production HEAD | `a72a064` — unchanged |
| Production `ledger.db` mtime | `2026-08-07T02:54:29Z` — unchanged |
| Local `ledger.db` | unchanged |
| `registry.yaml`, `thresholds.yaml`, sealed artifacts, lockbox | unchanged |
| Forward DB | run_log 260, 6 signals, max `run_date` 2026-08-27 |
| `g1_trend_regime_ma_cross` observations | 0 — first observation due 2026-08-29 |
| Next collection | `2026-08-29 01:00:24 UTC` — untouched |
