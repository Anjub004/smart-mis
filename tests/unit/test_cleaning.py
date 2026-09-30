"""Tests for the cleaning engine."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from smartmis.cleaning import ISSUES, REASON, SOURCE_ROW, Cleaner
from smartmis.core.config import AppConfig
from smartmis.validation import Validator
from tests.unit.test_validation import COLUMNS, ROWS


@pytest.fixture
def sales_frame() -> pd.DataFrame:
    return pd.DataFrame(ROWS, columns=COLUMNS, dtype=object)


@pytest.fixture
def cleaner(app_config: AppConfig) -> Cleaner:
    return Cleaner.from_config(app_config)


def _with(app_config: AppConfig, **changes: object) -> Cleaner:
    settings = app_config.settings.cleaning.model_copy(update=changes)
    return Cleaner(app_config.validation_rules, settings)


class TestFixture:
    def test_quarantine_matches_validation(
        self, cleaner: Cleaner, sales_frame: pd.DataFrame, app_config: AppConfig
    ) -> None:
        result = cleaner.clean(sales_frame, "sales")
        reasons = dict(zip(result.quarantine[SOURCE_ROW], result.quarantine[REASON], strict=True))
        assert reasons == {
            4: "exact duplicate of an earlier row",
            5: "invalid date in invoice_date",
            6: "missing branch",
            7: "quantity negative",
            8: "invalid float in unit_price",
            9: "discount_pct above 100",
            10: "duplicate business key (invoice_no + sku)",
        }
        assert result.data[SOURCE_ROW].tolist() == [1, 2, 3]
        invalid = Validator.from_config(app_config).validate(sales_frame, "sales").invalid_rows
        assert result.summary.rows_removed == invalid

    def test_quarantine_keeps_original_values(
        self, cleaner: Cleaner, sales_frame: pd.DataFrame
    ) -> None:
        quarantine = cleaner.clean(sales_frame, "sales").quarantine
        row8 = quarantine[quarantine[SOURCE_ROW] == 8].iloc[0]
        assert row8["unit_price"] == "abc"
        assert list(quarantine.columns) == [SOURCE_ROW, *COLUMNS, REASON]

    def test_types_after_cleaning(self, cleaner: Cleaner, sales_frame: pd.DataFrame) -> None:
        data = cleaner.clean(sales_frame, "sales").data
        assert pd.api.types.is_datetime64_any_dtype(data["invoice_date"])
        assert str(data["quantity"].dtype) == "Int64"
        assert data["unit_price"].dtype == "float64"
        assert data["quantity"].sum() == 6

    def test_summary(self, cleaner: Cleaner, sales_frame: pd.DataFrame) -> None:
        summary = cleaner.clean(sales_frame, "sales").summary
        assert (summary.rows_before, summary.rows_after, summary.rows_removed) == (10, 3, 7)
        assert summary.removed_by_reason["exact duplicate of an earlier row"] == 1
        text = summary.render_text()
        for label in (
            "Before Cleaning:",
            "After Cleaning:",
            "Records Removed:",
            "Records Modified:",
        ):
            assert label in text
        assert {"Step", "What happened"} <= set(summary.steps_frame().columns)

    def test_input_not_modified(self, cleaner: Cleaner, sales_frame: pd.DataFrame) -> None:
        before = sales_frame.copy()
        cleaner.clean(sales_frame, "sales")
        pd.testing.assert_frame_equal(sales_frame, before)

    def test_cleaned_data_revalidates_clean(
        self, cleaner: Cleaner, sales_frame: pd.DataFrame, app_config: AppConfig
    ) -> None:
        data = cleaner.clean(sales_frame, "sales").data.drop(columns=[SOURCE_ROW])
        report = Validator.from_config(app_config).validate(data, "sales")
        assert report.quality_score == 100.0
        assert report.invalid_rows == 0


class TestOperations:
    def test_headers_whitespace_placeholders_and_categories(self, cleaner: Cleaner) -> None:
        frame = pd.DataFrame(
            {
                "Invoice No.": ["A1", "A2", "A3", "A4"],
                "Invoice Date": ["2026-07-01"] * 4,
                "Branch": ["Powai", "  POWAI ", "powai", "Thane"],
                "Category": ["Grocery"] * 4,
                "SKU": ["S1", "S2", "S3", "S4"],
                "Qty": ["1", "2", "3", "4"],
                "Price": ["10", "10", "10", "10"],
                "Discount %": ["N/A", "5", "-", ""],
                "Store  Remarks": ["ok", " fine  ", "#REF!", None],
            }
        )
        result = cleaner.clean(frame, "sales")
        data, summary = result.data, result.summary
        assert list(data.columns) == [
            SOURCE_ROW,
            "invoice_no",
            "invoice_date",
            "branch",
            "category",
            "sku",
            "quantity",
            "unit_price",
            "discount_pct",
            "store_remarks",
        ]
        assert summary.columns_renamed["Qty"] == "quantity"
        assert data["branch"].tolist() == ["Powai", "Powai", "Powai", "Thane"]
        assert data["discount_pct"].tolist() == [0.0, 5.0, 0.0, 0.0]  # default applied
        assert data["store_remarks"].tolist()[:2] == ["ok", "fine"]
        assert pd.isna(data["store_remarks"].iloc[2])
        log = summary.change_log
        assert {
            "Trim whitespace",
            "Placeholders → missing",
            "Standardise categories",
            "Fill defaults",
        } <= set(log["step"])
        branch_change = log[(log["column"] == "branch") & (log["step"] == "Standardise categories")]
        assert set(branch_change["old_value"]) == {"POWAI", "powai"}
        assert summary.rows_modified == 4

    def test_allowed_values_are_canonicalised(self, cleaner: Cleaner) -> None:
        orders = pd.DataFrame(
            {
                "request_id": ["R1", "R2", "R3"],
                "branch": ["Powai"] * 3,
                "department": ["IT"] * 3,
                "priority": ["HIGH", " Critical", "urgent"],
                "created_at": ["2026-07-01 09:00"] * 3,
                "completed_at": ["2026-07-01 10:00", None, None],
                "status": ["Completed", "open", "OPEN"],
            }
        )
        result = cleaner.clean(orders, "orders")
        assert result.data["priority"].tolist() == ["high", "critical"]
        assert result.data["status"].tolist() == ["completed", "open"]
        assert result.quarantine[REASON].tolist() == ["priority not in allowed list"]

    def test_keep_mode_flags_instead_of_removing(
        self, app_config: AppConfig, sales_frame: pd.DataFrame
    ) -> None:
        result = _with(app_config, invalid_rows="keep").clean(sales_frame, "sales")
        # exact duplicate still removed; the other 6 problem rows are kept and flagged
        assert len(result.data) == 9
        assert result.data[ISSUES].notna().sum() == 6
        assert result.summary.rows_removed == 1

    def test_duplicate_keys_keep(self, app_config: AppConfig, sales_frame: pd.DataFrame) -> None:
        result = _with(app_config, duplicate_keys="keep").clean(sales_frame, "sales")
        assert 10 in result.data[SOURCE_ROW].tolist()

    def test_duplicates_not_removed_when_disabled(
        self, app_config: AppConfig, sales_frame: pd.DataFrame
    ) -> None:
        result = _with(app_config, remove_duplicate_rows=False).clean(sales_frame, "sales")
        # row 4 is then caught by the business-key rule instead
        reason = result.quarantine.set_index(SOURCE_ROW).loc[4, REASON]
        assert reason.startswith("duplicate business key")

    def test_category_case_option(self, app_config: AppConfig) -> None:
        frame = pd.DataFrame(
            {
                "invoice_no": ["A1", "A2"],
                "invoice_date": ["2026-07-01"] * 2,
                "branch": ["navi mumbai", "NAVI MUMBAI"],
                "category": ["grocery"] * 2,
                "sku": ["S1", "S2"],
                "quantity": ["1", "1"],
                "unit_price": ["1", "1"],
            }
        )
        data = _with(app_config, category_case="title").clean(frame, "sales").data
        assert data["branch"].tolist() == ["Navi Mumbai", "Navi Mumbai"]
        assert data["category"].tolist() == ["Grocery", "Grocery"]

    def test_change_log_limit(self, app_config: AppConfig, sales_frame: pd.DataFrame) -> None:
        messy = sales_frame.assign(branch=sales_frame["branch"].map(lambda b: f"  {b} "))
        result = _with(app_config, change_log_limit=3).clean(messy, "sales")
        assert len(result.summary.change_log) == 3
        assert result.summary.cells_modified > 3

    def test_excel_native_values(self, cleaner: Cleaner) -> None:
        frame = pd.DataFrame(
            {
                "invoice_no": ["A1", "A2"],
                "invoice_date": [datetime(2026, 7, 1, 0, 0), datetime(2026, 7, 2)],
                "branch": ["Powai", "Thane"],
                "category": ["Grocery", "FMCG"],
                "sku": ["S1", "S2"],
                "quantity": [2, 3.0],
                "unit_price": [10.5, 20],
            },
            dtype=object,
        )
        data = cleaner.clean(frame, "sales").data
        assert data["invoice_date"].tolist() == [
            pd.Timestamp("2026-07-01"),
            pd.Timestamp("2026-07-02"),
        ]
        assert data["quantity"].tolist() == [2, 3]
        assert data["unit_price"].tolist() == [10.5, 20.0]

    def test_empty_frame(self, cleaner: Cleaner) -> None:
        result = cleaner.clean(pd.DataFrame(columns=COLUMNS), "sales")
        assert result.data.empty and result.quarantine.empty
        assert result.summary.rows_before == 0


def test_sample_data_round_trip(app_config: AppConfig) -> None:
    from smartmis.samples import SampleDataGenerator

    dataset = SampleDataGenerator(seed=5, scale=0.3).generate()
    validator, cleaner = Validator.from_config(app_config), Cleaner.from_config(app_config)
    for name in ("sales", "orders", "inventory"):
        frame = dataset.tables()[name]
        report = validator.validate(frame, name)
        result = cleaner.clean(frame, name)
        assert result.summary.rows_removed == report.invalid_rows, name
        assert len(result.data) + len(result.quarantine) == len(frame)
        after = validator.validate(result.data.drop(columns=[SOURCE_ROW]), name)
        assert after.invalid_rows == 0, name
