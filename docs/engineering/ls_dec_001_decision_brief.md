# Architecture Decision Brief — `LS-DEC-001`

| Field | Value |
|---|---|
| **Decision ID** | `LS-DEC-001` |
| **Title** | Forward-tier storage location |
| **Status** | **Decision brief produced — awaiting Rohith's ruling** |
| **Owner** | Rohith Suresh (per `phase_3_label_store_roadmap.md` v1.1.0 §4.2: "Rohith, on the L2 recommendation") |
| **Due** | Before WBS 3.10 |
| **Document Type** | Engineering decision brief (not an ADR, not a roadmap edit) |
| **Author** | Claude Sonnet 5 |
| **Date** | 2026-08-06 |
| **Roadmap reference** | [`phase_3_label_store_roadmap.md`](phase_3_label_store_roadmap.md) v1.1.0 §4.2, §1.2 `LR4`, §13.4 |
| **Code written** | **None.** This is a decision brief only — no implementation, no ADR edit, no roadmap edit |

> **What this document is not.** It does not modify `ADR-I011` or any other ADR. It does not modify `phase_3_label_store_roadmap.md`. It does not implement WBS 3.10 or any part of it. It resolves exactly one open decision the roadmap already named and deferred to Rohith, using evidence gathered from the repository as it exists today — not from a hypothetical future architecture.

---

## 1. What exactly is `LS-DEC-001`?

Verbatim from the roadmap's own decisions register (§4.2):

> **`LS-DEC-001`** — Forward-tier storage location — co-locate in `label_store.db`, or a separate `label_forward.db`. Bears on `LR4` (transposition risk) and on 3.10's backup shape.

In concrete terms: `research/label_store/registry.py` (WBS 3.1) and `research/label_store/value_store.py` (WBS 3.2) each expose their own `init_db(path)`, and neither module currently forces or forbids a particular file layout. Four tables exist across the two modules:

| Table | Module | Content |
|---|---|---|
| `label_definitions` | `registry.py` | Semantic Label Definitions (versioned, append-only) |
| `label_specifications` | `registry.py` | Content-addressed Label Specifications (Tier A validated) |
| `label_value_research` | `value_store.py` | Research Label values (evictable cache, §9.3-verified) |
| `label_value_forward` | `value_store.py` | Forward Label values (six-state maturity lifecycle, storage-layer fill-once) |

`LS-DEC-001` asks: do all four tables live in **one** physical SQLite file, or does the forward tier (`label_value_forward`) live in a **second**, separate file?

This is a **physical storage-layout decision**, not a schema, API, or ADR-level decision. Every function signature in `registry.py`/`value_store.py` already accepts an arbitrary `db_path` — nothing about either module's code changes under any option below. What changes is which path `register_conventions.py` (WBS 3.3), the eventual H6 wrapper (WBS 3.4), and the backup/restore wiring (WBS 3.10) are told to use.

---

## 2. What architectural problem is it solving?

Two distinct problems, both named explicitly in the roadmap:

### 2.1 `LR4` — the transposition hazard this repository has already been burned by once

Verbatim, roadmap §1.2 finding `A-9`:

> `research/data/forward_evidence.db` (H6, `schema.sql`) and `research/data/evidence_forward.db` (multi-signal, `schema_forward.sql`) — **the names are transpositions**, and **four table names collide with different columns**. ... An operator-error hazard, pre-existing.

And `LR4` itself (§11):

> **`forward_evidence.db` / `evidence_forward.db` transposition** causes a write to the wrong database | Moderate / Severe | No default `--db` in any Phase 3 script; assert expected schema shape before any write; refuse on mismatch. **Not** solved by renaming (unrelated refactoring). **`LS-DEC-001` should avoid a third confusable name.**

`LS-DEC-001` exists because introducing a *second* Label Store database file risks recreating exactly this hazard a third time, in the same directory, for the same subsystem — unless the naming and layout are deliberately chosen to avoid it.

### 2.2 `ADR-I011` §7's storage-obligation asymmetry, and what it means for backup design

`ADR-I011` §7 splits the Label Value Store into two tiers **by obligation, not convenience**:

