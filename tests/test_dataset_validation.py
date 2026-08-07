"""WBS 4.4 — Admissibility Composition + Validation Tiers A-D + RF4
(ADR-I012 §8 stages 3-4, §8.1 RF4, §9.1, §12).

Roadmap acceptance for 4.4:
  - the meet is computed once and STATED EXPLICITLY, never asserted -> TestCompose
  - RF4 refuses floor > meet                                        -> TestRF4
  - all four tiers implemented                                      -> TestTierA/B/C/D
  - a hard failure produces a permanent Rejected record             -> TestRejectionPermanence
"""

from __future__ import annotations

import ast
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "research", ROOT / "research" / "admissibility"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from lattice import (  # noqa: E402
    Admissibility, Certification, Dimension, Integrity, Maturity, Provenance, Readiness,
)
from dataset_builder import citation_index as ci  # noqa: E402
from dataset_builder import registry as reg  # noqa: E402
from dataset_builder import resolution as res  # noqa: E402
from dataset_builder import validation as val  # noqa: E402
from tests.test_dataset_registry import valid_declaration  # noqa: E402
from tests.test_dataset_resolution import good_resolver  # noqa: E402

FEATURE_A = "f" * 64
LABEL_A = "a" * 64


def adm(p=Provenance.OFFLINE_CORPUS, c=Certification.GREEN, r=Readiness.READY,
        m=Maturity.FULL, i=Integrity.CLEAN, na=frozenset()) -> Admissibility:
    return Admissibility(p, c, r, m, i, not_applicable=na)


def ev(ref="dvol", **kw):
    kw.setdefault("ceiling", adm())
    kw.setdefault("certification_verdict", "GREEN")
    kw.setdefault("lane", "offline-corpus")
    return val.InputAdmissibility(kind="evidence", reference=ref, **kw)


def feat(ref=FEATURE_A, **kw):
    kw.setdefault("ceiling", adm())
    return val.InputAdmissibility(kind="feature", reference=ref, **kw)


def lab(ref=LABEL_A, **kw):
    kw.setdefault("ceiling", adm())
    return val.InputAdmissibility(kind="label", reference=ref, **kw)


def good_inputs():
    return [ev("dvol"), ev("spot"), feat(), lab()]


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "dataset_builder.db"
    reg.init_db(p)
    ci.init_db(p)
    return p


def _resolved(db, decl=None, lineage="line-a") -> str:
    """A dataset in phase Resolved, ready for validate()."""
    d = decl if decl is not None else valid_declaration()
    h = res.declare(db, d, lineage=lineage, declared_by="rohith").spec_hash
    res.resolve(db, h, good_resolver())
    return h


# ---------------------------------------------------------------------------
# Build / scope
# ---------------------------------------------------------------------------

class TestBuildAndScope:
    def test_no_src_import(self):
        src = (ROOT / "research" / "dataset_builder" / "validation.py").read_text(encoding="utf-8")
        bad = []
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, ast.Import):
                bad += [a.name for a in n.names if a.name.split(".")[0] == "src"]
            elif isinstance(n, ast.ImportFrom) and n.module:
                if n.module.split(".")[0] == "src":
                    bad.append(n.module)
        assert bad == []

    def test_tier_order_is_fixed(self):
        assert val.TIER_ORDER == ("A", "B", "C", "D")

    def test_deferred_checks_named_with_owner(self):
        """RF2 left this set at WBS 4.6 -- it is enforced at resolution now."""
        assert set(val.DEFERRED_CHECKS) == {"RF3"}
        for text in val.DEFERRED_CHECKS.values():
            assert "WBS 4." in text

    def test_report_carries_deferred(self, db):
        h = _resolved(db)
        report = val.validate(db, h, good_inputs())
        assert set(report.deferred) == {"RF3"}

    def test_rf4_delegates_to_the_lattice(self):
        """RF4 must not reimplement the comparison — a second authority on
        admissibility is exactly what IQROS-P4 forbids."""
        src = (ROOT / "research" / "dataset_builder" / "validation.py").read_text(encoding="utf-8")
        assert "satisfies(" in src
        assert "from composition import meet, satisfies" in src

    def test_tiers_run_in_order(self, db):
        h = _resolved(db)
        assert val.validate(db, h, good_inputs()).tiers_run == ["A", "B", "C", "D"]


