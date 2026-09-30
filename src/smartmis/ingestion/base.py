"""Data-source abstraction and load results.

Every connector (CSV, Excel, Google Sheets, SQL…) subclasses
:class:`BaseDataSource` and implements a single method, :meth:`_read`, that
returns the raw table *without headers applied*. The base class then:

1. detects the header row (unless told explicitly),
2. drops fully blank, unnamed trailing columns,
3. profiles the result (types, nulls, uniqueness), and
4. wraps unexpected errors in :class:`DataSourceError`.

Values are loaded **as-is** (text stays text). Converting types is the job of
the validation and cleaning stages, so nothing is silently changed on load.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, ClassVar, Literal

import pandas as pd

from smartmis.core.exceptions import DataSourceError, SmartMISError
from smartmis.core.logging import get_logger
from smartmis.core.timing import timed
from smartmis.ingestion.profiling import apply_header, profile_columns

log = get_logger(__name__)

HeaderMode = Literal["auto"] | int | None


@dataclass(frozen=True)
class ColumnProfile:
    """Summary of a single column as loaded."""

    name: str
    detected_type: str
    non_null_count: int
    null_count: int
    null_pct: float
    unique_count: int
    sample_values: tuple[str, ...]


@dataclass(frozen=True)
class FileProfile:
    """Metadata describing what was loaded and from where."""

    source_name: str
    source_type: str
    row_count: int
    column_count: int
    columns: tuple[ColumnProfile, ...]
    loaded_at: datetime
    load_seconds: float
    header_row_index: int | None
    empty_row_count: int
    dropped_columns: tuple[str, ...] = ()
    file_size_bytes: int | None = None
    file_hash: str | None = None
    sheet_name: str | None = None
    available_sheets: tuple[str, ...] = ()
    encoding: str | None = None
    delimiter: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def file_size_label(self) -> str:
        """Human-friendly size such as ``1.4 MB``."""
        if self.file_size_bytes is None:
            return "—"
        size = float(self.file_size_bytes)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} GB"  # pragma: no cover - loop always returns

    @property
    def column_types(self) -> dict[str, str]:
        return {c.name: c.detected_type for c in self.columns}

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["loaded_at"] = self.loaded_at.isoformat(timespec="seconds")
        data["file_size_label"] = self.file_size_label
        return data

    def columns_frame(self) -> pd.DataFrame:
        """Column profile as a DataFrame for display in the UI."""
        return pd.DataFrame(
            [
                {
                    "Column": c.name,
                    "Detected Type": c.detected_type,
                    "Non-null": c.non_null_count,
                    "Missing": c.null_count,
                    "Missing %": round(c.null_pct, 2),
                    "Unique": c.unique_count,
                    "Examples": ", ".join(c.sample_values),
                }
                for c in self.columns
            ]
        )


@dataclass(frozen=True)
class LoadedData:
    """A loaded table plus its profile."""

    dataframe: pd.DataFrame
    profile: FileProfile

    def preview(self, rows: int = 50) -> pd.DataFrame:
        return self.dataframe.head(rows)


@dataclass
class RawRead:
    """What a connector returns from :meth:`BaseDataSource._read`."""

    table: pd.DataFrame
    file_size_bytes: int | None = None
    file_hash: str | None = None
    sheet_name: str | None = None
    available_sheets: tuple[str, ...] = ()
    encoding: str | None = None
    delimiter: str | None = None
    headers_applied: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


def sha256_of(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class BaseDataSource(ABC):
    """Common behaviour for all data sources.

    Subclasses set :attr:`source_type` and implement :meth:`_read`.
    """

    source_type: ClassVar[str] = "base"
    read_error_hint: ClassVar[str] = "Check the connection details and try again."

    def __init__(self, name: str, *, header_row: HeaderMode = "auto") -> None:
        self.name = name
        self.header_row = header_row

    @abstractmethod
    def _read(self) -> RawRead:
        """Return the raw table. Headers may or may not already be applied."""

    def load(self) -> LoadedData:
        """Read, apply headers and profile the data.

        Raises:
            DataSourceError: if the source cannot be read or contains no data.
        """
        with timed(f"load:{self.name}", log) as timer:
            try:
                raw = self._read()
            except SmartMISError:
                raise
            except Exception as exc:  # connectors raise many library-specific errors
                raise DataSourceError(
                    f"Failed to read {self.source_type} source '{self.name}': {exc}",
                    user_message=f"'{self.name}' could not be read. {self.read_error_hint}",
                    details={"source": self.name, "source_type": self.source_type},
                ) from exc

            dropped: list[str] = []
            header_index: int | None = None
            if raw.headers_applied:
                table = raw.table
            else:
                table, header_index, dropped = apply_header(raw.table, self.header_row)

            if table.columns.empty:
                raise DataSourceError(
                    f"Source '{self.name}' contains no columns",
                    user_message=f"'{self.name}' appears to be empty.",
                    details={"source": self.name},
                )

            empty_rows = int(table.isna().all(axis=1).sum()) if len(table) else 0
            columns = tuple(ColumnProfile(**c) for c in profile_columns(table))

        profile = FileProfile(
            source_name=self.name,
            source_type=self.source_type,
            row_count=len(table),
            column_count=len(table.columns),
            columns=columns,
            loaded_at=datetime.now().replace(microsecond=0),
            load_seconds=round(timer.elapsed, 3),
            header_row_index=header_index,
            empty_row_count=empty_rows,
            dropped_columns=tuple(dropped),
            file_size_bytes=raw.file_size_bytes,
            file_hash=raw.file_hash,
            sheet_name=raw.sheet_name,
            available_sheets=raw.available_sheets,
            encoding=raw.encoding,
            delimiter=raw.delimiter,
            extra=raw.extra,
        )
        log.info(
            "Loaded %s '%s'%s: %d rows x %d columns in %.2fs",
            self.source_type,
            self.name,
            f" [{raw.sheet_name}]" if raw.sheet_name else "",
            profile.row_count,
            profile.column_count,
            profile.load_seconds,
        )
        return LoadedData(dataframe=table, profile=profile)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(name={self.name!r})"
