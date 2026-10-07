"""The bootstrap status endpoint -- what the frontend polls before it renders anything.

THIS IS THE ONE REAL SIGNAL THE FRONTEND ACTS ON. No timer, no fake progress:
this route returns exactly what :mod:`dynamic_db.bootstrap` currently reports,
read from a thread-safe snapshot that is correct even while a run is still in
progress. The frontend polls this on load and shows its own bootstrap screen
until ``ready`` is ``true`` or ``state`` is ``FAILED``.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.dynamic_client import bootstrap_status

router = APIRouter(prefix="/api/bootstrap", tags=["bootstrap"])


@router.get("/status")
def get_bootstrap_status() -> dict:
    """The current DYNAMIC_DB bootstrap state.

    Never raises: a failure to even READ the status is itself reported as a
    FAILED-shaped payload rather than a 500, since this is exactly the
    endpoint a frontend depends on to explain why nothing else is loading.
    """
    try:
        return bootstrap_status()
    except Exception as exc:  # noqa: BLE001 - this endpoint must never itself fail opaquely
        return {
            "state": "FAILED",
            "ready": False,
            "error": {"type": "status_unavailable", "message": str(exc)},
        }
