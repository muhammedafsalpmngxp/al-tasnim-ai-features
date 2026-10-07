"""Builds the compact, deterministic evidence payload sent to the LLM.

Everything in the payload was computed by SQL and validation_service. The LLM
receives facts and classifications only -- never raw SQL, never the database,
and never anything it is expected to compute for itself.
"""

from __future__ import annotations

from collections import OrderedDict
from decimal import Decimal
from typing import Any, Dict, List, Optional

from app.config.settings import get_settings
from app.models.daily import DailyDataset, DailyTask, QuantityStatus
from app.services.grouping_service import GroupingService

#: How many individual tasks go into the evidence payload, by scope. A broad
#: scope ("day" and anything else not narrowed to one well or task) only ever
#: needs a handful of representative tasks -- the summary counts already cover
#: every task, so listing all of them again buys the explanation nothing and
#: only grows the request. This is what keeps a busy day's evidence small
#: enough for the provider to accept: a real day was observed returning
#: HTTP 413 (payload too large) at the old fixed limit of 40 tasks regardless
#: of scope.
_TASK_EVIDENCE_LIMIT_BY_SCOPE = {
    "task": 1,
    "well": 30,
}
_DEFAULT_TASK_EVIDENCE_LIMIT = 10


def _q(value: Optional[Decimal]) -> Optional[str]:
    if value is None:
        return None
    normalised = value.normalize()
    return format(normalised, "f")


def _representative_sample(tasks: List[DailyTask], limit: int) -> List[DailyTask]:
    """Order tasks for evidence so every distinct status present, and every
    distinct well within it, appears as early as possible -- then keep only
    the first ``limit``.

    Two real problems this fixes at once, both observed live against actual
    data:

    * ``daily_detail.sql`` orders rows by ``(uom_code, wbs, activity_code,
      well_id)``, so a plain prefix of a broad scope's task list clusters on
      whichever activity happens to sort first -- often just two or three
      wells out of dozens in scope. A day with 46 wells produced an
      explanation naming only 2-3 of them, reading as "about one well" even
      though the summary counts above it already covered all 46.
    * The same raw order mixes an ON_PLAN task next to a BELOW_PLAN task
      next to another ON_PLAN task with no structure at all, which made it
      easy for the model's prose to blend distinct statuses together rather
      than describing each one as its own group.

    The fix is one interleave, not two: status is the outer axis (so a
    handful of examples already touches every status present before any
    status gets a second well) and well is the inner axis within each status
    (so those examples are not all the same one or two wells). Applied
    whether or not truncation is actually needed, so even an untruncated
    scope's evidence is grouped by status rather than left in arbitrary SQL
    order -- ``limit`` just decides where it gets cut off.
    """
    per_status_wells: Dict[QuantityStatus, "OrderedDict[int, List[DailyTask]]"] = {}
    for task in tasks:
        by_well = per_status_wells.setdefault(task.quantity_status, OrderedDict())
        by_well.setdefault(task.well_id, []).append(task)

    # One column per status actually present, in QuantityStatus definition
    # order; each column holds that status's wells' task lists, in the
    # order each well was first seen.
    columns = [
        list(per_status_wells[status].values())
        for status in QuantityStatus
        if status in per_status_wells
    ]

    # Flatten into one ordered list of (status, well) buckets by reading the
    # columns row by row: every status's first well before any status's
    # second well. This single order is what makes both axes diverse from
    # the very first picks, however many rounds it then takes to reach
    # `limit`.
    buckets: List[List[DailyTask]] = []
    row = 0
    while any(row < len(column) for column in columns):
        for column in columns:
            if row < len(column):
                buckets.append(column[row])
        row += 1

    ordered: List[DailyTask] = []
    round_index = 0
    while len(ordered) < limit:
        progressed = False
        for bucket in buckets:
            if round_index < len(bucket):
                ordered.append(bucket[round_index])
                progressed = True
                if len(ordered) >= limit:
                    return ordered
        if not progressed:
            break
        round_index += 1
    return ordered


