"""Header detection and column profiling.

Both functions are vectorised per column and inspect at most
:data:`TYPE_SAMPLE_SIZE` values per column, so profiling a 500k-row file costs
roughly the same as profiling a 5k-row one.
"""

from __future__ import annotations

import re
import warnings
from typing import Any, Literal

import numpy as np
import pandas as pd

HEADER_SCAN_ROWS = 20
TYPE_SAMPLE_SIZE = 5_000
NUMERIC_MATCH_RATIO = 0.95
DATE_MATCH_RATIO = 0.90
CATEGORY_MAX_UNIQUE = 50
CATEGORY_MAX_UNIQUE_RATIO = 0.05
SAMPLE_VALUES = 3

_NUMBER_LIKE = re.compile(r"^\s*[-+]?(\d{1,3}(,\d{3})+|\d+)?(\.\d+)?\s*%?\s*$")
_DATE_LIKE = re.compile(r"^\s*\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}")
_BOOL_VALUES = {"true", "false", "yes", "no", "y", "n"}


# -----------------------------------------------------------------------------
# Header detection
# -----------------------------------------------------------------------------


def _is_label(value: Any) -> bool:
    """True when a cell looks like a column label (non-empty text, not a number/date)."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return False
    if isinstance(value, (int, float, np.number, pd.Timestamp)) or hasattr(value, "year"):
        return False
    text = str(value).strip()
    return bool(text) and not _NUMBER_LIKE.match(text) and not _DATE_LIKE.match(text)


def detect_header_row(raw: pd.DataFrame, scan_rows: int = HEADER_SCAN_ROWS) -> int:
    """Return the index of the most likely header row in a header-less table.

    A header row is the first row, within the first ``scan_rows``, where

    * at least 60 % of the table's populated width is filled, and
    * every filled cell is a text label, and
    * labels are unique.

    Report-style extracts often carry a title and a blank line above the real
    header; this skips them. Falls back to row 0.
    """
    if raw.empty:
        return 0
    head = raw.head(scan_rows)
    width = int(head.notna().sum(axis=1).max())
    if width == 0:
        return 0
    for position, (_, row) in enumerate(head.iterrows()):  # ≤ 20 rows, not the dataset
        filled = row.dropna()
        filled = filled[filled.astype(str).str.strip() != ""]
        if len(filled) < max(1, int(np.ceil(width * 0.6))):
            continue
        labels = [str(v).strip() for v in filled]
        if all(_is_label(v) for v in filled) and len(set(labels)) == len(labels):
            return position
    return 0


def _unique_names(values: list[Any]) -> list[str]:
    """Blank headers become ``unnamed_N``; duplicates get ``_2``, ``_3`` suffixes."""
    names: list[str] = []
    seen: dict[str, int] = {}
    for index, value in enumerate(values):
        text = "" if value is None or (isinstance(value, float) and np.isnan(value)) else str(value)
        text = text.strip() or f"unnamed_{index + 1}"
        if text in seen:
            seen[text] += 1
            text = f"{text}_{seen[text]}"
        else:
            seen[text] = 1
        names.append(text)
    return names


def apply_header(
    raw: pd.DataFrame, mode: Literal["auto"] | int | None = "auto"
) -> tuple[pd.DataFrame, int | None, list[str]]:
    """Promote a header row and tidy the frame.

    Args:
        raw: Table read with ``header=None``.
        mode: ``"auto"`` to detect, an int row index, or ``None`` for "no header"
            (columns become ``column_1`` … ``column_n``).

    Returns:
        ``(frame, header_row_index, dropped_columns)``. Unnamed columns that are
        completely empty (typical Excel trailing columns) are dropped and listed.
    """
    if mode is None:
        frame = raw.copy()
        frame.columns = [f"column_{i + 1}" for i in range(frame.shape[1])]
        return frame.reset_index(drop=True), None, []

    header_index = detect_header_row(raw) if mode == "auto" else int(mode)
    if header_index >= len(raw):
        return pd.DataFrame(columns=_unique_names(list(raw.iloc[0])) if len(raw) else []), 0, []

    names = _unique_names(list(raw.iloc[header_index]))
    frame = raw.iloc[header_index + 1 :].reset_index(drop=True)
    frame.columns = names

    # Blank strings from CSV/Sheets count as missing for "is this column empty?".
    empty_mask = frame.replace(r"^\s*$", np.nan, regex=True).isna().all(axis=0)
    dropped = [c for c in frame.columns if c.startswith("unnamed_") and bool(empty_mask[c])]
    if dropped:
        frame = frame.drop(columns=dropped)
    return frame, header_index, dropped


# -----------------------------------------------------------------------------
# Type detection
# -----------------------------------------------------------------------------


def _as_text(sample: pd.Series) -> pd.Series:
    return sample.astype(str).str.strip()


def _numeric_parse(text: pd.Series) -> pd.Series:
    cleaned = text.str.replace(",", "", regex=False).str.rstrip("%").str.strip()
    return pd.to_numeric(cleaned, errors="coerce")


def _date_parse(sample: pd.Series) -> pd.Series[pd.Timestamp]:
    if pd.api.types.is_datetime64_any_dtype(sample):
        return sample
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return pd.to_datetime(sample, errors="coerce", format="mixed")


def infer_column_type(series: pd.Series) -> str:
    """Infer a SmartMIS column type from the values present.

    Returns one of ``empty, boolean, integer, float, percent, date, datetime,
    category, string``. The vocabulary matches ``validation_rules.yaml``.
    """
    values = series.dropna()
    if values.dtype == object or pd.api.types.is_string_dtype(values):
        values = values[_as_text(values) != ""]
    if values.empty:
        return "empty"
    sample = (
        values.sample(TYPE_SAMPLE_SIZE, random_state=0)
        if len(values) > TYPE_SAMPLE_SIZE
        else values
    )

    if pd.api.types.is_bool_dtype(sample):
        return "boolean"
    if pd.api.types.is_datetime64_any_dtype(sample):
        times = sample.dt.normalize() != sample
        return "datetime" if bool(times.any()) else "date"
    if pd.api.types.is_numeric_dtype(sample):
        numeric = sample.astype(float)
        return "integer" if bool((numeric % 1 == 0).all()) else "float"

    # Mixed/object columns (Excel) or text columns (CSV).
    native_dates = sample.map(lambda v: hasattr(v, "year") and hasattr(v, "month"))
    if bool(native_dates.mean() >= DATE_MATCH_RATIO):
        parsed = _date_parse(sample[native_dates])
        times = parsed.dt.normalize() != parsed
        return "datetime" if bool(times.any()) else "date"

    text = _as_text(sample)
    lowered = text.str.lower()
    if bool(lowered.isin(_BOOL_VALUES).all()):
        return "boolean"

    numeric = _numeric_parse(text)
    if numeric.notna().mean() >= NUMERIC_MATCH_RATIO:
        if bool(text.str.endswith("%").mean() >= NUMERIC_MATCH_RATIO):
            return "percent"
        valid = numeric.dropna()
        return "integer" if bool((valid % 1 == 0).all()) else "float"

    has_date_separators = text.str.contains(r"[-/.:]|\d\s+[A-Za-z]{3}", regex=True)
    if bool(has_date_separators.mean() >= DATE_MATCH_RATIO):
        parsed = _date_parse(text)
        if parsed.notna().mean() >= DATE_MATCH_RATIO:
            valid_dates = parsed.dropna()
            times = valid_dates.dt.normalize() != valid_dates
            return "datetime" if bool(times.any()) else "date"

    unique = text.nunique()
    ratio = unique / len(text)
    if len(text) >= 20 and (
        (unique <= CATEGORY_MAX_UNIQUE and ratio <= 0.5) or ratio <= CATEGORY_MAX_UNIQUE_RATIO
    ):
        return "category"
    return "string"


def profile_columns(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Return per-column profile dictionaries (see :class:`ColumnProfile`)."""
    total = len(frame)
    profiles: list[dict[str, Any]] = []
    for name in frame.columns:
        series = frame[name]
        present = series.dropna()
        if present.dtype == object or pd.api.types.is_string_dtype(present):
            present = present[_as_text(present) != ""]
        non_null = len(present)
        samples = tuple(str(v) for v in present.drop_duplicates().head(SAMPLE_VALUES))
        profiles.append(
            {
                "name": str(name),
                "detected_type": infer_column_type(series),
                "non_null_count": non_null,
                "null_count": total - non_null,
                "null_pct": round((total - non_null) / total * 100, 2) if total else 0.0,
                "unique_count": int(present.astype(str).nunique()) if non_null else 0,
                "sample_values": samples,
            }
        )
    return profiles
