"""Deterministic text analysis of one SQL statement. No model, no database.

Everything the validator, the dependency extractor and the schema checker need
to know about a query is decidable from its text, and all three read it through
this module so they can never disagree about what the query says.

ONE SCAN, NOT TWO REGEX PASSES, for blanking comments and string literals.
Handling them separately is exploitable. Strip comments first and a ``--``
inside a string breaks the literal; blank strings first and a lone quote inside
a comment can swallow a real statement, so that::

    SELECT a -- '
    <a second, destructive statement> -- '

would hide that second statement from every check here while SQL Server still
executes it.

BRACKETED IDENTIFIERS ARE PRESERVED, not blanked. This database has a column
literally named "PDO Well ID"; blanking it would make every reference to it
invisible to dependency tracking, which is the one place invisibility is
dangerous -- a column nobody records a dependency on is a column whose removal
nobody notices.
"""

from __future__ import annotations

import re
from typing import Dict, List, Sequence, Set, Tuple

#: An identifier is either bracketed (any characters, including spaces) or a
#: bare word. Both forms appear in real SQL against this database.
IDENT = r"(?:\[[^\]]+\]|\w+)"

_QUALIFIED = re.compile(rf"({IDENT})\s*\.\s*({IDENT})")
_TABLE_REF = re.compile(
    rf"\b(?:FROM|JOIN)\s+({IDENT})\s*\.\s*({IDENT})"
    rf"(?:\s+(?:AS\s+)?({IDENT}))?",
    re.IGNORECASE,
)
_PLACEHOLDER = re.compile(r"\{\{[^}]*\}\}")

#: Words that can follow a table reference and are NOT an alias. Without this,
#: "FROM a.b WHERE x" records "WHERE" as an alias and then resolves "where.col".
_NOT_AN_ALIAS = frozenset(
    """
WHERE ON INNER LEFT RIGHT FULL OUTER JOIN CROSS APPLY GROUP ORDER HAVING UNION
EXCEPT INTERSECT WITH SELECT AND OR AS FOR OPTION PIVOT UNPIVOT
""".split()
)

#: A string literal that looks like a date. A report date belongs in a bound
#: parameter; one written into the text pins the query to a day that has
#: already passed and is invisible to every other check here.
_DATE_LITERAL = re.compile(
    r"^\s*\d{4}[-/]\d{1,2}[-/]\d{1,2}([ T]\d{1,2}:\d{2}(:\d{2})?(\.\d+)?)?\s*$"
)

#: An ODBC/ADO connection string fragment. A credential must never be able to
#: reach a query, however it got there.
_CREDENTIALISH = re.compile(
    r"\b(PWD\s*=|PASSWORD\s*=|UID\s*=|USER\s+ID\s*=|Trusted_Connection\s*=|"
    r"Data\s+Source\s*=|Initial\s+Catalog\s*=)",
    re.IGNORECASE,
)


def bare(name: str) -> str:
    """``[PDO Well ID]`` -> ``PDO Well ID``; anything else unchanged."""
    name = name.strip()
    if name.startswith("[") and name.endswith("]"):
        return name[1:-1]
    return name


def clean(sql: str) -> str:
    """The statement with comments and string literals blanked out.

    Only this inspection copy is altered; what actually runs is always the
    original text. A keyword inside DATA is not a command -- a predicate
    matching the text 'update required' must not be rejected as an UPDATE.
    """
    out: List[str] = []
    i, n = 0, len(sql or "")
    while i < n:
        if sql.startswith("--", i):
            newline = sql.find("\n", i)
            i = n if newline == -1 else newline
            out.append(" ")
            continue
        if sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end == -1 else end + 2
            out.append(" ")
            continue
        char = sql[i]
        if char == "'":
            i += 1
            while i < n:
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            out.append("''")
            continue
        out.append(char)
        i += 1
    return "".join(out)


