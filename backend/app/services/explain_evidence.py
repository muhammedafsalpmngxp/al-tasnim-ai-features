"""The deterministic evidence for one explanation scope -- shared by the classic
``/api/daily/explain`` endpoint and by the agents' tools.

Everything here is SQL/Python. It decides what the model may be told about a
day, a slice of it, one well or one task; it never calls a model. Keeping it in
one place is what guarantees an agent's well summary and the classic well
summary are built from byte-identical evidence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

from app.models.daily import DailyTask
from app.services.crew_suggestion_service import CrewSuggestionService
from app.services.daily_service import DailyService
from app.services.evidence_service import EvidenceService
from app.services.well_activity_service import WellActivityService

logger = logging.getLogger(__name__)

VALID_SCOPES = {"day", "uom", "group", "status", "well", "task"}

#: How many proof rows travel to the panel. A well's incomplete tasks run to
#: a few dozen at most on this data, so this only bounds a pathological case
#: -- and when it bites, the response says so rather than quietly truncating.
PROOF_ROW_LIMIT = 300


@dataclass
class ScopeRequest:
    report_date: date
    scope: str = "day"
    uom: Optional[str] = None
    wbs: Optional[str] = None
    activity_code: Optional[str] = None
    status: Optional[str] = None
    well_id: Optional[int] = None
    task_daily_id: Optional[int] = None

    def is_filtered(self) -> bool:
        """True when a "day" request is really a slice of the day.

        The well-universe overview belongs to the whole view only. A request
        narrowed to one status, WBS, activity, unit or well is describing a
        subset of the day's reported tasks, and a count of every live well's
        open work alongside it would be a different population than the one
        being explained.
        """
        return any(
            value is not None
            for value in (
                self.uom, self.wbs, self.activity_code, self.status,
                self.well_id, self.task_daily_id,
            )
        )

    def is_front_page_day(self) -> bool:
        return self.scope == "day" and not self.is_filtered()


@dataclass
class ScopeEvidence:
    """What one scope resolves to. ``available`` is False only when there is
    nothing at all to explain."""

    available: bool
    evidence: Dict[str, Any] = field(default_factory=dict)
    #: The evidence as the model may see it -- narrower than ``evidence`` when
    #: a block with nothing to say was withheld.
    llm_evidence: Dict[str, Any] = field(default_factory=dict)
    withheld: List[str] = field(default_factory=list)
    sql_sources: List[Dict[str, Any]] = field(default_factory=list)
    proof: List[Dict[str, Any]] = field(default_factory=list)
    proof_note: Optional[str] = None
    tasks: List[DailyTask] = field(default_factory=list)


def build_scope_evidence(
    request: ScopeRequest,
    dataset,
    *,
    service: DailyService,
    evidence_service: EvidenceService,
    crew_suggestion_service: CrewSuggestionService,
    activity_service: WellActivityService,
) -> ScopeEvidence:
    """Resolve one scope to its evidence. Raises ``ValueError`` for a filter
    the dataset rejects (an unknown status, say)."""
    scope = request.scope
    tasks: List[DailyTask] = service.filter_tasks(
        dataset,
        uom=request.uom,
        wbs=request.wbs,
        activity_code=request.activity_code,
        status=request.status,
        well_id=request.well_id,
    )
    if request.task_daily_id is not None:
        tasks = [task for task in tasks if task.task_daily_id == request.task_daily_id]

    # A well's own task activity spans every date up to the report date, not
    # just that one date's entries, so it is resolved for a well scope
    # whether or not the well reported anything on the date itself.
    well_activity = None
    day_activity = None
    proof_rows: List[dict] = []
    if scope == "well" and request.well_id is not None:
        try:
            well_activity = activity_service.evidence(request.report_date, dataset, request.well_id)
            proof_rows = activity_service.proof_rows(request.report_date, request.well_id)
        except Exception:  # noqa: BLE001 - this must never break the well's own summary
            logger.exception("well task-activity evidence failed; continuing without it")
    elif request.is_front_page_day():
        try:
            day_activity = activity_service.day_evidence(request.report_date, dataset)
        except Exception:  # noqa: BLE001 - this must never break the day's own summary
            logger.exception("day task-activity evidence failed; continuing without it")

    if not tasks and well_activity is None and day_activity is None:
        return ScopeEvidence(available=False)

    evidence = evidence_service.build(
        dataset,
        tasks,
        scope=scope,
        uom=request.uom,
        wbs=request.wbs,
        activity_code=request.activity_code,
        status=request.status,
        well_id=request.well_id,
    )
    if well_activity is not None:
        evidence["well_task_activity"] = well_activity
    if day_activity is not None:
        evidence["live_well_task_activity"] = day_activity

    # Crew suggestion rides inside a single-task scope's evidence only; see
    # daily_report_rules.md section 8.
    if scope == "task" and len(tasks) == 1:
        try:
            crew_suggestion = crew_suggestion_service.build(
                well_id=tasks[0].well_id,
                task_code=tasks[0].task_code,
                report_date=request.report_date,
            )
        except Exception:  # noqa: BLE001 - this must never break the task's own summary
            logger.exception("crew suggestion evidence failed; continuing without it")
            crew_suggestion = None
        if crew_suggestion is not None:
            evidence["crew_suggestion"] = crew_suggestion

    # Evidence with nothing to say never reaches the model (section 8, #17).
    llm_evidence = evidence
    withheld: List[str] = []
    crew = evidence.get("crew_suggestion")
    if isinstance(crew, dict) and not (
        crew.get("suggested_crew") or crew.get("consult_crew") or crew.get("no_suggestion_reason")
    ):
        withheld.append("crew_suggestion")
        llm_evidence = {key: value for key, value in evidence.items() if key != "crew_suggestion"}

    sql_sources: List[Dict[str, Any]] = []
    if well_activity is not None or day_activity is not None:
        try:
            sql_sources = list(activity_service.sql_sources(request.report_date))
        except Exception:  # noqa: BLE001 - a missing .sql file must not break the summary
            logger.exception("could not load the SQL sources; continuing without them")

    shown = proof_rows[:PROOF_ROW_LIMIT]
    proof_note = None
    if len(proof_rows) > len(shown):
        proof_note = (
            f"Showing the first {len(shown)} of {len(proof_rows)} incomplete "
            "tasks. The counts above cover all of them."
        )

    return ScopeEvidence(
        available=True,
        evidence=evidence,
        llm_evidence=llm_evidence,
        withheld=withheld,
        sql_sources=sql_sources,
        proof=shown,
        proof_note=proof_note,
        tasks=tasks,
    )
