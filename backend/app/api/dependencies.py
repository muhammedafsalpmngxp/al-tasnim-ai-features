"""Shared route dependencies and the common date parameter.

The report date is always supplied by the caller as a bound parameter and is
never interpolated into SQL. When it is omitted the server's current date is
used -- the date is never hardcoded anywhere.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from fastapi import HTTPException, Query, status

from app.config.database import DatabaseUnavailable
from app.models.daily import DailyDataset
from app.services.crew_suggestion_service import CrewSuggestionService
from app.services.daily_service import DailyService
from app.services.evidence_service import EvidenceService
from app.services.export_service import ExportService
<<<<<<< HEAD
=======
from app.services.field_map_service import FieldMapService
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
from app.services.grouping_service import GroupingService
from app.services.llm_service import LLMService
from app.services.milestone_service import MilestoneService
from app.services.well_activity_service import WellActivityService

_daily_service = DailyService()
_grouping_service = GroupingService()
_export_service = ExportService(_grouping_service)
_evidence_service = EvidenceService(_grouping_service)
_llm_service = LLMService()
_milestone_service = MilestoneService()
_crew_suggestion_service = CrewSuggestionService()
_well_activity_service = WellActivityService()
<<<<<<< HEAD
=======
_field_map_service = FieldMapService()
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)


def get_daily_service() -> DailyService:
    return _daily_service


def get_grouping_service() -> GroupingService:
    return _grouping_service


def get_export_service() -> ExportService:
    return _export_service


def get_evidence_service() -> EvidenceService:
    return _evidence_service


def get_llm_service() -> LLMService:
    return _llm_service


def get_milestone_service() -> MilestoneService:
    return _milestone_service


def get_crew_suggestion_service() -> CrewSuggestionService:
    return _crew_suggestion_service


def get_well_activity_service() -> WellActivityService:
    return _well_activity_service


<<<<<<< HEAD
=======
def get_field_map_service() -> FieldMapService:
    return _field_map_service


>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
def parse_report_date(
    date_value: Optional[str] = Query(
        None,
        alias="date",
        description="Report date as YYYY-MM-DD. Defaults to the current date.",
    ),
) -> date:
    """Validate the requested report date, defaulting to today."""
    if date_value is None or date_value.strip() == "":
        return date.today()
    try:
        return datetime.strptime(date_value.strip(), "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid date {date_value!r}. Expected format YYYY-MM-DD.",
        ) from None


def load_dataset(
    service: DailyService, report_date: date, *, refresh: bool = False
) -> DailyDataset:
    """Load the day's evidence, turning a database failure into a clean 503."""
    try:
        return service.get_dataset(report_date, refresh=refresh)
    except DatabaseUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from None
