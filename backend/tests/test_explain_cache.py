"""Explanation caching: identical evidence must not call the LLM twice.

Pressing "AI summary" again for the same well, group or day -- with nothing
changed underneath it -- must reuse the earlier answer rather than spend new
LLM tokens on a request that would classify no differently than the last one.
Correctness comes from keying the cache on the evidence's own content hash
(``LLMService._evidence_hash``): identical evidence is, by construction, the
same question asked twice, so the same answer is correct; changed evidence
hashes differently and is never served a stale answer regardless of any size
or time limit. A "Refresh" additionally purges a date's cache outright (see
``TestRefreshInvalidatesTheCache``), so it always gets a fresh explanation even
in the rare case where the reloaded data comes back byte-identical.
"""

from __future__ import annotations

import threading
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

from app.api import dependencies
from app.models.daily import DayCounters
from app.services.daily_service import DailyService
from app.services.llm_service import LLMService, LLMUnavailable, _ExplainCache
from tests.conftest import REPORT_DATE, make_row, stub_well_activity_service

ROWS = [
    make_row(task_daily_id=1, well_id=101, planned=10, actual_quantity=10),
    make_row(task_daily_id=2, well_id=102, planned=5, actual_quantity=4),
]

#: Shaped like well_task_activity.sql returns, for the same two wells.
ACTIVITY_ROWS = [
    {
        "well_id": well_id,
        "logical_task_count": 4,
        "completed_task_count": 2,
        "incomplete_task_count": 2,
        "ongoing_task_count": 1,
        "not_started_task_count": 1,
        "ended_not_completed_task_count": 0,
        "last_task_date": REPORT_DATE,
    }
    for well_id in (101, 102)
]


class StubRepository:
    """Returns fixed rows for one date and nothing for any other."""

    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows

    def fetch_day(self, report_date: date):
        if report_date != REPORT_DATE:
            return [], DayCounters()
        rows = [dict(row, action_on=report_date) for row in self._rows]
        return rows, DayCounters(raw_row_count=len(rows), logical_task_count=len(rows))

    def fetch_dates_with_activity(self, limit: int):
        return [{"report_date": REPORT_DATE, "row_count": len(self._rows), "well_count": 2}]


class CountingLLMService(LLMService):
    """A real ``LLMService`` (real cache, real ``explain_safe``) whose
    ``explain`` never leaves the process -- it counts calls instead of
    reaching a provider, so a test can assert exactly how many times the
    expensive step was actually invoked.

<<<<<<< HEAD
    Deliberately does **not** call ``LLMService.__init__`` (which would read
    ``get_settings().explain_cache_file`` and read/write the real
    ``backend/.cache/explain_cache.json``): every test here gets a cache of
    its own, in memory only unless ``cache_file`` is given, so tests never
    depend on -- or leave dirty state in -- the real cache file on disk.
    """

    def __init__(self, cache_file: Optional["Path"] = None) -> None:
        self.calls = 0
        self._cache = _ExplainCache(cache_file)

    def explain(self, evidence):
