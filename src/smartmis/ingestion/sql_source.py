"""Generic SQL data source (SQLite, PostgreSQL, MySQL… via SQLAlchemy).

Read-only by design: either a ``table`` name (validated identifier) or a
single ``SELECT``/``WITH`` query is accepted. Credentials in the URL are never
logged — the source name uses SQLAlchemy's password-hiding rendering.

Drivers for external databases are installed separately, e.g.
``pip install "psycopg[binary]"`` or ``pip install pymysql``.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError

from smartmis.core.exceptions import DataSourceError
from smartmis.ingestion.base import BaseDataSource, RawRead
from smartmis.ingestion.registry import register_source

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")
_READ_ONLY_START = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|merge|replace|attach|pragma)\b",
    re.IGNORECASE,
)


def redact_url(url: str) -> str:
    try:
        return make_url(url).render_as_string(hide_password=True)
    except ArgumentError:
        return "<invalid database URL>"


@register_source("sql")
class SQLDataSource(BaseDataSource):
    """Load a table or a read-only query result."""

    source_type: ClassVar[str] = "sql"

    def __init__(
        self,
        url: str,
        *,
        table: str | None = None,
        query: str | None = None,
        params: dict[str, Any] | None = None,
        name: str | None = None,
    ) -> None:
        if (table is None) == (query is None):
            raise DataSourceError(
                "Provide exactly one of table or query",
                user_message="Choose either a table or a query for the SQL source.",
            )
        if table is not None and not _IDENTIFIER.match(table):
            raise DataSourceError(
                f"Invalid table name {table!r}",
                user_message=f"'{table}' is not a valid table name.",
            )
        if query is not None:
            self._check_read_only(query)
        self.url = url
        self.table = table
        self.query = query
        self.params = params or {}
        super().__init__(name or table or "sql_query", header_row=None)

    @staticmethod
    def _check_read_only(query: str) -> None:
        statements = [s for s in query.strip().rstrip(";").split(";") if s.strip()]
        if len(statements) != 1 or not _READ_ONLY_START.match(statements[0]):
            raise DataSourceError(
                "Only a single SELECT/WITH statement is allowed",
                user_message="SQL sources accept a single read-only SELECT query.",
            )
        if _FORBIDDEN.search(statements[0]):
            raise DataSourceError(
                "Query contains a data-modifying keyword",
                user_message="The query contains a data-modifying keyword and was blocked.",
            )

    def _read(self) -> RawRead:
        sql = f"SELECT * FROM {self.table}" if self.table else str(self.query)
        try:
            engine = create_engine(self.url)
        except (ArgumentError, ImportError) as exc:
            raise DataSourceError(
                f"Cannot create engine for {redact_url(self.url)}: {exc}",
                user_message="The database URL is invalid or its driver is not installed.",
            ) from exc
        try:
            with engine.connect() as connection:
                table = pd.read_sql_query(text(sql), connection, params=self.params)
        except (SQLAlchemyError, pd.errors.DatabaseError) as exc:
            raise DataSourceError(
                f"SQL read failed on {redact_url(self.url)}: {exc}",
                user_message="The database query failed. Check the table/query and connection.",
                details={"database": redact_url(self.url)},
            ) from exc
        finally:
            engine.dispose()
        return RawRead(
            table=table,
            headers_applied=True,
            extra={"database": redact_url(self.url), "sql": sql},
        )
