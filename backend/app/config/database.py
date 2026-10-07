"""SQL Server access. Read-only by contract.

Every statement issued through this module is validated to be SELECT-only
before it reaches the driver, and each connection is opened with autocommit so
no transaction is ever left open against the production database.
"""

from __future__ import annotations

import logging
import re
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Sequence

import pyodbc

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

#: Statements that must never be executed by this application.
FORBIDDEN_STATEMENTS = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "MERGE",
    "ALTER",
    "DROP",
    "TRUNCATE",
    "CREATE",
    "EXEC",
    "EXECUTE",
    "GRANT",
    "REVOKE",
    "BACKUP",
    "RESTORE",
)

_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)
_COMMENT_LINE = re.compile(r"--[^\n]*")
_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'", re.DOTALL)


class DatabaseUnavailable(RuntimeError):
    """The database could not be reached or the query failed."""


class ReadOnlyViolation(RuntimeError):
    """A statement that is not a pure SELECT was about to be executed."""


def _strip_noise(sql: str) -> str:
    """Remove comments and string literals so keyword scanning is meaningful."""
    without_comments = _COMMENT_LINE.sub(" ", _COMMENT_BLOCK.sub(" ", sql))
    return _STRING_LITERAL.sub(" '' ", without_comments)


def assert_read_only(sql: str) -> None:
    """Reject anything that is not a read.

    Raises:
        ReadOnlyViolation: if the statement is not a SELECT/WITH query or
            contains a forbidden keyword outside comments and literals.
    """
    cleaned = _strip_noise(sql).strip()
    if not cleaned:
        raise ReadOnlyViolation("Empty statement")

    first_word = re.split(r"\s+", cleaned, maxsplit=1)[0].upper().lstrip("(")
    if first_word not in {"SELECT", "WITH"}:
        raise ReadOnlyViolation(f"Only SELECT/WITH statements are allowed, got {first_word!r}")

    for keyword in FORBIDDEN_STATEMENTS:
        if re.search(rf"(?<![A-Za-z0-9_]){keyword}(?![A-Za-z0-9_])", cleaned, re.IGNORECASE):
            raise ReadOnlyViolation(f"Forbidden keyword {keyword} found in statement")

    if ";" in cleaned.rstrip().rstrip(";"):
        raise ReadOnlyViolation("Multiple statements are not allowed")


@contextmanager
def get_connection() -> Iterator[pyodbc.Connection]:
    """Open a short-lived read-only connection.

    pyodbc is configured to decode the server's non-Unicode (varchar) columns
    with the configured ANSI code page. ``ref.uom.uom_code`` stores values such
    as m² / m³ as single-byte characters; without this they arrive mangled.
    """
    settings = get_settings()
    try:
        connection = pyodbc.connect(
            settings.connection_string(),
            timeout=settings.db_connect_timeout,
            autocommit=True,
            readonly=True,
        )
    except pyodbc.Error as exc:
        # SQLSTATE only: the message may echo the connection string.
        sqlstate = exc.args[0] if exc.args else "unknown"
        logger.error("Database connection failed (SQLSTATE %s)", sqlstate)
        raise DatabaseUnavailable(
            "Unable to connect to the database. Check backend/.env and network access."
        ) from None

    try:
        connection.setdecoding(pyodbc.SQL_CHAR, encoding=settings.db_ansi_codepage)
        connection.setdecoding(pyodbc.SQL_WCHAR, encoding="utf-16le")
        connection.timeout = settings.db_query_timeout
        yield connection
    finally:
        connection.close()


def fetch_all(
    sql: str,
    params: Sequence[Any] = (),
    *,
    label: str = "query",
    connection: Optional[pyodbc.Connection] = None,
) -> List[Dict[str, Any]]:
    """Run a parameterised SELECT and return rows as dictionaries.

    Args:
        sql: A SELECT/WITH statement using ``?`` placeholders. User input is
            never concatenated into SQL.
        params: Values bound to the placeholders, in order.
        label: Name used in the timing log line.
        connection: Reuse an open connection instead of opening a new one.
    """
    assert_read_only(sql)

    def _run(conn: pyodbc.Connection) -> List[Dict[str, Any]]:
        started = time.perf_counter()
        cursor = conn.cursor()
        try:
            cursor.execute(sql, *params)
            columns = [column[0] for column in cursor.description or []]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        finally:
            cursor.close()
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "sql.%s executed in %.1f ms, %d row(s)", label, elapsed_ms, len(rows)
        )
        return rows

    try:
        if connection is not None:
            return _run(connection)
        with get_connection() as conn:
            return _run(conn)
    except pyodbc.Error as exc:
        sqlstate = exc.args[0] if exc.args else "unknown"
        logger.error("sql.%s failed (SQLSTATE %s)", label, sqlstate)
        raise DatabaseUnavailable(
            f"The database query '{label}' failed (SQLSTATE {sqlstate})."
        ) from None


def check_connectivity() -> Dict[str, Any]:
    """Verify the database answers and report the server it answered from."""
    rows = fetch_all(
        "SELECT DB_NAME() AS database_name, "
        "CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(64)) AS server_version, "
        "SYSDATETIME() AS server_time",
        label="connectivity",
    )
    return rows[0] if rows else {}
