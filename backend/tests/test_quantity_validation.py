"""Quantity classification -- the rule that every dashboard number rests on."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.models.daily import DataQualityFlag, QuantityStatus
from app.services.validation_service import build_task, classify_quantity
from tests.conftest import make_row


def d(value) -> Decimal:
    return Decimal(str(value))


class TestClassifyQuantity:
    def test_actual_equals_planned_is_on_plan(self):
        assert classify_quantity(d(15), d(15)) is QuantityStatus.ON_PLAN

    def test_actual_above_planned_is_above_plan(self):
        assert classify_quantity(d(15), d(20)) is QuantityStatus.ABOVE_PLAN

    def test_actual_below_planned_is_below_plan(self):
        assert classify_quantity(d(15), d(10)) is QuantityStatus.BELOW_PLAN

    def test_actual_null_is_no_actual(self):
        assert classify_quantity(d(15), None) is QuantityStatus.NO_ACTUAL

    def test_planned_zero_with_zero_actual_is_on_plan(self):
        assert classify_quantity(d(0), d(0)) is QuantityStatus.ON_PLAN

    def test_planned_zero_with_positive_actual_is_above_plan_not_an_error(self):
        # planned = 0 and actual > 0 is explicitly NOT an error condition.
        assert classify_quantity(d(0), d(12)) is QuantityStatus.ABOVE_PLAN

    def test_planned_zero_with_no_actual_is_no_actual(self):
        assert classify_quantity(d(0), None) is QuantityStatus.NO_ACTUAL

    def test_planned_null_with_actual_is_not_validated(self):
        # No planned quantity exists to compare against, and daily_report_rules.md
        # does not define this case, so it is exposed rather than guessed.
        assert classify_quantity(None, d(5)) is QuantityStatus.NOT_VALIDATED

    def test_planned_null_without_actual_is_still_no_actual(self):
        assert classify_quantity(None, None) is QuantityStatus.NO_ACTUAL

    @pytest.mark.parametrize(
        "planned,actual",
        [("15.0000", "15"), ("15.30", "15.3"), ("0.0", "0")],
    )
    def test_decimal_scale_does_not_affect_equality(self, planned, actual):
        assert classify_quantity(d(planned), d(actual)) is QuantityStatus.ON_PLAN

    def test_tiny_difference_is_not_absorbed_by_a_tolerance(self):
        # No tolerance is defined by the business rules, so none is applied.
        assert classify_quantity(d("15.0000"), d("14.9999")) is QuantityStatus.BELOW_PLAN


class TestQuantityOnBuiltTasks:
    def test_above_plan_carries_no_error_flag(self):
        task = build_task(make_row(planned=10, actual_quantity=14))
        assert task.quantity_status is QuantityStatus.ABOVE_PLAN
        assert task.data_quality_flags == []

    def test_missing_planned_is_flagged_and_not_validated(self):
        task = build_task(make_row(planned=None, actual_quantity=7))
        assert task.quantity_status is QuantityStatus.NOT_VALIDATED
        assert DataQualityFlag.MISSING_PLANNED in task.data_quality_flags

    def test_no_actual_entry_yields_no_actual(self):
        task = build_task(
            make_row(actual_quantity=None, actual_quantity_raw=None, is_actual_entry=0,
                     group_actual_entry_count=0)
        )
        assert task.quantity_status is QuantityStatus.NO_ACTUAL
        assert task.actual_quantity is None

    def test_unparseable_actual_is_flagged_and_treated_as_no_actual(self):
        task = build_task(
            make_row(
                actual_quantity=None,
                actual_quantity_raw="not-a-number",
                actual_quantity_unparseable=1,
            )
        )
        assert task.quantity_status is QuantityStatus.NO_ACTUAL
        assert DataQualityFlag.UNPARSEABLE_ACTUAL_QUANTITY in task.data_quality_flags

    def test_invalid_daily_json_is_flagged_without_losing_the_task(self):
        task = build_task(
            make_row(
                actual_quantity=None,
                actual_quantity_raw=None,
                daily_data_json_valid=0,
                daily_data_json_invalid=1,
            )
        )
        assert DataQualityFlag.INVALID_DAILY_JSON in task.data_quality_flags
        assert task.well_id == 31474  # the row is still reported, not dropped
