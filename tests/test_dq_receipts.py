"""
Data Completeness Framework — evaluation receipts, replay, point-in-time (PR-7).

Covers: receipt emission through the EXISTING governance ledger, the
data_readiness projection, historical replay under the HISTORICAL contract,
engine/check/rule versioning, unknown-rule and unknown-check fail-loud, bootstrap
handling, tamper detection, and ledger validation.

The load-bearing test in this file is
`TestHistoricalReplay::test_replay_uses_the_historical_contract_not_the_current_one`.
Everything else is machinery in service of it.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "dq"))
sys.path.insert(0, str(ROOT / "research" / "evidence_library"))

import contract_receipts as cre  # noqa: E402
import contract_registry as cr  # noqa: E402
import receipt_ledger as rl  # noqa: E402
import receipts as rcp  # noqa: E402

import engine  # noqa: E402
import readiness  # noqa: E402
import rules  # noqa: E402
import validation  # noqa: E402
from checks import CHECK_REGISTRY, UnknownCheckError  # noqa: E402
from contract import Contract, parse_contract  # noqa: E402
from engine import ENGINE_VERSION, ReadinessVerdict  # noqa: E402
from observation import InputObservation  # noqa: E402
from states import (  # noqa: E402
    BLOCK,
    COMPUTE,
    CONFIGURATION_ERROR,
    DEFER,
    HALT,
    INCOMPLETE_DATA,
    PARTIAL_DAY,
    READY,
    RETRY,
)

PERIOD = "2026-06-14"
CLOSED = "2026-06-15T02:00:00+00:00"
OPEN = "2026-06-14T00:05:00+00:00"


@pytest.fixture
def db(tmp_path) -> Path:
    p = tmp_path / "dq.db"
    readiness.init_db(p)
    return p


def make_contract(min_count: int = 3, contract_id: str = "corpus_daily") -> Contract:
    return parse_contract({
        "contract_id": contract_id,
        "grain": "utc_day",
        "produces": ["funding_bps"],
        "inputs": [{
            "id": "funding",
            "source": {"type": "static"},
            "criticality": "required",
            "coverage": {"min_count": min_count},
            "late_arrival_hours": 12,
            "remediation": "python -m ingest.binance_funding --start 2026-06",
        }],
    })


def activate(db, contract: Contract, actor="rohith", reason="initial activation") -> dict:
    """Activate a contract through the REAL registry (PR-2/3/4), never a file."""
    return cre.activate_contract_with_receipt(
        db, contract.contract_id, contract.canonical_text(), actor, reason
    )


def obs(count: int = 3) -> dict[str, InputObservation]:
    return {"funding": InputObservation(
        "funding", rows=count, coverage_count=count,
        newest_arrival_ts="2026-06-14T16:00:00+00:00",
    )}


# ---------------------------------------------------------------------------
# Emission
# ---------------------------------------------------------------------------

class TestEmission:
    def test_ready_verdict_emits_a_receipt_and_a_projection_row(self, db):
        activate(db, make_contract())
        result = readiness.evaluate_and_record(db, "corpus_daily", PERIOD, CLOSED, obs(3))

        assert result.verdict.state == READY
        assert result.verdict.action == COMPUTE
        assert result.may_derive

        receipt = result.receipt
        assert receipt["decision_type"] == "readiness.verdict"
        assert receipt["decision_class"] == "derived"
        assert receipt["action"] == "evaluate"
        assert receipt["subject_kind"] == "feature"
        assert receipt["subject_id"] == "corpus_daily:2026-06-14"

        row = readiness.get_readiness_by_receipt(db, receipt["receipt_id"])
        assert row["state"] == READY
        assert row["contract_hash"] == result.verdict.contract_hash
        assert row["engine_version"] == ENGINE_VERSION
        assert row["rule_id"] == "dq.completeness_gate"

    def test_a_receipt_is_written_even_on_defer(self, db):
        """§4: 'The receipt is written before the action is taken and REGARDLESS
        of the action.' A deferred day leaves a receipt saying 'deferred,
        PARTIAL_DAY, here is the evidence.' That is how a board distinguishes
        'this day was correctly skipped' from 'nothing ran' — today those two are
        indistinguishable, which is why the H6 outage was silent."""
        activate(db, make_contract())
        result = readiness.evaluate_and_record(db, "corpus_daily", PERIOD, OPEN, obs(1))

        assert result.verdict.state == PARTIAL_DAY
        assert result.verdict.action == DEFER
        assert not result.may_derive
        assert readiness.get_readiness_by_receipt(db, result.receipt["receipt_id"]) is not None

    def test_the_verdict_is_never_null_in_the_receipt(self, db):
        activate(db, make_contract())
        r = readiness.evaluate_and_record(db, "corpus_daily", PERIOD, OPEN, obs(1))
        body = r.receipt["body"]
        assert body["verdict"]["result"] == PARTIAL_DAY
        assert body["verdict"]["reason_code"] == DEFER

    def test_remediation_is_carried_as_a_literal_command(self, db):
        activate(db, make_contract())
        r = readiness.evaluate_and_record(db, "corpus_daily", PERIOD, "2026-06-30T00:00:00Z", obs(1))
        assert r.verdict.state == "BACKFILL_REQUIRED"
        assert r.receipt["body"]["reason_text"] == "python -m ingest.binance_funding --start 2026-06"

    def test_receipt_joins_the_existing_hash_chain(self, db):
        activate(db, make_contract())
        readiness.evaluate_and_record(db, "corpus_daily", PERIOD, CLOSED, obs(3))
        readiness.evaluate_and_record(db, "corpus_daily", "2026-06-15", CLOSED, obs(3))

        chain = rl.verify_chain(db)
        assert chain.ok
        # 1 contract.activate (PR-4) + 2 readiness.verdict, all one chain.
        assert chain.n_receipts == 3

    def test_receipt_records_the_engine_and_check_identity(self, db):
        activate(db, make_contract())
        r = readiness.evaluate_and_record(db, "corpus_daily", PERIOD, CLOSED, obs(3))
        rule_ref = r.receipt["body"]["rule"]
        assert rule_ref["rule_id"] == "dq.completeness_gate"
        assert rule_ref["engine_version"] == ENGINE_VERSION
        assert rule_ref["thresholds_hash"] == r.verdict.contract_hash

    def test_receipt_commits_to_the_observations_hash(self, db):
        activate(db, make_contract())
        r = readiness.evaluate_and_record(db, "corpus_daily", PERIOD, CLOSED, obs(3))
        refs = {i["kind"]: i["hash"] for i in r.receipt["body"]["inputs"]}
        assert refs["contract"] == r.verdict.contract_hash
        assert refs["observations"] == engine.observations_hash(r.observations)

    def test_every_check_appears_in_the_receipt_evaluation(self, db):
        activate(db, make_contract())
        r = readiness.evaluate_and_record(db, "corpus_daily", PERIOD, CLOSED, obs(3))
        check_ids = [e["check_id"] for e in r.receipt["body"]["evaluation"]]
        assert "configuration.contract.v1" in check_ids
        assert "funding/coverage.min_count.v1" in check_ids
        assert "temporal.period_closed.v1" in check_ids

    def test_partial_write_is_impossible(self, db, monkeypatch):
        """§6, 'No partial writes': a crash mid-write leaves NEITHER a receipt nor
        a projection row. The period is simply un-attempted — a state the system
        already handles (it is re-evaluated next cycle). A receipt without its
        substrate would be a verdict nobody could replay."""
        activate(db, make_contract())
        before = len(rl.get_all_receipts(db))

        real_dumps = readiness.json.dumps

        def explode(*a, **kw):
            raise RuntimeError("disk full")

        monkeypatch.setattr(readiness.json, "dumps", explode)
        with pytest.raises(RuntimeError):
            readiness.evaluate_and_record(db, "corpus_daily", PERIOD, CLOSED, obs(3))
        monkeypatch.setattr(readiness.json, "dumps", real_dumps)

        assert len(rl.get_all_receipts(db)) == before
        assert readiness.get_readiness(db, "corpus_daily", PERIOD) is None

    def test_data_readiness_is_append_only(self, db):
        activate(db, make_contract())
        r = readiness.evaluate_and_record(db, "corpus_daily", PERIOD, CLOSED, obs(3))
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                con.execute("UPDATE data_readiness SET state = 'READY'")
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                con.execute("DELETE FROM data_readiness")
        finally:
            con.close()


# ---------------------------------------------------------------------------
# Historical replay — THE point of the whole design
# ---------------------------------------------------------------------------

class TestHistoricalReplay:
    def test_replay_reproduces_the_verdict(self, db):
        activate(db, make_contract())
        r = readiness.evaluate_and_record(db, "corpus_daily", PERIOD, CLOSED, obs(1))
        assert r.verdict.state == INCOMPLETE_DATA

        result = readiness.replay_readiness(db, r.receipt["receipt_id"])
        assert result.ok
        assert result.hash_ok
        assert result.replay.mode == "verified"
        assert result.recomputed.state == INCOMPLETE_DATA
        assert result.recomputed.action == RETRY

    def test_replay_uses_the_historical_contract_not_the_current_one(self, db):
        """
        **The load-bearing test.**

        v1 declares a funding floor of 1. A day with 1 event is READY, and a
        receipt says so.

        v2 tightens the floor to 3 — the H6 correction (the old corpus query's
        `HAVING COUNT(*) >= 2` admitted a day missing an entire funding event;
        the floor is 3). Under v2, that same day is INCOMPLETE_DATA.

        Replaying the OLD receipt must still yield READY. Not because READY is
        right — it is not — but because that is what we BELIEVED on that day,
        under the contract that was in force, and an audit trail that flatters
        history is worthless. Law V1: activating a new contract version NEVER
        retroactively re-judges an existing receipt.

        Re-judging history is a separate, explicit, governed act
        (`resolve_for_reprocessing(..., contract_policy='current')`), and the
        second half of this test is exactly that act, performed deliberately.
        """
        activate(db, make_contract(min_count=1), reason="v1: floor of 1")
        r = readiness.evaluate_and_record(db, "corpus_daily", PERIOD, CLOSED, obs(1))
        assert r.verdict.state == READY
        v1_hash = r.verdict.contract_hash

        # --- the contract is tightened, and superseded ---
        activate(db, make_contract(min_count=3), reason="v2: H6 correction, floor is 3")
        active = cr.get_active(db, "corpus_daily")
        assert active["version"] == 2
        assert cr.get_by_hash(db, v1_hash)["phase"] == "superseded"

        # A FRESH derivation now uses v2 and correctly refuses the day.
        fresh = readiness.evaluate_and_record(db, "corpus_daily", PERIOD, CLOSED, obs(1))
        assert fresh.verdict.state == INCOMPLETE_DATA
        assert fresh.verdict.contract_version == 2

        # The OLD receipt still replays to its ORIGINAL verdict, under v1's bytes.
        replayed = readiness.replay_readiness(db, r.receipt["receipt_id"])
        assert replayed.ok
        assert replayed.recomputed.state == READY, "history must not be re-judged by a new contract"
        assert replayed.recomputed.contract_hash == v1_hash
        assert replayed.recomputed.contract_version == 1

    def test_replay_of_a_superseded_and_retired_contract_still_works(self, db):
        """Retirement removes a contract from FRESH DERIVATION, never from
        REPLAY. A retired contract that could not be dereferenced would render
        its entire historical corpus unreplayable — the precise opposite of the
        goal."""
        activate(db, make_contract(min_count=1))
        r = readiness.evaluate_and_record(db, "corpus_daily", PERIOD, CLOSED, obs(1))
        v1_hash = r.verdict.contract_hash

        activate(db, make_contract(min_count=3), reason="v2")
        cre.retire_contract_with_receipt(db, v1_hash, "rohith", "no longer in use")
        assert cr.get_by_hash(db, v1_hash)["phase"] == "retired"

        replayed = readiness.replay_readiness(db, r.receipt["receipt_id"])
        assert replayed.ok
        assert replayed.recomputed.state == READY

    def test_replay_uses_the_receipts_as_of_not_the_current_clock(self, db):
        """A replay that re-rendered a PARTIAL_DAY with today's clock would call
        it READY and report a successful reproduction. That is look-ahead, and it
        is the failure the whole evidence apparatus exists to prevent."""
        activate(db, make_contract())
        r = readiness.evaluate_and_record(db, "corpus_daily", PERIOD, OPEN, obs(1))
        assert r.verdict.state == PARTIAL_DAY

        replayed = readiness.replay_readiness(db, r.receipt["receipt_id"])
        assert replayed.ok
        assert replayed.recomputed.state == PARTIAL_DAY
        assert replayed.recomputed.as_of == "2026-06-14T00:05:00+00:00"

    def test_replay_needs_no_source_access(self, db):
        """The observations are the replay substrate. The source database may be
        gone, rewritten, or on a machine that no longer exists — an audit in 2031
        must not depend on it."""
        activate(db, make_contract())
        r = readiness.evaluate_and_record(db, "corpus_daily", PERIOD, CLOSED, obs(3))
        # There IS no source: the contract's source type is 'static' and the
        # observations were injected. Replay works anyway.
        assert readiness.replay_readiness(db, r.receipt["receipt_id"]).ok


# ---------------------------------------------------------------------------
# Fail loud, never substitute
# ---------------------------------------------------------------------------

class TestFailLoud:
    def test_unknown_rule_refuses_rather_than_substituting_the_current_one(self, db):
        """
        The receipt names (rule_id, engine_version). If that exact implementation
        is not registered, replay REFUSES.

        Substituting "whatever the current rule is" would replay a historical
        receipt under semantics it was never rendered under — while every hash
        still verified and replay still reported green. That is the one failure
        mode an audit trail must not have.
        """
        class FutureRule:
            rule_id = "dq.completeness_gate"
            engine_version = "dq.v99"

            def evaluate(self, contract, observations, period, as_of, *,
                         contract_version=None, contract_hash=None):
                return engine.evaluate(contract, observations, period, as_of,
                                       contract_version=contract_version,
                                       contract_hash=contract_hash)

        future = FutureRule()
        rules.register_rule(future)
        try:
            activate(db, make_contract())
            r = readiness.evaluate_and_record(
                db, "corpus_daily", PERIOD, CLOSED, obs(3), rule=future
            )
        finally:
            rules.READINESS_RULES._rules.pop(("dq.completeness_gate", "dq.v99"))

        assert r.receipt["body"]["rule"]["engine_version"] == "dq.v99"
        with pytest.raises(rules.UnknownRuleError, match="refuses rather than substituting"):
            readiness.replay_readiness(db, r.receipt["receipt_id"])

    def test_unknown_check_refuses_rather_than_substituting_the_modern_check(self, db, monkeypatch):
        """contract_versioning.md §3: 'If the current engine cannot honour a
        historical check_id, replay fails loudly — it never silently substitutes
        the modern check.'"""
        activate(db, make_contract())
        r = readiness.evaluate_and_record(db, "corpus_daily", PERIOD, CLOSED, obs(3))
        assert readiness.replay_readiness(db, r.receipt["receipt_id"]).ok

        # the engine "forgets" how to do a coverage check
        patched = dict(CHECK_REGISTRY)
        patched.pop("coverage.min_count.v1")
        monkeypatch.setattr(readiness, "CHECK_REGISTRY", patched)

        with pytest.raises(UnknownCheckError, match="no longer implements"):
            readiness.replay_readiness(db, r.receipt["receipt_id"])

    def test_a_rule_may_not_be_redefined_under_an_existing_identity(self):
        class Impostor:
            rule_id = "dq.completeness_gate"
            engine_version = ENGINE_VERSION

            def evaluate(self, *a, **kw):
                raise AssertionError("should never run")

        with pytest.raises(rules.UnknownRuleError, match="may never be redefined"):
            rules.register_rule(Impostor())

    def test_an_unresolvable_contract_hash_fails_replay_loudly(self, db):
        activate(db, make_contract())
        r = readiness.evaluate_and_record(db, "corpus_daily", PERIOD, CLOSED, obs(3))

        # A registry that no longer holds the contract (simulating the one thing
        # the registry promises never to do: prune).
        con = sqlite3.connect(db)
        try:
            con.execute("DROP TRIGGER trg_contracts_no_delete")
            con.execute("DELETE FROM contract_registry")
            con.commit()
        finally:
            con.close()

        with pytest.raises(cr.ContractNotFoundError):
            readiness.replay_readiness(db, r.receipt["receipt_id"])


