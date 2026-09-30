"""Column-name normalisation and schema matching.

Real-world files rarely use the exact names in ``validation_rules.yaml``.
``"Invoice No."``, ``" invoice_no "`` and ``"InvoiceNo"`` should all satisfy the
``invoice_no`` requirement, and ``aliases`` let an analyst map ``qty`` →
``quantity`` without code changes.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from smartmis.core.config import DatasetSchema

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^0-9a-zA-Z]+")
_SYMBOL_WORDS = {"%": " pct ", "#": " no ", "&": " and "}


def standardize_column_name(name: object) -> str:
    """Convert a header to ``snake_case``.

    >>> standardize_column_name("Invoice No.")
    'invoice_no'
    >>> standardize_column_name("Discount %")
    'discount_pct'
    >>> standardize_column_name("unitPrice")
    'unit_price'
    """
    text = str(name).strip()
    for symbol, word in _SYMBOL_WORDS.items():
        text = text.replace(symbol, word)
    text = _CAMEL_BOUNDARY.sub("_", text)
    text = _NON_ALNUM.sub("_", text).strip("_").lower()
    return text or "column"


def unique_standardized_names(names: Iterable[object]) -> list[str]:
    """Standardise names and de-duplicate collisions with ``_2``, ``_3``…"""
    result: list[str] = []
    seen: dict[str, int] = {}
    for name in names:
        base = standardize_column_name(name)
        count = seen.get(base, 0) + 1
        seen[base] = count
        result.append(base if count == 1 else f"{base}_{count}")
    return result


@dataclass(frozen=True)
class ColumnResolution:
    """How the columns of a DataFrame line up with a dataset schema."""

    mapping: dict[str, str]  # schema column -> actual DataFrame column
    missing_required: tuple[str, ...]
    missing_optional: tuple[str, ...]
    unexpected: tuple[str, ...]
    renamed: dict[str, str] = field(default_factory=dict)  # actual -> schema, when different

    @property
    def present(self) -> tuple[str, ...]:
        return tuple(self.mapping)


def resolve_columns(columns: Iterable[object], schema: DatasetSchema) -> ColumnResolution:
    """Match DataFrame columns to schema columns by standardised name, then alias."""
    actual = [str(c) for c in columns]
    by_standard: dict[str, str] = {}
    for column in actual:
        by_standard.setdefault(standardize_column_name(column), column)

    mapping: dict[str, str] = {}
    used: set[str] = set()
    for schema_col, rule in schema.columns.items():
        for candidate in (schema_col, *rule.aliases):
            source = by_standard.get(standardize_column_name(candidate))
            if source is not None and source not in used:
                mapping[schema_col] = source
                used.add(source)
                break

    missing_required = tuple(
        c for c, rule in schema.columns.items() if rule.required and c not in mapping
    )
    missing_optional = tuple(
        c for c, rule in schema.columns.items() if not rule.required and c not in mapping
    )
    unexpected = tuple(c for c in actual if c not in used)
    renamed = {src: dst for dst, src in mapping.items() if src != dst}
    return ColumnResolution(mapping, missing_required, missing_optional, unexpected, renamed)
