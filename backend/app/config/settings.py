"""Environment-driven configuration.

Every environment-specific value lives in backend/.env. Nothing in this file
carries a business default: thresholds, credentials, model names, hosts and
ports all come from the environment. See backend/.env.example.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[2]
SQL_DIR = BACKEND_DIR / "sql"
ENV_FILE = BACKEND_DIR / ".env"

load_dotenv(ENV_FILE, override=False)


class ConfigurationError(RuntimeError):
    """Raised when a required environment value is missing."""


def _get(*names: str, default: Optional[str] = None) -> Optional[str]:
    """First non-empty value among ``names``.

    Several names are accepted per setting so an existing .env written with
    either the canonical or a legacy key keeps working.
    """
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip() != "":
            return value.strip().strip('"').strip("'")
    return default


def _get_int(*names: str, default: int) -> int:
    raw = _get(*names)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{names[0]} must be an integer, got {raw!r}") from exc


def _get_bool(*names: str, default: bool) -> bool:
    raw = _get(*names)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "y", "on"}


class Settings:
    """Resolved application settings.

    Instantiating this class never raises for a missing database credential;
    :meth:`require_database` reports that as an operational error instead, so
    the API can start and surface a clean database-error state.
    """

    def __init__(self) -> None:
        # ---- database -----------------------------------------------------
        self.db_driver = _get("DB_DRIVER")
        self.db_server = _get("DB_SERVER", "DB_HOST")
        self.db_port = _get("DB_PORT")
        self.db_database = _get("DB_DATABASE", "DB_NAME")
        self.db_username = _get("DB_USERNAME", "DB_USER", "DB_UID")
        self.db_password = _get("DB_PASSWORD", "DB_PWD")
        self.db_encrypt = _get("DB_ENCRYPT", default="yes")
        self.db_trust_server_certificate = _get(
            "DB_TRUST_SERVER_CERTIFICATE", "DB_TRUST_CERT", default="no"
        )
        self.db_connect_timeout = _get_int("DB_CONNECT_TIMEOUT", default=15)
        self.db_query_timeout = _get_int("DB_QUERY_TIMEOUT", default=60)
        self.db_ansi_codepage = _get("DB_ANSI_CODEPAGE", default="cp1252")

        # ---- api ----------------------------------------------------------
        self.api_host = _get("API_HOST", default="127.0.0.1")
        self.api_port = _get_int("API_PORT", default=8000)
        self.api_log_level = _get("API_LOG_LEVEL", default="INFO")
        self.cors_origins: List[str] = [
            origin.strip()
            for origin in (_get("CORS_ORIGINS", default="") or "").split(",")
            if origin.strip()
        ]

        # ---- presentation / behaviour thresholds --------------------------
        # Number of logical daily tasks at or below which the dashboard shows
        # individual task cards instead of grouped summaries. Configurable so
        # the decision never sits inside React.
        self.detail_view_task_threshold = _get_int(
            "DETAIL_VIEW_TASK_THRESHOLD", default=25
        )
        # Seconds a resolved day dataset stays cached in memory. 0 disables.
        self.daily_cache_ttl_seconds = _get_int("DAILY_CACHE_TTL_SECONDS", default=60)
        # Maximum wells included in the evidence payload sent to the LLM.
        self.explain_max_wells = _get_int("EXPLAIN_MAX_WELLS", default=40)
        # Maximum successful explanations kept in the in-memory cache across
        # all report dates combined, evicted oldest-first once exceeded. Not a
        # correctness knob -- correctness comes from caching by the evidence's
        # own content hash, so changed data is never served a stale answer
        # regardless of this setting. This only bounds memory in a
        # long-running process.
        self.explain_cache_max_entries = _get_int(
            "EXPLAIN_CACHE_MAX_ENTRIES", default=500
        )
<<<<<<< HEAD
        # Where the AI explanation cache persists between process restarts --
        # a dev auto-reload, a redeploy, or just stopping and re-running the
        # app. Without this the cache was only ever in memory, so every
        # restart threw away every explanation generated so far and the very
        # next request for a well already explained minutes earlier paid for
        # a fresh LLM call again. A relative path resolves against
        # BACKEND_DIR. Correctness never depends on this file surviving --
        # content-hashing (see llm_service._evidence_hash) means a stale
        # answer is never served even after arbitrarily many restarts; this
        # setting only decides whether *reusable* answers survive one.
        self.explain_cache_file = _get(
            "EXPLAIN_CACHE_FILE", default=".cache/explain_cache.json"
        )
=======
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
        # Days ahead of a milestone deadline (pegging / FLAF / rig-on / rig-off)
        # at which a live well starts showing up as an "upcoming" priority
        # alert. Not a business rule -- business_rules.md defines the deadline
        # dates themselves (section 3) but not how far ahead counts as "near",
        # so this stays a configurable presentation threshold.
        self.milestone_priority_window_days = _get_int(
            "MILESTONE_PRIORITY_WINDOW_DAYS", default=7
        )
        # Maximum overdue milestones returned in the capped list (see
        # MilestonesResponse.overdue). overdue_count carries the true total.
        self.milestone_overdue_display_limit = _get_int(
            "MILESTONE_OVERDUE_DISPLAY_LIMIT", default=20
        )

        # ---- llm ----------------------------------------------------------
        self.llm_provider = _get("LLM_PROVIDER", default="groq")
        self.llm_api_key = _get("LLM_API_KEY", "GROQ_API_KEY", "api_key")
        self.llm_model = _get("LLM_MODEL", "GROQ_MODEL")
        self.llm_base_url = _get("LLM_BASE_URL")
        self.llm_timeout_seconds = _get_int("LLM_TIMEOUT_SECONDS", default=45)
        self.llm_max_tokens = _get_int("LLM_MAX_TOKENS", default=900)
        self.llm_temperature = float(_get("LLM_TEMPERATURE", default="0.2"))
<<<<<<< HEAD
=======
        # The front page's own whole-day summary is one call over every live
        # well, read first thing by every operator, so it may run on a
        # stronger model than the many per-well and per-task summaries. Blank
        # means it uses LLM_MODEL like everything else.
        self.llm_day_model = _get("LLM_DAY_MODEL") or self.llm_model
        # Reasoning models spend part of their completion budget thinking
        # before they write, so the day model can be given more room than
        # LLM_MAX_TOKENS without raising it for every other call.
        self.llm_day_max_tokens = _get_int(
            "LLM_DAY_MAX_TOKENS", default=self.llm_max_tokens
        )

        # ---- Oman field map (app.services.field_map_service) ----------------
        # Where the approximate field coordinates live; blank uses
        # app/config/oman_field_coordinates.json.
        self.field_map_coordinates_file = _get("FIELD_MAP_COORDINATES_FILE")
        # Seconds the field summary is reused. Well-to-field assignments move
        # slowly; the map must never re-query while it is panned or zoomed.
        self.field_map_cache_ttl_seconds = _get_int("FIELD_MAP_CACHE_TTL_SECONDS", default=600)

        # ---- Oman field map basemaps (app.services.field_map_basemaps) -----
        # Which satellite imagery the map offers: esri (default), custom or
        # none. The standard street map is always offered as the fallback.
        self.map_satellite_provider = (_get("MAP_SATELLITE_PROVIDER", default="esri") or "esri").lower()
        # An ArcGIS Location Platform API key. Blank uses Esri's public
        # MapServer tiles; set, the licensed Static Basemap Tiles service. A
        # browser-side key by Esri's design: scope it to basemaps and restrict
        # it to this site's referrer. Never committed; never in safe_dump().
        self.map_esri_api_key = _get("MAP_ESRI_API_KEY")
        # Label language for the keyed Esri service (CLDR code).
        self.map_labels_language = _get("MAP_LABELS_LANGUAGE", default="en")
        # Which basemap the page opens on: satellite or standard.
        self.map_default_basemap = (_get("MAP_DEFAULT_BASEMAP", default="satellite") or "satellite").lower()
        # MAP_SATELLITE_PROVIDER=custom: any imagery the organisation is
        # licensed for, as a {z}/{x}/{y}-style template, with its attribution.
        self.map_custom_satellite_url = _get("MAP_CUSTOM_SATELLITE_URL")
        self.map_custom_satellite_attribution = _get("MAP_CUSTOM_SATELLITE_ATTRIBUTION")
        self.map_custom_satellite_max_native_zoom = _get_int("MAP_CUSTOM_SATELLITE_MAX_NATIVE_ZOOM", default=18)
        self.map_custom_labels_url = _get("MAP_CUSTOM_LABELS_URL")
        self.map_custom_labels_attribution = _get("MAP_CUSTOM_LABELS_ATTRIBUTION")

        # ---- agents (app.agentic) ------------------------------------------
        # Each agent role names its own model, so the expensive one is spent
        # only where reasoning pays: planning an open question, and writing
        # the front page's brief. Blank falls back as noted.
        #   planner     turns a question into a bounded list of tool calls
        #   day writer  writes the front page's whole-day brief
        #   writer      writes well, task and question answers
        #   verifier    optional second opinion on a draft (AGENT_LLM_VERIFY);
        #               the deterministic number check always runs regardless
        self.agent_planner_model = _get("AGENT_PLANNER_MODEL") or self.llm_day_model
        self.agent_day_writer_model = _get("AGENT_DAY_WRITER_MODEL") or self.llm_day_model
        self.agent_writer_model = _get("AGENT_WRITER_MODEL") or self.llm_model
        self.agent_verifier_model = _get("AGENT_VERIFIER_MODEL") or self.llm_model
        self.agent_llm_verify = _get_bool("AGENT_LLM_VERIFY", default=False)
        # Hard limits on one run, so no question can turn into an unbounded
        # loop of tool calls or rewrites.
        self.agent_max_tool_calls = _get_int("AGENT_MAX_TOOL_CALLS", default=6)
        self.agent_max_revisions = _get_int("AGENT_MAX_REVISIONS", default=2)
        self.agent_planner_max_tokens = _get_int("AGENT_PLANNER_MAX_TOKENS", default=2000)
        self.agent_writer_max_tokens = _get_int(
            "AGENT_WRITER_MAX_TOKENS", default=self.llm_max_tokens
        )
        # Days ahead of a milestone deadline at which open work on that well
        # is reported as "open work before an upcoming deadline" (see the
        # agentic amendment to daily_report_rules.md section 9, rule 11).
        self.agent_deadline_window_days = _get_int(
            "AGENT_DEADLINE_WINDOW_DAYS", default=self.milestone_priority_window_days
        )
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)

        # ---- development only ---------------------------------------------
        # Never enabled by default. When true the API may serve an explicitly
        # labelled development fixture instead of the database.
        self.use_mock_data = _get_bool("USE_MOCK_DATA", default=False)

    # ------------------------------------------------------------------
    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key and self.llm_model)

    def require_database(self) -> None:
        missing = [
            name
            for name, value in (
                ("DB_DRIVER", self.db_driver),
                ("DB_SERVER", self.db_server),
                ("DB_DATABASE", self.db_database),
                ("DB_USERNAME", self.db_username),
                ("DB_PASSWORD", self.db_password),
            )
            if not value
        ]
        if missing:
            raise ConfigurationError(
                "Missing required database configuration in backend/.env: "
                + ", ".join(missing)
            )

    def connection_string(self) -> str:
        """ODBC connection string. Never logged, never returned by the API."""
        self.require_database()
        server = self.db_server
        if self.db_port:
            server = f"{server},{self.db_port}"
        parts = [
            f"DRIVER={{{self.db_driver}}}",
            f"SERVER={server}",
            f"DATABASE={self.db_database}",
            f"UID={self.db_username}",
            f"PWD={self.db_password}",
            f"Encrypt={self.db_encrypt}",
            f"TrustServerCertificate={self.db_trust_server_certificate}",
            f"Connection Timeout={self.db_connect_timeout}",
            # Belt and braces: the feature is read-only by design.
            "ApplicationIntent=ReadOnly",
            "APP=DailyMorningBrief",
        ]
        return ";".join(parts) + ";"

    def safe_dump(self) -> dict:
        """Configuration summary with every secret removed."""
        return {
            "db_server": self.db_server,
            "db_database": self.db_database,
            "db_driver": self.db_driver,
            "api_host": self.api_host,
            "api_port": self.api_port,
            "detail_view_task_threshold": self.detail_view_task_threshold,
            "daily_cache_ttl_seconds": self.daily_cache_ttl_seconds,
            "milestone_priority_window_days": self.milestone_priority_window_days,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
<<<<<<< HEAD
            "llm_configured": self.llm_configured,
            "use_mock_data": self.use_mock_data,
=======
            "llm_day_model": self.llm_day_model,
            "agent_models": {
                "planner": self.agent_planner_model,
                "day_writer": self.agent_day_writer_model,
                "writer": self.agent_writer_model,
                "verifier": self.agent_verifier_model if self.agent_llm_verify else None,
            },
            "llm_configured": self.llm_configured,
            "use_mock_data": self.use_mock_data,
            # DYNAMIC_DB's own configuration (schema allowlist, reasoning
            # model, compile mode) lives entirely in DYNAMIC_DB/.env and is
            # reported by GET /api/bootstrap/status and GET /api/health/schema
            # instead -- this application no longer carries a copy of it.
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
