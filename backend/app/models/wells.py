"""Internal domain objects for a well's own evidence, rather than one day's.

Two related things live here, both keyed by well rather than by daily task:

* the lifecycle milestones, which are not scoped to a report date at all --
  they are computed against "today" for every live well, independent of which
  day the operator happens to be browsing on the Daily Morning Brief;
* a well's task activity (``TaskState``, ``LogicalTask``, ``WellTaskActivity``),
  which spans every date up to and including the report date rather than that
  one date's entries.

Both are kept apart from app/models/daily.py for the same reason: nothing here
is one day's task row, so a change to how a daily entry is classified never
has to touch it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional


class MilestoneType(str, Enum):
    """The four well lifecycle dates business_rules.md defines a deadline for.

    See business_rules.md section 2 (column dictionary) and section 3
    (milestone deadlines). Location/Flowline Construction completion are
    excluded here because they have no actual-date column to test against --
    section 3 judges those by the rig rule instead, not by a date column.
    """

    PEGGING = "PEGGING"
    FLAF = "FLAF"
    RIG_ON = "RIG_ON"
    RIG_OFF = "RIG_OFF"


#: Human labels. The code above stays authoritative; this is display-only.
#: No organisation name (PDO/Al Tasnim) is shown here -- business_rules.md's
#: scope/responsibility split is an internal rule, not something this
#: operator-facing label needs to repeat.
MILESTONE_LABELS = {
    MilestoneType.PEGGING: "Pegging sheet",
    MilestoneType.FLAF: "FLAF",
    MilestoneType.RIG_ON: "Rig-on",
    MilestoneType.RIG_OFF: "Rig-off",
}


@dataclass
class MilestoneAlert:
    """One outstanding milestone for one live well, evaluated against today.

    ``days_remaining`` is signed exactly like schedule_variance_days in
    business_rules.md section 4: negative means the deadline has already
    passed (overdue), zero means it is due today, positive means it is still
    ahead. This is a deadline computed per section 3, not the schedule
    variance of section 4 -- the two are not the same number.
    """

    well_id: int
    milestone: MilestoneType
    deadline_date: date
    days_remaining: int
    #: The well's full set of lifecycle dates, for the detail view. Carried
    #: through so a click never needs a second query.
    pegged_date: Optional[date]
    flaf_issue_date: Optional[date]
    ex_rig_on_date: Optional[date]
    rig_on_date: Optional[date]
    ex_rig_off_date: Optional[date]
    rig_off_date: Optional[date]

    @property
    def overdue(self) -> bool:
        return self.days_remaining < 0

    @property
    def label(self) -> str:
        return MILESTONE_LABELS[self.milestone]


class TaskState(str, Enum):
    """The recorded state of one logical task, as of a report date.

    Deterministic, mutually exclusive and computed in SQL
    (``sql/well_task_state.sql``) from the task's own state columns --
    ``completed``, ``actual_start`` and ``actual_end``. Definition order is
    display order, the same convention ``QuantityStatus`` follows.

    ``task_daily.progress`` is deliberately not part of this: its unit is
    undefined (daily_report_rules.md section 7), so it cannot say whether a
    task is finished or under way, and it is never read by this feature.
    """

    #: completed = 1.
    COMPLETED = "COMPLETED"
    #: Not completed, an actual_start is recorded and no actual_end is --
    #: the narrow, three-part definition of a task physically under way.
    ONGOING = "ONGOING"
    #: Not completed, no actual_start recorded.
    NOT_STARTED = "NOT_STARTED"
    #: Not completed, yet an actual_end is recorded. Reported as its own
    #: state rather than folded into either of the two above, because the
    #: record says something that neither of them does.
    ENDED_NOT_COMPLETED = "ENDED_NOT_COMPLETED"


#: Human labels for the states above. The code stays authoritative.
TASK_STATE_LABELS = {
    TaskState.COMPLETED: "Completed",
    TaskState.ONGOING: "Ongoing",
    TaskState.NOT_STARTED: "Not started",
    TaskState.ENDED_NOT_COMPLETED: "Ended, not completed",
}


@dataclass
class LogicalTask:
    """One logical task (well_id, schedule_id, task_code) in its latest
    recorded state as of the report date.

    ``latest_action_on`` is the last date this task appeared in the daily
    records on or before the report date -- an activity date, never a proof
    of physical completion.
    """

    well_id: int
    schedule_id: Optional[int]
    task_code: Optional[str]
    task_state: TaskState
    latest_action_on: Optional[date]
    actual_start: Optional[date]
    actual_end: Optional[date]
    completed: Optional[bool]
    activity_id: Optional[str] = None
    activity_code: Optional[str] = None
    activity_description: Optional[str] = None
    wbs: Optional[str] = None


@dataclass
class WellTaskActivity:
    """One live well's task activity as of the report date.

    Every figure below was aggregated by SQL/Python: ``sql/well_task_activity.sql``
    for the state counts and the last task date, and the already-resolved daily
    dataset (``sql/daily_tasks.sql``'s grain) for ``today_reported_task_count``.
    Neither React nor the LLM derives any of them.

    The counts that appear side by side are disjoint, so they add up::

        open_task_count    = incomplete_task_count + ongoing_task_count
        logical_task_count = open_task_count + completed_task_count
    """

    well_id: int
    logical_task_count: int
    completed_task_count: int
    #: Every task not recorded as completed. The two figures below split this
    #: in two without overlapping:
    #:     open_task_count = incomplete_task_count + ongoing_task_count
    open_task_count: int
    #: Open but NOT ongoing -- no actual start recorded, or an actual end
    #: recorded without completion. An ongoing task is never also counted
    #: here, so the two can be read side by side and added.
    incomplete_task_count: int
    ongoing_task_count: int
    not_started_task_count: int
    ended_not_completed_task_count: int
    #: Logical tasks reported on the report date itself, at the daily grain
    #: (one per well_id/schedule_id/task_code/ActionOn), so repeated planning
    #: snapshots of one task are never counted twice.
    today_reported_task_count: int
    #: MAX(ActionOn) on or before the report date. The latest date this well
    #: appeared in the task-daily data -- NOT a completion date.
    last_task_date: Optional[date]

    @property
    def has_task_on_report_date(self) -> bool:
        return self.today_reported_task_count > 0

    @property
    def task_state_counts(self) -> "dict[str, int]":
        """The four states in definition order, zeros included.

        A state with no tasks is reported as a zero rather than omitted: on
        this row a zero is itself the answer to "how many are ongoing?".
        """
        return {
            TaskState.COMPLETED.value: self.completed_task_count,
            TaskState.ONGOING.value: self.ongoing_task_count,
            TaskState.NOT_STARTED.value: self.not_started_task_count,
            TaskState.ENDED_NOT_COMPLETED.value: self.ended_not_completed_task_count,
        }
