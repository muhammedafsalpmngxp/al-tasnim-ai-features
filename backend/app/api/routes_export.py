"""Excel export endpoint. The workbook is built in the backend, never in React."""

from __future__ import annotations

import logging
from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response

from app.api.dependencies import (
    get_daily_service,
    get_export_service,
    load_dataset,
    parse_report_date,
)
from app.services.daily_service import DailyService
from app.services.export_service import ExportService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/daily", tags=["export"])

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/export")
def export_excel(
    report_date: date = Depends(parse_report_date),
    service: DailyService = Depends(get_daily_service),
    export_service: ExportService = Depends(get_export_service),
) -> Response:
    """Detailed records for the selected date as an .xlsx download.

    A pure read: the workbook is assembled in memory and nothing is written
    back to SQL Server.
    """
    dataset = load_dataset(service, report_date)
    try:
        content = export_service.build_workbook(dataset)
    except Exception as exc:  # noqa: BLE001 - report cleanly instead of a 500 page
        logger.exception("Excel generation failed for %s", report_date)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"The Excel export could not be generated: {type(exc).__name__}",
        ) from None

    filename = export_service.filename(report_date)
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{filename}"; '
                f"filename*=UTF-8''{quote(filename)}"
            ),
            "Content-Length": str(len(content)),
        },
    )
