"""Shared pytest fixtures.

DB helper tests are integration tests against the dev Supabase project. If
Supabase isn't configured or reachable (e.g. CI without secrets, or schema not
yet applied), they skip rather than fail.
"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.db.client import ping


@pytest.fixture(scope="session")
def supabase_ready() -> bool:
    try:
        get_settings()
        ping()
    except Exception as exc:  # noqa: BLE001 — surface any config/connectivity issue as a skip
        pytest.skip(f"Supabase not configured/reachable (or schema not applied): {exc}")
    return True
