"""The agent graph:  plan -> act -> write -> verify -> (write again | end).

    plan    a FIXED plan for the dashboard's own briefs (day / well / task), so
            they cost no planning call and are as repeatable as before; a
            PLANNER model only for an open question, its plan validated
            against the tool catalogue and capped at AGENT_MAX_TOOL_CALLS.
    act     runs the plan's tools -- read-only, deterministic, never a model.
    write   one writer call over every tool result (or a cached answer for
            identical evidence within this run).
    verify  the deterministic check (app.agentic.verifier), plus an optional
            model second opinion. A failed draft returns to ``write`` with its
            issues, at most AGENT_MAX_REVISIONS times.

Every node appends to ``trace``: what it did, how long it took, which model it
used. The trace is returned beside the answer, so an operator can see the plan,
the tools called and the verifier's verdict for every sentence they read.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from collections import OrderedDict
from datetime import date
from typing import Any, Dict, List, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agentic import llm, prompts, tools, verifier
from app.config.settings import get_settings
from app.services.llm_service import LLMUnavailable

logger = logging.getLogger(__name__)


class AgentState(TypedDict, total=False):
    kind: str                     # "brief" | "ask"
    report_date: date
    scope: str                    # brief: day | well | task
    well_id: Optional[int]
    task_daily_id: Optional[int]
    question: str
    plan: List[Dict[str, Any]]
    unanswerable_reason: Optional[str]
    results: List[tools.ToolResult]
    draft: Optional[str]
    verification: Dict[str, Any]
    revisions: int
    retry: bool
    cached: bool
    models: Dict[str, str]
    error: Optional[str]
    trace: List[Dict[str, Any]]


# ---------------------------------------------------------------------------
# The per-run answer cache: in memory, cleared by Refresh and by a restart
# ---------------------------------------------------------------------------
class _RunCache:
    def __init__(self, max_entries: int = 500) -> None:
        self._entries: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._lock = threading.Lock()
        self._max = max_entries

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
            return entry

    def put(self, key: str, value: Dict[str, Any]) -> None:
        with self._lock:
            self._entries[key] = value
            self._entries.move_to_end(key)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)

    def invalidate_date(self, report_date: str) -> None:
        with self._lock:
            for key in [k for k, v in self._entries.items() if v.get("report_date") == report_date]:
                del self._entries[key]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


answer_cache = _RunCache()


def _cache_key(state: AgentState, writer_model: str) -> str:
    payload = {
        "v": prompts.AGENT_PROMPT_VERSION,
        "kind": state["kind"],
        "question": (state.get("question") or "").strip().lower(),
        "model": writer_model,
        "inputs": [result.for_model() for result in state.get("results", [])],
    }
    canonical = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _trace(state: AgentState, node: str, started: float, **detail: Any) -> List[Dict[str, Any]]:
    entry = {"node": node, "duration_s": round(time.perf_counter() - started, 3)}
    entry.update({k: v for k, v in detail.items() if v is not None})
    return list(state.get("trace", [])) + [entry]


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------
def fixed_plan(scope: str, well_id: Optional[int], task_daily_id: Optional[int]) -> List[Dict[str, Any]]:
    """The dashboard's own briefs: the same steps every time."""
    if scope == "well":
        return [
            {"tool": "well_overview", "args": {"well_id": well_id}, "why": "the well's own figures"},
            {"tool": "milestones", "args": {"well_id": well_id}, "why": "its lifecycle deadlines"},
        ]
    if scope == "task":
        return [
            {"tool": "task_detail", "args": {"task_daily_id": task_daily_id, "well_id": well_id},
             "why": "the task and its crew evidence"},
        ]
    return [
        {"tool": "day_overview", "args": {}, "why": "what was reported and where open work stands"},
        {"tool": "deadline_pressure", "args": {}, "why": "open work against upcoming deadlines"},
        {"tool": "data_quality", "args": {}, "why": "data problems worth flagging"},
    ]


_WELL_ID = re.compile(r"\bwell\s*#?\s*(\d{3,})\b", re.IGNORECASE)


def heuristic_plan(question: str) -> List[Dict[str, Any]]:
    """Used only when the planner model is unavailable or returns nothing usable."""
    lowered = question.lower()
    steps: List[Dict[str, Any]] = []
    match = _WELL_ID.search(question)
    if match:
        well_id = int(match.group(1))
        steps.append({"tool": "well_overview", "args": {"well_id": well_id}, "why": "the well asked about"})
        steps.append({"tool": "milestones", "args": {"well_id": well_id}, "why": "its deadlines"})
        return steps
    if any(word in lowered for word in ("deadline", "milestone", "rig", "flaf", "peg", "attention")):
        steps.append({"tool": "deadline_pressure", "args": {}, "why": "deadlines with open work"})
    if any(word in lowered for word in ("quality", "mapping", "unmapped", "missing")):
        steps.append({"tool": "data_quality", "args": {}, "why": "data problems"})
    if "not report" in lowered or "didn't report" in lowered or "no report" in lowered:
        steps.append({"tool": "list_wells", "args": {"filter": "no_report_with_open_work"}, "why": "wells without a report"})
    if not steps:
        steps.append({"tool": "day_overview", "args": {}, "why": "the day as a whole"})
    return steps


