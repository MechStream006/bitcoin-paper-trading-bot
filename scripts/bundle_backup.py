"""BTC Paper Trading Bot — encrypted recovery-code bundle (BK2 Phase 1).

Replicates the *code* needed to verify a recovered governance ledger into the
same off-host store as the ledger ciphertext, so disaster recovery needs two
credentials instead of three.

Why this exists
---------------
The 2026-08-12 BK2 drill (`docs/ops/bk2-offhost-drill-2026-08-12.md` §6) proved
off-host ledger recovery works, and exposed one undocumented dependency: the
documented procedure's `git clone <repo>` fails on a clean host because the
repository is private. `verify_chain()` and `report.py --check` — the code that
makes a recovered ledger *trustworthy* rather than merely present — live only
in that repo. Recovery therefore needed a THIRD credential (GitHub) that was
never designed, documented, or given a custody plan.

This module removes that dependency by shipping a full-history `git bundle`
alongside the ciphertext.

Design decisions (from the approved design audit)
-------------------------------------------------
1. **Full-history bundle, not a curated file set.** ~2.6 MB versus ~456 KB for
   a hand-picked set. The delta buys immunity from the failure mode that
   matters: a curated list silently rots the first time an import is added.
   A bundle also yields `git bundle verify` and exact SHA binding for free.

2. **The bundle is ENCRYPTED.** This is load-bearing, not hardening. Today
   `s3:GetObject` on the backup bucket yields ciphertext and a hashes-only
   manifest — worthless without the age identity. A *plaintext* bundle would
   turn one credential into a materially valuable one all by itself (full
   source, `registry.yaml`, `thresholds.yaml`). Encrypting to the SAME
   recipient keeps the blast radius exactly where it is and costs recovery
   nothing, since recovery already needs that identity for the ledger.

3. **No repo code is required to open it.** The bundle is decrypted with the
   stock `age` binary directly::

       age --decrypt --identity id.txt --output btcbot.bundle btcbot-<sha>.bundle.age

   That dissolves the bootstrap paradox (the decrypt tool living inside the
   thing being decrypted) without a second tool or a plaintext fallback.

4. **One current bundle serves every retained ledger generation.** Verified
   empirically: a bundle at `380fe20` cloned and checked out `48a3bc8` — the
   sha recorded in the live ledger manifests — with every recovery-critical
   file present. So the bundle is uploaded only when HEAD moves, never daily.
   **Constraint:** this holds only while history is append-only. A force-push
   or rebase on the bundled ref breaks the ancestor relationship and must
   trigger a fresh bundle.

5. **Upload only on SHA change.** The bucket applies `DefaultRetention:
   COMPLIANCE, 90 days` to every PUT, so an uploaded object cannot be deleted
   or overwritten for 90 days. Daily unconditional upload would accumulate 90
   undeletable versions per quarter for no benefit. The write-only production
   role cannot query S3 for existence (no ListBucket/HeadObject), so the
   "already bundled" decision is made from a host-side sentinel file.

Fail-closed everywhere
----------------------
Bundle creation, local verification, SHA binding, encryption and hashing must
all succeed before anything is uploaded. Any failure aborts with a non-zero
exit, leaves no partial artifact, and does NOT advance the sentinel. A bundle
that cannot be verified locally is never shipped — Object Lock makes an upload
irreversible for 90 days, so verification happens BEFORE the PUT, never after.

Environment
-----------
    BUNDLE_OFFHOST_CMD   Shell template with {file} and {manifest}
                         placeholders, exactly like BACKUP_OFFHOST_CMD.
                         Unset/empty = create and verify locally, upload
                         nothing. Deliberately SEPARATE from
                         BACKUP_OFFHOST_CMD so this can never perturb the
                         ledger backup leg.
    BACKUP_AGE_RECIPIENT Reused as-is. Same public recipient as the ledger.

Usage
-----
    python scripts/bundle_backup.py [--dry-run] [--force] [--ref main]

Exit codes
----------
    0  success (bundle created/uploaded, or skipped because SHA unchanged)
    1  failure (nothing partial left behind, sentinel not advanced)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# `import offhost_backup` must resolve whether this is run as a script from the
# repo root or imported by tests -- same shim backup.py already uses.
try:
    import offhost_backup
except ImportError:  # pragma: no cover - exercised only outside the test env
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import offhost_backup

BUNDLE_STATUS_FILENAME = ".bundle-status.json"
SENTINEL_FILENAME = ".last-bundled-sha"
S3_CODE_PREFIX = "code/"

DEFAULT_REF = "main"
DEFAULT_BUNDLE_DIR = ROOT / "data" / "code-backups"

_GIT_TIMEOUT = 300


class BundleError(RuntimeError):
    """Any failure that must abort before upload."""


# ---------------------------------------------------------------------------
# Git primitives
# ---------------------------------------------------------------------------

def _git(args: list[str], cwd: Path, timeout: int = _GIT_TIMEOUT) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", *args], cwd=str(cwd),
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        # A missing cwd, a missing git binary or a timeout must surface as a
        # BundleError like every other failure here, never as a raw OSError
        # that a caller might not be catching.
        raise BundleError(f"git {' '.join(args)} could not run: {exc}") from exc


def head_sha(repo_root: Path = ROOT, ref: str = "HEAD") -> str:
    """Full 40-character SHA of `ref`.

    Deliberately NOT `--short`. `backup.py::_git_sha()` records the 7-char form
    in every ledger manifest and is left untouched (changing it would alter
    existing manifest content for zero recovery benefit), but seven hex chars
    is weak provenance binding for an artifact whose whole job is to prove
    "this is the code that made that backup". The bundle records both: the full
    SHA as the authority, the short SHA to join against the ledger manifest.
    """
    r = _git(["rev-parse", ref], repo_root)
    if r.returncode != 0:
        raise BundleError(f"git rev-parse {ref} failed: {r.stderr.strip()}")
    sha = r.stdout.strip()
    if len(sha) != 40 or not all(c in "0123456789abcdef" for c in sha):
        raise BundleError(f"git rev-parse {ref} returned a non-SHA: {sha!r}")
    return sha


def create_bundle(out_path: Path, repo_root: Path = ROOT, ref: str = DEFAULT_REF) -> Path:
    """`git bundle create` for the full history of `ref`. Raises on failure."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.parent / (out_path.name + ".partial")
    tmp.unlink(missing_ok=True)
    try:
        r = _git(["bundle", "create", str(tmp), ref], repo_root)
        if r.returncode != 0:
            raise BundleError(f"git bundle create failed: {r.stderr.strip()}")
        if not tmp.exists() or tmp.stat().st_size == 0:
            raise BundleError("git bundle create produced no output")
        tmp.replace(out_path)
    finally:
        tmp.unlink(missing_ok=True)
    return out_path


