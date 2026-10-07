"""Read the live database and describe it compactly enough to put in a prompt.

NOTHING IN THIS MODULE NAMES A TABLE OR A COLUMN. It reads the catalogue for
the schemas the allowlist reaches into, applies ``INCLUDED_TABLES`` and the
column filters, and emits a snapshot. Point the application at a different
database and everything below follows it with no code change.

WHAT IS PRODUCED
----------------
``SchemaSnapshot``  the structured facts: tables, columns, datatypes,
                    nullability, primary keys, declared foreign keys, other
                    constraints, approximate row counts, measured grain
                    observations, and a measured numeric profile.
``render_schema()`` the same facts as the compact block the agents are shown.

TWO FINGERPRINTS, DELIBERATELY
------------------------------
``live_fingerprint``       structure + row counts + render version. Guards the
                           rendered text and the measured hints, because all of
                           them describe DATA that moves when rows are loaded.

``structure_fingerprint``  the same MINUS row counts and MINUS the render
                           version. Guards COMPILED SQL. This split is the
                           whole reason a compiled artifact survives: a new
                           day of task data arrives every morning, and an
                           artifact keyed on the live fingerprint would be
                           recompiled -- at full reasoning-model cost -- every
                           single day. A query's SQL depends on the structure
                           it reads, never on how many rows are in it today,
                           and never on how that structure was printed.

Per-table and per-column signatures exist for the same reason one level down:
"has ANYTHING in the database changed" is far too blunt a question to spend a
recompile on. What a capability actually cares about is "has anything changed
in the columns I read".
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from dynamic_db.db import get_connection
from dynamic_db.config import get_settings
from dynamic_db import identity

logger = logging.getLogger(__name__)

# Substrings that mark a column as secret -- never exposed to a prompt. Generic
# credential wording, tied to no schema, applied on top of EXCLUDED_COLUMNS.
SECRET_COLUMN_MARKERS = (
    "password",
    "secret",
    "token",
    "pwd",
    "apikey",
    "api_key",
    "credential",
    "private_key",
)

#: Bumped whenever :func:`render_schema` changes the TEXT it emits for an
#: unchanged database. Folded into the LIVE fingerprint, which guards the
#: rendered text -- and deliberately NOT into the structure fingerprint, which
#: guards compiled SQL. No way of printing a schema can make stored SQL wrong,
#: and folding it in there would invalidate every artifact over a spacing tweak.
RENDER_VERSION = "1-daily-report-dynamic"

_SNAPSHOT_STEM = "schema_snapshot"

# T-SQL reserved words. A column whose NAME is one of these must be written
# [bracketed] or the query fails to parse -- and the error misleads: SQL Server
# reports "Incorrect syntax near the keyword 'plan'" (42000), not "Invalid
# column name" (42S22), so it reads as though the column does not exist.
#
# This database really does have a column named `plan`, so this is not
# hypothetical. The list is conservative: `status`, `time`, `system` and
# `session` LOOK reserved but SQL Server accepts them bare, and bracketing
# every column would add noise to every prompt for nothing.
_TSQL_RESERVED = frozenset(
    """