def strip_comments(sql: str) -> str:
    """Comments removed, string literals KEPT.

    Placeholder scanning needs exactly this. Scanning the raw text finds the
    ``{{include:...}}`` a file's own header comment mentions while explaining
    how includes work, and rejects a perfectly good query for it. Scanning the
    fully cleaned text has the opposite fault: a placeholder written inside a
    string literal is a real unsubstituted placeholder, and blanking literals
    would hide it.
    """
    out: List[str] = []
    i, n = 0, len(sql or "")
    while i < n:
        if sql.startswith("--", i):
            newline = sql.find("\n", i)
            i = n if newline == -1 else newline
            out.append(" ")
            continue
        if sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end == -1 else end + 2
            out.append(" ")
            continue
        if sql[i] == "'":
            out.append(sql[i])
            i += 1
            while i < n:
                out.append(sql[i])
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        out.append(sql[i + 1])
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        out.append(sql[i])
        i += 1
    return "".join(out)


def string_literals(sql: str) -> List[str]:
    """The contents of every string literal, with comments ignored."""
    literals: List[str] = []
    i, n = 0, len(sql or "")
    while i < n:
        if sql.startswith("--", i):
            newline = sql.find("\n", i)
            i = n if newline == -1 else newline
            continue
        if sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        if sql[i] == "'":
            i += 1
            buffer: List[str] = []
            while i < n:
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        buffer.append("'")
                        i += 2
                        continue
                    i += 1
                    break
                buffer.append(sql[i])
                i += 1
            literals.append("".join(buffer))
            continue
        i += 1
    return literals


def date_literals(sql: str) -> List[str]:
    """String literals that are written as a date."""
    return [text for text in string_literals(sql) if _DATE_LITERAL.match(text)]


def looks_like_credentials(sql: str) -> bool:
    return bool(_CREDENTIALISH.search(clean(sql)))


def placeholders(sql: str) -> List[str]:
    """Unresolved ``{{...}}`` template tokens left in the statement.

    Comments are removed first: a file that DOCUMENTS the include directive in
    its own header is not a file with an unresolved placeholder in it.
    """
    return sorted(set(_PLACEHOLDER.findall(strip_comments(sql))))


def parameter_markers(sql: str) -> int:
    """How many ``?`` markers the statement binds.

    Counted on the cleaned copy, so a question mark inside a comment or inside
    a string literal is not mistaken for a parameter.
    """
    return clean(sql).count("?")


def statement_count(sql: str) -> int:
    """How many statements the text holds, ignoring one trailing semicolon."""
    body = clean(sql).strip().rstrip(";").strip()
    return 1 + body.count(";") if body else 0


def table_references(sql: str) -> List[str]:
    """Schema-qualified tables the statement reads, lower-cased.

    The dot is required on purpose. A bare word after FROM or JOIN is
    effectively always a CTE name or a subquery alias, so requiring the
    qualification flags a genuine physical reference and never a CTE.
    """
    found: List[str] = []
    for schema, table, _alias in _TABLE_REF.findall(clean(sql)):
        name = f"{bare(schema)}.{bare(table)}".lower()
        if name not in found:
            found.append(name)
    return found


def alias_map(sql: str) -> Dict[str, Set[str]]:
    """alias (lower-cased) -> the physical tables it can stand for.

    A set rather than a single table because the same alias may legitimately
    name different tables in two different subqueries of one statement. Where
    that happens, a column reference through it is attributed to all of them --
    conservative in the only direction that is safe: over-recording a
    dependency costs one extra recompile, under-recording it means a dropped
    column nobody notices.
    """
    mapping: Dict[str, Set[str]] = {}
    for schema, table, alias in _TABLE_REF.findall(clean(sql)):
        name = f"{bare(schema)}.{bare(table)}".lower()
        # The table's own last segment works as a prefix too, since SQL allows
        # qualifying by table name when no alias was given.
        mapping.setdefault(bare(table).lower(), set()).add(name)
        if not alias:
            continue
        alias_name = bare(alias)
        if alias_name.upper() in _NOT_AN_ALIAS:
            continue
        mapping.setdefault(alias_name.lower(), set()).add(name)
    return mapping


_BARE_SOURCE = re.compile(
    rf"\b(?:FROM|JOIN)\s+(\w+)(?!\s*\.)\s+(?:AS\s+)?({IDENT})\b", re.IGNORECASE
)


