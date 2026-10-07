"""Classifies outstanding well milestones into "upcoming" and "overdue".

Deterministic evidence (which milestones are outstanding, and their deadline
date) comes from SQL. This module owns exactly one judgement: how many days
ahead of a deadline counts as "priority" -- a presentation threshold, not a
business rule, so it is configurable (MILESTONE_PRIORITY_WINDOW_DAYS) rather
than hardcoded, the same way DETAIL_VIEW_TASK_THRESHOLD is.
"""

from __future__ import annotations

import threading
import time
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from app.config.settings import get_settings
from app.models.wells import MilestoneAlert, MilestoneType
from app.repositories.well_repository import WellRepository


class MilestoneService:
    """Loads outstanding milestones and splits them into upcoming / overdue.

    Every live well is scanned on each call, so the raw rows are cached with
    the same TTL discipline as DailyService's dataset cache (DAILY_CACHE_TTL_SECONDS)
    -- otherwise every dashboard load or banner refresh would repeat the full
    well.well_master scan even though these dates rarely change within a day.
    """

    def __init__(self, repository: Optional[WellRepository] = None) -> None:
        self._repository = repository or WellRepository()
        self._lock = threading.Lock()
        self._cached_rows: Optional[List[Dict[str, Any]]] = None
        self._cached_at: float = 0.0

    def _outstanding_rows(self) -> List[Dict[str, Any]]:
        ttl = get_settings().daily_cache_ttl_seconds
        with self._lock:
            if (
                ttl > 0
                and self._cached_rows is not None
                and time.monotonic() - self._cached_at <= ttl
            ):
                return self._cached_rows
        rows = self._repository.fetch_outstanding_milestones()
        with self._lock:
            self._cached_rows = rows
            self._cached_at = time.monotonic()
        return rows

    def upcoming_and_overdue(
        self, window_days: int, *, today: Optional[date] = None
    ) -> Tuple[List[MilestoneAlert], List[MilestoneAlert]]:
        """Split every outstanding milestone into upcoming vs. overdue.

        ``upcoming``: 0 <= days_remaining <= window_days, soonest first -- this
        is the "priority window" a well enters as it approaches a deadline.
        ``overdue``: days_remaining < 0, most-recently-missed first. Kept
        separate rather than folded into the same list: business_rules.md
        section 3 already treats a missed deadline as a distinct, standing
        condition, and on this data the overdue backlog can run into the
        hundreds -- mixing it into "upcoming" would bury the wells that are
        still approaching their deadline under a much larger, older backlog.
        """
        reference_date = today or date.today()
        rows = self._outstanding_rows()
        alerts = [_build_alert(row, reference_date) for row in rows]

        upcoming = sorted(
            (a for a in alerts if 0 <= a.days_remaining <= window_days),
            key=lambda a: a.days_remaining,
        )
        overdue = sorted(
            (a for a in alerts if a.days_remaining < 0),
            key=lambda a: a.days_remaining,
            reverse=True,  # most-recently-missed (closest to 0) first
        )
        return upcoming, overdue


def _build_alert(row: Dict[str, Any], reference_date: date) -> MilestoneAlert:
    deadline_date = row["deadline_date"]
    return MilestoneAlert(
        well_id=int(row["well_id"]),
        milestone=MilestoneType(row["milestone"]),
        deadline_date=deadline_date,
        days_remaining=(deadline_date - reference_date).days,
        pegged_date=row.get("pegged_date"),
        flaf_issue_date=row.get("flaf_issue_date"),
        ex_rig_on_date=row.get("ex_rig_on_date"),
        rig_on_date=row.get("rig_on_date"),
        ex_rig_off_date=row.get("ex_rig_off_date"),
        rig_off_date=row.get("rig_off_date"),
    )
