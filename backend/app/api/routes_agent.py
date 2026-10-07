"""The agentic endpoints.

    POST /api/agent/brief   the dashboard's AI summaries (day / well / task),
                            same request body as /api/daily/explain
    POST /api/agent/ask     an open question about one report date
    GET  /api/agent/tools   the tools an agent may call, as the planner sees them

An agent failure is never a dashboard failure: the response carries
``available: false`` with the reason, and the tool results it gathered.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.agentic import service as agent_service
from app.agentic import tools
from app.schemas.daily import ExplainRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent", tags=["agent"])

_BRIEF_SCOPES = {"day", "well", "task"}


class AskRequest(BaseModel):
    report_date: date
    question: str = Field(..., min_length=3, max_length=500)


@router.post("/brief")
def brief(request: ExplainRequest) -> Dict[str, Any]:
    scope = (request.scope or "day").strip().lower()
    if scope not in _BRIEF_SCOPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"The agent brief covers {sorted(_BRIEF_SCOPES)}; use /api/daily/explain for {scope!r}.",
        )
    if scope == "well" and request.well_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="well_id is required")
    if scope == "task" and request.task_daily_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="task_daily_id is required")
    try:
        return agent_service.brief(
            request.report_date,
            scope=scope,
            well_id=request.well_id,
            task_daily_id=request.task_daily_id,
        )
    except Exception as exc:  # noqa: BLE001 - the dashboard must survive anything here
        logger.exception("agent brief failed")
        return {
            "report_date": request.report_date,
            "kind": "brief",
            "scope": scope,
            "available": False,
            "error": f"The agent could not complete this summary ({type(exc).__name__}).",
            "evidence": {},
        }


@router.post("/ask")
def ask(request: AskRequest) -> Dict[str, Any]:
    try:
        return agent_service.ask(request.report_date, request.question)
    except Exception as exc:  # noqa: BLE001
        logger.exception("agent question failed")
        return {
            "report_date": request.report_date,
            "kind": "ask",
            "question": request.question,
            "available": False,
            "error": f"The agent could not answer this question ({type(exc).__name__}).",
            "evidence": {},
        }


@router.get("/tools")
def list_tools() -> Dict[str, Any]:
    return {"tools": tools.catalog()}
