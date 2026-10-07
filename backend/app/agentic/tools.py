"""The agents' tools: every one a fixed, read-only, deterministic service call.

THE BOUNDARY. An agent chooses WHICH tool to call and with WHAT arguments; the
tool alone decides what comes back. No tool accepts SQL, a column name or a
formula from a model; every figure a tool returns was computed by the same
SQL/Python the classic dashboard uses (``app.services.*``). A tool that fails
returns its error as data, so one unavailable source never ends a run.

Arguments are validated here, not trusted: a well id must be an integer, a
limit is clamped, an unknown filter is refused. A planner that asks for
something outside a tool's contract gets a refusal it can read, never a guess.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Dict, List, Optional

from app.api import dependencies
from app.config.settings import get_settings
from app.models.daily import QuantityStatus
from app.services.explain_evidence import ScopeRequest, build_scope_evidence

logger = logging.getLogger(__name__)


class ToolError(ValueError):
    """A tool was called outside its contract. The message is shown to the planner."""


@dataclass
class ToolResult:
    tool: str
    args: Dict[str, Any]
    ok: bool
    result: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    #: Seconds the tool took.
    duration_s: float = 0.0
    #: Audit material for the panel only -- never part of what a model sees.
    audit: Dict[str, Any] = field(default_factory=dict)

    def for_model(self) -> Dict[str, Any]:
        if not self.ok:
            return {"tool": self.tool, "args": self.args, "error": self.error}
        return {"tool": self.tool, "args": self.args, "result": self.result}


@dataclass
class Tool:
    name: str
    description: str
    #: argument -> plain description, as the planner sees it.
    params: Dict[str, str]
    fn: Callable[..., Dict[str, Any]]


# ---------------------------------------------------------------------------
# Argument helpers
# ---------------------------------------------------------------------------
def _int(args: Dict[str, Any], name: str, *, required: bool = True) -> Optional[int]:
    value = args.get(name)
    if value is None or value == "":
        if required:
            raise ToolError(f"'{name}' is required")
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ToolError(f"'{name}' must be a whole number, got {value!r}") from None


def _limit(args: Dict[str, Any], default: int, maximum: int) -> int:
    value = _int(args, "limit", required=False)
    return max(1, min(value or default, maximum))


def _dataset(report_date: date):
    return dependencies.load_dataset(dependencies.get_daily_service(), report_date)


def _scope(report_date: date, **fields: Any):
    request = ScopeRequest(report_date=report_date, **fields)
    return build_scope_evidence(
        request,
        _dataset(report_date),
        service=dependencies.get_daily_service(),
        evidence_service=dependencies.get_evidence_service(),
        crew_suggestion_service=dependencies.get_crew_suggestion_service(),
        activity_service=dependencies.get_well_activity_service(),
    )


def _scope_result(resolved) -> Dict[str, Any]:
    if not resolved.available:
        return {"available": False, "note": "No daily task record or task activity for this selection."}
    return {"available": True, "evidence": resolved.llm_evidence}


def _scope_audit(resolved) -> Dict[str, Any]:
    return {
        "evidence": resolved.evidence,
        "withheld": resolved.withheld,
        "sql_sources": resolved.sql_sources,
        "proof": resolved.proof,
        "proof_note": resolved.proof_note,
    }


# ---------------------------------------------------------------------------
# The tools
# ---------------------------------------------------------------------------
def day_overview(report_date: date, args: Dict[str, Any]):
    resolved = _scope(report_date, scope="day")
    return _scope_result(resolved), _scope_audit(resolved)


def well_overview(report_date: date, args: Dict[str, Any]):
    well_id = _int(args, "well_id")
    resolved = _scope(report_date, scope="well", well_id=well_id)
    return _scope_result(resolved), _scope_audit(resolved)


def task_detail(report_date: date, args: Dict[str, Any]):
    task_daily_id = _int(args, "task_daily_id")
    well_id = _int(args, "well_id", required=False)
    resolved = _scope(report_date, scope="task", task_daily_id=task_daily_id, well_id=well_id)
    return _scope_result(resolved), _scope_audit(resolved)


_WELL_FILTERS = {"reported", "open_work", "no_report_with_open_work", "all"}


def list_wells(report_date: date, args: Dict[str, Any]):
    """Wells with their figures as of the date, busiest first."""
    which = str(args.get("filter") or "open_work").strip().lower()
    status = str(args.get("status") or "").strip().upper() or None
    if which not in _WELL_FILTERS:
        raise ToolError(f"'filter' must be one of {sorted(_WELL_FILTERS)}")
    if status and status not in {member.value for member in QuantityStatus}:
        raise ToolError(f"'status' must be one of {[m.value for m in QuantityStatus]}")
    limit = _limit(args, 15, 50)

    dataset = _dataset(report_date)
    activity = dependencies.get_well_activity_service().wells_for_date(report_date, dataset)
    status_by_well: Dict[int, Dict[str, int]] = {}
    for task in dataset.tasks:
        counts = status_by_well.setdefault(task.well_id, {})
        counts[task.quantity_status.value] = counts.get(task.quantity_status.value, 0) + 1

    rows = []
    for well in activity:
        reported = well.today_reported_task_count
        if which == "reported" and not reported:
            continue
        if which == "open_work" and not well.open_task_count:
            continue
        if which == "no_report_with_open_work" and (reported or not well.open_task_count):
            continue
        statuses = status_by_well.get(well.well_id, {})
        if status and not statuses.get(status):
            continue
        rows.append(
            {
                "well_id": well.well_id,
                "reported_task_count_on_report_date": reported,
                "open_task_count": well.open_task_count,
                "incomplete_task_count": well.incomplete_task_count,
                "ongoing_task_count": well.ongoing_task_count,
                "last_task_date": well.last_task_date,
                "status_counts_on_report_date": statuses,
            }
        )
    rows.sort(key=lambda r: (-r["reported_task_count_on_report_date"], -r["open_task_count"], r["well_id"]))
    return {
        "filter": which,
        "status": status,
        "matching_well_count": len(rows),
        "wells": rows[:limit],
        "shown": min(limit, len(rows)),
    }, {}


def milestones(report_date: date, args: Dict[str, Any]):
    """Lifecycle deadlines, evaluated against TODAY (business_rules.md section 3)."""
    settings = get_settings()
    window = _int(args, "window_days", required=False)
    window = settings.milestone_priority_window_days if window is None else max(0, min(window, 60))
    well_id = _int(args, "well_id", required=False)
    limit = _limit(args, 20, 50)
    upcoming, overdue = dependencies.get_milestone_service().upcoming_and_overdue(window)
    if well_id is not None:
        upcoming = [a for a in upcoming if a.well_id == well_id]
        overdue = [a for a in overdue if a.well_id == well_id]

    def out(alert):
        return {
            "well_id": alert.well_id,
            "milestone": alert.label,
            "deadline_date": alert.deadline_date,
            "days_remaining": alert.days_remaining,
        }

    return {
        "evaluated_against": "today",
        "today": date.today(),
        "window_days": window,
        "upcoming_count": len(upcoming),
        "upcoming": [out(a) for a in upcoming[:limit]],
        "overdue_count": len(overdue),
        "overdue": [out(a) for a in overdue[:limit]],
    }, {}


def deadline_pressure(report_date: date, args: Dict[str, Any]):
    """Specialist: wells with open work before an upcoming deadline, or open
    work past an overdue one. A deterministic join of two existing results --
    the milestone deadlines and the well's open-task counts -- with the rule
    stated in the agentic amendment to daily_report_rules.md section 9 #11.
    It never says a task is late, delayed or at risk."""
    settings = get_settings()
    window = settings.agent_deadline_window_days
    limit = _limit(args, 15, 50)
    upcoming, overdue = dependencies.get_milestone_service().upcoming_and_overdue(window)
    dataset = _dataset(report_date)
    open_by_well = {
        well.well_id: well
        for well in dependencies.get_well_activity_service().wells_for_date(report_date, dataset)
    }

    def rows(alerts, condition):
        found = []
        for alert in alerts:
            well = open_by_well.get(alert.well_id)
            if well is None or not well.open_task_count:
                continue
            found.append(
                {
                    "well_id": alert.well_id,
                    "milestone": alert.label,
                    "deadline_date": alert.deadline_date,
                    "days_remaining": alert.days_remaining,
                    "open_task_count": well.open_task_count,
                    "ongoing_task_count": well.ongoing_task_count,
                    "incomplete_task_count": well.incomplete_task_count,
                    "condition": condition,
                }
            )
        return found

    before = rows(upcoming, "OPEN_WORK_BEFORE_UPCOMING_DEADLINE")
    past = rows(overdue, "OPEN_WORK_PAST_OVERDUE_DEADLINE")
    before.sort(key=lambda r: (r["days_remaining"], -r["open_task_count"]))
    past.sort(key=lambda r: (-r["open_task_count"], r["days_remaining"]))
    return {
        "rule": (
            "A well is listed when it has at least one open task and a milestone deadline "
            f"within {window} day(s) of today (or already passed). This is a statement of "
            "open work and a date, not a judgement about any task."
        ),
        "window_days": window,
        "open_work_before_upcoming_deadline_count": len(before),
        "open_work_before_upcoming_deadline": before[:limit],
        "open_work_past_overdue_deadline_count": len(past),
        "open_work_past_overdue_deadline": past[:limit],
    }, {}


def crew_options(report_date: date, args: Dict[str, Any]):
    """Specialist: the crew evidence for one task, exactly as SQL ranked it."""
    task_daily_id = _int(args, "task_daily_id")
    dataset = _dataset(report_date)
    task = next((t for t in dataset.tasks if t.task_daily_id == task_daily_id), None)
    if task is None:
        raise ToolError(f"no task with task_daily_id {task_daily_id} on {report_date}")
    suggestion = dependencies.get_crew_suggestion_service().build(
        well_id=task.well_id, task_code=task.task_code, report_date=report_date
    )
    return {
        "well_id": task.well_id,
        "task_code": task.task_code,
        "activity_description": task.activity_description,
        "crew_suggestion": suggestion,
        "advisory_only": True,
    }, {}


def data_quality(report_date: date, args: Dict[str, Any]):
    """Specialist: the day's data-quality flags, grouped. Counts only; the
    flags themselves were raised by validation_service."""
    limit = _limit(args, 10, 30)
    dataset = _dataset(report_date)
    by_flag: Dict[str, Dict[str, Any]] = {}
    for task in dataset.tasks:
        for flag in task.data_quality_flags:
            entry = by_flag.setdefault(flag.value, {"task_count": 0, "wells": set(), "activities": {}})
            entry["task_count"] += 1
            entry["wells"].add(task.well_id)
            label = task.activity_description or task.task_code or "unknown"
            entry["activities"][label] = entry["activities"].get(label, 0) + 1
    flags = []
    for name, entry in sorted(by_flag.items(), key=lambda kv: -kv[1]["task_count"]):
        top = sorted(entry["activities"].items(), key=lambda kv: -kv[1])[:limit]
        flags.append(
            {
                "flag": name,
                "task_count": entry["task_count"],
                "well_count": len(entry["wells"]),
                "most_affected": [{"activity": a, "task_count": c} for a, c in top],
            }
        )
    return {
        "report_date": report_date,
        "reported_task_count": len(dataset.tasks),
        "flag_count": len(flags),
        "flags": flags,
        "note": "Data-quality flags never change a quantity or a status.",
    }, {}


TOOLS: Dict[str, Tool] = {
    tool.name: tool
    for tool in (
        Tool("day_overview", "The whole day: what was reported on the date and where every live "
             "well's open work stands as of it.", {}, day_overview),
        Tool("well_overview", "One well: its tasks on the date (if any) and its open, incomplete "
             "and ongoing work as of the date.", {"well_id": "integer well id"}, well_overview),
        Tool("task_detail", "One reported task on the date, with its crew evidence.",
             {"task_daily_id": "integer", "well_id": "integer, optional"}, task_detail),
        Tool("list_wells", "Wells with their figures, busiest first.",
             {"filter": "reported | open_work | no_report_with_open_work | all",
              "status": "optional: ON_PLAN | ABOVE_PLAN | BELOW_PLAN | NO_ACTUAL | NOT_VALIDATED",
              "limit": "integer, default 15, max 50"}, list_wells),
        Tool("milestones", "Pegging / FLAF / rig-on / rig-off deadlines, evaluated against today.",
             {"window_days": "integer, optional", "well_id": "integer, optional",
              "limit": "integer, optional"}, milestones),
        Tool("deadline_pressure", "Wells that still have open tasks with a milestone deadline "
             "coming up or already passed.", {"limit": "integer, optional"}, deadline_pressure),
        Tool("crew_options", "Crew evidence for one task: the crew on it and, when SQL found one, "
             "a crew with a proven history on the same activity. Advisory only.",
             {"task_daily_id": "integer"}, crew_options),
        Tool("data_quality", "The day's data-quality flags, grouped by flag and activity.",
             {"limit": "integer, optional"}, data_quality),
    )
}


def catalog() -> List[Dict[str, Any]]:
    """What the planner is shown: names, descriptions and argument meanings."""
    return [
        {"name": tool.name, "description": tool.description, "args": tool.params}
        for tool in TOOLS.values()
    ]


def run_tool(name: str, report_date: date, args: Optional[Dict[str, Any]] = None) -> ToolResult:
    """Run one tool. Never raises: a refusal or a failure comes back as data."""
    args = dict(args or {})
    started = time.perf_counter()
    tool = TOOLS.get(name)
    if tool is None:
        return ToolResult(name, args, ok=False, error=f"unknown tool {name!r}")
    try:
        result, audit = tool.fn(report_date, args)
        return ToolResult(
            name, args, ok=True, result=result, audit=audit,
            duration_s=time.perf_counter() - started,
        )
    except ToolError as exc:
        return ToolResult(name, args, ok=False, error=str(exc),
                          duration_s=time.perf_counter() - started)
    except Exception as exc:  # noqa: BLE001 - one failed source must not end a run
        logger.exception("agent tool %s failed", name)
        detail = getattr(exc, "detail", None) or type(exc).__name__
        return ToolResult(name, args, ok=False, error=f"the tool failed: {detail}",
                          duration_s=time.perf_counter() - started)
