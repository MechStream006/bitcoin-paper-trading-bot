"""Local Pre-Validation Harness — Program P1, Increment 4.

Reproduces the historically-documented production ledger drift (missing
`runs.run_kind` column, missing `governance_receipts`, missing `pairwise_r`,
and stale/absent `current_run_per_lane` + `candidate_gate_status` views) on a
disposable COPY of the real local ledger, then proves the real migration
sequence — `migrate_run_kind.py` -> `migrate_bootstrap_evidence_admission.py`
-> `migrate_bootstrap_transition_receipts.py`, invoked via their actual CLI,
unmodified — closes the gap cleanly and idempotently.

Local-development only:
  - Skips entirely if the real local ledger.db is absent (it is gitignored;
    CI never has it) -- matches the existing `REAL_LEDGER.exists()` convention
    used elsewhere in this suite.
  - Every migration-script invocation passes an explicit `--db`/`--registry`
    pointing at a tmp_path copy -- never the scripts' own module-level
    defaults, which resolve to the real ledger.
  - `_assert_not_real_ledger()` is called before every subprocess invocation:
    a belt-and-suspenders guard, not reliance on "we remembered the flag".
  - `_guard_real_ledger_untouched` (autouse, module-scoped) hashes the real
    ledger before any test in this module runs and asserts it is
    byte-for-byte unchanged after all of them finish.

Does NOT fabricate the historically-documented extra `efbe6acb` ivrv run
(explicit scope decision, approved) -- a data-content nuance already
characterized by the original D-0 investigation, orthogonal to migration-
script and GV-SCHEMA behavior.

Run from repo root:
    conda run -n btc_bot pytest tests/test_migration_harness.py -v -s
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EVLIB = ROOT / "research" / "evidence_library"
REAL_LEDGER = EVLIB / "ledger.db"
REAL_REGISTRY = EVLIB / "registry.yaml"
REAL_THRESHOLDS = EVLIB / "thresholds.yaml"

sys.path.insert(0, str(EVLIB))
import governance_verification as gv  # noqa: E402
import receipt_ledger as rl  # noqa: E402
import report  # noqa: E402

pytestmark = pytest.mark.skipif(
    not REAL_LEDGER.exists(),
    reason="real ledger.db not present (gitignored; absent in CI) -- "
           "this harness is local-development only",
)

# Original `runs` column list minus `run_kind` (record.py _SCHEMA_SQL, verbatim
# except for the one dropped column).
_RUNS_COLUMNS_WITHOUT_RUN_KIND = (
    "run_id", "family_id", "run_ts", "git_sha", "fn_hash", "provenance",
    "window_start", "window_end", "engine_version", "n_trials", "sharpe",
    "oos_sharpe", "evidence_score", "verdict", "n_trades", "dsr_prob",
    "bh_p", "metrics_json",
)

_MIGRATION_SCRIPTS = (
    "migrate_run_kind.py",
    "migrate_bootstrap_evidence_admission.py",
    "migrate_bootstrap_transition_receipts.py",
)


# ---------------------------------------------------------------------------
# Real-ledger protection (applies to the whole module)
# ---------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _assert_not_real_ledger(path: Path) -> None:
    assert Path(path).resolve() != REAL_LEDGER.resolve(), (
        f"REFUSING: {path} resolves to the real ledger -- this harness must "
        f"never operate on it directly."
    )


@pytest.fixture(scope="module", autouse=True)
def _guard_real_ledger_untouched():
    before = _sha256(REAL_LEDGER)
    yield
    after = _sha256(REAL_LEDGER)
    assert before == after, (
        "the real local ledger.db changed during this test module's run -- "
        "the harness must operate ONLY on tmp_path copies"
    )


# ---------------------------------------------------------------------------
# Synthetic pre-migration ledger construction
# ---------------------------------------------------------------------------

def _schema_snapshot(db_path: Path) -> tuple:
    con = sqlite3.connect(db_path)
    try:
        return tuple(con.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall())
    finally:
        con.close()


def _build_synthetic_pre_migration_ledger(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Copy the real ledger/registry/thresholds into tmp_path, then strip the
    copy down to the historically-documented pre-migration shape. Returns
    (ledger_copy, registry_copy, thresholds_copy)."""
    ledger_copy = tmp_path / "ledger.db"
    registry_copy = tmp_path / "registry.yaml"
    thresholds_copy = tmp_path / "thresholds.yaml"
    shutil.copy2(REAL_LEDGER, ledger_copy)
    shutil.copy2(REAL_REGISTRY, registry_copy)
    shutil.copy2(REAL_THRESHOLDS, thresholds_copy)
    _assert_not_real_ledger(ledger_copy)

    con = sqlite3.connect(ledger_copy)
    try:
        con.execute("DROP TABLE IF EXISTS governance_receipts")
        con.execute("DROP TABLE IF EXISTS pairwise_r")
        # Approved decision: drop the currently-defined views here; the
        # migration sequence's own idempotent init_db() calls must recreate
        # them (CREATE VIEW IF NOT EXISTS only fires on genuine absence) --
        # verified post-migration, not assumed. `has_live_evidence` was
        # discovered during implementation (record.py:148) alongside the two
        # originally planned; all three are still actively defined by
        # _SCHEMA_SQL today.
        con.execute("DROP VIEW IF EXISTS current_run_per_lane")
        con.execute("DROP VIEW IF EXISTS candidate_gate_status")
        con.execute("DROP VIEW IF EXISTS has_live_evidence")
        # `latest_run_per_family` was ALSO discovered during implementation:
        # a stale artifact left on the real ledger from before its deletion
        # at PR-5 (record.py:17 -- "DELETED, not deprecated"). No current
        # code creates or queries it; dropping it is required only because
        # it references `runs` and would otherwise block the table rebuild
        # below. Deliberately NOT recreated -- it is dead schema, not part of
        # the documented pre-migration drift, and out of scope to resurrect.
        con.execute("DROP VIEW IF EXISTS latest_run_per_family")

        cols = ", ".join(_RUNS_COLUMNS_WITHOUT_RUN_KIND)
        con.executescript(f"""
            CREATE TABLE runs_pre_migration_shape (
                run_id          TEXT PRIMARY KEY,
                family_id       TEXT NOT NULL,
                run_ts          TEXT NOT NULL,
                git_sha         TEXT,
                fn_hash         TEXT,
                provenance      TEXT NOT NULL,
                window_start    TEXT,
                window_end      TEXT,
                engine_version  TEXT,
                n_trials        INTEGER,
                sharpe          REAL,
                oos_sharpe      REAL,
                evidence_score  REAL,
                verdict         TEXT,
                n_trades        INTEGER,
                dsr_prob        REAL,
                bh_p            REAL,
                metrics_json    TEXT NOT NULL
            );
            INSERT INTO runs_pre_migration_shape ({cols})
                SELECT {cols} FROM runs;
            DROP TABLE runs;
            ALTER TABLE runs_pre_migration_shape RENAME TO runs;
        """)
        con.commit()
    finally:
        con.close()

    return ledger_copy, registry_copy, thresholds_copy


