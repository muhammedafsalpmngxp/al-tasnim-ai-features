"""Daily Report's own capability catalogue -- CONTENT, not engine.

THIS MODULE IS THE ONLY PLACE DAILY REPORT'S BUSINESS CAPABILITIES ARE
DEFINED. Everything DYNAMIC_DB itself provides (the ``Capability``/
``Parameter`` shapes, the registry, the compile graph) is generic and knows
nothing about wells, tasks, WBS or crews -- that knowledge lives here, as
plain data, and is handed to the engine via
:func:`dynamic_db.capabilities.register`.

WHERE THE RULE TEXT COMES FROM. Each capability's ``rules`` callable slices
``business_rules.md`` / ``daily_report_rules.md`` using DYNAMIC_DB's generic,
reusable markdown-section slicer (:mod:`dynamic_db.rules`), by section
numbers this file declares. The engine itself never learns that two
documents exist, or what they are called -- it only ever sees the one string
each callable returns.

WHERE THE BASELINE SQL COMES FROM. Each capability's ``baseline_sql`` is the
already-loaded TEXT of the matching file in ``backend/sql/`` (with
``{{include:...}}`` directives already expanded by
:mod:`app.utils.sql_loader`). DYNAMIC_DB never reads a file from this
project's ``sql/`` directory itself -- it only ever receives the resulting
string.

CALL :func:`ensure_registered` ONCE, before the compiler service is first
used. It is idempotent -- re-registering merely replaces the same objects
with equivalent ones -- so it is safe to call from more than one entry point
(the FastAPI startup event, a CLI script, a test fixture).
"""

from __future__ import annotations

from typing import Tuple

import app._dynamic_db_path  # noqa: F401 - side effect: dynamic_db becomes importable
from dynamic_db import capabilities as dynamic_capabilities
from dynamic_db import rules as dynamic_rules
from dynamic_db.capabilities import PARAM_INT, PARAM_REPORT_DATE, Capability, Parameter

from app.config.settings import BACKEND_DIR
from app.utils.sql_loader import load_sql

_BUSINESS_RULES_PATH = str(BACKEND_DIR / "business_rules.md")
_DAILY_RULES_PATH = str(BACKEND_DIR / "daily_report_rules.md")

# Stable application-level capability identifiers -- unchanged from before
# the separation, so every existing caller (repositories, tests) keeps
# working against the same names.
DAILY_SUMMARY = "DAILY_SUMMARY"
DAILY_DETAILS = "DAILY_DETAILS"
WELL_ACTIVITY = "WELL_ACTIVITY"
WELL_DETAIL = "WELL_DETAIL"
MILESTONES = "MILESTONES"
CREW_SUGGESTION = "CREW_SUGGESTION"
ACTIVITY_DATES = "ACTIVITY_DATES"

ALL_CAPABILITY_IDS: Tuple[str, ...] = (
    DAILY_SUMMARY,
    DAILY_DETAILS,
    WELL_ACTIVITY,
    WELL_DETAIL,
    MILESTONES,
    CREW_SUGGESTION,
    ACTIVITY_DATES,
)

_REPORT_DATE = Parameter(
    "report_date", PARAM_REPORT_DATE, "the date the report is being produced for"
)


def _rules(business_sections: Tuple[int, ...], daily_sections: Tuple[int, ...]):
    """A zero-argument callable DYNAMIC_DB can call to get this capability's
    rule text -- built here, from THIS project's two documents, using
    DYNAMIC_DB's generic slicer. The engine never sees this function's body,
    only its result."""

    def _resolve() -> str:
        business = dynamic_rules.select(_BUSINESS_RULES_PATH, business_sections)
        daily = dynamic_rules.select(_DAILY_RULES_PATH, daily_sections)
        return dynamic_rules.combine(business, daily)

    return _resolve


def _baseline(filename: str) -> str:
    try:
        return load_sql(filename)
    except Exception:  # noqa: BLE001 - a missing baseline is reported, not fatal at import
        return ""


