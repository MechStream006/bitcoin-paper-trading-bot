"""WBS 4.8 — Dataset Materialiser, L1/L2 reproducibility
(ADR-I012 §8.2, §7.1, §16).

Roadmap acceptance for 4.8: materialiser; L1/L2; divergence events.

DETERMINISM IS THE PRIMARY REQUIREMENT.
`TestDeterminism` proves byte-identity across repeated runs, provider
iteration order, key order, and fresh processes.
"""

from __future__ import annotations

import ast
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "research", ROOT / "research" / "admissibility",
          ROOT / "research" / "evidence_library"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import receipt_ledger as rl  # noqa: E402

from dataset_builder import citation_index as ci  # noqa: E402
from dataset_builder import materialisation as mat  # noqa: E402
from dataset_builder import registry as reg  # noqa: E402
from dataset_builder import resolution as res  # noqa: E402
from dataset_builder import sealing as sl  # noqa: E402
from dataset_builder import validation as val  # noqa: E402
from tests.test_dataset_registry import valid_declaration  # noqa: E402
from tests.test_dataset_resolution import good_resolver  # noqa: E402
from tests.test_dataset_validation import good_inputs  # noqa: E402

FEATURE_A, FEATURE_B = "f" * 64, "e" * 64
LABEL_A, LABEL_B = "a" * 64, "b" * 64


class FakeProvider:
    """Test double for `ValueProvider`."""

    def __init__(self, features=None, labels=None, evidence=None):
        self.features = dict(features or {})
        self.labels = dict(labels or {})
        self.evidence = dict(evidence or {})
        self.calls: list[tuple] = []

    def feature_values(self, spec_hash, window):
        self.calls.append(("feature", spec_hash, window))
        return self.features.get(spec_hash, [])

    def label_values(self, spec_hash, window):
        self.calls.append(("label", spec_hash, window))
        return self.labels.get(spec_hash, [])

    def evidence_rows(self, source, revision, window):
        self.calls.append(("evidence", source, revision, window))
        return self.evidence.get(source, [])


def rich_provider():
    """Content with floats, ints, strings, nulls and nested values — the
    shapes real research data actually has."""
    return FakeProvider(
        features={
            FEATURE_A: [{"ts": 2, "v": 1.5, "tag": "b"},
                        {"ts": 1, "v": 0.5, "tag": "a"}],
            FEATURE_B: [{"ts": 1, "v": -0.25, "meta": {"src": "x"}}],
        },
        labels={
            LABEL_A: [{"ts": 1, "y": 1, "matured": True}],
            LABEL_B: [{"ts": 1, "y": None}],
        },
        evidence={
            "dvol": [{"ts": 1, "close": 48.1}],
            "spot": [{"ts": 1, "close": 64000.0}],
        },
    )


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "dataset_builder.db"
    reg.init_db(p)
    ci.init_db(p)
    sl.init_db(p)
    mat.init_db(p)
    return p


@pytest.fixture()
def ledger(tmp_path: Path) -> Path:
    p = tmp_path / "ledger.db"
    rl.init_db(p)
    return p


def _sealed(db, ledger, decl=None, lineage="line-a") -> str:
    d = decl if decl is not None else valid_declaration()
    h = res.declare(db, d, lineage=lineage, declared_by="rohith").spec_hash
    res.resolve(db, h, good_resolver())
    report = val.validate(db, h, good_inputs())
    sl.seal_dataset(db, h, report, sealed_by="rohith", ledger_db=ledger)
    return h


# ===========================================================================
# Determinism — the primary requirement
# ===========================================================================

