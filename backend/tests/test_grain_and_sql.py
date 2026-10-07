"""The daily row-grain strategy, the live-well rule and the read-only guarantee.

The grain tests assert the behaviour the SQL must produce and then verify the
SQL text itself contains the ranking that produces it, so a later edit to
daily_tasks.sql that reintroduces double counting fails here.
"""

from __future__ import annotations

import re
from decimal import Decimal

import pytest

from app.config.database import ReadOnlyViolation, assert_read_only
from app.models.daily import DailyDataset, DataQualityFlag, DayCounters
from app.services.grouping_service import GroupingService
from app.services.validation_service import build_task
from app.utils.sql_loader import load_sql
from tests.conftest import REPORT_DATE, make_row

_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)


def _code_only(sql: str) -> str:
    """Strip /* ... */ comments so a test checks executable SQL, not prose
    that happens to mention the very identifier the test is guarding against."""
    return _COMMENT_BLOCK.sub(" ", sql)


class TestDuplicateRows:
    def test_multiple_planning_snapshots_are_flagged_on_the_kept_row(self):
        # daily_tasks.sql keeps one row per logical task and carries the count
        # of rows it stood in for, so the condition stays visible.
        task = build_task(make_row(group_row_count=2, group_actual_entry_count=1))
        assert DataQualityFlag.MULTIPLE_TASK_ROWS in task.data_quality_flags

    def test_duplicate_actual_entries_are_flagged_distinctly(self):
        task = build_task(make_row(group_row_count=2, group_actual_entry_count=2))
        assert DataQualityFlag.DUPLICATE_ACTUAL_ENTRY in task.data_quality_flags
        assert DataQualityFlag.MULTIPLE_TASK_ROWS not in task.data_quality_flags

    def test_duplicate_rows_do_not_double_count_quantities(self):
        # Three raw rows resolve to one logical task: the planned quantity is
        # counted once, not three times.
        task = build_task(
            make_row(planned=100, actual_quantity=100, group_row_count=3,
                     group_actual_entry_count=1)
        )
        groups = GroupingService().build(_dataset([task]))
        assert groups[0].planned_quantity == Decimal(100)
        assert groups[0].actual_quantity == Decimal(100)
        assert groups[0].task_count == 1
        assert groups[0].well_count == 1

    def test_well_count_and_task_count_are_different_measures(self):
        # One well running three tasks in a group is one well and three tasks.
        tasks = [
            build_task(make_row(task_daily_id=i, well_id=31474, task_code=f"FLME1180-3147{i}"))
            for i in range(1, 4)
        ]
        groups = GroupingService().build(_dataset(tasks))
        assert groups[0].well_count == 1
        assert groups[0].task_count == 3


