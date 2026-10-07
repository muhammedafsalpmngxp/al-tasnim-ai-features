"""Put the independent DYNAMIC_DB project on ``sys.path``. Nothing else.

Imported, for its side effect only, by both :mod:`app.dynamic_client` (the
stable interface) and :mod:`app.capability_manifest` (the content DYNAMIC_DB
is fed) -- whichever of the two happens to be imported first must still find
``dynamic_db`` importable. Idempotent, and safe to import any number of times.
"""

from __future__ import annotations

import sys

from app.config.settings import BACKEND_DIR

_DYNAMIC_DB_ROOT = BACKEND_DIR.parent / "dynamic_db"
if str(_DYNAMIC_DB_ROOT) not in sys.path:
    sys.path.insert(0, str(_DYNAMIC_DB_ROOT))