class TestDeterminism:
    def test_repeated_materialisation_is_byte_identical(self, db, ledger):
        h = _sealed(db, ledger)
        a = mat.materialise(db, h, rich_provider())
        b = mat.materialise(db, h, rich_provider())
        assert a.snapshot.content == b.snapshot.content
        assert a.snapshot.sha256 == b.snapshot.sha256

    def test_provider_row_order_does_not_change_bytes(self, db, ledger):
        h = _sealed(db, ledger)
        p1 = rich_provider()
        p2 = rich_provider()
        p2.features[FEATURE_A] = list(reversed(p2.features[FEATURE_A]))
        assert (mat.materialise(db, h, p1).snapshot.sha256
                == mat.materialise(db, h, p2).snapshot.sha256)

    def test_key_order_within_a_row_does_not_change_bytes(self, db, ledger):
        h = _sealed(db, ledger)
        p1 = FakeProvider(features={FEATURE_A: [{"ts": 1, "v": 1.0, "z": "q"}]})
        p2 = FakeProvider(features={FEATURE_A: [{"z": "q", "v": 1.0, "ts": 1}]})
        assert (mat.materialise(db, h, p1).snapshot.sha256
                == mat.materialise(db, h, p2).snapshot.sha256)

    def test_no_timestamp_in_snapshot_content(self, db, ledger):
        """A wall-clock inside the bytes would break L1 on its first re-run."""
        h = _sealed(db, ledger)
        r = mat.materialise(db, h, rich_provider())
        body = r.snapshot.content.decode("utf-8")
        assert "materialised_at" not in body
        assert r.manifest.materialised_at  # it lives on the manifest instead

    def test_engine_version_not_in_snapshot_content(self, db, ledger):
        h = _sealed(db, ledger)
        r = mat.materialise(db, h, rich_provider())
        assert "engine_version" not in r.snapshot.content.decode("utf-8")
        assert r.manifest.engine_version == mat.MATERIALISER_ENGINE_VERSION

    def test_format_version_is_in_the_hashed_payload(self, db, ledger):
        """A serialisation change IS a content change."""
        h = _sealed(db, ledger)
        r = mat.materialise(db, h, rich_provider())
        assert "format_version" in r.snapshot.content.decode("utf-8")

    def test_deterministic_in_a_fresh_process(self, db, ledger, tmp_path):
        """Hash randomisation and dict ordering must not affect the bytes."""
        import subprocess
        h = _sealed(db, ledger)
        expected = mat.materialise(db, h, rich_provider()).snapshot.sha256
        script = tmp_path / "reproduce.py"
        script.write_text(
            "import sys\n"
            f"sys.path[:0] = [r'{ROOT / 'research'}', r'{ROOT / 'research' / 'admissibility'}',"
            f" r'{ROOT / 'research' / 'evidence_library'}', r'{ROOT}']\n"
            "from tests.test_materialisation import rich_provider\n"
            "from dataset_builder import materialisation as mat\n"
            f"r = mat.materialise(r'{db}', '{h}', rich_provider(), record=False)\n"
            "print(r.snapshot.sha256)\n", encoding="utf-8")
        out = subprocess.run([sys.executable, str(script)], capture_output=True,
                             text=True, cwd=ROOT)
        assert out.returncode == 0, out.stderr[-800:]
        assert out.stdout.strip() == expected

    def test_empty_dataset_is_deterministic(self, db, ledger):
        h = _sealed(db, ledger)
        a = mat.materialise(db, h, FakeProvider())
        b = mat.materialise(db, h, FakeProvider())
        assert a.snapshot.sha256 == b.snapshot.sha256
        assert a.snapshot.row_count == 0


