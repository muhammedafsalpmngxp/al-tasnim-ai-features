<<<<<<< HEAD
"""Database access for well lifecycle evidence (not scoped to a report date).

Queries live in backend/sql and are executed here with bound parameters only,
same contract as app/repositories/daily_repository.py.
=======
"""Database access for well lifecycle evidence.

Each query is resolved per capability from its verified artifact rather than
read from a fixed file -- same contract as app/repositories/daily_repository.py,
and for the same reason: the business question is stable, the physical query
answering it is not.
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List

<<<<<<< HEAD
from app.config.database import fetch_all
from app.utils.sql_loader import load_sql
=======
from app.capability_manifest import MILESTONES, WELL_ACTIVITY, WELL_DETAIL
from app.config.database import fetch_all
from app.dynamic_client import sql_for
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)

logger = logging.getLogger(__name__)


class WellRepository:
    """Read-only access to well.well_master lifecycle evidence."""

    def fetch_outstanding_milestones(self) -> List[Dict[str, Any]]:
        """One row per not-yet-reached milestone, for every live well.

        No parameters: the query itself decides nothing about what is "near" a
        deadline, only what is still outstanding. Windowing is owned by
        app/services/milestone_service.py.
        """
<<<<<<< HEAD
        sql = load_sql("well_milestones")
=======
        sql = sql_for(MILESTONES)
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
        rows = fetch_all(sql, label="well_milestones")
        logger.info("well milestones: %d outstanding row(s)", len(rows))
        return rows

    def fetch_well_task_activity(self, report_date: date) -> List[Dict[str, Any]]:
        """Per-well task-activity counters for every live well with task
        evidence as of ``report_date``.

        One set-based query for the whole well universe -- never one query per
        well. The report date is always bound as a parameter.
        """
<<<<<<< HEAD
        sql = load_sql("well_task_activity")
=======
        sql = sql_for(WELL_ACTIVITY, report_date=report_date)
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
        rows = fetch_all(sql, (report_date,), label="well_task_activity")
        logger.info(
            "well task activity for %s: %d live well(s) with task evidence",
            report_date,
            len(rows),
        )
        return rows

    def fetch_well_task_activity_detail(self, report_date: date) -> List[Dict[str, Any]]:
        """The incomplete logical tasks behind those counters, for every live
        well at once.

        Loaded on demand (the front page's own figures do not need it) and
        then cached for the report date, so expanding one well's task list --
        and then another's -- costs one query in total, not one per well.
        """
<<<<<<< HEAD
        sql = load_sql("well_task_activity_detail")
=======
        sql = sql_for(WELL_DETAIL, report_date=report_date)
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
        rows = fetch_all(sql, (report_date,), label="well_task_activity_detail")
        logger.info(
            "well task activity detail for %s: %d incomplete logical task(s)",
            report_date,
            len(rows),
        )
        return rows