# ---------------------------------------------------------------------------
# Tamper detection
# ---------------------------------------------------------------------------

class TestTamper:
    def test_mutated_observations_are_detected(self, db):
        """The receipt commits to the observations' hash. Mutating the substrate
        under it would otherwise replay to a verdict the receipt never rendered,
        and report success."""
        activate(db, make_contract())
        r = readiness.evaluate_and_record(db, "corpus_daily", PERIOD, CLOSED, obs(1))
        assert r.verdict.state == INCOMPLETE_DATA

        con = sqlite3.connect(db)
        try:
            con.execute("DROP TRIGGER trg_readiness_no_update")
            con.execute(
                "UPDATE data_readiness SET observations_json = ?",
                ('[{"input_id": "funding", "coverage_count": 3, "rows": 3, "min_ts": null, '
                 '"max_ts": null, "newest_arrival_ts": "2026-06-14T16:00:00+00:00", '
                 '"schema": null, "value_bounds": null, "coverage_denominator": null, '
                 '"history_rows": null, "has_interior_gaps": null, "content_hash": null, '
                 '"probe_error": null}]',),
            )
            con.commit()
        finally:
            con.close()

        with pytest.raises(readiness.ObservationsTamperError, match="mutated"):
            readiness.replay_readiness(db, r.receipt["receipt_id"])

    def test_mutated_receipt_body_fails_the_hash_check(self, db):
        activate(db, make_contract())
        r = readiness.evaluate_and_record(db, "corpus_daily", PERIOD, CLOSED, obs(1))

        con = sqlite3.connect(db)
        try:
            con.execute("DROP TRIGGER trg_receipts_no_update")
            body = r.receipt["body"]
            body["verdict"]["result"] = "READY"
            con.execute(
                "UPDATE governance_receipts SET body_json = ? WHERE receipt_id = ?",
                (readiness.json.dumps(body, sort_keys=True), r.receipt["receipt_id"]),
            )
            con.commit()
        finally:
            con.close()

        result = readiness.replay_readiness(db, r.receipt["receipt_id"])
        assert not result.hash_ok
        assert not result.ok
        assert validation.validate_ledger(db)  # and --check catches it


