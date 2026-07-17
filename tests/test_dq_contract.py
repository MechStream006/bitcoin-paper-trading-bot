"""
Data Completeness Framework — contract loading, versioning, resolution (PR-7).

Covers: the three resolution operations (§5) and their deliberate separation,
immutable loading from the registry, hash-addressing (never date resolution),
contract evolution, tamper detection, the CRLF/LF case, and the structural
guarantee that replay never reads the filesystem.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "dq"))
sys.path.insert(0, str(ROOT / "research" / "evidence_library"))

import contract_receipts as cre  # noqa: E402
import contract_registry as cr  # noqa: E402

import contract_loader as loader  # noqa: E402
import readiness  # noqa: E402
from contract import Contract, parse_contract  # noqa: E402


@pytest.fixture
def db(tmp_path) -> Path:
    p = tmp_path / "dq.db"
    readiness.init_db(p)
    return p


def make_contract(min_count: int = 3, contract_id: str = "corpus_daily") -> Contract:
    return parse_contract({
        "contract_id": contract_id,
        "grain": "utc_day",
        "inputs": [{
            "id": "funding",
            "source": {"type": "static"},
            "criticality": "required",
            "coverage": {"min_count": min_count},
            "late_arrival_hours": 12,
        }],
    })


def activate(db, contract, reason="activation"):
    return cre.activate_contract_with_receipt(
        db, contract.contract_id, contract.canonical_text(), "rohith", reason
    )


# ---------------------------------------------------------------------------
# The three resolution operations
# ---------------------------------------------------------------------------

class TestResolution:
    def test_fresh_derivation_resolves_the_unique_active_version(self, db):
        activate(db, make_contract(min_count=1))
        activate(db, make_contract(min_count=3), reason="v2")

        resolved = loader.resolve_for_derivation(db, "corpus_daily")
        assert resolved.version == 2
        assert resolved.phase == "active"
        assert resolved.contract.input("funding").coverage.min_count == 3

    def test_no_active_contract_raises_it_does_not_fall_back_to_a_draft(self, db):
        cr.append_contract(db, cr.ContractRecord(
            contract_id="corpus_daily", version=1, contract_schema_version=1,
            body=make_contract().canonical_text(), phase="draft",
            actor="rohith", reason="drafting",
        ))
        with pytest.raises(loader.NoActiveContractError, match="draft binds nothing"):
            loader.resolve_for_derivation(db, "corpus_daily")

    def test_replay_dereferences_by_hash(self, db):
        record = activate(db, make_contract(min_count=1))["contract"]
        activate(db, make_contract(min_count=3), reason="v2")

        # The v1 hash still dereferences to v1's EXACT bytes, though v2 is active.
        resolved = loader.resolve_for_replay(db, record["contract_hash"])
        assert resolved.version == 1
        assert resolved.phase == "superseded"
        assert resolved.contract.input("funding").coverage.min_count == 1

    def test_replay_of_an_unknown_hash_is_a_hard_failure(self, db):
        with pytest.raises(cr.ContractNotFoundError, match="never falls back to the filesystem"):
            loader.resolve_for_replay(db, "0" * 64)

    def test_reprocessing_refuses_without_an_explicit_policy(self, db):
        """§5.1: 'There is no default. A reprocessing job invoked without
        --contract-policy REFUSES TO RUN. The person running a repair at 2am is
        exactly the person who must be made to state which one they mean.'"""
        activate(db, make_contract())
        with pytest.raises(loader.ContractPolicyRequiredError, match="explicit --contract-policy"):
            loader.resolve_for_reprocessing(db, "corpus_daily", None, contract_policy=None)

    def test_reprocessing_pinned_reproduces_the_original_contract(self, db):
        v1 = activate(db, make_contract(min_count=1))["contract"]
        activate(db, make_contract(min_count=3), reason="v2")

        resolved = loader.resolve_for_reprocessing(
            db, "corpus_daily", v1["contract_hash"], contract_policy="pinned"
        )
        assert resolved.version == 1

    def test_reprocessing_current_deliberately_re_judges_history(self, db):
        v1 = activate(db, make_contract(min_count=1))["contract"]
        activate(db, make_contract(min_count=3), reason="v2")

        resolved = loader.resolve_for_reprocessing(
            db, "corpus_daily", v1["contract_hash"], contract_policy="current"
        )
        assert resolved.version == 2

    def test_pinned_without_a_hash_refuses(self, db):
        activate(db, make_contract())
        with pytest.raises(loader.ContractPolicyRequiredError):
            loader.resolve_for_reprocessing(db, "corpus_daily", None, contract_policy="pinned")

    def test_unknown_policy_refuses(self, db):
        activate(db, make_contract())
        with pytest.raises(loader.ContractPolicyRequiredError, match="unknown contract policy"):
            loader.resolve_for_reprocessing(db, "corpus_daily", None, contract_policy="whatever")

    def test_there_is_no_ambiguous_get_contract_function(self):
        """'There must be NO function that gets the contract without the caller
        having chosen which of the three it means. Ambiguity at the call site is
        the defect this whole document exists to eliminate; do not reintroduce it
        in the API.'"""
        assert not hasattr(loader, "get_contract")
        assert not hasattr(loader, "load_contract")
        assert not hasattr(loader, "resolve")


