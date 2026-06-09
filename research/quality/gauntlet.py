"""
Quality gauntlet for research lake partitions (WI-7).

Runs a fixed battery of checks on a DataFrame read from a lake partition and
returns a structured report dict.  All checks run regardless of prior failures
so every issue in a partition is visible in one pass.

Severity:
    CRITICAL  Data corruption or structural errors that make the partition
              unsuitable for analysis: duplicates, disorder, out-of-partition
              timestamps, or critically low coverage.
    WARNING   Anomalies that warrant inspection but do not invalidate the data:
              gaps, outliers, or moderate coverage shortfall.

Public API:
    run_gauntlet(df, *, dataset, symbol, year, month, ts_column,
                 report_path=None) -> dict
"""
from __future__ import annotations

import calendar
import json
import pathlib
from datetime import datetime, timezone
from typing import Any

import pandas as pd

# ── Dataset-specific constants ─────────────────────────────────────────────────
_INTERVAL_MS: dict[str, int] = {
    "spot":    60_000,
    "perp":    60_000,
    "funding": 28_800_000,
}
_RECORDS_PER_DAY: dict[str, int] = {
    "spot":    1_440,
    "perp":    1_440,
    "funding": 3,
}
_PRIMARY_COL: dict[str, str] = {
    "spot":    "close",
    "perp":    "close",
    "funding": "funding_rate",
}

# ── Timestamp sanity bounds (ms) ──────────────────────────────────────────────
_TS_MIN_MS = 1_483_228_800_000   # 2017-01-01 00:00:00 UTC
_TS_MAX_MS = 1_924_992_000_000   # 2031-01-01 00:00:00 UTC

# ── Funding cadence tolerance (CLAUDE.md §11: Binance ±1 ms noise) ────────────
_FUNDING_CADENCE_MS  = 28_800_000
_CADENCE_TOLERANCE_MS = 1_000

# ── Coverage thresholds ───────────────────────────────────────────────────────
_COV_CRITICAL_PCT = 95.0
_COV_WARNING_PCT  = 99.0

Finding = dict[str, Any]


# ── Individual checks ─────────────────────────────────────────────────────────

def _check_partition_consistency(
    df: pd.DataFrame, ts_col: str, year: int, month: int
) -> list[Finding]:
    """rows > 0, min_ts < max_ts, all timestamps belong to declared year/month."""
    findings: list[Finding] = []

    if len(df) == 0:
        findings.append({
            "check": "partition_consistency",
            "severity": "CRITICAL",
            "detail": "Partition has 0 rows.",
        })
        return findings   # no further timestamp checks make sense

    ts = df[ts_col]
    if ts.min() >= ts.max():
        findings.append({
            "check": "partition_consistency",
            "severity": "CRITICAL",
            "detail": f"min_ts >= max_ts (min={ts.min()}, max={ts.max()}).",
        })

    month_start_ms = int(datetime(year, month, 1, tzinfo=timezone.utc).timestamp() * 1_000)
    last_day       = calendar.monthrange(year, month)[1]
    month_end_ms   = int(
        datetime(year, month, last_day, 23, 59, 59, 999_000, tzinfo=timezone.utc).timestamp() * 1_000
    )
    outside = df[(ts < month_start_ms) | (ts > month_end_ms)]
    if not outside.empty:
        examples = outside[ts_col].head(3).tolist()
        findings.append({
            "check": "partition_consistency",
            "severity": "CRITICAL",
            "detail": (
                f"{len(outside)} row(s) have timestamps outside "
                f"{year}-{month:02d} [{month_start_ms}, {month_end_ms}]; "
                f"examples: {examples}"
            ),
        })
    return findings


def _check_monotonic(df: pd.DataFrame, ts_col: str) -> list[Finding]:
    """Timestamps must be strictly increasing (positive diffs)."""
    if len(df) < 2:
        return []
    diffs = df[ts_col].diff().dropna()
    bad   = (diffs <= 0).sum()
    if bad == 0:
        return []
    return [{
        "check": "monotonic_timestamps",
        "severity": "CRITICAL",
        "detail": f"{bad} non-positive diff(s) found (0 means duplicate, <0 means disorder).",
    }]


