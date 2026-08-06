# Constitutional Gap Analysis — v1

| Field | Value |
|---|---|
| **Title** | Constitutional Gap Analysis — v1 |
| **Document Type** | Architecture Analysis |
| **Status** | Complete |
| **Version** | 1.0.0 |
| **Analysed** | 2026-08-04 |
| **Analyst** | Rohith Suresh — Owner / Architect · Conducted with Claude Opus 5 |
| **Scope** | IQROS Master Architecture v0.1.0 · `ADR-I014` · `ADR-I015` · Research Platform Review v1 |
| **Subject** | Unresolved findings `M4` (Lockbox vs Holdout) and `M5` (Delegation / Rule Namespace) |

> **This is analysis, not design.** No ADR is designed here, no rule is proposed, and no finding is resolved. The output is a determination of **document structure** and the reasoning behind it. Where a resolution would require a decision, that decision is named and explicitly left open.

> **No reviewed document was modified.** Only `docs/architecture/README.md` was updated, to index this analysis (§9).

---

## 1. Executive Assessment

**Neither of the two proposed options is correct as stated.**

The question offered two structures: two independent constitutional ADRs, or one constitutional ADR with multiple sections. Analysis finds that **`M4` and `M5` are not the same kind of gap**, and that `M5` **is not a single finding** — it decomposes into two parts with different constitutional status and different urgency.

**Recommended: three remediation vehicles, of two different document kinds.**

| Vehicle | Addresses | Kind | Force depends on |
|---|---|---|---|
| **V1** | `M4` — sealed-data mechanisms | Constitutional ADR, specifying `IQROS-P7` | IQROS adoption |
| **V2** | `M5b` — rule namespace and reference integrity | **Index amendment** (governance, not ADR) | **Nothing — immediate** |
| **V3** | `M5a` — upward obligation delegation | **Contingent** — see §6.3 | A determination that must precede it |

Two findings emerged during analysis that **refine the review's own statements**. Both are recorded in §7 and both strengthen the case for separate vehicles.

---

## 2. Method

| Check | Approach |
|---|---|
| Finding text | Extracted verbatim from Review v1 §14 |
| Constitutional anchors | Located the Article each finding implicates |
| Mechanism scope | Traced every rule identifier across document boundaries |
| Coupling | Tested whether either finding's resolution requires the other's |
| Urgency | Measured actual reference-integrity failures observed in the repository |

**Not performed:** any determination of *how* either finding should be resolved. That is design.

---

## 3. Finding M4 — Lockbox vs Holdout

### 3.1 Scope

Three distinct single-use or use-counted mechanisms exist across two ADRs:

| # | Mechanism | Defined in | Applies to |
|---|---|---|---|
| 1 | **Holdout partition** — single-use | `ADR-I012` `DG-7` | A **Training Dataset** partition role |
| 2 | **Experiment Dataset substrate** — use-counted | `ADR-I012` `DG-8` | Any Experiment Dataset consumed by a trial |
| 3 | **Lockbox** — single-use, opening is a human act | `ADR-I013` `LB1`–`LB6` | A **sealed region** on an Experiment Dataset |

The review stated that mechanisms 1 and 3 have "no stated relationship." **Analysis finds a stronger defect** — see §7.1.

### 3.2 Dependencies

| Dependency | Status |
|---|---|
| `IQROS-P7` — sealed regions enforced at the data boundary | Written, Draft |
| `ADR-I012` §15 — partition roles, Training Dataset definition | Written, Draft |
| `ADR-I013` §10 — lockbox rules | Written, Draft |
| `ADR-I014` — admissibility | **Not a dependency.** Sealed-data identity is orthogonal to admissibility |
| `ADR-I015` — receipts | **Weak.** Lockbox opening emits a receipt (`LB1`), but the receipt contract is agnostic to what a lockbox *is* |

**No unwritten dependency blocks `M4`'s resolution.** Everything required to settle it already exists in Draft.

