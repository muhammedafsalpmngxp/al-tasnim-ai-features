"""Deterministic, schema-derived observations about one query. Advisory, never fatal.

These go to the verifier as EVIDENCE TO ADJUDICATE, not as rules to obey. Its
prompt tells it not to reject on a hunch, and a concern derived from the
database's own declared keys, measured grain and measured scales is not a
hunch -- it gives the reviewer something factual to rule on. The checks are
deliberately conservative and can flag a query that is perfectly fine, which is
exactly why a reviewer adjudicates them rather than a hard gate rejecting them.

NOTHING HERE IS SPECIFIC TO ANY DATABASE. Every observation is derived at
runtime from the snapshot introspection already built:

  * declared keys and foreign keys   what a join is supposed to line up with
  * measured repeating keys          where a table holds many rows per thing
  * measured numeric scale           what a comparison is actually comparing
  * measured row counts              whether a scope source holds anything
  * declared types                   whether a comparison can even be made

Point the application at a different database and the checks follow it with no
code change.

WHAT THESE CHECKS DELIBERATELY CANNOT SEE
-----------------------------------------
An alias resolves to a physical table only when it unambiguously names one. A
column reached through a CTE has no declared type here -- the CTE's own output
type would have to be inferred, and inferring it wrongly would send the
reviewer to fix something that is not broken. So a query that does all its work
inside CTEs (as the baselines here do) raises fewer concerns than one that
joins tables directly. That is the right trade: these are EVIDENCE, and
evidence that is sometimes absent is far better than evidence that is sometimes
invented.

WHY SCALE AND TYPE ARE CHECKED HERE AND NOT LEFT TO THE MODEL
-------------------------------------------------------------
Both fail SILENTLY and in the most convincing direction. A 0-1 fraction
compared against 100 returns a clean-looking result and reports the data as
healthy; nothing downstream can tell that apart from a genuine pass. A
varchar/int comparison does the opposite and throws at runtime, months later,
on the one row that holds something non-numeric. Both are decidable arithmetic
rather than opinion, so both are decided.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Sequence, Set

from dynamic_db import sqltext
from dynamic_db.capabilities import Capability
from dynamic_db.introspect import SchemaSnapshot

logger = logging.getLogger(__name__)

#: Ways a query collapses a table to one row per thing. Any one of them is
#: enough for the grain concern not to apply.
_COLLAPSING = (
    "count(distinct",
    "group by",
    "row_number",
    "rank()",
    "dense_rank",
    "select distinct",
)

_STRING_TYPES = frozenset(
    ("char", "varchar", "nchar", "nvarchar", "text", "ntext", "sysname")
)
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
        "bit",
    )
)
_TEMPORAL_TYPES = frozenset(
    ("date", "datetime", "datetime2", "smalldatetime", "datetimeoffset", "time")
)

_CONVERSION = re.compile(r"\b(TRY_)?(CAST|CONVERT|PARSE)\s*\(", re.IGNORECASE)


def _family(data_type: str) -> str:
    low = (data_type or "").lower()
    if low in _STRING_TYPES:
        return "text"
    if low in _NUMERIC_TYPES:
        return "number"
    if low in _TEMPORAL_TYPES:
        return "date"
    if low == "uniqueidentifier":
        return "guid"
    return low or "unknown"


def _is_converted(sql_low: str, alias: str, column: str) -> bool:
    """Whether this reference sits inside a conversion function call.

    A deliberately narrow window: the reference must appear within the same
    parenthesised call as the conversion keyword. Matching anywhere in the
    statement would silence the concern for a query that converts the column in
    one place and compares it raw in another, which is the case most worth
    reporting.
    """
    pattern = re.compile(
        r"\b(?:TRY_)?(?:CAST|CONVERT|PARSE)\s*\([^()]*\b"
        + re.escape(alias)
        + r"\s*\.\s*\[?"
        + re.escape(column)
        + r"\]?",
        re.IGNORECASE,
    )
    return bool(pattern.search(sql_low))


def _equality_joins(sql: str) -> List[tuple]:
    """(left_alias, left_col, right_alias, right_col) for every `a.x = b.y`."""
    pattern = re.compile(
        rf"({sqltext.IDENT})\s*\.\s*({sqltext.IDENT})\s*=\s*({sqltext.IDENT})\s*\.\s*({sqltext.IDENT})"
    )
    out = []
    for left_a, left_c, right_a, right_c in pattern.findall(sqltext.clean(sql)):
        out.append(
            (
                sqltext.bare(left_a).lower(),
                sqltext.bare(left_c).lower(),
                sqltext.bare(right_a).lower(),
                sqltext.bare(right_c).lower(),
            )
        )
    return out


def _resolve(
    aliases: Dict[str, Set[str]],
    prefix: str,
    ambiguous: Optional[Set[str]] = None,
) -> Optional[str]:
    """The one table a prefix names, or None when it is ambiguous or unknown.

    An alias that also names a CTE somewhere in the same statement resolves to
    nothing here. Reporting a concern about a column the query never claimed
    belonged to that table sends the author to fix something that is not wrong.
    """
    if ambiguous and prefix in ambiguous:
        return None
    targets = aliases.get(prefix)
    if targets and len(targets) == 1:
        return next(iter(targets))
    return None


def _grain_concerns(sql_low: str, snapshot: SchemaSnapshot, tables: Sequence[str]) -> List[str]:
    concerns: List[str] = []
    collapsing = any(token in sql_low for token in _COLLAPSING)
    for table in tables:
        fact = snapshot.get(table)
        if fact is None or not fact.repeating_key:
            continue
        if not collapsing:
            concerns.append(
                f"{fact.name} was MEASURED to hold many rows per {fact.repeating_key}, "
                f"and the query neither ranks, groups nor de-duplicates it, so each "
                f"thing is represented more than once and every count built on it is "
                f"multiplied."
            )
    return concerns


def _partition_key_concerns(
    sql: str, snapshot: SchemaSnapshot, aliases: Dict[str, Set[str]], ambiguous: Set[str]
) -> List[str]:
    """A ranking partitioned on a column that is unique per ROW.

    Partitioning by a surrogate row key leaves every row in its own group, so
    the ranking collapses nothing at all while looking exactly like a query
    that does. Caught here because it is decidable: the schema says the column
    is a single-column primary key.
    """
    concerns: List[str] = []
    body = sqltext.clean(sql)
    for match in re.finditer(r"PARTITION\s+BY\s+([^)]+)\)", body, re.IGNORECASE):
        keys = match.group(1)
        for prefix, column in sqltext._QUALIFIED.findall(keys):
            table = _resolve(aliases, sqltext.bare(prefix).lower(), ambiguous)
            if not table:
                continue
            fact = snapshot.get(table)
            if fact is None:
                continue
            primary = [name.lower() for name in fact.primary_key]
            if len(primary) == 1 and sqltext.bare(column).lower() == primary[0]:
                concerns.append(
                    f"The ranking partitions on {fact.name}.{sqltext.bare(column)}, which the "
                    f"database declares as that table's single-column primary key -- it is "
                    f"unique per ROW, so every row lands in its own partition and nothing is "
                    f"actually resolved to one row per thing."
                )
    return concerns


_MULTIPLICITY = re.compile(r"([\d,]+)\s+distinct\s+of\s+([\d,]+)")


def _join_multiplicity_concerns(
    sql: str,
    snapshot: SchemaSnapshot,
    aliases: Dict[str, Set[str]],
    ambiguous: Set[str],
    collapsing: bool,
) -> List[str]:
    """A join onto a column MEASURED to hold several rows per value.

    Stated from the measurement rather than from the grain marker, because the
    two answer different questions. The marker asks "does this table have a row
    identity"; this asks "will THIS join multiply rows", and a table with a
    perfectly good surrogate key -- a bridge table, for instance -- answers no
    to the first and yes to the second.
    """
    if collapsing:
        return []
    concerns: List[str] = []
    for left_alias, left_col, right_alias, right_col in _equality_joins(sql):
        for alias, column in ((left_alias, left_col), (right_alias, right_col)):
            table = _resolve(aliases, alias, ambiguous)
            if not table:
                continue
            fact = snapshot.get(table)
            if fact is None:
                continue
            measured = ""
            for name, value in fact.key_multiplicity.items():
                if name.lower() == column:
                    measured = value
                    break
            match = _MULTIPLICITY.search(measured)
            if not match:
                continue
            distinct = int(match.group(1).replace(",", ""))
            total = int(match.group(2).replace(",", ""))
            if distinct <= 0 or distinct >= total:
                continue
            concerns.append(
                f"The query joins on {fact.name}.{column}, which was MEASURED to hold only "
                f"{distinct:,} distinct values across {total:,} rows, and nothing in the query "
                f"collapses the result. Every matching row on the other side is repeated."
            )
    return concerns


def _foreign_key_concerns(
    sql_low: str, snapshot: SchemaSnapshot, tables: Sequence[str]
) -> List[str]:
    concerns: List[str] = []
    for table in tables:
        fact = snapshot.get(table)
        if fact is None:
            continue
        for relation in fact.foreign_keys:
            try:
                column, _, target = relation.partition(" -> ")
                target_table, _, target_column = target.rpartition(".")
            except ValueError:  # pragma: no cover - defensive
                continue
            column = column.strip().lower()
            target_table = target_table.strip().lower()
            target_column = target_column.strip().lower()
            if not column or not target_table or not target_column:
                continue
            if f"{column} =" not in sql_low and f"{column}=" not in sql_low:
                continue
            if target_table not in sql_low:
                continue
            if target_column not in sql_low:
                concerns.append(
                    f"{fact.name}.{column} is DECLARED to reference "
                    f"{target_table}.{target_column}, but that column does not appear in the "
                    f"query -- the join may be lining up against something else."
                )
    return concerns


def _scale_concerns(
    sql_low: str, snapshot: SchemaSnapshot, aliases: Dict[str, Set[str]], ambiguous: Set[str]
) -> List[str]:
    concerns: List[str] = []
    comparison = re.compile(
        rf"({sqltext.IDENT})\s*\.\s*({sqltext.IDENT})\s*(>=|<=|<>|!=|=|>|<)\s*([0-9]+(?:\.[0-9]+)?)"
    )
    for prefix, column, operator, literal in comparison.findall(sqltext.clean(sql_low)):
        table = _resolve(aliases, sqltext.bare(prefix).lower(), ambiguous)
        if not table:
            continue
        fact = snapshot.get(table)
        if fact is None:
            continue
        measured = ""
        for name, text in fact.numeric_profile.items():
            if name.lower() == sqltext.bare(column).lower():
                measured = text
                break
        if not measured:
            continue
        try:
            value = float(literal)
        except ValueError:  # pragma: no cover - the regex guarantees a number
            continue
        if "FRACTION_1" in measured and value > 1:
            concerns.append(
                f"{fact.name}.{sqltext.bare(column)} was MEASURED to hold 0-1 values, but the "
                f"query compares it {operator} {literal}. On a 0-1 column that condition is "
                f"either always or never true, so the query returns a silent, confident "
                f"result about nothing."
            )
        elif "PERCENT_100" in measured and 0 < value < 1:
            concerns.append(
                f"{fact.name}.{sqltext.bare(column)} was MEASURED to hold 0-100 values, but the "
                f"query compares it {operator} {literal}, which is a fraction."
            )
    return concerns


def _type_concerns(
    sql: str, snapshot: SchemaSnapshot, aliases: Dict[str, Set[str]], ambiguous: Set[str]
) -> List[str]:
    concerns: List[str] = []
    sql_low = sqltext.clean(sql).lower()
    for left_alias, left_col, right_alias, right_col in _equality_joins(sql):
        left_table = _resolve(aliases, left_alias, ambiguous)
        right_table = _resolve(aliases, right_alias, ambiguous)
        if not left_table or not right_table:
            continue
        left = snapshot.column(left_table, left_col)
        right = snapshot.column(right_table, right_col)
        if left is None or right is None:
            continue
        left_family, right_family = _family(left.data_type), _family(right.data_type)
        if left_family == right_family:
            continue
        if _is_converted(sql_low, left_alias, left_col) or _is_converted(
            sql_low, right_alias, right_col
        ):
            continue
        concerns.append(
            f"{left_table}.{left.name} is declared {left.type_text()} and "
            f"{right_table}.{right.name} is declared {right.type_text()}, and the query "
            f"compares them directly with no conversion. SQL Server converts implicitly and "
            f"RAISES on the first value that will not convert -- which takes the whole report "
            f"down rather than excluding one row."
        )
    return concerns


def _empty_table_concerns(snapshot: SchemaSnapshot, tables: Sequence[str]) -> List[str]:
    concerns: List[str] = []
    for table in tables:
        fact = snapshot.get(table)
        if fact is not None and fact.row_count == 0:
            concerns.append(
                f"{fact.name} currently holds 0 rows. A query scoped on an empty table does "
                f"not fail -- it reports an empty result, which reads downstream as a clean "
                f"answer for something nobody is actually measuring."
            )
    return concerns


def check(
    sql: str,
    snapshot: SchemaSnapshot,
    capability: Optional[Capability] = None,
) -> List[str]:
    """Concerns about one query. An empty list means these traps were avoided.

    A clean result is NOT a guarantee of correctness -- it means these specific,
    decidable mistakes are not present.
    """
    if not sql or snapshot is None or not snapshot.tables:
        return []
    try:
        tables = sqltext.table_references(sql)
        aliases = sqltext.alias_map(sql)
        ambiguous = sqltext.ambiguous_aliases(sql)
        sql_low = " ".join(sqltext.clean(sql).split()).lower()

        collapsing = any(token in sql_low for token in _COLLAPSING)

        concerns: List[str] = []
        concerns += _grain_concerns(sql_low, snapshot, tables)
        concerns += _join_multiplicity_concerns(sql, snapshot, aliases, ambiguous, collapsing)
        concerns += _partition_key_concerns(sql, snapshot, aliases, ambiguous)
        concerns += _foreign_key_concerns(sql_low, snapshot, tables)
        concerns += _scale_concerns(sql_low, snapshot, aliases, ambiguous)
        concerns += _type_concerns(sql, snapshot, aliases, ambiguous)
        concerns += _empty_table_concerns(snapshot, tables)

        if (
            capability is not None
            and capability.requires_grain_resolution
            and not sqltext.has_grain_resolution(sql)
        ):
            concerns.append(
                "This capability reports at a coarser grain than the data it reads, but the "
                "query contains no ranking, grouping or distinct count at all."
            )

        # Order-preserving de-duplication: the same concern reached from two
        # directions is one concern, and repeating it inflates its weight.
        return list(dict.fromkeys(concerns))
    except Exception as exc:  # noqa: BLE001 - an advisory signal must never break a compile
        logger.warning("dynamic: schema check skipped (%s)", exc)
        return []
