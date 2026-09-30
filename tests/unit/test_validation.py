"""Tests for the validation engine and Data Quality Score."""

from __future__ import annotations

import pandas as pd
import pytest

from smartmis.core.config import AppConfig, load_config
from smartmis.core.exceptions import ConfigurationError, DataValidationError
from smartmis.validation import Validator, detect_dataset_type
from smartmis.validation.scoring import DimensionScore, quality_score

COLUMNS = [
    "invoice_no",
    "invoice_date",
    "branch",
    "category",
    "sku",
    "quantity",
    "unit_price",
    "discount_pct",
]
# Hand-built fixture. Row numbers (1-based) and the issue each one carries:
ROWS = [
    ["INV1", "2026-07-01", "Powai", "Grocery", "S1", "2", "10", "0"],  # 1 ok
    ["INV2", "2026-07-01", "Powai", "Grocery", "S1", "1", "10", "5"],  # 2 ok
    ["INV3", "2026-07-02", "Thane", "FMCG", "S2", "3", "20", "0"],  # 3 ok
    ["INV3", "2026-07-02", "Thane", "FMCG", "S2", "3", "20", "0"],  # 4 exact duplicate of 3
    ["INV4", "2026-07-32", "Thane", "FMCG", "S3", "1", "20", "0"],  # 5 invalid date
    ["INV5", "2026-07-03", "", "Bakery", "S4", "1", "5", "0"],  # 6 missing branch
    ["INV6", "2026-07-03", "Andheri", "Bakery", "S4", "-2", "5", "0"],  # 7 negative quantity
    ["INV7", "2026-07-04", "Andheri", "Bakery", "S5", "1", "abc", "0"],  # 8 invalid number
    ["INV8", "2026-07-04", "Andheri", "Bakery", "S6", "1", "5", "150"],  # 9 percentage > 100
    ["INV1", "2026-07-05", "Powai", "Grocery", "S1", "4", "10", "0"],  # 10 duplicate key INV1+S1
]


@pytest.fixture
def sales_frame() -> pd.DataFrame:
    return pd.DataFrame(ROWS, columns=COLUMNS, dtype=object)


@pytest.fixture
def validator(app_config: AppConfig) -> Validator:
    return Validator.from_config(app_config)


def _check(report, name: str, column: str | None = None):  # type: ignore[no-untyped-def]
    return next(
        c for c in report.checks if c.name == name and (column is None or c.column == column)
    )


