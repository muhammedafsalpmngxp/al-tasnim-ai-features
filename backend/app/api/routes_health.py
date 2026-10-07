"""Health and configuration endpoints. No secret is ever returned."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from app.api.dependencies import get_llm_service
from app.config.database import DatabaseUnavailable, check_connectivity
from app.config.settings import ConfigurationError, get_settings
from app.schemas.daily import HealthResponse
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(llm_service: LLMService = Depends(get_llm_service)) -> HealthResponse:
    """Report database reachability and LLM configuration.

    The dashboard uses this to tell a database outage apart from a day with no
    records -- two very different things for an operator.
    """
    settings = get_settings()
    database = {"connected": False, "error": None}
    try:
        info = check_connectivity()
        database = {
            "connected": True,
            "error": None,
            "database_name": info.get("database_name"),
            "server_version": info.get("server_version"),
            "read_only": True,
        }
    except (DatabaseUnavailable, ConfigurationError) as exc:
        database["error"] = str(exc)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected health-check failure")
        database["error"] = f"Unexpected database error: {type(exc).__name__}"

    return HealthResponse(
        status="ok" if database["connected"] else "degraded",
        database=database,
        llm=llm_service.status(),
        config=settings.safe_dump(),
    )
<<<<<<< HEAD
=======


@router.get("/health/schema")
def schema_health(refresh: bool = False) -> dict:
    """What each capability is currently running, and whether it still fits.

    Deliberately answerable WITHOUT calling a model: it compares each compiled
    artifact's recorded dependencies against the live catalogue, which is
    metadata only. An operator can poll this as often as they like, and a
    monitor can alert on ``stale`` or on a capability reported unavailable long
    before anybody notices a missing section of the morning brief.

    Returns names of database objects, fingerprints and version identifiers --
    never a credential, a connection string or a row of data.
    """
    from app.dynamic_client import detailed_status

    try:
        return detailed_status(refresh=refresh)
    except Exception as exc:  # noqa: BLE001 - a status endpoint must not 500
        logger.exception("Schema status could not be built")
        return {"error": f"The schema status could not be read: {type(exc).__name__}"}


@router.get("/health/tables")
def table_health(history: int = 50) -> dict:
    """What happened in the database, table by table.

    The table ledger's view: how many tables it watches by status, the new
    tables flagged for review (possible successors of an approved table, or
    tables linked to one by foreign key), and the latest recorded changes.
    Served from DYNAMIC_DB's own cache -- no query against the database and no
    model call. A flagged table is never approved by this; only INCLUDED_TABLES
    decides what the report can use.
    """
    from app.dynamic_client import table_ledger

    try:
        return table_ledger(history=max(0, min(history, 500)))
    except Exception as exc:  # noqa: BLE001 - a status endpoint must not 500
        logger.exception("Table ledger status could not be built")
        return {"error": f"The table ledger could not be read: {type(exc).__name__}"}
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
