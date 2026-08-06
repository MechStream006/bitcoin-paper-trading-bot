"""
Required/optional database classification — Option A (ruled 2026-08-06).

Resolves two production-critical defects found by the Phase 4 D0 deployment
audit, both introduced by WBS 3.10 and both empirically reproduced against a
simulated EC2 tree before any fix was written:

  PROD-1  `backup.py --all` exited 1 every day on EC2 because
          research/label_store/label_store.db does not exist there -- while
          all four real backups succeeded. `btcbot-backup.service` is a
          Type=oneshot unit, so it entered `failed` daily, destroying the
          trustworthiness of the backup signal itself.

  PROD-2  The same absence made `check_backup_freshness` return WARNING.
          `btcbot-healthcheck.service` runs every 10 minutes with
          --alert-on-warning and has NO rate limiting, so that was 144 false
          Telegram alerts AND 144 failed units per day, permanently, for a
          condition that is correct and expected on EC2.

Classification:
    REQUIRED  btc_trading.db          -- absence is a real failure
    OPTIONAL  forward_evidence.db     (--evidence)
              evidence_forward.db     (--fwd-hg)
              ledger.db               (--ledger)
              label_store.db          (--label-store)

The eight scenarios below are the deployment shapes this platform actually
has: a fresh EC2 host, a mature host, and the mixed states in between.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO_ROOT / "scripts"
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _load(name: str, path: Path):
    """scripts/ is not a package, so load by file. The module MUST be
    registered in sys.modules before exec_module(): @dataclass resolves
    sys.modules[cls.__module__] during class creation, so CheckResult fails
    to build otherwise -- the same pitfall tests/test_healthcheck.py
    documents at its own loader."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


backup = _load("backup_classification_mod", _SCRIPTS / "backup.py")
hc = _load("healthcheck_classification_mod", _SCRIPTS / "healthcheck.py")

# (flag-less label, path under ROOT) for every database `--all` touches.
ALL_DBS = {
    "btc_trading": Path("data") / "btc_trading.db",
    "evidence": Path("research") / "data" / "forward_evidence.db",
    "fwd_hg": Path("research") / "data" / "evidence_forward.db",
    "ledger": Path("research") / "evidence_library" / "ledger.db",
    "label_store": Path("research") / "label_store" / "label_store.db",
}
OPTIONAL = ("evidence", "fwd_hg", "ledger", "label_store")
BACKUP_DIRS = {
    "btc_trading": "backups",
    "evidence": "evidence-backups",
    "fwd_hg": "evidence-fwd-backups",
    "ledger": "ledger-backups",
    "label_store": "label-store-backups",
}


def _make_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    con.commit()
    con.close()


def _build_root(tmp: Path, present: tuple[str, ...]) -> Path:
    """A ROOT containing exactly the named databases."""
    root = tmp / "opt_btcbot"
    for label in present:
        _make_db(root / ALL_DBS[label])
    return root


def _run_all(root: Path, monkeypatch, dry_run: bool = True) -> int:
    monkeypatch.setattr(backup, "ROOT", root)
    argv = ["backup.py", "--all",
            "--db-path", str(root / ALL_DBS["btc_trading"]),
            "--backup-dir", str(root / "data" / "backups")]
    if dry_run:
        argv.append("--dry-run")
    monkeypatch.setattr(sys, "argv", argv)
    return backup.main()


# ---------------------------------------------------------------------------
# Classification itself
# ---------------------------------------------------------------------------

class TestClassification:
    def test_only_btc_trading_is_required(self):
        assert backup.REQUIRED_DB_LABELS == frozenset({"btc_trading"})

    def test_is_required_predicate(self):
        assert backup.is_required("btc_trading") is True
        for label in OPTIONAL:
            assert backup.is_required(label) is False, label

    def test_an_unknown_label_is_treated_as_optional(self):
        """Fail-soft: a future database defaults to optional, so adding one
        can never reintroduce PROD-1 by omission."""
        assert backup.is_required("some_future_db") is False


# ---------------------------------------------------------------------------
# backup.py -- the eight deployment scenarios
# ---------------------------------------------------------------------------

