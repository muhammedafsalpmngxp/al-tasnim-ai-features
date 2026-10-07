"""The Oman field map's well explorer: filters, lookups and basemaps.

Offline, like test_field_map.py: the repository is stubbed with rows shaped
exactly as ``sql/field_map.sql`` returns them. The last class runs the real
query and skips itself when SQL Server is not reachable.
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from app.api import dependencies
from app.config.settings import Settings
from app.services import field_map_basemaps as fmb
from app.services.field_map_basemaps import basemap_config
from app.services.field_map_service import FieldMapService, InvalidFilter, load_config
from app.utils.sql_loader import load_sql

from tests.conftest import requires_database

CONFIG = {
    "fields": {"NIMR": {"latitude": 18.55, "longitude": 55.65, "label": "Nimr", "source": "test"}},
    "non_geographical_fields": {"RIG MOVE": "rig operation", "RIG MAINTENANCE": "rig operation"},
}


def row(well_id, status, field, *, cat=None, cat_text=None, fn=None, fn_text=None,
        comp=None, comp_text=None, rig=None, rig_no=None, location=None):
    return {
        "well_id": well_id,
        "status": status,
        "field": field,
        "field_key": field.upper().strip() if field else None,
        "well_category_id": cat,
        "well_category": cat_text,
        "well_function_id": fn,
        "well_function": fn_text,
        "well_completion_type_id": comp,
        "completion_type": comp_text,
        "rig_id": rig,
        "rig_no": rig_no,
        "well_location_id": 1 if location else None,
        "well_location": location,
    }


DEV = dict(cat=1, cat_text="Development")
OIL = dict(fn=5, fn_text="Oil Producer")
WI = dict(fn=6, fn_text="Water Injector")
ROWS = [
    row(1, "INCOMPLETE", "NIMR", **DEV, **OIL, comp=7, comp_text="PI", rig=10, rig_no="RIG-A", location="Pad 12"),
    row(2, "INCOMPLETE", "NIMR", **DEV, **OIL, comp=2, comp_text="ESP", rig=11, rig_no="RIG-B"),
    row(3, "INCOMPLETE", "NIMR", **DEV, **WI, rig=10, rig_no="RIG-A"),
    row(4, "INCOMPLETE", "NIMR", cat=3, cat_text="Appraisal", **OIL),              # no rig, no completion
    row(5, "INCOMPLETE", "NIMR"),                                                  # nothing recorded at all
    row(6, "COMPLETED", "NIMR", **DEV, **OIL, comp=7, comp_text="PI", rig=10, rig_no="RIG-A"),
    row(7, "INCOMPLETE", "NIMR", cat=9, cat_text=None, fn=99, fn_text=None),       # ids with no description
    row(1, "INCOMPLETE", "NIMR", **DEV, **OIL, comp=7, comp_text="PI", rig=10, rig_no="RIG-A"),  # repeated
    row(8, "INCOMPLETE", "RIG MOVE", **DEV, **OIL, rig=10, rig_no="RIG-A"),
]


class StubRepository:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def fetch_well_fields(self, *args, **kwargs):
        # Recorded so a test can prove no request value ever reaches the query.
        self.calls.append((args, kwargs))
        return list(self.rows)


@pytest.fixture
def repository():
    return StubRepository(ROWS)


@pytest.fixture
def service(tmp_path, repository):
    path = tmp_path / "coords.json"
    path.write_text(json.dumps(CONFIG), encoding="utf-8")
    return FieldMapService(repository=repository, config=load_config(path))


def ids(result):
    return [w["well_id"] for w in result["wells"]]


def options(result, name):
    return {o["value"]: (o["label"], o["count"]) for o in result["filters"][name]}


class TestStatus:
    def test_live_uses_the_completion_date_status(self, service):
        result = service.wells("NIMR", status="live")
        assert ids(result) == [1, 2, 3, 4, 5, 7]
        assert {w["status_code"] for w in result["wells"]} == {"INCOMPLETE"}

    def test_completed(self, service):
        result = service.wells("NIMR", status="completed")
        assert ids(result) == [6]
        assert result["wells"][0]["status"] == "Completed"

    def test_all_keeps_both_and_lists_live_first(self, service):
        assert ids(service.wells("NIMR", status="all")) == [1, 2, 3, 4, 5, 7, 6]

    def test_field_counts_are_the_whole_field_whatever_the_filter(self, service):
        result = service.wells("NIMR", status="completed", filters={"rig": "10"})
        assert (result["live_well_count"], result["completed_well_count"], result["total_well_count"]) == (6, 1, 7)
        assert result["matching_well_count"] == 1

    def test_an_unknown_status_is_refused(self, service):
        with pytest.raises(InvalidFilter):
            service.wells("NIMR", status="producing")


class TestFilters:
    def test_each_filter_alone(self, service):
        assert ids(service.wells("NIMR", status="live", filters={"category": "3"})) == [4]
        assert ids(service.wells("NIMR", status="live", filters={"function": "6"})) == [3]
        assert ids(service.wells("NIMR", status="live", filters={"completion_type": "2"})) == [2]
        assert ids(service.wells("NIMR", status="live", filters={"rig": "10"})) == [1, 3]

    def test_filters_combine(self, service):
        result = service.wells("NIMR", status="live", filters={"category": "1", "function": "5", "rig": "10"})
        assert ids(result) == [1]
        assert result["applied_filters"] == {"category": "1", "function": "5", "rig": "10"}

    def test_none_selects_wells_with_nothing_recorded(self, service):
        assert ids(service.wells("NIMR", status="live", filters={"rig": "none"})) == [4, 5, 7]
        assert ids(service.wells("NIMR", status="live", filters={"completion_type": "none"})) == [3, 4, 5, 7]
        assert ids(service.wells("NIMR", status="live", filters={"category": "none"})) == [5]

    def test_all_or_blank_is_no_filter(self, service):
        everything = ids(service.wells("NIMR", status="live"))
        assert ids(service.wells("NIMR", status="live", filters={"rig": "all", "category": ""})) == everything

    @pytest.mark.parametrize("value", ["abc", "1 OR 1=1", "1;DROP TABLE x", "-1", "1.5"])
    def test_a_value_that_is_not_an_id_is_refused(self, service, value):
        with pytest.raises(InvalidFilter):
            service.wells("NIMR", filters={"category": value})

    def test_repeated_rows_are_one_well(self, service):
        listed = ids(service.wells("NIMR"))
        assert listed.count(1) == 1 and len(listed) == len(set(listed))


class TestOptionsAndLabels:
    def test_options_carry_counts_and_the_stored_descriptions(self, service):
        result = service.wells("NIMR", status="live")
        assert options(result, "function") == {
            "5": ("Oil Producer", 3),
            "6": ("Water Injector", 1),
            "99": ("Function 99 (no description)", 1),
            "none": ("Not recorded", 1),
        }
        assert options(result, "rig") == {"10": ("RIG-A", 2), "11": ("RIG-B", 1), "none": ("Unassigned", 3)}
        assert sum(count for _, count in options(result, "category").values()) == result["matching_well_count"]

    def test_options_count_within_the_other_filters(self, service):
        result = service.wells("NIMR", status="live", filters={"rig": "10"})
        # Category counts over RIG-A's live wells; rig counts ignore the rig filter itself.
        assert options(result, "category") == {"1": ("Development", 2)}
        assert options(result, "rig")["11"] == ("RIG-B", 1)

    def test_the_chosen_value_is_offered_even_when_nothing_matches(self, service):
        result = service.wells("NIMR", status="completed", filters={"function": "6"})
        assert result["wells"] == []
        assert options(result, "function")["6"] == ("Water Injector", 0)

    def test_the_none_option_is_listed_last(self, service):
        values = [o["value"] for o in service.wells("NIMR", status="live")["filters"]["rig"]]
        assert values[-1] == "none"

    def test_well_rows_carry_every_attribute_and_nulls_stay_null(self, service):
        wells = {w["well_id"]: w for w in service.wells("NIMR")["wells"]}
        assert (wells[1]["category"], wells[1]["function"], wells[1]["completion_type"], wells[1]["rig"]) == (
            "Development", "Oil Producer", "PI", "RIG-A")
        assert wells[1]["well_location"] == "Pad 12"
        assert all(wells[5][k] is None for k in ("category", "function", "completion_type", "rig", "well_location"))
        assert wells[7]["category"] == "Category 9 (no description)" and wells[7]["category_id"] == 9
        assert all(w["latitude"] is None and w["longitude"] is None for w in wells.values())

    def test_an_operational_label_is_never_a_field_to_filter(self, service):
        from app.services.field_map_service import UnknownField

        with pytest.raises(UnknownField):
            service.wells("RIG MOVE", status="live")
        # And its well never leaks into a real field's list.
        assert 8 not in ids(service.wells("NIMR"))


class TestSqlSafety:
    SQL = load_sql("field_map")

    def test_the_query_takes_no_parameters(self):
        body = re.sub(r"/\*.*?\*/", " ", self.SQL, flags=re.DOTALL)
        assert "?" not in body and "{" not in body and "%s" not in body

    def test_no_request_value_reaches_the_repository(self, service, repository):
        hostile = "NIMR'; DROP TABLE well.well_master; --"
        from app.services.field_map_service import UnknownField

        with pytest.raises(UnknownField):
            service.wells(hostile, filters={"rig": "10"})
        service.wells("NIMR", status="live", filters={"category": "1", "rig": "none"})
        assert repository.calls and all(call == ((), {}) for call in repository.calls)

    def test_lookups_follow_the_declared_foreign_keys(self):
        sql = self.SQL
        assert "ds.well_category_id" in sql and "ON wc.well_category_id = lf.well_category_id" in sql
        assert "ds.well_function_id" in sql and "ON wf.well_function_id = lf.well_function_id" in sql
        assert "FROM well.well_progress wp" in sql
        assert "ON wct.well_completion_type_id = lc.well_completion_type_id" in sql
        assert "ON r.rig_id = wm.rig_id" in sql
        assert "ON wl.well_location_id = wm.well_location_id" in sql
        # well_type_id is a different attribute and never stands in for a category.
        assert "well_type" not in re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)

    def test_completion_type_is_one_row_per_well_the_latest_that_names_one(self):
        assert "PARTITION BY wp.well_id" in self.SQL
        assert "ORDER BY wp.week_number DESC, wp.progress_id DESC" in self.SQL
        assert "wp.well_completion_type_id IS NOT NULL" in self.SQL
        assert "lc.rn = 1" in self.SQL

    def test_the_field_snapshot_is_the_latest(self):
        assert "ORDER BY ds.week_number DESC, ds.load_timestamp DESC, ds.id DESC" in self.SQL

    def test_every_lookup_is_optional(self):
        body = re.sub(r"/\*.*?\*/", " ", self.SQL, flags=re.DOTALL)
        assert re.search(r"(?<!LEFT )JOIN", body) is None, "every join must be a LEFT JOIN"


class TestApi:
    @pytest.fixture
    def client(self, service, monkeypatch):
        from app.main import app

        monkeypatch.setattr(dependencies, "_field_map_service", service)
        return TestClient(app)

    def test_filters_arrive_as_query_parameters(self, client):
        body = client.get("/api/field-map/NIMR/wells", params={"status": "live", "rig": "10", "function": "5"}).json()
        assert [w["well_id"] for w in body["wells"]] == [1]
        assert body["status_filter"] == "live"
        assert set(body["filters"]) == {"category", "function", "completion_type", "rig"}

    def test_a_bad_filter_is_a_400_not_a_500(self, client):
        response = client.get("/api/field-map/NIMR/wells", params={"rig": "RIG-A"})
        assert response.status_code == 400
        assert "rig" in response.json()["detail"]
        assert client.get("/api/field-map/NIMR/wells", params={"status": "soon"}).status_code == 400

    def test_without_parameters_the_original_contract_holds(self, client):
        body = client.get("/api/field-map/NIMR/wells").json()
        assert body["status_filter"] == "all" and len(body["wells"]) == 7

    def test_the_summary_carries_the_basemaps(self, client):
        basemaps = client.get("/api/field-map").json()["basemaps"]
        assert basemaps["layers"]["standard"]["url"]
        assert basemaps["default"] in ("satellite", "standard")


# ---------------------------------------------------------------------------
# Basemaps
# ---------------------------------------------------------------------------
def settings(monkeypatch, **env):
    for name in ("MAP_SATELLITE_PROVIDER", "MAP_ESRI_API_KEY", "MAP_LABELS_LANGUAGE", "MAP_DEFAULT_BASEMAP",
                 "MAP_CUSTOM_SATELLITE_URL", "MAP_CUSTOM_SATELLITE_ATTRIBUTION", "MAP_CUSTOM_LABELS_URL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings()


class TestBasemaps:
    def test_default_is_esri_imagery_with_labels_and_needs_no_key(self, monkeypatch):
        config = basemap_config(settings(monkeypatch))
        sat = config["layers"]["satellite"]
        assert config["satellite_status"] == "esri_public" and config["default"] == "satellite"
        assert sat["url"] == fmb.ESRI_IMAGERY_URL and "{z}/{y}/{x}" in sat["url"]
        assert "Esri" in sat["attribution"] and "Vantor" in sat["attribution"]
        assert sat["max_native_zoom"] == 18 and sat["max_zoom"] > sat["max_native_zoom"]
        assert config["labels"]["url"] == fmb.ESRI_LABELS_URL
        # English on every label, with Arabic beside it at closer zooms.
        assert config["labels_language"] == "en+ar"
        assert config["layers"]["standard"]["url"] == fmb.ESRI_STREETS_URL
        assert "licence" in config["license_note"].lower() or "license" in config["license_note"].lower()

    def test_a_key_switches_to_the_licensed_services_with_english_only_labels(self, monkeypatch):
        config = basemap_config(settings(monkeypatch, MAP_ESRI_API_KEY="AAPK/secret+key"))
        assert config["satellite_status"] == "esri_api_key"
        assert config["layers"]["satellite"]["url"].startswith(fmb.ESRI_KEYED_IMAGERY_URL + "?token=AAPK%2Fsecret%2Bkey")
        labels = config["labels"]
        assert "arcgis/imagery/labels" in labels["url"] and "language=en" in labels["url"]
        assert config["labels_language"] == "en"
        assert labels["tile_size"] == 512 and labels["zoom_offset"] == -1
        assert "language=en" in config["layers"]["standard"]["url"]
        assert "Powered by" in config["layers"]["satellite"]["attribution"]

    def test_the_key_never_appears_in_the_health_dump(self, monkeypatch):
        dumped = json.dumps(settings(monkeypatch, MAP_ESRI_API_KEY="AAPK-do-not-leak").safe_dump())
        assert "AAPK-do-not-leak" not in dumped

    def test_none_falls_back_to_the_standard_map_and_says_why(self, monkeypatch):
        config = basemap_config(settings(monkeypatch, MAP_SATELLITE_PROVIDER="none"))
        assert config["layers"]["satellite"] is None and config["labels"] is None
        assert config["default"] == "standard" and config["satellite_status"] == "disabled"
        assert "turned off" in config["notice"]
        assert config["layers"]["standard"]["url"]

    def test_custom_without_a_url_is_reported_not_broken(self, monkeypatch):
        config = basemap_config(settings(monkeypatch, MAP_SATELLITE_PROVIDER="custom"))
        assert config["layers"]["satellite"] is None and config["default"] == "standard"
        assert "MAP_CUSTOM_SATELLITE_URL" in config["notice"]

    def test_custom_imagery_and_its_own_labels(self, monkeypatch):
        config = basemap_config(settings(
            monkeypatch,
            MAP_SATELLITE_PROVIDER="custom",
            MAP_CUSTOM_SATELLITE_URL="https://tiles.example/{z}/{x}/{y}.jpg",
            MAP_CUSTOM_SATELLITE_ATTRIBUTION="Example Imagery",
            MAP_CUSTOM_LABELS_URL="https://labels.example/{z}/{x}/{y}.png",
        ))
        assert config["layers"]["satellite"]["url"] == "https://tiles.example/{z}/{x}/{y}.jpg"
        assert config["labels"]["attribution"] == "Example Imagery"
        # Its label language is unknown, so it is not claimed.
        assert config["labels_language"] is None

    def test_an_unknown_provider_is_the_standard_map(self, monkeypatch):
        config = basemap_config(settings(monkeypatch, MAP_SATELLITE_PROVIDER="googel"))
        assert config["layers"]["satellite"] is None and config["notice"]

    def test_the_default_basemap_can_be_standard(self, monkeypatch):
        assert basemap_config(settings(monkeypatch, MAP_DEFAULT_BASEMAP="standard"))["default"] == "standard"


# ---------------------------------------------------------------------------
# The real query, when the database is reachable
# ---------------------------------------------------------------------------
@requires_database
@pytest.mark.database
class TestOnRealData:
    @pytest.fixture(scope="class")
    def rows(self):
        from app.repositories.field_map_repository import FieldMapRepository

        return FieldMapRepository().fetch_well_fields()

    def test_one_row_per_well_master_well(self, rows):
        from app.config.database import fetch_all

        ids_ = [r["well_id"] for r in rows]
        assert len(ids_) == len(set(ids_))
        total = fetch_all("SELECT COUNT(DISTINCT well_id) AS n FROM well.well_master", label="test")[0]["n"]
        assert len(ids_) == total

    def test_live_counts_match_well_master(self, rows):
        from app.config.database import fetch_all

        live = fetch_all(
            "SELECT COUNT(*) AS n, COUNT(DISTINCT rig_id) AS rigs, "
            "SUM(CASE WHEN rig_id IS NULL THEN 1 ELSE 0 END) AS unassigned "
            "FROM well.well_master WHERE eng_completion_date IS NULL",
            label="test",
        )[0]
        mine = [r for r in rows if r["status"] == "INCOMPLETE"]
        assert len(mine) == live["n"]
        assert len({r["rig_id"] for r in mine if r["rig_id"] is not None}) == live["rigs"]
        assert sum(r["rig_id"] is None for r in mine) == live["unassigned"]

    def test_every_lookup_id_has_its_description(self, rows):
        for id_column, text_column in (("well_category_id", "well_category"), ("well_function_id", "well_function"),
                                       ("well_completion_type_id", "completion_type"), ("rig_id", "rig_no")):
            missing = [r["well_id"] for r in rows if r[id_column] is not None and r[text_column] is None]
            assert missing == [], f"{id_column} without a description for wells {missing[:5]}"
