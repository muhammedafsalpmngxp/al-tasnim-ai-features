"""Crew suggestion: an advisory extension of the existing task AI summary.

These tests run entirely offline against ``CrewSuggestionService`` (with a
stub repository standing in for the database) and against the shipped SQL
text itself -- exactly the pattern ``test_grain_and_sql.py`` already uses for
``daily_tasks.sql``. Live-database behaviour (real historical evidence, real
report-date cutoffs) is covered separately in
``test_crew_suggestion_live_db.py``, which skips itself when SQL Server is not
reachable.

No test here ever hardcodes a real well_id, task_code, activity_code or crew_id
as a business value -- every id below is a synthetic fixture, standing in for
"any target task", exactly as the feature itself must work for any target task.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, Optional

import pytest

from app.config.database import assert_read_only
from app.repositories.crew_repository import CrewRepository
from app.services.crew_suggestion_service import CrewSuggestionService
from app.utils.sql_loader import load_sql

_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)


def _code_only(sql: str) -> str:
    return _COMMENT_BLOCK.sub(" ", sql)


class StubCrewRepository(CrewRepository):
    """Returns a fixed row (or None) instead of reaching the database."""

    def __init__(self, row: Optional[Dict[str, Any]]) -> None:
        self._row = row

    def fetch_target_task(self, *, well_id, task_code, report_date):
        return self._row


def _base_row(**overrides: Any) -> Dict[str, Any]:
    """A crew_suggestion.sql row shaped exactly like the real query returns,
    for an eligible task with one strong suggested crew."""
    row: Dict[str, Any] = {
        "target_well_id": 90001,
        "target_task_code": "ZZZZ0001-90001",
        "target_schedule_id": 1,
        "target_action_date": date(2026, 6, 1),
        "activity_id": "ZZZZ0001",
        "activity_code": "Z-Z-TST-001",
        "wbs": "Synthetic Test WBS",
        "wbs_crew_code": "ZTC-0001",
        "target_completed": False,
        "target_progress": None,
        "target_actual_start": None,
        "target_actual_end": None,
        "well_status": "INCOMPLETE_WELL",
        "crew_suggestion_eligible": 1,
        "crew_suggestion_suppression_reason": None,
        "crew_suggestion_suppression_code": None,
        "current_crew_id": None,
        "current_crew_type": None,
        "current_crew_instance_code": None,
        "current_crew_supervisor": None,
        "candidate_crew_id": 55501,
        "candidate_crew_type": "Synthetic Crew Type",
        "candidate_crew_instance_code": "SYN-0001",
        "candidate_crew_supervisor": "A. Synthetic Supervisor",
        "historical_completed_task_count": 6,
        "distinct_completed_well_count": 6,
        "completed_on_incomplete_well_count": 2,
        "completed_on_completed_well_count": 4,
        "typical_completion_days": 3.0,
        "average_completion_days": 4.5,
        "shortest_completion_days": 1,
        "longest_completion_days": 12,
        "most_recent_success_date": date(2026, 5, 1),
        "evidence_strength": "STRONG_HISTORY",
        "derived_availability": "NO_CURRENT_UNFINISHED_TASK",
        "historical_crew_candidate_count": 3,
        "available_crew_candidate_count": 1,
    }
    row.update(overrides)
    return row


class TestEligibleWithSuggestedCrew:
    def test_suggested_crew_carries_full_evidence(self):
        service = CrewSuggestionService(repository=StubCrewRepository(_base_row()))
        evidence = service.build(well_id=90001, task_code="ZZZZ0001-90001", report_date=date(2026, 6, 1))

        assert evidence["eligible"] is True
        assert evidence["suppression_reason"] is None
        suggested = evidence["suggested_crew"]
        assert suggested["crew_id"] == 55501
        assert suggested["historical_completed_task_count"] == 6
        assert suggested["distinct_completed_well_count"] == 6
        assert suggested["evidence_strength"] == "STRONG_HISTORY"
        assert suggested["derived_availability"] == "NO_CURRENT_UNFINISHED_TASK"

    def test_typical_is_median_and_average_is_mean_and_never_conflated(self):
        row = _base_row(typical_completion_days=3.0, average_completion_days=4.5)
        service = CrewSuggestionService(repository=StubCrewRepository(row))
        evidence = service.build(well_id=90001, task_code="ZZZZ0001-90001", report_date=date(2026, 6, 1))

        suggested = evidence["suggested_crew"]
        assert suggested["typical_completion_days"] == 3.0
        assert suggested["average_completion_days"] == 4.5
        assert suggested["typical_completion_days"] != suggested["average_completion_days"]

    def test_completion_on_an_incomplete_historical_well_still_counts(self):
        # daily_report_rules.md: task-level completion is valid evidence even
        # when the historical well itself was never completed.
        row = _base_row(completed_on_incomplete_well_count=5, completed_on_completed_well_count=0)
        service = CrewSuggestionService(repository=StubCrewRepository(row))
        evidence = service.build(well_id=90001, task_code="ZZZZ0001-90001", report_date=date(2026, 6, 1))

        suggested = evidence["suggested_crew"]
        assert suggested["completed_on_incomplete_well_count"] == 5
        assert suggested["historical_completed_task_count"] == 6

    def test_current_crew_recorded_when_present(self):
        row = _base_row(
            current_crew_id=77001,
            current_crew_type="Existing Crew Type",
            current_crew_supervisor="B. Existing Supervisor",
        )
        service = CrewSuggestionService(repository=StubCrewRepository(row))
        evidence = service.build(well_id=90001, task_code="ZZZZ0001-90001", report_date=date(2026, 6, 1))

        assert evidence["current_crew"]["recorded"] is True
        assert evidence["current_crew"]["crew_id"] == 77001
        assert evidence["current_crew"]["supervisor"] == "B. Existing Supervisor"


class TestCurrentCrewNull:
    def test_null_crew_id_is_not_recorded_not_no_crew_assigned(self):
        """NULL does not prove no assignment happened -- only that none is
        recorded on this task record. The evidence must say exactly that."""
        row = _base_row(current_crew_id=None, current_crew_type=None, current_crew_supervisor=None)
        service = CrewSuggestionService(repository=StubCrewRepository(row))
        evidence = service.build(well_id=90001, task_code="ZZZZ0001-90001", report_date=date(2026, 6, 1))

        assert evidence["current_crew"]["recorded"] is False
        assert evidence["current_crew"]["crew_id"] is None


class TestEligibilitySuppression:
    def test_completed_task_suppresses_suggestion_entirely(self):
        # A completed task never gets a candidate attached at all (the SQL
        # join itself excludes it) -- there is nothing left to consult either.
        row = _base_row(
            crew_suggestion_eligible=0,
            crew_suggestion_suppression_reason="Task already completed.",
            crew_suggestion_suppression_code="COMPLETED",
            target_completed=True,
            candidate_crew_id=None,
            candidate_crew_type=None,
            candidate_crew_supervisor=None,
            historical_completed_task_count=None,
            distinct_completed_well_count=None,
            completed_on_incomplete_well_count=None,
            completed_on_completed_well_count=None,
            typical_completion_days=None,
            average_completion_days=None,
            shortest_completion_days=None,
            longest_completion_days=None,
            most_recent_success_date=None,
            evidence_strength=None,
            derived_availability=None,
        )
        service = CrewSuggestionService(repository=StubCrewRepository(row))
        evidence = service.build(well_id=90001, task_code="ZZZZ0001-90001", report_date=date(2026, 6, 1))

        assert evidence["eligible"] is False
        assert evidence["suppression_reason"] == "Task already completed."
        assert "suggested_crew" not in evidence
        assert "consult_crew" not in evidence

    def test_progress_is_never_rendered_as_a_percentage(self):
        # The service must not introduce any percentage framing of progress;
        # it only ever forwards eligible/suppression_reason booleans/strings.
        row = _base_row(
            crew_suggestion_eligible=0,
            crew_suggestion_suppression_reason="Task is already in progress; crew suggestion suppressed.",
            crew_suggestion_suppression_code="IN_PROGRESS",
        )
        service = CrewSuggestionService(repository=StubCrewRepository(row))
        evidence = service.build(well_id=90001, task_code="ZZZZ0001-90001", report_date=date(2026, 6, 1))
        assert "%" not in str(evidence)


class TestInProgressGetsAnInformationalConsultCrewMention:
    """A task already showing recorded progress does not need a replacement,
    but the same historically-proven crew is still surfaced -- as a
    consult_crew mention, never as suggested_crew -- so the feature stays
    visible even when nothing needs to change."""

    def test_in_progress_with_a_historical_candidate_gets_consult_crew(self):
        row = _base_row(
            crew_suggestion_eligible=0,
            crew_suggestion_suppression_reason="Task is already in progress; crew suggestion suppressed.",
            crew_suggestion_suppression_code="IN_PROGRESS",
            target_progress="0.6",
        )
        service = CrewSuggestionService(repository=StubCrewRepository(row))
        evidence = service.build(well_id=90001, task_code="ZZZZ0001-90001", report_date=date(2026, 6, 1))

        assert evidence["eligible"] is False
        assert "suggested_crew" not in evidence  # never framed as a replacement
        consult = evidence["consult_crew"]
        assert consult["crew_id"] == 55501
        assert consult["historical_completed_task_count"] == 6
        assert consult["typical_completion_days"] == 3.0

    def test_in_progress_with_no_historical_candidate_gets_nothing(self):
        row = _base_row(
            crew_suggestion_eligible=0,
            crew_suggestion_suppression_reason="Task is already in progress; crew suggestion suppressed.",
            crew_suggestion_suppression_code="IN_PROGRESS",
            candidate_crew_id=None,
            candidate_crew_type=None,
            candidate_crew_supervisor=None,
            historical_completed_task_count=None,
            distinct_completed_well_count=None,
            completed_on_incomplete_well_count=None,
            completed_on_completed_well_count=None,
            typical_completion_days=None,
            average_completion_days=None,
            shortest_completion_days=None,
            longest_completion_days=None,
            most_recent_success_date=None,
            evidence_strength=None,
            derived_availability=None,
        )
        service = CrewSuggestionService(repository=StubCrewRepository(row))
        evidence = service.build(well_id=90001, task_code="ZZZZ0001-90001", report_date=date(2026, 6, 1))

        assert evidence["eligible"] is False
        assert "suggested_crew" not in evidence
        assert "consult_crew" not in evidence
        assert "no_suggestion_reason" not in evidence


class TestNoSuitableCrewFound:
    def test_unmapped_activity_gives_a_reason_and_no_crew(self):
        row = _base_row(
            activity_code=None,
            wbs=None,
            candidate_crew_id=None,
            historical_crew_candidate_count=0,
            available_crew_candidate_count=0,
        )
        service = CrewSuggestionService(repository=StubCrewRepository(row))
        evidence = service.build(well_id=90001, task_code="ZZZZ0001-90001", report_date=date(2026, 6, 1))

        assert evidence["eligible"] is True
        assert "suggested_crew" not in evidence
        assert "not mapped" in evidence["no_suggestion_reason"]

    def test_no_historical_crew_at_all_gives_a_reason_and_no_crew(self):
        row = _base_row(
            candidate_crew_id=None,
            historical_crew_candidate_count=0,
            available_crew_candidate_count=0,
        )
        service = CrewSuggestionService(repository=StubCrewRepository(row))
        evidence = service.build(well_id=90001, task_code="ZZZZ0001-90001", report_date=date(2026, 6, 1))

        assert evidence["eligible"] is True
        assert "suggested_crew" not in evidence
        assert evidence["no_suggestion_reason"]
        assert "no crew" not in evidence["no_suggestion_reason"].lower() or "no historical" in evidence["no_suggestion_reason"].lower()

    def test_all_historical_crews_busy_gives_a_reason_and_no_crew(self):
        row = _base_row(
            candidate_crew_id=None,
            historical_crew_candidate_count=4,
            available_crew_candidate_count=0,
        )
        service = CrewSuggestionService(repository=StubCrewRepository(row))
        evidence = service.build(well_id=90001, task_code="ZZZZ0001-90001", report_date=date(2026, 6, 1))

        assert evidence["eligible"] is True
        assert "suggested_crew" not in evidence
        assert "current unfinished task" in evidence["no_suggestion_reason"]


class TestTargetTaskNotFound:
    def test_none_when_the_target_task_cannot_be_resolved(self):
        service = CrewSuggestionService(repository=StubCrewRepository(None))
        evidence = service.build(well_id=1, task_code="DOES-NOT-EXIST", report_date=date(2026, 6, 1))
        assert evidence is None

    def test_blank_task_code_never_reaches_the_repository(self):
        class ExplodingRepository(CrewRepository):
            def fetch_target_task(self, *, well_id, task_code, report_date):
                raise AssertionError("must not query with a blank task_code")

        service = CrewSuggestionService(repository=ExplodingRepository())
        assert service.build(well_id=1, task_code="", report_date=date(2026, 6, 1)) is None


class TestCrewSuggestionSqlContract:
    """crew_suggestion.sql must keep the same guarantees daily_tasks.sql does,
    validated the same way test_grain_and_sql.py validates the base query."""

    @pytest.fixture(scope="class")
    def sql(self) -> str:
        return load_sql("crew_suggestion")

    def test_passes_the_read_only_guard(self, sql):
        assert_read_only(sql)

    def test_well_id_is_never_compared_or_joined_without_try_convert(self, sql):
        for line in sql.splitlines():
            if "td.well_id" not in line:
                continue
            stripped = line.strip()
            if stripped.startswith(("/*", "*", "--")):
                continue
            assert "TRY_CONVERT(INT, td.well_id)" in line or "TRY_CONVERT(int, td.well_id)" in line, (
                f"td.well_id used outside TRY_CONVERT: {line!r}"
            )

    def test_report_date_and_identifiers_are_parameterised(self, sql):
        assert "CAST(? AS DATE)" in sql
        assert "CAST(? AS INT)" in sql
        assert "CAST(? AS NVARCHAR(100))" in sql
        # No literal date, and no literal well/task identifier, anywhere.
        assert not re.search(r"'\d{4}-\d{2}-\d{2}'", sql)

    def test_no_hardcoded_business_values(self, sql):
        # These were the illustrative values in the feature request; none may
        # appear as a literal in the shipped query.
        code = _code_only(sql)
        for literal in ("10239", "FLME1046-10239", "M-S-MTS-LTU-51"):
            assert literal not in code

    def test_activity_id_extraction_matches_the_business_rule(self, sql):
        assert "LEFT(tl.task_code, NULLIF(CHARINDEX('-', tl.task_code), 0) - 1)" in sql
        assert "LEFT(hr.task_code, NULLIF(CHARINDEX('-', hr.task_code), 0) - 1)" in sql

    def test_mapping_uses_mapping_master_and_activity_master_csv(self, sql):
        code = _code_only(sql)
        assert "dbo.mapping_master" in code
        assert "dbo.activity_master_csv" in code
        assert "New_Activity_Code" in code
        assert "activity_group_description" in code

    def test_historical_completion_does_not_require_a_completed_well(self, sql):
        # The historical-evidence CTEs must never gate on well completion --
        # only the busy-crew availability signal is allowed to.
        historical_section = sql.split("HistoricalRaw AS", 1)[1].split("LatestCrewTasks AS", 1)[0]
        assert "eng_completion_date IS NOT NULL" not in historical_section
        assert "eng_completion_date IS NULL" in historical_section  # used to classify, not to exclude

    def test_historical_evidence_excludes_the_target_well(self, sql):
        assert "TRY_CONVERT(INT, td.well_id) <> p.well_id" in sql

    def test_duration_uses_actual_start_to_actual_end(self, sql):
        assert "DATEDIFF(DAY, h.actual_start, h.actual_end)" in sql

    def test_typical_duration_is_a_median_not_the_average(self, sql):
        assert "PERCENTILE_CONT(0.5)" in sql
        assert "AVG(CAST(hw.completion_days AS DECIMAL(18, 4)))" in sql

    def test_median_never_combines_distinct_with_a_windowed_function(self, sql):
        # SQL Server rejects SELECT DISTINCT sharing a select list with an
        # OVER(...) window function in this project's experience -- the
        # median CTE must collapse via ROW_NUMBER() = 1 instead.
        for line in sql.splitlines():
            if "OVER" in line:
                assert "DISTINCT" not in line

    def test_busy_crews_use_latest_logical_state_not_raw_snapshots(self, sql):
        latest_section = sql.split("LatestCrewTasks AS", 1)[1].split("BusyCrews AS", 1)[0]
        assert "ROW_NUMBER() OVER" in latest_section
        assert "grain_rank" in latest_section or "PARTITION BY" in latest_section

    def test_busy_is_a_hard_filter_not_a_ranking_score(self, sql):
        assert "derived_availability = 'NO_CURRENT_UNFINISHED_TASK'" in sql

    def test_employee_status_is_never_read(self, sql):
        assert "emp_status" not in _code_only(sql)

    def test_eligibility_is_decided_in_sql_not_left_to_the_caller(self, sql):
        assert "crew_suggestion_eligible" in sql
        assert "crew_suggestion_suppression_reason" in sql

    def test_ranking_is_the_fixed_deterministic_order(self, sql):
        ranking_block = sql.split("RankedCandidates AS", 1)[1].split("SELECT", 2)[1]
        assert "historical_completed_task_count DESC" in ranking_block
        assert "distinct_completed_well_count DESC" in ranking_block
        assert "typical_completion_days ASC" in ranking_block
        assert "most_recent_success_date DESC" in ranking_block
        assert "crew_id ASC" in ranking_block

    def test_evidence_strength_thresholds_match_the_business_rule(self, sql):
        assert "'STRONG_HISTORY'" in sql
        assert "'LIMITED_HISTORY'" in sql
        assert "'SINGLE_HISTORY'" in sql
        assert "'NO_HISTORY'" in sql

    def test_only_crews_with_actual_history_are_ever_candidates(self, sql):
        assert "ch.historical_completed_task_count >= 1" in sql

    def test_no_writes_anywhere_in_the_file(self, sql):
        code_upper = _code_only(sql).upper()
        for forbidden in ("INSERT ", "UPDATE ", "DELETE ", "MERGE ", "ALTER ", "DROP ", "TRUNCATE ", "EXEC "):
            assert forbidden not in code_upper

    def test_suppression_code_is_machine_checkable_alongside_the_sentence(self, sql):
        assert "crew_suggestion_suppression_code" in sql
        assert "'COMPLETED'" in sql
        assert "'IN_PROGRESS'" in sql

    def test_a_candidate_crew_is_attached_for_any_non_completed_task(self, sql):
        # Not just the stalled/eligible case -- an in-progress task must also
        # get a candidate attached, so the service can offer it as an
        # informational consult_crew mention rather than staying silent.
        assert "ISNULL(te.completed, 0) = 0" in sql
        # The old eligibility-only join predicate must not have survived.
        assert "ON te.crew_suggestion_eligible = 1" not in sql
