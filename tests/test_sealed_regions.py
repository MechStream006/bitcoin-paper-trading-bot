"""WBS 4.6 — RF2 sealed-region relocation (ADR-I012 §8.1 RF2, IQROS-P7; PS3).

Governed by `DS-DEC-004` (ratified 2026-08-07) and its amendment (same date):
RF2 is implemented **only within the Dataset Builder path**. No analysis
script is modified.

RESULT INVARIANCE IS THE PRIMARY ACCEPTANCE CRITERION.
`TestResultInvariance` proves it byte-for-byte against a baseline captured
before any edit, and the proof is structural: no file producing published
research output is touched, so no published value can change. The sealed
lockbox is never opened.
"""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "research", ROOT / "research" / "admissibility",
          ROOT / "research" / "evidence_library"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from forward_evidence.pre_reg_guard import _sha256_lf  # noqa: E402

from dataset_builder import citation_index as ci  # noqa: E402
from dataset_builder import registry as reg  # noqa: E402
from dataset_builder import resolution as res  # noqa: E402
from dataset_builder import sealed_regions as sr  # noqa: E402
from dataset_builder import validation as val  # noqa: E402
from tests.test_dataset_registry import valid_declaration  # noqa: E402
from tests.test_dataset_resolution import good_resolver  # noqa: E402

LOCKBOX_START = "2025-06-01"
LOCKBOX_END = "2026-05-31"

#: Files that must be byte-identical before and after RF2. Any change here is
#: a failed milestone (DS-DEC-004 amendment, criterion 3).
INVARIANT_FILES = [
    "research/forward_evidence/decision_function.py",
    "research/forward_evidence/m0_pre_register.py",
    "research/evidence/run_lockbox_validation.py",
    "research/forward_evidence/pre_registration.json",
    "research/forward_evidence/decision_function_g1_market_regime.py",
    "research/forward_evidence/decision_function_g3_atr_expansion.py",
    "research/forward_evidence/decision_function_g3_tsmom.py",
    "research/forward_evidence/decision_function_h5.py",
    "research/forward_evidence/decision_function_ivrv.py",
    "research/reports/evidence_library/lockbox_validation.json",
]


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "dataset_builder.db"
    reg.init_db(p)
    ci.init_db(p)
    return p


def _declare(db, decl=None, lineage="line-a"):
    return res.declare(db, decl if decl is not None else valid_declaration(),
                       lineage=lineage, declared_by="rohith")


#: A syntactically valid exclusion that covers nothing relevant. Used wherever
#: a test needs "the window is not excluded" -- an EMPTY list cannot be used,
#: because Tier A refuses `sealed_region_exclusions: present but empty` before
#: resolution is ever reached.
NON_COVERING = [{"start": "2018-01-01", "end": "2018-06-01", "reason": "unrelated"}]


def lockbox_window_declaration(start="2025-01-01", end="2025-12-31",
                               exclusions=None):
    """A declaration whose window overlaps the D.5 lockbox."""
    d = valid_declaration()
    d["temporal_scope"]["window_start"] = start
    d["temporal_scope"]["window_end"] = end
    d["temporal_scope"]["as_of"] = "2026-08-07"
    d["sealed_region_exclusions"] = (
        [{"start": LOCKBOX_START, "end": LOCKBOX_END, "reason": "lockbox"}]
        if exclusions is None else exclusions
    )
    return d


# ===========================================================================
# RESULT INVARIANCE — the primary acceptance criterion
# ===========================================================================

