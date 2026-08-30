# A2a — `registry.yaml` `preregistration.hash` Convention

**Date: 2026-08-30 (UTC) · Gate A2a · Decision: the PRE-REGISTRATION JSON FILE
SHA-256, LF-normalised**

**This is a NEW governance decision, not a reconstruction of historical intent.**
The evidence (§2) shows two conventions, both applied deliberately, neither
documented anywhere. It does not uniquely determine which was intended, and this
record does not pretend otherwise. The decision below is made now, on its merits,
and takes effect going forward.

**No artifact is modified by this gate.** `registry.yaml` is unchanged — the
corrections this record specifies are applied at **A2b**, which this gate does
not perform. `thresholds.yaml`, `collection_config.yaml`, every decision
function, every pre-registration JSON, `ledger.db` and production are all
untouched.

---

## 1. The decision

### 1.1 What `preregistration.hash` means

`preregistration` is a **content-addressed pointer to a pre-registration
document**. Its two keys are one unit:

```yaml
preregistration:
  path: research/forward_evidence/pre_registration_<signal>.json
  hash: <sha256>
```

`hash` is the SHA-256 **of the file at `path`**, and of nothing else. It answers
exactly one question: *is the pre-registration document this family was
registered against still the document sitting at that path?*

### 1.2 File hash, not decision-function hash

**`preregistration.hash` is the SHA-256 of the pre-registration JSON file.** It
is **not** the pinned decision-function hash, and the decision-function hash must
never be written into this field again.

### 1.3 LF-normalised

**Yes — LF normalisation is part of the convention.** The hash is taken over
`file_bytes.replace(b"\r\n", b"\n")`, the project-wide convention already
implemented identically in `pre_reg_guard._sha256_lf` and `receipts.sha256_lf`.

This is not stylistic. The repository is checked out **CRLF on the Windows
workstation and LF on the production host**, so a raw hash of the same file
differs by machine. Demonstrated in-hand at the A2 audit on `registry.yaml`
itself: raw `bfad1927…` locally, raw `92b37006…` on production, **identical
content** — the LF-normalised digest is `92b37006…` on both. A raw-byte pointer
would be host-dependent and would fail verification on one of the two machines
every time.

`g3_atr_expansion` currently matches under both raw and LF hashing because its
JSON happens to contain no CRLF; that coincidence is not a second convention.

---

## 2. Why this convention is authoritative

The evidence base is the A2 read-only audit. This section states the reasoning
for the decision; §3 states what the evidence does and does not establish about
history.

**2.1 A `path`/`hash` pair whose hash is not of the file at `path` is a broken
pointer by construction.** This is the decisive argument. The two keys sit in one
mapping. A reader — human or future validator — takes `hash` as pertaining to
`path`, because that is the only reading under which the pair means anything. The
decision-function convention silently violates it, and the violation is currently
published: `report.py:495-497` renders these into the git-tracked
`research/evidence_library/library.md` as ``**Pre-registration:** `<path>` (hash
`<h>…`)`` — a label under which four of six entries state a digest that is not
the digest of the named file.

**2.2 It completes a pin chain instead of duplicating one link of it.** The
intended chain has two links:

```
registry.preregistration.hash  --pins-->  pre-registration JSON
JSON.function_spec.decision_function_hash  --pins-->  decision function file
                                                      (enforced at runtime by
                                                       pre_reg_guard.verify_fn_hash)
```

Under the file-hash convention both links exist. Under the decision-function
convention the registry duplicates **link 2** — a value that already lives in the
JSON itself, is already enforced on every run, and is already recorded per
observation in `run_log.fn_hash` — while **link 1 does not exist at all**, so
nothing anywhere pins the pre-registration document. The registry adds no
information and leaves the document unprotected.

**2.3 It is universal; the alternative is not.** Every registered family has a
pre-registration JSON, so every family can carry a file hash. The
decision-function convention has a demonstrated failure mode:
`h6_funding_vol_regime`'s pre-registration (`pre_registration.json`) contains **no
`function_spec.decision_function_hash` key**, so its registry value had to be
sourced from a file the registry does not name. It is in fact the LF hash of
`research/forward_evidence/decision_function.py` (verified by search at this
gate) — correct, but **unverifiable from the registry alone**, because nothing in
the entry says which file it hashes.

**2.4 It is what every gate since the founding batch has actually done.** Both
pointers added after the initial seeding — `ivrv_vrp_spread` (2026-06-19,
`a7b47a8`) and `g3_atr_expansion` (2026-06-26, `dd5a3ba`) — used the file hash,
independently, seven days apart, and both still verify today. The
decision-function entries are a single batch from one commit that has never been
repeated.

