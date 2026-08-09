"""
.dockerignore recursion hardening regression test (2026-08-09 audit).

Bare secret/data-extension patterns in .dockerignore matched only at the
build-context root under this repo's BuildKit, not recursively into
subdirectories -- the same class of gotcha the file's own comment already
documents for **/__pycache__/, just never applied to .env/*.db/*.sqlite*
until this fix. Confirmed empirically with a disposable Docker build
context; the allow-list negations (!.env.example, !.env.*.example) had to
become equally recursive, or a real nested template
(deploy/compose/.env.example) would start being wrongly excluded.

This test rebuilds that same disposable context against the real,
unmodified .dockerignore on every run, so a future accidental de-anchoring
of any of these patterns is caught here rather than discovered as a real
local data leak.
"""
from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

DOCKER = shutil.which("docker")


def _docker_available() -> bool:
    if DOCKER is None:
        return False
    try:
        subprocess.run([DOCKER, "info"], capture_output=True, timeout=10, check=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


requires_docker = pytest.mark.skipif(
    not _docker_available(), reason="docker binary/daemon not available"
)


@requires_docker
def test_dockerignore_excludes_nested_secrets_and_data_but_keeps_templates_and_source(
    tmp_path: Path,
) -> None:
    assert DOCKER is not None
    ctx = tmp_path / "ctx"
    (ctx / "research" / "sub").mkdir(parents=True)
    (ctx / "deploy" / "compose").mkdir(parents=True)

    (ctx / "research" / "sub" / ".env").write_text("SECRET=leak\n")
    (ctx / "research" / "sub" / "test.db").write_text("db\n")
    (ctx / "research" / "sub" / "test.db-wal").write_text("wal\n")
    (ctx / "research" / "sub" / "test.db-shm").write_text("shm\n")
    (ctx / "research" / "sub" / "test.sqlite").write_text("sqlite\n")
    (ctx / "research" / "sub" / "test.sqlite3").write_text("sqlite3\n")

    (ctx / "deploy" / "compose" / ".env.example").write_text("TEMPLATE=1\n")
    (ctx / ".env.example").write_text("TEMPLATE=1\n")

    (ctx / "research" / "sub" / "keep_me.py").write_text("# legitimate source\n")

    shutil.copy(ROOT / ".dockerignore", ctx / ".dockerignore")
    (ctx / "Dockerfile").write_text(
        'FROM alpine:3.20\nWORKDIR /app\nCOPY . .\nCMD ["sh"]\n'
    )

    tag = f"dockerignore-recursion-test-{uuid.uuid4().hex[:12]}"
    try:
        subprocess.run(
            [DOCKER, "build", "--no-cache", "-t", tag, str(ctx)],
            capture_output=True, timeout=120, check=True,
        )
        result = subprocess.run(
            [DOCKER, "run", "--rm", tag, "find", "/app", "-type", "f"],
            capture_output=True, timeout=30, check=True, text=True,
        )
        files = set(result.stdout.strip().splitlines())
    finally:
        subprocess.run([DOCKER, "rmi", "-f", tag], capture_output=True, check=False)

    assert "/app/research/sub/.env" not in files
    assert "/app/research/sub/test.db" not in files
    assert "/app/research/sub/test.db-wal" not in files
    assert "/app/research/sub/test.db-shm" not in files
    assert "/app/research/sub/test.sqlite" not in files
    assert "/app/research/sub/test.sqlite3" not in files

    assert "/app/deploy/compose/.env.example" in files
    assert "/app/.env.example" in files
    assert "/app/research/sub/keep_me.py" in files
