"""Deterministic validation: the single source of the daily classification rules.

Nothing else in this application classifies a daily entry. React displays what
this module decides; the LLM is told to repeat it verbatim.

What is deliberately NOT implemented here
-----------------------------------------
daily_report_rules.md §7 does not define any of the following, so none of them
are invented here (see README, "Unresolved business questions"):

* a quantity tolerance -- so ``actual`` is compared to ``planned`` exactly, and
  no "close enough" band exists;
* whether a quantity difference is an error -- so ABOVE_PLAN and BELOW_PLAN are
  descriptive facts, never error states;
* any UOM conversion -- quantities are never converted between units, and tasks
  in different UOM are never summed together;
* the meaning of ``task_daily.required`` -- the column is not read anywhere.

A percentage variance is NOT computed when planned is zero or missing.

(A well's or a construction phase's actual completion date is a separate,
well-lifecycle question -- see business_rules.md, not this module.)
"""

from __future__ import annotations

import re
<<<<<<< HEAD
=======
from datetime import date
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from app.models.daily import (
<<<<<<< HEAD
    DailyTask,
    DataQualityFlag,
=======
    CrewMatchStatus,
    DailyTask,
    DataQualityFlag,
    DateStatus,
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
    MappingStatus,
    QuantityStatus,
)


def classify_quantity(
    planned: Optional[Decimal], actual: Optional[Decimal]
) -> QuantityStatus:
    """Classify a daily entry's reported quantity against its planned quantity.

    The rule, in full:

    ==============================  ==================
    condition                       status
    ==============================  ==================
    actual is NULL                  ``NO_ACTUAL``
    actual == planned               ``ON_PLAN``
    actual > planned                ``ABOVE_PLAN``
    actual < planned                ``BELOW_PLAN``
    actual present, planned is NULL ``NOT_VALIDATED``
    ==============================  ==================

    ``ABOVE_PLAN`` is not an error: it states that the reported actual is above
    the planned quantity. ``planned = 0`` with ``actual > 0`` is likewise not an
    error; it is simply ``ABOVE_PLAN``.

    A missing ``planned`` is NOT treated as zero -- that would be an invented
    rule, and it would turn every such task into a false ABOVE_PLAN. There is no
    planned quantity to compare against, daily_report_rules.md does not say how
    to classify that case, so it is exposed as ``NOT_VALIDATED`` and carries the
    ``MISSING_PLANNED`` data-quality flag. ``NO_ACTUAL`` still wins when there
    is no reported actual at all, because that judgement needs no comparison.
    """
    if actual is None:
        return QuantityStatus.NO_ACTUAL
    if planned is None:
        return QuantityStatus.NOT_VALIDATED
    if actual == planned:
        return QuantityStatus.ON_PLAN
    if actual > planned:
        return QuantityStatus.ABOVE_PLAN
    return QuantityStatus.BELOW_PLAN


def classify_mapping(
    activity_id: Optional[str],
    activity_code: Optional[str],
    activity_description: Optional[str],
    wbs: Optional[str],
    crew_code: Optional[str],
) -> MappingStatus:
    """Report how far the activity mapping chain resolved.

    Precedence runs down the chain: a task with no ``activity_code`` cannot have
    a description, a WBS or a crew, so it is reported as ``UNMAPPED_ACTIVITY``
    rather than as four separate failures. Missing information stays missing --
    nothing is substituted.
    """
    if not _has_text(activity_code):
        return MappingStatus.UNMAPPED_ACTIVITY
    if not _has_text(wbs):
        return MappingStatus.UNMAPPED_WBS
    if not _has_text(activity_description):
        return MappingStatus.UNMAPPED_DESCRIPTION
    if not _has_text(crew_code):
        return MappingStatus.UNMAPPED_CREW
    return MappingStatus.MAPPED


