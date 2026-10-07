"""Dataset A: the grouped daily summary (Validation status -> WBS -> Activity).

Aggregated from the very same resolved tasks the detail dataset returns, so
every figure on a summary card is traceable to the rows behind it.

Why the hierarchy starts at status
----------------------------------
The top level used to be UOM. That made the shape of the dashboard depend on
how many distinct units happened to appear on a given day, and it buried the
one question a morning brief exists to answer -- did yesterday's work land on
plan? -- two levels down. The five deterministic classes from
validation_service are the top level instead:

    ON_PLAN -> ABOVE_PLAN -> BELOW_PLAN -> NO_ACTUAL -> NOT_VALIDATED

The order is :class:`QuantityStatus` definition order, and it is fixed: the API
emits sections in it and the UI does not re-sort them, so the same status is in
the same place from one morning to the next.

A consequence worth stating plainly: every task inside a status group shares
that status, so the nested WBS and activity levels carry a ``task_count`` and
no per-level status breakdown. There is nothing left to break down.

Two rules still shape the aggregation:

* Quantities are summed **only within a single UOM**. No conversion between
  units exists in daily_report_rules.md, so a m3 total and a Joint total are never
  added together. A status group *can* span several units -- that is what
  grouping by status rather than by unit costs -- so a group reports totals
  only when every task in it shares one unit, and otherwise reports
  ``quantities_summable = false`` together with the units it spans. This is the
  same rule ``routes_daily._well_summaries`` and ``EvidenceService`` already
  apply; nothing is converted to make a total fit.
* ``well_count`` counts DISTINCT wells, while ``task_count`` counts TASKS. One
  well can run several tasks in the same group on the same day, so the two are
  different measures and are labelled as such in the API and the UI.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Dict, List, Optional, Set

from app.models.daily import (
    DailyDataset,
    DailyTask,
    DataQualityFlag,
    QuantityStatus,
)


class _Aggregate:
    """Counts, quantities and units shared by every level of the hierarchy.

    Quantities are accumulated as tasks arrive but are only *reported* when the
    finished group turns out to hold a single unit of measure. Withholding at
    read time rather than at write time is what lets one pass over the tasks
    serve every level: a group cannot know whether it spans two units until the
    last task has been added to it.
    """

    def __init__(self) -> None:
        self.task_count = 0
        self.data_quality_counts: Dict[str, int] = {}
        self._planned: Optional[Decimal] = None
        self._actual: Optional[Decimal] = None
        self._wells: Set[int] = set()
        self._uom_codes: Set[Optional[str]] = set()

    def _accumulate(self, task: DailyTask) -> None:
        self.task_count += 1
        self._wells.add(task.well_id)
        self._uom_codes.add(task.uom_code)

        if task.planned is not None:
            self._planned = (self._planned or Decimal(0)) + task.planned
        if task.actual_quantity is not None:
            self._actual = (self._actual or Decimal(0)) + task.actual_quantity

        for flag in task.data_quality_flags:
            self.data_quality_counts[flag.value] = (
                self.data_quality_counts.get(flag.value, 0) + 1
            )

    @property
    def well_count(self) -> int:
        return len(self._wells)

    @property
    def well_ids(self) -> List[int]:
        return sorted(self._wells)

    @property
    def quantities_summable(self) -> bool:
        """True when every task in this group shares one unit of measure.

        "UOM not recorded" counts as a unit of its own here: those tasks group
        together and their total is reported without a unit label, which is
        exactly what the ``MISSING_UOM`` flag already tells the reader.
        """
        return len(self._uom_codes) == 1

    @property
    def uom_code(self) -> Optional[str]:
        """The group's single unit, or None when it spans several (or records none)."""
        return next(iter(self._uom_codes)) if self.quantities_summable else None

    @property
    def uom_codes(self) -> List[str]:
        """Every unit present, so the UI can name them when a total is withheld."""
        return sorted(code for code in self._uom_codes if code is not None)

    @property
    def planned_quantity(self) -> Optional[Decimal]:
        return self._planned if self.quantities_summable else None

    @property
    def actual_quantity(self) -> Optional[Decimal]:
        return self._actual if self.quantities_summable else None


class ActivityGroup(_Aggregate):
    """Leaf of the hierarchy: one activity within one WBS within one status."""

    def __init__(
        self,
        status: QuantityStatus,
        wbs: Optional[str],
        activity_code: Optional[str],
        activity_description: Optional[str],
    ) -> None:
        super().__init__()
        self.status = status
        self.wbs = wbs
        self.activity_code = activity_code
        self.activity_description = activity_description

    def add(self, task: DailyTask) -> None:
        self._accumulate(task)


