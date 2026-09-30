"""Data ingestion: connectors, upload security and profiling.

Importing this package registers all built-in sources.
"""

from smartmis.ingestion.base import (
    BaseDataSource,
    ColumnProfile,
    FileProfile,
    LoadedData,
    RawRead,
)
from smartmis.ingestion.file_sources import CSVDataSource, ExcelDataSource, FileDataSource
from smartmis.ingestion.gsheets_source import GoogleSheetsDataSource
from smartmis.ingestion.registry import (
    available_sources,
    create_source,
    get_source_class,
    register_source,
    source_for_upload,
)
from smartmis.ingestion.sql_source import SQLDataSource

__all__ = [
    "BaseDataSource",
    "CSVDataSource",
    "ColumnProfile",
    "ExcelDataSource",
    "FileDataSource",
    "FileProfile",
    "GoogleSheetsDataSource",
    "LoadedData",
    "RawRead",
    "SQLDataSource",
    "available_sources",
    "create_source",
    "get_source_class",
    "register_source",
    "source_for_upload",
]
