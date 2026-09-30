"""Tests for data sources, upload security and profiling."""

from __future__ import annotations

import io
import re
import sys
import types
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine

from smartmis.core.config import AppConfig, UploadConfig
from smartmis.core.exceptions import DataSourceError, SmartMISError, UnsupportedFileError
from smartmis.ingestion import (
    CSVDataSource,
    ExcelDataSource,
    GoogleSheetsDataSource,
    SQLDataSource,
    available_sources,
    create_source,
    source_for_upload,
)
from smartmis.ingestion.profiling import apply_header, detect_header_row, infer_column_type
from smartmis.ingestion.security import safe_file_name, validate_upload
from smartmis.ingestion.sql_source import redact_url


@contextmanager
def user_error(exc_type: type[SmartMISError], pattern: str) -> Iterator[None]:
    """Assert that ``exc_type`` is raised and its *user-facing* message matches."""
    with pytest.raises(exc_type) as excinfo:
        yield
    assert re.search(pattern, excinfo.value.user_message), excinfo.value.user_message


@pytest.fixture
def upload(app_config: AppConfig) -> UploadConfig:
    return app_config.settings.upload


def _csv(text: str, encoding: str = "utf-8") -> bytes:
    return text.encode(encoding)


def _xlsx(sheets: dict[str, pd.DataFrame], title_rows: dict[str, list[str]] | None = None) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            titles = (title_rows or {}).get(name, [])
            frame.to_excel(
                writer, sheet_name=name, index=False, startrow=len(titles) + (1 if titles else 0)
            )
            for i, title in enumerate(titles, start=1):
                writer.sheets[name][f"A{i}"] = title
    return buffer.getvalue()


SALES_CSV = "invoice_no,invoice_date,branch,quantity,unit_price\n" + "".join(
    f"INV{i:03d},2026-07-{i % 28 + 1:02d},{['Powai', 'Thane', 'Andheri'][i % 3]},{i % 9},{i * 1.25:.2f}\n"
    for i in range(60)
)


# -----------------------------------------------------------------------------
# CSV
# -----------------------------------------------------------------------------


