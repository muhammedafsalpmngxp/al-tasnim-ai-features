"""Crew suggestion is wired into the EXISTING /api/daily/explain endpoint only.

There is no second endpoint and no second request. These tests exercise the
real route with a stubbed daily dataset, a stubbed crew-suggestion service and
a stubbed LLM client (capturing exactly what evidence it was called with),
mirroring the pattern already used in test_explain_cache.py.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

from app.api import dependencies
from app.models.daily import DayCounters
from app.services.crew_suggestion_service import CrewSuggestionService
from app.services.daily_service import DailyService
from app.services.llm_service import LLMService
from tests.conftest import REPORT_DATE, make_row, stub_well_activity_service

ROWS = [make_row(task_daily_id=1, well_id=101, task_code="AAAA0001-101", planned=10, actual_quantity=10)]


class StubRepository:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows

    def fetch_day(self, report_date: date):
        if report_date != REPORT_DATE:
            return [], DayCounters()
        rows = [dict(row, action_on=report_date) for row in self._rows]
        return rows, DayCounters(raw_row_count=len(rows), logical_task_count=len(rows))

    def fetch_dates_with_activity(self, limit: int):
        return [{"report_date": REPORT_DATE, "row_count": len(self._rows), "well_count": 1}]


class StubCrewSuggestionService(CrewSuggestionService):
    """Returns a fixed evidence dict instead of touching the database."""

    def __init__(self, evidence: Optional[Dict[str, Any]]) -> None:
        self._evidence = evidence
        self.calls: List[Dict[str, Any]] = []

    def build(self, *, well_id, task_code, report_date):
        self.calls.append({"well_id": well_id, "task_code": task_code, "report_date": report_date})
        return self._evidence


class CapturingLLMService(LLMService):
    """Records exactly the evidence dict it was asked to explain."""

    def __init__(self) -> None:
        self.received_evidence: List[Dict[str, Any]] = []
        from app.services.llm_service import _ExplainCache

<<<<<<< HEAD
        self._cache = _ExplainCache(None)

    def explain(self, evidence):
=======
        self._cache = _ExplainCache()

    def explain(self, evidence, **_overrides):
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
        self.received_evidence.append(evidence)
        return "a plain explanation", "test-model"


@pytest.fixture
def wired_client(monkeypatch):
    from app.main import app

    daily_service = DailyService(repository=StubRepository(ROWS))
    llm_service = CapturingLLMService()
    activity_service = stub_well_activity_service()
    monkeypatch.setattr(dependencies, "_daily_service", daily_service)
    monkeypatch.setattr(dependencies, "_llm_service", llm_service)
    app.dependency_overrides[dependencies.get_daily_service] = lambda: daily_service
    app.dependency_overrides[dependencies.get_llm_service] = lambda: llm_service
    app.dependency_overrides[dependencies.get_well_activity_service] = lambda: activity_service

    def _make(evidence: Optional[Dict[str, Any]]):
        crew_service = StubCrewSuggestionService(evidence)
        app.dependency_overrides[dependencies.get_crew_suggestion_service] = lambda: crew_service
        return TestClient(app), llm_service, crew_service

    try:
        yield _make
    finally:
        app.dependency_overrides.clear()


TASK_PAYLOAD = {
    "report_date": str(REPORT_DATE),
    "scope": "task",
    "well_id": 101,
    "task_daily_id": 1,
}


class TestCrewSuggestionOnlyAppliesToTaskScope:
    def test_a_well_scope_request_never_calls_the_crew_suggestion_service(self, wired_client):
        client, llm_service, crew_service = wired_client(None)
        client.post("/api/daily/explain", json={**TASK_PAYLOAD, "scope": "well", "task_daily_id": None})
        assert crew_service.calls == []

    def test_a_day_scope_request_never_calls_the_crew_suggestion_service(self, wired_client):
        client, llm_service, crew_service = wired_client(None)
        client.post("/api/daily/explain", json={"report_date": str(REPORT_DATE), "scope": "day"})
        assert crew_service.calls == []

    def test_a_task_scope_request_calls_the_crew_suggestion_service_once(self, wired_client):
        client, llm_service, crew_service = wired_client(None)
        client.post("/api/daily/explain", json=TASK_PAYLOAD)
        assert len(crew_service.calls) == 1
        assert crew_service.calls[0]["well_id"] == 101
        assert crew_service.calls[0]["task_code"] == "AAAA0001-101"


class TestEligibleSuggestionReachesTheModel:
    def test_eligible_evidence_with_a_suggested_crew_is_sent_to_the_llm(self, wired_client):
        evidence = {
            "eligible": True,
            "suppression_reason": None,
            "current_crew": {"crew_id": None, "recorded": False},
            "suggested_crew": {
                "crew_id": 999,
                "historical_completed_task_count": 5,
                "distinct_completed_well_count": 5,
                "completed_on_incomplete_well_count": 0,
                "completed_on_completed_well_count": 5,
                "typical_completion_days": 2.0,
                "average_completion_days": 2.5,
                "evidence_strength": "STRONG_HISTORY",
                "derived_availability": "NO_CURRENT_UNFINISHED_TASK",
            },
        }
        client, llm_service, crew_service = wired_client(evidence)
        client.post("/api/daily/explain", json=TASK_PAYLOAD)

        assert len(llm_service.received_evidence) == 1
        assert llm_service.received_evidence[0]["crew_suggestion"] == evidence


class TestIneligibleSuggestionNeverReachesTheModel:
    """A prompt rule alone cannot guarantee a non-deterministic model stays
    silent -- so the ineligible evidence must never even reach the LLM call."""

    def test_ineligible_evidence_is_withheld_from_the_llm_call(self, wired_client):
        evidence = {
            "eligible": False,
            "suppression_reason": "Task is already in progress; crew suggestion suppressed.",
            "current_crew": {"crew_id": 42, "recorded": True},
        }
        client, llm_service, crew_service = wired_client(evidence)
        response = client.post("/api/daily/explain", json=TASK_PAYLOAD)

        assert "crew_suggestion" not in llm_service.received_evidence[0]
        # ...but the client still receives it, for transparency/audit.
        assert response.json()["evidence"]["crew_suggestion"] == evidence

    def test_completed_task_evidence_is_also_withheld_from_the_llm_call(self, wired_client):
        evidence = {
            "eligible": False,
            "suppression_reason": "Task already completed.",
            "suppression_code": "COMPLETED",
            "current_crew": {"crew_id": None, "recorded": False},
        }
        client, llm_service, crew_service = wired_client(evidence)
        client.post("/api/daily/explain", json=TASK_PAYLOAD)

        assert "crew_suggestion" not in llm_service.received_evidence[0]

    def test_in_progress_evidence_with_no_consult_crew_is_withheld(self, wired_client):
        evidence = {
            "eligible": False,
            "suppression_reason": "Task is already in progress; crew suggestion suppressed.",
            "suppression_code": "IN_PROGRESS",
            "current_crew": {"crew_id": 42, "recorded": True},
        }
        client, llm_service, crew_service = wired_client(evidence)
        client.post("/api/daily/explain", json=TASK_PAYLOAD)

        assert "crew_suggestion" not in llm_service.received_evidence[0]


class TestInProgressConsultCrewReachesTheModel:
    """A task already showing progress needs no replacement, but a proven
    historical crew is still worth an informational mention -- unlike a
    genuine ineligible case, this evidence DOES reach the LLM."""

    def test_in_progress_evidence_with_a_consult_crew_reaches_the_llm(self, wired_client):
        evidence = {
            "eligible": False,
            "suppression_reason": "Task is already in progress; crew suggestion suppressed.",
            "suppression_code": "IN_PROGRESS",
            "current_crew": {"crew_id": 42, "recorded": True},
            "consult_crew": {
                "crew_id": 999,
                "historical_completed_task_count": 5,
                "distinct_completed_well_count": 5,
                "completed_on_incomplete_well_count": 0,
                "completed_on_completed_well_count": 5,
                "typical_completion_days": 2.0,
                "average_completion_days": 2.5,
                "evidence_strength": "STRONG_HISTORY",
                "derived_availability": "NO_CURRENT_UNFINISHED_TASK",
            },
        }
        client, llm_service, crew_service = wired_client(evidence)
        client.post("/api/daily/explain", json=TASK_PAYLOAD)

        assert len(llm_service.received_evidence) == 1
        assert llm_service.received_evidence[0]["crew_suggestion"] == evidence
        assert "suggested_crew" not in llm_service.received_evidence[0]["crew_suggestion"]


class TestNoCrewSuggestionEvidenceAtAll:
    def test_when_the_target_task_cannot_be_resolved_no_key_is_added(self, wired_client):
        client, llm_service, crew_service = wired_client(None)
        response = client.post("/api/daily/explain", json=TASK_PAYLOAD)

        assert "crew_suggestion" not in llm_service.received_evidence[0]
        assert "crew_suggestion" not in response.json()["evidence"]


class TestCrewSuggestionFailureNeverBreaksTheTaskSummary:
    def test_a_crew_suggestion_exception_still_returns_the_task_explanation(self, wired_client, monkeypatch):
        class ExplodingCrewService(CrewSuggestionService):
            def __init__(self) -> None:
                pass

            def build(self, *, well_id, task_code, report_date):
                raise RuntimeError("simulated crew-suggestion failure")

        from app.main import app

        app.dependency_overrides[dependencies.get_crew_suggestion_service] = lambda: ExplodingCrewService()
        client, llm_service, _ = wired_client(None)
        # wired_client's _make() above already re-overrides get_crew_suggestion_service
        # with a StubCrewSuggestionService(None); re-apply the exploding one last.
        app.dependency_overrides[dependencies.get_crew_suggestion_service] = lambda: ExplodingCrewService()

        response = client.post("/api/daily/explain", json=TASK_PAYLOAD)

        assert response.status_code == 200
        assert response.json()["available"] is True
        assert "crew_suggestion" not in llm_service.received_evidence[0]


class TestNoDatabaseWritesFromThisFeature:
    def test_crew_repository_only_ever_calls_fetch_all(self):
        """CrewRepository must expose no write path at all."""
        import inspect

        from app.repositories.crew_repository import CrewRepository

        source = inspect.getsource(CrewRepository)
        for forbidden in ("INSERT", "UPDATE", "DELETE", "execute_write", "commit"):
            assert forbidden not in source
