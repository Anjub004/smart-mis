"""Tests for shared column and parsing utilities."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from smartmis.core.config import AppConfig
from smartmis.utils.columns import (
    resolve_columns,
    standardize_column_name,
    unique_standardized_names,
)
from smartmis.utils.parsing import normalize_key, parse_dates, parse_numeric


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Invoice No.", "invoice_no"),
        ("  Unit Price ", "unit_price"),
        ("Discount %", "discount_pct"),
        ("unitPrice", "unit_price"),
        ("Sales & Returns", "sales_and_returns"),
        ("Bill #", "bill_no"),
        ("***", "column"),
    ],
)
def test_standardize_column_name(raw: str, expected: str) -> None:
    assert standardize_column_name(raw) == expected


def test_unique_standardized_names() -> None:
    assert unique_standardized_names(["Qty", "qty ", "QTY"]) == ["qty", "qty_2", "qty_3"]


def test_resolve_columns(app_config: AppConfig) -> None:
    schema = app_config.validation_rules.datasets["sales"]
    resolution = resolve_columns(["Invoice No", "Date", "Qty", "Price", "Remarks"], schema)
    assert resolution.mapping == {
        "invoice_no": "Invoice No",
        "invoice_date": "Date",
        "quantity": "Qty",
        "unit_price": "Price",
    }
    assert set(resolution.missing_required) == {"branch", "category", "sku"}
    assert resolution.unexpected == ("Remarks",)
    assert resolution.renamed["Qty"] == "quantity"


class TestParseNumeric:
    def test_text_values(self) -> None:
        series = pd.Series(
            ["1,200.50", "12%", " 7 ", "(10)", "₹ 99", "abc", None, ""], dtype=object
        )
        parsed = parse_numeric(series)
        assert parsed.values.tolist()[:5] == [1200.5, 12.0, 7.0, -10.0, 99.0]
        assert parsed.invalid.tolist() == [False] * 5 + [True, False, False]
        assert parsed.blank.tolist() == [False] * 6 + [True, True]

    def test_native_numbers(self) -> None:
        parsed = parse_numeric(pd.Series([1, 2.5, None]))
        assert not parsed.invalid.any()
        assert parsed.blank.tolist() == [False, False, True]

    def test_mixed_object_column(self) -> None:
        parsed = parse_numeric(pd.Series([3, "4", datetime(2026, 1, 1)], dtype=object))
        assert parsed.values.tolist()[:2] == [3.0, 4.0]
        assert parsed.invalid.tolist() == [False, False, True]


class TestParseDates:
    def test_formats_and_invalid(self) -> None:
        series = pd.Series(
            [
                "2026-07-01",
                "01/08/2026",
                "2026-02-30",
                "31/13/2026",
                "TBD",
                None,
                datetime(2026, 7, 3, 10, 5),
                "2026-07-01 08:30",
            ],
            dtype=object,
        )
        parsed = parse_dates(series, "datetime", dayfirst=True)
        assert parsed.values.iloc[1] == pd.Timestamp("2026-08-01")
        assert parsed.values.iloc[6] == pd.Timestamp("2026-07-03 10:05")
        assert parsed.invalid.tolist() == [False, False, True, True, True, False, False, False]

    def test_monthfirst(self) -> None:
        parsed = parse_dates(pd.Series(["01/08/2026"]), dayfirst=False)
        assert parsed.values.iloc[0] == pd.Timestamp("2026-01-08")

    def test_date_kind_drops_time(self) -> None:
        parsed = parse_dates(pd.Series(["2026-07-01 18:45"]), "date")
        assert parsed.values.iloc[0] == pd.Timestamp("2026-07-01")

    def test_datetime_dtype_passthrough(self) -> None:
        parsed = parse_dates(pd.Series(pd.to_datetime(["2026-07-01", None])), "date")
        assert parsed.blank.tolist() == [False, True]
        assert not parsed.invalid.any()


def test_normalize_key() -> None:
    assert normalize_key(pd.Series(["  Navi   MUMBAI "])).iloc[0] == "navi mumbai"