class TestFloatNormalisation:
    """`receipts.canonical_json` refuses raw IEEE floats because a float's
    repr is not guaranteed byte-stable. Without normalisation at the assembly
    boundary, L1 would be a guarantee the platform could not keep."""

    def test_floats_become_decimal_strings(self):
        assert mat.normalise_value(1.5) == "1.5"
        assert mat.normalise_value(-0.25) == "-0.25"

    def test_ints_are_left_alone(self):
        assert mat.normalise_value(3) == 3
        assert isinstance(mat.normalise_value(3), int)

    def test_bools_are_left_alone(self):
        assert mat.normalise_value(True) is True

    def test_nan_and_infinities_get_sentinels(self):
        assert mat.normalise_value(float("nan")) == "__NaN__"
        assert mat.normalise_value(float("inf")) == "__Infinity__"
        assert mat.normalise_value(float("-inf")) == "__-Infinity__"

    def test_nested_structures_normalised(self):
        out = mat.normalise_value({"a": [1.5, {"b": 2.5}]})
        assert out == {"a": ["1.5", {"b": "2.5"}]}

    def test_nan_survives_materialisation(self, db, ledger):
        """Real content carries NaN — the published lockbox record has
        `sortino: NaN`. Dropping or zeroing it would corrupt content."""
        h = _sealed(db, ledger)
        p = FakeProvider(features={FEATURE_A: [{"ts": 1, "v": float("nan")}]})
        r = mat.materialise(db, h, p)
        assert "__NaN__" in r.snapshot.content.decode("utf-8")

    def test_float_content_is_stable_across_runs(self, db, ledger):
        h = _sealed(db, ledger)
        p = FakeProvider(features={FEATURE_A: [{"ts": 1, "v": 0.1 + 0.2}]})
        assert (mat.materialise(db, h, p).snapshot.sha256
                == mat.materialise(db, h, p).snapshot.sha256)


# ===========================================================================
# Materialisation mechanics
# ===========================================================================

class TestMaterialise:
    def test_produces_snapshot_and_manifest(self, db, ledger):
        h = _sealed(db, ledger)
        r = mat.materialise(db, h, rich_provider())
        assert r.snapshot.spec_hash == h and r.snapshot.sha256
        assert r.manifest.snapshot_sha256 == r.snapshot.sha256
        assert r.ok is True

    def test_row_count_matches_content(self, db, ledger):
        h = _sealed(db, ledger)
        r = mat.materialise(db, h, rich_provider())
        assert r.snapshot.row_count == 2 + 1 + 1 + 1 + 1 + 1

    def test_column_inventory_recorded(self, db, ledger):
        h = _sealed(db, ledger)
        r = mat.materialise(db, h, rich_provider())
        assert f"features:{FEATURE_A}" in r.manifest.column_inventory
        assert r.manifest.column_inventory[f"features:{FEATURE_A}"] == ["tag", "ts", "v"]

    def test_only_sealed_may_materialise(self, db):
        d = valid_declaration()
        h = res.declare(db, d, lineage="l", declared_by="r").spec_hash
        with pytest.raises(mat.NotMaterialisableError, match="SEALED"):
            mat.materialise(db, h, rich_provider())

    def test_resolved_but_unsealed_refused(self, db):
        d = valid_declaration()
        h = res.declare(db, d, lineage="l", declared_by="r").spec_hash
        res.resolve(db, h, good_resolver())
        with pytest.raises(mat.NotMaterialisableError):
            mat.materialise(db, h, rich_provider())

    def test_superseded_may_still_materialise(self, db, ledger):
        """§10.2 keeps a superseded dataset citable; a citation that cannot be
        materialised is not resolvable in any useful sense."""
        a = _sealed(db, ledger)
        d2 = valid_declaration(); d2["temporal_scope"]["window_end"] = "2025-06-30"
        b = _sealed(db, ledger, d2)
        reg.supersede(db, a, successor_hash=b, change_class="extension")
        assert mat.materialise(db, a, rich_provider()).ok is True

    def test_unknown_hash(self, db):
        with pytest.raises(mat.MaterialisationError, match="no such specification"):
            mat.materialise(db, "0" * 64, rich_provider())

    def test_lr4_on_foreign_db(self, tmp_path):
        foreign = tmp_path / "f.db"
        sqlite3.connect(foreign).execute("CREATE TABLE x (a TEXT)")
        with pytest.raises(reg.NotADatasetBuilderStore):
            mat.materialise(foreign, "0" * 64, rich_provider())

    def test_requires_materialisation_schema(self, tmp_path, ledger):
        p = tmp_path / "d.db"
        reg.init_db(p); ci.init_db(p); sl.init_db(p)   # no mat.init_db
        h = _sealed(p, ledger)
        with pytest.raises(reg.NotADatasetBuilderStore, match="materialisation"):
            mat.materialise(p, h, rich_provider())

    def test_window_passed_to_provider(self, db, ledger):
        h = _sealed(db, ledger)
        p = rich_provider()
        mat.materialise(db, h, p)
        windows = {c[-1] for c in p.calls}
        assert windows == {("2021-01-01", "2025-05-31")}


