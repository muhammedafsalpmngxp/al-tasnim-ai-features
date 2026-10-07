"""Crew match and date validation -- daily_report_rules.md section 10.

Mirrors test_quantity_validation.py's shape: pure classification-function tests
first, then the same rules exercised through build_task() on a realistic row.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.models.daily import CrewMatchStatus, DataQualityFlag, DateStatus
from app.services.validation_service import build_task, classify_crew_match, classify_date
from tests.conftest import REPORT_DATE, make_row


class TestClassifyCrewMatch:
    def test_matching_codes_are_matched(self):
        assert classify_crew_match("MWS0602", "MWS0602") is CrewMatchStatus.MATCHED

    def test_case_and_whitespace_are_tolerated(self):
        assert classify_crew_match(" mws0602 ", "MWS0602") is CrewMatchStatus.MATCHED

    def test_different_codes_are_mismatched(self):
        assert classify_crew_match("MWS0602", "FCE-0402") is CrewMatchStatus.MISMATCHED

    def test_no_crew_code_is_not_comparable(self):
        assert classify_crew_match(None, "MWS0602") is CrewMatchStatus.NOT_COMPARABLE

    def test_no_crew_type_code_is_not_comparable(self):
        # The common case: no crew recorded on the task at all (~69% of rows).
        assert classify_crew_match("MWS0602", None) is CrewMatchStatus.NOT_COMPARABLE

    def test_neither_side_present_is_not_comparable(self):
        assert classify_crew_match(None, None) is CrewMatchStatus.NOT_COMPARABLE


class TestClassifyDate:
    def test_actual_equals_planned_is_on_time(self):
        assert classify_date(REPORT_DATE, REPORT_DATE) is DateStatus.ON_TIME

    def test_actual_before_planned_is_early_not_an_error(self):
        assert classify_date(REPORT_DATE, REPORT_DATE - timedelta(days=2)) is DateStatus.EARLY

    def test_actual_after_planned_is_late_not_an_error(self):
        assert classify_date(REPORT_DATE, REPORT_DATE + timedelta(days=2)) is DateStatus.LATE

    def test_no_actual_date_is_no_actual(self):
        assert classify_date(REPORT_DATE, None) is DateStatus.NO_ACTUAL

    def test_no_actual_date_wins_even_with_no_planned_date_either(self):
        assert classify_date(None, None) is DateStatus.NO_ACTUAL

    def test_planned_missing_with_an_actual_is_not_validated(self):
        assert classify_date(None, REPORT_DATE) is DateStatus.NOT_VALIDATED


class TestCrewMatchOnBuiltTasks:
    def test_matching_crew_carries_no_extra_flag(self):
        task = build_task(make_row(crew_code="MWS0602", crew_type_code="MWS0602"))
        assert task.crew_match_status is CrewMatchStatus.MATCHED

    def test_mismatched_crew_is_reported_without_blame(self):
        task = build_task(make_row(crew_code="MWS0602", crew_type_code="FCE-0402"))
        assert task.crew_match_status is CrewMatchStatus.MISMATCHED

    def test_no_crew_recorded_is_not_comparable_not_mismatched(self):
        # crew_id absent -> crew_personnel never resolves -> crew_type_code is None.
        task = build_task(make_row(crew_id=None, crew_type_id=None, crew_type_code=None))
        assert task.crew_match_status is CrewMatchStatus.NOT_COMPARABLE

    def test_unmapped_wbs_crew_is_not_comparable(self):
        task = build_task(make_row(crew_code=None))
        assert task.crew_match_status is CrewMatchStatus.NOT_COMPARABLE


class TestDateStatusOnBuiltTasks:
    def test_on_time_start_and_end_carry_no_flag(self):
        task = build_task(make_row())
        assert task.start_date_status is DateStatus.ON_TIME
        assert task.end_date_status is DateStatus.ON_TIME
        assert DataQualityFlag.INVALID_DATE_SEQUENCE not in task.data_quality_flags

    def test_late_end_is_reported_without_a_computed_variance(self):
        task = build_task(
            make_row(planned_end=REPORT_DATE, actual_end=REPORT_DATE + timedelta(days=3))
        )
        assert task.end_date_status is DateStatus.LATE

    def test_early_start_is_not_an_error(self):
        task = build_task(
            make_row(planned_start=REPORT_DATE, actual_start=REPORT_DATE - timedelta(days=1))
        )
        assert task.start_date_status is DateStatus.EARLY

    def test_no_actual_start_yet_is_no_actual(self):
        task = build_task(make_row(actual_start=None))
        assert task.start_date_status is DateStatus.NO_ACTUAL

    def test_end_before_start_is_flagged_invalid_sequence(self):
        task = build_task(
            make_row(
                actual_start=REPORT_DATE,
                actual_end=REPORT_DATE - timedelta(days=1),
            )
        )
        assert DataQualityFlag.INVALID_DATE_SEQUENCE in task.data_quality_flags

    def test_end_equal_to_start_is_not_an_invalid_sequence(self):
        task = build_task(make_row(actual_start=REPORT_DATE, actual_end=REPORT_DATE))
        assert DataQualityFlag.INVALID_DATE_SEQUENCE not in task.data_quality_flags

    def test_missing_actual_end_never_falsely_flags_invalid_sequence(self):
        task = build_task(make_row(actual_start=REPORT_DATE, actual_end=None))
        assert DataQualityFlag.INVALID_DATE_SEQUENCE not in task.data_quality_flags

    def test_quantity_status_is_independent_of_date_and_crew_status(self):
        # Same row, three independent verdicts -- never merged into one.
        task = build_task(
            make_row(
                planned=10,
                actual_quantity=10,
                crew_code="MWS0602",
                crew_type_code="FCE-0402",
                planned_end=REPORT_DATE,
                actual_end=REPORT_DATE + timedelta(days=1),
            )
        )
        assert task.quantity_status.value == "ON_PLAN"
        assert task.crew_match_status is CrewMatchStatus.MISMATCHED
        assert task.end_date_status is DateStatus.LATE