def _check_duplicates(df: pd.DataFrame, ts_col: str) -> list[Finding]:
    """Any duplicate timestamp is a CRITICAL error."""
    n_dupes = len(df) - df[ts_col].nunique()
    if n_dupes == 0:
        return []
    examples = (
        df[df.duplicated(subset=[ts_col], keep=False)][ts_col].head(3).tolist()
    )
    return [{
        "check": "duplicate_timestamps",
        "severity": "CRITICAL",
        "detail": f"{n_dupes} duplicate timestamp(s); examples: {examples}",
    }]


def _check_utc_normalization(df: pd.DataFrame, ts_col: str) -> list[Finding]:
    """All timestamps must be in a plausible UTC-ms range [2017, 2031)."""
    ts    = df[ts_col]
    below = (ts < _TS_MIN_MS).sum()
    above = (ts > _TS_MAX_MS).sum()
    if below == 0 and above == 0:
        return []
    return [{
        "check": "utc_normalization",
        "severity": "CRITICAL",
        "detail": (
            f"{below} timestamp(s) before 2017-01-01, "
            f"{above} timestamp(s) after 2031-01-01."
        ),
    }]


def _check_gaps(df: pd.DataFrame, dataset: str, ts_col: str) -> list[Finding]:
    """Detect missing records by comparing consecutive timestamp diffs to the expected interval."""
    expected_ms = _INTERVAL_MS.get(dataset)
    if expected_ms is None or len(df) < 2:
        return []

    ts    = df[ts_col].sort_values().reset_index(drop=True)
    diffs = ts.diff().dropna().astype("int64")

    # Threshold: gap means diff > expected + 10% (accounts for ±1 ms funding noise)
    gap_threshold_ms = expected_ms + max(1_000, expected_ms // 10)
    gap_diffs = diffs[diffs > gap_threshold_ms]
    if gap_diffs.empty:
        return []

    missing_records = int(((gap_diffs // expected_ms) - 1).sum())
    largest_gap_s   = int(gap_diffs.max()) // 1_000
    return [{
        "check": "gap_detection",
        "severity": "WARNING",
        "detail": (
            f"{len(gap_diffs)} gap(s) found; ~{missing_records} missing record(s); "
            f"largest gap {largest_gap_s}s (expected {expected_ms // 1_000}s interval)."
        ),
    }]


def _check_coverage(
    df: pd.DataFrame, dataset: str, year: int, month: int
) -> list[Finding]:
    """Actual row count vs expected for the declared partition."""
    rpd = _RECORDS_PER_DAY.get(dataset)
    if rpd is None:
        return []
    expected = calendar.monthrange(year, month)[1] * rpd
    actual   = len(df)
    pct      = actual / expected * 100 if expected > 0 else 0.0

    if pct >= _COV_WARNING_PCT:
        return []
    severity = "CRITICAL" if pct < _COV_CRITICAL_PCT else "WARNING"
    return [{
        "check": "coverage_validation",
        "severity": severity,
        "detail": (
            f"Coverage {pct:.2f}% ({actual}/{expected} expected rows "
            f"for {year}-{month:02d} {dataset})."
        ),
    }]


def _check_funding_cadence(df: pd.DataFrame, dataset: str, ts_col: str) -> list[Finding]:
    """Funding timestamps must be within ±1 s of an 8-hour UTC grid point."""
    if dataset != "funding" or len(df) == 0:
        return []
    remainder = df[ts_col] % _FUNDING_CADENCE_MS
    off = remainder[
        (remainder > _CADENCE_TOLERANCE_MS) &
        (remainder < _FUNDING_CADENCE_MS - _CADENCE_TOLERANCE_MS)
    ]
    if off.empty:
        return []
    return [{
        "check": "funding_cadence",
        "severity": "WARNING",
        "detail": (
            f"{len(off)} funding timestamp(s) off the 8-hour UTC grid "
            f"by more than {_CADENCE_TOLERANCE_MS} ms."
        ),
    }]


def _check_outliers(df: pd.DataFrame, dataset: str) -> list[Finding]:
    """Flag values outside [Q1 − 3×IQR, Q3 + 3×IQR] in the primary numeric column."""
    col = _PRIMARY_COL.get(dataset)
    if col is None or col not in df.columns:
        return []
    series = pd.to_numeric(df[col], errors="coerce").dropna()
    if len(series) < 4:
        return []
    q1, q3 = series.quantile(0.25), series.quantile(0.75)
    iqr     = q3 - q1
    if iqr == 0:
        return []
    lower, upper = q1 - 3 * iqr, q3 + 3 * iqr
    outliers = series[(series < lower) | (series > upper)]
    if outliers.empty:
        return []
    examples = [f"{v:.6g}" for v in outliers.head(3).tolist()]
    return [{
        "check": "outlier_annotation",
        "severity": "WARNING",
        "detail": (
            f"{len(outliers)} outlier(s) in '{col}' outside 3×IQR "
            f"[{lower:.6g}, {upper:.6g}]; examples: {examples}."
        ),
    }]


# ── Ordered check battery ──────────────────────────────────────────────────────
_CHECKS = [
    ("partition_consistency", lambda df, **kw: _check_partition_consistency(
        df, kw["ts_column"], kw["year"], kw["month"])),
    ("monotonic_timestamps",  lambda df, **kw: _check_monotonic(df, kw["ts_column"])),
    ("duplicate_timestamps",  lambda df, **kw: _check_duplicates(df, kw["ts_column"])),
    ("utc_normalization",     lambda df, **kw: _check_utc_normalization(df, kw["ts_column"])),
    ("gap_detection",         lambda df, **kw: _check_gaps(df, kw["dataset"], kw["ts_column"])),
    ("coverage_validation",   lambda df, **kw: _check_coverage(df, kw["dataset"], kw["year"], kw["month"])),
    ("funding_cadence",       lambda df, **kw: _check_funding_cadence(df, kw["dataset"], kw["ts_column"])),
    ("outlier_annotation",    lambda df, **kw: _check_outliers(df, kw["dataset"])),
]


# ── Public entry point ─────────────────────────────────────────────────────────

def run_gauntlet(
    df: pd.DataFrame,
    *,
    dataset: str,
    symbol: str,
    year: int,
    month: int,
    ts_column: str,
    report_path: str | pathlib.Path | None = None,
) -> dict:
    """
    Run the full quality battery on *df*.

    Parameters match the lake partition metadata.  The DataFrame is expected to
    have already been read via storage.lake.read_partition(); extra columns
    (e.g. Hive partition columns injected by PyArrow) are silently ignored.

    Returns a report dict with keys:
        dataset, symbol, year, month, rows_checked,
        checks_run, critical_count, warning_count, passed,
        findings (list), run_ts (ISO UTC str)

    If *report_path* is given, writes the report as indented JSON to that path.
    """
    ctx = dict(dataset=dataset, symbol=symbol, year=year, month=month, ts_column=ts_column)
    findings: list[Finding] = []

    for _name, check_fn in _CHECKS:
        try:
            findings.extend(check_fn(df, **ctx))
        except Exception as exc:   # noqa: BLE001
            findings.append({
                "check": _name,
                "severity": "CRITICAL",
                "detail": f"Check raised an exception: {exc!r}",
            })

    critical = sum(1 for f in findings if f["severity"] == "CRITICAL")
    warning  = sum(1 for f in findings if f["severity"] == "WARNING")

    report: dict = {
        "dataset":        dataset,
        "symbol":         symbol,
        "year":           year,
        "month":          month,
        "rows_checked":   len(df),
        "checks_run":     len(_CHECKS),
        "critical_count": critical,
        "warning_count":  warning,
        "passed":         critical == 0,
        "findings":       findings,
        "run_ts":         datetime.now(timezone.utc).isoformat(),
    }

    if report_path is not None:
        pathlib.Path(report_path).write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )

    return report
