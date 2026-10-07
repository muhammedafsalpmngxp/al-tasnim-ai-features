"""Internal typed shape of the crew-suggestion evidence.

Not a wire contract of its own -- there is no new endpoint. This evidence is
merged into the same ``evidence`` dict the existing ``/api/daily/explain``
endpoint already returns (``ExplainResponse.evidence``), under the key
``crew_suggestion``, and it rides along with the existing task-specific AI
summary. These models exist purely so the service that builds that evidence
from the raw SQL row is typed and validated, the same way app/schemas/daily.py
types the wire form of a DailyTask.

Every field here was computed by SQL/Python (see sql/crew_suggestion.sql and
app/services/crew_suggestion_service.py). The LLM only explains this
evidence; it never recalculates any of it.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel


class CurrentCrewEvidence(BaseModel):
    """The crew instance currently recorded on the target task, if any.

    ``recorded`` is false only when task_daily.crew_id is NULL -- which does
    NOT mean "no crew was assigned" (that would be inventing a fact); it means
    no crew is recorded on this task record.
    """

    crew_id: Optional[int] = None
    recorded: bool
    crew_type: Optional[str] = None
    supervisor: Optional[str] = None


class HistoricalCrewEvidence(BaseModel):
    """One historically-proven, currently-unbusy crew for this activity.

    Every field here is deterministic evidence for "why this crew" -- the LLM
    must never suggest a crew or state a reason not present here. The same
    shape is reused for two different framings in ``CrewSuggestionEvidence``
    below: as ``suggested_crew`` (a replacement candidate, when the target
    task is stalled) or as ``consult_crew`` (a purely informational "this crew
    has relevant experience, worth asking for feedback" mention, when the
    target task is merely already in progress). Which one is populated is
    decided by this service, never by the LLM, and the two are mutually
    exclusive.
    """

    crew_id: int
    crew_type: Optional[str] = None
    supervisor: Optional[str] = None

    historical_completed_task_count: int
    distinct_completed_well_count: int
    completed_on_incomplete_well_count: int
    completed_on_completed_well_count: int

    #: Median historical duration (actual_start -> actual_end), in days.
    #: "Typical" always means median -- never confused with the mean below.
    typical_completion_days: Optional[float] = None
    #: Arithmetic mean historical duration, in days.
    average_completion_days: Optional[float] = None
    shortest_completion_days: Optional[int] = None
    longest_completion_days: Optional[int] = None
    most_recent_success_date: Optional[date] = None

    evidence_strength: str
    #: A conservative, V1, task-level signal -- never an authoritative
    #: physical-availability status. See daily_report_rules.md.
    derived_availability: str


class CrewSuggestionEvidence(BaseModel):
    """The full crew-suggestion sub-payload merged into the task's evidence.

    ``eligible`` and ``suppression_reason``/``suppression_code`` are decided
    entirely in SQL (sql/crew_suggestion.sql); nothing here is inferred by
    this service or by the LLM.

    Exactly one of ``suggested_crew``, ``consult_crew`` or
    ``no_suggestion_reason`` is ever populated (or none, when there is
    nothing to say at all):

    - ``eligible`` true, a candidate found -> ``suggested_crew`` (a
      replacement candidate for a stalled task).
    - ``eligible`` true, no candidate found -> ``no_suggestion_reason``
      explains why in plain terms the LLM can restate.
    - ``eligible`` false because the task is already progressing, and a
      candidate was found -> ``consult_crew``: the task needs no replacement,
      but this crew's own track record on the same activity makes it worth
      asking for feedback or information if any is needed.
    - ``eligible`` false because the task is already progressing, and no
      candidate was found -> nothing at all; there is no one to point to.
    - ``eligible`` false because the task is already completed -> nothing at
      all; there is nothing left to add once a task is done.
    """

    eligible: bool
    suppression_reason: Optional[str] = None
    #: Machine-checkable twin of suppression_reason: None / "IN_PROGRESS" /
    #: "COMPLETED". The LLM is never told to branch on this -- it is here so
    #: this service (and its tests) never has to pattern-match the sentence.
    suppression_code: Optional[str] = None
    current_crew: CurrentCrewEvidence
    suggested_crew: Optional[HistoricalCrewEvidence] = None
    consult_crew: Optional[HistoricalCrewEvidence] = None
    no_suggestion_reason: Optional[str] = None