class TestResultInvariance:
    def test_baseline_exists(self):
        """The baseline was captured BEFORE any edit. Without it there is no
        proof, only assertion."""
        assert (ROOT / "BASELINE_46.json").exists(), (
            "BASELINE_46.json is missing — result invariance cannot be proven"
        )

    def test_every_invariant_file_is_bit_identical(self):
        """Content comparison against the pre-change baseline.

        Criterion 2: existing published research outputs remain bit-identical.
        Criterion 3: any observable difference is a failed milestone.

        Hashing uses `pre_reg_guard._sha256_lf` — the platform's own convention
        for hashing governed source artifacts, which normalises CRLF -> LF
        before hashing. The same function guards the production pre-registration
        lock, and the governance receipt ledger uses the identical convention
        (`receipts.sha256_lf`).

        The original raw `sha256(read_bytes())` recorded a *checkout rendering*
        rather than repository content: this repository has `core.autocrlf=true`,
        so two of the pinned files carry CRLF in a Windows working tree while
        their committed blobs are LF. Those entries could therefore never match
        on Linux — including in CI — regardless of whether anything changed.
        Normalising fixes what is measured; it does not relax it. A single
        changed character in any of these files still fails this test.
        """
        baseline = json.loads((ROOT / "BASELINE_46.json").read_text())
        drift = []
        for rel in INVARIANT_FILES:
            p = ROOT / rel
            if not p.exists():
                drift.append(f"{rel}: MISSING")
                continue
            now = _sha256_lf(p)
            was = baseline.get(rel)
            if was is None:
                drift.append(f"{rel}: absent from baseline")
            elif now != was:
                drift.append(f"{rel}: {was[:12]}... -> {now[:12]}...")
        assert drift == [], "RESULT INVARIANCE VIOLATED: " + "; ".join(drift)

    def test_fn_hash_unchanged(self):
        """Criterion 4: the registered fn_hash remains identical. If this
        fails, production forward-evidence collection breaks with
        `CRITICAL: fn_hash mismatch` on the next run."""
        fn = ROOT / "research/forward_evidence/decision_function.py"
        registered = json.loads(
            (ROOT / "research/forward_evidence/pre_registration.json").read_text()
        )["decision_function_hash"]
        assert hashlib.sha256(fn.read_bytes()).hexdigest() == registered

    def test_no_analysis_script_was_modified(self):
        """Structural proof, not just hash comparison: git reports no change
        to any file outside research/dataset_builder/ and tests/."""
        out = subprocess.run(
            ["git", "diff", "--name-only", "HEAD", "--",
             "research/forward_evidence", "research/evidence", "research/reports"],
            cwd=ROOT, capture_output=True, text=True,
        )
        assert out.stdout.strip() == "", (
            f"analysis scripts modified: {out.stdout.strip()}"
        )

    def test_rf2_touches_no_file_outside_the_builder(self):
        """DS-DEC-004 amendment: RF2 is confined to the Dataset Builder path."""
        out = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=ROOT, capture_output=True, text=True,
        )
        allowed = ("research/dataset_builder/", "tests/", "docs/", "BASELINE_46.json")
        offenders = [
            line[3:] for line in out.stdout.splitlines()
            if line[3:] and not line[3:].startswith(allowed)
        ]
        assert offenders == [], f"changes outside the authorised scope: {offenders}"

    def test_governed_source_matches_published_record(self):
        """The governed constant must equal the PUBLISHED lockbox record, not
        some script's literal. If these ever diverge, the governed source is
        wrong and every RF2 decision built on it is wrong."""
        published = json.loads(
            (ROOT / "research/reports/evidence_library/lockbox_validation.json").read_text()
        )["lockbox"]
        region = sr.get_region("d5_lockbox")
        assert [region.start, region.end] == published

    def test_lockbox_is_not_opened_by_this_milestone(self):
        """Criterion 4 of the amendment: the lockbox must not be opened or
        consumed.

        Proven by AST, not by string search. Naming the one-time script in a
        docstring or an invariant-file list is fine and necessary; importing
        or invoking it is not. A substring check cannot tell those apart —
        it failed on this very file's own prose.
        """
        for path in (ROOT / "research" / "dataset_builder").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for n in ast.walk(tree):
                if isinstance(n, ast.Import):
                    assert all("lockbox" not in a.name for a in n.names), path.name
                elif isinstance(n, ast.ImportFrom) and n.module:
                    assert "lockbox" not in n.module, path.name
                elif isinstance(n, ast.Call):
                    fn = getattr(n.func, "id", "") or getattr(n.func, "attr", "")
                    assert "run_lockbox" not in fn, f"{path.name} invokes {fn}"


# ===========================================================================
# The governed source
# ===========================================================================

class TestGovernedSource:
    def test_single_registry_exists(self):
        assert len(sr.SEALED_REGIONS) >= 1
        assert sr.get_region("d5_lockbox") is not None

    def test_region_is_immutable(self):
        region = sr.get_region("d5_lockbox")
        with pytest.raises(Exception):
            region.start = "2020-01-01"  # frozen dataclass

    def test_records_spent_state(self):
        region = sr.get_region("d5_lockbox")
        assert region.spent is True
        assert region.spent_on == "2026-06-10"

    def test_unknown_region_returns_none(self):
        assert sr.get_region("nope") is None

    def test_frozen_exceptions_are_named_with_reasons(self):
        """The two governance artifacts RF2 deliberately does not touch must
        be recorded in code, not merely remembered."""
        assert "research/forward_evidence/decision_function.py" in sr.FROZEN_EXCEPTIONS
        assert "research/forward_evidence/m0_pre_register.py" in sr.FROZEN_EXCEPTIONS
        for path, reason in sr.FROZEN_EXCEPTIONS.items():
            assert (ROOT / path).exists(), f"named exception does not exist: {path}"
            assert len(reason) > 40, f"exception {path} needs a real reason"

    def test_hash_lock_reason_is_accurate(self):
        """The recorded reason for freezing decision_function.py must be true,
        or the exception is a rationalisation."""
        reason = sr.FROZEN_EXCEPTIONS["research/forward_evidence/decision_function.py"]
        assert "hash" in reason.lower()
        registered = json.loads(
            (ROOT / "research/forward_evidence/pre_registration.json").read_text()
        )["decision_function_hash"]
        actual = hashlib.sha256(
            (ROOT / "research/forward_evidence/decision_function.py").read_bytes()
        ).hexdigest()
        assert registered == actual  # the lock is real and currently satisfied


