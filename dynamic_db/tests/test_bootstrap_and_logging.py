"""The fingerprint-gated startup gate, and the logging pipeline underneath it.

These are the properties the whole "Dynamic DB must run before the caller
becomes operational" design rests on:

  * a connection failure is a FAILED state, never a silent READY;
  * status is readable by another thread WHILE a run is still in progress --
    that is what lets a frontend show real, live progress;
  * every run gets a run_id, and that run_id is retrievable afterward;
  * a secret never survives into a log line or a run record;
  * duration is measured with a monotonic clock, never wall-clock subtraction.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import date
from pathlib import Path

import pytest

from dynamic_db import bootstrap, capabilities, logging_setup
from tests import fixtures as fx

REPORT_DATE = date(2026, 8, 4)


class TestRunIdentity:
    def test_run_ids_are_unique_and_carry_a_non_decreasing_time_prefix(self):
        # Second-resolution: several ids minted within the same wall-clock
        # second legitimately share a time prefix, with the random suffix
        # breaking the tie arbitrarily -- so uniqueness is asserted always,
        # and ordering only of the time prefix itself, never the full string.
        ids = [logging_setup.new_run_id() for _ in range(5)]
        assert len(set(ids)) == 5
        prefixes = [int(i.split("-", 1)[0]) for i in ids]
        assert prefixes == sorted(prefixes)

    def test_run_context_stamps_and_then_clears_the_current_run_id(self):
        assert logging_setup.current_run_id() == ""
        with logging_setup.run_context("abc123") as run_id:
            assert run_id == "abc123"
            assert logging_setup.current_run_id() == "abc123"
        assert logging_setup.current_run_id() == ""

    def test_nested_run_contexts_restore_the_outer_id_on_exit(self):
        with logging_setup.run_context("outer"):
            with logging_setup.run_context("inner"):
                assert logging_setup.current_run_id() == "inner"
            assert logging_setup.current_run_id() == "outer"


class TestRedaction:
    @pytest.mark.parametrize(
        "text",
        [
            "PWD=hunter2;SERVER=x",
            "password: hunter2",
            "api_key=sk-abc123def456",
            "Authorization: Bearer abcdefgh12345678",
            "REASONING_API_KEY=sk-proj-abcdefghijklmnopqrstuvwx",
        ],
    )
    def test_a_secret_shaped_value_is_redacted(self, text):
        assert "hunter2" not in logging_setup.redact(text)
        assert "abc123def456" not in logging_setup.redact(text)
        assert "abcdefgh12345678" not in logging_setup.redact(text)
        assert "abcdefghijklmnopqrstuvwx" not in logging_setup.redact(text)

    def test_ordinary_text_is_unchanged(self):
        text = "dynamic[MILESTONES]: 9 approved table(s), structure abc123"
        assert logging_setup.redact(text) == text

    def test_redaction_is_centralised_in_one_filter_not_scattered(self):
        # If this filter class ever stops existing, secret redaction would
        # have to be re-implemented at every call site -- exactly what this
        # design avoids.
        assert hasattr(logging_setup, "_RedactionFilter")


class TestTimer:
    def test_duration_is_never_negative_and_reflects_real_elapsed_time(self):
        with logging_setup.Timer("op") as timer:
            time.sleep(0.01)
        assert timer.duration_ms is not None
        assert timer.duration_ms >= 10

    def test_the_timer_uses_a_monotonic_clock_not_wall_clock_subtraction(self):
        import inspect as pyinspect

        source = pyinspect.getsource(logging_setup.Timer)
        assert "perf_counter" in source
        assert "time.time()" not in source


class TestRunRecords:
    def test_a_run_record_round_trips_by_run_id(self, tmp_path, monkeypatch):
        from dynamic_db.config import get_settings

        monkeypatch.setattr(get_settings(), "log_dir", str(tmp_path))
        record = logging_setup.RunRecord(
            run_id="test-run-1",
            component="dynamic_db",
            operation="bootstrap",
            started_at="2026-01-01T00:00:00Z",
            status="READY",
        )
        logging_setup.write_run_record(record)
        loaded = logging_setup.read_run_record("test-run-1")
        assert loaded is not None
        assert loaded["run_id"] == "test-run-1"
        assert loaded["status"] == "READY"

    def test_a_missing_run_id_returns_none_not_an_exception(self, tmp_path, monkeypatch):
        from dynamic_db.config import get_settings

        monkeypatch.setattr(get_settings(), "log_dir", str(tmp_path))
        assert logging_setup.read_run_record("never-existed") is None

    def test_context_values_are_redacted_when_written(self, tmp_path, monkeypatch):
        from dynamic_db.config import get_settings

        monkeypatch.setattr(get_settings(), "log_dir", str(tmp_path))
        record = logging_setup.RunRecord(
            run_id="test-run-2",
            component="dynamic_db",
            operation="bootstrap",
            started_at="2026-01-01T00:00:00Z",
            context={"connection": "SERVER=x;PWD=hunter2"},
        )
        logging_setup.write_run_record(record)
        raw = (tmp_path / "runs" / "test-run-2.json").read_text(encoding="utf-8")
        assert "hunter2" not in raw

    def test_list_run_records_returns_newest_first(self, tmp_path, monkeypatch):
        from dynamic_db.config import get_settings

        monkeypatch.setattr(get_settings(), "log_dir", str(tmp_path))
        for i in range(3):
            logging_setup.write_run_record(
                logging_setup.RunRecord(
                    run_id=f"run-{i}", component="dynamic_db", operation="bootstrap",
                    started_at="2026-01-01T00:00:00Z",
                )
            )
            time.sleep(0.01)
        records = logging_setup.list_run_records(limit=10)
        assert [r["run_id"] for r in records] == ["run-2", "run-1", "run-0"]

    def test_pruning_removes_only_records_older_than_retention(self, tmp_path, monkeypatch):
        from dynamic_db.config import get_settings

        monkeypatch.setattr(get_settings(), "log_dir", str(tmp_path))
        logging_setup.write_run_record(
            logging_setup.RunRecord(
                run_id="old", component="dynamic_db", operation="bootstrap", started_at="x"
            )
        )
        path = tmp_path / "runs" / "old.json"
        old_time = time.time() - 999 * 86400
        import os

        os.utime(path, (old_time, old_time))
        removed = logging_setup.prune_run_records(retention_days=30)
        assert removed == 1
        assert not path.exists()

    def test_zero_retention_disables_pruning(self, tmp_path, monkeypatch):
        from dynamic_db.config import get_settings

        monkeypatch.setattr(get_settings(), "log_dir", str(tmp_path))
        logging_setup.write_run_record(
            logging_setup.RunRecord(
                run_id="keep-forever", component="dynamic_db", operation="bootstrap", started_at="x"
            )
        )
        assert logging_setup.prune_run_records(retention_days=0) == 0


class TestBootstrapFailureHandling:
    def test_a_connection_failure_is_a_failed_state_never_a_silent_ready(self, tmp_path, monkeypatch):
        from dynamic_db import db as db_module
        from dynamic_db.config import get_settings

        monkeypatch.setattr(get_settings(), "dynamic_cache_dir", str(tmp_path / "cache"))
        monkeypatch.setattr(get_settings(), "log_dir", str(tmp_path / "logs"))

        def explode():
            raise db_module.DatabaseUnavailable("could not connect")

        monkeypatch.setattr(db_module, "check_connectivity", explode)
        orchestrator = bootstrap.BootstrapOrchestrator()
        status = orchestrator.run()

        assert status["state"] == bootstrap.STATE_FAILED
        assert status["ready"] is False
        assert status["error"]["type"] == "connection_failed"
        assert orchestrator.is_ready is False

    def test_a_failure_still_writes_a_retrievable_run_record(self, tmp_path, monkeypatch):
        from dynamic_db import db as db_module
        from dynamic_db.config import get_settings

        monkeypatch.setattr(get_settings(), "dynamic_cache_dir", str(tmp_path / "cache"))
        monkeypatch.setattr(get_settings(), "log_dir", str(tmp_path / "logs"))
        monkeypatch.setattr(
            db_module, "check_connectivity",
            lambda: (_ for _ in ()).throw(db_module.DatabaseUnavailable("down")),
        )
        orchestrator = bootstrap.BootstrapOrchestrator()
        status = orchestrator.run()
        record = logging_setup.read_run_record(status["run_id"])
        assert record is not None
        assert record["status"] == "FAILED"
        assert record["error"]["type"] == "connection_failed"

    def test_status_is_readable_from_another_thread_while_a_run_is_in_progress(
        self, tmp_path, monkeypatch
    ):
        from dynamic_db import db as db_module
        from dynamic_db.config import get_settings

        monkeypatch.setattr(get_settings(), "dynamic_cache_dir", str(tmp_path / "cache"))
        monkeypatch.setattr(get_settings(), "log_dir", str(tmp_path / "logs"))

        entered_connect = threading.Event()
        released = threading.Event()

        def slow_connect():
            entered_connect.set()
            released.wait(timeout=2)
            raise db_module.DatabaseUnavailable("down")

        monkeypatch.setattr(db_module, "check_connectivity", slow_connect)
        orchestrator = bootstrap.BootstrapOrchestrator()

        observed = []

        def poll():
            # Wait until the run is DEFINITELY inside the blocking connect
            # call before reading status, so this test asserts a guarantee
            # (status is readable mid-run) rather than a race.
            entered_connect.wait(timeout=2)
            observed.append(orchestrator.status["state"])
            released.set()

        thread = threading.Thread(target=poll)
        thread.start()
        status = orchestrator.run()
        thread.join(timeout=2)

        assert bootstrap.STATE_CONNECTING in observed
        assert status["state"] == bootstrap.STATE_FAILED

    def test_a_database_with_zero_approved_tables_fails_closed(self, tmp_path, monkeypatch):
        from dynamic_db import introspect as introspect_module
        from dynamic_db.config import get_settings
        from dynamic_db.introspect import SchemaSnapshot

        monkeypatch.setattr(get_settings(), "dynamic_cache_dir", str(tmp_path / "cache"))
        monkeypatch.setattr(get_settings(), "log_dir", str(tmp_path / "logs"))
        from dynamic_db import db as db_module

        monkeypatch.setattr(db_module, "check_connectivity", lambda: {"database_name": "x"})
        monkeypatch.setattr(
            introspect_module, "introspect", lambda **k: SchemaSnapshot(database="empty:1/db")
        )
        orchestrator = bootstrap.BootstrapOrchestrator()
        status = orchestrator.run()
        assert status["state"] == bootstrap.STATE_FAILED
        assert status["error"]["type"] == "no_approved_tables"

    def test_bootstrap_never_raises_even_on_a_completely_unexpected_error(
        self, tmp_path, monkeypatch
    ):
        from dynamic_db import db as db_module
        from dynamic_db.config import get_settings

        monkeypatch.setattr(get_settings(), "dynamic_cache_dir", str(tmp_path / "cache"))
        monkeypatch.setattr(get_settings(), "log_dir", str(tmp_path / "logs"))

        def explode():
            raise RuntimeError("something nobody anticipated")

        monkeypatch.setattr(db_module, "check_connectivity", explode)
        orchestrator = bootstrap.BootstrapOrchestrator()
        status = orchestrator.run()  # must not raise
        assert status["state"] == bootstrap.STATE_FAILED


class TestBootstrapSuccess:
    def test_a_successful_run_reaches_ready_and_reports_every_step(self, tmp_path, monkeypatch):
        from dynamic_db import db as db_module
        from dynamic_db import introspect as introspect_module
        from dynamic_db.config import get_settings

        monkeypatch.setattr(get_settings(), "dynamic_cache_dir", str(tmp_path / "cache"))
        monkeypatch.setattr(get_settings(), "log_dir", str(tmp_path / "logs"))
        monkeypatch.setattr(db_module, "check_connectivity", lambda: {"database_name": "fixture"})
        monkeypatch.setattr(introspect_module, "introspect", lambda **k: fx.snapshot())
        monkeypatch.setattr(introspect_module, "load_previous_snapshot", lambda: None)
        monkeypatch.setattr(introspect_module, "store_previous_snapshot", lambda snap: None)

        orchestrator = bootstrap.BootstrapOrchestrator()
        status = orchestrator.run()

        assert status["state"] == bootstrap.STATE_READY
        assert status["ready"] is True
        names = [s["name"] for s in status["steps"]]
        for expected in (
            bootstrap.STATE_CONNECTING,
            bootstrap.STATE_INTROSPECTING,
            bootstrap.STATE_GENERATING_FINGERPRINT,
            bootstrap.STATE_COMPARING_FINGERPRINT,
            bootstrap.STATE_VALIDATING,
            bootstrap.STATE_LOADING_ARTIFACTS,
            bootstrap.STATE_READY,
        ):
            assert expected in names
        assert status["structure_fingerprint"]
        assert status["duration_ms"] > 0

    def test_get_orchestrator_returns_the_same_process_wide_instance(self):
        bootstrap.reset_orchestrator()
        try:
            first = bootstrap.get_orchestrator()
            second = bootstrap.get_orchestrator()
            assert first is second
        finally:
            bootstrap.reset_orchestrator()

    def test_the_status_never_contains_a_secret(self, tmp_path, monkeypatch):
        from dynamic_db import db as db_module
        from dynamic_db import introspect as introspect_module
        from dynamic_db.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "dynamic_cache_dir", str(tmp_path / "cache"))
        monkeypatch.setattr(settings, "log_dir", str(tmp_path / "logs"))
        monkeypatch.setattr(settings, "db_password", "hunter2")
        monkeypatch.setattr(db_module, "check_connectivity", lambda: {"database_name": "fixture"})
        monkeypatch.setattr(introspect_module, "introspect", lambda **k: fx.snapshot())
        monkeypatch.setattr(introspect_module, "load_previous_snapshot", lambda: None)
        monkeypatch.setattr(introspect_module, "store_previous_snapshot", lambda snap: None)

        orchestrator = bootstrap.BootstrapOrchestrator()
        status = orchestrator.run()
        assert "hunter2" not in json.dumps(status, default=str)
