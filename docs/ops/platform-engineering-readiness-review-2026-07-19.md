# Platform Engineering Readiness Review — 2026-07-19

Reviews the platform after ADR-0001, Phases 0–4, the PostgreSQL Adoption
Review (2026-07-18), and ENG-017 (2026-07-19). **No code was changed.**

**Verdict: the platform is engineering-complete for its current mission.**

The binding constraint on the Bitcoin research programme is no longer
engineering capability. It is **evidence accumulation over calendar time**,
plus a small, precisely-identified set of gaps that gate the *next* research
milestone rather than the platform's fitness. Those gaps total roughly a week
of work; none of them is infrastructure expansion.

---

## 0. Scope limitation, stated first

This review was conducted from the **development host**. It can read the repo,
the governance ledger, the offline research corpus, and the unit files. It
**cannot** observe the production VPS: no `systemctl` state, no journald, no
live `evidence_forward.db`, no `collection_metrics` rows.

Where a finding depends on production state it is marked **[VERIFY ON HOST]**
rather than asserted. The local `evidence_forward.db` is empty and the local
`btc_trading.db` is ~58 days stale; both are dev artifacts and neither is
evidence about production.

---

## 1. Assessment by dimension

### Architecture — **Strong**

Four planes (Runtime / Research / Operations / Observability) with a boundary
rule set (B1–B4) that has held under pressure across four phases. The most
telling evidence is that INV-1 ("no container reads or writes project data")
survived Phases 1, 3, and 4 without a single exception being carved out — and
that where the ADR's literal text conflicted with reality (Phase 3's bridge
network vs. the `127.0.0.1`-only exporter), the deviation was documented at the
point of change rather than silently absorbed.

The capture path is deliberately minimal: one sustained writer, one batched
commit per 60 s, staggered timers, WAL. The PostgreSQL review confirmed there
is no contention mechanism to speak of. This is the right shape for the mission
and should not be elaborated.

### Reliability — **Good, with two untested assumptions**

Three genuinely independent detection layers exist (heartbeat ~30 min;
`healthcheck.py` dead-man 10 min; Prometheus trend alerts), and B4's insistence
that they stay independent is respected — Phase 3's alert rules each state why
they are not a duplicate of layers 1–2.

Two weaknesses, both about recovery rather than detection:

- **The restore path has never been exercised.** `scripts/restore.py` exists,
  verifies SHA-256 against the manifest plus `integrity_check`, and is
  documented in the runbook. But there is **no `tests/test_restore.py`**
  (`test_backup.py` exists) and **no record of a restore drill anywhere in the
  repo**. ADR §12 BK2 is explicit: *"Backups are worthless until a restore
  drill proves them… A backup that has never been restored is an untested
  assumption."* The single irreplaceable artifact (`ledger.db`, ADR BK1) is
  protected by a mechanism that has never been run end-to-end.
- **Off-host backup (BK3) is unconfirmed.** `backup.py` supports
  `BACKUP_OFFHOST_CMD`, but ADR §20.1 lists the target as not provisioned, and
  whether it is set cannot be checked from here without reading `.env`
  (correctly out of bounds). **[VERIFY ON HOST]** Local-only backups do not
  survive VPS loss, which is the scenario backups exist for.

### Observability — **Complete for the mission, with one unscheduled gate**

Capture quality is genuinely well instrumented: `collection_metrics`
(coverage, wrote/skipped/failed, `reconcile_count`, `stale_count`), the
`exch_ts` sane-band coercion, the Phase 2 exporter's 28 metrics, Phase 3's
dashboard and trend alerts, and Phase 4's read-only API. The distinction
between `coverage_pct` and `stale_count` — one catches never-ticked symbols,
the other catches a stalled ticker that coverage would hide — is the kind of
detail that only comes from having been burned, and it is documented.

One real gap: **`scripts/certify_day.py` is not scheduled by any unit.** It has
a schema table (`daily_certifier_log`, added in migration v4), a test file, and
a defined verdict vocabulary — but no `.service`/`.timer` pair runs it. Grep
across `deploy/` returns nothing. CLAUDE.md's research-integrity boundary
states that evidence accumulation requires *"the daily certifier reports
green"*; today nothing produces that verdict on a schedule. The infrastructure
was built and the wiring was never finished.

### Maintainability — **Strong**