class TestOffLifecycle:
    def test_materialisation_never_changes_phase(self, db, ledger):
        h = _sealed(db, ledger)
        mat.materialise(db, h, rich_provider())
        assert reg.get_specification(db, h)["phase"] == "Sealed"

    def test_materialisation_never_touches_citations(self, db, ledger):
        h = _sealed(db, ledger)
        before = len(ci.cites(db, h))
        mat.materialise(db, h, rich_provider())
        assert len(ci.cites(db, h)) == before

    def test_materialisation_never_touches_the_sealed_row(self, db, ledger):
        h = _sealed(db, ledger)
        before = sl.get_sealed(db, h)
        mat.materialise(db, h, rich_provider())
        assert sl.get_sealed(db, h) == before

    def test_no_registry_write(self, db, ledger):
        h = _sealed(db, ledger)
        con = sqlite3.connect(db)
        before = con.execute(
            "SELECT COUNT(*) FROM dataset_specifications").fetchone()[0]
        con.close()
        mat.materialise(db, h, rich_provider())
        con = sqlite3.connect(db)
        after = con.execute(
            "SELECT COUNT(*) FROM dataset_specifications").fetchone()[0]
        con.close()
        assert before == after


# ===========================================================================
# Manifest — self-describing without registry access
# ===========================================================================

class TestManifest:
    def test_manifest_is_self_describing(self, db, ledger):
        h = _sealed(db, ledger)
        r = mat.materialise(db, h, rich_provider())
        ok, errors = mat.verify_manifest_self_describing(r.manifest)
        assert ok is True and errors == []

    def test_manifest_carries_provenance_closure(self, db, ledger):
        h = _sealed(db, ledger)
        r = mat.materialise(db, h, rich_provider())
        assert r.manifest.provenance_closure["declaration_hash"] == h
        assert "citations" in r.manifest.provenance_closure

    def test_manifest_carries_the_capped_ceiling(self, db, ledger):
        """HZ-4.4-1 carried through: the manifest shows the CAPPED ceiling the
        seal recorded, not a recomputed one."""
        h = _sealed(db, ledger)
        r = mat.materialise(db, h, rich_provider())
        assert r.manifest.admissibility_ceiling == sl.admissibility_ceiling(db, h)

    def test_manifest_hash_is_deterministic(self, db, ledger):
        """Two manifests built from identical content hash identically. The
        manifest's own `materialised_at` differs between runs by design, so
        this compares a rebuilt manifest with that field held fixed -- proving
        the hash depends on content, not on when it was computed."""
        h = _sealed(db, ledger)
        r = mat.materialise(db, h, rich_provider())
        rebuilt = mat.Manifest(**r.manifest.to_dict())
        assert rebuilt.sha256 == r.manifest.sha256

    def test_manifest_hash_changes_with_content(self, db, ledger):
        h = _sealed(db, ledger)
        r = mat.materialise(db, h, rich_provider())
        d = r.manifest.to_dict(); d["row_count"] = d["row_count"] + 1
        assert mat.Manifest(**d).sha256 != r.manifest.sha256

    def test_manifest_records_exclusions_from_content(self, db, ledger):
        h = _sealed(db, ledger)
        r = mat.materialise(db, h, rich_provider())
        excluded = r.manifest.verification["excluded_from_content"]
        assert "materialised_at" in excluded and "engine_version" in excluded

    def test_incomplete_manifest_is_detected(self):
        m = mat.Manifest(
            spec_hash="", snapshot_sha256="", row_count=0, column_inventory={},
            provenance_closure={}, admissibility_ceiling={},
            format_version="", engine_version="", materialised_at="",
            verification={})
        ok, errors = mat.verify_manifest_self_describing(m)
        assert ok is False and errors