# ---------------------------------------------------------------------------
# Migration script invocation (real CLI, subprocess -- never internal calls)
# ---------------------------------------------------------------------------

def _run_migration_script(script: str, *args: str) -> subprocess.CompletedProcess:
    for a in args:
        if a.endswith(".db"):
            _assert_not_real_ledger(Path(a))
    proc = subprocess.run(
        [sys.executable, str(EVLIB / script), *args],
        cwd=EVLIB, capture_output=True, text=True, timeout=60,
    )
    return proc


def _run_sequence(ledger_copy: Path, registry_copy: Path) -> list[subprocess.CompletedProcess]:
    results = []
    results.append(_run_migration_script(
        "migrate_run_kind.py", "--commit", "--db", str(ledger_copy)))
    results.append(_run_migration_script(
        "migrate_bootstrap_evidence_admission.py", "--commit",
        "--db", str(ledger_copy), "--registry", str(registry_copy)))
    results.append(_run_migration_script(
        "migrate_bootstrap_transition_receipts.py", "--commit", "--db", str(ledger_copy)))
    return results


# ---------------------------------------------------------------------------
# The harness run -- built once per module, all tests assert against it
# ---------------------------------------------------------------------------

@dataclass
class HarnessRun:
    ledger_copy: Path
    registry_copy: Path
    thresholds_copy: Path
    pre_tables: frozenset
    pre_run_columns: frozenset
    pre_views: frozenset
    pre_schema_findings: list
    pre_check_rc: int
    pre_check_stdout: str
    pre_check_schema_unchanged: bool
    first_run_results: list = field(default_factory=list)
    post_schema_findings: list = None
    post_check_rc: int = None
    post_check_stdout: str = None
    chain_ok: bool = None
    chain_n_receipts: int = None
    backups_before_second_run: int = None
    backups_after_second_run: int = None
    second_run_results: list = field(default_factory=list)
    post_idempotent_snapshot: tuple = None
    post_migration_snapshot: tuple = None


