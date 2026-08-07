"""WBS 4.7 — RF3 lane enforcement (ADR-I012 §8.1 RF3, IQROS-P6; resolves PS4).

PS4: *"Lane disjointness is convention. Nothing structurally prevents a
confirmatory evaluation from resolving discovery-lane inputs"* — so *"the
confirmatory lane can be contaminated by the thing it exists to confirm."*
DG4 asks to make it structural; RF3 refuses at resolution.

Roadmap acceptance for 4.7:
  - a confirmatory declaration resolving discovery-lane inputs is refused
    BY NAME  -> TestRF3
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "research", ROOT / "research" / "admissibility",
          ROOT / "research" / "evidence_library"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import record  # noqa: E402  -- the governing lane authority

from dataset_builder import citation_index as ci  # noqa: E402
from dataset_builder import lanes as ln  # noqa: E402
from dataset_builder import registry as reg  # noqa: E402
from dataset_builder import resolution as res  # noqa: E402
from dataset_builder import validation as val  # noqa: E402
from tests.test_dataset_registry import valid_declaration  # noqa: E402
from tests.test_dataset_resolution import FakeResolver, good_resolver  # noqa: E402


class LaneResolver(FakeResolver):
    """A resolver that also reports the lane each input resolved to.

    RF3 reads this via `getattr`, not through the Protocol, so every existing
    resolver keeps working without it.
    """

    def __init__(self, lane_map=None, **kw):
        super().__init__(**kw)
        self.lane_map = dict(lane_map or {})

    def input_lane(self, kind, reference):
        return self.lane_map.get(reference)


def good_lane_resolver(lane_map=None):
    FEATURE_A, FEATURE_B = "f" * 64, "e" * 64
    LABEL_A, LABEL_B = "a" * 64, "b" * 64
    return LaneResolver(
        lane_map=lane_map,
        features={FEATURE_A: {"phase": "Active", "warmup_periods": 10},
                  FEATURE_B: {"phase": "Active", "warmup_periods": 10}},
        labels={LABEL_A: {"phase": "Active", "horizon_days": 30},
                LABEL_B: {"phase": "Active", "horizon_days": 30}},
    )


def lane_declaration(dataset_lane="L1", required_lane="L1"):
    d = valid_declaration()
    d["lane"] = dataset_lane
    d["evidence_references"]["required_lane"] = required_lane
    return d


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "dataset_builder.db"
    reg.init_db(p)
    ci.init_db(p)
    return p


def _declare(db, decl=None, lineage="line-a"):
    return res.declare(db, decl if decl is not None else valid_declaration(),
                       lineage=lineage, declared_by="rohith")


# ===========================================================================
# Single governing authority
# ===========================================================================

class TestSingleAuthority:
    def test_lane_table_is_imported_not_redefined(self):
        """Requirement 5: RF3 must have a single governing authority. The
        vocabulary comes from the evidence ledger's frozen table."""
        assert ln.LANES is record.LANES

    def test_no_lane_literal_is_redefined_in_the_module(self):
        """An assignment building its own lane dict would be a second
        authority, free to drift from the ledger's."""
        src = (ROOT / "research/dataset_builder/lanes.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = ([node.target] if isinstance(node, ast.AnnAssign) else node.targets)
                for t in targets:
                    if getattr(t, "id", "") == "LANES":
                        pytest.fail("lanes.py defines its own LANES table")

    def test_derived_sets_match_the_table(self):
        assert ln.VALID_LANE_CODES == frozenset(record.LANES)
        assert ln.VALID_RUN_KINDS == {k for _, k in record.LANES.values()}
        assert ln.VALID_PROVENANCES == {p for p, _ in record.LANES.values()}

    def test_only_the_adr_named_conflict_exists(self):
        """The ADR names exactly one conflict. Generalising it into a lattice
        would be inventing governance."""
        assert ln.FORBIDDEN_INPUT_LANES == {"confirmatory": frozenset({"discovery"})}

    def test_no_operational_import(self):
        src = (ROOT / "research/dataset_builder/lanes.py").read_text(encoding="utf-8")
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, ast.ImportFrom) and n.module:
                assert n.module.split(".")[0] != "src"
            elif isinstance(n, ast.Import):
                assert all(a.name.split(".")[0] != "src" for a in n.names)


# ===========================================================================
# Lane resolution
# ===========================================================================

