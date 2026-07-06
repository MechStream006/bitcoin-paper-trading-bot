"""Manifest generation tests -- src/archive/manifest.py (T5).

Covers: deterministic manifest construction from an already-written
Parquet artifact (T4), deterministic JSON serialization, file_sha256
correctness (and sensitivity to file corruption), content_hash
consistency with the artifact's own embedded metadata, structural
validation, atomic write with overwrite protection, and temp-file
cleanup on failure.

Run from repo root:
    conda run -n btc_bot pytest tests/archive/test_manifest.py -v
"""
from __future__ import annotations

import dataclasses
import hashlib

import pytest

from src.archive import golden_vectors as gv
from src.archive import manifest as mf
from src.archive import parquet_writer as pw
from src.archive.partition_reader import PartitionKey


def _write_artifact(tmp_path, rows=None, key=None):
    rows = gv.GV_1_ROWS if rows is None else rows
    key = key or PartitionKey("2026-07-01")
    path = pw.write_partition_parquet(rows, tmp_path, key)
    return path, key


# ---------------------------------------------------------------------------
# build_manifest: deterministic construction from the artifact
# ---------------------------------------------------------------------------

class TestBuildManifest:
    def test_fields_match_artifact_embedded_metadata(self, tmp_path):
        path, key = _write_artifact(tmp_path)
        m = mf.build_manifest(path, key)
        assert m.partition_key == "2026-07-01"
        assert m.artifact_filename == "2026-07-01.parquet"
        assert m.row_count == 1
        assert m.content_hash.startswith("sha256:")
        assert m.hash_spec_version == 1
        assert m.manifest_schema_version == mf.MANIFEST_SCHEMA_VERSION

    def test_content_hash_matches_content_hash_module(self, tmp_path):
        from src.archive import content_hash as ch
        path, key = _write_artifact(tmp_path, rows=gv.GV_2_ROWS)
        m = mf.build_manifest(path, key)
        assert m.content_hash == ch.content_hash(gv.GV_2_ROWS)

    def test_artifact_filename_is_relative_not_absolute(self, tmp_path):
        path, key = _write_artifact(tmp_path)
        m = mf.build_manifest(path, key)
        assert m.artifact_filename == path.name
        assert "/" not in m.artifact_filename and "\\" not in m.artifact_filename

    def test_partition_key_mismatch_raises(self, tmp_path):
        path, _ = _write_artifact(tmp_path, key=PartitionKey("2026-07-01"))
        with pytest.raises(ValueError, match="mismatch"):
            mf.build_manifest(path, PartitionKey("2026-07-02"))

    def test_empty_partition_manifest(self, tmp_path):
        path, key = _write_artifact(tmp_path, rows=gv.GV_0_ROWS)
        m = mf.build_manifest(path, key)
        assert m.row_count == 0


# ---------------------------------------------------------------------------
# file_sha256
# ---------------------------------------------------------------------------

class TestFileSha256:
    def test_matches_manual_sha256_of_file_bytes(self, tmp_path):
        path, _ = _write_artifact(tmp_path)
        expected = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        assert mf.compute_file_sha256(path) == expected

    def test_changes_when_file_bytes_change(self, tmp_path):
        path, key = _write_artifact(tmp_path)
        before = mf.build_manifest(path, key).file_sha256
        with open(path, "r+b") as fh:
            fh.seek(0)
            fh.write(b"\x00")  # corrupt one byte, same length
        after = mf.build_manifest(path, key).file_sha256
        assert before != after

    def test_differs_from_content_hash(self, tmp_path):
        # Different hash domains (row data vs. on-disk bytes) -- must not collide.
        path, key = _write_artifact(tmp_path)
        m = mf.build_manifest(path, key)
        assert m.file_sha256 != m.content_hash


# ---------------------------------------------------------------------------
# Deterministic serialization
# ---------------------------------------------------------------------------

class TestDeterministicSerialization:
    def test_two_independent_builds_serialize_byte_identically(self, tmp_path):
        d1, d2 = tmp_path / "a", tmp_path / "b"
        p1, k1 = _write_artifact(d1)
        p2, k2 = _write_artifact(d2)
        m1, m2 = mf.build_manifest(p1, k1), mf.build_manifest(p2, k2)
        assert mf._serialize(m1) == mf._serialize(m2)

    def test_serialization_has_no_wallclock_or_environment_fields(self, tmp_path):
        path, key = _write_artifact(tmp_path)
        m = mf.build_manifest(path, key)
        field_names = {f.name for f in dataclasses.fields(mf.Manifest)}
        forbidden = {"created_at", "hostname", "pid", "collector_code_sha",
                     "writer_code_version", "timestamp"}
        assert field_names & forbidden == set()

    def test_serialized_keys_are_sorted(self, tmp_path):
        import json
        path, key = _write_artifact(tmp_path)
        m = mf.build_manifest(path, key)
        parsed = json.loads(mf._serialize(m).decode("utf-8"))
        assert list(parsed.keys()) == sorted(parsed.keys())


