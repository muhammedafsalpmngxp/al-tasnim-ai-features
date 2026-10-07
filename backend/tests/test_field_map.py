"""The Oman Petroleum Field Map: classification, counts, status and the API.

Offline. The repository is stubbed with rows shaped exactly like
``sql/field_map.sql`` returns them; one test checks that SQL text itself for
the rules it must encode (authoritative well id, status from
eng_completion_date, one snapshot per well).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import dependencies
from app.config.settings import BACKEND_DIR
from app.services import field_map_service as fms
from app.services.field_map_service import FieldMapService, UnknownField, load_config
from app.utils.sql_loader import load_sql

CONFIG = {
    "fields": {
        "NIMR": {"latitude": 18.55, "longitude": 55.65, "label": "Nimr"},
        "MARMUL": {"latitude": 18.15, "longitude": 55.20, "label": "Marmul"},
        "OUTSIDE": {"latitude": 40.0, "longitude": 10.0, "label": "Not in Oman"},
        "BROKEN": {"latitude": "north", "longitude": 55.0},
    },
    "parent_fields": {"MARMUL AK": "MARMUL"},
    "non_geographical_fields": {"RIG MOVE": "rig operation", "RIG MAINTENANCE": "rig operation"},
    "non_geographical_patterns": ["*STANDBY*"],
}


def row(well_id, status, field):
    return {
        "well_id": well_id,
        "status": status,
        "field": field,
        "field_key": field.upper().strip() if field else None,
    }


ROWS = [
    row(101, "INCOMPLETE", "NIMR"),
    row(102, "COMPLETED", "NIMR"),
    row(103, "INCOMPLETE", "Nimr "),          # same field, different spelling
    row(101, "INCOMPLETE", "NIMR"),           # a repeated well: counted once
    row(201, "INCOMPLETE", "MARMUL AK"),      # sub-area -> parent position
    row(301, "INCOMPLETE", "RIG MOVE"),
    row(302, "COMPLETED", "RIG MAINTENANCE"),
    row(303, "INCOMPLETE", "RIG STANDBY"),     # caught by a pattern
    row(401, "INCOMPLETE", "EASTERN FLANK"),   # geographical, no coordinate
    row(402, "COMPLETED", "OUTSIDE"),          # its coordinate is refused
    row(501, "INCOMPLETE", None),              # no field at all
]


class StubRepository:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def fetch_well_fields(self):
        self.calls += 1
        return list(self.rows)


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "coords.json"
    path.write_text(json.dumps(CONFIG), encoding="utf-8")
    return load_config(path)


@pytest.fixture
def service(config):
    return FieldMapService(repository=StubRepository(ROWS), config=config)


def by_key(items):
    return {item["field_key"]: item for item in items}


class TestConfiguration:
    def test_coordinates_outside_oman_or_unreadable_are_refused(self, config):
        assert "OUTSIDE" in config.rejected and "BROKEN" in config.rejected
        assert "OUTSIDE" not in config.coordinates

    def test_every_coordinate_is_marked_approximate(self, config):
        assert {c["location_type"] for c in config.coordinates.values()} == {"approximate"}

    def test_the_shipped_coordinate_file_is_valid_and_inside_oman(self):
        config = load_config(BACKEND_DIR / "app" / "config" / "oman_field_coordinates.json")
        assert config.coordinates, "the shipped file maps no field"
        assert config.rejected == {}
        assert {"RIG MOVE", "RIG MAINTENANCE"} <= set(config.non_geographical)
        for key, coordinate in config.coordinates.items():
            assert coordinate.get("source"), f"{key} has no source"
        for parent in config.parents.values():
            assert parent in config.coordinates, f"parent field {parent} has no coordinate"


class TestSummary:
    def test_non_geographical_values_never_become_fields(self, service):
        summary = service.summary()
        keys = set(by_key(summary["fields"])) | set(by_key(summary["unmapped_fields"]))
        assert not keys & {"RIG MOVE", "RIG MAINTENANCE", "RIG STANDBY"}
        assert summary["coverage"]["non_geographical_value_count"] == 3
        assert summary["coverage"]["wells_in_non_geographical_values"] == 3

    def test_duplicate_rows_and_spellings_do_not_inflate_counts(self, service):
        nimr = by_key(service.summary()["fields"])["NIMR"]
        assert (nimr["live_well_count"], nimr["completed_well_count"], nimr["total_well_count"]) == (2, 1, 3)

    def test_a_sub_area_uses_its_parent_fields_position(self, service):
        ak = by_key(service.summary()["fields"])["MARMUL AK"]
        assert (ak["latitude"], ak["longitude"]) == (18.15, 55.2)
        assert ak["location_basis"] == "parent_field" and ak["parent_field"] == "MARMUL"

    def test_a_field_without_a_valid_coordinate_is_listed_never_plotted(self, service):
        summary = service.summary()
        plotted = by_key(summary["fields"])
        unmapped = by_key(summary["unmapped_fields"])
        for key in ("EASTERN FLANK", "OUTSIDE"):
            assert key not in plotted
            assert key in unmapped
            assert "latitude" not in unmapped[key]

    def test_every_plotted_field_is_approximate(self, service):
        assert {f["location_type"] for f in service.summary()["fields"]} == {"approximate"}

    def test_coverage_counts_every_well_once(self, service):
        coverage = service.summary()["coverage"]
        assert coverage["total_wells"] == 10          # 11 rows, well 101 twice
        assert coverage["wells_with_field"] == 9
        assert coverage["wells_on_map"] == 4          # NIMR 3 + MARMUL AK 1
        assert coverage["wells_in_unmapped_fields"] == 2

    def test_counts_match_the_underlying_rows(self, service):
        """Each field's counts equal a direct count over the distinct wells."""
        distinct = {r["well_id"]: r for r in ROWS}
        for field in service.summary()["fields"]:
            wells = [r for r in distinct.values()
                     if r["field_key"] and fms.normalise(r["field_key"]) == field["field_key"]]
            assert field["total_well_count"] == len(wells)
            assert field["live_well_count"] == sum(r["status"] == "INCOMPLETE" for r in wells)

    def test_the_summary_is_cached_and_refresh_rereads(self, config):
        repository = StubRepository(ROWS)
        service = FieldMapService(repository=repository, config=config)
        service.summary()
        service.summary()
        service.wells("NIMR")
        assert repository.calls == 1
        service.summary(refresh=True)
        assert repository.calls == 2