ADD ALL ALTER AND ANY AS ASC AUTHORIZATION BACKUP BEGIN BETWEEN BREAK BROWSE BULK BY CASCADE CASE
CHECK CHECKPOINT CLOSE CLUSTERED COALESCE COLLATE COLUMN COMMIT COMPUTE CONSTRAINT CONTAINS
CONTAINSTABLE CONTINUE CONVERT CREATE CROSS CURRENT CURRENT_DATE CURRENT_TIME CURRENT_TIMESTAMP
CURRENT_USER CURSOR DATABASE DBCC DEALLOCATE DECLARE DEFAULT DELETE DENY DESC DISK DISTINCT
DISTRIBUTED DOUBLE DROP DUMP ELSE END ERRLVL ESCAPE EXCEPT EXEC EXECUTE EXISTS EXIT EXTERNAL FETCH
FILE FILLFACTOR FOR FOREIGN FREETEXT FREETEXTTABLE FROM FULL FUNCTION GOTO GRANT GROUP HAVING
HOLDLOCK IDENTITY IDENTITY_INSERT IDENTITYCOL IF IN INDEX INNER INSERT INTERSECT INTO IS JOIN KEY
KILL LEFT LIKE LINENO LOAD MERGE NATIONAL NOCHECK NONCLUSTERED NOT NULL NULLIF OF OFF OFFSETS ON
OPEN OPENDATASOURCE OPENQUERY OPENROWSET OPENXML OPTION OR ORDER OUTER OVER PERCENT PIVOT PLAN
PRECISION PRIMARY PRINT PROC PROCEDURE PUBLIC RAISERROR READ READTEXT RECONFIGURE REFERENCES
REPLICATION RESTORE RESTRICT RETURN REVERT REVOKE RIGHT ROLLBACK ROWCOUNT ROWGUIDCOL RULE SAVE
SCHEMA SECURITYAUDIT SELECT SESSION_USER SET SETUSER SHUTDOWN SOME STATISTICS SYSTEM_USER TABLE
TABLESAMPLE TEXTSIZE THEN TO TOP TRAN TRANSACTION TRIGGER TRUNCATE TRY_CONVERT TSEQUAL UNION
UNIQUE UNPIVOT UPDATE UPDATETEXT USE USER VALUES VARYING VIEW WAITFOR WHEN WHERE WHILE WITH
WRITETEXT
""".split()
)

# A bare T-SQL identifier may only be a letter/underscore followed by letters,
# digits, underscore, $ or #. Anything else must be bracketed -- an independent
# reason from the reserved-word list (a name containing a space fails the same
# way, and this database has one).
_BARE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#]*$")

_NUMERIC_TYPES = frozenset(
    (
        "int",
        "bigint",
        "smallint",
        "tinyint",
        "decimal",
        "numeric",
        "float",
        "real",
        "money",
        "smallmoney",
    )
)

# Whole NAME TOKENS, never substrings. Substring matching looks fine and is
# quietly wrong: "ratio" is a substring of "du-ratio-n", which would classify
# every duration column as a 0-100 percentage.
_RATIO_MARKERS = frozenset(
    (
        "pct",
        "percent",
        "percentage",
        "progress",
        "ratio",
        "share",
        "weightage",
        "weight",
        "complete",
        "completion",
        "util",
        "utilisation",
        "utilization",
    )
)
_KEY_TOKENS = frozenset(("id", "key", "code", "no", "num", "uid", "guid", "ref"))
_CAMEL_SPLIT = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

#: Below this many rows a repeated key cannot distort an answer enough to be
#: worth a scan. Keeps rebuild time flat as the fact tables grow.
_GRAIN_CHECK_MIN_ROWS = 100
#: Above this many rows the numeric profile would be a real scan on a fact
#: table. Such tables are reported as NOT PROFILED rather than silently
#: skipped, so the agents know the scale is unknown instead of assuming one.
_NUMERIC_MAX_ROWS = 20_000_000
_NUMERIC_MAX_COLS_PER_QUERY = 40


# --------------------------------------------------------------------------
# The snapshot
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ColumnFact:
    name: str
    data_type: str
    max_length: Optional[int]
    nullable: bool
    ordinal: int

    def type_text(self) -> str:
        if self.max_length and self.max_length > 0:
            return f"{self.data_type}({self.max_length})"
        if self.max_length == -1:
            return f"{self.data_type}(max)"
        return self.data_type

    def signature(self) -> str:
        """Everything about this column that SQL written against it depends on.

        The ORDINAL POSITION is deliberately absent: reordering columns cannot
        break a query that names them, and including it would invalidate every
        artifact over a harmless table rebuild.
        """
        return f"{self.data_type}|{self.max_length if self.max_length is not None else ''}|{int(self.nullable)}"


@dataclass
class TableFact:
    name: str  # "schema.table", as the catalogue spells it
    columns: Dict[str, ColumnFact] = field(default_factory=dict)
    primary_key: List[str] = field(default_factory=list)
    foreign_keys: List[str] = field(default_factory=list)  # "col -> schema.table.col"
    constraints: List[str] = field(default_factory=list)  # CHECK / DEFAULT / UNIQUE
    row_count: Optional[int] = None
    repeating_key: Optional[str] = None  # measured: many rows per this key
    #: column -> distinct values measured, beside an EXACT total. Measured data,
    #: so deliberately absent from every signature below: it moves whenever rows
    #: are loaded, and a query's SQL does not depend on it.
    key_multiplicity: Dict[str, str] = field(default_factory=dict)
    approved: bool = True  # inside INCLUDED_TABLES
    numeric_profile: Dict[str, str] = field(default_factory=dict)

    def key(self) -> str:
        return self.name.lower()

    def signature(self) -> str:
        """This table's STRUCTURE, hashed. Row counts are excluded on purpose."""
        h = hashlib.sha256()
        h.update(self.key().encode("utf-8"))
        for name in sorted(self.columns, key=str.lower):
            column = self.columns[name]
            h.update(f"\nC|{name.lower()}|{column.signature()}".encode("utf-8"))
        for name in sorted(self.primary_key, key=str.lower):
            h.update(f"\nPK|{name.lower()}".encode("utf-8"))
        for relation in sorted(self.foreign_keys, key=str.lower):
            h.update(f"\nFK|{relation.lower()}".encode("utf-8"))
        for constraint in sorted(self.constraints, key=str.lower):
            h.update(f"\nX|{constraint.lower()}".encode("utf-8"))
        return h.hexdigest()

    def relationship_signature(self) -> str:
        """Keys and constraints only -- what a JOIN depends on.

        Separate from the column signature so a capability that reads two
        columns of a table is not recompiled because a third column changed,
        while a dropped foreign key -- which can genuinely change what a join
        means -- still reaches it.
        """
        h = hashlib.sha256()
        for name in sorted(self.primary_key, key=str.lower):
            h.update(f"PK|{name.lower()}\n".encode("utf-8"))
        for relation in sorted(self.foreign_keys, key=str.lower):
            h.update(f"FK|{relation.lower()}\n".encode("utf-8"))
        for constraint in sorted(self.constraints, key=str.lower):
            h.update(f"X|{constraint.lower()}\n".encode("utf-8"))
        return h.hexdigest()