# ===========================================================================
# L1 — bit reproducibility
# ===========================================================================

class TestL1:
    def test_pin_then_verify_passes(self, db, ledger):
        h = _sealed(db, ledger)
        r = mat.materialise(db, h, rich_provider())
        mat.pin_snapshot(db, h, r, pinned_by="rohith")
        v = mat.verify_l1(db, h, rich_provider())
        assert v.ok is True and v.level_verified == "L1"

    def test_l1_detects_changed_content(self, db, ledger):
        h = _sealed(db, ledger)
        mat.pin_snapshot(db, h, mat.materialise(db, h, rich_provider()),
                         pinned_by="rohith")
        changed = rich_provider()
        changed.features[FEATURE_A].append({"ts": 3, "v": 9.0})
        v = mat.verify_l1(db, h, changed)
        assert v.ok is False and v.divergence is not None
        assert v.divergence.actual_rows == 8   # 7 baseline rows + 1 appended

    def test_l1_requires_a_pin(self, db, ledger):
        h = _sealed(db, ledger)
        with pytest.raises(mat.MaterialisationError, match="no pinned snapshot"):
            mat.verify_l1(db, h, rich_provider())

    def test_double_pin_refused(self, db, ledger):
        h = _sealed(db, ledger)
        r = mat.materialise(db, h, rich_provider())
        mat.pin_snapshot(db, h, r, pinned_by="rohith")
        with pytest.raises(mat.AlreadyPinnedError, match="write-once"):
            mat.pin_snapshot(db, h, r, pinned_by="rohith")

    def test_pin_refuses_another_datasets_snapshot(self, db, ledger):
        a = _sealed(db, ledger)
        d2 = valid_declaration(); d2["intent"]["rationale"] = "second"
        b = _sealed(db, ledger, d2, lineage="line-b")
        ra = mat.materialise(db, a, rich_provider())
        with pytest.raises(mat.MaterialisationError, match="another dataset"):
            mat.pin_snapshot(db, b, ra, pinned_by="rohith")

    def test_pin_requires_pinned_by(self, db, ledger):
        h = _sealed(db, ledger)
        r = mat.materialise(db, h, rich_provider())
        with pytest.raises(mat.MaterialisationError, match="pinned_by"):
            mat.pin_snapshot(db, h, r, pinned_by="")

    def test_pin_is_immutable(self, db, ledger):
        h = _sealed(db, ledger)
        mat.pin_snapshot(db, h, mat.materialise(db, h, rich_provider()),
                         pinned_by="rohith")
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                con.execute("UPDATE snapshot_pins SET snapshot_sha256='x'")
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                con.execute("DELETE FROM snapshot_pins")
        finally:
            con.close()


# ===========================================================================
# L2 — semantic reproducibility and divergence
# ===========================================================================