class TestCSV:
    def test_basic_load_and_profile(self, upload: UploadConfig) -> None:
        data = CSVDataSource(_csv(SALES_CSV), "sales.csv", upload).load()
        profile = data.profile
        assert profile.row_count == 60
        assert profile.column_count == 5
        assert profile.delimiter == ","
        assert profile.encoding == "utf-8"
        assert profile.header_row_index == 0
        assert profile.file_hash and len(profile.file_hash) == 64
        assert profile.column_types == {
            "invoice_no": "string",
            "invoice_date": "date",
            "branch": "category",
            "quantity": "integer",
            "unit_price": "float",
        }
        # values are loaded as text — nothing is silently converted
        assert data.dataframe["quantity"].iloc[1] == "1"

    @pytest.mark.parametrize("delimiter", [";", "\t", "|"])
    def test_delimiter_sniffing(self, upload: UploadConfig, delimiter: str) -> None:
        text = SALES_CSV.replace(",", delimiter)
        profile = CSVDataSource(_csv(text), "sales.csv", upload).load().profile
        assert profile.delimiter == delimiter
        assert profile.column_count == 5

    def test_encoding_fallback_cp1252(self, upload: UploadConfig) -> None:
        text = "branch,note\nPowai,Café opening\n"
        data = CSVDataSource(_csv(text, "cp1252"), "notes.csv", upload).load()
        assert data.profile.encoding == "cp1252"
        assert data.dataframe["note"].iloc[0] == "Café opening"

    def test_utf8_bom_is_stripped(self, upload: UploadConfig) -> None:
        data = CSVDataSource(b"\xef\xbb\xbf" + _csv(SALES_CSV), "sales.csv", upload).load()
        assert data.dataframe.columns[0] == "invoice_no"

    def test_utf16(self, upload: UploadConfig) -> None:
        data = CSVDataSource("a,b\n1,2\n".encode("utf-16"), "u16.csv", upload).load()
        assert data.profile.encoding == "utf-16"
        assert list(data.dataframe.columns) == ["a", "b"]

    def test_title_rows_are_skipped(self, upload: UploadConfig) -> None:
        text = "DemoMart Sales Extract\nGenerated 30 Sep 2026\n\n" + SALES_CSV
        data = CSVDataSource(_csv(text), "report.csv", upload).load()
        assert list(data.dataframe.columns)[:2] == ["invoice_no", "invoice_date"]
        assert data.profile.row_count == 60

    def test_explicit_and_no_header(self, upload: UploadConfig) -> None:
        no_header = CSVDataSource(_csv("1,2\n3,4\n"), "n.csv", upload, header_row=None).load()
        assert list(no_header.dataframe.columns) == ["column_1", "column_2"]
        assert no_header.profile.row_count == 2
        explicit = CSVDataSource(_csv("x,y\na,b\n1,2\n"), "e.csv", upload, header_row=1).load()
        assert list(explicit.dataframe.columns) == ["a", "b"]

    def test_duplicate_and_blank_headers(self, upload: UploadConfig) -> None:
        data = CSVDataSource(_csv("qty,qty,,note\n1,2,3,x\n"), "d.csv", upload).load()
        assert list(data.dataframe.columns) == ["qty", "qty_2", "unnamed_3", "note"]

    def test_trailing_empty_unnamed_columns_dropped(self, upload: UploadConfig) -> None:
        data = CSVDataSource(_csv("a,b,,\n1,2,,\n3,4,,\n"), "t.csv", upload).load()
        assert list(data.dataframe.columns) == ["a", "b"]
        assert data.profile.dropped_columns == ("unnamed_3", "unnamed_4")

    def test_missing_values_counted(self, upload: UploadConfig) -> None:
        data = CSVDataSource(_csv("a,b\n1,\n,\n3,4\n"), "m.csv", upload).load()
        column_b = next(c for c in data.profile.columns if c.name == "b")
        assert column_b.null_count == 2
        assert column_b.null_pct == pytest.approx(66.67)
        assert data.profile.empty_row_count == 1

    def test_header_only_file(self, upload: UploadConfig) -> None:
        data = CSVDataSource(_csv("a,b,c\n"), "h.csv", upload).load()
        assert data.profile.row_count == 0
        assert data.profile.column_count == 3

    def test_inconsistent_rows_give_friendly_error(self, upload: UploadConfig) -> None:
        text = "a,b\n" + "1,2\n" * 250 + "1,2,3,4\n"
        with pytest.raises(DataSourceError) as excinfo:
            CSVDataSource(_csv(text), "bad.csv", upload).load()
        assert "inconsistent number of columns" in excinfo.value.user_message

    def test_from_path(self, upload: UploadConfig, tmp_path: Path) -> None:
        path = tmp_path / "sales.csv"
        path.write_text(SALES_CSV, encoding="utf-8")
        assert CSVDataSource.from_path(path, upload).load().profile.row_count == 60
        with user_error(DataSourceError, "not found"):
            CSVDataSource.from_path(tmp_path / "missing.csv", upload)


# -----------------------------------------------------------------------------
# Excel
# -----------------------------------------------------------------------------


