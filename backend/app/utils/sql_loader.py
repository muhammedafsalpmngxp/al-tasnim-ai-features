"""Loader for the .sql files in backend/sql.

Large queries live in files, never inside route functions. A file may pull in
another with ``{{include:<name>}}``; this is how daily_detail.sql and
daily_summary.sql share the single row-grain resolution defined in
daily_tasks.sql.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Set

from app.config.settings import SQL_DIR

_INCLUDE = re.compile(r"\{\{include:([a-zA-Z0-9_]+)\}\}")


class SqlNotFound(FileNotFoundError):
    """The requested .sql file does not exist."""


def _read(name: str) -> str:
    path = SQL_DIR / f"{name}.sql"
    if not path.is_file():
        raise SqlNotFound(f"SQL file not found: {path}")
    return path.read_text(encoding="utf-8")


def _expand(name: str, seen: Set[str]) -> str:
    if name in seen:
        raise ValueError(f"Circular SQL include detected at {name!r}")
    seen = seen | {name}

    def replace(match: re.Match) -> str:
        return _expand(match.group(1), seen)

    return _INCLUDE.sub(replace, _read(name))


@lru_cache(maxsize=32)
def load_sql(name: str) -> str:
    """Return the fully expanded text of ``backend/sql/<name>.sql``."""
    return _expand(name, set())
