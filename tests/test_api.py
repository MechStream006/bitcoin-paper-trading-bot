"""Read-only operational API -- ADR-0001 Phase 4 (§15.3, B1/B2, INV-3).

The most important tests here are the structural guards in
`TestReadOnlyByConstruction`. §15.3 requires that "no write endpoints" be
"enforced by an AST/source-scan test in the style of the existing PR-6/PR-8
structural guards, not by convention". Those tests are that enforcement: they
fail if anyone adds a mutating route or imports a governance-mutation function
into the API package, including in a future refactor nobody remembers this
constraint during.

Run from repo root:
    pytest tests/test_api.py -v
"""
from __future__ import annotations

import ast
import json
import logging
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api import services
from src.api.app import create_app
from src.api.cache import TTLCache
from src.api.config import ApiSettings, ConfigError, load_settings
from src.api.logging_setup import JsonFormatter

ROOT = Path(__file__).resolve().parent.parent
API_DIR = ROOT / "src" / "api"

TOKEN = "t" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def settings(tmp_path: Path) -> ApiSettings:
    """Settings pointed at a temp dir. No test depends on the real ledger --
    `research/evidence_library/ledger.db` is gitignored and absent in CI."""
    return ApiSettings(
        host="127.0.0.1",
        port=9109,
        token=TOKEN,
        trading_db_path=tmp_path / "btc_trading.db",
        forward_db_path=tmp_path / "evidence_forward.db",
        ledger_db_path=tmp_path / "ledger.db",
        registry_path=tmp_path / "registry.yaml",
        thresholds_path=tmp_path / "thresholds.yaml",
        backup_dir=tmp_path / "backups",
        service_name="btcbot.service",
        log_level="INFO",
        cache_ttl_seconds=0.0,  # disable caching so tests see every call
    )


@pytest.fixture
def client(settings) -> TestClient:
    return TestClient(create_app(settings))


_FAKE_RUNTIME = {
    "overall": "WARNING",
    "checks": [
        {"name": "disk", "severity": "OK", "message": "disk OK: 40.0 GB free",
         "detail": {"free_gb": 40.0}},
        {"name": "wal", "severity": "WARNING", "message": "WAL large: 250.0 MB",
         "detail": {"wal_size_mb": 250.0}},
    ],
    "integrity_check_included": False,
}

_FAKE_FORWARD = {
    "state": "ok", "severity": "ok", "latest_run_date": "2026-07-17",
    "expected_run_date": "2026-07-17",
    "signals": {"h5_vol_regime": "ok", "ivrv_vrp_spread": "ok"},
    "n_ok": 5, "n_logged": 5, "detail": "",
    "line": "fwd-evid : 2026-07-17  ok=5/5  fn_hash=ok  gov=ok",
}

_FAKE_GOV = {
    "ok": True, "as_of": None, "counts": {"receipts": 26},
    "subsystems_run": ["receipt_chain", "registry"],
    "findings": [], "summary": "governance OK — 26 receipt(s), 2 subsystem(s) verified",
}

_FAKE_EVIDENCE = {
    "as_of": None, "n_families": 1, "n_runs": 70, "n_transitions": 26,
    "counts_by_state": {"candidate": 1},
    "families": [{
        "family_id": "ivrv_vrp_spread", "state": "candidate", "role": "feature",
        "hypothesis": "VRP spread", "pinned_run_id": "r1", "pinned_run_kind": "discovery",
        "pinned_lane_code": "L0", "state_ts": "2026-06-19T00:00:00", "is_pin_stale": False,
        "flags": [], "pinned_run": {"sharpe": 0.265, "dsr_prob": 0.1646, "bh_p": 0.2893,
                                    "n_trades": 400, "run_ts": "2026-06-19T00:00:00",
                                    "provenance": "offline-corpus"},
        "lanes": {"discovery": {"run_id": "r1", "run_ts": "2026-06-19T00:00:00",
                                "sharpe": 0.265, "provenance": "offline-corpus"}},
    }],
}


