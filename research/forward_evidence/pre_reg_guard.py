"""
pre_reg_guard.py
================
Shared fn_hash enforcement for forward evidence collection runners.

Usage (inside a runner's main() — NOT at module import time):

    from forward_evidence.pre_reg_guard import verify_fn_hash, FnHashViolation

    def main():
        try:
            fn_hash = verify_fn_hash(
                pre_reg_path   = _PRE_REG,       # pathlib.Path to final JSON
                fn_file_path   = _FN_FILE,        # pathlib.Path to decision function
                signal_id      = "h5_vol_regime", # must match JSON "signal_id"
            )
        except FnHashViolation as exc:
            # log to DB / alerting here before exiting
            sys.exit(1)

The guard:
  - Reads and LF-normalises both files before hashing (CRLF -> LF)
  - Verifies signal_id in the JSON matches the caller's signal_id
  - Raises FnHashViolation (RuntimeError) on any mismatch
  - Returns the computed hash string on success (for storage in run_log.fn_hash)

Hashing convention:
    SHA-256 of LF-normalised bytes (raw.replace(b'\\r\\n', b'\\n'))
    Applied to decision function files only. Source artifact hashes in
    artifact_hashes (signals.py, engine.py, governance.py) use the same
    convention.
"""
from __future__ import annotations

import hashlib
import json
import pathlib


class FnHashViolation(RuntimeError):
    """Raised when the decision function file hash does not match the registered hash."""


def _sha256_lf(path: pathlib.Path) -> str:
    """Return SHA-256 of the file's bytes after normalising CRLF -> LF."""
    raw = path.read_bytes()
    normalised = raw.replace(b"\r\n", b"\n")
    return hashlib.sha256(normalised).hexdigest()


def verify_fn_hash(
    pre_reg_path: pathlib.Path,
    fn_file_path: pathlib.Path,
    signal_id: str,
) -> str:
    """
    Verify that the decision function file matches the registered hash.

    Parameters
    ----------
    pre_reg_path : pathlib.Path
        Path to the final pre-registration JSON for this signal.
    fn_file_path : pathlib.Path
        Path to the decision function file to verify.
    signal_id : str
        Expected signal_id; must match the JSON's 'signal_id' field.

    Returns
    -------
    str
        The computed SHA-256 hex digest (identical to the expected hash on success).
        Caller should store this in run_log.fn_hash for per-run auditability.

    Raises
    ------
    FnHashViolation
        If the computed hash differs from the registered hash, or if the
        pre-registration JSON's signal_id does not match the caller's signal_id.
    FileNotFoundError
        If either path does not exist.
    """
    reg = json.loads(pre_reg_path.read_text(encoding="utf-8"))

    # Cross-check: the JSON must belong to this signal
    json_signal_id = reg.get("signal_id", "")
    if json_signal_id != signal_id:
        raise FnHashViolation(
            f"[{signal_id}] pre-registration JSON signal_id mismatch: "
            f"expected '{signal_id}', JSON contains '{json_signal_id}'. "
            f"Wrong pre-registration file passed to verify_fn_hash()."
        )

    expected = reg["function_spec"]["decision_function_hash"]
    actual   = _sha256_lf(fn_file_path)

    if actual != expected:
        raise FnHashViolation(
            f"[{signal_id}] fn_hash MISMATCH — decision function file has been "
            f"modified post-registration.\n"
            f"  file:     {fn_file_path}\n"
            f"  expected: {expected}\n"
            f"  actual:   {actual}\n"
            "Aborting. Do NOT write to evidence DB. Investigate before re-running."
        )

    return actual
