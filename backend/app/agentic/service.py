"""Running the agent graph and shaping its answer for the dashboard.

The response deliberately keeps the classic explain response's fields
(``available``, ``explanation``, ``cached``, ``evidence``, ``sql_sources``,
``proof`` ...) so every existing panel renders an agent answer unchanged, and
adds what is new: the plan, the trace, the verifier's verdict and the models
each role used.
"""

from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any, Dict, Optional

from app.agentic.graph import AgentState, answer_cache, compiled_graph

logger = logging.getLogger(__name__)

#: Which tool's audit material fills the classic evidence / SQL / proof drawers.
_PRIMARY_TOOL = {"day": "day_overview", "well": "well_overview", "task": "task_detail"}


def invalidate_date(report_date: date) -> None:
    """Refresh drops the agents' answers for the date, like the classic cache."""
    answer_cache.invalidate_date(report_date.isoformat())


def _respond(state: AgentState, *, kind: str, scope: Optional[str], started: float) -> Dict[str, Any]:
    results = state.get("results", [])
    primary_name = _PRIMARY_TOOL.get(scope or "")
    primary = next((r for r in results if r.tool == primary_name and r.ok), None)
    audit = primary.audit if primary is not None else {}
    draft = state.get("draft")
    verification = state.get("verification") or {}

    return {
        "report_date": state["report_date"],
        "kind": kind,
        "scope": scope,
        "question": state.get("question"),
        "available": bool(draft),
        "explanation": draft,
        "error": None if draft else state.get("error") or "No answer could be produced.",
        "model": (state.get("models") or {}).get("writer"),
        "models": state.get("models") or {},
        "cached": bool(state.get("cached")),
        "verified": bool(verification.get("passed")) if draft else False,
        "verification": verification,
        "revisions": state.get("revisions", 0),
        "plan": state.get("plan", []),
        "trace": state.get("trace", []),
        "tool_results": [r.for_model() for r in results],
        # The classic drawers, from the scope's own tool when there is one.
        "evidence": audit.get("evidence") or {"tool_results": [r.for_model() for r in results]},
        "evidence_withheld_from_model": audit.get("withheld", []),
        "sql_sources": audit.get("sql_sources", []),
        "proof": audit.get("proof", []),
        "proof_note": audit.get("proof_note"),
        "duration_s": round(time.perf_counter() - started, 2),
    }


def brief(
    report_date: date,
    *,
    scope: str = "day",
    well_id: Optional[int] = None,
    task_daily_id: Optional[int] = None,
) -> Dict[str, Any]:
    """The dashboard's own AI summaries, produced by the agent graph."""
    started = time.perf_counter()
    state = compiled_graph().invoke(
        {
            "kind": "brief",
            "report_date": report_date,
            "scope": scope,
            "well_id": well_id,
            "task_daily_id": task_daily_id,
            "question": "",
            "revisions": 0,
            "trace": [],
            "models": {},
        }
    )
    return _respond(state, kind="brief", scope=scope, started=started)


def ask(report_date: date, question: str) -> Dict[str, Any]:
    """An open question about the report date, planned by the planner agent."""
    started = time.perf_counter()
    state = compiled_graph().invoke(
        {
            "kind": "ask",
            "report_date": report_date,
            "question": question.strip(),
            "revisions": 0,
            "trace": [],
            "models": {},
        }
    )
    return _respond(state, kind="ask", scope=None, started=started)