@pytest.fixture
def stubbed(monkeypatch):
    """Stub every service adapter. Endpoint tests assert wiring and shape; the
    adapters' fidelity to upstream logic is covered separately."""
    monkeypatch.setattr(services, "runtime_health", lambda s: dict(_FAKE_RUNTIME))
    monkeypatch.setattr(services, "forward_evidence_status", lambda s: dict(_FAKE_FORWARD))
    monkeypatch.setattr(services, "governance_chain", lambda s: dict(_FAKE_GOV))
    monkeypatch.setattr(services, "governance_verification",
                        lambda s, as_of=None: dict(_FAKE_GOV, as_of=as_of))
    monkeypatch.setattr(services, "evidence_summary",
                        lambda s, as_of=None: dict(_FAKE_EVIDENCE, as_of=as_of))
    monkeypatch.setattr(
        services, "evidence_family",
        lambda s, fid, as_of=None: (
            dict(_FAKE_EVIDENCE["families"][0], as_of=as_of)
            if fid == "ivrv_vrp_spread" else None
        ),
    )
    monkeypatch.setattr(services, "readiness",
                        lambda s: {"ready": True,
                                   "checks": {"ledger_present": True, "chain_valid": True,
                                              "n_receipts": 26},
                                   "detail": "ok"})


PROTECTED = [
    "/api/v1/runtime/health",
    "/api/v1/forward/status",
    "/api/v1/governance/chain",
    "/api/v1/governance/verify",
    "/api/v1/evidence/summary",
    "/api/v1/evidence/families/ivrv_vrp_spread",
]


# ---------------------------------------------------------------------------
# Read-only by construction (B1/B2, INV-3) -- the enforcement §15.3 requires
# ---------------------------------------------------------------------------

class TestReadOnlyByConstruction:

    def _api_sources(self) -> list[Path]:
        files = sorted(API_DIR.glob("*.py"))
        assert files, "no API sources found -- guard would vacuously pass"
        return files

    def test_every_route_is_a_get(self):
        """No POST/PUT/PATCH/DELETE route may exist anywhere in the package."""
        forbidden = {"post", "put", "patch", "delete", "api_route", "add_api_route"}
        found: list[str] = []
        for path in self._api_sources():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call):
                    attr = getattr(node.func, "attr", None)
                    if attr in forbidden:
                        found.append(f"{path.name}:{node.lineno} -> .{attr}()")
                if isinstance(node, ast.Attribute) and node.attr in forbidden:
                    found.append(f"{path.name}:{node.lineno} -> .{node.attr}")
        assert not found, (
            "Non-GET route or generic route registration found in the read-only API. "
            "ADR-0001 B2/INV-3 forbid governance mutation over HTTP:\n  "
            + "\n  ".join(found)
        )

    def test_app_registers_only_get_routes(self, settings):
        """Belt-and-braces: inspect the built app, not just the source."""
        app = create_app(settings)
        for route in app.routes:
            methods = getattr(route, "methods", set()) or set()
            offending = methods - {"GET", "HEAD", "OPTIONS"}
            assert not offending, f"{route.path} exposes {offending}"

    def test_no_mutation_function_is_imported(self):
        """The API must not even have a mutation function in scope.

        These are the real mutation entry points across PR-1..PR-9. A test that
        only checked HTTP verbs would still pass if someone called
        `promote_with_receipt()` from inside a GET handler.
        """
        forbidden = {
            "append_transition", "append_transition_on_connection",
            "transition_with_receipt", "promote_with_receipt", "demote_with_receipt",
            "append_receipt", "append_receipt_on_connection",
            "append_contract", "activate_contract", "retire_contract",
            "_activate_on_connection", "_retire_on_connection",
            "admit_evidence", "reject_evidence", "pin_evidence", "repin_evidence",
            "append_pairwise_r", "register_rule",
            "init_db", "migrate", "execute", "executemany", "commit",
        }
        found: list[str] = []
        for path in self._api_sources():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if alias.name in forbidden:
                            found.append(f"{path.name}:{node.lineno} imports {alias.name}")
                if isinstance(node, ast.Call):
                    name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                    if name in forbidden:
                        found.append(f"{path.name}:{node.lineno} calls {name}()")
        assert not found, (
            "Mutation capability reachable from the read-only API:\n  " + "\n  ".join(found)
        )

    def test_no_sql_write_statement_anywhere(self):
        """No DML/DDL string may appear in the package."""
        banned = ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ", "CREATE ", "REPLACE ")
        found: list[str] = []
        for path in self._api_sources():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    upper = node.value.upper()
                    for kw in banned:
                        if kw in upper:
                            found.append(f"{path.name}:{node.lineno} contains {kw.strip()}")
        assert not found, "SQL write statement in read-only API:\n  " + "\n  ".join(found)

    def test_does_not_import_runtime_execution_modules(self):
        """No execution/order path may be importable from the API (B2)."""
        banned_roots = {"src.execution", "src.exchange", "execution", "delta_client"}
        found: list[str] = []
        for path in self._api_sources():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                mod = None
                if isinstance(node, ast.ImportFrom):
                    mod = node.module
                elif isinstance(node, ast.Import):
                    mod = node.names[0].name
                if mod and any(mod == b or mod.startswith(b + ".") for b in banned_roots):
                    found.append(f"{path.name}:{node.lineno} imports {mod}")
        assert not found, "Execution capability imported:\n  " + "\n  ".join(found)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

