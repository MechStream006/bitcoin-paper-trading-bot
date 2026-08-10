"""Tests for scripts/offhost_backup.py — encrypted off-host backup (BK3).

Two tiers:

  * Logic tests (always run): recipient validation, manifest construction,
    corruption detection, fail-closed behaviour in backup.py.
  * Real-crypto tests (skipped when `age` is absent): a genuine
    encrypt -> transport -> verify -> decrypt round-trip against a real
    SQLite DB, proving the recovered bytes are identical and the DB opens.

The real-crypto tier matters disproportionately here. Every other control in
this repo fails loudly; an encryption control can fail *silently* by producing
artifacts that look fine and cannot actually be decrypted. Only an end-to-end
decrypt proves otherwise, which is why BK2's drill discipline is extended to
key material in docs/ops/offhost-backup.md.

Run:
    conda run -n btc_bot pytest tests/test_offhost_backup.py -v
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod          # so backup.py's `import offhost_backup` resolves
    spec.loader.exec_module(mod)
    return mod


ob = _load("offhost_backup")
backup = _load("backup")

AGE = shutil.which("age")
requires_age = pytest.mark.skipif(AGE is None, reason="age binary not installed")


def _make_db(path: Path, rows: int = 50) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE governance_receipts (id INTEGER PRIMARY KEY, payload TEXT)")
    for i in range(rows):
        conn.execute("INSERT INTO governance_receipts VALUES (?,?)", (i, f"receipt-{i}"))
    conn.commit()
    conn.close()
    return path


def _keypair(tmp: Path) -> tuple[str, Path]:
    """Generate a real age keypair. Returns (recipient, identity_file)."""
    identity = tmp / "key.txt"
    r = subprocess.run(["age-keygen", "-o", str(identity)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    recipient = ""
    for line in identity.read_text().splitlines():
        if line.startswith("# public key:"):
            recipient = line.split(":", 1)[1].strip()
    assert recipient.startswith("age1")
    return recipient, identity


# ---------------------------------------------------------------------------
# Recipient validation — the single most security-critical function here
# ---------------------------------------------------------------------------

class TestRecipientValidation:
    def test_valid_recipient_passes(self):
        r = "age1ql3z7hjy54pw3hyww5ayyfg7zqgvc7w3j2elw8zmrj2kg5sfn9aqmcac8p"
        assert ob.validate_recipient(r) == r

    def test_empty_recipient_is_fail_closed(self):
        """Unset must never silently degrade to shipping plaintext."""
        with pytest.raises(ValueError, match="not set"):
            ob.validate_recipient("")

    def test_private_key_pasted_as_recipient_is_rejected(self):
        """The one mistake most worth making impossible.

        Pasting AGE-SECRET-KEY-... into BACKUP_AGE_RECIPIENT would put the
        private key on the production host in .env, destroying the entire
        threat model -- and it would otherwise 'work', since age accepts an
        identity where a recipient is expected in some flows.
        """
        with pytest.raises(ValueError, match="PRIVATE identity"):
            ob.validate_recipient("AGE-SECRET-KEY-1QQPQZRFR8XZ3EXAMPLE")

    def test_private_key_rejected_case_insensitively(self):
        with pytest.raises(ValueError, match="PRIVATE identity"):
            ob.validate_recipient("age-secret-key-1qqpqzrfr8xz3example")

    def test_garbage_recipient_rejected(self):
        with pytest.raises(ValueError, match="does not look like"):
            ob.validate_recipient("ssh-rsa AAAAB3Nza")

    def test_whitespace_is_stripped(self):
        r = "age1ql3z7hjy54pw3hyww5ayyfg7zqgvc7w3j2elw8zmrj2kg5sfn9aqmcac8p"
        assert ob.validate_recipient(f"  {r}  ") == r


# ---------------------------------------------------------------------------
# Real crypto round-trip
# ---------------------------------------------------------------------------

@requires_age
class TestRoundTrip:
    def test_encrypt_then_decrypt_is_byte_identical(self, tmp_path):
        db = _make_db(tmp_path / "ledger.db")
        original = ob.sha256_file(db)
        recipient, identity = _keypair(tmp_path)

        ct, mf = ob.encrypt_backup(db, recipient)
        assert ct.exists() and mf.exists()

        out = tmp_path / "recovered.db"
        ob.decrypt_backup(ct, identity, out)
        assert ob.sha256_file(out) == original

    def test_ciphertext_is_not_plaintext(self, tmp_path):
        """Guards against a misconfiguration that copies instead of encrypting."""
        db = _make_db(tmp_path / "ledger.db")
        recipient, _ = _keypair(tmp_path)
        ct, _ = ob.encrypt_backup(db, recipient)

        raw = ct.read_bytes()
        assert raw[:16] != db.read_bytes()[:16]
        assert b"SQLite format 3" not in raw
        assert b"receipt-1" not in raw          # row content must not survive
        assert raw.startswith(b"age-encryption.org/")

    def test_recovered_db_is_queryable(self, tmp_path):
        db = _make_db(tmp_path / "ledger.db", rows=25)
        recipient, identity = _keypair(tmp_path)
        ct, _ = ob.encrypt_backup(db, recipient)
        out = tmp_path / "recovered.db"
        ob.decrypt_backup(ct, identity, out)

        conn = sqlite3.connect(str(out))
        assert conn.execute("SELECT COUNT(*) FROM governance_receipts").fetchone()[0] == 25
        assert conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        conn.close()

    def test_wrong_key_cannot_decrypt(self, tmp_path):
        """The security claim itself: a different identity must fail."""
        db = _make_db(tmp_path / "ledger.db")
        recipient, _ = _keypair(tmp_path)
        ct, _ = ob.encrypt_backup(db, recipient)

        other = tmp_path / "other"
        other.mkdir()
        _, wrong_identity = _keypair(other)

        with pytest.raises(RuntimeError, match="decrypt failed"):
            ob.decrypt_backup(ct, wrong_identity, tmp_path / "nope.db")
        assert not (tmp_path / "nope.db").exists()

    def test_embedded_source_manifest_survives(self, tmp_path):
        """Off-host copy must be self-describing — the host is gone in DR."""
        bdir = tmp_path / "backups"
        _make_db(tmp_path / "src.db", rows=7)
        backup.run_backup(tmp_path / "src.db", bdir, dry_run=False, db_label="ledger")
        bfile = sorted(bdir.glob("ledger_*.db"))[-1]

        recipient, _ = _keypair(tmp_path)
        _, mf = ob.encrypt_backup(bfile, recipient)
        m = json.loads(mf.read_text())
        assert m["source_manifest"]["row_counts"]["governance_receipts"] == 7
        assert m["source_manifest"]["sha256"] == m["plaintext_sha256"]


# ---------------------------------------------------------------------------
# Integrity verification
# ---------------------------------------------------------------------------

@requires_age
class TestIntegrity:
    def test_verify_detects_ciphertext_corruption_without_key(self, tmp_path):
        """Routine off-host auditing must work with no key present."""
        db = _make_db(tmp_path / "ledger.db")
        recipient, _ = _keypair(tmp_path)
        ct, mf = ob.encrypt_backup(db, recipient)

        raw = bytearray(ct.read_bytes())
        raw[-1] ^= 0xFF
        ct.write_bytes(bytes(raw))

        with pytest.raises(ValueError, match="CIPHERTEXT SHA-256 MISMATCH"):
            ob.verify_ciphertext(ct, mf)

    def test_verify_passes_on_intact_ciphertext(self, tmp_path):
        db = _make_db(tmp_path / "ledger.db")
        recipient, _ = _keypair(tmp_path)
        ct, mf = ob.encrypt_backup(db, recipient)
        assert ob.verify_ciphertext(ct, mf)["cipher"] == "age/X25519"

    def test_decrypt_refuses_corrupt_ciphertext_before_using_key(self, tmp_path):
        db = _make_db(tmp_path / "ledger.db")
        recipient, identity = _keypair(tmp_path)
        ct, _ = ob.encrypt_backup(db, recipient)

        raw = bytearray(ct.read_bytes())
        raw[len(raw) // 2] ^= 0xFF
        ct.write_bytes(bytes(raw))

        with pytest.raises(ValueError, match="CIPHERTEXT SHA-256 MISMATCH"):
            ob.decrypt_backup(ct, identity, tmp_path / "out.db")

    def test_no_partial_output_on_failure(self, tmp_path):
        """A truncated .db that looks restorable is worse than none at all."""
        db = _make_db(tmp_path / "ledger.db")
        recipient, _ = _keypair(tmp_path)
        ct, _ = ob.encrypt_backup(db, recipient)
        other = tmp_path / "o"; other.mkdir()
        _, wrong = _keypair(other)

        out = tmp_path / "out.db"
        with pytest.raises(RuntimeError):
            ob.decrypt_backup(ct, wrong, out)
        assert not out.exists()
        assert not (tmp_path / "out.db.partial").exists()

    def test_missing_identity_raises_before_touching_ciphertext(self, tmp_path):
        db = _make_db(tmp_path / "ledger.db")
        recipient, _ = _keypair(tmp_path)
        ct, _ = ob.encrypt_backup(db, recipient)
        with pytest.raises(FileNotFoundError, match="identity file not found"):
            ob.decrypt_backup(ct, tmp_path / "absent.txt", tmp_path / "out.db")


# ---------------------------------------------------------------------------
# Manifest contents
# ---------------------------------------------------------------------------

@requires_age
class TestManifest:
    def test_manifest_records_both_hashes(self, tmp_path):
        db = _make_db(tmp_path / "ledger.db")
        recipient, _ = _keypair(tmp_path)
        ct, mf = ob.encrypt_backup(db, recipient)
        m = json.loads(mf.read_text())
        assert m["plaintext_sha256"] == ob.sha256_file(db)
        assert m["ciphertext_sha256"] == ob.sha256_file(ct)
        assert m["plaintext_sha256"] != m["ciphertext_sha256"]

    def test_manifest_contains_no_private_key_material(self, tmp_path):
        """The manifest is shipped in cleartext — it must never carry secrets."""
        db = _make_db(tmp_path / "ledger.db")
        recipient, identity = _keypair(tmp_path)
        _, mf = ob.encrypt_backup(db, recipient)
        text = mf.read_text()
        assert "AGE-SECRET-KEY" not in text.upper()
        assert identity.read_text().strip().splitlines()[-1] not in text

    def test_manifest_records_recipient_for_key_rotation_audit(self, tmp_path):
        db = _make_db(tmp_path / "ledger.db")
        recipient, _ = _keypair(tmp_path)
        _, mf = ob.encrypt_backup(db, recipient)
        assert json.loads(mf.read_text())["recipient"] == recipient


# ---------------------------------------------------------------------------
# backup.py integration — fail-closed is the property that matters
# ---------------------------------------------------------------------------

class TestBackupIntegration:
    def test_encrypt_defaults_off_so_existing_behaviour_is_unchanged(self, tmp_path):
        """Regression: --encrypt must be opt-in. A deploy of this code must not
        change what the existing btcbot-backup.service does."""
        _make_db(tmp_path / "src.db")
        bdir = tmp_path / "b"
        assert backup.run_backup(tmp_path / "src.db", bdir, dry_run=False,
                                 db_label="ledger") == 0
        assert list(bdir.glob("*.age")) == []

    def test_missing_recipient_fails_closed_and_skips_transfer(self, tmp_path, monkeypatch, capsys):
        """The critical safety property: encryption failure must NOT fall back
        to shipping plaintext off-host."""
        _make_db(tmp_path / "src.db")
        bdir = tmp_path / "b"
        monkeypatch.setattr(backup, "_load_env_var",
                            lambda k: "" if k == "BACKUP_AGE_RECIPIENT" else "echo SHIPPED {file}")
        rc = backup.run_backup(tmp_path / "src.db", bdir, dry_run=False,
                               db_label="ledger", encrypt=True)
        err = capsys.readouterr()
        assert rc == 1
        assert "off-host transfer SKIPPED" in err.err
        assert "SHIPPED" not in err.out          # transfer must not have run
        assert list(bdir.glob("*.age")) == []

    def test_local_plaintext_backup_is_retained_after_encryption(self, tmp_path, monkeypatch):
        """BK3 governs what LEAVES the host; the local plaintext backup is the
        fast on-host restore path and must survive."""
        if AGE is None:
            pytest.skip("age binary not installed")
        _make_db(tmp_path / "src.db")
        bdir = tmp_path / "b"
        recipient, _ = _keypair(tmp_path)
        monkeypatch.setattr(backup, "_load_env_var",
                            lambda k: recipient if k == "BACKUP_AGE_RECIPIENT" else "")
        rc = backup.run_backup(tmp_path / "src.db", bdir, dry_run=False,
                               db_label="ledger", encrypt=True)
        assert rc == 0
        assert len(list(bdir.glob("ledger_*.db"))) == 1
        assert len(list(bdir.glob("ledger_*.db.age"))) == 1

    @requires_age
    def test_offhost_cmd_receives_ciphertext_not_plaintext(self, tmp_path, monkeypatch, capsys):
        """The whole point of BK3: plaintext must never be the thing shipped."""
        _make_db(tmp_path / "src.db")
        bdir = tmp_path / "b"
        recipient, _ = _keypair(tmp_path)
        env = {"BACKUP_AGE_RECIPIENT": recipient,
               "BACKUP_OFFHOST_CMD": "echo SHIPPING {file} {manifest}"}
        monkeypatch.setattr(backup, "_load_env_var", lambda k: env.get(k, ""))
        backup.run_backup(tmp_path / "src.db", bdir, dry_run=False,
                          db_label="ledger", encrypt=True)
        out = capsys.readouterr().out
        shipped = [l for l in out.splitlines() if "SHIPPING" in l]
        assert shipped, "off-host command did not run"
        assert ".age" in shipped[0]
        assert ".age-manifest.json" in shipped[0]


# ---------------------------------------------------------------------------
# Off-host status sidecar (P1 Increment 6 / BK3 monitoring gap)
# ---------------------------------------------------------------------------

class TestOffhostStatusSidecar:
    def test_not_configured_writes_sidecar_with_info_state(self, tmp_path, monkeypatch):
        """BACKUP_OFFHOST_CMD unset must be distinguishable from a failure."""
        _make_db(tmp_path / "src.db")
        bdir = tmp_path / "b"
        monkeypatch.setattr(backup, "_load_env_var", lambda k: "")
        rc = backup.run_backup(tmp_path / "src.db", bdir, dry_run=False, db_label="ledger")
        assert rc == 0
        payload = json.loads((bdir / backup.OFFHOST_STATUS_FILENAME).read_text())
        assert payload["configured"] is False
        assert payload["status"] == "not_configured"
        assert payload["last_success_at"] is None

    @requires_age
    def test_successful_transfer_writes_ok_status_and_hash(self, tmp_path, monkeypatch):
        """Off-host transfer only ever runs when encrypt=True -- exercise the
        real (encrypted) path, matching what actually happens in production."""
        _make_db(tmp_path / "src.db")
        bdir = tmp_path / "b"
        recipient, _ = _keypair(tmp_path)
        env = {"BACKUP_AGE_RECIPIENT": recipient, "BACKUP_OFFHOST_CMD": "echo OK {file}"}
        monkeypatch.setattr(backup, "_load_env_var", lambda k: env.get(k, ""))
        rc = backup.run_backup(tmp_path / "src.db", bdir, dry_run=False,
                               db_label="ledger", encrypt=True)
        assert rc == 0
        payload = json.loads((bdir / backup.OFFHOST_STATUS_FILENAME).read_text())
        assert payload["configured"] is True
        assert payload["status"] == "ok"
        assert payload["exit_code"] == 0
        assert payload["last_success_at"] == payload["attempted_at"]
        assert payload["artifact_sha256"] is not None

    def test_unencrypted_run_with_offhost_cmd_configured_does_not_transfer(
        self, tmp_path, monkeypatch, capsys,
    ):
        """The exact bug this fix closes: BACKUP_OFFHOST_CMD being set must
        never be sufficient on its own -- only encrypt=True may trigger a
        transfer. An unencrypted run (e.g. the real --all unit's ledger leg,
        which never passes --encrypt) must ship nothing off-host, ever, AND
        must not overwrite whatever a real encrypted run already recorded --
        otherwise a broken/missing encrypted timer would be silently reset
        to a harmless "not_configured" every day and its staleness would
        never escalate (the shared-sidecar blind spot)."""
        _make_db(tmp_path / "src.db")
        bdir = tmp_path / "b"
        bdir.mkdir()

        # Seed the sidecar with a real encrypted success, as the dedicated
        # ledger-encryption timer would already have written.
        from datetime import datetime, timezone
        backup._write_offhost_status(
            bdir, "ledger", datetime.now(timezone.utc), configured=True,
            encrypted=True, status="ok", exit_code=0, artifact=None,
        )
        before = json.loads((bdir / backup.OFFHOST_STATUS_FILENAME).read_text())

        monkeypatch.setattr(
            backup, "_load_env_var",
            lambda k: "echo SHOULD_NOT_RUN {file}" if k == "BACKUP_OFFHOST_CMD" else "",
        )
        rc = backup.run_backup(tmp_path / "src.db", bdir, dry_run=False,
                               db_label="ledger")  # encrypt defaults to False
        assert rc == 0
        out = capsys.readouterr().out
        assert "SHOULD_NOT_RUN" not in out          # no transfer attempted

        after = json.loads((bdir / backup.OFFHOST_STATUS_FILENAME).read_text())
        assert after == before                      # sidecar byte-for-byte untouched

    @requires_age
    def test_failed_transfer_writes_failed_status_but_run_backup_still_returns_0(
        self, tmp_path, monkeypatch,
    ):
        """Non-fatal off-host failure (existing behaviour) must now be
        visible via the sidecar instead of only a stderr line."""
        _make_db(tmp_path / "src.db")
        bdir = tmp_path / "b"
        recipient, _ = _keypair(tmp_path)
        env = {"BACKUP_AGE_RECIPIENT": recipient, "BACKUP_OFFHOST_CMD": "exit 1"}
        monkeypatch.setattr(backup, "_load_env_var", lambda k: env.get(k, ""))
        rc = backup.run_backup(tmp_path / "src.db", bdir, dry_run=False,
                               db_label="ledger", encrypt=True)
        assert rc == 0  # existing non-fatal behaviour preserved
        payload = json.loads((bdir / backup.OFFHOST_STATUS_FILENAME).read_text())
        assert payload["status"] == "failed"
        assert payload["exit_code"] != 0
        assert payload["last_success_at"] is None

    def test_last_success_at_carried_forward_across_a_later_failure(self, tmp_path):
        """A transfer that fails today must not erase yesterday's last known
        good transfer -- that timestamp is what staleness is measured from.

        Exercises `_write_offhost_status()` directly (rather than two full
        `run_backup()` calls) to avoid the unrelated `VACUUM INTO` same-second
        filename collision that two rapid real backups would hit -- that
        collision is a test-construction artifact of second-resolution backup
        filenames, not a sidecar defect.
        """
        from datetime import datetime, timedelta, timezone
        bdir = tmp_path / "b"
        bdir.mkdir()

        t1 = datetime(2026, 7, 24, 2, 0, 0, tzinfo=timezone.utc)
        backup._write_offhost_status(
            bdir, "ledger", t1, configured=True, encrypted=True,
            status="ok", exit_code=0, artifact=None,
        )
        first = json.loads((bdir / backup.OFFHOST_STATUS_FILENAME).read_text())
        assert first["status"] == "ok"
        good_ts = first["last_success_at"]
        assert good_ts == t1.isoformat()

        t2 = t1 + timedelta(days=1)
        backup._write_offhost_status(
            bdir, "ledger", t2, configured=True, encrypted=True,
            status="failed", exit_code=1, artifact=None,
        )
        second = json.loads((bdir / backup.OFFHOST_STATUS_FILENAME).read_text())
        assert second["status"] == "failed"
        assert second["last_success_at"] == good_ts   # preserved, not cleared

    @requires_age
    def test_sidecar_artifact_is_ciphertext_when_encrypted(self, tmp_path, monkeypatch):
        _make_db(tmp_path / "src.db")
        bdir = tmp_path / "b"
        recipient, _ = _keypair(tmp_path)
        env = {"BACKUP_AGE_RECIPIENT": recipient,
               "BACKUP_OFFHOST_CMD": "echo OK {file}"}
        monkeypatch.setattr(backup, "_load_env_var", lambda k: env.get(k, ""))
        backup.run_backup(tmp_path / "src.db", bdir, dry_run=False,
                          db_label="ledger", encrypt=True)
        payload = json.loads((bdir / backup.OFFHOST_STATUS_FILENAME).read_text())
        assert payload["artifact"].endswith(".db.age")
        assert payload["encrypted"] is True

    def test_dry_run_never_writes_sidecar(self, tmp_path, monkeypatch):
        _make_db(tmp_path / "src.db")
        bdir = tmp_path / "b"
        monkeypatch.setattr(
            backup, "_load_env_var",
            lambda k: "echo OK {file}" if k == "BACKUP_OFFHOST_CMD" else "",
        )
        backup.run_backup(tmp_path / "src.db", bdir, dry_run=True, db_label="ledger")
        assert not (bdir / backup.OFFHOST_STATUS_FILENAME).exists()