def _validate_plan(raw: Any, max_steps: int) -> List[Dict[str, Any]]:
    steps = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or item.get("tool") not in tools.TOOLS:
            continue
        args = item.get("args") if isinstance(item.get("args"), dict) else {}
        allowed = set(tools.TOOLS[item["tool"]].params)
        steps.append(
            {
                "tool": item["tool"],
                "args": {k: v for k, v in args.items() if k in allowed},
                "why": str(item.get("why") or "")[:200],
            }
        )
        if len(steps) >= max_steps:
            break
    return steps


def plan_node(state: AgentState) -> Dict[str, Any]:
    started = time.perf_counter()
    settings = get_settings()
    if state["kind"] == "brief":
        plan = fixed_plan(state.get("scope", "day"), state.get("well_id"), state.get("task_daily_id"))
        return {"plan": plan, "trace": _trace(state, "plan", started, mode="fixed", steps=len(plan))}

    question = state.get("question", "")
    catalog = json.dumps(tools.catalog(), indent=2)
    user = (
        f"Report date: {state['report_date'].isoformat()}\n\n"
        f"Tools:\n{catalog}\n\nQuestion: {question}"
    )
    models = dict(state.get("models", {}))
    try:
        text, usage = llm.chat(
            role="planner",
            model=settings.agent_planner_model,
            system=prompts.planner_system(settings.agent_max_tool_calls),
            user=user,
            max_tokens=settings.agent_planner_max_tokens,
            json_mode=True,
        )
        models["planner"] = usage["model"]
        parsed = llm.parse_json(text) or {}
        plan = _validate_plan(parsed.get("steps"), settings.agent_max_tool_calls)
        reason = parsed.get("unanswerable_reason") or None
        if not plan and not reason:
            plan = heuristic_plan(question)
            mode = "planner returned no usable step; heuristic plan"
        else:
            mode = "planner"
    except LLMUnavailable as exc:
        plan, reason, mode = heuristic_plan(question), None, f"planner unavailable ({exc}); heuristic plan"
        usage = {}
    return {
        "plan": plan,
        "unanswerable_reason": reason if not plan else None,
        "models": models,
        "trace": _trace(state, "plan", started, mode=mode, steps=len(plan),
                        model=usage.get("model"), reason=reason if not plan else None),
    }


# ---------------------------------------------------------------------------
# act
# ---------------------------------------------------------------------------
def act_node(state: AgentState) -> Dict[str, Any]:
    trace = list(state.get("trace", []))
    results: List[tools.ToolResult] = []
    for step in state.get("plan", [])[: get_settings().agent_max_tool_calls]:
        result = tools.run_tool(step["tool"], state["report_date"], step.get("args"))
        results.append(result)
        trace.append(
            {
                "node": "tool",
                "tool": result.tool,
                "args": result.args,
                "why": step.get("why"),
                "ok": result.ok,
                "error": result.error,
                "duration_s": round(result.duration_s, 3),
            }
        )
    return {"results": results, "trace": trace}


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------
def _writer_model(state: AgentState):
    settings = get_settings()
    if state["kind"] == "brief" and state.get("scope") == "day":
        return settings.agent_day_writer_model, settings.llm_day_max_tokens, 350
    return settings.agent_writer_model, settings.agent_writer_max_tokens, 250


def _unavailable(result: tools.ToolResult) -> bool:
    return (not result.ok) or (isinstance(result.result, dict) and result.result.get("available") is False)


def _nothing_to_explain(state: AgentState) -> bool:
    results = state.get("results", [])
    if not results:
        return True
    # A dashboard brief is about its scope's own tool (the first step): a well
    # with no evidence is not explained from its deadline list alone.
    if state["kind"] == "brief":
        return _unavailable(results[0])
    return all(
        (not r.ok) or (isinstance(r.result, dict) and r.result.get("available") is False)
        for r in results
    )


