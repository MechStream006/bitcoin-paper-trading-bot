"""
Tests for forward_evidence.pre_reg_guard.verify_fn_hash()

Run from research/:
    conda run -n btc_bot pytest forward_evidence/tests/test_pre_reg_guard.py -v
"""
from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

from forward_evidence.pre_reg_guard import FnHashViolation, _sha256_lf, verify_fn_hash


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_fn_file(tmp_path: pathlib.Path) -> pathlib.Path:
    """A minimal decision function file with LF line endings."""
    f = tmp_path / "decision_function_test.py"
    f.write_bytes(b"def compute_signal(close):\n    return close * 0\n")
    return f


@pytest.fixture()
def tmp_fn_file_crlf(tmp_path: pathlib.Path) -> pathlib.Path:
    """Same content but with CRLF line endings (simulates Windows checkout)."""
    f = tmp_path / "decision_function_test_crlf.py"
    f.write_bytes(b"def compute_signal(close):\r\n    return close * 0\r\n")
    return f


def _make_prereg(
    tmp_path: pathlib.Path,
    fn_file: pathlib.Path,
    signal_id: str = "test_signal",
) -> pathlib.Path:
    """Write a minimal pre-registration JSON pointing at fn_file's LF-normalised hash."""
    fn_hash = _sha256_lf(fn_file)
    data = {
        "schema_version": 1,
        "version": "1.0.0",
        "signal_id": signal_id,
        "pre_registration_date": "2026-06-14T17:48:39+00:00",
        "function_spec": {
            "decision_function_hash": fn_hash,
        },
    }
    p = tmp_path / f"pre_registration_{signal_id}.json"
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# _sha256_lf: line-ending normalisation
# ---------------------------------------------------------------------------

class TestSha256Lf:
    def test_lf_file_unchanged(self, tmp_fn_file: pathlib.Path) -> None:
        raw_hash = hashlib.sha256(tmp_fn_file.read_bytes()).hexdigest()
        assert _sha256_lf(tmp_fn_file) == raw_hash

    def test_crlf_equals_lf(
        self, tmp_fn_file: pathlib.Path, tmp_fn_file_crlf: pathlib.Path
    ) -> None:
        """CRLF and LF versions of the same content must produce the same hash."""
        assert _sha256_lf(tmp_fn_file) == _sha256_lf(tmp_fn_file_crlf)

    def test_different_content_different_hash(self, tmp_path: pathlib.Path) -> None:
        a = tmp_path / "a.py"
        b = tmp_path / "b.py"
        a.write_bytes(b"x = 1\n")
        b.write_bytes(b"x = 2\n")
        assert _sha256_lf(a) != _sha256_lf(b)


# ---------------------------------------------------------------------------
# verify_fn_hash: happy path
# ---------------------------------------------------------------------------

class TestVerifyFnHashPass:
    def test_returns_hash_string(
        self, tmp_path: pathlib.Path, tmp_fn_file: pathlib.Path
    ) -> None:
        pre_reg = _make_prereg(tmp_path, tmp_fn_file)
        result = verify_fn_hash(pre_reg, tmp_fn_file, "test_signal")
        assert result == _sha256_lf(tmp_fn_file)

    def test_crlf_file_passes_against_lf_registered_hash(
        self,
        tmp_path: pathlib.Path,
        tmp_fn_file: pathlib.Path,
        tmp_fn_file_crlf: pathlib.Path,
    ) -> None:
        """Hash registered from LF file; runtime file is CRLF (Windows checkout).
        Must still pass because both normalise to the same LF hash."""
        pre_reg = _make_prereg(tmp_path, tmp_fn_file, signal_id="test_signal")
        result = verify_fn_hash(pre_reg, tmp_fn_file_crlf, "test_signal")
        assert result == _sha256_lf(tmp_fn_file)

    def test_real_h5_round_trip(self) -> None:
        """End-to-end: verify against the actual registered h5 pre-registration file."""
        here = pathlib.Path(__file__).resolve().parent.parent  # forward_evidence/
        pre_reg = here / "pre_registration_h5_vol_regime.json"
        fn_file = here / "decision_function_h5.py"
        if not pre_reg.exists() or not fn_file.exists():
            pytest.skip("Registration files not present (not in CI environment)")
        result = verify_fn_hash(pre_reg, fn_file, "h5_vol_regime")
        assert len(result) == 64  # valid sha256 hex

    def test_real_g1_round_trip(self) -> None:
        here = pathlib.Path(__file__).resolve().parent.parent
        pre_reg = here / "pre_registration_g1_market_regime_sma200.json"
        fn_file = here / "decision_function_g1_market_regime.py"
        if not pre_reg.exists() or not fn_file.exists():
            pytest.skip("Registration files not present")
        result = verify_fn_hash(pre_reg, fn_file, "g1_market_regime_sma200")
        assert len(result) == 64

    def test_real_g3_round_trip(self) -> None:
        here = pathlib.Path(__file__).resolve().parent.parent
        pre_reg = here / "pre_registration_g3_tsmom.json"
        fn_file = here / "decision_function_g3_tsmom.py"
        if not pre_reg.exists() or not fn_file.exists():
            pytest.skip("Registration files not present")
        result = verify_fn_hash(pre_reg, fn_file, "g3_momentum_tsmom")
        assert len(result) == 64


