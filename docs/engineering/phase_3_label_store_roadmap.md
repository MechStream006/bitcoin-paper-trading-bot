# Phase 3 (Label Store) — Implementation Roadmap

| Field | Value |
|---|---|
| **Title** | Engineering Implementation Roadmap — Phase 3, Label Store |
| **Status** | **Draft — planning artifact. Authorised for L1–L3 on completion of this revision; L4+ gated (§15)** |
| **Version** | **1.1.0** |
| **Document Type** | Engineering Implementation Plan (not an ADR, not an architecture document) |
| **Governing ADR** | [`ADR-I011`](../architecture/adr/ADR-I011-label-store-architecture.md) Label Store Architecture v0.1.0 (**Draft**) |
| **Parent roadmap** | [`implementation_roadmap_v1.md`](implementation_roadmap_v1.md) v1.0.1 §6, Phase 3 (WBS 3.1–3.6) |
| **Predecessors** | [Phase 2 Closeout](verification/records/phase-2-closeout.md) · [Phase 3 Readiness Review](verification/records/phase-3-readiness-review.md) |
| **Authors** | Rohith Suresh — Owner / Architect · Drafted with Claude Opus 5 |
| **Date** | 2026-08-05 |
| **Changelog** | **1.1.0 (2026-08-05):** revision discharging conditions `C-1` and `C-2` of the Phase 3 Readiness Review. Closes defects `D-1`–`D-6`; amends scope transfers `S-2`, `S-3`, `S-5`; upholds `S-4` with concrete triggers; adds risks `LR11`–`LR13` and re-rates `LR2`/`LR5`. **WBS numbering 3.1–3.9 preserved**; 3.8 split into 3.8a/3.8b as approved; 3.10 added for backup wiring. No architectural decision changed. |
| **Code written** | **None.** Documentation only |

> **What this document is not.** It designs no architecture and decides nothing `ADR-I011` has not already decided. Where it proposes a file path, table name, or function signature, that is **engineering planning** — one conforming realisation of an already-written contract. Where it *corrects* the parent roadmap or the ADR, the correction is a matter of verified fact about this repository, not a change of scope, and is flagged as such (§1, §3).

---

## 0. Standing Gates This Roadmap Does Not Open

Unchanged from the parent roadmap §0. **`G-CONST`** (IQROS constitutional adoption — `ADR-I001` still unwritten) and **`G-STAB`** (stabilisation posture) both remain closed. This document plans work as if they will open, per the parent roadmap's §1.2 build-under-Draft decision, which is Rohith's to reverse at any milestone boundary.

**One additional gate is specific to Phase 3**, named here because it does not exist anywhere in the parent roadmap:

| Gate | Statement | Who opens it |
|---|---|---|
| **`G-PROD`** | Several Phase 3 items touch a **live, production-populated database** on the EC2 host. No Phase 0/1/2 milestone ever did. Host-side execution is a separate authorisation from repository-side implementation | Rohith, explicitly, per item |

---

## 1. Audit — `ADR-I011` Against the Completed Feature Store and the Real Repository

Every claim below was verified against the working tree, the real databases, and the running code. **Twelve findings; five change what Phase 3 must build.**

### 1.1 What the audit confirms exactly

| # | `ADR-I011` claim | Verified |
|---|---|---|
| **A-5** | H6 holds *"148 labels across 37 trades, 100% matured, plus 222 intra-hold path rows and 37 attribution records over a 1,987-day corpus"* | **Exact.** `forward_evidence.db`: `trade_labels` 148 (all `label_available=1`), `trade_entries` 37, `mae_mfe_path` 222, `attribution` 37, `corpus_daily` 1987. Horizons 5/7/14/30, exactly 37 each |
| **A-6** | The research label is *"a single scalar — one-day forward return — computed in memory, never persisted"* | **Exact.** `research/evidence/engine.py:203` — `m["ret_fwd1"] = m["close"].shift(-1) / m["close"] - 1.0`. Not persisted anywhere |
| **A-7** | PS6 — evidentiary standing is not a property of any label | **Confirmed open.** Repo-wide grep for `standing` / `evidentiary` across `research/forward_evidence/` and `research/evidence_library/` returns **zero** matches |
| **A-10** | The value maturity lifecycle *"has no analogue in the Feature Store"* | **Confirmed.** Nothing in `research/feature_store/` models pending/matured state. Genuinely new build |
| **A-12** | LG7 use-counting does not exist | **Confirmed.** No use-count mechanism anywhere |

### 1.2 Findings that change what Phase 3 must build

#### `A-1` — The fill-once guard is application-level, not storage-level

**The most consequential finding in the audit.**

`daily_job.py::_update_forward_labels()` enforces fill-once with a **`WHERE` clause**:

```sql
WHERE tl.label_day_ts = ? AND tl.label_available = 0
```

`forward_evidence.db` has **zero triggers** (verified: `SELECT name FROM sqlite_master WHERE type='trigger'` → empty). Nothing at the storage layer prevents an `UPDATE` of an already-filled label. Any code path that forgets the predicate — or any manual `sqlite3` session — silently falsifies a matured observation.

`ADR-I011` is unambiguous. §12 Tier C: *"the fill-once rule is **enforced at the storage layer**."* §7.2 (LG2): *"A matured Forward Label is **never rewritten**… overwriting it is falsification, not correction."*

**Consequence.** The parent roadmap's WBS 3.4 says *"the fill-once guard is reused, not reimplemented"*, and the Phase 3 DoD requires it *"proven reused via a shared-code-path test."* **Taken literally, satisfying that DoD would ship a guard the ADR rejects.** §4's WBS 3.4 resolves it: the *declare-then-fill discipline* is reused (it is correct and precedented); the *enforcement mechanism* is strengthened to a DB-level trigger, which the ADR requires.

**Precedent to follow, not invent:** `receipt_ledger.py` and `contract_registry.py` both use `RAISE(ABORT, …)` triggers for this exact class of guarantee, and `contract_lifecycle.py` already demonstrates the guarded "block updates except documented arcs" pattern a maturity lifecycle needs.

#### `A-2` — One of the three named fill conventions does not exist in this repository

`ADR-I011` §1 and PS1 name three audited conventions: *next-open with per-side slippage*, *close-to-close with turnover cost*, and *same-day close with a flat cost*. The parent roadmap's WBS 3.3 repeats the list verbatim.

**A repo-wide search finds no next-open convention anywhere** (`next_open`, `open_price`, `entry_open`, `shift(-1)…open` → zero matches). Entry and exit prices are close prices in every pipeline. The ADR audited a *reference platform*; that convention is not in this codebase.

**The three that actually exist are all close-based and differ in cost and direction handling:**

| # | Where | Entry / exit | Cost | Direction | Stored? |
|---|---|---|---|---|---|
| **C1** | `forward_evidence/daily_job.py` (H6) | same-day spot close both sides | flat `COST_BPS = 15.0`, charged **once at exit** | always `1` (long-only, hardcoded) | `trade_exits.net_return` — **stored** |
| **C2** | `research/evidence/engine.py::_net_daily()` | close-to-close, daily compounding | `cost_bps × turnover`, i.e. `\|Δposition\|` — charged **per position change** | signed continuous `position` | **not stored** — in-memory |
| **C3** | `forward_evidence/runner_base.py` + `trade_returns` view | close-to-close per trade | flat `cost_bps` per trade, charged **once**, stored per exit row | `±1` direction column | **view-derived at query time** |