@dataclass
class SchemaSnapshot:
    """Everything known about one database at one moment."""

    database: str
    tables: Dict[str, TableFact] = field(default_factory=dict)  # keyed lower-case
    included_tables: List[str] = field(default_factory=list)
    discovery_schemas: List[str] = field(default_factory=list)
    profiled: bool = False

    # -- lookups -----------------------------------------------------------
    def get(self, table: str) -> Optional[TableFact]:
        return self.tables.get((table or "").strip().lower())

    def approved_tables(self) -> List[TableFact]:
        return [t for t in self.tables.values() if t.approved]

    def approved_names(self) -> List[str]:
        return sorted(t.key() for t in self.tables.values() if t.approved)

    def has_column(self, table: str, column: str) -> bool:
        fact = self.get(table)
        if fact is None:
            return False
        return column.lower() in {c.lower() for c in fact.columns}

    def column(self, table: str, column: str) -> Optional[ColumnFact]:
        fact = self.get(table)
        if fact is None:
            return None
        for name, col in fact.columns.items():
            if name.lower() == column.lower():
                return col
        return None

    # -- signatures --------------------------------------------------------
    def table_signatures(self) -> Dict[str, str]:
        return {key: fact.signature() for key, fact in self.tables.items()}

    def structure_fingerprint(self) -> str:
        """Structure only. Guards compiled SQL. See the module docstring."""
        h = hashlib.sha256()
        h.update(f"db={self.database}\n".encode("utf-8"))
        h.update(("included=" + ",".join(sorted(self.included_tables)) + "\n").encode("utf-8"))
        for key in sorted(self.tables):
            h.update(f"{key}={self.tables[key].signature()}\n".encode("utf-8"))
        return h.hexdigest()

    def live_fingerprint(self) -> str:
        """Structure + row counts + render version. Guards the rendered text."""
        h = hashlib.sha256()
        h.update(self.structure_fingerprint().encode("utf-8"))
        h.update(f"|render={RENDER_VERSION}\n".encode("utf-8"))
        for key in sorted(self.tables):
            h.update(f"{key}#{self.tables[key].row_count}\n".encode("utf-8"))
        return h.hexdigest()

    def dependency_fingerprint(self, tables: Iterable[str], columns: Iterable[Tuple[str, str]]) -> str:
        """One hash standing for exactly the structure a query depends on.

        A table present in ``tables`` but missing from the snapshot folds in a
        ``<missing>`` marker rather than being skipped, so its DISAPPEARANCE
        moves the hash instead of being invisible.
        """
        h = hashlib.sha256()
        for name in sorted({str(t).strip().lower() for t in tables if str(t).strip()}):
            fact = self.tables.get(name)
            h.update(
                f"T|{name}={fact.relationship_signature() if fact else '<missing>'}\n".encode("utf-8")
            )
        pairs = sorted(
            {(str(t).strip().lower(), str(c).strip().lower()) for t, c in columns if str(c).strip()}
        )
        for table, column in pairs:
            col = self.column(table, column)
            h.update(
                f"C|{table}.{column}={col.signature() if col else '<missing>'}\n".encode("utf-8")
            )
        return h.hexdigest()

    # -- serialisation -----------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "database": self.database,
            "included_tables": list(self.included_tables),
            "discovery_schemas": list(self.discovery_schemas),
            "profiled": self.profiled,
            "tables": {
                key: {
                    "name": fact.name,
                    "approved": fact.approved,
                    "row_count": fact.row_count,
                    "repeating_key": fact.repeating_key,
                    "key_multiplicity": dict(fact.key_multiplicity),
                    "primary_key": list(fact.primary_key),
                    "foreign_keys": list(fact.foreign_keys),
                    "constraints": list(fact.constraints),
                    "numeric_profile": dict(fact.numeric_profile),
                    "columns": {
                        name: {
                            "data_type": col.data_type,
                            "max_length": col.max_length,
                            "nullable": col.nullable,
                            "ordinal": col.ordinal,
                        }
                        for name, col in fact.columns.items()
                    },
                }
                for key, fact in self.tables.items()
            },
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SchemaSnapshot":
        snapshot = cls(
            database=str(data.get("database") or ""),
            included_tables=list(data.get("included_tables") or []),
            discovery_schemas=list(data.get("discovery_schemas") or []),
            profiled=bool(data.get("profiled")),
        )
        for key, raw in (data.get("tables") or {}).items():
            fact = TableFact(
                name=str(raw.get("name") or key),
                approved=bool(raw.get("approved", True)),
                row_count=raw.get("row_count"),
                repeating_key=raw.get("repeating_key"),
                key_multiplicity=dict(raw.get("key_multiplicity") or {}),
                primary_key=list(raw.get("primary_key") or []),
                foreign_keys=list(raw.get("foreign_keys") or []),
                constraints=list(raw.get("constraints") or []),
                numeric_profile=dict(raw.get("numeric_profile") or {}),
            )
            for name, col in (raw.get("columns") or {}).items():
                fact.columns[name] = ColumnFact(
                    name=name,
                    data_type=str(col.get("data_type") or ""),
                    max_length=col.get("max_length"),
                    nullable=bool(col.get("nullable")),
                    ordinal=int(col.get("ordinal") or 0),
                )
            snapshot.tables[key] = fact
        return snapshot


# --------------------------------------------------------------------------
# Visibility filters
# --------------------------------------------------------------------------


def quote_column(name: str) -> str:
    """The form SQL must copy: [bracketed] when the bare name would not parse."""
    if name.upper() in _TSQL_RESERVED or not _BARE_IDENTIFIER.match(name):
        return f"[{name}]"
    return name