class TestExcel:
    @pytest.fixture
    def workbook(self) -> bytes:
        sales = pd.read_csv(io.StringIO(SALES_CSV))
        sales["invoice_date"] = pd.to_datetime(sales["invoice_date"])
        stock = pd.DataFrame({"sku": ["S1", "S2"], "stock_qty": [5, 0]})
        return _xlsx(
            {"Sales": sales, "Stock": stock, "Empty": pd.DataFrame()},
            title_rows={"Stock": ["Stock Report", "Week 39"]},
        )

    def test_list_and_default_sheet(self, upload: UploadConfig, workbook: bytes) -> None:
        source = ExcelDataSource(workbook, "mis.xlsx", upload)
        assert source.list_sheets() == ("Sales", "Stock", "Empty")
        data = source.load()
        assert data.profile.sheet_name == "Sales"
        assert data.profile.available_sheets == ("Sales", "Stock", "Empty")
        assert data.profile.row_count == 60
        assert data.profile.column_types["invoice_date"] == "date"
        assert data.profile.column_types["quantity"] == "integer"

    def test_select_sheet_with_title_rows(self, upload: UploadConfig, workbook: bytes) -> None:
        data = ExcelDataSource(workbook, "mis.xlsx", upload, sheet_name="Stock").load()
        assert list(data.dataframe.columns) == ["sku", "stock_qty"]
        assert data.profile.header_row_index == 2
        assert data.profile.row_count == 2

    def test_unknown_sheet(self, upload: UploadConfig, workbook: bytes) -> None:
        with pytest.raises(DataSourceError) as excinfo:
            ExcelDataSource(workbook, "mis.xlsx", upload, sheet_name="Nope").load()
        assert "Available sheets: Sales, Stock, Empty" in excinfo.value.user_message

    def test_load_all_sheets_skips_empty(self, upload: UploadConfig, workbook: bytes) -> None:
        results = ExcelDataSource(workbook, "mis.xlsx", upload).load_all_sheets()
        assert set(results) == {"Sales", "Stock"}

    def test_corrupt_workbook(self, upload: UploadConfig) -> None:
        with user_error(DataSourceError, "could not be opened"):
            ExcelDataSource(b"PK\x03\x04garbage", "broken.xlsx", upload).list_sheets()


# -----------------------------------------------------------------------------
# Security
# -----------------------------------------------------------------------------


class TestSecurity:
    @pytest.mark.parametrize("name", ["report.exe", "macro.xlsm", "data.json", "noext"])
    def test_disallowed_extensions(self, upload: UploadConfig, name: str) -> None:
        with user_error(UnsupportedFileError, "not supported|not allowed"):
            validate_upload(name, b"a,b\n1,2\n", upload)

    def test_empty_file(self, upload: UploadConfig) -> None:
        with user_error(UnsupportedFileError, "empty"):
            validate_upload("a.csv", b"", upload)

    def test_oversize(self, app_config: AppConfig) -> None:
        small = app_config.settings.upload.model_copy(update={"max_file_size_mb": 0.001})
        with pytest.raises(UnsupportedFileError) as excinfo:
            validate_upload("big.csv", b"x" * 5000, small)
        assert "maximum allowed size" in excinfo.value.user_message

    def test_fake_xlsx_rejected(self, upload: UploadConfig) -> None:
        with user_error(UnsupportedFileError, "not a valid Excel"):
            validate_upload("sales.xlsx", b"a,b\n1,2\n", upload)

    @pytest.mark.parametrize(
        ("content", "label"),
        [(b"MZ\x90\x00", "executable"), (b"%PDF-1.7", "PDF"), (b"PK\x03\x04", "ZIP")],
    )
    def test_binary_disguised_as_csv(
        self, upload: UploadConfig, content: bytes, label: str
    ) -> None:
        with user_error(UnsupportedFileError, label):
            validate_upload("data.csv", content + b"rest", upload)

    def test_nul_bytes_rejected(self, upload: UploadConfig) -> None:
        with user_error(UnsupportedFileError, "binary"):
            validate_upload("data.csv", b"a,b\x00\n", upload)

    def test_file_name_sanitised(self) -> None:
        assert safe_file_name("../../etc/pass wd.csv") == "pass wd.csv"
        assert safe_file_name("C:\\Users\\x\\Q3 sales;rm.csv") == "Q3 sales_rm.csv"
        with pytest.raises(UnsupportedFileError):
            safe_file_name("../")


