"""Shared pytest fixtures."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from smartmis.core.config import AppConfig, load_config

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_CONFIG_DIR = REPO_ROOT / "config"

_ENV_VARS = (
    "SMARTMIS_ENV",
    "LOG_LEVEL",
    "DATABASE_URL",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
    "SMTP_FROM",
    "SMTP_USE_TLS",
    "REPORT_RECIPIENTS",
    "GOOGLE_SERVICE_ACCOUNT_FILE",
    "AI_API_KEY",
    "AI_MODEL",
    "SMARTMIS_CONFIG_DIR",
)


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Keep tests independent of the developer's shell and real ``.env``."""
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SMARTMIS_HOME", str(tmp_path))
    from smartmis.core import paths

    paths.project_root.cache_clear()
    yield
    paths.project_root.cache_clear()


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """A writable copy of the repository's config folder."""
    target = tmp_path / "config"
    shutil.copytree(REPO_CONFIG_DIR, target)
    return target


@pytest.fixture
def app_config(config_dir: Path, tmp_path: Path) -> AppConfig:
    """Configuration loaded from the copied config folder with no ``.env``."""
    return load_config(config_dir, env_file=tmp_path / "missing.env")