def _is_secret(column: str) -> bool:
    low = column.lower()
    return any(marker in low for marker in SECRET_COLUMN_MARKERS)


def _is_excluded_column(schema: str, table: str, column: str) -> bool:
    excluded = set(get_settings().excluded_columns)
    if not excluded:
        return False
    low = column.lower()
    return (
        f"{schema}.{table}.{column}".lower() in excluded
        or f"{table}.{column}".lower() in excluded
        or low in excluded
    )


def _name_tokens(column: str) -> set:
    return {
        token
        for token in re.split(r"[^A-Za-z0-9]+", _CAMEL_SPLIT.sub("_", column).lower())
        if token
    }


def _is_key_like(column: str, primary_key: Sequence[str]) -> bool:
    if column in primary_key:
        return True
    last = re.split(r"[^A-Za-z0-9]+", _CAMEL_SPLIT.sub("_", column).lower())[-1]
    return last in _KEY_TOKENS or _name_tokens(column) == {"id"}


# --------------------------------------------------------------------------
# Catalogue reads
# --------------------------------------------------------------------------


def _schema_placeholders(schemas: Sequence[str]) -> str:
    return ",".join("?" for _ in schemas)


def _fetch_columns(cur, schemas: Sequence[str]) -> Dict[str, TableFact]:
    cur.execute(
        f"""
        SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE,
               CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE, ORDINAL_POSITION
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE LOWER(TABLE_SCHEMA) IN ({_schema_placeholders(schemas)})
        ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION
        """,
        *schemas,
    )
    tables: Dict[str, TableFact] = {}
    for sch, tbl, col, dtype, maxlen, nullable, ordinal in cur.fetchall():
        if _is_secret(col) or _is_excluded_column(sch, tbl, col):
            continue
        name = f"{sch}.{tbl}"
        fact = tables.setdefault(name.lower(), TableFact(name=name))
        fact.columns[col] = ColumnFact(
            name=col,
            data_type=str(dtype),
            max_length=int(maxlen) if maxlen is not None else None,
            nullable=str(nullable).upper() == "YES",
            ordinal=int(ordinal or 0),
        )
    return tables


def _fetch_primary_keys(cur, schemas: Sequence[str], tables: Dict[str, TableFact]) -> None:
    cur.execute(
        f"""
        SELECT sch.name, t.name, c.name, ic.key_ordinal
        FROM sys.indexes i
        JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
        JOIN sys.columns c   ON c.object_id  = ic.object_id AND c.column_id = ic.column_id
        JOIN sys.tables  t   ON t.object_id  = i.object_id
        JOIN sys.schemas sch ON sch.schema_id = t.schema_id
        WHERE i.is_primary_key = 1 AND LOWER(sch.name) IN ({_schema_placeholders(schemas)})
        ORDER BY sch.name, t.name, ic.key_ordinal
        """,
        *schemas,
    )
    for sch, tbl, col, _ordinal in cur.fetchall():
        fact = tables.get(f"{sch}.{tbl}".lower())
        # A hidden PK column is dropped here rather than later: this list feeds
        # both the PK marker and the grain scan, either of which would
        # otherwise name a column no prompt can see.
        if fact is None or col not in fact.columns:
            continue
        if col not in fact.primary_key:
            fact.primary_key.append(col)


def _fetch_foreign_keys(cur, schemas: Sequence[str], tables: Dict[str, TableFact]) -> None:
    cur.execute(
        f"""
        SELECT sch.name, t.name, c.name, rsch.name, rt.name, rc.name
        FROM sys.foreign_key_columns fkc
        JOIN sys.tables  t    ON t.object_id  = fkc.parent_object_id
        JOIN sys.schemas sch  ON sch.schema_id = t.schema_id
        JOIN sys.columns c    ON c.object_id  = fkc.parent_object_id
                             AND c.column_id  = fkc.parent_column_id
        JOIN sys.tables  rt   ON rt.object_id = fkc.referenced_object_id
        JOIN sys.schemas rsch ON rsch.schema_id = rt.schema_id
        JOIN sys.columns rc   ON rc.object_id  = fkc.referenced_object_id
                             AND rc.column_id  = fkc.referenced_column_id
        WHERE LOWER(sch.name) IN ({_schema_placeholders(schemas)})
        ORDER BY sch.name, t.name, c.name
        """,
        *schemas,
    )
    for fs, ft, fc, ts, tt, tc in cur.fetchall():
        child = tables.get(f"{fs}.{ft}".lower())
        parent = tables.get(f"{ts}.{tt}".lower())
        # Either end being invisible makes the relationship unusable -- a join
        # nobody can write -- so it is not described as if it existed.
        if child is None or parent is None:
            continue
        if fc not in child.columns or tc not in parent.columns:
            continue
        relation = f"{fc} -> {ts}.{tt}.{tc}"
        if relation not in child.foreign_keys:
            child.foreign_keys.append(relation)


