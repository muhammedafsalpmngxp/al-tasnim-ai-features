"""The hard gate. Deterministic, model-free, and run BEFORE the database is touched.

NO MODEL IS INVOLVED HERE, AND THAT IS THE POINT. This is the boundary that
guarantees this application can never modify the source database, whatever
privileges the configured login happens to hold and whatever a model was
persuaded to write. A guarantee that depends on a model behaving is not a
guarantee, and a model asserting "this SQL is safe" is not a safety mechanism.

Three separate things are checked, all decidable from the text plus the live
schema, all cheap:

  1. SAFETY    one read-only statement that reaches nothing outside the
               approved objects.
  2. SCHEMA    every object it names exists, right now, inside the allowlist.
  3. CONTRACT  the parameters, output aliases and grain the application needs.

All three run before any database round trip, so a defect costs microseconds
rather than a connection, a scan and a wasted reasoning-model call. All three
feed the same bounded rewrite budget: each is a MECHANICAL fault with an
obvious fix, unlike a semantic rejection from the verifier, and the exact
deterministic message is what goes back to the author.
"""

from __future__ import annotations

import re
from typing import List, Optional, Sequence

from dynamic_db import dependencies as deps
from dynamic_db import sqltext
from dynamic_db.capabilities import Capability
from dynamic_db.introspect import SchemaSnapshot

# Whole-word keywords that must never appear in a statement this engine runs.
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|EXEC|EXECUTE|"
    r"GRANT|REVOKE|BACKUP|RESTORE|SHUTDOWN|RECONFIGURE|DBCC|INTO|USE)\b",
    re.IGNORECASE,
)
_PROCEDURE = re.compile(r"\b(sp_|xp_)\w+", re.IGNORECASE)

# Read-side dangers. Each is legal inside a query -- so the rules above let them
# through -- but each reaches OUTSIDE the approved objects: the server's
# filesystem, a linked server, or the login catalogue. They cannot modify data;
# they leak it, which for a tool whose output is shared is just as serious.
_UNSAFE = re.compile(
    r"\b(OPENROWSET|OPENQUERY|OPENDATASOURCE|OPENXML|BULK|WAITFOR)\b"
    r"|\bfn_(get_audit_file|trace_gettable)\b"
    r"|\bsys\.(sql_logins|server_principals|credentials|master_key_passwords)\b",
    re.IGNORECASE,
)


def safety_problems(sql: str) -> List[str]:
    """Everything that makes this statement unsafe to run at all."""
    problems: List[str] = []
    if not sql or not sql.strip():
        return ["The query is empty."]

    body = sqltext.clean(sql).strip().rstrip(";").strip()
    if ";" in body:
        problems.append(
            "Multiple statements are not allowed - write a single query. "
            "A second statement is never executed by this engine."
        )
    lowered = body.lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        problems.append(
            "A query must begin with SELECT, or with WITH for a CTE chain."
        )
    match = _FORBIDDEN.search(body)
    if match:
        problems.append(
            f"Forbidden keyword {match.group(0)!r}. This engine is strictly read-only, so a "
            f"capability may only read."
        )
    if _PROCEDURE.search(body):
        problems.append("Stored-procedure calls (sp_ / xp_) are not allowed.")
    match = _UNSAFE.search(body)
    if match:
        problems.append(
            f"{match.group(0)!r} is not allowed - a query may read the approved objects only, "
            f"never remote servers, files on disk, or login and credential data."
        )
    if sqltext.looks_like_credentials(sql):
        problems.append(
            "The query text contains what looks like connection or credential settings. "
            "Credentials never belong in a query."
        )
    return problems