# ---------------------------------------------------------------------------
# verify_fn_hash: failure paths
# ---------------------------------------------------------------------------

class TestVerifyFnHashFail:
    def test_modified_file_raises(
        self, tmp_path: pathlib.Path, tmp_fn_file: pathlib.Path
    ) -> None:
        pre_reg = _make_prereg(tmp_path, tmp_fn_file)
        # Silently modify the file after registration
        tmp_fn_file.write_bytes(b"def compute_signal(close):\n    return close * 1\n")
        with pytest.raises(FnHashViolation, match="fn_hash MISMATCH"):
            verify_fn_hash(pre_reg, tmp_fn_file, "test_signal")

    def test_wrong_signal_id_raises(
        self, tmp_path: pathlib.Path, tmp_fn_file: pathlib.Path
    ) -> None:
        """Caller passes the wrong signal_id — wrong pre-registration file."""
        pre_reg = _make_prereg(tmp_path, tmp_fn_file, signal_id="other_signal")
        with pytest.raises(FnHashViolation, match="signal_id mismatch"):
            verify_fn_hash(pre_reg, tmp_fn_file, "test_signal")

    def test_wrong_prereg_for_right_signal(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Pre-reg JSON has matching signal_id but was built from a different file."""
        file_a = tmp_path / "fn_a.py"
        file_b = tmp_path / "fn_b.py"
        file_a.write_bytes(b"x = 1\n")
        file_b.write_bytes(b"x = 2\n")
        pre_reg = _make_prereg(tmp_path, file_a, signal_id="test_signal")
        with pytest.raises(FnHashViolation, match="fn_hash MISMATCH"):
            verify_fn_hash(pre_reg, file_b, "test_signal")

    def test_missing_fn_file_raises(
        self, tmp_path: pathlib.Path, tmp_fn_file: pathlib.Path
    ) -> None:
        pre_reg = _make_prereg(tmp_path, tmp_fn_file)
        missing = tmp_path / "nonexistent.py"
        with pytest.raises(FileNotFoundError):
            verify_fn_hash(pre_reg, missing, "test_signal")

    def test_missing_prereg_raises(
        self, tmp_path: pathlib.Path, tmp_fn_file: pathlib.Path
    ) -> None:
        missing_prereg = tmp_path / "no_such_prereg.json"
        with pytest.raises(FileNotFoundError):
            verify_fn_hash(missing_prereg, tmp_fn_file, "test_signal")

    def test_violation_is_runtime_error_subclass(
        self, tmp_path: pathlib.Path, tmp_fn_file: pathlib.Path
    ) -> None:
        """FnHashViolation must be a RuntimeError so it propagates through
        most except Exception clauses without being silently swallowed."""
        pre_reg = _make_prereg(tmp_path, tmp_fn_file)
        tmp_fn_file.write_bytes(b"# tampered\n")
        exc = None
        try:
            verify_fn_hash(pre_reg, tmp_fn_file, "test_signal")
        except FnHashViolation as e:
            exc = e
        assert exc is not None
        assert isinstance(exc, RuntimeError)

    def test_violation_is_not_system_exit(
        self, tmp_path: pathlib.Path, tmp_fn_file: pathlib.Path
    ) -> None:
        """The guard must NOT call sys.exit(). Caller controls process termination."""
        import subprocess, sys, textwrap
        pre_reg = _make_prereg(tmp_path, tmp_fn_file)
        tmp_fn_file.write_bytes(b"# tampered\n")
        # Prove it raises, not exits, by catching RuntimeError (parent of FnHashViolation)
        try:
            verify_fn_hash(pre_reg, tmp_fn_file, "test_signal")
            assert False, "should have raised"
        except SystemExit:
            pytest.fail("verify_fn_hash called sys.exit() — must raise instead")
        except FnHashViolation:
            pass  # correct behaviour
