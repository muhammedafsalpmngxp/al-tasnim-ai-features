"""Shared test fixtures.

The deterministic tests below run entirely offline: they exercise the rules and
the row-grain strategy against synthetic rows shaped exactly like the columns
daily_detail.sql returns. They never reach SQL Server, so they are stable in CI.

Tests that DO require the database are marked ``@pytest.mark.database`` and skip
themselves automatically when no connection is available.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

REPORT_DATE = date(2026, 8, 1)


def make_row(**overrides: Any) -> Dict[str, Any]:
    """A resolved daily row with the same keys daily_detail.sql produces."""
    row: Dict[str, Any] = {
        "task_daily_id": 1,
        "well_id": 31474,
        "action_on": REPORT_DATE,
        "schedule_id": 359,
        "task_code": "FLME1180-31474",
        "activity_id": "FLME1180",
        "activity_code": "FL-ME-ML08-03",
        "activity_description": "Pipe Stringing, Alignment, Fitup and Welding",
        "wbs": "Straightline Welding incl. supports",
        "crew_code": "MWS0602",
        "uom_id": 3,
        "uom_code": "Joint",
        "activity_uom": "Joint",
        "crew_id": 10570,
        "crew_type_id": 1,
<<<<<<< HEAD
=======
        "crew_type_code": "MWS0602",
        "planned_start": REPORT_DATE,
        "planned_end": REPORT_DATE,
        "actual_start": REPORT_DATE,
        "actual_end": REPORT_DATE,
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
        "planned": 15,
        "progress": 0,
        "actual_quantity": 15,
        "actual_quantity_raw": "15",
        "daily_completed": False,
        "ph_name": "Ammar",
        "is_actual_entry": 1,
        "daily_data_json_valid": 1,
        "daily_data_json_invalid": 0,
        "actual_quantity_unparseable": 0,
        "group_row_count": 1,
        "group_actual_entry_count": 1,
    }
    row.update(overrides)
    return row


class StubWellRepository:
    """A WellRepository that answers from fixed rows instead of the database.

    Lets any test that goes through /api/daily/explain or
    /api/daily/well-activity stay offline: the well-scoped evidence now
    includes a well's task activity, and that would otherwise be the one part
    of an otherwise hermetic test that reaches SQL Server.
    """

    def __init__(self, activity=None, detail=None):
        self.activity = list(activity or [])
        self.detail = list(detail or [])

    def fetch_well_task_activity(self, report_date: date):
        return list(self.activity)

    def fetch_well_task_activity_detail(self, report_date: date):
        return list(self.detail)

    def fetch_outstanding_milestones(self):
        return []


def stub_well_activity_service(activity=None, detail=None):
    """A real WellActivityService over StubWellRepository -- real aggregation,
    real caching, no database."""
    from app.services.well_activity_service import WellActivityService

    return WellActivityService(StubWellRepository(activity, detail))


@pytest.fixture
def report_date() -> date:
    return REPORT_DATE


@pytest.fixture(autouse=True)
def _isolate_llm_side_effects(tmp_path, monkeypatch):
    """No test may touch the real, persistent LLM cache file or the real
    project-root usage-expense log -- both are meant to reflect genuine
    application usage, never a test run.

    Most LLM-related tests explicitly monkeypatch ``LLMService.explain``, so
    this exists for the ones that go through the real ``/api/daily/explain``
    endpoint and its shared, module-level ``_llm_service`` singleton without
    mocking it (they only ever inspect the deterministic evidence, never the
    generated text). Redirecting the singleton's cache file to a throwaway
    path and disabling usage logging means such a test can still reach the
    real provider if `backend/.env` is configured -- this does not mock the
    network call -- but can never leave a trace in a real project file
    afterward.
    """
    import app.services.llm_service as llm_service_module
    from app.api import dependencies

    monkeypatch.setattr(llm_service_module, "_usage_tracker", None)
<<<<<<< HEAD
    monkeypatch.setattr(
        dependencies._llm_service._cache, "_file_path", tmp_path / "test_explain_cache.json"
    )
=======
    # The explanation cache is in memory only; start every test without the
    # answers an earlier test left in the shared singleton.
    monkeypatch.setattr(dependencies._llm_service, "_cache", llm_service_module._ExplainCache())

    # No test may spawn DYNAMIC_DB's real background bootstrap thread either:
    # it would open a real database connection and, on a stale schema, spend
    # real reasoning-model calls, from an ordinary unit test that never asked
    # for any of that. Marking it "already started" is what
    # start_bootstrap_background() itself checks before spawning anything.
    import app.dynamic_client as dynamic_client_module

    monkeypatch.setattr(dynamic_client_module, "_background_started", True)
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)


def database_available() -> bool:
    try:
        from app.config.database import check_connectivity

        check_connectivity()
        return True
    except Exception:  # noqa: BLE001
        return False


requires_database = pytest.mark.skipif(
    not database_available(), reason="SQL Server is not reachable from this environment"
)