def schema_problems(
    sql: str, snapshot: SchemaSnapshot, approved: Optional[Sequence[str]] = None
) -> List[str]:
    """Everything the statement names that the CURRENT database does not have."""
    problems: List[str] = []
    allowed = list(approved) if approved is not None else snapshot.approved_names()

    unknown = deps.unknown_tables(sql, allowed)
    if unknown:
        problems.append(
            "References "
            + ", ".join(unknown)
            + ", which is not an approved object in the SCHEMA block. Use only the exact "
            "schema-qualified names listed there."
        )

    footprint = deps.extract(sql, snapshot)
    if footprint.missing_tables:
        problems.append(
            "References "
            + ", ".join(footprint.missing_tables)
            + ", which does not exist in the current database."
        )
    if footprint.missing_columns:
        problems.append(
            "References "
            + ", ".join(f"{table}.{column}" for table, column in footprint.missing_columns)
            + ", which the SCHEMA block does not list under that table. A generic-sounding "
            "name is not evidence that a column is present."
        )
    return problems


def contract_problems(sql: str, capability: Capability) -> List[str]:
    """Everything the application itself requires of this capability's SQL."""
    problems: List[str] = []

    leftover = sqltext.placeholders(sql)
    if leftover:
        problems.append(
            "Unresolved template token(s) "
            + ", ".join(leftover)
            + " remain. The query is stored ready to run, so nothing may be left to fill in."
        )

    markers = sqltext.parameter_markers(sql)
    expected = capability.parameter_count
    if markers != expected:
        names = ", ".join(p.name for p in capability.parameters) or "(none)"
        if expected and markers == 0:
            problems.append(
                f"The query binds no parameter at all. {capability.id} is parameterised on "
                f"{names} and must carry exactly {expected} ? marker(s), in that order. "
                f"A stored query with the value written into its text is wrong the moment "
                f"that value changes."
            )
        else:
            problems.append(
                f"The query carries {markers} ? marker(s) but {capability.id} binds exactly "
                f"{expected}: {names}. Bind a value once into a CTE and reference it if it "
                f"is needed more than once."
            )

    if capability.requires_report_date:
        dates = sqltext.date_literals(sql)
        if dates:
            problems.append(
                "The query contains the literal date(s) "
                + ", ".join(sorted(set(dates))[:3])
                + ". The report date is a bound parameter; a date written into the text pins "
                "this capability to a day that has already passed."
            )

    aliases = sqltext.output_aliases(sql)
    missing = [
        column for column in capability.required_columns if column.lower() not in aliases
    ]
    if missing:
        problems.append(
            "The result set does not carry the required output alias(es) "
            + ", ".join(missing)
            + ". The application reads these names, so each must appear exactly, normally "
            "through an explicit AS."
        )

    if capability.requires_grain_resolution and not sqltext.has_grain_resolution(sql):
        problems.append(
            "This capability reports at a coarser grain than the data it reads, but the query "
            "contains no ranking, grouping or distinct count. Repeated records would each be "
            "counted separately."
        )
    return problems


def validate(
    sql: str,
    capability: Capability,
    snapshot: SchemaSnapshot,
    approved: Optional[Sequence[str]] = None,
) -> List[str]:
    """Every deterministic problem with this statement, most fundamental first.

    Safety first and short-circuiting: there is no value in telling the author
    which output alias its DROP statement is missing.
    """
    problems = safety_problems(sql)
    if problems:
        return problems
    problems += schema_problems(sql, snapshot, approved)
    problems += contract_problems(sql, capability)
    return problems


def execution_problems(
    capability: Capability, row_count: int, columns: Sequence[str]
) -> List[str]:
    """Contract checks that only a real execution can settle.

    Kept beside the static ones rather than inside the executor: they are the
    same contract, and splitting a contract across two files is how the two
    halves drift apart.
    """
    problems: List[str] = []
    present = {str(column).lower() for column in columns}
    missing = [
        column for column in capability.required_columns if column.lower() not in present
    ]
    if missing:
        problems.append(
            "The executed result set does not actually contain "
            + ", ".join(missing)
            + ", whatever the query text appeared to alias. Name each one with an explicit AS "
            "in the final SELECT."
        )
    if capability.single_row and row_count != 1:
        problems.append(
            f"{capability.id} must return EXACTLY ONE row, always - including when nothing was "
            f"recorded - and this returned {row_count}. Aggregate over the scope rather than "
            f"filtering to it."
        )
    return problems