class TestHandCalculatedScore:
    """Every number below is worked out by hand in docs/validation-rules.md."""

    def test_dimension_scores(self, validator: Validator, sales_frame: pd.DataFrame) -> None:
        report = validator.validate(sales_frame, "sales")
        dims = report.dimensions
        # Completeness: 7 required columns x 10 rows = 70 cells, 1 blank
        assert (dims["completeness"].checked, dims["completeness"].failed) == (70, 1)
        assert dims["completeness"].score == 98.57
        # Validity: 40 filled typed cells (date + 3 numeric), 2 unparseable
        assert (dims["validity"].checked, dims["validity"].failed) == (40, 2)
        assert dims["validity"].score == 95.0
        # Uniqueness: 10 rows, 1 exact duplicate + 1 repeated key
        assert (dims["uniqueness"].checked, dims["uniqueness"].failed) == (10, 2)
        assert dims["uniqueness"].score == 80.0
        # Consistency: 10 qty + 9 valid prices + 10 discounts = 29 values, 2 out of range
        assert (dims["consistency"].checked, dims["consistency"].failed) == (29, 2)
        assert dims["consistency"].score == 93.1
        # Conformity: 7 of 7 required columns present
        assert dims["conformity"].score == 100.0

    def test_overall_score_and_rows(self, validator: Validator, sales_frame: pd.DataFrame) -> None:
        report = validator.validate(sales_frame, "sales")
        # 0.30*98.57 + 0.25*95 + 0.20*80 + 0.15*93.1 + 0.10*100 = 93.286
        assert report.quality_score == 93.3
        assert report.total_rows == 10
        assert report.invalid_rows == 7
        assert report.valid_rows == 3
        assert report.invalid_row_mask.tolist() == [False] * 3 + [True] * 7
        assert report.status == "PASSED WITH WARNINGS"
        assert not report.is_blocking

    def test_named_checks(self, validator: Validator, sales_frame: pd.DataFrame) -> None:
        report = validator.validate(sales_frame, "sales")
        assert _check(report, "duplicate_rows").sample_rows == (4,)
        assert _check(report, "duplicate_keys").sample_rows == (10,)
        assert _check(report, "invalid_dates", "invoice_date").sample_rows == (5,)
        assert _check(report, "missing_values", "branch").sample_rows == (6,)
        assert _check(report, "negative_values", "quantity").sample_rows == (7,)
        assert _check(report, "invalid_numbers", "unit_price").sample_rows == (8,)
        assert _check(report, "invalid_percentages", "discount_pct").sample_rows == (9,)

    def test_metrics_and_text(self, validator: Validator, sales_frame: pd.DataFrame) -> None:
        report = validator.validate(sales_frame, "sales")
        assert report.metrics["duplicate_pct"] == 20.0
        assert report.metrics["invalid_dates_pct"] == 10.0
        assert report.metrics["missing_values_pct"] == pytest.approx(1.43)
        text = report.render_text()
        for label in (
            "DATA QUALITY REPORT",
            "Total Rows:",
            "Valid Rows:",
            "Invalid Rows:",
            "Data Quality Score:",
            "93.3%",
        ):
            assert label in text

    def test_issue_log(self, validator: Validator, sales_frame: pd.DataFrame) -> None:
        issues = validator.validate(sales_frame, "sales").issues
        assert set(issues["row"]) == {4, 5, 6, 7, 8, 9, 10}
        bad_price = issues[issues["check"] == "invalid_numbers"].iloc[0]
        assert (bad_price["row"], bad_price["column"], bad_price["value"]) == (
            8,
            "unit_price",
            "abc",
        )

    def test_input_not_modified(self, validator: Validator, sales_frame: pd.DataFrame) -> None:
        before = sales_frame.copy()
        validator.validate(sales_frame, "sales")
        pd.testing.assert_frame_equal(sales_frame, before)


class TestBlockingAndConformity:
    def test_missing_required_column_blocks(
        self, validator: Validator, sales_frame: pd.DataFrame
    ) -> None:
        report = validator.validate(sales_frame.drop(columns=["unit_price"]), "sales")
        assert report.is_blocking
        assert report.status == "FAILED"
        assert "unit_price" in report.blocking_reasons[0]
        assert report.dimensions["conformity"].score == pytest.approx(85.71)
        with pytest.raises(DataValidationError) as excinfo:
            report.raise_if_blocking()
        assert "unit_price" in excinfo.value.user_message

    def test_empty_dataset_blocks(self, validator: Validator) -> None:
        report = validator.validate(pd.DataFrame(columns=COLUMNS), "sales")
        assert report.is_blocking
        assert any("no data rows" in r for r in report.blocking_reasons)

    def test_score_threshold_blocks(self, app_config: AppConfig, sales_frame: pd.DataFrame) -> None:
        strict = app_config.settings.validation.model_copy(update={"blocking_quality_score": 95})
        report = Validator(app_config.validation_rules, strict).validate(sales_frame, "sales")
        assert any("below the minimum of 95%" in r for r in report.blocking_reasons)

    def test_unexpected_columns(self, app_config: AppConfig, sales_frame: pd.DataFrame) -> None:
        extra = sales_frame.assign(remarks="x")
        lenient = Validator.from_config(app_config).validate(extra, "sales")
        assert _check(lenient, "unexpected_columns").severity == "info"
        strict_schema = app_config.validation_rules.datasets["sales"].model_copy(
            update={"allow_unexpected_columns": False}
        )
        rules = app_config.validation_rules.model_copy(
            update={"datasets": {"sales": strict_schema}}
        )
        strict = Validator(rules, app_config.settings.validation).validate(extra, "sales")
        check = _check(strict, "unexpected_columns")
        assert (check.severity, check.failed, check.checked) == ("error", 1, 9)

    def test_unknown_dataset_type(self, validator: Validator, sales_frame: pd.DataFrame) -> None:
        with pytest.raises(ConfigurationError):
            validator.validate(sales_frame, "payroll")