class TestIntersection:
    def test_overlapping_window_detected(self):
        assert sr.intersecting_regions("2025-01-01", "2025-12-31")

    def test_window_entirely_before_does_not_intersect(self):
        assert sr.intersecting_regions("2021-01-01", "2025-05-31") == []

    def test_window_entirely_after_does_not_intersect(self):
        assert sr.intersecting_regions("2026-06-01", "2026-12-31") == []

    def test_single_shared_day_intersects(self):
        """A sealed region leaking one observation is still a leak."""
        assert sr.intersecting_regions("2020-01-01", LOCKBOX_START)
        assert sr.intersecting_regions(LOCKBOX_END, "2027-01-01")

    def test_window_inside_region(self):
        assert sr.intersecting_regions("2025-08-01", "2025-09-01")

    def test_region_inside_window(self):
        assert sr.intersecting_regions("2020-01-01", "2027-01-01")

    def test_unparseable_window_returns_empty(self):
        assert sr.intersecting_regions("not-a-date", "2026-01-01") == []


class TestCoverage:
    def _region(self):
        return sr.get_region("d5_lockbox")

    def test_exact_cover(self):
        assert sr.covers({"start": LOCKBOX_START, "end": LOCKBOX_END}, self._region())

    def test_wider_cover(self):
        assert sr.covers({"start": "2025-01-01", "end": "2027-01-01"}, self._region())

    def test_partial_cover_is_not_cover(self):
        """The conservative reading: partial coverage leaves the remainder
        readable, which is the leak RF2 exists to prevent."""
        assert not sr.covers({"start": LOCKBOX_START, "end": "2025-12-31"}, self._region())
        assert not sr.covers({"start": "2025-09-01", "end": LOCKBOX_END}, self._region())

    def test_malformed_exclusion_is_not_cover(self):
        assert not sr.covers({"start": "oops", "end": LOCKBOX_END}, self._region())
        assert not sr.covers({}, self._region())


# ===========================================================================
# RF2 at resolution
# ===========================================================================

class TestRF2:
    def test_intersecting_window_without_exclusion_refused(self, db):
        d = lockbox_window_declaration(exclusions=NON_COVERING)
        h = _declare(db, d).spec_hash
        with pytest.raises(res.ResolutionRefused) as exc:
            res.resolve(db, h, good_resolver())
        rf2 = [x for x in exc.value.refusals if x.code == "RF2"]
        assert rf2 and rf2[0].subject == "d5_lockbox"
        assert "no explicit exclusion" in rf2[0].detail

    def test_intersecting_window_with_full_exclusion_passes(self, db):
        h = _declare(db, lockbox_window_declaration()).spec_hash
        assert res.resolve(db, h, good_resolver()).ok is True

    def test_partial_exclusion_refused(self, db):
        d = lockbox_window_declaration(
            exclusions=[{"start": LOCKBOX_START, "end": "2025-12-31"}])
        h = _declare(db, d).spec_hash
        with pytest.raises(res.ResolutionRefused) as exc:
            res.resolve(db, h, good_resolver())
        assert any(x.code == "RF2" for x in exc.value.refusals)

    def test_non_intersecting_window_needs_no_exclusion(self, db):
        d = lockbox_window_declaration(start="2021-01-01", end="2025-05-31",
                                       exclusions=NON_COVERING)
        h = _declare(db, d).spec_hash
        assert res.resolve(db, h, good_resolver()).ok is True

    def test_standard_fixture_unaffected(self, db):
        """The house fixture's window ends before the lockbox begins, so RF2
        must not fire on it. If it did, every prior milestone's tests would
        break — which is itself a result-invariance signal."""
        h = _declare(db).spec_hash
        assert res.resolve(db, h, good_resolver()).ok is True

    def test_refusal_names_the_region_and_its_span(self, db):
        d = lockbox_window_declaration(exclusions=NON_COVERING)
        h = _declare(db, d).spec_hash
        with pytest.raises(res.ResolutionRefused) as exc:
            res.resolve(db, h, good_resolver())
        detail = [x for x in exc.value.refusals if x.code == "RF2"][0].detail
        assert "d5_lockbox" in detail and LOCKBOX_START in detail and LOCKBOX_END in detail

    def test_refusal_is_permanent(self, db):
        d = lockbox_window_declaration(exclusions=NON_COVERING)
        h = _declare(db, d).spec_hash
        with pytest.raises(res.ResolutionRefused):
            res.resolve(db, h, good_resolver())
        spec = reg.get_specification(db, h)
        assert spec["phase"] == "Rejected"
        assert "RF2" in spec["rejection_reason"]

    def test_a_refused_dataset_can_never_be_sealed_or_cited(self, db):
        """The architectural point of §8.1: a consumer cannot read a sealed
        region regardless of its own code, because the dataset never resolves."""
        d = lockbox_window_declaration(exclusions=NON_COVERING)
        h = _declare(db, d).spec_hash
        with pytest.raises(res.ResolutionRefused):
            res.resolve(db, h, good_resolver())
        assert reg.is_citable(db, h) is False
        with pytest.raises(reg.TransitionError):
            reg.mark_validated(db, h)

    def test_rf2_reported_with_other_refusals(self, db):
        d = lockbox_window_declaration(exclusions=NON_COVERING)
        h = _declare(db, d).spec_hash
        with pytest.raises(res.ResolutionRefused) as exc:
            res.resolve(db, h, res.StoreResolver())
        codes = {x.code for x in exc.value.refusals}
        assert "RF2" in codes and "RF1" in codes


