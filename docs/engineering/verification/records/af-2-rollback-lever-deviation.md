# `AF-2` — Rollback-Lever Deviation Record

| Field | Value |
|---|---|
| **Finding** | `AF-2`, Phase 4 Final Engineering Audit (2026-08-08) |
| **Subject** | `research/dataset_builder/dataset_policy.yaml` and the three configuration-flag rollback levers declared for WBS 4.2, 4.4 and 4.7 |
| **Type** | **Deviation record.** States what was built versus what was declared. **Ratifies nothing** |
| **Created** | 2026-08-08 |
| **Decision required from** | Rohith — accept the deviation, or schedule implementation |
| **Status** | **OPEN — awaiting owner acceptance** |

---

## 1. What was declared

Roadmap §13.1 lists a deliverable:

> | Rollout config | `research/dataset_builder/dataset_policy.yaml` | 4.2, 4.4, 4.7 |

Roadmap §10 (Rollback Strategy) declares three levers that would live in it:

| Mechanism | Declared lever | Declared effect when disabled |
|---|---|---|
| Citation index (4.2) | `citation_enforcement: false` | Index maintained, retirement not gated |
| Validation + RF4 (4.4) | `admissibility_enforcement: false` | Meet computed and recorded, not gating |
| RF3 (4.7) | `lane_enforcement: false` | Lane recorded, not gating |

## 2. What was built

**None of it.** Verified from source at commit `82ce7c0`:

- `research/dataset_builder/dataset_policy.yaml` — **does not exist**, anywhere
  in the repository.
- `citation_enforcement` — **zero occurrences** in any `.py` or `.yaml`.
- `lane_enforcement` — **zero occurrences**.
- `admissibility_enforcement` — one occurrence, in
  `research/label_store/propagation.py:109`, inside a **comment quoting the
  Label Store's own Phase 3 lever**. It is not the Dataset Builder's flag and
  does not gate anything in the builder.

## 3. What was built instead

Each of the three milestones recorded its rollback as *"purely additive"* —
`RC6` in `wbs-4.2`, `wbs-4.4`, and `wbs-4.7`. In practice the rollback for all
three is **revert the commit**:

| Milestone | Actual rollback | Effect |
|---|---|---|
| 4.2 | `git revert a203038` | Citation index removed entirely; no consumer existed |
| 4.4 | `git revert 80b5382` | Validation tiers removed; sealing did not yet exist |
| 4.7 | `git revert 5ece707` | RF3 removed; it is `not_evaluable` on every real declaration anyway (`TD-4.7-1`) |

Revert is a **valid** rollback. It is not the **declared** one. The difference
is real: a flag degrades gracefully at runtime without redeploying; a revert
removes the capability and requires a deployment.

## 4. Why this record rather than an implementation

Three reasons, stated so the choice is reviewable rather than assumed:

1. **A config file nothing reads is a fake control.** Creating
   `dataset_policy.yaml` with three flags that no code consults would make the
   roadmap's §10 appear satisfied while changing nothing. That is worse than the
   present honest gap — it would convert a visible deviation into an invisible
   one.
2. **Wiring the flags is engineering, and engineering is frozen** at `82ce7c0`.
   Each flag needs a read path, a default, a precedence rule against the
   existing constants, and its own negative tests. That is a milestone, not a
   documentation fix.
3. **Inventing the semantics would be inventing governance.** The roadmap states
   each flag's *effect* in one clause and nothing else — no precedence, no
   audit behaviour, no interaction with the guard triggers. Choosing those
   details unilaterally is exactly what "do not invent governance decisions"
   forbids.

## 5. Materiality

**Low, on current evidence.** The Dataset Builder has no runtime consumer: no
production path imports it, no systemd unit invokes it, and it is not deployed.
A graceful-degradation lever protects a *running* system; there is nothing
running to degrade.

The materiality changes the moment Phase 5 wires the builder into an evaluation
pipeline. At that point revert-only rollback means a Phase 5 incident requires a
deployment to resolve, not a config change.

## 6. Options for the owner

| Option | Consequence |
|---|---|
| **A — Accept the deviation** *(recommended while the builder has no consumer)* | Roadmap §10 and §13.1 are amended to record revert-only rollback for 4.2/4.4/4.7 and to drop `dataset_policy.yaml`. Phase 4 closes with the deviation recorded. Revisit if Phase 5 adds a runtime consumer |
| **B — Schedule implementation** | A scoped milestone builds `dataset_policy.yaml` and the three read paths with tests. Engineering unfreezes. `AF-2` closes on delivery, not on acceptance |
| **C — Defer without a record** | **Not recommended.** This is the status quo that produced `AF-2`: the roadmap asserts a control that does not exist, and nothing says so |

**Recommendation: Option A**, with the trigger for revisiting stated explicitly
— the first Phase 5 change that gives the Dataset Builder a runtime consumer.

## 7. What this record does not do

It does not amend the roadmap, does not mark any decision ratified, and does not
close `AF-2`. `AF-2` closes when the owner selects an option; until then it
remains an open Phase 4 blocker with its status now documented rather than
merely observed.