class TestResolveLane:
    @pytest.mark.parametrize("code,prov,kind", [
        ("L0", "offline-corpus", "discovery"),
        ("L1", "offline-corpus", "confirmatory"),
        ("L2", "live-forward", "forward"),
        ("L3", "mainnet", "production"),
    ])
    def test_lane_code(self, code, prov, kind):
        lane = ln.resolve_lane(code)
        assert (lane.code, lane.provenance, lane.run_kind) == (code, prov, kind)

    def test_lowercase_code_accepted(self):
        assert ln.resolve_lane("l1").code == "L1"

    @pytest.mark.parametrize("kind,code", [
        ("discovery", "L0"), ("confirmatory", "L1"),
        ("forward", "L2"), ("production", "L3"),
    ])
    def test_run_kind_determines_a_lane(self, kind, code):
        assert ln.resolve_lane(kind).code == code

    def test_unambiguous_provenance_determines_a_lane(self):
        assert ln.resolve_lane("live-forward").code == "L2"
        assert ln.resolve_lane("mainnet").code == "L3"

    def test_ambiguous_provenance_resolves_only_its_half(self):
        """offline-corpus is BOTH L0 and L1. Guessing which is precisely the
        contamination RF3 exists to prevent."""
        lane = ln.resolve_lane("offline-corpus")
        assert lane.code is None
        assert lane.provenance == "offline-corpus"
        assert lane.run_kind is None
        assert lane.is_complete is False

    @pytest.mark.parametrize("bad", ["", None, "nonsense", "L9", "  "])
    def test_unknown_expressions(self, bad):
        assert ln.resolve_lane(bad) == ln.UNKNOWN_LANE

    def test_str_is_diagnosable(self):
        assert "L1" in str(ln.resolve_lane("L1"))
        assert "partial" in str(ln.resolve_lane("offline-corpus"))
        assert "unknown" in str(ln.resolve_lane("nope"))

    def test_forbidden_for(self):
        assert ln.forbidden_for("confirmatory") == frozenset({"discovery"})
        for kind in ("discovery", "forward", "production", None, "unknown"):
            assert ln.forbidden_for(kind) == frozenset()


# ===========================================================================
# Conflict detection
# ===========================================================================

class TestCheckLaneConflicts:
    def test_confirmatory_requiring_discovery_conflicts(self):
        conflicts, _ = ln.check_lane_conflicts(
            lane_declaration("L1", "discovery"))
        assert len(conflicts) == 1
        assert conflicts[0].subject == "evidence_references.required_lane"

    def test_confirmatory_requiring_confirmatory_is_clean(self):
        conflicts, notev = ln.check_lane_conflicts(lane_declaration("L1", "L1"))
        assert conflicts == [] and notev == []

    def test_discovery_may_consume_discovery(self):
        """Only the confirmatory lane is protected. A discovery dataset using
        discovery inputs is normal."""
        conflicts, _ = ln.check_lane_conflicts(lane_declaration("L0", "L0"))
        assert conflicts == []

    def test_discovery_may_consume_confirmatory(self):
        conflicts, _ = ln.check_lane_conflicts(lane_declaration("L0", "L1"))
        assert conflicts == []

    def test_confirmatory_may_consume_forward(self):
        """Stronger provenance is not contamination."""
        conflicts, _ = ln.check_lane_conflicts(lane_declaration("L1", "L2"))
        assert conflicts == []

    def test_resolved_input_lane_conflicts(self):
        conflicts, _ = ln.check_lane_conflicts(
            lane_declaration("L1", "L1"), {"dvol": "discovery"})
        assert len(conflicts) == 1 and conflicts[0].subject == "dvol"

    def test_both_sources_reported(self):
        conflicts, _ = ln.check_lane_conflicts(
            lane_declaration("L1", "L0"), {"dvol": "L0"})
        assert len(conflicts) == 2
        assert {c.subject for c in conflicts} == {
            "evidence_references.required_lane", "dvol"}

    def test_ambiguous_dataset_lane_is_not_evaluable(self):
        """The existing house fixture carries `lane: offline-corpus`, which
        does not determine a run_kind. RF3 must report that, not pass."""
        conflicts, notev = ln.check_lane_conflicts(valid_declaration())
        assert conflicts == []
        assert notev and "does not determine a run_kind" in notev[0]

    def test_ambiguous_required_lane_is_not_evaluable(self):
        conflicts, notev = ln.check_lane_conflicts(
            lane_declaration("L1", "offline-corpus"))
        assert conflicts == []
        assert any("required_lane" in n for n in notev)

    def test_ambiguous_input_lane_is_not_evaluable(self):
        conflicts, notev = ln.check_lane_conflicts(
            lane_declaration("L1", "L1"), {"dvol": "offline-corpus"})
        assert conflicts == []
        assert any("dvol" in n for n in notev)

    def test_not_evaluable_is_never_a_silent_pass(self):
        _, notev = ln.check_lane_conflicts(valid_declaration())
        assert notev != []

    def test_deterministic_ordering(self):
        a, _ = ln.check_lane_conflicts(
            lane_declaration("L1", "L1"), {"z": "L0", "a": "L0"})
        b, _ = ln.check_lane_conflicts(
            lane_declaration("L1", "L1"), {"a": "L0", "z": "L0"})
        assert [c.subject for c in a] == [c.subject for c in b] == ["a", "z"]

    def test_conflict_names_both_lanes(self):
        conflicts, _ = ln.check_lane_conflicts(lane_declaration("L1", "L0"))
        detail = conflicts[0].detail
        assert "confirmatory" in detail and "discovery" in detail
        assert "RF3" in detail and "PS4" in detail