def ambiguous_aliases(sql: str) -> Set[str]:
    """Aliases that name something OTHER than a physical table somewhere here.

    A real, observed failure this prevents: one statement uses ``rc`` for a
    reference table inside one CTE and for a completely different CTE further
    down. Resolving every ``rc.<something>`` through the physical table then
    reports a long list of columns as missing -- none of which the query ever
    claimed were there -- and the statement is rejected for a fault that does
    not exist.

    Only REPORTING is suppressed for these, never attribution: a column that
    genuinely belongs to the physical table is still recorded as a dependency,
    because under-recording a dependency is the one failure with no visible
    symptom until a column disappears in production.
    """
    found: Set[str] = set()
    for _source, alias in _BARE_SOURCE.findall(clean(sql)):
        name = bare(alias)
        if name.upper() in _NOT_AN_ALIAS:
            continue
        found.add(name.lower())
    return found


def qualified_column_references(sql: str) -> List[Tuple[str, str]]:
    """Every ``prefix.name`` pair in the statement, lower-cased, order kept.

    Includes ``schema.table`` pairs; the caller filters those out using the
    alias map, which is the only place that knows which prefixes are tables.
    """
    found: List[Tuple[str, str]] = []
    seen: Set[Tuple[str, str]] = set()
    for left, right in _QUALIFIED.findall(clean(sql)):
        pair = (bare(left).lower(), bare(right).lower())
        if pair not in seen:
            seen.add(pair)
            found.append(pair)
    return found


def identifiers(sql: str) -> Set[str]:
    """Every bare word and bracketed identifier in the statement, lower-cased.

    Used to attribute an UNQUALIFIED column name to the tables that could have
    supplied it. Deliberately includes keywords: the caller intersects this
    with a known column list, and no keyword survives that.
    """
    body = clean(sql)
    words = {word.lower() for word in re.findall(r"\w+", body)}
    words |= {bare(token).lower() for token in re.findall(r"\[[^\]]+\]", body)}
    return words


def output_aliases(sql: str) -> Set[str]:
    """The column names the statement's result set will carry, best effort.

    Two shapes are recognised, and both are the ones this project's SQL uses:
    an explicit ``AS <name>``, and a bare trailing ``<something> <name>,``.
    Anything cleverer would be guesswork; a capability whose contract cannot be
    confirmed from the text is reported as unconfirmed rather than assumed to
    be satisfied.
    """
    body = clean(sql)
    aliases: Set[str] = set()
    for match in re.finditer(rf"\bAS\s+({IDENT})", body, re.IGNORECASE):
        aliases.add(bare(match.group(1)).lower())
    # `SELECT a.col,` and `SELECT a.col` at the end of a select list: the
    # result column takes the column's own name.
    for left, right in _QUALIFIED.findall(body):
        aliases.add(bare(right).lower())
    for match in re.finditer(rf"(?:SELECT|,)\s+({IDENT})\s*(?:,|$)", body, re.IGNORECASE):
        aliases.add(bare(match.group(1)).lower())
    return aliases


def has_grain_resolution(sql: str) -> bool:
    """Whether the statement collapses repeated rows to one per logical thing.

    Any of the recognised shapes is enough: a ranked window function, an
    explicit grouping, or a distinct count. This is a presence check, not proof
    of correctness -- whether the right key was partitioned on is a semantic
    question and belongs to the verifier.
    """
    body = " ".join(clean(sql).split()).upper()
    return any(
        token in body
        for token in (
            "ROW_NUMBER()",
            "RANK()",
            "DENSE_RANK()",
            "GROUP BY",
            "COUNT(DISTINCT",
            "SELECT DISTINCT",
        )
    )


def normalise(sql: str) -> str:
    """Whitespace-collapsed text, for hashing and for stable comparison."""
    return " ".join((sql or "").split())


def references_any(sql: str, names: Sequence[str]) -> bool:
    body = " ".join(clean(sql).split()).lower()
    return any(name.lower() in body for name in names)