=======
    Every instance gets a cache of its own, so no test depends on what
    another one left behind.
    """

    def __init__(self) -> None:
        self.calls = 0
        self._cache = _ExplainCache()

    def explain(self, evidence, **_overrides):
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
        self.calls += 1
        return f"explanation #{self.calls}", "test-model"


@pytest.fixture
def client_and_llm(monkeypatch):
    from app.main import app

    daily_service = DailyService(repository=StubRepository(ROWS))
    llm_service = CountingLLMService()
    # A well scope's evidence now carries that well's task activity; stub it
    # so these tests stay offline and their evidence stays fixed.
    activity_service = stub_well_activity_service(ACTIVITY_ROWS)
    monkeypatch.setattr(dependencies, "_daily_service", daily_service)
    monkeypatch.setattr(dependencies, "_llm_service", llm_service)
    app.dependency_overrides[dependencies.get_daily_service] = lambda: daily_service
    app.dependency_overrides[dependencies.get_llm_service] = lambda: llm_service
    app.dependency_overrides[dependencies.get_well_activity_service] = lambda: activity_service
    try:
        yield TestClient(app), llm_service
    finally:
        app.dependency_overrides.clear()


WELL_PAYLOAD = {"report_date": str(REPORT_DATE), "scope": "well", "well_id": 101}


class TestRepeatedRequestsAreServedFromCache:
    def test_a_second_identical_request_does_not_call_the_llm_again(self, client_and_llm):
        client, llm_service = client_and_llm

        first = client.post("/api/daily/explain", json=WELL_PAYLOAD).json()
        second = client.post("/api/daily/explain", json=WELL_PAYLOAD).json()

        assert llm_service.calls == 1
        assert first["cached"] is False
        assert second["cached"] is True
        assert second["explanation"] == first["explanation"]
        assert second["available"] is True

    def test_a_different_scope_is_not_served_the_other_scopes_cache(self, client_and_llm):
        client, llm_service = client_and_llm

        client.post("/api/daily/explain", json=WELL_PAYLOAD)
        other_well = client.post(
            "/api/daily/explain",
            json={**WELL_PAYLOAD, "well_id": 102},
        ).json()

        assert llm_service.calls == 2
        assert other_well["cached"] is False

    def test_a_failed_explanation_is_never_cached(self, client_and_llm, monkeypatch):
        client, llm_service = client_and_llm
        attempts = {"n": 0}

        def flaky(evidence):
            attempts["n"] += 1
            raise LLMUnavailable("provider down")

        monkeypatch.setattr(llm_service, "explain", flaky)

        first = client.post("/api/daily/explain", json=WELL_PAYLOAD).json()
        second = client.post("/api/daily/explain", json=WELL_PAYLOAD).json()

        assert first["available"] is False
        assert second["available"] is False
        # Both requests actually reached the (failing) provider -- a
        # transient failure must never be remembered as a permanent one.
        assert attempts["n"] == 2


class TestRefreshInvalidatesTheCache:
    def test_refreshing_the_summary_forces_a_fresh_explanation(self, client_and_llm):
        client, llm_service = client_and_llm

        client.post("/api/daily/explain", json=WELL_PAYLOAD)
        client.get(f"/api/daily/summary?date={REPORT_DATE}&refresh=true")
        client.post("/api/daily/explain", json=WELL_PAYLOAD)

        assert llm_service.calls == 2

    def test_refreshing_the_well_list_also_forces_a_fresh_explanation(self, client_and_llm):
        client, llm_service = client_and_llm

        client.post("/api/daily/explain", json=WELL_PAYLOAD)
        client.get(f"/api/daily/group-details?date={REPORT_DATE}&refresh=true")
        client.post("/api/daily/explain", json=WELL_PAYLOAD)

        assert llm_service.calls == 2

    def test_a_plain_reload_without_refresh_still_uses_the_cache(self, client_and_llm):
        """Refresh purges the cache; an ordinary re-fetch of the same date must not."""
        client, llm_service = client_and_llm

        client.post("/api/daily/explain", json=WELL_PAYLOAD)
        client.get(f"/api/daily/summary?date={REPORT_DATE}")  # no refresh=true
        client.post("/api/daily/explain", json=WELL_PAYLOAD)

        assert llm_service.calls == 1


class TestExplainSafeCachingDirectly:
    """Same guarantees, exercised directly against ``LLMService`` -- no HTTP,
    no FastAPI app, just the cache itself."""

    def test_identical_evidence_is_cached(self):
        service = CountingLLMService()
        evidence = {"report_date": "2026-08-01", "scope": "day", "summary": {"task_count": 5}}

        first = service.explain_safe(evidence)
        second = service.explain_safe(evidence)

        assert service.calls == 1
        assert first["cached"] is False
        assert second["cached"] is True
        assert second["explanation"] == first["explanation"]

    def test_evidence_that_differs_by_one_field_is_not_conflated(self):
        service = CountingLLMService()
        service.explain_safe({"report_date": "2026-08-01", "scope": "day", "summary": {"task_count": 5}})
        service.explain_safe({"report_date": "2026-08-01", "scope": "day", "summary": {"task_count": 6}})

        assert service.calls == 2

    def test_invalidate_date_forces_a_fresh_call_for_that_date(self):
        service = CountingLLMService()
        evidence = {"report_date": "2026-08-01", "scope": "day", "summary": {"task_count": 5}}

        service.explain_safe(evidence)
        service.invalidate_date(date(2026, 8, 1))
        result = service.explain_safe(evidence)

        assert service.calls == 2
        assert result["cached"] is False

    def test_invalidating_one_date_leaves_another_dates_cache_intact(self):
        service = CountingLLMService()
        evidence = {"report_date": "2026-08-01", "scope": "day", "summary": {"task_count": 5}}

        service.explain_safe(evidence)
        service.invalidate_date(date(2026, 8, 2))  # a different date
        result = service.explain_safe(evidence)

        assert service.calls == 1
        assert result["cached"] is True

    def test_the_cache_is_bounded_and_evicts_the_oldest_entry(self, monkeypatch):
        from app.config.settings import get_settings

        monkeypatch.setenv("EXPLAIN_CACHE_MAX_ENTRIES", "2")
        get_settings.cache_clear()
        try:
            service = CountingLLMService()
            e1 = {"report_date": "2026-08-01", "scope": "well", "group": {"well_id": 1}}
            e2 = {"report_date": "2026-08-01", "scope": "well", "group": {"well_id": 2}}
            e3 = {"report_date": "2026-08-01", "scope": "well", "group": {"well_id": 3}}

            service.explain_safe(e1)
            service.explain_safe(e2)
            service.explain_safe(e3)  # over budget -- evicts e1, the oldest
            result = service.explain_safe(e1)  # must be a fresh call again

            assert service.calls == 4
            assert result["cached"] is False
        finally:
            get_settings.cache_clear()


class TestConcurrentRequestsDoNotStampedeTheLLM:
    """Two requests racing on the exact same not-yet-cached evidence.

    This reproduces a real bug caught while verifying the cache by hand in a
    browser: React's development-mode double-effect fired two nearly
    simultaneous requests for the same well on the very first click, both
    missed the (empty) cache, and both called the LLM -- producing two
    differently-worded answers (temperature > 0) and leaving whichever
    finished last in the cache, mislabelled ``cached: true`` on the response
    that happened to arrive second. `explain_safe`'s per-key lock closes that
    window: a second concurrent request for the same key must wait for the
    first to finish, then reuse its answer rather than generating its own.
    """

    def test_two_concurrent_requests_for_the_same_evidence_call_the_llm_once(self):
        service = CountingLLMService()
        entered_explain = threading.Event()
        release = threading.Event()

        def slow_explain(evidence):
            entered_explain.set()
            release.wait(timeout=5)
            service.calls += 1
            return f"explanation #{service.calls}", "test-model"

        service.explain = slow_explain  # type: ignore[assignment]
        evidence = {"report_date": "2026-08-01", "scope": "well", "group": {"well_id": 1}}
        results: List[Optional[Dict[str, Any]]] = [None, None]

        def call(index: int) -> None:
            results[index] = service.explain_safe(evidence)

        first = threading.Thread(target=call, args=(0,))
        first.start()
        assert entered_explain.wait(timeout=5), "the first request never reached explain()"

        # The first request is now inside explain(), holding the per-key
        # lock, blocked on `release`. A second, concurrent request for the
        # exact same evidence must queue behind that lock, not also reach
        # explain() -- which is exactly what happened before this lock
        # existed: two nearly-simultaneous requests both saw an empty cache
        # and both called the LLM.
        second = threading.Thread(target=call, args=(1,))
        second.start()
        time.sleep(0.2)  # give the second request every chance to slip through
        assert service.calls == 0, "a second concurrent request reached the LLM"

        release.set()
        first.join(timeout=5)
        second.join(timeout=5)

        assert service.calls == 1
        assert results[0]["available"] and results[1]["available"]
        assert results[0]["explanation"] == results[1]["explanation"]
        # Exactly one of the two actually generated it; the other reused it.
        assert sorted(r["cached"] for r in results) == [False, True]


<<<<<<< HEAD
class TestCachePersistsAcrossRestarts:
    """The whole reason this cache writes to disk: a brand-new process must
    not have to re-ask the LLM a question an earlier run already answered.

    Before this, the cache lived only in memory, so a dev auto-reload, a
    redeploy, or simply stopping and re-running the app threw every cached
    explanation away -- the very next request for a well explained a minute
    earlier paid for a fresh LLM call again, indistinguishable from a
    genuinely new question. Each test below constructs a *second*,
    independent ``_ExplainCache``/``CountingLLMService`` pointed at the same
    file to stand in for that restart -- nothing is shared in memory between
    the two, exactly as nothing would be across a real process boundary.
    """

    def test_a_new_cache_instance_loads_what_an_earlier_one_persisted(self, tmp_path):
        cache_file = tmp_path / "explain_cache.json"
        entry = {"available": True, "explanation": "well 30365 is on plan", "model": "m", "error": None}

        first_process = _ExplainCache(cache_file)
        first_process.put("2026-08-01", "hash-a", entry, max_entries=500)

        second_process = _ExplainCache(cache_file)  # simulates a restart
        assert second_process.get("2026-08-01", "hash-a") == entry

    def test_a_missing_file_starts_cold_without_error(self, tmp_path):
        cache = _ExplainCache(tmp_path / "never-written.json")
        assert cache.get("2026-08-01", "hash-a") is None

    def test_a_corrupt_file_starts_cold_without_crashing(self, tmp_path):
        cache_file = tmp_path / "explain_cache.json"
        cache_file.write_text("{not valid json at all", encoding="utf-8")
        cache = _ExplainCache(cache_file)  # must not raise
        assert cache.get("2026-08-01", "hash-a") is None

    def test_invalidate_date_is_itself_persisted(self, tmp_path):
        cache_file = tmp_path / "explain_cache.json"
        entry = {"available": True, "explanation": "x", "model": "m", "error": None}

        first_process = _ExplainCache(cache_file)
        first_process.put("2026-08-01", "hash-a", entry, max_entries=500)
        first_process.invalidate_date("2026-08-01")

        second_process = _ExplainCache(cache_file)  # simulates a restart
        assert second_process.get("2026-08-01", "hash-a") is None

    def test_file_path_none_never_touches_disk(self, tmp_path):
        # The default for tests elsewhere in this module: no file_path means
        # purely in-memory, exactly like before persistence existed.
        cache = _ExplainCache(None)
        entry = {"available": True, "explanation": "x", "model": "m", "error": None}
        cache.put("2026-08-01", "hash-a", entry, max_entries=500)
        assert cache.get("2026-08-01", "hash-a") == entry
        assert list(tmp_path.iterdir()) == []

    def test_a_brand_new_llm_service_reuses_a_previous_ones_cached_answer(self, tmp_path):
        """The end-to-end version: this is the actual "restart the app,
        ask the same thing again" scenario, through the real explain_safe
        path rather than the cache directly."""
        cache_file = tmp_path / "explain_cache.json"
        evidence = {"report_date": "2026-08-01", "scope": "well", "group": {"well_id": 30365}}

        first_service = CountingLLMService(cache_file=cache_file)
        first_result = first_service.explain_safe(evidence)
        assert first_service.calls == 1
        assert first_result["cached"] is False

        second_service = CountingLLMService(cache_file=cache_file)  # a "restart"
        second_result = second_service.explain_safe(evidence)

        assert second_service.calls == 0, "a fresh instance re-called the LLM for an already-answered question"
        assert second_result["cached"] is True
        assert second_result["explanation"] == first_result["explanation"]
=======
class TestCacheLastsOneRunOnly:
    """An explanation belongs to the run that produced it: a new run -- a new
    LLMService, exactly what a restart creates -- asks the model afresh, and
    nothing is ever written to disk."""

    def test_a_new_run_does_not_reuse_an_earlier_runs_answer(self):
        evidence = {"report_date": "2026-08-01", "scope": "well", "group": {"well_id": 30365}}

        first_run = CountingLLMService()
        assert first_run.explain_safe(evidence)["cached"] is False
        assert first_run.explain_safe(evidence)["cached"] is True  # same run: reused
        assert first_run.calls == 1

        second_run = CountingLLMService()  # a restart
        result = second_run.explain_safe(evidence)
        assert second_run.calls == 1
        assert result["cached"] is False

    def test_nothing_is_written_to_disk(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        service = LLMService()
        service._cache.put(
            "2026-08-01",
            "hash-a",
            {"available": True, "explanation": "x", "model": "m", "error": None},
            max_entries=500,
        )
        assert list(tmp_path.iterdir()) == []
        assert not hasattr(service._cache, "_persist")

    def test_refresh_drops_that_dates_answers(self):
        evidence = {"report_date": "2026-08-01", "scope": "day", "summary": {"task_count": 1}}
        service = CountingLLMService()
        service.explain_safe(evidence)
        service._cache.invalidate_date("2026-08-01")
        assert service.explain_safe(evidence)["cached"] is False
        assert service.calls == 2

>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
