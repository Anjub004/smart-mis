"""Large-dataset performance checks. Run with ``pytest -m slow``."""

from __future__ import annotations

import time

import pytest

from smartmis.cleaning import Cleaner
from smartmis.core.config import AppConfig
from smartmis.samples import SampleDataGenerator
from smartmis.validation import Validator


@pytest.mark.slow
def test_500k_rows_validate_and_clean(app_config: AppConfig) -> None:
    dataset = SampleDataGenerator(seed=1, scale=16).generate()
    sales = dataset.sales.astype(str).where(dataset.sales.notna(), None)
    assert len(sales) > 450_000

    started = time.perf_counter()
    report = Validator.from_config(app_config).validate(sales, "sales")
    validate_seconds = time.perf_counter() - started

    started = time.perf_counter()
    result = Cleaner.from_config(app_config).clean(sales, "sales")
    clean_seconds = time.perf_counter() - started

    assert result.summary.rows_removed == report.invalid_rows
    # Generous limits so CI runners pass; typical laptop timings are ~2 s and ~4 s.
    assert validate_seconds < 30, validate_seconds
    assert clean_seconds < 60, clean_seconds
