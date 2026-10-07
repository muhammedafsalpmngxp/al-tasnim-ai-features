"""Tests against the real database for well-lifecycle milestones and the new
crew-personnel evidence on daily tasks. Skip themselves when SQL Server is not
reachable, same contract as test_live_wells_db.py.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.repositories.daily_repository import DailyRepository
from app.repositories.well_repository import WellRepository
from app.services.daily_service import DailyService
from app.services.milestone_service import MilestoneService

from tests.conftest import requires_database

pytestmark = [requires_database, pytest.mark.database]


class TestWellMilestonesOnRealData:
    def test_outstanding_milestones_are_only_for_live_wells_not_yet_reached(self):
        from app.config.database import fetch_all

        rows = WellRepository().fetch_outstanding_milestones()
        if not rows:
            pytest.skip("No outstanding milestones on this data")
        well_ids = sorted({int(r["well_id"]) for r in rows})
        placeholders = ",".join("?" for _ in well_ids)
        completed = fetch_all(
            f"SELECT well_id FROM well.well_master "
            f"WHERE well_id IN ({placeholders}) AND eng_completion_date IS NOT NULL",
            well_ids,
        )
        assert completed == [], "a completed well must never carry an outstanding milestone"

    def test_upcoming_window_never_returns_a_negative_days_remaining(self):
        upcoming, overdue = MilestoneService().upcoming_and_overdue(window_days=7)
        assert all(a.days_remaining >= 0 for a in upcoming)
        assert all(a.days_remaining < 0 for a in overdue)

    def test_wider_window_returns_a_superset_of_upcoming(self):
        narrow, _ = MilestoneService().upcoming_and_overdue(window_days=7)
        wide, _ = MilestoneService().upcoming_and_overdue(window_days=60)
        narrow_ids = {(a.well_id, a.milestone) for a in narrow}
        wide_ids = {(a.well_id, a.milestone) for a in wide}
        assert narrow_ids <= wide_ids


class TestCrewPersonnelOnRealData:
    def test_crew_supervisor_resolves_when_crew_id_is_present(self):
        rows = DailyRepository().fetch_dates_with_activity(5)
        if not rows:
            pytest.skip("No dates with daily entries are available")
        service = DailyService()
        for row in rows:
            dataset = service.get_dataset(row["report_date"])
            with_crew = [t for t in dataset.tasks if t.crew_id is not None]
            if with_crew:
                resolved = [t for t in with_crew if t.crew_supervisor or t.crew_type_name]
                # Not every crew_id is guaranteed to resolve on every date, but
                # the join must be capable of resolving at least some of them --
                # otherwise the join itself is broken.
                assert resolved, "no crew_id resolved to a supervisor or crew type on any task"
                return
        pytest.skip("No task carried a crew_id on the recent dates sampled")
