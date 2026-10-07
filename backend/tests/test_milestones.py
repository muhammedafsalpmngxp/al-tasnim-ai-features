"""Deterministic tests for the milestone priority-window classification.

These run entirely offline against a fake repository shaped like
well_milestones.sql's output -- the windowing decision (upcoming vs overdue,
sort order) is pure Python and must be verifiable without SQL Server.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List

from app.models.wells import MilestoneType
from app.services.milestone_service import MilestoneService

TODAY = date(2026, 9, 14)


def _row(well_id: int, milestone: str, days_from_today: int, **overrides: Any) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "well_id": well_id,
        "milestone": milestone,
        "deadline_date": TODAY + timedelta(days=days_from_today),
        "pegged_date": None,
        "flaf_issue_date": None,
        "ex_rig_on_date": None,
        "rig_on_date": None,
        "ex_rig_off_date": None,
        "rig_off_date": None,
    }
    row.update(overrides)
    return row


class FakeWellRepository:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows

    def fetch_outstanding_milestones(self):
        return self._rows


def test_within_window_is_upcoming_sorted_soonest_first():
    rows = [
        _row(1, "PEGGING", 3),
        _row(2, "RIG_ON", 1),
        _row(3, "FLAF", 7),
    ]
    service = MilestoneService(FakeWellRepository(rows))
    upcoming, overdue = service.upcoming_and_overdue(window_days=7, today=TODAY)

    assert overdue == []
    assert [a.well_id for a in upcoming] == [2, 1, 3]
    assert [a.days_remaining for a in upcoming] == [1, 3, 7]


def test_beyond_window_is_excluded_entirely():
    rows = [_row(1, "PEGGING", 8)]
    service = MilestoneService(FakeWellRepository(rows))
    upcoming, overdue = service.upcoming_and_overdue(window_days=7, today=TODAY)
    assert upcoming == []
    assert overdue == []


def test_due_today_counts_as_upcoming_not_overdue():
    rows = [_row(1, "RIG_OFF", 0)]
    service = MilestoneService(FakeWellRepository(rows))
    upcoming, overdue = service.upcoming_and_overdue(window_days=7, today=TODAY)
    assert len(upcoming) == 1
    assert upcoming[0].days_remaining == 0
    assert upcoming[0].overdue is False
    assert overdue == []


def test_past_deadline_is_overdue_sorted_most_recently_missed_first():
    rows = [
        _row(1, "PEGGING", -30),
        _row(2, "FLAF", -1),
        _row(3, "RIG_ON", -400),
    ]
    service = MilestoneService(FakeWellRepository(rows))
    upcoming, overdue = service.upcoming_and_overdue(window_days=7, today=TODAY)

    assert upcoming == []
    assert [a.well_id for a in overdue] == [2, 1, 3]
    assert all(a.overdue for a in overdue)


def test_milestone_type_and_label_round_trip():
    rows = [_row(1, "RIG_ON", 2)]
    service = MilestoneService(FakeWellRepository(rows))
    upcoming, _ = service.upcoming_and_overdue(window_days=7, today=TODAY)
    assert upcoming[0].milestone is MilestoneType.RIG_ON
    assert "Rig-on" in upcoming[0].label