<<<<<<< HEAD
=======
def classify_crew_match(
    crew_code: Optional[str], crew_type_code: Optional[str]
) -> CrewMatchStatus:
    """Compare the WBS-planned crew against the crew actually assigned.

    ``crew_code`` (``activity_master_csv.crew_code``) and ``crew_type_code``
    (``ref.crew_type.crew_type_code``, resolved from ``task_daily.crew_id``)
    are two independently-maintained columns that happen to share one code
    space -- measured, on rows where both resolve, 98.4% exact-string
    agreement. That is what makes a direct comparison meaningful rather than
    invented; it is never extended to any other pair of fields.

    Comparison is case-insensitive and trims whitespace (the same tolerance
    ``_clean`` already gives every other text field here), but performs no
    other normalisation -- the measured 98.4% agreement rate was exact-match,
    so no hyphen/format rewriting is needed to make this comparison fair.

    Either side missing -- no crew recorded on the task at all, or no WBS
    crew mapped -- is reported as ``NOT_COMPARABLE``, never guessed into a
    match or a mismatch.
    """
    if not _has_text(crew_code) or not _has_text(crew_type_code):
        return CrewMatchStatus.NOT_COMPARABLE
    if crew_code.strip().casefold() == crew_type_code.strip().casefold():
        return CrewMatchStatus.MATCHED
    return CrewMatchStatus.MISMATCHED


def classify_date(planned: Optional[date], actual: Optional[date]) -> DateStatus:
    """Classify one reported actual date against its planned counterpart.

    Same shape as :func:`classify_quantity`, deliberately: ``NO_ACTUAL`` wins
    over ``NOT_VALIDATED`` when there is no reported date at all, and being
    early is a descriptive fact (``EARLY``), never an error -- consistent with
    business_rules.md section 4 ("being EARLY is not an anomaly"). A missing
    planned date is exposed as ``NOT_VALIDATED`` rather than treated as a
    match or a difference; this has not been observed in practice (the
    planned columns are populated on every row measured) but is not assumed.
    """
    if actual is None:
        return DateStatus.NO_ACTUAL
    if planned is None:
        return DateStatus.NOT_VALIDATED
    if actual == planned:
        return DateStatus.ON_TIME
    if actual < planned:
        return DateStatus.EARLY
    return DateStatus.LATE


>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
def collect_data_quality_flags(row: Dict[str, Any], task: DailyTask) -> List[DataQualityFlag]:
    """Every data-quality condition visible on one resolved daily row.

    Data-quality problems are tracked separately from the operational quantity
    status so a mapping gap never distorts a planned or actual figure.
    """
    flags: List[DataQualityFlag] = []

    if task.mapping_status is MappingStatus.UNMAPPED_ACTIVITY:
        flags.append(DataQualityFlag.UNMAPPED_ACTIVITY)
    else:
        if not _has_text(task.wbs):
            flags.append(DataQualityFlag.UNMAPPED_WBS)
        if not _has_text(task.activity_description):
            flags.append(DataQualityFlag.UNMAPPED_DESCRIPTION)
        if not _has_text(task.crew_code):
            flags.append(DataQualityFlag.UNMAPPED_CREW)

    if not _has_text(task.task_code) or not _has_text(task.activity_id):
        flags.append(DataQualityFlag.MALFORMED_TASK_CODE)

    if not _has_text(task.uom_code):
        flags.append(DataQualityFlag.MISSING_UOM)

    if int(row.get("daily_data_json_invalid") or 0) == 1:
        flags.append(DataQualityFlag.INVALID_DAILY_JSON)

    if int(row.get("actual_quantity_unparseable") or 0) == 1:
        flags.append(DataQualityFlag.UNPARSEABLE_ACTUAL_QUANTITY)

    if int(row.get("group_actual_entry_count") or 0) > 1:
        flags.append(DataQualityFlag.DUPLICATE_ACTUAL_ENTRY)
    elif int(row.get("group_row_count") or 1) > 1:
        flags.append(DataQualityFlag.MULTIPLE_TASK_ROWS)

    if task.planned is None:
        flags.append(DataQualityFlag.MISSING_PLANNED)

<<<<<<< HEAD
=======
    if (
        task.actual_start is not None
        and task.actual_end is not None
        and task.actual_end < task.actual_start
    ):
        flags.append(DataQualityFlag.INVALID_DATE_SEQUENCE)