| Tier | Retention | Authority | If lost |
|---|---|---|---|
| Research tier | Evictable cache | None | **Nothing lost** |
| Forward tier | Indefinite | None — verifiable, not authoritative | **Irreplaceable** |

The registry (`label_definitions`/`label_specifications`) has a *third* recoverability property, distinct from either tier, established directly by this session's own WBS 3.3 work: it is **100% regenerable** by re-running `register_conventions.py --commit` (an idempotent, git-committed, deterministic script — verified this session by running `--commit` twice against a throwaway database and confirming identical row counts and identical hashes both times).

So the Label Store's four tables span **three different recoverability classes** in one candidate file:

1. Registry — regenerable by script (like `feature_store.db` today)
2. Research tier — evictable cache, nothing lost if gone
3. Forward tier — **irreplaceable from the first row** (roadmap §13.4, `D-4`)

`LS-DEC-001` is the question of whether that spread of criticality should live in one backup unit or be split so the truly irreplaceable data can be isolated. This directly gates WBS 3.10 (backup wiring), whose own entry criterion (§4.1) is **"3.2 merged; `LS-DEC-001` resolved"** — 3.10 cannot be scoped until this is decided.

---

## 3. What repository evidence requires this decision?

Evidence gathered this session, not assumed:

### 3.1 The Feature Store precedent is *not* the clean analogy it might appear to be

`research/feature_store/registry.py` and `research/feature_store/value_store.py` are structurally identical in shape to their Label Store counterparts — but querying the real, locally-materialised `research/feature_store/feature_store.db` (created by the actual `migrate_evidence_engine_features.py --commit` run) shows:

```
tables: ['feature_definitions', 'feature_specifications']
  feature_definitions: 17 rows
  feature_specifications: 17 rows
```

**Zero value-store tables exist in the real file.** `feature_value_cache`/`feature_value_materialized` have never been created there — `value_store.init_db()` has never been called against `feature_store.db` in real usage. This matches the already-documented finding `FS-TD-014` ("Value Store has no tenant") and `feature_store_backup_strategy.md` §3.1's own confirmation. **The honest conclusion: Feature Store has never actually exercised co-location of its registry and value store in practice — the question was structurally possible but never tested, because the value store has no real data yet.** This precedent cannot be cited as proof that co-location works cleanly; it can only be cited for the pattern it *did* establish (below).

### 3.2 `ledger.db` — the actual, heavily-exercised precedent for mixed-criticality co-location

`ledger.db` is the one database in this repository that **has** been exhaustively exercised: 70 runs, 142 gate_results, 26 governance receipts, 13 state transitions, all co-located in one file, under one unified backup/off-host/DR policy (Program P1, `CLAUDE.md` §18). Its rows span meaningfully different criticality (append-only receipts vs. mutable-shaped `pairwise_r` vs. evaluation runs), yet nobody has ever proposed splitting it by table criticality — the platform's own most-audited database uses exactly the "co-locate, protect the whole file at the strictest resident's level" pattern.

### 3.3 `scripts/backup.py` / `scripts/restore.py` operate at file granularity, not table granularity

Verified directly against source:

- `run_backup(db_path, backup_dir, ..., db_label=...)` (`scripts/backup.py`) performs one `VACUUM INTO` per **physical file**, with its own dedicated `backup_dir` and `db_label`. Four databases are wired in today (`btc_trading`, `evidence` = `forward_evidence.db`, `fwd_hg` = `evidence_forward.db`, `ledger`), each via its own named call site.
- `run_restore(backup_file, db_path, ...)` (`scripts/restore.py`) swaps exactly **one file**, with one `.pre-restore` safety copy, one `PRAGMA integrity_check`, one systemd service stop/start.

There is no mechanism anywhere in this codebase for applying a different backup cadence, retention policy, or off-host decision to a subset of tables within one file. **The backup/DR granularity this decision actually controls is: how many files, not how many tables.**

### 3.4 No cross-module foreign key exists today, under either option

Checked directly: `research/label_store/value_store.py` declares **zero** `FOREIGN KEY` constraints referencing `registry.py`'s tables (mirroring `feature_store/value_store.py`'s identical, documented scoped simplification — "this module holds no binding-resolution logic of its own"). `label_value_forward.spec_hash` is not SQLite-enforced against `label_specifications.spec_hash` today, regardless of file layout. **Co-location does not currently buy referential integrity — only atomic backup/restore as one unit.** This distinction matters because it would be easy to overstate co-location's benefit; the evidence does not support that overstatement.