### 3.3 Affected ADRs

| ADR | Involvement |
|---|---|
| `ADR-I012` | `DG-7`, `DG-8`, §15.1, `R5` risk note |
| `ADR-I013` | `LB1`–`LB6`, `EG8`, §19.2 dependency table |
| **Total** | **2 documents** |

`ADR-I009`, `ADR-I010`, `ADR-I011`, `ADR-I014`, `ADR-I015` are **unaffected**.

### 3.4 Future Impact

| Horizon | Impact |
|---|---|
| **Immediate** | A reader cannot determine whether opening a lockbox consumes a holdout, or whether both use-counts must be checked before a promotion |
| **Trading Platform series** | **High.** `ADR-I014` `SA3` makes live-capital deployment conditional on Evidence lineage. If a strategy's supporting evidence traces to a spent lockbox *and* a spent holdout, and the two are the same object counted twice, a deployment gate could double-count or under-count a single-use consumption |
| **ML readiness** | **High.** Training Dataset holdouts are the ML leakage defence. If lockbox semantics silently apply to them — or fail to — the leakage guarantee is not what it appears |
| **If left unresolved** | The defect propagates into every future ADR that cites either mechanism. It is **already** propagating: `ADR-I013` cites `ADR-I012`'s rule in three places |

### 3.5 Recommended Document Structure

> **A single constitutional ADR specifying `IQROS-P7`.**

This follows the established pattern precisely: `ADR-I014` specified `IQROS-P4`; `ADR-I015` specified `IQROS-P10`. `IQROS-P7` asserts that held-out data is sealed at the data boundary but does not define **what kinds of sealed data exist, whether they nest, or how consumption is counted**. That is the same shape of gap the two prior constitutional ADRs closed.

### 3.6 Justification

| Argument | Weight |
|---|---|
| **Single constitutional anchor.** `IQROS-P7`, uniquely | Strong |
| **Narrow, closed scope.** Two documents, three mechanisms, no unwritten dependency | Strong |
| **Precedent exists.** Two prior ADRs used exactly this pattern successfully | Strong |
| **Semantic, not structural.** Resolution changes what the architecture *means*, and must be citable as such | Strong |
| **Concrete defect present.** §7.1 shows this is not merely an undefined relationship | Decisive |

---

## 4. Finding M5 — Delegation and Rule Namespace

### 4.1 M5 Is Two Findings

The prompt frames `M5` as "Delegation / Rule Namespace." Review v1 classified these separately:

| Sub-finding | Review classification | Subject |
|---|---|---|
| **`M5a`** | `M5` — **Major** | Three lower-layer obligations delegate enforcement upward to `ADR-I012`, undeclared in their dependency headers |
| **`M5b`** | `m1` + `m2` — **Minor** | An internal identifier collision that crossed a document boundary; five governance-rule prefixes across five documents |

**The prompt's bundling is defensible for remediation** — both concern how documents refer to each other and name their rules. But the two halves have **different constitutional status**, which is the determining factor for document structure.

### 4.2 Scope — M5a (Delegation)

| Instance | Lower layer | Delegated to |
|---|---|---|
| Retention bounded by citations | `ADR-I009` §15.1 | `ADR-I012` citation index |
| Retirement requires zero citations | `ADR-I010` §11 | `ADR-I012` citation index |
| Superseded-input flag propagation | `ADR-I011` §10.3 | `ADR-I012` reverse index |

Ten upward citations total; three are load-bearing obligations.

### 4.3 Scope — M5b (Rule Namespace)

| Defect | Instances |
|---|---|
| Governance-rule prefix inconsistency | 5 conventions across 5 documents — `GV`, `FG`, `LG`, `DG-`, `EG` |
| Internal collision | `ADR-I012`: `DG1`–`DG9` (goals) vs `DG-1`–`DG-8` (governance) |
| Collision crossing a boundary | `ADR-I011` `DI7`/`DI8` and `ADR-I013` `LB4`/`EG8` cite into the colliding namespace |
| **Governed by** | `docs/architecture/README.md` §8–§9 — 14 existing rules in this family |