def write_node(state: AgentState) -> Dict[str, Any]:
    started = time.perf_counter()
    model, max_tokens, words = _writer_model(state)
    models = dict(state.get("models", {}))

    if state.get("unanswerable_reason"):
        text = (
            "This question cannot be answered from the daily report's data: "
            f"{state['unanswerable_reason']}"
        )
        return {"draft": text, "trace": _trace(state, "write", started, mode="unanswerable")}
    if _nothing_to_explain(state):
        return {
            "draft": None,
            "error": "There is no daily task record or task activity in this selection to explain.",
            "trace": _trace(state, "write", started, mode="nothing to explain"),
        }

    revisions = state.get("revisions", 0)
    if revisions == 0:
        cached = answer_cache.get(_cache_key(state, model))
        if cached is not None:
            models["writer"] = cached["model"]
            return {
                "draft": cached["draft"],
                "verification": cached["verification"],
                "cached": True,
                "models": models,
                "trace": _trace(state, "write", started, mode="reused from this run", model=cached["model"]),
            }

    inputs = [result.for_model() for result in state["results"]]
    user = (
        f"Report date: {state['report_date'].isoformat()}\n"
        + (f"Question: {state['question']}\n" if state.get("question") else "")
        + "\nTool results (authoritative):\n"
        + json.dumps(inputs, ensure_ascii=False, indent=2, default=str)
    )
    if revisions:
        user += (
            "\n\nYour previous draft was rejected by the verifier for these reasons:\n- "
            + "\n- ".join(state.get("verification", {}).get("issues", []))
            + "\n\nPrevious draft:\n" + (state.get("draft") or "")
            + "\n\nWrite a corrected answer that fixes every issue."
        )
    try:
        text, usage = llm.chat(
            role="writer", model=model, system=prompts.writer_system(words),
            user=user, max_tokens=max_tokens,
        )
    except LLMUnavailable as exc:
        return {
            "draft": None,
            "error": str(exc),
            "trace": _trace(state, "write", started, mode="failed", error=str(exc), model=model),
        }
    models["writer"] = usage["model"]
    return {
        "draft": text,
        "cached": False,
        "models": models,
        "trace": _trace(state, "write", started, mode="revision" if revisions else "draft",
                        model=usage["model"], output_tokens=usage.get("output_tokens")),
    }


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------
def verify_node(state: AgentState) -> Dict[str, Any]:
    started = time.perf_counter()
    if state.get("cached") or not state.get("draft") or state.get("unanswerable_reason"):
        return {"trace": _trace(state, "verify", started, mode="skipped")}

    inputs = [result.for_model() for result in state.get("results", [])]
    evidence = inputs + [{"report_date": state["report_date"]}]
    check = verifier.verify(state["draft"], evidence, question=state.get("question", ""))
    issues = list(check.issues)
    models = dict(state.get("models", {}))

    settings = get_settings()
    if settings.agent_llm_verify and not issues:
        try:
            text, usage = llm.chat(
                role="verifier",
                model=settings.agent_verifier_model,
                system=prompts.VERIFIER_SYSTEM,
                user="Tool results:\n" + json.dumps(inputs, default=str, ensure_ascii=False)
                + "\n\nDraft:\n" + state["draft"],
                max_tokens=600,
                json_mode=True,
            )
            models["verifier"] = usage["model"]
            opinion = llm.parse_json(text) or {}
            if opinion.get("ok") is False:
                issues.extend(str(i) for i in (opinion.get("issues") or [])[:5])
        except LLMUnavailable:
            logger.info("agent verifier model unavailable; deterministic check only")

    verification = {
        "passed": not issues,
        "issues": issues,
        "checked_numbers": check.checked_numbers,
        "llm_second_opinion": bool(settings.agent_llm_verify),
    }
    retry = bool(issues) and state.get("revisions", 0) < settings.agent_max_revisions
    update: Dict[str, Any] = {
        "verification": verification,
        "models": models,
        "retry": retry,
        "trace": _trace(state, "verify", started, passed=not issues, issues=len(issues),
                        next="revise" if retry else "finish"),
    }
    if retry:
        update["revisions"] = state.get("revisions", 0) + 1
    elif not issues:
        # Only a verified answer is ever reused.
        model, _, _ = _writer_model(state)
        answer_cache.put(
            _cache_key(state, model),
            {
                "report_date": state["report_date"].isoformat(),
                "draft": state["draft"],
                "verification": verification,
                "model": models.get("writer", model),
            },
        )
    return update


def _after_verify(state: AgentState) -> str:
    return "write" if state.get("retry") else END


def _after_write(state: AgentState) -> str:
    return "verify" if state.get("draft") else END


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("plan", plan_node)
    graph.add_node("act", act_node)
    graph.add_node("write", write_node)
    graph.add_node("verify", verify_node)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "act")
    graph.add_edge("act", "write")
    graph.add_conditional_edges("write", _after_write, {"verify": "verify", END: END})
    graph.add_conditional_edges("verify", _after_verify, {"write": "write", END: END})
    return graph.compile()


_compiled = None
_compiled_lock = threading.Lock()


def compiled_graph():
    global _compiled
    with _compiled_lock:
        if _compiled is None:
            _compiled = build_graph()
        return _compiled
