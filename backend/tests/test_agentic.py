"""The agentic layer: tools, verifier, graph and endpoints -- all offline.

The model is replaced by a fake ``httpx.post`` that answers per agent role
(planner / writer / verifier), recognised by its system prompt, so every test
controls exactly what each agent "says" and can count how often it was asked.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agentic import graph as agent_graph
from app.agentic import service as agent_service
from app.agentic import tools, verifier
from app.api import dependencies
from app.config.settings import get_settings
from app.services.daily_service import DailyService
from tests.conftest import REPORT_DATE, make_row, stub_well_activity_service
from tests.test_api_and_llm import StubRepository

REPORTING_WELL = 101
QUIET_WELL = 30349

ROWS = [make_row(task_daily_id=1, well_id=REPORTING_WELL, planned=10, actual_quantity=10)]
ACTIVITY = [
    {
        "well_id": QUIET_WELL,
        "logical_task_count": 5,
        "completed_task_count": 2,
        "open_task_count": 3,
        "incomplete_task_count": 1,
        "ongoing_task_count": 2,
        "not_started_task_count": 1,
        "ended_not_completed_task_count": 0,
        "last_task_date": date(2026, 7, 20),
    }
]


class _Alert:
    def __init__(self, well_id, days_remaining, label="Rig on"):
        self.well_id = well_id
        self.days_remaining = days_remaining
        self.deadline_date = date.today() + timedelta(days=days_remaining)
        self.label = label


class StubMilestones:
    def upcoming_and_overdue(self, window_days, *, today=None):
        return [_Alert(QUIET_WELL, 4)], [_Alert(REPORTING_WELL, -3, "FLAF")]


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def services(monkeypatch):
    daily = DailyService(repository=StubRepository(ROWS))
    monkeypatch.setattr(dependencies, "_daily_service", daily)
    monkeypatch.setattr(dependencies, "_well_activity_service", stub_well_activity_service(ACTIVITY, []))
    monkeypatch.setattr(dependencies, "_milestone_service", StubMilestones())
    agent_graph.answer_cache.clear()
    yield
    agent_graph.answer_cache.clear()


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "writer-model")
    monkeypatch.setenv("LLM_DAY_MODEL", "gpt-5-day")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("AGENT_MAX_REVISIONS", "2")
    monkeypatch.setenv("AGENT_LLM_VERIFY", "false")
    yield
    get_settings.cache_clear()


class FakeModel:
    """Answers each role from its own queue; the last answer repeats."""

    def __init__(self):
        self.answers: Dict[str, List[str]] = {"planner": [], "writer": [], "verifier": []}
        self.calls: List[Dict[str, Any]] = []

    def role_of(self, payload) -> str:
        system = payload["messages"][0]["content"]
        if system.startswith("You plan read-only"):
            return "planner"
        if system.startswith("You check a draft"):
            return "verifier"
        return "writer"

    def __call__(self, url, *, headers, json, timeout):
        role = self.role_of(json)
        self.calls.append({"role": role, "payload": json})
        queue = self.answers[role]
        text = queue.pop(0) if len(queue) > 1 else (queue[0] if queue else "")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
            request=httpx.Request("POST", url),
        )

    def count(self, role):
        return sum(1 for call in self.calls if call["role"] == role)


@pytest.fixture
def model(monkeypatch) -> FakeModel:
    fake = FakeModel()
    monkeypatch.setattr(httpx, "post", fake)
    return fake


# ---------------------------------------------------------------------------
# verifier
# ---------------------------------------------------------------------------
class TestVerifier:
    EVIDENCE = [{"result": {"well_id": 30349, "open_task_count": 3, "date": "2026-07-20", "status": "LATE"}}]

    def test_figures_from_the_evidence_pass(self):
        check = verifier.verify("Well 30349 has 3 open tasks, last seen 2026-07-20.", self.EVIDENCE)
        assert check.passed, check.issues

    def test_an_invented_figure_fails(self):
        check = verifier.verify("Well 30349 has 7 open tasks.", self.EVIDENCE)
        assert not check.passed
        assert any("7" in issue for issue in check.issues)

    def test_an_invented_date_fails(self):
        assert not verifier.verify("Last seen 2026-07-21.", self.EVIDENCE).passed

    def test_a_judgement_no_rule_defines_fails(self):
        check = verifier.verify("Well 30349 is at risk.", self.EVIDENCE)
        assert any("at risk" in issue for issue in check.issues)

    def test_a_word_the_evidence_carries_as_a_status_code_is_allowed(self):
        assert verifier.verify("The end date status is late.", self.EVIDENCE).passed

    def test_a_field_name_in_the_prose_fails(self):
        check = verifier.verify("The open_task_count is 3.", self.EVIDENCE)
        assert any("open_task_count" in issue for issue in check.issues)

    def test_list_numbering_is_not_a_figure(self):
        assert verifier.verify("1. Well 30349\n2. 3 open tasks", self.EVIDENCE).passed

    def test_thousands_separators_and_decimals_are_normalised(self):
        evidence = [{"result": {"total": 1226, "qty": "10.5000"}}]
        assert verifier.verify("1,226 tasks and 10.5 units.", evidence).passed


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------
class TestTools:
    def test_unknown_tool_is_refused_as_data(self):
        result = tools.run_tool("drop_table", REPORT_DATE, {})
        assert not result.ok and "unknown tool" in result.error

    def test_bad_arguments_are_refused_not_guessed(self):
        result = tools.run_tool("well_overview", REPORT_DATE, {"well_id": "thirty"})
        assert not result.ok and "whole number" in result.error

    def test_list_wells_without_a_report_but_with_open_work(self):
        result = tools.run_tool("list_wells", REPORT_DATE, {"filter": "no_report_with_open_work"})
        assert result.ok
        assert [w["well_id"] for w in result.result["wells"]] == [QUIET_WELL]

    def test_deadline_pressure_joins_deadlines_to_open_work(self):
        result = tools.run_tool("deadline_pressure", REPORT_DATE, {})
        assert result.ok
        before = result.result["open_work_before_upcoming_deadline"]
        assert [(r["well_id"], r["open_task_count"]) for r in before] == [(QUIET_WELL, 3)]
        # Well 101 has no open work in the activity stub, so its overdue FLAF is not listed.
        assert result.result["open_work_past_overdue_deadline"] == []

    def test_the_catalog_names_every_tool(self):
        assert {t["name"] for t in tools.catalog()} == set(tools.TOOLS)


# ---------------------------------------------------------------------------
# the graph
# ---------------------------------------------------------------------------
GOOD_WELL_ANSWER = "Well 30349 reported nothing on the date and has 3 open tasks: 2 ongoing and 1 incomplete."


class TestBrief:
    def test_a_well_brief_runs_a_fixed_plan_and_is_verified(self, model):
        model.answers["writer"] = [GOOD_WELL_ANSWER]
        result = agent_service.brief(REPORT_DATE, scope="well", well_id=QUIET_WELL)
        assert result["available"] and result["verified"]
        assert result["explanation"] == GOOD_WELL_ANSWER
        assert [s["tool"] for s in result["plan"]] == ["well_overview", "milestones"]
        assert [t["node"] for t in result["trace"]] == ["plan", "tool", "tool", "write", "verify"]
        assert model.count("planner") == 0  # fixed plans cost no planning call
        assert result["models"]["writer"] == "writer-model"
        assert result["evidence"]["well_task_activity"]  # the classic drawers still fill

    def test_the_same_brief_is_reused_within_the_run(self, model):
        model.answers["writer"] = [GOOD_WELL_ANSWER]
        first = agent_service.brief(REPORT_DATE, scope="well", well_id=QUIET_WELL)
        second = agent_service.brief(REPORT_DATE, scope="well", well_id=QUIET_WELL)
        assert first["cached"] is False and second["cached"] is True
        assert second["explanation"] == first["explanation"]
        assert model.count("writer") == 1

    def test_refresh_drops_the_reused_answer(self, model):
        model.answers["writer"] = [GOOD_WELL_ANSWER]
        agent_service.brief(REPORT_DATE, scope="well", well_id=QUIET_WELL)
        dependencies.get_llm_service().invalidate_date(REPORT_DATE)
        again = agent_service.brief(REPORT_DATE, scope="well", well_id=QUIET_WELL)
        assert again["cached"] is False
        assert model.count("writer") == 2

    def test_a_rejected_draft_is_revised(self, model):
        model.answers["writer"] = ["Well 30349 has 47 open tasks and is delayed.", GOOD_WELL_ANSWER]
        result = agent_service.brief(REPORT_DATE, scope="well", well_id=QUIET_WELL)
        assert result["verified"] and result["revisions"] == 1
        revision_prompt = model.calls[-1]["payload"]["messages"][1]["content"]
        assert "rejected by the verifier" in revision_prompt and "47" in revision_prompt

    def test_revisions_are_bounded_and_an_unverified_answer_says_so(self, model):
        model.answers["writer"] = ["Well 30349 has 47 open tasks."]
        result = agent_service.brief(REPORT_DATE, scope="well", well_id=QUIET_WELL)
        assert result["available"] and not result["verified"]
        assert result["revisions"] == 2
        assert model.count("writer") == 3
        assert result["verification"]["issues"]

    def test_the_day_brief_uses_the_day_writer_model(self, model):
        model.answers["writer"] = ["Well 101 reported 1 task on the date."]
        result = agent_service.brief(REPORT_DATE, scope="day")
        assert [s["tool"] for s in result["plan"]] == ["day_overview", "deadline_pressure", "data_quality"]
        assert result["models"]["writer"] == "gpt-5-day"

    def test_nothing_to_explain_is_not_sent_to_a_model(self, model):
        result = agent_service.brief(REPORT_DATE, scope="well", well_id=999999)
        assert not result["available"]
        assert model.count("writer") == 0


class TestAsk:
    def test_the_planner_chooses_tools_and_its_plan_is_validated(self, model):
        model.answers["planner"] = [
            '{"steps": ['
            '{"tool": "deadline_pressure", "args": {"limit": 5, "sql": "DROP"}, "why": "deadlines"},'
            '{"tool": "invented_tool", "args": {}, "why": "x"}'
            "]}"
        ]
        model.answers["writer"] = ["Well 30349 has 3 open tasks before an upcoming Rig on deadline."]
        result = agent_service.ask(REPORT_DATE, "Which wells need attention before their deadlines?")
        assert [s["tool"] for s in result["plan"]] == ["deadline_pressure"]
        assert result["plan"][0]["args"] == {"limit": 5}  # the unlisted argument is dropped
        assert result["verified"]

    def test_an_unanswerable_question_is_said_to_be_so(self, model):
        model.answers["planner"] = ['{"steps": [], "unanswerable_reason": "costs are not in the report"}']
        result = agent_service.ask(REPORT_DATE, "How much did well 30349 cost?")
        assert result["available"]
        assert "cannot be answered" in result["explanation"]
        assert model.count("writer") == 0

    def test_a_failed_planner_falls_back_to_a_heuristic_plan(self, model, monkeypatch):
        from app.services.llm_service import LLMUnavailable

        real_chat = agent_graph.llm.chat

        def chat(**kwargs):
            if kwargs["role"] == "planner":
                raise LLMUnavailable("down")
            return real_chat(**kwargs)

        monkeypatch.setattr(agent_graph.llm, "chat", chat)
        model.answers["writer"] = [GOOD_WELL_ANSWER]
        result = agent_service.ask(REPORT_DATE, "What is happening on well 30349?")
        assert [s["tool"] for s in result["plan"]] == ["well_overview", "milestones"]
        assert "heuristic" in result["trace"][0]["mode"]


class TestEndpoints:
    @pytest.fixture
    def client(self):
        from app.main import app

        return TestClient(app)

    def test_brief_endpoint_keeps_the_classic_fields(self, client, model):
        model.answers["writer"] = [GOOD_WELL_ANSWER]
        body = client.post(
            "/api/agent/brief",
            json={"report_date": str(REPORT_DATE), "scope": "well", "well_id": QUIET_WELL},
        ).json()
        for field in ("available", "explanation", "cached", "evidence", "sql_sources", "proof",
                      "trace", "plan", "verification", "verified"):
            assert field in body
        assert body["verified"] is True

    def test_brief_endpoint_refuses_a_scope_it_does_not_cover(self, client):
        response = client.post("/api/agent/brief", json={"report_date": str(REPORT_DATE), "scope": "uom"})
        assert response.status_code == 400

    def test_ask_endpoint(self, client, model):
        model.answers["planner"] = ['{"steps": [{"tool": "list_wells", "args": {"filter": "no_report_with_open_work"}}]}']
        model.answers["writer"] = ["Well 30349 reported nothing on the date and has 3 open tasks."]
        body = client.post(
            "/api/agent/ask",
            json={"report_date": str(REPORT_DATE), "question": "Which wells did not report?"},
        ).json()
        assert body["available"] and body["kind"] == "ask"

    def test_tools_endpoint(self, client):
        assert {t["name"] for t in client.get("/api/agent/tools").json()["tools"]} == set(tools.TOOLS)