### 4.4 Dependencies

| Dependency | For | Status |
|---|---|---|
| **IQROS Article 4** — plane dependency direction | `M5a` | Written, Draft — **and possibly implicated, see §7.2** |
| Architecture Documentation Index §8–§9 | `M5b` | **Active** — in force now |
| `ADR-I012` §6.1 — citation index | `M5a` | Written, Draft |
| **A prior determination** | `M5a` | **Unwritten — see §6.3** |

### 4.5 Affected Documents

| Sub-finding | Affected |
|---|---|
| `M5a` | `ADR-I009`, `I010`, `I011`, `I012` — **4 ADRs** |
| `M5b` | `ADR-I009`–`ADR-I015` — **7 ADRs**, plus the Index, plus **every future ADR** |

`M5b`'s scope is unbounded forward: it governs documents not yet written.

### 4.6 Future Impact

| Horizon | Impact |
|---|---|
| **Immediate — `M5b`** | **Already realised.** See §7.3 — eighteen reference defects were observed and repaired in a single working session |
| **Immediate — `M5a`** | Latent. A reader of `ADR-I009` alone cannot know its retention rule is unenforceable without a document two layers above |
| **Trading Platform series** | **`M5b` compounds.** Each new ADR adds citations into an ungoverned namespace. Cost scales with the number of written ADRs — measured at 13 repairs for one allocation |
| **`M5a` at Trading scale** | If upward delegation is permitted but undeclared, a Trading ADR could delegate a capital-consequence obligation upward with no declared dependency |
| **If left unresolved** | `M5b` degrades monotonically and cannot be retrofitted cheaply — every future citation compounds it |

### 4.7 Recommended Document Structure

> **`M5b` — an Index amendment. Not an ADR.**
>
> **`M5a` — contingent. Its document kind cannot be determined until a prior question is settled (§6.3).**

### 4.8 Justification

**Why `M5b` must not be an ADR:**

| Argument | Weight |
|---|---|
| **Force timing.** ADRs are Draft with no force until IQROS adoption. The Index is **Active now**. A naming convention is needed for the *next* ADR written — not after adoption | **Decisive** |
| **Subject match.** The Index already governs naming (`N1`–`N3`), numbering (`NS1`–`NS8`), status (`ST1`), versioning (`V1`–`V4`), and reviews (`RV1`). Rule-identifier naming is the same category | Strong |
| **Not architectural.** A prefix convention decides nothing about the system — only about how documents are written | Strong |
| **Precedent.** `NS8` was added to the Index mid-session when the need arose, and took effect immediately | Strong |

**Why `M5a` is contingent:** see §6.3.

---

## 5. Coupling Analysis

The determining question for one-versus-two is whether the findings are one problem.

| Test | `M4` | `M5` | Coupled? |
|---|---|---|---|
| Constitutional anchor | `IQROS-P7` | Article 4 *(possibly)* + Index | **No** |
| Kind of gap | Semantic — what the architecture *means* | Structural — how it is *written* | **No** |
| Affected documents | 2 | 4 (`M5a`) / 7+unbounded (`M5b`) | **No** |
| Resolution requires the other? | No | No | **No** |
| Shared rules or vocabulary | None | None | **No** |
| Lifecycle | One-time definitional decision | `M5b` is **ongoing** governance | **No** |
| Urgency | Latent, high future impact | `M5b` **already realised** | **No** |
| Force dependency | Requires IQROS adoption | `M5b` requires **nothing** | **No** |

**Zero of eight coupling tests indicate a shared problem.**

### 5.1 The Argument for Bundling, Weighed

One real argument favours a single document: **each new ADR carries a measured cross-reference cost.** The `ADR-I012` allocation alone required 13 repairs (§7.3). Fewer documents means less governance surface.