# ---------------------------------------------------------------------------
# Point-in-time
# ---------------------------------------------------------------------------

class TestPointInTime:
    def test_as_of_returns_what_we_knew_then_not_what_we_know_now(self, db):
        """A period evaluated PARTIAL_DAY at 00:05, then READY the next day, must
        still answer PARTIAL_DAY to 'what did we know at 00:06?'. A point-in-time
        read that quietly returns the later verdict has look-ahead baked into it,
        and every downstream training set inherits it."""
        activate(db, make_contract())
        readiness.evaluate_and_record(db, "corpus_daily", PERIOD, OPEN, obs(1))
        readiness.evaluate_and_record(db, "corpus_daily", PERIOD, CLOSED, obs(3))

        early = readiness.get_readiness(db, "corpus_daily", PERIOD, as_of="2026-06-14T00:06:00Z")
        assert early["state"] == PARTIAL_DAY

        now = readiness.get_readiness(db, "corpus_daily", PERIOD)
        assert now["state"] == READY

    def test_before_any_evaluation_there_is_no_receipt_which_is_not_ready(self, db):
        """NO_RECEIPT is distinct from every verdict. 'A day nobody looked at
        must not look like a day that passed.'"""
        activate(db, make_contract())
        readiness.evaluate_and_record(db, "corpus_daily", PERIOD, CLOSED, obs(3))
        assert readiness.get_readiness(db, "corpus_daily", PERIOD, as_of=OPEN) is None
        assert readiness.get_readiness(db, "corpus_daily", "2026-06-13") is None

    def test_history_retains_every_verdict(self, db):
        activate(db, make_contract())
        readiness.evaluate_and_record(db, "corpus_daily", PERIOD, OPEN, obs(1))
        readiness.evaluate_and_record(db, "corpus_daily", PERIOD, CLOSED, obs(3))

        history = readiness.get_readiness_history(db, "corpus_daily", PERIOD)
        assert [h["state"] for h in history] == [PARTIAL_DAY, READY]

    def test_the_partial_day_is_revisited_the_f3_latch_is_dead(self, db):
        """
        H6's F3: `INSERT OR IGNORE` + an `_already_processed()` keyed on *day
        exists* meant the partial day was written once and then NEVER revisited,
        forever.

        Here, a non-READY verdict produces NO durable derived row at all — the
        absence of the row IS the retry mechanism (L4). The next cycle re-gates
        the same period and, once it is closed and complete, permits the
        derivation. Nothing had to remember that the day was broken.
        """
        activate(db, make_contract())
        first = readiness.evaluate_and_record(db, "corpus_daily", PERIOD, OPEN, obs(1))
        assert not first.may_derive          # nothing may be written

        second = readiness.evaluate_and_record(db, "corpus_daily", PERIOD, CLOSED, obs(3))
        assert second.may_derive             # and the very same day is revisited, and passes

    def test_board_shows_the_latest_verdict_per_feature_period(self, db):
        activate(db, make_contract())
        readiness.evaluate_and_record(db, "corpus_daily", PERIOD, OPEN, obs(1))
        readiness.evaluate_and_record(db, "corpus_daily", PERIOD, CLOSED, obs(3))
        # NB: CLOSED (2026-06-15T02:00Z) is DURING 2026-06-15 — that period is
        # still open at that instant, so it must be evaluated from the day after.
        readiness.evaluate_and_record(db, "corpus_daily", "2026-06-15", "2026-06-16T02:00:00Z", obs(1))

        board = {r["period"]: r["state"] for r in readiness.get_readiness_board(db)}
        assert board == {"2026-06-14": READY, "2026-06-15": INCOMPLETE_DATA}


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