class TestAuth:

    @pytest.mark.parametrize("path", PROTECTED)
    def test_no_token_is_401(self, client, stubbed, path):
        r = client.get(path)
        assert r.status_code == 401
        assert r.headers["WWW-Authenticate"] == "Bearer"

    @pytest.mark.parametrize("path", PROTECTED)
    def test_wrong_token_is_401(self, client, stubbed, path):
        r = client.get(path, headers={"Authorization": "Bearer wrong-token-value-here"})
        assert r.status_code == 401

    @pytest.mark.parametrize("path", PROTECTED)
    def test_correct_token_is_200(self, client, stubbed, path):
        assert client.get(path, headers=AUTH).status_code == 200

    def test_missing_and_wrong_token_are_indistinguishable(self, client, stubbed):
        """Same status, same body -- no oracle telling an attacker whether the
        header was absent or merely wrong."""
        a = client.get(PROTECTED[0])
        b = client.get(PROTECTED[0], headers={"Authorization": "Bearer nope"})
        assert (a.status_code, a.json()) == (b.status_code, b.json())

    def test_token_prefix_does_not_authenticate(self, client, stubbed):
        r = client.get(PROTECTED[0], headers={"Authorization": f"Bearer {TOKEN[:-1]}"})
        assert r.status_code == 401

    def test_wrong_scheme_is_401(self, client, stubbed):
        r = client.get(PROTECTED[0], headers={"Authorization": f"Basic {TOKEN}"})
        assert r.status_code == 401

    def test_non_ascii_credential_does_not_500(self, client, stubbed):
        """`secrets.compare_digest` raises TypeError on a non-ASCII *str*, so a
        hostile header must not become a 500.

        Sent as raw bytes because httpx refuses to encode a non-ASCII str
        header -- but this is not a synthetic case: Starlette decodes incoming
        header bytes as latin-1, so byte 0xFC really does reach the dependency
        as the str 'ü'. That is exactly the input compare_digest rejects, and
        why auth.py encodes both sides before comparing.
        """
        r = client.get(
            PROTECTED[0],
            headers={"Authorization": "Bearer ünïcodé".encode("latin-1")},
        )
        assert r.status_code == 401

    def test_probes_are_unauthenticated(self, client, stubbed):
        assert client.get("/health").status_code == 200
        assert client.get("/readyz").status_code == 200

    def test_auth_failure_never_logs_the_credential(self, client, stubbed, caplog):
        secret = "super-secret-presented-value-abcdef"
        with caplog.at_level(logging.WARNING):
            client.get(PROTECTED[0], headers={"Authorization": f"Bearer {secret}"})
        blob = json.dumps([r.__dict__ for r in caplog.records], default=str)
        assert secret not in blob
        assert TOKEN not in blob


# ---------------------------------------------------------------------------
# Configuration -- fail closed
# ---------------------------------------------------------------------------