class TestFieldWells:
    def test_status_comes_from_sql_completion_date(self, service):
        wells = {w["well_id"]: w for w in service.wells("NIMR")["wells"]}
        assert wells[101]["status"] == "Incomplete" and wells[101]["status_code"] == "INCOMPLETE"
        assert wells[102]["status"] == "Completed" and wells[102]["status_code"] == "COMPLETED"

    def test_each_well_appears_once(self, service):
        ids = [w["well_id"] for w in service.wells("nimr")["wells"]]
        assert sorted(ids) == [101, 102, 103]
        assert len(ids) == len(set(ids))

    def test_no_well_is_given_a_coordinate(self, service):
        assert all(w["latitude"] is None and w["longitude"] is None for w in service.wells("NIMR")["wells"])

    def test_an_unmapped_field_still_lists_its_wells(self, service):
        result = service.wells("EASTERN FLANK")
        assert result["mapped"] is False and result["latitude"] is None
        assert result["location_note"] == "Field location unavailable."
        assert [w["well_id"] for w in result["wells"]] == [401]

    def test_a_non_geographical_value_is_refused(self, service):
        with pytest.raises(UnknownField):
            service.wells("RIG MOVE")


class TestSql:
    SQL = load_sql("field_map")

    def test_well_master_is_the_authoritative_well(self):
        assert "FROM well.well_master wm" in self.SQL
        assert "SELECT\n    wm.well_id" in self.SQL
        assert "LEFT JOIN latest_field" in self.SQL

    def test_status_comes_from_eng_completion_date_only(self):
        assert (
            "CASE WHEN wm.eng_completion_date IS NULL THEN 'INCOMPLETE' ELSE 'COMPLETED' END"
            in self.SQL
        )

    def test_one_snapshot_per_well(self):
        assert "PARTITION BY TRY_CONVERT(int, ds.well_id)" in self.SQL
        assert "lf.rn = 1" in self.SQL

    def test_it_is_read_only(self):
        from app.config.database import assert_read_only

        assert_read_only(self.SQL)


class TestApi:
    @pytest.fixture
    def client(self, service, monkeypatch):
        from app.main import app

        monkeypatch.setattr(dependencies, "_field_map_service", service)
        return TestClient(app)

    def test_the_field_map_endpoint(self, client):
        body = client.get("/api/field-map").json()
        assert {f["field_key"] for f in body["fields"]} == {"NIMR", "MARMUL AK"}
        assert "approximate" in body["location_note"]
        for f in body["fields"]:
            for key in ("latitude", "longitude", "live_well_count", "completed_well_count", "total_well_count"):
                assert key in f

    def test_the_field_wells_endpoint(self, client):
        body = client.get("/api/field-map/NIMR/wells").json()
        assert body["field_key"] == "NIMR" and len(body["wells"]) == 3

    def test_a_field_name_with_a_space(self, client):
        assert client.get("/api/field-map/MARMUL%20AK/wells").status_code == 200

    def test_non_geographical_and_unknown_fields_are_404(self, client):
        assert client.get("/api/field-map/RIG%20MOVE/wells").status_code == 404
        assert client.get("/api/field-map/NOWHERE/wells").status_code == 404
