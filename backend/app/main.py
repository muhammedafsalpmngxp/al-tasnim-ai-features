"""FastAPI application entry point for the Daily Morning Brief.

Read-only against SQL Server. SQL and Python calculate, React displays, the LLM
explains -- in that order, and never the other way round.
"""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config.settings import get_settings
<<<<<<< HEAD
=======
from app.dynamic_client import CapabilityUnavailable
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
from app.utils.logging_config import configure_logging

settings = get_settings()
configure_logging(settings.api_log_level)
logger = logging.getLogger(__name__)

# Configure logging *before* importing the route modules: importing
# routes_daily pulls in app.api.dependencies, which constructs the shared
# LLMService singleton on the spot -- and that constructor logs whether it
# warmed its explanation cache from a previous run. With the import above
# this line, that log call landed before any handler existed and was
# silently dropped; the cache still loaded correctly, but nothing said so.
from app.api import (
<<<<<<< HEAD
    routes_daily,
    routes_explain,
=======
    routes_agent,
    routes_bootstrap,
    routes_daily,
    routes_explain,
    routes_field_map,
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
    routes_export,
    routes_health,
    routes_wells,
)

app = FastAPI(
    title="Al Tasnim - Daily Morning Brief",
    description=(
        "Daily entry validation for live wells. All quantities and statuses are "
        "computed deterministically in SQL and Python; the LLM explains them and "
        "never calculates."
    ),
    version="1.0.0",
)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log each request with its duration. Never logs credentials."""
    request_id = uuid.uuid4().hex[:8]
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "req %s %s %s -> %s in %.1f ms",
        request_id,
        request.method,
        request.url.path + (f"?{request.url.query}" if request.url.query else ""),
        response.status_code,
        elapsed_ms,
    )
    response.headers["X-Request-ID"] = request_id
    return response


<<<<<<< HEAD
=======
@app.exception_handler(CapabilityUnavailable)
async def capability_unavailable_handler(
    request: Request, exc: CapabilityUnavailable
) -> JSONResponse:
    """A part of the report cannot be produced against the schema as it is now.

    503, and the reason, rather than a 500 or -- far worse -- a plausible
    answer to a different question. The database has changed under a query in a
    way the compiler could not resolve and could not verify, and the honest
    response is to say which capability is affected and why, so somebody can
    act on it. Every other section of the brief keeps working.
    """
    logger.warning("Capability unavailable on %s: %s", request.url.path, exc)
    return JSONResponse(status_code=503, content={"detail": str(exc)})


>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Never leak an internal error, a query or a credential to the client."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": (
                "An unexpected server error occurred. The daily task data may "
                "still be available; try refreshing."
            )
        },
    )


app.include_router(routes_health.router)
<<<<<<< HEAD
=======
app.include_router(routes_bootstrap.router)
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
app.include_router(routes_daily.router)
app.include_router(routes_wells.router)
app.include_router(routes_export.router)
app.include_router(routes_explain.router)
<<<<<<< HEAD
=======
app.include_router(routes_agent.router)
app.include_router(routes_field_map.router)
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)


@app.on_event("startup")
async def on_startup() -> None:
    logger.info("Daily Morning Brief API starting with config: %s", settings.safe_dump())
    if settings.use_mock_data:
        logger.warning(
            "USE_MOCK_DATA is enabled. This is a development-only setting and "
            "must never be used against production."
        )

<<<<<<< HEAD
=======
    # DYNAMIC_DB runs its full fingerprint-gated bootstrap in the BACKGROUND,
    # never blocking this startup event. The report endpoints stay reachable
    # immediately (each still gates itself per-capability via
    # CapabilityUnavailable, exactly as before this project existed); the
    # REAL "not ready yet" gate this project's architecture calls for is
    # enforced by the frontend, which polls GET /api/bootstrap/status and
    # withholds the report UI until it reports ready. See
    # app/dynamic_client.py for why this runs in a background thread rather
    # than blocking this coroutine.
    from app.dynamic_client import start_bootstrap_background

    start_bootstrap_background()

>>>>>>> 8e557ed (Update Daily Report Agentic implementation)

@app.get("/", include_in_schema=False)
def root() -> dict:
    return {
        "service": "Al Tasnim - Daily Morning Brief",
        "docs": "/docs",
        "health": "/api/health",
    }
