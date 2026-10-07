"""Offline fixtures for the schema-adaptive layer.

THE NAMES HERE ARE DELIBERATELY NOT THIS DATABASE'S NAMES. A test that uses the
real table names would pass just as happily if the code under test had those
names baked into it -- which is the one property these tests exist to disprove.
So the fixture schema is a small, invented one, and every test that matters is
run against it.

A test fixture is one of the four places physical names are allowed to live.
"""

from __future__ import annotations

from typing import Dict, Iterable, Optional, Sequence, Tuple

from dynamic_db.capabilities import Capability, Parameter
from dynamic_db.capabilities import PARAM_REPORT_DATE, PARAM_INT
from dynamic_db.introspect import ColumnFact, SchemaSnapshot, TableFact

FACT_TABLE = "ops.job_entry"
PARENT_TABLE = "ops.site"
LOOKUP_TABLE = "ref.unit"


def column(
    name: str,
    data_type: str = "int",
    *,
    length: Optional[int] = None,
    nullable: bool = True,
    ordinal: int = 0,
) -> ColumnFact:
    return ColumnFact(
        name=name, data_type=data_type, max_length=length, nullable=nullable, ordinal=ordinal
    )


def table(
    name: str,
    columns: Sequence[ColumnFact],
    *,
    primary_key: Sequence[str] = (),
    foreign_keys: Sequence[str] = (),
    constraints: Sequence[str] = (),
    row_count: Optional[int] = 1000,
    repeating_key: Optional[str] = None,
    key_multiplicity: Optional[Dict[str, str]] = None,
    numeric_profile: Optional[Dict[str, str]] = None,
    approved: bool = True,
) -> TableFact:
    fact = TableFact(
        name=name,
        primary_key=list(primary_key),
        foreign_keys=list(foreign_keys),
        constraints=list(constraints),
        row_count=row_count,
        repeating_key=repeating_key,
        key_multiplicity=dict(key_multiplicity or {}),
        approved=approved,
        numeric_profile=dict(numeric_profile or {}),
    )
    for index, col in enumerate(columns, start=1):
        fact.columns[col.name] = ColumnFact(
            name=col.name,
            data_type=col.data_type,
            max_length=col.max_length,
            nullable=col.nullable,
            ordinal=index,
        )
    return fact


def snapshot(**overrides) -> SchemaSnapshot:
    """A small invented schema shaped like the one this report really reads.

    One fact table held at a finer grain than it is reported at, one parent it
    is filtered by, and one lookup -- which is enough structure for every grain,
    join, scale and type check to have something real to bite on.
    """
    fact = table(
        FACT_TABLE,
        [
            column("row_id", "bigint", nullable=False),
            column("run_on", "date"),
            column("job_code", "nvarchar", length=100),
            column("sched_ref", "int"),
            column("site_ref", "varchar", length=10),  # deliberately not an int
            column("planned_qty", "decimal"),
            column("payload", "nvarchar", length=-1),
            column("done_flag", "bit"),
            column("started_on", "date"),
            column("ended_on", "date"),
            column("unit_ref", "int"),
            column("touched_at", "datetime2"),
            column("share_done", "decimal"),
        ],
        primary_key=["row_id"],
        foreign_keys=[f"unit_ref -> {LOOKUP_TABLE}.unit_id"],
        row_count=110_000,
        repeating_key="job_code",
        key_multiplicity={
            "job_code": "35,000 distinct of 110,000",
            "row_id": "110,000 distinct of 110,000",
            "site_ref": "800 distinct of 110,000",
        },
        numeric_profile={"share_done": "0..1 | nulls 2% | FRACTION_1 (0-1) - multiply by 100"},
    )
    parent = table(
        PARENT_TABLE,
        [
            column("site_id", "int", nullable=False),
            column("closed_on", "date"),
            column("opened_on", "date"),
        ],
        primary_key=["site_id"],
        row_count=800,
        key_multiplicity={"site_id": "800 distinct of 800"},
    )
    lookup = table(
        LOOKUP_TABLE,
        [column("unit_id", "int", nullable=False), column("unit_label", "varchar", length=20)],
        primary_key=["unit_id"],
        row_count=20,
    )
    snap = SchemaSnapshot(
        database="testhost:1433/TestDb",
        included_tables=[FACT_TABLE, PARENT_TABLE, LOOKUP_TABLE],
        discovery_schemas=["ops", "ref"],
        profiled=True,
    )
    for item in (fact, parent, lookup):
        snap.tables[item.key()] = item
    for key, value in overrides.items():
        setattr(snap, key, value)
    return snap


_STRING_TYPES = ("char", "varchar", "nchar", "nvarchar")