1,286 tests across 32 files. **Zero `TODO`/`FIXME`/`XXX`/`HACK` markers** in
`src/`, `scripts/`, and `research/`. Migrations are idempotent, guard on actual
schema state rather than `user_version` (so a partial run self-heals), support
`--dry-run`, and are individually tested. Structural invariants are enforced by
AST/source-scanning guards rather than convention in at least three places
(PR-6, PR-8, Phase 4's read-only guarantee).

The known-failing tests are two date-dependent expiry assertions, documented in
CLAUDE.md and stable across this session's four workstreams. They are noise,
but they are *load-bearing* noise: a suite with two permanently-red tests
trains the operator to skim the summary line. Worth fixing for hygiene, not
because they hide anything today.

### Governance — **Strong, with one hard blocker for the next milestone**

PR-1 through PR-9 produced a genuinely rigorous system: hash-chained
append-only receipts (26, chain verified), DB-level triggers blocking
`UPDATE`/`DELETE`, `BEGIN IMMEDIATE` serialisation with the TOCTOU race caught
and fixed under review, rule-versioned replay that refuses to substitute a
default rule, and point-in-time reconstruction from the ledger rather than
`registry.yaml`. `report.py --check` is clean; Phase 4 confirmed the API and
CLI agree exactly (35 families; 9 candidate / 22 discovery / 4 disqualified).

**The blocker:** `max_pairwise_r: 0.70` is declared in `thresholds.yaml` for
both `validated.edge` and `validated.feature` and is **enforced nowhere**.
Verified directly: no `pairwise_r` table in `ledger.db`, and no
`append_pairwise_r` / `check_validated_independence` anywhere in
`research/evidence_library/`. CLAUDE.md §16 already flagged this as deferred
with a frozen design and an explicit instruction — *"Implement before the first
Candidate → Validated promotion… implement at least one full `report.py
--check` cycle before the promotion attempt."* That promotion is the next
research milestone, so this is now on the critical path.

Also open: the **`n_trials` 34 vs 38** question (the DVOL family was explored as
trials 35–38 but registered against the frozen 34). Deferred to the next
governance session and unresolved. It affects the DSR denominator for any
DVOL-family promotion, including H-IVRV.

### Operational risk — **Moderate, concentrated in deployment lag**

The dominant risk is not a design flaw; it is that **built ≠ deployed**. Phases
3 and 4 and the ENG-017 migration all exist in the repo and none has been
applied to production. Every one of them is additive and independently
reversible, so the risk is drift and unrealised benefit rather than breakage —
but an un-applied ENG-017 means the 10-minute dead-man probe is still doing an
O(n) scan that grows linearly, and an unapplied Phase 3/4 means the trend and
query surfaces exist only on paper.

Ranked: (1) untested restore path against an irreplaceable ledger; (2)
unscheduled certifier; (3) unverified off-host backup; (4) deployment lag.

### Technical debt — **Low, and unusually well characterised**

Every item is measured, documented, and ticketed rather than folkloric:

| Item | Status |
|---|---|
| `certify_day.py` non-sargable `DATE(ts)` queries | Measured (21–34× available), documented in ENG-017 §7, deliberately not fixed in a perf ticket |
| `partition_reader.py` `ts LIKE ?\|\|'%'` | Measured (9× available); the one path no index can fix |
| `funding_snapshots` dormant/unwired | Documented in CLAUDE.md; write path exists, exercised only by tests |
| Phase 4 API `ReadWritePaths` on `evidence_library` | Broader than needed — `ledger.db` is `journal_mode=delete`, so V1's WAL rationale does not apply |
| Two date-dependent expiry test failures | Known, stable, documented |

This is the healthy kind of debt: bounded, priced, and chosen.

### Research readiness — **The actual constraint**

The governance ledger tells the story precisely:

- **All 70 runs are `provenance = offline-corpus`.** Zero `live-forward`, zero
  `mainnet`.
- **Run kinds: 35 `discovery`, 35 `confirmatory`. Zero `forward`.**
- **Zero families in `validated`.** 9 candidate, 22 discovery, 4 disqualified.
- ML readiness gates on `validated` with a target band of **10–20** families.

So the distance to ML readiness is 0 → 10–20 validated, and the Validated gate
requires non-offline provenance, DSR ≥ 0.50, and BH-p ≤ 0.10 evaluated **on
forward returns**. Even if all 9 current candidates validated, that is below
the band — the programme needs both forward evidence *and* more candidates.

**The most important structural finding of this review is a distinction that is
easy to get backwards.** CLAUDE.md's rule that testnet data is
pipeline-validation only is correct, but it does **not** block the current
research path:

- `data/btc_trading.db` (`option_quotes`) is **Delta testnet** — OI ≈ 0,
  economically synthetic IV. Pipeline validation only. The **options/IV/OI
  research axis is genuinely blocked** until Delta mainnet.
- `research/data/evidence_forward.db` (the H5 / G1 / G3 / IVRV daily collector)
  is fed by **`daily_ingest.py` → Binance OHLCV, plus Deribit DVOL** — real
  mainnet exchange data. Forward evidence from that collector **is legitimate
  research evidence today.**

The four registered forward signals are all spot/vol/regime/VRP families, none
of which depends on Delta option quotes. **The research programme can reach
Validated without Delta mainnet.** What it cannot do without Delta mainnet is
the options-surface work — and no family currently registered needs it.

Two clocks are therefore running, and only one is engineering-addressable:

- `promotion_ready` requires **`min_calendar_months: 6`** of forward evidence.
  That is irreducible; no amount of engineering shortens it.
- `production_ready` additionally requires 30 forward trades, live
  reconciliation, and **mainnet provenance**.

**The lockbox has matured.** `pre_registration_ivrv.json` seals
2025-06-01..2026-05-31, and the corpus now runs 2021-01-01 .. **2026-06-10** —
the window has fully elapsed and the data exists. It is correctly still SEALED
(sealing is a discipline about not spending it for gating, not a data
availability question). It remains the single-use final OOS test and should be
spent **only** at a genuine promotion attempt, after forward evidence exists —
not now, and not to make an interim decision look better.

Finally: **`g3_atr_expansion` is `enabled: false`** in
`collection_config.yaml` — a fifth pre-registered signal built, tested, and
sitting idle. Every day it stays off is a day of forward evidence not being
accumulated for it, against a 6-month promotion clock.

---

## 2. Prioritised roadmap

Scoped to work that **materially improves the research platform**.
Infrastructure expansion is excluded unless a trigger fired — see §3.

### 2.1 Must do now

Ordered by "what breaks or is lost if this slips".

| # | Item | Why now | Size |
|---|---|---|---|
| 1 | **Activate `g3_atr_expansion`** (`enabled: true`) | Pure calendar loss. A 6-month forward clock cannot start retroactively; every day off is a day never recovered. Fully pre-registered and tested already. | Minutes |
| 2 | **Schedule the daily certifier** — `btcbot-certify.{service,timer}`, mirroring the existing one-shot pattern | Evidence admissibility is defined as "certifier green", and nothing produces that verdict on a schedule. Without it, forward evidence accumulates *uncertified*, which is a retroactive-audit problem, not a future one. | ~½ day |
| 3 | **Restore drill + `tests/test_restore.py`** | BK2 is unsatisfied against the one irreplaceable artifact. Everything else in the platform assumes `ledger.db` is recoverable; that assumption is untested. Drill on a clean host, then verify `verify_chain()` valid and `report.py --check` clean. | ~1 day |
| 4 | **Deploy Phase 3, Phase 4, and the ENG-017 migration** | Built and verified but unapplied. ENG-017 in particular removes an O(n) scan from the 10-min dead-man path, and it gets more expensive to apply as the table grows (~55 s index build at 36M rows). Run the ADR §16 seven-item block after. | ~1 day |
| 5 | **Resolve off-host backup (ADR §20.1/§20.3)** — provision the target, set `BACKUP_OFFHOST_CMD`, document the encrypted `.env` copy | BK3 is what makes backups survive VPS loss. Pairs naturally with #3. **[VERIFY ON HOST]** first — it may already be configured. | ~½ day |

### 2.2 Next research milestone — *first Candidate → Validated promotion*

This is the platform's next genuine state change, and it has hard prerequisites.

| # | Item | Note |
|---|---|---|
| 6 | **Implement Proposal 1 — pairwise independence enforcement** | The design is frozen and fully specified in CLAUDE.md §16 (table DDL, `append_pairwise_r`, `check_validated_independence`, wiring into `check_validated_preconditions` and `validate_registry`). Implementation is a mechanical translation. Must land, and run one clean `report.py --check` cycle, **before** any promotion attempt. H-IVRV at max corr 0.37 is the natural first test case. |
| 7 | **Settle `n_trials` 34 vs 38 in a governance session** | Affects the DSR denominator for DVOL-family promotions. Unresolved thresholds discovered *during* a promotion attempt is the worst time to discover them. |
| 8 | **Accumulate ≥6 months of certified forward evidence** | Irreducible calendar. Requires #1 and #2 in place first, or the evidence is incomplete or uncertified. |
| 9 | **Then, and only then, spend the lockbox** | Single-use. Spend at a genuine promotion attempt, never for interim gating. |
| 10 | **Grow the candidate pool toward the 10–20 band** | 9 candidates cannot reach a 10–20 validated target even at a 100% pass rate. This is research work — new hypotheses on the existing offline corpus — not platform work. |

### 2.3 Future scale triggers

Pre-committed, so the decision is made once and not re-litigated under pressure.

| Trigger | Action | Current reading |
|---|---|---|
| `btc_trading.db` > 10 GB | Retention/rollup decision (**not** PostgreSQL — see the 2026-07-18 review) | 10.9 MB; ~341 days at measured growth incl. the ENG-017 index |
| `collection_metrics.failed` persistently non-zero | Investigate contention; re-open the PostgreSQL question | **[VERIFY ON HOST]** — not measurable from dev |
| A second sustained writer is introduced | Re-run the PostgreSQL review; its central premise is single-writer | None proposed |
| Delta **mainnet** credentials live | Unblocks the options/IV/OI research axis; re-baseline §13 constants on real liquidity | Testnet only; execution auth still blocked |
| Certifier red for >2 consecutive days | Halt evidence accumulation; treat the window as uncertified | No certifier scheduled yet (item #2) |
| journald retention proves insufficient for a real incident | Re-open log aggregation (ADR §18) | No incident yet |
| WAL > 200 MB sustained, or checkpoint starvation | Investigate long-running readers | `check_wal` covers this |

### 2.4 Long-term platform evolution

Directionally endorsed, explicitly **not** scheduled.

- **Retention and tiering.** The single highest-leverage future change: keep raw
  quotes for N days, roll older data to Parquet/aggregates. Preserves the
  one-file backup property and defuses the growth trigger permanently. Aligns
  with the already-sanctioned DuckDB + Parquet research stack.
- **Move analytical scans out of the operational store.** `btc_trading.db`
  should stay a capture buffer; heavy analysis belongs in the columnar corpus.
- **ML readiness review** at 10–20 validated families — the gate the entire
  Evidence Library exists to serve. Not before.
- **Execution lifecycle (Layer 6)** — SL/TP, testnet order placement, Telegram
  commands. Blocked on Delta auth and, correctly, on having something validated
  worth executing.
- **Phase 3b Tier-1 ring buffer** — remains behind Rohith's explicit gate.
- **Second Telegram channel** (ADR §20.2) to separate trend alerts from state
  heartbeats. Cosmetic until alert volume justifies it.
- **Sargable rewrites** in `certify_day.py` and `partition_reader.py` (ENG-017
  §7): 21–34× and 9× measured, zero storage cost. Do it when the certifier is
  scheduled and its runtime starts to matter.

---

## 3. Explicitly excluded — no trigger fired

Named so they are not silently reconsidered:

- **PostgreSQL** — reviewed 2026-07-18, not justified; neither trigger met.
- **Redis** — rejected in ADR §18; no multi-process state, no task queue need.
- **Containerizing the bot** — rejected; no multi-node requirement.
- **Log aggregation (Loki/ELK)** — deferred; journald has not failed an incident.
- **Public TLS ingress / reverse proxy** — rejected; SSH tunnel is sufficient
  for one operator.
- **Phase 3b ring buffer, Tier-4 capture, retention hierarchy** — gated.
- **Automated CD** — deferred pending ≥3 months of incident-free manual deploys.
- **Secret manager** — deferred; one host, one operator.
- **Widening the option universe beyond ATM±5 / 3 expiries** — this would be
  scope expansion without a hypothesis that needs the wings, and it would change
  the meaning of the existing corpus.

---

## 4. Conclusion

The platform is **engineering-complete for its current mission**. Four
infrastructure phases, a nine-PR governance architecture, a performance review
that correctly rejected a database migration, and a codebase with 1,286 tests
and zero debt markers is not a system waiting on more engineering.

What remains is a short, concrete list: five items to close now (roughly a
week, none of them infrastructure expansion), one frozen-design governance
implementation that gates the next milestone, and then a **six-month calendar
clock** that no amount of engineering can compress.

The most useful thing this review can say is where the real risk now lies. It is
not capacity, contention, or architecture. It is that a research platform can
look busy while accumulating nothing that counts — a fifth signal switched off,
a certifier that never runs, and forward evidence that has not started. Items 1
and 2 cost under a day between them and are the difference between the clock
running and the clock not running.
