"""Shared pytest fixtures.

DB helper tests are integration tests against the dev Supabase project. If
Supabase isn't configured or reachable (e.g. CI without secrets, or schema not
yet applied), they skip rather than fail.
"""

from __future__ import annotations

import os

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


@pytest.fixture(scope="session")
def anthropic_live() -> bool:
    """Gate for live Claude API tests (real calls cost money + are non-deterministic).

    Skips unless RUN_AI_LIVE is set AND an API key is configured. Run with:
        RUN_AI_LIVE=1 .venv\\Scripts\\python.exe -m pytest tests/test_generator.py tests/test_intent.py
    """
    if not os.environ.get("RUN_AI_LIVE"):
        pytest.skip("live AI tests disabled (set RUN_AI_LIVE=1 to enable)")
    try:
        get_settings()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"settings not configured: {exc}")
    return True