### 3.5 This repository's code has already, informally, converged on one file

- `register_conventions.py`'s only hardcoded default: `_DB_PATH = _THIS_DIR / "label_store.db"`.
- `.gitignore` (added at WBS 3.1) covers exactly one filename pattern: `research/label_store/label_store.db{,-wal,-shm}`. No `label_forward.db` entry exists.
- Every WBS 3.1/3.2/3.3 test fixture that needed a path used `"label_store.db"`.

None of this was a deliberate `LS-DEC-001` ruling — it was the path of least resistance during three milestones that never needed to write forward-tier data for real. But it is real, existing repository state that any option below must account for (Option 2/3 require a rename or an added file; Option 1 requires nothing).

### 3.6 `ADR-I012` and `ADR-I013` resolve the Label Store by content hash, never by file path

`ADR-I012` §6/§6.1: the Dataset Builder's citation index is its **own** registry, addressed by Specification hash, resolving "forward" (what does a dataset cite) and "reverse" (what cites this input) edges without any reference to physical file layout.

`ADR-I013` §19.3, verbatim:

> | May read | May **not** read |
> |---|---|
> | Sealed Research Datasets | Operational databases — ever, by any path |
> | Sealed Experiment Datasets | Feature Store directly |
> | Its own registries | **Label Store directly** |

The Experiment Framework is structurally forbidden from touching the Label Store by **any** path — it only ever consumes sealed Datasets. This is decisive evidence that `LS-DEC-001` has **no consequence above the Dataset Builder's own resolution layer** — confirmed by ADR text, not inferred.

---

## 4. Every viable option

### Option 1 — Full co-location (single `label_store.db`)

**Architecture.** All four tables (`label_definitions`, `label_specifications`, `label_value_research`, `label_value_forward`) live in one SQLite file, `research/label_store/label_store.db`. This is the file layout already implied by every WBS 3.1–3.3 default path.

