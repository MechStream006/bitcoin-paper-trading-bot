"""WBS 4.3 — Dataset Declaration + Resolution (ADR-I012 §8 stages 1-2, §8.1, §14).

Roadmap acceptance for 4.3, each covered below:
  - every reference bound to a hash at as_of; corpus revisions pinned -> TestResolve
  - RF1 refuses non-Active specifications BY NAME                     -> TestRF1
  - RF5 refuses an unsatisfiable maturity requirement                 -> TestRF5
  - RF6 refuses insufficient warm-up                                  -> TestRF6
  - automatic citation indexing at declaration                        -> TestDeclare
"""

from __future__ import annotations

import ast
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "research") not in sys.path:
    sys.path.insert(0, str(ROOT / "research"))

from dataset_builder import citation_index as ci  # noqa: E402
from dataset_builder import registry as reg  # noqa: E402
from dataset_builder import resolution as res  # noqa: E402
from tests.test_dataset_registry import valid_declaration  # noqa: E402

FEATURE_A = "f" * 64
FEATURE_B = "e" * 64
LABEL_A = "a" * 64
LABEL_B = "b" * 64

WIDE_COVERAGE = {
    "dvol": ("2019-01-01", "2026-08-01"),
    "spot": ("2019-01-01", "2026-08-01"),
    "funding": ("2019-01-01", "2026-08-01"),
    "perp": ("2019-01-01", "2026-08-01"),
}


class FakeResolver:
    """Test double for `ReferenceResolver`. Narrow by design -- the Protocol
    exists precisely so the refusals are testable without either store."""

    def __init__(self, features=None, labels=None, coverage=None, revisions=None):
        self.features = dict(features or {})
        self.labels = dict(labels or {})
        self.coverage = dict(coverage if coverage is not None else WIDE_COVERAGE)
        self.revisions = dict(revisions or {})

    def get_feature(self, spec_hash): return self.features.get(spec_hash)
    def get_label(self, spec_hash): return self.labels.get(spec_hash)
    def corpus_coverage(self, source): return self.coverage.get(source)
    def pinned_revision(self, source): return self.revisions.get(source)


def active_feature(warmup=10):
    return {"phase": "Active", "warmup_periods": warmup}


def active_label(horizon=30):
    return {"phase": "Active", "horizon_days": horizon, "maturity_rule": "horizon_elapsed"}


def good_resolver(**kw):
    return FakeResolver(
        features={FEATURE_A: active_feature(), FEATURE_B: active_feature()},
        labels={LABEL_A: active_label(), LABEL_B: active_label()},
        **kw,
    )


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "dataset_builder.db"
    reg.init_db(p)
    ci.init_db(p)
    return p


def _declare(db, decl=None, lineage="line-a"):
    return res.declare(
        db, decl if decl is not None else valid_declaration(),
        lineage=lineage, declared_by="rohith",
    )


# ---------------------------------------------------------------------------
# §3 BV — dependency direction and scope
# ---------------------------------------------------------------------------

