"""Which database are we talking about, and where do its cached artefacts live.

ONE SET OF CACHED ARTEFACTS PER DATABASE. Everything this package writes --
the rendered schema, the measured hints, the schema snapshot, the compiled SQL
artifacts -- describes one specific database. Keyed on anything less than the
server/port/database triple, pointing the app at a second database would
overwrite the first one's description while its compiled artifacts still
claimed to be current: every artifact would look fresh (its own fingerprint
matches) while the schema block behind it belonged somewhere else.

Resolved through functions rather than module constants because the configured
database is read at call time -- a test that repoints DB_DATABASE must see the
new paths, not the ones that happened to exist at import.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Optional

from dynamic_db.config import PACKAGE_ROOT, get_settings

logger = logging.getLogger(__name__)

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def database_identity() -> str:
    """A stable, non-secret identity for the configured database.

    Server, port and database name only -- never the username and never the
    password. This string is used for a directory name and appears in logs.
    """
    settings = get_settings()
    return f"{settings.db_server or ''}:{settings.db_port or ''}/{settings.db_database or ''}"


def identity_slug() -> str:
    """A filesystem-safe folder name for :func:`database_identity`.

    The readable part keeps a cache directory recognisable to an operator; the
    short hash keeps two databases whose readable parts collide (a host name
    with characters that sanitise to the same thing) genuinely separate.
    """
    identity = database_identity()
    readable = _SAFE.sub("_", identity).strip("_")[:48] or "database"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:10]
    return f"{readable}-{digest}"


def cache_root() -> Path:
    """The root under which every database's cache directory sits."""
    configured = get_settings().dynamic_cache_dir
    path = Path(configured)
    if not path.is_absolute():
        path = PACKAGE_ROOT / path
    return path


def cache_dir() -> Path:
    """This database's own cache directory, created on demand."""
    path = cache_root() / identity_slug()
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:  # noqa: BLE001 - a read-only cache dir must not break the app
        logger.warning("dynamic: could not create cache directory %s", path)
    return path


def cache_path(stem: str, suffix: str) -> Path:
    return cache_dir() / f"{stem}.{suffix}"


def read_cache(stem: str, suffix: str) -> str:
    """The cached text, or "" when it is absent or unreadable."""
    try:
        return cache_path(stem, suffix).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def write_cache(stem: str, suffix: str, text: str) -> None:
    """Write a cache file atomically. Never raises -- a cache that cannot be
    written degrades to "recompute every time", never to a broken request."""
    path = cache_path(stem, suffix)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:  # noqa: BLE001
        logger.warning("dynamic: could not write cache file %s", path)


def delete_cache(stem: str, suffix: str) -> None:
    path = cache_path(stem, suffix)
    try:
        if path.exists():
            path.unlink()
    except OSError:  # noqa: BLE001
        logger.warning("dynamic: could not remove cache file %s", path)


def artifact_dir(subdir: Optional[str] = None) -> Path:
    """Where compiled SQL artifacts live for this database."""
    path = cache_dir() / "artifacts"
    if subdir:
        path = path / subdir
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:  # noqa: BLE001
        logger.warning("dynamic: could not create artifact directory %s", path)
    return path