# ===========================================================================
# TV3 sabotage
# ===========================================================================

class TestTV3Sabotage:
    def test_rf2_is_not_vacuous(self, db):
        d = lockbox_window_declaration(exclusions=NON_COVERING)
        h = _declare(db, d).spec_hash
        with pytest.raises(res.ResolutionRefused):
            res.resolve(db, h, good_resolver())

    def test_rf2_does_not_refuse_everything(self, db):
        h = _declare(db, lockbox_window_declaration()).spec_hash
        assert res.resolve(db, h, good_resolver()).ok is True

    def test_full_coverage_rule_is_load_bearing(self):
        """If `covers` accepted partial overlap, the leak RF2 prevents would
        reopen silently."""
        region = sr.get_region("d5_lockbox")
        assert not sr.covers({"start": LOCKBOX_START, "end": "2025-07-01"}, region)

    def test_registry_is_not_empty(self):
        """An empty registry would make every RF2 test pass trivially."""
        assert sr.SEALED_REGIONS

    def test_frozen_exceptions_are_not_empty(self):
        assert len(sr.FROZEN_EXCEPTIONS) >= 2


# ===========================================================================
# Regression — 4.1-4.5 invariants
# ===========================================================================

class TestRegression:
    def test_rf2_removed_from_resolution_deferred(self):
        assert "RF2" not in res.DEFERRED_REFUSALS

    def test_rf2_removed_from_validation_deferred(self):
        assert "RF2" not in val.DEFERRED_CHECKS

    def test_rf3_also_relocated_at_wbs_4_7(self):
        """RF2 moved at 4.6 and RF3 at 4.7. Neither is deferred now; RF4
        alone remains, and only from resolution's perspective."""
        assert "RF3" not in res.DEFERRED_REFUSALS
        assert val.DEFERRED_CHECKS == {}
        assert "RF4" in res.DEFERRED_REFUSALS

    def test_validation_does_not_duplicate_rf2(self, db):
        """One authority per refusal. If validation also reported RF2 there
        would be two places to change and two chances to diverge."""
        h = _declare(db, lockbox_window_declaration()).spec_hash
        res.resolve(db, h, good_resolver())
        from tests.test_dataset_validation import good_inputs
        report = val.validate(db, h, good_inputs())
        assert report.by_code("RF2") == []

    def test_tb4_still_checks_exclusion_coherence(self, db):
        """RF2 decides whether an exclusion was required; TB4 still checks
        that a declared one is well-formed. Both, not one."""
        from tests.test_dataset_validation import good_inputs
        d = lockbox_window_declaration(
            exclusions=[{"start": LOCKBOX_START, "end": LOCKBOX_END},
                        {"start": "2026-01-01", "end": "2025-01-01"}])
        findings = val.check_tier_b(d, good_inputs())
        assert any(f.code == "TB4" for f in findings)

    def test_sealed_regions_imports_nothing_operational(self):
        src = (ROOT / "research/dataset_builder/sealed_regions.py").read_text(encoding="utf-8")
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, ast.ImportFrom) and n.module:
                assert n.module.split(".")[0] != "src"
            elif isinstance(n, ast.Import):
                assert all(a.name.split(".")[0] != "src" for a in n.names)

    def test_no_other_store_touched(self, tmp_path):
        p = tmp_path / "d.db"
        reg.init_db(p); ci.init_db(p)
        h = _declare(p, lockbox_window_declaration()).spec_hash
        res.resolve(p, h, good_resolver())
        assert {f.name for f in tmp_path.rglob("*.db")} == {"d.db"}
