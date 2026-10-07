"""Well task activity against the real database.

These verify what no offline test can: that the shipped queries really do
apply the live-well rule, really do stop at the report date, and really do
classify a task's state from its own recorded columns on actual data -- and
that the front page's "reported today" figure is the same number the rest of
the brief already shows for that well.

They skip themselves when SQL Server is unreachable, same contract as
tests/test_live_wells_db.py.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.config.database import fetch_all
from app.repositories.daily_repository import DailyRepository
from app.repositories.well_repository import WellRepository
from app.services.daily_service import DailyService
from app.services.well_activity_service import WellActivityService

from tests.conftest import requires_database

pytestmark = [requires_database, pytest.mark.database]


@pytest.fixture(scope="module")
def probe_date() -> date:
    """The most recent date that carries daily entries for live wells."""
    rows = DailyRepository().fetch_dates_with_activity(1)
    if not rows:
        pytest.skip("No dates with daily entries are available")
    return rows[0]["report_date"]


@pytest.fixture(scope="module")
def activity(probe_date):
    return WellRepository().fetch_well_task_activity(probe_date)


@pytest.fixture(scope="module")
def detail(probe_date):
    return WellRepository().fetch_well_task_activity_detail(probe_date)


class TestWellUniverse:
    def test_every_well_returned_is_live(self, activity):
        if not activity:
            pytest.skip("No well carries task evidence on the probe date")
        well_ids = sorted({int(row["well_id"]) for row in activity})
        placeholders = ",".join("?" for _ in well_ids)
        completed = fetch_all(
            "SELECT well_id FROM well.well_master "
            f"WHERE well_id IN ({placeholders}) AND eng_completion_date IS NOT NULL",
            well_ids,
        )
        assert completed == [], "a completed well must never appear in task activity"

    def test_invalid_well_ids_are_never_returned(self, activity):
        assert all(int(row["well_id"]) > 1 for row in activity)

    def test_non_numeric_well_ids_exist_and_do_not_crash_the_query(self, probe_date):
        # The reason every comparison goes through TRY_CONVERT: a bare
        # comparison against these rows throws SQLSTATE 22018. If the data no
        # longer holds any, the guard is untested rather than unnecessary.
        junk = fetch_all(
            "SELECT COUNT(*) AS n FROM well.task_daily "
            "WHERE well_id IS NOT NULL AND TRY_CONVERT(int, well_id) IS NULL"
        )[0]["n"]
        rows = WellRepository().fetch_well_task_activity(probe_date)
        assert isinstance(rows, list)
        if not junk:
            pytest.skip("No non-numeric well_id rows remain in the data")

    def test_a_task_row_alone_never_creates_a_well(self, activity):
        if not activity:
            pytest.skip("No well carries task evidence on the probe date")
        well_ids = sorted({int(row["well_id"]) for row in activity})
        placeholders = ",".join("?" for _ in well_ids)
        known = fetch_all(
            f"SELECT well_id FROM well.well_master WHERE well_id IN ({placeholders})",
            well_ids,
        )
        assert len(known) == len(well_ids), "every listed well must exist in well_master"


class TestTaskStateClassification:
    def test_each_returned_state_matches_the_rows_own_columns(self, detail):
        if not detail:
            pytest.skip("No incomplete task on the probe date")
        for row in detail:
            completed = bool(row["completed"])
            started = row["actual_start"] is not None
            ended = row["actual_end"] is not None
            state = row["task_state"]
            assert not completed, "the detail query returns incomplete tasks only"
            if ended:
                assert state == "ENDED_NOT_COMPLETED"
            elif started:
                assert state == "ONGOING"
            else:
                assert state == "NOT_STARTED"

    def test_ongoing_means_started_not_ended_and_not_completed(self, detail):
        ongoing = [row for row in detail if row["task_state"] == "ONGOING"]
        if not ongoing:
            pytest.skip("No ongoing task on the probe date")
        for row in ongoing:
            assert bool(row["completed"]) is False
            assert row["actual_start"] is not None
            assert row["actual_end"] is None

    def test_a_task_with_an_actual_end_is_never_counted_as_ongoing(self, detail):
        assert all(
            row["actual_end"] is None
            for row in detail
            if row["task_state"] == "ONGOING"
        )

    def test_a_task_that_never_started_is_never_counted_as_ongoing(self, detail):
        assert all(
            row["actual_start"] is not None
            for row in detail
            if row["task_state"] == "ONGOING"
        )

    def test_progress_does_not_decide_which_tasks_are_ongoing(self, probe_date, detail):
        """The shipped query never reads progress; this proves it changes
        nothing on real data by finding ongoing tasks on both sides of the
        `progress > 0` line that an earlier, wrong rule would have split on."""
        ongoing = {
            (int(row["well_id"]), row["schedule_id"], row["task_code"])
            for row in detail
            if row["task_state"] == "ONGOING"
        }
        if not ongoing:
            pytest.skip("No ongoing task on the probe date")

        rows = fetch_all(
            """
            WITH latest AS (
                SELECT TRY_CONVERT(int, td.well_id) AS well_id, td.schedule_id, td.task_code,
                       td.progress, td.completed, td.actual_start, td.actual_end,
                       ROW_NUMBER() OVER (
                           PARTITION BY TRY_CONVERT(int, td.well_id), td.schedule_id, td.task_code
                           ORDER BY td.ActionOn DESC, td.updated_at DESC, td.id DESC) AS rn
                FROM well.task_daily AS td
                INNER JOIN well.well_master AS wm
                        ON wm.well_id = TRY_CONVERT(int, td.well_id)
                       AND wm.eng_completion_date IS NULL
                WHERE td.ActionOn <= ? AND TRY_CONVERT(int, td.well_id) > 1
            )
            SELECT well_id, schedule_id, task_code, progress
            FROM latest
            WHERE rn = 1 AND completed = 0 AND actual_start IS NOT NULL AND actual_end IS NULL
            """,
            (probe_date,),
        )
        control = {(int(r["well_id"]), r["schedule_id"], r["task_code"]) for r in rows}
        assert ongoing == control, "ongoing must follow only completed/actual_start/actual_end"

        zero_or_null = {
            (int(r["well_id"]), r["schedule_id"], r["task_code"])
            for r in rows
            if r["progress"] is None or r["progress"] <= 0
        }
        positive = control - zero_or_null
        if zero_or_null and positive:
            # Both kinds are present and both are ongoing, so progress plainly
            # does not decide the classification on this data either.
            assert zero_or_null <= ongoing and positive <= ongoing


class TestCountsAndDates:
    def test_the_counts_add_up_to_the_wells_logical_task_count(self, activity):
        for row in activity:
            assert (
                row["completed_task_count"] + row["open_task_count"]
                == row["logical_task_count"]
            )
            assert (
                row["not_started_task_count"] + row["ended_not_completed_task_count"]
                == row["incomplete_task_count"]
            )

    def test_incomplete_and_ongoing_add_up_to_open_on_real_data(self, activity):
        # The two figures sit side by side on a well's row, so they must be
        # addable: no task may be counted in both.
        assert all(
            row["incomplete_task_count"] + row["ongoing_task_count"] == row["open_task_count"]
            for row in activity
        )

    def test_the_open_counts_match_the_tasks_behind_them(self, activity, detail):
        by_well: dict[int, int] = {}
        for row in detail:
            well_id = int(row["well_id"])
            by_well[well_id] = by_well.get(well_id, 0) + 1
        for row in activity:
            expected = by_well.get(int(row["well_id"]), 0)
            assert row["open_task_count"] == expected

    def test_last_task_date_is_never_after_the_report_date(self, activity, probe_date):
        assert all(row["last_task_date"] <= probe_date for row in activity)

    def test_last_task_date_is_the_max_action_on_for_that_well(self, activity, probe_date):
        if not activity:
            pytest.skip("No well carries task evidence on the probe date")
        sample = activity[0]
        rows = fetch_all(
            """
            SELECT MAX(td.ActionOn) AS last_task_date
            FROM well.task_daily AS td
            INNER JOIN well.well_master AS wm
                    ON wm.well_id = TRY_CONVERT(int, td.well_id)
                   AND wm.eng_completion_date IS NULL
            WHERE TRY_CONVERT(int, td.well_id) = ? AND td.ActionOn <= ?
            """,
            (int(sample["well_id"]), probe_date),
        )
        assert rows[0]["last_task_date"] == sample["last_task_date"]

    def test_a_future_row_never_affects_an_earlier_report_date(self, probe_date):
        """Asked for an older date, the answer must stop at that date -- a row
        recorded afterwards cannot reach back into it."""
        earlier = probe_date - timedelta(days=30)
        rows = WellRepository().fetch_well_task_activity(earlier)
        assert all(row["last_task_date"] <= earlier for row in rows)

    def test_an_older_report_date_never_sees_more_task_history(self, probe_date):
        earlier = probe_date - timedelta(days=30)
        now_counts = {
            int(r["well_id"]): r["logical_task_count"]
            for r in WellRepository().fetch_well_task_activity(probe_date)
        }
        then_counts = {
            int(r["well_id"]): r["logical_task_count"]
            for r in WellRepository().fetch_well_task_activity(earlier)
        }
        for well_id, count in then_counts.items():
            assert count <= now_counts.get(well_id, 0)


class TestReportDateCountMatchesTheRestOfTheBrief:
    def test_today_reported_task_count_equals_the_days_own_task_count(self, probe_date):
        dataset = DailyService().get_dataset(probe_date)
        wells = WellActivityService().wells_for_date(probe_date, dataset)
        expected: dict[int, int] = {}
        for task in dataset.tasks:
            expected[task.well_id] = expected.get(task.well_id, 0) + 1
        for well in wells:
            assert well.today_reported_task_count == expected.get(well.well_id, 0)

    def test_every_well_that_reported_today_is_on_the_list(self, probe_date):
        dataset = DailyService().get_dataset(probe_date)
        wells = {w.well_id for w in WellActivityService().wells_for_date(probe_date, dataset)}
        assert {task.well_id for task in dataset.tasks} <= wells


class TestQueryCost:
    def test_the_whole_page_costs_one_query_however_many_wells_are_expanded(self, probe_date):
        class CountingRepository(WellRepository):
            def __init__(self):
                self.activity_calls = 0
                self.detail_calls = 0

            def fetch_well_task_activity(self, report_date):
                self.activity_calls += 1
                return super().fetch_well_task_activity(report_date)

            def fetch_well_task_activity_detail(self, report_date):
                self.detail_calls += 1
                return super().fetch_well_task_activity_detail(report_date)

        repository = CountingRepository()
        service = WellActivityService(repository=repository)
        dataset = DailyService().get_dataset(probe_date)
        wells = service.wells_for_date(probe_date, dataset)
        for well in wells[:25]:
            service.incomplete_tasks(probe_date, well.well_id)
        assert repository.activity_calls == 1
        assert repository.detail_calls <= 1
