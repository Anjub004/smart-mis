"""Tests for exceptions, logging, paths and timing."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import pytest

from smartmis.core.config import AppConfig
from smartmis.core.exceptions import (
    ConfigurationError,
    DataSourceError,
    DataValidationError,
    KPICalculationError,
    SmartMISError,
    UnsupportedFileError,
)
from smartmis.core.logging import get_logger, setup_logging
from smartmis.core.paths import ensure_dir, project_root, resolve_path
from smartmis.core.timing import timed


class TestExceptions:
    def test_user_message_and_details(self) -> None:
        err = DataValidationError(
            "Column 'qty' missing in sales.xlsx",
            user_message="Required column 'qty' is missing.",
            details={"file": "sales.xlsx"},
        )
        assert str(err) == "Column 'qty' missing in sales.xlsx"
        assert err.user_message == "Required column 'qty' is missing."
        log = err.to_log_dict()
        assert log["error_type"] == "DataValidationError"
        assert log["file"] == "sales.xlsx"

    def test_default_user_message(self) -> None:
        assert "configuration" in ConfigurationError().user_message.lower()

    def test_hierarchy(self) -> None:
        assert issubclass(UnsupportedFileError, DataSourceError)
        assert issubclass(KPICalculationError, SmartMISError)
        with pytest.raises(SmartMISError):
            raise UnsupportedFileError("bad.exe")


class TestLogging:
    def test_writes_rotating_file_and_is_idempotent(
        self, app_config: AppConfig, tmp_path: Path
    ) -> None:
        log_dir = tmp_path / "logs"
        setup_logging(app_config, log_dir=log_dir, console=False)
        logger = setup_logging(app_config, log_dir=log_dir, console=False)
        tagged = [h for h in logger.handlers if getattr(h, "_smartmis_handler", None)]
        assert len(tagged) == 1

        get_logger("tests.core").info("hello from tests")
        for handler in logger.handlers:
            handler.flush()
        content = (log_dir / "smartmis.log").read_text(encoding="utf-8")
        assert "hello from tests" in content
        assert "smartmis.tests.core" in content

    def test_secrets_are_redacted(self, app_config: AppConfig, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        setup_logging(app_config, log_dir=log_dir, console=False)
        log = get_logger("tests.redact")
        log.warning("connecting with password=%s", "hunter2")
        log.warning("url postgresql://admin:s3cr3t@db/smartmis api_key: abc123")
        for handler in logging.getLogger("smartmis").handlers:
            handler.flush()
        content = (log_dir / "smartmis.log").read_text(encoding="utf-8")
        assert "hunter2" not in content
        assert "s3cr3t" not in content
        assert "abc123" not in content
        assert "password=***" in content

    def test_get_logger_namespacing(self) -> None:
        assert get_logger("kpi").name == "smartmis.kpi"
        assert get_logger("smartmis.kpi").name == "smartmis.kpi"


class TestPaths:
    def test_project_root_from_env(self, tmp_path: Path) -> None:
        assert project_root() == tmp_path.resolve()

    def test_resolve_and_ensure(self, tmp_path: Path) -> None:
        assert resolve_path("reports") == (tmp_path / "reports").resolve()
        absolute = tmp_path / "abs"
        assert resolve_path(absolute) == absolute
        created = ensure_dir("nested/dir")
        assert created.is_dir()


def test_timed_records_elapsed() -> None:
    with timed("sleep") as timer:
        time.sleep(0.01)
    assert timer.elapsed >= 0.01