# ---------------------------------------------------------------------------
# Stage 3 — admissibility composition
# ---------------------------------------------------------------------------

class TestCompose:
    def test_meet_of_uniform_inputs(self):
        m = val.compose_admissibility([ev(), feat(), lab()])
        assert m.provenance == Provenance.OFFLINE_CORPUS

    def test_meet_takes_the_weakest(self):
        m = val.compose_admissibility([
            ev(ceiling=adm(p=Provenance.MAINNET)),
            feat(ceiling=adm(p=Provenance.OFFLINE_CORPUS)),
        ])
        assert m.provenance == Provenance.OFFLINE_CORPUS

    def test_weakness_does_not_contaminate_other_dimensions(self):
        m = val.compose_admissibility([
            ev(ceiling=adm(c=Certification.AMBER)),
            feat(ceiling=adm(i=Integrity.FLAGGED)),
        ])
        assert m.certification == Certification.AMBER
        assert m.integrity == Integrity.FLAGGED
        assert m.provenance == Provenance.OFFLINE_CORPUS

    def test_order_independent(self):
        a, b, c = ev(ceiling=adm(c=Certification.AMBER)), feat(), lab()
        assert val.compose_admissibility([a, b, c]) == val.compose_admissibility([c, b, a])

    def test_no_inputs_refused(self):
        with pytest.raises(val.ValidationError, match="no inputs"):
            val.compose_admissibility([])

    def test_meet_is_stated_on_the_report(self, db):
        """§9.1 — computed once and stated explicitly, never implied."""
        h = _resolved(db)
        report = val.validate(db, h, good_inputs())
        assert report.computed_meet is not None
        assert report.capped_admissibility is not None


# ---------------------------------------------------------------------------
# Tier A
# ---------------------------------------------------------------------------

class TestTierA:
    def test_clean_declaration_passes(self):
        assert val.check_tier_a(valid_declaration()) == []

    def test_reuses_registry_tier_a(self):
        d = valid_declaration(); del d["lane"]
        findings = val.check_tier_a(d)
        assert findings and all(f.tier == "A" and f.blocking for f in findings)

    def test_finding_names_the_field(self):
        d = valid_declaration(); del d["lane"]
        assert any("lane" in f.fields[0] for f in val.check_tier_a(d))

    def test_finding_carries_adr_clause(self):
        d = valid_declaration(); del d["conditioning"]
        assert all("§7.2" in f.adr_clause or "Tier A" in f.adr_clause
                   for f in val.check_tier_a(d))


# ---------------------------------------------------------------------------
# Tier B — temporal integrity
# ---------------------------------------------------------------------------