| Dimension | Assessment |
|---|---|
| **Advantages** | Matches the already-exercised `ledger.db` precedent exactly (§3.2). Zero rework of existing WBS 3.1–3.3 code, paths, or `.gitignore`. Structurally eliminates `LR4`'s transposition risk — there is no second Label Store filename to confuse with anything. One atomic backup/restore unit: a restore always leaves the registry, research cache, and forward tier mutually consistent at the same instant, with zero coordination risk (relevant given §3.4's finding that no FK enforces this today). |
| **Disadvantages** | The registry (regenerable-by-script) and research tier (evictable, "nothing lost") inherit whatever backup/off-host policy the forward tier's irreplaceability demands — no way to apply a lighter policy to the disposable portion without leaving the whole file unprotected. |
| **Operational impact** | One file to monitor, one freshness check, one `db_label` in `scripts/backup.py`/`healthcheck.py`. Smallest WBS 3.10 implementation surface — mirrors `feature_store_backup_strategy.md` §4's own "small, mechanical PR" framing, scaled to one file instead of two. |
| **Backup implications** | One `run_backup()` call site, one retention policy, one manifest. The "waste" of backing up regenerable/disposable rows alongside irreplaceable ones is real but low-severity at this scale — `feature_store.db` is 40 KB for 17 rows; even with the full 148 H6 labels wrapped, `label_store.db` will remain a small file. |
| **Disaster recovery implications** | Single-file restore (`scripts/restore.py`, unchanged shape). No cross-file consistency window — the forward tier and the registry it references are always restored to the same instant together. |
| **Dataset Builder implications** | **None.** `ADR-I012` §6 resolves by Specification hash through the Label Store's own Python API, never by direct file path (§3.6). |
| **Experiment Framework implications** | **None.** `ADR-I013` §19.3 forbids the framework from touching the Label Store by any path at all (§3.6). |
| **Future ML implications** | **None directly.** ML-adjacent work (Evidence Library's Validated/ML-readiness gate) sits behind the same sealed-dataset boundary `ADR-I012`/`ADR-I013` establish; this decision is invisible above that boundary under every option. |
| **Production deployment implications** | No new systemd unit or deploy-time decision beyond what WBS 3.10 will need regardless — one `ReadWritePaths` entry, one file to name in whichever service eventually writes forward-tier data. |

### Option 2 — Forward tier isolated (`label_store.db` + a second file for `label_value_forward` only)

**Architecture.** `label_definitions`, `label_specifications`, and `label_value_research` remain in `label_store.db`; `label_value_forward` alone moves to a dedicated second file (the roadmap's own suggested name, `label_forward.db`, or a deliberately less-confusable alternative — see Disadvantages).

| Dimension | Assessment |
|---|---|
| **Advantages** | Enables a genuinely different backup/off-host policy for the one table that actually needs it, without over-protecting the regenerable/disposable majority of the store — the only real technical capability co-location cannot offer (§3.3: backup granularity is per-file). Matches `feature_store_backup_strategy.md`'s own doctrine of not building backup infrastructure for regenerable data until it's warranted. |
| **Disadvantages** | Directly risks the hazard `LR4` names by name: a second, `label_`-prefixed filename living beside `label_store.db` is structurally the same shape as the `forward_evidence.db`/`evidence_forward.db` pair that already caused a real incident in this repository (§1.2 `A-9`). The roadmap's own suggested name, `label_forward.db`, is only one word different from `label_store.db` — choosing it without deliberate care would satisfy the letter of "a separate file" while reintroducing the exact risk `LS-DEC-001` was named to avoid. Requires renaming/repointing the already-converged default path in `register_conventions.py` and updating `.gitignore`. |
| **Operational impact** | Two files to monitor, two freshness checks, two `db_label`s. Roughly 1.5–2× the WBS 3.10 implementation surface of Option 1 (each parameter in `run_backup()`/`healthcheck.py` is already generic, so this is additive wiring, not new logic — but it is a second call site, a second thing that can silently stop being monitored). |
| **Backup implications** | Two independent backup units. The forward tier can be given strict, no-N/A-path treatment (mirroring `ledger.db`'s DBV5 discipline) from its very first row, without that policy's cost being paid by the registry/cache portion. |
| **Disaster recovery implications** | A real DR event now requires two `restore.py` invocations. Since no FK ties `label_value_forward.spec_hash` to `label_specifications.spec_hash` today (§3.4), restoring the two files from different points in time (an operator restoring only the file they remember, or restoring them from backups taken on different schedules) could leave a forward-tier row referencing a Specification hash not yet present in a stale-restored registry — a consistency-window risk that Option 1 cannot have by construction. |
| **Dataset Builder implications** | **None** — same reasoning as Option 1 (§3.6). |
| **Experiment Framework implications** | **None** — same reasoning as Option 1 (§3.6). |
| **Future ML implications** | **None directly** — same reasoning as Option 1. |
| **Production deployment implications** | Two files to name in `ReadWritePaths` and any eventual systemd unit; two paths for an operator to get right during any future host-side migration or activation, mirroring the exact operator-error shape `LR4` already documents for the H6 pair. |

### Option 3 — Split by module (`label_store.db` = registry; `label_value.db` = both value-store tiers)

**Architecture.** `label_definitions`/`label_specifications` in `label_store.db`; `label_value_research` **and** `label_value_forward` together in a second file, e.g. `label_value.db`.

This is a plausible reading of "separate the two Python modules onto separate files," distinct from the roadmap's own literal wording (which names the **forward tier** specifically, not "the value store" as a whole) — named here because it is a real, distinguishable alternative a future reader might reach for, not because the roadmap suggests it.

| Dimension | Assessment |
|---|---|
| **Advantages** | Mirrors module boundaries exactly (one file per Python module's `init_db()`). Marginally cleaner conceptual story than Option 2 ("registry file" vs. "values file") for someone unfamiliar with the tier-criticality distinction. |
| **Disadvantages** | **Does not solve the problem `LS-DEC-001` exists to solve.** It still bundles the evictable, nothing-lost-if-gone research tier with the irreplaceable forward tier in one file — meaning that file still inherits the forward tier's strict backup obligation, gaining none of Option 2's actual backup-differentiation benefit, while paying Option 2's full cost (a second, confusable filename, §1.2 `A-9`-shaped risk, doubled WBS 3.10 surface, a DR consistency window). Strictly dominated by both other options: it has neither Option 1's simplicity nor Option 2's genuine backup-policy benefit. |
| **Operational impact** | Same two-file operational burden as Option 2, for a weaker justification. |
| **Backup implications** | The research tier's cache traffic (frequent, low-stakes writes/evictions) shares a backup unit with the forward tier's rare, precious writes — the file's write/change frequency is dominated by cache churn that carries no obligation at all (§7: "if lost, nothing is lost"), which is a worse backup-cost profile than either Option 1 (accepted deliberately, for simplicity) or Option 2 (avoided deliberately, for precision). |
| **Disaster recovery implications** | Same two-file consistency-window risk as Option 2 (§3.4), again without Option 2's compensating benefit. |
| **Dataset Builder implications** | **None** — same reasoning as Options 1/2. |
| **Experiment Framework implications** | **None** — same reasoning as Options 1/2. |
| **Future ML implications** | **None directly** — same reasoning as Options 1/2. |
| **Production deployment implications** | Same two-file deployment burden as Option 2, without Option 2's justification. |

---

## 5. Recommendation

**Option 1 — full co-location in a single `label_store.db` — is recommended.** The repository evidence supports this clearly enough to recommend rather than merely present neutrally:

1. **The only heavily-exercised precedent in this repository for mixed-criticality data (`ledger.db`) already uses this exact pattern**, successfully, across Program P1's full audit history (§3.2). There is no equivalent precedent anywhere in this repository for the "isolate the irreplaceable table" pattern Option 2 would introduce.
2. **`LR4` is a first-party, already-triggered risk** (§1.2 `A-9`) — this repository has already suffered the exact hazard a second Label Store filename risks recreating. Option 1 is the only option that eliminates this risk structurally rather than managing it carefully.
3. **The Dataset Builder and Experiment Framework are provably indifferent to this decision** (§3.6, verified against `ADR-I012`/`ADR-I013` text directly) — there is no offsetting architectural benefit to separation at any layer above the Label Store's own storage.
4. **The genuine technical benefit of Option 2 (differentiated backup policy) is real but not yet warranted at this data scale**, by this repository's own stated doctrine: `feature_store_backup_strategy.md` explicitly counsels against building backup infrastructure ahead of need ("protecting against a risk that does not exist yet"). Applying the forward tier's strict policy to the whole (small) file is a bounded, low-severity cost today.
5. **Option 1 requires zero rework** of the already-converged WBS 3.1–3.3 default paths, `.gitignore`, and test conventions; both other options require a deliberate rename and new wiring.

**Reopening trigger, named rather than left implicit** (mirroring the discipline `feature_store_backup_strategy.md` §3 already established for its own analogous question): if the research tier's cache write/eviction volume grows large enough to make co-located backup cadence genuinely wasteful — or if the forward tier's own backup/off-host requirements diverge materially from what a shared file can serve — this decision should be revisited **against real measured volume**, not speculatively. Nothing in the repository today (a 4-row registry, zero forward-tier rows yet) indicates that trigger is close.

**Where the evidence does not fully settle the question:** if a future reading of `IQROS-P1` (Platform Sovereignty) or a stricter interpretation of §7's "if lost — Irreplaceable" language were held to require *physically dedicated* infrastructure for the forward tier specifically, Option 2 would be defensible on those grounds. Nothing in `ADR-I011`'s actual text makes that requirement explicit, and no other database in this repository is held to that standard — so this brief does not treat it as a repository-evidence-backed objection, but it is named here rather than silently dismissed.

---

## 6. What this brief does not do

- It does not modify `ADR-I011` or any other ADR.
- It does not modify `phase_3_label_store_roadmap.md` — §4.2's `LS-DEC-001` row remains **Owed** until Rohith rules on this brief and a future, explicitly-authorized session records the outcome there.
- It does not implement WBS 3.10, change any default path in `register_conventions.py`, or touch `.gitignore`.
- It invents no architecture beyond the three options a plain reading of the roadmap's own wording and this repository's existing backup/restore mechanics actually support.
