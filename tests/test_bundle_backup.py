"""Tests for scripts/bundle_backup.py — encrypted recovery-code bundle (BK2).

Two tiers, mirroring tests/test_offhost_backup.py:

  * Logic tests (always run): SHA capture, sentinel skip/advance, manifest
    construction, status sidecar, healthcheck severity states.
  * Real-crypto tests (skipped when `age` is absent): a genuine
    create -> verify -> encrypt -> decrypt -> clone -> ancestor-checkout
    round-trip, proving the recovered bundle yields the exact code that made
    a given backup.

The real-crypto tier carries the weight. The whole point of this artifact is
that a clean host can reconstruct verification code without GitHub; only an
end-to-end decrypt-and-clone proves that, and a bundle that cannot be opened
fails exactly as silently as an undecryptable backup.

Run:
    conda run -n btc_bot pytest tests/test_bundle_backup.py -v
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ob = _load("offhost_backup")
bb = _load("bundle_backup")
hc = _load("healthcheck")

AGE = shutil.which("age")
requires_age = pytest.mark.skipif(AGE is None, reason="age binary not installed")

# A throwaway keypair is generated per-test rather than reusing the production
# recipient: no test may depend on production key material existing.
def _keypair(tmp_path: Path) -> tuple[str, Path]:
    identity = tmp_path / "id.txt"
    r = subprocess.run(["age-keygen", "-o", str(identity)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    pub = subprocess.run(["age-keygen", "-y", str(identity)],
                         capture_output=True, text=True)
    assert pub.returncode == 0, pub.stderr
    return pub.stdout.strip().splitlines()[0], identity


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd),
                          capture_output=True, text=True)


def _make_repo(path: Path, commits: int = 3) -> list[str]:
    """A real git repo with real history. Returns full SHAs oldest-first."""
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q", "-b", "main", "."], path)
    _git(["config", "user.email", "drill@example.invalid"], path)
    _git(["config", "user.name", "Drill"], path)
    shas = []
    for i in range(commits):
        (path / f"file{i}.txt").write_text(f"content {i}\n", encoding="utf-8")
        _git(["add", "-A"], path)
        _git(["commit", "-q", "-m", f"commit {i}"], path)
        shas.append(_git(["rev-parse", "HEAD"], path).stdout.strip())
    return shas


# ---------------------------------------------------------------------------
# 1 — SHA capture and binding
# ---------------------------------------------------------------------------

class TestHeadSha:
    def test_returns_full_40_char_sha(self, tmp_path: Path):
        repo = tmp_path / "repo"
        shas = _make_repo(repo)
        got = bb.head_sha(repo)
        assert got == shas[-1]
        assert len(got) == 40

    def test_raises_outside_a_repo(self, tmp_path: Path):
        with pytest.raises(bb.BundleError):
            bb.head_sha(tmp_path / "not-a-repo")


class TestBundleCreationAndVerify:
    def test_create_then_verify_binds_exact_sha(self, tmp_path: Path):
        repo = tmp_path / "repo"
        shas = _make_repo(repo)
        out = tmp_path / "out" / "b.bundle"
        bb.create_bundle(out, repo, "main")
        assert out.exists() and out.stat().st_size > 0
        detail = bb.verify_bundle(out, shas[-1], "main")
        assert detail["sha"] == shas[-1]
        assert detail["ref"] == "refs/heads/main"
        assert detail["complete_history"] is True

    def test_verify_rejects_wrong_sha(self, tmp_path: Path):
        repo = tmp_path / "repo"
        shas = _make_repo(repo)
        out = tmp_path / "b.bundle"
        bb.create_bundle(out, repo, "main")
        with pytest.raises(bb.BundleError, match="SHA mismatch"):
            bb.verify_bundle(out, shas[0], "main")   # an ancestor, not the tip

    def test_verify_rejects_missing_file(self, tmp_path: Path):
        with pytest.raises(bb.BundleError, match="not found"):
            bb.verify_bundle(tmp_path / "nope.bundle", "0" * 40, "main")

    def test_verify_rejects_corrupt_bundle(self, tmp_path: Path):
        repo = tmp_path / "repo"
        shas = _make_repo(repo)
        out = tmp_path / "b.bundle"
        bb.create_bundle(out, repo, "main")
        data = bytearray(out.read_bytes())
        for i in range(len(data) // 2, min(len(data), len(data) // 2 + 400)):
            data[i] = 0
        out.write_bytes(bytes(data))
        with pytest.raises(bb.BundleError):
            bb.verify_bundle(out, shas[-1], "main")

    def test_verify_works_without_a_surrounding_repo(self, tmp_path: Path, monkeypatch):
        """`git bundle verify` refuses to run outside a repository; the helper
        must construct its own scratch repo. Regression lock for the exact
        failure seen on the clean drill host."""
        repo = tmp_path / "repo"
        shas = _make_repo(repo)
        out = tmp_path / "elsewhere" / "b.bundle"
        bb.create_bundle(out, repo, "main")
        monkeypatch.chdir(tmp_path / "elsewhere")   # cwd is NOT a git repo
        assert bb.verify_bundle(out, shas[-1], "main")["sha"] == shas[-1]


# ---------------------------------------------------------------------------
# 2 — manifest
# ---------------------------------------------------------------------------

class TestGitManifest:
    def test_records_full_and_short_sha(self, tmp_path: Path):
        repo = tmp_path / "repo"
        shas = _make_repo(repo)
        out = tmp_path / "b.bundle"
        bb.create_bundle(out, repo, "main")
        m = bb.build_git_manifest(out, shas[-1], "main")
        assert m["git_sha"] == shas[-1]
        assert m["git_sha_short"] == shas[-1][:7]
        assert m["sha256"] == ob.sha256_file(out)
        assert m["file_size_bytes"] == out.stat().st_size
        assert m["schema"] == "btcbot-code-bundle/1"
        # The documented join key against a ledger manifest's 7-char git_sha.
        assert m["joins_ledger_manifest_on"] == "git_sha_short"

    def test_manifest_carries_no_key_material(self, tmp_path: Path):
        repo = tmp_path / "repo"
        shas = _make_repo(repo)
        out = tmp_path / "b.bundle"
        bb.create_bundle(out, repo, "main")
        text = json.dumps(bb.build_git_manifest(out, shas[-1], "main"))
        assert "AGE-SECRET-KEY" not in text.upper()
        assert "PRIVATE" not in text.upper()


# ---------------------------------------------------------------------------
# 3 — sentinel / skip-on-unchanged
# ---------------------------------------------------------------------------

class TestSentinel:
    def test_absent_sentinel_reads_none(self, tmp_path: Path):
        assert bb.read_sentinel(tmp_path) is None

    def test_write_then_read_roundtrip(self, tmp_path: Path):
        bb.write_sentinel(tmp_path, "a" * 40)
        assert bb.read_sentinel(tmp_path) == "a" * 40

    def test_unchanged_sha_skips_without_creating_a_bundle(self, tmp_path: Path):
        repo = tmp_path / "repo"
        shas = _make_repo(repo)
        bdir = tmp_path / "bundles"
        bb.write_sentinel(bdir, shas[-1])
        rc = bb.run_bundle_backup(repo_root=repo, bundle_dir=bdir, ref="main")
        assert rc == 0
        assert not list(bdir.glob("*.bundle"))
        status = json.loads((bdir / bb.BUNDLE_STATUS_FILENAME).read_text())
        assert status["status"] == "skipped_unchanged"

    @requires_age
    def test_force_overrides_the_skip(self, tmp_path: Path, monkeypatch):
        repo = tmp_path / "repo"
        shas = _make_repo(repo)
        bdir = tmp_path / "bundles"
        bb.write_sentinel(bdir, shas[-1])
        recipient, _ = _keypair(tmp_path)
        monkeypatch.setattr(
            ob, "load_env_var",
            lambda k, root=None: recipient if k == "BACKUP_AGE_RECIPIENT" else "",
        )
        assert bb.run_bundle_backup(
            repo_root=repo, bundle_dir=bdir, ref="main", force=True) == 0
        assert (bdir / f"btcbot-{shas[-1]}.bundle").exists()

    @requires_age
    def test_sentinel_not_advanced_when_upload_not_configured(self, tmp_path: Path, monkeypatch):
        """No upload -> the bundle is not off-host -> the sentinel must NOT
        claim it was successfully bundled."""
        repo = tmp_path / "repo"
        _make_repo(repo)
        bdir = tmp_path / "bundles"
        recipient, _ = _keypair(tmp_path)
        monkeypatch.setattr(
            ob, "load_env_var",
            lambda k, root=None: recipient if k == "BACKUP_AGE_RECIPIENT" else "",
        )
        rc = bb.run_bundle_backup(repo_root=repo, bundle_dir=bdir, ref="main")
        assert rc == 0
        assert bb.read_sentinel(bdir) is None
        status = json.loads((bdir / bb.BUNDLE_STATUS_FILENAME).read_text())
        assert status["status"] == "not_configured"


# ---------------------------------------------------------------------------
# 4 — fail-closed
# ---------------------------------------------------------------------------

class TestFailClosed:
    def test_encryption_failure_uploads_nothing_and_does_not_advance_sentinel(
        self, tmp_path: Path, monkeypatch
    ):
        repo = tmp_path / "repo"
        _make_repo(repo)
        bdir = tmp_path / "bundles"

        def _env(key, root=None):
            if key == "BACKUP_AGE_RECIPIENT":
                return "age1invalidrecipient"     # rejected by validate_recipient
            return "echo SHOULD-NOT-RUN {file} {manifest}"

        monkeypatch.setattr(ob, "load_env_var", _env)
        rc = bb.run_bundle_backup(repo_root=repo, bundle_dir=bdir, ref="main")
        assert rc == 1
        assert bb.read_sentinel(bdir) is None
        assert not list(bdir.glob("*.age"))
        status = json.loads((bdir / bb.BUNDLE_STATUS_FILENAME).read_text())
        assert status["status"] == "failed"

    def test_empty_recipient_refuses_rather_than_shipping_plaintext(
        self, tmp_path: Path, monkeypatch
    ):
        repo = tmp_path / "repo"
        _make_repo(repo)
        bdir = tmp_path / "bundles"

        def _env(key, root=None):
            if key == "BACKUP_AGE_RECIPIENT":
                return ""
            return "cp {file} {manifest} /tmp/"

        monkeypatch.setattr(ob, "load_env_var", _env)
        assert bb.run_bundle_backup(repo_root=repo, bundle_dir=bdir, ref="main") == 1
        assert not list(bdir.glob("*.age"))

    def test_failed_upload_does_not_advance_sentinel(self, tmp_path: Path, monkeypatch):
        repo = tmp_path / "repo"
        _make_repo(repo)
        bdir = tmp_path / "bundles"
        if AGE is None:
            pytest.skip("age binary not installed")
        recipient, _ = _keypair(tmp_path)

        def _env(key, root=None):
            if key == "BACKUP_AGE_RECIPIENT":
                return recipient
            return "exit 7"          # a failing "upload"

        monkeypatch.setattr(ob, "load_env_var", _env)
        assert bb.run_bundle_backup(repo_root=repo, bundle_dir=bdir, ref="main") == 1
        assert bb.read_sentinel(bdir) is None
        status = json.loads((bdir / bb.BUNDLE_STATUS_FILENAME).read_text())
        assert status["status"] == "failed" and status["exit_code"] == 7

    def test_dry_run_writes_nothing(self, tmp_path: Path):
        repo = tmp_path / "repo"
        _make_repo(repo)
        bdir = tmp_path / "bundles"
        assert bb.run_bundle_backup(
            repo_root=repo, bundle_dir=bdir, ref="main", dry_run=True) == 0
        assert not bdir.exists() or not list(bdir.iterdir())


# ---------------------------------------------------------------------------
# 5 — real crypto: the round-trip that actually proves recovery
# ---------------------------------------------------------------------------

@requires_age
class TestEncryptedRoundTrip:
    def test_full_recovery_path_including_ancestor_checkout(self, tmp_path: Path, monkeypatch):
        """The end-to-end claim: a clean host with only the ciphertext and the
        age identity recovers the exact code for an OLDER recorded git_sha."""
        repo = tmp_path / "repo"
        shas = _make_repo(repo, commits=4)
        bdir = tmp_path / "bundles"
        recipient, identity = _keypair(tmp_path)

        uploaded = tmp_path / "s3"
        uploaded.mkdir()

        def _env(key, root=None):
            if key == "BACKUP_AGE_RECIPIENT":
                return recipient
            return f'cp "{{file}}" "{{manifest}}" "{uploaded}/"'

        monkeypatch.setattr(ob, "load_env_var", _env)
        assert bb.run_bundle_backup(repo_root=repo, bundle_dir=bdir, ref="main") == 0
        assert bb.read_sentinel(bdir) == shas[-1]

        # --- what the recovery host has: ciphertext + manifest + identity ----
        ct = uploaded / f"btcbot-{shas[-1]}.bundle.age"
        mf = uploaded / f"btcbot-{shas[-1]}.bundle.age-manifest.json"
        assert ct.exists() and mf.exists()

        # No plaintext bundle was ever uploaded.
        assert not list(uploaded.glob("*.bundle"))

        # Ciphertext verification WITHOUT the key.
        manifest = ob.verify_ciphertext(ct, mf)
        assert manifest["recipient"] == recipient
        assert manifest["source_manifest"]["git_sha"] == shas[-1]

        # Decrypt with the stock age CLI only -- no repo code, no bootstrap
        # paradox. This is exactly the documented recovery step.
        recovered = tmp_path / "recovered.bundle"
        r = subprocess.run(
            [AGE, "--decrypt", "--identity", str(identity),
             "--output", str(recovered), str(ct)],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, r.stderr
        assert ob.sha256_file(recovered) == manifest["plaintext_sha256"]

        # Clone and check out an ANCESTOR sha -- the property that lets one
        # current bundle serve every retained ledger generation.
        clone = tmp_path / "clone"
        assert _git(["clone", "-q", "-b", "main", str(recovered), str(clone)],
                    tmp_path).returncode == 0
        assert _git(["checkout", "-q", shas[0]], clone).returncode == 0
        assert _git(["rev-parse", "HEAD"], clone).stdout.strip() == shas[0]
        assert (clone / "file0.txt").exists()

    def test_wrong_key_fails_closed_with_no_output(self, tmp_path: Path, monkeypatch):
        repo = tmp_path / "repo"
        shas = _make_repo(repo)
        bdir = tmp_path / "bundles"
        recipient, _ = _keypair(tmp_path)
        monkeypatch.setattr(
            ob, "load_env_var",
            lambda k, root=None: recipient if k == "BACKUP_AGE_RECIPIENT" else "",
        )
        bb.run_bundle_backup(repo_root=repo, bundle_dir=bdir, ref="main")
        ct = bdir / f"btcbot-{shas[-1]}.bundle.age"
        assert ct.exists()

        wrong_dir = tmp_path / "wrong"
        wrong_dir.mkdir()
        _, wrong_identity = _keypair(wrong_dir)
        out = tmp_path / "should_not_exist.bundle"
        r = subprocess.run(
            [AGE, "--decrypt", "--identity", str(wrong_identity),
             "--output", str(out), str(ct)],
            capture_output=True, text=True,
        )
        assert r.returncode != 0
        assert not out.exists() or out.stat().st_size == 0

    def test_corrupt_ciphertext_detected_before_decryption(self, tmp_path: Path, monkeypatch):
        repo = tmp_path / "repo"
        shas = _make_repo(repo)
        bdir = tmp_path / "bundles"
        recipient, _ = _keypair(tmp_path)
        monkeypatch.setattr(
            ob, "load_env_var",
            lambda k, root=None: recipient if k == "BACKUP_AGE_RECIPIENT" else "",
        )
        bb.run_bundle_backup(repo_root=repo, bundle_dir=bdir, ref="main")
        ct = bdir / f"btcbot-{shas[-1]}.bundle.age"
        data = bytearray(ct.read_bytes())
        data[-1] ^= 0xFF
        ct.write_bytes(bytes(data))
        with pytest.raises(ValueError, match="CIPHERTEXT SHA-256 MISMATCH"):
            ob.verify_ciphertext(ct)

    def test_no_plaintext_bundle_is_ever_handed_to_the_upload_command(
        self, tmp_path: Path, monkeypatch
    ):
        """The upload template must receive the ciphertext, never the bundle --
        the bundle-side analogue of
        test_offhost_cmd_receives_ciphertext_not_plaintext."""
        repo = tmp_path / "repo"
        _make_repo(repo)
        bdir = tmp_path / "bundles"
        recipient, _ = _keypair(tmp_path)
        seen = tmp_path / "seen.txt"

        def _env(key, root=None):
            if key == "BACKUP_AGE_RECIPIENT":
                return recipient
            return f'echo "{{file}}" > "{seen}"'

        monkeypatch.setattr(ob, "load_env_var", _env)
        bb.run_bundle_backup(repo_root=repo, bundle_dir=bdir, ref="main")
        handed = seen.read_text(encoding="utf-8").strip().strip('"')
        assert handed.endswith(".age")
        assert not handed.endswith(".bundle")


# ---------------------------------------------------------------------------
# 6 — healthcheck integration
# ---------------------------------------------------------------------------

class TestHealthcheckCodeBundle:
    def test_missing_sidecar_is_info_not_noise(self, tmp_path: Path):
        r = hc.check_code_bundle(tmp_path, ROOT)
        assert r.severity == hc.INFO

    def test_not_configured_is_info(self, tmp_path: Path):
        (tmp_path / hc.BUNDLE_STATUS_FILENAME).write_text(
            json.dumps({"configured": False}), encoding="utf-8")
        assert hc.check_code_bundle(tmp_path, ROOT).severity == hc.INFO

    def test_unreadable_sidecar_is_warning(self, tmp_path: Path):
        (tmp_path / hc.BUNDLE_STATUS_FILENAME).write_text("{not json", encoding="utf-8")
        assert hc.check_code_bundle(tmp_path, ROOT).severity == hc.WARNING

    def test_configured_but_never_uploaded_is_warning(self, tmp_path: Path):
        (tmp_path / hc.BUNDLE_STATUS_FILENAME).write_text(
            json.dumps({"configured": True, "last_success_sha": None}),
            encoding="utf-8")
        r = hc.check_code_bundle(tmp_path, ROOT)
        assert r.severity == hc.WARNING
        assert "never been uploaded" in r.message

    def test_head_ahead_of_bundle_is_warning(self, tmp_path: Path):
        repo = tmp_path / "repo"
        shas = _make_repo(repo, commits=2)
        (tmp_path / hc.BUNDLE_STATUS_FILENAME).write_text(
            json.dumps({"configured": True, "last_success_sha": shas[0]}),
            encoding="utf-8")
        r = hc.check_code_bundle(tmp_path, repo)
        assert r.severity == hc.WARNING
        assert "stale" in r.message

    def test_in_sync_is_ok(self, tmp_path: Path):
        repo = tmp_path / "repo"
        shas = _make_repo(repo)
        (tmp_path / hc.BUNDLE_STATUS_FILENAME).write_text(
            json.dumps({"configured": True, "last_success_sha": shas[-1]}),
            encoding="utf-8")
        r = hc.check_code_bundle(tmp_path, repo)
        assert r.severity == hc.OK
        assert "in sync" in r.message

    def test_artifact_hash_mismatch_is_critical(self, tmp_path: Path):
        repo = tmp_path / "repo"
        shas = _make_repo(repo)
        artifact = tmp_path / "btcbot-x.bundle.age"
        artifact.write_bytes(b"real content")
        (tmp_path / hc.BUNDLE_STATUS_FILENAME).write_text(
            json.dumps({
                "configured": True,
                "last_success_sha": shas[-1],
                "artifact": artifact.name,
                "artifact_sha256": "0" * 64,      # deliberately wrong
            }), encoding="utf-8")
        r = hc.check_code_bundle(tmp_path, repo)
        assert r.severity == hc.CRITICAL
        assert "hash mismatch" in r.message

    def test_check_makes_no_network_call(self):
        """Read-only against S3 means: no S3 call at all. Asserted against the
        source rather than by mocking, so it cannot regress silently."""
        src = (SCRIPTS / "healthcheck.py").read_text(encoding="utf-8")
        start = src.index("def check_code_bundle")
        end = src.index("def check_offhost_freshness")
        body = src[start:end]
        for forbidden in ("boto3", "aws ", "s3://", "urllib", "requests", "http"):
            assert forbidden not in body, f"{forbidden!r} present in check_code_bundle"