# ===========================================================================
# RF3 at resolution
# ===========================================================================

class TestRF3:
    def test_confirmatory_with_discovery_requirement_refused_by_name(self, db):
        h = _declare(db, lane_declaration("L1", "discovery")).spec_hash
        with pytest.raises(res.ResolutionRefused) as exc:
            res.resolve(db, h, good_resolver())
        rf3 = [x for x in exc.value.refusals if x.code == "RF3"]
        assert rf3 and rf3[0].subject == "evidence_references.required_lane"

    def test_confirmatory_with_discovery_input_refused(self, db):
        h = _declare(db, lane_declaration("L1", "L1")).spec_hash
        with pytest.raises(res.ResolutionRefused) as exc:
            res.resolve(db, h, good_lane_resolver({"dvol": "discovery"}))
        assert any(x.code == "RF3" and x.subject == "dvol"
                   for x in exc.value.refusals)

    def test_clean_confirmatory_passes(self, db):
        h = _declare(db, lane_declaration("L1", "L1")).spec_hash
        assert res.resolve(db, h, good_lane_resolver({"dvol": "L1"})).ok is True

    def test_discovery_dataset_passes(self, db):
        h = _declare(db, lane_declaration("L0", "L0")).spec_hash
        assert res.resolve(db, h, good_lane_resolver({"dvol": "L0"})).ok is True

    def test_standard_fixture_is_not_refused(self, db):
        """Regression guard: the house fixture's lane is ambiguous, so RF3
        reports not-evaluable rather than refusing. If this ever fails, RF3
        started guessing."""
        h = _declare(db).spec_hash
        report = res.resolve(db, h, good_resolver())
        assert report.ok is True
        assert report.not_evaluable != []

    def test_resolver_without_input_lane_still_works(self, db):
        """Every pre-4.7 resolver lacks `input_lane`. RF3 must read it with
        getattr, not require it."""
        h = _declare(db, lane_declaration("L1", "L1")).spec_hash
        assert not hasattr(good_resolver(), "input_lane")
        assert res.resolve(db, h, good_resolver()).ok is True

    def test_refusal_is_permanent(self, db):
        h = _declare(db, lane_declaration("L1", "discovery")).spec_hash
        with pytest.raises(res.ResolutionRefused):
            res.resolve(db, h, good_resolver())
        spec = reg.get_specification(db, h)
        assert spec["phase"] == "Rejected" and "RF3" in spec["rejection_reason"]

    def test_refused_dataset_can_never_be_sealed_or_cited(self, db):
        """DG4 made structural: the contaminated dataset never resolves."""
        h = _declare(db, lane_declaration("L1", "discovery")).spec_hash
        with pytest.raises(res.ResolutionRefused):
            res.resolve(db, h, good_resolver())
        assert reg.is_citable(db, h) is False
        with pytest.raises(reg.TransitionError):
            reg.mark_validated(db, h)

    def test_not_evaluable_surfaced_on_the_report(self, db):
        h = _declare(db).spec_hash
        assert res.resolve(db, h, good_resolver()).not_evaluable

    def test_no_not_evaluable_when_lanes_are_complete(self, db):
        h = _declare(db, lane_declaration("L1", "L1")).spec_hash
        report = res.resolve(db, h, good_lane_resolver({"dvol": "L1"}))
        assert report.not_evaluable == []


# ===========================================================================
# RF1–RF6 composition
# ===========================================================================

