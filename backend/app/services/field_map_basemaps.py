"""The Oman field map's basemaps: which tile layers the browser may draw.

Configuration only -- no tile is fetched, cached or re-served here, and no
field or well position comes from any of it. Imagery is a geographic backdrop;
the database stays the only source of what a well is or does.

Three layers, each independently configurable:

    satellite  top-down imagery (Esri World Imagery by default)
    labels     a place-name overlay drawn above the imagery (English; the
               public Esri one adds Arabic beside it at closer zooms)
    standard   a street map with every place named in English (Arabic
               beside it at closer zooms on the public Esri tiles) -- always
               offered, and the fallback whenever imagery is off,
               unconfigured or failing

Providers (``MAP_SATELLITE_PROVIDER``):

    esri    Esri World Imagery + Esri "World Boundaries and Places" labels.
            With ``MAP_ESRI_API_KEY`` blank: Esri's public MapServer tiles
            (the same host as the existing street map). With a key: the
            licensed ArcGIS Location Platform services -- imagery from
            ibasemaps-api, labels and streets from the Static Basemap Tiles
            service with ``language`` set, 512px tiles.
    custom  any imagery the organisation is licensed for, from
            ``MAP_CUSTOM_SATELLITE_URL`` (+ optional label overlay).
    none    no imagery; the standard map only.

The API key, when set, is placed in the tile URLs because the browser fetches
the tiles itself -- Esri's model for browser apps, where the key is scoped to
basemaps and restricted to the site's referrer. It is never logged, never in
``Settings.safe_dump()`` and never committed (backend/.env is git-ignored).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from urllib.parse import quote

from app.config.settings import Settings, get_settings

logger = logging.getLogger(__name__)

# -- Esri public MapServer tiles (no key) -------------------------------------
ESRI_PUBLIC = "https://server.arcgisonline.com/ArcGIS/rest/services"
ESRI_IMAGERY_URL = f"{ESRI_PUBLIC}/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}"
ESRI_LABELS_URL = f"{ESRI_PUBLIC}/Reference/World_Boundaries_and_Places/MapServer/tile/{{z}}/{{y}}/{{x}}"
ESRI_STREETS_URL = f"{ESRI_PUBLIC}/World_Street_Map/MapServer/tile/{{z}}/{{y}}/{{x}}"

# -- ArcGIS Location Platform (API key) ---------------------------------------
ESRI_KEYED_IMAGERY_URL = "https://ibasemaps-api.arcgis.com/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
ESRI_STATIC_TILES = "https://static-map-tiles-api.arcgis.com/arcgis/rest/services/static-basemap-tiles-service/v1"
ESRI_KEYED_LABELS_URL = f"{ESRI_STATIC_TILES}/arcgis/imagery/labels/static/tile/{{z}}/{{y}}/{{x}}"
ESRI_KEYED_STREETS_URL = f"{ESRI_STATIC_TILES}/arcgis/streets/static/tile/{{z}}/{{y}}/{{x}}"

ESRI_IMAGERY_ATTRIBUTION = (
    "Imagery &copy; Esri &mdash; Source: Esri, Vantor, Earthstar Geographics, "
    "and the GIS User Community"
)
ESRI_LABELS_ATTRIBUTION = "Labels &copy; Esri, Garmin, HERE, OpenStreetMap contributors and the GIS User Community"
ESRI_STREETS_ATTRIBUTION = (
    "Tiles &copy; Esri &mdash; Source: Esri, HERE, Garmin, USGS, OpenStreetMap "
    "contributors and the GIS User Community"
)
POWERED_BY_ESRI = 'Powered by <a href="https://www.esri.com">Esri</a>'

#: Highest zoom with real World Imagery over the Oman fields: verified over
#: Fahud, where z18 is imagery and z19 is Esri's "Map data not yet available".
#: Leaflet enlarges z18 tiles beyond it instead of showing that placeholder.
ESRI_IMAGERY_MAX_NATIVE_ZOOM = 18
MAX_ZOOM = 19

LICENSE_NOTES = {
    "esri_public": (
        "Esri World Imagery via Esri's public tile service, under the Esri Master "
        "License Agreement. Production or commercial use needs an ArcGIS licence "
        "or an ArcGIS Location Platform API key (MAP_ESRI_API_KEY)."
    ),
    "esri_api_key": "Esri ArcGIS Location Platform basemap services, billed to the configured API key's account.",
    "custom": "Custom imagery configured by MAP_CUSTOM_SATELLITE_URL; its licence is the organisation's own.",
}


def _layer(
    url: str,
    attribution: str,
    *,
    label: str,
    max_native_zoom: int,
    tile_size: int = 256,
) -> Dict[str, Any]:
    return {
        "label": label,
        "url": url,
        "attribution": attribution,
        "max_native_zoom": max_native_zoom,
        "max_zoom": MAX_ZOOM,
        "tile_size": tile_size,
        # A 512px tile covers what four 256px tiles do one level down.
        "zoom_offset": -1 if tile_size == 512 else 0,
    }


def _with_query(url: str, **params: Optional[str]) -> str:
    query = "&".join(f"{k}={quote(v, safe='')}" for k, v in params.items() if v)
    return f"{url}?{query}" if query else url


def basemap_config(settings: Optional[Settings] = None) -> Dict[str, Any]:
    """The layers the map may draw, which to open on, and why imagery is or
    is not available. Never raises: a misconfiguration becomes the standard
    map plus a ``notice`` saying what is missing."""
    settings = settings or get_settings()
    provider = (settings.map_satellite_provider or "esri").strip().lower()
    key = settings.map_esri_api_key
    language = settings.map_labels_language or "en"

    standard = _layer(ESRI_STREETS_URL, ESRI_STREETS_ATTRIBUTION, label="Standard", max_native_zoom=MAX_ZOOM)
    satellite: Optional[Dict[str, Any]] = None
    labels: Optional[Dict[str, Any]] = None
    notice: Optional[str] = None
    #: The overlay's label language, only where it is known.
    labels_language: Optional[str] = None

    if provider == "esri":
        if key:
            satellite = _layer(
                _with_query(ESRI_KEYED_IMAGERY_URL, token=key),
                f"{POWERED_BY_ESRI} | {ESRI_IMAGERY_ATTRIBUTION}",
                label="Satellite",
                max_native_zoom=ESRI_IMAGERY_MAX_NATIVE_ZOOM,
            )
            labels = _layer(
                _with_query(ESRI_KEYED_LABELS_URL, token=key, language=language),
                ESRI_LABELS_ATTRIBUTION,
                label="Labels",
                max_native_zoom=MAX_ZOOM,
                tile_size=512,
            )
            standard = _layer(
                _with_query(ESRI_KEYED_STREETS_URL, token=key, language=language),
                f"{POWERED_BY_ESRI} | {ESRI_STREETS_ATTRIBUTION}",
                label="Standard",
                max_native_zoom=MAX_ZOOM,
                tile_size=512,
            )
            labels_language = language
            status = "esri_api_key"
        else:
            satellite = _layer(
                ESRI_IMAGERY_URL, ESRI_IMAGERY_ATTRIBUTION,
                label="Satellite", max_native_zoom=ESRI_IMAGERY_MAX_NATIVE_ZOOM,
            )
            # Esri's own overlay for imagery. It takes no language parameter:
            # every name is in English, and at closer zooms the local Arabic
            # name is printed beside it (checked over Dhofar and Nimr, z11).
            # English-only labels need the keyed service's language=en.
            labels = _layer(ESRI_LABELS_URL, ESRI_LABELS_ATTRIBUTION, label="Labels", max_native_zoom=MAX_ZOOM)
            labels_language = "en+ar"
            status = "esri_public"
    elif provider == "custom":
        if settings.map_custom_satellite_url and settings.map_custom_satellite_attribution:
            satellite = _layer(
                settings.map_custom_satellite_url,
                settings.map_custom_satellite_attribution,
                label="Satellite",
                max_native_zoom=settings.map_custom_satellite_max_native_zoom,
            )
            if settings.map_custom_labels_url:
                labels = _layer(
                    settings.map_custom_labels_url,
                    settings.map_custom_labels_attribution or settings.map_custom_satellite_attribution,
                    label="Labels",
                    max_native_zoom=MAX_ZOOM,
                )
            status = "custom"
        else:
            status = "not_configured"
            notice = (
                "Satellite imagery is not configured: MAP_SATELLITE_PROVIDER=custom needs "
                "MAP_CUSTOM_SATELLITE_URL and MAP_CUSTOM_SATELLITE_ATTRIBUTION. Showing the standard map."
            )
    elif provider == "none":
        status = "disabled"
        notice = "Satellite imagery is turned off (MAP_SATELLITE_PROVIDER=none). Showing the standard map."
    else:
        status = "not_configured"
        notice = f"Unknown MAP_SATELLITE_PROVIDER {provider!r}. Showing the standard map."
        logger.warning("field map: unknown MAP_SATELLITE_PROVIDER %r", provider)

    default = settings.map_default_basemap if settings.map_default_basemap in ("satellite", "standard") else "satellite"
    if satellite is None:
        default = "standard"

    return {
        "default": default,
        "satellite_status": status,
        "license_note": LICENSE_NOTES.get(status),
        "notice": notice,
        "layers": {"satellite": satellite, "standard": standard},
        "labels": labels,
        "labels_language": labels_language,
    }
