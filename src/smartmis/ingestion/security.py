"""Upload safety checks.

Uploaded files are **parsed, never executed**. Before any parser sees the
bytes, :func:`validate_upload` checks:

* the extension is on the allow-list (``upload.allowed_extensions``),
* the size is within ``upload.max_file_size_mb`` and not zero,
* the content matches the extension (an ``.xlsx`` must be a ZIP container;
  a ``.csv`` must be text, not a disguised binary/executable), and
* the file name is reduced to a safe base name (no paths, no control chars).
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import PurePath

from smartmis.core.config import UploadConfig
from smartmis.core.exceptions import UnsupportedFileError

_ZIP_MAGIC = b"PK\x03\x04"
_BINARY_SIGNATURES: dict[bytes, str] = {
    b"MZ": "Windows executable",
    b"\x7fELF": "Linux executable",
    b"%PDF": "PDF document",
    b"PK\x03\x04": "ZIP archive",
    b"\xd0\xcf\x11\xe0": "legacy Office (.xls/.doc) file",
    b"#!": "script",
}
_SNIFF_BYTES = 8192
_UNSAFE_CHARS = re.compile(r"[^\w.\- ()&+]")


def safe_file_name(file_name: str) -> str:
    """Strip directories and unsafe characters: ``../../x;rm.csv`` → ``x_rm.csv``."""
    base = PurePath(file_name.replace("\\", "/")).name
    base = unicodedata.normalize("NFKC", base)
    base = "".join(ch for ch in base if unicodedata.category(ch)[0] != "C")
    base = _UNSAFE_CHARS.sub("_", base).strip(" .")
    if not base:
        raise UnsupportedFileError(
            f"Unusable file name {file_name!r}",
            user_message="The file name is not valid. Please rename the file and try again.",
        )
    return base[:200]


def file_extension(file_name: str) -> str:
    return PurePath(file_name).suffix.lower()


def check_file_size(name: str, size: int, config: UploadConfig) -> None:
    """Reject empty files and files over ``upload.max_file_size_mb``."""
    if size == 0:
        raise UnsupportedFileError(
            f"{name} is empty", user_message=f"'{name}' is empty.", details={"file": name}
        )
    if size > config.max_file_size_bytes:
        raise UnsupportedFileError(
            f"{name} is {size} bytes, limit {config.max_file_size_bytes}",
            user_message=(
                f"'{name}' is {size / 1024 / 1024:.1f} MB. "
                f"The maximum allowed size is {config.max_file_size_mb:g} MB."
            ),
            details={"file": name, "size": size},
        )


def validate_upload(file_name: str, content: bytes, config: UploadConfig) -> str:
    """Validate an upload and return its sanitised file name.

    Raises:
        UnsupportedFileError: with a user-friendly message describing the problem.
    """
    name = safe_file_name(file_name)
    extension = file_extension(name)
    allowed = ", ".join(config.allowed_extensions)

    if extension not in config.allowed_extensions:
        raise UnsupportedFileError(
            f"Extension {extension or '(none)'} not allowed for {name}",
            user_message=f"'{name}' is not supported. Please upload one of: {allowed}.",
            details={"file": name, "extension": extension},
        )

    check_file_size(name, len(content), config)

    head = content[:_SNIFF_BYTES]
    if extension == ".xlsx":
        if not head.startswith(_ZIP_MAGIC):
            raise UnsupportedFileError(
                f"{name} is not a valid xlsx container",
                user_message=f"'{name}' is not a valid Excel (.xlsx) file.",
                details={"file": name},
            )
    elif extension in {".csv", ".txt", ".tsv"}:
        for signature, label in _BINARY_SIGNATURES.items():
            if head.startswith(signature):
                raise UnsupportedFileError(
                    f"{name} looks like a {label}, not CSV",
                    user_message=f"'{name}' is not a text CSV file (it looks like a {label}).",
                    details={"file": name, "detected": label},
                )
        if b"\x00" in head and not head.startswith((b"\xff\xfe", b"\xfe\xff")):
            raise UnsupportedFileError(
                f"{name} contains binary data",
                user_message=f"'{name}' contains binary data and cannot be read as CSV.",
                details={"file": name},
            )
    return name