# -----------------------------------------------------------------------------
# Registry
# -----------------------------------------------------------------------------


class TestRegistry:
    def test_builtin_sources_registered(self) -> None:
        assert {"csv", "excel", "sql", "gsheets"} <= set(available_sources())

    def test_source_for_upload_by_extension(self, upload: UploadConfig) -> None:
        assert isinstance(source_for_upload("x.CSV", _csv(SALES_CSV), upload), CSVDataSource)
        with pytest.raises(UnsupportedFileError):
            source_for_upload("x.parquet", b"PAR1", upload)

    def test_unknown_source_type(self) -> None:
        with user_error(UnsupportedFileError, "not a supported data source"):
            create_source("ftp")


# -----------------------------------------------------------------------------
# SQL
# -----------------------------------------------------------------------------


class TestSQL:
    @pytest.fixture
    def db_url(self, tmp_path: Path) -> str:
        url = f"sqlite:///{tmp_path / 'demo.db'}"
        engine = create_engine(url)
        pd.read_csv(io.StringIO(SALES_CSV)).to_sql("sales", engine, index=False)
        engine.dispose()
        return url

    def test_table_and_query(self, db_url: str) -> None:
        table = SQLDataSource(db_url, table="sales").load()
        assert table.profile.row_count == 60
        assert table.profile.source_type == "sql"
        query = SQLDataSource(
            db_url,
            query="SELECT branch, SUM(quantity) AS qty FROM sales WHERE quantity > :q GROUP BY branch",
            params={"q": 0},
        ).load()
        assert list(query.dataframe.columns) == ["branch", "qty"]
        assert query.profile.row_count == 3

    @pytest.mark.parametrize(
        "query",
        ["DELETE FROM sales", "SELECT 1; DROP TABLE sales", "UPDATE sales SET quantity=0"],
    )
    def test_write_queries_blocked(self, db_url: str, query: str) -> None:
        with user_error(DataSourceError, "read-only|data-modifying"):
            SQLDataSource(db_url, query=query)

    def test_invalid_arguments(self, db_url: str) -> None:
        with user_error(DataSourceError, "valid table name"):
            SQLDataSource(db_url, table="sales; drop")
        with user_error(DataSourceError, "either a table or a query"):
            SQLDataSource(db_url)

    def test_missing_table(self, db_url: str) -> None:
        with user_error(DataSourceError, "query failed"):
            SQLDataSource(db_url, table="nope").load()

    def test_password_redacted(self) -> None:
        assert redact_url("postgresql://u:secret@h/db") == "postgresql://u:***@h/db"


# -----------------------------------------------------------------------------
# Google Sheets (with a fake gspread module)
# -----------------------------------------------------------------------------


class _FakeWorksheet:
    def __init__(self, title: str, values: list[list[str]]) -> None:
        self.title = title
        self._values = values

    def get_all_values(self) -> list[list[str]]:
        return self._values


class _FakeSpreadsheet:
    title = "DemoMart Targets"

    def __init__(self) -> None:
        self._sheets = [
            _FakeWorksheet(
                "Targets",
                [
                    ["Branch Targets", "", ""],
                    ["", "", ""],
                    ["branch", "month", "target"],
                    ["Powai", "Sep", "100000"],
                ],
            ),
            _FakeWorksheet("Notes", [["note"], ["hello"]]),
        ]
        self.sheet1 = self._sheets[0]

    def worksheets(self) -> list[_FakeWorksheet]:
        return self._sheets

    def worksheet(self, name: str) -> _FakeWorksheet:
        return next(s for s in self._sheets if s.title == name)