def _fetch_constraints(cur, schemas: Sequence[str], tables: Dict[str, TableFact]) -> None:
    """CHECK constraints, DEFAULT values and non-PK unique indexes.

    None of these appear in INFORMATION_SCHEMA.COLUMNS, so without them a DBA
    could drop a unique index or add a CHECK and nothing here would notice --
    the description would keep asserting a guarantee that no longer holds.
    """
    placeholders = _schema_placeholders(schemas)
    queries = (
        (
            "CHECK",
            f"""
            SELECT sch.name, t.name, cc.name, cc.definition
            FROM sys.check_constraints cc
            JOIN sys.tables  t   ON t.object_id = cc.parent_object_id
            JOIN sys.schemas sch ON sch.schema_id = t.schema_id
            WHERE LOWER(sch.name) IN ({placeholders})
            ORDER BY sch.name, t.name, cc.name
            """,
        ),
        (
            "DEFAULT",
            f"""
            SELECT sch.name, t.name, c.name, dc.definition
            FROM sys.default_constraints dc
            JOIN sys.tables  t   ON t.object_id = dc.parent_object_id
            JOIN sys.columns c   ON c.object_id = dc.parent_object_id
                                AND c.column_id = dc.parent_column_id
            JOIN sys.schemas sch ON sch.schema_id = t.schema_id
            WHERE LOWER(sch.name) IN ({placeholders})
            ORDER BY sch.name, t.name, c.name
            """,
        ),
        (
            "UNIQUE",
            f"""
            SELECT sch.name, t.name, i.name, c.name
            FROM sys.indexes i
            JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
            JOIN sys.columns c   ON c.object_id = ic.object_id AND c.column_id = ic.column_id
            JOIN sys.tables  t   ON t.object_id = i.object_id
            JOIN sys.schemas sch ON sch.schema_id = t.schema_id
            WHERE i.is_unique = 1 AND i.is_primary_key = 0
              AND LOWER(sch.name) IN ({placeholders})
            ORDER BY sch.name, t.name, i.name, ic.key_ordinal
            """,
        ),
    )
    for kind, sql in queries:
        try:
            cur.execute(sql, *schemas)
        except Exception as exc:  # noqa: BLE001 - a missing permission must not stop introspection
            logger.info("dynamic: %s constraints unavailable (%s)", kind, exc)
            continue
        for sch, tbl, first, second in cur.fetchall():
            fact = tables.get(f"{sch}.{tbl}".lower())
            if fact is None:
                continue
            entry = f"{kind}|{first}|{second}"
            if entry not in fact.constraints:
                fact.constraints.append(entry)


def _fetch_row_counts(cur, schemas: Sequence[str]) -> Dict[str, int]:
    """Approximate row count per table, from catalogue metadata -- not a scan.

    Two sources, tried in order, because the first needs a permission a
    read-only reporting login is routinely not granted. Without a row count the
    "too large to profile" guard never fires and the grain scan loses its
    cheap skip -- both silent failures, so the fallback matters.
    """
    placeholders = _schema_placeholders(schemas)
    attempts = (
        ("sys.dm_db_partition_stats", "p.row_count"),
        ("sys.partitions", "p.rows"),
    )
    last_exc: Optional[Exception] = None
    for source, column in attempts:
        try:
            cur.execute(
                f"""
                SELECT s.name, t.name, SUM({column})
                FROM {source} p
                JOIN sys.tables  t ON t.object_id = p.object_id
                JOIN sys.schemas s ON s.schema_id = t.schema_id
                WHERE p.index_id IN (0, 1) AND LOWER(s.name) IN ({placeholders})
                GROUP BY s.name, t.name
                """,
                *schemas,
            )
            return {f"{s}.{t}".lower(): int(n or 0) for s, t, n in cur.fetchall()}
        except Exception as exc:  # noqa: BLE001 - try the next source before giving up
            last_exc = exc
    logger.info("dynamic: row counts unavailable (%s) - tracking structure only", last_exc)
    return {}


# --------------------------------------------------------------------------
# Measured observations
# --------------------------------------------------------------------------


#: Name endings that make a column a plausible identity for a row. Structural,
#: not domain knowledge: every database names its keys this way, and anything
#: this misses is only a hint not offered, never a wrong one asserted.
_KEY_NAME_ENDINGS = ("id", "code", "key", "no")
#: How many candidate keys one table is measured on. A bound, because each one
#: is a COUNT(DISTINCT) over the same scan and a very wide table would
#: otherwise turn one cheap pass into a slow one.
_GRAIN_MAX_CANDIDATES = 6
#: A candidate repeats MATERIALLY only below this share of distinct values.
#: Without it, a near-unique column drowns out the real signal: one table here
#: holds 110,184 rows and 110,181 distinct row ids -- three accidental
#: duplicates, worth reporting as a fact and useless as a grain warning, while
#: the column that genuinely repeats has 35,749 distinct values and is the one
#: an author has to de-duplicate on.
_GRAIN_MATERIAL_RATIO = 0.99


#: Types SQL Server refuses to DISTINCT directly. A key stored in one of these
#: is unusual and this database has one, so they are CAST rather than skipped:
#: skipping would report "no grain hazard" for the very table whose key is
#: hardest to compare.
_LOB_TYPES = frozenset(("text", "ntext", "xml"))
#: Types that are never an identity and never worth measuring.
_UNMEASURABLE_TYPES = frozenset(("image", "binary", "varbinary", "geography", "geometry"))


def _distinct_expression(column: "ColumnFact") -> Optional[str]:
    """How to COUNT(DISTINCT ...) this column, or None when it cannot be one."""
    data_type = column.data_type.lower()
    if data_type in _UNMEASURABLE_TYPES:
        return None
    if data_type in _LOB_TYPES or column.max_length == -1:
        return f"CAST([{column.name}] AS nvarchar(4000))"
    return f"[{column.name}]"


