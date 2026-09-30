"""Data-source registry.

Connectors register themselves with :func:`register_source`; the UI and CLI
then create sources by type name or by file extension without importing
concrete classes. Adding a connector = one new module + one decorator.

Example::

    @register_source("parquet", extensions=(".parquet",))
    class ParquetDataSource(FileDataSource):
        ...
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import PurePath
from typing import TYPE_CHECKING, Any, TypeVar

from smartmis.core.exceptions import UnsupportedFileError

if TYPE_CHECKING:
    from smartmis.core.config import UploadConfig
    from smartmis.ingestion.base import BaseDataSource

SourceT = TypeVar("SourceT", bound="type[BaseDataSource]")

_SOURCES: dict[str, type[BaseDataSource]] = {}
_EXTENSIONS: dict[str, str] = {}


def register_source(
    source_type: str, *, extensions: tuple[str, ...] = ()
) -> Callable[[SourceT], SourceT]:
    """Class decorator that registers a data source under ``source_type``."""

    def decorator(cls: SourceT) -> SourceT:
        _SOURCES[source_type] = cls
        for ext in extensions:
            _EXTENSIONS[ext.lower()] = source_type
        return cls

    return decorator


def available_sources() -> dict[str, type[BaseDataSource]]:
    return dict(_SOURCES)


def get_source_class(source_type: str) -> type[BaseDataSource]:
    try:
        return _SOURCES[source_type]
    except KeyError as exc:
        raise UnsupportedFileError(
            f"Unknown source type {source_type!r}",
            user_message=f"'{source_type}' is not a supported data source.",
            details={"available": sorted(_SOURCES)},
        ) from exc


def create_source(source_type: str, **kwargs: Any) -> BaseDataSource:
    """Instantiate a registered source, e.g. ``create_source("sql", url=..., table=...)``."""
    return get_source_class(source_type)(**kwargs)


def source_for_upload(
    file_name: str, content: bytes, upload_config: UploadConfig, **kwargs: Any
) -> BaseDataSource:
    """Pick the right file source for an uploaded file based on its extension."""
    extension = PurePath(file_name).suffix.lower()
    source_type = _EXTENSIONS.get(extension)
    if source_type is None or extension not in upload_config.allowed_extensions:
        allowed = ", ".join(upload_config.allowed_extensions)
        raise UnsupportedFileError(
            f"No source for extension {extension!r}",
            user_message=f"'{file_name}' is not supported. Please upload one of: {allowed}.",
            details={"file": file_name},
        )
    return create_source(
        source_type, content=content, file_name=file_name, upload_config=upload_config, **kwargs
    )