class TestBaseSqlContract:
    """daily_tasks.sql must keep the guarantees the Python layer relies on."""

    @pytest.fixture(scope="class")
    def sql(self) -> str:
        return load_sql("daily_detail")

    def test_live_wells_use_eng_completion_date_only(self, sql):
        assert "eng_completion_date IS NULL" in sql
        for forbidden in ("status_id", "flowline_const_status_id"):
            assert not re.search(
                rf"WHERE[^;]*{forbidden}", sql, re.IGNORECASE | re.DOTALL
            ), f"{forbidden} must not define a live well"

    def test_invalid_well_ids_are_excluded(self, sql):
        assert "TRY_CONVERT(int, td.well_id) > 1" in sql

    def test_well_id_is_never_compared_or_joined_without_try_convert(self, sql):
        # well.task_daily.well_id is varchar and can hold non-numeric junk
        # (observed: '0000F', '0000I', '0000J'). A bare comparison or JOIN
        # against the int well_master.well_id throws SQLSTATE 22018 and takes
        # the whole report down; TRY_CONVERT returns NULL instead, which the
        # INNER JOIN then excludes safely.
        for line in sql.splitlines():
            if "td.well_id" not in line:
                continue
            # Every mention of td.well_id must be inside a TRY_CONVERT(...) call
            # or be the well_id -> AS well_id projection / a comment.
            stripped = line.strip()
            if stripped.startswith(("/*", "*", "--")):
                continue
            assert "TRY_CONVERT(int, td.well_id)" in line or "AS well_id" in line, (
                f"td.well_id used outside TRY_CONVERT: {line!r}"
            )

    def test_report_date_is_parameterised(self, sql):
        assert "td.ActionOn = ?" in sql
        # No literal date may appear anywhere in the query.
        assert not re.search(r"'\d{4}-\d{2}-\d{2}'", sql)

    def test_actual_quantity_comes_from_json_not_data_qty(self, sql):
        assert "JSON_VALUE(td.daily_data, '$.actual_quantity')" in sql
        assert "TRY_CONVERT(DECIMAL(18, 4)" in sql
        assert "ISJSON(td.daily_data)" in sql
        assert "data_qty" not in sql

    def test_required_column_is_never_read(self, sql):
        assert not re.search(r"\btd\.required\b", sql)

    def test_activity_id_extraction_matches_the_business_rule(self, sql):
        assert "LEFT(r.task_code, NULLIF(CHARINDEX('-', r.task_code), 0) - 1)" in sql

    def test_mapping_joins_are_left_joins_so_unmapped_work_survives(self, sql):
        assert "LEFT JOIN activity_mapping" in sql
        assert "LEFT JOIN activity_detail" in sql
        assert "LEFT JOIN ref.uom" in sql

    def test_grain_is_resolved_by_ranking_not_by_distinct_or_max_id(self, sql):
        assert "ROW_NUMBER() OVER" in sql
        assert "grain_rank = 1" in sql
        assert "is_actual_entry DESC" in sql
        assert "SELECT DISTINCT" not in sql.upper()

    def test_duplicate_conditions_are_carried_forward(self, sql):
        assert "group_row_count" in sql
        assert "group_actual_entry_count" in sql

    def test_wbs_and_crew_come_from_the_approved_columns(self, sql):
        assert "activity_group_description       AS wbs" in sql
        assert "amc.crew_code" in sql

    def test_activity_mapping_uses_mapping_master_not_the_superseded_table(self, sql):
        # dbo.activity_master_mapping is superseded and resolves only a
        # minority of activity_ids (measured ~30%, and 0% WBS resolution on a
        # sample date). dbo.mapping_master is the current source.
        code = _code_only(sql)
        assert "dbo.mapping_master" in code
        assert "dbo.activity_master_mapping" not in code
        assert "New_Activity_Code" in code

    def test_mapping_master_activity_id_is_cast_from_text_before_comparing(self, sql):
        # mapping_master.Activity_ID is stored as TEXT; SQL Server cannot
        # compare TEXT with = directly, so it must be CAST first.
        assert "CAST(mm.Activity_ID AS nvarchar(50))" in sql

    def test_mapping_master_crew_and_unsupported_columns_are_not_used(self, sql):
        # Crew is ONLY activity_master_csv.crew_code -- never backfilled from
        # mapping_master.New_Crew_code even though that column exists.
        code = _code_only(sql)
        assert "mm.New_Crew_code" not in code
        # mapping_master has no equivalent of the old table's project_type,
        # composition_code or class_b_ptw; nothing here may reintroduce them.
        for column in ("mm.project_type", "mm.composition_code", "mm.class_b_ptw"):
            assert column not in code

    def test_activity_master_uom_is_connected_as_reference_evidence_only(self, sql):
        code = _code_only(sql)
        assert "mm.UOM" in code
        assert "AS activity_uom" in code


class TestReadOnlyGuard:
    @pytest.mark.parametrize("name", ["daily_tasks", "daily_detail", "daily_summary"])
    def test_shipped_queries_pass_the_read_only_guard(self, name):
        sql = load_sql(name)
        if name == "daily_tasks":
            # The base fragment ends in a CTE; it is only ever used composed.
            sql = sql + "\nSELECT * FROM daily_tasks"
        assert_read_only(sql)

    @pytest.mark.parametrize(
        "statement",
        [
            "DELETE FROM well.task_daily",
            "UPDATE well.task_daily SET planned = 1",
            "INSERT INTO well.task_daily (id) VALUES (1)",
            "DROP TABLE well.task_daily",
            "TRUNCATE TABLE well.task_daily",
            "MERGE well.task_daily USING x ON 1=1",
            "ALTER TABLE well.task_daily ADD c INT",
            "SELECT 1; DROP TABLE well.task_daily",
            "EXEC sp_who",
        ],
    )
    def test_write_statements_are_rejected(self, statement):
        with pytest.raises(ReadOnlyViolation):
            assert_read_only(statement)

    def test_a_comment_mentioning_delete_does_not_trip_the_guard(self):
        assert_read_only("SELECT 1 AS x -- no DELETE happens here")

    def test_a_string_literal_mentioning_drop_does_not_trip_the_guard(self):
        assert_read_only("SELECT 'DROP TABLE' AS label")


def _dataset(tasks) -> DailyDataset:
    return DailyDataset(report_date=REPORT_DATE, tasks=tasks, counters=DayCounters())