def verify_bundle(bundle_path: Path, expected_sha: str, ref: str = DEFAULT_REF) -> dict:
    """Verify a bundle WITHOUT relying on the source repository.

    Two-stage, because one stage is not enough:

    1. ``git init`` a throwaway repo, then ``git bundle verify`` + ``list-heads``.
       `git bundle verify` refuses to run outside a repository
       (``error: need a repository to verify a bundle``) -- exactly the
       situation on a clean recovery host -- hence the scratch repo.
    2. A real ``git clone`` from the bundle.

    Stage 2 is not belt-and-braces. **`git bundle verify` validates the header,
    the refs and the prerequisite list -- it does NOT walk the packfile**, so a
    bundle whose pack is corrupted mid-stream passes stage 1 and only fails at
    clone time. Discovered while testing this function: 400 zeroed bytes in the
    middle of a bundle verified clean and then failed with
    ``fatal: pack has bad object``. Since Object Lock makes an upload
    irreversible for 90 days, the expensive check belongs BEFORE the PUT.

    Returns a detail dict. Raises BundleError if the bundle is not okay, is not
    a complete history, does not carry `expected_sha` at `ref`, or cannot be
    cloned.
    """
    bundle_path = bundle_path.resolve()
    if not bundle_path.exists():
        raise BundleError(f"bundle not found: {bundle_path}")

    with tempfile.TemporaryDirectory(prefix="bundle_verify_") as td:
        scratch = Path(td)
        init = _git(["init", "-q", "."], scratch)
        if init.returncode != 0:
            raise BundleError(f"git init failed: {init.stderr.strip()}")

        v = _git(["bundle", "verify", str(bundle_path)], scratch)
        if v.returncode != 0:
            raise BundleError(
                f"git bundle verify failed: {(v.stderr or v.stdout).strip()}"
            )
        out = (v.stdout or "") + (v.stderr or "")

        heads = _git(["bundle", "list-heads", str(bundle_path)], scratch)
        if heads.returncode != 0:
            raise BundleError(f"git bundle list-heads failed: {heads.stderr.strip()}")

        # Cheap structural checks first, so a wrong-SHA or incomplete bundle
        # fails fast with a precise message instead of after a full clone.
        if "complete history" not in out:
            raise BundleError(
                "bundle does not record a complete history -- refusing to ship "
                "a bundle that needs objects the recovery host will not have"
            )

        found = {}
        for line in heads.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                found[parts[1]] = parts[0]

        want_ref = ref if ref.startswith("refs/") else f"refs/heads/{ref}"
        actual = found.get(want_ref)
        if actual is None:
            raise BundleError(f"bundle has no {want_ref} (heads: {sorted(found)})")
        if actual != expected_sha:
            raise BundleError(
                f"bundle SHA mismatch for {want_ref}\n"
                f"  expected : {expected_sha}\n"
                f"  in bundle: {actual}"
            )

        # Stage 2: a real clone walks the packfile. See the docstring.
        short_ref = want_ref.rsplit("/", 1)[-1]
        clone_dir = scratch / "clone"
        c = _git(["clone", "-q", "-b", short_ref, str(bundle_path), str(clone_dir)],
                 scratch)
        if c.returncode != 0:
            raise BundleError(
                f"bundle failed to clone: {(c.stderr or c.stdout).strip()}"
            )
        cloned_head = _git(["rev-parse", "HEAD"], clone_dir)
        if cloned_head.returncode != 0 or cloned_head.stdout.strip() != expected_sha:
            raise BundleError(
                f"cloned HEAD does not match expected SHA\n"
                f"  expected: {expected_sha}\n"
                f"  cloned  : {cloned_head.stdout.strip()!r}"
            )

    return {"ref": want_ref, "sha": actual, "complete_history": True}


