"""Two front-page behaviours.

* Opening a well that reported nothing on the date, but is still a live well
  with open work, lands on that open work instead of a 404 -- the front page
  lists exactly those wells on a day with no tasks.
* The front page's own whole-day summary runs on LLM_DAY_MODEL; every per-well
  and per-task summary stays on LLM_MODEL, and the two never share a cached
  answer.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import dependencies
from app.config.settings import get_settings
from app.services.daily_service import DailyService
from app.services.llm_service import LLMService, _evidence_hash, _is_reasoning_model
from tests.conftest import REPORT_DATE, make_row, stub_well_activity_service
from tests.test_api_and_llm import StubRepository

#: Reported a task on the date.
REPORTING_WELL = 101
#: Reported nothing on the date, but still carries open work.
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

DETAIL = [
    {
        "well_id": QUIET_WELL,
        "schedule_id": 12,
        "task_code": "FLME1180-30349",
        "task_state": "ONGOING",
        "latest_action_on": date(2026, 7, 20),
        "actual_start": date(2026, 7, 10),
        "actual_end": None,
        "completed": False,
        "activity_id": "FLME1180",
        "activity_code": "FL-ME-ML08-03",
        "activity_description": "Pipe Stringing, Alignment, Fitup and Welding",
        "wbs": "Straightline Welding incl. supports",
    }
]


@pytest.fixture
def client(monkeypatch) -> TestClient:
    from app.main import app

    service = DailyService(repository=StubRepository(ROWS))
    activity_service = stub_well_activity_service(ACTIVITY, DETAIL)
    monkeypatch.setattr(dependencies, "_daily_service", service)
    app.dependency_overrides[dependencies.get_daily_service] = lambda: service
    app.dependency_overrides[dependencies.get_well_activity_service] = lambda: activity_service
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


class TestWellDetailWithoutTasksOnTheDate:
    def test_a_live_well_with_open_work_is_not_a_404(self, client):
        response = client.get(f"/api/daily/well/{QUIET_WELL}?date={REPORT_DATE}")
        assert response.status_code == 200
        body = response.json()
        assert body["well_id"] == QUIET_WELL
        assert body["task_count"] == 0
        assert body["tasks"] == []
        assert body["activity"]["open_task_count"] == 3
        assert body["activity"]["ongoing_task_count"] == 2
        assert [task["task_code"] for task in body["open_tasks"]] == ["FLME1180-30349"]

    def test_a_reporting_well_is_unchanged(self, client):
        body = client.get(f"/api/daily/well/{REPORTING_WELL}?date={REPORT_DATE}").json()
        assert body["task_count"] == 1
        assert body["activity"] is None
        assert body["open_tasks"] == []

    def test_a_well_with_no_evidence_at_all_is_still_a_404(self, client):
        response = client.get(f"/api/daily/well/999999?date={REPORT_DATE}")
        assert response.status_code == 404


class TestDayModel:
    @pytest.fixture(autouse=True)
    def _models(self, monkeypatch):
        get_settings.cache_clear()
        monkeypatch.setenv("LLM_API_KEY", "test-key")
        monkeypatch.setenv("LLM_MODEL", "small-model")
        monkeypatch.setenv("LLM_DAY_MODEL", "gpt-5-day-model")
        monkeypatch.setenv("LLM_DAY_MAX_TOKENS", "4000")
        monkeypatch.setenv("LLM_BASE_URL", "https://example.invalid/v1")
        yield
        get_settings.cache_clear()

    @pytest.fixture
    def sent(self, monkeypatch) -> List[Dict[str, Any]]:
        payloads: List[Dict[str, Any]] = []

        def fake_post(url, *, headers, json, timeout):
            payloads.append(json)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "Summary."}}]},
                request=httpx.Request("POST", url),
            )

        monkeypatch.setattr(httpx, "post", fake_post)
        return payloads

    def test_the_front_page_day_summary_uses_the_day_model(self, client, sent):
        body = client.post(
            "/api/daily/explain", json={"report_date": str(REPORT_DATE), "scope": "day"}
        ).json()
        assert body["model"] == "gpt-5-day-model"
        assert sent[-1]["model"] == "gpt-5-day-model"
        # A reasoning model takes max_completion_tokens and no temperature.
        assert sent[-1]["max_completion_tokens"] == 4000
        assert "max_tokens" not in sent[-1]
        assert "temperature" not in sent[-1]

    def test_a_well_summary_stays_on_the_default_model(self, client, sent):
        body = client.post(
            "/api/daily/explain",
            json={"report_date": str(REPORT_DATE), "scope": "well", "well_id": QUIET_WELL},
        ).json()
        assert body["available"] is True
        assert body["model"] == "small-model"
        assert sent[-1]["model"] == "small-model"
        assert "temperature" in sent[-1]
        assert "max_tokens" in sent[-1]

    def test_a_filtered_day_request_stays_on_the_default_model(self, client, sent):
        body = client.post(
            "/api/daily/explain",
            json={"report_date": str(REPORT_DATE), "scope": "day", "well_id": REPORTING_WELL},
        ).json()
        assert body["model"] == "small-model"

    def test_well_summary_for_a_quiet_well_is_reused_on_its_own_page(self, client, sent):
        """The front-page row and the well's own page send the identical
        request, so the second is answered from cache with the same text."""
        # The explain endpoint's LLM service is a process-wide singleton, so an
        # earlier test in this run may already have cached this very answer.
        dependencies._llm_service.invalidate_date(REPORT_DATE)
        request = {"report_date": str(REPORT_DATE), "scope": "well", "well_id": QUIET_WELL}
        first = client.post("/api/daily/explain", json=request).json()
        second = client.post("/api/daily/explain", json=request).json()
        assert first["cached"] is False
        assert second["cached"] is True
        assert second["explanation"] == first["explanation"]
        assert len(sent) == 1


def test_the_same_evidence_under_two_models_never_shares_a_cache_entry():
    evidence = {"report_date": "2026-08-01", "scope": "day", "summary": {"task_count": 1}}
    assert _evidence_hash(evidence, "a") != _evidence_hash(evidence, "b")
    assert _evidence_hash(evidence, "a") == _evidence_hash(evidence, "a")


@pytest.mark.parametrize(
    "model, expected",
    [
        ("gpt-5.6-luna", True),
        ("gpt-5-mini", True),
        ("o3-mini", True),
        ("gpt-4o-mini", False),
        ("openai/gpt-oss-120b", False),
    ],
)
def test_reasoning_models_are_recognised(model, expected):
    assert _is_reasoning_model(model) is expected


def test_explain_safe_without_overrides_calls_explain_as_before(monkeypatch):
    """Stubs written as ``lambda self, evidence`` must keep working."""
    get_settings.cache_clear()
    monkeypatch.setattr(LLMService, "explain", lambda self, evidence: ("stub", "stub-model"))
    service = LLMService()
    result = service.explain_safe({"report_date": "2026-08-01", "scope": "x"})
    assert result["explanation"] == "stub"
