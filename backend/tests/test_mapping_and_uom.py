"""Activity mapping, UOM handling and the no-conversion rule."""

from __future__ import annotations

from decimal import Decimal

from app.models.daily import DailyDataset, DataQualityFlag, MappingStatus
from app.services.grouping_service import GroupingService
from app.services.validation_service import build_task, classify_mapping
from tests.conftest import make_row


class TestActivityMapping:
    def test_fully_mapped_activity(self):
        task = build_task(make_row())
        assert task.mapping_status is MappingStatus.MAPPED
        assert task.data_quality_flags == []
        assert task.wbs == "Straightline Welding incl. supports"
        assert task.crew_code == "MWS0602"

    def test_unmapped_activity_is_exposed_not_dropped(self):
        task = build_task(
            make_row(
                activity_code=None,
                activity_description=None,
                wbs=None,
                crew_code=None,
            )
        )
        assert task.mapping_status is MappingStatus.UNMAPPED_ACTIVITY
        assert DataQualityFlag.UNMAPPED_ACTIVITY in task.data_quality_flags
        # Nothing is fabricated to fill the gap.
        assert task.wbs is None
        assert task.activity_description is None
        assert task.crew_code is None

    def test_missing_wbs_alone_is_reported_as_unmapped_wbs(self):
        task = build_task(make_row(wbs=None))
        assert task.mapping_status is MappingStatus.UNMAPPED_WBS
        assert DataQualityFlag.UNMAPPED_WBS in task.data_quality_flags

    def test_missing_description_alone_is_reported(self):
        task = build_task(make_row(activity_description=None))
        assert task.mapping_status is MappingStatus.UNMAPPED_DESCRIPTION

    def test_missing_crew_alone_is_reported(self):
        task = build_task(make_row(crew_code=None))
        assert task.mapping_status is MappingStatus.UNMAPPED_CREW

    def test_activity_id_extraction_uses_text_before_first_dash(self):
        # Mirrors LEFT(task_code, NULLIF(CHARINDEX('-', task_code), 0) - 1):
        # a task_code may hold more dashes and still yield the same activity_id.
        task = build_task(
            make_row(task_code="FLME1180-34516-T02", activity_id="FLME1180")
        )
        assert task.activity_id == "FLME1180"

    def test_task_code_without_a_dash_yields_no_activity_id(self):
        task = build_task(
            make_row(
                task_code="FLME1180",
                activity_id=None,
                activity_code=None,
                activity_description=None,
                wbs=None,
                crew_code=None,
            )
        )
        assert task.activity_id is None
        assert DataQualityFlag.MALFORMED_TASK_CODE in task.data_quality_flags

    def test_precedence_reports_the_first_break_in_the_chain(self):
        task = build_task(
            make_row(activity_code=None, wbs=None, crew_code=None)
        )
        assert task.mapping_status is MappingStatus.UNMAPPED_ACTIVITY

    def test_classify_mapping_is_whitespace_aware(self):
        assert (
            classify_mapping("FLME1180", "  ", "desc", "wbs", "crew")
            is MappingStatus.UNMAPPED_ACTIVITY
        )