class TestColumnMatching:
    def test_messy_headers_and_aliases(
        self, validator: Validator, sales_frame: pd.DataFrame
    ) -> None:
        messy = sales_frame.rename(
            columns={
                "invoice_no": "Invoice No.",
                "quantity": "Qty",
                "unit_price": "Price",
                "discount_pct": "Discount %",
                "branch": " BRANCH ",
            }
        )
        report = validator.validate(messy, "sales")
        assert not report.is_blocking
        assert report.column_mapping["quantity"] == "Qty"
        assert report.column_mapping["discount_pct"] == "Discount %"
        assert report.quality_score == 93.3
        assert any(c.name == "column_mapping" for c in report.checks)

    def test_detect_dataset_type(self, app_config: AppConfig, sales_frame: pd.DataFrame) -> None:
        rules = app_config.validation_rules
        assert detect_dataset_type(sales_frame.columns, rules) == "sales"
        orders = ["request_id", "branch", "department", "priority", "created_at", "status"]
        assert detect_dataset_type(orders, rules) == "orders"
        assert detect_dataset_type(["foo", "bar"], rules) is None


class TestOrders:
    @pytest.fixture
    def orders(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "request_id": ["R1", "R2", "R3", "R4"],
                "branch": ["Powai"] * 4,
                "department": ["IT"] * 4,
                "priority": ["High", " critical", "urgent", "low"],
                "created_at": [
                    "2026-07-01 09:00",
                    "2026-07-01 10:00",
                    "2026-07-01 11:00",
                    "02/07/2026 09:00",
                ],
                "completed_at": ["2026-07-01 12:00", "2026-07-01 08:00", None, "03/07/2026 10:30"],
                "status": ["completed", "completed", "open", "completed"],
            }
        )

    def test_allowed_list_is_case_insensitive(
        self, validator: Validator, orders: pd.DataFrame
    ) -> None:
        check = _check(validator.validate(orders, "orders"), "invalid_categories", "priority")
        assert check.failed == 1
        assert "urgent" in check.message

    def test_consistency_rule(self, validator: Validator, orders: pd.DataFrame) -> None:
        check = _check(validator.validate(orders, "orders"), "consistency_rule")
        assert (check.checked, check.failed, check.sample_rows) == (3, 1, (2,))

    def test_dayfirst_setting(self, app_config: AppConfig, orders: pd.DataFrame) -> None:
        monthfirst = app_config.settings.validation.model_copy(update={"dayfirst": False})
        report = Validator(app_config.validation_rules, monthfirst).validate(orders, "orders")
        # 02/07 and 03/07 are both valid either way, so validity is unchanged
        assert _check(report, "invalid_dates", "created_at").failed == 0


def test_duplicate_check_can_be_disabled(app_config: AppConfig, sales_frame: pd.DataFrame) -> None:
    settings = app_config.settings.validation.model_copy(update={"duplicate_check": False})
    report = Validator(app_config.validation_rules, settings).validate(sales_frame, "sales")
    assert not report.dimensions["uniqueness"].applicable
    assert all(c.name not in {"duplicate_rows", "duplicate_keys"} for c in report.checks)


def test_quality_score_renormalises_weights() -> None:
    scores = {
        "completeness": DimensionScore("completeness", 0.3, 10, 0),
        "validity": DimensionScore("validity", 0.25, 10, 5),
        "uniqueness": DimensionScore("uniqueness", 0.2, 0, 0),
        "consistency": DimensionScore("consistency", 0.15, 0, 0),
        "conformity": DimensionScore("conformity", 0.1, 0, 0),
    }
    # (0.3*100 + 0.25*50) / 0.55
    assert quality_score(scores) == 77.3  # type: ignore[arg-type]


def test_report_serialisation(validator: Validator, sales_frame: pd.DataFrame) -> None:
    data = validator.validate(sales_frame, "sales").to_dict()
    assert data["quality_score"] == 93.3
    assert data["dimensions"]["uniqueness"] == 80.0
    assert len(data["failed_checks"]) == 7


def test_sample_data_scores(app_config: AppConfig) -> None:
    """The shipped sample data is noisy but well above the blocking threshold."""
    from smartmis.samples import SampleDataGenerator

    dataset = SampleDataGenerator(seed=11, scale=0.3).generate()
    validator = Validator.from_config(load_config(app_config.config_dir))
    sales = validator.validate(dataset.sales, "sales")
    assert 95 < sales.quality_score < 100
    assert sales.invalid_rows > 0
    assert _check(sales, "duplicate_rows").failed == dataset.injected["sales_duplicate_rows"]