C1 and C3 differ (C1 stores `net_return` and cannot go short; C3 derives it in SQL and supports `±1`). **C2 differs from both structurally** — it charges on turnover, so a held position costs nothing to maintain, while C1/C3 charge a fixed cost per trade regardless of duration. Under a long hold they produce genuinely different numbers from the same prices. **PS1 is real; the ADR mis-identified one of its three instances.**

**Consequence.** WBS 3.3 registers **these three**. Registering a `next-open + slippage` Specification would fabricate a convention nothing uses — the dishonesty WBS 2.7 avoided when it refused to invent a `narrowing` conditioning example.

#### `A-3` — There are four pre-registration boundaries, not one

`ADR-I011` §10.1 and PS6 describe *"the pre-registration boundary"* as a singular *"timestamp constant"*. This repository has **six pre-registration artifacts carrying four distinct instants**:

| Pre-registration | Boundary |
|---|---|
| `pre_registration.json` (H6) | `2026-06-10T16:50:41Z` |
| `…_g1_market_regime_sma200.json`, `…_g3_tsmom.json`, `…_h5_vol_regime.json` | `2026-06-14T17:48:39Z` |
| `…_ivrv.json` | `2026-06-19T00:00:00Z` |
| `…_g3_atr_expansion.json` | `2026-06-26T00:00:00Z` |

**This strengthens the ADR's own argument.** PS6 says standing *"is re-derived by every consumer, and can be re-derived differently."* With four boundaries, a consumer must first select the **correct** one for the signal in question — strictly more error-prone than a single constant.

**Consequence.** WBS 3.5 keys standing to the **owning signal's own** pre-registration, resolved via `research/forward_evidence/pre_reg_guard.py`, which already performs that lookup.

#### `A-4` — The multi-signal collector's local database is empty; production state is not observable from here

`research/data/evidence_forward.db` has **0 rows in every table**. Last modified 2026-06-14.

**This does not mean the collector has collected nothing.** It runs on the EC2 host under `systemd`; project memory records H5/G1/G3/IVRV as collecting since roughly 2026-06-28. **The local copy is stale and production state cannot be verified from this session.**

**Consequence.** Any item wrapping the multi-signal collector must be sized against **host-verified** volume. This is also the exposure Program P1 hit at Increment 5, whose transcript was never captured back and which sits at `EXECUTED (ATTESTED)` rather than `DEMONSTRATED` to this day — see `S-7`.

#### `A-9` — Two similarly-named databases with different schemas

| File | Schema | Contents |
|---|---|---|
| `research/data/forward_evidence.db` | `schema.sql` | H6: `corpus_daily`, `regime_states`, `signal_log`, `trade_entries`, `trade_exits`, **`trade_labels`**, `mae_mfe_path`, `attribution`, `run_log` |
| `research/data/evidence_forward.db` | `schema_forward.sql` | Multi-signal: `run_log`, `signal_log`, `trade_entries`, `trade_exits`, `trade_returns` (view). **No label table** |

The names are transpositions and **four table names collide with different columns**. Both are backed up and gitignored.

**An operator-error hazard, pre-existing.** This roadmap does not propose renaming (unrelated refactoring). `LR4` guards it in Phase 3's own scripts instead.

### 1.3 Phase 2's gaps, seen from the Label Store — **complete inventory**

> **Revised in v1.1.0 (defect `D-3`).** The v1.0.0 table analysed five of the seven Phase 2 register items and silently omitted `FS-GAP-002` and `FS-GAP-005`. Both are restored below. All seven are now assessed, and every recurrence has a named owner in §3 or §4.