# ---------------------------------------------------------------------------
# Manifest + sentinel
# ---------------------------------------------------------------------------

def build_git_manifest(bundle_path: Path, full_sha: str, ref: str,
                       now: datetime | None = None) -> dict:
    """Git-provenance manifest for the bundle.

    Written as ``<stem>.manifest.json`` so that
    ``offhost_backup.encrypt_backup()`` picks it up and embeds it verbatim as
    ``source_manifest`` -- the same self-describing property the ledger's
    `.age-manifest.json` already has. Reusing that path means zero duplication
    of the encryption/manifest primitives.
    """
    ts = (now or datetime.now(timezone.utc)).isoformat()
    return {
        "schema": "btcbot-code-bundle/1",
        "bundle_ts": ts,
        "bundle_file": bundle_path.name,
        "file_size_bytes": bundle_path.stat().st_size,
        "sha256": offhost_backup.sha256_file(bundle_path),
        "git_sha": full_sha,
        "git_sha_short": full_sha[:7],
        "git_ref": ref,
        # The join key against a ledger backup manifest, whose own `git_sha`
        # is the 7-char form written by backup.py::_git_sha().
        "joins_ledger_manifest_on": "git_sha_short",
    }


def read_sentinel(bundle_dir: Path) -> str | None:
    f = bundle_dir / SENTINEL_FILENAME
    if not f.exists():
        return None
    try:
        val = f.read_text(encoding="utf-8").strip()
    except Exception:
        return None
    return val or None


def write_sentinel(bundle_dir: Path, sha: str) -> None:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / SENTINEL_FILENAME).write_text(sha + "\n", encoding="utf-8")