**This argument is rejected**, for three reasons:

1. **It optimises the wrong quantity.** Document count is cheap; a grab-bag ADR that cannot be superseded independently is expensive forever.
2. **It would bundle a Draft with an Active need.** `M5b` cannot wait for IQROS adoption, and bundling it into a Draft ADR would delay it indefinitely.
3. **It inverts the causation.** The cross-reference cost *is* `M5b`. Using that cost as a reason to bundle `M5b` into another document leaves the cost in place.

---

## 6. Recommendation

### 6.1 Determination

> **Neither proposed option. Three vehicles, two document kinds.**

| Vehicle | Addresses | Kind | Anchor | Timing |
|---|---|---|---|---|
| **V1** | `M4` | Constitutional ADR | Specifies `IQROS-P7` | After the §6.3 determination is unnecessary — may proceed now |
| **V2** | `M5b` | **Index amendment** | Index §8–§9 | **Immediate** — highest priority |
| **V3** | `M5a` | **Contingent** | Article 4 *or* Index | **Blocked** on §6.3 |

### 6.2 Priority Order

| Order | Vehicle | Rationale |
|---|---|---|
| **1** | **V2** (`M5b`) | Only item whose cost is *already being incurred*; only item needing no constitutional force; cost compounds with every document written |
| **2** | **V1** (`M4`) | Concrete defect present (§7.1); blocks nothing but propagates into the Trading series |
| **3** | **V3** (`M5a`) | Blocked on a determination; latent rather than realised |

### 6.3 The Determination V3 Is Blocked On

`M5a`'s document kind **cannot be decided by this analysis**, because it depends on a question this analysis must not answer:

> **Does IQROS Article 4's "dependencies flow strictly upward" rule govern *obligation delegation*, or only *implementation dependency*?**

| If the answer is | Then `M5a` is | And the vehicle is |
|---|---|---|
| **Article 4 governs only implementation dependency** | A documentation defect — obligations may be delegated upward; they must simply be declared | **Index rule.** Fold into V2 |
| **Article 4 governs obligation delegation** | An Article 4 **conformance gap** — three ADRs delegate in a direction the table does not permit | **Constitutional ADR** or an amendment. Separate from V1 |

**This determination must precede V3 and is a governance decision, not an analytical one.** See §7.2 for why the question is live rather than academic.

---

## 7. Findings That Refine Review v1

Three observations emerged during analysis that were not in Review v1. All are recorded because they bear on the recommendation.

### 7.1 `M4` Is a Scope Mismatch, Not Only an Undefined Relationship

Review v1 stated that holdout and lockbox have *"no stated relationship."* **Analysis finds a partial relationship that is stated — and is scope-mismatched.**

`ADR-I012` `DG-7` reads:

> *"**Holdout partitions are single-use.** Consumption is recorded against the consuming decision (§15)."*

`ADR-I012` §15 is *Research vs Training Dataset*. `DG-7` is therefore scoped to **Training Dataset partition roles**.

`ADR-I013` `LB4` reads:

> *"Use is **counted structurally** (`ADR-I012` DG-7). A spent lockbox is permanently marked."*

But `LB2` establishes that opening a lockbox *"produces a **new Experiment Dataset**."* A lockbox is a **sealed region on an Experiment Dataset** — not a Training Dataset partition role.

> **`ADR-I013` therefore applies a Training-Dataset-scoped rule to an object that is not a Training Dataset partition.**

This is a **defect**, not merely an ambiguity. It raises `M4`'s severity in practical terms and makes V1 more clearly necessary. *(Note: `EG8`'s citation of `DG-8` is scope-correct — `DG-8` governs Experiment Dataset substrate reuse, which is what `EG8` invokes.)*

### 7.2 `M5a` May Implicate Article 4, Not Only Documentation

Review v1 classified `M5a` as *"a documentation-integrity issue."* Analysis finds it **may be more.**

