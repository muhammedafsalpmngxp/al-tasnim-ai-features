"""The evidence sent to the LLM must represent the whole scope, not whatever
happens to sort first.

Caught live, against real data: a day with 46 wells produced an explanation
naming only 2-3 of them, because `daily_detail.sql` orders rows by
`(uom_code, wbs, activity_code, well_id)` and the old sampling just took the
first N tasks in that order -- a plain prefix that clusters on whichever
activity sorts first. The same raw order also mixed different statuses
together with no structure, making it easy for the model's prose to blend
an ON_PLAN task's description into a BELOW_PLAN one's.

``_representative_sample`` fixes both: status is the outer axis (so a
handful of examples already touches every status present) and well is the
inner axis within each status (so those examples are not all the same one or
two wells).
"""

from __future__ import annotations

from app.models.daily import QuantityStatus
from app.services.evidence_service import _representative_sample
from tests.conftest import make_row
from app.services.validation_service import build_task


def _task(well_id, status_inputs, task_daily_id):
    """Build one real DailyTask with a specific well and quantity status.

    ``status_inputs`` is a (planned, actual) pair chosen to classify to the
    wanted status via the real, unmodified classification rule -- never a
    fake/injected status -- so this test exercises the real DailyTask shape.
    """
    planned, actual = status_inputs
    return build_task(make_row(task_daily_id=task_daily_id, well_id=well_id, planned=planned, actual_quantity=actual))


ON_PLAN = (10, 10)
BELOW_PLAN = (10, 5)
ABOVE_PLAN = (10, 20)
NO_ACTUAL = (10, None)
NOT_VALIDATED = (None, 10)


class TestStatusDiversity:
    def test_every_present_status_appears_within_the_sample_even_when_one_dominates(self):
        # 30 ON_PLAN tasks across 30 different wells, plus one task each of
        # every other status -- a fixed sampling limit must not fill up
        # entirely on the dominant status and miss the rest.
        tasks = [_task(well_id=w, status_inputs=ON_PLAN, task_daily_id=w) for w in range(1, 31)]
        tasks.append(_task(well_id=1001, status_inputs=ABOVE_PLAN, task_daily_id=1001))
        tasks.append(_task(well_id=1002, status_inputs=BELOW_PLAN, task_daily_id=1002))
        tasks.append(_task(well_id=1003, status_inputs=NO_ACTUAL, task_daily_id=1003))
        tasks.append(_task(well_id=1004, status_inputs=NOT_VALIDATED, task_daily_id=1004))

        sample = _representative_sample(tasks, limit=5)

        statuses_in_sample = {task.quantity_status for task in sample}
        assert statuses_in_sample == {
            QuantityStatus.ON_PLAN,
            QuantityStatus.ABOVE_PLAN,
            QuantityStatus.BELOW_PLAN,
            QuantityStatus.NO_ACTUAL,
            QuantityStatus.NOT_VALIDATED,
        }

    def test_a_status_with_no_tasks_never_appears(self):
        tasks = [_task(well_id=1, status_inputs=ON_PLAN, task_daily_id=1)]
        sample = _representative_sample(tasks, limit=5)
        assert {task.quantity_status for task in sample} == {QuantityStatus.ON_PLAN}


class TestWellDiversity:
    def test_many_wells_of_the_same_status_are_spread_not_clustered(self):
        # 20 different wells, one ON_PLAN task each -- a small sample must
        # come from many distinct wells, not the same one or two repeated.
        tasks = [_task(well_id=w, status_inputs=ON_PLAN, task_daily_id=w) for w in range(1, 21)]
        sample = _representative_sample(tasks, limit=5)
        wells_in_sample = {task.well_id for task in sample}
        assert len(wells_in_sample) == 5  # five distinct wells, not five tasks from one well

    def test_a_well_with_many_tasks_does_not_crowd_out_other_wells(self):
        # Well 1 alone has 10 ON_PLAN tasks; wells 2-6 have one ON_PLAN task
        # each. A sample of 6 must include all five other wells, not just
        # well 1's tasks.
        tasks = [_task(well_id=1, status_inputs=ON_PLAN, task_daily_id=n) for n in range(10)]
        tasks += [_task(well_id=w, status_inputs=ON_PLAN, task_daily_id=100 + w) for w in range(2, 7)]

        sample = _representative_sample(tasks, limit=6)
        wells_in_sample = {task.well_id for task in sample}
        assert wells_in_sample == {1, 2, 3, 4, 5, 6}


class TestNoTruncationStillReorders:
    def test_a_sample_at_or_above_the_total_still_includes_every_task(self):
        tasks = [
            _task(well_id=1, status_inputs=ON_PLAN, task_daily_id=1),
            _task(well_id=2, status_inputs=BELOW_PLAN, task_daily_id=2),
            _task(well_id=3, status_inputs=NO_ACTUAL, task_daily_id=3),
        ]
        sample = _representative_sample(tasks, limit=10)
        assert len(sample) == 3
        assert {task.task_daily_id for task in sample} == {1, 2, 3}

    def test_result_is_deterministic_for_the_same_input(self):
        tasks = [_task(well_id=w, status_inputs=ON_PLAN, task_daily_id=w) for w in range(1, 11)]
        tasks.append(_task(well_id=99, status_inputs=BELOW_PLAN, task_daily_id=99))

        first = _representative_sample(tasks, limit=4)
        second = _representative_sample(tasks, limit=4)
        assert [t.task_daily_id for t in first] == [t.task_daily_id for t in second]


class TestLimitIsRespected:
    def test_the_sample_never_exceeds_the_limit(self):
        tasks = [_task(well_id=w, status_inputs=ON_PLAN, task_daily_id=w) for w in range(1, 51)]
        sample = _representative_sample(tasks, limit=10)
        assert len(sample) == 10

    def test_a_limit_of_one_still_returns_exactly_one_task(self):
        tasks = [_task(well_id=w, status_inputs=ON_PLAN, task_daily_id=w) for w in range(1, 5)]
        sample = _representative_sample(tasks, limit=1)
        assert len(sample) == 1