# ---------------------------------------------------------------------------
# Replay never reads the filesystem — structural, not a convention
# ---------------------------------------------------------------------------

class TestNoFilesystemInReplay:
    def test_the_replay_path_contains_no_file_read(self):
        """
        'A git checkout changes history. If the contract is read from
        contracts/*.yaml at replay time, then checking out an older commit
        changes what a 2026-06-14 receipt MEANS. The filesystem is not an
        archive.'

        Proven by AST inspection rather than by trusting a docstring: the ONE
        function in contract_loader that touches the filesystem is
        load_working_copy(), and it is unreachable from the replay path.
        """
        src = (ROOT / "research" / "dq" / "contract_loader.py").read_text(encoding="utf-8")
        tree = ast.parse(src)

        fs_readers = {"read_text", "read_bytes", "open", "load_working_copy"}
        replay_fns = {"resolve_for_replay", "_to_resolved"}

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in replay_fns:
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Call):
                        fn = inner.func
                        name = getattr(fn, "attr", None) or getattr(fn, "id", None)
                        assert name not in fs_readers, (
                            f"{node.name}() reaches {name}() — replay must never read "
                            f"the filesystem"
                        )

    def test_contract_registry_itself_has_no_file_reading_code(self):
        src = (ROOT / "research" / "evidence_library" / "contract_registry.py").read_text(
            encoding="utf-8"
        )
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", None)
                assert name not in ("read_text", "read_bytes"), (
                    "the registry is the archive; it must never read a working copy"
                )


# ---------------------------------------------------------------------------
# Identity, immutability, evolution
# ---------------------------------------------------------------------------

class TestContractIdentity:
    def test_version_is_assigned_monotonically_by_the_tool_not_the_author(self, db):
        """'Two contract authors on two branches cannot produce two different
        v4s.' The author never writes a version; the registry stamps it."""
        for i, floor in enumerate((1, 2, 3), start=1):
            record = activate(db, make_contract(min_count=floor), reason=f"v{i}")["contract"]
            assert record["version"] == i

    def test_exactly_one_active_version_enforced_by_the_database(self, db):
        activate(db, make_contract(min_count=1))
        activate(db, make_contract(min_count=3), reason="v2")

        actives = [
            c for c in cr.get_by_contract_id(db, "corpus_daily") if c["phase"] == "active"
        ]
        assert len(actives) == 1
        assert actives[0]["version"] == 2

    def test_activation_supersedes_the_incumbent(self, db):
        v1 = activate(db, make_contract(min_count=1))["contract"]
        v2 = activate(db, make_contract(min_count=3), reason="v2")["contract"]

        superseded = cr.get_by_hash(db, v1["contract_hash"])
        assert superseded["phase"] == "superseded"
        assert superseded["superseded_by"] == v2["contract_hash"]

    def test_nothing_is_ever_pruned(self, db):
        for floor in (1, 2, 3):
            activate(db, make_contract(min_count=floor), reason=f"floor {floor}")
        assert len(cr.get_all_activated(db, "corpus_daily")) == 3

    def test_hash_is_the_identity_the_integer_is_a_label(self, db):
        a = make_contract(min_count=3)
        b = make_contract(min_count=3)
        assert a.contract_hash() == b.contract_hash()

        c = make_contract(min_count=2)
        assert c.contract_hash() != a.contract_hash()

    def test_byte_identical_activation_is_refused(self, db):
        activate(db, make_contract())
        with pytest.raises(cr.DuplicateContractError):
            activate(db, make_contract(), reason="the same bytes again")

    def test_a_registry_body_round_trips_to_an_equivalent_contract(self, db):
        original = make_contract()
        activate(db, original)
        resolved = loader.resolve_for_derivation(db, "corpus_daily")
        assert resolved.contract.contract_hash() == original.contract_hash()
        assert resolved.contract.to_canonical() == original.to_canonical()

    def test_contract_hash_is_stable_across_processes(self):
        """Content-addressing is worthless if it is process-local. PYTHONHASHSEED
        randomisation must not reach a contract_hash."""
        script = (
            "import sys; sys.path.insert(0, r'%s'); sys.path.insert(0, r'%s');"
            "from contract import parse_contract;"
            "print(parse_contract({'contract_id':'x','grain':'utc_day','inputs':["
            "{'id':'i','source':{'type':'static'},'coverage':{'min_count':3},"
            "'late_arrival_hours':6}]}).contract_hash())"
            % (ROOT / "research" / "dq", ROOT / "research" / "evidence_library")
        )
        digests = {
            subprocess.run(
                [sys.executable, "-c", script], capture_output=True, text=True, check=True
            ).stdout.strip()
            for _ in range(3)
        }
        assert len(digests) == 1


