"""Application logging.

* Console handler for developers and scheduled-run output.
* Rotating file handler (``logs/smartmis.log``) for the technical trail.
* A redaction filter that masks anything resembling a secret before it is written.

Call :func:`setup_logging` once at an entry point (UI, CLI, scheduler); modules
obtain loggers with :func:`get_logger`.
"""

from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from smartmis.core.config import AppConfig
from smartmis.core.paths import ensure_dir

ROOT_LOGGER_NAME = "smartmis"

_SECRET_PATTERN = re.compile(
    r"(?i)(password|passwd|pwd|secret|token|api[_-]?key)(\s*[=:]\s*)([^\s,;&]+)"
)
_URL_CREDENTIALS = re.compile(r"(://[^:/\s]+:)([^@/\s]+)(@)")


class RedactSecretsFilter(logging.Filter):
    """Mask ``password=...``-style values and credentials embedded in URLs."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = _SECRET_PATTERN.sub(r"\1\2***", message)
        redacted = _URL_CREDENTIALS.sub(r"\1***\3", redacted)
        if redacted != message:
            record.msg = redacted
            record.args = None
        return True


def _handler_tag(handler: logging.Handler) -> str | None:
    return getattr(handler, "_smartmis_handler", None)


def setup_logging(
    config: AppConfig,
    *,
    level: str | None = None,
    log_dir: Path | None = None,
    console: bool = True,
) -> logging.Logger:
    """Configure the ``smartmis`` logger tree. Safe to call more than once.

    Args:
        config: Loaded application configuration.
        level: Override log level (defaults to ``LOG_LEVEL`` from the environment).
        log_dir: Override log directory (defaults to ``paths.logs``).
        console: Also log to stderr.

    Returns:
        The configured root ``smartmis`` logger.
    """
    logger = logging.getLogger(ROOT_LOGGER_NAME)
    logger.setLevel(level or config.env.log_level)
    logger.propagate = False

    # Idempotent: remove handlers we previously installed (e.g. Streamlit reruns).
    for handler in list(logger.handlers):
        if _handler_tag(handler):
            logger.removeHandler(handler)
            handler.close()

    log_settings = config.settings.logging
    formatter = logging.Formatter(log_settings.format)
    redactor = RedactSecretsFilter()

    directory = ensure_dir(log_dir or config.path("logs"))
    file_handler = RotatingFileHandler(
        directory / log_settings.file_name,
        maxBytes=log_settings.max_bytes,
        backupCount=log_settings.backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(redactor)
    file_handler._smartmis_handler = "file"  # type: ignore[attr-defined]
    logger.addHandler(file_handler)

    if console:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(formatter)
        stream_handler.addFilter(redactor)
        stream_handler._smartmis_handler = "console"  # type: ignore[attr-defined]
        logger.addHandler(stream_handler)

    logger.debug("Logging initialised (level=%s, dir=%s)", logger.level, directory)
    return logger


def get_logger(name: str) -> logging.Logger:
    """Return a child of the ``smartmis`` logger, e.g. ``get_logger(__name__)``."""
    if name == ROOT_LOGGER_NAME or name.startswith(f"{ROOT_LOGGER_NAME}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{ROOT_LOGGER_NAME}.{name}")
