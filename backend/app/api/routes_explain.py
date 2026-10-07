<<<<<<< HEAD
"""The Explain / Elaborate endpoint.

The request names a scope. The backend re-derives the deterministic evidence
for that scope, sends it to the LLM, and returns the explanation alongside the
evidence it was based on. The client cannot supply the figures to be explained,
and the LLM never touches the database.
=======
"""The classic, single-call Explain endpoint.

Kept in the agentic edition as the fallback and as the reference the agents
are measured against: the request names a scope, the backend re-derives the
deterministic evidence for it (``app.services.explain_evidence``), sends it to
the LLM once, and returns the explanation beside the evidence. The agentic
equivalent is ``/api/agent/brief`` (``app.api.routes_agent``), which builds its
evidence through the very same function.
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)

A failure here is not a dashboard failure: the response carries
``available: false`` with a reason, plus the evidence, which remains valid.
"""

from __future__ import annotations

import logging
<<<<<<< HEAD
from typing import List
=======
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import (
    get_crew_suggestion_service,
    get_daily_service,
    get_evidence_service,
    get_llm_service,
    get_well_activity_service,
    load_dataset,
)
<<<<<<< HEAD
from app.models.daily import DailyTask
=======
from app.config.settings import get_settings
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
from app.schemas.daily import ExplainRequest, ExplainResponse, ProofRowOut, SqlSourceOut
from app.services.crew_suggestion_service import CrewSuggestionService
from app.services.daily_service import DailyService
from app.services.evidence_service import EvidenceService
<<<<<<< HEAD
=======
from app.services.explain_evidence import VALID_SCOPES, ScopeRequest, build_scope_evidence
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
from app.services.llm_service import LLMService
from app.services.well_activity_service import WellActivityService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/daily", tags=["explain"])

<<<<<<< HEAD
VALID_SCOPES = {"day", "uom", "group", "status", "well", "task"}

#: How many proof rows travel to the panel. A well's incomplete tasks run to
#: a few dozen at most on this data, so this only bounds a pathological case
#: -- and when it bites, the response says so rather than quietly truncating.
_PROOF_ROW_LIMIT = 300


def _is_filtered(request: ExplainRequest) -> bool:
    """True when a "day" request is really a slice of the day.

    The well-universe overview belongs to the whole view only. A request
    narrowed to one status, WBS, activity, unit or well is describing a
    subset of the day's reported tasks, and a count of every live well's open
    work alongside it would be a different population than the one being
    explained.
    """
    return any(
        value is not None
        for value in (
            request.uom,
            request.wbs,
            request.activity_code,
            request.status,
            request.well_id,
            request.task_daily_id,
        )
=======

def scope_request(request: ExplainRequest) -> ScopeRequest:
    scope = (request.scope or "day").strip().lower()
    if scope not in VALID_SCOPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown scope {request.scope!r}. Valid scopes: "
            + ", ".join(sorted(VALID_SCOPES)),
        )
    return ScopeRequest(
        report_date=request.report_date,
        scope=scope,
        uom=request.uom,
        wbs=request.wbs,
        activity_code=request.activity_code,
        status=request.status,
        well_id=request.well_id,
        task_daily_id=request.task_daily_id,
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
    )


@router.post("/explain", response_model=ExplainResponse)
def explain(
    request: ExplainRequest,
    service: DailyService = Depends(get_daily_service),
    evidence_service: EvidenceService = Depends(get_evidence_service),
    llm_service: LLMService = Depends(get_llm_service),
    crew_suggestion_service: CrewSuggestionService = Depends(get_crew_suggestion_service),
    activity_service: WellActivityService = Depends(get_well_activity_service),
) -> ExplainResponse:
<<<<<<< HEAD
    scope = (request.scope or "day").strip().lower()
    if scope not in VALID_SCOPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown scope {request.scope!r}. Valid scopes: "
            + ", ".join(sorted(VALID_SCOPES)),
        )

    dataset = load_dataset(service, request.report_date)
    try:
        tasks: List[DailyTask] = service.filter_tasks(
            dataset,
            uom=request.uom,
            wbs=request.wbs,
            activity_code=request.activity_code,
            status=request.status,
            well_id=request.well_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from None

    if request.task_daily_id is not None:
        tasks = [task for task in tasks if task.task_daily_id == request.task_daily_id]

    # A well's own task activity spans every date up to the report date, not
    # just that one date's entries, so it is resolved for a well scope
    # whether or not the well reported anything on the date itself. It is
    # also the reason a well with no task today can still be explained: the
    # evidence about it -- how many of its tasks are incomplete, how many are
    # ongoing, when it was last seen -- exists regardless.
    well_activity = None
    day_activity = None
    proof_rows: List[dict] = []
    if scope == "well" and request.well_id is not None:
        try:
            well_activity = activity_service.evidence(
                request.report_date, dataset, request.well_id
            )
            # The working behind those figures, for the operator's own
            # panel: every incomplete task with the reason it is counted.
            # Never added to the evidence -- this is proof to read, not
            # input for the model.
            proof_rows = activity_service.proof_rows(request.report_date, request.well_id)
        except Exception:  # noqa: BLE001 - this must never break the well's own summary
            logger.exception("well task-activity evidence failed; continuing without it")
    elif scope == "day" and not _is_filtered(request):
        # The whole-view explanation describes the day the front page is
        # showing, and that page is a list of live wells -- including the ones
        # that reported nothing. Without this the summary could only discuss
        # what was reported: on a quiet day, one well and one task, while
        # hundreds of live wells carried unfinished work it never saw.
        try:
            day_activity = activity_service.day_evidence(request.report_date, dataset)
        except Exception:  # noqa: BLE001 - this must never break the day's own summary
            logger.exception("day task-activity evidence failed; continuing without it")

    if not tasks and well_activity is None and day_activity is None:
        return ExplainResponse(
            report_date=request.report_date,
            scope=scope,
=======
    scope = scope_request(request)
    dataset = load_dataset(service, request.report_date)
    try:
        resolved = build_scope_evidence(
            scope,
            dataset,
            service=service,
            evidence_service=evidence_service,
            crew_suggestion_service=crew_suggestion_service,
            activity_service=activity_service,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None

    if not resolved.available:
        return ExplainResponse(
            report_date=request.report_date,
            scope=scope.scope,
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
            available=False,
            error="There are no daily task records in this selection to explain.",
            evidence={},
        )

<<<<<<< HEAD
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
        # Merged as its own block rather than folded into "summary": the
        # summary describes what the well did on the report date, this
        # describes where the well's tasks stand overall as of it. Keeping
        # them apart is what stops the model reading one as the other.
        evidence["well_task_activity"] = well_activity
    if day_activity is not None:
        # Same separation, one level up: what every live well's tasks add up
        # to as of the date, beside what was actually reported on it.
        evidence["live_well_task_activity"] = day_activity

    # Crew suggestion is an extension of the task-specific AI summary only --
    # never a second UI control or a second request. It rides inside this
    # same evidence payload, for this same LLM call, only when the request
    # resolves to exactly one task (the task AI summary the frontend already
    # requests via scope="task" + task_daily_id). See daily_report_rules.md,
    # "Crew suggestion" section.
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

    # Whenever there is nothing at all for the model to say about crew
    # suggestion -- a completed task, or an in-progress task with no
    # historically-proven crew to point to -- the evidence is withheld from
    # the LLM call entirely rather than trusted to stay silent on its own. A
    # system-instruction rule alone cannot guarantee that from a
    # non-deterministic model. The full evidence, including the suppression
    # reason, still reaches the client below for transparency/audit -- only
    # the LLM's own input is narrowed. `suggested_crew` (a stalled, eligible
    # task), `consult_crew` (an in-progress task worth an informational
    # mention) and `no_suggestion_reason` (eligible, but no crew found) are
    # each, on their own, something worth the model narrating.
    llm_evidence = evidence
    withheld: List[str] = []
    crew_suggestion_evidence = evidence.get("crew_suggestion")
    if isinstance(crew_suggestion_evidence, dict) and not (
        crew_suggestion_evidence.get("suggested_crew")
        or crew_suggestion_evidence.get("consult_crew")
        or crew_suggestion_evidence.get("no_suggestion_reason")
    ):
        withheld.append("crew_suggestion")
        llm_evidence = {key: value for key, value in evidence.items() if key != "crew_suggestion"}

    # The queries behind whichever figures this scope carries, and the rows
    # behind its counts. Both are for the panel, and neither has been near
    # `llm_evidence` above.
    sql_sources: List[SqlSourceOut] = []
    if well_activity is not None or day_activity is not None:
        try:
            sql_sources = [
                SqlSourceOut(**source)
                for source in activity_service.sql_sources(request.report_date)
            ]
        except Exception:  # noqa: BLE001 - a missing .sql file must not break the summary
            logger.exception("could not load the SQL sources; continuing without them")

    shown_proof = proof_rows[:_PROOF_ROW_LIMIT]
    proof_note = None
    if len(proof_rows) > len(shown_proof):
        proof_note = (
            f"Showing the first {len(shown_proof)} of {len(proof_rows)} incomplete "
            "tasks. The counts above cover all of them."
        )

    result = llm_service.explain_safe(llm_evidence)
    if not result["available"]:
        logger.info("explanation unavailable for scope=%s: %s", scope, result["error"])

    return ExplainResponse(
        report_date=request.report_date,
        scope=scope,
=======
    # The front page's own whole-day summary runs on its own model
    # (LLM_DAY_MODEL); every per-well and per-task summary stays on LLM_MODEL.
    if scope.is_front_page_day():
        settings = get_settings()
        result = llm_service.explain_safe(
            resolved.llm_evidence,
            model=settings.llm_day_model,
            max_tokens=settings.llm_day_max_tokens,
        )
    else:
        result = llm_service.explain_safe(resolved.llm_evidence)
    if not result["available"]:
        logger.info("explanation unavailable for scope=%s: %s", scope.scope, result["error"])

    return ExplainResponse(
        report_date=request.report_date,
        scope=scope.scope,
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
        available=result["available"],
        explanation=result["explanation"],
        error=result["error"],
        model=result["model"],
        cached=result.get("cached", False),
<<<<<<< HEAD
        evidence=evidence,
        evidence_withheld_from_model=withheld,
        sql_sources=sql_sources,
        proof=[ProofRowOut(**row) for row in shown_proof],
        proof_note=proof_note,
=======
        evidence=resolved.evidence,
        evidence_withheld_from_model=resolved.withheld,
        sql_sources=[SqlSourceOut(**source) for source in resolved.sql_sources],
        proof=[ProofRowOut(**row) for row in resolved.proof],
        proof_note=resolved.proof_note,
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
    )
