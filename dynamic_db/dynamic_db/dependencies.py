"""What physical objects one compiled query actually depends on.

THIS IS WHAT MAKES A SCHEMA CHANGE CHEAP. Without it, one column added anywhere
marks every compiled capability stale and forces a full, reasoning-priced
recompile of queries that never read the changed table. With it, the change is
intersected with each capability's recorded dependencies and only the ones that
genuinely read the changed object are recompiled.

Extracted from the SQL text, deterministically, never declared by the model.
A model that reports its own dependencies is a model that can under-report
them, and an under-reported dependency is a column whose removal nobody
notices until a query fails in production at 6am.

CONSERVATIVE IN ONE DIRECTION ONLY. Where a column reference cannot be
attributed to exactly one table, it is attributed to every table that could
have supplied it. Over-recording costs one unnecessary recompile; under-
recording costs a silent break.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from dynamic_db import sqltext
from dynamic_db.introspect import SchemaSnapshot


@dataclass
class Dependencies:
    """The physical footprint of one statement."""

    tables: List[str] = field(default_factory=list)  # "schema.table", lower-case
    columns: List[Tuple[str, str]] = field(default_factory=list)  # (table, column)
    #: Tables the statement names that the current schema does not have. A
    #: non-empty list is, by itself, enough to mark a capability stale.
    missing_tables: List[str] = field(default_factory=list)
    #: Columns the statement names on a table that does have the table but not
    #: the column. Same consequence, one level down.
    missing_columns: List[Tuple[str, str]] = field(default_factory=list)
    #: Columns that could not be attributed to any known table. Not an error --
    #: a CTE's own output column looks exactly like this -- and never treated
    #: as one.
    unattributed: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "tables": list(self.tables),
            "columns": [f"{t}.{c}" for t, c in self.columns],
            "missing_tables": list(self.missing_tables),
            "missing_columns": [f"{t}.{c}" for t, c in self.missing_columns],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Dependencies":
        def split(value: str) -> Tuple[str, str]:
            parts = str(value).split(".")
            if len(parts) >= 3:
                return f"{parts[0]}.{parts[1]}".lower(), ".".join(parts[2:]).lower()
            return str(value).lower(), ""

        return cls(
            tables=[str(t).lower() for t in (data.get("tables") or [])],
            columns=[split(v) for v in (data.get("columns") or [])],
            missing_tables=[str(t).lower() for t in (data.get("missing_tables") or [])],
            missing_columns=[split(v) for v in (data.get("missing_columns") or [])],
        )

    @property
    def complete(self) -> bool:
        """Whether every object the statement names still exists."""
        return not self.missing_tables and not self.missing_columns


def extract(sql: str, snapshot: Optional[SchemaSnapshot] = None) -> Dependencies:
    """The tables and columns ``sql`` reads, resolved against ``snapshot``.

    Without a snapshot only the tables are resolved: attributing an
    unqualified column needs to know what columns each table has, and guessing
    that from a name would be exactly the kind of invisible wrong answer this
    module exists to avoid.
    """
    result = Dependencies()
    referenced = sqltext.table_references(sql)
    result.tables = list(referenced)

    if snapshot is None:
        return result

    known_columns: Dict[str, Set[str]] = {}
    for table in referenced:
        fact = snapshot.get(table)
        if fact is None:
            result.missing_tables.append(table)
            continue
        known_columns[table] = {name.lower() for name in fact.columns}

    aliases = sqltext.alias_map(sql)
    ambiguous = sqltext.ambiguous_aliases(sql)
    columns: Set[Tuple[str, str]] = set()

    # 1. Qualified references: alias.column, where the alias is a real table.
    for prefix, name in sqltext.qualified_column_references(sql):
        targets = aliases.get(prefix)
        if not targets:
            continue  # a CTE alias, a subquery alias, or a schema name
        resolvable = [t for t in targets if t in known_columns]
        if not resolvable:
            continue
        owners = [t for t in resolvable if name in known_columns[t]]
        if owners:
            for table in owners:
                columns.add((table, name))
        else:
            # Absent from EVERY table this prefix could name. Recorded rather
            # than dropped: it is the clearest possible signal that this
            # statement no longer fits the schema. Reported only when the
            # prefix unambiguously names ONE physical table and nothing else,
            # so a reused alias cannot manufacture a missing column.
            if len(resolvable) == 1 and prefix not in ambiguous:
                result.missing_columns.append((resolvable[0], name))

    # 2. Unqualified names, attributed to every referenced table that has one.
    #    A statement that qualifies everything (as this project's SQL does)
    #    adds nothing here; one that does not still records a dependency.
    words = sqltext.identifiers(sql)
    qualified_names = {name for _prefix, name in sqltext.qualified_column_references(sql)}
    for word in words:
        if word in qualified_names:
            continue
        owners = [table for table, available in known_columns.items() if word in available]
        if not owners:
            continue
        for table in owners:
            columns.add((table, word))

    result.columns = sorted(columns)
    result.missing_columns = sorted(set(result.missing_columns))
    result.missing_tables = sorted(set(result.missing_tables))
    return result


def is_affected(
    dependencies: Dependencies,
    changed_tables: Set[str],
    changed_columns: Set[Tuple[str, str]],
) -> bool:
    """Whether a change touches what this statement reads.

    Table-level and column-level intersection, kept separate so a column change
    reaches only the capabilities that read THAT column -- which is the whole
    reason dependencies are recorded per column rather than per table.
    """
    if not dependencies.complete:
        return True
    if changed_tables & {t.lower() for t in dependencies.tables}:
        return True
    return bool(changed_columns & {(t, c) for t, c in dependencies.columns})


def unknown_tables(sql: str, approved: Iterable[str]) -> List[str]:
    """Tables the statement reads that are not inside the approved boundary.

    Returns nothing when the boundary is empty: having no allowlist to check
    against must never be the reason a correct query is rejected -- the caller
    reports the empty allowlist itself, which is the real problem.
    """
    allowed = {str(name).strip().lower() for name in approved if str(name).strip()}
    if not allowed:
        return []
    return sorted(set(sqltext.table_references(sql)) - allowed)


def summarise(dependencies: Dependencies, limit: int = 8) -> str:
    """A short, log-friendly description. Never the whole list."""
    tables = dependencies.tables[:limit]
    more = len(dependencies.tables) - len(tables)
    text = ", ".join(tables) + (f" (+{more} more)" if more > 0 else "")
    return f"{text} | {len(dependencies.columns)} column(s)"


def merged(items: Sequence[Dependencies]) -> Dependencies:
    """One footprint covering several statements."""
    out = Dependencies()
    tables: List[str] = []
    columns: Set[Tuple[str, str]] = set()
    missing_tables: Set[str] = set()
    missing_columns: Set[Tuple[str, str]] = set()
    for item in items:
        for table in item.tables:
            if table not in tables:
                tables.append(table)
        columns |= set(item.columns)
        missing_tables |= set(item.missing_tables)
        missing_columns |= set(item.missing_columns)
    out.tables = tables
    out.columns = sorted(columns)
    out.missing_tables = sorted(missing_tables)
    out.missing_columns = sorted(missing_columns)
    return out