class WbsGroup(_Aggregate):
    """One WBS / work category within one status."""

    def __init__(self, status: QuantityStatus, wbs: Optional[str]) -> None:
        super().__init__()
        self.status = status
        self.wbs = wbs
        self.activities: Dict[str, ActivityGroup] = {}

    def add(self, task: DailyTask) -> None:
        key = task.activity_key
        group = self.activities.get(key)
        if group is None:
            group = ActivityGroup(
                status=self.status,
                wbs=task.wbs,
                activity_code=task.activity_code,
                activity_description=task.activity_description,
            )
            self.activities[key] = group
        group.add(task)
        self._accumulate(task)

    def sorted_activities(self) -> List[ActivityGroup]:
        """Busiest activity first; the unmapped bucket always sorts last."""
        return sorted(
            self.activities.values(),
            key=lambda g: (g.activity_code is None, -g.task_count, g.activity_code or ""),
        )


class StatusGroup(_Aggregate):
    """Top level of the hierarchy: one validation status for the whole day."""

    def __init__(self, status: QuantityStatus) -> None:
        super().__init__()
        self.status = status
        self.wbs_groups: Dict[str, WbsGroup] = {}

    def add(self, task: DailyTask) -> None:
        key = task.wbs_key
        group = self.wbs_groups.get(key)
        if group is None:
            group = WbsGroup(status=self.status, wbs=task.wbs)
            self.wbs_groups[key] = group
        group.add(task)
        self._accumulate(task)

    def sorted_wbs_groups(self) -> List[WbsGroup]:
        """Busiest work category first; the unmapped bucket always sorts last."""
        return sorted(
            self.wbs_groups.values(),
            key=lambda g: (g.wbs is None, -g.task_count, g.wbs or ""),
        )


class GroupingService:
    """Turns resolved daily tasks into the grouped summary."""

    def build(self, dataset: DailyDataset) -> List[StatusGroup]:
        """Status groups for the day, in :class:`QuantityStatus` order.

        A status with no tasks produces no section: the day-level totals already
        report it as a zero, and an empty section on the brief would be noise.
        """
        groups: Dict[str, StatusGroup] = {}
        for task in dataset.tasks:
            key = task.status_key
            group = groups.get(key)
            if group is None:
                group = StatusGroup(status=task.quantity_status)
                groups[key] = group
            group.add(task)

        return [
            groups[member.value] for member in QuantityStatus if member.value in groups
        ]

    def day_totals(self, dataset: DailyDataset) -> Dict[str, object]:
        """Day-level totals.

        Quantities are deliberately absent: summing across different UOM would
        require a conversion rule that daily_report_rules.md does not define.
        """
        status_counts = {member.value: 0 for member in QuantityStatus}
        wells: Set[int] = set()
        for task in dataset.tasks:
            status_counts[task.quantity_status.value] += 1
            wells.add(task.well_id)
        return {
            "task_count": len(dataset.tasks),
            "well_count": len(wells),
            "status_counts": status_counts,
        }

    def data_quality(self, dataset: DailyDataset) -> Dict[str, object]:
        """Day-level data-quality tally, kept apart from the operational numbers."""
        counts: Dict[str, int] = {}
        for task in dataset.tasks:
            for flag in task.data_quality_flags:
                counts[flag.value] = counts.get(flag.value, 0) + 1

        counters = dataset.counters
        return {
            "flag_counts": counts,
            "affected_task_count": sum(
                1 for task in dataset.tasks if task.data_quality_flags
            ),
            "raw_row_count": counters.raw_row_count,
            "logical_task_count": len(dataset.tasks),
            "superseded_row_count": counters.superseded_row_count,
            "multi_row_task_count": counters.multi_row_task_count,
            "duplicate_actual_task_count": counters.duplicate_actual_task_count,
            "invalid_json_row_count": counters.invalid_json_row_count,
            "unparseable_actual_row_count": counters.unparseable_actual_row_count,
        }

    @staticmethod
    def unmapped_flags() -> List[str]:
        return [
            DataQualityFlag.UNMAPPED_ACTIVITY.value,
            DataQualityFlag.UNMAPPED_WBS.value,
            DataQualityFlag.UNMAPPED_DESCRIPTION.value,
            DataQualityFlag.UNMAPPED_CREW.value,
        ]
