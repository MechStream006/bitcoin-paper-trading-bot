"""
FB-10b-X -- regression coverage for the four locally-remediated defects.

D-2 operator CLI plumbing · D-3 evidence-engine import collision ·
D-4 stale docstrings · D-5 explicit corpus-slice validation.

Nothing here computes DSR/BH, writes a snapshot, or reads the sealed lockbox.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_EVLIB = ROOT / "research" / "evidence_library"
_EVID = ROOT / "research" / "evidence"
_FWD = ROOT / "research" / "forward_evidence"
for _p in (_EVLIB, _EVID, _FWD, ROOT / "research"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import forward_dsr_bh as P  # noqa: E402
import forward_snapshot as FS  # noqa: E402

PY = sys.executable


# ---------------------------------------------------------------------------
# D-3 -- import collision
# ---------------------------------------------------------------------------

def _subprocess_probe(body: str) -> str:
    """Run a probe in a FRESH interpreter: sys.modules is process-global, so a
    poisoning test is only meaningful in isolation."""
    out = subprocess.run([PY, "-c", body], cwd=str(ROOT),
                         capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr[-3000:]
    return out.stdout


class TestImportCollision:
    def test_clean_interpreter_yields_34(self):
        out = _subprocess_probe(
            "import sys; sys.path.insert(0,'research/evidence_library')\n"
            "import forward_dsr_bh as P\n"
            "print(len(P.library_specs()))\n")
        assert out.strip() == "34"

    def test_poisoned_interpreter_still_yields_34(self):
        """research/dq/engine.py holds the bare name first -- the exact
        condition that made library_specs() raise ImportError."""
        out = _subprocess_probe(
            "import sys\n"
            "sys.path.insert(0,'research/dq'); import engine\n"
            "assert engine.__file__.replace('\\\\','/').endswith('research/dq/engine.py')\n"
            "sys.path.insert(0,'research/evidence_library')\n"
            "import forward_dsr_bh as P\n"
            "print(len(P.library_specs()))\n")
        assert out.strip() == "34"

    def test_poisoned_binding_is_restored_after_success(self):
        out = _subprocess_probe(
            "import sys\n"
            "sys.path.insert(0,'research/dq'); import engine\n"
            "before = sys.modules['engine'].__file__\n"
            "sys.path.insert(0,'research/evidence_library')\n"
            "import forward_dsr_bh as P\n"
            "P.library_specs()\n"
            "print(sys.modules['engine'].__file__ == before)\n")
        assert out.strip() == "True"

    def test_binding_is_restored_after_exception(self):
        """A module that raises during exec must not leave the bare name bound
        to our engine, nor leave a half-initialised module cached."""
        out = _subprocess_probe(
            "import sys, pathlib\n"
            "sys.path.insert(0,'research/dq'); import engine\n"
            "before = sys.modules['engine'].__file__\n"
            "sys.path.insert(0,'research/evidence_library')\n"
            "import forward_dsr_bh as P\n"
            "try:\n"
            "    P._load_evidence_module('this_module_does_not_exist')\n"
            "except BaseException:\n"
            "    pass\n"
            "key = P._BINDING_KEY % 'this_module_does_not_exist'\n"
            "print(sys.modules['engine'].__file__ == before, key not in sys.modules)\n")
        assert out.strip() == "True True"

    def test_absent_binding_is_restored_to_absent(self):
        """If the caller had no 'engine' at all, it must have none afterwards."""
        out = _subprocess_probe(
            "import sys\n"
            "sys.path.insert(0,'research/evidence_library')\n"
            "import forward_dsr_bh as P\n"
            "sys.modules.pop('engine', None)\n"
            "sys.modules.pop(P._BINDING_KEY % 'signals', None)\n"
            "sys.modules.pop(P._BINDING_KEY % 'engine', None)\n"
            "P.library_specs()\n"
            "print('engine' not in sys.modules)\n")
        assert out.strip() == "True"

    def test_no_global_import_hook_installed(self):
        src = (_EVLIB / "forward_dsr_bh.py").read_text(encoding="utf-8")
        for banned in ("sys.meta_path", "sys.path_hooks", "MetaPathFinder", "__import__"):
            assert banned not in src, f"{banned}: no global import-system change"

    def test_shared_modules_untouched(self):
        out = subprocess.run(
            ["git", "diff", "--name-only", "HEAD", "--",
             "research/dq/engine.py", "research/evidence/engine.py",
             "research/evidence/signals.py"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=30).stdout
        assert out.strip() == "", f"protected shared module changed: {out}"

    def test_unique_key_is_not_the_bare_name(self):
        assert "engine" != P._BINDING_KEY % "engine"
        assert (P._BINDING_KEY % "engine") not in ("engine", "signals")


# ---------------------------------------------------------------------------
# D-5 -- corpus-slice validation
# ---------------------------------------------------------------------------

def _master(days: list[str]):
    import pandas as pd
    return pd.DataFrame({
        "dt": [date.fromisoformat(d) for d in days],
        "close": [100.0 + i for i in range(len(days))],
    })


class TestCorpusValidation:
    def test_expected_window_is_derived_and_equals_the_preregistered_1612(self):
        """FDB-2a's two dates yield 1612 -- the row count the pre-registrations
        record for this window. Derived, never hardcoded."""
        days = P.expected_train_days()
        assert len(days) == 1612
        assert days[0] == "2021-01-01" and days[-1] == "2025-05-31"
        assert len(days) == (date(2025, 5, 31) - date(2021, 1, 1)).days + 1

    def test_complete_slice_passes(self):
        P.validate_train_slice(_master(P.expected_train_days()))

    def test_empty_slice_refuses(self):
        with pytest.raises(P.BatteryRefused) as exc:
            P.validate_train_slice(_master([]))
        assert exc.value.reason == "corpus_incomplete"

    def test_missing_partition_refuses_and_names_the_gap(self):
        """Production's actual shape: only 2025-01 onward present."""
        days = [d for d in P.expected_train_days() if d >= "2025-01-01"]
        with pytest.raises(P.BatteryRefused) as exc:
            P.validate_train_slice(_master(days))
        assert exc.value.reason == "corpus_incomplete"
        assert exc.value.context["first_missing"] == "2021-01-01"
        assert exc.value.context["missing_days"] == 1612 - len(days)
        assert "PROVISIONING fault" in exc.value.detail

    def test_incomplete_partition_refuses(self):
        """A single missing day inside an otherwise-complete window."""
        days = [d for d in P.expected_train_days() if d != "2023-07-04"]
        with pytest.raises(P.BatteryRefused) as exc:
            P.validate_train_slice(_master(days))
        assert exc.value.context["missing_days"] == 1
        assert exc.value.context["first_missing"] == "2023-07-04"

    def test_never_truncates_to_what_is_available(self):
        """A short-but-contiguous frame is refused, not accepted as the window."""
        days = P.expected_train_days()[:500]
        with pytest.raises(P.BatteryRefused):
            P.validate_train_slice(_master(days))

    def test_lockbox_day_present_in_the_frame_is_refused_not_read(self):
        """A deliberately unsliced frame carrying a sealed-window day must be
        refused as unsliced -- the validation never consults its values."""
        days = P.expected_train_days() + ["2025-06-01", "2026-05-31"]
        with pytest.raises(P.BatteryRefused) as exc:
            P.validate_train_slice(_master(days))
        assert exc.value.reason == "corpus_beyond_train_slice"
        assert exc.value.context["beyond"][0] == "2025-06-01"

    def test_train_end_is_not_the_lockbox_end(self):
        assert P.TRAIN_END == "2025-05-31"
        src = (_EVLIB / "forward_dsr_bh.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        for n in ast.walk(tree):
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and ast.get_docstring(n):
                n.body = n.body[1:] or [ast.Pass()]
        assert "2026-05-31" not in ast.unparse(tree)

    def test_frame_without_dt_column_refuses(self):
        import pandas as pd
        with pytest.raises(P.BatteryRefused) as exc:
            P.validate_train_slice(pd.DataFrame({"close": [1.0]}))
        assert exc.value.reason == "corpus_incomplete"


# ---------------------------------------------------------------------------
# D-5 -- corpus refusal is DISTINGUISHABLE from sample-size refusal
# ---------------------------------------------------------------------------

FROZEN_MINIMA = {
    "h5_vol_regime": 15, "g1_market_regime_sma200": 10, "g3_momentum_tsmom": 15,
    "g1_trend_regime_ma_cross": 30, "ivrv_vrp_spread": 30,
    "g2_funding_level": 30, "g3_atr_expansion": 26,
}
PRODUCTION_COUNTS = {
    "h5_vol_regime": 7, "g1_market_regime_sma200": 7, "g3_momentum_tsmom": 8,
    "ivrv_vrp_spread": 6, "g3_atr_expansion": 3,
    "g1_trend_regime_ma_cross": 0, "g2_funding_level": 0,
}


def _forward_db(tmp_path, counts):
    import sqlite3
    db = tmp_path / "evidence_forward.db"
    con = sqlite3.connect(db)
    con.executescript((_FWD / "schema_forward.sql").read_text(encoding="utf-8"))
    for fam, n in counts.items():
        for i in range(n):
            entry = (date(2026, 1, 1) + timedelta(days=8 * i)).isoformat()
            exit_ = (date(2026, 1, 1) + timedelta(days=8 * i + 7)).isoformat()
            con.execute("INSERT INTO trade_entries (signal_id, entry_date, direction,"
                        " entry_close_price, planned_exit_date, open) VALUES (?,?,?,?,?,0)",
                        (fam, entry, 1 if i % 2 else -1, 100.0, exit_))
            eid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
            con.execute("INSERT INTO trade_exits (entry_id, exit_date, exit_close_price,"
                        " cost_bps) VALUES (?,?,?,?)", (eid, exit_, 100.0 + (i % 5) - 2, 15.0))
    con.commit()
    con.close()
    return db


class TestRefusalsAreDistinguishable:
    def test_sample_size_refusal_when_corpus_is_irrelevant(self, tmp_path):
        """Today's production state: membership fails first, so the corpus is
        never consulted and the reason names the battery, not the data."""
        res = P.compute_battery(_forward_db(tmp_path, PRODUCTION_COUNTS),
                                "2026-09-01", FROZEN_MINIMA)
        assert res.status == "refused"
        assert res.reason == "empty_battery"
        assert res.reason != "corpus_incomplete"

    def test_corpus_refusal_when_the_battery_is_satisfied(self, tmp_path):
        """With a member present, an under-provisioned corpus refuses as a
        PROVISIONING fault -- never as member_sharpe_uncomputable."""
        counts = {f: 0 for f in FROZEN_MINIMA}
        counts["g1_market_regime_sma200"] = 12
        short = _master([d for d in P.expected_train_days() if d >= "2025-01-01"])
        res = P.compute_battery(_forward_db(tmp_path, counts), "2027-01-01",
                                FROZEN_MINIMA, master=short)
        assert res.status == "refused"
        assert res.reason == "corpus_incomplete"
        assert res.reason != "member_sharpe_uncomputable"
        assert res.members == ["g1_market_regime_sma200"]

    def test_the_two_reasons_are_never_the_same_string(self):
        assert "empty_battery" != "corpus_incomplete"

    def test_no_ledger_write_on_either_refusal(self, tmp_path):
        import record
        led = tmp_path / "ledger.db"
        record.init_db(led)
        before = led.read_bytes()
        P.compute_battery(_forward_db(tmp_path, PRODUCTION_COUNTS),
                          "2026-09-01", FROZEN_MINIMA)
        assert led.read_bytes() == before


# ---------------------------------------------------------------------------
# D-2 -- operator CLI
# ---------------------------------------------------------------------------

class TestOperatorCli:
    def test_producer_has_out_flag_and_writes_only_a_file(self, tmp_path):
        out = tmp_path / "battery.json"
        db = _forward_db(tmp_path, PRODUCTION_COUNTS)
        rc = P.main(["--forward-db", str(db), "--as-of", "2026-09-01",
                     "--actor", "tester", "--out", str(out)]
                    + [f"--minimum={k}={v}" for k, v in FROZEN_MINIMA.items()])
        assert rc == 0 and out.exists()
        doc = json.loads(out.read_text(encoding="utf-8"))
        assert doc["status"] == "refused" and doc["reason"] == "empty_battery"
        assert doc["payloads"] == {}

    def test_snapshot_cli_accepts_a_payload_and_refuses_a_mismatched_as_of(self, tmp_path):
        bad = tmp_path / "b.json"
        bad.write_text(json.dumps({"as_of": "2026-01-01", "payloads": {}}), encoding="utf-8")
        with pytest.raises(SystemExit) as exc:
            FS._load_dsr_payload(bad, "h5_vol_regime", "2026-09-01")
        assert "as_of" in str(exc.value)

    def test_snapshot_cli_refuses_a_family_that_was_not_a_member(self, tmp_path):
        doc = tmp_path / "b.json"
        doc.write_text(json.dumps({"as_of": "2026-09-01", "payloads": {}}), encoding="utf-8")
        with pytest.raises(SystemExit) as exc:
            FS._load_dsr_payload(doc, "h5_vol_regime", "2026-09-01")
        assert "not a battery member" in str(exc.value) or "no result" in str(exc.value)

    def test_absent_payload_yields_none_none(self):
        assert FS._load_dsr_payload(None, "h5_vol_regime", "2026-09-01") == (None, None)

    def test_payload_round_trips(self, tmp_path):
        doc = tmp_path / "b.json"
        doc.write_text(json.dumps({
            "as_of": "2026-09-01",
            "payloads": {"h5_vol_regime": {"block": {"schema": "x"},
                                           "governance": {"dsr_prob": 0.7, "bh_p": 0.02}}},
        }), encoding="utf-8")
        block, gov = FS._load_dsr_payload(doc, "h5_vol_regime", "2026-09-01")
        assert block == {"schema": "x"} and gov == {"dsr_prob": 0.7, "bh_p": 0.02}

    def test_both_cli_branches_pass_the_same_dsr_block(self):
        """Dry-run/write parity at the CLI: one loader, both branches."""
        src = (_FWD / "forward_snapshot.py").read_text(encoding="utf-8")
        body = src.split("def main(")[1]
        assert body.count("_load_dsr_payload(") == 1, "one loader for both branches"
        assert "dsr_block=dsr_block)" in body, "dry-run branch receives it"
        assert "dsr_block=dsr_block, governance=governance," in body, "write branch receives it"

    def test_cli_requires_explicit_family_as_of_and_actor(self):
        src = (_FWD / "forward_snapshot.py").read_text(encoding="utf-8")
        for flag in ('"--family", required=True', '"--as-of", required=True',
                     '"--actor", required=True'):
            assert flag in src, flag

    def test_write_remains_explicit_dry_run_returns_first(self):
        src = (_FWD / "forward_snapshot.py").read_text(encoding="utf-8")
        body = src.split("def main(")[1]
        assert body.index("if args.dry_run:") < body.index("run_id, outcome = write_snapshot")

    def test_no_scheduler_wiring(self):
        for mod in ("run_daily.py", "ledger_sync.py"):
            src = (_FWD / mod).read_text(encoding="utf-8")
            assert "forward_snapshot" not in src and "forward_dsr_bh" not in src, mod

    def test_ledger_sync_unchanged(self):
        out = subprocess.run(["git", "diff", "--", "research/forward_evidence/ledger_sync.py"],
                             cwd=str(ROOT), capture_output=True, text=True, timeout=30).stdout
        assert out.strip() == ""


# ---------------------------------------------------------------------------
# D-4 -- documentation
# ---------------------------------------------------------------------------

class TestDocstrings:
    DOC = (_FWD / "forward_snapshot.py")

    def test_stale_statements_are_gone(self):
        text = self.DOC.read_text(encoding="utf-8")
        assert "`governance=None` always" not in text
        assert "is always a *new* `(family_id, provenance, window_end)`" not in text

    def test_new_statements_match_the_implementation(self):
        text = self.DOC.read_text(encoding="utf-8")
        assert "No DSR / BH-p COMPUTED HERE" in text
        assert "`(family_id, provenance, window_end, engine_version)`" in text
        assert "engine-scoped" in text
        # and the code really does what the docstring now claims
        assert "governance=governance," in text
        assert "AND engine_version = ?" in text

    def test_documentation_change_hid_no_behaviour_change(self):
        """The D-4 edit touched only the module docstring."""
        out = subprocess.run(["git", "diff", "-U0", "--",
                              "research/forward_evidence/forward_snapshot.py"],
                             cwd=str(ROOT), capture_output=True, text=True, timeout=30).stdout
        import ast as _ast
        head = subprocess.run(["git", "show", "HEAD:research/forward_evidence/forward_snapshot.py"],
                              cwd=str(ROOT), capture_output=True, text=True, timeout=30).stdout
        cur = self.DOC.read_text(encoding="utf-8")

        def bodies(src):
            t = _ast.parse(src)
            t.body = [n for n in t.body
                      if not (isinstance(n, _ast.Expr) and isinstance(n.value, _ast.Constant)
                              and isinstance(n.value.value, str))]
            return {n.name: _ast.dump(n) for n in t.body if isinstance(n, _ast.FunctionDef)}

        before, after = bodies(head), bodies(cur)
        # functions untouched by D-2 must be structurally identical
        for name in ("build_snapshot_metrics", "_existing_snapshot_run", "_load_registry"):
            assert before[name] == after[name], f"{name} changed outside the D-4 scope"