class TestTierB:
    def test_clean_declaration_passes(self):
        assert val.check_tier_b(valid_declaration(), good_inputs()) == []

    def test_tb1_as_of_closure(self):
        d = valid_declaration()
        d["temporal_scope"]["window_end"] = "2026-12-31"
        d["temporal_scope"]["as_of"] = "2026-08-07"
        f = val.check_tier_b(d, good_inputs())
        assert any(x.code == "TB1" and x.blocking for x in f)

    def test_tb1_boundary_equal_passes(self):
        d = valid_declaration()
        d["temporal_scope"]["window_end"] = "2026-08-07"
        d["temporal_scope"]["as_of"] = "2026-08-07"
        assert not [x for x in val.check_tier_b(d, good_inputs()) if x.code == "TB1"]

    def test_tb2_label_horizon_termination(self):
        d = valid_declaration()
        d["temporal_scope"]["window_end"] = "2026-08-01"
        d["temporal_scope"]["as_of"] = "2026-08-07"
        d["label_references"][0]["horizon_days"] = 30
        f = val.check_tier_b(d, good_inputs())
        assert any(x.code == "TB2" for x in f)

    def test_tb2_boundary_exact_passes(self):
        d = valid_declaration()
        d["temporal_scope"]["window_end"] = "2026-07-08"
        d["temporal_scope"]["as_of"] = "2026-08-07"
        d["label_references"][0]["horizon_days"] = 30
        assert not [x for x in val.check_tier_b(d, good_inputs()) if x.code == "TB2"]

    def test_tb3_partition_before_window(self):
        d = valid_declaration()
        inputs = [ev("dvol", window=("2019-01-01", "2025-05-31"))]
        f = val.check_tier_b(d, inputs)
        assert any(x.code == "TB3" and "before window_start" in x.detail for x in f)

    def test_tb3_partition_after_window(self):
        d = valid_declaration()
        inputs = [ev("dvol", window=("2021-01-01", "2026-12-31"))]
        f = val.check_tier_b(d, inputs)
        assert any(x.code == "TB3" and "after window_end" in x.detail for x in f)

    def test_tb3_inside_window_passes(self):
        d = valid_declaration()
        inputs = [ev("dvol", window=("2021-06-01", "2025-01-01"))]
        assert not [x for x in val.check_tier_b(d, inputs) if x.code == "TB3"]

    def test_tb3_unknown_span_does_not_fire(self):
        """Absent information is not a finding — assert, never guess."""
        assert not [x for x in val.check_tier_b(valid_declaration(), [ev("dvol")])
                    if x.code == "TB3"]

    def test_tb4_inverted_exclusion(self):
        d = valid_declaration()
        d["sealed_region_exclusions"] = [{"start": "2026-01-01", "end": "2025-01-01"}]
        assert any(x.code == "TB4" for x in val.check_tier_b(d, good_inputs()))

    def test_tb4_unparseable_exclusion(self):
        d = valid_declaration()
        d["sealed_region_exclusions"] = [{"start": "not-a-date", "end": "2026-01-01"}]
        assert any(x.code == "TB4" for x in val.check_tier_b(d, good_inputs()))

    def test_tb4_non_mapping_exclusion(self):
        d = valid_declaration()
        d["sealed_region_exclusions"] = ["2025-01-01"]
        assert any(x.code == "TB4" for x in val.check_tier_b(d, good_inputs()))

    def test_tb4_non_intersecting_exclusion_is_not_a_finding(self):
        """Declaring the sealed lockbox even when the window ends before it is
        the defensive practice this platform uses -- the standard fixture does
        exactly that. Flagging it would penalise the careful declaration and
        reward the silent one. Whether an exclusion was REQUIRED is RF2's
        question (WBS 4.6), not TB4's."""
        d = valid_declaration()
        d["sealed_region_exclusions"] = [{"start": "2018-01-01", "end": "2018-06-01"}]
        assert [x for x in val.check_tier_b(d, good_inputs()) if x.code == "TB4"] == []

    def test_tb4_still_catches_a_malformed_span(self):
        d = valid_declaration()
        d["sealed_region_exclusions"] = [{"start": "2026-01-01", "end": "2025-01-01"}]
        assert any(x.code == "TB4" for x in val.check_tier_b(d, good_inputs()))

    def test_rf2_is_enforced_at_resolution_not_here(self, db):
        """Sealed-region INTERSECTION is RF2, enforced at resolution since WBS
        4.6. It must NOT appear in validation's deferred map (that would say
        unchecked) and must NOT be a validation finding (that would duplicate
        the authority). TB4 still checks exclusion coherence only."""
        h = _resolved(db)
        report = val.validate(db, h, good_inputs())
        assert "RF2" not in report.deferred
        assert report.by_code("RF2") == []


# ---------------------------------------------------------------------------
# Tier C + RF4
# ---------------------------------------------------------------------------