@pytest.fixture(scope="module")
def harness_run(tmp_path_factory) -> HarnessRun:
    tmp_path = tmp_path_factory.mktemp("migration_harness")
    ledger_copy, registry_copy, thresholds_copy = _build_synthetic_pre_migration_ledger(tmp_path)

    # Capture the pre-migration structural shape as VALUES now -- the fixture
    # mutates this same file in place for the rest of its own execution, so
    # nothing downstream may re-query the live file expecting to see this
    # state again.
    con = sqlite3.connect(ledger_copy)
    try:
        pre_tables = frozenset(r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"))
        pre_run_columns = frozenset(r[1] for r in con.execute('PRAGMA table_info("runs")'))
        pre_views = frozenset(r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='view'"))
    finally:
        con.close()

    import io
    import contextlib

    # --- Pre-migration checks ---
    pre_schema_findings = gv.check_schema_fingerprint(ledger_copy)
    pre_schema_snapshot = _schema_snapshot(ledger_copy)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        pre_check_rc = report.run_check(registry_copy, ledger_copy, thresholds_copy)
    pre_check_stdout = buf.getvalue()
    pre_check_schema_unchanged = _schema_snapshot(ledger_copy) == pre_schema_snapshot

    # --- Migration (first run) ---
    first_run_results = _run_sequence(ledger_copy, registry_copy)
    post_migration_snapshot = _schema_snapshot(ledger_copy)

    # --- Post-migration checks ---
    post_schema_findings = gv.check_schema_fingerprint(ledger_copy)
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        post_check_rc = report.run_check(registry_copy, ledger_copy, thresholds_copy)
    post_check_stdout = buf2.getvalue()
    chain = rl.verify_chain(ledger_copy)

    # --- Idempotency (second run) ---
    backup_glob_before = list(ledger_copy.parent.glob("ledger.pre-*"))
    second_run_results = _run_sequence(ledger_copy, registry_copy)
    backup_glob_after = list(ledger_copy.parent.glob("ledger.pre-*"))
    post_idempotent_snapshot = _schema_snapshot(ledger_copy)

    run = HarnessRun(
        ledger_copy=ledger_copy, registry_copy=registry_copy, thresholds_copy=thresholds_copy,
        pre_tables=pre_tables, pre_run_columns=pre_run_columns, pre_views=pre_views,
        pre_schema_findings=pre_schema_findings, pre_check_rc=pre_check_rc,
        pre_check_stdout=pre_check_stdout, pre_check_schema_unchanged=pre_check_schema_unchanged,
        first_run_results=first_run_results,
        post_schema_findings=post_schema_findings, post_check_rc=post_check_rc,
        post_check_stdout=post_check_stdout, chain_ok=chain.ok, chain_n_receipts=chain.n_receipts,
        backups_before_second_run=len(backup_glob_before),
        backups_after_second_run=len(backup_glob_after),
        second_run_results=second_run_results,
        post_idempotent_snapshot=post_idempotent_snapshot,
        post_migration_snapshot=post_migration_snapshot,
    )

    print("\n" + _transcript(run))
    return run


def _transcript(run: HarnessRun) -> str:
    lines = ["=" * 78, "MIGRATION HARNESS TRANSCRIPT (local dev only -- real ledger untouched)",
             "=" * 78]
    lines.append("\n--- 1. PRE-MIGRATION SCHEMA CHECK (GV-SCHEMA) ---")
    for f in run.pre_schema_findings:
        lines.append(f"  [{f.severity.upper()}] {f.code}: {f.message}")
    lines.append(f"\n--- 2. PRE-MIGRATION report.py --check (rc={run.pre_check_rc}) ---")
    lines.append(run.pre_check_stdout.strip())
    lines.append(f"  schema unchanged by this check: {run.pre_check_schema_unchanged}")
    lines.append("\n--- 3. MIGRATION SEQUENCE (first run) ---")
    for name, proc in zip(_MIGRATION_SCRIPTS, run.first_run_results):
        lines.append(f"  $ {name} --commit  (exit {proc.returncode})")
        lines.append("    " + proc.stdout.strip().replace("\n", "\n    "))
    lines.append(f"\n--- 4. POST-MIGRATION SCHEMA CHECK (GV-SCHEMA) ---")
    lines.append(f"  findings: {run.post_schema_findings!r}")
    lines.append(f"\n--- 5. POST-MIGRATION report.py --check (rc={run.post_check_rc}) ---")
    lines.append(run.post_check_stdout.strip())
    lines.append(f"\n--- 6. RECEIPT CHAIN --- ok={run.chain_ok} n_receipts={run.chain_n_receipts}")
    lines.append("\n--- 7. IDEMPOTENCY (second run) ---")
    lines.append(f"  backup files before second run: {run.backups_before_second_run}")
    lines.append(f"  backup files after second run:  {run.backups_after_second_run}")
    for name, proc in zip(_MIGRATION_SCRIPTS, run.second_run_results):
        lines.append(f"  $ {name} --commit  (exit {proc.returncode})")
        lines.append("    " + proc.stdout.strip().replace("\n", "\n    "))
    lines.append(f"\n--- 8. REAL LEDGER PROTECTION ---")
    lines.append(f"  real ledger sha256 unchanged across entire module run: verified by "
                 f"_guard_real_ledger_untouched (autouse module fixture)")
    lines.append("=" * 78)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPreMigrationDrift:

    def test_synthetic_ledger_reproduces_documented_drift(self, harness_run):
        """Asserts against the SNAPSHOT captured at construction time, not a
        live re-query of harness_run.ledger_copy -- by the time any test runs,
        that file already reflects the fixture's fully-migrated end state
        (the fixture mutates one file in place through its own lifecycle)."""
        assert "governance_receipts" not in harness_run.pre_tables
        assert "pairwise_r" not in harness_run.pre_tables
        assert "run_kind" not in harness_run.pre_run_columns
        assert "current_run_per_lane" not in harness_run.pre_views
        assert "candidate_gate_status" not in harness_run.pre_views
        assert "has_live_evidence" not in harness_run.pre_views
        assert "latest_run_per_family" not in harness_run.pre_views
        assert "runs" in harness_run.pre_tables  # the table itself must still exist, minus the column

    def test_gv_schema_reports_named_errors_before_migration(self, harness_run):
        findings = harness_run.pre_schema_findings
        assert any(
            f.code == "GV-SCHEMA" and f.severity == "error" and "runs.run_kind" in f.message
            for f in findings
        )
        assert any(
            f.code == "GV-SCHEMA" and f.severity == "error" and "governance_receipts" in f.message
            for f in findings
        )

    def test_report_check_fails_named_before_migration_with_no_mutation(self, harness_run):
        assert harness_run.pre_check_rc == 1
        assert "SCHEMA CHECK FAILED" in harness_run.pre_check_stdout
        assert "runs.run_kind" in harness_run.pre_check_stdout
        assert harness_run.pre_check_schema_unchanged is True


class TestMigrationSequence:

    def test_migration_sequence_applies_cleanly(self, harness_run):
        for name, proc in zip(_MIGRATION_SCRIPTS, harness_run.first_run_results):
            assert proc.returncode == 0, f"{name} failed:\n{proc.stdout}\n{proc.stderr}"

    def test_gv_schema_clean_after_migration(self, harness_run):
        assert harness_run.post_schema_findings == []

    def test_report_check_clean_after_migration(self, harness_run):
        assert harness_run.post_check_rc == 0
        assert "Registry check: OK" in harness_run.post_check_stdout
        assert "Total families: 35" in harness_run.post_check_stdout
        assert "candidate: 9" in harness_run.post_check_stdout
        assert "discovery: 22" in harness_run.post_check_stdout
        assert "disqualified: 4" in harness_run.post_check_stdout

    def test_verify_chain_valid_after_migration(self, harness_run):
        assert harness_run.chain_ok is True
        assert harness_run.chain_n_receipts == 26

    def test_views_recreated_after_migration(self, harness_run):
        con = sqlite3.connect(harness_run.ledger_copy)
        try:
            views = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='view'")}
            # Must be queryable, not merely present.
            con.execute("SELECT * FROM current_run_per_lane LIMIT 1").fetchall()
            con.execute("SELECT * FROM candidate_gate_status LIMIT 1").fetchall()
            con.execute("SELECT * FROM has_live_evidence LIMIT 1").fetchall()
        finally:
            con.close()
        assert "current_run_per_lane" in views
        assert "candidate_gate_status" in views
        assert "has_live_evidence" in views
        # Deliberately NOT recreated -- dead schema, dropped only because it
        # blocked the runs-table rebuild (see construction step comment).
        assert "latest_run_per_family" not in views


class TestIdempotency:

    def test_migration_sequence_is_idempotent(self, harness_run):
        for name, proc in zip(_MIGRATION_SCRIPTS, harness_run.second_run_results):
            assert proc.returncode == 0, f"{name} (2nd run) failed:\n{proc.stdout}\n{proc.stderr}"
        assert "0 rows to classify" in harness_run.second_run_results[0].stdout
        assert "0 pinned runs need a bootstrap admission receipt" in harness_run.second_run_results[1].stdout
        assert "0 transitions need a bootstrap receipt" in harness_run.second_run_results[2].stdout

    def test_idempotent_rerun_creates_no_new_backup_files(self, harness_run):
        assert harness_run.backups_after_second_run == harness_run.backups_before_second_run

    def test_idempotent_rerun_leaves_ledger_unchanged(self, harness_run):
        assert harness_run.post_migration_snapshot == harness_run.post_idempotent_snapshot


class TestRealLedgerProtection:

    def test_harness_never_touches_real_ledger(self, harness_run):
        # The module-scoped autouse fixture already asserts this for the
        # whole module; this test additionally re-asserts the invariant that
        # every path the harness operated on is NOT the real ledger.
        _assert_not_real_ledger(harness_run.ledger_copy)
        assert harness_run.ledger_copy.resolve() != REAL_LEDGER.resolve()
