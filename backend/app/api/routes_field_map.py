"""The Oman Petroleum Field Map endpoints. Read-only, deterministic.

    GET /api/field-map                  every geographical field with its
                                        approximate position and well counts,
                                        plus the fields with no position yet
    GET /api/field-map/{field}/wells    one field's wells, each once, with the
                                        status SQL decided; optionally narrowed
                                        by status, category, function,
                                        completion_type and rig, with each
                                        filter's options and counts

A non-geographical value (RIG MOVE, RIG MAINTENANCE, ...) is never a field
here: it is absent from the summary and refused by the wells endpoint.

Filter values are matched against rows already read by one parameterless
query; no request value is ever placed in SQL.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies import get_field_map_service
from app.config.database import DatabaseUnavailable
from app.services.field_map_service import FieldMapService, InvalidFilter, UnknownField

router = APIRouter(prefix="/api/field-map", tags=["field-map"])


@router.get("")
def field_map(
    refresh: bool = Query(False, description="Re-read the database and the coordinate file"),
    service: FieldMapService = Depends(get_field_map_service),
) -> Dict[str, Any]:
    try:
        return service.summary(refresh=refresh)
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from None


FILTER_HELP = "An id from this endpoint's `filters` options, `none` for nothing recorded, or `all`."


@router.get("/{field}/wells")
def field_wells(
    field: str,
    status_filter: str = Query("all", alias="status", description="live, completed or all"),
    category: Optional[str] = Query(None, description=FILTER_HELP),
    function: Optional[str] = Query(None, description=FILTER_HELP),
    completion_type: Optional[str] = Query(None, description=FILTER_HELP),
    rig: Optional[str] = Query(None, description=FILTER_HELP),
    service: FieldMapService = Depends(get_field_map_service),
) -> Dict[str, Any]:
    try:
        return service.wells(
            field,
            status=status_filter,
            filters={
                "category": category,
                "function": function,
                "completion_type": completion_type,
                "rig": rig,
            },
        )
    except InvalidFilter as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    except UnknownField:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No geographical field named {field!r}.",
        ) from None
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from None
