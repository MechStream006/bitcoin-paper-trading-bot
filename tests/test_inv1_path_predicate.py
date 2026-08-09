"""
INV-1 (ADR-0001 sec2) path-predicate regression test.

Root cause (found 2026-08-09): the INV-1 `find` check in
.github/workflows/ci.yml and its manual-verification twin in
docs/docker/research-image.md used `-path "*/research/data*"`. `find -path`
performs fnmatch-style globbing where `*` matches across `/`, so that
pattern is an unanchored substring match, not a directory-boundary match.
"research/data" is a literal prefix of "research/dataset_builder" (Phase 4's
new package), so every file under research/dataset_builder/ false-positived
as project data.

Fix: anchor the pattern with a trailing "/" -- `-path "*/research/data/*"` --
so it matches only inside research/data/, not anything merely prefixed by
those characters.

This test parses the predicate directly out of both files (not a
hand-copied literal) so any future edit that reintroduces the substring bug,
or lets the two files drift apart, fails here before it reaches CI.
"""
from __future__ import annotations

import fnmatch
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
DOCKER_DOC = REPO_ROOT / "docs" / "docker" / "research-image.md"

FIND_PREDICATE_RE = re.compile(
    r'find /app \\\( -iname "([^"]+)" -o -iname "([^"]+)" '
    r'-o -path "([^"]+)" -o -path "([^"]+)" \\\)'
)


def _extract_predicate(text: str) -> tuple[str, str, str, str]:
    match = FIND_PREDICATE_RE.search(text)
    assert match is not None, "INV-1 find predicate not found or its shape changed"
    return match.groups()


def _matches_inv1(path: str, predicate: tuple[str, str, str, str]) -> bool:
    iname_a, iname_b, path_c, path_d = predicate
    basename = path.rsplit("/", 1)[-1]
    return (
        fnmatch.fnmatch(basename.lower(), iname_a.lower())
        or fnmatch.fnmatch(basename.lower(), iname_b.lower())
        or fnmatch.fnmatch(path, path_c)
        or fnmatch.fnmatch(path, path_d)
    )


@pytest.fixture(scope="module")
def ci_predicate() -> tuple[str, str, str, str]:
    return _extract_predicate(CI_WORKFLOW.read_text(encoding="utf-8"))


def test_ci_and_docs_predicates_are_identical():
    ci_predicate = _extract_predicate(CI_WORKFLOW.read_text(encoding="utf-8"))
    doc_predicate = _extract_predicate(DOCKER_DOC.read_text(encoding="utf-8"))
    assert ci_predicate == doc_predicate


def test_research_data_clause_is_anchored_to_the_directory(ci_predicate):
    _, _, _, path_clause = ci_predicate
    assert path_clause == "*/research/data/*"


def test_evidence_library_clause_is_unchanged(ci_predicate):
    _, _, evidence_clause, _ = ci_predicate
    assert evidence_clause == "*/evidence_library*"


REAL_PROJECT_DATA_AND_SECRETS = [
    "/app/research/data/spot/2024-01.parquet",
    "/app/research/data/raw/_backup_dvol_BTC_20260806T101310Z/foo.parquet",
    "/app/research/evidence_library/ledger.db",
    "/app/.env",
    "/app/research/dataset_builder/dataset_builder.db",
]


@pytest.mark.parametrize("path", REAL_PROJECT_DATA_AND_SECRETS)
def test_forbidden_data_and_secrets_still_detected(ci_predicate, path):
    assert _matches_inv1(path, ci_predicate), (
        f"{path} must still be flagged as project data/secrets by INV-1"
    )


DATASET_BUILDER_SOURCE_FILES = [
    "/app/research/dataset_builder/admissibility_derivation.py",
    "/app/research/dataset_builder/validation.py",
    "/app/research/dataset_builder/__init__.py",
]


@pytest.mark.parametrize("path", DATASET_BUILDER_SOURCE_FILES)
def test_dataset_builder_source_is_no_longer_a_false_positive(ci_predicate, path):
    assert not _matches_inv1(path, ci_predicate), (
        f"{path} is research source code, not project data -- INV-1 must not flag it"
    )