class TestBuildAndScope:
    def test_no_src_import(self):
        src = (ROOT / "research" / "dataset_builder" / "resolution.py").read_text(encoding="utf-8")
        bad = []
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, ast.Import):
                bad += [a.name for a in n.names if a.name.split(".")[0] == "src"]
            elif isinstance(n, ast.ImportFrom) and n.module:
                if n.module.split(".")[0] == "src":
                    bad.append(n.module)
        assert bad == []

    def test_upstream_stores_imported_only_inside_the_adapter(self):
        """§8.3 stays inspectable: the only references to the upstream
        registries are inside StoreResolver's methods, not at module level."""
        src = (ROOT / "research" / "dataset_builder" / "resolution.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        module_level = []
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                name = getattr(node, "module", None) or ""
                names = [a.name for a in node.names]
                if any("feature_store" in x or "label_store" in x for x in [name, *names]):
                    module_level.append(name or names)
        assert module_level == [], f"upstream imported at module level: {module_level}"

    def test_deferred_refusals_are_named(self):
        """Deferred refusals must be visibly named, never silently absent.

        RF2 left this set at WBS 4.6 -- it is now enforced at resolution
        against the governed sealed-region registry. RF3 and RF4 remain
        deferred, each naming its owning milestone.
        """
        assert set(res.DEFERRED_REFUSALS) == {"RF4"}
        for code, text in res.DEFERRED_REFUSALS.items():
            assert "WBS 4." in text

    def test_rf2_and_rf3_are_no_longer_deferred(self):
        """WBS 4.6 and 4.7: neither may appear as deferred, or a reader would
        think it unchecked when it is enforced. RF4 alone remains, because it
        is a stage-4 check that genuinely does not run at resolution."""
        assert "RF2" not in res.DEFERRED_REFUSALS
        assert "RF3" not in res.DEFERRED_REFUSALS
        assert "RF4" in res.DEFERRED_REFUSALS

    def test_report_carries_deferred_by_default(self, db):
        h = _declare(db).spec_hash
        report = res.resolve(db, h, good_resolver())
        assert set(report.deferred) == {"RF4"}


# ---------------------------------------------------------------------------
# Stage 1 — Declaration
# ---------------------------------------------------------------------------

class TestDeclare:
    def test_declares_and_indexes_automatically(self, db):
        """Closes TD-4.2-2: after 4.2 indexing had to be called by hand."""
        h = _declare(db).spec_hash
        edges = ci.cites(db, h)
        assert len(edges) == 8
        assert {c.cited_kind for c in edges} == {"evidence", "feature", "label"}

    def test_declaration_is_citable_in_reverse_once_sealed(self, db):
        h = _declare(db).spec_hash
        reg.mark_resolved(db, h); reg.mark_validated(db, h); reg.seal(db, h)
        assert ci.cited_by(db, "feature", FEATURE_A) == [h]

    def test_tier_a_failure_leaves_no_citations(self, db):
        """A rejected declaration never became a dataset, so it has no edges."""
        d = valid_declaration()
        del d["lane"]
        with pytest.raises(reg.SpecificationRejected):
            _declare(db, d)
        assert ci.cites(db, reg.declaration_hash(d)) == []

    def test_idempotent_within_same_lineage(self, db):
        a = _declare(db).spec_hash
        b = _declare(db).spec_hash
        assert a == b
        assert len(ci.cites(db, a)) == 8

    def test_duplicate_lineage_refused(self, db):
        _declare(db, lineage="line-a")
        with pytest.raises(res.DuplicateLineageError, match="already exists under lineage"):
            _declare(db, lineage="line-b")

    def test_duplicate_lineage_error_names_both(self, db):
        _declare(db, lineage="line-a")
        with pytest.raises(res.DuplicateLineageError) as exc:
            _declare(db, lineage="line-b")
        assert "line-a" in str(exc.value) and "line-b" in str(exc.value)

    def test_different_content_may_open_a_new_lineage(self, db):
        _declare(db, lineage="line-a")
        d2 = valid_declaration()
        d2["intent"]["research_question"] = "a genuinely different question"
        h = _declare(db, d2, lineage="line-b").spec_hash
        assert reg.get_specification(db, h)["lineage"] == "line-b"

    def test_no_orphaned_declarations(self, db):
        _declare(db)
        d2 = valid_declaration(); d2["intent"]["rationale"] = "second"
        _declare(db, d2)
        assert res.find_unindexed_declarations(db) == []

    def test_orphan_detector_finds_a_bypassed_declaration(self, db):
        """registry.declare_dataset() called directly skips indexing."""
        d = valid_declaration()
        d["intent"]["rationale"] = "bypassed"
        h = reg.declare_dataset(db, d, lineage="x", declared_by="r").spec_hash
        assert h in res.find_unindexed_declarations(db)


# ---------------------------------------------------------------------------
# RF1 — unresolvable or non-Active
# ---------------------------------------------------------------------------

class TestRF1:
    def test_unresolvable_feature_refused_by_name(self, db):
        h = _declare(db).spec_hash
        r = FakeResolver(features={}, labels={LABEL_A: active_label(), LABEL_B: active_label()})
        with pytest.raises(res.ResolutionRefused) as exc:
            res.resolve(db, h, r)
        rf1 = [x for x in exc.value.refusals if x.code == "RF1"]
        assert {x.subject for x in rf1} >= {FEATURE_A, FEATURE_B}
        assert any("unresolvable" in x.detail for x in rf1)

    def test_unresolvable_label_refused_by_name(self, db):
        h = _declare(db).spec_hash
        r = FakeResolver(features={FEATURE_A: active_feature(), FEATURE_B: active_feature()}, labels={})
        with pytest.raises(res.ResolutionRefused) as exc:
            res.resolve(db, h, r)
        assert {x.subject for x in exc.value.refusals if x.code == "RF1"} >= {LABEL_A, LABEL_B}

    @pytest.mark.parametrize("phase", ["Registered", "Deprecated", "Superseded", "Retired", "Rejected"])
    def test_non_active_feature_refused_naming_its_phase(self, db, phase):
        h = _declare(db).spec_hash
        r = good_resolver()
        r.features[FEATURE_A] = {"phase": phase, "warmup_periods": 10}
        with pytest.raises(res.ResolutionRefused) as exc:
            res.resolve(db, h, r)
        hit = [x for x in exc.value.refusals if x.subject == FEATURE_A]
        assert hit and phase in hit[0].detail

    @pytest.mark.parametrize("phase", ["Registered", "Deprecated", "Superseded", "Retired", "Rejected"])
    def test_non_active_label_refused_naming_its_phase(self, db, phase):
        h = _declare(db).spec_hash
        r = good_resolver()
        r.labels[LABEL_A] = {"phase": phase, "horizon_days": 30}
        with pytest.raises(res.ResolutionRefused) as exc:
            res.resolve(db, h, r)
        hit = [x for x in exc.value.refusals if x.subject == LABEL_A]
        assert hit and phase in hit[0].detail

    def test_registered_label_refusal_explains_why(self, db):
        """The four Registered Label Specifications WBS 3.11 deferred must be
        refused with the reason, not just the phase."""
        h = _declare(db).spec_hash
        r = good_resolver()
        r.labels[LABEL_A] = {"phase": "Registered", "horizon_days": 30}
        with pytest.raises(res.ResolutionRefused) as exc:
            res.resolve(db, h, r)
        hit = [x for x in exc.value.refusals if x.subject == LABEL_A][0]
        assert "binding equivalence" in hit.detail

    def test_active_references_pass(self, db):
        h = _declare(db).spec_hash
        report = res.resolve(db, h, good_resolver())
        assert report.ok is True
        assert {x.reference for x in report.resolved if x.kind == "feature"} == {FEATURE_A, FEATURE_B}

    def test_all_refusals_reported_at_once(self, db):
        h = _declare(db).spec_hash
        with pytest.raises(res.ResolutionRefused) as exc:
            res.resolve(db, h, FakeResolver(features={}, labels={}))
        assert len(exc.value.refusals) == 4  # 2 features + 2 labels


# ---------------------------------------------------------------------------
# RF5 — maturity unsatisfiable
# ---------------------------------------------------------------------------

class TestRF5:
    def test_horizon_past_as_of_refused(self, db):
        d = valid_declaration()
        d["temporal_scope"]["window_end"] = "2026-08-01"
        d["temporal_scope"]["as_of"] = "2026-08-07"   # only 6 days of headroom
        h = _declare(db, d).spec_hash
        r = good_resolver()  # horizon 30d
        with pytest.raises(res.ResolutionRefused) as exc:
            res.resolve(db, h, r)
        rf5 = [x for x in exc.value.refusals if x.code == "RF5"]
        assert rf5 and "matures" in rf5[0].detail

    def test_sufficient_headroom_passes(self, db):
        d = valid_declaration()
        d["temporal_scope"]["window_end"] = "2026-06-01"
        d["temporal_scope"]["as_of"] = "2026-08-07"   # 67 days > 30
        h = _declare(db, d).spec_hash
        assert res.resolve(db, h, good_resolver()).ok is True

    def test_boundary_exactly_matured_passes(self, db):
        d = valid_declaration()
        d["temporal_scope"]["window_end"] = "2026-07-08"
        d["temporal_scope"]["as_of"] = "2026-08-07"   # exactly 30 days
        h = _declare(db, d).spec_hash
        assert res.resolve(db, h, good_resolver()).ok is True

    def test_one_day_short_refused(self, db):
        d = valid_declaration()
        d["temporal_scope"]["window_end"] = "2026-07-09"
        d["temporal_scope"]["as_of"] = "2026-08-07"   # 29 days
        h = _declare(db, d).spec_hash
        with pytest.raises(res.ResolutionRefused) as exc:
            res.resolve(db, h, good_resolver())
        assert any(x.code == "RF5" for x in exc.value.refusals)

    def test_non_demanding_requirement_is_not_checked(self, db):
        """A declaration not asking for maturity makes no claim RF5 can
        falsify."""
        d = valid_declaration()
        d["temporal_scope"]["window_end"] = "2026-08-01"
        d["temporal_scope"]["as_of"] = "2026-08-07"
        for ref in d["label_references"]:
            ref["maturity_requirement"] = "partial"
        h = _declare(db, d).spec_hash
        assert res.resolve(db, h, good_resolver()).ok is True

    def test_names_the_offending_label(self, db):
        d = valid_declaration()
        d["temporal_scope"]["window_end"] = "2026-08-01"
        d["temporal_scope"]["as_of"] = "2026-08-07"
        h = _declare(db, d).spec_hash
        r = good_resolver()
        r.labels[LABEL_B] = active_label(horizon=1)  # B is fine, A is not
        with pytest.raises(res.ResolutionRefused) as exc:
            res.resolve(db, h, r)
        rf5 = [x for x in exc.value.refusals if x.code == "RF5"]
        assert [x.subject for x in rf5] == [LABEL_A]

    def test_not_double_reported_when_rf1_already_refused(self, db):
        d = valid_declaration()
        d["temporal_scope"]["window_end"] = "2026-08-01"
        d["temporal_scope"]["as_of"] = "2026-08-07"
        h = _declare(db, d).spec_hash
        r = FakeResolver(features={FEATURE_A: active_feature(), FEATURE_B: active_feature()}, labels={})
        with pytest.raises(res.ResolutionRefused) as exc:
            res.resolve(db, h, r)
        assert [x.code for x in exc.value.refusals].count("RF5") == 0


# ---------------------------------------------------------------------------
# RF6 — warm-up unavailable
# ---------------------------------------------------------------------------

class TestRF6:
    def test_insufficient_warmup_refused(self, db):
        d = valid_declaration()
        d["temporal_scope"]["window_start"] = "2021-01-01"
        h = _declare(db, d).spec_hash
        r = good_resolver(coverage={"dvol": ("2020-12-28", "2026-08-01")})  # only 4 days before
        r.features[FEATURE_A] = active_feature(warmup=30)
        with pytest.raises(res.ResolutionRefused) as exc:
            res.resolve(db, h, r)
        rf6 = [x for x in exc.value.refusals if x.code == "RF6"]
        assert rf6 and "warm-up" in rf6[0].detail

    def test_sufficient_warmup_passes(self, db):
        d = valid_declaration()
        d["temporal_scope"]["window_start"] = "2021-01-01"
        h = _declare(db, d).spec_hash
        assert res.resolve(db, h, good_resolver()).ok is True

    def test_boundary_exact_warmup_passes(self, db):
        d = valid_declaration()
        d["temporal_scope"]["window_start"] = "2021-01-31"
        h = _declare(db, d).spec_hash
        r = good_resolver(coverage={"dvol": ("2021-01-01", "2026-08-01")})
        r.features[FEATURE_A] = active_feature(warmup=30)
        r.features[FEATURE_B] = active_feature(warmup=30)
        assert res.resolve(db, h, r).ok is True

    def test_one_day_short_refused(self, db):
        d = valid_declaration()
        d["temporal_scope"]["window_start"] = "2021-01-30"
        h = _declare(db, d).spec_hash
        r = good_resolver(coverage={"dvol": ("2021-01-01", "2026-08-01")})
        r.features[FEATURE_A] = active_feature(warmup=30)
        with pytest.raises(res.ResolutionRefused) as exc:
            res.resolve(db, h, r)
        assert any(x.code == "RF6" for x in exc.value.refusals)

    def test_latest_starting_source_is_binding(self, db):
        """A feature reads whichever sources it needs; the constraint is the
        source that starts latest."""
        d = valid_declaration()
        d["temporal_scope"]["window_start"] = "2021-01-01"
        h = _declare(db, d).spec_hash
        r = good_resolver(coverage={
            "dvol": ("2019-01-01", "2026-08-01"),
            "spot": ("2020-12-29", "2026-08-01"),  # the binding one
        })
        r.features[FEATURE_A] = active_feature(warmup=30)
        with pytest.raises(res.ResolutionRefused) as exc:
            res.resolve(db, h, r)
        assert "spot" in [x for x in exc.value.refusals if x.code == "RF6"][0].detail

    def test_no_coverage_information_skips_rf6(self, db):
        """RF6 cannot be evaluated without coverage; it must not fire
        spuriously (assert, never guess)."""
        d = valid_declaration()
        h = _declare(db, d).spec_hash
        r = good_resolver(coverage={})
        r.features[FEATURE_A] = active_feature(warmup=99999)
        assert res.resolve(db, h, r).ok is True

    def test_zero_warmup_never_refuses(self, db):
        d = valid_declaration()
        d["temporal_scope"]["window_start"] = "2021-01-01"
        h = _declare(db, d).spec_hash
        r = good_resolver(coverage={"dvol": ("2021-01-01", "2026-08-01")})
        r.features[FEATURE_A] = active_feature(warmup=0)
        r.features[FEATURE_B] = active_feature(warmup=0)
        assert res.resolve(db, h, r).ok is True


# ---------------------------------------------------------------------------
# Stage 2 — resolution mechanics
# ---------------------------------------------------------------------------

class TestResolve:
    def test_happy_path_moves_to_resolved(self, db):
        h = _declare(db).spec_hash
        report = res.resolve(db, h, good_resolver())
        assert report.ok is True
        assert reg.get_specification(db, h)["phase"] == "Resolved"

    def test_every_reference_bound(self, db):
        h = _declare(db).spec_hash
        report = res.resolve(db, h, good_resolver())
        kinds = {r.kind for r in report.resolved}
        assert kinds == {"feature", "label", "evidence"}

    def test_declared_pins_win_over_resolver(self, db):
        h = _declare(db).spec_hash
        r = good_resolver(revisions={"dvol": "rev9"})
        report = res.resolve(db, h, r)
        assert report.pinned_revisions["dvol"] == "rev2"  # declaration said rev2

    def test_resolver_supplies_unstated_pins(self, db):
        h = _declare(db).spec_hash
        r = good_resolver(revisions={"funding": "rev7"})
        report = res.resolve(db, h, r)
        assert report.pinned_revisions["funding"] == "rev7"

    def test_refusal_moves_to_rejected_permanently(self, db):
        h = _declare(db).spec_hash
        with pytest.raises(res.ResolutionRefused):
            res.resolve(db, h, FakeResolver(features={}, labels={}))
        spec = reg.get_specification(db, h)
        assert spec["phase"] == "Rejected"
        assert "RF1" in spec["rejection_reason"]

    def test_rejection_persisted_before_raising(self, db):
        """P4-D3: a caller swallowing the error cannot erase the record."""
        h = _declare(db).spec_hash
        try:
            res.resolve(db, h, FakeResolver(features={}, labels={}))
        except res.ResolutionRefused:
            pass
        assert reg.get_specification(db, h)["phase"] == "Rejected"

    def test_no_arc_out_of_rejected_after_refusal(self, db):
        """Rejected is terminal (§11). A second resolve -- even with a
        resolver that would now succeed -- is refused by the phase guard,
        naming Rejected. TransitionError, not ResolutionRefused: the refusal
        points never run, because the row is not resolvable at all."""
        h = _declare(db).spec_hash
        with pytest.raises(res.ResolutionRefused):
            res.resolve(db, h, FakeResolver(features={}, labels={}))
        with pytest.raises(reg.TransitionError, match="'Rejected'"):
            res.resolve(db, h, good_resolver())
        assert reg.get_specification(db, h)["phase"] == "Rejected"

    def test_resolve_requires_declared_phase(self, db):
        h = _declare(db).spec_hash
        res.resolve(db, h, good_resolver())
        with pytest.raises(reg.TransitionError, match="not 'Declared'"):
            res.resolve(db, h, good_resolver())

    def test_unknown_hash_refused(self, db):
        with pytest.raises(res.ResolutionError, match="no such specification"):
            res.resolve(db, "0" * 64, good_resolver())

    def test_refusals_by_code_helper(self, db):
        h = _declare(db).spec_hash
        try:
            res.resolve(db, h, FakeResolver(features={}, labels={}))
        except res.ResolutionRefused as exc:
            report = res.ResolutionReport(h, False, refusals=exc.refusals)
            assert len(report.refusals_by_code("RF1")) == 4
            assert report.refusals_by_code("RF5") == []

    def test_declare_and_resolve_combined(self, db):
        report = res.declare_and_resolve(
            db, valid_declaration(), lineage="line-a",
            declared_by="rohith", resolver=good_resolver(),
        )
        assert report.ok is True
        assert len(ci.cites(db, report.spec_hash)) == 8

    def test_combined_still_applies_tier_a(self, db):
        d = valid_declaration(); del d["lane"]
        with pytest.raises(reg.SpecificationRejected):
            res.declare_and_resolve(
                db, d, lineage="l", declared_by="r", resolver=good_resolver())

    def test_lr4_assertion_on_foreign_db(self, tmp_path):
        foreign = tmp_path / "foreign.db"
        sqlite3.connect(foreign).execute("CREATE TABLE x (a TEXT)")
        with pytest.raises(reg.NotADatasetBuilderStore):
            res.resolve(foreign, "0" * 64, good_resolver())


# ---------------------------------------------------------------------------
# StoreResolver adapter
# ---------------------------------------------------------------------------

class TestStoreResolver:
    def test_returns_none_without_stores(self):
        r = res.StoreResolver()
        assert r.get_feature("x") is None and r.get_label("x") is None

    def test_coverage_and_revisions(self):
        r = res.StoreResolver(
            corpus_coverage_map={"dvol": ("2021-01-01", "2026-01-01")},
            revision_map={"dvol": "rev2"},
        )
        assert r.corpus_coverage("dvol") == ("2021-01-01", "2026-01-01")
        assert r.pinned_revision("dvol") == "rev2"
        assert r.corpus_coverage("nope") is None

    def test_satisfies_the_protocol(self):
        assert isinstance(res.StoreResolver(), res.ReferenceResolver)

    def test_reads_a_real_feature_store(self, tmp_path):
        from feature_store import registry as fs
        p = tmp_path / "feature_store.db"
        fs.init_db(p)
        r = res.StoreResolver(feature_store_db=p)
        assert r.get_feature("0" * 64) is None  # empty store, resolves to None

    def test_reads_a_real_label_store(self, tmp_path):
        from label_store import registry as ls
        p = tmp_path / "label_store.db"
        ls.init_db(p)
        r = res.StoreResolver(label_store_db=p)
        assert r.get_label("0" * 64) is None


# ---------------------------------------------------------------------------
# TV3 — sabotage
# ---------------------------------------------------------------------------

class TestTV3Sabotage:
    def test_rf1_is_not_vacuous(self, db):
        """If RF1 never fired, every non-Active test would pass trivially."""
        h = _declare(db).spec_hash
        with pytest.raises(res.ResolutionRefused):
            res.resolve(db, h, FakeResolver(features={}, labels={}))

    def test_rf1_does_not_refuse_everything(self, db):
        h = _declare(db).spec_hash
        assert res.resolve(db, h, good_resolver()).ok is True

    def test_rf5_is_not_vacuous(self, db):
        d = valid_declaration()
        d["temporal_scope"]["window_end"] = "2026-08-06"
        d["temporal_scope"]["as_of"] = "2026-08-07"
        h = _declare(db, d).spec_hash
        with pytest.raises(res.ResolutionRefused) as exc:
            res.resolve(db, h, good_resolver())
        assert any(x.code == "RF5" for x in exc.value.refusals)

    def test_rf6_is_not_vacuous(self, db):
        d = valid_declaration()
        d["temporal_scope"]["window_start"] = "2021-01-01"
        h = _declare(db, d).spec_hash
        r = good_resolver(coverage={"dvol": ("2020-12-31", "2026-08-01")})
        r.features[FEATURE_A] = active_feature(warmup=365)
        with pytest.raises(res.ResolutionRefused) as exc:
            res.resolve(db, h, r)
        assert any(x.code == "RF6" for x in exc.value.refusals)

    def test_indexing_is_not_vacuous(self, db):
        h = _declare(db).spec_hash
        assert len(ci.cites(db, h)) == 8

    def test_duplicate_lineage_guard_is_not_vacuous(self, db):
        _declare(db, lineage="line-a")
        with pytest.raises(res.DuplicateLineageError):
            _declare(db, lineage="line-b")


# ---------------------------------------------------------------------------
# Regression — 4.1 and 4.2 invariants preserved
# ---------------------------------------------------------------------------

class TestRegression:
    def test_registry_lifecycle_unchanged(self, db):
        h = _declare(db).spec_hash
        res.resolve(db, h, good_resolver())
        reg.mark_validated(db, h)
        reg.seal(db, h)
        assert reg.is_citable(db, h) is True

    def test_sealed_immutability_still_enforced(self, db):
        h = _declare(db).spec_hash
        res.resolve(db, h, good_resolver())
        reg.mark_validated(db, h); reg.seal(db, h)
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="DATASET_TAMPER"):
                con.execute(
                    "UPDATE dataset_specifications SET declaration_json='{}' WHERE spec_hash=?",
                    (h,))
        finally:
            con.close()

    def test_citations_append_only_still_enforced(self, db):
        _declare(db)
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                con.execute("DELETE FROM citations")
        finally:
            con.close()

    def test_bidirectional_consistency_after_resolution(self, db):
        h = _declare(db).spec_hash
        res.resolve(db, h, good_resolver())
        ok, errors = ci.verify_bidirectional_consistency(db)
        assert ok is True and errors == []

    def test_content_hash_unchanged_by_resolution(self, db):
        h = _declare(db).spec_hash
        res.resolve(db, h, good_resolver())
        spec = reg.get_specification(db, h)
        assert reg.declaration_hash(spec["declaration"]) == h

    def test_rejected_dataset_keeps_its_citations(self, db):
        h = _declare(db).spec_hash
        with pytest.raises(res.ResolutionRefused):
            res.resolve(db, h, FakeResolver(features={}, labels={}))
        assert len(ci.cites(db, h)) == 8

    def test_rejected_dataset_does_not_pin_inputs(self, db):
        h = _declare(db).spec_hash
        with pytest.raises(res.ResolutionRefused):
            res.resolve(db, h, FakeResolver(features={}, labels={}))
        assert ci.cited_by(db, "feature", FEATURE_A) == []

    def test_no_other_store_touched(self, tmp_path):
        p = tmp_path / "d.db"
        reg.init_db(p); ci.init_db(p)
        res.declare_and_resolve(
            p, valid_declaration(), lineage="l",
            declared_by="r", resolver=good_resolver())
        assert {f.name for f in tmp_path.rglob("*.db")} == {"d.db"}