class TestRefusalComposition:
    def test_all_resolution_refusals_report_together(self, db):
        """RF1, RF2, RF3, RF5 and RF6 all run before any decision, so a caller
        sees every problem at once."""
        d = lane_declaration("L1", "discovery")
        d["temporal_scope"].update(window_start="2025-01-01",
                                   window_end="2025-12-31", as_of="2026-01-05")
        d["sealed_region_exclusions"] = [{"start": "2018-01-01", "end": "2018-02-01"}]
        h = _declare(db, d).spec_hash
        with pytest.raises(res.ResolutionRefused) as exc:
            res.resolve(db, h, res.StoreResolver(
                corpus_coverage_map={"dvol": ("2024-12-30", "2026-08-01")}))
        codes = {x.code for x in exc.value.refusals}
        assert {"RF1", "RF2", "RF3"} <= codes

    def test_rf4_is_the_only_refusal_still_deferred_at_resolution(self):
        assert set(res.DEFERRED_REFUSALS) == {"RF4"}

    def test_validation_defers_nothing_now(self):
        """RF2 and RF3 both relocated to resolution, so every check §12
        assigns to validation runs there."""
        assert val.DEFERRED_CHECKS == {}

    def test_rf3_has_one_authority_only(self, db):
        """Validation must not also report RF3, or there would be two places
        to change and two chances to diverge."""
        h = _declare(db, lane_declaration("L1", "L1")).spec_hash
        res.resolve(db, h, good_lane_resolver({"dvol": "L1"}))
        from tests.test_dataset_validation import good_inputs
        assert val.validate(db, h, good_inputs()).by_code("RF3") == []

    def test_tier_c_still_checks_lane_recorded(self, db):
        """TC2 checks a lane was RECORDED; RF3 checks it does not CONFLICT.
        Both, not one."""
        from tests.test_dataset_validation import ev
        m = val.compose_admissibility([ev("dvol", lane=None)])
        findings = val.check_tier_c(valid_declaration(), [ev("dvol", lane=None)], m, None)
        assert any(f.code == "TC2" for f in findings)

    def test_full_pipeline_to_sealed_with_lanes(self, db, tmp_path):
        import receipt_ledger as rl
        from dataset_builder import sealing as sl
        from tests.test_dataset_validation import good_inputs
        sl.init_db(db)
        ledger = tmp_path / "ledger.db"; rl.init_db(ledger)
        h = _declare(db, lane_declaration("L1", "L1")).spec_hash
        res.resolve(db, h, good_lane_resolver({"dvol": "L1"}))
        report = val.validate(db, h, good_inputs())
        sl.seal_dataset(db, h, report, sealed_by="rohith", ledger_db=ledger)
        assert reg.is_citable(db, h) is True
        assert rl.verify_chain(ledger).ok is True


# ===========================================================================
# TV3 sabotage
# ===========================================================================

class TestTV3Sabotage:
    def test_rf3_is_not_vacuous(self, db):
        h = _declare(db, lane_declaration("L1", "discovery")).spec_hash
        with pytest.raises(res.ResolutionRefused):
            res.resolve(db, h, good_resolver())

    def test_rf3_does_not_refuse_everything(self, db):
        h = _declare(db, lane_declaration("L1", "L1")).spec_hash
        assert res.resolve(db, h, good_lane_resolver({"dvol": "L1"})).ok is True

    def test_forbidden_table_is_load_bearing(self):
        assert ln.FORBIDDEN_INPUT_LANES.get("confirmatory")

    def test_ambiguity_handling_is_load_bearing(self):
        """If an ambiguous provenance silently resolved to a lane, RF3 would
        either refuse or pass on a guess. It must do neither."""
        assert ln.resolve_lane("offline-corpus").run_kind is None

    def test_not_evaluable_is_load_bearing(self, db):
        h = _declare(db).spec_hash
        assert res.resolve(db, h, good_resolver()).not_evaluable != []


# ===========================================================================
# Regression — 4.1–4.6 preserved
# ===========================================================================

class TestRegression:
    def test_rf2_still_enforced(self, db):
        from tests.test_sealed_regions import lockbox_window_declaration, NON_COVERING
        d = lockbox_window_declaration(exclusions=NON_COVERING)
        d["lane"] = "L1"; d["evidence_references"]["required_lane"] = "L1"
        h = _declare(db, d).spec_hash
        with pytest.raises(res.ResolutionRefused) as exc:
            res.resolve(db, h, good_resolver())
        assert any(x.code == "RF2" for x in exc.value.refusals)

    def test_declaration_hash_unaffected_by_lane_checks(self, db):
        d = lane_declaration("L1", "L1")
        h = _declare(db, d).spec_hash
        res.resolve(db, h, good_lane_resolver({"dvol": "L1"}))
        assert reg.declaration_hash(reg.get_specification(db, h)["declaration"]) == h

    def test_citation_graph_intact(self, db):
        h = _declare(db, lane_declaration("L1", "L1")).spec_hash
        res.resolve(db, h, good_lane_resolver({"dvol": "L1"}))
        assert len(ci.cites(db, h)) == 8
        ok, errors = ci.verify_bidirectional_consistency(db)
        assert ok is True and errors == []

    def test_result_invariance_still_holds(self):
        """4.7 must not touch any published artifact either."""
        import hashlib, json
        baseline = json.loads((ROOT / "BASELINE_46.json").read_text())
        for rel, was in baseline.items():
            now = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
            assert now == was, f"{rel} changed"

    def test_no_other_store_touched(self, tmp_path):
        p = tmp_path / "d.db"
        reg.init_db(p); ci.init_db(p)
        h = _declare(p, lane_declaration("L1", "L1")).spec_hash
        res.resolve(p, h, good_lane_resolver({"dvol": "L1"}))
        assert {f.name for f in tmp_path.rglob("*.db")} == {"d.db"}
