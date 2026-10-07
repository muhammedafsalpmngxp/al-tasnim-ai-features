"""Read-only access to the Oman field map's well-to-field evidence.

One set-based query (``sql/field_map.sql``) for every well at once -- never one
query per field or per well. It is a plain file query rather than a
dynamic_db capability: the map is a supporting visualisation, and compiling it
would spend reasoning-model calls on a lookup this simple. It still passes
through the same read-only guard as every other query (``fetch_all``).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from app.config.database import fetch_all
from app.utils.sql_loader import load_sql

logger = logging.getLogger(__name__)


class FieldMapRepository:
    def fetch_well_fields(self) -> List[Dict[str, Any]]:
        """One row per well.well_master well: ``well_id``, ``status``
        (COMPLETED / INCOMPLETE, decided in SQL), and its latest ``field`` /
        ``field_key`` from dsq.drilling_sequence (NULL when it has none)."""
        rows = fetch_all(load_sql("field_map"), label="field_map")
        logger.info("field map: %d well(s) read", len(rows))
        return rows