class TestConfig:

    def test_missing_token_refuses_to_start(self):
        with pytest.raises(ConfigError, match="API_TOKEN is not set"):
            load_settings(env={})

    def test_blank_token_refuses_to_start(self):
        with pytest.raises(ConfigError, match="API_TOKEN is not set"):
            load_settings(env={"API_TOKEN": "   "})

    def test_short_token_refuses_to_start(self):
        with pytest.raises(ConfigError, match="too short"):
            load_settings(env={"API_TOKEN": "abc123"})

    def test_valid_token_loads(self):
        s = load_settings(env={"API_TOKEN": TOKEN})
        assert s.token == TOKEN
        assert s.host == "127.0.0.1"
        assert s.port == 9109

    def test_host_is_not_env_overridable(self):
        """A typo in an env var must not be able to create a public exposure."""
        s = load_settings(env={"API_TOKEN": TOKEN, "API_HOST": "0.0.0.0"})
        assert s.host == "127.0.0.1"

    def test_redacted_never_contains_token(self, settings):
        assert TOKEN not in json.dumps(settings.redacted())


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

class TestProbes:

    def test_health_shape(self, client):
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["service"] == "btcbot-api"
        assert body["ts"]

    def test_health_touches_no_subsystem(self, client, monkeypatch):
        """Liveness must stay truthful when every data source is broken."""
        def boom(*a, **k):
            raise AssertionError("/health must not read any subsystem")
        for name in ("runtime_health", "forward_evidence_status", "governance_chain",
                     "governance_verification", "evidence_summary", "readiness"):
            monkeypatch.setattr(services, name, boom)
        assert client.get("/health").status_code == 200

    def test_readyz_reports_not_ready_as_200(self, client, monkeypatch):
        monkeypatch.setattr(services, "readiness",
                            lambda s: {"ready": False, "checks": {"ledger_present": False},
                                       "detail": "ledger not found"})
        r = client.get("/readyz")
        assert r.status_code == 200
        assert r.json()["ready"] is False


class TestRuntimeHealth:

    def test_returns_rollup_and_checks(self, client, stubbed):
        body = client.get("/api/v1/runtime/health", headers=AUTH).json()
        assert body["overall"] == "WARNING"
        assert {c["name"] for c in body["checks"]} == {"disk", "wal"}

    def test_integrity_check_is_excluded(self, client, stubbed):
        assert client.get("/api/v1/runtime/health", headers=AUTH).json()[
            "integrity_check_included"] is False

    def test_service_excludes_expensive_integrity_check(self, settings, monkeypatch):
        """The adapter must never call check_integrity -- it is the one
        long-running scan in healthcheck.py."""
        called: list[str] = []

        class FakeHC:
            OK, INFO, WARNING, CRITICAL = "OK", "INFO", "WARNING", "CRITICAL"
            _RANK = {"OK": 0, "INFO": 1, "WARNING": 2, "CRITICAL": 3}

            class _R:
                def __init__(self, name):
                    self.name, self.severity, self.message, self.detail = name, "OK", "", {}

            def __getattr__(self, name):
                def fn(*a, **k):
                    called.append(name)
                    return FakeHC._R(name.replace("check_", ""))
                return fn

        monkeypatch.setattr(services, "_import_healthcheck", lambda: FakeHC())
        services.runtime_health(settings)
        assert "check_integrity" not in called
        assert "check_db_liveness" in called


class TestForwardStatus:

    def test_shape(self, client, stubbed):
        body = client.get("/api/v1/forward/status", headers=AUTH).json()
        assert body["state"] == "ok"
        assert body["n_ok"] == 5
        assert "fwd-evid" in body["line"]

    def test_uses_summarize_verbatim(self, settings, monkeypatch):
        """The adapter must project summarize(), not re-derive freshness."""
        import src.alerts.forward_status as fs_mod
        seen: list[str] = []

        def fake_summarize(path, now=None):
            seen.append(path)
            return fs_mod.ForwardStatus("stale", "2026-07-01", "2026-07-17", {}, 0, 0, "late")

        monkeypatch.setattr(fs_mod, "summarize", fake_summarize)
        out = services.forward_evidence_status(settings)
        assert seen == [str(settings.forward_db_path)]
        assert out["state"] == "stale"
        assert out["severity"] == "degraded"  # from ForwardStatus.severity(), not recomputed


