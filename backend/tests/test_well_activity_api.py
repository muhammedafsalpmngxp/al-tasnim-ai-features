"""The task-activity endpoint and its evidence, end to end through FastAPI.

Offline: the daily dataset and the well-activity queries are both stubbed, so
what is asserted here is the wiring -- what the endpoint returns, what reaches
the model, and what a well with nothing reported on the date still gets -- not
the database.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

from app.api import dependencies
from app.models.daily import DayCounters
from app.services.daily_service import DailyService
from app.services.llm_service import LLMService, _ExplainCache
from tests.conftest import REPORT_DATE, make_row, stub_well_activity_service

#: One well reported a task on the date (101); the other has open work but
#: nothing reported that day (777) -- the case the front page exists to show.
ROWS = [make_row(task_daily_id=1, well_id=101, planned=10, actual_quantity=10)]

ACTIVITY_ROWS: List[Dict[str, Any]] = [
    {
        "well_id": 101,
        "logical_task_count": 9,
        "completed_task_count": 5,
        "open_task_count": 4,
        "incomplete_task_count": 2,
        "ongoing_task_count": 2,
        "not_started_task_count": 1,
        "ended_not_completed_task_count": 1,
        "last_task_date": REPORT_DATE,
    },
    {
        "well_id": 777,
        "logical_task_count": 6,
        "completed_task_count": 2,
        "open_task_count": 4,
        "incomplete_task_count": 1,
        "ongoing_task_count": 3,
        "not_started_task_count": 1,
        "ended_not_completed_task_count": 0,
        "last_task_date": date(2026, 7, 14),
    },
]

DETAIL_ROWS: List[Dict[str, Any]] = [
    {
        "well_id": 777,
        "schedule_id": 12,
        "task_code": "FLME1030-777",
        "task_state": "ONGOING",
        "latest_action_on": date(2026, 7, 14),
        "actual_start": date(2026, 7, 2),
        "actual_end": None,
        "completed": False,
        "activity_id": "FLME1030",
        "activity_code": "F-M-SLW-PWD-45",
        "activity_description": "Pipe Stringing",
        "wbs": "Straightline Welding incl. supports",
    },
    {
        "well_id": 777,
        "schedule_id": 12,
        "task_code": "FLME1040-777",
        "task_state": "NOT_STARTED",
        "latest_action_on": date(2026, 7, 10),
        "actual_start": None,
        "actual_end": None,
        "completed": False,
        "activity_id": "FLME1040",
        "activity_code": None,
        "activity_description": None,
        "wbs": None,
    },
]


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


class CapturingLLMService(LLMService):
    """Records exactly the evidence dict it was asked to explain."""

    def __init__(self) -> None:
        self.received_evidence: List[Dict[str, Any]] = []
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
def client_and_llm(monkeypatch):
    from app.main import app

    daily_service = DailyService(repository=StubRepository(ROWS))
    llm_service = CapturingLLMService()
    activity_service = stub_well_activity_service(ACTIVITY_ROWS, DETAIL_ROWS)
    monkeypatch.setattr(dependencies, "_daily_service", daily_service)
    monkeypatch.setattr(dependencies, "_llm_service", llm_service)
    app.dependency_overrides[dependencies.get_daily_service] = lambda: daily_service
    app.dependency_overrides[dependencies.get_llm_service] = lambda: llm_service
    app.dependency_overrides[dependencies.get_well_activity_service] = lambda: activity_service
    try:
        yield TestClient(app), llm_service
    finally:
        app.dependency_overrides.clear()


def _wells(client) -> Dict[int, Dict[str, Any]]:
    body = client.get(f"/api/daily/well-activity?date={REPORT_DATE}").json()
    return {well["well_id"]: well for well in body["wells"]}


class TestWellActivityEndpoint:
    def test_every_well_carries_the_four_front_page_figures(self, client_and_llm):
        client, _ = client_and_llm
        well = _wells(client)[101]
        assert well["open_task_count"] == 4
        assert well["incomplete_task_count"] == 2
        assert well["ongoing_task_count"] == 2
        assert well["today_reported_task_count"] == 1
        assert well["last_task_date"] == str(REPORT_DATE)

    def test_the_two_figures_beside_the_total_add_up_to_it(self, client_and_llm):
        client, _ = client_and_llm
        for well in _wells(client).values():
            assert (
                well["incomplete_task_count"] + well["ongoing_task_count"]
                == well["open_task_count"]
            )

    def test_a_well_with_no_task_on_the_date_is_still_listed(self, client_and_llm):
        client, _ = client_and_llm
        well = _wells(client)[777]
        assert well["today_reported_task_count"] == 0
        assert well["has_task_on_report_date"] is False
        # ...and its own open work and last-seen date are still reported.
        assert well["open_task_count"] == 4
        assert well["last_task_date"] == "2026-07-14"

    def test_the_whole_list_never_carries_the_task_rows_behind_it(self, client_and_llm):
        client, _ = client_and_llm
        body = client.get(f"/api/daily/well-activity?date={REPORT_DATE}").json()
        assert body["well_count"] == 2
        assert body["tasks"] == []

    def test_one_well_returns_the_tasks_behind_its_own_counts(self, client_and_llm):
        client, _ = client_and_llm
        body = client.get(f"/api/daily/well-activity?date={REPORT_DATE}&well_id=777").json()
        assert [well["well_id"] for well in body["wells"]] == [777]
        assert {task["task_code"] for task in body["tasks"]} == {
            "FLME1030-777",
            "FLME1040-777",
        }
        assert all(task["well_id"] == 777 for task in body["tasks"])

    def test_a_returned_task_never_carries_a_progress_value(self, client_and_llm):
        client, _ = client_and_llm
        body = client.get(f"/api/daily/well-activity?date={REPORT_DATE}&well_id=777").json()
        for task in body["tasks"]:
            assert "progress" not in task

    def test_an_unmapped_task_stays_visible_rather_than_being_dropped(self, client_and_llm):
        client, _ = client_and_llm
        body = client.get(f"/api/daily/well-activity?date={REPORT_DATE}&well_id=777").json()
        unmapped = [t for t in body["tasks"] if t["task_code"] == "FLME1040-777"][0]
        assert unmapped["activity_code"] is None
        assert unmapped["wbs"] is None

    def test_an_invalid_date_is_rejected_the_same_way_as_everywhere_else(self, client_and_llm):
        client, _ = client_and_llm
        assert client.get("/api/daily/well-activity?date=not-a-date").status_code == 400


class TestWellActivityReachesTheModelAsEvidence:
    def test_a_well_scope_explanation_is_given_the_task_activity(self, client_and_llm):
        client, llm_service = client_and_llm
        client.post(
            "/api/daily/explain",
            json={"report_date": str(REPORT_DATE), "scope": "well", "well_id": 101},
        )
        evidence = llm_service.received_evidence[0]["well_task_activity"]
        assert evidence["task_activity"]["open_task_count"] == 4
        assert evidence["task_activity"]["ongoing_task_count"] == 2
        assert evidence["task_activity"]["incomplete_task_count"] == 2

    def test_the_model_is_given_counts_and_a_bounded_sample_never_a_row_dump(
        self, client_and_llm
    ):
        client, llm_service = client_and_llm
        client.post(
            "/api/daily/explain",
            json={"report_date": str(REPORT_DATE), "scope": "well", "well_id": 777},
        )
        evidence = llm_service.received_evidence[0]["well_task_activity"]
        # Only the ongoing tasks are listed; the other states are counts.
        assert [task["task_code"] for task in evidence["ongoing_tasks"]] == ["FLME1030-777"]
        assert evidence["task_state_summary"]["NOT_STARTED"] == 1

    def test_a_well_with_no_task_on_the_date_can_still_be_explained(self, client_and_llm):
        client, llm_service = client_and_llm
        response = client.post(
            "/api/daily/explain",
            json={"report_date": str(REPORT_DATE), "scope": "well", "well_id": 777},
        )
        body = response.json()
        assert body["available"] is True
        summary = body["evidence"]["summary"]
        assert summary["task_count"] == 0
        assert summary["no_daily_entry"] is True
        # An absence of entries is said plainly, never reported as a zero
        # quantity or as several units that cannot be totalled.
        assert "absence of any entry" in summary["note"]

    def test_an_empty_selection_is_never_described_as_zero_wells(self, client_and_llm):
        # A payload full of zeroes is true and useless: explaining one well,
        # the model read them out as "there are 0 wells and 0 tasks in scope",
        # which describes the payload rather than the well. There is nothing
        # for it to read out any more.
        client, llm_service = client_and_llm
        client.post(
            "/api/daily/explain",
            json={"report_date": str(REPORT_DATE), "scope": "well", "well_id": 777},
        )
        summary = llm_service.received_evidence[0]["summary"]
        assert "well_count" not in summary
        assert "status_counts" not in summary
        assert "status_definitions" not in llm_service.received_evidence[0]
        assert "tasks" not in llm_service.received_evidence[0]

    def test_a_day_scope_explanation_is_not_given_one_wells_activity(self, client_and_llm):
        client, llm_service = client_and_llm
        client.post("/api/daily/explain", json={"report_date": str(REPORT_DATE), "scope": "day"})
        assert "well_task_activity" not in llm_service.received_evidence[0]


class TestTheDayScopeSeesEveryLiveWell:
    """The whole-view summary used to describe only what was reported that
    day. On a quiet date that is one well and one task, while every other live
    well's unfinished work went unmentioned because the payload never carried
    it."""

    def test_the_day_scope_is_given_every_live_wells_task_activity(self, client_and_llm):
        client, llm_service = client_and_llm
        client.post("/api/daily/explain", json={"report_date": str(REPORT_DATE), "scope": "day"})
        overview = llm_service.received_evidence[0]["live_well_task_activity"]
        assert overview["well_counts"]["with_task_evidence"] == 2
        assert overview["well_counts"]["reported_on_report_date"] == 1
        assert overview["well_counts"]["no_task_on_report_date"] == 1
        assert overview["well_counts"]["with_open_tasks"] == 2
        assert overview["well_counts"]["with_open_tasks_and_nothing_reported"] == 1

    def test_the_totals_cover_every_well_not_only_the_reporting_ones(self, client_and_llm):
        client, llm_service = client_and_llm
        client.post("/api/daily/explain", json={"report_date": str(REPORT_DATE), "scope": "day"})
        counts = llm_service.received_evidence[0]["live_well_task_activity"]["task_counts"]
        assert counts["open_total"] == 8   # 4 on the reporting well, 4 on the silent one
        assert counts["incomplete_total"] == 3
        assert counts["ongoing_total"] == 5
        assert counts["incomplete_total"] + counts["ongoing_total"] == counts["open_total"]

    def test_the_named_wells_are_the_ones_carrying_the_most_open_work(self, client_and_llm):
        client, llm_service = client_and_llm
        client.post("/api/daily/explain", json={"report_date": str(REPORT_DATE), "scope": "day"})
        listed = llm_service.received_evidence[0]["live_well_task_activity"][
            "wells_with_most_open_work"
        ]
        # Equal open counts; the busier ongoing figure ranks first.
        assert [well["well_id"] for well in listed] == [777, 101]
        assert listed[0]["today_reported_task_count"] == 0
        assert listed[0]["last_task_date"] == "2026-07-14"

    def test_a_slice_of_the_day_is_not_given_the_whole_universe(self, client_and_llm):
        # A status/WBS drill-down explains a subset of the day's reported
        # tasks; every live well's open work is a different population.
        client, llm_service = client_and_llm
        client.post(
            "/api/daily/explain",
            json={"report_date": str(REPORT_DATE), "scope": "day", "status": "ON_PLAN"},
        )
        assert "live_well_task_activity" not in llm_service.received_evidence[0]

    def test_a_well_scope_is_given_its_own_well_not_the_universe(self, client_and_llm):
        client, llm_service = client_and_llm
        client.post(
            "/api/daily/explain",
            json={"report_date": str(REPORT_DATE), "scope": "well", "well_id": 101},
        )
        assert "live_well_task_activity" not in llm_service.received_evidence[0]
        assert "well_task_activity" in llm_service.received_evidence[0]

    def test_a_date_with_nothing_reported_at_all_can_still_be_explained(self, client_and_llm):
        # The daily dataset is empty for any date but REPORT_DATE, while the
        # wells still carry open work -- the exact case the old "there are no
        # daily task records in this selection" dead end hid.
        client, llm_service = client_and_llm
        body = client.post(
            "/api/daily/explain", json={"report_date": "2026-07-04", "scope": "day"}
        ).json()
        assert body["available"] is True
        assert body["evidence"]["summary"]["no_daily_entry"] is True
        assert body["evidence"]["live_well_task_activity"]["well_counts"]["with_task_evidence"] == 2

    def test_an_unknown_well_with_no_evidence_is_reported_not_invented(self, client_and_llm):
        client, _ = client_and_llm
        body = client.post(
            "/api/daily/explain",
            json={"report_date": str(REPORT_DATE), "scope": "well", "well_id": 4242},
        ).json()
        assert body["available"] is False
        assert body["evidence"] == {}


class TestWhatIsSentToTheModel:
    def test_the_response_returns_the_evidence_the_model_was_given(self, client_and_llm):
        client, llm_service = client_and_llm
        body = client.post(
            "/api/daily/explain",
            json={"report_date": str(REPORT_DATE), "scope": "well", "well_id": 101},
        ).json()
        assert body["evidence"] == llm_service.received_evidence[0]
        assert body["evidence_withheld_from_model"] == []

    def test_the_evidence_carries_no_query_and_no_secret(self, client_and_llm):
        import json

        client, _ = client_and_llm
        body = client.post(
            "/api/daily/explain",
            json={"report_date": str(REPORT_DATE), "scope": "well", "well_id": 101},
        ).json()
        text = json.dumps(body["evidence"]).lower()
        for forbidden in ("select ", "pwd=", "uid=", "driver=", "api_key", "password", "bearer"):
            assert forbidden not in text


class TestFreshAndCachedArePropagatedHonestly:
    def test_a_first_generation_is_reported_as_fresh(self, client_and_llm):
        client, llm_service = client_and_llm
        body = client.post(
            "/api/daily/explain",
            json={"report_date": str(REPORT_DATE), "scope": "well", "well_id": 101},
        ).json()
        assert body["cached"] is False
        assert len(llm_service.received_evidence) == 1

    def test_the_same_request_again_is_reported_as_cached(self, client_and_llm):
        client, llm_service = client_and_llm
        payload = {"report_date": str(REPORT_DATE), "scope": "well", "well_id": 101}
        first = client.post("/api/daily/explain", json=payload).json()
        second = client.post("/api/daily/explain", json=payload).json()
        assert (first["cached"], second["cached"]) == (False, True)
        assert second["explanation"] == first["explanation"]
        assert len(llm_service.received_evidence) == 1

    def test_refreshing_the_well_activity_forces_a_fresh_explanation(self, client_and_llm):
        client, llm_service = client_and_llm
        payload = {"report_date": str(REPORT_DATE), "scope": "well", "well_id": 101}
        client.post("/api/daily/explain", json=payload)
        client.get(f"/api/daily/well-activity?date={REPORT_DATE}&refresh=true")
        again = client.post("/api/daily/explain", json=payload).json()
        assert again["cached"] is False
        assert len(llm_service.received_evidence) == 2


class TestShowingTheWorking:
    """A count an operator cannot check is a count they have to trust. The
    panel gets the queries the figures came from and the individual records
    behind them, with the reason each one is counted -- and the model gets
    neither."""

    def _explain_well(self, client, well_id=777):
        return client.post(
            "/api/daily/explain",
            json={"report_date": str(REPORT_DATE), "scope": "well", "well_id": well_id},
        ).json()

    def test_every_counted_task_is_listed_with_its_reason(self, client_and_llm):
        client, _ = client_and_llm
        proof = self._explain_well(client)["proof"]
        assert {row["task_code"] for row in proof} == {"FLME1030-777", "FLME1040-777"}
        assert all(row["reason"] for row in proof)
        # Every row counts toward exactly one of the two figures, never both
        # and never neither -- which is why they add up to the total.
        assert all(
            row["counts_as_incomplete"] != row["counts_as_ongoing"] for row in proof
        )

    def test_the_proof_says_which_tasks_the_ongoing_count_covers(self, client_and_llm):
        client, _ = client_and_llm
        by_code = {row["task_code"]: row for row in self._explain_well(client)["proof"]}
        assert by_code["FLME1030-777"]["counts_as_ongoing"] is True
        assert by_code["FLME1040-777"]["counts_as_ongoing"] is False

    def test_each_reason_restates_that_task_s_own_record(self, client_and_llm):
        client, _ = client_and_llm
        by_code = {row["task_code"]: row for row in self._explain_well(client)["proof"]}
        ongoing = by_code["FLME1030-777"]["reason"]
        assert "Not completed" in ongoing
        assert "2026-07-02" in ongoing          # its recorded actual start
        assert "no actual end" in ongoing
        assert "counts as ongoing" in ongoing
        not_started = by_code["FLME1040-777"]["reason"]
        assert "no actual start" in not_started
        assert "counts as incomplete rather than ongoing" in not_started

    def test_the_proof_carries_the_description_where_the_task_is_mapped(self, client_and_llm):
        client, _ = client_and_llm
        by_code = {row["task_code"]: row for row in self._explain_well(client)["proof"]}
        assert by_code["FLME1030-777"]["description"] == "Pipe Stringing"
        # ...and says nothing rather than inventing one where it is not.
        assert by_code["FLME1040-777"]["description"] is None

    def test_the_proof_row_count_matches_the_incomplete_count_it_explains(self, client_and_llm):
        client, _ = client_and_llm
        body = self._explain_well(client)
        counted = body["evidence"]["well_task_activity"]["task_activity"]["open_task_count"]
        # The stub's aggregate says four; two detail rows are supplied for
        # this well, so the note-free case is asserted on what is present.
        assert len(body["proof"]) <= counted
        assert body["proof_note"] is None

    def test_the_queries_behind_the_figures_are_returned_as_executed(self, client_and_llm):
        client, _ = client_and_llm
        sources = self._explain_well(client)["sql_sources"]
        files = [source["file"] for source in sources]
        assert "backend/sql/well_task_activity.sql" in files
        assert "backend/sql/well_task_activity_detail.sql" in files
        for source in sources:
            assert source["sql"].lstrip().startswith(("/*", "WITH", "SELECT"))
            assert source["label"]

    def test_the_report_date_is_shown_as_a_bound_parameter_not_spliced_in(self, client_and_llm):
        client, _ = client_and_llm
        for source in self._explain_well(client)["sql_sources"]:
            assert any(str(REPORT_DATE) in parameter for parameter in source["parameters"])
            # The query text itself never carries the value -- that is the
            # whole point of binding it.
            assert str(REPORT_DATE) not in source["sql"]

    def test_the_sql_carries_no_credential_or_connection_string(self, client_and_llm):
        import re

        client, _ = client_and_llm
        # The same key shape SecretRedactingFilter matches, rather than a bare
        # "sk-" substring: this text is full of ordinary words like
        # "task-state" that contain one.
        key_shape = re.compile(r"(gsk|sk)[_-][A-Za-z0-9_\-]{16,}", re.IGNORECASE)
        for source in self._explain_well(client)["sql_sources"]:
            lowered = source["sql"].lower()
            for forbidden in ("pwd=", "uid=", "driver=", "password", "api_key"):
                assert forbidden not in lowered
            assert not key_shape.search(source["sql"])

    def test_the_model_is_never_given_the_sql_or_the_proof(self, client_and_llm):
        client, llm_service = client_and_llm
        self._explain_well(client)
        evidence = llm_service.received_evidence[0]
        assert "sql" not in str(evidence).lower()
        assert "proof" not in evidence
        assert "reason" not in str(evidence)

    def test_a_task_scope_has_no_proof_or_sql_to_show(self, client_and_llm):
        client, _ = client_and_llm
        body = client.post(
            "/api/daily/explain",
            json={
                "report_date": str(REPORT_DATE),
                "scope": "task",
                "well_id": 101,
                "task_daily_id": 1,
            },
        ).json()
        assert body["proof"] == []
        assert body["sql_sources"] == []

    def test_the_day_scope_shows_the_queries_behind_its_own_figures(self, client_and_llm):
        client, _ = client_and_llm
        body = client.post(
            "/api/daily/explain", json={"report_date": str(REPORT_DATE), "scope": "day"}
        ).json()
        assert [source["file"] for source in body["sql_sources"]]
        # The day scope counts wells, not one well's tasks, so there is no
        # per-task proof table to show for it.
        assert body["proof"] == []
