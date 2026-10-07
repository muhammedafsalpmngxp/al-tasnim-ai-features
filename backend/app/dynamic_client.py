"""THE stable interface between Daily Report and DYNAMIC_DB.

EVERYTHING DAILY REPORT NEEDS FROM DYNAMIC_DB PASSES THROUGH THIS ONE MODULE.
No other file in this application imports ``dynamic_db.nodes.*``,
``dynamic_db.graph``, ``dynamic_db.artifacts`` or any other internal module --
only this client, and :mod:`app.capability_manifest` (which supplies DYNAMIC_DB
its content, the other half of this boundary). A repository that needs SQL
for a capability imports ``sql_for`` and a capability id FROM HERE, never from
``dynamic_db`` directly.

DYNAMIC_DB IS A SEPARATE, INDEPENDENTLY RUNNABLE PROJECT living beside this
one (``../dynamic_db``), not a subpackage of this application -- see
:mod:`app._dynamic_db_path` for how it is put on ``sys.path``.
"""

from __future__ import annotations

import logging
import threading
from datetime import date
from typing import Any, Dict, Optional

from app.config.settings import BACKEND_DIR

import app._dynamic_db_path  # noqa: F401 - side effect: dynamic_db becomes importable

logger = logging.getLogger(__name__)

from dynamic_db import bootstrap as _bootstrap_module  # noqa: E402
from dynamic_db import ledger as _ledger  # noqa: E402
from dynamic_db import llm as _dynamic_llm  # noqa: E402
from dynamic_db.service import CapabilityUnavailable, get_service  # noqa: E402

__all__ = [
    "CapabilityUnavailable",
    "sql_for",
    "ensure_ready",
    "bootstrap_status",
    "start_bootstrap_background",
    "compile_now",
    "detailed_status",
    "table_ledger",
]


# ---------------------------------------------------------------------------
# Manifest registration + usage-tracker wiring, done once per process.
# ---------------------------------------------------------------------------
_wired = False
_wire_lock = threading.Lock()


def _ensure_wired() -> None:
    global _wired
    if _wired:
        return
    with _wire_lock:
        if _wired:
            return
        from app import capability_manifest

        capability_manifest.ensure_registered()

        # Fold DYNAMIC_DB's own real model calls into the SAME expense
        # workbook the explanation layer already writes to, via dependency
        # injection -- DYNAMIC_DB itself never reaches for this file by path.
        # llm_usage_tracker.py lives at the project root (beside backend/ and
        # DYNAMIC_DB/), not on sys.path under its own name, so it is loaded by
        # file path -- the same technique app/services/llm_service.py already
        # uses for the same file.
        try:
            import importlib.util

            tracker_path = BACKEND_DIR.parent / "llm_usage_tracker.py"
            spec = importlib.util.spec_from_file_location("llm_usage_tracker", tracker_path)
            if spec is not None and spec.loader is not None:
                tracker = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(tracker)
                _dynamic_llm.set_usage_sink(tracker.log_usage)
        except Exception:  # noqa: BLE001 - accounting must never block startup
            logger.warning("dynamic_client: could not wire the usage tracker", exc_info=True)

        _wired = True


# ---------------------------------------------------------------------------
# The hot path repositories use
# ---------------------------------------------------------------------------
def sql_for(capability_id: str, *, report_date: Optional[date] = None) -> str:
    """The SQL that currently serves this capability. Raises
    :class:`CapabilityUnavailable` when the current schema cannot support it."""
    _ensure_wired()
    return get_service().sql_for(capability_id, report_date=report_date)


def compile_now(capability_id: str, *, report_date: Optional[date] = None, force: bool = False) -> Dict[str, Any]:
    """Compile one capability on demand (an operator action, e.g. from a CLI
    or an admin route) -- never called on the ordinary request path."""
    _ensure_wired()
    return get_service().compile(capability_id, report_date=report_date, force=force)


def detailed_status(*, refresh: bool = False) -> Dict[str, Any]:
    """Per-capability compiled-artifact status: version, origin, staleness.

    Distinct from :func:`bootstrap_status` -- this answers "what is compiled
    and does it still fit", on demand, at any time; bootstrap_status answers
    "how did the startup pipeline go". Both are read-only and free (no model
    call).
    """
    _ensure_wired()
    return get_service().status(refresh=refresh)


def table_ledger(*, history: int = 50) -> Dict[str, Any]:
    """What the table ledger knows: counts by status, the tables awaiting
    review, and the most recent recorded changes. Read from DYNAMIC_DB's
    cache only -- no database query, no model call."""
    return {**_ledger.summary(), "recent_changes": _ledger.load_events(limit=history)}


# ---------------------------------------------------------------------------
# The bootstrap gate
# ---------------------------------------------------------------------------
def bootstrap_status() -> Dict[str, Any]:
    """The current bootstrap state -- safe to call at any time, from any
    thread, including while a bootstrap run is still in progress. This is
    exactly what ``GET /api/bootstrap/status`` returns."""
    _ensure_wired()
    from app import capability_manifest

    orchestrator = _bootstrap_module.get_orchestrator(capability_manifest.ALL_CAPABILITY_IDS)
    return orchestrator.status


def ensure_ready() -> bool:
    """Whether DYNAMIC_DB has ever reached READY in this process."""
    _ensure_wired()
    from app import capability_manifest

    orchestrator = _bootstrap_module.get_orchestrator(capability_manifest.ALL_CAPABILITY_IDS)
    return orchestrator.is_ready


_background_started = False
_background_lock = threading.Lock()


def start_bootstrap_background(*, report_date: Optional[date] = None) -> None:
    """Kick off the full fingerprint-gated bootstrap pipeline in a background
    thread. Does not block: FastAPI's own startup returns immediately, and
    ``/api/bootstrap/status`` reports real progress as it happens.

    WHY BACKGROUND, NOT A BLOCKING STARTUP CALL. The FastAPI ``app`` object
    this project uses is shared by its whole test suite via ``TestClient`` --
    hundreds of existing tests instantiate it without a live database
    connection at hand. Blocking every one of those on a multi-second, real
    DB-dependent bootstrap would break or drastically slow the existing
    suite for no benefit those tests need. The REAL gate this project's own
    spec asks for -- the frontend not rendering the report until DYNAMIC_DB
    is READY -- is enforced by the frontend polling this status, not by
    blocking the backend process itself.
    """
    global _background_started
    _ensure_wired()
    with _background_lock:
        if _background_started:
            return
        _background_started = True

    from app import capability_manifest

    orchestrator = _bootstrap_module.get_orchestrator(capability_manifest.ALL_CAPABILITY_IDS)

    def _run() -> None:
        try:
            orchestrator.run(report_date=report_date)
        except Exception:  # noqa: BLE001 - a background thread must never crash the process
            logger.exception("dynamic_client: background bootstrap failed unexpectedly")
        # The table ledger starts once the bootstrap has finished, so its
        # first catalogue read never competes with the bootstrap's own. It
        # only reads metadata, and only ever writes to DYNAMIC_DB's cache.
        try:
            _ledger.start_watcher()
        except Exception:  # noqa: BLE001
            logger.exception("dynamic_client: the table watcher could not start")

    thread = threading.Thread(target=_run, name="dynamic-db-bootstrap", daemon=True)
    thread.start()
