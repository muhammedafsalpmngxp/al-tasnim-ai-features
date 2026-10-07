"""Environment-driven configuration for DYNAMIC_DB, entirely its own.

DYNAMIC_DB IS AN INDEPENDENTLY RUNNABLE PROJECT. It reads its own
``DYNAMIC_DB/.env`` and knows nothing about any application built on top of
it -- no Daily Report field, no narration/"fast" model, no explanation-cache
setting lives here. The database connection settings below are DUPLICATED
rather than shared with a downstream application's own .env: two independent
processes talking to the same physical SQL Server each hold their own
connection configuration, exactly as two independently deployed services
would in any other architecture.

Nothing in this file carries a business default: thresholds, credentials,
model names, hosts and ports all come from the environment. See
``DYNAMIC_DB/.env.example``.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

#: This package's own root -- DYNAMIC_DB/, two levels up from this file
#: (DYNAMIC_DB/dynamic_db/config.py). Every relative path this project writes
#: (cache, logs, artifacts) resolves against this, never against whatever
#: directory a process happened to be started from.
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PACKAGE_ROOT / ".env"

load_dotenv(ENV_FILE, override=False)


class ConfigurationError(RuntimeError):
    """Raised when a required environment value is missing."""


def _get(*names: str, default: Optional[str] = None) -> Optional[str]:
    """First non-empty value among ``names``."""
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


def _csv_lower(*names: str) -> List[str]:
    """A comma-separated setting, lowercased and de-duplicated, order kept.

    Lowercased because every physical name this project compares is compared
    case-insensitively: SQL Server is case-insensitive by default, and an
    allowlist that silently stopped matching because someone typed a capital
    letter would fail closed in the most confusing way possible.
    """
    raw = _get(*names, default="") or ""
    out: List[str] = []
    for part in raw.split(","):
        value = part.strip().strip('"').strip("'").lower()
        if value and value not in out:
            out.append(value)
    return out


class Settings:
    """Resolved DYNAMIC_DB settings.

    Instantiating this class never raises for a missing database credential;
    :meth:`require_database` reports that as an operational error instead, so
    the CLI and any caller can start and surface a clean error rather than
    crashing at import time.
    """

    def __init__(self) -> None:
        # ---- database connection -------------------------------------------
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

        # ---- the approved visibility boundary -------------------------------
        # An explicit allowlist. Empty means nothing is approved, which fails
        # closed rather than exposing the whole database.
        self.included_tables: List[str] = _csv_lower("INCLUDED_TABLES")
        # Schemas inside which a REPLACEMENT candidate may be looked for when
        # an included table disappears. Discovery never leaves this boundary,
        # and a candidate is never adopted without validation and verification.
        self.discovery_schemas: List[str] = _csv_lower("DISCOVERY_SCHEMAS")
        # Extra columns hidden from every prompt, on top of the built-in
        # secret-name filter.
        self.excluded_columns: List[str] = _csv_lower("EXCLUDED_COLUMNS")

        # ---- the table ledger (dynamic_db.ledger) ----------------------------
        # Observe widely, approve narrowly. The ledger reads catalogue metadata
        # only (table and column names, types, dates, foreign keys) -- never a
        # row of data -- so it may look further than INCLUDED_TABLES without
        # widening what any prompt or compiled SQL can reach.
        #   blank  the schemas INCLUDED_TABLES and DISCOVERY_SCHEMAS reach
        #   *      every schema this login can see
        #   a,b    exactly these schemas
        self.watch_schemas: List[str] = _csv_lower("WATCH_SCHEMAS")
        # Tables that are noise, never worth a review: "schema.table" or bare
        # table-name patterns, * and ? wildcards. Blank uses the built-in list.
        self.watch_ignore_patterns: List[str] = _csv_lower("WATCH_IGNORE_PATTERNS") or [
            "tmp_*", "*_tmp", "temp_*", "*_temp", "*_bak", "*_bak_*", "*backup*",
            "*_old", "*_copy", "*_copy_*",
        ]
        # Minutes between ledger passes while the host application runs. 0
        # disables the in-process watcher; `python -m dynamic_db.cli watch`
        # still runs a pass on demand (for example from Task Scheduler).
        self.watch_interval_minutes = _get_int("WATCH_INTERVAL_MINUTES", default=60)
        # How alike a new table's column names must be to an approved table's
        # (shared / all, 0-1) before it is flagged as a possible successor.
        self.watch_similarity_threshold = float(
            _get("WATCH_SIMILARITY_THRESHOLD", default="0.6") or "0.6"
        )

        # ---- compilation behaviour -------------------------------------------
        #   off      never compile; the caller must supply a baseline for
        #            every capability and DYNAMIC_DB only validates it.
        #   auto     serve the promoted verified artifact; when the schema has
        #            moved under it, recompile the affected capabilities.
        #   strict   as auto, but a capability whose artifact cannot be
        #            re-verified is reported unavailable rather than served
        #            from an artifact the current schema no longer supports.
        self.dynamic_sql_mode = (
            _get("DYNAMIC_SQL_MODE", default="auto") or "auto"
        ).strip().lower()
        self.dynamic_cache_dir = _get("DYNAMIC_CACHE_DIR", default=".cache")
        # Bounded rewrite budget. Total authoring work per capability is capped
        # at (verify retries + 1) x (sql retries + 1) calls.
        self.dynamic_max_sql_retries = _get_int("DYNAMIC_MAX_SQL_RETRIES", default=2)
        self.dynamic_max_verify_retries = _get_int(
            "DYNAMIC_MAX_VERIFY_RETRIES", default=2
        )
        # Detail rows shown to the verifier: a sample for judging the SHAPE of
        # a result, never a population to count from.
        self.dynamic_sample_rows = _get_int("DYNAMIC_SAMPLE_ROWS", default=5)
        # Seconds a candidate query may run during compilation.
        self.dynamic_execution_timeout = _get_int(
            "DYNAMIC_EXECUTION_TIMEOUT", default=120
        )
        # Whether the measured numeric profile is built. One aggregate pass per
        # table; worth paying for because a scale mistake fails silently.
        self.dynamic_profile_numeric = _get_bool(
            "DYNAMIC_PROFILE_NUMERIC", default=True
        )

        # ---- the reasoning model (SQL authoring + semantic verification) ----
        # DYNAMIC_DB has exactly ONE model role: reasoning. It has no
        # narration/"fast" model of its own -- that belongs entirely to
        # whatever application consumes its output.
        self.reasoning_model = _get("REASONING_MODEL")
        self.reasoning_provider = _get("REASONING_PROVIDER", default="openai")
        self.reasoning_base_url = _get("REASONING_BASE_URL")
        self.reasoning_api_key = _get("REASONING_API_KEY")
        self.reasoning_timeout_seconds = _get_int(
            "REASONING_TIMEOUT_SECONDS", default=180
        )
        self.reasoning_max_tokens = _get_int("REASONING_MAX_TOKENS", default=4000)
        # Falling back to a weaker model would silently reduce reasoning
        # capability on exactly the calls that need it most. Off by default.
        self.allow_reasoning_fallback = _get_bool(
            "ALLOW_REASONING_FALLBACK", default=False
        )
        self.reasoning_fallback_model = _get("REASONING_FALLBACK_MODEL")

        # ---- logging ----------------------------------------------------------
        # See dynamic_db.logging_setup. Every value here is configuration, not
        # a code constant: retention, rotation size and verbosity are all
        # operator decisions.
        self.log_dir = _get("LOG_DIR", default="logs")
        self.log_level = _get("LOG_LEVEL", default="INFO")
        self.log_console = _get_bool("LOG_CONSOLE", default=True)
        self.log_console_json = _get_bool("LOG_CONSOLE_JSON", default=False)
        self.log_max_bytes = _get_int("LOG_MAX_BYTES", default=10 * 1024 * 1024)
        self.log_backup_count = _get_int("LOG_BACKUP_COUNT", default=10)
        # How many days of per-run structured logs are kept before being
        # pruned. 0 disables pruning.
        self.log_retention_days = _get_int("LOG_RETENTION_DAYS", default=30)

    # ------------------------------------------------------------------
    @property
    def reasoning_configured(self) -> bool:
        return bool(self.reasoning_api_key and self.reasoning_model)

    @property
    def included_table_set(self) -> frozenset:
        return frozenset(self.included_tables)

    @property
    def included_schemas(self) -> List[str]:
        """The schemas the allowlist reaches into, lowercased.

        Derived from the allowlist rather than configured separately, so the
        catalogue reads and the allowlist can never disagree about scope. The
        discovery boundary is folded in, so a replacement candidate can be
        seen at all.
        """
        schemas: List[str] = []
        for name in self.included_tables:
            schema = name.split(".", 1)[0] if "." in name else ""
            if schema and schema not in schemas:
                schemas.append(schema)
        for schema in self.discovery_schemas:
            if schema and schema not in schemas:
                schemas.append(schema)
        return schemas

    @property
    def watched_schemas(self) -> List[str]:
        """The schemas the table ledger reads, lowercased; ``["*"]`` for all."""
        if "*" in self.watch_schemas:
            return ["*"]
        return list(self.watch_schemas) or self.included_schemas

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
                "Missing required database configuration in DYNAMIC_DB/.env: "
                + ", ".join(missing)
            )

    def connection_string(self) -> str:
        """ODBC connection string. Never logged, never returned by any status call."""
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
            # Belt and braces: this project is read-only by design.
            "ApplicationIntent=ReadOnly",
            "APP=DynamicDB",
        ]
        return ";".join(parts) + ";"

    def safe_dump(self) -> dict:
        """Configuration summary with every secret removed."""
        return {
            "db_server": self.db_server,
            "db_database": self.db_database,
            "db_driver": self.db_driver,
            "dynamic_sql_mode": self.dynamic_sql_mode,
            "included_tables": list(self.included_tables),
            "discovery_schemas": list(self.discovery_schemas),
            "watch_schemas": list(self.watch_schemas),
            "watch_interval_minutes": self.watch_interval_minutes,
            "reasoning_model": self.reasoning_model,
            "reasoning_provider": self.reasoning_provider,
            "reasoning_configured": self.reasoning_configured,
            "allow_reasoning_fallback": self.allow_reasoning_fallback,
            "log_dir": self.log_dir,
            "log_level": self.log_level,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