class TestTierC:
    def test_clean_passes(self):
        m = val.compose_admissibility(good_inputs())
        assert val.check_tier_c(valid_declaration(), good_inputs(), m, None) == []

    def test_tc1_missing_verdict(self):
        """Asserts the ABSENCE branch specifically, not just the TC1 code.
        An empty verdict also fails the unrecognised-value branch, so a test
        checking only the code cannot tell which condition fired -- and would
        pass even if the absence check were removed."""
        inputs = [ev("dvol", certification_verdict=None)]
        m = val.compose_admissibility(inputs)
        f = [x for x in val.check_tier_c(valid_declaration(), inputs, m, None)
             if x.code == "TC1"]
        assert f and f[0].blocking
        assert "no certification verdict" in f[0].detail
        assert "unrecognised" not in f[0].detail

    def test_tc1_distinguishes_absent_from_unrecognised(self):
        m = val.compose_admissibility([ev()])
        absent = [x for x in val.check_tier_c(
            valid_declaration(), [ev("dvol", certification_verdict=None)], m, None)
            if x.code == "TC1"][0]
        bogus = [x for x in val.check_tier_c(
            valid_declaration(), [ev("dvol", certification_verdict="MAYBE")], m, None)
            if x.code == "TC1"][0]
        assert absent.detail != bogus.detail

    def test_tc1_unrecognised_verdict(self):
        inputs = [ev("dvol", certification_verdict="PROBABLY_FINE")]
        m = val.compose_admissibility(inputs)
        f = val.check_tier_c(valid_declaration(), inputs, m, None)
        assert any(x.code == "TC1" and "unrecognised" in x.detail for x in f)

    def test_tc1_accepts_red_as_present(self):
        """RED is a determination, not an absence. It blocks via the lattice,
        not via Tier C."""
        inputs = [ev("dvol", certification_verdict="RED")]
        m = val.compose_admissibility(inputs)
        assert not [x for x in val.check_tier_c(valid_declaration(), inputs, m, None)
                    if x.code == "TC1"]

    def test_tc2_missing_lane(self):
        inputs = [ev("dvol", lane=None)]
        m = val.compose_admissibility(inputs)
        f = val.check_tier_c(valid_declaration(), inputs, m, None)
        assert any(x.code == "TC2" for x in f)

    def test_tc3_unresolved_readiness(self):
        inputs = [feat(readiness_resolved=False)]
        m = val.compose_admissibility(inputs)
        f = val.check_tier_c(valid_declaration(), inputs, m, None)
        assert any(x.code == "TC3" for x in f)

    def test_rf3_not_implemented_here(self, db):
        h = _resolved(db)
        assert "RF3" in val.validate(db, h, good_inputs()).deferred


class TestRF4:
    def test_floor_above_meet_refused(self):
        inputs = [ev(ceiling=adm(p=Provenance.OFFLINE_CORPUS))]
        m = val.compose_admissibility(inputs)
        floor = adm(p=Provenance.MAINNET)
        f = val.check_tier_c(valid_declaration(), inputs, m, floor)
        rf4 = [x for x in f if x.code == "RF4"]
        assert rf4 and rf4[0].blocking
        assert "PROVENANCE" in rf4[0].detail

    def test_floor_equal_to_meet_passes(self):
        inputs = [ev(ceiling=adm())]
        m = val.compose_admissibility(inputs)
        assert not [x for x in val.check_tier_c(valid_declaration(), inputs, m, adm())
                    if x.code == "RF4"]

    def test_floor_below_meet_passes(self):
        inputs = [ev(ceiling=adm(p=Provenance.MAINNET))]
        m = val.compose_admissibility(inputs)
        floor = adm(p=Provenance.OFFLINE_CORPUS)
        assert not [x for x in val.check_tier_c(valid_declaration(), inputs, m, floor)
                    if x.code == "RF4"]

    def test_names_every_unmet_dimension(self):
        """ADR-I014 AD4 — a refusal names every unmet dimension, not the first."""
        inputs = [ev(ceiling=adm(p=Provenance.OFFLINE_CORPUS, c=Certification.AMBER))]
        m = val.compose_admissibility(inputs)
        floor = adm(p=Provenance.MAINNET, c=Certification.GREEN)
        rf4 = [x for x in val.check_tier_c(valid_declaration(), inputs, m, floor)
               if x.code == "RF4"][0]
        assert "PROVENANCE" in rf4.detail and "CERTIFICATION" in rf4.detail

    def test_no_floor_declared_skips_rf4(self):
        inputs = [ev(ceiling=adm(p=Provenance.UNKNOWN))]
        m = val.compose_admissibility(inputs)
        assert not [x for x in val.check_tier_c(valid_declaration(), inputs, m, None)
                    if x.code == "RF4"]

    def test_floor_is_never_relaxed(self):
        """AD3 — failure is a refusal, never a downgrade of the floor."""
        inputs = [ev(ceiling=adm(p=Provenance.OFFLINE_CORPUS))]
        m = val.compose_admissibility(inputs)
        floor = adm(p=Provenance.MAINNET)
        rf4 = [x for x in val.check_tier_c(valid_declaration(), inputs, m, floor)
               if x.code == "RF4"][0]
        assert "never relaxed" in rf4.detail
        assert floor.provenance == Provenance.MAINNET  # unchanged

    def test_carries_governing_clause_and_tier(self):
        inputs = [ev(ceiling=adm(p=Provenance.OFFLINE_CORPUS))]
        m = val.compose_admissibility(inputs)
        rf4 = [x for x in val.check_tier_c(valid_declaration(), inputs, m,
                                           adm(p=Provenance.MAINNET)) if x.code == "RF4"][0]
        assert rf4.tier == "C"
        assert "RF4" in rf4.adr_clause and "IQROS-P4" in rf4.adr_clause
        assert rf4.fields == ("admissibility_floor",)