class TestBootstrap:
    def make_verdict(self) -> ReadinessVerdict:
        return ReadinessVerdict(
            contract_id="legacy", contract_hash="", contract_version=None,
            engine_version=ENGINE_VERSION, feature_id="legacy", period=PERIOD,
            as_of=CLOSED, state=READY, action=COMPUTE, checks=(), input_states={},
        )

    def test_bootstrap_receipt_refuses_replay_rather_than_approximating(self, db):
        """governance_receipts.md §6, 'bootstrap honesty' — the precedent PR-1's
        migrate_bootstrap_transitions.py set and PR-6's evidence-admission
        migration followed. Inventing the inputs would be a lie with a valid
        hash. verify() still holds forever."""
        receipt = readiness.bootstrap_readiness_receipt(
            db, self.make_verdict(),
            attestation_note="derived before the framework existed; inputs not captured",
        )
        result = readiness.replay_readiness(db, receipt["receipt_id"])
        assert result.hash_ok                       # verify() works
        assert result.replay.mode == "refused"      # replay() does not
        assert not result.ok

    def test_bootstrap_receipt_still_verifies_and_chains(self, db):
        readiness.bootstrap_readiness_receipt(
            db, self.make_verdict(), attestation_note="legacy"
        )
        assert rl.verify_chain(db).ok

    def test_bootstrap_requires_a_note(self, db):
        with pytest.raises(rcp.BodyValidationError, match="attestation_note"):
            readiness.bootstrap_readiness_receipt(db, self.make_verdict(), attestation_note="")

    def test_validate_ledger_does_not_demand_a_projection_for_a_bootstrap_receipt(self, db):
        readiness.bootstrap_readiness_receipt(
            db, self.make_verdict(), attestation_note="legacy"
        )
        assert validation.validate_ledger(db) == []

    def test_validate_replay_treats_a_refusal_as_not_a_failure(self, db):
        receipt = readiness.bootstrap_readiness_receipt(
            db, self.make_verdict(), attestation_note="legacy"
        )
        assert validation.validate_replay(db, receipt["receipt_id"]) == []