| Phase 2 item | Recurs in Phase 3? | Owner after this revision |
|---|---|---|
| [`FS-GAP-001`](feature_store_technical_debt_register.md#fs-gap-001--admissibility-inheritance-is-not-implemented) — admissibility lattice unused | **Recurs, from the other side.** §9.2 and DI5 require the meet across evidence, features, **and** labels | **WBS 3.7 — both stores** (`S-2`, amended) |
| [`FS-GAP-002`](feature_store_technical_debt_register.md#fs-gap-002--tier-d-output-hygiene-is-not-implemented) — Tier D output hygiene | ⚠️ **RESTORED. Recurs directly** — `ADR-I011` §12 Tier D is the same requirement class, and v1.0.0 mapped only 1 of its 4 checks | **Label side: WBS 3.9 (expanded). Feature side: NOT absorbed** — stays `FS-GAP-002`, recommended as separate scheduled work |
| `FS-GAP-003` — equivalence test-enforced, not registration-enforced | **Would recur by default.** §8.3 explicitly reuses `ADR-I010` §8.3's mechanism | **Deliberately inherited** (`S-4`) — with two concrete re-open triggers, `LR8` |
| `FS-GAP-004` — no registration receipt | **Recurs.** LG1 is verbatim `ADR-I010` FG1 | **WBS 3.8a + 3.8b** (`S-3`, amended — backfill mandatory) |
| [`FS-GAP-005`](feature_store_technical_debt_register.md#fs-gap-005--no-real-corpus_version-and-no-value-set-provenance-closure) — no real `corpus_version` | ⚠️ **RESTORED. Recurs** — §9.1's research-tier address and §14's provenance both require a corpus version | **Cannot be owned by Phase 3** — blocked on Phase 1 + Phase 4. **Binding instruction: do not invent one** (§4.3) |
| `FS-TD-013` — empty sensitivity variants | **Does not recur.** §8.2 has no sensitivity-variant field | n/a |
| `FS-TD-014` — Value Store has no tenant | **Does not recur — reverses.** The forward tier has 148 real rows waiting | n/a |

---

## 2. Assumption Verification — Phase 2 Closeout §6.1 Re-checked

| # | Assumption as handed over | Verdict | Note |
|---|---|---|---|
| **A1** | The `registry.py` pattern is proven and safe to copy | ✅ **Holds** | 17 Specifications, hash-stable across five regeneration events |
| **A2** | `ADR-I011`'s registry needs no new mechanism | ✅ **Holds** | §6 says so; nothing found requiring more at the *registry* level |
| **A3** | `ret_fwd1` is unclaimed and reserved | ✅ **Holds** | Excluded from Phase 2, guarded by an import-time assertion |
| **A4** | H6 is unmodified and available to wrap | ⚠️ **Holds with a correction** | Unmodified ✅ — but *"available to wrap"* understates the work: its fill-once guard does not meet §12 Tier C (`A-1`) |
| **A5** | `research/evidence/engine.py` is unmodified | ✅ **Holds** | Never edited across Phase 2 |
| **A6** | Three ingest sources carry conditioning Phase 3 can consume | ✅ **Holds** | `dvol`, `funding`, `binance_ohlcv` — all three also carry raw-lake revisioning |
| **A7** | Phase 0's lattice exists, is tested, unused — Phase 3 may be its first consumer | ✅ **Holds, and is now decided** | `S-2`, amended: **both stores**, not label-only |
| **A8** | Both full-suite baselines are stable | ✅ **Holds** | 1881/19/0 (`btc_bot`, repo-wide); 1525/37/0 (base env) |
| **A-11** | Phase 1's partition revisioning covers the H6-relevant ingest path | ✅ **Holds** | `raw_lake.py` provides content-addressed revisions with an append-only index; `binance_ohlcv.py` — H6's spot source — is one of the three conformed writers. **WBS 3.6's Phase 1 dependency is satisfied** |
| **A-12** | Nothing outside `research/` consumes H6's label tables | ✅ **Holds** | No `src/` module references `trade_labels` or either DB. The operational/research boundary is not at risk |

---

## 3. Scope Transfers — Decided

Seven transfers, each carrying the Readiness Review's decision. **Four accepted as proposed, two amended, one upheld with added triggers.**

| ID | Transfer | Decision | Lands in |
|---|---|---|---|
| **`S-1`** | **Storage-layer immutability becomes a Phase 3 requirement**, not an accepted simplification. `FS-TD-001` accepted Python-guarded transitions for features because *"single writer, no concurrent-writer scenario exists yet."* **That justification does not transfer** — a forward label is irreplaceable; §12 Tier C mandates storage-layer enforcement explicitly | ✅ **ACCEPTED as proposed** | WBS 3.2, 3.4 |
| **`S-2`** | **Phase 3 owns admissibility inheritance** | ⚠️ **AMENDED — build store-agnostically and apply to BOTH stores.** Label-only would leave Phase 4's DI5 meet with a missing leg, built later by a different milestone against a different integration point — `R4` drift through a different door. Cost: ~1 session over 3.7's M. Closes `FS-GAP-001` (High, constitutional, IQROS-P4) **completely** | WBS 3.7 |
| **`S-3`** | **Registration receipts built in Phase 3** | ⚠️ **AMENDED — the Phase 2 backfill is MANDATORY, not optional, and is split out as 3.8b.** If labels emit receipts and features do not, a reader encountering a receipt-less Specification cannot distinguish "not required" from "missing — defect" — the exact ambiguity `CLAUDE.md` §17 records at PR-3→PR-4, resolved there only by adding an explicit status enum | WBS 3.8a, 3.8b |
| **`S-4`** | **Equivalence stays test-enforced** (`FS-GAP-003` inherited) | ✅ **UPHELD, with concrete re-open triggers.** Resolving it means registering conformant bindings in **both** stores for a risk already controlled by hash-pinning plus full-suite tests. But "defer indefinitely" is not a decision — **two triggers now re-open it** (`LR8`) | Not scoped — `FS-GAP-003` |
| **`S-5`** | Import `warmup.py` **and** `conditioning.py` rather than re-implement | ⚠️ **SPLIT — `conditioning.py` accepted; the `warmup.py` half is DROPPED.** `conditioning.py` is directly required (§8.2 mandates the same three propagation rules over the same ingest sources). The warm-up clause was hedged — *"if a maturity-window analogue is wanted"* — and **no `ADR-I011` requirement calls for one**. Maturity is elapsed calendar time; warm-up is insufficient history. Different concepts that merely both gate a resolution. Carrying a speculative reuse invites a forced fit later | WBS 3.7 (conditioning only) |
| **`S-6`** | `ret_fwd1` migrates to the Label Store | ✅ **ACCEPTED as proposed** | WBS 3.3 |
| **`S-7`** | **Phase 3 captures host-side execution evidence into the repository** | ✅ **ACCEPTED, hardened to a per-milestone DoD criterion (`L-D2`)**, not a "should" | §8.1, all host-side items |

---

## 4. WBS Breakdown

Sizes are t-shirt (S = one focused session, M = 2–4, L = 5+), calibrated against Phase 2's actual pace.

**Numbering preserved.** Items 3.1–3.9 retain the IDs and intent they carried in v1.0.0 and, where applicable, in the parent roadmap. Changes from the parent are marked ⚠️; changes introduced by this revision are marked 🔄. **3.8 is split into 3.8a/3.8b** and **3.10 is added**, both per the approved Readiness Review.

### 4.1 Items

| ID | Item | **Owns (`ADR-I011` §)** | Size | Depends on | Entry criterion | Exit criterion |
|---|---|---|---|---|---|---|
| **3.1** | `research/label_store/registry.py` — `LabelDefinition`, `LabelSpecification`; Tier A completeness over all nine §8.2 mandatory fields **plus the calendar-governance field** (🔄 `LR7`); content-addressed; append-only; Specification lifecycle mirroring `ADR-I010` §11; change-classification and maturation-is-not-a-version semantics (🔄 `D-1` sweep) | §6 · §6.1(7.1) · §8.2 · §10 · §11.1 · §11.2 · **§12 Tier A** · §12.1 · §16.1 · §17 · LG6 · LG8 | M | 0.1–0.3 | Phase 0 complete; `research/label_store/` absent | Registry accepts a hand-written test Specification; **Tier A refuses a malformed one on each of the ten mandatory fields, one negative test per field**; an invariant test proves maturation does not change a Specification hash (§11.2) |
| **3.2** | `research/label_store/value_store.py` — research tier (compute-on-read, `ADR-I010` §7.3 verbatim) + forward tier (materialised) + the six-state maturity lifecycle, enforced by **DB-level triggers**; **both addressing forms stated explicitly** (🔄 §7.1: research triple, forward (spec · subject · declaration instant)) | §1.1 · §7 · **§7.1(9.1)** · §7.2(9.2) · §7.3(9.3) · **§12 Tier C** · §12.2 · LG2 · LG4 | **L** | 3.1 | 3.1 merged | Two tiers live; **a matured label cannot be updated by ANY SQL path, proven by an adversarial direct-`sqlite3` test**; every allowed maturity arc tested, every forbidden arc refused; `Void` recorded permanently |
| **3.3** | Register the **three real** conventions (C1, C2, C3) as three Specifications, plus `ret_fwd1` as the research-tier Specification | §8.3 · §16.1 (applied) · PS1 · PS2 | M | 3.1 | 3.1 merged; the `A-2` inventory accepted | Four Specifications registered; **C1/C2/C3 produce three distinct hashes from otherwise-identical inputs**; `ret_fwd1` no longer unclaimed. No forward data touched |
| **3.4** | Wrap the H6 forward-label system as the first forward-tier population (148 labels / 37 trades / 4 horizons). Declare-then-fill **discipline reused**; enforcement **strengthened** to the storage layer. `bootstrap` attestation + `Context` standing per §4.4 | **§12 Tier B** (🔄 `D-1`) · §16.2 · §14 (declaration/maturation instants) · LG2 applied · §7 forward tier | **L** | 3.2, 3.3, **3.10** | **3.10 complete** (backup wiring live before the first irreplaceable row); production row counts recorded on the host (`LR3`); `G-PROD` opened | 148 labels wrapped, `attestation=bootstrap`, standing `Context`; **zero mutation of original H6 rows**; migration idempotent across three `--commit` runs; **a negative test proves no label may be assigned `Forward evidence`** (🔄 `LR2`) |
| **3.5** | Evidentiary standing (`Context` / `Forward evidence` / `Promotion evidence`) recorded at declaration, immutable thereafter | §10.1 · **§12 Tier B** (standing-at-declaration half) · LG3 | M | 3.4 | 3.4 merged | Standing resolved **per-signal** via `pre_reg_guard.py` across all four boundaries; an adversarial test proves standing cannot be changed after declaration (LG3); two consumers cannot reach different conclusions |
| **3.6** | Superseded-input flagging (§11.3): every forward label records its corpus revision set; a corrected revision flags affected labels; `Flagged → Adjudicated` is a human act | §11.3 · §14 (revision set, flag history) · DG3 · PS4 | M | 3.4, 1.3 | 3.4 merged; a **disposable copy** of a revisioned partition exists to simulate a correction against | A simulated correction flags exactly the right labels and **neither rewrites nor trusts** them; `Flagged` labels are inadmissible until adjudicated; adjudication is human-gated and recorded |
| **3.7** | 🔄 **Conditioning propagation + store-agnostic admissibility inheritance.** Import `feature_store/conditioning.py` **unchanged**; build a store-agnostic inheritance module over `research/admissibility/` and apply it to **both** the Label Store and the Feature Store | §10.2 · §9.2 · DG6 · **`FS-GAP-001` (both stores)** | M+ | 3.1, 3.4 | 3.4 merged; Phase 0 lattice available | Every label set carries a propagated conditioning statement and a computed admissibility ceiling; **the same module computes the ceiling for all 17 Feature Specifications**, closing `FS-GAP-001` in both stores; `conditioning.py` unmodified (proven by diff) |
| **3.8a** | 🔄 `label.register` / `label.adjudicate` governance receipts, generic over subject kind | LG1 · §9 (registration receipt) · IQROS-P8, P10 | M | 3.1, 3.6 | 3.1 and 3.6 merged; `S-3` amendment accepted | Receipts emitted, chained, replayable; `verify_chain()` clean on the real ledger; a `derived`-path replay re-executes and matches |
| **3.8b** | 🔄 **Mandatory** bootstrap backfill of Phase 2's 17 `feature.register` receipts | **`FS-GAP-004` closure** (`ADR-I010` FG1) | S | 3.8a | 3.8a merged | 17 receipts written with `attestation=bootstrap`; idempotent re-run reports 0 pending; chain valid; **`FS-GAP-004` closed in both stores** |
| **3.9** | 🔄 **Set hygiene — all four §12 Tier D checks** (expanded from partial-maturity only): maturity fraction, **horizon coverage, outcome-distribution disclosure including class imbalance, standing composition**. Plus §16.3's partial-maturity refusal | **§12 Tier D (all four)** (🔄 `D-2`) · §16.3 · DG2 · PS3 | M | 3.2, 3.4, 3.5 | 3.5 merged (standing composition needs standing) | A full-maturity request against a partially-matured set is **refused by name**, observable via a query; all four Tier D statistics computed and recorded; each **caps admissibility** rather than blocking, feeding 3.7's meet |
| **3.10** | 🔄 **Forward-tier backup and durability wiring** — `scripts/backup.py`, `scripts/healthcheck.py`, `src/api/services.py`, following `feature_store_backup_strategy.md` §4's mechanical pattern | §7 (forward tier: *"If lost — Irreplaceable"*) · **`D-4`** | S | 3.2 | 3.2 merged; `LS-DEC-001` resolved | Forward-tier DB named in `backup.py` with `VACUUM INTO` + SHA-256 manifest + rotation; freshness check wired into both the CLI and `runtime_health()`; **a restore drill is performed and its transcript committed** |

### 4.2 Decisions register

| ID | Decision | Owner | Due | Status |
|---|---|---|---|---|
| **`LS-DEC-001`** | 🔄 Forward-tier storage location — co-locate in `label_store.db`, or a separate `label_forward.db`. Bears on `LR4` (transposition risk) and on 3.10's backup shape | Rohith, on the L2 recommendation | **Before 3.10** | **Owed** (was untracked in v1.0.0 — `D-6`) |
| **`LS-DEC-002`** | 🔄 Whether the multi-signal collector adopts the Label Store (PS5) | Rohith + `G-PROD` | Not scheduled | **Owed.** `ADR-I011` R8 makes it a separate gated decision; out of Phase 3 scope |

### 4.3 Explicitly out of Phase 3 scope, with the owner named

| Item | Why | Owner |
|---|---|---|
| LG7 / DI8 **use-counting** of Promotion Labels | Use-counting is *against a sealed decision*, which does not exist until the Dataset Builder | Phase 4 |
| LG5 citation-index check before retirement | Same as `FS-TD-004` | Phase 4 |
| §15 DI1–DI8 dataset integration | Phase 4 is the counterparty; nothing to build unilaterally | Phase 4 |
| §16.4 **path labels** as Specifications | `mae_mfe_path`'s 222 rows exist, but §16.4 states path measurements *"are separable"* — and the ADR calls the newer collector's omission of them **correct**. Registering them now adds 4× horizon multiplicity for no current research question | Deferred — research-question-gated |
| **Feature-side** Tier D (`FS-GAP-002`) | 🔄 Genuinely Phase 2 work. Absorbing it would let Phase 3 quietly grow a Feature Store obligation | `FS-GAP-002` — **recommended as separate scheduled work** |
| `FS-GAP-003` in either store | `S-4` — deliberate inheritance, with triggers | `FS-GAP-003` |
| **Inventing a `corpus_version`** | 🔄 **Binding instruction.** A locally-invented corpus version would look real and compose incorrectly at Phase 4. WBS 3.2's research tier uses the same opaque caller-supplied string Phase 2 uses, and records that §9.1's addressing guarantee is structurally sound but untested | `FS-GAP-005` — blocked on Phase 1 + Phase 4 |

### 4.4 The one genuinely hard question, and its answer

**How can 148 already-matured H6 labels enter a store whose central rule is that standing is assigned at declaration (LG3) and forward labels cannot be retrofitted?**

`ADR-I011`'s Standing Note is blunt: *"A label declared after its outcome is known is not forward evidence, whatever it is called."* All 148 are `label_available = 1`.

**The answer is the `bootstrap` attestation precedent this repository has already used three times** (`migrate_bootstrap_transitions.py`, `migrate_bootstrap_evidence_admission.py`, `migrate_bootstrap_transition_receipts.py`), each facing the identical problem of history predating its own governance mechanism.

WBS 3.4 wraps the 148 labels with:

- `attestation = "bootstrap"`, carrying the migration's own note;
- **standing = `Context`, never `Forward evidence`** — retained for regime and background analysis, **excluded from promotion evidence**, which is exactly what §10.1 defines `Context` to mean;
- a permanent record that the declare-then-fill discipline *was* honoured originally (placeholders at entry, filled as horizons arrived) even though no Specification governed it.

**This is the honest classification and the useful one.** It preserves 148 real observations while refusing to launder them into evidence they cannot be. Genuine `Forward evidence` standing begins with the first label declared *after* WBS 3.5 ships — the ordering constraint the ADR calls *"the one thing in this ADR that time can foreclose."* 🔄 Enforced by a negative test (`LR2`, raised to High likelihood).

---

## 5. Sprint Sequencing

| Sprint | WBS | Exit criterion (sprint-level; per-item criteria in §4.1 are separable) |
|---|---|---|
| **L1** | 3.1 | Registry accepts a test Specification; Tier A refuses on each of ten fields. **Zero real data touched** |
| **L2** | 3.2 | Two tiers + maturity lifecycle, trigger-enforced, adversarially proven. `LS-DEC-001` recommendation produced |
| **L3** | 3.3 | Three distinct convention hashes; `ret_fwd1` registered. **No forward data touched** |
| **L4** | **3.10 → 3.4** | 🔄 **Ordered within the sprint:** backup wiring lands *before* the first irreplaceable row is written. Then H6 wrapped, `bootstrap`/`Context`, idempotent |
| **L5** | 3.5 → 3.9 | Standing per-signal and immutable; then all four Tier D statistics and partial-maturity refusal |
| **L6** | 3.6, 3.7 | Superseded-input flagging live; conditioning + admissibility computed for **both** stores |
| **L7** | 3.8a → 3.8b | Label receipts chained and replayable; then Phase 2's 17 backfilled, closing `FS-GAP-004` |

**L1–L3 touch no real data at all** — three independently revertible sprints with zero data implication, mirroring how WBS 2.1–2.3 preceded 2.4.

**Ordering constraints within sprints:** `3.10 → 3.4` (durability before irreplaceability), `3.5 → 3.9` (standing composition needs standing), `3.8a → 3.8b` (backfill needs the emitter). L6's two items are independent and may be done in either order.

---

## 6. Dependencies

### 6.1 External to Phase 3

| Dependency | State | Blocking? |
|---|---|---|
| Phase 0 — `research/admissibility/`, receipt substrate | ✅ Complete | The only hard dependency (parent roadmap §5) |
| Phase 1 — raw-lake revisioning on the H6-relevant path | ✅ Verified (`A-11`) | Blocks **3.6 only** |
| Phase 2 — Feature Store | **Not a dependency.** Pattern reuse only — but 3.7 and 3.8b make *additive changes* to Phase 2 code (§12 `I9`) | No |
| Phase 1 WBS 1.6 for `research/ingest/calendar.py` | ⏳ Outstanding | **No** — H6 does not consume the macro calendar |
| `G-PROD` | ⏳ Not opened | Blocks host-side execution from L4 |
| Phase 2 commit plan executed | ⏳ Owed | 🔄 **Blocks L4 onward** (`LR13`) |

### 6.2 Internal

```
3.1 registry
 ├─→ 3.3 conventions ─────────────┐
 └─→ 3.2 value store ──┬──────────┴─→ 3.4 H6 wrap ─┬─→ 3.5 standing ─→ 3.9 Tier D set hygiene
                       └─→ 3.10 backup wiring ─────┤
                              (must precede 3.4)   ├─→ 3.6 superseded-input flagging
                                                    ├─→ 3.7 conditioning + admissibility (both stores)
                                                    └─→ 3.8a receipts ─→ 3.8b Phase 2 backfill
```

`3.6` additionally requires Phase 1's `raw_lake.py`; `3.7` additionally requires Phase 0's `research/admissibility/` and Phase 2's `conditioning.py`; `3.8a` additionally requires Phase 0's `receipts.py` / `receipt_ledger.py`.

### 6.3 Code reused rather than re-implemented

| Source | Used by | Basis |
|---|---|---|
| `research/feature_store/conditioning.py` | 3.7 | Generic — no feature-specific concept in any signature (`S-5`, accepted half) |
| ~~`research/feature_store/warmup.py`~~ | — | 🔄 **DROPPED** (`S-5`, rejected half). No `ADR-I011` requirement calls for a warm-up analogue; maturity is elapsed calendar time, not insufficient history |
| `research/admissibility/{lattice,composition}.py` | 3.7 | `S-2`, amended — consumed by a store-agnostic wrapper used by both stores |
| `research/evidence_library/{receipts,receipt_ledger}.py` | 3.8a, 3.8b | `S-3`, amended |
| `research/forward_evidence/pre_reg_guard.py` | 3.5 | Already resolves per-signal pre-registration (`A-3`) |
| `research/feature_store/registry.py` | 3.1 | **Pattern only, not import** — §6 sanctions mirroring |

---

## 7. Entry Criteria

**Per-item entry criteria are in §4.1.** Phase-level criteria:

| # | Criterion | State |
|---|---|---|
| **E1** | Phase 0 complete | ✅ |
| **E2** | `research/label_store/` does not exist | ✅ Verified absent |
| **E3** | Registry pattern proven against real data | ✅ Phase 2, 17 Specifications |
| **E4** | `ret_fwd1` unclaimed and structurally guarded | ✅ |
| **E5** | H6 unmodified and wrappable | ✅ (with `A-1`'s correction understood) |
| **E6** | The **real** convention inventory established | ✅ `A-2` |
| **E7** | Per-signal pre-registration boundaries enumerated | ✅ `A-3` |
| **E8** | Phase 1 revisioning verified for the H6 path | ✅ `A-11` |
| **E9** | Full-suite baselines established in both environments | ✅ 1881/19/0 and 1525/37/0 |
| **E10** | `S-1`…`S-7` scope transfers accepted, as decided in §3 | ✅ 🔄 **Discharged** by the Readiness Review §3 and this revision |
| **E11** | Phase 2 commit plan executed | ⏳ 🔄 **Raised from "advised" to blocking for L4 onward** (`LR13`) |
| **E12** | `FS-DEC-002` Phase 2 sign-off | ⏳ Owed — **not blocking**; Phase 3 depends on Phase 0 |
| **E13** | `G-PROD` opened | ⏳ **Not required to start.** Blocks host-side execution from L4 |

**L1–L3 may begin on E1–E10.** L4 additionally requires E11 and E13.

---

## 8. Exit Criteria

### 8.1 Per-milestone

The Engineering Verification Standard's `D1`–`D11` apply unchanged. **Three additions specific to Phase 3:**

| # | Criterion |
|---|---|
| **L-D1** | Every immutability claim is proven at the **storage layer**, adversarially — a direct `sqlite3` `UPDATE` against a matured label must fail, not merely an application path that declines to try |
| **L-D2** | Any item executed against a production database captures its full transcript into `docs/engineering/verification/records/` **in the same session** (`S-7`) |
| **L-D3** | No item assigns `Forward evidence` standing to any label whose outcome was known before its Specification existed (§4.4). 🔄 **Proven by a negative test, not only asserted** (`LR2`) |

**🔄 Reviewer sign-off (Verification Standard §12, RC1–RC8) is obtained per milestone, not deferred.** Phase 2 accumulated five unsigned records (`FS-DEC-002`); Phase 3 does not repeat that.

### 8.2 Phase-level

| # | Criterion |
|---|---|
| **P3-1** | ⚠️ The parent roadmap's Phase 3 DoD — *"the H6 fill-once guard is proven reused (not reimplemented) via a shared-code-path test"* — **restated per `A-1`**: the declare-then-fill *discipline* is proven reused via a shared-code-path test, **and** enforcement is proven strengthened to the storage layer. Both halves required |
| **P3-2** | Three fill conventions produce three distinct Specification hashes from otherwise-identical inputs (PS1 structurally resolved) |
| **P3-3** | The 148 H6 labels are wrapped, classified `Context`, `bootstrap`-attested, with zero mutation of the original rows |
| **P3-4** | A full-maturity request against a partially-matured set is **refused by name**, observable via a query (PS3) |
| **P3-5** | A simulated corpus correction flags the correct labels and neither rewrites nor trusts them (PS4 — the audit's *Unknown* made decidable) |
| **P3-6** | Standing is a recorded property, resolved per-signal; two consumers cannot reach different conclusions (PS6) |
| **P3-7** | Every label set carries a propagated conditioning statement and a computed admissibility ceiling (PS7, DG6) |
| **P3-8** | 🔄 **All four §12 Tier D statistics are computed and recorded** for every label set (`D-2`) |
| **P3-9** | 🔄 **`FS-GAP-001` and `FS-GAP-004` are closed in BOTH stores** (`S-2`, `S-3` amended) |
| **P3-10** | 🔄 **The forward tier is backed up, freshness-monitored, and a restore drill transcript is committed** (`D-4`, `LR12`) |
| **P3-11** | `report.py --check` clean on the real ledger; `verify_chain()` clean; both full suites at zero new failures |
| **P3-12** | A Phase 3 Closure Review is produced, including an `ADR-I011` Gap Matrix with an explicit **Unowned** bucket |

---

## 9. Verification Strategy

Governed by the [Engineering Verification Standard](verification/engineering_verification_standard.md) v1.0.0 — ten sections, explicit N/A where inapplicable, one record per milestone from the approved template.

| Standard § | Phase 3 emphasis |
|---|---|
| **§3 BV** | 🔄 **New structural guard:** an AST test asserting no `datetime.now()` appears in any maturation code path (`LR5`, re-rated) |
| **§4 TV3** | **Escalated to Phase 2's demonstrated bar.** Every milestone: unit-level sabotage **and**, from L4, live-script sabotage against a **disposable copy** of real data, reverted with hash-identical confirmation |
| **§4 TV4** | `ADR-I011` specifies more refusals than `ADR-I010`: ten Tier A fields, four maturity-arc violations, fill-once, partial-maturity, standing-immutability, flagged-label inadmissibility, **and `L-D3`'s forward-evidence prohibition**. **One negative test each** |
| **§4 TV5** | Fill-once; standing immutability (LG3); horizon-as-identity (§16.1); **maturation-is-not-a-version (§11.2)**; declaration instant ≠ maturation instant (§14) |
| **§5 REG** | Both environments, both scopes, every milestone. `research/label_store/` must be base-env-importable where it has no pandas dependency — per-class `skipif`, not blanket module-level `importorskip` (the `tests/test_conditioning.py` pattern) |
| **§7 DBV** | **Applies from 3.4 onward, and `DBV5` has no N/A path.** Backup + `PRAGMA integrity_check` before any real DB is touched. Migrations dry-run/`--commit`-gated, idempotent, proven by running twice |
| **§12 RC** | 🔄 **Sign-off obtained per milestone** (§8.1) |

**Three obligations with no Phase 2 analogue:**

1. **Adversarial storage-layer immutability.** Every fill-once and lifecycle-arc test must attempt the forbidden write through a **raw connection bypassing the application layer entirely**. A test proving only that the application declines to try proves nothing about `A-1`.
2. **Injected clock, structurally enforced.** Maturation is elapsed time; two pre-existing flakes already stem from `datetime.now()` in tests (`CLAUDE.md` §18, Increments 2 and 4). Phase 3 must not add a third — hence the AST guard above.
3. **Host-side evidence capture** (`S-7`, `L-D2`).

---

## 10. Rollback Strategy

Per IQROS `D5`/`G7`: every enforcement mechanism disableable by configuration, not redeploy. **One mechanism cannot follow that rule, and the exception is named rather than hidden.**

| Mechanism | Lever | Effect when disabled |
|---|---|---|
| Tier A completeness (3.1) | Revert the commit — no runtime consumer until 3.3 | Registry accepts nothing; nothing depends on it |
| **Storage-layer fill-once + maturity triggers (3.2)** | ⚠️ **None by configuration.** `DROP TRIGGER` is a deliberate, human, DDL act requiring a backup first | **Deliberate exception.** A configurable switch that turns off falsification protection is a switch that will eventually be left off. §7.2 admits no conditional. Recorded as a knowing deviation from the parent roadmap §14's blanket rule — the same honesty `FS-TD-010` applied to conditioning refusal |
| Convention Specifications (3.3) | Registry-level `Deprecated`; existing citations stay valid (§11.1) | No data implication — registry rows only |
| H6 wrap (3.4) | Restore from the pre-migration backup; **original H6 tables are never mutated**, so rollback is dropping new tables | Original 148 rows untouched by construction |
| Standing (3.5) | `label_policy.yaml` key `standing_enforcement: false` → recorded but not gating | Falls back to consumer-side date arithmetic — a logged regression, never default |
| Superseded-input flagging (3.6) | `superseded_input_flagging: false` | No new flags raised; **existing flags are never cleared** (IQROS-P15) |
| Admissibility meet (3.7) | `admissibility_enforcement: false` → computed and recorded, not gating. **Mirrors the warm-up `warn` precedent** | Ceilings recorded, not enforced — **in both stores**, symmetrically |
| Receipts (3.8a/3.8b) | Revert; the ledger is append-only so emitted receipts persist | Emission stops; history is never rewritten |
| Tier D + partial-maturity refusal (3.9) | `maturity_refusal: warn` — **ship in `warn` first**, per parent roadmap R5 | Partial maturity served with the fraction recorded, warning logged |
| Backup wiring (3.10) | Revert the commit; no data implication (adds protection, removes none) | Returns to today's unprotected state — **a regression, never a default** |

**No lever requires touching production data.** The 3.2 exception concerns *removing* a protection, not repairing data.

🔄 **Lesson carried from Phase 2:** `FS-TD-009` found warn-mode warnings had no durable capture, making the flip decision's own evidence precondition unsatisfiable. **Every Phase 3 `warn` mode writes to a queryable table, not stdout, from its first commit.**

---

## 11. Technical Risks

| ID | Risk | Likelihood / Impact | Mitigation |
|---|---|---|---|
| **`LR1`** | **Adding triggers to a live collector database breaks the running daily job.** `daily_job.py`'s fill path would hit a trigger it was never written against | **High / Severe** — a broken daily job stops forward evidence accrual, and lost calendar days are unrecoverable | Wrap in **new** `label_store` tables; do **not** add triggers to `trade_labels` in place. H6 keeps running unchanged; the Label Store observes it. Host-side application gated behind `G-PROD` with a stop-the-collector runbook. **The single most important design decision in Phase 3** |
| **`LR2`** | **Retroactive standing launders 148 known outcomes into forward evidence** | 🔄 **High** (raised from Moderate) **/ Severe** — the 148 are 100% matured, visibly clean, and *look* like ideal forward evidence; the pressure to classify them so will be real at L4 | §4.4's `bootstrap` + `Context` classification, enforced by `L-D3` **as a negative test**, not only a DoD line |
| **`LR3`** | **Production state is not observable from the dev machine** (`A-4`) | **Certain / Medium** | `S-7` / `L-D2`. 3.4's entry criterion requires host row counts recorded first |
| **`LR4`** | **`forward_evidence.db` / `evidence_forward.db` transposition** causes a write to the wrong database | **Moderate / Severe** | No default `--db` in any Phase 3 script; assert expected schema shape before any write; refuse on mismatch. **Not** solved by renaming (unrelated refactoring). `LS-DEC-001` should avoid a third confusable name |
| **`LR5`** | **Time-dependent maturation tests introduce a third timestamp flake** | 🔄 **Moderate / Medium** (impact raised) — Phase 3 is materially more time-dependent than any prior phase | Injected clock mandatory, **enforced by an AST structural test** (§9), not a convention |
| **`LR6`** | **Horizon-as-identity multiplies Specifications** — 4 horizons × 3 conventions × N signals | **Certain / Low** | Accepted per ADR R1. Making multiplicity legible is the point. Track registry growth as an operational metric, not a design problem |
| **`LR7`** | **The calendar trap** (§17) — a Specification not stating whether observation count or calendar span governs cannot be registered | **Low / Medium** — BTC trades 24/7, so the two coincide today | 🔄 **Promoted from a risk note to a mandatory Tier A field in WBS 3.1**, while it is free. Retrofitting after Specifications exist means new hashes for every one |
| **`LR8`** | **Phase 3 inherits `FS-GAP-003`** (equivalence test-enforced) | **Certain / Low** — `S-4`, by explicit choice | 🔄 **Two concrete re-open triggers** replace v1.0.0's untriggerable wording: (1) a **sixth** frozen implementation is added to `research/forward_evidence/`; (2) any equivalence test is skipped, quarantined, or marked `xfail`. Either re-opens `FS-GAP-003` as scoped work |
| **`LR9`** | **Scope inflation** — `ADR-I011` is larger than `ADR-I010` (six object classes vs five, two lifecycles vs one, ten mandatory fields vs eight) | **Moderate / Medium** — partially materialised: this revision added 3.8b, 3.10, and expanded 3.7 and 3.9 | Each addition closes a **mapped ADR requirement or a tracked gap**, not a new idea. §4.3's out-of-scope table is restated in full each revision. Path labels (§16.4) remain the likeliest creep vector and are pre-refused |
| **`LR10`** | **`ADR-I011` is Draft and factually wrong about this repository in two places** | **Confirmed / Medium** — materialised twice (`A-2`, `A-3`) | Build to the **verified repository** for facts, to the **ADR** for rules. Flag any further divergence rather than silently choosing |
| **`LR11`** | 🔄 **[NEW] Unmapped `ADR-I011` validation tiers** | **Closed by this revision / was Medium** | Tier B → 3.4 + 3.5; Tier D → 3.9 expanded. §14's self-review confirms all four tiers owned |
| **`LR12`** | 🔄 **[NEW] Forward-tier backup unassigned** | **Closed by this revision / was Severe** — the forward tier is irreplaceable from the first row, and `feature_store_backup_strategy.md`'s regenerability argument explicitly does not apply | WBS **3.10**, ordered **before** 3.4 within L4 |
| **`LR13`** | 🔄 **[NEW] Phase 3 begins atop four uncommitted phases** | **Moderate / Severe** | `E11` raised from "advised" to **blocking for L4 onward**. L1–L3 touch no real data and are safe to begin first |

---

## 12. Integration Points With the Feature Store

| # | Point | Nature | Direction |
|---|---|---|---|
| **I1** | **Registry pattern** | Mirrored, not imported (§6: *"No new governance mechanism is introduced"*) | Feature Store → Label Store, design-time only |
| **I2** | **`conditioning.py`** | **Imported unchanged.** Both ADRs require the same three propagation rules over the same ingest sources | Runtime import — the first cross-store code dependency |
| **I3** | **`research/admissibility/`** | 🔄 Consumed via a **store-agnostic** wrapper built in 3.7 and used by **both** stores | Shared dependency |
| **I4** | **`ret_fwd1` hand-off** | Excluded from the Feature Store by an import-time assertion; becomes a Label Specification in 3.3 | Feature Store → Label Store, one-time |
| **I5** | **`research/evidence/engine.py`** | Both stores wrap it, neither modifies it. C2's convention lives in `_net_daily()`; Phase 2's 17 features come from `load_master()` | Shared upstream — **must stay unmodified** |
| **I6** | **Receipt substrate** | 🔄 One mechanism, built in 3.8a, applied to labels **and** backfilled to features in 3.8b | Shared dependency |
| **I7** | **Dataset-ward composition** | Phase 4 composes feature ceilings, label ceilings, and evidence into one meet (DI5) | Both → Phase 4 |
| **I8** | **Deliberate non-integration** | The Label Store does **not** import `feature_store/registry.py` or `value_store.py`. §2's asymmetry means shared registry *code* would force feature semantics onto labels | Stated to prevent well-intentioned over-coupling |
| **I9** | 🔄 **[NEW] Authorised additive changes to Phase 2 code** | 3.7 attaches the admissibility ceiling at `migrate_evidence_engine_features.py`'s existing per-feature resolution point (where warm-up and conditioning already attach); 3.8b adds 17 bootstrap receipts. **Both additive; neither modifies `registry.py` or `value_store.py`** | Label Store → Feature Store, **deliberate and authorised** |

> **The integration risk worth naming.** Parent roadmap `R4` warns of registry-pattern *drift* between two independently-built stores. The answer is asymmetric and deliberate: **share what is genuinely generic** (conditioning, admissibility, receipts — `I2`, `I3`, `I6`) and **mirror rather than share what is semantically different** (`I1`, `I8`). Sharing the registry itself would be the wrong fix, because the two registries are not the same object — a Label Specification carries a maturity rule, a horizon, and three conventions that have no feature analogue.

---

## 13. Deliverable Definitions

### 13.1 Code

| Deliverable | Path | Owning WBS | Definition of complete |
|---|---|---|---|
| Label Registry | `research/label_store/registry.py` | 3.1 | Ten mandatory fields Tier-A-enforced; content-addressed; append-only triggers; `ADR-I010` §11 phase lifecycle; binding resolution refusing rather than substituting |
| Label Value Store | `research/label_store/value_store.py` | 3.2 | Research + forward tiers; six-state maturity lifecycle, **trigger-enforced**; both addressing forms explicit |
| Convention Specifications | *(data)* | 3.3 | C1, C2, C3 + `ret_fwd1` registered, four distinct hashes |
| H6 wrapper / migration | `research/label_store/migrate_h6_forward_labels.py` | 3.4 | Dry-run/`--commit`-gated, idempotent (proven twice), `bootstrap`-attested, original tables unmutated |
| Standing assignment | `research/label_store/standing.py` | 3.5 | Per-signal via `pre_reg_guard.py`; immutable after declaration |
| Superseded-input flagging | `research/label_store/superseded.py` | 3.6 | Records revision sets; flags on correction; `Flagged → Adjudicated` human-gated |
| Conditioning + admissibility | `research/label_store/propagation.py` | 3.7 | Imports `conditioning.py` unchanged; store-agnostic ceiling module applied to both stores |
| Receipts | `research/label_store/label_receipts.py` | 3.8a | `label.register` / `label.adjudicate` chained and replayable |
| Phase 2 receipt backfill | `research/evidence_library/migrate_bootstrap_feature_receipts.py` | 3.8b | 17 receipts, idempotent, chain valid |
| Set hygiene | `research/label_store/set_hygiene.py` | 3.9 | All four §12 Tier D statistics; partial-maturity refusal |
| Backup wiring | `scripts/backup.py`, `scripts/healthcheck.py`, `src/api/services.py` | **3.10** | Forward-tier DB named; freshness check in CLI and API; restore drill transcript committed |
| Rollout config | `research/label_store/label_policy.yaml` | 3.5, 3.7, 3.9 | Every `warn`-mode flag from §10, **with durable capture** |

### 13.2 Tests

One file per module under `tests/`, house style (`sys.path` insertion, `class TestXxx:`, plain `assert`, no `unittest.TestCase`). Base-env importable where no pandas dependency exists, gated per-class where it does. **Every refusal in §9's TV4 list gets one negative test.**

### 13.3 Documentation

| Deliverable | Path | Owning WBS |
|---|---|---|
| Verification record per milestone | `docs/engineering/verification/records/wbs-3.<n>-<slug>.md` | each |
| Label Migration Register | `docs/engineering/label_migration_register.md` | 3.4 |
| Maturity & Standing Contract | `docs/engineering/label_maturity_contract.md` — lifecycle, refusal, activation, rollback (mirrors `warmup_contract.md`) | 3.5, 3.9 |
| Label Store Technical Debt Register | `docs/engineering/label_store_technical_debt_register.md` | Phase 3 closeout |
| Phase 3 Closure Review | `docs/engineering/verification/records/phase-3-closure-review.md` — with an **Unowned** bucket | Phase 3 closeout |
| Phase 3 Closeout | `docs/engineering/verification/records/phase-3-closeout.md` | Phase 3 closeout |

### 13.4 Data artifacts

| Artifact | Owning WBS | Definition |
|---|---|---|
| `research/label_store/label_store.db` | 3.1, 3.2 | Registry + research tier. **Gitignored** — `.gitignore` addition required, mirroring `feature_store.db` |
| Forward-tier storage | 3.2, **`LS-DEC-001`** | 🔄 Location decided by `LS-DEC-001` before 3.10. Must avoid a third name confusable with the existing two (`LR4`) |
| Backup coverage | **3.10** | 🔄 **Now owned** (`D-4`). Unlike `feature_store.db`, the forward tier is **irreplaceable from the first row**; `feature_store_backup_strategy.md` §3.1's regenerability argument does not apply |

---

## 14. Self-Review

### 14.1 The five confirmations required of this revision

**1. Every `ADR-I011` requirement is owned.**

| Requirement | Owner |
|---|---|
| §1.1, §7, §7.1(9.1), §7.2(9.2), §7.3(9.3), §12.2, LG2, LG4 | **3.2** |
| §6, §6.1(7.1), §8.2, §10, §11.1, §11.2, §12.1, §16.1, §17, LG6, LG8 | **3.1** |
| §8.3, PS1, PS2 | **3.3** |
| §16.2, §14 (instants) | **3.4** |
| §10.1, LG3 | **3.5** |
| §11.3, DG3, PS4 | **3.6** |
| §10.2, §9.2, DG6 | **3.7** |
| LG1, §9 (registration receipt) | **3.8a** |
| §16.3, DG2, PS3 | **3.9** |
| §7 (forward-tier irreplaceability) | **3.10** |
| **§12 Tier A** | **3.1** |
| **§12 Tier B** | **3.4 + 3.5** ✅ *(was unowned — `D-1`)* |
| **§12 Tier C** | **3.2 + 3.4** |
| **§12 Tier D** | **3.9, all four checks** ✅ *(was 1-of-4 — `D-2`)* |
| §8.1 Promotion Labels — standing | **3.5**; use-counting → **Phase 4** |
| §9 value-set metadata — corpus version | **`FS-GAP-005`**, blocked; binding instruction in §4.3 |
| LG5, LG7, §15 DI1–DI8 | **Phase 4** |
| §16.4 path labels | **Deferred, research-question-gated** (§4.3) |
| §17 multi-asset beyond the calendar field | **Deferred** — asset is a resolution parameter; no multi-asset resolution exists yet |

✅ **Confirmed: every `ADR-I011` requirement has a named owner. Not every owner is Phase 3** — six are Phase 4, one is `FS-GAP-005`, two are explicitly deferred with a stated gate. **Zero are unowned.**

**2. Every WBS item has an owner.** ✅ All ten items (3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8a, 3.8b, 3.9, 3.10 — eleven including the split) carry an **Owns (`ADR-I011` §)** column in §4.1. Per the Readiness Review §2.1, this is requirement-ownership rather than a person column, since every human owner is Rohith.

**3. Every roadmap deliverable has an exit criterion.** ✅ §4.1 gives each WBS item a **separable** exit criterion — closing `D-5`'s finding that 3.5/3.9 and 3.6/3.7 previously shared sprint-level exits only. §13's three deliverable tables each carry an owning WBS, so no deliverable exists without one. Sprint-level criteria (§5) now aggregate item-level ones rather than substituting for them.

**4. Every transferred Phase 2 gap is explicitly tracked.** ✅ All seven Phase 2 register items are assessed in §1.3 (`FS-GAP-002` and `FS-GAP-005` restored, closing `D-3`): `FS-GAP-001` → 3.7 both stores; `FS-GAP-002` label side → 3.9, feature side explicitly **not absorbed** (§4.3); `FS-GAP-003` → deliberately inherited with two triggers (`LR8`); `FS-GAP-004` → 3.8a + 3.8b, closed in both stores; `FS-GAP-005` → blocked, with a binding do-not-invent instruction; `FS-TD-013`/`FS-TD-014` → do not recur, with reasons.

**5. No implementation has started.** ✅ `research/label_store/` does not exist. No code, test, schema, migration, or configuration file was created or modified in producing this revision. The only artifact written is this document.

### 14.2 Standard self-review

| Check | Result |
|---|---|
| Any architecture designed, amended, or newly decided? | **No.** Every object, field, rule, and refusal traces to a numbered `ADR-I011` section. Where this document departs from the ADR's *text* (`A-2`, `A-3`), it departs from a factual claim about a reference platform, not a design decision — and says so |
| Was existing numbering preserved? | **Yes.** 3.1–3.9 keep their IDs and intent. 3.8 → 3.8a/3.8b is the approved split; 3.10 is the single new number, for previously-unassigned work. No item was renumbered |
| Does it preserve existing behaviour? | **Yes.** Every item is additive. `LR1`'s mitigation is that H6 keeps running unchanged and the Label Store observes it. The two Phase 2 touches (`I9`) are additive and do not modify `registry.py` or `value_store.py` |
| Is scope creep pre-refused? | **Yes** — §4.3 names seven exclusions with an owner each; `LR9` acknowledges this revision grew the WBS and states the test each addition passed (closes a mapped requirement or tracked gap) |
| Are Phase 2's mistakes carried forward as lessons? | **Yes** — unsigned reviewer checklists (§8.1), warn-mode without durable capture (§10), timestamp flakes (`LR5` + AST guard), unowned gaps (§1.3, §14.1), uncaptured host evidence (`S-7`) |
| Were any files under `docs/architecture/` touched? | **No** |
| Does it begin Phase 3? | **No.** Planning only |

---

## 15. Status

**This roadmap is complete at v1.1.0. Phase 3 is not started.**

Readiness Review conditions:

| Condition | State |
|---|---|
| **`C-1`** — revise to v1.1 closing `D-1`…`D-6` | ✅ **Discharged by this revision** |
| **`C-2`** — record the E10 scope-transfer decisions | ✅ **Discharged** (§3) |
| **`C-3`** — execute the Phase 2 commit plan | ⏳ **Owed.** Blocks **L4 onward** only (`LR13`) |

**L1–L3 are authorised to begin.** They touch no real data, are independently revertible, and depend only on `E1`–`E10`, all satisfied. **L4 additionally requires `C-3`, `G-PROD`, host-verified production row counts, and `LS-DEC-001`.**

Three findings must be read before implementation, because each would produce wrong work if missed: **`A-1`** (the fill-once guard does not meet the ADR and cannot simply be "reused"), **`A-2`** (one of the three named conventions does not exist here), and **§4.4** (the 148 existing labels cannot honestly be classified as forward evidence).

| Role | Name | Date |
|---|---|---|
| Roadmap revision | Claude Opus 5 | 2026-08-05 |
| **Phase 3 authorisation** | **Rohith Suresh** | **_______________** |
