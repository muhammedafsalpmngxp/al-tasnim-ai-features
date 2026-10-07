"""Well-level task activity: the front page's per-well task figures, and the
same figures as evidence for a well's AI summary.

What this service owns, end to end:

* which live wells have task evidence at all as of the report date;
* how many of each well's logical tasks are incomplete, ongoing, not started
  or ended-without-being-completed;
* how many logical tasks the well reported on the report date itself;
* the last date the well appeared in the task-daily data;
* the individual incomplete tasks behind those counts, for the front page's
  expandable detail and for the AI evidence.

Every one of those is computed here or in SQL. React displays them and the
LLM explains them; neither derives one. ``task_daily.progress`` is not read
anywhere in this feature -- its unit is undefined (daily_report_rules.md
section 7), so it cannot decide whether a task is incomplete or ongoing.

Cost: one query per report date for the counters, plus one more for the
detail the first time any well's tasks are expanded. Both are cached for the
report date, so a page of wells -- and then a second, and a third, expanded
one after another -- never becomes a query per well.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date
from typing import Any, Dict, Generic, List, Optional, Tuple, TypeVar

from app.config.settings import get_settings
from app.models.daily import DailyDataset
from app.models.wells import LogicalTask, TaskState, WellTaskActivity
from app.repositories.well_repository import WellRepository
from app.utils.sql_loader import load_sql

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: How many individual tasks a well's AI evidence lists per state before it
#: switches to reporting only the count. The counts above the list always
#: cover every task regardless, so this never hides a figure -- it only bounds
#: how many examples travel to the model. Same reasoning, and the same
#: "totals cover everything, examples are a sample" contract, as
#: EvidenceService's per-scope task limit.
EVIDENCE_TASK_SAMPLE_LIMIT = 15

#: How many wells the day-scope overview names individually. The counts beside
#: the list always cover every live well in scope; this bounds only how many
#: are held up as examples, exactly as the per-task limit above does.
EVIDENCE_WELL_SAMPLE_LIMIT = 12


class _TtlCache(Generic[T]):
    """Tiny TTL cache keyed by report date.

    The same discipline as DailyService's dataset cache: a TTL that is
    configurable in one place (``DAILY_CACHE_TTL_SECONDS``), and a refresh
    that always bypasses it. Kept separate rather than shared because this
    holds a different shape of evidence with a different loading moment --
    the detail half is not loaded at all until someone asks for it.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: Dict[date, Tuple[float, T]] = {}

    def get(self, key: date, ttl_seconds: int) -> Optional[T]:
        if ttl_seconds <= 0:
            return None
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            stored_at, value = entry
            if time.monotonic() - stored_at > ttl_seconds:
                self._entries.pop(key, None)
                return None
            return value

    def put(self, key: date, value: T) -> None:
        with self._lock:
            self._entries[key] = (time.monotonic(), value)

    def invalidate(self, key: Optional[date] = None) -> None:
        with self._lock:
            if key is None:
                self._entries.clear()
            else:
                self._entries.pop(key, None)


