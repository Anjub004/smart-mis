"""Project path resolution.

SmartMIS resolves every relative path in configuration against a single
*project root*, so the app behaves the same whether it is launched from the repo
root, from ``src/``, from a scheduler, or inside Docker.

Resolution order for the project root:

1. ``SMARTMIS_HOME`` environment variable, if set.
2. The nearest parent of the current working directory containing
   ``config/settings.yaml``.
3. The repository root inferred from this file's location (``src/smartmis/core``).
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

_MARKER = Path("config") / "settings.yaml"


def _find_marker_upwards(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        if (candidate / _MARKER).is_file():
            return candidate
    return None


@lru_cache(maxsize=1)
def project_root() -> Path:
    """Return the absolute project root directory."""
    env_home = os.getenv("SMARTMIS_HOME")
    if env_home:
        return Path(env_home).expanduser().resolve()

    found = _find_marker_upwards(Path.cwd().resolve())
    if found is not None:
        return found

    # src/smartmis/core/paths.py -> parents[3] is the repository root
    return Path(__file__).resolve().parents[3]


def resolve_path(path: str | Path, *, base: Path | None = None) -> Path:
    """Resolve ``path`` against ``base`` (default: project root) unless already absolute."""
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate
    return ((base or project_root()) / candidate).resolve()


def ensure_dir(path: str | Path) -> Path:
    """Create ``path`` (and parents) if needed and return it as an absolute ``Path``."""
    directory = resolve_path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory
