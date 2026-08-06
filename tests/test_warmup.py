"""
Tests for research/feature_store/warmup.py (engineering roadmap Phase 2,
WBS 2.6 -- Warm-up Contract).

Pure stdlib + PyYAML, no pandas dependency (mirrors registry.py/
value_store.py's own base-env-compatible property) -- collects and runs in
both environments, no importorskip needed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

FEATURE_STORE_DIR = Path(__file__).resolve().parents[1] / "research" / "feature_store"
sys.path.insert(0, str(FEATURE_STORE_DIR))

import warmup  # noqa: E402


class TestCheckWarmupSufficientHistory:
    """Sufficient history always proceeds, regardless of mode."""

    @pytest.mark.parametrize("mode", ["disabled", "warn", "refuse"])
    def test_sufficient_history_proceeds_in_every_mode(self, mode):
        result = warmup.check_warmup("feat_x", warmup_periods=30, available_periods=30, mode=mode)
        assert result.ok is True
        assert result.sufficient is True
        assert result.action == "proceed"
        assert result.detail is None

    @pytest.mark.parametrize("mode", ["disabled", "warn", "refuse"])
    def test_more_than_sufficient_history_proceeds(self, mode):
        result = warmup.check_warmup("feat_x", warmup_periods=30, available_periods=1000, mode=mode)
        assert result.sufficient is True
        assert result.action == "proceed"

    def test_exactly_at_boundary_is_sufficient(self):
        """available_periods == warmup_periods is sufficient (>=, not >)."""
        result = warmup.check_warmup("feat_x", warmup_periods=30, available_periods=30, mode="refuse")
        assert result.sufficient is True


class TestCheckWarmupInsufficientHistory:
    """Insufficient history behaves differently per mode -- this is the
    entire point of the governed rollout flag."""

    def test_disabled_mode_proceeds_silently(self):
        result = warmup.check_warmup("feat_x", warmup_periods=100, available_periods=10, mode="disabled")
        assert result.ok is True
        assert result.sufficient is False
        assert result.action == "proceed"
        assert result.detail is not None

    def test_warn_mode_proceeds_with_warning(self):
        result = warmup.check_warmup("feat_x", warmup_periods=100, available_periods=10, mode="warn")
        assert result.ok is True
        assert result.sufficient is False
        assert result.action == "proceed_with_warning"
        assert "feat_x" in result.detail
        assert "100" in result.detail
        assert "10" in result.detail

    def test_refuse_mode_raises_warmup_refusal(self):
        with pytest.raises(warmup.WarmupRefusal) as exc_info:
            warmup.check_warmup("feat_x", warmup_periods=100, available_periods=10, mode="refuse")
        exc = exc_info.value
        assert exc.spec_name == "feat_x"
        assert exc.warmup_periods == 100
        assert exc.available_periods == 10

    def test_refuse_mode_never_returns_a_result(self):
        """refuse mode either raises or (if sufficient) returns -- it never
        returns a result with action == 'refused'; that string only
        appears in the caller's own bookkeeping (migrate_evidence_engine_
        features.py), not in WarmupCheckResult itself."""
        with pytest.raises(warmup.WarmupRefusal):
            warmup.check_warmup("feat_x", warmup_periods=100, available_periods=10, mode="refuse")


class TestCheckWarmupModeValidation:
    def test_unknown_mode_raises_policy_error(self):
        with pytest.raises(warmup.WarmupPolicyError):
            warmup.check_warmup("feat_x", warmup_periods=10, available_periods=100, mode="bogus")


class TestLoadWarmupMode:
    def test_missing_file_defaults_to_warn(self, tmp_path):
        missing = tmp_path / "does_not_exist.yaml"
        assert warmup.load_warmup_mode(missing) == "warn"

    def test_file_with_mode_disabled(self, tmp_path):
        p = tmp_path / "policy.yaml"
        p.write_text("mode: disabled\n", encoding="utf-8")
        assert warmup.load_warmup_mode(p) == "disabled"

    def test_file_with_mode_refuse(self, tmp_path):
        p = tmp_path / "policy.yaml"
        p.write_text("mode: refuse\n", encoding="utf-8")
        assert warmup.load_warmup_mode(p) == "refuse"

    def test_file_without_mode_key_defaults_to_warn(self, tmp_path):
        p = tmp_path / "policy.yaml"
        p.write_text("version: '1.0'\n", encoding="utf-8")
        assert warmup.load_warmup_mode(p) == "warn"

    def test_empty_file_defaults_to_warn(self, tmp_path):
        p = tmp_path / "policy.yaml"
        p.write_text("", encoding="utf-8")
        assert warmup.load_warmup_mode(p) == "warn"

    def test_unknown_mode_in_file_raises(self, tmp_path):
        p = tmp_path / "policy.yaml"
        p.write_text("mode: bogus\n", encoding="utf-8")
        with pytest.raises(warmup.WarmupPolicyError):
            warmup.load_warmup_mode(p)

    def test_check_warmup_reads_policy_file_when_mode_not_passed(self, tmp_path):
        p = tmp_path / "policy.yaml"
        p.write_text("mode: refuse\n", encoding="utf-8")
        with pytest.raises(warmup.WarmupRefusal):
            warmup.check_warmup(
                "feat_x", warmup_periods=100, available_periods=10,
                mode=warmup.load_warmup_mode(p),
            )


class TestRealPolicyFile:
    """The real, shipped warmup_policy.yaml -- confirms the actual rollout
    state matches this milestone's documented default (roadmap §16 R5:
    warn mode for the rollout, never refuse by default)."""

    def test_real_policy_file_exists(self):
        assert warmup._POLICY_PATH.exists()

    def test_real_policy_file_defaults_to_warn(self):
        assert warmup.load_warmup_mode() == "warn"

    def test_real_policy_file_is_valid_yaml_with_known_mode(self):
        import yaml
        data = yaml.safe_load(warmup._POLICY_PATH.read_text(encoding="utf-8"))
        assert data["mode"] in warmup.MODES
