"""Turns one crew_suggestion.sql row into the evidence merged into the
existing task AI summary.

Nothing here calculates anything -- eligibility, historical statistics,
ranking and availability were all decided in SQL (see sql/crew_suggestion.sql
and daily_report_rules.md, "Crew suggestion" section). This service only
shapes that one row into JSON-ready evidence and decides the handful of plain
"why wasn't a crew suggested" reasons the SQL couldn't phrase for itself.

Advisory only: this never writes to the database, never changes a crew_id,
and is never surfaced as a second UI control -- it rides inside the evidence
already sent to app/services/llm_service.py for the existing task-scoped
"Explain this task" request. See app/api/routes_explain.py for where it is
merged in, and only in, that one place.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Any, Dict, Optional

from app.repositories.crew_repository import CrewRepository
from app.schemas.crew_suggestion import (
    CrewSuggestionEvidence,
    CurrentCrewEvidence,
    HistoricalCrewEvidence,
)

logger = logging.getLogger(__name__)


def _number(value: Optional[Decimal]) -> Optional[float]:
    """Decimal -> float for JSON, without ever inventing precision that was
    not there (None stays None; SQL Server already rounded AVG to 4 places)."""
    if value is None:
        return None
    return float(value)


class CrewSuggestionService:
    """Builds the ``crew_suggestion`` evidence for one task, or ``None``.

    ``None`` means "this task has no crew-suggestion evidence to add" -- the
    task itself could not be resolved for this well_id/task_code/report_date
    (e.g. it belongs to a different report date than the one requested, or the
    identifiers do not match any recorded task). The caller must simply omit
    the ``crew_suggestion`` key in that case; it is not an error, and it is
    never surfaced as a failure of the task's own AI summary.
    """

    def __init__(self, repository: Optional[CrewRepository] = None) -> None:
        self._repository = repository or CrewRepository()

    def build(
        self, *, well_id: int, task_code: str, report_date: date
    ) -> Optional[Dict[str, Any]]:
        if not task_code:
            return None

        row = self._repository.fetch_target_task(
            well_id=well_id, task_code=task_code, report_date=report_date
        )
        if row is None:
            return None

        evidence = self._to_evidence(row)
        data = evidence.model_dump(mode="json")
        # suppression_reason/suppression_code and current_crew.crew_id stay
        # explicit nulls when absent (matching the rest of this app's wire
        # conventions -- e.g. ExplainResponse.error). suggested_crew,
        # consult_crew and no_suggestion_reason are mutually exclusive with
        # each other, so whichever ones do not apply are omitted entirely
        # rather than sent as a cluttering null.
        for optional_key in ("suggested_crew", "consult_crew", "no_suggestion_reason"):
            if data.get(optional_key) is None:
                data.pop(optional_key, None)
        return data

    @staticmethod
    def _candidate_from_row(row: Dict[str, Any]) -> HistoricalCrewEvidence:
        return HistoricalCrewEvidence(
            crew_id=row["candidate_crew_id"],
            crew_type=row.get("candidate_crew_type"),
            supervisor=row.get("candidate_crew_supervisor"),
            historical_completed_task_count=row["historical_completed_task_count"],
            distinct_completed_well_count=row["distinct_completed_well_count"],
            completed_on_incomplete_well_count=row["completed_on_incomplete_well_count"],
            completed_on_completed_well_count=row["completed_on_completed_well_count"],
            typical_completion_days=_number(row.get("typical_completion_days")),
            average_completion_days=_number(row.get("average_completion_days")),
            shortest_completion_days=row.get("shortest_completion_days"),
            longest_completion_days=row.get("longest_completion_days"),
            most_recent_success_date=row.get("most_recent_success_date"),
            evidence_strength=row["evidence_strength"],
            derived_availability=row["derived_availability"],
        )

    @classmethod
    def _to_evidence(cls, row: Dict[str, Any]) -> CrewSuggestionEvidence:
        eligible = bool(row.get("crew_suggestion_eligible"))
        suppression_reason = row.get("crew_suggestion_suppression_reason")
        suppression_code = row.get("crew_suggestion_suppression_code")
        has_candidate = row.get("candidate_crew_id") is not None

        current_crew = CurrentCrewEvidence(
            crew_id=row.get("current_crew_id"),
            recorded=row.get("current_crew_id") is not None,
            crew_type=row.get("current_crew_type"),
            supervisor=row.get("current_crew_supervisor"),
        )

        suggested_crew: Optional[HistoricalCrewEvidence] = None
        consult_crew: Optional[HistoricalCrewEvidence] = None
        no_suggestion_reason: Optional[str] = None

        if eligible:
            if has_candidate:
                # Stalled task, nothing in progress: a genuine replacement
                # candidate.
                suggested_crew = cls._candidate_from_row(row)
            elif row.get("activity_code") is None:
                no_suggestion_reason = (
                    "This task's activity is not mapped to an activity code, so no "
                    "historical comparison is available."
                )
            elif not row.get("historical_crew_candidate_count"):
                no_suggestion_reason = (
                    "No historical records show any crew completing this same "
                    "activity on another well by the selected report date."
                )
            elif not row.get("available_crew_candidate_count"):
                no_suggestion_reason = (
                    "Crews with a completion history for this activity were found, "
                    "but none of them is free of a current unfinished task in the "
                    "available records."
                )
        elif suppression_code == "IN_PROGRESS" and has_candidate:
            # The task needs no replacement -- it is already moving -- but the
            # same historically-proven crew is still worth surfacing as a
            # purely informational "ask them for feedback" mention, so the
            # feature stays visible even when nothing needs to change.
            consult_crew = cls._candidate_from_row(row)
        # suppression_code == "COMPLETED", or "IN_PROGRESS" with no candidate
        # at all: nothing to add. Falls through with everything left None.

        return CrewSuggestionEvidence(
            eligible=eligible,
            suppression_reason=suppression_reason,
            suppression_code=suppression_code,
            current_crew=current_crew,
            suggested_crew=suggested_crew,
            consult_crew=consult_crew,
            no_suggestion_reason=no_suggestion_reason,
        )