def _grain_scan_per_column(
    cur, schema: str, table: str, fact: "TableFact", candidates: List[str]
) -> Optional[List[Any]]:
    """The same measurement, one column at a time, tolerating individual failures."""
    try:
        cur.execute(f"SELECT COUNT_BIG(*) FROM [{schema}].[{table}]")
        row: List[Any] = [cur.fetchone()[0]]
    except Exception:  # noqa: BLE001
        return None
    for name in list(candidates):
        expression = _distinct_expression(fact.columns[name])
        try:
            cur.execute(
                f"SELECT COUNT(DISTINCT {expression}) FROM [{schema}].[{table}]"
            )
            row.append(cur.fetchone()[0])
        except Exception:  # noqa: BLE001
            # Measured as "not measurable" rather than as zero: zero would read
            # as an all-NULL column and suppress a real grain warning.
            row.append(None)
    return row


def _detect_repeating_keys(cur, snapshot: SchemaSnapshot) -> None:
    """Measure, per approved table, how many rows it holds per identity column.

    Forgetting to de-duplicate a table that holds many rows per thing is the
    single most damaging SQL mistake available in this database: it multiplies
    every count on the dashboard, and the query still runs and still looks
    right. Measuring it here means the author learns it from the database
    itself rather than from a comment somebody has to remember to write.

    THE TOTAL IS COUNTED EXACTLY, in the same pass. The catalogue row count is
    approximate -- fine for deciding whether a table is worth scanning, and
    quietly wrong for this: an approximate 110,184 against an exact 110,180
    distinct values reports a unique key as repeating, which would send every
    author chasing a de-duplication that is not needed.

    A TABLE WITH A UNIQUE CANDIDATE KEY GETS NO WARNING AT ALL. It already has
    one row per thing, so every other column in it "repeats" by definition and
    a marker would be noise -- on a one-row-per-well table, "many rows per rig"
    is true, useless, and actively misleading about what has to be
    de-duplicated.

    Otherwise the reported key is the FINEST candidate that repeats MATERIALLY:
    the one with the most distinct values, below the materiality threshold. The
    coarsest column always repeats the most and says the least -- "many rows per
    schedule" is true of almost any table and tells nobody anything.
    """
    for fact in snapshot.approved_tables():
        approximate = fact.row_count
        if approximate is not None and (
            approximate < _GRAIN_CHECK_MIN_ROWS or approximate > _NUMERIC_MAX_ROWS
        ):
            continue

        unique_by_declaration = {name.lower() for name in fact.primary_key} if len(
            fact.primary_key
        ) == 1 else set()
        candidates = [
            name
            for name in fact.columns
            if name.lower().endswith(_KEY_NAME_ENDINGS)
            and name.lower() not in unique_by_declaration
        ][:_GRAIN_MAX_CANDIDATES]
        if not candidates:
            continue

        schema, _, table = fact.name.partition(".")
        expressions = []
        measurable = []
        for name in candidates:
            expression = _distinct_expression(fact.columns[name])
            if expression is None:
                continue
            measurable.append(name)
            expressions.append(f"COUNT(DISTINCT {expression})")
        if not measurable:
            continue
        candidates = measurable

        try:
            cur.execute(
                f"SELECT {', '.join(['COUNT_BIG(*)'] + expressions)} "
                f"FROM [{schema}].[{table}]"
            )
            row = list(cur.fetchone())
        except Exception as exc:  # noqa: BLE001
            # One un-measurable column must not cost the whole table its grain
            # measurement. Retrying per column is slower and only happens on a
            # table that already failed, which is rare and worth the honesty:
            # silently reporting "no grain hazard" for a table nobody could
            # measure is the one outcome worse than measuring it twice.
            logger.info(
                "dynamic: grain scan for %s failed as one query (%s) - measuring per column",
                fact.name,
                exc,
            )
            row = _grain_scan_per_column(cur, schema, table, fact, candidates)
            if row is None:
                continue

        total = int(row[0] or 0)
        if not total:
            continue
        best_name, best_distinct = None, -1
        has_unique_identity = bool(unique_by_declaration)
        for name, measured in zip(candidates, row[1:]):
            if measured is None:
                fact.key_multiplicity[name] = f"not measurable (of {total:,} rows)"
                continue
            distinct = int(measured)
            fact.key_multiplicity[name] = f"{distinct:,} distinct of {total:,}"
            if distinct >= total:
                has_unique_identity = True
                continue
            if distinct <= 0:
                continue  # every value NULL: not a key, and not a grain signal
            if distinct / total > _GRAIN_MATERIAL_RATIO:
                continue
            if distinct > best_distinct:
                best_name, best_distinct = name, distinct
        fact.repeating_key = None if has_unique_identity else best_name