# ---------------------------------------------------------------------------
# Unknown contracts
# ---------------------------------------------------------------------------

class TestUnknownContract:
    def test_no_active_contract_is_a_configuration_error_with_a_receipt(self, db):
        """A gate that dies with a traceback writes no receipt, and a period with
        no receipt is a period nobody looked at — which must never be
        indistinguishable from a period that passed."""
        result = readiness.evaluate_and_record(db, "never_activated", PERIOD, CLOSED, obs(3))

        assert result.verdict.state == CONFIGURATION_ERROR
        assert result.verdict.action == HALT
        assert not result.may_derive
        assert result.receipt["body"]["attestation"] == "bootstrap"
        assert rl.verify_chain(db).ok

    def test_a_draft_contract_may_not_gate_a_derivation(self, db):
        """§2.1: 'a draft contract in a live pipeline is a CONFIGURATION_ERROR'.
        A draft binds nothing."""
        contract = make_contract()
        cr.append_contract(db, cr.ContractRecord(
            contract_id="corpus_daily", version=1, contract_schema_version=1,
            body=contract.canonical_text(), phase="draft",
            actor="rohith", reason="drafting",
        ))
        result = readiness.evaluate_and_record(db, "corpus_daily", PERIOD, CLOSED, obs(3))
        assert result.verdict.state == CONFIGURATION_ERROR