# ---------------------------------------------------------------------------
# Tier D — caps, never blocks
# ---------------------------------------------------------------------------

class TestTierD:
    def test_absent_stats_are_not_computed_not_passed(self):
        findings, not_computed = val.check_tier_d(None)
        assert findings == []
        assert not_computed == ["TD1", "TD2", "TD3", "TD4", "TD5"]

    def test_report_records_not_computed(self, db):
        h = _resolved(db)
        report = val.validate(db, h, good_inputs())
        assert report.not_computed == ["TD1", "TD2", "TD3", "TD4", "TD5"]

    def test_clean_stats_produce_no_findings(self):
        s = val.DatasetStatistics(row_count=1000, calendar_years_spanned=4,
                                  label_positive_fraction=0.5, matured_fraction=1.0)
        findings, not_computed = val.check_tier_d(s)
        assert findings == [] and not_computed == []

    @pytest.mark.parametrize("kwargs,code", [
        (dict(row_count=5), "TD1"),
        (dict(calendar_years_spanned=0), "TD2"),
        (dict(degenerate_columns=("col_a",)), "TD3"),
        (dict(label_positive_fraction=0.99), "TD4"),
        (dict(matured_fraction=0.10), "TD5"),
    ])
    def test_each_check_fires(self, kwargs, code):
        s = val.DatasetStatistics(**kwargs)
        findings, _ = val.check_tier_d(s)
        assert any(f.code == code for f in findings)

    def test_every_tier_d_finding_is_non_blocking(self):
        s = val.DatasetStatistics(row_count=1, calendar_years_spanned=0,
                                  degenerate_columns=("x",),
                                  label_positive_fraction=0.99, matured_fraction=0.0)
        findings, _ = val.check_tier_d(s)
        assert len(findings) == 5
        assert all(f.blocking is False for f in findings)

    def test_tier_d_never_rejects_a_dataset(self, db):
        """§12: Tier D caps, it does not block."""
        h = _resolved(db)
        s = val.DatasetStatistics(row_count=1, calendar_years_spanned=0,
                                  degenerate_columns=("x",),
                                  label_positive_fraction=0.99, matured_fraction=0.0)
        report = val.validate(db, h, good_inputs(), stats=s)
        assert report.ok is True
        assert reg.get_specification(db, h)["phase"] == "Validated"
        assert len(report.advisory) == 5

    def test_caps_lower_the_ceiling(self, db):
        h = _resolved(db)
        s = val.DatasetStatistics(row_count=1, calendar_years_spanned=4,
                                  label_positive_fraction=0.5, matured_fraction=1.0)
        report = val.validate(db, h, good_inputs(), stats=s)
        assert report.computed_meet.readiness == Readiness.READY
        assert report.capped_admissibility.readiness == Readiness.PARTIAL

    def test_caps_never_raise(self):
        low = adm(r=Readiness.NOT_READY)
        f = val.Finding("TD1", "D", "x", ("y",), "z", False,
                        caps={"dimension": "READINESS", "to": "PARTIAL"})
        assert val.apply_tier_d_caps(low, [f]).readiness == Readiness.NOT_READY

    def test_caps_skip_not_applicable_dimensions(self):
        """Never manufacture a deficiency on an N/A dimension (ADR-I014 §5.1)."""
        ceiling = adm(m=Maturity.FULL, na=frozenset({Dimension.MATURITY}))
        f = val.Finding("TD5", "D", "x", ("y",), "z", False,
                        caps={"dimension": "MATURITY", "to": "PARTIAL"})
        out = val.apply_tier_d_caps(ceiling, [f])
        assert out.maturity == Maturity.FULL

    def test_unknown_cap_is_ignored_not_crashed(self):
        f = val.Finding("TDX", "D", "x", ("y",), "z", False,
                        caps={"dimension": "NONSENSE", "to": "PARTIAL"})
        assert val.apply_tier_d_caps(adm(), [f]) == adm()

    def test_boundary_row_count(self):
        assert val.check_tier_d(val.DatasetStatistics(row_count=30))[0] == []
        assert any(f.code == "TD1" for f in
                   val.check_tier_d(val.DatasetStatistics(row_count=29))[0])

    def test_boundary_imbalance_symmetric(self):
        assert any(f.code == "TD4" for f in
                   val.check_tier_d(val.DatasetStatistics(label_positive_fraction=0.02))[0])
        assert any(f.code == "TD4" for f in
                   val.check_tier_d(val.DatasetStatistics(label_positive_fraction=0.98))[0])