def _classify_scale(column: str, lo, hi, primary_key: Sequence[str]) -> str:
    """Name the observed scale so a comparison uses the right bound.

    Reports what was MEASURED and flags what contradicts the column's own
    name. It never rewrites a value and never decides anything is wrong.
    """
    if lo is None or hi is None:
        return "no data"
    ratio_ish = bool(_name_tokens(column) & _RATIO_MARKERS) and not _is_key_like(
        column, primary_key
    )
    try:
        lo_f, hi_f = float(lo), float(hi)
    except (TypeError, ValueError):
        return "unknown"
    if not ratio_ish:
        return "plain number"
    if hi_f > 100:
        return f"PERCENT_100 but MAX={hi_f:g} EXCEEDS 100 - out-of-range values present"
    if lo_f < 0:
        return f"proportion but MIN={lo_f:g} is NEGATIVE - out-of-range values present"
    if hi_f <= 1.0:
        return "FRACTION_1 (0-1) - multiply by 100 to report a percentage"
    return "PERCENT_100 (0-100) - already a percentage, do NOT multiply"


def _profile_numeric(cur, snapshot: SchemaSnapshot) -> None:
    """Measure the range and scale of every numeric column, one pass per table.

    INFORMATION_SCHEMA cannot answer this: `decimal` says nothing about the
    range a column actually holds, and comparing a 0-1 column against 100 fails
    SILENTLY and convincingly -- the query runs and reports a confident zero.
    """
    for fact in snapshot.approved_tables():
        total = fact.row_count
        if total is not None and total > _NUMERIC_MAX_ROWS:
            fact.numeric_profile = {"__not_profiled__": f"{total:,} rows - scale UNKNOWN"}
            continue
        numeric = [
            name
            for name, col in fact.columns.items()
            if col.data_type.lower() in _NUMERIC_TYPES
        ][:_NUMERIC_MAX_COLS_PER_QUERY]
        if not numeric:
            continue
        schema, _, table = fact.name.partition(".")
        parts = ["COUNT(*)"]
        for name in numeric:
            quoted = f"[{name}]"
            parts += [
                f"MIN(CAST({quoted} AS float))",
                f"MAX(CAST({quoted} AS float))",
                f"COUNT({quoted})",
            ]
        try:
            cur.execute(f"SELECT {', '.join(parts)} FROM [{schema}].[{table}]")
            row = list(cur.fetchone())
        except Exception as exc:  # noqa: BLE001 - one bad table must not stop profiling
            logger.info("dynamic: numeric profile skipped for %s (%s)", fact.name, exc)
            continue
        rows = int(row[0] or 0)
        if not rows:
            continue
        index = 1
        for name in numeric:
            lo, hi, non_null = row[index : index + 3]
            index += 3
            scale = _classify_scale(name, lo, hi, fact.primary_key)
            nulls = rows - int(non_null or 0)
            span = (
                f"{float(lo):g}..{float(hi):g}"
                if lo is not None and hi is not None
                else "-"
            )
            # A column with a real SCALE earns the null rate beside it, because
            # that changes whether a join may require a match at all. A plain
            # number gets its range and nothing more: two thirds of a hint file
            # reading "plain number, avg ..., stdev ..." for identifier columns
            # is a third of a prompt nobody can act on.
            if scale == "plain number":
                fact.numeric_profile[name] = span
            else:
                fact.numeric_profile[name] = (
                    f"{span} | nulls {_null_pct(nulls, rows)} | {scale}"
                )


def _null_pct(nulls: int, total: int) -> str:
    """Never round to a figure that contradicts the statistics beside it.

    Plain rounding reports "100%" for a column that is 99.6% null, which then
    sits next to that column's own min and max. A reader resolves the
    contradiction by disbelieving one of them, and either choice is wrong.
    """
    if total <= 0 or nulls <= 0:
        return "0%"
    if nulls >= total:
        return "100%"
    pct = 100.0 * nulls / total
    if pct < 1:
        return "<1%"
    if pct > 99:
        return ">99%"
    return f"{pct:.0f}%"


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def render_schema(snapshot: SchemaSnapshot, tables: Optional[Sequence[str]] = None) -> str:
    """The compact schema block the agents are shown.

    ``tables`` narrows it to the objects one capability needs. Built
    identically every time and in a stable order, so the author and the
    verifier see BYTE-IDENTICAL text and a provider can prefix-cache it.
    """
    wanted = (
        sorted({str(t).strip().lower() for t in tables if str(t).strip()})
        if tables
        else snapshot.approved_names()
    )
    lines: List[str] = []
    for key in wanted:
        fact = snapshot.tables.get(key)
        if fact is None:
            lines.append(f"TABLE {key}  -- NOT PRESENT in the current database")
            lines.append("")
            continue
        count = fact.row_count
        if count == 0:
            lines.append(f"TABLE {fact.name}  -- EMPTY: 0 rows. Nothing can be found here.")
        elif count:
            lines.append(f"TABLE {fact.name}  -- {count:,} rows")
        else:
            # Unknown is not empty, and claiming otherwise would suppress real
            # work over a missing permission.
            lines.append(f"TABLE {fact.name}")
        for name in sorted(fact.columns, key=lambda n: fact.columns[n].ordinal):
            col = fact.columns[name]
            null = "" if col.nullable else " NOT NULL"
            pk = " PK" if name in fact.primary_key else ""
            measured = fact.numeric_profile.get(name)
            suffix = f"   [{measured}]" if measured else ""
            lines.append(f"  - {quote_column(name)} {col.type_text()}{null}{pk}{suffix}")
        if fact.repeating_key:
            key_text = quote_column(fact.repeating_key)
            lines.append(
                f"  MANY ROWS PER {key_text} - de-duplicate (COUNT(DISTINCT ...), "
                f"GROUP BY or a ranked ROW_NUMBER) before counting anything per-{key_text}"
            )
        if fact.key_multiplicity:
            # The measurement behind the marker, and the more useful half of it.
            # The marker names ONE key; this states how every candidate behaves,
            # so the author can resolve to the grain the RULES define rather
            # than to the one a heuristic guessed. A table with a unique row id
            # can still fan a join out badly, and only these numbers show it.
            measured = "; ".join(
                f"{name} {value}" for name, value in sorted(fact.key_multiplicity.items())
            )
            lines.append(
                f"  MEASURED ROW MULTIPLICITY (exact): {measured}. Joining or grouping on a "
                f"column whose distinct count is below the row count returns more than one "
                f"row per value."
            )
        for relation in fact.foreign_keys:
            # Only relationships whose OTHER END is also being shown. A foreign
            # key pointing at a table outside this block is a join nobody may
            # write, and naming it would put an object the author is forbidden
            # to use in front of it -- an invitation to spend a whole rewrite
            # cycle on a table the validator will reject.
            target = relation.partition(" -> ")[2].rsplit(".", 1)[0].lower()
            if target in set(wanted):
                lines.append(f"  FK: {relation}")
        not_profiled = fact.numeric_profile.get("__not_profiled__")
        if not_profiled:
            lines.append(f"  NOT PROFILED ({not_profiled}) - do not assume a numeric scale")
        lines.append("")
    return "\n".join(lines).strip()


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------