class TestUom:
    def test_valid_uom_is_carried_through(self):
        task = build_task(make_row(uom_id=3, uom_code="Joint"))
        assert task.uom_code == "Joint"
        assert DataQualityFlag.MISSING_UOM not in task.data_quality_flags

    def test_missing_uom_is_flagged_and_the_task_stays_visible(self):
        task = build_task(make_row(uom_id=None, uom_code=None))
        assert DataQualityFlag.MISSING_UOM in task.data_quality_flags
        assert task.uom_key == ""

    def test_different_uom_are_never_added_together(self):
        # Km and M land in one group now that the hierarchy starts at status,
        # so the group must withhold its totals rather than add 1 Km to 1000 M.
        # No conversion rule exists, and inventing one is the whole point of
        # this test.
        tasks = [
            build_task(make_row(task_daily_id=1, uom_id=6, uom_code="Km", planned=1, actual_quantity=1)),
            build_task(make_row(task_daily_id=2, uom_id=9, uom_code="M", planned=1000, actual_quantity=1000)),
        ]
        groups = GroupingService().build(_dataset(tasks))
        assert len(groups) == 1  # both are ON_PLAN
        group = groups[0]
        assert group.task_count == 2
        assert group.quantities_summable is False
        assert group.uom_codes == ["Km", "M"]
        assert group.planned_quantity is None
        assert group.actual_quantity is None

    def test_quantities_are_summed_only_within_one_uom(self):
        # Two Joint tasks in one status group total; the group that mixes Joint
        # with m3 reports no total at all.
        tasks = [
            build_task(make_row(task_daily_id=1, uom_code="Joint", planned=10, actual_quantity=10)),
            build_task(make_row(task_daily_id=2, uom_code="Joint", planned=5, actual_quantity=5)),
            build_task(make_row(task_daily_id=3, uom_code="Joint", planned=5, actual_quantity=4)),
            build_task(make_row(task_daily_id=4, uom_code="m3", planned=100, actual_quantity=90)),
        ]
        groups = {g.status.value: g for g in GroupingService().build(_dataset(tasks))}

        on_plan = groups["ON_PLAN"]
        assert on_plan.quantities_summable is True
        assert on_plan.uom_code == "Joint"
        assert on_plan.planned_quantity == Decimal(15)
        assert on_plan.actual_quantity == Decimal(15)

        below_plan = groups["BELOW_PLAN"]
        assert below_plan.quantities_summable is False
        assert below_plan.planned_quantity is None

    def test_a_group_with_no_uom_at_all_still_totals_its_quantities(self):
        # "UOM not recorded" is one bucket, not several: those tasks share a
        # (missing) unit, so their total is reported without a unit label and
        # MISSING_UOM says why.
        tasks = [
            build_task(make_row(task_daily_id=1, uom_id=None, uom_code=None,
                                planned=10, actual_quantity=10)),
            build_task(make_row(task_daily_id=2, uom_id=None, uom_code=None,
                                planned=5, actual_quantity=5)),
        ]
        group = GroupingService().build(_dataset(tasks))[0]
        assert group.quantities_summable is True
        assert group.uom_code is None
        assert group.uom_codes == []
        assert group.planned_quantity == Decimal(15)
        assert group.data_quality_counts[DataQualityFlag.MISSING_UOM.value] == 2

    def test_day_totals_report_no_cross_uom_quantity(self):
        tasks = [
            build_task(make_row(task_daily_id=1, uom_code="Joint", planned=10, actual_quantity=10)),
            build_task(make_row(task_daily_id=2, uom_code="m3", planned=100, actual_quantity=90)),
        ]
        totals = GroupingService().day_totals(_dataset(tasks))
        assert "planned_quantity" not in totals
        assert totals["task_count"] == 2


class TestActivityMasterUom:
    """dbo.mapping_master.UOM: reference evidence only, never a substitute.

    uom_code (ref.uom via task_daily.uom_id) stays the sole UOM used for
    grouping and quantity interpretation. activity_uom is exposed purely as
    traceable evidence -- most useful exactly when uom_code is missing.
    """

    def test_activity_uom_is_carried_through_alongside_uom_code(self):
        task = build_task(make_row(uom_code="Joint", activity_uom="Joint"))
        assert task.uom_code == "Joint"
        assert task.activity_uom == "Joint"

    def test_activity_uom_survives_when_the_tasks_own_uom_is_missing(self):
        # This is the common case in the data: task_daily.uom_id is NULL/
        # unmapped on a large share of rows where the activity master's UOM
        # is still available.
        task = build_task(make_row(uom_id=None, uom_code=None, activity_uom="Section"))
        assert task.uom_code is None
        assert task.activity_uom == "Section"
        # The task's own UOM is still genuinely missing -- the activity
        # master having a reference value does not make this any less a
        # data-quality gap on the daily entry itself.
        assert DataQualityFlag.MISSING_UOM in task.data_quality_flags

    def test_activity_uom_never_fills_in_for_a_missing_uom_code(self):
        # activity_uom must never be copied into uom_code -- that would be an
        # invented substitution rule, and uom_code drives grouping.
        task = build_task(make_row(uom_id=None, uom_code=None, activity_uom="Joint"))
        assert task.uom_code is None

    def test_activity_uom_is_absent_when_the_activity_is_unmapped(self):
        task = build_task(
            make_row(activity_code=None, activity_description=None, wbs=None,
                      crew_code=None, activity_uom=None)
        )
        assert task.activity_uom is None

    def test_activity_uom_does_not_affect_grouping_or_quantities(self):
        # Two tasks share uom_code but differ in activity_uom: they must still
        # total together, because activity_uom is not a grouping key and is
        # never what a quantity is interpreted in.
        tasks = [
            build_task(make_row(task_daily_id=1, uom_code="Joint", activity_uom="Joint",
                                 planned=10, actual_quantity=10)),
            build_task(make_row(task_daily_id=2, uom_code="Joint", activity_uom="Jts",
                                 planned=5, actual_quantity=5)),
        ]
        groups = GroupingService().build(_dataset(tasks))
        assert len(groups) == 1
        assert groups[0].task_count == 2
        assert groups[0].planned_quantity == Decimal(15)


def _dataset(tasks) -> DailyDataset:
    from app.models.daily import DayCounters
    from tests.conftest import REPORT_DATE

    return DailyDataset(report_date=REPORT_DATE, tasks=tasks, counters=DayCounters())
