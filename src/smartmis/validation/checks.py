"""Validation checks.

Each check inspects the data **without modifying it** and returns one or more
:class:`CheckOutcome` objects. Outcomes feed three things:

* the Data Quality Score (via ``dimension``, ``checked`` and ``failed``),
* the valid / invalid row split (via ``row_mask`` when ``invalidates_rows``), and
* the issue log shown to the user (via ``row_mask`` and ``column``).

To add a check, subclass :class:`BaseCheck`, implement :meth:`run`, and pass it
to :class:`~smartmis.validation.engine.Validator` (or append it to
:data:`DEFAULT_CHECKS`).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar, Literal

import pandas as pd

from smartmis.core.config import ColumnRule, DatasetSchema, ValidationSettings
from smartmis.utils.columns import ColumnResolution
from smartmis.utils.parsing import Parsed, blank_mask, normalize_key, parse_dates, parse_numeric

Dimension = Literal["completeness", "validity", "uniqueness", "consistency", "conformity"]
Severity = Literal["error", "warning", "info"]


@dataclass
class CheckOutcome:
    """Raw result of one check on one column (or on the whole table)."""

    name: str
    dimension: Dimension
    severity: Severity
    message: str
    checked: int = 0
    failed: int = 0
    column: str | None = None
    row_mask: pd.Series | None = field(default=None, repr=False)
    invalidates_rows: bool = False
    blocking: bool = False

    @property
    def passed(self) -> bool:
        return self.failed == 0


class CheckContext:
    """Shared state for one validation run, with parsed-column caching."""

    def __init__(
        self,
        frame: pd.DataFrame,
        schema: DatasetSchema,
        resolution: ColumnResolution,
        settings: ValidationSettings,
    ) -> None:
        self.frame = frame
        self.schema = schema
        self.resolution = resolution
        self.settings = settings
        self.row_count = len(frame)
        self._parsed: dict[str, Parsed] = {}
        self._blank: dict[str, pd.Series] = {}

    def present_columns(self) -> list[tuple[str, str, ColumnRule]]:
        """``(schema_name, actual_name, rule)`` for every schema column found."""
        return [
            (name, self.resolution.mapping[name], rule)
            for name, rule in self.schema.columns.items()
            if name in self.resolution.mapping
        ]

    def series(self, schema_column: str) -> pd.Series:
        return self.frame[self.resolution.mapping[schema_column]]

    def blank(self, schema_column: str) -> pd.Series:
        if schema_column not in self._blank:
            self._blank[schema_column] = blank_mask(self.series(schema_column))
        return self._blank[schema_column]

    def parsed(self, schema_column: str) -> Parsed:
        """Numeric or date parse of a column according to its schema type (cached)."""
        if schema_column not in self._parsed:
            rule = self.schema.columns[schema_column]
            series = self.series(schema_column)
            if rule.is_numeric:
                self._parsed[schema_column] = parse_numeric(series)
            elif rule.is_temporal:
                kind: Literal["date", "datetime"] = (
                    "datetime" if rule.type == "datetime" else "date"
                )
                self._parsed[schema_column] = parse_dates(
                    series, kind, dayfirst=self.settings.dayfirst
                )
            else:
                raise TypeError(f"Column '{schema_column}' is not numeric or temporal")
        return self._parsed[schema_column]


def _pct(part: int, whole: int) -> float:
    return round(part / whole * 100, 2) if whole else 0.0


class BaseCheck(ABC):
    """Interface for all checks."""

    name: ClassVar[str]

    @abstractmethod
    def run(self, ctx: CheckContext) -> list[CheckOutcome]:
        """Return zero or more outcomes."""


# -----------------------------------------------------------------------------
# Conformity
# -----------------------------------------------------------------------------


class RequiredColumnsCheck(BaseCheck):
    name = "required_columns"

    def run(self, ctx: CheckContext) -> list[CheckOutcome]:
        required = ctx.schema.required_columns
        missing = ctx.resolution.missing_required
        message = (
            "All required columns are present."
            if not missing
            else f"Missing required column(s): {', '.join(missing)}."
        )
        return [
            CheckOutcome(
                name=self.name,
                dimension="conformity",
                severity="error" if missing else "info",
                message=message,
                checked=len(required),
                failed=len(missing),
                blocking=bool(missing),
            )
        ]


class UnexpectedColumnsCheck(BaseCheck):
    name = "unexpected_columns"

    def run(self, ctx: CheckContext) -> list[CheckOutcome]:
        unexpected = ctx.resolution.unexpected
        if ctx.schema.allow_unexpected_columns:
            if not unexpected:
                return []
            return [
                CheckOutcome(
                    name=self.name,
                    dimension="conformity",
                    severity="info",
                    message=f"Extra column(s) kept but not used by the schema: {', '.join(unexpected)}.",
                )
            ]
        return [
            CheckOutcome(
                name=self.name,
                dimension="conformity",
                severity="error" if unexpected else "info",
                message=(
                    f"Unexpected column(s) not allowed by the schema: {', '.join(unexpected)}."
                    if unexpected
                    else "No unexpected columns."
                ),
                checked=len(ctx.frame.columns),
                failed=len(unexpected),
            )
        ]


class ColumnMappingCheck(BaseCheck):
    """Informational: columns that matched the schema under a different name."""

    name = "column_mapping"

    def run(self, ctx: CheckContext) -> list[CheckOutcome]:
        renamed = ctx.resolution.renamed
        if not renamed:
            return []
        pairs = ", ".join(f"'{src}' → {dst}" for src, dst in renamed.items())
        return [
            CheckOutcome(
                name=self.name,
                dimension="conformity",
                severity="info",
                message=f"Matched by name/alias: {pairs}.",
            )
        ]


# -----------------------------------------------------------------------------
# Completeness
# -----------------------------------------------------------------------------


class EmptyRowsCheck(BaseCheck):
    name = "empty_rows"

    def run(self, ctx: CheckContext) -> list[CheckOutcome]:
        if ctx.row_count == 0:
            return []
        masks = [blank_mask(ctx.frame[c]) for c in ctx.frame.columns]
        empty = pd.concat(masks, axis=1).all(axis=1) if masks else pd.Series(dtype=bool)
        count = int(empty.sum())
        if count == 0:
            return []
        return [
            CheckOutcome(
                name=self.name,
                dimension="completeness",
                severity="warning",
                message=f"{count:,} completely empty row(s).",
                failed=count,
                row_mask=empty,
                invalidates_rows=True,
            )
        ]


class MissingValuesCheck(BaseCheck):
    name = "missing_values"

    def run(self, ctx: CheckContext) -> list[CheckOutcome]:
        outcomes: list[CheckOutcome] = []
        threshold = ctx.settings.missing_value_threshold
        for schema_col, _, rule in ctx.present_columns():
            blank = ctx.blank(schema_col)
            missing = int(blank.sum())
            pct = _pct(missing, ctx.row_count)
            if rule.required:
                outcomes.append(
                    CheckOutcome(
                        name=self.name,
                        dimension="completeness",
                        severity="error" if missing else "info",
                        message=f"'{schema_col}' (required) is blank in {missing:,} row(s) ({pct}%).",
                        checked=ctx.row_count,
                        failed=missing,
                        column=schema_col,
                        row_mask=blank if missing else None,
                        invalidates_rows=True,
                    )
                )
            elif missing:
                default = (
                    f" Default {rule.default!r} will be applied."
                    if rule.default is not None
                    else ""
                )
                outcomes.append(
                    CheckOutcome(
                        name=self.name,
                        dimension="completeness",
                        severity="warning" if pct > threshold else "info",
                        message=f"'{schema_col}' (optional) is blank in {missing:,} row(s) ({pct}%).{default}",
                        column=schema_col,
                        row_mask=blank,
                    )
                )
        return outcomes


# -----------------------------------------------------------------------------
# Validity
# -----------------------------------------------------------------------------


class _ParseCheck(BaseCheck):
    kind_label: ClassVar[str]

    def _applies(self, rule: ColumnRule) -> bool:
        raise NotImplementedError

    def run(self, ctx: CheckContext) -> list[CheckOutcome]:
        outcomes: list[CheckOutcome] = []
        for schema_col, _, rule in ctx.present_columns():
            if not self._applies(rule):
                continue
            parsed = ctx.parsed(schema_col)
            checked = int((~parsed.blank).sum())
            failed = int(parsed.invalid.sum())
            outcomes.append(
                CheckOutcome(
                    name=self.name,
                    dimension="validity",
                    severity="error" if failed else "info",
                    message=(
                        f"'{schema_col}' has {failed:,} value(s) that are not valid {self.kind_label} "
                        f"({_pct(failed, checked)}% of filled cells)."
                        if failed
                        else f"'{schema_col}' values are valid {self.kind_label}."
                    ),
                    checked=checked,
                    failed=failed,
                    column=schema_col,
                    row_mask=parsed.invalid if failed else None,
                    invalidates_rows=True,
                )
            )
        return outcomes


class InvalidDatesCheck(_ParseCheck):
    name = "invalid_dates"
    kind_label = "dates"

    def _applies(self, rule: ColumnRule) -> bool:
        return rule.is_temporal


class InvalidNumbersCheck(_ParseCheck):
    name = "invalid_numbers"
    kind_label = "numbers"

    def _applies(self, rule: ColumnRule) -> bool:
        return rule.is_numeric


class InvalidCategoriesCheck(BaseCheck):
    name = "invalid_categories"

    def run(self, ctx: CheckContext) -> list[CheckOutcome]:
        outcomes: list[CheckOutcome] = []
        for schema_col, _, rule in ctx.present_columns():
            if not rule.allowed:
                continue
            allowed = {v.strip().casefold() for v in rule.allowed}
            blank = ctx.blank(schema_col)
            keys = normalize_key(ctx.series(schema_col))
            invalid = (~blank & ~keys.isin(allowed)).fillna(False).astype(bool)
            failed = int(invalid.sum())
            checked = int((~blank).sum())
            examples = ", ".join(
                sorted(ctx.series(schema_col)[invalid].astype(str).str.strip().unique()[:5])
            )
            outcomes.append(
                CheckOutcome(
                    name=self.name,
                    dimension="validity",
                    severity="error" if failed else "info",
                    message=(
                        f"'{schema_col}' has {failed:,} value(s) outside the allowed list "
                        f"({', '.join(rule.allowed)}); found: {examples}."
                        if failed
                        else f"'{schema_col}' values are all in the allowed list."
                    ),
                    checked=checked,
                    failed=failed,
                    column=schema_col,
                    row_mask=invalid if failed else None,
                    invalidates_rows=True,
                )
            )
        return outcomes


# -----------------------------------------------------------------------------
# Consistency
# -----------------------------------------------------------------------------


class RangeCheck(BaseCheck):
    """``min`` / ``max`` limits: negative quantities, percentages outside 0–100, etc."""

    name = "value_range"

    def run(self, ctx: CheckContext) -> list[CheckOutcome]:
        outcomes: list[CheckOutcome] = []
        for schema_col, _, rule in ctx.present_columns():
            if not rule.is_numeric or (rule.min is None and rule.max is None):
                continue
            parsed = ctx.parsed(schema_col)
            values = parsed.values
            checked = int(parsed.valid.sum())
            below = (parsed.valid & (values < rule.min)) if rule.min is not None else None
            above = (parsed.valid & (values > rule.max)) if rule.max is not None else None

            if rule.type == "percent":
                parts = [m for m in (below, above) if m is not None]
                mask = parts[0] if len(parts) == 1 else parts[0] | parts[1]
                sides = [
                    (
                        "invalid_percentages",
                        mask,
                        f"outside {rule.min:g}–{rule.max:g}" if rule.max is not None else "",
                    )
                ]
            else:
                sides = []
                if below is not None:
                    label = "negative_values" if rule.min == 0 else "below_minimum"
                    sides.append((label, below, f"below {rule.min:g}"))
                if above is not None:
                    sides.append(("above_maximum", above, f"above {rule.max:g}"))

            for name, mask, text in sides:
                failed = int(mask.sum())
                outcomes.append(
                    CheckOutcome(
                        name=name,
                        dimension="consistency",
                        severity="error" if failed else "info",
                        message=(
                            f"'{schema_col}' has {failed:,} value(s) {text or 'out of range'}."
                            if failed
                            else f"'{schema_col}' values are within range."
                        ),
                        checked=checked,
                        failed=failed,
                        column=schema_col,
                        row_mask=mask if failed else None,
                        invalidates_rows=True,
                    )
                )
        return outcomes


class ConsistencyRulesCheck(BaseCheck):
    """Cross-column rules from ``consistency_rules`` in the schema."""

    name = "consistency_rule"

    def run(self, ctx: CheckContext) -> list[CheckOutcome]:
        outcomes: list[CheckOutcome] = []
        for rule in ctx.schema.consistency_rules:
            if (
                rule.column not in ctx.resolution.mapping
                or rule.reference not in ctx.resolution.mapping
            ):
                continue
            left, right = ctx.parsed(rule.column), ctx.parsed(rule.reference)
            both = left.valid & right.valid
            if rule.type == "not_before":
                violation = both & (left.values < right.values)
                text = f"'{rule.column}' is earlier than '{rule.reference}'"
            else:
                violation = both & (left.values > right.values)
                text = f"'{rule.column}' is later than '{rule.reference}'"
            failed = int(violation.sum())
            outcomes.append(
                CheckOutcome(
                    name=self.name,
                    dimension="consistency",
                    severity="error" if failed else "info",
                    message=(
                        f"{text} in {failed:,} row(s). {rule.description}".strip()
                        if failed
                        else f"Rule passed: {rule.description or text + ' never'}."
                    ),
                    checked=int(both.sum()),
                    failed=failed,
                    column=rule.column,
                    row_mask=violation if failed else None,
                    invalidates_rows=True,
                )
            )
        return outcomes


# -----------------------------------------------------------------------------
# Uniqueness
# -----------------------------------------------------------------------------


def exact_duplicate_mask(frame: pd.DataFrame) -> pd.Series:
    """Rows identical to an earlier row across all columns (whitespace-insensitive)."""
    if frame.empty:
        return pd.Series(False, index=frame.index)
    normalised = frame.apply(lambda s: s.astype("string").str.strip())
    return normalised.duplicated(keep="first")


def duplicate_key_mask(
    frame: pd.DataFrame, key_columns: list[str], exclude: pd.Series | None = None
) -> pd.Series:
    """Rows whose business key repeats an earlier row (ignoring rows in ``exclude``)."""
    keys = pd.concat([normalize_key(frame[c]) for c in key_columns], axis=1)
    complete = keys.notna().all(axis=1) & (keys != "").all(axis=1)
    excluded = exclude if exclude is not None else pd.Series(False, index=frame.index)
    candidates = complete & ~excluded
    mask = pd.Series(False, index=frame.index)
    if candidates.any():
        mask[candidates] = keys[candidates].duplicated(keep="first")
    return mask


class DuplicateRowsCheck(BaseCheck):
    name = "duplicate_rows"

    def run(self, ctx: CheckContext) -> list[CheckOutcome]:
        if not ctx.settings.duplicate_check or ctx.row_count == 0:
            return []
        mask = exact_duplicate_mask(ctx.frame)
        failed = int(mask.sum())
        return [
            CheckOutcome(
                name=self.name,
                dimension="uniqueness",
                severity="error" if failed else "info",
                message=(
                    f"{failed:,} exact duplicate row(s) ({_pct(failed, ctx.row_count)}%)."
                    if failed
                    else "No exact duplicate rows."
                ),
                checked=ctx.row_count,
                failed=failed,
                row_mask=mask if failed else None,
                invalidates_rows=True,
            )
        ]


class DuplicateKeysCheck(BaseCheck):
    """Same business key, different values. Denominator is shared with DuplicateRowsCheck."""

    name = "duplicate_keys"

    def run(self, ctx: CheckContext) -> list[CheckOutcome]:
        key = list(ctx.schema.business_key)
        if not ctx.settings.duplicate_check or not key or ctx.row_count == 0:
            return []
        if any(k not in ctx.resolution.mapping for k in key):
            return []
        actual = [ctx.resolution.mapping[k] for k in key]
        mask = duplicate_key_mask(ctx.frame, actual, exclude=exact_duplicate_mask(ctx.frame))
        failed = int(mask.sum())
        return [
            CheckOutcome(
                name=self.name,
                dimension="uniqueness",
                severity="error" if failed else "info",
                message=(
                    f"{failed:,} row(s) repeat an existing business key ({' + '.join(key)}) "
                    "with different values."
                    if failed
                    else f"Business key ({' + '.join(key)}) is unique."
                ),
                checked=0,  # rows already counted by duplicate_rows
                failed=failed,
                row_mask=mask if failed else None,
                invalidates_rows=True,
            )
        ]


DEFAULT_CHECKS: tuple[BaseCheck, ...] = (
    RequiredColumnsCheck(),
    UnexpectedColumnsCheck(),
    ColumnMappingCheck(),
    EmptyRowsCheck(),
    MissingValuesCheck(),
    InvalidDatesCheck(),
    InvalidNumbersCheck(),
    InvalidCategoriesCheck(),
    RangeCheck(),
    ConsistencyRulesCheck(),
    DuplicateRowsCheck(),
    DuplicateKeysCheck(),
)
