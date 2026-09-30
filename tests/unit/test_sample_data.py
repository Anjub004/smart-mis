"""Tests for the DemoMart sample-data generator."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from smartmis.core.config import AppConfig
from smartmis.ingestion import CSVDataSource, ExcelDataSource
from smartmis.samples import SampleDataGenerator, SampleDataset
from smartmis.samples.generator import BRANCHES, HOLIDAYS


@pytest.fixture(scope="module")
def dataset() -> SampleDataset:
    return SampleDataGenerator(seed=7).generate()


@pytest.fixture(scope="module")
def clean_dataset() -> SampleDataset:
    return SampleDataGenerator(seed=7, inject_issues=False).generate()


def test_volumes(dataset: SampleDataset) -> None:
    assert len(dataset.sales) > 20_000
    assert len(dataset.inventory) >= 1_000
    assert len(dataset.orders) >= 1_000
    assert len(dataset.employees) == 240
    assert set(dataset.branches["branch"]) == {b[0] for b in BRANCHES}
    assert len(dataset.holidays) == len(HOLIDAYS)


def test_deterministic() -> None:
    first = SampleDataGenerator(seed=1, scale=0.2).generate()
    second = SampleDataGenerator(seed=1, scale=0.2).generate()
    pd.testing.assert_frame_equal(first.sales, second.sales)
    pd.testing.assert_frame_equal(first.orders, second.orders)


def test_scale_changes_volume() -> None:
    small = SampleDataGenerator(seed=1, scale=0.2).generate()
    assert len(small.sales) < 10_000
    with pytest.raises(ValueError):
        SampleDataGenerator(scale=0)


def test_clean_data_has_unique_business_keys(clean_dataset: SampleDataset) -> None:
    sales = clean_dataset.sales
    assert not sales.duplicated(["invoice_no", "sku"]).any()
    assert (sales["quantity"] > 0).all()
    assert sales["branch"].notna().all()
    assert not clean_dataset.orders["request_id"].duplicated().any()
    assert not clean_dataset.injected.get("sales_duplicate_rows")


def test_injected_issues_are_present_and_counted(dataset: SampleDataset) -> None:
    sales = dataset.sales
    injected = dataset.injected
    assert sales.duplicated().sum() == injected["sales_duplicate_rows"] > 0
    assert (pd.to_numeric(sales["quantity"], errors="coerce") < 0).sum() >= injected[
        "sales_negative_quantity"
    ] - injected["sales_duplicate_rows"]
    assert (
        sales["invoice_date"].isin(["2026-02-30", "31/13/2026", "TBD"]).sum()
        >= injected["sales_invalid_dates"]
    )
    assert (dataset.orders["priority"] == "urgent").sum() == injected["orders_invalid_priority"]


def test_completion_times_respect_working_calendar(clean_dataset: SampleDataset) -> None:
    orders = clean_dataset.orders
    completed = pd.to_datetime(orders.loc[orders["status"] == "completed", "completed_at"])
    minutes = completed.dt.hour * 60 + completed.dt.minute
    assert completed.dt.dayofweek.ne(6).all()  # never on Sunday
    assert minutes.between(8 * 60, 17 * 60).all()
    holidays = {pd.Timestamp(d).date() for d, _ in HOLIDAYS}
    assert not completed.dt.date.isin(holidays).any()
    non_completed = orders.loc[orders["status"] != "completed", "completed_at"]
    assert non_completed.isna().all()


def test_high_value_orders_exist(dataset: SampleDataset) -> None:
    assert (pd.to_numeric(dataset.orders["order_value"]) >= 100_000).sum() >= 12


def test_no_personal_contact_data(dataset: SampleDataset) -> None:
    columns = {c.lower() for c in dataset.employees.columns}
    assert not columns & {"email", "phone", "mobile", "address", "aadhaar", "pan"}


def test_written_files_load_through_ingestion(tmp_path: Path, app_config: AppConfig) -> None:
    dataset = SampleDataGenerator(seed=3, scale=0.1).generate()
    paths = dataset.write(tmp_path)
    assert {p.name for p in paths} >= {"sales.csv", "orders.csv", "demomart_mis_sample.xlsx"}
    upload = app_config.settings.upload
    sales = CSVDataSource.from_path(tmp_path / "sales.csv", upload).load()
    assert sales.profile.row_count == len(dataset.sales)
    workbook = ExcelDataSource.from_path(tmp_path / "demomart_mis_sample.xlsx", upload)
    assert workbook.list_sheets() == ("Sales", "Inventory", "Orders", "Sales_Extract")
    extract = ExcelDataSource.from_path(
        tmp_path / "demomart_mis_sample.xlsx", upload, sheet_name="Sales_Extract"
    ).load()
    assert extract.dataframe.columns[0] == "invoice_no"
    assert extract.profile.header_row_index == 2