class TestBackupScenarios:
    def test_all_databases_present(self, tmp_path, monkeypatch):
        """Mature deployment: everything exists, everything backs up."""
        root = _build_root(tmp_path, tuple(ALL_DBS))
        assert _run_all(root, monkeypatch) == 0

    def test_required_missing_fails(self, tmp_path, monkeypatch):
        """The case that must still fail loudly."""
        root = _build_root(tmp_path, OPTIONAL)          # every optional, no trading
        assert _run_all(root, monkeypatch) == 1

    @pytest.mark.parametrize("absent", OPTIONAL)
    def test_each_optional_missing_individually(self, tmp_path, monkeypatch, absent):
        present = tuple(l for l in ALL_DBS if l != absent)
        root = _build_root(tmp_path, present)
        assert _run_all(root, monkeypatch) == 0, f"{absent} absent should not fail --all"

    def test_multiple_optional_missing(self, tmp_path, monkeypatch):
        root = _build_root(tmp_path, ("btc_trading", "evidence"))
        assert _run_all(root, monkeypatch) == 0

    def test_only_required_present(self, tmp_path, monkeypatch):
        """Fresh EC2 host, first boot: only the trading DB exists."""
        root = _build_root(tmp_path, ("btc_trading",))
        assert _run_all(root, monkeypatch) == 0

    def test_fresh_ec2_deployment(self, tmp_path, monkeypatch):
        """The exact shape that caused PROD-1: trading + the three research
        DBs the deployed collectors create, but NO label_store.db."""
        root = _build_root(tmp_path, ("btc_trading", "evidence", "fwd_hg", "ledger"))
        assert _run_all(root, monkeypatch) == 0

    def test_mixed_deployment(self, tmp_path, monkeypatch):
        """Partway through research rollout: label_store exists, fwd_hg does not."""
        root = _build_root(tmp_path, ("btc_trading", "evidence", "ledger", "label_store"))
        assert _run_all(root, monkeypatch) == 0

    def test_nothing_present_still_fails_on_the_required_db(self, tmp_path, monkeypatch):
        root = _build_root(tmp_path, ())
        assert _run_all(root, monkeypatch) == 1


class TestBackupPreservesExistingBehaviour:
    """Requirement 4: databases that EXIST must behave exactly as before."""

    def test_present_optional_db_is_actually_backed_up(self, tmp_path, monkeypatch):
        root = _build_root(tmp_path, ("btc_trading", "label_store"))
        assert _run_all(root, monkeypatch, dry_run=False) == 0
        made = list((root / "data" / "label-store-backups").glob("label_store_*.db"))
        assert len(made) == 1

    def test_all_present_dbs_produce_backups_and_manifests(self, tmp_path, monkeypatch):
        root = _build_root(tmp_path, tuple(ALL_DBS))
        assert _run_all(root, monkeypatch, dry_run=False) == 0
        for label, subdir in BACKUP_DIRS.items():
            bdir = root / "data" / subdir
            assert list(bdir.glob(f"{label}_*.db")), f"{label} backup missing"
            assert list(bdir.glob(f"{label}_*.manifest.json")), f"{label} manifest missing"

    def test_absent_optional_creates_no_backup_directory(self, tmp_path, monkeypatch):
        root = _build_root(tmp_path, ("btc_trading",))
        assert _run_all(root, monkeypatch, dry_run=False) == 0
        assert not (root / "data" / "label-store-backups").exists()

    def test_a_real_backup_failure_still_propagates(self, tmp_path, monkeypatch):
        """Skipping must not mask a genuine failure of a PRESENT database."""
        root = _build_root(tmp_path, ("btc_trading",))
        (root / ALL_DBS["btc_trading"]).write_bytes(b"not a sqlite database at all")
        assert _run_all(root, monkeypatch, dry_run=False) != 0


# ---------------------------------------------------------------------------
# healthcheck.py -- PROD-2
# ---------------------------------------------------------------------------