class EvidenceService:
    """Assembles scope-specific evidence for the explanation endpoint."""

    def __init__(self, grouping_service: Optional[GroupingService] = None) -> None:
        self._grouping = grouping_service or GroupingService()

    def build(
        self,
        dataset: DailyDataset,
        tasks: List[DailyTask],
        *,
        scope: str,
        uom: Optional[str] = None,
        wbs: Optional[str] = None,
        activity_code: Optional[str] = None,
        status: Optional[str] = None,
        well_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        settings = get_settings()

        status_counts = {member.value: 0 for member in QuantityStatus}
        planned_total: Optional[Decimal] = None
        actual_total: Optional[Decimal] = None
        uom_codes = {task.uom_code for task in tasks}
        wells = {task.well_id for task in tasks}
        quality_counts: Dict[str, int] = {}

        for task in tasks:
            status_counts[task.quantity_status.value] += 1
            if task.planned is not None:
                planned_total = (planned_total or Decimal(0)) + task.planned
            if task.actual_quantity is not None:
                actual_total = (actual_total or Decimal(0)) + task.actual_quantity
            for flag in task.data_quality_flags:
                quality_counts[flag.value] = quality_counts.get(flag.value, 0) + 1

        # Totals are only meaningful inside one unit of measure. Where the scope
        # spans several, the totals are withheld rather than added together.
        single_uom = len(uom_codes) == 1
        if not tasks:
            # A scope can be real and still have no entry for this date -- a
            # well is on the front page on a day it reported nothing, and its
            # task activity is separate evidence (see WellActivityService).
            #
            # This case gets a summary of its own rather than the usual one
            # filled with zeroes. A zeroed `well_count` and five zeroed status
            # counts are true but meaningless here, and the model read them
            # out loud: explaining one well, it opened with "there are 0 wells
            # and 0 tasks in scope", which describes the shape of the payload
            # rather than the well. There is exactly one fact to state, so the
            # payload now carries exactly that fact.
            summary: Dict[str, Any] = {
                "task_count": 0,
                "no_daily_entry": True,
                "note": (
                    "No daily task entry was recorded in this selection on this "
                    "report date. This is an absence of any entry, not a zero "
                    "quantity, and there is no quantity, status or count to "
                    "report for the date itself. It says nothing about what the "
                    "well's tasks are doing otherwise."
                ),
            }
        else:
            summary = {
                "well_count": len(wells),
                "task_count": len(tasks),
                "status_counts": status_counts,
                "planned_quantity": _q(planned_total) if single_uom else None,
                "actual_quantity": _q(actual_total) if single_uom else None,
                "quantities_summable": single_uom,
            }
        if tasks and not single_uom:
            summary["quantities_withheld_reason"] = (
                "This scope spans more than one unit of measure. No conversion "
                "between units is defined, so quantities are not totalled."
            )

        # settings.explain_max_wells is an operator-configurable ceiling; the
        # scope decides how far below it the payload actually needs to go.
        scope_limit = _TASK_EVIDENCE_LIMIT_BY_SCOPE.get(scope, _DEFAULT_TASK_EVIDENCE_LIMIT)
        limit = max(1, min(scope_limit, settings.explain_max_wells))
        included = _representative_sample(tasks, limit)

        payload: Dict[str, Any] = {
            "report_date": dataset.report_date.isoformat(),
            "scope": scope,
            # Named status first: it is the dashboard's top-level grouping, so
            # it is also the first thing an explanation should be anchored to.
            "group": {
                "status_filter": status,
                "wbs": wbs,
                "activity_code": activity_code,
                "well_id": well_id,
                "uom": uom if uom is not None else (next(iter(uom_codes)) if single_uom else None),
            },
            "summary": summary,
            "data_quality": {
                "flag_counts": quality_counts,
                "note": (
                    "Data-quality conditions are reported separately from the "
                    "quantity figures and do not change them."
                ),
            },
            # Listed in the order the dashboard groups by, so the explanation
            # and the screen describe the day in the same sequence.
            "status_definitions": {
                "ON_PLAN": "The reported actual quantity equals the planned quantity.",
                "ABOVE_PLAN": "The reported actual quantity is above the planned quantity. This is not an error.",
                "BELOW_PLAN": "The reported actual quantity is below the planned quantity. This is not, on its own, an error.",
                "NO_ACTUAL": "No actual quantity was reported for the task on this date.",
                "NOT_VALIDATED": "An actual quantity was reported but no planned quantity exists to compare it against.",
            },
<<<<<<< HEAD
=======
            "crew_match_definitions": {
                "MATCHED": "The crew type actually assigned to the task matches the crew the WBS/activity mapping expects.",
                "MISMATCHED": "The crew type actually assigned to the task does not match the crew the WBS/activity mapping expects.",
                "NOT_COMPARABLE": "No crew is recorded on the task, or no WBS crew is mapped, so no comparison could be made.",
            },
            "date_status_definitions": {
                "ON_TIME": "The reported actual date equals the planned date.",
                "EARLY": "The reported actual date is before the planned date. This is not an error.",
                "LATE": "The reported actual date is after the planned date. This is not, on its own, an error.",
                "NO_ACTUAL": "No actual date has been reported yet.",
                "NOT_VALIDATED": "An actual date was reported but no planned date exists to compare it against.",
            },
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
            "constraints": {
                "quantity_tolerance": "not defined in the business rules",
                "uom_conversion": "not defined in the business rules; never convert between units",
                "progress_unit": (
                    "not defined in the business rules; report the progress value "
                    "as given and do not describe it as a percentage or as a "
                    "degree of completion"
                ),
                "cause_of_difference": "not recorded anywhere in this evidence",
                "responsibility": "not recorded anywhere in this evidence",
            },
            "tasks": [self._task_evidence(task) for task in included],
        }
        if not tasks:
            # Nothing was reported, so there is no status to define and no
            # task to list. Both keys are dropped rather than sent empty: an
            # empty list invites a sentence about what is not there, and the
            # one fact worth stating is already in `summary`.
            payload.pop("status_definitions", None)
            payload.pop("tasks", None)
        if len(tasks) > len(included):
            payload["tasks_truncated"] = {
                "included": len(included),
                "total": len(tasks),
                "note": (
                    "A representative sample spanning every status and as many "
                    "distinct wells as possible is listed, not every task; the "
                    "summary counts above cover all of them."
                ),
            }
        return payload

    @staticmethod
    def _task_evidence(task: DailyTask) -> Dict[str, Any]:
        return {
            "well_id": task.well_id,
            "task_code": task.task_code,
            "activity_code": task.activity_code,
            "activity_description": task.activity_description,
            "wbs": task.wbs,
            "crew_code": task.crew_code,
<<<<<<< HEAD
=======
            "crew_type_code": task.crew_type_code,
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
            "uom": task.uom_code,
            "planned": _q(task.planned),
            "actual": _q(task.actual_quantity),
            "progress": _q(task.progress),
<<<<<<< HEAD
            "quantity_status": task.quantity_status.value,
            "mapping_status": task.mapping_status.value,
=======
            "planned_start": task.planned_start.isoformat() if task.planned_start else None,
            "planned_end": task.planned_end.isoformat() if task.planned_end else None,
            "actual_start": task.actual_start.isoformat() if task.actual_start else None,
            "actual_end": task.actual_end.isoformat() if task.actual_end else None,
            "quantity_status": task.quantity_status.value,
            "mapping_status": task.mapping_status.value,
            "crew_match_status": task.crew_match_status.value,
            "start_date_status": task.start_date_status.value,
            "end_date_status": task.end_date_status.value,
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
            "data_quality_flags": [flag.value for flag in task.data_quality_flags],
            "ph_name": task.ph_name,
            "daily_completed": task.daily_completed,
        }
