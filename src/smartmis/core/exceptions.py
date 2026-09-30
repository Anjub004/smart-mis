"""SmartMIS exception hierarchy.

Every exception carries two messages:

* ``user_message`` — short, non-technical text that is safe to show in the UI.
* ``details``      — technical context (paths, column names, original error) that
                     is written to the log but never shown to end users.

Catch :class:`SmartMISError` at adapter boundaries (UI, CLI, scheduler) and let
anything else propagate as a genuine bug.
"""

from __future__ import annotations

from typing import Any


class SmartMISError(Exception):
    """Base class for all expected, handled SmartMIS errors."""

    default_user_message = "Something went wrong while processing your request."

    def __init__(
        self,
        message: str | None = None,
        *,
        user_message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.user_message = user_message or message or self.default_user_message
        self.details: dict[str, Any] = dict(details or {})
        super().__init__(message or self.user_message)

    def to_log_dict(self) -> dict[str, Any]:
        """Return a structured representation for logs and the audit trail."""
        return {
            "error_type": type(self).__name__,
            "message": str(self),
            "user_message": self.user_message,
            **self.details,
        }


class ConfigurationError(SmartMISError):
    """A configuration file is missing, unreadable or fails validation."""

    default_user_message = (
        "The application configuration is invalid. Please check the settings files."
    )


class DataSourceError(SmartMISError):
    """Data could not be read from a file, sheet or database."""

    default_user_message = "The data source could not be read."


class UnsupportedFileError(DataSourceError):
    """An uploaded file has a disallowed type, size or content."""

    default_user_message = "This file type or size is not supported."


class DataValidationError(SmartMISError):
    """The dataset fails blocking validation rules (e.g. missing required columns)."""

    default_user_message = (
        "The data did not pass validation. Please review the Data Quality report."
    )


class DataProcessingError(SmartMISError):
    """Cleaning, transformation or business-rule evaluation failed."""

    default_user_message = "The data could not be processed."


class KPICalculationError(DataProcessingError):
    """A KPI could not be computed from the available data."""

    default_user_message = "One or more KPIs could not be calculated."


class ReportGenerationError(SmartMISError):
    """A report or export file could not be created."""

    default_user_message = "The report could not be generated."


class NotificationError(SmartMISError):
    """An email or other notification could not be delivered."""

    default_user_message = "The report could not be sent. Please check the email settings."