def write_bundle_status(bundle_dir: Path, *, head: str, status: str,
                        configured: bool, exit_code: int | None,
                        artifact: Path | None, bundled_sha: str | None,
                        now: datetime | None = None) -> None:
    """Persisted signal for `healthcheck.py`, mirroring `.offhost-status.json`.

    `last_success_at` / `last_success_sha` carry forward so a host that
    attempts and fails repeatedly shows growing staleness rather than a
    perpetually "just attempted" timestamp.
    """
    now = now or datetime.now(timezone.utc)
    path = bundle_dir / BUNDLE_STATUS_FILENAME
    last_success_at = None
    last_success_sha = None
    if path.exists():
        try:
            prior = json.loads(path.read_text(encoding="utf-8"))
            last_success_at = prior.get("last_success_at")
            last_success_sha = prior.get("last_success_sha")
        except Exception:
            pass
    if status == "ok":
        last_success_at = now.isoformat()
        last_success_sha = bundled_sha

    payload = {
        "schema": "btcbot-bundle-status/1",
        "attempted_at": now.isoformat(),
        "last_success_at": last_success_at,
        "last_success_sha": last_success_sha,
        "head_sha": head,
        "bundled_sha": bundled_sha,
        "configured": configured,
        "encrypted": True,
        "status": status,          # ok | failed | skipped_unchanged | not_configured
        "exit_code": exit_code,
        "artifact": artifact.name if artifact else None,
        "artifact_sha256": (
            offhost_backup.sha256_file(artifact)
            if artifact and artifact.exists() else None
        ),
    }
    bundle_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_bundle_backup(
    repo_root: Path = ROOT,
    bundle_dir: Path = DEFAULT_BUNDLE_DIR,
    ref: str = DEFAULT_REF,
    dry_run: bool = False,
    force: bool = False,
) -> int:
    try:
        head = head_sha(repo_root)
    except BundleError as exc:
        print(f"[bundle] ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"[bundle] repo    : {repo_root}")
    print(f"[bundle] ref/HEAD: {ref} @ {head}")

    sentinel = read_sentinel(bundle_dir)
    if sentinel == head and not force:
        print(f"[bundle] HEAD unchanged since last successful bundle ({head[:7]}) — skipping")
        if not dry_run:
            write_bundle_status(
                bundle_dir, head=head, status="skipped_unchanged",
                configured=bool(_offhost_cmd()), exit_code=None,
                artifact=None, bundled_sha=sentinel,
            )
        return 0

    bundle_name = f"btcbot-{head}.bundle"
    bundle_path = bundle_dir / bundle_name

    if dry_run:
        print(f"[DRY RUN] would create  {bundle_path}")
        print(f"[DRY RUN] would encrypt {bundle_name}.age")
        print(f"[DRY RUN] would upload  {S3_CODE_PREFIX}{bundle_name}.age (+ manifest)")
        return 0

    # --- create + verify BEFORE any encryption or upload ---------------------
    try:
        create_bundle(bundle_path, repo_root, ref)
        size = bundle_path.stat().st_size
        print(f"[bundle] created : {bundle_name} ({size // 1024} KB)")

        detail = verify_bundle(bundle_path, head, ref)
        print(f"[bundle] verified: {detail['ref']} @ {detail['sha'][:7]}, complete history")
    except BundleError as exc:
        print(f"[bundle] ERROR: {exc}", file=sys.stderr)
        print("[bundle] refusing to encrypt or upload an unverified bundle", file=sys.stderr)
        bundle_path.unlink(missing_ok=True)
        write_bundle_status(
            bundle_dir, head=head, status="failed", configured=bool(_offhost_cmd()),
            exit_code=1, artifact=None, bundled_sha=None,
        )
        return 1

    git_manifest = build_git_manifest(bundle_path, head, ref)
    (bundle_dir / f"{bundle_path.stem}.manifest.json").write_text(
        json.dumps(git_manifest, indent=2), encoding="utf-8"
    )

    # --- encrypt (fail-closed: never fall back to shipping plaintext) --------
    try:
        recipient = offhost_backup.load_env_var("BACKUP_AGE_RECIPIENT", root=repo_root)
        ciphertext, age_manifest = offhost_backup.encrypt_backup(bundle_path, recipient)
        print(f"[bundle] encrypted: {ciphertext.name} ({ciphertext.stat().st_size // 1024} KB)")
    except Exception as exc:
        print(f"[bundle] ERROR: encryption failed: {exc}", file=sys.stderr)
        print("[bundle] upload SKIPPED — refusing to ship plaintext source",
              file=sys.stderr)
        write_bundle_status(
            bundle_dir, head=head, status="failed", configured=bool(_offhost_cmd()),
            exit_code=1, artifact=None, bundled_sha=None,
        )
        return 1

    # --- upload (only when explicitly configured) ----------------------------
    offhost_cmd = _offhost_cmd(repo_root)
    if not offhost_cmd:
        print("[bundle] BUNDLE_OFFHOST_CMD not set — created and verified locally, "
              "nothing uploaded")
        write_bundle_status(
            bundle_dir, head=head, status="not_configured", configured=False,
            exit_code=None, artifact=ciphertext, bundled_sha=None,
        )
        return 0

    cmd = offhost_cmd.format(file=ciphertext, manifest=age_manifest)
    print(f"[bundle] off-host: {cmd}")
    result = subprocess.run(cmd, shell=True, timeout=900)
    ok = result.returncode == 0
    if not ok:
        print(f"[bundle] ERROR: off-host upload exited {result.returncode}",
              file=sys.stderr)
    write_bundle_status(
        bundle_dir, head=head, status="ok" if ok else "failed", configured=True,
        exit_code=result.returncode, artifact=ciphertext,
        bundled_sha=head if ok else None,
    )
    if ok:
        write_sentinel(bundle_dir, head)
        print(f"[bundle] sentinel advanced to {head[:7]}")
        print("[bundle] done")
        return 0
    return 1


def _offhost_cmd(repo_root: Path = ROOT) -> str:
    return offhost_backup.load_env_var("BUNDLE_OFFHOST_CMD", root=repo_root)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Encrypted recovery-code bundle for off-host storage (BK2)",
    )
    ap.add_argument("--repo-root", type=Path, default=ROOT)
    ap.add_argument("--bundle-dir", type=Path, default=DEFAULT_BUNDLE_DIR)
    ap.add_argument("--ref", default=DEFAULT_REF,
                    help=f"git ref to bundle (default: {DEFAULT_REF})")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="bundle even when HEAD matches the sentinel")
    args = ap.parse_args(argv)
    return run_bundle_backup(
        repo_root=args.repo_root, bundle_dir=args.bundle_dir, ref=args.ref,
        dry_run=args.dry_run, force=args.force,
    )


if __name__ == "__main__":
    sys.exit(main())