# ---------------------------------------------------------------------------
# Structural validation
# ---------------------------------------------------------------------------

class TestValidateManifest:
    def _good(self, tmp_path):
        path, key = _write_artifact(tmp_path)
        return mf.build_manifest(path, key)

    def test_well_formed_manifest_passes(self, tmp_path):
        mf.validate_manifest(self._good(tmp_path))  # must not raise

    def test_negative_row_count_rejected(self, tmp_path):
        bad = dataclasses.replace(self._good(tmp_path), row_count=-1)
        with pytest.raises(ValueError, match="row_count"):
            mf.validate_manifest(bad)

    def test_unprefixed_content_hash_rejected(self, tmp_path):
        bad = dataclasses.replace(self._good(tmp_path), content_hash="deadbeef")
        with pytest.raises(ValueError, match="content_hash"):
            mf.validate_manifest(bad)

    def test_unprefixed_file_sha256_rejected(self, tmp_path):
        bad = dataclasses.replace(self._good(tmp_path), file_sha256="deadbeef")
        with pytest.raises(ValueError, match="file_sha256"):
            mf.validate_manifest(bad)

    def test_zero_hash_spec_version_rejected(self, tmp_path):
        bad = dataclasses.replace(self._good(tmp_path), hash_spec_version=0)
        with pytest.raises(ValueError, match="hash_spec_version"):
            mf.validate_manifest(bad)

    def test_empty_partition_key_rejected(self, tmp_path):
        bad = dataclasses.replace(self._good(tmp_path), partition_key="")
        with pytest.raises(ValueError, match="partition_key"):
            mf.validate_manifest(bad)


# ---------------------------------------------------------------------------
# Round-trip read/write
# ---------------------------------------------------------------------------

class TestReadWriteRoundTrip:
    def test_round_trip_equals_original(self, tmp_path):
        path, key = _write_artifact(tmp_path)
        m = mf.build_manifest(path, key)
        written = mf.write_manifest(m, tmp_path, key)
        assert mf.read_manifest(written) == m

    def test_written_file_is_valid_json_with_trailing_newline(self, tmp_path):
        path, key = _write_artifact(tmp_path)
        m = mf.build_manifest(path, key)
        written = mf.write_manifest(m, tmp_path, key)
        text = written.read_text(encoding="utf-8")
        assert text.endswith("\n")


# ---------------------------------------------------------------------------
# Atomic write / overwrite protection / cleanup on failure
# ---------------------------------------------------------------------------

class TestAtomicWriteAndCleanup:
    def test_overwrite_protection(self, tmp_path):
        path, key = _write_artifact(tmp_path)
        m = mf.build_manifest(path, key)
        written = mf.write_manifest(m, tmp_path, key)
        original_bytes = written.read_bytes()

        with pytest.raises(FileExistsError):
            mf.write_manifest(m, tmp_path, key)

        assert written.read_bytes() == original_bytes

    def test_no_tmp_files_left_after_success(self, tmp_path):
        path, key = _write_artifact(tmp_path)
        m = mf.build_manifest(path, key)
        mf.write_manifest(m, tmp_path, key)
        leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []

    def test_failure_during_write_leaves_no_tmp_or_final_file(self, tmp_path, monkeypatch):
        path, key = _write_artifact(tmp_path)
        m = mf.build_manifest(path, key)

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated replace failure")

        monkeypatch.setattr(mf.os, "replace", _boom)
        with pytest.raises(RuntimeError):
            mf.write_manifest(m, tmp_path, key)

        leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []
        assert not (tmp_path / "2026-07-01.manifest.json").exists()

    def test_nested_dest_dir_is_created(self, tmp_path):
        path, key = _write_artifact(tmp_path)
        m = mf.build_manifest(path, key)
        nested = tmp_path / "x" / "y"
        written = mf.write_manifest(m, nested, key)
        assert written.exists()
        assert written.parent == nested