>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
    return flags


def build_task(row: Dict[str, Any]) -> DailyTask:
    """Turn one resolved SQL row into a classified :class:`DailyTask`."""
    planned = _to_decimal(row.get("planned"))
    actual = _to_decimal(row.get("actual_quantity"))
    activity_id = _clean(row.get("activity_id"))
    activity_code = _clean(row.get("activity_code"))
    activity_description = _clean(row.get("activity_description"))
    wbs = _clean(row.get("wbs"))
    crew_code = _clean(row.get("crew_code"))
<<<<<<< HEAD
=======
    crew_type_code = _clean(row.get("crew_type_code"))
    planned_start = row.get("planned_start")
    planned_end = row.get("planned_end")
    actual_start = row.get("actual_start")
    actual_end = row.get("actual_end")
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)

    task = DailyTask(
        task_daily_id=int(row["task_daily_id"]),
        well_id=int(row["well_id"]),
        action_on=row["action_on"],
        schedule_id=_to_int(row.get("schedule_id")),
        task_code=_clean(row.get("task_code")),
        activity_id=activity_id,
        activity_code=activity_code,
        activity_description=activity_description,
        wbs=wbs,
        crew_code=crew_code,
        uom_id=_to_int(row.get("uom_id")),
        uom_code=_clean(row.get("uom_code")),
        activity_uom=_clean(row.get("activity_uom")),
        crew_id=_to_int(row.get("crew_id")),
        crew_type_id=_to_int(row.get("crew_type_id")),
        crew_type_name=_clean(row.get("crew_type_name")),
<<<<<<< HEAD
        crew_instance_code=_clean(row.get("crew_instance_code")),
        crew_supervisor=_clean(row.get("crew_supervisor")),
        crew_employees=_to_name_list(row.get("crew_employee_names")),
=======
        crew_type_code=crew_type_code,
        crew_instance_code=_clean(row.get("crew_instance_code")),
        crew_supervisor=_clean(row.get("crew_supervisor")),
        crew_employees=_to_name_list(row.get("crew_employee_names")),
        planned_start=planned_start,
        planned_end=planned_end,
        actual_start=actual_start,
        actual_end=actual_end,
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
        planned=planned,
        progress=_to_decimal(row.get("progress")),
        actual_quantity=actual,
        actual_quantity_raw=_clean(row.get("actual_quantity_raw")),
        daily_completed=_to_bool(row.get("daily_completed")),
        ph_name=_clean(row.get("ph_name")),
        quantity_status=classify_quantity(planned, actual),
        mapping_status=classify_mapping(
            activity_id, activity_code, activity_description, wbs, crew_code
        ),
<<<<<<< HEAD
=======
        crew_match_status=classify_crew_match(crew_code, crew_type_code),
        start_date_status=classify_date(planned_start, actual_start),
        end_date_status=classify_date(planned_end, actual_end),
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
        group_row_count=int(row.get("group_row_count") or 1),
        group_actual_entry_count=int(row.get("group_actual_entry_count") or 0),
    )
    task.data_quality_flags = collect_data_quality_flags(row, task)
    return task


# ---------------------------------------------------------------------------
# conversion helpers -- tolerant of one malformed value, never of a silent one
# ---------------------------------------------------------------------------


def _has_text(value: Optional[str]) -> bool:
    return value is not None and str(value).strip() != ""


def _clean(value: Any) -> Optional[str]:
    """Trim and collapse internal whitespace; empty becomes None.

    Some ``activity_group_description`` values carry embedded line breaks from
    the source spreadsheet. Collapsing runs of whitespace only affects layout --
    it never changes which WBS a task belongs to -- and it keeps one WBS from
    appearing as two differently-wrapped groups.
    """
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _to_decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        # A single malformed value must not break the report.
        return None


def _to_name_list(value: Any) -> List[str]:
    """Split a comma-joined STRING_AGG result of employee names into a list."""
    if value is None:
        return []
    return [name.strip() for name in str(value).split(",") if name.strip()]


def _to_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _to_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None