class TestL2:
    def test_l2_reproduces_unchanged_content(self, db, ledger):
        h = _sealed(db, ledger)
        mat.materialise(db, h, rich_provider())
        r = mat.rematerialise_l2(db, h, rich_provider())
        assert r.ok is True and r.level_verified == "L2"

    def test_l2_divergence_is_returned_not_raised(self, db, ledger):
        """§16: different content after an input correction is the CORRECT
        outcome. A divergence is information, not an error."""
        h = _sealed(db, ledger)
        mat.materialise(db, h, rich_provider())
        corrected = rich_provider()
        corrected.evidence["dvol"] = [{"ts": 1, "close": 49.9}]
        r = mat.rematerialise_l2(db, h, corrected)
        assert r.divergence is not None
        assert r.ok is False

    def test_divergence_names_both_hashes(self, db, ledger):
        h = _sealed(db, ledger)
        first = mat.materialise(db, h, rich_provider())
        corrected = rich_provider()
        corrected.evidence["dvol"] = [{"ts": 1, "close": 49.9}]
        d = mat.rematerialise_l2(db, h, corrected).divergence
        assert d.expected_sha256 == first.snapshot.sha256
        assert d.actual_sha256 != d.expected_sha256
        assert "divergence" in str(d)

    def test_divergence_row_delta(self, db, ledger):
        h = _sealed(db, ledger)
        mat.materialise(db, h, rich_provider())
        more = rich_provider()
        more.features[FEATURE_A].append({"ts": 9, "v": 1.0})
        assert mat.rematerialise_l2(db, h, more).divergence.row_delta == 1

    def test_divergence_is_recorded_permanently(self, db, ledger):
        """A divergence cannot be discovered and then quietly forgotten."""
        h = _sealed(db, ledger)
        mat.materialise(db, h, rich_provider())
        corrected = rich_provider()
        corrected.evidence["dvol"] = [{"ts": 1, "close": 49.9}]
        mat.rematerialise_l2(db, h, corrected)
        assert len(mat.divergences(db, h)) == 1

    def test_nothing_is_reconciled(self, db, ledger):
        """IQROS-P3: reported, never silently reconciled. The pin and the
        recorded history are both untouched by a divergence."""
        h = _sealed(db, ledger)
        first = mat.materialise(db, h, rich_provider())
        corrected = rich_provider()
        corrected.evidence["dvol"] = [{"ts": 1, "close": 49.9}]
        mat.rematerialise_l2(db, h, corrected)
        history = mat.materialisation_history(db, h)
        assert history[0]["snapshot_sha256"] == first.snapshot.sha256

    def test_l2_requires_a_prior_materialisation(self, db, ledger):
        h = _sealed(db, ledger)
        with pytest.raises(mat.MaterialisationError, match="never been materialised"):
            mat.rematerialise_l2(db, h, rich_provider())

    def test_l2_accepts_an_explicit_expected_hash(self, db, ledger):
        h = _sealed(db, ledger)
        r = mat.rematerialise_l2(db, h, rich_provider(),
                                 expected_sha256="0" * 64)
        assert r.divergence is not None

    def test_l1_and_l2_are_not_conflated(self, db, ledger):
        """§16: conflating the levels is how reproducibility claims become
        unfalsifiable. Each call reports exactly which level it verified."""
        h = _sealed(db, ledger)
        r = mat.materialise(db, h, rich_provider())
        mat.pin_snapshot(db, h, r, pinned_by="rohith")
        assert mat.verify_l1(db, h, rich_provider()).level_verified == "L1"
        assert mat.rematerialise_l2(db, h, rich_provider()).level_verified == "L2"


class TestHistory:
    def test_history_is_append_only(self, db, ledger):
        h = _sealed(db, ledger)
        mat.materialise(db, h, rich_provider())
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="write-once"):
                con.execute("UPDATE materialisations SET row_count = 0")
            with pytest.raises(sqlite3.IntegrityError, match="write-once"):
                con.execute("DELETE FROM materialisations")
        finally:
            con.close()

    def test_history_ordered_oldest_first(self, db, ledger):
        h = _sealed(db, ledger)
        for _ in range(3):
            mat.materialise(db, h, rich_provider())
        history = mat.materialisation_history(db, h)
        assert len(history) == 3
        assert [r["id"] for r in history] == sorted(r["id"] for r in history)

    def test_divergences_across_all_datasets(self, db, ledger):
        h = _sealed(db, ledger)
        mat.materialise(db, h, rich_provider())
        corrected = rich_provider()
        corrected.evidence["dvol"] = [{"ts": 1, "close": 1.0}]
        mat.rematerialise_l2(db, h, corrected)
        assert len(mat.divergences(db)) == 1


# ===========================================================================
# TV3 sabotage
# ===========================================================================