# ---------------------------------------------------------------------------
# Tamper detection
# ---------------------------------------------------------------------------

class TestTamperDetection:
    def test_an_edited_working_copy_is_detected(self, db):
        """'This is FnHashViolation for contracts, and it exists for the same
        reason: a change to what "complete" MEANS, made without going through
        activation, retroactively alters the admissibility of evidence with NO
        TRACE.'"""
        activate(db, make_contract(min_count=3))
        edited = make_contract(min_count=2)   # someone loosened the floor in the file
        with pytest.raises(loader.ContractTamperError, match="CONTRACT_TAMPER"):
            loader.check_tamper(db, edited)

    def test_an_unedited_working_copy_passes(self, db):
        contract = make_contract(min_count=3)
        activate(db, contract)
        loader.check_tamper(db, contract)   # does not raise

    def test_crlf_vs_lf_does_not_fire_a_spurious_violation(self, db, tmp_path):
        """The Windows-dev / Linux-deploy case. 'That lesson is already paid for;
        inherit it.' This test is not optional."""
        contract = make_contract()
        activate(db, contract)

        lf = tmp_path / "lf.yaml"
        lf.write_bytes(contract.canonical_text().encode("utf-8"))
        crlf = tmp_path / "crlf.yaml"
        crlf.write_bytes(contract.canonical_text().replace("\n", "\r\n").encode("utf-8"))

        loader.check_tamper(db, loader.load_working_copy(lf))
        loader.check_tamper(db, loader.load_working_copy(crlf))

    def test_a_mutated_registry_body_is_detected_on_resolve(self, db):
        import sqlite3
        record = activate(db, make_contract())["contract"]
        con = sqlite3.connect(db)
        try:
            con.execute("DROP TRIGGER trg_contracts_guard_update")
            con.execute(
                "UPDATE contract_registry SET body = ? WHERE contract_hash = ?",
                (make_contract(min_count=1).canonical_text(), record["contract_hash"]),
            )
            con.commit()
        finally:
            con.close()

        with pytest.raises(loader.ContractResolutionError, match="CONTRACT_TAMPER"):
            loader.resolve_for_replay(db, record["contract_hash"])

    def test_an_activated_contract_cannot_be_edited_in_place(self, db):
        import sqlite3
        record = activate(db, make_contract())["contract"]
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="CONTRACT_TAMPER"):
                con.execute(
                    "UPDATE contract_registry SET body = 'tampered' WHERE contract_hash = ?",
                    (record["contract_hash"],),
                )
        finally:
            con.close()


# ---------------------------------------------------------------------------
# Working copy (authoring only)
# ---------------------------------------------------------------------------

class TestWorkingCopy:
    def test_load_working_copy(self, tmp_path):
        path = tmp_path / "corpus_daily.yaml"
        path.write_text(
            "contract_id: corpus_daily\n"
            "grain: utc_day\n"
            "inputs:\n"
            "  - id: funding\n"
            "    source: {type: static}\n"
            "    coverage: {min_count: 3}\n"
            "    late_arrival_hours: 12\n",
            encoding="utf-8",
        )
        contract = loader.load_working_copy(path)
        assert contract.contract_id == "corpus_daily"
        assert contract.input("funding").coverage.min_count == 3

    def test_a_working_copy_and_its_registry_body_agree_on_the_hash(self, db, tmp_path):
        path = tmp_path / "c.yaml"
        path.write_text(
            "contract_id: corpus_daily\n"
            "grain: utc_day\n"
            "inputs:\n"
            "  - id: funding\n"
            "    source: {type: static}\n"
            "    coverage: {min_count: 3}\n"
            "    late_arrival_hours: 12\n",
            encoding="utf-8",
        )
        contract = loader.load_working_copy(path)
        activate(db, contract)
        assert loader.resolve_for_derivation(db, "corpus_daily").contract_hash == \
            contract.contract_hash()