# ---------------------------------------------------------------------------
# Stage 4 — the battery
# ---------------------------------------------------------------------------

class TestValidate:
    def test_happy_path_moves_to_validated(self, db):
        h = _resolved(db)
        report = val.validate(db, h, good_inputs())
        assert report.ok is True
        assert reg.get_specification(db, h)["phase"] == "Validated"

    def test_requires_resolved_phase(self, db):
        d = valid_declaration()
        h = res.declare(db, d, lineage="l", declared_by="r").spec_hash
        with pytest.raises(reg.TransitionError, match="not 'Resolved'"):
            val.validate(db, h, good_inputs())

    def test_unknown_hash(self, db):
        with pytest.raises(val.ValidationError, match="no such specification"):
            val.validate(db, "0" * 64, good_inputs())

    def test_all_blocking_tiers_reported_at_once(self, db):
        """Every blocking tier reports before any decision is made, so a caller
        fixes all problems in one pass rather than one validation at a time.

        Note the declaration itself is clean: TB1 (as_of closure) is
        unreachable through the normal path because registry Tier A already
        refuses `as_of < window_end` at declaration time -- see TD-4.4-1. The
        multi-tier case is therefore built from Tier C findings plus RF4.
        """
        h = _resolved(db)
        inputs = [ev("dvol", certification_verdict=None, lane=None),
                  feat(readiness_resolved=False)]
        with pytest.raises(val.ValidationRefused) as exc:
            val.validate(db, h, inputs, declared_floor=adm(p=Provenance.MAINNET))
        codes = {f.code for f in exc.value.findings}
        assert {"TC1", "TC2", "TC3", "RF4"} <= codes
        assert len({f.tier for f in exc.value.findings}) >= 1

    def test_lr4_on_foreign_db(self, tmp_path):
        foreign = tmp_path / "f.db"
        sqlite3.connect(foreign).execute("CREATE TABLE x (a TEXT)")
        with pytest.raises(reg.NotADatasetBuilderStore):
            val.validate(foreign, "0" * 64, good_inputs())

    def test_by_tier_and_by_code_helpers(self, db):
        h = _resolved(db)
        s = val.DatasetStatistics(row_count=1)
        report = val.validate(db, h, good_inputs(), stats=s)
        assert report.by_tier("D") and report.by_code("TD1")
        assert report.by_tier("A") == []

    def test_resolve_and_validate_combined(self, db):
        d = valid_declaration()
        h = res.declare(db, d, lineage="l", declared_by="r").spec_hash
        rr, vr = val.resolve_and_validate(db, h, good_resolver(), good_inputs())
        assert rr.ok is True and vr.ok is True
        assert reg.get_specification(db, h)["phase"] == "Validated"


class TestRejectionPermanence:
    def test_blocking_failure_rejects_permanently(self, db):
        h = _resolved(db)
        with pytest.raises(val.ValidationRefused):
            val.validate(db, h, good_inputs(), declared_floor=adm(p=Provenance.MAINNET))
        spec = reg.get_specification(db, h)
        assert spec["phase"] == "Rejected"
        assert "RF4" in spec["rejection_reason"]

    def test_rejection_written_before_raising(self, db):
        """P4-D3 / LS-TD-002 class of defect."""
        h = _resolved(db)
        try:
            val.validate(db, h, good_inputs(), declared_floor=adm(p=Provenance.MAINNET))
        except val.ValidationRefused:
            pass
        assert reg.get_specification(db, h)["phase"] == "Rejected"

    def test_no_arc_out_of_rejected(self, db):
        h = _resolved(db)
        with pytest.raises(val.ValidationRefused):
            val.validate(db, h, good_inputs(), declared_floor=adm(p=Provenance.MAINNET))
        with pytest.raises(reg.TransitionError, match="'Rejected'"):
            val.validate(db, h, good_inputs())

    def test_rejection_reason_carries_code_and_tier(self, db):
        h = _resolved(db)
        with pytest.raises(val.ValidationRefused):
            val.validate(db, h, good_inputs(), declared_floor=adm(p=Provenance.MAINNET))
        assert "[RF4/C]" in reg.get_specification(db, h)["rejection_reason"]


