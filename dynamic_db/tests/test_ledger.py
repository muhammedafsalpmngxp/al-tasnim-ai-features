"""The table ledger: history table by table, triage of new tables, read-only.

Offline: the catalogue is either built by hand (for ``reconcile``) or served by
a fake cursor that records every statement it is handed (for ``read_catalog``),
so nothing here needs SQL Server.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Dict, List, Optional

import pytest

from dynamic_db import ledger
from dynamic_db.ledger import TableMeta

APPROVED = ["well.task_daily", "well.well_master"]
IGNORE = ["tmp_*", "*_bak", "*backup*"]

TASK_COLUMNS = {
    "task_daily_id": "int(4) NOT NULL",
    "well_id": "varchar(20) NULL",
    "task_code": "nvarchar(100) NULL",
    "ActionOn": "date(3) NULL",
    "planned": "decimal(9) NULL",
    "daily_data": "nvarchar(-1) NULL",
}
WELL_COLUMNS = {"well_id": "int(4) NOT NULL", "well_name": "nvarchar(200) NULL"}


def meta(name, columns=None, *, created="2026-01-01T00:00:00", modified=None, references=None):
    return TableMeta(
        name=name,
        create_date=created,
        modify_date=modified or created,
        columns=dict(columns if columns is not None else {"id": "int(4) NOT NULL"}),
        references=sorted(references or []),
    )


def baseline() -> Dict[str, TableMeta]:
    tables = [
        meta("well.task_daily", TASK_COLUMNS, references=["well.well_master"]),
        meta("well.well_master", WELL_COLUMNS),
        meta("dbo.lookup_colour", {"id": "int(4) NOT NULL", "colour": "nvarchar(40) NULL"}),
    ]
    return {t.key(): t for t in tables}


def run(registry, catalog, now="2026-09-01T00:00:00+00:00", approved=APPROVED):
    return ledger.reconcile(
        registry,
        catalog,
        approved=approved,
        ignore_patterns=IGNORE,
        similarity_threshold=0.6,
        database="test",
        now=now,
    )


def types(events) -> List[str]:
    return [event["type"] for event in events]


class TestFirstPass:
    def test_records_a_baseline_not_one_addition_per_existing_table(self):
        registry, events = run({}, baseline())
        assert types(events) == [ledger.EVENT_TRACKING_STARTED]
        assert set(registry["tables"]) == set(baseline())

    def test_keeps_sql_servers_own_creation_date_for_the_past(self):
        registry, _ = run({}, baseline())
        assert registry["tables"]["well.task_daily"]["create_date"] == "2026-01-01T00:00:00"

    def test_approved_tables_are_marked_from_the_allowlist(self):
        registry, _ = run({}, baseline())
        assert registry["tables"]["well.task_daily"]["status"] == ledger.STATUS_APPROVED
        assert registry["tables"]["dbo.lookup_colour"]["status"] == ledger.STATUS_OBSERVED


class TestNewTables:
    def test_a_new_version_of_an_approved_table_is_a_candidate_successor(self):
        registry, _ = run({}, baseline())
        catalog = baseline()
        new = meta("well.task_daily_v2", {**TASK_COLUMNS, "crew_ref": "int(4) NULL"})
        catalog[new.key()] = new

        registry, events = run(registry, catalog, now="2026-09-02T00:00:00+00:00")
        entry = registry["tables"]["well.task_daily_v2"]
        assert entry["status"] == ledger.STATUS_CANDIDATE
        assert entry["kind"] == ledger.KIND_SUCCESSOR
        assert entry["related_to"] == ["well.task_daily"]
        (added,) = events
        assert added["type"] == ledger.EVENT_TABLE_ADDED
        assert added["status"] == ledger.STATUS_CANDIDATE
        assert "well.task_daily" in added["note"]

    def test_a_differently_named_table_with_the_same_columns_is_a_candidate(self):
        registry, _ = run({}, baseline())
        catalog = baseline()
        new = meta("ops.daily_entries", TASK_COLUMNS)
        catalog[new.key()] = new
        registry, _ = run(registry, catalog)
        assert registry["tables"]["ops.daily_entries"]["kind"] == ledger.KIND_SUCCESSOR

    def test_a_table_referencing_an_approved_one_is_a_candidate_extension(self):
        registry, _ = run({}, baseline())
        catalog = baseline()
        new = meta(
            "well.well_permit",
            {"permit_id": "int(4) NOT NULL", "well_id": "int(4) NULL", "issued": "date(3) NULL"},
            references=["well.well_master"],
        )
        catalog[new.key()] = new
        registry, _ = run(registry, catalog)
        entry = registry["tables"]["well.well_permit"]
        assert entry["status"] == ledger.STATUS_CANDIDATE
        assert entry["kind"] == ledger.KIND_EXTENSION
        assert entry["related_to"] == ["well.well_master"]

    def test_noise_is_ignored(self):
        registry, _ = run({}, baseline())
        catalog = baseline()
        for name in ("dbo.tmp_import_0915", "well.task_daily_bak", "dbo.full_backup_2026"):
            catalog[name] = meta(name, TASK_COLUMNS)
        registry, events = run(registry, catalog)
        for name in ("dbo.tmp_import_0915", "well.task_daily_bak", "dbo.full_backup_2026"):
            assert registry["tables"][name]["status"] == ledger.STATUS_IGNORED
        assert all(event["status"] == ledger.STATUS_IGNORED for event in events)

    def test_an_unrelated_table_is_only_observed(self):
        registry, _ = run({}, baseline())
        catalog = baseline()
        catalog["hr.leave_request"] = meta("hr.leave_request", {"id": "int(4) NOT NULL"})
        registry, _ = run(registry, catalog)
        entry = registry["tables"]["hr.leave_request"]
        assert (entry["status"], entry["kind"]) == (ledger.STATUS_OBSERVED, ledger.KIND_UNRELATED)

    def test_tables_of_one_family_are_linked(self):
        registry, _ = run({}, baseline())
        catalog = baseline()
        catalog["ops.readings_2025"] = meta("ops.readings_2025", {"id": "int(4) NOT NULL"})
        catalog["ops.readings_2026"] = meta("ops.readings_2026", {"id": "int(4) NOT NULL"})
        registry, _ = run(registry, catalog)
        entry = registry["tables"]["ops.readings_2026"]
        assert entry["kind"] == ledger.KIND_FAMILY
        assert entry["related_to"] == ["ops.readings_2025"]


class TestChangesToExistingTables:
    def test_column_changes_are_recorded_one_by_one(self):
        registry, _ = run({}, baseline())
        catalog = baseline()
        columns = dict(TASK_COLUMNS)
        columns.pop("planned")
        columns["well_id"] = "int(4) NULL"
        columns["crew_ref"] = "int(4) NULL"
        catalog["well.task_daily"] = meta(
            "well.task_daily", columns, modified="2026-09-02T00:00:00", references=["well.well_master"]
        )
        _, events = run(registry, catalog)
        by_type = {event["type"]: event for event in events}
        assert by_type[ledger.EVENT_COLUMN_ADDED]["column"] == "crew_ref"
        assert by_type[ledger.EVENT_COLUMN_REMOVED]["column"] == "planned"
        changed = by_type[ledger.EVENT_COLUMN_CHANGED]
        assert (changed["column"], changed["before"], changed["after"]) == (
            "well_id", "varchar(20) NULL", "int(4) NULL",
        )

    def test_nothing_changed_records_nothing(self):
        registry, _ = run({}, baseline())
        _, events = run(registry, baseline())
        assert events == []

    def test_a_removed_approved_table_names_its_likely_successor(self):
        registry, _ = run({}, baseline())
        catalog = baseline()
        catalog["well.task_daily_v2"] = meta("well.task_daily_v2", TASK_COLUMNS)
        registry, _ = run(registry, catalog)

        catalog.pop("well.task_daily")
        registry, events = run(registry, catalog)
        (removed,) = [e for e in events if e["type"] == ledger.EVENT_TABLE_REMOVED]
        assert removed["was"] == ledger.STATUS_APPROVED
        assert "well.task_daily_v2" in removed["note"]
        assert registry["tables"]["well.task_daily"]["status"] == ledger.STATUS_REMOVED

    def test_a_removed_table_that_returns_is_recorded_as_returning(self):
        registry, _ = run({}, baseline())
        catalog = baseline()
        catalog.pop("dbo.lookup_colour")
        registry, _ = run(registry, catalog)
        registry, events = run(registry, baseline())
        assert types(events) == [ledger.EVENT_TABLE_REAPPEARED]
        assert registry["tables"]["dbo.lookup_colour"]["status"] == ledger.STATUS_OBSERVED

    def test_approving_a_table_in_the_env_is_recorded(self):
        registry, _ = run({}, baseline())
        _, events = run(registry, baseline(), approved=APPROVED + ["dbo.lookup_colour"])
        assert types(events) == [ledger.EVENT_TABLE_APPROVED]

    def test_columns_are_kept_when_a_pass_did_not_need_to_read_them(self):
        registry, _ = run({}, baseline())
        catalog = baseline()
        for table in catalog.values():
            table.columns = None
            table.references = None
        registry, events = run(registry, catalog)
        assert events == []
        assert registry["tables"]["well.task_daily"]["columns"] == TASK_COLUMNS


class TestHelpers:
    @pytest.mark.parametrize(
        "name, family",
        [
            ("well.task_daily_v2", "task_daily"),
            ("ops.readings_2026", "readings"),
            ("ops.readings_2026_09", "readings"),
            ("dbo.activity_new", "activity"),
            ("dbo.activity", "activity"),
        ],
    )
    def test_family_name(self, name, family):
        assert ledger.family_name(name) == family

    def test_few_shared_columns_never_make_tables_alike(self):
        ratio, shared = ledger.column_overlap(["id", "name"], ["id", "name"])
        assert ratio == 1.0 and shared == 2  # below _MIN_SHARED_COLUMNS


# ---------------------------------------------------------------------------
# read_catalog: only SELECTs on catalogue views, and the modify_date shortcut
# ---------------------------------------------------------------------------
class FakeCursor:
    def __init__(self, tables, columns, foreign_keys):
        self.statements: List[str] = []
        self._results = {"sys.tables t": tables, "sys.columns c": columns, "sys.foreign_keys fk": foreign_keys}
        self._last: list = []

    def execute(self, sql, *params):
        self.statements.append(sql)
        for marker, rows in self._results.items():
            if f"FROM {marker}" in sql:
                self._last = rows
                return
        raise AssertionError(f"unexpected statement: {sql}")

    def fetchall(self):
        return list(self._last)


def fake_connection(monkeypatch, cursor):
    @contextmanager
    def _connection():
        class _Conn:
            def cursor(self_inner):
                return cursor

        yield _Conn()

    import dynamic_db.db as db_module

    monkeypatch.setattr(db_module, "get_connection", _connection)


CREATED = datetime(2026, 1, 1)
TABLE_ROWS = [("well", "task_daily", CREATED, CREATED), ("well", "well_master", CREATED, CREATED)]
COLUMN_ROWS = [
    ("well", "task_daily", "task_code", "nvarchar", 200, True),
    ("well", "task_daily", "user_password", "nvarchar", 200, True),  # secret: never kept
    ("well", "well_master", "well_id", "int", 4, False),
]
FK_ROWS = [("well", "task_daily", "well", "well_master")]


class TestReadCatalog:
    def test_every_statement_is_a_read_only_catalogue_select(self, monkeypatch):
        from dynamic_db.db import assert_read_only

        cursor = FakeCursor(TABLE_ROWS, COLUMN_ROWS, FK_ROWS)
        fake_connection(monkeypatch, cursor)
        catalog = ledger.read_catalog(["well"])
        assert len(cursor.statements) == 3
        for sql in cursor.statements:
            assert_read_only(sql)  # raises for anything but a single SELECT
            assert "sys." in sql
        task = catalog["well.task_daily"]
        assert task.columns == {"task_code": "nvarchar(200) NULL"}
        assert task.references == ["well.well_master"]

    def test_unchanged_dates_skip_the_column_and_key_catalogues(self, monkeypatch):
        cursor = FakeCursor(TABLE_ROWS, COLUMN_ROWS, FK_ROWS)
        fake_connection(monkeypatch, cursor)
        first = ledger.read_catalog(["well"])
        registry, _ = run({}, first, approved=APPROVED)

        cursor.statements.clear()
        second = ledger.read_catalog(["well"], known=registry["tables"])
        assert len(cursor.statements) == 1  # sys.tables only
        assert second["well.task_daily"].columns is None

    def test_an_altered_table_triggers_a_full_read(self, monkeypatch):
        cursor = FakeCursor(TABLE_ROWS, COLUMN_ROWS, FK_ROWS)
        fake_connection(monkeypatch, cursor)
        registry, _ = run({}, ledger.read_catalog(["well"]), approved=APPROVED)

        cursor._results["sys.tables t"] = [
            ("well", "task_daily", CREATED, datetime(2026, 9, 2)),
            ("well", "well_master", CREATED, CREATED),
        ]
        cursor.statements.clear()
        ledger.read_catalog(["well"], known=registry["tables"])
        assert len(cursor.statements) == 3

    def test_star_watches_every_schema(self, monkeypatch):
        cursor = FakeCursor(TABLE_ROWS, COLUMN_ROWS, FK_ROWS)
        fake_connection(monkeypatch, cursor)
        ledger.read_catalog(["*"])
        assert all("WHERE" not in sql for sql in cursor.statements)


# ---------------------------------------------------------------------------
# observe: persistence is local and the change log only ever grows
# ---------------------------------------------------------------------------
class TestObserve:
    def test_history_accumulates_across_passes(self, monkeypatch):
        from dynamic_db.config import get_settings

        monkeypatch.setattr(get_settings(), "included_tables", list(APPROVED))
        catalogs = [baseline(), {**baseline(), "well.task_daily_v2": meta("well.task_daily_v2", TASK_COLUMNS)}]
        monkeypatch.setattr(ledger, "read_catalog", lambda schemas, known=None, full=False: catalogs.pop(0))

        first = ledger.observe()
        second = ledger.observe()

        assert types(first["events"]) == [ledger.EVENT_TRACKING_STARTED]
        assert types(second["events"]) == [ledger.EVENT_TABLE_ADDED]
        assert [c["table"] for c in second["candidates"]] == ["well.task_daily_v2"]
        history = ledger.load_events()
        assert types(history) == [ledger.EVENT_TRACKING_STARTED, ledger.EVENT_TABLE_ADDED]
        assert ledger.load_events(table="task_daily_v2")[0]["type"] == ledger.EVENT_TABLE_ADDED
        assert ledger.load_registry()["tables"]["well.task_daily_v2"]["status"] == ledger.STATUS_CANDIDATE

    def test_a_database_failure_leaves_the_ledger_untouched(self, monkeypatch):
        from dynamic_db.db import DatabaseUnavailable

        def _down(*args, **kwargs):
            raise DatabaseUnavailable("down")

        monkeypatch.setattr(ledger, "read_catalog", _down)
        with pytest.raises(DatabaseUnavailable):
            ledger.observe()
        assert ledger.load_registry() == {}
        assert ledger.load_events() == []

    def test_the_watcher_can_be_turned_off(self):
        assert ledger.start_watcher(interval_minutes=0) is False


def test_watched_schemas_default_to_the_allowlists_reach(monkeypatch):
    from dynamic_db.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "included_tables", ["well.task_daily", "dbo.mapping_master"])
    monkeypatch.setattr(settings, "discovery_schemas", [])
    monkeypatch.setattr(settings, "watch_schemas", [])
    assert settings.watched_schemas == ["well", "dbo"]
    monkeypatch.setattr(settings, "watch_schemas", ["*"])
    assert settings.watched_schemas == ["*"]
