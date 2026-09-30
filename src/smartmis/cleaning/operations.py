"""Reusable cleaning operations.

Each function takes a DataFrame (and a :class:`ChangeTracker`), returns a *new*
DataFrame, and records exactly what it changed. They are independent of any
dataset schema unless a schema is passed explicitly, so they can be reused for
ad-hoc cleaning as well.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

import numpy as np
import pandas as pd

from smartmis.cleaning.summary import SOURCE_ROW, ChangeTracker
from smartmis.core.config import CategoryCase, ColumnRule, DatasetSchema
from smartmis.utils.columns import resolve_columns, unique_standardized_names
from smartmis.utils.parsing import normalize_key, parse_dates, parse_numeric


def _text_columns(frame: pd.DataFrame) -> list[str]:
    return [
        c
        for c in frame.columns
        if c != SOURCE_ROW and (frame[c].dtype == object or pd.api.types.is_string_dtype(frame[c]))
    ]


def standardize_column_names(
    frame: pd.DataFrame, schema: DatasetSchema | None, tracker: ChangeTracker
) -> tuple[pd.DataFrame, dict[str, str]]:
    """Rename schema columns (incl. aliases) to their schema names; snake_case the rest."""
    columns = [c for c in frame.columns if c != SOURCE_ROW]
    mapping: dict[str, str] = {}
    if schema is not None:
        resolution = resolve_columns(columns, schema)
        mapping.update({src: dst for dst, src in resolution.mapping.items()})
    others = [c for c in columns if c not in mapping]
    taken = set(mapping.values())
    for original, new in zip(others, unique_standardized_names(others), strict=True):
        candidate, n = new, 2
        while candidate in taken:
            candidate, n = f"{new}_{n}", n + 1
        mapping[original] = candidate
        taken.add(candidate)
    renamed = {k: v for k, v in mapping.items() if k != v}
    tracker.record_step(
        "Standardise column names",
        f"Renamed {len(renamed)} column(s)"
        + (
            f": {', '.join(f'{k!r} → {v}' for k, v in list(renamed.items())[:6])}"
            if renamed
            else "."
        ),
        rows=0,
    )
    return frame.rename(columns=mapping), renamed


def trim_whitespace(frame: pd.DataFrame, tracker: ChangeTracker) -> pd.DataFrame:
    """Strip leading/trailing spaces and collapse repeated inner spaces in text columns."""
    result = frame.copy()
    total = 0
    for column in _text_columns(result):
        original = result[column]
        text = original.astype("string")
        cleaned = text.str.strip().str.replace(r"\s+", " ", regex=True)
        changed = (cleaned != text).fillna(False).astype(bool) & text.notna()
        total += tracker.record_cells("Trim whitespace", column, changed, original, cleaned)
        result[column] = original.where(~changed, cleaned.astype(object))
    tracker.record_step("Trim whitespace", f"Trimmed {total:,} text cell(s).", rows=0, cells=total)
    return result


def replace_placeholders(
    frame: pd.DataFrame, placeholders: tuple[str, ...], tracker: ChangeTracker
) -> pd.DataFrame:
    """Turn blanks and placeholder tokens (``N/A``, ``-``, ``#REF!`` …) into real missing values."""
    result = frame.copy()
    tokens = set(placeholders)
    total = 0
    for column in _text_columns(result):
        original = result[column]
        text = original.astype("string").str.strip()
        mask = ((text == "") | text.str.lower().isin(tokens)).fillna(False).astype(bool)
        total += tracker.record_cells("Placeholders → missing", column, mask, original, pd.NA)
        result[column] = original.where(~mask, None)
    tracker.record_step(
        "Placeholders → missing",
        f"Converted {total:,} blank/placeholder cell(s) (e.g. 'N/A', '-') to missing.",
        rows=0,
        cells=total,
    )
    return result


def remove_duplicate_rows(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split exact duplicates (ignoring ``_source_row``) from the first occurrence."""
    data_columns = [c for c in frame.columns if c != SOURCE_ROW]
    if frame.empty:
        return frame, frame.iloc[0:0]
    normalised = frame[data_columns].apply(lambda s: s.astype("string").str.strip())
    duplicates = normalised.duplicated(keep="first")
    return frame[~duplicates], frame[duplicates]


def _canonical_spellings(series: pd.Series) -> dict[str, str]:
    """Most frequent spelling per case/space-insensitive key.

    Ties prefer mixed case (``"Powai"`` over ``"POWAI"``/``"powai"``), then alphabetical.
    """
    present = series.dropna().astype(str)
    if present.empty:
        return {}
    counts = (
        pd.DataFrame({"key": normalize_key(present), "value": present})
        .groupby(["key", "value"])
        .size()
        .reset_index(name="n")
    )
    shouty_or_flat = counts["value"].str.isupper() | counts["value"].str.islower()
    counts = counts.assign(style=shouty_or_flat.astype(int)).sort_values(
        ["key", "n", "style", "value"], ascending=[True, False, True, True]
    )
    return dict(counts.drop_duplicates("key")[["key", "value"]].itertuples(index=False, name=None))


def _apply_case(values: pd.Series, case: CategoryCase) -> pd.Series:
    if case == "title":
        return values.str.title()
    if case == "upper":
        return values.str.upper()
    if case == "lower":
        return values.str.lower()
    return values


def standardize_categories(
    frame: pd.DataFrame,
    schema: DatasetSchema,
    case: CategoryCase,
    tracker: ChangeTracker,
) -> pd.DataFrame:
    """Unify spellings of category values.

    * Columns with an ``allowed`` list map case-insensitively onto the list.
    * Other category columns map every variant onto its most common spelling,
      so ``"POWAI"`` and ``" powai "`` become ``"Powai"`` when that is the
      majority form.
    """
    result = frame.copy()
    total = 0
    for column, rule in schema.columns.items():
        if rule.type != "category" or column not in result.columns:
            continue
        original = result[column]
        keys = normalize_key(original)
        if rule.allowed:
            canon = {v.strip().casefold(): v for v in rule.allowed}
        else:
            canon = _canonical_spellings(original)
        mapped = keys.map(canon)
        standardised = _apply_case(mapped.astype("string"), case) if not rule.allowed else mapped
        standardised = standardised.where(standardised.notna(), original.astype("string"))
        changed = (standardised != original.astype("string")).fillna(False).astype(bool)
        total += tracker.record_cells(
            "Standardise categories", column, changed, original, standardised
        )
        result[column] = original.where(~changed, standardised.astype(object))
    tracker.record_step(
        "Standardise categories",
        f"Unified {total:,} category value(s) to a single spelling.",
        rows=0,
        cells=total,
    )
    return result


def convert_types(
    frame: pd.DataFrame,
    schema: DatasetSchema,
    tracker: ChangeTracker,
    *,
    dayfirst: bool,
) -> tuple[pd.DataFrame, dict[str, pd.Series]]:
    """Convert schema columns to proper dtypes.

    Returns the converted frame and, per column, a mask of cells whose value
    could not be converted (they become missing and are logged).
    """
    result = frame.copy()
    invalid: dict[str, pd.Series] = {}
    total = 0
    for column, rule in schema.columns.items():
        if column not in result.columns:
            continue
        original = result[column]
        if rule.is_numeric:
            parsed = parse_numeric(original)
            values: pd.Series = parsed.values
            if rule.type == "integer":
                valid = values.dropna()
                if bool((valid % 1 == 0).all()):
                    values = values.astype("Int64")
        elif rule.is_temporal:
            kind: Literal["date", "datetime"] = "datetime" if rule.type == "datetime" else "date"
            parsed = parse_dates(original, kind, dayfirst=dayfirst)
            values = parsed.values
        else:
            result[column] = original.astype("string")
            continue
        invalid[column] = parsed.invalid
        total += tracker.record_cells("Convert data types", column, parsed.invalid, original, pd.NA)
        result[column] = values
    tracker.record_step(
        "Convert data types",
        f"Converted schema columns to number/date types; {total:,} unconvertible value(s) set to missing.",
        rows=int(pd.concat(invalid.values(), axis=1).any(axis=1).sum()) if invalid else 0,
        cells=total,
    )
    return result, invalid


def fill_defaults(
    frame: pd.DataFrame, schema: DatasetSchema, tracker: ChangeTracker
) -> pd.DataFrame:
    """Fill missing values in *optional* columns that declare a ``default``."""
    result = frame.copy()
    total = 0
    for column, rule in schema.columns.items():
        if rule.default is None or rule.required or column not in result.columns:
            continue
        missing = result[column].isna()
        total += tracker.record_cells(
            "Fill defaults", column, missing, result[column], rule.default
        )
        result[column] = result[column].fillna(rule.default)
    tracker.record_step(
        "Fill defaults",
        f"Filled {total:,} missing optional value(s) with configured defaults.",
        rows=0,
        cells=total,
    )
    return result


def row_problems(
    frame: pd.DataFrame,
    schema: DatasetSchema,
    invalid_cells: dict[str, pd.Series],
    *,
    quarantine_duplicate_keys: bool,
) -> pd.Series:
    """Reason text per row for rows that cannot be used safely (empty string = OK).

    Operates on the *converted* frame, so every rule is evaluated on typed values.
    """
    reasons = pd.Series("", index=frame.index, dtype=object)

    def add(mask: pd.Series, text: str) -> None:
        mask = mask.reindex(frame.index, fill_value=False).fillna(False).astype(bool)
        if mask.any():
            current = reasons[mask]
            reasons[mask] = np.where(current == "", text, current + "; " + text)

    for column, rule in schema.columns.items():
        if column not in frame.columns:
            continue
        series = frame[column]
        if column in invalid_cells:
            add(invalid_cells[column], f"invalid {rule.type} in {column}")
        if rule.required:
            add(
                series.isna() & ~invalid_cells.get(column, pd.Series(False, index=frame.index)),
                f"missing {column}",
            )
        if rule.is_numeric:
            _range_reasons(series, column, rule, add)
        if rule.allowed:
            allowed = set(rule.allowed)
            add(
                series.notna() & ~series.astype("string").isin(allowed),
                f"{column} not in allowed list",
            )

    for rule_cfg in schema.consistency_rules:
        if rule_cfg.column in frame.columns and rule_cfg.reference in frame.columns:
            left, right = frame[rule_cfg.column], frame[rule_cfg.reference]
            if rule_cfg.type == "not_before":
                add(left < right, f"{rule_cfg.column} before {rule_cfg.reference}")
            else:
                add(left > right, f"{rule_cfg.column} after {rule_cfg.reference}")

    key = [k for k in schema.business_key if k in frame.columns]
    if quarantine_duplicate_keys and key and len(key) == len(schema.business_key):
        usable = reasons == ""
        keys = pd.concat([normalize_key(frame[k].astype("string")) for k in key], axis=1)
        complete = keys.notna().all(axis=1) & usable
        dup = pd.Series(False, index=frame.index)
        dup[complete] = keys[complete].duplicated(keep="first")
        add(dup, f"duplicate business key ({' + '.join(key)})")
    return reasons


def _range_reasons(
    series: pd.Series, column: str, rule: ColumnRule, add: Callable[[pd.Series, str], None]
) -> None:
    numeric = pd.to_numeric(series, errors="coerce")
    if rule.min is not None:
        label = "negative" if rule.min == 0 else f"below {rule.min:g}"
        add(numeric < rule.min, f"{column} {label}")
    if rule.max is not None:
        add(numeric > rule.max, f"{column} above {rule.max:g}")