class TestGoogleSheets:
    URL = "https://docs.google.com/spreadsheets/d/abc123/edit"

    @pytest.fixture
    def credentials(self, tmp_path: Path) -> Path:
        path = tmp_path / "sa.json"
        path.write_text("{}", encoding="utf-8")
        return path

    @pytest.fixture
    def fake_gspread(self, monkeypatch: pytest.MonkeyPatch) -> None:
        module = types.ModuleType("gspread")
        client = types.SimpleNamespace(open_by_url=lambda url: _FakeSpreadsheet())
        module.service_account = lambda filename: client  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "gspread", module)

    def test_reads_worksheet(self, credentials: Path, fake_gspread: None) -> None:
        source = GoogleSheetsDataSource(self.URL, credentials)
        assert source.list_worksheets() == ("Targets", "Notes")
        data = source.load()
        assert list(data.dataframe.columns) == ["branch", "month", "target"]
        assert data.profile.sheet_name == "Targets"
        notes = GoogleSheetsDataSource(self.URL, credentials, worksheet="Notes").load()
        assert notes.dataframe["note"].iloc[0] == "hello"

    def test_invalid_url(self, credentials: Path) -> None:
        with user_error(DataSourceError, "Google Sheets link"):
            GoogleSheetsDataSource("https://example.com/sheet", credentials)

    def test_missing_credentials(self, tmp_path: Path) -> None:
        with user_error(DataSourceError, "credentials are not configured"):
            GoogleSheetsDataSource(self.URL, tmp_path / "none.json").load()

    def test_gspread_not_installed(
        self, credentials: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "gspread", None)
        with user_error(DataSourceError, "not installed"):
            GoogleSheetsDataSource(self.URL, credentials).load()


# -----------------------------------------------------------------------------
# Profiling helpers
# -----------------------------------------------------------------------------


class TestProfiling:
    @pytest.mark.parametrize(
        ("values", "expected"),
        [
            (["1", "2", "3"], "integer"),
            (["1.5", "2", "1,200.75"], "float"),
            (["10%", "12.5%", "7%"], "percent"),
            (["2026-07-01", "2026-07-02", "01/08/2026"], "date"),
            (["2026-07-01 08:30", "2026-07-01 17:05"], "datetime"),
            (["yes", "no", "Yes"], "boolean"),
            ([None, "", "  "], "empty"),
            (["INV1", "INV2", "INV3"], "string"),
            (["Powai", "Thane"] * 20, "category"),
        ],
    )
    def test_infer_column_type(self, values: list[str | None], expected: str) -> None:
        assert infer_column_type(pd.Series(values, dtype=object)) == expected

    def test_native_types(self) -> None:
        assert infer_column_type(pd.Series([1, 2, 3])) == "integer"
        assert infer_column_type(pd.Series([1.5, 2.0])) == "float"
        assert infer_column_type(pd.to_datetime(pd.Series(["2026-07-01", "2026-07-02"]))) == "date"
        mixed = pd.Series(
            [pd.Timestamp("2026-07-01 10:00"), pd.Timestamp("2026-07-02")], dtype=object
        )
        assert infer_column_type(mixed) == "datetime"

    def test_detect_header_row(self) -> None:
        raw = pd.DataFrame(
            [["Title", None, None], [None, None, None], ["a", "b", "c"], ["1", "2", "3"]]
        )
        assert detect_header_row(raw) == 2
        numeric_first = pd.DataFrame([["1", "2"], ["3", "4"]])
        assert detect_header_row(numeric_first) == 0
        assert detect_header_row(pd.DataFrame()) == 0

    def test_apply_header_beyond_rows(self) -> None:
        frame, index, dropped = apply_header(pd.DataFrame([["a", "b"]]), 5)
        assert frame.empty and index == 0 and dropped == []

    def test_profile_serialisation(self, upload: UploadConfig) -> None:
        profile = CSVDataSource(_csv(SALES_CSV), "sales.csv", upload).load().profile
        as_dict = profile.to_dict()
        assert as_dict["source_name"] == "sales.csv"
        assert as_dict["file_size_label"].endswith(("B", "KB"))
        assert list(profile.columns_frame().columns)[:2] == ["Column", "Detected Type"]
