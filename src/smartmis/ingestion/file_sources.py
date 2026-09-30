"""CSV and Excel data sources.

Both work from **bytes**, so the same class serves a Streamlit upload
(``UploadedFile.getvalue()``), a CLI path (:meth:`FileDataSource.from_path`) or
a test fixture. Every instance passes :func:`validate_upload` before parsing.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import ClassVar

import pandas as pd

from smartmis.core.config import UploadConfig
from smartmis.core.exceptions import DataSourceError
from smartmis.ingestion.base import BaseDataSource, HeaderMode, LoadedData, RawRead, sha256_of
from smartmis.ingestion.registry import register_source
from smartmis.ingestion.security import check_file_size, validate_upload

_CANDIDATE_DELIMITERS = ",;\t|"
_SNIFF_CHARS = 64 * 1024
_WIDTH_SCAN_LINES = 200


class FileDataSource(BaseDataSource):
    """Shared behaviour for file-based sources."""

    source_type: ClassVar[str] = "file"
    read_error_hint: ClassVar[str] = "Please check that the file is valid and not corrupted."

    def __init__(
        self,
        content: bytes,
        file_name: str,
        upload_config: UploadConfig,
        *,
        header_row: HeaderMode = "auto",
    ) -> None:
        safe_name = validate_upload(file_name, content, upload_config)
        super().__init__(safe_name, header_row=header_row)
        self.content = content
        self.upload_config = upload_config
        self.file_hash = sha256_of(content)

    @classmethod
    def from_path(
        cls, path: str | Path, upload_config: UploadConfig, **kwargs: object
    ) -> FileDataSource:
        """Create a source from a file on disk (size-checked before reading)."""
        file_path = Path(path)
        if not file_path.is_file():
            raise DataSourceError(
                f"File not found: {file_path}",
                user_message=f"File '{file_path.name}' was not found.",
                details={"path": str(file_path)},
            )
        check_file_size(file_path.name, file_path.stat().st_size, upload_config)
        return cls(file_path.read_bytes(), file_path.name, upload_config, **kwargs)  # type: ignore[arg-type]


# -----------------------------------------------------------------------------
# CSV
# -----------------------------------------------------------------------------


@register_source("csv", extensions=(".csv", ".txt", ".tsv"))
class CSVDataSource(FileDataSource):
    """Comma/semicolon/tab/pipe-delimited text with encoding fallback."""

    source_type: ClassVar[str] = "csv"

    def __init__(
        self,
        content: bytes,
        file_name: str,
        upload_config: UploadConfig,
        *,
        header_row: HeaderMode = "auto",
        delimiter: str | None = None,
        encoding: str | None = None,
    ) -> None:
        super().__init__(content, file_name, upload_config, header_row=header_row)
        self.delimiter = delimiter
        self.encoding = encoding

    def _decode(self) -> tuple[str, str]:
        if self.encoding:
            return self.content.decode(self.encoding), self.encoding
        if self.content.startswith((b"\xff\xfe", b"\xfe\xff")):
            return self.content.decode("utf-16"), "utf-16"
        errors: list[str] = []
        for encoding in self.upload_config.csv_encoding_fallbacks:
            try:
                return self.content.decode(encoding), encoding
            except UnicodeDecodeError as exc:
                errors.append(f"{encoding}: {exc.reason}")
        raise DataSourceError(
            f"Could not decode {self.name}: {'; '.join(errors)}",
            user_message=f"'{self.name}' uses an unsupported text encoding. Save it as UTF-8 CSV.",
            details={"file": self.name},
        )

    @staticmethod
    def _sniff_delimiter(text: str) -> str:
        sample = text[:_SNIFF_CHARS]
        try:
            return csv.Sniffer().sniff(sample, delimiters=_CANDIDATE_DELIMITERS).delimiter
        except csv.Error:
            first = sample.splitlines()[0] if sample else ""
            counts = {d: first.count(d) for d in _CANDIDATE_DELIMITERS}
            best = max(counts, key=lambda d: counts[d])
            return best if counts[best] else ","

    @staticmethod
    def _max_width(text: str, delimiter: str) -> int:
        lines = text.splitlines()[:_WIDTH_SCAN_LINES]
        reader = csv.reader(lines, delimiter=delimiter)
        return max((len(row) for row in reader), default=0)

    def _read(self) -> RawRead:
        text, encoding = self._decode()
        text = text.lstrip("﻿")
        delimiter = self.delimiter or self._sniff_delimiter(text)
        width = self._max_width(text, delimiter)
        if width == 0:
            raise DataSourceError(
                f"{self.name} has no content", user_message=f"'{self.name}' is empty."
            )
        try:
            table = pd.read_csv(
                io.StringIO(text),
                sep=delimiter,
                header=None,
                names=list(range(width)),
                dtype=str,
                skip_blank_lines=True,
                keep_default_na=True,
            )
        except pd.errors.ParserError as exc:
            raise DataSourceError(
                f"CSV parse error in {self.name}: {exc}",
                user_message=(
                    f"'{self.name}' has rows with an inconsistent number of columns. "
                    "Check for unquoted delimiters inside values."
                ),
                details={"file": self.name, "delimiter": delimiter},
            ) from exc
        return RawRead(
            table=table,
            file_size_bytes=len(self.content),
            file_hash=self.file_hash,
            encoding=encoding,
            delimiter=delimiter,
        )


# -----------------------------------------------------------------------------
# Excel
# -----------------------------------------------------------------------------


@register_source("excel", extensions=(".xlsx",))
class ExcelDataSource(FileDataSource):
    """Excel workbook (``.xlsx``) with sheet selection."""

    source_type: ClassVar[str] = "excel"

    def __init__(
        self,
        content: bytes,
        file_name: str,
        upload_config: UploadConfig,
        *,
        header_row: HeaderMode = "auto",
        sheet_name: str | None = None,
    ) -> None:
        super().__init__(content, file_name, upload_config, header_row=header_row)
        self.sheet_name = sheet_name
        self._sheets: tuple[str, ...] | None = None

    def list_sheets(self) -> tuple[str, ...]:
        """Worksheet names in workbook order."""
        if self._sheets is None:
            try:
                with pd.ExcelFile(io.BytesIO(self.content), engine="openpyxl") as workbook:
                    self._sheets = tuple(str(s) for s in workbook.sheet_names)
            except Exception as exc:  # openpyxl raises zipfile/KeyError/InvalidFileException
                raise DataSourceError(
                    f"Cannot open workbook {self.name}: {exc}",
                    user_message=f"'{self.name}' could not be opened as an Excel workbook.",
                    details={"file": self.name},
                ) from exc
        return self._sheets

    def _resolve_sheet(self) -> str:
        sheets = self.list_sheets()
        if not sheets:
            raise DataSourceError(
                f"{self.name} has no worksheets", user_message=f"'{self.name}' has no worksheets."
            )
        if self.sheet_name is None:
            return sheets[0]
        if self.sheet_name not in sheets:
            raise DataSourceError(
                f"Sheet {self.sheet_name!r} not in {sheets}",
                user_message=(
                    f"Sheet '{self.sheet_name}' was not found in '{self.name}'. "
                    f"Available sheets: {', '.join(sheets)}."
                ),
                details={"file": self.name, "sheet": self.sheet_name},
            )
        return self.sheet_name

    def _read(self) -> RawRead:
        sheet = self._resolve_sheet()
        table = pd.read_excel(
            io.BytesIO(self.content),
            sheet_name=sheet,
            header=None,
            dtype=object,
            engine="openpyxl",
        )
        table = table.dropna(how="all").reset_index(drop=True)
        return RawRead(
            table=table,
            file_size_bytes=len(self.content),
            file_hash=self.file_hash,
            sheet_name=sheet,
            available_sheets=self.list_sheets(),
        )

    def load_all_sheets(self) -> dict[str, LoadedData]:
        """Load every worksheet; sheets that are empty are skipped."""
        results: dict[str, LoadedData] = {}
        original = self.sheet_name
        try:
            for sheet in self.list_sheets():
                self.sheet_name = sheet
                try:
                    results[sheet] = self.load()
                except DataSourceError:
                    continue
        finally:
            self.sheet_name = original
        return results