def _build() -> Tuple[Capability, ...]:
    return (
        Capability(
            id=DAILY_SUMMARY,
            intent=(
                "Describe the report date as a whole for live wells: how many raw daily "
                "entries existed, how many logical daily tasks they resolve to, and how "
                "many rows grain resolution set aside -- so the reduction is auditable "
                "rather than a silent shrink."
            ),
            concepts=(
                "live well",
                "daily task source",
                "report date filtering",
                "logical daily task grain",
                "actual quantity presence",
                "daily entry payload validity",
                "unparseable actual quantity",
                "duplicate actual entry",
                "multiple task rows",
            ),
            required_columns=(
                "raw_row_count",
                "raw_well_count",
                "raw_actual_entry_count",
                "invalid_json_row_count",
                "unparseable_actual_row_count",
                "logical_task_count",
                "multi_row_task_count",
                "duplicate_actual_task_count",
                "superseded_row_count",
            ),
            parameters=(_REPORT_DATE,),
            baseline_sql=_baseline("daily_summary"),
            rules=_rules((7,), (1, 3, 6)),
            single_row=True,
        ),
        Capability(
            id=DAILY_DETAILS,
            intent=(
                "One authoritative row per logical daily task on the report date, for "
                "live wells, carrying the activity/WBS/crew mapping chain, the unit of "
                "measure, the planned and actual quantities and the data-quality "
                "evidence -- but classifying nothing: quantity status and mapping "
                "status are decided once, in Python."
            ),
            concepts=(
                "live well",
                "daily task source",
                "report date filtering",
                "logical daily task grain",
                "actual quantity",
                "planned quantity",
                "progress as stored",
                "unit of measure",
                "activity identifier derived from the task code",
                "activity code mapping",
                "WBS",
                "crew code",
                "reference unit of measure from the activity master",
                "crew instance, type, supervisor and members",
                "data-quality evidence",
            ),
            required_columns=(
                "task_daily_id", "well_id", "action_on", "schedule_id", "task_code",
                "activity_id", "activity_code", "activity_description", "wbs", "crew_code",
                "uom_id", "uom_code", "activity_uom", "crew_id", "crew_type_id",
                "crew_type_name", "crew_instance_code", "crew_supervisor",
                "crew_employee_names", "planned", "progress", "actual_quantity",
                "actual_quantity_raw", "daily_completed", "ph_name", "is_actual_entry",
                "daily_data_json_valid", "daily_data_json_invalid",
                "actual_quantity_unparseable", "group_row_count", "group_actual_entry_count",
            ),
            parameters=(_REPORT_DATE,),
            baseline_sql=_baseline("daily_detail"),
            rules=_rules((7, 8), (1, 2, 3, 5, 6)),
        ),
        Capability(
            id=WELL_ACTIVITY,
            intent=(
                "Per live well, where its tasks stand as of the report date: the latest "
                "state of each logical task, counted into disjoint figures so that "
                "open = incomplete + ongoing and logical = open + completed."
            ),
            concepts=(
                "live well",
                "daily task source",
                "logical task identity across dates",
                "latest task state on or before the report date",
                "task state",
                "completed task",
                "ongoing task",
                "not started task",
                "ended without completion",
                "last task date",
            ),
            required_columns=(
                "well_id", "logical_task_count", "completed_task_count", "open_task_count",
                "incomplete_task_count", "ongoing_task_count", "not_started_task_count",
                "ended_not_completed_task_count", "last_task_date",
            ),
            parameters=(_REPORT_DATE,),
            baseline_sql=_baseline("well_task_activity"),
            rules=_rules((7,), (9,)),
        ),
        Capability(
            id=WELL_DETAIL,
            intent=(
                "The individual not-completed logical tasks behind the per-well counts, "
                "for every live well at once, so every figure on the front page can be "
                "checked against the rows it counted."
            ),
            concepts=(
                "live well",
                "logical task identity across dates",
                "latest task state on or before the report date",
                "task state",
                "activity identifier derived from the task code",
                "activity code mapping",
                "WBS",
            ),
            required_columns=(
                "well_id", "schedule_id", "task_code", "task_state", "latest_action_on",
                "actual_start", "actual_end", "completed", "activity_id", "activity_code",
                "activity_description", "wbs",
            ),
            parameters=(_REPORT_DATE,),
            baseline_sql=_baseline("well_task_activity_detail"),
            rules=_rules((7,), (2, 9)),
        ),
        Capability(
            id=MILESTONES,
            intent=(
                "For every live well, one row per lifecycle milestone not yet reached "
                "whose deadline is computable -- raw evidence only. What counts as "
                "'near' a deadline is a presentation threshold owned by the service "
                "layer and must not be decided here."
            ),
            concepts=(
                "live well",
                "well completion",
                "pegging sheet date",
                "FLAF date",
                "expected rig-on date",
                "actual rig-on date",
                "expected rig-off date",
                "actual rig-off date",
                "milestone deadline derived from the master date",
            ),
            required_columns=(
                "well_id", "milestone", "deadline_date", "pegged_date", "flaf_issue_date",
                "ex_rig_on_date", "rig_on_date", "ex_rig_off_date", "rig_off_date",
            ),
            parameters=(),
            baseline_sql=_baseline("well_milestones"),
            rules=_rules((2, 3, 4, 7, 9, 10), ()),
            requires_grain_resolution=False,
        ),
        Capability(
            id=CREW_SUGGESTION,
            intent=(
                "For ONE task on one well as of the report date: whether a replacement "
                "crew suggestion is even eligible, what crew is already recorded, and "
                "the single top-ranked historically proven crew with the evidence behind "
                "it. Advisory only, ranked deterministically, never an assignment."
            ),
            concepts=(
                "live well",
                "logical task identity across dates",
                "latest task state on or before the report date",
                "activity identifier derived from the task code",
                "activity code mapping",
                "WBS",
                "crew code",
                "historical task-level completion by a crew",
                "report-date-respecting history",
                "crew suggestion eligibility",
                "derived crew availability",
                "median and mean historical duration",
                "evidence strength banding",
                "deterministic candidate ranking",
            ),
            required_columns=(
                "target_well_id", "target_task_code", "target_schedule_id",
                "target_action_date", "activity_id", "activity_code", "wbs",
                "wbs_crew_code", "target_completed", "target_progress",
                "target_actual_start", "target_actual_end", "well_status",
                "crew_suggestion_eligible", "crew_suggestion_suppression_reason",
                "crew_suggestion_suppression_code", "current_crew_id", "current_crew_type",
                "current_crew_instance_code", "current_crew_supervisor", "candidate_crew_id",
                "candidate_crew_type", "candidate_crew_instance_code",
                "candidate_crew_supervisor", "historical_completed_task_count",
                "distinct_completed_well_count", "completed_on_incomplete_well_count",
                "completed_on_completed_well_count", "typical_completion_days",
                "average_completion_days", "shortest_completion_days",
                "longest_completion_days", "most_recent_success_date", "evidence_strength",
                "derived_availability", "historical_crew_candidate_count",
                "available_crew_candidate_count",
            ),
            parameters=(
                _REPORT_DATE,
                Parameter("well_id", PARAM_INT, "the well the target task belongs to"),
                Parameter("task_code", "text", "the target task's own code"),
            ),
            baseline_sql=_baseline("crew_suggestion"),
            rules=_rules((7, 8), (1, 2, 8)),
            probe_params_from=DAILY_DETAILS,
            probe_param_columns=("well_id", "task_code"),
        ),
        Capability(
            id=ACTIVITY_DATES,
            intent=(
                "The most recent report dates on which a live well actually reported an "
                "actual quantity, newest first, with how many raw entries and how many "
                "wells each of those dates holds. It is the date picker's list."
            ),
            concepts=(
                "live well",
                "daily task source",
                "actual quantity presence",
                "daily entry payload validity",
            ),
            required_columns=("report_date", "row_count", "well_count"),
            parameters=(
                Parameter("limit", PARAM_INT, "how many dates to return, newest first", probe_default=5),
            ),
            baseline_sql=_baseline("daily_activity_dates"),
            rules=_rules((7,), (1,)),
        ),
    )


_registered = False


def ensure_registered() -> None:
    """Register every Daily Report capability into DYNAMIC_DB's engine.

    Idempotent -- safe to call from more than one entry point. Re-registering
    is cheap: it does not re-read any file eagerly beyond what already
    happened at import time (``_baseline`` calls above run once, at module
    import), so calling this again just re-installs the same objects.
    """
    global _registered
    for capability in _build():
        dynamic_capabilities.register(capability)
    _registered = True


def is_registered() -> bool:
    return _registered