def _apply_allowlist(tables: Dict[str, TableFact], included: Sequence[str]) -> None:
    """Mark which discovered tables are inside the approved boundary.

    Tables outside it are kept in the snapshot -- change detection needs to see
    a plausible replacement for a table that vanished -- but never rendered
    into a prompt unless discovery explicitly asks for them.
    """
    approved = set(included)
    for key, fact in tables.items():
        fact.approved = key in approved


def introspect(use_cache: bool = True) -> SchemaSnapshot:
    """Read the live database and return a snapshot of it.

    With ``use_cache`` the measured observations (grain, numeric profile) are
    reused whenever the LIVE fingerprint still matches what they were built
    from. The catalogue itself is always re-read: those queries are metadata
    only and effectively free, and reading them is what lets a change be
    NOTICED rather than waited for.
    """
    settings = get_settings()
    schemas = settings.included_schemas
    if not schemas:
        logger.warning(
            "dynamic: INCLUDED_TABLES is empty - no database object is approved, "
            "so nothing can be introspected"
        )
        return SchemaSnapshot(database=identity.database_identity())

    with get_connection() as connection:
        cur = connection.cursor()
        tables = _fetch_columns(cur, schemas)
        _fetch_primary_keys(cur, schemas, tables)
        _fetch_foreign_keys(cur, schemas, tables)
        _fetch_constraints(cur, schemas, tables)
        counts = _fetch_row_counts(cur, schemas)
        for key, fact in tables.items():
            fact.row_count = counts.get(key)
        _apply_allowlist(tables, settings.included_tables)

        snapshot = SchemaSnapshot(
            database=identity.database_identity(),
            tables=tables,
            included_tables=list(settings.included_tables),
            discovery_schemas=list(settings.discovery_schemas),
        )

        cached = _read_cached_snapshot() if use_cache else None
        if cached is not None and cached.live_fingerprint() == snapshot.live_fingerprint():
            return cached

        _detect_repeating_keys(cur, snapshot)
        if settings.dynamic_profile_numeric:
            _profile_numeric(cur, snapshot)
        snapshot.profiled = True

    _write_cached_snapshot(snapshot)
    logger.info(
        "dynamic: schema read for %s -- %d approved table(s), structure %s",
        snapshot.database,
        len(snapshot.approved_tables()),
        snapshot.structure_fingerprint()[:12],
    )
    return snapshot


def _read_cached_snapshot() -> Optional[SchemaSnapshot]:
    raw = identity.read_cache(_SNAPSHOT_STEM, "json")
    if not raw.strip():
        return None
    try:
        return SchemaSnapshot.from_dict(json.loads(raw))
    except (ValueError, TypeError, KeyError):
        logger.warning("dynamic: cached schema snapshot is unreadable; re-reading the database")
        return None


def _write_cached_snapshot(snapshot: SchemaSnapshot) -> None:
    identity.write_cache(
        _SNAPSHOT_STEM, "json", json.dumps(snapshot.to_dict(), indent=2, sort_keys=True)
    )


def load_previous_snapshot() -> Optional[SchemaSnapshot]:
    """The last snapshot the change detector recorded, or ``None`` first time."""
    raw = identity.read_cache("schema_previous", "json")
    if not raw.strip():
        return None
    try:
        return SchemaSnapshot.from_dict(json.loads(raw))
    except (ValueError, TypeError, KeyError):
        return None


def store_previous_snapshot(snapshot: SchemaSnapshot) -> None:
    """Record this snapshot as the baseline the NEXT run compares against."""
    identity.write_cache(
        "schema_previous", "json", json.dumps(snapshot.to_dict(), indent=2, sort_keys=True)
    )


def refresh() -> SchemaSnapshot:
    """Force a full re-read, discarding the measured observations cache."""
    identity.delete_cache(_SNAPSHOT_STEM, "json")
    return introspect(use_cache=False)
