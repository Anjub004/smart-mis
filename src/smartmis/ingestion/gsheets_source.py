"""Google Sheets data source (optional).

Requires the ``gsheets`` extra (``pip install -e ".[gsheets]"``) and a Google
service-account key file referenced by ``GOOGLE_SERVICE_ACCOUNT_FILE``. Share
the sheet with the service account's e-mail address (Viewer is enough).

If the libraries or credentials are missing, this source raises a friendly
:class:`DataSourceError`; the rest of SmartMIS keeps working with Excel/CSV.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType
from typing import Any, ClassVar

import pandas as pd

from smartmis.core.exceptions import DataSourceError
from smartmis.ingestion.base import BaseDataSource, HeaderMode, RawRead
from smartmis.ingestion.registry import register_source


def _import_gspread() -> ModuleType:
    try:
        return importlib.import_module("gspread")
    except ImportError as exc:
        raise DataSourceError(
            "gspread is not installed",
            user_message=(
                'Google Sheets support is not installed. Run: pip install -e ".[gsheets]"'
            ),
        ) from exc


@register_source("gsheets")
class GoogleSheetsDataSource(BaseDataSource):
    """Read one worksheet of a Google Sheet by URL."""

    source_type: ClassVar[str] = "gsheets"

    def __init__(
        self,
        sheet_url: str,
        credentials_file: str | Path | None,
        *,
        worksheet: str | None = None,
        header_row: HeaderMode = "auto",
    ) -> None:
        if not sheet_url.startswith("https://docs.google.com/spreadsheets/"):
            raise DataSourceError(
                f"Not a Google Sheets URL: {sheet_url}",
                user_message="Please paste a full Google Sheets link (https://docs.google.com/spreadsheets/…).",
            )
        self.sheet_url = sheet_url
        self.credentials_file = Path(credentials_file) if credentials_file else None
        self.worksheet = worksheet
        super().__init__(worksheet or "Google Sheet", header_row=header_row)

    def _client(self) -> Any:
        if self.credentials_file is None or not self.credentials_file.is_file():
            raise DataSourceError(
                "Google service-account credentials not found",
                user_message=(
                    "Google Sheets credentials are not configured. Set "
                    "GOOGLE_SERVICE_ACCOUNT_FILE in .env (see docs/google-sheets.md)."
                ),
            )
        gspread = _import_gspread()
        return gspread.service_account(filename=str(self.credentials_file))

    def _spreadsheet(self) -> Any:
        return self._client().open_by_url(self.sheet_url)

    def list_worksheets(self) -> tuple[str, ...]:
        try:
            return tuple(ws.title for ws in self._spreadsheet().worksheets())
        except DataSourceError:
            raise
        except Exception as exc:  # gspread raises API/permission errors
            raise DataSourceError(
                f"Cannot list worksheets: {exc}",
                user_message="The Google Sheet could not be opened. Is it shared with the service account?",
            ) from exc

    def _read(self) -> RawRead:
        spreadsheet = self._spreadsheet()
        worksheet = spreadsheet.worksheet(self.worksheet) if self.worksheet else spreadsheet.sheet1
        values = worksheet.get_all_values()
        table = pd.DataFrame(values, dtype=object).replace("", None)
        titles = tuple(ws.title for ws in spreadsheet.worksheets())
        return RawRead(
            table=table,
            sheet_name=worksheet.title,
            available_sheets=titles,
            extra={"spreadsheet": getattr(spreadsheet, "title", "")},
        )
