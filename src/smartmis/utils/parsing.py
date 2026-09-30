"""Vectorised value parsing shared by validation and cleaning.

Each parser returns the parsed values **and** boolean masks so callers can tell
"blank" apart from "present but invalid" — the distinction the Data Quality
Score depends on. Nothing here mutates its input.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

import numpy as np
import pandas as pd

_NUMERIC_JUNK = r"[,\s₹$€£]"


@dataclass(frozen=True)
class Parsed:
    """Result of parsing a column."""

    values: pd.Series
    blank: pd.Series  # True where the cell is empty / missing
    invalid: pd.Series  # True where the cell has a value that could not be parsed

    @property
    def valid(self) -> pd.Series:
        return ~(self.blank | self.invalid)


def text_view(series: pd.Series) -> pd.Series:
    """String view of a column with NA preserved and surrounding whitespace removed."""
    return series.astype("string").str.strip()


def blank_mask(series: pd.Series) -> pd.Series:
    """True for NA and for empty / whitespace-only strings."""
    if pd.api.types.is_numeric_dtype(series) or pd.api.types.is_datetime64_any_dtype(series):
        return series.isna()
    text = text_view(series)
    return (text.isna() | (text == "")).fillna(True).astype(bool)


def parse_numeric(series: pd.Series) -> Parsed:
    """Parse numbers, accepting thousands separators, currency symbols and a trailing ``%``."""
    blank = blank_mask(series)
    if pd.api.types.is_bool_dtype(series):
        values = pd.Series(np.nan, index=series.index, dtype="float64")
        return Parsed(values, blank, ~blank)
    if pd.api.types.is_numeric_dtype(series):
        values = series.astype("float64")
        return Parsed(values, blank, pd.Series(False, index=series.index))

    text = text_view(series)
    cleaned = text.str.replace(_NUMERIC_JUNK, "", regex=True).str.removesuffix("%")
    # "(125.50)" accounting negatives
    negative = cleaned.str.fullmatch(r"\(.*\)").fillna(False).astype(bool)
    cleaned = cleaned.where(~negative, "-" + cleaned.str.strip("()"))
    values = pd.to_numeric(cleaned, errors="coerce").astype("float64")
    # Native Python numbers inside object columns (Excel) survive the string round-trip.
    invalid = (~blank & values.isna()).astype(bool)
    return Parsed(values, blank, invalid)


def parse_dates(
    series: pd.Series,
    kind: Literal["date", "datetime"] = "date",
    *,
    dayfirst: bool = True,
) -> Parsed:
    """Parse dates/datetimes.

    Strategy (fast path first):

    1. values that are already ``datetime``/``Timestamp`` objects are kept;
    2. ISO-8601 text (``2026-07-01``, ``2026-07-01 08:30``) is parsed in one pass;
    3. only what remains is parsed with the flexible parser using ``dayfirst``.

    Impossible dates such as ``2026-02-30`` or ``31/13/2026`` are *invalid*, not
    guessed. For ``kind="date"`` the time component is dropped.
    """
    blank = blank_mask(series)
    if pd.api.types.is_datetime64_any_dtype(series):
        values = pd.to_datetime(series)
    else:
        values = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")
        native = series.map(lambda v: isinstance(v, (datetime, date, pd.Timestamp))).astype(bool)
        if native.any():
            values[native] = pd.to_datetime(series[native].astype(object), errors="coerce")

        pending = ~blank & ~native
        if pending.any():
            text = text_view(series[pending])
            iso = pd.to_datetime(text, format="ISO8601", errors="coerce")
            values[pending] = iso
            rest = pending & values.isna()
            if rest.any():
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    flexible = pd.to_datetime(
                        text_view(series[rest]), format="mixed", dayfirst=dayfirst, errors="coerce"
                    )
                values[rest] = flexible

    if kind == "date":
        values = values.dt.normalize()
    invalid = (~blank & values.isna()).astype(bool)
    return Parsed(values, blank, invalid)


def normalize_key(series: pd.Series) -> pd.Series:
    """Case- and whitespace-insensitive comparison key (``"  POWAI "`` → ``"powai"``)."""
    return text_view(series).str.replace(r"\s+", " ", regex=True).str.casefold()