**2.5 It makes the field capable of detecting something.** A file hash goes stale
when the pre-registration is amended after registration — and that is precisely
an event governance should surface, treated elsewhere in this project as a hard
failure (`pre_reg_guard.FnHashViolation`: *"decision function file has been
modified post-registration ... Do NOT write to evidence DB"*). Under the
decision-function convention the field can never go stale, because it is not
about the file; it therefore can never detect anything.

**The honest counter-argument, and the answer.** Staleness is a real maintenance
cost, and it is the one genuine advantage of the decision-function convention.
The answer is that undetected staleness is the problem and *detected* staleness
is the point — but that is only true if something checks. **Nothing does today**
(§3.3). Until a validator exists the field remains display-only under either
convention, so this decision buys correctness of meaning now and detection later.
Adding that validator is **not** authorised here; see §6.

**2.6 It reopens nothing.** `preregistration` is not among CLAUDE.md §16's
governance-frozen constraints, is not a gate, gates no transition, and has no
ledger effect. 6A.17 §1.2 established on the record that it *"is an
inconsistently maintained convenience pointer, not a gate"* and that **"nothing
in `registry.yaml` distinguishes a collecting family from a non-collecting
one."** Both statements remain true after this decision.

---

## 3. What the evidence establishes about history — and what it does not

### 3.1 Both conventions were deliberate. Neither was documented.

| Commit | Date | Entries added | Convention |
|---|---|---|---|
| `1639dfb` | 2026-06-15 | `h6_funding_vol_regime`, `g1_market_regime_sma200`, `h5_vol_regime`, `g3_momentum_tsmom` | **decision-function hash** |
| `a7b47a8` | 2026-06-19 | `ivrv_vrp_spread` | **JSON file hash** |
| `dd5a3ba` | 2026-06-26 | `g3_atr_expansion` | **JSON file hash** |

**The founding batch was not a set of stale file hashes.** Verified at this gate
by reconstructing each JSON *as it stood at `1639dfb`* and hashing it: for
`h5_vol_regime`, `g1_market_regime_sma200` and `g3_momentum_tsmom` the registry
value did **not** equal the file hash even on the day it was written, and did
equal `function_spec.decision_function_hash`. The convention was chosen, not
drifted into.

A repository-wide search of `*.md` and `*.yaml` finds **no schema documentation
for this field anywhere**. Neither convention is written down. That is why §1 is
a new decision rather than a finding.

### 3.2 `h6_funding_vol_regime`'s value is explained, but its provenance is not recorded

`f78f0a52…` is the LF hash of `research/forward_evidence/decision_function.py`
(established by exhaustive search at this gate, not inferred). It is the same
convention as the rest of the founding batch, sourced from the decision-function
file directly because its JSON carries no such key. **No artifact records that
choice**, and this record does not supply a rationale for it.

### 3.3 Nothing validates this field, which is why the split went unnoticed

The only consumer is `report.py:495-497`, which renders and never verifies. There
is no check in `lifecycle.validate_registry`, `record.py`, or
`governance_verification.py`. The inconsistency survived from 2026-06-19 to
2026-08-30 without any gate detecting it.

---

## 4. How existing registry entries must be handled

Applied at **A2b**, not here.

| family | current value is | action at A2b |
|---|---|---|
| `ivrv_vrp_spread` | JSON file LF hash | **none** — already conforms |
| `g3_atr_expansion` | JSON file LF hash | **none** — already conforms |
| `h5_vol_regime` | decision-function hash | **correct** to the JSON file LF hash |
| `g1_market_regime_sma200` | decision-function hash | **correct** to the JSON file LF hash |
| `g3_momentum_tsmom` | decision-function hash | **correct** to the JSON file LF hash |
| `h6_funding_vol_regime` | LF hash of `decision_function.py` | **correct** to the JSON file LF hash |

**These corrections destroy no information and invalidate nothing.**

- The superseded values were never consumed by any validating code path (§3.3),
  so no governance decision, admission, transition or observation rests on them.
  Nothing downstream changes.
- Each superseded value remains permanently recoverable from git history, and
  each is reproducible from a file still in the repository — the three
  founding-batch values from their JSONs' own
  `function_spec.decision_function_hash`, and h6's from `decision_function.py`.
  This record names both sources so the recovery needs no archaeology.
- No decision-function hash is weakened: every one of them stays where it is
  actually enforced — inside its pre-registration JSON, checked on every run by
  `pre_reg_guard.verify_fn_hash`.

**A2b must recompute every value from the file at commit time rather than copying
a digest out of this record**, so that a JSON amended between A2a and A2b cannot
be pinned to a hash that was already wrong when it was written.

## 5. How the two null entries will be populated

At **A2b**, under §1's convention:

| family | pre-registration (authoritative linkage) | value at the time of this record |
|---|---|---|
| `g2_funding_level` | `pre_registration_g2_funding_level.json` — `runner_g2.py:24` `_PRE_REG` | `8355c16512cb5558bb7102cc746923164b7d2f26c72730e8e345c5b98aaddcc6` |
| `g1_trend_regime_ma_cross` | `pre_registration_g1_trend_regime.json` — `runner_g1_trend_regime.py:12` `_PRE_REG` | `2d3cbd210dd564493634c61b6417e0445160ff922c6fe527cc3267a28e34e966` |

The `path` in each case is the file the family's own runner loads and passes to
`pre_reg_guard.verify_fn_hash` — the authoritative linkage, not a name match.

**The blocker 6A.17 recorded has cleared.** 6A.17 §4 required that
`preregistration` *"must continue to point at nothing **while the
pre-registration is still a draft**."* Both documents are now active, not drafts:
`pre_registration_g1_trend_regime.json` was created at 6A.18 and its pinned
decision-function hash `2e6f9597…` was independently verified against production
at Gate 6A.20; `pre_registration_g2_funding_level.json` has been in live use since
`g2_funding_level` began collecting on 2026-08-26.

**A2b must also update `research/forward_evidence/tests/test_g1_trend_regime.py`
line 703**, which asserts `e["preregistration"] is None`. That assertion was
correct when written and becomes false by this decision. 6A.18 deferred U-4
precisely to avoid changing it without a mandate. It skips inside
`Dockerfile.research` (evidence_library excluded by INV-1), so it runs in the
local and CI suites only.

---

## 6. Explicitly not decided here

- **No validator is authorised.** §2.5's detection benefit requires a check that
  does not exist. Adding one to `validate_registry` / `governance_verification`
  is a separate, code-bearing decision — deliberately not folded into either A2a
  or A2b, because it would change what `report.py --check` can fail on, and
  `report.py --check` is the production forward-collection precondition whose
  failure caused INC-0005's 19-day outage.
- **`h6_funding_vol_regime`'s pre-registration path is not re-examined.** A2b
  corrects its hash to the file at the path already recorded. Whether
  `pre_registration.json` is the right document for that family is out of scope.
- **No other registry field is touched**, at this gate or at A2b.

---

## 7. Correction of the record: "4 of 6"

The A2 audit establishes, as fact and not interpretation, that a statement
repeated in two committed records is **wrong**:

> The `preregistration` pointer is set on **4 of the 6** collecting families and
> absent on 2
>
> — `docs/governance/g1_trend_regime_admission_decision.md:30` (6A.17 §1.2), and
> substantially identically at
> `docs/governance/g1_trend_regime_wiring_record.md:129` (6A.18 §5)

**The registry has held 5 set / 2 null throughout**, verified byte-for-byte at
three commits — `93f9a94` (6A.17), `4e3dec7` (6A.18) and `HEAD`. The correct
statement is *5 of the 7 collecting families, absent on 2*; restricted to the six
collecting at the time it was written, *5 of 6, absent on 1*. The published "4 + 2
= 6" is internally consistent but matches the file under no reading.

**The two source records are deliberately NOT rewritten.** They are records of
executed gates, and this gate's mandate is to create the A2a record only. The
correction is authoritative here, following the precedent set in CLAUDE.md §18's
GL-3 reconciliation, where the original text is preserved verbatim and superseded
by a later record rather than edited in place. Anyone citing the "4 of 6" figure
should cite this section instead.

**The error was immaterial to both decisions.** Neither 6A.17's admission nor
6A.18's wiring turned on the count; both cited it only to establish that the
pointer is inconsistently maintained and therefore not a gate. That conclusion is
strengthened, not weakened, by the true figures — the field is inconsistent in
population *and*, as §1 now settles, was inconsistent in meaning.

---

## 8. Trail

| Gate | Outcome |
|---|---|
| `1639dfb` (2026-06-15) | 4 pointers seeded under the decision-function convention |
| `a7b47a8` (2026-06-19) | `ivrv_vrp_spread` added under the file-hash convention |
| `dd5a3ba` (2026-06-26) | `g3_atr_expansion` added under the file-hash convention |
| 6A.17 | Recorded the field as "not a gate"; declined to set it while the pre-registration was a draft |
| 6A.18 | Deferred it as residual item **U-4** rather than setting it silently |
| A2 | Read-only audit: found the two-convention split and the count error |
| **A2a** | **This record. Convention decided: pre-registration JSON file SHA-256, LF-normalised. No artifact modified.** |
| A2b | Apply it: correct 4, populate 2, update `test_P06` — **not performed here** |
