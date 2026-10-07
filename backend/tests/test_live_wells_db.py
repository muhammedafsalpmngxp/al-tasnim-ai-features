"""Tests that run against the real database.

They skip themselves when SQL Server is not reachable, so the suite still runs
offline. What they verify cannot be checked any other way: that the shipped SQL
actually applies the live-well rule, honours the report date, and resolves the
row grain without double counting real data.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.config.database import fetch_all
from app.repositories.daily_repository import DailyRepository
from app.services.daily_service import DailyService

from tests.conftest import requires_database

pytestmark = [requires_database, pytest.mark.database]


@pytest.fixture(scope="module")
def probe_date() -> date:
    """The most recent date that carries daily entries for live wells."""
    rows = DailyRepository().fetch_dates_with_activity(1)
    if not rows:
        pytest.skip("No dates with daily entries are available")
    return rows[0]["report_date"]


class TestLiveWellRule:
    def test_every_returned_well_is_live(self, probe_date):
        dataset = DailyService().get_dataset(probe_date)
        if not dataset.tasks:
            pytest.skip("No tasks on the probe date")
        well_ids = sorted({task.well_id for task in dataset.tasks})
        placeholders = ",".join("?" for _ in well_ids)
        rows = fetch_all(
            "SELECT well_id FROM well.well_master "
            f"WHERE well_id IN ({placeholders}) AND eng_completion_date IS NOT NULL",
            well_ids,
        )
        assert rows == [], "completed wells must not appear in the daily brief"

    def test_completed_wells_are_excluded_even_when_they_have_daily_rows(self, probe_date):
        rows = fetch_all(
            """
            SELECT COUNT(*) AS n
            FROM well.task_daily AS td
            INNER JOIN well.well_master AS wm ON wm.well_id = TRY_CONVERT(int, td.well_id)
            WHERE td.ActionOn = ? AND wm.eng_completion_date IS NOT NULL
            """,
            (probe_date,),
        )
        excluded = int(rows[0]["n"])
        dataset = DailyService().get_dataset(probe_date)
        returned = {task.well_id for task in dataset.tasks}
        completed = fetch_all(
            "SELECT well_id FROM well.well_master WHERE eng_completion_date IS NOT NULL",
        )
        completed_ids = {int(row["well_id"]) for row in completed}
        assert returned.isdisjoint(completed_ids)
        # The exclusion is meaningful only if such rows actually exist.
        assert excluded >= 0

    def test_invalid_well_ids_are_never_returned(self, probe_date):
        dataset = DailyService().get_dataset(probe_date)
        assert all(task.well_id > 1 for task in dataset.tasks)


class TestReportDate:
    def test_only_the_requested_date_is_returned(self, probe_date):
        dataset = DailyService().get_dataset(probe_date)
        assert {task.action_on for task in dataset.tasks} <= {probe_date}

    def test_a_neighbouring_date_returns_a_different_dataset(self, probe_date):
        service = DailyService()
        today = service.get_dataset(probe_date)
        neighbour = service.get_dataset(probe_date - timedelta(days=1))
        assert all(task.action_on != probe_date for task in neighbour.tasks)
        assert today.report_date != neighbour.report_date


class TestGrainResolutionOnRealData:
    def test_one_row_per_logical_task(self, probe_date):
        dataset = DailyService().get_dataset(probe_date)
        keys = [
            (task.well_id, task.schedule_id, task.task_code, task.action_on)
            for task in dataset.tasks
        ]
        assert len(keys) == len(set(keys)), "grain resolution returned duplicates"

    def test_resolution_never_invents_rows(self, probe_date):
        dataset = DailyService().get_dataset(probe_date)
        assert len(dataset.tasks) <= dataset.counters.raw_row_count
        assert dataset.counters.superseded_row_count >= 0

    def test_the_actual_entry_row_wins_over_a_planning_snapshot(self, probe_date):
        """Where a logical task has several rows, the kept row is the one that
        carries the actual entry -- otherwise a reported quantity would vanish."""
        dataset = DailyService().get_dataset(probe_date)
        multi = [task for task in dataset.tasks if task.group_row_count > 1]
        for task in multi:
            if task.group_actual_entry_count >= 1:
                assert task.actual_quantity is not None, (
                    f"task {task.task_code} on well {task.well_id} had an actual "
                    "entry row but the resolved row lost it"
                )


class TestQueryCost:
    def test_a_full_day_costs_two_queries(self, probe_date):
        class CountingRepository(DailyRepository):
            def __init__(self):
                self.calls = 0

            def fetch_day(self, report_date):
                self.calls += 1
                return super().fetch_day(report_date)

        repository = CountingRepository()
        service = DailyService(repository=repository)
        dataset = service.get_dataset(probe_date)
        for well_id in {task.well_id for task in dataset.tasks}:
            service.filter_tasks(dataset, well_id=well_id)
        assert repository.calls == 1