class TestHealthcheckClassification:
    def test_required_missing_backup_dir_is_warning(self, tmp_path):
        r = hc.check_backup_freshness(tmp_path / "absent", label="btc_trading",
                                      check_name="backup", required=True)
        assert r.severity == hc.WARNING

    @pytest.mark.parametrize("label", ["ledger", "label_store"])
    def test_optional_missing_backup_dir_is_info(self, tmp_path, label):
        r = hc.check_backup_freshness(tmp_path / "absent", label=label,
                                      check_name=f"{label}_backup", required=False)
        assert r.severity == hc.INFO
        assert "optional database" in r.message

    def test_optional_empty_backup_dir_is_info(self, tmp_path):
        d = tmp_path / "label-store-backups"
        d.mkdir()
        r = hc.check_backup_freshness(d, label="label_store",
                                      check_name="label_store_backup", required=False)
        assert r.severity == hc.INFO

    def test_info_cannot_escalate_overall_or_alert(self):
        """The structural reason PROD-2 is fixed: INFO ranks below WARNING,
        and should_alert fires only on CRITICAL or WARNING."""
        assert hc._RANK[hc.INFO] < hc._RANK[hc.WARNING]

    def test_optional_freshness_is_still_enforced_once_backups_exist(self, tmp_path):
        """Only the never-configured case is downgraded. A STALE optional
        backup is a real signal and must stay a WARNING."""
        import os, time
        d = tmp_path / "label-store-backups"
        d.mkdir()
        old = d / "label_store_20200101_000000.db"
        old.write_bytes(b"x")
        stale = time.time() - (hc.BACKUP_WARN_HOURS + 24) * 3600
        os.utime(old, (stale, stale))
        r = hc.check_backup_freshness(d, label="label_store",
                                      check_name="label_store_backup", required=False)
        assert r.severity == hc.WARNING

    def test_optional_fresh_backup_is_ok(self, tmp_path):
        d = tmp_path / "ledger-backups"
        d.mkdir()
        (d / "ledger_20260806_000000.db").write_bytes(b"x")
        r = hc.check_backup_freshness(d, label="ledger", check_name="ledger_backup",
                                      required=False)
        assert r.severity == hc.OK


class TestHealthcheckWiring:
    """Structural guards, parsed with `ast` rather than text-matched -- a
    naive substring count picks up comments and a naive slice truncates at
    the `]` inside `List[CheckResult]`. Mirrors the AST-guard precedent set
    at WBS 3.4 and 3.11."""

    @staticmethod
    def _freshness_calls(path: Path) -> list[dict]:
        """Every check_backup_freshness(...) call, as {label, required}."""
        tree = ast.parse(path.read_text(encoding="utf-8"))
        out = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = getattr(fn, "attr", None) or getattr(fn, "id", None)
            if name != "check_backup_freshness":
                continue
            kw = {k.arg: k.value for k in node.keywords}
            label = kw.get("label")
            required = kw.get("required")
            out.append({
                "label": label.value if isinstance(label, ast.Constant) else "btc_trading",
                "required": required.value if isinstance(required, ast.Constant) else True,
            })
        return out

    def test_cli_marks_exactly_the_optional_databases(self):
        calls = self._freshness_calls(_SCRIPTS / "healthcheck.py")
        by_label = {c["label"]: c["required"] for c in calls}
        assert by_label.get("ledger") is False
        assert by_label.get("label_store") is False

    def test_trading_backup_check_stays_required(self):
        """The one whose absence genuinely matters must NOT be downgraded."""
        calls = self._freshness_calls(_SCRIPTS / "healthcheck.py")
        by_label = {c["label"]: c["required"] for c in calls}
        assert by_label.get("btc_trading") is True

    def test_api_services_mirrors_the_cli(self):
        """CLI and read-only API must never disagree about health."""
        cli = {c["label"]: c["required"]
               for c in self._freshness_calls(_SCRIPTS / "healthcheck.py")}
        api = {c["label"]: c["required"]
               for c in self._freshness_calls(_REPO_ROOT / "src" / "api" / "services.py")}
        assert api == cli, f"API/CLI divergence: api={api} cli={cli}"


class TestProd2EndToEnd:
    def test_fresh_ec2_healthcheck_sends_no_backup_alert(self, tmp_path, monkeypatch):
        """The PROD-2 scenario end-to-end: label-store backups absent must
        produce INFO, never a Telegram-triggering severity."""
        d = tmp_path / "label-store-backups"      # deliberately not created
        r = hc.check_backup_freshness(d, label="label_store",
                                      check_name="label_store_backup", required=False)
        would_alert = hc._RANK[r.severity] >= hc._RANK[hc.WARNING]
        assert r.severity == hc.INFO
        assert would_alert is False
