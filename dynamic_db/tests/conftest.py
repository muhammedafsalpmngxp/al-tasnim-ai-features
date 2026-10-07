"""Shared fixtures for DYNAMIC_DB's own, standalone test suite.

No dependency on any downstream application: this project's tests exercise
only what this project itself provides.
"""

from __future__ import annotations

import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

import pytest


def database_available() -> bool:
    try:
        from dynamic_db.db import check_connectivity

        check_connectivity()
        return True
    except Exception:  # noqa: BLE001
        return False


requires_database = pytest.mark.skipif(
    not database_available(), reason="SQL Server is not reachable from this environment"
)


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """No test may touch the real DYNAMIC_DB/.cache or DYNAMIC_DB/logs -- both
    are meant to reflect genuine runs, never a test."""
    from dynamic_db.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "dynamic_cache_dir", str(tmp_path / "cache"))
    monkeypatch.setattr(settings, "log_dir", str(tmp_path / "logs"))
    from dynamic_db import capabilities as capabilities_module

    yield
    capabilities_module.clear()
