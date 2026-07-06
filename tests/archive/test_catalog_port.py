"""CatalogPort dependency-boundary tests -- src/archive/catalog_port.py (T6).

Covers: structural (runtime_checkable) protocol conformance, the stricter
fail-closed ensure_catalog_port() gate, realistic interaction sequences
driven entirely through in-memory test doubles and unittest.mock, and an
explicit static check that this module introduces no concrete catalog
dependency (no sqlite3 import) -- proving the Archive Writer's dependency
on "a catalog" stays purely structural.

Run from repo root:
    conda run -n btc_bot pytest tests/archive/test_catalog_port.py -v
"""
from __future__ import annotations

import ast
import inspect
from unittest.mock import MagicMock

import pytest

from src.archive.catalog_port import CatalogPort, ensure_catalog_port


# ---------------------------------------------------------------------------
# A pure in-memory test double -- NOT a catalog implementation, no SQLite,
# no persistence. Exists only to drive protocol-conformance and interaction
# tests without depending on any concrete backend (M1.1's job, not T6's).
# ---------------------------------------------------------------------------

class _FakeCatalog:
    def __init__(self, pending_keys=()):
        self._pending = set(pending_keys)
        self._exported = {}
        self._verified = set()

    def is_pending(self, partition_key: str) -> bool:
        return partition_key in self._pending

    def mark_exported(self, partition_key: str, manifest) -> None:
        if partition_key not in self._pending:
            raise ValueError(f"{partition_key} is not PENDING")
        self._pending.discard(partition_key)
        self._exported[partition_key] = manifest

    def mark_verified(self, partition_key: str) -> None:
        if partition_key not in self._exported:
            raise ValueError(f"{partition_key} is not EXPORTED")
        self._verified.add(partition_key)


class _MissingMarkVerified:
    """Structurally incomplete: no mark_verified at all."""
    def is_pending(self, partition_key: str) -> bool:
        return True

    def mark_exported(self, partition_key: str, manifest) -> None:
        pass


class _NonCallableAttribute:
    """Has all three names, but is_pending is a plain attribute, not a method."""
    is_pending = True

    def mark_exported(self, partition_key: str, manifest) -> None:
        pass

    def mark_verified(self, partition_key: str) -> None:
        pass


# ---------------------------------------------------------------------------
# Structural (runtime_checkable) protocol conformance
# ---------------------------------------------------------------------------

class TestProtocolConformance:
    def test_isinstance_accepts_conformant_fake(self):
        assert isinstance(_FakeCatalog(), CatalogPort)

    def test_isinstance_rejects_incomplete_object(self):
        assert not isinstance(_MissingMarkVerified(), CatalogPort)

    def test_isinstance_rejects_unrelated_object(self):
        assert not isinstance(object(), CatalogPort)


# ---------------------------------------------------------------------------
# ensure_catalog_port: the fail-closed integration gate
# ---------------------------------------------------------------------------

class TestEnsureCatalogPort:
    def test_accepts_conformant_fake(self):
        fake = _FakeCatalog()
        assert ensure_catalog_port(fake) is fake

    def test_rejects_missing_method(self):
        with pytest.raises(TypeError, match="mark_verified"):
            ensure_catalog_port(_MissingMarkVerified())

    def test_rejects_non_callable_attribute(self):
        # isinstance-via-hasattr would accept this; ensure_catalog_port must not.
        candidate = _NonCallableAttribute()
        assert isinstance(candidate, CatalogPort)  # hasattr-only check passes
        with pytest.raises(TypeError, match="is_pending"):
            ensure_catalog_port(candidate)  # stricter check must still reject it

    def test_rejects_unrelated_object(self):
        with pytest.raises(TypeError):
            ensure_catalog_port(object())

    def test_rejects_none(self):
        with pytest.raises(TypeError):
            ensure_catalog_port(None)

    def test_rejects_plain_string(self):
        with pytest.raises(TypeError):
            ensure_catalog_port("not a catalog")

    def test_accepts_spec_bound_mock(self):
        # A Mock spec'd against the Protocol is the standard test-double
        # pattern for consumers of this interface (e.g. T7's own tests).
        mock_catalog = MagicMock(spec=CatalogPort)
        assert ensure_catalog_port(mock_catalog) is mock_catalog


# ---------------------------------------------------------------------------
# Interaction via test doubles/mocks
# ---------------------------------------------------------------------------

class TestInteractionViaFake:
    def test_realistic_pending_to_verified_sequence(self):
        catalog = _FakeCatalog(pending_keys=["2026-07-01"])
        manifest = object()  # opaque to the fake -- shape doesn't matter here

        assert catalog.is_pending("2026-07-01") is True
        catalog.mark_exported("2026-07-01", manifest)
        assert catalog.is_pending("2026-07-01") is False
        catalog.mark_verified("2026-07-01")
        assert "2026-07-01" in catalog._verified

    def test_unknown_partition_is_not_pending(self):
        # Fail-closed default: an unseeded/unknown key must never read as
        # pending (never silently treated as exportable).
        catalog = _FakeCatalog(pending_keys=["2026-07-01"])
        assert catalog.is_pending("1999-01-01") is False

    def test_mark_exported_before_pending_rejected_by_backend(self):
        # CatalogPort itself can't enforce this (structural only) -- but a
        # conformant backend, including this fake, is expected to.
        catalog = _FakeCatalog(pending_keys=[])
        with pytest.raises(ValueError):
            catalog.mark_exported("2026-07-01", object())

    def test_interaction_via_mock_records_calls(self):
        mock_catalog = MagicMock(spec=CatalogPort)
        mock_catalog.is_pending.return_value = True

        assert mock_catalog.is_pending("2026-07-01") is True
        mock_catalog.mark_exported("2026-07-01", "manifest-placeholder")
        mock_catalog.mark_verified("2026-07-01")

        mock_catalog.is_pending.assert_called_once_with("2026-07-01")
        mock_catalog.mark_exported.assert_called_once_with(
            "2026-07-01", "manifest-placeholder"
        )
        mock_catalog.mark_verified.assert_called_once_with("2026-07-01")


# ---------------------------------------------------------------------------
# No concrete catalog dependency introduced
# ---------------------------------------------------------------------------

class TestNoConcreteCatalogDependency:
    def test_module_does_not_import_sqlite3(self):
        import src.archive.catalog_port as mod
        source = inspect.getsource(mod)
        tree = ast.parse(source)
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_names.add(node.module.split(".")[0])
        assert "sqlite3" not in imported_names

    def test_module_defines_no_concrete_catalog_class(self):
        # Only CatalogPort (a Protocol) may be exported -- no SqliteCatalogPort
        # or any other concrete backend class belongs in this module.
        import src.archive.catalog_port as mod
        classes = [
            name for name, obj in vars(mod).items()
            if isinstance(obj, type) and obj.__module__ == mod.__name__
        ]
        assert classes == ["CatalogPort"]