class WellActivityService:
    """Builds per-well task activity from the deterministic evidence."""

    def __init__(self, repository: Optional[WellRepository] = None) -> None:
        self._repository = repository or WellRepository()
        self._activity_cache: _TtlCache[List[Dict[str, Any]]] = _TtlCache()
        self._detail_cache: _TtlCache[Dict[int, List[LogicalTask]]] = _TtlCache()

    # ------------------------------------------------------------------
    def invalidate(self, report_date: Optional[date] = None) -> None:
        """Drop the cached activity for one report date (or all of them).

        Called wherever the day's own dataset is force-refreshed, so
        "Refresh" reloads the front page's task-activity figures at the same
        moment it reloads everything else on that page.
        """
        self._activity_cache.invalidate(report_date)
        self._detail_cache.invalidate(report_date)

    # ------------------------------------------------------------------
    def wells_for_date(
        self,
        report_date: date,
        dataset: DailyDataset,
        *,
        refresh: bool = False,
    ) -> List[WellTaskActivity]:
        """Task activity for every live well with evidence as of ``report_date``.

        ``dataset`` is the day's already-resolved daily evidence; the count of
        tasks each well reported on the date itself is read from it rather
        than re-derived, so the front page's "today" figure is by construction
        the same number the well's own task count and drill-down show. That is
        also why this never issues a second query for it.
        """
        rows = self._activity_rows(report_date, refresh=refresh)
        today_counts = self._today_counts(dataset)

        wells = [self._to_activity(row, today_counts) for row in rows]

        # A well that reported a task on the date itself must always be on the
        # front page. It normally already is -- the same task row puts it in
        # both places -- but if the two queries were ever to disagree (a row
        # inserted between them, say), the day's own dataset wins rather than
        # a well silently vanishing from the list it belongs on.
        known = {well.well_id for well in wells}
        for well_id, count in sorted(today_counts.items()):
            if well_id not in known:
                wells.append(
                    WellTaskActivity(
                        well_id=well_id,
                        logical_task_count=0,
                        completed_task_count=0,
                        open_task_count=0,
                        incomplete_task_count=0,
                        ongoing_task_count=0,
                        not_started_task_count=0,
                        ended_not_completed_task_count=0,
                        today_reported_task_count=count,
                        last_task_date=report_date,
                    )
                )
        wells.sort(key=lambda well: well.well_id)
        return wells

    def well_for_date(
        self,
        report_date: date,
        dataset: DailyDataset,
        well_id: int,
    ) -> Optional[WellTaskActivity]:
        """One well's task activity, or ``None`` when it has no task evidence."""
        for well in self.wells_for_date(report_date, dataset):
            if well.well_id == well_id:
                return well
        return None

    def incomplete_tasks(self, report_date: date, well_id: int) -> List[LogicalTask]:
        """The incomplete logical tasks behind one well's counts.

        Served from the whole-universe detail query, loaded once per report
        date, so this stays one query for every well rather than one per well.
        """
        return list(self._detail_rows(report_date).get(well_id, []))

    # ------------------------------------------------------------------
    def evidence(
        self,
        report_date: date,
        dataset: DailyDataset,
        well_id: int,
    ) -> Optional[Dict[str, Any]]:
        """The ``task_activity`` evidence block for one well's AI summary.

        Returns ``None`` when the well has no task evidence at all as of the
        report date -- there is nothing to describe, and a block of zeroes
        would imply the well was measured when it was not.

        Everything here is already calculated. The model is given counts, a
        bounded sample of the tasks behind them, and the plain meaning of each
        figure, so it never has to (and is never able to) work one out.
        """
        activity = self.well_for_date(report_date, dataset, well_id)
        if activity is None:
            return None

        tasks = self.incomplete_tasks(report_date, well_id)
        ongoing = [task for task in tasks if task.task_state is TaskState.ONGOING]

        payload: Dict[str, Any] = {
            "well_id": well_id,
            "as_of_report_date": report_date.isoformat(),
            "task_activity": {
                "today_reported_task_count": activity.today_reported_task_count,
                "has_task_on_report_date": activity.has_task_on_report_date,
                "open_task_count": activity.open_task_count,
                "incomplete_task_count": activity.incomplete_task_count,
                "ongoing_task_count": activity.ongoing_task_count,
                "completed_task_count": activity.completed_task_count,
                "logical_task_count": activity.logical_task_count,
                "last_task_date": (
                    activity.last_task_date.isoformat()
                    if activity.last_task_date
                    else None
                ),
            },
            "task_state_summary": activity.task_state_counts,
            "ongoing_tasks": [
                self._task_evidence(task) for task in ongoing[:EVIDENCE_TASK_SAMPLE_LIMIT]
            ],
            "definitions": {
                "logical_task": (
                    "One task is one (well_id, schedule_id, task_code); its state "
                    "is the latest daily record for it on or before the report date. "
                    "The same task_code under two schedule_ids is two tasks, not a "
                    "duplicate."
                ),
                "today_reported_task_count": (
                    "Logical tasks this well reported on the report date itself. "
                    "Zero means no task was recorded for the well on that date."
                ),
                "open_task_count": (
                    "Every task whose latest recorded state is not completed. It "
                    "is exactly incomplete_task_count + ongoing_task_count, which "
                    "do not overlap."
                ),
                "incomplete_task_count": (
                    "Open tasks that are NOT ongoing: no recorded actual start, or "
                    "an actual end recorded without completion. Never includes an "
                    "ongoing task."
                ),
                "ongoing_task_count": (
                    "Open tasks that have a recorded actual start and no recorded "
                    "actual end."
                ),
                "last_task_date": (
                    "The latest date on which this well appeared in the task-daily "
                    "data, on or before the report date. It is NOT a completion "
                    "date and says nothing about what was finished."
                ),
                "task_states": {
                    TaskState.COMPLETED.value: "Recorded as completed.",
                    TaskState.ONGOING.value: (
                        "Not completed; an actual start is recorded and no actual end is."
                    ),
                    TaskState.NOT_STARTED.value: (
                        "Not completed; no actual start is recorded."
                    ),
                    TaskState.ENDED_NOT_COMPLETED.value: (
                        "Not completed, yet an actual end date is recorded. The "
                        "records say both; no rule resolves the two, so it is "
                        "reported exactly as it stands."
                    ),
                },
            },
            "constraints": {
                "progress": (
<<<<<<< HEAD
                    "task_daily.progress is not used anywhere in these figures and "
                    "must never be read as a degree of completion."
=======
                    "The recorded progress value is not used anywhere in these "
                    "figures and must never be read as a degree of completion."
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
                ),
                "delay": (
                    "No rule in this system defines when a task is late, so never "
                    "describe one as delayed, overdue or behind schedule."
                ),
                "cause": "Why a task is in its state is not recorded anywhere here.",
            },
        }

        if len(ongoing) > len(payload["ongoing_tasks"]):
            payload["ongoing_tasks_truncated"] = {
                "included": len(payload["ongoing_tasks"]),
                "total": len(ongoing),
                "note": (
                    "Only the first tasks are listed individually; "
                    "ongoing_task_count above covers all of them."
                ),
            }
        return payload

    def day_evidence(
        self,
        report_date: date,
        dataset: DailyDataset,
    ) -> Optional[Dict[str, Any]]:
        """Task activity across every live well, for the day-scope summary.

        Without this, "Explain this view" could only describe the wells that
        reported something on the date -- on a quiet day, one well and one
        task -- while several hundred live wells carried unfinished work it
        had no way of knowing about. The day's own figures still describe
        what was reported; this describes the standing position behind them,
        and the two are kept apart so neither is read as the other.

        Returns ``None`` when no live well has task evidence at all as of the
        date; there is nothing to summarise, and zeroes would imply otherwise.
        """
        wells = self.wells_for_date(report_date, dataset)
        if not wells:
            return None

        reported = [well for well in wells if well.has_task_on_report_date]
        with_open_work = [well for well in wells if well.open_task_count]
        with_incomplete = [well for well in wells if well.incomplete_task_count]
        with_ongoing = [well for well in wells if well.ongoing_task_count]
        silent_with_open_work = [
            well for well in with_open_work if not well.has_task_on_report_date
        ]

        # Busiest open work first, so the examples are the wells carrying most
        # of the total rather than whichever id sorts first. Ties break on
        # well_id so the sample -- and therefore the evidence hash behind the
        # explanation cache -- is identical for identical data.
        ranked = sorted(
            with_open_work,
            key=lambda well: (
                -well.open_task_count,
                -well.ongoing_task_count,
                well.well_id,
            ),
        )
        sample = ranked[:EVIDENCE_WELL_SAMPLE_LIMIT]

        payload: Dict[str, Any] = {
            "as_of_report_date": report_date.isoformat(),
            "well_counts": {
                "with_task_evidence": len(wells),
                "reported_on_report_date": len(reported),
                "no_task_on_report_date": len(wells) - len(reported),
                "with_open_tasks": len(with_open_work),
                "with_incomplete_tasks": len(with_incomplete),
                "with_ongoing_tasks": len(with_ongoing),
                "with_open_tasks_and_nothing_reported": len(silent_with_open_work),
            },
            "task_counts": {
                # open_total is the sum of the two below it, which do not
                # overlap -- the same split every well row shows.
                "open_total": sum(w.open_task_count for w in wells),
                "incomplete_total": sum(w.incomplete_task_count for w in wells),
                "ongoing_total": sum(w.ongoing_task_count for w in wells),
                "not_started_total": sum(w.not_started_task_count for w in wells),
                "ended_not_completed_total": sum(
                    w.ended_not_completed_task_count for w in wells
                ),
            },
            "wells_with_most_open_work": [
                {
                    "well_id": well.well_id,
                    "open_task_count": well.open_task_count,
                    "incomplete_task_count": well.incomplete_task_count,
                    "ongoing_task_count": well.ongoing_task_count,
                    "today_reported_task_count": well.today_reported_task_count,
                    "last_task_date": (
                        well.last_task_date.isoformat() if well.last_task_date else None
                    ),
                }
                for well in sample
            ],
            "definitions": {
                "scope": (
                    "Every live well with a task record on or before the report "
                    "date, not only the wells that reported something on it."
                ),
                "open_task_count": (
                    "Tasks whose latest recorded state is not completed. It is "
                    "exactly incomplete_task_count + ongoing_task_count, which do "
                    "not overlap."
                ),
                "incomplete_task_count": (
                    "Open tasks that are NOT ongoing: no recorded actual start, or "
                    "an actual end recorded without completion."
                ),
                "ongoing_task_count": (
                    "Open tasks with a recorded actual start and no recorded "
                    "actual end."
                ),
                "no_task_on_report_date": (
                    "Wells with no task recorded on the report date itself. That is "
                    "all it means: no entry for that day."
                ),
                "last_task_date": (
                    "The latest date a well appeared in the task-daily data. It is "
                    "NOT a completion date."
                ),
            },
            "constraints": {
                "progress": (
<<<<<<< HEAD
                    "task_daily.progress is not used in any of these figures and "
                    "must never be read as a degree of completion."
=======
                    "The recorded progress value is not used in any of these "
                    "figures and must never be read as a degree of completion."
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
                ),
                "delay": (
                    "No rule in this system defines when a task or a well is late, "
                    "so never describe one as delayed, overdue or behind schedule."
                ),
            },
        }
        if len(ranked) > len(sample):
            payload["wells_with_most_open_work_truncated"] = {
                "included": len(sample),
                "total": len(ranked),
                "note": (
                    "Only the wells carrying the most open work are listed "
                    "individually; the counts above cover every well in scope."
                ),
            }
        return payload

    # ------------------------------------------------------------------
    # Showing the working
    #
    # The two methods below exist for the operator, not the model: the exact
    # queries the figures came from, and the individual task records behind
    # them with the reason each one counts. Neither is ever sent to the LLM --
    # the model explains the finished figures, and handing it the query text
    # would only invite it to reason about SQL instead.
    # ------------------------------------------------------------------

    def proof_rows(self, report_date: date, well_id: int) -> List[Dict[str, Any]]:
        """Every incomplete task behind one well's counts, with its reason.

        The reason is a restatement of the record's own columns -- what
        ``completed``, ``actual_start`` and ``actual_end`` say on the latest
        row for that task -- never an interpretation of them. Read it beside
        the count and the arithmetic is checkable by hand.
        """
        rows: List[Dict[str, Any]] = []
        for task in self.incomplete_tasks(report_date, well_id):
            rows.append(
                {
                    "well_id": task.well_id,
                    "task_code": task.task_code,
                    "schedule_id": task.schedule_id,
                    "activity_code": task.activity_code,
                    "description": task.activity_description,
                    "wbs": task.wbs,
                    "task_state": task.task_state.value,
                    # Every row here is an open task; these two flags say
                    # which of the row's two figures it counts toward, and
                    # exactly one of them is ever true.
                    "counts_as_incomplete": task.task_state is not TaskState.ONGOING,
                    "counts_as_ongoing": task.task_state is TaskState.ONGOING,
                    "completed": bool(task.completed),
                    "actual_start": task.actual_start,
                    "actual_end": task.actual_end,
                    "last_task_date": task.latest_action_on,
                    "reason": self._reason(task),
                }
            )
        return rows

    @staticmethod
    def _reason(task: LogicalTask) -> str:
        """Why this task is counted the way it is, in the record's own terms."""
        seen = (
            f"latest record {task.latest_action_on.isoformat()}"
            if task.latest_action_on
            else "latest record"
        )
        if task.task_state is TaskState.ONGOING:
            return (
                f"Not completed ({seen}); actual start "
                f"{task.actual_start.isoformat() if task.actual_start else 'recorded'} "
                "and no actual end recorded, so it counts as ongoing."
            )
        if task.task_state is TaskState.NOT_STARTED:
            return (
                f"Not completed ({seen}); no actual start and no actual end recorded, "
                "so it counts as incomplete rather than ongoing."
            )
        if task.task_state is TaskState.ENDED_NOT_COMPLETED:
            return (
                f"Not completed ({seen}), although an actual end of "
                f"{task.actual_end.isoformat() if task.actual_end else 'unknown'} is "
                "recorded. Both are reported as they stand; it counts as incomplete "
                "rather than ongoing."
            )
        return f"Recorded as completed ({seen})."

    @staticmethod
    def sql_sources(report_date: date) -> List[Dict[str, Any]]:
        """The queries these figures came from, exactly as they are executed.

        The text is the shipped ``.sql`` file with its ``{{include:...}}``
        expanded -- the same string handed to the driver -- and the report
        date is listed beside it as the bound parameter it is, never spliced
        into the text. Nothing here carries a credential or a connection
        string: these files hold no such thing, and the panel showing them is
        for checking the rule, not the connection.
        """
        parameters = [f"? 1  report date = {report_date.isoformat()}"]
        return [
            {
                "label": "Counts on the well row (incomplete, ongoing, last task date)",
                "file": "backend/sql/well_task_activity.sql",
                "parameters": parameters,
                "sql": load_sql("well_task_activity"),
            },
            {
                "label": "The individual tasks behind those counts (the proof table)",
                "file": "backend/sql/well_task_activity_detail.sql",
                "parameters": parameters,
                "sql": load_sql("well_task_activity_detail"),
            },
        ]

    # ------------------------------------------------------------------
    @staticmethod
    def _task_evidence(task: LogicalTask) -> Dict[str, Any]:
        return {
            "task_code": task.task_code,
            # Part of the task's identity, not decoration: one task_code can
            # appear under two schedule_ids on the same well, and they are two
            # different tasks. Without this the pair reads as a duplicate.
            "schedule_id": task.schedule_id,
            "activity_code": task.activity_code,
            "activity_description": task.activity_description,
            "wbs": task.wbs,
            "task_state": task.task_state.value,
            "actual_start": task.actual_start.isoformat() if task.actual_start else None,
            "actual_end": task.actual_end.isoformat() if task.actual_end else None,
            "completed": bool(task.completed),
            "last_task_date": (
                task.latest_action_on.isoformat() if task.latest_action_on else None
            ),
        }

    @staticmethod
    def _today_counts(dataset: DailyDataset) -> Dict[int, int]:
        """Logical tasks per well on the report date itself.

        Counted over the day's already grain-resolved tasks (one row per
        well_id/schedule_id/task_code/ActionOn), so repeated planning
        snapshots of the same task are never counted twice.
        """
        counts: Dict[int, int] = {}
        for task in dataset.tasks:
            counts[task.well_id] = counts.get(task.well_id, 0) + 1
        return counts

    def _activity_rows(self, report_date: date, *, refresh: bool) -> List[Dict[str, Any]]:
        settings = get_settings()
        if refresh:
            self.invalidate(report_date)
        else:
            cached = self._activity_cache.get(report_date, settings.daily_cache_ttl_seconds)
            if cached is not None:
                return cached
        rows = self._repository.fetch_well_task_activity(report_date)
        self._activity_cache.put(report_date, rows)
        return rows

    def _detail_rows(self, report_date: date) -> Dict[int, List[LogicalTask]]:
        settings = get_settings()
        cached = self._detail_cache.get(report_date, settings.daily_cache_ttl_seconds)
        if cached is not None:
            return cached

        by_well: Dict[int, List[LogicalTask]] = {}
        for row in self._repository.fetch_well_task_activity_detail(report_date):
            try:
                task = self._to_task(row)
            except Exception:  # noqa: BLE001 - one bad row must not lose the well
                logger.exception(
                    "skipping unreadable well task row well_id=%s task_code=%s",
                    row.get("well_id"),
                    row.get("task_code"),
                )
                continue
            by_well.setdefault(task.well_id, []).append(task)
        self._detail_cache.put(report_date, by_well)
        return by_well

    @staticmethod
    def _to_activity(
        row: Dict[str, Any], today_counts: Dict[int, int]
    ) -> WellTaskActivity:
        well_id = int(row["well_id"])
        return WellTaskActivity(
            well_id=well_id,
            logical_task_count=int(row.get("logical_task_count") or 0),
            completed_task_count=int(row.get("completed_task_count") or 0),
            open_task_count=int(row.get("open_task_count") or 0),
            incomplete_task_count=int(row.get("incomplete_task_count") or 0),
            ongoing_task_count=int(row.get("ongoing_task_count") or 0),
            not_started_task_count=int(row.get("not_started_task_count") or 0),
            ended_not_completed_task_count=int(row.get("ended_not_completed_task_count") or 0),
            today_reported_task_count=today_counts.get(well_id, 0),
            last_task_date=row.get("last_task_date"),
        )

    @staticmethod
    def _to_task(row: Dict[str, Any]) -> LogicalTask:
        return LogicalTask(
            well_id=int(row["well_id"]),
            schedule_id=row.get("schedule_id"),
            task_code=row.get("task_code"),
            task_state=TaskState(row["task_state"]),
            latest_action_on=row.get("latest_action_on"),
            actual_start=row.get("actual_start"),
            actual_end=row.get("actual_end"),
            completed=None if row.get("completed") is None else bool(row["completed"]),
            activity_id=row.get("activity_id"),
            activity_code=row.get("activity_code"),
            activity_description=row.get("activity_description"),
            wbs=row.get("wbs"),
        )
