"""Crew suggestion against the real database.

Skips itself when SQL Server is not reachable, exactly like
test_live_wells_db.py. Every target task used below is discovered by query at
test time -- never a hardcoded well_id, task_code, activity_code or crew_id --
so these tests keep working for any target task the real data happens to
contain, not just the one example combination the feature request used to
illustrate the rule.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.config.database import fetch_all
from app.repositories.crew_repository import CrewRepository
from app.services.crew_suggestion_service import CrewSuggestionService

from tests.conftest import requires_database

pytestmark = [requires_database, pytest.mark.database]


def _one_task(where_sql: str) -> "dict | None":
    rows = fetch_all(
        f"""
        SELECT TOP 1 td.well_id, td.task_code, td.ActionOn
        FROM well.task_daily td
        WHERE TRY_CONVERT(int, td.well_id) > 1
          AND {where_sql}
        ORDER BY td.ActionOn DESC
        """,
        label="probe_target_task",
    )
    return rows[0] if rows else None


@pytest.fixture(scope="module")
def eligible_task():
    """Any real, currently unfinished, non-progressing, live-well task."""
    row = _one_task(
        "(td.progress IS NULL OR td.progress = 0) AND td.completed = 0 "
        "AND EXISTS (SELECT 1 FROM well.well_master wm "
        "WHERE wm.well_id = TRY_CONVERT(int, td.well_id) AND wm.eng_completion_date IS NULL)"
    )
    if row is None:
        pytest.skip("No eligible (unfinished, non-progressing) task available in this database")
    return row


@pytest.fixture(scope="module")
def progressing_task():
    """Any real task already showing positive recorded progress."""
    row = _one_task("td.progress IS NOT NULL AND td.progress > 0 AND td.completed = 0")
    if row is None:
        pytest.skip("No in-progress task available in this database")
    return row


@pytest.fixture(scope="module")
def completed_task():
    row = _one_task("td.completed = 1")
    if row is None:
        pytest.skip("No completed task available in this database")
    return row


class TestTargetResolutionIsDynamic:
    def test_an_eligible_task_resolves_without_any_hardcoded_identifier(self, eligible_task):
        service = CrewSuggestionService(CrewRepository())
        evidence = service.build(
            well_id=int(eligible_task["well_id"]),
            task_code=eligible_task["task_code"],
            report_date=eligible_task["ActionOn"],
        )
        assert evidence is not None
        assert evidence["eligible"] is True
        assert "suggested_crew" in evidence or "no_suggestion_reason" in evidence


class TestProgressSuppressionOnRealData:
    def test_a_real_task_already_in_progress_is_never_eligible(self, progressing_task):
        service = CrewSuggestionService(CrewRepository())
        evidence = service.build(
            well_id=int(progressing_task["well_id"]),
            task_code=progressing_task["task_code"],
            report_date=progressing_task["ActionOn"],
        )
        assert evidence is not None
        assert evidence["eligible"] is False
        assert "suggested_crew" not in evidence
        assert "progress" in evidence["suppression_reason"].lower()


class TestCompletedTaskSuppressionOnRealData:
    def test_a_real_completed_task_is_never_eligible(self, completed_task):
        service = CrewSuggestionService(CrewRepository())
        evidence = service.build(
            well_id=int(completed_task["well_id"]),
            task_code=completed_task["task_code"],
            report_date=completed_task["ActionOn"],
        )
        assert evidence is not None
        assert evidence["eligible"] is False
        assert "suggested_crew" not in evidence


class TestHistoricalCutoffOnRealData:
    def test_an_earlier_report_date_never_uses_later_historical_evidence(self, eligible_task):
        """most_recent_success_date, if present, is never after the report date
        the evidence was built for -- retrospective evidence is never
        contaminated by information from the future relative to it."""
        service = CrewSuggestionService(CrewRepository())
        report_date = eligible_task["ActionOn"]
        evidence = service.build(
            well_id=int(eligible_task["well_id"]),
            task_code=eligible_task["task_code"],
            report_date=report_date,
        )
        suggested = (evidence or {}).get("suggested_crew")
        if not suggested or not suggested.get("most_recent_success_date"):
            pytest.skip("No suggested crew with a most_recent_success_date to check")
        most_recent = date.fromisoformat(suggested["most_recent_success_date"])
        assert most_recent <= report_date

    def test_a_task_not_yet_started_by_an_earlier_date_resolves_to_no_evidence(self, eligible_task):
        """A report date far enough before the task's own first appearance
        must simply find no target task -- never invent one."""
        service = CrewSuggestionService(CrewRepository())
        much_earlier = eligible_task["ActionOn"] - timedelta(days=3650)
        evidence = service.build(
            well_id=int(eligible_task["well_id"]),
            task_code=eligible_task["task_code"],
            report_date=much_earlier,
        )
        assert evidence is None


class TestNoWritesEverOccur:
    def test_the_shipped_query_still_passes_the_read_only_guard_against_live_metadata(self):
        from app.config.database import assert_read_only
        from app.utils.sql_loader import load_sql

        assert_read_only(load_sql("crew_suggestion"))