class TestTV3Sabotage:
    def test_determinism_is_not_vacuous(self, db, ledger):
        """If assembly returned constant content, every determinism test would
        pass trivially. Different inputs must give different bytes."""
        h = _sealed(db, ledger)
        a = mat.materialise(db, h, rich_provider())
        b = mat.materialise(db, h, FakeProvider(features={FEATURE_A: [{"ts": 1}]}))
        assert a.snapshot.sha256 != b.snapshot.sha256

    def test_divergence_detection_is_not_vacuous(self, db, ledger):
        h = _sealed(db, ledger)
        mat.materialise(db, h, rich_provider())
        assert mat.rematerialise_l2(db, h, rich_provider()).divergence is None
        changed = rich_provider()
        changed.evidence["dvol"] = [{"ts": 1, "close": 99.0}]
        assert mat.rematerialise_l2(db, h, changed).divergence is not None

    def test_row_sort_is_load_bearing(self):
        a = mat._normalise_rows([{"ts": 2}, {"ts": 1}])
        b = mat._normalise_rows([{"ts": 1}, {"ts": 2}])
        assert a == b

    def test_float_normalisation_is_load_bearing(self):
        from receipts import canonical_json
        with pytest.raises(Exception):
            canonical_json({"v": 1.5})       # raw float refused
        canonical_json(mat.normalise_value({"v": 1.5}))  # normalised accepted

    def test_phase_guard_is_load_bearing(self, db):
        d = valid_declaration()
        h = res.declare(db, d, lineage="l", declared_by="r").spec_hash
        with pytest.raises(mat.NotMaterialisableError):
            mat.materialise(db, h, rich_provider())


# ===========================================================================
# Regression — 4.1–4.7 invariants and RF1–RF6
# ===========================================================================

class TestRegression:
    def test_no_operational_import(self):
        src = (ROOT / "research/dataset_builder/materialisation.py").read_text(encoding="utf-8")
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, ast.ImportFrom) and n.module:
                assert n.module.split(".")[0] != "src"
            elif isinstance(n, ast.Import):
                assert all(a.name.split(".")[0] != "src" for a in n.names)

    def test_rf1_to_rf6_unchanged(self):
        assert set(res.DEFERRED_REFUSALS) == {"RF4"}
        assert val.DEFERRED_CHECKS == {}

    def test_declaration_hash_unchanged_by_materialisation(self, db, ledger):
        h = _sealed(db, ledger)
        mat.materialise(db, h, rich_provider())
        spec = reg.get_specification(db, h)
        assert reg.declaration_hash(spec["declaration"]) == h

    def test_citation_graph_intact(self, db, ledger):
        h = _sealed(db, ledger)
        mat.materialise(db, h, rich_provider())
        ok, errors = ci.verify_bidirectional_consistency(db)
        assert ok is True and errors == []

    def test_sealed_immutability_still_enforced(self, db, ledger):
        h = _sealed(db, ledger)
        mat.materialise(db, h, rich_provider())
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="DG-3"):
                con.execute("UPDATE sealed_datasets SET sealed_by='x'")
        finally:
            con.close()

    def test_receipt_chain_unaffected(self, db, ledger):
        h = _sealed(db, ledger)
        mat.materialise(db, h, rich_provider())
        assert rl.verify_chain(ledger).ok is True

    def test_result_invariance_still_holds(self):
        """4.6's baseline re-proved, as every milestone must."""
        import hashlib, json
        baseline = json.loads((ROOT / "BASELINE_46.json").read_text())
        for rel, was in baseline.items():
            assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == was, rel

    def test_full_pipeline_declare_to_materialise(self, db, ledger):
        h = _sealed(db, ledger)
        r = mat.materialise(db, h, rich_provider())
        mat.pin_snapshot(db, h, r, pinned_by="rohith")
        assert reg.is_citable(db, h) is True
        assert mat.verify_l1(db, h, rich_provider()).ok is True
        ok, _ = mat.verify_manifest_self_describing(r.manifest)
        assert ok is True

    def test_no_other_store_touched(self, tmp_path):
        p = tmp_path / "d.db"; led = tmp_path / "l.db"
        reg.init_db(p); ci.init_db(p); sl.init_db(p); mat.init_db(p); rl.init_db(led)
        h = _sealed(p, led)
        mat.materialise(p, h, rich_provider())
        assert {f.name for f in tmp_path.rglob("*.db")} == {"d.db", "l.db"}