def with_column_type(
    snap: SchemaSnapshot, table_name: str, column_name: str, data_type: str, length=...
):
    """A copy of ``snap`` with one column retyped -- a real, breaking change.

    The declared length follows the new type unless one is given. A varchar
    that becomes an int has no length, and carrying the old one over would make
    the fixture describe a shape SQL Server cannot produce.
    """
    clone = SchemaSnapshot.from_dict(snap.to_dict())
    fact = clone.get(table_name)
    old = fact.columns[column_name]
    if length is ...:
        length = old.max_length if data_type.lower() in _STRING_TYPES else None
    fact.columns[column_name] = ColumnFact(
        name=old.name,
        data_type=data_type,
        max_length=length,
        nullable=old.nullable,
        ordinal=old.ordinal,
    )
    return clone


def without_column(snap: SchemaSnapshot, table_name: str, column_name: str):
    clone = SchemaSnapshot.from_dict(snap.to_dict())
    clone.get(table_name).columns.pop(column_name)
    return clone


def with_extra_column(snap: SchemaSnapshot, table_name: str, column_name: str):
    clone = SchemaSnapshot.from_dict(snap.to_dict())
    fact = clone.get(table_name)
    fact.columns[column_name] = ColumnFact(
        name=column_name, data_type="int", max_length=None, nullable=True, ordinal=99
    )
    return clone


def with_row_counts(snap: SchemaSnapshot, counts: Dict[str, int]):
    """A copy with different row counts and nothing else changed."""
    clone = SchemaSnapshot.from_dict(snap.to_dict())
    for name, value in counts.items():
        clone.get(name).row_count = value
    return clone


def without_table(snap: SchemaSnapshot, table_name: str):
    clone = SchemaSnapshot.from_dict(snap.to_dict())
    clone.tables.pop(table_name.lower())
    clone.included_tables = [t for t in clone.included_tables if t.lower() != table_name.lower()]
    return clone


# --------------------------------------------------------------------------
# A capability to compile, with a contract small enough to read
# --------------------------------------------------------------------------

TEST_CAPABILITY_ID = "TEST_DAILY_ROLLUP"


def _test_rules() -> str:
    """A stand-in for whatever a real caller's manifest would slice out of
    its own rule documents -- this engine never cares how many documents
    that came from, only that it gets one string back."""
    return (
        "A site is open when its closed_on date is not recorded. "
        "A logical job is one row per (job_code, sched_ref); repeated rows "
        "for the same job must be resolved to the most recently touched one."
    )


def test_capability(**overrides) -> Capability:
    defaults = dict(
        id=TEST_CAPABILITY_ID,
        intent="How much work each open site recorded on the report date.",
        concepts=("open site", "daily job source", "report date filtering", "logical job grain"),
        required_columns=("site_key", "job_total"),
        parameters=(
            Parameter("report_date", PARAM_REPORT_DATE, "the date being reported"),
        ),
        baseline_sql=GOOD_SQL,
        rules=_test_rules,
        single_row=False,
        requires_grain_resolution=True,
    )
    defaults.update(overrides)
    return Capability(**defaults)  # type: ignore[arg-type]


GOOD_SQL = f"""
WITH open_sites AS (
    SELECT s.site_id
    FROM {PARENT_TABLE} AS s
    WHERE s.closed_on IS NULL
),
ranked AS (
    SELECT j.job_code,
           TRY_CONVERT(int, j.site_ref) AS site_key,
           j.planned_qty,
           ROW_NUMBER() OVER (
               PARTITION BY TRY_CONVERT(int, j.site_ref), j.sched_ref, j.job_code
               ORDER BY j.touched_at DESC, j.row_id DESC
           ) AS grain_rank
    FROM {FACT_TABLE} AS j
    INNER JOIN open_sites AS o ON o.site_id = TRY_CONVERT(int, j.site_ref)
    WHERE j.run_on = ?
)
SELECT r.site_key                       AS site_key,
       SUM(r.planned_qty)               AS job_total
FROM ranked AS r
WHERE r.grain_rank = 1
GROUP BY r.site_key;
""".strip()


#: A query that reads ONLY the fact table. Used where the parent it normally
#: joins has been removed from the schema, so the author can still produce
#: something the validator accepts while the reviewer decides whether the
#: capability can be expressed at all without it.
SQL_FACT_ONLY = f"""
WITH ranked AS (
    SELECT TRY_CONVERT(int, j.site_ref) AS site_key,
           j.planned_qty,
           ROW_NUMBER() OVER (
               PARTITION BY j.job_code, j.sched_ref
               ORDER BY j.touched_at DESC, j.row_id DESC
           ) AS grain_rank
    FROM {FACT_TABLE} AS j
    WHERE j.run_on = ?
)
SELECT r.site_key           AS site_key,
       SUM(r.planned_qty)   AS job_total
FROM ranked AS r
WHERE r.grain_rank = 1
GROUP BY r.site_key;
""".strip()