# ---------------------------------------------------------------------------
# Ledger validation (the --check surface)
# ---------------------------------------------------------------------------

class TestLedgerValidation:
    def test_clean_ledger_passes(self, db):
        activate(db, make_contract())
        readiness.evaluate_and_record(db, "corpus_daily", PERIOD, CLOSED, obs(3))
        readiness.evaluate_and_record(db, "corpus_daily", PERIOD, OPEN, obs(1))
        assert validation.validate_ledger(db) == []

    def test_every_emitted_receipt_replays(self, db):
        activate(db, make_contract())
        for period, as_of, o in (
            (PERIOD, CLOSED, obs(3)), (PERIOD, OPEN, obs(1)),
            ("2026-06-15", "2026-06-30T00:00:00Z", obs(1)),
        ):
            r = readiness.evaluate_and_record(db, "corpus_daily", period, as_of, o)
            assert validation.validate_replay(db, r.receipt["receipt_id"]) == []

    def test_unresolvable_contract_hash_is_reported(self, db):
        activate(db, make_contract())
        readiness.evaluate_and_record(db, "corpus_daily", PERIOD, CLOSED, obs(3))
        con = sqlite3.connect(db)
        try:
            con.execute("DROP TRIGGER trg_contracts_no_delete")
            con.execute("DELETE FROM contract_registry")
            con.commit()
        finally:
            con.close()
        assert any(e.startswith("DQ-L5") for e in validation.validate_ledger(db))

    def test_missing_projection_row_is_reported(self, db):
        activate(db, make_contract())
        readiness.evaluate_and_record(db, "corpus_daily", PERIOD, CLOSED, obs(3))
        con = sqlite3.connect(db)
        try:
            con.execute("DROP TRIGGER trg_readiness_no_delete")
            con.execute("DELETE FROM data_readiness")
            con.commit()
        finally:
            con.close()
        assert any(e.startswith("DQ-L2") for e in validation.validate_ledger(db))

    def test_receipt_id_not_found(self, db):
        with pytest.raises(readiness.ReceiptNotFoundError):
            readiness.replay_readiness(db, "deadbeef")
