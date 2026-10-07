"""Builds the resolved daily dataset, and filters it for drill-down.

One database round trip per report date produces the full resolved evidence;
every drill-down level (group, status, well, task) is then served from that same
in-memory dataset. This is what guarantees the numbers on the summary card and
the rows behind them can never disagree -- and it is why clicking through the
hierarchy issues no extra queries per well.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date
from typing import Dict, List, Optional, Tuple

from app.config.settings import get_settings
from app.models.daily import DailyDataset, DailyTask, QuantityStatus
from app.repositories.daily_repository import DailyRepository
from app.services.validation_service import build_task

logger = logging.getLogger(__name__)


class _DatasetCache:
    """Tiny TTL cache keyed by report date.

    Drill-down is interactive: without this, every click would re-run the day's
    query. The TTL is configurable and a refresh always bypasses it.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: Dict[date, Tuple[float, DailyDataset]] = {}

    def get(self, key: date, ttl_seconds: int) -> Optional[DailyDataset]:
        if ttl_seconds <= 0:
            return None
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            stored_at, dataset = entry
            if time.monotonic() - stored_at > ttl_seconds:
                self._entries.pop(key, None)
                return None
            return dataset

    def put(self, key: date, dataset: DailyDataset) -> None:
        with self._lock:
            self._entries[key] = (time.monotonic(), dataset)

    def invalidate(self, key: Optional[date] = None) -> None:
        with self._lock:
            if key is None:
                self._entries.clear()
            else:
                self._entries.pop(key, None)


class DailyService:
    """Loads and filters the deterministic daily evidence."""

    def __init__(self, repository: Optional[DailyRepository] = None) -> None:
        self._repository = repository or DailyRepository()
        self._cache = _DatasetCache()

    # ------------------------------------------------------------------
    def get_dataset(self, report_date: date, *, refresh: bool = False) -> DailyDataset:
        """Resolved evidence for ``report_date``, from cache when warm."""
        settings = get_settings()
        if refresh:
            self._cache.invalidate(report_date)
        else:
            cached = self._cache.get(report_date, settings.daily_cache_ttl_seconds)
            if cached is not None:
                logger.debug("daily dataset for %s served from cache", report_date)
                return cached

        rows, counters = self._repository.fetch_day(report_date)
        tasks: List[DailyTask] = []
        skipped = 0
        for row in rows:
            try:
                tasks.append(build_task(row))
            except Exception:  # noqa: BLE001 - one bad row must not lose the day
                skipped += 1
                logger.exception(
                    "skipping unreadable daily row id=%s", row.get("task_daily_id")
                )
        if skipped:
            logger.warning("%d daily row(s) could not be interpreted for %s", skipped, report_date)

        dataset = DailyDataset(report_date=report_date, tasks=tasks, counters=counters)
        self._cache.put(report_date, dataset)
        return dataset

    def invalidate(self, report_date: Optional[date] = None) -> None:
        self._cache.invalidate(report_date)

    # ------------------------------------------------------------------
    def filter_tasks(
        self,
        dataset: DailyDataset,
        *,
        uom: Optional[str] = None,
        wbs: Optional[str] = None,
        activity_code: Optional[str] = None,
        status: Optional[str] = None,
        well_id: Optional[int] = None,
    ) -> List[DailyTask]:
        """Select tasks by group key and/or deterministic status.

        The classification being filtered on was computed by
        validation_service; this only selects, it never reclassifies. An empty
        string matches the explicit "not recorded" group, which is how a task
        with no UOM or no WBS stays reachable instead of disappearing.
        """
        status_value = self._parse_status(status)
        selected = []
        for task in dataset.tasks:
            if uom is not None and task.uom_key != uom:
                continue
            if wbs is not None and task.wbs_key != wbs:
                continue
            if activity_code is not None and task.activity_key != activity_code:
                continue
            if status_value is not None and task.quantity_status is not status_value:
                continue
            if well_id is not None and task.well_id != well_id:
                continue
            selected.append(task)
        return selected

    @staticmethod
    def _parse_status(status: Optional[str]) -> Optional[QuantityStatus]:
        if status is None or status.strip() == "":
            return None
        try:
            return QuantityStatus(status.strip().upper())
        except ValueError as exc:
            valid = ", ".join(member.value for member in QuantityStatus)
            raise ValueError(f"Unknown status {status!r}. Valid values: {valid}") from exc

    # ------------------------------------------------------------------
    def recent_dates(self, limit: int = 30) -> List[Dict[str, object]]:
        """Recent dates that actually carry daily entries, newest first."""
        rows = self._repository.fetch_dates_with_activity(limit)
        return [
            {
                "report_date": row["report_date"],
                "row_count": int(row["row_count"]),
                "well_count": int(row["well_count"]),
            }
            for row in rows
        ]