class TestGovernance:

    def test_chain_shape(self, client, stubbed):
        body = client.get("/api/v1/governance/chain", headers=AUTH).json()
        assert body["ok"] is True
        assert body["counts"]["receipts"] == 26

    def test_verify_threads_as_of(self, client, stubbed):
        body = client.get("/api/v1/governance/verify?as_of=2026-06-14", headers=AUTH).json()
        assert body["as_of"] == "2026-06-14"

    def test_governance_failure_is_200_not_5xx(self, client, monkeypatch):
        """`ok: false` is a finding to report, not an API fault -- a client must
        be able to tell 'governance is broken' from 'the API is broken'."""
        monkeypatch.setattr(
            services, "governance_verification",
            lambda s, as_of=None: {
                "ok": False, "as_of": None, "counts": {"receipts": 0},
                "subsystems_run": ["receipt_chain"],
                "findings": [{"code": "GV-CHAIN", "severity": "error",
                              "message": "hash mismatch at receipt 3",
                              "subsystem": "receipt_chain"}],
                "summary": "governance FAILED — 1 error(s), 0 warning(s)"},
        )
        r = client.get("/api/v1/governance/verify", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["ok"] is False
        assert r.json()["findings"][0]["code"] == "GV-CHAIN"

    @pytest.mark.parametrize("bad", ["not-a-date", "2026-13-45", "yesterday", "2026/06/14"])
    def test_malformed_as_of_is_422(self, client, stubbed, bad):
        assert client.get(f"/api/v1/governance/verify?as_of={bad}",
                          headers=AUTH).status_code == 422


class TestEvidence:

    def test_summary_shape(self, client, stubbed):
        body = client.get("/api/v1/evidence/summary", headers=AUTH).json()
        assert body["n_families"] == 1
        assert body["counts_by_state"] == {"candidate": 1}
        assert body["families"][0]["family_id"] == "ivrv_vrp_spread"

    def test_family_detail(self, client, stubbed):
        body = client.get("/api/v1/evidence/families/ivrv_vrp_spread", headers=AUTH).json()
        assert body["state"] == "candidate"
        assert body["pinned_run"]["sharpe"] == 0.265

    def test_unknown_family_is_404(self, client, stubbed):
        assert client.get("/api/v1/evidence/families/nope", headers=AUTH).status_code == 404

    def test_summary_does_not_write_report_files(self, settings, monkeypatch, tmp_path):
        """'Do not duplicate report generation' cuts both ways: the API must not
        invoke the markdown generators either -- that would mean an HTTP GET
        writes files into the repo."""
        import src.api.services as svc

        class FakeReport:
            LANE_CODE_BY_RUN_KIND: dict = {}

            @staticmethod
            def build_context(**kw):
                class Ctx:
                    as_of, n_runs, n_transitions, rows = None, 0, 0, {}
                return Ctx()

            @staticmethod
            def _state_rank(s):
                return 0

            @staticmethod
            def _role_str(f):
                return "edge"

            def __getattr__(self, name):
                if name.startswith("generate_"):
                    raise AssertionError(f"API must not call {name}")
                raise AttributeError(name)

        monkeypatch.setattr(svc, "_import_evidence_library", lambda: (object(), FakeReport()))
        out = svc.evidence_summary(settings)
        assert out["n_families"] == 0


# ---------------------------------------------------------------------------
# Failure isolation
# ---------------------------------------------------------------------------

class TestFailureIsolation:

    def test_subsystem_unavailable_is_503(self, client, monkeypatch):
        monkeypatch.setattr(
            services, "governance_chain",
            lambda s: (_ for _ in ()).throw(services.SubsystemUnavailable("ledger gone")))
        r = client.get("/api/v1/governance/chain", headers=AUTH)
        assert r.status_code == 503
        assert "ledger gone" in r.json()["detail"]

    def test_one_broken_subsystem_does_not_break_others(self, client, stubbed, monkeypatch):
        monkeypatch.setattr(
            services, "governance_chain",
            lambda s: (_ for _ in ()).throw(services.SubsystemUnavailable("down")))
        assert client.get("/api/v1/governance/chain", headers=AUTH).status_code == 503
        assert client.get("/api/v1/forward/status", headers=AUTH).status_code == 200
        assert client.get("/health").status_code == 200

    def test_missing_databases_do_not_crash_the_app(self, settings):
        """Every path in `settings` points at a nonexistent temp file here."""
        c = TestClient(create_app(settings))
        assert c.get("/health").status_code == 200
        assert c.get("/readyz").json()["ready"] is False


# ---------------------------------------------------------------------------
# Cache -- bounded load on the DBs the daily writer uses
# ---------------------------------------------------------------------------

class TestCache:

    def test_hit_within_ttl(self):
        calls = []
        c = TTLCache(60.0)
        for _ in range(5):
            c.get_or_compute("k", lambda: calls.append(1) or "v")
        assert len(calls) == 1

    def test_expiry_recomputes(self):
        calls = []
        c = TTLCache(0.05)
        c.get_or_compute("k", lambda: calls.append(1) or "v")
        time.sleep(0.1)
        c.get_or_compute("k", lambda: calls.append(1) or "v")
        assert len(calls) == 2

    def test_single_flight_under_concurrency(self):
        """N concurrent cold requests must produce ONE ledger read, not N."""
        calls = []
        c = TTLCache(60.0)

        def compute():
            time.sleep(0.05)
            calls.append(1)
            return "v"

        threads = [threading.Thread(target=lambda: c.get_or_compute("k", compute))
                   for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(calls) == 1

    def test_distinct_keys_do_not_block_each_other(self):
        c = TTLCache(60.0)
        assert c.get_or_compute("a", lambda: 1)[0] == 1
        assert c.get_or_compute("b", lambda: 2)[0] == 2

    def test_as_of_is_part_of_the_key(self, client, stubbed, settings):
        """A cached 'now' answer must never be served for a historical query."""
        seen = []
        app = create_app(settings)
        c = TestClient(app)
        import src.api.services as svc
        original = svc.governance_verification
        try:
            svc.governance_verification = lambda s, as_of=None: (
                seen.append(as_of) or dict(_FAKE_GOV, as_of=as_of))
            c.get("/api/v1/governance/verify", headers=AUTH)
            c.get("/api/v1/governance/verify?as_of=2026-06-14", headers=AUTH)
        finally:
            svc.governance_verification = original
        assert seen == [None, "2026-06-14"]


# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------

class TestStructuredLogging:

    def _fmt(self, **extra) -> dict:
        rec = logging.LogRecord("t", logging.INFO, __file__, 1, "hello", (), None)
        for k, v in extra.items():
            setattr(rec, k, v)
        return json.loads(JsonFormatter().format(rec))

    def test_output_is_valid_json_with_core_fields(self):
        obj = self._fmt()
        assert obj["msg"] == "hello"
        assert obj["level"] == "INFO"
        assert obj["logger"] == "t"
        assert obj["ts"].endswith("+00:00")

    def test_extra_fields_are_promoted(self):
        obj = self._fmt(event="request", status=200, duration_ms=1.5)
        assert obj["event"] == "request"
        assert obj["status"] == 200

    def test_non_serializable_extra_does_not_raise(self):
        obj = self._fmt(path=Path("/tmp/x"))
        assert "/tmp/x" in obj["path"].replace("\\", "/")

    def test_exception_is_captured(self):
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            rec = logging.LogRecord("t", logging.ERROR, __file__, 1, "failed", (),
                                    sys.exc_info())
            obj = json.loads(JsonFormatter().format(rec))
        assert "ValueError: boom" in obj["exc"]

    def test_access_log_emits_structured_request_event(self, client, stubbed, caplog):
        with caplog.at_level(logging.INFO):
            client.get("/api/v1/forward/status", headers=AUTH)
        events = [r for r in caplog.records if getattr(r, "event", None) == "request"]
        assert events, "no structured request event emitted"
        rec = events[-1]
        assert rec.method == "GET"
        assert rec.path == "/api/v1/forward/status"
        assert rec.status == 200
        assert isinstance(rec.duration_ms, float)

    def test_query_string_is_not_logged_verbatim(self, client, stubbed, caplog):
        with caplog.at_level(logging.INFO):
            client.get("/api/v1/governance/verify?as_of=2026-06-14", headers=AUTH)
        rec = [r for r in caplog.records if getattr(r, "event", None) == "request"][-1]
        assert rec.has_query is True
        assert not hasattr(rec, "query")

    def test_request_id_header_is_returned(self, client, stubbed):
        r = client.get("/health")
        assert len(r.headers["X-Request-ID"]) == 36


# ---------------------------------------------------------------------------
# OpenAPI
# ---------------------------------------------------------------------------

class TestOpenAPI:

    def test_schema_is_served(self, client):
        assert client.get("/openapi.json").status_code == 200

    def test_docs_are_served(self, client):
        assert client.get("/docs").status_code == 200

    def test_schema_declares_only_get_operations(self, client):
        paths = client.get("/openapi.json").json()["paths"]
        for path, ops in paths.items():
            assert set(ops) <= {"get"}, f"{path} documents non-GET: {set(ops)}"

    def test_every_protected_endpoint_is_documented(self, client):
        paths = client.get("/openapi.json").json()["paths"]
        for p in PROTECTED:
            key = p.replace("/ivrv_vrp_spread", "/{family_id}")
            assert key in paths, f"{key} missing from OpenAPI"

    def test_security_scheme_is_declared(self, client):
        schema = client.get("/openapi.json").json()
        assert "HTTPBearer" in schema["components"]["securitySchemes"]

    def test_token_is_never_in_the_schema(self, client):
        assert TOKEN not in client.get("/openapi.json").text


# ---------------------------------------------------------------------------
# Integration against the real repo files (skipped when absent, e.g. in CI)
# ---------------------------------------------------------------------------

_REAL_LEDGER = ROOT / "research" / "evidence_library" / "ledger.db"
_REAL_REGISTRY = ROOT / "research" / "evidence_library" / "registry.yaml"


@pytest.mark.skipif(not _REAL_LEDGER.exists(),
                    reason="real ledger.db not present (gitignored; absent in CI)")
class TestAgainstRealLedger:

    @pytest.fixture
    def real_client(self, tmp_path) -> TestClient:
        s = ApiSettings(
            host="127.0.0.1", port=9109, token=TOKEN,
            trading_db_path=ROOT / "data" / "btc_trading.db",
            forward_db_path=ROOT / "research" / "data" / "evidence_forward.db",
            ledger_db_path=_REAL_LEDGER,
            registry_path=_REAL_REGISTRY,
            thresholds_path=ROOT / "research" / "evidence_library" / "thresholds.yaml",
            backup_dir=ROOT / "data" / "backups",
            service_name="btcbot.service", log_level="INFO", cache_ttl_seconds=0.0,
        )
        return TestClient(create_app(s))

    def test_chain_verifies(self, real_client):
        body = real_client.get("/api/v1/governance/chain", headers=AUTH).json()
        assert body["ok"] is True, body["findings"]
        assert body["counts"]["receipts"] > 0

    def test_evidence_summary_matches_registry_size(self, real_client):
        import yaml
        registry = yaml.safe_load(_REAL_REGISTRY.read_text(encoding="utf-8"))
        body = real_client.get("/api/v1/evidence/summary", headers=AUTH).json()
        assert body["n_families"] == len(registry)

    def test_as_of_reconstruction_differs_from_now(self, real_client):
        now = real_client.get("/api/v1/evidence/summary", headers=AUTH).json()
        past = real_client.get("/api/v1/evidence/summary?as_of=2026-01-01",
                               headers=AUTH).json()
        assert past["n_transitions"] <= now["n_transitions"]

    def test_ledger_is_not_modified_by_reads(self, real_client):
        """The strongest available read-only assertion: hash the ledger, hit
        every endpoint, hash again."""
        import hashlib

        def digest() -> str:
            return hashlib.sha256(_REAL_LEDGER.read_bytes()).hexdigest()

        before = digest()
        for path in PROTECTED:
            real_client.get(path, headers=AUTH)
        assert digest() == before, "read-only API modified the governance ledger"