IQROS Article 4.1 states that the seven planes have *"dependencies flowing **strictly upward** in this table,"* where Storage *"may depend on"* Acquisition, and Research *"may depend on"* Evidence.

`ADR-I009` occupies the **Storage** plane. Its retention obligation is enforced by `ADR-I012`'s citation index, which occupies the **Research** or **Governance** plane — both of which appear *later* in the table.

> **Storage depending on Research or Governance is not a dependency Article 4's table permits.**

Whether this constitutes non-conformance turns entirely on whether Article 4's rule governs *obligation delegation* or only *implementation dependency* — the determination named in §6.3. **This analysis does not resolve it**, but records that `M5a` may be an Article 4 conformance question rather than a purely editorial one, which is why V3's document kind is contingent.

### 7.3 `M5b`'s Cost Is Measured, Not Projected

Review v1 noted that `M5`'s cost *"has already been demonstrated once in this repository's history."* **Analysis finds three distinct events in a single working session**, totalling eighteen reference defects:

| Event | Trigger | Defects |
|---|---|---|
| `ADR-I012` allocation | Number reallocation invalidated citations across three written ADRs | 10 stale citations + 3 stale forward-references = **13** |
| Index section renumber | Inserting a Reviews section shifted section numbers cited by four ADRs | **4** |
| `ADR-I015` internal renumber | A blanket section-reference shift corrupted one cross-document reference *(and coincidentally corrected two others)* | **1** |
| **Total** | | **18** |

Every one was caught only because a governance pass ran explicitly. **None would have been caught by reading any single document.**

This is the strongest available evidence for V2's priority: the cost is not hypothetical, it is recurring, and it scales with document count.

---

## 8. What This Analysis Does Not Decide

| Not decided | Belongs to |
|---|---|
| Whether a lockbox **is** a holdout, nests within one, or is orthogonal | V1 |
| How sealed-data consumption should be counted | V1 |
| Whether Article 4 governs obligation delegation | §6.3 determination — a governance decision |
| Whether upward delegation should be **permitted** or **removed** | V3, after §6.3 |
| Any rule-prefix convention | V2 |
| Whether `m3`, `m4`, `O1`–`O4` warrant remediation | Out of scope — not requested |

---

## 9. Documentation Governance

| Action | Status |
|---|---|
| Analysis indexed in `README.md` §6 | ✅ Applied |
| Review v1 modified | ❌ **No** — `RV1`; findings' disposition tracked in the Index, not by editing the review |
| `ADR-I014`, `ADR-I015`, IQROS modified | ❌ **No** |
| New naming convention established | `reviews/<subject>-<kind>-v<N>.md` — consistent with `RV1` |
| Links verified | ✅ All resolve |

---

## 10. Self-Review

| Check | Result |
|---|---|
| Six required elements per finding — scope, dependencies, affected ADRs, future impact, recommended structure, justification | ✅ `M4` §3.1–§3.6; `M5` §4.2–§4.8 |
| One-vs-two determination made | ✅ §6.1 — **neither**, with reasoning |
| Any ADR designed? | ✅ **No** — §8 enumerates what was deliberately not decided |
| Any reviewed document modified? | ✅ **No** |
| Findings grounded in extraction, not recollection | ✅ §2; §7.1 and §7.2 derive from tracing rule identifiers across boundaries |
| Analyst independence | ⚠️ **Limited, and stated.** The analyst drafted the documents under analysis. Compensated by mechanical extraction; §7.1–§7.3 are defects *against* that prior work, found by tracing rather than recall |
| Prompt framing accepted uncritically? | ✅ **No** — §4.1 records that the prompt's `M5` bundling merges a Major and two Minor findings, and explains why the bundling is defensible but not determinative |

---

*This analysis creates no architecture, designs no ADR, and resolves no finding. It records a structural determination and the reasoning behind it, as of 2026-08-04.*
