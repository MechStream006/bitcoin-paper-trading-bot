"""BTC Paper Trading Bot — encrypted off-host backup (ADR-0001 §12 BK3).

Encrypts a backup artifact on the production host, before it leaves, using
`age` with an **asymmetric recipient key**. Provides the matching decrypt +
verify path for disaster recovery.

Why asymmetric, not a passphrase
--------------------------------
The host holds only the *public* recipient key (`age1...`). It can encrypt;
it cannot decrypt. The private identity (`AGE-SECRET-KEY-1...`) is never
present on the production host at all.

This is the whole point. A symmetric passphrase stored in `/opt/btcbot/.env`
would mean that whoever compromises the host also gets every backup — which
is precisely the scenario off-host backups exist to survive. With an
asymmetric recipient, host compromise yields ciphertext and an encrypt-only
key, and nothing else.

Integrity model (three independent checks)
------------------------------------------
Encryption must not weaken BK4 ("integrity is verified via the SHA-256
manifest, not assumed from exit code 0"). It doesn't — it adds a layer:

  1. `plaintext_sha256`  — from the existing backup manifest. Verified AFTER
     decryption. Proves the recovered DB is byte-identical to what the host
     backed up, end-to-end across encryption + transport + storage.
  2. `ciphertext_sha256` — of the `.age` file. Verified BEFORE decryption.
     Detects transport/storage corruption **without needing the private
     key**, so the off-host copy can be audited routinely from any machine.
  3. `PRAGMA integrity_check` — on the decrypted DB (the existing
     `restore.py` path also does this).

A `.age-manifest.json` sidecar carries both hashes plus the original manifest
embedded verbatim. It is deliberately **cleartext**: it holds hashes, row
counts and timestamps — no secrets — and DR needs to read it before it has
anything decrypted. Shipping it encrypted would create a bootstrap problem
with no security benefit.

Exit codes
----------
    0  success
    1  failure (nothing partial is left behind)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

AGE_MANIFEST_SUFFIX = ".age-manifest.json"
CIPHERTEXT_SUFFIX = ".age"

EXIT_OK = 0
EXIT_ERROR = 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_env_var(key: str, root: Path | None = None) -> str:
    """Read a single var from the environment, falling back to `.env`.

    Mirrors `backup.py::_load_env_var` deliberately rather than importing it:
    `backup.py` is not a package module and this script must remain runnable
    standalone during disaster recovery, when the repo layout may be a fresh
    clone and nothing else is guaranteed to import cleanly.
    """
    val = os.environ.get(key, "")
    if val:
        return val.strip()
    env_file = (root or ROOT) / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(key + "="):
                return line[len(key) + 1:].strip()
    return ""


def find_age_binary(explicit: str | None = None) -> str:
    """Locate the `age` executable, or raise with an actionable message."""
    candidate = explicit or os.environ.get("AGE_BINARY") or "age"
    resolved = shutil.which(candidate)
    if not resolved:
        raise FileNotFoundError(
            f"'{candidate}' not found on PATH. Install it:\n"
            "  Ubuntu/Debian : sudo apt-get install -y age\n"
            "  macOS         : brew install age\n"
            "  Manual        : https://github.com/FiloSottile/age/releases\n"
            "Or set AGE_BINARY=/path/to/age."
        )
    return resolved


def validate_recipient(recipient: str) -> str:
    """Reject anything that is not an age X25519 public recipient.

    Fail-closed and specific. Two mistakes this catches:

    - An empty value (unset `BACKUP_AGE_RECIPIENT`), which must never silently
      degrade to "ship it in the clear".
    - An `AGE-SECRET-KEY-1...` private identity pasted where the public
      recipient belongs. That would put the private key in `.env` on the
      production host, destroying the entire threat model — the one mistake
      most worth making impossible.
    """
    r = (recipient or "").strip()
    if not r:
        raise ValueError(
            "BACKUP_AGE_RECIPIENT is not set. Off-host encryption is "
            "fail-closed: refusing to produce an unencrypted off-host "
            "artifact. See docs/ops/offhost-backup.md §3 to generate a key."
        )
    if r.upper().startswith("AGE-SECRET-KEY-"):
        raise ValueError(
            "BACKUP_AGE_RECIPIENT contains a PRIVATE identity key "
            "(AGE-SECRET-KEY-...), not a public recipient (age1...).\n"
            "The private key must NEVER be present on the production host — "
            "that is the entire point of the asymmetric design. Rotate this "
            "key immediately: it must be considered compromised."
        )
    if not r.startswith("age1"):
        raise ValueError(
            f"BACKUP_AGE_RECIPIENT does not look like an age recipient "
            f"(expected 'age1...', got '{r[:12]}...')."
        )
    return r


def run_age_encrypt(age_bin: str, recipient: str, src: Path, dst: Path) -> None:
    """Encrypt `src` -> `dst`. Raises RuntimeError on failure."""
    proc = subprocess.run(
        [age_bin, "--recipient", recipient, "--output", str(dst), str(src)],
        capture_output=True, text=True, timeout=3600,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"age encrypt failed (exit {proc.returncode}): {proc.stderr.strip()}")


def run_age_decrypt(age_bin: str, identity: Path, src: Path, dst: Path) -> None:
    """Decrypt `src` -> `dst` using an identity FILE.

    The identity is passed as `--identity <file>`, never as a command-line
    value: process arguments are world-readable via /proc on Linux, so a key
    passed inline would be exposed to every local user for the lifetime of
    the process.
    """
    proc = subprocess.run(
        [age_bin, "--decrypt", "--identity", str(identity),
         "--output", str(dst), str(src)],
        capture_output=True, text=True, timeout=3600,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"age decrypt failed (exit {proc.returncode}): {proc.stderr.strip()}")


def build_age_manifest(
    plaintext: Path,
    ciphertext: Path,
    recipient: str,
    source_manifest: dict | None,
    now: datetime | None = None,
) -> dict:
    ts = (now or datetime.now(timezone.utc)).isoformat()
    return {
        "schema": "btcbot-age-manifest/1",
        "encrypted_at": ts,
        "recipient": recipient,
        "cipher": "age/X25519",
        "plaintext_name": plaintext.name,
        "plaintext_sha256": sha256_file(plaintext),
        "plaintext_size_bytes": plaintext.stat().st_size,
        "ciphertext_name": ciphertext.name,
        "ciphertext_sha256": sha256_file(ciphertext),
        "ciphertext_size_bytes": ciphertext.stat().st_size,
        # The original backup manifest verbatim: row counts, ts_range, git_sha.
        # Embedding it means the off-host copy is self-describing -- DR does not
        # need the host (which by assumption is gone) to know what this file is.
        "source_manifest": source_manifest,
    }


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

def encrypt_backup(
    backup_file: Path,
    recipient: str,
    age_bin: str | None = None,
    out_dir: Path | None = None,
) -> tuple[Path, Path]:
    """Encrypt one backup artifact. Returns (ciphertext, age_manifest)."""
    if not backup_file.exists():
        raise FileNotFoundError(f"backup file not found: {backup_file}")

    recipient = validate_recipient(recipient)
    age_bin = age_bin or find_age_binary()

    out_dir = out_dir or backup_file.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    ciphertext = out_dir / (backup_file.name + CIPHERTEXT_SUFFIX)
    age_manifest_path = out_dir / (backup_file.name + AGE_MANIFEST_SUFFIX)

    src_manifest_path = backup_file.parent / (backup_file.stem + ".manifest.json")
    source_manifest = None
    if src_manifest_path.exists():
        source_manifest = json.loads(src_manifest_path.read_text(encoding="utf-8"))

    # Encrypt to a temp name, then atomically rename. A crash mid-encrypt must
    # not leave a truncated .age file that later looks like a valid backup --
    # a silently-corrupt backup is worse than an obviously-missing one.
    tmp = out_dir / (backup_file.name + CIPHERTEXT_SUFFIX + ".partial")
    try:
        run_age_encrypt(age_bin, recipient, backup_file, tmp)
        tmp.replace(ciphertext)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)

    manifest = build_age_manifest(backup_file, ciphertext, recipient, source_manifest)
    age_manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return ciphertext, age_manifest_path


def verify_ciphertext(ciphertext: Path, age_manifest_path: Path | None = None) -> dict:
    """Verify a `.age` artifact against its manifest WITHOUT the private key.

    This is what makes routine off-host auditing possible: the backup target
    can be checked for bit-rot or truncation from any machine, by anyone,
    without ever handling the decryption key.
    """
    if not ciphertext.exists():
        raise FileNotFoundError(f"ciphertext not found: {ciphertext}")
    age_manifest_path = age_manifest_path or Path(str(ciphertext).replace(
        CIPHERTEXT_SUFFIX, "") + AGE_MANIFEST_SUFFIX)
    if not age_manifest_path.exists():
        raise FileNotFoundError(f"age manifest not found: {age_manifest_path}")

    manifest = json.loads(age_manifest_path.read_text(encoding="utf-8"))
    expected = manifest.get("ciphertext_sha256", "")
    actual = sha256_file(ciphertext)
    if not expected:
        raise ValueError(f"{age_manifest_path.name} has no ciphertext_sha256")
    if actual != expected:
        raise ValueError(
            f"CIPHERTEXT SHA-256 MISMATCH for {ciphertext.name}\n"
            f"  expected : {expected}\n"
            f"  actual   : {actual}\n"
            "The off-host copy is corrupt or was modified in transit."
        )
    return manifest


def decrypt_backup(
    ciphertext: Path,
    identity: Path,
    out_path: Path,
    age_bin: str | None = None,
    age_manifest_path: Path | None = None,
    skip_integrity: bool = False,
) -> dict:
    """Verify + decrypt + verify + integrity-check. Returns the age manifest."""
    if not identity.exists():
        raise FileNotFoundError(
            f"identity file not found: {identity}\n"
            "This is the private key held OFF the production host. See "
            "docs/ops/offhost-backup.md §6 (disaster recovery)."
        )
    age_bin = age_bin or find_age_binary()

    # 1. Ciphertext integrity BEFORE spending time on decryption.
    manifest = verify_ciphertext(ciphertext, age_manifest_path)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.parent / (out_path.name + ".partial")
    try:
        run_age_decrypt(age_bin, identity, ciphertext, tmp)

        # 2. Plaintext integrity: end-to-end proof across encrypt+transport.
        expected = manifest.get("plaintext_sha256", "")
        actual = sha256_file(tmp)
        if expected and actual != expected:
            raise ValueError(
                f"PLAINTEXT SHA-256 MISMATCH after decryption\n"
                f"  expected : {expected}\n"
                f"  actual   : {actual}"
            )

        # 3. The DB actually opens and is structurally sound.
        if not skip_integrity:
            conn = sqlite3.connect(str(tmp))
            try:
                result = conn.execute("PRAGMA integrity_check").fetchall()
            finally:
                conn.close()
            if result != [("ok",)]:
                raise ValueError(f"decrypted DB failed integrity_check: {result}")

        tmp.replace(out_path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)

    return manifest


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_encrypt(args) -> int:
    recipient = args.recipient or load_env_var("BACKUP_AGE_RECIPIENT")
    try:
        ct, mf = encrypt_backup(args.backup_file, recipient,
                                age_bin=args.age_binary, out_dir=args.out_dir)
    except Exception as exc:
        print(f"[offhost] ERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR
    size_kb = ct.stat().st_size // 1024
    print(f"[offhost] encrypted -> {ct.name} ({size_kb} KB)")
    print(f"[offhost] manifest  -> {mf.name}")
    return EXIT_OK


def _cmd_verify(args) -> int:
    try:
        manifest = verify_ciphertext(args.ciphertext, args.manifest)
    except Exception as exc:
        print(f"[offhost] ERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR
    print(f"[offhost] ciphertext OK  {args.ciphertext.name}")
    print(f"[offhost]   encrypted_at : {manifest.get('encrypted_at')}")
    print(f"[offhost]   recipient    : {manifest.get('recipient')}")
    src = manifest.get("source_manifest") or {}
    if src.get("row_counts"):
        print(f"[offhost]   row_counts   : {src['row_counts']}")
    print("[offhost] NOTE: this proves the ciphertext is intact. It does NOT "
          "prove it is decryptable — only a real decrypt drill proves that.")
    return EXIT_OK


def _cmd_decrypt(args) -> int:
    try:
        manifest = decrypt_backup(
            args.ciphertext, args.identity, args.out,
            age_bin=args.age_binary, age_manifest_path=args.manifest,
            skip_integrity=args.skip_integrity,
        )
    except Exception as exc:
        print(f"[offhost] ERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR
    print(f"[offhost] decrypted -> {args.out}")
    print("[offhost] verified: ciphertext sha256, plaintext sha256, integrity_check")
    src = manifest.get("source_manifest") or {}
    if src.get("row_counts"):
        print(f"[offhost] row_counts (expected): {src['row_counts']}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Encrypted off-host backup (ADR-0001 BK3)",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_enc = sub.add_parser("encrypt", help="Encrypt a backup artifact for off-host transfer")
    p_enc.add_argument("backup_file", type=Path)
    p_enc.add_argument("--recipient", default=None,
                       help="age recipient (default: BACKUP_AGE_RECIPIENT from env/.env)")
    p_enc.add_argument("--out-dir", type=Path, default=None)
    p_enc.add_argument("--age-binary", default=None)
    p_enc.set_defaults(func=_cmd_encrypt)

    p_ver = sub.add_parser("verify", help="Verify a .age artifact WITHOUT the private key")
    p_ver.add_argument("ciphertext", type=Path)
    p_ver.add_argument("--manifest", type=Path, default=None)
    p_ver.set_defaults(func=_cmd_verify)

    p_dec = sub.add_parser("decrypt", help="Decrypt + verify (disaster recovery)")
    p_dec.add_argument("ciphertext", type=Path)
    p_dec.add_argument("--identity", type=Path, required=True,
                       help="Path to the age identity (private key) file")
    p_dec.add_argument("--out", type=Path, required=True)
    p_dec.add_argument("--manifest", type=Path, default=None)
    p_dec.add_argument("--age-binary", default=None)
    p_dec.add_argument("--skip-integrity", action="store_true",
                       help="Skip PRAGMA integrity_check (non-SQLite payloads)")
    p_dec.set_defaults(func=_cmd_decrypt)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
