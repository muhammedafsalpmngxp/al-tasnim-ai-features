"""API behaviour, drill-down traceability and LLM failure containment.

These tests stub the repository, so they assert the application's behaviour
without depending on the database.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from app.api import dependencies
from app.models.daily import DayCounters, QuantityStatus
from app.services.daily_service import DailyService
from app.services.evidence_service import EvidenceService
from app.services.llm_service import LLMService, LLMUnavailable
from tests.conftest import REPORT_DATE, make_row

OTHER_DATE = date(2026, 8, 2)


class StubRepository:
    """Returns fixed rows for one date and nothing for any other."""

    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows
        self.calls = 0

    def fetch_day(self, report_date: date):
        self.calls += 1
        if report_date != REPORT_DATE:
            return [], DayCounters()
        rows = [dict(row, action_on=report_date) for row in self._rows]
        return rows, DayCounters(raw_row_count=len(rows), logical_task_count=len(rows))

    def fetch_dates_with_activity(self, limit: int):
        return [{"report_date": REPORT_DATE, "row_count": len(self._rows), "well_count": 2}]


ROWS = [
    make_row(task_daily_id=1, well_id=101, uom_code="m3", uom_id=11,
             wbs="Tank Construction", activity_code="LC-TK-01",
             planned=30, actual_quantity=30),
    make_row(task_daily_id=2, well_id=102, uom_code="m3", uom_id=11,
             wbs="Tank Construction", activity_code="LC-TK-01",
             planned=30, actual_quantity=25),
    make_row(task_daily_id=3, well_id=103, uom_code="m3", uom_id=11,
             wbs="Tank Construction", activity_code="LC-TK-01",
             planned=30, actual_quantity=None, actual_quantity_raw=None,
             is_actual_entry=0, group_actual_entry_count=0),
    make_row(task_daily_id=4, well_id=104, uom_code="Joint", uom_id=3,
             wbs="Straightline Welding incl. supports", activity_code="FL-ME-ML08-03",
             planned=15, actual_quantity=18),
    # Same status as task 1 but a different unit, so the ON_PLAN status group
    # spans two UOM while each work category under it stays within one.
    make_row(task_daily_id=5, well_id=105, uom_code="Joint", uom_id=3,
             wbs="Straightline Welding incl. supports", activity_code="FL-ME-ML08-03",
             planned=10, actual_quantity=10),
]


@pytest.fixture
def client(monkeypatch) -> TestClient:
    from app.main import app

    service = DailyService(repository=StubRepository(ROWS))
    monkeypatch.setattr(dependencies, "_daily_service", service)
    app.dependency_overrides[dependencies.get_daily_service] = lambda: service
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


class TestSummaryAndDrillDown:
    def test_summary_groups_by_status_then_wbs(self, client):
        body = client.get(f"/api/daily/summary?date={REPORT_DATE}").json()
        statuses = [group["status"] for group in body["status_groups"]]
        # NOT_VALIDATED has no tasks today, so it has no section -- the day
        # totals still report it as a zero.
        assert statuses == ["ON_PLAN", "ABOVE_PLAN", "BELOW_PLAN", "NO_ACTUAL"]
        assert body["totals"]["status_counts"][QuantityStatus.NOT_VALIDATED.value] == 0

    def test_status_sections_keep_a_fixed_order_whatever_the_data(self, client):
        """The brief must read the same way every morning.

        Section order comes from QuantityStatus, never from how busy a status
        happens to be, so an operator always finds Below Plan in the same place.
        """
        body = client.get(f"/api/daily/summary?date={REPORT_DATE}").json()
        statuses = [group["status"] for group in body["status_groups"]]
        expected = [m.value for m in QuantityStatus if m.value in statuses]
        assert statuses == expected

    def test_summary_counts_are_deterministic(self, client):
        body = client.get(f"/api/daily/summary?date={REPORT_DATE}").json()
        groups = {g["status"]: g for g in body["status_groups"]}

        on_plan = groups["ON_PLAN"]
        assert on_plan["well_count"] == 2
        assert on_plan["task_count"] == 2

        below_plan = groups["BELOW_PLAN"]
        assert below_plan["task_count"] == 1
        assert below_plan["uom_code"] == "m3"
        assert below_plan["planned_quantity"] == "30"
        assert below_plan["actual_quantity"] == "25"

    def test_a_status_spanning_two_uom_withholds_its_totals(self, client):
        """A status group can mix units; a total across them would be invented.

        The work categories nested under it each stay within one unit, so their
        own totals are still reported -- the figure is withheld exactly where it
        would have meant nothing, and nowhere else.
        """
        body = client.get(f"/api/daily/summary?date={REPORT_DATE}").json()
        on_plan = next(g for g in body["status_groups"] if g["status"] == "ON_PLAN")

        assert on_plan["quantities_summable"] is False
        assert on_plan["uom_code"] is None
        assert on_plan["uom_codes"] == ["Joint", "m3"]
        assert on_plan["planned_quantity"] is None
        assert on_plan["actual_quantity"] is None

        for wbs_group in on_plan["wbs_groups"]:
            assert wbs_group["quantities_summable"] is True
            assert wbs_group["planned_quantity"] is not None

    def test_every_group_carries_the_status_of_its_section(self, client):
        """Nothing under a status section belongs to another status.

        This is what lets the nested levels drop their status breakdown: there
        is only ever one status below a section head.
        """
        body = client.get(f"/api/daily/summary?date={REPORT_DATE}").json()
        for status_group in body["status_groups"]:
            for wbs_group in status_group["wbs_groups"]:
                assert wbs_group["status"] == status_group["status"]
                for activity in wbs_group["activities"]:
                    assert activity["status"] == status_group["status"]

    def test_clicking_a_work_category_returns_exactly_those_wells(self, client):
        summary = client.get(f"/api/daily/summary?date={REPORT_DATE}").json()
        on_plan = next(g for g in summary["status_groups"] if g["status"] == "ON_PLAN")
        wbs = on_plan["wbs_groups"][0]

        body = client.get(
            "/api/daily/group-details",
            params={"date": str(REPORT_DATE), "status": "ON_PLAN", "wbs": wbs["wbs"]},
        ).json()
        assert body["task_count"] == wbs["task_count"]
        assert body["well_count"] == wbs["well_count"]
        assert all(task["quantity_status"] == "ON_PLAN" for task in body["tasks"])

    def test_every_summary_count_is_traceable_to_its_rows(self, client):
        summary = client.get(f"/api/daily/summary?date={REPORT_DATE}").json()
        for status_group in summary["status_groups"]:
            status = status_group["status"]

            body = client.get(
                "/api/daily/group-details",
                params={"date": str(REPORT_DATE), "status": status},
            ).json()
            assert body["task_count"] == status_group["task_count"], status

            for wbs_group in status_group["wbs_groups"]:
                body = client.get(
                    "/api/daily/group-details",
                    params={"date": str(REPORT_DATE), "status": status,
                            "wbs": wbs_group["wbs"]},
                ).json()
                assert body["task_count"] == wbs_group["task_count"], (status, wbs_group["wbs"])

                for activity in wbs_group["activities"]:
                    body = client.get(
                        "/api/daily/group-details",
                        params={"date": str(REPORT_DATE), "status": status,
                                "wbs": wbs_group["wbs"],
                                "activity_code": activity["activity_code"]},
                    ).json()
                    assert body["task_count"] == activity["task_count"], (
                        status, wbs_group["wbs"], activity["activity_code"]
                    )

    def test_the_sections_account_for_every_task_in_the_day(self, client):
        """No task may fall outside the grouping.

        Every task carries a status, so unlike the old UOM hierarchy there is
        no "not recorded" bucket to fall into at the top level.
        """
        body = client.get(f"/api/daily/summary?date={REPORT_DATE}").json()
        grouped = sum(group["task_count"] for group in body["status_groups"])
        assert grouped == body["totals"]["task_count"] == len(ROWS)

    def test_view_mode_comes_from_configuration(self, client):
        body = client.get(f"/api/daily/summary?date={REPORT_DATE}").json()
        assert body["view_mode"] in {"detail", "grouped"}
        assert body["detail_view_task_threshold"] >= 1
        if body["view_mode"] == "detail":
            assert len(body["tasks"]) == body["totals"]["task_count"]

    def test_unknown_status_is_rejected(self, client):
        response = client.get(
            "/api/daily/group-details",
            params={"date": str(REPORT_DATE), "status": "NOT_A_STATUS"},
        )
        assert response.status_code == 400

    def test_well_detail_lists_each_task_separately(self, client):
        body = client.get(f"/api/daily/well/101?date={REPORT_DATE}").json()
        assert body["well_id"] == 101
        assert body["task_count"] == 1
        assert body["tasks"][0]["quantity_status"] == "ON_PLAN"

    def test_unknown_well_returns_404(self, client):
        response = client.get(f"/api/daily/well/999999?date={REPORT_DATE}")
        assert response.status_code == 404


class TestDateHandling:
    def test_only_the_requested_date_is_returned(self, client):
        body = client.get(f"/api/daily/details?date={REPORT_DATE}").json()
        assert body["task_count"] == len(ROWS)
        assert {task["action_on"] for task in body["tasks"]} == {str(REPORT_DATE)}

    def test_a_date_with_no_records_is_an_empty_report_not_an_error(self, client):
        response = client.get(f"/api/daily/summary?date={OTHER_DATE}")
        assert response.status_code == 200
        body = response.json()
        assert body["totals"]["task_count"] == 0
        assert body["status_groups"] == []

    def test_malformed_date_is_rejected(self, client):
        assert client.get("/api/daily/summary?date=11-09-2026").status_code == 400
        assert client.get("/api/daily/summary?date=2026-13-45").status_code == 400

    def test_omitting_the_date_defaults_to_today(self, client):
        response = client.get("/api/daily/summary")
        assert response.status_code == 200
        assert response.json()["report_date"] == str(date.today())


class TestExport:
    def test_export_returns_a_workbook_named_for_the_date(self, client):
        response = client.get(f"/api/daily/export?date={REPORT_DATE}")
        assert response.status_code == 200
        assert f"daily_morning_brief_{REPORT_DATE}.xlsx" in response.headers["content-disposition"]
        assert response.content[:2] == b"PK"  # a real xlsx container

    def test_export_of_an_empty_day_still_produces_a_workbook(self, client):
        response = client.get(f"/api/daily/export?date={OTHER_DATE}")
        assert response.status_code == 200
        assert response.content[:2] == b"PK"


class TestLLMFailure:
    """An LLM failure must never take the dashboard with it."""

    def test_explain_reports_unavailable_without_fabricating_text(self, client, monkeypatch):
        def boom(self, evidence):
            raise LLMUnavailable("The AI explanation service could not be reached.")

        monkeypatch.setattr(LLMService, "explain", boom)
        body = client.post(
            "/api/daily/explain",
            json={"report_date": str(REPORT_DATE), "scope": "group", "uom": "m3"},
        ).json()
        assert body["available"] is False
        assert body["explanation"] is None
        assert "could not be reached" in body["error"]
        # The deterministic evidence is still returned and still correct.
        assert body["evidence"]["summary"]["task_count"] == 3

    def test_an_unexpected_llm_error_is_contained(self, client, monkeypatch):
        def boom(self, evidence):
            raise RuntimeError("connection reset")

        monkeypatch.setattr(LLMService, "explain", boom)
        response = client.post(
            "/api/daily/explain",
            json={"report_date": str(REPORT_DATE), "scope": "day"},
        )
        assert response.status_code == 200
        assert response.json()["available"] is False

    def test_the_dashboard_still_works_while_the_llm_is_down(self, client, monkeypatch):
        monkeypatch.setattr(
            LLMService, "explain", lambda self, e: (_ for _ in ()).throw(LLMUnavailable("down"))
        )
        assert client.get(f"/api/daily/summary?date={REPORT_DATE}").status_code == 200
        assert client.get(f"/api/daily/details?date={REPORT_DATE}").status_code == 200
        assert client.get(f"/api/daily/export?date={REPORT_DATE}").status_code == 200


class TestEvidencePayload:
    def test_evidence_carries_classifications_never_raw_sql(self, client, monkeypatch):
        # This test only inspects the evidence payload, never the generated
        # text -- stubbing explain() keeps it offline (this suite's own
        # stated principle, see conftest.py) rather than silently spending a
        # real token budget against whatever provider backend/.env names.
        monkeypatch.setattr(LLMService, "explain", lambda self, evidence: ("stub", "stub-model"))
        body = client.post(
            "/api/daily/explain",
            json={"report_date": str(REPORT_DATE), "scope": "group", "uom": "m3"},
        ).json()
        evidence = body["evidence"]
        assert evidence["summary"]["planned_quantity"] == "90"
        assert evidence["summary"]["actual_quantity"] == "55"
        assert all("quantity_status" in task for task in evidence["tasks"])
        assert "SELECT" not in str(evidence).upper()

    def test_evidence_withholds_totals_across_mixed_uom(self):
        from app.models.daily import DailyDataset
        from app.services.validation_service import build_task

        tasks = [build_task(row) for row in ROWS]
        dataset = DailyDataset(report_date=REPORT_DATE, tasks=tasks, counters=DayCounters())
        evidence = EvidenceService().build(dataset, tasks, scope="day")
        assert evidence["summary"]["quantities_summable"] is False
        assert evidence["summary"]["planned_quantity"] is None
        assert "quantities_withheld_reason" in evidence["summary"]

    def test_evidence_states_that_tolerance_and_conversion_are_undefined(self):
        from app.models.daily import DailyDataset
        from app.services.validation_service import build_task

        tasks = [build_task(ROWS[0])]
        dataset = DailyDataset(report_date=REPORT_DATE, tasks=tasks, counters=DayCounters())
        evidence = EvidenceService().build(dataset, tasks, scope="task")
        assert evidence["constraints"]["quantity_tolerance"].startswith("not defined")
        assert evidence["constraints"]["uom_conversion"].startswith("not defined")


class TestNoRepeatedQueriesPerWell:
    def test_drill_down_does_not_issue_one_query_per_well(self):
        repository = StubRepository(ROWS)
        service = DailyService(repository=repository)
        dataset = service.get_dataset(REPORT_DATE)
        for well_id in {row["well_id"] for row in ROWS}:
            service.filter_tasks(dataset, well_id=well_id)
        assert repository.calls == 1


class TestQuantityPrecision:
    def test_quantities_cross_the_wire_without_float_rounding(self, client):
        body = client.get(f"/api/daily/details?date={REPORT_DATE}").json()
        for task in body["tasks"]:
            if task["planned"] is not None:
                Decimal(task["planned"])  # parses exactly, no float artefacts
