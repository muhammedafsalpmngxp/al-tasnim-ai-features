"""The Oman Petroleum Field Map: which field each well belongs to, where that
field approximately is, and how many of its wells are live or completed.

A supporting, read-only visualisation. It calculates nothing the rest of the
Daily Report relies on, and nothing here is a business rule: status comes from
SQL (``sql/field_map.sql``), coordinates from configuration
(``app/config/oman_field_coordinates.json``), and this module only joins the
two and counts distinct wells.

EVERY FIELD VALUE IS CLASSIFIED, NEVER ASSUMED. ``dsq.drilling_sequence.field``
is mostly petroleum field names, but also holds operational labels (RIG MOVE,
RIG MAINTENANCE, ...). Each distinct value is one of:

    non_geographical  an operational label -- excluded from the map entirely
    mapped            a field with a configured, validated Oman coordinate
                      (its own, or its parent field's for a named sub-area)
    unmapped          a field with no coordinate yet -- listed, never plotted

A coordinate is only ever read from the configuration file, and one outside
Oman's bounds is rejected at load time rather than plotted.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config.settings import BACKEND_DIR, get_settings
from app.repositories.field_map_repository import FieldMapRepository
from app.services.field_map_basemaps import basemap_config

logger = logging.getLogger(__name__)

DEFAULT_COORDINATES_FILE = BACKEND_DIR / "app" / "config" / "oman_field_coordinates.json"

#: A generous box around the Sultanate (including Musandam and Dhofar). A
#: configured coordinate outside it is a mistake, not a field, and is refused.
OMAN_BOUNDS = {"lat_min": 16.4, "lat_max": 26.6, "lon_min": 51.8, "lon_max": 60.0}

CLASS_NON_GEOGRAPHICAL = "non_geographical"
CLASS_MAPPED = "mapped"
CLASS_UNMAPPED = "unmapped"

STATUS_LABELS = {"COMPLETED": "Completed", "INCOMPLETE": "Incomplete"}

LOCATION_NOTE = (
    "Field positions are approximate representative locations and do not "
    "represent exact wellhead coordinates."
)


class UnknownField(LookupError):
    """No geographical field by that name."""


class InvalidFilter(ValueError):
    """A filter value that is neither an id nor ``none``."""


#: The value a filter uses to ask for wells with nothing recorded.
NONE_VALUE = "none"

#: Status views of a field's wells. "all" keeps the original contract.
STATUS_FILTERS = {
    "live": {"INCOMPLETE"},
    "completed": {"COMPLETED"},
    "all": {"INCOMPLETE", "COMPLETED"},
}

#: The well attributes a field's wells can be filtered by: the request
#: parameter, the row's id column, its description column, and the words used
#: when an id has no description in its lookup table.
FILTER_DIMENSIONS: Tuple[Tuple[str, str, str, str], ...] = (
    ("category", "well_category_id", "well_category", "Category"),
    ("function", "well_function_id", "well_function", "Function"),
    ("completion_type", "well_completion_type_id", "completion_type", "Completion type"),
    ("rig", "rig_id", "rig_no", "Rig"),
)
NOT_RECORDED = {"rig": "Unassigned"}


def normalise(name: Optional[str]) -> str:
    return " ".join((name or "").split()).upper()


def _id_or_none(value: Any) -> Optional[int]:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _text_or_none(value: Any) -> Optional[str]:
    text = " ".join(str(value).split()) if value is not None else ""
    return text or None


# ---------------------------------------------------------------------------
# Configuration: the geographic side, kept apart from the database
# ---------------------------------------------------------------------------
@dataclass
class FieldMapConfig:
    coordinates: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    #: sub-area -> parent field, e.g. "MARMUL AK" -> "MARMUL".
    parents: Dict[str, str] = field(default_factory=dict)
    non_geographical: Dict[str, str] = field(default_factory=dict)
    non_geographical_patterns: List[str] = field(default_factory=list)
    #: Entries refused at load time, with the reason -- for the status payload.
    rejected: Dict[str, str] = field(default_factory=dict)

    def classify(self, key: str) -> Tuple[str, Optional[Dict[str, Any]], Optional[str]]:
        """``(class, coordinate, via_parent)`` for one normalised field name."""
        if key in self.non_geographical:
            return CLASS_NON_GEOGRAPHICAL, None, None
        if any(fnmatch.fnmatchcase(key, pattern) for pattern in self.non_geographical_patterns):
            return CLASS_NON_GEOGRAPHICAL, None, None
        if key in self.coordinates:
            return CLASS_MAPPED, self.coordinates[key], None
        parent = self.parents.get(key)
        if parent and parent in self.coordinates:
            return CLASS_MAPPED, self.coordinates[parent], parent
        return CLASS_UNMAPPED, None, None


def _in_oman(lat: float, lon: float) -> bool:
    b = OMAN_BOUNDS
    return b["lat_min"] <= lat <= b["lat_max"] and b["lon_min"] <= lon <= b["lon_max"]


def load_config(path: Optional[Path] = None) -> FieldMapConfig:
    """Read and validate the coordinate file. Never raises: a missing or
    broken file yields an empty configuration, so every field is simply
    unmapped and the map says so."""
    path = Path(path or get_settings().field_map_coordinates_file or DEFAULT_COORDINATES_FILE)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("field map: coordinate file %s could not be read", path)
        return FieldMapConfig()

    def entries(section):
        # Keys starting with "_" are notes for the people editing the file.
        return [(k, v) for k, v in (raw.get(section) or {}).items() if not str(k).startswith("_")]

    config = FieldMapConfig()
    for name, entry in entries("fields"):
        key = normalise(name)
        try:
            lat, lon = float(entry["latitude"]), float(entry["longitude"])
        except (KeyError, TypeError, ValueError):
            config.rejected[key] = "latitude/longitude missing or not a number"
            continue
        if not _in_oman(lat, lon):
            config.rejected[key] = f"({lat}, {lon}) is outside Oman"
            continue
        config.coordinates[key] = {
            "latitude": round(lat, 4),
            "longitude": round(lon, 4),
            "label": entry.get("label") or name.title(),
            "location_type": "approximate",
            "confidence": entry.get("confidence"),
            "source": entry.get("source"),
        }
    for child, parent in entries("parent_fields"):
        config.parents[normalise(child)] = normalise(parent)
    for name, reason in entries("non_geographical_fields"):
        config.non_geographical[normalise(name)] = reason
    config.non_geographical_patterns = [
        normalise(pattern) for pattern in (raw.get("non_geographical_patterns") or [])
    ]
    if config.rejected:
        logger.warning("field map: %d coordinate(s) rejected: %s", len(config.rejected), config.rejected)
    return config


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------
class FieldMapService:
    def __init__(
        self,
        repository: Optional[FieldMapRepository] = None,
        config: Optional[FieldMapConfig] = None,
    ) -> None:
        self._repository = repository or FieldMapRepository()
        self._config = config
        self._lock = threading.Lock()
        self._cached: Optional[Tuple[float, Dict[str, Any]]] = None

    @property
    def config(self) -> FieldMapConfig:
        if self._config is None:
            self._config = load_config()
        return self._config

    def invalidate(self) -> None:
        with self._lock:
            self._cached = None
            self._config = None

    # -- one read, reused ----------------------------------------------------
    def _model(self, *, refresh: bool = False) -> Dict[str, Any]:
        """Every well grouped by field, classified. Cached for
        FIELD_MAP_CACHE_TTL_SECONDS so moving round the map never re-queries."""
        ttl = get_settings().field_map_cache_ttl_seconds
        with self._lock:
            if refresh:
                self._cached = None
                self._config = None
            if self._cached and ttl > 0 and time.monotonic() - self._cached[0] < ttl:
                return self._cached[1]

        rows = self._repository.fetch_well_fields()
        config = self.config

        wells_by_id: Dict[int, Dict[str, Any]] = {}
        for row in rows:
            # well.well_master.well_id is the identity; a repeated id (never
            # expected from the query) is kept once, not counted twice.
            well_id = int(row["well_id"])
            if well_id in wells_by_id:
                continue
            well = {
                "well_id": well_id,
                "status_code": str(row["status"]).upper(),
                "field": row.get("field"),
                "field_key": normalise(row.get("field_key") or row.get("field")) or None,
                "well_location": _text_or_none(row.get("well_location")),
            }
            # Each lookup as SQL returned it: the id, and its description
            # (None when the id has none -- labelled later, never dropped).
            for _, id_column, text_column, _ in FILTER_DIMENSIONS:
                well[id_column] = _id_or_none(row.get(id_column))
                well[text_column] = _text_or_none(row.get(text_column))
            wells_by_id[well_id] = well

        groups: Dict[str, Dict[str, Any]] = {}
        for well in wells_by_id.values():
            key = well["field_key"]
            if not key:
                continue
            group = groups.setdefault(key, {"key": key, "names": {}, "wells": []})
            group["names"][well["field"]] = group["names"].get(well["field"], 0) + 1
            group["wells"].append(well)

        for key, group in groups.items():
            kind, coordinate, parent = config.classify(key)
            group["class"] = kind
            group["coordinate"] = coordinate
            group["parent"] = parent
            # The stored spelling used most often, exactly as the database has it.
            group["field"] = max(group["names"].items(), key=lambda kv: (kv[1], kv[0]))[0]

        model = {"wells": wells_by_id, "groups": groups, "generated_at": datetime.now(timezone.utc)}
        with self._lock:
            self._cached = (time.monotonic(), model)
        return model

    # -- responses -----------------------------------------------------------
    @staticmethod
    def _counts(wells: List[Dict[str, Any]]) -> Dict[str, int]:
        live = sum(1 for w in wells if w["status_code"] == "INCOMPLETE")
        completed = sum(1 for w in wells if w["status_code"] == "COMPLETED")
        return {"live_well_count": live, "completed_well_count": completed, "total_well_count": len(wells)}

    def summary(self, *, refresh: bool = False) -> Dict[str, Any]:
        model = self._model(refresh=refresh)
        fields, unmapped = [], []
        non_geo_fields, non_geo_wells = 0, 0
        for group in model["groups"].values():
            counts = self._counts(group["wells"])
            if group["class"] == CLASS_NON_GEOGRAPHICAL:
                non_geo_fields += 1
                non_geo_wells += counts["total_well_count"]
                continue
            if group["class"] == CLASS_MAPPED:
                coordinate = group["coordinate"]
                fields.append(
                    {
                        "field": group["field"],
                        "field_key": group["key"],
                        "label": coordinate["label"] if not group["parent"] else group["field"].title(),
                        "latitude": coordinate["latitude"],
                        "longitude": coordinate["longitude"],
                        "location_type": "approximate",
                        # A named sub-area shares its parent field's position.
                        "location_basis": "parent_field" if group["parent"] else "field",
                        "parent_field": group["parent"],
                        # How sure the coordinate file is of this position, and where it came from.
                        "coordinate_confidence": coordinate.get("confidence"),
                        "coordinate_source": coordinate.get("source"),
                        **counts,
                    }
                )
            else:
                unmapped.append({"field": group["field"], "field_key": group["key"], **counts,
                                 "well_count": counts["total_well_count"]})

        fields.sort(key=lambda f: (-f["total_well_count"], f["field_key"]))
        unmapped.sort(key=lambda f: (-f["total_well_count"], f["field_key"]))

        all_wells = list(model["wells"].values())
        with_field = [w for w in all_wells if w["field_key"]]
        on_map = sum(f["total_well_count"] for f in fields)
        live_on_map = sum(f["live_well_count"] for f in fields)
        return {
            "generated_at": model["generated_at"],
            "location_note": LOCATION_NOTE,
            "bounds": OMAN_BOUNDS,
            # Read from settings on every call (never cached with the wells),
            # so a changed key or provider applies on the next page load.
            "basemaps": basemap_config(),
            "fields": fields,
            "unmapped_fields": unmapped,
            "coverage": {
                "total_wells": len(all_wells),
                "live_wells": sum(1 for w in all_wells if w["status_code"] == "INCOMPLETE"),
                "wells_with_field": len(with_field),
                "wells_without_field": len(all_wells) - len(with_field),
                "live_wells_with_field": sum(1 for w in with_field if w["status_code"] == "INCOMPLETE"),
                "wells_on_map": on_map,
                "live_wells_on_map": live_on_map,
                "wells_in_unmapped_fields": sum(f["total_well_count"] for f in unmapped),
                "wells_in_non_geographical_values": non_geo_wells,
                "distinct_field_values": len(model["groups"]),
                "mapped_field_count": len(fields),
                "unmapped_field_count": len(unmapped),
                "non_geographical_value_count": non_geo_fields,
            },
        }

    # -- filters -------------------------------------------------------------
    @staticmethod
    def _parse_filters(filters: Optional[Dict[str, Optional[str]]]) -> Dict[str, Optional[int]]:
        """``{dimension: id}`` for each filter given; ``None`` means "nothing
        recorded". A blank or "all" value is no filter. Anything else that is
        not a whole number is refused rather than guessed at."""
        parsed: Dict[str, Optional[int]] = {}
        for name, _, _, _ in FILTER_DIMENSIONS:
            raw = (filters or {}).get(name)
            value = str(raw).strip().lower() if raw is not None else ""
            if value in ("", "all"):
                continue
            if value == NONE_VALUE:
                parsed[name] = None
            elif value.isdigit():
                parsed[name] = int(value)
            else:
                raise InvalidFilter(f"{name} must be an id, '{NONE_VALUE}' or 'all', got {raw!r}")
        return parsed

    @staticmethod
    def _matches(well: Dict[str, Any], parsed: Dict[str, Optional[int]], skip: Optional[str] = None) -> bool:
        for name, id_column, _, _ in FILTER_DIMENSIONS:
            if name == skip or name not in parsed:
                continue
            if well[id_column] != parsed[name]:
                return False
        return True

    @classmethod
    def _options(
        cls,
        wells: List[Dict[str, Any]],
        parsed: Dict[str, Optional[int]],
        field_wells: List[Dict[str, Any]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Each filter's choices with how many wells each would show, given
        every OTHER filter (so combining filters never offers a dead end
        without saying so). The chosen value is always offered, even at 0,
        with its description from anywhere in the field."""
        options: Dict[str, List[Dict[str, Any]]] = {}
        for name, id_column, text_column, noun in FILTER_DIMENSIONS:
            counted: Dict[Optional[int], Dict[str, Any]] = {}
            for well in wells:
                if not cls._matches(well, parsed, skip=name):
                    continue
                ident = well[id_column]
                entry = counted.setdefault(ident, {"id": ident, "text": well[text_column], "count": 0})
                entry["count"] += 1
            if name in parsed and parsed[name] not in counted:
                counted[parsed[name]] = {"id": parsed[name], "text": None, "count": 0}
                # Its description, from any of the field's wells that carries it.
                for well in field_wells:
                    if well[id_column] == parsed[name] and well[text_column]:
                        counted[parsed[name]]["text"] = well[text_column]
                        break
            choices = [
                {
                    "value": NONE_VALUE if e["id"] is None else str(e["id"]),
                    "label": _option_label(name, noun, e["id"], e["text"]),
                    "count": e["count"],
                }
                for e in counted.values()
            ]
            choices.sort(key=lambda c: (c["value"] == NONE_VALUE, -c["count"], c["label"]))
            options[name] = choices
        return options

    def wells(
        self,
        field_name: str,
        *,
        status: str = "all",
        filters: Optional[Dict[str, Optional[str]]] = None,
        refresh: bool = False,
    ) -> Dict[str, Any]:
        """The wells of one geographical field, each once, status from SQL.
        A non-geographical value is not a field and is refused.

        ``status`` (live / completed / all) and ``filters`` (category,
        function, completion_type, rig: an id, "none" or "all") narrow the
        list. The field's own live / completed / total counts are always the
        whole field's; ``matching_well_count`` is the narrowed list's size."""
        status_key = (status or "all").strip().lower()
        if status_key not in STATUS_FILTERS:
            raise InvalidFilter(f"status must be one of {sorted(STATUS_FILTERS)}, got {status!r}")
        parsed = self._parse_filters(filters)

        model = self._model(refresh=refresh)
        key = normalise(field_name)
        group = model["groups"].get(key)
        if group is None or group["class"] == CLASS_NON_GEOGRAPHICAL:
            raise UnknownField(field_name)

        in_status = [w for w in group["wells"] if w["status_code"] in STATUS_FILTERS[status_key]]
        matching = sorted(
            (w for w in in_status if self._matches(w, parsed)),
            key=lambda w: (0 if w["status_code"] == "INCOMPLETE" else 1, w["well_id"]),
        )
        coordinate = group["coordinate"]
        return {
            "field": group["field"],
            "field_key": key,
            "mapped": group["class"] == CLASS_MAPPED,
            "latitude": coordinate["latitude"] if coordinate else None,
            "longitude": coordinate["longitude"] if coordinate else None,
            "location_type": "approximate" if coordinate else None,
            "location_note": LOCATION_NOTE if coordinate else "Field location unavailable.",
            **self._counts(group["wells"]),
            "status_filter": status_key,
            "applied_filters": {
                name: (NONE_VALUE if value is None else str(value)) for name, value in parsed.items()
            },
            "matching_well_count": len(matching),
            "filters": self._options(in_status, parsed, group["wells"]),
            "wells": [self._well_row(w) for w in matching],
        }

    @staticmethod
    def _well_row(w: Dict[str, Any]) -> Dict[str, Any]:
        row: Dict[str, Any] = {
            "well_id": w["well_id"],
            "status": STATUS_LABELS.get(w["status_code"], w["status_code"]),
            "status_code": w["status_code"],
        }
        for name, id_column, text_column, noun in FILTER_DIMENSIONS:
            row[f"{name}_id"] = w[id_column]
            row[name] = (
                None if w[id_column] is None else _option_label(name, noun, w[id_column], w[text_column])
            )
        row["well_location"] = w["well_location"]
        # No per-well position exists yet. Kept in the contract so real
        # wellhead coordinates can be added without a redesign.
        row["latitude"] = None
        row["longitude"] = None
        return row


def _option_label(name: str, noun: str, ident: Optional[int], text: Optional[str]) -> str:
    """The description as stored; for nothing recorded, "Not recorded" (or
    "Unassigned" for a rig); for an id missing from its lookup table, the id
    itself -- never another value's description."""
    if ident is None:
        return NOT_RECORDED.get(name, "Not recorded")
    if text:
        return text
    return f"{noun} {ident} (no description)"
