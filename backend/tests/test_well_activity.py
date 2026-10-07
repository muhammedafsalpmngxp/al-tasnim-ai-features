"""Well-level task activity: the front page's per-well figures and the
evidence behind a well's AI summary.

These tests run offline. They cover two halves:

* the SQL contract -- the rules that must hold in the query text itself
  (authoritative well universe, TRY_CONVERT protection, report-date bound,
  the state classification, and the absence of ``progress``), so a later edit
  cannot quietly change what a figure means;
* the Python half -- aggregation, the report-date task count, caching, the
  evidence payload and the "one query for every well, never one per well"
  guarantee -- against synthetic rows shaped exactly like the queries return.

The classification itself is additionally checked against real rows in
tests/test_well_activity_live_db.py, which skips when SQL Server is
unreachable.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List

import pytest

from app.config.database import assert_read_only
from app.models.daily import DailyDataset, DayCounters
from app.models.wells import TaskState, WellTaskActivity
from app.services.well_activity_service import WellActivityService
from app.utils.sql_loader import load_sql
from tests.conftest import REPORT_DATE, make_row
from app.services.validation_service import build_task

_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)


def _code_only(sql: str) -> str:
    """Strip /* ... */ comments so a test checks executable SQL, not prose
    that happens to mention the very identifier the test is guarding against."""
    return _COMMENT_BLOCK.sub(" ", sql)


def activity_row(**overrides: Any) -> Dict[str, Any]:
    """A row shaped like well_task_activity.sql returns."""
    # 10 tasks: 6 completed, 4 open -- and those 4 split into 2 incomplete
    # (1 not started + 1 ended-not-completed) and 2 ongoing, which is the
    # relationship every figure below depends on.
    row: Dict[str, Any] = {
        "well_id": 31474,
        "logical_task_count": 10,
        "completed_task_count": 6,
        "open_task_count": 4,
        "incomplete_task_count": 2,
        "ongoing_task_count": 2,
        "not_started_task_count": 1,
        "ended_not_completed_task_count": 1,
        "last_task_date": date(2026, 7, 30),
    }
    row.update(overrides)
    return row


def detail_row(**overrides: Any) -> Dict[str, Any]:
    """A row shaped like well_task_activity_detail.sql returns."""
    row: Dict[str, Any] = {
        "well_id": 31474,
        "schedule_id": 359,
        "task_code": "FLME1180-31474",
        "task_state": "ONGOING",
        "latest_action_on": date(2026, 7, 30),
        "actual_start": date(2026, 7, 20),
        "actual_end": None,
        "completed": False,
        "activity_id": "FLME1180",
        "activity_code": "FL-ME-ML08-03",
        "activity_description": "Pipe Stringing, Alignment, Fitup and Welding",
        "wbs": "Straightline Welding incl. supports",
    }
    row.update(overrides)
    return row


class FakeWellRepository:
    """Counts its calls, so "never one query per well" can be asserted."""

    def __init__(self, activity: List[Dict[str, Any]], detail: List[Dict[str, Any]] | None = None):
        self._activity = activity
        self._detail = detail or []
        self.activity_calls = 0
        self.detail_calls = 0

    def fetch_well_task_activity(self, report_date: date) -> List[Dict[str, Any]]:
        self.activity_calls += 1
        return list(self._activity)

    def fetch_well_task_activity_detail(self, report_date: date) -> List[Dict[str, Any]]:
        self.detail_calls += 1
        return list(self._detail)


def dataset(*tasks) -> DailyDataset:
    return DailyDataset(report_date=REPORT_DATE, tasks=list(tasks), counters=DayCounters())


# ---------------------------------------------------------------------------
# SQL contract
# ---------------------------------------------------------------------------


class TestWellTaskStateSqlContract:
    @pytest.fixture(scope="class")
    def sql(self) -> str:
        return load_sql("well_task_activity")

    @pytest.fixture(scope="class")
    def detail_sql(self) -> str:
        return load_sql("well_task_activity_detail")

    def test_the_well_universe_is_well_master_not_task_daily(self, sql):
        code = _code_only(sql)
        # well_master is the FROM of the universe; task_daily joins onto it,
        # so a task row can never bring a well into existence by itself.
        assert "FROM well.well_master AS wm" in code
        assert "INNER JOIN live_wells AS lw" in code

    def test_live_wells_use_eng_completion_date_only(self, sql):
        code = _code_only(sql)
        assert "eng_completion_date IS NULL" in code
        for forbidden in ("status_id", "flowline_const_status_id"):
            assert forbidden not in code, f"{forbidden} must not define a live well"

    def test_well_id_is_never_compared_or_joined_without_try_convert(self, sql):
        # task_daily.well_id is varchar and holds non-numeric junk ('0000F',
        # '0000I', '0000J'); a bare comparison against the int
        # well_master.well_id throws SQLSTATE 22018 and takes the report down.
        for line in sql.splitlines():
            stripped = line.strip()
            if "td.well_id" not in line or stripped.startswith(("/*", "*", "--")):
                continue
            assert "TRY_CONVERT(int, td.well_id)" in line, (
                f"td.well_id used outside TRY_CONVERT: {line!r}"
            )

    def test_invalid_well_ids_are_excluded(self, sql):
        assert "TRY_CONVERT(int, td.well_id) > 1" in sql

    def test_the_report_date_is_bound_once_and_never_a_literal(self, sql, detail_sql):
        for text in (sql, detail_sql):
            code = _code_only(text)
            assert code.count("?") == 1, "the report date must be bound exactly once"
            assert "CAST(? AS date) AS report_date" in code
            assert not re.search(r"'\d{4}-\d{2}-\d{2}'", code), "no hardcoded date"

    def test_future_rows_cannot_reach_an_earlier_report_date(self, sql):
        assert "td.ActionOn <= p.report_date" in _code_only(sql)

    def test_latest_state_uses_the_documented_ranking(self, sql):
        code = _code_only(sql)
        assert "PARTITION BY r.well_id, r.schedule_id, r.task_code" in code
        assert "ORDER BY r.ActionOn DESC, r.updated_at DESC, r.id DESC" in code
        assert "state_rank = 1" in code

    def test_task_state_is_classified_from_the_recorded_state_columns(self, sql):
        code = _code_only(sql)
        assert "WHEN s.completed = 1              THEN 'COMPLETED'" in code
        assert "WHEN s.actual_end IS NOT NULL     THEN 'ENDED_NOT_COMPLETED'" in code
        assert "WHEN s.actual_start IS NOT NULL   THEN 'ONGOING'" in code
        assert "ELSE 'NOT_STARTED'" in code

    @pytest.mark.parametrize("name", ["well_task_activity", "well_task_activity_detail"])
    def test_progress_is_never_read_by_this_feature(self, name):
        # The decisive check for "progress does not affect this
        # classification": the column is not read at all, so it cannot.
        code = _code_only(load_sql(name))
        assert not re.search(r"\btd\.progress\b", code)
        assert not re.search(r"\bprogress\b", code, re.IGNORECASE)

    @pytest.mark.parametrize("name", ["well_task_activity", "well_task_activity_detail"])
    def test_planned_schedule_dates_are_never_read_as_proof_of_work(self, name):
        # startDate/endDate are a schedule, not evidence that work is under
        # way; only actual_start/actual_end are.
        code = _code_only(load_sql(name))
        for column in ("startDate", "endDate", "target_start", "target_end",
                       "committed_start", "committed_end"):
            assert column not in code

    def test_counts_are_aggregated_in_sql_not_left_to_the_caller(self, sql):
        code = _code_only(sql)
        assert "AS incomplete_task_count" in code
        assert "AS ongoing_task_count" in code
        assert "GROUP BY s.well_id" in code

    def test_last_task_date_is_a_max_action_on(self, sql):
        assert "MAX(s.latest_action_on)" in _code_only(sql)

    def test_the_detail_query_returns_only_incomplete_tasks(self, detail_sql):
        assert "WHERE s.task_state <> 'COMPLETED'" in _code_only(detail_sql)

    def test_the_detail_query_keeps_unmapped_work_visible(self, detail_sql):
        code = _code_only(detail_sql)
        assert "LEFT JOIN activity_mapping" in code
        assert "LEFT JOIN activity_detail" in code
        assert "dbo.mapping_master" in code
        assert "dbo.activity_master_mapping" not in code

    @pytest.mark.parametrize(
        "name", ["well_task_activity", "well_task_activity_detail"]
    )
    def test_shipped_queries_pass_the_read_only_guard(self, name):
        assert_read_only(load_sql(name))

    def test_the_state_fragment_is_shared_not_duplicated(self):
        # Both files include the same base, so the two can never drift into
        # different definitions of "the latest state of a task".
        for name in ("well_task_activity", "well_task_activity_detail"):
            path_text = (load_sql(name))
            assert "logical_task_state AS" in path_text


# ---------------------------------------------------------------------------
# Aggregation and the report-date count
# ---------------------------------------------------------------------------


class TestWellActivityFigures:
    def test_counts_are_reported_as_the_query_returned_them(self):
        service = WellActivityService(FakeWellRepository([activity_row()]))
        well = service.wells_for_date(REPORT_DATE, dataset())[0]
        assert well.open_task_count == 4
        assert well.incomplete_task_count == 2
        assert well.ongoing_task_count == 2
        assert well.completed_task_count == 6
        assert well.logical_task_count == 10

    def test_a_completed_task_is_never_counted_as_incomplete(self):
        # Ten tasks, all completed: nothing incomplete, nothing ongoing.
        service = WellActivityService(
            FakeWellRepository([
                activity_row(
                    logical_task_count=10,
                    completed_task_count=10,
                    open_task_count=0,
                    incomplete_task_count=0,
                    ongoing_task_count=0,
                    not_started_task_count=0,
                    ended_not_completed_task_count=0,
                )
            ])
        )
        well = service.wells_for_date(REPORT_DATE, dataset())[0]
        assert well.open_task_count == 0
        assert well.incomplete_task_count == 0
        assert well.ongoing_task_count == 0
        assert well.task_state_counts[TaskState.COMPLETED.value] == 10

    def test_the_four_states_are_always_reported_including_their_zeros(self):
        service = WellActivityService(FakeWellRepository([activity_row(ongoing_task_count=0)]))
        counts = service.wells_for_date(REPORT_DATE, dataset())[0].task_state_counts
        assert list(counts) == [state.value for state in TaskState]
        assert counts[TaskState.ONGOING.value] == 0

    def test_incomplete_and_ongoing_do_not_overlap_and_add_up_to_open(self):
        # The figures sit side by side on a well's row, so they must be
        # addable: ongoing is no longer counted inside incomplete as well.
        well = WellTaskActivity(
            well_id=1,
            logical_task_count=5,
            completed_task_count=1,
            open_task_count=4,
            incomplete_task_count=1,
            ongoing_task_count=3,
            not_started_task_count=1,
            ended_not_completed_task_count=0,
            today_reported_task_count=0,
            last_task_date=None,
        )
        assert well.incomplete_task_count + well.ongoing_task_count == well.open_task_count
        assert well.open_task_count + well.completed_task_count == well.logical_task_count

    def test_last_task_date_is_passed_through_untouched(self):
        service = WellActivityService(
            FakeWellRepository([activity_row(last_task_date=date(2025, 12, 20))])
        )
        well = service.wells_for_date(REPORT_DATE, dataset())[0]
        assert well.last_task_date == date(2025, 12, 20)

    def test_a_well_with_no_recorded_task_date_reports_none_not_a_substitute(self):
        service = WellActivityService(FakeWellRepository([activity_row(last_task_date=None)]))
        assert service.wells_for_date(REPORT_DATE, dataset())[0].last_task_date is None


class TestReportDateTaskCount:
    def test_a_task_on_the_selected_date_is_detected(self):
        service = WellActivityService(FakeWellRepository([activity_row(well_id=31474)]))
        day = dataset(build_task(make_row(well_id=31474)))
        well = service.wells_for_date(REPORT_DATE, day)[0]
        assert well.today_reported_task_count == 1
        assert well.has_task_on_report_date is True

    def test_no_task_on_the_selected_date_returns_zero(self):
        service = WellActivityService(FakeWellRepository([activity_row(well_id=31474)]))
        well = service.wells_for_date(REPORT_DATE, dataset())[0]
        assert well.today_reported_task_count == 0
        assert well.has_task_on_report_date is False

    def test_another_wells_tasks_do_not_count_towards_this_well(self):
        service = WellActivityService(FakeWellRepository([activity_row(well_id=31474)]))
        day = dataset(build_task(make_row(well_id=99999, task_daily_id=7)))
        wells = {well.well_id: well for well in service.wells_for_date(REPORT_DATE, day)}
        assert wells[31474].today_reported_task_count == 0

    def test_the_count_is_the_resolved_logical_grain_not_raw_rows(self):
        # One logical task standing in for three raw planning snapshots counts
        # once -- the grain daily_tasks.sql already resolved, reused here
        # rather than re-derived with a second, subtly different rule.
        day = dataset(
            build_task(make_row(well_id=31474, group_row_count=3, group_actual_entry_count=1))
        )
        service = WellActivityService(FakeWellRepository([activity_row(well_id=31474)]))
        assert service.wells_for_date(REPORT_DATE, day)[0].today_reported_task_count == 1

    def test_a_well_reporting_today_is_listed_even_if_the_counters_missed_it(self):
        # The day's own dataset always wins: a well that reported a task on
        # the date can never fall off the front page.
        day = dataset(build_task(make_row(well_id=40404)))
        service = WellActivityService(FakeWellRepository([activity_row(well_id=31474)]))
        wells = {well.well_id: well for well in service.wells_for_date(REPORT_DATE, day)}
        assert 40404 in wells
        assert wells[40404].today_reported_task_count == 1
        assert wells[40404].last_task_date == REPORT_DATE


# ---------------------------------------------------------------------------
# Detail, caching and query cost
# ---------------------------------------------------------------------------


class TestDetailAndQueryCost:
    def test_incomplete_tasks_are_grouped_to_their_own_well(self):
        repository = FakeWellRepository(
            [activity_row(well_id=1), activity_row(well_id=2)],
            [detail_row(well_id=1, task_code="A-1"), detail_row(well_id=2, task_code="B-2")],
        )
        service = WellActivityService(repository)
        assert [t.task_code for t in service.incomplete_tasks(REPORT_DATE, 1)] == ["A-1"]
        assert [t.task_code for t in service.incomplete_tasks(REPORT_DATE, 2)] == ["B-2"]

    def test_expanding_many_wells_costs_one_query_not_one_per_well(self):
        repository = FakeWellRepository(
            [activity_row(well_id=i) for i in range(1, 30)],
            [detail_row(well_id=i, task_code=f"T-{i}") for i in range(1, 30)],
        )
        service = WellActivityService(repository)
        for well_id in range(1, 30):
            service.incomplete_tasks(REPORT_DATE, well_id)
        assert repository.detail_calls == 1

    def test_the_counters_are_loaded_once_per_report_date(self):
        repository = FakeWellRepository([activity_row()])
        service = WellActivityService(repository)
        for _ in range(5):
            service.wells_for_date(REPORT_DATE, dataset())
        assert repository.activity_calls == 1

    def test_refresh_reloads_the_counters(self):
        repository = FakeWellRepository([activity_row()])
        service = WellActivityService(repository)
        service.wells_for_date(REPORT_DATE, dataset())
        service.wells_for_date(REPORT_DATE, dataset(), refresh=True)
        assert repository.activity_calls == 2

    def test_invalidate_drops_both_halves_for_the_date(self):
        repository = FakeWellRepository([activity_row()], [detail_row()])
        service = WellActivityService(repository)
        service.wells_for_date(REPORT_DATE, dataset())
        service.incomplete_tasks(REPORT_DATE, 31474)
        service.invalidate(REPORT_DATE)
        service.wells_for_date(REPORT_DATE, dataset())
        service.incomplete_tasks(REPORT_DATE, 31474)
        assert (repository.activity_calls, repository.detail_calls) == (2, 2)

    def test_an_unreadable_detail_row_never_loses_the_rest_of_the_well(self):
        repository = FakeWellRepository(
            [activity_row(well_id=1)],
            [detail_row(well_id=1, task_state="NOT_A_STATE"), detail_row(well_id=1, task_code="OK-1")],
        )
        service = WellActivityService(repository)
        assert [t.task_code for t in service.incomplete_tasks(REPORT_DATE, 1)] == ["OK-1"]


# ---------------------------------------------------------------------------
# The evidence handed to the model
# ---------------------------------------------------------------------------


class TestWellActivityEvidence:
    @pytest.fixture
    def service(self) -> WellActivityService:
        return WellActivityService(
            FakeWellRepository(
                [
                    activity_row(
                        well_id=31474,
                        open_task_count=6,
                        incomplete_task_count=4,
                        ongoing_task_count=2,
                    )
                ],
                [
                    detail_row(well_id=31474, task_code="ON-1", task_state="ONGOING"),
                    detail_row(well_id=31474, task_code="ON-2", task_state="ONGOING"),
                    detail_row(
                        well_id=31474,
                        task_code="NS-1",
                        task_state="NOT_STARTED",
                        actual_start=None,
                    ),
                ],
            )
        )

    def test_the_figures_are_supplied_already_calculated(self, service):
        evidence = service.evidence(REPORT_DATE, dataset(), 31474)
        activity = evidence["task_activity"]
        assert activity["open_task_count"] == 6
        assert activity["incomplete_task_count"] == 4
        assert activity["ongoing_task_count"] == 2
        # The model is handed the relationship, not left to work it out.
        assert (
            activity["incomplete_task_count"] + activity["ongoing_task_count"]
            == activity["open_task_count"]
        )
        assert activity["today_reported_task_count"] == 0
        assert activity["last_task_date"] == "2026-07-30"

    def test_only_ongoing_tasks_are_listed_as_ongoing(self, service):
        evidence = service.evidence(REPORT_DATE, dataset(), 31474)
        assert [task["task_code"] for task in evidence["ongoing_tasks"]] == ["ON-1", "ON-2"]
        assert all(task["actual_end"] is None for task in evidence["ongoing_tasks"])

    def test_each_listed_task_carries_its_own_identity(self, service):
        # One task_code can appear under two schedule_ids and be two tasks.
        task = service.evidence(REPORT_DATE, dataset(), 31474)["ongoing_tasks"][0]
        assert task["schedule_id"] == 359

    def test_no_task_in_the_evidence_carries_a_progress_value(self, service):
        # progress takes no part in any of these figures, so the model is
        # never handed one to reason from -- only the constraint saying so.
        evidence = service.evidence(REPORT_DATE, dataset(), 31474)
        for task in evidence["ongoing_tasks"]:
            assert "progress" not in task
        assert "progress" not in evidence["task_activity"]
<<<<<<< HEAD
        assert evidence["constraints"]["progress"].startswith("task_daily.progress is not used")
=======
        constraint = evidence["constraints"]["progress"]
        # The RULE is asserted, not one phrasing of it -- and the constraint
        # must not name a physical column, because it is sent to the model and
        # would be wrong the moment that column is renamed.
        assert "not used" in constraint
        assert "degree of completion" in constraint
        assert "task_daily" not in constraint
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)

    def test_the_model_is_told_what_each_figure_means(self, service):
        definitions = service.evidence(REPORT_DATE, dataset(), 31474)["definitions"]
        assert "last_task_date" in definitions
        assert "NOT a completion date" in definitions["last_task_date"]

    def test_a_well_with_no_task_evidence_gets_no_invented_zeros(self):
        service = WellActivityService(FakeWellRepository([]))
        assert service.evidence(REPORT_DATE, dataset(), 12345) is None

    def test_a_long_ongoing_list_is_sampled_without_losing_the_total(self):
        repository = FakeWellRepository(
            [
                activity_row(
                    well_id=1,
                    open_task_count=40,
                    incomplete_task_count=0,
                    ongoing_task_count=40,
                )
            ],
            [detail_row(well_id=1, task_code=f"T-{i}", task_state="ONGOING") for i in range(40)],
        )
        evidence = WellActivityService(repository).evidence(REPORT_DATE, dataset(), 1)
        assert len(evidence["ongoing_tasks"]) < 40
        assert evidence["ongoing_tasks_truncated"]["total"] == 40
        assert evidence["task_activity"]["ongoing_task_count"] == 40


class TestEvidenceIsStableAcrossRequests:
    """The explanation cache is keyed on a SHA-256 of the evidence, so the
    same question asked twice must produce byte-identical evidence. Anything
    that reorders a list inside it silently turns every cache hit into a
    fresh, paid-for LLM call -- observed for real when two tasks sharing a
    task_code under different schedule_ids came back either way round."""

    def test_the_detail_query_breaks_every_tie_in_its_ordering(self):
        code = _code_only(load_sql("well_task_activity_detail"))
        assert "ORDER BY s.well_id, s.task_state, s.task_code, s.schedule_id" in code

    def test_the_same_evidence_serialises_identically_twice(self):
        import json

        rows = [
            detail_row(well_id=1, task_code="SAME-1", schedule_id=356),
            detail_row(well_id=1, task_code="SAME-1", schedule_id=551),
        ]
        service = WellActivityService(
            FakeWellRepository([activity_row(well_id=1, ongoing_task_count=2)], rows)
        )
        first = json.dumps(service.evidence(REPORT_DATE, dataset(), 1), sort_keys=True)
        second = json.dumps(service.evidence(REPORT_DATE, dataset(), 1), sort_keys=True)
        assert first == second

    def test_two_tasks_sharing_a_code_stay_distinguishable_in_the_evidence(self):
        rows = [
            detail_row(well_id=1, task_code="SAME-1", schedule_id=356),
            detail_row(well_id=1, task_code="SAME-1", schedule_id=551),
        ]
        service = WellActivityService(
            FakeWellRepository([activity_row(well_id=1, ongoing_task_count=2)], rows)
        )
        tasks = service.evidence(REPORT_DATE, dataset(), 1)["ongoing_tasks"]
        assert [task["schedule_id"] for task in tasks] == [356, 551]


class TestDayScopeOverview:
    """The whole-view summary's evidence: every live well, not only the ones
    that reported something on the date."""

    @pytest.fixture
    def service(self) -> WellActivityService:
        return WellActivityService(
            FakeWellRepository(
                [
                    activity_row(well_id=1, open_task_count=0, incomplete_task_count=0,
                                 ongoing_task_count=0, completed_task_count=3,
                                 logical_task_count=3, not_started_task_count=0,
                                 ended_not_completed_task_count=0),
                    activity_row(well_id=2, open_task_count=9, incomplete_task_count=5,
                                 ongoing_task_count=4, not_started_task_count=5,
                                 ended_not_completed_task_count=0),
                    activity_row(well_id=3, open_task_count=2, incomplete_task_count=1,
                                 ongoing_task_count=1, not_started_task_count=1,
                                 ended_not_completed_task_count=0),
                ]
            )
        )

    def test_it_counts_wells_that_reported_nothing_on_the_date(self, service):
        day = dataset(build_task(make_row(well_id=3)))
        counts = service.day_evidence(REPORT_DATE, day)["well_counts"]
        assert counts["with_task_evidence"] == 3
        assert counts["reported_on_report_date"] == 1
        assert counts["no_task_on_report_date"] == 2
        assert counts["with_open_tasks"] == 2
        assert counts["with_open_tasks_and_nothing_reported"] == 1

    def test_the_totals_span_every_well_in_scope(self, service):
        counts = service.day_evidence(REPORT_DATE, dataset())["task_counts"]
        assert counts["open_total"] == 11
        assert counts["incomplete_total"] == 6
        assert counts["ongoing_total"] == 5
        assert counts["incomplete_total"] + counts["ongoing_total"] == counts["open_total"]

    def test_the_named_wells_are_ranked_by_open_work_and_are_stable(self, service):
        first = service.day_evidence(REPORT_DATE, dataset())["wells_with_most_open_work"]
        second = service.day_evidence(REPORT_DATE, dataset())["wells_with_most_open_work"]
        assert [well["well_id"] for well in first] == [2, 3]
        assert first == second, "the sample must be identical for identical data"

    def test_a_well_with_no_open_work_is_not_held_up_as_an_example(self, service):
        listed = service.day_evidence(REPORT_DATE, dataset())["wells_with_most_open_work"]
        assert 1 not in [well["well_id"] for well in listed]

    def test_a_long_list_is_sampled_without_losing_the_total(self):
        repository = FakeWellRepository(
            [
                activity_row(well_id=i, open_task_count=i, incomplete_task_count=i,
                             ongoing_task_count=0)
                for i in range(1, 40)
            ]
        )
        overview = WellActivityService(repository).day_evidence(REPORT_DATE, dataset())
        assert len(overview["wells_with_most_open_work"]) < 39
        assert overview["wells_with_most_open_work_truncated"]["total"] == 39
        assert overview["well_counts"]["with_open_tasks"] == 39

    def test_no_well_evidence_at_all_returns_nothing_rather_than_zeros(self):
        service = WellActivityService(FakeWellRepository([]))
        assert service.day_evidence(REPORT_DATE, dataset()) is None

    def test_the_overview_never_carries_progress(self, service):
        overview = service.day_evidence(REPORT_DATE, dataset())
        for well in overview["wells_with_most_open_work"]:
            assert "progress" not in well
<<<<<<< HEAD
        assert overview["constraints"]["progress"].startswith("task_daily.progress is not used")
=======
        constraint = overview["constraints"]["progress"]
        assert "not used" in constraint
        assert "degree of completion" in constraint
        assert "task_daily" not in constraint
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)


class TestProofRows:
    """The reason on each row restates that record's own columns. It is the
    difference between a count an operator can check and one they have to
    take on trust."""

    def _service(self, *detail):
        return WellActivityService(
            FakeWellRepository([activity_row(well_id=1)], list(detail))
        )

    def test_an_ongoing_task_says_it_started_and_has_not_ended(self):
        service = self._service(
            detail_row(
                well_id=1,
                task_state="ONGOING",
                actual_start=date(2026, 7, 20),
                actual_end=None,
                latest_action_on=date(2026, 7, 30),
            )
        )
        row = service.proof_rows(REPORT_DATE, 1)[0]
        # Exactly one of the two, never both -- that is what makes the figures
        # on the well's row add up.
        assert row["counts_as_incomplete"] is False
        assert row["counts_as_ongoing"] is True
        assert row["reason"] == (
            "Not completed (latest record 2026-07-30); actual start 2026-07-20 "
            "and no actual end recorded, so it counts as ongoing."
        )

    def test_a_task_that_never_started_says_so_and_is_not_ongoing(self):
        service = self._service(
            detail_row(well_id=1, task_state="NOT_STARTED", actual_start=None, actual_end=None)
        )
        row = service.proof_rows(REPORT_DATE, 1)[0]
        assert row["counts_as_incomplete"] is True
        assert row["counts_as_ongoing"] is False
        assert "no actual start and no actual end recorded" in row["reason"]
        assert "counts as incomplete rather than ongoing" in row["reason"]

    def test_an_ended_but_not_completed_task_reports_both_halves(self):
        service = self._service(
            detail_row(
                well_id=1,
                task_state="ENDED_NOT_COMPLETED",
                actual_start=date(2026, 7, 1),
                actual_end=date(2026, 7, 9),
            )
        )
        row = service.proof_rows(REPORT_DATE, 1)[0]
        assert "Not completed" in row["reason"]
        assert "actual end of 2026-07-09 is recorded" in row["reason"]
        assert row["counts_as_ongoing"] is False

    def test_the_proof_carries_what_the_task_is_and_never_invents_it(self):
        service = self._service(
            detail_row(well_id=1, activity_description=None, activity_code=None, wbs=None)
        )
        row = service.proof_rows(REPORT_DATE, 1)[0]
        assert row["description"] is None
        assert row["activity_code"] is None
        assert row["task_code"] == "FLME1180-31474"

    def test_the_proof_never_carries_a_progress_value(self):
        row = self._service(detail_row(well_id=1)).proof_rows(REPORT_DATE, 1)[0]
        assert "progress" not in row

    def test_a_well_with_nothing_incomplete_has_nothing_to_prove(self):
        assert self._service().proof_rows(REPORT_DATE, 1) == []


class TestSqlSources:
    def test_both_queries_behind_the_figures_are_offered(self):
        sources = WellActivityService.sql_sources(REPORT_DATE)
        assert [source["file"] for source in sources] == [
            "backend/sql/well_task_activity.sql",
            "backend/sql/well_task_activity_detail.sql",
        ]

    def test_the_text_is_the_expanded_query_that_actually_runs(self):
        sources = WellActivityService.sql_sources(REPORT_DATE)
        for source in sources:
            # The {{include:...}} directive is expanded, not passed through --
            # this is the string the driver is given. Checked against the
            # executable text, since the file's own comments describe the
            # directive by name.
            assert "{{include:" not in _code_only(source["sql"])
            assert "logical_task_state AS" in source["sql"]

    def test_the_date_is_listed_as_a_parameter_and_never_in_the_text(self):
        for source in WellActivityService.sql_sources(REPORT_DATE):
            assert any(REPORT_DATE.isoformat() in p for p in source["parameters"])
            assert REPORT_DATE.isoformat() not in source["sql"]