# ---------------------------------------------------------------------------
# TV3 sabotage
# ---------------------------------------------------------------------------

class TestTV3Sabotage:
    def test_rf4_is_not_vacuous(self):
        inputs = [ev(ceiling=adm(p=Provenance.OFFLINE_CORPUS))]
        m = val.compose_admissibility(inputs)
        assert [x for x in val.check_tier_c(valid_declaration(), inputs, m,
                                            adm(p=Provenance.MAINNET)) if x.code == "RF4"]

    def test_rf4_does_not_refuse_everything(self):
        inputs = [ev(ceiling=adm())]
        m = val.compose_admissibility(inputs)
        assert not [x for x in val.check_tier_c(valid_declaration(), inputs, m, adm())
                    if x.code == "RF4"]

    def test_tier_b_is_not_vacuous(self):
        d = valid_declaration()
        d["temporal_scope"]["window_end"] = "2027-01-01"
        assert [x for x in val.check_tier_b(d, good_inputs()) if x.code == "TB1"]

    def test_tier_c_is_not_vacuous(self):
        inputs = [ev(certification_verdict=None)]
        m = val.compose_admissibility(inputs)
        assert [x for x in val.check_tier_c(valid_declaration(), inputs, m, None)]

    def test_tier_d_is_not_vacuous(self):
        assert val.check_tier_d(val.DatasetStatistics(row_count=1))[0]

    def test_tier_d_blocking_flag_is_load_bearing(self, db):
        """If any Tier D finding were blocking, a hygiene issue would reject a
        dataset — the exact conflation §12 forbids."""
        h = _resolved(db)
        s = val.DatasetStatistics(row_count=1, calendar_years_spanned=0,
                                  degenerate_columns=("x",),
                                  label_positive_fraction=0.99, matured_fraction=0.0)
        assert val.validate(db, h, good_inputs(), stats=s).ok is True


# ---------------------------------------------------------------------------
# Regression — 4.1/4.2/4.3 invariants preserved
# ---------------------------------------------------------------------------

class TestRegression:
    def test_full_lifecycle_to_sealed(self, db):
        h = _resolved(db)
        val.validate(db, h, good_inputs())
        reg.seal(db, h)
        assert reg.is_citable(db, h) is True

    def test_sealed_immutability_still_enforced(self, db):
        h = _resolved(db)
        val.validate(db, h, good_inputs())
        reg.seal(db, h)
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="DATASET_TAMPER"):
                con.execute("UPDATE dataset_specifications SET declaration_json='{}' "
                            "WHERE spec_hash=?", (h,))
        finally:
            con.close()

    def test_citations_intact_after_validation(self, db):
        h = _resolved(db)
        val.validate(db, h, good_inputs())
        assert len(ci.cites(db, h)) == 8

    def test_bidirectional_consistency_after_validation(self, db):
        h = _resolved(db)
        val.validate(db, h, good_inputs())
        ok, errors = ci.verify_bidirectional_consistency(db)
        assert ok is True and errors == []

    def test_declaration_hash_unchanged(self, db):
        h = _resolved(db)
        val.validate(db, h, good_inputs())
        spec = reg.get_specification(db, h)
        assert reg.declaration_hash(spec["declaration"]) == h

    def test_rejected_at_validation_stops_pinning(self, db):
        h = _resolved(db)
        with pytest.raises(val.ValidationRefused):
            val.validate(db, h, good_inputs(), declared_floor=adm(p=Provenance.MAINNET))
        assert ci.cited_by(db, "feature", FEATURE_A) == []

    def test_resolution_refusals_still_work(self, db):
        d = valid_declaration()
        h = res.declare(db, d, lineage="l2", declared_by="r").spec_hash
        with pytest.raises(res.ResolutionRefused):
            res.resolve(db, h, res.StoreResolver())

    def test_no_other_store_touched(self, tmp_path):
        p = tmp_path / "d.db"
        reg.init_db(p); ci.init_db(p)
        h = res.declare(p, valid_declaration(), lineage="l", declared_by="r").spec_hash
        res.resolve(p, h, good_resolver())
        val.validate(p, h, good_inputs())
        assert {f.name for f in tmp_path.rglob("*.db")} == {"d.db"}
